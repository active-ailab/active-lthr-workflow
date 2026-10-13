#!/usr/bin/env python3
"""Internal Jira attachment proxy for the LTHR browser analyzer."""

import json
import base64
import hashlib
import netrc
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_ATTACHMENT_BYTES = 128 * 1024 * 1024
MAX_TOTAL_DOWNLOAD_BYTES = 512 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 5
MAX_ARCHIVE_ENTRIES = 5000
CHUNK_SIZE = 1024 * 1024
ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-[0-9]+$")
SESSION_TTL_SECONDS = 900
SESSION_STORE = {}
SESSION_LOCK = threading.Lock()
JIRA_COMMAND = None


class ServiceError(Exception):
    def __init__(self, status, code, message, extra=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}


def find_jira_command():
    configured = os.environ.get("JIRA_CLI", "").strip()
    candidates = [configured] if configured else []
    candidates.extend(["jira", "jira.cmd", "jira.exe"])
    for candidate in candidates:
        if not candidate:
            continue
        found = shutil.which(candidate)
        if found:
            return [found]
        expanded = Path(candidate).expanduser()
        if expanded.is_file():
            return [str(expanded)]
    return None


def run_jira_cli(args):
    if not JIRA_COMMAND:
        raise ServiceError(503, "JIRA_CLI_NOT_FOUND", "Jira CLI is unavailable; restart the service after installing it")
    try:
        result = subprocess.run(
            JIRA_COMMAND + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise ServiceError(504, "JIRA_CLI_TIMEOUT", "Jira CLI timed out while reading the issue")
    except OSError:
        raise ServiceError(503, "JIRA_CLI_FAILED", "Could not start Jira CLI")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ServiceError(502, "JIRA_CLI_REQUEST_FAILED", detail[-1500:] or "Jira CLI request failed")
    return result.stdout


def jira_config():
    config_path = os.environ.get("JIRA_CONFIG_FILE", "~/.config/.jira/.config.yml")
    path = Path(config_path).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise ServiceError(502, "JIRA_CONFIG_NOT_FOUND", "Could not read the Jira CLI config file")
    server_match = re.search(r"(?m)^server:\s*[\"']?([^\s\"']+)", text)
    auth_match = re.search(r"(?m)^auth_type:\s*[\"']?([^\s\"']+)", text)
    if not server_match:
        raise ServiceError(502, "JIRA_CONFIG_INVALID", "Jira CLI config does not define a server")
    return server_match.group(1).rstrip("/"), (auth_match.group(1).lower() if auth_match else "basic")


def jira_auth_header(host, auth_type):
    token = os.environ.get("JIRA_API_TOKEN", "").strip()
    if auth_type == "bearer" and token:
        return "Bearer " + token
    try:
        auth = netrc.netrc().authenticators(host)
    except (OSError, netrc.NetrcParseError):
        auth = None
    if auth and auth[0] and auth[2]:
        raw = f"{auth[0]}:{auth[2]}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")
    if token and auth_type == "basic":
        login = os.environ.get("JIRA_USERNAME", "").strip()
        if login:
            raw = f"{login}:{token}".encode("utf-8")
            return "Basic " + base64.b64encode(raw).decode("ascii")
    raise ServiceError(502, "JIRA_CREDENTIALS_NOT_FOUND", "Jira CLI works, but its credentials are not available through the configured .netrc or JIRA_API_TOKEN")


class SameHostRedirectHandler(HTTPRedirectHandler):
    def __init__(self, expected_host):
        self.expected_host = expected_host
        super().__init__()

    def redirect_request(self, request, response, code, message, headers, new_url):
        if urlparse(new_url).hostname != self.expected_host:
            raise ServiceError(502, "UNSAFE_JIRA_REDIRECT", "Jira redirected the attachment outside the configured Jira host")
        return super().redirect_request(request, response, code, message, headers, new_url)


def download_issue_attachments(issue_key, destination):
    raw = run_jira_cli(["issue", "view", issue_key, "--raw"])
    try:
        issue = json.loads(raw)
    except json.JSONDecodeError:
        raise ServiceError(502, "JIRA_CLI_BAD_RESPONSE", "Jira CLI returned invalid issue JSON")
    attachments = (issue.get("fields") or {}).get("attachment") or []
    if not attachments:
        return 0
    server, auth_type = jira_config()
    server_parts = urlparse(server)
    authorization = jira_auth_header(server_parts.hostname, auth_type)
    opener = build_opener(SameHostRedirectHandler(server_parts.hostname))
    total = 0
    for index, attachment in enumerate(attachments, 1):
        content_url = attachment.get("content", "")
        parts = urlparse(content_url)
        if parts.scheme != server_parts.scheme or parts.hostname != server_parts.hostname:
            raise ServiceError(502, "UNSAFE_ATTACHMENT_URL", "Jira returned an attachment URL outside its configured server")
        request = Request(content_url, headers={"Authorization": authorization, "Accept": "application/octet-stream"})
        filename = safe_filename(attachment.get("filename", f"attachment_{index}"))
        target_dir = destination / f"attachment_{index:04d}_{attachment.get('id', index)}"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        try:
            with opener.open(request, timeout=60) as response, target.open("wb") as output:
                copied = 0
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    copied += len(chunk)
                    total += len(chunk)
                    if copied > MAX_ATTACHMENT_BYTES or total > MAX_TOTAL_DOWNLOAD_BYTES:
                        raise ServiceError(413, "DOWNLOAD_LIMIT_EXCEEDED", "Jira attachments exceed the configured download limits")
                    output.write(chunk)
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise ServiceError(502, "JIRA_AUTH_FAILED", "Jira CLI read the issue, but attachment download authentication failed")
            raise ServiceError(502, "JIRA_ATTACHMENT_DOWNLOAD_FAILED", f"Jira attachment download failed with HTTP {exc.code}")
        except URLError:
            raise ServiceError(502, "JIRA_UNAVAILABLE", "Could not download an attachment from Jira")
    return len(attachments)


def safe_filename(name):
    name = str(name or "attachment")
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._() -]", "_", name).strip(" .")
    return name[:180] or "attachment"


def safe_archive_target(root, member_name):
    if "\\" in member_name or "\x00" in member_name:
        return None
    member = PurePosixPath(member_name)
    if not member.parts or member.is_absolute() or any(part in ("..", "") for part in member.parts):
        return None
    if member.parts and ":" in member.parts[0]:
        return None
    root_resolved = root.resolve()
    target = root.joinpath(*member.parts).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError:
        return None
    return target


def expand_archives(root):
    queue = [(path, 0) for path in root.rglob("*") if path.is_file() and zipfile.is_zipfile(path)]
    extracted_bytes = 0
    entry_count = 0
    archive_count = 0
    while queue:
        archive_path, depth = queue.pop(0)
        if depth >= MAX_ARCHIVE_DEPTH:
            raise ServiceError(413, "ARCHIVE_NESTING_LIMIT", "Nested ZIP depth exceeds the configured limit")
        archive_count += 1
        output_root = root / "expanded" / f"archive_{archive_count:04d}_{safe_filename(archive_path.name)}"
        output_root.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for member in archive.infolist():
                    entry_count += 1
                    if entry_count > MAX_ARCHIVE_ENTRIES:
                        raise ServiceError(413, "ARCHIVE_ENTRY_LIMIT", "ZIP files contain too many entries")
                    mode = member.external_attr >> 16
                    if stat.S_ISLNK(mode):
                        continue
                    target = safe_archive_target(output_root, member.filename)
                    if target is None:
                        continue
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    if extracted_bytes + member.file_size > MAX_EXPANDED_BYTES:
                        raise ServiceError(413, "ARCHIVE_SIZE_LIMIT", "Expanded ZIP data exceeds the configured size limit")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    copied = 0
                    with archive.open(member) as source, target.open("wb") as output:
                        while True:
                            chunk = source.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            copied += len(chunk)
                            extracted_bytes += len(chunk)
                            if extracted_bytes > MAX_EXPANDED_BYTES or copied > member.file_size:
                                raise ServiceError(413, "ARCHIVE_SIZE_LIMIT", "Expanded ZIP data exceeds the configured size limit")
                            output.write(chunk)
                    if zipfile.is_zipfile(target):
                        queue.append((target, depth + 1))
        except zipfile.BadZipFile:
            raise ServiceError(422, "INVALID_ZIP", f"Invalid ZIP archive: {archive_path.name}")
    return archive_count


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            chunk = source.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.digest()


def find_target_files(root):
    logs = []
    fits = []
    seen_log_hashes = set()

    def collect(path):
        if not path.is_file():
            return
        name = path.name.lower()
        suffix = path.suffix.lower()
        if suffix in (".log", ".txt") and (name.startswith("mili") or name.startswith("ios")):
            digest = file_sha256(path)
            if digest not in seen_log_hashes:
                seen_log_hashes.add(digest)
                logs.append(path)
        elif suffix == ".fit":
            fits.append(path)

    for path in sorted(root.rglob("*")):
        if not path.is_file() or "expanded" in path.parts:
            # Expanded files are traversed below separately; avoid returning
            # archive bytes themselves as candidate source files.
            continue
        collect(path)
    expanded_root = root / "expanded"
    if expanded_root.exists():
        for path in sorted(expanded_root.rglob("*")):
            collect(path)
    return logs, fits


def build_file_session(issue_key, work_root):
    originals = work_root / "attachments"
    originals.mkdir()
    attachment_count = download_issue_attachments(issue_key, originals)
    if attachment_count == 0 or not any(path.is_file() for path in originals.rglob("*")):
        raise ServiceError(404, "NO_ATTACHMENTS", "Jira issue has no attachments", {"feedbackFallback": "not_implemented"})

    archive_count = expand_archives(work_root)
    logs, fits = find_target_files(work_root)
    if not logs:
        raise ServiceError(404, "NO_TARGET_LOGS", "No MILI/ios .log or .txt files were found", {
            "jiraAttachmentCount": attachment_count,
            "zipArchiveCount": archive_count,
            "feedbackFallback": "not_implemented",
        })

    manifest = {
        "issueKey": issue_key,
        "jiraAttachmentCount": attachment_count,
        "zipArchiveCount": archive_count,
        "files": [],
        "feedbackFallback": "not_needed",
    }
    session_id = secrets.token_urlsafe(24)
    session_root = Path(os.environ.get("SESSION_DIR", tempfile.gettempdir())) / "lthr-jira-sessions" / session_id
    session_root.mkdir(parents=True, exist_ok=False)
    file_map = {}
    try:
        for file_type, paths in (("log", logs), ("fit", fits)):
            for path in paths:
                file_id = secrets.token_urlsafe(18)
                filename = safe_filename(path.name)
                stored_path = session_root / f"{file_id}_{filename}"
                shutil.copyfile(path, stored_path)
                entry = {
                    "fileId": file_id,
                    "type": file_type,
                    "name": path.name,
                    "size": stored_path.stat().st_size,
                    "downloadUrl": f"/api/v1/sessions/{session_id}/files/{file_id}",
                }
                manifest["files"].append(entry)
                file_map[file_id] = {"path": stored_path, "name": path.name, "type": file_type}
        with SESSION_LOCK:
            SESSION_STORE[session_id] = {
                "root": session_root,
                "expiresAt": time.time() + SESSION_TTL_SECONDS,
                "files": file_map,
            }
    except Exception:
        shutil.rmtree(session_root, ignore_errors=True)
        raise
    manifest.update({"status": "ready", "sessionId": session_id, "expiresInSeconds": SESSION_TTL_SECONDS})
    return manifest


def purge_expired_sessions():
    now = time.time()
    with SESSION_LOCK:
        expired = [key for key, session in SESSION_STORE.items() if session["expiresAt"] <= now]
        sessions = [SESSION_STORE.pop(key) for key in expired]
    for session in sessions:
        shutil.rmtree(session["root"], ignore_errors=True)


def get_session_file(session_id, file_id):
    purge_expired_sessions()
    with SESSION_LOCK:
        session = SESSION_STORE.get(session_id)
        if not session:
            return None
        return session["files"].get(file_id)


class Handler(BaseHTTPRequestHandler):
    server_version = "LTHRJiraAttachmentService/1.0"

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.add_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def add_cors_headers(self):
        request_origin = self.headers.get("Origin", "")
        configured_origins = os.environ.get("ALLOWED_ORIGIN", "").strip()
        allowed_origins = {
            origin.strip()
            for origin in configured_origins.split(",")
            if origin.strip()
        } if configured_origins else {
            "https://active-ailab.github.io",
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        }
        if request_origin and request_origin in allowed_origins:
            self.send_header("Access-Control-Allow-Origin", request_origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Credentials", "true")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.add_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"status": "ok"})
            return
        match = re.fullmatch(r"/api/v1/sessions/([A-Za-z0-9_-]+)/files/([A-Za-z0-9_-]+)", self.path)
        if match:
            entry = get_session_file(match.group(1), match.group(2))
            if not entry or not entry["path"].is_file():
                self.send_json(404, {"error": "FILE_EXPIRED_OR_NOT_FOUND", "message": "Temporary file is unavailable; search Jira again"})
                return
            size = entry["path"].stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", "inline; filename*=UTF-8''" + quote(entry["name"], safe=""))
            self.add_cors_headers()
            self.end_headers()
            with entry["path"].open("rb") as source:
                shutil.copyfileobj(source, self.wfile, CHUNK_SIZE)
            return
        self.send_json(404, {"error": "NOT_FOUND", "message": "Endpoint not found"})

    def do_POST(self):
        match = re.fullmatch(r"/api/v1/issues/([A-Za-z][A-Za-z0-9_]*-[0-9]+)/attachments", self.path)
        if not match:
            self.send_json(404, {"error": "NOT_FOUND", "message": "Endpoint not found"})
            return
        issue_key = match.group(1).upper()
        if not ISSUE_KEY_RE.fullmatch(issue_key):
            self.send_json(400, {"error": "INVALID_ISSUE_KEY", "message": "Invalid Jira issue key"})
            return
        try:
            with tempfile.TemporaryDirectory(prefix="lthr-jira-") as temp_dir:
                manifest = build_file_session(issue_key, Path(temp_dir))
                self.send_json(200, manifest)
        except ServiceError as exc:
            self.send_json(exc.status, {"error": exc.code, "message": exc.message, **exc.extra})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            self.send_json(500, {"error": "INTERNAL_ERROR", "message": "Attachment processing failed"})

    def log_message(self, fmt, *args):
        # Do not log request headers or Jira attachment content.
        super().log_message(fmt, *args)


def main():
    global JIRA_COMMAND
    JIRA_COMMAND = find_jira_command()
    if not JIRA_COMMAND:
        raise SystemExit("ERROR: Jira CLI was not found (expected `jira` in PATH). Service was not started.")
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"LTHR Jira attachment service listening on {host}:{port} (using {' '.join(JIRA_COMMAND)})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

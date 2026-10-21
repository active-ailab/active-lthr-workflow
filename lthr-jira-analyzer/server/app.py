#!/usr/bin/env python3
"""Internal Jira attachment proxy for the LTHR browser analyzer."""

import json
import hashlib
import os
import re
import secrets
import shutil
import stat
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse
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
JIRA_LOGIN_SESSIONS = {}
JIRA_LOGIN_LOCK = threading.Lock()
JIRA_LOGIN_TTL_SECONDS = 8 * 60 * 60
DEFAULT_JIRA_HOSTS = {"jira.huami.com"}


class ServiceError(Exception):
    def __init__(self, status, code, message, extra=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}


def normalize_jira_url(value):
    base_url = str(value or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    try:
        port = parsed.port
    except ValueError:
        raise ServiceError(400, "INVALID_JIRA_URL", "Jira address must be a valid HTTPS URL")
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or port not in (None, 443)
    ):
        raise ServiceError(400, "INVALID_JIRA_URL", "Jira address must be a valid HTTPS URL")
    allowed_hosts = {
        host.strip().lower()
        for host in os.environ.get("JIRA_ALLOWED_HOSTS", "jira.huami.com").split(",")
        if host.strip()
    } or DEFAULT_JIRA_HOSTS
    if parsed.hostname.lower() not in allowed_hosts:
        raise ServiceError(403, "JIRA_HOST_NOT_ALLOWED", "This Jira server address is not allowed")
    return base_url


def jira_same_origin(url, base_url):
    requested = urlparse(url)
    expected = urlparse(base_url)
    return (
        requested.scheme.lower(),
        (requested.hostname or "").lower(),
        requested.port or 443,
    ) == (
        expected.scheme.lower(),
        (expected.hostname or "").lower(),
        expected.port or 443,
    )


def login_to_jira(base_url, username, password):
    base_url = normalize_jira_url(base_url)
    if not username or not password:
        raise ServiceError(400, "JIRA_CREDENTIALS_REQUIRED", "Enter both a Jira username and password")
    body = json.dumps({"username": username, "password": password}).encode("utf-8")
    request = Request(
        base_url + "/rest/auth/1/session",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with build_opener(SameHostRedirectHandler(base_url)).open(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code in (401, 403):
            login_reason = exc.headers.get("X-Seraph-LoginReason", "").upper()
            if login_reason == "AUTHENTICATION_DENIED":
                raise ServiceError(401, "JIRA_LOGIN_DENIED", "Jira denied this login before checking the password. Sign in to Jira in your browser first; a CAPTCHA, SSO policy, or temporary security lock may need attention.")
            if login_reason == "AUTHENTICATION_FAILED":
                raise ServiceError(401, "JIRA_LOGIN_FAILED", "Jira rejected the credentials. Check the Jira username and password; some Jira setups require the account username instead of an email address.")
            raise ServiceError(401, "JIRA_LOGIN_FAILED", "Jira rejected this login. Check the Jira username/password, or confirm whether this account requires SSO or two-step verification.")
        raise ServiceError(502, "JIRA_LOGIN_FAILED", f"Jira login failed with HTTP {exc.code}")
    except URLError:
        raise ServiceError(502, "JIRA_UNAVAILABLE", "Could not connect to the Jira address")
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ServiceError(502, "JIRA_LOGIN_BAD_RESPONSE", "Jira returned an invalid login response; SSO login pages are not supported by this form")
    session = payload.get("session") or {}
    cookie_name = str(session.get("name", ""))
    cookie_value = str(session.get("value", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]+", cookie_name) or not cookie_value or any(char in cookie_value for char in "\r\n;,"):
        raise ServiceError(401, "JIRA_LOGIN_FAILED", "Jira did not return a session; verify the account and authentication method")
    return base_url, cookie_name, cookie_value


def create_login_session(jira_session):
    app_token = secrets.token_urlsafe(48)
    with JIRA_LOGIN_LOCK:
        JIRA_LOGIN_SESSIONS[app_token] = {
            "jiraBaseUrl": jira_session[0],
            "cookieName": jira_session[1],
            "cookieValue": jira_session[2],
            "expiresAt": time.time() + JIRA_LOGIN_TTL_SECONDS,
        }
    return app_token


def get_login_session(app_token):
    if not app_token:
        return None
    with JIRA_LOGIN_LOCK:
        session = JIRA_LOGIN_SESSIONS.get(app_token)
        if not session:
            return None
        if session["expiresAt"] <= time.time():
            JIRA_LOGIN_SESSIONS.pop(app_token, None)
            return None
        return session


def bearer_session(handler):
    authorization = handler.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None, None
    token = authorization[7:].strip()
    return token, get_login_session(token)


class SameHostRedirectHandler(HTTPRedirectHandler):
    def __init__(self, expected_url):
        self.expected_url = expected_url
        super().__init__()

    def redirect_request(self, request, response, code, message, headers, new_url):
        if not jira_same_origin(new_url, self.expected_url):
            raise ServiceError(502, "UNSAFE_JIRA_REDIRECT", "Jira redirected the attachment outside the configured Jira host")
        return super().redirect_request(request, response, code, message, headers, new_url)


def jira_api_json(path, jira_session):
    base_url = jira_session["jiraBaseUrl"]
    request = Request(
        base_url + path,
        headers={"Cookie": jira_session["cookieName"] + "=" + jira_session["cookieValue"], "Accept": "application/json"},
    )
    try:
        with build_opener(SameHostRedirectHandler(base_url)).open(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise ServiceError(401, "JIRA_ACCESS_DENIED", "Jira rejected the authorization or the account cannot view this issue")
        raise ServiceError(502, "JIRA_API_FAILED", f"Jira issue request failed with HTTP {exc.code}")
    except URLError:
        raise ServiceError(502, "JIRA_UNAVAILABLE", "Could not connect to Jira")
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ServiceError(502, "JIRA_BAD_RESPONSE", "Jira returned invalid issue JSON")


def download_issue_attachments(issue_key, destination, jira_session):
    raw = jira_api_json(f"/rest/api/2/issue/{quote(issue_key)}?fields=attachment", jira_session)
    try:
        attachments = (raw.get("fields") or {}).get("attachment") or []
    except AttributeError:
        raise ServiceError(502, "JIRA_BAD_RESPONSE", "Jira response did not contain issue fields")
    if not attachments:
        return 0
    server = jira_session["jiraBaseUrl"]
    opener = build_opener(SameHostRedirectHandler(server))
    total = 0
    for index, attachment in enumerate(attachments, 1):
        content_url = attachment.get("content", "")
        if not jira_same_origin(content_url, server):
            raise ServiceError(502, "UNSAFE_ATTACHMENT_URL", "Jira returned an attachment URL outside its configured server")
        request = Request(content_url, headers={
            "Cookie": jira_session["cookieName"] + "=" + jira_session["cookieValue"],
            "Accept": "application/octet-stream",
        })
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
                raise ServiceError(401, "JIRA_ACCESS_DENIED", "Jira rejected the authorization while downloading an attachment")
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
    preferred_logs = []
    fallback_logs = []
    fits = []
    seen_preferred_hashes = set()
    seen_fallback_hashes = set()

    def collect(path):
        if not path.is_file():
            return
        name = path.name.lower()
        suffix = path.suffix.lower()
        if suffix in (".log", ".txt") and name != "hmble_log.log":
            digest = file_sha256(path)
            if digest not in seen_fallback_hashes:
                seen_fallback_hashes.add(digest)
                fallback_logs.append(path)
            if name.startswith(("mili", "ios", "amazfit")) and digest not in seen_preferred_hashes:
                seen_preferred_hashes.add(digest)
                preferred_logs.append(path)
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
    # Prefer known device-name prefixes. If none exist on the issue, fall back
    # to other .log/.txt files because device names vary across products.
    return preferred_logs or fallback_logs, fits


def build_file_session(issue_key, work_root, jira_session, owner_token):
    originals = work_root / "attachments"
    originals.mkdir()
    attachment_count = download_issue_attachments(issue_key, originals, jira_session)
    if attachment_count == 0 or not any(path.is_file() for path in originals.rglob("*")):
        raise ServiceError(404, "NO_ATTACHMENTS", "Jira issue has no attachments", {"feedbackFallback": "not_implemented"})

    archive_count = expand_archives(work_root)
    logs, fits = find_target_files(work_root)
    if not logs:
        raise ServiceError(404, "NO_TARGET_LOGS", "No eligible .log or .txt files were found", {
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
                "ownerToken": owner_token,
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

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.add_cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            if parse_qs(parsed.query).get("return") == ["app"]:
                self.send_response(302)
                self.send_header("Location", "https://active-ailab.github.io/active-lthr-workflow/")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            self.send_json(200, {"status": "ok", "jiraAuthentication": "user-password"})
            return
        if parsed.path == "/api/v1/auth/status":
            token, session = bearer_session(self)
            self.send_json(200, {"authenticated": bool(session)})
            return
        match = re.fullmatch(r"/api/v1/sessions/([A-Za-z0-9_-]+)/files/([A-Za-z0-9_-]+)", parsed.path)
        if match:
            token, auth_session = bearer_session(self)
            if not auth_session:
                self.send_json(401, {"error": "JIRA_LOGIN_REQUIRED", "message": "Log in to Jira before loading the attachment"})
                return
            entry = get_session_file(match.group(1), match.group(2))
            if not entry or not entry["path"].is_file():
                self.send_json(404, {"error": "FILE_EXPIRED_OR_NOT_FOUND", "message": "Temporary file is unavailable; search Jira again"})
                return
            with SESSION_LOCK:
                owner_token = SESSION_STORE.get(match.group(1), {}).get("ownerToken", "")
            if not secrets.compare_digest(owner_token, token):
                self.send_json(403, {"error": "SESSION_ACCESS_DENIED", "message": "This temporary file belongs to another Jira session"})
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
        if self.path == "/api/v1/auth/logout":
            token, _ = bearer_session(self)
            if token:
                with JIRA_LOGIN_LOCK:
                    JIRA_LOGIN_SESSIONS.pop(token, None)
            self.send_json(200, {"authenticated": False})
            return
        if self.path == "/api/v1/auth/login":
            try:
                payload = self.read_json_body(16 * 1024)
                jira_session = login_to_jira(
                    payload.get("baseUrl"),
                    str(payload.get("username", "")).strip(),
                    str(payload.get("password", "")),
                )
                self.send_json(200, {
                    "authenticated": True,
                    "sessionToken": create_login_session(jira_session),
                })
            except ServiceError as exc:
                self.send_json(exc.status, {"error": exc.code, "message": exc.message})
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception:
                self.send_json(500, {"error": "INTERNAL_ERROR", "message": "Jira login failed"})
            return
        match = re.fullmatch(r"/api/v1/issues/([A-Za-z][A-Za-z0-9_]*-[0-9]+)/attachments", self.path)
        if not match:
            self.send_json(404, {"error": "NOT_FOUND", "message": "Endpoint not found"})
            return
        issue_key = match.group(1).upper()
        if not ISSUE_KEY_RE.fullmatch(issue_key):
            self.send_json(400, {"error": "INVALID_ISSUE_KEY", "message": "Invalid Jira issue key"})
            return
        owner_token, auth_session = bearer_session(self)
        if not auth_session:
            self.send_json(401, {"error": "JIRA_LOGIN_REQUIRED", "message": "Please log in to Jira first"})
            return
        try:
            with tempfile.TemporaryDirectory(prefix="lthr-jira-") as temp_dir:
                manifest = build_file_session(issue_key, Path(temp_dir), auth_session, owner_token)
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

    def read_json_body(self, max_bytes):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ServiceError(400, "INVALID_REQUEST", "Invalid request body length")
        if length <= 0 or length > max_bytes:
            raise ServiceError(413, "REQUEST_SIZE_LIMIT", "Request body is missing or too large")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ServiceError(400, "INVALID_REQUEST", "Request body must be valid JSON")
        if not isinstance(payload, dict):
            raise ServiceError(400, "INVALID_REQUEST", "Request body must be a JSON object")
        return payload


def main():
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"LTHR Jira attachment service listening on {host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

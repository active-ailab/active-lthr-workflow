"""Jira issue helpers — meta, attachments, download, comment, upload, sample selection."""

import re
import shutil
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .watchlink_runtime import run_cmd

# ── CSV attachment name patterns ──
CSV_NAME_RE = re.compile(r"^gomore_data_.+\.csv$", re.IGNORECASE)
CSV_TIMESTAMP_RE = re.compile(r"^gomore_data_(\d{8})_(\d{6})\.csv$", re.IGNORECASE)

FindZeppCmdFn = Callable[[], List[str]]
GetIssueMetaFn = Callable[[Sequence[str], str], Dict[str, str]]
ListAttachmentsFn = Callable[[Sequence[str], str], List[Dict[str, str]]]
DownloadAttachmentFn = Callable[[Sequence[str], str, str, Path, float], Path]
LogFn = Callable[[str], None]


def find_zepp_cmd() -> List[str]:
    for name in ("zepp", "zepp.cmd", "zepp.exe"):
        if shutil.which(name):
            return [name]
    raise RuntimeError("zepp command not found in PATH")


def get_issue_meta(zepp_cmd: Sequence[str], issue_key: str) -> Dict[str, str]:
    out = run_cmd(list(zepp_cmd) + ["jira", "get", issue_key], timeout=30).stdout
    link_match = re.search(r"链接:\s*(https?://\S+)", out)
    title_match = re.search(r"标题:\s*(.+)", out)
    status_match = re.search(r"状态:\s*(.+)", out)
    return {
        "key": issue_key,
        "link": link_match.group(1).strip() if link_match else "",
        "title": title_match.group(1).strip() if title_match else "",
        "status": status_match.group(1).strip() if status_match else "",
    }


def list_jira_attachments(zepp_cmd: Sequence[str], issue_key: str) -> List[Dict[str, str]]:
    out = run_cmd(list(zepp_cmd) + ["jira", "attachment", "list", issue_key], timeout=30).stdout
    attachments: List[Dict[str, str]] = []
    for raw_line in out.splitlines():
        line = raw_line.strip()
        match = re.match(r"^\[(?P<id>\d+)\]\s+(?P<name>.+)$", line)
        if match:
            attachments.append({"id": match.group("id"), "filename": match.group("name").strip()})
    return attachments


def download_issue_attachment(
    zepp_cmd: Sequence[str],
    issue_key: str,
    filename: str,
    output_dir: Path,
    timeout: float = 120,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_cmd(
        list(zepp_cmd)
        + ["jira", "attachment", "download", issue_key, "-o", str(output_dir), "-f", filename],
        timeout=timeout,
    )
    path = output_dir / filename
    if not path.exists():
        raise RuntimeError(f"Downloaded attachment not found: {path}")
    return path


def choose_sample_attachments(
    attachments: List[Dict[str, str]],
    csv_name: str = "",
    all_csvs: bool = False,
) -> List[Dict[str, str]]:
    """Select gomore_data_*.csv attachment(s) from a Jira issue.

    - csv_name: pick the attachment with this exact filename or id
    - all_csvs: return all matching CSVs (sorted by timestamp)
    - default: return the single latest CSV
    """
    csv_attachments = [a for a in attachments if CSV_NAME_RE.match(a["filename"])]
    if not csv_attachments:
        raise RuntimeError("No gomore_data_*.csv attachment found in Jira issue")

    if csv_name:
        for attachment in csv_attachments:
            if attachment["filename"] == csv_name or attachment["id"] == csv_name:
                return [attachment]
        available = ", ".join(a["filename"] for a in csv_attachments)
        raise RuntimeError(f"Requested csv '{csv_name}' not found. Available: {available}")

    def _sort_key(item: Dict[str, str]) -> Tuple[str, str]:
        match = CSV_TIMESTAMP_RE.match(item["filename"])
        if match:
            return ("1", "".join(match.groups()), item["filename"])
        return ("0", item["filename"], item["filename"])

    sorted_attachments = sorted(csv_attachments, key=_sort_key)
    if all_csvs:
        return sorted_attachments
    return [sorted_attachments[-1]]


def add_jira_comment(zepp_cmd: Sequence[str], issue_key: str, comment: str, timeout: float = 30) -> None:
    run_cmd(list(zepp_cmd) + ["jira", "comment", "add", issue_key, comment], timeout=timeout)


def upload_attachments(
    zepp_cmd: Sequence[str],
    issue_key: str,
    file_paths: Sequence[Path],
    timeout: float = 120,
) -> None:
    run_cmd(list(zepp_cmd) + ["jira", "attachment", "add", issue_key] + [str(p) for p in file_paths], timeout=timeout)


def resolve_issue_sample_inputs(
    *,
    issue_key: str,
    download_dir: Path,
    csv_path: str = "",
    csv_name: str = "",
    all_csvs: bool = False,
    log_fn: Optional[LogFn] = None,
    find_zepp_cmd_fn: FindZeppCmdFn = find_zepp_cmd,
    get_issue_meta_fn: GetIssueMetaFn = get_issue_meta,
    list_jira_attachments_fn: ListAttachmentsFn = list_jira_attachments,
    download_issue_attachment_fn: DownloadAttachmentFn = download_issue_attachment,
) -> Tuple[Dict[str, str], List[Path], List[str]]:
    if csv_path:
        sample_csv = Path(csv_path).expanduser().resolve()
        if not sample_csv.exists():
            raise RuntimeError(f"Local CSV not found: {sample_csv}")
        return {
            "key": issue_key,
            "link": "",
            "title": "",
            "status": "",
        }, [sample_csv], []

    zepp_cmd = find_zepp_cmd_fn()
    if log_fn:
        log_fn("[INFO] Fetching issue meta from Jira...")
    issue_meta = get_issue_meta_fn(zepp_cmd, issue_key)
    if log_fn:
        log_fn(f"[INFO] Issue: {issue_meta.get('key')} — {issue_meta.get('title', '')[:60]}")
        log_fn("[INFO] Listing Jira attachments...")
    attachments = list_jira_attachments_fn(zepp_cmd, issue_key)
    if log_fn:
        log_fn(f"[INFO] Found {len(attachments)} attachments")
    sample_attachments = choose_sample_attachments(
        attachments,
        csv_name=csv_name,
        all_csvs=all_csvs,
    )
    sample_csvs = [
        download_issue_attachment_fn(
            zepp_cmd,
            issue_key,
            sample_attachment["filename"],
            download_dir,
            120,
        )
        for sample_attachment in sample_attachments
    ]
    return issue_meta, sample_csvs, zepp_cmd

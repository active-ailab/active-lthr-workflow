from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Sequence

from .jira_utils import add_jira_comment, upload_attachments
from .report_generator import build_comment


UploadAttachmentsFn = Callable[[Sequence[str], str, Sequence[Path]], None]
BuildCommentFn = Callable[
    [Mapping[str, object], Sequence[Mapping[str, object]], Sequence[str], str, str], str
]
AddJiraCommentFn = Callable[[Sequence[str], str, str], None]

SDK_VERSION_RE = re.compile(r"\bSDK Version:\s*([^\r\n]+)", re.IGNORECASE)


def extract_device_sdk_version(log_text: str) -> str:
    versions: List[str] = []
    for match in SDK_VERSION_RE.finditer(log_text or ""):
        version = match.group(1).strip()
        if version and version not in versions:
            versions.append(version)
    return ", ".join(versions) if versions else "未获取"


def _package_artifacts(artifact_paths: Sequence[Path]) -> Path:
    paths = [Path(path).resolve() for path in artifact_paths]
    if not paths:
        raise ValueError("No simulation artifacts were provided for Jira upload")

    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Cannot package missing simulation artifacts: "
            + ", ".join(str(path) for path in missing)
        )

    archive_names = [path.name for path in paths]
    if len(set(archive_names)) != len(archive_names):
        raise ValueError("Simulation artifacts have duplicate file names")

    first_path = paths[0]
    if all(path.parent == first_path.parent and path.stem == first_path.stem for path in paths):
        archive_name = f"{first_path.stem}.zip"
    else:
        archive_name = f"{first_path.stem}_artifacts.zip"
    archive_path = first_path.parent / archive_name
    if archive_path in paths:
        archive_path = first_path.parent / f"{first_path.stem}_artifacts.zip"

    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, arcname=path.name)
    return archive_path


def resolve_model_signature(model_name: str = "", reasoning_effort: str = "") -> str:
    cfg_model = ""
    cfg_effort = ""
    cfg = Path.home() / ".codex" / "config.toml"
    if cfg.exists():
        try:
            text = cfg.read_text(encoding="utf-8", errors="replace")
            model_match = re.search(r'^\s*model\s*=\s*"([^"]+)"', text, re.M)
            effort_match = re.search(r'^\s*model_reasoning_effort\s*=\s*"([^"]+)"', text, re.M)
            if model_match:
                cfg_model = model_match.group(1).strip()
            if effort_match:
                cfg_effort = effort_match.group(1).strip()
        except Exception:
            pass

    model = (
        model_name
        or os.getenv("CODEX_MODEL")
        or os.getenv("OPENAI_MODEL")
        or os.getenv("MODEL")
        or cfg_model
    ).strip()
    effort = (
        reasoning_effort
        or os.getenv("CODEX_REASONING_EFFORT")
        or os.getenv("REASONING_EFFORT")
        or cfg_effort
    ).strip()

    if model and effort:
        return f"（by {model} | reasoning={effort}）"
    if model:
        return f"（by {model}）"
    if effort:
        return f"（reasoning={effort}）"
    return ""


def publish_issue_artifacts(
    *,
    zepp_cmd: Sequence[str],
    issue_key: str,
    issue_meta: Mapping[str, object],
    sample_summaries: Sequence[Mapping[str, object]],
    artifact_paths: Sequence[Path],
    signature: str = "",
    device_sdk_version: str = "未获取",
    no_comment: bool = False,
    upload_attachments_fn: UploadAttachmentsFn = upload_attachments,
    build_comment_fn: BuildCommentFn = build_comment,
    add_jira_comment_fn: AddJiraCommentFn = add_jira_comment,
) -> str:
    archive_path = _package_artifacts(artifact_paths)
    upload_attachments_fn(zepp_cmd, issue_key, [archive_path])
    if no_comment:
        return ""

    comment = build_comment_fn(
        issue_meta,
        sample_summaries,
        [archive_path.name],
        signature,
        device_sdk_version,
    )
    add_jira_comment_fn(zepp_cmd, issue_key, comment)
    return comment

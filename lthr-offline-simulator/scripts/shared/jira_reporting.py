from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable, Dict, Mapping, Sequence

from .jira_utils import add_jira_comment, upload_attachments
from .report_generator import build_comment


UploadAttachmentsFn = Callable[[Sequence[str], str, Sequence[Path]], None]
BuildCommentFn = Callable[[Mapping[str, object], Sequence[Mapping[str, object]], Sequence[str], str], str]
AddJiraCommentFn = Callable[[Sequence[str], str, str], None]


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
    no_comment: bool = False,
    upload_attachments_fn: UploadAttachmentsFn = upload_attachments,
    build_comment_fn: BuildCommentFn = build_comment,
    add_jira_comment_fn: AddJiraCommentFn = add_jira_comment,
) -> str:
    upload_attachments_fn(zepp_cmd, issue_key, artifact_paths)
    if no_comment:
        return ""

    comment = build_comment_fn(
        issue_meta,
        sample_summaries,
        [path.name for path in artifact_paths],
        signature,
    )
    add_jira_comment_fn(zepp_cmd, issue_key, comment)
    return comment

"""Workflow reporter — unified artifact writing and runtime diagnostics.

Each entry script produces: (1) a raw log, (2) a JSON summary, and
optionally (3) a Markdown report.  This module centralises the write
logic and runtime hint injection so individual scripts don't need to
re-derive the same fire-and-forget write patterns.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .report_generator import build_report_markdown
from .watchlink_runtime import (
    build_runtime_diagnostic_payload,
    build_runtime_hint,
    format_timestamp,
)
from _summary_schema import (
    set_status,
    update_artifacts,
    update_metrics,
)


def inject_runtime_hint(
    summary: Dict[str, object],
    error_text: str,
    *,
    prefix: str = "",
) -> None:
    """Attach a diagnostic payload and optional runtime hint to *summary*."""
    payload: Dict[str, Any] = build_runtime_diagnostic_payload(error_text) or {}  # type: ignore[assignment]
    for k, v in payload.items():
        key = f"{prefix}{k}" if prefix else k
        summary[key] = v
    hint = build_runtime_hint(error_text)
    if hint:
        summary["runtime_hint"] = hint


def write_artifacts(
    *,
    log_path: Path,
    json_path: Path,
    report_path: Optional[Path] = None,
    report_artifact_key: str = "report_path",
    raw_log: str,
    summary: Mapping[str, object],
    report_extra: Optional[Mapping[str, object]] = None,
) -> Dict[str, str]:
    """Write log + JSON (+ optional Markdown report), update summary schema.

    Returns a dict of {log_path, json_path, [report_path]} suitable for
    print/display downstream.
    """
    summary_mut: Dict[str, object] = dict(summary)

    update_artifacts(
        summary_mut,
        log_path=str(log_path),
        json_path=str(json_path),
    )
    if report_path:
        update_artifacts(summary_mut, **{report_artifact_key: str(report_path)})

    update_metrics(summary_mut, log_size_bytes=len(raw_log))

    log_path.write_text(raw_log, encoding="utf-8")
    json_path.write_text(
        json.dumps(summary_mut, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    result: Dict[str, str] = {
        "log_path": str(log_path),
        "json_path": str(json_path),
    }

    if report_path:
        report_extra_dict = dict(report_extra or {})
        report_text = report_extra_dict.get("report_text", "")
        if not report_text:
            report_text = build_report_markdown(
                issue_meta=report_extra_dict.get("issue_meta", {}),
                sample_summaries=report_extra_dict.get("sample_summaries", []),
                raw_log_name=report_extra_dict.get("raw_log_name", log_path.name),
                signature=report_extra_dict.get("signature", ""),
            )
        report_path.write_text(report_text, encoding="utf-8")
        result["report_path"] = str(report_path)
        result[report_artifact_key] = str(report_path)
        result["report_text"] = report_text

    return result


def write_json_summary(
    *,
    json_path: Path,
    json_artifact_key: str = "json_path",
    summary: Mapping[str, object],
) -> Dict[str, str]:
    """Write a JSON-only summary payload and record the artifact path."""
    summary_mut: Dict[str, object] = dict(summary)
    update_artifacts(summary_mut, **{json_artifact_key: str(json_path)})
    json_path.write_text(
        json.dumps(summary_mut, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return {
        "json_path": str(json_path),
        json_artifact_key: str(json_path),
    }


def print_artifact_paths(result: Dict[str, str]) -> None:
    """Print artifact locations in the standard [OK] format."""
    print(f"[OK] log={result.get('log_path', '')}")
    print(f"[OK] json={result.get('json_path', '')}")
    if result.get("report_path"):
        print(f"[OK] report={result['report_path']}")

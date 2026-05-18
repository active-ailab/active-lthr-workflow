from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Mapping, Optional

from _summary_schema import init_summary, set_status, update_metrics

from .deploy_utils import LTHR_OUTPUT_ROOT
from .watchlink_runtime import format_timestamp


def resolve_default_output_dir(mode: str = "img_extract") -> Path:
    """Default IMG output root under the shared temp workspace."""
    return LTHR_OUTPUT_ROOT / "lthr_img_extract" / f"{mode}_{format_timestamp()}"


def init_img_extract_summary(
    *,
    mode: str,
    source_kind: str,
    source_mode: str,
    status: str = "running",
    source_extra: Optional[Mapping[str, object]] = None,
) -> Dict[str, object]:
    source = {"kind": source_kind}
    if source_extra:
        source.update(dict(source_extra))
    summary = init_summary(
        workflow="img_extract",
        status=status,
        mode=mode,
        source=source,
    )
    summary.update({
        "source_mode": source_mode,
        "mount_root": "",
        "log_files_copied": [],
        "log_files_deleted": 0,
        "batches": [],
        "summary": "",
        "report_path": "",
        "json_path": "",
        "batches_json_path": "",
        "summary_json_path": "",
    })
    return summary


def finalize_img_extract_summary(
    summary: Dict[str, object],
    *,
    batches: List[Dict[str, object]],
    log_files_copied: List[str],
    log_files_deleted: int,
    status: str,
) -> Dict[str, object]:
    summary["batches"] = batches
    summary["log_files_copied"] = list(log_files_copied)
    summary["log_files_deleted"] = int(log_files_deleted)
    set_status(summary, status)

    total_samples = sum(int(batch.get("total", 0)) for batch in batches)
    passed = sum(int(batch.get("passed", 0)) for batch in batches)
    failed = sum(int(batch.get("failed", 0)) for batch in batches)
    update_metrics(
        summary,
        batch_count=len(batches),
        log_file_count=len(log_files_copied),
        log_deleted_count=log_files_deleted,
        total_samples=total_samples,
        passed=passed,
        failed=failed,
    )
    return summary

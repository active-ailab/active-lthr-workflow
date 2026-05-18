#!/usr/bin/env python3
"""Shared summary schema helpers for LTHR simulator outputs."""

import time
from typing import Dict, Mapping, Optional


SCHEMA_VERSION = "lthr-summary-v1"


def init_summary(
    workflow: str,
    status: str = "initialized",
    issue: Optional[Mapping[str, object]] = None,
    mode: str = "",
    source: Optional[Mapping[str, object]] = None,
) -> Dict[str, object]:
    summary: Dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "workflow": workflow,
        "status": status,
        "primary_error_code": "",
        "error_codes": [],
        "runtime_hint": "",
        "timestamps": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        },
        "artifacts": {},
        "metrics": {},
        "issue": dict(issue) if issue else {},
    }
    if mode:
        summary["mode"] = mode
    if source:
        summary["source"] = dict(source)
    return summary


def set_status(summary: Dict[str, object], status: str) -> Dict[str, object]:
    summary["status"] = status
    return summary


def update_artifacts(summary: Dict[str, object], **artifacts: object) -> Dict[str, object]:
    target = summary.setdefault("artifacts", {})
    if not isinstance(target, dict):
        target = {}
        summary["artifacts"] = target
    for key, value in artifacts.items():
        if value in (None, ""):
            continue
        target[key] = value
    return summary


def update_metrics(summary: Dict[str, object], **metrics: object) -> Dict[str, object]:
    target = summary.setdefault("metrics", {})
    if not isinstance(target, dict):
        target = {}
        summary["metrics"] = target
    for key, value in metrics.items():
        if value is None:
            continue
        target[key] = value
    return summary


def apply_runtime_payload(
    summary: Dict[str, object],
    payload: Optional[Mapping[str, object]],
    prefix: str = "",
) -> Dict[str, object]:
    if not payload:
        return summary

    primary_error_code = str(payload.get("primary_error_code") or "")
    error_codes = list(payload.get("error_codes") or [])
    runtime_hint = str(payload.get("runtime_hint") or "")

    if prefix:
        normalized_prefix = prefix[:-1] if prefix.endswith("_") else prefix
        summary.setdefault("phase_diagnostics", {})
        phase_diagnostics = summary["phase_diagnostics"]
        if isinstance(phase_diagnostics, dict):
            phase_diagnostics[normalized_prefix] = {
                "primary_error_code": primary_error_code,
                "error_codes": error_codes,
                "runtime_hint": runtime_hint,
            }
        if primary_error_code:
            summary[f"{prefix}primary_error_code"] = primary_error_code
        if error_codes:
            summary[f"{prefix}error_codes"] = error_codes
        if runtime_hint:
            summary[f"{prefix}runtime_hint"] = runtime_hint
        return summary

    if primary_error_code:
        summary["primary_error_code"] = primary_error_code
    if error_codes:
        summary["error_codes"] = error_codes
    if runtime_hint:
        summary["runtime_hint"] = runtime_hint
    return summary
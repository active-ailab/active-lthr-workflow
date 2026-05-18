from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .utest_runner import listen_uart, send_uart_cmd


START_MARKER = "GoMore Offline CSV Test Start"
END_MARKER = "GoMore Offline CSV Test End"

RE_SAMPLE_PROGRESS = re.compile(r"SAMPLE\s*\[\s*(\d+)\s*/\s*(\d+)\s*\]")
RE_ROUND_PROGRESS = re.compile(r"Round\s*(\d+)\s*/\s*(\d+)")
RE_SAMPLE_RESULT = re.compile(r"Sample\[\d+/\d+\]\s+(PASS|FAIL)")
RE_SUMMARY = re.compile(r"All sample cases completed:\s*total=(\d+),\s*failed=(\d+)")


SendUartFn = Callable[[Path, str, str, int], str]
ListenUartFn = Callable[[Path, str, int], str]
TimeFn = Callable[[], float]
ProgressCallback = Callable[[Dict[str, int]], None]


def has_test_activity(text: str) -> bool:
    return any((
        START_MARKER in text,
        END_MARKER in text,
        RE_SAMPLE_PROGRESS.search(text),
        RE_ROUND_PROGRESS.search(text),
        RE_SAMPLE_RESULT.search(text),
        RE_SUMMARY.search(text),
    ))


def init_progress(*, total_rounds: int = 3) -> Dict[str, int]:
    return {
        "current_sample": 0,
        "total_samples": 0,
        "current_round": 0,
        "total_rounds": total_rounds,
        "passed": 0,
        "failed": 0,
    }


def update_progress(progress: Dict[str, int], chunk: str) -> Dict[str, int]:
    sample_match = RE_SAMPLE_PROGRESS.search(chunk)
    if sample_match:
        progress["current_sample"] = int(sample_match.group(1))
        progress["total_samples"] = int(sample_match.group(2))

    round_match = RE_ROUND_PROGRESS.search(chunk)
    if round_match:
        progress["current_round"] = int(round_match.group(1))
        progress["total_rounds"] = int(round_match.group(2))

    for result_match in RE_SAMPLE_RESULT.finditer(chunk):
        if result_match.group(1) == "PASS":
            progress["passed"] += 1
        else:
            progress["failed"] += 1
    return progress


def collect_summary_counts(full_log: str, progress: Dict[str, int]) -> Dict[str, int]:
    summary_match = RE_SUMMARY.search(full_log)
    if summary_match:
        total_samples = int(summary_match.group(1))
        failed = int(summary_match.group(2))
        return {
            "total_samples": total_samples,
            "failed": failed,
            "passed": total_samples - failed,
        }
    return {
        "total_samples": progress["total_samples"],
        "passed": progress["passed"],
        "failed": progress["failed"],
    }


def run_multi_sample_simulation(
    *,
    wlctl_path: Path,
    uart_port: str,
    run_timeout_sec: int,
    listen_chunk_sec: int,
    progress_callback: Optional[ProgressCallback] = None,
    send_uart_cmd_fn: SendUartFn = send_uart_cmd,
    listen_uart_fn: ListenUartFn = listen_uart,
    time_fn: TimeFn = time.time,
) -> Tuple[Dict[str, object], str]:
    raw_sections: List[str] = []
    run_started_at = time_fn()

    initial = send_uart_cmd_fn(
        wlctl_path,
        "utest gomore_offline_csv_multi_tests",
        uart_port,
        2000,
    )
    raw_sections.append("===== RUN MULTI_TESTS =====\n")
    raw_sections.append(initial)
    full_log = initial

    started = has_test_activity(full_log)
    ended = END_MARKER in full_log
    progress = init_progress()
    deadline = time_fn() + run_timeout_sec

    while time_fn() < deadline and not ended:
        chunk_sec = max(1, min(listen_chunk_sec, int(deadline - time_fn()) or 1))
        chunk = listen_uart_fn(wlctl_path, uart_port, chunk_sec)
        raw_sections.append(chunk)
        full_log += chunk

        if has_test_activity(chunk):
            started = True

        update_progress(progress, chunk)
        if started and not ended and progress_callback:
            progress_callback(dict(progress))

        if END_MARKER in chunk:
            ended = True
            break

    total_runtime = time_fn() - run_started_at
    counts = collect_summary_counts(full_log, progress)

    if not started:
        status = "test_not_started"
    elif not ended:
        status = "test_timeout"
    else:
        status = "completed"

    return {
        "status": status,
        "total_samples": counts["total_samples"],
        "passed": counts["passed"],
        "failed": counts["failed"],
        "runtime_seconds": round(total_runtime),
        "log_size_bytes": len(full_log),
        "started": started,
        "ended": ended,
        "progress": dict(progress),
    }, "".join(raw_sections)

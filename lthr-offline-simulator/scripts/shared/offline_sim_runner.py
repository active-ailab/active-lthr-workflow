from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .data_analyzer import analyze_trend, summarize_stability
from .device_flow import deploy_data_to_device, ensure_device_ready_sequence
from .utest_runner import listen_uart, send_uart_cmd


START_MARKER = "GoMore Offline CSV Test Start"
END_MARKER = "GoMore Offline CSV Test End"
RESULT_RE = re.compile(
    r"Gomore output ltSpeed=(?P<ltSpeed>[-+]?\d+(?:\.\d+)?) "
    r"lh=(?P<lh>-?\d+) mh=(?P<mh>-?\d+) guideResult=(?P<guideResult>-?\d+)"
)


DeployHook = Callable[[str], Dict[str, object]]
SendUartFn = Callable[[Path, str, str, int, Optional[float]], str]
ListenUartFn = Callable[[Path, str, int, Optional[float]], str]
TimeFn = Callable[[], float]


def _match_to_result(match: re.Match[str]) -> Dict[str, object]:
    return {
        "ltSpeed": float(match.group("ltSpeed")),
        "lh": int(match.group("lh")),
        "mh": int(match.group("mh")),
        "guideResult": int(match.group("guideResult")),
        "raw": match.group(0),
    }


def parse_result_matches(text: str) -> List[Dict[str, object]]:
    return [_match_to_result(match) for match in RESULT_RE.finditer(text or "")]


def parse_latest_result(text: str) -> Optional[Dict[str, object]]:
    matches = parse_result_matches(text)
    return matches[-1] if matches else None


def run_single_simulation(
    wlctl_path: Path,
    run_index: int,
    uart_port: str,
    run_timeout_sec: int,
    listen_chunk_sec: int,
    *,
    send_uart_cmd_fn: SendUartFn = send_uart_cmd,
    listen_uart_fn: ListenUartFn = listen_uart,
    time_fn: TimeFn = time.time,
) -> Tuple[Dict[str, object], str]:
    run_log_parts: List[str] = []
    initial = send_uart_cmd_fn(
        wlctl_path,
        "utest gomore_offline_csv_tests",
        uart_port,
        1500,
        20,
    )
    run_log_parts.append(initial)
    full_log = initial
    latest_result = parse_latest_result(full_log)
    started = (
        START_MARKER in full_log
        or END_MARKER in full_log
        or latest_result is not None
        or "utest module [gomore_offline_csv_tests] test report" in full_log
    )

    deadline = time_fn() + run_timeout_sec
    ended = END_MARKER in full_log

    while time_fn() < deadline and not ended:
        seconds = max(1, min(listen_chunk_sec, int(deadline - time_fn()) or 1))
        try:
            chunk = listen_uart_fn(
                wlctl_path,
                uart_port,
                seconds,
                seconds + 8,
            )
        except Exception as exc:
            run_log_parts.append(f"\n[WARN] UART listen chunk failed during run #{run_index}: {exc}\n")
            continue
        run_log_parts.append(chunk)
        full_log += chunk
        if START_MARKER in chunk:
            started = True
        parsed = parse_latest_result(chunk)
        if parsed:
            latest_result = parsed
            started = True
        if "utest module [gomore_offline_csv_tests] test report" in chunk:
            started = True
        if started and END_MARKER in chunk:
            ended = True
            break

    if not started:
        raise RuntimeError(f"Run #{run_index}: did not detect '{START_MARKER}'")
    if not ended:
        tail = full_log[-4000:]
        raise RuntimeError(f"Run #{run_index}: timed out waiting for '{END_MARKER}'. Recent UART output:\n{tail}")

    result: Dict[str, object] = {
        "run": run_index,
        "status": "ok",
        "started": started,
        "ended": ended,
    }
    if latest_result:
        result.update(latest_result)
    else:
        result["status"] = "missing_result"
        result["raw"] = ""
    return result, "".join(run_log_parts)


def run_sample_flow(
    *,
    wlctl_path: Path,
    sample_csv: Path,
    disk_root: str,
    mgr_port: str,
    uart_port: str,
    runs: int,
    boot_timeout_sec: int,
    mount_wait_sec: int,
    run_timeout_sec: int,
    listen_chunk_sec: int,
    serial_settle_sec: int,
    deploy_attempts: int,
    deploy_only: bool,
    deploy_hook: DeployHook,
    deploy_hook_wsl: Optional[DeployHook] = None,
) -> Tuple[Dict[str, object], str]:
    raw_sections: List[str] = []
    gomore_root_text, deploy_log = deploy_data_to_device(
        wlctl_path=wlctl_path,
        mgr_port=mgr_port,
        disk_root=disk_root,
        mount_wait_sec=mount_wait_sec,
        deploy_attempts=deploy_attempts,
        deploy_hook=deploy_hook,
        deploy_hook_wsl=deploy_hook_wsl or deploy_hook,
    )
    raw_sections.append(deploy_log)

    raw_sections.append("===== DEVICE READY SEQUENCE =====\n")
    ensure_device_ready_sequence(
        wlctl_path=wlctl_path,
        mgr_port=mgr_port,
        uart_port=uart_port,
        boot_timeout_sec=boot_timeout_sec,
        serial_settle_sec=serial_settle_sec,
    )

    if deploy_only:
        return {
            "sample_csv": sample_csv.name,
            "gomore_root": gomore_root_text,
            "mode": "deploy_only",
            "results": [],
            "stability": "deploy-only 检查通过（已跳过 UART utest）",
            "trend": {
                "valid_runs": 0,
                "total_runs": 0,
                "note": "deploy-only mode skipped UART utest",
            },
        }, "".join(raw_sections)

    results: List[Dict[str, object]] = []
    for idx in range(1, runs + 1):
        raw_sections.append(f"===== RUN {idx} =====\n")
        result, run_log = run_single_simulation(
            wlctl_path,
            run_index=idx,
            uart_port=uart_port,
            run_timeout_sec=run_timeout_sec,
            listen_chunk_sec=listen_chunk_sec,
        )
        results.append(result)
        raw_sections.append(run_log)

    return {
        "sample_csv": sample_csv.name,
        "gomore_root": gomore_root_text,
        "results": results,
        "stability": summarize_stability(results),
        "trend": analyze_trend(results),
    }, "".join(raw_sections)

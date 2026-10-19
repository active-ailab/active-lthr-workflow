from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .data_analyzer import analyze_trend, summarize_stability
from .device_flow import deploy_data_to_device, ensure_device_ready_sequence
from .utest_runner import run_uart_session


START_MARKER = "GoMore Offline CSV Test Start"
END_MARKER = "GoMore Offline CSV Test End"
RESULT_RE = re.compile(
    r"Gomore output ltSpeed=(?P<ltSpeed>[-+]?\d+(?:\.\d+)?) "
    r"lh=(?P<lh>-?\d+) mh=(?P<mh>-?\d+) guideResult=(?P<guideResult>-?\d+)"
)


DeployHook = Callable[[str], Dict[str, object]]
RunUartSessionFn = Callable[[Path, str, str, int, Optional[float]], str]


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
    *,
    run_uart_session_fn: RunUartSessionFn = run_uart_session,
) -> Tuple[Dict[str, object], str]:
    run_log_parts: List[str] = []
    timeout = max(float(run_timeout_sec) + 30.0, 45.0)
    try:
        full_log = run_uart_session_fn(
            wlctl_path,
            "utest gomore_offline_csv_tests",
            uart_port=uart_port,
            seconds=run_timeout_sec,
            timeout=timeout,
            until_marker=END_MARKER,
        )
    except Exception as exc:
        raise RuntimeError(f"Run #{run_index}: UART session failed: {exc}") from exc
    run_log_parts.append(full_log)

    started = START_MARKER in full_log
    ended = END_MARKER in full_log
    latest_result = parse_latest_result(full_log)

    if not started:
        raise RuntimeError(
            f"Run #{run_index}: UTEST_START_NOT_CONFIRMED; did not detect '{START_MARKER}'. "
            f"Recent UART output:\n{full_log[-4000:]}"
        )
    if not ended:
        raise RuntimeError(
            f"Run #{run_index}: UTEST_TIMEOUT; did not detect '{END_MARKER}'. "
            f"Recent UART output:\n{full_log[-4000:]}"
        )

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
    serial_settle_sec: int,
    deploy_attempts: int,
    deploy_only: bool,
    deploy_hook: DeployHook,
    deploy_hook_wsl: Optional[DeployHook] = None,
    clean_between_runs: bool = False,
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
        if idx > 1 and clean_between_runs:
            raw_sections.append(f"===== PREPARE ISOLATED RUN {idx} =====\n")
            try:
                _, repeat_deploy_log = deploy_data_to_device(
                    wlctl_path=wlctl_path,
                    mgr_port=mgr_port,
                    disk_root=disk_root,
                    mount_wait_sec=mount_wait_sec,
                    deploy_attempts=deploy_attempts,
                    deploy_hook=deploy_hook,
                    deploy_hook_wsl=deploy_hook_wsl or deploy_hook,
                )
                raw_sections.append(repeat_deploy_log)
                ensure_device_ready_sequence(
                    wlctl_path=wlctl_path,
                    mgr_port=mgr_port,
                    uart_port=uart_port,
                    boot_timeout_sec=boot_timeout_sec,
                    serial_settle_sec=serial_settle_sec,
                )
            except Exception as exc:
                error = f"Run #{idx}: failed to reset and redeploy clean state: {exc}"
                results.append({"run": idx, "status": "deploy_error", "error": error})
                raw_sections.append(f"[ERROR] {error}\n")
                continue

        try:
            result, run_log = run_single_simulation(
                wlctl_path,
                run_index=idx,
                uart_port=uart_port,
                run_timeout_sec=run_timeout_sec,
            )
        except Exception as exc:
            error = str(exc)
            if "UART session failed" in error:
                status = "uart_error"
            elif "UTEST_START_NOT_CONFIRMED" in error:
                status = "utest_start_not_confirmed"
            else:
                status = "utest_timeout"
            result = {"run": idx, "status": status, "error": error}
            run_log = f"[ERROR] {error}\n"
        results.append(result)
        raw_sections.append(run_log)

    return {
        "sample_csv": sample_csv.name,
        "gomore_root": gomore_root_text,
        "results": results,
        "stability": summarize_stability(results),
        "trend": analyze_trend(results),
    }, "".join(raw_sections)

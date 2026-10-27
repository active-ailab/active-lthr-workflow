from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .data_analyzer import analyze_trend, summarize_stability
from .data_sample_ops import extract_simulation_artifacts
from .device_flow import deploy_data_to_device, ensure_device_ready_sequence
from .offline_sim_contract import (
    END_MARKER,
    RESULT_LINE_RE,
    SAMPLE_PROGRESS_RE,
    START_MARKER,
    TEST_COMMAND,
    make_run_id,
    normalize_log_for_report,
    parse_result_ini,
    parse_round_results,
    validate_result,
    write_config,
)
from .utest_runner import listen_uart, run_uart_session, send_uart_cmd


RESULT_RE = RESULT_LINE_RE


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
    return [_match_to_result(match) for match in RESULT_LINE_RE.finditer(text or "")]


def parse_latest_result(text: str) -> Optional[Dict[str, object]]:
    matches = parse_result_matches(text)
    return matches[-1] if matches else None


def execute_simulation_utest(
    *,
    wlctl_path: Path,
    uart_port: str,
    run_timeout_sec: int,
    listen_chunk_sec: int,
    send_uart_cmd_fn=send_uart_cmd,
    listen_uart_fn=listen_uart,
    time_fn=time.time,
    state_callback: Optional[Callable[[Dict[str, object], str], None]] = None,
) -> Tuple[Dict[str, object], str]:
    """Send the new UTest command once, then only listen until its end marker or timeout."""
    start_time = time_fn()
    logs: List[str] = ["===== RUN CONFIGURED SIMULATION =====\n"]
    try:
        initial = send_uart_cmd_fn(
            wlctl_path,
            TEST_COMMAND,
            uart_port=uart_port,
            read_ms=2000,
            timeout=10,
        )
    except Exception as exc:
        log = "[ERROR] UART command failed: {}\n".format(exc)
        logs.append(log)
        return {
            "status": "uart_error",
            "started": False,
            "ended": False,
            "runtime_seconds": round(time_fn() - start_time),
            "error": str(exc),
        }, "".join(logs)

    logs.append(initial or "")
    full_log = initial or ""
    started = START_MARKER in full_log or SAMPLE_PROGRESS_RE.search(full_log) is not None
    ended = END_MARKER in full_log
    deadline = time_fn() + run_timeout_sec
    listen_error = ""

    while time_fn() < deadline and not ended:
        chunk_seconds = max(1, min(listen_chunk_sec, int(deadline - time_fn()) or 1))
        try:
            chunk = listen_uart_fn(
                wlctl_path,
                uart_port=uart_port,
                seconds=chunk_seconds,
                timeout=chunk_seconds + 8,
            )
        except Exception as exc:
            listen_error = str(exc)
            logs.append("\n[ERROR] UART listen failed: {}\n".format(listen_error))
            break
        logs.append(chunk or "")
        full_log += chunk or ""
        started = started or START_MARKER in full_log or SAMPLE_PROGRESS_RE.search(full_log) is not None
        ended = END_MARKER in full_log
        if state_callback:
            state_callback(
                {
                    "status": "completed" if ended else ("running" if started else "waiting"),
                    "started": started,
                    "ended": ended,
                    "runtime_seconds": round(time_fn() - start_time),
                    "log_size_bytes": len(full_log),
                },
                full_log,
            )

    if ended:
        status = "completed"
    elif listen_error:
        status = "uart_error"
    elif started:
        status = "test_timeout"
    else:
        status = "test_not_started"
    return {
        "status": status,
        "started": started,
        "ended": ended,
        "runtime_seconds": round(time_fn() - start_time),
        "log_size_bytes": len(full_log),
        "error": listen_error,
    }, "".join(logs)


def run_single_simulation(
    wlctl_path: Path,
    run_index: int,
    uart_port: str,
    run_timeout_sec: int,
    *,
    run_uart_session_fn: RunUartSessionFn = run_uart_session,
) -> Tuple[Dict[str, object], str]:
    """Compatibility wrapper for callers needing a single configured UTest invocation."""
    timeout = max(float(run_timeout_sec) + 30.0, 45.0)
    try:
        full_log = run_uart_session_fn(
            wlctl_path,
            TEST_COMMAND,
            uart_port=uart_port,
            seconds=run_timeout_sec,
            timeout=timeout,
            until_marker=END_MARKER,
        )
    except Exception as exc:
        error = "Run #{}: UART session failed: {}".format(run_index, exc)
        return {"run": run_index, "status": "uart_error", "error": error}, "[ERROR] {}\n".format(error)

    started = START_MARKER in full_log
    ended = END_MARKER in full_log
    result: Dict[str, object] = {
        "run": run_index,
        "status": "completed" if ended else ("test_timeout" if started else "test_not_started"),
        "started": started,
        "ended": ended,
    }
    latest_result = parse_latest_result(full_log)
    if latest_result:
        result.update(latest_result)
    else:
        # No threshold output is a valid algorithm outcome; execution status comes from sim_result.ini.
        result["result_missing"] = True
    return result, full_log


def run_sample_flow(
    *,
    wlctl_path: Path,
    data_sample_dir: Path,
    sample_paths: Sequence[str],
    mode: str,
    rounds: int,
    run_id: str,
    output_dir: Path,
    disk_root: str,
    mgr_port: str,
    uart_port: str,
    boot_timeout_sec: int,
    mount_wait_sec: int,
    run_timeout_sec: int,
    listen_chunk_sec: int,
    pre_run_delay_sec: int = 0,
    serial_settle_sec: int,
    deploy_attempts: int,
    deploy_only: bool,
    deploy_hook: DeployHook,
    deploy_hook_wsl: Optional[DeployHook] = None,
    state_callback: Optional[Callable[[Dict[str, object], str], None]] = None,
    device_artifacts_dir: Optional[Path] = None,
) -> Tuple[Dict[str, object], str]:
    write_config(
        data_sample_dir,
        run_id=run_id,
        mode=mode,
        rounds=rounds,
        sample_paths=sample_paths,
    )
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
    if pre_run_delay_sec > 0:
        raw_sections.append("===== PRE-RUN DELAY {}s =====\n".format(pre_run_delay_sec))
        time.sleep(pre_run_delay_sec)

    if deploy_only:
        return {
            "run_id": run_id,
            "mode": mode,
            "rounds": rounds,
            "sample_count": len(sample_paths),
            "status": "deploy_only",
            "results": [],
            "stability": "deploy-only 检查通过（已跳过 UART utest）",
            "trend": {"valid_runs": 0, "total_runs": 0, "note": "UART UTest skipped"},
        }, "".join(raw_sections)

    utest_state, run_log = execute_simulation_utest(
        wlctl_path=wlctl_path,
        uart_port=uart_port,
        run_timeout_sec=run_timeout_sec,
        listen_chunk_sec=listen_chunk_sec,
        state_callback=state_callback,
    )
    raw_sections.append(run_log)

    device_artifacts_dir = device_artifacts_dir or output_dir
    device_artifacts_dir.mkdir(parents=True, exist_ok=True)
    extraction_log = ""
    extraction_info: Dict[str, object] = {}

    def _extract(data_root: str) -> Dict[str, object]:
        info = extract_simulation_artifacts(
            data_root,
            run_id=run_id,
            mode=mode,
            rounds=rounds,
            sample_count=len(sample_paths),
            output_dir=device_artifacts_dir,
        )
        if info.get("validation_error"):
            info["extraction_error"] = info["validation_error"]
        extraction_info.update(info)
        return info

    if utest_state.get("ended"):
        try:
            _, extraction_log = deploy_data_to_device(
                wlctl_path=wlctl_path,
                mgr_port=mgr_port,
                disk_root=disk_root,
                mount_wait_sec=mount_wait_sec,
                deploy_attempts=1,
                deploy_hook=_extract,
                deploy_hook_wsl=_extract,
            )
        except Exception as exc:
            extraction_info = {"extraction_error": str(exc)}
            extraction_log = "[ERROR] Result extraction failed: {}\n".format(exc)
    else:
        extraction_info = {
            "extraction_error": "UTest end marker was not observed; device artifacts remain on device"
        }
        extraction_log = "[ERROR] {}\n".format(extraction_info["extraction_error"])
    if extraction_info.get("data_sample_cleared"):
        extraction_log += "[INFO] Cleared device data_sample after successful artifact extraction.\n"
    raw_sections.append(extraction_log)

    result_payload = extraction_info.get("result") if extraction_info else None
    if not isinstance(result_payload, dict):
        result_path = device_artifacts_dir / "sim_result.ini"
        if result_path.exists():
            result_payload = parse_result_ini(result_path.read_text(encoding="utf-8", errors="replace"))
            try:
                validate_result(
                    result_payload,
                    run_id,
                    expected_mode=mode,
                    expected_rounds=rounds,
                    expected_sample_count=len(sample_paths),
                )
            except Exception as exc:
                extraction_info["extraction_error"] = str(exc)
        else:
            result_payload = {}

    result_samples = result_payload.get("samples", []) if isinstance(result_payload, dict) else []
    normalized_log = normalize_log_for_report("".join(raw_sections), sample_paths)
    parsed_samples = parse_round_results(
        run_log,
        samples=sample_paths,
        rounds=rounds,
        result_samples=result_samples if isinstance(result_samples, list) else [],
    )
    for sample in parsed_samples:
        sample["stability"] = summarize_stability(sample["results"])
        sample["trend"] = analyze_trend(sample["results"])
        sample["gomore_root"] = gomore_root_text

    overall_status = str(result_payload.get("overall_status", "failed")) if isinstance(result_payload, dict) else "failed"
    if extraction_info.get("extraction_error") or utest_state.get("status") != "completed":
        overall_status = "failed"
    errors: List[str] = []
    if extraction_info.get("extraction_error"):
        errors.append(str(extraction_info["extraction_error"]))
    if utest_state.get("error"):
        errors.append("UART error: {}".format(utest_state["error"]))
    if isinstance(result_payload, dict) and result_payload.get("error_reason"):
        errors.append(
            "C error stage={} reason={} code={}".format(
                result_payload.get("error_stage", ""),
                result_payload.get("error_reason", ""),
                result_payload.get("error_code", ""),
            )
        )
    for index, sample in enumerate(parsed_samples, 1):
        if sample.get("error_reason"):
            errors.append(
                "sample {} ({}) stage={} reason={} code={} stopped_round={}".format(
                    index,
                    sample.get("sample_csv", ""),
                    sample.get("error_stage", ""),
                    sample.get("error_reason", ""),
                    sample.get("error_code", ""),
                    sample.get("stopped_round", ""),
                )
            )
    if overall_status != "success" and not errors:
        errors.append("C simulation overall_status={}".format(overall_status))
    summary: Dict[str, object] = {
        "run_id": run_id,
        "mode": mode,
        "rounds": rounds,
        "sample_count": len(sample_paths),
        "gomore_root": gomore_root_text,
        "status": overall_status,
        "utest_status": utest_state.get("status"),
        "started": utest_state.get("started", False),
        "ended": utest_state.get("ended", False),
        "runtime_seconds": utest_state.get("runtime_seconds", 0),
        "result": result_payload,
        "result_path": extraction_info.get("result_path", ""),
        "history_paths": extraction_info.get("history_paths", []),
        "data_sample_cleared": extraction_info.get("data_sample_cleared", False),
        "samples": parsed_samples,
        "error": "; ".join(errors),
        "errors": errors,
        "log_path": "",
    }
    return summary, normalized_log

#!/usr/bin/env python3
"""
Minimal script to run gomore offline CSV test via UART only.
Data (CSV + cleaned state) must already be on device.
Skips all mount/disk operations — no sudo needed.
"""
import argparse
import sys
import time
from pathlib import Path

# Add script dir to path for shared helper imports
_script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_script_dir))

from _output_templates import render_asset_template
from _summary_schema import apply_runtime_payload, init_summary, update_metrics
from shared.deploy_utils import ensure_mgr_vbus_on
from shared.offline_sim_runner import END_MARKER, START_MARKER, parse_result_matches
from shared.utest_runner import listen_uart, prepare_uart_shell_for_long_log, send_uart_cmd
from shared.watchlink_runtime import (
    _safe_reconfigure_stdio,
    build_runtime_diagnostic_payload,
    build_runtime_hint,
    build_wlctl_path,
)
from shared.workflow_reporter import write_artifacts


def _build_utest_summary_markdown(summary: dict) -> str:
    latest = summary.get("latest_result")
    all_results = summary.get("all_results") or []
    relevant_lines = summary.get("relevant_lines") or []
    error_lines = summary.get("error_lines") or []

    if latest:
        result_lines = [
            "## Latest Result",
            "",
            f"- ltSpeed: {latest['ltSpeed']}",
            f"- lh: {latest['lh']}",
            f"- mh: {latest['mh']}",
            f"- guideResult: {latest['guideResult']}",
            f"- Raw: {latest['raw']}",
        ]
    else:
        result_lines = [
            "## Latest Result",
            "",
            "- No parsed Gomore result was found in the UART log.",
        ]

    if all_results:
        result_lines.extend(["", "## All Parsed Results", ""])
        for idx, item in enumerate(all_results, 1):
            result_lines.append(f"- #{idx}: {item['raw']}")

    error_section = ["## Error Summary", "", f"- Error Count: {summary.get('error_count', 0)}"]
    if error_lines:
        error_section.append("")
        for line in error_lines[:10]:
            error_section.append(f"- {line}")

    relevant_section = ["## Relevant Log Lines", ""]
    if relevant_lines:
        for line in relevant_lines[:20]:
            relevant_section.append(f"- {line}")
    else:
        relevant_section.append("- No additional relevant lines were extracted.")

    return render_asset_template(
        "utest_summary_template.md",
        {
            "generated_at": summary["generated_at"],
            "uart_port": summary["uart_port"],
            "started": summary["started"],
            "ended": summary["ended"],
            "chunk_count": summary["chunk_count"],
            "run_duration": summary["run_duration"],
            "raw_log_name": summary["raw_log_name"],
            "error_code_line": (
                f"Primary Error Code: {summary.get('primary_error_code')}\n"
                if summary.get("primary_error_code") else ""
            ),
            "result_section": "\n".join(result_lines),
            "error_section": "\n".join(error_section),
            "relevant_section": "\n".join(relevant_section),
        },
    ).rstrip() + "\n"


def main() -> int:
    _safe_reconfigure_stdio()

    parser = argparse.ArgumentParser(description="Run utest gomore_offline_csv_tests via UART only")
    parser.add_argument("--uart-port", default="COM36", help="UART port")
    parser.add_argument("--mgr-port", default="COM37", help="MGR port for VBUS check")
    parser.add_argument("--run-timeout-sec", type=int, default=300, help="Max wait for test end")
    parser.add_argument("--listen-chunk-sec", type=int, default=5, help="UART listen window per poll")
    parser.add_argument("--probe-timeout-sec", type=int, default=8, help="Timeout used when probing a UART port")
    parser.add_argument("--skip-vbus-check", action="store_true", help="Skip MGR VBUS check (use when VBUS is already confirmed ON)")
    args = parser.parse_args()

    wlctl_path = build_wlctl_path()

    # VBUS 检查：确保 MGR/UART 通讯电源已开启
    if not args.skip_vbus_check:
        print(f"[INFO] Checking MGR VBUS status via {args.mgr_port} ...")
        ensure_mgr_vbus_on(wlctl_path, mgr_port=args.mgr_port, timeout_sec=max(6.0, args.probe_timeout_sec / 2.0))
        print(f"[INFO] VBUS check passed")
    else:
        print("[INFO] SKIP VBUS check (--skip-vbus-check)")
    run_started_at = time.time()
    candidate_ports = [args.uart_port]
    for port in ("COM36", "COM37", "COM38", "COM39"):
        if port not in candidate_ports:
            candidate_ports.append(port)

    selected_port = args.uart_port
    prepare_log = ""
    last_error = None
    for port in candidate_ports:
        try:
            prepare_log = prepare_uart_shell_for_long_log(
                wlctl_path,
                uart_port=port,
                timeout_sec=args.probe_timeout_sec,
            )
            initial = send_uart_cmd(
                wlctl_path,
                "utest gomore_offline_csv_tests",
                uart_port=port,
                read_ms=1500,
                timeout=args.probe_timeout_sec,
            )
            selected_port = port
            break
        except Exception as exc:
            last_error = exc
            print(f"[WARN] UART port {port} probe failed: {exc}")
    else:
        raise RuntimeError(f"Unable to communicate with any candidate UART port: {candidate_ports}. last_error={last_error}")

    print(f"[INFO] wlctl: {wlctl_path}")
    print(f"[INFO] UART port: {selected_port}")
    print(f"[INFO] Starting utest gomore_offline_csv_tests ...")
    print(f"[UART initial response ({len(initial)} chars)]")
    if initial:
        print(initial[-1000:] if len(initial) > 1000 else initial)

    full_log = prepare_log + "===== RUN SINGLE_TEST =====\n" + initial
    started = START_MARKER in full_log
    latest_result = None
    deadline = time.time() + args.run_timeout_sec
    ended = END_MARKER in full_log

    chunk_count = 0
    while time.time() < deadline and not ended:
        seconds = max(1, min(args.listen_chunk_sec, int(deadline - time.time()) or 1))
        chunk = listen_uart(wlctl_path, uart_port=selected_port, seconds=seconds, timeout=seconds + 5)
        chunk_count += 1
        full_log += chunk

        if START_MARKER in chunk:
            started = True
            print(f"[CHUNK {chunk_count}] START marker detected!")
        if END_MARKER in chunk:
            ended = True
            print(f"[CHUNK {chunk_count}] END marker detected!")

        parsed = parse_result_matches(chunk)
        for item in parsed:
            latest_result = item
            print(f"[CHUNK {chunk_count}] RESULT: {item['raw']}")

        if chunk_count % 12 == 0:
            elapsed = int(time.time() - (deadline - args.run_timeout_sec))
            print(f"[INFO] Listening... chunk={chunk_count}, elapsed={elapsed}s, log={len(full_log)} chars")

        if ended:
            break

    all_results = parse_result_matches(full_log)
    latest_result = all_results[-1] if all_results else None
    error_lines = []
    relevant_lines = []
    for line in full_log.splitlines():
        lowered = line.lower()
        if "gomore" in lowered or "csv" in lowered or "error" in lowered or "ret=" in line:
            relevant_lines.append(line)
        if "process error" in lowered or "error=-4" in line or "ret=-4" in line:
            error_lines.append(line)

    # Save raw log and summary artifacts
    output_dir = Path.home() / "Lyon" / "gomore_utest_results"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    log_path = output_dir / f"utest_raw_{timestamp}.log"
    summary_path = output_dir / f"utest_summary_{timestamp}.md"
    json_path = output_dir / f"utest_summary_{timestamp}.json"
    log_path.write_text(full_log, encoding="utf-8")

    generated_at = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    summary_payload = init_summary(
        workflow="utest_only",
        status="completed" if started and ended else ("test_not_started" if not started else "test_timeout"),
        mode="uart_only",
        source={
            "kind": "device_uart_only",
        },
    )
    summary_payload.update({
        "generated_at": generated_at,
        "uart_port": selected_port,
        "started": started,
        "ended": ended,
        "chunk_count": chunk_count,
        "run_duration": f"{int(time.time() - run_started_at)}s",
        "raw_log_name": log_path.name,
        "latest_result": latest_result,
        "all_results": all_results,
        "error_count": len(error_lines),
        "error_lines": error_lines,
        "relevant_lines": relevant_lines,
    })
    update_metrics(
        summary_payload,
        chunk_count=chunk_count,
        total_results=len(all_results),
        error_count=len(error_lines),
        runtime_seconds=int(time.time() - run_started_at),
    )
    if not started:
        apply_runtime_payload(summary_payload, build_runtime_diagnostic_payload("start marker not found"))
    elif not ended:
        apply_runtime_payload(summary_payload, build_runtime_diagnostic_payload("timeout waiting for test end"))
    artifact_result = write_artifacts(
        log_path=log_path,
        json_path=json_path,
        report_path=summary_path,
        report_artifact_key="summary_path",
        raw_log=full_log,
        summary=summary_payload,
        report_extra={"report_text": _build_utest_summary_markdown(summary_payload)},
    )

    print("\n" + "=" * 60)
    print(f"[SUMMARY] started={started}, ended={ended}, chunks={chunk_count}")
    print(f"[SUMMARY] log saved: {artifact_result['log_path']}")
    print(f"[SUMMARY] markdown saved: {artifact_result['summary_path']}")
    print(f"[SUMMARY] json saved: {artifact_result['json_path']}")

    if not started:
        print("[FAIL] Start marker not found. Test may not have run.")
        print("[FAIL] Check if CSV file exists on device at /storage/sport/gomore/data_sample/")
        for line in relevant_lines[:20]:
            print(f"  RELEVANT: {line}")
        return 1

    if latest_result:
        print(f"[PASS] Final result: {latest_result['raw']}")
        print(f"  ltSpeed    = {latest_result['ltSpeed']}")
        print(f"  lh         = {latest_result['lh']}")
        print(f"  mh         = {latest_result['mh']}")
        print(f"  guideResult= {latest_result['guideResult']}")
    else:
        print("[WARN] No Gomore output result found in log")

    if all_results:
        print(f"\n[ALL RESULTS ({len(all_results)} found)]:")
        for i, item in enumerate(all_results):
            print(f"  #{i + 1}: {item['raw']}")

    for line in error_lines[:5]:
        print(f"  ERROR: {line}")
    if len(error_lines) > 5:
        print(f"  ... and {len(error_lines) - 5} more errors")
    print(f"[ERROR COUNT] {len(error_lines)} error lines found")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        runtime_hint = build_runtime_hint(str(exc))
        if runtime_hint:
            print(runtime_hint, file=sys.stderr, flush=True)
        raise

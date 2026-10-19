#!/usr/bin/env python3
import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from _summary_schema import init_summary, update_metrics

from shared.data_sample_ops import (
    clean_and_copy_single_sample,
    verify_single_sample_deployment,
)
from shared.deploy_utils import (
    LTHR_OUTPUT_ROOT,
    housekeep_output,
)
from shared.offline_sim_runner import (
    run_sample_flow,
)
from shared.jira_utils import (
    resolve_issue_sample_inputs,
)
from shared.jira_reporting import (
    publish_issue_artifacts,
    resolve_model_signature,
)
from shared.watchlink_runtime import (
    _safe_reconfigure_stdio,
    build_runtime_hint,
    build_wlctl_path,
    ensure_wsl_windows_bridge,
    format_timestamp,
    is_wsl_environment as _is_wsl_environment,
)
from shared.workflow_reporter import print_artifact_paths, write_artifacts

WSL_LEGACY_MOUNT_ROOT_RE = re.compile(r"^/mnt/[A-Za-z]/?$")


def normalize_disk_root_for_wsl(disk_root: str) -> Tuple[str, str]:
    """WSL 下忽略旧式 `/mnt/<盘符>` 根路径，让脚本回到稳定的 Windows data-root 流程。"""
    normalized = disk_root.strip()
    if not normalized or not _is_wsl_environment():
        return normalized, ""

    resolved = str(Path(normalized).expanduser())
    if WSL_LEGACY_MOUNT_ROOT_RE.match(resolved):
        return "", (
            f"[WARN] WSL legacy --disk-root={resolved} 会强制走旧的 FS 挂载路径；"
            "已自动忽略该参数并切回稳定的 Windows data-root 流程。\n"
        )
    return normalized, ""


# ── Jira helpers → shared/jira_utils.py ──
# ── Jira posting → shared/jira_utils.py ──

def main() -> None:
    _safe_reconfigure_stdio()
    housekeep_output()

    parser = argparse.ArgumentParser(description="Run LTHR offline CSV simulation on device and upload results to Jira")
    parser.add_argument("issue_key", help="Jira issue key, e.g. ITS-51497")
    parser.add_argument("--csv-name", default="", help="Explicit gomore_data_*.csv attachment name or attachment id")
    parser.add_argument("--all-csvs", action="store_true", help="Run the full flow for every gomore_data_*.csv attachment")
    parser.add_argument("--disk-root", default="", help="Mounted data disk root, sport dir, or gomore dir (WSL 推荐留空，走 Windows data-root 流程)")
    parser.add_argument("--work-dir", default="", help="Root work directory (default: /tmp/tmp_lthr_out/topic_ota)")
    parser.add_argument("--mgr-port", default="", help="Explicit MGR port when multiple devices are connected")
    parser.add_argument("--uart-port", default="", help="Explicit UART port when multiple devices are connected")
    parser.add_argument("--runs", type=int, default=1, help="Simulation run count (default: 1)")
    parser.add_argument(
        "--clean-between-runs",
        action="store_true",
        help="Clear GoMore history and reset the device before each run after the first",
    )
    parser.add_argument("--skip-jira", action="store_true", help="Skip all Jira communication (offline mode, results saved locally only)")
    parser.add_argument("--csv-path", default="", help="Local CSV file path (bypass Jira download, implies --skip-jira)")
    parser.add_argument("--boot-timeout-sec", type=int, default=180, help="Timeout waiting for ready screen logs")
    parser.add_argument("--mount-wait-sec", type=int, default=30, help="Timeout waiting for mounted data disk")
    parser.add_argument("--run-timeout-sec", type=int, default=2400, help="Per-run timeout waiting for test end")
    parser.add_argument("--mount-settle-sec", type=int, default=3, help="Sleep after entering disk mode")
    parser.add_argument("--serial-settle-sec", type=int, default=5, help="Sleep after leaving disk mode")
    parser.add_argument("--deploy-attempts", type=int, default=3, help="Retry count for mount/copy/unmount deployment before UART utest")
    parser.add_argument("--deploy-only", action="store_true", help="Only verify mount/cleanup/copy/unmount deployment, skip UART utest")
    parser.add_argument("--no-comment", action="store_true", help="Do not post Jira comment")
    parser.add_argument("--model-name", default="", help="Model name for report signature")
    parser.add_argument("--reasoning-effort", default="", help="Reasoning effort for report signature")
    args = parser.parse_args()

    if not args.deploy_only and args.runs <= 0:
        raise RuntimeError("--runs must be greater than 0")

    effective_disk_root, disk_root_notice = normalize_disk_root_for_wsl(args.disk_root)
    if disk_root_notice:
        print(disk_root_notice.rstrip())

    if _is_wsl_environment():
        try:
            ensure_wsl_windows_bridge(force=True)
        except Exception as exc:
            print(f"[FATAL] {exc}", file=sys.stderr, flush=True)
            raise

    wlctl_path = build_wlctl_path()
    if not wlctl_path.exists():
        raise RuntimeError(f"watchlink entrypoint not found: {wlctl_path}")

    temp_root = LTHR_OUTPUT_ROOT / "topic_ota"
    root_work_dir = Path(args.work_dir).expanduser() if args.work_dir else temp_root
    issue_work_dir = root_work_dir / args.issue_key / f"offline_sim_{format_timestamp()}"
    download_dir = issue_work_dir / "download"
    artifacts_dir = issue_work_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    download_dir.mkdir(parents=True, exist_ok=True)

    skip_jira = args.skip_jira or bool(args.csv_path)
    signature = resolve_model_signature(args.model_name, args.reasoning_effort)

    sample_summaries: List[Dict[str, object]] = []
    raw_sections: List[str] = []

    issue_meta, sample_csvs, zepp_cmd = resolve_issue_sample_inputs(
        issue_key=args.issue_key,
        download_dir=download_dir,
        csv_path=args.csv_path,
        csv_name=args.csv_name,
        all_csvs=args.all_csvs,
        log_fn=lambda line: print(line, flush=True),
    )

    for sample_csv in sample_csvs:
        raw_sections.append(f"========== SAMPLE {sample_csv.name} ==========\n")
        def _deploy_single(gomore_root: str) -> Dict[str, object]:
            cleanup = clean_and_copy_single_sample(gomore_root, sample_csv)
            return verify_single_sample_deployment(
                gomore_root,
                cleanup["copied_sample"][0],
                sample_csv.stat().st_size,
            )

        sample_summary, sample_log = run_sample_flow(
            wlctl_path=wlctl_path,
            sample_csv=sample_csv,
            disk_root=effective_disk_root,
            mgr_port=args.mgr_port,
            uart_port=args.uart_port,
            runs=args.runs,
            clean_between_runs=args.clean_between_runs,
            boot_timeout_sec=args.boot_timeout_sec,
            mount_wait_sec=args.mount_wait_sec,
            run_timeout_sec=args.run_timeout_sec,
            serial_settle_sec=args.serial_settle_sec,
            deploy_attempts=args.deploy_attempts,
            deploy_only=args.deploy_only,
            deploy_hook=_deploy_single,
        )
        sample_summaries.append(sample_summary)
        raw_sections.append(sample_log)

    stamp = format_timestamp()
    report_path = artifacts_dir / f"lthr_offline_sim_{args.issue_key}_{stamp}.md"
    json_path = artifacts_dir / f"lthr_offline_sim_{args.issue_key}_{stamp}.json"
    log_path = artifacts_dir / f"lthr_offline_sim_{args.issue_key}_{stamp}.log"

    has_trend_analysis = any(
        s.get("trend") and s["trend"].get("valid_runs", 0) >= 2
        for s in sample_summaries
    )
    successful_runs = sum(
        1
        for sample in sample_summaries
        for result in sample.get("results", [])
        if result.get("status") == "ok" and "ltSpeed" in result
    )
    total_runs = sum(len(sample.get("results", [])) for sample in sample_summaries)
    deploy_only_samples = sum(1 for sample in sample_summaries if sample.get("mode") == "deploy_only")

    json_payload = init_summary(
        workflow="single_sample",
        status="deploy_only_completed" if args.deploy_only else "completed",
        issue=issue_meta,
        mode="deploy_only" if args.deploy_only else "simulation",
        source={
            "kind": "local_csv" if args.csv_path else "jira_attachment",
            "skip_jira": skip_jira,
            "all_csvs": bool(args.all_csvs),
        },
    )
    json_payload["samples"] = sample_summaries
    json_payload["has_trend_analysis"] = has_trend_analysis
    update_metrics(
        json_payload,
        sample_count=len(sample_summaries),
        total_runs=total_runs,
        successful_runs=successful_runs,
        deploy_only_samples=deploy_only_samples,
        has_trend_analysis=has_trend_analysis,
    )
    artifact_result = write_artifacts(
        log_path=log_path,
        json_path=json_path,
        report_path=report_path,
        raw_log="".join(raw_sections),
        summary=json_payload,
        report_extra={
            "issue_meta": issue_meta,
            "sample_summaries": sample_summaries,
            "raw_log_name": log_path.name,
            "signature": signature,
        },
    )

    if skip_jira:
        print("[INFO] --skip-jira: Jira upload and comment skipped (results saved locally only)")
    else:
        publish_issue_artifacts(
            zepp_cmd=zepp_cmd,
            issue_key=args.issue_key,
            issue_meta=issue_meta,
            sample_summaries=sample_summaries,
            artifact_paths=[report_path, json_path, log_path],
            signature=signature,
            no_comment=args.no_comment,
        )

    if args.deploy_only:
        print("[INFO] --deploy-only: deployment health check completed, UART utest skipped")

    print(f"[OK] issue={args.issue_key}")
    print(f"[OK] sample_count={len(sample_summaries)}")
    print_artifact_paths(artifact_result)


def _should_print_wsl_shutdown_hint(exc: Exception) -> bool:
    if not _is_wsl_environment():
        return False
    text = str(exc).lower()
    keywords = (
        "wsl windows interop is unavailable",
        "cmd.exe timed out",
        "powershell.exe timed out",
        "backend: windows powershell bridge",
        "the port is closed",
    )
    return any(k in text for k in keywords)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        runtime_hint = build_runtime_hint(str(exc))
        if runtime_hint:
            print(runtime_hint, file=sys.stderr, flush=True)
        if _should_print_wsl_shutdown_hint(exc):
            print(
                "[HINT] 检测到 WSL->Windows bridge 异常。请在 Windows 终端执行 `wsl --shutdown`，"
                "重开 WSL 后先验证 `cmd.exe /c echo ok` 与 "
                "`powershell.exe -NoProfile -Command \"Write-Output ok\"` 可立即返回，再重试。",
                file=sys.stderr,
                flush=True,
            )
        raise

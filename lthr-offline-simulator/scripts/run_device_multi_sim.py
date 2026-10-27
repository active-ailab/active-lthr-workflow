#!/usr/bin/env python3
"""Deploy an explicit GoMore sample config, run the C UTest once, and extract artifacts."""

import argparse
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

from _summary_schema import (
    set_status,
    update_metrics,
)
from shared.data_sample_ops import deploy_data_sample_tree, stage_data_sample_inputs
from shared.deploy_utils import (
    LTHR_OUTPUT_ROOT,
    housekeep_output,
)
from shared.offline_sim_contract import collect_sample_paths, make_run_id
from shared.offline_sim_runner import run_sample_flow
from shared.multi_sim_support import (
    init_img_only_summary,
    init_multi_sample_summary,
    print_data_sample_inventory,
    run_img_extract_phase,
)
from shared.watchlink_runtime import (
    WatchlinkDevice,
    _safe_reconfigure_stdio,
    build_runtime_hint,
    build_wlctl_path,
    ensure_wsl_windows_bridge,
    format_timestamp,
    is_wsl_environment as _is_wsl_environment,
    remove_from_mounted_root,
)
from shared.workflow_reporter import (
    print_artifact_paths,
    write_artifacts,
    write_json_summary,
)


# ── 本地 data_sample 来源 ─────────────────────────────────
def _resolve_data_sample(args) -> Path:
    if not args.data_dir:
        raise RuntimeError("仿真流程必须显式传入本地 --data-dir data_sample 目录")
    path = Path(args.data_dir).expanduser().resolve()
    if not path.is_dir():
        raise RuntimeError(f"data_sample 目录不存在: {path}")
    print(f"[信息] 使用本地 data_sample: {path}")
    return path

# ── IMG 日志清理（Phase 0）─────────────────────────────────

def _clean_img_logs(wlctl_path: Path, mgr_port: str) -> None:
    """挂载 IMG 分区，删除所有 *.log 文件，然后卸载。

    在仿真启动前执行，确保 IMG 盘不残留旧日志。
    """
    print(f"\n{'='*60}")
    print(f"Phase 0: 清理 IMG 盘旧日志")
    print(f"{'='*60}")
    dev = None
    try:
        dev = WatchlinkDevice(mgr_port=mgr_port, wlctl_path=wlctl_path)
        # mount-log 实际映射到 IMG 分区
        mount_out = dev.mount_log(timeout=30)
        log_root_text = mount_out.strip().rstrip("\\").rstrip("/")
        print(f"  [信息] IMG 分区挂载路径: {log_root_text}")

        # 删除所有 .log 文件（递归）
        deleted = remove_from_mounted_root(Path(log_root_text), "**/*.log", recursive=True, missing_ok=True)
        print(f"  [完成] 已删除 {len(deleted)} 个旧日志文件")

        dev.unmount()
        print(f"  [完成] IMG 分区已卸载")
    except Exception as e:
        print(f"  [警告] 清理 IMG 日志失败: {e}")
        if dev:
            try:
                dev.unmount()
            except Exception:
                pass


def main(argv: Optional[List[str]] = None) -> int:
    _safe_reconfigure_stdio()
    housekeep_output()

    parser = argparse.ArgumentParser(
        description="Deploy data_sample, run one configured GoMore UTest, and extract its artifacts"
    )
    parser.add_argument("--disk-root", default="", help="Mounted data disk root")
    parser.add_argument("--data-dir", default="", help="Local data_sample directory")
    parser.add_argument("--output-dir", default="", help="Output directory")
    parser.add_argument("--mgr-port", default="", help="Explicit MGR port")
    parser.add_argument("--uart-port", default="", help="Explicit UART port")
    parser.add_argument("--mode", choices=(
        "single_sample",
        "single_person_multi_sample",
        "multi_person_multi_sample",
    ))
    parser.add_argument("--rounds", type=int)
    parser.add_argument("--boot-timeout-sec", type=int, default=180)
    parser.add_argument("--mount-wait-sec", type=int, default=30)
    parser.add_argument("--run-timeout-sec", type=int, default=72000)
    parser.add_argument("--listen-chunk-sec", type=int, default=10)
    parser.add_argument("--serial-settle-sec", type=int, default=5)
    parser.add_argument("--pre-run-delay-sec", type=int, default=30)

    parser.add_argument("--with-img-extract", action="store_true")
    parser.add_argument("--img-only", action="store_true")
    parser.add_argument("--keep-device-logs", action="store_true")
    parser.add_argument("--img-mount-wait-sec", type=int, default=30)
    parser.add_argument("--img-extract-delay-sec", type=int, default=30)
    args = parser.parse_args(argv)

    if _is_wsl_environment():
        ensure_wsl_windows_bridge(force=True)

    wlctl_path = build_wlctl_path()
    if not wlctl_path.exists():
        raise RuntimeError(f"watchlink entrypoint not found: {wlctl_path}")

    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else LTHR_OUTPUT_ROOT / "lthr_multi_sim"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = format_timestamp()

    if args.img_only:
        summary_info = init_img_only_summary(stamp)
        run_img_extract_phase(
            wlctl_path=wlctl_path,
            mgr_port=args.mgr_port,
            output_dir=output_dir,
            mount_wait_sec=args.img_mount_wait_sec,
            keep_device_logs=args.keep_device_logs,
            summary_info=summary_info,
        )
        json_path = output_dir / f"device_multi_sim_{stamp}.json"
        result = write_json_summary(json_path=json_path, summary=summary_info)
        print(f"[完成] JSON 摘要: {result['json_path']}")
        return 0

    if not args.mode:
        parser.error("运行仿真时必须显式指定 --mode")
    if args.rounds is None or args.rounds <= 0:
        parser.error("运行仿真时必须显式指定正整数 --rounds")

    local_data_sample = _resolve_data_sample(args)
    sample_files = collect_sample_paths(local_data_sample)
    if not sample_files:
        raise RuntimeError(f"在 {local_data_sample} 中未找到 gomore_data_*.csv")
    print_data_sample_inventory(local_data_sample, sample_files)

    run_id = make_run_id()
    temporary_storage = tempfile.TemporaryDirectory(prefix="gomore_data_sample_")
    staged_data_sample = Path(temporary_storage.name) / "data_sample"
    sample_paths = stage_data_sample_inputs(
        local_data_sample,
        staged_data_sample,
        sample_files,
    )
    run_output_dir = output_dir if args.output_dir else output_dir / run_id
    run_output_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_output_dir / "device_multi_sim.log"
    json_path = run_output_dir / "device_multi_sim.json"

    summary_info = init_multi_sample_summary(stamp, local_data_sample, len(sample_paths))
    summary_info.update({
        "run_id": run_id,
        "mode": args.mode,
        "rounds": args.rounds,
        "sample_count": len(sample_paths),
        "status": "deploying",
    })
    def _on_state(state: Dict[str, object], full_log: str) -> None:
        summary_info["utest_state"] = dict(state)
        set_status(summary_info, "utest_" + str(state.get("status", "running")))
        write_artifacts(
            log_path=log_path,
            json_path=json_path,
            raw_log=full_log,
            summary=summary_info,
        )

    def _deploy(root: str) -> Dict[str, object]:
        info = deploy_data_sample_tree(root, staged_data_sample)
        print(
            "[INFO] Deployed {} configured sample(s) and sim_config.ini to {}".format(
                len(sample_paths), info.get("data_sample", "")
            )
        )
        return info

    if args.with_img_extract:
        _clean_img_logs(wlctl_path=wlctl_path, mgr_port=args.mgr_port)

    flow_summary, full_log = run_sample_flow(
        wlctl_path=wlctl_path,
        data_sample_dir=staged_data_sample,
        sample_paths=sample_paths,
        mode=args.mode,
        rounds=args.rounds,
        run_id=run_id,
        output_dir=run_output_dir,
        disk_root=args.disk_root,
        mgr_port=args.mgr_port,
        uart_port=args.uart_port,
        boot_timeout_sec=args.boot_timeout_sec,
        mount_wait_sec=args.mount_wait_sec,
        run_timeout_sec=args.run_timeout_sec,
        listen_chunk_sec=args.listen_chunk_sec,
        pre_run_delay_sec=args.pre_run_delay_sec,
        serial_settle_sec=args.serial_settle_sec,
        deploy_attempts=1,
        deploy_only=False,
        deploy_hook=_deploy,
        state_callback=_on_state,
    )
    temporary_storage.cleanup()
    summary_info.update({
        "status": flow_summary.get("status", "failed"),
        "simulation": flow_summary,
        "samples": flow_summary.get("samples", []),
        "result_path": flow_summary.get("result_path", ""),
        "history_paths": flow_summary.get("history_paths", []),
        "error": flow_summary.get("error", ""),
        "runtime_seconds": flow_summary.get("runtime_seconds", 0),
        "log_path": str(log_path),
        "log_size_bytes": len(full_log),
    })
    samples = flow_summary.get("samples", [])
    succeeded = sum(
        1 for sample in samples
        if isinstance(sample, dict) and sample.get("device_status") == "success"
    )
    failed = len(samples) - succeeded
    summary_info["total_samples"] = len(sample_paths)
    summary_info["passed"] = succeeded
    summary_info["failed"] = failed
    update_metrics(
        summary_info,
        input_csv_count=len(sample_paths),
        total_samples=len(sample_paths),
        passed=succeeded,
        failed=failed,
        rounds=args.rounds,
        total_rounds=len(sample_paths) * args.rounds,
        runtime_seconds=flow_summary.get("runtime_seconds", 0),
        log_size_bytes=len(full_log),
    )

    if args.with_img_extract and flow_summary.get("ended"):
        if args.img_extract_delay_sec > 0:
            time.sleep(args.img_extract_delay_sec)
        run_img_extract_phase(
            wlctl_path=wlctl_path,
            mgr_port=args.mgr_port,
            output_dir=run_output_dir,
            mount_wait_sec=args.img_mount_wait_sec,
            keep_device_logs=args.keep_device_logs,
            summary_info=summary_info,
        )

    artifact_result = write_artifacts(
        log_path=log_path,
        json_path=json_path,
        raw_log=full_log,
        summary=summary_info,
    )
    print_artifact_paths(artifact_result)
    print(
        "[SUMMARY] run_id={} mode={} rounds={} samples={} status={}".format(
            run_id, args.mode, args.rounds, len(sample_paths), summary_info["status"]
        )
    )
    if summary_info["status"] != "success":
        print(
            "[ERROR] Configured simulation finished with status {}: {}".format(
                summary_info["status"], summary_info.get("error", "")
            ),
            file=sys.stderr,
        )
        return 1
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        runtime_hint = build_runtime_hint(str(exc))
        if runtime_hint:
            print(runtime_hint, file=sys.stderr, flush=True)
        raise

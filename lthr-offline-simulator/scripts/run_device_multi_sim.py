#!/usr/bin/env python3
"""
run_device_multi_sim.py - 在设备上批量运行 data_sample 多样本仿真并保存完整日志

流程:
  Phase 1. 通过 SDK 挂载 data 盘 (v3dl 风格: WL+VBUS=TEMP WL+DISK=FS)
  Phase 2. 将 PC 端 data_sample/ 完整目录递归拷贝到设备
  Phase 3. 卸载 data 盘 (WL+DISK=NULL), 等待设备重启
  Phase 4. 发送 utest gomore_offline_csv_multi_tests, 实时 UART 监听
  Phase 5. [可选 --with-img-extract] 挂载 IMG 日志分区提取完整日志, 卸载

推荐使用 --with-img-extract 获取完整设备日志替代不完整的 UART 串口日志。

Windows 兼容: .ps1 自动 powershell 包装, D:-Z: 盘符扫描。
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from _summary_schema import (
    set_status,
    update_metrics,
)
from shared.data_sample_ops import deploy_data_sample_tree
from shared.device_flow import (
    deploy_data_to_device,
    ensure_device_ready_sequence,
)
from shared.deploy_utils import (
    LTHR_OUTPUT_ROOT,
    housekeep_output,
)
from shared.multi_sim_runner import (
    START_MARKER,
    END_MARKER,
    run_multi_sample_simulation,
)
from shared.multi_sim_support import (
    collect_data_sample_csvs,
    init_img_only_summary,
    init_multi_sample_summary,
    print_progress,
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
    inject_runtime_hint,
    print_artifact_paths,
    write_artifacts,
    write_json_summary,
)


# ── data_sample 来源 ─────────────────────────────────────
# 优先从飞书云盘拉取，失败后让用户指定 --data-dir
DATA_SAMPLE_FOLDER_TOKEN = "HYS7fstGll8lGedAVKFc6RNxnAb"


def _resolve_data_sample(args) -> Tuple[Path, bool]:
    """解析 data_sample 目录。返回 (path, from_cloud) 元组。

    策略：
      1. 如果传了 --data-dir，直接使用
      2. 否则尝试从飞书云盘 DATA_SAMPLE_FOLDER_TOKEN 拉取
      3. 拉取失败时报错，要求用户指定 --data-dir
    """
    if args.data_dir:
        path = Path(args.data_dir).expanduser().resolve()
        if not path.is_dir():
            raise RuntimeError(f"data_sample 目录不存在: {path}")
        print(f"[信息] 使用本地 data_sample: {path}")
        return path, False

    # 尝试从飞书云盘拉取
    print(f"[信息] 未指定 --data-dir，尝试从飞书云盘拉取 data_sample...")
    dl_dir = Path("/tmp/lthr_data_sample_cloud").resolve()
    dl_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 先 ls 看看文件夹里有什么
        ls_result = subprocess.run(
            ["lark-cli", "drive", "files", "list",
             "--params", f'{{"folder_token":"{DATA_SAMPLE_FOLDER_TOKEN}","page_size":50}}',
             "--format", "json", "--as", "user"],
            capture_output=True, text=True, timeout=30,
        )
        if ls_result.returncode != 0:
            raise RuntimeError(f"lark-cli 无法访问云盘文件夹: {ls_result.stderr[:200]}")
    except Exception as e:
        print(f"  [错误] 无法从飞书云盘获取 data_sample: {e}")
        print(f"  请通过 --data-dir 指定本地 data_sample 目录")
        raise RuntimeError(
            f"未指定 --data-dir 且无法从飞书云盘拉取 data_sample。\n"
            f"请运行: python3 {Path(__file__).name} --data-dir /path/to/data_sample ..."
        )

    # 使用 +pull 下载
    try:
        cwd = str(dl_dir)
        pull_result = subprocess.run(
            ["lark-cli", "drive", "+pull",
             "--folder-token", DATA_SAMPLE_FOLDER_TOKEN,
             "--local-dir", "./",
             "--if-exists", "overwrite",
             "--as", "user"],
            capture_output=True, text=True, timeout=120, cwd=cwd,
        )
        if pull_result.returncode != 0:
            raise RuntimeError(f"lark-cli +pull 失败: {pull_result.stderr[:200]}")
        print(f"  [完成] 已从飞书云盘下载 data_sample 到 {dl_dir}")
    except Exception as e:
        print(f"  [错误] 下载 data_sample 失败: {e}")
        print(f"  请通过 --data-dir 指定本地 data_sample 目录")
        raise RuntimeError(
            f"未指定 --data-dir 且无法从飞书云盘下载 data_sample。\n"
            f"请运行: python3 {Path(__file__).name} --data-dir /path/to/data_sample ..."
        )

    if not dl_dir.is_dir():
        raise RuntimeError(f"下载后的 data_sample 目录不存在: {dl_dir}")
    return dl_dir, True

# ── Runtime diagnostics → shared/workflow_reporter.py ──

# ── Artifact writing → shared/workflow_reporter.py ──


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


def main() -> None:
    _safe_reconfigure_stdio()
    housekeep_output()

    parser = argparse.ArgumentParser(
        description="在设备上批量运行 data_sample 所有 CSV 的多样本仿真并保存 UART 日志 + IMG 日志提取"
    )
    parser.add_argument("--disk-root", default="",
                        help="Mounted data disk root (如 /mnt/f)")
    parser.add_argument("--data-dir", default="",
                        help="PC 端 data_sample 目录 (不指定时尝试从飞书云盘拉取，失败后报错)")
    parser.add_argument("--output-dir", default="",
                        help="日志输出目录 (默认: /tmp/tmp_lthr_out/lthr_multi_sim)")
    parser.add_argument("--mgr-port", default="",
                        help="Explicit MGR port when multiple devices")
    parser.add_argument("--uart-port", default="",
                        help="Explicit UART port when multiple devices")
    parser.add_argument("--boot-timeout-sec", type=int, default=180,
                        help="等待设备启动超时 (秒)")
    parser.add_argument("--mount-wait-sec", type=int, default=30,
                        help="等待 data 盘挂载超时 (秒)")
    parser.add_argument("--run-timeout-sec", type=int, default=72000,
                        help="multi_tests 执行总超时 (默认 20 小时)")
    parser.add_argument("--listen-chunk-sec", type=int, default=10,
                        help="每次 UART 监听窗口 (秒)")
    parser.add_argument("--mount-settle-sec", type=int, default=3,
                        help="挂载后等待时间 (秒)")
    parser.add_argument("--serial-settle-sec", type=int, default=5,
                        help="退出磁盘模式后等待时间 (秒)")
    parser.add_argument("--pre-run-delay-sec", type=int, default=30,
                        help="设备就绪后等待 N 秒再发测试命令, 避免日志溢出 (默认 30)")

    # IMG 日志提取参数
    parser.add_argument("--with-img-extract", action="store_true",
                        help="仿真完成后自动挂载 IMG 分区提取完整日志（推荐使用，替代不完整的 UART 日志）")
    parser.add_argument("--img-only", action="store_true",
                        help="仅执行 IMG 日志提取, 跳过仿真流程 (需设备上已有仿真结果)")
    parser.add_argument("--keep-device-logs", action="store_true",
                        help="提取后不删除设备 IMG 分区上的日志文件")
    parser.add_argument("--img-mount-wait-sec", type=int, default=30,
                        help="等待 IMG 分区挂载超时 (秒)")
    parser.add_argument("--img-extract-delay-sec", type=int, default=30,
                        help="仿真完成后等待 N 秒再提取 IMG 日志, 确保日志完全刷写 (默认 30)")
    args = parser.parse_args()

    if _is_wsl_environment():
        try:
            ensure_wsl_windows_bridge(force=True)
        except Exception as exc:
            print(f"[FATAL] {exc}", file=sys.stderr, flush=True)
            raise

    # ── 前置检查 ──
    wlctl_path = build_wlctl_path()
    if not wlctl_path.exists():
        raise RuntimeError(f"watchlink entrypoint not found: {wlctl_path}")

    # ── 输出目录 ──
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser().resolve()
    else:
        output_dir = LTHR_OUTPUT_ROOT / "lthr_multi_sim"
    output_dir.mkdir(parents=True, exist_ok=True)

    stamp = format_timestamp()

    # ════════════════════════════════════════════
    # IMG-Only Mode: 跳过仿真, 仅提取 IMG 日志
    # ════════════════════════════════════════════
    if args.img_only:
        print(f"[信息] --img-only 模式: 仅提取 IMG 日志")
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
        return

    local_data_sample, from_cloud = _resolve_data_sample(args)
    csv_files = collect_data_sample_csvs(local_data_sample)
    print_data_sample_inventory(local_data_sample, csv_files)

    log_path = output_dir / f"device_multi_sim_{stamp}.log"
    json_path = output_dir / f"device_multi_sim_{stamp}.json"

    raw_sections: List[str] = []
    summary_info: dict = init_multi_sample_summary(stamp, local_data_sample, len(csv_files))

    def _fail_phase(status: str, error: Exception, label: str) -> None:
        set_status(summary_info, f"{status}: {error}")
        raw_sections.append(f"[FATAL] {error}\n")
        inject_runtime_hint(summary_info, str(error))
        if summary_info.get("runtime_hint"):
            raw_sections.append(f"{summary_info['runtime_hint']}\n")
        write_artifacts(
            log_path=log_path,
            json_path=json_path,
            raw_log="".join(raw_sections),
            summary=summary_info,
        )
        print(f"[错误] {label} 失败: {error}", file=sys.stderr)
        sys.exit(1)

    # ════════════════════════════════════════════
    # Phase 0: 清理 IMG 盘旧日志（确保不混入旧数据）
    # ════════════════════════════════════════════
    _clean_img_logs(wlctl_path=wlctl_path, mgr_port=args.mgr_port)

    # ════════════════════════════════════════════
    # Phase 1: 挂载 data 盘 → 清理旧 data_sample → 拷贝新 data_sample → 卸载
    # ════════════════════════════════════════════
    print(f"\n{'='*60}")
    print(f"Phase 1: 挂载 data 盘并部署 data_sample 到设备")
    print(f"{'='*60}")
    print(f"  步骤: 挂载 → 清理旧 data_sample → 拷贝新 CSV → 验证 → 卸载")

    def _deploy_data_sample(gomore_root: str) -> Dict[str, object]:
        deployment = deploy_data_sample_tree(gomore_root, local_data_sample)
        for rel_path in deployment.get("copied_csv_relative_paths", []):
            print(f"  [复制] {rel_path}")
        copied_count = len(deployment.get("copied_csv_relative_paths", []))
        print(f"  [完成] 共拷贝 {copied_count} 个 CSV 文件到设备 {deployment.get('data_sample')}")
        if copied_count == 0:
            raise RuntimeError(f"在 {local_data_sample} 中未找到任何 CSV 文件")
        return deployment

    try:
        gomore_root_text, deploy_log = deploy_data_to_device(
            wlctl_path=wlctl_path,
            mgr_port=args.mgr_port,
            disk_root=args.disk_root,
            mount_wait_sec=args.mount_wait_sec,
            deploy_attempts=1,  # multi-sample deploy is heavy; single attempt
            deploy_hook=_deploy_data_sample,
            deploy_hook_wsl=_deploy_data_sample,
        )
        raw_sections.append(deploy_log)
        summary_info["gomore_root"] = gomore_root_text
        print(f"[信息] 设备 gomore 根目录: {gomore_root_text}")
        print(f"[信息] 已退出磁盘模式")

    except Exception as e:
        _fail_phase("disk_phase_failed", e, "Phase 1")

    # ════════════════════════════════════════════
    # Phase 2: 等待设备启动
    # ════════════════════════════════════════════
    print(f"\n{'='*60}")
    print(f"Phase 2: 等待设备启动")
    print(f"{'='*60}")

    try:
        ensure_device_ready_sequence(
            wlctl_path=wlctl_path,
            mgr_port=args.mgr_port,
            uart_port=args.uart_port,
            boot_timeout_sec=args.boot_timeout_sec,
            serial_settle_sec=args.serial_settle_sec,
        )
        print(f"[信息] 设备已就绪")
        print("[信息] 已完成 UART 常亮与 shell 稳定探测")
        print(f"[信息] 等待 {args.pre_run_delay_sec} 秒后发送测试命令（避免日志溢出）...")
        time.sleep(args.pre_run_delay_sec)
    except Exception as e:
        _fail_phase("boot_phase_failed", e, "Phase 2")

    # ════════════════════════════════════════════
    # Phase 3: 执行 multi_tests 仿真
    # ════════════════════════════════════════════
    print(f"\n{'='*60}")
    print(f"Phase 3: 执行 utest gomore_offline_csv_multi_tests")
    print(f"{'='*60}")
    print(f"  开始时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}")
    print(f"  超时设定: {args.run_timeout_sec} 秒 ({args.run_timeout_sec // 3600} 小时)")
    print(f"  日志保存: {log_path}")
    print()

    run_start = time.time()
    try:
        phase_summary, run_log = run_multi_sample_simulation(
            wlctl_path=wlctl_path,
            uart_port=args.uart_port,
            run_timeout_sec=args.run_timeout_sec,
            listen_chunk_sec=args.listen_chunk_sec,
            progress_callback=print_progress,
        )
        raw_sections.append(run_log)
        total_runtime = time.time() - run_start
        full_log = run_log
        started = bool(phase_summary.get("started"))
        ended = bool(phase_summary.get("ended"))

        if not started:
            set_status(summary_info, "test_not_started")
            inject_runtime_hint(summary_info, "start marker not found")
            if summary_info.get("runtime_hint"):
                raw_sections.append(f"{summary_info['runtime_hint']}\n")
            print(f"\n[错误] 未检测到 '{START_MARKER}'", file=sys.stderr)
        elif not ended:
            set_status(summary_info, "test_timeout")
            inject_runtime_hint(summary_info, "timeout waiting for test end")
            if summary_info.get("runtime_hint"):
                raw_sections.append(f"{summary_info['runtime_hint']}\n")
            print(f"\n[错误] 超时未检测到 '{END_MARKER}'", file=sys.stderr)
            tail = full_log[-4000:]
            print(f"  尾部日志: {tail[-500:]}", file=sys.stderr)
        else:
            set_status(summary_info, "completed")
            print(f"\n[信息] multi_tests 执行完成")

        summary_info["total_samples"] = int(phase_summary.get("total_samples", 0))
        summary_info["passed"] = int(phase_summary.get("passed", 0))
        summary_info["failed"] = int(phase_summary.get("failed", 0))
        summary_info["runtime_seconds"] = round(total_runtime)
        summary_info["log_path"] = str(log_path)
        summary_info["log_size_bytes"] = int(phase_summary.get("log_size_bytes", len(full_log)))
        update_metrics(
            summary_info,
            total_samples=summary_info["total_samples"],
            passed=summary_info["passed"],
            failed=summary_info["failed"],
            runtime_seconds=summary_info["runtime_seconds"],
            log_size_bytes=summary_info["log_size_bytes"],
        )

        print(f"\n{'='*60}")
        print(f"[摘要] 状态: {summary_info['status']}")
        print(f"[摘要] 样本总数: {summary_info['total_samples']}")
        print(f"[摘要] PASS: {summary_info['passed']}")
        print(f"[摘要] FAIL: {summary_info['failed']}")
        print(f"[摘要] 运行时长: {total_runtime:.0f} 秒 ({total_runtime / 3600:.1f} 小时)")
        print(f"[摘要] 日志大小: {len(full_log)} 字节")
        print(f"[摘要] 日志文件: {log_path}")
        print(f"{'='*60}")

    except Exception as e:
        set_status(summary_info, f"run_phase_failed: {e}")
        raw_sections.append(f"[FATAL] {e}\n")
        inject_runtime_hint(summary_info, str(e))
        runtime_hint = build_runtime_hint(str(e))
        if runtime_hint:
            raw_sections.append(f"{runtime_hint}\n")
            print(runtime_hint, file=sys.stderr, flush=True)

    # ════════════════════════════════════════════
    # Phase 5 (可选): IMG 日志提取 (推荐: 获取完整设备日志)
    # ════════════════════════════════════════════
    if args.with_img_extract and "completed" in summary_info.get("status", ""):
        delay = args.img_extract_delay_sec
        print(f"\n[信息] 等待 {delay} 秒让日志刷写到 IMG 分区...")
        time.sleep(delay)
        run_img_extract_phase(
            wlctl_path=wlctl_path,
            mgr_port=args.mgr_port,
            output_dir=output_dir,
            mount_wait_sec=args.img_mount_wait_sec,
            keep_device_logs=args.keep_device_logs,
            summary_info=summary_info,
        )

    # ════════════════════════════════════════════
    # 保存输出
    # ════════════════════════════════════════════
    result = write_artifacts(log_path=log_path, json_path=json_path, raw_log="".join(raw_sections), summary=summary_info)
    print_artifact_paths(result)
    print(f"[完成] JSON 摘要: {json_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        runtime_hint = build_runtime_hint(str(exc))
        if runtime_hint:
            print(runtime_hint, file=sys.stderr, flush=True)
        raise

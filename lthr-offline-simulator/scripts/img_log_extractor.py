#!/usr/bin/env python3
"""
img_log_extractor.py — 从设备 IMG 日志分区提取完整仿真日志

通过 WatchlinkDevice SDK 挂载 IMG 盘 (v3dl 风格单命令 WL+VBUS=TEMP WL+DISK=IMG)、
提取 *.log、解析、unmount 全流程。可作为独立 CLI 脚本使用，也可被导入调用。
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from _output_templates import format_optional_line, render_asset_template
from _summary_schema import update_artifacts
from shared.deploy_utils import (
    detect_mount_roots,
    housekeep_output,
)
from shared.img_extract_support import (
    finalize_img_extract_summary,
    init_img_extract_summary,
    resolve_default_output_dir,
)
from shared.watchlink_runtime import (
    WatchlinkDevice,
    _safe_reconfigure_stdio,
    build_runtime_hint,
    build_wlctl_path,
    ensure_local_mount_root,
    format_timestamp,
    pull_mounted_paths,
    remove_from_mounted_root,
)
from shared.workflow_reporter import write_json_summary
from disk_utils import select_log_root

# ── 日志标记常量 ──
START_MARKER = "GoMore Offline CSV Test Start"
END_MARKER = "GoMore Offline CSV Test End"
RESULT_RE = re.compile(
    r"Gomore output ltSpeed=(?P<ltSpeed>[-+]?\d+(?:\.\d+)?) "
    r"lh=(?P<lh>-?\d+) mh=(?P<mh>-?\d+) guideResult=(?P<guideResult>-?\d+)"
)
SAMPLE_RESULT_RE = re.compile(r"Sample\[(\d+)/(\d+)\]\s+(PASS|FAIL)")
SUMMARY_RE = re.compile(r"All sample cases completed:\s*total=(\d+),\s*failed=(\d+)")
COLLECTED_RE = re.compile(r"Collected sample summary:\s*total=(\d+)\s+root=(\d+)\s+base=(\d+)\s+jira=(\d+)")

# ════════════════════════════════════════════
# IMG 日志解析
# ════════════════════════════════════════════

def parse_img_logs(log_paths: List[Path]) -> List[Dict]:
    """
    解析 IMG 日志, 提取所有 GoMore 离线仿真测试批次结果.

    返回 list[dict], 每个 dict 包含:
      - batch_index: 批次序号
      - total: 样本总数
      - failed: 失败数
      - collected: dict(total/root/base/jira) 或 None
      - samples: list[dict(sample_idx, status, alg_outputs)]
      - raw_start_line: 起始行号
      - raw_end_line: 结束行号
    """
    batches: List[Dict] = []
    current_batch: Optional[Dict] = None
    current_sample: Optional[Dict] = None

    for log_path in log_paths:
        print(f"[解析] 读取 {log_path.name}...")
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()

        for line_no, line in enumerate(lines, 1):
            # Test Start → 新批次开始
            if START_MARKER in line:
                if current_batch is not None and current_sample is not None:
                    current_batch.setdefault("samples", []).append(current_sample)
                    current_sample = None
                if current_batch is not None:
                    batches.append(current_batch)
                current_batch = {
                    "batch_index": len(batches) + 1,
                    "start_line": line_no,
                    "total": 0,
                    "failed": 0,
                    "passed": 0,
                    "collected": None,
                    "samples": [],
                    "log_file": log_path.name,
                }
                current_sample = None
                continue

            if current_batch is None:
                continue

            # Collected sample summary
            m = COLLECTED_RE.search(line)
            if m:
                current_batch["collected"] = {
                    "total": int(m.group(1)),
                    "root": int(m.group(2)),
                    "base": int(m.group(3)),
                    "jira": int(m.group(4)),
                }
                continue

            # 算法输出
            m = RESULT_RE.search(line)
            if m:
                current_batch.setdefault("_alg_outputs", []).append({
                    "ltSpeed": float(m.group("ltSpeed")),
                    "lh": int(m.group("lh")),
                    "mh": int(m.group("mh")),
                    "guideResult": int(m.group("guideResult")),
                })
                continue

            # Sample PASS/FAIL
            m = SAMPLE_RESULT_RE.search(line)
            if m:
                samp_idx = int(m.group(1))
                samp_total = int(m.group(2))
                samp_status = m.group(3)

                current_batch["total"] = max(current_batch["total"], samp_total)
                alg_outs = current_batch.pop("_alg_outputs", [])
                if current_sample is not None:
                    current_batch.setdefault("samples", []).append(current_sample)

                current_sample = {
                    "sample_idx": samp_idx,
                    "total": samp_total,
                    "status": samp_status,
                    "alg_outputs": alg_outs,
                }
                continue

            # All sample cases completed
            m = SUMMARY_RE.search(line)
            if m:
                current_batch["failed"] = int(m.group(2))
                current_batch["passed"] = int(m.group(1)) - int(m.group(2))

                # 完成当前样本和批次
                if current_sample is not None:
                    current_batch.setdefault("samples", []).append(current_sample)
                    current_sample = None
                current_batch["_alg_outputs"] = []
                continue

            # Test End → 关闭当前批次
            if END_MARKER in line:
                current_batch.setdefault("samples", [])
                current_batch.setdefault("_alg_outputs", [])
                if current_batch["samples"]:
                    batches.append(current_batch)
                else:
                    # 空的批次(无样本), 丢弃
                    pass
                current_batch = None
                current_sample = None
                continue

        # 文件结束, 如果有未关闭的批次
        if current_batch is not None:
            # 丢弃不完整的批次
            current_batch = None
            current_sample = None

    print(f"[解析] 共检测到 {len(batches)} 个测试批次")
    for batch in batches:
        n = len(batch.get("samples", []))
        c = batch.get("collected") or {}
        print(f"  批次{batch['batch_index']}: {n} 样本 ({n}/{batch['total']}), "
              f"PASS={n - batch['failed']}, FAIL={batch['failed']} "
              f"(base={c.get('base', '?')} jira={c.get('jira', '?')})")

    return batches


def print_batch_summary(batches: List[Dict]) -> str:
    """生成批次的文本摘要"""
    lines = []
    lines.append("=" * 80)
    lines.append("GoMore 离线 CSV 仿真 — IMG 日志完整结果")
    lines.append("=" * 80)

    for batch in batches:
        samples = batch.get("samples", [])
        collected = batch.get("collected") or {}
        lines.append(f"\n批次 #{batch['batch_index']} ({batch['log_file']})")
        if collected:
            lines.append(f"  汇总: total={collected.get('total','?')} "
                         f"root={collected.get('root','?')} "
                         f"base={collected.get('base','?')} "
                         f"jira={collected.get('jira','?')}")
        lines.append(f"  结果: {batch['passed']}/{batch['total']} PASS, "
                     f"{batch['failed']} FAILED")

        for s in samples:
            outs = s.get("alg_outputs", [])
            final = outs[-1] if outs else None
            if final and final["ltSpeed"] >= 0:
                lines.append(
                    f"    Sample[{s['sample_idx']}/{s['total']}] {s['status']}: "
                    f"ltSpeed={final['ltSpeed']:.3f} lh={final['lh']} mh={final['mh']}"
                )
            elif final:
                lines.append(
                    f"    Sample[{s['sample_idx']}/{s['total']}] {s['status']}: "
                    f"ltSpeed=INVALID lh={final['lh']} mh={final['mh']} "
                    f"(全部输出: {'; '.join(str(o['ltSpeed']) for o in outs)})"
                )
            else:
                lines.append(
                    f"    Sample[{s['sample_idx']}/{s['total']}] {s['status']}: "
                    f"(无算法输出)"
                )

    lines.append("\n" + "=" * 80)
    return "\n".join(lines)


def build_img_report_markdown(
    batches: List[Dict],
    source_mode: str,
    source_detail: str = "",
    mount_root: str = "",
    log_file_count: int = 0,
    log_deleted_count: int = 0,
) -> str:
    """生成统一的 IMG 提取 Markdown 报告。"""
    batch_sections: List[str] = []

    if batches:
        for batch in batches:
            batch_sections.append(f"## Batch {batch.get('batch_index', '?')} ({batch.get('log_file', '?')})")
            collected = batch.get("collected") or {}
            if collected:
                batch_sections.append(
                    f"- Collected: total={collected.get('total', '?')} root={collected.get('root', '?')} "
                    f"base={collected.get('base', '?')} jira={collected.get('jira', '?')}"
                )
            batch_sections.append(
                f"- Result: PASS={batch.get('passed', 0)} FAIL={batch.get('failed', 0)} / total={batch.get('total', 0)}"
            )
            samples = batch.get("samples") or []
            if samples:
                batch_sections.append("")
                batch_sections.append("| Sample | Status | ltSpeed | lh | mh | guideResult |")
                batch_sections.append("| --- | --- | --- | --- | --- | --- |")
                for sample in samples:
                    outputs = sample.get("alg_outputs") or []
                    final = outputs[-1] if outputs else None
                    if final:
                        batch_sections.append(
                            f"| {sample.get('sample_idx', '?')}/{sample.get('total', '?')} | {sample.get('status', '?')} | "
                            f"{final.get('ltSpeed', '-')} | {final.get('lh', '-')} | {final.get('mh', '-')} | {final.get('guideResult', '-')} |"
                        )
                    else:
                        batch_sections.append(
                            f"| {sample.get('sample_idx', '?')}/{sample.get('total', '?')} | {sample.get('status', '?')} | - | - | - | - |"
                        )
            else:
                batch_sections.append("- No parsed sample entries were found in this batch.")
            batch_sections.append("")
    else:
        batch_sections = ["_No parsed IMG batches were produced._"]

    return render_asset_template(
        "img_report_template.md",
        {
            "generated_at": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()),
            "source_mode": source_mode,
            "source_detail_line": format_optional_line("Source Detail", source_detail),
            "mount_root_line": format_optional_line("Mount Root", mount_root),
            "log_file_count": log_file_count,
            "log_deleted_count": log_deleted_count,
            "batch_count": len(batches),
            "batch_sections": "\n".join(batch_sections).rstrip(),
        },
    ).rstrip() + "\n"


def write_img_outputs(
    summary_payload: Optional[Dict[str, object]],
    batches: List[Dict],
    output_dir: Path,
    source_mode: str,
    source_detail: str = "",
    mount_root: str = "",
    log_file_count: int = 0,
    log_deleted_count: int = 0,
) -> Dict[str, str]:
    """统一写出 IMG Markdown 报告和 JSON 批次结果。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    report_markdown = build_img_report_markdown(
        batches=batches,
        source_mode=source_mode,
        source_detail=source_detail,
        mount_root=mount_root,
        log_file_count=log_file_count,
        log_deleted_count=log_deleted_count,
    )
    report_path = output_dir / "img_simulation_report.md"
    report_path.write_text(report_markdown, encoding="utf-8")
    print(f"[输出] Markdown 报告: {report_path}")

    json_path = output_dir / "img_simulation_batches.json"
    save_batch_report(batches, json_path)
    artifacts = {
        "report_path": str(report_path),
        "json_path": str(json_path),
        "batches_json_path": str(json_path),
        "summary": report_markdown,
    }
    if summary_payload is not None:
        update_artifacts(
            summary_payload,
            report_path=str(report_path),
            json_path=str(json_path),
            batches_json_path=str(json_path),
        )
        summary_json_path = output_dir / "img_simulation_summary.json"
        write_json_summary(
            json_path=summary_json_path,
            json_artifact_key="summary_json_path",
            summary=summary_payload,
        )
        print(f"[输出] Summary JSON: {summary_json_path}")
        artifacts["summary_json_path"] = str(summary_json_path)
    return artifacts


def save_batch_report(batches: List[Dict], output_path: Path) -> None:
    """保存批次结构化为 JSON"""
    output_path.write_text(
        json.dumps(batches, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"[输出] JSON 批次结果: {output_path}")


# ════════════════════════════════════════════
# 高層流程 — 全自动 IMG 提取+解析
# ════════════════════════════════════════════

def run_img_extract_pipeline(
    wlctl_path: Path,
    mgr_port: str = "",
    output_dir: Optional[Path] = None,
    mount_wait_sec: int = 30,
    keep_device_logs: bool = False,
    skip_mount: bool = False,
    mount_root: Optional[Path] = None,
) -> Dict:
    """
    全自动 IMG 日志提取流水线:
      1. 挂载 IMG (或跳过)
      2. 等待日志刷新 (20s)
      3. 扫描并复制 *.log
      4. 解析日志
      5. 可选: 删除设备日志
      6. 卸载 IMG

    返回 dict {
      "mount_root": str,
      "log_files_copied": [str],
      "log_files_deleted": int,
      "batches": [parsed_batches],
      "summary": str,
    }
    """
    if output_dir is None:
        output_dir = resolve_default_output_dir("img_extract")
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    result: Dict = init_img_extract_summary(
        mode="device_img_extract",
        source_kind="device_img_extract",
        source_mode="device_img_extract",
        status="running",
    )

    copied: List[Path] = []
    log_dir = output_dir / "img_logs"

    if not skip_mount:
        print("[IMG] 挂载日志分区 (WL+VBUS=TEMP WL+DISK=IMG)...")
        dev = WatchlinkDevice(mgr_port=mgr_port, wlctl_path=wlctl_path)
        with dev.open_disk("log", timeout=mount_wait_sec) as session:
            result["mount_root"] = str(session.root)
            print(f"  [OK] IMG 分区已挂载: {session.root}")
            print("[IMG] 等待日志刷写到磁盘 (20s)...")
            time.sleep(20)
            copied = session.pull_files("**/*.log", log_dir)
            if not copied:
                print("  [警告] IMG 分区中未找到 *.log 文件")
            else:
                print(f"  [OK] 共复制 {len(copied)} 个日志文件到 {log_dir}")
            if not keep_device_logs:
                removed = session.remove_paths("**/*.log", missing_ok=True)
                result["log_files_deleted"] = len([path for path in removed if path.name.lower().endswith(".log")])
    else:
        if mount_root:
            resolved_root = ensure_local_mount_root(Path(mount_root))
        else:
            roots = detect_mount_roots()
            selected_root = select_log_root(roots)
            if selected_root is None:
                raise RuntimeError("No IMG disk found. Provide --mount-root or remove --skip-mount.")
            resolved_root = ensure_local_mount_root(selected_root)
        result["mount_root"] = str(resolved_root)
        print("[IMG] 等待日志刷写到磁盘 (20s)...")
        time.sleep(20)
        copied = pull_mounted_paths(resolved_root, "**/*.log", log_dir)
        if not copied:
            print("  [警告] IMG 分区中未找到 *.log 文件")
        else:
            print(f"  [OK] 共复制 {len(copied)} 个日志文件到 {log_dir}")
        if not keep_device_logs:
            removed = remove_from_mounted_root(resolved_root, "**/*.log", missing_ok=True)
            result["log_files_deleted"] = len([path for path in removed if path.name.lower().endswith(".log")])

    copied_strings = [str(p) for p in copied]

    # Phase 4: 解析
    batches: List[Dict] = []
    if copied:
        batches = parse_img_logs(copied)
        result["batches"] = batches
        print(print_batch_summary(batches))
    else:
        print("[IMG] 无日志文件, 跳过解析")

    finalize_img_extract_summary(
        result,
        batches=batches,
        log_files_copied=copied_strings,
        log_files_deleted=int(result["log_files_deleted"]),
        status="completed" if copied else "completed_no_logs",
    )
    artifacts = write_img_outputs(
        summary_payload=result,
        batches=batches,
        output_dir=output_dir,
        source_mode=result["source_mode"],
        mount_root=result["mount_root"],
        log_file_count=len(copied_strings),
        log_deleted_count=result["log_files_deleted"],
    )
    result.update(artifacts)

    return result


# ════════════════════════════════════════════
# CLI 入口 (独立使用)
# ════════════════════════════════════════════

def main_cli() -> None:
    _safe_reconfigure_stdio()
    housekeep_output()

    parser = argparse.ArgumentParser(
        description="从设备 IMG 日志分区提取并解析 GoMore 离线仿真日志"
    )
    parser.add_argument("--output-dir", default="",
                        help="输出目录 (默认: 系统临时工作目录)")
    parser.add_argument("--mgr-port", default="",
                        help="MGR 串口号")
    parser.add_argument("--mount-wait-sec", type=int, default=30,
                        help="等待 IMG 分区挂载超时 (秒)")
    parser.add_argument("--keep-device-logs", action="store_true",
                        help="不删除设备上的日志文件")
    parser.add_argument("--skip-mount", action="store_true",
                        help="跳过挂载步骤 (用于已挂载的场景)")
    parser.add_argument("--mount-root", default="",
                        help="已挂载的 IMG 分区路径 (配合 --skip-mount 使用)")
    parser.add_argument("--log-file", default="",
                        help="直接解析本地日志文件 (无需连接设备)")

    args = parser.parse_args()

    wlctl_path = build_wlctl_path()

    if args.log_file:
        # 直接解析本地日志文件
        log_path = Path(args.log_file).resolve()
        if not log_path.exists():
            raise RuntimeError(f"日志文件不存在: {log_path}")
        print(f"[INFO] 直接解析本地日志: {log_path}")
        batches = parse_img_logs([log_path])
        if args.output_dir:
            out_dir = Path(args.output_dir).resolve()
        else:
            out_dir = resolve_default_output_dir("log_parse")
        out_dir.mkdir(parents=True, exist_ok=True)

        print(print_batch_summary(batches))
        summary_payload = init_img_extract_summary(
            mode="local_log_parse",
            source_kind="local_log_parse",
            source_mode="local_log_parse",
            status="completed",
            source_extra={"log_file": str(log_path)},
        )
        finalize_img_extract_summary(
            summary_payload,
            batches=batches,
            log_files_copied=[str(log_path)],
            log_files_deleted=0,
            status="completed",
        )
        write_img_outputs(
            summary_payload=summary_payload,
            batches=batches,
            output_dir=out_dir,
            source_mode="local_log_parse",
            source_detail=str(log_path),
            log_file_count=1,
            log_deleted_count=0,
        )
        return

    output_dir = Path(args.output_dir).resolve() if args.output_dir else resolve_default_output_dir("img_extract")

    mount_root = Path(args.mount_root).resolve() if args.mount_root else None

    result = run_img_extract_pipeline(
        wlctl_path=wlctl_path,
        mgr_port=args.mgr_port,
        output_dir=output_dir,
        mount_wait_sec=args.mount_wait_sec,
        keep_device_logs=args.keep_device_logs,
        skip_mount=args.skip_mount,
        mount_root=mount_root,
    )

    print("\n" + "=" * 80)
    print("IMG 日志提取完成!")
    print(f"  挂载点: {result['mount_root']}")
    print(f"  日志文件: {len(result['log_files_copied'])} 个")
    print(f"  解析批次: {len(result.get('batches', []))} 个")
    print(f"  删除设备日志: {result['log_files_deleted']} 个")
    if result.get("report_path"):
        print(f"  Markdown 报告: {result['report_path']}")
    if result.get("summary_json_path"):
        print(f"  Summary JSON: {result['summary_json_path']}")
    if result.get("batches_json_path"):
        print(f"  Batches JSON: {result['batches_json_path']}")
    print("=" * 80)


if __name__ == "__main__":
    try:
        main_cli()
    except Exception as exc:
        runtime_hint = build_runtime_hint(str(exc))
        if runtime_hint:
            print(runtime_hint, file=sys.stderr, flush=True)
        raise

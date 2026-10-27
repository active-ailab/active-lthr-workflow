from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

from _summary_schema import init_summary, update_artifacts, update_metrics

from .offline_sim_contract import collect_sample_paths
from .workflow_reporter import inject_runtime_hint


PrintFn = Callable[[str], None]
ImgPipelineFn = Callable[..., Dict[str, object]]


def print_progress(progress: Dict[str, int]) -> None:
    """Print current multi-sample progress to stderr."""
    total = progress.get("total_samples", 0)
    current = progress.get("current_sample", 0)
    round_cur = progress.get("current_round", 0)
    round_total = progress.get("total_rounds", 3)
    passed = progress.get("passed", 0)
    failed = progress.get("failed", 0)
    if total > 0:
        pct = current * 100.0 / total
        print(
            f"\r  进度: [{current}/{total} 样本] [{round_cur}/{round_total} 轮] "
            f"PASS={passed} FAIL={failed} ({pct:.0f}%)",
            file=sys.stderr,
            end="",
        )
        sys.stderr.flush()


def _load_img_extract_pipeline() -> Optional[ImgPipelineFn]:
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        module = importlib.import_module("img_log_extractor")
    except ImportError:
        return None
    return getattr(module, "run_img_extract_pipeline", None)


def run_img_extract_phase(
    *,
    wlctl_path: Path,
    mgr_port: str,
    output_dir: Path,
    mount_wait_sec: int,
    keep_device_logs: bool,
    summary_info: Dict[str, object],
    pipeline_fn: Optional[ImgPipelineFn] = None,
    print_fn: PrintFn = print,
    error_print_fn: PrintFn = lambda text: print(text, file=sys.stderr),
) -> Dict[str, object]:
    """Run IMG extraction and merge its summary into *summary_info*."""
    pipeline = pipeline_fn or _load_img_extract_pipeline()
    if pipeline is None:
        print_fn("[IMG_ERROR] img_log_extractor 模块不可用, 跳过 IMG 提取")
        summary_info["img_extract_status"] = "module_unavailable"
        return summary_info

    print_fn(f"\n{'=' * 60}")
    print_fn("Phase 5: 从 IMG 分区提取仿真日志")
    print_fn(f"{'=' * 60}")
    print_fn("  流程: 挂载 IMG (v3dl 单命令) → 等待刷写 → 复制 *.log → 解析 → 卸载")

    try:
        img_result = pipeline(
            wlctl_path=wlctl_path,
            mgr_port=mgr_port,
            output_dir=output_dir,
            mount_wait_sec=mount_wait_sec,
            keep_device_logs=keep_device_logs,
        )
        batches = img_result.get("batches", [])
        summary_info["img_extract_status"] = img_result.get("status", "completed")
        summary_info["img_batches"] = len(batches)
        log_files = img_result.get("log_files_copied", [])
        summary_info["img_log_files"] = len(log_files)
        summary_info["img_log_path"] = log_files[0] if log_files else ""
        summary_info["img_report_path"] = img_result.get("report_path", "")
        summary_info["img_json_path"] = img_result.get("json_path", "")
        summary_info["img_summary_json_path"] = img_result.get("summary_json_path", "")
        summary_info["img_batches_json_path"] = img_result.get("batches_json_path", "")
        update_artifacts(
            summary_info,
            img_report_path=summary_info["img_report_path"],
            img_json_path=summary_info["img_json_path"],
            img_summary_json_path=summary_info["img_summary_json_path"],
            img_batches_json_path=summary_info["img_batches_json_path"],
        )
        if batches:
            summary_info["img_total_samples"] = sum(int(batch.get("total", 0)) for batch in batches)
            summary_info["img_passed"] = sum(int(batch.get("passed", 0)) for batch in batches)
            summary_info["img_failed"] = sum(int(batch.get("failed", 0)) for batch in batches)
            update_metrics(
                summary_info,
                img_batches=summary_info["img_batches"],
                img_log_files=summary_info["img_log_files"],
                img_total_samples=summary_info["img_total_samples"],
                img_passed=summary_info["img_passed"],
                img_failed=summary_info["img_failed"],
            )
    except Exception as exc:
        summary_info["img_extract_status"] = f"failed: {exc}"
        inject_runtime_hint(summary_info, str(exc), prefix="img_")
        error_print_fn(f"\n[IMG_ERROR] IMG 提取阶段失败: {exc}")
        runtime_hint = str(summary_info.get("img_runtime_hint", "") or "")
        if runtime_hint:
            error_print_fn(runtime_hint)

    return summary_info


def resolve_data_sample_dir(data_dir: str, script_path: Path) -> Path:
    """解析 data_sample 目录。必须显式指定 data_dir。"""
    del script_path
    if not data_dir:
        raise RuntimeError("仿真流程必须显式传入本地 --data-dir data_sample 目录")
    path = Path(data_dir).expanduser().resolve()
    if not path.is_dir():
        raise RuntimeError(f"data_sample 目录不存在: {path}")
    return path


def collect_data_sample_csvs(local_data_sample: Path) -> List[Path]:
    return collect_sample_paths(local_data_sample)


def print_data_sample_inventory(
    local_data_sample: Path,
    csv_files: List[Path],
    *,
    preview_limit: int = 10,
    print_fn: PrintFn = print,
) -> None:
    print_fn(f"[信息] PC 端 data_sample: {local_data_sample}")
    print_fn(f"[信息] 待拷贝 CSV: {len(csv_files)} 个")
    for csv_path in csv_files[:preview_limit]:
        print_fn(f"       {csv_path.relative_to(local_data_sample)}")
    if len(csv_files) > preview_limit:
        print_fn(f"       ... 及其他 {len(csv_files) - preview_limit} 个")


def init_img_only_summary(stamp: str) -> Dict[str, object]:
    summary = init_summary(
        workflow="multi_sample",
        status="img_extract_only",
        mode="img_only",
        source={
            "kind": "img_only",
            "img_only": True,
        },
    )
    summary["timestamp"] = stamp
    return summary


def init_multi_sample_summary(stamp: str, local_data_sample: Path, csv_count: int) -> Dict[str, object]:
    summary = init_summary(
        workflow="multi_sample",
        status="initialized",
        mode="device_multi_sim",
        source={
            "kind": "device_multi_sim",
            "data_sample_dir": str(local_data_sample),
            "img_only": False,
        },
    )
    summary["timestamp"] = stamp
    summary["data_sample_dir"] = str(local_data_sample)
    summary["csv_count"] = csv_count
    summary["total_samples"] = 0
    summary["passed"] = 0
    summary["failed"] = 0
    update_metrics(summary, input_csv_count=csv_count, total_samples=0, passed=0, failed=0)
    return summary

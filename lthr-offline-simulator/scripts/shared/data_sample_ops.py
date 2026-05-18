from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from .jira_utils import CSV_TIMESTAMP_RE
from .watchlink_runtime import (
    ensure_local_mount_root,
    format_timestamp,
    push_to_mounted_root,
    remove_from_mounted_root,
)


def normalize_gomore_root(gomore_root: str | Path) -> Path:
    return ensure_local_mount_root(Path(gomore_root))


def clean_and_copy_single_sample(gomore_root: str | Path, sample_csv: Path) -> Dict[str, List[str]]:
    root = normalize_gomore_root(gomore_root)
    base_dir = root / "sport" / "gomore"
    target_name = sample_csv.name
    if not CSV_TIMESTAMP_RE.match(target_name):
        target_name = f"gomore_data_{format_timestamp()}.csv"

    deleted_csvs = [
        str(path)
        for path in remove_from_mounted_root(base_dir, "data_sample", recursive=True, missing_ok=True)
        if path.name.lower().endswith(".csv")
    ]
    deleted_state_files: List[str] = []
    for filename in ("gomore_his.data", "lactateData.data"):
        deleted_state_files.extend(
            str(path)
            for path in remove_from_mounted_root(base_dir, filename, missing_ok=True)
        )
    copied_path = push_to_mounted_root(base_dir, sample_csv, f"data_sample/{target_name}")[0]
    return {
        "deleted_csvs": deleted_csvs,
        "deleted_state_files": deleted_state_files,
        "copied_sample": [str(copied_path)],
    }


def verify_single_sample_deployment(
    gomore_root: str | Path,
    copied_sample: str | Path,
    source_size: int,
) -> Dict[str, object]:
    root = normalize_gomore_root(gomore_root)
    base_dir = root / "sport" / "gomore"
    copied_path = Path(copied_sample)
    data_sample_dir = base_dir / "data_sample"
    csv_paths = sorted(
        (path for path in data_sample_dir.glob("*.csv") if path.is_file()),
        key=lambda path: (path.stat().st_mtime, path.name),
    )
    state_files = [
        str(path)
        for path in (base_dir / "gomore_his.data", base_dir / "lactateData.data")
        if path.exists()
    ]
    data = {
        "copied_exists": copied_path.exists(),
        "copied_size": copied_path.stat().st_size if copied_path.exists() else -1,
        "source_size": source_size,
        "csv_count": len(csv_paths),
        "csv_paths": [str(path) for path in csv_paths],
        "latest_csv": str(csv_paths[-1]) if csv_paths else "",
        "state_file_count": len(state_files),
        "state_files": state_files,
    }
    if not data["copied_exists"]:
        raise RuntimeError(f"Copied sample missing on device: {copied_path}")
    if int(data["copied_size"]) != int(data["source_size"]):
        raise RuntimeError(
            f"Copied sample size mismatch: copied={data['copied_size']} source={data['source_size']}"
        )
    if int(data["csv_count"]) != 1:
        raise RuntimeError(f"Expected exactly 1 root-level CSV in data_sample, got {data['csv_count']}")
    if data["latest_csv"] != str(copied_path):
        raise RuntimeError(f"Latest device CSV mismatch: expected {copied_path}, got {data['latest_csv']}")
    if int(data["state_file_count"]) != 0:
        raise RuntimeError(f"History files still exist after cleanup: {data['state_files']}")
    return data


def deploy_data_sample_tree(gomore_root: str | Path, local_data_sample: Path) -> Dict[str, object]:
    root = normalize_gomore_root(gomore_root)
    # 设备路径 /storage/sport/gomore/data_sample 对应挂载点 sport/gomore/data_sample
    base_dir = root / "sport" / "gomore"
    base_dir.mkdir(parents=True, exist_ok=True)
    
    # 清理旧 data_sample
    print(f"  [检查] 正在清理设备旧 data_sample 目录...")
    removed_paths = remove_from_mounted_root(base_dir, "data_sample", recursive=True, missing_ok=True)
    if removed_paths:
        print(f"  [清理] 已删除设备旧 data_sample: {len(removed_paths)} 个文件/目录")
    else:
        print(f"  [清理] 设备 data_sample 目录不存在或已为空，无需清理")
    
    # 拷贝新文件
    copied_paths = push_to_mounted_root(base_dir, local_data_sample, "data_sample")

    deleted_state_files: List[str] = []
    for hist_file in ("gomore_his.data", "lactateData.data"):
        deleted_state_files.extend(
            str(path)
            for path in remove_from_mounted_root(base_dir, hist_file, missing_ok=True)
        )

    source_csv_count = len(list(local_data_sample.rglob("gomore_data_*.csv")))
    source_meta_count = len([path for path in local_data_sample.glob("base_case_updates_*") if path.is_file()])
    data_sample_root = base_dir / "data_sample"
    csv_paths = sorted(
        (path for path in data_sample_root.rglob("gomore_data_*.csv") if path.is_file()),
        key=lambda path: str(path.relative_to(data_sample_root)),
    )
    meta_paths = sorted(
        (path for path in data_sample_root.glob("base_case_updates_*") if path.is_file()),
        key=lambda path: path.name,
    )
    state_files = [
        str(path)
        for path in (base_dir / "gomore_his.data", base_dir / "lactateData.data")
        if path.exists()
    ]

    data = {
        "source_csv_count": source_csv_count,
        "source_meta_count": source_meta_count,
        "csv_count": len(csv_paths),
        "csv_paths": [str(path) for path in csv_paths],
        "meta_count": len(meta_paths),
        "meta_paths": [str(path) for path in meta_paths],
        "state_file_count": len(state_files),
        "state_files": state_files,
        "data_sample": str(data_sample_root),
        "deleted_state_files": deleted_state_files,
        "copied_paths": [str(path) for path in copied_paths],
        "copied_csv_relative_paths": [
            str(path.relative_to(data_sample_root))
            for path in copied_paths
            if path.name.startswith("gomore_data_") and path.suffix.lower() == ".csv"
        ],
    }
    if data["csv_count"] != source_csv_count:
        raise RuntimeError(
            f"Device data_sample CSV count mismatch: copied={data['csv_count']} expected={source_csv_count}"
        )
    if data["meta_count"] != source_meta_count:
        raise RuntimeError(
            f"Device data_sample meta count mismatch: copied={data['meta_count']} expected={source_meta_count}"
        )
    if data["state_file_count"] != 0:
        raise RuntimeError(f"History files still exist after cleanup: {data['state_files']}")
    return data

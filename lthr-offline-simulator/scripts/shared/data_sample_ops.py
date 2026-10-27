from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Sequence

from .jira_utils import CSV_TIMESTAMP_RE
from .watchlink_runtime import (
    ensure_local_mount_root,
    format_timestamp,
    list_mounted_paths,
    pull_mounted_paths,
    push_to_mounted_root,
    read_mounted_file,
    remove_from_mounted_root,
)
from .offline_sim_contract import RESULT_NAME, parse_result_ini, validate_result


def normalize_gomore_root(gomore_root: str | Path) -> Path:
    return ensure_local_mount_root(Path(gomore_root))


def clean_and_copy_single_sample(gomore_root: str | Path, sample_csv: Path) -> Dict[str, List[str]]:
    root = normalize_gomore_root(gomore_root)
    base_dir = root / "sport" / "gomore"
    target_name = sample_csv.name
    if not CSV_TIMESTAMP_RE.match(target_name):
        target_name = f"gomore_data_{format_timestamp()}.csv"

    copied_path = push_to_mounted_root(base_dir, sample_csv, f"data_sample/{target_name}")[0]
    return {
        "deleted_csvs": [],
        "deleted_state_files": [],
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
    csv_paths = sorted(path for path in data_sample_dir.rglob("*.csv") if path.is_file())
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
        "latest_csv": str(copied_path) if copied_path.exists() else "",
        "state_file_count": len(state_files),
        "state_files": state_files,
    }
    if not data["copied_exists"]:
        raise RuntimeError(f"Copied sample missing on device: {copied_path}")
    if int(data["copied_size"]) != int(data["source_size"]):
        raise RuntimeError(
            f"Copied sample size mismatch: copied={data['copied_size']} source={data['source_size']}"
        )
    return data


def deploy_data_sample_tree(gomore_root: str | Path, local_data_sample: Path) -> Dict[str, object]:
    root = normalize_gomore_root(gomore_root)
    # 设备路径 /storage/sport/gomore/data_sample 对应挂载点 sport/gomore/data_sample
    base_dir = root / "sport" / "gomore"
    base_dir.mkdir(parents=True, exist_ok=True)
    
    # Do not pre-clean: the config selects this run's inputs. Full-success cleanup runs after extraction.
    copied_paths = push_to_mounted_root(base_dir, local_data_sample, "data_sample")

    source_csv_count = sum(
        1 for path in local_data_sample.rglob("*")
        if path.is_file() and path.name.lower().startswith("gomore_data_") and path.suffix.lower() == ".csv"
    )
    source_meta_count = len([path for path in local_data_sample.glob("base_case_updates_*") if path.is_file()])
    data_sample_root = base_dir / "data_sample"
    missing_copies = [str(path) for path in copied_paths if not path.is_file()]
    if missing_copies:
        raise RuntimeError("Device deployment copy verification failed: " + ", ".join(missing_copies))
    csv_paths = sorted(
        (
            path for path in data_sample_root.rglob("*")
            if path.is_file() and path.name.lower().startswith("gomore_data_") and path.suffix.lower() == ".csv"
        ),
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
        "deleted_state_files": [],
        "copied_paths": [str(path) for path in copied_paths],
        "copied_csv_relative_paths": [
            str(path.relative_to(data_sample_root))
            for path in copied_paths
            if path.name.lower().startswith("gomore_data_") and path.suffix.lower() == ".csv"
        ],
    }
    data["copied_csv_count"] = len(data["copied_csv_relative_paths"])
    if data["copied_csv_count"] != source_csv_count:
        raise RuntimeError(
            f"Device data_sample copied CSV count mismatch: copied={data['copied_csv_count']} expected={source_csv_count}"
        )
    if data["meta_count"] < source_meta_count:
        raise RuntimeError(
            f"Device data_sample metadata count is smaller than source: copied={data['meta_count']} expected_at_least={source_meta_count}"
        )
    return data


def stage_data_sample_inputs(
    source_data_sample: Path,
    staged_data_sample: Path,
    sample_paths: Sequence[Path],
) -> List[str]:
    """Create a clean local data_sample containing only configured inputs and metadata."""
    source_root = source_data_sample.expanduser().resolve()
    destination = staged_data_sample.expanduser().resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(f"data_sample directory not found: {source_root}")
    if (
        source_root == destination
        or source_root in destination.parents
        or destination in source_root.parents
    ):
        raise ValueError("staging directory must be separate from the source data_sample tree")

    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    relative_samples: List[str] = []
    for sample in sample_paths:
        resolved_sample = sample.expanduser().resolve()
        try:
            relative = resolved_sample.relative_to(source_root)
        except ValueError as exc:
            raise ValueError(f"sample is outside data_sample root: {sample}") from exc
        if not resolved_sample.is_file() or not resolved_sample.name.lower().startswith("gomore_data_") or resolved_sample.suffix.lower() != ".csv":
            raise ValueError(f"not a GoMore sample CSV: {resolved_sample}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(resolved_sample, target)
        relative_samples.append("data_sample/" + relative.as_posix())

    # Preserve root-level case metadata used by existing data_sample workflows.
    for metadata in source_root.glob("base_case_updates_*"):
        if metadata.is_file():
            shutil.copyfile(metadata, destination / metadata.name)

    return relative_samples


def extract_simulation_artifacts(
    data_root: str | Path,
    *,
    run_id: str,
    mode: str,
    rounds: int,
    sample_count: int,
    output_dir: Path,
) -> Dict[str, object]:
    """Copy this run's result/history locally; clear data_sample only after a validated full success."""
    root = ensure_local_mount_root(Path(data_root))
    device_dir = "sport/gomore/data_sample"
    result_device_path = f"{device_dir}/{RESULT_NAME}"
    output_dir.mkdir(parents=True, exist_ok=True)
    result_text = read_mounted_file(root, result_device_path)
    result = parse_result_ini(result_text)

    result_path = output_dir / RESULT_NAME
    result_path.write_text(result_text, encoding="utf-8")
    validation_error = None
    try:
        validate_result(
            result,
            run_id,
            expected_mode=mode,
            expected_rounds=rounds,
            expected_sample_count=sample_count,
        )
    except Exception as exc:
        validation_error = exc

    archive_pattern = f"{device_dir}/gomore_his_{run_id}*.data"
    archive_sources = list_mounted_paths(root, archive_pattern, recursive=False)
    copied_archives: List[str] = []
    if archive_sources:
        with tempfile.TemporaryDirectory(prefix="gomore_history_") as temporary_dir:
            copied = pull_mounted_paths(root, archive_pattern, Path(temporary_dir))
            if len(copied) != len(archive_sources):
                raise RuntimeError(
                    f"History archive extraction count mismatch: copied={len(copied)} expected={len(archive_sources)}"
                )
            copied_by_name = {path.name: path for path in copied}
            for source in archive_sources:
                temporary_copy = copied_by_name.get(source.name)
                if (
                    temporary_copy is None
                    or not temporary_copy.exists()
                    or temporary_copy.stat().st_size != source.stat().st_size
                ):
                    raise RuntimeError(f"History archive extraction verification failed: {source.name}")
            for source in archive_sources:
                final_path = output_dir / source.name
                shutil.move(str(copied_by_name[source.name]), str(final_path))
                copied_archives.append(str(final_path))

    # The copied result is retained locally even if validation fails; remove only the extracted device files.
    remove_from_mounted_root(root, result_device_path, missing_ok=False)
    for source in archive_sources:
        remove_from_mounted_root(root, f"{device_dir}/{source.name}", missing_ok=False)
    data_sample_cleared = False
    if not validation_error and result.get("overall_status") == "success":
        remove_from_mounted_root(root, f"{device_dir}/*", recursive=True, missing_ok=True)
        if list_mounted_paths(root, device_dir, recursive=False):
            raise RuntimeError("data_sample cleanup verification failed after successful simulation")
        data_sample_cleared = True
    return {
        "result_path": str(result_path),
        "result": result,
        "history_paths": copied_archives,
        "history_count": len(copied_archives),
        "data_sample_cleared": data_sample_cleared,
        "validation_error": str(validation_error) if validation_error is not None else "",
    }

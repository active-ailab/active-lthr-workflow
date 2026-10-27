from __future__ import annotations

import re
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Dict, List, Sequence

CONFIG_NAME = "sim_config.ini"
RESULT_NAME = "sim_result.ini"
TEST_COMMAND = "utest gomore_offline_csv_sim_tests"
START_MARKER = "GoMore Offline CSV Simulation Start"
END_MARKER = "GoMore Offline CSV Simulation End"
MODES = {
    "single_sample",
    "single_person_multi_sample",
    "multi_person_multi_sample",
}
MAX_SAMPLES = 64
MAX_SAMPLE_PATH = 256
MAX_RUN_ID = 48

RESULT_LINE_RE = re.compile(
    r"Gomore output ltSpeed=(?P<ltSpeed>[-+]?\d+(?:\.\d+)?) "
    r"lh=(?P<lh>-?\d+) mh=(?P<mh>-?\d+) guideResult=(?P<guideResult>-?\d+)"
)
SAMPLE_PROGRESS_RE = re.compile(
    r"(?:Sample\s+(?P<sample>\d+)/(?P<sample_total>\d+),\s*)?"
    r"round\s+(?P<round>\d+)/(?P<round_total>\d+),\s*start=(?P<start>\d+)",
    re.IGNORECASE,
)


def make_run_id() -> str:
    return "sim_{}_{}".format(time.strftime("%Y%m%d_%H%M%S", time.localtime()), uuid.uuid4().hex[:8])


def collect_sample_paths(data_sample_dir: Path) -> List[Path]:
    """Return GoMore input CSVs in stable relative-path order; metadata is excluded."""
    root = data_sample_dir.resolve()
    return sorted(
        (
            path for path in root.rglob("*")
            if path.is_file()
            and path.name.lower().startswith("gomore_data_")
            and path.suffix.lower() == ".csv"
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _validate_relative_sample_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    if not normalized.startswith("data_sample/") or not normalized.endswith(".csv"):
        raise ValueError("sample path must start with data_sample/ and end with .csv")
    if ".." in PurePosixPath(normalized).parts or "\n" in normalized or "\r" in normalized:
        raise ValueError("sample path contains an invalid component")
    if len(normalized) >= MAX_SAMPLE_PATH:
        raise ValueError("sample path exceeds C-side path limit")
    return normalized


def render_config(
    *,
    run_id: str,
    mode: str,
    rounds: int,
    sample_paths: Sequence[str],
) -> str:
    if mode not in MODES:
        raise ValueError("mode must be one of: " + ", ".join(sorted(MODES)))
    if not run_id or len(run_id) > MAX_RUN_ID or not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise ValueError("run_id must be 1-48 ASCII letters, digits, '_' or '-'")
    if rounds <= 0:
        raise ValueError("rounds must be greater than zero")
    if not sample_paths or len(sample_paths) > MAX_SAMPLES:
        raise ValueError("sample_count must be between 1 and 64")
    if mode == "single_sample" and len(sample_paths) != 1:
        raise ValueError("single_sample mode requires exactly one sample")
    if mode != "single_sample" and len(sample_paths) < 2:
        raise ValueError("multi-sample modes require at least two samples")

    lines = [
        "version=1",
        "run_id={}".format(run_id),
        "mode={}".format(mode),
        "rounds={}".format(rounds),
        "sample_count={}".format(len(sample_paths)),
    ]
    for index, path in enumerate(sample_paths):
        lines.append("sample.{}={}".format(index, _validate_relative_sample_path(path)))
    return "\n".join(lines) + "\n"


def write_config(
    data_sample_dir: Path,
    *,
    run_id: str,
    mode: str,
    rounds: int,
    sample_paths: Sequence[str],
) -> Path:
    path = data_sample_dir / CONFIG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(
            render_config(run_id=run_id, mode=mode, rounds=rounds, sample_paths=sample_paths)
        )
    return path


def parse_result_ini(text: str) -> Dict[str, object]:
    fields: Dict[str, str] = {}
    samples: Dict[int, Dict[str, str]] = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        match = re.match(r"^sample\.(\d+)\.(.+)$", key)
        if match:
            samples.setdefault(int(match.group(1)), {})[match.group(2)] = value
        else:
            fields[key] = value
    fields["samples"] = [samples[index] for index in sorted(samples)]
    return fields


def validate_result(
    result: Dict[str, object],
    expected_run_id: str,
    *,
    expected_mode: str = "",
    expected_rounds: int = 0,
    expected_sample_count: int = 0,
) -> None:
    if result.get("version") != "1":
        raise RuntimeError("sim_result.ini has an unsupported or missing version")
    if result.get("run_id") != expected_run_id:
        raise RuntimeError(
            "sim_result.ini run_id mismatch: expected {}, got {}".format(
                expected_run_id, result.get("run_id", "")
            )
        )
    if result.get("overall_status") not in {"running", "success", "partial", "failed"}:
        raise RuntimeError("sim_result.ini has an invalid overall_status")
    if expected_mode and result.get("mode") != expected_mode:
        raise RuntimeError("sim_result.ini mode does not match sim_config.ini")
    if expected_rounds and str(result.get("rounds")) != str(expected_rounds):
        raise RuntimeError("sim_result.ini rounds do not match sim_config.ini")
    if expected_sample_count and str(result.get("sample_count")) != str(expected_sample_count):
        raise RuntimeError("sim_result.ini sample_count does not match sim_config.ini")
    samples = result.get("samples")
    if expected_sample_count and (not isinstance(samples, list) or len(samples) != expected_sample_count):
        raise RuntimeError("sim_result.ini sample entries do not match sim_config.ini")


def _legacy_header(sample_path: str, sample_index: int, sample_count: int) -> str:
    parts = PurePosixPath(sample_path).parts
    if parts and parts[0] == "data_sample":
        parts = parts[1:]
    filename = parts[-1]
    if parts and parts[0] in {"BASE_CASE", "JIRA_CASE", "ROOT_CASE"}:
        case = parts[0]
        sample_dir = parts[1] if len(parts) > 2 else Path(filename).stem
    else:
        case = "ROOT_CASE"
        sample_dir = parts[-2] if len(parts) > 1 else Path(filename).stem
    sample_dir = re.sub(r"\s+", "_", sample_dir)
    return "========== SAMPLE[{}/{}] CASE={} SAMPLE_DIR={} FILE={} ==========".format(
        sample_index, sample_count, case, sample_dir, filename
    )


def normalize_log_for_report(log_text: str, sample_paths: Sequence[str]) -> str:
    """Add report-compatible sample headers while preserving every original C log line."""
    output: List[str] = []
    for line in (log_text or "").splitlines(keepends=True):
        match = SAMPLE_PROGRESS_RE.search(line)
        if match:
            sample_index = int(match.group("sample") or "1")
            if 1 <= sample_index <= len(sample_paths):
                output.append(
                    _legacy_header(sample_paths[sample_index - 1], sample_index, len(sample_paths)) + "\n"
                )
                output.append(
                    "Round {}/{} start\n".format(match.group("round"), match.group("round_total"))
                )
        output.append(line)
    return "".join(output)


def parse_round_results(
    log_text: str,
    *,
    samples: Sequence[str],
    rounds: int,
    result_samples: Sequence[Dict[str, str]],
) -> List[Dict[str, object]]:
    """Associate final algorithm output lines with the C sample/round progress markers."""
    outputs: Dict[tuple[int, int], Dict[str, object]] = {}
    current_sample = 0
    current_round = 0
    for line in (log_text or "").splitlines():
        progress = SAMPLE_PROGRESS_RE.search(line)
        if progress:
            current_sample = int(progress.group("sample") or "1") - 1
            current_round = int(progress.group("round"))
            continue
        result_match = RESULT_LINE_RE.search(line)
        if result_match and 0 <= current_sample < len(samples) and current_round > 0:
            outputs[(current_sample, current_round)] = {
                "run": current_round,
                "status": "ok",
                "ltSpeed": float(result_match.group("ltSpeed")),
                "lh": int(result_match.group("lh")),
                "mh": int(result_match.group("mh")),
                "guideResult": int(result_match.group("guideResult")),
                "raw": result_match.group(0),
            }

    summaries: List[Dict[str, object]] = []
    for sample_index, sample_path in enumerate(samples):
        device_sample = result_samples[sample_index] if sample_index < len(result_samples) else {}
        sample_status = device_sample.get("status", "pending")
        rounds_completed = int(device_sample.get("rounds_completed", "0") or 0)
        stopped_round = int(device_sample.get("stopped_round", "0") or 0)
        sample_results: List[Dict[str, object]] = []
        for round_index in range(1, rounds + 1):
            item = outputs.get((sample_index, round_index))
            if item is not None:
                sample_results.append(item)
            elif round_index <= rounds_completed:
                sample_results.append({"run": round_index, "status": "ok", "result_missing": True})
            elif stopped_round == round_index and sample_status in {"input_error", "execution_error"}:
                sample_results.append({"run": round_index, "status": sample_status})
            else:
                sample_results.append({"run": round_index, "status": "not_run"})
        summaries.append({
            "sample_path": sample_path,
            "sample_csv": PurePosixPath(sample_path).name,
            "device_status": sample_status,
            "rounds_completed": rounds_completed,
            "stopped_round": stopped_round,
            "error_stage": device_sample.get("error_stage", ""),
            "error_reason": device_sample.get("error_reason", ""),
            "error_code": device_sample.get("error_code", ""),
            "results": sample_results,
        })
    return summaries

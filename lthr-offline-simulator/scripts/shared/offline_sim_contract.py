from __future__ import annotations

import argparse
import re
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Sequence

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
UINT32_MAX = (1 << 32) - 1
DEFAULT_INTERVAL_SECONDS = 86400
MAX_CSV_ROWS = 20000

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


def add_simulation_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--start-time", type=int, help="Unix UTC seconds; omitted: current device time")
    parser.add_argument("--interval-seconds", type=int, help="Interval between simulation starts in seconds (default: 86400)")
    parser.add_argument("--clear-history-on-start", type=int, choices=(0, 1),
                        help="single_sample only: 1 starts with empty history (default), 0 reuses original history")
    parser.add_argument("--clear-history-on-end", type=int, choices=(0, 1),
                        help="single_sample only: 1 archives simulation history and restores original (default), 0 keeps simulation history active")


def simulation_options_from_args(args: argparse.Namespace) -> Dict[str, Optional[int]]:
    return {key: getattr(args, key) for key in (
        "start_time", "interval_seconds", "clear_history_on_start", "clear_history_on_end",
    )}


def validate_simulation_options(
    mode: str,
    *,
    start_time: Optional[int] = None,
    interval_seconds: Optional[int] = None,
    clear_history_on_start: Optional[int] = None,
    clear_history_on_end: Optional[int] = None,
) -> None:
    for key, value in (("start_time", start_time), ("interval_seconds", interval_seconds)):
        if value is not None and (type(value) is not int or not 1 <= value <= UINT32_MAX):
            raise ValueError("{} must be an integer between 1 and {}".format(key, UINT32_MAX))
    for key, value in (("clear_history_on_start", clear_history_on_start),
                       ("clear_history_on_end", clear_history_on_end)):
        if value is not None:
            if type(value) is not int or value not in (0, 1):
                raise ValueError("{} must be 0 or 1".format(key))
            if mode != "single_sample":
                raise ValueError("{} requires single_sample mode".format(key))


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
    start_time: Optional[int] = None,
    interval_seconds: Optional[int] = None,
    clear_history_on_start: Optional[int] = None,
    clear_history_on_end: Optional[int] = None,
) -> str:
    if mode not in MODES:
        raise ValueError("mode must be one of: " + ", ".join(sorted(MODES)))
    if not run_id or len(run_id) > MAX_RUN_ID or not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise ValueError("run_id must be 1-48 ASCII letters, digits, '_' or '-'")
    if type(rounds) is not int or not 1 <= rounds <= UINT32_MAX:
        raise ValueError("rounds must be between 1 and {}".format(UINT32_MAX))
    if not sample_paths or len(sample_paths) > MAX_SAMPLES:
        raise ValueError("sample_count must be between 1 and 64")
    if mode == "single_sample" and len(sample_paths) != 1:
        raise ValueError("single_sample mode requires exactly one sample")
    if mode != "single_sample" and len(sample_paths) < 2:
        raise ValueError("multi-sample modes require at least two samples")
    options = dict(start_time=start_time, interval_seconds=interval_seconds,
                   clear_history_on_start=clear_history_on_start, clear_history_on_end=clear_history_on_end)
    validate_simulation_options(mode, **options)
    if start_time is not None:
        max_index = rounds - 1 if mode == "multi_person_multi_sample" else rounds * len(sample_paths) - 1
        if start_time + max_index * (interval_seconds or DEFAULT_INTERVAL_SECONDS) + MAX_CSV_ROWS + 1 > UINT32_MAX:
            raise ValueError("configured_time_axis_overflow: start time, interval and rounds exceed C timestamp range")

    lines = [
        "version=1",
        "run_id={}".format(run_id),
        "mode={}".format(mode),
        "rounds={}".format(rounds),
        "sample_count={}".format(len(sample_paths)),
    ]
    lines.extend("{}={}".format(key, value) for key, value in options.items() if value is not None)
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
    start_time: Optional[int] = None,
    interval_seconds: Optional[int] = None,
    clear_history_on_start: Optional[int] = None,
    clear_history_on_end: Optional[int] = None,
) -> Path:
    config_text = render_config(
        run_id=run_id, mode=mode, rounds=rounds, sample_paths=sample_paths,
        start_time=start_time, interval_seconds=interval_seconds,
        clear_history_on_start=clear_history_on_start, clear_history_on_end=clear_history_on_end,
    )
    path = data_sample_dir / CONFIG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(config_text)
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
    expected_start_time: Optional[int] = None,
    expected_interval_seconds: Optional[int] = None,
    expected_clear_history_on_start: Optional[int] = None,
    expected_clear_history_on_end: Optional[int] = None,
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
    for key, expected, default in (
        ("start_time", expected_start_time, None),
        ("interval_seconds", expected_interval_seconds, DEFAULT_INTERVAL_SECONDS),
        ("clear_history_on_start", expected_clear_history_on_start, 1),
        ("clear_history_on_end", expected_clear_history_on_end, 1),
    ):
        # Legacy firmware remains usable with omitted options; explicit options must be echoed.
        if expected is None and key not in result:
            continue
        value = expected if expected is not None else default
        if value is not None and str(result.get(key)) != str(value):
            raise RuntimeError("sim_result.ini {} does not match sim_config.ini".format(key))
    if result.get("overall_status") == "success" and "start_time" in result:
        try:
            start = int(result["start_time"])
        except (TypeError, ValueError) as exc:
            raise RuntimeError("sim_result.ini has an invalid start_time") from exc
        if not 1 <= start <= UINT32_MAX - MAX_CSV_ROWS - 1:
            raise RuntimeError("sim_result.ini has an invalid start_time")
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

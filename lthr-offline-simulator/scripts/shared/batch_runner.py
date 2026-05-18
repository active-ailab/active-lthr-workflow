from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .deploy_utils import LTHR_OUTPUT_ROOT


RunSubprocessFn = Callable[..., Any]
JsonLoadsFn = Callable[[str], Dict[str, object]]
TimeFn = Callable[[], float]


@dataclass
class BatchIssueExecution:
    issue: str
    issue_dir: Path
    cmd: List[str]
    returncode: int
    elapsed_sec: float
    stdout: str
    stderr: str
    json_path: Optional[Path]
    data: Optional[Dict[str, object]]
    json_error: str = ""


def resolve_batch_work_dir(custom_work_dir: str, stamp: str) -> Path:
    if custom_work_dir:
        return Path(custom_work_dir).expanduser().resolve()
    return LTHR_OUTPUT_ROOT / "topic_ota" / f"batch_{stamp}"


def load_issues(issues: Sequence[str], issues_file: str = "") -> List[str]:
    collected: List[str] = list(issues)
    if issues_file:
        path = Path(issues_file).expanduser()
        if not path.exists():
            raise RuntimeError(f"Issues file not found: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                collected.append(line)
    if not collected:
        raise RuntimeError("No issues specified (use --issues or --issues-file)")

    deduped: List[str] = []
    seen = set()
    for issue in collected:
        if issue not in seen:
            seen.add(issue)
            deduped.append(issue)
    return deduped


def find_json_artifact(issue_dir: Path) -> Optional[Path]:
    matches = sorted(issue_dir.glob("artifacts/lthr_offline_sim_*.json"))
    return matches[-1] if matches else None


def build_issue_command(
    *,
    python_exe: str,
    child_script: Path,
    issue: str,
    child_argv: Sequence[str],
    issue_dir: Path,
) -> List[str]:
    return [python_exe, str(child_script), issue, *child_argv, "--work-dir", str(issue_dir)]


def execute_batch_issue(
    *,
    issue: str,
    batch_dir: Path,
    python_exe: str,
    child_script: Path,
    child_argv: Sequence[str],
    run_subprocess_fn: RunSubprocessFn = subprocess.run,
    json_loads_fn: JsonLoadsFn = json.loads,
    time_fn: TimeFn = time.time,
) -> BatchIssueExecution:
    issue_dir = batch_dir / issue
    issue_dir.mkdir(parents=True, exist_ok=True)
    cmd = build_issue_command(
        python_exe=python_exe,
        child_script=child_script,
        issue=issue,
        child_argv=child_argv,
        issue_dir=issue_dir,
    )

    started_at = time_fn()
    completed = run_subprocess_fn(cmd, capture_output=True, text=True)
    elapsed_sec = time_fn() - started_at

    json_path = find_json_artifact(issue_dir) if completed.returncode == 0 else None
    data: Optional[Dict[str, object]] = None
    json_error = ""
    if json_path:
        try:
            data = json_loads_fn(json_path.read_text(encoding="utf-8"))
        except Exception as exc:
            json_error = str(exc)

    return BatchIssueExecution(
        issue=issue,
        issue_dir=issue_dir,
        cmd=cmd,
        returncode=completed.returncode,
        elapsed_sec=elapsed_sec,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        json_path=json_path,
        data=data,
        json_error=json_error,
    )

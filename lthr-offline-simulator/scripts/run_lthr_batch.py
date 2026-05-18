#!/usr/bin/env python3
"""
Batch runner: process multiple Jira issues through run_lthr_offline_sim.py sequentially.

Each issue goes through the full pipeline (download CSV → device sim → upload results).
Results are aggregated into a consolidated batch report.

Usage:
  python3 run_lthr_batch.py --issues MOD-7923 MOD-7924 --disk-root /mnt/f
  python3 run_lthr_batch.py --issues-file batch.txt --runs 10 --continue-on-error
  python3 run_lthr_batch.py --issues MOD-7923 MOD-7924 --csv-path /tmp/sample.csv --runs 5
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

from shared.batch_runner import (
    execute_batch_issue,
    load_issues,
    resolve_batch_work_dir,
)
from shared.deploy_utils import housekeep_output
from shared.report_generator import (
    build_consolidated_report as _build_consolidated_report,
    format_duration as _format_duration,
)
from shared.watchlink_runtime import build_runtime_hint


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Batch LTHR offline simulator: run_lthr_offline_sim.py for multiple issues",
        add_help=False,
    )
    p.add_argument("--issues", nargs="+", default=[],
                    help="Jira issue keys to process")
    p.add_argument("--issues-file", default="",
                    help="File with one issue key per line (# for comments)")
    p.add_argument("--continue-on-error", action="store_true",
                    help="Skip failed issues and continue")
    p.add_argument("--batch-work-dir", default="",
                    help="Root work dir (default: /tmp/tmp_lthr_out/topic_ota/batch_<stamp>)")
    return p

def collect_batch_args() -> Tuple[argparse.Namespace, List[str]]:
    """Parse batch-specific args; return (batch_args, remaining_argv)."""
    if "-h" in sys.argv or "--help" in sys.argv:
        _build_parser().print_help()
        print()
        print("Additionally, all run_lthr_offline_sim.py arguments are accepted and passed through.")
        sys.exit(0)
    parser = _build_parser()
    ns, remaining = parser.parse_known_args()
    return ns, remaining

def main() -> None:
    housekeep_output()
    batch_ns, child_argv = collect_batch_args()
    issues = load_issues(batch_ns.issues, batch_ns.issues_file)
    continue_on_error = batch_ns.continue_on_error
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())

    batch_dir = resolve_batch_work_dir(batch_ns.batch_work_dir, stamp)
    batch_dir.mkdir(parents=True, exist_ok=True)

    child_script = Path(__file__).resolve().parent / "run_lthr_offline_sim.py"
    if not child_script.exists():
        raise RuntimeError(f"Child script not found: {child_script}")

    python = sys.executable
    total_start = time.time()
    batch_results: List[Dict] = []
    success_count = 0
    fail_count = 0

    for idx, issue in enumerate(issues, 1):
        print(f"\n{'=' * 60}")
        print(f"[{idx}/{len(issues)}] Processing {issue} ...")
        print(f"{'=' * 60}")

        execution = execute_batch_issue(
            issue=issue,
            batch_dir=batch_dir,
            python_exe=python,
            child_script=child_script,
            child_argv=child_argv,
        )

        if execution.stdout:
            print(execution.stdout, end="")
        if execution.stderr:
            print(execution.stderr, end="", file=sys.stderr)

        if execution.returncode == 0:
            success_count += 1
            if execution.data:
                batch_results.append(execution.data)
            elif execution.json_error:
                print(f"[WARN] Failed to parse JSON for {issue}: {execution.json_error}")
        else:
            fail_count += 1
            err_msg = (
                f"[FAIL] {issue} exited with code {execution.returncode} "
                f"({_format_duration(execution.elapsed_sec)})"
            )
            print(err_msg, file=sys.stderr)
            runtime_hint = build_runtime_hint(f"{execution.stderr}\n{execution.stdout}")
            if runtime_hint and runtime_hint not in execution.stderr and runtime_hint not in execution.stdout:
                print(runtime_hint, file=sys.stderr)
            if not continue_on_error:
                raise RuntimeError(err_msg)
            print(f"[CONTINUE] Skipping {issue} per --continue-on-error")

    total_elapsed = time.time() - total_start

    report = _build_consolidated_report(
        batch_results,
        batch_dir,
        stamp,
        total_elapsed,
        issue_count=len(issues),
        success_count=success_count,
        fail_count=fail_count,
    )
    report_path = batch_dir / f"batch_{stamp}_consolidated.md"
    report_path.write_text(report, encoding="utf-8")

    print(f"\n{'=' * 60}")
    print(f"Batch complete ({_format_duration(total_elapsed)})")
    print(f"  Success: {success_count}/{len(issues)}")
    print(f"  Failed:  {fail_count}/{len(issues)}")
    print(f"  Batch Dir: {batch_dir}")
    print(f"  Report:   {report_path}")

    sys.exit(1 if fail_count > 0 else 0)


if __name__ == "__main__":
    main()

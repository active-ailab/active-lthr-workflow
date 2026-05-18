from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from shared.batch_runner import (  # noqa: E402
    build_issue_command,
    execute_batch_issue,
    load_issues,
)


class _Completed:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class BatchRunnerTests(unittest.TestCase):
    def test_load_issues_reads_file_and_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            issues_file = Path(tmp) / "issues.txt"
            issues_file.write_text("# comment\nMOD-2\nMOD-1\nMOD-2\n", encoding="utf-8")
            issues = load_issues(["MOD-1"], str(issues_file))
            self.assertEqual(issues, ["MOD-1", "MOD-2"])

    def test_build_issue_command_appends_work_dir(self) -> None:
        cmd = build_issue_command(
            python_exe="python3",
            child_script=Path("/tmp/run_lthr_offline_sim.py"),
            issue="MOD-1",
            child_argv=["--runs", "3"],
            issue_dir=Path("/tmp/work/MOD-1"),
        )
        self.assertEqual(
            cmd,
            [
                "python3",
                "/tmp/run_lthr_offline_sim.py",
                "MOD-1",
                "--runs",
                "3",
                "--work-dir",
                "/tmp/work/MOD-1",
            ],
        )

    def test_execute_batch_issue_reads_latest_json_on_success(self) -> None:
        calls = []

        def _run(cmd, capture_output, text):
            calls.append((cmd, capture_output, text))
            return _Completed(returncode=0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            batch_dir = Path(tmp)
            issue_dir = batch_dir / "MOD-1" / "artifacts"
            issue_dir.mkdir(parents=True, exist_ok=True)
            (issue_dir / "lthr_offline_sim_20260515_120000.json").write_text(
                '{"status": "completed"}',
                encoding="utf-8",
            )

            result = execute_batch_issue(
                issue="MOD-1",
                batch_dir=batch_dir,
                python_exe="python3",
                child_script=Path("/tmp/run_lthr_offline_sim.py"),
                child_argv=["--runs", "1"],
                run_subprocess_fn=_run,
                time_fn=lambda: 10.0,
            )

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.data, {"status": "completed"})
            self.assertEqual(result.stdout, "ok")
            self.assertTrue(str(result.json_path).endswith(".json"))
            self.assertEqual(len(calls), 1)

    def test_execute_batch_issue_captures_json_parse_error(self) -> None:
        def _run(cmd, capture_output, text):
            return _Completed(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            batch_dir = Path(tmp)
            issue_dir = batch_dir / "MOD-2" / "artifacts"
            issue_dir.mkdir(parents=True, exist_ok=True)
            (issue_dir / "lthr_offline_sim_20260515_120000.json").write_text(
                "{bad json",
                encoding="utf-8",
            )

            result = execute_batch_issue(
                issue="MOD-2",
                batch_dir=batch_dir,
                python_exe="python3",
                child_script=Path("/tmp/run_lthr_offline_sim.py"),
                child_argv=[],
                run_subprocess_fn=_run,
                time_fn=lambda: 5.0,
            )

            self.assertEqual(result.returncode, 0)
            self.assertIsNone(result.data)
            self.assertTrue(result.json_error)

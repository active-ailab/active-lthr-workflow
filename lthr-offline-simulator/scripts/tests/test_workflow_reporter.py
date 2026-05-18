from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from shared.workflow_reporter import write_artifacts, write_json_summary  # noqa: E402


class WorkflowReporterTests(unittest.TestCase):
    def test_write_artifacts_writes_markdown_report(self) -> None:
        sample_summary = {
            "sample_csv": "gomore_data_20260515_120000.csv",
            "gomore_root": "/mnt/f/sport/gomore",
            "results": [
                {
                    "run": 1,
                    "status": "ok",
                    "ltSpeed": 1.23,
                    "lh": 150,
                    "mh": 160,
                    "guideResult": 1,
                }
            ],
            "stability": "stable",
            "cleanup": {
                "deleted_csvs": [],
                "deleted_state_files": [],
                "copied_sample": ["/mnt/f/sport/gomore/data_sample/gomore_data_20260515_120000.csv"],
            },
            "deploy_attempt": 1,
            "deployment": {
                "latest_csv": "/mnt/f/sport/gomore/data_sample/gomore_data_20260515_120000.csv",
                "csv_count": 1,
                "state_file_count": 0,
            },
            "trend": {"valid_runs": 1},
        }

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "run.log"
            json_path = tmp_path / "run.json"
            report_path = tmp_path / "run.md"

            result = write_artifacts(
                log_path=log_path,
                json_path=json_path,
                report_path=report_path,
                raw_log="raw output\n",
                summary={"status": "completed"},
                report_extra={
                    "issue_meta": {
                        "key": "ITS-1",
                        "link": "https://jira.example/ITS-1",
                        "title": "offline sim",
                    },
                    "sample_summaries": [sample_summary],
                    "raw_log_name": "run.log",
                    "signature": "",
                },
            )

            self.assertEqual(result["report_path"], str(report_path))
            self.assertIn("ITS-1", report_path.read_text(encoding="utf-8"))
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["artifacts"]["report_path"], str(report_path))

    def test_write_artifacts_supports_custom_report_artifact_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "run.log"
            json_path = tmp_path / "run.json"
            summary_path = tmp_path / "summary.md"

            result = write_artifacts(
                log_path=log_path,
                json_path=json_path,
                report_path=summary_path,
                report_artifact_key="summary_path",
                raw_log="uart log\n",
                summary={"status": "completed"},
                report_extra={"report_text": "# UART Summary\n"},
            )

            self.assertEqual(result["summary_path"], str(summary_path))
            self.assertEqual(result["report_path"], str(summary_path))
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["artifacts"]["summary_path"], str(summary_path))

    def test_write_json_summary_writes_json_and_artifact_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            json_path = tmp_path / "summary.json"

            result = write_json_summary(
                json_path=json_path,
                summary={"status": "completed"},
            )

            self.assertEqual(result["json_path"], str(json_path))
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["artifacts"]["json_path"], str(json_path))

    def test_write_json_summary_supports_custom_artifact_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            json_path = tmp_path / "summary.json"

            result = write_json_summary(
                json_path=json_path,
                json_artifact_key="summary_json_path",
                summary={"status": "completed"},
            )

            self.assertEqual(result["summary_json_path"], str(json_path))
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["artifacts"]["summary_json_path"], str(json_path))

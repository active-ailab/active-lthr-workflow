from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from shared.report_generator import (  # noqa: E402
    build_comment,
    build_consolidated_report,
    build_report_markdown,
    format_deployment_summary,
    format_duration,
    format_windows_copy_summary,
)


class ReportGeneratorTests(unittest.TestCase):
    def test_format_helpers_render_expected_fields(self) -> None:
        deployment = format_deployment_summary(
            {"latest_csv": "/tmp/a.csv", "csv_count": 1, "copied_size": 128, "state_file_count": 0}
        )
        windows = format_windows_copy_summary(
            {"csv_count": 2, "meta_count": 1, "state_file_count": 0, "data_sample": "X:/sport/gomore/data_sample"}
        )
        self.assertIn("latest_csv=/tmp/a.csv", deployment)
        self.assertIn("meta_count=1", windows)
        self.assertEqual(format_duration(3661), "1h01m01s")

    def test_build_comment_includes_sample_and_report_includes_issue(self) -> None:
        sample = {
            "sample_csv": "gomore_data_1.csv",
            "gomore_root": "/mnt/test/gomore",
            "results": [{"run": 1, "status": "ok", "ltSpeed": 1.23, "lh": 150, "mh": 160, "guideResult": 1}],
            "stability": "stable",
            "cleanup": {"deleted_csvs": [], "deleted_state_files": [], "copied_sample": ["/tmp/gomore_data_1.csv"]},
            "deploy_attempt": 1,
            "deployment": {"latest_csv": "/tmp/gomore_data_1.csv", "csv_count": 1, "state_file_count": 0},
            "trend": {"valid_runs": 1},
        }
        issue = {"key": "MOD-1", "link": "https://jira.example/MOD-1", "title": "offline sim"}

        comment = build_comment(issue, [sample], ["report.md"], "gpt-test")
        report = build_report_markdown(issue, [sample], "raw.log", "gpt-test")

        self.assertIn("乳酸阈离线仿真结果", comment)
        self.assertIn("gomore_data_1.csv", comment)
        self.assertIn("# LTHR Offline Simulation Report", report)
        self.assertIn("Issue: MOD-1", report)

    def test_build_consolidated_report_links_issue_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            batch_dir = Path(tmp)
            artifact_dir = batch_dir / "MOD-1" / "artifacts"
            artifact_dir.mkdir(parents=True)
            (artifact_dir / "lthr_offline_sim_20260514.md").write_text("report", encoding="utf-8")

            batch_results = [
                {
                    "issue": {"key": "MOD-1", "title": "offline sim"},
                    "samples": [
                        {
                            "sample_csv": "gomore_data_1.csv",
                            "results": [{"run": 1, "status": "ok", "ltSpeed": 1.23, "lh": 150, "mh": 160, "guideResult": 1}],
                            "stability": "stable",
                            "trend": {
                                "valid_runs": 2,
                                "ltSpeed": {"mean": 1.2, "std": 0.1, "trend": "持平", "slope": 0.0},
                                "mh": {"mean": 160.0, "trend": "持平", "slope": 0.0},
                            },
                        }
                    ],
                }
            ]
            report = build_consolidated_report(
                batch_results=batch_results,
                batch_dir=batch_dir,
                stamp="20260514_120000",
                total_duration=61,
                issue_count=1,
                success_count=1,
                fail_count=0,
            )
            self.assertIn("# Batch LTHR Offline Simulation Report", report)
            self.assertIn("MOD-1", report)
            self.assertIn("完整报告", report)

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from shared.img_extract_support import (  # noqa: E402
    finalize_img_extract_summary,
    init_img_extract_summary,
    resolve_default_output_dir,
)


class ImgExtractSupportTests(unittest.TestCase):
    def test_resolve_default_output_dir_uses_mode_prefix(self) -> None:
        path = resolve_default_output_dir("log_parse")
        self.assertIn("lthr_img_extract", str(path))
        self.assertIn("log_parse_", path.name)

    def test_init_img_extract_summary_seeds_expected_defaults(self) -> None:
        summary = init_img_extract_summary(
            mode="local_log_parse",
            source_kind="local_log_parse",
            source_mode="local_log_parse",
            status="completed",
            source_extra={"log_file": "/tmp/a.log"},
        )
        self.assertEqual(summary["workflow"], "img_extract")
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["mode"], "local_log_parse")
        self.assertEqual(summary["source"]["log_file"], "/tmp/a.log")
        self.assertEqual(summary["log_files_copied"], [])
        self.assertEqual(summary["summary_json_path"], "")

    def test_finalize_img_extract_summary_updates_metrics(self) -> None:
        summary = init_img_extract_summary(
            mode="device_img_extract",
            source_kind="device_img_extract",
            source_mode="device_img_extract",
        )
        finalize_img_extract_summary(
            summary,
            batches=[
                {"total": 3, "passed": 2, "failed": 1},
                {"total": 2, "passed": 2, "failed": 0},
            ],
            log_files_copied=["/tmp/a.log", "/tmp/b.log"],
            log_files_deleted=4,
            status="completed",
        )
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["log_files_deleted"], 4)
        self.assertEqual(summary["metrics"]["batch_count"], 2)
        self.assertEqual(summary["metrics"]["log_file_count"], 2)
        self.assertEqual(summary["metrics"]["total_samples"], 5)
        self.assertEqual(summary["metrics"]["passed"], 4)
        self.assertEqual(summary["metrics"]["failed"], 1)

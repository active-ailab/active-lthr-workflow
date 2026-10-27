from __future__ import annotations

import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from shared import multi_sim_support  # noqa: E402
from shared.multi_sim_support import (  # noqa: E402
    collect_data_sample_csvs,
    init_img_only_summary,
    init_multi_sample_summary,
    print_data_sample_inventory,
    resolve_data_sample_dir,
    run_img_extract_phase,
)


class MultiSimSupportTests(unittest.TestCase):
    def test_resolve_data_sample_dir_uses_explicit_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data_sample"
            data_dir.mkdir(parents=True, exist_ok=True)
            resolved = resolve_data_sample_dir(str(data_dir), Path("/tmp/ignored.py"))
            self.assertEqual(resolved, data_dir.resolve())

    def test_resolve_data_sample_dir_requires_explicit_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "--data-dir"):
            resolve_data_sample_dir("", Path("/tmp/run_device_multi_sim.py"))

    def test_collect_and_print_data_sample_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data_sample"
            nested = data_dir / "nested"
            nested.mkdir(parents=True, exist_ok=True)
            (data_dir / "gomore_data_20260515_120000.csv").write_text("a", encoding="utf-8")
            (nested / "gomore_data_20260516_120000.csv").write_text("b", encoding="utf-8")
            (data_dir / "ignored.csv").write_text("ignored", encoding="utf-8")
            csv_files = collect_data_sample_csvs(data_dir)
            lines = []
            print_data_sample_inventory(data_dir, csv_files, print_fn=lines.append, preview_limit=1)
            self.assertEqual([path.name for path in csv_files], [
                "gomore_data_20260515_120000.csv",
                "gomore_data_20260516_120000.csv",
            ])
            self.assertIn("[信息] 待拷贝 CSV: 2 个", lines)
            self.assertIn("       gomore_data_20260515_120000.csv", lines)
            self.assertIn("       ... 及其他 1 个", lines)

    def test_init_summary_helpers_seed_expected_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data_sample"
            data_dir.mkdir(parents=True, exist_ok=True)
            img_only = init_img_only_summary("20260515_120000")
            multi = init_multi_sample_summary("20260515_120001", data_dir, 7)
            self.assertEqual(img_only["mode"], "img_only")
            self.assertEqual(img_only["timestamp"], "20260515_120000")
            self.assertEqual(multi["mode"], "device_multi_sim")
            self.assertEqual(multi["csv_count"], 7)
            self.assertEqual(multi["metrics"]["input_csv_count"], 7)

    def test_run_img_extract_phase_marks_module_unavailable(self) -> None:
        messages = []
        summary = {}
        with mock.patch.object(multi_sim_support, "_load_img_extract_pipeline", return_value=None):
            result = run_img_extract_phase(
                wlctl_path=Path("/tmp/wlctl"),
                mgr_port="COM37",
                output_dir=Path("/tmp/out"),
                mount_wait_sec=30,
                keep_device_logs=False,
                summary_info=summary,
                pipeline_fn=None,
                print_fn=messages.append,
            )
        self.assertIs(result, summary)
        self.assertEqual(summary["img_extract_status"], "module_unavailable")
        self.assertTrue(any("模块不可用" in line for line in messages))

    def test_run_img_extract_phase_merges_success_metrics(self) -> None:
        summary = {}

        def _pipeline(**kwargs):
            return {
                "status": "completed",
                "batches": [
                    {"total": 3, "passed": 2, "failed": 1},
                    {"total": 2, "passed": 2, "failed": 0},
                ],
                "log_files_copied": ["a.log", "b.log"],
                "report_path": "/tmp/report.md",
                "json_path": "/tmp/img.json",
                "summary_json_path": "/tmp/summary.json",
                "batches_json_path": "/tmp/batches.json",
            }

        run_img_extract_phase(
            wlctl_path=Path("/tmp/wlctl"),
            mgr_port="COM37",
            output_dir=Path("/tmp/out"),
            mount_wait_sec=30,
            keep_device_logs=True,
            summary_info=summary,
            pipeline_fn=_pipeline,
        )
        self.assertEqual(summary["img_extract_status"], "completed")
        self.assertEqual(summary["img_batches"], 2)
        self.assertEqual(summary["img_log_files"], 2)
        self.assertEqual(summary["img_total_samples"], 5)
        self.assertEqual(summary["img_passed"], 4)
        self.assertEqual(summary["img_failed"], 1)
        self.assertEqual(summary["artifacts"]["img_report_path"], "/tmp/report.md")
        self.assertEqual(summary["metrics"]["img_batches"], 2)

    def test_run_img_extract_phase_records_prefixed_runtime_hint_on_failure(self) -> None:
        errors = []
        summary = {}

        def _pipeline(**kwargs):
            raise RuntimeError("img explode")

        run_img_extract_phase(
            wlctl_path=Path("/tmp/wlctl"),
            mgr_port="COM37",
            output_dir=Path("/tmp/out"),
            mount_wait_sec=30,
            keep_device_logs=False,
            summary_info=summary,
            pipeline_fn=_pipeline,
            error_print_fn=errors.append,
        )
        self.assertTrue(str(summary["img_extract_status"]).startswith("failed:"))
        self.assertIn("img_runtime_hint", summary)
        self.assertTrue(any("IMG_ERROR" in line for line in errors))

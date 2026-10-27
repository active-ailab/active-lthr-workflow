from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from shared import data_sample_ops  # noqa: E402


class DataSampleOpsTests(unittest.TestCase):
    def test_clean_and_copy_single_sample_preserves_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_root = tmp_path
            gomore_root = data_root / "sport" / "gomore"
            (gomore_root / "data_sample").mkdir(parents=True)
            (gomore_root / "data_sample" / "old.csv").write_text("old\n", encoding="utf-8")
            (gomore_root / "gomore_his.data").write_text("hist\n", encoding="utf-8")
            (gomore_root / "lactateData.data").write_text("lactate\n", encoding="utf-8")
            sample_csv = tmp_path / "custom.csv"
            sample_csv.write_text("body\n", encoding="utf-8")

            old_timestamp = data_sample_ops.format_timestamp
            try:
                data_sample_ops.format_timestamp = lambda: "20260515_120000"
                cleanup = data_sample_ops.clean_and_copy_single_sample(data_root, sample_csv)
            finally:
                data_sample_ops.format_timestamp = old_timestamp

            copied_path = Path(cleanup["copied_sample"][0])
            self.assertEqual(copied_path.name, "gomore_data_20260515_120000.csv")
            self.assertTrue(copied_path.exists())
            self.assertTrue((gomore_root / "data_sample" / "old.csv").exists())
            self.assertTrue((gomore_root / "gomore_his.data").exists())
            self.assertTrue((gomore_root / "lactateData.data").exists())

    def test_verify_single_sample_deployment_accepts_clean_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            copied_sample = data_root / "sport" / "gomore" / "data_sample" / "gomore_data_20260515_120000.csv"
            copied_sample.parent.mkdir(parents=True)
            copied_sample.write_text("body\n", encoding="utf-8")

            payload = data_sample_ops.verify_single_sample_deployment(
                data_root,
                copied_sample,
                source_size=copied_sample.stat().st_size,
            )
            self.assertEqual(payload["csv_count"], 1)
            self.assertEqual(payload["latest_csv"], str(copied_sample))
            self.assertEqual(payload["state_file_count"], 0)

    def test_verify_single_sample_deployment_accepts_data_root_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp)
            copied_sample = data_root / "sport" / "gomore" / "data_sample" / "gomore_data_20260515_120000.csv"
            copied_sample.parent.mkdir(parents=True)
            copied_sample.write_text("body\n", encoding="utf-8")

            payload = data_sample_ops.verify_single_sample_deployment(
                data_root,
                copied_sample,
                source_size=copied_sample.stat().st_size,
            )
            self.assertEqual(payload["csv_count"], 1)
            self.assertEqual(payload["latest_csv"], str(copied_sample))
            self.assertEqual(payload["state_file_count"], 0)

    def test_deploy_data_sample_tree_copies_nested_csvs_and_preserves_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_root = tmp_path
            gomore_root = data_root / "sport" / "gomore"
            (gomore_root / "data_sample" / "stale").mkdir(parents=True)
            (gomore_root / "data_sample" / "stale" / "old.csv").write_text("old\n", encoding="utf-8")
            (gomore_root / "gomore_his.data").write_text("hist\n", encoding="utf-8")
            (gomore_root / "lactateData.data").write_text("lactate\n", encoding="utf-8")

            local_data_sample = tmp_path / "local_data_sample"
            (local_data_sample / "nested").mkdir(parents=True)
            (local_data_sample / "nested" / "gomore_data_20260515_120000.csv").write_text("csv\n", encoding="utf-8")
            (local_data_sample / "base_case_updates_meta.json").write_text("meta\n", encoding="utf-8")

            payload = data_sample_ops.deploy_data_sample_tree(data_root, local_data_sample)

            self.assertEqual(payload["csv_count"], 1)
            self.assertEqual(payload["meta_count"], 1)
            self.assertEqual(payload["state_file_count"], 2)
            self.assertIn("nested/gomore_data_20260515_120000.csv", payload["copied_csv_relative_paths"])
            self.assertTrue((gomore_root / "data_sample" / "stale" / "old.csv").exists())

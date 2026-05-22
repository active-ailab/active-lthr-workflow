from __future__ import annotations

import json
import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import base_steps  # noqa: E402


class BaseStepsTests(unittest.TestCase):
    def test_merge_template_records_preserves_template_order_and_baselines(self) -> None:
        template_records = {
            "01lyh": {"样本": "01lyh", "Garmin_LTHR": 165, "DUT_LTHR": 170},
            "02lzh": {"样本": "02lzh", "Garmin_LTHR": 160, "DUT_LTHR": 161},
        }
        new_records = {
            "1lyh": {"仿真1_LTHR": 170, "仿真2_LTHR": 171},
            "02lzh": {"仿真1_LTHR": 161, "仿真2_LTHR": 161},
        }

        merged = base_steps._merge_template_records(template_records, new_records)

        self.assertEqual(list(merged.keys()), ["01lyh", "02lzh"])
        self.assertEqual(merged["01lyh"]["样本"], "01lyh")
        self.assertEqual(merged["01lyh"]["Garmin_LTHR"], 165)
        self.assertEqual(merged["01lyh"]["DUT_LTHR"], 170)
        self.assertEqual(merged["01lyh"]["仿真1_LTHR"], 170)
        self.assertEqual(merged["01lyh"]["仿真2_LTHR"], 171)
        self.assertEqual(merged["02lzh"]["Garmin_LTHR"], 160)
        self.assertEqual(merged["02lzh"]["仿真1_LTHR"], 161)

    def test_filter_record_fields_drops_unknown_status_fields(self) -> None:
        record = {
            "样本": "12rsj",
            "仿真1_LTHR": 152,
            "仿真3_状态": "未产出有效仿真结果",
            "仿真2_状态": "ok",
        }

        filtered, skipped = base_steps._filter_record_fields(record, {"样本", "仿真1_LTHR"})

        self.assertEqual(filtered, {"样本": "12rsj", "仿真1_LTHR": 152})
        self.assertEqual(skipped, {"仿真2_状态", "仿真3_状态"})

    def test_resolve_report_source_refreshes_stale_base_data_with_new_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            base_data_path = tmp_path / "base_data_1.0.16.1.json"
            samples_path = tmp_path / "samples_1.0.16.1.json"

            base_data_path.write_text(
                json.dumps(
                    {
                        "13nwr": {
                            "样本": "13nwr",
                            "Garmin_LTP": "4'33",
                            "仿真1_LTP": "14'20",
                            "仿真1_LTP（S）": 860,
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            samples_path.write_text(
                json.dumps(
                    {
                        "13nwr": {
                            "仿真1_LTP": "4'11",
                            "仿真1_LTP（S）": 251,
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            args = SimpleNamespace(
                json_file=str(samples_path),
                sdk_ver="1.0.16.1",
                output_dir=str(tmp_path),
            )

            resolved = base_steps._resolve_report_source(args, dry_run=False)

            self.assertEqual(resolved, str(base_data_path))
            merged = json.loads(base_data_path.read_text(encoding="utf-8"))
            self.assertEqual(merged["13nwr"]["Garmin_LTP"], "4'33")
            self.assertEqual(merged["13nwr"]["仿真1_LTP"], "4'11")
            self.assertEqual(merged["13nwr"]["仿真1_LTP（S）"], 251)

    def test_resolve_report_source_auto_pulls_base_data_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            samples_path = tmp_path / "samples_1.0.17.2.json"

            samples_path.write_text(
                json.dumps(
                    {
                        "13nwr": {
                            "仿真1_LTP": "4'11",
                            "仿真1_LTP（S）": 251,
                            "仿真1_LTHR": 168,
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            args = SimpleNamespace(
                json_file=str(samples_path),
                sdk_ver="1.0.17.2",
                output_dir=str(tmp_path),
                base_token="base-token",
                base_template="",
                table_id="table-id",
            )

            fake_base_result = {
                "ok": True,
                "data": {
                    "dummy": True,
                },
            }
            fake_base_records = {
                "13nwr": {
                    "样本": "13nwr",
                    "Garmin_LTP": "4'33",
                    "DUT_LTP": "4'15",
                    "Garmin_LTHR": 168,
                    "DUT_LTHR": 167,
                }
            }

            with mock.patch.object(base_steps, "_run_record_list_with_retry", return_value=fake_base_result), \
                 mock.patch.object(base_steps, "_extract_record_map", return_value=fake_base_records):
                resolved = base_steps._resolve_report_source(args, dry_run=False)

            base_data_path = tmp_path / "base_data_1.0.17.2.json"
            self.assertEqual(resolved, str(base_data_path))
            self.assertTrue(base_data_path.exists())

            merged = json.loads(base_data_path.read_text(encoding="utf-8"))
            self.assertEqual(merged["13nwr"]["Garmin_LTP"], "4'33")
            self.assertEqual(merged["13nwr"]["DUT_LTP"], "4'15")
            self.assertEqual(merged["13nwr"]["仿真1_LTP"], "4'11")
            self.assertEqual(merged["13nwr"]["仿真1_LTHR"], 168)

    def test_effective_base_url_prefers_copied_base_token(self) -> None:
        args = SimpleNamespace(
            _copied_base_token="HdjmbDFgAa5U2EsLx7TcnSApnhq",
            base_url="https://zepp.feishu.cn/base/TZGZbPQ7EaI5EasOyEDc1QZtnFi",
            base_template="TZGZbPQ7EaI5EasOyEDc1QZtnFi",
            base_token="TZGZbPQ7EaI5EasOyEDc1QZtnFi",
        )

        resolved = base_steps._effective_base_url(args)

        self.assertEqual(resolved, "https://zepp.feishu.cn/base/HdjmbDFgAa5U2EsLx7TcnSApnhq")

    def test_rewrite_report_base_url_replaces_template_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.md"
            report_path.write_text(
                "\n".join([
                    "# title",
                    "## 5. 数据表（Base 快照）",
                    "",
                    "> 完整数据见飞书 Base: https://zepp.feishu.cn/base/TZGZbPQ7EaI5EasOyEDc1QZtnFi",
                    "",
                    "tail",
                ]),
                encoding="utf-8",
            )

            changed = base_steps._rewrite_report_base_url(
                str(report_path),
                "https://zepp.feishu.cn/base/HdjmbDFgAa5U2EsLx7TcnSApnhq",
            )

            self.assertTrue(changed)
            content = report_path.read_text(encoding="utf-8")
            self.assertIn("https://zepp.feishu.cn/base/HdjmbDFgAa5U2EsLx7TcnSApnhq", content)
            self.assertNotIn("https://zepp.feishu.cn/base/TZGZbPQ7EaI5EasOyEDc1QZtnFi", content)


if __name__ == "__main__":
    unittest.main()
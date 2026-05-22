from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _report_utils  # noqa: E402
import base_steps  # noqa: E402
import generate_jira_report as jira_report_gen  # noqa: E402
import jira_steps  # noqa: E402
from _report_utils import _parse_markdown_table_records, _payload_to_records, pace_format, parse_pace_to_seconds, run_lark  # noqa: E402
from generate_garmin_dut_compare_report import generate_report, load_records  # noqa: E402
from generate_sim_report import load_from_json  # noqa: E402
import run_pipeline  # noqa: E402
from types import SimpleNamespace


class _FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class ReportUtilsTests(unittest.TestCase):
    def test_parse_pace_helpers(self) -> None:
        self.assertEqual(parse_pace_to_seconds("5'55"), 355)
        self.assertIsNone(parse_pace_to_seconds("bad"))
        self.assertEqual(pace_format(355), "5'55")
        self.assertEqual(pace_format(None), "N/A")

    def test_payload_to_records_normalizes_empty_values(self) -> None:
        payload = {
            "fields": ["样本", "值", "备注"],
            "data": [["S1", 123, ""], ["S2", None, "ok"]],
        }
        records = _payload_to_records(payload)
        self.assertEqual(records["S1"]["值"], 123)
        self.assertIsNone(records["S1"]["备注"])
        self.assertIsNone(records["S2"]["值"])

    def test_parse_markdown_table_records_handles_base_output(self) -> None:
        raw = """`_record_id` is metadata for record operations, not a table field.

| _record_id | 样本 | Garmin_LTHR | DUT_LTHR | 仿真1_LTHR |
| --- | --- | --- | --- | --- |
| rec1 | 01lyh | 165 | 170 | 170 |
| rec2 | 02lzh | 160 | 161 | 161 |
"""
        records = _parse_markdown_table_records(raw)
        self.assertEqual(list(records.keys()), ["01lyh", "02lzh"])
        self.assertEqual(records["01lyh"]["_record_id"], "rec1")
        self.assertEqual(records["01lyh"]["Garmin_LTHR"], 165)
        self.assertEqual(records["02lzh"]["DUT_LTHR"], 161)

    def test_run_lark_appends_identity_and_json_flags(self) -> None:
        old_run = _report_utils.subprocess.run
        calls = []

        def fake_run(cmd, capture_output=True, text=True, timeout=120, **kwargs):
            calls.append(cmd)
            return _FakeResult(stdout=json.dumps({"ok": True, "data": {"x": 1}}))

        try:
            _report_utils.subprocess.run = fake_run
            result = run_lark(["base", "+record-list", "--base-token", "B"], identity="user")
        finally:
            _report_utils.subprocess.run = old_run

        self.assertTrue(result["ok"])
        self.assertIn("--as", calls[0])
        self.assertNotIn("--format", calls[0])  # 不再默认添加 --format

    def test_run_lark_force_json_adds_format_flag(self) -> None:
        old_run = _report_utils.subprocess.run
        calls = []

        def fake_run(cmd, capture_output=True, text=True, timeout=120, **kwargs):
            calls.append(cmd)
            return _FakeResult(stdout=json.dumps({"ok": True, "data": {"x": 1}}))

        try:
            _report_utils.subprocess.run = fake_run
            result = run_lark(["base", "+record-list", "--base-token", "B"], identity="user", force_json=True)
        finally:
            _report_utils.subprocess.run = old_run

        self.assertTrue(result["ok"])
        self.assertIn("--format", calls[0])

    def test_load_from_json_uses_shared_payload_conversion(self) -> None:
        payload = {
            "data": {
                "fields": ["样本", "仿真1_LTP", "备注"],
                "data": [["S1", "5'55", ""], ["S2", "", "ok"]],
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            records = load_from_json(str(path))
        self.assertEqual(records["S1"]["仿真1_LTP"], "5'55")
        self.assertIsNone(records["S1"]["备注"])
        self.assertIsNone(records["S2"]["仿真1_LTP"])

    def test_garmin_report_load_records_handles_payload(self) -> None:
        payload = {
            "data": {
                "fields": ["样本", "Garmin_LTHR", "DUT_LTHR", "仿真1_LTHR"],
                "data": [["S1", 170, 171, 169]],
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "garmin.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            records = load_records(str(path))
        self.assertEqual(records[0]["样本"], "S1")
        self.assertEqual(records[0]["DUT_LTHR"], 171)

    def test_garmin_report_generation_includes_summary(self) -> None:
        records = [{
            "样本": "S1",
            "年龄": 30,
            "性别": "M",
            "身高": 175,
            "体重": 68,
            "静息": 50,
            "220-age": 190,
            "样本分组": "A",
            "Garmin_LTHR": 170,
            "DUT_LTHR": 171,
            "仿真1_LTHR": 169,
            "仿真2_LTHR": 170,
            "仿真3_LTHR": 170,
            "Garmin_LTP": "5'40",
            "DUT_LTP": "5'42",
            "仿真1_LTP": "5'41",
            "仿真2_LTP": "5'41",
            "仿真3_LTP": "5'42",
            "Garmin_LTP(S)": 340,
            "DUT_LTP（S）": 342,
            "仿真1_LTP（S）": 341,
            "仿真2_LTP（S）": 341,
            "仿真3_LTP（S）": 342,
            "Garmin_HRmax": 190,
            "DUT_HRmax": 189,
            "仿真1_MAXHR": 190,
            "仿真2_MAXHR": 190,
            "仿真3_MAXHR": 189,
        }]
        report = generate_report(records, sdk_version="1.2.3", report_date="2026-05-14", base_url="https://example.com/base")
        self.assertIn("SDK v1.2.3", report)
        self.assertIn("## 综合结论", report)
        self.assertIn("https://example.com/base", report)

    def test_run_pipeline_uses_skill_local_report_scripts(self) -> None:
        self.assertTrue(run_pipeline.SCRIPTS["sim_report"].endswith("generate_sim_report.py"))
        self.assertTrue(run_pipeline.SCRIPTS["garmin_report"].endswith("generate_garmin_dut_compare_report.py"))
        self.assertNotIn("/tools/", run_pipeline.SCRIPTS["sim_report"])
        self.assertNotIn("/tools/", run_pipeline.SCRIPTS["garmin_report"])

    def test_run_pipeline_parse_steps_supports_ranges(self) -> None:
        self.assertEqual(run_pipeline.parse_steps("1-3,5"), [1, 2, 3, 5])

    def test_step_handlers_are_split_across_modules(self) -> None:
        self.assertIn(4, base_steps.BASE_STEP_HANDLERS)
        self.assertIn(11, jira_steps.JIRA_STEP_HANDLERS)
        self.assertTrue(run_pipeline.STEP_HANDLERS[4].__module__.endswith("base_steps"))
        self.assertTrue(run_pipeline.STEP_HANDLERS[11].__module__.endswith("jira_steps"))

    def test_jira_report_uses_pace_seconds_for_baseline_speed(self) -> None:
        new_samples = {
            "MAT-13034/gomore_data_20260412_093657": {
                "年龄": 25,
                "性别": "男",
                "身高": 192,
                "体重": 76.0,
                "静息": 42,
                "仿真1_LTP": "3'38",
                "仿真1_LTP（S）": 218,
                "仿真1_LTHR": 170,
                "仿真1_MAXHR": 179,
                "仿真2_LTP": "3'39",
                "仿真2_LTP（S）": 219,
                "仿真2_LTHR": 170,
                "仿真2_MAXHR": 179,
                "仿真3_LTP": "3'40",
                "仿真3_LTP（S）": 220,
                "仿真3_LTHR": 170,
                "仿真3_MAXHR": 179,
            }
        }
        baseline = {
            "MAT-13034": [
                {
                    "file": "gomore_data_20260412_093657.csv",
                    "rows": 4465,
                    "ltSpeed": 15.412609,
                    "lh": 187.0,
                    "mh": 187.0,
                }
            ]
        }

        report = jira_report_gen.generate_report(new_samples, baseline, new_label="SDK 1.0.16.1", old_label="旧 SDK")

        self.assertIn("ltSpeed=15.4126 (3'54\"/km)", report)
        self.assertIn("| 第1轮 | 170 | 170 (↓-17 (-9.1%)) | 179 | 3'38 | 218.00 (", report)
        self.assertNotIn("989.00", report)

    def test_jira_step_13_refreshes_samples_from_log_when_no_explicit_json(self) -> None:
        old_run_cmd = jira_steps.run_cmd
        old_has_detected_case = jira_steps.has_detected_case
        commands = []

        def fake_run_cmd(cmd, dry_run=False, **kwargs):
            commands.append(cmd)
            out_path = cmd[cmd.index("-o") + 1]
            Path(out_path).write_text("{}", encoding="utf-8")

        try:
            jira_steps.run_cmd = fake_run_cmd
            jira_steps.has_detected_case = lambda args, case_name: case_name == "JIRA_CASE"
            with tempfile.TemporaryDirectory() as tmp:
                baseline_path = Path(tmp) / "jira_baseline_1.0.16.1.json"
                baseline_path.write_text("{}", encoding="utf-8")
                args = SimpleNamespace(
                    jira_json=None,
                    log_file="/tmp/fake.log",
                    sdk_ver="1.0.16.1",
                    output_dir=tmp,
                )

                resolved = jira_steps._resolve_jira_json_source(args, dry_run=False)

                self.assertEqual(resolved, str(Path(tmp) / "jira_samples_1.0.16.1.json"))
                self.assertEqual(commands[0][-2:], ["--case", "JIRA_CASE"])
        finally:
            jira_steps.run_cmd = old_run_cmd
            jira_steps.has_detected_case = old_has_detected_case

    def test_jira_report_explains_missing_comparison_when_all_rounds_invalid(self) -> None:
        new_samples = {
            "WIN-2552/gomore_data_20260416_120945": {
                "年龄": 46,
                "性别": "男",
                "身高": 178,
                "体重": 68.5,
                "静息": 60,
                "仿真1_LTHR": None,
                "仿真1_LTP": None,
                "仿真1_LTP（S）": None,
                "仿真1_MAXHR": None,
                "仿真1_状态": "invalid_result: ltSpeed=-1.000, lh=0, mh=0",
                "仿真2_LTHR": None,
                "仿真2_LTP": None,
                "仿真2_LTP（S）": None,
                "仿真2_MAXHR": None,
                "仿真2_状态": "invalid_result: ltSpeed=-1.000, lh=0, mh=0",
                "仿真3_LTHR": None,
                "仿真3_LTP": None,
                "仿真3_LTP（S）": None,
                "仿真3_MAXHR": None,
                "仿真3_状态": "invalid_result: ltSpeed=-1.000, lh=0, mh=0",
            }
        }
        baseline = {
            "WIN-2552": [
                {
                    "file": "gomore_data_20260416_120945.csv",
                    "rows": 4597,
                    "ltSpeed": 12.828135,
                    "lh": 148.0,
                    "mh": 152.0,
                }
            ]
        }

        report = jira_report_gen.generate_report(new_samples, baseline, new_label="SDK 1.0.16.1", old_label="旧 SDK")

        self.assertIn("#### SDK 仿真结果状态", report)
        self.assertIn("当前样本未产出有效仿真结果，因此无法生成仿真对比表", report)
        self.assertIn("第1轮: invalid_result: ltSpeed=-1.000, lh=0, mh=0", report)

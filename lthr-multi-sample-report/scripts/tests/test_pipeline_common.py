from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import _pipeline_common  # noqa: E402


class PipelineCommonTests(unittest.TestCase):
    def test_resolve_offline_summary_keeps_log_and_uses_stable_temp_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary_path = Path(tmp) / "summary.json"
            log_path = Path(tmp) / "run.log"
            payload = {
                "workflow": "multi_sample",
                "status": "ok",
                "artifacts": {"log_path": str(log_path)},
            }
            summary_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            args = types.SimpleNamespace(
                offline_summary=str(summary_path),
                output_dir=None,
                log_file=None,
            )
            _pipeline_common.resolve_offline_summary(args)

            self.assertEqual(args.log_file, str(log_path))
            output_dir = _pipeline_common.resolve_default_output_dir(args)
            self.assertEqual(output_dir, _pipeline_common.resolve_default_output_dir(args))
            self.assertIn("lthr-multi-sample-report", output_dir)
            self.assertIn(summary_path.stem, output_dir)

    def test_configure_case_routing_defaults_base_steps(self) -> None:
        old_detect = _pipeline_common.detect_log_cases
        try:
            _pipeline_common.detect_log_cases = lambda _: {"BASE_CASE"}
            args = types.SimpleNamespace(
                log_file="/tmp/fake.log",
                offline_summary="/tmp/summary.json",
                steps="1-10",
                detected_cases=None,
            )
            _pipeline_common.configure_case_routing(args, steps_explicit=False)
        finally:
            _pipeline_common.detect_log_cases = old_detect

        self.assertEqual(args.steps, "1-10")
        self.assertEqual(args.detected_cases, {"BASE_CASE"})

    def test_configure_case_routing_defaults_jira_steps(self) -> None:
        old_detect = _pipeline_common.detect_log_cases
        try:
            _pipeline_common.detect_log_cases = lambda _: {"JIRA_CASE"}
            args = types.SimpleNamespace(
                log_file="/tmp/fake.log",
                offline_summary="/tmp/summary.json",
                steps="1-10",
                detected_cases=None,
            )
            _pipeline_common.configure_case_routing(args, steps_explicit=False)
        finally:
            _pipeline_common.detect_log_cases = old_detect

        self.assertEqual(args.steps, "11-14")

    def test_configure_case_routing_defaults_all_steps_for_mixed_case(self) -> None:
        old_detect = _pipeline_common.detect_log_cases
        try:
            _pipeline_common.detect_log_cases = lambda _: {"BASE_CASE", "JIRA_CASE"}
            args = types.SimpleNamespace(
                log_file="/tmp/fake.log",
                offline_summary="/tmp/summary.json",
                steps="1-10",
                detected_cases=None,
            )
            _pipeline_common.configure_case_routing(args, steps_explicit=False)
        finally:
            _pipeline_common.detect_log_cases = old_detect

        self.assertEqual(args.steps, "1-14")

    def test_auto_detect_json_file_prefers_output_dir_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            target = out_dir / "samples_1.2.3.4.json"
            target.write_text("{}", encoding="utf-8")

            old_get_search_dirs = _pipeline_common.get_search_dirs
            try:
                _pipeline_common.get_search_dirs = lambda args: [str(out_dir)]
                args = types.SimpleNamespace(
                    json_file=None,
                    output_dir=str(out_dir),
                    sdk_ver="1.2.3.4",
                )
                _pipeline_common.auto_detect_json_file(args)
            finally:
                _pipeline_common.get_search_dirs = old_get_search_dirs

        self.assertEqual(args.json_file, str(target))

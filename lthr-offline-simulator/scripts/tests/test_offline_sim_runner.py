from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from shared import offline_sim_runner  # noqa: E402


class OfflineSimRunnerTests(unittest.TestCase):
    def test_parse_latest_result_returns_last_match(self) -> None:
        text = (
            "Gomore output ltSpeed=1.01 lh=150 mh=160 guideResult=1\n"
            "noise\n"
            "Gomore output ltSpeed=1.23 lh=151 mh=161 guideResult=2\n"
        )
        latest = offline_sim_runner.parse_latest_result(text)
        self.assertIsNotNone(latest)
        self.assertEqual(latest["ltSpeed"], 1.23)
        self.assertEqual(latest["guideResult"], 2)

    def test_run_single_simulation_collects_result_after_listen(self) -> None:
        listen_calls = []

        def fake_session(wlctl_path, cmd_text, uart_port, seconds, timeout, until_marker):
            self.assertEqual(cmd_text, "utest gomore_offline_csv_sim_tests")
            self.assertEqual(until_marker, offline_sim_runner.END_MARKER)
            listen_calls.append((seconds, timeout))
            return (
                "GoMore Offline CSV Simulation Start\n"
                "Gomore output ltSpeed=1.23 lh=150 mh=160 guideResult=1\n"
                "GoMore Offline CSV Simulation End\n"
            )

        result, raw_log = offline_sim_runner.run_single_simulation(
            Path("/fake/wlctl.sh"),
            run_index=1,
            uart_port="COM38",
            run_timeout_sec=5,
            run_uart_session_fn=fake_session,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["ltSpeed"], 1.23)
        self.assertIn("GoMore Offline CSV Simulation End", raw_log)
        self.assertTrue(listen_calls)

    def test_run_single_simulation_reports_missing_start_marker(self) -> None:
        def fake_session(wlctl_path, cmd_text, uart_port, seconds, timeout, until_marker):
            return "still booting...\n"

        result, _raw_log = offline_sim_runner.run_single_simulation(
            Path("/fake/wlctl.sh"),
            run_index=2,
            uart_port="COM38",
            run_timeout_sec=2,
            run_uart_session_fn=fake_session,
        )
        self.assertEqual(result["status"], "test_not_started")
        self.assertFalse(result["started"])

    def test_run_sample_flow_deploy_only_uses_shared_orchestration(self) -> None:
        seen: dict[str, object] = {}

        def fake_deploy_data_to_device(**kwargs):
            seen["deploy_kwargs"] = kwargs
            return "/mnt/f/sport/gomore", "DEPLOY LOG\n"

        def fake_ready_sequence(**kwargs):
            seen["ready_kwargs"] = kwargs
            return None

        old_deploy = offline_sim_runner.deploy_data_to_device
        old_ready = offline_sim_runner.ensure_device_ready_sequence
        try:
            offline_sim_runner.deploy_data_to_device = fake_deploy_data_to_device
            offline_sim_runner.ensure_device_ready_sequence = fake_ready_sequence

            with tempfile.TemporaryDirectory() as tmp:
                data_sample_dir = Path(tmp) / "data_sample"
                summary, raw_log = offline_sim_runner.run_sample_flow(
                    wlctl_path=Path("/fake/wlctl.sh"),
                    data_sample_dir=data_sample_dir,
                    sample_paths=["data_sample/gomore_data_20260515_120000.csv"],
                    mode="single_sample",
                    rounds=3,
                    run_id="sim_test",
                    output_dir=Path(tmp) / "output",
                    disk_root="",
                    mgr_port="COM37",
                    uart_port="COM38",
                    boot_timeout_sec=10,
                    mount_wait_sec=20,
                    run_timeout_sec=30,
                    listen_chunk_sec=5,
                    serial_settle_sec=2,
                    deploy_attempts=1,
                    deploy_only=True,
                    deploy_hook=lambda gomore_root: {"csv_count": 1},
                )
                config_text = (data_sample_dir / "sim_config.ini").read_text(encoding="utf-8")
        finally:
            offline_sim_runner.deploy_data_to_device = old_deploy
            offline_sim_runner.ensure_device_ready_sequence = old_ready

        self.assertEqual(summary["mode"], "single_sample")
        self.assertEqual(summary["status"], "deploy_only")
        self.assertIn("rounds=3", config_text)
        self.assertIn("DEPLOY LOG", raw_log)
        self.assertIn("DEVICE READY SEQUENCE", raw_log)
        self.assertEqual(seen["deploy_kwargs"]["mgr_port"], "COM37")

from __future__ import annotations

import sys
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

        def fake_send(wlctl_path, cmd_text, uart_port, read_ms, timeout):
            self.assertEqual(cmd_text, "utest gomore_offline_csv_tests")
            return "booting...\n"

        def fake_listen(wlctl_path, uart_port, seconds, timeout):
            listen_calls.append((seconds, timeout))
            return (
                "GoMore Offline CSV Test Start\n"
                "Gomore output ltSpeed=1.23 lh=150 mh=160 guideResult=1\n"
                "GoMore Offline CSV Test End\n"
            )

        result, raw_log = offline_sim_runner.run_single_simulation(
            Path("/fake/wlctl.sh"),
            run_index=1,
            uart_port="COM38",
            run_timeout_sec=5,
            listen_chunk_sec=2,
            send_uart_cmd_fn=fake_send,
            listen_uart_fn=fake_listen,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["ltSpeed"], 1.23)
        self.assertIn("GoMore Offline CSV Test End", raw_log)
        self.assertTrue(listen_calls)

    def test_run_single_simulation_raises_when_start_marker_missing(self) -> None:
        class FakeClock:
            def __init__(self) -> None:
                self.value = 0

            def time(self) -> int:
                self.value += 1
                return self.value

        clock = FakeClock()

        def fake_send(wlctl_path, cmd_text, uart_port, read_ms, timeout):
            return "booting...\n"

        def fake_listen(wlctl_path, uart_port, seconds, timeout):
            return "still booting...\n"

        with self.assertRaises(RuntimeError) as ctx:
            offline_sim_runner.run_single_simulation(
                Path("/fake/wlctl.sh"),
                run_index=2,
                uart_port="COM38",
                run_timeout_sec=2,
                listen_chunk_sec=1,
                send_uart_cmd_fn=fake_send,
                listen_uart_fn=fake_listen,
                time_fn=clock.time,
            )
        self.assertIn("did not detect", str(ctx.exception))

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

            summary, raw_log = offline_sim_runner.run_sample_flow(
                wlctl_path=Path("/fake/wlctl.sh"),
                sample_csv=Path("/tmp/sample.csv"),
                disk_root="",
                mgr_port="COM37",
                uart_port="COM38",
                runs=3,
                boot_timeout_sec=10,
                mount_wait_sec=20,
                run_timeout_sec=30,
                listen_chunk_sec=5,
                serial_settle_sec=2,
                deploy_attempts=1,
                deploy_only=True,
                deploy_hook=lambda gomore_root: {"csv_count": 1},
            )
        finally:
            offline_sim_runner.deploy_data_to_device = old_deploy
            offline_sim_runner.ensure_device_ready_sequence = old_ready

        self.assertEqual(summary["mode"], "deploy_only")
        self.assertEqual(summary["gomore_root"], "/mnt/f/sport/gomore")
        self.assertIn("DEPLOY LOG", raw_log)
        self.assertIn("DEVICE READY SEQUENCE", raw_log)
        self.assertEqual(seen["deploy_kwargs"]["mgr_port"], "COM37")

from __future__ import annotations

import itertools
import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from shared.multi_sim_runner import (  # noqa: E402
    collect_summary_counts,
    has_test_activity,
    init_progress,
    run_multi_sample_simulation,
    update_progress,
)


class MultiSimRunnerTests(unittest.TestCase):
    def test_has_test_activity_detects_progress_and_summary(self) -> None:
        self.assertTrue(has_test_activity("Sample 1/12, round 1/3, start=123"))
        self.assertTrue(has_test_activity("Simulation completed: run_id=sim_test status=success"))
        self.assertFalse(has_test_activity("plain shell prompt"))

    def test_update_progress_tracks_round_and_results(self) -> None:
        progress = init_progress()
        update_progress(
            progress,
            "Sample 3/10, round 2/4, start=123\n"
            "sample.2.status=success\nsample.3.status=input_error\n",
        )
        self.assertEqual(progress["current_sample"], 3)
        self.assertEqual(progress["total_samples"], 10)
        self.assertEqual(progress["current_round"], 2)
        self.assertEqual(progress["total_rounds"], 4)
        self.assertEqual(progress["passed"], 1)
        self.assertEqual(progress["failed"], 1)

    def test_collect_summary_counts_uses_sample_statuses(self) -> None:
        counts = collect_summary_counts(
            "sample.0.status=success\nsample.1.status=input_error\n",
            {"total_samples": 2, "passed": 0, "failed": 0},
        )
        self.assertEqual(counts, {"total_samples": 2, "passed": 1, "failed": 1})

    def test_run_multi_sample_simulation_reports_completed_status(self) -> None:
        progress_updates = []
        timeline = itertools.count()

        def _send(_wlctl_path, _cmd, _uart_port, _read_ms):
            return "GoMore Offline CSV Simulation Start\n"

        def _listen(_wlctl_path, _uart_port, _seconds):
            return (
                "Sample 1/2, round 1/3, start=123\n"
                "sample.0.status=success\nsample.1.status=success\n"
                "Simulation completed: run_id=sim_test status=success\n"
                "GoMore Offline CSV Simulation End\n"
            )

        summary, raw_log = run_multi_sample_simulation(
            wlctl_path=Path("/tmp/wlctl"),
            uart_port="COM36",
            run_timeout_sec=30,
            listen_chunk_sec=5,
            progress_callback=progress_updates.append,
            send_uart_cmd_fn=_send,
            listen_uart_fn=_listen,
            time_fn=lambda: next(timeline),
        )

        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["total_samples"], 2)
        self.assertEqual(summary["passed"], 2)
        self.assertEqual(summary["failed"], 0)
        self.assertTrue(summary["started"])
        self.assertTrue(summary["ended"])
        self.assertIn("===== RUN MULTI_TESTS =====", raw_log)
        self.assertEqual(progress_updates[-1]["current_sample"], 1)

    def test_run_multi_sample_simulation_times_out_without_end_marker(self) -> None:
        timeline = itertools.count()

        def _send(_wlctl_path, _cmd, _uart_port, _read_ms):
            return "GoMore Offline CSV Simulation Start\n"

        def _listen(_wlctl_path, _uart_port, _seconds):
            return "Sample 1/5, round 1/3, start=123\n"

        summary, _raw_log = run_multi_sample_simulation(
            wlctl_path=Path("/tmp/wlctl"),
            uart_port="COM36",
            run_timeout_sec=3,
            listen_chunk_sec=2,
            send_uart_cmd_fn=_send,
            listen_uart_fn=_listen,
            time_fn=lambda: next(timeline),
        )

        self.assertEqual(summary["status"], "test_timeout")
        self.assertTrue(summary["started"])
        self.assertFalse(summary["ended"])

    def test_run_multi_sample_simulation_reports_not_started(self) -> None:
        timeline = itertools.count()

        def _send(_wlctl_path, _cmd, _uart_port, _read_ms):
            return "shell prompt only\n"

        def _listen(_wlctl_path, _uart_port, _seconds):
            return "still idle\n"

        summary, _raw_log = run_multi_sample_simulation(
            wlctl_path=Path("/tmp/wlctl"),
            uart_port="COM36",
            run_timeout_sec=2,
            listen_chunk_sec=1,
            send_uart_cmd_fn=_send,
            listen_uart_fn=_listen,
            time_fn=lambda: next(timeline),
        )

        self.assertEqual(summary["status"], "test_not_started")
        self.assertFalse(summary["started"])

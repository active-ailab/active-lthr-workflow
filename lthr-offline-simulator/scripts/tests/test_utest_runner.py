from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from shared.utest_runner import prepare_device_for_long_log, wait_device_ready  # noqa: E402


class MockUartDevice:
    def __init__(self, probe_outputs=None, listen_outputs=None):
        self.probe_outputs = list(probe_outputs or [])
        self.listen_outputs = list(listen_outputs or [])
        self.send_calls = []
        self.listen_calls = []

    def send_uart(self, cmd: str, read_ms: int = 1500, timeout=None) -> str:
        self.send_calls.append((cmd, read_ms, timeout))
        if not self.probe_outputs:
            raise RuntimeError("No send_uart response configured")
        response = self.probe_outputs.pop(0)
        if isinstance(response, Exception):
            raise response
        return str(response)

    def listen_uart(self, seconds: int = 5, timeout=None) -> str:
        self.listen_calls.append((seconds, timeout))
        if not self.listen_outputs:
            return ""
        response = self.listen_outputs.pop(0)
        if isinstance(response, Exception):
            raise response
        return str(response)


class UtestRunnerTests(unittest.TestCase):
    def test_wait_device_ready_accepts_probe_success(self) -> None:
        dev = MockUartDevice(probe_outputs=["monkey status on\n"])
        output = wait_device_ready(dev, timeout_sec=2, listen_chunk_sec=1)
        self.assertIn("probe #1 succeeded", output)
        self.assertEqual(dev.send_calls[0][0], "monkey -g")

    def test_prepare_device_for_long_log_accepts_shell_prompt(self) -> None:
        dev = MockUartDevice(probe_outputs=["$ "])
        output = prepare_device_for_long_log(dev, timeout_sec=1)
        self.assertIn("keep-awake command returned shell prompt", output)
        self.assertEqual(dev.send_calls[0][0], "power -s on")

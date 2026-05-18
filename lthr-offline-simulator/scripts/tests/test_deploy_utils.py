from __future__ import annotations

import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from shared.deploy_utils import ensure_device_vbus_on, wait_for_disk_root  # noqa: E402


class MockMgrDevice:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def send_mgr(self, cmd: str, timeout: float = 12.0) -> str:
        self.calls.append((cmd, timeout))
        if not self.outputs:
            raise RuntimeError("No more responses configured")
        response = self.outputs.pop(0)
        if isinstance(response, Exception):
            raise response
        return str(response)


class DeployUtilsTests(unittest.TestCase):
    def test_ensure_device_vbus_on_turns_on_when_query_reports_off(self) -> None:
        dev = MockMgrDevice(["0\n", "OK\n"])
        output = ensure_device_vbus_on(dev, retry_interval=0)
        self.assertIn("VBUS turned ON successfully", output)
        self.assertEqual(dev.calls[0][0], "WL+VBUS=?")
        self.assertEqual(dev.calls[1][0], "WL+VBUS=ON")

    def test_ensure_device_vbus_on_accepts_ok_amid_info_lines(self) -> None:
        dev = MockMgrDevice([
            "0\n",
            "[INFO] Auto-selected MGR port: COM37\n[INFO] Backend: Windows PowerShell bridge\nOK\n",
        ])
        output = ensure_device_vbus_on(dev, retry_interval=0)
        self.assertIn("VBUS turned ON successfully", output)

    def test_wait_for_disk_root_explicit_root_uses_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "mount"
            root.mkdir(parents=True)
            with mock.patch("common.disk_utils.get_volume_label", return_value="DATA A1B2"):
                selected = wait_for_disk_root(explicit_root=str(root), timeout_sec=1)
            self.assertEqual(selected, root)

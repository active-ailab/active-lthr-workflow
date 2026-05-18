from __future__ import annotations

import sys
import unittest
from unittest import mock
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from shared import device_flow  # noqa: E402


class _FakeDevice:
    def __init__(self, mount_root: Path) -> None:
        self.mount_root = mount_root

    def mount_data(self, timeout: int = 30) -> str:
        return f"{self.mount_root}\n"

    def unmount(self, timeout: int = 15) -> str:
        return "UNMOUNTED\n"

    def open_disk(self, disk: str, timeout: int = 30, auto_unmount: bool = True):
        assert disk == "data"
        parent = self

        class _Session:
            def __init__(self) -> None:
                self.root = parent.mount_root
                self.mount_output = f"{parent.mount_root}\n"
                self.unmount_output = "UNMOUNTED\n"

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        return _Session()


class DeviceFlowTests(unittest.TestCase):
    def test_deploy_data_to_device_passes_mount_resolvers_to_drive_guard(self) -> None:
        mount_root = Path("/tmp/fake-data")
        seen: dict[str, object] = {}

        def fake_collect(detect_fn, resolve_fn):
            seen["detect_fn"] = detect_fn
            seen["resolve_fn"] = resolve_fn
            return {"F:"}

        def fake_wait_for_disk_root(explicit_root, timeout_sec, exclude_roots=None):
            seen["explicit_root"] = explicit_root
            seen["timeout_sec"] = timeout_sec
            seen["exclude_roots"] = exclude_roots
            return mount_root

        def fake_deploy(data_root_text: str):
            seen["data_root_text"] = data_root_text
            return {
                "latest_csv": f"{data_root_text}/data_sample/gomore_data_20260515_120000.csv",
                "csv_count": 1,
                "copied_size": 128,
                "state_file_count": 0,
            }

        old_collect = device_flow.collect_preexisting_drives
        old_is_wsl = device_flow.is_wsl_environment
        old_wait = device_flow.wait_for_disk_root
        old_device = device_flow.WatchlinkDevice
        try:
            device_flow.collect_preexisting_drives = fake_collect
            device_flow.is_wsl_environment = lambda: False
            device_flow.wait_for_disk_root = fake_wait_for_disk_root
            device_flow.WatchlinkDevice = lambda mgr_port, wlctl_path: _FakeDevice(mount_root)

            data_root, raw_log = device_flow.deploy_data_to_device(
                wlctl_path=Path("/fake/wlctl.sh"),
                mgr_port="COM37",
                mount_wait_sec=25,
                deploy_attempts=1,
                deploy_hook=fake_deploy,
            )
        finally:
            device_flow.collect_preexisting_drives = old_collect
            device_flow.is_wsl_environment = old_is_wsl
            device_flow.wait_for_disk_root = old_wait
            device_flow.WatchlinkDevice = old_device

        self.assertEqual(data_root, str(mount_root))
        self.assertIs(seen["detect_fn"], device_flow.detect_mount_roots)
        with mock.patch("common.disk_utils.get_volume_label", return_value="DATA A1B2"):
            self.assertEqual(seen["resolve_fn"](mount_root), mount_root)
        self.assertEqual(seen["data_root_text"], str(mount_root))
        self.assertEqual(seen["exclude_roots"], ["F:"])
        self.assertIn("Deployment verified", raw_log)

    def test_deploy_data_to_device_uses_mounted_disk_session_in_wsl(self) -> None:
        mount_root = Path("/mnt/f")
        seen: dict[str, object] = {}

        def fake_collect(detect_fn, resolve_fn):
            seen["detect_fn"] = detect_fn
            seen["resolve_fn"] = resolve_fn
            return {"E:"}

        def fake_deploy(data_root_text: str):
            seen["data_root_text"] = data_root_text
            return {
                "latest_csv": f"{data_root_text}/data_sample/gomore_data_20260515_120000.csv",
                "csv_count": 1,
                "copied_size": 128,
                "state_file_count": 0,
            }

        old_collect = device_flow.collect_preexisting_drives
        old_is_wsl = device_flow.is_wsl_environment
        old_device = device_flow.WatchlinkDevice
        try:
            device_flow.collect_preexisting_drives = fake_collect
            device_flow.is_wsl_environment = lambda: True
            device_flow.WatchlinkDevice = lambda mgr_port, wlctl_path: _FakeDevice(mount_root)

            data_root, raw_log = device_flow.deploy_data_to_device(
                wlctl_path=Path("/fake/wlctl.sh"),
                mgr_port="COM37",
                mount_wait_sec=25,
                deploy_attempts=1,
                deploy_hook=fake_deploy,
            )
        finally:
            device_flow.collect_preexisting_drives = old_collect
            device_flow.is_wsl_environment = old_is_wsl
            device_flow.WatchlinkDevice = old_device

        self.assertEqual(data_root, str(mount_root))
        self.assertEqual(seen["data_root_text"], str(mount_root))
        self.assertIn("Deployment verified", raw_log)
        self.assertIn(str(mount_root), raw_log)
        self.assertIn("UNMOUNTED", raw_log)

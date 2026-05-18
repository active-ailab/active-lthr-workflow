from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from shared.watchlink_runtime import (  # noqa: E402
    WATCHLINK_SCRIPTS_DIR,
    build_runtime_hint,
    build_wlctl_path,
    format_timestamp,
)


class WatchlinkRuntimeTests(unittest.TestCase):
    def test_runtime_hint_is_provided_by_watchlink_diagnose_utils(self) -> None:
        hint = build_runtime_hint("cmd.exe timed out")
        self.assertIn("E_WSL_BRIDGE", hint)

    def test_build_wlctl_path_discovers_real_entrypoint(self) -> None:
        self.assertTrue(WATCHLINK_SCRIPTS_DIR.exists())
        self.assertTrue((WATCHLINK_SCRIPTS_DIR / "watchlink_discovery.py").exists())
        self.assertTrue(build_wlctl_path().exists())

    def test_format_timestamp_shape(self) -> None:
        self.assertRegex(format_timestamp(), re.compile(r"^\d{8}_\d{6}$"))

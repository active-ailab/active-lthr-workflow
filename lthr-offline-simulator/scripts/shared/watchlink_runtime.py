from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Iterable, List, Optional


def _iter_watchlink_script_candidates(anchor: Optional[Path] = None) -> Iterable[Path]:
    env_dir = os.getenv("WATCHLINK_V3_SCRIPTS_DIR", "").strip()
    if env_dir:
        yield Path(env_dir).expanduser()

    base = Path(anchor or __file__).resolve()
    base = base if base.is_dir() else base.parent
    yield base
    for root in base.parents:
        yield root / "agent_skills" / "skills" / "device" / "watchlink-v3" / "scripts"
        yield root / "skills" / "device" / "watchlink-v3" / "scripts"
        yield root / "watchlink-v3" / "scripts"

    yield Path.home() / "active_skills" / "device-skills" / "watchlink-v3" / "scripts"
    yield Path.home() / "agent_skills" / "skills" / "device" / "watchlink-v3" / "scripts"
    yield Path.home() / ".agents" / "skills" / "watchlink-v3" / "scripts"


def _ensure_watchlink_scripts_on_path(anchor: Optional[Path] = None) -> Path:
    checked: List[str] = []
    for candidate in _iter_watchlink_script_candidates(anchor=anchor):
        checked.append(str(candidate))
        if not candidate.is_dir():
            continue
        if not (candidate / "watchlink_discovery.py").exists():
            continue
        candidate_text = str(candidate)
        if candidate_text not in sys.path:
            sys.path.insert(0, candidate_text)
        return candidate
    raise FileNotFoundError(
        "Unable to locate watchlink-v3 scripts directory. Checked: "
        + ", ".join(checked)
    )


WATCHLINK_SCRIPTS_DIR = _ensure_watchlink_scripts_on_path(anchor=Path(__file__).resolve())

from watchlink_discovery import build_wlctl_path as _build_external_wlctl_path  # noqa: E402
from watchlink_discovery import ensure_wlctl_sdk as _ensure_external_wlctl_sdk  # noqa: E402

_ensure_external_wlctl_sdk(anchor=Path(__file__).resolve())

from diagnose_utils import (  # noqa: E402
    append_runtime_hint,
    build_runtime_diagnostic_payload,
    build_runtime_hint,
)
from common.fs_utils import (  # noqa: E402
    MountedDiskSession,
    ensure_local_mount_root,
    list_mounted_paths,
    pull_mounted_paths,
    push_to_mounted_root,
    read_mounted_file,
    remove_from_mounted_root,
)
from wlctl_sdk import (  # noqa: E402
    IS_WIN32,
    WatchlinkDevice,
    WatchlinkDeviceProtocol,
    ensure_wsl_windows_bridge,
    extract_disk_root,
    is_wsl_environment,
    run_cmd,
    run_powershell,
    to_windows_path,
)


def _safe_reconfigure_stdio() -> None:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def format_timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S", time.localtime())


def build_wlctl_path() -> Path:
    return _build_external_wlctl_path(anchor=Path(__file__).resolve())


__all__ = [
    "IS_WIN32",
    "WATCHLINK_SCRIPTS_DIR",
    "MountedDiskSession",
    "WatchlinkDevice",
    "WatchlinkDeviceProtocol",
    "_safe_reconfigure_stdio",
    "append_runtime_hint",
    "build_runtime_diagnostic_payload",
    "build_runtime_hint",
    "build_wlctl_path",
    "ensure_local_mount_root",
    "ensure_wsl_windows_bridge",
    "extract_disk_root",
    "format_timestamp",
    "is_wsl_environment",
    "list_mounted_paths",
    "pull_mounted_paths",
    "push_to_mounted_root",
    "read_mounted_file",
    "remove_from_mounted_root",
    "run_cmd",
    "run_powershell",
    "to_windows_path",
]

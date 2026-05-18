from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .watchlink_runtime import WatchlinkDevice, WatchlinkDeviceProtocol
from disk_utils import detect_mount_roots, wait_for_disk_root

# ── Shared output root ──
LTHR_OUTPUT_ROOT = Path("/tmp/tmp_lthr_out")
_OUTPUT_MAX_AGE_SEC = 7 * 24 * 3600  # 7 days


def housekeep_output() -> int:
    """Remove output subdirs older than 7 days.  Returns number deleted.

    Called once at script startup, before any new output is written.
    """
    if not LTHR_OUTPUT_ROOT.exists():
        LTHR_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        return 0

    deadline = time.time() - _OUTPUT_MAX_AGE_SEC
    deleted = 0
    for child in LTHR_OUTPUT_ROOT.iterdir():
        if not child.is_dir():
            continue
        try:
            mtime = child.stat().st_mtime
            if mtime < deadline:
                import shutil
                shutil.rmtree(child, ignore_errors=True)
                deleted += 1
        except OSError:
            pass
    return deleted


# ── WSL mount bridge → shared/windows_mount_bridge.py ──

def _make_mgr_device(wlctl_path: Path, mgr_port: str = "") -> WatchlinkDevice:
    return WatchlinkDevice(mgr_port=mgr_port, wlctl_path=wlctl_path)


def _mgr_response_tokens(output: str) -> List[str]:
    tokens: List[str] = []
    for raw_line in (output or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("["):
            continue
        tokens.append(line.lower())
    return tokens


def ensure_device_vbus_on(
    dev: WatchlinkDeviceProtocol,
    timeout_sec: float = 10.0,
    retry_interval: float = 1.0,
    check_cmd: str = "WL+VBUS=?",
    turn_on_cmd: str = "WL+VBUS=ON",
) -> str:
    """Ensure VBUS is ON before UART operations.

    First queries VBUS state, then sends WL+VBUS=ON if needed.
    For disk-mode contexts where serial disconnects are expected,
    pass turn_on_cmd='WL+VBUS=TEMP' directly.
    """
    logs: List[str] = []
    output = ""

    logs.append(f"===== MGR {check_cmd} =====\n")
    try:
        output = dev.send_mgr(check_cmd, timeout=max(timeout_sec, 8.0))
        logs.append(output)
    except Exception as exc:
        logs.append(f"[WARN] VBUS check failed: {exc}\n")
        logs.append("[INFO] VBUS query failed; will attempt to turn VBUS on anyway.\n")

    query_tokens = _mgr_response_tokens(output)
    if any(token in ("1", "1.0", "on", "vbus=on", "wl+vbus=on") for token in query_tokens):
        logs.append("[INFO] VBUS is already ON\n")
        return "".join(logs)

    logs.append(f"===== MGR {turn_on_cmd} =====\n")
    for attempt in range(3):
        try:
            output = dev.send_mgr(turn_on_cmd, timeout=max(timeout_sec, 8.0))
            logs.append(output)
            response_tokens = _mgr_response_tokens(output)
            if any(token in ("ok", "1", "1.0", "on", "vbus=on", "wl+vbus=on") for token in response_tokens):
                logs.append("[INFO] VBUS turned ON successfully\n")
                return "".join(logs)
        except Exception as exc:
            logs.append(f"[WARN] VBUS ON attempt {attempt + 1}/3 failed: {exc}\n")
        if attempt < 2:
            time.sleep(retry_interval)

    raise RuntimeError(
        "Failed to enable VBUS after 3 attempts. "
        "MGR/UART serial communication requires VBUS power.\n"
        + "".join(logs)
    )


def ensure_mgr_vbus_on(
    wlctl_path: Path,
    mgr_port: str = "",
    timeout_sec: float = 10.0,
    retry_interval: float = 1.0,
) -> str:
    return ensure_device_vbus_on(
        _make_mgr_device(wlctl_path, mgr_port=mgr_port),
        timeout_sec=timeout_sec,
        retry_interval=retry_interval,
    )


def send_mgr_cmd(
    wlctl_path: Path,
    cmd_text: str,
    mgr_port: str = "",
    check: bool = True,
    timeout: Optional[float] = None,
) -> str:
    dev = _make_mgr_device(wlctl_path, mgr_port=mgr_port)
    try:
        return dev.send_mgr(cmd_text, timeout=timeout or 12.0)
    except Exception as exc:
        if check:
            raise
        return str(exc)


def reset_device_after_unmount(
    wlctl_path: Path,
    mgr_port: str = "",
    settle_sec: float = 8.0,
) -> str:
    dev = _make_mgr_device(wlctl_path, mgr_port=mgr_port)
    return dev.reset(settle_sec=settle_sec)


# ════════════════════════════════════════════
# IMG log disk helpers
# ════════════════════════════════════════════

def wait_for_log_root(timeout_sec: int = 30) -> Path:
    """Wait for an IMG disk to appear and return its mount root."""
    from disk_utils import detect_mount_roots as _detect, select_log_root as _select

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        roots = _detect()
        selected = _select(roots) if roots else None
        if selected:
            return selected
        time.sleep(0.5)
    raise RuntimeError(
        f"Timeout ({timeout_sec}s) waiting for IMG log disk to appear.\n"
        f"Detected mount roots: {_detect()}"
    )


__all__ = [
    "LTHR_OUTPUT_ROOT",
    "WatchlinkDevice",
    "WatchlinkDeviceProtocol",
    "detect_mount_roots",
    "ensure_device_vbus_on",
    "ensure_mgr_vbus_on",
    "housekeep_output",
    "reset_device_after_unmount",
    "send_mgr_cmd",
    "wait_for_disk_root",
    "wait_for_log_root",
]

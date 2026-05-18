"""Device flow orchestration — shared mount/deploy/ready lifecycle.

This module captures the common device-phase patterns shared by
run_lthr_offline_sim.py and run_device_multi_sim.py, leaving entry scripts
to focus on parameter assembly and simulation-specific logic.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from disk_utils import select_data_root

from .deploy_utils import (
    LTHR_OUTPUT_ROOT,
    detect_mount_roots,
    ensure_mgr_vbus_on,
    housekeep_output,
    reset_device_after_unmount,
    wait_for_disk_root,
)
from .report_generator import format_deployment_summary
from .utest_runner import (
    prepare_uart_shell_for_long_log,
    wait_for_device_ready,
)
from .watchlink_runtime import (
    MountedDiskSession,
    WatchlinkDevice,
    is_wsl_environment,
)
from .windows_mount_bridge import collect_preexisting_drives, normalize_drive_letter


# ── Pre-mount collision guard → shared/windows_mount_bridge.py ────

# ── Deploy callback type ─────────────────────────────────────────────

DeployHook = Callable[[str], Dict[str, object]]
"""Signature: (data_root_text: str) -> deployment_info dict.

The callback is responsible for copying CSV(s) to the device and
returning a verification dict. For WSL it receives a DATA root path
(e.g. ``F:\\`` or ``/mnt/f``); for Linux a native Path.
"""


# ── Mount & deploy with retries ───────────────────────────────────────

def deploy_data_to_device(
    *,
    wlctl_path: Path,
    mgr_port: str,
    disk_root: str = "",
    mount_wait_sec: int = 30,
    deploy_attempts: int = 3,
    deploy_hook: DeployHook,
    deploy_hook_wsl: Optional[DeployHook] = None,
) -> Tuple[str, str]:
    """Mount DATA (FS) partition, deploy CSVs, unmount.

    Returns (data_root, concatenated_raw_log).

    WSL: mounts via watchlink-v3 SDK, hands the mounted root text to
    *deploy_hook* (or *deploy_hook_wsl* if provided).

    Linux / explicit disk-root: mounts via WatchlinkDevice.mount_data(),
    hands the native path string to *deploy_hook*.
    """
    preexisting_drives = collect_preexisting_drives(
        detect_mount_roots,
        lambda root: select_data_root([root]),
    )
    raw_logs: List[str] = []
    deploy_errors: List[str] = []

    for attempt in range(1, deploy_attempts + 1):
        mounted = False
        session: Optional[MountedDiskSession] = None
        raw_logs.append(f"===== DEPLOY ATTEMPT {attempt}/{deploy_attempts} =====\n")
        try:
            if is_wsl_environment() and not disk_root:
                dev = WatchlinkDevice(mgr_port=mgr_port, wlctl_path=wlctl_path)
                with dev.open_disk("data", timeout=mount_wait_sec) as mounted_session:
                    session = mounted_session
                    mounted = True
                    data_root = str(session.root)
                    drive_letter = normalize_drive_letter(data_root)
                    if drive_letter and drive_letter in preexisting_drives:
                        raise RuntimeError(
                            f"Mounted DATA root {data_root} matches a "
                            f"preexisting drive.  Unmount old devices before retrying."
                        )
                    raw_logs.append(session.mount_output)

                    hook = deploy_hook_wsl or deploy_hook
                    info = hook(data_root)
                    raw_logs.append(format_deployment_summary(info))
                mounted = False
                if session and session.unmount_output:
                    raw_logs.append(session.unmount_output)
            else:
                dev = WatchlinkDevice(mgr_port=mgr_port, wlctl_path=wlctl_path)
                if disk_root:
                    raw_logs.append(
                        "[INFO] Using explicit disk root; skip auto mount-data and reuse existing DATA disk.\n"
                    )
                    mounted = True
                else:
                    mount_log = dev.mount_data(timeout=mount_wait_sec)
                    raw_logs.append(mount_log)
                    mounted = True

                data_root_path = wait_for_disk_root(
                    disk_root,
                    mount_wait_sec,
                    exclude_roots=None if disk_root else list(preexisting_drives),
                )
                data_root = str(data_root_path)

                hook = deploy_hook
                info = hook(data_root)
                raw_logs.append(format_deployment_summary(info))

                unmount_log = dev.unmount()
                raw_logs.append(unmount_log)
                mounted = False

            return data_root, "".join(raw_logs)

        except Exception as exc:
            deploy_errors.append(f"attempt {attempt}: {exc}")
            raw_logs.append(f"[WARN] Deploy attempt {attempt} failed: {exc}\n")
            if mounted:
                try:
                    if session and session.unmount_output:
                        raw_logs.append(session.unmount_output)
                    else:
                        dev = WatchlinkDevice(mgr_port=mgr_port, wlctl_path=wlctl_path)
                        raw_logs.append(dev.unmount())
                except Exception:
                    pass
            if attempt >= deploy_attempts:
                raise RuntimeError(
                    "Device deployment failed after retries:\n- "
                    + "\n- ".join(deploy_errors)
                )
            time.sleep(2.0)

    raise RuntimeError("Unreachable")  # pragma: no cover


# ── Device boot & ready sequence ──────────────────────────────────────

def ensure_device_ready_sequence(
    *,
    wlctl_path: Path,
    mgr_port: str,
    uart_port: str,
    boot_timeout_sec: int = 180,
    serial_settle_sec: int = 5,
) -> None:
    """Reset device, ensure VBUS, wait for launcher/shell-ready.

    Should be called after unmount to bring the device to a known-good state
    before running UART tests.
    """
    reset_device_after_unmount(wlctl_path, mgr_port=mgr_port, settle_sec=serial_settle_sec)
    ensure_mgr_vbus_on(wlctl_path, mgr_port=mgr_port)
    wait_for_device_ready(wlctl_path, uart_port=uart_port, timeout_sec=boot_timeout_sec, listen_chunk_sec=5)
    prepare_uart_shell_for_long_log(wlctl_path, uart_port=uart_port, timeout_sec=15)

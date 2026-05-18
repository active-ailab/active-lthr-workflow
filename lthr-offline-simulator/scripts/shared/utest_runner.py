from __future__ import annotations

import re
import time
from pathlib import Path
from typing import List, Optional, Sequence

from .watchlink_runtime import (
    WatchlinkDevice,
    WatchlinkDeviceProtocol,
    append_runtime_hint,
    is_wsl_environment,
)


_SHELL_PROMPT_RE = re.compile(r"(?:^|[\r\n])(?:\$ |# )")
_MONKEY_STATUS_RE = re.compile(
    r"monkey\s+status|monkey\s*=|status\s*=|monkey\s+is\s+(?:on|off)",
    re.IGNORECASE,
)
_UART_LISTEN_BRIDGE_ABORT_RE = re.compile(r"the i/o operation has been aborted", re.IGNORECASE)
_LINUX_TTY_NOT_FOUND_RE = re.compile(r"linux tty not found", re.IGNORECASE)


def _uart_probe_succeeded(output: str, probe_cmd: str, probe_token: str) -> bool:
    probe_cmd_norm = (probe_cmd or "").strip().lower()
    if not output:
        return False
    if probe_token and probe_token in output:
        return True
    if _SHELL_PROMPT_RE.search(output):
        return True
    if probe_cmd_norm == "monkey -g" and _MONKEY_STATUS_RE.search(output):
        return True
    return False


def _make_uart_device(wlctl_path: Path, uart_port: str = "") -> WatchlinkDevice:
    return WatchlinkDevice(uart_port=uart_port, wlctl_path=wlctl_path)


def send_device_uart(
    dev: WatchlinkDeviceProtocol,
    cmd_text: str,
    read_ms: int = 1500,
    timeout: Optional[float] = None,
) -> str:
    return dev.send_uart(cmd_text, read_ms=read_ms, timeout=timeout)


def listen_device_uart(
    dev: WatchlinkDeviceProtocol,
    seconds: int = 5,
    timeout: Optional[float] = None,
) -> str:
    return dev.listen_uart(seconds=seconds, timeout=timeout)


def send_uart_cmd(
    wlctl_path: Path,
    cmd_text: str,
    uart_port: str = "",
    read_ms: int = 1500,
    timeout: Optional[float] = None,
) -> str:
    try:
        return send_device_uart(
            _make_uart_device(wlctl_path, uart_port=uart_port),
            cmd_text,
            read_ms=read_ms,
            timeout=timeout,
        )
    except Exception as exc:
        raise RuntimeError(append_runtime_hint(str(exc))) from exc


def listen_uart(
    wlctl_path: Path,
    uart_port: str = "",
    seconds: int = 5,
    timeout: Optional[float] = None,
) -> str:
    try:
        return listen_device_uart(
            _make_uart_device(wlctl_path, uart_port=uart_port),
            seconds=seconds,
            timeout=timeout,
        )
    except Exception as exc:
        message = str(exc)
        if (
            is_wsl_environment()
            and _UART_LISTEN_BRIDGE_ABORT_RE.search(message)
            and _LINUX_TTY_NOT_FOUND_RE.search(message)
        ):
            return "\n[WARN] UART listen bridge aborted; retrying next listen window.\n"
        raise RuntimeError(append_runtime_hint(message)) from exc


def wait_device_ready(
    dev: WatchlinkDeviceProtocol,
    timeout_sec: int,
    listen_chunk_sec: int,
    ready_markers: Sequence[str] = ("gotoScreen:LauncherScreen", "gotoScreen:ChargeScreen"),
    probe_cmd: str = "monkey -g",
    probe_token: str = "monkey status",
    label: str = "ready screen logs",
) -> str:
    deadline = time.time() + timeout_sec
    chunks: List[str] = []
    probe_index = 0

    while time.time() < deadline:
        seconds = max(1, min(listen_chunk_sec, int(deadline - time.time()) or 1))
        probe_index += 1
        probe_read_ms = max(1800, min(6000, seconds * 900))
        try:
            probe = send_device_uart(
                dev,
                probe_cmd,
                read_ms=probe_read_ms,
                timeout=probe_read_ms / 1000 + 10,
            )
        except Exception as exc:
            chunks.append(f"[WARN] UART shell probe #{probe_index} failed: {exc}\n")
        else:
            if probe:
                chunks.append(probe)
                if _uart_probe_succeeded(probe, probe_cmd, probe_token):
                    chunks.append(f"[INFO] UART shell probe #{probe_index} succeeded\n")
                    return "".join(chunks)

        try:
            chunk = listen_device_uart(
                dev,
                seconds=seconds,
                timeout=seconds + 8,
            )
        except Exception as exc:
            chunk = f"\n[WARN] UART listen failed while waiting for {label}: {exc}\n"
        chunks.append(chunk)
        if any(marker in chunk for marker in ready_markers):
            return "".join(chunks)

    tail = "".join(chunks)[-4000:]
    raise RuntimeError(
        append_runtime_hint(
            "Device did not reach launcher-ready or shell-ready state within timeout. "
            f"markers={tuple(ready_markers)}, probe={probe_cmd}. Recent UART output:\n{tail}"
        )
    )


def wait_for_device_ready(
    wlctl_path: Path,
    uart_port: str,
    timeout_sec: int,
    listen_chunk_sec: int,
    ready_markers: Sequence[str] = ("gotoScreen:LauncherScreen", "gotoScreen:ChargeScreen"),
    probe_cmd: str = "monkey -g",
    probe_token: str = "monkey status",
    label: str = "ready screen logs",
) -> str:
    return wait_device_ready(
        _make_uart_device(wlctl_path, uart_port=uart_port),
        timeout_sec=timeout_sec,
        listen_chunk_sec=listen_chunk_sec,
        ready_markers=ready_markers,
        probe_cmd=probe_cmd,
        probe_token=probe_token,
        label=label,
    )


def prepare_device_for_long_log(
    dev: WatchlinkDeviceProtocol,
    timeout_sec: int = 15,
    keep_awake_cmd: str = "power -s on",
    keep_awake_read_ms: int = 1200,
    probe_cmd: str = "monkey -g",
    probe_token: str = "monkey status",
    probe_read_ms: int = 2500,
) -> str:
    chunks: List[str] = []

    if keep_awake_cmd:
        chunks.append(f"===== UART {keep_awake_cmd} =====\n")
        try:
            keep_awake_output = send_device_uart(
                dev,
                keep_awake_cmd,
                read_ms=keep_awake_read_ms,
                timeout=max(8.0, keep_awake_read_ms / 1000 + 5),
            )
        except Exception as exc:
            chunks.append(f"[WARN] {keep_awake_cmd} failed: {exc}\n")
        else:
            chunks.append(keep_awake_output)
            if _uart_probe_succeeded(keep_awake_output, probe_cmd, probe_token):
                chunks.append("[INFO] UART keep-awake command returned shell prompt\n")
                return "".join(chunks)

    deadline = time.time() + max(1, timeout_sec)
    probe_index = 0
    while time.time() < deadline:
        probe_index += 1
        chunks.append(f"===== UART shell probe #{probe_index} =====\n")
        try:
            probe = send_device_uart(
                dev,
                probe_cmd,
                read_ms=probe_read_ms,
                timeout=max(8.0, probe_read_ms / 1000 + 6),
            )
        except Exception as exc:
            chunks.append(f"[WARN] UART shell probe #{probe_index} failed: {exc}\n")
        else:
            chunks.append(probe)
            if _uart_probe_succeeded(probe, probe_cmd, probe_token):
                chunks.append(f"[INFO] UART shell probe #{probe_index} succeeded\n")
                return "".join(chunks)

        try:
            chunk = listen_device_uart(dev, seconds=1, timeout=9)
        except Exception as exc:
            chunks.append(f"[WARN] UART listen warm-up failed: {exc}\n")
        else:
            chunks.append(chunk)
            if _uart_probe_succeeded(chunk, probe_cmd, probe_token):
                chunks.append("[INFO] UART listen warm-up returned shell prompt\n")
                return "".join(chunks)

    tail = "".join(chunks)[-4000:]
    raise RuntimeError(
        append_runtime_hint(
            "UART shell did not stabilize after ready. "
            f"probe={probe_cmd}. Recent UART output:\n{tail}"
        )
    )


def prepare_uart_shell_for_long_log(
    wlctl_path: Path,
    uart_port: str,
    timeout_sec: int = 15,
    keep_awake_cmd: str = "power -s on",
    keep_awake_read_ms: int = 1200,
    probe_cmd: str = "monkey -g",
    probe_token: str = "monkey status",
    probe_read_ms: int = 2500,
) -> str:
    return prepare_device_for_long_log(
        _make_uart_device(wlctl_path, uart_port=uart_port),
        timeout_sec=timeout_sec,
        keep_awake_cmd=keep_awake_cmd,
        keep_awake_read_ms=keep_awake_read_ms,
        probe_cmd=probe_cmd,
        probe_token=probe_token,
        probe_read_ms=probe_read_ms,
    )


__all__ = [
    "WatchlinkDevice",
    "WatchlinkDeviceProtocol",
    "listen_device_uart",
    "listen_uart",
    "prepare_device_for_long_log",
    "prepare_uart_shell_for_long_log",
    "send_device_uart",
    "send_uart_cmd",
    "wait_device_ready",
    "wait_for_device_ready",
]

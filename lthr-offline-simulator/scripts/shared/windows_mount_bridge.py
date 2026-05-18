"""WSL/Windows DATA-root helpers used for collision guards."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional


def normalize_drive_letter(root_path: str) -> str:
    """Extract drive letter from a WSL or Windows DATA root.

    /mnt/f             → F:
    F:\\               → F:
    /media/user/disk  → "" (no drive letter)
    """
    if root_path.startswith("/mnt/") and len(root_path) > 5:
        return root_path[5].upper() + ":"
    if len(root_path) >= 2 and root_path[1] == ":":
        return root_path[:2].upper()
    return ""


def collect_preexisting_drives(
    detect_mount_roots_fn: Optional[Callable[[], list[Path]]] = None,
    resolve_data_root_fn: Optional[Callable[[Path], Optional[Path]]] = None,
) -> set[str]:
    """Build a set of drive letters that already have DATA roots mounted.

    Handles both WSL (/mnt/f/...) and native Windows (F:\\...) paths.
    Used before mount+deploy to avoid picking the wrong device.
    """
    from disk_utils import detect_mount_roots, select_data_root

    detect_fn = detect_mount_roots_fn or detect_mount_roots
    resolve_fn = resolve_data_root_fn or (lambda path: select_data_root([path]))

    roots: list[str] = []
    for mp in detect_fn():
        resolved = resolve_fn(mp)
        if resolved:
            roots.append(str(resolved))

    drives: set = set()
    for r in roots:
        dl = normalize_drive_letter(r)
        if dl:
            drives.add(dl)
    return drives

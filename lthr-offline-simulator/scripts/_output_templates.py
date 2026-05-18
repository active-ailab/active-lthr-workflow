#!/usr/bin/env python3
"""Shared asset-backed template helpers for LTHR simulator outputs."""

import re
from pathlib import Path
from typing import Mapping


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
ASSETS_DIR = SKILL_ROOT / "assets"
TEMPLATE_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def load_asset_template(template_name: str) -> str:
    template_path = ASSETS_DIR / template_name
    if not template_path.exists():
        raise RuntimeError(f"Template asset not found: {template_path}")
    return template_path.read_text(encoding="utf-8")


def render_asset_template(template_name: str, context: Mapping[str, object]) -> str:
    template_text = load_asset_template(template_name)
    missing_keys = set()

    def replace(match: re.Match) -> str:
        key = match.group(1)
        if key not in context:
            missing_keys.add(key)
            return match.group(0)
        value = context[key]
        return "" if value is None else str(value)

    rendered = TEMPLATE_VAR_RE.sub(replace, template_text)
    if missing_keys:
        missing = ", ".join(sorted(missing_keys))
        raise RuntimeError(f"Template {template_name} is missing context keys: {missing}")
    return rendered


def format_optional_line(label: str, value: str) -> str:
    if not value:
        return ""
    return f"{label}: {value}\n"


def format_signature_block(signature: str, trailing_blank_line: bool = False) -> str:
    if not signature:
        return ""
    suffix = "\n\n" if trailing_blank_line else "\n"
    return f"{signature}{suffix}"
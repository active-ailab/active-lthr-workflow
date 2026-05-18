#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import sys
from typing import Optional
from typing import Callable, Optional


def _fix_none(v):
    if v is None or v == "":
        return None
    return v


def _payload_to_records(
    payload: dict,
    value_normalizer: Optional[Callable[[object], object]] = None,
) -> dict:
    if not payload or "fields" not in payload:
        return {}
    fields = payload["fields"]
    rows = payload.get("data", [])
    normalize = value_normalizer or _fix_none
    records = {}
    for row in rows:
        values = [normalize(v) for v in row]
        record = dict(zip(fields, values))
        sample_key = record.get("样本") or record.get("sample")
        if sample_key:
            records[str(sample_key)] = record
    return records


def _coerce_markdown_cell(value: str):
    text = (value or "").strip()
    if text == "":
        return None
    if re.fullmatch(r"-?\d+", text):
        try:
            return int(text)
        except ValueError:
            return text
    if re.fullmatch(r"-?\d+\.\d+", text):
        try:
            return float(text)
        except ValueError:
            return text
    return text


def _parse_markdown_table_records(raw_text: str) -> dict:
    """解析 lark-cli base +record-list 返回的 Markdown 表格。"""
    if not raw_text:
        return {}

    table_lines = [line.strip() for line in raw_text.splitlines() if line.strip().startswith("|")]
    if len(table_lines) < 2:
        return {}

    headers = [cell.strip() for cell in table_lines[0].strip("|").split("|")]
    records = {}
    for line in table_lines[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < len(headers):
            cells += [""] * (len(headers) - len(cells))
        record = {
            header: _coerce_markdown_cell(cells[idx])
            for idx, header in enumerate(headers)
        }
        sample_key = record.get("样本") or record.get("sample")
        if sample_key:
            records[str(sample_key)] = record
    return records


def parse_pace_to_seconds(pace_str):
    if not pace_str:
        return None
    try:
        parts = str(pace_str).split("'")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return None


def pace_format(seconds):
    if seconds is None:
        return "N/A"
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}'{s:02d}"


def run_lark(
    args: list,
    identity: str = "user",
    dry_run: bool = False,
    timeout: int = 120,
    force_json: bool = False,
    allow_raw: bool = False,
    cwd: Optional[str] = None,
) -> dict:
    cmd = ["lark-cli"] + list(args)
    if "--as" not in cmd:
        cmd += ["--as", identity]
    if force_json and "--format" not in cmd:
        cmd += ["--format", "json"]

    if dry_run:
        print(f"  $ {' '.join(cmd)}")
        return {"ok": True, "dry_run": True}

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "命令超时"}
    except FileNotFoundError:
        return {"ok": False, "error": "lark-cli 未找到，请确认已安装"}

    if result.returncode != 0:
        err = (result.stderr or "")[:500]
        if err:
            print(f"lark-cli 错误 (exit={result.returncode}): {err}", file=sys.stderr)
        return {"ok": False, "error": err or f"exit={result.returncode}"}

    stdout = result.stdout or ""
    if not stdout.strip():
        return {"ok": True, "raw": ""}

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        if allow_raw:
            return {"ok": True, "raw": stdout}
        return {"ok": False, "error": f"JSON 解析失败: {exc}"}

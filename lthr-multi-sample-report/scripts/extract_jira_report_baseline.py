#!/usr/bin/env python3
"""Extract one SDK version's per-round JIRA results from a generated Markdown report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ISSUE_RE = re.compile(r"^##\s+([A-Z][A-Z0-9]+-\d+)\s*$", re.IGNORECASE)
CSV_RE = re.compile(r"^###\s+(.+?\.csv)\s*$", re.IGNORECASE)
ROUND_RE = re.compile(r"^第\s*(\d+)\s*轮$")
NUMBER_RE = re.compile(r"^[-+]?\d+(?:\.\d+)?$")
PACE_RE = re.compile(r"(\d+)\s*['’:]\s*(\d{1,2})")
VERSION_RE = re.compile(r"^\|\s*\*\*新版本\*\*\s*\|\s*(.*?)\s*\|\s*$", re.MULTILINE)
USER_RE = re.compile(
    r"-\s*\*\*用户\*\*:\s*(\d+(?:\.\d+)?)岁\s*/\s*(男|女)\s*/\s*"
    r"(\d+(?:\.\d+)?)cm\s*/\s*(\d+(?:\.\d+)?)kg\s*/\s*静息\s*"
    r"(\d+(?:\.\d+)?)bpm"
)


def parse_pace_seconds(value: str):
    match = PACE_RE.search(value)
    if not match:
        return None
    minutes, seconds = int(match.group(1)), int(match.group(2))
    if seconds >= 60:
        return None
    return minutes * 60 + seconds


def parse_report(text: str, expected_version: str) -> dict:
    version_match = VERSION_RE.search(text)
    if not version_match:
        raise ValueError("报告中缺少新版本字段，无法确认基准版本")
    actual_version = re.sub(r"^SDK\s+", "", version_match.group(1).strip())
    if actual_version != expected_version:
        raise ValueError(
            f"报告新版本为 {actual_version!r}，与要求的 {expected_version!r} 不一致"
        )

    output = {}
    issue = None
    filename = None
    user_info = None
    current_rows = []

    def flush_sample():
        nonlocal current_rows
        if not issue or not filename or not current_rows:
            current_rows = []
            return
        valid = [row for row in current_rows if row["lh"] is not None and row["mh"] is not None and row["ltSpeed"] is not None]
        if valid:
            output.setdefault(issue, []).extend({
                "result_num": row["round"],
                "round": row["round"],
                "file": filename,
                "rows": None,
                "ltSpeed": row["ltSpeed"],
                "pace_precision": "whole_second_report",
                "lh": row["lh"],
                "mh": row["mh"],
                "userInfo": dict(user_info) if user_info else None,
            } for row in valid)
        current_rows = []

    for line in text.splitlines():
        issue_match = ISSUE_RE.match(line.strip())
        if issue_match:
            flush_sample()
            issue = issue_match.group(1).upper()
            output.setdefault(issue, [])
            filename = None
            user_info = None
            continue

        csv_match = CSV_RE.match(line.strip())
        if csv_match:
            flush_sample()
            filename = csv_match.group(1).strip()
            user_info = None
            continue

        if issue and filename:
            user_match = USER_RE.search(line)
            if user_match:
                age, gender, height, weight, hr_rest = user_match.groups()
                user_info = {
                    "age": float(age),
                    "gender": 1 if gender == "男" else 0,
                    "height": float(height),
                    "weight": float(weight),
                    "hrMax": None,
                    "hrRest": float(hr_rest),
                    "unknown": None,
                }

            if line.strip().startswith("|"):
                cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                if len(cells) >= 5:
                    round_match = ROUND_RE.match(cells[0])
                    if round_match:
                        round_num = int(round_match.group(1))
                        lthr = float(cells[1]) if NUMBER_RE.fullmatch(cells[1]) else None
                        maxhr = float(cells[3]) if NUMBER_RE.fullmatch(cells[3]) else None
                        pace_seconds = parse_pace_seconds(cells[4])
                        if lthr is not None and maxhr is not None and pace_seconds:
                            current_rows.append({
                                "round": round_num,
                                "lh": lthr,
                                "mh": maxhr,
                                "ltSpeed": 3600.0 / pace_seconds,
                            })

    flush_sample()
    sample_count = sum(len(rows) for rows in output.values())
    if not sample_count:
        raise ValueError("未从报告提取到带 LTHR、MAXHR 和配速的有效逐轮基准")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, help="上一 SDK 版本的 JIRA 对比报告 Markdown")
    parser.add_argument("--expected-version", required=True, help="报告中的新版本必须与该值一致")
    parser.add_argument("-o", "--output", required=True, help="输出基准 JSON 路径")
    args = parser.parse_args()

    try:
        text = Path(args.report).read_text(encoding="utf-8")
        data = parse_report(text, args.expected_version)
        Path(args.output).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"OK: 已提取 {sum(len(rows) for rows in data.values())} 轮基准，涉及 {len(data)} 个 Issue")
    print(f"    输出: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

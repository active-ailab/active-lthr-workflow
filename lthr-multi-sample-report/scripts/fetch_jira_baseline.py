#!/usr/bin/env python3
"""
fetch_jira_baseline.py - 从 JIRA 评论中提取旧版 SDK 基准值

从 JIRA Issue 的评论中提取 GoMore 乳酸阈分析结果（旧版 SDK 出值），
用于与新 SDK 版本进行对比。

用法:
  # 提取单个 JIRA Issue 的基准值
  python3 fetch_jira_baseline.py COLOGNE-3107

  # 提取多个 Issue
  python3 fetch_jira_baseline.py COLOGNE-3107 MOD-7798 WIN-1957

  # 输出到指定文件
  python3 fetch_jira_baseline.py COLOGNE-3107 -o baseline.json

  # 输出格式化表格
  python3 fetch_jira_baseline.py COLOGNE-3107 MOD-7798 --table
"""

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Optional


# ─── 正则 ──────────────────────────────────────────────────

# 匹配结果行: * Result #1: gomore_data_xxx.csv | rows=N | ltSpeed=X | lh=Y | mh=Z
# 兼容 Jira Wiki 标记: lh={color:#DE350B}187.{color}0
RE_RESULT_LINE = re.compile(
    r"\*\s*Result\s*#(\d+):\s*(\S+\.csv)\s*\|\s*rows\s*=\s*(\d+)"
    r"\s*\|\s*ltSpeed\s*=\s*([\d.]+)"
    r"(?:\s*\([^)]*\))?"
    r"\s*\|\s*lh\s*=\s*([^|]+)"
    r"\s*\|\s*mh\s*=\s*([^|]+)",
    re.IGNORECASE,
)

# 新评论格式：先给出 CSV，再列出每轮的结果。
RE_SAMPLE_LINE = re.compile(r"^\s*\*\s*Sample:\s*(\S+\.csv)\s*\|", re.IGNORECASE)
RE_RUN_LINE = re.compile(
    r"^\s*-\s*Run\s*#(\d+):\s*"
    r"ltSpeed=(\S+)\s+lh=(\S+)\s+mh=(\S+)(?:\s+guideResult=\S+)?",
    re.IGNORECASE,
)

# 匹配 userInfo 行
RE_USER_INFO = re.compile(
    r"userInfo\s*=\s*\[([\d.,\s-]+)\]",
)

# 解析数字，去除 Jira Wiki 标记
def _clean_jira_value(raw: str) -> str:
    """去除 Jira Wiki 标记如 {color:#DE350B} 和 {color}，保留数值。"""
    raw = re.sub(r"\{[^}]*\}", "", raw)
    return raw.strip()


def _parse_jira_number(raw: str) -> Optional[float]:
    """Parse a numeric comment field after removing Jira Wiki color markup."""
    cleaned = _clean_jira_value(raw)
    match = re.fullmatch(r"[-+]?\d+(?:\.\d+)?", cleaned)
    return float(match.group(0)) if match else None


def _attach_user_info(entry: dict, current_user_info: Optional[str]) -> None:
    if current_user_info is None:
        return
    parts = [p.strip() for p in current_user_info.split(",")]
    entry["userInfo"] = {
        "age": float(parts[0]) if len(parts) > 0 else None,
        "gender": int(float(parts[1])) if len(parts) > 1 else None,
        "height": float(parts[2]) if len(parts) > 2 else None,
        "weight": float(parts[3]) if len(parts) > 3 else None,
        "hrMax": float(parts[4]) if len(parts) > 4 else None,
        "hrRest": float(parts[5]) if len(parts) > 5 else None,
        "unknown": float(parts[6]) if len(parts) > 6 else None,
    }


# ─── 核心函数 ──────────────────────────────────────────────

def fetch_issue_baseline(issue_key: str) -> list:
    """
    从单个 JIRA Issue 的评论中提取旧 Result 行和新 Sample/Run 行。
    新格式结果带 round；旧格式结果不带 round，供报告兼容复用。
    """
    cmd = ["zepp", "jira", "comment", "list", issue_key]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  [错误] 运行 zepp 命令失败: {e}", file=sys.stderr)
        return []

    if r.returncode != 0:
        print(f"  [错误] {issue_key}: zepp comment list 返回错误: {r.stderr.strip()[:200]}", file=sys.stderr)
        return []

    lines = r.stdout.split("\n")
    results = []
    current_user_info = None
    current_sample_file = None

    for line in lines:
        if "【乳酸阈离线仿真结果】" in line:
            current_sample_file = None
            current_user_info = None

        # 检测 userInfo（可能在下一行）
        ui_m = RE_USER_INFO.search(line)
        if ui_m:
            current_user_info = ui_m.group(1).strip()

        sample_match = RE_SAMPLE_LINE.search(line)
        if sample_match:
            current_sample_file = sample_match.group(1)
            continue

        # 旧格式：* Result #1: file.csv | rows=N | ltSpeed=... | lh=... | mh=...
        rm = RE_RESULT_LINE.search(line)
        if rm:
            result_num, csv_file, rows, lt_speed, lh_raw, mh_raw = rm.groups()
            lt_speed_num = _parse_jira_number(lt_speed)
            lh = _parse_jira_number(lh_raw)
            mh = _parse_jira_number(mh_raw)
            if lt_speed_num is None or lh is None or mh is None:
                continue

            entry = {
                "result_num": int(result_num),
                "file": csv_file,
                "rows": int(rows),
                "ltSpeed": lt_speed_num,
                "lh": lh,
                "mh": mh,
            }
            _attach_user_info(entry, current_user_info)
            results.append(entry)
            current_user_info = None  # 重置
            continue

        # 新格式：每条 Run 绑定最近的 Sample 文件名。
        run_match = RE_RUN_LINE.search(line)
        if run_match and current_sample_file:
            round_num, lt_speed_raw, lh_raw, mh_raw = run_match.groups()
            lt_speed = _parse_jira_number(lt_speed_raw)
            lh = _parse_jira_number(lh_raw)
            mh = _parse_jira_number(mh_raw)
            # 无效/未出值轮次不能作为旧版阈值基准，但也不代表仿真执行失败。
            if lt_speed is None or lh is None or mh is None or min(lt_speed, lh, mh) <= 0:
                continue

            entry = {
                "result_num": int(round_num),
                "round": int(round_num),
                "file": current_sample_file,
                "rows": None,
                "ltSpeed": lt_speed,
                "lh": lh,
                "mh": mh,
            }
            _attach_user_info(entry, current_user_info)
            results.append(entry)
            current_user_info = None

    return results


def format_baseline(issue_key: str, results: list) -> str:
    """格式化为可读字符串"""
    if not results:
        return f"{issue_key}: 未找到基准数据"

    lines = []
    lines.append(f"\n{'='*60}")
    lines.append(f"Issue: {issue_key}")
    lines.append(f"{'='*60}")
    for r in results:
        user_str = ""
        if r.get("userInfo"):
            u = r["userInfo"]
            gender = "男" if u.get("gender") == 1 else ("女" if u.get("gender") == 0 else "未知")
            user_str = f" [用户: {u.get('age','?')}岁/{gender}/{u.get('height','?')}cm/{u.get('weight','?')}kg]"
        lines.append(
            f"  #{r['result_num']}: {r['file']} | "
            f"ltSpeed={r['ltSpeed']:.6f} | lh={r['lh']:.1f} | mh={r['mh']:.1f}"
            f"{user_str}"
        )
    return "\n".join(lines)


def to_dict_all(issues_data: dict) -> dict:
    """将全部 Issue 数据整理成 { issue_key: [results] } 结构输出"""
    return issues_data


# ─── CLI ───────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="从 JIRA 评论提取旧版 SDK 乳酸阈基准值"
    )
    parser.add_argument("issue_keys", nargs="+", help="JIRA Issue key，如 COLOGNE-3107")
    parser.add_argument("-o", "--output", default=None, help="输出 JSON 文件路径")
    parser.add_argument("--table", action="store_true", help="以表格形式输出")
    args = parser.parse_args()

    all_data = {}

    for issue_key in args.issue_keys:
        print(f"正在提取 {issue_key} ...", file=sys.stderr)
        results = fetch_issue_baseline(issue_key)
        all_data[issue_key] = results

        if args.table:
            print(format_baseline(issue_key, results))
        else:
            if results:
                print(f"  {issue_key}: 提取 {len(results)} 条结果", file=sys.stderr)
            else:
                print(f"  {issue_key}: 未找到基准数据", file=sys.stderr)

    # 输出 JSON
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(all_data, f, ensure_ascii=False, indent=2)
        print(f"\n已保存至 {args.output}", file=sys.stderr)
    else:
        # 默认 stdout 输出 JSON
        print(json.dumps(all_data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

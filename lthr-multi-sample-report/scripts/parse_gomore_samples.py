#!/usr/bin/env python3
"""
parse_gomore_samples.py - 从 GoMore 日志中提取多样本仿真数据

用途：
从包含 "SAMPLE[i/j] CASE=BASE_CASE" 或 "CASE=JIRA_CASE" 的日志中提取
多轮仿真结果，生成 JSON/CSV 格式供 Feishu Base 导入。

输出：
- JSON: 结构化字段，按样本 key 为顶层键
- CSV: 扁平表，每行一个样本

用法示例：
  # 解析日志，输出 JSON
  python3 parse_gomore_samples.py /path/to/log.log -o samples.json

  # 解析日志，输出 CSV
  python3 parse_gomore_samples.py /path/to/log.log -o samples.csv

  # 指定输出格式
  python3 parse_gomore_samples.py /path/to/log.log --format csv

  # 只提取 BASE_CASE
  python3 parse_gomore_samples.py /path/to/log.log --case BASE_CASE

  # 提取所有 case，加上分组阶段
  python3 parse_gomore_samples.py /path/to/log.log --case ALL --with-group
"""

import re
import json
import csv
import sys
import os
import argparse
from collections import defaultdict
from typing import Optional


RE_SAMPLE_HEADER = re.compile(
    r"SAMPLE\s*\[(\d+)\s*/\s*(\d+)\]\s*CASE\s*=\s*(\w+)\s+SAMPLE_DIR\s*=\s*(\S+?)\s+FILE\s*=\s*(\S+)",
    re.IGNORECASE,
)
RE_USER_INFO = re.compile(
    r"User:\s*age\s*=\s*([\d.]+)\s*,\s*gender\s*=\s*(\d+)\s*,\s*height\s*=\s*([\d.]+)\s*,\s*weight\s*=\s*([\d.]+).*?hrRest\s*=\s*([\d.]+)",
    re.IGNORECASE,
)
RE_BASE_LINE = re.compile(
    r"(?:gomoreParseUGCallback|Gomore\s+output)\s+ltSpeed\s*=\s*([\d.]+)\s*lh\s*=\s*([\d.]+)\s*mh\s*=\s*([\d.]+)",
    re.IGNORECASE,
)

RE_ROUND_MARKER = re.compile(
    r"(?:\bRound\s+(\d+)(?:\s*/\s*\d+)?(?:\s+start)?\b)|(?:\bround\s*[:=]\s*(\d+)\b)|(?:第\s*(\d+)\s*[次轮])",
    re.IGNORECASE,
)

RE_220_AGE = re.compile(r"220[\s-]*age\s*(?:[:=]\s*|\s+)(\d+)", re.IGNORECASE)


def parse_log(filepath: str, case_filter: str = "BASE_CASE", with_group: bool = False) -> dict:
    """
    主解析函数：从日志中提取样本数据。
    返回 { sample_name: { fields } } 结构。
    """
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    samples: dict = {}
    current_sample: Optional[str] = None
    current_case: Optional[str] = None
    current_round: int = 1

    for i, line in enumerate(lines):
        # 检测样本头
        m = RE_SAMPLE_HEADER.search(line)
        if m:
            idx, total, case_name, sample_dir, filename = m.groups()
            current_case = case_name.upper()

            # 样本名: BASE_CASE 保留目录名本身 (01lyh..13nwr)，保证和模板 Base 对齐
            # JIRA_CASE 取 "SAMPLE_DIR/文件名不含扩展名" (一个 Issue 可能多个 CSV)
            if current_case == "JIRA_CASE":
                fname = os.path.splitext(filename)[0]
                sample_key = f"{sample_dir}/{fname}"  # 如 COLOGNE-3107/gomore_data_20260320_110740
            else:
                sample_key = sample_dir.lower()
            current_sample = sample_key
            current_round = 1

            if case_filter.upper() != "ALL" and current_case != case_filter.upper():
                current_sample = None
            continue

        if current_sample is None:
            continue

        # 检测轮次 (如果日志中有轮次标记)
        rm = RE_ROUND_MARKER.search(line)
        if rm:
            r_val = rm.group(1) or rm.group(2) or rm.group(3)
            current_round = int(r_val)

        # 检测用户信息
        ui = RE_USER_INFO.search(line)
        if ui:
            age, gender, height, weight, hr_rest = ui.groups()
            if current_sample not in samples:
                samples[current_sample] = {}
            info = samples[current_sample]
            info["年龄"] = int(float(age)) if age else None
            info["性别"] = "女" if int(float(gender)) == 1 else "男"
            info["身高"] = int(float(height)) if height else None
            info["体重"] = float(weight) if weight else None
            info["静息"] = int(float(hr_rest)) if hr_rest else None
            info["220-age"] = 220 - info["年龄"] if info.get("年龄") else None
            continue

        # 检测输出结果行
        bl = RE_BASE_LINE.search(line)
        if bl:
            lt_speed, lh, mh = bl.groups()
            if current_sample not in samples:
                samples[current_sample] = {}

            s = samples[current_sample]
            prefix = f"仿真{current_round}"

            # ltSpeed (pace 转分:秒格式)
            lt_val = float(lt_speed)
            lt_min = int(lt_val)
            lt_sec = int(round((lt_val - lt_min) * 60))
            s[f"{prefix}_LTP"] = f"{lt_min}'{lt_sec:02d}"
            s[f"{prefix}_LTP（S）"] = int(lt_val * 60)

            # LTHR = lh
            s[f"{prefix}_LTHR"] = int(float(lh))

            # MAXHR = mh
            s[f"{prefix}_MAXHR"] = int(float(mh))
            continue

    # 填充缺失轮次为 None（让所有样本有相同的键）
    _fill_missing_rounds(samples)
    return samples


def _fill_missing_rounds(samples: dict, max_round: int = 3) -> None:
    """确保每个样本有仿真1-3的键，缺失置 None。"""
    for sname, sdata in samples.items():
        for rnd in range(1, max_round + 1):
            p = f"仿真{rnd}"
            for k in ("_LTHR", "_LTP（S）", "_MAXHR", "_LTP"):
                key = p + k
                if key not in sdata:
                    sdata[key] = None
        # 基础字段默认
        for bk in ("年龄", "性别", "身高", "体重", "静息", "220-age"):
            if bk not in sdata:
                sdata[bk] = None


def sort_samples(samples: dict) -> dict:
    """按样本名数字+字母排序。"""
    def _sort_key(k):
        text = str(k)
        m = re.match(r"^(\d+)([A-Za-z].*)$", text)
        if m:
            return (0, int(m.group(1)), m.group(2).lower())
        return (1, text.lower())
    return dict(sorted(samples.items(), key=lambda x: _sort_key(x[0])))


def to_csv(samples: dict, output_path: str) -> str:
    """输出 CSV 文件。"""
    fieldnames = [
        "样本", "年龄", "性别", "身高", "体重", "静息", "220-age",
        "仿真1_LTHR", "仿真1_LTP", "仿真1_LTP（S）", "仿真1_MAXHR",
        "仿真2_LTHR", "仿真2_LTP", "仿真2_LTP（S）", "仿真2_MAXHR",
        "仿真3_LTHR", "仿真3_LTP", "仿真3_LTP（S）", "仿真3_MAXHR",
    ]
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for sname, sdata in sort_samples(samples).items():
            row = {"样本": sname}
            row.update(sdata)
            w.writerow(row)
    return output_path


def to_json(samples: dict, output_path: str) -> str:
    """输出 JSON 文件。"""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sort_samples(samples), f, ensure_ascii=False, indent=2)
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="从 GoMore 日志提取多样本仿真数据"
    )
    parser.add_argument("logfile", help="GoMore 日志文件路径")
    parser.add_argument("-o", "--output", default=None,
                        help="输出文件路径（自动推断格式）")
    parser.add_argument("--format", choices=["json", "csv"], default=None,
                        help="强制指定输出格式")
    parser.add_argument("--case", choices=["BASE_CASE", "JIRA_CASE", "ALL"],
                        default="BASE_CASE",
                        help="提取的 case 类型 (默认: BASE_CASE)")
    parser.add_argument("--with-group", action="store_true",
                        help="尝试解析样本分组")
    args = parser.parse_args()

    if not os.path.isfile(args.logfile):
        print(f"ERROR: 文件不存在: {args.logfile}", file=sys.stderr)
        sys.exit(1)

    samples = parse_log(args.logfile, case_filter=args.case)

    if not samples:
        print("WARN: 未解析到任何样本数据", file=sys.stderr)
        sys.exit(0)

    # 推断输出格式与路径
    fmt = args.format
    out_path = args.output
    if out_path is None:
        base = os.path.splitext(os.path.basename(args.logfile))[0]
        if fmt is None:
            fmt = "json"
        out_path = f"{base}_samples.{fmt}"
    else:
        if fmt is None:
            ext = os.path.splitext(out_path)[1].lower()
            fmt = "json" if ext == ".json" else "csv"

    count = len(samples)
    if fmt == "csv":
        result = to_csv(samples, out_path)
    else:
        result = to_json(samples, out_path)

    print(f"OK: 提取 {count} 个样本 -> {result}")


if __name__ == "__main__":
    main()

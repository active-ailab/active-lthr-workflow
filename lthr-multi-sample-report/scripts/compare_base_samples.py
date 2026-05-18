#!/usr/bin/env python3
"""
compare_base_samples.py - 对比两个 Feishu Base 版本的仿真样本数据，生成差异报告

用法:
  # 从两个 Base 拉数据并对比
  python3 compare_base_samples.py --old-base <old_base_token> --new-base <new_base_token> --old-table <table_id> --new-table <table_id>

  # 从本地 JSON 对比 (已拉取过)
  python3 compare_base_samples.py --old-json old.json --new-json new.json

  # 生成对比报告
  python3 compare_base_samples.py --old-json old.json --new-json new.json --report compare.md

输出:
  - Markdown 对比报告，含执行摘要、字段变化统计、样本逐项差异
"""

import json
import sys
import os
import argparse
from collections import defaultdict

from _report_utils import _payload_to_records


def load_records(path_or_token: str, is_table: bool = False) -> dict:
    """加载记录，从 JSON 文件或通过 lark-cli 拉取。"""
    if os.path.isfile(path_or_token):
        with open(path_or_token, "r", encoding="utf-8") as f:
            data = json.load(f)
            # 兼容 { data: { fields: [...], data: [[...]] } } 结构
            if isinstance(data, dict) and "data" in data:
                d2 = data["data"]
                if isinstance(d2, dict) and "data" in d2 and "fields" in d2:
                    return _payload_to_records(d2)
                return data
            return data
    else:
        print(f"ERROR: 文件不存在: {path_or_token}", file=sys.stderr)
        print("提示: 请先通过 lark-cli base +record-list 拉取数据存为 JSON", file=sys.stderr)
        sys.exit(1)


def compare(old: dict, new: dict) -> dict:
    """对比两个版本的样本，返回差异结构。"""
    all_keys = set(old.keys()) | set(new.keys())
    common = set(old.keys()) & set(new.keys())
    only_old = set(old.keys()) - set(new.keys())
    only_new = set(new.keys()) - set(old.keys())

    all_fields = set()
    for s in common:
        all_fields |= set(old[s].keys()) | set(new[s].keys())
    all_fields.discard("样本")
    all_fields.discard("sample")

    field_changes = defaultdict(list)   # field -> [sample_names]
    sample_diffs = {}                    # sample_name -> {field: (old_val, new_val)}

    for s in common:
        diffs = {}
        for f in all_fields:
            ov = old[s].get(f)
            nv = new[s].get(f)
            # 处理 None vs 0 的差异
            if ov is None and nv is None:
                continue
            if ov is None:
                ov_str = ""
            elif isinstance(ov, float):
                ov_str = str(ov)
            else:
                ov_str = str(ov)

            if nv is None:
                nv_str = ""
            elif isinstance(nv, float):
                nv_str = str(nv)
            else:
                nv_str = str(nv)

            if ov_str != nv_str:
                field_changes[f].append(s)
                diffs[f] = (ov, nv)

        if diffs:
            sample_diffs[s] = diffs

    return {
        "old_count": len(old),
        "new_count": len(new),
        "common": len(common),
        "only_old": sorted(only_old),
        "only_new": sorted(only_new),
        "changed_count": len(sample_diffs),
        "unchanged_count": len(common) - len(sample_diffs),
        "field_changes": dict(sorted(field_changes.items(),
                                      key=lambda x: (-len(x[1]), x[0]))),
        "sample_diffs": dict(sorted(sample_diffs.items())),
    }


def generate_report(result: dict, old_label: str, new_label: str) -> str:
    """生成 Markdown 对比报告。"""
    lines = []
    lines.append(f"# {old_label} vs {new_label} 仿真结果对比报告\n")
    lines.append("## 执行摘要（简明结论）\n")
    lines.append(f"- 对比范围：{old_label} → {new_label}")
    lines.append(f"- 旧版样本数: {result['old_count']}")
    lines.append(f"- 新版样本数: {result['new_count']}")
    lines.append(f"- 共同样本数: {result['common']}")
    lines.append(f"- 有变化样本数: {result['changed_count']}")
    lines.append(f"- 无变化样本数: {result['unchanged_count']}")
    lines.append(f"- 仅旧版存在: {len(result['only_old'])}")
    lines.append(f"- 仅新版存在: {len(result['only_new'])}")

    # 字段变化统计
    lines.append("\n## 字段变化统计\n")
    lines.append("| 字段 | 变化样本数 | 样本 |")
    lines.append("| --- | ---: | --- |")
    fc_sorted = sorted(result["field_changes"].items(),
                       key=lambda x: (-len(x[1]), x[0]))
    for field, samples in fc_sorted:
        samples_str = ", ".join(samples[:10])
        if len(samples) > 10:
            samples_str += f", ... (+{len(samples)-10})"
        lines.append(f"| {field} | {len(samples)} | {samples_str} |")

    # 样本逐项差异
    lines.append("\n## 样本逐项差异\n")
    for sname, diffs in result["sample_diffs"].items():
        lines.append(f"### {sname}")
        lines.append(f"- 差异字段数: {len(diffs)}\n")
        lines.append("| 字段 | 旧版 | 新版 |")
        lines.append("| --- | --- | --- |")
        for field, (ov, nv) in sorted(diffs.items()):
            lines.append(f"| {field} | {_fmt(ov)} | {_fmt(nv)} |")
        lines.append("")

    return "\n".join(lines)


def _fmt(val) -> str:
    if val is None:
        return ""
    if isinstance(val, float):
        return str(val)
    return str(val)


def main():
    parser = argparse.ArgumentParser(description="对比两个版本的 Feishu Base 样本数据")
    parser.add_argument("--old-json", help="旧版 JSON 文件路径")
    parser.add_argument("--new-json", help="新版 JSON 文件路径")
    parser.add_argument("--old-label", default="SDK 旧版", help="旧版名称标签")
    parser.add_argument("--new-label", default="SDK 新版", help="新版名称标签")
    parser.add_argument("--report", default=None, help="输出对比报告路径 (.md)")
    parser.add_argument("--json-diff", default=None, help="输出差异 JSON 路径")

    args = parser.parse_args()

    if not args.old_json or not args.new_json:
        parser.print_help()
        print("\n错误: --old-json 和 --new-json 是必需的", file=sys.stderr)
        sys.exit(1)

    old_records = load_records(args.old_json)
    new_records = load_records(args.new_json)

    result = compare(old_records, new_records)

    # 打印统计
    print(f"旧版样本: {result['old_count']}")
    print(f"新版样本: {result['new_count']}")
    print(f"共同样本: {result['common']}")
    print(f"有变化: {result['changed_count']}")
    print(f"无变化: {result['unchanged_count']}")

    if result["only_old"]:
        print(f"仅旧版存在: {', '.join(result['only_old'])}")
    if result["only_new"]:
        print(f"仅新版存在: {', '.join(result['only_new'])}")

    if args.report:
        report = generate_report(result, args.old_label, args.new_label)
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"报告已生成: {args.report}")

    if args.json_diff:
        with open(args.json_diff, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"差异 JSON: {args.json_diff}")


if __name__ == "__main__":
    main()

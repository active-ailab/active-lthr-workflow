#!/usr/bin/env python3
"""
generate_sim_report.py - 生成 SDK 单版本仿真测试结果报告 (Markdown)

功能:
  从 Feishu Base (JSON dump 或 live pull) 读取样本数据，生成单版本仿真测试结果报告，
  包含仿真结果总表、一致性分析和各样本详细结果。

用法:
  # 从本地 JSON 生成
  python3 generate_sim_report.py --json data.json --sdk-version "1.0.15.0" -o report.md

  # 从飞书 Base 直接拉取生成
  python3 generate_sim_report.py --base-token <token> --table-id <id> --sdk-version "1.0.15.0" -o report.md

  # 使用 --all-fields 输出包含 Garmin/DUT 字段的完整数据表
  python3 generate_sim_report.py --json data.json --sdk-version "1.0.15.0" --all-fields -o report.md
"""

import json
import os
import sys
import argparse

from _report_utils import _payload_to_records, parse_pace_to_seconds, pace_format, run_lark


def _fix_val(v):
    """统一处理空值"""
    if v is None or v == "":
        return None
    return v


def _display(v):
    """将缺失值渲染为空单元格，同时保留数值 0。"""
    return "" if v is None or v == "" else v


def load_from_json(json_path):
    """从本地 JSON 文件加载数据，兼容多种格式"""
    with open(json_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # 兼容 { data: { fields: [...], data: [[...]] } } 结构 (lark-cli record-list 输出)
    if isinstance(raw, dict) and "data" in raw:
        d2 = raw["data"]
        if isinstance(d2, dict) and "data" in d2 and "fields" in d2:
            return _payload_to_records(d2, value_normalizer=_fix_val)
        # 已经是 { "01lyh": {...}, ... } 格式
        if all(isinstance(v, dict) for v in d2.values()):
            return d2
        return raw

    # 已经是 { "01lyh": {...}, ... } 格式
    if all(isinstance(v, dict) for v in raw.values()):
        # 将样本名作为字段写入
        for k, v in raw.items():
            if "样本" not in v:
                v["样本"] = k
        return raw

    return raw


def load_from_base(base_token, table_id):
    """通过 lark-cli 拉取 Base 数据"""
    result = run_lark([
        "base", "+record-list",
        "--base-token", base_token,
        "--table-id", table_id,
    ], force_json=True)
    if not result.get("ok"):
        print(f"lark-cli 错误: {result.get('error', '')}", file=sys.stderr)
        sys.exit(1)
    payload = result.get("data", {})
    return _payload_to_records(payload, value_normalizer=_fix_val)


def generate_report(raw_data, sdk_version, all_fields=False, data_source="本地 JSON 数据"):
    """生成完整 Markdown 报告"""
    lines = []
    lines.append(f"# SDK {sdk_version} 版本仿真测试结果")
    lines.append("")
    lines.append(f"> 生成时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"> 样本数: {len(raw_data)} 个")
    lines.append(f"> 数据来源: {data_source}")
    lines.append(f"> 说明: 本报告展示 SDK v{sdk_version} 在 {len(raw_data)} 个样本上的 3 轮仿真测试结果，")
    lines.append("> 包含乳酸阈值心率(LTHR)、配速(LTP)、配速秒数(LTP(S))、最大心率(MAXHR) 四个核心指标。")
    lines.append("")

    # === 1. 样本概览 ===
    lines.append("## 1. 样本概览")
    lines.append("")
    lines.append("| 样本 | 年龄 | 性别 | 身高(cm) | 体重(kg) | 静息心率 | 220-age | 分组 |")
    lines.append("|------|:---:|:----:|:--------:|:--------:|:--------:|:-------:|:----:|")
    for sid in sorted(raw_data.keys()):
        rec = raw_data[sid]
        lines.append(f"| {sid} | {_display(rec.get('年龄'))} | {_display(rec.get('性别'))} | "
                     f"{_display(rec.get('身高'))} | {_display(rec.get('体重'))} | {_display(rec.get('静息'))} | "
                     f"{_display(rec.get('220-age'))} | {_display(rec.get('样本分组', rec.get('group', '')))} |")
    lines.append("")

    # === 2. 仿真结果总表 ===
    lines.append("## 2. 仿真结果总表")
    lines.append("")
    lines.append("### 2.1 乳酸阈值心率 (LTHR)")
    lines.append("")
    lines.append("| 样本 | 仿真1 | 仿真2 | 仿真3 | 均值 | 极差 | 一致性 |")
    lines.append("|------|:----:|:----:|:----:|:----:|:----:|:------:|")
    for sid in sorted(raw_data.keys()):
        rec = raw_data[sid]
        v1 = rec.get("仿真1_LTHR")
        v2 = rec.get("仿真2_LTHR")
        v3 = rec.get("仿真3_LTHR")
        vals = [v for v in [v1, v2, v3] if v is not None]
        avg = round(sum(vals) / len(vals), 1) if vals else ""
        rng = max(vals) - min(vals) if len(vals) > 1 else 0
        consistent = "✅" if rng <= 1 else "❌"
        lines.append(f"| {sid} | {v1 or ''} | {v2 or ''} | {v3 or ''} | {avg} | {rng} | {consistent} |")
    lines.append("")

    lines.append("### 2.2 配速 (LTP)")
    lines.append("")
    lines.append("| 样本 | 仿真1 | 仿真2 | 仿真3 | 均值 | 极差(s) | 一致性 |")
    lines.append("|------|:----:|:----:|:----:|:----:|:-------:|:------:|")
    for sid in sorted(raw_data.keys()):
        rec = raw_data[sid]
        v1 = parse_pace_to_seconds(rec.get("仿真1_LTP"))
        v2 = parse_pace_to_seconds(rec.get("仿真2_LTP"))
        v3 = parse_pace_to_seconds(rec.get("仿真3_LTP"))
        vals = [v for v in [v1, v2, v3] if v is not None]
        avg_s = round(sum(vals) / len(vals)) if vals else 0
        rng = max(vals) - min(vals) if len(vals) > 1 else 0
        consistent = "✅" if rng <= 2 else "❌"
        lines.append(f"| {sid} | {_display(rec.get('仿真1_LTP'))} | {_display(rec.get('仿真2_LTP'))} | "
                     f"{_display(rec.get('仿真3_LTP'))} | {pace_format(avg_s) if vals else ''} | {rng} | {consistent} |")
    lines.append("")

    lines.append("### 2.3 配速秒数 (LTP(S))")
    lines.append("")
    lines.append("| 样本 | 仿真1 | 仿真2 | 仿真3 | 均值 | 极差 | 一致性 |")
    lines.append("|------|:----:|:----:|:----:|:----:|:----:|:------:|")
    for sid in sorted(raw_data.keys()):
        rec = raw_data[sid]
        v1 = rec.get("仿真1_LTP（S）")
        v2 = rec.get("仿真2_LTP（S）")
        v3 = rec.get("仿真3_LTP（S）")
        vals = [v for v in [v1, v2, v3] if v is not None]
        avg = round(sum(vals) / len(vals), 1) if vals else ""
        rng = max(vals) - min(vals) if len(vals) > 1 else 0
        consistent = "✅" if rng <= 5 else "❌"
        lines.append(f"| {sid} | {v1 or ''} | {v2 or ''} | {v3 or ''} | {avg} | {rng} | {consistent} |")
    lines.append("")

    lines.append("### 2.4 最大心率 (MAXHR)")
    lines.append("")
    lines.append("| 样本 | 仿真1 | 仿真2 | 仿真3 | 均值 | 极差 | 一致性 |")
    lines.append("|------|:----:|:----:|:----:|:----:|:----:|:------:|")
    for sid in sorted(raw_data.keys()):
        rec = raw_data[sid]
        v1 = rec.get("仿真1_MAXHR")
        v2 = rec.get("仿真2_MAXHR")
        v3 = rec.get("仿真3_MAXHR")
        vals = [v for v in [v1, v2, v3] if v is not None]
        avg = round(sum(vals) / len(vals), 1) if vals else ""
        rng = max(vals) - min(vals) if len(vals) > 1 else 0
        consistent = "✅" if rng <= 1 else "❌"
        lines.append(f"| {sid} | {v1 or ''} | {v2 or ''} | {v3 or ''} | {avg} | {rng} | {consistent} |")
    lines.append("")

    # === 3. 仿真一致性分析 ===
    lines.append("## 3. 仿真一致性分析")
    lines.append("")
    lines.append("### 3.1 各指标三轮一致性统计")
    lines.append("")
    lines.append("| 样本 | LTHR | MAXHR | LTP | LTP(S) | 全部一致 |")
    lines.append("|------|:----:|:-----:|:---:|:------:|:--------:|")
    all_consistent = 0
    for sid in sorted(raw_data.keys()):
        rec = raw_data[sid]
        # LTHR
        lthr_vals = [rec.get(f"仿真{i}_LTHR") for i in range(1, 4) if rec.get(f"仿真{i}_LTHR") is not None]
        lthr_ok = (max(lthr_vals) - min(lthr_vals) <= 1) if len(lthr_vals) > 1 else True
        # MAXHR
        maxhr_vals = [rec.get(f"仿真{i}_MAXHR") for i in range(1, 4) if rec.get(f"仿真{i}_MAXHR") is not None]
        maxhr_ok = (max(maxhr_vals) - min(maxhr_vals) <= 1) if len(maxhr_vals) > 1 else True
        # LTP
        ltp_vals = [parse_pace_to_seconds(rec.get(f"仿真{i}_LTP")) for i in range(1, 4) if rec.get(f"仿真{i}_LTP")]
        ltp_vals = [v for v in ltp_vals if v is not None]
        ltp_ok = (max(ltp_vals) - min(ltp_vals) <= 2) if len(ltp_vals) > 1 else True
        # LTP(S)
        ltps_vals = [rec.get(f"仿真{i}_LTP（S）") for i in range(1, 4) if rec.get(f"仿真{i}_LTP（S）") is not None]
        ltps_ok = (max(ltps_vals) - min(ltps_vals) <= 5) if len(ltps_vals) > 1 else True

        all_ok = lthr_ok and maxhr_ok and ltp_ok and ltps_ok
        if all_ok:
            all_consistent += 1
        lines.append(f"| {sid} | {'✅' if lthr_ok else '❌'} | {'✅' if maxhr_ok else '❌'} | "
                     f"{'✅' if ltp_ok else '❌'} | {'✅' if ltps_ok else '❌'} | "
                     f"{'✅' if all_ok else '❌'} |")
    lines.append("")

    # === 3.2 一致性汇总 ===
    lines.append("### 3.2 一致性汇总")
    lines.append("")
    lines.append(f"- **全部四项指标均一致**: {all_consistent}/{len(raw_data)} 样本")
    lines.append(f"- **一致性率**: {all_consistent * 100 // len(raw_data)}%")
    lines.append("")

    # === 4. 各样本详细结果 ===
    lines.append("## 4. 各样本详细结果")
    lines.append("")

    for sid in sorted(raw_data.keys()):
        rec = raw_data[sid]
        lines.append(f"### {sid}")
        lines.append("")
        lines.append("| 指标 | 仿真1 | 仿真2 | 仿真3 | 均值 |")
        lines.append("|------|:----:|:----:|:----:|:----:|")

        # LTHR
        l1 = rec.get("仿真1_LTHR")
        l2 = rec.get("仿真2_LTHR")
        l3 = rec.get("仿真3_LTHR")
        l_vals = [v for v in [l1, l2, l3] if v is not None]
        l_avg = round(sum(l_vals) / len(l_vals), 0) if l_vals else ""
        lines.append(f"| LTHR (bpm) | {l1 or ''} | {l2 or ''} | {l3 or ''} | {l_avg} |")

        # LTP
        p1 = rec.get("仿真1_LTP")
        p2 = rec.get("仿真2_LTP")
        p3 = rec.get("仿真3_LTP")
        p_vals = [parse_pace_to_seconds(rec.get(f"仿真{i}_LTP")) for i in range(1, 4) if rec.get(f"仿真{i}_LTP")]
        p_vals = [v for v in p_vals if v is not None]
        p_avg = pace_format(round(sum(p_vals) / len(p_vals))) if p_vals else ""
        lines.append(f"| LTP (配速) | {p1 or ''} | {p2 or ''} | {p3 or ''} | {p_avg} |")

        # LTP(S)
        s1 = rec.get("仿真1_LTP（S）")
        s2 = rec.get("仿真2_LTP（S）")
        s3 = rec.get("仿真3_LTP（S）")
        s_vals = [v for v in [s1, s2, s3] if v is not None]
        s_avg = round(sum(s_vals) / len(s_vals), 0) if s_vals else ""
        lines.append(f"| LTP(S) (秒) | {s1 or ''} | {s2 or ''} | {s3 or ''} | {s_avg} |")

        # MAXHR
        m1 = rec.get("仿真1_MAXHR")
        m2 = rec.get("仿真2_MAXHR")
        m3 = rec.get("仿真3_MAXHR")
        m_vals = [v for v in [m1, m2, m3] if v is not None]
        m_avg = round(sum(m_vals) / len(m_vals), 0) if m_vals else ""
        lines.append(f"| MAXHR (bpm) | {m1 or ''} | {m2 or ''} | {m3 or ''} | {m_avg} |")
        lines.append("")

    # === 可选: 5. 完整数据表 ===
    if all_fields:
        lines.append("## 5. 完整数据表 (Base 快照)")
        lines.append("")
        lines.append("| 样本 | 年龄 | 性别 | 身高 | 体重 | 静息 | 220-age | "
                     "仿真1_LTHR | 仿真2_LTHR | 仿真3_LTHR | "
                     "仿真1_LTP | 仿真2_LTP | 仿真3_LTP | "
                     "仿真1_LTP(S) | 仿真2_LTP(S) | 仿真3_LTP(S) | "
                     "仿真1_MAXHR | 仿真2_MAXHR | 仿真3_MAXHR |")
        lines.append("|------|:---:|:----:|:----:|:----:|:----:|:-------:|"
                     ":--------:|:--------:|:--------:|"
                     ":--------:|:--------:|:--------:|"
                     ":--------:|:--------:|:--------:|"
                     ":--------:|:--------:|:--------:|")
        for sid in sorted(raw_data.keys()):
            rec = raw_data[sid]
            lines.append(
                f"| {sid} | {_display(rec.get('年龄'))} | {_display(rec.get('性别'))} | "
                f"{_display(rec.get('身高'))} | {_display(rec.get('体重'))} | {_display(rec.get('静息'))} | {_display(rec.get('220-age'))} | "
                f"{_display(rec.get('仿真1_LTHR'))} | {_display(rec.get('仿真2_LTHR'))} | {_display(rec.get('仿真3_LTHR'))} | "
                f"{_display(rec.get('仿真1_LTP'))} | {_display(rec.get('仿真2_LTP'))} | {_display(rec.get('仿真3_LTP'))} | "
                f"{_display(rec.get('仿真1_LTP（S）'))} | {_display(rec.get('仿真2_LTP（S）'))} | {_display(rec.get('仿真3_LTP（S）'))} | "
                f"{_display(rec.get('仿真1_MAXHR'))} | {_display(rec.get('仿真2_MAXHR'))} | {_display(rec.get('仿真3_MAXHR'))} |"
            )

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"*报告由自动化工具生成 | SDK v{sdk_version} | 三仿真轮次测试验证*")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="生成 SDK 单版本仿真测试结果报告")
    parser.add_argument("--json", help="本地 JSON 数据文件路径")
    parser.add_argument("--base-token", help="飞书 Base token")
    parser.add_argument("--table-id", help="飞书 Base 表 ID")
    parser.add_argument("--sdk-version", required=True, help="SDK 版本号 (如 1.0.15.0)")
    parser.add_argument("-o", "--output", required=True, help="输出 Markdown 文件路径")
    parser.add_argument("--all-fields", action="store_true", help="输出包含完整字段的数据表")
    parser.add_argument("--data-source", default=None, help="报告中展示的数据来源说明")
    args = parser.parse_args()

    if args.json:
        raw_data = load_from_json(args.json)
    elif args.base_token and args.table_id:
        raw_data = load_from_base(args.base_token, args.table_id)
    else:
        print("错误: 需要 --json 或 (--base-token + --table-id)", file=sys.stderr)
        sys.exit(1)

    data_source = args.data_source or ("本地 JSON 数据" if args.json else "Feishu Base - 乳酸阈多样本仿真测试结果")
    report = generate_report(raw_data, args.sdk_version, all_fields=args.all_fields, data_source=data_source)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"报告已生成: {args.output}")
    print(f"共 {len(raw_data)} 个样本 | SDK v{args.sdk_version}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Garmin vs DUT vs SDK Simulation 三源对比报告生成器
从本地 JSON 数据读取样本数据，生成包含 Garmin 基准、DUT 实测、SDK 仿真的三源对比分析报告。

用法:
  python3 generate_garmin_dut_compare_report.py --json data.json --sdk-version "1.0.15.0" -o report.md
  python3 generate_garmin_dut_compare_report.py --json data.json --sdk-version "1.0.15.0" --report-date 2026-04-30 --base-url "https://xxx.feishu.cn/base/..." -o report.md
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

from _report_utils import _payload_to_records, parse_pace_to_seconds, pace_format


def _fix_val(value):
    if value is None or value == "":
        return None
    return value


def pace_diff(p1, p2):
    """计算配速差值 (秒)，正数表示 p1 比 p2 慢。"""
    s1 = parse_pace_to_seconds(p1)
    s2 = parse_pace_to_seconds(p2)
    if s1 is None or s2 is None:
        return None
    return s1 - s2


def load_records(json_path):
    """从 JSON 文件加载记录，兼容 record-list、list[dict]、dict[dict]。"""
    with open(json_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    if isinstance(raw_data, dict) and "data" in raw_data:
        payload = raw_data["data"]
        if isinstance(payload, dict) and "fields" in payload and "data" in payload:
            return list(_payload_to_records(payload, value_normalizer=_fix_val).values())
        if isinstance(payload, dict) and all(isinstance(v, dict) for v in payload.values()):
            records = []
            for sample_key, record in payload.items():
                if "样本" not in record:
                    record = dict(record)
                    record["样本"] = sample_key
                records.append(record)
            return records

    if isinstance(raw_data, list) and all(isinstance(v, dict) for v in raw_data):
        return raw_data

    if isinstance(raw_data, dict) and all(isinstance(v, dict) for v in raw_data.values()):
        records = []
        for sample_key, record in raw_data.items():
            if "样本" not in record:
                record = dict(record)
                record["样本"] = sample_key
            records.append(record)
        return records

    print(f"错误: 无法解析 JSON 格式 (类型: {type(raw_data).__name__})", file=sys.stderr)
    sys.exit(1)


def analyze_garmin_dut_diff(records):
    analysis = []
    for rec in records:
        diff_lthr = None
        if rec.get("Garmin_LTHR") is not None and rec.get("DUT_LTHR") is not None:
            diff_lthr = rec["DUT_LTHR"] - rec["Garmin_LTHR"]

        diff_ltp = pace_diff(rec.get("DUT_LTP"), rec.get("Garmin_LTP"))
        diff_ltp_s = None
        if rec.get("Garmin_LTP(S)") is not None and rec.get("DUT_LTP（S）") is not None:
            diff_ltp_s = rec["DUT_LTP（S）"] - rec["Garmin_LTP(S)"]

        diff_hrmax = None
        if rec.get("Garmin_HRmax") is not None and rec.get("DUT_HRmax") is not None:
            diff_hrmax = rec["DUT_HRmax"] - rec["Garmin_HRmax"]

        analysis.append({
            "样本": rec["样本"],
            "diff_LTHR": diff_lthr,
            "diff_LTP_sec": diff_ltp,
            "diff_LTP(S)": diff_ltp_s,
            "diff_HRmax": diff_hrmax,
        })
    return analysis


def analyze_simulation_consistency(records):
    analysis = []
    for rec in records:
        lthr_vals = [rec.get(f"仿真{i}_LTHR") for i in range(1, 4)]
        lthr_vals = [v for v in lthr_vals if v is not None]

        maxhr_vals = [rec.get(f"仿真{i}_MAXHR") for i in range(1, 4)]
        maxhr_vals = [v for v in maxhr_vals if v is not None]

        ltp_vals = [parse_pace_to_seconds(rec.get(f"仿真{i}_LTP")) for i in range(1, 4)]
        ltp_vals = [v for v in ltp_vals if v is not None]

        ltp_s_vals = [rec.get(f"仿真{i}_LTP（S）") for i in range(1, 4)]
        ltp_s_vals = [v for v in ltp_s_vals if v is not None]

        analysis.append({
            "样本": rec["样本"],
            "LTHR_range": max(lthr_vals) - min(lthr_vals) if len(lthr_vals) > 1 else 0,
            "LTHR_consistent": max(lthr_vals) - min(lthr_vals) <= 1 if len(lthr_vals) > 1 else True,
            "MAXHR_range": max(maxhr_vals) - min(maxhr_vals) if len(maxhr_vals) > 1 else 0,
            "MAXHR_consistent": max(maxhr_vals) - min(maxhr_vals) <= 1 if len(maxhr_vals) > 1 else True,
            "LTP_range_sec": max(ltp_vals) - min(ltp_vals) if len(ltp_vals) > 1 else 0,
            "LTP_consistent": max(ltp_vals) - min(ltp_vals) <= 2 if len(ltp_vals) > 1 else True,
            "LTP(S)_range": max(ltp_s_vals) - min(ltp_s_vals) if len(ltp_s_vals) > 1 else 0,
            "LTP(S)_consistent": max(ltp_s_vals) - min(ltp_s_vals) <= 5 if len(ltp_s_vals) > 1 else True,
        })
    return analysis


def analyze_dut_vs_simulation(records):
    analysis = []
    for rec in records:
        sim_lthr = [rec.get(f"仿真{i}_LTHR") for i in range(1, 4)]
        sim_lthr = [v for v in sim_lthr if v is not None]
        avg_sim_lthr = sum(sim_lthr) / len(sim_lthr) if sim_lthr else None

        sim_ltp = [parse_pace_to_seconds(rec.get(f"仿真{i}_LTP")) for i in range(1, 4)]
        sim_ltp = [v for v in sim_ltp if v is not None]
        avg_sim_ltp = sum(sim_ltp) / len(sim_ltp) if sim_ltp else None

        sim_ltp_s = [rec.get(f"仿真{i}_LTP（S）") for i in range(1, 4)]
        sim_ltp_s = [v for v in sim_ltp_s if v is not None]
        avg_sim_ltp_s = sum(sim_ltp_s) / len(sim_ltp_s) if sim_ltp_s else None

        sim_maxhr = [rec.get(f"仿真{i}_MAXHR") for i in range(1, 4)]
        sim_maxhr = [v for v in sim_maxhr if v is not None]
        avg_sim_maxhr = sum(sim_maxhr) / len(sim_maxhr) if sim_maxhr else None

        diff_lthr = None
        if rec.get("DUT_LTHR") is not None and avg_sim_lthr is not None:
            diff_lthr = rec["DUT_LTHR"] - avg_sim_lthr

        diff_ltp = None
        if rec.get("DUT_LTP") and avg_sim_ltp is not None:
            dut_ltp_s = parse_pace_to_seconds(rec["DUT_LTP"])
            if dut_ltp_s is not None:
                diff_ltp = dut_ltp_s - avg_sim_ltp

        diff_ltp_s = None
        if rec.get("DUT_LTP（S）") is not None and avg_sim_ltp_s is not None:
            diff_ltp_s = rec["DUT_LTP（S）"] - avg_sim_ltp_s

        analysis.append({
            "样本": rec["样本"],
            "avg_sim_LTHR": round(avg_sim_lthr, 1) if avg_sim_lthr is not None else None,
            "DUT_vs_Sim_LTHR": round(diff_lthr, 1) if diff_lthr is not None else None,
            "avg_sim_LTP": pace_format(avg_sim_ltp) if avg_sim_ltp is not None else None,
            "DUT_vs_Sim_LTP_sec": round(diff_ltp, 1) if diff_ltp is not None else None,
            "avg_sim_MAXHR": round(avg_sim_maxhr, 1) if avg_sim_maxhr is not None else None,
        })
    return analysis


def classify_error(diff, metric="LTHR"):
    if diff is None:
        return "N/A"
    thresholds = {
        "LTHR": (2, 5, 999),
        "HRmax": (2, 5, 999),
        "LTP(S)": (10, 30, 999),
    }
    first, second, _ = thresholds.get(metric, (1, 3, 999))
    if abs(diff) <= first:
        return "✅ 优秀"
    if abs(diff) <= second:
        return "⚠️ 可接受"
    return "❌ 偏差大"


def generate_report(records, sdk_version="?", report_date=None, base_url=""):
    if report_date is None:
        report_date = datetime.datetime.now().strftime("%Y-%m-%d")

    gd_analysis = analyze_garmin_dut_diff(records)
    sim_cons = analyze_simulation_consistency(records)
    dut_sim = analyze_dut_vs_simulation(records)

    lthr_diffs = [a["diff_LTHR"] for a in gd_analysis if a["diff_LTHR"] is not None]
    consistent_count = sum(
        1
        for s in sim_cons
        if s["LTHR_consistent"] and s["MAXHR_consistent"] and s["LTP_consistent"] and s["LTP(S)_consistent"]
    )
    inconsistent = [
        s["样本"]
        for s in sim_cons
        if not (s["LTHR_consistent"] and s["MAXHR_consistent"] and s["LTP_consistent"] and s["LTP(S)_consistent"])
    ]
    dut_lthr_diffs = [ds["DUT_vs_Sim_LTHR"] for ds in dut_sim if ds["DUT_vs_Sim_LTHR"] is not None]

    lines = []
    lines.append(f"# Garmin vs DUT vs SDK 仿真三源对比报告 (SDK v{sdk_version})")
    lines.append("")
    lines.append(f"> 生成时间: {report_date}")
    lines.append("> 数据来源: Feishu Base - 乳酸阈多样本仿真测试结果")
    lines.append(f"> 样本数: {len(records)} 个")
    lines.append("")
    lines.append(f"> **说明**: 本报告中 \"仿真_1\" 表示被测 SDK (v{sdk_version}) 的首次仿真结果，")
    lines.append("> 用于与 Garmin 基准值及 DUT (设备端) 实测值进行三源对比。")
    lines.append("> 收敛分析: \"Garmin-仿真1\" 差值绝对值小于 \"Garmin-DUT\" 差值绝对值时标绿色，")
    lines.append("> 表明 SDK 仿真结果相比 DUT 更接近 Garmin 基准；否则标红，表示有劣化。")

    lines.append("")
    lines.append("## 综合结论")
    lines.append("")
    lines.append("### 1. Garmin vs DUT vs 仿真_1")
    if lthr_diffs:
        avg_diff = sum(lthr_diffs) / len(lthr_diffs)
        abs_avg = sum(abs(d) for d in lthr_diffs) / len(lthr_diffs)
        within_2 = sum(1 for d in lthr_diffs if abs(d) <= 2)
        within_5 = sum(1 for d in lthr_diffs if abs(d) <= 5)
        lines.append(f"- **LTHR**: DUT 与 Garmin 平均误差 {avg_diff:+.1f} bpm，平均绝对误差 {abs_avg:.1f} bpm")
        lines.append(f"- 误差 ≤2bpm: {within_2}/{len(lthr_diffs)} ({within_2 * 100 // len(lthr_diffs)}%)")
        lines.append(f"- 误差 ≤5bpm: {within_5}/{len(lthr_diffs)} ({within_5 * 100 // len(lthr_diffs)}%)")
        lines.append("- **LTHR 仿真_1 vs DUT/Garmin**: 仿真_1 与 Garmin/DUT 的偏差详见逐项表")

    lines.append("")
    lines.append("### 2. SDK 仿真一致性")
    lines.append(f"- **三轮仿真一致性**: {consistent_count}/{len(sim_cons)} 样本全部指标一致")
    if inconsistent:
        lines.append(f"- **需关注样本**: {', '.join(inconsistent)}")

    lines.append("")
    lines.append("### 3. DUT vs SDK 仿真验证")
    if dut_lthr_diffs:
        avg_dut_lthr = sum(dut_lthr_diffs) / len(dut_lthr_diffs)
        lines.append(f"- **LTHR**: DUT 与 SDK 仿真平均差异 {avg_dut_lthr:+.1f} bpm")
        lines.append("- DUT 值与仿真均值的差异详见 DUT vs SDK 仿真均值对比章节")

    lines.append("")
    lines.append("## 1. 样本概览")
    lines.append("")
    lines.append("| 样本 | 年龄 | 性别 | 身高 | 体重 | 静息 | 220-age | 分组 |")
    lines.append("|------|------|------|------|------|------|---------|------|")
    for rec in records:
        lines.append(
            f"| {rec['样本']} | {rec.get('年龄', '')} | {rec.get('性别', '')} | "
            f"{rec.get('身高', '')} | {rec.get('体重', '')} | {rec.get('静息', '')} | "
            f"{rec.get('220-age', '')} | {rec.get('样本分组', '')} |"
        )

    lines.append("")
    lines.append("## 2. Garmin vs DUT vs 仿真_1 对比")
    lines.append("")
    lines.append("### 2.1 LTHR 对比")
    lines.append("")
    lines.append("| 样本 | Garmin_LTHR | DUT_LTHR | 仿真_1_LTHR | Garmin-DUT | Garmin-仿真1 | DUT-仿真1 |")
    lines.append("|------|------------|----------|------------|------------|-------------|-----------|")
    for rec in records:
        g = rec.get("Garmin_LTHR", "")
        d = rec.get("DUT_LTHR", "")
        s1 = rec.get("仿真1_LTHR", "")
        gd = d - g if (g is not None and d is not None and g != "" and d != "") else None
        gs1 = s1 - g if (g is not None and s1 is not None and g != "" and s1 != "") else None
        ds1 = s1 - d if (d is not None and s1 is not None and d != "" and s1 != "") else None
        gd_s = f"{gd:+d}" if gd is not None else "N/A"
        if gd is not None and gs1 is not None:
            color = "green" if abs(gs1) < abs(gd) else "red"
            gs1_s = f"<span style='color:{color}'>{gs1:+d}</span>"
        else:
            gs1_s = "N/A"
        ds1_s = f"{ds1:+d}" if ds1 is not None else "N/A"
        lines.append(f"| {rec['样本']} | {g} | {d} | {s1} | {gd_s} | {gs1_s} | {ds1_s} |")

    lines.append("")
    lines.append("### 2.2 配速 (LTP) 对比")
    lines.append("")
    lines.append("| 样本 | Garmin_LTP | DUT_LTP | 仿真_1_LTP | Garmin-DUT(s) | Garmin-仿真1(s) | DUT-仿真1(s) |")
    lines.append("|------|------------|---------|------------|---------------|----------------|---------------|")
    for rec in records:
        g = rec.get("Garmin_LTP", "")
        d = rec.get("DUT_LTP", "")
        s1 = rec.get("仿真1_LTP", "")
        gd = pace_diff(d, g)
        gs1 = pace_diff(s1, g)
        ds1 = pace_diff(s1, d)
        gd_s = f"{gd:+d}" if gd is not None else "N/A"
        if gd is not None and gs1 is not None:
            color = "green" if abs(gs1) < abs(gd) else "red"
            gs1_s = f"<span style='color:{color}'>{gs1:+d}</span>"
        else:
            gs1_s = "N/A"
        ds1_s = f"{ds1:+d}" if ds1 is not None else "N/A"
        lines.append(f"| {rec['样本']} | {g} | {d} | {s1} | {gd_s}s | {gs1_s}s | {ds1_s}s |")

    lines.append("")
    lines.append("### 2.3 LTP(S) 对比")
    lines.append("")
    lines.append("| 样本 | Garmin_LTP(S) | DUT_LTP(S) | 仿真_1_LTP(S) | Garmin-DUT | Garmin-仿真1 | DUT-仿真1 |")
    lines.append("|------|--------------|------------|---------------|------------|-------------|-----------|")
    for rec in records:
        g = rec.get("Garmin_LTP(S)", "")
        d = rec.get("DUT_LTP（S）", "")
        s1 = rec.get("仿真1_LTP（S）", "")
        gd = d - g if (g is not None and d is not None and g != "" and d != "") else None
        gs1 = s1 - g if (g is not None and s1 is not None and g != "" and s1 != "") else None
        ds1 = s1 - d if (d is not None and s1 is not None and d != "" and s1 != "") else None
        gd_s = f"{gd:+d}" if gd is not None else "N/A"
        if gd is not None and gs1 is not None:
            color = "green" if abs(gs1) < abs(gd) else "red"
            gs1_s = f"<span style='color:{color}'>{gs1:+d}</span>"
        else:
            gs1_s = "N/A"
        ds1_s = f"{ds1:+d}" if ds1 is not None else "N/A"
        lines.append(f"| {rec['样本']} | {g} | {d} | {s1} | {gd_s} | {gs1_s} | {ds1_s} |")

    lines.append("")
    lines.append("### 2.4 最大心率 (HRmax) 对比")
    lines.append("")
    lines.append("| 样本 | Garmin_HRmax | DUT_HRmax | 仿真_1_MAXHR | Garmin-DUT | Garmin-仿真1 | DUT-仿真1 |")
    lines.append("|------|-------------|-----------|--------------|------------|-------------|-----------|")
    for rec in records:
        g = rec.get("Garmin_HRmax", "")
        d = rec.get("DUT_HRmax", "")
        s1 = rec.get("仿真1_MAXHR", "")
        gd = d - g if (g is not None and d is not None and g != "" and d != "") else None
        gs1 = s1 - g if (g is not None and s1 is not None and g != "" and s1 != "") else None
        ds1 = s1 - d if (d is not None and s1 is not None and d != "" and s1 != "") else None
        gd_s = f"{gd:+d}" if gd is not None else "N/A"
        if gd is not None and gs1 is not None:
            color = "green" if abs(gs1) < abs(gd) else "red"
            gs1_s = f"<span style='color:{color}'>{gs1:+d}</span>"
        else:
            gs1_s = "N/A"
        ds1_s = f"{ds1:+d}" if ds1 is not None else "N/A"
        lines.append(f"| {rec['样本']} | {g} | {d} | {s1} | {gd_s} | {gs1_s} | {ds1_s} |")

    lines.append("")
    lines.append("## 3. SDK 仿真三轮一致性分析")
    lines.append("")
    lines.append("| 样本 | LTHR极差 | 一致 | MAXHR极差 | 一致 | LTP极差(s) | 一致 | LTP(S)极差 | 一致 |")
    lines.append("|------|---------|------|-----------|------|-----------|------|-----------|------|")
    for result in sim_cons:
        lines.append(
            f"| {result['样本']} | {result['LTHR_range']} | {'✅' if result['LTHR_consistent'] else '❌'} | "
            f"{result['MAXHR_range']} | {'✅' if result['MAXHR_consistent'] else '❌'} | "
            f"{result['LTP_range_sec']} | {'✅' if result['LTP_consistent'] else '❌'} | "
            f"{result['LTP(S)_range']} | {'✅' if result['LTP(S)_consistent'] else '❌'} |"
        )
    lines.append("")
    lines.append(f"**三轮仿真一致性**: {consistent_count}/{len(sim_cons)} 样本全部指标一致")

    lines.append("")
    lines.append("## 4. DUT vs SDK 仿真均值对比")
    lines.append("")
    lines.append("### 4.1 LTHR 对比")
    lines.append("")
    lines.append("| 样本 | DUT_LTHR | SDK仿真均值 | 差值 | 评估 |")
    lines.append("|------|---------|-----------|------|------|")
    record_by_sample = {rec["样本"]: rec for rec in records}
    for result in dut_sim:
        rec = record_by_sample[result["样本"]]
        err_cls = classify_error(result["DUT_vs_Sim_LTHR"], "LTHR")
        diff_str = f"{result['DUT_vs_Sim_LTHR']:+.1f}" if result["DUT_vs_Sim_LTHR"] is not None else "N/A"
        avg_str = str(result["avg_sim_LTHR"]) if result["avg_sim_LTHR"] is not None else "N/A"
        lines.append(f"| {result['样本']} | {rec.get('DUT_LTHR', '')} | {avg_str} | {diff_str} | {err_cls} |")

    lines.append("")
    lines.append("### 4.2 配速 (LTP) 对比")
    lines.append("")
    lines.append("| 样本 | DUT_LTP | SDK仿真均值 | 差值(s) |")
    lines.append("|------|---------|-----------|---------|")
    for result in dut_sim:
        rec = record_by_sample[result["样本"]]
        diff_str = f"{result['DUT_vs_Sim_LTP_sec']:+.1f}" if result["DUT_vs_Sim_LTP_sec"] is not None else "N/A"
        lines.append(f"| {result['样本']} | {rec.get('DUT_LTP', '')} | {result.get('avg_sim_LTP', 'N/A')} | {diff_str}s |")

    lines.append("")
    lines.append("## 5. 数据表（Base 快照）")
    if base_url:
        lines.append("")
        lines.append(f"> 完整数据见飞书 Base: {base_url}")
    lines.append("")
    lines.append("| 样本 | 年龄 | 性别 | 身高 | 体重 | 静息 | 220-age | Garmin_LTHR | DUT_LTHR | 仿真1_LTHR | 仿真2_LTHR | 仿真3_LTHR | Garmin_LTP | DUT_LTP | 仿真1_LTP | 仿真2_LTP | 仿真3_LTP | Garmin_LTP(S) | DUT_LTP(S) | 仿真1_LTP(S) | 仿真2_LTP(S) | 仿真3_LTP(S) | Garmin_HRmax | DUT_HRmax | 仿真1_MAXHR | 仿真2_MAXHR | 仿真3_MAXHR | 样本分组 |")
    lines.append("|------|------|------|------|------|------|---------|------------|----------|-----------|-----------|-----------|-----------|---------|-----------|-----------|-----------|--------------|------------|-------------|-------------|-------------|-------------|-----------|------------|------------|------------|---------|")
    for rec in records:
        lines.append(
            f"| {rec['样本']} | {rec.get('年龄', '')} | {rec.get('性别', '')} | "
            f"{rec.get('身高', '')} | {rec.get('体重', '')} | {rec.get('静息', '')} | {rec.get('220-age', '')} | "
            f"{rec.get('Garmin_LTHR', '')} | {rec.get('DUT_LTHR', '')} | "
            f"{rec.get('仿真1_LTHR', '')} | {rec.get('仿真2_LTHR', '')} | {rec.get('仿真3_LTHR', '')} | "
            f"{rec.get('Garmin_LTP', '')} | {rec.get('DUT_LTP', '')} | "
            f"{rec.get('仿真1_LTP', '')} | {rec.get('仿真2_LTP', '')} | {rec.get('仿真3_LTP', '')} | "
            f"{rec.get('Garmin_LTP(S)', '')} | {rec.get('DUT_LTP（S）', '')} | "
            f"{rec.get('仿真1_LTP（S）', '')} | {rec.get('仿真2_LTP（S）', '')} | {rec.get('仿真3_LTP（S）', '')} | "
            f"{rec.get('Garmin_HRmax', '')} | {rec.get('DUT_HRmax', '')} | "
            f"{rec.get('仿真1_MAXHR', '')} | {rec.get('仿真2_MAXHR', '')} | {rec.get('仿真3_MAXHR', '')} | "
            f"{rec.get('样本分组', '')} |"
        )

    lines.append("")
    lines.append("---")
    lines.append(f"*报告由自动化工具生成 | {len(records)} 个样本 | 三源对比验证*")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Garmin vs DUT vs SDK 三源对比报告生成器")
    parser.add_argument("--json", required=True, help="JSON 数据文件路径")
    parser.add_argument("--sdk-version", required=True, help="SDK 版本号")
    parser.add_argument("-o", "--output", required=True, help="输出 Markdown 文件路径")
    parser.add_argument("--report-date", help="报告日期 (默认今天)")
    parser.add_argument("--base-url", help="飞书 Base 链接 (用于报告内引用)")
    args = parser.parse_args()

    records = load_records(args.json)
    report = generate_report(
        records,
        sdk_version=args.sdk_version,
        report_date=args.report_date,
        base_url=args.base_url or "",
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"报告已生成: {args.output}")
    print(f"共 {len(records)} 个样本 | SDK v{args.sdk_version}")


if __name__ == "__main__":
    main()

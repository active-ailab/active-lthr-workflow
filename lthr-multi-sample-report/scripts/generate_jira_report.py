#!/usr/bin/env python3
"""
generate_jira_report.py - JIRA CASE 新旧 SDK 对比报告生成器

将新 SDK 解析结果 (parse_gomore_samples.py --case JIRA_CASE) 与
旧版 SDK 基准值 (fetch_jira_baseline.py) 进行对比，生成 Markdown 报告。

用法:
  # 生成 JIRA CASE 对比报告
  python3 generate_jira_report.py \\
    --new-json jira_samples.json \\
    --baseline baseline.json \\
    --sdk-ver 1.0.16.0 \\
    -o jira_compare_report.md

  # 指定旧版 SDK 版本号
  python3 generate_jira_report.py \\
    --new-json jira_samples.json \\
    --baseline baseline.json \\
    --old-label "SDK 1.0.9.0" \\
    --new-label "SDK 1.0.16.0" \\
    -o report.md

  # 仅输出 Issue 列表（快速概览）
  python3 generate_jira_report.py --new-json jira_samples.json --baseline baseline.json --summary-only
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from typing import Optional


# ─── 工具函数 ──────────────────────────────────────────────

def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def pace_to_str(lt_speed: float) -> str:
    """将 ltSpeed (km/h) 转为配速字符串 (min/km)。"""
    if lt_speed <= 0:
        return "N/A"
    pace_min_per_km = 60.0 / lt_speed
    m = int(pace_min_per_km)
    s = int(round((pace_min_per_km - m) * 60))
    return f"{m}'{s:02d}\"/km"


def fmt_change(new_val, old_val, fmt="{:.1f}", higher_better=False):
    """格式化变化值，带 +/- 和百分比。"""
    if old_val is None or new_val is None:
        return "—"
    diff = new_val - old_val
    if abs(old_val) < 0.001:
        pct = ""
    else:
        pct = f" ({diff/old_val*100:+.1f}%)"
    arrow = "↑" if diff > 0 else ("↓" if diff < 0 else "→")
    return f"{fmt.format(new_val)} ({arrow}{fmt.format(diff)}{pct})"


def fmt_lthr_change(new_val, old_val):
    """LTHR 变化格式化。"""
    return fmt_change(new_val, old_val, fmt="{:.0f}")


def fmt_ltp_change(new_val, old_val):
    """LTP 变化格式化。"""
    return fmt_change(new_val, old_val, fmt="{:.2f}")


def gender_cn(val):
    return "男" if val == 1 else ("女" if val == 0 else "未知")


# ─── 数据匹配 ──────────────────────────────────────────────

def match_baseline(sample_key: str, baseline_data: dict) -> Optional[dict]:
    """
    从基准数据中匹配对应的条目。
    sample_key 格式: "COLOGNE-3107/gomore_data_20260320_110740"
    baseline_data: { "COLOGNE-3107": [ { file: "...", lh: ..., mh: ... }, ... ] }
    """
    if "/" not in sample_key:
        return None
    issue_key, fname = sample_key.split("/", 1)
    issue_key = issue_key.upper()
    fname_csv = fname + ".csv"

    issue_results = baseline_data.get(issue_key, [])
    if not issue_results:
        return None

    for entry in issue_results:
        if entry.get("file", "").startswith(fname):
            return entry

    # 如果就一个结果，返回第一个（WIN-1957 这种的）
    if len(issue_results) == 1:
        return issue_results[0]

    return None


# ─── 报告生成 ──────────────────────────────────────────────

def extract_round_values(sample_data: dict, round_num: int) -> dict:
    """从样本数据中提取指定轮次的值。"""
    p = f"仿真{round_num}"
    return {
        "ltp_s": sample_data.get(f"{p}_LTP（S）"),
        "ltp": sample_data.get(f"{p}_LTP"),
        "lthr": sample_data.get(f"{p}_LTHR"),
        "maxhr": sample_data.get(f"{p}_MAXHR"),
    }


def generate_report(
    new_samples: dict,
    baseline_data: dict,
    new_label: str = "新 SDK",
    old_label: str = "旧 SDK",
    summary_only: bool = False,
) -> str:
    """生成 JIRA CASE 对比报告 Markdown。"""
    lines = []
    lines.append(f"# JIRA CASE 新旧 SDK 对比报告")
    lines.append("")
    lines.append(f"| 项目 | 值 |")
    lines.append(f"|------|-----|")
    lines.append(f"| **新版本** | {new_label} |")
    lines.append(f"| **旧版本** | {old_label} |")
    lines.append(f"| **样本数** | {len(new_samples)} |")
    lines.append(f"| **生成时间** | {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |")
    lines.append("")

    # ── 按 Issue key 分组 ──
    issue_groups = defaultdict(dict)
    issue_rounds = defaultdict(set)
    for skey, sdata in new_samples.items():
        if "/" in skey:
            issue_key = skey.split("/")[0]
            issue_groups[issue_key][skey] = sdata
            # 收集该样本包含的轮次数
            for r in range(1, 4):
                if sdata.get(f"仿真{r}_LTHR") is not None:
                    issue_rounds[issue_key].add(r)

    # ── 概览表 ──
    if summary_only:
        lines.append("## 快速概览")
        lines.append("")
        lines.append("| Issue | CSV 数 | 对比结果 |")
        lines.append("|-------|--------|----------|")
        for issue_key in sorted(issue_groups.keys()):
            samples = issue_groups[issue_key]
            all_ok = True
            for skey, sdata in samples.items():
                bl = match_baseline(skey, baseline_data)
                if bl:
                    for r in sorted(issue_rounds[issue_key]):
                        vals = extract_round_values(sdata, r)
                        if vals["lthr"] is not None and abs(vals["lthr"] - bl["lh"]) > 1:
                            all_ok = False
            status = "✅ 一致" if all_ok else "⚠️ 有偏差"
            lines.append(f"| [{issue_key}](https://jira.huami.com/browse/{issue_key}) | {len(samples)} | {status} |")
        lines.append("")
        return "\n".join(lines)

    # ── 详细对比 ──
    for issue_key in sorted(issue_groups.keys()):
        samples = issue_groups[issue_key]
        rounds_present = sorted(issue_rounds[issue_key])

        lines.append(f"---")
        lines.append("")
        lines.append(f"## {issue_key}")
        lines.append("")
        lines.append(f"[在 Jira 中打开](https://jira.huami.com/browse/{issue_key})")
        lines.append("")

        for skey in sorted(samples.keys()):
            sdata = samples[skey]
            csv_fname = skey.split("/")[-1] + ".csv"
            bl = match_baseline(skey, baseline_data)
            has_user = sdata.get("年龄") is not None

            lines.append(f"### {csv_fname}")
            lines.append("")

            # 用户信息
            if has_user:
                lines.append(f"- **用户**: {sdata.get('年龄','?')}岁 / "
                             f"{sdata.get('性别','?')} / "
                             f"{sdata.get('身高','?')}cm / "
                             f"{sdata.get('体重','?')}kg / "
                             f"静息{sdata.get('静息','?')}bpm")
            if bl and bl.get("userInfo"):
                u = bl["userInfo"]
                lines.append(f"- **旧 SDK 用户信息**: {u.get('age','?')}岁 / "
                             f"{gender_cn(u.get('gender'))} / "
                             f"{u.get('height','?')}cm / "
                             f"{u.get('weight','?')}kg / "
                             f"静息{u.get('hrRest','?')}bpm")
            lines.append("")

            # 基准值
            if bl:
                lines.append(f"- **{old_label} 基准值**: "
                             f"LTHR={bl['lh']:.0f} | "
                             f"MAXHR={bl['mh']:.0f} | "
                             f"ltSpeed={bl['ltSpeed']:.4f} ({pace_to_str(bl['ltSpeed'])})")
                if bl.get("rows"):
                    lines.append(f"- **CSV 行数**: {bl['rows']} rows")
            else:
                lines.append(f"- **{old_label}**: 无基准数据")
            lines.append("")

            # 多轮次对比表
            if rounds_present:
                lines.append(f"#### SDK 多轮次结果 vs 旧版基准")
                lines.append("")
                # 表头
                header = "| 轮次 | LTHR | ΔLTHR | MAXHR | LTP (配速) | ΔLTP |"
                sep = "|------|------|-------|-------|------------|------|"
                lines.append(header)
                lines.append(sep)

                for r in rounds_present:
                    vals = extract_round_values(sdata, r)
                    r_lthr = vals["lthr"]
                    r_maxhr = vals["maxhr"]
                    r_ltp = vals["ltp"]
                    r_ltp_s = vals["ltp_s"]

                    # 与基准对比
                    if bl:
                        lthr_vs = fmt_lthr_change(r_lthr, bl["lh"]) if r_lthr is not None else "—"
                        ltp_s_vs = "—"
                        if r_ltp_s is not None and bl["ltSpeed"] > 0:
                            ltp_s_vs = fmt_ltp_change(r_ltp_s, bl["ltSpeed"] * 60)
                    else:
                        lthr_vs = "—"
                        ltp_s_vs = "—"

                    ltp_str = f"{r_ltp}" if r_ltp else "—"
                    lthr_str = f"{r_lthr:.0f}" if r_lthr is not None else "—"
                    maxhr_str = f"{r_maxhr:.0f}" if r_maxhr is not None else "—"

                    lines.append(f"| 第{r}轮 | {lthr_str} | {lthr_vs} | "
                                 f"{maxhr_str} | {ltp_str} | {ltp_s_vs} |")

                lines.append("")

                # 稳定性分析
                lthr_vals = [extract_round_values(sdata, r)["lthr"] for r in rounds_present]
                lthr_vals = [v for v in lthr_vals if v is not None]
                if len(lthr_vals) >= 2:
                    lthr_range = max(lthr_vals) - min(lthr_vals)
                    lthr_avg = sum(lthr_vals) / len(lthr_vals)
                    if lthr_range <= 1:
                        stability = "✅ 稳定"
                    elif lthr_range <= 3:
                        stability = "⚠️ 略有波动"
                    else:
                        stability = "❌ 波动较大"
                    lines.append(f"- **稳定性**: {stability} (范围 {lthr_range:.0f} bpm, 平均 {lthr_avg:.0f} bpm)")
                    lines.append("")

            # 单轮次仿真总结 (如果只有 1 轮)
            if len(rounds_present) == 1:
                r = rounds_present[0]
                vals = extract_round_values(sdata, r)
                lines.append(f"#### 与旧版 SDK 对比总结")
                lines.append("")
                if bl:
                    lthr_diff = (vals["lthr"] - bl["lh"]) if vals["lthr"] is not None else None
                    mh_diff = (vals["maxhr"] - bl["mh"]) if vals["maxhr"] is not None else None

                    if lthr_diff is not None:
                        direction = "偏高" if lthr_diff > 0 else ("偏低" if lthr_diff < 0 else "一致")
                        lines.append(f"- **LTHR**: 新={vals['lthr']:.0f} vs 旧={bl['lh']:.0f} "
                                     f"({lthr_diff:+.0f}, 新版本{direction})")
                    if mh_diff is not None:
                        lines.append(f"- **MAXHR**: 新={vals['maxhr']:.0f} vs 旧={bl['mh']:.0f} "
                                     f"({mh_diff:+.0f})")
                else:
                    lines.append("- 无旧版基准数据可对比")
                lines.append("")

        lines.append("")

    # ── 汇总 ──
    lines.append(f"---")
    lines.append("")
    lines.append(f"## 汇总")
    lines.append("")
    lines.append(f"| Issue | CSV 数 | LTHR 对比 | 稳定性 |")
    lines.append(f"|-------|--------|-----------|--------|")

    for issue_key in sorted(issue_groups.keys()):
        samples = issue_groups[issue_key]
        rds = sorted(issue_rounds[issue_key])

        # 汇总 LTHR 偏差
        lthr_diffs = []
        for skey, sdata in samples.items():
            bl = match_baseline(skey, baseline_data)
            if bl:
                for r in rds:
                    v = extract_round_values(sdata, r)["lthr"]
                    if v is not None:
                        lthr_diffs.append(v - bl["lh"])

        if lthr_diffs:
            avg_diff = sum(lthr_diffs) / len(lthr_diffs)
            if abs(avg_diff) <= 1:
                lthr_status = f"✅ {avg_diff:+.1f} (一致)"
            elif abs(avg_diff) <= 3:
                lthr_status = f"⚠️ {avg_diff:+.1f}"
            else:
                lthr_status = f"❌ {avg_diff:+.1f}"
        else:
            lthr_status = "—"

        # 稳定性
        all_lthr = []
        for skey, sdata in samples.items():
            for r in rds:
                v = extract_round_values(sdata, r)["lthr"]
                if v is not None:
                    all_lthr.append(v)
        if len(all_lthr) >= 2:
            rng = max(all_lthr) - min(all_lthr)
            if rng <= 1:
                stab = "✅"
            elif rng <= 3:
                stab = "⚠️"
            else:
                stab = "❌"
        else:
            stab = "—"

        lines.append(f"| [{issue_key}](https://jira.huami.com/browse/{issue_key}) | "
                     f"{len(samples)} | {lthr_status} | {stab} |")

    lines.append("")
    lines.append(f"---")
    lines.append(f"*报告自动生成 | {old_label} vs {new_label}*")
    lines.append("")

    return "\n".join(lines)


# ─── CLI ───────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="JIRA CASE 新旧 SDK 对比报告生成器"
    )
    parser.add_argument("--new-json", required=True, help="新 SDK 解析结果 JSON (parse_gomore_samples.py 输出)")
    parser.add_argument("--baseline", required=True, help="旧版 SDK 基准值 JSON (fetch_jira_baseline.py 输出)")
    parser.add_argument("--old-label", default="旧 SDK", help="旧版 SDK 标签")
    parser.add_argument("--new-label", default="新 SDK", help="新版 SDK 标签")
    parser.add_argument("-o", "--output", default=None, help="输出 Markdown 文件路径")
    parser.add_argument("--summary-only", action="store_true", help="仅输出快速概览表格")
    args = parser.parse_args()

    if not os.path.isfile(args.new_json):
        print(f"ERROR: 文件不存在: {args.new_json}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(args.baseline):
        print(f"ERROR: 文件不存在: {args.baseline}", file=sys.stderr)
        sys.exit(1)

    new_samples = load_json(args.new_json)
    baseline_data = load_json(args.baseline)

    report = generate_report(
        new_samples, baseline_data,
        new_label=args.new_label,
        old_label=args.old_label,
        summary_only=args.summary_only,
    )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"OK: 报告已保存至 {args.output}", file=sys.stderr)
        print(f"    样本数: {len(new_samples)}", file=sys.stderr)
        print(f"    涉及 Issue: {len(set(k.split('/')[0] for k in new_samples.keys() if '/' in k))}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()

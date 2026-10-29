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
from typing import List, Optional, Tuple


# ─── 工具函数 ──────────────────────────────────────────────

def speed_kph_to_pace_seconds(lt_speed: float) -> Optional[float]:
    """将速度值 km/h 换算为配速秒数 s/km。"""
    if lt_speed <= 0:
        return None
    return 3600.0 / lt_speed


def pace_seconds_to_str(pace_seconds: Optional[float]) -> str:
    """将配速秒数格式化为 m'ss\"/km。"""
    if pace_seconds is None:
        return "N/A"
    total_seconds = int(round(pace_seconds))
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes}'{seconds:02d}\"/km"


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def pace_to_str(lt_speed: float) -> str:
    """将 ltSpeed (km/h) 转为配速字符串 (min/km)。"""
    return pace_seconds_to_str(speed_kph_to_pace_seconds(lt_speed))


def fmt_change(new_val, old_val, fmt="{:.1f}", higher_better=False):
    """格式化差值列，按变化方向显示差值和百分比。"""
    if old_val is None or new_val is None:
        return "—"
    diff = new_val - old_val
    diff = float(fmt.format(diff))
    if diff == 0:
        diff = 0.0
    if abs(old_val) < 0.001:
        pct = ""
    else:
        pct = f" ({diff/old_val*100:+.1f}%)"
    arrow = "↑" if diff > 0 else ("↓" if diff < 0 else "→")
    return f"{arrow}{fmt.format(abs(diff))}{pct}"


def fmt_lthr_change(new_val, old_val):
    """LTHR 变化格式化。"""
    return fmt_change(new_val, old_val, fmt="{:.0f}")


def fmt_ltp_change(new_val, old_val):
    """LTP 变化格式化。"""
    return fmt_change(new_val, old_val, fmt="{:.0f}")


def gender_cn(val):
    return "男" if val == 1 else ("女" if val == 0 else "未知")


# ─── 数据匹配 ──────────────────────────────────────────────

def match_baseline(
    sample_key: str,
    baseline_data: dict,
    round_num: Optional[int] = None,
) -> Optional[dict]:
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

    matching = [
        entry for entry in issue_results
        if os.path.basename(entry.get("file", "")).casefold() == fname_csv.casefold()
    ]
    if not matching:
        matching = [
            entry for entry in issue_results
            if os.path.basename(entry.get("file", "")).casefold().startswith(fname.casefold())
        ]
    if not matching and len(issue_results) == 1:
        # 兼容旧评论：一个 Issue 只有一个 CSV Result 时可直接复用。
        matching = issue_results
    if not matching:
        return None

    round_entries = [entry for entry in matching if entry.get("round") is not None]
    if round_num is not None:
        if round_entries:
            return next((entry for entry in round_entries if entry.get("round") == round_num), None)
        # 旧格式没有轮次字段，沿用单条基准对所有新轮次做比较。
        return matching[0]

    if round_entries:
        return next((entry for entry in round_entries if entry.get("round") == 1), round_entries[0])
    return matching[0]


# ─── 报告生成 ──────────────────────────────────────────────

def extract_round_values(sample_data: dict, round_num: int) -> dict:
    """从样本数据中提取指定轮次的值。"""
    p = f"仿真{round_num}"
    return {
        "ltp_s": sample_data.get(f"{p}_LTP（S）"),
        "ltp": sample_data.get(f"{p}_LTP"),
        "lthr": sample_data.get(f"{p}_LTHR"),
        "maxhr": sample_data.get(f"{p}_MAXHR"),
        "status": sample_data.get(f"{p}_状态"),
    }


def collect_invalid_round_statuses(sample_data: dict) -> List[Tuple[int, str]]:
    statuses = []
    for r in range(1, 4):
        status = sample_data.get(f"仿真{r}_状态")
        if status:
            statuses.append((r, status))
    return statuses


def generate_report(
    new_samples: dict,
    baseline_data: dict,
    new_label: str = "新 SDK",
    old_label: str = "旧 SDK",
    summary_only: bool = False,
    simulation_status: str = "",
    baseline_source: str = "",
    simulation_run_id: str = "",
    document_owner: str = "",
    document_organization: str = "",
) -> str:
    """生成 JIRA CASE 对比报告 Markdown。"""
    lines = []
    lines.append(f"# JIRA CASE 新旧 SDK 对比报告")
    lines.append("")
    if document_owner and document_organization:
        lines.append(f"Owner: {document_owner}  ")
        lines.append(f"Organization: {document_organization}")
        lines.append("")
    lines.append(f"| 项目 | 值 |")
    lines.append(f"|------|-----|")
    lines.append(f"| **新版本** | {new_label} |")
    lines.append(f"| **旧版本** | {old_label} |")
    lines.append(f"| **样本数** | {len(new_samples)} |")
    if simulation_status:
        status_labels = {"success": "成功", "partial": "部分完成", "failed": "失败"}
        lines.append(f"| **C 端仿真状态** | {status_labels.get(simulation_status, simulation_status)} |")
    if simulation_run_id:
        lines.append(f"| **仿真批次** | `{simulation_run_id}` |")
    if baseline_source:
        lines.append(f"| **旧版基准来源** | {baseline_source} |")
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
                for r in sorted(issue_rounds[issue_key]):
                    bl = match_baseline(skey, baseline_data, round_num=r)
                    vals = extract_round_values(sdata, r)
                    if bl and vals["lthr"] is not None and abs(vals["lthr"] - bl["lh"]) > 1:
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
            bl = match_baseline(skey, baseline_data, round_num=1) or match_baseline(skey, baseline_data)
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
                if bl.get("pace_precision") == "whole_second_report":
                    pace_note = "；配速来自历史报告整秒显示"
                    lines.append(f"- **{old_label} 基准值**: "
                                 f"LTHR={bl['lh']:.0f} | "
                                 f"MAXHR={bl['mh']:.0f} | "
                                 f"LTP={pace_to_str(bl['ltSpeed'])}{pace_note}")
                else:
                    lines.append(f"- **{old_label} 基准值**: "
                                 f"LTHR={bl['lh']:.0f} | "
                                 f"MAXHR={bl['mh']:.0f} | "
                                 f"ltSpeed={bl['ltSpeed']:.4f} ({pace_to_str(bl['ltSpeed'])})")
                if bl.get("rows"):
                    lines.append(f"- **CSV 行数**: {bl['rows']} rows")
            else:
                lines.append(f"- **{old_label}**: 无基准数据")
            lines.append("")

            # 多轮次对比表：保留有状态标记但没有有效数值的轮次。
            sample_rounds = [
                r for r in range(1, 4)
                if any(sdata.get(f"仿真{r}_{suffix}") is not None
                       for suffix in ("LTHR", "LTP", "LTP（S）", "MAXHR", "状态"))
            ]
            if sample_rounds:
                lines.append(f"#### SDK 多轮次结果 vs 旧版基准")
                lines.append("")
                # 表头
                header = "| 轮次 | LTHR | ΔLTHR | MAXHR | LTP (配速) | ΔLTP (秒/公里) | 状态 |"
                sep = "|------|------|-------|-------|------------|------|------|"
                lines.append(header)
                lines.append(sep)

                for r in sample_rounds:
                    vals = extract_round_values(sdata, r)
                    r_lthr = vals["lthr"]
                    r_maxhr = vals["maxhr"]
                    r_ltp = vals["ltp"]
                    r_ltp_s = vals["ltp_s"]
                    round_baseline = match_baseline(skey, baseline_data, round_num=r)

                    # 与基准对比
                    if round_baseline:
                        lthr_vs = fmt_lthr_change(r_lthr, round_baseline["lh"]) if r_lthr is not None else "—"
                        ltp_s_vs = "—"
                        old_ltp_s = speed_kph_to_pace_seconds(round_baseline["ltSpeed"])
                        if r_ltp_s is not None and old_ltp_s is not None:
                            ltp_s_vs = fmt_ltp_change(float(r_ltp_s), old_ltp_s)
                    else:
                        lthr_vs = "—"
                        ltp_s_vs = "—"

                    ltp_str = f"{r_ltp}" if r_ltp else "—"
                    lthr_str = f"{r_lthr:.0f}" if r_lthr is not None else "—"
                    maxhr_str = f"{r_maxhr:.0f}" if r_maxhr is not None else "—"
                    round_status = vals["status"] or (
                        "有效结果" if any(v is not None for v in (r_lthr, r_maxhr, r_ltp_s)) else "未输出"
                    )

                    lines.append(f"| 第{r}轮 | {lthr_str} | {lthr_vs} | "
                                 f"{maxhr_str} | {ltp_str} | {ltp_s_vs} | {round_status} |")

                lines.append("")
                invalid_statuses = collect_invalid_round_statuses(sdata)
                for round_num, status in invalid_statuses:
                    lines.append(f"- 第{round_num}轮状态：`{status}`")
                if invalid_statuses:
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
            else:
                invalid_statuses = collect_invalid_round_statuses(sdata)
                lines.append("#### SDK 仿真结果状态")
                lines.append("")
                lines.append("- **说明**: 当前样本没有可对比的有效乳酸阈结果；这不代表仿真执行失败，请结合 C 端仿真状态和逐轮日志判断。")
                if invalid_statuses:
                    for round_num, status in invalid_statuses:
                        lines.append(f"- 第{round_num}轮: {status}")
                lines.append("")

            # 单轮次仿真总结 (如果只有 1 轮)
            if len(rounds_present) == 1:
                r = rounds_present[0]
                vals = extract_round_values(sdata, r)
                round_baseline = match_baseline(skey, baseline_data, round_num=r)
                lines.append(f"#### 与旧版 SDK 对比总结")
                lines.append("")
                if round_baseline:
                    lthr_diff = (vals["lthr"] - round_baseline["lh"]) if vals["lthr"] is not None else None
                    mh_diff = (vals["maxhr"] - round_baseline["mh"]) if vals["maxhr"] is not None else None

                    if lthr_diff is not None:
                        direction = "偏高" if lthr_diff > 0 else ("偏低" if lthr_diff < 0 else "一致")
                        lines.append(f"- **LTHR**: 新={vals['lthr']:.0f} vs 旧={round_baseline['lh']:.0f} "
                                     f"({lthr_diff:+.0f}, 新版本{direction})")
                    if mh_diff is not None:
                        lines.append(f"- **MAXHR**: 新={vals['maxhr']:.0f} vs 旧={round_baseline['mh']:.0f} "
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
            for r in rds:
                bl = match_baseline(skey, baseline_data, round_num=r)
                v = extract_round_values(sdata, r)["lthr"]
                if bl and v is not None:
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
    parser.add_argument("--simulation-status", default="", help="C 端 summary 中的仿真状态")
    parser.add_argument("--baseline-source", default="", help="旧版基准的可审计来源说明")
    parser.add_argument("--simulation-run-id", default="", help="C 端 summary 中的 run_id")
    parser.add_argument("--document-owner", default="", help="可选的报告 Owner 元数据")
    parser.add_argument("--document-organization", default="", help="可选的报告 Organization 元数据")
    parser.add_argument("-o", "--output", default=None, help="输出 Markdown 文件路径")
    parser.add_argument("--summary-only", action="store_true", help="仅输出快速概览表格")
    args = parser.parse_args()

    if bool(args.document_owner) != bool(args.document_organization):
        parser.error("--document-owner 和 --document-organization 必须一起提供")

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
        simulation_status=args.simulation_status,
        baseline_source=args.baseline_source,
        simulation_run_id=args.simulation_run_id,
        document_owner=args.document_owner,
        document_organization=args.document_organization,
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

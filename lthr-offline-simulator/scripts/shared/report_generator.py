from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, List, Sequence

from _output_templates import (
    format_optional_line as _format_optional_line,
    format_signature_block as _format_signature_block,
    render_asset_template as _render_asset_template,
)


def format_deployment_summary(verification: Dict[str, object]) -> str:
    return (
        "[INFO] Deployment verified: "
        f"latest_csv={verification.get('latest_csv')} "
        f"csv_count={verification.get('csv_count')} "
        f"copied_size={verification.get('copied_size')} "
        f"state_file_count={verification.get('state_file_count')}\n"
    )


def format_windows_copy_summary(data: Dict[str, object]) -> str:
    return (
        "[INFO] Windows multi deployment verified: "
        f"csv_count={data.get('csv_count')} "
        f"meta_count={data.get('meta_count')} "
        f"state_file_count={data.get('state_file_count')} "
        f"data_sample={data.get('data_sample')}\n"
    )


def format_duration(secs: float) -> str:
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def build_comment(
    issue_meta: Dict[str, str],
    sample_summaries: List[Dict[str, object]],
    artifact_names: Sequence[str],
    _signature: str,
    device_sdk_version: str = "未获取",
) -> str:
    lines: List[str] = []
    for sample in sample_summaries:
        results = sample["results"]
        ok_results = [r for r in results if r.get("status") == "ok" and "ltSpeed" in r]
        lines.append(
            f"* Sample: {sample['sample_csv']} | runs={len(results)} | success={len(ok_results)} | stability={sample['stability']}"
        )
        if sample.get("error"):
            lines.append(f"  - Simulation Error: {sample['error']}")
        if sample.get("mode") == "deploy_only":
            lines.append("  - Deployment verified only; UART utest skipped")
            continue
        for result in results:
            if result.get("status") == "ok" and "ltSpeed" in result:
                lines.append(
                    "  - Run #{run}: ltSpeed={ltSpeed:.6f} lh={lh} mh={mh} guideResult={guideResult}".format(
                        run=result["run"],
                        ltSpeed=float(result["ltSpeed"]),
                        lh=result["lh"],
                        mh=result["mh"],
                        guideResult=result["guideResult"],
                    )
                )
            else:
                lines.append(f"  - Run #{result['run']}: {result.get('status', 'failed')}")
        trend = sample.get("trend")
        if trend and trend.get("valid_runs", 0) >= 2:
            lt_series_str = ", ".join("{:.6f}".format(float(r["ltSpeed"])) for r in ok_results)
            lines.append(f"  - 【ltSpeed 趋势】: {trend['ltSpeed']['trend']} (slope={trend['ltSpeed']['slope']})")
            lines.append(f"    mean={trend['ltSpeed']['mean']}, std={trend['ltSpeed']['std']}, CV={trend['ltSpeed']['cv']}")
            lines.append(f"    序列: [{lt_series_str}]")
            lines.append(f"  - 【mh 趋势】: {trend['mh']['trend']} (slope={trend['mh']['slope']})")
            lines.append(f"    mean={trend['mh']['mean']:.1f}, range=[{trend['mh']['min']:.0f}~{trend['mh']['max']:.0f}]")
    return _render_asset_template(
        "jira_comment_template.md",
        {
            "sample_count": len(sample_summaries),
            "device_sdk_version": device_sdk_version or "未获取",
            "sample_lines": ("\n".join(lines) + "\n") if lines else "",
            "attachment_line": f"* 附件: {', '.join(artifact_names)}\n" if artifact_names else "",
        },
    ).rstrip()


def build_report_markdown(
    issue_meta: Dict[str, str],
    sample_summaries: List[Dict[str, object]],
    raw_log_name: str,
    signature: str = "",
) -> str:
    lines: List[str] = []
    for index, sample in enumerate(sample_summaries, 1):
        lines.append(f"## {index}. {sample['sample_csv']}")
        lines.append(f"- Gomore Root: {sample['gomore_root']}")
        if sample.get("attachment_run_mode"):
            lines.append(f"- Attachment Run Mode: {sample['attachment_run_mode']}")
        if sample.get("mode") == "deploy_only":
            lines.append("- Mode: deploy-only (skipped UART utest)")
        lines.append(f"- Runs: {len(sample['results'])}")
        lines.append(f"- Stability: {sample['stability']}")
        if sample.get("error"):
            lines.append(f"- Simulation Error: {sample['error']}")
        cleanup = sample.get("cleanup") or {}
        if cleanup:
            lines.append(f"- Deleted CSV Count: {len(cleanup.get('deleted_csvs', []))}")
            lines.append(f"- Deleted State File Count: {len(cleanup.get('deleted_state_files', []))}")
            copied = cleanup.get("copied_sample") or []
            if copied:
                lines.append(f"- Copied Sample: {copied[0]}")
        lines.append(f"- Deploy Attempt: {sample.get('deploy_attempt', 1)}")
        deployment = sample.get("deployment") or {}
        if deployment:
            lines.append(f"- Device Latest CSV: {deployment.get('latest_csv', '')}")
            lines.append(f"- Device Root CSV Count: {deployment.get('csv_count', 0)}")
            lines.append(f"- Device State File Count: {deployment.get('state_file_count', 0)}")
        lines.append("")
        if sample.get("mode") == "deploy_only":
            lines.append("- Deployment health check passed; no UART result table generated.")
            lines.append("")
            continue
        lines.append("| Run | Status | ltSpeed | lh | mh | guideResult |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for result in sample["results"]:
            if result.get("status") == "ok" and "ltSpeed" in result:
                lines.append(
                    f"| {result['run']} | ok | {float(result['ltSpeed']):.6f} | "
                    f"{result['lh']} | {result['mh']} | {result['guideResult']} |"
                )
            else:
                lines.append(f"| {result['run']} | {result.get('status', 'failed')} | - | - | - | - |")
        lines.append("")
        trend = sample.get("trend")
        if trend and trend.get("valid_runs", 0) >= 2:
            lines.append("### 趋势分析")
            lines.append("")
            lines.append("| 指标 | 均值 | 标准差 | CV | 最小值 | 最大值 | 极差 | 斜率 | 趋势 |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
            lt = trend["ltSpeed"]
            lines.append(
                f"| ltSpeed | {lt['mean']} | {lt['std']} | {lt['cv']} | "
                f"{lt['min']} | {lt['max']} | {lt['range']} | {lt['slope']} | {lt['trend']} |"
            )
            mh = trend["mh"]
            lines.append(
                f"| mh | {mh['mean']:.1f} | {mh['std']:.1f} | {mh['cv']} | "
                f"{mh['min']:.0f} | {mh['max']:.0f} | {mh['range']:.0f} | {mh['slope']} | {mh['trend']} |"
            )
            lh = trend["lh"]
            lines.append(
                f"| lh | {lh['mean']:.1f} | {lh['std']:.1f} | {lh['cv']} | "
                f"{lh['min']:.0f} | {lh['max']:.0f} | {lh['range']:.0f} | - | - |"
            )
            lines.append("")
            ok_results = [r for r in sample["results"] if r.get("status") == "ok" and "ltSpeed" in r]
            lt_series = ", ".join(f"{float(r['ltSpeed']):.6f}" for r in ok_results)
            mh_series = ", ".join(str(r["mh"]) for r in ok_results)
            lines.append(f"- **ltSpeed 序列**: [{lt_series}]")
            lines.append(f"- **mh 序列**: [{mh_series}]")
            lines.append(
                f"- **结论**: ltSpeed {trend['ltSpeed']['trend']} (slope={trend['ltSpeed']['slope']}), "
                f"mh {trend['mh']['trend']} (slope={trend['mh']['slope']})"
            )
            if trend["guideResult_unique_count"] > 1:
                lines.append(f"- **guideResult 出现 {trend['guideResult_unique_count']} 种不同值**, 需关注")
            lines.append("")
    return _render_asset_template(
        "report_template.md",
        {
            "signature_block": _format_signature_block(signature, trailing_blank_line=True),
            "generated_at": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()),
            "issue_key": issue_meta["key"],
            "issue_link_line": _format_optional_line("Link", issue_meta.get("link", "")),
            "issue_title_line": _format_optional_line("Title", issue_meta.get("title", "")),
            "sample_count": len(sample_summaries),
            "raw_log_name": raw_log_name,
            "sample_sections": "\n".join(lines).rstrip(),
        },
    ).rstrip() + "\n"


def build_consolidated_report(
    batch_results: List[Dict],
    batch_dir: Path,
    stamp: str,
    total_duration: float,
    issue_count: int,
    success_count: int,
    fail_count: int,
) -> str:
    del stamp
    summary_lines: List[str] = ["## 汇总", ""]
    detail_lines: List[str] = ["## 各 Issue 详情", ""]

    if batch_results:
        summary_lines.append("| Issue | Sample | Runs | Valid | Stability | ltSpeed Trend | mh Trend |")
        summary_lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for br in batch_results:
            meta = br.get("issue", {})
            key = meta.get("key", "?")
            for s in br.get("samples", []):
                trend = s.get("trend", {}) or {}
                lt_t = trend.get("ltSpeed", {}).get("trend", "-") if trend else "-"
                mh_t = trend.get("mh", {}).get("trend", "-") if trend else "-"
                valid = trend.get("valid_runs", "?") if trend else "?"
                summary_lines.append(
                    f"| {key} | {s.get('sample_csv', '?')} | "
                    f"{len(s.get('results', []))} | {valid} | "
                    f"{s.get('stability', '?')} | {lt_t} | {mh_t} |"
                )
    else:
        summary_lines.append("_No successful issue results were parsed from child artifacts._")

    for br in batch_results:
        meta = br.get("issue", {})
        key = meta.get("key", "?")
        title = meta.get("title", "")
        detail_lines.append(f"### {key}" + (f" — {title}" if title else ""))
        detail_lines.append("")
        for s in br.get("samples", []):
            detail_lines.append(f"- **Sample**: {s.get('sample_csv', '?')}")
            detail_lines.append(f"- **Runs**: {len(s.get('results', []))}")
            detail_lines.append(f"- **Stability**: {s.get('stability', '?')}")
            trend = s.get("trend")
            if trend and trend.get("valid_runs", 0) >= 2:
                lt = trend["ltSpeed"]
                detail_lines.append(
                    f"- **ltSpeed**: mean={lt['mean']}, std={lt['std']}, "
                    f"trend={lt['trend']} (slope={lt['slope']})"
                )
                mh = trend["mh"]
                detail_lines.append(
                    f"- **mh**: mean={mh['mean']:.1f}, "
                    f"trend={mh['trend']} (slope={mh['slope']})"
                )
        artifact_dir = batch_dir / key / "artifacts"
        md_files = sorted(artifact_dir.glob("lthr_offline_sim_*.md"))
        if md_files:
            detail_lines.append(f"- [完整报告]({md_files[-1].relative_to(batch_dir)})")
        detail_lines.append("")

    return _render_asset_template(
        "batch_report_template.md",
        {
            "generated_at": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()),
            "total_duration": format_duration(total_duration),
            "issue_count": issue_count,
            "success_count": success_count,
            "fail_count": fail_count,
            "summary_section": "\n".join(summary_lines).rstrip(),
            "detail_section": "\n".join(detail_lines).rstrip(),
            "generator_name": "run_lthr_batch.py",
        },
    ).rstrip() + "\n"


__all__ = [
    "build_comment",
    "build_consolidated_report",
    "build_report_markdown",
    "format_deployment_summary",
    "format_duration",
    "format_windows_copy_summary",
]

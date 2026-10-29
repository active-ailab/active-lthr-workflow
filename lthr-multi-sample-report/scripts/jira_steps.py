#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys

from _pipeline_common import (
    LYON_DIR,
    SCRIPTS,
    get_output_dir,
    get_search_dirs,
    has_detected_case,
    infer_jira_issues_from_json,
    infer_jira_issues_from_log,
    log_step,
    run_cmd,
)
from _report_utils import run_lark
from base_steps import ensure_version_folder


def _overlay_jira_results_from_offline_summary(summary_path, samples_path):
    """Use the C-side per-sample result list to repair interleaved/missing log headers."""
    if not summary_path or not os.path.isfile(summary_path) or not os.path.isfile(samples_path):
        return False
    with open(summary_path, "r", encoding="utf-8") as stream:
        summary = json.load(stream)
    sample_rows = summary.get("samples") or (summary.get("simulation") or {}).get("samples") or []
    if summary.get("workflow") != "multi_sample" or not sample_rows:
        return False

    with open(samples_path, "r", encoding="utf-8") as stream:
        parsed = json.load(stream)
    overlaid = 0
    for row in sample_rows:
        sample_path = str(row.get("sample_path") or "").replace("\\", "/")
        parts = [part for part in sample_path.split("/") if part]
        try:
            case_index = parts.index("JIRA_CASE")
            issue_key = parts[case_index + 1].upper()
            filename = parts[-1]
        except (ValueError, IndexError):
            continue
        sample_key = f"{issue_key}/{os.path.splitext(filename)[0]}"
        sample_data = parsed.setdefault(sample_key, {})
        for run_num in range(1, 4):
            prefix = f"仿真{run_num}"
            for suffix in ("_LTHR", "_LTP", "_LTP（S）", "_MAXHR", "_状态"):
                sample_data[prefix + suffix] = None

        for result in row.get("results") or []:
            run_num = result.get("run")
            if not isinstance(run_num, int) or run_num < 1 or run_num > 3:
                continue
            prefix = f"仿真{run_num}"
            speed = result.get("ltSpeed")
            lthr = result.get("lh")
            maxhr = result.get("mh")
            if result.get("result_missing") or speed is None or lthr is None or maxhr is None:
                sample_data[prefix + "_状态"] = "result_missing: C-side summary has no algorithm output"
                continue
            speed = float(speed)
            lthr = float(lthr)
            maxhr = float(maxhr)
            if speed <= 0 or lthr <= 0 or maxhr <= 0:
                sample_data[prefix + "_状态"] = (
                    f"invalid_result: ltSpeed={speed:.3f}, lh={int(lthr)}, mh={int(maxhr)}"
                )
                continue
            pace_seconds_exact = 3600.0 / speed
            minutes, seconds = divmod(int(round(pace_seconds_exact)), 60)
            sample_data[prefix + "_LTHR"] = int(lthr)
            sample_data[prefix + "_LTP"] = f"{minutes}'{seconds:02d}"
            sample_data[prefix + "_LTP（S）"] = pace_seconds_exact
            sample_data[prefix + "_MAXHR"] = int(maxhr)
        overlaid += 1

    if not overlaid:
        return False
    with open(samples_path, "w", encoding="utf-8") as stream:
        json.dump(parsed, stream, ensure_ascii=False, indent=2)
    print(f"  [对齐] 已用 C 端 summary 校准 {overlaid} 个 JIRA_CASE 样本的轮次结果")
    return True


def _refresh_jira_samples_from_log(args, dry_run=False):
    """从当前日志重新解析 JIRA_CASE 样本，避免沿用陈旧缓存。"""
    if not getattr(args, "log_file", None):
        return None
    if not has_detected_case(args, "JIRA_CASE"):
        return None

    out_path = os.path.join(get_output_dir(args), f"jira_samples_{args.sdk_ver}.json")
    cmd = [
        sys.executable, SCRIPTS["parse"],
        args.log_file,
        "-o", out_path,
        "--case", "JIRA_CASE",
    ]
    issue_key = getattr(args, "offline_summary_issue_key", "")
    if not issue_key:
        jira_issues = getattr(args, "jira_issues", None) or []
        if len(jira_issues) == 1:
            issue_key = jira_issues[0]
    if issue_key:
        cmd.extend(["--jira-issue-key", issue_key])
    run_cmd(cmd, dry_run=dry_run)
    if not dry_run:
        _overlay_jira_results_from_offline_summary(
            getattr(args, "offline_summary", None), out_path
        )
    return out_path


def _resolve_jira_json_source(args, dry_run=False):
    """优先使用显式 jira_json；否则在有日志时重刷一份最新 JIRA 样本。"""
    if getattr(args, "jira_json", None):
        return args.jira_json

    refreshed = _refresh_jira_samples_from_log(args, dry_run=dry_run)
    if refreshed:
        return refreshed

    for base_dir in get_search_dirs(args):
        candidate = os.path.join(base_dir, f"jira_samples_{args.sdk_ver}.json")
        if os.path.exists(candidate):
            return candidate
    return None


def step_11_jira_parse_log(args, dry_run=False):
    """步骤11: 从日志解析 JIRA_CASE 样本。"""
    log_step(11, "解析 JIRA_CASE 样本")
    if not has_detected_case(args, "JIRA_CASE"):
        print("  [跳过] 日志中未检测到 JIRA_CASE")
        return None
    if not args.log_file:
        print("  [跳过] 未指定 --log-file")
        return None

    return _refresh_jira_samples_from_log(args, dry_run=dry_run)


def step_12_jira_fetch_baseline(args, dry_run=False):
    """步骤12: 从 JIRA 评论提取旧版基准值。"""
    log_step(12, "提取 JIRA 基准值")
    if getattr(args, "jira_baseline", None) and os.path.isfile(args.jira_baseline):
        print(f"  [复用] 使用指定的版本基准: {args.jira_baseline}")
        return args.jira_baseline
    if not args.jira_issues:
        candidate_json = args.jira_json
        if not candidate_json:
            for base_dir in get_search_dirs(args):
                candidate = os.path.join(base_dir, f"jira_samples_{args.sdk_ver}.json")
                if os.path.exists(candidate):
                    candidate_json = candidate
                    break
        inferred_issues = infer_jira_issues_from_json(candidate_json)
        if not inferred_issues:
            inferred_issues = infer_jira_issues_from_log(args.log_file)
        if inferred_issues:
            args.jira_issues = inferred_issues
            print(f"  [自动] 推断 issues: {' '.join(inferred_issues)}")
    if not args.jira_issues:
        print("  [跳过] 未指定 --jira-issues")
        return None

    out_path = os.path.join(get_output_dir(args), f"jira_baseline_{args.sdk_ver}.json")
    cmd = [sys.executable, SCRIPTS["jira_baseline"]] + args.jira_issues + ["-o", out_path]
    run_cmd(cmd, dry_run=dry_run)
    return out_path


def step_13_jira_report(args, dry_run=False):
    """步骤13: 生成 JIRA CASE 对比报告。"""
    log_step(13, "生成 JIRA CASE 对比报告")

    jira_json = _resolve_jira_json_source(args, dry_run=dry_run)

    baseline_json = args.jira_baseline
    if not baseline_json:
        for base_dir in get_search_dirs(args):
            candidate = os.path.join(base_dir, f"jira_baseline_{args.sdk_ver}.json")
            if os.path.exists(candidate):
                baseline_json = candidate
                break

    if not jira_json:
        print("  [跳过] 未找到 JIRA 样本 JSON (需先运行步骤11)")
        return
    if not baseline_json:
        print("  [跳过] 未找到基准 JSON (需先运行步骤12)")
        return

    out_path = os.path.join(get_output_dir(args), f"jira_compare_report_{args.sdk_ver}.md")
    cmd = [
        sys.executable, SCRIPTS["jira_report"],
        "--new-json", jira_json,
        "--baseline", baseline_json,
        "--new-label", f"SDK {args.sdk_ver}",
        "--old-label", f"SDK {args.prev_ver}" if getattr(args, "prev_ver", None) else "旧 SDK",
        "-o", out_path,
    ]
    simulation_status = getattr(args, "offline_summary_status", "")
    if simulation_status:
        cmd.extend(["--simulation-status", simulation_status])
    run_id = getattr(args, "offline_summary_run_id", "")
    if run_id:
        cmd.extend(["--simulation-run-id", run_id])
    baseline_source = getattr(args, "jira_baseline_source", "")
    if baseline_source:
        cmd.extend(["--baseline-source", baseline_source])
    document_owner = getattr(args, "report_owner", "")
    document_organization = getattr(args, "report_organization", "")
    if document_owner and document_organization:
        cmd.extend(["--document-owner", document_owner])
        cmd.extend(["--document-organization", document_organization])
    run_cmd(cmd, dry_run=dry_run)
    print(f"  [提示] 查看报告: {out_path}")
    return out_path


def step_14_jira_publish(args, doc_url=None, dry_run=False):
    """步骤14: 发布 JIRA 对比报告到飞书文档（归档到版本子文件夹）。"""
    log_step(14, "发布 JIRA 对比报告")
    report_path = os.path.join(get_output_dir(args), f"jira_compare_report_{args.sdk_ver}.md")
    if not os.path.exists(report_path):
        print(f"  [跳过] 报告文件不存在: {report_path}")
        return

    if dry_run:
        print(f"  [干跑] lark-cli docs +create --title ... --markdown ... --folder-token <ver>")
        return "https://dryrun.feishu.cn/doc/jira_xxx"

    ver_token = ensure_version_folder(args, dry_run=False)

    with open(report_path, "r", encoding="utf-8") as f:
        content = f.read()

    cmd = [
        "docs", "+create",
        "--title", f"JIRA 对比报告 SDK {args.sdk_ver}",
        "--markdown", content,
    ]
    if ver_token:
        cmd += ["--folder-token", ver_token]

    result = run_lark(cmd, allow_raw=True)
    if not result.get("ok"):
        retry_cmd = ["lark-cli", "docs", "+create",
                     "--title", f"JIRA 对比报告 SDK {args.sdk_ver}",
                     "--markdown", content]
        if ver_token:
            retry_cmd += ["--folder-token", ver_token]
        retry = run_cmd(retry_cmd, cwd=LYON_DIR)
        if retry.returncode == 0:
            print("  [完成] 报告已发布")
            return retry.stdout
    else:
        doc_url = (result.get("data") or {}).get("doc_url", "")
        if doc_url:
            print(f"  [完成] 报告已发布: {doc_url}")
            return doc_url
        print("  [完成] 报告已发布（未获取到链接）")
    return None


JIRA_STEP_HANDLERS = {
    11: step_11_jira_parse_log,
    12: step_12_jira_fetch_baseline,
    13: step_13_jira_report,
}

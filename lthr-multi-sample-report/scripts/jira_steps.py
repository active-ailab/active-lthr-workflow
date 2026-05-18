#!/usr/bin/env python3
from __future__ import annotations

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


def step_11_jira_parse_log(args, dry_run=False):
    """步骤11: 从日志解析 JIRA_CASE 样本。"""
    log_step(11, "解析 JIRA_CASE 样本")
    if not has_detected_case(args, "JIRA_CASE"):
        print("  [跳过] 日志中未检测到 JIRA_CASE")
        return None
    if not args.log_file:
        print("  [跳过] 未指定 --log-file")
        return None

    out_path = os.path.join(get_output_dir(args), f"jira_samples_{args.sdk_ver}.json")
    cmd = [
        sys.executable, SCRIPTS["parse"],
        args.log_file,
        "-o", out_path,
        "--case", "JIRA_CASE",
    ]
    run_cmd(cmd, dry_run=dry_run)
    return out_path


def step_12_jira_fetch_baseline(args, dry_run=False):
    """步骤12: 从 JIRA 评论提取旧版基准值。"""
    log_step(12, "提取 JIRA 基准值")
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

    jira_json = args.jira_json
    if not jira_json:
        for base_dir in get_search_dirs(args):
            candidate = os.path.join(base_dir, f"jira_samples_{args.sdk_ver}.json")
            if os.path.exists(candidate):
                jira_json = candidate
                break

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
        "--old-label", "旧 SDK",
        "-o", out_path,
    ]
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

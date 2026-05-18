#!/usr/bin/env python3
"""
run_pipeline.py - 乳酸阈(LTHR)多样本仿真报告全流程编排器

一键执行新 SDK 发布后的完整工作流：
  解析日志 → 写入Base → 拉取数据 → 三源报告 → 单版本报告 → 版本对比 → 发布飞书 → 归档 → 群通知

--sdk-ver 可从日志自动提取，无需手动指定。

JIRA CASE 工作流（步骤11-14）：
  解析 JIRA_CASE 日志 → 提取 JIRA 基准值 → 生成对比报告 → 发布飞书文档

用法:
  # 一键全流程（SDK 版本从日志自动提取）
  python3 run_pipeline.py --log-file /path/to/lyon_xxx.log --base-token X --table-id Y

  # 指定版本（覆盖自动提取）
  python3 run_pipeline.py --sdk-ver 1.0.15.0 --log-file /path/to/lyon_xxx.log

  # 仅生成报告（步骤4-6），不涉及飞书
  python3 run_pipeline.py --sdk-ver 1.0.15.0 --json data.json --steps 4-6

  # 跳过某些步骤
  python3 run_pipeline.py --sdk-ver 1.0.15.0 --skip-step 7,8,9,10

  # 干跑模式
  python3 run_pipeline.py --sdk-ver 1.0.15.0 --json data.json --dry-run

  # JIRA CASE 全流程（解析→提取基准→对比报告→发布）
  python3 run_pipeline.py --log-file /path/to/lyon_xxx.log \\
    --sdk-ver 1.0.16.0 \\
    --jira-issues COLOGNE-3107 MAT-13034 MOD-7798 MOD-8699 WIN-1957 WIN-2552 \\
    --steps 11-14

  # JIRA CASE 仅生成报告（跳过解析和提取）
  python3 run_pipeline.py --sdk-ver 1.0.16.0 \\
    --jira-json jira_samples_1.0.16.0.json \\
    --jira-baseline jira_baseline_1.0.16.0.json \\
    --steps 13

  # JIRA CASE + BASE CASE 全流程
  python3 run_pipeline.py --log-file /path/to/lyon_xxx.log \\
    --sdk-ver 1.0.16.0 \\
    --base-token X --table-id Y \\
    --jira-issues COLOGNE-3107 MAT-13034 MOD-7798 MOD-8699 WIN-1957 WIN-2552 \\
    --steps 1-14
"""

from __future__ import annotations

import argparse
import sys

from _pipeline_common import (
    DATA_DIR,
    SCRIPTS,
    apply_config_defaults,
    auto_detect_json_file,
    configure_case_routing,
    extract_sdk_version,
    parse_steps,
    resolve_offline_summary,
)
from base_steps import BASE_STEP_HANDLERS, step_10_notify
from jira_steps import JIRA_STEP_HANDLERS, step_14_jira_publish


STEP_HANDLERS = {}
STEP_HANDLERS.update(BASE_STEP_HANDLERS)
STEP_HANDLERS.update(JIRA_STEP_HANDLERS)


def main():
    parser = argparse.ArgumentParser(
        description="LTHR 多样本仿真报告全流程编排器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # SDK 版本 (提供 --log-file 时可自动提取)
    parser.add_argument("--sdk-ver", help="SDK 版本号 (如 1.0.15.0)，不指定则从日志自动提取")
    parser.add_argument("--prev-ver", help="上一版 SDK 版本号 (如 1.0.9)")

    # 数据来源
    parser.add_argument("--log-file", help="GoMore 日志文件路径 (步骤1)")
    parser.add_argument("--json", dest="json_file", help="样本 JSON 数据文件路径")
    parser.add_argument("--offline-summary", help="lthr-offline-simulator 多样本 summary JSON 路径")
    parser.add_argument("--case", default="BASE_CASE", help="日志解析过滤 case (默认 BASE_CASE)")
    parser.add_argument("--output-dir", help="报告与中间 JSON 输出目录，默认 DATA_DIR；传 --offline-summary 时默认跟随 summary 所在目录")

    # 飞书 Base
    parser.add_argument("--base-token", help="飞书 Base token")
    parser.add_argument("--table-id", help="飞书 Base 表 ID")
    parser.add_argument("--prev-base-token", help="旧版 Base token (步骤6)")
    parser.add_argument("--base-template", help="Base 模板 token (步骤8)")
    parser.add_argument("--gomore-folder", help="GomoreSDK_Report 文件夹 token (步骤9)")

    # 飞书发布
    parser.add_argument("--base-url", help="飞书 Base 链接 (报告内引用)")
    parser.add_argument("--chat-id", help="群 ID (步骤10)")

    # JIRA CASE
    parser.add_argument("--jira-issues", nargs="*", help="JIRA Issue key 列表 (步骤12)，如 COLOGNE-3107 MOD-7798")
    parser.add_argument("--jira-json", help="JIRA 样本 JSON 路径 (步骤13，覆盖自动推断)")
    parser.add_argument("--jira-baseline", help="JIRA 基准 JSON 路径 (步骤13，覆盖自动推断)")

    # 流程控制
    parser.add_argument("--steps", default="1-10", help="执行步骤范围，如 '1-10', '4-6', '1,3,5', '11-14'")
    parser.add_argument("--skip-step", help="跳过步骤，如 '7,8,9'")
    parser.add_argument("--dry-run", action="store_true", help="干跑模式，只打印不执行")
    parser.add_argument("--all-fields", action="store_true", help="单版本报告包含完整字段")

    args = parser.parse_args()
    steps_explicit = "--steps" in sys.argv

    # 从 _pipeline_config.py 加载默认凭证（固化到 skill 中）
    apply_config_defaults(args)

    resolve_offline_summary(args)

    # 自动提取 SDK 版本
    if not args.sdk_ver:
        if args.log_file:
            args.sdk_ver = extract_sdk_version(args.log_file)
        if not args.sdk_ver:
            parser.error("请指定 --sdk-ver，或提供 --log-file 以自动提取")

    configure_case_routing(args, steps_explicit=steps_explicit)

    print(f"\n{'#'*60}")
    print(f"# LTHR 多样本仿真报告全流程")
    print(f"# SDK: {args.sdk_ver}" + (f" (vs {args.prev_ver})" if args.prev_ver else ""))
    print(f"# 模式: {'DRY-RUN' if args.dry_run else 'EXECUTE'}")
    print(f"{'#'*60}\n")

    # 解析步骤
    steps = parse_steps(args.steps)
    if args.skip_step:
        skip = set(int(s.strip()) for s in args.skip_step.split(","))
        steps = [s for s in steps if s not in skip]

    auto_detect_json_file(args)

    doc_url = None

    for step in steps:
        if step == 10:
            step_10_notify(args, doc_url=doc_url, dry_run=args.dry_run)
            continue
        if step == 14:
            result = step_14_jira_publish(args, doc_url=doc_url, dry_run=args.dry_run)
            if result:
                doc_url = result
            continue

        handler = STEP_HANDLERS.get(step)
        if handler is None:
            print(f"  [跳过] 未知步骤: {step}")
            continue

        result = handler(args, dry_run=args.dry_run)
        if step == 1 and result and not args.json_file:
            args.json_file = result
        elif step == 3 and result:
            args.json_file = result
        elif step == 7 and result:
            doc_url = result
        elif step == 11 and result and not args.jira_json:
            args.jira_json = result
        elif step == 12 and result and not args.jira_baseline:
            args.jira_baseline = result

    print(f"\n{'#'*60}")
    print(f"# 流程完成 ✅")
    print(f"# SDK: {args.sdk_ver}")
    print(f"# 执行步骤: {', '.join(str(s) for s in steps)}")
    print(f"{'#'*60}\n")


if __name__ == "__main__":
    main()

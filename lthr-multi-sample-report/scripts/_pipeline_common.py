#!/usr/bin/env python3
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

# Pipeline 14 步细化说明（供实现与调试参考）
#
# 说明：本项目将多样本仿真与报告流程分为两类 case：BASE_CASE（步骤 1-10）和
# JIRA_CASE（步骤 11-14）。以下为每一步的建议细化子步骤与依赖、需要的凭证说明。
#
# 1. 解析日志 -> 生成 samples JSON
#    - 读取 multi-sample device log（或 offline_summary 指定的 log）
#    - 提取每个样本的 header、文件名、SDK 版本、结果（PASS/FAIL）等元信息
#    - 生成 samples_{sdk_ver}.json 或 base_data_{sdk_ver}.json，输出到 output_dir
#    - 脚本：parse_gomore_samples.py
#
# 2. 合并 baseline（可选）
#    - 如果存在多个 baseline JSON，合并并去重
#    - 输出 base_data_{sdk_ver}.json
#    - 脚本/工具：compare_base_samples.py
#
# 3. 生成单版本 SDK 仿真聚合报告
#    - 使用 samples JSON 与 run 日志生成 markdown 报告（统计/图表/摘要）
#    - 脚本：generate_sim_report.py
#
# 4. 生成 Garmin/DUT 对比报告（可选）
#    - 比较 DUT 输出与 Garmin 参考，输出对比表格与结论
#    - 脚本：generate_garmin_dut_compare_report.py
#
# 5. 提取并保存 artifacts（截图、日志、JSON）
#    - 将重要 artifacts 复制到统一输出目录，并记录路径
#    - 在 workflow summary 中注入 runtime_hint（例如 device info）
#
# 6. 本地预览与校验
#    - 在本地打开/检查生成的 markdown 与 JSON，验证内容完整性
#
# 7. 写入 Feishu Base（需要 feishu token + table id）
#    - 参数：--feishu-token, --feishu-table
#    - 将 samples/聚合报告记录为若干行记录（create 或 update）
#    - 脚本：feishu_base_utils.py
#
# 8. 复制 Base 模板（可选）
#    - 使用 lark-cli/base-copy 或 Feishu API 复制模板并重命名
#    - 参数：base_template_token（或从 URL 提取），目标名称
#
# 9. 发布/归档到 Gomore Folder（需要 gomore-folder token）
#    - 将最终报告与 artifacts 上传到指定 Gomore 文件夹或归档系统
#    - 参数：--gomore-folder-token
#
# 10. 群通知（需要 chat-id 或 webhook）
#    - 在指定群/聊天发送带链接的通知（报告摘要 + 链接）
#    - 参数：--chat-id 或 webhook URL
#
# 11. 为 JIRA CASE 解析样本并生成 baseline（JIRA）
#    - 从日志中提取 JIRA_CASE 指向的 SAMPLE_DIR
#    - 将样本打包并生成 jira_samples JSON
#    - 脚本：fetch_jira_baseline.py
#
# 12. 生成 JIRA 专用报告
#    - 使用 jira_samples.json 生成针对每个 issue 的报告，包含重现步骤与 artifacts
#    - 脚本：generate_jira_report.py
#
# 13. 将结果附加到 JIRA（需要 jira 凭证）
#    - 使用 zepp/jira CLI 或 REST API 上传附件并更新 issue
#    - 参数：--jira-token 或通过环境变量
#
# 14. 在 JIRA 上创建/更新 baseline 并归档样本
#    - 将 baseline JSON 与 artifacts 上传到 JIRA 或内部存储，标注版本信息
#
# 注：步骤 7-10 与 13-14 涉及第三方凭证（Feishu / Gomore / Jira），实现时应支持
# 从命令行参数、环境变量或交互式输入获取凭证，并在日志中避免明文打印。


RE_CASE_TAG = re.compile(r"CASE\s*=\s*(BASE_CASE|JIRA_CASE)", re.IGNORECASE)
RE_JIRA_SAMPLE_DIR = re.compile(r"CASE\s*=\s*JIRA_CASE\s+SAMPLE_DIR\s*=\s*(\S+)", re.IGNORECASE)

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
LYON_DIR = os.path.abspath(os.path.join(SKILL_DIR, "../../../../Lyon"))
DATA_DIR = os.path.join(LYON_DIR, "framework/engine/sportEngine/alg_gomore/data_sample")

SCRIPTS = {
    "parse": os.path.join(SKILL_DIR, "parse_gomore_samples.py"),
    "feishu_base": os.path.join(SKILL_DIR, "feishu_base_utils.py"),
    "compare": os.path.join(SKILL_DIR, "compare_base_samples.py"),
    "sim_report": os.path.join(SKILL_DIR, "generate_sim_report.py"),
    "garmin_report": os.path.join(SKILL_DIR, "generate_garmin_dut_compare_report.py"),
    "jira_baseline": os.path.join(SKILL_DIR, "fetch_jira_baseline.py"),
    "jira_report": os.path.join(SKILL_DIR, "generate_jira_report.py"),
}


def log_step(step: int, msg: str):
    print(f"\n{'='*60}")
    print(f"[步骤 {step}] {msg}")
    print(f"{'='*60}")


def run_cmd(cmd: list, dry_run: bool = False, cwd: str = None) -> subprocess.CompletedProcess:
    """运行命令，支持干跑模式。"""
    print(f"  $ {' '.join(cmd)}")
    if dry_run:
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=cwd)
        if result.stdout:
            out_lines = result.stdout.strip().split("\n")
            for line in out_lines[:10]:
                print(f"  {line}")
            if len(out_lines) > 10:
                print(f"  ... (+{len(out_lines) - 10} lines)")
        if result.returncode != 0:
            print(f"  [错误] exit={result.returncode}: {result.stderr[:300]}", file=sys.stderr)
        return result
    except subprocess.TimeoutExpired:
        print("  [超时] 命令超过 300 秒", file=sys.stderr)
        return subprocess.CompletedProcess(cmd, -1, stdout=b"", stderr=b"Timeout")
    except FileNotFoundError as exc:
        print(f"  [错误] 命令未找到: {exc}", file=sys.stderr)
        return subprocess.CompletedProcess(cmd, -1, stdout=b"", stderr=str(exc).encode())


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def detect_log_cases(log_file: str) -> set:
    """从日志中快速探测包含的 case 类型。"""
    cases = set()
    if not log_file or not os.path.isfile(log_file):
        return cases
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                match = RE_CASE_TAG.search(line)
                if not match:
                    continue
                cases.add(match.group(1).upper())
                if len(cases) >= 2:
                    break
    except (IOError, OSError) as exc:
        print(f"  [警告] 无法探测日志 case: {exc}")
    return cases


def has_detected_case(args, case_name: str) -> bool:
    """若已完成 case 探测，则只在目标 case 存在时执行对应步骤。"""
    detected_cases = getattr(args, "detected_cases", None)
    if not detected_cases:
        return True
    return case_name.upper() in detected_cases


def infer_jira_issues_from_json(json_path: str) -> list:
    """从 jira_samples JSON 顶层 key 中恢复 issue 列表。"""
    if not json_path or not os.path.isfile(json_path):
        return []

    data = load_json(json_path)
    if not isinstance(data, dict):
        return []

    return sorted({
        str(sample_key).split("/", 1)[0].upper()
        for sample_key in data.keys()
        if "/" in str(sample_key)
    })


def infer_jira_issues_from_log(log_file: str) -> list:
    """从原始日志的 JIRA_CASE sample header 中恢复 issue 列表。"""
    issues = set()
    if not log_file or not os.path.isfile(log_file):
        return []

    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                match = RE_JIRA_SAMPLE_DIR.search(line)
                if match:
                    issues.add(match.group(1).upper())
    except (IOError, OSError) as exc:
        print(f"  [警告] 无法从日志推断 JIRA issues: {exc}")
        return []
    return sorted(issues)


def configure_case_routing(args, steps_explicit: bool) -> None:
    """offline-summary 模式下按日志内容自动决定 case 路由与默认步骤。"""
    args.detected_cases = detect_log_cases(args.log_file)
    if args.detected_cases:
        print(f"[auto-case] 检测到 case: {', '.join(sorted(args.detected_cases))}")
    elif args.offline_summary:
        print("[auto-case] 未从日志中检测到 CASE 标记，保留用户原始 steps 配置")

    if not args.offline_summary or steps_explicit or not args.detected_cases:
        return

    if args.detected_cases == {"BASE_CASE"}:
        args.steps = "1-10"
    elif args.detected_cases == {"JIRA_CASE"}:
        args.steps = "11-14"
    else:
        args.steps = "1-14"
    print(f"[auto-case] 未显式指定 --steps，自动采用步骤: {args.steps}")


def get_output_dir(args) -> str:
    """Return the explicit output directory or a stable temp directory for this input."""
    output_dir = getattr(args, "output_dir", None) or resolve_default_output_dir(args)
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def resolve_default_output_dir(args) -> str:
    """Keep generated artifacts out of Lyon's input data_sample directory."""
    inputs = (
        ("summary", getattr(args, "offline_summary", None)),
        ("log", getattr(args, "log_file", None)),
        ("json", getattr(args, "json_file", None)),
        ("jira_json", getattr(args, "jira_json", None)),
    )
    source_kind, source_path = next(((kind, path) for kind, path in inputs if path), ("sdk", ""))
    if source_path:
        absolute_path = os.path.abspath(os.path.expanduser(source_path))
        stem = os.path.splitext(os.path.basename(absolute_path))[0]
        identity = absolute_path
    else:
        stem = getattr(args, "sdk_ver", "unknown") or "unknown"
        jira_issues = getattr(args, "jira_issues", None) or []
        identity = f"{stem}:{getattr(args, 'case', '')}:{','.join(jira_issues)}"

    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "input"
    path_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
    task_dir = f"{source_kind}_{safe_stem}_{path_hash}"
    return os.path.join(tempfile.gettempdir(), "lthr-multi-sample-report", task_dir)


def get_search_dirs(args) -> list:
    """输入文件自动推断时的搜索目录，优先用户输出目录。"""
    dirs = []
    output_dir = getattr(args, "output_dir", None)
    if output_dir:
        dirs.append(output_dir)
    if DATA_DIR not in dirs:
        dirs.append(DATA_DIR)
    return dirs


def resolve_offline_summary(args) -> None:
    """从 offline-simulator 单样本或多样本摘要中恢复报告输入。"""
    if not getattr(args, "offline_summary", None):
        return

    summary = load_json(args.offline_summary)
    artifacts = summary.get("artifacts") or {}
    workflow = summary.get("workflow") or ""
    status = summary.get("status") or ""
    runtime_hint = summary.get("runtime_hint") or ""
    source = summary.get("source") or {}
    issue = summary.get("issue") or {}
    log_path = artifacts.get("log_path") or summary.get("log_path")
    # img_log_path 优先级最高：IMG 提取的完整设备日志
    img_log_path = summary.get("img_log_path", "")
    if img_log_path and os.path.isfile(img_log_path):
        log_path = img_log_path
        print(f"[offline-summary] 使用 IMG 提取日志: {img_log_path}")

    print(f"[offline-summary] workflow={workflow or '?'} status={status or '?'}")
    args.offline_summary_status = status
    if runtime_hint:
        print(f"[offline-summary] runtime_hint: {runtime_hint}")

    if status in {"partial", "failed"}:
        print(
            "[offline-summary] 警告: C 端仿真状态为 {}；报告会保留已产生数据，"
            "请结合样本状态和原始日志判断未完成部分。".format(status)
        )

    if workflow and workflow not in {"single_sample", "multi_sample", "img_extract"}:
        print(f"[offline-summary] 警告: 非预期 workflow={workflow}，仍继续尝试解析")

    issue_key = str(issue.get("key") or "").strip()
    if source.get("kind") == "jira_attachment" and issue_key:
        args.offline_summary_issue_key = issue_key
        if not getattr(args, "jira_issues", None):
            args.jira_issues = [issue_key]
        print(f"[offline-summary] Jira issue: {issue_key}")

    if not args.log_file and log_path:
        args.log_file = log_path
        print(f"[offline-summary] 使用日志: {args.log_file}")

    if not args.log_file:
        print("[offline-summary] 警告: 摘要内未找到可解析的 log_path，步骤1/11 将无法自动解析")


def extract_sdk_version(log_file: str) -> str:
    """从日志中提取 GoMore SDK 版本号 (格式 X.X.X.X)。"""
    re_ver = re.compile(r"(?:GoMore\s+)?SDK\s+(?:Version|Version String):?\s*(\d+\.\d+\.\d+\.\d+)", re.IGNORECASE)
    matches = []
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                match = re_ver.search(line)
                if match:
                    matches.append(match.group(1))
    except (IOError, OSError) as exc:
        print(f"  [警告] 无法读取日志文件提取版本: {exc}")

    if not matches:
        return None

    version = matches[-1]
    unique_versions = []
    for item in matches:
        if item not in unique_versions:
            unique_versions.append(item)

    if len(unique_versions) > 1:
        print(
            "  [警告] 日志中检测到多个 SDK 版本: "
            + ", ".join(unique_versions)
            + f"；使用最后一次出现的版本 {version}"
        )
    else:
        print(f"  [自动] 从日志提取 SDK 版本: {version}")
    return version


def auto_detect_json_file(args) -> None:
    """自动推断 BASE_CASE 输入 JSON。"""
    if args.json_file:
        return

    candidates = []
    for base_dir in get_search_dirs(args):
        candidates.extend([
            os.path.join(base_dir, f"samples_{args.sdk_ver}.json"),
            os.path.join(base_dir, f"base_data_{args.sdk_ver}.json"),
        ])
    for base_dir in get_search_dirs(args):
        candidates.extend(sorted(glob.glob(os.path.join(base_dir, "base_case_updates_*.json"))))

    for candidate in candidates:
        if os.path.exists(candidate):
            args.json_file = candidate
            print(f"[自动] 使用 JSON: {candidate}")
            return


# ── 默认凭证配置（固化到 skill 中，免于每次重复输入）──
_CFG_LOADED = False
_BASE_TOKEN = ""
_TABLE_ID = ""
_BASE_TEMPLATE = ""
_BASE_URL = ""
_GOMORE_FOLDER = ""
_CHAT_ID = ""


def _load_pipeline_config() -> None:
    """从 _pipeline_config.py 加载默认凭证，文件不存在时静默跳过。"""
    global _CFG_LOADED, _BASE_TOKEN, _TABLE_ID, _BASE_TEMPLATE, _BASE_URL, _GOMORE_FOLDER, _CHAT_ID
    if _CFG_LOADED:
        return
    _CFG_LOADED = True
    try:
        import importlib.util
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_pipeline_config.py")
        if not os.path.isfile(cfg_path):
            return
        spec = importlib.util.spec_from_file_location("_pipeline_config", cfg_path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _BASE_TOKEN = getattr(mod, "BASE_TOKEN", "") or ""
            _TABLE_ID = getattr(mod, "TABLE_ID", "") or ""
            _BASE_TEMPLATE = getattr(mod, "BASE_TEMPLATE", "") or ""
            _BASE_URL = getattr(mod, "BASE_URL", "") or ""
            _GOMORE_FOLDER = getattr(mod, "GOMORE_FOLDER", "") or ""
            _CHAT_ID = getattr(mod, "CHAT_ID", "") or ""
    except Exception:
        pass


def apply_config_defaults(args) -> None:
    """将 _pipeline_config.py 中的默认值填入未指定的 CLI 参数。"""
    _load_pipeline_config()
    if not getattr(args, "base_token", None) and _BASE_TOKEN:
        args.base_token = _BASE_TOKEN
    if not getattr(args, "table_id", None) and _TABLE_ID:
        args.table_id = _TABLE_ID
    if not getattr(args, "base_template", None) and _BASE_TEMPLATE:
        args.base_template = _BASE_TEMPLATE
    if not getattr(args, "base_url", None) and _BASE_URL:
        args.base_url = _BASE_URL
    if not getattr(args, "gomore_folder", None) and _GOMORE_FOLDER:
        args.gomore_folder = _GOMORE_FOLDER
    if not getattr(args, "chat_id", None) and _CHAT_ID:
        args.chat_id = _CHAT_ID


def parse_steps(step_spec: str) -> list:
    """解析步骤范围，如 '1-10', '4-6', '1,3,5'。"""
    steps = set()
    for part in step_spec.split(","):
        part = part.strip()
        if "-" in part:
            begin, end = part.split("-", 1)
            for step in range(int(begin), int(end) + 1):
                steps.add(step)
        else:
            steps.add(int(part))
    return sorted(steps)

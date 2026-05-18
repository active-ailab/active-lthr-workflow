#!/usr/bin/env python3
from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import time

from _pipeline_common import (
    LYON_DIR,
    SCRIPTS,
    get_output_dir,
    get_search_dirs,
    has_detected_case,
    load_json,
    log_step,
    run_cmd,
)
from _report_utils import _parse_markdown_table_records, run_lark, _payload_to_records


# ── 共享辅助函数 ──

def _list_drive_folder(parent_token, dry_run=False):
    """列出云盘文件夹内容，返回文件列表。使用原生 API 格式。"""
    if dry_run or not parent_token:
        return []
    try:
        import subprocess, json
        result = subprocess.run(
            ["lark-cli", "drive", "files", "list",
             "--params", f'{{"folder_token":"{parent_token}","page_size":100}}',
             "--format", "json", "--as", "user"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        if data.get("code") != 0:
            return []
        return data.get("data", {}).get("files", [])
    except Exception:
        return []


def ensure_version_folder(args, dry_run=False):
    """在 GomoreSDK_Report 下确认版本子文件夹，返回 folder_token。

    策略：
      1. 查找已有同名文件夹，取最新创建的那个
      2. 没找到才新建
      3. 单次运行内缓存到 args._gomore_ver_token
    """
    if not args.gomore_folder:
        return ""
    cached = getattr(args, "_gomore_ver_token", None)
    if cached:
        return cached

    ver_folder = args.sdk_ver

    # 1) 查找已有同名文件夹
    existing = _list_drive_folder(args.gomore_folder, dry_run=dry_run)
    matches = [f for f in existing if f.get("name") == ver_folder and f.get("type") == "folder"]
    if matches:
        # 按创建时间取最新
        matches.sort(key=lambda f: int(f.get("created_time", "0")), reverse=True)
        token = matches[0].get("token", "")
        if token:
            args._gomore_ver_token = token
            print(f"  [信息] 使用已有版本文件夹 {ver_folder} → {token[:8]}...")
            return token

    # 2) 没找到 → 新建
    result = run_lark([
        "drive", "+create-folder",
        "--folder-token", args.gomore_folder,
        "--name", ver_folder,
    ], dry_run=dry_run, allow_raw=True)
    if result.get("ok"):
        token = (result.get("data") or {}).get("folder_token", "")
        if token:
            args._gomore_ver_token = token
            return token
    print(f"  [警告] 无法创建版本文件夹 {ver_folder}")
    return ""


def step_1_parse_log(args, dry_run=False):
    """步骤1: 解析 GoMore 日志提取样本。"""
    log_step(1, "解析 GoMore 日志")
    if not has_detected_case(args, "BASE_CASE"):
        print("  [跳过] 日志中未检测到 BASE_CASE")
        return None
    if not args.log_file:
        print("  [跳过] 未指定 --log-file")
        return None

    out_path = os.path.join(get_output_dir(args), f"samples_{args.sdk_ver}.json")
    cmd = [
        sys.executable, SCRIPTS["parse"],
        args.log_file,
        "-o", out_path,
    ]
    if args.case:
        cmd += ["--case", args.case]
    run_cmd(cmd, dry_run=dry_run)
    return out_path


def _fetch_all_record_ids(args, dry_run=False):
    """获取 Base 表中所有记录的 record_id 列表。"""
    # 需要 --format json 来获取 record_id_list
    result = run_lark([
        "base", "+record-list",
        "--base-token", args.base_token,
        "--table-id", args.table_id,
        "--limit", "200",
        "--format", "json",
    ], dry_run=dry_run, allow_raw=True)
    if not result.get("ok"):
        return []
    payload = result.get("data") or {}
    return payload.get("record_id_list") or []


def _delete_all_records(args, record_ids, dry_run=False):
    """批量删除所有记录。"""
    if not record_ids:
        return True
    # 分批删除（+record-delete 一次接受多个 --record-id）
    batch_size = 50
    ok = True
    for i in range(0, len(record_ids), batch_size):
        batch = record_ids[i:i + batch_size]
        cmd = [
            "base", "+record-delete",
            "--base-token", args.base_token,
            "--table-id", args.table_id,
            "--yes",
        ]
        for rid in batch:
            cmd += ["--record-id", rid]
        result = run_lark(cmd, dry_run=dry_run, allow_raw=True)
        if not result.get("ok"):
            print(f"  [警告] 删除记录失败: {result.get('error', '?')}")
            ok = False
    return ok


def _resolve_full_source(args, dry_run=False):
    """优先使用完整的 base_case_updates JSON（含全部样本），而非解析日志的少数样本。"""
    if not args.json_file:
        return args.json_file
    fname = os.path.basename(args.json_file)
    if fname.startswith("samples_") or fname.startswith("base_data_"):
        for base_dir in get_search_dirs(args):
            candidates = sorted(glob.glob(os.path.join(base_dir, "base_case_updates_*.json")))
            if candidates:
                full = candidates[-1]
                with open(full) as f:
                    data = json.load(f)
                if isinstance(data, dict) and len(data) >= 5:
                    print(f"  [信息] 使用完整数据源 {os.path.basename(full)} ({len(data)} 条)")
                    return full
    return args.json_file


def _resolve_report_source(args, dry_run=False):
    """报告优先使用更完整的数据源：base_case_updates > base_data > 当前 json。"""
    source = _resolve_full_source(args, dry_run=dry_run)
    if not source:
        return source

    fname = os.path.basename(source)
    if fname.startswith("samples_"):
        for base_dir in get_search_dirs(args):
            candidate = os.path.join(base_dir, f"base_data_{args.sdk_ver}.json")
            if os.path.exists(candidate):
                print(f"  [信息] 使用 Base 数据源 {os.path.basename(candidate)}")
                return candidate
    return source


def _extract_record_map(result: dict) -> dict:
    """从 lark-cli 返回中提取 record map。"""
    if result.get("raw"):
        records = _parse_markdown_table_records(result.get("raw", ""))
        if records:
            return records
    for key in ("records", "data", "items"):
        payload = result.get(key) or {}
        if isinstance(payload, dict) and "fields" in payload:
            return _payload_to_records(payload)
    return {}


def _extract_record_ids(result: dict) -> list:
    payload = result.get("data") or {}
    if isinstance(payload, dict):
        record_ids = payload.get("record_id_list") or []
        if record_ids:
            return record_ids

    records = _extract_record_map(result)
    return [record.get("_record_id") for record in records.values() if record.get("_record_id")]


def _sample_key_aliases(sample_name) -> set[str]:
    """为样本名生成兼容别名，兼容旧的去前导 0 写法。"""
    text = str(sample_name or "").strip().lower()
    if not text:
        return set()

    aliases = {text}
    stripped = text.lstrip("0")
    if stripped:
        aliases.add(stripped)
    if len(text) >= 4:
        aliases.add(text[-4:])
    return aliases


def _merge_template_records(template_records: dict, new_records: dict) -> dict:
    """按模板顺序合并仿真结果，保留 Garmin/DUT 等基准字段。"""
    if not template_records:
        merged = {}
        for sample_name, fields in (new_records or {}).items():
            record = dict(fields)
            record["样本"] = record.get("样本") or sample_name
            merged[sample_name] = record
        return merged

    alias_to_new = {}
    for sample_name, fields in (new_records or {}).items():
        for alias in _sample_key_aliases(sample_name):
            alias_to_new.setdefault(alias, (sample_name, fields))

    merged = {}
    consumed = set()
    for sample_name, fields in template_records.items():
        record = dict(fields)
        matched = None
        for alias in _sample_key_aliases(sample_name):
            matched = alias_to_new.get(alias)
            if matched:
                break
        if matched:
            incoming_name, incoming_fields = matched
            record.update(incoming_fields)
            consumed.add(incoming_name)
        record["样本"] = fields.get("样本") or sample_name
        merged[sample_name] = record

    for sample_name, fields in (new_records or {}).items():
        if sample_name in consumed:
            continue
        record = dict(fields)
        record["样本"] = record.get("样本") or sample_name
        merged[sample_name] = record
    return merged


def _fetch_all_records_by_token(base_token, table_id, dry_run=False):
    """获取指定 Base 表的所有记录，保留返回顺序。"""
    result = _run_record_list_with_retry([
        "base", "+record-list",
        "--base-token", base_token,
        "--table-id", table_id,
        "--limit", "200",
    ], dry_run=dry_run)
    if not result.get("ok"):
        return {}
    return _extract_record_map(result)


def _run_record_list_with_retry(cmd_args, dry_run=False, max_retries=12):
    """record-list 在 Base 复制期可能短暂失败，做轻量重试。"""
    last_result = {"ok": False, "error": "unknown"}
    for attempt in range(1, max_retries + 1):
        result = run_lark(cmd_args, dry_run=dry_run, allow_raw=True, force_json=True)
        if result.get("ok"):
            return result
        error_msg = str(result.get("error", ""))
        last_result = result
        if "base is copying" in error_msg or "800004046" in error_msg:
            print(f"  [等待] Base 仍在复制中，等待 3 秒... ({attempt}/{max_retries})")
            time.sleep(3)
            continue
        return result
    return last_result


# ── 模板复制与目标 Base 工具 ───────────────────────────────

def _copy_template_and_get_token(args, dry_run=False):
    """复制模板 Base，返回新 Base 的 token。缓存到 args._copied_base_token。"""
    cached = getattr(args, "_copied_base_token", None)
    if cached:
        return cached
    if not args.base_template:
        print("  [跳过] 未指定 --base-template")
        return ""

    ver_token = ensure_version_folder(args, dry_run=dry_run)
    name = f"SDK_{args.sdk_ver}版本仿真测试结果"
    cmd = [
        "base", "+base-copy",
        "--base-token", args.base_template,
        "--name", name,
    ]
    if ver_token:
        cmd += ["--folder-token", ver_token]

    result = run_lark(cmd, dry_run=dry_run, allow_raw=True)
    new_token = ""
    new_table_id = args.table_id or ""
    if result.get("ok"):
        data = result.get("data") or {}
        new_token = data.get("base_token", "") or data.get("base", {}).get("base_token", "")
        if new_token:
            args._copied_base_token = new_token
            print(f"  [完成] 已复制模板 → 新 Base token: {new_token}")
            # 先等待 Base 复制完成
            _wait_for_base_ready(new_token, dry_run=dry_run)
            # 再查询新 Base 的 table ID（复制后可能变化）
            resolved_table = _resolve_table_id(new_token, args.table_id, dry_run=dry_run)
            if resolved_table and resolved_table != args.table_id:
                new_table_id = resolved_table
                print(f"  [信息] 新 Base 表 ID: {new_table_id}")
            args._copied_base_table_id = new_table_id
    if not new_token:
        print(f"  [警告] 模板复制失败，回退到使用原模板")
        new_token = args.base_template
        args._copied_base_token = new_token
    return new_token


def _resolve_table_id(base_token, default_table_id, dry_run=False):
    """查询 Base 的第一个可用表 ID。"""
    result = run_lark([
        "base", "+table-list",
        "--base-token", base_token,
    ], dry_run=dry_run, allow_raw=True)
    if result.get("ok"):
        tables = (result.get("data") or {}).get("tables") or []
        if tables:
            return tables[0].get("id", "") or default_table_id
    return default_table_id


def _wait_for_base_ready(base_token, max_retries=12, dry_run=False):
    """等待 Base 复制完成（最多 36 秒）。"""
    for attempt in range(1, max_retries + 1):
        result = run_lark([
            "base", "+table-list",
            "--base-token", base_token,
        ], dry_run=dry_run, allow_raw=True)
        if result.get("ok"):
            return True
        error_msg = str(result.get("error", ""))
        if "base is copying" in error_msg or "800004046" in error_msg:
            print(f"  [等待] Base 复制中，等待 3 秒... ({attempt}/{max_retries})")
            time.sleep(3)
            continue
        return False
    print(f"  [警告] Base 复制超时，将尝试继续操作")
    return False


def _target_base_token(args):
    """获取目标 Base token：优先使用复制后的副本，fallback 到原模板。"""
    cached = getattr(args, "_copied_base_token", None)
    return cached or args.base_template or args.base_token or ""


def _delete_records(base_token, table_id, record_ids, dry_run=False):
    """批量删除 Base 记录。"""
    if not record_ids:
        return True
    for i in range(0, len(record_ids), 50):
        cmd = [
            "base", "+record-delete",
            "--base-token", base_token,
            "--table-id", table_id,
            "--yes",
        ]
        for rid in record_ids[i:i+50]:
            cmd += ["--record-id", rid]
        result = run_lark(cmd, dry_run=dry_run, allow_raw=True)
        if not result.get("ok"):
            return False
    return True


def step_2_write_base(args, dry_run=False):
    """步骤2: 复制模板 Base → 写入数据到副本（模板不修改）。"""
    log_step(2, "写入飞书 Base")
    if not args.base_template:
        print("  [跳过] 未指定 --base-template")
        return
    if not args.json_file:
        print("  [跳过] 未指定 --json (需先解析日志)")
        return

    # 1) 复制模板，获取新 Base token
    target_token = _copy_template_and_get_token(args, dry_run=dry_run)
    target_table = getattr(args, "_copied_base_table_id", None) or args.table_id
    if not target_token or not target_table:
        print("  [跳过] Base token 或 table-id 无效")
        return

    # 2) 读取模板记录，后续按模板顺序合并仿真字段，保留 Garmin/DUT 等基准数据
    template_records = {}
    if not dry_run:
        template_records = _fetch_all_records_by_token(target_token, target_table, dry_run=dry_run)
        if template_records:
            print(f"  [信息] 模板副本现有记录: {len(template_records)} 条")

    # 3) 合并并重写新数据到副本
    source = _resolve_full_source(args, dry_run=dry_run)
    data = load_json(source or args.json_file)
    if not isinstance(data, dict):
        print("  [跳过] 当前 JSON 不是 dict-of-dicts 结构")
        return

    merged_data = _merge_template_records(template_records, data)

    if not dry_run:
        existing = _fetch_all_record_ids_by_token(target_token, target_table)
        if existing:
            _delete_records(target_token, target_table, existing, dry_run=dry_run)
            print(f"  [完成] 已清空新 Base 旧记录: {len(existing)} 条")

    written = 0
    for sample_name, fields in merged_data.items():
        record = dict(fields)
        record["样本"] = record.get("样本") or sample_name
        rec_json = json.dumps(record, ensure_ascii=False)
        run_lark([
            "base", "+record-upsert",
            "--base-token", target_token,
            "--table-id", target_table,
            "--json", rec_json,
        ], dry_run=dry_run, allow_raw=True)
        written += 1

    print(f"  [完成] 已合并写入 {written} 条记录到新 Base（保留模板基准字段与顺序）")


def _fetch_all_record_ids_by_token(base_token, table_id):
    """获取指定 Base 表的所有 record_id。"""
    result = _run_record_list_with_retry([
        "base", "+record-list",
        "--base-token", base_token,
        "--table-id", table_id,
        "--limit", "200",
    ], dry_run=False)
    if not result.get("ok"):
        return []
    return _extract_record_ids(result)


def step_3_pull_base(args, dry_run=False):
    """步骤3: 从副本拉取 Base 数据。"""
    log_step(3, "拉取 Base 数据")
    target_token = _target_base_token(args)
    target_table = getattr(args, "_copied_base_table_id", None) or args.table_id
    if not target_token or not target_table:
        print("  [跳过] 未指定 base-token / table-id")
        return None

    out_path = os.path.join(get_output_dir(args), f"base_data_{args.sdk_ver}.json")
    result = _run_record_list_with_retry([
        "base", "+record-list",
        "--base-token", target_token,
        "--table-id", target_table,
        "--limit", "200",
    ], dry_run=dry_run)

    if not dry_run and result.get("ok"):
        records = _extract_record_map(result)
        if records:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
            print(f"  [完成] 已保存 {len(records)} 条记录至 {out_path}")
        else:
            print(f"  [注意] 未从返回数据中提取到记录，保存原始结果为 {out_path}")
    return out_path


def step_4_garmin_report(args, dry_run=False):
    """步骤4: 生成三源对比报告。"""
    log_step(4, "生成三源对比报告")
    source = _resolve_report_source(args, dry_run=dry_run)
    if not source:
        print("  [跳过] 未指定 --json")
        return

    out_path = os.path.join(get_output_dir(args), f"garmin_dut_sim_compare_report_{args.sdk_ver}.md")
    cmd = [
        sys.executable, SCRIPTS["garmin_report"],
        "--json", source,
        "--sdk-version", args.sdk_ver,
        "-o", out_path,
    ]
    if args.base_url:
        cmd += ["--base-url", args.base_url]
    run_cmd(cmd, dry_run=dry_run)


def step_5_sim_report(args, dry_run=False):
    """步骤5: 生成单版本仿真报告。"""
    log_step(5, "生成单版本仿真报告")
    source = _resolve_report_source(args, dry_run=dry_run)
    if not source:
        print("  [跳过] 未指定 --json")
        return

    out_path = os.path.join(get_output_dir(args), f"sdk_{args.sdk_ver}_sim_report.md")
    cmd = [
        sys.executable, SCRIPTS["sim_report"],
        "--json", source,
        "--sdk-version", args.sdk_ver,
        "-o", out_path,
    ]
    if args.all_fields:
        cmd += ["--all-fields"]
    run_cmd(cmd, dry_run=dry_run)


def step_6_compare_report(args, dry_run=False):
    """步骤6: 生成版本对比报告。"""
    log_step(6, "生成版本对比报告 (vs 上一版)")
    if not args.prev_ver:
        print("  [跳过] 未指定 --prev-ver")
        return

    old_json = None
    for base_dir in get_search_dirs(args):
        candidate = os.path.join(base_dir, f"base_data_{args.prev_ver}.json")
        if os.path.exists(candidate):
            old_json = candidate
            break
    if old_json is None:
        old_json = os.path.join(get_output_dir(args), f"base_data_{args.prev_ver}.json")
    if not os.path.exists(old_json) and args.prev_base_token:
        print("  [注意] 旧版 JSON 未找到，尝试从 --prev-base-token 拉取...")
        result = run_lark([
            "base", "+record-list",
            "--base-token", args.prev_base_token,
            "--table-id", args.table_id,
            "--limit", "200",
        ], dry_run=dry_run, allow_raw=True)
        if not dry_run and result.get("ok"):
            with open(old_json, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

    if not dry_run and not os.path.exists(old_json):
        print(f"  [跳过] 旧版数据文件不存在: {old_json}")
        return

    out_path = os.path.join(get_output_dir(args), f"sdk_{args.prev_ver}_vs_{args.sdk_ver}_compare_report.md")
    cmd = [
        sys.executable, SCRIPTS["compare"],
        "--old-json", old_json,
        "--new-json", args.json_file,
        "--old-label", f"SDK {args.prev_ver}",
        "--new-label", f"SDK {args.sdk_ver}",
        "--report", out_path,
    ]
    run_cmd(cmd, dry_run=dry_run)


def step_7_publish_doc(args, dry_run=False):
    """步骤7: 发布报告到飞书文档（归档到版本子文件夹）。"""
    log_step(7, "发布报告到飞书文档")
    report_path = os.path.join(get_output_dir(args), f"garmin_dut_sim_compare_report_{args.sdk_ver}.md")
    if not os.path.exists(report_path):
        print(f"  [跳过] 报告文件不存在: {report_path}")
        return

    if dry_run:
        print(f"  [干跑] lark-cli docs +create --title ... --markdown ... --folder-token <ver>")
        return "https://dryrun.feishu.cn/doc/xxx"

    # 先确保版本子文件夹存在
    ver_token = ensure_version_folder(args, dry_run=False)

    with open(report_path, "r", encoding="utf-8") as f:
        content = f.read()

    cmd = [
        "docs", "+create",
        "--title", f"GoMore SDK {args.sdk_ver} 三源对比报告",
        "--markdown", content,
    ]
    if ver_token:
        cmd += ["--folder-token", ver_token]

    result = run_lark(cmd, allow_raw=True)
    if not result.get("ok"):
        print("  [重试] 通过 shell 调用...")
        retry_cmd = ["lark-cli", "docs", "+create",
                     "--title", f"GoMore SDK {args.sdk_ver} 三源对比报告",
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


def step_8_copy_base(args, dry_run=False):
    """步骤8: 复制 Base（如步骤2已完成复制则跳过）。"""
    log_step(8, "复制 Base 多维表格")
    # 步骤2 已复制过模板 → 跳过
    if getattr(args, "_copied_base_token", None):
        print("  [跳过] 步骤2 已复制模板")
        return None
    if not args.base_template:
        print("  [跳过] 未指定 --base-template")
        return None

    ver_token = ensure_version_folder(args, dry_run=dry_run)

    cmd = [
        "base", "+base-copy",
        "--base-token", args.base_template,
        "--name", f"SDK_{args.sdk_ver}版本仿真测试结果",
    ]
    if ver_token:
        cmd += ["--folder-token", ver_token]

    result = run_lark(cmd, dry_run=dry_run, allow_raw=True)

    if not dry_run and result.get("ok"):
        print("  [完成] Base 已创建")
    return result


def step_9_archive(args, dry_run=False):
    """步骤9: 归档日志文件到版本子文件夹。"""
    log_step(9, "归档日志文件")
    if not args.gomore_folder:
        print("  [跳过] 未指定 --gomore-folder")
        return

    if not args.log_file or not os.path.exists(args.log_file):
        print("  [跳过] 日志文件不存在")
        return

    gz_path = args.log_file + ".gz"
    if not dry_run:
        import gzip
        with open(args.log_file, "rb") as f_in:
            with gzip.open(gz_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
    print(f"  [完成] 日志已压缩: {gz_path}")

    # 1) 先上传到根目录，捕获 file_token
    gz_dir = os.path.dirname(os.path.abspath(gz_path))
    gz_name = os.path.basename(gz_path)
    upload_result = run_lark([
        "drive", "+upload",
        "--file", gz_name,
    ], dry_run=dry_run, cwd=gz_dir, allow_raw=True)

    file_token = ""
    if upload_result.get("ok"):
        file_token = (upload_result.get("data") or {}).get("file_token", "")
        file_url = (upload_result.get("data") or {}).get("url", "")
        if file_token:
            print(f"  [完成] 已上传至云盘: {file_url}")
    else:
        print(f"  [完成] 压缩包已保留在本地: {gz_path}")
        return

    # 2) 使用公共的版本文件夹（与步骤7/8/14 共享同一目录）
    ver_token = ensure_version_folder(args, dry_run=dry_run)
    if not ver_token:
        print(f"  [警告] 版本文件夹获取失败，文件保留在根目录")
        return

    # 3) 移动文件到版本子文件夹
    move_result = run_lark([
        "drive", "+move",
        "--file-token", file_token,
        "--folder-token", ver_token,
        "--type", "file",
    ], dry_run=dry_run, allow_raw=True)

    if move_result.get("ok"):
        folder_url = f"https://zepp.feishu.cn/drive/folder/{ver_token}"
        print(f"  [完成] 日志已归档至 {args.sdk_ver}/ → {folder_url}")
    else:
        print(f"  [警告] 文件移动失败，文件仍保留在根目录")


def step_10_notify(args, doc_url=None, dry_run=False):
    """步骤10: 发送群通知。"""
    log_step(10, "发送群通知")
    if not args.chat_id:
        print("  [跳过] 未指定 --chat-id")
        return

    msg = (
        f"【SDK {args.sdk_ver} 仿真报告】已完成\n\n"
        f"报告：{doc_url or '（请查看云空间 GomoreSDK_Report 文件夹）'}\n\n"
        f"步骤完成：\n"
        f"- 三源对比报告 ✅\n"
        f"- 单版本仿真报告 ✅\n"
    )
    if args.prev_ver:
        msg += f"- SDK {args.prev_ver} vs {args.sdk_ver} 版本对比 ✅\n"
    msg += "\n#运动算法 #乳酸阈"

    run_lark([
        "im", "+messages-send",
        "--chat-id", args.chat_id,
        "--markdown", msg,
    ], dry_run=dry_run, allow_raw=True)
    print("  [完成] 通知已发送")


BASE_STEP_HANDLERS = {
    1: step_1_parse_log,
    2: step_2_write_base,
    3: step_3_pull_base,
    4: step_4_garmin_report,
    5: step_5_sim_report,
    6: step_6_compare_report,
    7: step_7_publish_doc,
    8: step_8_copy_base,
    9: step_9_archive,
}

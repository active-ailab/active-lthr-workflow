#!/usr/bin/env python3
"""
feishu_base_utils.py - Feishu Base 交互工具

提供从 Feishu Base 拉取记录、写入记录的 Python 封装。
实际通过共享 run_lark 封装调用 lark-cli 命令。
"""

import json
import sys

from _report_utils import _parse_markdown_table_records, _payload_to_records, run_lark


def fetch_records(base_token: str, table_id: str,
                  output: str = None, identity: str = "user") -> dict:
    """从 Feishu Base 拉取所有记录。
    
    返回 { sample_name: {...} } 格式。
    """
    # 先获取 fields
    result = run_lark([
        "base", "+record-list",
        "--base-token", base_token,
        "--table-id", table_id,
        "--limit", "200",
    ], identity=identity)

    if not result.get("ok"):
        print(f"拉取记录失败: {result}", file=sys.stderr)
        return {}

    # 提取 data，兼容 JSON 和 Markdown table 两种输出
    data = result.get("data", {})
    records = _payload_to_records(data)
    if not records and result.get("raw"):
        records = _parse_markdown_table_records(result.get("raw", ""))

    if output:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"记录已保存: {output}")

    return records


def write_records(base_token: str, table_id: str,
                  records: list,
                  identity: str = "user"):
    """批量写入记录到 Feishu Base。
    
    Args:
        records: [{ "fields": { "字段名": 值, ... } }]
    """
    written = 0
    for rec in records:
        fields = rec.get("fields", rec)
        rec_json = json.dumps(fields, ensure_ascii=False)
        result = run_lark([
            "base", "+record-upsert",
            "--base-token", base_token,
            "--table-id", table_id,
            "--json", rec_json,
        ], identity=identity)
        if result.get("ok"):
            written += 1
    return written

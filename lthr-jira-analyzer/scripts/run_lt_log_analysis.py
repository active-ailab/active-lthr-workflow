#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile, is_zipfile


def _safe_reconfigure_stdio():
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def find_zepp_cmd():
    # Windows may expose zepp as zepp.cmd / zepp.exe
    for name in ["zepp", "zepp.cmd", "zepp.exe"]:
        p = shutil.which(name)
        if p:
            return [name]
    raise RuntimeError("zepp command not found in PATH")


def run_cmd(cmd, cwd=None, check=True):
    p = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if check and p.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\n"
            f"exit={p.returncode}\nstdout:\n{p.stdout}\nstderr:\n{p.stderr}"
        )
    return p


def get_issue_meta(zepp_cmd, issue_key):
    p = run_cmd(zepp_cmd + ["jira", "get", issue_key])
    out = p.stdout
    m_link = re.search(r"链接:\s*(https?://\S+)", out)
    m_title = re.search(r"标题:\s*(.+)", out)
    m_status = re.search(r"状态:\s*(.+)", out)
    return {
        "key": issue_key,
        "link": m_link.group(1).strip() if m_link else "",
        "title": m_title.group(1).strip() if m_title else "",
        "status": m_status.group(1).strip() if m_status else "",
    }


def list_jira_attachments(zepp_cmd, issue_key):
    p = run_cmd(zepp_cmd + ["jira", "attachment", "list", issue_key])
    names = []
    for line in p.stdout.splitlines():
        m = re.search(r"\]\s+([^\s]+)\s*$", line.strip())
        if m and not m.group(1).startswith("大小:"):
            names.append(m.group(1))
    return names


def download_jira_attachments(zepp_cmd, issue_key, download_dir):
    download_dir.mkdir(parents=True, exist_ok=True)
    # Run in target dir to avoid CLI output-dir compatibility differences.
    run_cmd(zepp_cmd + ["jira", "attachment", "download", issue_key], cwd=str(download_dir))


def download_feedback_attachments(zepp_cmd, issue_key, download_dir):
    download_dir.mkdir(parents=True, exist_ok=True)
    run_cmd(zepp_cmd + ["feedback", "download", issue_key, "-o", str(download_dir)])


def upload_feedback_files_if_needed(zepp_cmd, issue_key, jira_has_zip, download_dir):
    if jira_has_zip:
        return []
    zip_files = sorted([p for p in download_dir.rglob("*.zip") if p.is_file()])
    jpg_files = sorted([p for p in download_dir.rglob("*.jpg") if p.is_file()])
    files = zip_files + jpg_files
    if not files:
        return []
    run_cmd(zepp_cmd + ["jira", "attachment", "add", issue_key] + [str(p) for p in files])
    return files


def recursive_extract_zip(zip_path, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    with ZipFile(zip_path, "r") as zf:
        zf.extractall(out_dir)


def extract_all_nested_zips(root_dir):
    visited = set()
    changed = True
    while changed:
        changed = False
        for p in list(root_dir.rglob("*.zip")):
            rp = p.resolve()
            if rp in visited:
                continue
            try:
                if is_zipfile(p):
                    target = p.with_suffix("")
                    target.mkdir(parents=True, exist_ok=True)
                    recursive_extract_zip(p, target)
                    visited.add(rp)
                    changed = True
            except Exception:
                continue


def find_log_files(root_dir):
    exts = {".txt", ".log"}
    return sorted([p for p in root_dir.rglob("*") if p.is_file() and p.suffix.lower() in exts])


def find_fit_files(root_dir):
    return sorted([p for p in root_dir.rglob("*") if p.is_file() and p.suffix.lower() == ".fit"])


def analyze_log_fit_sources(source_script, log_files, fit_files, analysis_dir):
    node = shutil.which("node")
    if not node:
        raise RuntimeError("分析 FIT/log 融合需要 Node.js（页面与命令行共用同一解析器）")
    args = [node, str(source_script), str(analysis_dir)]
    args.extend(f"log:{p.resolve()}" for p in log_files)
    args.extend(f"fit:{p.resolve()}" for p in fit_files)
    parsed = json.loads(run_cmd(args).stdout)
    results = []
    for sample in parsed.get("results", []):
        user_info = sample.get("userInfo")
        if isinstance(user_info, str):
            try:
                user_info = [float(value) for value in user_info.split(",")]
            except ValueError:
                user_info = None
        results.append({
            "index": sample.get("index"),
            "csv": sample.get("csv"),
            "rows": sample.get("rowCount"),
            "start_ts": sample.get("startTimestamp"),
            "userInfo": user_info,
            "userInfoSource": sample.get("userInfoSource"),
            "alg_gm": sample.get("alg"),
            "filename": sample.get("filename"),
            "sources": sample.get("sources", []),
            "matchRate": sample.get("matchRate"),
            "fitOnlyCount": sample.get("fitOnlyCount"),
            "logOnlyCount": sample.get("logOnlyCount"),
            "statusFilteredCount": sample.get("statusFilteredCount"),
        })
    return {"results": results}


def run_parser(script_path, log_path, out_dir):
    cmd = [sys.executable, str(script_path), str(log_path), str(out_dir), "--json"]
    p = run_cmd(cmd)
    return json.loads(p.stdout)


def has_gomore_markers(log_path, max_bytes=2 * 1024 * 1024):
    markers = (b"_processGomoreAlgo", b"SPRT_GMS rh", b"ALG_GM")
    try:
        with open(log_path, "rb") as f:
            data = f.read(max_bytes)
        return any(m in data for m in markers)
    except Exception:
        return False


def sort_log_candidates(log_files):
    # Priority:
    # 1) files that contain GoMore markers
    # 2) larger files first (often full device logs)
    scored = []
    for p in log_files:
        try:
            size = p.stat().st_size
        except Exception:
            size = 0
        marker = 1 if has_gomore_markers(p) else 0
        scored.append((marker, size, p))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [x[2] for x in scored]


def try_parse_with_candidates(parser_script, log_files, analysis_dir):
    last_err = None
    for log_file in sort_log_candidates(log_files):
        try:
            parsed = run_parser(parser_script, log_file, analysis_dir)
            return log_file, parsed
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    raise RuntimeError("No *.txt/*.log candidates to parse")


def format_lt_speed(v):
    if v is None:
        return "-"
    try:
        return f"{float(v):.6f}"
    except Exception:
        return str(v)


def resolve_model_signature(model_name="", reasoning_effort=""):
    cfg_model = ""
    cfg_effort = ""
    cfg = Path.home() / ".codex" / "config.toml"
    if cfg.exists():
        try:
            text = cfg.read_text(encoding="utf-8", errors="replace")
            m = re.search(r'^\s*model\s*=\s*"([^"]+)"', text, re.M)
            if m:
                cfg_model = m.group(1).strip()
            m = re.search(r'^\s*model_reasoning_effort\s*=\s*"([^"]+)"', text, re.M)
            if m:
                cfg_effort = m.group(1).strip()
        except Exception:
            pass

    model = (
        model_name
        or os.getenv("CODEX_MODEL")
        or os.getenv("OPENAI_MODEL")
        or os.getenv("MODEL")
        or cfg_model
    )
    effort = (
        reasoning_effort
        or os.getenv("CODEX_REASONING_EFFORT")
        or os.getenv("REASONING_EFFORT")
        or cfg_effort
    )
    model = (model or "").strip()
    effort = (effort or "").strip()
    if model and effort:
        return f"（by {model} | reasoning={effort}）"
    if model:
        return f"（by {model}）"
    if effort:
        return f"（reasoning={effort}）"
    return ""


def build_comment(issue_meta, source_txt, parse_json, signature, root_work_dir=None):
    results = parse_json.get("results", [])
    lines = ["【乳酸阈值分析】"]

    if not results:
        lines.append("未解析到可导出的运动数据。")
        return "\n".join(lines)

    file_metadata = extract_lthr_log_metadata(source_txt)
    for i, r in enumerate(results, 1):
        metadata = extract_lthr_log_metadata(source_txt, r)
        metadata["sdk_version"] = metadata.get("sdk_version") or file_metadata.get("sdk_version")
        activity_time = format_activity_time(r)
        lines.append("")
        lines.append(f"运动{i}（{activity_time}）")
        user_info_source = r.get("userInfoSource")
        if user_info_source:
            lines.append(f"用户信息来源：{user_info_source}")
        elif r.get("userInfoFound") is False:
            lines.append("用户信息来源：回退值，具体来源未记录")

        if not has_any_user_info(r.get("userInfo")):
            lines.append("未检索到用户信息数据，无法分析")
            continue

        lines.append(f"用户信息：{format_user_info(r.get('userInfo'))}")
        csv_name = Path(r.get("csv", "")).name
        if csv_name:
            lines.append(f"CSV：{csv_name}")

        alg_raw = r.get("alg_gm")
        if isinstance(alg_raw, list):
            alg = alg_raw[0] if alg_raw else {}
        elif isinstance(alg_raw, dict):
            alg = alg_raw
        else:
            alg = {}
        lt_speed_value = alg.get("ltSpeed")
        lh_value = alg.get("lh")
        mh_value = alg.get("mh")
        lt_speed = float(lt_speed_value) if lt_speed_value is not None else -1
        lh = lh_value if lh_value is not None and float(lh_value) > 0 else "-"
        mh = mh_value if mh_value is not None and float(mh_value) > 0 else "-"
        guide_result = alg.get("guideResult") if alg.get("guideResult") is not None else "-"
        sdk_version = metadata.get("sdk_version") or "-"
        lines.append(f"GoMore 出值（SDK 版本：{sdk_version}）")
        if lt_speed > 0 or lh != "-" or mh != "-":
            pace = alg.get("pace") or format_speed_as_pace(lt_speed)
            lines.append(f"LT HR：{lh} bpm｜LT PACE：{pace if lt_speed > 0 else '-'}｜MAX HR：{mh} bpm｜guideResult：{guide_result}")
        else:
            lines.append(f"结果：未出值｜guideResult：{guide_result}")

        history_hr = metadata.get("start_fit_lt_hr")
        history_pace = metadata.get("start_fit_lt_pace")
        if history_hr is not None or history_pace is not None:
            history_pace_text = format_speed_as_pace(history_pace) if history_pace is not None else "-"
            lines.append(f"历史乳酸阈：LT HR {history_hr if history_hr is not None else '-'} bpm｜LT PACE {history_pace_text}")

        evidence = []
        for label, key in (("有效点数", "value_count"), ("输出 Vo2Max", "output_vo2max"), ("最终 Vo2Max", "final_vo2max")):
            value = metadata.get(key)
            if value is not None:
                evidence.append(f"{label}：{format_number(value)}")
        if evidence:
            lines.append("输出依据：" + "｜".join(evidence))

        estimate_lines = build_compact_estimate(r, metadata)
        lines.extend(estimate_lines)

        # Retain only actionable abnormal findings; omit the old verbose breakdown.
        if root_work_dir:
            anomalies = build_anomaly_analysis([r], root_work_dir)
            if anomalies:
                lines.extend(anomalies)

    return "\n".join(lines)


def format_number(value):
    try:
        number = float(value)
        return str(int(number)) if number.is_integer() else f"{number:g}"
    except (TypeError, ValueError):
        return str(value)


def format_activity_time(result):
    timestamp = result.get("start_ts")
    if timestamp is None:
        return "时间未知"
    try:
        timezone_minutes = 0
        csv_path = result.get("csv")
        if csv_path:
            with open(csv_path, "r", encoding="utf-8", newline="") as csv_file:
                first_row = next(csv.DictReader(csv_file), None)
            if first_row and first_row.get("timeZoneOffset"):
                timezone_minutes = int(float(first_row["timeZoneOffset"]))
        local_time = datetime.fromtimestamp(float(timestamp) + timezone_minutes * 60, timezone.utc)
        return local_time.strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, TypeError, ValueError, OverflowError):
        return "时间未知"


def has_any_user_info(user_info):
    if not isinstance(user_info, list) or len(user_info) < 6:
        return False
    try:
        return any(float(user_info[index]) > 0 for index in (0, 2, 3, 5))
    except (TypeError, ValueError):
        return False


def format_user_info(user_info):
    values = user_info if isinstance(user_info, list) else []
    fields = []
    if len(values) > 0 and values[0] > 0:
        fields.append(f"年龄 {format_number(values[0])}")
    if len(values) > 1 and values[1] in (0, 1):
        fields.append(f"性别 {'女' if values[1] == 0 else '男'}")
    if len(values) > 2 and values[2] > 0:
        fields.append(f"身高 {format_number(values[2])} cm")
    if len(values) > 3 and values[3] > 0:
        fields.append(f"体重 {format_number(values[3])} kg")
    if len(values) > 5 and values[5] > 0:
        fields.append(f"静息心率 {format_number(values[5])} bpm")
    return "｜".join(fields) if fields else "信息不完整"


def format_speed_as_pace(speed):
    try:
        seconds = round(3600 / float(speed))
        return f"{seconds // 60}'{seconds % 60:02d}\"/km"
    except (TypeError, ValueError, ZeroDivisionError):
        return "-"


def build_compact_estimate(result, metadata):
    user_info = result.get("userInfo") or []
    try:
        age = float(user_info[0])
        rest_hr = float(user_info[5])
    except (IndexError, TypeError, ValueError):
        return ["分析估值：无法判断出值条件（用户信息不完整）"]

    max_hr, _ = resolve_gate_max_hr(metadata.get("max_hr_values", []), age)
    hr_line = (max_hr - rest_hr) * 0.7 + rest_hr
    stats = compute_gate_stats(result.get("csv"), hr_line)
    if stats is None:
        return [f"分析估值：UseMax {max_hr:.1f} bpm｜Hr Line {hr_line:.1f} bpm｜估值点数无法计算"]

    points = stats["both_pass"]
    status = "符合出值条件" if points > 720 else "不符合出值条件"
    lines = [f"分析估值：{status}｜UseMax {max_hr:.1f} bpm｜Hr Line {hr_line:.1f} bpm｜估值点数 {points}"]
    if points <= 720:
        reasons = []
        if stats["hr_pass"] <= 720:
            reasons.append(f"HR 达标点数 {stats['hr_pass']}（需超过 720）")
        if stats["speed_pass"] <= 720:
            reasons.append(f"速度达标点数 {stats['speed_pass']}（需超过 720）")
        if reasons:
            lines.append("不符合原因：" + "；".join(reasons))
    if metadata.get("value_count") is None:
        lines.append("SDK 实际有效点数：无法确认")
    return lines


def extract_lthr_log_metadata(log_path, result=None):
    """Extract gate-related fields with the log's output semantics."""
    try:
        full_text = log_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    text = full_text
    if result and result.get("start_pos") is not None and result.get("end_pos") is not None:
        try:
            start_pos = int(result["start_pos"])
            end_pos = int(result["end_pos"])
            previous_disables = list(re.finditer(r"_disableGomoreAlgo|disable.*Gomore", full_text[:start_pos], re.IGNORECASE))
            metadata_start = previous_disables[-1].start() if previous_disables else 0
            text = full_text[metadata_start:end_pos]
        except (TypeError, ValueError):
            text = full_text

    def numbers(value):
        return [float(x) for x in re.findall(r"-?(?:\d+\.\d+|\d+)", value)]

    max_hr_values = []
    value_count = None
    output_vo2max = None
    workout_score = None
    fit_app_score = None
    vo2max_history = []
    sdk_version = None
    start_fit_lt_pace = None
    start_fit_lt_hr = None
    lt_pace = []
    lt_hr = []
    process_status = []
    for line in text.splitlines():
        lower = line.lower()
        version_match = re.search(r"ALG_GM\s+SDK Version\s*:\s*([^\r\n]+)", line, re.IGNORECASE)
        if version_match:
            sdk_version = version_match.group(1).strip()
        start_fit_match = re.search(r"\[GoMoRe\]\[startFit\]\[ltPace\]\s*=\s*(-?(?:\d+\.\d+|\d+)).*?\[ltHr\]\s*=\s*(-?(?:\d+\.\d+|\d+))", line, re.IGNORECASE)
        if start_fit_match:
            start_fit_lt_pace = float(start_fit_match.group(1))
            start_fit_lt_hr = float(start_fit_match.group(2))
        vo2_history_match = re.search(r"\[GoMoRe\]\[HisPd\]\[vo2max\]\s*=\s*([^\r\n]+)", line, re.IGNORECASE)
        if vo2_history_match:
            vo2max_history.extend(numbers(vo2_history_match.group(1)))
        # EHisPd[maxHr] contains the recent history, with the newest value
        # on the right. Keep the sequence so build_gate_analysis can apply
        # the rule for the current run: one record means the SDK used the
        # default maxHR; two or more records mean the previous (rightmost - 1)
        # record was used. Do not use SPT_S_SY/UI maxhr fields here.
        if re.search(r"ehispd.*maxhr", lower):
            max_hr_values = numbers(line.split("=", 1)[-1])
        if "valuecount" in lower:
            values = numbers(line.split("=", 1)[-1])
            if values:
                value_count = values[0]
            if len(values) > 1:
                output_vo2max = values[1]
            if len(values) > 1:
                workout_score = values[1]
        if re.search(r"\[gomore\]\[fitapp\]=", lower):
            values = numbers(line.split("=", 1)[-1])
            if values:
                # The fitApp line after valueCount carries the aggregate
                # score; use its final numeric field.
                fit_app_score = values[-1]
        # Only read the bracketed GoMore result fields. In particular,
        # SPT_S_SY _queryLtHr 170 is a UI query value, not the final GoMore
        # ltHr output and must not be reported as the log-end result.
        pace_match = re.search(r"\[ltpace\]\s*=\s*(-?(?:\d+\.\d+|\d+))", lower)
        if pace_match:
            lt_pace.append(float(pace_match.group(1)))
        hr_match = re.search(r"\[lthr\]\s*=\s*(-?(?:\d+\.\d+|\d+))", lower)
        if hr_match:
            lt_hr.append(float(hr_match.group(1)))
        if "process completed" in lower:
            process_status.append(line.strip())

    return {
        "max_hr_values": max_hr_values,
        "value_count": value_count,
        "output_vo2max": output_vo2max,
        "workout_score": workout_score,
        "fit_app_score": fit_app_score,
        "final_vo2max": fit_app_score,
        "vo2max_history": vo2max_history,
        "sdk_version": sdk_version,
        "start_fit_lt_pace": start_fit_lt_pace,
        "start_fit_lt_hr": start_fit_lt_hr,
        "lt_pace": lt_pace[-1] if lt_pace else None,
        "lt_hr": lt_hr[-1] if lt_hr else None,
        "process_status": process_status[-1] if process_status else None,
    }


def compute_gate_stats(csv_path, hr_gate):
    """Count raw points using the km/h SPEED written by gomore_log_to_csv.py."""
    stats = {"hr_valid": 0, "hr_pass": 0, "speed_valid": 0, "speed_pass": 0, "both_pass": 0}
    try:
        with open(csv_path, "r", encoding="utf-8", errors="replace", newline="") as f:
            for row in csv.DictReader(f):
                try:
                    hr = float(row.get("HR", "-1"))
                except (TypeError, ValueError):
                    hr = -1
                try:
                    speed_kmh = float(row.get("SPEED", "-1"))
                except (TypeError, ValueError):
                    speed_kmh = -1
                hr_ok = hr > 0
                speed_ok = speed_kmh >= 0
                hr_pass = hr_ok and hr > hr_gate
                speed_pass = speed_ok and speed_kmh > 6
                stats["hr_valid"] += int(hr_ok)
                stats["hr_pass"] += int(hr_pass)
                stats["speed_valid"] += int(speed_ok)
                stats["speed_pass"] += int(speed_pass)
                stats["both_pass"] += int(hr_pass and speed_pass)
    except Exception:
        return None
    return stats


def resolve_gate_max_hr(max_hr_values, age):
    """Resolve the maxHR used by the current GoMore run.

    EHisPd[maxHr] stores recent values from left to right, with the newest
    value on the right. Only -1 is invalid. A single valid stored value is the
    initial/default history, so the current run uses the SDK default 220-age.
    With two or more valid values, the value immediately before the newest one
    is the current run's maxHR.
    """
    values = []
    for value in max_hr_values or []:
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if value != -1:
            values.append(value)
    default_max_hr = 220 - float(age)
    if len(values) >= 2:
        return values[-2], "EHisPd[maxHr]倒数第二个值（本次运动使用）"
    if len(values) == 1:
        return default_max_hr, "默认公式 220-age（EHisPd[maxHr]仅有一个记录）"
    return default_max_hr, "默认公式 220-age（无有效历史记录）"


def build_gate_analysis(source_txt, results):
    """Build a source-based LTHR eligibility analysis for the Jira comment."""
    if not results:
        return []
    metadata = extract_lthr_log_metadata(source_txt)
    user_info = results[0].get("userInfo")
    if not isinstance(user_info, list) or len(user_info) < 6:
        return ["  * 出值条件分析: userInfo 不完整，无法计算 70%HRR 门槛"]
    try:
        age = float(user_info[0])
        rest_hr = float(user_info[5])
    except (TypeError, ValueError):
        return ["  * 出值条件分析: age/hrRest 无法解析，无法计算 70%HRR 门槛"]

    max_hr_values = [v for v in metadata.get("max_hr_values", []) if v != -1]
    max_hr, max_hr_source = resolve_gate_max_hr(max_hr_values, age)
    hr_gate = (max_hr - rest_hr) * 0.7 + rest_hr
    parts = [
        "判定口径=以下 HR/SPEED/时间统计为 SDK 外部辅助判定，仅供参考",
        f"userInfo(age={age:g}, restHR={rest_hr:g})",
        f"MaxHR={max_hr:.1f}（{max_hr_source}）",
        f"门槛=HR>{hr_gate:.1f}bpm 且 SPEED>6kph，时间>720s",
    ]
    if max_hr_values:
        history_text = ", ".join(f"{value:g}" for value in max_hr_values)
        parts.insert(1, f"EHisPd[maxHr]历史=[{history_text}]（最新在右）")
    if metadata.get("value_count") is not None:
        score = metadata.get("workout_score")
        score_text = f", 本次评分={score:g}" if score is not None else ""
        parts.append(
            f"SDK日志实际有效点数(valueCount第一个值)={metadata['value_count']:g}{score_text}"
        )
    else:
        parts.append("SDK日志实际有效点数=valueCount未解析到，不能用外部统计替代")
    if metadata.get("fit_app_score") is not None:
        parts.append(f"GoMoRe fitApp综合历史评分={metadata['fit_app_score']:g}")
    if metadata.get("lt_pace") is not None or metadata.get("lt_hr") is not None:
        parts.append(f"日志结束字段 ltPace={metadata.get('lt_pace', '-')}, ltHr={metadata.get('lt_hr', '-')}")
    if metadata.get("process_status"):
        parts.append(f"处理状态={metadata['process_status']}")

    for i, result in enumerate(results, 1):
        stats = compute_gate_stats(result.get("csv"), hr_gate)
        if stats is None:
            continue
        status = "达到 720 秒条件" if stats["both_pass"] > 720 else "未达到 720 秒条件"
        parts.append(
            f"Result #{i} HR有效/达标={stats['hr_valid']}/{stats['hr_pass']}, "
            f"速度有效/达标={stats['speed_valid']}/{stats['speed_pass']}, "
            f"外部辅助同时达标={stats['both_pass']}（参考有效点数），辅助判定={status}"
        )
    parts.append(
        "结论边界=外部辅助同时达标点数不等于 SDK 最终有效点数；实际有效点数以 SDK 日志 valueCount 为准，"
        "且满足基础门槛不等于一定出值，仍需结合历史状态和最终输出"
    )
    return ["* 出值条件分析: " + "；".join(parts)]


def upload_csvs(zepp_cmd, issue_key, csv_paths):
    if not csv_paths:
        return
    run_cmd(zepp_cmd + ["jira", "attachment", "add", issue_key] + [str(p) for p in csv_paths])


def generate_plots(plot_script, csv_paths):
    """Generate one HR/SPEED SVG per CSV without failing the CSV workflow."""
    plots = []
    for csv_path in csv_paths:
        out_path = csv_path.with_name(f"{csv_path.stem}_hr_speed.svg")
        result = run_cmd(
            [sys.executable, str(plot_script), str(csv_path), "-o", str(out_path)],
            check=False,
        )
        if result.returncode == 0 and out_path.is_file():
            plots.append(out_path)
        else:
            print(f"[PLOT WARN] Failed to plot {csv_path.name}: {result.stderr.strip()}", flush=True)
    return plots


def add_comment(zepp_cmd, issue_key, comment):
    run_cmd(zepp_cmd + ["jira", "comment", "add", issue_key, comment])


def compute_csv_stats(csv_path):
    """Read a sample CSV and compute maxHR and avgSpeed.
    
    Returns (max_hr, avg_speed) or (None, None) on failure.
    The CSV has header: TIMESTAMP,...,HR,SPEED,...
    HR uses 255 as an invalid sentinel in some logs, so only in-workout
    values within a normal physiological range are counted.
    """
    try:
        import csv as csv_mod
        hr_vals = []
        speed_vals = []
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv_mod.DictReader(f)
            for row in reader:
                hr_str = row.get("HR", "").strip()
                sp_str = row.get("SPEED", "").strip()
                if hr_str and hr_str != "-1":
                    try:
                        hr_val = float(hr_str)
                        if 30 <= hr_val <= 250:
                            hr_vals.append(hr_val)
                    except ValueError:
                        pass
                if sp_str and sp_str != "-1.0" and sp_str != "-1":
                    try:
                        speed_vals.append(float(sp_str))
                    except ValueError:
                        pass
        max_hr = max(hr_vals) if hr_vals else None
        avg_speed = sum(speed_vals) / len(speed_vals) if speed_vals else None
        return max_hr, avg_speed
    except Exception:
        return None, None


def build_anomaly_analysis(results, root_work_dir):
    """Analyze each result's CSV for anomaly patterns and return analysis lines.
    
    Threshold checks:
      - lthr < maxHR * 0.9   => lactate threshold too LOW relative to max HR
      - lthr >= maxHR         => lactate threshold too HIGH (>= max HR)
      - mh < maxHR            => aerobic threshold upper bound (mh) below max HR
      - ltSpeed < avgSpeed_kmh => lactate threshold speed slower than average speed
    
    Returns list of markdown lines (may be empty if no anomalies).
    """
    lines = []
    for i, r in enumerate(results, 1):
        csv_path = r.get("csv")
        if not csv_path or not os.path.isfile(csv_path):
            continue
        max_hr, avg_speed = compute_csv_stats(csv_path)
        if max_hr is None:
            continue

        alg_raw = r.get("alg_gm")
        if isinstance(alg_raw, list):
            alg = alg_raw[0] if alg_raw else {}
        elif isinstance(alg_raw, dict):
            alg = alg_raw
        else:
            alg = {}

        lt_speed = alg.get("ltSpeed")
        lh = alg.get("lh")
        mh = alg.get("mh")

        findings = []
        # 1. lthr vs maxHR
        if lt_speed is not None and float(lt_speed) > 0 and max_hr is not None:
            threshold_hr = lh if lh is not None else mh
            if threshold_hr is not None and float(threshold_hr) > 0:
                threshold_val = float(threshold_hr)
                if threshold_val < max_hr * 0.9:
                    findings.append(
                        f"乳酸阈心率({threshold_val:.0f}) < 最大心率*0.9({max_hr*0.9:.0f})，"
                        f"阈心率偏低"
                    )
                if threshold_val >= max_hr:
                    findings.append(
                        f"乳酸阈心率({threshold_val:.0f}) >= 最大心率({max_hr:.0f})，"
                        f"阈心率异常偏高"
                    )
        # 2. mh < maxHR
        if mh is not None and float(mh) > 0 and max_hr is not None:
            mh_val = float(mh)
            if mh_val < max_hr:
                findings.append(
                    f"有氧阈上限(mh={mh_val:.0f}) < 最大心率({max_hr:.0f})，"
                    f"有氧上限未覆盖到最大心率"
                )
        # 3. ltSpeed < avgSpeed
        if lt_speed is not None and float(lt_speed) > 0 and avg_speed is not None:
            lt_val = float(lt_speed)
            avg_speed_kmh = avg_speed
            if lt_val < avg_speed_kmh:
                findings.append(
                    f"乳酸阈速度({lt_val:.2f}km/h) < 平均速度({avg_speed_kmh:.2f}km/h)，"
                    f"阈配速慢于平均速度"
                )

        if findings:
            csv_name = Path(csv_path).name
            stats_text = f"maxHR={max_hr:.0f}, avgSpeed={avg_speed:.2f}km/h" if avg_speed is not None else f"maxHR={max_hr:.0f}"
            lines.append(f"Result #{i} ({csv_name}): {stats_text}，" + "；".join(f"⚠️ {f_text}" for f_text in findings))

    return lines


def collect_sample_to_lark_drive(csv_paths, issue_key, issue_title=""):
    """Upload sample CSVs to Feishu Drive path: GomoreSDK_Report/data_sample/JIRA_CASE/.
    
    Uses lark-cli to search/create folders and upload files.
    Returns list of uploaded file names (may be empty on failure).
    """
    if not csv_paths:
        return []
    try:
        # 1. Find or create folder hierarchy
        def _run_lark(cmd_list):
            p = subprocess.run(cmd_list, text=True, capture_output=True)
            if p.returncode != 0:
                stderr = p.stderr.strip()
                if stderr:
                    print(f"[LARK WARN] {' '.join(cmd_list[:4])}... -> {stderr}", flush=True)
                return None
            return json.loads(p.stdout) if p.stdout.strip() else {}

        def _find_or_create_folder(parent_token, name):
            # Search for existing folder
            if parent_token:
                search_cmd = [
                    "lark-cli", "drive", "+search",
                    "--query", name,
                    "--doc-types", "folder",
                ]
                if parent_token != "root":
                    search_cmd.extend(["--folder-tokens", parent_token])
            else:
                search_cmd = [
                    "lark-cli", "drive", "+search",
                    "--query", name,
                    "--doc-types", "folder",
                    "--mine",
                ]
            result = _run_lark(search_cmd)
            if result and isinstance(result, dict):
                items = result.get("items", [])
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict) and item.get("title") == name:
                            return item.get("token") or item.get("folder_token")

            # Create folder if not found
            create_cmd = ["lark-cli", "drive", "+create-folder", "--name", name]
            if parent_token and parent_token != "root":
                create_cmd.extend(["--folder-token", parent_token])
            result = _run_lark(create_cmd)
            if result and isinstance(result, dict):
                return result.get("token") or result.get("folder_token")
            return None

        # Build path: GomoreSDK_Report / data_sample / JIRA_CASE
        root_token = _find_or_create_folder(None, "GomoreSDK_Report")
        if not root_token:
            print("[COLLECT] Cannot find/create GomoreSDK_Report folder, skip", flush=True)
            return []

        data_sample_token = _find_or_create_folder(root_token, "data_sample")
        if not data_sample_token:
            print("[COLLECT] Cannot find/create data_sample folder, skip", flush=True)
            return []

        jira_case_token = _find_or_create_folder(data_sample_token, "JIRA_CASE")
        if not jira_case_token:
            print("[COLLECT] Cannot find/create JIRA_CASE folder, skip", flush=True)
            return []

        # 2. Upload each CSV
        uploaded = []
        for csv_path in csv_paths:
            csv_p = Path(csv_path)
            if not csv_p.is_file():
                continue
            # Name file as: {issue_key}_{original_name}
            remote_name = f"{issue_key}_{csv_p.name}"
            upload_cmd = [
                "lark-cli", "drive", "+upload",
                "--file", str(csv_p.resolve()),
                "--name", remote_name,
                "--folder-token", jira_case_token,
            ]
            result = _run_lark(upload_cmd)
            if result is not None:
                uploaded.append(remote_name)
                print(f"[COLLECT] Uploaded {remote_name} to GomoreSDK_Report/data_sample/JIRA_CASE/", flush=True)
            else:
                print(f"[COLLECT WARN] Failed to upload {remote_name}", flush=True)

        return uploaded
    except Exception as e:
        print(f"[COLLECT ERROR] {e}", flush=True)
        return []


def resolve_log_files(zepp_cmd, issue_key, base_dir, explicit_log=""):
    download_dir = base_dir / "download"
    extract_dir = base_dir / "extract"
    for d in [download_dir, extract_dir]:
        d.mkdir(parents=True, exist_ok=True)

    if explicit_log:
        p = Path(explicit_log)
        if not p.exists():
            raise RuntimeError(f"log file not found: {p}")
        return [p]

    def _collect_logs_from_download():
        for f in download_dir.rglob("*"):
            if f.is_file() and f.suffix.lower() == ".zip" and is_zipfile(f):
                target = extract_dir / f.stem
                target.mkdir(parents=True, exist_ok=True)
                recursive_extract_zip(f, target)

        for f in download_dir.rglob("*"):
            if f.is_file() and f.suffix.lower() != ".zip":
                rel = f.relative_to(download_dir)
                dest = extract_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)

        extract_all_nested_zips(extract_dir)
        return find_log_files(extract_dir)

    attachment_names = list_jira_attachments(zepp_cmd, issue_key)
    has_plain_log_in_jira = any(name.lower().endswith((".txt", ".log")) for name in attachment_names)
    has_fit_in_jira = any(name.lower().endswith(".fit") for name in attachment_names)
    has_zip_in_jira = any(name.lower().endswith(".zip") for name in attachment_names)

    # Priority 1: Jira plain text logs (*.txt / *.log)
    if has_plain_log_in_jira or has_fit_in_jira:
        download_jira_attachments(zepp_cmd, issue_key, download_dir)
        return _collect_logs_from_download(), "jira_log"

    # Priority 2: Jira *.zip
    if has_zip_in_jira:
        download_jira_attachments(zepp_cmd, issue_key, download_dir)
        return _collect_logs_from_download(), "jira_zip"

    # Priority 3: feedback fallback
    download_feedback_attachments(zepp_cmd, issue_key, download_dir)
    upload_feedback_files_if_needed(
        zepp_cmd=zepp_cmd,
        issue_key=issue_key,
        jira_has_zip=has_zip_in_jira,
        download_dir=download_dir,
    )
    return _collect_logs_from_download(), "feedback"


def analyze_one_issue(
    zepp_cmd,
    issue_key,
    root_work_dir,
    parser_script,
    plot_script=None,
    explicit_log="",
    post_comment=True,
    signature="",
    collect_to_lark=False,
):
    issue_meta = get_issue_meta(zepp_cmd, issue_key)
    base_dir = root_work_dir / issue_key
    analysis_dir = base_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    if explicit_log:
        log_files = [Path(explicit_log)]
        source_channel = "explicit_log"
    else:
        log_files, source_channel = resolve_log_files(zepp_cmd, issue_key, base_dir, explicit_log=explicit_log)

    source_root = Path(explicit_log).parent if explicit_log else base_dir / "extract"
    fit_files = find_fit_files(source_root) if source_root.exists() else []

    if not log_files and not fit_files:
        # If Jira path yielded no *.txt/*.log, fallback once via feedback.
        if source_channel in ("jira_log", "jira_zip"):
            download_dir = base_dir / "download_fb"
            extract_dir = base_dir / "extract_fb"
            download_feedback_attachments(zepp_cmd, issue_key, download_dir)
            upload_feedback_files_if_needed(
                zepp_cmd=zepp_cmd,
                issue_key=issue_key,
                jira_has_zip=(source_channel == "jira_zip"),
                download_dir=download_dir,
            )
            for f in download_dir.rglob("*"):
                if f.is_file() and f.suffix.lower() == ".zip" and is_zipfile(f):
                    target = extract_dir / f.stem
                    target.mkdir(parents=True, exist_ok=True)
                    recursive_extract_zip(f, target)
            for f in download_dir.rglob("*"):
                if f.is_file() and f.suffix.lower() != ".zip":
                    rel = f.relative_to(download_dir)
                    dest = extract_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
            extract_all_nested_zips(extract_dir)
            log_files = find_log_files(extract_dir)
            fit_files = find_fit_files(extract_dir)
            source_channel = "feedback"
        if not log_files and not fit_files:
            raise RuntimeError(f"{issue_key}: No *.txt/*.log or *.fit found after download/extract")

    if log_files:
        source_txt, parse_json = try_parse_with_candidates(parser_script, log_files, analysis_dir)
    else:
        source_txt, parse_json = Path(""), {"results": []}

    if fit_files:
        source_script = Path(__file__).with_name("analyze_lthr_sources.js")
        parse_json = analyze_log_fit_sources(source_script, log_files, fit_files, analysis_dir)
        if log_files:
            source_txt = log_files[0]
    results = parse_json.get("results", [])
    csv_paths = [Path(r.get("csv")) for r in results if r.get("csv")]

    # If Jira path has no parse results, fallback to feedback and retry once.
    if (not results) and not fit_files and source_channel in ("jira_log", "jira_zip") and not explicit_log:
        download_dir = base_dir / "download_fb_retry"
        extract_dir = base_dir / "extract_fb_retry"
        download_feedback_attachments(zepp_cmd, issue_key, download_dir)
        upload_feedback_files_if_needed(
            zepp_cmd=zepp_cmd,
            issue_key=issue_key,
            jira_has_zip=(source_channel == "jira_zip"),
            download_dir=download_dir,
        )
        for f in download_dir.rglob("*"):
            if f.is_file() and f.suffix.lower() == ".zip" and is_zipfile(f):
                target = extract_dir / f.stem
                target.mkdir(parents=True, exist_ok=True)
                recursive_extract_zip(f, target)
        for f in download_dir.rglob("*"):
            if f.is_file() and f.suffix.lower() != ".zip":
                rel = f.relative_to(download_dir)
                dest = extract_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
        extract_all_nested_zips(extract_dir)
        fb_log_files = find_log_files(extract_dir)
        fb_fit_files = find_fit_files(extract_dir)
        if fb_log_files:
            source_txt, parse_json = try_parse_with_candidates(parser_script, fb_log_files, analysis_dir)
            results = parse_json.get("results", [])
            csv_paths = [Path(r.get("csv")) for r in results if r.get("csv")]
            source_channel = "feedback"
        elif fb_fit_files:
            source_txt = Path("")
            parse_json = analyze_log_fit_sources(Path(__file__).with_name("analyze_lthr_sources.js"), [], fb_fit_files, analysis_dir)
            results = parse_json.get("results", [])
            csv_paths = [Path(r.get("csv")) for r in results if r.get("csv")]
            source_channel = "feedback"

    plot_paths = generate_plots(plot_script, csv_paths) if plot_script else []
    upload_csvs(zepp_cmd, issue_key, csv_paths + plot_paths)

    # Build comment with anomaly analysis (Feature 1)
    comment = build_comment(issue_meta, source_txt, parse_json, signature=signature, root_work_dir=root_work_dir)
    if post_comment:
        add_comment(zepp_cmd, issue_key, comment)

    # Collect sample to Lark Drive if requested (Feature 2)
    lark_collected = []
    if collect_to_lark and csv_paths:
        print(f"[COLLECT] Uploading sample CSVs to Feishu Drive GomoreSDK_Report/data_sample/JIRA_CASE/ ...", flush=True)
        lark_collected = collect_sample_to_lark_drive(
            [str(p) for p in csv_paths],
            issue_key,
            issue_title=issue_meta.get("title", ""),
        )
        if lark_collected:
            print(f"[COLLECT] Uploaded {len(lark_collected)} file(s) to Lark Drive", flush=True)
        else:
            print(f"[COLLECT] No files uploaded to Lark Drive", flush=True)

    return {
        "issue": issue_meta,
        "source_channel": source_channel,
        "source_txt": str(source_txt),
        "csv_count": len(csv_paths),
        "csv_paths": [str(p) for p in csv_paths],
        "plot_paths": [str(p) for p in plot_paths],
        "result_count": len(results),
        "comment": comment,
        "lark_collected": lark_collected,
    }


def write_batch_report(summaries, report_path):
    lines = ["# LTHR Batch Analysis Report", ""]
    lines.append(f"Generated At: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}")
    lines.append(f"Issue Count: {len(summaries)}")
    lines.append("")

    for idx, s in enumerate(summaries, 1):
        issue = s["issue"]
        issue_key = issue.get("key", "")
        issue_link = issue.get("link", "")
        title = issue.get("title", "")
        status = issue.get("status", "")
        lines.append(f"## {idx}. {issue_key}")
        if issue_link:
            lines.append(f"- Link: {issue_link}")
        if title:
            lines.append(f"- Title: {title}")
        if status:
            lines.append(f"- Status: {status}")
        lines.append(f"- Source Log: {Path(s['source_txt']).name}")
        lines.append(f"- CSV Uploaded: {s['csv_count']}")
        lines.append(f"- Result Count: {s['result_count']}")
        lines.append("- Jira Comment Summary:")
        for cl in s["comment"].splitlines():
            lines.append(f"  {cl}")
        lines.append("")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


def parse_issue_keys(args):
    keys = list(args.issue_keys)
    if args.issue_file:
        for line in Path(args.issue_file).read_text(encoding="utf-8").splitlines():
            k = line.strip()
            if not k or k.startswith("#"):
                continue
            keys.append(k)
    uniq = []
    seen = set()
    for k in keys:
        if k in seen:
            continue
        seen.add(k)
        uniq.append(k)
    if not uniq:
        raise RuntimeError("No issue keys provided")
    return uniq


def main():
    _safe_reconfigure_stdio()

    parser = argparse.ArgumentParser(description="One-click LTHR log analysis for single or batch Jira issues")
    parser.add_argument("issue_keys", nargs="*", help="Jira issue keys, e.g. ITS-51120 ITS-50805")
    parser.add_argument("--issue-file", default="", help="Text file containing issue keys, one per line")
    parser.add_argument("--work-dir", default="", help="Root work dir (default: <temp>/topic_ota)")
    parser.add_argument("--txt-log", default="", help="Explicit log path (*.txt/*.log, single issue only)")
    parser.add_argument("--model-name", default="", help="Model name for Jira comment signature")
    parser.add_argument("--reasoning-effort", default="", help="Reasoning effort for Jira comment signature")
    parser.add_argument("--no-comment", action="store_true", help="Do not post Jira comments")
    parser.add_argument("--collect-sample", action="store_true", help="Upload sample CSVs to Feishu Drive GomoreSDK_Report/data_sample/JIRA_CASE/")
    parser.add_argument("--plot", dest="plot", action="store_true", help="Generate and upload HR/SPEED SVGs for each CSV (default)")
    parser.add_argument("--no-plot", dest="plot", action="store_false", help="Skip generating and uploading HR/SPEED SVGs")
    parser.set_defaults(plot=True)
    parser.add_argument("--report", default="", help="Batch report output path (.md)")
    args = parser.parse_args()

    issue_keys = parse_issue_keys(args)
    if args.txt_log and len(issue_keys) != 1:
        raise RuntimeError("--txt-log only supports single issue mode")

    script_dir = Path(__file__).resolve().parent
    parser_script = script_dir / "gomore_log_to_csv.py"
    if not parser_script.exists():
        raise RuntimeError(f"Parser script not found: {parser_script}")
    plot_script = script_dir / "plot_gomore_hr_speed_svg.py"
    if args.plot and not plot_script.exists():
        raise RuntimeError(f"Plot script not found: {plot_script}")

    temp_root = Path(tempfile.gettempdir()) / "topic_ota"
    root_work_dir = Path(args.work_dir) if args.work_dir else temp_root
    root_work_dir.mkdir(parents=True, exist_ok=True)

    zepp_cmd = find_zepp_cmd()
    signature = resolve_model_signature(model_name=args.model_name, reasoning_effort=args.reasoning_effort)

    summaries = []
    for key in issue_keys:
        print(f"[RUN] {key}")
        s = analyze_one_issue(
            zepp_cmd=zepp_cmd,
            issue_key=key,
            root_work_dir=root_work_dir,
            parser_script=parser_script,
            plot_script=plot_script if args.plot else None,
            explicit_log=args.txt_log if len(issue_keys) == 1 else "",
            post_comment=(not args.no_comment),
            signature=signature,
            collect_to_lark=args.collect_sample,
        )
        summaries.append(s)
        print(f"[OK] {key} | csv={s['csv_count']} | plot={len(s['plot_paths'])} | source={Path(s['source_txt']).name}")

    if len(issue_keys) > 1:
        if args.report:
            report_path = Path(args.report)
        else:
            ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
            report_path = root_work_dir / f"lthr_batch_report_{ts}.md"
        write_batch_report(summaries, report_path)
        print(f"[REPORT] {report_path}")

    print("Done")


if __name__ == "__main__":
    main()

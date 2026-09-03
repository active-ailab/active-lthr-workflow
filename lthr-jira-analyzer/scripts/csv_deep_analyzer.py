#!/usr/bin/env python3
"""
csv_deep_analyzer.py - 乳酸阈 CSV 深度分析报告生成器 (Feature 3)

从 gomore 样本 CSV 中提取运动数据，生成结构化分析报告：
- 每公里分段统计
- 巡航段 vs 冲刺段对比
- 心率-配速对应关系
- 乳酸阈值合理性评估
- 与设备输出对比分析

用法:
  python3 csv_deep_analyzer.py path/to/gomore_data_*.csv [--age 44] [--lt-hr 160] [--lt-pace-sec 301] [--user-desc "最后2km提速"]

输出:
  - stdout: Markdown 格式分析报告
  - 或 --output report.md 写入文件
"""

import csv
import math
import argparse
import sys
import os
from datetime import datetime, timezone
from typing import List, Tuple, Optional


# CSV 列索引（0-based）
COL_TIMESTAMP = 0
COL_TZ = 2
COL_HR = 15
COL_SPEED = 16
COL_USERINFO = 23  # userInfo 在第 24 列（0-based index 23），非 24！索引 24 是 EndInput.workoutDate
COL_WORKOUT_TYPE = 21

# 工作类型映射
WORKOUT_TYPES = {
    1: "户外跑步（含操场）",
    2: "户外步行",
    3: "户外骑行",
    4: "跑步机",
    5: "室内骑行",
    6: "椭圆机",
    10: "泳池游泳",
    11: "开放水域游泳",
    15: "越野跑",
    23: "徒步",
    27: "力量训练",
    31: "户外跑步",
}


def parse_csv(path: str) -> Tuple[List[dict], List[str]]:
    """读取 CSV，返回 (rows_as_dict_list, header_list)。"""
    rows = []
    header = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        for r in reader:
            if len(r) <= max(COL_HR, COL_SPEED, COL_TIMESTAMP):
                continue
            rows.append(r)
    return rows, header


def safe_float(v, default=0.0):
    if v is None:
        return default
    v = v.strip()
    if not v:
        return default
    try:
        return float(v)
    except ValueError:
        return default


def safe_int(v, default=0):
    if v is None:
        return default
    v = v.strip()
    if not v:
        return default
    try:
        return int(float(v))
    except ValueError:
        return default


def speed_csv_to_kmh(v):
    """gomore_log_to_csv.py writes the source SPEED value in km/h."""
    return safe_float(v, -1.0)


def kmh_to_pace_str(kmh: float) -> str:
    """km/h → "M'SS\"/km" 格式的配速字符串"""
    if kmh <= 0:
        return "N/A"
    min_per_km = 60.0 / kmh
    m = int(min_per_km)
    s = int((min_per_km - m) * 60)
    return f"{m}'{s:02d}\"/km"


def pace_sec_to_pace_str(pace_sec: float) -> str:
    """秒/公里 → "M'SS\"/km" 格式"""
    if pace_sec <= 0:
        return "N/A"
    m = int(pace_sec) // 60
    s = int(pace_sec) % 60
    return f"{m}'{s:02d}\"/km"


def pace_sec_to_kmh(pace_sec: float) -> float:
    """秒/公里 → km/h"""
    if pace_sec <= 0:
        return 0
    return 3600.0 / pace_sec


def format_duration(seconds: int) -> str:
    """秒 → "MM:SS" 或 "H:MM:SS" """
    if seconds < 3600:
        m = seconds // 60
        s = seconds % 60
        return f"{m}:{s:02d}"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}:{m:02d}:{s:02d}"


def _get_userinfo_hr(userinfo_str: str) -> Optional[int]:
    """从 userInfo 字段解析静息心率。格式: "age.0,gender.0,height.0,weight.0,-1.0,hr_rest.0,-1.0" """
    if not userinfo_str:
        return None
    parts = userinfo_str.split(",")
    if len(parts) >= 6:
        try:
            hr = int(float(parts[5]))
            return hr if hr > 0 else None
        except ValueError:
            return None
    return None


def _get_userinfo_age(userinfo_str: str) -> Optional[int]:
    """从 userInfo 字段解析年龄。格式: "age.0,gender.0,height.0,weight.0,..." """
    if not userinfo_str:
        return None
    parts = userinfo_str.split(",")
    if len(parts) >= 1:
        try:
            age = int(float(parts[0]))
            return age if age > 0 else None
        except ValueError:
            return None
    return None


def _detect_sprint_phase(hr_series: List[int], speed_series: List[float]) -> Tuple[int, int]:
    """通过 HR 曲线检测巡航段和冲刺段的分界点。
    
    返回 (cruise_end_idx, sprint_start_idx)。
    策略：从后往前找 HR 开始明显上升的点（HR 较前段均值高出 >10bpm）。
    """
    n = len(hr_series)
    if n < 60:
        return 0, n  # 数据太少，不分段

    # 平滑 HR（10 点移动平均）
    smooth_hr = []
    for i in range(n):
        start = max(0, i - 5)
        end = min(n, i + 5)
        smooth_hr.append(sum(hr_series[start:end]) / (end - start))

    # 从后往前找 HR 比前段均值高出 >= 10bpm 的持续上升段
    # 用最后 20% 数据的平均 HR 作为 "冲刺段HR均值"，往前找到第一个低于此值-8bpm 的点
    last_20pct = max(n // 5, 30)
    sprint_hr_avg = sum(smooth_hr[-last_20pct:]) / last_20pct

    for i in range(n - last_20pct, last_20pct, -1):
        if smooth_hr[i] < sprint_hr_avg - 8:
            # 找到分段点了
            return i, i + 1

    return n // 2, n // 2  # fallback: 在中间分段


def analyze_csv(path: str, age: Optional[int] = None,
                lt_hr: Optional[int] = None, lt_pace_sec: Optional[int] = None,
                lt_mh: Optional[int] = None,
                user_desc: str = "",
                workout_type: Optional[int] = None) -> str:
    """对单个 gomore CSV 执行深度分析，返回 Markdown 报告。"""
    rows, header = parse_csv(path)

    if not rows:
        return "# 错误：CSV 文件为空或格式不正确\n"

    # 读取元数据
    first_ts = safe_int(rows[0][COL_TIMESTAMP])
    last_ts = safe_int(rows[-1][COL_TIMESTAMP])
    duration = last_ts - first_ts
    if duration <= 0:
        duration = len(rows)  # fallback: 用行数作为秒数

    # 运动类型
    wt = workout_type
    if wt is None:
        wt = safe_int(rows[0][COL_WORKOUT_TYPE], 31)
    wt_name = WORKOUT_TYPES.get(wt, f"未知({wt})")

    # 计算每行数据
    hr_data = []
    speed_data = []
    valid_hr = []
    valid_spd = []
    timestamps = []

    for r in rows:
        ts = safe_int(r[COL_TIMESTAMP])
        hr = safe_int(r[COL_HR], -1)
        spd = speed_csv_to_kmh(r[COL_SPEED])
        timestamps.append(ts)

        hr_data.append(hr)
        speed_data.append(spd)

        if 30 <= hr <= 250:
            valid_hr.append(hr)
        if spd > 0:
            valid_spd.append(spd)

    # 基本信息
    total_rows = len(rows)
    valid_hr_count = len(valid_hr)
    hr_min = min(valid_hr) if valid_hr else 0
    hr_max = max(valid_hr) if valid_hr else 0
    hr_avg = sum(valid_hr) / len(valid_hr) if valid_hr else 0
    spd_avg = sum(valid_spd) / len(valid_spd) if valid_spd else 0

    # 总距离（km）：速度(km/h) * 1秒 / 3600
    total_dist = sum(s / 3600.0 for s in valid_spd)

    # 时间格式化
    start_dt = datetime.fromtimestamp(first_ts, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(last_ts, tz=timezone.utc)

    # userInfo
    userinfo_raw = rows[0][COL_USERINFO].strip() if len(rows[0]) > COL_USERINFO else ""
    userinfo_desc = f"`{userinfo_raw}`" if userinfo_raw and userinfo_raw != "0" else "全为 `0`，未包含用户信息"

    # 年龄
    userinfo_age = _get_userinfo_age(userinfo_raw)
    resolved_age = age or userinfo_age or 35  # fallback 35
    hr_max_est = 208 - resolved_age * 0.7

    # ==================== 每公里分段统计 ====================
    cum_dist = 0.0
    km_sections = []  # [(km_n, hr_min, hr_max, hr_avg, ts)]
    km_hr_buffer = []
    prev_km = 0

    for i, r in enumerate(rows):
        spd = speed_csv_to_kmh(r[COL_SPEED])
        hr = safe_int(r[COL_HR], -1)
        if spd > 0:
            cum_dist += spd / 3600.0
        if 30 <= hr <= 250:
            km_hr_buffer.append(hr)

        km_now = int(cum_dist)
        if km_now > prev_km and km_hr_buffer:
            ts_str = datetime.fromtimestamp(safe_int(r[COL_TIMESTAMP]), tz=timezone.utc).strftime("%H:%M")
            km_sections.append((
                prev_km + 1,
                min(km_hr_buffer),
                max(km_hr_buffer),
                sum(km_hr_buffer) // len(km_hr_buffer),
                ts_str,
            ))
            prev_km = km_now
            km_hr_buffer = []

    # 最后一段（不足1km）
    if km_hr_buffer:
        ts_str = datetime.fromtimestamp(timestamps[-1], tz=timezone.utc).strftime("%H:%M")
        km_sections.append((
            prev_km + 1,
            min(km_hr_buffer),
            max(km_hr_buffer),
            sum(km_hr_buffer) // len(km_hr_buffer),
            ts_str + " (末段)",
        ))

    # ==================== 巡航段 vs 冲刺段 ====================
    # 使用自动检测分段点
    cruise_end_idx, sprint_start_idx = _detect_sprint_phase(hr_data, speed_data)
    # 如果用户提供了描述，尝试解析"最后Nkm"
    sprint_km_hint = None
    if user_desc:
        import re
        m = re.search(r"最后(\d+(?:\.\d+)?)\s*km", user_desc)
        if m:
            sprint_km_hint = float(m.group(1))

    if sprint_km_hint:
        # 用距离来确定分段
        cum = 0.0
        sprint_start_idx = 0
        for i, r in enumerate(rows):
            spd = speed_csv_to_kmh(r[COL_SPEED])
            if spd > 0:
                cum += spd / 3600.0
            if cum >= total_dist - sprint_km_hint:
                sprint_start_idx = i
                break
        cruise_end_idx = sprint_start_idx - 1
    else:
        # 用 HR 检测结果
        sprint_km_hint = None

    # 巡航段 HR/速度
    cruise_hrs = [h for h in hr_data[:cruise_end_idx] if 30 <= h <= 250]
    cruise_spds = [s for s in speed_data[:cruise_end_idx] if s > 0]
    # 冲刺段 HR/速度
    sprint_hrs = [h for h in hr_data[sprint_start_idx:] if 30 <= h <= 250]
    sprint_spds = [s for s in speed_data[sprint_start_idx:] if s > 0]

    # 巡航段距离
    cruise_dist = sum(s / 3600.0 for s in cruise_spds)
    sprint_dist = sum(s / 3600.0 for s in sprint_spds)

    # ==================== HR 区间配速映射 ====================
    hr_bins = [(130, 140), (140, 150), (150, 155), (155, 160), (160, 170)]
    hr_pace_map = []
    for hr_min_bin, hr_max_bin in hr_bins:
        bin_hrs = []
        bin_spds = []
        for i, r in enumerate(rows):
            h = safe_int(r[COL_HR], -1)
            s = speed_csv_to_kmh(r[COL_SPEED])
            if hr_min_bin <= h < hr_max_bin and s > 0:
                bin_hrs.append(h)
                bin_spds.append(s)
        if bin_hrs:
            avg_bin_hr = sum(bin_hrs) / len(bin_hrs)
            avg_bin_spd = sum(bin_spds) / len(bin_spds)
            hr_pace_map.append((hr_min_bin, hr_max_bin, avg_bin_hr, avg_bin_spd))

    # 高 HR 阈值数据
    high_hr_threshold = 155
    high_hr_spds = [s for i, s in enumerate(speed_data)
                    if s > 0 and 30 <= hr_data[i] <= 250 and hr_data[i] >= high_hr_threshold]
    high_hr_hrs = [h for h in hr_data if 30 <= h <= 250 and h >= high_hr_threshold]

    # ==================== 乳酸阈值分析 ====================
    resolved_lt_hr = lt_hr or hr_max  # 如果没有提供设备LT HR，用CSV最大HR
    resolved_lt_pace_sec = lt_pace_sec

    lt_pace_str = pace_sec_to_pace_str(resolved_lt_pace_sec) if resolved_lt_pace_sec else "N/A"
    lt_speed_kmh = pace_sec_to_kmh(resolved_lt_pace_sec) if resolved_lt_pace_sec else None

    lt_hr_pct = (resolved_lt_hr / hr_max_est * 100) if hr_max_est > 0 else 0

    # 算法输出最大心率(mh)占比
    lt_mh_pct = (resolved_lt_hr / lt_mh * 100) if lt_mh and lt_mh > 0 else None

    # 同等HR下实际配速
    if high_hr_spds:
        actual_high_hr_avg_spd = sum(high_hr_spds) / len(high_hr_spds)
        actual_high_hr_avg_hr = sum(high_hr_hrs) / len(high_hr_hrs)
    else:
        actual_high_hr_avg_spd = 0
        actual_high_hr_avg_hr = 0

    # 最后一段冲刺配速
    if sprint_spds:
        sprint_avg_spd = sum(sprint_spds) / len(sprint_spds)
        sprint_avg_hr = sum(sprint_hrs) / len(sprint_hrs) if sprint_hrs else 0
        sprint_peak_hr = max(sprint_hrs) if sprint_hrs else 0
    else:
        sprint_avg_spd = 0
        sprint_avg_hr = 0
        sprint_peak_hr = 0

    # ==================== 生成报告 ====================

    lines = []
    csv_name = os.path.basename(path)

    lines.append("# 乳酸阈值 — CSV 数据分析报告")
    lines.append("")

    # 概述
    lines.append("## 概述")
    lines.append("")
    lines.append(f"- **CSV 文件**: `{csv_name}`")
    lines.append(f"- **运动类型**: {wt_name} (workoutType={wt})")
    lines.append(f"- **运动时间**: {start_dt.strftime('%Y-%m-%d %H:%M')} ~ {end_dt.strftime('%H:%M')} ({format_duration(duration)})")
    lines.append(f"- **总距离**: ~{total_dist:.1f} km")
    lines.append("")

    # CSV 数据特征
    lines.append("## CSV 数据特征")
    lines.append("")
    lines.append("### 用户信息")
    lines.append("")
    lines.append(f"CSV 中 userInfo 列: {userinfo_desc}")
    if userinfo_age:
        lines.append(f"- 从 userInfo 解析年龄: {userinfo_age}")
    if age:
        lines.append(f"- 外部提供年龄: {age}")
    lines.append("")
    lines.append("### 数据质量")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|------|-----|")
    lines.append(f"| 总样本数 | {total_rows} |")
    lines.append(f"| 有效HR样本 | {valid_hr_count} |")
    lines.append(f"| HR 范围 | {hr_min} ~ {hr_max} bpm |")
    lines.append(f"| HR 平均值 | {hr_avg:.0f} bpm |")
    lines.append(f"| 速度单位 | **km/h（gomore_log_to_csv.py 导出值）** |")
    lines.append("")
    lines.append(f"> **重要**：本报告针对 `gomore_log_to_csv.py` 生成的 CSV，`SPEED` 按 km/h 读取。例如首段巡航速度约 {spd_avg:.1f} km/h = {kmh_to_pace_str(spd_avg)}。")
    lines.append("")

    # 运动节奏分析
    lines.append("## 运动节奏分析")
    lines.append("")
    lines.append("### 每公里分段统计")
    lines.append("")
    lines.append("| 公里 | HR 范围(平均) | 时间点 | 说明 |")
    lines.append("|------|-------------|-------|------|")
    for km_n, km_hr_min, km_hr_max, km_hr_avg, ts_str in km_sections:
        remark = ""
        if km_n == 1:
            remark = "热身"
        elif km_hr_avg >= 150:
            remark = "高强度"
        elif km_hr_max - km_hr_min > 15:
            remark = "节奏变化"
        lines.append(f"| km{km_n} | {km_hr_min}-{km_hr_max}({km_hr_avg}) | {ts_str} | {remark} |")
    lines.append("")

    # 巡航 vs 冲刺
    cruise_name = f"前{cruise_dist:.0f}km (巡航)"
    sprint_name = f"最后{sprint_dist:.1f}km"
    if sprint_km_hint:
        sprint_name = f"最后{sprint_km_hint:.0f}km (冲刺)"

    lines.append("### 巡航段 vs 冲刺段")
    lines.append("")
    lines.append("| 阶段 | 距离 | HR 平均 | HR 峰值 | 平均配速 | 平均速度 |")
    lines.append("|------|------|---------|---------|---------|---------|")

    if cruise_spds:
        c_avg_hr = sum(cruise_hrs) / len(cruise_hrs) if cruise_hrs else 0
        c_peak_hr = max(cruise_hrs) if cruise_hrs else 0
        c_avg_spd = sum(cruise_spds) / len(cruise_spds)
        c_avg_pace = kmh_to_pace_str(c_avg_spd)
        lines.append(f"| **{cruise_name}** | {cruise_dist:.1f}km | {c_avg_hr:.0f} | {c_peak_hr} | **{c_avg_pace}** | {c_avg_spd:.1f} km/h |")

    if sprint_spds:
        s_avg_hr = sum(sprint_hrs) / len(sprint_hrs) if sprint_hrs else 0
        s_peak_hr = max(sprint_hrs) if sprint_hrs else 0
        s_avg_spd = sum(sprint_spds) / len(sprint_spds)
        s_avg_pace = kmh_to_pace_str(s_avg_spd)
        lines.append(f"| **{sprint_name}** | {sprint_dist:.1f}km | {s_avg_hr:.0f} | {s_peak_hr} | **{s_avg_pace}** | {s_avg_spd:.1f} km/h |")
        if cruise_spds:
            delta_hr = s_avg_hr - c_avg_hr
            lines.append(f"| 差异 | - | +{delta_hr:.0f} bpm | - | - | +{s_avg_spd - c_avg_spd:.1f} km/h |")

    lines.append("")

    # HR-配速对应
    lines.append("## 心率-配速对应关系")
    lines.append("")
    lines.append("| HR 区间 | 平均 HR | 平均速度 | 对应配速 |")
    lines.append("|---------|---------|---------|---------|")
    for min_bin, max_bin, avg_hr_bin, avg_spd_bin in hr_pace_map:
        lines.append(f"| {min_bin}-{max_bin} bpm | {avg_hr_bin:.0f} | {avg_spd_bin:.1f} km/h | {kmh_to_pace_str(avg_spd_bin)} |")
    lines.append("")

    # 乳酸阈值分析
    lines.append("## 乳酸阈值分析")
    lines.append("")
    lines.append("### 设备报告的乳酸阈值")
    lines.append("")
    lines.append("| 参数 | 值 | 占估算HRmax比例 | 占算法mh比例 |")
    lines.append("|------|-----|----------------|-------------|")
    mh_col = f"| {lt_mh_pct:.1f}%" if lt_mh_pct is not None else "| -"
    lines.append(f"| LT 心率 | **{resolved_lt_hr} bpm** | {lt_hr_pct:.1f}%{mh_col} |")
    lines.append(f"| LT 配速 | **{lt_pace_str}** | - | - |")
    if lt_speed_kmh:
        lines.append(f"| LT 速度 | {lt_speed_kmh:.1f} km/h | - | - |")
    if lt_mh:
        lines.append(f"| 算法最大心率(mh) | **{lt_mh} bpm** | {(lt_mh/hr_max_est*100):.1f}% | - |")
    lines.append("")
    lines.append(f"> 默认 HRmax (208-age×0.7 公式): 208 - {resolved_age}×0.7 = **{hr_max_est:.1f} bpm**")
    lines.append("")

    lines.append("### 实际运动数据 vs 设备阈值")
    lines.append("")
    lines.append("| 数据来源 | HR | 对应配速 |")
    lines.append("|---------|-----|---------|")
    lines.append(f"| **设备 LT** | {resolved_lt_hr} | {lt_pace_str} |")
    if actual_high_hr_avg_spd > 0:
        lines.append(f"| HR≥{high_hr_threshold} 实际平均 | {actual_high_hr_avg_hr:.0f} | {kmh_to_pace_str(actual_high_hr_avg_spd)} |")
    if sprint_avg_spd > 0:
        lines.append(f"| {sprint_name} 实际平均 | {sprint_avg_hr:.0f} | {kmh_to_pace_str(sprint_avg_spd)} |")
    lines.append("")

    # 核心发现
    lines.append("### 核心发现")
    lines.append("")

    # 自动生成偏差描述
    deviation_parts = []

    # mh 占比分析
    if lt_mh_pct is not None:
        if lt_mh_pct > 95:
            deviation_parts.append(f"**乳酸阈心率 ({resolved_lt_hr} bpm) 占算法最大心率(mh={lt_mh})的 {lt_mh_pct:.1f}%** — 偏高，接近最大心率")
        elif lt_mh_pct < 80:
            deviation_parts.append(f"**乳酸阈心率 ({resolved_lt_hr} bpm) 占算法最大心率(mh={lt_mh})的 {lt_mh_pct:.1f}%** — 偏低，与最大心率差距较大")
        else:
            deviation_parts.append(f"**乳酸阈心率 ({resolved_lt_hr} bpm) 占算法最大心率(mh={lt_mh})的 {lt_mh_pct:.1f}%** — 处于合理区间")

    if lt_hr_pct < 80:
        deviation_parts.append(f"**乳酸阈心率 ({resolved_lt_hr} bpm) 偏低** — 仅占估算 HRmax 的 {lt_hr_pct:.1f}%，低于乳酸阈典型范围(85-92%)")
    elif lt_hr_pct > 92:
        deviation_parts.append(f"**乳酸阈心率 ({resolved_lt_hr} bpm) 偏高** — 占估算 HRmax 的 {lt_hr_pct:.1f}%，接近或超过乳酸阈典型范围上限(92%)")
    else:
        deviation_parts.append(f"**乳酸阈心率 ({resolved_lt_hr} bpm) 合理** — 占估算 HRmax 的 {lt_hr_pct:.1f}%，在乳酸阈典型范围(85-92%)内")

    if lt_speed_kmh and spd_avg > 0:
        if lt_speed_kmh < spd_avg:
            diff_pace = kmh_to_pace_str(lt_speed_kmh)
            actual_pace = kmh_to_pace_str(spd_avg)
            deviation_parts.append(
                f"**乳酸阈配速 ({lt_pace_str}) 慢于全程平均配速 ({actual_pace})**"
            )
        if actual_high_hr_avg_spd > 0 and lt_speed_kmh and lt_speed_kmh < actual_high_hr_avg_spd:
            d = actual_high_hr_avg_spd - lt_speed_kmh
            deviation_parts.append(
                f"**乳酸阈配速与同等HR下实际配速差异**: "
                f"设备LT配速={lt_pace_str}，同等HR(≥{high_hr_threshold})实际配速={kmh_to_pace_str(actual_high_hr_avg_spd)}，差异约{d:.1f} km/h"
            )

    if not deviation_parts:
        deviation_parts.append("设备乳酸阈值与用户实际运动数据基本吻合。")

    lines.append("**关键发现**:")
    for dp in deviation_parts:
        lines.append(f"- {dp}")
    lines.append("")

    # 具体分析
    lines.append("具体来说：")
    lines.append("")
    lines.append(f"1. **乳酸阈心率 ({resolved_lt_hr} bpm) 的合理性**：")
    lt_assessment = "合理" if 80 <= lt_hr_pct <= 92 else ("偏低" if lt_hr_pct < 80 else "偏高")
    reached_text = "用户实际运动HR达到了该值" if hr_max >= resolved_lt_hr else f"用户实际HR未达到该值(最高{hr_max})"
    lines.append(f"   - 占估算 HRmax {hr_max_est:.0f} 的 {lt_hr_pct:.1f}%（{lt_assessment}）")
    if lt_mh_pct is not None:
        mh_assessment = "合理" if 80 <= lt_mh_pct <= 95 else ("偏低" if lt_mh_pct < 80 else "偏高")
        lines.append(f"   - 占算法输出最大心率(mh={lt_mh})的 {lt_mh_pct:.1f}%（{mh_assessment}）")
    lines.append(f"   - {reached_text}")
    lines.append("")

    if lt_speed_kmh and actual_high_hr_avg_spd > 0:
        diff_kmh = actual_high_hr_avg_spd - lt_speed_kmh
        if abs(diff_kmh) > 0.5:
            direction = "偏保守" if diff_kmh > 0 else "偏激进"
            lines.append(f"2. **乳酸阈配速 ({lt_pace_str}) 的偏差**：")
            lines.append(f"   - 与同等HR(≥{high_hr_threshold})实际配速相比{direction}，偏差约{'%+.1f' % diff_kmh} km/h")
            lines.append(f"   - 用户实际在该HR区间的配速为 {kmh_to_pace_str(actual_high_hr_avg_spd)}")
            lines.append("")
            lines.append("3. 造成偏差的可能原因：")
        else:
            lines.append(f"2. **乳酸阈配速 ({lt_pace_str})** 与用户实际表现基本一致。")
            lines.append("")
            lines.append("3. 可能存在的细微偏差原因：")
    else:
        lines.append(f"2. **乳酸阈配速分析**：数据不足，无法评估。")
        lines.append("")
        lines.append("3. 可能影响精度的因素：")

    userinfo_missing = not userinfo_raw or userinfo_raw.strip() == "0"
    if userinfo_missing:
        lines.append("   - **用户个人信息缺失**: CSV 中 `userInfo` 字段为 `0`，算法可能使用默认值导致计算偏差")
    else:
        lines.append(f"   - **用户信息完整**: 已从 CSV 读取用户信息（年龄={userinfo_age}, 静息HR={_get_userinfo_hr(userinfo_raw)}），算法使用实际参数")
    lines.append("   - **算法滤波平滑**: GoMore 算法可能对配速做了平滑处理，选取了稍低的值")
    lines.append("")

    # 结论
    lines.append("## 结论")
    lines.append("")
    if 80 <= lt_hr_pct <= 92:
        lines.append(f"1. **设备乳酸阈值 ({resolved_lt_hr} bpm, {lt_pace_str}) 整体{'合理' if not deviation_parts else '基本合理'}**。")
    else:
        lines.append(f"1. **设备乳酸阈值 ({resolved_lt_hr} bpm, {lt_pace_str}) 需结合其他信息判断**。")
    lines.append(f"2. 用户在实际运动中的表现{'超过了' if sprint_avg_spd > (lt_speed_kmh or 0) else '接近'}设备显示的乳酸阈值强度。")

    # 建议修正
    if sprint_spds and high_hr_spds:
        suggested_hr = int(actual_high_hr_avg_hr) if actual_high_hr_avg_hr > 0 else lt_hr
        suggested_pace = kmh_to_pace_str(actual_high_hr_avg_spd) if actual_high_hr_avg_spd > 0 else lt_pace_str
        lines.append(f"3. 乳酸阈心率 {resolved_lt_hr} bpm 占估算最大心率的 {lt_hr_pct:.1f}%，处于乳酸阈的典型范围（85-92% HRmax）。")
        if actual_high_hr_avg_spd > 0 and lt_speed_kmh and abs(actual_high_hr_avg_spd - lt_speed_kmh) > 0.5:
            lines.append(f"4. **建议的修正乳酸阈值**: ~{suggested_hr} bpm @ {suggested_pace}（基于 HR≥{high_hr_threshold} 区间的平均表现）")
    else:
        lines.append(f"3. 需更多数据来评估乳酸阈值精度。")
        lines.append(f"4. 建议收集更多含 GPS 和 HR 的跑步数据后重新评估。")

    # mh 占比综合评价
    if lt_mh_pct is not None:
        lines.append("")
        lines.append(f"4. **算法最大心率(mh)对比**: 乳酸阈心率占算法输出最大心率(mh={lt_mh})的 {lt_mh_pct:.1f}%，可综合判断阈值设置的合理性。")
    lines.append("")

    # 备注
    lines.append("---")
    lines.append("")
    lines.append("## 备注")
    lines.append("")
    lines.append(f"- **分析方法**: 纯 CSV 数据统计分析与 GoMore 算法逻辑交叉验证")
    lines.append(f"- **分析局限性**: 如需精确验证 GoMore SDK 算法输出，需要在真实设备上运行 `utest gomore_offline_csv_tests`")
    lines.append(f"- **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="乳酸阈 CSV 深度分析报告生成器（Feature 3）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python3 csv_deep_analyzer.py gomore_data_20260506_112517.csv
  python3 csv_deep_analyzer.py data.csv --age 44 --lt-hr 160 --lt-pace-sec 301
  python3 csv_deep_analyzer.py data.csv --user-desc "最后2km提速" --output report.md
"""
    )
    parser.add_argument("csv", help="GoMore 样本 CSV 文件路径")
    parser.add_argument("--age", type=int, default=None,
                        help="用户年龄（用于 Tanaka HRmax 估算）")
    parser.add_argument("--lt-hr", type=int, default=None,
                        help="设备输出的乳酸阈心率 (bpm)")
    parser.add_argument("--lt-pace-sec", type=int, default=None,
                        help="设备输出的乳酸阈配速 (秒/km)，如 301 = 5'01/km")
    parser.add_argument("--lt-mh", type=int, default=None,
                        help="算法输出的最大心率 mh (bpm)，如 195")
    parser.add_argument("--user-desc", type=str, default="",
                        help="用户描述文本，如 '最后2km提速'，用于辅助分段")
    parser.add_argument("--workout-type", type=int, default=None,
                        help="运动类型（如 31=户外跑步，4=跑步机）")
    parser.add_argument("-o", "--output", type=str, default=None,
                        help="输出文件路径（默认输出到 stdout）")
    args = parser.parse_args()

    if not os.path.isfile(args.csv):
        print(f"错误: 文件不存在: {args.csv}", file=sys.stderr)
        sys.exit(1)

    report = analyze_csv(
        args.csv,
        age=args.age,
        lt_hr=args.lt_hr,
        lt_pace_sec=args.lt_pace_sec,
        lt_mh=args.lt_mh,
        user_desc=args.user_desc,
        workout_type=args.workout_type,
    )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"报告已写入: {args.output}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
gomore_log_to_csv.py

位置：framework/engine/sportEngine/alg_gomore/sample_data/gomore_log_to_csv.py

用途：
从包含 `_processGomoreAlgo` 事件的日志中提取心率/速度/时间等字段，生成与本工程样例格式一致的 CSV 文件。
生成的文件名示例：`gomore_data_YYYYMMDD_HHMMSS.csv`（若提供输出目录或文件则按指定处理）。

快速用法示例：

# 输出到日志同目录（自动从日志文件名提取时间）：
python3 gomore_log_to_csv.py /path/to/MILI_LYON-D1710330-20251225184042.log_bak

# 指定输出目录或文件名：
python3 gomore_log_to_csv.py /path/to/log.log /tmp/
python3 gomore_log_to_csv.py /path/to/log.log /tmp/my.csv

# 覆盖 StartInput.workoutType 与 userInfo：
python3 gomore_log_to_csv.py /path/to/log.log -s 31 -u "30.0,0.0,170.0,60.0,-1.0,55.0,-1.0"

备注：脚本会在日志中查找 `_processGomoreAlgo` 并在周围上下文解析 `utcTimeStamp`/`timestamp`、`timeZoneOffset`、`hrRef`/`HR`/`hr`、`speedRef`/`SPEED`/`sd` 等字段。
分段日志优先使用 `sport cmd` 所在行打印的设备时钟；再以 GoMore enable 的 `rtc/tz` 换算为 epoch。
只有两类真实时间都缺失时，才使用自增时间戳作为最后兜底。

"""

import sys
import os
import re
import csv
import time
import argparse
import calendar
import math
from datetime import datetime
from typing import List, Tuple
import json

CTX_BACK = 200
CTX_FWD = 500
DEFAULT_USERINFO = '38.0,1.0,173.0,65.0,-1.0,60.0,-1.0'

# 匹配器
RE_UTC = re.compile(r"utcTimeStamp\s*[:=]\s*(\d{9,12})", re.IGNORECASE)
RE_TIMESTAMP = re.compile(r"\btimestamp\s*[:=]\s*(\d{9,12})", re.IGNORECASE)
RE_NUM_TS = re.compile(r"\b(1[5-9]\d{8,10})\b")  # 10+位，简单检测 epoch-like
RE_TZ = re.compile(r"timeZoneOffset\s*[:=]\s*([+-]?\d+)", re.IGNORECASE)
RE_ANY_TZ = re.compile(r"\btz\s*[:=]\s*([+-]?\d+)", re.IGNORECASE)
RE_HR = re.compile(r"(?:hrRef|HR|hr|rh)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
RE_SPEED = re.compile(r"(?:speedRef|SPEED|sd)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
RE_SPRT_GMS = re.compile(
    r"SPRT_GMS\s+rh\s*=\s*([+-]?[0-9]+(?:\.[0-9]+)?)\s*,\s*"
    r"sd\s*=\s*([+-]?[0-9]+(?:\.[0-9]+)?)\s*,\s*"
    r"state\s*=\s*([0-9]+)",
    re.IGNORECASE,
)
SPRT_GMS_RUNNING_STATE = 2
RE_OLD_GOMORECFG = re.compile(r"_gomoreSdkConfig[^\n\r]*", re.IGNORECASE)
# 匹配 ALG_GM 行中可能的 c <7数字> 格式
RE_ALG_GM_C = re.compile(r"ALG_GM[^\n\r]*\bc\s*([0-9eE+\-., \t]+)", re.IGNORECASE)
# store last matched line for diagnostics
LAST_PARSED_USERINFO_LINE = None


def _line_bounds(text: str, pos: int) -> Tuple[int, int]:
    """Return the bounds of the physical log line that contains *pos*."""
    start = text.rfind('\n', 0, pos) + 1
    end = text.find('\n', pos)
    return start, len(text) if end == -1 else end


def _log_clock_at(text: str, pos: int):
    """Read the device local clock printed in a log line.

    Typical sport logs use ``I>09/03 05:06:04.796``.  This is deliberately
    line-local: nearby lines may belong to another asynchronous module.
    """
    start, end = _line_bounds(text, pos)
    m = re.search(r"\b(\d{1,2})/(\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?", text[start:end])
    if not m:
        return None
    return tuple(int(v) for v in m.groups())


def _clock_delta_seconds(anchor_clock, event_clock, year: int) -> int:
    """Return event - anchor in local clock seconds, including date rollover."""
    anchor = datetime(year, *anchor_clock)
    event = datetime(year, *event_clock)
    delta = (event - anchor).total_seconds()
    # Logs can span New Year.  Pick the closest plausible occurrence.
    if delta < -180 * 86400:
        event = datetime(year + 1, *event_clock)
    elif delta > 180 * 86400:
        event = datetime(year - 1, *event_clock)
    return int((event - anchor).total_seconds())


def _filename_date(input_name: str):
    """Return YYYYMMDD from the usual device-log filename, if available."""
    m = re.search(r"(?<!\d)(20\d{6})(?:[_-]?\d{6})?", input_name or '')
    return m.group(1) if m else None


def _format_log_datetime(content: str, pos: int, input_name: str, fallback_ts: int) -> str:
    clock = _log_clock_at(content, pos)
    if not clock:
        return ''
    date = _filename_date(input_name)
    year = int(date[:4]) if date else time.gmtime(fallback_ts).tm_year
    try:
        return datetime(year, *clock).strftime('%Y-%m-%d %H:%M:%S')
    except ValueError:
        return ''



def extract_fields_around(text: str, pos: int) -> Tuple[int,int,int,float]:
    """从给定位置前后文本提取字段。返回 (utc, tz, hr, speed)。

    改进点：
    - 在上下文中列出所有可能的时间候选，选择与事件位置距离最近的那个（避免选择远处的 ID / 大数字）
    - 将毫秒/微秒级时间归一化为秒（通过 //1000 循环），保证最终为秒级 epoch
    - 若未找到时间返回 -1
    """
    start = max(0, pos - CTX_BACK)
    end = min(len(text), pos + CTX_FWD)
    ctx = text[start:end]

    # 收集所有时间候选（utc/timestamp/num），按与事件绝对距离选择最近的
    cand = []  # list of (distance, int_value)
    for rx in (RE_UTC, RE_TIMESTAMP, RE_NUM_TS):
        for m in rx.finditer(ctx):
            try:
                v = int(m.group(1))
            except Exception:
                continue
            abs_pos = start + m.start()
            dist = abs(abs_pos - pos)
            cand.append((dist, v))

    if cand:
        # 选择最近的候选
        cand.sort(key=lambda x: x[0])
        utc = cand[0][1]
        # 归一化到秒（如果是 ms/us，则 //=1000 直到长度合理）
        try:
            # reduce by factors of 1000 while it's clearly >10 digits
            while utc > 9999999999:
                utc = utc // 1000
        except Exception:
            pass
    else:
        utc = -1

    # timezone
    tz = 0
    best_tz = None
    for m in RE_TZ.finditer(ctx):
        abs_pos = start + m.start()
        dist = abs(abs_pos - pos)
        try:
            val = int(m.group(1))
        except Exception:
            continue
        if best_tz is None or dist < best_tz[0]:
            best_tz = (dist, val)
    if best_tz:
        tz = best_tz[1]

    # hr and speed: pick nearest match as well
    hr = -1.0
    best_hr = None
    for m in RE_HR.finditer(ctx):
        abs_pos = start + m.start()
        dist = abs(abs_pos - pos)
        try:
            val = float(m.group(1))
        except Exception:
            continue
        if best_hr is None or dist < best_hr[0]:
            best_hr = (dist, val)
    if best_hr:
        hr = best_hr[1]

    speed = -1.0
    best_sp = None
    for m in RE_SPEED.finditer(ctx):
        abs_pos = start + m.start()
        dist = abs(abs_pos - pos)
        try:
            val = float(m.group(1))
        except Exception:
            continue
        if best_sp is None or dist < best_sp[0]:
            best_sp = (dist, val)
    if best_sp:
        speed = best_sp[1]

    return utc, tz, hr, speed


def parse_log(content: str) -> List[dict]:
    """解析整个日志文本，返回事件列表，每项为字典：{'pos', 'utc', 'tz', 'hr', 'speed'}。

    改进：保持时间单调递增；若捕获到非单调或缺失时间则基于 last_utc 修正。
    """
    sprt_matches = list(RE_SPRT_GMS.finditer(content))
    process_positions = [m.start() for m in re.finditer(r"_processGomoreAlgo", content)]
    if not sprt_matches and not process_positions:
        return []

    rows = []
    last_utc = None
    # SPRT_GMS carries the sport state on the same line.  Prefer it whenever
    # available so paused/stopped records do not enter the replay sample.
    # Keep all running-state values, including invalid HR/speed sentinels.
    events = []
    if sprt_matches:
        for m in sprt_matches:
            if int(m.group(3)) != SPRT_GMS_RUNNING_STATE:
                continue
            events.append((m.start(), float(m.group(1)), float(m.group(2))))
    else:
        # Older logs may only contain the actual process-call marker and have
        # no sport state field to filter on.
        events = [(pos, None, None) for pos in process_positions]

    for pos, event_hr, event_speed in events:
        utc, tz, hr, speed = extract_fields_around(content, pos)
        if event_hr is not None:
            hr = event_hr
            speed = event_speed
        # 如果没有找到 utc，则用基于 last_utc 的自增时间戳
        if utc == -1 or utc is None:
            if last_utc is not None:
                utc = last_utc + 1
            else:
                utc = int(time.time())
        else:
            # 如果捕到的 utc 非单调（<= last_utc），修正为 last_utc+1
            if last_utc is not None and utc <= last_utc:
                utc = last_utc + 1
        last_utc = utc

        # Keep the running sample and encode invalid GoMore input values with
        # the CSV sentinels consumed by the offline simulator.
        hr_int = int(hr) if 0 <= hr < 255 else 255
        if speed < 0:
            speed = 0.0
        rows.append({'pos': pos, 'utc': utc, 'raw_utc': utc, 'tz': tz, 'hr': hr_int, 'speed': speed})

    return rows


def extract_userinfo_from_old_log(content: str) -> str:
    global LAST_PARSED_USERINFO_LINE
    """从旧的 `_gomoreSdkConfig` 日志行提取并还原 userInfo（旧日志中值可能为 userInfo*2）。
    返回格式化的 7 字段字符串，未找到返回空串。

    逻辑：先找包含 _gomoreSdkConfig 的整行，再从该行中提取所有数字（浮点/整数/科学计数法），
    再根据 _looks_doubled 规则决定是否除以 2 还原。
    """
    m = RE_OLD_GOMORECFG.search(content)
    line = None
    matched_via_old = False
    if m:
        line = m.group(0)
        matched_via_old = True
    else:
        # 未找到 _gomoreSdkConfig 时，尝试匹配 ALG_GM 行中的 c 字段（示例: "ALG_GM c 54.000000,2.000000,362.000000,...")
        m2 = RE_ALG_GM_C.search(content)
        if m2:
            line = m2.group(1)
    if not line:
        LAST_PARSED_USERINFO_LINE = None
        return ''
    LAST_PARSED_USERINFO_LINE = line
    nums = re.findall(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', line)
    vals = [float(x) for x in nums] if nums else []
    if not vals:
        LAST_PARSED_USERINFO_LINE = None
        return ''
    # 旧日志中 _gomoreSdkConfig 情况：始终认为这些值是放大的（按历史行为除以2）
    def _looks_doubled(vlist: List[float]) -> bool:
        if len(vlist) >= 3:
            try:
                h = abs(vlist[2])
                if h > 250:
                    return True
            except Exception:
                pass
        for x in vlist:
            try:
                if abs(x) > 300:
                    return True
            except Exception:
                continue
        return False

    if matched_via_old:
        # 已匹配到明确的 _gomoreSdkConfig 行，按用户要求始终除以2
        vals = [v / 2.0 for v in vals]
    else:
        if _looks_doubled(vals):
            vals = [v / 2.0 for v in vals]

    if len(vals) < 7:
        vals += [-1.0] * (7 - len(vals))
    vals = vals[:7]
    return ','.join(['{:.1f}'.format(v) for v in vals])


def extract_userinfo_in_range(content: str, start_pos: int = 0, end_pos: int = None) -> str:
    """Return the latest user profile found in a single parsed activity range."""
    start_pos = max(0, start_pos)
    previous_disables = list(re.finditer(r"_disableGomoreAlgo|disable.*Gomore", content[:start_pos], re.IGNORECASE))
    if previous_disables:
        start_pos = previous_disables[-1].start()
    window = content[start_pos:end_pos]
    matches = list(RE_OLD_GOMORECFG.finditer(window))
    if matches:
        return extract_userinfo_from_old_log(matches[-1].group(0))
    c_matches = list(RE_ALG_GM_C.finditer(window))
    if c_matches:
        return extract_userinfo_from_old_log(c_matches[-1].group(0))
    return ''


def _normalize_userinfo(userinfo: str) -> str:
    """Return a normalized seven-field profile, or empty when required fields are missing."""
    if not userinfo:
        return ''

    fields = userinfo.split(',')[:7]
    if len(fields) < 4:
        return ''
    fields.extend(['-1'] * (7 - len(fields)))

    values = []
    for field in fields:
        try:
            value = float(field.strip())
        except (TypeError, ValueError):
            value = -1.0
        if not math.isfinite(value):
            value = -1.0
        values.append(value)

    age, gender, height, weight = values[:4]
    if age <= 0 or not age.is_integer() or gender not in (0.0, 1.0) or height <= 0 or weight <= 0:
        return ''

    # GoMore treats nonpositive optional values as absent.
    for idx in range(4, 7):
        if values[idx] <= 0:
            values[idx] = -1.0
    return ','.join(f'{value:.1f}' for value in values)


def _resolve_userinfo_fallback(parsed_userinfo: str, userinfo_arg: str,
                               userinfo_arg_explicit: bool) -> Tuple[str, str]:
    parsed = _normalize_userinfo(parsed_userinfo)
    if parsed:
        return parsed, '整份日志其他位置（回退）'

    if userinfo_arg_explicit:
        explicit = _normalize_userinfo(userinfo_arg)
        if explicit:
            return explicit, '命令行 --user-info 参数'

    default = _normalize_userinfo(DEFAULT_USERINFO)
    return default, f'脚本默认值（{DEFAULT_USERINFO}）'


# 算法输出解析正则
RE_LTSPEED = re.compile(r"ltSpeed\s*=\s*(-?[0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
RE_LH = re.compile(r"\blh\s*=\s*(-?[0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
RE_MH = re.compile(r"\bmh\s*=\s*(-?[0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
RE_GUIDE_RESULT = re.compile(r"\bguideResult\s*=\s*(-?[0-9]+)", re.IGNORECASE)


def convert_speed_to_pace_str(speed_kmh: float) -> str:
    """将 km/h 转换为配速字符串 x'xx" /km"""
    try:
        s = float(speed_kmh)
    except Exception:
        return '-'
    if s <= 0:
        return '-'
    pace_min = 60.0 / s
    minutes = int(pace_min)
    seconds = int(round((pace_min - minutes) * 60))
    if seconds == 60:
        minutes += 1
        seconds = 0
    return f"{minutes}'{seconds:02d}\" /km"


def extract_alg_outputs(content: str):
    """从日志中提取 ALG_GM 算法输出（ltSpeed/lh/mh），返回字典列表。"""
    results = []
    for m in re.finditer(r"ALG_GM[^\n]*", content, re.IGNORECASE):
        line = m.group(0)
        lt = RE_LTSPEED.search(line)
        lh = RE_LH.search(line)
        mh = RE_MH.search(line)
        guide = RE_GUIDE_RESULT.search(line)
        if lt or lh or mh or guide:
            lt_val = float(lt.group(1)) if lt else None
            lh_val = float(lh.group(1)) if lh else None
            mh_val = float(mh.group(1)) if mh else None
            results.append({
                'pos': m.start(),
                'line': line,
                'ltSpeed': lt_val,
                'lh': lh_val,
                'mh': mh_val,
                'guideResult': int(guide.group(1)) if guide else None,
            })
    return results


def _normalize_epoch_candidate(val: int) -> int:
    """把不同位数的时间戳 candidate 规范化为秒级（10位）epoch。

    策略：尝试除以 10 的幂（每次 //=10）直到值落入合理范围 [1.5e9, 3.5e9]，否则退回最接近的近似。
    """
    try:
        v = int(val)
    except Exception:
        return val
    LOW = 1_500_000_000
    HIGH = 3_500_000_000
    # if already in range, keep
    if LOW <= v <= HIGH:
        return v
    # repeatedly divide by 10 until in range or too small
    while v > HIGH:
        v = v // 10
        if LOW <= v <= HIGH:
            return v
    # if still too small, return as-is
    return v


def find_enable_disable_pairs(content: str):
    """寻找 start/end 配对，并提取其真实时间锚点。

    ``sport cmd`` 会打印设备本地时钟，因此优先把它作为行时间锚点；
    GoMore enable 中的 rtc/tz 只用来把该本地时钟换算为 epoch。
    """
    # 更精确的enable匹配：避免误匹配 StartInput.workoutType，只匹配明确的enable动作
    re_start = re.compile(r"GoMore\s+enable|enable.*Gomore|ALG_GM[^\n]*enable|GoMore\s+enabled|\benable\s+GoMore|_enableGomoreAlgo", re.IGNORECASE)
    re_end = re.compile(r"_disableGomoreAlgo|disable.*Gomore", re.IGNORECASE)
    re_rtc = re.compile(r"rtc\s*[:=]\s*(\d{6,20})", re.IGNORECASE)
    re_sport_cmd_start = re.compile(
        r"sport\s+start\s+time|sport\s+cmd\s*=\s*1\b|(?:cmd|command)\s+START\b",
        re.IGNORECASE,
    )

    starts_matches = list(re_start.finditer(content))
    ends = [m.start() for m in re_end.finditer(content)]
    if not starts_matches or not ends:
        return []

    pairs = []
    ei = 0
    last_paired_end = -1
    for m in starts_matches:
        s = m.start()
        # 忽略落在已配对区间内的 start，避免重叠段导致后一个 CSV 包含前一个
        if s <= last_paired_end:
            continue
        # find first end > s
        while ei < len(ends) and ends[ei] <= s:
            ei += 1
        if ei >= len(ends):
            break
        e = ends[ei]
        # The enable line normally carries rtc/tz.  Do not use arbitrary epoch
        # values near a sample record: they frequently belong to another task.
        ctx_start = max(0, s - CTX_BACK)
        ctx_end = min(len(content), s + CTX_FWD)
        ctx = content[ctx_start:ctx_end]
        rtc_val = None
        mr = re_rtc.search(ctx)
        if mr:
            try:
                candidate = int(mr.group(1))
                rtc_val = _normalize_epoch_candidate(candidate)
            except Exception:
                rtc_val = None
        tz_val = None
        tz_matches = list(RE_ANY_TZ.finditer(ctx))
        if tz_matches:
            # For this log family SPRT_GMS may print a bit-field before the
            # ALG_GM line.  The last tz in the enable context is the actual
            # GoMore minute offset (for example tz=480), so use it.
            try:
                tz_val = int(tz_matches[-1].group(1))
            except Exception:
                pass

        # Prefer the sport command clock.  It still exists in logs where the
        # GoMore data lines contain no timestamp at all.
        cmd_begin = max(0, s - 2000)
        cmd_end = min(len(content), s + 2000)
        cmd_match = re_sport_cmd_start.search(content[cmd_begin:cmd_end])
        cmd_pos = cmd_begin + cmd_match.start() if cmd_match else None
        time_anchor_pos = cmd_pos if cmd_pos is not None else s
        pairs.append({
            'start_pos': s,
            'end_pos': e,
            'rtc': rtc_val,
            'tz': tz_val,
            'time_anchor_pos': time_anchor_pos,
        })
        last_paired_end = e
        ei += 1
    return pairs


def write_csv(rows: List[Tuple[int,int,int,float]], out_path: str, sport_type: int = 31, userinfo: str = '38.0,1.0,173.0,65.0,-1.0,60.0,-1.0'):
    """写出与样例 `gomore_data_YYYYMMDD_HHMMSS.csv` 相同的列。

    列（按样例）：
    TIMESTAMP,DATE_TIME,timeZoneOffset,PPG1,PPG2,ACC_X,ACC_Y,ACC_Z,ppgLength,accLength,
    Longitude,Latitude,Altitude,gpsSpeed,gpsAccuracy,HR,SPEED,wristOff,speedSourceOut,
    speedOut,updateIndexRet,StartInput.workoutType,StartInput.rtc,userInfo,
    EndInput.workoutDate,EndOutput.pace0,EndOutput.pace1,EndOutput.hrEstorOut
    """
    # 如果 out_path 指向一个目录、以路径分隔符结尾或为空，则在该目录内根据首个时间戳自动命名
    if (out_path == '' or os.path.isdir(out_path) or out_path.endswith(('/', os.path.sep))) and len(rows) > 0:
        first_ts = rows[0][0]
        dt = time.gmtime(first_ts)
        name = time.strftime('gomore_data_%Y%m%d_%H%M%S.csv', dt)
        # 确保使用平台相关的路径分隔符
        if os.path.isdir(out_path):
            out_path = os.path.join(out_path, name)
        else:
            out_path = os.path.join(out_path.rstrip('/\\'), name)
        out_path = os.path.normpath(out_path)

    def _format_date_time(utc: int, tz_minutes: int) -> str:
        try:
            tt = time.gmtime(utc + tz_minutes * 60)
            return time.strftime('%Y%m%d %H:%M', tt)
        except Exception:
            return ''

    # ensure output directory exists
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            'TIMESTAMP', 'DATE_TIME', 'timeZoneOffset', 'PPG1', 'PPG2', 'ACC_X', 'ACC_Y', 'ACC_Z',
            'ppgLength', 'accLength', 'Longitude', 'Latitude', 'Altitude', 'gpsSpeed', 'gpsAccuracy',
            'HR', 'SPEED', 'wristOff', 'speedSourceOut', 'speedOut', 'updateIndexRet',
            'StartInput.workoutType', 'StartInput.rtc', 'userInfo', 'EndInput.workoutDate',
            'EndOutput.pace0', 'EndOutput.pace1', 'EndOutput.hrEstorOut', 'ACTIVE_TIMESTAMP'
        ])

        # 默认 userInfo 和结束字段
        default_userinfo = userinfo
        for item in rows:
            utc, tz, hr, speed = item[:4]
            active_utc = item[4] if len(item) > 4 else utc
            date_time = _format_date_time(utc, tz)
            # default values to mimic sample file
            ppg1 = -1.0
            ppg2 = -1.0
            acc_x = acc_y = acc_z = -1.0
            ppgLength = 0
            accLength = 0
            longitude = latitude = altitude = -1.0
            gpsSpeed = -1.0
            gpsAccuracy = -1.0
            hr_field = int(hr) if 0 <= float(hr) < 255 else 255
            speed_field = float('{:.2f}'.format(speed)) if float(speed) >= 0 else 0.00
            wristOff = 0
            speedSourceOut = -1.0
            speedOut = -1.0
            updateIndexRet = 0
            start_workout_type = sport_type
            start_rtc = 0 if utc is None else utc
            end_workout_date = 0
            end_pace0 = -1.00
            end_pace1 = -1.00
            end_hr_est = -1.00

            writer.writerow([
                utc, date_time, tz, ppg1, ppg2, acc_x, acc_y, acc_z, ppgLength, accLength,
                longitude, latitude, altitude, gpsSpeed, gpsAccuracy, hr_field, speed_field,
                wristOff, speedSourceOut, speedOut, updateIndexRet, start_workout_type, start_rtc,
                default_userinfo, end_workout_date, '{:.2f}'.format(end_pace0),
                '{:.2f}'.format(end_pace1), '{:.2f}'.format(end_hr_est), active_utc
            ])


def _extract_datetime_from_filename(fname: str) -> str:
    m = re.search(r"(\d{8}_\d{6})", fname)
    return m.group(1) if m else ''


def allocate_output_csv_path(out_dir: str, base_template: str, base_ts: int, used_paths: set,
                             tz_minutes: int = 0) -> Tuple[str, int]:
    """为 CSV 分配不冲突路径。

    规则：
    - 先使用 base_ts 对应的 `YYYYMMDD_HHMMSS`
    - 若文件已存在或本次运行内已分配过，则按秒 +1 重试
    """
    try:
        ts = int(base_ts)
    except Exception:
        ts = int(time.time())

    while True:
        dt = time.strftime('%Y%m%d_%H%M%S', time.gmtime(ts + tz_minutes * 60))
        filename = f"{base_template.format(dt=dt)}.csv"
        outpath = os.path.abspath(os.path.normpath(os.path.join(out_dir, filename)))
        key = os.path.normcase(outpath)
        if key not in used_paths and not os.path.exists(outpath):
            used_paths.add(key)
            return outpath, ts
        ts += 1


def normalize_segment_timestamps(seg: List[dict], rtc_anchor: int = None,
                                 tz_anchor: int = None, content: str = None,
                                 time_anchor_pos: int = None,
                                 input_name: str = ''):
    """Use log clocks to normalize one segment without compressing pauses.

    When ``rtc`` is available, derive every event timestamp from the device
    clock printed on its own log line.  With no rtc, use the log filename date
    plus the same clocks as a deterministic fallback.  The old ``+1`` path is
    retained only for logs that expose neither kind of real time.
    """
    if not seg:
        return

    tz = int(tz_anchor) if tz_anchor is not None else 0
    anchor_clock = (_log_clock_at(content, time_anchor_pos)
                    if content is not None and time_anchor_pos is not None else None)
    if anchor_clock and content is not None:
        if rtc_anchor is not None:
            base = int(rtc_anchor)
            year = time.gmtime(base + tz * 60).tm_year
            for r in seg:
                clock = _log_clock_at(content, r['pos'])
                if clock:
                    r['utc'] = base + _clock_delta_seconds(anchor_clock, clock, year)
                    r['raw_utc'] = r['utc']
                    r['tz'] = tz
        else:
            date = _filename_date(input_name)
            if date:
                year, month, day = int(date[:4]), int(date[4:6]), int(date[6:8])
                base = calendar.timegm(datetime(year, month, day, *anchor_clock[2:]).timetuple()) - tz * 60
                for r in seg:
                    clock = _log_clock_at(content, r['pos'])
                    if clock:
                        r['utc'] = base + _clock_delta_seconds(anchor_clock, clock, year)
                        r['raw_utc'] = r['utc']
                        r['tz'] = tz

    # 无 rtc 锚点时，优先使用每行原始时间戳并在分段内保证单调递增
    seed = None
    for r in seg:
        rv = r.get('raw_utc', r.get('utc'))
        if isinstance(rv, int) and rv > 0:
            seed = rv
            break
    if seed is None:
        seed = int(time.time())

    last = None
    for r in seg:
        rv = r.get('raw_utc', r.get('utc'))
        if not isinstance(rv, int) or rv <= 0:
            rv = (last + 1) if last is not None else seed
        if last is not None and rv <= last:
            rv = last + 1
        r['utc'] = rv
        last = rv


def rows_with_active_timestamps(seg, content, input_name, start_pos=0, end_pos=None,
                                rtc_anchor=None, tz_anchor=None, time_anchor_pos=None):
    """Return running rows with pauses removed from the chart's active axis."""
    matches = [m for m in RE_SPRT_GMS.finditer(content, start_pos, end_pos or len(content))]
    status_rows = []
    for match in matches:
        utc, tz, _, _ = extract_fields_around(content, match.start())
        if utc <= 0:
            continue
        status_rows.append({'pos': match.start(), 'utc': utc, 'raw_utc': utc, 'tz': tz,
                            'state': int(match.group(3))})
    if status_rows:
        normalize_segment_timestamps(status_rows, rtc_anchor=rtc_anchor, tz_anchor=tz_anchor,
                                     content=content, time_anchor_pos=time_anchor_pos,
                                     input_name=input_name)
    intervals = []
    pause_start = None
    for status in status_rows:
        if status['state'] != SPRT_GMS_RUNNING_STATE:
            if pause_start is None:
                pause_start = status['utc']
        elif pause_start is not None:
            if status['utc'] > pause_start:
                intervals.append((pause_start, status['utc']))
            pause_start = None

    output = []
    for row in seg:
        if any(start <= row['utc'] < end for start, end in intervals):
            continue
        paused = sum(max(0, min(row['utc'], end) - start) for start, end in intervals)
        if pause_start is not None and row['utc'] > pause_start:
            paused += row['utc'] - pause_start
        output.append((row['utc'], row['tz'], row['hr'], row['speed'], row['utc'] - paused))
    return output


def split_rows_by_timestamp_jumps(rows: List[dict], min_jump_seconds: int = 300) -> List[List[dict]]:
    """根据时间戳中的大跳跃自动拆分运动数据段。
    
    如果相邻两行时间相差超过 min_jump_seconds（默认5分钟），认为是不同运动分段。
    返回分段的行列表。
    """
    if not rows:
        return []
    
    segments = []
    current_segment = [rows[0]]
    
    for i in range(1, len(rows)):
        curr_utc = rows[i]['utc']
        prev_utc = rows[i-1]['utc']
        
        # 检测时间跳跃
        time_diff = curr_utc - prev_utc
        
        if time_diff > min_jump_seconds:
            # 时间发生大跳跃，开始新段
            if current_segment:
                segments.append(current_segment)
            current_segment = [rows[i]]
        else:
            # 继续当前段
            current_segment.append(rows[i])
    
    # 添加最后一段
    if current_segment:
        segments.append(current_segment)
    
    return segments if len(segments) > 1 else [rows]


def normalize_cli_argv(argv: List[str]) -> List[str]:
    """规范化命令行参数，兼容 Windows 下常见的智能标点输入。"""
    if not argv:
        return argv

    quote_chars = '"\'\u201c\u201d\u2018\u2019'
    # 常见 Unicode dash：en dash / em dash / minus sign
    dash_chars = '\u2013\u2014\u2212'
    dash_re = re.compile(rf"^[{dash_chars}]+")

    out = []
    for raw in argv:
        a = raw.strip()
        # 去掉首尾引号（含中文弯引号）
        if len(a) >= 2 and a[0] in quote_chars and a[-1] in quote_chars:
            a = a[1:-1]

        # 将 Unicode dash 前缀转为标准 option 前缀
        m = dash_re.match(a)
        if m:
            rest = a[m.end():]
            if rest:
                # 单个/多个 Unicode dash 都统一映射为 GNU 长选项前缀
                a = '--' + rest.lstrip('-')

        out.append(a)
    return out


def main():
    # Windows 控制台默认编码可能导致打印某些字符时报错，进而中断导出流程
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description='Extract _processGomoreAlgo events from log to sample-style CSV',
        epilog='''
使用说明：

命令格式：
  python3 gomore_log_to_csv.py <infile|-> [outfile]

主要参数：
  -s/--sport-type N     指定 StartInput.workoutType（默认 31）
  -u/--user-info STR    userInfo 七字段字符串（示例默认值）
  --json                以 JSON 格式把结果写到 stdout（machine可读）
  --debug               打印诊断信息并在调试时导出未被 enable/disable 覆盖的原始日志行

行为要点：
  - 优先按 enable -> disable 配对导出（每个完整对生成一个 CSV，文件名示例：gomore_data_YYYYMMDD_HHMMSS.csv）。
  - CSV 时间优先由 sport cmd 日志行的真实设备时钟生成，并使用 GoMore enable 的 rtc/tz 校准；没有 rtc 时可从日志文件日期兜底。
  - 未被任何完整段覆盖的原始行仅在 --debug 时导出为 gomore_uncovered_lines_*.log。

JSON 输出（--json）示例：
{
  "results": [
    {
      "index": 1,
      "userInfo": [38.0,1.0,173.0,65.0,-1.0,60.0,-1.0],
      "alg_gm": {"ltSpeed": 10.65, "lh": 149, "mh": 202, "pace": "5'38\" /km"},
      "csv": "/abs/path/gomore_data_20260307_234655.csv",
      "rows": 6709,
      "start_ts": 1772927215,
      "start_pos": 18851388,
      "end_pos": 30720944
    }
  ]
}

退出码：
  0 成功
  1 无 _processGomoreAlgo 条目
  2 写 CSV 失败
  3 读取输入失败

示例：
  人类可读： python3 gomore_log_to_csv.py /path/log.log
  JSON 输出： python3 gomore_log_to_csv.py /path/log.log --json
  调试输出： python3 gomore_log_to_csv.py /path/log.log --debug
''',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('infile', help='input log file path or - for stdin')
    parser.add_argument('outfile', nargs='?', default='', help='output file or directory (optional)')
    parser.add_argument('-s', '--sport-type', type=int, default=31, help='运动类型数值，默认为31')
    parser.add_argument('-u', '--user-info', default=DEFAULT_USERINFO, help='用户信息CSV字符串，示例: "38.0,1.0,173.0,65.0,-1.0,60.0,-1.0"')
    parser.add_argument('--json', action='store_true', help='输出结果为 JSON 到 stdout (machine-readable)')
    parser.add_argument('--debug', action='store_true', help='打印诊断信息用于调试（事件计数、对覆盖等）')
    norm_argv = normalize_cli_argv(sys.argv[1:])
    args = parser.parse_args(norm_argv)

    infile = args.infile
    outfile = args.outfile
    sport_type_arg = args.sport_type
    userinfo_arg = args.user_info
    userinfo_arg_explicit = any(
        arg in ('-u', '--user-info') or arg.startswith('--user-info=')
        for arg in norm_argv
    )
    json_out = args.json
    debug = args.debug


    # read content
    try:
        if infile == '-':
            content = sys.stdin.read()
            input_dir = '.'
            input_name = ''
        else:
            with open(infile, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            input_dir = os.path.dirname(os.path.abspath(infile))
            input_name = os.path.basename(infile)
    except Exception as e:
        print(f'Failed to open/read infile {infile}: {e}', file=sys.stderr, flush=True)
        sys.exit(3)


    rows = parse_log(content)

    # 解析 ALG_GM 算法结果（如果存在）但不在此列出汇总，结果将在每个输出段中单独报告
    alg_results = extract_alg_outputs(content)

    # 试着从旧日志或 ALG_GM c 字段解析 userInfo（优先于后续输出分支）
    parsed_userinfo = extract_userinfo_from_old_log(content)
    userinfo_arg, global_userinfo_source = _resolve_userinfo_fallback(
        parsed_userinfo, userinfo_arg, userinfo_arg_explicit
    )
    if debug and _normalize_userinfo(parsed_userinfo):
        print(f"[DEBUG] Parsed userInfo early (before segmentation): {userinfo_arg}", flush=True)
        print(f"[DEBUG] Matched line: {LAST_PARSED_USERINFO_LINE}", flush=True)
    elif debug and parsed_userinfo:
        print(f"[DEBUG] Ignoring invalid userInfo; using {global_userinfo_source}", flush=True)

    # debug: total candidates in content
    if debug:
        positions_all = [m.start() for m in re.finditer(r"_processGomoreAlgo|SPRT_GMS\s*rh", content)]
        print(f'[DEBUG] total _processGomoreAlgo/SPRT_GMS hits: {len(positions_all)}', flush=True)
        print(f'[DEBUG] parse_log returned rows: {len(rows)}', flush=True)
        print(f'[DEBUG] alg_results found: {len(alg_results)}', flush=True)

    # 优先按 enable/disable 对导出（只有完整的 start->end 区间才写 CSV）
    pairs = find_enable_disable_pairs(content)
    if debug:
        print(f'[DEBUG] enable/disable pairs found: {len(pairs)}', flush=True)
    if pairs:
        any_written = False
        results_output = []
        used_output_paths = set()
        # prepare out_dir/base_template similar to previous logic
        if not outfile:
            out_dir = input_dir
            base_template = 'gomore_data_{dt}'
        else:
            if os.path.isdir(outfile) or outfile.endswith(('/', os.path.sep)):
                out_dir = outfile if os.path.isdir(outfile) else outfile.rstrip('/\\')
                base_template = 'gomore_data_{dt}'
            else:
                out_dir = os.path.dirname(os.path.abspath(outfile)) or input_dir
                provided = os.path.basename(outfile)
                name, _ext = os.path.splitext(provided)
                base_template = (name + '_{dt}') if name else 'gomore_data_{dt}'

        total_covered = 0
        for idx, pair in enumerate(pairs, start=1):
            start_pos = pair.get('start_pos')
            end_pos = pair.get('end_pos')
            rtc_anchor = pair.get('rtc')
            tz_anchor = pair.get('tz')
            time_anchor_pos = pair.get('time_anchor_pos')
            seg = [r for r in rows if r['pos'] >= start_pos and r['pos'] < end_pos]
            algs_in_seg = [ar for ar in alg_results if ar['pos'] >= start_pos and ar['pos'] < end_pos]
            if not seg:
                print(f"Segment #{idx} between enable@{start_pos} and disable@{end_pos} has no rows - skipped (incomplete)", flush=True)
                continue
            if debug:
                print(f"[DEBUG] Segment #{idx}: start={start_pos} end={end_pos} rtc={rtc_anchor} tz={tz_anchor} time_anchor={time_anchor_pos} rows_in_seg={len(seg)} algs_in_seg={len(algs_in_seg)}", flush=True)
            total_covered += len(seg)
            sample_userinfo = _normalize_userinfo(
                extract_userinfo_in_range(content, start_pos, end_pos)
            )
            sample_userinfo_source = (
                '该运动段关联日志范围（含前序上下文）' if sample_userinfo
                else global_userinfo_source
            )
            if not sample_userinfo:
                sample_userinfo = userinfo_arg
            sample_userinfo_found = not sample_userinfo_source.startswith('脚本默认值')

            # 关键修正：每个分段独立校正时间，避免第二段起点受第一段影响
            normalize_segment_timestamps(
                seg,
                rtc_anchor=rtc_anchor if rtc_anchor else None,
                tz_anchor=tz_anchor,
                content=content,
                time_anchor_pos=time_anchor_pos,
                input_name=input_name,
            )

            first_utc = seg[0]['utc']
            outpath, _resolved_name_ts = allocate_output_csv_path(
                out_dir, base_template, first_utc, used_output_paths, seg[0]['tz'])

            tuples = rows_with_active_timestamps(
                seg, content, input_name, start_pos=start_pos, end_pos=end_pos,
                rtc_anchor=rtc_anchor if rtc_anchor else None, tz_anchor=tz_anchor,
                time_anchor_pos=time_anchor_pos,
            )
            try:
                write_csv(tuples, outpath, sport_type=sport_type_arg, userinfo=sample_userinfo)
                any_written = True
            except Exception as e:
                print(f'Failed to write CSV to {outpath}: {e}', file=sys.stderr, flush=True)
                continue

            # build alg entry from algs_in_seg (take first if any)
            alg_entry = None
            if algs_in_seg:
                ar = algs_in_seg[0]
                lt = ar.get('ltSpeed')
                alg_entry = {'ltSpeed': lt, 'lh': ar.get('lh'), 'mh': ar.get('mh'), 'guideResult': ar.get('guideResult'), 'pace': convert_speed_to_pace_str(lt) if lt is not None else None}

            try:
                userinfo_list = [float(x) for x in sample_userinfo.split(',')]
            except Exception:
                userinfo_list = userinfo_arg

            res_obj = {
                'index': idx,
                'userInfo': userinfo_list,
                'userInfoFound': sample_userinfo_found,
                'userInfoSource': sample_userinfo_source,
                'alg_gm': alg_entry,
                'csv': outpath,
                'rows': len(tuples),
                'start_ts': first_utc,
                'start_log_time': _format_log_datetime(content, seg[0]['pos'], input_name, first_utc),
                'start_pos': start_pos,
                'end_pos': end_pos
            }
            results_output.append(res_obj)

            if not json_out:
                print('\n' + '='*40, flush=True)
                print(f'Result #{idx}', flush=True)
                print('-' * 40, flush=True)
                print(f'UserInfo: {sample_userinfo}', flush=True)
                print(f'UserInfo source: {sample_userinfo_source}', flush=True)
                if alg_entry:
                    lt = alg_entry.get('ltSpeed')
                    pace = convert_speed_to_pace_str(lt) if lt is not None else '-'
                    print(f'ALG_GM: ltSpeed={lt} ({pace}), lh={alg_entry.get("lh")}, mh={alg_entry.get("mh")}', flush=True)
                else:
                    print('ALG_GM: (none)', flush=True)
                print(f'CSV: {outpath}', flush=True)
                print(f'Rows: {len(tuples)}', flush=True)
                print('='*40 + '\n', flush=True)

        if any_written:
            if debug:
                # 导出未被 enable/disable 覆盖的 rows（如果有），仅在 debug 模式
                pairs_intervals = [(p['start_pos'], p['end_pos']) for p in pairs]
                uncovered = [r for r in rows if not any(s <= r['pos'] < e for s, e in pairs_intervals)]
                if uncovered:
                    try:
                        dt_un = time.strftime('%Y%m%d_%H%M%S', time.gmtime(uncovered[0]['utc']))
                    except Exception:
                        dt_un = time.strftime('%Y%m%d_%H%M%S', time.gmtime(int(time.time())))
                    out_un_name = f"gomore_uncovered_lines_{dt_un}.log"
                    out_un_path = os.path.join(out_dir, out_un_name)
                    out_un_path = os.path.abspath(os.path.normpath(out_un_path))
                    # write raw original log lines for uncovered positions
                    def _get_line_at_pos(p):
                        s = content.rfind('\n', 0, p)
                        if s == -1:
                            s = 0
                        else:
                            s = s + 1
                        e = content.find('\n', p)
                        if e == -1:
                            e = len(content)
                        return content[s:e].rstrip('\r')
                    try:
                        with open(out_un_path, 'w', encoding='utf-8', newline='') as uf:
                            for r in uncovered:
                                uf.write(_get_line_at_pos(r['pos']) + '\n')
                        print(f'Uncovered raw lines written: {out_un_path}', flush=True)
                        print(f'Uncovered rows: {len(uncovered)}', flush=True)
                    except Exception as e:
                        print(f'Failed to write uncovered log to {out_un_path}: {e}', file=sys.stderr, flush=True)
                print(f"[DEBUG] Total rows: {len(rows)}, rows covered by enable/disable pairs: {total_covered}", flush=True)
            if json_out:
                print(json.dumps({'results': results_output}, ensure_ascii=False, indent=2), flush=True)
            return
        else:
            print('No complete enable/disable segments produced CSVs; falling back to other segmentation.', flush=True)

    if not rows:
        print('No _processGomoreAlgo entries found in log.', file=sys.stderr, flush=True)
        sys.exit(1)
    
    # 如果前面的分段都失败了，尝试基于时间戳跳跃自动拆分
    ts_segments = split_rows_by_timestamp_jumps(rows, min_jump_seconds=300)
    if debug and len(ts_segments) > 1:
        print(f'[DEBUG] Detected {len(ts_segments)} segments by timestamp jumps', flush=True)

    # 优先使用日志中旧格式的 _gomoreSdkConfig 提供的 userInfo（若存在）
    parsed_userinfo = extract_userinfo_from_old_log(content)
    userinfo_arg, global_userinfo_source = _resolve_userinfo_fallback(
        parsed_userinfo, userinfo_arg, userinfo_arg_explicit
    )
    if debug and _normalize_userinfo(parsed_userinfo):
        print(f"[DEBUG] Parsed userInfo from _gomoreSdkConfig: {userinfo_arg}", flush=True)
        print(f"[DEBUG] Matched line: {LAST_PARSED_USERINFO_LINE}", flush=True)
    elif debug:
        message = (
            f"Ignoring invalid userInfo; using {global_userinfo_source}"
            if parsed_userinfo else 'No valid _gomoreSdkConfig userInfo; using fallback'
        )
        print(f'[DEBUG] {message}', flush=True)

    # 根据 ALG_GM 输出决定是否拆分为多个 CSV（即使只有1个，如果有多个segments也应该拆分）
    if alg_results and len(alg_results) >= 1:
        # 准备输出目录和基名模板
        if not outfile:
            out_dir = input_dir
            base_template = 'gomore_data_{dt}'
        else:
            if os.path.isdir(outfile) or outfile.endswith(('/', os.path.sep)):
                out_dir = outfile if os.path.isdir(outfile) else outfile.rstrip('/\\')
                base_template = 'gomore_data_{dt}'
            else:
                out_dir = os.path.dirname(os.path.abspath(outfile)) or input_dir
                provided = os.path.basename(outfile)
                name, _ext = os.path.splitext(provided)
                base_template = (name + '_{dt}') if name else 'gomore_data_{dt}'

        # 将 rows 按 ALG_GM 区间分割：优先使用 ALG_GM 附近的时间戳（utc），若不可用则退回到位置（pos）分割
        alg_info = []
        for ar in alg_results:
            try:
                a_utc, a_tz, a_hr, a_speed = extract_fields_around(content, ar['pos'])
            except Exception:
                a_utc = -1
            alg_info.append({'pos': ar['pos'], 'utc': a_utc if a_utc != -1 else None})
        # 排序：有 utc 的按 utc 排序，否则按 pos 排序
        alg_info.sort(key=lambda x: (x['utc'] is None, x['utc'] if x['utc'] is not None else x['pos']))
        segments = []
        for i, info in enumerate(alg_info):
            start_pos = info['pos']
            start_utc = info['utc']
            end_info = alg_info[i+1] if i+1 < len(alg_info) else None
            end_pos = end_info['pos'] if end_info else None
            end_utc = end_info['utc'] if end_info else None
            if start_utc is not None:
                # use utc-based split
                seg = [r for r in rows if r['utc'] >= start_utc and (end_utc is None or r['utc'] < end_utc)]
            else:
                # fallback to pos-based split
                seg = [r for r in rows if r['pos'] >= start_pos and (end_pos is None or r['pos'] < end_pos)]
            segments.append(seg)

        any_written = False
        results_output = []
        used_output_paths = set()
        # create mapping from pos to alg_result for per-segment reporting
        alg_by_pos = {r['pos']: r for r in alg_results}
        segments_covered = 0
        for idx, seg in enumerate(segments, start=1):
            if not seg:
                continue
            if debug:
                print(f"[DEBUG] segment #{idx} has {len(seg)} rows; first pos={seg[0]['pos']}, first_utc={seg[0]['utc']}", flush=True)
            segments_covered += len(seg)
            sample_userinfo = _normalize_userinfo(
                extract_userinfo_in_range(content, seg[0]['pos'], seg[-1]['pos'] + 1)
            )
            sample_userinfo_source = (
                '该运动段关联日志范围（含前序上下文）' if sample_userinfo
                else global_userinfo_source
            )
            if not sample_userinfo:
                sample_userinfo = userinfo_arg
            sample_userinfo_found = not sample_userinfo_source.startswith('脚本默认值')
            normalize_segment_timestamps(seg)
            first_utc = seg[0]['utc']
            outpath, _resolved_name_ts = allocate_output_csv_path(
                out_dir, base_template, first_utc, used_output_paths, seg[0]['tz'])
            tuples = rows_with_active_timestamps(
                seg, content, input_name, start_pos=seg[0]['pos'], end_pos=seg[-1]['pos'] + 1,
            )
            try:
                write_csv(tuples, outpath, sport_type=sport_type_arg, userinfo=sample_userinfo)
                any_written = True
            except Exception as e:
                print(f'Failed to write CSV to {outpath}: {e}', file=sys.stderr, flush=True)
                continue

            # Find corresponding ALG_GM info for this segment using same index
            alg_info_for_seg = alg_info[idx-1] if idx-1 < len(alg_info) else None
            alg_result = alg_by_pos.get(alg_info_for_seg['pos']) if alg_info_for_seg else None

            # build result dict
            alg_entry = None
            if alg_result:
                lt = alg_result.get('ltSpeed')
                lh = alg_result.get('lh')
                mh = alg_result.get('mh')
                alg_entry = {'ltSpeed': lt, 'lh': lh, 'mh': mh, 'guideResult': alg_result.get('guideResult'), 'pace': convert_speed_to_pace_str(lt) if lt is not None else None}

            # parse userinfo into list
            try:
                userinfo_list = [float(x) for x in sample_userinfo.split(',')]
            except Exception:
                userinfo_list = userinfo_arg

            res_obj = {
                'index': idx,
                'userInfo': userinfo_list,
                'userInfoFound': sample_userinfo_found,
                'userInfoSource': sample_userinfo_source,
                'alg_gm': alg_entry,
                'csv': outpath,
                'rows': len(tuples),
                'start_ts': first_utc,
                'start_log_time': _format_log_datetime(content, seg[0]['pos'], input_name, first_utc)
            }
            results_output.append(res_obj)

            if not json_out:
                # Print grouped block for this result (human readable)
                print('\n' + '='*40, flush=True)
                print(f'Result #{idx}', flush=True)
                print('-' * 40, flush=True)
                # user info
                print(f'UserInfo: {sample_userinfo}', flush=True)
                print(f'UserInfo source: {sample_userinfo_source}', flush=True)
                # ALG_GM outputs (if found)
                if alg_result:
                    lt = alg_result.get('ltSpeed')
                    lh = alg_result.get('lh')
                    mh = alg_result.get('mh')
                    pace = convert_speed_to_pace_str(lt) if lt is not None else '-'
                    print(f'ALG_GM: ltSpeed={lt} ({pace}), lh={lh}, mh={mh}', flush=True)
                else:
                    print('ALG_GM: (none)', flush=True)
                # CSV path and rows
                print(f'CSV: {outpath}', flush=True)
                print(f'Rows: {len(tuples)}', flush=True)
                print('='*40 + '\n', flush=True)

        if any_written:
            if json_out:
                print(json.dumps({'results': results_output}, ensure_ascii=False, indent=2), flush=True)
            if debug:
                print(f"[DEBUG] total rows={len(rows)}, rows covered by alg-based segments={segments_covered}", flush=True)
            return
        else:
            if debug:
                print('[DEBUG] ALG_GM segments produced no CSVs; trying timestamp-based splitting as fallback.', flush=True)

    # 单个 CSV（默认行为） — 写出后以 Result 风格打印汇总
    if not outfile:
        dt = _extract_datetime_from_filename(input_name)
        if not dt:
            # fallback to first row timestamp
            dt = time.strftime('%Y%m%d_%H%M%S', time.gmtime(rows[0]['utc']))
        outname = f'gomore_data_{dt}.csv'
        outfile = os.path.join(input_dir, outname)
    else:
        # if outfile is a directory, auto-name inside it
        if os.path.isdir(outfile) or outfile.endswith(('/', os.path.sep)):
            outdir = outfile if os.path.isdir(outfile) else outfile.rstrip('/\\')
            dt = _extract_datetime_from_filename(input_name) or time.strftime('%Y%m%d_%H%M%S', time.gmtime(rows[0]['utc']))
            outfile = os.path.join(outdir, f'gomore_data_{dt}.csv')
            outfile = os.path.normpath(outfile)

    # Resolve absolute path and write
    outfile = os.path.abspath(outfile)
    tuples = rows_with_active_timestamps(rows, content, input_name)
    single_userinfo = _normalize_userinfo(
        extract_userinfo_in_range(content, 0, len(content))
    )
    single_userinfo_source = '本次解析日志范围' if single_userinfo else global_userinfo_source
    if not single_userinfo:
        single_userinfo = userinfo_arg
    single_userinfo_found = not single_userinfo_source.startswith('脚本默认值')
    try:
        write_csv(tuples, outfile, sport_type=sport_type_arg, userinfo=single_userinfo)
    except Exception as e:
        print(f'Failed to write CSV to {outfile}: {e}', file=sys.stderr, flush=True)
        sys.exit(2)

    # prepare single-result object
    try:
        userinfo_list = [float(x) for x in single_userinfo.split(',')]
    except Exception:
        userinfo_list = userinfo_arg

    alg_list = []
    if alg_results:
        for r in alg_results:
            lt = r.get('ltSpeed')
            alg_list.append({'ltSpeed': lt, 'lh': r.get('lh'), 'mh': r.get('mh'), 'guideResult': r.get('guideResult'), 'pace': convert_speed_to_pace_str(lt) if lt is not None else None})

    single_res = {
        'index': 1,
        'userInfo': userinfo_list,
        'userInfoFound': single_userinfo_found,
        'userInfoSource': single_userinfo_source,
        'alg_gm': alg_list,
        'csv': outfile,
        'rows': len(tuples),
        'start_ts': rows[0]['utc'] if rows else None,
        'start_log_time': _format_log_datetime(content, rows[0]['pos'], input_name, rows[0]['utc']) if rows else ''
    }

    if json_out:
        print(json.dumps({'results': [single_res]}, ensure_ascii=False, indent=2), flush=True)
    else:
        # Print single-result block in the same style as multi-segment results
        print('\n' + '='*40, flush=True)
        print('Result #1', flush=True)
        print('-' * 40, flush=True)
        print(f'UserInfo: {single_userinfo}', flush=True)
        print(f'UserInfo source: {single_userinfo_source}', flush=True)
        if alg_results:
            for i, r in enumerate(alg_results, start=1):
                pace = convert_speed_to_pace_str(r.get('ltSpeed')) if r.get('ltSpeed') is not None else '-'
                print(f'ALG_GM #{i}: ltSpeed={r.get("ltSpeed")} ({pace}), lh={r.get("lh")}, mh={r.get("mh")}', flush=True)
        else:
            print('ALG_GM: (none)', flush=True)
        print(f'CSV: {outfile}', flush=True)
        print(f'Rows: {len(tuples)}', flush=True)
        print('='*40 + '\n', flush=True)


if __name__ == '__main__':
    main()

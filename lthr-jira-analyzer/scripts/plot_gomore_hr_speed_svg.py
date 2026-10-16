#!/usr/bin/env python3
"""Generate a GoMore HR/SPEED SVG chart using only the Python standard library."""

import argparse
import csv
from datetime import datetime, timedelta
import html
import math
import os


PIXELS_PER_5_MINUTES, HEIGHT = 250, 780
LEFT, RIGHT, TOP, BOTTOM = 88, 110, 82, 115


def load_data(path):
    points = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"TIMESTAMP", "HR", "SPEED"}
        if reader.fieldnames is None or required - set(reader.fieldnames):
            raise ValueError("CSV must contain TIMESTAMP, HR and SPEED")
        for row in reader:
            ts = int(float(row["TIMESTAMP"]))
            tz = int(float(row.get("timeZoneOffset") or 0))
            local = datetime.utcfromtimestamp(ts) + timedelta(minutes=tz)
            hr = float(row["HR"])
            speed = float(row["SPEED"])
            # 255 is the sensor's invalid-HR sentinel; plotting it as a real
            # value creates a spike and expands the HR axis for the whole chart.
            points.append((ts, local, hr if 0 <= hr < 255 else None, speed if speed >= 0 else None))
    if not points:
        raise ValueError("CSV contains no data rows")
    return points


def split_polyline(points, value_index, map_x, map_y):
    chunks, current = [], []
    for point in points:
        value = point[value_index]
        if value is None:
            if current:
                chunks.append(current)
                current = []
            continue
        current.append(f"{map_x(point[0]):.1f},{map_y(value):.1f}")
    if current:
        chunks.append(current)
    return chunks


def split_area(points, value_index, map_x, map_y, baseline_y):
    areas, current = [], []

    def append_area(segment):
        if not segment:
            return
        coords = [(map_x(point[0]), map_y(point[value_index])) for point in segment]
        path = [f"M {coords[0][0]:.1f} {baseline_y:.1f}", f"L {coords[0][0]:.1f} {coords[0][1]:.1f}"]
        path.extend(f"L {x:.1f} {y:.1f}" for x, y in coords[1:])
        path.extend((f"L {coords[-1][0]:.1f} {baseline_y:.1f}", "Z"))
        areas.append(" ".join(path))

    for point in points:
        if point[value_index] is None:
            append_area(current)
            current = []
        else:
            current.append(point)
    append_area(current)
    return areas


def render(csv_path, output):
    points = load_data(csv_path)
    ts0, ts1 = points[0][0], points[-1][0]
    if ts1 <= ts0:
        ts1 = ts0 + 1
    duration = ts1 - ts0
    plot_w = duration * PIXELS_PER_5_MINUTES / 300
    plot_h = HEIGHT - TOP - BOTTOM
    svg_width = math.ceil(LEFT + plot_w + RIGHT)
    plot_right = LEFT + plot_w
    hr_values = [p[2] for p in points if p[2] is not None]
    speed_values = [p[3] for p in points if p[3] is not None]
    hr_top = max(100.0, math.ceil(max(hr_values, default=0.0) * 1.10 / 10) * 10)
    speed_top = max(1.0, math.ceil(max(speed_values, default=0.0) * 1.10 * 2) / 2)
    map_x = lambda ts: LEFT + (ts - ts0) * plot_w / (ts1 - ts0)
    map_hr = lambda value: TOP + plot_h * (1.0 - value / hr_top)
    map_speed = lambda value: TOP + plot_h * (1.0 - value / speed_top)

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_width}" height="{HEIGHT}" viewBox="0 0 {svg_width} {HEIGHT}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text{font-family:Arial,sans-serif;font-size:13px;fill:#333}.small{font-size:12px;fill:#666}</style>',
        f'<text x="{LEFT}" y="28" font-size="18">{html.escape(os.path.basename(csv_path))}</text>',
        f'<text class="small" x="{LEFT}" y="47">{points[0][1]:%Y-%m-%d %H:%M:%S} ~ {points[-1][1]:%Y-%m-%d %H:%M:%S}</text>',
    ]
    for i in range(6):
        ratio = i / 5
        y = TOP + plot_h * ratio
        hr_label = hr_top * (1 - ratio)
        speed_tick = speed_top * (1 - ratio)
        if speed_tick > 0:
            pace_seconds = int(round(3600 / speed_tick))
            speed_label = f"{pace_seconds // 60}′{pace_seconds % 60:02d}″"
        else:
            speed_label = "—"
        lines += [
            f'<line x1="{LEFT}" y1="{y:.1f}" x2="{plot_right:.1f}" y2="{y:.1f}" stroke="#ddd" stroke-dasharray="3,3"/>',
            f'<text x="{LEFT - 9}" y="{y + 4:.1f}" text-anchor="end" fill="#d62728">{hr_label:.0f}</text>',
            f'<text x="{plot_right + 9:.1f}" y="{y + 4:.1f}" fill="#1f77b4">{speed_label}</text>',
        ]
    lines += [
        f'<line x1="{LEFT}" y1="{TOP}" x2="{LEFT}" y2="{TOP + plot_h}" stroke="#d62728"/>',
        f'<line x1="{plot_right:.1f}" y1="{TOP}" x2="{plot_right:.1f}" y2="{TOP + plot_h}" stroke="#1f77b4"/>',
        f'<text x="{LEFT}" y="{TOP - 8}" fill="#d62728">HR (bpm)</text>',
        f'<text x="{plot_right:.1f}" y="{TOP - 8}" fill="#1f77b4">Pace (min/km)</text>',
        f'<text class="small" x="{LEFT + plot_w / 2:.1f}" y="{TOP - 8}" text-anchor="middle">HR / Pace comparison (all samples)</text>',
    ]
    tick_times = list(range(0, duration + 1, 300))
    if not tick_times or tick_times[-1] != duration:
        tick_times.append(duration)
    for elapsed in tick_times:
        x = map_x(ts0 + elapsed)
        elapsed_minutes = int(round(elapsed / 60))
        duration_label = f"{elapsed_minutes // 60}:{elapsed_minutes % 60:02d}"
        lines += [
            f'<line x1="{x:.1f}" y1="{TOP + plot_h}" x2="{x:.1f}" y2="{TOP + plot_h + 5}" stroke="#777"/>',
            f'<text class="small" x="{x:.1f}" y="{TOP + plot_h + 23}" text-anchor="middle">{duration_label}</text>',
        ]
    lines.append(f'<text class="small" x="{LEFT + plot_w / 2:.1f}" y="{HEIGHT - 45}" text-anchor="middle">Duration (h:mm)</text>')
    series = (("#d62728", 2, map_hr), ("#1f77b4", 3, map_speed))
    for color, value_index, mapper in series:
        for area in split_area(points, value_index, map_x, mapper, TOP + plot_h):
            lines.append(f'<path d="{area}" fill="{color}" fill-opacity="0.10"/>')
    for color, value_index, mapper in series:
        for chunk in split_polyline(points, value_index, map_x, mapper):
            lines.append(f'<polyline points="{" ".join(chunk)}" fill="none" stroke="{color}" stroke-width="2.0" stroke-linecap="round" stroke-linejoin="round"/>')
    invalid_hr = sum(p[2] is None for p in points)
    invalid_speed = sum(p[3] is None for p in points)
    # Count the sentinel in the source data even though it is excluded from the curve.
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        hr_255 = sum(float(row["HR"]) == 255 for row in csv.DictReader(f))
    lines += [
        f'<text class="small" x="{LEFT}" y="{HEIGHT - 25}">rows={len(points)}; HR invalid={invalid_hr}; HR=255={hr_255}; Speed invalid={invalid_speed}</text>',
        '</svg>',
    ]
    with open(output, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print(output)


def main():
    parser = argparse.ArgumentParser(description="Generate GoMore HR/SPEED SVG")
    parser.add_argument("csv")
    parser.add_argument("-o", "--output")
    args = parser.parse_args()
    output = args.output or os.path.splitext(args.csv)[0] + "_hr_speed.svg"
    render(args.csv, output)


if __name__ == "__main__":
    main()

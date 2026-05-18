from __future__ import annotations

from collections import Counter
from typing import Dict, List, Tuple


def summarize_stability(results: List[Dict[str, object]]) -> str:
    ok_results = [r for r in results if r.get("status") == "ok" and "ltSpeed" in r]
    if not ok_results:
        return "无有效仿真结果"
    total_runs = len(results)
    signatures = Counter(
        (
            f"{float(r['ltSpeed']):.6f}",
            str(r["lh"]),
            str(r["mh"]),
            str(r["guideResult"]),
        )
        for r in ok_results
    )
    top_signature, top_count = signatures.most_common(1)[0]
    if len(signatures) == 1:
        return (
            f"{total_runs} 次结果一致: ltSpeed={top_signature[0]} lh={top_signature[1]} "
            f"mh={top_signature[2]} guideResult={top_signature[3]}"
        )
    return (
        f"{total_runs} 次仿真存在 {len(signatures)} 组结果，"
        f"最常见组合出现 {top_count}/{len(ok_results)} 次: "
        f"ltSpeed={top_signature[0]} lh={top_signature[1]} mh={top_signature[2]} guideResult={top_signature[3]}"
    )


def _calc_linear_trend(values: List[float]) -> Tuple[float, str]:
    n = len(values)
    if n < 3:
        return 0.0, "数据点不足（<3）"
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    num = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    den = sum((i - mean_x) ** 2 for i in range(n))
    slope = num / den if den != 0 else 0.0
    if abs(slope) < 1e-8:
        return slope, "持平"
    if slope > 0:
        return slope, "缓慢上升" if slope < 0.02 else "明显上升"
    return slope, "缓慢下降" if slope > -0.02 else "明显下降"


def analyze_trend(results: List[Dict[str, object]]) -> Dict[str, object]:
    ok_results = [r for r in results if r.get("status") == "ok" and "ltSpeed" in r]
    trend_info: Dict[str, object] = {
        "valid_runs": len(ok_results),
        "total_runs": len(results),
    }
    if len(ok_results) < 2:
        trend_info["note"] = "有效结果不足2次，无法分析趋势"
        return trend_info

    lt_speeds = [float(r["ltSpeed"]) for r in ok_results]
    mhs = [int(r["mh"]) for r in ok_results]
    lhs = [int(r["lh"]) for r in ok_results]
    guide_results = [int(r["guideResult"]) for r in ok_results]

    def _stats(values: List[float]) -> Dict[str, float]:
        n = len(values)
        mean = sum(values) / n
        var = sum((v - mean) ** 2 for v in values) / n
        std = var ** 0.5
        return {
            "mean": round(mean, 6),
            "std": round(std, 6),
            "min": round(min(values), 6),
            "max": round(max(values), 6),
            "range": round(max(values) - min(values), 6),
            "cv": round(std / mean, 6) if mean != 0 else 0.0,
        }

    lt_stats = _stats(lt_speeds)
    lt_slope, lt_trend = _calc_linear_trend(lt_speeds)
    lt_stats["slope"] = round(lt_slope, 6)
    lt_stats["trend"] = lt_trend

    mh_stats = _stats([float(v) for v in mhs])
    mh_slope, mh_trend = _calc_linear_trend([float(v) for v in mhs])
    mh_stats["slope"] = round(mh_slope, 6)
    mh_stats["trend"] = mh_trend

    lh_stats = _stats([float(v) for v in lhs])

    trend_info["ltSpeed"] = lt_stats
    trend_info["mh"] = mh_stats
    trend_info["lh"] = lh_stats
    trend_info["guideResult_unique_count"] = len(set(guide_results))

    trend_info["conclusion"] = "\n".join(
        [
            f"ltSpeed 均值={lt_stats['mean']}, 标准差={lt_stats['std']}, CV={lt_stats['cv']}, 趋势: {lt_trend}",
            "ltSpeed 序列: [" + ", ".join(f"{v:.6f}" for v in lt_speeds) + "]",
            f"mh 均值={mh_stats['mean']:.1f}, 范围=[{mh_stats['min']:.0f}~{mh_stats['max']:.0f}], 趋势: {mh_trend}",
            "mh 序列: [" + ", ".join(str(v) for v in mhs) + "]",
        ]
    )
    return trend_info


__all__ = [
    "analyze_trend",
    "summarize_stability",
]

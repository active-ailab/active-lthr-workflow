from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from shared.data_analyzer import analyze_trend, summarize_stability  # noqa: E402


class DataAnalyzerTests(unittest.TestCase):
    def test_summarize_stability_reports_consistent_runs(self) -> None:
        results = [
            {"status": "ok", "ltSpeed": 1.23, "lh": 150, "mh": 160, "guideResult": 1},
            {"status": "ok", "ltSpeed": 1.23, "lh": 150, "mh": 160, "guideResult": 1},
        ]
        summary = summarize_stability(results)
        self.assertIn("2 次结果一致", summary)
        self.assertIn("ltSpeed=1.230000", summary)

    def test_analyze_trend_builds_stats_for_valid_runs(self) -> None:
        results = [
            {"status": "ok", "ltSpeed": 1.00, "lh": 150, "mh": 160, "guideResult": 1},
            {"status": "ok", "ltSpeed": 1.01, "lh": 151, "mh": 161, "guideResult": 1},
            {"status": "ok", "ltSpeed": 1.03, "lh": 152, "mh": 163, "guideResult": 1},
        ]
        trend = analyze_trend(results)
        self.assertEqual(trend["valid_runs"], 3)
        self.assertEqual(trend["ltSpeed"]["trend"], "缓慢上升")
        self.assertEqual(trend["guideResult_unique_count"], 1)
        self.assertIn("ltSpeed 序列", trend["conclusion"])

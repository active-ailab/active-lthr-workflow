from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import base_steps  # noqa: E402


class BaseStepsTests(unittest.TestCase):
    def test_merge_template_records_preserves_template_order_and_baselines(self) -> None:
        template_records = {
            "01lyh": {"样本": "01lyh", "Garmin_LTHR": 165, "DUT_LTHR": 170},
            "02lzh": {"样本": "02lzh", "Garmin_LTHR": 160, "DUT_LTHR": 161},
        }
        new_records = {
            "1lyh": {"仿真1_LTHR": 170, "仿真2_LTHR": 171},
            "02lzh": {"仿真1_LTHR": 161, "仿真2_LTHR": 161},
        }

        merged = base_steps._merge_template_records(template_records, new_records)

        self.assertEqual(list(merged.keys()), ["01lyh", "02lzh"])
        self.assertEqual(merged["01lyh"]["样本"], "01lyh")
        self.assertEqual(merged["01lyh"]["Garmin_LTHR"], 165)
        self.assertEqual(merged["01lyh"]["DUT_LTHR"], 170)
        self.assertEqual(merged["01lyh"]["仿真1_LTHR"], 170)
        self.assertEqual(merged["01lyh"]["仿真2_LTHR"], 171)
        self.assertEqual(merged["02lzh"]["Garmin_LTHR"], 160)
        self.assertEqual(merged["02lzh"]["仿真1_LTHR"], 161)


if __name__ == "__main__":
    unittest.main()
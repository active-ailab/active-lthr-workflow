from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import parse_gomore_samples  # noqa: E402


class ParseGomoreSamplesTests(unittest.TestCase):
    def test_parse_log_tracks_rounds_from_round_fraction_marker(self) -> None:
        log_text = textwrap.dedent(
            """
            I>0.100 GOMORE_C utest ========== SAMPLE[1/1] CASE=BASE_CASE SAMPLE_DIR=01lyh FILE=gomore_data.csv ==========
            I>0.101 GOMORE_C utest   Round 1/3 start
            I>0.102 GOMORE_C utest User: age=26, gender=0, height=180.0, weight=72.0, hrMax=0.0, hrRest=60.0, vo2Max=0.0
            I>0.103 ALG_GM utest Gomore output ltSpeed=10.150821 lh=170 mh=188 guideResult=0
            I>0.104 HEA_COM HealthSe Get Data len=4320 ,m_actDataLen=4320, m_actDataIndex=4319
            I>0.105 GOMORE_C utest   Round 2/3 start
            I>0.106 ALG_GM utest Gomore output ltSpeed=10.135679 lh=171 mh=189 guideResult=0
            I>0.107 GOMORE_C utest   Round 3/3 start
            I>0.108 ALG_GM utest Gomore output ltSpeed=10.120000 lh=172 mh=190 guideResult=0
            """
        ).strip()

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "multi.log"
            log_path.write_text(log_text, encoding="utf-8")

            samples = parse_gomore_samples.parse_log(str(log_path), case_filter="BASE_CASE")

        self.assertIn("01lyh", samples)
        record = samples["01lyh"]
        self.assertEqual(record["仿真1_LTHR"], 170)
        self.assertEqual(record["仿真2_LTHR"], 171)
        self.assertEqual(record["仿真3_LTHR"], 172)
        self.assertEqual(record["性别"], "男")
        self.assertNotIn("仿真4319_LTHR", record)
        self.assertEqual(record["仿真1_LTP"], "5'55")
        self.assertEqual(record["仿真1_LTP（S）"], 355)
        self.assertEqual(record["仿真2_LTP"], "5'55")
        self.assertEqual(record["仿真2_LTP（S）"], 355)
        self.assertEqual(record["仿真3_LTP"], "5'56")
        self.assertEqual(record["仿真3_LTP（S）"], 356)
        self.assertEqual(record["仿真3_MAXHR"], 190)

    def test_parse_log_keeps_invalid_round_status_for_failed_output(self) -> None:
        log_text = textwrap.dedent(
            """
            I>0.100 GOMORE_C utest ========== SAMPLE[1/1] CASE=JIRA_CASE SAMPLE_DIR=WIN-2552 FILE=gomore_data.csv ==========
            I>0.101 GOMORE_C utest   Round 1/3 start
            I>0.102 ALG_GM utest Gomore output ltSpeed=-1.000000 lh=0 mh=0 guideResult=0
            I>0.103 GOMORE_C utest   Round 2/3 start
            I>0.104 ALG_GM utest Gomore output ltSpeed=-1.000000 lh=0 mh=0 guideResult=0
            I>0.105 GOMORE_C utest   Round 3/3 start
            I>0.106 ALG_GM utest Gomore output ltSpeed=-1.000000 lh=0 mh=0 guideResult=0
            """
        ).strip()

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "invalid.log"
            log_path.write_text(log_text, encoding="utf-8")

            samples = parse_gomore_samples.parse_log(str(log_path), case_filter="JIRA_CASE")

        record = samples["WIN-2552/gomore_data"]
        self.assertIsNone(record["仿真1_LTHR"])
        self.assertIsNone(record["仿真1_LTP"])
        self.assertEqual(record["仿真1_状态"], "invalid_result: ltSpeed=-1.000, lh=0, mh=0")
        self.assertEqual(record["仿真3_状态"], "invalid_result: ltSpeed=-1.000, lh=0, mh=0")


if __name__ == "__main__":
    unittest.main()
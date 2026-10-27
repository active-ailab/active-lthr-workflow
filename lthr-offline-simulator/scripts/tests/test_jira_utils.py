from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from shared.jira_utils import resolve_issue_sample_inputs  # noqa: E402


class JiraUtilsTests(unittest.TestCase):
    def test_resolve_issue_sample_inputs_local_csv_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            csv_path = tmp_path / "gomore_data_20260515_120000.csv"
            csv_path.write_text("csv", encoding="utf-8")

            issue_meta, sample_csvs, zepp_cmd = resolve_issue_sample_inputs(
                issue_key="MOD-1",
                download_dir=tmp_path / "download",
                csv_path=str(csv_path),
            )

            self.assertEqual(issue_meta["key"], "MOD-1")
            self.assertEqual(issue_meta["title"], "")
            self.assertEqual(sample_csvs, [csv_path.resolve()])
            self.assertEqual(zepp_cmd, [])

    def test_resolve_issue_sample_inputs_downloads_selected_jira_csvs(self) -> None:
        logs = []

        def _find():
            return ["zepp"]

        def _meta(zepp_cmd, issue_key):
            self.assertEqual(list(zepp_cmd), ["zepp"])
            self.assertEqual(issue_key, "MOD-2")
            return {"key": "MOD-2", "title": "sample issue", "link": "", "status": "Open"}

        def _list(zepp_cmd, issue_key):
            return [
                {"id": "1", "filename": "note.txt"},
                {"id": "2", "filename": "gomore_data_20260515_120000.csv"},
                {"id": "3", "filename": "gomore_data_20260516_120000.csv"},
            ]

        downloaded = []

        def _download(zepp_cmd, issue_key, filename, output_dir, timeout):
            downloaded.append((list(zepp_cmd), issue_key, filename, output_dir, timeout))
            path = output_dir / filename
            output_dir.mkdir(parents=True, exist_ok=True)
            path.write_text("csv", encoding="utf-8")
            return path

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            issue_meta, sample_csvs, zepp_cmd = resolve_issue_sample_inputs(
                issue_key="MOD-2",
                download_dir=tmp_path / "download",
                all_csvs=True,
                multi_attachment_mode="sequential",
                log_fn=logs.append,
                find_zepp_cmd_fn=_find,
                get_issue_meta_fn=_meta,
                list_jira_attachments_fn=_list,
                download_issue_attachment_fn=_download,
            )

            self.assertEqual(zepp_cmd, ["zepp"])
            self.assertEqual(issue_meta["title"], "sample issue")
            self.assertEqual([path.name for path in sample_csvs], [
                "gomore_data_20260515_120000.csv",
                "gomore_data_20260516_120000.csv",
            ])
            self.assertEqual(len(downloaded), 2)
            self.assertIn("[INFO] Fetching issue meta from Jira...", logs)
            self.assertIn("[INFO] Found 3 attachments", logs)

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from shared.jira_reporting import publish_issue_artifacts, resolve_model_signature  # noqa: E402


class JiraReportingTests(unittest.TestCase):
    def test_resolve_model_signature_uses_env_before_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cfg_dir = home / ".codex"
            cfg_dir.mkdir(parents=True, exist_ok=True)
            (cfg_dir / "config.toml").write_text(
                'model = "cfg-model"\nmodel_reasoning_effort = "cfg-effort"\n',
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {
                    "HOME": str(home),
                    "CODEX_MODEL": "env-model",
                },
                clear=False,
            ):
                self.assertEqual(
                    resolve_model_signature(),
                    "（by env-model | reasoning=cfg-effort）",
                )

    def test_publish_issue_artifacts_uploads_and_posts_comment(self) -> None:
        uploads = []
        comments = []

        def _upload(zepp_cmd, issue_key, artifact_paths):
            uploads.append((list(zepp_cmd), issue_key, [path.name for path in artifact_paths]))

        def _build(issue_meta, sample_summaries, artifact_names, signature, device_sdk_version):
            self.assertEqual(issue_meta["key"], "MOD-1")
            self.assertEqual(len(sample_summaries), 1)
            self.assertEqual(signature, "sig")
            self.assertEqual(device_sdk_version, "未获取")
            return f"comment:{','.join(artifact_names)}"

        def _comment(zepp_cmd, issue_key, comment):
            comments.append((list(zepp_cmd), issue_key, comment))

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifacts = [tmp_path / "report.md", tmp_path / "result.json", tmp_path / "raw.log"]
            for path in artifacts:
                path.write_text(path.name, encoding="utf-8")
            comment = publish_issue_artifacts(
                zepp_cmd=["zepp"],
                issue_key="MOD-1",
                issue_meta={"key": "MOD-1"},
                sample_summaries=[{"sample_csv": "gomore_data.csv"}],
                artifact_paths=artifacts,
                signature="sig",
                upload_attachments_fn=_upload,
                build_comment_fn=_build,
                add_jira_comment_fn=_comment,
            )

        archive_name = "report_artifacts.zip"
        self.assertEqual(comment, f"comment:{archive_name}")
        self.assertEqual(uploads, [(["zepp"], "MOD-1", [archive_name])])
        self.assertEqual(comments, [(["zepp"], "MOD-1", f"comment:{archive_name}")])

    def test_publish_issue_artifacts_skips_comment_when_disabled(self) -> None:
        uploads = []
        comments = []

        def _upload(zepp_cmd, issue_key, artifact_paths):
            uploads.append((list(zepp_cmd), issue_key, len(artifact_paths)))

        def _comment(zepp_cmd, issue_key, comment):
            comments.append((list(zepp_cmd), issue_key, comment))

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            artifacts = [tmp_path / "report.md"]
            artifacts[0].write_text("report", encoding="utf-8")
            comment = publish_issue_artifacts(
                zepp_cmd=["zepp"],
                issue_key="MOD-2",
                issue_meta={"key": "MOD-2"},
                sample_summaries=[],
                artifact_paths=artifacts,
                no_comment=True,
                upload_attachments_fn=_upload,
                add_jira_comment_fn=_comment,
            )

        self.assertEqual(comment, "")
        self.assertEqual(uploads, [(["zepp"], "MOD-2", 1)])
        self.assertEqual(comments, [])

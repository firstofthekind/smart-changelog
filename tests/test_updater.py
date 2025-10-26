import io
import os
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from smart_changelog import updater


class VersionHelperTests(unittest.TestCase):
    def test_update_context_render_uses_template(self) -> None:
        original_template = updater.Template

        class DummyTemplate:
            def __init__(self, fmt: str) -> None:
                self.fmt = fmt

            def render(self, **kwargs) -> str:  # pragma: no cover - deterministic stub
                return "rendered"

        try:
            updater.Template = DummyTemplate  # type: ignore[assignment]
            ctx = updater.UpdateContext(
                ticket_id="ABC-1",
                category="feature",
                title="Title",
                author="Author",
                date="2025-01-01",
            )
            self.assertEqual(ctx.render_entry(), "rendered")
        finally:
            updater.Template = original_template
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.original_cwd = Path.cwd()
        os.chdir(self.tmpdir.name)

    def tearDown(self) -> None:
        os.chdir(self.original_cwd)

    def test_current_version_from_manifest(self) -> None:
        Path("manifest.yaml").write_text(
            """version:\n  major: 2\n  minor: 7\n  patch: ${CI_PIPELINE_IID}\n  prerelease: rc1\n""",
            encoding="utf-8",
        )
        self.assertEqual(updater._current_version(), "2.7-rc1")

    def test_current_version_missing_manifest(self) -> None:
        self.assertEqual(updater._current_version(), "0.0")

    def test_ensure_version_block_creates_new(self) -> None:
        content = "# Changelog\n"
        updated, created = updater._ensure_version_block(content, "## 1.5", "2025-02-02")
        self.assertTrue(created)
        self.assertIn("## 1.5", updated)
        self.assertIn("_Last updated: 2025-02-02_", updated)

    def test_ensure_version_block_replaces_unreleased(self) -> None:
        content = (
            "# Changelog\n\n"
            "## [Unreleased]\n_Last updated: 2025-01-01_\n\n"
            "### 🧩 New Features\n\n"
            "### 🐛 Bug Fixes\n\n"
            "### ⚙️ Changes\n"
        )
        updated, created = updater._ensure_version_block(content, "## 3.0", "2025-03-03")
        self.assertTrue(created)
        self.assertIn("## 3.0", updated)
        self.assertNotIn("Unreleased", updated)

    def test_upsert_entry_for_version_inserts_and_updates(self) -> None:
        block = (
            "# Changelog\n\n"
            "## 1.0\n_Last updated: 2025-01-01_\n\n"
            "### 🧩 New Features\n\n"
            "### 🐛 Bug Fixes\n\n"
            "### ⚙️ Changes\n"
        )
        updated, changed = updater._upsert_entry_for_version(
            block,
            "## 1.0",
            "### ⚙️ Changes",
            "- First entry (CHANGE-1, Alice, 2025-01-02)",
            "CHANGE-1",
        )
        self.assertTrue(changed)
        self.assertIn("First entry", updated)

        updated_again, changed_again = updater._upsert_entry_for_version(
            updated,
            "## 1.0",
            "### ⚙️ Changes",
            "- First entry updated (CHANGE-1, Alice, 2025-01-03)",
            "CHANGE-1",
        )
        self.assertTrue(changed_again)
        self.assertIn("First entry updated", updated_again)

    def test_upsert_entry_adds_missing_section(self) -> None:
        block = (
            "# Changelog\n\n"
            "## 2.0\n_Last updated: 2025-05-01_\n"
        )
        updated, changed = updater._upsert_entry_for_version(
            block,
            "## 2.0",
            "### 🐛 Bug Fixes",
            "- Fix bug (BUG-1, Bob, 2025-05-02)",
            "BUG-1",
        )
        self.assertTrue(changed)
        self.assertIn("### 🐛 Bug Fixes", updated)
        self.assertIn("Fix bug", updated)

    def test_update_last_updated(self) -> None:
        content = (
            "# Changelog\n\n"
            "## 1.2\n_Last updated: 2025-01-01_\n\n"
            "### ⚙️ Changes\n"
        )
        updated, changed = updater._update_last_updated(content, "## 1.2", "2025-04-04")
        self.assertTrue(changed)
        self.assertIn("_Last updated: 2025-04-04_", updated)

    def test_update_last_updated_inserts_when_missing(self) -> None:
        content = (
            "# Changelog\n\n"
            "## 3.0\n\n"
            "### ⚙️ Changes\n"
        )
        updated, changed = updater._update_last_updated(content, "## 3.0", "2025-06-06")
        self.assertTrue(changed)
        self.assertIn("_Last updated: 2025-06-06_", updated)


class RunUpdateIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.original_cwd = Path.cwd()
        os.chdir(self.tmpdir.name)
        Path("manifest.yaml").write_text(
            """version:\n  major: 1\n  minor: 5\n  patch: ${CI_PIPELINE_IID}\n  prerelease: ""\n""",
            encoding="utf-8",
        )
        Path("CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
        os.environ["CI_COMMIT_AUTHOR"] = "Alice"

    def tearDown(self) -> None:
        os.chdir(self.original_cwd)
        os.environ.pop("CI_COMMIT_AUTHOR", None)

    def test_run_update_with_jira_ticket(self) -> None:
        def fake_git_output(cmd):
            joined = " ".join(cmd)
            if "--pretty=%s" in joined:
                return "feat: add endpoint"
            if "rev-parse" in joined:
                return "abcdef1"
            return "placeholder"

        with mock.patch.object(updater, "_gather_context_strings", return_value=[]), mock.patch.object(
            updater, "_detect_ticket_id", return_value="FOK-123"
        ), mock.patch.object(
            updater, "get_ticket_summary", return_value={"title": "Implement endpoint"}
        ), mock.patch.object(
            updater, "_maybe_commit_and_push"
        ) as commit_mock, mock.patch.object(
            updater, "_git_output", side_effect=fake_git_output
        ):
            updater.run_update(dry_run=False, use_ai=False, forced_ticket=None, verbose=False)

        commit_mock.assert_called_once()
        content = Path("CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## 1.5", content)
        self.assertIn("- Implement endpoint (FOK-123, Alice", content)

    def test_run_update_with_ai_enrichment_enabled(self) -> None:
        def fake_git_output(cmd):
            joined = " ".join(cmd)
            if "--pretty=%s" in joined:
                return "feat: add ai"
            if "rev-parse" in joined:
                return "ai12345"
            return "placeholder"

        with mock.patch.object(updater, "_gather_context_strings", return_value=[]), mock.patch.object(
            updater, "_detect_ticket_id", return_value="FOK-999"
        ), mock.patch.object(
            updater, "get_ticket_summary", return_value={"title": "Initial"}
        ), mock.patch.object(
            updater, "enhance_description", side_effect=lambda title, _: f"AI:{title}"
        ) as enhance_mock, mock.patch.object(
            updater, "_maybe_commit_and_push"
        ), mock.patch.object(
            updater, "_git_output", side_effect=fake_git_output
        ):
            updater.run_update(dry_run=False, use_ai=True, forced_ticket=None, verbose=False)

        enhance_mock.assert_called()
        content = Path("CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("AI:Initial", content)

    def test_run_update_without_ticket_uses_commit_history(self) -> None:
        context = updater.UpdateContext(
            ticket_id="CHANGE-abc123",
            category="change",
            title="Update docs",
            author="Bob",
            date="2025-02-02",
        )

        def fake_git_output(cmd):
            joined = " ".join(cmd)
            if "--pretty=%s" in joined:
                return "chore: update docs"
            if "rev-parse" in joined:
                return "abc1234"
            return "placeholder"

        with mock.patch.object(updater, "_gather_context_strings", return_value=[]), mock.patch.object(
            updater, "_detect_ticket_id", return_value=None
        ), mock.patch.object(
            updater, "_contexts_from_commit_history", return_value=[context]
        ), mock.patch.object(
            updater, "_maybe_commit_and_push"
        ) as commit_mock, mock.patch.object(
            updater, "_git_output", side_effect=fake_git_output
        ):
            updater.run_update(dry_run=False, use_ai=False, forced_ticket=None, verbose=False)

        commit_mock.assert_called_once()
        content = Path("CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("CHANGE-abc123", content)
        self.assertIn("Update docs", content)

    def test_run_update_without_ticket_creates_fallback_when_history_empty(self) -> None:
        def fake_git_output(cmd):
            joined = " ".join(cmd)
            if "--pretty=%s" in joined:
                return "fix: address issue"
            if "rev-parse" in joined:
                return "fedcba1"
            return "placeholder"

        with mock.patch.object(updater, "_gather_context_strings", return_value=[]), mock.patch.object(
            updater, "_detect_ticket_id", return_value=None
        ), mock.patch.object(
            updater, "_contexts_from_commit_history", return_value=[]
        ), mock.patch.object(
            updater, "enhance_description", side_effect=lambda title, _: f"AI:{title}"
        ) as enhance_mock, mock.patch.object(
            updater, "_maybe_commit_and_push"
        ) as commit_mock, mock.patch.object(
            updater, "_git_output", side_effect=fake_git_output
        ):
            updater.run_update(dry_run=False, use_ai=True, forced_ticket=None, verbose=False)

        commit_mock.assert_called_once()
        content = Path("CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("CHANGE-fedcba1", content)
        self.assertIn("AI:fix: address issue", content)
        enhance_mock.assert_called()

    def test_run_update_dry_run_outputs_preview(self) -> None:
        def fake_git_output(cmd):
            joined = " ".join(cmd)
            if "--pretty=%s" in joined:
                return "feat: dry run"
            if "rev-parse" in joined:
                return "dryrun1"
            return "placeholder"

        with mock.patch.object(updater, "_gather_context_strings", return_value=[]), mock.patch.object(
            updater, "_detect_ticket_id", return_value="FOK-DRY"
        ), mock.patch.object(
            updater, "get_ticket_summary", return_value={"title": "Dry"}
        ), mock.patch.object(
            updater, "_maybe_commit_and_push"
        ), mock.patch.object(
            updater, "_git_output", side_effect=fake_git_output
        ), mock.patch("sys.stdout", new_callable=io.StringIO) as fake_stdout:
            updater.run_update(dry_run=True, use_ai=False, forced_ticket=None, verbose=True)

        output = fake_stdout.getvalue()
        self.assertIn("Dry", output)


class AdditionalHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

    def test_current_version_fallback_parser(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        cwd = Path.cwd()
        try:
            os.chdir(temp.name)
            Path("manifest.yaml").write_text(
                """# sample\nversion:\n  major: 4\n  minor: 2\n  prerelease: \"\"\n""",
                encoding="utf-8",
            )
            original_yaml = updater.yaml
            updater.yaml = None  # type: ignore[assignment]
            self.assertEqual(updater._current_version(), "4.2")
        finally:
            updater.yaml = original_yaml
            os.chdir(cwd)

    def test_current_version_read_failure(self) -> None:
        path = Path("manifest.yaml")
        path.write_text("version:\n  major: 1\n  minor: 0\n", encoding="utf-8")
        with mock.patch.object(Path, "read_text", side_effect=OSError("boom")):
            self.assertEqual(updater._current_version(path), "0.0")

    def test_current_version_yaml_failure(self) -> None:
        path = Path("manifest.yaml")
        path.write_text("version: : bad", encoding="utf-8")
        original_yaml = updater.yaml
        updater.yaml = mock.Mock()
        updater.yaml.safe_load.side_effect = ValueError("bad")
        try:
            self.assertEqual(updater._current_version(path), "0.0")
        finally:
            updater.yaml = original_yaml

    def test_current_version_missing_major_minor(self) -> None:
        path = Path("manifest.yaml")
        path.write_text("version:\n  major: 1\n", encoding="utf-8")
        self.assertEqual(updater._current_version(path), "0.0")

    def test_contexts_from_commit_history_parses_git_log(self) -> None:
        log_output = (
            "abcdef123456789\tfeat: add api\tAlice\t2025-01-01\n"
            "bcdefa234567890\tfix bug\tBob\t2025-01-02"
        )

        with mock.patch.object(updater, "_git_output", return_value=log_output), mock.patch.object(
            updater, "enhance_description", side_effect=lambda title, _: f"AI:{title}"
        ):
            contexts = updater._contexts_from_commit_history(set(), use_ai=True, limit=10)

        self.assertEqual(len(contexts), 2)
        ids = [ctx.ticket_id for ctx in contexts]
        self.assertIn("CHANGE-abcdef1", ids)
        self.assertIn("CHANGE-bcdefa2", ids)
        self.assertTrue(all(ctx.title.startswith("AI:") for ctx in contexts))

    def test_read_changelog_bootstraps(self) -> None:
        temp_path = Path(self.tmpdir.name) / "NEW_CHANGELOG.md"
        content = updater._read_changelog(temp_path)
        self.assertTrue(temp_path.exists())
        self.assertIn("# Changelog", content)

    def test_detect_ticket_id_variants(self) -> None:
        self.assertEqual(updater._detect_ticket_id("ABC-1", []), "ABC-1")
        self.assertIsNone(updater._detect_ticket_id("bad-format", []))
        self.assertEqual(updater._detect_ticket_id(None, ["feat ABC-2"]), "ABC-2")
        self.assertIsNone(updater._detect_ticket_id(None, []))

    def test_ensure_version_block_inserts_after_header(self) -> None:
        content = "# Changelog\n\n## 0.9\n"
        updated, created = updater._ensure_version_block(content, "## 0.10", "2025-07-07")
        self.assertTrue(created)
        self.assertIn("## 0.10", updated)

    def test_upsert_entry_in_block_appends_when_heading_ends_block(self) -> None:
        block = "## 4.0\n_Last updated: 2025-05-01_\n\n### 🧩 New Features"
        updated, changed = updater._upsert_entry_in_block(block, "### 🧩 New Features", "- Item", "ITEM-1")
        self.assertTrue(changed)
        self.assertIn("- Item", updated)

    def test_maybe_commit_skip_env_variable(self) -> None:
        os.environ["SMART_CHANGELOG_SKIP_COMMIT"] = "1"
        try:
            updater._maybe_commit_and_push("CHANGELOG.md", "entry")
        finally:
            os.environ.pop("SMART_CHANGELOG_SKIP_COMMIT", None)

    def test_current_branch_prefers_env(self) -> None:
        os.environ["GITHUB_REF_NAME"] = "feature"
        self.addCleanup(lambda: os.environ.pop("GITHUB_REF_NAME", None))
        self.assertEqual(updater._current_branch(), "feature")

    def test_current_branch_falls_back_to_git(self) -> None:
        for key in ["GITHUB_REF_NAME", "GITHUB_HEAD_REF", "CI_COMMIT_BRANCH", "CI_DEFAULT_BRANCH"]:
            os.environ.pop(key, None)
        with mock.patch.object(updater, "_git_output", return_value="main"):
            self.assertEqual(updater._current_branch(), "main")

    def test_detect_author_prefers_env(self) -> None:
        os.environ["CI_COMMIT_AUTHOR"] = "CI Bot"
        self.addCleanup(lambda: os.environ.pop("CI_COMMIT_AUTHOR", None))
        self.assertEqual(updater._detect_author(), "CI Bot")

    def test_detect_author_falls_back_to_git(self) -> None:
        with mock.patch.object(updater, "_git_output", return_value="Git User"):
            self.assertEqual(updater._detect_author(), "Git User")

    def test_gather_context_strings_collects_git_metadata(self) -> None:
        with mock.patch.object(updater, "_git_output", side_effect=["message", "branch"]):
            contexts = updater._gather_context_strings()
        self.assertIn("message", contexts)
        self.assertIn("branch", contexts)

    def test_fallback_ticket_identifier_uses_git(self) -> None:
        with mock.patch.object(updater, "_git_output", return_value="abc1234"):
            self.assertEqual(updater._fallback_ticket_identifier(), "CHANGE-abc1234")

    def test_fallback_ticket_identifier_without_git(self) -> None:
        with mock.patch.object(updater, "_git_output", return_value=None):
            self.assertEqual(updater._fallback_ticket_identifier(), "CHANGE-NOREF")

    def test_first_non_empty(self) -> None:
        self.assertEqual(updater._first_non_empty(["", " value "]), "value")
        self.assertEqual(updater._first_non_empty([]), "")

    def test_maybe_commit_and_push_happy_path(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, check=False, stdout=None, stderr=None, env=None):
            calls.append(cmd)
            if cmd[:2] == ["git", "add"]:
                return subprocess.CompletedProcess(cmd, 0)
            if cmd[:3] == ["git", "diff", "--cached"]:
                return subprocess.CompletedProcess(cmd, 1)
            if cmd[:2] == ["git", "commit"]:
                return subprocess.CompletedProcess(cmd, 0)
            if cmd[:2] == ["git", "push"]:
                return subprocess.CompletedProcess(cmd, 0)
            return subprocess.CompletedProcess(cmd, 0)

        with mock.patch.object(updater, "_git_available", return_value=True), mock.patch.object(
            updater, "_current_branch", return_value="main"
        ), mock.patch("subprocess.run", side_effect=fake_run):
            updater._maybe_commit_and_push("CHANGELOG.md", "entry")

        self.assertGreaterEqual(len(calls), 4)

    def test_maybe_commit_handles_failures(self) -> None:
        with mock.patch.object(updater, "_git_available", return_value=False):
            updater._maybe_commit_and_push("CHANGELOG.md", "entry")

        def raise_on_add(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd)

        with mock.patch.object(updater, "_git_available", return_value=True), mock.patch("subprocess.run", side_effect=raise_on_add):
            updater._maybe_commit_and_push("CHANGELOG.md", "entry")

        sequence = [
            subprocess.CompletedProcess(["git", "add"], 0),
            subprocess.CompletedProcess(["git", "diff", "--cached", "--quiet"], 0),
        ]

        def fake_run_no_changes(cmd, **kwargs):
            return sequence.pop(0)

        with mock.patch.object(updater, "_git_available", return_value=True), mock.patch("subprocess.run", side_effect=fake_run_no_changes):
            updater._maybe_commit_and_push("CHANGELOG.md", "entry")

        def sequence_commit_fail(cmd, **kwargs):
            if cmd[:2] == ["git", "add"]:
                return subprocess.CompletedProcess(cmd, 0)
            if cmd[:3] == ["git", "diff", "--cached", "--quiet"]:
                return subprocess.CompletedProcess(cmd, 1)
            if cmd[:2] == ["git", "commit"]:
                raise subprocess.CalledProcessError(1, cmd)
            return subprocess.CompletedProcess(cmd, 0)

        with mock.patch.object(updater, "_git_available", return_value=True), mock.patch("subprocess.run", side_effect=sequence_commit_fail):
            updater._maybe_commit_and_push("CHANGELOG.md", "entry")

        def sequence_push_fail(cmd, **kwargs):
            if cmd[:2] == ["git", "add"]:
                return subprocess.CompletedProcess(cmd, 0)
            if cmd[:3] == ["git", "diff", "--cached", "--quiet"]:
                return subprocess.CompletedProcess(cmd, 1)
            if cmd[:2] == ["git", "commit"]:
                return subprocess.CompletedProcess(cmd, 0)
            if cmd[:2] == ["git", "push"]:
                raise subprocess.CalledProcessError(1, cmd)
            return subprocess.CompletedProcess(cmd, 0)

        with mock.patch.object(updater, "_git_available", return_value=True), mock.patch.object(
            updater, "_current_branch", return_value="main"
        ), mock.patch("subprocess.run", side_effect=sequence_push_fail):
            updater._maybe_commit_and_push("CHANGELOG.md", "entry")


if __name__ == "__main__":
    unittest.main()

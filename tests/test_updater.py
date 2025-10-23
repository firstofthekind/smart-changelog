import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest import mock

from smart_changelog import updater


class UpdateContextTests(unittest.TestCase):
    def test_render_entry_without_jinja(self) -> None:
        ctx = updater.UpdateContext(
            ticket_id="ABC-1",
            category="feature",
            title="Add feature",
            author="Alice",
            date="2025-01-01",
        )
        rendered = ctx.render_entry()
        self.assertIn("ABC-1", rendered)
        self.assertTrue(rendered.startswith("- Add feature"))

    def test_render_entry_with_custom_template(self) -> None:
        class FakeTemplate:
            def __init__(self, template_string: str) -> None:
                self.template_string = template_string

            def render(self, **context: Any) -> str:
                return "- {title} ({ticket}, {author}, {date})".format(**context)

        original_template = updater.Template
        try:
            updater.Template = FakeTemplate  # type: ignore[assignment]
            ctx = updater.UpdateContext(
                ticket_id="XYZ-9",
                category="change",
                title="Adjust settings",
                author="Bob",
                date="2025-02-02",
            )
            rendered = ctx.render_entry()
        finally:
            updater.Template = original_template

        self.assertEqual(rendered, "- Adjust settings (XYZ-9, Bob, 2025-02-02)")


class UpdaterFunctionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.cwd = Path(self.tmpdir.name)
        self.original_cwd = Path.cwd()
        self.env_backup = os.environ.copy()

    def tearDown(self) -> None:
        os.chdir(self.original_cwd)
        os.environ.clear()
        os.environ.update(self.env_backup)

    def test_read_changelog_bootstraps_from_template(self) -> None:
        os.chdir(self.cwd)
        path = self.cwd / "CHANGELOG.md"
        content = updater._read_changelog(path)
        self.assertIn("# Changelog", content)
        self.assertTrue(path.exists())

    def test_read_changelog_returns_existing(self) -> None:
        os.chdir(self.cwd)
        path = self.cwd / "CHANGELOG.md"
        path.write_text("existing", encoding="utf-8")
        content = updater._read_changelog(path)
        self.assertEqual(content, "existing")

    def test_detect_ticket_from_env(self) -> None:
        os.environ["CI_COMMIT_TITLE"] = "feat: add support ABC-77"
        ticket = updater._detect_ticket_id(None, ["feat: add support ABC-77"])
        self.assertEqual(ticket, "ABC-77")

    def test_detect_ticket_forced_valid(self) -> None:
        ticket = updater._detect_ticket_id("ABC-77", [])
        self.assertEqual(ticket, "ABC-77")

    def test_detect_ticket_forced_invalid(self) -> None:
        self.assertIsNone(updater._detect_ticket_id("ticket-123", []))

    def test_detect_ticket_no_candidates(self) -> None:
        ticket = updater._detect_ticket_id(None, [])
        self.assertIsNone(ticket)

    def test_detect_ticket_from_git_output(self) -> None:
        with mock.patch("smart_changelog.updater._gather_context_strings", return_value=["feature/ABC-88"]):
            ticket = updater._detect_ticket_id(None, updater._gather_context_strings())
        self.assertEqual(ticket, "ABC-88")

    def test_categorize_variants(self) -> None:
        self.assertEqual(updater._categorize("feat: add"), "feature")
        self.assertEqual(updater._categorize("fix: bug"), "fix")
        self.assertEqual(updater._categorize("refactor: code"), "change")
        self.assertEqual(updater._categorize("chore: misc"), "change")
        self.assertEqual(updater._categorize("update dependencies"), "change")

    def test_upsert_entry_adds_new_entry(self) -> None:
        base = (
            "# Changelog\n\n"
            "## [Unreleased]\n_Last updated: 2025-01-01_\n\n"
            "### 🧩 New Features\n\n"
            "### 🐛 Bug Fixes\n\n"
            "### ⚙️ Changes\n"
        )
        updated, changed = updater._upsert_entry(
            base,
            "### 🧩 New Features",
            "- New item (ABC-1, Alice, 2025-01-02)",
            "ABC-1",
        )
        self.assertTrue(changed)
        self.assertIn("New item", updated)

    def test_upsert_entry_updates_existing(self) -> None:
        base = (
            "# Changelog\n\n"
            "## [Unreleased]\n_Last updated: 2025-01-01_\n\n"
            "### 🧩 New Features\n\n"
            "- Old item (ABC-1, Alice, 2025-01-01)\n"
        )
        updated, changed = updater._upsert_entry(
            base,
            "### 🧩 New Features",
            "- Updated item (ABC-1, Alice, 2025-01-03)",
            "ABC-1",
        )
        self.assertTrue(changed)
        self.assertIn("Updated item", updated)

    def test_upsert_entry_detects_existing_entry(self) -> None:
        base = (
            "# Changelog\n\n"
            "## [Unreleased]\n_Last updated: 2025-01-01_\n\n"
            "### 🧩 New Features\n\n"
            "- Entry (ABC-1, Alice, 2025-01-01)\n"
        )
        updated, changed = updater._upsert_entry(
            base,
            "### 🧩 New Features",
            "- Entry (ABC-1, Alice, 2025-01-01)",
            "ABC-1",
        )
        self.assertFalse(changed)
        self.assertEqual(updated, base)

    def test_upsert_entry_appends_when_section_missing(self) -> None:
        base = (
            "# Changelog\n\n"
            "## [Unreleased]\n_Last updated: 2025-01-01_\n"
        )
        updated, changed = updater._upsert_entry(
            base,
            "### 🧩 New Features",
            "- Item (ABC-1, Alice, 2025-01-02)",
            "ABC-1",
        )
        self.assertTrue(changed)
        self.assertIn("### 🧩 New Features", updated)

    def test_upsert_entry_creates_unreleased_when_missing(self) -> None:
        base = "# Changelog\n"
        updated, changed = updater._upsert_entry(
            base,
            "### 🧩 New Features",
            "- Item (ABC-1, Alice, 2025-01-02)",
            "ABC-1",
        )
        self.assertTrue(changed)
        self.assertIn("## [Unreleased]", updated)

    def test_upsert_entry_respects_blank_line_after_heading(self) -> None:
        base = (
            "# Changelog\n\n"
            "## [Unreleased]\n_Last updated: 2025-01-01_\n\n"
            "### 🧩 New Features\n\n"
            "- Existing (ABC-2, Bob, 2025-01-01)\n"
        )
        updated, changed = updater._upsert_entry(
            base,
            "### 🧩 New Features",
            "- New Item (ABC-1, Alice, 2025-01-02)",
            "ABC-1",
        )
        self.assertTrue(changed)
        self.assertIn("- New Item", updated)

    def test_update_last_updated_existing_marker(self) -> None:
        content = "_Last updated: 2025-01-01_"
        updated, changed = updater._update_last_updated(content, "2025-01-02")
        self.assertTrue(changed)
        self.assertIn("2025-01-02", updated)

    def test_update_last_updated_injects_when_missing(self) -> None:
        content = "## [Unreleased]\nSome text"
        updated, changed = updater._update_last_updated(content, "2025-05-05")
        self.assertTrue(changed)
        self.assertIn("_Last updated: 2025-05-05_", updated)

    def test_update_last_updated_no_change(self) -> None:
        content = "## Release notes\nNothing here"
        updated, changed = updater._update_last_updated(content, "2025-06-06")
        self.assertFalse(changed)
        self.assertEqual(updated, content)

    def test_git_output_handles_failures(self) -> None:
        with mock.patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, ["git"])):
            result = updater._git_output(["git"])
        self.assertIsNone(result)

    def test_maybe_commit_skips_when_env_set(self) -> None:
        os.environ["SMART_CHANGELOG_SKIP_COMMIT"] = "1"
        with mock.patch("smart_changelog.updater._git_available", return_value=True):
            updater._maybe_commit_and_push("CHANGELOG.md", "entry")

    def test_maybe_commit_git_not_available(self) -> None:
        with mock.patch("smart_changelog.updater._git_available", return_value=False):
            updater._maybe_commit_and_push("CHANGELOG.md", "entry")

    def test_maybe_commit_full_flow(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], check: bool = False, **kwargs: Any):
            calls.append(cmd)
            if cmd[:2] == ["git", "add"]:
                return subprocess.CompletedProcess(cmd, 0)
            if cmd[:3] == ["git", "diff", "--cached"]:
                # Simulate differences by returning non-zero
                return subprocess.CompletedProcess(cmd, 1)
            if cmd[:2] == ["git", "commit"]:
                return subprocess.CompletedProcess(cmd, 0)
            if cmd[:2] == ["git", "push"]:
                return subprocess.CompletedProcess(cmd, 0)
            return subprocess.CompletedProcess(cmd, 0)

        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("smart_changelog.updater._git_available", return_value=True):
                with mock.patch("smart_changelog.updater._current_branch", return_value="main"):
                    with mock.patch("subprocess.run", side_effect=fake_run):
                        updater._maybe_commit_and_push("CHANGELOG.md", "entry")

        self.assertGreaterEqual(len(calls), 4)

    def test_maybe_commit_skips_when_no_changes(self) -> None:
        sequence = [
            subprocess.CompletedProcess(["git", "add"], 0),
            subprocess.CompletedProcess(["git", "diff", "--cached", "--quiet"], 0),
        ]

        def fake_run(cmd: list[str], check: bool = False, **kwargs: Any):
            return sequence.pop(0)

        with mock.patch("smart_changelog.updater._git_available", return_value=True):
            with mock.patch("subprocess.run", side_effect=fake_run):
                updater._maybe_commit_and_push("CHANGELOG.md", "entry")

        self.assertEqual(sequence, [])

    def test_maybe_commit_handles_stage_failure(self) -> None:
        with mock.patch("smart_changelog.updater._git_available", return_value=True):
            with mock.patch(
                "subprocess.run",
                side_effect=subprocess.CalledProcessError(1, ["git", "add"]),
            ):
                updater._maybe_commit_and_push("CHANGELOG.md", "entry")

    def test_maybe_commit_handles_commit_failure(self) -> None:
        sequence = [
            subprocess.CompletedProcess(["git", "add"], 0),
            subprocess.CompletedProcess(["git", "diff", "--cached", "--quiet"], 1),
            subprocess.CalledProcessError(1, ["git", "commit"]),
        ]

        def fake_run(cmd: list[str], check: bool = False, **kwargs: Any):
            result = sequence.pop(0)
            if isinstance(result, subprocess.CalledProcessError):
                raise result
            return result

        with mock.patch("smart_changelog.updater._git_available", return_value=True):
            with mock.patch("subprocess.run", side_effect=fake_run):
                updater._maybe_commit_and_push("CHANGELOG.md", "entry")

    def test_maybe_commit_handles_push_failure(self) -> None:
        sequence: list[Any] = [
            subprocess.CompletedProcess(["git", "add"], 0),
            subprocess.CompletedProcess(["git", "diff", "--cached", "--quiet"], 1),
            subprocess.CompletedProcess(["git", "commit", "-m", "msg"], 0),
            subprocess.CalledProcessError(1, ["git", "push"]),
        ]

        def fake_run(cmd: list[str], check: bool = False, **kwargs: Any):
            result = sequence.pop(0)
            if isinstance(result, subprocess.CalledProcessError):
                raise result
            return result

        with mock.patch("smart_changelog.updater._git_available", return_value=True):
            with mock.patch("smart_changelog.updater._current_branch", return_value="main"):
                with mock.patch("subprocess.run", side_effect=fake_run):
                    updater._maybe_commit_and_push("CHANGELOG.md", "entry")

    def test_maybe_commit_skips_when_branch_unknown(self) -> None:
        sequence = [
            subprocess.CompletedProcess(["git", "add"], 0),
            subprocess.CompletedProcess(["git", "diff", "--cached", "--quiet"], 1),
            subprocess.CompletedProcess(["git", "commit", "-m", "msg"], 0),
        ]

        def fake_run(cmd: list[str], check: bool = False, **kwargs: Any):
            return sequence.pop(0)

        with mock.patch("smart_changelog.updater._git_available", return_value=True):
            with mock.patch("smart_changelog.updater._current_branch", return_value=None):
                with mock.patch("subprocess.run", side_effect=fake_run):
                    updater._maybe_commit_and_push("CHANGELOG.md", "entry")

    def test_git_available(self) -> None:
        with mock.patch("subprocess.call", return_value=0) as call_mock:
            self.assertTrue(updater._git_available())
        call_mock.assert_called_once()

    def test_detect_author_prefers_env(self) -> None:
        os.environ["CI_COMMIT_AUTHOR"] = "CI Bot"
        author = updater._detect_author()
        self.assertEqual(author, "CI Bot")

    def test_detect_author_falls_back_to_git(self) -> None:
        with mock.patch("smart_changelog.updater._git_output", return_value="Alice"):
            author = updater._detect_author()
        self.assertEqual(author, "Alice")

    def test_gather_context_strings_collects_data(self) -> None:
        os.environ["CI_COMMIT_TITLE"] = "feat: ABC-1"
        with mock.patch("smart_changelog.updater._git_output", side_effect=["message", "branch"]):
            candidates = updater._gather_context_strings()
        self.assertIn("feat: ABC-1", candidates)
        self.assertIn("message", candidates)
        self.assertIn("branch", candidates)

    def test_git_output_success(self) -> None:
        completed = subprocess.CompletedProcess(["git"], 0, stdout="value\n")

        def fake_run(cmd, check, stdout, stderr, text):
            return completed

        with mock.patch("subprocess.run", side_effect=fake_run):
            result = updater._git_output(["git"])
        self.assertEqual(result, "value")

    def test_fallback_ticket_identifier_uses_git(self) -> None:
        with mock.patch("smart_changelog.updater._git_output", return_value="abcdef1"):
            token = updater._fallback_ticket_identifier()
        self.assertEqual(token, "CHANGE-abcdef1")

    def test_fallback_ticket_identifier_no_git(self) -> None:
        with mock.patch("smart_changelog.updater._git_output", return_value=None):
            token = updater._fallback_ticket_identifier()
        self.assertEqual(token, "CHANGE-NOREF")

    def test_first_non_empty(self) -> None:
        result = updater._first_non_empty(["", "   ", "value", "next"])
        self.assertEqual(result, "value")
        self.assertEqual(updater._first_non_empty([]), "")

    def test_run_update_dry_run_outputs_content(self) -> None:
        changelog = (
            "# Changelog\n\n"
            "## [Unreleased]\n_Last updated: 2025-01-01_\n\n"
            "### 🧩 New Features\n\n"
            "### 🐛 Bug Fixes\n\n"
            "### ⚙️ Changes\n"
        )

        os.chdir(self.cwd)
        with mock.patch("smart_changelog.updater._gather_context_strings", return_value=["feat: ABC-1"]), mock.patch(
            "smart_changelog.updater._read_changelog", return_value=changelog
        ), mock.patch(
            "smart_changelog.updater._detect_ticket_id", return_value="ABC-1"
        ), mock.patch("smart_changelog.updater._categorize", return_value="feature"), mock.patch(
            "smart_changelog.updater.get_ticket_summary", return_value={"title": "Implement feature"}
        ), mock.patch(
            "smart_changelog.updater._maybe_commit_and_push"
        ) as commit_mock, mock.patch(
            "smart_changelog.updater.Path.write_text"
        ):
            with mock.patch("smart_changelog.updater._update_last_updated", return_value=(changelog, True)):
                with mock.patch("smart_changelog.updater._upsert_entry", return_value=(changelog, True)):
                    with mock.patch("sys.stdout", new_callable=io.StringIO) as fake_stdout:
                        updater.run_update(dry_run=True, use_ai=False, forced_ticket=None, verbose=True)

        output = fake_stdout.getvalue()
        self.assertIn("# Changelog", output)
        commit_mock.assert_not_called()

    def test_run_update_writes_file(self) -> None:
        changelog_path = self.cwd / "CHANGELOG.md"
        changelog_path.write_text(
            "# Changelog\n\n"
            "## [Unreleased]\n_Last updated: 2025-01-01_\n\n"
            "### 🧩 New Features\n\n"
            "### 🐛 Bug Fixes\n\n"
            "### ⚙️ Changes\n",
            encoding="utf-8",
        )

        new_content = "updated changelog"

        os.chdir(self.cwd)
        with mock.patch("smart_changelog.updater._gather_context_strings", return_value=["feat: ABC-2"]), mock.patch(
            "smart_changelog.updater._read_changelog", return_value=changelog_path.read_text()
        ):
            with mock.patch("smart_changelog.updater._detect_ticket_id", return_value="ABC-2"):
                with mock.patch("smart_changelog.updater._categorize", return_value="feature"):
                    with mock.patch("smart_changelog.updater.get_ticket_summary", return_value={"title": "Implement"}):
                        with mock.patch("smart_changelog.updater._upsert_entry", return_value=(new_content, True)):
                            with mock.patch(
                                "smart_changelog.updater._update_last_updated", return_value=(new_content, True)
                            ):
                                with mock.patch("smart_changelog.updater._maybe_commit_and_push") as commit_mock:
                                    updater.run_update(
                                        dry_run=False, use_ai=False, forced_ticket=None, verbose=False
                                    )

        self.assertEqual(changelog_path.read_text(), new_content)
        commit_mock.assert_called_once()

    def test_run_update_with_ai_enrichment(self) -> None:
        changelog = (
            "# Changelog\n\n"
            "## [Unreleased]\n_Last updated: 2025-01-01_\n\n"
            "### 🧩 New Features\n\n"
            "### 🐛 Bug Fixes\n\n"
            "### ⚙️ Changes\n"
        )

        os.chdir(self.cwd)
        with mock.patch("smart_changelog.updater._gather_context_strings", return_value=["feat: AI-123"]), mock.patch(
            "smart_changelog.updater._read_changelog", return_value=changelog
        ), mock.patch(
            "smart_changelog.updater._detect_ticket_id", return_value="AI-123"
        ), mock.patch("smart_changelog.updater._categorize", return_value="feature"), mock.patch(
            "smart_changelog.updater.get_ticket_summary", return_value={"title": "Implement feature"}
        ), mock.patch(
            "smart_changelog.updater.enhance_description", return_value="Enhanced summary"
        ) as enhance_mock, mock.patch(
            "smart_changelog.updater._upsert_entry", return_value=(changelog, True)
        ), mock.patch(
            "smart_changelog.updater._update_last_updated", return_value=(changelog, True)
        ), mock.patch(
            "smart_changelog.updater._maybe_commit_and_push"
        ) as commit_mock:
            updater.run_update(dry_run=False, use_ai=True, forced_ticket=None, verbose=False)

        enhance_mock.assert_called_once()
        commit_mock.assert_called_once()

    def test_run_update_already_up_to_date(self) -> None:
        changelog = "# Changelog\n"
        os.chdir(self.cwd)
        with mock.patch("smart_changelog.updater._gather_context_strings", return_value=["Existing change"]), mock.patch(
            "smart_changelog.updater._read_changelog", return_value=changelog
        ), mock.patch(
            "smart_changelog.updater._detect_ticket_id", return_value="ABC-9"
        ), mock.patch("smart_changelog.updater._categorize", return_value="feature"), mock.patch(
            "smart_changelog.updater.get_ticket_summary", return_value={"title": "Existing"}
        ), mock.patch(
            "smart_changelog.updater._upsert_entry", return_value=(changelog, False)
        ), mock.patch(
            "smart_changelog.updater._update_last_updated", return_value=(changelog, False)
        ), mock.patch(
            "smart_changelog.updater._maybe_commit_and_push"
        ) as commit_mock:
            updater.run_update(dry_run=False, use_ai=False, forced_ticket=None, verbose=False)

        commit_mock.assert_not_called()

    def test_run_update_without_ticket(self) -> None:
        os.chdir(self.cwd)
        with mock.patch("smart_changelog.updater._gather_context_strings", return_value=["MR: Add feature"]), mock.patch(
            "smart_changelog.updater._detect_ticket_id", return_value=None
        ), mock.patch("smart_changelog.updater._categorize", return_value="feature"), mock.patch(
            "smart_changelog.updater._upsert_entry", return_value=("content", True)
        ), mock.patch(
            "smart_changelog.updater._update_last_updated", return_value=("content", True)
        ), mock.patch(
            "smart_changelog.updater.Path.write_text"
        ), mock.patch(
            "smart_changelog.updater._maybe_commit_and_push"
        ) as commit_mock, mock.patch(
            "smart_changelog.updater.get_ticket_summary"
        ) as jira_mock, mock.patch(
            "smart_changelog.updater._git_output", return_value="abcdef1"
        ):
            updater.run_update(dry_run=False, use_ai=False, forced_ticket=None, verbose=False)

        commit_mock.assert_called_once()
        jira_mock.assert_not_called()

    def test_current_branch_from_env(self) -> None:
        os.environ["GITHUB_REF_NAME"] = "feature"
        self.assertEqual(updater._current_branch(), "feature")

    def test_current_branch_fallback(self) -> None:
        with mock.patch("smart_changelog.updater._git_output", return_value="develop"):
            branch = updater._current_branch()
        self.assertEqual(branch, "develop")

    def test_current_branch_none(self) -> None:
        with mock.patch("smart_changelog.updater._git_output", return_value=None):
            branch = updater._current_branch()
        self.assertIsNone(branch)


if __name__ == "__main__":
    unittest.main()

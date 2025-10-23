"""Core changelog update logic."""
from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Dict, Optional, Tuple

try:
    from jinja2 import Template
except ImportError:  # pragma: no cover - optional dependency fallback
    Template = None  # type: ignore[assignment]

from .ai_helper import enhance_description
from .jira_client import get_ticket_summary

LOGGER = logging.getLogger(__name__)

TICKET_PATTERN = re.compile(r"([A-Z][A-Z0-9]+-\d+)")
SECTION_HEADINGS = {
    "feature": "### 🧩 New Features",
    "fix": "### 🐛 Bug Fixes",
    "change": "### ⚙️ Changes",
}


@dataclass
class UpdateContext:
    """Aggregates data required to build a changelog entry."""

    ticket_id: str
    category: str
    title: str
    author: str
    date: str

    def render_entry(self) -> str:
        if Template is not None:
            template = Template("- {{ title }} ({{ ticket }}, {{ author }}, {{ date }})")
            return template.render(
                title=self.title.strip(),
                ticket=self.ticket_id,
                author=self.author.strip() or "Unknown",
                date=self.date,
            )
        # Fallback avoids jinja2 dependency when unavailable.
        author = self.author.strip() or "Unknown"
        return f"- {self.title.strip()} ({self.ticket_id}, {author}, {self.date})"


def run_update(*, dry_run: bool, use_ai: bool, forced_ticket: Optional[str], verbose: bool) -> None:
    """Public entrypoint used by the CLI."""

    if verbose:
        LOGGER.debug("Verbose mode enabled")

    changelog_path = Path("CHANGELOG.md")
    changelog_text = _read_changelog(changelog_path)

    context_strings = _gather_context_strings()
    ticket_id = _detect_ticket_id(forced_ticket, context_strings)

    commit_title = _git_output(["git", "log", "-1", "--pretty=%s"]) or ""
    category_key = _categorize(commit_title)
    heading = SECTION_HEADINGS.get(category_key, SECTION_HEADINGS["change"])

    author = _detect_author()
    date_str = datetime.utcnow().date().isoformat()

    jira_summary: Dict[str, str] = {}
    title = commit_title
    fallback_mode = False
    if not ticket_id:
        fallback_mode = True
        ticket_id = _fallback_ticket_identifier()
        fallback_title = _first_non_empty(context_strings) or commit_title or "Unspecified change"
        title = fallback_title
        jira_summary = {"title": title}
        LOGGER.info("Proceeding without Jira ticket; using fallback identifier %s", ticket_id)
    if not fallback_mode:
        jira_summary = get_ticket_summary(ticket_id)
        title = jira_summary.get("title") or commit_title or ticket_id

    if use_ai:
        title = enhance_description(title, ticket_id)

    context = UpdateContext(
        ticket_id=ticket_id,
        category=category_key,
        title=title,
        author=author,
        date=date_str,
    )

    LOGGER.info("Updating changelog for %s (category: %s)", ticket_id, category_key)
    new_entry = context.render_entry()

    updated_text, entry_changed = _upsert_entry(changelog_text, heading, new_entry, ticket_id)
    updated_text, date_changed = _update_last_updated(updated_text, date_str)

    if not entry_changed and not date_changed:
        LOGGER.info("Changelog already up to date for %s", ticket_id)
        return

    if dry_run:
        LOGGER.info("Dry-run enabled; not writing any files")
        print(updated_text)
        return

    changelog_path.write_text(updated_text, encoding="utf-8")
    LOGGER.debug("CHANGELOG.md updated on disk")

    _maybe_commit_and_push("CHANGELOG.md", new_entry)


def _read_changelog(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")

    LOGGER.info("CHANGELOG.md not found; bootstrapping from template")
    template_text = resources.files("smart_changelog.templates").joinpath("changelog_template.md").read_text(encoding="utf-8")
    path.write_text(template_text, encoding="utf-8")
    return template_text


def _detect_ticket_id(forced_ticket: Optional[str], candidates: list[str]) -> Optional[str]:
    if forced_ticket:
        match = TICKET_PATTERN.search(forced_ticket)
        if match:
            return match.group(1)
        LOGGER.warning("Forced ticket '%s' does not match expected pattern", forced_ticket)
        return None

    for candidate in candidates:
        match = TICKET_PATTERN.search(candidate)
        if match:
            return match.group(1)

    return None


def _gather_context_strings() -> list[str]:
    candidates = []
    env_vars = [
        "CI_COMMIT_TITLE",
        "CI_MERGE_REQUEST_TITLE",
        "CI_COMMIT_MESSAGE",
        "CI_COMMIT_BRANCH",
        "GITHUB_HEAD_REF",
        "GITHUB_REF_NAME",
        "GITHUB_REF",
        "BRANCH_NAME",
    ]
    for key in env_vars:
        value = os.getenv(key)
        if value:
            candidates.append(value)

    commit_message = _git_output(["git", "log", "-1", "--pretty=%B"]) or ""
    branch_name = _git_output(["git", "rev-parse", "--abbrev-ref", "HEAD"]) or ""
    candidates.extend(filter(None, [commit_message, branch_name]))

    return candidates


def _categorize(commit_title: str) -> str:
    title_lower = commit_title.lower()
    # Inspect the first word when available, falling back to substring checks.
    first_word = title_lower.split()[0] if title_lower else ""

    if first_word.startswith("feat") or "feat:" in title_lower:
        return "feature"
    if first_word.startswith("fix") or "fix:" in title_lower:
        return "fix"
    if first_word.startswith("chore") or "chore:" in title_lower:
        return "change"
    if first_word.startswith("refactor") or "refactor:" in title_lower or "change" in title_lower:
        return "change"
    return "change"


def _detect_author() -> str:
    env_candidates = ["CI_COMMIT_AUTHOR", "GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"]
    for key in env_candidates:
        value = os.getenv(key)
        if value:
            return value

    author = _git_output(["git", "log", "-1", "--pretty=%an"]) or ""
    return author or "Unknown"


def _upsert_entry(content: str, heading: str, entry: str, ticket_id: str) -> Tuple[str, bool]:
    section_regex = re.compile(rf"({re.escape(heading)}\n(?:.*?))(\n### |\n## |\Z)", re.DOTALL)
    match = section_regex.search(content)

    if not match:
        LOGGER.warning("Section '%s' not found, appending to [Unreleased] block", heading)
        unreleased_regex = re.compile(r"(## \[Unreleased\].*?)(\n## |\Z)", re.DOTALL)
        unreleased_match = unreleased_regex.search(content)
        new_section = f"{heading}\n\n{entry}\n"
        if unreleased_match:
            start, end = unreleased_match.span(1)
            unreleased_block = unreleased_match.group(1)
            updated_block = unreleased_block.rstrip() + "\n\n" + new_section
            content = content[:start] + updated_block + content[end:]
        if not unreleased_match:
            content = content.rstrip() + "\n\n## [Unreleased]\n\n" + new_section
        return content, True

    section = match.group(1)
    section_lines = section.splitlines()

    marker = f"({ticket_id}"
    for idx, line in enumerate(section_lines):
        if marker in line:
            normalized_line = line.strip()
            if normalized_line == entry:
                return content, False
            section_lines[idx] = entry
            new_section = "\n".join(section_lines)
            updated = content[: match.start(1)] + new_section + content[match.end(1):]
            return updated, True

    insert_index = 1
    if len(section_lines) > 1 and section_lines[1].strip() == "":
        insert_index = 2
    section_lines.insert(insert_index, entry)
    new_section = "\n".join(section_lines)
    updated = content[: match.start(1)] + new_section + content[match.end(1):]
    return updated, True


def _update_last_updated(content: str, date_str: str) -> Tuple[str, bool]:
    pattern = re.compile(r"(_Last updated:\s*)(\d{4}-\d{2}-\d{2})(_)")
    if pattern.search(content):
        new_content = pattern.sub(rf"\g<1>{date_str}\3", content, count=1)
        return new_content, new_content != content

    # Fallback: inject immediately after the [Unreleased] header when the marker is missing.
    unreleased_header = "## [Unreleased]"
    marker = "_Last updated:"
    if unreleased_header in content and marker not in content:
        replacement = f"{unreleased_header}\n_Last updated: {date_str}_"
        new_content = content.replace(unreleased_header, replacement, 1)
        return new_content, True

    return content, False


def _maybe_commit_and_push(changelog_path: str, entry_preview: str) -> None:
    if os.getenv("SMART_CHANGELOG_SKIP_COMMIT") == "1":
        LOGGER.info("SMART_CHANGELOG_SKIP_COMMIT=1; skipping git commit and push")
        return

    if not _git_available():
        LOGGER.debug("Git not available; skipping auto commit")
        return

    try:
        subprocess.run(["git", "add", changelog_path], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as exc:
        LOGGER.warning("Failed to stage %s: %s", changelog_path, exc)
        return

    diff_check = subprocess.run(["git", "diff", "--cached", "--quiet"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if diff_check.returncode == 0:
        LOGGER.debug("No staged changes detected after update; skipping commit")
        return

    commit_message = "chore: update changelog [skip ci]"
    commit_env = os.environ.copy()
    commit_env.setdefault("GIT_AUTHOR_NAME", os.getenv("CI_COMMIT_AUTHOR", "SmartChangelog Bot"))
    commit_env.setdefault("GIT_AUTHOR_EMAIL", os.getenv("CI_COMMIT_AUTHOR_EMAIL", "bot@example.com"))
    commit_env.setdefault("GIT_COMMITTER_NAME", commit_env["GIT_AUTHOR_NAME"])
    commit_env.setdefault("GIT_COMMITTER_EMAIL", commit_env["GIT_AUTHOR_EMAIL"])

    try:
        subprocess.run(["git", "commit", "-m", commit_message], check=True, env=commit_env)
        LOGGER.info("Committed changelog update")
    except subprocess.CalledProcessError as exc:
        LOGGER.warning("Failed to commit changelog update: %s", exc)
        return

    branch = _current_branch()
    if not branch:
        LOGGER.warning("Unable to determine current branch; skipping git push")
        return

    try:
        subprocess.run(["git", "push", "origin", branch], check=True)
        LOGGER.info("Pushed changelog update to origin/%s", branch)
    except subprocess.CalledProcessError as exc:
        LOGGER.warning("Failed to push changelog update: %s", exc)


def _git_available() -> bool:
    return subprocess.call(["git", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0


def _git_output(command: list[str]) -> Optional[str]:
    try:
        result = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError:
        return None
    return result.stdout.strip()


def _current_branch() -> Optional[str]:
    env_candidates = [
        os.getenv("GITHUB_REF_NAME"),
        os.getenv("GITHUB_HEAD_REF"),
        os.getenv("CI_COMMIT_BRANCH"),
        os.getenv("CI_DEFAULT_BRANCH"),
    ]
    for candidate in env_candidates:
        if candidate:
            return candidate

    branch = _git_output(["git", "symbolic-ref", "--short", "HEAD"])
    if branch:
        return branch

    return None


def _fallback_ticket_identifier() -> str:
    commit_sha = _git_output(["git", "rev-parse", "--short", "HEAD"])
    if commit_sha:
        return f"CHANGE-{commit_sha}"
    return "CHANGE-NOREF"


def _first_non_empty(values: list[str]) -> str:
    for value in values:
        if value and value.strip():
            return value.strip()
    return ""


__all__ = ["run_update"]

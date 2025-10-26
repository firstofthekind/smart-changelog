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
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover - optional dependency path
    yaml = None  # type: ignore[assignment]

try:
    from jinja2 import Template
except ImportError:  # pragma: no cover - optional dependency fallback
    Template = None  # type: ignore[assignment]

from .ai_helper import enhance_description, suggest_category
from .jira_client import get_ticket_summary

LOGGER = logging.getLogger(__name__)

TICKET_PATTERN = re.compile(r"([A-Z][A-Z0-9]+-\d+)")
SECTION_HEADINGS = {
    "feature": "### 🧩 New Features",
    "fix": "### 🐛 Bug Fixes",
    "change": "### ⚙️ Changes",
}

SECTION_MARKERS = {
    key: {
        "start": f"<!-- section:{key} -->",
        "end": f"<!-- /section:{key} -->",
    }
    for key in SECTION_HEADINGS
}


def _read_template_text(filename: str) -> str:
    """Read a template asset, tolerating namespace package execution."""
    try:
        return resources.files("smart_changelog.templates").joinpath(filename).read_text(encoding="utf-8")
    except Exception:
        fallback_path = Path(__file__).parent / "templates" / filename
        return fallback_path.read_text(encoding="utf-8")


def _section_definitions() -> List[Dict[str, Any]]:
    """Builds metadata for each changelog section in render order."""
    definitions: List[Dict[str, Any]] = []
    for key, heading in SECTION_HEADINGS.items():
        definitions.append(
            {
                "key": key,
                "heading": heading.replace("### ", "").strip(),
                "start_marker": SECTION_MARKERS[key]["start"],
                "end_marker": SECTION_MARKERS[key]["end"],
            }
        )
    return definitions


def _load_version_template_text() -> str:
    """Loads the version block template, respecting SMART_CHANGELOG_TEMPLATE."""
    custom_template = os.getenv("SMART_CHANGELOG_TEMPLATE")
    if custom_template:
        try:
            return Path(custom_template).read_text(encoding="utf-8")
        except Exception as exc:  # pragma: no cover - filesystem variance
            LOGGER.warning("Failed to load SMART_CHANGELOG_TEMPLATE '%s': %s", custom_template, exc)

    return _read_template_text("version_block.md.j2")


def _normalise_block(block: str) -> str:
    """Normalise leading/trailing whitespace to ease comparisons and replacements."""
    trimmed = block.strip("\n")
    return trimmed + "\n"


def _render_version_block(
    version: str,
    date_str: str,
    entries_by_section: Optional[Dict[str, List[str]]] = None,
) -> str:
    """Render a version block using the configured template."""
    entries = entries_by_section or {}
    sections: List[Dict[str, Any]] = []
    for definition in _section_definitions():
        sections.append(
            {
                **definition,
                "entries": entries.get(definition["key"], []),
            }
        )

    template_text = _load_version_template_text()
    if Template is not None:
        rendered = Template(template_text).render(version=version, date=date_str, sections=sections)
    else:  # pragma: no cover - fallback when jinja2 missing
        rendered = _render_version_block_fallback(version, date_str, sections)

    return _normalise_block(rendered)


def _render_version_block_fallback(version: str, date_str: str, sections: List[Dict[str, Any]]) -> str:
    lines: List[str] = [f"## {version}", f"_Last updated: {date_str}_", ""]
    for section in sections:
        lines.append(section["start_marker"])
        entries = section.get("entries") or []
        if entries:
            lines.append(f"### {section['heading']}")
            lines.append("")
            lines.extend(entries)
            lines.append("")
        lines.append(section["end_marker"])
        lines.append("")

    return "\n".join(lines)


def _parse_version_block(block: str) -> Dict[str, Any]:
    """Parse an existing version block into its metadata and entries."""
    lines = block.strip().splitlines()
    version = ""
    if lines and lines[0].startswith("## "):
        version = lines[0][3:].strip()

    date_match = re.search(r"_Last updated:\s*(\d{4}-\d{2}-\d{2})_", block)
    date = date_match.group(1) if date_match else ""

    sections: Dict[str, List[str]] = {}
    for definition in _section_definitions():
        key = definition["key"]
        start_marker = definition["start_marker"]
        end_marker = definition["end_marker"]
        start_idx = block.find(start_marker)
        end_idx = block.find(end_marker)

        entries: List[str] = []
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            segment = block[start_idx + len(start_marker) : end_idx]
            entries = _extract_entries_from_segment(segment)
        else:
            entries = _extract_entries_from_heading(block, SECTION_HEADINGS[key])

        sections[key] = entries

    return {"version": version, "date": date, "sections": sections}


def _extract_entries_from_segment(segment: str) -> List[str]:
    """Extract entry lines from a templated section segment."""
    lines = segment.splitlines()
    cleaned = _strip_non_entry_lines(lines)
    return cleaned


def _extract_entries_from_heading(block: str, heading: str) -> List[str]:
    """Fallback parser for legacy headings without template markers."""
    pattern = re.compile(rf"{re.escape(heading)}\n(.*?)(?=\n### |\n## |\Z)", re.DOTALL)
    match = pattern.search(block)
    if not match:
        return []
    lines = match.group(1).splitlines()
    cleaned = _strip_non_entry_lines(lines)
    return cleaned


def _strip_non_entry_lines(lines: List[str]) -> List[str]:
    """Trim blank lines and remove heading remnants from a section."""
    trimmed = list(lines)
    while trimmed and not trimmed[0].strip():
        trimmed.pop(0)
    if trimmed and trimmed[0].lstrip().startswith("###"):
        trimmed.pop(0)
    while trimmed and not trimmed[0].strip():
        trimmed.pop(0)
    while trimmed and not trimmed[-1].strip():
        trimmed.pop()
    return [line.rstrip() for line in trimmed if line.strip()]


def _replace_version_block(content: str, version_heading: str, new_block: str) -> Tuple[str, bool]:
    """Replace an existing version block with freshly rendered content."""
    match = _find_version_block(content, version_heading)
    if not match:
        LOGGER.warning("Version block '%s' not found", version_heading)
        return content, False

    existing = _normalise_block(match.group(1))
    replacement = _normalise_block(new_block)
    if existing == replacement:
        return content, False

    updated = content[: match.start(1)] + replacement + content[match.end(1):]
    return updated, True


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

    version = _current_version()
    version_heading = f"## {version}"
    date_str = datetime.utcnow().date().isoformat()

    changelog_text, version_created = _ensure_version_block(changelog_text, version_heading, date_str)

    context_strings = _gather_context_strings()
    existing_ids = _extract_existing_ids(changelog_text)
    ticket_id = _detect_ticket_id(forced_ticket, context_strings)

    commit_title = _git_output(["git", "log", "-1", "--pretty=%s"]) or ""
    author = _detect_author()

    contexts: List[UpdateContext] = []

    if ticket_id:
        LOGGER.info("Fetching Jira summary for %s", ticket_id)
        jira_summary = get_ticket_summary(ticket_id)
        title = jira_summary.get("title") or commit_title or ticket_id
        if use_ai:
            LOGGER.info("Using OpenAI to enhance title for %s", ticket_id)
            title = enhance_description(title, ticket_id)
        category = _resolve_category(
            commit_title,
            use_ai=use_ai,
            ticket_title=title,
            ticket_labels=jira_summary.get("labels"),
            ticket_status=jira_summary.get("status"),
        )
        contexts.append(
            UpdateContext(
                ticket_id=ticket_id,
                category=category,
                title=title,
                author=author,
                date=date_str,
            )
        )
    else:
        LOGGER.info("No Jira ticket detected; gathering commit history")
        fallback_contexts = _contexts_from_commit_history(existing_ids, use_ai)
        if not fallback_contexts:
            fallback_id = _fallback_ticket_identifier()
            fallback_title = commit_title or _first_non_empty(context_strings) or "Unspecified change"
            if use_ai:
                LOGGER.info("Using OpenAI to enhance fallback title for %s", fallback_id)
                fallback_title = enhance_description(fallback_title, fallback_id)
            fallback_category = _resolve_category(
                commit_title,
                use_ai=use_ai,
                ticket_title=fallback_title,
                ticket_labels=None,
                ticket_status=None,
            )
            fallback_contexts = [
                UpdateContext(
                    ticket_id=fallback_id,
                    category=fallback_category,
                    title=fallback_title,
                    author=author,
                    date=date_str,
                )
            ]
            LOGGER.info("Proceeding without Jira ticket; using fallback identifier %s", fallback_contexts[0].ticket_id)
        contexts.extend(fallback_contexts)

    changes_made = version_created
    last_entry = ""
    changelog_updated = changelog_text

    for ctx in contexts:
        heading = SECTION_HEADINGS.get(ctx.category, SECTION_HEADINGS["change"])
        LOGGER.info("Updating changelog for %s (category: %s)", ctx.ticket_id, ctx.category)
        entry = ctx.render_entry()
        changelog_updated, entry_changed = _upsert_entry_for_version(
            changelog_updated,
            version_heading,
            heading,
            entry,
            ctx.ticket_id,
        )
        existing_ids.add(ctx.ticket_id)
        changes_made = changes_made or entry_changed
        last_entry = entry

    changelog_updated, date_changed = _update_last_updated(changelog_updated, version_heading, date_str)
    changes_made = changes_made or date_changed

    if not changes_made:
        LOGGER.info("Changelog already up to date")
        return

    if dry_run:
        LOGGER.info("Dry-run enabled; not writing any files")
        print(changelog_updated)
        return

    changelog_path.write_text(changelog_updated, encoding="utf-8")
    LOGGER.debug("CHANGELOG.md updated on disk")

    if contexts:
        _maybe_commit_and_push("CHANGELOG.md", last_entry)


def _read_changelog(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")

    LOGGER.info("CHANGELOG.md not found; bootstrapping from template")
    template_text = _read_template_text("changelog_template.md")
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


def _categorize_from_labels(labels: Optional[List[str]]) -> Optional[str]:
    if not labels:
        return None

    for label in labels:
        normalized = label.lower()
        if "bug" in normalized or normalized.startswith("fix"):
            return "fix"
        if "feature" in normalized or normalized.startswith("feat"):
            return "feature"
        if any(token in normalized for token in ["chore", "maintenance", "refactor", "doc", "change", "improv", "enhanc"]):
            return "change"
    return None


def _resolve_category(
    commit_title: str,
    *,
    use_ai: bool,
    ticket_title: Optional[str] = None,
    ticket_labels: Optional[List[str]] = None,
    ticket_status: Optional[str] = None,
) -> str:
    base_category = _categorize(commit_title)

    label_category = _categorize_from_labels(ticket_labels)
    if label_category:
        base_category = label_category

    if not use_ai:
        return base_category

    context_parts: List[str] = []
    if ticket_title:
        context_parts.append(f"Ticket title: {ticket_title}")
    if commit_title:
        context_parts.append(f"Commit title: {commit_title}")
    if ticket_status:
        context_parts.append(f"Ticket status: {ticket_status}")
    if ticket_labels:
        context_parts.append(f"Labels: {', '.join(ticket_labels)}")

    if not context_parts and commit_title:
        context_parts.append(commit_title)

    context_blob = "\n".join(context_parts)
    ai_category = suggest_category(context_blob) if context_blob else None
    if ai_category in SECTION_HEADINGS:
        return ai_category

    return base_category


def _detect_author() -> str:
    env_candidates = ["CI_COMMIT_AUTHOR", "GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"]
    for key in env_candidates:
        value = os.getenv(key)
        if value:
            return value

    author = _git_output(["git", "log", "-1", "--pretty=%an"]) or ""
    return author or "Unknown"


def _current_version(manifest_path: Optional[Path] = None) -> str:
    path = manifest_path or Path("manifest.yaml")
    if not path.exists():
        LOGGER.warning("manifest.yaml not found; defaulting version 0.0")
        return "0.0"

    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # pragma: no cover - read issues
        LOGGER.warning("Failed to read manifest.yaml: %s", exc)
        return "0.0"

    data: Dict[str, Any]
    if yaml is not None:
        try:
            data = yaml.safe_load(text) or {}
        except Exception as exc:  # pragma: no cover - malformed manifest
            LOGGER.warning("Failed to parse manifest.yaml: %s", exc)
            return "0.0"
    else:  # pragma: no cover - fallback path
        data = _parse_manifest_without_yaml(text)

    version = (data or {}).get("version", {}) or {}
    major = version.get("major")
    minor = version.get("minor")
    prerelease = version.get("prerelease") or ""

    if major is None or minor is None:
        LOGGER.warning("Manifest missing major/minor version; defaulting to 0.0")
        return "0.0"

    version_str = f"{major}.{minor}"
    if prerelease:
        version_str = f"{version_str}-{prerelease}"
    return str(version_str)


def _parse_manifest_without_yaml(text: str) -> Dict[str, Any]:  # pragma: no cover - minimal fallback
    result: Dict[str, Any] = {}
    version: Dict[str, Any] = {}
    in_version = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("version:"):
            in_version = True
            continue
        if in_version and ":" in line:
            key, value = [part.strip() for part in line.split(":", 1)]
            if value.startswith("\"") and value.endswith("\""):
                value = value[1:-1]
            elif value.isdigit():
                value = int(value)
            version[key] = value
    result["version"] = version
    return result


def _ensure_version_block(content: str, version_heading: str, date_str: str) -> Tuple[str, bool]:
    if _find_version_block(content, version_heading):
        return content, False

    version = version_heading.replace("##", "", 1).strip()
    block = _render_version_block(version, date_str)
    if not block.endswith("\n\n"):
        block = block + "\n"

    unreleased_pattern = re.compile(r"## \[Unreleased\].*?(?=\n## |\Z)", re.DOTALL)
    match = unreleased_pattern.search(content)
    if match:
        content = content[: match.start()] + block + content[match.end():].lstrip("\n")
        return content, True

    if content.strip() == "# Changelog" or not content.strip():
        base = content.rstrip()
        if not base:
            new_content = block
        else:
            new_content = base + "\n\n" + block
        return new_content, True

    header_match = re.search(r"^# .*?(\n|\Z)", content)
    insert_at = header_match.end() if header_match else 0
    new_content = content[:insert_at] + "\n" + block + content[insert_at:]
    return new_content, True


def _find_version_block(content: str, version_heading: str) -> Optional[re.Match[str]]:
    pattern = re.compile(rf"({re.escape(version_heading)}\n(?:.*?))(?=\n## |\Z)", re.DOTALL)
    return pattern.search(content)


def _extract_existing_ids(content: str) -> Set[str]:
    return set(re.findall(r"(CHANGE-[A-Za-z0-9]+)", content))


def _contexts_from_commit_history(existing_ids: Set[str], use_ai: bool, limit: int = 50) -> List[UpdateContext]:
    log_output = _git_output(
        ["git", "log", f"--pretty=format:%H%x09%s%x09%an%x09%cs", "-n", str(limit)]
    )
    if not log_output:
        return []

    new_contexts: List[UpdateContext] = []
    for line in log_output.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        full_sha, subject, author, commit_date = parts
        ticket_id = f"CHANGE-{_short_sha(full_sha)}"
        if ticket_id in existing_ids:
            break

        title = subject.strip() or full_sha[:12]
        if use_ai:
            LOGGER.info("Using OpenAI to enhance commit title for %s", ticket_id)
            title = enhance_description(title, ticket_id)
        category = _resolve_category(
            subject,
            use_ai=use_ai,
            ticket_title=title,
            ticket_labels=None,
            ticket_status=None,
        )

        new_contexts.append(
            UpdateContext(
                ticket_id=ticket_id,
                category=category,
                title=title,
                author=author.strip() or "Unknown",
                date=commit_date,
            )
        )

    new_contexts.reverse()
    return new_contexts


def _upsert_entry_for_version(
    content: str,
    version_heading: str,
    category_heading: str,
    entry: str,
    ticket_id: str,
) -> Tuple[str, bool]:
    version_match = _find_version_block(content, version_heading)
    if not version_match:
        LOGGER.warning("Version block '%s' not found", version_heading)
        return content, False

    block = version_match.group(1)
    parsed = _parse_version_block(block)
    sections = {key: list(values) for key, values in parsed["sections"].items()}

    section_key = next((key for key, heading in SECTION_HEADINGS.items() if heading == category_heading), "change")
    section_entries = sections.get(section_key, [])

    marker = f"({ticket_id}"
    for idx, line in enumerate(section_entries):
        if marker in line:
            if line.strip() == entry:
                return content, False
            section_entries[idx] = entry
            break
    else:
        section_entries.insert(0, entry)

    sections[section_key] = section_entries

    version_value = parsed.get("version") or version_heading.replace("##", "", 1).strip()
    date_value = parsed.get("date", "")
    new_block = _render_version_block(version_value, date_value, sections)
    updated_content, changed = _replace_version_block(content, version_heading, new_block)
    return updated_content, changed


def _update_last_updated(content: str, version_heading: str, date_str: str) -> Tuple[str, bool]:
    match = _find_version_block(content, version_heading)
    if not match:
        return content, False

    block = match.group(1)
    pattern = re.compile(r"(_Last updated:\s*)(\d{4}-\d{2}-\d{2})(_)")
    if pattern.search(block):
        updated_block = pattern.sub(rf"\g<1>{date_str}\3", block, count=1)
    else:
        lines = block.splitlines()
        if len(lines) >= 2 and lines[1].startswith("_Last updated:"):
            lines[1] = f"_Last updated: {date_str}_"
            updated_block = "\n".join(lines)
        else:
            updated_block = block.replace(version_heading, f"{version_heading}\n_Last updated: {date_str}_", 1)

    if updated_block == block:
        return content, False

    updated_content = content[: match.start(1)] + updated_block + content[match.end(1):]
    return updated_content, True


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


def _short_sha(full_sha: str) -> str:
    return (full_sha or "nohash")[:7]


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

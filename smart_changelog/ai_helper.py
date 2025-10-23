"""Utilities for enriching changelog entries with OpenAI."""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency path
    OpenAI = None  # type: ignore

LOGGER = logging.getLogger(__name__)


def enhance_description(title: str, ticket_id: str) -> str:
    """Optionally enhance the changelog line using OpenAI.

    When the OpenAI client is unavailable or misconfigured the original title is returned unchanged.
    """

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        LOGGER.debug("OPENAI_API_KEY not set; skipping AI enrichment")
        return title

    if OpenAI is None:
        LOGGER.warning("openai package not installed; cannot use AI enrichment")
        return title

    client = OpenAI(api_key=api_key)
    prompt = (
        "You are helping to craft terse changelog entries. "
        "Rewrite the following Jira ticket title so it is a single concise release-note sentence "
        "without losing important context. Avoid markdown or bullet prefixes.\n"
        f"Ticket: {ticket_id}\nTitle: {title}"
    )

    try:
        response = client.responses.create(
            model="gpt-4o-mini",
            input=prompt,
            max_output_tokens=120,
        )
    except Exception as exc:  # pragma: no cover - network/runtime issues
        LOGGER.warning("OpenAI request failed for %s: %s", ticket_id, exc)
        return title

    enriched = _first_text(response)
    if not enriched:
        LOGGER.debug("OpenAI returned empty response for %s", ticket_id)
        return title

    return enriched.strip()


def _first_text(response: Any) -> Optional[str]:  # type: ignore[name-defined]
    """Extract the first textual output from an OpenAI response."""

    # The responses API tends to expose a convenient ``output_text`` attribute. Fall back to scanning
    # the structured data if needed to stay compatible across SDK versions.
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text

    data = getattr(response, "data", None)
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "output_text":
                        value = block.get("text") or block.get("value")
                        if isinstance(value, str) and value.strip():
                            return value
    return None


__all__ = ["enhance_description"]

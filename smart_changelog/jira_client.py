"""Thin wrapper around the Jira REST API used by Smart Changelog."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List

try:
    import requests
except ImportError:  # pragma: no cover - optional dependency fallback
    requests = None  # type: ignore[assignment]

LOGGER = logging.getLogger(__name__)


@dataclass
class JiraTicket:
    """Represents the distilled metadata needed for a changelog entry."""

    title: str
    status: str | None = None
    labels: List[str] | None = None

    def as_dict(self) -> Dict[str, Any]:
        return {"title": self.title, "status": self.status, "labels": self.labels or []}


def get_ticket_summary(ticket_id: str, *, jira_url: str | None = None, token: str | None = None) -> Dict[str, Any]:
    """Retrieve the Jira ticket summary information.

    Parameters
    ----------
    ticket_id:
        Ticket identifier (e.g. PROJ-123).
    jira_url:
        Optional override for the Jira base URL. Defaults to the ``JIRA_URL`` environment variable.
    token:
        Optional override for the Jira API token. Defaults to the ``JIRA_TOKEN`` environment variable.

    Returns
    -------
    dict
        Dictionary containing ``title``, ``status``, and ``labels`` keys. When the ticket cannot be
        retrieved the title falls back to the ticket identifier and the other fields are empty.
    """

    jira_url = jira_url or os.getenv("JIRA_URL")
    token = token or os.getenv("JIRA_TOKEN")

    if not jira_url or not token:
        LOGGER.debug("Jira credentials missing; using ticket id as title")
        return JiraTicket(title=ticket_id).as_dict()

    if requests is None:
        LOGGER.warning("requests library not available; returning basic ticket info")
        return JiraTicket(title=ticket_id).as_dict()

    url = f"{jira_url.rstrip('/')}/rest/api/3/issue/{ticket_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except requests.HTTPError as exc:  # type: ignore[redundant-except]
        if exc.response is not None and exc.response.status_code == 404:
            LOGGER.warning("Jira ticket %s not found (404)", ticket_id)
        else:
            LOGGER.warning("Failed to fetch Jira ticket %s: %s", ticket_id, exc)
        return JiraTicket(title=ticket_id).as_dict()
    except requests.RequestException as exc:
        LOGGER.warning("Network error while fetching Jira ticket %s: %s", ticket_id, exc)
        return JiraTicket(title=ticket_id).as_dict()

    try:
        data = response.json()
    except ValueError:
        LOGGER.warning("Invalid JSON while decoding Jira ticket %s", ticket_id)
        return JiraTicket(title=ticket_id).as_dict()

    fields: Dict[str, Any] = data.get("fields", {}) or {}
    summary = fields.get("summary") or ticket_id
    status = None
    if isinstance(fields.get("status"), dict):
        status = fields["status"].get("name")
    labels: List[str] = []
    if isinstance(fields.get("labels"), list):
        labels = [label for label in fields["labels"] if isinstance(label, str)]

    return JiraTicket(title=summary, status=status, labels=labels).as_dict()


__all__ = ["get_ticket_summary", "JiraTicket"]

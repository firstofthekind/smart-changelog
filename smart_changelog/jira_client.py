"""Thin wrapper around the Jira REST API used by Smart Changelog."""
from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover - optional dependency path
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


def get_ticket_summary(
    ticket_id: str,
    *,
    jira_url: str | None = None,
    token: str | None = None,
) -> Dict[str, Any]:
    """Retrieve the Jira ticket summary information.

    Supports either a bearer token (``JIRA_TOKEN``) or the email/token pair (``JIRA_EMAIL`` +
    ``JIRA_API_TOKEN``) commonly used with Atlassian Cloud.
    """

    jira_url = jira_url or os.getenv("JIRA_URL")
    bearer_token = token or os.getenv("JIRA_TOKEN")
    email = os.getenv("JIRA_EMAIL")
    api_token = os.getenv("JIRA_API_TOKEN")

    if not jira_url:
        LOGGER.debug("JIRA_URL not provided; falling back to raw ticket id")
        return JiraTicket(title=ticket_id).as_dict()

    if requests is None:
        LOGGER.warning("requests library not available; returning basic ticket info")
        return JiraTicket(title=ticket_id).as_dict()

    headers = _build_auth_headers(bearer_token, email, api_token)
    if headers is None:
        LOGGER.debug("Jira credentials missing; using ticket id as title")
        return JiraTicket(title=ticket_id).as_dict()

    url = f"{jira_url.rstrip('/')}/rest/api/3/issue/{ticket_id}"
    LOGGER.debug("Requesting Jira issue %s from %s", ticket_id, url)

    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except requests.HTTPError as exc:  # type: ignore[redundant-except]
        not_found = exc.response is not None and exc.response.status_code == 404
        message = "Jira ticket %s not found (404)" if not_found else "Failed to fetch Jira ticket %s: %s"
        args: tuple[Any, ...] = (ticket_id,) if not_found else (ticket_id, exc)
        LOGGER.warning(message, *args)
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


def _build_auth_headers(
    bearer_token: Optional[str],
    email: Optional[str],
    api_token: Optional[str],
) -> Optional[Dict[str, str]]:
    """Construct authorization headers for Jira requests."""

    if email and api_token:
        token_bytes = f"{email}:{api_token}".encode("utf-8")
        encoded = base64.b64encode(token_bytes).decode("ascii")
        LOGGER.debug("Using Jira basic authentication via email/API token")
        return {
            "Authorization": f"Basic {encoded}",
            "Accept": "application/json",
        }

    if bearer_token:
        LOGGER.debug("Using Jira bearer token authentication")
        return {
            "Authorization": f"Bearer {bearer_token}",
            "Accept": "application/json",
        }

    return None


__all__ = ["get_ticket_summary", "JiraTicket"]

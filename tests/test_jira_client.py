import os
import unittest
from unittest import mock

from smart_changelog import jira_client


class JiraClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_missing_credentials_returns_ticket_id(self) -> None:
        os.environ.pop("JIRA_URL", None)
        os.environ.pop("JIRA_TOKEN", None)
        result = jira_client.get_ticket_summary("ABC-123")
        self.assertEqual(result["title"], "ABC-123")
        self.assertEqual(result["labels"], [])

    def test_missing_credentials_with_url_returns_ticket_id(self) -> None:
        os.environ["JIRA_URL"] = "https://example.atlassian.net"
        os.environ.pop("JIRA_TOKEN", None)
        os.environ.pop("JIRA_EMAIL", None)
        os.environ.pop("JIRA_API_TOKEN", None)
        dummy = _dummy_requests(None)
        dummy.get = mock.Mock(side_effect=AssertionError("should not call"))
        with mock.patch.object(jira_client, "requests", dummy):
            result = jira_client.get_ticket_summary("ABC-126")
        self.assertEqual(result["title"], "ABC-126")

    def test_requests_not_available(self) -> None:
        os.environ["JIRA_URL"] = "https://example.atlassian.net"
        os.environ["JIRA_TOKEN"] = "token"
        original = jira_client.requests
        try:
            jira_client.requests = None
            result = jira_client.get_ticket_summary("ABC-124")
        finally:
            jira_client.requests = original

        self.assertEqual(result["title"], "ABC-124")
        self.assertEqual(result["labels"], [])

    def test_basic_auth_headers_used(self) -> None:
        os.environ["JIRA_URL"] = "https://example.atlassian.net"
        os.environ["JIRA_EMAIL"] = "user@example.com"
        os.environ["JIRA_API_TOKEN"] = "apitoken"
        os.environ.pop("JIRA_TOKEN", None)

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"fields": {"summary": "Summary"}}

        dummy = _dummy_requests(FakeResponse())

        with mock.patch.object(jira_client, "requests", dummy):
            jira_client.get_ticket_summary("ABC-200")

        headers = dummy.get.call_args.kwargs["headers"]
        self.assertTrue(headers["Authorization"].startswith("Basic "))

    def test_bearer_auth_headers_used(self) -> None:
        os.environ["JIRA_URL"] = "https://example.atlassian.net"
        os.environ["JIRA_TOKEN"] = "token"
        os.environ.pop("JIRA_EMAIL", None)
        os.environ.pop("JIRA_API_TOKEN", None)

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"fields": {"summary": "Summary"}}

        dummy = _dummy_requests(FakeResponse())

        with mock.patch.object(jira_client, "requests", dummy):
            jira_client.get_ticket_summary("ABC-201")

        headers = dummy.get.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer token")

    def test_successful_response(self) -> None:
        os.environ["JIRA_URL"] = "https://example.atlassian.net"
        os.environ["JIRA_TOKEN"] = "token"

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "fields": {
                        "summary": "Implement feature",
                        "status": {"name": "In Progress"},
                        "labels": ["backend", "high-priority"],
                    }
                }

        dummy = _dummy_requests(FakeResponse())

        with mock.patch.object(jira_client, "requests", dummy):
            data = jira_client.get_ticket_summary("ABC-125")

        self.assertEqual(data["title"], "Implement feature")
        self.assertEqual(data["status"], "In Progress")
        self.assertEqual(data["labels"], ["backend", "high-priority"])
        dummy.get.assert_called_once()

    def test_ticket_not_found(self) -> None:
        os.environ["JIRA_URL"] = "https://example.atlassian.net"
        os.environ["JIRA_TOKEN"] = "token"

        class FakeResponse:
            status_code = 404

            def raise_for_status(self):
                raise dummy.HTTPError(response=self)  # type: ignore[arg-type]

        dummy = _dummy_requests(FakeResponse(), status_exception=True)

        with mock.patch.object(jira_client, "requests", dummy):
            data = jira_client.get_ticket_summary("ABC-404")

        self.assertEqual(data["title"], "ABC-404")
        self.assertEqual(data["labels"], [])

    def test_http_error_non_404(self) -> None:
        os.environ["JIRA_URL"] = "https://example.atlassian.net"
        os.environ["JIRA_TOKEN"] = "token"

        class FakeResponse:
            status_code = 500

            def raise_for_status(self):
                raise dummy.HTTPError(response=self)  # type: ignore[arg-type]

        dummy = _dummy_requests(FakeResponse(), status_exception=True)

        with mock.patch.object(jira_client, "requests", dummy):
            data = jira_client.get_ticket_summary("ABC-500")

        self.assertEqual(data["title"], "ABC-500")

    def test_request_exception(self) -> None:
        os.environ["JIRA_URL"] = "https://example.atlassian.net"
        os.environ["JIRA_TOKEN"] = "token"

        dummy = _dummy_requests(None)
        dummy.get = mock.Mock(side_effect=dummy.RequestException("timeout"))

        with mock.patch.object(jira_client, "requests", dummy):
            result = jira_client.get_ticket_summary("ABC-500")

        self.assertEqual(result["title"], "ABC-500")

    def test_invalid_json(self) -> None:
        os.environ["JIRA_URL"] = "https://example.atlassian.net"
        os.environ["JIRA_TOKEN"] = "token"

        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                raise ValueError("invalid json")

        dummy = _dummy_requests(FakeResponse())

        with mock.patch.object(jira_client, "requests", dummy):
            result = jira_client.get_ticket_summary("ABC-JSON")

        self.assertEqual(result["title"], "ABC-JSON")

    def test_build_auth_headers_none(self) -> None:
        self.assertIsNone(jira_client._build_auth_headers(None, None, None))


def _dummy_requests(response, status_exception: bool = False):
    class DummyRequests:
        class HTTPError(Exception):
            def __init__(self, response=None):
                super().__init__("http error")
                self.response = response

        class RequestException(Exception):
            pass

    dummy = DummyRequests()
    if response is not None:
        dummy.get = mock.Mock(return_value=response)
        if status_exception:
            def raise_error(self):
                raise dummy.HTTPError(response=self)  # type: ignore[misc]

            response.raise_for_status = raise_error.__get__(response, response.__class__)  # type: ignore[attr-defined]
        else:
            response.raise_for_status = lambda self=response: None  # type: ignore[assignment]
    else:
        dummy.get = mock.Mock()
    return dummy


if __name__ == "__main__":
    unittest.main()

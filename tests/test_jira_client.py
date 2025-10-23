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

        class DummyRequests:
            class HTTPError(Exception):
                def __init__(self, response=None):
                    super().__init__("http error")
                    self.response = response

        dummy = DummyRequests()
        dummy.get = mock.Mock(return_value=FakeResponse())

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

        class DummyRequests:
            class HTTPError(Exception):
                def __init__(self, response=None):
                    super().__init__("http error")
                    self.response = response

            class RequestException(Exception):
                pass

        dummy = DummyRequests()
        dummy.get = mock.Mock(return_value=FakeResponse())

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

        class DummyRequests:
            class HTTPError(Exception):
                def __init__(self, response=None):
                    super().__init__("http error")
                    self.response = response

            class RequestException(Exception):
                pass

        dummy = DummyRequests()
        dummy.get = mock.Mock(return_value=FakeResponse())

        with mock.patch.object(jira_client, "requests", dummy):
            data = jira_client.get_ticket_summary("ABC-500")

        self.assertEqual(data["title"], "ABC-500")

    def test_request_exception(self) -> None:
        os.environ["JIRA_URL"] = "https://example.atlassian.net"
        os.environ["JIRA_TOKEN"] = "token"

        class DummyRequests:
            class HTTPError(Exception):
                pass

            class RequestException(Exception):
                pass

        dummy = DummyRequests()
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

        class DummyRequests:
            class HTTPError(Exception):
                pass

            class RequestException(Exception):
                pass

        dummy = DummyRequests()
        dummy.get = mock.Mock(return_value=FakeResponse())

        with mock.patch.object(jira_client, "requests", dummy):
            result = jira_client.get_ticket_summary("ABC-JSON")

        self.assertEqual(result["title"], "ABC-JSON")


if __name__ == "__main__":
    unittest.main()

import os
import unittest
from unittest import mock

from smart_changelog import ai_helper


class AIHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = os.environ.copy()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_enhance_description_without_api_key_returns_original(self) -> None:
        result = ai_helper.enhance_description("Original", "ABC-1")
        self.assertEqual(result, "Original")

    def test_enhance_description_without_openai_package(self) -> None:
        os.environ["OPENAI_API_KEY"] = "dummy"
        original = ai_helper.OpenAI
        try:
            ai_helper.OpenAI = None
            result = ai_helper.enhance_description("Ticket title", "ABC-2")
        finally:
            ai_helper.OpenAI = original

        self.assertEqual(result, "Ticket title")

    def test_enhance_description_happy_path(self) -> None:
        os.environ["OPENAI_API_KEY"] = "dummy"

        class FakeResponse:
            output_text = "Refined text"

        class FakeClient:
            def __init__(self, api_key: str) -> None:
                self.api_key = api_key

            class responses:
                @staticmethod
                def create(model: str, input: str, max_output_tokens: int):
                    return FakeResponse()

        with mock.patch.object(ai_helper, "OpenAI", FakeClient):
            self.assertEqual(
                ai_helper.enhance_description("Raw title", "ABC-3"),
                "Refined text",
            )

    def test_enhance_description_handles_exception(self) -> None:
        os.environ["OPENAI_API_KEY"] = "dummy"

        class ExplodingClient:
            def __init__(self, api_key: str) -> None:
                self.api_key = api_key

            class responses:
                @staticmethod
                def create(*args, **kwargs):
                    raise RuntimeError("boom")

        with mock.patch.object(ai_helper, "OpenAI", ExplodingClient):
            result = ai_helper.enhance_description("Raw title", "ABC-4")

        self.assertEqual(result, "Raw title")

    def test_enhance_description_handles_empty_response(self) -> None:
        os.environ["OPENAI_API_KEY"] = "dummy"

        class EmptyResponse:
            output_text = ""
            data = []

        class EmptyClient:
            def __init__(self, api_key: str) -> None:
                self.api_key = api_key

            class responses:
                @staticmethod
                def create(*args, **kwargs):
                    return EmptyResponse()

        with mock.patch.object(ai_helper, "OpenAI", EmptyClient):
            result = ai_helper.enhance_description("Raw title", "ABC-5")

        self.assertEqual(result, "Raw title")

    def test_first_text_extracts_from_structured_payload(self) -> None:
        class Response:
            output_text = ""
            data = [
                {
                    "content": [
                        {"type": "output_text", "text": "Structured response"},
                    ]
                }
            ]

        extracted = ai_helper._first_text(Response())
        self.assertEqual(extracted, "Structured response")

    def test_first_text_none_path(self) -> None:
        class Response:
            output_text = ""
            data = [{"content": []}]

        self.assertIsNone(ai_helper._first_text(Response()))


if __name__ == "__main__":
    unittest.main()

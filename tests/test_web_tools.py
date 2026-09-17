import unittest
from unittest.mock import patch

from tests.external_stubs import install

install()

from tools import web_tools


class ValidateUrlTests(unittest.TestCase):
    def test_rejects_non_http_schemes(self):
        """scrape_website is reachable from tool calls the model chooses, so the
        scheme check is the boundary that keeps it off the local filesystem and
        internal protocols."""
        for url in (
            "file:///etc/passwd",
            "ftp://example.com/x",
            "gopher://example.com",
            "javascript:alert(1)",
            "data:text/html,<script>",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    web_tools._validate_url(url)

    def test_rejects_url_without_hostname(self):
        with self.assertRaises(ValueError):
            web_tools._validate_url("http://")

    def test_accepts_http_and_https(self):
        for url in ("http://example.com", "https://example.com/path?q=1"):
            with self.subTest(url=url):
                self.assertEqual(web_tools._validate_url(url), url)


class ScrapeWebsiteTests(unittest.TestCase):
    def test_invalid_scheme_returns_status_instead_of_raising(self):
        result = web_tools.scrape_website(url="file:///etc/passwd")

        self.assertEqual(result["content"], "")
        self.assertIn("invalid url", result["status"])

    def test_strips_html_tags_and_truncates(self):
        class FakeResponse:
            text = "<html><body><p>Acme  builds\n\nsoftware</p></body></html>"

            def raise_for_status(self):
                return None

        with patch.object(web_tools.requests, "get", return_value=FakeResponse()):
            result = web_tools.scrape_website(url="https://acme.example")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["content"], "Acme builds software")

    def test_truncates_to_max_content_chars(self):
        class FakeResponse:
            text = "x" * (web_tools._MAX_CONTENT_CHARS * 2)

            def raise_for_status(self):
                return None

        with patch.object(web_tools.requests, "get", return_value=FakeResponse()):
            result = web_tools.scrape_website(url="https://acme.example")

        self.assertEqual(len(result["content"]), web_tools._MAX_CONTENT_CHARS)


class WebSearchTests(unittest.TestCase):
    def test_missing_api_key_returns_error_payload(self):
        with patch.object(web_tools.os, "getenv", return_value=None):
            result = web_tools.web_search(query="acme")

        self.assertEqual(result["results"], [])
        self.assertIn("TAVILY_API_KEY", result["error"])

    def test_empty_query_after_sanitization_is_rejected(self):
        with patch.object(web_tools.os, "getenv", return_value="tvly-test-key"):
            result = web_tools.web_search(query="\x00\x1f  ")

        self.assertEqual(result["error"], "Empty search query.")

    def test_max_results_is_clamped(self):
        captured = {}

        class FakeClient:
            def __init__(self, api_key):
                pass

            def search(self, **kwargs):
                captured.update(kwargs)
                return {"answer": "", "results": []}

        with patch.object(web_tools.os, "getenv", return_value="tvly-test-key"):
            with patch.object(web_tools, "TavilyClient", FakeClient):
                web_tools.web_search(query="acme", max_results=99)

        self.assertEqual(captured["max_results"], 10)


if __name__ == "__main__":
    unittest.main()

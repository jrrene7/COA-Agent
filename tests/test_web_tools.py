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


class _FakeResponse:
    """Stands in for requests.Response, including the redirect flags."""

    is_redirect = False
    is_permanent_redirect = False

    def __init__(self, text="", status=200, location=None):
        self.text = text
        self.status_code = status
        self.headers = {"Location": location} if location else {}
        if location:
            self.is_redirect = True

    def raise_for_status(self):
        return None


class _Recorder:
    """Captures each URL requests.get is called with, replying from a script."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.urls = []

    def __call__(self, url, **kwargs):
        self.urls.append(url)
        assert kwargs.get("allow_redirects") is False, "redirects must be followed manually"
        return self.responses.pop(0)


def _public_dns(*addresses):
    """Patch resolution so tests never depend on real DNS."""
    return patch.object(web_tools, "_resolve_host", lambda host, port: list(addresses))


class SsrfTests(unittest.TestCase):
    """The model chooses these URLs, so the address check is the real boundary."""

    def test_blocks_cloud_metadata_endpoint(self):
        with self.assertRaisesRegex(ValueError, "link-local"):
            web_tools._validate_url("http://169.254.169.254/latest/meta-data/")

    def test_blocks_loopback(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            web_tools._validate_url("http://127.0.0.1/admin")

    def test_blocks_ipv6_loopback(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            web_tools._validate_url("http://[::1]/")

    def test_blocks_private_ranges(self):
        for url in ("http://10.0.0.5/", "http://192.168.1.1/", "http://172.16.0.1/"):
            with self.subTest(url=url):
                with self.assertRaisesRegex(ValueError, "private"):
                    web_tools._validate_url(url)

    def test_blocks_hostname_that_resolves_private(self):
        """A public-looking name pointing at RFC1918 space is the common bypass."""
        with _public_dns("10.1.2.3"):
            with self.assertRaisesRegex(ValueError, "private"):
                web_tools._validate_url("https://internal.acme.example/")

    def test_blocks_ipv4_mapped_ipv6_loopback(self):
        with _public_dns("::ffff:127.0.0.1"):
            with self.assertRaisesRegex(ValueError, "loopback"):
                web_tools._validate_url("https://acme.example/")

    def test_blocks_when_any_resolved_address_is_private(self):
        """One public answer must not launder a private one."""
        with _public_dns("93.184.216.34", "10.0.0.1"):
            with self.assertRaises(ValueError):
                web_tools._validate_url("https://acme.example/")

    def test_unresolvable_host_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "could not resolve"):
            web_tools._validate_url("https://nx.invalid.example/")

    def test_allows_public_address(self):
        with _public_dns("93.184.216.34"):
            self.assertEqual(
                web_tools._validate_url("https://acme.example/"), "https://acme.example/"
            )


class ScrapeWebsiteTests(unittest.TestCase):
    def test_invalid_scheme_returns_status_instead_of_raising(self):
        result = web_tools.scrape_website(url="file:///etc/passwd")

        self.assertEqual(result["content"], "")
        self.assertIn("invalid url", result["status"])

    def test_private_address_returns_status_instead_of_raising(self):
        result = web_tools.scrape_website(url="http://169.254.169.254/")

        self.assertEqual(result["content"], "")
        self.assertIn("invalid url", result["status"])

    def test_strips_html_tags_and_truncates(self):
        getter = _Recorder(_FakeResponse("<html><body><p>Acme  builds\n\nsoftware</p></body></html>"))
        with _public_dns("93.184.216.34"), patch.object(web_tools.requests, "get", getter):
            result = web_tools.scrape_website(url="https://acme.example")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["content"], "Acme builds software")

    def test_truncates_to_max_content_chars(self):
        getter = _Recorder(_FakeResponse("x" * (web_tools._MAX_CONTENT_CHARS * 2)))
        with _public_dns("93.184.216.34"), patch.object(web_tools.requests, "get", getter):
            result = web_tools.scrape_website(url="https://acme.example")

        self.assertEqual(len(result["content"]), web_tools._MAX_CONTENT_CHARS)

    def test_redirect_to_a_private_address_is_blocked(self):
        """The bypass the scheme check never saw: a public page redirecting inward."""
        getter = _Recorder(
            _FakeResponse(location="http://169.254.169.254/latest/meta-data/"),
            _FakeResponse("secrets"),
        )
        with patch.object(web_tools, "_resolve_host",
                          lambda host, port: ["93.184.216.34"] if host == "acme.example"
                          else ["169.254.169.254"]), \
             patch.object(web_tools.requests, "get", getter):
            result = web_tools.scrape_website(url="https://acme.example")

        self.assertEqual(result["content"], "")
        self.assertIn("invalid url", result["status"])
        # It must never have issued the second request.
        self.assertEqual(len(getter.urls), 1)

    def test_redirect_to_a_public_address_is_followed(self):
        getter = _Recorder(
            _FakeResponse(location="https://acme.example/final"),
            _FakeResponse("<p>Arrived</p>"),
        )
        with _public_dns("93.184.216.34"), patch.object(web_tools.requests, "get", getter):
            result = web_tools.scrape_website(url="https://acme.example")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["content"], "Arrived")
        self.assertEqual(result["url"], "https://acme.example/final")

    def test_redirect_loop_is_bounded(self):
        responses = [_FakeResponse(location="https://acme.example/next") for _ in range(10)]
        getter = _Recorder(*responses)
        with _public_dns("93.184.216.34"), patch.object(web_tools.requests, "get", getter):
            result = web_tools.scrape_website(url="https://acme.example")

        self.assertIn("redirect", result["status"])
        self.assertLessEqual(len(getter.urls), web_tools._MAX_REDIRECTS + 1)


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

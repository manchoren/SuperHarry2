"""Deterministic contract tests for mediated search-provider adapters."""

import json
import sqlite3
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from superharry.search_providers import (
    DEFAULT_MWMBL_SEARCH_ENDPOINT,
    MwmblSearchProvider,
    SearchProviderConfigurationError,
    SearchProviderRateLimited,
    SearchProviderUnavailable,
    create_search_provider,
)
from superharry.search_provider_limits import MwmblRequestLimiter

_MwmblSearchProvider = MwmblSearchProvider
_test_directories = []


class FakeClock:
    def __init__(self):
        self.value = time.time()
        self.elapsed = 0.0

    def now(self):
        return self.value

    def monotonic(self):
        return self.elapsed

    def sleep(self, delay):
        self.value += delay
        self.elapsed += delay


def response(payload, *, status=200):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return {"status": status, "headers": {}, "body": body}


def limiter(*, monthly_limit=1000):
    directory = tempfile.TemporaryDirectory()
    _test_directories.append(directory)
    clock = FakeClock()
    return MwmblRequestLimiter(
        directory.name, monthly_limit=monthly_limit, wall_clock=clock.now,
        monotonic=clock.monotonic, sleeper=clock.sleep,
    )


def _usage_attempts(limiter):
    connection = sqlite3.connect(limiter.database_path)
    try:
        return connection.execute("SELECT attempts FROM mwmbl_usage WHERE singleton=1").fetchone()[0]
    finally:
        connection.close()


def MwmblSearchProvider(transport, *args, **kwargs):
    if not args and "limiter" not in kwargs:
        kwargs["limiter"] = limiter()
    return _MwmblSearchProvider(transport, *args, **kwargs)


class SearchProviderTests(unittest.TestCase):
    def setUp(self):
        _test_directories.clear()
        self.addCleanup(lambda: [directory.cleanup() for directory in _test_directories])

    def test_mwmbl_provider_uses_injected_transport_and_normalizes_results(self):
        calls = []

        def transport(url):
            calls.append(url)
            return response({
                "query": "Django web framework",
                "number_of_results": 1,
                "results": [{
                    "title": " Django ",
                    "url": "https://www.djangoproject.com/start/?source=mwmbl#readme",
                    "content": " A Python web framework. ",
                }],
            })

        provider = create_search_provider("mwmbl", transport, limiter())
        result = provider.search(" Django web framework ")

        self.assertEqual(len(calls), 1)
        self.assertEqual(urlsplit(calls[0]).scheme, "https")
        self.assertEqual(urlsplit(calls[0]).netloc, "api.mwmbl.org")
        self.assertEqual(parse_qs(urlsplit(calls[0]).query), {"q": ["Django web framework"]})
        self.assertEqual(result[0].title, "Django")
        self.assertEqual(result[0].url, "https://www.djangoproject.com/start/?source=mwmbl")
        self.assertEqual(result[0].snippet, "A Python web framework.")

    def test_endpoint_can_be_configured_without_changing_transport(self):
        urls = []
        provider = MwmblSearchProvider(
            lambda url: urls.append(url) or response({"results": []}),
            endpoint="https://search-proxy.example/v2/search",
        )

        self.assertEqual(provider.search("query"), [])
        self.assertEqual(urlsplit(urls[0]).netloc, "search-proxy.example")
        self.assertEqual(urlsplit(urls[0]).path, "/v2/search")

    def test_default_endpoint_is_the_documented_mwmbl_endpoint(self):
        provider = MwmblSearchProvider(lambda url: response({"results": []}))
        self.assertEqual(provider.endpoint, DEFAULT_MWMBL_SEARCH_ENDPOINT)

    def test_empty_results_are_a_successful_empty_search(self):
        provider = MwmblSearchProvider(lambda url: response({"results": []}))
        self.assertEqual(provider.search("a query"), [])

    def test_only_bounded_http_results_are_returned_and_duplicates_are_removed(self):
        items = [
            {"title": "one", "url": "https://example.test/one", "content": "first"},
            {"title": "duplicate", "url": "https://example.test/one", "content": "duplicate"},
            {"title": "script", "url": "javascript:alert(1)", "content": "bad"},
            {"title": "credentials", "url": "https://user:pass@example.test/", "content": "bad"},
            {"title": "port", "url": "https://example.test:444/", "content": "bad"},
            {"title": "two", "url": "https://example.test/two", "content": "second"},
        ]
        provider = MwmblSearchProvider(lambda url: response({"results": items}), max_results=2)

        result = provider.search("a query")

        self.assertEqual([item.url for item in result], [
            "https://example.test/one", "https://example.test/two",
        ])

    def test_invalid_query_is_rejected_before_transport(self):
        calls = []
        provider = MwmblSearchProvider(lambda url: calls.append(url) or response({"results": []}))

        for query in ("", "  ", "a\nquery", "x" * 501, None):
            with self.subTest(query=query), self.assertRaises(SearchProviderConfigurationError):
                provider.search(query)
        self.assertEqual(calls, [])

    def test_unconfigured_provider_and_unsafe_endpoint_fail_clearly(self):
        with self.assertRaises(SearchProviderConfigurationError):
            create_search_provider("unknown", lambda url: response({"results": []}), limiter())
        for endpoint in (
            "http://search.example/api",
            "https://user:pass@search.example/api",
            "https://search.example:444/api",
            "https://search.example/api?key=secret",
            "https://search.example/api#fragment",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(SearchProviderConfigurationError):
                MwmblSearchProvider(lambda url: response({"results": []}), endpoint=endpoint)

    def test_rate_limit_and_provider_errors_are_reported_without_fabricating_results(self):
        with self.assertRaisesRegex(SearchProviderRateLimited, "quota or rate limit"):
            MwmblSearchProvider(lambda url: response({}, status=429)).search("query")
        with self.assertRaisesRegex(SearchProviderUnavailable, "HTTP 503"):
            MwmblSearchProvider(lambda url: response({}, status=503)).search("query")
        with self.assertRaises(SearchProviderUnavailable):
            MwmblSearchProvider(lambda url: response(b"not-json")).search("query")
        with self.assertRaises(SearchProviderUnavailable):
            MwmblSearchProvider(lambda url: response({"results": "invalid"})).search("query")
        with self.assertRaises(SearchProviderUnavailable):
            MwmblSearchProvider(lambda url: {
                "status": 200, "headers": "invalid", "body": b'{"results": []}',
            }).search("query")

    def test_response_size_and_result_count_are_bounded(self):
        with self.assertRaisesRegex(SearchProviderUnavailable, "size limit"):
            MwmblSearchProvider(lambda url: response(b"x" * (1024 * 1024 + 1))).search("query")
        items = [
            {"title": str(i), "url": f"https://example.test/{i}", "content": ""}
            for i in range(101)
        ]
        with self.assertRaisesRegex(SearchProviderUnavailable, "too many"):
            MwmblSearchProvider(lambda url: response({"results": items})).search("query")

    def test_provider_transport_failures_are_not_silently_converted_to_empty_results(self):
        def fail(_url):
            raise TimeoutError("synthetic timeout")

        with self.assertRaisesRegex(SearchProviderUnavailable, "temporarily unavailable"):
            MwmblSearchProvider(fail).search("query")

    def test_429_and_monthly_quota_are_counted_without_retry(self):
        calls = []
        rate_limiter = limiter()
        provider = MwmblSearchProvider(
            lambda url: calls.append(url) or response({}, status=429), rate_limiter,
        )
        with self.assertRaises(SearchProviderRateLimited):
            provider.search("query")
        self.assertEqual(_usage_attempts(rate_limiter), 1)
        self.assertEqual(len(calls), 1)

        quota_limiter = limiter(monthly_limit=1)
        quota_calls = []
        quota_provider = MwmblSearchProvider(
            lambda url: quota_calls.append(url) or response({"results": []}), quota_limiter,
        )
        quota_provider.search("first")
        with self.assertRaisesRegex(SearchProviderRateLimited, "monthly request quota"):
            quota_provider.search("second")
        self.assertEqual(len(quota_calls), 1)


if __name__ == "__main__":
    unittest.main()

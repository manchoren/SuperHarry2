"""Configurable, network-agnostic adapters for public web search providers.

The caller supplies the transport so search requests can later use the same
task-scoped network mediation as page navigation. This module never opens a
socket itself.
"""

import json
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit

from superharry.search_provider_limits import (
    MwmblLimiterError,
    MwmblMonthlyQuotaExceeded,
    MwmblRequestLimiter,
)


DEFAULT_MWMBL_SEARCH_ENDPOINT = "https://api.mwmbl.org/api/v2/search/"
MAX_QUERY_LENGTH = 500
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_RESULTS = 10
MAX_PROVIDER_RESULTS = 100
MAX_TITLE_LENGTH = 300
MAX_SNIPPET_LENGTH = 4000
MAX_RESULT_URL_LENGTH = 4096


class SearchProviderError(Exception):
    """Base class for expected provider errors safe to report to a user."""


class SearchProviderConfigurationError(SearchProviderError):
    """The selected provider or its endpoint is not configured correctly."""


class SearchProviderUnavailable(SearchProviderError):
    """The provider could not return a usable response."""


class SearchProviderRateLimited(SearchProviderUnavailable):
    """The provider rejected a request because of its quota or rate limit."""


class SearchTransport(Protocol):
    def __call__(self, url: str) -> dict:
        """Return status, headers, and body after applying shared egress controls."""


@dataclass(frozen=True)
class SearchResult:
    """A bounded, untrusted result returned by a search provider."""

    title: str
    url: str
    snippet: str


class MwmblSearchProvider:
    """Adapter for Mwmbl's anonymous JSON search endpoint.

    `transport` must route the request through the caller's approved network
    boundary. It receives a fully encoded HTTPS URL and returns a response
    dictionary compatible with the host network mediator's browser response.
    This adapter dispatches through the required SQLite ledger limiter, which
    enforces Mwmbl's limits within the single host worker runtime.
    """

    name = "mwmbl"

    def __init__(
        self,
        transport: SearchTransport,
        limiter: MwmblRequestLimiter,
        *,
        endpoint: str = DEFAULT_MWMBL_SEARCH_ENDPOINT,
        max_results: int = MAX_RESULTS,
    ):
        if not callable(transport):
            raise SearchProviderConfigurationError("A mediated search transport is required.")
        if not isinstance(limiter, MwmblRequestLimiter):
            raise SearchProviderConfigurationError("A shared Mwmbl request limiter is required.")
        self.endpoint = _validate_endpoint(endpoint)
        if type(max_results) is not int or not 1 <= max_results <= MAX_RESULTS:
            raise SearchProviderConfigurationError("Search result limit must be between 1 and 10.")
        self.transport = transport
        self.limiter = limiter
        self.max_results = max_results

    def search(self, query: str) -> list[SearchResult]:
        query = _validate_query(query)
        url = f"{self.endpoint}?{urlencode({'q': query})}"
        try:
            response = self.limiter.dispatch(lambda: self.transport(url))
        except MwmblMonthlyQuotaExceeded as error:
            raise SearchProviderRateLimited(
                "Mwmbl monthly request quota has been reached. Try again next month."
            ) from error
        except MwmblLimiterError as error:
            raise SearchProviderUnavailable(
                "Mwmbl search is temporarily blocked because shared quota tracking is unavailable."
            ) from error
        except PermissionError:
            raise
        except (OSError, TimeoutError) as error:
            raise SearchProviderUnavailable("Mwmbl search is temporarily unavailable.") from error

        status, body = _validate_response(response)
        if status == 429:
            raise SearchProviderRateLimited(
                "Mwmbl search quota or rate limit was reached. Try again later."
            )
        if not 200 <= status < 300:
            raise SearchProviderUnavailable(f"Mwmbl search returned HTTP {status}.")
        if len(body) > MAX_RESPONSE_BYTES:
            raise SearchProviderUnavailable("Mwmbl search response exceeded the size limit.")

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SearchProviderUnavailable("Mwmbl returned an invalid JSON response.") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise SearchProviderUnavailable("Mwmbl returned an unexpected response format.")
        if len(payload["results"]) > MAX_PROVIDER_RESULTS:
            raise SearchProviderUnavailable("Mwmbl returned too many search results.")

        results = []
        seen_urls = set()
        for item in payload["results"]:
            result = _parse_result(item)
            if result is None or result.url in seen_urls:
                continue
            results.append(result)
            seen_urls.add(result.url)
            if len(results) >= self.max_results:
                break
        return results


def create_search_provider(
    name: str,
    transport: SearchTransport,
    limiter: MwmblRequestLimiter,
    *,
    endpoint: str | None = None,
    max_results: int = MAX_RESULTS,
) -> MwmblSearchProvider:
    """Build the configured provider without performing network I/O."""

    if not isinstance(name, str) or name.strip().lower() != MwmblSearchProvider.name:
        raise SearchProviderConfigurationError(
            "Unsupported web search provider; configure 'mwmbl'."
        )
    return MwmblSearchProvider(
        transport,
        limiter,
        endpoint=endpoint or DEFAULT_MWMBL_SEARCH_ENDPOINT,
        max_results=max_results,
    )


def validate_search_query(value: str) -> str:
    """Validate and normalize a user-provided public search query."""

    return _validate_query(value)


def _validate_endpoint(value: str) -> str:
    if not isinstance(value, str) or len(value) > MAX_RESULT_URL_LENGTH:
        raise SearchProviderConfigurationError("Mwmbl search endpoint is invalid.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise SearchProviderConfigurationError("Mwmbl search endpoint is invalid.") from error
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or port not in (None, 443)):
        raise SearchProviderConfigurationError(
            "Mwmbl search endpoint must be an HTTPS URL on the standard port without query data."
        )
    path = parsed.path or "/"
    return urlunsplit(("https", parsed.netloc, path, "", ""))


def _validate_query(value: str) -> str:
    if not isinstance(value, str):
        raise SearchProviderConfigurationError("Enter a search query as text.")
    query = value.strip()
    if (not query or len(query) > MAX_QUERY_LENGTH
            or any(ord(char) < 32 or ord(char) == 127 for char in query)):
        raise SearchProviderConfigurationError(
            f"Search query must contain 1 to {MAX_QUERY_LENGTH} printable characters."
        )
    return query


def _validate_response(response: dict) -> tuple[int, bytes]:
    if not isinstance(response, dict) or set(response) != {"status", "headers", "body"}:
        raise SearchProviderUnavailable("Search transport returned an invalid response.")
    status = response["status"]
    headers = response["headers"]
    body = response["body"]
    if (type(status) is not int or not 100 <= status <= 599
            or not isinstance(headers, dict) or not isinstance(body, bytes)):
        raise SearchProviderUnavailable("Search transport returned an invalid response.")
    return status, body


def _parse_result(item: dict) -> SearchResult | None:
    if not isinstance(item, dict):
        return None
    title = item.get("title")
    url = _canonical_result_url(item.get("url"))
    snippet = item.get("content", item.get("snippet", ""))
    if not isinstance(title, str) or not title.strip() or url is None or not isinstance(snippet, str):
        return None
    return SearchResult(
        title=title.strip()[:MAX_TITLE_LENGTH],
        url=url,
        snippet=snippet.strip()[:MAX_SNIPPET_LENGTH],
    )


def _canonical_result_url(value: str) -> str | None:
    if not isinstance(value, str) or not value or len(value) > MAX_RESULT_URL_LENGTH:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or port not in (None, 80 if parsed.scheme == "http" else 443)):
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))

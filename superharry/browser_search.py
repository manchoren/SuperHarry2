"""Host-side Mwmbl search for an already approved browser task."""

import os

from .search_provider_limits import MwmblLimiterError, configured_mwmbl_limiter
from .search_providers import create_search_provider, SearchProviderUnavailable


def search_mwmbl_urls(
    query,
    *,
    mediator,
    authorized,
    cancelled,
    environ=None,
    limiter_factory=configured_mwmbl_limiter,
):
    """Return a small ordered URL list using only the host mediator and ledger."""
    environment = os.environ if environ is None else environ
    try:
        limiter = limiter_factory(environ=environment)
    except MwmblLimiterError as error:
        raise SearchProviderUnavailable(str(error)) from error

    def mediated_get(url):
        return mediator.retrieve_browser(
            {"method": "GET", "url": url}, authorized=authorized, cancelled=cancelled,
        )

    provider = create_search_provider(
        environment.get("WEB_SEARCH_PROVIDER", "mwmbl"), mediated_get, limiter,
        endpoint=environment.get("MWMBL_SEARCH_ENDPOINT"), max_results=5,
    )
    results = provider.search(query)
    urls = [result.url for result in results]
    if not urls:
        raise SearchProviderUnavailable("Mwmbl did not return any usable public pages.")
    return urls

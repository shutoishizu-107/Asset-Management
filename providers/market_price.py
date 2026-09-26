"""Price-history adapter selection for configured source priority."""
from __future__ import annotations

from .alpha_vantage import AlphaVantageProvider
from .base import ProviderError
from .fred import FredProvider
from .nasdaq_data_link import NasdaqDataLinkProvider
from .tiingo import TiingoProvider
from .yahoo_finance import YahooFinanceDevelopmentProvider


ADAPTERS = {
    'alpha_vantage': AlphaVantageProvider,
    'tiingo': TiingoProvider,
    'nasdaq_data_link': NasdaqDataLinkProvider,
    'fred': FredProvider,
    'yahoo_finance': YahooFinanceDevelopmentProvider,
}


def create(provider_id: str):
    adapter = ADAPTERS.get(provider_id)
    if adapter is None:
        raise ProviderError('provider_adapter_unavailable')
    return adapter()
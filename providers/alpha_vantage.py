"""Alpha Vantage REST adapter. Never called unless collection is explicitly enabled."""
from __future__ import annotations

import urllib.parse
from typing import Any

from .base import Observation, ProviderError, request_json


class AlphaVantageProvider:
    provider_id = 'alpha_vantage'

    def fetch(self, provider: dict[str, Any], resource: dict[str, Any], api_key: str | None):
        if not api_key:
            raise ProviderError('api_key_unavailable')
        base = str(resource.get('base_url') or provider['base_url'])
        params = {
            'function': str(resource.get('function', 'TIME_SERIES_DAILY_ADJUSTED')),
            'symbol': str(resource.get('symbol', '')),
            'outputsize': str(resource.get('outputsize', 'compact')),
            'apikey': api_key,
        }
        if not params['symbol']:
            raise ProviderError('symbol_not_configured')
        url = base + '?' + urllib.parse.urlencode(params)
        payload, raw = request_json(url, headers={'Accept': 'application/json'}, timeout=int(resource.get('timeout_seconds', 20)))
        if isinstance(payload, dict) and ('Error Message' in payload or 'Note' in payload or 'Information' in payload):
            # Do not expose the upstream response, which may echo query details.
            raise ProviderError('upstream_rejected_request')
        series_key = resource.get('series_key')
        if not series_key:
            series_key = next((key for key in payload if 'Time Series' in key), None)
        series = payload.get(series_key) if isinstance(payload, dict) else None
        value_key = resource.get('value_key', '5. adjusted close')
        observations = []
        if isinstance(series, dict):
            for day, row in sorted(series.items()):
                value = row.get(value_key) if isinstance(row, dict) else None
                if value is None:
                    continue
                try:
                    observations.append({'date': day, 'value': float(value)})
                except (TypeError, ValueError):
                    continue
        if not observations:
            raise ProviderError('price_series_unavailable')
        observation = Observation(str(resource['symbol']), str(resource.get('metric', 'price_history')), observations, str(resource.get('unit', 'USD')), observations[-1]['date'], base, 'raw', max_staleness_days=resource.get('max_staleness_days'))
        return [observation], raw
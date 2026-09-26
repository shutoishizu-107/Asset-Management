"""Tiingo REST adapter. Raw publication is blocked by policy unless explicitly approved."""
from __future__ import annotations

from typing import Any

from .base import Observation, ProviderError, request_json


class TiingoProvider:
    provider_id = 'tiingo'

    def fetch(self, provider: dict[str, Any], resource: dict[str, Any], api_key: str | None):
        if not api_key:
            raise ProviderError('api_key_unavailable')
        ticker = str(resource.get('ticker', ''))
        if not ticker:
            raise ProviderError('ticker_not_configured')
        base = str(resource.get('base_url') or provider['base_url']).rstrip('/')
        url = base + '/' + ticker + '/prices'
        params = {}
        if resource.get('start_date'):
            params['startDate'] = str(resource['start_date'])
        if resource.get('end_date'):
            params['endDate'] = str(resource['end_date'])
        if params:
            import urllib.parse
            url += '?' + urllib.parse.urlencode(params)
        payload, raw = request_json(url, headers={'Authorization': 'Token ' + api_key, 'Accept': 'application/json'}, timeout=int(resource.get('timeout_seconds', 20)))
        if not isinstance(payload, list):
            raise ProviderError('unexpected_response_shape')
        field = str(resource.get('price_field', 'adjClose'))
        points = []
        for row in payload:
            if not isinstance(row, dict) or row.get(field) is None or row.get('date') is None:
                continue
            points.append({'date': str(row['date'])[:10], 'value': float(row[field])})
        points.sort(key=lambda row: row['date'])
        if not points:
            raise ProviderError('price_series_unavailable')
        observation = Observation(ticker, str(resource.get('metric', 'price_history')), points, str(resource.get('unit', 'USD')), points[-1]['date'], base + '/' + ticker + '/prices', 'raw', max_staleness_days=resource.get('max_staleness_days'))
        return [observation], raw
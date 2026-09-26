"""FRED observations API adapter for configured macro series."""
from __future__ import annotations

import urllib.parse
from typing import Any

from .base import Observation, ProviderError, request_json


class FredProvider:
    provider_id = 'fred'

    def fetch(self, provider: dict[str, Any], resource: dict[str, Any], api_key: str | None):
        if not api_key:
            raise ProviderError('api_key_unavailable')
        series_id = str(resource.get('series_id', ''))
        metric = str(resource.get('metric', ''))
        if not series_id or not metric:
            raise ProviderError('series_configuration_missing')
        base = str(resource.get('base_url') or provider['base_url'])
        params = {'series_id': series_id, 'api_key': api_key, 'file_type': 'json'}
        for key in ('observation_start', 'observation_end', 'frequency', 'aggregation_method', 'units'):
            if resource.get(key) is not None:
                params[key] = str(resource[key])
        url = base + '?' + urllib.parse.urlencode(params)
        payload, raw = request_json(url, headers={'Accept': 'application/json'}, timeout=int(resource.get('timeout_seconds', 20)))
        observations = payload.get('observations', []) if isinstance(payload, dict) else []
        points = []
        for row in observations:
            if row.get('value') in (None, '.'):
                continue
            try:
                points.append({'date': str(row['date']), 'value': float(row['value'])})
            except (KeyError, TypeError, ValueError):
                continue
        if not points:
            raise ProviderError('series_has_no_observations')
        observation = Observation(series_id, metric, points, resource.get('unit'), points[-1]['date'], base, 'raw', max_staleness_days=resource.get('max_staleness_days'))
        return [observation], raw
"""Nasdaq Data Link dataset adapter; license is reviewed per configured dataset."""
from __future__ import annotations

import urllib.parse
from typing import Any

from .base import Observation, ProviderError, request_json


class NasdaqDataLinkProvider:
    provider_id = 'nasdaq_data_link'

    def fetch(self, provider: dict[str, Any], resource: dict[str, Any], api_key: str | None):
        code = str(resource.get('dataset_code', ''))
        if not code:
            raise ProviderError('dataset_code_not_configured')
        if provider.get('api_key_required') and not api_key:
            raise ProviderError('api_key_unavailable')
        base = str(resource.get('base_url') or provider['base_url']).rstrip('/')
        params = dict(resource.get('params', {}))
        if api_key:
            params['api_key'] = api_key
        url = base + '/datasets/' + urllib.parse.quote(code, safe='/') + '.json'
        if params:
            url += '?' + urllib.parse.urlencode(params)
        payload, raw = request_json(url, headers={'Accept': 'application/json'}, timeout=int(resource.get('timeout_seconds', 20)))
        dataset = payload.get('dataset', {}) if isinstance(payload, dict) else {}
        columns = dataset.get('column_names', [])
        date_column = resource.get('date_column')
        value_column = resource.get('value_column')
        if not date_column or not value_column:
            raise ProviderError('dataset_columns_not_configured')
        try:
            date_index = columns.index(date_column)
            value_index = columns.index(value_column)
        except ValueError:
            raise ProviderError('configured_column_not_found') from None
        points = []
        for row in dataset.get('data', []):
            if len(row) <= max(date_index, value_index) or row[value_index] is None:
                continue
            try:
                points.append({'date': str(row[date_index])[:10], 'value': float(row[value_index])})
            except (TypeError, ValueError):
                continue
        points.sort(key=lambda row: row['date'])
        if not points:
            raise ProviderError('dataset_has_no_mapped_values')
        source_url = base + '/datasets/' + urllib.parse.quote(code, safe='/') + '.json'
        observation = Observation(str(resource.get('subject', code)), str(resource['metric']), points, resource.get('unit'), points[-1]['date'], source_url, 'raw', max_staleness_days=resource.get('max_staleness_days'))
        return [observation], raw
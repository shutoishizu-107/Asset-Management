"""Development-only local fixture reader. This module never calls Yahoo endpoints."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .base import Observation, ProviderError


class YahooFinanceDevelopmentProvider:
    provider_id = 'yahoo_finance'

    def fetch(self, provider: dict[str, Any], resource: dict[str, Any], api_key: str | None = None):
        if provider.get('provider_id', self.provider_id) != self.provider_id:
            raise ProviderError('provider_id_mismatch')
        if api_key:
            raise ProviderError('api_key_not_used_for_development_fixture')
        fixture = resource.get('fixture_path')
        if not fixture:
            raise ProviderError('development_fixture_not_configured')
        path = Path(fixture)
        if not path.is_file():
            raise ProviderError('development_fixture_unavailable')
        if path.suffix.lower() == '.json':
            raw = path.read_bytes()
            payload = json.loads(raw.decode('utf-8-sig'))
            rows = payload.get('prices', payload) if isinstance(payload, dict) else payload
        elif path.suffix.lower() == '.csv':
            raw = path.read_bytes()
            rows = list(csv.DictReader(raw.decode('utf-8-sig').splitlines()))
        else:
            raise ProviderError('fixture_must_be_csv_or_json')
        if not isinstance(rows, list):
            raise ProviderError('fixture_shape_invalid')
        field = str(resource.get('price_field', 'close'))
        points = []
        for row in rows:
            try:
                points.append({'date': str(row['date'])[:10], 'value': float(row[field])})
            except (KeyError, TypeError, ValueError):
                continue
        points.sort(key=lambda point: point['date'])
        if not points:
            raise ProviderError('fixture_has_no_price_points')
        observation = Observation(
            str(resource.get('subject', 'sample')),
            str(resource.get('metric', 'price_history')),
            points,
            resource.get('unit', 'USD'),
            points[-1]['date'],
            'fixture://' + path.name,
            'raw',
            'sample',
            resource.get('max_staleness_days'),
        )
        return [observation], raw
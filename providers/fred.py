"""FRED observations API adapter for configured macro series."""
from __future__ import annotations

import json
import urllib.parse
import urllib.error
import urllib.request
from typing import Any

from .base import HttpsRedirectHandler, Observation, ProviderError, validate_safe_source_url


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
        validate_safe_source_url(base)
        params = {'series_id': series_id, 'api_key': api_key, 'file_type': 'json'}
        for key in ('observation_start', 'observation_end', 'frequency', 'aggregation_method', 'units'):
            if resource.get(key) is not None:
                params[key] = str(resource[key])
        url = base + '?' + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={'Accept': 'application/json'}, method='GET')
        opener = urllib.request.build_opener(HttpsRedirectHandler())
        timeout = int(resource.get('timeout_seconds', 20))
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read(20_000_001)
                http_status = int(getattr(response, 'status', 200) or 200)
        except urllib.error.HTTPError as error:
            raise ProviderError(f'http_status_{error.code}') from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ProviderError('network_error') from None
        if len(raw) > 20_000_000:
            raise ProviderError('response_too_large')
        try:
            payload = json.loads(raw.decode('utf-8-sig'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderError('invalid_json') from None
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
        subject = str(resource.get('subject') or series_id)
        if metric == 'risk_free_rate':
            latest = points[-1]
            annual_rate = float(latest['value'])
            # FRED Treasury/Fed series are commonly annual percent values.
            if str(resource.get('value_scale') or 'percent_annualized') == 'percent_annualized':
                annual_rate /= 100.0
            observation = Observation(
                subject=subject,
                metric=metric,
                value=annual_rate,
                unit=str(resource.get('unit') or 'annual_fraction'),
                as_of=latest['date'],
                source_url=base,
                data_class='raw',
                data_origin='real',
                max_staleness_days=resource.get('max_staleness_days'),
                report_period=str(resource.get('report_period') or 'monthly'),
            )
            return [observation], raw, {'http_status': http_status}
        observation = Observation(
            subject,
            metric,
            points,
            resource.get('unit'),
            points[-1]['date'],
            base,
            'raw',
            max_staleness_days=resource.get('max_staleness_days'),
            report_period=resource.get('report_period'),
        )
        return [observation], raw, {'http_status': http_status}
"""Provider protocol, safe HTTPS transport, and structured-resource adapter."""
from __future__ import annotations

import csv
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any


class ProviderError(Exception):
    """Expected upstream, configuration, or parsing error without secret data."""

    def __init__(self, code: str):
        if not code.replace('_', '').isalnum() or code.lower() != code:
            raise ValueError('ProviderError requires a lowercase identifier code')
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Observation:
    subject: str
    metric: str
    value: Any
    unit: str | None
    as_of: str | None
    source_url: str
    data_class: str = 'raw'
    data_origin: str = 'real'
    max_staleness_days: int | None = None
    report_period: str | None = None
    metadata_type: str | None = None
    series_type: str | None = None
    frequency: str | None = None
    currency: str | None = None


class HttpsRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        if urllib.parse.urlsplit(new_url).scheme.lower() != 'https':
            raise ProviderError('insecure_redirect_blocked')
        return super().redirect_request(request, response, code, message, headers, new_url)


def validate_safe_source_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() != 'https' or not parsed.hostname:
        raise ProviderError('https_url_required')
    if parsed.username is not None or parsed.password is not None:
        raise ProviderError('credentials_in_source_url_forbidden')
    query_keys = {key.lower() for key, _ in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)}
    if query_keys & {'api_key', 'apikey', 'token', 'access_token', 'key'}:
        raise ProviderError('credential_in_source_url_forbidden')


def request_bytes(url: str, headers: dict[str, str] | None = None, timeout: int = 20, max_bytes: int = 20_000_000) -> bytes:
    validate_safe_source_url(url)
    request = urllib.request.Request(url, headers=headers or {}, method='GET')
    opener = urllib.request.build_opener(HttpsRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            final = urllib.parse.urlsplit(response.geturl())
            if final.scheme.lower() != 'https':
                raise ProviderError('insecure_final_url_blocked')
            payload = response.read(max_bytes + 1)
    except urllib.error.HTTPError as error:
        raise ProviderError(f'http_status_{error.code}') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProviderError('network_error') from None
    if len(payload) > max_bytes:
        raise ProviderError('response_too_large')
    return payload


def request_json(url: str, headers: dict[str, str] | None = None, timeout: int = 20) -> tuple[Any, bytes]:
    validate_safe_source_url(url)
    raw = request_bytes(url, headers=headers, timeout=timeout)
    try:
        return json.loads(raw.decode('utf-8-sig')), raw
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProviderError('invalid_json') from None


def request_csv(url: str, headers: dict[str, str] | None = None, timeout: int = 20) -> tuple[list[dict[str, str]], bytes]:
    validate_safe_source_url(url)
    raw = request_bytes(url, headers=headers, timeout=timeout)
    try:
        text = raw.decode('utf-8-sig')
        if text.lstrip().lower().startswith(('<!doctype html', '<html')):
            raise ProviderError('structured_response_is_html')
        rows = list(csv.DictReader(io.StringIO(text)))
    except (UnicodeDecodeError, csv.Error):
        raise ProviderError('invalid_csv') from None
    if not rows:
        raise ProviderError('empty_csv')
    return rows, raw


def iso_date(value: Any) -> str | None:
    if value in (None, '', '.', 'null'):
        return None
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


class ConfiguredStructuredProvider:
    """Read only configured CSV/JSON downloads. It never scrapes HTML."""
    provider_id = ''

    def fetch(self, provider: dict[str, Any], resource: dict[str, Any], api_key: str | None = None) -> tuple[list[Observation], bytes]:
        url = resource.get('url')
        if not url:
            raise ProviderError('resource_url_not_configured')
        fmt = resource.get('format')
        if fmt == 'csv':
            rows, raw = request_csv(url, headers=self.headers(provider, api_key), timeout=int(resource.get('timeout_seconds', 20)))
        elif fmt == 'json':
            payload, raw = request_json(url, headers=self.headers(provider, api_key), timeout=int(resource.get('timeout_seconds', 20)))
            rows = self.json_rows(payload, resource)
        else:
            raise ProviderError('structured_format_required')
        observations = []
        mapping = resource.get('columns', {})
        metric = mapping.get('metric')
        series_groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            as_of = iso_date(row.get(mapping.get('as_of', 'as_of')))
            value_column = mapping.get('value')
            if not as_of or not value_column or not metric:
                continue
            raw_value = row.get(value_column)
            if raw_value in (None, ''):
                continue
            try:
                parsed_number = Decimal(str(raw_value).replace(',', '').strip())
                if not parsed_number.is_finite():
                    continue
                if metric == 'holdings' and resource.get('weight_unit') == 'percent':
                    parsed_number /= Decimal(100)
                scale = resource.get('value_scale')
                if scale == 'million':
                    parsed_number *= Decimal(1_000_000)
                elif scale == 'billion':
                    parsed_number *= Decimal(1_000_000_000)
                value: Any = format(parsed_number.normalize(), 'f')
            except InvalidOperation:
                value = raw_value
            subject = str(row.get(mapping.get('subject', 'subject'), resource.get('subject', 'unconfigured')))
            common = dict(
                subject=subject,
                metric=str(metric),
                unit=mapping.get('unit'),
                source_url=url,
                data_class='raw',
                data_origin='real',
                max_staleness_days=resource.get('max_staleness_days', provider.get('max_staleness_days')),
                report_period=row.get(mapping.get('report_period', 'report_period')),
            )
            if metric == 'holdings':
                security_id = row.get(mapping.get('security_id', 'security_id'))
                if security_id in (None, ''):
                    continue
                security_name = str(row.get(mapping.get('security_name', 'security_name')) or security_id).strip()
                normalized_name = security_name.upper()
                if normalized_name.startswith(('REGION::', 'SECTOR::', 'ASSET::')) or any(token in security_name.lower() for token in ('region', 'sector', 'allocation', 'exposure')):
                    continue
                complete = resource.get('holdings_complete') is True
                observations.append(Observation(value={
                    'security_name': security_name,
                    'security_id': str(security_id),
                    'ticker': row.get(mapping.get('ticker', 'ticker')) or None,
                    'isin': row.get(mapping.get('isin', 'isin')) or None,
                    'cusip': row.get(mapping.get('cusip', 'cusip')) or None,
                    'weight': value,
                    'complete': complete,
                }, as_of=as_of, **common))
            elif metric in {'price_history', 'nav'}:
                series_groups.setdefault(subject, []).append({'date': as_of, 'value': value})
            else:
                observations.append(Observation(value=value, as_of=as_of, **common))
        for subject, points in series_groups.items():
            points.sort(key=lambda point: point['date'])
            observations.append(Observation(
                subject=subject,
                metric=str(metric),
                value=points,
                unit=mapping.get('unit'),
                as_of=points[-1]['date'],
                source_url=url,
                data_class='raw',
                data_origin='real',
                max_staleness_days=resource.get('max_staleness_days', provider.get('max_staleness_days')),
                report_period=resource.get('report_period'),
            ))
        if not observations:
            raise ProviderError('no_mapped_observations')
        return observations, raw

    def headers(self, provider: dict[str, Any], api_key: str | None) -> dict[str, str]:
        headers = {'Accept': 'text/csv, application/json'}
        key_header = provider.get('api_key_header')
        if api_key and key_header:
            headers[str(key_header)] = str(provider.get('api_key_header_prefix', '')) + api_key
        return headers

    def json_rows(self, payload: Any, resource: dict[str, Any]) -> list[dict[str, Any]]:
        path = resource.get('rows_path')
        value = payload
        if path:
            for part in path.split('.'):
                value = value[part] if isinstance(value, dict) else None
        if not isinstance(value, list):
            raise ProviderError('json_rows_mapping_required')
        return value
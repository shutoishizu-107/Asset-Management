"""iShares product-page facts and discovered holdings downloads."""
from __future__ import annotations

import csv
import html
import io
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .base import Observation, ProviderError, request_bytes, validate_safe_source_url


class IsharesProvider:
    provider_id = 'ishares'

    def fetch(self, provider: dict[str, Any], resource: dict[str, Any], api_key: str | None = None):
        _ = provider, api_key
        product_url = str(resource.get('url') or '')
        validate_safe_source_url(product_url)
        page_raw = request_bytes(product_url, headers={'Accept': 'text/html,application/xhtml+xml'})
        page_text = page_raw.decode('utf-8-sig', errors='strict')
        if not self._looks_html(page_text):
            raise ProviderError('product_page_not_html')
        metric = str(resource.get('metric') or '')
        subject = str(resource.get('subject') or resource.get('entity_id') or '')
        if metric in {'expense_ratio', 'aum', 'inception_date', 'benchmark'}:
            observation = self._fact_observation(page_text, subject, metric, product_url)
            if observation is None:
                raise ProviderError('parser_schema_mismatch')
            return [observation], page_raw
        if metric in {'number_of_holdings', 'holdings'}:
            download_url = self._holdings_download_url(page_text)
            if not download_url:
                raise ProviderError('holdings_download_not_discovered')
            raw = request_bytes(download_url, headers={'Accept': 'text/csv,text/plain'})
            observations, count = self._parse_holdings(raw, subject, download_url)
            if metric == 'number_of_holdings':
                if count == 0:
                    raise ProviderError('holdings_schema_mismatch')
                return [Observation(subject=subject, metric=metric, value=count, unit='count', as_of=observations[0].as_of, source_url=download_url)], raw
            return observations, raw
        raise ProviderError('provider_metric_not_supported')

    @staticmethod
    def _looks_html(text: str) -> bool:
        return text.lstrip().lower().startswith(('<!doctype html', '<html', '<head', '<body'))

    @staticmethod
    def _jsonld_documents(text: str) -> list[Any]:
        documents = []
        for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', text, re.I | re.S):
            try:
                documents.append(json.loads(html.unescape(match.group(1).strip())))
            except json.JSONDecodeError:
                continue
        return documents

    @classmethod
    def _walk_dicts(cls, value: Any):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from cls._walk_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from cls._walk_dicts(child)

    @classmethod
    def _properties(cls, text: str) -> dict[str, dict[str, Any]]:
        properties = {}
        for document in cls._jsonld_documents(text):
            for item in cls._walk_dicts(document):
                additional = item.get('additionalProperty')
                if not isinstance(additional, list):
                    continue
                for prop in additional:
                    if isinstance(prop, dict) and prop.get('name'):
                        properties[str(prop['name']).strip().lower()] = prop
        return properties

    @classmethod
    def _fact_observation(cls, text: str, subject: str, metric: str, source_url: str) -> Observation | None:
        properties = cls._properties(text)
        names = {
            'expense_ratio': ('expense ratio:', 'expense ratio'),
            'aum': ('net assets of fund',),
            'inception_date': ('fund inception',),
            'benchmark': ('benchmark index',),
        }[metric]
        prop = next((properties.get(name) for name in names if properties.get(name)), None)
        if not prop:
            return None
        raw_value = prop.get('value')
        reference = prop.get('valueReference') if isinstance(prop.get('valueReference'), dict) else {}
        reference_date = reference.get('value') if str(reference.get('name', '')).lower() == 'as of dates' else None
        if metric == 'expense_ratio':
            try:
                value = format(Decimal(str(raw_value).replace('%', '').replace(',', '')) / Decimal(100), 'f')
            except (InvalidOperation, TypeError):
                return None
            as_of = cls._parse_date(reference_date)
            return Observation(subject=subject, metric=metric, value=value, unit='fraction', as_of=as_of, metadata_type='static' if as_of is None else None, report_period='prospectus' if as_of is None else None, source_url=source_url)
        if metric == 'aum':
            try:
                value = format(Decimal(re.sub(r'[^0-9.-]', '', str(raw_value))).normalize(), 'f')
            except (InvalidOperation, TypeError):
                return None
            as_of = cls._parse_date(reference_date)
            return Observation(subject=subject, metric=metric, value=value, unit='USD', as_of=as_of, source_url=source_url) if as_of else None
        if metric == 'inception_date':
            value = cls._parse_date(raw_value)
            return Observation(subject=subject, metric=metric, value=value, unit='date', as_of=None, metadata_type='static', source_url=source_url) if value else None
        value = str(raw_value or '').strip()
        return Observation(subject=subject, metric=metric, value=value, unit='name', as_of=None, metadata_type='static', source_url=source_url) if value else None

    @classmethod
    def _holdings_download_url(cls, text: str) -> str | None:
        for document in cls._jsonld_documents(text):
            for item in cls._walk_dicts(document):
                if item.get('@type') == 'DataDownload' and 'holdings' in str(item.get('name', '')).lower():
                    url = item.get('contentUrl')
                    if isinstance(url, str) and url.startswith('https://'):
                        return url
        return None

    @classmethod
    def _parse_holdings(cls, raw: bytes, subject: str, source_url: str) -> tuple[list[Observation], int]:
        text = raw.decode('utf-8-sig', errors='strict')
        if cls._looks_html(text):
            raise ProviderError('structured_response_is_html')
        lines = text.splitlines()
        header_index = next((index for index, line in enumerate(lines) if 'Ticker' in line and 'Name' in line and 'Weight (%)' in line), None)
        if header_index is None:
            raise ProviderError('holdings_schema_mismatch')
        reader = csv.DictReader(io.StringIO('\n'.join(lines[header_index:])))
        if not {'Ticker', 'Name', 'Weight (%)'} <= set(reader.fieldnames or []):
            raise ProviderError('holdings_schema_mismatch')
        match = re.search(r'Fund Holdings as of,?"?([^"\r\n]+)', '\n'.join(lines[:header_index]), re.I)
        as_of = cls._parse_date(match.group(1)) if match else None
        if not as_of:
            raise ProviderError('holdings_as_of_missing')
        observations = []
        total = 0.0
        for row in reader:
            name = str(row.get('Name') or '').strip()
            ticker = str(row.get('Ticker') or '').strip()
            try:
                weight = float(str(row.get('Weight (%)') or '').replace(',', '')) / 100
            except (TypeError, ValueError):
                continue
            if not name or not ticker or not 0 <= weight <= 1:
                continue
            normalized = name.upper()
            if normalized.startswith(('REGION::', 'SECTOR::', 'ASSET::')) or any(token in name.lower() for token in ('region', 'sector', 'allocation', 'exposure')):
                continue
            total += weight
            observations.append(Observation(subject=subject, metric='holdings', value={'security_name': name, 'ticker': ticker, 'weight': format(weight, 'f'), 'asset_class': row.get('Asset Class') or None, 'complete': False}, unit='fraction', as_of=as_of, source_url=source_url, report_period=as_of))
        complete = abs(total - 1.0) <= 0.03
        observations = [Observation(**{**observation.__dict__, 'value': {**observation.value, 'complete': complete}}) for observation in observations]
        return observations, len(observations)

    @staticmethod
    def _parse_date(value: Any) -> str | None:
        if value in (None, ''):
            return None
        text = str(value).strip()
        for fmt in ('%b %d, %Y', '%B %d, %Y', '%Y-%m-%d'):
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                continue
        return None
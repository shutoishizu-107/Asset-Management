"""Cache-first provider orchestration with stale fallback and immutable metadata."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from .policy import CachePolicy


def _iso_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (TypeError, ValueError):
        raise ValueError('invalid_datetime') from None
    if parsed.tzinfo is None:
        raise ValueError('timezone_required')
    return parsed.astimezone(timezone.utc)


def _has_value(value: Any, zero_is_missing: bool) -> bool:
    if value is None or value == '':
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float, Decimal)):
        if isinstance(value, float) and not math.isfinite(value):
            return False
        return not (zero_is_missing and value == 0)
    if isinstance(value, str):
        try:
            number = Decimal(value.strip())
        except InvalidOperation:
            return bool(value.strip())
        return number.is_finite() and not (zero_is_missing and number == 0)
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


def _safe_entity(entity_id: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,99}', entity_id):
        raise ValueError('entity_id must be a safe 1-100 character identifier')
    return entity_id


@dataclass(frozen=True)
class CacheResult:
    status: str
    entity_id: str
    metric: str
    provider_id: str
    entry: dict[str, Any] | None
    age_days: int | None
    reason: str | None = None


class CacheManager:
    """Store one entity file with nested metric/provider entries.

    fetcher() returns a normalized mapping with data, source_url, and as_of.
    Provider/network details remain outside this cache manager.
    """
    def __init__(self, root: Path, policy: CachePolicy, clock: Callable[[], datetime] | None = None):
        self.root = root
        self.policy = policy
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def cache_path(self, entity_id: str, metric: str) -> Path:
        entity_id = _safe_entity(entity_id)
        if metric.startswith('index_') or metric in {'benchmark', 'index_composition', 'index_region_weights', 'index_sector_weights', 'methodology'}:
            namespace = 'indexes'
        elif metric in {'fed_funds_rate', 'treasury_yields', 'cpi', 'unemployment', 'recession_indicators', 'macro'}:
            namespace = 'macro'
        elif metric in {'nisa_eligibility', 'nisa_rules'}:
            namespace = 'nisa'
        else:
            namespace = 'funds'
        return self.root / 'cache' / namespace / f'{entity_id}.json'

    def _read_entry(self, path: Path, entity_id: str, metric: str, provider_id: str) -> tuple[dict[str, Any] | None, str | None]:
        if not path.exists():
            return None, None
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
            if payload.get('schema_version') != 1 or payload.get('entity_id') != entity_id:
                raise ValueError('cache_schema_or_entity_mismatch')
            entry = payload['metrics'][metric]['providers'][provider_id]
            self._validate_entry(entry, entity_id, metric, provider_id)
            return entry, None
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            return None, str(error) if str(error) == 'cache_schema_or_entity_mismatch' else 'cache_malformed'

    @staticmethod
    def _validate_entry(entry: dict[str, Any], entity_id: str, metric: str, provider_id: str) -> None:
        required = {
            'schema_version', 'entity_id', 'metric', 'provider_id', 'provider', 'source_url',
            'fetched_at', 'as_of', 'expires_at', 'stale', 'license_status', 'cache_allowed', 'data',
        }
        if not required <= entry.keys() or entry.get('schema_version') != 1:
            raise ValueError('cache_malformed')
        if entry.get('entity_id') != entity_id or entry.get('metric') != metric or entry.get('provider_id') != provider_id:
            raise ValueError('cache_malformed')
        if not entry.get('provider') or not entry.get('source_url') or entry.get('data') is None:
            raise ValueError('cache_malformed')
        _iso_datetime(entry['fetched_at'])
        _iso_datetime(entry['expires_at'])
        if not isinstance(entry.get('as_of'), str) or not entry['as_of'].strip():
            raise ValueError('cache_malformed')

    def _write_entry(self, path: Path, entity_id: str, metric: str, provider_id: str, entry: dict[str, Any]) -> None:
        payload: dict[str, Any]
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
                if payload.get('schema_version') != 1 or payload.get('entity_id') != entity_id:
                    payload = {'schema_version': 1, 'entity_id': entity_id, 'metrics': {}}
            except (OSError, json.JSONDecodeError, AttributeError):
                payload = {'schema_version': 1, 'entity_id': entity_id, 'metrics': {}}
        else:
            payload = {'schema_version': 1, 'entity_id': entity_id, 'metrics': {}}
        payload.setdefault('metrics', {}).setdefault(metric, {'providers': {}}).setdefault('providers', {})[provider_id] = entry
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        temporary.replace(path)

    def get(
        self,
        entity_id: str,
        metric: str,
        provider: dict[str, Any],
        fetcher: Callable[[], dict[str, Any]],
        *,
        offline: bool = False,
        force_refresh: bool = False,
        persist_cache: bool = True,
    ) -> CacheResult:
        entity_id = _safe_entity(entity_id)
        provider_id = provider.get('provider_id')
        if not isinstance(provider_id, str) or not provider_id:
            raise ValueError('provider_id is required')
        ttl = self.policy.ttl_days(metric)
        path = self.cache_path(entity_id, metric)
        cached, cache_error = self._read_entry(path, entity_id, metric, provider_id)
        now = self.clock().astimezone(timezone.utc)
        cache_permitted = (
            provider.get('cache_allowed') is True
            and provider.get('license_status') in {'verified', 'approved'}
        )
        if not cache_permitted:
            cached = None
            cache_error = 'cache_not_permitted'
        if cached is not None:
            expires_at = _iso_datetime(cached['expires_at'])
            age_days = max(0, (now.date() - _iso_datetime(cached['fetched_at']).date()).days)
            if now < expires_at and not force_refresh:
                fresh = dict(cached, stale=False, fetch_status='cache_hit', error_message=None)
                return CacheResult('fresh', entity_id, metric, provider_id, fresh, age_days)
        else:
            age_days = None

        if offline:
            if cached is not None:
                stale = dict(cached, stale=True, fetch_status='offline_stale_fallback', error_message=None)
                return CacheResult('stale', entity_id, metric, provider_id, stale, age_days, 'offline_cache_expired')
            reason = cache_error or 'offline_cache_miss'
            return CacheResult('unavailable', entity_id, metric, provider_id, None, None, reason)

        fetch_error: str | None = None
        try:
            fetched = fetcher()
            data = fetched.get('data')
            if not _has_value(data, self.policy.zero_is_missing):
                raise ValueError('provider_returned_empty_or_zero_value')
            source_url = fetched.get('source_url')
            as_of = fetched.get('as_of')
            if not source_url or not as_of:
                raise ValueError('provider_missing_source_url_or_as_of')
            fetched_at = now.isoformat(timespec='seconds')
            entry = {
                'schema_version': 1,
                'entity_id': entity_id,
                'metric': metric,
                'provider_id': provider_id,
                'provider': str(fetched.get('provider') or provider.get('provider_name') or provider_id),
                'provider_version': fetched.get('provider_version', provider.get('provider_version')),
                'source_url': str(source_url),
                'fetched_at': fetched_at,
                'as_of': str(as_of),
                'expires_at': (now + timedelta(days=ttl)).isoformat(timespec='seconds'),
                'stale': False,
                'license_status': str(fetched.get('license_status') or provider.get('license_status', 'unknown')),
                'cache_allowed': provider.get('cache_allowed', False) is True,
                'raw_data_publication_allowed': provider.get('raw_data_publication_allowed', False),
                'derived_data_publication_allowed': provider.get('derived_data_publication_allowed', False),
                'fetch_status': 'success',
                'error_message': None,
                'last_successful_fetch_at': fetched_at,
                'content_hash': hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest(),
                'report_period': fetched.get('report_period'),
                'data': data,
            }
            if entry['cache_allowed'] and persist_cache:
                self._write_entry(path, entity_id, metric, provider_id, entry)
            return CacheResult('fresh', entity_id, metric, provider_id, entry, 0, None if entry['cache_allowed'] else 'cache_not_permitted')
        except Exception as error:
            # Provider/network errors must not stop report generation. Avoid serializing exception text that may contain URLs or secrets.
            error_code = getattr(error, 'code', None)
            if isinstance(error_code, str) and re.fullmatch(r'[a-z0-9_]+', error_code):
                fetch_error = error_code
            else:
                fetch_error = str(error) if str(error).startswith(('provider_', 'http_status_', 'invalid_', 'cache_', 'offline_', 'api_key_', 'license_', 'dataset_', 'request_', 'fund_flow_')) else f'fetch_error:{type(error).__name__}'

        if cached is not None:
            stale = dict(cached, stale=True, fetch_status='stale_fallback', error_message=fetch_error)
            if stale.get('cache_allowed') is True and persist_cache:
                self._write_entry(path, entity_id, metric, provider_id, stale)
            return CacheResult('stale', entity_id, metric, provider_id, stale, age_days, fetch_error)
        reason = fetch_error or cache_error or 'provider_unavailable'
        return CacheResult('unavailable', entity_id, metric, provider_id, None, None, reason)
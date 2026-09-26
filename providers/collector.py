"""Cache-first source priority router for normalized provider observations."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cache.manager import CacheManager
from cache.policy import CachePolicy

from .base import ProviderError
from .models import DataRecord, unavailable_record


def _entity_id(resource: dict[str, Any], provider_id: str) -> str:
    for key in ('entity_id', 'fund_id', 'subject', 'symbol', 'ticker', 'series_id', 'dataset_code', 'id'):
        value = resource.get(key)
        if value:
            return str(value)
    return f'{provider_id}-unconfigured'


def _resource_observation_data(observations):
    if not observations:
        raise ProviderError('provider_returned_no_observations')
    return {
        'observations': [
            {
                'subject': observation.subject,
                'value': observation.value,
                'unit': observation.unit,
                'as_of': observation.as_of,
                'report_period': observation.report_period,
                'data_class': observation.data_class,
                'data_origin': observation.data_origin,
            }
            for observation in observations
        ]
    }


def _record_from_cache(result, provider: dict[str, Any], metric: str, source_role: str) -> list[DataRecord]:
    if result.status == 'unavailable' or result.entry is None:
        return [unavailable_record(
            {**provider, 'provider_id': provider.get('provider_id', result.provider_id)},
            metric,
            result.entity_id,
            result.reason or 'provider_unavailable',
        )]
    entry = result.entry
    data = entry.get('data')
    observations = data.get('observations') if isinstance(data, dict) else None
    if not isinstance(observations, list) or not observations:
        return [unavailable_record(
            {**provider, 'provider_id': result.provider_id}, metric, result.entity_id, 'cache_payload_malformed'
        )]
    records = []
    for observation in observations:
        records.append(DataRecord(
            schema_version=1,
            provider_id=result.provider_id,
            provider_name=str(entry.get('provider') or provider.get('provider_name') or result.provider_id),
            metric=metric,
            subject=str(observation.get('subject') or result.entity_id),
            status='available',
            value=observation.get('value'),
            unit=observation.get('unit'),
            source_name=str(entry.get('provider') or provider.get('provider_name') or result.provider_id),
            source_url=entry.get('source_url'),
            fetched_at=entry['fetched_at'],
            as_of=str(observation.get('as_of') or entry.get('as_of')),
            expires_at=entry.get('expires_at'),
            report_period=observation.get('report_period') or entry.get('report_period'),
            stale=result.status == 'stale',
            freshness_status=result.status,
            confidence=str(entry.get('confidence') or provider.get('confidence') or 'unrated'),
            license_status=str(entry.get('license_status', provider.get('license_status', 'unknown'))),
            cache_allowed=entry.get('cache_allowed', provider.get('cache_allowed', False)),
            redistribution_allowed=entry.get('redistribution_allowed', provider.get('redistribution_allowed', 'verify')),
            raw_data_publication_allowed=entry.get('raw_data_publication_allowed', provider.get('raw_data_publication_allowed', False)),
            derived_data_publication_allowed=entry.get('derived_data_publication_allowed', provider.get('derived_data_publication_allowed', False)),
            data_class=str(observation.get('data_class', 'raw')),
            data_origin=str(observation.get('data_origin', 'real')),
            source_role=source_role,
            reason=entry.get('error_message') if result.status == 'stale' else None,
            raw_sha256=None,
            age_days=result.age_days,
        ))
    return records


class ProviderCollector:
    def __init__(
        self,
        root: Path,
        registry: dict[str, Any],
        adapters: dict[str, type],
        *,
        offline: bool = False,
        force_refresh: bool = False,
        persist_cache: bool = True,
    ):
        self.root = root
        self.registry = registry
        self.providers = registry['providers']
        self.adapters = adapters
        self.offline = offline
        self.force_refresh = force_refresh
        self.persist_cache = persist_cache
        self.cache = CacheManager(root, CachePolicy.load(root / 'cache/cache_policy.yaml'))
        self.env = self._load_environment()
        self.request_counts: dict[str, int] = {}
        self.finished_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
        self.rate_state_path = root / 'data/private/provider_rate_state.json'
        self.rate_state = self._load_rate_state()

    def collect(self, metrics: list[str] | None = None) -> list[DataRecord]:
        records = []
        priorities = self.registry.get('metric_priority', {})
        selected_metrics = metrics if metrics is not None else list(priorities)
        for metric in selected_metrics:
            candidates = priorities.get(metric, [])
            configured_resources = {
                provider_id: [resource for resource in self.providers.get(provider_id, {}).get('resources', []) if resource.get('metric') == metric]
                for provider_id in candidates
            }
            entities = sorted({_entity_id(resource, provider_id) for provider_id, resources in configured_resources.items() for resource in resources})
            if not entities:
                primary_id = next((provider_id for provider_id in candidates if provider_id in self.providers), 'unknown')
                provider = self.providers.get(primary_id, {'provider_id': primary_id, 'provider_name': primary_id})
                records.append(unavailable_record(provider, metric, 'unconfigured', 'resource_not_configured'))
                continue

            for entity in entities:
                selected = False
                last_reason = 'no_eligible_provider'
                for index, provider_id in enumerate(candidates):
                    provider = self.providers.get(provider_id)
                    if not provider or metric not in provider.get('metrics', []):
                        continue
                    resources = [resource for resource in configured_resources.get(provider_id, []) if _entity_id(resource, provider_id) == entity]
                    role = 'primary' if index == 0 else 'fallback'

                    def fetcher(provider=provider, provider_id=provider_id, resources=resources):
                        return self._fetch(provider_id, provider, resources)

                    result = self.cache.get(
                        entity,
                        metric,
                        {**provider, 'provider_id': provider_id},
                        fetcher,
                        offline=self.offline,
                        force_refresh=self.force_refresh,
                        persist_cache=self.persist_cache,
                    )
                    last_reason = result.reason or result.status
                    provider_records = _record_from_cache(result, {**provider, 'provider_id': provider_id}, metric, role)
                    if result.status != 'unavailable':
                        records.extend(provider_records)
                        selected = True
                        break
                if not selected:
                    primary_id = next((provider_id for provider_id in candidates if provider_id in self.providers), 'unknown')
                    provider = self.providers.get(primary_id, {'provider_id': primary_id, 'provider_name': primary_id})
                    records.append(unavailable_record(provider, metric, entity, last_reason))

        if not self.offline and self.persist_cache and self.rate_state:
            self.rate_state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.rate_state_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.rate_state, indent=2) + '\n', encoding='utf-8')
            temporary.replace(self.rate_state_path)
        self.finished_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
        return records

    @property
    def request_count(self) -> int:
        return sum(self.request_counts.values())

    def _fetch(self, provider_id: str, provider: dict[str, Any], resources: list[dict[str, Any]]) -> dict[str, Any]:
        if not provider.get('enabled'):
            raise ProviderError('provider_disabled')
        if provider.get('acquisition_status') != 'approved' or provider.get('license_status') not in {'approved', 'verified'}:
            raise ProviderError('license_not_approved')
        if provider.get('api_available') and provider.get('rate_limit_review_status') != 'approved':
            raise ProviderError('rate_limit_not_reviewed')
        request_budget = provider.get('request_budget_per_run')
        min_interval = provider.get('min_request_interval_seconds')
        if type(request_budget) is not int or request_budget < 1 or type(min_interval) not in (int, float) or min_interval < 0:
            raise ProviderError('rate_limit_budget_not_configured')
        if not resources:
            raise ProviderError('resource_not_configured')
        if len(resources) > request_budget or self.request_counts.get(provider_id, 0) + len(resources) > request_budget:
            raise ProviderError('request_budget_exceeded')
        api_key_name = provider.get('api_key_env')
        api_key = self.env.get(api_key_name) if api_key_name else None
        if provider.get('api_key_required') and not api_key:
            raise ProviderError('api_key_unavailable')
        if provider.get('scraping_required'):
            raise ProviderError('html_scraping_disabled_by_policy')
        if provider.get('dataset_license_required'):
            if any(resource.get('license_status') not in {'approved', 'verified'} for resource in resources):
                raise ProviderError('dataset_license_not_approved')
        if any(resource.get('requires_dataset_approval') and resource.get('acquisition_status') != 'approved' for resource in resources):
            raise ProviderError('dataset_acquisition_not_approved')

        previous_request = self.rate_state.get(provider_id)
        if previous_request:
            try:
                elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(previous_request)).total_seconds()
            except (TypeError, ValueError):
                elapsed = float('inf')
            if elapsed < float(min_interval):
                raise ProviderError('provider_rate_interval_not_elapsed')

        adapter_type = self.adapters.get(provider_id)
        if adapter_type is None:
            raise ProviderError('provider_adapter_unavailable')
        adapter = adapter_type()
        observations = []
        for resource in resources:
            self.request_counts[provider_id] = self.request_counts.get(provider_id, 0) + 1
            self.rate_state[provider_id] = datetime.now(timezone.utc).isoformat(timespec='seconds')
            values, _raw_response = adapter.fetch(provider, resource, api_key)
            observations.extend(values)
        if not observations:
            raise ProviderError('provider_returned_no_observations')
        source_urls = {item.source_url for item in observations}
        if len(source_urls) != 1:
            raise ProviderError('multiple_sources_in_one_cache_entry')
        return {
            'provider': provider['provider_name'],
            'source_url': observations[0].source_url,
            'as_of': max(item.as_of for item in observations),
            'report_period': observations[0].report_period if len({item.report_period for item in observations}) == 1 else None,
            'license_status': provider['license_status'],
            'confidence': provider.get('confidence', 'unrated'),
            'raw_data_publication_allowed': provider.get('raw_data_publication_allowed', False),
            'derived_data_publication_allowed': provider.get('derived_data_publication_allowed', False),
            'data': {
                'observations': [
                    {
                        'subject': item.subject,
                        'value': item.value,
                        'unit': item.unit,
                        'as_of': item.as_of,
                        'report_period': item.report_period,
                        'data_class': item.data_class,
                        'data_origin': item.data_origin,
                    }
                    for item in observations
                ]
            },
        }

    def _load_environment(self) -> dict[str, str]:
        from .registry import load_dotenv
        return load_dotenv(self.root / '.env')

    def _load_rate_state(self) -> dict[str, str]:
        if not self.rate_state_path.is_file():
            return {}
        try:
            payload = json.loads(self.rate_state_path.read_text(encoding='utf-8'))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}
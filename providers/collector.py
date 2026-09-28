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
                'metadata_type': observation.metadata_type,
                'series_type': observation.series_type,
                'frequency': observation.frequency,
                'currency': observation.currency,
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
            as_of=observation.get('as_of') or entry.get('as_of'),
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
            http_status=entry.get('http_status'),
            metadata_type=observation.get('metadata_type') or entry.get('metadata_type'),
            series_type=observation.get('series_type') or entry.get('series_type'),
            frequency=observation.get('frequency') or entry.get('frequency'),
            currency=observation.get('currency') or entry.get('currency'),
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
        self._expand_vanguard_resource_template()
        self._expand_ishares_resource_template()
        self._expand_tiingo_price_template()
        vanguard = self.providers.get('vanguard')
        if isinstance(vanguard, dict) and isinstance(vanguard.get('resource_map'), dict):
            configured = sum(
                len(metric_map)
                for subject, metric_map in vanguard['resource_map'].items()
                if isinstance(metric_map, dict) and subject != 'unconfigured'
            )
            if configured:
                vanguard['request_budget_per_run'] = max(int(vanguard.get('request_budget_per_run') or 0), configured)
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
        self.attempts: list[dict[str, Any]] = []
        self.provider_count = len(self.providers)
        self.enabled_provider_count = sum(1 for provider in self.providers.values() if provider.get('enabled'))
        self.configured_resource_count = sum(self._configured_resource_count(provider) for provider in self.providers.values())
        self.actual_force_refresh_requests = 0

    def _expand_vanguard_resource_template(self) -> None:
        fund_master_path = self.root / 'config/funds.json'
        if not fund_master_path.is_file():
            return
        try:
            import json
            fund_master = json.loads(fund_master_path.read_text(encoding='utf-8-sig')).get('funds', {})
            vanguard_subjects = [subject for subject, metadata in fund_master.items() if metadata.get('provider') == 'vanguard']
        except (OSError, json.JSONDecodeError, AttributeError):
            return
        provider = self.providers.get('vanguard')
        if not isinstance(provider, dict):
            return
        resource_map = provider.get('resource_map')
        template = resource_map.get('VT') if isinstance(resource_map, dict) else None
        if not isinstance(template, dict):
            return
        for subject in vanguard_subjects:
            resource_map.setdefault(subject, {})
            for metric, resource in template.items():
                if not isinstance(resource, dict):
                    continue
                expanded = dict(resource)
                expanded['url'] = str(expanded.get('url') or '').replace('/VT', f'/{subject}')
                expanded['subject'] = subject
                expanded['entity_id'] = subject
                resource_map[subject].setdefault(metric, expanded)
            for metric in ('aum', 'inception_date'):
                resource_map[subject].setdefault(metric, {
                    'url': str(next(iter(template.values())).get('url') or '').replace('/VT', f'/{subject}'),
                    'subject': subject,
                    'entity_id': subject,
                })

    def _expand_ishares_resource_template(self) -> None:
        fund_master_path = self.root / 'config/funds.json'
        provider = self.providers.get('ishares')
        if not fund_master_path.is_file() or not isinstance(provider, dict):
            return
        try:
            import json
            funds = json.loads(fund_master_path.read_text(encoding='utf-8-sig')).get('funds', {})
        except (OSError, json.JSONDecodeError, AttributeError):
            return
        resource_map = provider.setdefault('resource_map', {})
        metrics = ('expense_ratio', 'aum', 'inception_date', 'benchmark', 'number_of_holdings', 'holdings')
        for subject, metadata in funds.items():
            if metadata.get('provider') != 'ishares' or not metadata.get('product_id') or not metadata.get('product_slug'):
                continue
            url = f"https://www.ishares.com/us/products/{metadata['product_id']}/{metadata['product_slug']}"
            resource_map.setdefault(subject, {})
            for metric in metrics:
                resource_map[subject].setdefault(metric, {'url': url, 'subject': subject, 'entity_id': subject})

    def _expand_tiingo_price_template(self) -> None:
        fund_master_path = self.root / 'config/funds.json'
        provider = self.providers.get('tiingo')
        if not fund_master_path.is_file() or not isinstance(provider, dict):
            return
        try:
            import json
            funds = json.loads(fund_master_path.read_text(encoding='utf-8-sig')).get('funds', {})
        except (OSError, json.JSONDecodeError, AttributeError):
            return
        resource_map = provider.setdefault('resource_map', {})
        for subject, metadata in funds.items():
            if subject not in {'ACWI', 'ITOT', 'IXUS', 'EFA', 'IDEV'}:
                continue
            ticker = metadata.get('ticker') or subject
            resource_map.setdefault(subject, {}).setdefault('price_history', {
                'ticker': ticker,
                'subject': subject,
                'entity_id': subject,
                'start_date': '2016-01-01',
                'price_field': 'adjClose',
                'series_type': 'adjusted_close',
            })

    @staticmethod
    def _provider_metric_resources(provider: dict[str, Any], metric: str) -> list[dict[str, Any]]:
        resources: list[dict[str, Any]] = [
            dict(resource)
            for resource in provider.get('resources', [])
            if isinstance(resource, dict) and resource.get('metric') == metric
        ]
        if resources:
            return resources

        subject_metric_resources = provider.get('subject_metric_resources') or provider.get('resource_map') or {}
        if isinstance(subject_metric_resources, dict):
            for subject, metric_map in subject_metric_resources.items():
                if not isinstance(metric_map, dict):
                    continue
                mapped = metric_map.get(metric)
                if mapped is None:
                    continue
                mapped_resources = mapped if isinstance(mapped, list) else [mapped]
                for resource in mapped_resources:
                    if not isinstance(resource, dict):
                        continue
                    normalized = dict(resource)
                    normalized.setdefault('metric', metric)
                    normalized.setdefault('subject', str(subject))
                    normalized.setdefault('entity_id', str(subject))
                    resources.append(normalized)
        return resources

    @classmethod
    def _configured_resource_count(cls, provider: dict[str, Any]) -> int:
        resources = provider.get('resources', [])
        count = sum(1 for resource in resources if isinstance(resource, dict))
        subject_metric_resources = provider.get('subject_metric_resources') or provider.get('resource_map') or {}
        if not isinstance(subject_metric_resources, dict):
            return count
        for metric_map in subject_metric_resources.values():
            if not isinstance(metric_map, dict):
                continue
            for mapped in metric_map.values():
                if isinstance(mapped, list):
                    count += sum(1 for resource in mapped if isinstance(resource, dict))
                elif isinstance(mapped, dict):
                    count += 1
        return count

    def collect(self, metrics: list[str] | None = None) -> list[DataRecord]:
        records = []
        priorities = self.registry.get('metric_priority', {})
        selected_metrics = metrics if metrics is not None else list(priorities)
        for metric in selected_metrics:
            candidates = priorities.get(metric, [])
            configured_resources = {
                provider_id: self._provider_metric_resources(self.providers.get(provider_id, {}), metric)
                for provider_id in candidates
            }
            entities = sorted({_entity_id(resource, provider_id) for provider_id, resources in configured_resources.items() for resource in resources})
            if not entities:
                primary_id = next((provider_id for provider_id in candidates if provider_id in self.providers), 'unknown')
                provider = self.providers.get(primary_id, {'provider_id': primary_id, 'provider_name': primary_id})
                records.append(unavailable_record(provider, metric, 'unconfigured', 'resource_not_configured'))
                self.attempts.append({
                    'metric': metric,
                    'entity_id': 'unconfigured',
                    'provider_id': primary_id,
                    'source_role': 'primary',
                    'action': 'resolve_resource',
                    'request_attempted': False,
                    'http_status': None,
                    'status': 'unavailable',
                    'freshness_status': 'unavailable',
                    'reason': 'resource_not_configured',
                    'fetch_status': None,
                    'age_days': None,
                })
                continue

            for entity in entities:
                selected = False
                last_reason = 'no_eligible_provider'
                primary_reason = None
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
                    if index == 0 and last_reason not in {'provider_disabled', 'resource_not_configured'}:
                        primary_reason = last_reason
                    provider_records = _record_from_cache(result, {**provider, 'provider_id': provider_id}, metric, role)
                    fetch_status = result.entry.get('fetch_status') if result.entry else None
                    http_status = result.entry.get('http_status') if result.entry else None
                    self.attempts.append({
                        'metric': metric,
                        'entity_id': entity,
                        'provider_id': provider_id,
                        'source_role': role,
                        'action': 'fetch' if fetch_status in {'success', 'stale_fallback', 'offline_stale_fallback'} else 'cache',
                        'request_attempted': fetch_status in {'success', 'stale_fallback'},
                        'http_status': http_status,
                        'status': result.status,
                        'freshness_status': result.status,
                        'reason': last_reason,
                        'fetch_status': fetch_status,
                        'age_days': result.age_days,
                    })
                    if result.status != 'unavailable':
                        records.extend(provider_records)
                        selected = True
                        break
                if not selected:
                    primary_id = next((provider_id for provider_id in candidates if provider_id in self.providers), 'unknown')
                    provider = self.providers.get(primary_id, {'provider_id': primary_id, 'provider_name': primary_id})
                    records.append(unavailable_record(provider, metric, entity, primary_reason or last_reason))

        if not self.offline and self.persist_cache and self.rate_state:
            self.rate_state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.rate_state_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.rate_state, indent=2) + '\n', encoding='utf-8')
            temporary.replace(self.rate_state_path)
        self.finished_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
        return records

    def diagnostics(self) -> dict[str, Any]:
        status_counts: dict[str, int] = {}
        fetch_status_counts: dict[str, int] = {}
        reason_counts: dict[str, int] = {}
        http_status_counts: dict[str, int] = {}
        for attempt in self.attempts:
            status = str(attempt.get('status') or 'unknown')
            status_counts[status] = status_counts.get(status, 0) + 1
            fetch_status = str(attempt.get('fetch_status') or 'none')
            fetch_status_counts[fetch_status] = fetch_status_counts.get(fetch_status, 0) + 1
            reason = str(attempt.get('reason') or 'unknown')
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            http_status = str(attempt.get('http_status') or 'none')
            http_status_counts[http_status] = http_status_counts.get(http_status, 0) + 1
        return {
            'provider_count': self.provider_count,
            'enabled_provider_count': self.enabled_provider_count,
            'configured_resource_count': self.configured_resource_count,
            'request_attempt_count': self.request_count,
            'actual_force_refresh_requests': self.actual_force_refresh_requests,
            'status_counts': status_counts,
            'fetch_status_counts': fetch_status_counts,
            'reason_counts': reason_counts,
            'http_status_counts': http_status_counts,
            'attempts': self.attempts,
        }

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
            if self.force_refresh:
                self.actual_force_refresh_requests += 1
            self.rate_state[provider_id] = datetime.now(timezone.utc).isoformat(timespec='seconds')
            fetch_result = adapter.fetch(provider, resource, api_key)
            metadata: dict[str, Any] = {}
            if isinstance(fetch_result, tuple) and len(fetch_result) == 3:
                values, _raw_response, metadata = fetch_result
            elif isinstance(fetch_result, tuple) and len(fetch_result) == 2:
                values, _raw_response = fetch_result
            else:
                raise ProviderError('invalid_adapter_response')
            observations.extend(values)
        if not observations:
            raise ProviderError('provider_returned_no_observations')
        source_urls = {item.source_url for item in observations}
        if len(source_urls) != 1:
            raise ProviderError('multiple_sources_in_one_cache_entry')
        as_of_values = [item.as_of for item in observations if item.as_of]
        metadata_types = {item.metadata_type for item in observations}
        return {
            'provider': provider['provider_name'],
            'source_url': observations[0].source_url,
            'as_of': max(as_of_values) if as_of_values else None,
            'metadata_type': 'static' if metadata_types == {'static'} else None,
            'report_period': observations[0].report_period if len({item.report_period for item in observations}) == 1 else None,
            'license_status': provider['license_status'],
            'confidence': provider.get('confidence', 'unrated'),
            'raw_data_publication_allowed': provider.get('raw_data_publication_allowed', False),
            'derived_data_publication_allowed': provider.get('derived_data_publication_allowed', False),
            'http_status': metadata.get('http_status'),
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
                        'metadata_type': item.metadata_type,
                        'series_type': item.series_type,
                        'frequency': item.frequency,
                        'currency': item.currency,
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
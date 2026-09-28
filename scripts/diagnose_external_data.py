"""Diagnose external-data collection stages and summarize failure roots."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from providers.registry import collect_latest


STAGES = (
    'STAGE1_CONFIG',
    'STAGE2_AUTH',
    'STAGE3_NETWORK',
    'STAGE4_RESPONSE',
    'STAGE5_NORMALIZE',
    'STAGE6_DERIVED',
    'STAGE7_CACHE_SNAPSHOT',
)

_STAGE_BY_REASON = {
    'provider_disabled': 'STAGE1_CONFIG',
    'license_not_approved': 'STAGE1_CONFIG',
    'rate_limit_not_reviewed': 'STAGE1_CONFIG',
    'rate_limit_budget_not_configured': 'STAGE1_CONFIG',
    'resource_not_configured': 'STAGE1_CONFIG',
    'dataset_license_not_approved': 'STAGE1_CONFIG',
    'dataset_acquisition_not_approved': 'STAGE1_CONFIG',
    'api_key_unavailable': 'STAGE2_AUTH',
    'network_error': 'STAGE3_NETWORK',
    'provider_rate_interval_not_elapsed': 'STAGE3_NETWORK',
    'request_budget_exceeded': 'STAGE3_NETWORK',
    'provider_adapter_unavailable': 'STAGE3_NETWORK',
    'provider_returned_no_observations': 'STAGE4_RESPONSE',
    'provider_returned_empty_or_zero_value': 'STAGE4_RESPONSE',
    'provider_missing_source_url_or_as_of': 'STAGE4_RESPONSE',
    'multiple_sources_in_one_cache_entry': 'STAGE4_RESPONSE',
    'resource_url_not_configured': 'STAGE4_RESPONSE',
    'structured_format_required': 'STAGE4_RESPONSE',
    'no_mapped_observations': 'STAGE4_RESPONSE',
    'provider_metric_not_supported': 'STAGE4_RESPONSE',
    'invalid_adapter_response': 'STAGE4_RESPONSE',
    'invalid_json': 'STAGE5_NORMALIZE',
    'invalid_csv': 'STAGE5_NORMALIZE',
    'empty_csv': 'STAGE5_NORMALIZE',
    'invalid_price_point': 'STAGE5_NORMALIZE',
    'duplicate_price_dates': 'STAGE5_NORMALIZE',
    'cache_malformed': 'STAGE7_CACHE_SNAPSHOT',
    'cache_schema_or_entity_mismatch': 'STAGE7_CACHE_SNAPSHOT',
    'cache_not_permitted': 'STAGE7_CACHE_SNAPSHOT',
    'offline_cache_miss': 'STAGE7_CACHE_SNAPSHOT',
    'offline_cache_expired': 'STAGE7_CACHE_SNAPSHOT',
}


_DEPENDENCY_REASONS = {
    'price_series_unavailable',
    'benchmark_series_unavailable',
    'risk_free_series_unavailable',
    'holdings_unavailable',
    'holdings_not_complete',
    'holdings_source_or_as_of_mismatch',
    'resource_not_configured',
}


def classify_stage(provider_id: str, reason: str | None) -> str:
    if provider_id == 'analysis_engine':
        return 'STAGE6_DERIVED'
    if not reason:
        return 'STAGE4_RESPONSE'
    if reason.startswith('http_status_'):
        return 'STAGE3_NETWORK'
    if reason.startswith('fetch_error:'):
        return 'STAGE3_NETWORK'
    return _STAGE_BY_REASON.get(reason, 'STAGE4_RESPONSE')


def _build_stage_counters(records: list[dict], attempts: list[dict]) -> dict[str, dict[str, int]]:
    reason_counters = {stage: Counter() for stage in STAGES}
    for attempt in attempts:
        reason = attempt.get('reason')
        stage = classify_stage(str(attempt.get('provider_id') or ''), reason)
        reason_counters[stage][str(reason or 'unknown')] += 1
    for record in records:
        provider_id = str(record.get('provider_id') or '')
        if provider_id != 'analysis_engine' or record.get('status') != 'unavailable':
            continue
        reason = str(record.get('reason') or 'unknown')
        stage = classify_stage(provider_id, reason)
        reason_counters[stage][reason] += 1
    return {stage: dict(counter) for stage, counter in reason_counters.items() if counter}


def diagnose(root: Path, *, offline: bool, force_refresh: bool, metrics: list[str] | None = None) -> dict:
    collected = collect_latest(
        root,
        persist=True,
        offline=offline,
        force_refresh=force_refresh,
        metrics=metrics,
    )
    records = collected.get('records', [])
    diagnostics = collected.get('diagnostics', {})
    attempts = diagnostics.get('attempts', [])

    status_counts = Counter(record.get('status', 'unknown') for record in records)
    freshness_counts = Counter(record.get('freshness_status', 'unknown') for record in records)
    fetch_status_counts = Counter(diagnostics.get('fetch_status_counts', {}))
    http_status_counts = Counter(diagnostics.get('http_status_counts', {}))

    derived_unavailable = [
        record for record in records
        if record.get('provider_id') == 'analysis_engine' and record.get('status') == 'unavailable'
    ]
    dependency_failed = [
        record for record in derived_unavailable
        if str(record.get('reason') or '') == 'dependency_failed'
        or str(record.get('root_cause') or '') in _DEPENDENCY_REASONS
    ]

    successful_fetches = [
        attempt for attempt in attempts
        if attempt.get('action') == 'fetch'
        and attempt.get('request_attempted') is True
        and str(attempt.get('status')) in {'fresh', 'stale'}
    ]

    stage_reason_counts = _build_stage_counters(records, attempts)
    reason_counts = Counter(diagnostics.get('reason_counts', {}))

    return {
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'root': str(root),
        'mode': {
            'offline': offline,
            'force_refresh': force_refresh,
            'metrics': metrics or 'all',
        },
        'summary': {
            'provider_count': diagnostics.get('provider_count', 0),
            'enabled_provider_count': diagnostics.get('enabled_provider_count', 0),
            'configured_resource_count': diagnostics.get('configured_resource_count', 0),
            'request_attempt_count': diagnostics.get('request_attempt_count', 0),
            'actual_force_refresh_requests': diagnostics.get('actual_force_refresh_requests', 0),
            'available_count': status_counts.get('available', 0),
            'unavailable_count': status_counts.get('unavailable', 0),
            'cache_hit_count': fetch_status_counts.get('cache_hit', 0),
            'cache_stale_fallback_count': fetch_status_counts.get('stale_fallback', 0) + fetch_status_counts.get('offline_stale_fallback', 0),
            'http_status_counts': dict(http_status_counts),
            'freshness_counts': dict(freshness_counts),
        },
        'stage_reason_counts': stage_reason_counts,
        'top_reasons': dict(reason_counts.most_common(20)),
        'derived_dependency_failures': {
            'count': len(dependency_failed),
            'root_cause_counts': dict(Counter(str(item.get('root_cause') or item.get('reason') or 'unknown') for item in dependency_failed)),
        },
        'successful_fetch_logs': [
            {
                'subject': item.get('entity_id'),
                'metric': item.get('metric'),
                'provider': item.get('provider_id'),
                'action': item.get('action'),
                'request_attempted': item.get('request_attempted'),
                'http_status': item.get('http_status'),
                'status': 'success' if str(item.get('status')) in {'fresh', 'stale'} else item.get('status'),
            }
            for item in successful_fetches
        ],
        'attempts': attempts,
    }


def _parse_metrics(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    metrics = [item.strip() for item in raw.split(',') if item.strip()]
    return metrics or None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--refresh-external-data', action='store_true')
    mode.add_argument('--offline', action='store_true')
    parser.add_argument('--metrics', help='Comma-separated metric names (optional)')
    args = parser.parse_args()

    root = args.root.resolve()
    report = diagnose(
        root,
        offline=args.offline,
        force_refresh=args.refresh_external_data,
        metrics=_parse_metrics(args.metrics),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

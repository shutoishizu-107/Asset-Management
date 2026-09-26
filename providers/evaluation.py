"""Readiness checks for portfolio research; never scores incomplete data."""
from __future__ import annotations

from typing import Any

from .metrics import weighted_portfolio_overlap
from .models import DataRecord, utc_now


REQUIRED_FUND_METRICS = ('expense_ratio', 'aum', 'holdings', 'benchmark')


def evaluate_fund_readiness(fund_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    relevant = [
        record for record in records
        if record.get('subject') == fund_id and record.get('metric') in REQUIRED_FUND_METRICS
    ]
    status_by_metric = {}
    for metric in REQUIRED_FUND_METRICS:
        matches = [record for record in relevant if record.get('metric') == metric and record.get('status') == 'available']
        if not matches:
            status_by_metric[metric] = 'unavailable'
        elif any(record.get('stale', True) for record in matches):
            status_by_metric[metric] = 'stale'
        else:
            status_by_metric[metric] = 'available'
    missing = [metric for metric, status in status_by_metric.items() if status != 'available']
    return {
        'fund_id': fund_id,
        'status': 'ready_for_manual_review' if not missing else 'unavailable',
        'metrics': status_by_metric,
        'missing_or_stale_metrics': missing,
        'score': None,
        'recommendation': None,
        'reason': 'No scoring weights or approved recommendation model are configured.' if not missing else 'Required current data is unavailable or stale; no score or recommendation was produced.',
    }


def evaluate_funds(fund_ids: list[str], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [evaluate_fund_readiness(fund_id, records) for fund_id in fund_ids]


def derive_portfolio_overlaps(source_records: list[DataRecord], fund_ids: list[str]) -> list[DataRecord]:
    """Compute overlap only from complete holdings with matching source and as-of."""
    holdings_by_fund: dict[str, list[DataRecord]] = {fund_id: [] for fund_id in fund_ids}
    for record in source_records:
        if record.metric == 'holdings' and record.subject in holdings_by_fund and record.status == 'available':
            holdings_by_fund[record.subject].append(record)

    result = []
    for index, fund_a in enumerate(fund_ids):
        for fund_b in fund_ids[index + 1:]:
            left, right = holdings_by_fund[fund_a], holdings_by_fund[fund_b]
            reason = None
            source_record = (left or right)[0] if left or right else None
            if not left or not right:
                reason = 'holdings_unavailable'
            elif len({(item.provider_id, item.as_of, item.source_url) for item in left + right}) != 1:
                reason = 'holdings_source_or_as_of_mismatch'
            elif any(item.stale for item in left + right):
                reason = 'holdings_stale'
            elif any(not isinstance(item.value, dict) or item.value.get('complete') is not True for item in left + right):
                reason = 'holdings_not_complete'

            overlap_value = None
            if reason is None:
                weights_a = {item.value['security_id']: float(item.value['weight']) for item in left}
                weights_b = {item.value['security_id']: float(item.value['weight']) for item in right}
                overlap = weighted_portfolio_overlap(weights_a, weights_b, complete_a=True, complete_b=True)
                if overlap['status'] == 'available':
                    overlap_value = overlap['value']
                else:
                    reason = overlap['reason']

            provider = source_record
            result.append(DataRecord(
                schema_version=1,
                provider_id='analysis_engine',
                provider_name='Local analysis engine',
                metric='portfolio_overlap',
                subject=f'{fund_a}|{fund_b}',
                status='available' if reason is None else 'unavailable',
                value=overlap_value,
                unit='fraction_of_portfolio' if reason is None else None,
                source_name=provider.source_name if provider else 'unavailable',
                source_url=provider.source_url if provider else None,
                fetched_at=utc_now(),
                as_of=provider.as_of if provider and reason is None else None,
                expires_at=provider.expires_at if provider and reason is None else None,
                report_period=provider.report_period if provider else None,
                stale=reason is not None or bool(provider and provider.stale),
                freshness_status=provider.freshness_status if provider and reason is None else 'unavailable',
                confidence=provider.confidence if provider and reason is None else 'unavailable',
                license_status=provider.license_status if provider else 'unreviewed',
                cache_allowed=all(item.cache_allowed is True for item in left + right) if left and right else False,
                redistribution_allowed=provider.redistribution_allowed if provider else 'verify',
                raw_data_publication_allowed=False,
                derived_data_publication_allowed=all(item.derived_data_publication_allowed is True for item in left + right) if left and right else False,
                data_class='derived',
                data_origin=provider.data_origin if provider else 'unknown',
                source_role='primary',
                reason=reason,
                age_days=provider.age_days if provider and reason is None else None,
            ))
    return result
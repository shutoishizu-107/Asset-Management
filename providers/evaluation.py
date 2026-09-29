"""Readiness checks for portfolio research; never scores incomplete data."""
from __future__ import annotations

from typing import Any

from .metrics import derived_records, weighted_portfolio_overlap
from .models import DataRecord, utc_now
from .scoring import score_funds


REQUIRED_FUND_METRICS = (
    'expense_ratio', 'total_expense_ratio', 'aum', 'fund_flow_1y', 'benchmark', 'holdings',
    'number_of_holdings', 'top10_concentration', 'us_weight', 'tech_weight', 'small_cap_weight',
    'nisa_tsumitate_eligible', 'nisa_growth_eligible',
)


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


def evaluate_funds(
    fund_ids: list[str],
    records: list[dict[str, Any]],
    scoring_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if scoring_config is not None:
        return score_funds(fund_ids, records, scoring_config)
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
                dependency='holdings' if reason is not None else None,
                root_cause=reason if reason is not None else None,
                age_days=provider.age_days if provider and reason is None else None,
                http_status=provider.http_status if provider and reason is None else None,
            ))
    return result


def _complete_holdings(records: list[DataRecord], subject: str) -> tuple[dict[str, float] | None, DataRecord | None, str | None]:
    all_matches = [item for item in records if item.metric == 'holdings' and item.subject == subject]
    if not all_matches:
        return None, None, 'holdings_unavailable'
    unavailable_reason = next((item.reason for item in all_matches if item.status == 'unavailable' and item.reason), None)
    if unavailable_reason == 'holdings_not_available_from_source':
        evidence = next((item for item in all_matches if item.status == 'unavailable'), all_matches[0])
        return None, evidence, 'individual_holdings_unavailable'
    matches = [item for item in all_matches if item.status == 'available']
    if not matches:
        evidence = next((item for item in all_matches if item.status == 'unavailable'), all_matches[0])
        return None, evidence, 'holdings_unavailable'
    if len({(item.provider_id, item.as_of, item.source_url) for item in matches}) != 1:
        return None, matches[0], 'holdings_source_or_as_of_mismatch'
    if any(item.stale for item in matches):
        return None, matches[0], 'holdings_stale'
    if any(not isinstance(item.value, dict) or item.value.get('complete') is not True for item in matches):
        return None, matches[0], 'holdings_not_complete'
    weights = {}
    for item in matches:
        try:
            security_name = str(item.value.get('security_name') or item.value.get('security_id') or '').strip()
            if not security_name:
                return None, matches[0], 'individual_holdings_unavailable'
            aggregate_name = security_name.upper()
            if aggregate_name.startswith(('REGION::', 'SECTOR::', 'ASSET::')):
                return None, matches[0], 'individual_holdings_unavailable'
            if any(token in security_name.lower() for token in ('region', 'sector', 'allocation', 'exposure')):
                return None, matches[0], 'individual_holdings_unavailable'
            weight = float(item.value['weight'])
            security_id = str(
                item.value.get('ticker')
                or item.value.get('isin')
                or item.value.get('cusip')
                or item.value.get('security_id')
                or security_name
            ).strip()
        except (KeyError, TypeError, ValueError):
            return None, matches[0], 'invalid_holding_weight'
        if not security_id or not 0 <= weight <= 1 or security_id in weights:
            return None, matches[0], 'invalid_holding_weight'
        weights[security_id] = weight
    if not weights or abs(sum(weights.values()) - 1.0) > 0.02:
        return None, matches[0], 'holdings_weights_not_complete'
    return weights, matches[0], None


def _weighted_current_portfolio(
    records: list[DataRecord], portfolio_weights: dict[str, float]
) -> tuple[dict[str, float] | None, DataRecord | None, str | None]:
    positive_positions = {subject: value for subject, value in portfolio_weights.items() if value > 0}
    if not positive_positions or any(value < 0 for value in portfolio_weights.values()):
        return None, None, 'current_portfolio_holdings_unavailable'
    total_value = sum(positive_positions.values())
    aggregated: dict[str, float] = {}
    evidence = []
    for subject, position_value in positive_positions.items():
        weights, source, reason = _complete_holdings(records, subject)
        if reason or weights is None or source is None:
            return None, source, f'current_portfolio_{reason or "holdings_unavailable"}'
        evidence.append(source)
        portfolio_share = position_value / total_value
        for security_id, weight in weights.items():
            aggregated[security_id] = aggregated.get(security_id, 0.0) + portfolio_share * weight
    if len({(item.provider_id, item.source_url, item.as_of) for item in evidence}) != 1:
        return None, evidence[0] if evidence else None, 'current_portfolio_holdings_source_or_as_of_mismatch'
    return aggregated, evidence[0] if evidence else None, None


def derive_named_overlap_metrics(
    source_records: list[DataRecord],
    candidate_ids: list[str],
    reference_subjects: dict[str, str],
    portfolio_weights: dict[str, float] | None = None,
) -> list[DataRecord]:
    """Create named overlap metrics only from complete, fresh, date-aligned holdings."""
    reference_metrics = {
        'overlap_with_sp500': reference_subjects.get('overlap_with_sp500'),
        'overlap_with_fang': reference_subjects.get('overlap_with_fang'),
        'overlap_with_all_country': reference_subjects.get('overlap_with_all_country'),
    }
    current_map = None
    current_source = None
    current_reason = 'current_portfolio_holdings_unavailable'
    if portfolio_weights is not None:
        current_map, current_source, current_reason = _weighted_current_portfolio(source_records, portfolio_weights)

    result = []
    for candidate_id in candidate_ids:
        candidate_map, candidate_source, candidate_reason = _complete_holdings(source_records, candidate_id)
        for metric, reference_id in reference_metrics.items():
            reference_map, reference_source, reference_reason = _complete_holdings(source_records, reference_id) if reference_id else (None, None, 'reference_not_configured')
            reason = candidate_reason or reference_reason
            if reason is None and candidate_source and reference_source and (
                candidate_source.provider_id, candidate_source.source_url, candidate_source.as_of
            ) != (
                reference_source.provider_id, reference_source.source_url, reference_source.as_of
            ):
                reason = 'holdings_source_or_as_of_mismatch'
            outcome = None
            if reason is None:
                outcome = weighted_portfolio_overlap(candidate_map, reference_map, complete_a=True, complete_b=True)
                if outcome['status'] != 'available':
                    reason = outcome['reason']

            evidence = candidate_source or reference_source
            result.append(_overlap_record(metric, candidate_id, outcome, reason, evidence, [candidate_source, reference_source]))

        reason = candidate_reason or current_reason
        if reason is None and candidate_source and current_source and (
            candidate_source.provider_id, candidate_source.source_url, candidate_source.as_of
        ) != (
            current_source.provider_id, current_source.source_url, current_source.as_of
        ):
            reason = 'holdings_source_or_as_of_mismatch'
        outcome = None
        evidence = candidate_source or current_source
        evidence_records = [candidate_source, current_source]
        if reason is None:
            outcome = weighted_portfolio_overlap(candidate_map, current_map, complete_a=True, complete_b=True)
            if outcome['status'] != 'available':
                reason = outcome['reason']
        result.append(_overlap_record('overlap_with_current_portfolio', candidate_id, outcome, reason, evidence, evidence_records))
    return result


def _overlap_record(
    metric: str,
    subject: str,
    outcome: dict[str, Any] | None,
    reason: str | None,
    evidence: DataRecord | None,
    evidence_records: list[DataRecord | None],
) -> DataRecord:
    available = reason is None and outcome is not None and outcome.get('status') == 'available' and evidence is not None
    root_cause = reason or 'overlap_unavailable'
    failure_reason = None if available else 'dependency_failed'
    return DataRecord(
        schema_version=1,
        provider_id='analysis_engine',
        provider_name='Local analysis engine',
        metric=metric,
        subject=subject,
        status='available' if available else 'unavailable',
        value=outcome['value'] if available else None,
        unit='fraction_of_portfolio' if available else None,
        source_name=evidence.source_name if evidence else 'unavailable',
        source_url=evidence.source_url if evidence else None,
        fetched_at=utc_now(),
        as_of=evidence.as_of if available and evidence else None,
        expires_at=evidence.expires_at if available and evidence else None,
        report_period=evidence.report_period if evidence else None,
        stale=not available or bool(evidence and evidence.stale),
        freshness_status=evidence.freshness_status if available and evidence else 'unavailable',
        confidence=evidence.confidence if available and evidence else 'unavailable',
        license_status=evidence.license_status if evidence else 'unreviewed',
        cache_allowed=all(item.cache_allowed is True for item in evidence_records if item is not None) if available else False,
        redistribution_allowed=evidence.redistribution_allowed if evidence else 'verify',
        raw_data_publication_allowed=False,
        derived_data_publication_allowed=all(item.derived_data_publication_allowed is True for item in evidence_records if item is not None) if available else False,
        data_class='derived',
        data_origin=evidence.data_origin if evidence else 'unknown',
        source_role='primary',
        reason=failure_reason,
        dependency=None if available else 'holdings',
        root_cause=None if available else root_cause,
        age_days=evidence.age_days if available and evidence else None,
        http_status=evidence.http_status if available and evidence else None,
    )


def derive_holdings_summary_metrics(source_records: list[DataRecord], fund_ids: list[str]) -> list[DataRecord]:
    """Derive holding count, top-10 weight, and top-10 constituents from complete holdings snapshots."""
    result = []
    for fund_id in fund_ids:
        weights, source, reason = _complete_holdings(source_records, fund_id)
        if source is None:
            continue
        if reason or weights is None:
            outcomes = {
                metric: {'status': 'unavailable', 'value': None, 'reason': reason or 'holdings_unavailable'}
                for metric in ('number_of_holdings', 'top10_concentration', 'top10_holdings')
            }
        else:
            ranked = sorted(weights.items(), key=lambda item: item[1], reverse=True)
            outcomes = {
                'number_of_holdings': {'status': 'available', 'value': len(weights), 'unit': 'count'},
                'top10_concentration': {
                    'status': 'available',
                    'value': sum(weight for _, weight in ranked[:10]),
                    'unit': 'fraction',
                },
                'top10_holdings': {
                    'status': 'available',
                    'value': [
                        {'security_id': security_id, 'weight': weight}
                        for security_id, weight in ranked[:10]
                    ],
                    'unit': 'holdings_weight_fraction',
                },
            }
        result.extend(derived_records(source, outcomes))
    return result
"""Deterministic derived metrics from normalized price and holdings records."""
from __future__ import annotations

import math
import statistics
from datetime import date
from typing import Any

from .models import DataRecord, utc_now


def _price_points(series: Any) -> list[tuple[date, float]]:
    if not isinstance(series, list):
        raise ValueError('price_series_unavailable')
    points = []
    for row in series:
        if not isinstance(row, dict) or 'date' not in row or 'value' not in row:
            raise ValueError('invalid_price_point')
        try:
            point_date = date.fromisoformat(str(row['date'])[:10])
            value = float(row['value'])
        except (TypeError, ValueError):
            raise ValueError('invalid_price_point') from None
        if not math.isfinite(value) or value <= 0:
            raise ValueError('price_must_be_finite_and_positive')
        points.append((point_date, value))
    points.sort(key=lambda item: item[0])
    if len(points) < 2:
        raise ValueError('insufficient_price_observations')
    if len({point[0] for point in points}) != len(points):
        raise ValueError('duplicate_price_dates')
    return points


def _annualized_return(points: list[tuple[date, float]]) -> float:
    elapsed_days = (points[-1][0] - points[0][0]).days
    if elapsed_days <= 0:
        raise ValueError('nonpositive_price_period')
    years = elapsed_days / 365.2425
    return (points[-1][1] / points[0][1]) ** (1 / years) - 1


def calculate_price_metrics(
    price_series: Any,
    risk_free_annual: float | None = None,
    benchmark_series: Any | None = None,
) -> dict[str, dict[str, Any]]:
    """Calculate CAGR, annualized volatility, Sharpe, max drawdown and tracking difference.

    Risk-free and benchmark inputs are never fabricated. Missing inputs produce unavailable metrics.
    """
    try:
        points = _price_points(price_series)
    except ValueError as error:
        reason = str(error)
        return {metric: {'status': 'unavailable', 'value': None, 'reason': reason} for metric in (
            'annualized_return', 'volatility', 'sharpe_ratio', 'max_drawdown', 'tracking_difference'
        )}
    returns = [current[1] / previous[1] - 1 for previous, current in zip(points, points[1:])]
    elapsed_days = (points[-1][0] - points[0][0]).days
    mean_interval = elapsed_days / (len(points) - 1)
    periods_per_year = 365.2425 / mean_interval
    volatility = statistics.stdev(returns) * math.sqrt(periods_per_year) if len(returns) >= 2 else None
    peak = points[0][1]
    maximum_drawdown = 0.0
    for _, price in points:
        peak = max(peak, price)
        maximum_drawdown = min(maximum_drawdown, price / peak - 1)
    result = {
        'annualized_return': {'status': 'available', 'value': _annualized_return(points), 'unit': 'fraction'},
        'volatility': ({'status': 'available', 'value': volatility, 'unit': 'fraction'} if volatility is not None else
                       {'status': 'unavailable', 'value': None, 'reason': 'insufficient_returns_for_sample_volatility'}),
        'max_drawdown': {'status': 'available', 'value': maximum_drawdown, 'unit': 'fraction'},
    }
    if risk_free_annual is None:
        result['sharpe_ratio'] = {'status': 'unavailable', 'value': None, 'reason': 'risk_free_series_unavailable'}
    elif volatility is None or volatility == 0:
        result['sharpe_ratio'] = {'status': 'unavailable', 'value': None, 'reason': 'volatility_unavailable_or_zero'}
    elif not math.isfinite(risk_free_annual):
        result['sharpe_ratio'] = {'status': 'unavailable', 'value': None, 'reason': 'invalid_risk_free_input'}
    else:
        average_return = statistics.mean(returns)
        periodic_rf = (1 + risk_free_annual) ** (1 / periods_per_year) - 1
        result['sharpe_ratio'] = {'status': 'available', 'value': (average_return - periodic_rf) * periods_per_year / volatility, 'unit': 'ratio'}
    if benchmark_series is None:
        result['tracking_difference'] = {'status': 'unavailable', 'value': None, 'reason': 'benchmark_series_unavailable'}
    else:
        try:
            benchmark = _price_points(benchmark_series)
            strategy_by_date = dict(points)
            benchmark_by_date = dict(benchmark)
            shared_dates = sorted(strategy_by_date.keys() & benchmark_by_date.keys())
            if len(shared_dates) < 2:
                raise ValueError('insufficient_aligned_benchmark_observations')
            aligned_strategy = [(day, strategy_by_date[day]) for day in shared_dates]
            aligned_benchmark = [(day, benchmark_by_date[day]) for day in shared_dates]
            result['tracking_difference'] = {
                'status': 'available',
                'value': _annualized_return(aligned_strategy) - _annualized_return(aligned_benchmark),
                'unit': 'fraction_annualized_return_difference',
            }
        except ValueError as error:
            result['tracking_difference'] = {'status': 'unavailable', 'value': None, 'reason': str(error)}
    return result


def derived_records(source: DataRecord, metrics: dict[str, dict[str, Any]]) -> list[DataRecord]:
    records = []
    for metric, outcome in metrics.items():
        available = outcome['status'] == 'available' and source.status == 'available'
        records.append(DataRecord(
            schema_version=1,
            provider_id='analysis_engine',
            provider_name='Local analysis engine',
            metric=metric,
            subject=source.subject,
            status='available' if available else 'unavailable',
            value=outcome.get('value') if available else None,
            unit=outcome.get('unit'),
            source_name=source.source_name,
            source_url=source.source_url,
            fetched_at=utc_now(),
            as_of=source.as_of if available else None,
            expires_at=source.expires_at if available else None,
            report_period=source.report_period,
            stale=source.stale if available else True,
            freshness_status=source.freshness_status if available else 'unavailable',
            confidence=source.confidence if available else 'unavailable',
            license_status=source.license_status,
            cache_allowed=source.cache_allowed,
            redistribution_allowed=source.redistribution_allowed,
            raw_data_publication_allowed=False,
            derived_data_publication_allowed=source.derived_data_publication_allowed,
            data_class='derived',
            data_origin=source.data_origin,
            source_role=source.source_role,
            reason=outcome.get('reason') if not available else None,
            age_days=source.age_days if available else None,
        ))
    return records


def weighted_portfolio_overlap(
    holdings_a: dict[str, float] | None,
    holdings_b: dict[str, float] | None,
    *,
    complete_a: bool,
    complete_b: bool,
) -> dict[str, Any]:
    """Return sum(min(weight_a, weight_b)); weights must be fractions and inputs complete."""
    if holdings_a is None or holdings_b is None:
        return {'status': 'unavailable', 'value': None, 'reason': 'holdings_unavailable'}
    if not complete_a or not complete_b:
        return {'status': 'unavailable', 'value': None, 'reason': 'holdings_not_complete'}
    for holdings in (holdings_a, holdings_b):
        if any(not math.isfinite(weight) or weight < 0 for weight in holdings.values()):
            return {'status': 'unavailable', 'value': None, 'reason': 'invalid_holding_weight'}
        if sum(holdings.values()) <= 0:
            return {'status': 'unavailable', 'value': None, 'reason': 'empty_holdings'}
    overlap = sum(min(holdings_a.get(ticker, 0.0), holdings_b.get(ticker, 0.0)) for ticker in holdings_a.keys() | holdings_b.keys())
    return {'status': 'available', 'value': overlap, 'unit': 'fraction_of_portfolio', 'method': 'sum_of_minimum_security_weights'}
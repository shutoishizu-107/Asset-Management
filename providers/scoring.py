"""Role-relative fund scoring with explicit missing-data coverage."""
from __future__ import annotations

from datetime import date
from typing import Any


def load_scoring_config(root) -> dict[str, Any] | None:
    import json
    from pathlib import Path

    path = Path(root) / 'config/scoring.yaml'
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f'Cannot parse scoring config {path.name}: {type(error).__name__}') from None
    if payload.get('schema_version') != 1 or not isinstance(payload.get('fund_score'), dict):
        raise ValueError('Unsupported scoring config schema')
    categories = payload['fund_score']
    total = sum(float(category.get('weight', 0)) for category in categories.values())
    if abs(total - 100) > 1e-9:
        raise ValueError('Fund score weights must total 100')
    return payload


def _numeric(record: dict[str, Any] | None, direction: str) -> float | None:
    if not record or record.get('status') != 'available':
        return None
    value = record.get('value')
    try:
        if direction == 'lower_abs':
            return -abs(float(value))
        if direction == 'fund_age':
            started = date.fromisoformat(str(value)[:10])
            return (date.today() - started).days / 365.2425
        if direction == 'diversity':
            if isinstance(value, dict):
                return float(len(value))
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _percentiles(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda item: item[1])
    result: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        rank = (index + end - 1) / 2
        score = 50.0 if len(ordered) == 1 else rank / (len(ordered) - 1) * 100
        for subject, _ in ordered[index:end]:
            result[subject] = score
        index = end
    return result


def _confidence(coverage: float, config: dict[str, Any]) -> str:
    thresholds = config.get('coverage', {})
    if coverage >= float(thresholds.get('high', 80)):
        return '高'
    if coverage >= float(thresholds.get('medium', 60)):
        return '中'
    return '低 / 参考値'


def score_funds(fund_ids: list[str], records: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    by_subject_metric = {(record.get('subject'), record.get('metric')): record for record in records}
    roles = {
        subject: str((by_subject_metric.get((subject, 'role_category')) or {}).get('value') or 'Unknown')
        for subject in fund_ids
    }
    categories = config['fund_score']
    metric_total = sum(float(metric.get('weight', 0)) for category in categories.values() for metric in category.get('metrics', {}).values())
    role_subjects: dict[str, list[str]] = {}
    for subject, role in roles.items():
        role_subjects.setdefault(role, []).append(subject)

    metric_scores: dict[tuple[str, str], float] = {}
    for category in categories.values():
        for metric, definition in category.get('metrics', {}).items():
            values = {
                subject: value
                for subject in fund_ids
                if roles[subject] in role_subjects
                for value in [_numeric(by_subject_metric.get((subject, metric)), definition.get('direction', 'higher'))]
                if value is not None
            }
            raw_scores = _percentiles(values)
            for subject, raw_score in raw_scores.items():
                direction = definition.get('direction', 'higher')
                metric_scores[(subject, metric)] = raw_score if direction in {'higher', 'fund_age', 'diversity'} else 100 - raw_score

    result = []
    for subject in fund_ids:
        category_results: dict[str, Any] = {}
        available_weight = 0.0
        quality_weight = 0.0
        quality_points = 0.0
        fit_weight = 0.0
        fit_points = 0.0
        for category_name, category in categories.items():
            category_weight = float(category['weight'])
            category_available = 0.0
            category_points = 0.0
            metric_results = {}
            for metric, definition in category.get('metrics', {}).items():
                weight = float(definition['weight'])
                score = metric_scores.get((subject, metric))
                metric_results[metric] = {
                    'score': round(score, 2) if score is not None else None,
                    'weight': weight,
                    'status': 'available' if score is not None else 'unavailable',
                }
                if score is not None:
                    category_available += weight
                    category_points += score * weight
                    available_weight += weight
            category_score = category_points / category_available if category_available else None
            category_results[category_name] = {
                'score': round(category_score, 2) if category_score is not None else None,
                'weight': category_weight,
                'available_weight': category_available,
                'metrics': metric_results,
            }
            if category_name in {'cost', 'tracking_quality', 'risk_adjusted_performance', 'downside_risk', 'scale_stability'}:
                quality_weight += category_available
                quality_points += category_points
            elif category_name == 'current_portfolio_fit':
                fit_weight += category_available
                fit_points += category_points
        total_points = sum(
            item['score'] * item['available_weight']
            for item in category_results.values()
            if item['score'] is not None
        )
        score = total_points / available_weight if available_weight else None
        coverage = available_weight / metric_total * 100 if metric_total else 0.0
        quality_score = quality_points / quality_weight if quality_weight else None
        fit_score = fit_points / fit_weight if fit_weight else None
        candidate_count = len(role_subjects.get(roles[subject], []))
        eligible = coverage >= float(config.get('ranking', {}).get('min_data_coverage', 60)) and candidate_count >= int(config.get('ranking', {}).get('min_role_candidates', 3))
        if candidate_count < 3:
            evaluation_status = '参考スコア'
        elif candidate_count < 5:
            evaluation_status = '暫定スコア'
        else:
            evaluation_status = '通常評価'
        if coverage < float(config.get('ranking', {}).get('min_data_coverage', 60)):
            evaluation_status += '（データ不足）'
        result.append({
            'fund_id': subject,
            'role': roles[subject],
            'score': round(score, 2) if score is not None else None,
            'fund_quality_score': round(quality_score, 2) if quality_score is not None else None,
            'portfolio_fit_score': round(fit_score, 2) if fit_score is not None else None,
            'data_coverage_score': round(coverage, 2),
            'confidence': _confidence(coverage, config),
            'candidate_count': candidate_count,
            'evaluation_status': evaluation_status,
            'ranking_eligible': eligible,
            'provisional': not eligible,
            'categories': category_results,
            'weighted_numerator': round(total_points, 6),
            'available_weight_denominator': round(available_weight, 6),
            'total_score_recalculated': round(total_points / available_weight, 6) if available_weight else None,
        })

    for role, subjects in role_subjects.items():
        ranked = sorted(
            (item for item in result if item['role'] == role and item['ranking_eligible'] and item['score'] is not None),
            key=lambda item: item['score'], reverse=True,
        )
        for rank, item in enumerate(ranked, 1):
            item['rank'] = rank
    return result

"""Source registry, dotenv loader, and cache-first external data collection facade."""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from .alpha_vantage import AlphaVantageProvider
from .avantis import AvantisProvider
from .collector import ProviderCollector
from .etf_com import EtfComProvider
from .evaluation import derive_holdings_summary_metrics, derive_named_overlap_metrics, evaluate_funds
from .fred import FredProvider
from .fund_flow import FundFlowProvider
from .index_provider import FtseRussellProvider, IndexProvider, MsciProvider, NasdaqIndexesProvider, SpGlobalProvider
from .invesco import InvescoProvider
from .ishares import IsharesProvider
from .models import DataRecord, public_projection, utc_now
from .metrics import calculate_expense_ratio_metrics, calculate_price_metrics, derived_records
from .morningstar import MorningstarProvider
from .mutual_fund_jp import JapaneseMutualFundProvider
from .nasdaq_data_link import NasdaqDataLinkProvider
from .public_jp import FsaProvider, ToushinProvider
from .spdr import SpdrProvider
from .tiingo import TiingoProvider
from .vanguard import VanguardProvider
from .yahoo_finance import YahooFinanceDevelopmentProvider
from .scoring import load_scoring_config


ADAPTERS = {
    'alpha_vantage': AlphaVantageProvider,
    'tiingo': TiingoProvider,
    'nasdaq_data_link': NasdaqDataLinkProvider,
    'fred': FredProvider,
    'yahoo_finance': YahooFinanceDevelopmentProvider,
    'ishares': IsharesProvider,
    'vanguard': VanguardProvider,
    'avantis': AvantisProvider,
    'invesco': InvescoProvider,
    'spdr': SpdrProvider,
    'mutual_fund_jp': JapaneseMutualFundProvider,
    'index_provider': IndexProvider,
    'msci': MsciProvider,
    'ftse_russell': FtseRussellProvider,
    'sp_global': SpGlobalProvider,
    'nasdaq_indexes': NasdaqIndexesProvider,
    'etf_com': EtfComProvider,
    'morningstar': MorningstarProvider,
    'fund_flow': FundFlowProvider,
    'fsa': FsaProvider,
    'toushin': ToushinProvider,
}


def load_fund_master(root: Path) -> dict[str, dict[str, Any]]:
    path = root / 'config/funds.json'
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f'Cannot parse fund master {path.name}: {type(error).__name__}') from None
    funds = payload.get('funds') if isinstance(payload, dict) else None
    if payload.get('schema_version') != 1 or not isinstance(funds, dict):
        raise ValueError('Unsupported fund master schema')
    return {str(subject): dict(metadata) for subject, metadata in funds.items()}


def fund_master_records(root: Path, fund_master: dict[str, dict[str, Any]]) -> list[DataRecord]:
    fetched_at = utc_now()
    records = []
    for subject, metadata in fund_master.items():
        for metric, value in (
            ('fund_name', metadata.get('fund_name')),
            ('ticker', metadata.get('ticker')),
            ('asset_class', metadata.get('asset_class')),
            ('role_category', metadata.get('role')),
        ):
            if not value:
                continue
            records.append(DataRecord(
                schema_version=1,
                provider_id='fund_master',
                provider_name='Fund Master',
                metric=metric,
                subject=subject,
                status='available',
                value=value,
                unit='text',
                source_name=str(metadata.get('provider') or 'Fund Master'),
                source_url=metadata.get('official_source'),
                fetched_at=fetched_at,
                as_of='config',
                expires_at=None,
                report_period=None,
                stale=False,
                freshness_status='fresh',
                confidence='verified',
                license_status='approved',
                cache_allowed=True,
                redistribution_allowed=True,
                raw_data_publication_allowed=True,
                derived_data_publication_allowed=True,
                data_class='raw',
                data_origin='real',
                source_role='primary',
            ))
    return records


def load_registry(path: Path) -> dict[str, Any]:
    """Load sources.yaml, expressed as a JSON-compatible YAML 1.2 document."""
    try:
        config = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f'Cannot parse source registry {path.name}: {type(error).__name__}') from None
    if config.get('schema_version') != 1 or not isinstance(config.get('providers'), dict):
        raise ValueError('Unsupported source registry schema')
    defaults = config.get('provider_defaults', {})
    required = {
        'provider_name', 'official', 'api_available', 'structured_download', 'free_tier',
        'api_key_required', 'scraping_required', 'redistribution_allowed', 'raw_data_publication_allowed',
        'derived_data_publication_allowed', 'cache_allowed', 'rate_limit', 'priority', 'metrics', 'base_url',
    }
    for provider_id, provider in config['providers'].items():
        provider['provider_id'] = provider_id
        for key, value in defaults.items():
            provider.setdefault(key, value)
        missing = required - provider.keys()
        if missing:
            raise ValueError(f'Provider {provider_id} missing fields: {", ".join(sorted(missing))}')
        provider.setdefault('enabled', False)
        provider.setdefault('resources', [])
    return config


def load_dotenv(path: Path, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Read simple KEY=VALUE entries without logging or overriding process env."""
    target = os.environ if environ is None else environ
    if not path.is_file():
        return dict(target)
    result = dict(target)
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        text = line.strip()
        if not text or text.startswith('#') or '=' not in text:
            continue
        key, value = text.split('=', 1)
        key, value = key.strip(), value.strip().strip('"\'')
        if key and key not in result:
            result[key] = value
    return result


def derive_price_history_metrics(records: list[DataRecord]) -> list[DataRecord]:
    benchmarks = {
        record.subject: record
        for record in records
        if record.metric == 'benchmark_price_history'
    }
    benchmark_names = {
        record.subject: str(record.value)
        for record in records
        if record.metric == 'benchmark' and record.status == 'available'
    }
    risk_free_rates = {
        record.subject: record
        for record in records
        if record.metric == 'risk_free_rate'
    }
    result = []
    for source in records:
        if source.metric != 'price_history':
            continue
        if source.status != 'available':
            missing_source = source.reason or 'price_series_unavailable'
            unavailable_metrics = {
                metric: {'status': 'unavailable', 'value': None, 'reason': missing_source}
                for metric in (
                    'return_1y', 'return_3y_annualized', 'return_5y_annualized',
                    'annualized_return', 'volatility', 'sharpe_ratio', 'max_drawdown',
                    'tracking_difference', 'tracking_error',
                )
            }
            result.extend(derived_records(source, unavailable_metrics))
            continue
        benchmark_name = benchmark_names.get(source.subject)
        benchmark = benchmarks.get(benchmark_name or '') or benchmarks.get(source.subject)
        benchmark_series = benchmark.value if benchmark and benchmark.status == 'available' and not benchmark.stale else None
        risk_free = risk_free_rates.get(source.subject) or risk_free_rates.get('GLOBAL')
        risk_free_annual = None
        if risk_free and risk_free.status == 'available' and not risk_free.stale:
            try:
                risk_free_annual = float(risk_free.value)
                if risk_free.unit in {'percent', 'percentage'}:
                    risk_free_annual /= 100
                elif risk_free.unit not in {'fraction', 'annual_fraction', 'decimal'}:
                    risk_free_annual = None
            except (TypeError, ValueError):
                risk_free_annual = None
        calculated = calculate_price_metrics(source.value, risk_free_annual, benchmark_series)
        result.extend(derived_records(source, calculated))
    return result


def derive_expense_metrics(records: list[DataRecord]) -> list[DataRecord]:
    result = []
    for source in records:
        if source.metric != 'expense_ratio':
            continue
        if source.status != 'available':
            unavailable = {
                'expense_ratio_bps': {
                    'status': 'unavailable',
                    'value': None,
                    'reason': source.reason or 'expense_ratio_unavailable',
                }
            }
            result.extend(derived_records(source, unavailable))
            continue
        result.extend(derived_records(source, calculate_expense_ratio_metrics(source.value)))
    return result


def derive_fund_age_metrics(records: list[DataRecord]) -> list[DataRecord]:
    result = []
    for source in records:
        if source.metric != 'inception_date' or source.status != 'available':
            continue
        try:
            inception = date.fromisoformat(str(source.value)[:10])
            as_of = date.fromisoformat(str(source.as_of or source.fetched_at)[:10])
            years = (as_of - inception).days / 365.2425
            if years < 0:
                continue
        except (TypeError, ValueError):
            continue
        result.extend(derived_records(source, {
            'fund_age': {'status': 'available', 'value': years, 'unit': 'years', 'as_of': as_of.isoformat(), 'metadata_type': None},
        }))
    return result


def collect_latest(
    root: Path,
    fetch_enabled: bool | None = None,
    persist: bool = True,
    *,
    offline: bool = False,
    force_refresh: bool = False,
    portfolio_weights: dict[str, int | float] | None = None,
    metrics: list[str] | None = None,
) -> dict[str, Any]:
    """Cache-first collection. Legacy fetch_enabled=False remains an offline alias."""
    if fetch_enabled is not None:
        if fetch_enabled:
            force_refresh = True
        else:
            offline = True
    if offline and force_refresh:
        raise ValueError('offline and force_refresh cannot be used together')

    config = load_registry(root / 'sources.yaml')
    fund_master = load_fund_master(root)
    scoring_config = load_scoring_config(root)
    collector = ProviderCollector(root, config, ADAPTERS, offline=offline, force_refresh=force_refresh, persist_cache=persist)
    records = collector.collect(metrics=metrics)
    records.extend(fund_master_records(root, fund_master))
    records.extend(derive_price_history_metrics(records))
    records.extend(derive_expense_metrics(records))
    records.extend(derive_fund_age_metrics(records))
    fund_metric_names = {
        'expense_ratio', 'total_expense_ratio', 'aum', 'fund_flow_1m', 'fund_flow_1y',
        'fund_name', 'ticker', 'asset_class', 'role_category', 'benchmark', 'inception_date', 'currency',
        'number_of_holdings', 'top10_concentration',
        'us_weight', 'tech_weight', 'small_cap_weight', 'value_exposure', 'growth_exposure',
        'nisa_tsumitate_eligible', 'nisa_growth_eligible', 'sbi_available', 'domestic_alternative',
        'holdings', 'price_history', 'nav',
    }
    api_fund_ids = sorted({
        record.subject
        for record in records
        if record.metric in fund_metric_names
        and record.subject not in {'', 'unconfigured'}
        and record.provider_id != 'analysis_engine'
    })
    records.extend(derive_holdings_summary_metrics(records, api_fund_ids))
    records.extend(derive_named_overlap_metrics(
        records,
        api_fund_ids,
        config.get('portfolio_overlap_references', {}),
        portfolio_weights,
    ))
    projected = public_projection(records)
    return {
        'fetched_at': collector.finished_at,
        'fetch_requested': not offline,
        'requests_made': collector.request_count,
        'records': [record.to_dict() for record in records],
        'public_records': projected,
        'fund_evaluations': evaluate_funds(api_fund_ids, projected, scoring_config),
        'metric_priority_level': config.get('metric_priority_level', {}),
        'scoring_config': scoring_config,
        'diagnostics': collector.diagnostics(),
        'snapshot_path': None,
    }

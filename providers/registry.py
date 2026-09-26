"""Source registry, dotenv loader, and cache-first external data collection facade."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .alpha_vantage import AlphaVantageProvider
from .avantis import AvantisProvider
from .collector import ProviderCollector
from .etf_com import EtfComProvider
from .evaluation import derive_portfolio_overlaps, evaluate_funds
from .fred import FredProvider
from .fund_flow import FundFlowProvider
from .index_provider import FtseRussellProvider, IndexProvider, MsciProvider, NasdaqIndexesProvider, SpGlobalProvider
from .invesco import InvescoProvider
from .ishares import IsharesProvider
from .models import DataRecord, public_projection
from .morningstar import MorningstarProvider
from .mutual_fund_jp import JapaneseMutualFundProvider
from .nasdaq_data_link import NasdaqDataLinkProvider
from .public_jp import FsaProvider, ToushinProvider
from .spdr import SpdrProvider
from .tiingo import TiingoProvider
from .vanguard import VanguardProvider
from .yahoo_finance import YahooFinanceDevelopmentProvider


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


def collect_latest(
    root: Path,
    fetch_enabled: bool | None = None,
    persist: bool = True,
    *,
    offline: bool = False,
    force_refresh: bool = False,
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
    collector = ProviderCollector(root, config, ADAPTERS, offline=offline, force_refresh=force_refresh, persist_cache=persist)
    records = collector.collect()
    candidate_path = root / 'docs/fund_candidates.json'
    try:
        fund_candidates = json.loads(candidate_path.read_text(encoding='utf-8-sig'))
        fund_ids = [item['fund'] for item in fund_candidates.get('items', [])]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        fund_ids = []
    records.extend(derive_portfolio_overlaps(records, fund_ids))
    projected = public_projection(records)
    return {
        'fetched_at': collector.finished_at,
        'fetch_requested': not offline,
        'requests_made': collector.request_count,
        'records': [record.to_dict() for record in records],
        'public_records': projected,
        'fund_evaluations': evaluate_funds(fund_ids, projected),
        'snapshot_path': None,
    }

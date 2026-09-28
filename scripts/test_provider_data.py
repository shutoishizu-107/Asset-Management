import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.base import ConfiguredStructuredProvider, ProviderError, request_csv, validate_safe_source_url
from cache.monthly_snapshot import MonthlySnapshotStore
from providers.evaluation import REQUIRED_FUND_METRICS, derive_holdings_summary_metrics, derive_named_overlap_metrics, derive_portfolio_overlaps, evaluate_fund_readiness
from providers.fred import FredProvider
from providers.metrics import calculate_price_metrics, weighted_portfolio_overlap
from providers.models import DataRecord, public_projection
from providers.registry import ADAPTERS, collect_latest, derive_fund_age_metrics, derive_price_history_metrics, load_registry
from providers.base import Observation
from providers.vanguard import VanguardProvider
from providers.tiingo import TiingoProvider
from providers.yahoo_finance import YahooFinanceDevelopmentProvider


def make_record(**overrides):
    values = {
        'schema_version': 1,
        'provider_id': 'test',
        'provider_name': 'Test provider',
        'metric': 'aum',
        'subject': 'TEST',
        'status': 'available',
        'value': 123.0,
        'unit': 'USD',
        'source_name': 'Test provider',
        'source_url': 'https://example.test/data.csv',
        'fetched_at': '2026-09-26T12:00:00+00:00',
        'as_of': '2026-09-25',
        'expires_at': '2026-10-25T12:00:00+00:00',
        'report_period': None,
        'stale': False,
        'freshness_status': 'fresh',
        'confidence': 'high',
        'license_status': 'approved',
        'cache_allowed': True,
        'redistribution_allowed': True,
        'raw_data_publication_allowed': True,
        'derived_data_publication_allowed': True,
        'data_class': 'raw',
        'data_origin': 'real',
        'source_role': 'primary',
        'age_days': 1,
    }
    values.update(overrides)
    return DataRecord(**values)


class ProviderDataTests(unittest.TestCase):
    def test_fund_age_is_derived_from_inception_date(self):
        source = make_record(metric='inception_date', value='2000-01-01', unit='date', as_of='2026-01-01')
        derived = derive_fund_age_metrics([source])
        self.assertEqual(len(derived), 1)
        self.assertEqual(derived[0].metric, 'fund_age')
        self.assertGreater(derived[0].value, 25)

    def test_four_fund_vanguard_template_and_fund_master(self):
        result = collect_latest(
            ROOT,
            persist=False,
            offline=True,
            metrics=['expense_ratio', 'price_history', 'benchmark_price_history', 'benchmark', 'holdings', 'region_weights', 'sector_weights'],
        )
        subjects = {
            record['subject']
            for record in result['records']
            if record['metric'] == 'fund_name' and record['status'] == 'available'
        }
        self.assertTrue({'VT', 'SPGM', 'ACWI', 'eMAXIS Slim 全世界株式（オール・カントリー）', 'SBI・V・全世界株式'} <= subjects)
        self.assertTrue({'VTI', 'VOO', 'ITOT', 'SCHB', 'SPTM'} <= subjects)
        self.assertTrue({'VXUS', 'IXUS', 'VEU', 'ACWX', 'CWI'} <= subjects)
        self.assertTrue({'VEA', 'SPDW', 'EFA', 'IDEV', 'SCHF'} <= subjects)
        for subject in {'VT', 'VTI', 'VXUS', 'VOO', 'VEA', 'VEU'}:
            self.assertTrue(any(record['subject'] == subject and record['metric'] == 'expense_ratio' for record in result['records']))
        self.assertGreaterEqual(result['diagnostics']['configured_resource_count'], 42)
        evaluations = {item['fund_id']: item for item in result['fund_evaluations']}
        self.assertEqual({evaluations['VT']['candidate_count'], evaluations['VTI']['candidate_count'], evaluations['VXUS']['candidate_count'], evaluations['VEA']['candidate_count']}, {5})
        self.assertTrue(all(evaluations[subject]['rank'] if evaluations[subject]['ranking_eligible'] else evaluations[subject]['provisional'] for subject in ('VT', 'VTI', 'VXUS', 'VEA')))

    def prepare_root(self, root: Path):
        (root / 'docs').mkdir(exist_ok=True)
        (root / 'cache').mkdir(exist_ok=True)
        shutil.copyfile(ROOT / 'sources.yaml', root / 'sources.yaml')
        shutil.copyfile(ROOT / 'cache/cache_policy.yaml', root / 'cache/cache_policy.yaml')

    def test_registry_has_candidates_but_defaults_to_disabled_and_unreviewed(self):
        registry = load_registry(ROOT / 'sources.yaml')
        self.assertGreaterEqual(len(registry['providers']), 20)
        enabled = [provider for provider in registry['providers'].values() if provider['enabled']]
        self.assertEqual(sorted(provider['provider_id'] for provider in enabled), ['fred', 'ishares', 'vanguard'])
        for provider in registry['providers'].values():
            if provider['provider_id'] in {'vanguard', 'fred', 'ishares'}:
                self.assertEqual(provider['acquisition_status'], 'approved')
                self.assertEqual(provider['license_status'], 'approved')
            else:
                self.assertNotEqual(provider['acquisition_status'], 'approved')
            self.assertFalse(provider['raw_retention_allowed'])
            self.assertIn('request_budget_per_run', provider)
            self.assertIn('rate_limit_review_status', provider)
        self.assertIn('nisa_eligibility', registry['metric_priority'])
        self.assertEqual(registry['metric_priority']['total_expense_ratio'], ['mutual_fund_jp'])
        self.assertEqual(registry['metric_priority_level']['fund_flow_1m'], 'medium')
        self.assertEqual(registry['metric_priority_level']['top10_concentration'], 'high')
        self.assertEqual(registry['metric_priority_level']['value_exposure'], 'medium')

    def test_default_collection_is_offline_and_saves_unavailable_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_root(root)
            with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('network must not be called')):
                result = collect_latest(root, fetch_enabled=False)
            self.assertTrue(result['records'])
            self.assertTrue(all(row['status'] == 'unavailable' and row['value'] is None for row in result['records']))
            self.assertIsNone(result['snapshot_path'])
            self.assertFalse((root / 'data/private/external_raw').exists())
            self.assertTrue(all(item['score'] is None and item['recommendation'] is None for item in result['fund_evaluations']))

    def test_provider_integration_is_cache_first_and_refreshable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'docs').mkdir()
            (root / 'cache').mkdir()
            shutil.copyfile(ROOT / 'cache/cache_policy.yaml', root / 'cache/cache_policy.yaml')
            registry = json.loads((ROOT / 'sources.yaml').read_text(encoding='utf-8'))
            registry['metric_priority'] = {'expense_ratio': ['vanguard']}
            provider = registry['providers']['vanguard']
            registry['providers']['fred'].update(enabled=False, resources=[])
            provider.update(
                enabled=True,
                acquisition_status='approved',
                license_status='approved',
                cache_allowed=True,
                request_budget_per_run=2,
                min_request_interval_seconds=0,
                resources=[{'metric':'expense_ratio','entity_id':'VT','url':'https://official.example/vt.csv'}],
            )
            (root / 'sources.yaml').write_text(json.dumps(registry), encoding='utf-8')

            class MockOfficialProvider:
                calls = 0
                def fetch(self, provider_config, resource, api_key=None):
                    self.calls += 1
                    observation = Observation(
                        subject='VT', metric='expense_ratio', value='0.04', unit='fraction',
                        as_of='2026-09-25', source_url=resource['url'], data_origin='sample',
                    )
                    return [observation], b'mocked-fixture-response'

            adapter = MockOfficialProvider()
            with patch.dict(ADAPTERS, {'vanguard': lambda: adapter}):
                first = collect_latest(root, persist=True)
                self.assertEqual(first['requests_made'], 1)
                cache_path = root / 'cache/funds/VT.json'
                self.assertTrue(cache_path.is_file())
                second = collect_latest(root, persist=True)
                self.assertEqual(second['requests_made'], 0)
                self.assertEqual(adapter.calls, 1)
                self.assertTrue(all(item['status'] == 'unavailable' or item['data_origin'] == 'sample' for item in second['records']))
                refreshed = collect_latest(root, persist=True, force_refresh=True)
                self.assertEqual(refreshed['requests_made'], 1)
                self.assertEqual(adapter.calls, 2)
                self.assertEqual(json.loads(cache_path.read_text(encoding='utf-8'))['metrics']['expense_ratio']['providers']['vanguard']['data']['observations'][0]['data_origin'], 'sample')

    def test_terms_and_missing_api_key_block_enabled_api_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_root(root)
            registry = json.loads((ROOT / 'sources.yaml').read_text(encoding='utf-8'))
            registry['metric_priority'] = {'price_history': ['alpha_vantage']}
            provider = registry['providers']['alpha_vantage']
            provider.update(
                enabled=True,
                resources=[{'metric': 'price_history', 'symbol': 'VT'}],
                request_budget_per_run=1,
                min_request_interval_seconds=1,
            )
            (root / 'sources.yaml').write_text(json.dumps(registry), encoding='utf-8')
            with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('unreviewed source must not be called')):
                result = collect_latest(root, fetch_enabled=True, persist=False)
            self.assertEqual(result['records'][0]['reason'], 'license_not_approved')

            provider.update(acquisition_status='approved', license_status='approved', rate_limit_review_status='approved')
            (root / 'sources.yaml').write_text(json.dumps(registry), encoding='utf-8')
            with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('missing key must block network')):
                result = collect_latest(root, fetch_enabled=True, persist=False)
            self.assertEqual(result['records'][0]['reason'], 'api_key_unavailable')

    def test_request_budget_blocks_multiple_configured_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_root(root)
            registry = json.loads((ROOT / 'sources.yaml').read_text(encoding='utf-8'))
            registry['metric_priority'] = {'price_history': ['alpha_vantage']}
            provider = registry['providers']['alpha_vantage']
            provider.update(
                enabled=True,
                acquisition_status='approved',
                license_status='approved',
                rate_limit_review_status='approved',
                api_key_required=False,
                request_budget_per_run=1,
                min_request_interval_seconds=0,
                resources=[
                    {'metric': 'price_history', 'symbol': 'TEST-A'},
                    {'metric': 'price_history', 'symbol': 'TEST-B'},
                ],
            )
            (root / 'sources.yaml').write_text(json.dumps(registry), encoding='utf-8')
            class MockPriceProvider:
                calls = 0

                def fetch(self, provider_config, resource, api_key=None):
                    self.calls += 1
                    observation = Observation(
                        subject=resource['symbol'], metric='price_history',
                        value=[{'date': '2026-09-25', 'value': 100}], unit='USD',
                        as_of='2026-09-25', source_url='https://provider.example/price', data_origin='sample',
                    )
                    return [observation], b'mock'

            mock_provider = MockPriceProvider()
            with patch.dict(ADAPTERS, {'alpha_vantage': lambda: mock_provider}):
                result = collect_latest(root, fetch_enabled=True, persist=False)
            self.assertEqual(mock_provider.calls, 1)
            self.assertTrue(any(record.get('reason') == 'request_budget_exceeded' for record in result['records']))
            self.assertEqual({item['fund_id'] for item in result['fund_evaluations']}, {'TEST-A', 'TEST-B'})

    def test_subject_metric_resource_map_is_resolved_without_legacy_resources_array(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_root(root)
            registry = json.loads((ROOT / 'sources.yaml').read_text(encoding='utf-8'))
            registry['metric_priority'] = {'expense_ratio': ['vanguard']}
            registry['providers']['fred'].update(enabled=False, resources=[])
            provider = registry['providers']['vanguard']
            provider.update(
                enabled=True,
                acquisition_status='approved',
                license_status='approved',
                cache_allowed=True,
                request_budget_per_run=1,
                min_request_interval_seconds=0,
                resources=[],
                resource_map={
                    'VT': {
                        'expense_ratio': {
                            'url': 'https://official.example/vt.csv',
                            'format': 'csv',
                            'columns': {
                                'as_of': 'as_of',
                                'value': 'expense_ratio',
                                'metric': 'expense_ratio',
                                'unit': 'fraction',
                            },
                        }
                    }
                },
            )
            (root / 'sources.yaml').write_text(json.dumps(registry), encoding='utf-8')

            class MockOfficialProvider:
                calls = 0

                def fetch(self, provider_config, resource, api_key=None):
                    self.calls += 1
                    observation = Observation(
                        subject='VT', metric='expense_ratio', value='0.05', unit='fraction',
                        as_of='2026-09-25', source_url=resource['url'], data_origin='sample',
                    )
                    return [observation], b'mocked-fixture-response'

            adapter = MockOfficialProvider()
            with patch.dict(ADAPTERS, {'vanguard': lambda: adapter}):
                result = collect_latest(root, persist=False)

            self.assertEqual(adapter.calls, 1)
            self.assertEqual(result['requests_made'], 1)
            self.assertEqual(result['diagnostics']['configured_resource_count'], 1)
            self.assertEqual(result['records'][0]['subject'], 'VT')
            self.assertEqual(result['records'][0]['metric'], 'expense_ratio')

    def test_source_urls_require_https_and_reject_embedded_credentials(self):
        with self.assertRaisesRegex(ProviderError, 'https_url_required'):
            validate_safe_source_url('http://example.test/data.csv')
        with self.assertRaisesRegex(ProviderError, 'credentials_in_source_url_forbidden'):
            validate_safe_source_url('https://user:secret@example.test/data.csv')
        with self.assertRaisesRegex(ProviderError, 'credential_in_source_url_forbidden'):
            validate_safe_source_url('https://example.test/data?api_key=secret')

    def test_cache_and_publication_permissions_both_gate_values(self):
        no_cache = public_projection([make_record(cache_allowed=False)])[0]
        self.assertEqual((no_cache['status'], no_cache['value']), ('unavailable', None))
        no_redistribution = public_projection([make_record(raw_data_publication_allowed='verify')])[0]
        self.assertEqual((no_redistribution['status'], no_redistribution['value']), ('unavailable', None))
        sample = public_projection([make_record(data_origin='sample')])[0]
        self.assertEqual((sample['status'], sample['value']), ('unavailable', None))
        unavailable = make_record(status='unavailable', value=None, as_of=None, confidence='unavailable', stale=True, freshness_status='unavailable')
        self.assertEqual(public_projection([unavailable])[0]['status'], 'unavailable')

    def test_price_metrics_are_calculated_and_missing_inputs_stay_unavailable(self):
        series = [
            {'date': '2023-01-01', 'value': 100},
            {'date': '2023-07-01', 'value': 120},
            {'date': '2024-01-01', 'value': 90},
            {'date': '2024-07-01', 'value': 110},
        ]
        metrics = calculate_price_metrics(series)
        self.assertEqual(metrics['annualized_return']['status'], 'available')
        self.assertEqual(metrics['max_drawdown']['value'], -0.25)
        self.assertEqual(metrics['return_3y_annualized']['status'], 'unavailable')
        self.assertEqual(metrics['return_5y_annualized']['status'], 'unavailable')
        self.assertEqual(metrics['sharpe_ratio']['reason'], 'risk_free_series_unavailable')
        self.assertEqual(metrics['tracking_difference']['reason'], 'benchmark_series_unavailable')
        self.assertEqual(calculate_price_metrics(None)['annualized_return']['status'], 'unavailable')

    def test_period_returns_and_tracking_error_require_aligned_history(self):
        series = [
            {'date': f'{year}-01-01', 'value': 100 * (1.1 ** (year - 2020))}
            for year in range(2020, 2026)
        ]
        benchmark = [
            {'date': f'{year}-01-01', 'value': 100 * (1.05 ** (year - 2020))}
            for year in range(2020, 2026)
        ]
        metrics = calculate_price_metrics(series, risk_free_annual=0.02, benchmark_series=benchmark)
        self.assertAlmostEqual(metrics['return_1y']['value'], 0.1, places=6)
        self.assertAlmostEqual(metrics['return_3y_annualized']['value'], 0.1, places=4)
        self.assertAlmostEqual(metrics['return_5y_annualized']['value'], 0.1, places=4)
        self.assertEqual(metrics['tracking_difference']['status'], 'available')
        self.assertEqual(metrics['tracking_error']['status'], 'available')

    def test_price_series_validation_and_derived_metrics(self):
        series = [
            {'date': '2020-01-01', 'value': 100},
            {'date': '2021-01-01', 'value': 120},
            {'date': '2022-01-01', 'value': 90},
            {'date': '2023-01-01', 'value': 150},
            {'date': '2024-01-01', 'value': 180},
            {'date': '2025-01-01', 'value': 200},
            {'date': '2026-01-01', 'value': 220},
        ]
        metrics = calculate_price_metrics(series)
        self.assertEqual(metrics['return_5y_annualized']['status'], 'available')
        self.assertEqual(metrics['volatility']['status'], 'available')
        self.assertLess(metrics['max_drawdown']['value'], 0)
        duplicate = calculate_price_metrics(series + [{'date': '2026-01-01', 'value': 221}])
        self.assertEqual(duplicate['annualized_return']['reason'], 'duplicate_price_dates')
        invalid = calculate_price_metrics([{'date': '2020-01-01', 'value': 0}, {'date': '2021-01-01', 'value': 1}])
        self.assertEqual(invalid['annualized_return']['reason'], 'price_must_be_finite_and_positive')

    def test_tiingo_adjusted_close_metadata_without_network(self):
        provider = TiingoProvider()
        payload = [
            {'date': '2020-01-01T00:00:00.000Z', 'adjClose': 100},
            {'date': '2021-01-01T00:00:00.000Z', 'adjClose': 110},
        ]
        with patch('providers.tiingo.request_json', return_value=(payload, b'fixture')):
            observations, _ = provider.fetch(
                {'base_url': 'https://api.tiingo.com/tiingo/daily'},
                {'ticker': 'ACWI', 'metric': 'price_history', 'price_field': 'adjClose'},
                api_key='test-key',
            )
        self.assertEqual(observations[0].series_type, 'adjusted_close')
        self.assertEqual(observations[0].frequency, 'daily')
        self.assertEqual(observations[0].currency, 'USD')

    def test_price_history_is_emitted_as_derived_metric_records(self):
        series = [
            {'date': f'{year}-01-01', 'value': 100 * (1.1 ** (year - 2020))}
            for year in range(2020, 2026)
        ]
        source = make_record(metric='price_history', subject='FUND-A', value=series, as_of='2025-01-01')
        benchmark = make_record(metric='benchmark_price_history', subject='FUND-A', value=series, as_of='2025-01-01')
        risk_free = make_record(metric='risk_free_rate', subject='FUND-A', value=0.02, unit='fraction', as_of='2025-01-01')

        derived = derive_price_history_metrics([source, benchmark, risk_free])
        by_metric = {record.metric: record for record in derived}
        self.assertEqual(by_metric['return_1y'].status, 'available')
        self.assertEqual(by_metric['return_3y_annualized'].status, 'available')
        self.assertEqual(by_metric['return_5y_annualized'].status, 'available')
        self.assertEqual(by_metric['sharpe_ratio'].status, 'available')
        self.assertEqual(by_metric['tracking_difference'].status, 'available')
        self.assertEqual(by_metric['tracking_error'].status, 'available')

    def test_unavailable_price_source_reason_propagates_to_derived_metrics(self):
        source = make_record(
            metric='price_history', subject='unconfigured', status='unavailable', value=None,
            as_of=None, freshness_status='unavailable', reason='resource_not_configured',
            confidence='unavailable', stale=True,
        )

        derived = derive_price_history_metrics([source])

        self.assertEqual(len(derived), 9)
        self.assertTrue(all(record.status == 'unavailable' for record in derived))
        self.assertTrue(all(record.reason == 'dependency_failed' for record in derived))
        self.assertTrue(all(record.dependency == 'price_history' for record in derived))
        self.assertTrue(all(record.root_cause == 'resource_not_configured' for record in derived))

    def test_vt_e2e_minimal_fetch_cache_derived_snapshot_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'docs').mkdir()
            (root / 'cache').mkdir()
            shutil.copyfile(ROOT / 'cache/cache_policy.yaml', root / 'cache/cache_policy.yaml')
            registry = json.loads((ROOT / 'sources.yaml').read_text(encoding='utf-8'))
            registry['metric_priority'] = {'expense_ratio': ['vanguard']}
            provider = registry['providers']['vanguard']
            provider.update(
                enabled=True,
                acquisition_status='approved',
                license_status='approved',
                cache_allowed=True,
                raw_data_publication_allowed=True,
                derived_data_publication_allowed=True,
                request_budget_per_run=1,
                min_request_interval_seconds=0,
                resources=[],
                resource_map={'VT': {'expense_ratio': {'url': 'https://investor.vanguard.com/irr/funds/profile/VT'}}},
            )
            (root / 'sources.yaml').write_text(json.dumps(registry), encoding='utf-8')

            class MockVanguardProvider:
                calls = 0

                def fetch(self, provider_config, resource, api_key=None):
                    self.calls += 1
                    return [Observation(
                        subject='VT', metric='expense_ratio', value='0.0006', unit='fraction',
                        as_of='2026-09-25', source_url=resource['url'], data_origin='real',
                    )], b'mock-json', {'http_status': 200}

            adapter = MockVanguardProvider()
            with patch.dict(ADAPTERS, {'vanguard': lambda: adapter}):
                first = collect_latest(root, persist=True)
                self.assertEqual(first['requests_made'], 1)
                first_records = first['records']
                self.assertTrue(any(row['metric'] == 'expense_ratio' and row['subject'] == 'VT' and row['status'] == 'available' for row in first_records))
                self.assertTrue(any(row['metric'] == 'expense_ratio_bps' and row['subject'] == 'VT' and row['status'] == 'available' for row in first_records))
                cache_path = root / 'cache/funds/VT.json'
                self.assertTrue(cache_path.is_file())
                cached = json.loads(cache_path.read_text(encoding='utf-8'))['metrics']['expense_ratio']['providers']['vanguard']
                self.assertEqual(cached['http_status'], 200)
                self.assertEqual(cached['source_url'], 'https://investor.vanguard.com/irr/funds/profile/VT')
                self.assertIn('fetched_at', cached)
                self.assertIn('as_of', cached)
                self.assertIn('expires_at', cached)
                self.assertIn('license_status', cached)
                self.assertIn('confidence', cached)
                self.assertIn('data', cached)

                second = collect_latest(root, persist=True)
                self.assertEqual(second['requests_made'], 0)
                self.assertEqual(adapter.calls, 1)
                self.assertGreaterEqual(second['diagnostics']['fetch_status_counts'].get('cache_hit', 0), 1)

                market = MonthlySnapshotStore(root)
                saved = market.write('2026-09-26', second['public_records'], second['fund_evaluations'])
                self.assertTrue((saved['path'] / 'manifest.json').is_file())

    def test_vt_e2e_failure_matrix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare_root(root)
            base_registry = json.loads((ROOT / 'sources.yaml').read_text(encoding='utf-8'))
            base_registry['metric_priority'] = {'expense_ratio': ['vanguard']}

            def run_once(provider_patch, adapter_impl=None, persist=True):
                registry = json.loads(json.dumps(base_registry))
                provider = registry['providers']['vanguard']
                provider.update(
                    acquisition_status='approved',
                    license_status='approved',
                    cache_allowed=True,
                    request_budget_per_run=1,
                    min_request_interval_seconds=0,
                    resources=[],
                    resource_map={'VT': {'expense_ratio': {'url': 'https://investor.vanguard.com/irr/funds/profile/VT'}}},
                    enabled=True,
                )
                provider.update(provider_patch)
                (root / 'sources.yaml').write_text(json.dumps(registry), encoding='utf-8')
                if adapter_impl is None:
                    return collect_latest(root, fetch_enabled=True, persist=persist)
                with patch.dict(ADAPTERS, {'vanguard': lambda: adapter_impl}):
                    return collect_latest(root, fetch_enabled=True, persist=persist)

            disabled = run_once({'enabled': False})
            self.assertTrue(any(row.get('reason') == 'provider_disabled' for row in disabled['records']))

            not_approved = run_once({'acquisition_status': 'unreviewed', 'license_status': 'unreviewed'})
            self.assertTrue(any(row.get('reason') == 'license_not_approved' for row in not_approved['records']))

            missing_resource = run_once({'resource_map': {}, 'resources': []})
            self.assertTrue(any(row.get('reason') == 'resource_not_configured' for row in missing_resource['records']))

            class HttpErrorProvider:
                def fetch(self, provider_config, resource, api_key=None):
                    raise ProviderError('http_status_500')

            http_error = run_once({}, HttpErrorProvider())
            self.assertTrue(any(row.get('reason') == 'http_status_500' for row in http_error['records']))

            class MalformedProvider:
                def fetch(self, provider_config, resource, api_key=None):
                    return [], b'malformed', {'http_status': 200}

            malformed = run_once({}, MalformedProvider())
            self.assertTrue(any(row.get('reason') == 'provider_returned_no_observations' for row in malformed['records']))

            class SuccessProvider:
                calls = 0
                def fetch(self, provider_config, resource, api_key=None):
                    self.calls += 1
                    return [Observation(
                        subject='VT', metric='expense_ratio', value='0.0006', unit='fraction',
                        as_of='2026-09-25', source_url=resource['url'], data_origin='real',
                    )], b'ok', {'http_status': 200}

            success = SuccessProvider()
            first = run_once({}, success, persist=True)
            self.assertEqual(first['requests_made'], 1)

            class FailingProvider:
                def fetch(self, provider_config, resource, api_key=None):
                    raise ProviderError('network_error')

            stale = run_once({}, FailingProvider(), persist=True)
            self.assertTrue(any(row.get('metric') == 'expense_ratio' and row.get('status') == 'available' and row.get('stale') is True for row in stale['records']))

    def test_holdings_overlap_requires_complete_holdings(self):
        overlap = weighted_portfolio_overlap({'AAA': 0.6, 'BBB': 0.4}, {'AAA': 0.3, 'CCC': 0.7}, complete_a=True, complete_b=True)
        self.assertEqual(overlap['status'], 'available')
        self.assertEqual(overlap['value'], 0.3)
        self.assertEqual(weighted_portfolio_overlap({'AAA': 0.6}, {'AAA': 0.4}, complete_a=False, complete_b=True)['status'], 'unavailable')
        self.assertEqual(weighted_portfolio_overlap(None, {'AAA': 1}, complete_a=False, complete_b=True)['reason'], 'holdings_unavailable')

    def test_overlap_records_require_complete_same_source_same_date_holdings(self):
        common = dict(metric='holdings', unit='fraction', data_class='raw')
        source_a = make_record(subject='FUND-A', value={'security_id': 'AAA', 'weight': '0.6', 'complete': True}, **common)
        source_b = make_record(subject='FUND-A', value={'security_id': 'BBB', 'weight': '0.4', 'complete': True}, **common)
        other_a = make_record(subject='FUND-B', value={'security_id': 'AAA', 'weight': '0.3', 'complete': True}, **common)
        other_b = make_record(subject='FUND-B', value={'security_id': 'CCC', 'weight': '0.7', 'complete': True}, **common)
        result = derive_portfolio_overlaps([source_a, source_b, other_a, other_b], ['FUND-A', 'FUND-B'])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].status, 'available')
        self.assertEqual(result[0].value, 0.3)
        partial = make_record(subject='FUND-B', value={'security_id': 'AAA', 'weight': '0.3', 'complete': False}, **common)
        unavailable = derive_portfolio_overlaps([source_a, source_b, partial], ['FUND-A', 'FUND-B'])[0]
        self.assertEqual(unavailable.reason, 'holdings_not_complete')
        date_mismatch_record = make_record(subject='FUND-B', value={'security_id': 'AAA', 'weight': '0.3', 'complete': True}, as_of='2026-09-20', **common)
        mismatch = derive_portfolio_overlaps([source_a, source_b, date_mismatch_record], ['FUND-A', 'FUND-B'])[0]
        self.assertEqual(mismatch.reason, 'holdings_source_or_as_of_mismatch')

    def test_named_overlaps_cover_reference_funds_and_weighted_current_portfolio(self):
        def holding(subject, security_id, weight):
            return make_record(
                metric='holdings', subject=subject,
                value={'security_id': security_id, 'weight': str(weight), 'complete': True},
                unit='fraction',
            )

        records = [
            holding('CANDIDATE', 'AAA', 0.6), holding('CANDIDATE', 'BBB', 0.4),
            holding('SP500', 'AAA', 0.3), holding('SP500', 'CCC', 0.7),
            holding('FANG', 'AAA', 0.5), holding('FANG', 'BBB', 0.5),
            holding('ALL_COUNTRY', 'BBB', 0.7), holding('ALL_COUNTRY', 'CCC', 0.3),
            holding('CURRENT_A', 'AAA', 1.0), holding('CURRENT_B', 'BBB', 1.0),
        ]
        metrics = derive_named_overlap_metrics(
            records,
            ['CANDIDATE'],
            {
                'overlap_with_sp500': 'SP500',
                'overlap_with_fang': 'FANG',
                'overlap_with_all_country': 'ALL_COUNTRY',
            },
            {'CURRENT_A': 100, 'CURRENT_B': 100},
        )
        by_metric = {record.metric: record for record in metrics}
        self.assertEqual(by_metric['overlap_with_sp500'].value, 0.3)
        self.assertEqual(by_metric['overlap_with_fang'].value, 0.9)
        self.assertEqual(by_metric['overlap_with_all_country'].value, 0.4)
        self.assertEqual(by_metric['overlap_with_current_portfolio'].value, 0.9)

        missing = derive_named_overlap_metrics(records, ['CANDIDATE'], {}, None)
        current_overlap = next(record for record in missing if record.metric == 'overlap_with_current_portfolio')
        self.assertEqual(current_overlap.status, 'unavailable')

    def test_fund_readiness_never_scores_missing_or_stale_inputs(self):
        result = evaluate_fund_readiness('FUND-A', [])
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(result['score'])
        self.assertIsNone(result['recommendation'])
        self.assertEqual(len(result['missing_or_stale_metrics']), len(REQUIRED_FUND_METRICS))

    def test_complete_holdings_derive_count_and_top10_concentration(self):
        holdings = [
            make_record(metric='holdings', subject='FUND-A', value={
                'security_id': f'SEC-{index}', 'weight': str(weight), 'complete': True,
            }, unit='fraction')
            for index, weight in enumerate((0.20, 0.18, 0.15, 0.12, 0.10, 0.08, 0.06, 0.04, 0.03, 0.02, 0.02))
        ]
        summary = derive_holdings_summary_metrics(holdings, ['FUND-A'])
        by_metric = {record.metric: record for record in summary}
        self.assertEqual(by_metric['number_of_holdings'].value, 11)
        self.assertAlmostEqual(by_metric['top10_concentration'].value, 0.98)
        self.assertEqual(by_metric['top10_holdings'].status, 'available')
        self.assertEqual(len(by_metric['top10_holdings'].value), 10)
        self.assertEqual(by_metric['top10_holdings'].value[0]['security_id'], 'SEC-0')

    def test_aggregate_region_rows_are_not_treated_as_individual_holdings(self):
        holdings = [
            make_record(metric='holdings', subject='VT', value={
                'security_id': 'REGION::North America',
                'weight': '0.65',
                'complete': True,
            }, unit='fraction'),
            make_record(metric='holdings', subject='VT', value={
                'security_id': 'REGION::Europe',
                'weight': '0.35',
                'complete': True,
            }, unit='fraction'),
        ]
        summary = derive_holdings_summary_metrics(holdings, ['VT'])
        by_metric = {record.metric: record for record in summary}
        self.assertEqual(by_metric['number_of_holdings'].status, 'unavailable')
        self.assertEqual(by_metric['number_of_holdings'].reason, 'dependency_failed')
        self.assertEqual(by_metric['number_of_holdings'].dependency, 'holdings')
        self.assertEqual(by_metric['number_of_holdings'].root_cause, 'individual_holdings_unavailable')
        self.assertEqual(by_metric['top10_concentration'].status, 'unavailable')
        self.assertEqual(by_metric['top10_holdings'].status, 'unavailable')

    def test_aggregate_sector_rows_are_not_treated_as_individual_holdings(self):
        holdings = [
            make_record(metric='holdings', subject='VT', value={
                'security_id': 'SECTOR::Technology',
                'weight': '0.50',
                'complete': True,
            }, unit='fraction'),
            make_record(metric='holdings', subject='VT', value={
                'security_id': 'SECTOR::Financials',
                'weight': '0.50',
                'complete': True,
            }, unit='fraction'),
        ]
        summary = derive_holdings_summary_metrics(holdings, ['VT'])
        by_metric = {record.metric: record for record in summary}
        self.assertEqual(by_metric['number_of_holdings'].status, 'unavailable')
        self.assertEqual(by_metric['number_of_holdings'].root_cause, 'individual_holdings_unavailable')

    def test_holdings_unavailable_from_source_propagates_to_top10_metrics(self):
        source = make_record(
            provider_id='vanguard', provider_name='Vanguard', metric='holdings', subject='VT', status='unavailable',
            value=None, as_of=None, freshness_status='unavailable', confidence='unavailable', stale=True,
            reason='holdings_not_available_from_source',
        )
        summary = derive_holdings_summary_metrics([source], ['VT'])
        by_metric = {record.metric: record for record in summary}
        self.assertEqual(by_metric['number_of_holdings'].reason, 'dependency_failed')
        self.assertEqual(by_metric['number_of_holdings'].root_cause, 'individual_holdings_unavailable')
        self.assertEqual(by_metric['top10_concentration'].reason, 'dependency_failed')
        self.assertEqual(by_metric['top10_holdings'].reason, 'dependency_failed')

    def test_vanguard_region_weights_remain_available_when_holdings_unavailable(self):
        provider = VanguardProvider()
        payload = {
            'dashboard': {'expenseRatio': '0.06', 'expenseRatioAsOfDate': '02/27/2026'},
            'portfolioComposition': {
                'weightedExposures': {
                    'region': {
                        'asOfDate': '08/31/2026',
                        'exposure': [
                            {'name': 'North America', 'value': '65.0565'},
                            {'name': 'Europe', 'value': '13.8614'},
                        ],
                    },
                    'sectorExposure': {'isavailable': False, 'exposure': []},
                }
            },
            'performancefees': {'totalReturns': {'annualReturn': {'asOfDate': '12/31/2025'}}},
        }
        with patch.object(VanguardProvider, '_request_json', return_value=(payload, b'{}', 200)):
            region_rows, _, _ = provider.fetch({}, {'metric': 'region_weights', 'subject': 'VT', 'url': 'https://investor.vanguard.com/irr/funds/profile/VT'})
            self.assertEqual(region_rows[0].metric, 'region_weights')
            with self.assertRaisesRegex(ProviderError, 'holdings_not_available_from_source'):
                provider.fetch({}, {'metric': 'holdings', 'subject': 'VT', 'url': 'https://investor.vanguard.com/irr/funds/profile/VT'})

    def test_fred_risk_free_rate_is_normalized_to_annual_fraction(self):
        provider = FredProvider()
        payload = {
            'observations': [
                {'date': '2026-08-01', 'value': '4.80'},
                {'date': '2026-09-01', 'value': '4.92'},
            ]
        }

        class _Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, _size=None):
                return json.dumps(payload).encode('utf-8')

        class _Opener:
            def open(self, _request, timeout=20):
                _ = timeout
                return _Response()

        with patch('urllib.request.build_opener', return_value=_Opener()):
            observations, _raw, metadata = provider.fetch(
                {'base_url': 'https://api.stlouisfed.org/fred/series/observations'},
                {
                    'metric': 'risk_free_rate',
                    'subject': 'GLOBAL',
                    'series_id': 'TB3MS',
                    'unit': 'annual_fraction',
                    'value_scale': 'percent_annualized',
                },
                api_key='dummy-key',
            )
        self.assertEqual(metadata['http_status'], 200)
        self.assertEqual(observations[0].subject, 'GLOBAL')
        self.assertEqual(observations[0].metric, 'risk_free_rate')
        self.assertEqual(observations[0].unit, 'annual_fraction')
        self.assertAlmostEqual(float(observations[0].value), 0.0492, places=8)

    def test_structured_csv_reader_normalizes_holdings_rows(self):
        provider = ConfiguredStructuredProvider()
        resource = {
            'url': 'https://official.example/holdings.csv',
            'format': 'csv',
            'subject': 'FUND-A',
            'holdings_complete': True,
            'weight_unit': 'percent',
            'columns': {
                'as_of': 'as_of',
                'value': 'weight',
                'metric': 'holdings',
                'subject': 'fund',
                'security_id': 'ticker',
                'report_period': 'period',
                'unit': 'fraction',
            },
        }
        rows = [
            {'as_of': '2026-09-25', 'weight': '60.0', 'fund': 'FUND-A', 'ticker': 'AAA', 'period': '2026-09-25'},
            {'as_of': '2026-09-25', 'weight': '40.0', 'fund': 'FUND-A', 'ticker': 'BBB', 'period': '2026-09-25'},
        ]
        with patch('providers.base.request_csv', return_value=(rows, b'fixture-bytes')):
            observations, raw = provider.fetch({'provider_name': 'Official', 'max_staleness_days': 5}, resource)
        self.assertEqual(raw, b'fixture-bytes')
        self.assertEqual([row.value['weight'] for row in observations], ['0.6', '0.4'])
        self.assertTrue(all(row.value['complete'] for row in observations))
        self.assertTrue(all(row.report_period == '2026-09-25' for row in observations))

    def test_structured_source_scales_aum_and_rejects_aggregate_holdings(self):
        provider = ConfiguredStructuredProvider()
        aum_resource = {
            'url': 'https://official.example/aum.csv', 'format': 'csv', 'value_scale': 'billion',
            'columns': {'as_of': 'as_of', 'value': 'aum', 'metric': 'aum', 'unit': 'USD'},
        }
        with patch('providers.base.request_csv', return_value=([{'as_of': '2026-09-25', 'aum': '2.5'}], b'aum')):
            observations, _ = provider.fetch({'provider_name': 'Official'}, aum_resource)
        self.assertEqual(observations[0].value, '2500000000')

        holdings_resource = {
            'url': 'https://official.example/holdings.csv', 'format': 'csv', 'holdings_complete': True,
            'columns': {'as_of': 'as_of', 'value': 'weight', 'metric': 'holdings', 'security_id': 'security_id'},
        }
        rows = [{'as_of': '2026-09-25', 'weight': '1.0', 'security_id': 'REGION::North America'}]
        with patch('providers.base.request_csv', return_value=(rows, b'aggregate')):
            with self.assertRaises(ProviderError):
                provider.fetch({'provider_name': 'Official'}, holdings_resource)

    def test_structured_csv_rejects_html_product_page(self):
        with patch('providers.base.request_bytes', return_value=b'<!doctype html><html></html>'):
            with self.assertRaisesRegex(ProviderError, 'structured_response_is_html'):
                request_csv('https://official.example/product.csv')

    def test_development_fixture_is_marked_sample_and_not_publishable(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / 'sample.json'
            fixture.write_text(json.dumps({'prices': [{'date': '2026-01-01', 'close': 100}, {'date': '2026-02-01', 'close': 101}]}), encoding='utf-8')
            provider = YahooFinanceDevelopmentProvider()
            observations, _ = provider.fetch({'provider_id': 'yahoo_finance'}, {'fixture_path': str(fixture), 'subject': 'TEST', 'metric': 'price_history'})
            self.assertEqual(observations[0].data_origin, 'sample')
            self.assertTrue(observations[0].source_url.startswith('fixture://'))


if __name__ == '__main__':
    unittest.main()

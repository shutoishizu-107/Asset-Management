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

from providers.base import ConfiguredStructuredProvider, ProviderError, validate_safe_source_url
from providers.evaluation import derive_portfolio_overlaps, evaluate_fund_readiness
from providers.metrics import calculate_price_metrics, weighted_portfolio_overlap
from providers.models import DataRecord, public_projection
from providers.registry import ADAPTERS, collect_latest, load_registry
from providers.base import Observation
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
    def prepare_root(self, root: Path):
        (root / 'docs').mkdir(exist_ok=True)
        (root / 'cache').mkdir(exist_ok=True)
        shutil.copyfile(ROOT / 'sources.yaml', root / 'sources.yaml')
        shutil.copyfile(ROOT / 'cache/cache_policy.yaml', root / 'cache/cache_policy.yaml')
        shutil.copyfile(ROOT / 'docs/fund_candidates.json', root / 'docs/fund_candidates.json')

    def test_registry_has_candidates_but_defaults_to_disabled_and_unreviewed(self):
        registry = load_registry(ROOT / 'sources.yaml')
        self.assertGreaterEqual(len(registry['providers']), 20)
        for provider in registry['providers'].values():
            self.assertFalse(provider['enabled'])
            self.assertNotEqual(provider['acquisition_status'], 'approved')
            self.assertFalse(provider['raw_retention_allowed'])
            self.assertIn('request_budget_per_run', provider)
            self.assertIn('rate_limit_review_status', provider)
        self.assertIn('nisa_eligibility', registry['metric_priority'])
        self.assertEqual(registry['metric_priority']['total_expense_ratio'], ['mutual_fund_jp'])

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
            shutil.copyfile(ROOT / 'docs/fund_candidates.json', root / 'docs/fund_candidates.json')
            shutil.copyfile(ROOT / 'cache/cache_policy.yaml', root / 'cache/cache_policy.yaml')
            registry = json.loads((ROOT / 'sources.yaml').read_text(encoding='utf-8'))
            registry['metric_priority'] = {'expense_ratio': ['vanguard']}
            provider = registry['providers']['vanguard']
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
        self.assertEqual(metrics['sharpe_ratio']['reason'], 'risk_free_series_unavailable')
        self.assertEqual(metrics['tracking_difference']['reason'], 'benchmark_series_unavailable')
        self.assertEqual(calculate_price_metrics(None)['annualized_return']['status'], 'unavailable')

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

    def test_fund_readiness_never_scores_missing_or_stale_inputs(self):
        result = evaluate_fund_readiness('FUND-A', [])
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(result['score'])
        self.assertIsNone(result['recommendation'])
        self.assertEqual(len(result['missing_or_stale_metrics']), 4)

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

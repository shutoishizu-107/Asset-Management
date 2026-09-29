import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cache.manager import CacheManager
from cache.monthly_snapshot import MonthlySnapshotStore
from cache.policy import CachePolicy, CachePolicyError


class FakeClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class CacheManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.policy = CachePolicy.load(ROOT / 'cache/cache_policy.yaml')
        self.clock = FakeClock(datetime(2026, 9, 26, 12, tzinfo=timezone.utc))
        self.manager = CacheManager(self.root, self.policy, self.clock)
        self.provider = {
            'provider_id': 'vanguard',
            'provider_name': 'Vanguard',
            'cache_allowed': True,
            'license_status': 'verified',
            'redistribution_allowed': 'verify',
            'raw_data_publication_allowed': False,
            'derived_data_publication_allowed': 'verify',
        }
        self.fetch_calls = 0

    def tearDown(self):
        self.temp.cleanup()

    def fetch_success(self, value=123.45, as_of='2026-09-25'):
        def fetcher():
            self.fetch_calls += 1
            return {
                'data': value,
                'provider': 'Vanguard',
                'source_url': 'https://investor.vanguard.com/fund-data',
                'as_of': as_of,
                'license_status': 'verified',
                'report_period': None,
            }
        return fetcher

    def test_fresh_cache_is_used_without_provider_call(self):
        first = self.manager.get('VT', 'expense_ratio', self.provider, self.fetch_success())
        self.assertEqual(first.status, 'fresh')
        self.assertEqual(self.fetch_calls, 1)
        second = self.manager.get('VT', 'expense_ratio', self.provider, self.fetch_success(999))
        self.assertEqual(second.status, 'fresh')
        self.assertEqual(second.entry['data'], 123.45)
        self.assertEqual(self.fetch_calls, 1)

    def test_expired_cache_fetch_success_updates_provider_entry(self):
        self.manager.get('VT', 'aum', self.provider, self.fetch_success(1000))
        self.clock.value += timedelta(days=8)
        refreshed = self.manager.get('VT', 'aum', self.provider, self.fetch_success(1100))
        self.assertEqual(refreshed.status, 'fresh')
        self.assertEqual(refreshed.entry['data'], 1100)
        self.assertEqual(refreshed.entry['expires_at'], '2026-10-11T12:00:00+00:00')
        path_data = json.loads(self.manager.cache_path('VT', 'aum').read_text(encoding='utf-8'))
        self.assertEqual(path_data['metrics']['aum']['providers']['vanguard']['data'], 1100)

    def test_expired_fetch_failure_returns_stale_successful_value(self):
        self.manager.get('VT', 'holdings', self.provider, self.fetch_success({'top10': ['AAA']}))
        self.clock.value += timedelta(days=15)
        stale = self.manager.get('VT', 'holdings', self.provider, lambda: self._raise('provider_timeout'))
        self.assertEqual(stale.status, 'stale')
        self.assertTrue(stale.entry['stale'])
        self.assertEqual(stale.entry['data'], {'top10': ['AAA']})
        self.assertEqual(stale.entry['fetch_status'], 'stale_fallback')
        self.assertEqual(stale.age_days, 15)

    def test_holdings_semantic_error_does_not_use_stale_fallback(self):
        self.manager.get('VT', 'holdings', self.provider, self.fetch_success({'top10': ['AAA']}))
        self.clock.value += timedelta(days=15)

        class SemanticError(Exception):
            code = 'holdings_not_available_from_source'

        result = self.manager.get('VT', 'holdings', self.provider, lambda: (_ for _ in ()).throw(SemanticError()))
        self.assertEqual(result.status, 'unavailable')
        self.assertEqual(result.reason, 'holdings_not_available_from_source')
        self.assertIsNone(result.entry)

    def test_missing_cache_and_fetch_failure_is_unavailable(self):
        result = self.manager.get('VTI', 'aum', self.provider, lambda: self._raise('provider_down'))
        self.assertEqual(result.status, 'unavailable')
        self.assertIsNone(result.entry)

    def test_offline_mode_never_calls_provider_and_uses_stale_cache(self):
        self.manager.get('VXUS', 'aum', self.provider, self.fetch_success(500))
        self.clock.value += timedelta(days=8)
        result = self.manager.get('VXUS', 'aum', self.provider, lambda: self._raise('must_not_call'), offline=True)
        self.assertEqual(result.status, 'stale')
        self.assertEqual(result.reason, 'offline_cache_expired')
        self.assertEqual(self.fetch_calls, 1)
        missing = self.manager.get('VOO', 'aum', self.provider, lambda: self._raise('must_not_call'), offline=True)
        self.assertEqual(missing.status, 'unavailable')
        self.assertEqual(missing.reason, 'offline_cache_miss')

    def test_force_refresh_ignores_a_fresh_cache(self):
        self.manager.get('AVUV', 'expense_ratio', self.provider, self.fetch_success(0.25))
        refreshed = self.manager.get('AVUV', 'expense_ratio', self.provider, self.fetch_success(0.26), force_refresh=True)
        self.assertEqual(refreshed.entry['data'], 0.26)
        self.assertEqual(self.fetch_calls, 2)

    def test_metric_specific_ttl_is_loaded_from_yaml(self):
        self.assertEqual(self.policy.ttl_days('expense_ratio'), 30)
        self.assertEqual(self.policy.ttl_days('total_expense_ratio'), 180)
        self.assertEqual(self.policy.ttl_days('aum'), 7)
        self.assertEqual(self.policy.ttl_days('holdings'), 14)
        self.assertEqual(self.policy.ttl_days('fund_flow'), 30)
        self.assertEqual(self.policy.ttl_days('benchmark'), 365)
        self.assertEqual(self.policy.ttl_days('nisa_eligibility'), 30)
        self.assertEqual(self.policy.ttl_days('price_history'), 1)
        self.assertEqual(self.policy.ttl_days('pe_ratio'), 7)
        self.assertEqual(self.policy.ttl_days('pb_ratio'), 7)
        self.assertEqual(self.policy.ttl_days('index_region_weights'), 30)
        self.assertEqual(self.policy.ttl_days('index_sector_weights'), 30)
        with self.assertRaises(CachePolicyError):
            self.policy.ttl_days('not_configured')

    def test_malformed_cache_is_ignored_and_replaced_on_success(self):
        path = self.manager.cache_path('QQQM', 'aum')
        path.parent.mkdir(parents=True)
        path.write_text('{broken json', encoding='utf-8')
        result = self.manager.get('QQQM', 'aum', self.provider, self.fetch_success(800))
        self.assertEqual(result.status, 'fresh')
        payload = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(payload['metrics']['aum']['providers']['vanguard']['data'], 800)

    def test_zero_and_empty_values_are_not_cached_as_success(self):
        for value in (0, 0.0, '0', '', None, [], {}):
            with self.subTest(value=value):
                result = self.manager.get('GLDM', 'aum', self.provider, self.fetch_success(value))
                self.assertEqual(result.status, 'unavailable')
                self.assertIsNone(result.entry)
        self.assertFalse(self.manager.cache_path('GLDM', 'aum').exists())

    def test_provider_cache_entry_preserves_metadata_and_stale_flag(self):
        result = self.manager.get('BND', 'total_expense_ratio', self.provider, self.fetch_success(0.08))
        for key in ('provider', 'source_url', 'fetched_at', 'as_of', 'expires_at', 'stale', 'license_status', 'data', 'last_successful_fetch_at', 'content_hash'):
            self.assertIn(key, result.entry)
        self.assertFalse(result.entry['stale'])
        self.assertEqual(result.entry['as_of'], '2026-09-25')
        self.assertEqual(result.entry['report_period'], None)

    def test_revoked_cache_permission_blocks_existing_cache(self):
        self.manager.get('AVDV', 'aum', self.provider, self.fetch_success(321))
        revoked = dict(self.provider, cache_allowed='verify')
        result = self.manager.get('AVDV', 'aum', revoked, lambda: self._raise('offline'), offline=True)
        self.assertEqual(result.status, 'unavailable')
        self.assertEqual(result.reason, 'cache_not_permitted')

    def test_entity_file_supports_same_metric_from_multiple_providers(self):
        self.manager.get('VT', 'aum', self.provider, self.fetch_success(100))
        other = dict(self.provider, provider_id='blackrock', provider_name='BlackRock')
        self.manager.get('VT', 'aum', other, self.fetch_success(200))
        payload = json.loads(self.manager.cache_path('VT', 'aum').read_text(encoding='utf-8'))
        self.assertEqual(set(payload['metrics']['aum']['providers']), {'vanguard', 'blackrock'})

    def _raise(self, message):
        raise RuntimeError(message)


class MonthlySnapshotTests(unittest.TestCase):
    def test_snapshot_splits_categories_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MonthlySnapshotStore(Path(directory))
            records = [
                {'metric':'expense_ratio','subject':'VT','status':'unavailable','value':None},
                {'metric':'index_region_weights','subject':'MSCI_ACWI','status':'unavailable','value':None},
                {'metric':'cpi','subject':'FRED_CPI','status':'unavailable','value':None},
                {'metric':'nisa_eligibility','subject':'FUND-X','status':'unavailable','value':None},
            ]
            first = store.write('2026-09-26', records, [])
            self.assertTrue((first['path'] / 'funds.json').is_file())
            self.assertTrue((first['path'] / 'indexes.json').is_file())
            self.assertTrue((first['path'] / 'macro.json').is_file())
            self.assertTrue((first['path'] / 'nisa.json').is_file())
            second = store.write('2026-09-26', [], [], force_revision=False)
            self.assertEqual(second['path'], first['path'])
            self.assertEqual(len(list((Path(directory) / 'data/market/2026-09-26').glob('revisions/*'))), 0)
            revision = store.write('2026-09-26', records, [], force_revision=True)
            self.assertNotEqual(revision['path'], first['path'])
            latest = store.latest('2026-09')
            self.assertEqual(latest['path'], revision['path'])
            self.assertEqual(len(latest['records']), 4)

    def test_corrupt_monthly_snapshot_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'data/market/2026-09-26'
            folder.mkdir(parents=True)
            (folder / 'manifest.json').write_text('{bad', encoding='utf-8')
            store = MonthlySnapshotStore(Path(directory))
            self.assertIsNone(store.latest('2026-09'))

    def test_monthly_snapshot_lookup_is_scoped_to_selected_date(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MonthlySnapshotStore(Path(directory))
            first = store.write('2026-09-20', [{'metric': 'expense_ratio', 'subject': 'A'}], [])
            second = store.write('2026-09-26', [{'metric': 'expense_ratio', 'subject': 'B'}], [])

            self.assertEqual(store.latest('2026-09', '2026-09-20')['path'], first['path'])
            self.assertEqual(store.latest('2026-09', '2026-09-26')['path'], second['path'])
            self.assertEqual(store.write('2026-09-20', [], [])['path'], first['path'])


if __name__ == '__main__':
    unittest.main()
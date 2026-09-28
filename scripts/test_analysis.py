import copy
import io
import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from html.parser import HTMLParser

from analyze_holdings import parse_text, decode_csv, load_snapshot, percentage, integer, compare, contribution_projection, run
from history_analysis import parse_export, reconcile, analyze_history
from history_report import goal_projection, required_goal_return, nisa_lifetime_exhaustion
from reporting import render_report
from report_sections import render_portfolio_options

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / 'data/raw/csv'


class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (RAW / '保有状況_20260926_20260926.csv').read_bytes()
        cls.text = decode_csv(cls.raw)[0]
        cls.snapshot = load_snapshot(RAW / '保有状況_20260926_20260926.csv')

    def test_totals_are_details_only(self):
        s = self.snapshot
        self.assertEqual(len(s['holdings']), 11)
        self.assertEqual(len(s['funds']), 6)
        self.assertEqual(len(s['accounts']), 5)
        self.assertEqual(s['totals']['value_yen'], 10592284)
        self.assertEqual(s['totals']['cost_yen'], 7228144)
        self.assertEqual(s['totals']['gain_yen'], 3364140)
        self.assertEqual(s['totals']['day_change_yen'], 42563)
        self.assertEqual(s['totals']['gain_pct'], '46.54')

    def test_both_encodings(self):
        for encoding in ('utf-8-sig', 'cp932'):
            parsed, sections = parse_text(decode_csv(self.text.encode(encoding))[0])
            self.assertEqual(len(parsed), 11)
            self.assertEqual(sections['全体']['summary']['gain_yen'], 3364140)

    def test_corrupt_summary_fails(self):
        with self.assertRaisesRegex(ValueError, 'Summary mismatch'):
            parse_text(self.text.replace('10592284', '10592285', 1))

    def test_missing_or_duplicate_detail_fails(self):
        lines = self.text.splitlines()
        target = next(i for i, line in enumerate(lines) if '715022' in line)
        for changed in (lines[:target] + lines[target + 1:], lines[:target] + [lines[target]] + lines[target:]):
            with self.assertRaises(ValueError):
                parse_text('\n'.join(changed))

    def test_no_decimal_truncation_and_zero_denominator(self):
        with self.assertRaises(ValueError):
            integer('1.2')
        self.assertIsNone(percentage(0, 0))
        self.assertEqual(percentage(-2192, 50004), '-4.38')

    def test_history_counts_and_no_double_counting(self):
        h = analyze_history(RAW, self.snapshot['holdings'])
        self.assertEqual(len(h['orders']['records']), 132)
        self.assertEqual(len(h['trades']['records']), 53)
        self.assertEqual((h['buy_count'], h['sell_count']), (52, 1))
        self.assertEqual((h['buy_yen'], h['sell_yen']), (2750000, 200000))
        self.assertEqual(len(h['reconciliation']), 51)
        self.assertEqual(len(h['unmatched_buys']), 1)
        self.assertEqual(h['unmatched_buys'][0]['amount_yen'], 200000)
        statuses = {r['status']: r for r in h['statuses']}
        self.assertEqual(statuses['完了']['amount_yen'], 5749000)
        self.assertEqual(statuses['買付余力不足']['count'], 5)
        self.assertEqual(statuses['取引不可']['count'], 12)

    def test_partial_history_export_fails(self):
        data = decode_csv((RAW / '約定履歴_20240927_20260926.csv').read_bytes())[0]
        rows = list(csv.reader(io.StringIO(data)))
        i = next(i for i, r in enumerate(rows) if r and r[0] == '商品指定')
        rows[i + 1][-1] = '52'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'partial.csv'
            with path.open('w', encoding='utf-8', newline='') as f:
                csv.writer(f).writerows(rows)
            with self.assertRaisesRegex(ValueError, 'Partial export'):
                parse_export(path, 'trades')

    def test_ambiguous_match_not_silently_accepted(self):
        orders = parse_export(RAW / '積立買付注文履歴_20210927_20260926.csv', 'orders')['records']
        trades = parse_export(RAW / '約定履歴_20240927_20260926.csv', 'trades')['records']
        match, missing = reconcile(orders + [copy.deepcopy(orders[0])], trades)
        self.assertEqual(len(match), 50)
        self.assertTrue(any(r['match_candidates'] == 2 for r in missing))

    def test_projection_arithmetic(self):
        rows = contribution_projection(self.snapshot, {'monthly_contributions_yen': {'S&P500系': 45000, 'オール・カントリー': 90000, 'FANG+': 15000}})
        after = {r['category']: r for r in rows if r['months'] == 60}
        self.assertEqual(sum(r['value_yen'] for r in after.values()), 19592284)
        self.assertEqual(after['FANG+']['value_yen'], 2072366)
        self.assertEqual(after['FANG+']['weight_pct'], '10.58')

    def test_goal_projection_uses_age_based_contributions(self):
        profile = json.loads((ROOT / 'config/profile.json').read_text(encoding='utf-8'))
        goal = json.loads((ROOT / 'config/goals.json').read_text(encoding='utf-8'))
        goal = {**profile, **goal}
        as_of = '2026-09-26T15:19:45'
        base = goal_projection(10592284, as_of, 0, False, goal)
        increased = goal_projection(10592284, as_of, 0, True, goal)
        self.assertEqual(base, {'value_yen': 36242284, 'contributions_yen': 25650000, 'months': 135})
        self.assertEqual(increased, {'value_yen': 39242284, 'contributions_yen': 28650000, 'months': 135})
        self.assertEqual(round(required_goal_return(10592284, as_of, False, goal), 2), 13.68)

    def test_nisa_snapshot_and_lifetime_projection(self):
        assumptions = json.loads((ROOT / 'docs/analysis_assumptions.json').read_text(encoding='utf-8'))
        profile = json.loads((ROOT / 'config/profile.json').read_text(encoding='utf-8'))
        goal = json.loads((ROOT / 'config/goals.json').read_text(encoding='utf-8'))
        nisa = assumptions['nisa_snapshot']
        goal = {**profile, **goal}
        self.assertEqual(nisa['annual']['total']['used_yen'], nisa['annual']['growth']['used_yen'] + nisa['annual']['tsumitate']['used_yen'])
        self.assertEqual(nisa['annual']['total']['remaining_yen'], 2250000)
        self.assertEqual(nisa['lifetime']['total']['remaining_yen'], 14488536)
        base = nisa_lifetime_exhaustion(nisa['source_date'], goal, nisa)
        increased = nisa_lifetime_exhaustion(nisa['source_date'], goal, nisa, True)
        self.assertEqual((base['month'], base['age_years'], base['taxable_yen']), ('2033-05', 30, 161464))
        self.assertEqual((increased['month'], increased['age_years'], increased['taxable_yen']), ('2033-04', 30, 161464))

    def test_comparison_new_and_removed_holdings(self):
        old = copy.deepcopy(self.snapshot)
        new = copy.deepcopy(self.snapshot)
        new['holdings'] = new['holdings'][1:]
        additional = copy.deepcopy(new['holdings'][0])
        additional['fund'] = 'NEW FUND'
        new['holdings'].append(additional)
        result = compare(new, old)
        self.assertEqual(sum(r['status'] == 'added' for r in result['rows']), 1)
        self.assertEqual(sum(r['status'] == 'removed' for r in result['rows']), 1)
        self.assertEqual(sum(r['value_yen_delta'] for r in result['rows']), additional['value_yen'] - old['holdings'][0]['value_yen'])

    def test_end_to_end_and_html(self):
        before = {p.name: p.read_bytes() for p in RAW.glob('*.csv')}
        with tempfile.TemporaryDirectory() as directory:
            info = run(ROOT, Path(directory))
            report = Path(directory) / 'reports/monthly/資産分析_202609.html'
            html = report.read_text(encoding='utf-8')
            for text in ('10,592,284', '2,750,000', 'Executive Summary', '35歳で1億円', 'NISA戦略・利用状況', '1,350,000円', '14,488,536円', '19.5%', '2033年5月', '2033年4月', '推奨ポートフォリオ設計', '全世界株コア', '小型株・バリュー', '成長株サテライト', '候補ファンド比較', '70%', '15%', '10%', '5%', '全売却'):
                self.assertIn(text, html)
            self.assertIn('Vanguard Total World Stock ETF (VT)', html)
            self.assertIn('経費率', html)
            self.assertIn('0.06%', html)
            self.assertIn('6.00 bps', html)
            self.assertNotIn('return_3y_annualized (derived)', html)
            self.assertNotIn('(high)', html)
            self.assertNotIn('5.999999999999999', html)
            self.assertIn('Provider Coverage', html)
            self.assertIn('Scoring Methodology', html)
            self.assertIn('href="#fund-detail-vt"', html)
            self.assertEqual(html.count('<details id="fund-detail-'), 20)
            comparison_start = html.index('候補ファンド比較')
            methodology_start = html.index('id="data-methodology"')
            self.assertNotIn('<details id="fund-detail-', html[comparison_start:methodology_start])
            for hidden_candidate_content in ('投資候補ファンド比較', '候補21本', 'ジャンル別・優先候補', '投資対象（日本語）', 'eMAXIS Slim オルカン|SBI'):
                self.assertNotIn(hidden_candidate_content, html)
            self.assertNotIn('fund_candidates', info)
            evaluations = {item['fund_id']: item for item in info['external_data']['fund_evaluations']}
            self.assertEqual(set(evaluations), {
                'VT', 'VTI', 'VXUS', 'VOO', 'SPGM', 'ACWI',
                'eMAXIS Slim 全世界株式（オール・カントリー）', 'SBI・V・全世界株式',
                'ITOT', 'SCHB', 'SPTM', 'VEU', 'IXUS', 'ACWX', 'CWI',
                'VEA', 'SPDW', 'EFA', 'IDEV', 'SCHF',
            })
            self.assertTrue(all(item['provisional'] for item in evaluations.values()))
            self.assertNotIn('portfolio_overlap', {record['metric'] for record in info['external_data']['records']})
            self.assertLess(html.index('全世界株コア'), html.index('小型株・バリュー'))
            self.assertIn('バリュー特性', html)
            section_order = [html.index(marker) for marker in ('id="summary"', 'id="goal"', 'id="portfolio"', 'id="diagnosis"', 'id="nisa"', 'id="portfolio-options"', 'id="contributions"', 'id="existing-assets"', 'id="risk"', 'id="data-methodology"')]
            self.assertEqual(section_order, sorted(section_order))
            self.assertNotIn('今後の推奨ポートフォリオ', html)
            for excluded in ('余力不足', '未完了注文額', '差引購入額', '純買付口数'):
                self.assertNotIn(excluded, html)
            self.assertTrue((Path(directory) / 'reports/charts/資産配分_202609.html').is_file())
            chart = (Path(directory) / 'reports/charts/資産配分_202609.html').read_text(encoding='utf-8')
            self.assertIn('href="../../index.html">Dashboard', chart)
            self.assertIn('href="../index.html">月次レポート一覧', chart)
            self.assertIn('href="../../index.html">Dashboard', html)
            self.assertIn('href="../index.html">月次レポート一覧', html)
            index = (Path(directory) / 'reports/index.html').read_text(encoding='utf-8')
            self.assertIn('monthly/資産分析_202609.html', index)
            self.assertIn('charts/資産配分_202609.html', index)
            dashboard = (Path(directory) / 'index.html').read_text(encoding='utf-8')
            self.assertIn('Asset Management Dashboard', dashboard)
            self.assertIn('Goal Projection', dashboard)
            self.assertIn('reports/monthly/資産分析_202609.html', dashboard)
            self.assertIn('2026-09-25画面転記', dashboard)
            self.assertIn('10.6', dashboard)
            for developer_text in ('CSVを配置', 'Python 3.10', 'Branch Strategy'):
                self.assertNotIn(developer_text, dashboard)
            self.assertNotIn('<script src=', html)
            self.assertNotIn('https://cdn', html)
            self.assertNotIn('S&amp;amp;P', html)
            HTMLParser().feed(html)
            info['snapshot']['funds'][0]['name'] = '<script>alert(1)</script>'
            self.assertIn('&lt;script&gt;', render_report(info, 'test'))
            self.assertNotIn('<script>alert(1)</script>', render_report(info, 'test'))
        self.assertEqual(before, {p.name: p.read_bytes() for p in RAW.glob('*.csv')})

    def test_portfolio_metric_table_uses_only_api_fund_records(self):
        with tempfile.TemporaryDirectory() as directory:
            info = run(ROOT, Path(directory))
            info['external_data']['records'] = [
                {
                    'provider_id': 'official_fund_api', 'metric': 'expense_ratio',
                    'subject': 'API-FUND', 'status': 'available', 'value': 0.001,
                    'freshness_status': 'fresh', 'source_role': 'primary', 'fetched_at': '2026-09-26',
                },
                {
                    'provider_id': 'analysis_engine', 'metric': 'overlap_with_sp500',
                    'subject': 'API-FUND', 'status': 'unavailable', 'value': None,
                    'freshness_status': 'unavailable', 'reason': 'holdings_unavailable',
                },
                {
                    'provider_id': 'analysis_engine', 'metric': 'portfolio_overlap',
                    'subject': 'OLD-FUND-A|OLD-FUND-B', 'status': 'unavailable', 'value': None,
                },
            ]
            info['external_data']['metric_priority_level'] = {'expense_ratio': 'high'}
            html = render_portfolio_options(info)
            self.assertIn('API-FUND', html)
            self.assertIn('候補ファンド比較', html)
            self.assertIn('経費率', html)
            self.assertIn('0.10%', html)
            self.assertNotIn('S&amp;P500との重複率', html)
            self.assertIn('data-methodology', html)
            self.assertIn('#fund-detail-api-fund', html)
            self.assertIn('未取得', html)
            self.assertNotIn('expense_ratio (high)', html)
            self.assertNotIn('overlap_with_sp500', html)
            self.assertNotIn('OLD-FUND-A', html)

    def test_monthly_run_prefers_immutable_external_snapshot(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / 'data/raw', root / 'data/raw')
            shutil.copytree(ROOT / 'config', root / 'config')
            (root / 'docs').mkdir()
            for name in ('analysis_assumptions.json', 'data_sources.json', 'handover.md'):
                shutil.copyfile(ROOT / 'docs' / name, root / 'docs' / name)
            shutil.copyfile(ROOT / 'sources.yaml', root / 'sources.yaml')
            shutil.copytree(ROOT / 'cache', root / 'cache')

            first = run(root, month='2026-09', offline=True)
            first_path = first['external_data']['monthly_snapshot_path']
            self.assertTrue((root / first_path / 'manifest.json').is_file())
            manifest = json.loads((root / first_path / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['metric_priority_level'].get('expense_ratio'), 'high')
            with patch('providers.registry.collect_latest', side_effect=AssertionError('monthly snapshot must be preferred')):
                second = run(root, month='2026-09', offline=False)
            self.assertEqual(second['external_data']['monthly_snapshot_path'], first_path)
            self.assertFalse(second['external_data']['fetch_requested'])

            refreshed_collection = {
                'fetched_at': '2026-09-26T22:00:00+00:00',
                'fetch_requested': True,
                'requests_made': 0,
                'public_records': first['external_data']['records'],
                'fund_evaluations': [],
                'snapshot_path': None,
            }
            with patch('providers.registry.collect_latest', return_value=refreshed_collection):
                refreshed = run(root, month='2026-09', refresh_external_data=True)
            self.assertNotEqual(refreshed['external_data']['monthly_snapshot_path'], first_path)
            self.assertEqual(len(list((root / 'data/market/2026-09-26/revisions').iterdir())), 1)


if __name__ == '__main__':
    unittest.main()

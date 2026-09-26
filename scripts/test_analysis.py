import copy
import io
import csv
import json
import tempfile
import unittest
from pathlib import Path
from html.parser import HTMLParser

from analyze_holdings import parse_text, decode_csv, load_snapshot, percentage, integer, compare, contribution_projection, run
from history_analysis import parse_export, reconcile, analyze_history
from history_report import goal_projection, required_goal_return
from reporting import render_report

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
        goal = {
            'birth_year': 2003,
            'birth_month': 1,
            'target_age': 35,
            'target_yen': 100000000,
            'contributions_yen': {
                'through_age_25': 150000,
                'age_26_to_29': 200000,
                'age_30_plus_base': 200000,
                'age_30_plus_tentative': 250000,
            },
        }
        as_of = '2026-09-26T15:19:45'
        base = goal_projection(10592284, as_of, 0, False, goal)
        increased = goal_projection(10592284, as_of, 0, True, goal)
        self.assertEqual(base, {'value_yen': 36242284, 'contributions_yen': 25650000, 'months': 135})
        self.assertEqual(increased, {'value_yen': 39242284, 'contributions_yen': 28650000, 'months': 135})
        self.assertEqual(round(required_goal_return(10592284, as_of, False, goal), 2), 13.68)

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
            for text in ('10,592,284', '2,750,000', '価格変動', '35歳で1億円', '30歳から月25万円', '案A：成長株を上乗せ', '案B：地域分散', '案C：Gold 10%', '案D：Gold 5%', '候補21本', '全世界（除く米国）', '0.1838%'):
                self.assertIn(text, html)
            self.assertNotIn('今後の推奨ポートフォリオ', html)
            self.assertNotIn('積立案と期間を変更して確認', html)
            self.assertEqual(len(info['fund_candidates']['items']), 21)
            self.assertIn('基本コア', html)
            for excluded in ('余力不足', '売却', '未完了注文額', '差引購入額', '純買付口数'):
                self.assertNotIn(excluded, html)
            self.assertTrue((Path(directory) / 'reports/charts/資産配分_202609.html').is_file())
            index = (Path(directory) / 'reports/index.html').read_text(encoding='utf-8')
            self.assertIn('monthly/資産分析_202609.html', index)
            self.assertIn('charts/資産配分_202609.html', index)
            self.assertNotIn('<script src=', html)
            self.assertNotIn('https://cdn', html)
            HTMLParser().feed(html)
            info['snapshot']['funds'][0]['name'] = '<script>alert(1)</script>'
            self.assertIn('&lt;script&gt;', render_report(info, 'test'))
            self.assertNotIn('<script>alert(1)</script>', render_report(info, 'test'))
        self.assertEqual(before, {p.name: p.read_bytes() for p in RAW.glob('*.csv')})


if __name__ == '__main__':
    unittest.main()

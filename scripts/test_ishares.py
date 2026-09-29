import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.base import ProviderError
from providers.ishares import IsharesProvider


class IsharesTests(unittest.TestCase):
    def setUp(self):
        self.provider = IsharesProvider()
        self.url = 'https://www.ishares.com/us/products/239600/ishares-msci-acwi-etf'
        self.page = (
            '<html><script type="application/ld+json">' + json.dumps({
                '@graph': [{
                    '@type': 'PropertyValue',
                    'additionalProperty': [
                        {'name': 'Net Assets of Fund', 'value': '$33,286,389,581', 'valueReference': {'name': 'As of Dates', 'value': 'Sep 25, 2026'}},
                        {'name': 'Fund Inception', 'value': 'Mar 26, 2008'},
                        {'name': 'Expense Ratio:', 'value': '0.32', 'valueReference': {'name': 'As of Dates', 'value': 'Aug 31, 2026'}},
                        {'name': 'Benchmark Index', 'value': 'MSCI All Country World Index (Net)'},
                    ],
                }, {
                    '@type': 'DataDownload',
                    'name': 'iShares MSCI ACWI ETF Holdings CSV',
                    'contentUrl': 'https://www.ishares.com/us/products/239600/latest-holdings.csv',
                }]
            }) + '</script></html>'
        ).encode()
        self.holdings = b'''iShares MSCI ACWI ETF\nFund Holdings as of,"Sep 24, 2026"\n\nTicker,Name,Sector,Asset Class,Market Value,Weight (%),Quantity\nNVDA,NVIDIA,Technology,Equity,100,60,1\nAAPL,APPLE,Technology,Equity,66,40,1\n'''

    def test_fund_facts_parse_static_and_snapshot_metrics(self):
        with patch('providers.ishares.request_bytes', return_value=self.page):
            facts = {}
            for metric in ('expense_ratio', 'aum', 'inception_date', 'benchmark'):
                facts[metric] = self.provider.fetch({}, {'url': self.url, 'subject': 'ACWI', 'metric': metric})[0][0]
        self.assertEqual(facts['expense_ratio'].value, '0.0032')
        self.assertEqual(facts['expense_ratio'].as_of, '2026-08-31')
        self.assertIsNone(facts['expense_ratio'].metadata_type)
        self.assertIsNone(facts['expense_ratio'].report_period)
        self.assertEqual(facts['aum'].value, '33286389581')
        self.assertEqual(facts['aum'].as_of, '2026-09-25')
        self.assertEqual(facts['inception_date'].value, '2008-03-26')
        self.assertIsNone(facts['inception_date'].as_of)
        self.assertEqual(facts['inception_date'].metadata_type, 'static')
        self.assertEqual(facts['benchmark'].value, 'MSCI All Country World Index (Net)')

    def test_holdings_download_is_discovered_and_validated(self):
        def response(url, headers=None):
            return self.page if url == self.url else self.holdings

        with patch('providers.ishares.request_bytes', side_effect=response):
            observations, raw = self.provider.fetch({}, {'url': self.url, 'subject': 'ACWI', 'metric': 'holdings'})
        self.assertEqual(len(observations), 2)
        self.assertEqual(observations[0].as_of, '2026-09-24')
        self.assertTrue(all(item.value['complete'] for item in observations))
        self.assertEqual(raw, self.holdings)

    def test_html_masquerading_holdings_is_rejected(self):
        with patch('providers.ishares.request_bytes', side_effect=[self.page, b'<!doctype html><html></html>']):
            with self.assertRaisesRegex(ProviderError, 'structured_response_is_html'):
                self.provider.fetch({}, {'url': self.url, 'subject': 'ACWI', 'metric': 'holdings'})

    def test_malformed_facts_fail_closed(self):
        with patch('providers.ishares.request_bytes', return_value=b'<html><script type="application/ld+json">{}</script></html>'):
            with self.assertRaisesRegex(ProviderError, 'parser_schema_mismatch'):
                self.provider.fetch({}, {'url': self.url, 'subject': 'ACWI', 'metric': 'aum'})


if __name__ == '__main__':
    unittest.main()

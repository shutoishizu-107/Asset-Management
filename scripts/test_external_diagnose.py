import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.diagnose_external_data import classify_stage, _parse_metrics


class ExternalDiagnoseTests(unittest.TestCase):
    def test_stage_classification(self):
        self.assertEqual(classify_stage('vanguard', 'provider_disabled'), 'STAGE1_CONFIG')
        self.assertEqual(classify_stage('fred', 'api_key_unavailable'), 'STAGE2_AUTH')
        self.assertEqual(classify_stage('alpha_vantage', 'http_status_429'), 'STAGE3_NETWORK')
        self.assertEqual(classify_stage('vanguard', 'provider_returned_no_observations'), 'STAGE4_RESPONSE')
        self.assertEqual(classify_stage('vanguard', 'invalid_json'), 'STAGE5_NORMALIZE')
        self.assertEqual(classify_stage('analysis_engine', 'holdings_unavailable'), 'STAGE6_DERIVED')
        self.assertEqual(classify_stage('vanguard', 'offline_cache_miss'), 'STAGE7_CACHE_SNAPSHOT')

    def test_parse_metrics(self):
        self.assertIsNone(_parse_metrics(None))
        self.assertIsNone(_parse_metrics(' , '))
        self.assertEqual(_parse_metrics('expense_ratio, aum ,risk_free_rate'), ['expense_ratio', 'aum', 'risk_free_rate'])


if __name__ == '__main__':
    unittest.main()

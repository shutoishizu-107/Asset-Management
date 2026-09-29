import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from providers.scoring import load_scoring_config, score_funds


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.config = load_scoring_config(ROOT)

    @staticmethod
    def record(subject, metric, value, role='test_role'):
        if metric == 'role_category':
            value = role
        return {'subject': subject, 'metric': metric, 'status': 'available', 'value': value}

    def test_weight_total_is_100(self):
        total = sum(category['weight'] for category in self.config['fund_score'].values())
        self.assertEqual(total, 100)

    def test_percentile_direction_and_missing_coverage(self):
        records = []
        for subject, expense in (('A', 0.01), ('B', 0.02), ('C', 0.03)):
            records.extend([
                self.record(subject, 'role_category', 'test_role'),
                self.record(subject, 'expense_ratio', expense),
                self.record(subject, 'tracking_error', expense),
            ])
        results = {item['fund_id']: item for item in score_funds(['A', 'B', 'C'], records, self.config)}
        self.assertGreater(results['A']['categories']['cost']['metrics']['expense_ratio']['score'], results['C']['categories']['cost']['metrics']['expense_ratio']['score'])
        self.assertGreater(results['A']['data_coverage_score'], 0)
        self.assertLess(results['A']['data_coverage_score'], 60)
        self.assertIsNone(results['A']['categories']['cost']['metrics']['total_expense_ratio']['score'])
        self.assertFalse(results['A']['ranking_eligible'])

    def test_role_candidate_shortfall_is_provisional(self):
        records = [
            self.record('A', 'role_category', 'one_role'),
            self.record('A', 'expense_ratio', 0.01),
            self.record('B', 'role_category', 'other_role'),
            self.record('B', 'expense_ratio', 0.02),
        ]
        results = {item['fund_id']: item for item in score_funds(['A', 'B'], records, self.config)}
        self.assertTrue(results['A']['provisional'])
        self.assertIsNone(results['A'].get('rank'))

    def test_scores_stay_in_range(self):
        records = []
        for subject in ('A', 'B', 'C'):
            records.append(self.record(subject, 'role_category', 'test_role'))
            for metric, value in (
                ('expense_ratio', 0.01), ('tracking_difference', 0.0),
                ('tracking_error', 0.01), ('sharpe_ratio', 1.0),
                ('return_5y_annualized', 0.1), ('return_3y_annualized', 0.1),
                ('max_drawdown', -0.1), ('volatility', 0.1), ('aum', 1_000_000),
                ('number_of_holdings', 100), ('top10_concentration', 0.2),
                ('overlap_with_current_portfolio', 0.1),
            ):
                records.append(self.record(subject, metric, value))
        for item in score_funds(['A', 'B', 'C'], records, self.config):
            self.assertIsNotNone(item['score'])
            self.assertGreaterEqual(item['score'], 0)
            self.assertLessEqual(item['score'], 100)
            self.assertGreaterEqual(item['fund_quality_score'], 0)
            self.assertLessEqual(item['fund_quality_score'], 100)

    def test_zero_coverage_category_is_null_and_excluded_from_portfolio_fit(self):
        records = [
            self.record('A', 'role_category', 'test_role'),
            self.record('A', 'expense_ratio', 0.01),
            self.record('A', 'aum', 1_000_000),
            self.record('A', 'inception_date', '2000-01-01'),
        ]
        result = score_funds(['A'], records, self.config)[0]
        self.assertIsNone(result['categories']['current_portfolio_fit']['score'])
        self.assertEqual(result['categories']['current_portfolio_fit']['available_weight'], 0)
        self.assertIsNone(result['portfolio_fit_score'])
        self.assertAlmostEqual(result['total_score_recalculated'], result['weighted_numerator'] / result['available_weight_denominator'])

    def test_available_metric_weight_is_counted_once(self):
        records = [self.record('A', 'role_category', 'test_role'), self.record('A', 'expense_ratio', 0.01)]
        result = score_funds(['A'], records, self.config)[0]
        self.assertEqual(result['categories']['cost']['available_weight'], 12)
        self.assertEqual(result['data_coverage_score'], 12)
        self.assertNotEqual(result['categories']['cost']['score'], 0)


if __name__ == '__main__':
    unittest.main()

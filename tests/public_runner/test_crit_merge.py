"""Interactions between the 2026-11-26 critique slices after the merge."""
import copy
import json
import unittest
from pathlib import Path

from investmentideas import valuation
from tests.public_runner.test_market_consistency import plan as glide_plan


class HorizonRowsWithPathsTests(unittest.TestCase):
    """Horizon rows cut or extend per-year sales-to-capital and R&D share paths."""

    def plan(self):
        plan = glide_plan()
        for scenario in plan['scenarios'].values():
            years = len(scenario['growth_rates'])
            scenario['sales_to_capital'] = {'year1': 2.0, 'target': 3.0, 'target_year': 5}
            scenario['rnd_share_path'] = [0.08 + 0.002 * year for year in range(years)]
        return plan

    def test_rows_compute_with_paths(self):
        result = valuation.evaluate(self.plan())
        rows = {row['name']: row for row in result['convention_panel']['rows'] if row.get('kind') == 'horizon'}
        self.assertTrue(rows, 'no horizon rows')
        for name, row in rows.items():
            if name.endswith(('_5_years', '_15_years', '_20_years')):
                self.assertNotEqual(row['status'], 'not_calculated', (name, row.get('reason')))
                self.assertIsInstance(row['per_share_value'], float, name)

    def test_resized_paths_hold_the_last_year(self):
        scenario = {'sales_to_capital_path': [1.0, 2.0, 3.0], 'rnd_share_path': [0.1, 0.2, 0.3], 'other': 1}
        self.assertEqual(valuation._resized_paths(scenario, 5)['sales_to_capital_path'], [1.0, 2.0, 3.0, 3.0, 3.0])
        self.assertEqual(valuation._resized_paths(scenario, 2)['rnd_share_path'], [0.1, 0.2])
        self.assertEqual(valuation._resized_paths(scenario, 2)['other'], 1)


if __name__ == '__main__':
    unittest.main()

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


class LargerRndTests(unittest.TestCase):
    """Reported R&D is the larger of the inclusive concept and its part (J&J, fiscal 2025)."""

    def fact(self, concept, value):
        from datetime import date
        from decimal import Decimal
        value = Decimal(value)
        return {'concept': concept, 'parsed_start': date(2024, 12, 30), 'parsed_end': date(2025, 12, 28),
                'unit': 'iso4217:USD', 'value': value, 'source_id': 'S1', 'form': '10-K', 'fact_origin': 'sec_companyfacts'}

    def test_part_wins_when_the_inclusive_concept_is_smaller(self):
        from datetime import date
        from investmentideas import inputs
        facts = [self.fact('us-gaap:ResearchAndDevelopmentExpense', 109_000_000),
                 self.fact('us-gaap:ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost', 14_665_000_000)]
        concepts = dict(inputs.OPERATING_REFERENCE)['research_and_development']
        conflicts = []
        concept, chosen = inputs._larger_rnd(facts, concepts, date(2024, 12, 30), date(2025, 12, 28), 'USD', conflicts, 'R&D')
        self.assertEqual(concept, 'us-gaap:ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost')
        self.assertEqual(chosen['value'], 14_665_000_000)
        self.assertEqual(conflicts, [])

    def test_inclusive_wins_when_larger(self):
        from datetime import date
        from investmentideas import inputs
        facts = [self.fact('us-gaap:ResearchAndDevelopmentExpense', 15_000_000_000),
                 self.fact('us-gaap:ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost', 14_665_000_000)]
        concepts = dict(inputs.OPERATING_REFERENCE)['research_and_development']
        concept, chosen = inputs._larger_rnd(facts, concepts, date(2024, 12, 30), date(2025, 12, 28), 'USD', [], 'R&D')
        self.assertEqual(concept, 'us-gaap:ResearchAndDevelopmentExpense')

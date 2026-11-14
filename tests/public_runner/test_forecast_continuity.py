"""Year-1 forecasts start from the reported operating lines.

Live plans on 2026-11-14 set Apple's R&D at $68-96 billion against $45.8
billion reported, which put year-1 EBIT margin at 20-27% against 33%.
"""
import copy
from datetime import date
from decimal import Decimal
import unittest

from investmentideas import inputs, pipeline
from investmentideas.valuation import evaluate

from tests.public_runner.test_market_consistency import plan

START, END = date(2025, 9, 28), date(2026, 9, 26)


def fact(concept, value, source='S2', start=START, end=END, form='10-K'):
    return {'concept': concept, 'parsed_start': start, 'parsed_end': end, 'unit': 'iso4217:USD',
            'value': Decimal(value), 'source_id': source, 'fact_origin': 'sec_companyfacts', 'form': form,
            'start_date': start.isoformat(), 'end_date': end.isoformat(), 'instant': None, 'context_ref': None}


class OperatingReferenceTests(unittest.TestCase):
    def test_reported_lines_for_the_anchor_year_and_its_margins(self):
        facts = [fact('us-gaap:GrossProfit', 233602e6), fact('us-gaap:ResearchAndDevelopmentExpense', 45765e6),
                 fact('us-gaap:SellingGeneralAndAdministrativeExpense', 29774e6),
                 fact('us-gaap:OperatingIncomeLoss', 158063e6), fact('us-gaap:OperatingIncomeLoss', 158063e6, 'S4'),
                 fact('us-gaap:ResearchAndDevelopmentExpense', 34550e6, start=date(2024, 9, 29), end=date(2025, 9, 27)),
                 fact('us-gaap:ResearchAndDevelopmentExpense', 11730e6, start=date(2026, 6, 28), form='10-Q')]
        reference = inputs._operating_reference(facts, START, END, 'USD', Decimal(478834e6))
        self.assertEqual(reference['lines']['research_and_development']['value'], 45765000000)
        self.assertEqual(reference['lines']['operating_income']['source_refs'], ['S2', 'S4'])
        self.assertAlmostEqual(reference['operating_margin'], 158063 / 478834)
        self.assertAlmostEqual(reference['gross_margin'], 233602 / 478834)
        self.assertNotIn('operating_expenses', reference['lines'])

    def test_conflicting_lines_are_left_out_without_touching_input_coverage(self):
        facts = [fact('us-gaap:ResearchAndDevelopmentExpense', 45765e6),
                 fact('us-gaap:ResearchAndDevelopmentExpense', 46000e6, 'S4'), fact('us-gaap:OperatingIncomeLoss', -5e9)]
        reference = inputs._operating_reference(facts, START, END, 'USD', Decimal(100e9))
        self.assertNotIn('research_and_development', reference['lines'])
        self.assertTrue(reference['conflicts'])
        self.assertAlmostEqual(reference['operating_margin'], -.05)


def locked_plan(margin):
    value = plan()
    value['reported_inputs'] = {'operating_reference': {
        'period': {'start_date': '2025-01-01', 'end_date': '2025-12-31'}, 'currency': 'USD', 'operating_margin': margin,
        'lines': {'operating_income': {'value': 300e6, 'source_refs': ['S2']},
                  'research_and_development': {'value': 80e6, 'source_refs': ['S2']}}}}
    return value


class ContinuityCheckTests(unittest.TestCase):
    def test_the_diagnostic_compares_year_one_with_the_reported_margin(self):
        value = locked_plan(.30)
        result = pipeline._evaluate(value)
        rows = result['forecast_continuity']['scenarios']
        first = result['scenarios']['base']['cashflows'][0]['ebit_margin']
        self.assertAlmostEqual(rows['base']['change_percentage_points'], (first - .30) * 100)
        self.assertIn('Year-1 margin against the last reported year', pipeline._continuity_exhibit(result)[1])

    def test_a_break_without_a_cited_basis_goes_back_with_the_reported_lines(self):
        value = locked_plan(.60)
        result = pipeline._evaluate(value)
        with self.assertRaisesRegex(ValueError, r'(?s)breaks from the reported 60\.0% operating margin.*R&D 0\.1 billion'):
            pipeline._check_continuity(value, result)
        value['margin_continuity_basis'] = {name: 'too short [S1]' for name in ('bear', 'base', 'bull')}
        with self.assertRaises(ValueError):
            pipeline._check_continuity(value, result)
        value['margin_continuity_basis'] = {name: 'A disclosed one-time litigation charge reverses next year [S2].'
                                            for name in ('bear', 'base', 'bull')}
        pipeline._check_continuity(value, result)
        value['margin_continuity_basis'] = 'not an object'
        with self.assertRaisesRegex(ValueError, 'object keyed by scenario'):
            pipeline._check_continuity(value, result)

    def test_plans_without_reported_lines_are_not_checked(self):
        value = plan()
        result = pipeline._evaluate(value)
        self.assertNotIn('forecast_continuity', result)
        pipeline._check_continuity(value, result)
        self.assertEqual(result['weighted_per_share_value'], evaluate(copy.deepcopy(value))['weighted_per_share_value'])


if __name__ == '__main__':
    unittest.main()

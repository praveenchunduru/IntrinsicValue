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
        self.assertIn('Year-1 margin against the last reported results', pipeline._continuity_exhibit(result)[1])

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
        with self.assertRaisesRegex(ValueError, 'object keyed by bear, base or bull'):
            pipeline._check_continuity(value, result)

    def test_plans_without_reported_lines_are_not_checked(self):
        value = plan()
        result = pipeline._evaluate(value)
        self.assertNotIn('forecast_continuity', result)
        pipeline._check_continuity(value, result)
        self.assertEqual(result['weighted_per_share_value'], evaluate(copy.deepcopy(value))['weighted_per_share_value'])


if __name__ == '__main__':
    unittest.main()


SOURCES = [{"id": "S1", "source_tier": "regulatory_primary", "data": {}}]


class ReinvestmentContinuityTests(unittest.TestCase):
    def history(self, capex, depreciation, revenue=(200e9, 210e9, 245e9, 280e9, 330e9)):
        rows = [{'fiscal_year_end': f'202{index}-06-30', 'revenue': value, 'capital_expenditure': c, 'depreciation_amortization': d,
                 'acquisitions': None} for index, (value, c, d) in enumerate(zip(revenue, capex, depreciation), 2)]
        return [{'id': 'S2', 'source_tier': 'regulatory_primary', 'data': {}, 'historical_baseline': {'rows': rows}}]

    def test_the_reference_is_net_reinvestment_and_its_incremental_rate(self):
        sources = self.history(capex=(24e9, 28e9, 44e9, 65e9, 116e9), depreciation=(13e9, 11e9, 15e9, 22e9, 34e9))
        reference = pipeline.reinvestment_reference(sources)
        self.assertEqual(reference['net_reinvestment'], 82e9)
        self.assertAlmostEqual(reference['incremental_sales_to_capital'], (330e9 - 210e9) / (29e9 + 43e9 + 82e9))
        self.assertEqual(reference['years'], 3)

    def test_capital_light_or_shrinking_businesses_are_not_checked(self):
        light = self.history(capex=(9e9,) * 5, depreciation=(12e9,) * 5)
        self.assertIsNone(pipeline.reinvestment_reference(light))
        shrinking = self.history(capex=(30e9,) * 5, depreciation=(10e9,) * 5, revenue=(300e9, 290e9, 280e9, 270e9, 260e9))
        self.assertIsNone(pipeline.reinvestment_reference(shrinking))

    def locked(self, basis=None):
        value = plan()
        first = evaluate(value)['scenarios']['base']['cashflows'][0]
        reinvested = first['net_reinvestment']
        change = first['revenue'] - first['revenue'] / (1 + first['revenue_growth'])
        # Reported reinvestment twice the plan's year 1, at a third of its capital efficiency.
        value['reinvestment_reference'] = {'fiscal_year_end': '2026-06-30', 'net_reinvestment': 2 * reinvested,
            'capital_expenditure': 3 * reinvested, 'depreciation_amortization': reinvested, 'acquisitions': None,
            'incremental_sales_to_capital': change / reinvested / 3, 'years': 3, 'source_refs': ['S1'], 'basis': 'test'}
        if basis is not None:
            value['capital_efficiency_basis'] = basis
        return value

    def test_a_large_fall_in_reinvestment_needs_a_cited_basis(self):
        value = self.locked()
        with self.assertRaisesRegex(ValueError, 'Year-1 net reinvestment is below two thirds'):
            pipeline._check_continuity(value, pipeline._evaluate(value), SOURCES)

    def test_a_cited_basis_explains_the_fall(self):
        basis = {name: 'Management guided capital spending to fall as data-center construction completes [S1].'
                 for name in ('bear', 'base', 'bull')}
        value = self.locked(basis)
        result = pipeline._evaluate(value)
        pipeline._check_continuity(value, result, SOURCES)
        self.assertIn('stated basis', '\n'.join(pipeline._reinvestment_lines(result)))
        self.assertIn('Year-1 net reinvestment against the reported', pipeline.computed_summary(value, result))

    def test_a_basis_citing_an_unknown_source_is_rejected(self):
        value = self.locked({name: 'Management guided capital spending to fall as construction completes [S999].'
                             for name in ('bear', 'base', 'bull')})
        with self.assertRaisesRegex(ValueError, 'capital_efficiency_basis.bear cites S999'):
            pipeline._check_continuity(value, pipeline._evaluate(value), SOURCES)

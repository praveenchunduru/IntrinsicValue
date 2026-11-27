"""Year-1 forecasts start from the reported operating lines.

Live plans on 2026-11-14 set Apple's R&D at $68-96 billion against $45.8
billion reported, which put year-1 EBIT margin at 20-27% against 33%.
"""
import copy
from datetime import date
from decimal import Decimal
import unittest

from investmentideas import inputs, memo_style, pipeline
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
        self.assertIn('| Year-1 EBIT margin | 30.0% |', '\n'.join(pipeline._continuity_parts(result, memo_style.Style('USD'))[0]))

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
        with self.assertRaisesRegex(ValueError, 'Reinvestment over forecast years 1-3 departs from the reported run-rate'):
            pipeline._check_continuity(value, pipeline._evaluate(value), SOURCES)

    def test_a_cited_basis_explains_the_fall(self):
        basis = {name: 'Management guided capital spending to fall as data-center construction completes [S1].'
                 for name in ('bear', 'base', 'bull')}
        value = self.locked(basis)
        result = pipeline._evaluate(value)
        pipeline._check_continuity(value, result, SOURCES)
        text = '\n'.join(pipeline._continuity_parts(result, memo_style.Style('USD'))[0])
        self.assertIn('- Base reinvestment basis: Management guided capital spending', text)
        self.assertRegex(text, r'\| Net reinvestment, years 1-3 \(\$[BM]?\) \| [\d,.]+ a year \|')

    def test_a_basis_citing_an_unknown_source_is_rejected(self):
        value = self.locked({name: 'Management guided capital spending to fall as construction completes [S999].'
                             for name in ('bear', 'base', 'bull')})
        with self.assertRaisesRegex(ValueError, 'capital_efficiency_basis.bear cites S999'):
            pipeline._check_continuity(value, pipeline._evaluate(value), SOURCES)


    def reference(self, reinvested, rate):
        return {'fiscal_year_end': '2026-06-30', 'net_reinvestment': reinvested, 'capital_expenditure': 2 * reinvested,
                'depreciation_amortization': reinvested, 'acquisitions': None, 'incremental_sales_to_capital': rate,
                'years': 3, 'source_refs': ['S1'], 'basis': 'test'}

    def test_the_window_is_years_one_to_three(self):
        value = plan()
        # Growth, and so reinvestment, nearly stops in years 2-3.
        value['scenarios']['base']['growth_rates'] = [.06, 0, 0] + [.06] * 7
        flows = evaluate(value)['scenarios']['base']['cashflows']
        first = flows[0]['net_reinvestment']
        # Year 1 matches the reported run-rate at the reported capital efficiency; years 2-3 reinvest nothing.
        value['reinvestment_reference'] = self.reference(first, 2.0)
        row = pipeline._evaluate(value)['reinvestment_continuity']['scenarios']['base']
        self.assertEqual(row['years'], 3)
        self.assertAlmostEqual(row['net_reinvestment'], first)
        self.assertAlmostEqual(row['sales_to_capital'], 2.0)
        self.assertAlmostEqual(row['reinvestment_floor'], 2 * first)
        self.assertFalse(row['within_band'])

    def test_capital_efficiency_alone_breaks_the_band(self):
        value = plan()
        flows = evaluate(value)['scenarios']['base']['cashflows']
        total = sum(row['net_reinvestment'] for row in flows[:3])
        change = flows[2]['revenue'] - flows[0]['revenue'] / (1 + flows[0]['revenue_growth'])
        # Reinvestment at the reported run-rate, capital efficiency twice the reported rate.
        value['reinvestment_reference'] = self.reference(total / 3, change / total / 2)
        self.assertFalse(pipeline._evaluate(value)['reinvestment_continuity']['scenarios']['base']['within_band'])
        value['reinvestment_reference'] = self.reference(total / 3, change / total)
        self.assertTrue(pipeline._evaluate(value)['reinvestment_continuity']['scenarios']['base']['within_band'])


class CostPathTests(unittest.TestCase):
    def history(self, margins=(.30, .31, .32, .33, .40)):
        rows = [{'fiscal_year_end': f'202{i}-06-30', 'revenue': 200e9, 'operating_margin': m}
                for i, m in enumerate(margins, 2)]
        return [{'id': 'S2', 'source_tier': 'regulatory_primary', 'data': {}, 'historical_baseline': {'rows': rows}}]

    def test_the_reference_is_the_reported_peak_and_average_margin(self):
        reference = pipeline.cost_path_reference(self.history())
        self.assertAlmostEqual(reference['max_operating_margin'], .40)
        self.assertAlmostEqual(reference['average_operating_margin'], .332)
        self.assertEqual(reference['years'], 5)
        self.assertAlmostEqual(reference['final_year_margin_ceilings']['bear'], .332)
        self.assertAlmostEqual(reference['final_year_margin_ceilings']['bull'], .45)
        self.assertIsNone(pipeline.cost_path_reference(self.history(margins=(.3, .3))))

    def locked(self, peak, average):
        value = plan()
        value['cost_path_reference'] = {'fiscal_year_end': '2026-06-30', 'years': 5, 'max_operating_margin': peak,
                                        'average_operating_margin': average, 'source_refs': ['S1'], 'basis': 'test'}
        return value

    def test_ceilings_are_the_average_for_bear_and_the_peak_plus_5_points_otherwise(self):
        value = plan()
        finals = {name: row['cashflows'][row['explicit_forecast_years'] - 1]['ebit_margin'] if row.get('explicit_forecast_years')
                  else row['cashflows'][-1]['ebit_margin'] for name, row in evaluate(value)['scenarios'].items()}
        value = self.locked(peak=max(finals.values()) - .05, average=finals['bear'])
        check = pipeline._evaluate(value)['cost_path_continuity']
        self.assertTrue(all(row['within_band'] for row in check['scenarios'].values()))
        self.assertAlmostEqual(check['scenarios']['bear']['ceiling'], finals['bear'])
        self.assertAlmostEqual(check['scenarios']['base']['ceiling'], max(finals.values()))

    def test_a_margin_above_its_ceiling_is_rejected_and_no_citation_lifts_it(self):
        value = plan()
        result = evaluate(value)
        final = max(row['cashflows'][-1]['ebit_margin'] for row in result['scenarios'].values())
        value = self.locked(peak=final - .10, average=final - .12)
        value['cost_path_basis'] = {name: 'Disclosed price increases and a fixed-cost base lift margins over time [S1].'
                                    for name in ('bear', 'base', 'bull')}
        with self.assertRaisesRegex(ValueError, 'Final-year EBIT margin exceeds the house ceiling.*there is no exception'):
            pipeline._check_continuity(value, pipeline._evaluate(value), SOURCES)

    def test_a_low_margin_business_gets_a_narrower_allowance(self):
        ceiling, label = pipeline._margin_ceiling('base', {'max_operating_margin': .039, 'average_operating_margin': .036})
        # 40% of the peak, between the 1.5-point floor and the 5-point cap.
        self.assertAlmostEqual(ceiling, .039 * 1.4)
        self.assertIn('plus 1.6 points', label)

    def test_an_unprofitable_history_sets_no_ceiling(self):
        value = self.locked(peak=-.05, average=-.10)
        result = pipeline._evaluate(value)
        pipeline._check_continuity(value, result, SOURCES)
        self.assertIn('| Final-year margin ceiling |  | none |', '\n'.join(pipeline._continuity_parts(result, memo_style.Style('USD'))[0]))

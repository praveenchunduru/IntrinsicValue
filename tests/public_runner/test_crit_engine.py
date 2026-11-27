"""Engine fixes from the Damodaran, Mauboussin and Druckenmiller critiques (2026-11-26).

Continuing-value timing (Mauboussin F8), net R&D investment in the continuing value
amortizing the forecast's own R&D (Damodaran F3), the implied-return search below terminal growth (Damodaran F1,
Druckenmiller F5, Mauboussin F6), a sales-to-capital path (Damodaran F7,
Druckenmiller F6) and the forecast ROIC line (Mauboussin F3). Expected numbers are
computed here by hand, independently of the engine.
"""
import copy
import json
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from investmentideas import pipeline, roic, valuation
from investmentideas.valuation import evaluate

from tests.public_runner.test_market_consistency import RF, plan as glide_plan, wacc
from tests.public_runner.test_valuation import plan as constant_plan
from tests.public_runner.test_underwriting_integration import inputs as operating_plan


def steady(value):
    """The house steady state: the terminal return follows the terminal discount rate."""
    for scenario in value['scenarios'].values():
        scenario['terminal_roic_basis'] = 'terminal_rate'
    return value


def base_value(value):
    return evaluate(value)['scenarios']['base']['per_share_value']


def lock(value, *, operating_reference=None, sources=()):
    """Lock a plan with reported inputs fixed (as the plan-repair tests do)."""
    fixed = {'actuals': value['actuals'], 'currency': 'USD', 'source_refs': value['source_refs'],
             'input_dates': value.get('input_dates', {}),
             'coverage': {'complete': True, 'status': 'complete', 'conflicts': [], 'missing_fields': []}}
    if operating_reference is not None:
        fixed['operating_reference'] = operating_reference
    def pinned(proposal, _sources):
        result = copy.deepcopy(proposal)
        result['reported_inputs'] = fixed
        return result, []
    with patch.object(pipeline, 'canonical_reported_inputs', return_value=fixed), \
            patch.object(pipeline, 'pin_reported_inputs', side_effect=pinned):
        return pipeline._lock_plan(value, list(sources))


class ContinuingValueTimingTests(unittest.TestCase):
    def test_constant_rate_continuing_value_capitalizes_final_year_nopat(self):
        value = constant_plan()
        result = evaluate(value)['scenarios']['base']
        final = result['cashflows'][-1]
        # Revenue 1e9 * 1.05^5, margin 20%, tax 25%; R = 15%, g = 2%, r = 10%.
        nopat = 1e9 * 1.05 ** 5 * .2 * .75
        self.assertAlmostEqual(final['nopat'], nopat, places=3)
        self.assertAlmostEqual(result['terminal_nopat'], nopat, places=3)
        self.assertAlmostEqual(result['terminal_enterprise_value'], nopat * (1 - .02 / .15) / (.10 - .02), places=2)
        self.assertAlmostEqual(result['terminal_reinvestment'], nopat * .02 / .15, places=3)
        self.assertIn('supports that NOPAT', result['continuing_value_basis'])

    def test_growth_adds_no_value_under_the_house_steady_state(self):
        # Glide, ten explicit years, terminal return = terminal rate: the pipeline's house case.
        values = []
        for growth, basis in ((None, None), (.03, 'Synthetic slower market.'), (0.0, 'Synthetic flat market.')):
            value = steady(glide_plan())
            for scenario in value['scenarios'].values():
                if growth is not None:
                    scenario.update(terminal_growth=growth, terminal_growth_basis=basis)
            result = evaluate(value)
            self.assertEqual(result['scenarios']['base']['terminal_growth'], RF if growth is None else growth)
            values.append(result['scenarios']['base']['per_share_value'])
        self.assertAlmostEqual(values[0], values[1], places=9)
        self.assertAlmostEqual(values[0], values[2], places=9)
        # And under one constant rate, for any terminal growth below it.
        constant = []
        for growth in (0.0, .02, .05, .09):
            value = steady(constant_plan())
            for scenario in value['scenarios'].values():
                scenario['terminal_growth'] = growth
            constant.append(base_value(value))
        for item in constant[1:]:
            self.assertAlmostEqual(item, constant[0], places=9)

    def test_the_steady_state_has_no_singularity_at_terminal_growth(self):
        value = steady(constant_plan())
        actuals, definitions, _ = valuation._validated(valuation.materialize(value)[0])
        base = definitions['base']
        for rate in (.02, .015):  # equal to and below terminal growth
            result = valuation._project(actuals, base, flat=rate)
            self.assertAlmostEqual(result['terminal_enterprise_value'], result['terminal_nopat'] / rate)
        # An explicit terminal return still needs the rate above growth.
        with self.assertRaisesRegex(ValueError, 'above terminal growth'):
            valuation._project(actuals, dict(base, terminal_roic_follows_rate=False, terminal_roic=.15), flat=.02)

    def test_steady_state_years_pay_for_their_own_growth(self):
        value = glide_plan(years=6)
        result = evaluate(value)['scenarios']['base']
        rows = result['cashflows']
        for previous, row in zip(rows[5:], rows[6:]):
            self.assertEqual(row['phase'], 'steady_state')
            increase = (row['revenue'] - previous['revenue']) * .2 * .8
            self.assertAlmostEqual(row['net_reinvestment'], increase / .15)
        self.assertAlmostEqual(result['terminal_nopat'], rows[-1]['nopat'])


class RndAddBackTests(unittest.TestCase):
    SHARES = [.08 + .01 * year for year in range(10)]

    def test_the_steady_growth_fallback_matches_the_roic_vintages(self):
        # R&D growing at g with six-year straight-line amortization from the year after the spending.
        growth, start = .04, date(2000, 6, 30)
        rows = [{'fiscal_year_end': (start + timedelta(days=365 * year)).isoformat(),
                 'research_and_development': 100 * (1 + growth) ** year,
                 'selling_and_marketing': 0.0, 'general_and_administrative': 0.0} for year in range(12)]
        investment, amortization, _ = roic._capitalized(rows, 11)
        self.assertAlmostEqual((investment - amortization) / investment, valuation.rnd_net_investment_share(growth))
        self.assertEqual(valuation.RND_LIFE_YEARS, 6)
        self.assertAlmostEqual(valuation.rnd_net_investment_share(0.0), 0.0)

    def test_growth_adds_no_value_with_the_add_back_on_a_ten_year_glide_plan(self):
        values = []
        for growth, basis in ((None, None), (.03, 'Synthetic slower market.'), (0.0, 'Synthetic flat market.')):
            value = steady(glide_plan())
            for scenario in value['scenarios'].values():
                scenario['rnd_share_path'] = self.SHARES
                if growth is not None:
                    scenario.update(terminal_growth=growth, terminal_growth_basis=basis)
            result = evaluate(value)['scenarios']['base']
            addback = result['terminal_rnd_addback']
            self.assertEqual(addback['amortization_basis'], 'forecast')
            self.assertGreater(addback['net_investment_added_to_nopat'], 0)
            values.append(result['per_share_value'])
        self.assertAlmostEqual(values[0], values[1], places=9)
        self.assertAlmostEqual(values[0], values[2], places=9)
        self.assertGreater(values[0], base_value(steady(glide_plan())))

    def test_amortization_is_the_forecasts_own_rnd_of_the_six_prior_years(self):
        value = steady(glide_plan())
        for scenario in value['scenarios'].values():
            scenario['rnd_share_path'] = self.SHARES
        result = evaluate(value)['scenarios']['base']
        spend = [share * row['revenue'] for share, row in zip(self.SHARES, result['cashflows'])]
        addback = result['terminal_rnd_addback']
        self.assertAlmostEqual(addback['research_and_development'], spend[9], places=6)
        self.assertEqual(len(addback['amortized_spending']), 6)
        self.assertAlmostEqual(addback['amortization'], sum(spend[3:9]) / 6, places=6)
        self.assertAlmostEqual(result['terminal_nopat'], result['terminal_nopat_before_rnd_addback'] + spend[9] - sum(spend[3:9]) / 6, places=6)
        self.assertAlmostEqual(result['terminal_enterprise_value'], result['terminal_nopat'] / result['terminal_rate'])
        self.assertIn("amortization of the forecast's own R&D", result['continuing_value_basis'])

    def test_short_forecasts_use_reported_rnd_and_fall_back_only_without_it(self):
        value = steady(constant_plan())
        value['scenarios']['base']['rnd_share_path'] = .1
        spend = [.1 * 1e9 * 1.05 ** year for year in range(1, 6)]
        # Five forecast years need the anchor year and the year before from the reported history.
        value['reported_rnd_history'] = {'amounts': [80e6, 90e6, 95e6], 'source_refs': ['S2']}
        addback = evaluate(value)['scenarios']['base']['terminal_rnd_addback']
        self.assertEqual(addback['amortization_basis'], 'forecast_and_reported')
        self.assertAlmostEqual(addback['amortization'], (90e6 + 95e6 + sum(spend[:4])) / 6, places=3)
        self.assertAlmostEqual(addback['net_investment_added_to_nopat'], spend[4] - (90e6 + 95e6 + sum(spend[:4])) / 6, places=3)
        # Without enough history the amortization assumes R&D grew at terminal growth, and says so.
        value['reported_rnd_history'] = {'amounts': [95e6]}
        result = evaluate(value)['scenarios']['base']
        addback = result['terminal_rnd_addback']
        self.assertEqual(addback['amortization_basis'], 'terminal_growth_steady_state')
        self.assertAlmostEqual(addback['net_investment_added_to_nopat'], spend[4] * valuation.rnd_net_investment_share(.02), places=3)
        self.assertIn('assuming R&D grew at terminal growth', result['continuing_value_basis'])
        with self.assertRaisesRegex(ValueError, 'cannot be negative'):
            evaluate(dict(value, reported_rnd_history={'amounts': [-1.0]}))

    def test_the_panel_shows_rnd_expensed_as_the_earlier_rule(self):
        value = steady(glide_plan())
        rows = {row['name']: row for row in evaluate(value)['convention_panel']['rows']}
        self.assertEqual(rows['terminal_rnd_expensed']['status'], 'not_applicable')
        expensed = base_value(value)
        value['scenarios']['base']['rnd_share_path'] = self.SHARES
        result = evaluate(value)
        rows = {row['name']: row for row in result['convention_panel']['rows']}
        row = rows['terminal_rnd_expensed']
        self.assertEqual(row['status'], 'calculated')
        self.assertIn('earlier house rule', row['label'])
        self.assertAlmostEqual(row['per_share_value'], expensed)
        self.assertLess(row['dollar_delta'], 0)
        # Continuing the forecast's efficiency keeps R&D expensed, as the forecast does.
        reference = glide_plan()
        reference['scenarios']['base']['terminal_roic'] = .2 * .8 * 2
        self.assertAlmostEqual(rows['terminal_reinvestment_continues_forecast']['per_share_value'], base_value(reference))

    def test_the_lock_sets_the_plans_rnd_shares_and_revalue_reproduces_them(self):
        value = operating_plan()
        for scenario in value['scenarios'].values():
            scenario['terminal_growth'] = .02
        value['operating_model']['scenarios']['base']['expenses']['research_and_development'] = [100, 110, 120, 130, 150]
        history = [{'fiscal_year_end': f'202{year}-12-31', 'revenue': 900 + 50 * year, 'research_and_development': 80 + 5 * year}
                   for year in range(3)]
        source = {'id': 'S2', 'data': {}, 'historical_baseline': {'rows': history}}
        locked, changes = lock(value, sources=[source])
        self.assertEqual(locked['scenarios']['base']['rnd_share_path'], [.1, .11, .12, .13, .15])
        self.assertEqual(locked['scenarios']['bear']['rnd_share_path'], [.1] * 5)
        self.assertEqual(locked['scenarios']['base']['rnd_basis'], 'plan_rnd_line')
        # The anchor year (revenue 1,000) ends the reported history.
        self.assertEqual(locked['reported_rnd_history']['amounts'], [80, 85, 90])
        self.assertIn('scenarios.base.rnd_share_path', [change['field'] for change in changes])
        self.assertIn('adds net R&D investment', locked['assumptions_label'])
        result = evaluate(locked)
        self.assertEqual(evaluate(json.loads(json.dumps(locked)))['weighted_per_share_value'], result['weighted_per_share_value'])
        addback = result['scenarios']['base']['terminal_rnd_addback']
        self.assertEqual(addback['amortization_basis'], 'forecast_and_reported')
        self.assertAlmostEqual(addback['amortization'], (85 + 90 + 100 + 110 + 120 + 130) / 6)
        again = [change for change in lock(locked, sources=[source])[1] if change['field'].endswith('rnd_share_path')]
        self.assertEqual(again, [])
        # A history that does not end at the anchor year is not used.
        history[-1]['revenue'] = 999
        self.assertNotIn('reported_rnd_history', lock(value, sources=[source])[0])

    def test_the_shares_follow_an_edited_rnd_line_and_an_rnd_counterfactual(self):
        locked, _ = lock(operating_plan())
        edited = copy.deepcopy(locked)
        edited['operating_model']['scenarios']['base']['expenses']['research_and_development'][-1] = 150
        result = evaluate(edited)['scenarios']['base']
        self.assertAlmostEqual(result['terminal_rnd_addback']['research_and_development_share'], .15)
        locked['counterfactuals'] = {'higher_rnd': {'operating_overrides': {'expenses': {
            'research_and_development': [100, 100, 100, 100, 200]}}}}
        case = evaluate(locked)['counterfactuals']['higher_rnd']
        self.assertAlmostEqual(case['effective_inputs']['rnd_share_path'][-1], .2)
        self.assertIn('rnd_share_path', case['fixed_input_basis']['changed_base_inputs'])

    def test_aggregate_plans_hold_the_reported_share_and_no_rnd_means_none(self):
        value = operating_plan()
        value.pop('operating_model')
        for scenario in value['scenarios'].values():
            scenario.update(growth_rates=[0] * 5, ebit_margins=[.3] * 5)
        reference = {'operating_margin': .3, 'lines': {'research_and_development': {'value': 80, 'source_refs': ['S1']}}}
        locked, _ = lock(copy.deepcopy(value), operating_reference=reference)
        self.assertEqual(locked['scenarios']['base']['rnd_share_path'], [.08] * 5)
        self.assertEqual(locked['scenarios']['base']['rnd_basis'], 'reported_share')
        locked, changes = lock(copy.deepcopy(value), operating_reference={'operating_margin': .3, 'lines': {}})
        self.assertNotIn('rnd_share_path', locked['scenarios']['base'])
        self.assertNotIn('adds net R&D investment', locked['assumptions_label'])

    def test_supplied_plans_keep_their_own_conventions(self):
        value = operating_plan()
        with patch.object(pipeline, 'canonical_reported_inputs', return_value={
                'actuals': value['actuals'], 'currency': 'USD', 'source_refs': value['source_refs'],
                'input_dates': value['input_dates'], 'coverage': {'complete': True}}), \
                patch.object(pipeline, 'pin_reported_inputs',
                             side_effect=lambda proposal, _: (dict(copy.deepcopy(proposal), reported_inputs={}), [])):
            locked, _ = pipeline._lock_plan(value, [], apply_conventions=False)
        self.assertNotIn('rnd_share_path', locked['scenarios']['base'])


class ImpliedReturnTests(unittest.TestCase):
    def test_the_search_goes_below_terminal_growth_under_the_steady_state(self):
        value = steady(glide_plan())
        actuals, definitions, _ = valuation._validated(valuation.materialize(value)[0])
        base = definitions['base']
        ceiling_before = valuation._project(actuals, base, flat=RF + 1e-4)['per_share_value']
        value['market_price'] = round(1.25 * ceiling_before, 2)
        result = evaluate(value)
        row = result['implied_return_at_price']['scenarios']['base']
        self.assertEqual(row['search_floor'], valuation.IMPLIED_RATE_FLOOR)
        self.assertLess(row['implied_return'], RF)
        self.assertAlmostEqual(valuation._project(actuals, base, flat=row['implied_return'])['per_share_value'],
                               value['market_price'], places=6)
        self.assertFalse(row['fragile'])
        # The exhibit never moves the house value.
        self.assertAlmostEqual(result['scenarios']['base']['per_share_value'], base_value(steady(glide_plan())))

    def test_an_explicit_terminal_return_keeps_the_floor_at_terminal_growth(self):
        value = glide_plan()
        row = evaluate(value)['implied_return_at_price']['scenarios']['base']
        self.assertAlmostEqual(row['search_floor'], RF + 1e-4)

    def test_when_no_rate_solves_the_bound_is_reported_and_stated(self):
        value = steady(glide_plan())
        value['market_price'] = 1e6
        result = evaluate(value)
        headline = result['implied_return_at_price']['probability_weighted_value']
        self.assertIsNone(headline['implied_return'])
        bound = headline['price_bound']
        self.assertEqual(bound['price_position'], 'above_highest_value')
        self.assertAlmostEqual(bound['rate_at_highest_value'], valuation.IMPLIED_RATE_FLOOR)
        self.assertLess(bound['highest_value'], 1e6)
        summary = pipeline.computed_summary(value, result)
        self.assertIn('no flat discount rate between 0.50% and 50% reconciles the price', summary)
        self.assertIn(f'USD {bound["highest_value"]:,.2f}', summary)
        self.assertIn('equivalent to one flat rate of', summary)
        exhibit = '\n'.join(pipeline._market_consistency_exhibits(result, 'USD'))
        self.assertIn('- Base: no flat discount rate between 0.50% and 50% reconciles the price', exhibit)

    def test_a_solved_rate_keeps_the_summary_sentence(self):
        value = steady(glide_plan())
        result = evaluate(value)
        self.assertIn('What the price implies: one flat discount rate of', pipeline.computed_summary(value, result))


class SalesToCapitalPathTests(unittest.TestCase):
    def test_year_t_reinvestment_uses_year_t_ratio(self):
        value = constant_plan()
        value['scenarios']['base']['sales_to_capital'] = {'year1': 2, 'target': 4, 'target_year': 3}
        result = evaluate(value)
        rows = result['scenarios']['base']['cashflows']
        ratios = [2, 3, 4, 4, 4]
        revenue = 1e9
        for row, ratio in zip(rows, ratios):
            self.assertAlmostEqual(row['net_reinvestment'], revenue * .05 / ratio, places=3)
            revenue *= 1.05
        transition = result['computed_diagnostics']['terminal_transition']['base']
        self.assertEqual(transition['forecast_sales_to_capital'], 4)
        self.assertEqual(transition['forecast_sales_to_capital_path'], ratios)
        # One ratio per year values the same; a held path is the single ratio exactly.
        listed = constant_plan()
        listed['scenarios']['base']['sales_to_capital'] = ratios
        self.assertEqual(evaluate(listed)['scenarios'], result['scenarios'])
        held = constant_plan()
        held['scenarios']['base']['sales_to_capital'] = {'year1': 2, 'target': 2, 'target_year': 4}
        self.assertEqual(evaluate(held)['scenarios'], evaluate(constant_plan())['scenarios'])

    def test_bad_paths_and_conflicting_duplicates_are_rejected(self):
        for spec, message in (({'year1': 2, 'target': 0}, 'must be positive'),
                              ({'year1': 2, 'target_year': 9}, 'target_year must be 1 to 5'),
                              ({'year1': 2, 'slope': 1}, 'must be a number or'), ([2, 3], 'one ratio per explicit')):
            value = constant_plan()
            value['scenarios']['base']['sales_to_capital'] = spec
            with self.assertRaisesRegex(ValueError, message):
                evaluate(value)
        value = constant_plan()
        value['scenarios']['base'].update(sales_to_capital={'year1': 2, 'target': 4}, sales_to_capital_path=[2] * 5)
        with self.assertRaisesRegex(ValueError, 'sales_to_capital_path conflicts'):
            evaluate(value)

    def test_the_lock_writes_the_path_and_rebuilds_a_stale_one(self):
        value = operating_plan()
        value['scenarios']['base']['sales_to_capital'] = {'year1': 2, 'target': 3, 'target_year': 5}
        locked, changes = lock(value)
        self.assertEqual(locked['scenarios']['base']['sales_to_capital_path'], [2, 2.25, 2.5, 2.75, 3])
        self.assertIn('scenarios.base.sales_to_capital_path', [change['field'] for change in changes])
        self.assertNotIn('sales_to_capital_path', locked['scenarios']['bear'])
        locked['scenarios']['base']['sales_to_capital']['target'] = 4
        relocked, _ = lock(locked)
        self.assertEqual(relocked['scenarios']['base']['sales_to_capital_path'][-1], 4)
        evaluate(relocked)

    def test_counterfactuals_and_price_requirements_handle_the_path(self):
        value = constant_plan()
        value['market_price'] = 20.0
        value['scenarios']['base']['sales_to_capital'] = {'year1': 2, 'target': 4, 'target_year': 3}
        # A path in another scenario must not break the counterfactual's revalidation.
        value['scenarios']['bear']['sales_to_capital'] = {'year1': 2, 'target': 1.5}
        value['counterfactuals'] = {'lower_tax': {'tax_rate': .2}, 'flat_capital': {'sales_to_capital': 3}}
        result = evaluate(value)
        self.assertEqual(result['counterfactuals']['lower_tax']['effective_inputs']['tax_rate'], .2)
        cases = result['counterfactuals']
        self.assertEqual(cases['lower_tax']['effective_inputs']['sales_to_capital_path'], [2, 3, 4, 4, 4])
        flat = constant_plan()
        flat['scenarios']['base']['sales_to_capital'] = 3
        self.assertAlmostEqual(cases['flat_capital']['per_share_value'], base_value(flat))
        row = next(row for row in result['price_requirements']['rows'] if row['input'] == 'sales_to_capital')
        self.assertEqual(row['house_sales_to_capital_path'], [2, 3, 4, 4, 4])
        self.assertIn('one ratio in every explicit year', row['label'])
        summary = pipeline.computed_summary(value, result)
        self.assertIn('2.00 in year 1 to 4.00 in year 5', summary)

    def test_the_three_year_window_still_catches_a_jump_after_year_one(self):
        def checked(spec):
            value = constant_plan()
            value['scenarios']['base']['sales_to_capital'] = spec
            first = evaluate(constant_plan())['scenarios']['base']['cashflows'][0]
            # Reported: year 1's reinvestment at the plan's own year-1 capital efficiency.
            value['reinvestment_reference'] = {
                'fiscal_year_end': '2026-06-30', 'net_reinvestment': first['net_reinvestment'],
                'capital_expenditure': 2 * first['net_reinvestment'], 'depreciation_amortization': first['net_reinvestment'],
                'acquisitions': None, 'incremental_sales_to_capital': 2.0, 'years': 3, 'source_refs': ['S1'], 'basis': 'test'}
            return pipeline._evaluate(value)['reinvestment_continuity']['scenarios']['base']
        self.assertTrue(checked(2)['within_band'])
        jump = checked({'year1': 2, 'target': 6, 'target_year': 2})
        self.assertFalse(jump['within_band'])
        revenue = [1e9 * 1.05 ** year for year in range(4)]
        expected = (revenue[1] - revenue[0]) / 2 + (revenue[2] - revenue[1]) / 6 + (revenue[3] - revenue[2]) / 6
        self.assertAlmostEqual(jump['net_reinvestment'], expected, places=3)

    def test_the_schema_offers_the_path_and_the_terminal_sentence_is_gone(self):
        self.assertIn('{"year1":x,"target":y,"target_year":n}', pipeline.FORECAST_SCHEMA)
        self.assertNotIn('capital-intensity transition', pipeline.FORECAST_SCHEMA)
        self.assertNotIn('terminal cash discontinuity', pipeline.UNDERWRITING_GUIDANCE)


class ForecastReturnOnCapitalTests(unittest.TestCase):
    REFERENCE = {'invested_capital': 1e9, 'fiscal_year_end': '2025-12-31', 'source_refs': ['S2']}

    def test_capital_rolls_forward_by_net_reinvestment(self):
        value = steady(constant_plan())
        value['invested_capital_reference'] = self.REFERENCE
        result = evaluate(value)
        returns = result['forecast_return_on_capital']
        base = returns['scenarios']['base']
        flows = result['scenarios']['base']['cashflows']
        capital = [1e9]
        for row in flows:
            capital.append(capital[-1] + row['net_reinvestment'])
        for index, row in enumerate(base['years']):
            self.assertAlmostEqual(row['invested_capital'], capital[index + 1], places=3)
            self.assertAlmostEqual(row['return_on_invested_capital'], flows[index]['nopat'] / ((capital[index] + capital[index + 1]) / 2))
        self.assertTrue(all(row['incremental_return_3y'] is None for row in base['years'][:3]))
        self.assertAlmostEqual(base['years'][3]['incremental_return_3y'],
                               (flows[3]['nopat'] - flows[0]['nopat']) / (capital[3] - capital[0]))
        self.assertAlmostEqual(base['return_on_new_capital_after_forecast'], .10)
        self.assertIn('Display only', returns['basis'])
        # Display only: the value is the same without it.
        self.assertEqual(result['scenarios'], evaluate(steady(constant_plan()))['scenarios'])

    def test_the_reference_is_the_last_reported_invested_capital(self):
        rows = [{'fiscal_year_end': '2024-12-31', 'invested_capital': 9e8}, {'fiscal_year_end': '2025-12-31', 'invested_capital': 1e9}]
        source = [{'id': 'S2', 'data': {}, 'historical_baseline': {'rows': rows}}]
        reference = pipeline.forecast_capital_reference(source)
        self.assertEqual((reference['invested_capital'], reference['fiscal_year_end'], reference['source_refs']),
                         (1e9, '2025-12-31', ['S2']))
        rows[-1]['invested_capital'] = None
        self.assertIsNone(pipeline.forecast_capital_reference(source))
        # Changed 2026-11-27: the history's last year must be the revenue anchor year.
        value = operating_plan()
        anchored = [dict(rows[0], revenue=value['actuals']['revenue'])]
        locked, _ = lock(value, sources=[dict(source[0], historical_baseline={'rows': anchored})])
        self.assertEqual(locked['invested_capital_reference']['invested_capital'], 9e8)
        locked, _ = lock(value, sources=[dict(source[0], historical_baseline={'rows': [dict(anchored[0], revenue=1.0)]})])
        self.assertNotIn('invested_capital_reference', locked)

    def test_the_memo_prints_the_line_under_the_forecast(self):
        value = steady(constant_plan())
        value['invested_capital_reference'] = self.REFERENCE
        result = evaluate(value)
        text = '\n'.join(pipeline._forecast_roic_lines(result, 'USD'))
        self.assertIn('### Forecast return on invested capital', text)
        self.assertIn('| Measure | Year 1 | Year 2 | Year 3 | Year 4 | Year 5 | After the forecast |', text)
        base = result['forecast_return_on_capital']['scenarios']['base']
        self.assertIn(f'| Base ROIC | {base["years"][0]["return_on_invested_capital"]:.1%} |', text)
        self.assertIn(f'| Base 3-year incremental ROIC | n/a | n/a | n/a | {base["years"][3]["incremental_return_3y"]:.1%}', text)
        self.assertIn('| 10.0% |', text)
        self.assertIn('including goodwill and acquired intangibles', text)
        self.assertIn("EBIT after the plan's tax rate", text)
        self.assertIn('### Forecast return on invested capital', pipeline.valuation_section(value, result))
        self.assertEqual(pipeline._forecast_roic_lines(evaluate(constant_plan()), 'USD'), [])


if __name__ == '__main__':
    unittest.main()

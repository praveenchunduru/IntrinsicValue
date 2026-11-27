"""Exhibits beside the convention panel: competitive advantage periods, rate regimes, the index benchmark.

Each row is checked against the engine valuing an equivalent plan, not against a
re-typed continuing-value formula, so the checks hold when that formula changes.
No row may change a house value or the largest-convention-step ranking.
"""
import copy
from datetime import date
import json
import unittest
from unittest.mock import Mock

from investmentideas import cap_fade, macro, pipeline, prompt_context, quality, valuation
from investmentideas.valuation import evaluate

from tests.public_runner.test_macro import aa_csv, beta_html, workbook
from tests.public_runner.test_market_consistency import ERP, KD, RF, plan
from tests.public_runner.test_market_pins import ERP as PIN_ERP, TABLE, pin, proposal

GROWTH = [.10, .09, .08, .07, .06, .06, .05, .05, .04, .04]
MARGINS = [.18, .185, .19, .195, .2, .2, .205, .21, .21, .21]

# Damodaran's January 2026 US industries as the pipeline parses them (an archived
# Betas.html): some names carry the table's line breaks ("Insur ance").
JANUARY_2026_INDUSTRIES = [
    'Advertising', 'Aerospace/Defense', 'Air Transport', 'Apparel', 'Auto & Truck', 'Auto Parts', 'Bank (Money Center)',
    'Banks (Regional)', 'Beverage (Alcoholic)', 'Beverage (Soft)', 'Broadcasting', 'Brokerage & Investment Banking',
    'Building Materials', 'Business & Consumer Services', 'Cable TV', 'Chemical (Basic)', 'Chemical (Diversified)',
    'Chemical (Specialty)', 'Coal & Related Energy', 'Computer Services', 'Computers/Peripherals', 'Construction Supplies',
    'Diversified', 'Drugs (Biotechnology)', 'Drugs (Pharmaceutical)', 'Education', 'Electrical Equipment',
    'Electronics (Consumer & Office)', 'Electronics (General)', 'Engineering/Construction', 'Entertainment',
    'Environmental & Waste Services', 'Farming/Agriculture', 'Financial Svcs. (Non-bank & Insur ance)', 'Food Processing',
    'Food Wholesalers', 'Furn/Home Furnishings', 'Green & Renewable Energy', 'Healthcare Products',
    'Healthcare Support Services', 'Heathcare Information and Techno logy', 'Homebuilding', 'Hospitals/Healthcare Facilities',
    'Hotel/Gaming', 'Household Products', 'Information Services', 'Insurance (General)', 'Insurance (Life)',
    'Insurance (Prop/Cas.)', 'Investments & Asset Management', 'Machinery', 'Metals & Mining', 'Office Equipment & Services',
    'Oil/Gas (Integrated)', 'Oil/Gas (Production and Exploratio n)', 'Oil/Gas Distribution', 'Oilfield Svcs/Equip.',
    'Packaging & Container', 'Paper/Forest Products', 'Power', 'Precious Metals', 'Publishing & Newspapers', 'R.E.I.T.',
    'Real Estate (Development)', 'Real Estate (General/Diversified)', 'Real Estate (Operations & Service s)', 'Recreation',
    'Reinsurance', 'Restaurant/Dining', 'Retail (Automotive)', 'Retail (Building Supply)', 'Retail (Distributors)',
    'Retail (General)', 'Retail (Grocery and Food)', 'Retail (REITs)', 'Retail (Special Lines)', 'Rubber& Tires',
    'Semiconductor', 'Semiconductor Equip', 'Shipbuilding & Marine', 'Shoe', 'Software (Entertainment)',
    'Software (Internet)', 'Software (System & Application)', 'Steel', 'Telecom (Wireless)', 'Telecom. Equipment',
    'Telecom. Services', 'Tobacco', 'Transportation', 'Transportation (Railroads)', 'Trucking', 'Utility (General)',
    'Utility (Water)']


def house_plan(**options):
    """The synthetic plan with a fading forecast and the house terminal return (the terminal rate)."""
    value = plan(**options)
    for scenario in value['scenarios'].values():
        scenario.update(growth_rates=list(GROWTH), ebit_margins=list(MARGINS), terminal_roic_basis='terminal_rate')
    return value


def bottom_up(value, *sectors):
    value['discount_rate']['unlevered_beta'] = {
        'calculation': 'bottom_up_sector_weighted', 'observed_at': '2026-01', 'date_precision': 'month',
        'source_refs': ['S2'], 'label': 'Business mix',
        'sectors': [{'sector': name, 'weight': weight, 'cash_corrected_unlevered_beta': 1.2, 'firms': 30}
                    for name, weight in sectors]}
    return value


def rows(result):
    return {row['name']: row for row in result['convention_panel']['rows']}


def base_value(value):
    return evaluate(value)['scenarios']['base']['per_share_value']


def internals(value):
    prepared, _, _ = valuation.materialize(copy.deepcopy(value))
    actuals, definitions, _ = valuation._validated(prepared)
    return actuals, definitions['base']


def shifted(value, rf, premium=ERP, day=None):
    """The same plan dated at another Treasury rate and premium; the debt cost moves with the Treasury rate."""
    value = copy.deepcopy(value)
    rate = value['discount_rate']
    rate['pretax_cost_of_debt']['value'] = KD + rf - rate['risk_free_rate']['value']
    rate['risk_free_rate']['value'], rate['equity_risk_premium']['value'] = rf, premium
    if day is not None:
        rate['risk_free_rate']['observed_at'] = rate['equity_risk_premium']['observed_at'] = day
    return value


class FadeTableTests(unittest.TestCase):
    def test_exhibit_25_rates_and_every_mapping_target(self):
        self.assertEqual(len(cap_fade.FADE_RATES), 22)
        self.assertTrue(all(.10 <= rate <= .30 for rate in cap_fade.FADE_RATES.values()))
        # Exhibit 25 reports an average fade rate of 0.20 across the industry groups.
        self.assertAlmostEqual(sum(cap_fade.FADE_RATES.values()) / 22, .20, delta=.005)
        for name, group in cap_fade._INDUSTRIES.items():
            with self.subTest(industry=name):
                self.assertTrue(group is None or group in cap_fade.FADE_RATES or group == cap_fade._SPANS)

    def test_every_industry_in_the_january_2026_table_has_a_decided_mapping(self):
        mapped = 0
        for name in JANUARY_2026_INDUSTRIES:
            group, reason = cap_fade.industry_group(name)
            with self.subTest(industry=name):
                self.assertNotEqual(reason, 'not in the industry mapping')
            mapped += group is not None
        self.assertEqual(mapped, 77)
        self.assertEqual(cap_fade.industry_group('Financial Svcs. (Non-bank & Insur ance)'),
                         (None, 'financials and real estate are not in Exhibit 25'))
        self.assertEqual(cap_fade.industry_group('Rubber & Tires')[0], 'Automobiles & Components')

    def test_a_mix_weights_the_equivalent_years_of_its_industries(self):
        fade = cap_fade.industry_fade([{'sector': 'Computers/Peripherals', 'weight': .7425},
                                       {'sector': 'Software (System & Application)', 'weight': .2575}])
        self.assertEqual(fade['status'], 'mapped')
        self.assertAlmostEqual(fade['equivalent_years'], .7425 / .17 + .2575 / .22, places=12)
        self.assertEqual(fade['extension_years'], 6)
        self.assertEqual([item['industry_group'] for item in fade['sectors']],
                         ['Technology Hardware & Equipment', 'Software & Services'])
        # Page 33: a fade rate of 0.20 is five more years of excess returns.
        self.assertEqual(cap_fade.industry_fade([{'sector': 'Chemical (Basic)', 'weight': 1}])['extension_years'], 5)

    def test_unmapped_mixes_say_why(self):
        for sectors, text in (([{'sector': 'Banks (Regional)', 'weight': 1}], 'financials'),
                              ([{'sector': 'Retail (General)', 'weight': .6}, {'sector': 'Shoe', 'weight': .4}], 'spans'),
                              ([{'sector': 'Quantum Widgets', 'weight': 1}], 'not in the industry mapping'),
                              (None, 'no bottom-up industry mapping')):
            with self.subTest(sectors=sectors):
                fade = cap_fade.industry_fade(sectors)
                self.assertEqual(fade['status'], 'unmapped')
                self.assertIn(text, fade['reason'])


class RatePathTests(unittest.TestCase):
    def test_house_path_keeps_years_1_to_10_and_the_terminal_rate_after(self):
        _, base = internals(house_plan())
        house, terminal = valuation._rate_path(base)
        longer = dict(base, growth_rates=[.05] * 15, ebit_margins=[.2] * 15)
        rates, _ = valuation._rate_path(dict(longer, house_rate_path=True))
        self.assertEqual(rates[:10], house)
        self.assertEqual(rates[10:], [terminal] * 5)
        default, _ = valuation._rate_path(longer)
        self.assertEqual(default[:10], [house[0]] * 10)

    def test_plans_still_state_at_most_15_explicit_years(self):
        value = plan(years=16)
        with self.assertRaisesRegex(ValueError, '1–15 years'):
            evaluate(value)


class HorizonRowTests(unittest.TestCase):
    def test_five_years_is_the_engine_extending_a_five_year_plan(self):
        result = evaluate(house_plan())
        short = house_plan()
        for scenario in short['scenarios'].values():
            scenario['growth_rates'], scenario['ebit_margins'] = GROWTH[:5], MARGINS[:5]
        row = rows(result)['competitive_advantage_period_5_years']
        self.assertEqual((row['kind'], row['status'], row['horizon_years']), ('horizon', 'calculated', 5))
        self.assertAlmostEqual(row['per_share_value'], base_value(short), places=9)
        self.assertLess(row['per_share_value'], result['scenarios']['base']['per_share_value'])

    def test_longer_horizons_add_years_and_keep_the_first_ten(self):
        value = house_plan()
        result = evaluate(value)
        actuals, base = internals(value)
        house = valuation._project(actuals, base)
        for years in (15, 20):
            extended = valuation._extended_forecast(base, years)
            extra = years - 10
            expected = [GROWTH[-1] + (RF - GROWTH[-1]) * step / extra for step in range(1, extra + 1)]
            self.assertEqual(extended['growth_rates'][:10], GROWTH)
            for got, want in zip(extended['growth_rates'][10:], expected):
                self.assertAlmostEqual(got, want, places=15)
            self.assertAlmostEqual(extended['growth_rates'][-1], RF, places=15)
            self.assertEqual(extended['ebit_margins'][10:], [MARGINS[-1]] * extra)
            projection = valuation._project(actuals, extended)
            for mine, theirs in zip(projection['cashflows'][:10], house['cashflows']):
                for key in ('revenue', 'fcff', 'discount_factor', 'discount_rate'):
                    self.assertAlmostEqual(mine[key], theirs[key], places=9)
            self.assertTrue(all(row['phase'] == 'explicit' for row in projection['cashflows']))
            self.assertTrue(all(abs(row['discount_rate'] - base['terminal_rate']) < 1e-15 for row in projection['cashflows'][10:]))
            row = rows(result)[f'competitive_advantage_period_{years}_years']
            self.assertAlmostEqual(row['per_share_value'], projection['per_share_value'], places=9)
            self.assertGreater(row['per_share_value'], result['scenarios']['base']['per_share_value'])

    def test_constant_rate_rows_equal_the_engine_on_an_equivalent_plan(self):
        value = plan(glide=False, timing=False)
        for scenario in value['scenarios'].values():
            scenario.update(growth_rates=list(GROWTH), ebit_margins=list(MARGINS), terminal_growth=.02)
        result = evaluate(value)
        longer = copy.deepcopy(value)
        for scenario in longer['scenarios'].values():
            scenario['growth_rates'] = GROWTH + [GROWTH[-1] + (.02 - GROWTH[-1]) * step / 5 for step in range(1, 6)]
            scenario['ebit_margins'] = MARGINS + [MARGINS[-1]] * 5
        self.assertAlmostEqual(rows(result)['competitive_advantage_period_15_years']['per_share_value'],
                               base_value(longer), places=9)
        self.assertIn("plan's 15% terminal return", rows(result)['competitive_advantage_period_5_years']['label'])
        actuals, base = internals(value)
        short = dict(base, growth_rates=GROWTH[:5], ebit_margins=MARGINS[:5], steady_state_through=10)
        projection = valuation._project(actuals, short)
        self.assertEqual(len(projection['cashflows']), 10)
        self.assertEqual([row['revenue_growth'] for row in projection['cashflows'][5:]], [.02] * 5)
        self.assertAlmostEqual(rows(result)['competitive_advantage_period_5_years']['per_share_value'],
                               projection['per_share_value'], places=9)

    def test_industry_fade_row_is_the_horizon_its_rate_converts_to(self):
        for industry, rate, horizon in (('Beverage (Soft)', .10, 20), ('Software (System & Application)', .22, 15)):
            with self.subTest(industry=industry):
                result = evaluate(bottom_up(house_plan(), (industry, 1.0)))
                fade = rows(result)['competitive_advantage_period_industry_fade']
                self.assertEqual(fade['horizon_years'], horizon)
                self.assertIn(f'{rate:.2f}', fade['label'])
                self.assertIn(cap_fade.industry_group(industry)[0], fade['label'])
                self.assertAlmostEqual(fade['per_share_value'],
                                       rows(result)[f'competitive_advantage_period_{horizon}_years']['per_share_value'], places=9)

    def test_an_unmapped_company_gets_a_note_and_no_fade_row(self):
        result = evaluate(bottom_up(house_plan(), ('Retail (General)', .5), ('Shoe', .5)))
        self.assertNotIn('competitive_advantage_period_industry_fade', rows(result))
        self.assertTrue(any('Retail (General)' in note for note in result['convention_panel']['exhibit_notes']))

    def test_exhibits_leave_the_house_value_and_the_ranking_alone(self):
        value = house_plan()
        result = evaluate(value)
        actuals, base = internals(value)
        prepared, _, _ = valuation.materialize(copy.deepcopy(value))
        _, definitions, _ = valuation._validated(prepared)
        alone = valuation._convention_panel(actuals, definitions, result['scenarios'], prepared['_discounting'])
        panel = result['convention_panel']
        self.assertEqual(panel['largest_meaningful_step'], alone['largest_meaningful_step'])
        self.assertEqual(panel['rows'][:len(alone['rows'])], alone['rows'])
        self.assertEqual({row['kind'] for row in panel['rows'][len(alone['rows']):]}, {'horizon', 'rate_regime'})
        self.assertEqual(result['scenarios']['base']['per_share_value'], valuation._project(actuals, base)['per_share_value'])


class TreasuryShiftTests(unittest.TestCase):
    def test_each_shift_equals_the_plan_revalued_at_that_treasury_rate(self):
        value = house_plan()
        result = evaluate(value)
        for delta, name in ((-.02, 'minus_200'), (-.01, 'minus_100'), (.01, 'plus_100'), (.02, 'plus_200')):
            with self.subTest(delta=delta):
                row = rows(result)[f'treasury_rate_{name}_bp']
                self.assertEqual((row['kind'], row['status']), ('rate_regime', 'calculated'))
                self.assertAlmostEqual(row['per_share_value'], base_value(shifted(value, RF + delta)), places=9)
                self.assertAlmostEqual(row['terminal_growth'], RF + delta, places=15)
                self.assertEqual(row['terminal_growth_rule'], 'equals the risk-free rate')
                self.assertIsNotNone(row['per_share_value_at_house_terminal_growth'])

    def test_without_debt_the_rate_effect_is_the_whole_path_point_row(self):
        value = house_plan()
        value['discount_rate']['financial_debt']['value'] = 0.0
        found = rows(evaluate(value))
        for sign in ('plus', 'minus'):
            self.assertAlmostEqual(found[f'treasury_rate_{sign}_100_bp']['per_share_value_at_house_terminal_growth'],
                                   found[f'required_return_{sign}_1_point']['per_share_value'], places=9)

    def test_stated_lower_growth_is_capped_at_a_lower_risk_free_rate(self):
        value = house_plan()
        for scenario in value['scenarios'].values():
            scenario.update(terminal_growth=.035, terminal_growth_basis='Installed base shrinking as the category is replaced.')
        row = rows(evaluate(value))['treasury_rate_minus_200_bp']
        self.assertEqual(row['terminal_growth_rule'], 'capped at the risk-free rate')
        expected = shifted(value, RF - .02)
        for scenario in expected['scenarios'].values():
            scenario['terminal_growth'] = RF - .02
        self.assertAlmostEqual(row['per_share_value'], base_value(expected), places=9)
        self.assertEqual(rows(evaluate(value))['treasury_rate_plus_100_bp']['terminal_growth_rule'], 'as stated')

    def test_constant_rate_plans_hold_their_terminal_growth(self):
        value = plan(glide=False, timing=False)
        for scenario in value['scenarios'].values():
            scenario['terminal_growth'] = .02
        row = rows(evaluate(value))['treasury_rate_plus_200_bp']
        self.assertEqual((row['terminal_growth'], row['terminal_growth_rule']), (.02, 'held: constant-rate plan'))
        self.assertAlmostEqual(row['per_share_value'], base_value(shifted(value, RF + .02)), places=9)

    def test_plain_wacc_plans_move_the_whole_path_and_hurdles_do_not_apply(self):
        value = plan(glide=False, timing=False)
        value.pop('discount_rate')
        for scenario in value['scenarios'].values():
            scenario.update(terminal_growth=.02, wacc=.09)
        found = rows(evaluate(value))
        self.assertEqual(found['treasury_rate_plus_100_bp']['per_share_value'], found['required_return_plus_1_point']['per_share_value'])
        self.assertIn('no dated rate inputs', found['treasury_rate_plus_100_bp']['label'])
        hurdle = plan(glide=False, timing=False)
        hurdle['discount_rate'].update(basis='house_hurdle', house_hurdle={'value': .1, 'observed_at': '2026-03-31',
                                                                           'source_refs': ['S1'], 'label': 'Policy hurdle'})
        for scenario in hurdle['scenarios'].values():
            scenario['terminal_growth'] = .02
        self.assertEqual(rows(evaluate(hurdle))['treasury_rate_plus_100_bp']['status'], 'not_applicable')


def history_plan(months=None):
    value = house_plan()
    for key in ('risk_free_rate', 'equity_risk_premium'):
        value['discount_rate'][key]['observed_at'] = '2026-03-01'
    value['rate_history'] = {'source_refs': ['S1'], 'months': months or [
        ['2021-08-01', .0123, .0431], ['2023-10-01', .0459, .0503], ['2026-03-01', RF, ERP]]}
    return value


class RateHistoryTests(unittest.TestCase):
    def test_lowest_and_highest_months_equal_the_plan_revalued_at_their_pairs(self):
        value = history_plan()
        result = evaluate(value)
        found = rows(result)
        values = {day: base_value(shifted(value, rf, premium, day)) for day, rf, premium in value['rate_history']['months']}
        self.assertAlmostEqual(values['2026-03-01'], result['scenarios']['base']['per_share_value'], places=9)
        low, high = min(values, key=values.get), max(values, key=values.get)
        self.assertEqual(found['rate_history_lowest']['observed_at'], low)
        self.assertEqual(found['rate_history_highest']['observed_at'], high)
        self.assertAlmostEqual(found['rate_history_lowest']['per_share_value'], values[low], places=9)
        self.assertAlmostEqual(found['rate_history_highest']['per_share_value'], values[high], places=9)
        self.assertIn(high[:7], found['rate_history_highest']['label'])
        summary = result['convention_panel']['rate_history']
        self.assertEqual((summary['months'], summary['calculated'], summary['first'], summary['last']),
                         (3, 3, '2021-08-01', '2026-03-01'))
        self.assertAlmostEqual(summary['median_per_share_value'], sorted(values.values())[1], places=9)
        self.assertEqual(result['convention_panel']['largest_meaningful_step'],
                         evaluate(house_plan())['convention_panel']['largest_meaningful_step'])

    def test_a_month_without_a_valid_rate_path_is_listed_not_invented(self):
        value = history_plan([['2025-01-01', .10, 0.0], ['2026-03-01', RF, ERP]])
        summary = evaluate(value)['convention_panel']['rate_history']
        self.assertEqual(summary['calculated'], 1)
        self.assertEqual(summary['not_calculated'][0]['observed_at'], '2025-01-01')

    def test_a_history_that_does_not_end_at_the_plans_pair_is_not_used(self):
        value = history_plan([['2025-01-01', .04, .05], ['2026-02-01', RF, ERP]])
        result = evaluate(value)
        self.assertNotIn('rate_history_lowest', rows(result))
        self.assertTrue(any('ends at 2026-02-01' in note for note in result['convention_panel']['exhibit_notes']))

    def test_malformed_histories_are_errors(self):
        for months, text in (([['2026-03-15', RF, ERP]], 'start-of-month'),
                             ([['2026-03-01', RF, ERP], ['2025-03-01', RF, ERP]], 'oldest first'),
                             ([['2026-03-01', .5, ERP]], 'ranges'),
                             ([['2026-03-01', RF]], 'Each rate_history month'),
                             ([], '1-240')):
            with self.subTest(months=months), self.assertRaisesRegex(ValueError, text):
                value = history_plan()
                value['rate_history']['months'] = months
                evaluate(value)


def monthly_rows(start=date(2014, 1, 1), end=date(2025, 1, 1), missing=()):
    rows, day = [], start
    while day <= end:
        rows.append((day, .02 + day.month / 1000, None if day in missing else .05 + day.year / 1e5))
        day = date(day.year + (day.month == 12), day.month % 12 + 1, 1)
    return rows


class MacroHistoryTests(unittest.TestCase):
    def test_parser_keeps_ten_years_of_same_row_pairs_ending_at_the_observation(self):
        gap = date(2020, 6, 1)
        result = macro.parse_damodaran_erp(workbook(rows=monthly_rows(missing={gap})), as_of='2025-01-03')
        history = result['monthly_history']
        self.assertEqual((history[0][0], history[-1][0]), ('2015-02-01', '2025-01-01'))
        self.assertEqual(len(history), 119)
        self.assertNotIn(gap.isoformat(), [day for day, _, _ in history])
        self.assertEqual(history[-1][1:], [result['risk_free_rate'], result['equity_risk_premium']])
        self.assertAlmostEqual(history[0][1], .022, places=12)
        earlier = macro.parse_damodaran_erp(workbook(rows=monthly_rows()), as_of='2024-06-15')['monthly_history']
        self.assertEqual((earlier[0][0], earlier[-1][0], len(earlier)), ('2014-07-01', '2024-06-01', 120))

    def test_gathered_history_sits_beside_the_data_given_to_models(self):
        payloads = {macro.ERP_URL: workbook(rows=monthly_rows()), macro.BETAS_URL: beta_html(), macro.AA_URL: aa_csv()}
        sources, gaps = macro.gather_us_calibration(Mock(side_effect=lambda url: payloads[url]), as_of='2025-01-03')
        self.assertEqual(gaps, [])
        erp = next(source for source in sources if source['data']['dataset'] == 'damodaran_us_implied_erp')
        self.assertEqual(len(erp['monthly_history']), 120)
        self.assertNotIn('monthly_history', erp['data'])
        self.assertIn('sustainable-payout ERP', erp['monthly_history_basis'])
        self.assertNotIn('monthly_history', json.dumps(pipeline._evidence_context(
            [dict(source, id=f'S{index}', raw_document=None) for index, source in enumerate(sources, 1)])))


HISTORY = [['2026-02-01', .044, .046], ['2026-03-01', .045, .045]]


class PipelineTests(unittest.TestCase):
    def test_python_pins_the_history_only_from_the_premium_source(self):
        source = dict(PIN_ERP, monthly_history=copy.deepcopy(HISTORY), monthly_history_basis='Monthly pairs.')
        value, _, _ = pin(proposal(), sources=(source, TABLE))
        self.assertEqual(value['rate_history']['months'], HISTORY)
        self.assertEqual(value['rate_history']['source_refs'], ['S8'])
        result = pipeline.evaluate(value)
        self.assertEqual(result['convention_panel']['rate_history']['calculated'], 2)
        typed = proposal()
        typed['rate_history'] = {'source_refs': ['S8'], 'months': [['2026-03-01', .01, .09]]}
        value, _, _ = pin(typed, sources=(PIN_ERP, TABLE))
        self.assertNotIn('rate_history', value)

    def test_a_rate_history_must_cite_a_known_source(self):
        value = history_plan()
        sources = [{'id': 'S1'}, {'id': 'S2'}]
        pipeline._validate_plan_sources(value, sources)
        value['rate_history']['source_refs'] = ['S99']
        with self.assertRaisesRegex(ValueError, 'Unknown source reference for rate_history'):
            pipeline._validate_plan_sources(value, sources)

    def test_memo_prints_the_rows_notes_and_the_index_beside_the_company(self):
        value = history_plan()
        result = evaluate(value)
        summary = pipeline.computed_summary(value, result)
        ratio = result['index_benchmark']['value_to_price']
        self.assertIn(f'| S&P 500 under the same conventions | index benchmark | n/a | {ratio:.2f} |', summary)
        self.assertIn('Index benchmark: a stylized S&P 500 at the 2026-03-01 Treasury rate of 4.50%', summary)
        self.assertLess(summary.index('| Weighted |'), summary.index('| S&P 500'))
        panel = '\n'.join(pipeline._market_consistency_exhibits(result, 'USD'))
        for text in ('(horizon) |', '(rate regime) |', 'Horizon rows change only', 'median base value is USD',
                     'Largest convention step: '):
            self.assertIn(text, panel)
        self.assertIn('- No industry-fade row', panel)
        self.assertEqual(pipeline.computed_summary(value, result).count('Largest convention steps'), 1)

    def test_models_and_figure_checks_do_not_see_the_monthly_pairs(self):
        value = history_plan([['2021-08-01', .0123, .0431], ['2026-03-01', RF, ERP]])
        result = evaluate(value)
        shown = json.loads(prompt_context.valuation_context(value, result))
        self.assertNotIn('months', shown['plan']['rate_history'])
        self.assertEqual(shown['plan']['rate_history']['months_in_saved_plan'], 2)
        self.assertIn('rate_history', value)
        _, _, pool = quality.trace_pools([], ({'rate_history': value['rate_history']},))
        self.assertNotIn(.0123, pool)
        _, _, pool = quality.trace_pools([], ({'other': value['rate_history']},))
        self.assertIn(.0123, pool)


class IndexBenchmarkTests(unittest.TestCase):
    def test_benchmark_is_the_engine_valuing_a_steady_state_index_plan(self):
        result = evaluate(house_plan())
        benchmark = result['index_benchmark']
        self.assertEqual(benchmark['status'], 'calculated')
        rate, growth, ret = RF + ERP, RF, valuation.MARKET_RETURN_ON_NEW_CAPITAL
        index = {'currency': 'USD', 'amount_unit': 'currency_units', 'share_unit': 'shares', 'assumptions_label': 'Index.',
                 'actuals': {'revenue': 1000.0, 'net_debt': 0.0, 'other_equity_claims': 0.0, 'shares_outstanding': 1.0},
                 'source_refs': {key: ['S1'] for key in ('revenue', 'net_debt', 'other_equity_claims', 'shares_outstanding')},
                 'scenarios': {name: {'probability': weight, 'growth_rates': [growth] * 10, 'ebit_margins': [.3] * 10,
                                      'tax_rate': 0.0, 'sales_to_capital': ret / ((1 + growth) * .3), 'wacc': rate,
                                      'terminal_growth': growth, 'terminal_roic': rate, 'terminal_roic_basis': 'terminal_rate'}
                               for name, weight in (('bear', 0.0), ('base', 1.0), ('bull', 0.0))}}
        price = 1000 * (1 + growth) * .3 * (1 - growth / ret) / (rate - growth)
        # One timing for value and price (engine review, 2026-11-26): each index year invests
        # growth / return of its NOPAT for the following year's growth, so the continuing value
        # starts from year-11 NOPAT. Hand value, independent of the engine:
        nopat = [1000 * .3 * (1 + growth) ** year for year in range(1, 12)]
        house = (sum(n * (1 - growth / ret) / (1 + rate) ** year for year, n in enumerate(nopat[:10], 1)) +
                 nopat[10] / rate / (1 + rate) ** 10)
        self.assertAlmostEqual(benchmark['value_to_price'], house / price, places=9)
        result_engine = valuation._project({'revenue': 1000.0, 'net_debt': 0.0, 'other_equity_claims': 0.0,
                                            'shares_outstanding': 1.0},
                                           dict(index['scenarios']['base'], convention='constant',
                                                terminal_roic_follows_rate=True))
        self.assertAlmostEqual(result_engine['pv_forecast_fcff'] + nopat[9] * (1 + growth) / rate *
                               result_engine['cashflows'][-1]['discount_factor'], house, places=6)
        self.assertLess(benchmark['value_to_price'], 1)
        self.assertAlmostEqual(benchmark['value_to_price_with_no_excess_return_years'],
                               ERP / (rate * (1 - growth / ret)), places=12)
        self.assertEqual(benchmark['company_base_value_to_price'], result['scenarios']['base']['value_to_price'])
        self.assertEqual((benchmark['observed_at'], benchmark['excess_return_years']), ('2026-03-31', 10))

    def test_benchmark_needs_a_dated_us_dollar_rate_pair(self):
        value = plan(glide=False, timing=False)
        value.pop('discount_rate')
        for scenario in value['scenarios'].values():
            scenario.update(terminal_growth=.02, wacc=.09)
        self.assertEqual(evaluate(value)['index_benchmark']['status'], 'not_applicable')
        self.assertNotIn('S&P 500', pipeline.computed_summary(value, evaluate(value)))


if __name__ == '__main__':
    unittest.main()

"""Market-consistent valuation conventions (v2): every expected number is computed here by hand.

The implied equity risk premium is solved from the index price assuming the market
grows at the risk-free rate forever. A valuation that uses it with lower terminal
growth or one constant firm rate values the index itself away from its price
(live AAPL run, 2026-11-14: the S&P 500 at about 80% of its price).
"""
import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest

from investmentideas.valuation import evaluate, save_valuation

RF, ERP, TAX, KD = .045, .045, .25, .06
EQUITY, DEBT = 1000.0, 100.0


def record(value, label, observed='2026-03-31', **extra):
    return {'value': value, 'observed_at': observed, 'source_refs': ['S1'], 'label': label, **extra}


def plan(years=10, *, glide=True, timing=True, growth=.06):
    scenario = lambda probability, margin: {'probability': probability, 'growth_rates': [growth] * years,
                                            'ebit_margins': [margin] * years, 'tax_rate': .2,
                                            'sales_to_capital': 2.0, 'terminal_roic': .15}
    value = {'currency': 'USD', 'amount_unit': 'currency_units', 'share_unit': 'shares',
             'valuation_date': '2026-03-31', 'assumptions_label': 'Synthetic house forecasts.',
             'actuals': {'revenue': 1000.0, 'net_debt': 50.0, 'other_equity_claims': 0.0, 'shares_outstanding': 100.0},
             'source_refs': {key: ['S1'] for key in ('revenue', 'net_debt', 'other_equity_claims', 'shares_outstanding', 'market_price')},
             'market_price': 20.0,
             'net_debt_bridge': {'as_of': '2025-12-31', 'debt': 100.0, 'selected_cash_and_nonoperating_investments': 50.0, 'net_debt': 50.0},
             'scenarios': {'bear': scenario(.25, .18), 'base': scenario(.5, .2), 'bull': scenario(.25, .22)},
             'discount_rate': {'as_of': '2026-03-31', 'currency': 'USD', 'amount_unit': 'currency_units',
                               'lease_convention': 'gaap_operating',
                               'risk_free_rate': record(RF, 'Treasury'), 'equity_risk_premium': record(ERP, 'Implied ERP'),
                               'unlevered_beta': record(1.2, 'Sector beta'), 'market_equity': record(EQUITY, 'Market equity'),
                               'financial_debt': record(DEBT, 'Debt'), 'finance_lease_debt': record(0.0, 'Leases'),
                               'pretax_cost_of_debt': record(KD, 'Yield proxy'), 'marginal_tax_rate': record(TAX, 'Statutory')}}
    if glide:
        value['discount_rate']['term_structure'] = 'glide_to_market_v1'
        value['discount_rate']['terminal_unlevered_beta'] = {'value': .9, 'observed_at': '2026-01', 'date_precision': 'month',
                                                             'source_refs': ['S2'], 'label': 'Market operating-asset beta'}
    if timing:
        value['valuation_timing'] = {'convention': 'stub_v1', 'anchor_date': '2025-12-31'}
    return value


def wacc(unlevered, equity=EQUITY, debt=DEBT):
    levered = unlevered * (1 + (1 - TAX) * debt / equity)
    return equity / (equity + debt) * (RF + levered * ERP) + debt / (equity + debt) * KD * (1 - TAX)


def hand_value(inputs, name='base', *, stub=90 / 365, horizon=10, flat=None, terminal_roic=None, growth_default=RF, terminal_rate=None):
    """Independent re-implementation of the v2 conventions."""
    scenario = inputs['scenarios'][name]
    initial, terminal = wacc(1.2), terminal_rate or wacc(.9)
    if flat is not None:
        initial = terminal = flat
    explicit = len(scenario['growth_rates'])
    hold = max(5, horizon - 5)
    rates = [initial if year <= hold else initial + (terminal - initial) * (year - hold) / (horizon - hold)
             for year in range(1, horizon + 1)]
    g = scenario.get('terminal_growth', growth_default)
    roic = terminal_roic or scenario['terminal_roic']
    revenue, cumulative, value = inputs['actuals']['revenue'], 1.0, 0.0
    after_tax = scenario['ebit_margins'][-1] * (1 - scenario['tax_rate'])
    for year in range(1, horizon + 1):
        previous = revenue
        if year <= explicit:
            revenue *= 1 + scenario['growth_rates'][year - 1]
            nopat = revenue * scenario['ebit_margins'][year - 1] * (1 - scenario['tax_rate'])
            fcff = nopat - (revenue - previous) / scenario['sales_to_capital']
        else:
            # A steady-state year invests for the following year's growth at the return on new
            # capital (none in the last year); the first also pays its own growth, a year late,
            # with that year's return (2026-11-26).
            revenue *= 1 + g
            nopat = revenue * after_tax
            fcff = nopat - (revenue * g * after_tax / roic if year < horizon else 0.0)
            if year == explicit + 1:
                fcff -= (revenue - previous) * after_tax / roic * (1 + rates[year - 1])
        cumulative *= 1 + rates[year - 1]
        value += fcff * (1 - stub if year == 1 else 1) * (1 + rates[0]) ** stub / cumulative
    # The capital in place at the end of the rate path supports that year's NOPAT (2026-11-26).
    terminal_nopat = revenue * scenario['ebit_margins'][-1] * (1 - scenario['tax_rate'])
    value += terminal_nopat * (1 - g / roic) / (terminal - g) * (1 + rates[0]) ** stub / cumulative
    return (value - inputs['actuals']['net_debt']) / inputs['actuals']['shares_outstanding']


class RatePathTests(unittest.TestCase):
    def test_glide_holds_five_years_then_moves_to_a_leverage_neutral_terminal_rate(self):
        result = evaluate(plan())
        path = result['discounting']['base_rate_path']
        self.assertEqual(len(path), 10)
        self.assertTrue(all(abs(rate - wacc(1.2)) < 1e-12 for rate in path[:5]))
        self.assertAlmostEqual(path[5], wacc(1.2) + (wacc(.9) - wacc(1.2)) / 5, places=12)
        self.assertAlmostEqual(path[-1], wacc(.9), places=12)
        self.assertAlmostEqual(result['discounting']['terminal_rate'], wacc(.9), places=12)
        self.assertAlmostEqual(result['scenarios']['base']['per_share_value'], hand_value(plan()), places=9)
        self.assertEqual(result['model'], 'unlevered-fcff-incremental-sales-to-capital-v2')

    def test_terminal_rate_rises_with_leverage_instead_of_collapsing_toward_growth(self):
        # Holding the levered beta at one would make terminal rates fall with debt; at 60%
        # debt they would sit within about two points of risk-free growth.
        spreads = []
        for debt in (0.0, 400.0, 1500.0):
            inputs = plan()
            inputs['discount_rate']['financial_debt']['value'] = debt
            result = evaluate(inputs)
            self.assertAlmostEqual(result['discounting']['terminal_rate'], wacc(.9, debt=debt), places=12)
            spreads.append(result['scenarios']['base']['terminal_spread'])
        self.assertTrue(all(spread > .02 for spread in spreads))
        self.assertAlmostEqual(spreads[0], .9 * ERP, places=12)

    def test_short_forecasts_extend_with_steady_state_years_and_long_ones_hold_longer(self):
        short = evaluate(plan(years=6))
        rows = short['scenarios']['base']['cashflows']
        self.assertEqual([row['phase'] for row in rows], ['explicit'] * 6 + ['steady_state'] * 4)
        self.assertAlmostEqual(short['scenarios']['base']['per_share_value'], hand_value(plan(years=6)), places=9)
        long = evaluate(plan(years=12))
        path = long['discounting']['base_rate_path']
        self.assertEqual(len(path), 12)
        self.assertTrue(all(abs(rate - wacc(1.2)) < 1e-12 for rate in path[:7]))
        self.assertAlmostEqual(long['scenarios']['base']['per_share_value'], hand_value(plan(years=12), horizon=12), places=9)

    def test_stub_counts_only_the_unexpired_first_year_and_discounts_from_the_valuation_date(self):
        result = evaluate(plan())
        first = result['scenarios']['base']['cashflows'][0]
        self.assertAlmostEqual(first['counted_fraction'], 1 - 90 / 365, places=12)
        self.assertAlmostEqual(first['discount_time'], 1 - 90 / 365, places=12)
        no_stub = evaluate(plan(timing=False))
        self.assertAlmostEqual(no_stub['scenarios']['base']['per_share_value'], hand_value(plan(timing=False), stub=0), places=9)
        stale = plan()
        stale['valuation_timing']['anchor_date'] = '2024-12-31'
        with self.assertRaisesRegex(ValueError, 'within the year before the valuation date'):
            evaluate(stale)

    def test_constant_rate_plans_are_unchanged(self):
        legacy = plan(glide=False, timing=False)
        for scenario in legacy['scenarios'].values():
            scenario['terminal_growth'] = .02
        result = evaluate(legacy)
        self.assertEqual(result['model'], 'unlevered-fcff-incremental-sales-to-capital-v1')
        self.assertNotIn('discounting', result)
        self.assertNotIn('phase', result['scenarios']['base']['cashflows'][0])
        self.assertIn('discount_rate', result['reverse_dcf'])

    def test_published_examples_recompute_with_only_the_continuing_value_retimed(self):
        # The shipped constant-rate examples keep every explicit cash flow. Their continuing
        # value now capitalizes year-T NOPAT, which the capital in place supports, instead of
        # year-T NOPAT grown once more (2026-11-26), so it falls by exactly 1 / (1 + g).
        root = Path(__file__).resolve().parents[2] / 'examples'
        for name in ('AAPL-2026-11-06', 'AAPL-2026-11-06-revised'):
            saved = json.loads((root / name / 'valuation.json').read_text())
            assumptions = json.loads((root / name / 'assumptions.json').read_text())
            result = evaluate(assumptions)
            with self.subTest(example=name):
                for scenario in ('bear', 'base', 'bull'):
                    now, then = result['scenarios'][scenario], saved['scenarios'][scenario]
                    self.assertEqual(now['cashflows'], then['cashflows'])
                    self.assertAlmostEqual(now['pv_forecast_fcff'] / then['pv_forecast_fcff'], 1, places=12)
                    growth = assumptions['scenarios'][scenario]['terminal_growth']
                    self.assertAlmostEqual(now['pv_terminal_value'] / then['pv_terminal_value'], 1 / (1 + growth), places=12)
                self.assertLess(result['weighted_per_share_value'], saved['weighted_per_share_value'])


class TerminalGrowthTests(unittest.TestCase):
    def test_terminal_growth_defaults_to_the_risk_free_rate_and_cannot_exceed_it(self):
        result = evaluate(plan())
        self.assertEqual(result['scenarios']['base']['terminal_growth'], RF)
        self.assertEqual(result['scenarios']['base']['terminal_growth_source'], 'risk_free_rate')
        above = plan()
        above['scenarios']['bull']['terminal_growth'] = RF + .001
        with self.assertRaisesRegex(ValueError, 'exceeds the risk-free rate'):
            evaluate(above)

    def test_lower_terminal_growth_needs_a_company_specific_basis(self):
        lower = plan()
        lower['scenarios']['bear']['terminal_growth'] = .02
        with self.assertRaisesRegex(ValueError, 'terminal_growth_basis'):
            evaluate(lower)
        lower['scenarios']['bear']['terminal_growth_basis'] = 'Installed base shrinking as the product category is replaced.'
        result = evaluate(lower)
        self.assertEqual(result['scenarios']['bear']['terminal_growth'], .02)
        self.assertIn('Installed base', result['scenarios']['bear']['terminal_growth_basis'])

    def test_declining_or_low_return_firms_get_no_silent_default(self):
        declining = plan(growth=-.03)
        with self.assertRaisesRegex(ValueError, 'state terminal_growth'):
            evaluate(declining)
        low_return = plan()
        for scenario in low_return['scenarios'].values():
            scenario['terminal_roic'] = .04
        with self.assertRaisesRegex(ValueError, 'state terminal_growth'):
            evaluate(low_return)

    def test_scalar_wacc_cannot_override_the_rate_path(self):
        duplicate = plan()
        duplicate['scenarios']['base']['wacc'] = .07
        with self.assertRaisesRegex(ValueError, 'wacc must be omitted'):
            evaluate(duplicate)


class ImpliedReturnAndPanelTests(unittest.TestCase):
    def test_implied_return_is_the_flat_rate_at_the_price_beside_its_like_for_like_comparator(self):
        inputs = plan()
        result = evaluate(inputs)
        row = result['implied_return_at_price']['scenarios']['base']
        self.assertAlmostEqual(hand_value(inputs, flat=row['implied_return']), 20.0, places=6)
        self.assertAlmostEqual(hand_value(inputs, flat=row['required_return_flat_equivalent']),
                               result['scenarios']['base']['per_share_value'], places=6)
        self.assertAlmostEqual(row['gap_percentage_points'], (row['required_return_flat_equivalent'] - row['implied_return']) * 100, places=9)
        weighted = result['implied_return_at_price']['probability_weighted_value']
        self.assertIn('not the expected return across scenarios', weighted['label'])

    def test_panel_rows_are_fixed_and_sensitivities_are_never_the_largest_step(self):
        result = evaluate(plan())
        panel = result['convention_panel']
        names = [row['name'] for row in panel['rows']]
        for expected in ('constant_initial_rate', 'damodaran_default_terminal_rate', 'terminal_return_equals_terminal_rate',
                         'terminal_reinvestment_continues_forecast', 'required_return_minus_1_point'):
            self.assertIn(expected, names)
        self.assertNotIn(panel['largest_meaningful_step'], ('required_return_minus_1_point', 'required_return_plus_1_point'))
        damodaran = next(row for row in panel['rows'] if row['name'] == 'damodaran_default_terminal_rate')
        self.assertAlmostEqual(damodaran['per_share_value'], hand_value(plan(), terminal_rate=RF + ERP), places=9)
        no_excess = next(row for row in panel['rows'] if row['name'] == 'terminal_return_equals_terminal_rate')
        self.assertAlmostEqual(no_excess['per_share_value'], hand_value(plan(), terminal_roic=wacc(.9)), places=9)

    def test_near_zero_spreads_are_marked_not_meaningful(self):
        cheap = plan()
        cheap['discount_rate']['unlevered_beta']['value'] = .05   # initial rate barely above risk-free growth
        result = evaluate(cheap)
        row = next(row for row in result['convention_panel']['rows'] if row['name'] == 'constant_initial_rate')
        self.assertEqual(row['status'], 'not_applicable')

    def test_counterfactual_wacc_is_one_flat_rate_under_the_glide(self):
        inputs = plan()
        inputs['counterfactuals'] = {'flat_eight': {'wacc': .08}}
        result = evaluate(inputs)
        case = result['counterfactuals']['flat_eight']
        self.assertAlmostEqual(case['per_share_value'], hand_value(inputs, flat=.08), places=9)
        self.assertEqual(case['fixed_input_basis']['changed_base_inputs']['wacc'], .08)

    def test_value_to_price_replaces_upside_language(self):
        result = evaluate(plan())
        self.assertNotIn('weighted_upside_percent', result)
        self.assertAlmostEqual(result['weighted_value_to_price'], result['weighted_per_share_value'] / 20.0, places=12)
        self.assertNotIn('upside_percent', result['scenarios']['base'])


class BetaTests(unittest.TestCase):
    def test_sector_weighted_bottom_up_beta_uses_pinned_table_values(self):
        inputs = plan()
        inputs['discount_rate']['unlevered_beta'] = {
            'calculation': 'bottom_up_sector_weighted', 'observed_at': '2026-01', 'date_precision': 'month',
            'source_refs': ['S2'], 'label': 'Hardware and software mix',
            'sectors': [{'sector': 'Hardware', 'weight': .75, 'cash_corrected_unlevered_beta': 1.3},
                        {'sector': 'Software', 'weight': .25, 'cash_corrected_unlevered_beta': 1.1}]}
        result = evaluate(inputs)
        beta = result['discount_rate_calculation']['provenance']['unlevered_beta']
        self.assertAlmostEqual(beta['value'], .75 * 1.3 + .25 * 1.1, places=12)
        self.assertAlmostEqual(result['discounting']['base_rate_path'][0], wacc(.75 * 1.3 + .25 * 1.1), places=12)
        inputs['discount_rate']['unlevered_beta']['sectors'][0]['weight'] = .7
        with self.assertRaisesRegex(ValueError, 'sum to one'):
            evaluate(inputs)


class OutputTests(unittest.TestCase):
    def test_csv_files_carry_the_rate_path_columns(self):
        inputs = plan(years=8)
        result = evaluate(inputs)
        with tempfile.TemporaryDirectory() as folder:
            paths = save_valuation(folder, inputs, result)
            with open(paths['cashflows.csv']) as stream:
                rows = list(csv.DictReader(stream))
            self.assertIn('discount_rate', rows[0])
            self.assertEqual(sum(row['phase'] == 'steady_state' for row in rows), 6)
            with open(paths['sensitivity.csv']) as stream:
                header = next(csv.reader(stream))
            self.assertEqual(header[:3], ['rate_shift', 'initial_rate', 'terminal_rate'])


if __name__ == '__main__':
    unittest.main()

"""The pipeline pins market-consistent conventions and every beta number from the run's sources."""
import copy
import unittest

from investmentideas import pipeline

from tests.public_runner.test_market_consistency import plan

ERP = {'id': 'S8', 'kind': 'macro_calibration', 'data': {'dataset': 'damodaran_us_implied_erp', 'risk_free_rate': .045,
                                                         'equity_risk_premium': .045, 'observed_at': '2026-03-01'}}
TABLE = {'id': 'S9', 'kind': 'macro_calibration', 'data': {
    'dataset': 'damodaran_us_sector_betas', 'observed_at': '2026-01', 'sectors': [
        {'sector': 'Hardware', 'firms': 30, 'cash_corrected_unlevered_beta': 1.3, 'is_aggregate': False},
        {'sector': 'Software', 'firms': 300, 'cash_corrected_unlevered_beta': 1.1, 'is_aggregate': False},
        {'sector': 'Total Market (without financial s)', 'firms': 4800, 'cash_corrected_unlevered_beta': .9, 'is_aggregate': True}]}}
REGRESSION = {'id': 'S11', 'kind': 'historical_equity_beta', 'data': {'beta': 1.1, 'last_return_week_end': '2026-03-27',
                                                                       'benchmark_symbol': 'SPY'}}
REPORTED = {'input_dates': {'revenue': {'end_date': '2025-12-31'}, 'net_debt': {'instant': '2025-12-31'}}}


def proposal(beta=None):
    value = plan(glide=False, timing=False)
    if beta is not None:
        value['discount_rate']['unlevered_beta'] = beta
    return value


def pin(value, sources=(ERP, TABLE, REGRESSION), reported=REPORTED, day='2026-03-31'):
    changes = []
    notes = pipeline._pin_market_conventions(value, list(sources), reported, day, changes)
    return value, changes, notes


BOTTOM_UP = {'calculation': 'bottom_up_sector_weighted', 'source_refs': ['S9'], 'label': 'Mix',
             'sectors': [{'sector': 'Hardware', 'weight': .75}, {'sector': 'Software', 'weight': .25}]}


class PinTests(unittest.TestCase):
    def test_usd_plans_get_the_glide_market_beta_and_timing_from_sources(self):
        value, changes, notes = pin(proposal(copy.deepcopy(BOTTOM_UP)))
        rate = value['discount_rate']
        self.assertEqual(rate['term_structure'], 'glide_to_market_v1')
        self.assertEqual(rate['terminal_unlevered_beta']['value'], .9)
        self.assertEqual(rate['terminal_unlevered_beta']['source_refs'], ['S9'])
        self.assertEqual(value['valuation_timing'], {'convention': 'stub_v1', 'anchor_date': '2025-12-31'})
        self.assertEqual([item['cash_corrected_unlevered_beta'] for item in rate['unlevered_beta']['sectors']], [1.3, 1.1])
        self.assertEqual(rate['unlevered_beta']['observed_at'], '2026-01')
        self.assertEqual([case['name'] for case in value['beta_cross_checks']], ['regression_beta', 'blume_adjusted_regression_beta'])
        self.assertAlmostEqual(value['beta_cross_checks'][1]['unlevered_beta']['historical_equity_beta'], 2 / 3 * 1.1 + 1 / 3)
        self.assertEqual(notes, [])
        result = pipeline.evaluate(value)
        self.assertEqual(result['model'], 'unlevered-fcff-incremental-sales-to-capital-v2')
        self.assertEqual([row['status'] for row in result['beta_cross_checks']], ['calculated', 'calculated'])

    def test_pins_are_idempotent_and_record_only_real_changes(self):
        value, first, _ = pin(proposal(copy.deepcopy(BOTTOM_UP)))
        _, second, _ = pin(value)
        self.assertTrue(first)
        self.assertEqual(second, [])

    def test_model_typed_beta_numbers_are_replaced_by_source_values(self):
        beta = {'calculation': 'historical_issuer_cash_corrected', 'historical_equity_beta': 1.5, 'observed_at': '2026-03-27',
                'source_refs': ['S11'], 'label': 'Regression'}
        value, changes, _ = pin(proposal(beta))
        self.assertEqual(value['discount_rate']['unlevered_beta']['historical_equity_beta'], 1.1)
        self.assertEqual([case['name'] for case in value['beta_cross_checks']], ['blume_adjusted_regression_beta'])
        typed = copy.deepcopy(BOTTOM_UP)
        typed['sectors'][0]['cash_corrected_unlevered_beta'] = 0.5
        value, _, _ = pin(proposal(typed))
        self.assertEqual(value['discount_rate']['unlevered_beta']['sectors'][0]['cash_corrected_unlevered_beta'], 1.3)

    def test_unknown_or_aggregate_sectors_go_back_for_repair(self):
        for name in ('Consumer Hardware', 'Total Market (without financial s)'):
            beta = copy.deepcopy(BOTTOM_UP)
            beta['sectors'][0]['sector'] = name
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'exact names'):
                pin(proposal(beta))

    def test_an_effective_rate_entered_as_the_us_marginal_rate_goes_back_for_repair(self):
        value = proposal()
        value['discount_rate']['marginal_tax_rate']['value'] = .17
        with self.assertRaisesRegex(ValueError, '21% US federal statutory rate'):
            pin(value)

    def test_a_missing_rate_block_is_required_when_the_market_inputs_exist(self):
        value = proposal()
        value.pop('discount_rate')
        with self.assertRaisesRegex(ValueError, 'dated discount_rate is required'):
            pin(value)
        _, _, notes = pin(value, sources=(TABLE,))
        self.assertIn('not applied', notes[0])

    def test_non_usd_plans_keep_one_constant_rate_and_say_so(self):
        value = proposal()
        value['currency'] = 'EUR'
        value['discount_rate']['currency'] = 'EUR'
        with self.assertRaisesRegex(ValueError, '(?s)need terminal_growth.*one constant discount rate'):
            pin(copy.deepcopy(value))
        for scenario in value['scenarios'].values():
            scenario['terminal_growth'] = .025
        value, _, notes = pin(value)
        self.assertNotIn('term_structure', value['discount_rate'])
        self.assertTrue(any('not applied' in note for note in notes))

    def test_rate_instructions_match_the_convention_python_applies(self):
        self.assertIn('Omit scenario wacc and terminal_growth', pipeline._rate_convention([ERP, TABLE], 'USD'))
        self.assertIn('state terminal_growth for each scenario', pipeline._rate_convention([ERP, TABLE], 'EUR'))
        self.assertIn('state terminal_growth for each scenario', pipeline._rate_convention([ERP], 'USD'))
        self.assertIn('Give wacc and terminal_growth', pipeline._rate_convention([TABLE], 'USD'))
        self.assertNotIn('Python discounts with the current cost of capital', pipeline.FORECAST_SCHEMA)

    def test_a_null_rate_block_is_treated_as_absent_and_a_malformed_one_is_rejected(self):
        value = proposal()
        value['discount_rate'] = None
        with self.assertRaisesRegex(ValueError, 'dated discount_rate is required'):
            pin(value)
        value = proposal()
        value['discount_rate'] = ['not', 'an', 'object']
        with self.assertRaisesRegex(ValueError, 'must be an object'):
            pin(value)

    def test_stale_or_mismatched_balance_sheets_get_no_stub(self):
        value, _, notes = pin(proposal(), day='2027-06-30')
        self.assertNotIn('valuation_timing', value)
        self.assertTrue(any('days from the valuation date' in note for note in notes))
        mismatched = {'input_dates': {'revenue': {'end_date': '2025-12-31'}, 'net_debt': {'instant': '2025-09-30'}}}
        value, _, _ = pin(proposal(), reported=mismatched)
        self.assertNotIn('valuation_timing', value)

    def test_memo_exhibits_show_what_the_price_implies(self):
        value, _, _ = pin(proposal(copy.deepcopy(BOTTOM_UP)))
        result = pipeline.evaluate(value)
        text = pipeline.valuation_section(value, result)
        for expected in ('### Discount-rate path', '### What the price implies', 'Value under alternative conventions',
                         'Beta cross-check', 'Valuation date 2026-03-31'):
            self.assertIn(expected, text)
        self.assertNotIn('upside', text.lower())


if __name__ == '__main__':
    unittest.main()

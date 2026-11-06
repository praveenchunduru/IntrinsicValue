"""Independent identities for computed memo exhibits, without network/models."""
import copy
import json
import unittest

from investmentideas.valuation import evaluate


def plan():
    """Flat $150m annual FCFF perpetuity: operating EV is exactly $1.5bn."""
    return {
        'currency': 'USD', 'amount_unit': 'currency_units', 'share_unit': 'shares',
        'assumptions_label': 'Synthetic house assumptions for financial identities.',
        'valuation_date': '2026-11-06', 'market_price': 20,
        'actuals': {'revenue': 1_000_000_000, 'net_debt': -100_000_000,
                    'other_equity_claims': 20_000_000, 'shares_outstanding': 100_000_000},
        'source_refs': {key: ['S1'] for key in
                        ('revenue', 'net_debt', 'other_equity_claims', 'shares_outstanding', 'market_price')},
        'input_dates': {'market_price': {'observed_at': '2026-11-05T21:00:00Z'}},
        'forecast_periods': [{'fiscal_year': 2027 + year, 'period_end': f'{2027 + year}-09-30'}
                             for year in range(5)],
        'net_debt_bridge': {'currency': 'USD', 'as_of': '2026-09-30',
                            'debt': 100_000_000, 'selected_cash_and_nonoperating_investments': 200_000_000,
                            'net_debt': -100_000_000},
        'scenarios': {name: {'probability': weight, 'growth_rates': [0] * 5,
                             'ebit_margins': [.2] * 5, 'tax_rate': .25,
                             'sales_to_capital': 2, 'wacc': .1,
                             'terminal_growth': 0, 'terminal_roic': .15}
                      for name, weight in [('bear', .25), ('base', .5), ('bull', .25)]},
    }


def repurchase(amount=100_000_000, price=20, **overrides):
    return {'name': 'purchase', 'amount': amount, 'price': price,
            'source_refs': ['S2'], 'label': 'Synthetic one-period illustration',
            'interpretation': 'forward_conditional_illustration', **overrides}


def rate_plan(combined=False, unknown_split=False):
    inputs = plan()
    def record(value, label):
        return {'value': value, 'label': label, 'observed_at': '2026-09-30',
                'currency': 'USD', 'source_refs': ['S1']}
    for scenario in inputs['scenarios'].values():
        scenario.pop('wacc')
    inputs['discount_rate'] = {
        'as_of': '2026-11-06', 'currency': 'USD', 'amount_unit': 'currency_units',
        'lease_convention': 'gaap_operating',
        'risk_free_rate': record(.04, 'Matched Treasury'),
        'equity_risk_premium': record(.05, 'Matched ERP'),
        'unlevered_beta': record(1.2, 'Operating asset risk assumption'),
        'market_equity': record(2_000_000_000, 'Market equity'),
        'financial_debt': record(100_000_000, 'Financial debt excluding finance leases'),
        'finance_lease_debt': record(20_000_000, 'Separately reported finance lease liabilities'),
        'pretax_cost_of_debt': record(.06, 'Debt yield proxy'),
        'marginal_tax_rate': record(.25, 'Tax shield assumption'),
    }
    inputs['other_claims_bridge'] = {'currency': 'USD', 'as_of': '2026-09-30',
        'reported_finance_lease_liabilities': 20_000_000,
        'finance_leases_included_in_net_debt': combined,
        'other_equity_claims': 0 if combined else 20_000_000}
    inputs['net_debt_bridge']['finance_leases_included_in_net_debt'] = combined
    if combined:
        inputs['net_debt_bridge'].update(debt=120_000_000, net_debt=-80_000_000)
        inputs['actuals'].update(net_debt=-80_000_000, other_equity_claims=0)
    if unknown_split:
        inputs['other_claims_bridge']['reported_finance_lease_liabilities'] = None
        inputs['discount_rate']['financial_debt'].update(
            value=120_000_000, allocation='combined_debt_including_finance_leases',
            label='Combined debt including finance leases; separate split unavailable')
        inputs['discount_rate']['finance_lease_debt'].update(
            value=0, allocation='included_in_financial_debt',
            label='Zero allocation only; finance leases included in combined financial debt')
    return inputs


class MemoDiagnosticsTests(unittest.TestCase):
    def test_entry_and_original_exit_multiples_have_explicit_periods(self):
        result = evaluate(plan())
        exhibit = result['computed_diagnostics']['valuation_multiples']
        # Quote * ordinary shares + net debt + claims = 2bn - 100m + 20m.
        self.assertEqual(exhibit['market']['market_equity_value'], 2_000_000_000)
        self.assertEqual(exhibit['market']['enterprise_value'], 1_920_000_000)
        base = exhibit['scenarios']['base']
        self.assertAlmostEqual(base['forecast_year1']['market_ev_to_ebit']['value'], 9.6)
        self.assertAlmostEqual(base['forecast_year1']['market_ev_to_nopat']['value'], 12.8)
        # Zero growth / 10% WACC gives TV=150m/.1=1.5bn at end of year five.
        self.assertAlmostEqual(base['original_dcf_exit']['enterprise_value'], 1_500_000_000)
        self.assertAlmostEqual(base['original_dcf_exit']['ev_to_final_forecast_ebit']['value'], 7.5)
        self.assertAlmostEqual(base['original_dcf_exit']['ev_to_final_forecast_nopat']['value'], 10)
        self.assertEqual(base['forecast_year1']['period']['discount_time_years'], 1)
        self.assertEqual(base['original_dcf_exit']['period']['discount_time_years'], 5)
        self.assertEqual(base['forecast_year1']['period']['supplied_period_metadata']['fiscal_year'], 2027)

    def test_unchanged_multiple_return_matches_independent_coupon_identity(self):
        result = evaluate(plan())
        cases = result['computed_diagnostics']['valuation_multiples']['scenarios']['base']
        for metric in ('ebit', 'nopat'):
            case = cases['unchanged_entry_multiple_exit_returns'][metric]
            # $20 cost less $0.80 immediate bridge = $19.20 operating investment.
            # Flat earnings at an unchanged multiple return $19.20 exit plus
            # a $1.50 annual FCFF coupon. Yield = 1.50 / 19.20 = 7.8125%.
            self.assertAlmostEqual(case['annual_return_solutions'][0], .078125, places=8)
            self.assertAlmostEqual(case['conditional_exit_enterprise_value'], 1_920_000_000)
            self.assertAlmostEqual(case['cashflows_per_share'][0], -19.2)
            self.assertAlmostEqual(case['cashflows_per_share'][-1], 20.7)
            self.assertEqual(case['entry_earnings_period'], 'first_explicit_forecast_year')
            self.assertEqual(case['exit_earnings_period'], 'last_explicit_forecast_year')
            self.assertIn('integer model years', case['basis'])
        # The actual DCF terminal amount is preserved, not replaced by the exhibit.
        self.assertAlmostEqual(result['scenarios']['base']['terminal_enterprise_value'], 1_500_000_000)

    def test_terminal_next_year_and_final_forecast_denominators_are_distinct(self):
        inputs = plan()
        inputs['scenarios']['base']['terminal_growth'] = .02
        case = evaluate(inputs)['computed_diagnostics']['valuation_multiples']['scenarios']['base']['original_dcf_exit']
        self.assertEqual(case['final_forecast_nopat'], 150_000_000)
        self.assertAlmostEqual(case['terminal_year_nopat'], 153_000_000)
        self.assertAlmostEqual(case['ev_to_final_forecast_nopat']['value'] /
                               case['ev_to_terminal_year_nopat']['value'], 1.02)

    def test_missing_quote_and_nonpositive_earnings_do_not_make_fake_multiples(self):
        inputs = plan()
        del inputs['market_price']
        result = evaluate(inputs)['computed_diagnostics']['valuation_multiples']
        self.assertEqual(result['market']['status'], 'unavailable')
        case = result['scenarios']['base']
        self.assertIsNone(case['forecast_year1']['market_ev_to_ebit']['value'])
        self.assertEqual(case['unchanged_entry_multiple_exit_returns']['ebit']['status'], 'unavailable')
        self.assertEqual(case['original_dcf_exit']['ev_to_final_forecast_ebit']['status'], 'calculated')
        for first_margin in (0, -.2):
            inputs = plan()
            inputs['scenarios']['base']['ebit_margins'][0] = first_margin
            case = evaluate(inputs)['computed_diagnostics']['valuation_multiples']['scenarios']['base']
            self.assertEqual(case['forecast_year1']['market_ev_to_ebit']['status'], 'not_meaningful')
            self.assertEqual(case['forecast_year1']['market_ev_to_ebit']['denominator'], first_margin * 1e9)
            self.assertIsNone(case['forecast_year1']['market_ev_to_ebit']['value'])
            self.assertEqual(case['unchanged_entry_multiple_exit_returns']['nopat']['status'], 'not_meaningful')

    def test_negative_market_EV_is_retained_without_conventional_multiple(self):
        inputs = plan()
        inputs['actuals']['net_debt'] = -3_000_000_000
        case = evaluate(inputs)['computed_diagnostics']['valuation_multiples']
        self.assertEqual(case['market']['enterprise_value'], -980_000_000)
        self.assertEqual(case['scenarios']['base']['forecast_year1']['market_ev_to_ebit']['status'], 'not_meaningful')

    def test_entry_policy_uses_explicit_discount_and_preserves_valuation(self):
        inputs = plan()
        original = evaluate(inputs)
        self.assertNotIn('entry_policy', original['computed_diagnostics'])
        inputs['entry_policy'] = {'basis': 'base', 'margin_of_safety': .25,
                                  'label': 'Chosen house rule', 'rationale': 'Synthetic explicit decision policy'}
        untouched = copy.deepcopy(inputs)
        changed = evaluate(inputs)
        policy = changed['computed_diagnostics']['entry_policy']
        # EV1.5bn + cash200m - debt100m - claims20m = 1.58bn, /100m.
        self.assertAlmostEqual(policy['reference_per_share_value'], 15.8)
        self.assertAlmostEqual(policy['conditional_entry_price'], 11.85)
        self.assertEqual(policy['policy'], inputs['entry_policy'])
        self.assertEqual(changed['scenarios'], original['scenarios'])
        self.assertEqual(inputs, untouched)

    def test_weighted_entry_policy_and_zero_discount_have_no_hidden_default(self):
        inputs = plan()
        inputs['scenarios']['bear']['ebit_margins'] = [.1] * 5
        inputs['scenarios']['bull']['ebit_margins'] = [.4] * 5
        inputs['entry_policy'] = {'basis': 'weighted', 'margin_of_safety': 0,
                                  'label': 'Explicit no-haircut rule', 'rationale': 'Use disclosed weights'}
        policy = evaluate(inputs)['computed_diagnostics']['entry_policy']
        # (.25 * 750m + .5 * 1500m + .25 * 3000m + net80m) /100m.
        self.assertAlmostEqual(policy['conditional_entry_price'], 17.675)
        inputs['actuals']['net_debt'] = 100_000_000_000
        policy = evaluate(inputs)['computed_diagnostics']['entry_policy']
        self.assertLess(policy['reference_per_share_value'], 0)
        self.assertIsNone(policy['conditional_entry_price'])
        self.assertEqual(policy['status'], 'not_meaningful')

    def test_entry_policy_rejects_missing_or_invalid_policy_choices(self):
        valid = {'basis': 'base', 'margin_of_safety': 0, 'label': 'Rule', 'rationale': 'Reason'}
        invalid = [None, {}, {**valid, 'basis': 'bull'}, {**valid, 'margin_of_safety': -1},
                   {**valid, 'margin_of_safety': 1}, {**valid, 'margin_of_safety': True},
                   {**valid, 'margin_of_safety': float('nan')}, {**valid, 'label': ''},
                   {**valid, 'rationale': ''}, {**valid, 'unexpected': 1}]
        for policy in invalid:
            with self.subTest(policy=policy):
                inputs = plan()
                inputs['entry_policy'] = policy
                with self.assertRaises(ValueError):
                    evaluate(inputs)

    def test_cash_funded_repurchase_changes_cash_equity_and_shares_once(self):
        inputs = plan()
        original = evaluate(inputs)
        inputs['repurchase_tests'] = [repurchase()]
        untouched = copy.deepcopy(inputs)
        changed = evaluate(inputs)
        test = changed['computed_diagnostics']['repurchase_tests'][0]
        self.assertEqual(test['shares_repurchased'], 5_000_000)
        self.assertEqual(test['post_repurchase_shares'], 95_000_000)
        self.assertEqual(test['post_repurchase_nonoperating_cash'], 100_000_000)
        self.assertEqual(test['post_repurchase_net_debt'], 0)
        self.assertEqual(test['debt_unchanged'], 100_000_000)
        for case in test['scenarios'].values():
            self.assertAlmostEqual(case['post_repurchase_equity_value'], 1_480_000_000, delta=1e-5)
            self.assertAlmostEqual(case['post_repurchase_per_share_value'], 1_480 / 95)
            self.assertAlmostEqual(case['value_transferred_to_selling_holders_vs_original_house_value'], 21_000_000, delta=1e-5)
            # Value loss per remaining share * remaining shares equals transfer.
            self.assertAlmostEqual(-case['value_change']['dollar_delta'] * 95_000_000, 21_000_000, delta=1e-5)
        self.assertEqual(changed['scenarios'], original['scenarios'])
        self.assertEqual(changed['weighted_per_share_value'], original['weighted_per_share_value'])
        self.assertEqual(inputs, untouched)

    def test_fair_and_discount_repurchase_identity_and_independent_cases(self):
        inputs = plan()
        inputs['repurchase_tests'] = [
            repurchase(price=15.8, name='fair'),
            repurchase(price=10, name='discount', interpretation='historical_retrospective_conditional')]
        tests = evaluate(inputs)['computed_diagnostics']['repurchase_tests']
        self.assertAlmostEqual(tests[0]['scenarios']['base']['post_repurchase_per_share_value'], 15.8)
        self.assertAlmostEqual(tests[0]['scenarios']['base']['value_change']['dollar_delta'], 0)
        self.assertEqual(tests[1]['post_repurchase_shares'], 90_000_000)
        self.assertAlmostEqual(tests[1]['scenarios']['base']['post_repurchase_per_share_value'], 1_480 / 90)
        self.assertEqual(tests[1]['post_repurchase_nonoperating_cash'], 100_000_000)
        self.assertIn('not observed historical value destruction', tests[1]['basis'])

    def test_repurchase_bounds_and_cash_bridge_reconciliation(self):
        invalid = [None, {}, [repurchase(amount=200_000_001)], [repurchase(amount=0)],
                   [repurchase(price=0)], [repurchase(price=1)], [repurchase(price=float('inf'))],
                   [repurchase(source_refs=[])], [repurchase(interpretation='historical_loss')],
                   [repurchase(), repurchase()], [repurchase(extra=1)],
                   [repurchase(name=f'case{i}') for i in range(9)]]
        for cases in invalid:
            with self.subTest(cases=cases):
                inputs = plan()
                inputs['repurchase_tests'] = cases
                with self.assertRaises(ValueError):
                    evaluate(inputs)
        for edit in ('missing', 'cash', 'debt', 'net_debt'):
            inputs = plan()
            inputs['repurchase_tests'] = [repurchase()]
            if edit == 'missing':
                del inputs['net_debt_bridge']
            else:
                key = 'selected_cash_and_nonoperating_investments' if edit == 'cash' else edit
                inputs['net_debt_bridge'][key] += 1_000_000
            with self.subTest(bridge=edit), self.assertRaises(ValueError):
                evaluate(inputs)
        for key, value in (('currency', 'EUR'), ('amount_unit', 'millions')):
            inputs = plan()
            inputs['repurchase_tests'] = [repurchase()]
            inputs['net_debt_bridge'][key] = value
            with self.subTest(funding_units=key), self.assertRaises(ValueError):
                evaluate(inputs)
        # The entire explicitly modeled cash balance may be used if shares stay positive.
        inputs = plan()
        inputs['repurchase_tests'] = [repurchase(amount=200_000_000)]
        self.assertEqual(evaluate(inputs)['computed_diagnostics']['repurchase_tests'][0]['post_repurchase_nonoperating_cash'], 0)

    def test_exhibits_remain_strict_json_even_with_undefined_ratios(self):
        inputs = plan()
        inputs['scenarios']['bear']['ebit_margins'][0] = 0
        json.dumps(evaluate(inputs), allow_nan=False)

    def test_combined_debt_known_lease_split_matches_separate_bridge_identity(self):
        separate = evaluate(rate_plan())
        combined = evaluate(rate_plan(combined=True))
        calibration = combined['discount_rate_calculation']
        # D=100m financial +20m leases, beta=1.2*(1+.75*120/2000).
        # Equity cost=10.27%, after-tax debt cost=4.5%, capital=2120m.
        expected_wacc = (2_000 * .1027 + 120 * .045) / 2_120
        self.assertAlmostEqual(calibration['included_debt'], 120_000_000)
        self.assertAlmostEqual(calibration['applied_wacc'], expected_wacc)
        # Both bridges deduct total120m - cash200m, exactly once.
        self.assertAlmostEqual(combined['weighted_per_share_value'],
                               (150_000_000 / expected_wacc + 80_000_000) / 100_000_000)
        for name in ('bear', 'base', 'bull'):
            for field in ('enterprise_value', 'equity_value', 'per_share_value',
                          'cashflows', 'terminal_enterprise_value', 'fixed_exit_return_at_market_price'):
                self.assertEqual(combined['scenarios'][name][field], separate['scenarios'][name][field])
        self.assertEqual(calibration['financial_debt'], 100_000_000)
        self.assertEqual(calibration['finance_lease_debt'], 20_000_000)
        checks = calibration['valuation_input_reconciliations']
        self.assertEqual(checks[0]['bridge_input'], 100_000_000)
        self.assertEqual(checks[1]['bridge_input'], 20_000_000)
        self.assertEqual(checks[2]['status'], 'reconciled_combined_debt_once')

    def test_unknown_lease_split_requires_declared_zero_allocation_not_absence(self):
        inputs = rate_plan(combined=True, unknown_split=True)
        untouched = copy.deepcopy(inputs)
        result = evaluate(inputs)
        calibration = result['discount_rate_calculation']
        self.assertEqual(calibration['included_debt'], 120_000_000)
        self.assertEqual(calibration['financial_debt'], 120_000_000)
        self.assertEqual(calibration['finance_lease_debt'], 0)
        self.assertIn('zero is an allocation, not reported absence',
                      calibration['conventions']['financial_debt_input'])
        self.assertIsNone(calibration['valuation_input_reconciliations'][0]['reported_finance_lease_liabilities'])
        self.assertTrue(calibration['valuation_input_reconciliations'][1][
            'zero_finance_lease_input_is_allocation_not_reported_absence'])
        separate = evaluate(rate_plan())
        for name in ('bear', 'base', 'bull'):
            for field in ('enterprise_value', 'equity_value', 'per_share_value',
                          'cashflows', 'terminal_enterprise_value', 'fixed_exit_return_at_market_price'):
                self.assertEqual(result['scenarios'][name][field], separate['scenarios'][name][field])
        self.assertEqual(inputs, untouched)

    def test_combined_debt_rejects_duplicate_inclusion_and_conflicting_split(self):
        mutations = [
            lambda p: p['discount_rate']['financial_debt'].update(value=120_000_000),
            lambda p: p['discount_rate']['finance_lease_debt'].update(value=0),
            lambda p: p['other_claims_bridge'].update(reported_finance_lease_liabilities=130_000_000),
            lambda p: p['other_claims_bridge'].update(finance_leases_included_in_net_debt=False),
            lambda p: p['actuals'].update(other_equity_claims=20_000_000),
            lambda p: p['net_debt_bridge'].update(finance_leases_included_in_net_debt='true'),
        ]
        for mutate in mutations:
            inputs = rate_plan(combined=True)
            mutate(inputs)
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                evaluate(inputs)

    def test_combined_debt_rejects_unmatched_dates_currency_and_measurement(self):
        mutations = [
            lambda p: p['discount_rate']['financial_debt'].update(observed_at='2026-09-29'),
            lambda p: p['discount_rate']['finance_lease_debt'].update(observed_at='2026-09-29'),
            lambda p: p['other_claims_bridge'].update(as_of='2026-09-29'),
            lambda p: p['net_debt_bridge'].update(currency='EUR'),
            lambda p: p['other_claims_bridge'].update(amount_unit='millions'),
            lambda p: p['discount_rate']['financial_debt'].update(measurement='market_value'),
            lambda p: p['other_claims_bridge'].update(measurement='market_value'),
        ]
        for mutate in mutations:
            inputs = rate_plan(combined=True)
            mutate(inputs)
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                evaluate(inputs)

    def test_unknown_split_rejects_undeclared_or_mislabelled_allocation(self):
        mutations = [
            lambda p: p['discount_rate']['financial_debt'].pop('allocation'),
            lambda p: p['discount_rate']['finance_lease_debt'].pop('allocation'),
            lambda p: p['discount_rate']['financial_debt'].update(value=100_000_000),
            lambda p: p['discount_rate']['finance_lease_debt'].update(value=20_000_000),
            lambda p: p['discount_rate']['finance_lease_debt'].update(label='Reported zero finance leases'),
            lambda p: p['discount_rate']['financial_debt'].update(label='Financial debt excluding leases'),
        ]
        for mutate in mutations:
            inputs = rate_plan(combined=True, unknown_split=True)
            mutate(inputs)
            with self.subTest(inputs=inputs), self.assertRaises(ValueError):
                evaluate(inputs)


if __name__ == '__main__':
    unittest.main()

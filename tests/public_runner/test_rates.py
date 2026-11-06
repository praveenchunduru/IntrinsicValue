"""Source-dated cost-of-capital arithmetic; no model or network calls."""
import copy
import json
import unittest

from investmentideas.rates import calculate_discount_rate, calculate_wacc


def record(value, label, date='2026-01-02', **metadata):
    return {'value': value, 'observed_at': date, 'source_refs': ['S1'],
            'label': label, **metadata}


def inputs():
    return {
        'as_of': '2026-01-03', 'currency': 'USD', 'amount_unit': 'currency_units',
        'lease_convention': 'gaap_operating',
        'risk_free_rate': record(.04, 'Paired ten-year Treasury observation'),
        'equity_risk_premium': record(.05, 'Matched implied ERP, specified payout method'),
        'unlevered_beta': record(1.2, 'Sector unlevered beta proxy', date='2025-01-01', sector='Computers'),
        'market_equity': record(1000, 'Market equity at the quoted share price'),
        'financial_debt': record(200, 'Financial borrowing excluding finance leases'),
        'finance_lease_debt': record(10, 'Reported finance lease liabilities'),
        'pretax_cost_of_debt': record(.06, 'Dated issuer debt-yield proxy; not coupon'),
        'marginal_tax_rate': record(.25, 'Normalized marginal tax assumption'),
    }


class DiscountRateTests(unittest.TestCase):
    def test_capm_relevering_and_market_capital_weights(self):
        result = calculate_discount_rate(inputs())
        # Independently fixed example: $109.45 equity cost + $9.45 debt cost,
        # divided by $1,210 of market equity and included gross debt.
        self.assertAlmostEqual(result['market_wacc'], 118.9 / 1210)
        self.assertAlmostEqual(result['relevered_beta'], 1.389)
        self.assertAlmostEqual(result['cost_of_equity'], .10945)
        self.assertAlmostEqual(result['after_tax_cost_of_debt'], .045)
        self.assertEqual(result['included_debt'], 210)
        self.assertAlmostEqual(result['equity_weight'], 1000 / 1210)
        self.assertAlmostEqual(result['debt_weight'], 210 / 1210)
        self.assertEqual(result['applied_wacc'], result['market_wacc'])
        self.assertEqual(result['conventions']['financial_debt_measurement'], 'book_value_proxy')
        self.assertEqual(result, calculate_wacc(inputs()))

    def test_zero_debt_is_explicit_and_unlevered_beta_is_retained(self):
        spec = inputs()
        spec['financial_debt']['value'] = spec['finance_lease_debt']['value'] = 0
        result = calculate_discount_rate(spec)
        self.assertAlmostEqual(result['market_wacc'], .10)
        self.assertEqual(result['relevered_beta'], 1.2)
        self.assertEqual((result['equity_weight'], result['debt_weight']), (1, 0))
        for missing in ('financial_debt', 'finance_lease_debt'):
            invalid = copy.deepcopy(spec)
            del invalid[missing]
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, 'explicit dated input'):
                calculate_discount_rate(invalid)

    def test_gaap_operating_lease_liabilities_do_not_enter_capital_weights(self):
        spec = inputs()
        baseline = calculate_discount_rate(spec)
        spec['operating_lease_debt'] = record(9000, 'GAAP operating lease liability, excluded from debt')
        result = calculate_discount_rate(spec)
        self.assertEqual(result['market_wacc'], baseline['market_wacc'])
        self.assertEqual(result['included_debt'], 210)
        self.assertEqual(result['operating_lease_debt_included'], 0)
        self.assertIn('operating expense remains', result['conventions']['operating_leases'])
        self.assertEqual(result['provenance']['operating_lease_debt']['value'], 9000)

    def test_capitalized_operating_leases_require_explicit_adjusted_ebit(self):
        spec = inputs()
        spec['lease_convention'] = 'capitalized_operating'
        spec['operating_lease_debt'] = record(90, 'Capitalized operating lease liabilities')
        for ebit in (None, 'gaap_operating'):
            if ebit is not None:
                spec['ebit_convention'] = ebit
            with self.subTest(ebit=ebit), self.assertRaisesRegex(ValueError, 'lease_adjusted EBIT'):
                calculate_discount_rate(spec)
        spec['ebit_convention'] = 'operating_lease_adjusted'
        result = calculate_discount_rate(spec)
        self.assertEqual(result['included_debt'], 300)
        self.assertAlmostEqual(result['market_wacc'], 127 / 1300)
        del spec['operating_lease_debt']
        with self.assertRaisesRegex(ValueError, 'explicit operating_lease_debt'):
            calculate_discount_rate(spec)

    def test_hurdle_is_distinct_and_requires_explicit_application(self):
        spec = inputs()
        baseline = calculate_discount_rate(spec)
        spec['house_hurdle'] = record(.12, 'Documented investment committee hurdle')
        result = calculate_discount_rate(spec)
        self.assertEqual(result['applied_wacc'], baseline['market_wacc'])
        self.assertEqual(result['house_hurdle'], .12)
        spec['basis'] = 'house_hurdle'
        result = calculate_discount_rate(spec)
        self.assertEqual(result['applied_wacc'], .12)
        self.assertEqual(result['market_wacc'], baseline['market_wacc'])
        del spec['house_hurdle']
        with self.assertRaisesRegex(ValueError, 'explicit dated house_hurdle'):
            calculate_discount_rate(spec)

    def test_newer_riskfree_cannot_be_cross_paired_with_older_erp(self):
        spec = inputs()
        spec['risk_free_rate']['observed_at'] = '2026-01-03'
        with self.assertRaisesRegex(ValueError, 'matched observation date'):
            calculate_discount_rate(spec)
        # Beta and issuer balance-sheet dates need not match the market pair.
        spec['risk_free_rate']['observed_at'] = '2026-01-02'
        result = calculate_discount_rate(spec)
        self.assertEqual(result['matched_observation_date'], '2026-01-02')
        self.assertEqual(result['input_ages_days']['unlevered_beta'], 367)

    def test_future_observations_publications_and_valuation_dates_are_rejected(self):
        for name in inputs():
            if isinstance(inputs()[name], dict):
                spec = inputs()
                spec[name]['observed_at'] = '2026-01-04'
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'after as_of'):
                    calculate_discount_rate(spec)
        spec = inputs()
        spec['unlevered_beta']['publication_date'] = '2026-01-04'
        with self.assertRaisesRegex(ValueError, 'publication_date is after as_of'):
            calculate_discount_rate(spec)
        spec = inputs()
        spec['as_of'] = '9999-01-01'
        with self.assertRaisesRegex(ValueError, 'cannot be in the future'):
            calculate_discount_rate(spec)

    def test_dates_are_strict_and_optional_argument_cannot_disagree(self):
        for invalid in ('20260102', '2026-1-2', '2026-02-30', None, 20260102, True):
            spec = inputs()
            spec['risk_free_rate']['observed_at'] = invalid
            with self.subTest(date=invalid), self.assertRaisesRegex(ValueError, 'YYYY-MM-DD'):
                calculate_discount_rate(spec)
        spec = inputs()
        del spec['as_of']
        self.assertEqual(calculate_discount_rate(spec, as_of='2026-01-03')['as_of'], '2026-01-03')
        with self.assertRaisesRegex(ValueError, 'must match'):
            calculate_discount_rate(inputs(), as_of='2026-01-02')

    def test_beta_month_precision_is_preserved_without_inventing_a_day(self):
        spec = inputs()
        spec['unlevered_beta'].update(observed_at='2025-01', date_precision='month')
        result = calculate_discount_rate(spec)
        self.assertEqual(result['provenance']['unlevered_beta']['observed_at'], '2025-01')
        self.assertEqual(result['input_observation_periods']['unlevered_beta'],
                         {'start': '2025-01-01', 'end': '2025-01-31', 'precision': 'month'})
        self.assertEqual(result['input_ages_days']['unlevered_beta'], 337)
        for observed, precision in [('2026-01', 'month'), ('2025-01-01', 'month'), ('2025-01', 'day')]:
            spec['unlevered_beta'].update(observed_at=observed, date_precision=precision)
            with self.subTest(observed=observed, precision=precision), self.assertRaises(ValueError):
                calculate_discount_rate(spec)
        spec = inputs()
        for name in ('risk_free_rate', 'equity_risk_premium'):
            spec[name].update(observed_at='2025-12', date_precision='month')
        with self.assertRaisesRegex(ValueError, 'market pairs require exact dates'):
            calculate_discount_rate(spec)

    def test_audit_records_are_deep_copied_and_json_serializable(self):
        spec = inputs()
        before = copy.deepcopy(spec)
        result = calculate_discount_rate(spec)
        self.assertEqual(spec, before)
        self.assertEqual(result['provenance']['unlevered_beta']['sector'], 'Computers')
        result['provenance']['risk_free_rate']['source_refs'].append('mutation')
        self.assertEqual(spec, before)
        json.dumps(result, allow_nan=False)

    def test_sensitivities_change_one_parameter_and_keep_hurdle_separate(self):
        spec = inputs()
        spec['house_hurdle'] = record(.12, 'Explicit house hurdle')
        spec['basis'] = 'house_hurdle'
        result = calculate_discount_rate(spec)
        self.assertEqual(len(result['sensitivity']), 8)
        raised_rf = next(row for row in result['sensitivity']
                         if row['parameter'] == 'risk_free_rate' and row['change'] > 0)
        self.assertAlmostEqual(raised_rf['market_wacc'] - result['market_wacc'], .005 * result['equity_weight'])
        self.assertEqual(raised_rf['relevered_beta'], result['relevered_beta'])
        self.assertEqual(result['applied_wacc'], .12)
        self.assertNotIn('applied_wacc', raised_rf)
        self.assertIn('not a scenario risk premium', raised_rf['purpose'])

    def test_invalid_metadata_units_amounts_and_rates_do_not_become_defaults(self):
        mutations = [
            ('risk_free_rate', 'value', True), ('equity_risk_premium', 'value', '0.05'),
            ('unlevered_beta', 'value', float('nan')), ('financial_debt', 'value', float('inf')),
            ('financial_debt', 'value', -1), ('market_equity', 'value', 0),
            ('marginal_tax_rate', 'value', .8), ('pretax_cost_of_debt', 'value', -.01),
            ('equity_risk_premium', 'source_refs', []), ('equity_risk_premium', 'source_refs', ['']),
            ('unlevered_beta', 'label', ''), ('market_equity', 'measurement', 'book_value'),
            ('financial_debt', 'measurement', 'net_debt'), ('financial_debt', 'amount_unit', 'millions'),
            ('risk_free_rate', 'currency', 'EUR'), ('risk_free_rate', 'notes', {'bad': object()}),
        ]
        for name, key, value in mutations:
            spec = inputs()
            spec[name][key] = value
            with self.subTest(name=name, key=key, value=value), self.assertRaises(ValueError):
                calculate_discount_rate(spec)
        for key, value in [('amount_unit', 'millions'), ('currency', 'usd'),
                           ('basis', 'scenario_premium'), ('scenario_risk_premium', .02),
                           ('lease_convention', 'automatic'), ('ebit_convention', 'operating_lease_adjusted')]:
            spec = inputs()
            spec[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                calculate_discount_rate(spec)

    def test_boundary_sensitivities_are_disclosed_instead_of_clipped(self):
        spec = inputs()
        spec['pretax_cost_of_debt']['value'] = 0
        result = calculate_discount_rate(spec)
        below = next(row for row in result['sensitivity']
                     if row['parameter'] == 'pretax_cost_of_debt' and row['change'] < 0)
        self.assertIn('unavailable_reason', below)
        self.assertEqual(below['input_value'], -.01)
        self.assertNotIn('market_wacc', below)


if __name__ == '__main__':
    unittest.main()

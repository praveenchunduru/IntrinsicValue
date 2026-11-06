import copy
import json
import unittest

from investmentideas.operating import apply, derive, derive_counterfactual, validate


def plan():
    result = {
        'currency': 'USD', 'amount_unit': 'currency_units', 'share_unit': 'shares',
        'actuals': {'revenue': 1000, 'net_debt': 50, 'other_equity_claims': 10,
                    'shares_outstanding': 100},
        'scenarios': {},
        'operating_model': {
            'currency': 'USD', 'amount_unit': 'currency_units',
            'assumptions_label': 'Forward growth, gross margins and GAAP expenses are house assumptions.',
            'earnings_mode': 'gross_profit_less_expenses',
            'components': [
                {'name': 'products', 'base_revenue': 700, 'source_refs': ['S1'],
                 'gross_margin_group': 'products', 'disclosure': 'Shared forward group margin; individual category margin unreported.'},
                {'name': 'services', 'base_revenue': 300, 'source_refs': ['S2']}],
            'scenarios': {}}}
    definitions = {
        'bear': ([0, 0], [0, 0], [.3, .3], [.7, .7], [100, 100], [80, 80], .25),
        'base': ([.1, 0], [.2, .1], [.4, .4], [.8, .8], [100, 110], [50, 55], .5),
        'bull': ([.2, .1], [.3, .2], [.45, .46], [.82, .83], [110, 120], [55, 60], .25)}
    for name, (products, services, product_margin, service_margin, rd, sga, probability) in definitions.items():
        result['scenarios'][name] = {'probability': probability, 'tax_rate': .2,
                                     'sales_to_capital': 2, 'wacc': .1,
                                     'terminal_growth': .02, 'terminal_roic': .15}
        result['operating_model']['scenarios'][name] = {
            'components': {'products': {'growth_rates': products, 'gross_margins': product_margin},
                           'services': {'growth_rates': services, 'gross_margins': service_margin}},
            'expenses': {'research_and_development': rd, 'selling_general_administrative': sga}}
    return result


class OperatingTests(unittest.TestCase):
    def test_component_mix_and_gaap_expenses_reconcile_to_consolidated_earnings(self):
        inputs = plan()
        original = copy.deepcopy(inputs)
        result = derive(inputs)
        first, second = result['scenarios']['base']['cashflows']
        self.assertAlmostEqual(first['components']['products']['revenue'], 770)
        self.assertAlmostEqual(first['components']['services']['revenue'], 360)
        self.assertAlmostEqual(first['revenue'], 1130)
        # Growth is starting-revenue weighted (13%), not an average of 10% and 20%.
        self.assertAlmostEqual(first['revenue_growth'], .13)
        self.assertAlmostEqual(first['gross_profit'], 596)
        self.assertEqual(first['expense_breakdown'], {'research_and_development': 100., 'selling_general_administrative': 50.})
        self.assertAlmostEqual(first['operating_expenses'], 150)
        self.assertAlmostEqual(first['ebit'], 446)
        self.assertAlmostEqual(second['revenue'], 1166)
        self.assertAlmostEqual(second['gross_profit'], 624.8)
        self.assertAlmostEqual(second['ebit'], 459.8)
        self.assertGreater(second['gross_profit'] / second['revenue'], first['gross_profit'] / first['revenue'])
        for case in result['scenarios'].values():
            for row in case['cashflows']:
                self.assertAlmostEqual(row['revenue'], sum(component['revenue'] for component in row['components'].values()))
                self.assertAlmostEqual(row['gross_profit'], sum(component['gross_profit'] for component in row['components'].values()))
                self.assertAlmostEqual(row['ebit'], row['gross_profit'] - sum(row['expense_breakdown'].values()))
                self.assertAlmostEqual(row['ebit_margin'], row['ebit'] / row['revenue'])
        self.assertEqual(result['components'][0]['source_refs'], ['S1'])
        self.assertIn('individual category margin unreported', first['components']['products']['disclosure'])
        self.assertEqual(inputs, original)
        json.dumps(result, allow_nan=False)

    def test_higher_rd_lowers_ebit_without_changing_component_revenue(self):
        inputs = plan()
        before = derive(inputs)['scenarios']['base']['cashflows'][0]
        inputs['operating_model']['scenarios']['base']['expenses']['research_and_development'][0] += 25
        after = derive(inputs)['scenarios']['base']['cashflows'][0]
        self.assertEqual(before['components'], after['components'])
        self.assertEqual(before['revenue'], after['revenue'])
        self.assertAlmostEqual(before['ebit'] - after['ebit'], 25)
        self.assertAlmostEqual(before['ebit_margin'] - after['ebit_margin'], 25 / 1130)

    def test_revenue_only_keeps_explicit_ebit_path_without_inventing_allocations(self):
        inputs = plan()
        inputs['operating_model']['earnings_mode'] = 'aggregate_ebit_margins'
        for name, case in inputs['operating_model']['scenarios'].items():
            case.pop('expenses')
            for component in case['components'].values():
                component.pop('gross_margins')
            inputs['scenarios'][name]['ebit_margins'] = [.2, .21]
        materialized, result = apply(inputs)
        self.assertEqual(materialized['scenarios']['base']['ebit_margins'], [.2, .21])
        for case in result['scenarios'].values():
            for row in case['cashflows']:
                self.assertIsNone(row['gross_profit'])
                self.assertIsNone(row['operating_expenses'])
                self.assertIsNone(row['expense_breakdown'])
                self.assertAlmostEqual(row['ebit'], row['revenue'] * row['ebit_margin'])
                for component in row['components'].values():
                    self.assertIsNone(component['gross_profit'])
                    self.assertIsNone(component['gross_margin'])
        counter_inputs, counter = derive_counterfactual(inputs, {'components': {'services': {'growth_rates': [.3, .1]}}})
        self.assertEqual(counter_inputs['ebit_margins'], [.2, .21])
        self.assertEqual(counter['fixed_input_basis']['retained_aggregate_ebit_margins'], [.2, .21])
        self.assertIsNone(counter['cashflows'][0]['gross_profit'])
        with self.assertRaises(ValueError):
            derive_counterfactual(inputs, {'components': {'services': {'gross_margins': [.8, .8]}}})
        del inputs['scenarios']['base']['ebit_margins']
        with self.assertRaisesRegex(ValueError, 'explicit aggregate'):
            derive(inputs)

    def test_apply_populates_only_derived_forecasts_and_checks_duplicates(self):
        inputs = plan()
        original = copy.deepcopy(inputs)
        materialized, result = apply(inputs)
        self.assertEqual(materialized['actuals'], inputs['actuals'])
        self.assertEqual(materialized['operating_model'], inputs['operating_model'])
        self.assertEqual(inputs, original)
        for name, case in inputs['scenarios'].items():
            for field, value in case.items():
                self.assertEqual(materialized['scenarios'][name][field], value)
            self.assertEqual(materialized['scenarios'][name]['growth_rates'], result['scenarios'][name]['growth_rates'])
        self.assertEqual(apply(materialized), (materialized, result))
        materialized['scenarios']['base']['growth_rates'][0] += .001
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            apply(materialized)
        materialized, _ = apply(inputs)
        materialized['scenarios']['base']['ebit_margins'][0] += .001
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            apply(materialized)

    def test_generic_path_is_unchanged_when_model_absent(self):
        inputs = {'scenarios': {'base': {'growth_rates': [.05]}}}
        self.assertIsNone(validate(inputs))
        self.assertIsNone(derive(inputs))
        result, forecasts = apply(inputs)
        self.assertEqual(result, inputs)
        self.assertIsNot(result, inputs)
        self.assertIsNone(forecasts)

    def test_counterfactual_aggregate_overrides_require_explicit_detachment(self):
        inputs = plan()
        baseline = derive(inputs)['scenarios']
        inputs['counterfactuals'] = {'lower_rate': {'wacc': .08}}
        basis = derive(inputs)['counterfactual_basis']['lower_rate']
        self.assertTrue(basis['component_breakdown_derived'])
        self.assertEqual(basis['forecast_basis'], 'operating_model_base')
        inputs['counterfactuals']['higher_growth'] = {'growth_rates': [.2, .2]}
        with self.assertRaisesRegex(ValueError, 'counterfactual_policy=aggregate_override'):
            apply(inputs)
        inputs['operating_model']['counterfactual_policy'] = 'aggregate_override'
        _, result = apply(inputs)
        basis = result['counterfactual_basis']['higher_growth']
        self.assertFalse(basis['component_breakdown_derived'])
        self.assertEqual(basis['overridden_operating_fields'], ['growth_rates'])
        self.assertIn('not derived', basis['disclosure'])
        self.assertEqual(result['scenarios'], baseline)
        inputs['counterfactuals']['not_allowed'] = {'actuals': {'net_debt': 0}}
        with self.assertRaisesRegex(ValueError, 'never actuals or probabilities'):
            derive(inputs)

    def test_invalid_facts_dimensions_and_unsupported_inputs_are_rejected(self):
        mutations = [
            lambda x: x['operating_model'].update(currency='EUR'),
            lambda x: x['operating_model'].update(amount_unit='millions'),
            lambda x: x['operating_model'].update(actuals={'revenue': 999}),
            lambda x: x['operating_model'].update(assumptions_label=''),
            lambda x: x['operating_model']['components'][0].update(base_revenue=701),
            lambda x: x['operating_model']['components'][0].update(source_refs=[]),
            lambda x: x['operating_model']['components'][0].update(base_revenue=float('nan')),
            lambda x: x['operating_model']['components'][1].update(name='PRODUCTS'),
            lambda x: x['operating_model']['scenarios']['bear'].update(probability=1),
            lambda x: x['operating_model']['scenarios']['bear']['components'].pop('services'),
            lambda x: x['operating_model']['scenarios']['base']['components']['products'].update(growth_rates=[.1]),
            lambda x: x['operating_model']['scenarios']['base']['components']['products'].update(gross_margins=[.4]),
            lambda x: x['operating_model']['scenarios']['base']['components']['products'].update(growth_rates=[3.1, .1]),
            lambda x: x['operating_model']['scenarios']['base']['components']['products'].update(gross_margins=[True, .4]),
            lambda x: x['operating_model']['scenarios']['base']['expenses'].pop('selling_general_administrative'),
            lambda x: x['operating_model']['scenarios']['base']['expenses'].update(research_and_development=[-1, 0]),
            lambda x: x['operating_model']['scenarios']['base']['expenses'].update(research_and_development=[float('inf'), 0])]
        for i, mutate in enumerate(mutations):
            with self.subTest(mutation=i):
                inputs = plan()
                mutate(inputs)
                with self.assertRaises(ValueError):
                    apply(inputs)

    def test_driver_counterfactual_reconsolidates_existing_components_without_mutation(self):
        inputs, _ = apply(plan())
        original = copy.deepcopy(inputs)
        overrides = {'components': {'services': {'growth_rates': [.3, .1]}}}
        base, detail = derive_counterfactual(inputs, overrides)
        first = detail['cashflows'][0]
        self.assertAlmostEqual(first['revenue'], 1160)
        self.assertAlmostEqual(first['gross_profit'], 620)
        self.assertAlmostEqual(first['ebit'], 470)
        self.assertAlmostEqual(base['growth_rates'][0], .16)
        self.assertAlmostEqual(base['ebit_margins'][0], 470 / 1160)
        self.assertEqual(base['wacc'], inputs['scenarios']['base']['wacc'])
        self.assertEqual(base['probability'], inputs['scenarios']['base']['probability'])
        self.assertEqual(detail['operating_overrides'], overrides)
        self.assertEqual(detail['fixed_input_basis']['actuals'], inputs['actuals'])
        self.assertTrue(detail['component_breakdown_derived'])
        self.assertEqual(inputs, original)
        self.assertEqual(derive(inputs)['scenarios']['base']['cashflows'][0]['revenue'], 1130)

    def test_temporary_and_persistent_gross_margin_shocks_have_different_durations(self):
        inputs = plan()
        baseline = derive(inputs)['scenarios']['base']['cashflows']
        _, temporary = derive_counterfactual(inputs, {'components': {'products': {'gross_margins': [.35, .4]}}})
        _, persistent = derive_counterfactual(inputs, {'components': {'products': {'gross_margins': [.35, .35]}}})
        self.assertAlmostEqual(temporary['cashflows'][0]['ebit'], baseline[0]['ebit'] - 38.5)
        self.assertAlmostEqual(temporary['cashflows'][1]['ebit'], baseline[1]['ebit'])
        self.assertAlmostEqual(persistent['cashflows'][1]['ebit'], baseline[1]['ebit'] - 38.5)
        _, higher_rd = derive_counterfactual(inputs, {'expenses': {'research_and_development': [125, 110]}})
        self.assertAlmostEqual(higher_rd['cashflows'][0]['ebit'], baseline[0]['ebit'] - 25)
        self.assertEqual(higher_rd['cashflows'][0]['components'], baseline[0]['components'])

    def test_plan_accepts_coherent_operating_countercase_but_rejects_mixed_paths(self):
        inputs = plan()
        inputs['counterfactuals'] = {'services_growth': {
            'operating_overrides': {'components': {'services': {'growth_rates': [.3, .1]}}}, 'wacc': .08}}
        _, forecasts = apply(inputs)
        basis = forecasts['counterfactual_basis']['services_growth']
        self.assertEqual(basis['forecast_basis'], 'component_operating_override')
        self.assertTrue(basis['component_breakdown_derived'])
        self.assertEqual(basis['overridden_operating_fields'], ['components.services.growth_rates'])
        inputs['counterfactuals']['services_growth']['ebit_margins'] = [.4, .4]
        with self.assertRaisesRegex(ValueError, 'cannot mix'):
            apply(inputs)

    def test_operating_countercase_rejects_new_facts_names_and_invalid_paths(self):
        invalid = [{}, {'actuals': {'revenue': 500}}, {'probability': 1},
                   {'components': {}}, {'expenses': {}},
                   {'components': {'new_product': {'growth_rates': [.1, .1]}}},
                   {'components': {'products': {'base_revenue': 999}}},
                   {'components': {'products': {'source_refs': ['S3']}}},
                   {'components': {'products': {'growth_rates': [.1]}}},
                   {'components': {'products': {'gross_margins': [1.1, .4]}}},
                   {'expenses': {'new_cost': [10, 10]}},
                   {'expenses': {'research_and_development': [-1, 100]}}]
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    derive_counterfactual(plan(), overrides)
        with self.assertRaisesRegex(ValueError, 'existing operating_model'):
            derive_counterfactual({}, {'components': {'products': {'growth_rates': [.1, .1]}}})

    def test_explicit_zero_expenses_and_loss_margins_are_preserved(self):
        inputs = plan()
        expenses = inputs['operating_model']['scenarios']['base']['expenses']
        expenses['selling_general_administrative'] = [0, 0]
        expenses['research_and_development'] = [700, 700]
        first = derive(inputs)['scenarios']['base']['cashflows'][0]
        self.assertEqual(first['expense_breakdown']['selling_general_administrative'], 0)
        self.assertLess(first['ebit'], 0)
        self.assertLess(first['ebit_margin'], 0)

    def test_overflow_and_zero_revenue_denominators_fail_explicitly(self):
        inputs = plan()
        inputs['actuals']['revenue'] = 1e308
        inputs['operating_model']['components'][0]['base_revenue'] = 7e307
        inputs['operating_model']['components'][1]['base_revenue'] = 3e307
        inputs['operating_model']['scenarios']['bear']['components']['products']['growth_rates'] = [3, 3]
        with self.assertRaisesRegex(ValueError, 'finite'):
            derive(inputs)
        inputs = plan()
        inputs['actuals']['revenue'] = 1e-323
        for item in inputs['operating_model']['components']:
            item['base_revenue'] = 5e-324
        for case in inputs['operating_model']['scenarios'].values():
            for component in case['components'].values():
                component['growth_rates'] = [-.95, -.95]
        with self.assertRaisesRegex(ValueError, 'positive'):
            derive(inputs)


if __name__ == '__main__':
    unittest.main()

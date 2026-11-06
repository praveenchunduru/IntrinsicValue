"""Independent financial identities and provenance across underwriting modules."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from investmentideas.pipeline import _validate_plan_sources, valuation_section
from investmentideas.valuation import evaluate, save_valuation


def inputs():
    def record(value, label):
        return {'value':value, 'observed_at':'2026-01-02', 'label':label, 'source_refs':['S1']}
    scenarios = {name:{'probability':probability, 'tax_rate':.2, 'sales_to_capital':2,
                       'terminal_growth':0, 'terminal_roic':.15}
                 for name, probability in [('bear',.25), ('base',.5), ('bull',.25)]}
    operating = {name:{'components':{
        'products':{'growth_rates':[0]*5, 'gross_margins':[.4]*5},
        'services':{'growth_rates':[0]*5, 'gross_margins':[.8]*5}},
        'expenses':{'research_and_development':[100]*5, 'selling_general_administrative':[50]*5}}
        for name in scenarios}
    return {'currency':'USD', 'amount_unit':'currency_units', 'share_unit':'shares',
        'valuation_date':'2026-01-03', 'assumptions_label':'Synthetic forward house assumptions.',
        'actuals':{'revenue':1000, 'net_debt':50, 'other_equity_claims':10, 'shares_outstanding':100},
        'market_price':10, 'source_refs':{key:['S1'] for key in ('revenue','net_debt','other_equity_claims','shares_outstanding','market_price')},
        'input_dates':{'market_price':{'observed_at':'2026-01-02T21:00:00Z'},
                       'shares_outstanding':{'instant':'2026-01-02'}},
        'net_debt_bridge':{'as_of':'2026-01-02', 'debt':80, 'selected_cash_and_nonoperating_investments':30, 'net_debt':50},
        'other_claims_bridge':{'as_of':'2026-01-02', 'reported_finance_lease_liabilities':10},
        'scenarios':scenarios,
        'operating_model':{'currency':'USD', 'amount_unit':'currency_units',
            'assumptions_label':'Forward growth, margins and GAAP expenses are house assumptions.',
            'earnings_mode':'gross_profit_less_expenses',
            'components':[{'name':'products','base_revenue':700,'source_refs':['S1']},
                          {'name':'services','base_revenue':300,'source_refs':['S1']}],
            'scenarios':operating},
        'discount_rate':{'as_of':'2026-01-03', 'currency':'USD','amount_unit':'currency_units',
            'lease_convention':'gaap_operating', 'risk_free_rate':record(.04,'Matched Treasury'),
            'equity_risk_premium':record(.05,'Matched ERP'), 'unlevered_beta':record(1.2,'Sector proxy'),
            'market_equity':record(1000,'Current market equity'), 'financial_debt':record(80,'Book financial debt'),
            'finance_lease_debt':record(10,'Finance leases'), 'pretax_cost_of_debt':record(.06,'Yield proxy'),
            'marginal_tax_rate':record(.25,'Marginal tax assumption')}}


class UnderwritingIntegrationTests(unittest.TestCase):
    def test_detailed_operating_and_rate_inputs_match_independent_perpetuity(self):
        plan=inputs(); original=copy.deepcopy(plan); result=evaluate(plan)
        # GP=700*40%+300*80%=520; EBIT=370; NOPAT=296.
        # CAPM/WACC: equity cost104.05 plus debt cost4.05, capital1090.
        expected=(296/(108.1/1090)-60)/100
        self.assertAlmostEqual(result['weighted_per_share_value'],expected)
        self.assertEqual(result['operating_forecasts']['scenarios']['base']['cashflows'][0]['ebit'],370)
        self.assertEqual(plan,original)
        self.assertTrue(all(item['status'].startswith('reconciled') for item in
                            result['discount_rate_calculation']['valuation_input_reconciliations']))

    def test_driver_case_and_monitoring_use_the_same_computed_value_and_basis(self):
        plan=inputs(); house=evaluate(plan)
        plan['counterfactuals']={'service_expansion':{'operating_overrides':{
            'components':{'services':{'growth_rates':[.1]*5}}},'wacc':.08}}
        plan['monitoring']=[{'name':'Paid services growth','observation':'Annual services revenue',
            'threshold':'At least10% growth','review_by':'2027-01-31','source_refs':['S1'],
            'counterfactual':'service_expansion'}]
        result=evaluate(plan); case=result['counterfactuals']['service_expansion']; row=case['cashflows'][0]
        self.assertAlmostEqual(row['revenue'],1030)
        self.assertAlmostEqual(row['ebit'],394)
        self.assertAlmostEqual(row['fcff'],300.2)
        for field in ('growth_rates','ebit_margins','wacc'):
            self.assertIn(field,case['fixed_input_basis']['changed_base_inputs'])
            self.assertNotIn(field,case['fixed_input_basis']['unchanged_base_inputs'])
        self.assertEqual(case['operating_forecast']['additional_forecast_overrides'],{'wacc':.08})
        trigger=result['monitoring'][0]
        self.assertEqual(trigger['conditional_per_share_value'],case['per_share_value'])
        self.assertAlmostEqual(trigger['value_change']['dollar_delta'],case['per_share_value']-house['scenarios']['base']['per_share_value'])
        self.assertEqual(result['scenarios'],house['scenarios'])
        self.assertEqual(result['weighted_per_share_value'],house['weighted_per_share_value'])

    def test_historical_equity_beta_is_explicitly_unlevered_and_cash_corrected(self):
        plan=inputs();record=plan['discount_rate']['unlevered_beta']
        record.pop('value');record.update(calculation='historical_issuer_cash_corrected',historical_equity_beta=1.2)
        result=evaluate(plan);beta=result['discount_rate_calculation']['provenance']['unlevered_beta']
        # Snapshot D/E=90/1000; cash fraction30/1090.
        expected=(1.2/(1+.75*.09))/(1-30/1090)
        self.assertAlmostEqual(beta['value'],expected)
        self.assertNotIn('value',plan['discount_rate']['unlevered_beta'])
        plan['discount_rate']['unlevered_beta']['value']=99
        with self.assertRaisesRegex(ValueError,'conflicts'):evaluate(plan)

    def test_fixed_exit_cash_return_is_distinct_from_recapitalizing_terminal_value(self):
        plan=inputs();plan['market_price']=60
        plan['actuals'].update(net_debt=0,other_equity_claims=0)
        plan['net_debt_bridge'].update(debt=0,selected_cash_and_nonoperating_investments=0,net_debt=0)
        plan['other_claims_bridge']['reported_finance_lease_liabilities']=0
        for field in ('financial_debt','finance_lease_debt'):plan['discount_rate'][field]['value']=0
        plan['discount_rate']['market_equity']['value']=6000
        result=evaluate(plan);base=result['scenarios']['base']
        # $2.96 annually forever -> value$29.60 at10%; original exit$29.60.
        self.assertAlmostEqual(base['per_share_value'],29.6)
        self.assertAlmostEqual(base['fixed_exit_return_at_model_value']['annual_return_solutions'][0],.1)
        self.assertLess(base['fixed_exit_return_at_market_price']['annual_return_solutions'][0],0)
        self.assertAlmostEqual(sum(base['fixed_exit_return_at_market_price']['cashflows_per_share']),-15.6)
        # A different operation recapitalizes terminal cash at4.9333% to reach$60.
        self.assertAlmostEqual(result['reverse_dcf']['discount_rate']['solutions'][0]['wacc'],2.96/60)

    def test_rate_comparisons_recompute_without_replacing_house_forecasts(self):
        plan=inputs();baseline=evaluate(plan)
        record=copy.deepcopy(plan['discount_rate']['unlevered_beta']);record['value']=1
        plan['calibration_comparisons']=[{'name':'unit_beta','label':'Explicit forward normalization assumption',
                                          'overrides':{'unlevered_beta':record}}]
        result=evaluate(plan);case=result['calibration_comparisons'][0]
        self.assertLess(case['wacc'],baseline['discount_rate_calculation']['applied_wacc'])
        self.assertGreater(case['per_share_value'],baseline['scenarios']['base']['per_share_value'])
        self.assertEqual(result['scenarios'],baseline['scenarios'])
        self.assertEqual(result['weighted_per_share_value'],baseline['weighted_per_share_value'])
        plan['calibration_comparisons'][0]['overrides']={'risk_free_rate':plan['discount_rate']['risk_free_rate']}
        with self.assertRaisesRegex(ValueError,'pair together'):evaluate(plan)

    def test_issuer_beta_tax_comparison_rederives_beta_from_canonical_inputs(self):
        plan=inputs()
        beta=plan['discount_rate']['unlevered_beta']
        beta.pop('value')
        beta.update(calculation='historical_issuer_cash_corrected',historical_equity_beta=1.2)
        baseline=evaluate(plan)
        # Valid duplicate house rates must not constrain the conditional rate.
        for scenario in plan['scenarios'].values():
            scenario['wacc']=baseline['discount_rate_calculation']['applied_wacc']
        tax=copy.deepcopy(plan['discount_rate']['marginal_tax_rate'])
        tax.update(value=.17,label='Conditional marginal tax assumption')
        plan['calibration_comparisons']=[{'name':'lower_tax','label':'Conditional debt shield',
                                          'overrides':{'marginal_tax_rate':tax}}]
        original=copy.deepcopy(plan)
        result=evaluate(plan)
        house=result['discount_rate_calculation']
        changed=result['calibration_comparisons'][0]['discount_rate_calculation']
        expected_beta=(1.2/(1+.83*.09))/(1-30/1090)
        self.assertAlmostEqual(changed['provenance']['unlevered_beta']['value'],expected_beta)
        self.assertNotEqual(changed['provenance']['unlevered_beta']['value'],
                            house['provenance']['unlevered_beta']['value'])
        derived=changed['provenance']['unlevered_beta']['derived_calculation']
        self.assertEqual(derived['inputs']['marginal_tax_rate'],.17)
        self.assertEqual(derived['inputs']['nonoperating_cash'],30)
        self.assertAlmostEqual(changed['relevered_beta'],house['relevered_beta'])
        self.assertAlmostEqual(changed['cost_of_equity'],house['cost_of_equity'])
        self.assertAlmostEqual(changed['applied_wacc']-house['applied_wacc'],
                               (90/1090)*.06*(.25-.17))
        self.assertEqual(changed['provenance']['marginal_tax_rate'],tax)
        self.assertEqual(changed['valuation_input_reconciliations'],
                         house['valuation_input_reconciliations'])
        self.assertEqual(result['scenarios'],baseline['scenarios'])
        self.assertEqual(result['weighted_per_share_value'],baseline['weighted_per_share_value'])
        self.assertEqual(plan,original)
        # An inherited duplicate beta is checked for the house, then rederived
        # for a comparison with different financing assumptions.
        plan['discount_rate']['unlevered_beta']['value']=house['provenance']['unlevered_beta']['value']
        duplicate_original=copy.deepcopy(plan)
        self.assertEqual(evaluate(plan)['calibration_comparisons'],result['calibration_comparisons'])
        self.assertEqual(plan,duplicate_original)

    def test_rate_comparison_materializes_typed_beta_override_and_checks_duplicates(self):
        plan=inputs()
        beta=copy.deepcopy(plan['discount_rate']['unlevered_beta'])
        beta.pop('value')
        beta.update(calculation='historical_issuer_cash_corrected',historical_equity_beta=1.2)
        plan['calibration_comparisons']=[{'name':'issuer_history','label':'Conditional issuer history',
                                          'overrides':{'unlevered_beta':beta}}]
        original=copy.deepcopy(plan)
        result=evaluate(plan)
        changed=result['calibration_comparisons'][0]['discount_rate_calculation']
        self.assertAlmostEqual(changed['provenance']['unlevered_beta']['value'],
                               (1.2/(1+.75*.09))/(1-30/1090))
        self.assertIn('derived_calculation',changed['provenance']['unlevered_beta'])
        self.assertEqual(plan,original)
        beta['value']=99
        with self.assertRaisesRegex(ValueError,'unlevered_beta.value conflicts'):
            evaluate(plan)

    def test_discount_rate_solve_recovers_base_without_calling_it_an_observed_beta(self):
        plan=inputs(); value=evaluate(plan)['scenarios']['base']['per_share_value']
        plan['market_price']=value
        # Reconcile the quote-derived market cap explicitly when repricing.
        plan['discount_rate']['market_equity']['observed_at']='2026-01-01'
        result=evaluate(plan)
        solved=result['reverse_dcf']['discount_rate']
        self.assertEqual(solved['status'],'solved')
        self.assertTrue(any(abs(row['wacc']-result['discount_rate_calculation']['applied_wacc'])<1e-8
                            for row in solved['solutions']))
        self.assertIn('not measured issuer betas',solved['interpretation'])

    def test_saved_canonical_inputs_remain_editable_without_stale_aggregate_copies(self):
        plan=inputs()
        with tempfile.TemporaryDirectory() as folder:
            save_valuation(folder,plan,evaluate(plan))
            edited=json.loads((Path(folder)/'assumptions.json').read_text())
            self.assertNotIn('wacc',edited['scenarios']['base'])
            self.assertNotIn('growth_rates',edited['scenarios']['base'])
            edited['operating_model']['scenarios']['base']['components']['services']['growth_rates'][0]=.1
            self.assertGreater(evaluate(edited)['scenarios']['base']['cashflows'][0]['revenue'],1000)
        self.assertIn('Executable valuation',valuation_section(plan,evaluate(plan)))

    def test_conflicting_dates_leases_or_comparable_financial_records_reject(self):
        mutations=[lambda p:p.update(valuation_date='2026-01-01'),
            lambda p:p['discount_rate']['financial_debt'].update(value=200),
            lambda p:p['discount_rate']['finance_lease_debt'].update(value=900),
            lambda p:p['discount_rate']['market_equity'].update(value=1200),
            lambda p:p['discount_rate'].update(lease_convention='capitalized_operating',ebit_convention='operating_lease_adjusted')]
        for mutate in mutations:
            plan=inputs();mutate(plan)
            with self.subTest(plan=plan),self.assertRaises(ValueError):evaluate(plan)
        plan=inputs();plan['discount_rate']['financial_debt'].update(observed_at='2025-12-31',value=90)
        checks=evaluate(plan)['discount_rate_calculation']['valuation_input_reconciliations']
        self.assertEqual(checks[0]['status'],'not_comparable_dates_or_measurement')

    def test_nested_unknown_source_refs_reject_before_model_calls(self):
        for field in ('operating_model','discount_rate','monitoring'):
            plan=inputs()
            if field=='operating_model':plan[field]['components'][0]['source_refs']=['S404']
            elif field=='discount_rate':plan[field]['risk_free_rate']['source_refs']=['S404']
            else:plan[field]=[{'source_refs':['S404']}]
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'Unknown source'):
                _validate_plan_sources(plan,[{'id':'S1'}])

    def test_monitoring_cannot_fabricate_a_case_or_supply_a_quoted_value(self):
        plan=inputs();plan['monitoring']=[{'name':'Trigger','observation':'Revenue','threshold':'Greater than10%',
             'review_by':'2027-01-31','source_refs':['S1'],'counterfactual':'nonexistent','conditional_per_share_value':999}]
        with self.assertRaisesRegex(ValueError,'computed conditional case'):evaluate(plan)

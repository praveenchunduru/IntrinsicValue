import csv
import json
from pathlib import Path
import tempfile
import unittest

from investmentideas.valuation import evaluate, save_valuation


def plan():
    result = {'currency':'USD', 'amount_unit':'currency_units', 'share_unit':'shares',
              'assumptions_label':'House forecasts; zero other claims explicitly assumed pending verification.',
              'actuals':{'revenue':1_000_000_000, 'net_debt':100_000_000,
                         'other_equity_claims':0, 'shares_outstanding':100_000_000},
              'source_refs':{key:['S1'] for key in ('revenue','net_debt','other_equity_claims','shares_outstanding','market_price')},
              'scenarios':{}}
    for name, growth, margin, probability in [('bear',.02,.15,.25),('base',.05,.20,.5),('bull',.08,.25,.25)]:
        result['scenarios'][name]={'probability':probability,'growth_rates':[growth]*5,
                                  'ebit_margins':[margin]*5,'tax_rate':.25,
                                  'sales_to_capital':2,'wacc':.10,
                                  'terminal_growth':.02,'terminal_roic':.15}
    return result


def audit_plan():
    """Public-case house assumptions reproducing the memo's audited discrepancy."""
    inputs = plan()
    inputs['actuals'].update(revenue=478_834_000_000, net_debt=23_389_000_000,
                            shares_outstanding=14_532_259_000)
    definitions = {
        'bear': ([.04,.04,.03,.03,.03,.02,.02], [.30,.29,.28,.275,.27,.265,.26], .175, 2., .095, .025, .14),
        'base': ([.08,.07,.06,.06,.05,.05,.04], [.32,.315,.315,.31,.31,.305,.30], .17, 2.5, .085, .03, .18),
        'bull': ([.12,.11,.10,.09,.08,.07,.06], [.335,.34,.345,.345,.34,.335,.33], .165, 3., .08, .035, .22)}
    for name, values in definitions.items():
        inputs['scenarios'][name].update(dict(zip(
            ('growth_rates','ebit_margins','tax_rate','sales_to_capital','wacc','terminal_growth','terminal_roic'),values)))
    return inputs


class ValuationTests(unittest.TestCase):
    def test_flat_perpetuity_matches_closed_form(self):
        inputs=plan(); inputs['actuals']['net_debt']=0
        for item in inputs['scenarios'].values():
            item.update(growth_rates=[0]*5, ebit_margins=[.2]*5, terminal_growth=0)
        result=evaluate(inputs)
        # $1B revenue * 20% margin * (1-25%) / 10% WACC = $1.5B.
        self.assertAlmostEqual(result['weighted_per_share_value'],15)
        for item in result['scenarios'].values():
            self.assertAlmostEqual(item['enterprise_value'],1_500_000_000,delta=1e-6)
            self.assertAlmostEqual(item['terminal_reinvestment'],0)

    def test_equity_bridge_subtracts_net_debt_and_claims_once(self):
        inputs=plan()
        reference=evaluate(inputs)['scenarios']['base']
        inputs['actuals']['net_debt']=-50_000_000
        inputs['actuals']['other_equity_claims']=25_000_000
        changed=evaluate(inputs)['scenarios']['base']
        self.assertEqual(reference['enterprise_value'],changed['enterprise_value'])
        self.assertAlmostEqual(changed['per_share_value']-reference['per_share_value'],1.25)
        self.assertAlmostEqual(changed['equity_value'],changed['enterprise_value']+50_000_000-25_000_000)

    def test_terminal_growth_requires_reinvestment(self):
        result=evaluate(plan())['scenarios']['base']
        self.assertAlmostEqual(result['terminal_reinvestment_rate'],.02/.15)
        self.assertAlmostEqual(result['terminal_fcff'],result['terminal_nopat']*(1-.02/.15))
        self.assertGreater(result['terminal_reinvestment'],0)
        self.assertAlmostEqual(result['enterprise_value'],result['pv_forecast_fcff']+result['pv_terminal_value'])

    def test_cashflow_identity_and_loss_tax_treatment(self):
        inputs=plan(); inputs['scenarios']['bear']['ebit_margins']=[-.1,.05,.1,.15,.15]
        result=evaluate(inputs)['scenarios']['bear']
        first=result['cashflows'][0]
        self.assertLess(first['ebit'],0)
        self.assertEqual(first['cash_operating_tax'],0)
        self.assertLess(first['fcff'],0)
        for row in result['cashflows']:
            self.assertAlmostEqual(row['fcff'],row['nopat']-row['net_reinvestment'])
            self.assertAlmostEqual(row['pv_fcff'],row['fcff']*row['discount_factor'])

    def test_negative_equity_value_not_floored(self):
        inputs=plan(); inputs['actuals']['net_debt']=100_000_000_000
        result=evaluate(inputs)
        self.assertLess(result['weighted_per_share_value'],0)
        self.assertLess(result['scenarios']['base']['equity_value'],0)

    def test_higher_wacc_reduces_value_and_higher_margin_increases_it(self):
        inputs=plan(); baseline=evaluate(inputs)['scenarios']['base']['per_share_value']
        inputs['scenarios']['base']['wacc']=.12
        self.assertLess(evaluate(inputs)['scenarios']['base']['per_share_value'],baseline)
        inputs=plan(); inputs['scenarios']['base']['ebit_margins']=[.25]*5
        self.assertGreater(evaluate(inputs)['scenarios']['base']['per_share_value'],baseline)

    def test_sensitivity_contains_base_and_excludes_invalid_denominator(self):
        inputs=plan(); inputs['scenarios']['base'].update(wacc=.025,terminal_growth=.02)
        result=evaluate(inputs)
        self.assertTrue(all(row['wacc']>row['terminal_growth'] for row in result['sensitivity']))
        central=next(row for row in result['sensitivity'] if row['wacc']==.025 and row['terminal_growth']==.02)
        self.assertAlmostEqual(central['per_share_value'],result['scenarios']['base']['per_share_value'])

    def test_reverse_dcf_recovers_known_growth(self):
        inputs=plan(); inputs['market_price']=evaluate(inputs)['scenarios']['base']['per_share_value']
        result=evaluate(inputs)
        self.assertEqual(result['reverse_dcf']['status'],'solved')
        self.assertTrue(any(abs(growth-.05)<1e-7 for growth in result['reverse_dcf']['constant_annual_growth_solutions']))

    def test_reverse_dcf_records_compounding_revenue_and_cashflows(self):
        inputs=plan(); inputs['market_price']=evaluate(inputs)['scenarios']['base']['per_share_value']
        reverse=evaluate(inputs)['reverse_dcf']
        self.assertEqual(len(reverse['solution_projections']),len(reverse['constant_annual_growth_solutions']))
        for growth,projection in zip(reverse['constant_annual_growth_solutions'],reverse['solution_projections']):
            self.assertEqual(projection['constant_annual_growth'],growth)
            self.assertAlmostEqual(projection['per_share_value'],inputs['market_price'],delta=1e-7)
            previous=inputs['actuals']['revenue']
            for year,(row,flow) in enumerate(zip(projection['revenue_path'],projection['cashflows']),1):
                expected=inputs['actuals']['revenue']*(1+growth)**year
                self.assertEqual(row['year'],year)
                self.assertAlmostEqual(row['revenue'],expected,delta=1e-5)
                self.assertAlmostEqual(row['revenue_increment'],expected-previous,delta=1e-5)
                self.assertEqual(row['revenue'],flow['revenue'])
                self.assertAlmostEqual(flow['net_reinvestment'],row['revenue_increment']/2)
                previous=expected
            self.assertGreater(projection['revenue_path'][-1]['revenue_increment'],projection['revenue_path'][0]['revenue_increment'])

    def test_counterfactual_matches_closed_form_without_changing_house_values(self):
        inputs=plan(); baseline=evaluate(inputs)
        overrides={'growth_rates':[0]*5,'ebit_margins':[.4]*5,'wacc':.08,'terminal_growth':0}
        inputs['counterfactuals']={'flat_cashflows':overrides}
        original=json.loads(json.dumps(inputs))
        result=evaluate(inputs); case=result['counterfactuals']['flat_cashflows']
        # Constant $300M annual NOPAT forever / 8%, less $100M debt, /100M shares.
        self.assertAlmostEqual(case['per_share_value'],36.5)
        self.assertAlmostEqual(case['enterprise_value'],3_750_000_000,delta=1e-5)
        self.assertEqual(case['overrides'],overrides)
        self.assertNotIn('probability',case)
        self.assertEqual(case['effective_inputs']['tax_rate'],.25)
        self.assertEqual(case['fixed_input_basis']['actuals'],inputs['actuals'])
        self.assertEqual(case['fixed_input_basis']['unchanged_base_inputs'],{'tax_rate':.25,'sales_to_capital':2.,'terminal_roic':.15})
        self.assertTrue(case['fixed_input_basis']['house_scenarios_and_probabilities_unchanged'])
        self.assertEqual(result['scenarios'],baseline['scenarios'])
        self.assertEqual(result['weighted_per_share_value'],baseline['weighted_per_share_value'])
        self.assertEqual(result['computed_diagnostics'],baseline['computed_diagnostics'])
        self.assertEqual(inputs,original)
        self.assertAlmostEqual(case['terminal_value_fraction'],1/1.08**5)
        self.assertTrue(all(row['revenue_increment']==0 for row in case['revenue_path']))

    def test_explicit_public_case_counterfactual_reproduces_competing_value(self):
        inputs=audit_plan()
        inputs['actuals'].update(net_debt=-56_824_000_000,other_equity_claims=1_456_000_000,
                                shares_outstanding=14_532_182_000)
        inputs['scenarios']['base']['sales_to_capital']=3
        inputs['counterfactuals']={'competing_case':{'growth_rates':[.15]*7,'ebit_margins':[.36]*7,'wacc':.08}}
        result=evaluate(inputs)['counterfactuals']['competing_case']
        self.assertAlmostEqual(result['per_share_value'],342.44040004457236)
        self.assertAlmostEqual(result['cashflows'][-1]['revenue']/1e12,1.2737079594443728)
        self.assertAlmostEqual(result['terminal_value_fraction'],.7746640534032645)
        # The complete unchanged bridge, not just revenue growth, supports this value.
        self.assertEqual(result['net_debt'],-56_824_000_000)
        self.assertEqual(result['other_equity_claims'],1_456_000_000)
        self.assertEqual(result['effective_inputs']['terminal_roic'],.18)

    def test_counterfactuals_reject_unbounded_or_invalid_overrides(self):
        invalid=[None,[],{'case':{}},{'case':{'probability':1}},
                 {'case':{'actuals':{'net_debt':0}}},{'case':{'unknown':1}},
                 {'bad name':{'wacc':.08}},{'x'*65:{'wacc':.08}},
                 {f'case{i}':{'wacc':.08} for i in range(9)},
                 {'case':{'growth_rates':[.1]*6,'ebit_margins':[.2]*6}},
                 {'case':{'growth_rates':[.1]*4}},
                 {'case':{'ebit_margins':[.2]*4}},
                 {'case':{'wacc':.02}},{'case':{'terminal_roic':0}},
                 {'case':{'sales_to_capital':0}},{'case':{'tax_rate':-.01}},
                 {'case':{'wacc':float('nan')}}]
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                inputs=plan(); inputs['counterfactuals']=overrides
                with self.assertRaises(ValueError):
                    evaluate(inputs)

    def test_missing_unknown_and_inconsistent_units_are_rejected(self):
        for missing in ['revenue','net_debt','shares_outstanding','other_equity_claims']:
            inputs=plan(); del inputs['actuals'][missing]
            with self.assertRaisesRegex(ValueError,'Missing'):
                evaluate(inputs)
        for unit in ['millions','USD millions',None]:
            inputs=plan(); inputs['amount_unit']=unit
            with self.assertRaisesRegex(ValueError,'amount_unit'):
                evaluate(inputs)
        inputs=plan(); inputs['source_refs']['net_debt']=[]
        with self.assertRaisesRegex(ValueError,'source_refs.net_debt'):
            evaluate(inputs)

    def test_invalid_economic_assumptions_are_rejected(self):
        for key,value in [('wacc',.02),('terminal_roic',0),('sales_to_capital',0),('tax_rate',-.1),('probability',-.1)]:
            inputs=plan(); inputs['scenarios']['base'][key]=value
            with self.assertRaises(ValueError):
                evaluate(inputs)
        inputs=plan(); inputs['scenarios']['bull']['probability']=.5
        with self.assertRaisesRegex(ValueError,'sum to one'):
            evaluate(inputs)
        inputs=plan(); inputs['actuals']['shares_outstanding']=0
        with self.assertRaises(ValueError):
            evaluate(inputs)
        for value in [True,float('nan'),float('inf'),10**400,None]:
            inputs=plan(); inputs['actuals']['net_debt']=value
            with self.assertRaises(ValueError):
                evaluate(inputs)


    def test_wacc_diagnostic_reproduces_actual_24_dollar_effect(self):
        result=evaluate(audit_plan())
        self.assertAlmostEqual(result['weighted_per_share_value'],163.86094905235066)
        diagnosis=result['computed_diagnostics']['base_wacc']
        self.assertAlmostEqual(diagnosis['baseline_per_share_value'],156.57848046332927)
        raised=next(row for row in diagnosis['changes'] if row['wacc_change_percentage_points']==1)
        self.assertEqual(raised['status'],'calculated')
        self.assertEqual(raised['wacc'],.095)
        self.assertAlmostEqual(raised['per_share_value'],132.5839490183144)
        self.assertAlmostEqual(raised['dollar_delta'],-23.99453144501487)
        self.assertLess(raised['percent_delta'],-15)
        self.assertGreater(raised['percent_delta'],-16)
        # This directly catches the prior memo's erroneous $6–7 assertion.
        self.assertGreater(abs(raised['dollar_delta']),20)

    def test_uniform_tax_changes_all_scenarios_without_changing_house_plan(self):
        inputs=audit_plan(); original=json.loads(json.dumps(inputs))
        diagnosis=evaluate(inputs)['computed_diagnostics']['uniform_tax']
        twenty,twenty_four=diagnosis['changes']
        self.assertEqual((twenty['tax_rate'],twenty_four['tax_rate']),(.20,.24))
        self.assertAlmostEqual(twenty['weighted_per_share_value'],157.479088046242)
        self.assertAlmostEqual(twenty['percent_delta'],-3.8946808516712395)
        self.assertAlmostEqual(twenty_four['weighted_per_share_value'],149.28579532036224)
        self.assertAlmostEqual(twenty_four['percent_delta'],-8.894830535451081)
        self.assertEqual(inputs,original)
        self.assertIn('probability weights',diagnosis['fixed_assumptions'])
        self.assertEqual(set(twenty['scenario_per_share_values']),{'bear','base','bull'})

    def test_terminal_transition_exposes_investment_step_without_changing_values(self):
        result=evaluate(audit_plan())
        base=result['computed_diagnostics']['terminal_transition']['base']
        self.assertAlmostEqual(base['implied_forecast_incremental_roic'],.6225)
        self.assertEqual(base['terminal_roic'],.18)
        self.assertAlmostEqual(base['equivalent_terminal_sales_to_capital'],.18/(.30*.83*1.03))
        self.assertAlmostEqual(base['terminal_reinvestment_if_forecast_sales_to_capital_continued']/1e9,8.554555090271924)
        self.assertAlmostEqual(base['terminal_reinvestment']/1e9,30.47203811113935)
        self.assertAlmostEqual(base['reinvestment_step_vs_continuing_forecast_sales_to_capital']/1e9,21.91748302086743)
        self.assertLess(base['terminal_fcff'],base['last_forecast_fcff'])
        self.assertLess(base['fcff_step_percent'],0)
        self.assertGreater(base['alternative_per_share_value_if_forecast_sales_to_capital_continued'],156.57848046332927)
        self.assertAlmostEqual(result['scenarios']['base']['per_share_value'],156.57848046332927)
        self.assertIn('not observed market inputs',result['computed_diagnostics']['basis'])

    def test_matching_terminal_capital_efficiency_has_no_reinvestment_step(self):
        inputs=plan()
        for case in inputs['scenarios'].values():
            case.update(ebit_margins=[.2]*5,tax_rate=.25,sales_to_capital=2,terminal_roic=.3*1.02)
        result=evaluate(inputs)
        for name,row in result['computed_diagnostics']['terminal_transition'].items():
            self.assertAlmostEqual(row['reinvestment_step_vs_continuing_forecast_sales_to_capital'],0,delta=1e-6)
            self.assertAlmostEqual(row['alternative_per_share_value_if_forecast_sales_to_capital_continued'],result['scenarios'][name]['per_share_value'])

    def test_invalid_wacc_alternative_and_zero_baseline_are_explicit(self):
        inputs=plan(); inputs['scenarios']['base'].update(wacc=.025,terminal_growth=.02)
        row=evaluate(inputs)['computed_diagnostics']['base_wacc']['changes'][0]
        self.assertEqual(row['status'],'not_calculated')
        self.assertIsNone(row['per_share_value'])
        self.assertIsNone(row['percent_delta'])
        inputs=plan(); inputs['actuals']['net_debt']=evaluate(inputs)['scenarios']['base']['enterprise_value']
        result=evaluate(inputs)
        self.assertEqual(result['scenarios']['base']['per_share_value'],0)
        for row in result['computed_diagnostics']['base_wacc']['changes']:
            self.assertIsNone(row['percent_delta'])
        json.dumps(result,allow_nan=False)


    def test_saving_keeps_assumptions_editable_and_results_recomputable(self):
        inputs=plan(); result=evaluate(inputs)
        with tempfile.TemporaryDirectory() as folder:
            paths=save_valuation(folder,inputs,result)
            saved=json.loads(Path(paths['assumptions.json']).read_text())
            self.assertEqual(evaluate(saved),result)
            with Path(paths['cashflows.csv']).open() as stream:
                rows=list(csv.DictReader(stream))
            self.assertEqual(len(rows),15)
            self.assertEqual(set(row['scenario'] for row in rows),{'bear','base','bull'})
            self.assertTrue(Path(paths['sensitivity.csv']).read_text().startswith('wacc,'))
        self.assertEqual(inputs,plan())


if __name__=='__main__':
    unittest.main()

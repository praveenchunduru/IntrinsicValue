import copy
from datetime import datetime
import json
import unittest

from investmentideas.inputs import canonical_reported_inputs, pin_reported_inputs

BALANCE_DATE = '2026-09-26'


def fact(concept, value, *, instant=BALANCE_DATE, start=None, end=None, unit='iso4217:USD'):
    return {'concept':concept,'normalized_value':value,'normalized_decimal':str(value),
            'context_ref':'balance' if instant else 'annual', 'instant':instant,
            'start_date':start,'end_date':end,'unit':unit,'entity_identifier':'0000320193',
            'parse_status':'parsed','is_nil':False,'dimensions':{}}


def sources():
    facts = [fact('us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax',478834000000,
                  instant=None,start='2025-09-28',end=BALANCE_DATE),
             fact('us-gaap:CashAndCashEquivalentsAtCarryingValue',36305000000),
             fact('us-gaap:MarketableSecuritiesCurrent',20417000000),
             fact('us-gaap:MarketableSecuritiesNoncurrent',80213000000),
             fact('us-gaap:CommercialPaper',1997000000),
             fact('us-gaap:LongTermDebtCurrent',9906000000),
             fact('us-gaap:LongTermDebtNoncurrent',68208000000),
             fact('us-gaap:FinanceLeaseLiability',1456000000),
             fact('us-gaap:FinanceLeaseLiabilityCurrent',700000000),
             fact('us-gaap:FinanceLeaseLiabilityNoncurrent',756000000),
             fact('us-gaap:OperatingLeaseLiability',12912000000),
             fact('us-gaap:CommonStockSharesOutstanding',14532259000,unit='xbrli:shares'),
             fact('dei:EntityCommonStockSharesOutstanding',14532182000,instant='2026-10-23',unit='xbrli:shares'),
             fact('us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding',14720306000,
                  instant=None,start='2025-09-28',end=BALANCE_DATE,unit='xbrli:shares')]
    return [{'id':'S19','kind':'regulatory_primary','data':{'metadata':{
        'cik':320193,'form':'10-K','reportDate':BALANCE_DATE,'filingDate':'2026-11-03'},'inline_facts':facts}},
        {'id':'S1','kind':'market_snapshot','data':{
            'currency':'USD','regularMarketPrice':344.17,
            'regularMarketTime':int(datetime.fromisoformat('2026-11-05T21:00:01+00:00').timestamp()),
            'quote_observed_at_utc':'2026-11-05T21:00:01+00:00'}}]


def bad_plan():
    return {'currency':'USD','valuation_date':'2026-11-06','amount_unit':'currency_units','share_unit':'shares',
            'assumptions_label':'House forecasts; taxes are assumptions',
            'actuals':{'revenue':478834000000,'net_debt':-56627000000,
                       'other_equity_claims':1456000000,'shares_outstanding':14532259000},
            'source_refs':{field:['S2'] for field in ['revenue','net_debt','other_equity_claims','shares_outstanding']},
            'market_price':350,
            'net_debt_bridge':{'debt':81567000000,'selected_cash_and_nonoperating_investments':138194000000,
                               'explanation':'Included finance leases in debt plus an invented $1.259B cash rounding residual'},
            'scenarios':{'base':{'wacc':.095,'probability':.5,'growth_rates':[.07,.08],
                                 'terminal_growth':.03,'terminal_roic':.18},
                         'bear':{'probability':.25},'bull':{'probability':.25}}}


def find(items, concept):
    return next(item for item in items[0]['data']['inline_facts'] if item['concept']==concept)


class ReportedInputTests(unittest.TestCase):
    def test_direct_regulatory_filing_kind_is_supported(self):
        records=sources()
        records[0]['kind']='sec_filing'
        records[0]['source_tier']='regulatory_primary'
        self.assertTrue(canonical_reported_inputs(records)['coverage']['complete'])

    def test_corrects_actual_bad_bridge_and_finance_lease_double_count(self):
        original=bad_plan()
        before=copy.deepcopy(original)
        corrected,changes=pin_reported_inputs(original,sources())
        self.assertEqual(original,before)
        self.assertEqual(corrected['scenarios'],original['scenarios'])
        self.assertEqual(corrected['assumptions_label'],original['assumptions_label'])
        self.assertEqual(corrected['actuals'],{'revenue':478834000000,'net_debt':-56824000000,
                                            'other_equity_claims':1456000000,'shares_outstanding':14532182000})
        bridge=corrected['net_debt_bridge']
        self.assertEqual(bridge['debt'],80111000000)
        self.assertEqual(bridge['selected_cash_and_nonoperating_investments'],136935000000)
        self.assertEqual(sum(item['value'] for item in bridge['cash_components'].values()),136935000000)
        self.assertEqual(sum(item['value'] for item in bridge['debt_components'].values()),80111000000)
        self.assertNotIn('finance_leases',bridge['debt_components'])
        claims=corrected['other_claims_bridge']
        self.assertEqual(claims['reported_finance_lease_liabilities'],1456000000)
        self.assertFalse(claims['finance_leases_included_in_net_debt'])
        self.assertEqual(claims['additional_unidentified_claims'],0)
        self.assertIn('not a reported fact',claims['additional_unidentified_claims_basis'])
        self.assertIn('Explicit house modeling policy',bridge['cash_classification_policy'])
        self.assertNotIn('1259000000',json.dumps(bridge))
        self.assertTrue(corrected['reported_inputs']['coverage']['complete'])
        self.assertEqual(corrected['source_refs']['net_debt'],['S19'])
        self.assertEqual({r['field'] for r in changes}, {'actuals.net_debt','actuals.shares_outstanding','market_price'})
        repeated,repeated_changes=pin_reported_inputs(corrected,sources())
        self.assertEqual(repeated,corrected)
        self.assertEqual(repeated_changes,[])

    def test_latest_cover_shares_and_dated_quote_are_pinned_not_averages(self):
        corrected,_=pin_reported_inputs(bad_plan(),sources())
        self.assertEqual(corrected['actuals']['shares_outstanding'],14532182000)
        self.assertEqual(corrected['input_dates']['shares_outstanding']['instant'],'2026-10-23')
        self.assertEqual(corrected['market_price'],344.17)
        self.assertEqual(corrected['input_dates']['market_price']['observed_at'],'2026-11-05T21:00:01+00:00')
        self.assertEqual(corrected['valuation_date'],'2026-11-06')

    def test_no_primary_facts_do_not_create_reported_zeros(self):
        report=canonical_reported_inputs([],currency='USD',as_of='2026-11-06')
        self.assertEqual(report['actuals'],{})
        self.assertEqual(report['coverage']['status'],'unavailable')
        self.assertFalse(report['coverage']['complete'])
        corrected,changes=pin_reported_inputs(bad_plan(),[])
        self.assertEqual(corrected['actuals'],bad_plan()['actuals'])
        self.assertEqual(changes,[])
        self.assertFalse(corrected['reported_inputs']['coverage']['complete'])

    def test_missing_component_wrong_currency_or_wrong_date_never_becomes_zero(self):
        for case in ('missing','currency','date','dimension'):
            data=sources()
            target=find(data,'us-gaap:MarketableSecuritiesNoncurrent')
            if case=='missing':
                data[0]['data']['inline_facts'].remove(target)
            elif case=='currency':
                target['unit']='iso4217:EUR'
            elif case=='date':
                target['instant']='2025-09-27'
            else:
                target['dimensions']={'segment':'overseas'}
            with self.subTest(case=case):
                report=canonical_reported_inputs(data,currency='USD',as_of='2026-11-06')
                self.assertNotIn('net_debt',report['actuals'])
                self.assertEqual(report['net_debt_bridge'],{})
                self.assertIn('net_debt',report['coverage']['missing_fields'])

    def test_conflicting_same_period_values_are_not_arbitrarily_selected(self):
        data=sources()
        data[0]['data']['inline_facts'].append(fact('us-gaap:MarketableSecuritiesCurrent',20418000000))
        report=canonical_reported_inputs(data,currency='USD',as_of='2026-11-06')
        self.assertNotIn('net_debt',report['actuals'])
        self.assertEqual(report['coverage']['status'],'conflicted')
        self.assertTrue(report['coverage']['conflicts'])

    def test_missing_finance_leases_are_unknown_and_conflicting_lease_totals_fail(self):
        data=sources()
        data[0]['data']['inline_facts']=[f for f in data[0]['data']['inline_facts'] if not f['concept'].startswith('us-gaap:FinanceLease')]
        report=canonical_reported_inputs(data,currency='USD',as_of='2026-11-06')
        self.assertNotIn('other_equity_claims',report['actuals'])
        self.assertEqual(report['other_claims_bridge'],{})
        data=sources()
        find(data,'us-gaap:FinanceLeaseLiabilityCurrent').update(normalized_value=701000000,normalized_decimal='701000000')
        report=canonical_reported_inputs(data,currency='USD',as_of='2026-11-06')
        self.assertNotIn('other_equity_claims',report['actuals'])
        self.assertEqual(report['coverage']['status'],'conflicted')

    def test_average_or_issued_shares_are_not_denominator_fallbacks(self):
        data=sources()
        data[0]['data']['inline_facts']=[f for f in data[0]['data']['inline_facts'] if f['concept'] not in
                                      {'dei:EntityCommonStockSharesOutstanding','us-gaap:CommonStockSharesOutstanding'}]
        data[0]['data']['inline_facts'].append(fact('us-gaap:CommonStockSharesIssued',999999,unit='xbrli:shares'))
        report=canonical_reported_inputs(data,currency='USD',as_of='2026-11-06')
        self.assertNotIn('shares_outstanding',report['actuals'])

    def test_secondary_inline_facts_are_not_treated_as_primary(self):
        data=sources();data[0]['kind']='financial_timeseries'
        report=canonical_reported_inputs(data,currency='USD',as_of='2026-11-06')
        self.assertEqual(report['actuals'],{})
        self.assertEqual(report['market_price'],344.17)

    def test_wrong_issuer_revenue_quarters_and_future_quote_are_refused(self):
        data=sources()
        find(data,'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax')['start_date']='2026-06-28'
        find(data,'us-gaap:CashAndCashEquivalentsAtCarryingValue')['entity_identifier']='1234'
        data[1]['data']['regularMarketTime']=int(datetime.fromisoformat('2099-01-01T21:00:01+00:00').timestamp())
        report=canonical_reported_inputs(data,currency='USD',as_of='2026-11-06')
        self.assertNotIn('revenue',report['actuals'])
        self.assertNotIn('net_debt',report['actuals'])
        self.assertNotIn('market_price',report)

    def test_conflicting_primary_annual_revenue_does_not_choose_older_or_arbitrary(self):
        data=sources()
        duplicate=copy.deepcopy(find(data,'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax'))
        duplicate.update(normalized_value=479000000000,normalized_decimal='479000000000')
        data[0]['data']['inline_facts'].append(duplicate)
        report=canonical_reported_inputs(data,currency='USD',as_of='2026-11-06')
        self.assertNotIn('revenue',report['actuals'])
        self.assertNotIn('net_debt',report['actuals'])
        self.assertEqual(report['coverage']['status'],'conflicted')


if __name__ == '__main__':
    unittest.main()

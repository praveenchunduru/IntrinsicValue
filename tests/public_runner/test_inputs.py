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
    # Synthetic reviewed disclosure: taxonomy alone cannot establish that the
    # commercial-paper subset exhausts an issuer's short-term financing.
    disclosure = 'Commercial paper is the sole category of short-term financing in this synthetic balance sheet.'
    return [{'id':'S19','kind':'regulatory_primary','data':{'metadata':{
        'cik':320193,'form':'10-K','reportDate':BALANCE_DATE,'filingDate':'2026-11-03'},'inline_facts':facts,
        'excerpts':[disclosure], 'reported_input_classifications':{'short_term_financing_total':{
            'reviewed':True,'concept':'us-gaap:CommercialPaper','instant':BALANCE_DATE,'source_excerpt':disclosure}}}},
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
    def alternate(self, *extra, remove=()):
        records = sources()
        records[0]['data'].pop('reported_input_classifications')
        retained = records[0]['data']['inline_facts']
        retained[:] = [item for item in retained if item['concept'].split(':')[-1] not in set(remove)]
        retained.extend(fact('us-gaap:' + concept, value) for concept, value in extra)
        return records

    def test_total_long_term_debt_is_not_noncurrent_debt_and_paper_is_subset(self):
        data = self.alternate(('ShortTermBorrowings', 3000000000), ('LongTermDebt', 78114000000))
        report = canonical_reported_inputs(data, as_of='2026-11-06')
        self.assertTrue(report['coverage']['complete'])
        self.assertEqual(report['net_debt_bridge']['debt'], 81114000000)
        self.assertEqual(set(report['net_debt_bridge']['debt_components']), {'short_term_borrowings', 'term_debt'})
        self.assertEqual(report['actuals']['net_debt'], -55821000000)

    def test_current_and_noncurrent_term_alternative_with_explicit_zero_short_term(self):
        data = self.alternate(('ShortTermBorrowings', 0), remove=('CommercialPaper',))
        report = canonical_reported_inputs(data, as_of='2026-11-06')
        self.assertTrue(report['coverage']['complete'])
        self.assertEqual(report['net_debt_bridge']['debt'], 78114000000)
        self.assertEqual(report['net_debt_bridge']['debt_components']['short_term_borrowings']['value'], 0)
        data[0]['data']['inline_facts'].remove(find(data, 'us-gaap:ShortTermBorrowings'))
        missing = canonical_reported_inputs(data, as_of='2026-11-06')
        self.assertNotIn('net_debt', missing['actuals'])

    def test_commercial_paper_alone_needs_primary_reviewed_total_scope(self):
        data = self.alternate()
        report = canonical_reported_inputs(data, as_of='2026-11-06')
        self.assertNotIn('net_debt', report['actuals'])
        self.assertIn('commercial paper alone', ' '.join(report['coverage']['notes']))
        reviewed = canonical_reported_inputs(sources(), as_of='2026-11-06')
        scope = reviewed['net_debt_bridge']['debt_components']['short_term_borrowings']['classification_evidence']
        self.assertEqual(scope['source_id'], 'S19')
        for change in ('unreviewed', 'invented_excerpt', 'wrong_date', 'different_concept'):
            data = sources()
            declaration = data[0]['data']['reported_input_classifications']['short_term_financing_total']
            if change == 'unreviewed':
                declaration['reviewed'] = False
            elif change == 'invented_excerpt':
                declaration['source_excerpt'] = 'This quotation does not exist in the supplied primary source.'
            elif change == 'wrong_date':
                declaration['instant'] = '2025-09-27'
            else:
                declaration['concept'] = 'us-gaap:NotesPayableCurrent'
            with self.subTest(change=change):
                self.assertNotIn('net_debt', canonical_reported_inputs(data)['actuals'])

    def test_combined_total_includes_finance_leases_once_even_without_separate_lease_tag(self):
        data = self.alternate(('DebtAndCapitalLeaseObligations', 81567000000),
                              remove=('CommercialPaper', 'LongTermDebtCurrent', 'LongTermDebtNoncurrent',
                                      'FinanceLeaseLiability', 'FinanceLeaseLiabilityCurrent', 'FinanceLeaseLiabilityNoncurrent'))
        report = canonical_reported_inputs(data, as_of='2026-11-06')
        self.assertTrue(report['coverage']['complete'])
        self.assertEqual(report['actuals']['net_debt'], -55368000000)
        self.assertEqual(report['actuals']['other_equity_claims'], 0)
        self.assertIsNone(report['other_claims_bridge']['reported_finance_lease_liabilities'])
        self.assertTrue(report['other_claims_bridge']['finance_leases_included_in_net_debt'])
        self.assertIn('covered by', report['other_claims_bridge']['finance_lease_amount_basis'])

    def test_total_financial_debt_alternative_does_not_require_short_term_components(self):
        data = self.alternate(('DebtLongtermAndShorttermCombinedAmount', 80111000000),
                              remove=('CommercialPaper', 'LongTermDebtCurrent', 'LongTermDebtNoncurrent'))
        report = canonical_reported_inputs(data, as_of='2026-11-06')
        self.assertTrue(report['coverage']['complete'])
        self.assertEqual(report['actuals']['net_debt'], -56824000000)
        self.assertEqual(report['actuals']['other_equity_claims'], 1456000000)

    def test_primary_total_debt_disclosure_can_classify_a_long_term_debt_tag(self):
        for method in ('primary_debt_table_total', 'manual_primary_review'):
            data = self.alternate(('LongTermDebt', 78114000000), remove=('CommercialPaper',))
            quote = ('NOTE DEBT (in millions) 2026 2025 Total debt 78,114 90,678. '
                     'No short-term borrowings or commercial paper were outstanding as of September 26, 2026.')
            data[0]['data']['excerpts'].append(quote)
            data[0]['data']['reported_input_classifications'] = {'financial_debt_total': {
                'method': method, 'reviewed': method == 'manual_primary_review',
                'scope_evidence_type': 'dated_no_short_term_financing',
                'concept': 'us-gaap:LongTermDebt', 'instant': BALANCE_DATE,
                'normalized_decimal': '78114000000', 'source_excerpt': quote}}
            with self.subTest(method=method):
                report = canonical_reported_inputs(data, as_of='2026-11-06')
                self.assertTrue(report['coverage']['complete'])
                self.assertEqual(report['net_debt_bridge']['debt'], 78114000000)
                component = report['net_debt_bridge']['debt_components']['financial_debt']
                self.assertEqual(component['classification_evidence']['method'], method)
                self.assertEqual(component['facts'][0]['concept'], 'us-gaap:LongTermDebt')
                self.assertNotIn('short_term_borrowings', report['net_debt_bridge']['debt_components'])

    def test_classified_total_debt_needs_matching_primary_value_date_and_scope_evidence(self):
        for change in ('value', 'date', 'method', 'excerpt', 'no_label', 'no_year', 'unreviewed_manual'):
            data = self.alternate(('LongTermDebt', 78114000000), remove=('CommercialPaper',))
            quote = ('NOTE DEBT (in millions) 2026 2025 Total debt 78,114 90,678. '
                     'No short-term borrowings were outstanding as of September 26, 2026.')
            declaration = {'method': 'primary_debt_table_total', 'concept': 'us-gaap:LongTermDebt',
                           'scope_evidence_type': 'dated_no_short_term_financing',
                           'instant': BALANCE_DATE, 'normalized_decimal': '78114000000', 'source_excerpt': quote}
            if change == 'value':
                declaration['normalized_decimal'] = '78114000001'
            elif change == 'date':
                declaration['instant'] = '2025-09-27'
            elif change == 'method':
                declaration['method'] = 'llm_inferred'
            elif change == 'excerpt':
                declaration['source_excerpt'] = '2026 Total debt invented excerpt not present in the primary source.'
            elif change == 'no_label':
                quote = declaration['source_excerpt'] = '2026 2025 Some unrelated annual debt commentary.'
            elif change == 'no_year':
                quote = declaration['source_excerpt'] = 'NOTE DEBT (in millions) Total debt 78,114 90,678.'
            else:
                declaration['method'] = 'manual_primary_review'
            data[0]['data']['excerpts'].append(quote)
            data[0]['data']['reported_input_classifications'] = {'financial_debt_total': declaration}
            with self.subTest(change=change):
                self.assertNotIn('net_debt', canonical_reported_inputs(data)['actuals'])

    def test_reviewed_flags_do_not_expand_a_hard_long_term_only_debt_scope(self):
        for method in ('primary_debt_table_total', 'manual_primary_review'):
            data = self.alternate(('LongTermDebt', 78114000000), remove=('CommercialPaper',))
            quote = 'Components of long-term debt (in millions) 2026 2025 Total debt 78,114 90,678.'
            data[0]['data']['excerpts'].append(quote)
            data[0]['data']['reported_input_classifications'] = {'financial_debt_total': {
                'method': method, 'reviewed': True, 'concept': 'us-gaap:LongTermDebt', 'instant': BALANCE_DATE,
                'normalized_decimal': '78114000000', 'source_excerpt': quote}}
            with self.subTest(method=method):
                self.assertNotIn('net_debt', canonical_reported_inputs(data)['actuals'])

    def test_explicit_disjoint_financing_categories_support_total_scope(self):
        data = self.alternate(('LongTermDebt', 78114000000), remove=('CommercialPaper',))
        quote = 'NOTE DEBT 2026 2025 Short-term borrowings $0 $0 Long-term debt $78,114 $90,678 Total debt $78,114 $90,678.'
        data[0]['data']['excerpts'].append(quote)
        data[0]['data']['reported_input_classifications'] = {'financial_debt_total': {
            'method': 'primary_debt_table_total', 'scope_evidence_type': 'disjoint_debt_categories',
            'concept': 'us-gaap:LongTermDebt', 'instant': BALANCE_DATE,
            'normalized_decimal': '78114000000', 'source_excerpt': quote}}
        report = canonical_reported_inputs(data)
        self.assertTrue(report['coverage']['complete'])
        self.assertEqual(report['net_debt_bridge']['debt'], 78114000000)

    def test_current_debt_plus_unsuffixed_noncurrent_combined_debt(self):
        data = self.alternate(('DebtCurrent', 12603000000),
                              ('LongTermDebtAndCapitalLeaseObligations', 68964000000),
                              remove=('CommercialPaper', 'LongTermDebtCurrent', 'LongTermDebtNoncurrent'))
        report = canonical_reported_inputs(data, as_of='2026-11-06')
        self.assertTrue(report['coverage']['complete'])
        self.assertEqual(report['net_debt_bridge']['debt'], 81567000000)
        self.assertEqual(report['actuals']['other_equity_claims'], 0)

    def test_short_term_plus_combined_long_term_total_or_parts(self):
        for total in (False, True):
            additions = [('ShortTermBorrowings', 1997000000),
                         ('LongTermDebtAndCapitalLeaseObligationsCurrent', 10606000000),
                         ('LongTermDebtAndCapitalLeaseObligations', 68964000000)]
            if total:
                additions.append(('LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities', 79570000000))
            data = self.alternate(*additions, remove=('LongTermDebtCurrent', 'LongTermDebtNoncurrent'))
            with self.subTest(total=total):
                report = canonical_reported_inputs(data, as_of='2026-11-06')
                self.assertTrue(report['coverage']['complete'])
                self.assertEqual(report['net_debt_bridge']['debt'], 81567000000)
                self.assertEqual(report['actuals']['other_equity_claims'], 0)

    def test_complete_alternatives_reconcile_and_conflicting_totals_fail(self):
        for concept, amount in (('LongTermDebt', 78114000001),
                                ('DebtAndCapitalLeaseObligations', 81567000001),
                                ('ShortTermBorrowings', 1000000000)):
            data = self.alternate(('ShortTermBorrowings', 1997000000), (concept, amount))
            with self.subTest(concept=concept):
                report = canonical_reported_inputs(data, as_of='2026-11-06')
                self.assertNotIn('net_debt', report['actuals'])
                self.assertEqual(report['coverage']['status'], 'conflicted')

    def test_inclusive_debt_totals_do_not_hide_conflicting_partial_subsets(self):
        for extra in (
                [('DebtAndCapitalLeaseObligations', 1)],
                [('DebtCurrent', 1), ('LongTermDebtAndCapitalLeaseObligations', 68964000000)],
                [('DebtAndCapitalLeaseObligations', 81567000000),
                 ('LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities', 70000000000)],
                [('DebtAndCapitalLeaseObligations', 1000000)],
                [('DebtAndCapitalLeaseObligations', 80111000001),
                 ('DebtLongtermAndShorttermCombinedAmount', 80111000000)]):
            data = self.alternate(*extra)
            with self.subTest(extra=extra):
                report = canonical_reported_inputs(data, as_of='2026-11-06')
                self.assertNotIn('net_debt', report['actuals'])
                self.assertEqual(report['coverage']['status'], 'conflicted')

    def test_cash_subtotals_replace_parts_and_long_term_strategic_investments_are_excluded(self):
        data = self.alternate(('ShortTermBorrowings', 1997000000),
                              ('CashCashEquivalentsAndShortTermInvestments', 56722000000),
                              ('ShortTermInvestments', 20417000000), ('LongTermInvestments', 900000000000))
        report = canonical_reported_inputs(data, as_of='2026-11-06')
        self.assertTrue(report['coverage']['complete'])
        self.assertEqual(report['net_debt_bridge']['selected_cash_and_nonoperating_investments'], 136935000000)
        self.assertEqual(set(report['net_debt_bridge']['cash_components']),
                         {'cash_and_short_term_investments', 'noncurrent_marketable_securities'})

    def test_total_marketable_securities_is_not_added_to_its_parts(self):
        data = self.alternate(('ShortTermBorrowings', 1997000000), ('MarketableSecurities', 100630000000))
        report = canonical_reported_inputs(data, as_of='2026-11-06')
        self.assertEqual(report['net_debt_bridge']['selected_cash_and_nonoperating_investments'], 136935000000)
        self.assertEqual(set(report['net_debt_bridge']['cash_components']),
                         {'cash_and_cash_equivalents', 'marketable_securities'})
        find(data, 'us-gaap:MarketableSecurities')['normalized_value'] += 1
        find(data, 'us-gaap:MarketableSecurities')['normalized_decimal'] = '100630000001'
        self.assertNotIn('net_debt', canonical_reported_inputs(data)['actuals'])

    def test_omitted_investments_are_explicitly_excluded_by_selected_cash_policy(self):
        data = self.alternate(('ShortTermBorrowings', 1997000000),
                              remove=('MarketableSecuritiesNoncurrent', 'MarketableSecuritiesCurrent'))
        report = canonical_reported_inputs(data, as_of='2026-11-06')
        self.assertTrue(report['coverage']['complete'])
        self.assertEqual(report['net_debt_bridge']['selected_cash_and_nonoperating_investments'], 36305000000)
        self.assertIn('noncurrent_marketable_securities', report['net_debt_bridge']['cash_coverage']['missing_investment_categories'])
        self.assertIn('not reported as zero', report['net_debt_bridge']['cash_classification_policy'])

    def test_selected_cash_subtotal_does_not_hide_an_impossible_marketable_subset(self):
        data = self.alternate(('ShortTermBorrowings', 1997000000), ('ShortTermInvestments', 1))
        report = canonical_reported_inputs(data, as_of='2026-11-06')
        self.assertNotIn('net_debt', report['actuals'])
        self.assertEqual(report['coverage']['status'], 'conflicted')

    def test_aggregate_liability_alternative_uses_exact_anchor_currency_and_issuer(self):
        for change in ('date', 'currency', 'dimension', 'issuer'):
            data = self.alternate(('DebtAndCapitalLeaseObligations', 81567000000),
                                  remove=('CommercialPaper', 'LongTermDebtCurrent', 'LongTermDebtNoncurrent'))
            total = find(data, 'us-gaap:DebtAndCapitalLeaseObligations')
            if change == 'date':
                total['instant'] = '2025-09-27'
            elif change == 'currency':
                total['unit'] = 'iso4217:EUR'
            elif change == 'dimension':
                total['dimensions'] = {'segment': 'subsidiary'}
            else:
                total['entity_identifier'] = '9999'
            with self.subTest(change=change):
                self.assertNotIn('net_debt', canonical_reported_inputs(data)['actuals'])

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
            target=find(data,'us-gaap:CommercialPaper')
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

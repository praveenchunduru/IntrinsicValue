"""Reported-input shapes observed in live SEC records on 2026-11-14.

Before these rules every one of eleven large issuers stopped at the free
primary-input preflight. Each case below reproduces the fact pattern of one
issuer with synthetic amounts where the pattern is what matters, and with the
issuer's reported amounts where an exact reconciliation is being checked.
"""
import json
import unittest
from unittest.mock import patch

from investmentideas import core, pipeline
from investmentideas.companyfacts import normalize_companyfacts
from investmentideas.inputs import (FINANCING_ABSENCE_SCAN, LEASE_ABSENCE_POLICY, SHORT_TERM_POLICY,
                                    canonical_reported_inputs)

IDENTITY = {'cik_str': 123456, 'ticker': 'TEST', 'title': 'Test Issuer'}
END = '2026-06-30'


def row(value, end=END, *, start=None, filed='2026-07-29', form='10-K', accn='0001193125-26-123456'):
    result = {'val': value, 'end': end, 'filed': filed, 'form': form, 'accn': accn, 'fy': 2026, 'fp': 'FY'}
    if start is not None:
        result['start'] = start
    return result


LATER_10Q = {'filed': '2026-10-29', 'form': '10-Q', 'accn': '0001193125-26-654321'}


def issuer(balance, *, revenue=None, shares=1000):
    """One annual period. A balance value may be a list of ``(value, row kwargs)`` observations."""
    tags = {}
    def add(tag, value, **kwargs):
        tags.setdefault(tag, {'units': {'USD': []}})['units']['USD'].append(row(value, **kwargs))
    for tag, value in (revenue or {'RevenueFromContractWithCustomerExcludingAssessedTax': 1000}).items():
        add(tag, value, start='2025-07-01')
    for tag, value in {'CashAndCashEquivalentsAtCarryingValue': 10, **balance}.items():
        for amount, kwargs in (value if isinstance(value, list) else [(value, {})]):
            add(tag, amount, **kwargs)
    dei = ({'EntityCommonStockSharesOutstanding': {'units': {'shares': [row(shares, '2026-07-20')]}}}
           if shares else {})
    return {'cik': 123456, 'entityName': 'Test Issuer', 'facts': {'us-gaap': tags, 'dei': dei}}


def report(balance, *, complete_record=True, **kwargs):
    source = normalize_companyfacts(issuer(balance, **kwargs), identity=IDENTITY, as_of='2026-11-06',
                                    entity_record_complete=complete_record)
    return canonical_reported_inputs([source], as_of='2026-11-06')


TERM = {'LongTermDebt': 780, 'LongTermDebtCurrent': 100, 'LongTermDebtNoncurrent': 680}
LEASES = {'FinanceLeaseLiability': 15, 'FinanceLeaseLiabilityCurrent': 7, 'FinanceLeaseLiabilityNoncurrent': 8}


class ShortTermFinancingTests(unittest.TestCase):
    def test_commercial_paper_and_term_debt_pin_with_a_disclosed_assumption_after_an_attested_scan(self):
        # Apple: commercial paper, complete long-term debt, no short-term total.
        result = report({'CommercialPaper': 20, **TERM, **LEASES})
        bridge = result['net_debt_bridge']
        self.assertTrue(result['coverage']['complete'])
        self.assertEqual(result['coverage']['fields']['net_debt'], 'pinned_with_disclosed_assumption')
        self.assertEqual(bridge['debt'], 800)
        self.assertEqual(result['actuals']['net_debt'], 790)
        self.assertEqual(set(bridge['debt_components']), {'commercial_paper', 'term_debt'})
        self.assertEqual(bridge['short_term_financing']['basis'], 'identified_categories_house_assumption')
        self.assertEqual(bridge['short_term_financing']['identified_reported_categories'], ['commercial_paper'])
        self.assertEqual(bridge['short_term_financing']['scanned_concepts'], sorted(FINANCING_ABSENCE_SCAN))
        self.assertIn(SHORT_TERM_POLICY, result['notes'])
        self.assertEqual(result['actuals']['other_equity_claims'], 15)

    def test_no_short_term_concept_at_all_is_an_assumption_never_a_reported_zero(self):
        # Microsoft, Costco, Salesforce: long-term debt only.
        for term in (TERM, {key: TERM[key] for key in ('LongTermDebtCurrent', 'LongTermDebtNoncurrent')}):
            result = report({**term, **LEASES})
            with self.subTest(term=sorted(term)):
                self.assertEqual(result['net_debt_bridge']['debt'], 780)
                self.assertEqual(result['coverage']['fields']['net_debt'], 'pinned_with_disclosed_assumption')
                self.assertEqual(result['net_debt_bridge']['short_term_financing']['identified_reported_categories'], [])

    def test_a_trimmed_or_hand_built_record_cannot_support_the_absence_assumption(self):
        result = report({'CommercialPaper': 20, **TERM, **LEASES}, complete_record=False)
        self.assertEqual(result['coverage']['missing_fields'], ['net_debt'])
        self.assertFalse(result['net_debt_bridge'])
        self.assertTrue(any('no SEC entity-wide record attests' in note for note in result['coverage']['notes']))
        self.assertNotIn(SHORT_TERM_POLICY, result['notes'])

    def test_reported_financing_that_cannot_be_classified_blocks_instead_of_being_added_or_ignored(self):
        for concept in ('LineOfCredit', 'LinesOfCreditCurrent', 'ShortTermBankLoansAndNotesPayable',
                        'ShortTermNonBankLoansAndNotesPayable', 'BankOverdrafts', 'WarehouseAgreementBorrowings'):
            result = report({'CommercialPaper': 20, concept: 5, **TERM, **LEASES})
            with self.subTest(concept=concept):
                self.assertEqual(result['coverage']['missing_fields'], ['net_debt'])
                self.assertTrue(any('us-gaap:' + concept in note for note in result['coverage']['notes']))
        # An explicitly reported zero is not an unclassified borrowing.
        self.assertTrue(report({'CommercialPaper': 20, 'LineOfCredit': 0, **TERM, **LEASES})['coverage']['complete'])

    def test_a_reported_short_term_total_or_complete_debt_total_always_displaces_the_assumption(self):
        total = report({'ShortTermBorrowings': 50, 'CommercialPaper': 20, **TERM, **LEASES})
        self.assertEqual(total['net_debt_bridge']['debt'], 830)
        self.assertEqual(total['coverage']['fields']['net_debt'], 'pinned')
        self.assertEqual(total['net_debt_bridge']['short_term_financing']['basis'], 'reported')
        self.assertNotIn(SHORT_TERM_POLICY, total['notes'])
        whole = report({'DebtLongtermAndShorttermCombinedAmount': 900, 'CommercialPaper': 20, **TERM, **LEASES})
        self.assertEqual(whole['net_debt_bridge']['debt'], 900)
        self.assertEqual(set(whole['net_debt_bridge']['debt_components']), {'financial_debt'})
        self.assertEqual(whole['coverage']['fields']['net_debt'], 'pinned')

    def test_identified_other_short_term_borrowings_are_added_to_commercial_paper(self):
        # Coca-Cola: two identified categories and a combined long-term family.
        result = report({'CommercialPaper': 1495, 'OtherShortTermBorrowings': 56,
                         'LongTermDebtAndCapitalLeaseObligationsCurrent': 1822,
                         'LongTermDebtAndCapitalLeaseObligations': 42119})
        bridge = result['net_debt_bridge']
        self.assertTrue(result['coverage']['complete'])
        self.assertEqual(bridge['debt'], 45492)
        self.assertTrue(bridge['finance_leases_included_in_net_debt'])
        self.assertEqual(result['actuals']['other_equity_claims'], 0)
        self.assertEqual(bridge['short_term_financing']['identified_reported_categories'],
                         ['commercial_paper', 'other_short_term_borrowings'])


class ReportedIdentityTests(unittest.TestCase):
    NVIDIA = {'DebtCurrent': 999, 'LongTermDebt': 8468, 'LongTermDebtCurrent': 999, 'LongTermDebtNoncurrent': 7469}

    def test_current_debt_equal_to_current_long_term_debt_proves_no_short_term_borrowing(self):
        result = report(self.NVIDIA)
        self.assertTrue(result['coverage']['complete'])
        self.assertEqual(result['coverage']['fields']['net_debt'], 'pinned')
        self.assertEqual(result['net_debt_bridge']['debt'], 8468)
        self.assertEqual(result['net_debt_bridge']['short_term_financing']['basis'], 'reported_current_debt_identity')
        self.assertNotIn(SHORT_TERM_POLICY, result['notes'])
        # The identity needs no absence scan.
        self.assertEqual(report(self.NVIDIA, complete_record=False)['coverage']['fields']['net_debt'], 'pinned')

    def test_current_debt_above_the_identified_categories_contradicts_the_assumption(self):
        unexplained = report({**self.NVIDIA, 'DebtCurrent': 1500})
        self.assertIn('net_debt', unexplained['coverage']['missing_fields'])
        explained = report({**self.NVIDIA, 'DebtCurrent': 1500, 'CommercialPaper': 501})
        self.assertEqual(explained['net_debt_bridge']['debt'], 8969)
        self.assertEqual(explained['coverage']['fields']['net_debt'], 'pinned_with_disclosed_assumption')

    def test_absent_finance_lease_concepts_are_a_labelled_assumption_only_after_an_attested_scan(self):
        result = report(self.NVIDIA)
        claims = result['other_claims_bridge']
        self.assertEqual(result['actuals']['other_equity_claims'], 0)
        self.assertIsNone(claims['reported_finance_lease_liabilities'])
        self.assertEqual(claims['finance_lease_amount_basis'], LEASE_ABSENCE_POLICY)
        self.assertIn(LEASE_ABSENCE_POLICY, result['notes'])
        self.assertIn('other_equity_claims', report(self.NVIDIA, complete_record=False)['coverage']['missing_fields'])
        for partial in ({'FinanceLeaseLiabilityCurrent': 5}, {'CapitalLeaseObligationsNoncurrent': 5}):
            with self.subTest(partial=partial):
                self.assertIn('other_equity_claims', report({**self.NVIDIA, **partial})['coverage']['missing_fields'])


class ReconciliationTests(unittest.TestCase):
    ALPHABET = {'CommercialPaper': 0, 'LongTermDebt': 49085, 'LongTermDebtCurrent': 1996,
                'LongTermDebtNoncurrent': 46547, 'DebtInstrumentCarryingAmount': 49085, **LEASES}

    def test_gross_principal_tagged_as_long_term_debt_reconciles_exactly_to_carrying_parts(self):
        # Alphabet reports the unamortized amount with either sign across filings.
        result = report({**self.ALPHABET, 'DebtInstrumentUnamortizedDiscountPremiumAndDebtIssuanceCostsNet':
                         [(-542, {}), (542, LATER_10Q)]})
        bridge = result['net_debt_bridge']
        self.assertTrue(result['coverage']['complete'])
        self.assertEqual(bridge['debt'], 48543)
        self.assertEqual(set(bridge['debt_components']), {'commercial_paper', 'current_term_debt', 'noncurrent_term_debt'})
        self.assertEqual(bridge['long_term_debt_reconciliation']['gross_principal'], 49085)
        self.assertEqual(bridge['long_term_debt_reconciliation']['unamortized_discount_and_issuance_costs'], 542)
        self.assertEqual(bridge['long_term_debt_reconciliation']['carrying_amount'], 48543)

    def test_an_unreconciled_or_inexact_total_stays_a_conflict(self):
        # Amazon: no reported amount explains the difference.
        cases = {'no unamortized amount': self.ALPHABET,
                 'inexact unamortized amount': {**self.ALPHABET, 'DebtInstrumentUnamortizedDiscountPremiumAndDebtIssuanceCostsNet': 541},
                 'total is not the reported gross principal': {**self.ALPHABET, 'DebtInstrumentCarryingAmount': 49000,
                     'DebtInstrumentUnamortizedDiscountPremiumAndDebtIssuanceCostsNet': 542}}
        for name, balance in cases.items():
            result = report(balance)
            with self.subTest(name):
                self.assertEqual(result['coverage']['status'], 'conflicted')
                self.assertFalse(result['net_debt_bridge'])

    def test_a_whole_debt_total_equal_to_the_long_term_subtotal_is_excluded_when_short_term_debt_is_reported(self):
        # Walmart's quarterly filings tag year-end long-term debt with the whole-debt concept.
        balance = {'ShortTermBorrowings': 6596, 'LongTermDebt': 38166, 'LongTermDebtCurrent': 3542,
                   'LongTermDebtNoncurrent': 34624, 'DebtLongtermAndShorttermCombinedAmount': [(38166, LATER_10Q)], **LEASES}
        result = report(balance)
        bridge = result['net_debt_bridge']
        self.assertEqual(bridge['debt'], 44762)
        self.assertEqual(result['coverage']['fields']['net_debt'], 'pinned')
        self.assertEqual(bridge['excluded_scope_inconsistent_totals'][0]['concept'],
                         'us-gaap:DebtLongtermAndShorttermCombinedAmount')
        # Without separately reported short-term debt the total keeps its taxonomy scope.
        balance.pop('ShortTermBorrowings')
        plain = report(balance)
        self.assertEqual(plain['net_debt_bridge']['debt'], 38166)
        self.assertNotIn('excluded_scope_inconsistent_totals', plain['net_debt_bridge'])
        # A different whole-debt total is a real conflict, not a narrower scope.
        self.assertEqual(report({**balance, 'ShortTermBorrowings': 6596,
                                 'DebtLongtermAndShorttermCombinedAmount': 40000})['coverage']['status'], 'conflicted')

    def test_self_inconsistent_long_term_facts_remain_rejected(self):
        # Home Depot: long-term debt excluding leases exceeds the combined family less reported leases.
        result = report({'CommercialPaper': 4464, 'DebtAndCapitalLeaseObligations': 51308, 'LongTermDebt': 49397,
                         'LongTermDebtAndCapitalLeaseObligations': 46341,
                         'LongTermDebtAndCapitalLeaseObligationsCurrent': 4967,
                         'FinanceLeaseLiability': 2963, 'FinanceLeaseLiabilityCurrent': 288,
                         'FinanceLeaseLiabilityNoncurrent': 2675})
        self.assertEqual(result['coverage']['status'], 'conflicted')
        self.assertIn('net_debt', result['coverage']['missing_fields'])

    def test_shares_reported_only_by_class_are_named_as_the_gap(self):
        result = report({**TERM, **LEASES}, shares=None)
        self.assertEqual(result['coverage']['missing_fields'], ['shares_outstanding'])
        self.assertTrue(any('only by share class' in note for note in result['coverage']['notes']))


class RevenueScopeTests(unittest.TestCase):
    def test_total_revenues_are_selected_over_a_smaller_contract_revenue_amount(self):
        # Walmart: total revenues include membership and other income.
        result = report({**TERM, **LEASES}, revenue={'Revenues': 713163,
                                                     'RevenueFromContractWithCustomerExcludingAssessedTax': 706413})
        self.assertEqual(result['actuals']['revenue'], 713163)
        self.assertIn('us-gaap:Revenues', result['input_dates']['revenue']['basis'])
        self.assertTrue(result['coverage']['complete'])

    def test_a_total_below_contract_revenue_or_conflicting_totals_remain_conflicts(self):
        below = report({**TERM, **LEASES}, revenue={'Revenues': 700,
                                                    'RevenueFromContractWithCustomerExcludingAssessedTax': 706})
        self.assertEqual(below['coverage']['status'], 'conflicted')
        self.assertNotIn('revenue', below['actuals'])


class StatedPrecisionTests(unittest.TestCase):
    def inline(self, *facts):
        def fact(concept, value, *, decimals=None, instant=END, start=None, unit='iso4217:USD'):
            return {'concept': concept, 'normalized_value': value, 'normalized_decimal': str(value),
                    'context_ref': 'c', 'instant': instant if start is None else None, 'start_date': start,
                    'end_date': END if start else None, 'unit': unit, 'entity_identifier': '123456',
                    'parse_status': 'parsed', 'is_nil': False, 'dimensions': {},
                    **({'decimals': decimals} if decimals is not None else {})}
        rows = [fact('us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax', 713163000000, start='2025-07-01'),
                fact('us-gaap:ShortTermBorrowings', 6596000000, decimals='-6'),
                fact('us-gaap:LongTermDebt', 38166000000, decimals='-6'),
                fact('us-gaap:FinanceLeaseLiability', 6761000000, decimals='-6'),
                fact('dei:EntityCommonStockSharesOutstanding', 7933746241, instant='2026-07-20', unit='xbrli:shares'),
                *(fact(*args, **kwargs) for args, kwargs in facts)]
        return canonical_reported_inputs([{'id': 'S4', 'kind': 'regulatory_primary', 'data': {
            'metadata': {'cik': 123456, 'form': '10-K', 'filingDate': '2026-07-29'}, 'inline_facts': rows}}],
            as_of='2026-11-06')

    CASH = 'us-gaap:CashAndCashEquivalentsAtCarryingValue'

    def test_a_coarser_prose_repetition_within_its_own_precision_is_not_a_conflict(self):
        # Walmart's 10-K: "10,727" in millions on the balance sheet and "$10.7 billion" in the discussion.
        result = self.inline(((self.CASH, 10727000000), {'decimals': '-6'}),
                             ((self.CASH, 10700000000), {'decimals': '-8'}))
        self.assertTrue(result['coverage']['complete'])
        self.assertEqual(result['net_debt_bridge']['selected_cash_and_nonoperating_investments'], 10727000000)

    def test_values_outside_the_stated_precision_or_without_one_remain_conflicts(self):
        cases = {'beyond half a unit': {'decimals': '-8'}, 'no stated precision': {},
                 'same precision': {'decimals': '-6'}}
        for name, kwargs in cases.items():
            value = 10800000000 if name == 'beyond half a unit' else 10700000000
            result = self.inline(((self.CASH, 10727000000), {'decimals': '-6'}), ((self.CASH, value), kwargs))
            with self.subTest(name):
                self.assertEqual(result['coverage']['status'], 'conflicted')


class PromptSizeTests(unittest.TestCase):
    def source(self):
        return normalize_companyfacts(issuer({'CommercialPaper': 20, **TERM, **LEASES}), identity=IDENTITY,
                                      as_of='2026-11-06', source_id='S2', entity_record_complete=True)

    def test_per_filing_records_are_not_sent_to_models_but_every_selected_value_is(self):
        source = self.source()
        presented = core.prompt_companyfacts(source['data'])
        self.assertNotIn('reported_facts', presented)
        self.assertEqual(presented['reported_fact_records']['count'], len(source['data']['reported_facts']))
        self.assertEqual([(row['concept'], row['value']) for row in presented['financial_context_table']],
                         [(row['concept'], row['value']) for row in source['data']['financial_context_table']])
        self.assertIn('reported_facts', source['data'])  # The saved source is unchanged.
        context = pipeline._evidence_context([source])
        self.assertNotIn('source_record', context)
        self.assertIn('us-gaap:CommercialPaper', context)
        self.assertEqual(core.prompt_companyfacts({'series': [1]}), {'series': [1]})

    def test_quick_mode_prompts_omit_filing_records_and_leave_output_headroom(self):
        catalog = {'data': [{'id': model, 'pricing': {'prompt': '0.000001', 'completion': '0.000002'},
                             'supported_parameters': ['temperature']} for model in core.MODELS['economy']]}
        requests = []
        def transport(url, *, key=None, payload=None, timeout=90):
            if payload is None:
                return catalog
            requests.append(payload)
            return {'model': payload['model'], 'usage': {'cost': 0.001, 'prompt_tokens': 10, 'completion_tokens': 10},
                    'choices': [{'finish_reason': 'stop', 'message': {'content': 'ok [S1]'}}]}
        sources = [{'id': 'S1', 'title': 'quote', 'url': None, 'kind': 'market_snapshot', 'data': {}}, self.source()]
        import tempfile
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(core, 'collect', return_value=(sources, [])), \
                patch.object(core, 'request_json', side_effect=transport):
            core.run('TEST', 'key', profile='economy', budget=2, directory=folder)
        self.assertEqual([request['max_tokens'] for request in requests], [8000, 8000, 6000, 9000])
        for request in requests:
            content = request['messages'][1]['content']
            prompt = ''.join(part['text'] for part in content) if isinstance(content, list) else content
            self.assertNotIn('source_record', prompt)
            self.assertIn('us-gaap:LongTermDebt', prompt)
            self.assertIn('at most', prompt)
        self.assertLess(len(json.dumps(requests[0])), len(json.dumps(self.source())))

    def test_the_wait_for_a_completion_scales_with_its_output_limit(self):
        self.assertEqual(core.completion_timeout(3500), 235)
        self.assertEqual(core.completion_timeout(14000), 760)
        self.assertEqual(core.completion_timeout(500), 180)
        self.assertEqual(core.completion_timeout(10**6), 900)

    def test_search_lanes_reserve_the_headroom_they_request(self):
        self.assertGreaterEqual(pipeline.WEB_RESEARCH_TOKENS, 6000)


if __name__ == '__main__':
    unittest.main()

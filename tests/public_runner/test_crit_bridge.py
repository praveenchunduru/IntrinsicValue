"""Equity bridge beyond net debt, diluted shares, and three live preflight shapes.

Damodaran (critique F2, 2026-11-26): non-operating investments, minority
interests, preferred stock and pension underfunding belong in the bridge from
enterprise value to equity value. Mauboussin (F7): with stock compensation as an
expense, divide by diluted shares. Johnson & Johnson, Procter & Gamble and Uber
stopped at the free reported-input preflight on 2026-11-26; each case below
reproduces the issuer's reported fact pattern.
"""
import copy
import json
from pathlib import Path
import unittest

from investmentideas import pipeline, roic
from investmentideas.companyfacts import normalize_companyfacts
from investmentideas.inputs import (DILUTION_ABSENCE_POLICY, NONOPERATING_ABSENCE_POLICY, PENSION_POLICY,
                                    RESTRICTED_CASH_POLICY, canonical_reported_inputs, pin_reported_inputs)
from investmentideas.valuation import evaluate

ROOT = Path(__file__).resolve().parents[2]
IDENTITY = {'cik_str': 123456, 'ticker': 'TEST', 'title': 'Test Issuer'}
END = '2026-06-30'
START = '2025-07-01'


def row(value, end=END, *, start=None, filed='2026-07-29', form='10-K', accn='0001193125-26-123456'):
    result = {'val': value, 'end': end, 'filed': filed, 'form': form, 'accn': accn, 'fy': 2026, 'fp': 'FY'}
    if start is not None:
        result['start'] = start
    return result


LATER_10Q = {'filed': '2026-10-29', 'form': '10-Q', 'accn': '0001193125-26-654321'}
TERM = {'LongTermDebtCurrent': 100, 'LongTermDebtNoncurrent': 680}
M = 1000000
LEASES = {'FinanceLeaseLiability': 15, 'FinanceLeaseLiabilityCurrent': 7, 'FinanceLeaseLiabilityNoncurrent': 8}


def facts_source(balance, *, shares=1000, periods=None, cash=True):
    """A companyfacts record after an attested whole-record scan. Values may be lists of (value, row kwargs)."""
    tags = {}

    def add(tag, value, unit='USD', **kwargs):
        tags.setdefault(tag, {'units': {}})['units'].setdefault(unit, []).append(row(value, **kwargs))
    add('RevenueFromContractWithCustomerExcludingAssessedTax', 1000, start=START)
    for tag, value in ({'CashAndCashEquivalentsAtCarryingValue': 10} if cash else {}).items():
        add(tag, value)
    for tag, value in balance.items():
        for amount, kwargs in (value if isinstance(value, list) else [(value, {})]):
            add(tag, amount, **kwargs)
    for tag, value in (periods or {}).items():
        add(tag, value, unit='shares', start=START)
    payload = {'cik': 123456, 'entityName': 'Test Issuer', 'facts': {
        'us-gaap': tags, 'dei': {'EntityCommonStockSharesOutstanding': {'units': {'shares': [row(shares, '2026-07-20')]}}}}}
    return normalize_companyfacts(payload, identity=IDENTITY, as_of='2026-11-06', source_id='S2',
                                  entity_record_complete=True)


def inline_source(*facts):
    """The 10-K's inline facts: ``(concept, value, decimals)`` on the balance-sheet date."""
    rows = [{'concept': 'us-gaap:' + concept, 'normalized_value': value, 'normalized_decimal': str(value),
             'context_ref': 'c', 'instant': END, 'start_date': None, 'end_date': None, 'unit': 'iso4217:USD',
             'entity_identifier': '123456', 'parse_status': 'parsed', 'is_nil': False, 'dimensions': {},
             'decimals': decimals} for concept, value, decimals in facts]
    return {'id': 'S4', 'kind': 'regulatory_primary', 'data': {
        'metadata': {'cik': 123456, 'form': '10-K', 'filingDate': '2026-07-29'}, 'inline_facts': rows}}


def report(balance, *inline, **kwargs):
    sources = [facts_source(balance, **kwargs)] + ([inline_source(*inline)] if inline else [])
    return canonical_reported_inputs(sources, as_of='2026-11-06')


class NonOperatingAssetTests(unittest.TestCase):
    def test_the_balance_sheet_investments_line_is_added_and_its_parts_are_not(self):
        # Microsoft: LongTermInvestments holds its equity-method and non-marketable stakes.
        result = report({'CashCashEquivalentsAndShortTermInvestments': 50, **TERM, **LEASES, 'LongTermInvestments': 36,
                         'EquityMethodInvestments': 12, 'EquitySecuritiesWithoutReadilyDeterminableFairValueAmount': 12})
        bridge = result['nonoperating_assets_bridge']
        self.assertTrue(result['coverage']['complete'])
        self.assertEqual(result['actuals']['nonoperating_assets'], 36)
        self.assertEqual(set(bridge['components']), {'long_term_investments'})
        self.assertEqual({entry['line'] for entry in bridge['not_added']},
                         {'equity_method_investments', 'equity_securities_nonmarketable'})
        self.assertEqual(result['coverage']['fields']['nonoperating_assets'], 'pinned')
        self.assertEqual(bridge['as_of'], result['net_debt_bridge']['as_of'])

    def test_only_the_largest_nested_line_is_added_as_in_invested_capital(self):
        # Alphabet's other investments contain its equity-method stakes.
        balance = {**TERM, **LEASES, 'OtherLongTermInvestments': 68, 'EquityMethodInvestments': 5,
                   'EquitySecuritiesWithoutReadilyDeterminableFairValueAmount': 64}
        result = report(balance)
        self.assertEqual(result['actuals']['nonoperating_assets'], 68)
        # The bridge adds exactly what the return-on-capital table removes.
        row_inputs = {'long_term_investments': None, 'securities_noncurrent': None, 'other_long_term_investments': 68.0,
                      'equity_method_investments': 5.0, 'equity_securities_nonmarketable': 64.0}
        self.assertEqual(sum(amount for _, amount in roic.nonoperating_investments(row_inputs)), 68)

    def test_noncurrent_securities_selected_as_cash_are_not_added_again(self):
        # Apple: noncurrent marketable securities are in the selected cash.
        result = report({'MarketableSecuritiesCurrent': 20, 'MarketableSecuritiesNoncurrent': 80, **TERM, **LEASES})
        self.assertEqual(result['net_debt_bridge']['selected_cash_and_nonoperating_investments'], 110)
        self.assertEqual(result['actuals']['nonoperating_assets'], 0)
        self.assertEqual(result['nonoperating_assets_bridge']['not_added'][0]['line'], 'securities_noncurrent')
        # The evidence is the reported line, so this zero is pinned, not assumed.
        self.assertEqual(result['coverage']['fields']['nonoperating_assets'], 'pinned')
        self.assertEqual(result['source_refs']['nonoperating_assets'], ['S2'])

    def test_investments_line_beside_noncurrent_cash_securities_falls_back_to_equity_lines(self):
        result = report({'MarketableSecuritiesCurrent': 20, 'MarketableSecuritiesNoncurrent': 80, **TERM, **LEASES,
                         'LongTermInvestments': 95, 'EquityMethodInvestments': 9})
        self.assertEqual(result['actuals']['nonoperating_assets'], 9)
        self.assertEqual(result['nonoperating_assets_bridge']['not_added'][0]['line'], 'long_term_investments')

    def test_marketable_equity_is_added_only_where_cash_cannot_contain_it(self):
        # Nvidia: cash plus available-for-sale debt securities; publicly held equity sits outside both.
        nvidia = {'AvailableForSaleSecuritiesDebtSecurities': 39, 'DebtSecuritiesCurrent': 39, **TERM,
                  'EquitySecuritiesFvNi': 12, 'EquitySecuritiesWithoutReadilyDeterminableFairValueAmount': 22, **LEASES}
        self.assertEqual(report(nvidia)['actuals']['nonoperating_assets'], 34)
        # Meta: selected marketable securities can contain equity, so it is not added.
        meta = {'MarketableSecuritiesCurrent': 45, **TERM, **LEASES, 'EquitySecuritiesFvNi': 6,
                'EquitySecuritiesWithoutReadilyDeterminableFairValueAmount': 20}
        self.assertEqual(report(meta)['actuals']['nonoperating_assets'], 20)
        # Salesforce: the other-investments line added can contain its public stakes.
        salesforce = {'AvailableForSaleSecuritiesDebtSecurities': 2, **TERM, **LEASES, 'EquitySecuritiesFvNi': 1,
                      'OtherLongTermInvestments': 7}
        self.assertEqual(report(salesforce)['actuals']['nonoperating_assets'], 7)

    def test_carrying_value_is_used_and_a_quoted_value_is_shown_only(self):
        # Coca-Cola: equity-method stakes at $20.2 billion book and $34.3 billion quoted.
        result = report({**TERM, **LEASES, 'EquityMethodInvestments': 20235, 'EquityMethodInvestmentQuotedMarketValue': 34286})
        bridge = result['nonoperating_assets_bridge']
        self.assertEqual(result['actuals']['nonoperating_assets'], 20235)
        self.assertEqual(bridge['reported_quoted_market_value_not_used']['value'], 34286)
        self.assertIn('Quoted market values are not used', bridge['basis'])

    def test_no_investment_concept_is_a_disclosed_zero(self):
        result = report({**TERM, **LEASES})
        self.assertEqual(result['actuals']['nonoperating_assets'], 0)
        self.assertEqual(result['coverage']['fields']['nonoperating_assets'], 'pinned_with_disclosed_assumption')
        self.assertTrue(result['source_refs']['nonoperating_assets'][0].startswith('assumption:'))
        self.assertIn(NONOPERATING_ABSENCE_POLICY, result['notes'])


class ClaimTests(unittest.TestCase):
    def test_minority_interests_and_preferred_are_deducted_at_carrying_value(self):
        # AT&T and Walmart: noncontrolling interests in equity and redeemable ones outside it; Oracle: preferred.
        result = report({**TERM, **LEASES, 'MinorityInterest': 60, 'RedeemableNoncontrollingInterestEquityCarryingAmount': 3,
                         'PreferredStockValue': 50})
        claims = result['other_claims_bridge']
        self.assertEqual(result['actuals']['other_equity_claims'], 15 + 60 + 3 + 50)
        self.assertEqual(claims['finance_lease_claim'], 15)
        self.assertEqual({name: item['value'] for name, item in claims['components'].items()},
                         {'noncontrolling_interests': 60, 'redeemable_noncontrolling_interests': 3, 'preferred_stock': 50})
        self.assertEqual(claims['other_equity_claims'], result['actuals']['other_equity_claims'])
        self.assertEqual([entry['line'] for entry in claims['components_assumed_zero']], ['defined_benefit_obligations'])
        self.assertTrue(any(ref.startswith('assumption: defined benefit obligations') for ref in
                            result['source_refs']['other_equity_claims']))

    def test_noncontrolling_interest_can_be_derived_from_total_and_parent_equity(self):
        result = report({**TERM, **LEASES, 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest': 500,
                         'StockholdersEquity': 470})
        item = result['other_claims_bridge']['components']['noncontrolling_interests']
        self.assertEqual(item['value'], 30)
        self.assertTrue(item['basis'].startswith('derived'))
        # Restated equity totals leave the fallback underived; they do not stop the bridge.
        restated = report({**TERM, **LEASES, 'StockholdersEquity': [(470, {}), (465, LATER_10Q)],
                           'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest': 500})
        self.assertTrue(restated['coverage']['complete'])
        self.assertNotIn('noncontrolling_interests', restated['other_claims_bridge']['components'])

    def test_pension_underfunding_is_deducted_before_tax_and_a_surplus_is_not_added(self):
        # Procter & Gamble: separate pension and retiree-health liabilities, no plan asset line.
        pg = report({**TERM, **LEASES, 'DefinedBenefitPensionPlanLiabilitiesNoncurrent': 2204,
                     'OtherPostretirementDefinedBenefitPlanLiabilitiesNoncurrent': 736})
        benefits = pg['other_claims_bridge']['components']['defined_benefit_obligations']
        self.assertEqual(benefits['value'], 2940)
        self.assertEqual(benefits['tax_treatment'], 'before tax')
        self.assertIn(PENSION_POLICY, pg['notes'])
        # Johnson & Johnson: overfunded plans exceed the recognized liabilities. The combined
        # employee-obligations concept, which also holds postemployment benefits, is not used.
        jnj = report({**TERM, **LEASES, 'DefinedBenefitPensionPlanCurrentAndNoncurrentLiabilities': 2917,
                      'OtherPostretirementDefinedBenefitPlanLiabilitiesCurrentAndNoncurrent': 1774,
                      'PensionAndOtherPostretirementDefinedBenefitPlansLiabilitiesNoncurrent': 6957,
                      'DefinedBenefitPlanAssetsForPlanBenefitsNoncurrent': 7300})
        benefits = jnj['other_claims_bridge']['components']['defined_benefit_obligations']
        self.assertEqual(benefits['recognized_liabilities'], 4691)
        self.assertEqual(benefits['value'], 0)
        self.assertEqual(benefits['net_surplus_not_added'], 2609)
        self.assertEqual(jnj['actuals']['other_equity_claims'], 15)

    def test_postemployment_obligation_stands_in_only_without_defined_benefit_concepts(self):
        # AT&T tags its pension and postretirement obligation as postemployment benefits.
        att = report({**TERM, **LEASES, 'PostemploymentBenefitsLiabilityNoncurrent': 8478,
                      'PostemploymentBenefitsLiabilityCurrent': 635})
        benefits = att['other_claims_bridge']['components']['defined_benefit_obligations']
        self.assertEqual((benefits['group'], benefits['value']), ('postemployment_benefits', 9113))
        both = report({**TERM, **LEASES, 'DefinedBenefitPensionPlanLiabilitiesNoncurrent': 100,
                       'PostemploymentBenefitsLiabilityNoncurrent': 900})
        self.assertEqual(both['other_claims_bridge']['components']['defined_benefit_obligations']['value'], 100)

    def test_a_pension_total_that_conflicts_with_its_parts_stops_the_bridge(self):
        result = report({**TERM, **LEASES, 'DefinedBenefitPensionPlanCurrentAndNoncurrentLiabilities': 100,
                         'DefinedBenefitPensionPlanLiabilitiesCurrent': 10, 'DefinedBenefitPensionPlanLiabilitiesNoncurrent': 80})
        self.assertEqual(result['coverage']['status'], 'conflicted')


class DilutionTests(unittest.TestCase):
    def test_increment_is_diluted_less_basic_and_value_divides_by_diluted_shares(self):
        result = report({**TERM, **LEASES}, periods={'WeightedAverageNumberOfDilutedSharesOutstanding': 1040,
                                                     'WeightedAverageNumberOfSharesOutstandingBasic': 1010})
        self.assertEqual(result['actuals']['dilutive_shares'], 30)
        self.assertEqual(result['share_count_bridge']['diluted_shares'], 1030)
        self.assertEqual(result['coverage']['fields']['dilutive_shares'], 'pinned')
        self.assertEqual(result['input_dates']['dilutive_shares']['end_date'], END)

    def test_convertible_debt_shares_are_left_out_because_net_debt_deducts_the_debt(self):
        result = report({**TERM, **LEASES}, periods={'WeightedAverageNumberOfDilutedSharesOutstanding': 1040,
                                                     'WeightedAverageNumberOfSharesOutstandingBasic': 1010,
                                                     'IncrementalCommonSharesAttributableToConversionOfDebtSecurities': 7})
        self.assertEqual(result['actuals']['dilutive_shares'], 23)

    def test_preferred_counted_in_diluted_shares_is_not_also_deducted(self):
        # Procter & Gamble: 68.3 million preferred conversion shares are in its diluted count.
        result = report({**TERM, **LEASES, 'PreferredStockValue': 756},
                        periods={'WeightedAverageNumberOfDilutedSharesOutstanding': 1089,
                                 'WeightedAverageNumberOfSharesOutstandingBasic': 1000,
                                 'IncrementalCommonSharesAttributableToConversionOfPreferredStock': 68})
        preferred = result['other_claims_bridge']['components']['preferred_stock']
        self.assertEqual((preferred['value'], preferred['carrying_value_not_deducted']), (0, 756))
        self.assertEqual(result['actuals']['dilutive_shares'], 89)
        self.assertEqual(result['actuals']['other_equity_claims'], 15)

    def test_missing_or_equal_counts_give_zero_with_their_basis(self):
        missing = report({**TERM, **LEASES})
        self.assertEqual(missing['actuals']['dilutive_shares'], 0)
        self.assertEqual(missing['coverage']['fields']['dilutive_shares'], 'pinned_with_disclosed_assumption')
        self.assertEqual(missing['share_count_bridge']['basis'], DILUTION_ABSENCE_POLICY)
        equal = report({**TERM, **LEASES}, periods={'WeightedAverageNumberOfShareOutstandingBasicAndDiluted': 1000})
        self.assertEqual((equal['actuals']['dilutive_shares'], equal['coverage']['fields']['dilutive_shares']), (0, 'pinned'))

    def test_diluted_below_basic_is_a_conflict(self):
        result = report({**TERM, **LEASES}, periods={'WeightedAverageNumberOfDilutedSharesOutstanding': 990,
                                                     'WeightedAverageNumberOfSharesOutstandingBasic': 1000})
        self.assertEqual(result['coverage']['status'], 'conflicted')

    def test_a_split_after_the_fiscal_year_scales_the_increment(self):
        result = report({**TERM, **LEASES}, shares=10000,
                        periods={'WeightedAverageNumberOfDilutedSharesOutstanding': 1020,
                                 'WeightedAverageNumberOfSharesOutstandingBasic': 1000})
        self.assertEqual(result['actuals']['dilutive_shares'], 200)
        self.assertEqual(result['coverage']['fields']['dilutive_shares'], 'pinned_with_disclosed_assumption')


class PreflightShapeTests(unittest.TestCase):
    def test_short_term_line_equal_to_rounded_current_debt_is_current_debt(self):
        # Johnson & Johnson: loans and notes payable $8,495 million tagged ShortTermBorrowings,
        # current debt "$8.5 billion" (decimals -8) and $2,000 million of current long-term debt.
        balance = {'ShortTermBorrowings': 8495 * M, 'DebtCurrent': 8500 * M, 'LongTermDebt': 41438 * M,
                   'LongTermDebtCurrent': 2000 * M, 'LongTermDebtNoncurrent': 39438 * M}
        inline = [(name, value, '-8' if name == 'DebtCurrent' else '-6') for name, value in balance.items()]
        result = report(balance, *inline)
        bridge = result['net_debt_bridge']
        self.assertEqual(result['coverage']['conflicts'], [])
        self.assertEqual(bridge['debt'], (8495 + 39438) * M)
        self.assertEqual({name: item['value'] for name, item in bridge['debt_components'].items()},
                         {'short_term_borrowings': 6495 * M, 'term_debt': 41438 * M})
        self.assertEqual(bridge['short_term_financing']['basis'], 'reported_current_debt_less_current_maturities')
        self.assertEqual(bridge['current_debt_reconciliation']['reported_current_debt'], 8500 * M)
        self.assertEqual(result['actuals']['other_equity_claims'], 0)
        # Without the stated precision the same figures stay a conflict.
        self.assertEqual(report(balance)['coverage']['status'], 'conflicted')

    def test_genuine_short_term_borrowings_beside_current_maturities_are_unchanged(self):
        balance = {'ShortTermBorrowings': 300, 'DebtCurrent': 407, **TERM, **LEASES}
        bridge = report(balance)['net_debt_bridge']
        self.assertNotIn('current_debt_reconciliation', bridge)
        self.assertEqual(bridge['debt'], 300 + 780)

    def test_parts_within_their_stated_rounding_of_current_debt_agree(self):
        # Procter & Gamble: debt due within one year $11,296 million against parts of $6,457, $4,837
        # and $3 million, each rounded to millions; cash tagged only with the restricted-cash total.
        balance = {'DebtCurrent': 11296 * M, 'CommercialPaper': 4837 * M, 'OtherShortTermBorrowings': 3 * M,
                   'LongTermDebtCurrent': 6457 * M, 'LongTermDebtNoncurrent': 22842 * M,
                   'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents': 9942 * M}
        inline = [(name, value, '-6') for name, value in balance.items()]
        sources = [facts_source(balance, cash=False), inline_source(*inline)]
        result = canonical_reported_inputs(sources, as_of='2026-11-06')
        bridge = result['net_debt_bridge']
        self.assertTrue(result['coverage']['complete'])
        self.assertEqual(bridge['debt'], (4837 + 3 + 6457 + 22842) * M)
        self.assertEqual(bridge['short_term_financing']['basis'], 'identified_categories_house_assumption')
        self.assertEqual(set(bridge['cash_components']), {'cash_including_restricted_cash'})
        self.assertEqual(result['coverage']['fields']['net_debt'], 'pinned_with_disclosed_assumption')
        self.assertIn(RESTRICTED_CASH_POLICY, result['notes'])
        # Two million beyond the rounding the stated precision allows is still a conflict.
        far = dict(balance, OtherShortTermBorrowings=5 * M)
        sources = [facts_source(far, cash=False), inline_source(*[(name, value, '-6') for name, value in far.items()])]
        self.assertEqual(canonical_reported_inputs(sources, as_of='2026-11-06')['coverage']['status'], 'conflicted')

    def test_reported_restricted_cash_blocks_the_cash_fallback(self):
        result = report({'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents': 50, 'RestrictedCashCurrent': 5,
                         **TERM, **LEASES}, cash=False)
        self.assertNotIn('net_debt', result['actuals'])

    def test_principal_observations_beside_carrying_ones_are_set_aside(self):
        # Uber: the 10-K tagged LongTermDebt at principal ($10,600 million); later 10-Qs tagged the
        # same date at carrying value ($10,521 million = principal less $79 million unamortized).
        balance = {'LongTermDebt': [(10600, {}), (10521, LATER_10Q)], 'DebtInstrumentCarryingAmount': 10600,
                   'DebtInstrumentUnamortizedDiscountPremiumAndDebtIssuanceCostsNet': 79,
                   'LongTermDebtCurrent': 0, 'LongTermDebtNoncurrent': 10521, 'ShortTermBorrowings': 0, **LEASES,
                   'MarketableSecuritiesCurrent': 528,
                   'AvailableForSaleSecuritiesDebtSecurities': [(10326, {}), (10325, LATER_10Q)]}
        result = report(balance)
        bridge = result['net_debt_bridge']
        self.assertEqual(result['coverage']['conflicts'], [])
        self.assertEqual(bridge['debt'], 10521)
        self.assertEqual(bridge['long_term_debt_observations_set_aside']['gross_principal'], 10600)
        # The conflicting available-for-sale total is a fallback that is not needed here.
        self.assertIn('available_for_sale_debt_securities', bridge['cash_coverage']['unused_conflicting_categories'])
        self.assertEqual(bridge['selected_cash_and_nonoperating_investments'], 10 + 528)

    def test_a_conflicting_available_for_sale_total_still_stops_when_it_is_needed(self):
        balance = {**TERM, **LEASES, 'AvailableForSaleSecuritiesDebtSecurities': [(10326, {}), (10325, LATER_10Q)]}
        self.assertEqual(report(balance)['coverage']['status'], 'conflicted')

    def test_unexplained_long_term_debt_revisions_stay_conflicts(self):
        balance = {'LongTermDebt': [(10600, {}), (10500, LATER_10Q)], 'DebtInstrumentCarryingAmount': 10600,
                   'DebtInstrumentUnamortizedDiscountPremiumAndDebtIssuanceCostsNet': 79,
                   'LongTermDebtCurrent': 0, 'LongTermDebtNoncurrent': 10521, **LEASES}
        self.assertEqual(report(balance)['coverage']['status'], 'conflicted')


def plan(actuals, refs=None):
    return {'currency': 'USD', 'amount_unit': 'currency_units', 'share_unit': 'shares',
            'assumptions_label': 'House forecasts', 'actuals': actuals,
            'source_refs': refs or {key: ['S1'] for key in actuals},
            'scenarios': {name: {'probability': weight, 'growth_rates': [.03] * 5, 'ebit_margins': [.2] * 5,
                                 'tax_rate': .2, 'sales_to_capital': 2.0, 'wacc': .09, 'terminal_growth': .03,
                                 'terminal_roic': .09} for name, weight in (('bear', 0), ('base', 1), ('bull', 0))}}


class ValuationBridgeTests(unittest.TestCase):
    BASE = {'revenue': 1000, 'net_debt': 100, 'other_equity_claims': 20, 'shares_outstanding': 100}

    def test_equity_adds_investments_and_divides_by_diluted_shares(self):
        old = evaluate(plan(dict(self.BASE)))['scenarios']['base']
        new = evaluate(plan(dict(self.BASE, nonoperating_assets=50, dilutive_shares=4)))['scenarios']['base']
        self.assertAlmostEqual(new['enterprise_value'], old['enterprise_value'])
        self.assertAlmostEqual(new['equity_value'], old['enterprise_value'] - 100 - 20 + 50)
        self.assertAlmostEqual(new['per_share_value'], new['equity_value'] / 104)
        self.assertEqual((new['diluted_shares'], new['nonoperating_assets']), (104, 50))

    def test_a_plan_without_the_new_fields_values_as_before(self):
        result = evaluate(plan(dict(self.BASE)))['scenarios']['base']
        self.assertAlmostEqual(result['per_share_value'], (result['enterprise_value'] - 120) / 100)
        self.assertEqual((result['nonoperating_assets'], result['dilutive_shares']), (0, 0))

    def test_new_fields_need_sources_and_cannot_be_negative(self):
        refs = {key: ['S1'] for key in self.BASE}
        with self.assertRaisesRegex(ValueError, 'source_refs.nonoperating_assets'):
            evaluate(plan(dict(self.BASE, nonoperating_assets=5), refs))
        with self.assertRaisesRegex(ValueError, 'dilutive_shares cannot be negative'):
            evaluate(plan(dict(self.BASE, dilutive_shares=-1)))

    def test_market_enterprise_value_uses_the_same_bridge(self):
        priced = plan(dict(self.BASE, nonoperating_assets=50, dilutive_shares=4))
        priced['market_price'] = 10
        priced['source_refs']['market_price'] = ['S1']
        market = evaluate(priced)['computed_diagnostics']['valuation_multiples']['market']
        self.assertEqual(market['enterprise_value'], 10 * 104 + 100 + 20 - 50)

    def test_shipped_examples_value_alike_with_zero_bridge_fields(self):
        # The shipped examples predate both fields; stating them as zero changes nothing.
        # (Their saved values predate the 2026-11-26 continuing-value retiming, which
        # test_market_consistency checks separately.)
        for name in ('AAPL-2026-11-06', 'AAPL-2026-11-06-revised'):
            plan_value = json.loads((ROOT / 'examples' / name / 'assumptions.json').read_text())
            stated = json.loads(json.dumps(plan_value))
            stated['actuals'].update(nonoperating_assets=0, dilutive_shares=0)
            for field in ('nonoperating_assets', 'dilutive_shares'):
                stated['source_refs'][field] = ['assumption: zero for this test']
            before, after = evaluate(plan_value), evaluate(stated)
            with self.subTest(name):
                for scenario in ('bear', 'base', 'bull'):
                    self.assertEqual(before['scenarios'][scenario]['per_share_value'],
                                     after['scenarios'][scenario]['per_share_value'])


class PinAndMemoTests(unittest.TestCase):
    def sources(self):
        return [facts_source({**TERM, **LEASES, 'EquityMethodInvestments': 40, 'MinorityInterest': 25,
                              'DefinedBenefitPensionPlanLiabilitiesNoncurrent': 30},
                             periods={'WeightedAverageNumberOfDilutedSharesOutstanding': 1030,
                                      'WeightedAverageNumberOfSharesOutstandingBasic': 1000})]

    def test_pinned_plan_carries_the_bridges_and_prints_each_line(self):
        locked, _ = pin_reported_inputs(plan({'revenue': 1, 'net_debt': 0, 'other_equity_claims': 0, 'shares_outstanding': 1}),
                                        self.sources())
        self.assertEqual(locked['actuals']['nonoperating_assets'], 40)
        self.assertEqual(locked['actuals']['dilutive_shares'], 30)
        self.assertEqual(locked['actuals']['other_equity_claims'], 15 + 25 + 30)
        for name in ('nonoperating_assets_bridge', 'share_count_bridge'):
            self.assertIn(name, locked)
        result = evaluate(locked)
        text = pipeline.valuation_section(locked, result)
        for label in ('Less net debt', 'Less finance leases', 'Less noncontrolling interests',
                      'Less net unfunded pension and postretirement obligations, before tax',
                      'Plus non-operating investments', 'Plus dilutive increment', 'Diluted shares', 'Value per share'):
            self.assertIn('| ' + label, text)
        self.assertIn('equity-method investments', text)
        self.assertNotIn('—', text.split('### Enterprise value to value per share')[1].split('###')[0])

    def test_assumption_references_for_the_new_fields_are_accepted(self):
        locked, _ = pin_reported_inputs(plan({'revenue': 1, 'net_debt': 0, 'other_equity_claims': 0, 'shares_outstanding': 1}),
                                        [facts_source({**TERM, **LEASES})])
        self.assertTrue(locked['source_refs']['dilutive_shares'][0].startswith('assumption:'))
        pipeline._validate_plan_sources(locked, [{'id': 'S2'}])

    def test_companyfacts_keeps_conversion_share_counts(self):
        source = facts_source({**TERM}, periods={'IncrementalCommonSharesAttributableToConversionOfPreferredStock': 68})
        kept = [item for item in source['data']['reported_facts']
                if item['concept'] == 'us-gaap:IncrementalCommonSharesAttributableToConversionOfPreferredStock']
        self.assertEqual([(item['unit'], item['normalized_value']) for item in kept], [('xbrli:shares', 68)])


if __name__ == '__main__':
    unittest.main()

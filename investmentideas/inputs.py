"""Pin reported inputs to dated primary inline or SEC entity-wide API facts.

This module performs accounting arithmetic and applies explicitly disclosed house
classification policies. It never supplies missing reported quantities as zero,
changes forecasts, or asks a language model to reconcile an input bridge.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import math
import re

from . import roic

REVENUE = {'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax',
           'us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax',
           'us-gaap:Revenues', 'us-gaap:SalesRevenueNet'}
SHARES = {'dei:EntityCommonStockSharesOutstanding', 'us-gaap:CommonStockSharesOutstanding'}
# Reported operating lines for the anchor year. They are reference data for
# forecast continuity, never valuation inputs. A live plan (2026-11-14) put
# Apple's R&D at $68-96 billion against $45.8 billion reported, because no
# reported expense level was in front of the forecast author. Line names match
# the operating-model expense keys where one exists.
OPERATING_REFERENCE = (
    ('gross_profit', ('us-gaap:GrossProfit',)),
    ('cost_of_revenue', ('us-gaap:CostOfGoodsAndServicesSold', 'us-gaap:CostOfRevenue')),
    ('research_and_development', ('us-gaap:ResearchAndDevelopmentExpense',
                                  'us-gaap:ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost')),
    ('selling_general_administrative', ('us-gaap:SellingGeneralAndAdministrativeExpense',)),
    ('selling_and_marketing', ('us-gaap:SellingAndMarketingExpense', 'us-gaap:MarketingExpense')),
    ('general_and_administrative', ('us-gaap:GeneralAndAdministrativeExpense',)),
    # Operating expenses exclude cost of revenue; CostsAndExpenses includes it.
    ('operating_expenses', ('us-gaap:OperatingExpenses',)),
    ('total_costs_and_expenses', ('us-gaap:CostsAndExpenses',)),
    ('operating_income', ('us-gaap:OperatingIncomeLoss',)),
)
SIGNED_REFERENCE_LINES = {'gross_profit', 'operating_income'}
LINE_SCOPES = {
    'cost_of_revenue': "the issuer's cost of revenue; its scope follows the issuer's own definition",
    'operating_expenses': 'operating expenses excluding cost of revenue',
    'total_costs_and_expenses': 'total costs and expenses, including cost of revenue',
}


def _period_line(facts, concepts, start, end, currency, signed, conflicts, label, *, annual=True):
    """The first concept with observations for the exact period decides the line.

    A conflicted preferred concept leaves the line out rather than falling back
    to a concept with a different scope.
    """
    for concept in concepts:
        items = [item for item in facts if item['concept'] == concept and item['parsed_start'] == start and
                 item['parsed_end'] == end and item['unit'] == 'iso4217:' + currency and
                 (not annual or item.get('fact_origin') != 'sec_companyfacts' or item.get('form') not in {'10-Q', '10-Q/A'}) and
                 (signed or item['value'] >= 0)]
        if not items:
            continue
        chosen = _unique(items, label, conflicts)
        return (concept, chosen) if chosen else None
    return None


def _larger_rnd(facts, concepts, start, end, currency, conflicts, label):
    """R&D: the larger of the inclusive concept and its part excluding acquired in-process cost.

    ResearchAndDevelopmentExpense includes acquired in-process R&D, so it cannot be
    smaller than its part. Johnson & Johnson tags only its in-process charge with the
    inclusive concept ($0.1 billion against $14.7 billion of R&D excluding it, fiscal
    2025), so taking the first concept left a plan's R&D near 0.1% of revenue. The ROIC
    table applies the same rule. A conflicted inclusive concept still leaves the line
    out, and a conflicted part only counts when the inclusive concept is absent.
    """
    inclusive, part = concepts
    before = len(conflicts)
    first = _period_line(facts, (inclusive,), start, end, currency, False, conflicts, label)
    if first is None and len(conflicts) > before:
        return None
    scratch = []
    second = _period_line(facts, (part,), start, end, currency, False, scratch, label)
    if first is None:
        conflicts.extend(scratch)
    found = [item for item in (first, second) if item]
    return max(found, key=lambda item: item[1]['value']) if found else None


def _operating_reference(facts, start, end, currency, revenue, revenue_concept=None):
    """Unique reported operating lines for the revenue anchor's exact fiscal year.

    Also reports the latest year-to-date operating margin that begins the day
    after the anchor, because year 1 of a forecast is that fiscal year.
    """
    lines, conflicts = {}, []
    for name, concepts in OPERATING_REFERENCE:
        label = 'reported ' + name.replace('_', ' ')
        if name == 'research_and_development':
            found = _larger_rnd(facts, concepts, start, end, currency, conflicts, label)
        else:
            found = _period_line(facts, concepts, start, end, currency, name in SIGNED_REFERENCE_LINES, conflicts, label)
        if found:
            concept, chosen = found
            lines[name] = {'value': _json_number(chosen['value']), 'concept': concept, 'basis': 'reported',
                           'source_refs': chosen['source_refs']}
    def derived(name, value, parts, basis):
        lines[name] = {'value': _json_number(value), 'concept': None, 'basis': basis,
                       'source_refs': sorted({ref for part in parts for ref in lines[part]['source_refs']})}
    if 'selling_general_administrative' not in lines and {'selling_and_marketing', 'general_and_administrative'} <= set(lines):
        derived('selling_general_administrative',
                Decimal(str(lines['selling_and_marketing']['value'])) + Decimal(str(lines['general_and_administrative']['value'])),
                ('selling_and_marketing', 'general_and_administrative'),
                'derived: sum of reported selling and marketing and general and administrative')
    if 'operating_expenses' not in lines and {'total_costs_and_expenses', 'cost_of_revenue'} <= set(lines):
        derived('operating_expenses',
                Decimal(str(lines['total_costs_and_expenses']['value'])) - Decimal(str(lines['cost_of_revenue']['value'])),
                ('total_costs_and_expenses', 'cost_of_revenue'),
                'derived: reported total costs and expenses less reported cost of revenue')
    if 'gross_profit' not in lines and 'cost_of_revenue' in lines and revenue:
        lines['gross_profit'] = {'value': _json_number(revenue - Decimal(str(lines['cost_of_revenue']['value']))),
                                 'concept': None, 'source_refs': lines['cost_of_revenue']['source_refs'],
                                 'basis': "derived: revenue less reported cost of revenue; scope follows the issuer's "
                                          'cost-of-revenue definition'}
    for name, scope in LINE_SCOPES.items():
        if name in lines:
            lines[name]['scope'] = scope
    output = {'period': {'start_date': start.isoformat(), 'end_date': end.isoformat()}, 'currency': currency,
              'lines': lines, 'conflicts': conflicts,
              'basis': ('Reported operating lines for the revenue anchor year. Reference for year-1 forecast continuity '
                        'only; not used in the valuation.')}
    if 'operating_income' in lines and revenue:
        output['operating_margin'] = float(Decimal(str(lines['operating_income']['value'])) / revenue)
    if 'gross_profit' in lines and revenue:
        output['gross_margin'] = float(Decimal(str(lines['gross_profit']['value'])) / revenue)
    following = end + timedelta(days=1)
    concepts = (revenue_concept,) if revenue_concept else tuple(sorted(REVENUE))
    ends = sorted({item['parsed_end'] for item in facts if item['concept'] in concepts and
                   item['parsed_start'] == following and item['parsed_end'] and item['parsed_end'] > following and
                   (item['parsed_end'] - following).days < 360 and item['unit'] == 'iso4217:' + currency}, reverse=True)
    for latest in ends:
        ytd_conflicts = []
        sales = _period_line(facts, concepts, following, latest, currency, False, ytd_conflicts,
                             'year-to-date revenue', annual=False)
        income = _period_line(facts, ('us-gaap:OperatingIncomeLoss',), following, latest, currency, True, ytd_conflicts,
                              'year-to-date operating income', annual=False)
        if sales and income and sales[1]['value'] > 0:
            output['year_to_date'] = {
                'period': {'start_date': following.isoformat(), 'end_date': latest.isoformat()},
                'revenue': _json_number(sales[1]['value']), 'operating_income': _json_number(income[1]['value']),
                'operating_margin': float(income[1]['value'] / sales[1]['value']),
                'source_refs': sorted(set(sales[1]['source_refs']) | set(income[1]['source_refs'])),
                'basis': 'Reported year to date for the fiscal year after the anchor: the first forecast year.'}
            break
        conflicts.extend(ytd_conflicts)
    return output
# Scope follows the FASB US GAAP 2026 documentation, not similar-looking names:
# https://xbrl.fasb.org/us-gaap/2026/elts/us-gaap-doc-2026.xml
# In particular LongTermDebt includes current maturities, while the unsuffixed
# LongTermDebtAndCapitalLeaseObligations is NONCURRENT.
CASH = {
    'cash_and_cash_equivalents': 'CashAndCashEquivalentsAtCarryingValue',
    'cash_and_short_term_investments': 'CashCashEquivalentsAndShortTermInvestments',
    'short_term_investments': 'ShortTermInvestments',
    'marketable_securities': 'MarketableSecurities',
    'current_marketable_securities': 'MarketableSecuritiesCurrent',
    'noncurrent_marketable_securities': 'MarketableSecuritiesNoncurrent',
    # Fallback only, when none of the investment categories above is reported:
    # NVIDIA tags its $52.0 billion balance-sheet marketable securities line with
    # an extension that includes equity securities, leaving only cash selected.
    'available_for_sale_debt_securities': 'AvailableForSaleSecuritiesDebtSecurities',
}
# Reported equity securities stay outside the selected-asset policy; their
# amount is shown when the AFS fallback is used, so the exclusion is visible.
EXCLUDED_EQUITY_SECURITIES = ('EquitySecuritiesFvNi', 'EquitySecuritiesFvNiCurrentAndNoncurrent')
AFS_POLICY = ('Explicit house policy: no balance-sheet marketable-securities or short-term-investment concept is reported '
              'on the date, so available-for-sale debt securities (us-gaap:AvailableForSaleSecuritiesDebtSecurities) are '
              'selected. Equity securities and other investments outside that concept are excluded. The available-for-sale '
              'total can include securities the issuer reports within cash equivalents; that overlap is not separately '
              'tagged and is not removed.')
# LongTermDebt above the balance-sheet carrying amounts by at most this share of
# them is read as principal before unamortized discount, issuance costs and
# hedge adjustments. Among the 13 issuers swept on 2026-11-15, reported
# unamortized balances ran from 0.3% (Apple) to 2.7% (Microsoft, after its
# Activision debt exchange) of carrying long-term debt; Amazon's LongTermDebt
# exceeds its carrying parts by 0.64% and Home Depot's by 2.2%. A larger gap is
# more than a discount and stays a conflict, and so does a gap under 0.1%, below
# the smallest unamortized balance seen (Apple's 0.34%): that is a disagreement
# between reported figures, not a discount.
PRINCIPAL_TOLERANCE = Decimal('0.03')
PRINCIPAL_FLOOR = Decimal('0.001')
PRINCIPAL_POLICY = ('Explicit house reading: reported LongTermDebt exceeds the balance-sheet carrying amounts by 0.1% to 3% '
                    'of them, the range of unamortized discount, issuance costs and hedge adjustments, so it is read '
                    'as principal and the carrying amounts are used. The difference is disclosed in the bridge.')
DEBT = {
    'total_debt_and_finance_leases': 'DebtAndCapitalLeaseObligations',
    'financial_debt': 'DebtLongtermAndShorttermCombinedAmount',
    'current_debt_and_finance_leases': 'DebtCurrent',
    'short_term_borrowings': 'ShortTermBorrowings',
    'commercial_paper': 'CommercialPaper',
    'other_short_term_borrowings': 'OtherShortTermBorrowings',
    'term_debt': 'LongTermDebt',
    'current_term_debt': 'LongTermDebtCurrent',
    'noncurrent_term_debt': 'LongTermDebtNoncurrent',
    'term_debt_and_finance_leases': 'LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities',
    'current_term_debt_and_finance_leases': 'LongTermDebtAndCapitalLeaseObligationsCurrent',
    'noncurrent_term_debt_and_finance_leases': 'LongTermDebtAndCapitalLeaseObligations',
}
FINANCE_LEASES = {'total': 'FinanceLeaseLiability',
                 'current': 'FinanceLeaseLiabilityCurrent',
                 'noncurrent': 'FinanceLeaseLiabilityNoncurrent'}
# Financing with initial terms under one year that the taxonomy keeps outside
# long-term debt. Only ShortTermBorrowings is a complete category total.
# CommercialPaper and OtherShortTermBorrowings ("classified as other") are
# disjoint identified categories. A positive amount under any concept below
# cannot be placed without issuer disclosure, so it stops an assumption-based
# bridge instead of being added or ignored.
UNCLASSIFIED_FINANCING = {
    'short_term_bank_loans': 'ShortTermBankLoansAndNotesPayable',
    'short_term_nonbank_loans': 'ShortTermNonBankLoansAndNotesPayable',
    'bank_overdrafts': 'BankOverdrafts',
    'line_of_credit': 'LineOfCredit',
    'current_lines_of_credit': 'LinesOfCreditCurrent',
    'warehouse_borrowings': 'WarehouseAgreementBorrowings',
}
LEGACY_LEASES = {'total': 'CapitalLeaseObligations', 'current': 'CapitalLeaseObligationsCurrent',
                 'noncurrent': 'CapitalLeaseObligationsNoncurrent'}
# DebtInstrumentCarryingAmount is BEFORE unamortized discount and issuance
# costs; LongTermDebt and its current/noncurrent parts are AFTER them.
DEBT_RECONCILIATION = {
    'gross_long_term_debt': 'DebtInstrumentCarryingAmount',
    'unamortized_discount_and_issuance_costs': 'DebtInstrumentUnamortizedDiscountPremiumAndDebtIssuanceCostsNet',
}
CASH_POLICY = ('Explicit house modeling policy: selected nonoperating assets comprise reported cash and '
               'cash equivalents plus directly reported marketable securities or short-term investments '
               'on the annual balance-sheet date. Inclusive subtotals replace their components. Missing '
               'investment categories are excluded from this selected-asset policy, not reported as zero. '
               'Long-term investments without a marketable classification are excluded here; the equity '
               'bridge adds separately the investment lines the return-on-capital table removes from invested '
               'capital. This does not '
               'establish that all cash is economically surplus or required operating cash is zero.')
CLAIMS_POLICY = ('Explicit house assumption: additional unidentified equity claims are zero. This is '
                 'not a reported fact or evidence that no additional claims exist; identified finance '
                 'lease liabilities are deducted once, either within a selected combined debt subtotal '
                 'or separately as other equity claims.')
LEASE_POLICY = ('Finance leases are deducted once: separately in other equity claims when financial debt excludes them, '
                'or within an explicitly selected combined debt-and-finance-lease subtotal. Operating lease liabilities '
                'are not added under the unadjusted operating-EBIT convention; capitalizing them would require a consistent EBIT and reinvestment restatement.')
SHORT_TERM_POLICY = ('Explicit house assumption: short-term financing beyond the identified reported categories is '
                     'zero. The SEC entity-wide structured record was scanned for every modelled borrowing and '
                     'finance-lease concept on the balance-sheet date and reported no other short-term borrowing '
                     'amount. An absent concept is not a reported zero; undisclosed or differently tagged '
                     'borrowings would raise debt and lower equity value.')
LEASE_ABSENCE_POLICY = ('Explicit house assumption: finance lease liabilities are zero because the scanned SEC '
                        'entity-wide structured record reports no finance or capital lease liability concept on '
                        'the balance-sheet date. An absent concept is not a reported zero.')
# Procter & Gamble tags its balance-sheet cash only with the cash-flow statement
# total, which includes restricted cash. Where no restricted cash is reported on
# the date the two are the same amount, so the total stands in for cash.
CASH_INCLUDING_RESTRICTED = 'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents'
RESTRICTED_CASH = ('RestrictedCash', 'RestrictedCashCurrent', 'RestrictedCashNoncurrent',
                   'RestrictedCashAndCashEquivalentsAtCarryingValue', 'RestrictedCashAndCashEquivalentsCurrent',
                   'RestrictedCashAndCashEquivalentsNoncurrent', 'RestrictedCashAndInvestments',
                   'RestrictedCashAndInvestmentsCurrent', 'RestrictedCashAndInvestmentsNoncurrent')
RESTRICTED_CASH_POLICY = ('Explicit house reading: the issuer tags no cash and cash equivalents concept on the balance-sheet '
                          'date, only cash, cash equivalents and restricted cash '
                          '(us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents), and reports no '
                          'restricted cash on that date, so that total is taken as cash. Restricted cash the issuer does '
                          'not tag would be counted as cash.')
# Non-operating investments added to equity value. The lines and their precedence
# are those roic.invested_capital removes from invested capital, so the two agree.
NONOPERATING_FIELDS = ('long_term_investments', 'securities_noncurrent', *roic.NESTED_INVESTMENTS,
                       'equity_securities_marketable')
# Marketable equity at fair value: the roic tag, then the current-and-noncurrent total.
MARKETABLE_EQUITY = tuple(dict.fromkeys((*roic.INSTANTS['equity_securities_marketable'], *EXCLUDED_EQUITY_SECURITIES)))
EQUITY_METHOD_QUOTED_VALUE = 'EquityMethodInvestmentQuotedMarketValue'
# Selected cash categories that can contain noncurrent securities.
NONCURRENT_CASH_CATEGORIES = ('noncurrent_marketable_securities', 'marketable_securities', 'available_for_sale_debt_securities')
# Investments maturing within a year; equity securities cannot be among them.
OTHER_SHORT_TERM_INVESTMENTS = 'OtherShortTermInvestments'
# Selected cash categories that exclude equity securities by definition.
EQUITY_FREE_CASH_CATEGORIES = frozenset({'cash_and_cash_equivalents', 'cash_including_restricted_cash',
                                         'available_for_sale_debt_securities'})
NONOPERATING_POLICY = (
    'Explicit house policy: non-operating investments are added to equity value at carrying value on the balance-sheet '
    'date of net debt. The lines are those the return-on-capital table removes from invested capital: the '
    'balance-sheet investments line (us-gaap:LongTermInvestments) where it is tagged, otherwise noncurrent securities '
    'plus the larger of other long-term investments and equity-method investments plus non-marketable equity '
    'securities, which never overlap (ASC 321 excludes equity-method investments) but can both sit inside other '
    'long-term investments. Marketable equity securities (us-gaap:EquitySecuritiesFvNi) are added when the selected '
    'cash holds only cash, cash equivalents, debt securities and investments with maturities, and no investment line '
    'added can contain them. A line that may '
    'already be in the selected cash is not added again. Quoted market values are not used. Any of these investments '
    'the business needs to operate would lower equity value.')
NONOPERATING_ABSENCE_POLICY = ('Explicit house assumption: no non-operating investment concept is reported on the '
                               'balance-sheet date, so non-operating investments are zero. An absent concept is not a '
                               'reported zero.')
# Claims on enterprise value beside debt and finance leases, at carrying value.
NONCONTROLLING = ('MinorityInterest', 'NonredeemableNoncontrollingInterest')
REDEEMABLE_NONCONTROLLING = ('RedeemableNoncontrollingInterestEquityCarryingAmount',)
TOTAL_EQUITY = ('StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest', 'StockholdersEquity')
PREFERRED = ('PreferredStockValue', 'PreferredStockValueOutstanding')
# Defined-benefit pension and other postretirement liabilities recognized on the
# balance sheet, tried in this order. Each part is a current-and-noncurrent total,
# or noncurrent plus current. Johnson & Johnson tags its whole employee-related
# obligations line, which also holds postemployment benefits, with the combined
# concept, so the separate pension and postretirement concepts come first. AT&T
# tags its pension and postretirement obligation only as postemployment benefits.
BENEFIT_GROUPS = (
    ('pension_and_other_postretirement_plans', (
        ('pension_plans', 'DefinedBenefitPensionPlanCurrentAndNoncurrentLiabilities',
         'DefinedBenefitPensionPlanLiabilitiesCurrent', 'DefinedBenefitPensionPlanLiabilitiesNoncurrent'),
        ('other_postretirement_plans', 'OtherPostretirementDefinedBenefitPlanLiabilitiesCurrentAndNoncurrent',
         'OtherPostretirementDefinedBenefitPlanLiabilitiesCurrent', 'OtherPostretirementDefinedBenefitPlanLiabilitiesNoncurrent'))),
    ('combined_pension_and_postretirement', (
        ('combined_plans', None, 'PensionAndOtherPostretirementDefinedBenefitPlansCurrentLiabilities',
         'PensionAndOtherPostretirementDefinedBenefitPlansLiabilitiesNoncurrent'),)),
    ('combined_including_postemployment', (
        ('combined_plans', None, 'PensionAndOtherPostretirementAndPostemploymentBenefitPlansLiabilitiesCurrent',
         'PensionAndOtherPostretirementAndPostemploymentBenefitPlansLiabilitiesNoncurrent'),)),
    ('postemployment_benefits', (
        ('postemployment', 'PostemploymentBenefitsLiabilityCurrentAndNoncurrent', 'PostemploymentBenefitsLiabilityCurrent',
         'PostemploymentBenefitsLiabilityNoncurrent'),)),
)
BENEFIT_ASSETS = roic.INSTANTS['pension_assets']
CLAIMS_COMPONENT_POLICY = (
    'Explicit house policy: noncontrolling interests (us-gaap:MinorityInterest or NonredeemableNoncontrollingInterest, '
    'otherwise total equity less parent equity), redeemable noncontrolling interests and preferred stock are deducted at '
    'carrying value on the balance-sheet date of net debt; market values are not used. A category with no reported '
    'concept is assumed zero; an absent concept is not a reported zero. Preferred stock whose conversion the filing '
    'counts in diluted shares is deducted through the share count instead.')
PENSION_POLICY = (
    'Explicit house policy: defined-benefit pension and other postretirement obligations are deducted as the recognized '
    'liabilities of underfunded plans, gross and before tax. Damodaran deducts underfunded pension and health care '
    'obligations when moving from firm value to equity value and keeps them out of the cost of capital. Recognized '
    'assets of overfunded plans are not credited, either as an asset or against other plans\' deficits: his '
    'conservative rule for excess plan assets assumes the cost of reclaiming them is prohibitive ("Dealing with Cash, '
    'Cross Holdings and Other Non-Operating Assets", 2005). The tax deduction on future '
    'contributions is not credited; at a marginal tax rate t it would raise equity value by t times the deduction. '
    'Where no defined-benefit concept is tagged, a reported postemployment benefit obligation is deducted as the '
    'retirement obligation; AT&T reports its pension and postretirement obligation that way.')
# Weighted-average shares for the anchor fiscal year (financial-context concepts)
# and the conversion shares that can be inside the diluted count.
DILUTION = {
    'diluted_weighted_average': 'WeightedAverageNumberOfDilutedSharesOutstanding',
    'basic_weighted_average': 'WeightedAverageNumberOfSharesOutstandingBasic',
    'basic_and_diluted_weighted_average': 'WeightedAverageNumberOfShareOutstandingBasicAndDiluted',
    'preferred_conversion_shares': 'IncrementalCommonSharesAttributableToConversionOfPreferredStock',
    'debt_conversion_shares': 'IncrementalCommonSharesAttributableToConversionOfDebtSecurities',
}
# A share count more than this factor away from the year's basic average signals a
# split or large issuance after the fiscal year; the increment is then scaled.
DILUTION_SCALE_BAND = (Decimal(2) / 3, Decimal(3) / 2)
SHARE_COUNT_CONCEPTS = frozenset('us-gaap:' + DILUTION[key] for key in ('preferred_conversion_shares', 'debt_conversion_shares'))
DILUTION_POLICY = (
    'Value per share divides equity value by outstanding shares plus the dilutive increment of the latest fiscal year: '
    'reported diluted less basic weighted-average shares, which counts employee awards by the treasury-stock method and '
    'convertible securities if converted. Conversion shares of debt that the filing reports separately are left out, '
    'because that debt is already deducted in net debt. The increment is a fiscal-year average applied to the later '
    'outstanding count. Stock-based compensation stays an operating expense.')
DILUTION_ABSENCE_POLICY = ('Explicit house assumption: no diluted or basic weighted-average share count is reported for '
                           'the anchor fiscal year, so the dilutive increment is zero.')
# Concepts whose absence on the balance-sheet date supports the two assumptions
# above. A companyfacts source attests the scan in ``selection``.
FINANCING_ABSENCE_SCAN = frozenset(
    'us-gaap:' + concept for group in (DEBT, FINANCE_LEASES, UNCLASSIFIED_FINANCING, LEGACY_LEASES)
    for concept in group.values())
EQUITY_BRIDGE_CONCEPTS = frozenset(
    'us-gaap:' + concept for concept in (
        CASH_INCLUDING_RESTRICTED, *RESTRICTED_CASH, OTHER_SHORT_TERM_INVESTMENTS, EQUITY_METHOD_QUOTED_VALUE, *NONCONTROLLING,
        *REDEEMABLE_NONCONTROLLING, *TOTAL_EQUITY, *PREFERRED, *BENEFIT_ASSETS,
        *(concept for field in NONOPERATING_FIELDS for concept in roic.INSTANTS[field]),
        *(concept for _, parts in BENEFIT_GROUPS for part in parts for concept in part[1:] if concept),
        DILUTION['preferred_conversion_shares'], DILUTION['debt_conversion_shares']))
PRIMARY_INPUT_CONCEPTS = REVENUE | SHARES | {
    'us-gaap:' + concept for group in (CASH, DEBT, FINANCE_LEASES, UNCLASSIFIED_FINANCING, LEGACY_LEASES,
                                        DEBT_RECONCILIATION) for concept in group.values()
} | {'us-gaap:' + concept for concept in EXCLUDED_EQUITY_SECURITIES} | EQUITY_BRIDGE_CONCEPTS
STRUCTURED_FORMS = frozenset({'10-K', '10-K/A', '10-Q', '10-Q/A', '20-F', '20-F/A', '40-F', '40-F/A'})
ANNUAL_REPORT_FORMS = STRUCTURED_FORMS - {'10-Q', '10-Q/A'}
# Reported inputs the equity bridge needs. The first four are essential; the
# last two default to zero in plans saved before they existed.
REPORTED_FIELDS = ('revenue', 'net_debt', 'other_equity_claims', 'shares_outstanding', 'nonoperating_assets',
                   'dilutive_shares')
FIELD_PREREQUISITES = {'nonoperating_assets': 'net_debt', 'dilutive_shares': 'revenue'}
# The annual report's cover count stands in for a balance-sheet count within this
# many days after the balance-sheet date (Coca-Cola's 10-K cover is dated 49 days after).
SHARE_DATE_WINDOW_DAYS = 60
CLASS_OF_STOCK_AXIS = 'us-gaap:StatementClassOfStockAxis'
CLASS_SHARES_BASIS = ('sum of cover-page outstanding shares by share class (dei:EntityCommonStockSharesOutstanding on '
                      'us-gaap:StatementClassOfStockAxis) on one date; no entity-wide total is reported')


def _share_class(raw, origin):
    """The share-class member of a cover-page class count from an inline filing, else None.

    Meta reports outstanding shares only by class (2,214,472,367 Class A and
    341,768,886 Class B on its October 2026 10-Q cover), so no entity-wide count
    existed to pin.
    """
    dimensions = raw.get('dimensions')
    if (origin != 'inline_xbrl' or raw.get('dimension_filter') != 'class_of_stock_cover_shares' or
            raw.get('concept') != 'dei:EntityCommonStockSharesOutstanding' or not isinstance(dimensions, dict) or
            list(dimensions) != [CLASS_OF_STOCK_AXIS] or not isinstance(dimensions[CLASS_OF_STOCK_AXIS], str)):
        return None
    return dimensions[CLASS_OF_STOCK_AXIS] or None


def _class_share_total(items, conflicts, *, earliest=False):
    """The latest (or earliest) single-date sum of class cover counts, used only without an entity-wide count."""
    by_date = {}
    for item in items:
        if item['unit'] == 'xbrli:shares' and item['parsed_instant'] and item['value'] > 0:
            by_date.setdefault(item['parsed_instant'], []).append(item)
    if not by_date:
        return None
    latest = min(by_date) if earliest else max(by_date)
    totals = {}
    for item in by_date[latest]:
        totals.setdefault(item['source_id'], {}).setdefault(item['share_class'], set()).add(item['value'])
    sums = set()
    for classes in totals.values():
        if any(len(values) != 1 for values in classes.values()):
            conflicts.append('Conflicting cover-page outstanding shares for one share class; no total was selected')
            return None
        sums.add(sum(next(iter(values)) for values in classes.values()))
    if len(sums) != 1:
        conflicts.append('Cover-page share-class totals conflict between primary sources; no total was selected')
        return None
    used = by_date[latest]
    return {'value': sums.pop(), 'instant': latest.isoformat(), 'source_refs': sorted({item['source_id'] for item in used}),
            'classes': {item['share_class']: _json_number(item['value']) for item in used}}


def _cover_rounding(items):
    """Keep the cover-page count when the balance-sheet counts are that count rounded.

    ExxonMobil Holdings reported 4,079,222,444 shares on its cover and
    4,079,000,000 on its balance sheet for the same date; the company-facts
    record carries no stated precision, so the rounding is read from the
    balance-sheet figure's trailing zeros (at least a thousand). Anything else
    is left for ``_unique`` to judge.
    """
    items = list(items)
    cover = {item['value'] for item in items if item['concept'] == 'dei:EntityCommonStockSharesOutstanding'}
    others = [item for item in items if item['concept'] != 'dei:EntityCommonStockSharesOutstanding']
    if len(cover) != 1 or not others or len({item['value'] for item in items}) == 1:
        return items
    count = next(iter(cover))
    def rounded(value):
        unit = 1
        while unit < 10**9 and value % (unit * 10) == 0:
            unit *= 10
        return unit >= 1000 and abs(value - count) * 2 <= unit
    if all(item['value'] == count or rounded(item['value']) for item in others):
        return [item for item in items if item['value'] == count]
    return items


def _no_annual_report_note(statements):
    note = ('No annual report is in the primary record: every SEC filing and company fact collected for this registrant '
            'is quarterly (10-Q), so no annual revenue, balance-sheet anchor or net-debt bridge can be pinned')
    if statements:
        note += ('. The registrant\'s filing states that it is a successor registrant ("' + statements[0] + '"); the '
                 'predecessor\'s annual reports are filed under a different SEC CIK and were not collected. Use the '
                 'predecessor\'s annual report as reviewed primary evidence, or wait for the successor\'s first 10-K')
    return note


def _subtotal(items, total, current, noncurrent, label, conflicts):
    """Return a total OR both parts, never total plus parts."""
    whole, first, second = (items.get(key) for key in (total, current, noncurrent))
    if whole and first and second and whole['value'] != first['value'] + second['value']:
        conflicts.append(f'{label} total conflicts with current plus noncurrent components')
        return None
    if whole and any(part and part['value'] > whole['value'] for part in (first, second)):
        conflicts.append(f'{label} component exceeds the reported total')
        return None
    return {total: whole} if whole else {current: first, noncurrent: second} if first and second else None


def _amount(components):
    return sum((item['value'] for item in components.values()), Decimal(0))


def _components_public(components):
    return {name: {'value': _json_number(item['value']), 'source_refs': item['source_refs'],
                   'facts': item['facts'],
                   **({'classification_evidence': item['classification_evidence']}
                      if item.get('classification_evidence') else {}),
                   **({'reconciliation': item['reconciliation']} if item.get('reconciliation') else {})}
            for name, item in components.items()}


def _reconcile_gross_term_debt(items, reconciliation, leases=None):
    """Return items without a LongTermDebt fact that is reported gross principal.

    Some issuers tag the debt-note total before unamortized discount and
    issuance costs as LongTermDebt. It is set aside on exact reported
    arithmetic: it equals DebtInstrumentCarryingAmount, and less the reported
    unamortized amount it equals current plus noncurrent carrying debt.
    Otherwise it is set aside when it exceeds the balance-sheet carrying amounts
    by ``PRINCIPAL_FLOOR`` to ``PRINCIPAL_TOLERANCE`` of them, and the difference is disclosed:
    Amazon reports no unamortized amount on the date, and Home Depot reports its
    carrying debt only together with finance leases, which are then subtracted.
    The balance-sheet carrying parts are used either way. Nothing is estimated.
    """
    whole, first, second = (items.get(key) for key in ('term_debt', 'current_term_debt', 'noncurrent_term_debt'))
    gross = reconciliation.get('gross_long_term_debt')
    unamortized = reconciliation.get('unamortized_discount_and_issuance_costs')
    if not whole:
        return items, None
    adjusted = dict(items)
    adjusted['term_debt'] = None
    if first and second and gross and unamortized:
        carrying = first['value'] + second['value']
        if (whole['value'] != carrying and gross['value'] == whole['value'] and
                whole['value'] - abs(unamortized['value']) == carrying):
            return adjusted, {
                'basis': 'Reported LongTermDebt equals gross principal before unamortized discount and issuance costs; '
                         'current plus noncurrent carrying amounts are used',
                'gross_principal': _json_number(whole['value']),
                'unamortized_discount_and_issuance_costs': _json_number(abs(unamortized['value'])),
                'carrying_amount': _json_number(carrying),
                'source_facts': [fact for item in (whole, gross, unamortized) for fact in item['facts']]}
    lease_parts = (leases or {}).get('reconciled')
    combined = (items.get('current_term_debt_and_finance_leases'), items.get('noncurrent_term_debt_and_finance_leases'))
    if first and second:
        carrying, parts = first['value'] + second['value'], (first, second)
        scope = 'current plus noncurrent long-term debt'
    elif all(combined) and lease_parts:
        carrying = combined[0]['value'] + combined[1]['value'] - _amount(lease_parts)
        parts = (*combined, *lease_parts.values())
        scope = 'current plus noncurrent long-term debt and finance leases, less reported finance lease liabilities'
    else:
        return items, None
    difference = whole['value'] - carrying
    if carrying <= 0 or not PRINCIPAL_FLOOR * carrying <= difference <= PRINCIPAL_TOLERANCE * carrying:
        return items, None
    return adjusted, {
        'basis': f'Reported LongTermDebt exceeds the balance-sheet carrying amount ({scope}) by '
                 f'{float(difference / carrying):.2%} of it, within the {float(PRINCIPAL_TOLERANCE):.0%} bound for '
                 'unamortized discount, issuance costs and hedge adjustments; it is read as principal and the carrying '
                 'amounts are used',
        'policy': PRINCIPAL_POLICY,
        'gross_principal': _json_number(whole['value']),
        'carrying_amount': _json_number(carrying),
        'difference': _json_number(difference),
        **({'reported_unamortized_discount_and_issuance_costs': _json_number(abs(unamortized['value']))} if unamortized else {}),
        'source_facts': [fact for item in (whole, *parts) for fact in item['facts']]}


def _validate_scopes(items, scopes, label, conflicts):
    """Check every known disjoint subset and exact partition against each total.

    Scope bits encode accounting categories, not display tags. Incomplete
    subsets (commercial paper within short-term borrowing) provide bounds but
    never establish that their enclosing liability category is complete.

    Amounts compare within the rounding their own stated inline precision
    allows: half a unit of each amount's ``decimals``, summed over the total and
    the parts compared with it. Procter & Gamble reports current debt of $11,296
    million and parts of $6,457, $4,837 and $3 million, each rounded to millions.
    Amounts without a stated precision compare exactly.
    """
    rows = [(key, item['value'], *scopes[key], _half_unit(item)) for key, item in items.items()
            if item and key in scopes]
    for key, total, enclosing, enclosing_complete, total_slack in rows:
        if not enclosing_complete:
            continue
        # State: (covered scope, exact partition) -> {rounding slack of the parts: (low, high)}.
        states = {(0, True): {Decimal(0): (Decimal(0), Decimal(0))}}
        for part_key, value, scope, complete, slack in rows:
            if key == part_key or scope & enclosing != scope:
                continue
            for (covered, exact), bounds in list(states.items()):
                if covered & scope:
                    continue
                state = (covered | scope, exact and complete)
                merged = states.setdefault(state, {})
                for parts_slack, (low, high) in list(bounds.items()):
                    combined, new_low, new_high = parts_slack + slack, low + value, high + value
                    old = merged.get(combined)
                    merged[combined] = (min(old[0], new_low), max(old[1], new_high)) if old else (new_low, new_high)
        def inconsistent(covered, exact, parts_slack, low, high):
            tolerance = total_slack + parts_slack
            return (high > total + tolerance or
                    (covered == enclosing and exact and (abs(low - total) > tolerance or abs(high - total) > tolerance)))
        if any(inconsistent(covered, exact, parts_slack, low, high)
               for (covered, exact), bounds in states.items() if covered
               for parts_slack, (low, high) in bounds.items()):
            conflicts.append(f'{label} {key} conflicts with reported disjoint components or subtotals')


def _classified_debt(sources, items, anchor, classification):
    """Accept exact primary scope evidence for a same-date reported amount.

    ``financial_debt_total`` requires method ``primary_debt_table_total`` or
    ``manual_primary_review``, a matching ``normalized_decimal``, and an exact
    same-source excerpt. Both methods require positive whole-financing scope;
    the automatic method records ``scope_evidence_type`` and also requires a
    Total debt label and anchor year. Ingestion verifies table-year/value-column
    alignment. Manual review is explicit. No declared quantity replaces a fact.
    """
    allowed = {'us-gaap:' + DEBT[key] for key in (
        'financial_debt', 'term_debt', 'current_term_debt', 'noncurrent_term_debt',
        'short_term_borrowings', 'commercial_paper')}
    for source in sources:
        if (not isinstance(source, dict) or not (source.get('kind') == 'regulatory_primary' or
                                                 source.get('source_tier') == 'regulatory_primary')):
            continue
        data = source.get('data', {})
        if not isinstance(data, dict):
            continue
        declarations = data.get('reported_input_classifications', {})
        declaration = declarations.get(classification) if isinstance(declarations, dict) else None
        if not isinstance(declaration, dict) or declaration.get('instant') != anchor.isoformat():
            continue
        method = declaration.get('method', 'manual_primary_review')
        if method not in {'manual_primary_review', 'primary_debt_table_total'}:
            continue
        if method == 'manual_primary_review' and declaration.get('reviewed') is not True:
            continue
        concept = declaration.get('concept')
        if classification == 'short_term_financing_total':
            if concept != 'us-gaap:CommercialPaper' or method != 'manual_primary_review':
                continue
        elif concept not in allowed or 'method' not in declaration:
            continue
        candidates = [item for item in items.values() if item and item['concept'] == concept and
                      source.get('id') in item['source_refs']]
        if not candidates:
            continue
        item = candidates[0]
        if classification == 'financial_debt_total' and _number(declaration.get('normalized_decimal')) != item['value']:
            continue
        excerpt = declaration.get('source_excerpt')
        text = [data.get('excerpt', ''), data.get('excerpts', []), source.get('primary_focus_excerpts', [])]
        def strings(value):
            if isinstance(value, str):
                return [value]
            if isinstance(value, (list, tuple)):
                return [part for value_item in value for part in strings(value_item)]
            if isinstance(value, dict):
                return [part for value_item in value.values() for part in strings(value_item)]
            return []
        normalized = ' '.join(excerpt.split()) if isinstance(excerpt, str) else ''
        if len(normalized) < 20 or not any(normalized in ' '.join(part.split()) for part in strings(text)):
            continue
        if classification == 'financial_debt_total' and re.search(
                r'\b(?:components|composition|summary|details|breakdown)\s+of\s+(?:our\s+)?long[\s-]+term\s+debt\b|'
                r'\blong[\s-]+term\s+debt\s+(?:consists?|consisted|comprises?|comprised)\b|'
                r'\b(?:short[\s-]+term\s+(?:debt|borrowings?|financing)|commercial\s+paper)\b.{0,100}\bseparately\b', normalized, re.I):
            # A reviewed flag cannot enlarge an explicitly narrower primary
            # subtotal into total financial financing.
            continue
        if classification == 'financial_debt_total':
            short_pattern = r'\bshort[\s-]+term\s+(?:debt|borrowings?|financing)\b'
            date_patterns = [re.escape(anchor.isoformat())]
            for month in (anchor.strftime('%B'), anchor.strftime('%b')):
                date_patterns.append(re.escape(month) + r'\.?\s+' + str(anchor.day) + r',?\s+' + str(anchor.year))
            exact_date = r'\b(?:' + '|'.join(date_patterns) + r')\b'
            no_short = r'\bno\s+short[\s-]+term\s+(?:debt|borrowings?|financing)\b[^.;]{0,160}\boutstanding\b'
            qualified = bool(re.search(r'\b(?:except|but|other\s+than)\b', normalized, re.I))
            dated_scope = not qualified and bool(re.search(
                exact_date + r'[^.;]{0,160}' + no_short + '|' + no_short + r'[^.;]{0,160}' + exact_date,
                normalized, re.I))
            amount_after_label = r'(?:\s*\([^)]{1,80}\))?\s*[:$]?\s*[-(]?\d'
            categories_scope = bool(re.search(short_pattern + amount_after_label, normalized, re.I) and
                                    re.search(r'\blong[\s-]+term\s+debt\b' + amount_after_label, normalized, re.I))
            scope_type = declaration.get('scope_evidence_type')
            if method == 'primary_debt_table_total':
                if not ((scope_type == 'dated_no_short_term_financing' and dated_scope) or
                        (scope_type == 'disjoint_debt_categories' and categories_scope)):
                    continue
            elif not (dated_scope or categories_scope):
                continue
        if method == 'primary_debt_table_total' and (
                not re.search(r'\btotal\s+(?:financial\s+)?debt\b', normalized, re.I) or
                str(anchor.year) not in normalized):
            continue
        selected = dict(item)
        selected['classification_evidence'] = {'source_id': source['id'], **declaration,
                                               'method': method,
                                               'basis': 'Exact primary-disclosure scope classification; not inferred from absent tags'}
        return selected
    return None


def _cash_selection(items, conflicts):
    """Select disclosed assets; unknown investment categories are excluded by policy."""
    before = len(conflicts)
    # Scope atoms: cash=1, current marketables=2, other current investments=4,
    # noncurrent marketables=8. Broad short-term investments need not equal
    # current marketables: the former may include other current investments.
    _validate_scopes(items, {
        'cash_and_cash_equivalents': (1, True), 'cash_and_short_term_investments': (7, True),
        'short_term_investments': (6, True), 'marketable_securities': (10, True),
        'current_marketable_securities': (2, True), 'noncurrent_marketable_securities': (8, True),
    }, 'Cash selection', conflicts)
    marketable = _subtotal(items, 'marketable_securities', 'current_marketable_securities',
                          'noncurrent_marketable_securities', 'Marketable securities', conflicts)
    cash = items['cash_and_cash_equivalents']
    inclusive = items['cash_and_short_term_investments']
    short = items['short_term_investments']
    if len(conflicts) != before:
        return None
    if inclusive:
        selected = {'cash_and_short_term_investments': inclusive}
        if items['noncurrent_marketable_securities']:
            selected['noncurrent_marketable_securities'] = items['noncurrent_marketable_securities']
        return selected
    if cash:
        selected = {'cash_and_cash_equivalents': cash}
    elif items.get('cash_including_restricted_cash'):
        selected = {'cash_including_restricted_cash': items['cash_including_restricted_cash']}
    else:
        return None
    if marketable and not short:
        selected.update(marketable)
    elif short:
        selected['short_term_investments'] = short
        if items['noncurrent_marketable_securities']:
            selected['noncurrent_marketable_securities'] = items['noncurrent_marketable_securities']
    else:
        selected.update({key: items[key] for key in ('current_marketable_securities',
                        'noncurrent_marketable_securities') if items[key]})
    if len(selected) == 1 and items.get('available_for_sale_debt_securities'):
        selected['available_for_sale_debt_securities'] = items['available_for_sale_debt_securities']
    return selected


def _set_aside_narrow_totals(items, leases):
    """Exclude a whole-debt total that the issuer's own facts show is a long-term subtotal.

    DebtLongtermAndShorttermCombinedAmount and DebtAndCapitalLeaseObligations
    include short-term debt by definition. When such a fact exactly equals the
    reported long-term subtotal at its lease scope while a positive short-term
    borrowing amount is separately reported, both cannot hold. The reported
    components are used, which is always the larger debt figure, and the
    excluded total is disclosed. A reviewed classification is never set aside.
    """
    if not any(items[key] and items[key]['value'] > 0
               for key in ('short_term_borrowings', 'commercial_paper', 'other_short_term_borrowings')):
        return items, []
    def amounts(total, current, noncurrent):
        found = {items[total]['value']} if items[total] else set()
        if items[current] and items[noncurrent]:
            found.add(items[current]['value'] + items[noncurrent]['value'])
        return found
    term = amounts('term_debt', 'current_term_debt', 'noncurrent_term_debt')
    combined = amounts('term_debt_and_finance_leases', 'current_term_debt_and_finance_leases',
                       'noncurrent_term_debt_and_finance_leases')
    adjusted, excluded = dict(items), []
    for key, narrower, scope in (('financial_debt', term, 'long-term debt including current maturities'),
                                 ('total_debt_and_finance_leases', combined,
                                  'long-term debt and finance leases including current maturities')):
        item = items[key]
        if item and not item.get('classification_evidence') and item['value'] in narrower:
            adjusted[key] = None
            excluded.append({'concept': item['concept'], 'value': _json_number(item['value']),
                             'reason': 'Equals the reported ' + scope + ' while positive short-term borrowings are '
                                       'separately reported; excluded as narrower than its taxonomy scope',
                             'source_facts': item['facts']})
    return adjusted, excluded


def _current_long_term_amounts(items, leases):
    """Reported current long-term debt (and finance lease) amounts, by available scope."""
    amounts = set()
    if items['current_term_debt_and_finance_leases']:
        amounts.add(items['current_term_debt_and_finance_leases']['value'])
    if items['current_term_debt']:
        lease = leases.get('current')
        amounts.add(items['current_term_debt']['value'] + (lease['value'] if lease else Decimal(0)))
    return amounts


def _short_term_line_is_current_debt(items, leases):
    """Read a short-term borrowings fact that equals reported current debt as that total.

    Johnson & Johnson tags its balance-sheet "loans and notes payable" line, $8,495
    million, as ShortTermBorrowings, states current debt of $8.5 billion
    (DebtCurrent, rounded to hundreds of millions) and reports $2,000 million of
    current long-term debt. Short-term borrowings exclude current maturities by
    definition, so the three cannot all hold. The line equals the current-debt
    total, so it is that total: short-term borrowings are the line less current
    long-term debt and current finance leases, $6,495 million, and current debt is
    read at the line's finer stated amount. Applies only when current long-term
    debt is larger than the rounding of current debt, so no other reading fits.
    """
    short, current, term = (items.get(key) for key in ('short_term_borrowings', 'current_debt_and_finance_leases',
                                                       'current_term_debt'))
    if not (short and current and term) or short.get('classification_evidence'):
        return items, None
    lease = leases.get('current')
    maturities = term['value'] + (lease['value'] if lease else Decimal(0))
    tolerance = _half_unit(current) + _half_unit(short)
    if term['value'] <= tolerance or abs(short['value'] - current['value']) > tolerance or short['value'] < maturities:
        return items, None
    parts = [short, term, *([lease] if lease else [])]
    record = {
        'basis': 'Reported ShortTermBorrowings equals reported current debt (us-gaap:DebtCurrent) within the rounding of '
                 'its stated precision and exceeds reported current long-term debt, so the short-term borrowings line is '
                 'the current-debt total, including current maturities. Short-term borrowings are that line less reported '
                 'current long-term debt and current finance leases.',
        'short_term_borrowings_line': _json_number(short['value']),
        'reported_current_debt': _json_number(current['value']),
        'reported_current_long_term_debt': _json_number(term['value']),
        **({'reported_current_finance_leases': _json_number(lease['value'])} if lease else {}),
        'derived_short_term_borrowings': _json_number(short['value'] - maturities),
        'source_facts': [fact for item in (short, current, term, *([lease] if lease else [])) for fact in item['facts']]}
    adjusted = dict(items)
    adjusted['short_term_borrowings'] = dict(
        short, value=short['value'] - maturities, stated_decimals=None, reconciliation=record,
        source_refs=sorted({ref for item in parts for ref in item['source_refs']}),
        facts=[fact for item in parts for fact in item['facts']])
    adjusted['current_debt_and_finance_leases'] = dict(current, value=short['value'], stated_decimals=short['stated_decimals'])
    return adjusted, record


def _gross_term_observations(facts, anchor, currency, reconciliation, carrying):
    """LongTermDebt observations on the date that are gross principal beside carrying ones.

    Uber's 10-K tagged LongTermDebt at $10,600 million, its principal
    (DebtInstrumentCarryingAmount), and its later 10-Q comparatives tagged the same
    date at $10,521 million, the carrying amount. Principal less the reported
    $79 million of unamortized discount and issuance costs equals the carrying
    observation and current plus noncurrent carrying debt, so the principal
    observations are set aside, as the gross-principal reconciliation does for a
    single LongTermDebt fact. Returns (ids to exclude, disclosure) or (set(), None).
    """
    observed = [item for item in facts if item['concept'] == 'us-gaap:' + DEBT['term_debt'] and
                item['parsed_instant'] == anchor and item['unit'] == 'iso4217:' + currency and item['value'] >= 0]
    values = {item['value'] for item in observed}
    gross, unamortized = reconciliation.get('gross_long_term_debt'), reconciliation.get('unamortized_discount_and_issuance_costs')
    if len(values) != 2 or not gross or not unamortized or carrying is None:
        return set(), None
    principal, net = gross['value'], gross['value'] - abs(unamortized['value'])
    if values != {principal, net} or net != carrying or principal == net:
        return set(), None
    excluded = [item for item in observed if item['value'] == principal]
    return {id(item) for item in excluded}, {
        'basis': 'Some reported LongTermDebt observations for the date equal gross principal before unamortized discount '
                 'and issuance costs; the others equal the carrying amount, which is principal less the reported '
                 'unamortized amount and current plus noncurrent carrying debt. The principal observations are set aside.',
        'gross_principal': _json_number(principal),
        'unamortized_discount_and_issuance_costs': _json_number(abs(unamortized['value'])),
        'carrying_amount': _json_number(net),
        'set_aside_facts': [_public_fact(item) for item in excluded]}


def _debt_selection(items, leases, conflicts, *, assume_identified_short_term=False):
    """Compare complete disjoint alternatives at identical scopes before selection.

    Returns ``(components, leases_included, short_term_basis)`` or ``None``.
    Short-term financing is established, in order, by a reported or reviewed
    category total, by reported current debt that equals current long-term
    debt, or (only when the caller has verified the absence scan) by the
    disclosed identified-category assumption. A reported complete total always
    displaces the assumption. Reported current debt contradicts the assumption
    only beyond the rounding its own stated precision and that of the
    identified categories allow (Procter & Gamble: $11,296 million against
    $11,297 million of parts, each rounded to millions).
    """
    before = len(conflicts)
    # Scope atoms: commercial paper=1, other short-term borrowings=32, any
    # remaining short-term financing=64, current/noncurrent term debt=2/4,
    # current/noncurrent finance leases=8/16. The two identified short-term
    # categories are bounds within short-term financing (97), never its total.
    _validate_scopes({**items, **{'finance_lease_' + key: leases.get(key)
                                for key in ('total', 'current', 'noncurrent')}}, {
        'total_debt_and_finance_leases': (127, True), 'financial_debt': (103, True),
        'current_debt_and_finance_leases': (107, True), 'short_term_borrowings': (97, True),
        'commercial_paper': (1, True), 'other_short_term_borrowings': (32, True), 'term_debt': (6, True),
        'current_term_debt': (2, True), 'noncurrent_term_debt': (4, True),
        'term_debt_and_finance_leases': (30, True),
        'current_term_debt_and_finance_leases': (10, True),
        'noncurrent_term_debt_and_finance_leases': (20, True),
        'finance_lease_total': (24, True), 'finance_lease_current': (8, True),
        'finance_lease_noncurrent': (16, True),
    }, 'Liability', conflicts)
    term = _subtotal(items, 'term_debt', 'current_term_debt', 'noncurrent_term_debt',
                     'Long-term debt', conflicts)
    combined = _subtotal(items, 'term_debt_and_finance_leases',
                         'current_term_debt_and_finance_leases',
                         'noncurrent_term_debt_and_finance_leases',
                         'Long-term debt and finance leases', conflicts)
    short = items['short_term_borrowings']
    financial = items['financial_debt']
    lease_total = leases.get('reconciled')
    known_lease_floor = max(
        leases['total']['value'] if leases.get('total') else Decimal(0),
        sum((leases[key]['value'] for key in ('current', 'noncurrent') if leases.get(key)), Decimal(0)))
    candidates = []
    if financial:
        candidates.append(({'financial_debt': financial}, False))
    if items['total_debt_and_finance_leases']:
        candidates.append(({'total_debt_and_finance_leases': items['total_debt_and_finance_leases']}, True))
    current, noncurrent = items['current_debt_and_finance_leases'], items['noncurrent_term_debt_and_finance_leases']
    if current and noncurrent:
        candidates.append(({'current_debt_and_finance_leases': current,
                            'noncurrent_term_debt_and_finance_leases': noncurrent}, True))
    if current and items['noncurrent_term_debt'] and leases.get('noncurrent'):
        candidates.append(({'current_debt_and_finance_leases': current,
                            'noncurrent_term_debt': items['noncurrent_term_debt'],
                            'noncurrent_finance_leases': leases['noncurrent']}, True))
    bases = ['reported'] * len(candidates)
    short_parts = short_basis = None
    if short:
        short_parts = {'short_term_borrowings': short}
        short_basis = ('manual_primary_review' if short.get('classification_evidence') else
                       'reported_current_debt_less_current_maturities' if short.get('reconciliation') else 'reported')
    else:
        # Reported current debt covers short-term borrowings, current long-term
        # debt and current finance leases. Equality with the reported current
        # long-term amounts proves the remaining categories are zero.
        if current and current['value'] in _current_long_term_amounts(items, leases):
            short_parts, short_basis = {}, 'reported_current_debt_identity'
    if short_parts is not None:
        if combined:
            candidates.append(({**short_parts, **combined}, True))
            bases.append(short_basis)
        if term:
            candidates.append(({**short_parts, **term}, False))
            bases.append(short_basis)
    if not candidates and assume_identified_short_term and len(conflicts) == before:
        identified = {key: items[key] for key in ('commercial_paper', 'other_short_term_borrowings') if items[key]}
        expected_current = {_amount(identified) + amount for amount in _current_long_term_amounts(items, leases)}
        tolerance = (_half_unit(current) + sum((_half_unit(item) for item in identified.values()), Decimal(0)) +
                     max(_half_unit(items['current_term_debt_and_finance_leases']),
                         _half_unit(items['current_term_debt']) + _half_unit(leases.get('current'))))
        if current and expected_current and all(abs(current['value'] - amount) > tolerance for amount in expected_current):
            # Reported current debt exceeds the identified categories: other
            # short-term financing exists, so the assumption is contradicted.
            combined = term = None
        if combined:
            candidates.append(({**identified, **combined}, True))
            bases.append('identified_categories_house_assumption')
        if term:
            candidates.append(({**identified, **term}, False))
            bases.append('identified_categories_house_assumption')
    inclusive_amounts = [_amount(parts) for parts, included in candidates if included]
    exclusive_amounts = [_amount(parts) for parts, included in candidates if not included]
    if any(amount < known_lease_floor + (max(exclusive_amounts) if exclusive_amounts else 0)
           for amount in inclusive_amounts):
        conflicts.append('Combined debt is below separately reported financial debt plus known finance leases')
    for inclusive in (False, True):
        amounts = {_amount(parts) for parts, has_leases in candidates if has_leases == inclusive}
        if len(amounts) > 1:
            conflicts.append('Complete financial-debt alternatives conflict at the same liability scope')
    if lease_total:
        totals = {_amount(parts) + (Decimal(0) if included else _amount(lease_total))
                  for parts, included in candidates}
        if len(totals) > 1:
            conflicts.append('Combined debt and finance leases conflict with separately reported debt plus finance leases')
    if len(conflicts) != before or not candidates:
        return None
    # Preserve separate finance-lease treatment when a complete separate bridge
    # exists; otherwise an inclusive debt subtotal carries leases exactly once.
    wanted = not lease_total
    index = next((index for index, candidate in enumerate(candidates) if candidate[1] == wanted), 0)
    return (*candidates[index], bases[index])


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        result = Decimal(str(value))
        if not result.is_finite() or not math.isfinite(float(result)):
            return None
        return result
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _json_number(value):
    return int(value) if value == value.to_integral_value() else float(value)


def _cik(value):
    try:
        if isinstance(value, bool):
            return None
        return str(int(value)) if int(value) > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _public_fact(item):
    return {key: item.get(key) for key in ('concept', 'source_id', 'context_ref', 'instant',
            'start_date', 'end_date', 'unit', 'entity_identifier')} | {'value': _json_number(item['value'])} | {
            key: item[key] for key in ('fact_origin', 'filing_date', 'form', 'accession_number',
                                     'original_value', 'original_unit', 'frame', 'source_url', 'source_record') if key in item}


def _precision(item):
    """Stated inline-XBRL decimals; None means the value is taken as exact."""
    decimals = item.get('decimals')
    return decimals if isinstance(decimals, int) and not isinstance(decimals, bool) else None


def _unique(items, label, conflicts):
    if not items:
        return None
    values = {item['value'] for item in items}
    if len(values) != 1:
        # Filings repeat a statement amount in prose at a coarser stated
        # precision ("$10.7 billion", decimals=-8). Such a fact is a consistent
        # duplicate only if it lies within half a unit of its OWN stated
        # precision of the single most precisely reported value.
        exact = [item for item in items if _precision(item) is None]
        finest = exact or [item for item in items
                           if _precision(item) == max(_precision(other) for other in items)]
        best = {item['value'] for item in finest}
        consistent = len(best) == 1 and all(
            item['value'] in best or (_precision(item) is not None and
                                      abs(item['value'] - next(iter(best))) <= Decimal(5).scaleb(-_precision(item) - 1))
            for item in items)
        if not consistent:
            conflicts.append(f'Conflicting primary values for {label}; no value was selected')
            return None
        items = [item for item in items if item['value'] in best]
    selected = dict(items[0])
    selected['source_refs'] = sorted({item['source_id'] for item in items})
    selected['facts'] = [_public_fact(item) for item in items]
    # The finest inline precision the selected amount is stated at; company-facts
    # mirrors of the same filings carry none. None means no stated rounding.
    stated = [_precision(item) for item in items if _precision(item) is not None]
    selected['stated_decimals'] = max(stated) if stated else None
    return selected


def _half_unit(item):
    """Half a unit of an item's stated inline precision: the rounding it can carry."""
    decimals = item.get('stated_decimals') if item else None
    return Decimal(5).scaleb(-decimals - 1) if isinstance(decimals, int) else Decimal(0)


def _quote(sources, currency, cutoff):
    candidates = []
    now = datetime.now(timezone.utc)
    for source in sources:
        if not isinstance(source, dict) or source.get('kind') != 'market_snapshot':
            continue
        data = source.get('data')
        if not isinstance(data, dict) or data.get('currency') != currency:
            continue
        value = _number(data.get('regularMarketPrice'))
        if value is None or value <= 0:
            continue
        timestamp = data.get('regularMarketTime')
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            continue
        try:
            observed = datetime.fromtimestamp(timestamp, timezone.utc)
        except (ValueError, OverflowError, OSError):
            continue
        if observed > now or observed.date() > cutoff:
            continue
        supplied = data.get('quote_observed_at_utc')
        if supplied:
            try:
                stated = datetime.fromisoformat(supplied.replace('Z', '+00:00'))
                if stated.tzinfo is None or abs((stated - observed).total_seconds()) > 1:
                    continue
            except (TypeError, ValueError):
                continue
        candidates.append((observed, value, source.get('id')))
    if not candidates:
        return None
    latest = max(row[0] for row in candidates)
    matches = [row for row in candidates if row[0] == latest]
    if len({row[1] for row in matches}) != 1:
        return None
    return {'value': _json_number(matches[0][1]), 'observed_at': latest.isoformat(),
            'source_refs': sorted({row[2] for row in matches if isinstance(row[2], str)})}


def _component(concept, item, basis):
    return {'value': _json_number(item['value']), 'concept': 'us-gaap:' + concept if concept else None, 'basis': basis,
            'source_refs': item['source_refs'], 'facts': item['facts']}


def _first_reported(pick, concepts, label):
    """(concept, item) of the first concept with an amount on the date, else (None, None)."""
    for concept in concepts:
        item = pick('us-gaap:' + concept, label + ' (us-gaap:' + concept + ')')
        if item:
            return concept, item
    return None, None


def _equity_free_inclusive_cash(pick, cash):
    """{'cash_and_short_term_investments'} when that selected total is cash plus investments with maturities.

    Coca-Cola's cash, cash equivalents and short-term investments ($13,872 million)
    are its cash and cash equivalents ($10,270 million) plus other short-term
    investments ($3,602 million). OtherShortTermInvestments are investments
    maturing within a year, which equity securities cannot be, so the total holds
    no equity; Coca-Cola reports its marketable equity on separate lines. Any other
    composition, or a short-term investments concept that can hold trading equity,
    leaves the total able to contain equity securities.
    """
    total = cash.get('cash_and_short_term_investments')
    if not total:
        return set()
    parts = [pick('us-gaap:' + concept, 'equity-free cash test ' + concept, [])
             for concept in (CASH['cash_and_cash_equivalents'], OTHER_SHORT_TERM_INVESTMENTS)]
    if not all(parts):
        return set()
    tolerance = _half_unit(total) + sum((_half_unit(item) for item in parts), Decimal(0))
    return {'cash_and_short_term_investments'} if abs(total['value'] - sum(item['value'] for item in parts)) <= tolerance else set()


def _nonoperating_assets(pick, cash):
    """Non-operating investments at carrying value: the lines roic removes from invested capital, less selected cash.

    Returns the bridge record; ``nonoperating_assets`` is its Decimal total.
    """
    found = {}
    for field in NONOPERATING_FIELDS:
        concepts = MARKETABLE_EQUITY if field == 'equity_securities_marketable' else roic.INSTANTS[field]
        concept, item = _first_reported(pick, concepts, 'non-operating ' + field.replace('_', ' '))
        if item:
            found[field] = (concept, item)
    row = {field: float(item['value']) for field, (_, item) in found.items()}
    lines, not_added = roic.nonoperating_investments(row), []
    noncurrent_cash = [name for name in NONCURRENT_CASH_CATEGORIES if name in cash]
    if noncurrent_cash and any(field == 'long_term_investments' for field, _ in lines):
        not_added.append({'line': 'long_term_investments',
                          'reason': 'LongTermInvestments can contain the noncurrent securities selected as cash (' +
                                    ', '.join(noncurrent_cash) + '); the equity-investment lines are added instead'})
        lines = roic.nonoperating_investments({key: value for key, value in row.items()
                                               if key not in ('long_term_investments', 'securities_noncurrent')})
    elif noncurrent_cash and any(field == 'securities_noncurrent' for field, _ in lines):
        not_added.append({'line': 'securities_noncurrent',
                          'reason': 'Noncurrent securities are already in the selected cash (' + ', '.join(noncurrent_cash) + ')'})
        lines = [line for line in lines if line[0] != 'securities_noncurrent']
    if 'equity_securities_marketable' in row:
        holders = sorted(set(cash) - EQUITY_FREE_CASH_CATEGORIES - _equity_free_inclusive_cash(pick, cash))
        containers = [field for field, _ in lines if field in ('long_term_investments', 'other_long_term_investments')]
        if holders:
            not_added.append({'line': 'equity_securities_marketable',
                              'reason': 'The selected cash includes categories that can contain equity securities (' +
                                        ', '.join(holders) + ')'})
        elif containers:
            not_added.append({'line': 'equity_securities_marketable',
                              'reason': 'The investment line added (' + ', '.join(containers) + ') can contain them'})
        else:
            lines.append(('equity_securities_marketable', row['equity_securities_marketable']))
    chosen = {field for field, _ in lines}
    if 'other_long_term_investments' in chosen:
        for field in roic.DISJOINT_INVESTMENTS:
            if field in found:
                not_added.append({'line': field, 'reason': 'Other long-term investments, which can contain equity-method '
                                                           'stakes and non-marketable equity, are larger than those two '
                                                           'lines together and are added instead'})
    elif any(field in chosen for field in roic.DISJOINT_INVESTMENTS) and 'other_long_term_investments' in found:
        not_added.append({'line': 'other_long_term_investments',
                          'reason': 'Smaller than equity-method investments plus non-marketable equity, which it can '
                                    'contain; those two disjoint lines are added instead'})
    if 'long_term_investments' in chosen:
        for field in ('securities_noncurrent', *roic.NESTED_INVESTMENTS):
            if field in found:
                not_added.append({'line': field, 'reason': 'Within the balance-sheet investments line (us-gaap:LongTermInvestments)'})
    for entry in not_added:
        if entry['line'] in found:
            concept, item = found[entry['line']]
            entry.update(concept='us-gaap:' + concept, value=_json_number(item['value']), source_refs=item['source_refs'])
    components = {field: _component(found[field][0], found[field][1], 'reported carrying value') for field in chosen}
    total = sum((found[field][1]['value'] for field in chosen), Decimal(0))
    reported = any(field in found for field in NONOPERATING_FIELDS)
    record = {'nonoperating_assets': total, 'components': components, 'not_added': not_added,
              'basis': NONOPERATING_POLICY if reported else NONOPERATING_ABSENCE_POLICY,
              'assumed_absent': not reported,
              'concepts_looked_for': sorted({'us-gaap:' + concept for field in NONOPERATING_FIELDS for concept in roic.INSTANTS[field]} |
                                            {'us-gaap:' + concept for concept in MARKETABLE_EQUITY})}
    if 'equity_method_investments' in chosen:
        quoted = pick('us-gaap:' + EQUITY_METHOD_QUOTED_VALUE, 'quoted value of equity-method investments', [])
        if quoted:
            record['reported_quoted_market_value_not_used'] = {
                'value': _json_number(quoted['value']), 'concept': 'us-gaap:' + EQUITY_METHOD_QUOTED_VALUE,
                'source_refs': quoted['source_refs'],
                'basis': 'Reported quoted market value of the listed equity-method stakes; carrying value is used.'}
    return record


def _benefit_obligations(pick, conflicts):
    """Recognized defined-benefit liabilities of underfunded plans, gross; overfunded plans are not credited.

    Johnson & Johnson reports $4,691 million of pension and retiree-health
    liabilities and $7,300 million of plan assets in excess of obligations in other
    plans. One plan's surplus cannot pay another's deficit, and reclaiming it is
    taxed heavily, so the conservative rule the policy cites applies: the $4,691
    million is deducted and the surplus is not netted against it.
    """
    def part(label, total, current, noncurrent):
        whole = pick('us-gaap:' + total, label + ' (us-gaap:' + total + ')') if total else None
        first = pick('us-gaap:' + current, label + ' (us-gaap:' + current + ')') if current else None
        second = pick('us-gaap:' + noncurrent, label + ' (us-gaap:' + noncurrent + ')') if noncurrent else None
        if whole:
            pieces = [item for item in (first, second) if item]
            slack = _half_unit(whole) + sum((_half_unit(item) for item in pieces), Decimal(0))
            parts_sum = sum((item['value'] for item in pieces), Decimal(0))
            if pieces and (parts_sum > whole['value'] + slack or
                           (len(pieces) == 2 and abs(parts_sum - whole['value']) > slack)):
                conflicts.append(f'Reported {label} total conflicts with its current and noncurrent parts')
                return []
            return [(total, whole)]
        return [(concept, item) for concept, item in ((current, first), (noncurrent, second)) if item]
    group_name, used = None, []
    for name, parts in BENEFIT_GROUPS:
        found = [(part_name, line) for part_name, *concepts in parts
                 for line in part(name.replace('_', ' ') + ' ' + part_name.replace('_', ' '), *concepts)]
        if found:
            group_name, used = name, found
            break
    assets_concept, assets = _first_reported(pick, BENEFIT_ASSETS, 'overfunded plan assets')
    liabilities = sum((item['value'] for _, (_, item) in used), Decimal(0))
    surplus = assets['value'] if assets else Decimal(0)
    record = {'value': liabilities, 'group': group_name,
              'recognized_liabilities': _json_number(liabilities),
              'liability_lines': [{'plan': part_name, **_component(concept, item, 'recognized liability, before tax')}
                                  for part_name, (concept, item) in used],
              'overfunded_plan_assets': _json_number(surplus),
              **({'overfunded_plan_assets_line': _component(assets_concept, assets, 'recognized plan assets in excess of '
                                                            'obligations; not credited against other plans')}
                 if assets else {}),
              'overfunded_plan_assets_credited': 0,
              'tax_treatment': 'before tax', 'basis': PENSION_POLICY,
              'assumed_absent': not used and not assets,
              'source_refs': sorted({ref for _, (_, item) in used for ref in item['source_refs']} |
                                    set(assets['source_refs'] if assets else []))}
    return record


def _claim_components(pick, preferred_in_shares, conflicts):
    """Noncontrolling interests, preferred stock and benefit obligations at carrying value on the date."""
    components, looked_for = {}, []
    concept, item = _first_reported(pick, NONCONTROLLING, 'noncontrolling interests')
    if item:
        components['noncontrolling_interests'] = _component(concept, item, 'reported carrying value')
    else:
        # Only a fallback: restated equity totals leave it underived rather than stopping the bridge.
        derivation_conflicts = []
        total, parent = (pick('us-gaap:' + name, 'equity ' + name, derivation_conflicts) for name in TOTAL_EQUITY)
        if total and parent and total['value'] >= parent['value'] and not derivation_conflicts:
            components['noncontrolling_interests'] = {
                'value': _json_number(total['value'] - parent['value']), 'concept': None,
                'basis': 'derived: total equity including noncontrolling interests less parent stockholders\' equity',
                'source_refs': sorted(set(total['source_refs']) | set(parent['source_refs'])),
                'facts': total['facts'] + parent['facts']}
    concept, item = _first_reported(pick, REDEEMABLE_NONCONTROLLING, 'redeemable noncontrolling interests')
    if item:
        components['redeemable_noncontrolling_interests'] = _component(concept, item, 'reported carrying value')
    concept, item = _first_reported(pick, PREFERRED, 'preferred stock')
    if item and preferred_in_shares and item['value'] > 0:
        components['preferred_stock'] = dict(_component(concept, item, 'reported carrying value; counted through the conversion '
                                                                       'shares in the dilutive increment, so not deducted here'),
                                             value=0, carrying_value_not_deducted=_json_number(item['value']))
    elif item:
        components['preferred_stock'] = _component(concept, item, 'reported carrying value')
    benefits = _benefit_obligations(pick, conflicts)
    if not benefits['assumed_absent']:
        components['defined_benefit_obligations'] = {key: (_json_number(value) if key == 'value' else value)
                                                     for key, value in benefits.items() if key != 'assumed_absent'}
    for name, concepts in (('noncontrolling_interests', (*NONCONTROLLING, *TOTAL_EQUITY)),
                           ('redeemable_noncontrolling_interests', REDEEMABLE_NONCONTROLLING),
                           ('preferred_stock', PREFERRED),
                           ('defined_benefit_obligations', tuple(concept for _, parts in BENEFIT_GROUPS for part in parts
                                                                 for concept in part[1:] if concept) + BENEFIT_ASSETS)):
        if name not in components:
            looked_for.append({'line': name, 'value': 0, 'concepts_looked_for': ['us-gaap:' + concept for concept in concepts],
                               'basis': 'assumption: no concept reported on the date; zero, not a reported fact'})
    return components, looked_for


def _dilution(facts, start, end, outstanding, conflicts):
    """Dilutive increment of the anchor fiscal year, with its basis, or None on conflict."""
    found = {}
    for name, concept in DILUTION.items():
        items = [item for item in facts if item['concept'] == 'us-gaap:' + concept and item['parsed_start'] == start and
                 item['parsed_end'] == end and item['unit'] == 'xbrli:shares' and item['value'] >= 0 and
                 (item.get('fact_origin') != 'sec_companyfacts' or item.get('form') not in {'10-Q', '10-Q/A'})]
        chosen = _unique(items, name.replace('_', ' ') + ' for the anchor fiscal year', conflicts)
        if chosen:
            found[name] = chosen
    used = []
    period = {'start_date': start.isoformat(), 'end_date': end.isoformat()}
    diluted, basic = found.get('diluted_weighted_average'), found.get('basic_weighted_average')
    record = {'period': period, 'basis': DILUTION_POLICY, 'adjustments': []}
    if diluted and basic:
        increment = diluted['value'] - basic['value']
        if increment < 0:
            conflicts.append('Reported diluted weighted-average shares are below basic weighted-average shares; '
                             'no dilutive increment was selected')
            return None
        used += [diluted, basic]
        record.update(diluted_weighted_average=_json_number(diluted['value']),
                      basic_weighted_average=_json_number(basic['value']), status='reported')
    elif found.get('basic_and_diluted_weighted_average'):
        increment = Decimal(0)
        used.append(found['basic_and_diluted_weighted_average'])
        record.update(basic_and_diluted_weighted_average=_json_number(found['basic_and_diluted_weighted_average']['value']),
                      status='reported_basic_equals_diluted')
    else:
        increment = Decimal(0)
        record.update(basis=DILUTION_ABSENCE_POLICY, status='assumed_zero')
    debt_shares = found.get('debt_conversion_shares')
    if debt_shares and debt_shares['value'] > 0 and increment > 0:
        removed = min(debt_shares['value'], increment)
        increment -= removed
        used.append(debt_shares)
        record['adjustments'].append({'reason': 'conversion shares of convertible debt, which net debt already deducts',
                                      'shares_removed': _json_number(removed),
                                      'concept': 'us-gaap:' + DILUTION['debt_conversion_shares']})
    preferred = found.get('preferred_conversion_shares')
    record['preferred_in_diluted_shares'] = bool(preferred and preferred['value'] > 0 and record.get('status') == 'reported')
    if record['preferred_in_diluted_shares']:
        used.append(preferred)
        record['preferred_conversion_shares'] = _json_number(preferred['value'])
    if basic and outstanding and increment > 0:
        ratio = Decimal(str(outstanding)) / basic['value']
        if not DILUTION_SCALE_BAND[0] <= ratio <= DILUTION_SCALE_BAND[1]:
            # A split or large issuance after the fiscal year: keep the increment's share of the count.
            increment = (increment * ratio).to_integral_value()
            record['adjustments'].append({'reason': 'outstanding shares differ from the fiscal-year basic average by more than '
                                                    'half, a split or large issuance; the increment is scaled by their ratio',
                                          'ratio': float(ratio)})
    record['dilutive_shares'] = increment
    record['source_refs'] = sorted({ref for item in used for ref in item['source_refs']})
    record['facts'] = [fact for item in used for fact in item['facts']]
    return record


def canonical_reported_inputs(sources, *, currency=None, as_of=None):
    """Return pinned actuals, primary references, bridges and explicit coverage.

    A liability bridge requires a complete classified alternative in the same currency
    on the latest annual revenue's exact balance-sheet date. Cash follows the
    explicitly disclosed selected-asset policy. A reported zero must be present
    in primary evidence; it is never inferred from a missing tag. Cover-page and
    balance-sheet outstanding shares are eligible; annual average/issued shares
    are never denominator substitutes. Ambiguous values remain unpinned.
    """
    sources = sources if isinstance(sources, list) else []
    today = datetime.now(timezone.utc).date()
    cutoff = min(_day(as_of) or today, today)
    conflicts, skipped, facts, source_ids, scanned_dates = [], [], [], set(), set()
    annual_forms_seen, successor_statements = False, []
    for source in sources:
        if not isinstance(source, dict) or not (source.get('kind')=='regulatory_primary' or
                                                source.get('source_tier')=='regulatory_primary'):
            continue
        data = source.get('data')
        if not isinstance(data, dict):
            continue
        sid = source.get('id')
        if not isinstance(sid, str) or not sid:
            continue
        metadata = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}
        source_filed = _day(metadata.get('filingDate'))
        expected_entity = _cik(metadata.get('cik'))
        annual_forms_seen = annual_forms_seen or metadata.get('form') in ANNUAL_REPORT_FORMS
        if isinstance(metadata.get('successor_registrant_statement'), str):
            successor_statements.append(metadata['successor_registrant_statement'][:400])
        collections = []
        if isinstance(data.get('inline_facts'), list) and not (source_filed and source_filed > cutoff):
            collections.append((data['inline_facts'], 'inline_xbrl'))
        if isinstance(data.get('reported_facts'), list):
            url = source.get('url')
            match = re.fullmatch(r'https://data\.sec\.gov/api/xbrl/companyfacts/CIK([0-9]{10})\.json', url) if isinstance(url, str) else None
            if data.get('fact_origin') == 'sec_companyfacts' and expected_entity and match and _cik(match.group(1)) == expected_entity:
                collections.append((data['reported_facts'], 'sec_companyfacts'))
                selection = data.get('selection') if isinstance(data.get('selection'), dict) else {}
                scanned, dates_kept = selection.get('absence_scan_concepts'), selection.get('protected_balance_dates')
                # The scan holds on either side of the cutoff. Run after it, absence in the
                # larger record is absence in the smaller one; run before it, the scan covers
                # every fact this source holds, all filed by its as_of. Requiring a scan no
                # later than the cutoff dropped net debt whenever the valuation date was the
                # US calendar day before the UTC retrieval date (live run, 2026-11-27 03:10Z).
                if isinstance(scanned, list) and FINANCING_ABSENCE_SCAN <= set(scanned) and isinstance(dates_kept, list):
                    scanned_dates.update(day for day in dates_kept if isinstance(day, str))
            else:
                skipped.append('Structured primary facts lack a recognized SEC companyfacts origin and issuer endpoint')
        for raw, origin in ((raw, origin) for rows, origin in collections for raw in rows):
            if not isinstance(raw, dict) or raw.get('is_nil'):
                continue
            share_class = _share_class(raw, origin)
            if raw.get('dimensions') != {} and not share_class:
                continue
            annual_forms_seen = annual_forms_seen or (origin == 'sec_companyfacts' and raw.get('form') in ANNUAL_REPORT_FORMS)
            filed = source_filed
            if origin == 'sec_companyfacts':
                filed = _day(raw.get('filing_date'))
                accession = raw.get('accession_number')
                if (raw.get('fact_origin') != origin or raw.get('normalization_status') != 'normalized' or
                        not filed or filed > cutoff or raw.get('form') not in STRUCTURED_FORMS or
                        raw.get('source_url') != url or
                        not isinstance(accession, str) or not re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession)):
                    continue
            elif raw.get('parse_status') != 'parsed':
                continue
            entity = _cik(raw.get('entity_identifier'))
            if not entity or (expected_entity and entity != expected_entity):
                skipped.append('Primary fact has a missing or mismatched issuer identifier')
                continue
            value = _number(raw.get('normalized_decimal', raw.get('normalized_value')))
            normalized = _number(raw.get('normalized_value'))
            if value is None or normalized is None or value != normalized:
                continue
            if origin == 'sec_companyfacts':
                original_unit = raw.get('original_unit')
                expected_unit = ('xbrli:shares' if original_unit == 'shares' else
                                 'iso4217:' + original_unit if isinstance(original_unit, str) and re.fullmatch(r'[A-Z]{3}', original_unit) else
                                 'iso4217:' + original_unit[:3] + '/xbrli:shares' if isinstance(original_unit, str) and re.fullmatch(r'[A-Z]{3}/shares', original_unit) else None)
                if _number(raw.get('original_value')) != value or expected_unit != raw.get('unit'):
                    continue
            instant, start, end = (_day(raw.get(key)) for key in ('instant', 'start_date', 'end_date'))
            if instant:
                if instant > cutoff or (filed and instant > filed):
                    continue
            elif not start or not end or end > cutoff or (filed and end > filed) or start > end:
                continue
            item = {key: raw.get(key) for key in ('concept', 'context_ref', 'instant', 'start_date', 'end_date', 'unit')}
            item['fact_origin'] = origin
            if origin == 'inline_xbrl' and isinstance(raw.get('decimals'), str) and re.fullmatch(r'-?\d{1,2}', raw['decimals']):
                item['decimals'] = int(raw['decimals'])
            if origin == 'sec_companyfacts':
                item.update({key: raw.get(key) for key in ('filing_date', 'form', 'accession_number', 'original_value',
                                                          'original_unit', 'frame', 'source_url', 'source_record')})
            item.update(source_id=sid, entity_identifier=entity, value=value, parsed_instant=instant,
                        parsed_start=start, parsed_end=end)
            if share_class:
                item['share_class'] = share_class
            facts.append(item)
            source_ids.add(sid)
    entities = {item['entity_identifier'] for item in facts}
    # Class-level cover counts serve only the share total below.
    class_shares = [item for item in facts if item.get('share_class')]
    facts = [item for item in facts if not item.get('share_class')]
    actuals, refs, dates, bridge, claims_bridge = {}, {}, {}, {}, {}
    operating_reference = None
    fields = {key: 'unavailable' for key in REPORTED_FIELDS}
    if len(entities) > 1:
        conflicts.append('Primary reported facts identify multiple issuers; no reported actuals were pinned')
        facts, class_shares = [], []
    anchor_period = None
    annual = [item for item in facts if item['concept'] in REVENUE and
              (item.get('fact_origin') != 'sec_companyfacts' or item.get('form') not in {'10-Q', '10-Q/A'}) and item['parsed_start'] and item['parsed_end'] and
              320 <= (item['parsed_end'] - item['parsed_start']).days <= 400 and item['value'] > 0 and
              isinstance(item['unit'], str) and re.fullmatch(r'iso4217:[A-Z]{3}', item['unit'])]
    anchor = None
    if facts and not annual and not annual_forms_seen:
        skipped.append(_no_annual_report_note(successor_statements))
    if annual:
        latest = max(item['parsed_end'] for item in annual)
        latest_facts = [item for item in annual if item['parsed_end'] == latest]
        currencies = {item['unit'].split(':')[1] for item in latest_facts}
        if currency is None and len(currencies) == 1:
            currency = next(iter(currencies))
        compatible = [item for item in latest_facts if item['unit'] == 'iso4217:' + str(currency)]
        if not compatible:
            skipped.append('Latest annual revenue is not reported in the requested currency; no conversion or older-year substitution made')
        else:
            intervals = {(item['parsed_start'], item['parsed_end']) for item in compatible}
            if len(intervals) != 1:
                conflicts.append('Conflicting annual revenue periods; no annual anchor selected')
            else:
                # us-gaap:Revenues is total revenue; the contract-revenue
                # concepts are narrower. A larger reported total alongside a
                # consistent narrower amount is a scope difference, not a conflict.
                by_concept = {}
                for item in compatible:
                    by_concept.setdefault(item['concept'], set()).add(item['value'])
                total = by_concept.get('us-gaap:Revenues')
                revenue_basis = 'primary annual duration'
                if (len({item['value'] for item in compatible}) > 1 and total and
                        all(len(values) == 1 for values in by_concept.values()) and
                        all(next(iter(values)) <= next(iter(total)) for values in by_concept.values())):
                    compatible = [item for item in compatible if item['concept'] == 'us-gaap:Revenues']
                    revenue_basis = ('primary annual duration; reported total revenues (us-gaap:Revenues) selected over '
                                     'narrower reported contract-revenue amounts')
                revenue = _unique(compatible, 'latest annual revenue', conflicts)
                if revenue:
                    anchor = latest
                    anchor_period = (revenue['parsed_start'], revenue['parsed_end'])
                    operating_reference = _operating_reference(facts, revenue['parsed_start'], revenue['parsed_end'],
                                                               currency, revenue['value'], revenue['concept'])
                    actuals['revenue'] = _json_number(revenue['value'])
                    refs['revenue'] = revenue['source_refs']
                    dates['revenue'] = {'start_date': revenue['start_date'], 'end_date': revenue['end_date'], 'basis': revenue_basis}
                    fields['revenue'] = 'pinned'
    if anchor:
        def pick(concept, name, sink=None, exclude=frozenset()):
            return _unique([item for item in facts if item['concept'] == concept and item['parsed_instant'] == anchor and
                            item['unit'] == 'iso4217:' + currency and item['value'] >= 0 and id(item) not in exclude],
                           name, conflicts if sink is None else sink)
        cash_conflicts_before = len(conflicts)
        # The available-for-sale total is a fallback used only when no other
        # investment category is selected. Its own conflict then stops the
        # bridge; otherwise it is disclosed and does not (Uber restated it by
        # $1 million in later 10-Q comparatives while selecting marketable securities).
        afs_conflicts = []
        cash_items = {name: pick('us-gaap:' + concept, name, afs_conflicts if name == 'available_for_sale_debt_securities' else None)
                      for name, concept in CASH.items()}
        # Restricted cash only decides whether the fallback applies; a conflicting
        # restricted-cash amount counts as reported.
        restricted_conflicts = []
        restricted = [concept for concept in RESTRICTED_CASH
                      if (found := pick('us-gaap:' + concept, 'restricted cash ' + concept, restricted_conflicts)) and
                      found['value'] > 0]
        if not cash_items['cash_and_cash_equivalents'] and not restricted and not restricted_conflicts:
            cash_items['cash_including_restricted_cash'] = pick('us-gaap:' + CASH_INCLUDING_RESTRICTED,
                                                                'cash including restricted cash')
        cash = _cash_selection(cash_items, conflicts)
        if cash and afs_conflicts:
            if set(cash) <= {'cash_and_cash_equivalents', 'cash_including_restricted_cash'}:
                conflicts.extend(afs_conflicts)
        if len(conflicts) != cash_conflicts_before:
            cash = None
        lease_conflicts_before = len(conflicts)
        lease = {name: pick('us-gaap:' + concept, 'finance lease ' + name)
                 for name, concept in FINANCE_LEASES.items()}
        lease_parts = _subtotal(lease, 'total', 'current', 'noncurrent', 'Finance lease', conflicts)
        lease_conflicted = len(conflicts) != lease_conflicts_before
        if lease_conflicted:
            lease_parts = None
        lease['reconciled'] = lease_parts
        debt_conflicts_before = len(conflicts)
        # Issuers report the unamortized amount with either sign; only its
        # magnitude enters the exact reconciliation.
        reconciliation_items = {
            name: _unique([{**item, 'value': abs(item['value'])} for item in facts
                           if item['concept'] == 'us-gaap:' + concept and item['parsed_instant'] == anchor and
                           item['unit'] == 'iso4217:' + currency], name, [])
            for name, concept in DEBT_RECONCILIATION.items()}
        carrying_parts = [pick('us-gaap:' + DEBT[key], key, []) for key in ('current_term_debt', 'noncurrent_term_debt')]
        gross_ids, gross_observations = _gross_term_observations(
            facts, anchor, currency, reconciliation_items,
            sum(item['value'] for item in carrying_parts) if all(carrying_parts) else None)
        debt_items = {name: pick('us-gaap:' + concept, name, exclude=gross_ids if name == 'term_debt' else frozenset())
                      for name, concept in DEBT.items()}
        if not debt_items['short_term_borrowings']:
            debt_items['short_term_borrowings'] = _classified_debt(
                sources, debt_items, anchor, 'short_term_financing_total')
        classified_total = _classified_debt(sources, debt_items, anchor, 'financial_debt_total')
        if classified_total:
            if debt_items['financial_debt'] and debt_items['financial_debt']['value'] != classified_total['value']:
                conflicts.append('Primary classified total debt conflicts with the reported total financial debt')
            else:
                debt_items['financial_debt'] = classified_total
        debt_items, term_reconciliation = _reconcile_gross_term_debt(debt_items, reconciliation_items, lease)
        debt_items, excluded_totals = _set_aside_narrow_totals(debt_items, lease)
        debt_items, short_line_reconciliation = _short_term_line_is_current_debt(debt_items, lease)
        # The absence assumptions need an entity-wide record that attests it
        # looked for every modelled financing concept on this exact date.
        scan_attested = anchor.isoformat() in scanned_dates
        unclassified = {name: item for name, item in (
            (name, pick('us-gaap:' + concept, name)) for name, concept in UNCLASSIFIED_FINANCING.items())
            if item and item['value'] > 0}
        legacy_leases = [name for name, concept in LEGACY_LEASES.items()
                         if (found := pick('us-gaap:' + concept, 'capital lease ' + name)) and found['value'] > 0]
        debt_choice = _debt_selection(debt_items, lease, conflicts,
                                      assume_identified_short_term=scan_attested and not unclassified)
        if len(conflicts) != debt_conflicts_before:
            debt_choice = None
        debt, leases_in_debt, short_basis = debt_choice if debt_choice else ({}, False, None)
        if cash and debt_choice:
            cash_sum, debt_sum = _amount(cash), _amount(debt)
            actuals['net_debt'] = _json_number(debt_sum - cash_sum)
            refs['net_debt'] = sorted({sid for item in [*cash.values(), *debt.values()] for sid in item['source_refs']})
            dates['net_debt'] = {'instant': anchor.isoformat(), 'basis': 'same-period primary classified debt less explicitly selected nonoperating assets'}
            bridge = {'currency': currency, 'as_of': anchor.isoformat(), 'debt': _json_number(debt_sum),
                      'selected_cash_and_nonoperating_investments': _json_number(cash_sum),
                      'net_debt': actuals['net_debt'],
                      'cash_components': _components_public(cash),
                      'debt_components': _components_public(debt),
                      'finance_leases_included_in_net_debt': leases_in_debt,
                      'cash_classification_policy': CASH_POLICY,
                      'cash_coverage': {'basis': 'selected directly reported assets, not an assertion that omitted investments are zero',
                                        'excluded_unclassified_investments': 'LongTermInvestments and other investments without a selected marketable classification; see nonoperating_assets_bridge',
                                        'unselected_reported_categories': [name for name in CASH if cash_items[name] and name not in cash],
                                        'missing_investment_categories': [name for name in CASH if not cash_items[name]]},
                      'explanation': 'Exact sum of disjoint reported debt components minus selected cash and investments. Totals replace their components; no rounding residual or second cash add-back is permitted.',
                      'lease_policy': LEASE_POLICY}
            available_for_sale = 'available_for_sale_debt_securities' in cash
            if available_for_sale:
                equity = [(concept, item) for concept in EXCLUDED_EQUITY_SECURITIES
                          if (item := pick('us-gaap:' + concept, 'equity securities ' + concept))]
                bridge['cash_coverage']['available_for_sale_fallback'] = {
                    'basis': AFS_POLICY,
                    'excluded_reported_equity_securities': [{'concept': 'us-gaap:' + concept, 'value': _json_number(item['value']),
                                                             'source_refs': item['source_refs']} for concept, item in equity]}
            assumed = short_basis == 'identified_categories_house_assumption'
            identified = [name for name in ('commercial_paper', 'other_short_term_borrowings') if name in debt]
            bridge['short_term_financing'] = {
                'basis': short_basis,
                'identified_reported_categories': identified,
                'explanation': {
                    'reported': 'A reported complete debt total or short-term borrowing total establishes short-term financing.',
                    'manual_primary_review': 'A reviewed primary-disclosure classification establishes short-term financing.',
                    'reported_current_debt_identity': 'Reported current debt equals reported current long-term debt, so short-term borrowings are zero by reported arithmetic.',
                    'reported_current_debt_less_current_maturities': 'The issuer\'s short-term borrowings line equals its reported current debt, so short-term borrowings are that line less reported current long-term debt and current finance leases, by reported arithmetic.',
                    'identified_categories_house_assumption': SHORT_TERM_POLICY}[short_basis],
                **({'scanned_concepts': sorted(FINANCING_ABSENCE_SCAN)} if assumed else {})}
            if term_reconciliation and not any(name in debt for name in ('term_debt', 'financial_debt', 'total_debt_and_finance_leases')):
                bridge['long_term_debt_reconciliation'] = term_reconciliation
            if excluded_totals:
                bridge['excluded_scope_inconsistent_totals'] = excluded_totals
            if gross_observations:
                bridge['long_term_debt_observations_set_aside'] = gross_observations
            if short_line_reconciliation and 'short_term_borrowings' in debt:
                bridge['current_debt_reconciliation'] = short_line_reconciliation
            if afs_conflicts and 'available_for_sale_debt_securities' not in cash:
                bridge['cash_coverage']['unused_conflicting_categories'] = {
                    'available_for_sale_debt_securities': afs_conflicts,
                    'basis': 'The available-for-sale total is a fallback for issuers that report no other investment '
                             'category; another category is selected here, so its conflicting observations are not used.'}
            restricted_fallback = 'cash_including_restricted_cash' in cash
            if restricted_fallback:
                bridge['cash_coverage']['restricted_cash_fallback'] = {
                    'basis': RESTRICTED_CASH_POLICY,
                    'restricted_cash_concepts_looked_for': ['us-gaap:' + concept for concept in RESTRICTED_CASH]}
            principal = bool((bridge.get('long_term_debt_reconciliation') or {}).get('policy'))
            fields['net_debt'] = ('pinned_with_disclosed_assumption'
                                  if assumed or available_for_sale or principal or restricted_fallback else 'pinned')
        else:
            if not cash:
                skipped.append('Net-debt cash selection unavailable or conflicting on the annual balance-sheet date')
            if not debt_choice and unclassified:
                skipped.append('Net-debt liability bridge incomplete: reported ' +
                               ', '.join(sorted('us-gaap:' + UNCLASSIFIED_FINANCING[name] for name in unclassified)) +
                               ' cannot be classified between short-term financing and long-term debt without issuer disclosure; '
                               'supply a reviewed primary classification')
            elif not debt_choice and not scan_attested:
                skipped.append('Net-debt liability bridge incomplete: no SEC entity-wide record attests a scan of financing concepts on the balance-sheet date, so absent short-term borrowing cannot be assumed')
            if not debt_choice:
                skipped.append('Net-debt liability bridge incomplete: requires total debt and finance leases, current debt plus noncurrent debt and leases, or total short-term financing plus complete long-term debt; commercial paper alone does not establish total short-term financing')
        # A combined debt subtotal covers finance leases even where the lease
        # amount is not separately disclosed. Zero here is allocation to avoid
        # a second deduction, never evidence of an absent liability.
        if (leases_in_debt and debt) or lease_parts:
            amount = _amount(lease_parts) if lease_parts else None
            claim_amount = Decimal(0) if leases_in_debt and debt else amount
            used = list(debt.values()) if leases_in_debt and debt else list(lease_parts.values())
            actuals['other_equity_claims'] = _json_number(claim_amount)
            refs['other_equity_claims'] = sorted({sid for item in used for sid in item['source_refs']}) + ['assumption: additional unidentified claims are zero, not a reported fact']
            dates['other_equity_claims'] = {'instant': anchor.isoformat(), 'basis': 'finance leases already included in debt; no second deduction' if leases_in_debt else 'reported finance lease liabilities plus explicit house assumption for unidentified claims'}
            claims_bridge = {'currency': currency, 'as_of': anchor.isoformat(),
                             'reported_finance_lease_liabilities': _json_number(amount) if amount is not None else None,
                             'finance_lease_amount_basis': 'separately reported total or complete current/noncurrent components' if amount is not None else 'not separately available; covered by the selected combined debt-and-finance-lease subtotal',
                             'finance_leases_included_in_net_debt': leases_in_debt,
                             'additional_unidentified_claims': 0,
                             'additional_unidentified_claims_basis': CLAIMS_POLICY,
                             'other_equity_claims': _json_number(claim_amount),
                             'source_facts': [fact for item in used for fact in item['facts']], 'lease_policy': LEASE_POLICY}
            fields['other_equity_claims'] = 'pinned_with_disclosed_assumption'
        elif (scan_attested and not legacy_leases and not lease_conflicted and
              not any(lease[key] for key in FINANCE_LEASES) and
              not any(debt_items[key] for key in ('total_debt_and_finance_leases', 'term_debt_and_finance_leases',
                                                  'current_term_debt_and_finance_leases',
                                                  'noncurrent_term_debt_and_finance_leases'))):
            actuals['other_equity_claims'] = 0
            refs['other_equity_claims'] = ['assumption: no finance lease liability concept is reported; additional unidentified claims are zero, not a reported fact']
            dates['other_equity_claims'] = {'instant': anchor.isoformat(), 'basis': 'no reported finance lease liability concept; explicit house assumptions'}
            claims_bridge = {'currency': currency, 'as_of': anchor.isoformat(),
                             'reported_finance_lease_liabilities': None,
                             'finance_lease_amount_basis': LEASE_ABSENCE_POLICY,
                             'finance_leases_included_in_net_debt': False,
                             'additional_unidentified_claims': 0,
                             'additional_unidentified_claims_basis': CLAIMS_POLICY,
                             'other_equity_claims': 0,
                             'scanned_concepts': sorted('us-gaap:' + concept for group in (FINANCE_LEASES, LEGACY_LEASES)
                                                        for concept in group.values()),
                             'source_facts': [], 'lease_policy': LEASE_POLICY}
            fields['other_equity_claims'] = 'pinned_with_disclosed_assumption'
    outstanding = [item for item in facts if item['concept'] in SHARES and item['unit'] == 'xbrli:shares' and
                   item['parsed_instant'] and item['value'] > 0]
    # Shares are counted where net debt is: on its balance-sheet date, or on the
    # annual report's cover within SHARE_DATE_WINDOW_DAYS after it. Salesforce's
    # latest cover (823 million, 2026-08-20) followed $27.3 billion of buybacks
    # that the year-end net debt does not include; its balance sheet shows 929 million.
    window, reported_counts = None, bool(outstanding or class_shares)
    if anchor:
        window = [item for item in outstanding if anchor <= item['parsed_instant'] <= anchor + timedelta(days=SHARE_DATE_WINDOW_DAYS)]
        outstanding = ([item for item in window if item['parsed_instant'] == anchor] or
                       [item for item in window if item['parsed_instant'] == min(other['parsed_instant'] for other in window)]
                       if window else [])
        class_shares = [item for item in class_shares if item['parsed_instant'] and
                        anchor <= item['parsed_instant'] <= anchor + timedelta(days=SHARE_DATE_WINDOW_DAYS)]
    class_total = None if outstanding else _class_share_total(class_shares, conflicts, earliest=bool(anchor))
    def share_basis(instant):
        if not anchor:
            return 'latest reported ordinary shares outstanding; never annual average or issued shares'
        if instant == anchor:
            return ('reported ordinary shares outstanding on the balance-sheet date of net debt; never annual average or '
                    'issued shares')
        return (f'cover-page ordinary shares outstanding {(instant - anchor).days} days after the balance-sheet date of net '
                'debt, the nearest reported count; no count is reported on that date')
    if outstanding:
        latest = max(item['parsed_instant'] for item in outstanding)
        chosen = _unique(_cover_rounding(item for item in outstanding if item['parsed_instant'] == latest),
                         'latest reported outstanding shares', conflicts)
        if chosen:
            actuals['shares_outstanding'] = _json_number(chosen['value'])
            refs['shares_outstanding'] = chosen['source_refs']
            dates['shares_outstanding'] = {'instant': latest.isoformat(), 'basis': share_basis(latest)}
            if anchor:
                dates['shares_outstanding']['days_after_net_debt_date'] = (latest - anchor).days
            fields['shares_outstanding'] = 'pinned'
    elif class_total:
        actuals['shares_outstanding'] = _json_number(class_total['value'])
        refs['shares_outstanding'] = class_total['source_refs']
        instant = date.fromisoformat(class_total['instant'])
        dates['shares_outstanding'] = {'instant': class_total['instant'],
                                       'basis': CLASS_SHARES_BASIS + ('; ' + share_basis(instant) if anchor else ''),
                                       'classes': class_total['classes']}
        if anchor:
            dates['shares_outstanding']['days_after_net_debt_date'] = (instant - anchor).days
        fields['shares_outstanding'] = 'pinned'
    elif anchor and reported_counts and not window and not class_shares:
        skipped.append(f'No outstanding share count is reported on the net-debt balance-sheet date ({anchor.isoformat()}) or '
                       f'within {SHARE_DATE_WINDOW_DAYS} days after it; a later count would not match net debt. Supply a '
                       'reviewed primary count for that date')
    else:
        skipped.append('Outstanding shares are not reported as one entity-wide amount (for example, only by share class); '
                       'supply a reviewed primary total')
    nonoperating_bridge, share_bridge = {}, {}
    if anchor:
        dilution = _dilution(facts, *anchor_period, actuals.get('shares_outstanding'), conflicts)
        if dilution is not None:
            assumed_zero = dilution['status'] == 'assumed_zero'
            scaled = any('ratio' in entry for entry in dilution['adjustments'])
            actuals['dilutive_shares'] = _json_number(dilution['dilutive_shares'])
            refs['dilutive_shares'] = dilution['source_refs'] or [
                'assumption: no weighted-average share count is reported for the anchor fiscal year; '
                'dilutive increment zero, not a reported fact']
            dates['dilutive_shares'] = {**dilution['period'],
                                        'basis': 'anchor fiscal year diluted less basic weighted-average shares, added to the '
                                                 'latest outstanding shares'}
            fields['dilutive_shares'] = 'pinned_with_disclosed_assumption' if assumed_zero or scaled else 'pinned'
            share_bridge = {key: value for key, value in dilution.items() if key not in ('dilutive_shares', 'source_refs')}
            share_bridge.update(dilutive_shares=actuals['dilutive_shares'], source_refs=refs['dilutive_shares'])
            if 'shares_outstanding' in actuals:
                share_bridge.update(shares_outstanding=actuals['shares_outstanding'],
                                    shares_outstanding_as_of=dates['shares_outstanding']['instant'],
                                    diluted_shares=_json_number(Decimal(str(actuals['shares_outstanding'])) +
                                                                dilution['dilutive_shares']))
        if 'net_debt' in actuals:
            record = _nonoperating_assets(pick, cash)
            components = record['components']
            actuals['nonoperating_assets'] = _json_number(record['nonoperating_assets'])
            # With nothing added, the evidence is the reported lines left out (already in cash, or within another line).
            refs['nonoperating_assets'] = (
                sorted({ref for item in components.values() for ref in item['source_refs']}) or
                sorted({ref for entry in record['not_added'] for ref in entry.get('source_refs', [])}) or
                ['assumption: no non-operating investment concept is reported on the date; zero, not a reported fact'])
            dates['nonoperating_assets'] = {'instant': anchor.isoformat(),
                                            'basis': 'reported carrying value of non-operating investments not selected as cash, '
                                                     'on the balance-sheet date of net debt'}
            fields['nonoperating_assets'] = 'pinned_with_disclosed_assumption' if record['assumed_absent'] else 'pinned'
            nonoperating_bridge = {'currency': currency, 'as_of': anchor.isoformat(),
                                   **{key: value for key, value in record.items() if key != 'assumed_absent'},
                                   'nonoperating_assets': actuals['nonoperating_assets'],
                                   'measurement': 'carrying value; quoted market values are not used'}
        if 'other_equity_claims' in actuals:
            components, absent = _claim_components(
                pick, bool(dilution and dilution.get('preferred_in_diluted_shares')), conflicts)
            lease_claim = Decimal(str(actuals['other_equity_claims']))
            total = lease_claim + sum((Decimal(str(item['value'])) for item in components.values()), Decimal(0))
            actuals['other_equity_claims'] = _json_number(total)
            reported = sorted({ref for ref in refs['other_equity_claims'] if not ref.startswith('assumption:')} |
                              {ref for item in components.values() for ref in item['source_refs']})
            assumptions = [ref for ref in refs['other_equity_claims'] if ref.startswith('assumption:')]
            if absent:
                assumptions.append('assumption: ' + ', '.join(entry['line'].replace('_', ' ') for entry in absent) +
                                   ' not reported on the date; zero, not a reported fact')
            refs['other_equity_claims'] = reported + assumptions
            dates['other_equity_claims']['basis'] += ('; noncontrolling interests, preferred stock and unfunded '
                                                      'defined-benefit obligations at carrying value, before tax')
            claims_bridge.update(finance_lease_claim=_json_number(lease_claim), components=components,
                                 components_assumed_zero=absent, component_policy=CLAIMS_COMPONENT_POLICY,
                                 other_equity_claims=actuals['other_equity_claims'])
    if currency is None:
        market_currencies = {source['data'].get('currency') for source in sources
                             if isinstance(source, dict) and source.get('kind') == 'market_snapshot' and
                             isinstance(source.get('data'), dict) and
                             isinstance(source['data'].get('currency'), str) and
                             re.fullmatch(r'[A-Z]{3}', source['data']['currency'])}
        if len(market_currencies) == 1:
            currency = next(iter(market_currencies))
    quote = _quote(sources, currency, cutoff) if currency else None
    complete = all(field in actuals for field in REPORTED_FIELDS)
    output = {'actuals': actuals, 'currency': currency, 'source_refs': refs, 'input_dates': dates,
              'net_debt_bridge': bridge, 'other_claims_bridge': claims_bridge,
              'nonoperating_assets_bridge': nonoperating_bridge, 'share_count_bridge': share_bridge,
              'coverage': {'status': ('conflicted' if conflicts else 'complete' if complete else 'partial' if actuals else 'unavailable'),
                           'complete': complete and not conflicts, 'fields': fields,
                           'recognized_primary_source_ids': sorted(source_ids),
                           # A bridge line is listed only once its own prerequisite is pinned.
                           'missing_fields': [field for field in fields if field not in actuals and
                                              (field not in FIELD_PREREQUISITES or FIELD_PREREQUISITES[field] in actuals)],
                           'conflicts': conflicts, 'notes': sorted(set(skipped))},
              'notes': [CASH_POLICY, CLAIMS_POLICY, LEASE_POLICY,
                        *([SHORT_TERM_POLICY] if (bridge.get('short_term_financing') or {}).get('basis') ==
                          'identified_categories_house_assumption' else []),
                        *([AFS_POLICY] if (bridge.get('cash_coverage') or {}).get('available_for_sale_fallback') else []),
                        *([PRINCIPAL_POLICY] if (bridge.get('long_term_debt_reconciliation') or {}).get('policy') else []),
                        *([LEASE_ABSENCE_POLICY] if claims_bridge.get('finance_lease_amount_basis') == LEASE_ABSENCE_POLICY else []),
                        *([RESTRICTED_CASH_POLICY] if (bridge.get('cash_coverage') or {}).get('restricted_cash_fallback') else []),
                        *([nonoperating_bridge['basis']] if nonoperating_bridge else []),
                        *([CLAIMS_COMPONENT_POLICY, PENSION_POLICY] if claims_bridge.get('components') is not None else []),
                        *([share_bridge['basis']] if share_bridge else []),
                        'Shares are counted on the balance-sheet date of net debt, or on the annual report cover within '
                        f'{SHARE_DATE_WINDOW_DAYS} days after it; the quote is later and the dates remain distinct.',
                        'Missing primary facts are not zero; unsupported or ambiguous inputs remain unpinned.']}
    if operating_reference and operating_reference['lines']:
        output['operating_reference'] = operating_reference
    if quote:
        output.update(market_price=quote['value'])
        refs['market_price'] = quote['source_refs']
        dates['market_price'] = {'observed_at': quote['observed_at'], 'basis': 'regular-session observed quote; not the research date'}
    return output


def pin_reported_inputs(plan, sources):
    """Return a corrected copy and auditable changes; never alter forecasts.

    Uncovered inputs are retained rather than invented. Callers must inspect
    ``reported_inputs.coverage`` before asserting that the model is primary-pinned.
    Conflicting source evidence is exposed in coverage and is never arbitrarily
    resolved. Selected-asset classification and unidentified-claim zero are house policies.
    """
    if not isinstance(plan, dict):
        raise ValueError('Valuation plan must be an object')
    corrected = copy.deepcopy(plan)
    canonical = canonical_reported_inputs(sources, currency=plan.get('currency'), as_of=plan.get('valuation_date'))
    changes = []
    actuals = corrected.setdefault('actuals', {})
    refs = corrected.setdefault('source_refs', {})
    dates = corrected.setdefault('input_dates', {})
    if not all(isinstance(value, dict) for value in (actuals, refs, dates)):
        raise ValueError('actuals, source_refs and input_dates must be objects')
    for field, value in canonical['actuals'].items():
        previous = actuals.get(field)
        actuals[field] = value
        if previous != value:
            changes.append({'field': 'actuals.' + field, 'previous': previous, 'pinned': value,
                            'source_refs': canonical['source_refs'][field], 'reason': 'primary reported facts and explicit bridge policies'})
    for field in canonical['actuals']:
        refs[field] = canonical['source_refs'][field]
        dates[field] = canonical['input_dates'][field]
    for name in ('net_debt_bridge', 'other_claims_bridge', 'nonoperating_assets_bridge', 'share_count_bridge'):
        if canonical[name]:
            corrected[name] = canonical[name]
    if 'market_price' in canonical:
        previous = corrected.get('market_price')
        corrected['market_price'] = canonical['market_price']
        refs['market_price'] = canonical['source_refs']['market_price']
        dates['market_price'] = canonical['input_dates']['market_price']
        if previous != canonical['market_price']:
            changes.append({'field': 'market_price', 'previous': previous, 'pinned': canonical['market_price'],
                            'source_refs': refs['market_price'], 'reason': 'dated non-future regular-session observation'})
    corrected['reported_inputs'] = canonical
    return corrected, changes


# SEC industry codes whose firms fund operations with deposits or policyholder
# float, where debt and investment income are operating items and a free cash
# flow to the firm with net debt subtracted does not value the equity.
# Depository institutions 6000-6099 (JPMorgan: 6021, national commercial banks)
# and insurance carriers 6300-6399. Health plans (6324) report operating income
# and finance with ordinary debt, so they are left to the operating-income test.
BANK_SIC = range(6011, 6037)  # Depository institutions; 6099 (money transmitters) is not a bank
INSURER_SIC = range(6300, 6400)
OPERATING_SIC_EXCEPTIONS = frozenset({'6324'})


def operating_company_gate(sources, reported=None):
    """Whether the operating-company model applies, from free primary data only.

    Reads the SEC industry code that research.gather_primary keeps in filing
    metadata. ``refuse`` is True for a bank or insurer by industry code, or when
    the anchor fiscal year has reported revenue but no operating income line
    (JPMorgan reports neither operating income nor capital expenditure).
    ``reported`` is this module's canonical_reported_inputs result for the same
    sources, computed here when omitted. Without an annual revenue anchor the
    operating-income test is not made: the reported-input preflight already
    stops such a run and names its cause (ExxonMobil Holdings, a successor
    registrant with no annual report). Nothing here makes a request; a run
    should check it before any paid stage.
    """
    sic = description = None
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, dict) or source.get('source_tier') != 'regulatory_primary':
            continue
        data = source.get('data') if isinstance(source.get('data'), dict) else {}
        metadata = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}
        if isinstance(metadata.get('sic'), str) and re.fullmatch(r'\d{4}', metadata['sic']):
            sic = metadata['sic']
            description = metadata.get('sicDescription') if isinstance(metadata.get('sicDescription'), str) else None
            break
    if reported is None:
        reported = canonical_reported_inputs(sources)
    anchored = 'revenue' in (reported.get('actuals') or {})
    lines = (reported.get('operating_reference') or {}).get('lines') or {}
    operating_income = ('operating_income' in lines) if anchored else None
    kind = None
    if sic and sic not in OPERATING_SIC_EXCEPTIONS:
        kind = 'bank' if int(sic) in BANK_SIC else 'insurer' if int(sic) in INSURER_SIC else None
    reasons = []
    if kind:
        reasons.append(f'the issuer is a {kind} by SEC industry code {sic}' + (f' ({description})' if description else '') +
                       '; deposits or policyholder funds are operating liabilities, so a free cash flow to the firm '
                       'less net debt does not value its equity')
    if operating_income is False:
        reasons.append('its latest annual report has revenue but no operating income line (us-gaap:OperatingIncomeLoss), '
                       'to which the operating model anchors its margins')
    return {'refuse': bool(reasons), 'financial_institution': kind, 'sic': sic, 'sic_description': description,
            'operating_income_reported': operating_income, 'reasons': reasons,
            'message': ('The operating-company valuation model does not apply: ' + '; and '.join(reasons) + '.')
            if reasons else None}

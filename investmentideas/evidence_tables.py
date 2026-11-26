"""Deterministic evidence tables built from reported filings; no network access.

Two exhibits are computed here so that the forecast author and memo writer copy
reported numbers instead of searching for them or doing arithmetic:

* ``historical_baseline``: up to five fiscal years of operating history from the
  SEC companyfacts record (growth, margins, tax, reinvestment, buybacks).
* ``breakdown_tables``: revenue, cost and operating income by product/service,
  segment and geography from a filing's inline XBRL.

A live AAPL run (2026-11-14) sent the models 16 KB of a 232 KB 10-K and none of
its category or segment facts; the writer filled the gaps from unverified web
excerpts. Every figure here carries the concept it came from. Missing concepts
stay missing; nothing is set to zero to complete a row.

The company-facts record holds only standard-taxonomy, nondimensional facts. A
free sweep of 13 issuers (2026-11-15) found lines it cannot carry: issuer
extension tags (Microsoft's "depreciation, amortization, and other", its
acquisitions line) and repurchase share counts tagged only on an equity-component
or share-class axis. ``filing_supplement`` reads those few facts from the latest
annual filing's inline XBRL; the company-facts value always decides when both exist.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path
import re

ANNUAL_FORMS = {'10-K', '10-K/A', '20-F', '20-F/A', '40-F', '40-F/A'}
REVENUE = ('RevenueFromContractWithCustomerExcludingAssessedTax', 'RevenueFromContractWithCustomerIncludingAssessedTax',
           'Revenues', 'SalesRevenueNet')
DURATIONS = {
    'gross_profit': ('GrossProfit',),
    'cost_of_revenue': ('CostOfGoodsAndServicesSold', 'CostOfRevenue'),
    'operating_income': ('OperatingIncomeLoss',),
    'research_and_development': ('ResearchAndDevelopmentExpense', 'ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost'),
    'selling_general_administrative': ('SellingGeneralAndAdministrativeExpense',),
    'pretax_income': ('IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest',
                      'IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments'),
    'income_tax_expense': ('IncomeTaxExpenseBenefit',),
    'income_taxes_paid': ('IncomeTaxesPaidNet', 'IncomeTaxesPaid'),
    'net_income': ('NetIncomeLoss',),
    'operating_cash_flow': ('NetCashProvidedByUsedInOperatingActivities',),
    'capital_expenditure': ('PaymentsToAcquirePropertyPlantAndEquipment', 'PaymentsToAcquireProductiveAssets'),
    'stock_compensation': ('ShareBasedCompensation', 'AllocatedShareBasedCompensationExpense'),
    'repurchases': ('PaymentsForRepurchaseOfCommonStock',),
    'dividends': ('PaymentsOfDividends', 'PaymentsOfDividendsCommonStock'),
    'diluted_shares': ('WeightedAverageNumberOfDilutedSharesOutstanding',),
    'diluted_eps': ('EarningsPerShareDiluted',),
    # Leases signed in the year: reinvestment that capital expenditure omits.
    # Microsoft added $24.6 billion of finance-lease assets in fiscal 2026, about
    # a fifth of its capital expenditure.
    'finance_lease_additions': ('RightOfUseAssetObtainedInExchangeForFinanceLeaseLiability',),
}
# Selling and G&A reported separately are summed into SG&A, the same rule as
# inputs._operating_reference (Microsoft, Alphabet, Salesforce, Meta and Amazon
# report no SellingGeneralAndAdministrativeExpense).
SELLING = ('SellingAndMarketingExpense', 'MarketingExpense')
GENERAL = ('GeneralAndAdministrativeExpense',)
# Acquisition payments. NVIDIA reports its $13.0 billion Groq payment as
# PaymentsToAcquireBusinessTwoNetOfCashAcquired on its own cash-flow line, beside
# $1.5 billion of other acquisitions under the plural concept.
ACQUISITIONS = ('PaymentsToAcquireBusinessesNetOfCashAcquired', 'PaymentsToAcquireBusinessTwoNetOfCashAcquired')
# Issuer extensions for the cash-flow acquisitions line, which the company-facts
# record never carries: Microsoft, Alphabet, Meta and Amazon fold purchases of
# intangibles or nonmarketable securities into it; Coca-Cola adds equity-method
# investments. The concept name is kept so that the broader scope is visible.
ACQUISITION_EXTENSION = re.compile(r'(?:(?:Acquisitions|PaymentsToAcquireBusiness)\w*NetOfCashAcquired\w*|AcquisitionsOfBusinesses\w*)')
# Depreciation and amortization totals. The cash-flow statement carries the
# total; a note can tag a partial figure with a total concept (Salesforce tags
# $1.2 billion as DepreciationDepletionAndAmortization against $3.6 billion of
# DepreciationAndAmortization in its cash-flow statement), so the largest
# reported total is taken. Depreciation alone is used only when no total is
# reported, and is labelled. Microsoft's cash-flow total is an extension
# (msft:DepreciationAmortizationAndOther, $38.5 billion against $34.3 billion
# of us-gaap:Depreciation in fiscal 2026).
DEPRECIATION_TOTALS = ('DepreciationDepletionAndAmortization', 'DepreciationAndAmortization',
                       'DepreciationAmortizationAndAccretionNet')
DEPRECIATION_ONLY = ('Depreciation',)
DEPRECIATION_EXTENSION = re.compile(r'Depreciation(?:Depletion)?(?:And)?Amortization\w*')
INSTANTS = {
    'inventory': ('InventoryNet',),
    'equity': ('StockholdersEquity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'),
}
# Balance-sheet lines for invested capital (equity + debt + finance leases - cash and
# marketable securities), from which Python computes the return on capital that
# limits the house terminal return (owner choice, 2026-11-15). An approximation:
# each line uses the first standard concept reported, missing lines count as zero.
CAPITAL_INSTANTS = {
    'long_term_debt_total': ('LongTermDebt',),
    'long_term_debt_noncurrent': ('LongTermDebtNoncurrent',),
    'long_term_debt_current': ('LongTermDebtCurrent',),
    'commercial_paper': ('CommercialPaper',),
    'finance_lease_liability': ('FinanceLeaseLiability',),
    'finance_lease_noncurrent': ('FinanceLeaseLiabilityNoncurrent',),
    'finance_lease_current': ('FinanceLeaseLiabilityCurrent',),
    'cash': ('CashAndCashEquivalentsAtCarryingValue',),
    'marketable_current': ('MarketableSecuritiesCurrent', 'ShortTermInvestments', 'AvailableForSaleSecuritiesDebtSecuritiesCurrent'),
    'marketable_noncurrent': ('MarketableSecuritiesNoncurrent', 'AvailableForSaleSecuritiesDebtSecuritiesNoncurrent'),
}
STATUTORY_TAX_FALLBACK = .25


def _invested_capital(row):
    """Equity + debt + finance leases - cash and marketable securities, or None without equity."""
    if not isinstance(row.get('equity'), (int, float)):
        return None
    def amount(name):
        value = row.get(name)
        return value if isinstance(value, (int, float)) else 0
    debt = (amount('long_term_debt_total') if row.get('long_term_debt_total') is not None else
            amount('long_term_debt_noncurrent') + amount('long_term_debt_current')) + amount('commercial_paper')
    leases = (amount('finance_lease_liability') if row.get('finance_lease_liability') is not None else
              amount('finance_lease_noncurrent') + amount('finance_lease_current'))
    return row['equity'] + debt + leases - amount('cash') - amount('marketable_current') - amount('marketable_noncurrent')
# Share counts the writer would otherwise subtract by hand: a live draft
# (2026-11-14) stated Apple's net share reduction a thousand times too small.
SHARES_OUTSTANDING = ('CommonStockSharesOutstanding',)
COVER_SHARES = 'EntityCommonStockSharesOutstanding'
# Coca-Cola and Walmart tag no balance-sheet outstanding count; their 10-K cover
# reports one about seven weeks after the fiscal year end.
COVER_WINDOW_DAYS = 120
SHARE_UNITS = {'shares'}
# Average buyback price pairs a value and a share count from the same disclosure;
# cash-flow payments can include purchases outside the program (MSFT: about a
# third above the program's own price).
REPURCHASE_PAIRS = (('StockRepurchasedDuringPeriodValue', 'StockRepurchasedDuringPeriodShares'),
                    ('StockRepurchasedAndRetiredDuringPeriodValue', 'StockRepurchasedAndRetiredDuringPeriodShares'),
                    ('TreasuryStockValueAcquiredCostMethod', 'TreasuryStockSharesAcquired'))
# The filing's own average price (Walmart, Costco, Salesforce). It is preferred to
# a computed pair: Salesforce's fiscal 2026 pair gives 254.42 against its stated
# 254.21, and a year has one price.
STATED_REPURCHASE_PRICE = 'TreasuryStockAcquiredAverageCostPerShare'
# A pair is divided only when the share count's stated rounding moves the price
# by at most 2%. Coca-Cola tags 2025's 9.4 million repurchased shares as
# 9 million (decimals -6): $634 million over 9 million is $70.44 against the
# stated $67.42. Meta (40 million, 1.25%) and Salesforce (50 million, 1%) pass.
REPURCHASE_SHARE_PRECISION = Decimal('0.02')
PROGRAM_AXIS = 'srt:ShareRepurchaseProgramAxis'
CLASS_AXIS = 'us-gaap:StatementClassOfStockAxis'
EQUITY_AXIS = 'us-gaap:StatementEquityComponentsAxis'
# Members of the equity-component axis that hold a full repurchased share count
# (Salesforce: treasury stock; Alphabet, Meta: common stock). Values on this axis
# are split across components (Walmart: common stock, paid-in capital, retained
# earnings), so a value is read only from the parent total or the treasury-stock
# component, and only when every component reporting it agrees (JPMorgan's
# treasury-stock cost of $31,924 million includes excise tax, its common-stock
# member $31,640 million does not: no value is chosen between them).
SHARE_COMPONENTS = re.compile(r'(?:CommonStock\w*|TreasuryStock\w*|CommonClass\w+)Member')
VALUE_COMPONENTS = re.compile(r'(?:ParentMember|TreasuryStock\w*Member)')
LEASE_OBLIGATION = 'UnrecordedUnconditionalPurchaseObligationBalanceSheetAmount'
LEASE_TERM_AXIS = 'us-gaap:LeaseContractualTermAxis'
# Per-share and share-count values change basis at a stock split.
SHARE_BASIS_FIELDS = ('diluted_shares', 'diluted_eps')


def _day(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _annual_rows(gaap, concept, as_of=None):
    record = gaap.get(concept) or {}
    for unit, rows in (record.get('units') or {}).items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if (isinstance(row, dict) and row.get('form') in ANNUAL_FORMS and isinstance(row.get('val'), (int, float))
                    and not isinstance(row.get('val'), bool) and _day(row.get('end')) and _day(row.get('filed'))
                    and (as_of is None or row['filed'] <= as_of)):
                yield unit, row


def _fiscal_years(gaap, count, as_of=None):
    """Latest ``count`` annual revenue periods.

    The narrower revenue concepts are preferred in order, except that reported
    total revenues (us-gaap:Revenues) above every narrower amount for the same
    period is selected, the rule canonical_reported_inputs applies to the anchor
    year. Walmart's history showed net sales of $706.4 billion while its pinned
    revenue was total revenues of $713.2 billion, which add membership income.
    """
    by_end = {}
    for concept in REVENUE:
        for unit, row in _annual_rows(gaap, concept, as_of):
            if len(unit) != 3 or 'start' not in row or not _day(row.get('start')):
                continue
            if not 350 <= (_day(row['end']) - _day(row['start'])).days <= 380:
                continue
            options = by_end.setdefault(row['end'], {})
            current = options.get(concept)
            if current is None or row['filed'] > current[0]:
                options[concept] = (row['filed'], row['start'], row['val'], unit)
    periods = []
    for end in sorted(by_end)[-count:]:
        options = by_end[end]
        chosen = next(concept for concept in REVENUE if concept in options)
        total = options.get('Revenues')
        if (total and chosen != 'Revenues' and
                all(value[1] == total[1] and value[3] == total[3] and value[2] <= total[2] for value in options.values()) and
                any(value[2] != total[2] for value in options.values())):
            chosen = 'Revenues'
        _, start, value, unit = options[chosen]
        periods.append({'start': start, 'end': end, 'revenue': value, 'revenue_concept': chosen, 'currency': unit})
    return periods


def _duration(gaap, concepts, start, end, currency, as_of=None):
    for concept in concepts:
        best = None
        for unit, row in _annual_rows(gaap, concept, as_of):
            if unit not in ({currency} | SHARE_UNITS | {currency + '/shares'}):
                continue
            if row.get('start') == start and row['end'] == end and (best is None or row['filed'] > best['filed']):
                best = row
        if best is not None:
            return best['val'], concept, best['filed']
    return None, None, None


# A split restates earlier share counts and per-share values. Quarterly reports
# are scanned too: between a split and the next annual report, the latest annual
# values are on the old basis.
SPLIT_SHARE_CONCEPTS = ('CommonStockSharesOutstanding', 'WeightedAverageNumberOfDilutedSharesOutstanding',
                        'WeightedAverageNumberOfSharesOutstandingBasic')
SPLIT_PER_SHARE_CONCEPTS = ('EarningsPerShareDiluted', 'EarningsPerShareBasic')
SPLIT_FORMS = ANNUAL_FORMS | {'10-Q', '10-Q/A'}


# A restatement by more than this factor corrects a scale error, not a split:
# Coca-Cola's 2019-2020 quarterly reports restated weighted-average shares a
# million-fold (millions retagged as units), which marked a split that never was.
SPLIT_FACTOR_LIMIT = 50


def _restatements(gaap, concept, as_of=None):
    """(filing date, factor) for each later report of the same period at a split-sized multiple."""
    vintages = {}
    for unit, rows in ((gaap.get(concept) or {}).get('units') or {}).items():
        for row in rows if isinstance(rows, list) else []:
            value = row.get('val') if isinstance(row, dict) else None
            if (row.get('form') in SPLIT_FORMS and isinstance(value, (int, float)) and not isinstance(value, bool)
                    and value and _day(row.get('end')) and _day(row.get('filed'))
                    and (as_of is None or row['filed'] <= as_of)):
                vintages.setdefault((unit, row.get('start'), row['end']), []).append((row['filed'], abs(value)))
    for observations in vintages.values():
        observations.sort()
        for (_, old), (filed, new) in zip(observations, observations[1:]):
            if 1.5 <= max(new / old, old / new) <= SPLIT_FACTOR_LIMIT:
                yield filed, new / old


def _split_date(gaap, as_of=None):
    """Latest filing that restated earlier values for a stock split, or None.

    A past share count restated by 1.5 to 50 times is a split. Per-share values
    are noisier near zero, so a filing without a share-count restatement counts
    only when at least two of its per-share restatements share one factor and
    they are most of the per-share restatements in that filing. Salesforce's
    March 2019 10-K restated EPS for an accounting change by factors from 1.8 to
    3.1, two of which coincided; a split moves every restated period alike
    (Alphabet's April 2023 10-Q: 0.0498 and 0.0500).
    """
    dates = {filed for concept in SPLIT_SHARE_CONCEPTS for filed, _ in _restatements(gaap, concept, as_of)}
    per_share = {}
    for concept in SPLIT_PER_SHARE_CONCEPTS:
        for filed, factor in _restatements(gaap, concept, as_of):
            per_share.setdefault(filed, []).append(factor)
    for filed, factors in per_share.items():
        cluster = max(sum(1 for other in factors if abs(other / factor - 1) < .02) for factor in factors)
        if cluster >= 2 and cluster * 2 > len(factors):
            dates.add(filed)
    return max(dates) if dates else None


def _instant(gaap, concepts, end, currency, as_of=None):
    for concept in concepts:
        best = None
        for unit, row in _annual_rows(gaap, concept, as_of):
            if unit != currency or 'start' in row:
                continue
            if row['end'] == end and (best is None or row['filed'] > best['filed']):
                best = row
        if best is not None:
            return best['val'], concept
    return None, None


def _share_instant(gaap, concepts, end, as_of=None):
    """Period-end share count, blank when reported only before a later split restatement."""
    for concept in concepts:
        best = None
        for unit, row in _annual_rows(gaap, concept, as_of):
            if unit in SHARE_UNITS and 'start' not in row and row['end'] == end and (best is None or row['filed'] > best['filed']):
                best = row
        if best is not None:
            split = _split_date(gaap, as_of)
            return (None if split and best['filed'] < split else best['val']), concept
    return None, None


def _ratio(numerator, denominator):
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _local(concept):
    return concept.split(':', 1)[-1]


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _plain(value):
    """A reported amount as int or float, as company facts carry them."""
    return int(value) if value == value.to_integral_value() else float(value)


def _unit_name(unit):
    """'USD', 'shares' or 'USD/shares' from a parsed inline unit, else None."""
    simple, numerator, denominator = unit['simple'], unit['numerator'], unit['denominator']
    if len(simple) == 1 and not numerator and not denominator:
        measure = simple[0]
        if re.fullmatch(r'iso4217:[A-Z]{3}', measure):
            return measure.split(':')[1]
        return 'shares' if measure == 'xbrli:shares' else None
    if (not simple and len(numerator) == 1 and len(denominator) == 1 and
            re.fullmatch(r'iso4217:[A-Z]{3}', numerator[0]) and denominator[0] == 'xbrli:shares'):
        return numerator[0].split(':')[1] + '/shares'
    return None


def _wanted(concept, members):
    """Facts the supplement keeps from an annual filing; everything else is ignored."""
    local = _local(concept)
    if not members:
        return bool(ACQUISITION_EXTENSION.fullmatch(local) or DEPRECIATION_EXTENSION.fullmatch(local) or
                    local in {name for pair in REPURCHASE_PAIRS for name in pair} or local == STATED_REPURCHASE_PRICE or
                    local in SHARES_OUTSTANDING or local == COVER_SHARES or 'NotYetCommenced' in local)
    if len(members) != 1:
        return False
    (axis, member), = members.items()
    if local in {name for pair in REPURCHASE_PAIRS for name in pair} or local == STATED_REPURCHASE_PRICE:
        return axis in {PROGRAM_AXIS, CLASS_AXIS, EQUITY_AXIS}
    if local in SHARES_OUTSTANDING or local == COVER_SHARES:
        return axis in {CLASS_AXIS, EQUITY_AXIS}
    # Leases signed but not commenced, tagged with the purchase-obligation total
    # on a lease or obligation-category axis (Microsoft, Amazon, Apple) rather
    # than with a concept of their own.
    return (local == LEASE_OBLIGATION and isinstance(member, str) and
            ('NotYetCommenced' in member or axis == LEASE_TERM_AXIS))


def filing_supplement(document, *, source_id=None, filing_date=None, form=None, report_date=None):
    """Facts from an annual filing's inline XBRL that company facts cannot carry.

    Kept: issuer-extension acquisitions and depreciation totals (nondimensional
    only), repurchase values, share counts and stated average prices on the
    repurchase-program, share-class or equity-component axis, outstanding share
    counts by share class or equity component, and leases signed but not yet
    commenced. Values are exact decimals; nothing is summed here.
    """
    from .research import MAX_DOCUMENT_BYTES, _InlineFactParser, _inline_number
    if not isinstance(document, str):
        raise ValueError('Inline XBRL document must be decoded text')
    if len(document.encode()) > MAX_DOCUMENT_BYTES:
        raise ValueError('Inline XBRL document exceeds the source size limit')

    class Parser(_InlineFactParser):
        # Extension concepts carry the issuer's own prefix; the selection below
        # is by name and axis, and every fact comes from the issuer's own filing.
        def _selected(self, name):
            return isinstance(name, str) and re.fullmatch(r'[A-Za-z][\w.-]*:[A-Za-z]\w*', name) is not None

    parser = Parser(document)
    parser.feed(document)
    facts, seen = [], set()
    for frame in parser.facts:
        attrs = frame['attrs']
        context = parser.contexts.get(attrs.get('contextref'))
        unit = parser.units.get(attrs.get('unitref'))
        if context is None or unit is None:
            continue
        members = dict(context['members'])
        if any(not isinstance(member, str) for member in members.values()) or not _wanted(attrs['name'], members):
            continue
        unit_name = _unit_name(unit)
        value, exact, status = _inline_number(''.join(frame['parts']).strip(), attrs)
        if unit_name is None or status not in {'parsed', 'explicit_zero_transform'} or exact is None:
            continue
        period = context['period']
        start, end, instant = (_day(period.get(key)) for key in ('start_date', 'end_date', 'instant'))
        if not (instant or (start and end and start <= end)):
            continue
        decimals = attrs.get('decimals')
        key = (attrs['name'], tuple(sorted(members.items())), period.get('start_date'), period.get('end_date'),
               period.get('instant'), unit_name, exact)
        if key in seen:
            continue
        seen.add(key)
        facts.append({'concept': attrs['name'], 'dimensions': members, 'unit': unit_name, 'value': exact,
                      'start': period.get('start_date') if not instant else None,
                      'end': period.get('instant') or period.get('end_date'),
                      'decimals': int(decimals) if isinstance(decimals, str) and re.fullmatch(r'-?\d{1,2}', decimals) else None})
    return {'source_id': source_id, 'filing_date': filing_date, 'form': form, 'report_date': report_date, 'facts': facts}


def filing_supplement_for(folder, sources, as_of=None):
    """The supplement from the latest archived annual SEC filing among ``sources``, or None.

    The archive must sit inside ``folder`` and match its recorded checksum, as
    for every derived table. A filing dated after ``as_of`` is not used.
    """
    root = Path(folder).resolve()
    best = None
    for source in sources if isinstance(sources, list) else []:
        data = source.get('data') if isinstance(source, dict) else None
        metadata = data.get('metadata') if isinstance(data, dict) else None
        if (not isinstance(metadata, dict) or metadata.get('form') not in ANNUAL_FORMS or not source.get('archive') or
                source.get('kind') != 'sec_filing' or not _day(metadata.get('filingDate')) or
                (as_of and metadata['filingDate'] > as_of)):
            continue
        if best is None or metadata['filingDate'] > best[1]['filingDate']:
            best = (source, metadata)
    if best is None:
        return None
    source, metadata = best
    archive = (root / source['archive']).resolve()
    try:
        if not archive.is_relative_to(root) or not archive.is_file():
            return None
        raw = archive.read_bytes()
        if hashlib.sha256(raw).hexdigest() != source.get('archive_sha256'):
            return None
        return filing_supplement(raw.decode('utf-8'), source_id=source.get('id'), filing_date=metadata['filingDate'],
                                 form=metadata['form'], report_date=metadata.get('reportDate'))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _supplement_facts(supplement, local_names, start, end, unit, *, axis=None):
    """Supplement facts for exact local names and period, nondimensional or on one axis."""
    found = []
    for fact in (supplement or {}).get('facts') or []:
        if (_local(fact['concept']) in local_names and fact['start'] == start and fact['end'] == end and
                fact['unit'] == unit and (list(fact['dimensions']) == [axis] if axis else not fact['dimensions'])):
            found.append(fact)
    return found


def _supplement_amount(supplement, local, start, end, unit):
    """One repurchase value or share count from the filing, with its basis, or None.

    Order: nondimensional; the repurchase-program axis (programs are disjoint,
    so members are summed); the share-class axis (classes are disjoint, summed);
    the equity-component axis under the component rules above. A member that
    reports two different values for one period makes the axis ambiguous.
    """
    plain = {Decimal(fact['value']): fact for fact in _supplement_facts(supplement, {local}, start, end, unit)}
    if len(plain) == 1:
        (value, fact), = plain.items()
        return value, fact['decimals'], 'reported'
    if plain:
        return None
    for axis, label in ((PROGRAM_AXIS, 'repurchase-program axis'), (CLASS_AXIS, 'share-class axis')):
        by_member = {}
        for fact in _supplement_facts(supplement, {local}, start, end, unit, axis=axis):
            by_member.setdefault(fact['dimensions'][axis], set()).add((Decimal(fact['value']), fact['decimals']))
        if by_member:
            if any(len({value for value, _ in values}) != 1 for values in by_member.values()):
                return None
            total = sum(next(iter(values))[0] for values in by_member.values())
            # Rounding in a sum is bounded by its most coarsely stated part.
            stated = [decimals for values in by_member.values() for _, decimals in values if decimals is not None]
            return total, min(stated, default=None), ('sum over the ' if len(by_member) > 1 else 'on the ') + label
    components = {}
    for fact in _supplement_facts(supplement, {local}, start, end, unit, axis=EQUITY_AXIS):
        components.setdefault(_local(fact['dimensions'][EQUITY_AXIS]), set()).add((Decimal(fact['value']), fact['decimals']))
    if not components:
        return None
    if unit == 'shares':
        holders = {member: values for member, values in components.items() if SHARE_COMPONENTS.fullmatch(member)}
    elif 'ParentMember' in components:
        holders = {'ParentMember': components['ParentMember']}
    else:
        holders = {member: values for member, values in components.items() if VALUE_COMPONENTS.fullmatch(member)}
        if holders and len({value for values in components.values() for value, _ in values}) != 1:
            return None
    values = {value for values in holders.values() for value, _ in values}
    if len(values) != 1:
        return None
    # One amount stated at several precisions is known to the finest of them.
    precisions = [decimals for values in holders.values() for _, decimals in values]
    return values.pop(), (None if None in precisions else max(precisions)), 'on the equity-component axis'


def _share_tolerance(shares, decimals):
    """Relative error a share count's stated rounding allows; 0 for an exact count."""
    if decimals is None:
        return Decimal(0)
    return Decimal(5).scaleb(-decimals - 1) / shares


def _trailing_precision(value):
    """Decimals implied by trailing zeros (2,000,000 -> -6), a conservative reading of an untagged count."""
    number = int(Decimal(str(value)))
    if number <= 0:
        return None
    for decimals in (-6, -3):
        if number % 10 ** -decimals == 0:
            return decimals
    return None


def _repurchase(gaap, supplement, start, end, currency, as_of, split):
    """(price, shares, basis, tolerance) for one fiscal year, or Nones.

    The program's own value and share count come from one pair of concepts;
    cash-flow payments are never divided by program shares. A missing half is
    read from the annual filing (Microsoft tags its program value on the
    repurchase-program axis; Alphabet, Meta and Salesforce tag share counts only
    on the share-class or equity-component axis). A stated average price is the
    year's one price. ``tolerance`` is the relative error that a rounded share
    count allows in a computed price (Meta's 40 million: 1.25%).
    """
    supplement_filed = (supplement or {}).get('filing_date')
    pair = None
    for value_concept, share_concept in REPURCHASE_PAIRS:
        value, _, _ = _duration(gaap, (value_concept,), start, end, currency, as_of)
        value_basis, share_decimals = 'company facts', None
        if value is None:
            found = _supplement_amount(supplement, value_concept, start, end, currency)
            value, _, value_basis = (found if found else (None, None, None))
        shares, _, shares_filed = _duration(gaap, (share_concept,), start, end, currency, as_of)
        share_basis = 'company facts'
        if shares is None:
            found = _supplement_amount(supplement, share_concept, start, end, 'shares')
            if found:
                shares, share_decimals, share_basis = found
                shares_filed = supplement_filed
        elif shares:
            # Company facts carry no precision; Home Depot's FY2024 count of 2,000,000
            # is rounded to millions (a 25% price error) and was treated as exact.
            found = _supplement_amount(supplement, share_concept, start, end, 'shares')
            share_decimals = found[1] if found and Decimal(str(found[0])) == Decimal(str(shares)) else _trailing_precision(shares)
        if value and shares and value > 0 and shares > 0:
            pair = (Decimal(str(value)), Decimal(str(shares)), share_decimals, shares_filed,
                    f'{value_concept} / {share_concept}' + ''.join(
                        f'; {half} {basis} in the annual filing' for half, basis in
                        (('value', value_basis), ('shares', share_basis)) if basis != 'company facts'))
            break
    if pair and split and pair[3] and pair[3] < split:
        # Reported only before a later split restatement: another basis.
        return None, None, None, None
    stated, _, stated_filed = _duration(gaap, (STATED_REPURCHASE_PRICE,), start, end, currency, as_of)
    stated_basis = f'{STATED_REPURCHASE_PRICE} (stated)'
    if stated is None:
        found = _supplement_facts(supplement, {STATED_REPURCHASE_PRICE}, start, end, currency + '/shares')
        program = _supplement_facts(supplement, {STATED_REPURCHASE_PRICE}, start, end, currency + '/shares', axis=PROGRAM_AXIS)
        if len({fact['value'] for fact in found}) == 1:
            stated = Decimal(found[0]['value'])
        elif not found and len({(fact['dimensions'][PROGRAM_AXIS], fact['value']) for fact in program}) == 1:
            # One program's stated price is the year's price (Salesforce).
            stated = Decimal(program[0]['value'])
            stated_basis += ' on the repurchase-program axis in the annual filing'
        stated_filed = supplement_filed
    if stated is not None and stated > 0 and not (split and stated_filed and stated_filed < split):
        stated = Decimal(str(stated))
        if pair:
            computed = pair[0] / pair[1]
            note = '' if abs(computed / stated - 1) < Decimal('0.0005') else f', preferred to the ratio {pair[4]}'
            return float(stated), _plain(pair[1]), stated_basis + note, 0.0
        return float(stated), None, stated_basis, 0.0
    if pair:
        tolerance = _share_tolerance(pair[1], pair[2])
        if tolerance <= REPURCHASE_SHARE_PRECISION:
            return float(pair[0] / pair[1]), _plain(pair[1]), pair[4], float(tolerance)
    return None, None, None, None


def _acquisitions(gaap, supplement, start, end, currency, as_of):
    """(amount, concept) of cash paid for acquisitions, or (None, None).

    The second-business concept is added only when it exceeds the first, so it
    cannot be a part of it (NVIDIA: Groq $13.0 billion beside $1.5 billion). An
    extension total is used only when no standard concept is reported, and only
    when one extension concept is.
    """
    parts = []
    for concept in ACQUISITIONS:
        value, _, _ = _duration(gaap, (concept,), start, end, currency, as_of)
        if value is None:
            found = {Decimal(fact['value']) for fact in _supplement_facts(supplement, {concept}, start, end, currency)}
            value = found.pop() if len(found) == 1 else None
        if value is not None:
            parts.append((Decimal(str(value)), concept))
    if len(parts) == 2 and parts[1][0] > parts[0][0]:
        return _plain(parts[0][0] + parts[1][0]), ' + '.join(concept for _, concept in parts)
    if parts:
        return _plain(parts[0][0]), parts[0][1]
    extensions = {}
    for fact in (supplement or {}).get('facts') or []:
        if (not fact['dimensions'] and fact['start'] == start and fact['end'] == end and fact['unit'] == currency and
                ACQUISITION_EXTENSION.fullmatch(_local(fact['concept'])) and not fact['concept'].startswith('us-gaap:')):
            extensions.setdefault(fact['concept'], set()).add(Decimal(fact['value']))
    if len(extensions) == 1:
        (concept, values), = extensions.items()
        if len(values) == 1:
            return _plain(values.pop()), concept + ' (issuer extension in the annual filing)'
    return None, None


def _depreciation(gaap, supplement, start, end, currency, as_of):
    """(amount, concept, depreciation_only) from the depreciation family, or (None, None, False)."""
    totals = []
    for concept in DEPRECIATION_TOTALS:
        value, _, _ = _duration(gaap, (concept,), start, end, currency, as_of)
        if value is not None:
            totals.append((Decimal(str(value)), concept))
    for fact in (supplement or {}).get('facts') or []:
        if (not fact['dimensions'] and fact['start'] == start and fact['end'] == end and fact['unit'] == currency and
                not fact['concept'].startswith('us-gaap:') and DEPRECIATION_EXTENSION.fullmatch(_local(fact['concept']))):
            totals.append((Decimal(fact['value']), fact['concept'] + ' (issuer extension in the annual filing)'))
    if totals:
        value, concept = max(totals, key=lambda item: item[0])
        return _plain(value), concept, False
    value, concept, _ = _duration(gaap, DEPRECIATION_ONLY, start, end, currency, as_of)
    return (value, concept, True) if value is not None else (None, None, False)


def _cover_shares(payload, supplement, end, as_of, split):
    """(count, cover date, concept) from the first annual-report cover after ``end``, or Nones.

    Coca-Cola and Walmart report outstanding shares only on the 10-K cover; the
    date is kept so that the count is never read as a fiscal-year-end figure.
    """
    dei = ((payload or {}).get('facts') or {}).get('dei') or {}
    limit = (_day(end) + timedelta(days=COVER_WINDOW_DAYS)).isoformat()
    best = None
    for unit, row in _annual_rows(dei, COVER_SHARES, as_of):
        if unit in SHARE_UNITS and 'start' not in row and end < row['end'] <= limit:
            if best is None or (row['end'], row['filed']) < (best['end'], best['filed']):
                best = row
    if best is not None:
        if split and best['filed'] < split:
            return None, None, None
        return best['val'], best['end'], 'dei:' + COVER_SHARES
    if not supplement or (split and (supplement.get('filing_date') or '') < split):
        return None, None, None
    by_date = {}
    for fact in supplement.get('facts') or []:
        if (_local(fact['concept']) == COVER_SHARES and fact['unit'] == 'shares' and fact['start'] is None and
                end < fact['end'] <= limit and list(fact['dimensions']) in ([], [CLASS_AXIS])):
            by_date.setdefault(fact['end'], []).append(fact)
    for date_, facts in sorted(by_date.items()):
        plain = {fact['value'] for fact in facts if not fact['dimensions']}
        if len(plain) == 1:
            return _plain(Decimal(plain.pop())), date_, 'dei:' + COVER_SHARES + ' (annual filing)'
        classes = {}
        for fact in facts:
            if fact['dimensions']:
                classes.setdefault(fact['dimensions'][CLASS_AXIS], set()).add(fact['value'])
        if classes and all(len(values) == 1 for values in classes.values()):
            return (_plain(sum(Decimal(next(iter(values))) for values in classes.values())), date_,
                    'dei:' + COVER_SHARES + ' summed over share classes (annual filing)')
    return None, None, None


def _filing_shares(supplement, end, split):
    """A fiscal-year-end outstanding count from the annual filing (Meta tags it by share class only)."""
    if not supplement or (split and (supplement.get('filing_date') or '') < split):
        return None, None
    found = {}
    for fact in supplement.get('facts') or []:
        if _local(fact['concept']) in SHARES_OUTSTANDING and fact['end'] == end and fact['start'] is None and fact['unit'] == 'shares':
            found.setdefault(tuple(fact['dimensions'].items()), set()).add(fact['value'])
    plain = found.get((), set())
    if len(plain) == 1:
        return _plain(Decimal(plain.pop())), 'CommonStockSharesOutstanding (annual filing)'
    components = {values.pop() for key, values in found.items() if len(values) == 1 and key and key[0][0] == EQUITY_AXIS
                  and SHARE_COMPONENTS.fullmatch(_local(key[0][1])) and 'Treasury' not in key[0][1]}
    if len(components) == 1:
        return _plain(Decimal(components.pop())), 'CommonStockSharesOutstanding on the equity-component axis (annual filing)'
    classes = {key[0][1]: values for key, values in found.items() if key and key[0][0] == CLASS_AXIS}
    if classes and all(len(values) == 1 for values in classes.values()):
        return (_plain(sum(Decimal(next(iter(values))) for values in classes.values())),
                'CommonStockSharesOutstanding summed over share classes (annual filing)')
    return None, None


def _lease_commitments(supplement, end, currency):
    """(amount, concept) of leases signed but not yet commenced at ``end``, or (None, None).

    Issuers tag this differently: Costco and Home Depot with one extension
    concept, Alphabet with current and noncurrent extension amounts, Amazon and
    Apple with a purchase-obligation concept on a not-yet-commenced member, and
    Microsoft with one $329.1 billion amount tagged twice (operating and finance
    members of the lease axis), which is taken once. An amount that cannot be
    told apart is left out; the leases focus excerpt still carries the text.
    """
    facts = [fact for fact in (supplement or {}).get('facts') or []
             if fact['start'] is None and fact['end'] == end and fact['unit'] == currency]
    plain = {}
    for fact in facts:
        local = _local(fact['concept'])
        if not fact['dimensions'] and 'NotYetCommenced' in local and not re.search(r'Term|Prepayment|Payments?Due', local):
            plain.setdefault(fact['concept'], set()).add(Decimal(fact['value']))
    if any(len(values) != 1 for values in plain.values()):
        return None, None
    plain = {concept: values.pop() for concept, values in plain.items()}
    current = [concept for concept in plain if re.search(r'Current', _local(concept)) and not re.search(r'Noncurrent', _local(concept))]
    noncurrent = [concept for concept in plain if re.search(r'Noncurrent', _local(concept))]
    totals = [concept for concept in plain if concept not in current + noncurrent]
    if len(totals) == 1:
        return _plain(plain[totals[0]]), totals[0]
    if not totals and len(current) == 1 and len(noncurrent) == 1:
        return _plain(plain[current[0]] + plain[noncurrent[0]]), f'{current[0]} + {noncurrent[0]}'
    if plain:
        return None, None
    tagged = {}
    for fact in facts:
        if len(fact['dimensions']) == 1 and _local(fact['concept']) == LEASE_OBLIGATION:
            (axis, member), = fact['dimensions'].items()
            if 'NotYetCommenced' in member or axis == LEASE_TERM_AXIS:
                tagged.setdefault((fact['concept'], axis), {}).setdefault(member, set()).add(Decimal(fact['value']))
    if len(tagged) != 1:
        return None, None
    (concept, axis), members = tagged.popitem()
    if any(len(values) != 1 for values in members.values()):
        return None, None
    values = {next(iter(values)) for values in members.values()}
    if len(values) == 1:
        member_names = ', '.join(sorted(members))
        return _plain(values.pop()), f'{concept} on {member_names}' + (' (one amount tagged on each member, taken once)'
                                                                     if len(members) > 1 else '')
    return None, None


CONCEPT_FIELDS = ('revenue_concept', 'depreciation_amortization_concept', 'selling_general_administrative_concept',
                  'acquisitions_concept', 'average_repurchase_price_concept', 'shares_outstanding_concept',
                  'lease_commitments_not_commenced_concept')


def _from_filing(row):
    return (row.get('lease_commitments_not_commenced') is not None or
            any('annual filing' in (row.get(field) or '') for field in CONCEPT_FIELDS))


def historical_baseline(payload, years=5, as_of=None, supplement=None):
    """Reported operating history from a full SEC companyfacts payload.

    Returns rows oldest first. A ratio is None when either side is unreported.
    Average repurchase price divides a reported repurchase value by the share
    count of the same disclosure, never cash-flow payments or share-count
    changes, unless the filing states its own average price. Per-share and
    share-count values reported only before a later split restatement are left
    blank rather than mixed with the new basis.

    ``supplement`` (from ``filing_supplement``) adds facts from the latest annual
    filing's inline XBRL. Company facts decide wherever both report a value; the
    supplement fills what company facts cannot carry and is named in ``concepts``.
    """
    gaap = ((payload or {}).get('facts') or {}).get('us-gaap') or {}
    if supplement and as_of and (supplement.get('filing_date') or '') > as_of:
        supplement = None
    periods = _fiscal_years(gaap, years + 1, as_of)
    rows, concepts = [], {}
    split = _split_date(gaap, as_of)
    for period in periods:
        start, end, currency = period['start'], period['end'], period['currency']
        row = {'fiscal_year_start': start, 'fiscal_year_end': end, 'currency': currency, 'revenue': period['revenue'],
               'revenue_concept': period['revenue_concept']}
        concepts['revenue'] = period['revenue_concept']
        filed = {}
        for name, options in DURATIONS.items():
            row[name], used, filed[name] = _duration(gaap, options, start, end, currency, as_of)
            if used:
                concepts.setdefault(name, used)
                if name in SHARE_BASIS_FIELDS and split and filed[name] < split:
                    # Reported only before a later split restatement: another basis.
                    row[name] = None
                    row.setdefault('split_basis_blank', []).append(name)
        if row['selling_general_administrative'] is None:
            selling, selling_concept, _ = _duration(gaap, SELLING, start, end, currency, as_of)
            general, general_concept, _ = _duration(gaap, GENERAL, start, end, currency, as_of)
            if selling is not None and general is not None:
                row['selling_general_administrative'] = selling + general
                row['selling_general_administrative_concept'] = (f'derived: {selling_concept} + {general_concept}')
                concepts.setdefault('selling_general_administrative', row['selling_general_administrative_concept'])
        row['acquisitions'], row['acquisitions_concept'] = _acquisitions(gaap, supplement, start, end, currency, as_of)
        if row['acquisitions_concept']:
            concepts.setdefault('acquisitions', row['acquisitions_concept'])
        (row['depreciation_amortization'], row['depreciation_amortization_concept'],
         row['depreciation_only']) = _depreciation(gaap, supplement, start, end, currency, as_of)
        if row['depreciation_amortization_concept']:
            concepts.setdefault('depreciation_amortization', row['depreciation_amortization_concept'])
        (row['average_repurchase_price'], row['shares_repurchased'], row['average_repurchase_price_concept'],
         row['average_repurchase_price_tolerance']) = _repurchase(gaap, supplement, start, end, currency, as_of, split)
        if row['average_repurchase_price_concept']:
            concepts.setdefault('average_repurchase_price', row['average_repurchase_price_concept'])
        for name, options in INSTANTS.items():
            row[name], used = _instant(gaap, options, end, currency, as_of)
            if used:
                concepts.setdefault(name, used)
        for name, options in CAPITAL_INSTANTS.items():
            row[name], used = _instant(gaap, options, end, currency, as_of)
        row['shares_outstanding'], used = _share_instant(gaap, SHARES_OUTSTANDING, end, as_of)
        row['shares_outstanding_as_of'] = end if used else None
        if row['shares_outstanding'] is None and not used:
            row['shares_outstanding'], used = _filing_shares(supplement, end, split)
            row['shares_outstanding_as_of'] = end if used else None
        if row['shares_outstanding'] is None and not used:
            row['shares_outstanding'], row['shares_outstanding_as_of'], used = _cover_shares(payload, supplement, end, as_of, split)
        row['shares_outstanding_concept'] = used
        if used:
            concepts.setdefault('shares_outstanding', used)
        row['lease_commitments_not_commenced'] = row['lease_commitments_not_commenced_concept'] = None
        if supplement and supplement.get('report_date') == end:
            amount, concept = _lease_commitments(supplement, end, currency)
            row['lease_commitments_not_commenced'], row['lease_commitments_not_commenced_concept'] = amount, concept
            if concept:
                concepts['lease_commitments_not_commenced'] = concept
        if row['gross_profit'] is None and row['cost_of_revenue'] is not None:
            row['gross_profit'] = row['revenue'] - row['cost_of_revenue']
            row['gross_profit_derived'] = True
        rows.append(row)
    table = []
    for index, row in enumerate(rows):
        prior = rows[index - 1] if index else None
        revenue = row['revenue']
        pretax = row['pretax_income']
        if pretax is None and row['net_income'] is not None and row['income_tax_expense'] is not None:
            pretax = row['net_income'] + row['income_tax_expense']
        item = {key: row[key] for key in ('fiscal_year_end', 'currency', 'revenue', 'operating_income', 'net_income',
                                          'operating_cash_flow', 'capital_expenditure', 'depreciation_amortization',
                                          'stock_compensation', 'repurchases', 'average_repurchase_price', 'dividends',
                                          'diluted_shares', 'diluted_eps', 'inventory', 'research_and_development',
                                          'selling_general_administrative', 'gross_profit', 'shares_outstanding',
                                          'shares_repurchased', 'acquisitions', 'finance_lease_additions',
                                          'lease_commitments_not_commenced', 'revenue_concept',
                                          'depreciation_amortization_concept', 'depreciation_only',
                                          'acquisitions_concept', 'average_repurchase_price_concept',
                                          'average_repurchase_price_tolerance',
                                          'shares_outstanding_as_of', 'shares_outstanding_concept',
                                          'lease_commitments_not_commenced_concept')}
        item['selling_general_administrative_concept'] = row.get('selling_general_administrative_concept')
        # A change between counts on different dates or bases is not a change.
        same_basis = bool(prior and (row['shares_outstanding_as_of'] == row['fiscal_year_end']) ==
                          (prior['shares_outstanding_as_of'] == prior['fiscal_year_end']))
        def change(key):
            return row[key] - prior[key] if prior and row[key] is not None and prior[key] is not None else None
        def growth(key):
            # Growth from a negative or zero base has no meaning.
            return (_ratio(change(key), prior[key]) if prior and prior[key] is not None and prior[key] > 0
                    and row[key] is not None else None)
        # Return on beginning invested capital: operating income after the year's
        # effective tax rate (statutory 25% when that is missing or outside 0-50%).
        capital = _invested_capital(row)
        prior_capital = _invested_capital(prior) if prior else None
        tax_rate = (_ratio(row['income_tax_expense'], pretax) if (pretax or 0) > 0 else None)
        tax_rate = tax_rate if isinstance(tax_rate, float) and 0 <= tax_rate <= .5 else STATUTORY_TAX_FALLBACK
        after_tax = row['operating_income'] * (1 - tax_rate) if isinstance(row['operating_income'], (int, float)) else None
        roic = after_tax / prior_capital if after_tax is not None and prior_capital and prior_capital > 0 else None
        item.update(invested_capital=capital, return_on_invested_capital=roic,
                    return_on_capital_unbounded=bool(after_tax and after_tax > 0 and prior_capital is not None and prior_capital <= 0))
        item.update(
            net_share_change=change('shares_outstanding') if same_basis else None,
            operating_income_growth=growth('operating_income'),
            diluted_eps_growth=growth('diluted_eps'),
            revenue_growth=_ratio(revenue - prior['revenue'], prior['revenue']) if prior else None,
            gross_margin=_ratio(row['gross_profit'], revenue), operating_margin=_ratio(row['operating_income'], revenue),
            research_and_development_to_revenue=_ratio(row['research_and_development'], revenue),
            selling_general_administrative_to_revenue=_ratio(row['selling_general_administrative'], revenue),
            effective_tax_rate=_ratio(row['income_tax_expense'], pretax) if (pretax or 0) > 0 else None,
            cash_tax_rate=_ratio(row['income_taxes_paid'], pretax) if (pretax or 0) > 0 else None,
            capital_expenditure_to_revenue=_ratio(row['capital_expenditure'], revenue),
            depreciation_amortization_to_revenue=_ratio(row['depreciation_amortization'], revenue),
            stock_compensation_to_revenue=_ratio(row['stock_compensation'], revenue),
            gross_profit_derived=bool(row.get('gross_profit_derived')))
        table.append(item)
    # The first period exists only to compute the first year's growth.
    table = table[1:] if len(table) > years else table
    result = {'rows': table, 'concepts': concepts, 'source': 'SEC companyfacts annual filings (10-K/20-F/40-F)',
              'basis': 'Reported annual values; ratios computed by Python. Missing concepts are blank, never zero.'}
    if supplement and any(_from_filing(row) for row in table):
        result['supplement_source'] = supplement.get('source_id')
        result['source'] += f'; gaps filled from the {supplement.get("form") or "annual"} filed {supplement.get("filing_date")}'
    return result


def _member_label(member):
    if ' within ' in member:
        # A product or region reported within one segment (research.inline_breakdown_facts).
        inner, segment = member.split(' within ', 1)
        return f'{_member_label(inner)} in {_member_label(segment)}'
    local = member.split(':', 1)[-1]
    local = re.sub(r'Member$', '', local)
    return re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', local)


def breakdown_tables(facts, years=3):
    """Annual revenue, cost and operating income by member, with growth and margins.

    ``facts`` come from ``research.inline_breakdown_facts``. Only annual
    periods (350-380 days) are tabled. Members on one axis can overlap (for
    example Products and iPhone), so members are never summed here.
    """
    def short(concept):
        return concept.split(':', 1)[-1]
    revenue = set(REVENUE)
    cost = {'CostOfGoodsAndServicesSold', 'CostOfRevenue'}
    annual = [fact for fact in facts if 350 <= (_day(fact['end_date']) - _day(fact['start_date'])).days <= 380]
    output = {}
    for axis in ('product_or_service', 'segment', 'geography'):
        rows = [fact for fact in annual if fact['axis'] == axis]
        if not rows:
            continue
        ends = sorted({fact['end_date'] for fact in rows if short(fact['concept']) in revenue}, reverse=True)[:years]
        if not ends:
            continue
        members = []
        for member in dict.fromkeys(fact['member'] for fact in rows):
            def value(concepts, end):
                # The first concept with one consistent value decides; a filer that
                # tags both total revenues and net sales is not a conflict.
                for concept in concepts:
                    found = {Decimal(fact['normalized_decimal']) for fact in rows if fact['member'] == member and
                             fact['end_date'] == end and short(fact['concept']) == concept}
                    if len(found) == 1:
                        return found.pop()
                    if found:
                        return None
                return None
            sales = [value(REVENUE, end) for end in ends]
            if not any(item is not None for item in sales):
                continue
            costs = [value(('CostOfGoodsAndServicesSold', 'CostOfRevenue'), end) for end in ends]
            operating = [value(('OperatingIncomeLoss',), end) for end in ends]
            entry = {'member': member, 'label': _member_label(member), 'revenue': sales,
                     'revenue_growth': [float(sales[i] / sales[i + 1] - 1) if i + 1 < len(sales) and sales[i] is not None
                                        and sales[i + 1] else None for i in range(len(sales))]}
            if any(item is not None for item in costs):
                entry['cost_of_revenue'] = costs
                entry['gross_margin'] = [float(1 - c / s) if c is not None and s else None for c, s in zip(costs, sales)]
            if any(item is not None for item in operating):
                entry['operating_income'] = operating
                entry['operating_margin'] = [float(o / s) if o is not None and s else None for o, s in zip(operating, sales)]
            for key in ('revenue', 'cost_of_revenue', 'operating_income'):
                if key in entry:
                    entry[key] = [None if item is None else int(item) if item == item.to_integral_value() else float(item)
                                  for item in entry[key]]
            members.append(entry)
        if members:
            output[axis] = {'fiscal_year_ends': ends, 'members': members,
                            'note': 'Members on one axis can overlap; they are not summed.'}
    return output


def _money(value):
    if value is None:
        return 'n/a'
    return f'{float(value) / 1e9:,.1f}'


def _pct(value):
    return 'n/a' if value is None else f'{value * 100:.1f}%'


def _basis_notes(rows, source_id, filing_id):
    """One line per column whose basis is not the plain reported concept, with the years it covers."""
    def cite(concept):
        return f' [{filing_id}]' if filing_id and 'annual filing' in concept else (f' [{source_id}]' if source_id else '')
    def by_concept(field, wanted):
        groups = {}
        for row in rows:
            concept = row.get(field)
            if concept and wanted(row, concept):
                groups.setdefault(concept, []).append(row['fiscal_year_end'])
        return '; '.join(f'{concept} for FY ending {", ".join(ends)}{cite(concept)}' for concept, ends in groups.items())
    notes = []
    depreciation = {row.get('depreciation_amortization_concept') for row in rows if row.get('depreciation_amortization_concept')}
    if len(depreciation) > 1 or any('extension' in concept or row.get('depreciation_only') for row in rows
                                    for concept in [row.get('depreciation_amortization_concept') or '']):
        notes.append('D&A column: ' + by_concept('depreciation_amortization_concept', lambda row, concept: True) +
                     ('. us-gaap:Depreciation is depreciation only, reported where no depreciation-and-amortization '
                      'total is' if any(row.get('depreciation_only') for row in rows) else ''))
    text = by_concept('selling_general_administrative_concept', lambda row, concept: concept.startswith('derived'))
    if text:
        notes.append('SG&A column: ' + text + ', summed by Python')
    text = by_concept('acquisitions_concept', lambda row, concept: '+' in concept)
    if text:
        notes.append('Acquisitions column: ' + text + ', two cash-flow lines summed by Python')
    text = by_concept('acquisitions_concept', lambda row, concept: 'extension' in concept)
    if text:
        notes.append('Acquisitions column: ' + text + '. An issuer-extension line can include purchases of '
                     'intangibles or investments, as its name states')
    text = by_concept('average_repurchase_price_concept', lambda row, concept: 'stated' in concept or 'annual filing' in concept)
    if text:
        notes.append('Average buyback price: ' + text)
    rounded = [f'{row["fiscal_year_end"]} ±{row["average_repurchase_price_tolerance"] * 100:.1f}%' for row in rows
               if row.get('average_repurchase_price') is not None and (row.get('average_repurchase_price_tolerance') or 0) > .005]
    if rounded:
        notes.append('Average buyback price marked ~ divides by a share count the filing rounds, so it is good only to '
                     + ', '.join(rounded))
    text = by_concept('shares_outstanding_concept', lambda row, concept: row.get('shares_outstanding_as_of') != row['fiscal_year_end']
                      or 'annual filing' in concept)
    if text:
        notes.append('Shares outstanding: ' + text + '. A cover-page count is dated after the fiscal year end; '
                     'changes are shown only between counts on the same basis')
    latest = rows[-1]
    if latest.get('lease_commitments_not_commenced') is not None:
        notes.append(f'Leases signed but not yet commenced at {latest["fiscal_year_end"]}: '
                     f'{_money(latest["lease_commitments_not_commenced"])} billion of future lease payments, not in '
                     f'reported lease liabilities ({latest["lease_commitments_not_commenced_concept"]})'
                     f'{f" [{filing_id}]" if filing_id else ""}')
    return notes


def render_baseline(baseline, source_id=None):
    rows = (baseline or {}).get('rows') or []
    if not rows:
        return ''
    ref = f' [{source_id}]' if source_id else ''
    currency = rows[-1].get('currency', '')
    reported = [row for row in rows if row.get('depreciation_amortization') is not None]
    legacy = (baseline.get('concepts') or {}).get('depreciation_amortization') == 'Depreciation'
    only = all(row.get('depreciation_only') for row in reported) if any('depreciation_only' in row for row in reported) else legacy
    depreciation = 'depreciation only/rev' if reported and only else 'D&A/rev'
    lines = [f'Reported operating history{ref}, {currency} billions; ratios computed by Python from reported values; '
             'n/a means not reported on a comparable basis.',
             'FY end | revenue | growth | gross margin | operating margin | operating income growth | R&D/rev | SG&A/rev | '
             f'effective tax | capex/rev | {depreciation} | SBC/rev | buybacks (cash) | avg buyback price | dividends | '
             'diluted EPS | EPS growth | shares outstanding at FY end (millions) | change in shares outstanding (millions) | '
             'shares repurchased (millions) | acquisitions (cash) | finance-lease additions | invested capital | '
             'return on invested capital']
    for row in rows:
        price = row.get('average_repurchase_price')
        eps = row.get('diluted_eps')
        shares = _shares(row.get('shares_outstanding'))
        as_of = row.get('shares_outstanding_as_of')
        if row.get('shares_outstanding') is not None and as_of and as_of != row['fiscal_year_end']:
            shares += f' (cover {as_of})'
        price_text = 'n/a' if price is None else f'{price:,.2f}'
        if price is not None and (row.get('average_repurchase_price_tolerance') or 0) > .005:
            price_text = '~' + price_text
        lines.append(' | '.join([
            row['fiscal_year_end'], _money(row['revenue']), _pct(row['revenue_growth']), _pct(row['gross_margin']),
            _pct(row['operating_margin']), _pct(row.get('operating_income_growth')),
            _pct(row['research_and_development_to_revenue']),
            _pct(row['selling_general_administrative_to_revenue']), _pct(row['effective_tax_rate']),
            _pct(row['capital_expenditure_to_revenue']), _pct(row['depreciation_amortization_to_revenue']),
            _pct(row['stock_compensation_to_revenue']), _money(row['repurchases']),
            price_text, _money(row['dividends']),
            'n/a' if eps is None else f'{eps:,.2f}', _pct(row.get('diluted_eps_growth')),
            shares, _shares(row.get('net_share_change'), signed=True),
            _shares(row.get('shares_repurchased')), _money(row.get('acquisitions')),
            _money(row.get('finance_lease_additions')), _money(row.get('invested_capital')),
            'n/m (capital at or below zero)' if row.get('return_on_capital_unbounded') else
            _pct(row.get('return_on_invested_capital'))]))
    notes = _basis_notes(rows, source_id, baseline.get('supplement_source'))
    if any(row.get('return_on_invested_capital') is not None or row.get('return_on_capital_unbounded') for row in rows):
        notes = [*notes, 'Return on invested capital: operating income after the year\'s effective tax rate over the prior '
                         'year-end equity plus debt and finance leases less cash and marketable securities; an approximation '
                         'from standard concepts, missing lines counted as zero']
    if notes:
        lines.append('Basis notes:')
        lines.extend('- ' + note + '.' for note in notes)
    return '\n'.join(lines)


def _shares(value, signed=False):
    if value is None:
        return 'n/a'
    return f'{value / 1e6:+,.1f}' if signed else f'{value / 1e6:,.1f}'


def render_breakdowns(tables, source_id=None):
    if not tables:
        return ''
    ref = f' [{source_id}]' if source_id else ''
    titles = {'product_or_service': 'by product or service', 'segment': 'by reportable segment', 'geography': 'by geography'}
    lines = []
    for axis, table in tables.items():
        ends = table['fiscal_year_ends']
        lines.append(f'Reported annual revenue {titles[axis]}{ref}, billions; fiscal years ending {", ".join(ends)} '
                     '(latest first); members can overlap and are not summed.')
        for entry in table['members']:
            parts = [f"{entry['label']}: revenue " + ' / '.join(_money(v) for v in entry['revenue']),
                     'growth ' + ' / '.join(_pct(v) for v in entry['revenue_growth'][:-1] or [None])]
            if 'gross_margin' in entry:
                parts.append('gross margin ' + ' / '.join(_pct(v) for v in entry['gross_margin']))
            if 'operating_margin' in entry:
                parts.append('operating income ' + ' / '.join(_money(v) for v in entry['operating_income']) +
                             ', margin ' + ' / '.join(_pct(v) for v in entry['operating_margin']))
            lines.append('- ' + '; '.join(parts))
    return '\n'.join(lines)

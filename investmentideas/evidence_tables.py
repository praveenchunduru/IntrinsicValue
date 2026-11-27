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

from . import country_risk, memo_style, roic

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
# Return on invested capital follows Mauboussin and Callahan (Counterpoint
# Global, October 2022); the method and its checks are in roic.py. Extra fiscal
# years open the averages, the three-year incremental return and the six-year
# R&D vintages of the intangible adjustment.
ROIC_EXTRA_YEARS = max(life for _, life in roic.CAPITALIZATION.values()) + 2


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


def _research_and_development(gaap, start, end, currency, as_of=None):
    """(value, concept, filed) for R&D: the larger of the inclusive concept and its part.

    ResearchAndDevelopmentExpense includes acquired in-process R&D, so it cannot be
    smaller than ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost.
    Johnson & Johnson tags only its acquired in-process charge with the inclusive
    concept ($0.1 billion for 2025 against $14.7 billion of R&D), which put its
    R&D at 0.1% of revenue.
    """
    found = [_duration(gaap, (concept,), start, end, currency, as_of) for concept in DURATIONS['research_and_development']]
    found = [item for item in found if item[0] is not None]
    return max(found, key=lambda item: item[0]) if found else (None, None, None)


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
            row[name], used, filed[name] = (_research_and_development(gaap, start, end, currency, as_of)
                                            if name == 'research_and_development' else
                                            _duration(gaap, options, start, end, currency, as_of))
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
        item['fiscal_year_start'] = row['fiscal_year_start']
        # A change between counts on different dates or bases is not a change.
        same_basis = bool(prior and (row['shares_outstanding_as_of'] == row['fiscal_year_end']) ==
                          (prior['shares_outstanding_as_of'] == prior['fiscal_year_end']))
        def change(key):
            return row[key] - prior[key] if prior and row[key] is not None and prior[key] is not None else None
        def growth(key):
            # Growth from a negative or zero base has no meaning.
            return (_ratio(change(key), prior[key]) if prior and prior[key] is not None and prior[key] > 0
                    and row[key] is not None else None)
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
    _attach_roic(table, gaap, as_of)
    # The first period exists only to compute the first year's growth.
    table = table[1:] if len(table) > years else table
    _mark_outside_supplement(table, supplement)
    result = {'rows': table, 'concepts': concepts, 'source': 'SEC companyfacts annual filings (10-K/20-F/40-F)',
              'basis': 'Reported annual values; ratios computed by Python. Missing concepts are blank, never zero.'}
    if supplement and any(_from_filing(row) for row in table):
        result['supplement_source'] = supplement.get('source_id')
        result['source'] += f'; gaps filled from the {supplement.get("form") or "annual"} filed {supplement.get("filing_date")}'
    quarterly = quarterly_table(payload, as_of)
    if quarterly:
        result['quarterly'] = quarterly
    return result


# Quarterly rate of change (Druckenmiller lens, 2026-11-26 critique): the annual
# table showed Nvidia's 0.2% growth and 16% margin for the year to January 2023
# while its quarters ran at 7%, 10% and then 30%; Meta's annual +37% hid quarterly
# growth that had fallen from +56% to -1%. The quarters are already in the
# company-facts record.
QUARTERS_SHOWN = 8
QUARTERLY_FORMS = ANNUAL_FORMS | {'10-Q', '10-Q/A'}
# A fiscal quarter is 12-14 weeks; Costco's fourth quarter is 16 weeks.
QUARTER_DAYS = (75, 120)
# Contract liabilities first: the concept that replaced deferred revenue under ASC 606.
DEFERRED_REVENUE = ('ContractWithCustomerLiabilityCurrent', 'DeferredRevenueCurrent', 'ContractWithCustomerLiability',
                    'DeferredRevenue')
# Quarters longer or shorter than their year-earlier quarter by more than this many
# days get no growth figure: Home Depot's 13-week fourth quarter of fiscal 2025 against
# 14 weeks a year earlier showed -3.8% where sales per day rose 3.6%.
QUARTER_LENGTH_DAYS = 3


def _reported_periods(gaap, concept, currency, as_of=None):
    """{(start, end): [(value, filed), ...]}: every reported value of the period with each filing date, by ``as_of``."""
    found = {}
    for unit, rows in ((gaap.get(concept) or {}).get('units') or {}).items():
        if unit != currency or not isinstance(rows, list):
            continue
        for row in rows:
            if (not isinstance(row, dict) or row.get('form') not in QUARTERLY_FORMS or
                    not isinstance(row.get('val'), (int, float)) or isinstance(row.get('val'), bool) or
                    not _day(row.get('end')) or not _day(row.get('filed')) or (as_of and row['filed'] > as_of)):
                continue
            found.setdefault((row.get('start'), row['end']), set()).add((row['val'], row['filed']))
    return {key: sorted(values, key=lambda item: (item[1], item[0])) for key, values in found.items()}


def _latest_value(vintages):
    return max(vintages, key=lambda item: item[1]) if vintages else None


# Two cumulative figures of one fiscal year are paired from filings at most this far
# apart, the later period filed last: a fourth quarter is the 10-K less the third
# quarter's 10-Q, both before or both after a restatement. Johnson & Johnson's nine
# months of 2022 were restated in October 2023 without Kenvue; the 10-K less the
# restated figure gave a fourth quarter of $34.9 billion against about $23.7 billion.
VINTAGE_PAIR_DAYS = 200


def _quarter_value(series, fiscal_start, start, end, previous_end, fiscal_end):
    """(value, basis, filed) for one fiscal quarter: as reported, or one cumulative period less another.

    A derived quarter pairs the latest filing of the later cumulative period with the
    latest filing of the earlier one made on or before it; a pair more than
    VINTAGE_PAIR_DAYS apart mixes vintages and is not used.
    """
    direct = _latest_value(series.get((start, end)))
    if direct:
        return direct[0], 'reported', direct[1]
    if not previous_end:
        return None, None, None
    later, earlier = series.get((fiscal_start, end)) or [], series.get((fiscal_start, previous_end)) or []
    if not later or not earlier:
        return None, None, None
    for value, filed in sorted(later, key=lambda item: item[1], reverse=True):
        before = _latest_value([item for item in earlier if item[1] <= filed])
        if before and (_day(filed) - _day(before[1])).days <= VINTAGE_PAIR_DAYS:
            basis = ('fiscal year less nine months' if end == fiscal_end else
                     'year to date less the previous year to date')
            return value - before[0], basis, filed
    return None, 'no figures of one vintage', None


def _comparative_growth(series, row, prior):
    """(growth, basis) on the year-earlier quarter from comparatives in the same filings, or (None, None).

    A 10-Q reports each quarter beside the year-earlier one, so the latest filing that
    reports both gives growth on one basis. A derived quarter uses the same two filings
    for both years: the fourth quarter is the 10-K's year less the third-quarter 10-Q's
    nine months, and the 10-K and that 10-Q also report the year-earlier figures.
    """
    def by_filing(key):
        return {filed: value for value, filed in series.get(key) or []}
    now, then = by_filing((row['period_start'], row['period_end'])), by_filing((prior['period_start'], prior['period_end']))
    for filed in sorted(set(now) & set(then), reverse=True):
        if then[filed] and then[filed] > 0:
            return now[filed] / then[filed] - 1, 'same_filing'
    (start, previous, _), (prior_start, prior_previous, _) = row['_frame'], prior['_frame']
    if not previous or not prior_previous:
        return None, None
    later, earlier = by_filing((start, row['period_end'])), by_filing((start, previous))
    prior_later, prior_earlier = by_filing((prior_start, prior['period_end'])), by_filing((prior_start, prior_previous))
    for first in sorted(set(later) & set(prior_later), reverse=True):
        for second in sorted(set(earlier) & set(prior_earlier), reverse=True):
            if second <= first and (_day(first) - _day(second)).days <= VINTAGE_PAIR_DAYS:
                before = prior_later[first] - prior_earlier[second]
                if before > 0:
                    return (later[first] - earlier[second]) / before - 1, 'same_filings'
    return None, None


def quarterly_table(payload, as_of=None, quarters=QUARTERS_SHOWN):
    """The latest fiscal quarters from the company-facts record, oldest first, or None.

    Each quarter is reported on its own where the filing does so, else derived from
    cumulative periods of the same fiscal year: the fourth quarter as the fiscal year
    less nine months, a second or third quarter as one year to date less the last.
    Revenue uses the concept the annual history chose for that fiscal year. Growth
    compares a quarter with the one ending 350-380 days earlier, from comparatives in
    the same filings (_comparative_growth), so a restatement between the years is not
    read as growth; with no such filings it is n/a. Quarters whose lengths differ by more than QUARTER_LENGTH_DAYS (a 14-week
    quarter against a 13-week one) get no growth figure. Inventory and deferred
    revenue are balance-sheet values at each quarter end where tagged.
    """
    gaap = ((payload or {}).get('facts') or {}).get('us-gaap') or {}
    years = _fiscal_years(gaap, 4, as_of)
    if not years:
        return None
    currency = years[-1]['currency']
    following = (_day(years[-1]['end']) + timedelta(days=1)).isoformat()
    frames = [(year['start'], year['end'], year['revenue_concept']) for year in years if year['currency'] == currency]
    frames.append((following, None, years[-1]['revenue_concept']))
    income = _reported_periods(gaap, 'OperatingIncomeLoss', currency, as_of)
    series = {concept: _reported_periods(gaap, concept, currency, as_of) for concept in REVENUE}

    def fiscal_quarters(fiscal_start, fiscal_end, concept):
        revenue = series[concept]
        limit = fiscal_end or (_day(fiscal_start) + timedelta(days=340)).isoformat()
        ends = sorted(end for start, end in revenue if start == fiscal_start and end < limit and
                      QUARTER_DAYS[0] <= (_day(end) - _day(start)).days <= 300)
        if fiscal_end:
            ends.append(fiscal_end)
        found, previous = [], None
        for number, end in enumerate(ends, 1):
            start = fiscal_start if previous is None else (_day(previous) + timedelta(days=1)).isoformat()
            if number > 4 or not QUARTER_DAYS[0] <= (_day(end) - _day(start)).days <= QUARTER_DAYS[1]:
                break
            sales, sales_basis, _ = _quarter_value(revenue, fiscal_start, start, end, previous, fiscal_end)
            if sales is None:
                break
            operating, operating_basis, _ = _quarter_value(income, fiscal_start, start, end, previous, fiscal_end)
            found.append({'period_start': start, 'period_end': end, 'days': (_day(end) - _day(start)).days + 1,
                          'fiscal_quarter': number, 'revenue': sales,
                          'revenue_basis': sales_basis, 'revenue_concept': concept, 'operating_income': operating,
                          'operating_income_basis': operating_basis,
                          'operating_margin': operating / sales if operating is not None and sales > 0 else None,
                          '_frame': (fiscal_start, previous, fiscal_end)})
            previous = end
        return found

    rows = []
    for fiscal_start, fiscal_end, concept in frames:
        # The annual history's concept first; quarterly reports can use another
        # (Nvidia's 10-Qs tag Revenues for years whose annual total is contract revenue).
        # One concept per fiscal year, so a derived quarter never mixes scopes.
        options = [fiscal_quarters(fiscal_start, fiscal_end, option)
                   for option in (concept, *(item for item in REVENUE if item != concept))]
        rows += max(options, key=len)
    # The latest unbroken run of quarters.
    run = rows[-1:]
    for row in reversed(rows[:-1]):
        if _day(run[0]['period_start']) - _day(row['period_end']) != timedelta(days=1):
            break
        run.insert(0, row)
    rows = run
    for row in rows:
        end = _day(row['period_end'])
        row['revenue_growth'] = row['revenue_growth_basis'] = None
        prior = next((other for other in rows if 350 <= (end - _day(other['period_end'])).days <= 380), None)
        if prior is None:
            continue
        if abs(row['days'] - prior['days']) > QUARTER_LENGTH_DAYS:
            row['revenue_growth_basis'] = 'unequal_length'
            row['prior_days'] = prior['days']
            continue
        for concept in dict.fromkeys((row['revenue_concept'], prior['revenue_concept'])):
            growth, basis = _comparative_growth(series[concept], row, prior)
            if basis:
                row['revenue_growth'], row['revenue_growth_basis'] = growth, basis
                break
        else:
            row['revenue_growth_basis'] = 'no_comparative'
    for row in rows:
        row.pop('_frame', None)
    rows = rows[-quarters:]
    if len(rows) < 4:
        return None
    instants = {}
    for name, options in (('inventory', ('InventoryNet',)), ('deferred_revenue', DEFERRED_REVENUE)):
        for option in options:
            values = {end: _latest_value(vintages)[0]
                      for (start, end), vintages in _reported_periods(gaap, option, currency, as_of).items() if start is None}
            if any(row['period_end'] in values for row in rows):
                instants[name] = option
                for row in rows:
                    row[name] = values.get(row['period_end'])
                break
    output = {'rows': rows, 'currency': currency, 'concepts': instants,
              'basis': 'Fiscal quarters from 10-Q and 10-K facts in the SEC company-facts record; a quarter not '
                       'reported on its own is one cumulative period less another of the same fiscal year.'}
    last = rows[-4:]
    if (all(row['revenue'] is not None and row['operating_income'] is not None for row in last) and
            all(_day(b['period_start']) - _day(a['period_end']) == timedelta(days=1) for a, b in zip(last, last[1:])) and
            last[-1]['period_end'] > years[-1]['end']):
        sales, operating = sum(row['revenue'] for row in last), sum(row['operating_income'] for row in last)
        if sales > 0:
            output['trailing_twelve_months'] = {
                'period': {'start_date': last[0]['period_start'], 'end_date': last[-1]['period_end']},
                'days': (_day(last[-1]['period_end']) - _day(last[0]['period_start'])).days + 1,
                'revenue': sales, 'operating_income': operating, 'operating_margin': operating / sales,
                'quarters': len(last), 'quarters_after_fiscal_year': sum(row['period_end'] > years[-1]['end'] for row in last),
                'basis': 'The four latest fiscal quarters summed.'}
    return output


# The filing's own row label where the member name loses it (2026-11-27 memo audits).
# Each was checked against the filing's revenue table. Inline facts carry no labels,
# so names not listed here are split from the member name.
FILING_LABELS = {
    'aapl:WearablesHomeandAccessoriesMember': 'Wearables, Home and Accessories',
    'goog:GoogleSearchOtherMember': 'Google Search & other',
    'goog:YouTubeAdvertisingRevenueMember': 'YouTube ads',
    'goog:GoogleNetworkMember': 'Google Network',
    'goog:GoogleAdvertisingRevenueMember': 'Google advertising',
    'goog:SubscriptionsPlatformsAndDevicesRevenueMember': 'Google subscriptions, platforms, and devices',
    'msft:XBOXMember': 'Gaming',
    'msft:SearchAdvertisingMember': 'Search and news advertising',
    'ko:A.PacificMember': 'Asia Pacific',
    'ko:EuropeMiddleEastAfricaMember': 'Europe, Middle East and Africa',
}
# Brand names that the camel-case split breaks apart.
BRANDS = {'You Tube': 'YouTube', 'Linked In': 'LinkedIn', 'Git Hub': 'GitHub', 'Pay Pal': 'PayPal',
          'Whats App': 'WhatsApp', 'Air Pods': 'AirPods', 'Apple Care': 'AppleCare', 'Mac Book': 'MacBook',
          'Play Station': 'PlayStation', 'Three Six Five': '365'}
CONNECTORS = ('And', 'Or', 'Of', 'The', 'In', 'For', 'Excluding', 'Including', 'Other')
# A glued lowercase "and" ("Homeand") is split off unless the word is one that ends in -and.
_GLUED_AND = re.compile(r'(?<=[a-z]{3})(?<![lmprtwh])and(?=[A-Z])')


def _member_label(member, issuer_prefixes=()):
    """A reader's label for a breakdown member: the filing's own label where known, else the split member name."""
    if ' within ' in member:
        # A product or region reported within one segment (research.inline_breakdown_facts).
        inner, segment = member.split(' within ', 1)
        return f'{_member_label(inner, issuer_prefixes)} in {_member_label(segment, issuer_prefixes)}'
    if member in FILING_LABELS:
        return FILING_LABELS[member]
    if member.startswith('country:'):
        for prefix in issuer_prefixes:
            label = (country_risk.ISSUER_PREFIX_LABELS.get(prefix) or {}).get(member)
            if label:
                return label
        return country_risk.ISO_COUNTRIES.get(member.split(':', 1)[1].upper(), member.split(':', 1)[1])
    local = re.sub(r'Member$', '', member.split(':', 1)[-1])
    if local in ('NonUs', 'NonUS'):
        return 'Non-US'
    local = _GLUED_AND.sub('And', local)
    text = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', local)
    for broken, brand in BRANDS.items():
        text = re.sub(r'\b' + broken + r'\b', brand, text)
    return ' '.join(word if index == 0 or word not in CONNECTORS or word == 'Other' else word.lower()
                    for index, word in enumerate(text.split()))


def _issuer_prefixes(members):
    """Issuer-extension prefixes among a table's members ('aapl' for aapl:OtherCountriesMember)."""
    standard = {'us-gaap', 'srt', 'country', 'dei', 'ifrs-full'}
    return tuple(sorted({member.split(':', 1)[0] for item in members for member in item.split(' within ')
                         if ':' in member and member.split(':', 1)[0] not in standard}))


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
        prefixes = _issuer_prefixes({fact['member'] for fact in rows})
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
            entry = {'member': member, 'label': _member_label(member, prefixes), 'revenue': sales,
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


def _money(value, scale=1e9):
    """An amount in billions (prompts), or in the memo table's own scale: one decimal in billions, whole below."""
    if value is None:
        return 'n/a'
    text = f'{float(value) / scale:,.{1 if scale >= 1e9 else 0}f}'
    return '0' if scale < 1e9 and not text.strip('-0.,') else text


def scale_of(values):
    """(letter, divisor) for a memo table, by memo_style.table_scale."""
    letter, divisor, _ = memo_style.table_scale([float(value) for value in values if isinstance(value, (int, float))])
    return letter, divisor


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
        only = all(row.get('depreciation_only') for row in rows if row.get('depreciation_amortization') is not None)
        notes.append(('Depreciation column: ' if only else 'D&A column: ') +
                     by_concept('depreciation_amortization_concept', lambda row, concept: True) +
                     ('. us-gaap:Depreciation is depreciation only, reported where no depreciation-and-amortization '
                      'total is' if any(row.get('depreciation_only') for row in rows) else ''))
    text = by_concept('selling_general_administrative_concept', lambda row, concept: concept.startswith('derived'))
    if text:
        notes.append('SG&A column: ' + text + ', summed')
    text = by_concept('acquisitions_concept', lambda row, concept: '+' in concept)
    if text:
        notes.append('Acquisitions column: ' + text + ', two cash-flow lines summed')
    text = by_concept('acquisitions_concept', lambda row, concept: 'extension' in concept)
    if text:
        names = sorted({re.sub(r'\s*\(.*$', '', row['acquisitions_concept']).split(':')[-1] for row in rows
                        if 'extension' in (row.get('acquisitions_concept') or '')})
        scope = '; '.join(re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', name).lower() for name in names)
        notes.append('Acquisitions column: ' + text + f'. The issuer line covers {scope}, as its name says, so it can '
                     'include more than business acquisitions')
    text = by_concept('average_repurchase_price_concept', lambda row, concept: 'stated' in concept or 'annual filing' in concept)
    if text:
        notes.append('Average buyback price: ' + text)
    label, differing = repurchase_price_basis(rows)
    if differing:
        notes.append(f'Average buyback price is the {label}. Cash buybacks over the same shares give a different '
                     'price: ' + '; '.join(f'{end} {price:,.2f} against {cash:,.2f}' for end, price, cash in differing))
    outside = {}
    for row in rows:
        for field in row.get('outside_filings_scanned') or []:
            outside.setdefault(field, []).append(row['fiscal_year_end'])
    for field, _, name in SUPPLEMENT_FIELDS:
        if field in outside:
            notes.append(f'{name} for the fiscal years ending {", ".join(outside[field])} is shown as outside filings '
                         f'scanned: the figure comes from the annual filing{f" [{filing_id}]" if filing_id else ""}, whose '
                         'periods do not include those years; older filings report it')
    rounded = [f'±{row["average_repurchase_price_tolerance"] * 100:.1f}% for FY{row["fiscal_year_end"][:4]}' for row in rows
               if row.get('average_repurchase_price') is not None and (row.get('average_repurchase_price_tolerance') or 0) > .005]
    if rounded:
        notes.append('Average buyback price marked ~ divides by a share count the filing rounds, so it is accurate to '
                     'within ' + (rounded[0] if len(rounded) == 1 else ', '.join(rounded[:-1]) + ' and ' + rounded[-1]))
    text = by_concept('shares_outstanding_concept', lambda row, concept: row.get('shares_outstanding_as_of') != row['fiscal_year_end']
                      or 'annual filing' in concept)
    if text:
        notes.append('Shares outstanding: ' + text + '. A cover-page count is dated after the fiscal year end; '
                     'changes are shown only between counts on the same basis')
    latest = rows[-1]
    if latest.get('lease_commitments_not_commenced') is not None:
        notes.append(f'Leases signed but not yet commenced at {latest["fiscal_year_end"]}: '
                     f'{_money(latest["lease_commitments_not_commenced"])} billion of future lease payments, outside '
                     f'reported lease liabilities ({latest["lease_commitments_not_commenced_concept"]})'
                     f'{f" [{filing_id}]" if filing_id else ""}')
    return notes


# Inputs the annual filing supplies when company facts lack them. A year outside
# that filing's periods shows n/a for them although older filings report the figure.
SUPPLEMENT_FIELDS = (('acquisitions', 'acquisitions_concept', 'Acquisitions'),
                     ('average_repurchase_price', 'average_repurchase_price_concept', 'Average buyback price'),
                     ('depreciation_amortization', 'depreciation_amortization_concept', 'D&A'))
OUTSIDE_SCANNED = 'outside filings scanned'


def _mark_outside_supplement(table, supplement):
    """Record, per row, the supplement-filled fields that are blank only because the year is outside the filing."""
    if not supplement:
        return
    covered = {(fact['start'], fact['end']) for fact in supplement.get('facts') or [] if fact.get('start')}
    for field, concept, _ in SUPPLEMENT_FIELDS:
        if not any(row.get(concept) and 'annual filing' in row[concept] for row in table):
            continue
        for row in table:
            if row.get(field) is None and (row.get('fiscal_year_start'), row['fiscal_year_end']) not in covered:
                row.setdefault('outside_filings_scanned', []).append(field)


def depreciation_phrase(rows):
    """How the depreciation input of net reinvestment reads for these rows: the words to use in a note.

    Alphabet reports depreciation only (us-gaap:Depreciation), so its net
    reinvestment is capital expenditure less depreciation; Microsoft uses
    depreciation alone for two years and a depreciation-and-amortization total after.
    """
    used = [row for row in rows or [] if row.get('depreciation_amortization') is not None]
    only = [row['fiscal_year_end'] for row in used if row.get('depreciation_only')]
    if used and len(only) == len(used):
        return 'depreciation (the filings report no depreciation-and-amortization total)'
    if only:
        return ('depreciation and amortization (depreciation only for the fiscal years ending ' + ', '.join(only) + ')')
    return 'depreciation and amortization'


def repurchase_price_basis(rows):
    """(label, [(fiscal year end, price, cash price)]) for the average buyback price row.

    The label names the price's basis: the share note's repurchase value over its
    shares, the repurchase program's value over its shares, or the filing's stated
    price. The list gives each year where cash buybacks divided by the same shares
    differ from the price by more than 0.5% (Apple fiscal 2026: $85.8 billion over
    297 million shares in the share note against $86.4 billion of cash buybacks).
    """
    concepts = [row.get('average_repurchase_price_concept') or '' for row in rows or [] if row.get('average_repurchase_price')]
    if not concepts:
        return None, []
    kinds = {('stated price' if 'stated' in concept else
              'program value / program shares' if 'repurchase-program axis' in concept else
              'treasury-stock cost in the equity note / shares' if 'TreasuryStock' in concept else
              'repurchase value in the equity statement / shares')
             for concept in concepts}
    label = ' and '.join(sorted(kinds))
    differing = []
    for row in rows:
        price, cash, shares = row.get('average_repurchase_price'), row.get('repurchases'), row.get('shares_repurchased')
        if price and cash and shares:
            cash_price = cash / shares
            if abs(price / cash_price - 1) > .005:
                differing.append((row['fiscal_year_end'], price, cash_price))
    return label, differing


def _rate_instant(gaap, concepts, end, as_of=None):
    for concept in concepts:
        best = None
        for unit, row in _annual_rows(gaap, concept, as_of):
            if unit == 'pure' and 'start' not in row and row['end'] == end and (best is None or row['filed'] > best['filed']):
                best = row
        if best is not None:
            return best['val']
    return None


def _roic_inputs(gaap, period, as_of):
    """One fiscal year's inputs to roic.history from company facts."""
    start, end, currency = period['start'], period['end'], period['currency']
    row = {'fiscal_year_end': end, 'revenue': period['revenue']}
    # Cost of revenue and gross profit only tell whether R&D is reported inside SG&A.
    for name in ('operating_income', 'income_tax_expense', 'pretax_income', 'net_income', 'selling_general_administrative',
                 'cost_of_revenue', 'gross_profit'):
        row[name], _, _ = _duration(gaap, DURATIONS[name], start, end, currency, as_of)
    row['research_and_development'], _, _ = _research_and_development(gaap, start, end, currency, as_of)
    for name, options in roic.DURATIONS.items():
        row[name], _, _ = _duration(gaap, options, start, end, currency, as_of)
    if row['deferred_tax_expense'] is None:
        # Apple tags no deferred tax total, only its federal, state and foreign parts.
        for parts in DEFERRED_TAX_PARTS:
            values = [_duration(gaap, (concept,), start, end, currency, as_of)[0] for concept in parts]
            if all(value is not None for value in values):
                row['deferred_tax_expense'] = sum(values)
                row['deferred_tax_basis'] = 'sum of ' + ', '.join(parts)
                break
    if row['pretax_income'] is None and row['net_income'] is not None and row['income_tax_expense'] is not None:
        row['pretax_income'] = row['net_income'] + row['income_tax_expense']
    for name, options in roic.INSTANTS.items():
        row[name], _ = _instant(gaap, options, end, currency, as_of)
    for name, options in roic.RATES.items():
        row[name] = _rate_instant(gaap, options, end, as_of)
    return row


# A deferred tax total from its reported parts, where no total is tagged.
DEFERRED_TAX_PARTS = (('DeferredFederalIncomeTaxExpenseBenefit', 'DeferredStateAndLocalIncomeTaxExpenseBenefit',
                       'DeferredForeignIncomeTaxExpenseBenefit'),
                      ('DeferredFederalStateAndLocalTaxExpenseBenefit', 'DeferredForeignIncomeTaxExpenseBenefit'))
ROIC_FIELDS = ('nopat', 'nopat_margin', 'ebita', 'ebit_basis', 'operating_lease_interest', 'nopat_tax_rate', 'nopat_tax_basis',
               'invested_capital', 'average_invested_capital', 'capital_turnover', 'return_on_invested_capital',
               'return_on_capital_unbounded', 'invested_capital_excluding_acquired', 'return_excluding_acquired',
               'return_excluding_acquired_unbounded', 'intangible_investment', 'intangible_amortization',
               'capitalized_intangibles', 'adjusted_nopat', 'adjusted_invested_capital', 'adjusted_return',
               'adjusted_return_unbounded', 'intangibles_basis', 'sga_includes_rnd', 'incremental_return', 'incremental_return_status',
               'incremental_capital_change', 'investment_rate')
# The basis note flags a latest NOPAT tax rate this far from its three-year median
# (Microsoft, Alphabet, Amazon, Meta and Salesforce: 8-12% in the latest year
# against medians near 20%, 2026-11-26 critique).
TAX_RATE_NOTE_GAP = .05


def _attach_roic(table, gaap, as_of):
    """Add Mauboussin-method ROIC fields to each history row, from a longer run of fiscal years."""
    if not table:
        return
    periods = _fiscal_years(gaap, len(table) + ROIC_EXTRA_YEARS, as_of)
    inputs = [_roic_inputs(gaap, period, as_of) for period in periods]
    measures, status = roic.history(inputs)
    by_end = {item['fiscal_year_end']: item for item in measures}
    deferred = {item['fiscal_year_end']: item.get('deferred_tax_basis') for item in inputs}
    for row in table:
        item = by_end.get(row['fiscal_year_end']) or {}
        row.update({field: item.get(field) for field in ROIC_FIELDS})
        row['deferred_tax_basis'] = deferred.get(row['fiscal_year_end'])
        row['return_on_capital_unbounded'] = bool(row['return_on_capital_unbounded'])
        row['intangibles_capitalization'] = status


def render_baseline(baseline, source_id=None, include_roic=True, scale=1e9):
    rows = (baseline or {}).get('rows') or []
    if not rows:
        return ''
    ref = f' [{source_id}]' if source_id else ''
    currency = rows[-1].get('currency', '')
    reported = [row for row in rows if row.get('depreciation_amortization') is not None]
    legacy = (baseline.get('concepts') or {}).get('depreciation_amortization') == 'Depreciation'
    only = all(row.get('depreciation_only') for row in reported) if any('depreciation_only' in row for row in reported) else legacy
    depreciation = 'depreciation only/rev' if reported and only else 'D&A/rev'
    mixed = not only and any(row.get('depreciation_only') for row in reported)
    def outside(row, field, text):
        return OUTSIDE_SCANNED if text == 'n/a' and field in (row.get('outside_filings_scanned') or []) else text
    def depreciation_cell(row):
        text = _pct(row['depreciation_amortization_to_revenue'])
        # A depreciation-only year beside depreciation-and-amortization years says so in its cell.
        return text + (' (depreciation only)' if mixed and row.get('depreciation_only') and text != 'n/a' else '')
    lines = [f'Reported operating history{ref}, {currency} billions; ratios computed by Python from reported values; '
             'n/a means not reported on a comparable basis; "outside filings scanned" marks a figure older filings report.',
             'FY end | revenue | growth | gross margin | operating margin | operating income growth | R&D/rev | SG&A/rev | '
             f'effective tax | capex/rev | {depreciation} | SBC/rev | buybacks (cash) | avg buyback price | dividends | '
             'diluted EPS | EPS growth | shares outstanding at FY end (millions) | change in shares outstanding (millions) | '
             'shares repurchased (millions) | acquisitions (cash) | finance-lease additions']
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
            row['fiscal_year_end'], _money(row['revenue'], scale), _pct(row['revenue_growth']), _pct(row['gross_margin']),
            _pct(row['operating_margin']), _pct(row.get('operating_income_growth')),
            _pct(row['research_and_development_to_revenue']),
            _pct(row['selling_general_administrative_to_revenue']), _pct(row['effective_tax_rate']),
            _pct(row['capital_expenditure_to_revenue']), outside(row, 'depreciation_amortization', depreciation_cell(row)),
            _pct(row['stock_compensation_to_revenue']), _money(row['repurchases'], scale),
            outside(row, 'average_repurchase_price', price_text), _money(row['dividends'], scale),
            'n/a' if eps is None else f'{eps:,.2f}', _pct(row.get('diluted_eps_growth')),
            shares, _shares(row.get('net_share_change'), signed=True),
            _shares(row.get('shares_repurchased')), outside(row, 'acquisitions', _money(row.get('acquisitions'), scale)),
            _money(row.get('finance_lease_additions'), scale)]))
    notes = _basis_notes(rows, source_id, baseline.get('supplement_source'))
    if notes:
        lines.append('Basis notes:')
        lines.extend('- ' + note + '.' for note in notes)
    return '\n'.join(lines + (_roic_lines(rows, ref, scale) if include_roic else []))


def render_quarterly(baseline, source_id=None, markdown=False, notes_out=None, scale=1e9, letter='B'):
    """The quarterly rate-of-change table: one row per quarter, or with ``markdown`` one column per quarter."""
    quarterly = (baseline or {}).get('quarterly') or {}
    rows = quarterly.get('rows') or []
    if not rows:
        return ''
    ref = f' [{source_id}]' if source_id else ''
    concepts = quarterly.get('concepts') or {}
    def growth(row):
        if row.get('revenue_growth_basis') == 'unequal_length':
            return f'n/m ({row.get("days")} vs {row.get("prior_days")} days)'
        return _pct(row.get('revenue_growth'))
    # Only computed cells are marked; balances reported at the quarter end are not (Apple's fourth
    # quarter: revenue and operating income are the fiscal year less nine months, inventory is reported).
    def derived_revenue(row):
        return row.get('revenue_basis') not in (None, 'reported')
    def derived_operating(row):
        return derived_revenue(row) or row.get('operating_income_basis') not in (None, 'reported')
    def mark(text, flag):
        return text + (' (derived)' if flag and text != 'n/a' else '')
    measures = [('revenue', lambda row: mark(_money(row.get('revenue'), scale), derived_revenue(row))),
                ('growth on the year-earlier quarter', lambda row: mark(growth(row), derived_revenue(row))),
                ('operating margin', lambda row: mark(_pct(row.get('operating_margin')), derived_operating(row)))]
    if 'inventory' in concepts:
        measures.append(('inventory at quarter end', lambda row: _money(row.get('inventory'), scale)))
    if 'deferred_revenue' in concepts:
        measures.append(('deferred revenue at quarter end', lambda row: _money(row.get('deferred_revenue'), scale)))
    def label(row):
        return f'Q{row["fiscal_quarter"]}'
    caption = (f'Reported fiscal quarters{ref}, {quarterly.get("currency", "")} billions; ratios computed by Python from '
               'reported values; n/a means not reported.')
    notes = []
    # A 53-week year only when a fiscal year itself runs past 366 days; otherwise the quarter-end dates moved.
    ends = [_day(row['fiscal_year_end']) for row in (baseline or {}).get('rows') or [] if row.get('fiscal_year_end')]
    long_year = any((later - earlier).days > 366 for earlier, later in zip(ends, ends[1:]))
    length_reason = 'as in a 53-week year' if long_year else 'because the quarter-end dates moved'
    derived = [row for row in rows if derived_operating(row)]
    if derived:
        bases = {row["revenue_basis"] if row["revenue_basis"] != "reported" else row["operating_income_basis"] for row in derived}
        notes.append('Cells marked derived are computed from the filings; balances at the quarter end are reported. '
                     + ('Each Q4 is the fiscal year less the first nine months' if bases == {'fiscal year less nine months'}
                        else '; '.join(f'{row["period_end"]} is the {row["revenue_basis"] if row["revenue_basis"] != "reported" else row["operating_income_basis"]}'
                                       for row in derived))
                     + '. The company facts do not tag these quarters on their own; an earnings release may still present them')
    if any(row.get('revenue_growth_basis') == 'unequal_length' for row in rows):
        notes.append('Growth marked n/m (a vs b days) compares quarters of different length, this quarter\'s days against '
                     f'the year-earlier quarter\'s, {length_reason}')
    notes.append('Growth compares figures from the same filing, or for a derived quarter the same two filings, so a '
                 'restatement between the years is not read as growth' +
                 ('; n/a where no filing reports both years' if any(row.get('revenue_growth_basis') == 'no_comparative'
                                                                     for row in rows) else ''))
    if concepts:
        notes.append('Balance-sheet concepts: ' + ', '.join(f'{name.replace("_", " ")} us-gaap:{concept}'
                                                             for name, concept in concepts.items()))
    ttm = quarterly.get('trailing_twelve_months')
    if ttm:
        days = ttm.get('days')
        span = (f' ({days} days, ' + ('a 53-week year' if long_year else 'because the quarter-end dates moved') + ')'
                if isinstance(days, int) and days > 367 else '')
        amount = (_money if notes_out is None else
                  (lambda value: memo_style.Style(quarterly.get('currency', '')).amount(value) if value is not None else 'n/a'))
        notes.append(f'Trailing twelve months to {ttm["period"]["end_date"]}{span}: revenue {amount(ttm["revenue"])}, operating '
                     f'income {amount(ttm["operating_income"])}, operating margin {_pct(ttm["operating_margin"])}')
    if notes_out is not None:
        # The memo's layout: units in the row labels, notes collected for the method notes.
        unit = memo_style.Style(quarterly.get('currency', '')).unit(letter)
        names = {'revenue': f'Revenue ({unit})', 'inventory at quarter end': f'Inventory, quarter end ({unit})',
                 'deferred revenue at quarter end': f'Deferred revenue, quarter end ({unit})'}
        notes_out.extend(note + '.' for note in notes)
        lines = [f'Fiscal quarters ending on the dates shown{ref}. Ratios are computed from reported values; n/a means '
                 'not reported.', '', '| Measure | ' + ' | '.join(f'{row["period_end"]} {label(row)}' for row in rows) + ' |',
                 '|---|' + '---:|' * len(rows)]
        for name, cell in measures:
            text = names.get(name, name[0].upper() + name[1:])
            lines.append(f'| {text} | ' + ' | '.join(cell(row) for row in rows) + ' |')
        return '\n'.join(lines)
    if markdown:
        lines = ['', caption, '', '| Measure | ' + ' | '.join(f'{row["period_end"]} {label(row)}' for row in rows) + ' |',
                 '|---|' + '---:|' * len(rows)]
        for name, cell in measures:
            lines.append(f'| {name[0].upper() + name[1:]} | ' + ' | '.join(cell(row) for row in rows) + ' |')
        return '\n'.join(lines + ([''] + ['- ' + note + '.' for note in notes] if notes else []))
    lines = [caption, 'quarter end | fiscal quarter | ' + ' | '.join(name for name, _ in measures)]
    lines += [' | '.join([row['period_end'], label(row)] + [cell(row) for _, cell in measures]) for row in rows]
    if notes:
        lines.append('Quarterly notes:')
        lines.extend('- ' + note + '.' for note in notes)
    return '\n'.join(lines)


def quarterly_markdown(baseline, source_id=None):
    """The memo's quarterly table in its own scale: (lines, notes); notes go to the memo's method notes."""
    notes = []
    rows = ((baseline or {}).get('quarterly') or {}).get('rows') or []
    letter, divisor = scale_of(row.get('revenue') for row in rows)
    text = render_quarterly(baseline, source_id, notes_out=notes, scale=divisor, letter=letter)
    return (text.split('\n') if text else []), notes


def render_roic(baseline, source_id=None, scale=1e9):
    """The return-on-capital table alone: caption, header, one row per year, then 'ROIC basis:' notes."""
    rows = (baseline or {}).get('rows') or []
    return '\n'.join(_roic_lines(rows, f' [{source_id}]' if source_id else '', scale)[1:])


def _return(row, key):
    if row.get(key + '_unbounded') or (key == 'return_on_invested_capital' and row.get('return_on_capital_unbounded')):
        return 'n/m (capital at or below zero)'
    return _pct(row.get(key))


TAX_BASES = {'cash': 'the cash tax rate (provision less deferred tax expense, over pretax income)',
             'effective': 'the effective tax rate, because deferred tax expense is not tagged or puts the cash rate outside 0-50%',
             'statutory': 'a 25% rate, because neither rate is between 0% and 50%'}


def _nopat_note(rows):
    """The NOPAT basis as it ran: which years used which tax rate, and whether lease interest was found."""
    years = {}
    for row in rows:
        if row.get('nopat') is not None and row.get('nopat_tax_basis') in TAX_BASES:
            years.setdefault(row['nopat_tax_basis'], []).append(row['fiscal_year_end'])
    if len(years) == 1:
        tax = f'NOPAT is EBITA after {TAX_BASES[next(iter(years))]} in every year shown.'
    else:
        tax = 'NOPAT is EBITA after ' + '; '.join(f'{TAX_BASES[basis]} for the fiscal years ending {", ".join(ends)}'
                                                for basis, ends in years.items()) + '.'
    if any(row.get('deferred_tax_basis') for row in rows):
        tax += ' Deferred tax expense is the sum of its federal, state and foreign parts where no total is tagged.'
    interest = [row for row in rows if row.get('nopat') is not None]
    if interest and all(not row.get('operating_lease_interest') for row in interest):
        lease = (' EBITA is operating income plus amortization of acquired intangibles. The interest in operating lease '
                 'cost is not added: no weighted-average operating lease discount rate is tagged, so it is taken as zero.')
    else:
        missing = [row['fiscal_year_end'] for row in interest if not row.get('operating_lease_interest')]
        lease = (' EBITA is operating income plus amortization of acquired intangibles plus the interest in operating '
                 'lease cost, the average operating lease liability times its weighted-average discount rate' +
                 (f'; no rate is tagged for the fiscal years ending {", ".join(missing)}, where it is taken as zero'
                  if missing else '') + '.')
    return tax + lease


def _roic_lines(rows, ref, scale=1e9):
    """Return on invested capital table, Mauboussin and Callahan method."""
    if not any(row.get('nopat') is not None or row.get('invested_capital') is not None for row in rows):
        return []
    adjusted = any(row.get('adjusted_return') is not None or row.get('adjusted_return_unbounded') for row in rows)
    header = ('FY end | NOPAT | NOPAT margin | invested capital (year end) | average invested capital | capital turnover | '
              'ROIC | ROIC excluding goodwill and acquired intangibles | ' +
              ('ROIC with intangibles capitalized | ' if adjusted else '') + '3-year incremental ROIC | NOPAT tax rate')
    lines = ['', f'Return on invested capital{ref}, Mauboussin and Callahan method (Counterpoint Global, October 2022); '
             'computed by Python from reported values, billions of the reporting currency.', header]
    tags = {'split': ' (split)', 'one_line': ' (one line)'}
    for row in rows:
        turnover = row.get('capital_turnover')
        incremental = row.get('incremental_return')
        status = row.get('incremental_return_status')
        cells = [row['fiscal_year_end'], _money(row.get('nopat'), scale), _pct(row.get('nopat_margin')),
                 _money(row.get('invested_capital'), scale), _money(row.get('average_invested_capital'), scale),
                 'n/a' if turnover is None else f'{turnover:.2f}x', _return(row, 'return_on_invested_capital'),
                 _return(row, 'return_excluding_acquired')]
        if adjusted:
            value = _return(row, 'adjusted_return')
            cells.append(value + (tags.get(row.get('intangibles_basis'), '') if value != 'n/a' else ''))
        cells += ['n/m (capital change under a tenth of capital)' if status == 'small_capital_change' else
                  'n/m (capital fell or unchanged)' if incremental is None and row.get('incremental_capital_change') is not None
                  else _pct(incremental),
                  _pct(row.get('nopat_tax_rate')) + ('' if row.get('nopat_tax_basis') in (None, 'cash') else
                                                     f' ({row["nopat_tax_basis"]})')]
        if row.get('ebit_basis') not in (None, 'operating income'):
            cells[1] += '*'
        lines.append(' | '.join(cells))
    status = rows[-1].get('intangibles_capitalization')
    absent = {'no_split': 'the method applies separate shares to selling and marketing and to G&A, which the filings '
                          'do not report apart',
              'no_sga': 'no SG&A line is reported',
              'no_rnd': 'no R&D line is reported, and the method does not take a missing line as zero',
              'short_history': 'the reported history is shorter than the six-year R&D life'}
    bases = {row.get('intangibles_basis') for row in rows
             if row.get('adjusted_return') is not None or row.get('adjusted_return_unbounded')}
    capitalization = []
    if 'split' in bases:
        capitalization.append('Years marked (split), where selling and marketing and G&A are reported apart, use the '
                              'company method the paper applies to Microsoft: 100% of R&D is treated as investment over six '
                              'years, and 70% of selling and marketing and 20% of G&A over two years each.')
    if 'one_line' in bases:
        capitalization.append('Years marked (one line), where SG&A is one line, use the shares of Peters and Taylor that '
                              'Mauboussin and Callahan state ("ROIC and Intangible Assets", November 2022): 100% of R&D over '
                              'six years and 30% of SG&A, taken as non-R&D SG&A, over two years.' +
                              (' R&D is reported inside SG&A (revenue less cost of revenue less SG&A equals operating '
                               'income), so the 30% applies to SG&A less R&D.'
                               if any(row.get('sga_includes_rnd') for row in rows) else ''))
    marked = any(row.get('ebit_basis') not in (None, 'operating income') for row in rows)
    rates = [row.get('nopat_tax_rate') for row in rows[-3:]]
    tax = []
    if len(rates) == 3 and all(isinstance(rate, (int, float)) for rate in rates):
        median = sorted(rates)[1]
        latest = rows[-1]
        if abs(rates[-1] - median) > TAX_RATE_NOTE_GAP:
            roic_at_median = (latest['return_on_invested_capital'] * (1 - median) / (1 - rates[-1])
                              if isinstance(latest.get('return_on_invested_capital'), (int, float)) and rates[-1] < 1 else None)
            tax.append(f'- The latest NOPAT tax rate, {_pct(rates[-1])} for the year ending {latest["fiscal_year_end"]}, '
                       f'differs from its three-year median of {_pct(median)} by more than {TAX_RATE_NOTE_GAP * 100:.0f} '
                       'points' + (f'; at the median rate that year\'s ROIC would be {_pct(roic_at_median)}'
                                   if roic_at_median is not None else '') + '.')
    lines += ['ROIC basis:',
              *(['- * No operating income is reported: EBIT is pretax income plus net interest expense, so other '
                 'non-operating items stay in it.'] if marked else []),
              '- ' + _nopat_note(rows),
              '- Invested capital is total assets less current liabilities other than debt, less cash and current '
              'securities above an operating balance of 2% of revenue, less noncurrent securities and equity investments. '
              'ROIC divides NOPAT by the average of opening and closing invested capital.',
              '- Incremental ROIC is the NOPAT change over three years divided by the change in invested capital over the '
              'three years ending a year earlier. It is noisy, and the method warns against comparing it directly with the '
              'cost of capital. It is n/m when capital fell or rose by less than a tenth of the average of the two capital '
              'figures.',
              *tax,
              ('- Intangibles capitalized. ' + ' '.join(capitalization) + ' Amortization is straight line from the year '
               'after the spending; NOPAT adds the year\'s investment less its amortization and invested capital adds the '
               'unamortized balance. The lives are the company method\'s; the November 2022 paper sets them by industry.'
               if adjusted and capitalization else
               '- ROIC with intangibles capitalized is not shown: ' + absent.get(status, 'the inputs are incomplete') + '.'),
              '- No adjustment is made for restructuring charges, impairments or one-time tax items, which the paper '
              'removes. Years that contain them are shown as reported' +
              (', and a tax rate outside 0-50% falls back as noted.' if any(row.get('nopat_tax_basis') == 'statutory'
                                                                            for row in rows) else '.'),
              '- Mauboussin and Callahan report a median 2021 ROIC of 8.8% for Russell 3000 companies outside financials '
              'and real estate, and 9.0% with intangibles capitalized ("ROIC and Intangible Assets", November 2022).']
    return lines


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


def _reader_label(label):
    """'IPhone' is iPhone in the reader's table; other labels are unchanged."""
    return re.sub(r'^I(?=[A-Z][a-z])', 'i', label)


SEGMENT_SUM_GAP = .01


def segment_operating_note(table, consolidated=None, currency='USD'):
    """A note when segment operating income does not add up to consolidated operating income, or None.

    Apple's segments earned $212.5 billion in fiscal 2026 against $158.1 billion
    consolidated: its segment measure leaves out R&D and corporate costs. Without
    the consolidated figure the note gives the segment sum alone.
    ``consolidated`` maps fiscal year ends to consolidated operating income.
    """
    ends = (table or {}).get('fiscal_year_ends') or []
    members = [entry for entry in (table or {}).get('members') or [] if ' within ' not in (entry.get('member') or '')]
    if not ends or not members:
        return None
    values = [(entry.get('operating_income') or [None])[0] for entry in members]
    if any(value is None for value in values):
        return None
    total = sum(values)
    style = memo_style.Style(currency)
    whole = (consolidated or {}).get(ends[0])
    if isinstance(whole, (int, float)) and whole:
        if abs(total - whole) <= SEGMENT_SUM_GAP * abs(whole):
            return None
        return (f'Segment operating income sums to {style.amount(total)} for the fiscal year ending {ends[0]}, against '
                f'consolidated operating income of {style.amount(whole)}: the segment measure leaves out costs the '
                'company does not allocate to segments, so segment margins are not comparable with the consolidated margin.')
    return (f'Segment operating income sums to {style.amount(total)} for the fiscal year ending {ends[0]}. The segment '
            'measure can leave out costs the company does not allocate to segments; compare it with consolidated '
            'operating income in the annual results.')


def breakdown_markdown(tables, source_id=None, currency='USD', consolidated_operating_income=None):
    """The memo's breakdown tables, one revenue table per axis and a margin table where margins exist: (lines, notes).

    ``consolidated_operating_income`` (fiscal year end -> amount) lets the segment note
    set the segment sum beside the consolidated figure.
    """
    if not tables:
        return [], []
    ref = f' [{source_id}]' if source_id else ''
    titles = {'product_or_service': 'Revenue by product or service', 'segment': 'Revenue by reportable segment',
              'geography': 'Revenue by geography'}
    lines, notes = [], ['Breakdown members can overlap and are not summed.']
    for axis, table in tables.items():
        ends = table['fiscal_year_ends']
        years = [end[:4] for end in ends]
        currency = table.get('currency') or currency or 'USD'
        members = table['members']
        prefixes = _issuer_prefixes({entry.get('member') for entry in members if isinstance(entry.get('member'), str)})
        # Labels are recomputed from the member, so a saved table gets the current names.
        name = lambda entry: (_member_label(entry['member'], prefixes) if isinstance(entry.get('member'), str)
                              else entry['label'])
        letter, scale = scale_of(value for entry in members for value in entry['revenue'] + (entry.get('operating_income') or []))
        unit = memo_style.Style(currency).unit(letter)
        lines += ['', f'### {titles.get(axis, axis)}', '', f'Fiscal years ending {", ".join(ends)}{ref}.', '']
        lines += ['| Member | ' + ' | '.join(f'{year} ({unit})' for year in years) + ' | ' +
                  ' | '.join(f'Growth {year}' for year in years[:-1]) + ' |',
                  '|---|' + '---:|' * (2 * len(years) - 1)]
        for entry in members:
            growth = (entry['revenue_growth'][:-1] or [None]) if len(years) > 1 else []
            growth = (growth + [None] * (len(years) - 1))[:len(years) - 1]
            lines.append(f'| {_reader_label(name(entry))} | ' + ' | '.join(_money(v, scale) for v in entry['revenue']) + ' | ' +
                         ' | '.join(_pct(v) for v in growth) + ' |')
        margins = [entry for entry in members if 'gross_margin' in entry or 'operating_margin' in entry]
        if margins:
            gross = any('gross_margin' in entry for entry in margins)
            operating = any('operating_margin' in entry for entry in margins)
            header = (['Member'] + ([f'Gross margin {year}' for year in years] if gross else []) +
                      ([f'Operating margin {year}' for year in years] if operating else []))
            lines += ['', '| ' + ' | '.join(header) + ' |', '|---|' + '---:|' * (len(header) - 1)]
            for entry in margins:
                cells = ([_pct(v) for v in entry.get('gross_margin') or [None] * len(years)] if gross else []) + \
                        ([_pct(v) for v in entry.get('operating_margin') or [None] * len(years)] if operating else [])
                lines.append(f'| {_reader_label(name(entry))} | ' + ' | '.join(cells) + ' |')
            if operating:
                lines += ['', '| Member | ' + ' | '.join(f'Operating income {year} ({unit})' for year in years) + ' |',
                          '|---|' + '---:|' * len(years)]
                for entry in margins:
                    if 'operating_income' in entry:
                        lines.append(f'| {_reader_label(name(entry))} | ' +
                                     ' | '.join(_money(v, scale) for v in entry['operating_income']) + ' |')
        if axis == 'segment':
            note = segment_operating_note(table, consolidated_operating_income, currency)
            if note:
                notes.append(note)
    return lines, notes

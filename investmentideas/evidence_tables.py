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
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
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
    'acquisitions': ('PaymentsToAcquireBusinessesNetOfCashAcquired',),
    'depreciation_amortization': ('DepreciationDepletionAndAmortization', 'DepreciationAndAmortization',
                                  'DepreciationAmortizationAndAccretionNet', 'Depreciation'),
    'stock_compensation': ('ShareBasedCompensation', 'AllocatedShareBasedCompensationExpense'),
    'repurchases': ('PaymentsForRepurchaseOfCommonStock',),
    'dividends': ('PaymentsOfDividends', 'PaymentsOfDividendsCommonStock'),
    'shares_repurchased': ('StockRepurchasedDuringPeriodShares', 'StockRepurchasedAndRetiredDuringPeriodShares'),
    'diluted_shares': ('WeightedAverageNumberOfDilutedSharesOutstanding',),
    'diluted_eps': ('EarningsPerShareDiluted',),
}
INSTANTS = {
    'inventory': ('InventoryNet',),
    'equity': ('StockholdersEquity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'),
}
SHARE_UNITS = {'shares'}


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
    """Latest ``count`` annual revenue periods, preferring the narrower revenue concepts in order."""
    by_end = {}
    for rank, concept in enumerate(REVENUE):
        for unit, row in _annual_rows(gaap, concept, as_of):
            if len(unit) != 3 or 'start' not in row or not _day(row.get('start')):
                continue
            if not 350 <= (_day(row['end']) - _day(row['start'])).days <= 380:
                continue
            candidate = (-rank, row['filed'], row['start'], row['val'], concept, unit)
            current = by_end.get(row['end'])
            if current is None or candidate[:2] > current[:2]:
                by_end[row['end']] = candidate
    ends = sorted(by_end)[-count:]
    return [{'start': by_end[end][2], 'end': end, 'revenue': by_end[end][3], 'revenue_concept': by_end[end][4],
             'currency': by_end[end][5]} for end in ends]


def _duration(gaap, concepts, start, end, currency, as_of=None):
    for concept in concepts:
        best = None
        for unit, row in _annual_rows(gaap, concept, as_of):
            if unit not in ({currency} | SHARE_UNITS | {currency + '/shares'}):
                continue
            if row.get('start') == start and row['end'] == end and (best is None or row['filed'] > best['filed']):
                best = row
        if best is not None:
            return best['val'], concept
    return None, None


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


def _ratio(numerator, denominator):
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def historical_baseline(payload, years=5, as_of=None):
    """Reported operating history from a full SEC companyfacts payload.

    Returns rows oldest first. A ratio is None when either side is unreported.
    Average repurchase price uses reported shares repurchased only; it is never
    inferred from changes in share count.
    """
    gaap = ((payload or {}).get('facts') or {}).get('us-gaap') or {}
    periods = _fiscal_years(gaap, years + 1, as_of)
    rows, concepts = [], {}
    for period in periods:
        row = {'fiscal_year_start': period['start'], 'fiscal_year_end': period['end'], 'currency': period['currency'],
               'revenue': period['revenue']}
        concepts['revenue'] = period['revenue_concept']
        for name, options in DURATIONS.items():
            row[name], used = _duration(gaap, options, period['start'], period['end'], period['currency'], as_of)
            if used:
                concepts.setdefault(name, used)
        for name, options in INSTANTS.items():
            row[name], used = _instant(gaap, options, period['end'], period['currency'], as_of)
            if used:
                concepts.setdefault(name, used)
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
                                          'stock_compensation', 'repurchases', 'shares_repurchased', 'dividends',
                                          'diluted_shares', 'diluted_eps', 'inventory', 'research_and_development',
                                          'selling_general_administrative', 'gross_profit')}
        item.update(
            revenue_growth=_ratio(revenue - prior['revenue'], prior['revenue']) if prior else None,
            gross_margin=_ratio(row['gross_profit'], revenue), operating_margin=_ratio(row['operating_income'], revenue),
            research_and_development_to_revenue=_ratio(row['research_and_development'], revenue),
            selling_general_administrative_to_revenue=_ratio(row['selling_general_administrative'], revenue),
            effective_tax_rate=_ratio(row['income_tax_expense'], pretax) if (pretax or 0) > 0 else None,
            cash_tax_rate=_ratio(row['income_taxes_paid'], pretax) if (pretax or 0) > 0 else None,
            capital_expenditure_to_revenue=_ratio(row['capital_expenditure'], revenue),
            depreciation_amortization_to_revenue=_ratio(row['depreciation_amortization'], revenue),
            stock_compensation_to_revenue=_ratio(row['stock_compensation'], revenue),
            average_repurchase_price=(_ratio(row['repurchases'], row['shares_repurchased'])
                                      if row['shares_repurchased'] else None),
            gross_profit_derived=bool(row.get('gross_profit_derived')))
        table.append(item)
    # The first period exists only to compute the first year's growth.
    table = table[1:] if len(table) > years else table
    return {'rows': table, 'concepts': concepts, 'source': 'SEC companyfacts annual filings (10-K/20-F/40-F)',
            'basis': 'Reported annual values; ratios computed by Python. Missing concepts are blank, never zero.'}


def _member_label(member):
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
                found = [Decimal(fact['normalized_decimal']) for fact in rows if fact['member'] == member and
                         fact['end_date'] == end and short(fact['concept']) in concepts]
                return found[0] if len(set(found)) == 1 else None
            sales = [value(revenue, end) for end in ends]
            if not any(item is not None for item in sales):
                continue
            costs = [value(cost, end) for end in ends]
            operating = [value({'OperatingIncomeLoss'}, end) for end in ends]
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


def render_baseline(baseline, source_id=None):
    rows = (baseline or {}).get('rows') or []
    if not rows:
        return ''
    ref = f' [{source_id}]' if source_id else ''
    currency = rows[-1].get('currency', '')
    lines = [f'Reported operating history{ref}, {currency} billions; ratios computed by Python from reported values.',
             'FY end | revenue | growth | gross margin | operating margin | R&D/rev | SG&A/rev | effective tax | '
             'capex/rev | D&A/rev | SBC/rev | buybacks | avg buyback price | dividends | diluted EPS']
    for row in rows:
        price = row.get('average_repurchase_price')
        eps = row.get('diluted_eps')
        lines.append(' | '.join([
            row['fiscal_year_end'], _money(row['revenue']), _pct(row['revenue_growth']), _pct(row['gross_margin']),
            _pct(row['operating_margin']), _pct(row['research_and_development_to_revenue']),
            _pct(row['selling_general_administrative_to_revenue']), _pct(row['effective_tax_rate']),
            _pct(row['capital_expenditure_to_revenue']), _pct(row['depreciation_amortization_to_revenue']),
            _pct(row['stock_compensation_to_revenue']), _money(row['repurchases']),
            'n/a' if price is None else f'{price:,.2f}', _money(row['dividends']),
            'n/a' if eps is None else f'{eps:,.2f}']))
    return '\n'.join(lines)


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

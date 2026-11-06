"""Pin reported inputs to dated primary inline or SEC entity-wide API facts.

This module performs accounting arithmetic and applies explicitly disclosed house
classification policies. It never supplies missing reported quantities as zero,
changes forecasts, or asks a language model to reconcile an input bridge.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import math
import re

REVENUE = {'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax',
           'us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax',
           'us-gaap:Revenues', 'us-gaap:SalesRevenueNet'}
SHARES = {'dei:EntityCommonStockSharesOutstanding', 'us-gaap:CommonStockSharesOutstanding'}
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
}
DEBT = {
    'total_debt_and_finance_leases': 'DebtAndCapitalLeaseObligations',
    'financial_debt': 'DebtLongtermAndShorttermCombinedAmount',
    'current_debt_and_finance_leases': 'DebtCurrent',
    'short_term_borrowings': 'ShortTermBorrowings',
    'commercial_paper': 'CommercialPaper',
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
CASH_POLICY = ('Explicit house modeling policy: selected nonoperating assets comprise reported cash and '
               'cash equivalents plus directly reported marketable securities or short-term investments '
               'on the annual balance-sheet date. Inclusive subtotals replace their components. Missing '
               'investment categories are excluded from this selected-asset policy, not reported as zero. '
               'Long-term investments without a marketable classification are excluded. This does not '
               'establish that all cash is economically surplus or required operating cash is zero.')
CLAIMS_POLICY = ('Explicit house assumption: additional unidentified equity claims are zero. This is '
                 'not a reported fact or evidence that no additional claims exist; identified finance '
                 'lease liabilities are deducted once, either within a selected combined debt subtotal '
                 'or separately as other equity claims.')
LEASE_POLICY = ('Finance leases are deducted once: separately in other equity claims when financial debt excludes them, '
                'or within an explicitly selected combined debt-and-finance-lease subtotal. Operating lease liabilities '
                'are not added under the unadjusted operating-EBIT convention; capitalizing them would require a consistent EBIT and reinvestment restatement.')
PRIMARY_INPUT_CONCEPTS = REVENUE | SHARES | {
    'us-gaap:' + concept for group in (CASH, DEBT, FINANCE_LEASES) for concept in group.values()
}
STRUCTURED_FORMS = frozenset({'10-K', '10-K/A', '10-Q', '10-Q/A', '20-F', '20-F/A', '40-F', '40-F/A'})


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
                      if item.get('classification_evidence') else {})} for name, item in components.items()}


def _validate_scopes(items, scopes, label, conflicts):
    """Check every known disjoint subset and exact partition against each total.

    Scope bits encode accounting categories, not display tags. Incomplete
    subsets (commercial paper within short-term borrowing) provide bounds but
    never establish that their enclosing liability category is complete.
    """
    rows = [(key, item['value'], *scopes[key]) for key, item in items.items()
            if item and key in scopes]
    for key, total, enclosing, enclosing_complete in rows:
        if not enclosing_complete:
            continue
        states = {(0, True): (Decimal(0), Decimal(0))}
        for part_key, value, scope, complete in rows:
            if key == part_key or scope & enclosing != scope:
                continue
            for (covered, exact), (low, high) in list(states.items()):
                if covered & scope:
                    continue
                state = (covered | scope, exact and complete)
                new_low, new_high = low + value, high + value
                old = states.get(state)
                states[state] = (min(old[0], new_low), max(old[1], new_high)) if old else (new_low, new_high)
        if any(high > total or (covered == enclosing and exact and (low != total or high != total))
               for (covered, exact), (low, high) in states.items() if covered):
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
    if not cash:
        return None
    selected = {'cash_and_cash_equivalents': cash}
    if marketable and not short:
        selected.update(marketable)
    elif short:
        selected['short_term_investments'] = short
        if items['noncurrent_marketable_securities']:
            selected['noncurrent_marketable_securities'] = items['noncurrent_marketable_securities']
    else:
        selected.update({key: items[key] for key in ('current_marketable_securities',
                        'noncurrent_marketable_securities') if items[key]})
    return selected


def _debt_selection(items, leases, conflicts):
    """Compare complete disjoint alternatives at identical scopes before selection."""
    before = len(conflicts)
    # Scope atoms: short-term borrowing=1, current/noncurrent term debt=2/4,
    # current/noncurrent finance leases=8/16. Commercial paper provides a
    # lower bound within atom 1, not a complete short-term financing category.
    _validate_scopes({**items, **{'finance_lease_' + key: leases.get(key)
                                for key in ('total', 'current', 'noncurrent')}}, {
        'total_debt_and_finance_leases': (31, True), 'financial_debt': (7, True),
        'current_debt_and_finance_leases': (11, True), 'short_term_borrowings': (1, True),
        'commercial_paper': (1, False), 'term_debt': (6, True),
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
    if short and combined:
        candidates.append(({'short_term_borrowings': short, **combined}, True))
    if short and term:
        candidates.append(({'short_term_borrowings': short, **term}, False))
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
    if lease_total:
        return next((candidate for candidate in candidates if not candidate[1]), candidates[0])
    return next((candidate for candidate in candidates if candidate[1]), candidates[0])


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


def _unique(items, label, conflicts):
    if not items:
        return None
    values = {item['value'] for item in items}
    if len(values) != 1:
        conflicts.append(f'Conflicting primary values for {label}; no value was selected')
        return None
    selected = dict(items[0])
    selected['source_refs'] = sorted({item['source_id'] for item in items})
    selected['facts'] = [_public_fact(item) for item in items]
    return selected


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
    conflicts, skipped, facts, source_ids = [], [], [], set()
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
        collections = []
        if isinstance(data.get('inline_facts'), list) and not (source_filed and source_filed > cutoff):
            collections.append((data['inline_facts'], 'inline_xbrl'))
        if isinstance(data.get('reported_facts'), list):
            url = source.get('url')
            match = re.fullmatch(r'https://data\.sec\.gov/api/xbrl/companyfacts/CIK([0-9]{10})\.json', url) if isinstance(url, str) else None
            if data.get('fact_origin') == 'sec_companyfacts' and expected_entity and match and _cik(match.group(1)) == expected_entity:
                collections.append((data['reported_facts'], 'sec_companyfacts'))
            else:
                skipped.append('Structured primary facts lack a recognized SEC companyfacts origin and issuer endpoint')
        for raw, origin in ((raw, origin) for rows, origin in collections for raw in rows):
            if not isinstance(raw, dict) or raw.get('is_nil') or raw.get('dimensions') != {}:
                continue
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
            if origin == 'sec_companyfacts':
                item.update({key: raw.get(key) for key in ('filing_date', 'form', 'accession_number', 'original_value',
                                                          'original_unit', 'frame', 'source_url', 'source_record')})
            item.update(source_id=sid, entity_identifier=entity, value=value, parsed_instant=instant,
                        parsed_start=start, parsed_end=end)
            facts.append(item)
            source_ids.add(sid)
    entities = {item['entity_identifier'] for item in facts}
    actuals, refs, dates, bridge, claims_bridge = {}, {}, {}, {}, {}
    fields = {key: 'unavailable' for key in ('revenue', 'net_debt', 'other_equity_claims', 'shares_outstanding')}
    if len(entities) > 1:
        conflicts.append('Primary reported facts identify multiple issuers; no reported actuals were pinned')
        facts = []
    annual = [item for item in facts if item['concept'] in REVENUE and
              (item.get('fact_origin') != 'sec_companyfacts' or item.get('form') not in {'10-Q', '10-Q/A'}) and item['parsed_start'] and item['parsed_end'] and
              320 <= (item['parsed_end'] - item['parsed_start']).days <= 400 and item['value'] > 0 and
              isinstance(item['unit'], str) and re.fullmatch(r'iso4217:[A-Z]{3}', item['unit'])]
    anchor = None
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
                revenue = _unique(compatible, 'latest annual revenue', conflicts)
                if revenue:
                    anchor = latest
                    actuals['revenue'] = _json_number(revenue['value'])
                    refs['revenue'] = revenue['source_refs']
                    dates['revenue'] = {'start_date': revenue['start_date'], 'end_date': revenue['end_date'], 'basis': 'primary annual duration'}
                    fields['revenue'] = 'pinned'
    if anchor:
        def pick(concept, name):
            return _unique([item for item in facts if item['concept'] == concept and item['parsed_instant'] == anchor and
                            item['unit'] == 'iso4217:' + currency and item['value'] >= 0], name, conflicts)
        cash_conflicts_before = len(conflicts)
        cash_items = {name: pick('us-gaap:' + concept, name) for name, concept in CASH.items()}
        cash = _cash_selection(cash_items, conflicts)
        if len(conflicts) != cash_conflicts_before:
            cash = None
        lease_conflicts_before = len(conflicts)
        lease = {name: pick('us-gaap:' + concept, 'finance lease ' + name)
                 for name, concept in FINANCE_LEASES.items()}
        lease_parts = _subtotal(lease, 'total', 'current', 'noncurrent', 'Finance lease', conflicts)
        if len(conflicts) != lease_conflicts_before:
            lease_parts = None
        lease['reconciled'] = lease_parts
        debt_conflicts_before = len(conflicts)
        debt_items = {name: pick('us-gaap:' + concept, name) for name, concept in DEBT.items()}
        if not debt_items['short_term_borrowings']:
            debt_items['short_term_borrowings'] = _classified_debt(
                sources, debt_items, anchor, 'short_term_financing_total')
        classified_total = _classified_debt(sources, debt_items, anchor, 'financial_debt_total')
        if classified_total:
            if debt_items['financial_debt'] and debt_items['financial_debt']['value'] != classified_total['value']:
                conflicts.append('Primary classified total debt conflicts with the reported total financial debt')
            else:
                debt_items['financial_debt'] = classified_total
        debt_choice = _debt_selection(debt_items, lease, conflicts)
        if len(conflicts) != debt_conflicts_before:
            debt_choice = None
        debt, leases_in_debt = debt_choice if debt_choice else ({}, False)
        if cash and debt:
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
                                        'excluded_unclassified_investments': 'LongTermInvestments and other investments without a selected marketable classification',
                                        'unselected_reported_categories': [name for name in CASH if cash_items[name] and name not in cash],
                                        'missing_investment_categories': [name for name in CASH if not cash_items[name]]},
                      'explanation': 'Exact sum of disjoint reported debt components minus selected cash and investments. Totals replace their components; no rounding residual or second cash add-back is permitted.',
                      'lease_policy': LEASE_POLICY}
            fields['net_debt'] = 'pinned'
        else:
            if not cash:
                skipped.append('Net-debt cash selection unavailable or conflicting on the annual balance-sheet date')
            if not debt:
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
    outstanding = [item for item in facts if item['concept'] in SHARES and item['unit'] == 'xbrli:shares' and
                   item['parsed_instant'] and item['value'] > 0]
    if outstanding:
        latest = max(item['parsed_instant'] for item in outstanding)
        chosen = _unique([item for item in outstanding if item['parsed_instant'] == latest], 'latest reported outstanding shares', conflicts)
        if chosen:
            actuals['shares_outstanding'] = _json_number(chosen['value'])
            refs['shares_outstanding'] = chosen['source_refs']
            dates['shares_outstanding'] = {'instant': latest.isoformat(), 'basis': 'latest reported ordinary shares outstanding; never annual average or issued shares'}
            fields['shares_outstanding'] = 'pinned'
    if currency is None:
        market_currencies = {source['data'].get('currency') for source in sources
                             if isinstance(source, dict) and source.get('kind') == 'market_snapshot' and
                             isinstance(source.get('data'), dict) and
                             isinstance(source['data'].get('currency'), str) and
                             re.fullmatch(r'[A-Z]{3}', source['data']['currency'])}
        if len(market_currencies) == 1:
            currency = next(iter(market_currencies))
    quote = _quote(sources, currency, cutoff) if currency else None
    output = {'actuals': actuals, 'currency': currency, 'source_refs': refs, 'input_dates': dates,
              'net_debt_bridge': bridge, 'other_claims_bridge': claims_bridge,
              'coverage': {'status': ('conflicted' if conflicts else 'complete' if len(actuals) == 4 else 'partial' if actuals else 'unavailable'),
                           'complete': len(actuals) == 4 and not conflicts, 'fields': fields,
                           'recognized_primary_source_ids': sorted(source_ids),
                           'missing_fields': [field for field in fields if field not in actuals],
                           'conflicts': conflicts, 'notes': sorted(set(skipped))},
              'notes': [CASH_POLICY, CLAIMS_POLICY, LEASE_POLICY,
                        'Reported shares can postdate the balance sheet and predate the quote; the dates remain distinct.',
                        'Missing primary facts are not zero; unsupported or ambiguous inputs remain unpinned.']}
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
    for name in ('net_debt_bridge', 'other_claims_bridge'):
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

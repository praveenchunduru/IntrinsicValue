"""Pin reported valuation inputs to nondimensional, dated primary inline facts.

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
CASH = {'cash_and_cash_equivalents': 'us-gaap:CashAndCashEquivalentsAtCarryingValue',
        'current_marketable_securities': 'us-gaap:MarketableSecuritiesCurrent',
        'noncurrent_marketable_securities': 'us-gaap:MarketableSecuritiesNoncurrent'}
DEBT = {'commercial_paper': 'us-gaap:CommercialPaper',
        'current_term_debt': 'us-gaap:LongTermDebtCurrent',
        'noncurrent_term_debt': 'us-gaap:LongTermDebtNoncurrent'}
FINANCE_LEASES = {'total': 'us-gaap:FinanceLeaseLiability',
                 'current': 'us-gaap:FinanceLeaseLiabilityCurrent',
                 'noncurrent': 'us-gaap:FinanceLeaseLiabilityNoncurrent'}
CASH_POLICY = ('Explicit house modeling policy: all reported cash and cash equivalents plus current and '
               'noncurrent marketable securities are classified as nonoperating assets. This does not '
               'establish that all cash is economically surplus or that required operating cash is zero.')
CLAIMS_POLICY = ('Explicit house assumption: additional unidentified equity claims are zero. This is '
                 'not a reported fact or evidence that no additional claims exist; identified finance '
                 'lease liabilities are deducted once as other equity claims, outside financial net debt.')
LEASE_POLICY = ('Finance leases are excluded from financial debt and deducted once in other equity claims. '
                'Operating lease liabilities are not added under the unadjusted operating-EBIT convention; '
                'capitalizing them would require a consistent EBIT and reinvestment restatement.')


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
            'start_date', 'end_date', 'unit', 'entity_identifier')} | {'value': _json_number(item['value'])}


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

    A bridge requires every named cash/debt component in the same currency and on
    the latest annual revenue's exact balance-sheet date. A zero must be present
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
        if not isinstance(data, dict) or not isinstance(data.get('inline_facts'), list):
            continue
        sid = source.get('id')
        if not isinstance(sid, str) or not sid:
            continue
        metadata = data.get('metadata') if isinstance(data.get('metadata'), dict) else {}
        filed = _day(metadata.get('filingDate'))
        if filed and filed > cutoff:
            continue
        expected_entity = _cik(metadata.get('cik'))
        for raw in data['inline_facts']:
            if (not isinstance(raw, dict) or raw.get('parse_status') != 'parsed' or raw.get('is_nil') or
                    raw.get('dimensions') != {}):
                continue
            entity = _cik(raw.get('entity_identifier'))
            if not entity or (expected_entity and entity != expected_entity):
                skipped.append('Primary fact has a missing or mismatched issuer identifier')
                continue
            value = _number(raw.get('normalized_decimal', raw.get('normalized_value')))
            normalized = _number(raw.get('normalized_value'))
            if value is None or normalized is None or value != normalized:
                continue
            instant, start, end = (_day(raw.get(key)) for key in ('instant', 'start_date', 'end_date'))
            if instant:
                if instant > cutoff or (filed and instant > filed):
                    continue
            elif not start or not end or end > cutoff or start > end:
                continue
            item = {key: raw.get(key) for key in ('concept', 'context_ref', 'instant', 'start_date', 'end_date', 'unit')}
            item.update(source_id=sid, entity_identifier=entity, value=value, parsed_instant=instant,
                        parsed_start=start, parsed_end=end)
            facts.append(item)
            source_ids.add(sid)
    entities = {item['entity_identifier'] for item in facts}
    actuals, refs, dates, bridge, claims_bridge = {}, {}, {}, {}, {}
    fields = {key: 'unavailable' for key in ('revenue', 'net_debt', 'other_equity_claims', 'shares_outstanding')}
    if len(entities) > 1:
        conflicts.append('Primary inline facts identify multiple issuers; no reported actuals were pinned')
        facts = []
    annual = [item for item in facts if item['concept'] in REVENUE and item['parsed_start'] and item['parsed_end'] and
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
        cash = {name: pick(concept, name) for name, concept in CASH.items()}
        debt = {name: pick(concept, name) for name, concept in DEBT.items()}
        if all(cash.values()) and all(debt.values()):
            cash_sum = sum(item['value'] for item in cash.values())
            debt_sum = sum(item['value'] for item in debt.values())
            actuals['net_debt'] = _json_number(debt_sum - cash_sum)
            refs['net_debt'] = sorted({sid for item in [*cash.values(), *debt.values()] for sid in item['source_refs']})
            dates['net_debt'] = {'instant': anchor.isoformat(), 'basis': 'same-period primary financial debt less selected nonoperating assets'}
            bridge = {'currency': currency, 'as_of': anchor.isoformat(), 'debt': _json_number(debt_sum),
                      'selected_cash_and_nonoperating_investments': _json_number(cash_sum),
                      'net_debt': actuals['net_debt'],
                      'cash_components': {name: {'value': _json_number(item['value']), 'source_refs': item['source_refs'],
                                               'facts': item['facts']} for name, item in cash.items()},
                      'debt_components': {name: {'value': _json_number(item['value']), 'source_refs': item['source_refs'],
                                               'facts': item['facts']} for name, item in debt.items()},
                      'cash_classification_policy': CASH_POLICY,
                      'explanation': 'Exact sum of reported financial-debt components minus the exact sum of selected cash and marketable securities. No rounding residual or second cash add-back is permitted. Finance leases are outside this debt subtotal.',
                      'lease_policy': LEASE_POLICY}
            fields['net_debt'] = 'pinned'
        else:
            skipped.append('Net-debt bridge incomplete: every cash and financial-debt component must exist in the same currency on the annual balance-sheet date')
        lease_conflicts_before = len(conflicts)
        lease = {name: pick(concept, 'finance lease ' + name) for name, concept in FINANCE_LEASES.items()}
        total = lease['total']
        parts = lease['current'] and lease['noncurrent']
        if total and parts and total['value'] != lease['current']['value'] + lease['noncurrent']['value']:
            conflicts.append('Finance lease total conflicts with current plus noncurrent components')
            total = None
            parts = False
        if len(conflicts) != lease_conflicts_before:
            total, parts = None, False
        if total or parts:
            amount = total['value'] if total else lease['current']['value'] + lease['noncurrent']['value']
            used = [total] if total else [lease['current'], lease['noncurrent']]
            actuals['other_equity_claims'] = _json_number(amount)
            refs['other_equity_claims'] = sorted({sid for item in used for sid in item['source_refs']}) + ['assumption: additional unidentified claims are zero, not a reported fact']
            dates['other_equity_claims'] = {'instant': anchor.isoformat(), 'basis': 'reported finance lease liabilities plus explicit house assumption for unidentified claims'}
            claims_bridge = {'currency': currency, 'as_of': anchor.isoformat(),
                             'reported_finance_lease_liabilities': _json_number(amount),
                             'finance_leases_included_in_net_debt': False,
                             'additional_unidentified_claims': 0,
                             'additional_unidentified_claims_basis': CLAIMS_POLICY,
                             'other_equity_claims': _json_number(amount),
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
    resolved. All-cash classification and unidentified-claim zero are house policies.
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
                            'source_refs': canonical['source_refs'][field], 'reason': 'primary inline facts and explicit bridge policies'})
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

"""Bounded, source-honest SEC companyfacts normalization; no network access.

The SEC API supplies standardized taxonomy facts applying to the entire filing
entity. It does not supply the original inline context IDs or context XML. The
records below therefore remain ``sec_companyfacts`` observations, not parsed
inline facts. Filing fiscal-year labels and optional calendar frames are retained
as provenance, never used to replace the actual start/end dates.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re

from .inputs import PRIMARY_INPUT_CONCEPTS, REVENUE, SHARES, STRUCTURED_FORMS

ACCEPTED_FORMS = STRUCTURED_FORMS
ANNUAL_FORMS = ACCEPTED_FORMS - {'10-Q', '10-Q/A'}
MAX_REPORTED_FACTS = 1600
MAX_OBSERVATIONS_PER_PERIOD = 24
FINANCIAL_CONTEXT_CONCEPTS = frozenset('us-gaap:' + name for name in (
    'OperatingIncomeLoss', 'NetIncomeLoss', 'GrossProfit',
    'NetCashProvidedByUsedInOperatingActivities', 'PaymentsToAcquirePropertyPlantAndEquipment',
    'ShareBasedCompensation', 'StockBasedCompensation', 'DepreciationDepletionAndAmortization',
    'DepreciationDepletionAndAmortizationPropertyPlantAndEquipment', 'InventoryNet',
    'RepaymentsForRepurchaseOfCommonStock', 'PaymentsForRepurchaseOfCommonStock',
    'PaymentsOfDividends', 'IncomeTaxesPaidNet', 'IncomeTaxExpenseBenefit',
    'WeightedAverageNumberOfDilutedSharesOutstanding', 'WeightedAverageNumberOfSharesOutstandingBasic',
    'WeightedAverageNumberOfShareOutstandingBasicAndDiluted',
    'EarningsPerShareBasic', 'EarningsPerShareDiluted', 'EarningsPerShareBasicAndDiluted',
))
_AVERAGE_SHARES = frozenset(concept for concept in FINANCIAL_CONTEXT_CONCEPTS if 'WeightedAverage' in concept)
_PER_SHARE = frozenset(concept for concept in FINANCIAL_CONTEXT_CONCEPTS if 'EarningsPerShare' in concept)


def _unit(concept, original):
    if concept in SHARES | _AVERAGE_SHARES:
        return 'xbrli:shares' if original == 'shares' else None
    if concept in _PER_SHARE:
        return 'iso4217:' + original[:3] + '/xbrli:shares' if isinstance(original, str) and re.fullmatch(r'[A-Z]{3}/shares', original) else None
    return 'iso4217:' + original if isinstance(original, str) and re.fullmatch(r'[A-Z]{3}', original) else None


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _cik(value):
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]{1,10}', str(value)):
        return None
    result = int(value)
    return str(result) if result > 0 else None


def _value(raw):
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    try:
        number = Decimal(str(raw))
        return number if number.is_finite() and math.isfinite(float(number)) else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _observation_key(item):
    return (item['filing_date'], item['accession_number'], str(item.get('frame') or ''),
            str(item.get('fiscal_year') or ''), str(item.get('fiscal_period') or ''))


def normalize_companyfacts(payload, *, identity, source_id=None, as_of=None):
    """Return one regulatory-primary source after checking directory identity.

    ``identity`` is the SEC directory record containing ``cik_str``, ``title``
    and ``ticker``. Each record's own filing date governs historical eligibility.
    Three annual revenue periods and their exact-date balance-sheet observations
    are protected before recent quarters or later cover-page shares are selected.
    Caps never silently drop a competing reported value: normalization fails if
    the conflict-preserving representation cannot fit its explicit bounds.
    """
    if not isinstance(payload, dict) or not isinstance(identity, dict):
        raise ValueError('SEC companyfacts and directory identity must be objects')
    expected, actual = _cik(identity.get('cik_str')), _cik(payload.get('cik'))
    if not expected or actual != expected:
        raise ValueError('SEC companyfacts issuer does not match the directory identity')
    tags_by_namespace = payload.get('facts')
    if not isinstance(tags_by_namespace, dict):
        raise ValueError('SEC companyfacts taxonomy records are unavailable')
    today = datetime.now(timezone.utc).date()
    cutoff = min(_day(as_of) or today, today)
    url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{int(expected):010d}.json'
    records, rejected, seen = [], 0, set()
    for namespace, tags in tags_by_namespace.items():
        if namespace not in {'us-gaap', 'dei'} or not isinstance(tags, dict):
            continue
        for tag, record in tags.items():
            concept = namespace + ':' + tag
            if concept not in PRIMARY_INPUT_CONCEPTS | FINANCIAL_CONTEXT_CONCEPTS or not isinstance(record, dict):
                continue
            units = record.get('units')
            if not isinstance(units, dict):
                continue
            for original_unit, rows in units.items():
                unit = _unit(concept, original_unit)
                if unit is None or not isinstance(rows, list):
                    continue
                for index, raw in enumerate(rows):
                    if not isinstance(raw, dict):
                        rejected += 1
                        continue
                    filed, end = _day(raw.get('filed')), _day(raw.get('end'))
                    start = _day(raw.get('start')) if 'start' in raw else None
                    amount = _value(raw.get('val'))
                    accession, form = raw.get('accn'), raw.get('form')
                    if (not filed or not end or end > filed or filed > cutoff or
                            ('start' in raw and (not start or start > end)) or amount is None or
                            form not in ACCEPTED_FORMS or not isinstance(accession, str) or
                            not re.fullmatch(r'\d{10}-\d{2}-\d{6}', accession)):
                        rejected += 1
                        continue
                    raw_record = {key: raw[key] for key in ('start', 'end', 'val', 'accn', 'filed', 'form', 'fy', 'fp', 'frame')
                                  if key in raw}
                    signature = (concept, original_unit, json.dumps(raw_record, sort_keys=True, separators=(',', ':')))
                    if signature in seen:
                        continue
                    seen.add(signature)
                    records.append({
                        'concept': concept, 'fact_origin': 'sec_companyfacts', 'normalization_status': 'normalized',
                        'fact_role': 'valuation_input' if concept in PRIMARY_INPUT_CONCEPTS else 'financial_context',
                        'normalized_decimal': str(amount),
                        'normalized_value': int(amount) if amount == amount.to_integral_value() else float(amount),
                        'original_value': raw['val'], 'original_unit': original_unit,
                        'unit': unit, 'entity_identifier': expected, 'dimensions': {}, 'context_ref': None,
                        'instant': None if start else end.isoformat(),
                        'start_date': start.isoformat() if start else None,
                        'end_date': end.isoformat() if start else None,
                        'filing_date': filed.isoformat(), 'form': form, 'accession_number': accession,
                        'fiscal_year': raw.get('fy'), 'fiscal_period': raw.get('fp'), 'frame': raw.get('frame'),
                        'source_url': url, 'source_record_index': index, 'source_record': raw_record,
                    })
    annual_periods = sorted({(item['start_date'], item['end_date']) for item in records
                             if item['concept'] in REVENUE and item['start_date'] and item['form'] in ANNUAL_FORMS and
                             320 <= (_day(item['end_date']) - _day(item['start_date'])).days <= 400},
                            key=lambda period: (period[1], period[0]))
    annual_ends = sorted({period[1] for period in annual_periods})[-3:]
    annual_periods = [period for period in annual_periods if period[1] in annual_ends]
    annual_accessions = {item['accession_number'] for item in records
                         if item['concept'] in REVENUE and item['form'] in ANNUAL_FORMS and
                         (item['start_date'], item['end_date']) in annual_periods}
    latest_balance_dates = sorted({item['instant'] for item in records
                                   if item['instant'] and item['concept'] not in SHARES})[-2:]
    all_share_dates = sorted({item['instant'] for item in records if item['instant'] and item['concept'] in SHARES})
    annual_share_dates = sorted({item['instant'] for item in records if item['instant'] and item['concept'] in SHARES and
                                 item['form'] in ANNUAL_FORMS})[-3:]
    protected_share_dates = {item['instant'] for item in records if item['instant'] and item['concept'] in SHARES and
                             (item['instant'] in annual_ends or item['accession_number'] in annual_accessions)}
    share_dates = sorted(set(all_share_dates[-2:] + annual_share_dates) | protected_share_dates)
    recent_durations = sorted({(item['start_date'], item['end_date']) for item in records if item['start_date']},
                              key=lambda period: (period[1], period[0]))[-3:]
    balance_dates = set(annual_ends + latest_balance_dates)
    duration_periods = set(annual_periods + recent_durations)
    groups = defaultdict(list)
    for item in records:
        if (item['instant'] in (set(share_dates) if item['concept'] in SHARES else balance_dates) or
                (item['start_date'], item['end_date']) in duration_periods):
            groups[(item['concept'], item['unit'], item['instant'], item['start_date'], item['end_date'])].append(item)
    selected = []
    for observations in groups.values():
        values = defaultdict(list)
        for item in sorted(observations, key=_observation_key):
            values[item['normalized_decimal']].append(item)
        mandatory = {id(item) for rows in values.values() for item in (rows[0], rows[-1])}
        if len(mandatory) > MAX_OBSERVATIONS_PER_PERIOD:
            raise ValueError('SEC companyfacts period exceeds the conflict-preserving observation bound')
        kept = [item for item in observations if id(item) in mandatory]
        extras = sorted((item for item in observations if id(item) not in mandatory), key=_observation_key, reverse=True)
        selected.extend(kept + extras[:MAX_OBSERVATIONS_PER_PERIOD - len(kept)])
    if len(selected) > MAX_REPORTED_FACTS:
        raise ValueError('SEC companyfacts selected periods exceed the reported-fact bound')
    selected.sort(key=lambda item: (item['concept'], item['unit'], item['instant'] or item['end_date'],
                                    item['start_date'] or '', *_observation_key(item)))
    context_groups = defaultdict(list)
    for index, item in enumerate(selected):
        context_groups[(item['concept'], item['unit'], item['instant'], item['start_date'], item['end_date'])].append((index, item))
    context_table = []
    selected_source_id = source_id or 'SEC-FACTS-' + expected
    for (concept, unit, instant, start, end), observations in context_groups.items():
        values = sorted({Decimal(item['normalized_decimal']) for _, item in observations})
        numbers = [int(value) if value == value.to_integral_value() else float(value) for value in values]
        context_table.append({'concept': concept, 'unit': unit, 'instant': instant, 'start_date': start, 'end_date': end,
                              'status': 'reported' if len(values) == 1 else 'conflicted',
                              'value': numbers[0] if len(values) == 1 else None, 'reported_values': numbers,
                              'source_refs': [selected_source_id], 'reported_fact_indices': [index for index, _ in observations],
                              'filing_dates': sorted({item['filing_date'] for _, item in observations}),
                              'ordinary_share_denominator_eligible': concept in SHARES})
    try:
        canonical_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                                   allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError):
        raise ValueError('SEC companyfacts payload is not a finite JSON object') from None
    return {
        'id': selected_source_id, 'title': 'SEC company facts: ' + str(payload.get('entityName') or identity.get('title') or expected),
        'url': url, 'kind': 'sec_facts', 'source_tier': 'regulatory_primary',
        'retrieved_at': datetime.now(timezone.utc).isoformat(),
        'data': {
            'metadata': {'cik': int(expected), 'issuer': payload.get('entityName'),
                         'directory_issuer': identity.get('title'), 'ticker': identity.get('ticker'), 'source_url': url},
            'fact_origin': 'sec_companyfacts', 'canonical_payload_sha256': canonical_hash,
            'reported_facts': selected,
            'financial_context_table': context_table,
            'reported_fact_note': ('Official SEC standardized-taxonomy entity-wide observations. Original inline context IDs and '
                                   'dimension XML are not provided by this API. Per-record filing dates and accessions remain explicit; '
                                   'missing tags remain unknown, and filing fiscal-year/frame labels do not replace fact periods.'),
            'selection': {'as_of': cutoff.isoformat(), 'annual_periods': [list(period) for period in annual_periods],
                          'protected_balance_dates': sorted(balance_dates), 'share_dates': share_dates,
                          'valid_record_count': len(records), 'selected_record_count': len(selected),
                          'rejected_record_count': rejected, 'omitted_record_count': len(records) - len(selected),
                          'policy': 'Three latest annual revenue periods, same-date bridge facts and annual-filing shares; two latest balance/share dates; recent duration observations; earliest/latest observations of every retained competing value.'},
        },
    }

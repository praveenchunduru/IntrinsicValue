"""Summarize request-ledger actuals and retained estimates without request text.

This reports provider usage and the runner's spending ledger. It does not infer
unreported tokens, cache savings, invoice settlement, model quality or best price.
Stage response reuse adds no API-request row and cannot be counted from a ledger.
"""
from __future__ import annotations

import math
import re


_LABEL = re.compile(r'[A-Za-z0-9_./:@+\-]{1,200}\Z')
_MONEY = ('total_charged_or_reserved_usd', 'known_usage_cost_usd', 'retained_estimates_usd')
_TOKENS = ('prompt_tokens', 'completion_tokens', 'cached_prompt_tokens')


def _number(value, name, *, tokens=False):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'Cost ledger {name} must be a finite nonnegative JSON number')
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or value < 0 or (tokens and value != int(value)):
        raise ValueError(f'Cost ledger {name} must be a finite nonnegative ' + ('integer' if tokens else 'JSON number'))
    return int(value) if tokens else float(value)


def _label(value):
    # Routing labels are useful; arbitrary prose and response/error text are not.
    return value if isinstance(value, str) and _LABEL.fullmatch(value) else 'unknown'


def _empty():
    return {
        'request_count': 0,
        'requests_with_known_usage_cost': 0,
        'requests_with_retained_estimates': 0,
        'requests_without_charge': 0,
        'request_statuses': {},
        **{name: [] for name in _MONEY},
        **{name: [] for name in _TOKENS},
    }


def _add(group, values):
    group['request_count'] += 1
    status = values['status']
    group['request_statuses'][status] = group['request_statuses'].get(status, 0) + 1
    charge, actual = values['charge'], values['actual']
    if charge is not None:
        group['total_charged_or_reserved_usd'].append(charge)
    else:
        group['requests_without_charge'] += 1
    if actual is not None:
        group['known_usage_cost_usd'].append(actual)
        group['requests_with_known_usage_cost'] += 1
    elif charge is not None:
        group['retained_estimates_usd'].append(charge)
        group['requests_with_retained_estimates'] += 1
    for name in _TOKENS:
        if values[name] is not None:
            group[name].append(values[name])


def _finish(group):
    result = {name: group[name] for name in (
        'request_count', 'requests_with_known_usage_cost',
        'requests_with_retained_estimates', 'requests_without_charge')}
    for name in _MONEY:
        try:
            value = math.fsum(group[name])
        except OverflowError:
            raise ValueError('Cost ledger total exceeds finite JSON numbers') from None
        if not math.isfinite(value):
            raise ValueError('Cost ledger total exceeds finite JSON numbers')
        result[name] = value
    for name in _TOKENS:
        result[name] = sum(group[name]) if group[name] else None
        result[name + '_reported_requests'] = len(group[name])
    result['request_statuses'] = dict(sorted(group['request_statuses'].items()))
    return result


def cost_summary(ledger):
    """Return deterministic totals and stage/model breakdowns of API ledger rows.

    Known cost means a reported ``usage.cost``, including zero; a successful
    response without that field still has an estimated, not known, charge.
    Missing token counts remain unknown (None), with coverage counts alongside.
    Provider cached prompt tokens are not added to prompt tokens a second time.

    The charged/reserved column governs its total. For older/minimal rows without
    it, reported usage cost, then the saved reservation, supply the available
    charge. Rows with none of these remain explicitly unpriced. Initial reserves
    are not added again after a reported charge replaces them. The original
    ledger is never mutated, and only routing labels/numeric usage are emitted.
    """
    if not isinstance(ledger, list):
        raise ValueError('Cost ledger must be a list of request rows')
    totals, stages, models, pairs = _empty(), {}, {}, {}
    for row in ledger:
        if not isinstance(row, dict):
            raise ValueError('Cost ledger request rows must be objects')
        usage = row.get('usage')
        if usage is None:
            usage = {}
        if not isinstance(usage, dict):
            raise ValueError('Cost ledger usage must be an object when reported')
        charged = _number(row.get('charged_or_reserved_usd'), 'charged_or_reserved_usd')
        reserved = _number(row.get('reserved_usd'), 'reserved_usd')
        actual = _number(usage.get('cost'), 'usage.cost')
        charge = charged if charged is not None else actual if actual is not None else reserved
        details = usage.get('prompt_tokens_details')
        if details is None:
            details = {}
        if not isinstance(details, dict):
            raise ValueError('Cost ledger prompt token details must be an object when reported')
        values = {
            'charge': charge, 'actual': actual, 'status': _label(row.get('status')),
            'prompt_tokens': _number(usage.get('prompt_tokens'), 'usage.prompt_tokens', tokens=True),
            'completion_tokens': _number(usage.get('completion_tokens'), 'usage.completion_tokens', tokens=True),
            'cached_prompt_tokens': _number(details.get('cached_tokens'), 'usage.prompt_tokens_details.cached_tokens', tokens=True),
        }
        stage, model = _label(row.get('stage')), _label(row.get('model'))
        actual_model = _label(row.get('actual_model')) if row.get('actual_model') is not None else None
        pair = (stage, model, actual_model)
        _add(totals, values)
        _add(stages.setdefault(stage, _empty()), values)
        _add(models.setdefault(model, _empty()), values)
        _add(pairs.setdefault(pair, _empty()), values)
    return {
        'currency': 'USD',
        'totals': _finish(totals),
        'by_stage': [{'stage': stage, **_finish(group)} for stage, group in sorted(stages.items())],
        'by_model': [{'model': model, **_finish(group)} for model, group in sorted(models.items())],
        'by_stage_model': [
            {'stage': stage, 'model': model, 'actual_model': actual_model, **_finish(pairs[(stage, model, actual_model)])}
            for stage, model, actual_model in sorted(pairs, key=lambda key: (key[0], key[1], key[2] or ''))
        ],
        'scope': {
            'costs': 'Provider-reported usage costs and retained ledger estimates; not an invoice reconciliation or a price-performance ranking.',
            'model_grouping': 'by_model uses the requested model; by_stage_model also preserves actual_model when reported.',
            'tokens': 'Only reported counts are summed; None means no count was reported. Cached prompt tokens are part of provider usage, not extra prompt tokens.',
            'stage_cache': 'Stage response reuse makes no API ledger row. Stage cache hits are not observable or counted here.',
        },
    }

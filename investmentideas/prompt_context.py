"""Deterministic valuation presentation; saved full math artifacts stay authoritative.

This module neither evaluates assumptions nor rounds reported/computed numbers.
It removes repeated JSON subtrees and detailed intermediate countercase rows.
"""
from __future__ import annotations

import copy
import hashlib
import json


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _pointer(path, key):
    return path + '/' + str(key).replace('~', '~0').replace('/', '~1')


def _has_literal_reference(value):
    if isinstance(value, dict):
        return (set(value) == {'same_as', 'sha256'} or
                any(_has_literal_reference(item) for item in value.values()))
    return isinstance(value, list) and any(_has_literal_reference(item) for item in value)


_CASHFLOW_AGGREGATES = (
    'year', 'fiscal_year', 'period_end', 'end_date',
    'fcff', 'free_cash_flow', 'discount_factor', 'pv_fcff',
)


def _case_cashflows(rows):
    """Retain annual cash/PV totals and complete first/last operating rows."""
    if (not isinstance(rows, list) or len(rows) <= 2 or
            not all(isinstance(row, dict) for row in rows)):
        return rows
    aggregates = []
    for row in rows:
        # Unknown schemas are retained intact rather than guessed or zeroed.
        if not any(key in row for key in ('fcff', 'free_cash_flow', 'pv_fcff')):
            return rows
        aggregates.append({key: row[key] for key in _CASHFLOW_AGGREGATES if key in row})
    summary = {
        'row_count': len(rows),
        'annual_aggregate_cashflows': aggregates,
        'first_operating_row': rows[0],
        'last_operating_row': rows[-1],
        'detail_scope': 'Intermediate countercase operating detail is in the full result artifact.',
    }
    return summary if len(_json(summary)) < len(_json(rows)) else rows


def _deduplicate(value, path, seen):
    """Replace only byte-exact JSON repeats when a backward reference is shorter."""
    serialized = _json(value)
    eligible = isinstance(value, (dict, list, str)) and len(serialized.encode('utf-8')) >= 160
    if eligible:
        fingerprint = hashlib.sha256(serialized.encode('utf-8')).hexdigest()
        previous = seen.get(fingerprint)
        if previous is not None:
            original_serialized, original_path = previous
            reference = {'same_as': original_path, 'sha256': fingerprint}
            # Compare originals as well: hash equality alone is not identity.
            if original_serialized == serialized and len(_json(reference)) < len(serialized):
                return reference
        else:
            seen[fingerprint] = (serialized, path)
    if isinstance(value, dict):
        return {key: _deduplicate(value[key], _pointer(path, key), seen)
                for key in sorted(value)}
    if isinstance(value, list):
        return [_deduplicate(item, _pointer(path, index), seen)
                for index, item in enumerate(value)]
    return value


def valuation_context(plan, result):
    """Return compact JSON retaining assumptions and authoritative result headlines.

    The full plan and all result fields remain, including unfamiliar calibration,
    monitoring, sensitivity, diagnostic and reverse-DCF fields. Only cashflows
    directly inside named ``counterfactuals``/``countercases`` are summarized:
    annual FCFF/PV observations plus complete first/last operating rows survive.
    Main-scenario cashflows and every annual revenue path remain complete.

    Repeated subtrees/long text use plain ``same_as`` RFC6901 pointers within this JSON,
    with SHA256 of the original compact, sorted, UTF-8 JSON subtree. References
    always target an earlier materialized value. No input is mutated or evaluated.
    """
    if not isinstance(plan, dict) or not isinstance(result, dict):
        raise ValueError('Plan and result must be JSON objects')
    # Validate JSON and nonfinite values before transforming any content.
    plan_hash, result_hash = _sha(plan), _sha(result)
    references_enabled = not (_has_literal_reference(plan) or _has_literal_reference(result))
    presentation = copy.deepcopy(result)
    for key in ('counterfactuals', 'countercases'):
        cases = presentation.get(key)
        if isinstance(cases, dict):
            for case in cases.values():
                if isinstance(case, dict) and 'cashflows' in case:
                    case['cashflows'] = _case_cashflows(case['cashflows'])
    payload = {
        'context_metadata': {
            'format': 'valuation-context-v1',
            'full_math_artifacts_authoritative': True,
            'authority': 'The full original plan and result artifacts are authoritative; this is a deterministic presentation, not new valuation math.',
            'plan_sha256': plan_hash,
            'result_sha256': result_hash,
            'references_enabled': references_enabled,
            'projection_scope': 'Main scenarios and all revenue paths are complete. Countercase cashflows may show annual FCFF/PV aggregates and complete first/last operating rows; intermediate operating detail remains in the full artifact. All other fields are retained.',
            'reference_basis': 'When references_enabled is true, same_as is a plain RFC6901 JSON pointer (not a URI fragment). Resolve it for the exact repeated value; sha256 hashes its compact sorted UTF-8 JSON after cashflow presentation and before reference replacement. References are disabled if an original input contains the reserved two-field same_as/sha256 object, so original literal objects retain their meaning.',
        },
        'plan': copy.deepcopy(plan),
        'result': presentation,
    }
    return _json(_deduplicate(payload, '', {}) if references_enabled else payload)

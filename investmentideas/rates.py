"""Deterministic, source-dated market WACC and an optional explicit house hurdle.

``calculate_discount_rate(spec)`` accepts decimal rates and whole currency units::

    {"as_of": "2026-11-06", "currency": "USD", "amount_unit": "currency_units",
     "basis": "market_wacc", "lease_convention": "gaap_operating",
     "risk_free_rate": record, "equity_risk_premium": record,
     "unlevered_beta": record, "market_equity": record,
     "financial_debt": record, "finance_lease_debt": record,
     "pretax_cost_of_debt": record, "marginal_tax_rate": record}

Every record requires ``value``, ``observed_at`` (YYYY-MM-DD), a nonempty
``source_refs`` list and a descriptive ``label``. Additional JSON metadata such as
sector, tenor, publication date, methodology and source URL is preserved. Zero
debt must be supplied explicitly. ``financial_debt`` excludes finance leases,
which are entered separately. Its ``measurement`` is ``book_value_proxy`` unless
explicitly ``market_value``. Equity is market equity, never book equity.
Only the sector-beta record may instead use ``observed_at=YYYY-MM`` with explicit
``date_precision=month``. Its observation month must end no later than ``as_of``;
the source's unavailable measurement day is never invented.

The risk-free rate and implied ERP must be from the same observation date.
Different-dated sector beta and balance-sheet observations remain visible; none
can be after the valuation date. No network access or replacement of inputs
occurs here. A newer Treasury rate must not silently replace the ERP-paired rate.

``gaap_operating`` keeps operating-lease expense in EBIT and excludes operating
lease liabilities from relevering and capital weights. ``capitalized_operating``
requires an explicit ``ebit_convention=operating_lease_adjusted`` and a sourced
``operating_lease_debt`` record; this calculator does not adjust cash flows.
Finance leases are included in debt under either convention. Gross debt is used;
cash is not netted against debt when relevering beta or computing capital weights.

An optional ``house_hurdle`` record is reported separately and applied only with
``basis=house_hurdle``. A hurdle is a policy choice, never relabeled market WACC.
Parameter sensitivities are isolated calculations, not scenario risk premia.
"""
from __future__ import annotations

import copy
from calendar import monthrange
from datetime import date, datetime, timezone
import json
import math
import re


_REQUIRED = ('risk_free_rate', 'equity_risk_premium', 'unlevered_beta',
             'market_equity', 'financial_debt', 'finance_lease_debt',
             'pretax_cost_of_debt', 'marginal_tax_rate')
_OPTIONAL = ('operating_lease_debt', 'house_hurdle')
_FIELDS = set(_REQUIRED + _OPTIONAL + ('as_of', 'currency', 'amount_unit',
              'basis', 'lease_convention', 'ebit_convention'))
_RANGES = {'risk_free_rate': (-.05, .30), 'equity_risk_premium': (0, .40),
           'unlevered_beta': (0, 5), 'pretax_cost_of_debt': (0, .60),
           'marginal_tax_rate': (0, .60), 'house_hurdle': (0, 1)}


def _date(value, name):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError(f'{name} must be YYYY-MM-DD')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f'{name} must be YYYY-MM-DD') from None


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a finite number')
    try:
        valid = math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f'{name} must be a finite number')
    return float(value)


def _observation_period(record, name):
    precision = record.get('date_precision', 'day')
    value = record.get('observed_at')
    if name == 'unlevered_beta' and precision == 'month':
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}', value):
            raise ValueError('Monthly unlevered_beta.observed_at must be YYYY-MM')
        start = _date(value + '-01', name + '.observed_at')
        end = date(start.year, start.month, monthrange(start.year, start.month)[1])
        return start, end, precision
    if precision != 'day':
        raise ValueError('Only unlevered_beta supports date_precision=month; market pairs require exact dates')
    observation = _date(value, name + '.observed_at')
    return observation, observation, precision


def _record(spec, name, as_of):
    record = spec.get(name)
    if not isinstance(record, dict):
        raise ValueError(f'{name} requires an explicit dated input record; unknown is not zero')
    value = _number(record.get('value'), name + '.value')
    _, observation, _ = _observation_period(record, name)
    if observation > as_of:
        raise ValueError(f'{name}.observed_at is after as_of; future observations are invalid')
    if 'publication_date' in record and _date(record['publication_date'], name + '.publication_date') > as_of:
        raise ValueError(f'{name}.publication_date is after as_of')
    refs = record.get('source_refs')
    if (not isinstance(refs, list) or not refs or
            any(not isinstance(ref, str) or not ref.strip() for ref in refs)):
        raise ValueError(f'{name}.source_refs must be a nonempty list of source references')
    if not isinstance(record.get('label'), str) or not record['label'].strip():
        raise ValueError(f'{name}.label must describe the input and its convention')
    if record.get('currency', spec['currency']) != spec['currency']:
        raise ValueError(f'{name}.currency must match the discount-rate currency')
    if name in ('market_equity', 'financial_debt', 'finance_lease_debt', 'operating_lease_debt'):
        if record.get('amount_unit', 'currency_units') != 'currency_units':
            raise ValueError(f'{name} must use whole currency_units')
        if value < 0 or (name == 'market_equity' and value == 0):
            raise ValueError(f'{name} must be nonnegative, and market_equity must be positive')
    if name == 'market_equity' and record.get('measurement', 'market_value') != 'market_value':
        raise ValueError('market_equity.measurement must be market_value; book equity is not a capital weight')
    if name in _RANGES:
        low, high = _RANGES[name]
        if not low <= value <= high or (name == 'house_hurdle' and value in (0, 1)):
            raise ValueError(f'{name} must be within its decimal-rate range {low} to {high}')
    return value


def _market(values, included_debt):
    equity = values['market_equity']
    total = equity + included_debt
    debt_to_equity = included_debt / equity
    beta = values['unlevered_beta'] * (1 + (1 - values['marginal_tax_rate']) * debt_to_equity)
    equity_cost = values['risk_free_rate'] + beta * values['equity_risk_premium']
    equity_weight, debt_weight = equity / total, included_debt / total
    after_tax_debt = values['pretax_cost_of_debt'] * (1 - values['marginal_tax_rate'])
    wacc = equity_weight * equity_cost + debt_weight * after_tax_debt
    result = {'market_wacc': wacc, 'relevered_beta': beta,
              'cost_of_equity': equity_cost, 'after_tax_cost_of_debt': after_tax_debt,
              'debt_to_equity': debt_to_equity,
              'equity_weight': equity_weight, 'debt_weight': debt_weight}
    if not all(math.isfinite(number) for number in result.values()) or not 0 < wacc < 1:
        raise ValueError('Inputs do not produce a finite market WACC between zero and one')
    return result


def calculate_discount_rate(spec, *, as_of=None):
    """Validate the evidence and compute WACC without altering supplied inputs.

    ``as_of`` optionally supplies the valuation date when absent from ``spec``;
    if both are provided they must match. Neither may be in the future (UTC).
    Returned provenance is a deep copy suitable for an assumptions/audit artifact.
    """
    if not isinstance(spec, dict):
        raise ValueError('discount_rate must be an object')
    unknown = set(spec) - _FIELDS
    if unknown:
        raise ValueError('Unsupported discount_rate fields: ' + ', '.join(sorted(map(str, unknown))))
    valuation_date = _date(spec.get('as_of', as_of), 'as_of')
    if as_of is not None and _date(as_of, 'as_of') != valuation_date:
        raise ValueError('as_of argument and discount_rate.as_of must match')
    if valuation_date > datetime.now(timezone.utc).date():
        raise ValueError('as_of cannot be in the future')
    if not isinstance(spec.get('currency'), str) or not re.fullmatch(r'[A-Z]{3}', spec['currency']):
        raise ValueError('currency must be an explicit three-letter currency code')
    if spec.get('amount_unit') != 'currency_units':
        raise ValueError('amount_unit must be currency_units; use whole currency amounts')
    basis = spec.get('basis', 'market_wacc')
    if basis not in ('market_wacc', 'house_hurdle'):
        raise ValueError('basis must be market_wacc or house_hurdle')
    lease = spec.get('lease_convention')
    if lease not in ('gaap_operating', 'capitalized_operating'):
        raise ValueError('lease_convention must explicitly be gaap_operating or capitalized_operating')
    ebit = spec.get('ebit_convention', 'gaap_operating' if lease == 'gaap_operating' else None)
    if lease == 'gaap_operating' and ebit != 'gaap_operating':
        raise ValueError('gaap_operating leases require gaap_operating EBIT')
    if lease == 'capitalized_operating' and ebit != 'operating_lease_adjusted':
        raise ValueError('Capitalized operating leases require operating_lease_adjusted EBIT; no automatic adjustment')
    values = {name: _record(spec, name, valuation_date) for name in _REQUIRED}
    for name in _OPTIONAL:
        if name in spec:
            values[name] = _record(spec, name, valuation_date)
    if spec['risk_free_rate']['observed_at'] != spec['equity_risk_premium']['observed_at']:
        raise ValueError('risk_free_rate and equity_risk_premium must use the same matched observation date')
    if lease == 'capitalized_operating' and 'operating_lease_debt' not in values:
        raise ValueError('Capitalized operating leases require explicit operating_lease_debt')
    if basis == 'house_hurdle' and 'house_hurdle' not in values:
        raise ValueError('basis=house_hurdle requires an explicit dated house_hurdle record')
    measurement = spec['financial_debt'].get('measurement', 'book_value_proxy')
    if measurement not in ('book_value_proxy', 'market_value'):
        raise ValueError('financial_debt.measurement must be book_value_proxy or market_value')
    included_operating = values['operating_lease_debt'] if lease == 'capitalized_operating' else 0
    debt = values['financial_debt'] + values['finance_lease_debt'] + included_operating
    result = _market(values, debt)
    result.update({'as_of': valuation_date.isoformat(), 'currency': spec['currency'],
                   'amount_unit': 'currency_units', 'basis': basis,
                   'applied_wacc': values['house_hurdle'] if basis == 'house_hurdle' else result['market_wacc'],
                   'house_hurdle': values.get('house_hurdle'),
                   'market_equity': values['market_equity'], 'included_debt': debt,
                   'financial_debt': values['financial_debt'], 'finance_lease_debt': values['finance_lease_debt'],
                   'operating_lease_debt_included': included_operating,
                   'matched_observation_date': spec['risk_free_rate']['observed_at']})
    result['conventions'] = {
        'lease_convention': lease, 'ebit_convention': ebit,
        'financial_debt_measurement': measurement,
        'capital_weights': 'market equity plus gross financial debt and included leases; no cash netting',
        'financial_debt_input': 'excludes separately supplied finance and operating leases',
        'tax': 'normalized marginal tax for the debt tax shield; not the historical effective tax rate',
            'beta': 'Hamada relevering of the supplied operating-asset beta using marginal tax and included gross debt; zero debt beta assumption',
        'debt_cost_application': 'one supplied pretax debt cost applied to included financial and lease debt',
        'house_hurdle': 'optional explicit policy choice, separate from market WACC',
        'operating_leases': ('operating expense remains in GAAP EBIT; operating lease liabilities excluded'
                             if lease == 'gaap_operating' else
                             'caller affirms lease-adjusted EBIT/cashflows; operating lease liabilities included'),
        'formulas': {'relevered_beta': 'unlevered_beta * (1 + (1 - marginal_tax_rate) * debt / market_equity)',
                     'cost_of_equity': 'risk_free_rate + relevered_beta * equity_risk_premium',
                     'market_wacc': 'equity_weight * cost_of_equity + debt_weight * pretax_cost_of_debt * (1 - marginal_tax_rate)'}}
    provenance = {name: copy.deepcopy(spec[name]) for name in _REQUIRED + _OPTIONAL if name in spec}
    try:
        json.dumps(provenance, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('Input provenance must contain finite JSON-compatible metadata') from None
    result['provenance'] = provenance
    periods = {name: _observation_period(record, name) for name, record in provenance.items()}
    result['input_observation_periods'] = {
        name: {'start': start.isoformat(), 'end': end.isoformat(), 'precision': precision}
        for name, (start, end, precision) in periods.items()}
    result['input_ages_days'] = {name: (valuation_date - end).days
                                 for name, (_, end, _) in periods.items()}
    result['input_age_basis'] = 'Days since observation-period end; a monthly beta has no asserted measurement day'
    result['sensitivity'] = []
    for parameter, shift in (('risk_free_rate', .005), ('equity_risk_premium', .01),
                             ('unlevered_beta', .10), ('pretax_cost_of_debt', .01)):
        for delta in (-shift, shift):
            changed = dict(values, **{parameter: values[parameter] + delta})
            low, high = _RANGES[parameter]
            row = {'parameter': parameter, 'change': delta, 'input_value': changed[parameter],
                   'purpose': 'isolated parameter sensitivity; not a scenario risk premium'}
            if low <= changed[parameter] <= high:
                try:
                    row.update(_market(changed, debt))
                except ValueError as error:
                    row['unavailable_reason'] = str(error)
            else:
                row['unavailable_reason'] = 'Changed input is outside the supported input range'
            result['sensitivity'].append(row)
    return result


# A short alias for callers that need only the market-WACC calculator interface.
calculate_wacc = calculate_discount_rate

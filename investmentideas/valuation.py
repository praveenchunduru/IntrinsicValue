"""Deterministic, editable unlevered FCFF valuation in whole currency units.

``evaluate(plan)`` expects the following JSON-compatible schema::

    {
      "currency": "USD", "amount_unit": "currency_units", "share_unit": "shares",
      "assumptions_label": "House forecasts; zero other claims is an assumption...",
      "actuals": {"revenue": 1000000000, "net_debt": 100000000,
                  "other_equity_claims": 0, "shares_outstanding": 100000000},
      "source_refs": {"revenue": ["S1"], "net_debt": ["S2"],
                      "other_equity_claims": ["assumption: no identified claims"],
                      "shares_outstanding": ["S2"], "market_price": ["S3"]},
      "market_price": 12.0,
      "scenarios": {
        "bear": {"probability": .25, "growth_rates": [.02, .02, .02, .02, .02],
                 "ebit_margins": [.18, .18, .18, .18, .18], "tax_rate": .25,
                 "sales_to_capital": 2.0, "wacc": .10,
                 "terminal_growth": .02, "terminal_roic": .15},
        "base": {"...": "same required fields"},
        "bull": {"...": "same required fields"}
      }
    }

All money amounts, including net debt and claims, use *whole* currency units;
shares are a whole-share count. Net debt already deducts the selected cash balance:
it is subtracted once, and there is no second cash add-back. Unknown actuals are
errors, never zero defaults. The pipeline must verify source support and dates.

Annual FCFF = EBIT - cash operating taxes - incremental revenue / sales_to_capital.
Sales-to-capital is an incremental investment ratio including working capital and
net fixed investment; maintenance investment offsets depreciation in this model.
Negative EBIT creates no assumed cash tax credit. No NOL schedule is modelled.
Terminal reinvestment = terminal NOPAT * terminal_growth / terminal_roic. Debt and
other noncommon claims are bridged after enterprise value. No value floors apply.

Optional ``counterfactuals`` maps up to eight named cases to base-scenario
overrides, for example ``{"higher_growth": {"growth_rates": [.10] * 5}}``.
Names use 1–64 letters, digits, underscores, dots or hyphens, starting with a
letter or digit. Only growth_rates, ebit_margins, tax_rate, sales_to_capital,
wacc, terminal_growth and terminal_roic may be overridden. Trajectories must
retain the base horizon. These conditional cases never alter actuals, scenario
probabilities or the house weighted value. Output records exact overrides,
effective inputs, fixed-input basis and independently projected cash flows.
Reverse-DCF output also records each solved growth rate's projected revenue and
annual revenue increments, using that exact solution and the fixed base inputs.
"""
from __future__ import annotations

import copy
import csv
from datetime import date
import json
import math
from pathlib import Path
import re

SCENARIOS = ('bear', 'base', 'bull')
ACTUAL_FIELDS = ('revenue', 'net_debt', 'other_equity_claims', 'shares_outstanding')
FORECAST_FIELDS = ('growth_rates', 'ebit_margins', 'tax_rate', 'sales_to_capital',
                   'wacc', 'terminal_growth', 'terminal_roic')
MAX_COUNTERFACTUALS = 8


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


def _required(mapping, name, context):
    if name not in mapping or mapping[name] is None:
        raise ValueError(f'Missing {context}.{name}; unknown inputs cannot be replaced with zero')
    return mapping[name]


def _reference(value):
    return ((isinstance(value, str) and bool(value.strip())) or
            (isinstance(value, list) and bool(value) and
             all(isinstance(item, str) and item.strip() for item in value)))


def _validated(plan):
    if not isinstance(plan, dict):
        raise ValueError('Valuation plan must be an object')
    if not isinstance(plan.get('currency'), str) or not re.fullmatch(r'[A-Z]{3}', plan['currency']):
        raise ValueError('currency must be an explicit three-letter currency code')
    if plan.get('amount_unit') != 'currency_units' or plan.get('share_unit') != 'shares':
        raise ValueError('Use amount_unit=currency_units and share_unit=shares; do not mix millions with whole shares')
    label = plan.get('assumptions_label')
    if not isinstance(label, str) or not label.strip():
        raise ValueError('A nonempty assumptions_label must distinguish forecasts and assumptions from reported actuals')
    if 'valuation_date' in plan:
        try:
            date.fromisoformat(plan['valuation_date'])
        except (ValueError, TypeError):
            raise ValueError('valuation_date must be YYYY-MM-DD') from None
    actuals = _required(plan, 'actuals', 'plan')
    refs = _required(plan, 'source_refs', 'plan')
    if not isinstance(actuals, dict) or not isinstance(refs, dict):
        raise ValueError('actuals and source_refs must be objects')
    actuals = {key: _number(_required(actuals, key, 'actuals'), 'actuals.' + key) for key in ACTUAL_FIELDS}
    for key in ACTUAL_FIELDS:
        if not _reference(refs.get(key)):
            raise ValueError(f'Explicit source_refs.{key} is required; an assumed zero must be disclosed')
    if actuals['revenue'] <= 0 or actuals['shares_outstanding'] <= 0:
        raise ValueError('Revenue and shares_outstanding must be positive')
    if actuals['other_equity_claims'] < 0:
        raise ValueError('other_equity_claims cannot be negative')
    definitions = _required(plan, 'scenarios', 'plan')
    if not isinstance(definitions, dict) or set(definitions) != set(SCENARIOS):
        raise ValueError('Provide exactly bear, base and bull scenario objects')
    scenarios = {}
    for name in SCENARIOS:
        item = definitions[name]
        if not isinstance(item, dict):
            raise ValueError(f'{name} scenario must be an object')
        growth = _required(item, 'growth_rates', name)
        margins = _required(item, 'ebit_margins', name)
        if (not isinstance(growth, list) or not isinstance(margins, list) or
                not 1 <= len(growth) <= 15 or len(growth) != len(margins)):
            raise ValueError(f'{name}: provide matching annual growth_rates and ebit_margins lists of 1–15 years')
        values = {key: _number(_required(item, key, name), name + '.' + key)
                  for key in ('probability', 'tax_rate', 'sales_to_capital', 'wacc', 'terminal_growth', 'terminal_roic')}
        values['growth_rates'] = [_number(value, name + '.growth_rates') for value in growth]
        values['ebit_margins'] = [_number(value, name + '.ebit_margins') for value in margins]
        if not 0 <= values['probability'] <= 1:
            raise ValueError(f'{name}: probability must be between zero and one')
        if not 0 <= values['tax_rate'] <= .6:
            raise ValueError(f'{name}: cash operating tax_rate must be between 0 and 0.60')
        if values['sales_to_capital'] <= 0:
            raise ValueError(f'{name}: sales_to_capital must be positive')
        if not 0 < values['wacc'] < 1:
            raise ValueError(f'{name}: wacc must be between zero and one')
        if not -.10 <= values['terminal_growth'] <= .15 or values['wacc'] <= values['terminal_growth']:
            raise ValueError(f'{name}: terminal growth must be between -10% and 15% and strictly below WACC')
        if values['terminal_roic'] <= 0 or values['terminal_roic'] < values['terminal_growth']:
            raise ValueError(f'{name}: terminal_roic must be positive and at least terminal_growth')
        if any(not -.95 <= value <= 3 for value in values['growth_rates']):
            raise ValueError(f'{name}: annual growth must be between -95% and 300%')
        if any(not -1 <= value <= 1 for value in values['ebit_margins']):
            raise ValueError(f'{name}: EBIT margins must be between -100% and 100%')
        if values['ebit_margins'][-1] <= 0:
            raise ValueError(f'{name}: a going-concern terminal model requires a positive final EBIT margin; use an explicit distress model otherwise')
        scenarios[name] = values
    if not math.isclose(sum(value['probability'] for value in scenarios.values()), 1, rel_tol=0, abs_tol=1e-8):
        raise ValueError('Scenario probabilities must sum to one')
    market = None
    if plan.get('market_price') is not None:
        market = _number(plan['market_price'], 'market_price')
        if market <= 0 or not _reference(refs.get('market_price')):
            raise ValueError('A positive market_price requires its own source reference')
    return actuals, scenarios, market


def _project(actuals, scenario):
    revenue = actuals['revenue']
    rows = []
    for year, (growth, margin) in enumerate(zip(scenario['growth_rates'], scenario['ebit_margins']), 1):
        previous = revenue
        revenue *= 1 + growth
        ebit = revenue * margin
        cash_tax = max(ebit, 0) * scenario['tax_rate']
        nopat = ebit - cash_tax
        reinvestment = (revenue - previous) / scenario['sales_to_capital']
        fcff = nopat - reinvestment
        discount = (1 + scenario['wacc']) ** year
        rows.append({'year': year, 'revenue': revenue, 'revenue_growth': growth,
                     'ebit_margin': margin, 'ebit': ebit, 'cash_operating_tax': cash_tax,
                     'nopat': nopat, 'net_reinvestment': reinvestment, 'fcff': fcff,
                     'discount_factor': 1 / discount, 'pv_fcff': fcff / discount})
    terminal_revenue = revenue * (1 + scenario['terminal_growth'])
    terminal_ebit = terminal_revenue * scenario['ebit_margins'][-1]
    terminal_nopat = terminal_ebit * (1 - scenario['tax_rate'])
    terminal_reinvestment = terminal_nopat * scenario['terminal_growth'] / scenario['terminal_roic']
    terminal_fcff = terminal_nopat - terminal_reinvestment
    terminal_value = terminal_fcff / (scenario['wacc'] - scenario['terminal_growth'])
    pv_terminal = terminal_value / (1 + scenario['wacc']) ** len(rows)
    pv_forecast = sum(row['pv_fcff'] for row in rows)
    enterprise = pv_forecast + pv_terminal
    equity = enterprise - actuals['net_debt'] - actuals['other_equity_claims']
    per_share = equity / actuals['shares_outstanding']
    result = {'enterprise_value': enterprise, 'net_debt': actuals['net_debt'],
              'other_equity_claims': actuals['other_equity_claims'], 'equity_value': equity,
              'shares_outstanding': actuals['shares_outstanding'], 'per_share_value': per_share,
              'pv_forecast_fcff': pv_forecast, 'pv_terminal_value': pv_terminal,
              'terminal_value_fraction': pv_terminal / enterprise if enterprise else None,
              'terminal_revenue': terminal_revenue, 'terminal_nopat': terminal_nopat,
              'terminal_reinvestment': terminal_reinvestment, 'terminal_fcff': terminal_fcff,
              'terminal_reinvestment_rate': scenario['terminal_growth'] / scenario['terminal_roic'],
              'terminal_enterprise_value': terminal_value, 'cashflows': rows}
    # Reject numeric overflow rather than serialize Infinity or silently clamp it.
    numbers = [value for value in result.values() if isinstance(value, (int, float))]
    numbers.extend(value for row in rows for value in row.values())
    if not all(math.isfinite(value) for value in numbers):
        raise ValueError('Valuation arithmetic overflowed; review units and input magnitudes')
    return result


def _sensitivities(actuals, base):
    rows = []
    for wacc in sorted({round(base['wacc'] + offset, 10) for offset in (-.01, 0, .01)}):
        for growth in sorted({round(base['terminal_growth'] + offset, 10) for offset in (-.005, 0, .005)}):
            if not (0 < wacc < 1 and -.1 <= growth <= .15 and growth < wacc and growth <= base['terminal_roic']):
                continue
            changed = dict(base, wacc=wacc, terminal_growth=growth)
            result = _project(actuals, changed)
            rows.append({'wacc': wacc, 'terminal_growth': growth,
                         'per_share_value': result['per_share_value'], 'enterprise_value': result['enterprise_value']})
    return rows


def _reverse_growth(actuals, base, target):
    # Do not assume that growth creates value: a low-return company can destroy it.
    # Find every bracket in a bounded grid and report multiple solutions honestly.
    def value(growth):
        changed = dict(base, growth_rates=[growth] * len(base['growth_rates']))
        return _project(actuals, changed)['per_share_value'] - target
    samples = [(-.5 + index / 100, None) for index in range(101)]
    samples = [(growth, value(growth)) for growth, _ in samples]
    roots = []
    tolerance = max(1, abs(target)) * 1e-10
    for growth, residual in samples:
        if abs(residual) <= tolerance and not any(abs(growth - root) < 1e-7 for root in roots):
            roots.append(growth)
    for (left, fl), (right, fr) in zip(samples, samples[1:]):
        if fl * fr >= 0:
            continue
        for _ in range(70):
            middle = (left + right) / 2
            fm = value(middle)
            if abs(fm) <= tolerance:
                left = right = middle
                break
            if fl * fm <= 0:
                right = middle
            else:
                left, fl = middle, fm
        root = (left + right) / 2
        if not any(abs(root - previous) < 1e-7 for previous in roots):
            roots.append(root)
    roots.sort()
    projections = []
    for growth in roots:
        projection = _project(actuals, dict(base, growth_rates=[growth] * len(base['growth_rates'])))
        projection.update(constant_annual_growth=growth,
                          revenue_path=_revenue_path(actuals, projection['cashflows']))
        projections.append(projection)
    return {'status': 'solved' if roots else 'not_bracketed',
            'constant_annual_growth_solutions': roots,
            'solution_projections': projections,
            'search_range': [-.5, .5], 'target_market_price': target,
            'interpretation': 'Constant forecast revenue growth with base margins, capital efficiency, WACC and terminal assumptions fixed; not a forecast. Multiple solutions may exist.'}


def _revenue_path(actuals, cashflows):
    previous = actuals['revenue']
    rows = []
    for row in cashflows:
        rows.append({'year': row['year'], 'revenue': row['revenue'],
                     'revenue_increment': row['revenue'] - previous})
        previous = row['revenue']
    return rows


def _counterfactuals(plan, actuals, definitions):
    """Validate and project optional base overrides without changing house cases."""
    requested = plan.get('counterfactuals', {})
    if not isinstance(requested, dict) or len(requested) > MAX_COUNTERFACTUALS:
        raise ValueError(f'counterfactuals must be an object with at most {MAX_COUNTERFACTUALS} cases')
    results = {}
    base = definitions['base']
    for name, overrides in requested.items():
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', name):
            raise ValueError('counterfactual names must be 1–64 letters, digits, underscores, dots or hyphens, starting with a letter or digit')
        if (not isinstance(overrides, dict) or not overrides or
                any(key not in FORECAST_FIELDS for key in overrides)):
            raise ValueError(f'counterfactual {name}: provide only nonempty forecast overrides from {", ".join(FORECAST_FIELDS)}; actuals and probabilities cannot change')
        for field in ('growth_rates', 'ebit_margins'):
            if field in overrides and (not isinstance(overrides[field], list) or
                                      len(overrides[field]) != len(base['growth_rates'])):
                raise ValueError(f'counterfactual {name}: {field} must retain the base forecast horizon')
        # Reuse the same financial validation as house scenarios. No evaluate()
        # recursion or probability substitution is involved.
        changed = dict(base, **overrides)
        trial = dict(plan, scenarios=dict(definitions, base=changed))
        _, validated, _ = _validated(trial)
        effective = validated['base']
        projection = _project(actuals, effective)
        projection.update(
            basis='Conditional base-scenario counterfactual, not an observed market input, forecast or replacement for the house scenarios.',
            overrides=copy.deepcopy(overrides),
            effective_inputs={field: copy.deepcopy(effective[field]) for field in FORECAST_FIELDS},
            fixed_input_basis={
                'reference_scenario': 'base',
                'actuals': copy.deepcopy(actuals),
                'unchanged_base_inputs': {field: copy.deepcopy(base[field]) for field in FORECAST_FIELDS if field not in overrides},
                'house_scenarios_and_probabilities_unchanged': True,
                'note': 'Only the listed forecast overrides change; actuals, the equity bridge, shares, currency and all unlisted base forecast inputs stay fixed. This case has no scenario probability and does not enter the weighted value.'},
            revenue_path=_revenue_path(actuals, projection['cashflows']))
        results[name] = projection
    return results


def _finite_or_none(value):
    return value if math.isfinite(value) else None


def _value_change(value, baseline):
    difference = _finite_or_none(value - baseline)
    percent = (_finite_or_none(difference / abs(baseline) * 100)
               if baseline and difference is not None else None)
    return {'dollar_delta': difference, 'percent_delta': percent}


def _computed_diagnostics(actuals, definitions, scenarios, weighted):
    """Conditional numerical comparisons; never silently replace house inputs.

    The signed percentage change divides by the absolute baseline value. It is
    undefined at a zero baseline. Invalid or overflowing alternatives are marked
    not_calculated rather than breaking an otherwise valid original valuation.
    """
    base = definitions['base']
    baseline = scenarios['base']['per_share_value']
    wacc_changes = []
    for shift in (-.01, .01):
        wacc = round(base['wacc'] + shift, 10)
        row = {'wacc': wacc, 'wacc_change_percentage_points': shift * 100,
               'per_share_value': None, 'dollar_delta': None, 'percent_delta': None}
        if not 0 < wacc < 1 or wacc <= base['terminal_growth']:
            row.update(status='not_calculated', reason='Alternative WACC must be positive, below 100%, and above terminal growth.')
        else:
            try:
                value = _project(actuals, dict(base, wacc=wacc))['per_share_value']
                row.update(status='calculated', per_share_value=value, **_value_change(value, baseline))
            except (ValueError, OverflowError):
                row.update(status='not_calculated', reason='Alternative arithmetic exceeded finite numeric bounds.')
        wacc_changes.append(row)
    tax_changes = []
    for tax in (.20, .24):
        row = {'tax_rate': tax, 'weighted_per_share_value': None,
               'dollar_delta': None, 'percent_delta': None}
        try:
            values = {name: _project(actuals, dict(definitions[name], tax_rate=tax))['per_share_value']
                      for name in SCENARIOS}
            value = sum(values[name] * definitions[name]['probability'] for name in SCENARIOS)
            if not math.isfinite(value):
                raise ValueError('Nonfinite alternative value')
            row.update(status='calculated', weighted_per_share_value=value,
                       scenario_per_share_values=values, **_value_change(value, weighted))
        except (ValueError, OverflowError):
            row.update(status='not_calculated', reason='Alternative arithmetic exceeded finite numeric bounds.')
        tax_changes.append(row)
    transition = {}
    for name in SCENARIOS:
        inputs, result = definitions[name], scenarios[name]
        last = result['cashflows'][-1]
        margin = inputs['ebit_margins'][-1] * (1 - inputs['tax_rate'])
        continued_reinvestment = _finite_or_none((result['terminal_revenue'] - last['revenue']) / inputs['sales_to_capital'])
        equivalent_capital = (_finite_or_none(inputs['terminal_roic'] / (margin * (1 + inputs['terminal_growth']))) if margin else None)
        step = (_finite_or_none(result['terminal_reinvestment'] - continued_reinvestment)
                if continued_reinvestment is not None else None)
        fcff_delta = _value_change(result['terminal_fcff'], last['fcff'])
        alternative_price = None
        if continued_reinvestment is not None:
            terminal_fcff = result['terminal_nopat'] - continued_reinvestment
            terminal_pv = terminal_fcff / (inputs['wacc'] - inputs['terminal_growth']) / (1 + inputs['wacc']) ** len(result['cashflows'])
            equity = result['pv_forecast_fcff'] + terminal_pv - actuals['net_debt'] - actuals['other_equity_claims']
            alternative_price = _finite_or_none(equity / actuals['shares_outstanding'])
        transition[name] = {
            'basis': 'Conditional forecast-to-terminal bridge; no observed return or capital-efficiency measurement is asserted.',
            'last_forecast_fcff': last['fcff'], 'terminal_fcff': result['terminal_fcff'],
            'fcff_step_currency_units': fcff_delta['dollar_delta'],
            'fcff_step_percent': fcff_delta['percent_delta'],
            'implied_forecast_incremental_roic': _finite_or_none(margin * inputs['sales_to_capital']),
            'implied_forecast_incremental_roic_basis': 'Final forecast after-tax EBIT margin times forecast sales-to-capital; a constant-margin incremental-return identity, not measured historical ROIC or a claim about changing-margin incremental returns.',
            'terminal_roic': inputs['terminal_roic'],
            'forecast_sales_to_capital': inputs['sales_to_capital'],
            'equivalent_terminal_sales_to_capital': equivalent_capital,
            'terminal_roic_required_for_continuing_forecast_sales_to_capital': _finite_or_none(margin * inputs['sales_to_capital'] * (1 + inputs['terminal_growth'])),
            'reinvestment_timing_note': 'Forecast reinvestment uses the current-year revenue increase; terminal reinvestment uses next-year NOPAT times growth/ROIC. Thus cash-flow-equivalent terminal sales-to-capital is ROIC / [after-tax margin * (1 + terminal growth)].',
            'terminal_reinvestment': result['terminal_reinvestment'],
            'terminal_reinvestment_if_forecast_sales_to_capital_continued': continued_reinvestment,
            'reinvestment_step_vs_continuing_forecast_sales_to_capital': step,
            'alternative_per_share_value_if_forecast_sales_to_capital_continued': alternative_price,
            'alternative_value_basis': 'Only terminal reinvestment changes to incremental terminal revenue / forecast sales-to-capital; forecast cash flows, WACC, terminal growth, terminal NOPAT, net debt, claims and shares stay fixed.'}
    return {
        'basis': 'Computed, conditional house-assumption diagnostics; these are not observed market inputs, forecasts or automatic assumption corrections.',
        'delta_unit': 'result currency units per share',
        'percentage_delta_basis': 'Signed value difference / absolute baseline value * 100; null for a zero baseline or nonfinite comparison.',
        'base_wacc': {'baseline_wacc': base['wacc'], 'baseline_per_share_value': baseline,
                      'fixed_assumptions': 'All other base-case forecast and terminal inputs, net debt, claims and shares are fixed.',
                      'changes': wacc_changes},
        'uniform_tax': {'baseline_weighted_per_share_value': weighted,
                        'baseline_scenario_tax_rates': {name: definitions[name]['tax_rate'] for name in SCENARIOS},
                        'fixed_assumptions': 'Replace every scenario cash operating tax rate with the stated alternative; all other scenario inputs, probability weights, net debt, claims and shares stay fixed.',
                        'changes': tax_changes},
        'terminal_transition': transition}


def evaluate(plan):
    """Recompute all scenarios, bridge enterprise to equity, and return results."""
    actuals, definitions, market = _validated(plan)
    counterfactuals = _counterfactuals(plan, actuals, definitions)
    scenarios = {}
    for name in SCENARIOS:
        result = _project(actuals, definitions[name])
        result['probability'] = definitions[name]['probability']
        if market is not None:
            result['upside_percent'] = (result['per_share_value'] / market - 1) * 100
        scenarios[name] = result
    weighted = sum(result['per_share_value'] * result['probability'] for result in scenarios.values())
    result = {'model': 'unlevered-fcff-incremental-sales-to-capital-v1',
              'currency': plan['currency'], 'amount_unit': 'currency_units', 'share_unit': 'shares',
              'assumptions_label': plan['assumptions_label'], 'source_refs': copy.deepcopy(plan['source_refs']),
              'scenarios': scenarios, 'weighted_per_share_value': weighted,
              'sensitivity': _sensitivities(actuals, definitions['base']),
              'computed_diagnostics': _computed_diagnostics(actuals, definitions, scenarios, weighted),
              'disclosures': [
                  'Forecasts and scenario weights are explicit house assumptions, not reported facts.',
                  'Cash operating taxes are zero on losses; no NOL benefit is assumed.',
                  'Net debt includes the selected cash balance and is subtracted once. Cash is not added again.',
                  'Incremental sales-to-capital represents net operating reinvestment, including working capital. Maintenance investment is assumed to offset depreciation.',
                  'Terminal growth consumes reinvestment at terminal ROIC; it is not free growth.',
                  'Negative cash flows and negative equity values are preserved; no artificial value floor is applied.',
                  'Source references establish traceability only; the caller must verify financial definitions, dates and accounting support.'
              ]}
    if 'valuation_date' in plan:
        result['valuation_date'] = plan['valuation_date']
    if 'counterfactuals' in plan:
        result['counterfactuals'] = counterfactuals
    if market is not None:
        result['market_price'] = market
        result['weighted_upside_percent'] = (weighted / market - 1) * 100
        result['reverse_dcf'] = _reverse_growth(actuals, definitions['base'], market)
    return result


def save_valuation(folder, assumptions, results):
    """Write editable assumptions and independently computed JSON/CSV artifacts."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    paths = {name: folder / name for name in ('assumptions.json', 'valuation.json', 'cashflows.csv', 'sensitivity.csv')}
    for name, data in (('assumptions.json', assumptions), ('valuation.json', results)):
        paths[name].write_text(json.dumps(data, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    columns = ('scenario', 'year', 'revenue', 'revenue_growth', 'ebit_margin', 'ebit',
               'cash_operating_tax', 'nopat', 'net_reinvestment', 'fcff', 'discount_factor', 'pv_fcff')
    with paths['cashflows.csv'].open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for name in SCENARIOS:
            for row in results['scenarios'][name]['cashflows']:
                writer.writerow(dict(row, scenario=name))
    with paths['sensitivity.csv'].open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=('wacc', 'terminal_growth', 'per_share_value', 'enterprise_value'))
        writer.writeheader()
        writer.writerows(results['sensitivity'])
    return {name: str(path) for name, path in paths.items()}


def main():
    """Recompute an edited assumptions file without models, network, or credentials."""
    import argparse
    import sys
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('assumptions', type=Path)
    parser.add_argument('--output', type=Path, help='Output directory; defaults to the assumptions file directory')
    args = parser.parse_args()
    try:
        assumptions = json.loads(args.assumptions.read_text(encoding='utf-8'))
        results = evaluate(assumptions)
        paths = save_valuation(args.output or args.assumptions.parent, assumptions, results)
        print(f"{results['currency']} {results['weighted_per_share_value']:.2f} per share (scenario weighted)")
        print(paths['valuation.json'])
    except (ValueError, TypeError, OSError, KeyError) as exc:
        print(f'Valuation stopped: {exc}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

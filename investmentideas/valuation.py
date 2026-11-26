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

Computed ``valuation_multiples`` diagnostics show entry EV/first-forecast-year
earnings, original DCF exit multiples and conditional returns at an unchanged
entry multiple. They use the existing integer-year, debt/claims-settled FCFF
convention, not a forecast of common-share payouts or trading multiples.

``entry_policy`` is evaluated only so plans saved before its removal reproduce.
The research pipeline drops it from every plan: this project values companies and
sets no entry prices. Its fields are basis (base or weighted), margin_of_safety,
label and rationale; the conditional price is reference value * (1 - margin_of_safety).

Optional ``repurchase_tests`` is a list of at most eight independent one-period
cash-funded illustrations. Each requires name, amount, price, source_refs, label
and interpretation (historical_retrospective_conditional or
forward_conditional_illustration). They require a reconciled net_debt_bridge with
debt and selected_cash_and_nonoperating_investments; spending cannot exceed that
cash. Each case reduces cash and shares once, leaving operating values unchanged.
Historical prices are compared with current house values, not historical fair
values; forward illustrations are not future repurchase forecasts.

Market-consistent conventions (v2, opt-in). The implied equity risk premium is
solved from the market index under specific assumptions, so a valuation using it
must use the same market-level conventions or it values the index itself away
from its price. ``discount_rate.term_structure = "glide_to_market_v1"`` holds the
current WACC for forecast years 1-5 and moves it linearly to a terminal cost of
capital by year 10 (the market's operating-asset beta relevered at the firm's own
leverage; see rates.py). Forecasts shorter than 10 years are extended to year 10
with steady-state years (terminal growth, final margin, terminal tax, reinvestment
= NOPAT * g / ROIC); longer forecasts hold the initial rate to year N - 5. In this
mode terminal growth defaults to the same-date risk-free rate (the ERP's own
long-run growth), may not exceed it, and needs ``terminal_growth_basis`` when
lower; a firm whose final forecast growth is below 1% or whose terminal ROIC is
below the risk-free rate must state terminal growth. ``valuation_timing =
{"convention": "stub_v1", "anchor_date": <fiscal year end>}`` dates the present
value at ``valuation_date``: year-one cash flow counts only for the unexpired part
of the year, as in Damodaran's implied-ERP calculator, and every flow is discounted
from the valuation date. Optional scenario ``terminal_tax_rate`` applies to
steady-state years and the terminal value. Plans without these fields keep the
original constant-rate, integer-year conventions and reproduce exactly.

When net_debt_bridge.finance_leases_included_in_net_debt is true, its debt is a
combined total. Same-date rate inputs split that total using separately reported
finance leases. If that split is unavailable, financial_debt must declare
allocation="combined_debt_including_finance_leases" and finance_lease_debt must
declare allocation="included_in_financial_debt" with value zero. Their labels
must describe the combined total and zero allocation. Zero then allocates a
combined amount once; it does not assert that finance lease liabilities are zero.
"""
from __future__ import annotations

import copy
import csv
from datetime import date
import json
import math
from pathlib import Path
import re

from .operating import apply as apply_operating, derive_counterfactual
from .rates import calculate_discount_rate
from .market_beta import operating_asset_beta

SCENARIOS = ('bear', 'base', 'bull')
ACTUAL_FIELDS = ('revenue', 'net_debt', 'other_equity_claims', 'shares_outstanding')
FORECAST_FIELDS = ('growth_rates', 'ebit_margins', 'tax_rate', 'sales_to_capital',
                   'wacc', 'terminal_growth', 'terminal_roic')
# Overridable alongside FORECAST_FIELDS; absent unless stated, so that a changed
# tax_rate still reaches steady-state years and the terminal value.
OPTIONAL_FORECAST_FIELDS = ('terminal_tax_rate',)
MAX_COUNTERFACTUALS = 8
GLIDE = 'glide_to_market_v1'
GLIDE_HOLD_YEARS = 5
GLIDE_END_YEAR = 10
# Terminal value is r - g away from infinity. Below one point it is rejected;
# below two points results are reported but flagged as fragile.
MIN_TERMINAL_SPREAD = .01
FRAGILE_TERMINAL_SPREAD = .02
GROWTH_CAP_TOLERANCE = 1e-6


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
    discounting = plan.get('_discounting') or {'convention': 'constant'}
    glide = discounting['convention'] == GLIDE
    timing = plan.get('_timing') or {'stub_years': 0.0}
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
        rate_keys = () if glide else ('wacc',)
        values = {key: _number(_required(item, key, name), name + '.' + key)
                  for key in ('probability', 'tax_rate', 'sales_to_capital', *rate_keys, 'terminal_roic')}
        values['growth_rates'] = [_number(value, name + '.growth_rates') for value in growth]
        values['ebit_margins'] = [_number(value, name + '.ebit_margins') for value in margins]
        if glide and item.get('wacc') is not None:
            raise ValueError(f'{name}.wacc must be omitted: the dated discount-rate path governs every year')
        if item.get('terminal_tax_rate') is not None:
            values['terminal_tax_rate'] = _number(item['terminal_tax_rate'], name + '.terminal_tax_rate')
        if not 0 <= values['probability'] <= 1:
            raise ValueError(f'{name}: probability must be between zero and one')
        for key in ('tax_rate', 'terminal_tax_rate'):
            if key in values and not 0 <= values[key] <= .6:
                raise ValueError(f'{name}: cash operating {key} must be between 0 and 0.60')
        if values['sales_to_capital'] <= 0:
            raise ValueError(f'{name}: sales_to_capital must be positive')
        if glide:
            rf = discounting['risk_free_rate']
            stated = item.get('terminal_growth')
            if stated is None:
                if values['growth_rates'][-1] < .01 or values['terminal_roic'] < rf:
                    raise ValueError(f'{name}: state terminal_growth with terminal_growth_basis; the risk-free default '
                                     'does not apply when final forecast growth is below 1% or terminal ROIC is below the risk-free rate')
                values['terminal_growth'], values['terminal_growth_source'] = rf, 'risk_free_rate'
            else:
                values['terminal_growth'] = _number(stated, name + '.terminal_growth')
                values['terminal_growth_source'] = 'stated'
                if values['terminal_growth'] > rf + GROWTH_CAP_TOLERANCE:
                    raise ValueError(f'{name}: terminal growth {values["terminal_growth"]:.2%} exceeds the risk-free rate '
                                     f'{rf:.2%}, which is the long-run growth the equity risk premium assumes')
                if values['terminal_growth'] < rf - GROWTH_CAP_TOLERANCE:
                    basis = item.get('terminal_growth_basis')
                    if not isinstance(basis, str) or not basis.strip():
                        raise ValueError(f'{name}: terminal growth below the risk-free rate needs a company-specific '
                                         'terminal_growth_basis; the equity risk premium assumes the market grows at that rate')
                    values['terminal_growth_basis'] = basis.strip()
            values.update(convention=GLIDE, initial_rate=discounting['initial'], terminal_rate=discounting['terminal'],
                          risk_free_rate=rf)
            terminal_rate = values['terminal_rate']
        else:
            values['terminal_growth'] = _number(_required(item, 'terminal_growth', name), name + '.terminal_growth')
            values['convention'] = 'constant'
            if not 0 < values['wacc'] < 1:
                raise ValueError(f'{name}: wacc must be between zero and one')
            terminal_rate = values['wacc']
        values['stub_years'] = timing['stub_years']
        if not -.10 <= values['terminal_growth'] <= .15 or terminal_rate <= values['terminal_growth']:
            raise ValueError(f'{name}: terminal growth must be between -10% and 15% and strictly below the terminal discount rate')
        if glide and terminal_rate - values['terminal_growth'] < MIN_TERMINAL_SPREAD:
            raise ValueError(f'{name}: terminal discount rate {terminal_rate:.2%} is within one point of terminal growth; '
                             'the terminal value is not meaningful')
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


def _horizon(scenario):
    explicit = len(scenario['growth_rates'])
    return max(explicit, GLIDE_END_YEAR) if scenario.get('convention') == GLIDE else explicit


def _rate_path(scenario, *, flat=None, shift=0.0, terminal=None):
    """(annual rates, terminal rate) for a validated scenario: the one rate source.

    ``flat`` applies one rate in every year and the terminal value; ``shift``
    moves the whole path; ``terminal`` replaces the glide target.
    """
    horizon = _horizon(scenario)
    if flat is not None:
        return [flat] * horizon, flat
    if scenario.get('convention') != GLIDE:
        if 'wacc' not in scenario or terminal is not None:
            raise ValueError('A constant-rate scenario requires its wacc and has no separate terminal rate')
        rate = scenario['wacc'] + shift
        return [rate] * horizon, rate
    initial = scenario['initial_rate'] + shift
    final = (scenario['terminal_rate'] if terminal is None else terminal) + shift
    hold = max(GLIDE_HOLD_YEARS, horizon - (GLIDE_END_YEAR - GLIDE_HOLD_YEARS))
    span = horizon - hold
    return [initial if year <= hold else initial + (final - initial) * (year - hold) / span
            for year in range(1, horizon + 1)], final


def _project(actuals, scenario, *, flat=None, shift=0.0, terminal=None):
    if scenario.get('convention') == GLIDE and 'wacc' in scenario:
        raise ValueError('Internal error: a glide scenario cannot carry a scalar wacc')
    rates, terminal_rate = _rate_path(scenario, flat=flat, shift=shift, terminal=terminal)
    if not all(0 < rate < 1 for rate in rates) or terminal_rate <= scenario['terminal_growth']:
        raise ValueError('Discount rates must be between zero and one and the terminal rate above terminal growth')
    stub = scenario.get('stub_years', 0.0)
    versioned = scenario.get('convention') == GLIDE or stub > 0
    explicit = len(scenario['growth_rates'])
    revenue = actuals['revenue']
    rows = []
    cumulative = 1.0
    roll = (1 + rates[0]) ** stub
    for year in range(1, len(rates) + 1):
        previous = revenue
        if year <= explicit:
            growth, margin, tax = scenario['growth_rates'][year - 1], scenario['ebit_margins'][year - 1], scenario['tax_rate']
        else:
            growth, margin, tax = scenario['terminal_growth'], scenario['ebit_margins'][-1], scenario.get('terminal_tax_rate', scenario['tax_rate'])
        revenue *= 1 + growth
        ebit = revenue * margin
        cash_tax = max(ebit, 0) * tax
        nopat = ebit - cash_tax
        if year <= explicit:
            reinvestment = (revenue - previous) / scenario['sales_to_capital']
        else:
            reinvestment = nopat * scenario['terminal_growth'] / scenario['terminal_roic']
        fcff = nopat - reinvestment
        cumulative *= 1 + rates[year - 1]
        counted = 1 - stub if year == 1 else 1.0
        if versioned:
            factor, present = roll / cumulative, fcff * counted * roll / cumulative
        else:
            # Constant-rate, integer-year plans keep the original arithmetic bit for bit.
            discount = (1 + rates[0]) ** year
            factor, present = 1 / discount, fcff / discount
        row = {'year': year, 'revenue': revenue, 'revenue_growth': growth,
               'ebit_margin': margin, 'ebit': ebit, 'cash_operating_tax': cash_tax,
               'nopat': nopat, 'net_reinvestment': reinvestment, 'fcff': fcff,
               'discount_factor': factor, 'pv_fcff': present}
        if versioned:
            row.update(phase='explicit' if year <= explicit else 'steady_state',
                       discount_rate=rates[year - 1], discount_time=year - stub, counted_fraction=counted)
        rows.append(row)
    terminal_tax = scenario.get('terminal_tax_rate', scenario['tax_rate'])
    terminal_revenue = revenue * (1 + scenario['terminal_growth'])
    terminal_ebit = terminal_revenue * scenario['ebit_margins'][-1]
    terminal_nopat = terminal_ebit * (1 - terminal_tax)
    terminal_reinvestment = terminal_nopat * scenario['terminal_growth'] / scenario['terminal_roic']
    terminal_fcff = terminal_nopat - terminal_reinvestment
    terminal_value = terminal_fcff / (terminal_rate - scenario['terminal_growth'])
    pv_terminal = terminal_value * roll / cumulative if versioned else terminal_value / (1 + rates[0]) ** len(rows)
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
    if versioned:
        result.update(initial_rate=rates[0], terminal_rate=terminal_rate,
                      terminal_spread=terminal_rate - scenario['terminal_growth'],
                      terminal_growth=scenario['terminal_growth'], stub_years=stub,
                      explicit_forecast_years=explicit, steady_state_years=len(rates) - explicit)
    # Reject numeric overflow rather than serialize Infinity or silently clamp it.
    numbers = [value for value in result.values() if isinstance(value, (int, float))]
    numbers.extend(value for row in rows for value in row.values() if isinstance(value, (int, float)))
    if not all(math.isfinite(value) for value in numbers):
        raise ValueError('Valuation arithmetic overflowed; review units and input magnitudes')
    return result


def _sensitivities(actuals, base):
    if base.get('convention') == GLIDE:
        rows = []
        cap = base['risk_free_rate']
        for shift in (-.01, 0.0, .01):
            for growth in sorted({round(min(base['terminal_growth'] + offset, cap), 10) for offset in (-.01, -.005, 0)}):
                rates, terminal = _rate_path(dict(base, terminal_growth=growth), shift=shift)
                row = {'rate_shift': shift, 'initial_rate': rates[0], 'terminal_rate': terminal,
                       'terminal_growth': growth, 'per_share_value': None, 'enterprise_value': None}
                if terminal - growth < MIN_TERMINAL_SPREAD or growth > base['terminal_roic'] or rates[0] <= 0:
                    row['status'] = 'not_meaningful'
                else:
                    result = _project(actuals, dict(base, terminal_growth=growth), shift=shift)
                    row.update(per_share_value=result['per_share_value'], enterprise_value=result['enterprise_value'],
                               status='fragile_spread' if terminal - growth < FRAGILE_TERMINAL_SPREAD else 'calculated')
                rows.append(row)
        return rows
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


def _reverse_wacc(actuals, base, target, calibration=None):
    """Conditional rate solves, not an observation of investor expectations."""
    low, high = max(.0001, base['terminal_growth'] + .0001), .50
    def residual(rate):
        return _project(actuals, dict(base, wacc=rate))['per_share_value'] - target
    samples = [(low + (high-low)*index/100, None) for index in range(101)]
    samples = [(rate, residual(rate)) for rate, _ in samples]
    tolerance = max(1, abs(target))*1e-10
    roots = []
    for rate, error in samples:
        if abs(error) <= tolerance:
            roots.append(rate)
    for (left, fl), (right, fr) in zip(samples, samples[1:]):
        if fl*fr >= 0:
            continue
        for _ in range(70):
            middle = (left+right)/2
            fm = residual(middle)
            if abs(fm) <= tolerance:
                left = right = middle
                break
            if fl*fm <= 0:
                right = middle
            else:
                left, fl = middle, fm
        rate = (left+right)/2
        if not any(abs(rate-found)<1e-9 for found in roots):
            roots.append(rate)
    solutions = []
    for rate in sorted(roots):
        projection = _project(actuals, dict(base, wacc=rate))
        row = {'wacc':rate, 'per_share_value':projection['per_share_value'],
               'terminal_value_fraction':projection['terminal_value_fraction']}
        if calibration is not None:
            values = calibration['provenance']
            premium = values['equity_risk_premium']['value']
            equity_weight = calibration['equity_weight']
            if premium and equity_weight:
                equity_cost = (rate-calibration['debt_weight']*calibration['after_tax_cost_of_debt'])/equity_weight
                beta = (equity_cost-values['risk_free_rate']['value'])/premium
                row['conditional_equity_beta'] = beta
                row['conditional_unlevered_beta'] = beta/(1+(1-values['marginal_tax_rate']['value'])*
                    calibration['included_debt']/calibration['market_equity'])
        solutions.append(row)
    return {'status':'solved' if solutions else 'not_bracketed', 'search_range':[low,high],
            'target_market_price':target, 'solutions':solutions,
            'fixed_assumptions':'All base operating, tax, reinvestment, terminal growth/ROIC and equity-bridge inputs stay fixed. Only a constant WACC changes.',
            'interpretation':'Conditional discount-rate solutions and, where available, algebraically equivalent betas using the supplied financing weights and rate/ERP inputs. They are not measured issuer betas, a unique market belief or an approved discount-rate calibration.'}


def _cashflow_irr(cashflows, times=None):
    low, high = -.95, 1.0
    times = list(range(len(cashflows))) if times is None else times
    def npv(rate):
        try:
            value = math.fsum(amount/(1+rate)**time for time,amount in zip(times,cashflows))
            return value if math.isfinite(value) else None
        except (OverflowError, ZeroDivisionError, ValueError):
            return None
    samples = [(low+(high-low)*index/200,None) for index in range(201)]
    samples = [(rate,npv(rate)) for rate,_ in samples]
    tolerance = max(1,abs(cashflows[0]))*1e-10
    roots = [rate for rate,error in samples if error is not None and abs(error)<=tolerance]
    for (left,fl),(right,fr) in zip(samples,samples[1:]):
        if fl is None or fr is None or fl*fr>=0:
            continue
        for _ in range(70):
            middle=(left+right)/2; fm=npv(middle)
            if fm is None:
                break
            if abs(fm)<=tolerance:
                left=right=middle;break
            if fl*fm<=0:
                right=middle
            else:
                left,fl=middle,fm
        root=(left+right)/2
        if not any(abs(root-prior)<1e-8 for prior in roots):
            roots.append(root)
    return {'status':'solved' if roots else 'not_bracketed',
            'annual_return_solutions':sorted(roots),'search_range':[low,high],
            'nonfinite_grid_points_skipped':sum(error is None for _,error in samples)}


def _fixed_exit_return(actuals, projection, price):
    shares=actuals['shares_outstanding']
    opening=-(actuals['net_debt']+actuals['other_equity_claims'])/shares
    rows=projection['cashflows']
    cashflows=[-price+opening]+[row['fcff']*row.get('counted_fraction',1.0)/shares for row in rows]
    times=[0.0]+[row.get('discount_time',row['year']) for row in rows]
    exit_value=projection['terminal_enterprise_value']/shares
    cashflows[-1]+=exit_value
    solved=_cashflow_irr(cashflows,times)
    versioned='discount_time' in rows[0]
    solved.update({'entry_price':price,'opening_bridge_distribution_per_share':opening,
                   'terminal_enterprise_value_per_share':exit_value,'cashflows_per_share':cashflows,
                   'basis':('Illustrative debt/claims-settled cash stream: the signed opening net-asset bridge is distributed (or funded) immediately; each year\'s unlevered FCFF is paid at its discount time (under a valuation-date stub the first year counts only its unexpired fraction and later flows fall at year k minus the stub); enterprise value is sold at the final model year, a steady-state year when the forecast was extended, at the ORIGINAL scenario terminal formula. Exit value is fixed while solving the return. No actual payout, refinancing, tax or stock-price-return forecast is established.'
                            if versioned else
                            'Illustrative debt/claims-settled cash stream: the signed opening net-asset bridge is distributed (or funded) immediately; all unlevered FCFF is paid annually; enterprise value is sold in the last explicit year at the ORIGINAL scenario terminal formula. Exit value is fixed while solving the return. No actual payout, refinancing, tax or stock-price-return forecast is established.')})
    return solved


def _positive_multiple(numerator, denominator):
    """A conventional positive EV/earnings multiple, with explicit limits."""
    result = {'numerator': numerator, 'denominator': denominator, 'value': None}
    if numerator is None or denominator is None:
        return dict(result, status='unavailable', reason='A comparable numerator or denominator is unavailable.')
    if not math.isfinite(numerator) or not math.isfinite(denominator):
        return dict(result, status='not_calculated', reason='Inputs exceed finite numeric bounds.')
    if numerator <= 0 or denominator <= 0:
        return dict(result, status='not_meaningful', reason='A conventional EV/earnings multiple requires positive enterprise value and positive earnings; signed amounts are retained.')
    value = _finite_or_none(numerator / denominator)
    if value is None:
        return dict(result, status='not_calculated', reason='The ratio exceeds finite numeric bounds.')
    return dict(result, status='calculated', value=value)


def _forecast_period(plan, index, row=None):
    result = {'model_year': index + 1, 'discount_time_years': (row or {}).get('discount_time', index + 1)}
    if row and 'phase' in row:
        result['phase'] = row['phase']
    periods = plan.get('forecast_periods')
    if isinstance(periods, list) and index < len(periods) and isinstance(periods[index], dict):
        result['supplied_period_metadata'] = copy.deepcopy(periods[index])
    return result


def _unchanged_multiple_return(actuals, projection, price, metric, multiple):
    """Keep operations fixed and apply a like-period entry multiple at exit."""
    final_earnings = projection['cashflows'][-1][metric]
    last_phase = projection['cashflows'][-1].get('phase')
    result = {'entry_multiple': copy.deepcopy(multiple), 'earnings_metric': metric,
              'entry_earnings_period': 'first_explicit_forecast_year',
              'exit_earnings_period': 'final_steady_state_year' if last_phase == 'steady_state' else 'last_explicit_forecast_year',
              'final_forecast_earnings': final_earnings,
              'original_dcf_terminal_enterprise_value': projection['terminal_enterprise_value'],
              'conditional_exit_enterprise_value': None}
    if multiple['status'] != 'calculated':
        return dict(result, status=multiple['status'], reason=multiple['reason'])
    if final_earnings <= 0:
        return dict(result, status='not_meaningful', reason='Applying a conventional positive entry multiple requires positive final-year earnings.')
    exit_value = _finite_or_none(multiple['value'] * final_earnings)
    if exit_value is None:
        return dict(result, status='not_calculated', reason='Conditional exit arithmetic exceeds finite numeric bounds.')
    alternative = dict(projection, terminal_enterprise_value=exit_value)
    solved = _fixed_exit_return(actuals, alternative, price)
    if 'discount_time' in projection['cashflows'][0]:
        solved['basis'] = (
            'Conditional debt/claims-settled owner stream at the saved market price: the signed opening '
            'net-debt/other-claims bridge is settled immediately, each year\'s unlevered FCFF is distributed at its '
            'discount time (under a valuation-date stub the first year counts only its unexpired fraction), and '
            'enterprise value is sold at the final model year. The exit equals entry market EV / first-forecast-year '
            'earnings times the same scenario final-model-year earnings (a steady-state year when the forecast was '
            'extended), using the named EBIT or NOPAT metric consistently. Operating cash flows, taxes, reinvestment, '
            'current shares and opening financing stay fixed; no ongoing debt or buyback schedule is assumed. This '
            'alternative exit replaces the original terminal formula only in this return diagnostic; it is not a '
            'common-share payout, refinancing or trading-price forecast.')
        return dict(result, **solved, conditional_exit_enterprise_value=exit_value)
    solved['basis'] = (
        'Conditional debt/claims-settled owner stream at the saved market price: the signed opening '
        'net-debt/other-claims bridge is settled immediately, all unlevered FCFF is distributed at '
        'integer model years 1 through N, and enterprise value is sold at year N. The exit equals '
        'entry market EV / first-forecast-year earnings times the same scenario final-forecast-year '
        'earnings, using the named EBIT or NOPAT metric consistently. Operating cash flows, taxes, '
        'reinvestment, current shares and opening financing stay fixed; no ongoing debt or buyback '
        'schedule is assumed. Supplied fiscal-calendar metadata does not change discount timing. '
        'This alternative exit replaces the original terminal formula only in this return diagnostic; '
        'it is not a common-share payout, refinancing or trading-price forecast.')
    return dict(result, **solved, conditional_exit_enterprise_value=exit_value)


def _valuation_multiples(plan, actuals, scenarios, market):
    shares = actuals['shares_outstanding']
    equity = _finite_or_none(market * shares) if market is not None else None
    enterprise = (_finite_or_none(equity + actuals['net_debt'] + actuals['other_equity_claims'])
                  if equity is not None else None)
    market_bridge = {
        'status': 'unavailable' if market is None else 'calculated' if enterprise is not None else 'not_calculated',
        'market_price': market, 'shares_outstanding': shares,
        'market_equity_value': equity, 'net_debt': actuals['net_debt'],
        'other_equity_claims': actuals['other_equity_claims'], 'enterprise_value': enterprise,
        'source_refs': {key: copy.deepcopy(plan['source_refs'][key]) for key in
                        ('net_debt', 'other_equity_claims', 'shares_outstanding')},
        'basis': 'Saved quote times dated ordinary shares, plus signed net debt and other noncommon claims. Selected cash is already deducted in net debt; it is not deducted twice.',
    }
    if market is not None:
        market_bridge['source_refs']['market_price'] = copy.deepcopy(plan['source_refs']['market_price'])
    if isinstance(plan.get('input_dates'), dict):
        market_bridge['input_dates'] = copy.deepcopy(plan['input_dates'])
    output = {}
    for name, projection in scenarios.items():
        first, last = projection['cashflows'][0], projection['cashflows'][-1]
        entry = {metric: _positive_multiple(enterprise, first[metric]) for metric in ('ebit', 'nopat')}
        output[name] = {
            'forecast_year1': {'period': _forecast_period(plan, 0, first), 'ebit': first['ebit'],
                               'nopat': first['nopat'], 'market_ev_to_ebit': entry['ebit'],
                               'market_ev_to_nopat': entry['nopat']},
            'original_dcf_exit': {
                'period': _forecast_period(plan, len(projection['cashflows']) - 1, last),
                'enterprise_value': projection['terminal_enterprise_value'],
                'final_forecast_ebit': last['ebit'], 'final_forecast_nopat': last['nopat'],
                'terminal_year_nopat': projection['terminal_nopat'],
                'ev_to_final_forecast_ebit': _positive_multiple(projection['terminal_enterprise_value'], last['ebit']),
                'ev_to_final_forecast_nopat': _positive_multiple(projection['terminal_enterprise_value'], last['nopat']),
                'ev_to_terminal_year_nopat': _positive_multiple(projection['terminal_enterprise_value'], projection['terminal_nopat']),
                'basis': ('Original scenario terminal formula, received at the final model year (a steady-state year when the forecast was extended to year 10), at its discount time. Final-model-year earnings and next terminal-year NOPAT are separate denominators.'
                          if 'discount_time' in last else
                          'Original scenario terminal formula, received at the end of its last explicit model year. Final-forecast-year earnings and next terminal-year NOPAT are separate denominators.'),
            },
            'unchanged_entry_multiple_exit_returns': {
                metric: _unchanged_multiple_return(actuals, projection, market, metric, entry[metric])
                for metric in ('ebit', 'nopat')},
        }
    return {
        'market': market_bridge, 'scenarios': output,
        'basis': 'Computed valuation exhibits. Entry multiples use first-explicit-forecast-year earnings, not historical or trailing earnings. Negative or zero EV/earnings are retained as amounts but do not produce conventional multiples. Exit alternatives have no probability weight and do not change the DCF.',
        'discount_timing': ('Annual FCFF discounted from the valuation date: the first year counts only its unexpired fraction and every later flow and the exit fall at year k minus that stub; no midyear adjustment.'
                            if plan.get('_timing', {}).get('stub_years') else
                            'Existing model convention: annual FCFF at integer years 1..N, exit at year N, opening financing bridge at time zero; no stub or midyear adjustment.'),
    }


def _entry_policy(plan, scenarios, weighted, market):
    policy = plan['entry_policy']
    fields = {'basis', 'margin_of_safety', 'label', 'rationale'}
    if not isinstance(policy, dict) or set(policy) != fields:
        raise ValueError('entry_policy requires exactly basis, margin_of_safety, label and rationale')
    if policy['basis'] not in ('base', 'weighted'):
        raise ValueError('entry_policy.basis must be base or weighted')
    for field in ('label', 'rationale'):
        if not isinstance(policy[field], str) or not policy[field].strip():
            raise ValueError(f'entry_policy.{field} must explicitly describe the house rule')
    margin = _number(policy['margin_of_safety'], 'entry_policy.margin_of_safety')
    if not 0 <= margin < 1:
        raise ValueError('entry_policy.margin_of_safety must be at least zero and below one')
    reference = scenarios['base']['per_share_value'] if policy['basis'] == 'base' else weighted
    entry = _finite_or_none(reference * (1 - margin)) if reference > 0 else None
    return {
        'policy': copy.deepcopy(policy), 'reference_per_share_value': reference,
        'status': 'calculated' if entry is not None else 'not_meaningful',
        'conditional_entry_price': entry, 'market_price': market,
        'entry_price_vs_market_percent': ((entry / market - 1) * 100
                                          if entry is not None and market is not None else None),
        'basis': 'Explicit house valuation policy: reference model value per share times (1 - selected margin of safety). No margin is supplied by the engine. Weighted basis uses disclosed scenario probabilities. A positive reference value is required for a meaningful entry price. This is a conditional valuation threshold, not a promised return, trading recommendation or execution guarantee.',
    }


def _repurchase_tests(plan, actuals, scenarios, weighted):
    requested = plan['repurchase_tests']
    if not isinstance(requested, list) or len(requested) > 8:
        raise ValueError('repurchase_tests must contain at most eight independent cases')
    if not requested:
        return []
    bridge = plan.get('net_debt_bridge')
    if not isinstance(bridge, dict):
        raise ValueError('repurchase_tests require an explicit reconciled nonoperating cash bridge')
    if (('currency' in bridge and bridge['currency'] != plan['currency']) or
            ('amount_unit' in bridge and bridge['amount_unit'] != 'currency_units')):
        raise ValueError('Repurchase funding bridge currency and amount units must match the valuation plan')
    cash = _number(_required(bridge, 'selected_cash_and_nonoperating_investments', 'net_debt_bridge'),
                   'net_debt_bridge.selected_cash_and_nonoperating_investments')
    debt = _number(_required(bridge, 'debt', 'net_debt_bridge'), 'net_debt_bridge.debt')
    if cash < 0 or debt < 0 or not math.isclose(debt - cash, actuals['net_debt'], rel_tol=1e-8, abs_tol=1e-6):
        raise ValueError('repurchase_tests require nonnegative cash/debt reconciled to actuals.net_debt')
    if ('net_debt' in bridge and not math.isclose(_number(bridge['net_debt'], 'net_debt_bridge.net_debt'),
                                                  actuals['net_debt'], rel_tol=1e-8, abs_tol=1e-6)):
        raise ValueError('repurchase_tests net_debt_bridge.net_debt conflicts with actuals.net_debt')
    shares, names, output = actuals['shares_outstanding'], set(), []
    fields = {'name', 'amount', 'price', 'source_refs', 'label', 'interpretation'}
    for item in requested:
        if not isinstance(item, dict) or set(item) != fields:
            raise ValueError('Each repurchase test requires exactly name, amount, price, source_refs, label and interpretation')
        name = item['name']
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', name)
                or name in names):
            raise ValueError('Repurchase test names must be unique 1–64 character identifiers')
        names.add(name)
        if not isinstance(item['label'], str) or not item['label'].strip() or not _reference(item['source_refs']):
            raise ValueError('Repurchase tests require an explicit label and source_refs')
        if item['interpretation'] not in ('historical_retrospective_conditional', 'forward_conditional_illustration'):
            raise ValueError('Repurchase interpretation must distinguish historical_retrospective_conditional from forward_conditional_illustration')
        amount = _number(item['amount'], 'repurchase.amount')
        price = _number(item['price'], 'repurchase.price')
        if amount <= 0 or price <= 0 or amount > cash:
            raise ValueError('Repurchase amount and price must be positive; cash-funded amount cannot exceed modeled nonoperating cash')
        retired = amount / price
        remaining = shares - retired
        if not math.isfinite(retired) or remaining <= 0:
            raise ValueError('Repurchase must leave a positive ordinary-share count')
        post_net_debt = actuals['net_debt'] + amount
        cases = {}
        for scenario, projection in scenarios.items():
            equity = projection['enterprise_value'] - post_net_debt - actuals['other_equity_claims']
            per_share = equity / remaining
            transfer = retired * (price - projection['per_share_value'])
            if not all(math.isfinite(value) for value in (post_net_debt, equity, per_share, transfer)):
                raise ValueError('Repurchase arithmetic exceeds finite numeric bounds')
            cases[scenario] = {
                'enterprise_value': projection['enterprise_value'],
                'original_equity_value': projection['equity_value'], 'post_repurchase_equity_value': equity,
                'original_per_share_value': projection['per_share_value'], 'post_repurchase_per_share_value': per_share,
                'value_change': _value_change(per_share, projection['per_share_value']),
                'value_transferred_to_selling_holders_vs_original_house_value': transfer,
            }
        post_weighted = sum(cases[name]['post_repurchase_per_share_value'] * scenarios[name]['probability']
                            for name in SCENARIOS)
        if not math.isfinite(post_weighted):
            raise ValueError('Weighted repurchase arithmetic exceeds finite numeric bounds')
        output.append({
            'name': name, 'label': item['label'], 'source_refs': copy.deepcopy(item['source_refs']),
            'interpretation': item['interpretation'], 'amount': amount, 'price': price,
            'shares_repurchased': retired, 'original_shares': shares, 'post_repurchase_shares': remaining,
            'original_nonoperating_cash': cash, 'post_repurchase_nonoperating_cash': cash - amount,
            'debt_unchanged': debt, 'original_net_debt': actuals['net_debt'], 'post_repurchase_net_debt': post_net_debt,
            'other_equity_claims_unchanged': actuals['other_equity_claims'],
            'funding_bridge': copy.deepcopy(bridge), 'funding_source_refs': copy.deepcopy(plan['source_refs']['net_debt']),
            'scenarios': cases, 'original_weighted_per_share_value': weighted,
            'post_repurchase_weighted_per_share_value': post_weighted,
            'weighted_value_change': _value_change(post_weighted, weighted),
            'basis': 'Independent contemporaneous one-period cash-funded illustration at current house values: cash falls by the amount spent, net debt increases by that same amount, and shares fall by amount / price exactly once. Enterprise operating value, forecast cash flows, discount rates, other claims and scenario weights stay fixed. Each test starts from the original snapshot; tests are not cumulative. No future FCFF, financing, dividend or SBC offset funds this purchase. A historical price is a retrospective conditional comparison with current underwriting, not observed historical value destruction; a forward illustration is not a forecast of actual repurchases or shareholder returns.',
        })
    return output


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
                any(key not in (*FORECAST_FIELDS, *OPTIONAL_FORECAST_FIELDS, 'terminal_growth_basis', 'operating_overrides') for key in overrides)):
            raise ValueError(f'counterfactual {name}: provide only nonempty forecast overrides from {", ".join(FORECAST_FIELDS)}; actuals and probabilities cannot change')
        for field in ('growth_rates', 'ebit_margins'):
            if field in overrides and (not isinstance(overrides[field], list) or
                                      len(overrides[field]) != len(base['growth_rates'])):
                raise ValueError(f'counterfactual {name}: {field} must retain the base forecast horizon')
        # Reuse the same financial validation as house scenarios. No evaluate()
        # recursion or probability substitution is involved.
        operating_case = None
        if 'operating_overrides' in overrides:
            if any(field in overrides for field in ('growth_rates', 'ebit_margins')):
                raise ValueError('A counterfactual cannot mix operating drivers with aggregate growth or EBIT overrides')
            changed, operating_case = derive_counterfactual(plan, overrides['operating_overrides'])
            changed.update({key:value for key,value in overrides.items() if key != 'operating_overrides'})
        else:
            changed = dict(base, **overrides)
        flat = None
        if base.get('convention') == GLIDE and changed.get('wacc') is not None:
            # Under the rate glide, a counterfactual wacc is one flat rate in every year and the terminal value.
            flat = _number(changed.pop('wacc'), f'counterfactual {name}.wacc')
            if not 0 < flat < 1:
                raise ValueError(f'counterfactual {name}: wacc must be between zero and one')
        for key in ('convention', 'initial_rate', 'terminal_rate', 'risk_free_rate', 'stub_years', 'terminal_growth_source'):
            changed.pop(key, None)
        if base.get('terminal_growth_source') == 'risk_free_rate' and 'terminal_growth' not in overrides:
            changed.pop('terminal_growth', None)
        trial = dict(plan, scenarios=dict(definitions, base=changed))
        try:
            _, validated, _ = _validated(trial)
        except ValueError as error:
            raise ValueError(f'counterfactual {name}: {str(error).replace("base: ", "")}') from None
        effective = validated['base']
        listed = (*FORECAST_FIELDS, *OPTIONAL_FORECAST_FIELDS)
        changed_fields = {field for field in listed if effective.get(field) != base.get(field)}
        if flat is not None:
            changed_fields.add('wacc')
        projection = _project(actuals, effective, flat=flat)
        projection.update(
            basis='Conditional base-scenario counterfactual, not an observed market input, forecast or replacement for the house scenarios.',
            overrides=copy.deepcopy(overrides),
            effective_inputs={**{field: copy.deepcopy(effective[field]) for field in listed if field in effective},
                              **({'wacc': flat, 'rate_override': 'one flat rate in every year and the terminal value'}
                                 if flat is not None else {})},
            fixed_input_basis={
                'reference_scenario': 'base',
                'actuals': copy.deepcopy(actuals),
                'unchanged_base_inputs': {field: copy.deepcopy(base[field]) for field in listed if field not in changed_fields and field in base},
                'changed_base_inputs': {**{field: copy.deepcopy(effective[field]) for field in listed if field in changed_fields and field in effective},
                                        **({'wacc': flat} if flat is not None else {})},
                'house_scenarios_and_probabilities_unchanged': True,
                'note': 'Only the listed forecast overrides change; actuals, the equity bridge, shares, currency and all unlisted base forecast inputs stay fixed. This case has no scenario probability and does not enter the weighted value.'},
            revenue_path=_revenue_path(actuals, projection['cashflows']))
        results[name] = projection
        if operating_case is not None:
            operating_case['additional_forecast_overrides'] = {
                field:copy.deepcopy(value) for field,value in overrides.items() if field != 'operating_overrides'}
            operating_case['valuation_case_basis'] = (
                'The component schedule derives operating revenue and EBIT. This valuation also applies '
                'the separately listed tax, capital, discount-rate or terminal overrides, if any; '
                'its component schedule alone is not the complete counterfactual valuation.')
            for field in operating_case['additional_forecast_overrides']:
                operating_case['fixed_input_basis']['unchanged_base_inputs'].pop(field,None)
            operating_case['fixed_input_basis']['additional_valuation_overrides'] = copy.deepcopy(operating_case['additional_forecast_overrides'])
            operating_case['fixed_input_basis']['disclosure'] += (
                ' The complete valuation additionally applies the separately listed additional_valuation_overrides.')
            projection['operating_forecast'] = operating_case
    return results


def _monitoring(plan, counterfactuals, baseline, calibration_comparisons=()):
    """Bind disclosed observation thresholds to computed, conditional cases."""
    requested = plan.get('monitoring', [])
    if not isinstance(requested, list) or len(requested) > 16:
        raise ValueError('monitoring must contain at most 16 observation records')
    rows = []
    for item in requested:
        if not isinstance(item, dict):
            raise ValueError('Each monitoring item must be an observation record')
        for field in ('name', 'observation', 'review_by', 'threshold'):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError(f'Monitoring requires explicit {field}')
        try:
            date.fromisoformat(item['review_by'])
        except ValueError:
            raise ValueError('Monitoring review_by must be YYYY-MM-DD') from None
        refs = item.get('source_refs')
        if not _reference(refs):
            raise ValueError('Monitoring source_refs must identify the evidence motivating the observation')
        references=[field for field in ('counterfactual','calibration_comparison') if field in item]
        if len(references)!=1 or not isinstance(item[references[0]],str):
            raise ValueError('Monitoring must name exactly one computed counterfactual or calibration comparison')
        if references[0]=='counterfactual':
            case = counterfactuals.get(item['counterfactual'])
        else:
            case = next((case for case in calibration_comparisons if case['name']==item['calibration_comparison']),None)
        if case is None or case.get('per_share_value') is None:
            raise ValueError('Monitoring counterfactual must name a computed conditional case')
        # A stated direction must match the linked case (an adverse Apple R&D trigger
        # was linked to a case that raised value by USD 5.29, 2026-11-15).
        direction = item.get('direction')
        if direction is not None:
            if direction not in ('lowers_value', 'raises_value'):
                raise ValueError('Monitoring direction must be lowers_value or raises_value')
            delta = case['per_share_value'] - baseline
            if (direction == 'lowers_value') != (delta < 0):
                raise ValueError(f'Monitoring {item["name"]} says it {direction.replace("_", " ")}, but its linked case '
                                 f'changes base value by {delta:+.2f} per share; link a case with that effect or correct the direction')
        rows.append({**copy.deepcopy(item), 'base_per_share_value': baseline,
                     'conditional_per_share_value': case['per_share_value'],
                     'value_change': _value_change(case['per_share_value'], baseline),
                     'basis': 'Analyst-selected observation and decision threshold, not a reported outcome or an automatic forecast. The named counterfactual changes only its disclosed inputs; correlated cases are not additive.'})
    return rows


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
    glide = base.get('convention') == GLIDE
    base_rates, base_terminal = _rate_path(base)
    wacc_changes = []
    for shift in (-.01, .01):
        wacc = round(base_rates[0] + shift, 10)
        row = {'wacc': wacc, 'wacc_change_percentage_points': shift * 100,
               'per_share_value': None, 'dollar_delta': None, 'percent_delta': None}
        if glide:
            row['terminal_rate'] = base_terminal + shift
        if not 0 < wacc < 1 or base_terminal + shift <= base['terminal_growth'] + (MIN_TERMINAL_SPREAD if glide else 0):
            row.update(status='not_calculated', reason='Alternative rate must be positive, below 100%, and above terminal growth.')
        else:
            try:
                # Constant-rate plans keep the original rounded alternative rate exactly.
                value = (_project(actuals, base, shift=shift) if glide else
                         _project(actuals, dict(base, wacc=wacc)))['per_share_value']
                row.update(status='calculated', per_share_value=value, **_value_change(value, baseline))
            except (ValueError, OverflowError):
                row.update(status='not_calculated', reason='Alternative arithmetic exceeded finite numeric bounds.')
        wacc_changes.append(row)
    tax_changes = []
    for tax in (.20, .24):
        row = {'tax_rate': tax, 'weighted_per_share_value': None,
               'dollar_delta': None, 'percent_delta': None}
        try:
            values = {name: _project(actuals, dict(definitions[name], tax_rate=tax,
                                                   **({'terminal_tax_rate': tax} if 'terminal_tax_rate' in definitions[name] else {})))['per_share_value']
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
        rows = result['cashflows']
        explicit = len(inputs['growth_rates'])
        # The step is measured where the forecast ends: from the last explicit year to the first
        # post-forecast period (a steady-state year under the rate glide, otherwise the terminal year).
        last = rows[explicit - 1]
        following = rows[explicit] if explicit < len(rows) else None
        following_fcff = following['fcff'] if following else result['terminal_fcff']
        following_reinvestment = following['net_reinvestment'] if following else result['terminal_reinvestment']
        following_revenue = following['revenue'] if following else result['terminal_revenue']
        margin = inputs['ebit_margins'][-1] * (1 - inputs['tax_rate'])
        terminal_margin = inputs['ebit_margins'][-1] * (1 - inputs.get('terminal_tax_rate', inputs['tax_rate']))
        continued_reinvestment = _finite_or_none((following_revenue - last['revenue']) / inputs['sales_to_capital'])
        equivalent_capital = (_finite_or_none(inputs['terminal_roic'] / (terminal_margin * (1 + inputs['terminal_growth']))) if terminal_margin else None)
        step = (_finite_or_none(following_reinvestment - continued_reinvestment)
                if continued_reinvestment is not None else None)
        fcff_delta = _value_change(following_fcff, last['fcff'])
        alternative_price = None
        if continued_reinvestment is not None and inputs.get('convention') != GLIDE and not inputs.get('stub_years'):
            # Original constant-rate arithmetic, kept bit for bit for existing plans.
            terminal_fcff = result['terminal_nopat'] - continued_reinvestment
            terminal_pv = terminal_fcff / (inputs['wacc'] - inputs['terminal_growth']) / (1 + inputs['wacc']) ** len(result['cashflows'])
            equity = result['pv_forecast_fcff'] + terminal_pv - actuals['net_debt'] - actuals['other_equity_claims']
            alternative_price = _finite_or_none(equity / actuals['shares_outstanding'])
        elif continued_reinvestment is not None and terminal_margin:
            # The same reinvestment expressed as the return it implies, so one projection
            # (with its rate path, steady-state years and timing) computes it.
            continuing_roic = terminal_margin * inputs['sales_to_capital'] * (1 + inputs['terminal_growth'])
            if continuing_roic >= inputs['terminal_growth'] and continuing_roic > 0:
                try:
                    alternative_price = _finite_or_none(_project(actuals, dict(inputs, terminal_roic=continuing_roic))['per_share_value'])
                except (ValueError, ArithmeticError):
                    alternative_price = None
        transition[name] = {
            'basis': 'Conditional forecast-to-terminal bridge; no observed return or capital-efficiency measurement is asserted.',
            'last_forecast_fcff': last['fcff'], 'terminal_fcff': result['terminal_fcff'],
            **({'first_post_forecast_period': 'steady_state_year', 'first_post_forecast_fcff': following_fcff} if following else {}),
            'fcff_step_currency_units': fcff_delta['dollar_delta'],
            'fcff_step_percent': fcff_delta['percent_delta'],
            'implied_forecast_incremental_roic': _finite_or_none(margin * inputs['sales_to_capital']),
            'implied_forecast_incremental_roic_basis': 'Final forecast after-tax EBIT margin times forecast sales-to-capital; a constant-margin incremental-return identity, not measured historical ROIC or a claim about changing-margin incremental returns.',
            'terminal_roic': inputs['terminal_roic'],
            'forecast_sales_to_capital': inputs['sales_to_capital'],
            'equivalent_terminal_sales_to_capital': equivalent_capital,
            'terminal_roic_required_for_continuing_forecast_sales_to_capital': _finite_or_none(terminal_margin * inputs['sales_to_capital'] * (1 + inputs['terminal_growth'])),
            'reinvestment_timing_note': 'Forecast reinvestment uses the current-year revenue increase; terminal reinvestment uses next-year NOPAT times growth/ROIC. Thus cash-flow-equivalent terminal sales-to-capital is ROIC / [after-tax margin * (1 + terminal growth)].',
            'terminal_reinvestment': result['terminal_reinvestment'],
            'terminal_reinvestment_if_forecast_sales_to_capital_continued': continued_reinvestment,
            'reinvestment_step_vs_continuing_forecast_sales_to_capital': step,
            'alternative_per_share_value_if_forecast_sales_to_capital_continued': alternative_price,
            'alternative_value_basis': ('Post-forecast reinvestment (steady-state years and the terminal value) follows the forecast sales-to-capital, expressed as the return on new capital it implies; explicit forecast cash flows, discount rates, terminal growth, NOPAT, net debt, claims and shares stay fixed.'
                                        if following else
                                        'Only terminal reinvestment changes to incremental terminal revenue / forecast sales-to-capital; forecast cash flows, WACC, terminal growth, terminal NOPAT, net debt, claims and shares stay fixed.')}
    capital_changes=[]
    for ratio in sorted({1.0,2.0,base['sales_to_capital'],6.0,12.0}):
        row = {'sales_to_capital': ratio, 'per_share_value': None,
               'dollar_delta': None, 'percent_delta': None}
        try:
            value = _project(actuals, dict(base, sales_to_capital=ratio))['per_share_value']
            row.update(status='calculated', per_share_value=value, **_value_change(value, baseline))
        except (ValueError, ArithmeticError):
            row.update(status='not_calculated', reason='Alternative arithmetic exceeded finite numeric bounds.')
        capital_changes.append(row)
    no_forecast_investment=_finite_or_none((scenarios['base']['enterprise_value']+
        sum(row['net_reinvestment']*row['discount_factor']*row.get('counted_fraction',1.0) for row in scenarios['base']['cashflows']
            if row.get('phase','explicit')=='explicit')-
        actuals['net_debt']-actuals['other_equity_claims'])/actuals['shares_outstanding'])
    no_terminal_excess_return = {'terminal_roic': base_terminal, 'per_share_value': None,
                                 'dollar_delta': None, 'percent_delta': None,
                                 'fixed_assumptions': 'Only the return on new terminal investment changes to the terminal discount rate. Terminal operating margin and growth and every explicit-forecast and equity-bridge input stay fixed; existing asset rents are not separately erased. The company earns no excess return on new capital while the market risk premium still assumes the market does.'}
    try:
        value = _project(actuals, dict(base, terminal_roic=base_terminal))['per_share_value']
        no_terminal_excess_return.update(status='calculated', per_share_value=value,
                                        **_value_change(value, baseline))
    except (ValueError, ArithmeticError):
        no_terminal_excess_return.update(status='not_calculated',
                                        reason='Alternative arithmetic exceeded finite numeric bounds.')
    return {
        'basis': 'Computed, conditional house-assumption diagnostics; these are not observed market inputs, forecasts or automatic assumption corrections.',
        'delta_unit': 'result currency units per share',
        'percentage_delta_basis': 'Signed value difference / absolute baseline value * 100; null for a zero baseline or nonfinite comparison.',
        'base_wacc': {'baseline_wacc': base_rates[0], 'baseline_per_share_value': baseline,
                      'fixed_assumptions': ('The whole discount-rate path, including the terminal rate, shifts by the stated amount. ' if glide else '')+
                                           'All other base-case forecast and terminal inputs, net debt, claims and shares are fixed.',
                      'changes': wacc_changes},
        'uniform_tax': {'baseline_weighted_per_share_value': weighted,
                        'baseline_scenario_tax_rates': {name: definitions[name]['tax_rate'] for name in SCENARIOS},
                        **({'baseline_scenario_terminal_tax_rates': {name: definitions[name].get('terminal_tax_rate', definitions[name]['tax_rate'])
                                                                     for name in SCENARIOS}}
                           if any('terminal_tax_rate' in definitions[name] for name in SCENARIOS) else {}),
                        'fixed_assumptions': 'Replace every scenario cash operating tax rate with the stated alternative; all other scenario inputs, probability weights, net debt, claims and shares stay fixed.',
                        'changes': tax_changes},
        'terminal_transition': transition,
        'base_capital_efficiency':{
            'fixed_assumptions':'Only explicit-forecast sales-to-capital changes. Terminal reinvestment, operating margins/growth, taxes, discount rate and equity bridge stay fixed.',
            'changes':capital_changes,
            'zero_explicit_forecast_net_investment_per_share_value':no_forecast_investment,
            'zero_explicit_forecast_net_investment_status':'calculated' if no_forecast_investment is not None else 'not_calculated',
            'zero_investment_basis':'Set only explicit-forecast net reinvestment to zero, preserving terminal reinvestment. This is an illustration, not an achievable forecast or a universal value floor/ceiling.'},
        'base_terminal_return_at_wacc':no_terminal_excess_return}


def _measurement_day(record):
    if not isinstance(record, dict):
        return None
    value = next((record.get(key) for key in ('as_of', 'instant', 'observed_at', 'end_date') if record.get(key)), None)
    try:
        return date.fromisoformat(value[:10]).isoformat() if isinstance(value, str) else None
    except ValueError:
        return None


def _calibration_reconciliations(plan, spec):
    checks = []
    debt_bridge, claims_bridge = plan.get('net_debt_bridge', {}), plan.get('other_claims_bridge', {})
    for bridge_name, bridge in (('net_debt_bridge', debt_bridge), ('other_claims_bridge', claims_bridge)):
        if isinstance(bridge, dict):
            if (bridge.get('currency', spec['currency']) != spec['currency'] or
                    bridge.get('amount_unit', 'currency_units') != 'currency_units'):
                raise ValueError(f'{bridge_name} currency and amount units must match the discount-rate inputs')
            if ('finance_leases_included_in_net_debt' in bridge and
                    not isinstance(bridge['finance_leases_included_in_net_debt'], bool)):
                raise ValueError(f'{bridge_name}.finance_leases_included_in_net_debt must be an explicit boolean')
    combined = isinstance(debt_bridge, dict) and debt_bridge.get('finance_leases_included_in_net_debt', False)
    if (isinstance(claims_bridge, dict) and 'finance_leases_included_in_net_debt' in claims_bridge and
            claims_bridge['finance_leases_included_in_net_debt'] != combined):
        raise ValueError('Debt and other-claims bridges conflict on finance-lease inclusion')
    if combined:
        if not isinstance(claims_bridge, dict):
            raise ValueError('A combined debt bridge requires an explicit other-claims allocation record')
        total = _number(_required(debt_bridge, 'debt', 'net_debt_bridge'), 'net_debt_bridge.debt')
        if total < 0:
            raise ValueError('Combined debt and finance leases cannot be negative')
        bridge_date = _measurement_day(debt_bridge)
        measurement = debt_bridge.get('measurement', 'book_value_proxy')
        for field in ('financial_debt', 'finance_lease_debt'):
            record = spec[field]
            if (bridge_date is None or _measurement_day(record) != bridge_date or
                    record.get('measurement', 'book_value_proxy') != measurement):
                raise ValueError('Combined debt reconciliation requires matching rate/bridge dates and measurement bases')
        lease = claims_bridge.get('reported_finance_lease_liabilities')
        if lease is not None:
            if (_measurement_day(claims_bridge) != bridge_date or
                    claims_bridge.get('measurement', 'book_value_proxy') != measurement):
                raise ValueError('A finance-lease split requires the same date and measurement as combined debt')
            lease = _number(lease, 'other_claims_bridge.reported_finance_lease_liabilities')
            if not 0 <= lease <= total:
                raise ValueError('Reported finance leases must be nonnegative and cannot exceed combined debt')
            expected_financial, expected_lease = total - lease, lease
            if any('allocation' in spec[field] for field in ('financial_debt', 'finance_lease_debt')):
                raise ValueError('A known separate finance-lease amount must be split explicitly, not assigned an unknown-split allocation')
            scope = 'separately_reported_finance_leases_split_from_combined_debt'
        else:
            expected_financial, expected_lease = total, 0.
            expected_allocations = {'financial_debt': 'combined_debt_including_finance_leases',
                                    'finance_lease_debt': 'included_in_financial_debt'}
            for field, allocation in expected_allocations.items():
                record = spec[field]
                if record.get('allocation') != allocation:
                    raise ValueError('Unknown finance-lease split requires explicit combined-debt and zero-allocation declarations')
                label = record.get('label', '').lower()
                if ((field == 'financial_debt' and not ('combined' in label and 'lease' in label)) or
                        (field == 'finance_lease_debt' and 'allocation' not in label)):
                    raise ValueError('Unknown-split rate labels must identify combined debt and the finance-lease zero allocation')
            if spec['finance_lease_debt']['value'] != 0:
                raise ValueError('Unknown-split finance_lease_debt must be exactly zero as an allocation')
            scope = 'unknown_finance_lease_split_combined_amount_allocated_once'
        for field, expected in (('financial_debt', expected_financial), ('finance_lease_debt', expected_lease)):
            record = spec[field]
            if not math.isclose(record['value'], expected, rel_tol=1e-8, abs_tol=1e-6):
                raise ValueError(f'discount_rate.{field} conflicts with the combined debt/finance-lease allocation')
            checks.append({'field': field, 'status': 'reconciled_combined_debt_allocation',
                           'rate_input': record['value'], 'bridge_input': expected,
                           'combined_debt': total, 'reported_finance_lease_liabilities': lease,
                           'rate_date': _measurement_day(record), 'bridge_date': bridge_date,
                           'scope': scope, 'allocation': record.get('allocation'),
                           'zero_finance_lease_input_is_allocation_not_reported_absence': lease is None})
        included = spec['financial_debt']['value'] + spec['finance_lease_debt']['value']
        if not math.isclose(included, total, rel_tol=1e-8, abs_tol=1e-6):
            raise ValueError('Included rate debt must equal combined debt and finance leases exactly once')
        checks.append({'field': 'included_debt', 'status': 'reconciled_combined_debt_once',
                       'rate_input': included, 'bridge_input': total, 'scope': scope})
        # A supplied claims allocation is an accounting identity, not a new zero default.
        allocated_claims = claims_bridge.get('other_equity_claims')
        if allocated_claims is not None and not math.isclose(
                _number(allocated_claims, 'other_claims_bridge.other_equity_claims'),
                _number(plan['actuals']['other_equity_claims'], 'actuals.other_equity_claims'),
                rel_tol=1e-8, abs_tol=1e-6):
            raise ValueError('Other equity claims conflict with the finance-leases-already-in-debt allocation')
    else:
        for field, bridge_name, amount_name in (
                ('financial_debt', 'net_debt_bridge', 'debt'),
                ('finance_lease_debt', 'other_claims_bridge', 'reported_finance_lease_liabilities')):
            record, bridge = spec[field], plan.get(bridge_name, {})
            reported = bridge.get(amount_name) if isinstance(bridge, dict) else None
            same_date = _measurement_day(record) == _measurement_day(bridge) and _measurement_day(record) is not None
            comparable = record.get('measurement', 'book_value_proxy') == 'book_value_proxy'
            status = 'not_comparable_dates_or_measurement'
            if reported is not None and same_date and comparable:
                reported = _number(reported, bridge_name + '.' + amount_name)
                if not math.isclose(record['value'], reported, rel_tol=1e-8, abs_tol=1e-6):
                    raise ValueError(f'discount_rate.{field} conflicts with the same-date reported equity bridge')
                status = 'reconciled'
            checks.append({'field': field, 'status': status, 'rate_input': record['value'],
                           'bridge_input': reported, 'rate_date': _measurement_day(record),
                           'bridge_date': _measurement_day(bridge)})
    record = spec['market_equity']
    dates = plan.get('input_dates', {})
    quote_date = _measurement_day(dates.get('market_price'))
    share_date = _measurement_day(dates.get('shares_outstanding'))
    rate_date = _measurement_day(record)
    comparable = rate_date is not None and rate_date == quote_date == share_date
    construction = record.get('construction', record.get('measurement'))
    explicit_quote_basis = construction == 'quote_times_reported_shares'
    expected = None
    status = 'not_comparable_dates_or_measurement'
    if 'market_price' in plan and (comparable or explicit_quote_basis):
        expected = _number(plan['market_price'], 'market_price') * _number(plan['actuals']['shares_outstanding'], 'shares_outstanding')
        if not math.isclose(record['value'], expected, rel_tol=1e-8, abs_tol=1e-6):
            explained = (construction == 'independent_market_cap' and
                         isinstance(record.get('reconciliation'), str) and record['reconciliation'].strip())
            if not explained:
                raise ValueError('discount_rate.market_equity conflicts with the comparable quote and share count')
            status = 'independent_market_cap_difference_disclosed'
        else:
            status = 'reconciled_quote_times_reported_shares'
    checks.append({'field': 'market_equity', 'status': status, 'rate_input': record['value'],
                   'quote_times_reported_shares': expected, 'rate_date': rate_date,
                   'quote_date': quote_date, 'share_date': share_date,
                   'reconciliation': record.get('reconciliation')})
    return checks


def materialize(plan):
    """Derive optional operating schedules and sourced discount rates explicitly.

    These are calculations, not model-selected replacements for actuals. Duplicate
    aggregate paths/rates must agree; omit them when the detailed inputs govern.
    """
    prepared, operating = apply_operating(plan)
    calibration = None
    prepared['_timing'] = _timing(prepared)
    if 'discount_rate' in prepared:
        spec = prepared['discount_rate']
        if (not isinstance(spec, dict) or spec.get('currency') != prepared.get('currency')
                or spec.get('amount_unit') != prepared.get('amount_unit')):
            raise ValueError('discount_rate currency and amount_unit must match the valuation plan')
        if spec.get('lease_convention') != 'gaap_operating':
            raise ValueError('This FCFF model retains operating rent; discount_rate must use gaap_operating lease treatment')
        beta_record = spec.get('unlevered_beta', {})
        if isinstance(beta_record,dict) and beta_record.get('calculation') == 'historical_issuer_cash_corrected':
            bridge = prepared.get('net_debt_bridge', {})
            if not isinstance(bridge,dict) or 'selected_cash_and_nonoperating_investments' not in bridge:
                raise ValueError('Issuer beta cash correction requires an explicit nonoperating cash bridge')
            derived = operating_asset_beta(beta_record.get('historical_equity_beta'),
                market_equity=spec['market_equity']['value'], financial_debt=spec['financial_debt']['value'],
                finance_lease_debt=spec['finance_lease_debt']['value'],
                nonoperating_cash=bridge['selected_cash_and_nonoperating_investments'],
                marginal_tax_rate=spec['marginal_tax_rate']['value'])
            beta_value = derived['cash_corrected_unlevered_beta']
            if 'value' in beta_record and not math.isclose(_number(beta_record['value'],'unlevered_beta.value'),beta_value,rel_tol=1e-9,abs_tol=1e-12):
                raise ValueError('unlevered_beta.value conflicts with its explicit issuer-history calculation; omit the duplicate value')
            beta_record['value'] = beta_value
            beta_record['derived_calculation'] = derived
            beta_record['calculation_snapshot_date'] = spec.get('as_of')
        elif isinstance(beta_record,dict) and beta_record.get('calculation') == 'bottom_up_sector_weighted':
            beta_value, derived = _bottom_up_beta(beta_record)
            if 'value' in beta_record and not math.isclose(_number(beta_record['value'],'unlevered_beta.value'),beta_value,rel_tol=1e-9,abs_tol=1e-12):
                raise ValueError('unlevered_beta.value conflicts with its sector-weighted calculation; omit the duplicate value')
            beta_record['value'] = beta_value
            beta_record['derived_calculation'] = derived
        calibration = calculate_discount_rate(spec, as_of=prepared.get('valuation_date'))
        calibration['valuation_input_reconciliations'] = _calibration_reconciliations(prepared, spec)
        if (isinstance(prepared.get('net_debt_bridge'), dict) and
                prepared['net_debt_bridge'].get('finance_leases_included_in_net_debt')):
            unknown_split = prepared.get('other_claims_bridge', {}).get('reported_finance_lease_liabilities') is None
            calibration['conventions']['financial_debt_input'] = (
                'Combined financial debt and finance leases allocated entirely to financial_debt; '
                'finance_lease_debt zero is an allocation, not reported absence. The separate lease amount is unknown.'
                if unknown_split else
                'Financial debt excludes separately reported finance leases: combined bridge debt minus the same-date lease amount; finance leases are included once through finance_lease_debt.')
        if calibration['term_structure'] == GLIDE:
            if prepared.get('currency') != spec['risk_free_rate'].get('currency', spec['currency']):
                raise ValueError('The rate glide needs a risk-free rate in the cash-flow currency')
            for name in SCENARIOS:
                if prepared['scenarios'][name].get('wacc') is not None:
                    raise ValueError(f'{name}.wacc must be omitted: the dated discount-rate path governs every year')
            prepared['_discounting'] = {
                'convention': GLIDE, 'initial': calibration['applied_wacc'], 'terminal': calibration['terminal_wacc'],
                'risk_free_rate': spec['risk_free_rate']['value'], 'equity_risk_premium': spec['equity_risk_premium']['value'],
                'damodaran_default_terminal_rate': calibration['damodaran_default_terminal_rate'],
                'marginal_tax_rate': spec['marginal_tax_rate']['value']}
        else:
            for name in SCENARIOS:
                scenario = prepared['scenarios'][name]
                rate = calibration['applied_wacc']
                if 'wacc' in scenario and not math.isclose(scenario['wacc'], rate, rel_tol=1e-9, abs_tol=1e-12):
                    raise ValueError(f'{name}.wacc conflicts with the dated discount_rate calculation; omit the duplicate rate')
                scenario['wacc'] = rate
    return prepared, operating, calibration


def _bottom_up_beta(record):
    """Weighted cash-corrected industry unlevered beta from pinned table values."""
    sectors = record.get('sectors')
    if not isinstance(sectors, list) or not 1 <= len(sectors) <= 8:
        raise ValueError('bottom_up_sector_weighted needs 1-8 sectors from the supplied industry table')
    total, weighted, names = 0.0, 0.0, set()
    for item in sectors:
        if not isinstance(item, dict) or not isinstance(item.get('sector'), str) or not item['sector'].strip():
            raise ValueError('Each bottom-up sector needs its exact industry-table name')
        if item['sector'] in names:
            raise ValueError('Bottom-up sectors must be distinct')
        names.add(item['sector'])
        weight = _number(item.get('weight'), 'sector weight')
        beta = _number(item.get('cash_corrected_unlevered_beta'), f'{item["sector"]} cash_corrected_unlevered_beta')
        if not 0 < weight <= 1 or not 0 < beta < 5:
            raise ValueError('Sector weights must be in (0, 1] and pinned betas in (0, 5)')
        total += weight
        weighted += weight * beta
    if not math.isclose(total, 1, abs_tol=1e-6):
        raise ValueError('Bottom-up sector weights must sum to one')
    return weighted, {'basis': 'Weighted average of cash-corrected industry unlevered betas pinned from the dated industry table; '
                               'weights are the stated business mix. Industry averages are simple averages across firms, '
                               'so a firm that dominates its industry partly measures itself.',
                      'sectors': copy.deepcopy(sectors), 'weighted_unlevered_beta': weighted}


def _one_year_after(day):
    try:
        return day.replace(year=day.year + 1)
    except ValueError:  # 29 February
        return day.replace(year=day.year + 1, day=28)


def _timing(plan):
    """Stub between the fiscal-year anchor and the valuation date (v2 opt-in)."""
    spec = plan.get('valuation_timing')
    if spec is None:
        return {'convention': 'integer_years_from_anchor', 'stub_years': 0.0}
    if not isinstance(spec, dict) or spec.get('convention') != 'stub_v1' or set(spec) != {'convention', 'anchor_date'}:
        raise ValueError('valuation_timing must be {"convention": "stub_v1", "anchor_date": "YYYY-MM-DD"}')
    try:
        anchor, valued = date.fromisoformat(spec['anchor_date']), date.fromisoformat(plan['valuation_date'])
    except (KeyError, TypeError, ValueError):
        raise ValueError('valuation_timing needs anchor_date and valuation_date as YYYY-MM-DD') from None
    days = (valued - anchor).days
    year_length = (_one_year_after(anchor) - anchor).days
    if not 0 <= days < year_length:
        raise ValueError(f'The fiscal-year anchor is {days} days from the valuation date; supply a balance sheet '
                         'dated within the year before the valuation date')
    return {'convention': 'stub_v1', 'anchor_date': anchor.isoformat(), 'valuation_date': valued.isoformat(),
            'elapsed_days': days, 'fiscal_year_days': year_length, 'stub_years': days / year_length,
            'basis': 'Present value dated at valuation_date. Year-one cash flow counts only for the unexpired part '
                     'of the year (as in Damodaran\'s implied-ERP calculator) and every flow is discounted from the '
                     'valuation date. Net debt stays at the anchor balance sheet; cash generated and distributed '
                     'since the anchor is not reconciled.'}


def _with_calculation(base, calculation):
    """A base scenario discounted with another discount-rate calculation's path.

    Under the glide a changed risk-free rate also changes a defaulted terminal
    growth (the equity risk premium's own long-run growth), and the cap and
    spread rules are applied again, as for the house case.
    """
    if base.get('convention') != GLIDE:
        return dict(base, wacc=calculation['applied_wacc'])
    rf = calculation['provenance']['risk_free_rate']['value']
    scenario = dict(base, initial_rate=calculation['applied_wacc'], terminal_rate=calculation['terminal_wacc'], risk_free_rate=rf)
    if base.get('terminal_growth_source') == 'risk_free_rate':
        if base['growth_rates'][-1] < .01 or base['terminal_roic'] < rf:
            raise ValueError('the risk-free terminal-growth default does not apply at this risk-free rate')
        scenario['terminal_growth'] = rf
    elif scenario['terminal_growth'] > rf + GROWTH_CAP_TOLERANCE:
        raise ValueError(f'stated terminal growth exceeds this comparison\'s {rf:.2%} risk-free rate')
    if scenario['terminal_rate'] - scenario['terminal_growth'] < MIN_TERMINAL_SPREAD or scenario['terminal_roic'] < scenario['terminal_growth']:
        raise ValueError('terminal rate within one point of terminal growth, or terminal return below growth')
    return scenario


def _calibration_comparisons(plan, actuals, base, baseline):
    comparisons=plan.get('calibration_comparisons',[])
    if not isinstance(comparisons,list) or len(comparisons)>8:
        raise ValueError('calibration_comparisons must contain at most eight explicit rate cases')
    if comparisons and 'discount_rate' not in plan:
        raise ValueError('Calibration comparisons require a dated base discount_rate')
    allowed={'risk_free_rate','equity_risk_premium','unlevered_beta','pretax_cost_of_debt','marginal_tax_rate'}
    output=[]; names=set()
    for case in comparisons:
        if (not isinstance(case,dict) or not isinstance(case.get('name'),str) or
                not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',case['name']) or
                case['name'] in names or not isinstance(case.get('label'),str) or not case['label'].strip()):
            raise ValueError('Each calibration comparison requires a unique name and explicit label')
        names.add(case['name'])
        overrides=case.get('overrides')
        if not isinstance(overrides,dict) or not overrides or overrides.keys()-allowed:
            raise ValueError('Calibration comparisons may change only explicit risk/return input records')
        if ('risk_free_rate' in overrides)!=('equity_risk_premium' in overrides):
            raise ValueError('A comparison must replace the matched Treasury/ERP pair together')
        trial=copy.deepcopy(plan)
        trial['discount_rate'].update(copy.deepcopy(overrides))
        beta=trial['discount_rate'].get('unlevered_beta',{})
        if ('unlevered_beta' not in overrides and isinstance(beta,dict)
                and beta.get('calculation')=='historical_issuer_cash_corrected'):
            # The house already validated this duplicate. Its conditional
            # value must be rederived when a dependency such as tax changes.
            beta.pop('value',None)
        for scenario in trial['scenarios'].values():
            scenario.pop('wacc',None)
        _,_,calculation=materialize(trial)
        glide=base.get('convention')==GLIDE
        try:
            scenario=_with_calculation(base,calculation)
            projection=_project(actuals,scenario)
        except ValueError as error:
            output.append({'name':case['name'],'label':case['label'],'overrides':copy.deepcopy(overrides),
                           'status':'not_calculated','reason':str(error)[:300],'per_share_value':None,'wacc':calculation['applied_wacc']})
            continue
        row={'name':case['name'],'label':case['label'],'overrides':copy.deepcopy(overrides),'status':'calculated',
             'discount_rate_calculation':calculation,'wacc':calculation['applied_wacc'],
             **({'terminal_rate':calculation['terminal_wacc'],'terminal_growth':scenario['terminal_growth'],
                 'terminal_growth_source':base.get('terminal_growth_source')} if glide else {}),
             'per_share_value':projection['per_share_value'],
             'terminal_value_fraction':projection['terminal_value_fraction'],
             'value_change':_value_change(projection['per_share_value'],baseline),
             'fixed_assumptions':(
                 'Only the listed rate-input records and their dependent calculations change: the initial rate, the '
                 'terminal rate and, where terminal growth defaults to the risk-free rate, terminal growth, which moves '
                 'with the comparison\'s risk-free rate. Base operating cash flows, terminal ROIC, equity bridge and house '
                 'scenarios/weights stay fixed. Historical-fit alternatives are not forward confidence intervals.' if glide else
                 'Only the listed rate-input records and their dependent calculations, including resulting constant WACC, change. Base operating cash flows, terminal growth/ROIC, equity bridge and house scenarios/weights stay fixed. Historical-fit alternatives are not forward confidence intervals.')}
        if glide and abs(scenario['terminal_growth']-base['terminal_growth'])>1e-12:
            # Separates the rate effect from the growth effect for the reader.
            try:
                row['per_share_value_at_house_terminal_growth']=_project(
                    actuals,dict(scenario,terminal_growth=base['terminal_growth']))['per_share_value']
            except (ValueError,ArithmeticError):
                row['per_share_value_at_house_terminal_growth']=None
        output.append(row)
    return output


def evaluate(plan):
    """Recompute all scenarios, bridge enterprise to equity, and return results."""
    canonical_plan=copy.deepcopy(plan)
    plan, operating, calibration = materialize(canonical_plan)
    actuals, definitions, market = _validated(plan)
    discounting = plan.get('_discounting') or {'convention': 'constant'}
    versioned = discounting['convention'] == GLIDE or plan['_timing']['stub_years'] > 0
    counterfactuals = _counterfactuals(plan, actuals, definitions)
    scenarios = {}
    for name in SCENARIOS:
        result = _project(actuals, definitions[name])
        result['probability'] = definitions[name]['probability']
        for key in ('terminal_growth_source', 'terminal_growth_basis'):
            if key in definitions[name]:
                result[key] = definitions[name][key]
        if market is not None:
            result['value_to_price'] = result['per_share_value'] / market
            result['fixed_exit_return_at_market_price'] = _fixed_exit_return(actuals,result,market)
            if result['per_share_value']>0:
                result['fixed_exit_return_at_model_value'] = _fixed_exit_return(actuals,result,result['per_share_value'])
        scenarios[name] = result
    weighted = sum(result['per_share_value'] * result['probability'] for result in scenarios.values())
    result = {'model': 'unlevered-fcff-incremental-sales-to-capital-' + ('v2' if versioned else 'v1'),
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
    if operating is not None:
        result['operating_forecasts'] = operating
    if calibration is not None:
        result['discount_rate_calculation'] = calibration
    if 'calibration_comparisons' in plan:
        result['calibration_comparisons'] = _calibration_comparisons(canonical_plan,actuals,definitions['base'],scenarios['base']['per_share_value'])
    if 'monitoring' in plan:
        result['monitoring'] = _monitoring(plan,counterfactuals,scenarios['base']['per_share_value'],result.get('calibration_comparisons',[]))
    if versioned:
        rates, terminal = _rate_path(definitions['base'])
        result['discounting'] = {
            'convention': discounting['convention'], 'timing': copy.deepcopy(plan['_timing']),
            'base_rate_path': rates, 'terminal_rate': terminal,
            **({'initial_rate': discounting['initial'], 'hold_years': GLIDE_HOLD_YEARS, 'glide_end_year': GLIDE_END_YEAR,
                'risk_free_rate': discounting['risk_free_rate'], 'equity_risk_premium': discounting['equity_risk_premium'],
                'damodaran_default_terminal_rate': discounting['damodaran_default_terminal_rate'],
                'terminal_relevered_beta': calibration['terminal_relevered_beta'],
                'terminal_unlevered_beta': calibration['provenance']['terminal_unlevered_beta']['value'],
                'basis': 'Forecast years 1-5 at the current WACC, then a linear move to a terminal cost of capital at '
                         'market-average business risk (market operating-asset beta relevered at the firm\'s own leverage) '
                         'by year 10. Forecasts shorter than 10 years are extended to year 10 with steady-state years.'}
               if discounting['convention'] == GLIDE else {})}
    if market is not None:
        result['market_price'] = market
        result['weighted_value_to_price'] = weighted / market
        result['reverse_dcf'] = _reverse_growth(actuals, definitions['base'], market)
        if discounting['convention'] != GLIDE:
            result['reverse_dcf']['discount_rate'] = _reverse_wacc(actuals, definitions['base'], market, calibration)
        result['implied_return_at_price'] = _implied_return(actuals, definitions, scenarios, weighted, market, discounting)
        result['price_requirements'] = _price_requirements(actuals, definitions['base'], market)
        peak = (plan.get('cost_path_reference') or {}).get('max_operating_margin')
        result['convention_panel'] = _convention_panel(actuals, definitions, scenarios, discounting,
                                                       reported_peak_margin=peak if isinstance(peak, (int, float)) else None)
    if 'beta_cross_checks' in plan:
        result['beta_cross_checks'] = _beta_cross_checks(canonical_plan, actuals, definitions['base'], scenarios['base']['per_share_value'])
    result['computed_diagnostics']['valuation_multiples'] = _valuation_multiples(plan, actuals, scenarios, market)
    if 'entry_policy' in plan:
        result['computed_diagnostics']['entry_policy'] = _entry_policy(plan, scenarios, weighted, market)
    if 'repurchase_tests' in plan:
        result['computed_diagnostics']['repurchase_tests'] = _repurchase_tests(plan, actuals, scenarios, weighted)
    return result


def _price_requirements(actuals, base, market):
    """What the price requires of one base-case input at a time, every other input fixed.

    A live first draft (2026-11-14) explained the gap to price by adding convention
    changes that lower value. This exhibit states directly how far each input
    would have to move for the base value to equal the price, or that no value
    of it does. Inputs interact; rows are alternatives and are never combined.
    """
    def solve(mutate, low, high, samples=48):
        def gap(x):
            scenario = copy.deepcopy(base)
            mutate(scenario, x)
            # Only inputs the engine would accept in a plan are evaluated.
            if (any(not -1 < margin < 1 for margin in scenario['ebit_margins']) or
                    any(growth <= -1 for growth in scenario['growth_rates']) or
                    scenario['sales_to_capital'] <= 0 or scenario['terminal_roic'] <= scenario['terminal_growth']):
                return None
            try:
                return _project(actuals, scenario)['per_share_value'] - market
            except (ValueError, ArithmeticError, KeyError, OverflowError):
                return None
        grid = [low + (high - low) * index / samples for index in range(samples + 1)]
        values = [gap(x) for x in grid]
        for (left, fl), (right, fr) in zip(zip(grid, values), zip(grid[1:], values[1:])):
            if fl is None or fr is None or fl * fr > 0:
                continue
            for _ in range(50):
                middle = (left + right) / 2
                fm = gap(middle)
                if fm is None:
                    break
                if fl * fm <= 0:
                    right = middle
                else:
                    left, fl = middle, fm
            return {'status': 'solved', 'needed': (left + right) / 2}
        valid = [(x, value) for x, value in zip(grid, values) if value is not None]
        if not valid:
            return {'status': 'not_calculated'}
        # Closest approach to the price from whichever side every value lies on.
        best = min(valid, key=lambda item: abs(item[1]))
        return {'status': 'not_reachable', 'searched': [low, high], 'closest_value': best[1] + market, 'at': best[0],
                'direction': 'above' if best[1] > 0 else 'below'}
    def shift(field):
        def mutate(scenario, delta):
            scenario[field] = [value + delta for value in scenario[field]]
        return mutate
    def set_to(field):
        def mutate(scenario, value):
            scenario[field] = value
        return mutate
    def path(field, delta):
        # A shift row's year-1 figure alone was misread live (2026-11-14) as the
        # whole path; report the projected path it produces, steady-state years included.
        scenario = copy.deepcopy(base)
        shift(field)(scenario, delta)
        try:
            flows = _project(actuals, scenario)['cashflows']
        except (ValueError, ArithmeticError, KeyError, OverflowError):
            return None
        if field == 'growth_rates':
            if actuals['revenue'] <= 0 or flows[-1]['revenue'] <= 0:
                return None
            over = (flows[-1]['revenue'] / actuals['revenue']) ** (1 / len(flows)) - 1
            return {'year1': flows[0]['revenue_growth'], 'over_forecast': over, 'years': len(flows),
                    'measure': 'compound_annual_growth'}
        return {'year1': flows[0]['ebit_margin'], 'over_forecast': sum(flow['ebit_margin'] for flow in flows) / len(flows),
                'years': len(flows), 'measure': 'average'}
    terminal_rate = base.get('terminal_rate', base.get('wacc'))
    rows = []
    for name, label, mutate, low, high, house, kind in (
            ('revenue_growth', 'Revenue growth in every explicit forecast year', shift('growth_rates'), -.2, .3,
             base['growth_rates'][0], 'shift'),
            ('ebit_margin', 'EBIT margin in every explicit forecast year', shift('ebit_margins'), -.3, .45,
             base['ebit_margins'][0], 'shift'),
            ('terminal_roic', 'Terminal return on new capital', set_to('terminal_roic'),
             base['terminal_growth'] + .001, 3.0, base['terminal_roic'], 'level'),
            ('sales_to_capital', 'Sales-to-capital (capital efficiency)', set_to('sales_to_capital'), .25, 40.0,
             base['sales_to_capital'], 'level'),
            ('terminal_growth', 'Terminal growth', set_to('terminal_growth'), -.03, terminal_rate - .0101,
             base['terminal_growth'], 'level')):
        row = {'input': name, 'label': label, 'kind': kind, 'house': house, **solve(mutate, low, high)}
        if kind == 'shift':
            field = 'growth_rates' if name == 'revenue_growth' else 'ebit_margins'
            row['house_path'] = path(field, 0.0)
            if row['status'] in ('solved', 'not_reachable'):
                row['needed_path' if row['status'] == 'solved' else 'at_path'] = path(
                    field, row['needed'] if row['status'] == 'solved' else row['at'])
        rows.append(row)
    return {'rows': rows, 'market_price': market,
            'basis': 'Base scenario. One input changed at a time, all others fixed; "shift" rows add the same number of '
                     'points to every explicit forecast year (steady-state years keep terminal growth and the final margin) '
                     'and report the projected path: compound annual revenue growth or the average EBIT margin over all '
                     'forecast years. Rows are alternatives, not additive, and carry no probability. '
                     'A row that is not reachable reports the value closest to the price within the inputs the engine accepts.'}


def _roots(function, low, high, samples=160):
    """Every bracketed root of a scalar function on [low, high]."""
    grid = [low + (high - low) * index / samples for index in range(samples + 1)]
    values = []
    for point in grid:
        try:
            values.append(function(point))
        except (ValueError, ArithmeticError):
            values.append(None)
    roots = []
    for (left, fl), (right, fr) in zip(zip(grid, values), zip(grid[1:], values[1:])):
        if fl is None or fr is None or fl * fr > 0:
            continue
        if fl == 0:
            root = left
        else:
            for _ in range(80):
                middle = (left + right) / 2
                fm = function(middle)
                if fl * fm <= 0:
                    right = middle
                else:
                    left, fl = middle, fm
            root = (left + right) / 2
        if not any(abs(root - previous) < 1e-7 for previous in roots):
            roots.append(root)
    return sorted(roots)


def _implied_return(actuals, definitions, scenarios, weighted, market, discounting):
    """The one flat rate at which value equals the price, beside its like-for-like comparator.

    The comparator is the flat rate that reproduces the house value from the same
    cash flows (the house rate path summarised as one number). A rate within two
    points of terminal growth is flagged: value is then very sensitive to it.
    """
    def solve(value_of, target, floor):
        roots = _roots(lambda rate: value_of(rate) - target, floor + 1e-4, .5)
        return roots[0] if len(roots) == 1 else None, roots
    rows = {}
    for name in SCENARIOS:
        inputs, house = definitions[name], scenarios[name]['per_share_value']
        value_of = lambda rate, inputs=inputs: _project(actuals, inputs, flat=rate)['per_share_value']
        implied, implied_roots = solve(value_of, market, inputs['terminal_growth'])
        required, required_roots = solve(value_of, house, inputs['terminal_growth'])
        row = {'implied_return': implied, 'required_return_flat_equivalent': required,
               'status': 'solved' if implied is not None and required is not None else
                         'multiple_solutions' if len(implied_roots) > 1 or len(required_roots) > 1 else 'not_bracketed'}
        if implied is not None:
            spread = implied - inputs['terminal_growth']
            row.update(terminal_spread_at_implied_return=spread, fragile=spread < FRAGILE_TERMINAL_SPREAD)
        if implied is not None and required is not None:
            row['gap_percentage_points'] = (required - implied) * 100
        if inputs.get('convention') == GLIDE:
            shift_roots = _roots(lambda delta, inputs=inputs: _project(actuals, inputs, shift=delta)['per_share_value'] - market, -.08, .08)
            row['parallel_shift_of_house_path_to_price'] = shift_roots[0] if len(shift_roots) == 1 else None
        rows[name] = row
    floor = max(definitions[name]['terminal_growth'] for name in SCENARIOS)
    weighted_of = lambda rate: sum(_project(actuals, definitions[name], flat=rate)['per_share_value'] *
                                   definitions[name]['probability'] for name in SCENARIOS)
    implied, _ = solve(weighted_of, market, floor)
    required, _ = solve(weighted_of, weighted, floor)
    output = {
        'scenarios': rows,
        'probability_weighted_value': {
            'implied_return': implied, 'required_return_flat_equivalent': required,
            'gap_percentage_points': (required - implied) * 100 if implied is not None and required is not None else None,
            'label': 'Rate of return at which the probability-weighted value equals the price; not the expected return across scenarios.'},
        'market_price': market,
        'basis': 'One flat discount rate applied in every year and in the terminal value, with each scenario\'s cash flows, '
                 'terminal growth, terminal return and timing fixed. The required return is the flat rate that reproduces '
                 'the house value from the same cash flows, so the two numbers are like for like. This describes what the '
                 'price implies; it is not a forecast of shareholder returns.'}
    if discounting.get('convention') == GLIDE:
        output['context'] = {'initial_rate': discounting['initial'], 'terminal_rate': discounting['terminal'],
                             'risk_free_rate': discounting['risk_free_rate'],
                             'risk_free_plus_equity_risk_premium': discounting['risk_free_rate'] + discounting['equity_risk_premium']}
    return output


def _convention_panel(actuals, definitions, scenarios, discounting, reported_peak_margin=None):
    """Base-case value under one alternative convention at a time; the same rows for every plan.

    Rows whose terminal rate is within two points of terminal growth are reported
    but marked fragile and excluded from the largest-step ranking, because their
    values are dominated by a near-zero denominator rather than by the convention.
    """
    base, baseline = definitions['base'], scenarios['base']['per_share_value']
    glide = base.get('convention') == GLIDE
    rates, terminal = _rate_path(base)
    margin = base['ebit_margins'][-1] * (1 - base.get('terminal_tax_rate', base['tax_rate']))
    cases = []
    def add(name, label, change, *, applies=True, reason=None, kind='convention', **project):
        row = {'name': name, 'label': label, 'kind': kind, 'per_share_value': None, 'dollar_delta': None, 'percent_delta': None}
        if not applies:
            cases.append(dict(row, status='not_applicable', reason=reason))
            return
        try:
            scenario = dict(base, **change)
            result = _project(actuals, scenario, **project)
            spread = result.get('terminal_spread', _rate_path(scenario, **project)[1] - scenario['terminal_growth'])
            row.update(per_share_value=result['per_share_value'], terminal_spread=spread,
                       status='fragile_spread' if spread < FRAGILE_TERMINAL_SPREAD else 'calculated',
                       **_value_change(result['per_share_value'], baseline))
        except (ValueError, ArithmeticError) as error:
            row.update(status='not_calculated', reason=str(error)[:300])
        cases.append(row)
    if glide:
        rf, erp = discounting['risk_free_rate'], discounting['equity_risk_premium']
        add('constant_initial_rate', 'The current discount rate held in every year and in the terminal value (no move toward market-average risk)',
            {}, flat=rates[0], applies=rates[0] - base['terminal_growth'] >= FRAGILE_TERMINAL_SPREAD,
            reason='Initial rate within two points of terminal growth; a constant-rate perpetuity is not meaningful')
        add('damodaran_default_terminal_rate', 'Terminal rate = risk-free rate + equity risk premium, whatever the leverage (Damodaran spreadsheet default)',
            {}, terminal=discounting['damodaran_default_terminal_rate'])
        add('terminal_growth_at_risk_free_rate', 'Terminal growth = risk-free rate, the long-run growth the equity risk premium assumes',
            {'terminal_growth': rf},
            applies=base['terminal_growth'] < rf - GROWTH_CAP_TOLERANCE and base['growth_rates'][-1] >= .01 and base['terminal_roic'] >= rf,
            reason='House terminal growth already equals the risk-free rate, or the business is declining or earns below the risk-free rate')
        add('tax_to_marginal_rate', 'Steady-state and terminal tax at the marginal rate (Damodaran default; applied to every firm it values the index several percent below its price)',
            {'terminal_tax_rate': discounting['marginal_tax_rate']},
            applies=abs(base.get('terminal_tax_rate', base['tax_rate']) - discounting['marginal_tax_rate']) > 1e-9,
            reason='Terminal tax already equals the marginal rate')
    add('terminal_return_equals_terminal_rate', 'Terminal return on new capital = terminal discount rate: the company earns no excess return on new investment, while the market risk premium still assumes the market does',
        {'terminal_roic': terminal}, applies=terminal >= base['terminal_growth'] and abs(base['terminal_roic'] - terminal) > 1e-9,
        reason='House terminal return already equals the terminal rate')
    # Damodaran's levels (owner choice, 2026-11-26): a lasting excess return of 4
    # points over the terminal cost of capital, and margins held at the best year.
    add('terminal_return_rate_plus_4_points', 'Terminal return on new capital = terminal discount rate + 4 points: a lasting but modest excess return on new investment',
        {'terminal_roic': terminal + .04}, applies=abs(base['terminal_roic'] - terminal - .04) > 1e-9 and terminal + .04 >= base['terminal_growth'],
        reason='House terminal return already equals the terminal rate plus 4 points')
    held = [min(value, reported_peak_margin) for value in base['ebit_margins']] if reported_peak_margin is not None else None
    add('base_margin_held_at_reported_peak', 'Base-case EBIT margin never above the reported five-year peak operating margin (no expansion beyond the best reported year)',
        {'ebit_margins': held}, applies=held is not None and reported_peak_margin > 0 and held != list(base['ebit_margins']),
        reason='No reported margin history, or the base case never exceeds the reported peak')
    add('terminal_return_15_percent', 'Terminal return on new capital 15% (Damodaran\'s override for durable advantages, used for Apple in 2024)',
        {'terminal_roic': .15}, applies=abs(base['terminal_roic'] - .15) > 1e-9 and .15 >= base['terminal_growth'],
        reason='House terminal return is already 15%')
    add('terminal_return_25_percent', 'Terminal return on new capital 25%: a claim that new investment keeps earning well above the market forever',
        {'terminal_roic': .25}, applies=abs(base['terminal_roic'] - .25) > 1e-9 and .25 >= base['terminal_growth'],
        reason='House terminal return is already 25%')
    continuing = margin * base['sales_to_capital'] * (1 + base['terminal_growth'])
    add('terminal_reinvestment_continues_forecast', 'Terminal reinvestment continues the forecast capital efficiency (no step at the end of the forecast)',
        {'terminal_roic': continuing}, applies=continuing > 0 and continuing >= base['terminal_growth'] and abs(continuing - base['terminal_roic']) > 1e-6,
        reason='Forecast capital efficiency already equals the terminal return')
    for shift in (-.01, .01):
        add(f'required_return_{"minus" if shift < 0 else "plus"}_1_point', f'Whole discount-rate path {shift * 100:+.0f} point',
            {}, shift=shift, kind='sensitivity')
    meaningful = [row for row in cases if row['kind'] == 'convention' and row['status'] == 'calculated' and row['dollar_delta'] is not None]
    largest = max(meaningful, key=lambda row: abs(row['dollar_delta']), default=None)
    return {'rows': cases, 'baseline_per_share_value': baseline,
            'largest_meaningful_step': largest['name'] if largest else None,
            'basis': 'Base scenario, one convention changed at a time from the house case; every other input fixed. '
                     'Convention rows are alternatives a careful analyst could defend; sensitivity rows show scale only and '
                     'are not ranked. These carry no probability, are not forecasts and are never tuned to the price.'}


def _beta_cross_checks(plan, actuals, base, baseline):
    """Reserved comparisons of the headline beta with pinned alternatives (non-optional)."""
    checks = plan.get('beta_cross_checks', [])
    if not checks:
        return []
    if not isinstance(checks, list) or len(checks) > 4 or 'discount_rate' not in plan:
        raise ValueError('beta_cross_checks needs a dated discount_rate and at most four cases')
    output = []
    for case in checks:
        if not isinstance(case, dict) or set(case) != {'name', 'label', 'unlevered_beta'}:
            raise ValueError('Each beta cross-check needs exactly name, label and unlevered_beta')
        trial = copy.deepcopy(plan)
        trial['discount_rate']['unlevered_beta'] = copy.deepcopy(case['unlevered_beta'])
        trial.pop('beta_cross_checks', None)
        for scenario in trial['scenarios'].values():
            scenario.pop('wacc', None)
        try:
            _, _, calculation = materialize(trial)
            scenario = _with_calculation(base, calculation)
            value = _project(actuals, scenario)['per_share_value']
            output.append({'name': case['name'], 'label': case['label'], 'unlevered_beta': calculation['provenance']['unlevered_beta'].get('value'),
                           'wacc': calculation['applied_wacc'], 'per_share_value': value, 'status': 'calculated',
                           **({'terminal_rate': scenario['terminal_rate'], 'terminal_growth': scenario['terminal_growth']}
                              if base.get('convention') == GLIDE else {}),
                           'value_change': _value_change(value, baseline)})
        except (ValueError, ArithmeticError, KeyError) as error:
            output.append({'name': case['name'], 'label': case['label'], 'status': 'not_calculated', 'reason': str(error)[:300]})
    return output


def save_valuation(folder, assumptions, results):
    """Write editable assumptions and independently computed JSON/CSV artifacts."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    paths = {name: folder / name for name in ('assumptions.json', 'valuation.json', 'cashflows.csv', 'sensitivity.csv')}
    for name, data in (('assumptions.json', assumptions), ('valuation.json', results)):
        paths[name].write_text(json.dumps(data, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    columns = ['scenario', 'year', 'revenue', 'revenue_growth', 'ebit_margin', 'ebit',
               'cash_operating_tax', 'nopat', 'net_reinvestment', 'fcff', 'discount_factor', 'pv_fcff']
    # Rate-path plans add per-year phase, rate and timing columns after the original ones.
    for name in SCENARIOS:
        for row in results['scenarios'][name]['cashflows']:
            columns += [key for key in row if key not in columns]
    with paths['cashflows.csv'].open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for name in SCENARIOS:
            for row in results['scenarios'][name]['cashflows']:
                writer.writerow(dict(row, scenario=name))
    sensitivity_columns = ['wacc', 'terminal_growth', 'per_share_value', 'enterprise_value']
    if results['sensitivity'] and 'rate_shift' in results['sensitivity'][0]:
        sensitivity_columns = ['rate_shift', 'initial_rate', 'terminal_rate', 'terminal_growth', 'per_share_value', 'enterprise_value', 'status']
    with paths['sensitivity.csv'].open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=sensitivity_columns)
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

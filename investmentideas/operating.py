"""Consolidate sourced revenue components into explicit operating forecasts.

The optional ``plan.operating_model`` schema is::

    {
      "currency": "USD", "amount_unit": "currency_units",
      "assumptions_label": "Forward growth, margins and expenses are house assumptions.",
      "earnings_mode": "gross_profit_less_expenses",
      "counterfactual_policy": "aggregate_override",
      "components": [
        {"name": "products", "base_revenue": 700, "source_refs": ["S1"],
         "gross_margin_group": "products", "disclosure": "Shared forward margin assumption."},
        {"name": "services", "base_revenue": 300, "source_refs": ["S1"]}
      ],
      "scenarios": {
        "bear": {
          "components": {
            "products": {"growth_rates": [.02, .02], "gross_margins": [.35, .35]},
            "services": {"growth_rates": [.08, .08], "gross_margins": [.75, .75]}
          },
          "expenses": {"research_and_development": [90, 95],
                       "selling_general_administrative": [60, 65]}
        },
        "base": {"...": "same structure"}, "bull": {"...": "same structure"}
      }
    }

Money is in whole currency units. Starting component revenue must reconcile to
``plan.actuals.revenue``; there is no residual component or balancing plug.
Source references support starting revenue, not the forward arrays. Optional
``gross_margin_group`` and ``disclosure`` are labels, never evidence that an
individual component's historical margin was disclosed. Caller verifies sources.

In ``aggregate_ebit_margins`` mode, omit component gross margins and expenses.
The main scenario's explicit ``ebit_margins`` is retained; unknown component
gross profits and expense details remain null, not zero or invented allocations.

``validate(plan)`` returns a normalized model, or None when absent.
``derive(plan)`` returns detailed operating forecasts, or None when absent.
``apply(plan)`` returns ``(copied_plan, forecasts)``. It fills omitted main
growth/margin arrays and rejects inconsistent duplicates instead of overwriting
them. Actuals, taxes, capital efficiency, discount rates and probabilities do not
change. GAAP R&D/SG&A must include their stock-compensation and rent costs under
the caller's accounting convention; no second compensation deduction is made.

Counterfactuals overriding growth or EBIT margins are rejected unless
``counterfactual_policy=aggregate_override`` is explicit. Such cases have no
derived component breakdown; they do not change the house operating schedules.
Other counterfactual inputs leave the base operating forecast intact. A reverse
DCF solving aggregate growth likewise does not establish component forecasts.

``derive_counterfactual(plan, operating_overrides)`` returns
``(base_inputs, detail)`` for a coherent company-driver case. Overrides may
partially replace existing base-component growth/margin arrays or existing
expense arrays, e.g. ``{"components": {"services": {"growth_rates": [...]}}}``.
Starting facts, horizon and house scenarios stay fixed. A plan counterfactual may
carry these in ``operating_overrides`` together with nonoperating forecast
overrides, but cannot also override aggregate growth_rates or ebit_margins.
"""
from __future__ import annotations

import copy
import math
import re


SCENARIOS = ('bear', 'base', 'bull')
MAX_COMPONENTS = 32
MAX_EXPENSES = 32
REQUIRED_EXPENSES = {'research_and_development', 'selling_general_administrative'}
FORECAST_FIELDS = {'growth_rates', 'ebit_margins', 'tax_rate', 'sales_to_capital',
                   'wacc', 'terminal_growth', 'terminal_roic'}


def _number(value, context):
    try:
        finite = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f'{context} must be a finite number')
    return float(value)


def _fields(value, allowed, required, context):
    if not isinstance(value, dict):
        raise ValueError(f'{context} must be an object')
    missing = required - value.keys()
    unknown = value.keys() - allowed
    if missing or unknown:
        raise ValueError(f'{context}: missing fields {sorted(map(str, missing))}; unsupported fields {sorted(map(str, unknown))}')


def _name(value, context):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', value):
        raise ValueError(f'{context} must be a 1–64 character name using letters, digits, underscores, dots or hyphens')
    return value


def _label(value, context):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{context} must be a nonempty disclosure')
    return value


def _array(value, context, horizon=None, lower=None, upper=None):
    if not isinstance(value, list) or not 1 <= len(value) <= 15:
        raise ValueError(f'{context} must be an annual array of 1–15 values')
    if horizon is not None and len(value) != horizon:
        raise ValueError(f'{context} must match the common forecast horizon of {horizon} years')
    numbers = [_number(item, context) for item in value]
    if any((lower is not None and item < lower) or (upper is not None and item > upper) for item in numbers):
        raise ValueError(f'{context} values must be between {lower} and {upper}')
    return numbers


def _sum(values, context):
    try:
        total = math.fsum(values)
    except (OverflowError, ValueError):
        raise ValueError(f'{context} arithmetic must remain finite') from None
    return _number(total, context)


def _validated_operating_overrides(model, overrides):
    """Validate partial changes to existing base forward drivers only."""
    allowed = {'components', 'expenses'} if model['earnings_mode'] == 'gross_profit_less_expenses' else {'components'}
    _fields(overrides, allowed, set(), 'operating_overrides')
    if not overrides:
        raise ValueError('operating_overrides must change at least one existing forward driver')
    result = {}
    if 'components' in overrides:
        components = overrides['components']
        existing = model['scenarios']['base']['components']
        _fields(components, set(existing), set(), 'operating_overrides.components')
        if not components:
            raise ValueError('operating_overrides.components cannot be empty')
        result['components'] = {}
        for name, fields in components.items():
            allowed_fields = {'growth_rates', 'gross_margins'} if model['earnings_mode'] == 'gross_profit_less_expenses' else {'growth_rates'}
            _fields(fields, allowed_fields, set(), f'operating_overrides.components.{name}')
            if not fields:
                raise ValueError(f'operating_overrides.components.{name} must change a forward array')
            result['components'][name] = {}
            for field, values in fields.items():
                lower, upper = (-.95, 3) if field == 'growth_rates' else (-1, 1)
                result['components'][name][field] = _array(values, f'operating_overrides.{name}.{field}', model['horizon'], lower, upper)
    if 'expenses' in overrides:
        expenses = overrides['expenses']
        existing = model['scenarios']['base']['expenses']
        _fields(expenses, set(existing), set(), 'operating_overrides.expenses')
        if not expenses:
            raise ValueError('operating_overrides.expenses cannot be empty')
        result['expenses'] = {name: _array(values, f'operating_overrides.expenses.{name}', model['horizon'], 0)
                              for name, values in expenses.items()}
    return result


def validate(plan):
    """Validate the optional model and return normalized inputs without mutation."""
    if not isinstance(plan, dict):
        raise ValueError('Plan must be an object')
    if 'operating_model' not in plan:
        return None
    model = plan['operating_model']
    required = {'currency', 'amount_unit', 'assumptions_label', 'earnings_mode', 'components', 'scenarios'}
    _fields(model, required | {'counterfactual_policy'}, required, 'operating_model')
    if not isinstance(plan.get('currency'), str) or not re.fullmatch(r'[A-Z]{3}', plan['currency']) or model['currency'] != plan['currency']:
        raise ValueError('operating_model.currency must match the explicit three-letter plan currency')
    if plan.get('amount_unit') != 'currency_units' or model['amount_unit'] != 'currency_units':
        raise ValueError('operating_model and plan amount_unit must both be currency_units')
    label = _label(model['assumptions_label'], 'operating_model.assumptions_label')
    mode = model['earnings_mode']
    if mode not in ('gross_profit_less_expenses', 'aggregate_ebit_margins'):
        raise ValueError('earnings_mode must be gross_profit_less_expenses or aggregate_ebit_margins')
    policy = model.get('counterfactual_policy', 'reject')
    if policy not in ('reject', 'aggregate_override'):
        raise ValueError('counterfactual_policy must be reject or aggregate_override')
    actuals = plan.get('actuals')
    if not isinstance(actuals, dict) or 'revenue' not in actuals:
        raise ValueError('Explicit actuals.revenue is required for component reconciliation')
    reported = _number(actuals['revenue'], 'actuals.revenue')
    if reported <= 0:
        raise ValueError('actuals.revenue must be positive')
    components = model['components']
    if not isinstance(components, list) or not 1 <= len(components) <= MAX_COMPONENTS:
        raise ValueError(f'Provide 1–{MAX_COMPONENTS} named revenue components')
    normalized = []
    names = set()
    for item in components:
        _fields(item, {'name', 'base_revenue', 'source_refs', 'gross_margin_group', 'disclosure'},
                {'name', 'base_revenue', 'source_refs'}, 'revenue component')
        name = _name(item['name'], 'component name')
        if name.casefold() in names:
            raise ValueError('Revenue component names must be unique, including case')
        names.add(name.casefold())
        revenue = _number(item['base_revenue'], f'{name}.base_revenue')
        if revenue < 0:
            raise ValueError('Starting component revenue cannot be negative')
        refs = item['source_refs']
        if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) and ref.strip() for ref in refs):
            raise ValueError(f'{name}.source_refs must explicitly identify starting revenue facts')
        component = {'name': name, 'base_revenue': revenue, 'source_refs': copy.deepcopy(refs)}
        for field in ('gross_margin_group', 'disclosure'):
            if field in item:
                component[field] = _label(item[field], f'{name}.{field}')
        normalized.append(component)
    total = _sum((item['base_revenue'] for item in normalized), 'Starting component revenue')
    tolerance = max(.01, abs(reported) * 1e-10)
    if abs(total - reported) > tolerance:
        raise ValueError('Starting component revenue does not reconcile to actuals.revenue; no silent plug is permitted')
    _fields(model['scenarios'], set(SCENARIOS), set(SCENARIOS), 'operating_model.scenarios')
    _fields(plan.get('scenarios'), set(SCENARIOS), set(SCENARIOS), 'plan.scenarios')
    definitions = {}
    component_names = {item['name'] for item in normalized}
    horizon = None
    for scenario in SCENARIOS:
        if not isinstance(plan['scenarios'][scenario], dict):
            raise ValueError(f'{scenario} scenario must be an object')
        entry = model['scenarios'][scenario]
        allowed = {'components', 'expenses'} if mode == 'gross_profit_less_expenses' else {'components'}
        _fields(entry, allowed, allowed, f'{scenario} operating forecast')
        _fields(entry['components'], component_names, component_names, f'{scenario}.components')
        cases = {}
        for item in normalized:
            name = item['name']
            definition = entry['components'][name]
            fields = {'growth_rates', 'gross_margins'} if mode == 'gross_profit_less_expenses' else {'growth_rates'}
            _fields(definition, fields, fields, f'{scenario}.{name}')
            growth = _array(definition['growth_rates'], f'{scenario}.{name}.growth_rates', horizon, -.95, 3)
            horizon = len(growth) if horizon is None else horizon
            case = {'growth_rates': growth}
            if mode == 'gross_profit_less_expenses':
                case['gross_margins'] = _array(definition['gross_margins'], f'{scenario}.{name}.gross_margins', horizon, -1, 1)
            cases[name] = case
        definition = {'components': cases}
        if mode == 'gross_profit_less_expenses':
            expenses = entry['expenses']
            if not isinstance(expenses, dict) or not REQUIRED_EXPENSES <= expenses.keys() or len(expenses) > MAX_EXPENSES:
                raise ValueError(f'{scenario}.expenses must explicitly include R&D and SG&A, using their full field names, and at most {MAX_EXPENSES} expense lines')
            expense_names = set()
            definition['expenses'] = {}
            for name, values in expenses.items():
                _name(name, 'Expense name')
                if name.casefold() in expense_names:
                    raise ValueError('Expense names must be unique, including case')
                expense_names.add(name.casefold())
                definition['expenses'][name] = _array(values, f'{scenario}.expenses.{name}', horizon, 0)
        else:
            scenario_inputs = plan['scenarios'][scenario]
            if not isinstance(scenario_inputs, dict) or 'ebit_margins' not in scenario_inputs:
                raise ValueError(f'{scenario}: revenue-only segmentation requires an explicit aggregate ebit_margins path')
            definition['ebit_margins'] = _array(scenario_inputs['ebit_margins'], f'{scenario}.ebit_margins', horizon, -1, 1)
        definitions[scenario] = definition
    model_inputs = {'earnings_mode': mode, 'horizon': horizon, 'scenarios': definitions}
    counterfactuals = plan.get('counterfactuals', {})
    if not isinstance(counterfactuals, dict):
        raise ValueError('counterfactuals must be an object')
    counterfactual_basis = {}
    for name, overrides in counterfactuals.items():
        if not isinstance(overrides, dict) or overrides.keys() - (FORECAST_FIELDS | {'operating_overrides'}):
            raise ValueError('Counterfactuals may override forecast inputs only, never actuals or probabilities')
        changed = sorted(overrides.keys() & {'growth_rates', 'ebit_margins'})
        if 'operating_overrides' in overrides:
            if changed:
                raise ValueError('Counterfactuals cannot mix operating_overrides with aggregate growth_rates or ebit_margins')
            operating = _validated_operating_overrides(model_inputs, overrides['operating_overrides'])
            changed_fields = [f'components.{component}.{field}' for component, fields in operating.get('components', {}).items() for field in fields]
            changed_fields.extend(f'expenses.{expense}' for expense in operating.get('expenses', {}))
            counterfactual_basis[name] = {
                'forecast_basis': 'component_operating_override', 'component_breakdown_derived': True,
                'overridden_operating_fields': sorted(changed_fields),
                'disclosure': 'Named base component and expense forward arrays are recomputed for this case; starting facts, house scenarios and probabilities stay fixed.'}
            continue
        if changed and policy != 'aggregate_override':
            raise ValueError(f'Counterfactual {name} overrides operating forecasts; explicitly select counterfactual_policy=aggregate_override or supply a coherent component model')
        counterfactual_basis[name] = {
            'forecast_basis': 'aggregate_override' if changed else 'operating_model_base',
            'component_breakdown_derived': not bool(changed),
            'overridden_operating_fields': changed,
            'disclosure': ('Aggregate counterfactual only: component revenue, gross profit and expense breakdowns are not derived for this case; house component schedules remain unchanged.' if changed else 'Base component revenue and operating earnings stay fixed; only the specified nonoperating forecast parameters change.')}
    return {'currency': model['currency'], 'amount_unit': 'currency_units',
            'earnings_mode': mode, 'assumptions_label': label,
            'counterfactual_policy': policy, 'components': normalized,
            'base_revenue': reported, 'horizon': horizon, 'scenarios': definitions,
            'base_revenue_reconciliation': {'reported_total': reported, 'component_total': total,
                                            'difference': total - reported, 'tolerance': tolerance},
            'counterfactual_basis': counterfactual_basis}


def derive(plan):
    """Compute component revenue and consolidated EBIT without modifying inputs."""
    model = validate(plan)
    if model is None:
        return None
    forecasts = {}
    for name in SCENARIOS:
        inputs = model['scenarios'][name]
        revenues = {item['name']: item['base_revenue'] for item in model['components']}
        previous_total = model['base_revenue']
        rows = []
        for year in range(model['horizon']):
            details = {}
            for item in model['components']:
                component = item['name']
                growth = inputs['components'][component]['growth_rates'][year]
                revenues[component] = _number(revenues[component] * (1 + growth), f'{name}.{component}.revenue')
                margin = (inputs['components'][component]['gross_margins'][year]
                          if model['earnings_mode'] == 'gross_profit_less_expenses' else None)
                gross = (_number(revenues[component] * margin, f'{name}.{component}.gross_profit')
                         if margin is not None else None)
                detail = {'revenue': revenues[component], 'revenue_growth': growth,
                          'gross_margin': margin, 'gross_profit': gross}
                for field in ('gross_margin_group', 'disclosure'):
                    if field in item:
                        detail[field] = item[field]
                details[component] = detail
            revenue = _sum(revenues.values(), f'{name}.revenue')
            if revenue <= 0:
                raise ValueError(f'{name}: consolidated revenue must remain positive; arithmetic underflow cannot create a zero denominator')
            growth = _number(revenue / previous_total - 1, f'{name}.revenue_growth')
            if model['earnings_mode'] == 'gross_profit_less_expenses':
                gross_profit = _sum((row['gross_profit'] for row in details.values()), f'{name}.gross_profit')
                expense_breakdown = {key: values[year] for key, values in inputs['expenses'].items()}
                expenses = _sum(expense_breakdown.values(), f'{name}.operating_expenses')
                ebit = _number(gross_profit - expenses, f'{name}.ebit')
                margin = _number(ebit / revenue, f'{name}.ebit_margin')
            else:
                gross_profit = expenses = expense_breakdown = None
                margin = inputs['ebit_margins'][year]
                ebit = _number(revenue * margin, f'{name}.ebit')
            if not -1 <= margin <= 1:
                raise ValueError(f'{name}: derived EBIT margin must be between -100% and 100% for the FCFF engine')
            rows.append({'year': year + 1, 'revenue': revenue, 'revenue_growth': growth,
                         'gross_profit': gross_profit, 'expense_breakdown': expense_breakdown,
                         'operating_expenses': expenses, 'ebit': ebit, 'ebit_margin': margin,
                         'components': details})
            previous_total = revenue
        forecasts[name] = {'growth_rates': [row['revenue_growth'] for row in rows],
                           'ebit_margins': [row['ebit_margin'] for row in rows], 'cashflows': rows}
    return {'model': 'sourced-revenue-components-v1', 'currency': model['currency'],
            'amount_unit': model['amount_unit'], 'earnings_mode': model['earnings_mode'],
            'assumptions_label': model['assumptions_label'], 'horizon': model['horizon'],
            'components': copy.deepcopy(model['components']),
            'base_revenue_reconciliation': model['base_revenue_reconciliation'],
            'scenarios': forecasts, 'counterfactual_basis': model['counterfactual_basis'],
            'disclosures': ['Starting revenues are sourced facts subject to caller verification; every forward growth, gross margin and expense array is a house assumption.',
                            'Gross-margin group labels do not establish observed margins for individual revenue components.',
                            'Operating expense arrays retain GAAP R&D, SG&A, stock compensation and operating rent under the stated convention; no additional compensation charge is introduced.',
                            'Revenue-only mode does not invent component gross profits or allocate group operating expenses.',
                            'Aggregate counterfactuals and reverse-DCF growth solutions do not imply a derived component forecast.']}


def apply(plan):
    """Return a copy with derived FCFF inputs, rejecting conflicting duplicates."""
    forecasts = derive(plan)
    result = copy.deepcopy(plan)
    if forecasts is None:
        return result, None
    for name in SCENARIOS:
        inputs = result['scenarios'][name]
        if not isinstance(inputs, dict):
            raise ValueError(f'{name} scenario must be an object')
        for field in ('growth_rates', 'ebit_margins'):
            derived = forecasts['scenarios'][name][field]
            if field in inputs:
                existing = _array(inputs[field], f'{name}.{field}', len(derived))
                if any(not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-10)
                       for actual, expected in zip(existing, derived)):
                    raise ValueError(f'{name}.{field} conflicts with operating_model; omit the duplicate or reconcile it explicitly')
            inputs[field] = list(derived)
    return result, forecasts


def derive_counterfactual(plan, operating_overrides):
    """Return derived base inputs and detailed company-driver counterfactual.

    Duplicate aggregate base forecasts are removed on a private copy so they
    cannot conceal or block the new component economics. In revenue-only mode,
    the explicit aggregate EBIT-margin path is an input and remains fixed.
    This helper does not apply separate WACC/tax/capital/terminal overrides;
    the valuation caller can apply those to the returned base_inputs afterward.
    """
    model = validate(plan)
    if model is None:
        raise ValueError('operating_overrides require an existing operating_model')
    overrides = _validated_operating_overrides(model, operating_overrides)
    trial = copy.deepcopy(plan)
    trial.pop('counterfactuals', None)
    base_model = trial['operating_model']['scenarios']['base']
    for component, fields in overrides.get('components', {}).items():
        for field, values in fields.items():
            base_model['components'][component][field] = list(values)
    for expense, values in overrides.get('expenses', {}).items():
        base_model['expenses'][expense] = list(values)
    trial['scenarios']['base'].pop('growth_rates', None)
    if model['earnings_mode'] == 'gross_profit_less_expenses':
        trial['scenarios']['base'].pop('ebit_margins', None)
    forecasts = derive(trial)
    detail = copy.deepcopy(forecasts['scenarios']['base'])
    inputs = copy.deepcopy(trial['scenarios']['base'])
    for field in ('growth_rates', 'ebit_margins'):
        inputs[field] = list(detail[field])
    detail.update(
        model='sourced-revenue-components-counterfactual-v1',
        currency=forecasts['currency'], amount_unit=forecasts['amount_unit'],
        earnings_mode=forecasts['earnings_mode'], assumptions_label=forecasts['assumptions_label'],
        operating_overrides=copy.deepcopy(operating_overrides),
        effective_component_inputs=copy.deepcopy(base_model),
        starting_components=copy.deepcopy(forecasts['components']),
        base_revenue_reconciliation=copy.deepcopy(forecasts['base_revenue_reconciliation']),
        component_breakdown_derived=True,
        fixed_input_basis={
            'reference_scenario': 'base', 'actuals': copy.deepcopy(plan['actuals']),
            'unchanged_base_inputs': {key: copy.deepcopy(value) for key, value in plan['scenarios']['base'].items()
                                      if key not in ('growth_rates', 'ebit_margins')},
            'house_scenarios_and_probabilities_unchanged': True,
            'disclosure': 'Only the listed existing component or expense forward arrays change. Starting revenue facts, currency, horizon, actuals, equity bridge and house probabilities stay fixed; no balancing plug or aggregate EBIT override is introduced.'})
    if model['earnings_mode'] == 'aggregate_ebit_margins':
        detail['fixed_input_basis']['retained_aggregate_ebit_margins'] = list(model['scenarios']['base']['ebit_margins'])
    return inputs, detail

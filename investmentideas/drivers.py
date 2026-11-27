"""Value impact of one driver group: a plan re-valued with another plan's growth, margins, capital, tax or rate.

Used by the forecast consensus (which group of inputs moves the value most across
the samples) and by tools/replay.py --plan-samples (the same around the median
sample). Each swap holds every other input of the plan it changes.
"""
from __future__ import annotations

import copy

from .valuation import SCENARIOS, evaluate

DRIVER_GROUPS = ('growth path', 'margin path', 'capital efficiency', 'tax', 'discount rate')
# A swap that cannot be valued is recorded as an error row; it never stops the caller.
SWAP_ERRORS = (ArithmeticError, AttributeError, IndexError, KeyError, TypeError, ValueError)


def _explicit_rows(result, name):
    scenario = result['scenarios'][name]
    rows = scenario.get('cashflows') or []
    years = scenario.get('explicit_forecast_years')
    return rows[:years] if isinstance(years, int) and years > 0 else rows


def _operating(trial, name):
    model = trial.get('operating_model')
    if not isinstance(model, dict):
        return None, None
    return model.get('earnings_mode'), (model.get('scenarios') or {}).get(name)


def _swap_growth(trial, result, donor_result):
    """The donor's total revenue path; the median's revenue mix and margin path are held."""
    for name in SCENARIOS:
        rows = _explicit_rows(result, name)
        target = [row['revenue_growth'] for row in _explicit_rows(donor_result, name)]
        if len(target) != len(rows):
            raise ValueError('the forecast horizons differ')
        scenario = trial['scenarios'][name]
        mode, operating = _operating(trial, name)
        if not isinstance(operating, dict):
            scenario['growth_rates'] = target
            continue
        factors, cumulative = [], 1.0
        for new, old in zip(target, (row['revenue_growth'] for row in rows)):
            cumulative *= (1 + new) / (1 + old)
            factors.append(((1 + new) / (1 + old), cumulative))
        for component in (operating.get('components') or {}).values():
            component['growth_rates'] = [(1 + growth) * factor - 1 for growth, (factor, _) in
                                         zip(component['growth_rates'], factors)]
        for line, values in (operating.get('expenses') or {}).items():
            # Expenses are dollars: scaled with revenue so each line keeps its share.
            operating['expenses'][line] = [value * level for value, (_, level) in zip(values, factors)]
        scenario.pop('growth_rates', None)
        if mode == 'gross_profit_less_expenses':
            scenario.pop('ebit_margins', None)


def _swap_margins(trial, result, donor_result):
    """The donor's EBIT margin path on the median's revenue; gross margins kept, expense lines scaled."""
    for name in SCENARIOS:
        rows = _explicit_rows(result, name)
        target = [row['ebit_margin'] for row in _explicit_rows(donor_result, name)]
        if len(target) != len(rows):
            raise ValueError('the forecast horizons differ')
        scenario = trial['scenarios'][name]
        mode, operating = _operating(trial, name)
        if mode == 'gross_profit_less_expenses' and isinstance(operating, dict):
            expenses = operating['expenses']
            for year, (row, margin) in enumerate(zip(rows, target)):
                total = sum(values[year] for values in expenses.values())
                wanted = total + (row['ebit_margin'] - margin) * row['revenue']
                if total <= 0 or wanted < 0:
                    raise ValueError(f'year {year + 1} margin cannot be reached by scaling operating expenses')
                for values in expenses.values():
                    values[year] *= wanted / total
            scenario.pop('ebit_margins', None)
        else:
            scenario['ebit_margins'] = target


def _copy_fields(trial, donor_plan, fields):
    for name in SCENARIOS:
        scenario, donor = trial['scenarios'][name], donor_plan['scenarios'][name]
        for field in fields:
            if field in donor:
                scenario[field] = copy.deepcopy(donor[field])
            else:
                scenario.pop(field, None)


def _swap_discount(trial, donor_plan):
    if ('discount_rate' in trial) != ('discount_rate' in donor_plan):
        raise ValueError('only one of the plans has a sourced discount-rate build')
    if 'discount_rate' in donor_plan:
        trial['discount_rate'] = copy.deepcopy(donor_plan['discount_rate'])
    _copy_fields(trial, donor_plan, ('wacc',))
    for name in SCENARIOS:
        scenario, donor = trial['scenarios'][name], donor_plan['scenarios'][name]
        # The terminal return is pinned to the terminal discount rate, so it moves with it.
        if scenario.get('terminal_roic_basis') == 'terminal_rate' and donor.get('terminal_roic_basis') == 'terminal_rate':
            scenario['terminal_roic'] = donor['terminal_roic']


def with_driver_group(plan, result, donor_plan, donor_result, group):
    """A copy of ``plan`` with one driver group taken from the donor sample."""
    trial = copy.deepcopy(plan)
    # Named cases and monitoring rows do not change scenario values and could
    # reject a swapped base path for reasons unrelated to the measurement.
    trial.pop('counterfactuals', None)
    trial.pop('monitoring', None)
    if group == 'growth path':
        _swap_growth(trial, result, donor_result)
    elif group == 'margin path':
        _swap_margins(trial, result, donor_result)
    elif group == 'capital efficiency':
        _copy_fields(trial, donor_plan, ('sales_to_capital', 'sales_to_capital_path'))
    elif group == 'tax':
        _copy_fields(trial, donor_plan, ('tax_rate', 'terminal_tax_rate'))
    elif group == 'discount rate':
        _swap_discount(trial, donor_plan)
    else:
        raise ValueError(f'unknown driver group {group}')
    return trial


def decompose(median, donors):
    """Re-value the median sample with each other sample's drivers, one group at a time.

    ``median`` and each donor are (sample number, locked plan, valuation). The range
    of each group spans the median's own base value and every swapped value.
    """
    number, plan, result = median
    own = result['scenarios']['base']['per_share_value']
    groups = {}
    for group in DRIVER_GROUPS:
        rows = []
        for donor_number, donor_plan, donor_result in donors:
            try:
                value = evaluate(with_driver_group(plan, result, donor_plan, donor_result, group))
                rows.append({'donor_sample': donor_number, 'base_value': value['scenarios']['base']['per_share_value']})
            except SWAP_ERRORS as exc:
                rows.append({'donor_sample': donor_number, 'error': str(exc)[:300]})
        values = [own] + [row['base_value'] for row in rows if 'base_value' in row]
        spread = max(values) - min(values)
        groups[group] = {'swaps': rows, 'min_base_value': min(values), 'max_base_value': max(values),
                         'range': spread, 'range_fraction_of_median': spread / own if own else None}
    largest = max(groups, key=lambda group: groups[group]['range']) if groups else None
    return {'median_sample': number, 'median_sample_base_value': own, 'groups': groups,
            'largest_group': largest,
            'basis': 'Each row re-values the median sample with one driver group from another sample and every other '
                     'input unchanged. Growth path: the donor\'s total revenue growth by year, with the median\'s '
                     'component mix and expense shares held. Margin path: the donor\'s EBIT margin by year on the '
                     'median\'s revenue (operating expenses scaled, gross margins kept). Capital efficiency: '
                     'sales-to-capital. Tax: forecast and terminal tax rates. Discount rate: the rate build, with the '
                     'terminal return that is pinned to the terminal rate. Groups interact, so the ranges do not add up.'}

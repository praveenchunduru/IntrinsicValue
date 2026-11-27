"""One forecast plan from several plans written independently on the same evidence.

Four plan samples on identical evidence and an identical prompt at temperature 0
(2026-11-27) valued Apple at 138-151 and Microsoft at 354-398 a share. Every
sample used the same segments and a similar shape: the differences are noise
around one view. The median of each numeric forecast input removes most of it.

The inputs are plans as the model proposed them (the parsed plan response), and
the output is a plan in the same form, so it goes through the lock, the
continuity checks and the engine exactly as a single model-written plan does.
The proposal level is the one where the model's own choices live: expense shares
are still shares (the lock turns them into dollars from each scenario's own
revenue), sales-to-capital is still the model's spec, and the Python-pinned
records (reported actuals, the pinned industry betas, market equity, rates,
terminal return, dates) are added afterwards by the same lock.

Numbers come from the median across samples; text (rationales, cited bases,
labels, counterfactuals, monitoring, calibration and repurchase cases) comes
from the representative sample, the one whose consolidated base-case revenue
and EBIT paths are closest to the per-year median paths. Nothing here reads or
compares against the market price.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import statistics

from . import drivers
from .valuation import _sales_to_capital_ratios

SCENARIOS = ('bear', 'base', 'bull')
BASIS_FIELDS = ('margin_continuity_basis', 'capital_efficiency_basis')
# Scenario fields the engine or the lock sets; the representative's value stays.
PINNED_SCENARIO_FIELDS = frozenset({'probability', 'terminal_roic', 'terminal_roic_basis', 'sales_to_capital_path',
                                    'rnd_share_path', 'rnd_basis'})
MAX_SECTORS = 8
ROUND = 10
# Measured on the saved samples (tools/consensus_check.py, 2026-11-27): the spread
# of the base value across 3-sample consensus plans was 7.1% (Microsoft) and 1.2%
# (Apple) with medians, 3.9% and 2.9% with means, and 2.9% and 1.0% with means of
# the forecast paths and the median of the industry weights. With three samples the
# mean uses all of them; the median keeps one sample's outlier industry mapping out.
DEFAULT_METHOD = 'mean'
DEFAULT_SECTOR_METHOD = 'median'


class ConsensusError(ValueError):
    """No plan could be formed from the samples."""


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _numbers(values):
    return isinstance(values, list) and bool(values) and all(_number(value) for value in values)


def _center(values, method):
    values = list(values)
    if method == 'mean':
        return round(math.fsum(values) / len(values), ROUND)
    return round(statistics.median(values), ROUND)


def _per_year(paths, method):
    horizon = min(len(path) for path in paths)
    return [_center([path[year] for path in paths], method) for year in range(horizon)]


# ------------------------------------------------------------------ structure

def _operating(plan):
    model = plan.get('operating_model')
    return model if isinstance(model, dict) else None


def _horizon(plan, name):
    """Forecast years of one scenario, read from the model's own arrays."""
    model = _operating(plan)
    if model:
        scenario = ((model.get('scenarios') or {}).get(name) or {}) if isinstance(model.get('scenarios'), dict) else {}
        components = scenario.get('components') if isinstance(scenario.get('components'), dict) else {}
        lengths = [len(item['growth_rates']) for item in components.values()
                   if isinstance(item, dict) and isinstance(item.get('growth_rates'), list)]
        return min(lengths) if lengths else None
    growth = ((plan.get('scenarios') or {}).get(name) or {}).get('growth_rates')
    return len(growth) if isinstance(growth, list) else None


def _beta_calculation(plan):
    rate = plan.get('discount_rate')
    if not isinstance(rate, dict):
        return None
    beta = rate.get('unlevered_beta')
    return beta.get('calculation') if isinstance(beta, dict) else 'value'


def structure(plan):
    """What a consensus needs to agree on: earnings mode, component names, horizon and rate form."""
    model = _operating(plan)
    names = tuple(sorted(str(item.get('name')) for item in (model or {}).get('components') or []
                         if isinstance(item, dict)))
    scenarios = plan.get('scenarios') if isinstance(plan.get('scenarios'), dict) else {}
    return {'earnings_mode': model.get('earnings_mode') if model else 'consolidated',
            'components': list(names),
            'horizon': [_horizon(plan, name) for name in SCENARIOS],
            'scenarios': sorted(scenarios),
            'discount_rate': isinstance(plan.get('discount_rate'), dict),
            'beta_calculation': _beta_calculation(plan)}


def _signature(plan):
    item = structure(plan)
    return (item['earnings_mode'], tuple(item['components']), tuple(item['horizon']), tuple(item['scenarios']),
            item['discount_rate'], item['beta_calculation'])


# ------------------------------------------------------------- consolidated paths

def _bases(model):
    return {item.get('name'): item.get('base_revenue') for item in model.get('components') or [] if isinstance(item, dict)}


def _component_revenue(bases, scenario):
    components = scenario.get('components') if isinstance(scenario.get('components'), dict) else {}
    paths = {}
    for name, base in bases.items():
        growth = (components.get(name) or {}).get('growth_rates')
        if not _number(base) or not _numbers(growth):
            return None
        level, path = base, []
        for rate in growth:
            level *= 1 + rate
            path.append(level)
        paths[name] = path
    return paths


def _scenario_revenue(bases, scenario):
    paths = _component_revenue(bases, scenario)
    if not paths:
        return None
    horizon = min(len(path) for path in paths.values())
    return [math.fsum(path[year] for path in paths.values()) for year in range(horizon)]


def _proposal_paths(plan, name, held_shares):
    """Consolidated revenue and EBIT by year from a proposal alone (used when no valuation is given)."""
    model = _operating(plan)
    scenario = ((plan.get('scenarios') or {}).get(name) or {})
    if model:
        operating = (model.get('scenarios') or {}).get(name) or {}
        bases = _bases(model)
        paths = _component_revenue(bases, operating)
        if not paths:
            return None
        horizon = min(len(path) for path in paths.values())
        revenue = [math.fsum(path[year] for path in paths.values()) for year in range(horizon)]
        if model.get('earnings_mode') == 'gross_profit_less_expenses':
            components = operating.get('components') or {}
            gross = [math.fsum(paths[item][year] * ((components.get(item) or {}).get('gross_margins') or [0] * horizon)[year]
                               for item in paths) for year in range(horizon)]
            shares = _expense_paths(operating, bases, horizon, name, held_shares)
            ebit = [gross[year] - math.fsum(values[year] for values in shares.values()) * revenue[year]
                    for year in range(horizon)]
        else:
            margins = scenario.get('ebit_margins')
            if not _numbers(margins):
                return None
            ebit = [revenue[year] * margins[year] for year in range(min(horizon, len(margins)))]
        return revenue, ebit
    growth, margins = scenario.get('growth_rates'), scenario.get('ebit_margins')
    if not (_numbers(growth) and _numbers(margins)):
        return None
    level, revenue = 1.0, []
    for rate in growth:
        level *= 1 + rate
        revenue.append(level)
    return revenue, [amount * margin for amount, margin in zip(revenue, margins)]


def _valued_paths(result, name='base'):
    scenario = ((result or {}).get('scenarios') or {}).get(name) or {}
    rows = scenario.get('cashflows') or []
    years = scenario.get('explicit_forecast_years')
    rows = rows[:years] if isinstance(years, int) and years > 0 else rows
    if not rows or not all(_number(row.get('revenue')) and _number(row.get('ebit_margin')) for row in rows):
        return None
    return [row['revenue'] for row in rows], [row['revenue'] * row['ebit_margin'] for row in rows]


def _distance(path, reference):
    """Mean absolute relative gap of revenue and EBIT by year from the reference paths."""
    gaps = []
    for mine, middle in zip(path, reference):
        for value, center in zip(mine, middle):
            if center:
                gaps.append(abs(value / center - 1))
    return math.fsum(gaps) / len(gaps) if gaps else math.inf


def _median_paths(paths):
    revenue = _per_year([path[0] for path in paths], 'median')
    ebit = _per_year([path[1] for path in paths], 'median')
    return revenue, ebit


def _closest(indices, paths):
    """The index whose base-case paths are closest to the per-year median paths; lowest index on a tie."""
    usable = [index for index in indices if paths[index]]
    if not usable:
        return indices[0], {index: None for index in indices}
    reference = _median_paths([paths[index] for index in usable])
    distances = {index: (_distance(paths[index], reference) if paths[index] else None) for index in indices}
    best = min(usable, key=lambda index: (distances[index], index))
    return best, distances


# ---------------------------------------------------------------- the inputs

def _expense_paths(operating, bases, horizon, name, held_shares):
    """Per-year expense shares of revenue, by line, as the lock would build them."""
    from . import pipeline
    spec = operating.get('expense_shares')
    if spec is None and isinstance(operating.get('expenses'), dict):
        revenue = _scenario_revenue(bases, operating)
        if revenue and all(amount > 0 for amount in revenue):
            return {line: [value / amount for value, amount in zip(values, revenue)]
                    for line, values in operating['expenses'].items() if _numbers(values)}
        return {}
    if not isinstance(spec, dict):
        return dict({line: [share] * horizon for line, share in (held_shares or {}).items()})
    paths = {line: pipeline._share_path(item, horizon, f'operating_model.scenarios.{name}.expense_shares.{line}')
             for line, item in spec.items()}
    paths = pipeline._combined_sga(paths, name)
    for line, share in (held_shares or {}).items():
        if line in ('research_and_development', 'selling_general_administrative') and line not in paths:
            paths[line] = [share] * horizon
    return paths


def _sales_to_capital(spec, horizon, name):
    try:
        return _sales_to_capital_ratios(spec, horizon, name)
    except (ValueError, TypeError):
        return None


class _Merge:
    def __init__(self, group, method, held_shares, notes, sector_method='median'):
        self.group, self.method, self.held, self.notes = group, method, held_shares, notes
        self.sector_method = sector_method

    def series(self, label, paths):
        return _per_year(paths, self.method)

    def scalar(self, label, values, method=None):
        return _center(values, method or self.method)


def _merge_scenario(merge, out, name, horizon, governed):
    plans = merge.group
    scenario = out['scenarios'][name]
    members = [((plan.get('scenarios') or {}).get(name) or {}) for plan in plans]
    for field in list(scenario):
        if field in PINNED_SCENARIO_FIELDS:
            continue
        if field in governed:
            # Python derives these from the operating model and would drop them.
            scenario.pop(field)
            continue
        value = scenario[field]
        if field == 'sales_to_capital':
            paths = [_sales_to_capital(member.get(field), horizon, name) for member in members]
            paths = [path for path in paths if path]
            if paths:
                path = merge.series(f'{name}.sales_to_capital', paths)
                scenario[field] = path[0] if all(ratio == path[0] for ratio in path) else path
            continue
        if _number(value):
            values = [member[field] for member in members if _number(member.get(field))]
            if len(values) < len(members):
                merge.notes.append(f'{name}.{field} is given in {len(values)} of {len(members)} samples; '
                                   'the median is over those samples')
            scenario[field] = merge.scalar(f'{name}.{field}', values)
        elif _numbers(value):
            paths = [member[field] for member in members if _numbers(member.get(field)) and len(member[field]) == len(value)]
            scenario[field] = merge.series(f'{name}.{field}', paths)


def _merge_operating(merge, out, name, horizon):
    model = out['operating_model']
    scenario = (model.get('scenarios') or {}).get(name)
    if not isinstance(scenario, dict):
        return
    members = [((plan['operating_model'].get('scenarios') or {}).get(name) or {}) for plan in merge.group]
    for component, item in (scenario.get('components') or {}).items():
        if not isinstance(item, dict):
            continue
        rows = [((member.get('components') or {}).get(component) or {}) for member in members]
        for field, value in item.items():
            if _numbers(value):
                paths = [row[field] for row in rows if _numbers(row.get(field)) and len(row[field]) == len(value)]
                item[field] = merge.series(f'{name}.{component}.{field}', paths)
            elif _number(value):
                item[field] = merge.scalar(f'{name}.{component}.{field}',
                                           [row[field] for row in rows if _number(row.get(field))])
    if model.get('earnings_mode') != 'gross_profit_less_expenses':
        return
    bases = [_bases(plan['operating_model']) for plan in merge.group]
    # Each sample's shares as its own lock builds them: a left-out R&D or SG&A line
    # holds its reported share, and every reported share is held without expense_shares.
    shares = [_expense_paths(member, base, horizon, name, merge.held) for member, base in zip(members, bases)]
    lines = list(dict.fromkeys(line for paths in shares for line in paths))
    if not lines:
        return
    result = {}
    for line in lines:
        paths = [paths[line] for paths in shares if line in paths]
        if len(paths) < len(shares):
            if merge.held is not None:
                # The lock gives that sample no such line.
                paths += [[0.0] * horizon] * (len(shares) - len(paths))
            else:
                merge.notes.append(f'{name} expense line {line} is given in {len(paths)} of {len(shares)} samples; '
                                   'the median is over those samples')
        result[line] = merge.series(f'{name}.expense_shares.{line}', paths)
    scenario.pop('expenses', None)
    scenario['expense_shares'] = result


def _merge_rate(merge, out):
    rate = out.get('discount_rate')
    if not isinstance(rate, dict):
        return
    members = [plan.get('discount_rate') or {} for plan in merge.group]
    for key, record in rate.items():
        if not isinstance(record, dict):
            continue
        rows = [member.get(key) if isinstance(member.get(key), dict) else {} for member in members]
        if _number(record.get('value')):
            values = [row['value'] for row in rows if _number(row.get('value'))]
            record['value'] = merge.scalar(f'discount_rate.{key}', values)
        if key == 'unlevered_beta' and _number(record.get('historical_equity_beta')):
            values = [row['historical_equity_beta'] for row in rows if _number(row.get('historical_equity_beta'))]
            record['historical_equity_beta'] = merge.scalar('discount_rate.unlevered_beta.historical_equity_beta', values)
        if key == 'unlevered_beta' and isinstance(record.get('sectors'), list):
            record['sectors'] = _merge_sectors(merge, record['sectors'], rows)
            record['label'] = (str(record.get('label') or '').rstrip() + ' Weights are the ' +
                               getattr(merge, 'sector_used', merge.sector_method) + f' across {len(rows)} forecast '
                               'samples, with an industry a sample did not use counted as zero, then rescaled to one.').strip()


def _merge_sectors(merge, sectors, rows):
    """Median weight per industry (zero where a sample leaves it out), renormalized."""
    entries = {}
    for item in [*sectors, *(entry for row in rows for entry in (row.get('sectors') or []))]:
        if isinstance(item, dict) and isinstance(item.get('sector'), str) and item['sector'] not in entries:
            entries[item['sector']] = item
    weights = {}
    for name in entries:
        values = []
        for row in rows:
            match = [entry.get('weight') for entry in row.get('sectors') or []
                     if isinstance(entry, dict) and entry.get('sector') == name]
            values.append(match[0] if match and _number(match[0]) else 0.0)
        weights[name] = merge.scalar(f'discount_rate.unlevered_beta.{name}.weight', values, merge.sector_method)
    kept = [name for name in entries if weights[name] > 0]
    if len(kept) > MAX_SECTORS:
        kept = sorted(kept, key=lambda name: (-weights[name], list(entries).index(name)))[:MAX_SECTORS]
        kept = [name for name in entries if name in kept]
        merge.notes.append(f'More than {MAX_SECTORS} industries kept a weight; the {MAX_SECTORS} largest are used')
    dropped = [name for name in entries if name not in kept]
    if dropped and any(weights[name] > 0 for name in kept):
        merge.notes.append(f'Industries with a {merge.sector_method} weight of zero were dropped from the beta: ' +
                           ', '.join(dropped))
    total = math.fsum(weights[name] for name in kept)
    if total <= 0 and merge.sector_method == 'median':
        # Every sample mapped the business to different industries: the mean keeps each mapping at its share.
        merge.notes.append('No industry kept a positive median weight, so the beta uses the mean industry weights.')
        merge.notes[:] = [note for note in merge.notes if not note.startswith('Industries with a median weight of zero')]
        mean = _Merge(merge.group, merge.method, merge.held, merge.notes, 'mean')
        merge.sector_used = 'mean'
        return _merge_sectors(mean, sectors, rows)
    if total <= 0:
        raise ConsensusError('No industry kept a positive weight')
    merged = []
    for name in kept:
        item = {key: value for key, value in copy.deepcopy(entries[name]).items()
                if key not in ('cash_corrected_unlevered_beta', 'firms')}
        item['weight'] = weights[name] / total
        merged.append(item)
    return merged


def _merge(group, representative, method, held_shares, notes, sector_method):
    out = copy.deepcopy(representative)
    merge = _Merge(group, method, held_shares, notes, sector_method)
    model = _operating(out)
    governed = (('growth_rates', 'ebit_margins') if model and model.get('earnings_mode') == 'gross_profit_less_expenses'
                else ('growth_rates',) if model else ())
    for name in SCENARIOS:
        if name not in (out.get('scenarios') or {}):
            continue
        horizon = _horizon(out, name)
        _merge_scenario(merge, out, name, horizon, governed)
        if model:
            _merge_operating(merge, out, name, horizon)
    _merge_rate(merge, out)
    return out


# ------------------------------------------------------------------ the plan

def _values(result):
    scenarios = (result or {}).get('scenarios') or {}
    return {name: (scenarios.get(name) or {}).get('per_share_value') for name in SCENARIOS}


def _basis_donors(group, plans, order, numbers):
    donors = {}
    for field in BASIS_FIELDS:
        for name in SCENARIOS:
            have = [numbers[index] for index in order if index in group and
                    isinstance((plans[index].get(field) or {}).get(name), str)]
            if have:
                donors.setdefault(field, {})[name] = have
    return donors


def consensus_plan(plans, *, valuations=None, held_shares=None, method=DEFAULT_METHOD,
                   sector_method=DEFAULT_SECTOR_METHOD, numbers=None):
    """One plan from K proposals: the median (or mean) of each numeric forecast input.

    ``plans`` are parsed plan responses; ``valuations`` the engine result of each
    (used for the report and to find the representative sample; without them the
    consolidated paths are rebuilt from the proposals). ``held_shares`` are the
    reported expense shares a plan holds for a line it leaves out. Industry beta
    weights use ``sector_method``. ``numbers`` label the samples in the report
    (default 1..K). Returns ``(plan, report)``; ``plan`` is in proposal form, for
    the normal lock.
    """
    if not isinstance(plans, list) or not plans or not all(isinstance(plan, dict) for plan in plans):
        raise ConsensusError('consensus_plan needs a list of plan objects')
    if method not in ('median', 'mean') or sector_method not in ('median', 'mean'):
        raise ConsensusError('method and sector_method must be median or mean')
    if valuations is not None and len(valuations) != len(plans):
        raise ConsensusError('one valuation per plan is needed')
    count = len(plans)
    numbers = list(numbers) if numbers is not None else list(range(1, count + 1))
    if len(numbers) != count or len(set(numbers)) != count:
        raise ConsensusError('one distinct sample number per plan is needed')
    paths = [(_valued_paths(valuations[index]) if valuations else None) or
             _proposal_paths(plans[index], 'base', held_shares) for index in range(count)]
    groups = {}
    for index, plan in enumerate(plans):
        groups.setdefault(_signature(plan), []).append(index)
    ranked = sorted(groups.values(), key=lambda members: (-len(members), members[0]))
    report = {'method': method, 'sector_weight_method': sector_method, 'sample_count': count, 'notes': [],
              'samples': [{'sample': numbers[index], 'structure': structure(plans[index]),
                           'values': _values(valuations[index]) if valuations else None} for index in range(count)]}
    if count == 1:
        report.update(method='single_sample', representative_sample=numbers[0],
                      structure_decision={'decision': 'single_sample', 'group': [numbers[0]], 'excluded': []})
        report['samples'][0]['in_consensus'] = True
        return copy.deepcopy(plans[0]), report
    largest = ranked[0]
    if len(largest) == 1:
        # No two samples agree: the one closest to the per-year median paths of all of them.
        chosen, distances = _closest(list(range(count)), paths)
        for index in range(count):
            report['samples'][index].update(distance_to_median_paths=distances[index], in_consensus=index == chosen)
        report.update(method='closest_single_sample', representative_sample=numbers[chosen],
                      structure_decision={'decision': 'no_two_samples_share_a_structure', 'group': [numbers[chosen]],
                                          'excluded': [numbers[index] for index in range(count) if index != chosen]})
        report['notes'].append(f'No two of the {count} samples share a structure (earnings mode, components, horizon and '
                               f'rate form), so the plan is sample {numbers[chosen]}, the one closest to the per-year '
                               'median of the consolidated base-case revenue and EBIT paths; no inputs were combined.')
        return copy.deepcopy(plans[chosen]), report
    ties = [members for members in ranked if len(members) == len(largest)]
    if len(ties) > 1:
        # Equal groups: the one holding the sample closest to the median paths of all samples.
        best, _ = _closest(list(range(count)), paths)
        largest = next((members for members in ties if best in members), ties[0])
    group = list(largest)
    representative, distances = _closest(group, paths)
    for index in range(count):
        report['samples'][index].update(in_consensus=index in group, distance_to_median_paths=distances.get(index))
    excluded = [numbers[index] for index in range(count) if index not in group]
    report['structure_decision'] = {
        'decision': 'all_samples_share_a_structure' if not excluded else 'largest_group_sharing_a_structure',
        'group': [numbers[index] for index in group], 'excluded': excluded, 'structure': structure(plans[group[0]])}
    if excluded:
        report['notes'].append(f'Sample{"s" if len(excluded) > 1 else ""} {", ".join(map(str, excluded))} '
                               f'use{"" if len(excluded) > 1 else "s"} a different structure and '
                               f'{"are" if len(excluded) > 1 else "is"} left out of the consensus.')
    if len(group) == 2:
        # The median of two is their mean; the industry weights then average too.
        report['notes'].append('Only two samples share a structure, so the consensus is their mean.')
    members = [plans[index] for index in group]
    try:
        plan = _merge(members, plans[representative], method, held_shares, report['notes'], sector_method)
    except LOCK_ERRORS as exc:
        # Valid samples whose inputs cannot be combined: the closest one, whole, with the reason.
        report.update(method='uncombinable_closest_sample', representative_sample=numbers[representative],
                      combine_error=str(exc)[:300])
        report['structure_decision'] = {**report['structure_decision'], 'combined_group': report['structure_decision']['group'],
                                        'group': [numbers[representative]]}
        for index in range(count):
            report['samples'][index]['in_consensus'] = index == representative
        report['notes'].append(f'The samples could not be combined ({str(exc)[:200]}), so the plan is sample '
                               f'{numbers[representative]}, the one closest to their per-year median revenue and EBIT paths.')
        return copy.deepcopy(plans[representative]), report
    order = sorted(group, key=lambda index: ((distances.get(index) if distances.get(index) is not None else math.inf), index))
    positions = {str(numbers[index]): position + 1 for position, index in enumerate(group)}
    report.update(forecast_positions=positions, text_label_count=len(group), held_shares=held_shares)
    report['text_figures'] = _align_plan_text(plan, plans[representative], held_shares, positions[str(numbers[representative])],
                                              len(group), method)
    report.update(representative_sample=numbers[representative],
                  representative_basis='closest consolidated base-case revenue and EBIT paths to the per-year median of '
                                       'the samples in the consensus (mean absolute relative gap)',
                  basis_donors=_basis_donors(set(group), plans, order, numbers))
    report['notes'] = list(dict.fromkeys(report['notes']))
    return plan, report


# ------------------------------------------------------- text of one sample
#
# The rationales and cited bases come from one sample, so a figure they restate is
# that sample's input. Where such a figure matches exactly one of the sample's own
# inputs at the precision written, it is rewritten to the combined input at that
# precision; any other figure is left, and the text is labelled as written for one
# of the forecasts. A figure followed by a citation before the next figure is a
# sourced fact and is never touched.

FIGURE = re.compile(r'(?<![\w$.\-])(-?\d+(?:\.\d+)?)(\s?%|\s?percent\b)?')
UNITS_AFTER = re.compile(r'\s?(?:bn|billion|million|m\b|x\b|years?\b|bps\b)', re.IGNORECASE)
CITATION = re.compile(r'\[S\d+\]')


def _ends(path):
    return [path[0], path[-1]] if _numbers(path) else []


def _figure_inputs(plan, name, held_shares):
    """(kind, label, value) of one scenario's inputs a rationale may restate: first and last forecast years."""
    rows = []
    scenario = (plan.get('scenarios') or {}).get(name) or {}
    for field in ('tax_rate', 'terminal_tax_rate'):
        if _number(scenario.get(field)):
            rows.append(('pct', f'{name}.{field}', scenario[field]))
    for field in ('growth_rates', 'ebit_margins'):
        for value in _ends(scenario.get(field)):
            rows.append(('pct', f'{name}.{field}', value))
    horizon = _horizon(plan, name)
    ratios = _sales_to_capital(scenario.get('sales_to_capital'), horizon, name) if horizon else None
    for label, value in zip(('year 1', 'target'), _ends(ratios or [])):
        rows.append(('ratio', f'{name}.sales_to_capital {label}', value))
    model = _operating(plan)
    operating = ((model or {}).get('scenarios') or {}).get(name) or {}
    for component, item in (operating.get('components') or {}).items():
        for field in ('growth_rates', 'gross_margins'):
            for value in _ends((item or {}).get(field)):
                rows.append(('pct', f'{name}.{component}.{field}', value))
    if model and model.get('earnings_mode') == 'gross_profit_less_expenses' and horizon:
        try:
            shares = _expense_paths(operating, _bases(model), horizon, name, held_shares)
        except LOCK_ERRORS:
            shares = {}
        for line, path in shares.items():
            for value in _ends(path):
                rows.append(('pct', f'{name}.expense_shares.{line}', value))
    return rows


def _pairs(own, combined):
    """(kind, label, own value, combined value) for inputs present in both, in order."""
    mine = {}
    for kind, label, value in own:
        mine.setdefault((kind, label), []).append(value)
    theirs = {}
    for kind, label, value in combined:
        theirs.setdefault((kind, label), []).append(value)
    return [(kind, label, a, b) for (kind, label), values in mine.items()
            for a, b in zip(values, theirs.get((kind, label), []))]


def _written(value, kind, decimals):
    number = value * 100 if kind == 'pct' else value
    return round(number, decimals)


def _format(number, decimals):
    text = f'{number:.{decimals}f}'
    return '0' + text[2:] if text.startswith('-0') and float(text) == 0 else text


def align_figures(text, own_plan, combined_plan, name, held_shares=None):
    """Rewrite figures in ``text`` that restate one of ``own_plan``'s inputs to ``combined_plan``'s, where unambiguous.

    Returns (text, changes, left): ``changes`` lists each rewrite, ``left`` each
    figure that matched an input but could not be rewritten unambiguously.
    """
    if not isinstance(text, str) or not text:
        return text, [], []
    pairs = _pairs(_figure_inputs(own_plan, name, held_shares), _figure_inputs(combined_plan, name, held_shares))
    tokens = list(FIGURE.finditer(text))
    out, cursor, changes, left = [], 0, [], []
    for index, token in enumerate(tokens):
        number, unit = token.group(1), token.group(2)
        following = text[token.end():tokens[index + 1].start() if index + 1 < len(tokens) else len(text)]
        sentence_end = re.search(r'[.;](?:\s|$)', following)
        clause = following[:sentence_end.start()] if sentence_end else following
        if CITATION.search(clause) or (not unit and (UNITS_AFTER.match(following) or '.' not in number)):
            continue
        kind = 'pct' if unit else 'ratio'
        decimals = len(number.split('.')[1]) if '.' in number else 0
        shown = float(number)
        matches = [(label, b) for k, label, a, b in pairs if k == kind and _written(a, kind, decimals) == shown]
        if not matches:
            continue
        targets = sorted({_format(_written(b, kind, decimals), decimals) for _, b in matches})
        if len(targets) != 1:
            left.append({'figure': token.group(0), 'inputs': [label for label, _ in matches], 'reason': 'ambiguous'})
            continue
        if targets[0] != number:
            out += [text[cursor:token.start(1)], targets[0]]
            cursor = token.end(1)
            changes.append({'from': token.group(0).strip(), 'to': targets[0] + (unit or ''),
                            'inputs': [label for label, _ in matches]})
    out.append(text[cursor:])
    return ''.join(out), changes, left


def label_text(text, position, count, method):
    """The label of text written with one of several forecasts."""
    kind = 'averages' if method == 'mean' or count == 2 else 'medians'
    suffix = f'Written with forecast {position} of {count}; the valued inputs are the {kind} in the tables.'
    if not isinstance(text, str) or suffix in text:
        return text
    return text.rstrip() + ('' if text.rstrip().endswith(('.', '!', '?')) else '.') + ' ' + suffix


def _align_plan_text(plan, own_plan, held_shares, position, count, method):
    """Align and label the representative's rationales and bases in the combined plan; return the record."""
    record = {'changed': [], 'left': []}
    for field in ('scenario_rationales', *BASIS_FIELDS):
        texts = plan.get(field)
        if not isinstance(texts, dict):
            continue
        for name, text in list(texts.items()):
            if not isinstance(text, str):
                continue
            new, changes, left = align_figures(text, own_plan, plan, name, held_shares)
            record['changed'] += [{'field': f'{field}.{name}', **item} for item in changes]
            record['left'] += [{'field': f'{field}.{name}', **item} for item in left]
            texts[name] = label_text(new, position, count, method)
    return record


def closest_sample(plans, valuations, numbers, reason):
    """The valid sample closest to the per-year median revenue and EBIT paths, whole, when the samples cannot be combined."""
    paths = [_valued_paths(result) for result in valuations]
    chosen, distances = _closest(list(range(len(plans))), paths)
    report = {'method': 'uncombinable_closest_sample', 'sector_weight_method': None, 'sample_count': len(plans),
              'representative_sample': numbers[chosen], 'combine_error': str(reason)[:300],
              'structure_decision': {'decision': 'samples_could_not_be_combined', 'group': [numbers[chosen]],
                                     'excluded': [numbers[index] for index in range(len(plans)) if index != chosen]},
              'samples': [{'sample': numbers[index], 'structure': structure(plans[index]), 'values': _values(valuations[index]),
                           'in_consensus': index == chosen, 'distance_to_median_paths': distances[index]}
                          for index in range(len(plans))],
              'notes': [f'The samples could not be combined ({str(reason)[:200]}), so the plan is sample {numbers[chosen]}, '
                        'the one closest to their per-year median revenue and EBIT paths.']}
    return copy.deepcopy(plans[chosen]), report


# ------------------------------------------------------------- lock and check

LOCK_ERRORS = (ValueError, KeyError, TypeError, AttributeError, ZeroDivisionError, OverflowError)
# Methods whose plan is one sample, whole: nothing is combined and its text matches its own inputs.
SINGLE_METHODS = ('single_sample', 'closest_single_sample', 'uncombinable_closest_sample')


def lock_consensus(plan, report, lock, samples):
    """Lock the consensus; add a missing cited basis from a donor; else fall back to the representative.

    ``lock(plan)`` locks, checks and values a proposal, raises on failure and
    returns ``(result, payload)``: the engine valuation and anything the caller
    wants back. ``samples`` maps the report's sample numbers to their proposals
    (a list is numbered from 1). Returns
    ``(plan_used, payload)`` and completes the report: any basis taken from
    another sample, any fallback, and the values of the plan used.
    """
    try:
        result, payload = lock(plan)
        finish_report(report, result, report['method'] if report.get('method') in SINGLE_METHODS else 'consensus')
        return plan, payload
    except LOCK_ERRORS as exc:
        first = str(exc)
    if report.get('method') in SINGLE_METHODS:
        raise ValueError(first)
    samples = samples if isinstance(samples, dict) else {index + 1: item for index, item in enumerate(samples)}
    added = []
    trial = copy.deepcopy(plan)
    for field, scenarios in (report.get('basis_donors') or {}).items():
        for name, donors in scenarios.items():
            own = trial.get(field) if isinstance(trial.get(field), dict) else {}
            if isinstance(own.get(name), str):
                continue
            text = samples[donors[0]][field][name]
            donor = samples[donors[0]]
            if report.get('forecast_positions', {}).get(str(donors[0])):
                text, _changes, _open = align_figures(text, donor, trial, name, report.get('held_shares'))
                text = label_text(text, report['forecast_positions'][str(donors[0])], report['text_label_count'],
                                  report.get('method'))
            trial[field] = {**own, name: text}
            added.append((field, name, donors[0]))
    passed = False
    if added:
        try:
            lock(trial)
            passed = True
        except LOCK_ERRORS:
            pass
    if passed:
        # Keep only the bases the consensus needs, in a fixed order.
        for field, name, donor in list(added):
            smaller = copy.deepcopy(trial)
            smaller[field].pop(name)
            if not smaller[field]:
                smaller.pop(field)
            try:
                lock(smaller)
            except LOCK_ERRORS:
                continue
            trial = smaller
            added.remove((field, name, donor))
        result, payload = lock(trial)
        report['bases_from_other_samples'] = [{'field': field, 'scenario': name, 'sample': donor}
                                              for field, name, donor in added]
        if added:
            report['notes'].append('The consensus departs from the reported run-rate where the representative sample '
                                   'does not, so its cited basis comes from ' +
                                   '; '.join(f'sample {donor} ({field}.{name})' for field, name, donor in added) + '.')
        finish_report(report, result, 'consensus')
        return trial, payload
    fallback = report['representative_sample']
    report['fallback'] = {'to_sample': fallback, 'reason': first[:600]}
    report['notes'].append(f'The consensus failed a check that the samples passed ({first[:300]}); the plan is the '
                           f'representative sample {fallback} unchanged.')
    candidate = copy.deepcopy(samples[fallback])
    result, payload = lock(candidate)
    finish_report(report, result, 'representative_sample_fallback')
    return candidate, payload


def finish_report(report, result, used):
    """Record which plan was valued, its values, and the range of the samples in the consensus."""
    report['plan_used'] = used
    report['consensus_values'] = _values(result)
    ranges = {}
    for name in SCENARIOS:
        numbers = [row['values'][name] for row in report['samples']
                   if row.get('in_consensus', True) and row.get('values') and _number(row['values'].get(name))]
        if numbers:
            ranges[name] = {'min': min(numbers), 'max': max(numbers), 'median': statistics.median(numbers)}
    report['sample_value_range'] = ranges
    return report


WORDS = {1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five'}


def method_note(report, currency=''):
    """One sentence or two for the Method section, from a finished report; None for one requested forecast."""
    requested = report.get('requested_samples', report['sample_count'])
    if requested == 1:
        return None
    whole = report.get('method') in SINGLE_METHODS or report.get('plan_used') == 'representative_sample_fallback'
    rows = [row for row in report['samples'] if (whole or row.get('in_consensus')) and
            _number((row.get('values') or {}).get('base'))]
    unit = f' {currency}' if currency else ''
    spread = (f'; their base values ranged from {min(row["values"]["base"] for row in rows):,.2f} to '
              f'{max(row["values"]["base"] for row in rows):,.2f}{unit} a share' if len(rows) > 1 else '')
    dropped = report.get('dropped') or []
    tail = (f' {WORDS.get(len(dropped), len(dropped)).capitalize()} further forecast'
            f'{"s" if len(dropped) > 1 else ""} failed validation after {"their" if len(dropped) > 1 else "its"} '
            'repairs and {} left out.'.format('were' if len(dropped) > 1 else 'was') if dropped else '')
    count = report['sample_count']
    if report.get('method') == 'single_sample':
        return (f'{WORDS.get(requested, requested).capitalize()} forecasts were requested from the same evidence; only '
                'one passed validation, and it is the forecast valued.' + tail)
    total = WORDS.get(count, str(count))
    if whole:
        why = ('the combined plan failed a forecast check the individual forecasts passed' if report.get('fallback')
               else 'their inputs could not be combined' if report.get('method') == 'uncombinable_closest_sample'
               else 'no two forecasts shared a structure')
        return (f'{total.capitalize()} forecasts were written independently from the same evidence{spread}. The forecast '
                f'valued is the one closest to their per-year median revenue and EBIT paths, because {why}.' + tail)
    used = len(report['structure_decision']['group'])
    which = (f'{total} forecasts written independently from the same evidence' if used == count else
             f'the {WORDS.get(used, used)} of {total} independently written forecasts that share one structure')
    if report.get('method') == 'mean' and report.get('sector_weight_method') == 'median' and used > 2:
        head = (f'The forecast averages {which}, input by input, with the median of their industry weights for the '
                'beta')
    else:
        head = f'The forecast is the {"mean" if report.get("method") == "mean" or used == 2 else "median"} of {which}'
    return head + spread + '.' + tail


# ----------------------------------------------------------- value impact

def value_impact(plan, result, donors):
    """Which input group moves the value most across the samples, by re-valuing the plan valued.

    ``plan`` and ``result`` are the locked consensus and its valuation; ``donors``
    are (sample number, locked plan, valuation) for each sample in the consensus.
    Each row swaps one driver group (growth path, margin path, capital efficiency,
    tax, discount rate) from one sample into the consensus and holds the rest, as
    ``tools/replay.py --plan-samples`` does around its median sample. Groups are
    ranked by the range of base values they produce.
    """
    try:
        report = drivers.decompose((0, plan, result), donors)
    except Exception as exc:  # noqa: BLE001 -- a diagnostic never stops the plan stage
        return {'basis': 'The value-impact step failed; nothing was ranked.',
                'error': f'{type(exc).__name__}: {str(exc)[:300]}', 'groups': []}
    rows = []
    for group, item in report['groups'].items():
        swaps = [row for row in item['swaps'] if 'base_value' in row]
        rows.append({'group': group, 'min_base_value': item['min_base_value'], 'max_base_value': item['max_base_value'],
                     'range': item['range'], 'range_fraction': item['range_fraction_of_median'],
                     'swaps': item['swaps'], 'valid_swaps': len(swaps)})
    rows.sort(key=lambda row: (-row['range'], drivers.DRIVER_GROUPS.index(row['group'])))
    return {'basis': 'Each row re-values the plan valued with one sample\'s driver group and every other input '
                     'unchanged; groups interact, so the ranges do not add up.', 'groups': rows}


# ------------------------------------------------------------ the record

def fingerprint(plan):
    """A digest of the forecast inputs of a locked plan, to tell later whether the plan valued was edited."""
    model = plan.get('operating_model') if isinstance(plan.get('operating_model'), dict) else {}
    rate = plan.get('discount_rate') if isinstance(plan.get('discount_rate'), dict) else {}
    beta = rate.get('unlevered_beta') if isinstance(rate.get('unlevered_beta'), dict) else {}
    inputs = {'scenarios': {name: {key: value for key, value in (scenario or {}).items()
                                   if key in ('tax_rate', 'terminal_tax_rate', 'sales_to_capital', 'growth_rates',
                                              'ebit_margins', 'terminal_growth', 'wacc')}
                            for name, scenario in (plan.get('scenarios') or {}).items() if isinstance(scenario, dict)},
              'operating': {name: {'components': (scenario or {}).get('components'),
                                   'expenses': (scenario or {}).get('expenses')}
                            for name, scenario in (model.get('scenarios') or {}).items() if isinstance(scenario, dict)},
              'beta': [(item.get('sector'), item.get('weight')) for item in beta.get('sectors') or [] if isinstance(item, dict)],
              'rate': {key: (record or {}).get('value') for key, record in rate.items()
                       if isinstance(record, dict) and key in ('pretax_cost_of_debt', 'marginal_tax_rate')}}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()[:16]


def combined(record):
    """True when the plan valued is a combination of two or more forecasts."""
    return bool(record) and record.get('plan_used') == 'consensus' and \
        len((record.get('structure_decision') or {}).get('group') or []) > 1


def _range(record):
    whole = record.get('method') in SINGLE_METHODS or record.get('plan_used') == 'representative_sample_fallback'
    values = [row['values']['base'] for row in record.get('samples') or []
              if (whole or row.get('in_consensus')) and _number((row.get('values') or {}).get('base'))]
    return (min(values), max(values)) if len(values) > 1 else None


def _count(record):
    return len((record.get('structure_decision') or {}).get('group') or [])


def first_page_sentence(record, style):
    """One sentence under the scenario table, or None when no combination of forecasts is valued."""
    if not combined(record) or not _range(record):
        return None
    low, high = _range(record)
    count = WORDS.get(_count(record), str(_count(record)))
    kind = 'averages' if record.get('method') == 'mean' or _count(record) == 2 else 'is the median of'
    if record.get('edited_after_consensus'):
        return (f'The forecast began as the {"average" if kind == "averages" else "median"} of {count} forecasts '
                f'written from the same evidence, valued at {style.per_share(low)} to {style.per_share(high)}, and was '
                'revised after review.')
    return (f'The base case {kind} {count} forecasts written from the same evidence; their values ranged from '
            f'{style.per_share(low)} to {style.per_share(high)}.')


def method_notes(record, style):
    """The Method notes on how the forecast was built from several samples; [] for one requested forecast."""
    if not record or record.get('requested_samples', record.get('sample_count', 1)) == 1:
        return []
    count, requested = _count(record), record.get('requested_samples', record.get('sample_count'))
    dropped = record.get('dropped') or []
    span = _range(record)
    values = (f' Their base values ranged from {style.per_share(span[0])} to {style.per_share(span[1])} a share.'
              if span else '')
    left = (f' {WORDS.get(len(dropped), len(dropped)).capitalize()} of the {WORDS.get(requested, requested)} '
            f'forecasts requested failed validation after {"their" if len(dropped) > 1 else "its"} repairs and '
            f'{"were" if len(dropped) > 1 else "was"} left out.' if dropped else '')
    excluded = (record.get('structure_decision') or {}).get('excluded') or []
    other = (f' {WORDS.get(len(excluded), len(excluded)).capitalize()} of the others used a different set of segments or '
             f'horizon and {"were" if len(excluded) > 1 else "was"} left out.' if excluded and combined(record) else '')
    written = (f'{WORDS.get(count, count)} of the {WORDS.get(requested, requested)}'
               if isinstance(requested, int) and requested > count else WORDS.get(count, count))
    if combined(record):
        if record.get('method') == 'mean' or count == 2:
            how = ('Growth, gross margins, expense shares, sales-to-capital and tax rates are averaged year by year'
                   + ('; the industry weights behind the beta take the median, so an industry only one forecast used '
                      'drops out' if record.get('sector_weight_method') == 'median' and count > 2 else '') + '.')
        else:
            how = 'Each forecast input is the median of the forecasts, year by year.'
        note = (f'The forecast combines {written} forecasts written independently from the same evidence '
                f'and request. {how} The rationales and named cases come from the forecast closest to the per-year '
                f'median revenue and EBIT paths.{values}{left}{other}')
    elif record.get('plan_used') == 'representative_sample_fallback':
        note = (f'{WORDS.get(count, count).capitalize()} forecasts were written independently from the same evidence. '
                'Their combination failed a forecast check the individual forecasts passed, so the forecast valued is '
                f'the one closest to their per-year median revenue and EBIT paths.{values}{left}')
    elif record.get('method') == 'uncombinable_closest_sample':
        note = (f'{WORDS.get(requested, requested).capitalize()} forecasts were written independently from the same '
                'evidence, but their inputs could not be combined, so the forecast valued is the one closest to their '
                f'per-year median revenue and EBIT paths.{values}{left}')
    elif record.get('method') == 'closest_single_sample':
        note = (f'{WORDS.get(requested, requested).capitalize()} forecasts were written independently from the same '
                'evidence, but no two used the same segments and horizon, so the forecast valued is the one closest to '
                f'their median path.{values}{left}')
    else:
        note = (f'{WORDS.get(requested, requested).capitalize()} forecasts were requested from the same evidence; one '
                f'passed validation and is the forecast valued.{left}')
    notes = [note]
    if record.get('edited_after_consensus'):
        notes.append('A forecast correction after review then edited the plan valued. The values above are those of the '
                     'forecasts before the correction; the scenario table shows the corrected plan.')
    return notes


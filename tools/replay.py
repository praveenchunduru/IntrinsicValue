"""Re-run a saved full run's later stages on its frozen evidence, paying only for those stages.

Usage:
  python tools/replay.py SAVED_RUN_FOLDER OUTPUT_DIR [--from plan|draft] [--max-revisions 1]
                         [--budget 20] [--profile balanced] [--strong-model ID] [--review-model ID]
  python tools/replay.py SAVED_RUN_FOLDER OUTPUT_DIR --plan-samples N [--budget 8] [--resume]
  python tools/replay.py SAVED_RUN_FOLDER OUTPUT_DIR --plan-samples N --consensus-only

--plan-samples N measures repeatability: the forecast plan is requested N times
on the identical prompt, one request after another in one provider cache session,
and each answer is locked, validated, repaired if needed and valued. No draft,
review or web research runs. Results go to OUTPUT_DIR/TICKER/plan-samples/
plan-samples.json, with a table and a driver decomposition printed at the end,
followed by the consensus of the valid samples (every subset of three, and all
of them), locked and valued as a run does (tools/consensus_check.py).
--consensus-only makes no request: it reports the consensus of the first N
samples already saved under OUTPUT_DIR/TICKER/plan-samples/.

The saved folder's sources (cut to those that existed when its first draft was
written), archived documents and paid web-research answers are copied into a
new folder. Everything from the chosen stage onward is recomputed with the
current code and prompts: --from plan rebuilds the forecast plan (as many plan
samples as the profile takes by default, three for balanced), --from draft
keeps the saved first plan, and --from review keeps the saved plan and current
draft so only the free checks, their fix call and the review run. Primary documents are re-derived from their
archives by the normal per-run refresh, so evidence-extraction changes apply.
Requires OPENROUTER_API_KEY; no SEC or web collection is repeated.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from investmentideas import core, pipeline  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import consensus_check  # noqa: E402  (tools/, beside this script)
from investmentideas.valuation import SCENARIOS, evaluate  # noqa: E402


def first_draft_cut(saved, folder, *, keep_all=False):
    """Sources and plan as they were when the saved run's first draft was written."""
    sources = [source for source in saved['sources']
               if keep_all or not (isinstance(source.get('data'), dict) and source['data'].get('review_iteration'))]
    if [source['id'] for source in sources] != [f'S{index}' for index in range(1, len(sources) + 1)]:
        raise ValueError('Review-stage sources are interleaved with collection sources; cannot cut cleanly')
    backups = sorted((folder / 'revisions').glob('model-before-[0-9]*/assumptions.json'),
                     key=lambda path: int(path.parent.name.rsplit('-', 1)[1]))
    plan = json.loads(backups[0].read_text()) if backups else saved.get('plan')
    return sources, plan


def seed(saved_folder, target, *, start):
    saved = json.loads((saved_folder / 'state.json').read_text())
    # A review of the saved current draft needs every source that draft cites.
    sources, plan = first_draft_cut(saved, saved_folder, keep_all=(start == 'review'))
    if target.exists():
        raise ValueError(f'{target} already exists; choose a new output directory')
    target.mkdir(parents=True)
    if (saved_folder / 'sources_raw').is_dir():
        shutil.copytree(saved_folder / 'sources_raw', target / 'sources_raw')
    keep = ('web_',) + (('valuation_plan',) if start in ('draft', 'review') else ()) + (('full_draft',) if start == 'review' else ())
    plan_notes = set(saved.get('plan_gap_notes', []))
    state = {'version': saved['version'], 'ticker': saved['ticker'], 'ledger': [],
             'stages': {key: value for key, value in saved['stages'].items() if key.startswith(keep)},
             'sources': sources, 'gaps': [gap for gap in saved.get('gaps', []) if gap not in plan_notes and
                                          'correction after review' not in gap],
             'iteration': 0, 'review_history': [], 'quality_status': 'in_progress', 'complete': False,
             'evidence_collected': True, 'web_research_complete': True,
             'workflow_contract': pipeline._workflow_contract(),
             'replay_origin': {'folder': str(saved_folder), 'from': start,
                               'state_sha256': hashlib.sha256((saved_folder / 'state.json').read_bytes()).hexdigest()}}
    if start == 'review':
        if not saved.get('current_memo') or not saved.get('plan'):
            raise ValueError('The saved run has no draft and plan to review')
        plan = saved['plan']
        state['current_memo'] = saved['current_memo']
    if start in ('draft', 'review'):
        if not plan:
            raise ValueError('The saved run has no plan to keep')
        state['plan'] = plan
        state['plan_conventions'] = saved.get('plan_conventions')
    core.save(target / 'state.json', state)
    core.save(target / 'run-identity.json', {'id': 'replay'})
    return state


def scorecard(folder):
    state = json.loads((folder / 'state.json').read_text())
    lines = [f'folder: {folder}', f'status: {state.get("quality_status")}  complete: {state.get("complete")}  '
             f'iteration: {state.get("iteration")}']
    spent = 0.0
    for row in state['ledger']:
        spent += row.get('charged_or_reserved_usd', 0)
        usage = row.get('usage') or {}
        lines.append(f'  {row["stage"]:28} {row["model"]:34} ${row.get("charged_or_reserved_usd", 0):.3f} '
                     f'in {usage.get("prompt_tokens", "?")} out {usage.get("completion_tokens", "?")} {row.get("status")}')
    lines.append(f'total charged or reserved: ${spent:.2f}')
    if state.get('prereview_fixes'):
        lines.append('pre-review fixes: ' + json.dumps(state['prereview_fixes'])[:600])
    for review in state.get('review_history', []):
        counts = {}
        for finding in review.get('findings', []):
            counts[finding.get('severity')] = counts.get(finding.get('severity'), 0) + 1
        lines.append(f'review {review["iteration"]}: {review["verdict"]} {counts} weakness handled: '
                     f'{(review.get("central_weakness") or {}).get("memo_handles_it")} gap explained: {review.get("gap_explained")}')
        for finding in review.get('findings', []):
            lines.append(f'    {finding["id"]} {finding["severity"]}: {finding["issue"][:160]}')
    valuation = state.get('valuation') or {}
    if valuation:
        lines.append(f'weighted value {valuation.get("weighted_per_share_value")} value/price {valuation.get("weighted_value_to_price")}')
    return '\n'.join(lines)


# ---------------------------------------------------------------- plan sampling
#
# Each sample is its own replay folder seeded exactly as --from plan seeds one, so
# no sample can reuse another's saved plan response (stages are cached by prompt
# digest within a folder). The evidence synthesis that feeds the plan prompt is
# bought once, in sample 1, and copied into the later folders, so every sample
# sends the byte-identical plan request. One provider session id is shared, so
# samples 2..N read the evidence prefix that sample 1 wrote to the cache.

SAMPLING_STAGES = ('evidence_synthesis', 'valuation_plan')
DRIVER_GROUPS = ('growth path', 'margin path', 'capital efficiency', 'tax', 'discount rate')
FINAL = ('complete', 'unavailable', 'failed')


class PlanStageDone(Exception):
    """The run reached a stage after the plan; sampling stops it before any request."""


def _request_digest(prompt, prefix):
    return hashlib.sha256(((prefix or '') + '\x00' + prompt).encode()).hexdigest()


def _sampling_router(base, session, requests):
    class SamplingRouter(base):
        @property
        def session(self):
            return session

        @session.setter
        def session(self, value):
            # pipeline._run names the session after its folder; all samples share one.
            pass

        def complete(self, stage, model, prompt, max_tokens=3500, *, web=False, prefix=None, **options):
            if web or not stage.startswith(SAMPLING_STAGES):
                raise PlanStageDone(stage)
            if stage.startswith('valuation_plan'):
                requests.append({'stage': stage, 'sha256': _request_digest(prompt, prefix)})
            return super().complete(stage, model, prompt, max_tokens, web=web, prefix=prefix, **options)
    return SamplingRouter


def _plan_only_preflight(original):
    def preflight(router, state, **kwargs):
        report = original(router, state, **kwargs)
        stages = [row for row in report['stages'] if row['stage'].startswith(SAMPLING_STAGES)]
        if any(key.startswith('evidence_synthesis') for key in state.get('stages', {})):
            stages = [row for row in stages if not row['stage'].startswith('evidence_synthesis')]
        floor = sum(row['reservation_floor_usd'] for row in stages)
        report.update(scope='Plan sampling: one forecast plan (and the evidence synthesis in sample 1); repairs excluded',
                      stages=stages, known_context_reservation_floor_usd=floor, passed=router.budget >= floor,
                      suggested_total_allowance_usd=max(1.0, float(math.ceil(floor))))
        return report
    return preflight


@contextlib.contextmanager
def _plan_only(session, requests):
    router, preflight = core.Router, pipeline._initial_budget_preflight
    core.Router = _sampling_router(router, session, requests)
    pipeline._initial_budget_preflight = _plan_only_preflight(preflight)
    try:
        yield
    finally:
        core.Router, pipeline._initial_budget_preflight = router, preflight


def _state(folder):
    path = folder / 'state.json'
    return json.loads(path.read_text()) if path.exists() else {}


def _spent(state):
    return sum(row.get('charged_or_reserved_usd', 0) or 0 for row in state.get('ledger', []))


def _sample_folders(root):
    folders = [path for path in root.glob('sample-*') if path.is_dir() and path.name[7:].isdigit()]
    return sorted(folders, key=lambda path: int(path.name[7:]))


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _explicit_rows(result, name):
    scenario = result['scenarios'][name]
    rows = scenario.get('cashflows') or []
    years = scenario.get('explicit_forecast_years')
    return rows[:years] if isinstance(years, int) and years > 0 else rows


def _usage_totals(rows):
    totals = {'requests': len(rows), 'prompt_tokens': 0, 'cached_tokens': 0, 'cache_write_tokens': 0,
              'completion_tokens': 0, 'cost_usd': 0.0, 'uncertain_reservations': 0}
    for row in rows:
        usage = row.get('usage') or {}
        details = usage.get('prompt_tokens_details') or {}
        totals['prompt_tokens'] += usage.get('prompt_tokens') or 0
        totals['completion_tokens'] += usage.get('completion_tokens') or 0
        totals['cached_tokens'] += details.get('cached_tokens') or 0
        totals['cache_write_tokens'] += details.get('cache_write_tokens') or 0
        totals['cost_usd'] += row.get('charged_or_reserved_usd', 0) or 0
        if _number(usage.get('cost')) is None:
            totals['uncertain_reservations'] += 1
    return totals


def plan_metrics(plan, result):
    """The drivers and values of one sampled plan, read from the locked plan and its valuation."""
    base = (plan.get('scenarios') or {}).get('base') or {}
    scenarios = result.get('scenarios') or {}
    rows = _explicit_rows(result, 'base')
    anchor = (plan.get('actuals') or {}).get('revenue')
    years = len(rows)
    cagr = ((rows[-1]['revenue'] / anchor) ** (1 / years) - 1
            if rows and _number(anchor) and anchor > 0 and rows[-1].get('revenue', 0) > 0 else None)
    path = base.get('sales_to_capital_path')
    if isinstance(path, list) and path:
        sales_to_capital = {'year1': path[0], 'year5': path[min(4, len(path) - 1)], 'year10': path[min(9, len(path) - 1)]}
    else:
        ratio = _number(base.get('sales_to_capital'))
        sales_to_capital = {'year1': ratio, 'year5': ratio, 'year10': ratio}
    rate = plan.get('discount_rate') if isinstance(plan.get('discount_rate'), dict) else {}
    record = rate.get('unlevered_beta') if isinstance(rate.get('unlevered_beta'), dict) else {}
    sectors = [{'sector': item.get('sector'), 'weight': item.get('weight'),
                'cash_corrected_unlevered_beta': item.get('cash_corrected_unlevered_beta')}
               for item in record.get('sectors') or [] if isinstance(item, dict)]
    weighted = (sum(item['weight'] * item['cash_corrected_unlevered_beta'] for item in sectors)
                if sectors and all(_number(item['weight']) is not None and
                                   _number(item['cash_corrected_unlevered_beta']) is not None for item in sectors)
                else _number(record.get('value')))
    calculation = result.get('discount_rate_calculation') or {}
    return {
        'per_share_value': {name: (scenarios.get(name) or {}).get('per_share_value') for name in SCENARIOS},
        'weighted_per_share_value': result.get('weighted_per_share_value'),
        'revenue_cagr': cagr, 'forecast_years': years,
        'ebit_margin_year1': rows[0].get('ebit_margin') if rows else None,
        'ebit_margin_final_year': rows[-1].get('ebit_margin') if rows else None,
        'sales_to_capital': sales_to_capital,
        'tax_rate': base.get('tax_rate'), 'terminal_tax_rate': base.get('terminal_tax_rate'),
        'unlevered_beta': {'calculation': record.get('calculation'), 'value': weighted, 'sectors': sectors},
        'relevered_beta': calculation.get('relevered_beta'),
        'initial_discount_rate': (scenarios.get('base') or {}).get('initial_rate', base.get('wacc')),
        'terminal_discount_rate': (scenarios.get('base') or {}).get('terminal_rate', base.get('wacc')),
    }


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
            except (ValueError, KeyError, TypeError, ZeroDivisionError, OverflowError) as exc:
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


def summarize_sample(folder, outcome):
    state = _state(folder)
    ledger = state.get('ledger', [])
    plan_rows = [row for row in ledger if row.get('stage', '').startswith('valuation_plan')]
    record = {'sample': int(folder.name[7:]), 'folder': str(folder), 'status': outcome.get('status'),
              'error': outcome.get('error'),
              'plan_requests': len(plan_rows),
              'repairs': sum(row['stage'].startswith('valuation_plan_repair') for row in plan_rows),
              'repair_reasons': state.get('plan_repair_reasons', []),
              'omitted_plan_sections': state.get('omitted_plan_sections'),
              'response_normalizations': state.get('response_normalizations', []),
              'plan_request_sha256': outcome.get('plan_request_sha256'),
              'plan_usage': _usage_totals(plan_rows),
              'first_plan_request': _usage_totals(plan_rows[:1]),
              'other_usage': _usage_totals([row for row in ledger if row not in plan_rows]),
              'total_cost_usd': _spent(state)}
    plan, result = state.get('plan'), state.get('valuation')
    if record['status'] == 'complete' and isinstance(plan, dict) and isinstance(result, dict):
        record['metrics'] = plan_metrics(plan, result)
    elif isinstance(plan, dict) and plan.get('unavailable'):
        record['unavailable_reason'] = plan.get('reason')
    return record


def _summary(records):
    values = [(row['sample'], row['metrics']['per_share_value']['base']) for row in records if row.get('metrics')]
    values = [(number, value) for number, value in values if _number(value) is not None]
    if not values:
        return {'samples_valued': 0}
    numbers = [value for _, value in values]
    median = statistics.median(numbers)
    closest = min(values, key=lambda item: (abs(item[1] - median), item[0]))[0]
    return {'samples_valued': len(values), 'min_base_value': min(numbers), 'median_base_value': median,
            'max_base_value': max(numbers),
            'spread_fraction': (max(numbers) - min(numbers)) / median if median else None,
            'median_sample': closest,
            'samples_with_repairs': sum(1 for row in records if row.get('repairs')),
            'samples_failed': sum(1 for row in records if row.get('status') in ('failed', 'unavailable'))}


def _cache_warnings(records):
    warnings = []
    for row in records[1:]:
        first = row['first_plan_request']
        if first['requests'] and first['prompt_tokens'] and not first['cached_tokens']:
            warnings.append(f'WARNING: sample {row["sample"]} read no cached tokens: its plan request was billed at the '
                            f'full input price (${row["plan_usage"]["cost_usd"]:.2f}). Without the cache the '
                            'measurement costs about N times the first sample instead of about $0.30 per extra sample.')
    digests = {row['plan_request_sha256'] for row in records if row.get('plan_request_sha256')}
    if len(digests) > 1:
        warnings.append('WARNING: the samples did not send byte-identical plan requests; the provider cache and the '
                        'comparison both assume one prompt.')
    return warnings


def _format(value, kind):
    if value is None:
        return '-'
    if kind == 'money':
        return f'{value:,.0f}' if abs(value) >= 1000 else f'{value:.1f}'
    if kind == 'pct':
        return f'{100 * value:.1f}%'
    if kind == 'ratio':
        return f'{value:.2f}'
    return str(value)


def sampling_table(report):
    head = ('#', 'status', 'base', 'bear', 'bull', 'CAGR', 'EBITm1', 'EBITm10', 'S/C1', 'S/C5', 'S/C10', 'tax',
            'beta', 'r0', 'rT', 'rep', 'prompt', 'cached', 'cost')
    lines = [' '.join(f'{name:>8}' for name in head)]
    for row in report['samples']:
        metrics = row.get('metrics') or {}
        values = metrics.get('per_share_value') or {}
        capital = metrics.get('sales_to_capital') or {}
        usage = row['plan_usage']
        cells = [str(row['sample']), row['status'] or '-', _format(values.get('base'), 'money'),
                 _format(values.get('bear'), 'money'), _format(values.get('bull'), 'money'),
                 _format(metrics.get('revenue_cagr'), 'pct'), _format(metrics.get('ebit_margin_year1'), 'pct'),
                 _format(metrics.get('ebit_margin_final_year'), 'pct'), _format(capital.get('year1'), 'ratio'),
                 _format(capital.get('year5'), 'ratio'), _format(capital.get('year10'), 'ratio'),
                 _format(metrics.get('tax_rate'), 'pct'), _format((metrics.get('unlevered_beta') or {}).get('value'), 'ratio'),
                 _format(metrics.get('initial_discount_rate'), 'pct'), _format(metrics.get('terminal_discount_rate'), 'pct'),
                 str(row['repairs']), str(usage['prompt_tokens']), str(usage['cached_tokens']), f'${usage["cost_usd"]:.2f}']
        lines.append(' '.join(f'{cell:>8}' for cell in cells))
    for row in report['samples']:
        sectors = ((row.get('metrics') or {}).get('unlevered_beta') or {}).get('sectors')
        if sectors:
            lines.append(f'sample {row["sample"]} beta weights: ' +
                         ', '.join(f'{item["sector"]} {item["weight"]} x {item["cash_corrected_unlevered_beta"]}'
                                   for item in sectors))
        if row.get('repair_reasons') or row.get('error'):
            lines.append(f'sample {row["sample"]} {row["status"]}: ' +
                         ' | '.join([*row.get('repair_reasons', []), *([row['error']] if row.get('error') else [])])[:600])
    summary = report['summary']
    if summary.get('samples_valued'):
        lines.append(f'base value over {summary["samples_valued"]} valued samples: min {_format(summary["min_base_value"], "money")}'
                     f'  median {_format(summary["median_base_value"], "money")}  max {_format(summary["max_base_value"], "money")}'
                     f'  spread (max-min)/median {_format(summary["spread_fraction"], "pct")}')
    decomposition = report.get('decomposition')
    if decomposition:
        lines.append(f'driver decomposition around sample {decomposition["median_sample"]} '
                     f'(base {_format(decomposition["median_sample_base_value"], "money")}):')
        for group, item in decomposition['groups'].items():
            errors = sum('error' in swap for swap in item['swaps'])
            lines.append(f'  {group:20} {_format(item["min_base_value"], "money"):>8} to '
                         f'{_format(item["max_base_value"], "money"):>8}  range {_format(item["range"], "money"):>7} '
                         f'({_format(item["range_fraction_of_median"], "pct")})' + (f'  {errors} swap(s) invalid' if errors else ''))
        lines.append(f'  varies most: {decomposition["largest_group"]}')
    lines.append(f'total charged or reserved: ${report["total_cost_usd"]:.2f} of ${report["budget_usd"]:.2f}')
    if report.get('stopped'):
        lines.append('stopped: ' + report['stopped'])
    lines.extend(report.get('warnings', []))
    return '\n'.join(lines)


def _report(root, saved_folder, samples, budget, models, stopped):
    records = []
    for folder in _sample_folders(root):
        outcome_path = folder / 'sample-outcome.json'
        outcome = json.loads(outcome_path.read_text()) if outcome_path.exists() else {'status': 'not_started'}
        records.append(summarize_sample(folder, outcome))
    report = {'saved_run': str(saved_folder), 'requested_samples': samples, 'budget_usd': budget, 'models': models,
              'samples': records, 'summary': _summary(records),
              'total_cost_usd': sum(row['total_cost_usd'] for row in records),
              'shared_synthesis_cost_usd': sum(row['other_usage']['cost_usd'] for row in records),
              'warnings': _cache_warnings(records), 'stopped': stopped}
    summary = report['summary']
    valued = [row for row in records if row.get('metrics')]
    if summary.get('samples_valued', 0) >= 2:
        states = {row['sample']: _state(Path(row['folder'])) for row in valued}
        median = summary['median_sample']
        report['decomposition'] = decompose(
            (median, states[median]['plan'], states[median]['valuation']),
            [(number, state['plan'], state['valuation']) for number, state in states.items() if number != median])
    core.save(root / 'plan-samples.json', report)
    return report


def sample_plans(saved_folder, output, *, samples, key, easy, strong, reviewer, budget, resume=False, progress=print):
    """Request the forecast plan ``samples`` times on one frozen prompt; see the module docstring."""
    saved_folder = Path(saved_folder).resolve()
    saved = json.loads((saved_folder / 'state.json').read_text())
    if not 1 <= samples <= 50:
        raise ValueError('--plan-samples must be 1 to 50')
    if not 0 < budget <= 100:
        raise ValueError('Budget must be greater than zero and at most $100')
    root = Path(output).resolve() / saved['ticker'] / 'plan-samples'
    if root.exists() and not resume:
        raise ValueError(f'{root} already exists; choose a new output directory or pass --resume')
    root.mkdir(parents=True, exist_ok=True)
    session = 'iv-sample-' + pipeline._digest(str(root))[:24]
    models = {'easy': easy, 'strong': strong}
    synthesis, estimate, stopped = {}, None, None
    for number in range(1, samples + 1):
        folder = root / f'sample-{number}'
        outcome_path = folder / 'sample-outcome.json'
        outcome = json.loads(outcome_path.read_text()) if outcome_path.exists() else {}
        state = _state(folder)
        synthesis = synthesis or {name: value for name, value in state.get('stages', {}).items()
                                  if name.startswith('evidence_synthesis')}
        plan_rows = [row for row in state.get('ledger', []) if row.get('stage', '').startswith('valuation_plan')]
        estimate = next((row['reserved_usd'] for row in plan_rows if row.get('reserved_usd')), estimate)
        if outcome.get('status') in FINAL:
            continue
        others = sum(_spent(_state(path)) for path in _sample_folders(root) if path != folder)
        if not folder.exists() and estimate is not None and others + estimate > budget:
            stopped = (f'before sample {number}: its plan reservation of ${estimate:.2f} would bring the total to '
                       f'${others + estimate:.2f}, above the ${budget:.2f} budget')
            break
        if others >= budget:
            stopped = f'before sample {number}: the ${budget:.2f} budget is used'
            break
        if not folder.exists():
            seeded = seed(saved_folder, folder, start='plan')
            seeded['replay_origin']['plan_sample'] = number
            if synthesis:
                seeded['stages'].update(synthesis)
            core.save(folder / 'state.json', seeded)
        requests = []
        outcome = {'status': 'in_progress'}
        progress(f'plan sample {number} of {samples}')
        try:
            with pipeline._lock(folder), _plan_only(session, requests):
                pipeline._run(folder, saved['ticker'], key, easy, strong, budget - others, '', progress, 1, '', [],
                              None, None, None, None, None, default_reviewer=reviewer)
            outcome['status'] = 'complete'
        except PlanStageDone:
            plan = _state(folder).get('plan') or {}
            outcome['status'] = 'unavailable' if plan.get('unavailable') else 'complete'
        except core.BudgetExceeded as exc:
            outcome.update(status='budget_stopped', error=str(exc)[:600])
            stopped = f'sample {number}: {exc}'
        except core.HTTPFailure as exc:
            outcome.update(status='provider_error', error=str(exc)[:600])
            stopped = f'sample {number}: {exc}'
        except core.IncompleteResponse as exc:
            # A cut-off, filtered or empty plan answer is a sampled outcome; so is
            # a plan still invalid after its repairs. Anything else stops the run.
            outcome.update(status='failed' if requests else 'error', error=str(exc)[:600])
            if not requests:
                stopped = f'sample {number}: {exc}'
        except RuntimeError as exc:
            failed = bool(requests) and str(exc).startswith('Valuation plan')
            outcome.update(status='failed' if failed else 'error', error=str(exc)[:600])
            if not failed:
                stopped = f'sample {number}: {exc}'
        if requests:
            outcome['plan_request_sha256'] = requests[0]['sha256']
        core.save(outcome_path, outcome)
        state = _state(folder)
        synthesis = synthesis or {name: value for name, value in state.get('stages', {}).items()
                                  if name.startswith('evidence_synthesis')}
        estimate = next((row['reserved_usd'] for row in state.get('ledger', [])
                         if row.get('stage', '').startswith('valuation_plan') and row.get('reserved_usd')), estimate)
        report = _report(root, saved_folder, samples, budget, models, stopped)
        for warning in report['warnings']:
            if f'sample {number} ' in warning:
                progress(warning)
        if stopped:
            break
    report = _report(root, saved_folder, samples, budget, models, stopped)
    if report['summary'].get('samples_valued', 0) >= 2:
        # The consensus of the valid samples, locked and valued as a run does; printed first.
        report['consensus'] = consensus_check.check(root)
        core.save(root / 'plan-samples.json', report)
        progress(consensus_check.table(report['consensus']))
    progress(sampling_table(report))
    progress(f'plan samples: {root / "plan-samples.json"}')
    return report


def consensus_only(saved_folder, output, *, samples, progress=print):
    """The consensus of the first ``samples`` saved plan samples; no request is made."""
    saved = json.loads((Path(saved_folder).resolve() / 'state.json').read_text())
    root = Path(output).resolve() / saved['ticker'] / 'plan-samples'
    if not root.is_dir():
        raise ValueError(f'{root} has no saved plan samples; run --plan-samples first')
    result = consensus_check.check(root, limit=samples)
    core.save(root / 'plan-consensus-check.json', result)
    progress(consensus_check.table(result))
    progress(f'consensus check: {root / "plan-consensus-check.json"}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('saved')
    parser.add_argument('output')
    parser.add_argument('--from', dest='start', choices=('plan', 'draft', 'review'), default='plan')
    parser.add_argument('--max-revisions', type=int, default=1)
    parser.add_argument('--budget', type=float, default=pipeline.DEFAULT_BUDGET)
    parser.add_argument('--profile', choices=tuple(core.MODELS), default='balanced')
    parser.add_argument('--strong-model')
    parser.add_argument('--review-model')
    parser.add_argument('--resume', action='store_true', help='continue an existing replay folder (prior charges count)')
    parser.add_argument('--plan-samples', type=int, metavar='N',
                        help='request only the forecast plan N times on the identical prompt and compare the values')
    parser.add_argument('--consensus-only', action='store_true',
                        help='with --plan-samples N: report the consensus of the first N saved samples, with no request')
    args = parser.parse_args()
    if args.consensus_only:
        if args.plan_samples is None:
            raise SystemExit('--consensus-only needs --plan-samples N')
        try:
            consensus_only(args.saved, args.output, samples=args.plan_samples)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        return
    key = os.environ.get('OPENROUTER_API_KEY', '').strip()
    if not key:
        raise SystemExit('Set OPENROUTER_API_KEY')
    saved_folder = Path(args.saved).resolve()
    saved = json.loads((saved_folder / 'state.json').read_text())
    if args.plan_samples is not None:
        if args.start != 'plan':
            raise SystemExit('--plan-samples replays from the plan; omit --from')
        easy, strong = core.MODELS[args.profile]
        strong = args.strong_model or strong
        try:
            sample_plans(saved_folder, args.output, samples=args.plan_samples, key=key, easy=easy, strong=strong,
                         reviewer=args.review_model or pipeline._default_reviewer(args.profile, strong),
                         budget=args.budget, resume=args.resume)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        return
    target = Path(args.output).resolve() / saved['ticker'] / 'full-replay'
    if args.resume and target.exists():
        state = json.loads((target / 'state.json').read_text())
        origin = state.get('replay_origin') or {}
        if origin.get('from') != args.start:
            raise SystemExit(f'This replay started from {origin.get("from")!r}; resume it with --from {origin.get("from")}')
        if state.get('workflow_contract') != pipeline._workflow_contract():
            # A replay compares prompts on frozen inputs: a code change since the
            # last attempt must not discard and re-pay its plan or draft.
            print('Code changed since this replay started; its saved stages are kept (contract re-stamped).')
            state['workflow_contract'] = pipeline._workflow_contract()
            core.save(target / 'state.json', state)
    else:
        seed(saved_folder, target, start=args.start)
    easy, strong = core.MODELS[args.profile]
    strong = args.strong_model or strong
    reviewer = args.review_model or pipeline._default_reviewer(args.profile, strong)
    try:
        with pipeline._lock(target):
            pipeline._run(target, saved['ticker'], key, easy, strong, args.budget, '', print, args.max_revisions, '', [],
                          None, None, args.review_model, None, None, default_reviewer=reviewer,
                          plan_samples=pipeline.default_plan_samples(args.profile))
    finally:
        print(scorecard(target))


if __name__ == '__main__':
    main()

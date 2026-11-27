"""Free check of the forecast consensus on saved plan samples: no requests are made.

Usage:
  python tools/consensus_check.py SAMPLES_FOLDER [SAMPLES_FOLDER ...] [--method median|mean] [--json OUT]

SAMPLES_FOLDER is a ``plan-samples`` folder written by ``tools/replay.py
--plan-samples`` (``sample-k/state.json`` per sample). Each sample's accepted
plan response is read from its saved stages, the proposal form the pipeline's
consensus takes. The consensus of every subset of three samples, and of all
samples, is locked, checked and valued exactly as a run does, on the samples'
shared frozen sources. Printed: bear, base and bull for each sample and each
consensus, and the spread of the 3-subset consensus values,
(max - min) / median.
"""
from __future__ import annotations

import argparse
import copy
import itertools
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from investmentideas import consensus, core, pipeline  # noqa: E402

SCENARIOS = ('bear', 'base', 'bull')


def accepted_proposal(state):
    """The plan response the sample's lock accepted: its last valuation-plan stage, parsed."""
    names = [name for name in state.get('stages', {}) if name.startswith('valuation_plan')]
    if not names:
        return None
    plan, _ = pipeline._response_json(state['stages'][names[-1]])
    return plan if isinstance(plan, dict) and not plan.get('unavailable') else None


def load(folder):
    folder = Path(folder)
    samples = []
    for path in sorted(folder.glob('sample-*/state.json'), key=lambda item: int(item.parent.name.split('-')[1])):
        state = json.loads(path.read_text())
        proposal = accepted_proposal(state)
        if proposal is not None:
            samples.append({'sample': int(path.parent.name.split('-')[1]), 'state': state, 'proposal': proposal})
    if not samples:
        raise SystemExit(f'No accepted plan samples under {folder}')
    digests = {pipeline._digest(sample['state']['sources']) for sample in samples}
    if len(digests) != 1:
        raise SystemExit('The samples were not taken on identical sources')
    return samples


def locker(state):
    """The plan stage's own lock: optional sections that alone fail are left out, as in a run."""
    def lock(candidate):
        (plan, changes, result), omitted = pipeline._omit_invalid_optional_sections(
            candidate, lambda item: pipeline._lock_and_value(item, state))
        return result, (plan, changes, result, omitted)
    return lock


def values(result):
    return {name: result['scenarios'][name]['per_share_value'] for name in SCENARIOS}


def build(samples, chosen, lock, method, held, sector_method):
    proposals = [samples[index]['proposal'] for index in chosen]
    valuations = [samples[index]['result'] for index in chosen]
    plan, report = consensus.consensus_plan(copy.deepcopy(proposals), valuations=valuations, held_shares=held,
                                            method=method, sector_method=sector_method)
    plan, (locked, changes, result, omitted) = consensus.lock_consensus(plan, report, lock, proposals)
    report['sample_numbers'] = [samples[index]['sample'] for index in chosen]
    if report.get('plan_used') == 'consensus' and len(chosen) > 1:
        report['value_impact'] = consensus.value_impact(
            locked, result, [(samples[index]['sample'], samples[index]['locked'], samples[index]['result']) for index in chosen])
    return values(result), report


def spread(numbers):
    middle = statistics.median(numbers)
    return (max(numbers) - min(numbers)) / middle if middle else None


def check(folder, method=consensus.DEFAULT_METHOD, sector_method=consensus.DEFAULT_SECTOR_METHOD, limit=None):
    samples = load(folder)[:limit] if limit else load(folder)
    state = samples[0]['state']
    lock = locker(state)
    skipped = []
    for sample in samples:
        try:
            sample['result'], (locked, *_rest) = lock(sample['proposal'])
            sample['locked'] = locked
        except consensus.LOCK_ERRORS as exc:
            # A response saved before its repairs finished is not a valid sample.
            skipped.append({'sample': sample['sample'], 'reason': str(exc)[:300]})
            sample['result'] = None
            continue
        sample['values'] = values(sample['result'])
    samples = [sample for sample in samples if sample['result'] is not None]
    if not samples:
        raise SystemExit(f'No saved plan sample under {folder} passes the lock')
    held = pipeline._reported_expense_shares(locked)
    count = len(samples)
    rows = []
    for size in sorted({3, count} if count >= 3 else {count}):
        for chosen in itertools.combinations(range(count), size):
            value, report = build(samples, chosen, lock, method, held, sector_method)
            rows.append({'samples': [samples[index]['sample'] for index in chosen], 'values': value,
                         'representative_sample': report.get('representative_sample'),
                         'plan_used': report.get('plan_used'), 'notes': report.get('notes'),
                         'bases_from_other_samples': report.get('bases_from_other_samples'),
                         'value_impact': [{key: item[key] for key in ('group', 'min_base_value', 'max_base_value', 'range')}
                                          for item in (report.get('value_impact') or {}).get('groups', [])]})
    triples = [row for row in rows if len(row['samples']) == 3]
    summary = {name: spread([row['values'][name] for row in triples]) for name in SCENARIOS} if len(triples) > 1 else {}
    single = {name: spread([sample['values'][name] for sample in samples]) for name in SCENARIOS}
    return {'folder': str(folder), 'ticker': state.get('ticker'), 'method': method, 'sector_method': sector_method,
            'samples': [{'sample': sample['sample'], 'values': sample['values']} for sample in samples], 'skipped': skipped,
            'single_sample_spread': single, 'consensus': rows, 'three_subset_spread': summary}


def table(result):
    lines = [f'{result["ticker"]} ({result["method"]}; industry weights {result["sector_method"]}) from {result["folder"]}']
    lines.append(f'  {"plan":24} {"bear":>8} {"base":>8} {"bull":>8}  representative / notes')
    for sample in result['samples']:
        value = sample['values']
        lines.append(f'  {"sample " + str(sample["sample"]):24} ' + ' '.join(f'{value[name]:8.2f}' for name in SCENARIOS))
    for row in result['consensus']:
        value = row['values']
        label = 'consensus of ' + ','.join(map(str, row['samples']))
        extra = f'rep {row["representative_sample"]}'
        if row['plan_used'] != 'consensus':
            extra += f'; {row["plan_used"]}'
        if row.get('bases_from_other_samples'):
            extra += '; bases from ' + ', '.join(f'{item["sample"]}:{item["field"]}.{item["scenario"]}'
                                                 for item in row['bases_from_other_samples'])
        lines.append(f'  {label:24} ' + ' '.join(f'{value[name]:8.2f}' for name in SCENARIOS) + '  ' + extra)
        if row.get('value_impact'):
            lines.append('      base value by driver group swapped in: ' + '; '.join(
                f'{item["group"]} {item["min_base_value"]:.1f}-{item["max_base_value"]:.1f}' for item in row['value_impact'][:3]))
    def percent(item):
        return ' '.join(f'{name} {100 * item[name]:.1f}%' for name in SCENARIOS if item.get(name) is not None)
    lines.append('  spread of single samples, (max-min)/median: ' + percent(result['single_sample_spread']))
    for item in result.get('skipped', []):
        lines.append(f'  sample {item["sample"]} skipped: {item["reason"]}')
    if result['three_subset_spread']:
        lines.append('  spread of 3-subset consensus values:        ' + percent(result['three_subset_spread']))
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('folders', nargs='+')
    parser.add_argument('--method', choices=('median', 'mean'), default=consensus.DEFAULT_METHOD,
                        help=f'how forecast inputs are combined (default: {consensus.DEFAULT_METHOD}, as in a run)')
    parser.add_argument('--sector-method', choices=('median', 'mean'), default=consensus.DEFAULT_SECTOR_METHOD,
                        help=f'how industry beta weights are combined (default: {consensus.DEFAULT_SECTOR_METHOD})')
    parser.add_argument('--json', help='also write the full results to this file')
    args = parser.parse_args()
    results = [check(folder, args.method, args.sector_method) for folder in args.folders]
    for result in results:
        print(table(result))
    if args.json:
        core.save(Path(args.json), results)


if __name__ == '__main__':
    main()

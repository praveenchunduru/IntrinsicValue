"""Re-run a saved full run's later stages on its frozen evidence, paying only for those stages.

Usage:
  python tools/replay.py SAVED_RUN_FOLDER OUTPUT_DIR [--from plan|draft] [--max-revisions 1]
                         [--budget 4] [--profile balanced] [--strong-model ID] [--review-model ID]

The saved folder's sources (cut to those that existed when its first draft was
written), archived documents and paid web-research answers are copied into a
new folder. Everything from the chosen stage onward is recomputed with the
current code and prompts: --from plan rebuilds the forecast plan, --from draft
keeps the saved first plan, and --from review keeps the saved plan and current
draft so only the free checks, their fix call and the review run. Primary documents are re-derived from their
archives by the normal per-run refresh, so evidence-extraction changes apply.
Requires OPENROUTER_API_KEY; no SEC or web collection is repeated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from investmentideas import core, pipeline  # noqa: E402


def first_draft_cut(saved, folder):
    """Sources and plan as they were when the saved run's first draft was written."""
    sources = [source for source in saved['sources']
               if not (isinstance(source.get('data'), dict) and source['data'].get('review_iteration'))]
    if [source['id'] for source in sources] != [f'S{index}' for index in range(1, len(sources) + 1)]:
        raise ValueError('Review-stage sources are interleaved with collection sources; cannot cut cleanly')
    backups = sorted((folder / 'revisions').glob('model-before-[0-9]*/assumptions.json'),
                     key=lambda path: int(path.parent.name.rsplit('-', 1)[1]))
    plan = json.loads(backups[0].read_text()) if backups else saved.get('plan')
    return sources, plan


def seed(saved_folder, target, *, start):
    saved = json.loads((saved_folder / 'state.json').read_text())
    sources, plan = first_draft_cut(saved, saved_folder)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('saved')
    parser.add_argument('output')
    parser.add_argument('--from', dest='start', choices=('plan', 'draft', 'review'), default='plan')
    parser.add_argument('--max-revisions', type=int, default=1)
    parser.add_argument('--budget', type=float, default=4.0)
    parser.add_argument('--profile', choices=tuple(core.MODELS), default='balanced')
    parser.add_argument('--strong-model')
    parser.add_argument('--review-model')
    parser.add_argument('--resume', action='store_true', help='continue an existing replay folder (prior charges count)')
    args = parser.parse_args()
    key = os.environ.get('OPENROUTER_API_KEY', '').strip()
    if not key:
        raise SystemExit('Set OPENROUTER_API_KEY')
    saved_folder = Path(args.saved).resolve()
    saved = json.loads((saved_folder / 'state.json').read_text())
    target = Path(args.output).resolve() / saved['ticker'] / 'full-replay'
    if not (args.resume and target.exists()):
        seed(saved_folder, target, start=args.start)
    easy, strong = core.MODELS[args.profile]
    strong = args.strong_model or strong
    reviewer = args.review_model or pipeline._default_reviewer(args.profile, strong)
    try:
        with pipeline._lock(target):
            pipeline._run(target, saved['ticker'], key, easy, strong, args.budget, '', print, args.max_revisions, '', [],
                          None, None, args.review_model, None, None, default_reviewer=reviewer)
    finally:
        print(scorecard(target))


if __name__ == '__main__':
    main()

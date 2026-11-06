"""Launch the local interface, generate research, or recompute saved assumptions."""
import argparse
import json
import math
import os
from pathlib import Path
import sys

from .core import MODELS, run
from .sec_http import sec_contact_scope


def main():
    parser = argparse.ArgumentParser(description='InvestmentIdeas — OpenRouter-only research')
    parser.add_argument('ticker', nargs='?')
    parser.add_argument('--quick', action='store_true', help='Use the four-call financial overview instead of the full research pipeline')
    parser.add_argument('--profile', choices=MODELS, default='balanced')
    parser.add_argument('--budget', type=float, help='Total cumulative USD allowance, including prior uncertain charges (default: full 10, quick 2; not a promised cost)')
    parser.add_argument('--sec-contact',help='Requester contact email for SEC access (memory only; alternatively set SEC_CONTACT_EMAIL)')
    parser.add_argument('--output', default='research-output')
    parser.add_argument('--evidence', type=Path, help='Optional UTF-8 filing or research text (up to 100 KB)')
    parser.add_argument('--easy-model', help='Override extraction model with an OpenRouter model ID')
    parser.add_argument('--review-model',help='Explicit independent review-model override; saved with the current run')
    parser.add_argument('--strong-model', help='Override writing/review model with an OpenRouter model ID')
    parser.add_argument('--max-revisions',type=int,default=3,help='Full memo correction limit, 0–8 (default: 3)')
    parser.add_argument('--sources',type=Path,help='Append a JSON list of supplemental archived sources to the same full run (at most 3 MB)')
    parser.add_argument('--snapshot',type=Path,help='Replay a dated S1… source manifest without refreshing evidence or quotes (at most 3 MB)')
    parser.add_argument('--forecast-plan',type=Path,help='Review an explicit forecast JSON against pinned reported inputs; never bypasses review (at most 150 KB)')
    parser.add_argument('--draft',type=Path,help='Review an edited memo body in the same saved full run (UTF-8, at most 150 KB)')
    parser.add_argument('--counterfactuals',type=Path,help='Optional JSON object of named base-scenario forecast overrides (up to 64 KB)')
    parser.add_argument('--review-notes',type=Path,help='Reopen the same full run with additional independent review notes (UTF-8, at most 30 KB)')
    parser.add_argument('--revalue', type=Path, help='Recompute an edited assumptions.json without a key or model calls; --output is the destination folder')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if args.budget is None:
        args.budget = 2.0 if args.quick else 10.0
    try:
        if args.revalue:
            if args.ticker or args.quick or args.evidence or args.counterfactuals or args.draft or args.snapshot or args.forecast_plan:
                parser.error('--revalue cannot be combined with a ticker, --quick or --evidence')
            if args.revalue.stat().st_size > 1_000_000:
                raise ValueError('Assumptions file must be at most 1 MB')
            from .valuation import evaluate, save_valuation
            assumptions = json.loads(args.revalue.read_text(encoding='utf-8'))
            results = evaluate(assumptions)
            folder = Path(args.output)
            folder.mkdir(parents=True, exist_ok=True)
            save_valuation(folder, assumptions, results)
            print(folder / 'valuation.json')
            return
        if not math.isfinite(args.budget) or not 0 < args.budget <= 100:
            raise ValueError('Budget must be greater than zero and at most $100')
        if not args.ticker:
            from .web import serve
            extra={'sec_contact':args.sec_contact} if args.sec_contact else {}
            serve(args.port, args.output, default_mode='quick' if args.quick else 'full',**extra)
            return
        evidence = args.evidence.read_text(encoding='utf-8') if args.evidence else ''
        if len(evidence.encode()) > 100000:
            raise ValueError('Evidence must be at most 100 KB; provide a focused excerpt')
        if args.quick:
            runner = run
        else:
            from .pipeline import run as runner
        extra = {}
        if not args.quick:
            notes=args.review_notes.read_text(encoding='utf-8') if args.review_notes else ''
            if args.sources and args.sources.stat().st_size>3000000:
                raise ValueError('Supplemental source JSON must be at most 3 MB')
            sources=json.loads(args.sources.read_text(encoding='utf-8')) if args.sources else None
            extra={'max_revisions':args.max_revisions,'review_notes':notes,'extra_sources':sources}
            for path, limit, field in ((args.snapshot,3000000,'source_snapshot'),(args.forecast_plan,150000,'forecast_plan')):
                if path:
                    if path.stat().st_size > limit:
                        raise ValueError(f'{field} file exceeds its size limit')
                    extra[field] = json.loads(path.read_text(encoding='utf-8'))
            if args.review_model:
                extra['review_model']=args.review_model
            if args.draft:
                if args.draft.stat().st_size>150000:
                    raise ValueError('Draft must be at most 150 KB')
                extra['draft']=args.draft.read_text(encoding='utf-8')
            if args.counterfactuals:
                if args.counterfactuals.stat().st_size>64000:
                    raise ValueError('Counterfactual JSON must be at most 64 KB')
                extra['counterfactuals']=json.loads(args.counterfactuals.read_text(encoding='utf-8'))
        elif args.review_notes or args.counterfactuals or args.draft or args.snapshot or args.forecast_plan:
            raise ValueError('--review-notes, --counterfactuals, --draft, --snapshot and --forecast-plan apply only to full research')
        with sec_contact_scope(args.sec_contact):
            folder = runner(args.ticker, os.environ.get('OPENROUTER_API_KEY', ''), profile=args.profile,
                            budget=args.budget, directory=args.output, evidence=evidence,
                            easy_model=args.easy_model, strong_model=args.strong_model, progress=print, **extra)
        print(folder / 'memo.md')
    except (ValueError, RuntimeError, OSError, KeyError, IndexError, TypeError) as exc:
        print(f'Research stopped: {exc}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

"""IntrinsicValue: value a company from its SEC filings and write a research memo.

Run with a ticker for the command line, or with no ticker for the browser form.
"""
import sys

if sys.version_info < (3, 9):  # pragma: no cover - checked before any 3.9 syntax is imported
    sys.exit(f'IntrinsicValue needs Python 3.9 or newer; this is {sys.version.split()[0]}. '
             'On a Mac, /usr/bin/python3 works. Elsewhere, install Python from python.org.')

import argparse
import errno
import json
import math
import os
from pathlib import Path
import time

from . import __version__
from .core import MODELS, run
from .sec_http import sec_contact_scope

EXAMPLES = '''examples:
  {prog} --setup                save your OpenRouter key and email once
  {prog} MSFT                   value Microsoft and write a memo
  {prog} MSFT --dry-run         collect evidence and check inputs, free, no key needed
  {prog}                        open the browser form instead
  {prog} --list                 list past runs
  {prog} --revalue FOLDER/assumptions.json   recompute after editing assumptions, free

Full research usually costs a few dollars; --budget is a ceiling, never a target.'''


def setup_notes(sec_contact=None, environ=None, needs_sec=True):
    """Which of the two settings is missing, in one plain sentence each. Values are never printed."""
    environ = os.environ if environ is None else environ
    notes = []
    if not environ.get('OPENROUTER_API_KEY', '').strip():
        notes.append('Setup: OPENROUTER_API_KEY is not set, so no memo can be written. Run --setup to save it, '
                     'add --dry-run to collect evidence for free, or run with no ticker and use the browser form.')
    contact = sec_contact or environ.get('SEC_CONTACT_EMAIL', '').strip() or environ.get('SEC_USER_AGENT', '').strip()
    if needs_sec and not contact:
        notes.append('Setup: no SEC contact email is set, so SEC filings cannot be collected. '
                     'Run --setup, set SEC_CONTACT_EMAIL, or pass --sec-contact you@example.com.')
    return notes


def _program():
    """The name the user typed: the installed command, or python3 -m with the package they ran."""
    path = sys.argv[0] or ''
    name = os.path.basename(path)
    if name in ('intrinsicvalue', 'investmentideas', 'intrinsicvalue.exe', 'investmentideas.exe'):
        return name.rsplit('.', 1)[0] if name.endswith('.exe') else name
    package = os.path.basename(os.path.dirname(path)) if name == '__main__.py' else ''
    return f'python3 -m {package}' if package in ('intrinsicvalue', 'investmentideas') else 'python3 -m intrinsicvalue'


def _port(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f'{value} is not a port number') from None
    if not 0 <= number <= 65535:
        raise argparse.ArgumentTypeError(f'{value} is not a port number; use one from 1024 to 65535, for example 8766 '
                                         '(0 picks a free one)')
    return number


def build_parser(prog=None):
    prog = prog or _program()
    parser = argparse.ArgumentParser(
        prog=prog, formatter_class=argparse.RawDescriptionHelpFormatter,
        description='IntrinsicValue: value a company from its SEC filings and write a research memo. '
                    'Valuation research only; it is not investment advice.',
        epilog=EXAMPLES.format(prog=prog))
    parser.add_argument('ticker', nargs='?', help='Stock ticker, for example MSFT. Leave it out to open the browser form.')
    parser.add_argument('--version', action='version', version=f'IntrinsicValue {__version__}')
    parser.add_argument('--setup', action='store_true',
                        help='Ask for your OpenRouter key and email once and save them in .env in this folder')
    parser.add_argument('--dry-run', action='store_true',
                        help='Collect evidence and check inputs only: no key needed, no model calls, nothing charged')
    parser.add_argument('--list', action='store_true', help='List past runs under the output folder, newest first')
    parser.add_argument('--open', action='store_true', help='Open the finished memo in your browser')
    parser.add_argument('--quick', action='store_true', help='A four-call overview with no valuation model (about $0.50)')
    parser.add_argument('--profile', choices=MODELS, default='balanced', metavar='{balanced,economy}',
                        help='Models to use: balanced (Claude Opus writes, GPT reviews; the default) or economy (open-weight models, cheaper)')
    parser.add_argument('--budget', type=float,
                        help='Most the run may spend, in US dollars, counting earlier attempts (default 20; 2 with --quick). '
                             'The run stops before a call would go over it')
    parser.add_argument('--sec-contact', metavar='EMAIL',
                        help='Your email for SEC access, for this run only (or save it with --setup)')
    parser.add_argument('--output', default='research-output', metavar='FOLDER',
                        help='Where runs are saved (default: research-output in this folder)')
    parser.add_argument('--port', type=_port, default=8765, help='Port for the browser form (default 8765)')
    parser.add_argument('--no-browser', action='store_true', help='Do not open a browser window when the form starts')
    parser.add_argument('--revalue', type=Path, metavar='ASSUMPTIONS',
                        help='Recompute an edited assumptions.json with no key or model calls; prints the new values')
    advanced = parser.add_argument_group('advanced options')
    advanced.add_argument('--evidence', type=Path, metavar='FILE', help='Extra filing or research text to include (UTF-8, up to 100 KB)')
    advanced.add_argument('--plan-samples', type=int, choices=range(1, 6), metavar='N',
                          help='Forecasts written from the same evidence, 1 to 5, combined input by input '
                               '(default 3 for balanced, 1 for economy)')
    advanced.add_argument('--max-revisions', type=int, default=None, metavar='N',
                          help='Revision rounds after the first review, 0 to 8 (default 3)')
    advanced.add_argument('--easy-model', metavar='MODEL', help='OpenRouter model ID for extraction and web research')
    advanced.add_argument('--strong-model', metavar='MODEL', help='OpenRouter model ID for the forecast and the memo')
    advanced.add_argument('--review-model', metavar='MODEL', help='OpenRouter model ID for the independent review')
    advanced.add_argument('--sources', type=Path, metavar='FILE', help='JSON list of extra archived sources (up to 3 MB)')
    advanced.add_argument('--snapshot', type=Path, metavar='FILE',
                          help='Replay a saved source list instead of collecting evidence (up to 3 MB)')
    advanced.add_argument('--forecast-plan', type=Path, metavar='FILE', help='Review your own forecast JSON (up to 150 KB)')
    advanced.add_argument('--draft', type=Path, metavar='FILE', help='Review your edited memo text in the same run (up to 150 KB)')
    advanced.add_argument('--counterfactuals', type=Path, metavar='FILE',
                          help='JSON object of named what-if changes to the base case (up to 64 KB)')
    advanced.add_argument('--review-notes', type=Path, metavar='FILE',
                          help='Reopen a run with your own review notes (UTF-8, up to 30 KB)')
    return parser


def say(text=''):
    print(text, flush=True)


def warn(text):
    print(text, file=sys.stderr, flush=True)


def read_bounded(path, limit, flag):
    """The bytes of a regular file of at most ``limit`` bytes; errors name the flag and the file."""
    path = Path(path)
    try:
        if not path.exists():
            raise ValueError(f'{flag}: {path} does not exist')
        if not path.is_file():
            raise ValueError(f'{flag}: {path} is not a regular file')
        if path.stat().st_size > limit:
            raise ValueError(f'{flag}: {path} is larger than the {limit // 1000:,} KB allowed')
        with path.open('rb') as handle:
            data = handle.read(limit + 1)
    except OSError as exc:
        raise ValueError(f'{flag}: {path} cannot be read ({exc.strerror or exc})') from None
    if len(data) > limit:
        raise ValueError(f'{flag}: {path} is larger than the {limit // 1000:,} KB allowed')
    return data


def read_text(path, limit, flag):
    try:
        return read_bounded(path, limit, flag).decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError(f'{flag}: {path} is not UTF-8 text') from None


def _read_json(path, limit, what, flag=None):
    text = read_text(path, limit, flag or what)
    try:
        return json.loads(text)
    except RecursionError:
        raise ValueError(f'{flag or what}: {path} is nested too deeply to be {what}') from None
    except ValueError as exc:
        if 'digits' in str(exc):
            raise ValueError(f'{flag or what}: {path} has a number too long to read') from None
        raise ValueError(f'{flag or what}: {path} is not valid JSON, so it cannot be used as {what}') from None


def revalue(path, output):
    """Recompute an edited assumptions file and return the lines to print."""
    from . import runs
    from .valuation import evaluate, save_valuation
    from .pipeline import attach_diagnostics
    if not Path(path).exists():
        raise ValueError(f'--revalue: {path} does not exist. Give the path to assumptions.json inside a run folder '
                         '(python3 -m intrinsicvalue --list shows the folders).')
    try:
        assumptions = json.loads(read_text(path, 1_000_000, '--revalue'))
    except (ValueError, RecursionError) as exc:
        if str(exc).startswith('--revalue'):
            raise
        raise ValueError(f'--revalue: {Path(path).name} is not an assumptions.json file from a run (it is not valid JSON).') from None
    if not isinstance(assumptions, dict) or not isinstance(assumptions.get('scenarios'), dict):
        raise ValueError(f'--revalue: {Path(path).name} is not an assumptions.json file from a run (it has no scenarios).')
    results = attach_diagnostics(assumptions, evaluate(assumptions))
    previous_path = Path(path).parent / 'valuation.json'
    before, previous = {}, None
    if previous_path.is_file():
        try:
            previous = json.loads(read_text(previous_path, 20_000_000, 'the saved valuation.json'))
            before = runs.headline(Path(path).parent)
        except (ValueError, RecursionError):
            previous = None  # a damaged neighbour is not compared with; the new valuation stands alone
    if isinstance(previous, dict):
        from .consensus import carry
        carry(previous, assumptions, results)
    folder = Path(output)
    folder.mkdir(parents=True, exist_ok=True)
    save_valuation(folder, assumptions, results)
    return revalue_table(results, before, folder)


def revalue_table(results, before, folder):
    from .runs import per_share
    if not isinstance(results, dict) or not isinstance(results.get('scenarios'), dict):
        return [str(Path(folder) / 'valuation.json')]
    currency = results.get('currency') or 'USD'
    price = results.get('market_price')
    lines = ['Recomputed with no model calls and nothing charged.', '',
             f'{"Case":6} {"Value per share":>16} {"Before":>12} {"Change":>10}' + (f' {"Value / price":>14}' if price else '')]
    for name in ('bear', 'base', 'bull'):
        value = (results['scenarios'].get(name) or {}).get('per_share_value')
        old = before.get(name)
        change = (f'{value - old:+,.2f}' if isinstance(value, (int, float)) and isinstance(old, (int, float)) else '')
        ratio = f' {value / price:>14.2f}' if price and isinstance(value, (int, float)) else (' ' * 15 if price else '')
        lines.append(f'{name.title():6} {per_share(value, currency):>16} {per_share(old, currency) if old is not None else "":>12} '
                     f'{change:>10}{ratio}')
    if price:
        lines.append(f'Price used: {per_share(price, currency)}. The base case is the headline value; bear and bull are stress cases.')
    if not before:
        lines.append('No saved valuation.json was beside the assumptions file, so there is nothing to compare with.')
    lines += ['', f'Saved: {Path(folder).resolve() / "valuation.json"} (with cashflows.csv and sensitivity.csv)']
    return lines


def _serve(args):
    from .web import serve
    extra = {'sec_contact': args.sec_contact} if args.sec_contact else {}
    if args.no_browser:
        extra['open_browser'] = False
    try:
        serve(args.port, args.output, default_mode='quick' if args.quick else 'full', **extra)
    except OSError as exc:
        if exc.errno in (errno.EADDRINUSE, getattr(errno, 'WSAEADDRINUSE', -1)) or 'in use' in str(exc).lower():
            raise ValueError(f'Port {args.port} is busy. IntrinsicValue may already be running: open '
                             f'http://127.0.0.1:{args.port}. Otherwise start it on another port: '
                             f'python3 -m intrinsicvalue --port {args.port + 1 if args.port < 65535 else 8766}') from None
        raise


def _open(path):
    import webbrowser
    try:
        webbrowser.open(Path(path).resolve().as_uri())
    except (OSError, webbrowser.Error):
        warn(f'Could not open a browser; open {path} yourself.')


def main(prog=None):
    parser = build_parser(prog)
    args = parser.parse_args()
    if args.setup:
        if args.ticker or args.revalue or args.list:
            parser.error('--setup is run on its own')
        from .settings import run_setup
        try:
            say(run_setup())
        except (KeyboardInterrupt, EOFError):
            warn('\nSetup cancelled; nothing was saved.')
            sys.exit(1)
        except (ValueError, OSError) as exc:
            warn(f'Setup stopped: {exc}')
            sys.exit(1)
        return
    if args.list:
        from .runs import format_list, list_runs
        say(format_list(list_runs(args.output), args.output))
        return
    if args.output == '' or (isinstance(args.output, str) and not args.output.strip()):
        parser.error('--output needs a folder name')
    if args.revalue:
        research_only = [flag for flag, used in (
            ('a ticker', args.ticker is not None), ('--quick', args.quick), ('--dry-run', args.dry_run),
            ('--evidence', args.evidence), ('--counterfactuals', args.counterfactuals), ('--draft', args.draft),
            ('--snapshot', args.snapshot), ('--forecast-plan', args.forecast_plan), ('--sources', args.sources),
            ('--review-notes', args.review_notes), ('--review-model', args.review_model), ('--budget', args.budget is not None),
            ('--profile', args.profile != 'balanced'), ('--plan-samples', args.plan_samples),
            ('--max-revisions', args.max_revisions is not None), ('--easy-model', args.easy_model),
            ('--strong-model', args.strong_model), ('--sec-contact', args.sec_contact), ('--open', args.open)) if used]
        if research_only:
            parser.error('--revalue recomputes a saved file with no model calls, so it takes only --output; '
                         'leave out ' + ', '.join(research_only))
        output = args.output if args.output != 'research-output' else args.revalue.parent / 'revalued'
        try:
            for line in revalue(args.revalue, output):
                say(line)
        except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
            warn(f'Stopped: {exc}')
            sys.exit(1)
        return
    if args.budget is None:
        args.budget = 2.0 if args.quick else 20.0  # pipeline.DEFAULT_BUDGET: a ceiling, not the expected bill
    if args.ticker is not None and not args.ticker.strip():
        parser.error('the ticker is empty. Name one, for example MSFT, or leave it out to open the browser form')
    try:
        if not math.isfinite(args.budget) or not 0 < args.budget <= 100:
            raise ValueError('The budget must be more than $0 and at most $100')
        # A `.env` file in the working folder supplies the key and the SEC contact once,
        # for the browser form and the command line alike. Exported values win.
        from .settings import apply_env_file, describe
        filled = apply_env_file()
        if filled:
            warn(describe(filled))
        if args.ticker is None:
            _serve(args)
            return
    except (ValueError, OSError) as exc:
        warn(f'Stopped: {exc}')
        sys.exit(1)
    _research(args)


def _inputs(args):
    """Read every optional input file before any network work, each bounded and named."""
    evidence = read_text(args.evidence, 100_000, '--evidence') if args.evidence else ''
    if len(evidence.encode()) > 100_000:
        raise ValueError(f'--evidence: {args.evidence} is larger than 100 KB; provide a focused excerpt')
    extra = {}
    if not args.quick:
        notes = read_text(args.review_notes, 30_000, '--review-notes') if args.review_notes else ''
        sources = _read_json(args.sources, 3_000_000, 'supplemental sources', '--sources') if args.sources else None
        extra = {'max_revisions': 3 if args.max_revisions is None else args.max_revisions, 'review_notes': notes,
                 'extra_sources': sources, 'plan_samples': args.plan_samples}
        if args.snapshot:
            extra['source_snapshot'] = _read_json(args.snapshot, 3_000_000, 'a source snapshot', '--snapshot')
        if args.forecast_plan:
            extra['forecast_plan'] = _read_json(args.forecast_plan, 150_000, 'a forecast plan', '--forecast-plan')
        if args.review_model:
            extra['review_model'] = args.review_model
        if args.draft:
            extra['draft'] = read_text(args.draft, 150_000, '--draft')
        if args.counterfactuals:
            extra['counterfactuals'] = _read_json(args.counterfactuals, 64_000, 'counterfactuals', '--counterfactuals')
    else:
        full_only = [flag for flag, used in (
            ('--review-notes', args.review_notes), ('--counterfactuals', args.counterfactuals), ('--draft', args.draft),
            ('--snapshot', args.snapshot), ('--sources', args.sources), ('--forecast-plan', args.forecast_plan),
            ('--plan-samples', args.plan_samples), ('--max-revisions', args.max_revisions is not None),
            ('--review-model', args.review_model)) if used]
        if full_only:
            raise ValueError(', '.join(full_only) + ' apply only to full research; leave out --quick or leave them out')
    return evidence, extra


def preflight_checks(args, environ=None, key_check=None):
    """Ticker, SEC email, key and a free key check, in that order. Returns (ticker, key)."""
    from . import preflight
    environ = os.environ if environ is None else environ
    supplied = bool(args.snapshot or args.sources)
    if supplied:
        from .core import symbol
        ticker = symbol(args.ticker)
    else:
        ticker = preflight.check_ticker(args.ticker)
    if not args.snapshot:
        preflight.check_contact(args.sec_contact, environ)
    if args.dry_run:
        return ticker, ''
    key = preflight.check_key_format(environ.get('OPENROUTER_API_KEY', ''))
    info = (key_check or preflight.check_key)(key)
    remaining = info.get('limit_remaining') if isinstance(info, dict) else None
    note = f' It has ${remaining:,.2f} of its spending limit left.' if remaining is not None else ''
    say(f'Checked: {ticker} is in the SEC company list, the SEC email is set and OpenRouter accepted the key.{note}')
    if remaining is not None and remaining < 0.5:
        raise preflight.PreflightError(f'The key has only ${remaining:.2f} of its spending limit left, too little for a run. '
                                       'Raise the limit at https://openrouter.ai/settings/keys; nothing was charged.')
    return ticker, key


def _research(args):
    from . import explain, memo_html, runs
    from .preflight import PreflightError
    from .progress import Reporter
    started = time.time()
    try:
        evidence, extra = _inputs(args)
        ticker, key = preflight_checks(args)
    except PreflightError as exc:
        warn(f'Stopped before starting: {exc}')
        sys.exit(1)
    except (ValueError, OSError) as exc:
        warn(f'Stopped before starting: {exc}')
        sys.exit(1)
    except KeyboardInterrupt:
        warn('\nStopped. Nothing was started or charged.')
        sys.exit(130)
    if args.dry_run:
        say('Dry run: collecting evidence and checking inputs. No model is called and nothing is charged.')
    say('Keep this window open until the run finishes. Press Ctrl+C to stop; finished steps are saved.')
    if args.quick:
        runner = run
    else:
        from .pipeline import run as runner
    reporter = Reporter(args.output, ticker, quick=args.quick, out=say)
    folder, stopped = None, None
    try:
        with sec_contact_scope(args.sec_contact):
            folder = Path(runner(ticker, key, profile=args.profile, budget=args.budget, directory=args.output,
                                 evidence=evidence, easy_model=args.easy_model, strong_model=args.strong_model,
                                 progress=reporter, **extra))
    except KeyboardInterrupt:
        found = runs.find_run_folder(args.output, ticker, started)
        cost = runs.cost_of(found) if found else None
        spent = f' Spent so far: {runs.money(cost["total"])}.' if cost and cost['total'] > 0 else ' Nothing was charged.'
        warn(f'\nStopped. Finished steps are saved; run the same command again to resume.{spent}')
        sys.exit(130)
    except (ValueError, RuntimeError, OSError, KeyError, IndexError, TypeError) as exc:
        raw = explain.redact(str(exc), key, args.sec_contact or '')
        if args.dry_run and raw.startswith('Set OPENROUTER_API_KEY'):
            folder = Path(getattr(exc, 'folder', None) or runs.find_run_folder(args.output, ticker, started) or args.output)
            say('Dry run finished: evidence was collected and the inputs passed their checks. Nothing was charged.')
            say(f'Files: {folder.resolve()}')
            say('To write the memo, save your key with python3 -m intrinsicvalue --setup and run the same command '
                'without --dry-run; the collected evidence is reused.')
            return
        stopped = explain.plain(raw, ticker=ticker)
        found = getattr(exc, 'folder', None)
        folder = Path(found) if found else runs.find_run_folder(args.output, ticker, started)
        if folder is None:
            warn(f'Stopped: {stopped}')
            sys.exit(1)
        runs.replace_stale_memo(folder, started, stopped, 'Fix the cause above and run the same command again; '
                                'finished steps are reused.')
    html_path = None
    try:
        html_path = memo_html.write_beside(folder)
    except OSError as exc:
        warn(f'The readable memo.html could not be written: {exc.strerror or exc}')
    say('')
    for line in runs.final_summary(folder, budget=args.budget, stopped=stopped):
        (warn if stopped and line.startswith('Stopped') else say)(line)
    if not stopped:
        say(_next_step(folder))
    if args.open and html_path:
        _open(html_path)
    if stopped:
        sys.exit(1)


def _next_step(folder):
    from .runs import summarize
    row = summarize(folder)
    if row['status'] in ('needs_review', 'unresolved_findings'):
        return ('Next: read the open findings under "Review status" at the end of the memo. To change the '
                f'forecast, edit {Path(folder).resolve() / "assumptions.json"} and run --revalue on it.')
    if row.get('base') is not None:
        return (f'Next: to try your own assumptions, edit {Path(folder).resolve() / "assumptions.json"} and run '
                f'python3 -m intrinsicvalue --revalue "{Path(folder).resolve() / "assumptions.json"}"')
    return 'Next: python3 -m intrinsicvalue --list shows every saved run.'


if __name__ == '__main__':
    main()

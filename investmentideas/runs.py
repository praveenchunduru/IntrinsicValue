"""Saved runs as a person sees them: a plain status, the headline values, the cost and the memo.

Everything here reads files a run already wrote. Nothing is fetched and no model is called.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

# What each saved status means, and what to do next.
STATUS = {
    'reviewed_draft': ('Finished', 'The independent review left no major findings open.'),
    'needs_review': ('Finished, review findings open',
                     'The reviewer left findings open. They are listed under "Review status" at the end of the memo.'),
    'unresolved_findings': ('Finished, review findings open',
                            'Major review findings were still open after the last revision round. They are listed under '
                            '"Review status" at the end of the memo; read them before relying on the value.'),
    'budget_paused': ('Paused before paid research',
                      'The spending allowance is below what the first draft and review need. Run again with a larger --budget.'),
    'missing_valuation': ('Stopped, inputs missing',
                          'The filings did not give every input the valuation needs. The memo explains which ones.'),
    'in_progress': ('Not finished', 'Run the same command again to continue; finished steps are reused.'),
}


def _read_json(path, limit=25_000_000):
    try:
        if path.stat().st_size > limit:
            return None
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def money(value):
    return f'${value:,.2f}' if _number(value) is not None else 'unknown'


def per_share(value, currency='USD'):
    if _number(value) is None:
        return 'n/a'
    return (f'${value:,.2f}' if currency in (None, 'USD') else f'{currency} {value:,.2f}')


def cost_of(folder):
    """{'total', 'known', 'held'} in dollars from the run's cost files, or None."""
    folder = Path(folder)
    summary = _read_json(folder / 'cost-summary.json', 5_000_000)
    totals = summary.get('totals') if isinstance(summary, dict) else None
    if isinstance(totals, dict) and _number(totals.get('total_charged_or_reserved_usd')) is not None:
        return {'total': totals['total_charged_or_reserved_usd'], 'known': _number(totals.get('known_usage_cost_usd')) or 0.0,
                'held': _number(totals.get('retained_estimates_usd')) or 0.0}
    ledger = _read_json(folder / 'usage.json', 5_000_000)
    if not isinstance(ledger, list):
        state = _read_json(folder / 'state.json')
        ledger = state.get('ledger') if isinstance(state, dict) else None
    if isinstance(ledger, list):
        rows = [row for row in ledger if isinstance(row, dict)]
        charges = [_number(row.get('charged_or_reserved_usd')) for row in rows]
        if all(charge is not None for charge in charges):
            known = sum(_number((row.get('usage') or {}).get('cost') if isinstance(row.get('usage'), dict) else None) or 0
                        for row in rows if row.get('status') in ('complete', 'received', 'incomplete'))
            total = sum(charges)
            return {'total': total, 'known': min(known, total), 'held': max(total - known, 0.0)}
    return None


def cost_line(cost, budget=None):
    if not cost:
        return 'Cost: nothing was charged.' if cost is not None else 'Cost: unknown.'
    if cost['total'] <= 0:
        return 'Cost: nothing was charged.'
    text = f'Cost: {money(cost["known"])} reported by OpenRouter'
    if cost['held'] > 0.005:
        text += f', plus {money(cost["held"])} held for calls whose charge was not reported'
    if budget:
        text += f' (allowance {money(budget)})'
    return text + '.'


def headline(folder):
    """Base, bear and bull value per share and value / price from valuation.json, or {}."""
    valuation = _read_json(Path(folder) / 'valuation.json')
    if not isinstance(valuation, dict):
        return {}
    scenarios = valuation.get('scenarios') or {}
    values = {name: _number((scenarios.get(name) or {}).get('per_share_value')) for name in ('bear', 'base', 'bull')}
    price = _number(valuation.get('market_price'))
    out = {'currency': valuation.get('currency') or 'USD', 'price': price, **values}
    if values['base'] is not None and price:
        out['ratio'] = values['base'] / price
    return out


def _started(folder):
    marker = _read_json(Path(folder) / 'run-identity.json', 100_000)
    stamp = marker.get('started_at') if isinstance(marker, dict) else None
    if isinstance(stamp, str):
        try:
            return datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()
        except ValueError:
            pass
    try:
        stat = Path(folder).stat()
        return getattr(stat, 'st_birthtime', None) or stat.st_mtime
    except OSError:
        return None


def summarize(folder):
    """A plain description of one saved run folder."""
    folder = Path(folder)
    state = _read_json(folder / 'state.json') or {}
    full = str(state.get('version', '')).startswith('full-') or folder.name.startswith('full-')
    code = state.get('quality_status') if full else ('complete' if state.get('complete') else 'in_progress')
    reviewed = bool(state.get('review_history')) and not state.get('pending_revision')
    if not full and state.get('complete'):
        label, detail = 'Quick overview finished', 'A four-call overview; it has no valuation model.'
    elif full and not state.get('complete') and code in (None, 'in_progress', 'needs_review') and \
            not state.get('current_memo') and not state.get('stages'):
        label, detail = 'Evidence collected, no memo yet', ('Run the same command (without --dry-run, with a key) '
                                                             'to write the memo; the evidence is reused.')
    elif full and code in ('needs_review', 'in_progress') and not state.get('complete') and not reviewed:
        label, detail = STATUS['in_progress']
    else:
        label, detail = STATUS.get(code, STATUS['in_progress'])
    memo = folder / 'memo.md'
    html = folder / 'memo.html'
    when = _started(folder)
    try:
        updated = max(p.stat().st_mtime for p in (folder / 'state.json', folder) if p.exists())
    except ValueError:
        updated = when
    return {'folder': folder, 'ticker': state.get('ticker') or folder.parent.name, 'full': full,
            'status': code or 'in_progress', 'label': label, 'detail': detail,
            'complete': bool(state.get('complete')), 'started': when, 'updated': updated,
            'cost': cost_of(folder), **headline(folder),
            'memo': memo if memo.is_file() else None, 'html': html if html.is_file() else None}


def _date(stamp):
    if not stamp:
        return 'unknown   '
    return datetime.fromtimestamp(stamp).strftime('%Y-%m-%d %H:%M')


def list_runs(directory):
    """Every run folder under ``directory`` (TICKER/<run>/state.json), newest first."""
    base = Path(directory)
    if not base.is_dir():
        return []
    rows = []
    for state in base.glob('*/*/state.json'):
        if state.parent.parent.parent.resolve() != base.resolve():
            continue
        rows.append(summarize(state.parent))
    rows.sort(key=lambda row: row.get('updated') or 0, reverse=True)
    return rows


def format_list(rows, directory):
    if not rows:
        return (f'No saved runs under {Path(directory).resolve()}. Start one with: python3 -m intrinsicvalue MSFT\n'
                '(Runs saved somewhere else can be listed with --list --output <that folder>.)')
    lines = [f'Saved runs under {Path(directory).resolve()}, newest first:', '']
    header = f'{"Date":16}  {"Ticker":7} {"Status":31} {"Base value":>11} {"Value/price":>11} {"Cost":>8}  Memo'
    lines += [header, '-' * len(header)]
    for row in rows:
        ratio = f'{row["ratio"]:.2f}' if row.get('ratio') is not None else 'n/a'
        cost = money(row['cost']['total']) if row.get('cost') else 'n/a'
        memo = row.get('html') or row.get('memo')
        lines.append(f'{_date(row.get("updated")):16}  {row["ticker"][:7]:7} {row["label"][:31]:31} '
                     f'{per_share(row.get("base"), row.get("currency")):>11} {ratio:>11} {cost:>8}  '
                     f'{memo.resolve() if memo else "(no memo yet)"}')
    return '\n'.join(lines)


def final_summary(folder, *, budget=None, stopped=None):
    """The lines printed at the end of a run: what happened, the value, the cost, the memo, what next."""
    row = summarize(folder)
    lines = []
    if stopped:
        lines.append(f'Stopped: {stopped}')
    else:
        lines.append(f'{row["label"]}: {row["ticker"]}. {row["detail"]}')
    if row.get('base') is not None:
        value = (f'Base value {per_share(row["base"], row["currency"])} a share'
                 + (f' against a price of {per_share(row["price"], row["currency"])}, value / price {row["ratio"]:.2f}'
                    if row.get('ratio') is not None else '')
                 + (f'; bear {per_share(row["bear"], row["currency"])}, bull {per_share(row["bull"], row["currency"])}'
                    if row.get('bear') is not None and row.get('bull') is not None else '') + '.')
        lines.append(value)
    lines.append(cost_line(row['cost'], budget))
    target = row.get('html') or row.get('memo')
    if target:
        lines.append(f'Memo: {target.resolve()}')
    lines.append(f'All files: {Path(folder).resolve()}')
    return lines


def find_run_folder(directory, ticker, since):
    """The newest run folder for ``ticker`` written at or after ``since`` (a time.time() value), or None."""
    base = Path(directory) / ticker
    if not base.is_dir():
        return None
    best = None
    for child in base.iterdir():
        try:
            touched = max(p.stat().st_mtime for p in [child, *child.glob('*.json')])
        except (OSError, ValueError):
            continue
        if child.is_dir() and touched >= since - 2 and (best is None or touched > best[0]):
            best = (touched, child)
    return best[1] if best else None


def replace_stale_memo(folder, started, reason, next_step):
    """Make sure a stopped run's memo.md describes this stop, not an earlier attempt.

    A memo written during this attempt, or one bound to a completed review, is left alone.
    """
    folder = Path(folder)
    memo = folder / 'memo.md'
    state = _read_json(folder / 'state.json') or {}
    if state.get('complete') or state.get('completed_artifact_hashes'):
        return False
    try:
        if memo.is_file() and memo.stat().st_mtime >= started - 1:
            return False
    except OSError:
        return False
    ticker = state.get('ticker') or folder.parent.name
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    memo.write_text(f'# {ticker}: run stopped\n\nThis run stopped on {stamp} before a memo was written.\n\n'
                    f'Reason: {reason}\n\nNext step: {next_step}\n', encoding='utf-8')
    return True


RESUME_WINDOW_SECONDS = 24 * 3600


def started_at_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def resumable(folder, now=None):
    """Whether a run folder from an earlier UTC day is unfinished and was started within 24 hours."""
    folder = Path(folder)
    state = _read_json(folder / 'state.json')
    if not isinstance(state, dict) or state.get('complete'):
        return False
    marker = _read_json(folder / 'run-identity.json', 100_000)
    stamp = marker.get('started_at') if isinstance(marker, dict) else None
    if not isinstance(stamp, str):
        return False
    try:
        started = datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return False
    now = time.time() if now is None else now
    return 0 <= now - started <= RESUME_WINDOW_SECONDS

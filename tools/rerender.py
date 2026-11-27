"""Rebuild a saved run's memo with the current rendering code, at no cost.

Usage:
  python tools/rerender.py SAVED_RUN_FOLDER OUT.md [METHOD_NOTES.md]

The computed parts (first page, appendices, notes, sources and review footer)
are re-rendered from the saved plan, valuation and sources in state.json. The
saved prose sections are kept as they are. The house method notes go to
METHOD_NOTES.md, by default method-notes.md beside OUT.md, which the memo links,
and a readable OUT.html is written beside OUT.md. Nothing is fetched and no model
is called. The saved run folder is only read: an output path inside it is refused,
so a saved run cannot be overwritten.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from investmentideas import memo_html, pipeline  # noqa: E402


class RerenderStopped(Exception):
    """A plain reason the memo was not rebuilt; printed without a traceback."""


def _load_state(folder):
    folder = Path(folder)
    if not folder.is_dir():
        raise RerenderStopped(f'{folder} is not a folder; give the saved run folder that holds state.json')
    path = folder / 'state.json'
    if not path.is_file():
        raise RerenderStopped(f'{path} does not exist; give the saved run folder that holds state.json')
    try:
        state = json.loads(path.read_text(encoding='utf-8-sig'))
    except UnicodeDecodeError:
        raise RerenderStopped(f'{path} is not UTF-8 text') from None
    except json.JSONDecodeError as exc:
        raise RerenderStopped(f'{path} is empty' if not exc.doc.strip() else
                              f'{path} is not valid JSON ({exc.msg} at line {exc.lineno}); it may be cut short') from None
    except (OSError, RecursionError) as exc:
        raise RerenderStopped(f'{path} cannot be read ({exc})') from None
    if not isinstance(state, dict):
        raise RerenderStopped(f'{path} holds a JSON {type(state).__name__}; a saved run state is an object')
    shapes = (('current_memo', str, 'the memo prose'), ('sources', list, 'the source list'),
              ('gaps', list, 'the evidence gaps'), ('plan', dict, 'the forecast plan'),
              ('valuation', dict, 'the valuation'))
    for field, shape, what in shapes:
        if field in state and state[field] is not None and not isinstance(state[field], shape):
            raise RerenderStopped(f'{path}: {field} ({what}) must be a JSON {"text" if shape is str else "object" if shape is dict else "list"}')
    if not state.get('current_memo'):
        raise RerenderStopped(f'{path} has no memo prose (current_memo) to re-render')
    if 'sources' not in state:
        raise RerenderStopped(f'{path} has no sources list; the memo cites sources by their [S#] tags')
    if any(not isinstance(item, dict) or not isinstance(item.get('id'), str) for item in state['sources']):
        raise RerenderStopped(f'{path}: every entry in sources must be an object with an id such as "S1"')
    return state


def warnings(state):
    """What the rebuilt memo will leave out, so a thin memo is never silent."""
    notes = []
    if not state.get('plan') or state['plan'].get('unavailable'):
        notes.append('the saved run has no forecast plan, so the memo has no assumptions detail and its summary '
                     'carries no [S#] source tags')
    if not state.get('valuation'):
        notes.append('the saved run has no valuation, so the memo has no valuation summary or detail')
    return notes


def rerender(folder):
    """The memo text."""
    state = _load_state(folder)
    return pipeline._render(state['current_memo'], state['sources'], state.get('gaps') or [],
                            state.get('plan') or {}, state.get('valuation'), state)


def _output_path(folder, out, what='the memo'):
    """An output file outside the saved run folder; the run folder is only ever read."""
    folder, out = Path(folder).resolve(), Path(out)
    target = out.resolve()
    if target.is_dir():
        raise RerenderStopped(f'{out} is a folder; give a file name such as {out / "memo.md"}')
    if not target.parent.is_dir():
        raise RerenderStopped(f'The folder {out.parent} does not exist; create it or choose another output path')
    if folder == target.parent or folder in target.parents:
        raise RerenderStopped(f'{out} is inside the saved run folder {folder}, which this tool only reads; '
                              f'write {what} to a path outside it')
    return target


def _write(path, text):
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(text, encoding='utf-8')
    temp.replace(path)


def method_notes(folder):
    """The method-notes.md text that goes beside the memo."""
    state = _load_state(folder)
    return pipeline.method_notes_document(state['current_memo'], state['sources'], state.get('gaps') or [],
                                          state.get('plan') or {}, state.get('valuation'), state)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) not in (2, 3):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        state = _load_state(argv[0])
        target = _output_path(argv[0], argv[1])
        notes = _output_path(argv[0], argv[2] if len(argv) == 3 else target.with_name(pipeline.METHOD_NOTES_FILE),
                             'the method notes')
        _write(target, rerender(argv[0]))
        _write(notes, method_notes(argv[0]))
        html = memo_html.write_for(target) if target.suffix.lower() == '.md' else None
    except RerenderStopped as exc:
        print(f'rerender stopped: {exc}', file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        # The saved state passed the checks above but its contents could not be rendered.
        print(f'rerender stopped: the saved run in {argv[0]} could not be rendered '
              f'({type(exc).__name__}: {str(exc)[:300]}). Check state.json in that folder.', file=sys.stderr)
        return 1
    for note in warnings(state):
        print(f'rerender warning: {note}', file=sys.stderr)
    print(target)
    print(notes)
    if html:
        print(html)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

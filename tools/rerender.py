"""Rebuild a saved run's memo with the current rendering code, at no cost.

Usage:
  python tools/rerender.py SAVED_RUN_FOLDER OUT.md [METHOD_NOTES.md]

The computed parts (first page, appendices, notes, sources and review footer)
are re-rendered from the saved plan, valuation and sources in state.json. The
saved prose sections are kept as they are. The house method notes go to
METHOD_NOTES.md, by default method-notes.md beside OUT.md, which the memo links.
Nothing is fetched, no model is called and the saved run folder is only read.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from investmentideas import pipeline  # noqa: E402


def _saved(folder):
    state = json.loads((Path(folder) / 'state.json').read_text(encoding='utf-8'))
    if not state.get('current_memo'):
        raise ValueError('The saved run has no memo prose to re-render')
    return state


def rerender(folder):
    """The memo text."""
    state = _saved(folder)
    return pipeline._render(state['current_memo'], state['sources'], state.get('gaps', []),
                            state.get('plan', {}), state.get('valuation'), state)


def method_notes(folder):
    """The method-notes.md text that goes beside the memo."""
    state = _saved(folder)
    return pipeline.method_notes_document(state['current_memo'], state['sources'], state.get('gaps', []),
                                          state.get('plan', {}), state.get('valuation'), state)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) not in (2, 3):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    out = Path(argv[1])
    notes = Path(argv[2]) if len(argv) == 3 else out.with_name(pipeline.METHOD_NOTES_FILE)
    if out.resolve().parent == Path(argv[0]).resolve() or notes.resolve().parent == Path(argv[0]).resolve():
        print('Write the re-rendered files outside the saved run folder.', file=sys.stderr)
        return 2
    out.write_text(rerender(argv[0]), encoding='utf-8')
    notes.write_text(method_notes(argv[0]), encoding='utf-8')
    print(out)
    print(notes)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

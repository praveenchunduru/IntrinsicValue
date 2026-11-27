"""Rebuild a saved run's memo with the current rendering code, at no cost.

Usage:
  python tools/rerender.py SAVED_RUN_FOLDER OUT.md

The computed parts (first page, valuation detail, reported history, method
notes, sources and review status) are re-rendered from the saved plan,
valuation and sources in state.json. The saved prose sections are kept as
they are. Nothing is fetched and no model is called.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from investmentideas import pipeline  # noqa: E402


def rerender(folder):
    state = json.loads((Path(folder) / 'state.json').read_text(encoding='utf-8'))
    if not state.get('current_memo'):
        raise ValueError('The saved run has no memo prose to re-render')
    return pipeline._render(state['current_memo'], state['sources'], state.get('gaps', []),
                            state.get('plan', {}), state.get('valuation'), state)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    Path(argv[1]).write_text(rerender(argv[0]), encoding='utf-8')
    print(argv[1])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

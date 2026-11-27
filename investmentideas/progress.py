"""Progress a person can follow: plain step names, "step N of M", time so far and money so far.

The pipelines report internal stage names ("valuation_plan: anthropic/claude-opus-5.5").
This layer translates those messages for the command line and the browser; it does not
change what the pipelines do. It can also stop a run between steps (see RunStopped).
"""
from __future__ import annotations

from pathlib import Path
import re
import threading
import time

from .runs import cost_of, find_run_folder, money


class RunStopped(BaseException):
    """Raised at the start of a step after a stop was asked for.

    It derives from BaseException, as KeyboardInterrupt does, so the pipelines' own
    error handling does not treat it as a failed step. It is raised only where a new
    step announces itself, which is before that step reserves or spends anything.
    """


FULL_STEPS = 9
QUICK_STEPS = 5

# (pattern on the stage name, step number, plain description). First match wins.
_FULL = (
    (r'web_business', 2, 'researching the business on the web'),
    (r'web_challenge', 2, 'looking for evidence against the obvious view'),
    (r'web_expectations', 2, 'researching what the market expects'),
    (r'web_claims', 2, 'checking claims made about the company'),
    (r'web_', 2, 'researching on the web'),
    (r'evidence_synthesis', 3, 'building the dated evidence table'),
    (r'valuation_plan(_s\d+)?_repair', 4, 'repairing the forecast so it passes the checks'),
    (r'valuation_plan', 4, 'writing the forecast'),
    (r'full_draft', 5, 'writing the memo'),
    (r'draft_check_fix', 6, 'fixing problems the automatic checks found'),
    (r'review_format', 7, 'reformatting the independent review'),
    (r'review_', 7, 'independent review by a second model'),
    (r'finding_check', 7, 'double-checking the reviewer\'s findings'),
    (r'targeted_research', 8, 'researching the points the reviewer raised'),
    (r'model_response_format', 8, 'reformatting the forecast response'),
    (r'model_response', 8, 'deciding whether the forecast needs to change'),
    (r'prose_edits', 8, 'editing the memo for the review'),
    (r'revise', 8, 'rewriting the memo after the forecast changed'),
)
_QUICK = {'extract': (2, 'extracting the evidence table'), 'draft': (3, 'writing the overview'),
          'review': (4, 'reviewing the overview'), 'memo': (5, 'writing the final overview')}

_VENDORS = {'openai': 'GPT', 'gpt': 'GPT', 'deepseek': 'DeepSeek', 'kimi': 'Kimi', 'claude': 'Claude'}


def model_name(model):
    """'anthropic/claude-opus-5.5' -> 'Claude Opus 5.5'."""
    name = model.split('/', 1)[-1]
    name = re.sub(r'-\d{4}$', '', name)
    words = []
    for part in name.split('-'):
        words.append(_VENDORS.get(part.lower(), part[:1].upper() + part[1:]))
    return ' '.join(words)


def _round_of(stage):
    match = re.search(r'_(\d+)(?:_retry)?$', stage)
    return int(match.group(1)) if match else None


def _elapsed(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f'{seconds} s'
    if seconds < 3600:
        return f'{seconds // 60} min'
    return f'{seconds // 3600} h {seconds % 3600 // 60} min'


class Reporter:
    """A progress callback that prints (or stores) plain progress lines.

    ``Reporter(directory, ticker, out=print)`` is passed to a pipeline as ``progress``.
    ``stop()`` asks the run to stop before its next step.
    """

    def __init__(self, directory, ticker, *, quick=False, out=print, clock=time.monotonic, wall=time.time):
        self.directory, self.ticker, self.quick = Path(directory), ticker, quick
        self.out, self.clock = out, clock
        self.started, self.started_wall = clock(), wall()
        self.total = QUICK_STEPS if quick else FULL_STEPS
        self.step = 0
        self.last = ''
        self.stop_requested = threading.Event()

    def stop(self):
        self.stop_requested.set()

    def folder(self):
        return find_run_folder(self.directory, self.ticker, self.started_wall)

    def spent(self):
        folder = self.folder()
        cost = cost_of(folder) if folder else None
        return cost['total'] if cost else 0.0

    def elapsed(self):
        return self.clock() - self.started

    def translate(self, message):
        """(plain text, starts_a_step) for one pipeline progress message."""
        text = str(message)
        if text.startswith(('Collecting financial facts', 'Retrieving keyless market')):
            return self._step(1, 'collecting SEC filings and market data (free)'), True
        if text.startswith('Initial budget preflight'):
            floor = re.search(r'\$([0-9.,]+) known-context', text)
            allowance = re.search(r'\$([0-9.,]+) total allowance', text)
            if floor and allowance:
                return (f'The first draft and review need at least ${floor.group(1)} of your ${allowance.group(1)} '
                        'allowance; the run stops before it would go over.'), False
            return 'Checked the spending allowance.', False
        if text.startswith('Resuming the unfinished run'):
            return text + '.', False
        if text.startswith('Using completed research'):
            return 'This research is already finished; showing the saved result.', False
        match = re.match(r'Forecast plan sample (\d+) of (\d+)$', text)
        if match:
            return self._step(4, f'writing forecast {match.group(1)} of {match.group(2)}'), True
        if re.match(r'(Prose edits not applied|Prose edit retry not applied|Pre-review fix not applied|'
                    r'Finding check not used)', text):
            return 'One suggested change could not be applied; the next review sees the earlier wording.', False
        match = re.match(r'([a-z][a-z0-9_]*)(?: \(retry with a larger output limit\))?: (\S+/\S+)$', text)
        if match:
            stage, model = match.group(1), match.group(2)
            retry = ' (retry with more room)' if 'larger output limit' in text else ''
            if stage.endswith('_without_reasoning'):
                stage, retry = stage[:-len('_without_reasoning')], ' (retry without extended thinking)'
            if self.quick and stage in _QUICK:
                number, words = _QUICK[stage]
                return self._step(number, f'{words} ({model_name(model)}){retry}'), True
            for pattern, number, words in _FULL:
                if re.match(pattern, stage):
                    revision = _round_of(stage)
                    if number == 7 and revision:
                        words += f', round {revision + 1}'
                    elif number == 8 and revision:
                        words += f', revision {revision}'
                    return self._step(number, f'{words} ({model_name(model)}){retry}'), True
        if re.match(r'[a-z_]+: .*memo\.md$', text) or text.startswith('Complete: '):
            return '', False  # the end of a run; the closing summary says it in full
        return text, False

    def _step(self, number, words):
        self.step = max(self.step, number)
        return f'Step {number} of {self.total}: {words}.'

    def status_line(self, text):
        cost = self.spent()
        tail = f' [{_elapsed(self.elapsed())}' + (f', {money(cost)} so far]' if cost > 0 else ']')
        return text + tail

    def __call__(self, message):
        text, starts = self.translate(message)
        if starts and self.stop_requested.is_set():
            raise RunStopped()
        if not text:
            return
        self.last = text
        self.out(self.status_line(text))

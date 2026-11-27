"""Plain wording for the reasons a run stops, for the command line and for the browser form.

The pipelines raise precise, technical messages. This module rewords the common ones,
says what to do next, and for the browser replaces command-line flags with the form
field that does the same job. Secrets are removed from any message before it is shown.
"""
from __future__ import annotations

import re

KEYS_PAGE = 'https://openrouter.ai/settings/keys'


def redact(text, *secrets):
    for secret in secrets:
        if isinstance(secret, str) and len(secret.strip()) >= 4:
            text = text.replace(secret.strip(), '[hidden]')
    # Anything shaped like an OpenRouter key, whatever its source.
    return re.sub(r'sk-or-[A-Za-z0-9_\-]{8,}', '[hidden key]', text)


def _sentence(text):
    text = text.strip()
    while text.endswith('..'):
        text = text[:-1]
    return text if not text or text[-1] in '.!?' else text + '.'


def plain(message, *, browser=False, ticker=None):
    """A short, specific explanation of a stop, with the next step."""
    text = str(message).strip()
    command = f'python3 -m intrinsicvalue {ticker or "TICKER"}'
    match = re.match(r'HTTP (\d{3}) from openrouter\.ai', text)
    if match:
        code = int(match.group(1))
        if code in (401, 403):
            return f'OpenRouter rejected the key (HTTP {code}). Copy it again from {KEYS_PAGE}. Nothing was charged for the refused call.'
        if code == 402:
            return 'OpenRouter says the key is out of credit. Add credit at https://openrouter.ai/settings/credits, then start again with the same settings; finished steps are reused.'
        if code == 429:
            return 'OpenRouter is limiting requests. Wait a few minutes, then start again with the same settings; finished steps are reused.'
        return (f'OpenRouter returned an error (HTTP {code}). Wait a few minutes, then start again with the same '
                'settings; finished steps are reused.')
    if text.startswith('Network or invalid JSON response from'):
        host = text.rsplit(' ', 1)[-1]
        return f'Could not reach {host}, or it sent an unreadable answer. Check your connection and start again; finished steps are reused.'
    match = re.match(r'Budget would be exceeded before (\S+): \$([0-9.]+) used/reserved, \$([0-9.]+) reserved for next call', text)
    if match:
        spent, need = float(match.group(2)), float(match.group(3))
        how = ('raise the spending allowance in the form and start again with the same ticker'
               if browser else f'run the same command again with a larger --budget')
        return (f'The next step could cost up to ${need:.2f}, which would take the run past its allowance '
                f'(${spent:.2f} already spent or held). To continue, {how}; finished steps are reused.')
    if text.startswith('Initial budget preflight stopped'):
        floor = re.search(r'below the \$([0-9.,]+)', text)
        suggested = re.search(r'--budget ([0-9.]+)', text)
        need = f' at least ${floor.group(1)}' if floor else ' more'
        if browser:
            advice = (f'Set the spending allowance to ${float(suggested.group(1)):.0f} and start again'
                      if suggested and float(suggested.group(1)) <= 20 else
                      'The browser allows at most $20; choose the Economy profile or Quick overview, or run it from a terminal with a larger --budget')
        else:
            advice = (f'Run again with --budget {suggested.group(1)} (a ceiling; the run can cost less)'
                      if suggested else 'Choose --profile economy or --quick')
        return f'Nothing was charged. The first draft and review need{need} of allowance. {advice}.'
    if text.startswith('Reported-input preflight stopped before paid research'):
        detail = text.split(':', 1)[1].strip()
        missing = detail.split('.', 1)[0]
        if 'financial-institution valuation model' in text:
            return ('Nothing was charged. This company looks like a bank or insurer, which this tool does not value '
                    '(its model is for operating companies).')
        fix = ('From a terminal you can supply the missing filing data with '
               f'{command} --sources FILE (see docs/reference.md)' if browser else
               f'Supply the missing filing data with {command} --sources FILE (see docs/reference.md), or try again later')
        return (f'Nothing was charged. The SEC filings did not give every input the valuation needs '
                f'(missing or conflicting: {missing.replace("_", " ")}). The memo explains which ones. {fix}.')
    if text.startswith('Saved artifacts changed after review'):
        if browser:
            return ('Files in this run were edited after the review, so they are no longer shown as reviewed. '
                    'Start the same ticker again for a fresh review, or review your own edits from a terminal with '
                    f'{command} --draft FILE.')
        return _sentence(text)
    if text.startswith('No evidence could be retrieved'):
        return ('No evidence could be retrieved. Check the ticker and your internet connection'
                + (', or paste a filing excerpt under More options.' if browser else ', or add --evidence FILE with a filing excerpt.'))
    if text.startswith('Only market metadata is available'):
        return ('Only a share price was found, with no financial statements. '
                + ('Paste a filing excerpt under More options and start again.' if browser else 'Add --evidence FILE with a filing excerpt and run again.'))
    if text.startswith('Set OPENROUTER_API_KEY'):
        return ('Evidence is collected and checked, and nothing was charged. Add an OpenRouter key to write the memo'
                + ('.' if browser else ': python3 -m intrinsicvalue --setup'))
    if text.startswith('Model unavailable:'):
        return _sentence(text.replace('override the model', 'set a different model with --strong-model')) if not browser else \
            _sentence(text.split('.')[0]) + ' Choose the other profile and start again.'
    text = _sentence(text)
    if browser:
        text = re.sub(r'--budget ([0-9.]+)', lambda m: f'a spending allowance of ${m.group(1)}', text)
        text = text.replace('--evidence', 'the evidence box under More options')
        text = text.replace('--sec-contact', 'the email field')
        text = re.sub(r'--(sources|draft|forecast-plan|review-notes|counterfactuals|snapshot|max-revisions)\b',
                      lambda m: f'the command line option --{m.group(1)}', text)
    return text

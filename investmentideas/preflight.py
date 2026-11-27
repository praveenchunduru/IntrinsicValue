"""Checks made before any network work, so a first mistake fails in a second with a plain message.

The command line and the browser form use the same checks, in this order: the ticker is
in the SEC company list bundled with the package, an SEC contact email is set (unless
supplied evidence replaces SEC access), an OpenRouter key is set, and one free call to
OpenRouter's key endpoint confirms the key is accepted. No check prints or returns a
key or an email.
"""
from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request

from . import sec_directory
from .sec_http import _contact, open_http, user_agent

# GET /api/v1/key returns the calling key's limits and usage. It is free, and an
# unknown or revoked key answers 401 ("User not found"), checked 2026-11-27 against
# https://openrouter.ai/docs/api-reference/limits with a deliberately fake key.
KEY_URL = 'https://openrouter.ai/api/v1/key'
KEYS_PAGE = 'https://openrouter.ai/settings/keys'
SETUP_HINT = 'Run python3 -m intrinsicvalue --setup to save your key and email once.'


class PreflightError(ValueError):
    """An input problem found before any network work; the message says what to do next."""

    def __init__(self, message, field=None):
        super().__init__(message)
        self.field = field


_DIRECTORY = None


def _directory():
    global _DIRECTORY
    if _DIRECTORY is None:
        try:
            _DIRECTORY = list(sec_directory._read_snapshot()['directory'].values())
        except RuntimeError:
            _DIRECTORY = []
    return _DIRECTORY


def _same(a, b):
    return a.replace('-', '.') == b.replace('-', '.')


def _words(text):
    return re.findall(r'[a-z0-9]+', text.lower())


_NAME_FILLER = {'inc', 'corp', 'corporation', 'co', 'company', 'the', 'ltd', 'plc', 'holdings', 'group', 'sa', 'nv', 'ag', 'class'}


def suggestions(text, limit=3):
    """Tickers whose company name matches ``text`` word for word, best match first."""
    wanted = [word for word in _words(text) if word not in _NAME_FILLER and len(word) >= 2]
    if not wanted:
        return []
    scored = []
    for order, row in enumerate(_directory()):
        title = _words(row.get('title', ''))
        if all(any(word == part or (len(word) >= 4 and part.startswith(word)) for part in title) for word in wanted):
            # Earlier rows in the SEC list are the larger companies.
            scored.append((len(title), order, row))
    scored.sort(key=lambda item: (item[1], item[0]))
    seen, out = set(), []
    for _, _, row in scored:
        if row['ticker'] not in seen:
            seen.add(row['ticker'])
            out.append(f"{row['ticker']} ({row['title']})")
        if len(out) == limit:
            break
    return out


def check_ticker(value):
    """The ticker in upper case, or PreflightError naming the problem and likely tickers."""
    if not isinstance(value, str) or not value.strip():
        raise PreflightError('Enter a ticker, for example MSFT.', 'ticker')
    raw = value.strip()
    candidate = raw.upper().replace('.', '-') if raw.isascii() else ''
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9\-]{0,14}', candidate):
        found = suggestions(raw)
        if found:
            raise PreflightError(f'"{raw[:40]}" is not a ticker. Did you mean {_or(found)}?', 'ticker')
        raise PreflightError('Enter a ticker of 1 to 15 letters, digits, dots or hyphens, for example MSFT.', 'ticker')
    directory = _directory()
    if directory and not any(_same(row['ticker'].upper(), candidate) for row in directory):
        found = suggestions(raw)
        hint = f' Did you mean {_or(found)}?' if found else ' Check the symbol on sec.gov or your broker.'
        raise PreflightError(f'{candidate} is not in the SEC company list that comes with IntrinsicValue.{hint}', 'ticker')
    return candidate


def _or(items):
    return items[0] if len(items) == 1 else ', '.join(items[:-1]) + ' or ' + items[-1]


def has_contact(sec_contact=None, environ=None):
    environ = os.environ if environ is None else environ
    return bool((sec_contact or '').strip() or environ.get('SEC_CONTACT_EMAIL', '').strip()
                or environ.get('SEC_USER_AGENT', '').strip())


def check_contact(sec_contact=None, environ=None, *, browser=False):
    """Refuse a missing or malformed SEC contact email before anything is fetched."""
    if not has_contact(sec_contact, environ):
        if browser:
            raise PreflightError('Enter your email. The SEC asks every automated visitor for a contact '
                                 'and serves no filings without one.', 'sec_contact')
        raise PreflightError('No SEC contact email is set, so no filings can be fetched. ' + SETUP_HINT +
                             ' Or add --sec-contact you@example.com to this command.', 'sec_contact')
    if sec_contact:
        try:
            _contact(sec_contact)
        except ValueError:
            raise PreflightError('That email address does not look right. Enter it as name@example.com.',
                                 'sec_contact') from None


def check_key_format(key, *, browser=False):
    """Refuse a missing key, or one with spaces or line breaks inside it, before any network call."""
    if not isinstance(key, str) or not key.strip():
        if browser:
            raise PreflightError(f'Enter your OpenRouter key. You can create one at {KEYS_PAGE}.', 'key')
        raise PreflightError('No OpenRouter key is set. ' + SETUP_HINT +
                             ' To check inputs and collect evidence for free without a key, add --dry-run.', 'key')
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in key.strip()):
        raise PreflightError('The OpenRouter key contains spaces or line breaks. Copy it again from '
                             f'{KEYS_PAGE} and paste it as one line.', 'key')
    return key.strip()


def check_key(key, *, opener=open_http, timeout=20):
    """One free call that confirms OpenRouter accepts ``key``; returns its remaining limit, if any.

    Nothing is reserved or charged. The response body is read only for the key's
    remaining spending limit; no provider text is shown to the user.
    """
    key = check_key_format(key)
    request = Request(KEY_URL, headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json',
                                        'User-Agent': user_agent(KEY_URL)})
    try:
        with opener(request, timeout=timeout) as response:
            body = response.read(65536)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise PreflightError('That key was rejected by OpenRouter. Copy it again from '
                                 f'{KEYS_PAGE}; nothing was charged.', 'key') from None
        if exc.code == 402:
            raise PreflightError('OpenRouter says this key has no credit left. Add credit at '
                                 'https://openrouter.ai/settings/credits; nothing was charged.', 'key') from None
        if exc.code == 429:
            raise PreflightError('OpenRouter is limiting requests right now. Wait a minute and try again; '
                                 'nothing was charged.', 'key') from None
        raise PreflightError(f'OpenRouter did not answer the key check (HTTP {exc.code}). Try again in a few '
                             'minutes; nothing was charged.', 'key') from None
    except (URLError, OSError, ValueError) as exc:
        raise PreflightError(network_message(exc, 'openrouter.ai'), 'key') from None
    try:
        data = json.loads(body).get('data') or {}
        remaining = data.get('limit_remaining')
    except (ValueError, AttributeError):
        return {}
    if isinstance(remaining, (int, float)) and not isinstance(remaining, bool) and remaining >= 0:
        return {'limit_remaining': float(remaining)}
    return {}


def is_certificate_error(exc):
    reason = getattr(exc, 'reason', exc)
    text = f'{type(reason).__name__} {reason}'
    return 'CERTIFICATE_VERIFY_FAILED' in text or 'SSLCertVerificationError' in text


def certificate_fix():
    import sys
    version = f'{sys.version_info.major}.{sys.version_info.minor}'
    if sys.platform == 'darwin':
        return ('This Python has no web certificates installed. Fix it once by double-clicking '
                f'"Install Certificates.command" in Applications > Python {version}, or use the Mac\'s own /usr/bin/python3.')
    return 'This Python cannot verify web certificates. Install your system\'s CA certificates package and try again.'


def network_message(exc, host):
    if is_certificate_error(exc):
        return certificate_fix()
    return f'Could not reach {host}. Check your internet connection and try again; nothing was charged.'

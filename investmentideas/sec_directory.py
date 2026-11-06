"""Official SEC ticker-to-CIK lookup with a disclosed, dated mapping snapshot.

Only the public issuer directory is bundled. Financial facts always come from a
live source; a snapshot lookup never supplies or fabricates company financials.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
from importlib import resources
import json
from typing import Callable

DIRECTORY_URL = 'https://www.sec.gov/files/company_tickers.json'


def _validate_directory(value):
    if not isinstance(value, dict) or not value:
        raise ValueError('SEC ticker directory must be a nonempty object')
    for index, row in value.items():
        if not isinstance(index, str) or not index.isdecimal() or not isinstance(row, dict):
            raise ValueError('Invalid SEC ticker directory row')
        cik = row.get('cik_str')
        ticker, title = row.get('ticker'), row.get('title')
        if (isinstance(cik, bool) or not isinstance(cik, int) or not 0 < cik < 10**10 or
                not isinstance(ticker, str) or not ticker.strip() or len(ticker) > 30 or
                not isinstance(title, str) or not title.strip() or
                any(ord(char) < 32 for char in ticker)):
            raise ValueError('Invalid SEC issuer mapping')
    return value


def _read_snapshot():
    try:
        text = resources.files(__package__).joinpath('company_tickers.json').read_text(encoding='utf-8')
        snapshot = json.loads(text)
        directory = _validate_directory(snapshot['directory'])
        if snapshot['source_url'] != DIRECTORY_URL:
            raise ValueError('Unexpected snapshot source')
        timestamp = datetime.fromisoformat(snapshot['retrieved_at'].replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            raise ValueError('Missing snapshot retrieval timezone')
        digest = hashlib.sha256(json.dumps(directory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if digest != snapshot['directory_sha256']:
            raise ValueError('Snapshot integrity check failed')
        if len(snapshot['source_sha256']) != 64:
            raise ValueError('Missing original-source digest')
        return snapshot
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError('Bundled SEC ticker snapshot is missing or invalid; retry live SEC access or reinstall the package') from None


def snapshot_metadata():
    """Return public provenance for a caller's explicit fallback disclosure."""
    snapshot = _read_snapshot()
    return {key: snapshot[key] for key in ('source_url', 'retrieved_at', 'source_sha256', 'directory_sha256')}


def load_directory(fetch_callable: Callable):
    """Return ``(directory, used_snapshot)``; live official data takes precedence.

    The callable receives the official SEC URL and returns decoded JSON, as does
    the portable runner's request_json. A failed request or malformed response
    falls back to the validated public mapping snapshot. Callers must disclose
    that mapping's retrieval date and still fetch current company facts live.
    """
    try:
        return _validate_directory(fetch_callable(DIRECTORY_URL)), False
    except (RuntimeError, OSError, ValueError, TypeError, KeyError):
        return _read_snapshot()['directory'], True

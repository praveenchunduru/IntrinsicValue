"""Requester identity and redirect boundaries for public-source HTTP access."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
import re
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener, urlopen as _urlopen

from . import __version__

SEC_HOSTS = frozenset({'sec.gov', 'www.sec.gov', 'data.sec.gov'})
MAX_SOURCE_BYTES = 20_000_000
# No URL in the User-Agent: SEC (data.sec.gov / www.sec.gov) returns HTTP 403
# for any User-Agent containing "github" (verified live 2026-11-07 with curl:
# every UA variant containing github/github.com -> 403, the same request
# with the github token removed -> 200). The contact email appended by
# user_agent() for SEC hosts still identifies the requester, as SEC requires.
PRODUCT_USER_AGENT = f'IntrinsicValue/{__version__}'
_CONTACT = ContextVar('intrinsicvalue_sec_contact', default=None)
_EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}\Z")
_RATE_LOCK = threading.Lock()
_LAST_REQUEST = 0.0


class SECContactRequired(RuntimeError):
    """A real requester contact must be configured before accessing SEC."""


def _contact(value):
    if (not isinstance(value, str) or len(value) > 254 or any(ord(c) < 32 for c in value) or
            not _EMAIL.fullmatch(value.strip()) or '..' in value):
        raise ValueError('SEC contact must be an email address without header control characters')
    return value.strip()


@contextmanager
def sec_contact_scope(email=None):
    """Use a per-run contact without changing process environment or saved state."""
    if email is None or email == '':
        yield
        return
    token = _CONTACT.set(_contact(email))
    try:
        yield
    finally:
        _CONTACT.reset(token)


def user_agent(url):
    parsed=urlsplit(url)
    if parsed.hostname not in SEC_HOSTS:
        return PRODUCT_USER_AGENT
    if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443):
        raise ValueError('SEC requests require HTTPS on the standard port without URL credentials')
    scoped = _CONTACT.get()
    if scoped:
        return PRODUCT_USER_AGENT + ' ' + scoped
    configured = os.environ.get('SEC_USER_AGENT', '').strip()
    if configured:
        if len(configured) > 512 or any(ord(c) < 32 or ord(c) > 126 for c in configured):
            raise ValueError('SEC_USER_AGENT must be a single bounded ASCII header with a contact email')
        addresses = re.findall(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}", configured)
        if not addresses:
            raise ValueError('SEC_USER_AGENT must identify a contact email')
        _contact(addresses[-1])
        return configured
    email = os.environ.get('SEC_CONTACT_EMAIL', '').strip()
    if email:
        return PRODUCT_USER_AGENT + ' ' + _contact(email)
    raise SECContactRequired('SEC contact email required: use --sec-contact, the local SEC contact field, or SEC_CONTACT_EMAIL')


def _pace_sec(url):
    if urlsplit(url).hostname in SEC_HOSTS:
        global _LAST_REQUEST
        with _RATE_LOCK:
            delay = .25 - (time.monotonic() - _LAST_REQUEST)
            if delay > 0:
                time.sleep(delay)
            _LAST_REQUEST = time.monotonic()


class _RedirectBoundary(HTTPRedirectHandler):
    def __init__(self, authenticated=False, allowed_hosts=None):
        self.authenticated = authenticated
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if (self.authenticated or target.scheme != 'https' or target.username or target.password or
                target.port not in (None, 443) or
                (self.allowed_hosts is not None and target.hostname not in self.allowed_hosts)):
            raise HTTPError(req.full_url, code, 'HTTP redirect refused by request boundary', headers, fp)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header('Authorization')
            redirected.add_header('User-Agent', user_agent(newurl))
            _pace_sec(newurl)
        return redirected


def open_http(request, timeout=90, *, allowed_hosts=None):
    """Never redirect credentials; restrict source redirects before following."""
    url = request.full_url
    authenticated = request.has_header('Authorization')
    if authenticated and urlsplit(url).scheme != 'https':
        raise ValueError('Authenticated HTTP requests require HTTPS')
    if urlsplit(url).hostname in SEC_HOSTS:
        request.add_header('User-Agent', user_agent(url))
        if allowed_hosts is None:
            allowed_hosts = SEC_HOSTS
    _pace_sec(url)
    if authenticated or allowed_hosts is not None:
        return build_opener(_RedirectBoundary(authenticated, allowed_hosts)).open(request, timeout=timeout)
    return _urlopen(request, timeout=timeout)

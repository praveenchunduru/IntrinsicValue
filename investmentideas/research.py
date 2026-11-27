"""Keyless primary documents with bounded excerpts and reproducible archives.

Source text is untrusted evidence, never instructions. ``raw_document`` is a
public source archive for the caller to save, **not** part of the model prompt.
The data/excerpts fields are bounded and identify offsets in the full cleaned
text. Failure to retrieve a filing is a disclosed research gap.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import html
from html.parser import HTMLParser
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request

from .sec_directory import load_directory, snapshot_metadata
from .sec_http import MAX_SOURCE_BYTES, open_http as urlopen, user_agent
from .inputs import PLAN_TYPE_AXIS, PLAN_TYPE_CONCEPTS, PLAN_TYPE_MEMBERS, PRIMARY_INPUT_CONCEPTS
from .companyfacts import FINANCIAL_CONTEXT_CONCEPTS

MAX_DOCUMENT_BYTES = MAX_SOURCE_BYTES
MAX_EXCERPT_CHARS = 16_000
SEC_HOST = 'www.sec.gov'
ALLOWED_TEXT_HOSTS = {SEC_HOST}
# The release narrative ahead of its statement tables, within the 16,000-character excerpt bound.
RELEASE_OPENING_CHARS = 10_000
RELEASE_INTERPRETATION = ('Issuer statements, including forward-looking and promotional statements, require attribution; '
                          'they are not independent evidence of adoption or economic returns.')


class _PlainText(HTMLParser):
    """Preserve block/table boundaries and omit scripts and hidden XBRL headers."""
    _skip = {'script', 'style', 'noscript', 'head', 'ix:header'}
    _blocks = {'p', 'div', 'section', 'article', 'h1', 'h2', 'h3', 'h4', 'h5',
               'li', 'ul', 'ol', 'table', 'tr', 'br', 'hr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], []

    def handle_starttag(self, tag, attrs):
        if tag in self._skip:
            self.hidden.append(tag)
        if not self.hidden:
            if tag in self._blocks:
                self.parts.append('\n')
            elif tag in {'td', 'th'}:
                self.parts.append('\t')

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
            return
        if tag in self._blocks:
            self.parts.append('\n')
        elif tag in {'td', 'th'}:
            self.parts.append('\t')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def html_to_text(document):
    if not isinstance(document, str):
        raise ValueError('Document must be decoded text')
    parser = _PlainText()
    parser.feed(document)
    text = ''.join(parser.parts).replace('\xa0', ' ')
    lines = [re.sub(r'[ \t\r\f\v]+', ' ', line).strip() for line in text.split('\n')]
    return '\n'.join(line for line in lines if line)


def fetch_official_text(url):
    """Bounded official-host fetch; never accepts credentials or local URLs."""
    parsed = urlparse(url)
    if (parsed.scheme != 'https' or parsed.hostname not in ALLOWED_TEXT_HOSTS or
            parsed.username or parsed.password or parsed.port not in {None, 443}):
        raise ValueError('Only known HTTPS regulator/issuer documents are supported')
    request = Request(url, headers={'User-Agent': user_agent(url), 'Accept': 'text/html,application/xhtml+xml,application/xml,text/plain'})
    try:
        with urlopen(request, timeout=35, allowed_hosts=ALLOWED_TEXT_HOSTS) as response:
            final = urlparse(response.geturl())
            if final.scheme != 'https' or final.hostname not in ALLOWED_TEXT_HOSTS:
                raise RuntimeError('Official document redirected outside its allowed hosts')
            raw = response.read(MAX_DOCUMENT_BYTES + 1)
            if len(raw) > MAX_DOCUMENT_BYTES:
                raise RuntimeError('Official document exceeds the 20 MB retrieval limit')
            content_type = response.headers.get_content_type()
            if content_type not in {'text/html', 'application/xhtml+xml', 'application/xml', 'text/xml', 'text/plain', 'application/rss+xml'}:
                raise RuntimeError('Official document is not an HTML, XML or text source')
            encoding = response.headers.get_content_charset() or 'utf-8'
            return raw.decode(encoding, errors='replace')
    except HTTPError as exc:
        raise RuntimeError(f'Official document HTTP {exc.code} from {parsed.hostname}') from None
    except (URLError, TimeoutError, LookupError):
        raise RuntimeError(f'Official document unavailable from {parsed.hostname}') from None


_EXCERPT_HEADINGS = (
    ('business', r'(?im)^\s*(?:item\s*1\s*[.\-:]?\s*)?business\s*$'),
    ('risks', r'(?im)^\s*(?:item\s*1a\s*[.\-:]?\s*)?risk factors\s*$'),
    ('management discussion', r'(?im)^\s*(?:item\s*7\s*[.\-:]?\s*)?management[’\']?s discussion and analysis[^\n]*$'),
    ('segment disclosure', r'(?im)^\s*(?:note\s+\d+\s*[–\-:]\s*)?(?:segment information(?: and geographic data)?|segment operating performance)\s*$'),
    ('operating results', r'(?im)^\s*consolidated statements of (?:operations|income)\s*$'),
    ('balance sheet', r'(?im)^\s*consolidated balance sheets\s*$'),
    ('cash flow', r'(?im)^\s*consolidated statements of cash flows\s*$'),
)
# An earnings release titles its statements in its own words and puts them
# after its narrative: "CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS
# (Unaudited)" (Apple), "INCOME STATEMENTS" and "BALANCE SHEETS" (Microsoft),
# "STATEMENTS OF EARNINGS" (Home Depot), "Consolidated Earnings Information"
# (P&G), "statement of earnings" (Johnson & Johnson). Comprehensive income is
# not the income statement.
_STATEMENT_PREFIX = r'(?i)(?:condensed\s+)?(?:consolidated\s+)?'
_STATEMENT_SUFFIX = r'(?:\s*\(unaudited\))?'
RELEASE_STATEMENT_HEADINGS = (
    ('operating results', _STATEMENT_PREFIX + r'(?:statements?\s+of\s+(?:operations|income|earnings)|income\s+statements?|'
                          r'earnings\s+information)' + _STATEMENT_SUFFIX),
    ('balance sheet', _STATEMENT_PREFIX + r'(?:balance\s+sheets?|statements?\s+of\s+financial\s+position)' + _STATEMENT_SUFFIX),
    ('cash flow', _STATEMENT_PREFIX + r'(?:statements?\s+of\s+cash\s+flows?|cash\s+flows?\s+statements?)' + _STATEMENT_SUFFIX),
)
# Statements that end a window without one of their own: Amazon's statement of
# operations is followed by its statement of comprehensive income.
RELEASE_STATEMENT_BOUNDARY = (_STATEMENT_PREFIX + r'(?:statements?\s+of\s+comprehensive\s+(?:income|loss|earnings)(?:\s+\(loss\))?|'
                              r'comprehensive\s+income\s+statements?|statements?\s+of\s+(?:changes\s+in\s+)?'
                              r'(?:stockholders|shareholders|shareowners)[’\']?\s+equity)' + _STATEMENT_SUFFIX)
# Each statement window runs from its heading to the next statement heading,
# within these widths. Release statements measured 750 to 2,700 characters
# (16 issuers, 2026-11-27); the widths sum to 8,200, so the narrative opening
# keeps at least 7,800 of the 16,000-character excerpt bound.
RELEASE_STATEMENT_CHARS = {'operating results': 3000, 'balance sheet': 2600, 'cash flow': 2600}
_STATEMENT_STARTS = frozenset({'condensed', 'consolidated', 'statement', 'statements', 'income', 'balance', 'cash',
                               'earnings', 'comprehensive'})


def _heading_lines(text):
    """Yield original line offsets and normalized heading candidates once.

    Matching whole documents with leading whitespace lets it cross line breaks
    and repeatedly reconsider the same blank lines. Collapse internal spacing
    and omit empty candidates before applying the bounded heading patterns.
    Keep offsets in the original cleaned text, never in the normalized copy.
    """
    start = 0
    for line in text.split('\n'):
        normalized = ' '.join(line.split())
        if normalized:
            yield start, start + len(line), normalized
        start += len(line) + 1


def _matched_headings(headings, pattern, starts=frozenset()):
    """Match lines, allowing a bounded heading split by HTML block breaks.

    ``starts`` adds first words that may begin a split heading.
    """
    beginning = {'business', 'risk', "management's", 'management’s', 'management',
                 'segment', 'consolidated', 'note', 'research', 'r', 'r&',
                 'provision', 'income', 'taxation', 'leases', 'lease', 'commitments',
                 'contractual', 'property', 'property,'} | starts
    expression = re.compile(pattern)
    for index, (start, end, line) in enumerate(headings):
        if expression.fullmatch(line):
            yield start, end, line
            continue
        first = line.split(' ', 1)[0].lower()
        if len(line) > 256 or (first not in beginning and not first.startswith('item')):
            continue
        # SEC HTML may put each heading word on its own line. Limit joining to
        # a short heading, never an arbitrarily long paragraph or blank run.
        candidate = line
        for _, next_end, following in headings[index + 1:index + 16]:
            if len(candidate) + len(following) + 1 > 256:
                break
            candidate += '\n' + following
            if expression.fullmatch(candidate):
                yield start, next_end, candidate
                break


def release_statement_windows(text, headings=None):
    """(label, start, end) of a release's income statement, balance sheet and cash flow statement.

    The first heading of each statement is used; its window ends at the next
    statement heading of any kind (comprehensive income and equity included),
    within ``RELEASE_STATEMENT_CHARS``. A statement the release does not
    contain has no window.
    """
    headings = list(_heading_lines(text)) if headings is None else headings
    first = {}
    boundaries = {start for start, _, _ in _matched_headings(headings, RELEASE_STATEMENT_BOUNDARY, _STATEMENT_STARTS)}
    for label, pattern in RELEASE_STATEMENT_HEADINGS:
        matches = [start for start, _, _ in _matched_headings(headings, pattern, _STATEMENT_STARTS)]
        boundaries.update(matches)
        if matches:
            first[label] = matches[0]
    windows = []
    for label, _ in RELEASE_STATEMENT_HEADINGS:
        if label in first:
            start = first[label]
            end = min([len(text), start + RELEASE_STATEMENT_CHARS[label]] + [b for b in boundaries if b > start])
            windows.append((label, start, end))
    return windows


def select_excerpts(text, max_chars=MAX_EXCERPT_CHARS, opening_chars=1200, statements=False):
    """Exact text windows; selection is not a claim of complete filing coverage.

    ``opening_chars`` sizes the first window. An earnings release puts its
    results, quotes and guidance first: with the 1,200-character filing opening,
    Walmart's and Meta's releases kept 2,800 characters, all but the headline
    being a segment table.

    ``statements`` (earnings releases) keeps the release's income statement,
    balance sheet and cash flow statement whole after the opening, which ends
    where the first statement begins or where the statements would no longer
    fit. Without it, 8 of 15 releases collected on 2026-11-26 had neither an
    income statement nor a balance sheet window: their headings did not match
    the filing patterns and the statements sat after the 10,000-character opening.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Official document contains no readable text')
    if len(text) <= max_chars:
        return [{'excerpt_id': 'E1', 'label': 'complete cleaned document',
                 'start_char': 0, 'end_char': len(text), 'text': text}]
    headings = list(_heading_lines(text))
    statement_windows = release_statement_windows(text, headings) if statements else []
    opening_end = min(opening_chars, len(text))
    if statement_windows:
        opening_end = min([opening_end, max_chars - sum(end - start for _, start, end in statement_windows)] +
                          [start for _, start, _ in statement_windows])
    windows = [('document opening', 0, opening_end)] + statement_windows
    widths = {'business': 2400, 'risks': 1800, 'management discussion': 2400,
              'segment disclosure': 1600, 'operating results': 2200,
              'balance sheet': 2200, 'cash flow': 2200}
    for label, pattern in _EXCERPT_HEADINGS:
        if statement_windows and label in RELEASE_STATEMENT_CHARS:
            continue
        matches = [start for start, _, _ in _matched_headings(headings, pattern)]
        if matches:
            # Filings often repeat headings in the contents before the section.
            position = next((start for start in matches if start > 6000), matches[-1])
            start = max(0, position - 120)
            windows.append((label, start, min(len(text), start + widths[label])))
    # Long documents without conventional SEC headings still get disclosed,
    # deterministic coverage beyond the first navigation/contents paragraphs.
    if len(windows) == 1:
        for fraction in (.25, .5, .75):
            start = int(len(text) * fraction)
            windows.append(('document text sample', start, min(len(text), start + 7000)))
    excerpts, used = [], 0
    for label, start, end in windows:
        if used >= max_chars:
            break
        # Avoid repeating a section already included in a prior window.
        if any(start >= old['start_char'] and start < old['end_char'] for old in excerpts):
            continue
        end = min(end, start + max_chars - used)
        value = text[start:end]
        if value:
            excerpts.append({'excerpt_id': f'E{len(excerpts) + 1}', 'label': label,
                             'start_char': start, 'end_char': end, 'text': value})
            used += len(value)
    return excerpts


_FOCUS_HEADINGS = (
    ('research and development cost drivers', 1200, 2,
     r'(?im)^[ \t]*(?:research\s+(?:and|&)\s+development|r\s*&\s*d)(?:\s+(?:expenses?|costs?))?[ \t]*$'),
    ('income-tax provision and rate drivers', 1800, 1,
     r'(?im)^[ \t]*(?:provision\s+for\s+income\s+taxes|income\s+tax\s+expense)(?:\s+and\s+effective\s+tax\s+rate)?[ \t]*$'),
    ('income-tax note disclosures', 1800, 1,
     r'(?im)^[ \t]*(?:note\s+\d+[a-z]?\s*[\u2013\u2014\-:.]\s*)?(?:income\s+taxes|taxation)[ \t]*$'),
    # Margin, inventory, commitment and buyback disclosures carry the facts a
    # memo needs; without them a live AAPL memo quoted them from web excerpts.
    ('gross margin drivers', 1600, 1,
     r'(?im)^[ \t]*(?:(?:products?|services?)\s+(?:and\s+services\s+)?)?gross\s+margins?[ \t]*$'),
    ('inventories', 1000, 1, r'(?im)^[ \t]*(?:note\s+\d+[a-z]?\s*[\u2013\u2014\-:.]\s*)?inventor(?:y|ies)[ \t]*$'),
    ('purchase commitments', 1200, 1,
     r'(?im)^[ \t]*(?:(?:manufacturing|unconditional)\s+)?purchase\s+(?:obligations|commitments)[ \t]*$'),
    ('share repurchases', 1400, 1,
     r'(?im)^[ \t]*(?:(?:share|stock)\s+repurchases?(?:\s+programs?)?|issuer\s+purchases\s+of\s+equity\s+securities)[ \t]*$'),
    # Lease, commitment and fixed-asset notes. Two live memos (2026-11-15)
    # deducted Microsoft's $66.6 billion of finance leases while its datacenter
    # leases signed but not commenced reached the evidence only through an
    # unverified web excerpt.
    ('leases', 1600, 1,
     r'(?im)^[ \t]*(?:note\s+\d+[a-z]?\s*[–—\-:.]\s*)?leases?(?:\s+and\s+(?:contractual\s+)?commitments)?[ \t]*$'),
    ('commitments', 1200, 1,
     r'(?im)^[ \t]*(?:note\s+\d+[a-z]?\s*[–—\-:.]\s*)?(?:commitments(?:\s+and\s+contingencies)?|'
     r'contractual\s+obligations(?:\s+and\s+commitments)?)[ \t]*$'),
    ('property and equipment', 1000, 1,
     r'(?im)^[ \t]*(?:note\s+\d+[a-z]?\s*[–—\-:.]\s*)?property,?\s+(?:plant,?\s+)?and\s+equipment(?:,?\s+net)?[ \t]*$'),
)
# A lease note puts its not-yet-commenced amount after the maturity table, past
# a heading window (Microsoft: "had not yet commenced of $329.1 billion"), so
# that sentence is selected by its own wording.
_FOCUS_PHRASES = (
    ('leases signed but not yet commenced', 400, 600,
     r'(?i)\b(?:not\s+yet|have\s+not|had\s+not|has\s+not)\s+(?:yet\s+)?commenced\b'),
)
_FOCUS_LIMIT = 16000


def _has_prose_sentence(text):
    """Recognize eight consecutive prose words before sentence punctuation.

    Tokens and the gaps between them are scanned once. Do not use a repeated
    word-group regex followed by a broad tail: a punctuation-free paragraph
    makes that expression try overlapping partitions of the same words.
    Numbers/table separators break a word run; line breaks end a sentence.
    """
    for line in text.split('\n'):
        last_stop = max(line.rfind('.'), line.rfind('!'), line.rfind('?'))
        if last_stop < 0:
            continue
        count, previous_end = 0, 0
        for word in re.finditer(r"\b[A-Za-z][A-Za-z\u2019'\-]*", line):
            if word.end() > last_stop:
                break
            gap = line[previous_end:word.start()]
            count = count + 1 if all(char in ' ,;:' for char in gap) else 1
            if count >= 8:
                return True
            previous_end = word.end()
    return False


def archive_focus_excerpts(document):
    """Select exact R&D/tax windows from an already archived public document.

    Offsets are Unicode character indices in ``html_to_text(document)``; hashes
    use UTF-8 for both the decoded archive and cleaned text. At most
    ``_FOCUS_LIMIT`` text characters are returned. This is selected evidence, not full review of the
    archive, and no heading or missing disclosure is invented. Margin, inventory,
    purchase-commitment, repurchase, lease, commitment and property disclosures
    are selected the same way, plus the first sentence stating leases signed but
    not yet commenced. Substantive repeated headings rank above contents entries,
    including later MD&A text.
    """
    if not isinstance(document, str):
        raise ValueError('Document must be decoded text')
    if len(document.encode()) > MAX_DOCUMENT_BYTES:
        raise ValueError('Document exceeds the archive size limit')
    cleaned = html_to_text(document)
    if not cleaned:
        return []

    windows = []
    headings = list(_heading_lines(cleaned))
    for label, width, count, pattern in _FOCUS_HEADINGS:
        candidates = []
        for start, heading_end, heading in _matched_headings(headings, pattern):
            following = cleaned[heading_end:heading_end + width]
            # A contents row or table of figures alone is not a disclosure.
            # Require a prose sentence rather than rewarding a heading merely
            # because it occurs late in a long document.
            if not _has_prose_sentence(following):
                continue
            score = 0
            nearby = following[:800]
            if label.startswith('research'):
                score += 8 * bool(re.search(r'\b(?:r\s*&\s*d|research\s+(?:and|&)\s+development)\s+(?:expense|cost|spending)', nearby, re.I))
                score += 3 * bool(re.search(r'\b(?:primarily|due to|driven by|attributable|headcount)', nearby, re.I))
                score += bool(re.search(r'\b(?:increased?|decreased?|higher|lower|compared)\b', nearby, re.I))
            elif not label.startswith('income-tax'):
                score += 3 * bool(re.search(r'\d', nearby))
                score += 2 * bool(re.search(r'\b(?:primarily|due to|driven by|compared|percent|million|billion)\b', nearby, re.I))
                if label == 'leases':
                    # The note with lease costs and maturities, not an accounting-policy paragraph.
                    score += 3 * bool(re.search(r'\b(?:finance\s+leases?|lease\s+(?:cost|liabilit))', following, re.I))
            else:
                score += 3 * bool(re.search(r'\b(?:effective tax rate|one.time|tax charge|tax provision|deferred tax|valuation allowance)\b', following, re.I))
                score += 2 * bool(re.match(r'[ \t]*note\s+\d+', heading, re.I))
            # Earlier equally substantive matches are stable; no issuer or
            # fiscal date is preferred or inferred by this selector.
            candidates.append((score, start, min(len(cleaned), start + width)))
        selected = []
        for _, start, end in sorted(candidates, key=lambda item: (-item[0], item[1])):
            if any(start < old_end and end > old_start for old_start, old_end in selected):
                continue
            selected.append((start, end))
            windows.append((start, end, label))
            if len(selected) >= count:
                break
    for label, before, after, pattern in _FOCUS_PHRASES:
        # The first stated amount: a later repetition adds nothing.
        for match in re.finditer(pattern, cleaned):
            start, end = max(0, match.start() - before), min(len(cleaned), match.end() + after)
            if re.search(r'\$\s?\d', cleaned[match.start():end]) and _has_prose_sentence(cleaned[start:end]):
                windows.append((start, end, label))
                break

    # Merge overlap between neighboring R&D and tax discussions, preserving
    # both labels without spending the bounded packet on duplicate text.
    merged = []
    for start, end, label in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
            if label not in merged[-1][2]:
                merged[-1][2].append(label)
        else:
            merged.append([start, end, [label]])

    archive_hash = hashlib.sha256(document.encode()).hexdigest()
    cleaned_hash = hashlib.sha256(cleaned.encode()).hexdigest()
    excerpts, used = [], 0
    for start, end, labels in merged:
        end = min(end, start + _FOCUS_LIMIT - used)
        if end <= start:
            break
        value = cleaned[start:end]
        excerpts.append({'label': '; '.join(labels), 'start_char': start,
                         'end_char': end, 'text': value,
                         'text_sha256': hashlib.sha256(value.encode()).hexdigest(),
                         'cleaned_text_sha256': cleaned_hash,
                         'raw_decoded_text_sha256': archive_hash,
                         'offset_basis': 'html_to_text(document) Unicode character offsets'})
        used += len(value)
    return excerpts


def _source(title, url, document, *, kind, source_tier, metadata, max_chars=MAX_EXCERPT_CHARS, opening_chars=1200,
            statements=False):
    if not isinstance(document, str) or len(document.encode()) > MAX_DOCUMENT_BYTES:
        raise RuntimeError('Official source is missing or exceeds its archive size limit')
    text = html_to_text(document)
    if any(marker in text[:2000].lower() for marker in ('access denied', 'undeclared automated tool', 'request rate threshold exceeded')):
        raise RuntimeError('Official source returned an access-control page instead of the document')
    if len(text) < 200:
        raise RuntimeError('Official source has insufficient readable text')
    excerpts = select_excerpts(text, max_chars, opening_chars, statements)
    return {'title': title, 'url': url, 'kind': kind, 'source_tier': source_tier,
            'retrieved_at': datetime.now(timezone.utc).isoformat(),
            'raw_document': document,
            'data': {'metadata': metadata,
                     'raw_decoded_text_sha256': hashlib.sha256(document.encode()).hexdigest(),
                     'cleaned_text_sha256': hashlib.sha256(text.encode()).hexdigest(),
                     'cleaned_text_chars': len(text), 'excerpt_chars': sum(len(e['text']) for e in excerpts),
                     'excerpts': excerpts,
                     'coverage_note': ('Complete cleaned document.' if len(text) <= max_chars else
                                       'Selected exact text windows only; the full document is archived separately. '
                                       'Missing sections are not evidence that an issue is immaterial.')}}


def _filing_record(recent, index, form):
    """One validated submission-history row, or None."""
    try:
        record = {key: recent[key][index] for key in ('filingDate', 'reportDate', 'accessionNumber', 'primaryDocument')}
        if (not re.fullmatch(r'\d{10}-\d{2}-\d{6}', record['accessionNumber']) or
                not re.fullmatch(r'[A-Za-z0-9_.-]+', record['primaryDocument'])):
            return None
        for key in ('filingDate', 'reportDate'):
            datetime.strptime(record[key], '%Y-%m-%d')
    except (KeyError, IndexError, ValueError, TypeError):
        return None
    record['form'] = form
    return record


def _filing_records(submissions):
    """The latest annual report and the latest quarterly report after it.

    A 10-Q whose period ends on or before the latest annual report's is older
    evidence than that report and is skipped: Apple's June 2026 10-Q and
    Costco's May 2026 10-Q were selected beside 10-Ks for later fiscal years.
    """
    recent = submissions.get('filings', {}).get('recent', {})
    if not isinstance(recent, dict):
        raise ValueError('SEC submission history is malformed')
    selected = []
    for desired in ({'10-K', '20-F', '40-F'}, {'10-Q'}):
        for index, form in enumerate(recent.get('form', [])):
            if form not in desired:
                continue
            record = _filing_record(recent, index, form)
            if record is None:
                continue
            if form == '10-Q' and selected and record['reportDate'] <= selected[0]['reportDate']:
                break
            selected.append(record)
            break
    return selected


def _earnings_release_record(submissions):
    """The latest 8-K reporting results of operations (Item 2.02), or None."""
    recent = submissions.get('filings', {}).get('recent', {})
    items = recent.get('items') if isinstance(recent, dict) else None
    if not isinstance(items, list):
        return None
    for index, form in enumerate(recent.get('form', [])):
        if form != '8-K' or index >= len(items) or not isinstance(items[index], str):
            continue
        if '2.02' not in [item.strip() for item in items[index].split(',')]:
            continue
        record = _filing_record(recent, index, form)
        if record is not None:
            record['items'] = items[index]
            return record
    return None


class _FilingIndex(HTMLParser):
    """Rows of an EDGAR filing index: cell texts and the first link in each row."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = {'cells': [], 'href': None}
        elif tag in {'td', 'th'} and self.row is not None:
            self.cell = []
        elif tag == 'a' and self.row is not None and self.row['href'] is None:
            self.row['href'] = dict(attrs).get('href')

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {'td', 'th'} and self.row is not None and self.cell is not None:
            self.row['cells'].append(' '.join(''.join(self.cell).split()))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None


_EXHIBIT_DOCUMENT = re.compile(r'[A-Za-z0-9_.-]+\.(?:htm|html|txt)')


def _exhibit_kind(value):
    """'EX-99.1' for EX-99.1 or EX-99.01, 'EX-99.2' for EX-99.2, 'EX-99' for EX-99; None for anything else."""
    match = re.fullmatch(r'(?i)(?:ex|exhibit)[-\s]*(?:no\.?\s*)?99(?:\.0*(\d{1,2}))?', ' '.join(str(value).split()))
    if not match:
        return None
    return 'EX-99' + ('.' + match.group(1) if match.group(1) else '')


def _folder_document(href, cik, accession):
    """A document name inside the filing's own folder, or None for any other link."""
    href = (href or '').strip()
    if href.startswith('/ix?doc='):
        href = href[len('/ix?doc='):]
    if href.startswith('https://' + SEC_HOST + '/'):
        href = href[len('https://' + SEC_HOST):]
    folder = f'/Archives/edgar/data/{cik}/{accession}/'
    if href.startswith(folder):
        href = href[len(folder):]
    # A bare name is relative to the index page or 8-K, both in the filing folder.
    return href if _EXHIBIT_DOCUMENT.fullmatch(href) else None


def _index_exhibits(index_html, cik, accession):
    """[(exhibit type, document name)] of the exhibit-99 documents in a filing index table."""
    parser = _FilingIndex()
    parser.feed(index_html)
    found = []
    for row in parser.rows:
        kind = next((kind for kind in map(_exhibit_kind, row['cells']) if kind), None)
        name = _folder_document(row['href'], cik, accession)
        if kind and name and kind not in dict(found):
            found.append((kind, name))
    return found


def _header_exhibits(headers_html):
    """[(exhibit type, document name)] from a filing's index-headers page.

    The page reprints the submission header, one <TYPE>, <SEQUENCE> and
    <FILENAME> per document. Walmart's 2026-11-19 8-K index page rendered an
    empty document table while this page listed its EX-99.1.
    """
    text = html.unescape(headers_html)
    found = []
    for kind, name in re.findall(r'(?i)<TYPE>\s*([^\s<]{1,40})\s*<SEQUENCE>\s*\d{1,4}\s*<FILENAME>\s*([^\s<]{1,200})', text):
        kind = _exhibit_kind(kind)
        if kind and _EXHIBIT_DOCUMENT.fullmatch(name) and kind not in dict(found):
            found.append((kind, name))
    return found


def _primary_exhibits(document, cik, accession):
    """[(exhibit type, document name)] linked from an 8-K's own exhibit index (a row numbered 99.1)."""
    parser = _FilingIndex()
    parser.feed(document)
    found = []
    for row in parser.rows:
        number = next((cell for cell in row['cells'] if re.fullmatch(r'(?i)(?:exhibit\s+)?99(?:\.\d{1,2})?', cell)), None)
        kind = _exhibit_kind('EX-' + number.split()[-1]) if number else None
        name = _folder_document(row['href'], cik, accession)
        if kind and name and kind not in dict(found):
            found.append((kind, name))
    return found


def _release_choice(exhibits):
    """(exhibit type, document name) of the release: EX-99.1, else a lone EX-99; or None."""
    named = dict(exhibits)
    for kind in ('EX-99.1', 'EX-99'):
        if kind in named:
            return kind, named[kind]
    return None


def _release_exhibit(index_html, cik, accession):
    """(exhibit type, document name) of the earnings release in a filing index, or None.

    EX-99.1 is preferred; a filing whose only press-release exhibit is EX-99 or
    EX-99.01 is accepted. The link must stay inside the filing's own folder.
    """
    return _release_choice(_index_exhibits(index_html, cik, accession))


_SUCCESSOR = re.compile(r'(?i)\bsuccessor\s+(?:registrant|issuer)\b|\bRule\s+12g-3\b')


def _successor_statement(document):
    """The filing's own sentence naming it a successor registrant, bounded, or None.

    ExxonMobil Holdings (CIK 2115436) replaced Exxon Mobil Corporation as the
    registrant of XOM in 2026 and had filed only 10-Qs: no annual report exists
    under its CIK until its first 10-K.
    """
    # Filings break lines inside a sentence, so only a full stop bounds it.
    text = ' '.join(html_to_text(document).split())
    match = _SUCCESSOR.search(text)
    if not match:
        return None
    start = max(text.rfind('. ', max(0, match.start() - 300), match.start()) + 2, match.start() - 300, 0)
    end = text.find('. ', match.end())
    sentence = text[start:end + 1 if end >= 0 else len(text)].strip()
    return sentence[:400]


def gather_primary(ticker, fetch_json, fetch_text=None):
    """Return public primary source records and specific unresolved retrieval gaps.

    At most two SEC filings are retrieved, plus the exhibit-99.1 earnings release
    of the latest 8-K reporting results (Item 2.02), for every issuer, whether
    or not it is newer than the latest 10-Q; no fabricated per-company URLs are
    used. The SEC industry code (SIC) from the
    submission history is kept in each record's metadata.
    The caller assigns final source IDs, archives ``raw_document`` outside model
    context, and may request supplementary research for gaps reported here.
    """
    ticker = str(ticker).strip().upper()
    fetch_text = fetch_text or fetch_official_text
    sources, gaps = [], []
    try:
        directory, snapshot_used = load_directory(fetch_json)
        company = next(row for row in directory.values()
                       if row['ticker'].replace('-', '.') == ticker.replace('-', '.'))
        cik = int(company['cik_str'])
        submissions_url = f'https://data.sec.gov/submissions/CIK{cik:010d}.json'
        submissions = fetch_json(submissions_url)
        if not isinstance(submissions, dict):
            raise ValueError('SEC submission response is not an object')
        if int(submissions.get('cik', cik)) != cik:
            raise ValueError('SEC submission issuer does not match requested CIK')
        issuer = submissions.get('name') or company['title']
        # The industry code identifies banks and insurers before any paid stage
        # (inputs.operating_company_gate); JPMorgan reached a paid draft.
        industry = {key: submissions[key] for key, check in (('sic', r'\d{4}'), ('sicDescription', r'[^\x00-\x1f]{1,200}'))
                    if isinstance(submissions.get(key), str) and re.fullmatch(check, submissions[key])}
        records = _filing_records(submissions)
        if not records:
            gaps.append('No usable recent annual or quarterly SEC filing was identified.')
        elif not any(record['form'] != '10-Q' for record in records):
            gaps.append(f'The SEC filing history of {issuer} (CIK {cik}) holds no annual report (10-K, 20-F or 40-F); '
                        'annual figures were not reviewed.')
        for record in records:
            accession = record['accessionNumber'].replace('-', '')
            url = f'https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{record["primaryDocument"]}'
            try:
                # Low volume, sequential fetching stays well below SEC's 10/s ceiling.
                time.sleep(.15)
                document = fetch_text(url)
                metadata = dict(record, issuer=issuer, cik=cik, source_url=submissions_url,
                                ticker_mapping_snapshot_used=snapshot_used, **industry)
                if snapshot_used:
                    metadata['ticker_mapping_snapshot'] = snapshot_metadata()
                if not any(other['form'] != '10-Q' for other in records):
                    metadata['annual_report_on_file'] = False
                    statement = _successor_statement(document)
                    if statement:
                        metadata['successor_registrant_statement'] = statement
                        gaps.append(f'{issuer} states that it is a successor registrant ("{statement}"); the predecessor\'s '
                                    'annual reports are filed under a different CIK and were not collected.')
                sources.append(_source(f'SEC {record["form"]}: {metadata["issuer"]}; filed {record["filingDate"]}',
                                       url, document, kind='sec_filing', source_tier='regulatory_primary', metadata=metadata))
            except (RuntimeError, ValueError, OSError, TypeError):
                gaps.append(f'SEC {record["form"]} filed {record["filingDate"]} was identified but its document was unavailable; it was not reviewed.')
        release = _earnings_release_record(submissions)
        if release is None:
            gaps.append('No 8-K reporting results of operations (Item 2.02) is in the recent SEC filing history; '
                        'no issuer earnings release was reviewed.')
        else:
            try:
                release_sources, release_gaps = _earnings_release(release, cik, issuer, submissions_url, fetch_text, industry)
                sources.extend(release_sources)
                gaps.extend(release_gaps)
            except (RuntimeError, ValueError, OSError, TypeError) as error:
                gaps.append(f'The 8-K earnings release filed {release["filingDate"]} was identified but its exhibit was '
                            f'unavailable ({_reason(error)}); it was not reviewed.')
    except (RuntimeError, ValueError, KeyError, TypeError, StopIteration, OSError):
        gaps.append('SEC filing history could not be retrieved or the issuer was not mapped; annual and quarterly filings were not reviewed.')
    return sources, gaps


def _reason(error):
    """One bounded line naming why a retrieval failed."""
    return ' '.join(str(error).split())[:300] or type(error).__name__


def _release_location(record, cik, fetch_text):
    """(exhibit type, document name, how it was located, every exhibit-99 document) of an 8-K's release.

    The filing index table is read first, then the index-headers page, then
    the 8-K's own exhibit index. Walmart's 2026-11-19 8-K (59 documents) had an
    index page with an empty document table, so its release was reported
    unavailable and the run cited it only from unverified web excerpts. The
    error names what each page showed.
    """
    accession = record['accessionNumber'].replace('-', '')
    folder = f'https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/'
    pages = (('filing index', f'{folder}{record["accessionNumber"]}-index.htm',
              lambda page: _index_exhibits(page, cik, accession)),
             ('filing index headers', f'{folder}{record["accessionNumber"]}-index-headers.html', _header_exhibits),
             ('8-K exhibit index', folder + record['primaryDocument'],
              lambda page: _primary_exhibits(page, cik, accession)))
    findings = []
    for located_by, url, parse in pages:
        try:
            time.sleep(.15)
            exhibits = parse(fetch_text(url))
        except (RuntimeError, ValueError, OSError, TypeError) as error:
            findings.append(f'{located_by}: {_reason(error)}')
            continue
        choice = _release_choice(exhibits)
        if choice:
            return choice[0], choice[1], located_by, exhibits
        findings.append(f'{located_by}: no exhibit 99.1 or 99 document')
    raise RuntimeError('; '.join(findings))


def _statement_labels(document):
    """Labels of the statements a release document contains (see ``release_statement_windows``)."""
    return [label for label, _, _ in release_statement_windows(html_to_text(document))]


def _exhibit_number(kind):
    return int(kind.split('.', 1)[1]) if '.' in kind else 0


# Other exhibit-99 documents read when the release holds neither an income
# statement nor a balance sheet; Johnson & Johnson and AT&T file theirs as
# EX-99.2 (2026-10-13 and 2026-10-21).
RELEASE_STATEMENT_EXHIBITS = 2


def _earnings_release(record, cik, issuer, submissions_url, fetch_text, industry):
    """(issuer-primary sources, gaps) for the earnings release of one 8-K.

    A release is the issuer's own dated statement of results: issuer primary,
    not regulatory primary, and bounded like other primary excerpts. A live
    Microsoft run (2026-11-15) had its earnings release only as an unverified
    web excerpt. Its excerpts keep the income statement, balance sheet and cash
    flow statement whole. When the release holds neither of the first two, the
    first other exhibit-99 document of the same 8-K that does is a second
    source. Raises RuntimeError when the release itself is unavailable.
    """
    kind, name, located_by, exhibits = _release_location(record, cik, fetch_text)
    accession = record['accessionNumber'].replace('-', '')
    folder = f'https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/'
    index_url = f'{folder}{record["accessionNumber"]}-index.htm'
    metadata = dict(record, issuer=issuer, cik=cik, exhibit=kind, document=name, filing_index_url=index_url,
                    exhibit_located_by=located_by, source_url=submissions_url, published_at=record['filingDate'],
                    interpretation=RELEASE_INTERPRETATION, **industry)
    try:
        time.sleep(.15)
        document = fetch_text(folder + name)
        source = _source(f'{issuer} earnings release (8-K Item 2.02, {kind}); filed {record["filingDate"]}', folder + name,
                         document, kind='issuer_release', source_tier='issuer_primary',
                         metadata=dict(metadata, statements=_statement_labels(document)),
                         max_chars=MAX_EXCERPT_CHARS, opening_chars=RELEASE_OPENING_CHARS, statements=True)
    except (RuntimeError, ValueError, OSError, TypeError) as error:
        raise RuntimeError(f'{kind} {name}: {_reason(error)}') from None
    sources, gaps = [source], []
    metadata = source['data']['metadata']
    if {'operating results', 'balance sheet'} & set(metadata['statements']):
        return sources, gaps
    others = sorted((other for other in exhibits if other[0] not in {'EX-99', 'EX-99.1'}),
                    key=lambda other: _exhibit_number(other[0]))
    for other_kind, other_name in others[:RELEASE_STATEMENT_EXHIBITS]:
        try:
            time.sleep(.15)
            other = fetch_text(folder + other_name)
            statements = _statement_labels(other)
            if not {'operating results', 'balance sheet'} & set(statements):
                continue
            sources.append(_source(
                f'{issuer} earnings release financial statements (8-K Item 2.02, {other_kind}); filed {record["filingDate"]}',
                folder + other_name, other, kind='issuer_release', source_tier='issuer_primary',
                metadata=dict(metadata, exhibit=other_kind, document=other_name, statements=statements, release_exhibit=kind),
                max_chars=MAX_EXCERPT_CHARS, statements=True))
            break
        except (RuntimeError, ValueError, OSError, TypeError) as error:
            gaps.append(f'{other_kind} ({other_name}) of the 8-K earnings release filed {record["filingDate"]} was '
                        f'unavailable ({_reason(error)}); its statements were not reviewed.')
    return sources, gaps


# Selected concepts are not interchangeable: e.g. weighted-average shares are
# kept distinct from period-end and later cover-page shares outstanding.
_INLINE_CONCEPTS = {
    'FinanceLeaseLiabilityCurrent', 'FinanceLeaseLiabilityNoncurrent',
    'OperatingLeaseLiabilityCurrent', 'OperatingLeaseLiabilityNoncurrent',
    'FinanceLeaseLiability', 'OperatingLeaseLiability',
    'CashAndCashEquivalentsAtCarryingValue',
    'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents',
    'MarketableSecuritiesCurrent', 'MarketableSecuritiesNoncurrent',
    'ShortTermInvestments', 'LongTermInvestments',
    'LongTermDebtCurrent', 'LongTermDebtNoncurrent', 'LongTermDebt',
    'LongTermDebtAndFinanceLeaseObligationsCurrent',
    'LongTermDebtAndFinanceLeaseObligationsNoncurrent',
    'CommercialPaper', 'ShortTermBorrowings',
    'RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet',
    'OperatingIncomeLoss', 'NetIncomeLoss',
    'ShareBasedCompensation', 'StockBasedCompensation',
    'NetCashProvidedByUsedInOperatingActivities', 'PaymentsToAcquirePropertyPlantAndEquipment',
    'CommonStockSharesOutstanding', 'CommonStockSharesIssued',
    'EntityCommonStockSharesOutstanding',
    'WeightedAverageNumberOfDilutedSharesOutstanding', 'WeightedAverageNumberOfSharesOutstandingBasic',
}
_INLINE_CONCEPTS.update(concept.split(':', 1)[1] for concept in PRIMARY_INPUT_CONCEPTS | FINANCIAL_CONTEXT_CONCEPTS)


def _inline_number(token, attrs):
    """Apply a bounded, known inline-XBRL numeric transform, not display units."""
    from decimal import Decimal, InvalidOperation, localcontext
    import math

    nil = str(attrs.get('xsi:nil', attrs.get('nil', ''))).lower() in {'true', '1'}
    if nil:
        return None, None, 'nil'
    scale_text = attrs.get('scale', '0')
    if not isinstance(scale_text, str) or not re.fullmatch(r'-?\d{1,2}', scale_text):
        return None, None, 'invalid_scale'
    scale = int(scale_text)
    if not -15 <= scale <= 15 or len(token) > 100:
        return None, None, 'numeric_bound_exceeded'
    transform = attrs.get('format', '').split(':')[-1].lower()
    lexical = re.sub(r'\s+', '', token).strip()
    negative = attrs.get('sign') == '-'
    if lexical.startswith('(') and lexical.endswith(')'):
        negative, lexical = True, lexical[1:-1]
    if lexical.startswith('-') or lexical.startswith('\u2212'):
        negative, lexical = True, lexical[1:]
    elif lexical.startswith('+'):
        lexical = lexical[1:]
    if transform in {'fixed-zero', 'zerodash', 'numdash'}:
        # This is an explicit XBRL transform, not an assumption for an unknown dash.
        return 0, '0', 'explicit_zero_transform'
    dot_formats = {'', 'num-dot-decimal', 'numdotdecimal', 'num-space-dot', 'numspacedot'}
    comma_formats = {'num-comma-decimal', 'numcommadecimal', 'num-space-comma', 'numspacecomma'}
    if transform in dot_formats:
        if ',' in lexical and not re.fullmatch(r'\d{1,3}(?:,\d{3})+(?:\.\d+)?', lexical):
            return None, None, 'invalid_grouping'
        lexical = lexical.replace(',', '')
    elif transform in comma_formats:
        if '.' in lexical and not re.fullmatch(r'\d{1,3}(?:\.\d{3})+(?:,\d+)?', lexical):
            return None, None, 'invalid_grouping'
        lexical = lexical.replace('.', '').replace(',', '.')
    else:
        return None, None, 'unsupported_transform'
    if not re.fullmatch(r'(?:\d+(?:\.\d*)?|\.\d+)', lexical):
        return None, None, 'unparsed_token'
    try:
        with localcontext() as context:
            context.prec = 60
            value = Decimal(lexical) * (Decimal(10) ** scale)
            if negative:
                value = -abs(value)
            if not value.is_finite() or (value and not -300 <= value.adjusted() <= 30):
                return None, None, 'numeric_bound_exceeded'
            exact = format(value, 'f')
            number = int(value) if value == value.to_integral_value() else float(value)
            if isinstance(number, float) and not math.isfinite(number):
                return None, None, 'numeric_bound_exceeded'
            return number, exact, 'parsed'
    except (InvalidOperation, ValueError, OverflowError):
        return None, None, 'unparsed_token'


class _InlineFactParser(HTMLParser):
    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.document = document
        self.line_starts = [0] + [match.end() for match in re.finditer('\n', document)]
        self.namespaces, self.contexts, self.units = {}, {}, {}
        self.context, self.unit = None, None
        self.captures, self.fractions, self.facts = [], [], []
        self.unit_container, self.exclude_depth = [], 0

    def char_offset(self):
        line, column = self.getpos()
        return self.line_starts[line - 1] + column

    def _selected(self, name):
        if not isinstance(name, str) or ':' not in name:
            return False
        prefix, local = name.split(':', 1)
        if local not in _INLINE_CONCEPTS:
            return False
        namespace = self.namespaces.get(prefix.lower(), '')
        if namespace:
            parsed = urlparse(namespace)
            return ((parsed.hostname in {'fasb.org', 'xbrl.fasb.org'} and parsed.path.startswith('/us-gaap/')) or
                    (parsed.hostname == 'xbrl.sec.gov' and parsed.path.startswith('/dei/')))
        return prefix.lower() in {'us-gaap', 'dei'}

    def _inline_tag(self, tag, local):
        if ':' not in tag or tag.split(':')[-1] != local:
            return False
        prefix = tag.split(':', 1)[0]
        namespace = self.namespaces.get(prefix, '')
        if namespace:
            return namespace.rstrip('/') in {
                'http://www.xbrl.org/2013/inlineXBRL', 'http://www.xbrl.org/2008/inlineXBRL',
                'https://www.xbrl.org/2013/inlineXBRL', 'https://www.xbrl.org/2008/inlineXBRL'}
        return prefix == 'ix'

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for name, value in attrs.items():
            if name.startswith('xmlns:') and isinstance(value, str):
                self.namespaces[name[6:]] = value
        local = tag.split(':')[-1]
        if local == 'context':
            self.context = {'id': attrs.get('id'), 'period': {}, 'has_dimensions': False,
                            'entity_identifier': None, 'members': {}}
        elif local == 'unit':
            self.unit = {'id': attrs.get('id'), 'simple': [], 'numerator': [], 'denominator': []}
        elif self.context is not None and local in {'explicitmember', 'typedmember'}:
            self.context['has_dimensions'] = True
            if local == 'explicitmember' and isinstance(attrs.get('dimension'), str):
                self.captures.append({'tag': tag, 'kind': 'member', 'dimension': attrs['dimension'],
                                      'target': self.context, 'parts': []})
            elif local == 'typedmember':
                self.context['members'][str(attrs.get('dimension'))] = None
        if self.context is not None and local in {'instant', 'startdate', 'enddate', 'identifier'}:
            self.captures.append({'tag': tag, 'kind': local, 'target': self.context, 'parts': []})
        if self.unit is not None and local in {'unitnumerator', 'unitdenominator'}:
            self.unit_container.append(local)
        if self.unit is not None and local == 'measure':
            area = self.unit_container[-1] if self.unit_container else None
            area = {'unitnumerator': 'numerator', 'unitdenominator': 'denominator'}.get(area, 'simple')
            self.captures.append({'tag': tag, 'kind': 'measure', 'area': area, 'target': self.unit, 'parts': []})
        if self._inline_tag(tag, 'exclude'):
            self.exclude_depth += 1
        if self._inline_tag(tag, 'nonfraction'):
            if self._selected(attrs.get('name')):
                line, column = self.getpos()
                frame = {'attrs': attrs, 'parts': [], 'start_char': self.char_offset(), 'line': line, 'column': column}
            else:
                frame = None
            self.fractions.append(frame)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        for capture in self.captures:
            capture['parts'].append(data)
        if not self.exclude_depth:
            for fact in self.fractions:
                if fact is not None:
                    fact['parts'].append(data)

    def handle_endtag(self, tag):
        local = tag.split(':')[-1]
        if self.captures and self.captures[-1]['tag'] == tag:
            capture = self.captures.pop()
            value = ''.join(capture['parts']).strip()
            target, kind = capture['target'], capture['kind']
            if kind == 'measure':
                target[capture['area']].append(value)
            elif kind == 'member':
                target['members'][capture['dimension']] = value
            elif kind == 'identifier':
                target['entity_identifier'] = value
            else:
                target['period'][{'instant': 'instant', 'startdate': 'start_date', 'enddate': 'end_date'}[kind]] = value
        if local == 'context' and self.context is not None:
            self.contexts[self.context['id']] = self.context
            self.context = None
        elif local == 'unit' and self.unit is not None:
            self.units[self.unit['id']] = self.unit
            self.unit = None
        elif local in {'unitnumerator', 'unitdenominator'} and self.unit_container:
            self.unit_container.pop()
        if self._inline_tag(tag, 'nonfraction') and self.fractions:
            fact = self.fractions.pop()
            if fact is not None:
                end = self.document.find('>', self.char_offset())
                fact['end_char'] = end + 1 if end >= 0 else self.char_offset()
                self.facts.append(fact)
        if self._inline_tag(tag, 'exclude') and self.exclude_depth:
            self.exclude_depth -= 1


# Axes whose members break a reported line into products, segments or regions.
# Facts on any other axis (fair-value levels, equity components, award types)
# are not used for these tables.
BREAKDOWN_AXES = {'srt:ProductOrServiceAxis': 'product_or_service',
                  'us-gaap:StatementBusinessSegmentsAxis': 'segment',
                  'srt:StatementGeographicalAxis': 'geography'}
# A second axis that only says the member is an operating segment does not
# change what is measured.
_NEUTRAL_MEMBERS = {('srt:ConsolidationItemsAxis', 'us-gaap:OperatingSegmentsMember')}


SEGMENT_AXIS = 'us-gaap:StatementBusinessSegmentsAxis'


def inline_breakdown_facts(document):
    """Reported facts broken down by product/service, segment or geography.

    Uses the same parser, concepts and number rules as ``inline_facts``. A fact
    qualifies when its context has exactly one breakdown axis (plus at most a
    neutral operating-segment qualifier) and a parsed numeric value, or when it
    has two breakdown axes of which one is the segment axis. Alphabet, Meta,
    Walmart and Home Depot report product revenue only within a segment; such a
    fact is filed under its product or geography axis with the member keyed
    "<member> within <segment member>", since one product name can recur in
    two segments (Walmart's grocery in Walmart U.S. and in Sam's Club). Nothing
    is summed or inferred here.
    """
    from datetime import date
    if not isinstance(document, str):
        raise ValueError('Inline XBRL document must be decoded text')
    if len(document.encode()) > MAX_DOCUMENT_BYTES:
        raise ValueError('Inline XBRL document exceeds the 3 MB source limit')
    parser = _InlineFactParser(document)
    parser.feed(document)
    result, seen = [], set()
    for frame in parser.facts:
        attrs = frame['attrs']
        context = parser.contexts.get(attrs.get('contextref'))
        unit = parser.units.get(attrs.get('unitref'))
        if context is None or unit is None or not context['has_dimensions']:
            continue
        members = {axis: member for axis, member in context.get('members', {}).items()
                   if (axis, member) not in _NEUTRAL_MEMBERS}
        if (len(members) == 2 and SEGMENT_AXIS in members and all(axis in BREAKDOWN_AXES for axis in members) and
                all(isinstance(member, str) and member for member in members.values())):
            axis = next(axis for axis in members if axis != SEGMENT_AXIS)
            member = members[axis] + ' within ' + members[SEGMENT_AXIS]
        elif len(members) == 1:
            (axis, member), = members.items()
        else:
            continue
        if axis not in BREAKDOWN_AXES or not isinstance(member, str) or not member:
            continue
        period = context['period']
        try:
            start, end = date.fromisoformat(period.get('start_date', '')), date.fromisoformat(period.get('end_date', ''))
        except ValueError:
            continue
        if start > end:
            continue
        token = ''.join(frame['parts']).strip()
        value, exact, status = _inline_number(token, attrs)
        if status != 'parsed' or value is None:
            continue
        measures = unit['simple'] if len(unit['simple']) == 1 and not unit['numerator'] else []
        if len(measures) != 1 or not re.fullmatch(r'iso4217:[A-Z]{3}', measures[0]):
            continue
        key = (attrs['name'], axis, member, period.get('start_date'), period.get('end_date'), exact)
        if key in seen:
            continue
        seen.add(key)
        result.append({'concept': attrs['name'], 'axis': BREAKDOWN_AXES[axis], 'axis_concept': axis,
                       'member': member, 'start_date': period['start_date'], 'end_date': period['end_date'],
                       'unit': measures[0], 'normalized_value': value, 'normalized_decimal': exact,
                       'decimals': attrs.get('decimals'), 'context_ref': attrs.get('contextref')})
    return result


CLASS_OF_STOCK_AXIS = 'us-gaap:StatementClassOfStockAxis'


def _class_cover_shares(name, context):
    """A cover-page outstanding count for one share class (Meta reports no entity total)."""
    members = context.get('members', {})
    return (isinstance(name, str) and name.split(':', 1)[-1] == 'EntityCommonStockSharesOutstanding' and
            list(members) == [CLASS_OF_STOCK_AXIS] and isinstance(members[CLASS_OF_STOCK_AXIS], str) and
            bool(members[CLASS_OF_STOCK_AXIS]))


# Two plan types, a handful of balance concepts and two or three dates.
MAX_PLAN_TYPE_FACTS = 60


def _plan_type_benefit(name, context):
    """A pension or retiree-health balance on the plan-type axis (Coca-Cola tags these only by plan type)."""
    members = context.get('members', {})
    return (isinstance(name, str) and name.split(':', 1)[-1] in PLAN_TYPE_CONCEPTS and
            list(members) == [PLAN_TYPE_AXIS] and members[PLAN_TYPE_AXIS] in PLAN_TYPE_MEMBERS.values())


def inline_facts(document):
    """Return at most 100 recent selected inline-XBRL facts, plus defined-benefit balances by plan type.

    Values retain exact concepts, contexts, dates, units, transformations and raw
    locations; no concepts are aggregated and missing or nil values never become
    zero. Dimensional contexts are excluded, except cover-page outstanding
    shares by share class, which keep their dimension and are marked
    ``class_of_stock_cover_shares``, and pension and other postretirement
    balances on the plan-type axis, marked ``defined_benefit_plan_type`` and
    kept outside the 100-fact limit. A numeric zero is emitted
    only for a stated zero or an explicit XBRL zero transform. Offsets/columns are
    Unicode-character positions in the original decoded HTML, with 1-based lines.
    """
    from datetime import date
    if not isinstance(document, str):
        raise ValueError('Inline XBRL document must be decoded text')
    if len(document.encode()) > MAX_DOCUMENT_BYTES:
        raise ValueError('Inline XBRL document exceeds the 3 MB source limit')
    parser = _InlineFactParser(document)
    parser.feed(document)
    result, plans, seen = [], [], set()
    for frame in parser.facts:
        attrs = frame['attrs']
        context = parser.contexts.get(attrs.get('contextref'))
        unit = parser.units.get(attrs.get('unitref'))
        if context is None or unit is None:
            continue
        by_class = context['has_dimensions'] and _class_cover_shares(attrs.get('name'), context)
        by_plan = context['has_dimensions'] and not by_class and _plan_type_benefit(attrs.get('name'), context)
        if context['has_dimensions'] and not by_class and not by_plan:
            continue
        period = context['period']
        try:
            if period.get('instant'):
                date.fromisoformat(period['instant'])
            elif period.get('start_date') and period.get('end_date'):
                if date.fromisoformat(period['start_date']) > date.fromisoformat(period['end_date']):
                    continue
            else:
                continue
        except ValueError:
            continue
        token = ''.join(frame['parts']).strip()
        value, exact, status = _inline_number(token, attrs)
        is_nil = status == 'nil'
        unit_value = (unit['simple'][0] if len(unit['simple']) == 1 and not unit['numerator'] and not unit['denominator']
                      else {'measures': unit['simple'], 'numerator': unit['numerator'], 'denominator': unit['denominator']})
        unique = (attrs['name'], attrs.get('contextref'), attrs.get('unitref'), exact, status)
        if unique in seen:
            continue
        seen.add(unique)
        scale_raw = attrs.get('scale', '0')
        scale = int(scale_raw) if isinstance(scale_raw, str) and re.fullmatch(r'-?\d{1,2}', scale_raw) else None
        (plans if by_plan else result).append({'concept': attrs['name'], 'context_ref': attrs.get('contextref'),
                       'instant': period.get('instant'), 'start_date': period.get('start_date'),
                       'end_date': period.get('end_date'), 'entity_identifier': context['entity_identifier'],
                       'unit_ref': attrs.get('unitref'), 'unit': unit_value,
                       'scale': scale, 'sign': attrs.get('sign'), 'decimals': attrs.get('decimals'),
                       'format': attrs.get('format'), 'normalized_value': value, 'normalized_decimal': exact,
                       'is_nil': is_nil, 'parse_status': status, 'raw_token': token,
                       'dimensions': dict(context['members']) if by_class or by_plan else {},
                       'dimension_filter': ('class_of_stock_cover_shares' if by_class else
                                            'defined_benefit_plan_type' if by_plan else 'nondimensional_only'),
                       'location': {'fact_id': attrs.get('id'), 'start_char': frame['start_char'],
                                    'end_char': frame['end_char'], 'line': frame['line'], 'column': frame['column'],
                                    'raw_html': document[frame['start_char']:frame['end_char']]}})
    result.sort(key=lambda fact: (fact['instant'] or fact['end_date'] or '',
                                  fact['start_date'] or '', fact['concept'], fact['context_ref']), reverse=True)
    plans.sort(key=lambda fact: (fact['instant'] or '', fact['concept'], fact['context_ref']), reverse=True)
    return result[:100] + plans[:MAX_PLAN_TYPE_FACTS]

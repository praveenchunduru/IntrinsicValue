"""Keyless primary documents with bounded excerpts and reproducible archives.

Source text is untrusted evidence, never instructions. ``raw_document`` is a
public source archive for the caller to save, **not** part of the model prompt.
The data/excerpts fields are bounded and identify offsets in the full cleaned
text. Failure to retrieve a filing is a disclosed research gap.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request

from .sec_directory import load_directory, snapshot_metadata
from .sec_http import MAX_SOURCE_BYTES, open_http as urlopen, user_agent
from .inputs import PRIMARY_INPUT_CONCEPTS
from .companyfacts import FINANCIAL_CONTEXT_CONCEPTS

MAX_DOCUMENT_BYTES = MAX_SOURCE_BYTES
MAX_EXCERPT_CHARS = 16_000
SEC_HOST = 'www.sec.gov'
ALLOWED_TEXT_HOSTS = {SEC_HOST}
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


def _matched_headings(headings, pattern):
    """Match lines, allowing a bounded heading split by HTML block breaks."""
    beginning = {'business', 'risk', "management's", 'management’s', 'management',
                 'segment', 'consolidated', 'note', 'research', 'r', 'r&',
                 'provision', 'income', 'taxation', 'leases', 'lease', 'commitments',
                 'contractual', 'property', 'property,'}
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


def select_excerpts(text, max_chars=MAX_EXCERPT_CHARS):
    """Exact text windows; selection is not a claim of complete filing coverage."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Official document contains no readable text')
    if len(text) <= max_chars:
        return [{'excerpt_id': 'E1', 'label': 'complete cleaned document',
                 'start_char': 0, 'end_char': len(text), 'text': text}]
    windows = [('document opening', 0, min(1200, len(text)))]
    widths = {'business': 2400, 'risks': 1800, 'management discussion': 2400,
              'segment disclosure': 1600, 'operating results': 2200,
              'balance sheet': 2200, 'cash flow': 2200}
    headings = list(_heading_lines(text))
    for label, pattern in _EXCERPT_HEADINGS:
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


def _source(title, url, document, *, kind, source_tier, metadata, max_chars=MAX_EXCERPT_CHARS):
    if not isinstance(document, str) or len(document.encode()) > MAX_DOCUMENT_BYTES:
        raise RuntimeError('Official source is missing or exceeds its archive size limit')
    text = html_to_text(document)
    if any(marker in text[:2000].lower() for marker in ('access denied', 'undeclared automated tool', 'request rate threshold exceeded')):
        raise RuntimeError('Official source returned an access-control page, not a reviewed document')
    if len(text) < 200:
        raise RuntimeError('Official source has insufficient readable text')
    excerpts = select_excerpts(text, max_chars)
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


def _release_exhibit(index_html, cik, accession):
    """(exhibit type, document name) of the earnings release in a filing index, or None.

    EX-99.1 is preferred; a filing whose only press-release exhibit is EX-99 or
    EX-99.01 is accepted. The link must stay inside the filing's own folder.
    """
    parser = _FilingIndex()
    parser.feed(index_html)
    folder = f'/Archives/edgar/data/{cik}/{accession}/'
    found = {}
    for row in parser.rows:
        kind = next((cell.upper() for cell in row['cells'] if re.fullmatch(r'EX-99(?:\.0?1)?', cell, re.I)), None)
        href = row['href'] or ''
        if href.startswith('/ix?doc='):
            href = href[len('/ix?doc='):]
        name = href[len(folder):] if href.startswith(folder) else ''
        if kind and re.fullmatch(r'[A-Za-z0-9_.-]+\.(?:htm|html|txt)', name):
            found.setdefault(kind.replace('EX-99.01', 'EX-99.1'), name)
    for kind in ('EX-99.1', 'EX-99'):
        if kind in found:
            return kind, found[kind]
    return None


_SUCCESSOR = re.compile(r'(?i)\bsuccessor\s+(?:registrant|issuer)\b|\bRule\s+12g-3\b')


def _successor_statement(document):
    """The filing's own sentence naming it a successor registrant, bounded, or None.

    ExxonMobil Holdings (CIK 2115436) replaced Exxon Mobil Corporation as the
    registrant of XOM in 2026 and had filed only 10-Qs: no annual report exists
    under its CIK until its first 10-K.
    """
    text = html_to_text(document)
    match = _SUCCESSOR.search(text)
    if not match:
        return None
    start = max(text.rfind('.', 0, match.start()), text.rfind('\n', 0, match.start())) + 1
    end = text.find('.', match.end())
    sentence = ' '.join(text[start:end + 1 if end >= 0 else len(text)].split())
    return sentence[:400]


def gather_primary(ticker, fetch_json, fetch_text=None):
    """Return public primary source records and specific unresolved retrieval gaps.

    At most two SEC filings are retrieved, plus the exhibit-99.1 earnings release
    of the latest 8-K reporting results (Item 2.02), for every issuer; no
    fabricated per-company URLs are used. The SEC industry code (SIC) from the
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
            sources_before = len(sources)
            try:
                sources.extend(_earnings_release(release, cik, issuer, submissions_url, fetch_text, industry))
            except (RuntimeError, ValueError, OSError, TypeError):
                pass
            if len(sources) == sources_before:
                gaps.append(f'The 8-K earnings release filed {release["filingDate"]} was identified but its exhibit was '
                            'unavailable; it was not reviewed.')
    except (RuntimeError, ValueError, KeyError, TypeError, StopIteration, OSError):
        gaps.append('SEC filing history could not be retrieved or the issuer was not mapped; annual and quarterly filings were not reviewed.')
    return sources, gaps


def _earnings_release(record, cik, issuer, submissions_url, fetch_text, industry):
    """The earnings-release exhibit of one 8-K as an issuer-primary source (a list of at most one).

    A release is the issuer's own dated statement of results: issuer primary,
    not regulatory primary, and bounded like other primary excerpts. A live
    Microsoft run (2026-11-15) had its earnings release only as an unverified
    web excerpt.
    """
    accession = record['accessionNumber'].replace('-', '')
    index_url = f'https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{record["accessionNumber"]}-index.htm'
    time.sleep(.15)
    exhibit = _release_exhibit(fetch_text(index_url), cik, accession)
    if exhibit is None:
        return []
    kind, name = exhibit
    url = f'https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{name}'
    time.sleep(.15)
    document = fetch_text(url)
    metadata = dict(record, issuer=issuer, cik=cik, exhibit=kind, document=name, filing_index_url=index_url,
                    source_url=submissions_url, published_at=record['filingDate'],
                    interpretation=RELEASE_INTERPRETATION, **industry)
    return [_source(f'{issuer} earnings release (8-K Item 2.02, {kind}); filed {record["filingDate"]}', url, document,
                    kind='issuer_release', source_tier='issuer_primary', metadata=metadata, max_chars=MAX_EXCERPT_CHARS)]


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


def inline_facts(document):
    """Return at most 100 recent selected inline-XBRL facts.

    Values retain exact concepts, contexts, dates, units, transformations and raw
    locations; no concepts are aggregated and missing or nil values never become
    zero. Dimensional contexts are excluded, except cover-page outstanding
    shares by share class, which keep their dimension and are marked
    ``class_of_stock_cover_shares``. A numeric zero is emitted
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
    result, seen = [], set()
    for frame in parser.facts:
        attrs = frame['attrs']
        context = parser.contexts.get(attrs.get('contextref'))
        unit = parser.units.get(attrs.get('unitref'))
        if context is None or unit is None:
            continue
        by_class = context['has_dimensions'] and _class_cover_shares(attrs.get('name'), context)
        if context['has_dimensions'] and not by_class:
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
        result.append({'concept': attrs['name'], 'context_ref': attrs.get('contextref'),
                       'instant': period.get('instant'), 'start_date': period.get('start_date'),
                       'end_date': period.get('end_date'), 'entity_identifier': context['entity_identifier'],
                       'unit_ref': attrs.get('unitref'), 'unit': unit_value,
                       'scale': scale, 'sign': attrs.get('sign'), 'decimals': attrs.get('decimals'),
                       'format': attrs.get('format'), 'normalized_value': value, 'normalized_decimal': exact,
                       'is_nil': is_nil, 'parse_status': status, 'raw_token': token,
                       'dimensions': dict(context['members']) if by_class else {},
                       'dimension_filter': 'class_of_stock_cover_shares' if by_class else 'nondimensional_only',
                       'location': {'fact_id': attrs.get('id'), 'start_char': frame['start_char'],
                                    'end_char': frame['end_char'], 'line': frame['line'], 'column': frame['column'],
                                    'raw_html': document[frame['start_char']:frame['end_char']]}})
    result.sort(key=lambda fact: (fact['instant'] or fact['end_date'] or '',
                                  fact['start_date'] or '', fact['concept'], fact['context_ref']), reverse=True)
    return result[:100]

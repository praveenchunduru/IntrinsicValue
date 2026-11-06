"""Conservative same-filing classification of a labeled primary debt total.

LongTermDebt is not intrinsically all financial debt. A labeled debt-note total
must align with the exact inline fact and dated table column, and the note must
positively establish whole-debt scope through disjoint categories or an explicit
dated no-short-term-financing statement. Missing short-term debt tags never
participate in the decision and never become reported zeros.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import hashlib
from html.parser import HTMLParser
import re
from .sec_http import MAX_SOURCE_BYTES

MAX_DOCUMENT_BYTES = MAX_SOURCE_BYTES
MAX_TABLE_CHARS = 100_000
MAX_EXCERPT_CHARS = 12_000
_NOTE = re.compile(r'Note\s+\d+[a-z]?\s*[\u2013\u2014.:-]\s*(?:Debt|Borrowings)', re.I)
_TOTAL = re.compile(r'Total\s+(?:financial\s+)?debt', re.I)
_NARROW = re.compile(
    r'\b(?:components|composition|summary|details|breakdown)\s+of\s+(?:our\s+)?long[- ]term\s+debt\b'
    r'|\blong[- ]term\s+debt\s+(?:consists|consisted|comprises|comprised)\b', re.I)


class _Tables(HTMLParser):
    """Record bounded table cells/columns and actual inline-tag locations."""
    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.document = document
        self.lines = [0] + [match.end() for match in re.finditer('\n', document)]
        self.stack, self.tables = [], []
        self.heading, self.last_heading = None, None
        self.hidden = []

    def char_offset(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'script', 'style', 'noscript', 'head', 'ix:header'}:
            self.hidden.append(tag)
        if self.hidden:
            return
        if tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p'}:
            self.heading = {'tag': tag, 'start': self.char_offset(), 'parts': []}
        if tag == 'table':
            if self.stack:
                self.stack[-1]['invalid'] = True
            self.stack.append({'start': self.char_offset(), 'heading': self.last_heading,
                               'rows': [], 'row': None, 'cell': None, 'invalid': False})
        if not self.stack:
            return
        table = self.stack[-1]
        if tag == 'tr':
            if table['row'] is not None:
                table['invalid'] = True
            table['row'] = []
            table['column'] = 0
        elif tag in {'td', 'th'} and table['row'] is not None:
            if table['cell'] is not None:
                table['invalid'] = True
            span = attrs.get('colspan', '1')
            if (not isinstance(span, str) or not re.fullmatch(r'[1-9][0-9]{0,2}', span) or int(span) > 128 or
                    attrs.get('rowspan', '1') != '1'):
                table['invalid'] = True
                span = '1'
            table['cell'] = {'column': table['column'], 'span': int(span),
                             'parts': [], 'facts': []}
            table['column'] += int(span)
            if table['column'] > 256:
                table['invalid'] = True
        elif tag.split(':')[-1] == 'nonfraction' and table['cell'] is not None:
            table['cell']['facts'].append({'start': self.char_offset(), 'attrs': attrs})
        elif tag == 'br' and table['cell'] is not None:
            table['cell']['parts'].append(' ')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self.hidden:
            return
        if self.heading is not None:
            self.heading['parts'].append(data)
        if self.stack and self.stack[-1]['cell'] is not None:
            self.stack[-1]['cell']['parts'].append(data)

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
            return
        if self.heading is not None and tag == self.heading['tag']:
            text = ' '.join(''.join(self.heading['parts']).split())
            # Word-exported SEC filings often use a bold paragraph as a note
            # heading. Ordinary explanatory paragraphs keep the note scope.
            if self.heading['tag'] != 'p' or re.match(r'Note\s+\d', text, re.I):
                self.last_heading = {'start': self.heading['start'], 'text': text}
            self.heading = None
        if not self.stack:
            return
        table = self.stack[-1]
        if tag in {'td', 'th'} and table['cell'] is not None:
            cell = table['cell']
            cell['text'] = ' '.join(''.join(cell.pop('parts')).split())
            table['row'].append(cell)
            table['cell'] = None
        elif tag == 'tr' and table['row'] is not None:
            if table['cell'] is not None or len(table['rows']) >= 512:
                table['invalid'] = True
            else:
                table['rows'].append(table['row'])
            table['row'] = None
        elif tag == 'table':
            end = self.document.find('>', self.char_offset())
            table['end'] = end + 1 if end >= 0 else self.char_offset()
            self.stack.pop()
            if table['row'] is not None or table['cell'] is not None:
                table['invalid'] = True
            self.tables.append(table)


def _column_date(text):
    text = re.sub(r'^as\s+of\s+', '', text, flags=re.I)
    if re.fullmatch(r'(?:19|20)\d{2}', text):
        return text
    for pattern in ('%B %d, %Y', '%b %d, %Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            pass
    return None


def _fact_key(fact):
    if not isinstance(fact, dict):
        return None
    location = fact.get('location', {})
    if not isinstance(location, dict):
        return None
    if (any(not isinstance(fact.get(key), str) for key in (
            'concept', 'context_ref', 'instant', 'normalized_decimal', 'unit', 'parse_status')) or
            not all(isinstance(location.get(key), int) for key in ('start_char', 'end_char')) or
            not isinstance(location.get('raw_html'), str)):
        return None
    return tuple(fact.get(key) for key in ('concept', 'context_ref', 'instant',
                 'normalized_decimal', 'unit', 'parse_status')) + (
                     location.get('start_char'), location.get('end_char'), location.get('raw_html'))


def _scope_evidence(preface, rows, column, fact):
    """Require positive whole-debt scope, beyond a generic Total debt label."""
    instant = fact['instant']
    for line in preface.split('\n'):
        dated = bool(re.search(r'(?<![0-9])' + re.escape(instant) + r'(?![0-9])', line))
        for match in re.finditer(r'\b[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4}\b', line):
            dated = dated or _column_date(match.group()) == instant
        if (dated and re.search(r'\bno\s+short[- ]term\s+(?:borrowings|debt|financing)\b', line, re.I) and
                re.search(r'\boutstanding\b|\bthere\s+(?:was|were|is|are)\s+no\b|\bhad\s+no\b', line, re.I) and
                not re.search(r'\b(?:except|excluding|apart from|other than|however|but)\b', line, re.I)):
            return 'dated_no_short_term_financing'
    # A table may instead show disjoint short-term and long-term debt
    # categories in the dated column. Verify the displayed arithmetic using
    # the total's own inline scale/transform, never a guessed display unit.
    from .research import _inline_number
    amounts = {}
    labels = {'short': r'short[- ]term\s+(?:debt|borrowings)',
              'long': r'long[- ]term\s+debt(?:,?\s+including\s+current\s+maturities)?'}
    for name, pattern in labels.items():
        matches = [row for row in rows if any(re.fullmatch(pattern, cell['text'], re.I) for cell in row)]
        if len(matches) != 1:
            return None
        cells = [cell for cell in matches[0] if cell['column'] == column and cell['span'] == 1]
        if len(cells) != 1:
            return None
        token = re.sub(r'[$\u20ac\u00a3\u00a5\s]', '', cells[0]['text'])
        _, exact, status = _inline_number(token, {'scale': str(fact['scale']), 'format': fact.get('format') or ''})
        if status != 'parsed' or Decimal(exact) < 0:
            return None
        amounts[name] = Decimal(exact)
    return ('disjoint_debt_categories' if sum(amounts.values()) == Decimal(fact['normalized_decimal']) else None)


def classify_primary_debt(document, facts, *, anchor=None):
    """Return classifications and exact source excerpts, or empty collections.

    ``facts`` must come from this decoded filing. Reparse it independently to
    verify contexts, dimensionality, units, transforms and exact fact locations;
    an arbitrary supplied fact or a similar printed number is insufficient.
    ``anchor`` may select an exact balance-sheet date. Otherwise the latest
    qualified date is returned and the input adapter still checks its anchor.
    Callers must attach the resulting excerpt to the same primary source.
    """
    if not isinstance(document, str) or len(document.encode()) > MAX_DOCUMENT_BYTES:
        raise ValueError('Primary debt classification requires decoded HTML within 3 MB')
    if not isinstance(facts, list):
        raise ValueError('Primary debt classification requires a same-filing fact list')
    if anchor is not None:
        try:
            anchor = date.fromisoformat(anchor).isoformat()
        except (TypeError, ValueError):
            raise ValueError('Primary debt anchor must be an ISO balance-sheet date') from None
    empty = {'classifications': {}, 'excerpts': []}
    if not facts:
        return empty
    # The collector imports this module; lazy imports avoid a module-load cycle.
    from .research import html_to_text, inline_facts
    supplied = {_fact_key(fact) for fact in facts if isinstance(fact, dict) and
                fact.get('concept') == 'us-gaap:LongTermDebt'}
    verified = [fact for fact in inline_facts(document) if
                fact.get('concept') == 'us-gaap:LongTermDebt' and
                fact.get('parse_status') == 'parsed' and not fact.get('is_nil') and
                fact.get('dimensions') == {} and fact.get('instant') and
                isinstance(fact.get('unit'), str) and
                re.fullmatch(r'iso4217:[A-Z]{3}', fact['unit']) and
                _fact_key(fact) in supplied]
    if not verified:
        return empty
    by_start = {fact['location']['start_char']: fact for fact in verified}
    parser = _Tables(document)
    parser.feed(document)
    candidates = []
    for table in parser.tables:
        heading = table['heading']
        if (table['invalid'] or not heading or not _NOTE.fullmatch(heading['text']) or
                not 0 <= table['start'] - heading['start'] <= 4096 or
                table['end'] - table['start'] > MAX_TABLE_CHARS):
            continue
        preface = html_to_text(document[heading['start']:table['start']])
        if (_NARROW.search(preface) or
                (re.search(r'\b(?:short[- ]term\s+(?:borrowings|debt)|commercial\s+paper)\b', preface, re.I) and
                 re.search(r'\bseparately\b', preface, re.I))):
            continue
        if sum(any(_TOTAL.fullmatch(cell['text']) for cell in row) for row in table['rows']) != 1:
            continue
        headers, dated_columns = [], set()
        for row in table['rows']:
            labels = [cell for cell in row if _TOTAL.fullmatch(cell['text'])]
            if not labels:
                dates = {cell['column']: _column_date(cell['text']) for cell in row
                         if cell['span'] == 1 and not cell['facts'] and _column_date(cell['text'])}
                if dates:
                    headers.append(dates)
                    if len(dates) >= 2 and len({value[:4] for value in dates.values()}) == len(dates):
                        dated_columns.update(dates)
                continue
            if len(labels) != 1 or not headers:
                continue
            for cell in row:
                if cell['span'] != 1 or len(cell['facts']) != 1:
                    continue
                if cell['column'] not in dated_columns:
                    continue
                frame = cell['facts'][0]
                fact = by_start.get(frame['start'])
                if not fact or frame['attrs'].get('contextref') != fact['context_ref']:
                    continue
                visible = re.sub(r'[$\u20ac\u00a3\u00a5\s]', '', cell['text'])
                token = re.sub(r'[$\u20ac\u00a3\u00a5\s]', '', fact.get('raw_token', ''))
                if visible != token:
                    continue
                instant = fact['instant']
                if anchor is not None and instant != anchor:
                    continue
                dates = {header[cell['column']] for header in headers if cell['column'] in header}
                if len(dates) != 1:
                    continue
                column_date = dates.pop()
                if column_date != instant and column_date != instant[:4]:
                    continue
                scope = _scope_evidence(preface, table['rows'], cell['column'], fact)
                if scope:
                    candidates.append((instant, fact, heading['start'], table['end'], scope))
    if not candidates:
        return empty
    latest = max(item[0] for item in candidates)
    selected = [item for item in candidates if item[0] == latest]
    if len(selected) != 1:
        return empty  # Repeated totals/tables are ambiguous even if values coincide.
    instant, fact, raw_start, raw_end, scope = selected[0]
    excerpt = html_to_text(document[raw_start:raw_end])
    if len(excerpt) > MAX_EXCERPT_CHARS or not _TOTAL.search(excerpt):
        return empty
    cleaned = html_to_text(document)
    start = cleaned.find(excerpt)
    if start < 0 or cleaned.find(excerpt, start + 1) >= 0:
        return empty
    declaration = {'method': 'primary_debt_table_total', 'concept': fact['concept'],
                   'instant': instant, 'normalized_decimal': fact['normalized_decimal'],
                   'source_excerpt': excerpt, 'scope_evidence_type': scope}
    return {'classifications': {'financial_debt_total': declaration}, 'excerpts': [{
        'label': 'Primary debt-note table: labeled total and dated inline fact',
        'start_char': start, 'end_char': start + len(excerpt), 'text': excerpt,
        'text_sha256': hashlib.sha256(excerpt.encode()).hexdigest(),
        'cleaned_text_sha256': hashlib.sha256(cleaned.encode()).hexdigest(),
        'raw_decoded_text_sha256': hashlib.sha256(document.encode()).hexdigest(),
        'raw_start_char': raw_start, 'raw_end_char': raw_end,
        'offset_basis': 'html_to_text(document) Unicode character offsets'}]}

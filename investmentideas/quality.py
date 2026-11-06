"""Conservative diagnostics for portable research drafts, not certification.

These checks establish source-reference resolution, structural coverage and a few
explicit machine-readable date/value comparisons. They do not establish semantic
source support, official financial truth, forecast quality or investment merit.
Model reviewers must investigate those questions separately.

Review JSON is ``{verdict: 'pass'|'revise', findings: [{id, severity, issue,
source_refs: ['S1'], fix, current_excerpt?}]}``. Findings describe currently unresolved issues;
``revise`` is required iff a blocker or major remains. Minor observations may be
reported alongside ``pass``. A passing review remains a model's assessment.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import re

SOURCE_ID = re.compile(r'S[1-9][0-9]*\Z')
CITATION = re.compile(r'\[([^\]\n]{1,250})\]')
SOURCE_IN_CITATION = re.compile(r'\bS[1-9][0-9]*\b')
MAX_REVIEW_EXCERPT = 1200
WITHDRAWN_MATERIAL_FINDING = re.compile(
    r'\bno\s+(?:(?:additional|further|other)\s+)?(?:corrections?|actions?|changes?|revisions?)\s+'
    r'(?:is\s+)?(?:needed|required|necessary)\b'
    r'|\b(?:this\s+|the\s+)?finding\s+(?:is\s+|has\s+been\s+)?(?:already\s+)?'
    r'(?:resolved|corrected|fixed|withdrawn|retracted|downgraded)\b'
    r'|\b(?:withdraw|retract)\s+(?:this|the)\s+(?:finding|issue|allegation)\b'
    r'|\b(?:this\s+|the\s+)(?:issue|allegation)\s+(?:is\s+|has\s+been\s+)'
    r'(?:already\s+)?(?:resolved|withdrawn|retracted)\b'
    r'|^\s*(?:already\s+)?(?:resolved|withdrawn|retracted)\b'
    r'|\bminor\s+(?:presentation\s+)?issue\s+only\b', re.I)

def citation_ids(text):
    return {source for bracket in CITATION.findall(text) for source in SOURCE_IN_CITATION.findall(bracket)}
DIAGNOSTIC_SCOPE = {
    'checks': ['source reference resolution', 'decision-section coverage',
               'explicit quote dates', 'explicit model value comparisons'],
    'limitations': ['A resolving citation does not establish support for its claim.',
                    'Unlabelled prose numbers are not independently verified here.',
                    'Model review and software checks are not independent certification.']
}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Structured JSON contains a duplicate object key')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('Structured JSON contains a non-finite number')


def _finite_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Structured JSON contains a non-finite number')
    if isinstance(value, dict):
        for item in value.values():
            _finite_json(item)
    elif isinstance(value, list):
        for item in value:
            _finite_json(item)
    return value


def parse_json(text):
    """Parse one complete JSON value, optionally enclosed by one JSON fence.

    No prefix/suffix prose, duplicate keys, NaN or Infinity are accepted. Errors
    never echo the supplied text, which may contain untrusted research material.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > 2_000_000:
        raise ValueError('Expected nonempty structured JSON of at most 2 MB')
    value = text.strip().removeprefix('\ufeff').strip()
    if value.startswith('```'):
        match = re.fullmatch(r'```(?:json)?[ \t]*\r?\n?(.*?)\r?\n?```', value, re.I | re.S)
        if not match:
            raise ValueError('Expected one complete JSON code fence without surrounding prose')
        value = match.group(1).strip()
    try:
        return _finite_json(json.loads(value, object_pairs_hook=_unique_object, parse_constant=_invalid_constant))
    except (ValueError, RecursionError) as exc:
        raise ValueError('Invalid structured JSON; provide exactly one complete JSON value') from None


def _strip_quote_emphasis(text, delimiter):
    """Remove matching emphasis with monotonic opening/closing scans."""
    width, size = len(delimiter), len(text)
    parts, start, opening, closing = [], 0, 0, 0
    underscore = delimiter[0] == '_'

    def word(char):
        return char == '_' or char.isalnum()

    while opening + width < size:
        previous = text[opening - 1] if opening else ''
        excluded = (word(previous) or previous == '\\') if underscore else previous in {'\\', '*'}
        if (not text.startswith(delimiter, opening) or excluded or
                text[opening + width].isspace()):
            opening += 1
            continue
        closing = max(closing, opening + width + 1)
        while closing + width <= size:
            following = text[closing + width] if closing + width < size else ''
            excluded = word(following) if underscore else following == '*'
            if (text.startswith(delimiter, closing) and not excluded and
                    not text[closing - 1].isspace()):
                break
            closing += 1
        else:
            # No later opening can have a closing delimiter either.
            break
        parts.extend((text[start:opening], text[opening + width:closing]))
        start = opening = closing + width
    parts.append(text[start:])
    return ''.join(parts)


def _strip_quote_backticks(text):
    """Pair equal maximal backtick runs without rescanning unmatched spans."""
    runs, position = [], 0
    while position < len(text):
        if text[position] != '`':
            position += 1
            continue
        end = position + 1
        while end < len(text) and text[end] == '`':
            end += 1
        runs.append((position, end))
        position = end
    next_run, latest = [None] * len(runs), {}
    for index in range(len(runs) - 1, -1, -1):
        opening, end = runs[index]
        width = end - opening
        next_run[index] = latest.get(width)
        latest[width] = index
    parts, start, index = [], 0, 0
    while index < len(runs):
        match = next_run[index]
        if match is None:
            index += 1
            continue
        opening, end = runs[index]
        closing, close_end = runs[match]
        parts.extend((text[start:opening], text[end:closing]))
        start, index = close_end, match + 1
    parts.append(text[start:])
    return ''.join(parts)


def _render_normalized_quote(text):
    """Normalize only balanced inline Markdown and whitespace, not wording.

    This is deliberately not fuzzy matching or a general Markdown renderer.
    Numbers, words, punctuation and their order remain significant; omission
    markers are never removed or treated as wildcards.
    """
    for delimiter in ('**', '__', '*', '_'):
        text = _strip_quote_emphasis(text, delimiter)
    text = _strip_quote_backticks(text)
    return ' '.join(text.split())


def _quote_typography(text):
    """Treat typographic and straight quotation marks as presentation only.

    No words, digits, dashes, omission markers or other punctuation change.
    This does not repair paraphrases or join disjoint passages.
    """
    return text.translate(str.maketrans({'‘': "'", '’': "'", '“': '"', '”': '"'}))


def parse_review(text, memo=None):
    """Validate unresolved findings and their logically matching verdict.

    When a frozen memo is supplied, material findings must quote bounded wording
    from that version, allowing only Markdown emphasis/backticks and whitespace
    differences, or explicitly recorded smart/straight quote substitutions.
    Match provenance is retained; unmatched optional minor excerpts
    remain explicitly unverified. Location does not establish an allegation's
    truth. Explicitly withdrawn material findings invalidate the review rather
    than being silently dropped, downgraded or converted into approval.
    """
    if memo is not None and not isinstance(memo, str):
        raise ValueError('Frozen review memo must be text')
    rendered_memo = _render_normalized_quote(memo) if memo is not None else None
    typography_memo = _quote_typography(rendered_memo) if rendered_memo is not None else None
    review = parse_json(text)
    if not isinstance(review, dict) or not isinstance(review.get('findings'), list):
        raise ValueError('Review must be an object with a findings list')
    verdict = review.get('verdict')
    if not isinstance(verdict, str) or verdict.strip().lower() not in {'pass', 'revise'}:
        raise ValueError('Review verdict must be pass or revise')
    verdict = verdict.strip().lower()
    findings, identifiers = [], set()
    for finding in review['findings']:
        if not isinstance(finding, dict):
            raise ValueError('Each review finding must be an object')
        for key in ('id', 'severity', 'issue', 'fix'):
            if not isinstance(finding.get(key), str) or not finding[key].strip():
                raise ValueError(f'Review finding requires a nonempty {key}')
        identifier, severity = finding['id'].strip(), finding['severity'].strip().lower()
        if identifier in identifiers:
            raise ValueError('Review finding IDs must be unique')
        if severity not in {'blocker', 'major', 'minor'}:
            raise ValueError('Finding severity must be blocker, major or minor')
        refs = finding.get('source_refs')
        if not isinstance(refs, list) or any(not isinstance(ref, str) or not SOURCE_ID.fullmatch(ref) for ref in refs):
            raise ValueError('Finding source_refs must be a list of source IDs such as S1')
        if finding.get('status', 'open') != 'open':
            raise ValueError('Review findings must describe current unresolved issues, not archived dispositions')
        excerpt = finding.get('current_excerpt')
        material = severity in {'blocker', 'major'}
        excerpt_match = None
        if excerpt is not None or (memo is not None and material):
            if not isinstance(excerpt, str) or (material and not excerpt.strip()) or len(excerpt) > MAX_REVIEW_EXCERPT:
                raise ValueError(f'Review current_excerpt must be nonempty text of at most {MAX_REVIEW_EXCERPT} characters')
            normalized_excerpt = _render_normalized_quote(excerpt)
            if memo is None:
                excerpt_match = 'not_checked'
            elif excerpt.strip() and excerpt in memo:
                excerpt_match = 'literal'
            elif normalized_excerpt and normalized_excerpt in rendered_memo:
                excerpt_match = 'render-normalized'
            elif normalized_excerpt and _quote_typography(normalized_excerpt) in typography_memo:
                excerpt_match = 'typography-normalized'
            else:
                excerpt_match = 'not_matched'
            if material and excerpt_match == 'not_matched':
                raise ValueError('Review current_excerpt must quote contiguous wording from the frozen current memo')
        if material:
            # Do not interpret the memo's quoted wording as the reviewer's own
            # withdrawal. This deliberately checks only explicit dispositions.
            for key in ('issue', 'fix'):
                disposition = finding[key]
                if excerpt:
                    for opening, closing in (('"', '"'), ("'", "'"), ('“', '”')):
                        disposition = disposition.replace(opening + excerpt + closing, '')
                if WITHDRAWN_MATERIAL_FINDING.search(disposition):
                    raise ValueError('Material review finding explicitly withdraws or requests no correction; return only unresolved findings')
        identifiers.add(identifier)
        parsed = {'id': identifier, 'severity': severity, 'issue': finding['issue'].strip(),
                  'source_refs': list(dict.fromkeys(refs)), 'fix': finding['fix'].strip()}
        if excerpt is not None:
            parsed['current_excerpt'] = excerpt
            parsed['current_excerpt_match'] = excerpt_match
        findings.append(parsed)
    expected = 'revise' if any(item['severity'] in {'blocker', 'major'} for item in findings) else 'pass'
    if verdict != expected:
        raise ValueError('Review verdict contradicts its unresolved blocker/major findings')
    return {'verdict': verdict, 'findings': findings}


def _body(memo):
    # Register entries cannot substitute for citations attached to memo content.
    register = re.search(r'(?im)^#{1,6}\s*\**(?:source register|source ledger|references|sources)\b', memo)
    return memo[:register.start()] if register else memo


def claim_ledger(memo):
    """Map paragraphs to their stated citations without asserting semantic support."""
    if not isinstance(memo, str):
        return []
    ledger = []
    for paragraph in re.split(r'\n\s*\n', _body(memo)):
        text = paragraph.strip()
        if not text or re.fullmatch(r'#{1,6}[^\n]*', text):
            continue
        ledger.append({'paragraph': len(ledger) + 1, 'text': text,
                       'source_refs': sorted(citation_ids(text)),
                       'semantic_support': 'not_established_by_citation_syntax'})
    return ledger


def _finding(identifier, severity, issue, fix, refs=()):
    return {'id': identifier, 'severity': severity, 'issue': issue,
            'source_refs': list(refs), 'fix': fix}


def _headings(memo):
    headings = []
    for line in memo.splitlines():
        stripped = line.strip()
        if stripped.startswith('#'):
            headings.append(re.sub(r'[#*_`]', '', stripped).strip().lower())
        elif re.fullmatch(r'\*\*[^*\n]{2,150}\*\*:?|__[^_\n]{2,150}__:', stripped):
            headings.append(re.sub(r'[*_`]', '', stripped).strip().lower())
    return headings


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (TypeError, OverflowError):
        return False


def _expected_values(valuation):
    if not isinstance(valuation, dict):
        return {}
    expected = {}
    for key in ('weighted_per_share_value', 'weighted_value_per_share', 'weighted_per_share', 'weighted_intrinsic_value_per_share', 'weighted_intrinsic_value'):
        if _finite(valuation.get(key)):
            expected['weighted_value_per_share'] = valuation[key]
            break
    scenarios = valuation.get('scenarios', {})
    if isinstance(scenarios, dict):
        for scenario in ('bear', 'base', 'bull'):
            result = scenarios.get(scenario, {})
            if isinstance(result, dict):
                for key in ('value_per_share', 'intrinsic_value_per_share', 'per_share_value', 'equity_value_per_share'):
                    if _finite(result.get(key)):
                        expected[scenario + '_per_share'] = result[key]
                        break
    return expected


def _visible_values(memo):
    """Read only explicitly labelled per-share values, never arbitrary dollars."""
    found = []
    labels = {
        'weighted_value_per_share': r'weighted(?:\s+(?:intrinsic|fair))?\s+value\s+per[- ]share',
        'bear_per_share': r'bear(?:\s+case)?(?:\s+(?:intrinsic|fair))?\s+value\s+per[- ]share',
        'base_per_share': r'base(?:\s+case)?(?:\s+(?:intrinsic|fair))?\s+value\s+per[- ]share',
        'bull_per_share': r'bull(?:\s+case)?(?:\s+(?:intrinsic|fair))?\s+value\s+per[- ]share'
    }
    number = r'([-+]?[0-9][0-9,]*(?:\.[0-9]+)?)'
    clean = re.sub(r'\*\*|__', '', memo)
    for key, label in labels.items():
        for match in re.finditer(r'\b' + label + r'\s*[:=]\s*(?:USD\s*|\$\s*)?' + number, clean, re.I):
            found.append((key, float(match.group(1).replace(',', ''))))
    for match in re.finditer(r'<!--\s*valuation:([a-z_]+)\s*=\s*' + number + r'\s*-->', memo, re.I):
        found.append((match.group(1).lower(), float(match.group(2).replace(',', ''))))
    lines = clean.splitlines()
    for index, line in enumerate(lines):
        if '|' not in line:
            continue
        headers = [cell.strip().lower() for cell in line.strip().strip('|').split('|')]
        value_columns = [n for n, cell in enumerate(headers) if re.search(r'per[- /]share|value\s*/\s*share', cell)]
        scenario_columns = [n for n, cell in enumerate(headers) if 'scenario' in cell or cell == 'case']
        if not value_columns or not scenario_columns:
            continue
        for row in lines[index + 1:index + 12]:
            if '|' not in row:
                break
            cells = [cell.strip() for cell in row.strip().strip('|').split('|')]
            if max(value_columns[0], scenario_columns[0]) >= len(cells):
                continue
            label = cells[scenario_columns[0]].lower()
            scenario = next((name for name in ('bear', 'base', 'bull') if re.fullmatch(name + r'(?: case)?', label)), None)
            key = scenario + '_per_share' if scenario else ('weighted_value_per_share' if re.search(r'weighted|expected', label) else None)
            value = re.fullmatch(r'(?:USD\s*|\$\s*)?' + number, cells[value_columns[0]], re.I)
            if key and value:
                found.append((key, float(value.group(1).replace(',', ''))))
    return found


def inspect_memo(memo, sources, valuation=None):
    """Return conservative deterministic findings; absence is not certification.

    Optional explicit labels or per-share valuation tables can be compared against
    the executable model. Unlabelled narrative valuation numbers require semantic
    review and are deliberately not guessed from arbitrary currency amounts.
    """
    findings = []
    if not isinstance(memo, str) or not memo.strip():
        return [_finding('memo-empty', 'blocker', 'The research memo is empty.', 'Generate a complete memo before review.')]
    sources = sources if isinstance(sources, list) else []
    valid, duplicate, invalid_source = {}, set(), False
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get('id'), str) or not SOURCE_ID.fullmatch(source['id']):
            invalid_source = True
            continue
        if source['id'] in valid:
            duplicate.add(source['id'])
        valid[source['id']] = source
    if invalid_source:
        findings.append(_finding('source-id-invalid', 'blocker', 'A source has no valid source ID.', 'Assign each source one unique S1-style ID.'))
    if not valid or not any(source.get('data') for source in valid.values()):
        findings.append(_finding('evidence-empty', 'blocker', 'No nonempty source evidence accompanies the memo.', 'Retrieve and preserve factual evidence before drafting.'))
    if duplicate:
        findings.append(_finding('source-id-ambiguous', 'blocker', 'Multiple evidence records share a source ID.', 'Assign unique IDs and update affected citations.', sorted(duplicate)))
    if valid and all(source.get('kind') == 'market_snapshot' for source in valid.values()):
        findings.append(_finding('financial-evidence-missing', 'blocker', 'Quote metadata alone does not provide financial research evidence.', 'Retrieve financial statements or primary filings before analysis.'))
    references = citation_ids(_body(memo))
    unknown = references - valid.keys()
    if unknown:
        findings.append(_finding('citation-unresolved', 'major', 'Memo citations refer to unavailable evidence records.', 'Replace or support each unresolved source reference.', sorted(unknown)))
    if valid and not references:
        findings.append(_finding('citations-absent', 'major', 'The memo body has no source citations.', 'Attach preserved source IDs to material factual claims.'))
    headings = _headings(memo)
    groups = {
        'executive-view': (r'executive|investment view|conclusion|recommendation|thesis|decision', 'major'),
        'business-economics': (r'business|economics|industry|competitive|company overview', 'minor'),
        'financial-analysis': (r'financial|historical|cash flow|balance sheet|unit economics', 'major'),
        'valuation': (r'valuation|intrinsic value|discounted cash flow|\bdcf\b|what is priced|\bmodel\b', 'major'),
        'scenarios': (r'scenario|\bbear\b|\bbull\b|stress', 'major'),
        'risks': (r'\brisk|downside', 'major'),
        'disconfirming-evidence': (r'disconfirm|counter.?thesis|what would change|falsif|why we might be wrong|alternative explanation|kill criteria|contrary', 'minor'),
        'catalysts': (r'catalyst|monitor|watch|milestone|signpost', 'minor'),
        'diligence-questions': (r'question|diligence|unknown|missing evidence|limitation|unresolved', 'minor')
    }
    if not headings:
        findings.append(_finding('memo-structure', 'minor', 'No clear memo section headings were detected.', 'Make the decision, evidence, valuation and risks easy to locate.'))
    else:
        for name, (pattern, severity) in groups.items():
            if not any(re.search(pattern, heading) for heading in headings):
                findings.append(_finding('section-' + name, severity, f'No clear {name.replace("-", " ")} section was detected.', 'Add a substantive section or clearly identify the evidence gap.'))
    observed = set()
    for source in valid.values():
        if source.get('kind') != 'market_snapshot' or not isinstance(source.get('data'), dict):
            continue
        data = source['data']
        for key in ('quote_observed_at_utc', 'quote_observed_at_exchange_offset'):
            value = data.get(key)
            if isinstance(value, str):
                try:
                    observed.add(datetime.fromisoformat(value.replace('Z', '+00:00')).date().isoformat())
                except ValueError:
                    pass
        stamp = data.get('regularMarketTime')
        if _finite(stamp):
            try:
                quote = datetime.fromtimestamp(stamp, timezone.utc)
                observed.add(quote.date().isoformat())
                offset = data.get('gmtoffset')
                if isinstance(offset, int) and not isinstance(offset, bool) and -86400 < offset < 86400:
                    observed.add(quote.astimezone(timezone(timedelta(seconds=offset))).date().isoformat())
            except (ValueError, OverflowError, OSError):
                pass
    if observed:
        dates = set(re.findall(r'<!--\s*quote_as_of:\s*(\d{4}-\d{2}-\d{2})\s*-->', memo, re.I))
        for paragraph in claim_ledger(memo):
            text = paragraph['text']
            if re.search(r'\b(?:quote|market price|closing price|share price|stock price)\b', text, re.I):
                dates.update(re.findall(r'\b(?:as of|on|observed(?: at)?|dated)\s+(\d{4}-\d{2}-\d{2})\b', text, re.I))
        analysis = re.search(r'\b(?:analysis|research|memo) date\s*:\s*(\d{4}-\d{2}-\d{2})', memo, re.I)
        if analysis:
            for paragraph in claim_ledger(memo):
                text = paragraph['text']
                if re.search(r'\bon (?:the )?(?:analysis|research|memo) date\b', text, re.I) and re.search(r'\b(?:quote|market|closing|share|stock).{0,35}\bprice\b', text, re.I):
                    dates.add(analysis.group(1))
        if dates - observed:
            findings.append(_finding('quote-date-mismatch', 'major', 'An explicitly stated quote observation date differs from the saved regular-session timestamp.', 'State the saved quote observation date separately from the memo date.'))
    expected, seen = _expected_values(valuation), _visible_values(memo)
    mismatches, unmatched = set(), set()
    for key, value in seen:
        if key not in expected:
            unmatched.add(key)
        elif not math.isfinite(value) or abs(value - expected[key]) > max(.00500001, abs(expected[key]) * 1e-6):
            mismatches.add(key)
    if mismatches:
        findings.append(_finding('valuation-value-mismatch', 'major', 'Explicit per-share values in the memo differ from the executable model: ' + ', '.join(sorted(mismatches)) + '.', 'Regenerate every displayed valuation figure from the current model outputs.'))
    if unmatched and not expected:
        findings.append(_finding('valuation-model-missing', 'major', 'The memo states explicit per-share values without comparable executable model outputs.', 'Supply and recompute a supported valuation model, or remove unsupported targets.'))
    return findings

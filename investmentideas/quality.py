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
import bisect
import json
import math
import re

SOURCE_ID = re.compile(r'S[1-9][0-9]*\Z')
CITATION = re.compile(r'\[([^\]\n]{1,250})\]')
SOURCE_IN_CITATION = re.compile(r'\bS[1-9][0-9]*\b')
MAX_REVIEW_EXCERPT = 1200
# Shortest exact run of memo wording that can anchor a finding whose quotation
# also contains words the memo does not (an inserted parenthetical, two passages
# joined with an ellipsis). The repair prompt asks for 50 to 300 characters.
MIN_EXCERPT_ANCHOR = 50
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


def _longest_exact_span(memo, excerpt):
    """Longest run of ``excerpt`` that is verbatim in ``memo``, cut to whole words.

    Reviewers often embellish an otherwise accurate quotation. The finding is
    then anchored to the part that is exactly the memo's wording. Nothing is
    matched approximately and no memo text the reviewer did not quote is added.
    Returns ``None`` when no exact run of at least ``MIN_EXCERPT_ANCHOR``
    characters exists.
    """
    if len(excerpt) < MIN_EXCERPT_ANCHOR:
        return None
    from difflib import SequenceMatcher
    match = SequenceMatcher(None, memo, excerpt, autojunk=False).find_longest_match(0, len(memo), 0, len(excerpt))
    start, end = match.a, match.a + match.size
    # A run that begins or ends inside a word is cut back to the word boundary.
    if start > 0 and not memo[start - 1].isspace() and not memo[start].isspace():
        while start < end and not memo[start].isspace():
            start += 1
    if end < len(memo) and not memo[end].isspace() and not memo[end - 1].isspace():
        while end > start and not memo[end - 1].isspace():
            end -= 1
    span = memo[start:end].strip()
    return span if len(span) >= MIN_EXCERPT_ANCHOR else None


def parse_review(text, memo=None, *, anchor_embellished=False):
    """Validate unresolved findings and their logically matching verdict.

    When a frozen memo is supplied, material findings must quote bounded wording
    from that version, allowing only Markdown emphasis/backticks and whitespace
    differences, or explicitly recorded smart/straight quote substitutions.
    Changed words or numbers and joined passages are rejected.

    ``anchor_embellished`` is for a review that has already had its one format
    repair. A quotation that still contains words absent from the memo is then
    anchored to its longest exact run of at least ``MIN_EXCERPT_ANCHOR``
    characters, with the reviewer's own wording kept beside it, instead of
    discarding an entire review for one inexact quotation. The inexact words
    are never treated as the memo's. A quotation with no such run is rejected.
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
            raise ValueError('Review findings must describe current unresolved issues only; archived dispositions are excluded')
        excerpt = finding.get('current_excerpt')
        material = severity in {'blocker', 'major'}
        excerpt_match, reviewer_excerpt = None, None
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
                anchor = (_longest_exact_span(typography_memo, _quote_typography(normalized_excerpt))
                          if anchor_embellished else None)
                if anchor is None:
                    excerpt_match = 'not_matched'
                else:
                    excerpt_match, reviewer_excerpt, excerpt = 'longest-exact-span', excerpt, anchor
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
        rule = finding.get('rule')
        rule = str(rule).strip().upper() if rule is not None and str(rule).strip() else None
        if rule is not None:
            if rule not in RULE_SEVERITY:
                raise ValueError('Finding rule must be one of ' + ', '.join(RULE_SEVERITY))
        identifiers.add(identifier)
        parsed = {'id': identifier, 'severity': severity, 'issue': finding['issue'].strip(),
                  'source_refs': list(dict.fromkeys(refs)), 'fix': finding['fix'].strip()}
        if rule:
            parsed['rule'] = rule
        if excerpt is not None:
            parsed['current_excerpt'] = excerpt
            parsed['current_excerpt_match'] = excerpt_match
            if reviewer_excerpt is not None:
                parsed['reviewer_excerpt'] = reviewer_excerpt
        findings.append(parsed)
    expected = 'revise' if any(item['severity'] in {'blocker', 'major'} for item in findings) else 'pass'
    if verdict != expected:
        raise ValueError('Review verdict contradicts its unresolved blocker/major findings')
    # A finding is graded no higher than the rule it names; the verdict follows.
    for item in findings:
        cap = RULE_SEVERITY.get(item.get('rule'))
        if cap and _SEVERITY_RANK[item['severity']] > _SEVERITY_RANK[cap]:
            item['severity_as_reviewed'], item['severity'] = item['severity'], cap
    as_reviewed = verdict
    verdict = 'revise' if any(item['severity'] in {'blocker', 'major'} for item in findings) else 'pass'
    parsed = {'verdict': verdict, 'findings': findings}
    if verdict != as_reviewed:
        parsed['verdict_as_reviewed'] = as_reviewed
    # The reviewer's statement of the conclusion's central weakness is kept when
    # well formed. It is reported to the reader; it is not a finding by itself.
    weakness = review.get('central_weakness')
    if (isinstance(weakness, dict) and all(isinstance(weakness.get(key), str) and weakness[key].strip()
                                           for key in ('claim', 'computed_effect', 'memo_handles_it'))):
        parsed['central_weakness'] = {'claim': weakness['claim'].strip()[:1200],
                                      'computed_effect': weakness['computed_effect'].strip()[:600],
                                      'memo_handles_it': _assessment_answer(weakness['memo_handles_it'])}
    explained = review.get('gap_explained')
    if isinstance(explained, str) and explained.strip():
        parsed['gap_explained'] = _assessment_answer(explained)
    return parsed


def _assessment_answer(text):
    """Map a reviewer's yes/partly/no answer; anything short of a plain yes counts against the memo."""
    word = re.sub(r'[^a-z ]', '', text.strip().lower()).strip()
    if word == 'yes':
        return 'yes'
    if word.startswith(('partly', 'partial', 'partially', 'somewhat')):
        return 'partly'
    return 'no'


# One quality standard, shared by the writer prompt, the deterministic checks and
# the reviewer. Live reviews (2026-11-14) applied a private, moving standard and
# never converged; the writer could not see what would be judged.
# Each rule has an ID; a review finding names the rule it breaks and cannot be
# graded above that rule's class. Live reviews (2026-11-14) graded wording
# choices as major and drew different majors from the same draft.
RUBRIC_RULES = (
    ('B1', 'blocker', 'a quote or fact not in its cited source'),
    ('B2', 'blocker', 'a trading-layer term (rating, target price, entry level, margin of safety, upside or downside, '
                      'position size)'),
    ('B3', 'blocker', 'a valuation figure that differs from the computed output'),
    ('M1', 'major', 'a company fact or a number supported only by an unverified web excerpt, or an unverified excerpt '
                    'used without an inline "unverified" label'),
    ('M2', 'major', 'one-at-a-time rows added together or called cumulative'),
    ('M3', 'major', 'a number that is neither in the evidence, nor in the computed output, nor a shown calculation '
                    'from cited figures'),
    ('M4', 'major', 'a statement that contradicts the computed account of which inputs alone can and cannot bring '
                    'value to the price (the first-page table "What the price needs", the flat discount rate included)'),
    ('M5', 'major', 'a material claim contradicted by primary evidence in the run'),
    ('M6', 'major', 'a model output cited to a filing or source as if reported'),
    ('N1', 'minor', 'rounding within the last displayed digit'),
    ('N2', 'minor', 'wording, emphasis or structure'),
    ('N3', 'minor', 'an omission that does not change the conclusion'),
)
# The rule the unverified-company-fact check enforces, told to the writer, the fix and edit
# calls and the reviewer in the same words (rule M1; Walmart's first review, 2026-11-27).
UNVERIFIED_FACT_RULE = (
    'A company fact or figure whose only support is an unverified excerpt is deleted, replaced by the primary-source '
    'figure, or stated as the own view or estimate of an outside party the sentence names (a research firm, an '
    'analyst, an aggregator, a press report, consensus), in a sentence that draws no company conclusion and makes no '
    'comparison with the house forecast. An excerpt, or the company\'s own filing quoted through one, is not an outside '
    'party, and an "unverified" label does not make a company fact acceptable. Wrong: "Advertising revenue rose 37% in '
    'the quarter [S19, unverified]." Right: "One outside analysis argues that advertising is growing faster than '
    'merchandise sales [S19, unverified]."')
RULE_SEVERITY = {rule: severity for rule, severity, _ in RUBRIC_RULES}
# The trading-layer terms rule B2 names. A B2 finding whose quoted text has none
# of them is refuted by Python ("accretive" was graded a blocker, 2026-11-15).
TRADING_TERMS = re.compile(r'\b(?:ratings?|rated|buy|sell|overweight|underweight|outperform|underperform|target price|'
                           r'price target|entry (?:price|level|point)|margin of safety|upside|downside|position siz\w*)\b', re.I)
HOUSE_ATTRIBUTION = re.compile(r'\b(?:house|computed)\b', re.I)
_SEVERITY_RANK = {'minor': 0, 'major': 1, 'blocker': 2}


def _rules(severity):
    return '; '.join(f'{rule} {text}' for rule, level, text in RUBRIC_RULES if level == severity)


# The owner's voice rules (2026-08-02) and the memo's number style (2026-11-27), told to the
# writer and judged by the reviewer as wording (N2); the free checks below count the
# mechanical ones.
WRITING_STANDARD = (
    'Writing standard (rule N2), for the eight prose sections only: the first page, computed tables, labels Python '
    'prints and the method notes are not graded for style. Open each section with its conclusion. Write short declarative sentences that state '
    'the positive claim. Never use "X, not Y" or any other contrast formula, and end no paragraph on a summary '
    'sentence. Use concrete nouns, never consultant abstractions or metaphors. State a limit once, plainly, with '
    'no commentary on what the evidence does or does not prove. Do not restate the first page\'s tables. State a '
    'figure as the house or computed value ("the base-case value is $139"); do not narrate the exhibit it came '
    'from ("the computed summary places it at"). Numbers: the first page names the currency once, so write $139 '
    'a share, prices as quoted ($355.58), aggregates as $34.3B or $850M, never "USD" for a dollar filer and never '
    'a number of seven or more digits; percentages to one decimal; no decimals unless the precision matters; no '
    'em dashes.')
RUBRIC = (
    'Quality standard (shared by the writer, the automatic checks and the reviewer). Each review finding names the '
    'rule it breaks. Blocker: ' + _rules('blocker') + '. Major: ' + _rules('major') + '. Minor: ' + _rules('minor') +
    '. A house or computed figure that the prose names as such is attributed, even when the sentence also cites the '
    'reported figure it is compared with. Naming the convention step the value is most sensitive to is required and '
    'is not an attribution of the gap to price. Rounding is never material; unverified excerpts used only for attributed '
    'outside views, consensus or guidance context are not material, but a company fact or number supported only by '
    'an unverified excerpt is, whatever its label. ' + UNVERIFIED_FACT_RULE + ' ' + WRITING_STANDARD)


# Free checks run on a draft before the paid review. They trace every figure in
# the prose to the computed output, the reported inputs or a primary source, and
# catch the defects a live review spent three paid rounds on (2026-11-14).
UNVERIFIED_SOURCE_TIERS = {'search_excerpt_unverified'}
PROSE_WORD_LIMIT = 3600
_NUMBER = re.compile(r'(?<![\w.\[/-])(-?\$?\d[\d,]*(?:\.\d+)?)(\s*(?:%|bps|bp|x|×|billion|bn|B|million|mm|M|trillion|T|percentage points?|points?|pts|pp)\b|%)?', re.I)
_SCALES = {'%': (1e-2, 1), 'points': (1e-2, 1), 'point': (1e-2, 1), 'pts': (1e-2, 1), 'pp': (1e-2, 1),
           'percentage points': (1e-2, 1), 'percentage point': (1e-2, 1), 'bps': (1e-4, 1), 'bp': (1e-4, 1), 'billion': (1e9, 1e3, 1), 'bn': (1e9, 1e3, 1),
           'b': (1e9, 1e3, 1), 'million': (1e6, 1), 'mm': (1e6, 1), 'm': (1e6, 1), 'trillion': (1e12, 1e6, 1e3, 1),
           't': (1e12, 1e6, 1e3, 1),
           'x': (1,), '×': (1,)}
# Prose that assigns the gap to price a cause; Python states which inputs can close it.
_CAUSE = re.compile(r'\b(?:(?:gap|difference|disagreement|divergence)\b[^.;]{0,40}?\b(?:value and (?:the )?price|'
                    r'price and (?:the )?value|(?:to|from) (?:the )?(?:market )?price|value[- ]to[- ]price)|discount to the price|'
                    r'premium to the price)\b[^.;]{0,90}?\b(?:primarily|principally|almost entirely|mainly|largely|'
                    r'rests? (?:on|in)|lies? in|sits? in|is driven by|driven by|explained by|attributable to)\b', re.I)
# A sentence that reports the index benchmark's own discount or premium to its price
# ("the S&P 500 is worth 0.78 of its price under these conventions, a discount to the
# price that rests on ...") is context, not a cause of the company's gap.
_BENCHMARK = re.compile(r'\b(?:index benchmark|S&P 500|the index)\b', re.I)


def _assigns_gap_cause(line):
    matches = list(_CAUSE.finditer(line))
    if not matches:
        return False
    if _BENCHMARK.search(line) and all(re.match(r'(?:discount|premium) to the price', match.group(0), re.I)
                                       for match in matches):
        return False
    return True


# A rising word with a falling pair, or the reverse ("a further increase" from
# USD 678 billion to USD 677 billion, 2026-11-15).
_UNIT = r'\s*(?P<{0}>%|percent|points?|bps|billion|bn|million|mm|thousand|trillion)?'
_DIRECTION = re.compile(r'\b(?P<word>increas\w*|rose|rise[sn]?|grew|grow(?:s|n)?|higher|expand\w*|climb\w*|'
                        r'decreas\w*|declin\w*|fell|fall(?:s|en)?|lower|contract\w*|shrank|drop\w*)\b[^.;]{0,80}?'
                        r'\bfrom\s+(?:USD\s*|\$)?(?P<a>-?[\d,]+(?:\.\d+)?)' + _UNIT.format('ua') +
                        r'[^.;]{0,40}?\bto\s+(?:USD\s*|\$)?(?P<b>-?[\d,]+(?:\.\d+)?)' + _UNIT.format('ub'), re.I)
_YEAR = re.compile(r'(?:19|20)\d\d')
_FREQUENCY = re.compile(r'\b(?:historical(?:ly)? frequenc\w*|in (?:most|many) (?:past )?cycles)\b', re.I)
_META = re.compile(r'\b(?:Python|the prompt|as instructed|the instructions)\b')
_ADDITIVE = re.compile(r'\b(?:together|combined|in total|cumulative(?:ly)?|add up|sum of|more than the entire|account for (?:all|the entire|more than))\b', re.I)
_PANEL = re.compile(r'\b(?:convention|panel|step|one[- ]at[- ]a[- ]time|terminal return|rate path)\b', re.I)


# Left out of the figure trace: the monthly pairs in the plan's rate_history.months,
# up to 240 rates that would let almost any small percentage in a draft pass as traced.
# Only that list: the convention panel's rate_history summary (months computed, the
# median value) is computed output and stays traceable (review of the merged code,
# 2026-11-27: a median rate-regime value the memo quoted was flagged as untraced).
def _untraced(parent, key, item):
    return parent == 'rate_history' and key == 'months' and isinstance(item, list)


def _numeric_leaves(value, out, parent=None):
    if isinstance(value, dict):
        for key, item in value.items():
            if not _untraced(parent, key, item):
                _numeric_leaves(item, out, key)
    elif isinstance(value, list):
        for item in value:
            _numeric_leaves(item, out)
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, (int, float)):
        if value == value and abs(value) < 1e16:
            out.add(abs(float(value)))
    elif isinstance(value, str):
        for match in re.finditer(r'\d[\d,]*(?:\.\d+)?', value[:200000]):
            try:
                out.add(float(match.group().replace(',', '')))
            except ValueError:
                pass
    return out


def _traced(number, decimals, unit, pool):
    """True when a pool value displays as this number at its own precision."""
    tolerance = 0.5 * 10 ** -decimals + 1e-9
    for scale in _SCALES.get((unit or '').strip().lower(), (1, 1e3, 1e6, 1e9)):
        low, high = (number - tolerance) * scale, (number + tolerance) * scale
        index = bisect.bisect_left(pool, low)
        if index < len(pool) and pool[index] <= high:
            return True
    return False


# The computed first page and the computed sections after the prose, in the current
# layout and in the layout before the 2026-11-27 redesign.
COMPUTED_FIRST_PAGE = re.compile(r'(?ms)^## (?:Valuation summary \(computed by Python\)|Summary)\n.*?(?=^#|\Z)')
COMPUTED_AFTER_PROSE = re.compile(r'\n## (?:Executable valuation|Valuation detail)\b')


def writer_prose(memo):
    """The writer's text: no computed first page and nothing from the computed valuation detail on."""
    body = COMPUTED_AFTER_PROSE.split(memo, 1)[0]
    # The first page has no headings of its own: it ends at the next heading of any level.
    summary = COMPUTED_FIRST_PAGE.search(body)
    if summary:
        body = body.replace(summary.group(0), '')
    return body


def prose_words(memo):
    """Words of the writer's prose: no computed summary, no Python-rendered valuation section."""
    return len(re.findall(r"[A-Za-z][A-Za-z'\-]*", writer_prose(memo)))


def trace_pools(sources, computed=()):
    """Sorted numeric pools: (primary sources and computed output, unverified excerpts, computed output)."""
    primary, unverified, house = set(), set(), set()
    for source in sources:
        if isinstance(source, dict):
            target = unverified if source.get('source_tier') in UNVERIFIED_SOURCE_TIERS else primary
            for part in (source.get('data'), source.get('primary_focus_excerpts'), source.get('breakdown_tables'),
                         source.get('historical_baseline')):
                _numeric_leaves(part, target)
    for item in computed:
        _numeric_leaves(item, house)
    # The memo shows computed figures to three significant figures ($1,970B, $41.4):
    # those rounded forms trace too. Nothing coarser is added.
    house |= {float(f'{value:.3g}') for value in house if value}
    return sorted(primary | house), sorted(unverified), sorted(house)


def _figures(text):
    """(display, number, decimals, unit) for each checkable figure: no citations, years, dates or small whole numbers."""
    clean = re.sub(r'\[[^\]]*S\d[^\]]*\]', '', text)
    clean = re.sub(r'\b(?:S&P|Nasdaq|Russell|FTSE|Stoxx|Nikkei|MSCI)\s*\d+\b', '', clean, flags=re.I)
    clean = re.sub(r'\b(?:19|20)\d\d(?:-\d\d-\d\d)?\b|\bFY\s?\d+|\bQ[1-4]\b|\b\d+(?:st|nd|rd|th)\b|\(\d\)|\b\d+\.\s', '', clean)
    for match in _NUMBER.finditer(clean):
        raw = match.group(1).replace('$', '').replace(',', '')
        try:
            number = abs(float(raw))
        except ValueError:
            continue
        decimals = len(raw.split('.')[1]) if '.' in raw else 0
        digits = len(raw.replace('-', '').replace('.', '').lstrip('0'))
        # Whole numbers under three digits match some pool value by chance.
        if decimals == 0 and digits < 3:
            continue
        yield match.group(0).strip(), number, decimals, match.group(2)


def untraced_figures(text, pools):
    """Checkable figures in ``text`` that match no primary source or computed value, or None if it has none."""
    figures = list(_figures(text or ''))
    if not figures:
        return None
    return [display for display, number, decimals, unit in figures if not _traced(number, decimals, unit, pools[0])]


# A company fact resting only on unverified web excerpts (rule M1). Every saved first review
# raised it (Walmart, Microsoft, Coca-Cola, Alphabet and Apple, 2026-11), and the reviewer
# does not accept an inline "unverified" label on a company fact. What those reviews accepted
# and rejected sets the test. Accepted: the sentence's subject is an outside party or its view
# (consensus, an aggregator, an outside analysis, a press report, Morningstar, "one outside
# view argues", "it holds that"), guidance, or a named speaker's words (management, the CEO or
# CFO "cited", "describes", said on the call). Rejected: the same figures stated as company
# results ("advertising rose 37%", "executive pay ties about 82%"); an excerpt as the subject
# ("an unverified research excerpt estimates concentrate margins of 40% to 60%"); the company's
# own documents quoted through an excerpt ("the 10-Q describes", "the results reported"); a
# gap note that restates the figure ("$45.2 billion ... come only from an unverified call
# excerpt"); a conditional built on an unverified premise ("price investment funded by a
# refund that does not recur could leave lower prices"); and the house forecast compared with
# an unverified figure ("the house operating forecast is close to consensus").
_OUTSIDE_PARTY = re.compile(
    r'\b(?:consensus|analysts?|aggregators?|outside|third[- ]party|press (?!release)|news|media|surveys?|brokers?|'
    r'sell[- ]side|bulls?|bears?|critics?|commentators?|investors?|short[- ]sellers?|rating agenc\w+|'
    r'forecasters?|observers?|opposing (?:case|view)|(?:this|that|one|another|a|the) view|'
    r'(?:research|brokerage|advisory|consulting) (?:firm|house|note|report)s?)\b', re.I)
# Named outside parties: research and data providers, banks, and firm names such as "TON618 Capital".
_NAMED_PARTY = re.compile(
    r'\b(?:Morningstar|Bloomberg|Reuters|FactSet|Visible Alpha|Zacks|Yahoo|Stock Analysis|MarketWatch|Barron\'s|'
    r'Seeking Alpha|CNBC|Financial Times|Wall Street Journal|WSJ|Moody\'s|Fitch|Gartner|IDC|Canalys|Counterpoint|'
    r'Jefferies|Goldman|Morgan Stanley|JPMorgan|J\.P\. Morgan|UBS|Barclays|Citi(?:group)?|Bernstein|Evercore|'
    r'Wedbush|DBS|Wells Fargo|BofA|Bank of America|Deutsche Bank|HSBC|Mizuho|Nomura|'
    r'[A-Z][\w&.\'-]*(?: [A-Z][\w&.\'-]*)* (?:Capital|Research|Securities|Partners|Analytics|Advisors|Markets))\b')
_GUIDANCE = re.compile(r'\bguid(?:ance|ed)\b', re.I)
_VIEW_VERB = re.compile(r'\b(?:holds?|held|argues?|argued|contends?|contended|claims?|claimed|believes?|believed|'
                        r'predicts?|predicted|warns?|warned)\b', re.I)
_PRONOUN_REPORT = re.compile(r'^(?:\W*\w+\W*\*\*\s*)?(?:It|They|He|She|The (?:firm|report|analyst|note|article|author))'
                             r'\s+(?:also\s+)?(?:notes?|says?|said|reports?|reported|estimates?|expects?|describes?|'
                             r'cites?|puts?|sees?|calls?|projects?|forecasts?)\b')
_SPEAKER = re.compile(r'\b(?:management|CEO|CFO|chief (?:executive|financial) officer|executives?|chair(?:man|woman)?)\b'
                      r'[^.;]{0,60}?\b(?:said|says|cited|cites|described|describes|told|stated|noted|notes|expects?|'
                      r'expected|called|calls|guided|framed|frames)\b|\bon the (?:earnings |results )?call\b', re.I)
_EXCERPT_SUBJECT = re.compile(r'\b(?:an?|the)\s+(?:unverified\s+)?(?:[\w-]+\s+){0,2}excerpts?\b', re.I)
_ONLY_UNVERIFIED = re.compile(r'\b(?:only|solely)\s+(?:in|by|as|from)\b[^.;]{0,40}\bunverified\b|'
                              r'\bis (?:unverified|outside) context\b|\bcontext only\b|\bonly\b[^.;]{0,30}\bcontext\b', re.I)
_COMPANY_DOCUMENT = re.compile(r'\b(?:10-[KQ]|8-K|20-F|annual report|quarterly report|proxy(?: statement)?|'
                               r'earnings release|press release|the release|(?:quarter|year|period)(?:ly)? results|'
                               r'the results|the filing)\b', re.I)
_FIGURE = re.compile(r'\d')
# The house forecast set beside an unverified figure. Raised by the first reviews of
# Microsoft and Coca-Cola and in Alphabet's second, cited or not; the evidence-tier rule
# bars an unverified excerpt as the only support for a number used in a comparison.
_COMPARISON = re.compile(r'\b(?:close to|consistent with|in line with|matches|agrees? with|near(?!-)|ahead of|'
                         r'above|below|higher than|lower than|compares? with|against|differs? from|differ from|'
                         r'modestly different from|supports?|confirms?)\b', re.I)
_UNVERIFIED_REFERENT = re.compile(r'\b(?:consensus|guidance|expectations|(?:outside|analysts?\W?) (?:estimates?|models?|'
                                  r'figures?)|(?:that|this|those|these) (?:description|estimates?|figures?|guidance))\b', re.I)
_HOUSE_SUBJECT = re.compile(r'\b(?:house|base(?:-case)? (?:path|case|forecast)|our forecast)\b', re.I)


def _attributed_outside(line):
    """True when an unverified-only sentence presents its content as an outside party's view, guidance or a speaker's words."""
    figure = bool(_FIGURE.search(re.sub(r'\b(?:19|20)\d\d(?:-\d\d(?:-\d\d)?)?\b', '', line)))
    if _ONLY_UNVERIFIED.search(line) and not figure:
        return True  # an evidence-gap note that restates no figure
    if _SPEAKER.search(line) or _PRONOUN_REPORT.search(line):
        return True
    outside = _OUTSIDE_PARTY.search(line) or _NAMED_PARTY.search(line)
    if not outside and (_EXCERPT_SUBJECT.search(line) or _COMPANY_DOCUMENT.search(line)):
        return False  # an excerpt or the company's own document is a conduit, not a party
    return bool(outside or _GUIDANCE.search(line) or _VIEW_VERB.search(line))


def _citations(line):
    return set(re.findall(r'S\d+', ' '.join(re.findall(r'\[([^\]]*S\d[^\]]*)\]', line))))


def unverified_company_facts(body, tiers):
    """Sentences of the prose that rest a company fact, or a comparison with the house forecast, on unverified excerpts.

    ``tiers`` maps a source ID to its tier. Returns (kind, sentence) pairs: 'fact' for a sentence
    whose citations are all unverified and which presents its content as the company's, and
    'comparison' for a sentence that sets the house forecast beside consensus, guidance or an
    outside estimate supported, if at all, only by unverified excerpts. A conclusion that rests
    on such a figure without naming it is not detected: the saved reviews show no wording that
    separates it reliably from a conclusion drawn from the primary record.
    """
    found = []
    # "Walmart U.S. advertising" is one sentence.
    for raw in re.split(r'(?<=[.!?])(?<!\bU\.S\.)(?<!\bU\.K\.)(?<!\bInc\.)(?<!\bCo\.)(?<!\bvs\.)\s+|\n+', body or ''):
        line = raw.strip()
        if not line or line.startswith(('#', '|')):
            continue
        cited = _citations(line)
        unverified = bool(cited) and all(tiers.get(ref) in UNVERIFIED_SOURCE_TIERS for ref in cited)
        # Citations and a bold lead-in ("**Revenue against outside estimates.**") are not the claim.
        text = re.sub(r'^\W*\*\*[^*]+\*\*\s*', '', re.sub(r'\[[^\]]*\]', '', line))
        if (not cited or unverified) and _HOUSE_SUBJECT.search(text) and _COMPARISON.search(text) and \
                _UNVERIFIED_REFERENT.search(text):
            found.append(('comparison', line[:300]))
        elif unverified and not _attributed_outside(text):
            found.append(('fact', line[:300]))
    return found



UNVERIFIED_FACT_FIX = (
    'Delete the claim; or replace it with the primary-source figure if the evidence has one, citing that source; or '
    'restate it as the own view or estimate of an outside party the sentence names, in a sentence that draws no '
    'company conclusion from it. Never keep it as a company fact with an "unverified" label. For a comparison of the house forecast with '
    'an unverified figure, delete the comparison; consensus may stay as attributed context in its own sentence.')


def prereview_checks(memo, sources, computed=()):
    """Deterministic defects in a draft's prose, before any paid review.

    ``computed`` holds the valuation output, the locked plan and any rendered
    tables. Returns findings in the review shape (severity, issue, excerpt).
    The memo body ends at the Python-rendered "## Executable valuation".
    """
    body = writer_prose(memo)
    # Python marks every unverified citation inline; judge the writer's own text.
    body = re.sub(r'\b(S\d+) unverified\b', r'\1', body)
    tiers = {source.get('id'): source.get('source_tier') for source in sources if isinstance(source, dict)}
    primary_pool, unverified_pool, house_pool = trace_pools(sources, computed)
    untraced, unlabelled, additive, unlabelled_claims, cite_primary, causes = [], [], [], [], [], []
    tables, directions, frequencies, meta = [], [], [], []
    for raw_line in body.split('\n'):
        if raw_line.lstrip().startswith('|'):
            tables.append(raw_line.strip()[:200])
    for sentence in re.split(r'(?<=[.!?])\s+|\n+', body):
        line = sentence.strip()
        if not line or line.startswith('#'):
            continue
        cited = set(re.findall(r'S\d+', ' '.join(re.findall(r'\[([^\]]*S\d[^\]]*)\]', line))))
        if _ADDITIVE.search(line) and _PANEL.search(line):
            additive.append(line[:300])
        if _assigns_gap_cause(line):
            causes.append(line[:300])
        for match in _DIRECTION.finditer(line):
            if _YEAR.fullmatch(match.group('a')) and _YEAR.fullmatch(match.group('b')):
                continue  # "declined from 2021 to 2023" is a span of years
            if (match.group('ua') or '').lower().rstrip('s') != (match.group('ub') or '').lower().rstrip('s'):
                continue  # mixed units cannot be compared here
            try:
                a, b = float(match.group('a').replace(',', '')), float(match.group('b').replace(',', ''))
            except ValueError:
                continue
            rising = re.match(r'(?i)increas|rose|rise|grew|grow|higher|expand|climb', match.group('word'))
            if (rising and b < a) or (not rising and b > a):
                directions.append(line[:300])
        if _FREQUENCY.search(line) and not cited:
            frequencies.append(line[:300])
        if _META.search(line):
            meta.append(line[:300])
        for display, number, decimals, unit in _figures(line):
            if _traced(number, decimals, unit, primary_pool):
                if (cited and all(tiers.get(ref) in UNVERIFIED_SOURCE_TIERS for ref in cited) and
                        not _traced(number, decimals, unit, house_pool)):
                    cite_primary.append((display, line[:300]))
                continue
            if _traced(number, decimals, unit, unverified_pool):
                if not re.search(r'unverified', line, re.I):
                    unlabelled.append((display, line[:300]))
                continue
            untraced.append((display, line[:300]))
    company_facts = unverified_company_facts(body, tiers)
    findings = []
    def add(identifier, severity, issue, fix, items):
        findings.append({'id': identifier, 'severity': severity, 'issue': issue, 'fix': fix, 'source_refs': [],
                         'items': [{'figure': figure, 'sentence': text} for figure, text in items][:25]})
    if company_facts:
        # Minor: the fix call still removes these before the paid review, but a false alarm
        # (2 of about 40 flagged sentences in the saved runs) can never hold up approval alone.
        add('unverified-company-fact', 'minor', f'{len(company_facts)} sentences state a company fact or figure, or '
            'compare the house forecast with one, whose only support is an unverified web excerpt (rule M1). An '
            '"unverified" label does not make a company fact acceptable.', UNVERIFIED_FACT_FIX,
            [('', text) for _, text in company_facts])
    if untraced:
        add('number-untraced', 'minor', f'{len(untraced)} figures in the prose match nothing in the computed output, '
            'the reported inputs or any primary source within display rounding.',
            'Replace each with the exact figure from the computed tables or a primary source, or remove it.', untraced)
    if unlabelled:
        add('number-unverified-only', 'minor', f'{len(unlabelled)} figures appear only in unverified web excerpts and the '
            'sentence does not present them as unverified outside views or consensus context.',
            'Use the primary figure if one exists; otherwise attribute it as unverified outside context in words, or remove it.',
            unlabelled)
    if cite_primary:
        add('cite-primary-source', 'minor', f'{len(cite_primary)} figures are cited only to unverified excerpts although '
            'a primary source or the computed tables contain them.',
            'Cite the primary source or computed table that contains each figure.', cite_primary)
    if tables:
        add('table-in-prose', 'minor', 'The prose contains table rows; Python prints the computed and reported tables.',
            'Delete the table rows and refer to the computed or reported table by name.', [('', text) for text in tables])
    if directions:
        add('comparison-direction', 'minor', 'A sentence describes a change in the opposite direction to its own figures.',
            'Correct the direction word to match the figures, or the figures to match the sources.',
            [('', text) for text in directions])
    if frequencies:
        add('uncited-frequency', 'minor', 'A frequency or base rate is stated without a citation.',
            'Remove the frequency claim or cite the source that contains it.', [('', text) for text in frequencies])
    if meta:
        add('meta-reference', 'minor', 'The prose mentions Python, prompts or instructions.',
            'Rewrite to refer to the computed summary, exhibits or reported tables by name.', [('', text) for text in meta])
    if causes:
        add('gap-cause-wording', 'minor', 'The prose assigns the difference between value and price a cause; Python '
            'states which inputs alone can and cannot close it.',
            'Remove the attribution or replace it with a reference to the computed account in the summary.',
            [('', text) for text in causes])
    if additive:
        add('convention-rows-added', 'minor', 'Convention or one-at-a-time rows are described as adding up or combining.',
            'State each row separately; rows are alternatives and are never added together.', [('', text) for text in additive])
    dollars = any(isinstance(item, dict) and str(item.get('currency') or '').upper() == 'USD' for item in computed)
    for identifier, issue, fix, items in style_findings(body, dollars):
        add(identifier, 'minor', issue, fix, [('', text) for text in items])
    words = prose_words(memo)
    if words > PROSE_WORD_LIMIT:
        add('length', 'minor', f'The prose is {words} words; the budget is about 3,000.',
            'Cut repetition and secondary detail, keeping every section.', [])
    return findings


# Thresholds for the counted style checks, calibrated on the three live memos of
# 2026-11-27 (Apple, Microsoft, Coca-Cola): their prose has 8, 2 and 5 ", not "
# contrasts and 5, 1 and 6 sentences that narrate an exhibit. A draft at or under
# the allowance passes; a factual "not" or a single pointer to an exhibit is normal.
ANTITHESIS_ALLOWANCE = 2
NARRATION_ALLOWANCE = 2
_ANTITHESIS = re.compile(r',\s+not\s+(?!only\b|just\b)', re.I)
_NARRATION = re.compile(
    r'\b(?:the\s+)?(?:computed\s+)?(?:summary|exhibit|table|panel|diagnostics?|rows?|output)\s+(?:above\s+|below\s+)?'
    r'(?:places?|puts?|shows?|gives?|states?|reports?|records?|lists?|sets?|computes?|explains?)\b'
    r'|\baccording to the (?:computed|summary|exhibit|table)', re.I)
# Seven or more digits, with or without thousands separators; citations and links excluded.
_LONG_NUMBER = re.compile(r'(?<![\w,.])\d{1,3}(?:,\d{3}){2,}(?![\d,])|(?<![\w,.])\d{7,}(?![\d,])')


def _sentences(text):
    clean = re.sub(r'\[[^\]\n]*\]|https?://\S+', '', text)
    return [part.strip() for part in re.split(r'(?<=[.!?])\s+|\n+', clean) if part.strip() and not part.startswith('#')]


from .memo_style import dollar_amounts, usd_amount_count  # noqa: E402  (shared with the renderer)


def style_counts(text, dollars=True):
    """Raw counts of the style rules in a passage, for comparing an edit's replacement with its quote."""
    clean = re.sub(r'\[[^\]\n]*\]|https?://\S+', '', text or '')
    return {'currency-code': usd_amount_count(clean) if dollars else 0,
            'long-number': len(_LONG_NUMBER.findall(clean)), 'em-dash': clean.count('\u2014'),
            'contrast-formula': len(_ANTITHESIS.findall(clean)), 'exhibit-narration': len(_NARRATION.findall(clean))}


COUNTED_RULES = {'contrast-formula': ANTITHESIS_ALLOWANCE, 'exhibit-narration': NARRATION_ALLOWANCE}


def style_added(quote, replacement, dollars=True, memo=None):
    """The style rules a replacement breaks more often than the text it replaces.

    "USD", long numbers and em dashes count on any increase. A contrast or an exhibit
    narration counts only when the edited ``memo`` would then exceed the allowance the
    free check uses, so an edit is held to the same standard as the draft.
    """
    before, after = style_counts(quote, dollars), style_counts(replacement, dollars)
    added = [rule for rule, count in after.items() if count > before[rule]]
    if memo is not None:
        total = style_counts(memo, dollars)
        added = [rule for rule in added if rule not in COUNTED_RULES or
                 total[rule] - before[rule] + after[rule] > COUNTED_RULES[rule]]
    return added


def style_findings(prose, dollars=True):
    """(id, issue, fix, sentences) for each counted style rule the prose breaks."""
    sentences = _sentences(prose)
    found = []
    usd = [line[:300] for line in sentences if usd_amount_count(line)] if dollars else []
    if usd:
        found.append(('currency-code', f'{len(usd)} sentences write "USD" for a dollar filer; the first page names the '
                      'currency once.', 'Write $139 a share, $34.3B or $850M; keep quoted prices as quoted.', usd))
    long = [line[:300] for line in sentences if _LONG_NUMBER.search(line)]
    if long:
        found.append(('long-number', f'{len(long)} sentences print a number of seven or more digits.',
                      'Write the amount as $34.3B or $850M, or a share count as 14.6B or 297M shares.', long))
    dashes = [line[:300] for line in sentences if '\u2014' in line]
    if dashes:
        found.append(('em-dash', f'{len(dashes)} sentences use an em dash.',
                      'Use a full stop, a comma or a plain relative clause.', dashes))
    contrast = [line[:300] for line in sentences if _ANTITHESIS.search(line)]
    if len(contrast) > ANTITHESIS_ALLOWANCE:
        found.append(('contrast-formula', f'{len(contrast)} sentences use the ", not" contrast formula.',
                      'State the positive claim once and drop the contrast.', contrast))
    narration = [line[:300] for line in sentences if _NARRATION.search(line)]
    if len(narration) > NARRATION_ALLOWANCE:
        found.append(('exhibit-narration', f'{len(narration)} sentences narrate an exhibit instead of stating the figure.',
                      'State the figure as the house or computed value, for example "the base-case value is $139".',
                      narration))
    return found


def assessment_findings(review):
    """Findings implied by the reviewer's own assessment of the conclusion.

    Only a review that states neither answer gets a finding, a minor one. Until
    2026-11-15 an answer short of "yes" on the central weakness or the gap to
    price became a major finding; the answers are now recorded for the reader
    (commit 8864a45, see below).
    """
    weakness = review.get('central_weakness') if isinstance(review.get('central_weakness'), dict) else None
    explained = review.get('gap_explained')
    if weakness is None and explained is None:
        return [_finding('review-assessment-missing', 'minor',
                         'The reviewer did not state the central weakness of the conclusion and whether the memo '
                         'explains the difference between value and price in the required form.',
                         'Request a review that states both.')]
    # Both answers are recorded for the reader and no longer become a major
    # finding: Python states which inputs can close the gap, and every
    # assessment major in the 2026-11-14/15 runs was refuted or induced by
    # the attribution wording (investigation, 2026-11-15).
    return []


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


def _decimals(text):
    return len(text.split('.')[1]) if '.' in text else 0


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
            found.append((key, float(match.group(1).replace(',', '')), _decimals(match.group(1))))
    for match in re.finditer(r'<!--\s*valuation:([a-z_]+)\s*=\s*' + number + r'\s*-->', memo, re.I):
        found.append((match.group(1).lower(), float(match.group(2).replace(',', '')), _decimals(match.group(2))))
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
                found.append((key, float(value.group(1).replace(',', '')), _decimals(value.group(1))))
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
        # The memo structure names these "Risks, including evidence that the
        # framework is wrong" and "Observations that would change the value".
        'disconfirming-evidence': (r'disconfirm|counter.?thesis|what would change|would change the value|framework.{0,40}wrong|falsif|why we might be wrong|alternative explanation|kill criteria|contrary', 'minor'),
        'catalysts': (r'catalyst|monitor|watch|milestone|signpost|observations? that would change', 'minor'),
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
            # Clause by clause: a live draft (2026-11-15) wrote "a quote observed at the
            # 2026-11-13 close; the valuation is dated 2026-11-15" and the valuation date
            # was read as a quote date.
            for clause in re.split(r'(?<=[.;])\s+', paragraph['text']):
                if (re.search(r'\b(?:quote|market price|closing price|share price|stock price)\b', clause, re.I)
                        and not re.search(r'\bvaluation\b', clause, re.I)):
                    dates.update(re.findall(r'\b(?:as of|on|observed(?: at)?|dated)\s+(?:the\s+)?(\d{4}-\d{2}-\d{2})\b',
                                            clause, re.I))
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
    for key, value, decimals in seen:
        # A value matches at the precision it is displayed: $139 for 138.86, 41.45 for 41.4519.
        tolerance = max(.00500001, 0.5 * 10 ** -decimals + 1e-9, abs(expected.get(key, 0)) * 1e-6)
        if key not in expected:
            unmatched.add(key)
        elif not math.isfinite(value) or abs(value - expected[key]) > tolerance:
            mismatches.add(key)
    if mismatches:
        findings.append(_finding('valuation-value-mismatch', 'major', 'Explicit per-share values in the memo differ from the executable model: ' + ', '.join(sorted(mismatches)) + '.', 'Regenerate every displayed valuation figure from the current model outputs.'))
    if unmatched and not expected:
        findings.append(_finding('valuation-model-missing', 'major', 'The memo states explicit per-share values without comparable executable model outputs.', 'Supply and recompute a supported valuation model, or remove unsupported targets.'))
    return findings

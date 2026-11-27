"""The six-section outline with "## In brief" (readability review, 2026-11-27) and its free checks.

Offline throughout: the checks are pure functions, and the two pipeline runs use the fake
transport of test_stab_loop on the archived Apple example. No key, no network, no paid call.
"""
import json
import re
import tempfile
import unittest

from investmentideas import pipeline, quality
from investmentideas.quality import (amount_format_problems, house_vocabulary, in_brief_problems, inspect_memo,
                                     parse_review, prereview_checks, prose_outline, repeated_figures,
                                     structure_problems, trace_pools)

from test_stab_loop import LoopTransport, major, run_loop, stages


def words(count, start=''):
    """Filler prose of ``count`` words that states no figure."""
    return (start + ' ' + ' '.join(['Demand held'] * (count // 2))).strip() + '.'


BRIEF = ('Example Corp sells industrial software and earned a 30% operating margin last year [S1]. Our value is below '
         'the price because our forecast keeps that margin while the price needs it to reach 41% for a decade. A '
         'quarter of margin above 35% would change the value most.')
SIX = '\n'.join([
    '## In brief', '', BRIEF, '',
    '## 1. Conclusion and the gap to price', '', words(120, 'Margins decide the gap.'), '',
    '## 2. Business economics', '', words(120, 'Returns come from licences.'), '',
    '## 3. Forecast and scenarios', '', words(120, 'The base case starts from reported results.'), '',
    '## 4. Capital allocation and claims on value', '', words(120, 'Buybacks were small.'), '',
    '## 5. What would change the value', '', words(120, 'Margin is the observation that matters.'), '',
    '## 6. Evidence gaps', '', '- Fourth-quarter results are unfiled.', '- Segment margins are undisclosed.', ''])
EIGHT = '\n'.join(['## 1. Valuation conclusion', '', words(120), '', '## 2. Variant view', '', words(120), '',
                   '## 3. Business economics, industry and competition', '', words(120), '',
                   '## 4. Management, capital allocation and financial history', '', words(120), '',
                   '## 5. Forecast and scenarios', '', words(120), '',
                   '## 6. Observations that would change the value', '', words(120), '',
                   '## 7. Risks, including evidence that the valuation framework is wrong', '', words(120), '',
                   '## 8. Evidence gaps and unresolved questions', '', words(40), ''])


class OutlineTests(unittest.TestCase):
    def test_the_outline_is_read_from_the_headings(self):
        self.assertEqual(prose_outline(SIX), 'six')
        self.assertEqual(prose_outline(SIX.replace('## In brief', '## Preface')), 'six')  # numbered 1 to 6
        self.assertEqual(prose_outline(EIGHT), 'legacy')
        self.assertEqual(prose_outline('## Executive view\n\nText.\n## Risks\n\nText.\n'), 'legacy')
        self.assertEqual(quality.word_limit(SIX), (quality.PROSE_WORD_LIMIT, quality.PROSE_WORD_BUDGET))
        self.assertEqual(quality.word_limit(EIGHT), (quality.LEGACY_PROSE_WORD_LIMIT, quality.LEGACY_PROSE_WORD_BUDGET))

    def test_the_sections_prompt_names_the_new_headings_in_order(self):
        headings = re.findall(r'"(## [^"]+)"', pipeline.SECTIONS)
        self.assertEqual(headings, ['## In brief', '## 1. Conclusion and the gap to price', '## 2. Business economics',
                                    '## 3. Forecast and scenarios', '## 4. Capital allocation and claims on value',
                                    '## 5. What would change the value', '## 6. Evidence gaps'])
        for heading in headings:
            self.assertEqual(prose_outline(SIX.replace(heading, heading)), 'six')
        self.assertIn(quality.PROSE_WORD_BUDGET, pipeline.MEMO_WORDS)

    def test_both_outlines_satisfy_the_heading_checks(self):
        sources = [{'id': 'S1', 'title': 'Filing', 'kind': 'filing', 'data': {'excerpt': 'Revenue 1'}}]
        for body in (SIX, EIGHT):
            ids = {row['id'] for row in inspect_memo('# T\n\n' + body.replace('Demand held.', 'Demand held [S1].'), sources)}
            self.assertFalse({identifier for identifier in ids if identifier.startswith('section-')}, body[:40])


class StructureTests(unittest.TestCase):
    def test_a_complete_body_has_no_structural_problem(self):
        self.assertEqual(structure_problems(SIX), [])
        self.assertEqual(structure_problems(EIGHT), [])
        self.assertEqual(in_brief_problems(SIX), [])

    def test_the_walmart_stub_is_caught(self):
        # Section 1 of the approved Walmart memo (2026-11-27): one sentence, then five blank lines.
        stub = EIGHT.replace(words(120) + '\n\n## 2.', 'The value sits below the price because the margin is thin. '
                             'The conclusion rests on reported margins of 4.5% and 4.2% [S2].\n\n\n\n\n\n## 2.', 1)
        problems = structure_problems(stub)
        self.assertEqual(len(problems), 2, problems)
        self.assertIn('the section needs at least 50', problems[0])
        self.assertIn('5 blank lines', problems[1])

    def test_empty_and_unfinished_sections_are_caught(self):
        empty = SIX.replace('- Fourth-quarter results are unfiled.\n- Segment margins are undisclosed.', '')
        self.assertEqual(structure_problems(empty), ['"6. Evidence gaps" is empty.'])
        unfinished = SIX.replace(words(120, 'Buybacks were small.'), words(120, 'Buybacks were small.')[:-1] + ' [S2], ')
        self.assertTrue(any('stops mid-sentence' in problem for problem in structure_problems(unfinished)))

    def test_a_short_gaps_list_passes(self):
        self.assertEqual(structure_problems(SIX.replace('- Segment margins are undisclosed.', '')), [])


class InBriefTests(unittest.TestCase):
    def test_a_missing_in_brief_is_raised_only_for_the_new_outline(self):
        without = SIX.split('## 1.', 1)[1]
        self.assertEqual(in_brief_problems('## 1.' + without), ['The prose has no "## In brief" section.'])
        self.assertEqual(in_brief_problems(EIGHT), [])

    def test_length_and_sentence_count(self):
        long = SIX.replace(BRIEF, BRIEF + ' ' + words(60, 'More text follows here.'))
        problems = in_brief_problems(long)
        self.assertTrue(any('the limit is 80' in problem for problem in problems))
        self.assertTrue(any('5 sentences' in problem for problem in problems))
        two = SIX.replace(BRIEF, 'Walmart sells groceries in the U.S. and abroad and earned $29.8B [S2]. Our value is '
                                 'below the price because the margin is thin.')
        self.assertEqual(in_brief_problems(two), ['"In brief" has 2 sentences; it needs 3.'])

    def test_it_must_come_first(self):
        moved = SIX.replace('## In brief\n\n' + BRIEF + '\n\n', '').replace('## 6. Evidence gaps',
                                                                             '## In brief\n\n' + BRIEF + '\n\n## 6. Evidence gaps')
        self.assertIn('"In brief" is not the first section.', in_brief_problems(moved))


class RepetitionTests(unittest.TestCase):
    def test_a_figure_in_three_sections_is_flagged_and_the_in_brief_is_not_counted(self):
        body = SIX.replace('Margins decide the gap.', 'The price needs 14.8% growth.')
        body = body.replace('Returns come from licences.', 'Growth of 14.8% is rare.')
        self.assertEqual(repeated_figures(body), [])  # sections 1 and 2 only
        self.assertEqual(repeated_figures(body.replace('A quarter of margin', 'Growth of 14.8% or a quarter of margin')), [])
        body = body.replace('Buybacks were small.', 'The 14.8% figure returns.')
        self.assertEqual(repeated_figures(body), [('14.8%', ['1', '2', '4'])])

    def test_the_same_amount_in_another_unit_is_one_figure_only_when_written_alike(self):
        body = SIX.replace('Margins decide the gap.', 'Debt is $802.1B.').replace(
            'Returns come from licences.', 'Commitments of $802.1B.').replace('Buybacks were small.', 'Still $802.1B.')
        self.assertEqual(repeated_figures(body), [('$802.1B', ['1', '2', '4'])])


class AmountFormatTests(unittest.TestCase):
    def test_reader_hostile_amounts_are_flagged(self):
        for sentence in ('Operating cash flow was $11,599M in 2023.', 'Market value reached $3,946B.',
                         'Revenue of $395.97B is the aggregate.', 'The average price was $464.42.',
                         'Shares repurchased were 85.0 million.', 'The rate is 9.83% today.', 'Bounded at +30.0 points.'):
            self.assertEqual(len(amount_format_problems(sentence)), 1, sentence)

    def test_plain_amounts_pass(self):
        for sentence in ('Operating cash flow was $11.6B in 2023.', 'The margin was 32.0% in 2025.', 'Value is $42.9 a share.',
                         'Value / price is 0.81.', 'Sales-to-capital rises from 0.60 to 1.03.', 'Leases total $850M.',
                         'The price was $87.40 [S1].'):
            self.assertEqual(amount_format_problems(sentence), [], sentence)


class VocabularyTests(unittest.TestCase):
    def test_house_vocabulary_has_an_allowance_and_reads_only_the_new_outline(self):
        two = 'The house value is lower. The bridge is dated.'
        self.assertEqual(house_vocabulary(two), [])
        self.assertEqual(len(house_vocabulary(two + ' A convention row moves it. The stub is short.')), 4)
        jargon = ' The house value is lower. The bridge is dated. A convention row moves it.'
        sources = [{'id': 'S1', 'title': 'Filing', 'kind': 'filing', 'data': {'excerpt': 'Revenue 1'}}]
        new = [row['id'] for row in prereview_checks(SIX.replace('Margins decide the gap.', 'Margins decide.' + jargon), sources)]
        old = [row['id'] for row in prereview_checks(EIGHT.replace('Demand held.', 'Demand held.' + jargon, 1), sources)]
        self.assertIn('house-vocabulary', new)
        self.assertNotIn('house-vocabulary', old)


class PrereviewSeverityTests(unittest.TestCase):
    SOURCES = [{'id': 'S1', 'title': 'Filing', 'kind': 'filing', 'data': {'excerpt': 'Revenue 1'}}]

    def test_only_the_empty_section_check_blocks(self):
        body = SIX.replace('- Fourth-quarter results are unfiled.\n- Segment margins are undisclosed.', '')
        body = body.replace(BRIEF, 'Too short.').replace('Margins decide the gap.', 'It was $11,599M and 14.8%.')
        body = body.replace('Returns come from licences.', '14.8% again.').replace('Buybacks were small.', '14.8% thrice.')
        rows = {row['id']: row['severity'] for row in prereview_checks(body, self.SOURCES)}
        self.assertEqual(rows['section-empty'], 'blocker')
        for identifier in ('in-brief', 'figure-repeated', 'amount-format'):
            self.assertEqual(rows[identifier], 'minor', identifier)

    def test_an_old_body_is_not_faulted_for_its_outline_or_its_old_length_budget(self):
        rows = {row['id'] for row in prereview_checks(EIGHT, self.SOURCES)}
        self.assertFalse(rows & {'section-empty', 'in-brief', 'length', 'house-vocabulary'})
        long = EIGHT.replace(words(120), words(400))  # about 3,000 words: inside the old budget only
        self.assertGreater(quality.prose_words(long), quality.PROSE_WORD_LIMIT)
        self.assertNotIn('length', {row['id'] for row in prereview_checks(long, self.SOURCES)})
        six_long = SIX.replace(words(120, 'Buybacks were small.'), words(2700, 'Buybacks were small.'))
        self.assertIn('length', {row['id'] for row in prereview_checks(six_long, self.SOURCES)})


class RubricTests(unittest.TestCase):
    def test_contradiction_and_structure_rules(self):
        self.assertEqual(quality.RULE_SEVERITY['M7'], 'major')
        self.assertEqual(quality.RULE_SEVERITY['B4'], 'blocker')
        self.assertIn('a method note or another section', quality.RUBRIC)
        memo = 'The 34.8% three-year compound rate is the closest-to-price path [S2].'
        review = parse_review(json.dumps({'verdict': 'revise', 'findings': [dict(
            major(memo, rule='M7'), severity='blocker', source_refs=['S2'])]}), memo=memo)
        self.assertEqual(review['findings'][0]['severity'], 'major')  # capped at the rule's class
        self.assertIn('M7', pipeline.FIGURE_RULES)
        self.assertIn('section-empty', pipeline.FIGURE_CHECK_IDS)


class EditGuardTests(unittest.TestCase):
    POOLS = trace_pools([{'id': 'S1', 'data': {'operating_cash_flow': 11599e6}}])

    def apply(self, quote, replacement, rule='N2', memo=None):
        memo = memo or ('## 4. Capital allocation and claims on value\n\nOperating cash flow was $11,599M in 2023 [S1].\n'
                        '\nDividends rose.\n')
        return pipeline._apply_finding_edits(memo, [{'finding': 'F1', 'quote': quote, 'replacement': replacement}],
                                             {'F1': major('x', rule=rule)}, self.POOLS, True, {})

    def test_a_restyled_amount_is_the_same_figure(self):
        memo, records = self.apply('Operating cash flow was $11,599M in 2023 [S1].',
                                   'Operating cash flow was $11.6B in 2023 [S1].')
        self.assertTrue(records[0]['applied'], records)
        self.assertIn('$11.6B', memo)
        memo, records = self.apply('Operating cash flow was $11,599M in 2023 [S1].',
                                   'Operating cash flow was $12.6B in 2023 [S1].')
        self.assertFalse(records[0]['applied'])

    def test_both_outlines_keep_their_headings_and_a_deletion_leaves_no_blank_run(self):
        for heading in ('## 4. Capital allocation and claims on value', '## 4. Management, capital allocation and '
                                                                        'financial history', '## In brief'):
            memo = heading + '\n\nOperating cash flow was $11,599M in 2023 [S1].\n\nDividends rose.\n\nBuybacks fell.\n'
            out, records = self.apply('Dividends rose.', '', memo=memo)
            self.assertTrue(records[0]['applied'])
            self.assertNotIn('\n\n\n', out)
            self.assertTrue(out.startswith(heading))
            _, records = self.apply(heading + '\n\n', '', memo=memo)
            self.assertIn('removes the heading', records[0]['reason'])

    def test_an_empty_section_is_answered_with_its_figures(self):
        memo = '## 6. Evidence gaps\n\n## Valuation detail\n'
        out, records = pipeline._apply_finding_edits(
            memo, [{'finding': 'section-empty', 'quote': '## 6. Evidence gaps',
                    'replacement': '## 6. Evidence gaps\n\n- Cash flow of $11.6B in 2023 is not split by segment [S1].'}],
            {'section-empty': {'id': 'section-empty', 'severity': 'blocker'}}, self.POOLS, True, {})
        self.assertTrue(records[0]['applied'], records)


def six_section_memo(memo):
    """The archived example's prose rearranged under the In brief and the six numbered headings."""
    parts = re.split(r'(?m)^## .*$', memo)[1:]
    bodies = [part.strip() for part in parts if part.strip()]
    headings = ['## 1. Conclusion and the gap to price', '## 2. Business economics', '## 3. Forecast and scenarios',
                '## 4. Capital allocation and claims on value', '## 5. What would change the value']
    out = ['## In brief', '', 'Apple sells devices and services and earns most of its profit on the iPhone [S4]. Our '
           'value is below the price because our forecast discounts the cash flows at a higher rate than the price '
           'needs. Products margin recovery would change the value most.', '']
    for index, heading in enumerate(headings):
        out += [heading, '', bodies[2 * index], '', bodies[2 * index + 1], '']
    out += ['## 6. Evidence gaps', '', '- Fourth-quarter results are unfiled [S4].',
            '- Services margins by product are undisclosed [S4].', '']
    return '\n'.join(out)


class SixSectionTransport(LoopTransport):
    """Drafts under the new outline; the edit call answers each finding with the edit given for it."""

    def __init__(self, *, draft, answers, **kwargs):
        super().__init__(**kwargs)
        self.memo = draft
        self.excerpt = next(p.strip() for p in self.memo.split('\n\n')
                            if len(p.strip()) > 200 and not p.strip().startswith(('#', '|', '*')))[:150]
        self.answers = answers

    def transport(self, url, *, key=None, payload=None, timeout=90):
        from test_budget_workflow import instructions
        prompt = instructions(payload) if url.endswith('/chat/completions') else ''
        if prompt.startswith('Answer the review findings'):
            self.requests.append(payload)
            return self.reply(payload, json.dumps({'edits': self.answers(self)}))
        return super().transport(url, key=key, payload=payload, timeout=timeout)


class SixSectionRunTests(unittest.TestCase):
    def test_a_draft_under_the_new_outline_is_reviewed_and_edited(self):
        from test_budget_workflow import FullContractTransport, instructions
        draft = six_section_memo(FullContractTransport().memo)

        def answers(fixture):
            # An M7 contradiction answered by rewording its sentence and restyling the house label.
            return [{'finding': 'F1', 'quote': fixture.excerpt,
                     'replacement': fixture.excerpt.replace('house', 'our', 1) + ' That rate is ours.'}]
        fixture = SixSectionTransport(draft=draft, answers=answers, reviews=[['contradiction']])
        fixture.scripted = [[major(fixture.excerpt, rule='M7')]]
        with tempfile.TemporaryDirectory() as directory:
            folder, state = run_loop(directory, fixture)
            log = json.loads((folder / 'reviews' / 'edits-1.json').read_text())
            memo = (folder / 'memo.md').read_text()
        draft_prompt = next(instructions(p) for p in fixture.requests if instructions(p).startswith(pipeline.WRITER_OPENING))
        review_prompt = next(instructions(p) for p in fixture.requests if instructions(p).startswith('Review this equity memo'))
        self.assertIn('"## In brief": three sentences', draft_prompt)
        self.assertIn('a contradiction is major under M7', review_prompt)
        self.assertIn('(9) that no section of the prose is empty', review_prompt)
        sequence = stages(state)
        self.assertEqual(sequence[sequence.index('review_0'):],
                         ['review_0', 'finding_check_0', 'targeted_research_1', 'prose_edits_1', 'review_1'])
        self.assertEqual([(record['finding'], record['applied']) for record in log['edits']], [('F1', True)])
        self.assertNotIn('section-empty', log['free_checks_after'])
        self.assertTrue(state['complete'])
        self.assertIn('## In brief', memo)
        self.assertIn('That rate is ours.', state['current_memo'])
        self.assertEqual(prose_outline(state['current_memo']), 'six')

    def test_an_empty_section_blocks_until_an_edit_writes_it(self):
        from test_budget_workflow import FullContractTransport
        draft = six_section_memo(FullContractTransport().memo).replace(
            '- Fourth-quarter results are unfiled [S4].\n- Services margins by product are undisclosed [S4].\n', '')

        def answers(fixture):
            return [{'finding': 'section-empty', 'quote': '## 6. Evidence gaps',
                     'replacement': '## 6. Evidence gaps\n\n- Fourth-quarter results are unfiled [S4].\n'
                                    '- Services margins by product are undisclosed [S4].'}]
        fixture = SixSectionTransport(draft=draft, answers=answers, reviews=[[]])
        with tempfile.TemporaryDirectory() as directory:
            folder, state = run_loop(directory, fixture)
            first = json.loads((folder / 'reviews' / 'review-0.json').read_text())
            log = json.loads((folder / 'reviews' / 'edits-1.json').read_text())
        blocker = next(f for f in first['findings'] if f['id'] == 'section-empty')
        self.assertEqual(blocker['severity'], 'blocker')
        self.assertEqual(first['verdict'], 'revise')
        self.assertIn('draft_check_fix_0', stages(state))
        self.assertEqual([(record['finding'], record['applied']) for record in log['edits']], [('section-empty', True)])
        self.assertTrue(state['complete'])
        self.assertIn('Services margins by product are undisclosed', state['current_memo'])


if __name__ == '__main__':
    unittest.main()

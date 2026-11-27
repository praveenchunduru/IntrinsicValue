"""The review loop: a stable cached prefix, the forecast response only when needed, exact edits, two rounds.

Offline throughout: the archived Apple example supplies realistic evidence, plan and memo
shapes, and a fake transport answers every stage. No key, no network, no paid call.
"""
import copy
import inspect
import json
import math
from pathlib import Path
import tempfile
import unittest

from investmentideas import __main__ as cli, core, pipeline
from investmentideas.prompt_context import rounded, valuation_context
from investmentideas.quality import trace_pools, untraced_figures

from test_budget_workflow import FullContractTransport, RATES, full_text, instructions, prefix_text


def major(excerpt, *, rule='M5', identifier='F1', severity='major'):
    finding = {'id': identifier, 'severity': severity, 'issue': 'Synthetic prose defect in the quoted sentence',
               'source_refs': ['S4'], 'current_excerpt': excerpt, 'fix': 'Correct the quoted sentence'}
    if rule:
        finding['rule'] = rule
    return finding


class LoopTransport(FullContractTransport):
    """Reviews come from a script, targeted research adds a source, and the edit call answers by mode.

    edit: 'anchor' replaces the quoted text, 'miss' quotes text the memo lacks,
    'forecast' says only a forecast change can answer F1, 'fail_once' interrupts the first edit call.
    """

    def __init__(self, *, reviews, edit='anchor', unchanged=False, **kwargs):
        super().__init__(**kwargs)
        self.scripted, self.edit, self.research, self.unchanged = reviews, edit, 0, unchanged

    def reply(self, payload, content):
        prompt_tokens = math.ceil(len((core.SYSTEM + full_text(payload)).encode()) / 3.05)
        completion_tokens = math.ceil(len(content.encode()) / 4)
        pin, pout = RATES[payload['model']]
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': content, 'annotations': []}}],
                'model': payload['model'],
                'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': completion_tokens,
                          'cost': prompt_tokens * pin + completion_tokens * pout, 'prompt_tokens_details': {'cached_tokens': 0}}}

    def transport(self, url, *, key=None, payload=None, timeout=90):
        prompt = instructions(payload) if url.endswith('/chat/completions') else ''
        if prompt.startswith('Answer the review findings'):
            self.requests.append(copy.deepcopy(payload))
            if self.edit == 'fail_once':
                self.edit = 'anchor'
                raise RuntimeError('Synthetic edit interruption')
            if self.edit == 'forecast_once':
                self.edit = 'anchor'
                answer = {'edits': [], 'forecast': [{'finding': 'F1', 'reason': 'Only a lower forecast answers it'}]}
            elif self.edit == 'forecast':
                answer = {'edits': [], 'forecast': [{'finding': 'F1', 'reason': 'Only a lower forecast answers it'}]}
            elif self.edit == 'miss':
                answer = {'edits': [{'finding': 'F1', 'quote': 'Text that is not in the memo.', 'replacement': 'x'}]}
            else:
                answer = {'edits': [{'finding': 'F1', 'quote': self.excerpt,
                                     'replacement': self.excerpt + ' The dated monitoring condition is a house judgment.'}]}
            return self.reply(payload, json.dumps(answer))
        if prompt.startswith('Address these review findings') and self.unchanged:
            self.requests.append(copy.deepcopy(payload))
            return self.reply(payload, json.dumps({'unchanged': True, 'reason': 'The forecast is supported [S4].',
                                                   'finding_dispositions': {'F1': 'clarified: memo_only'}}))
        response = super().transport(url, key=key, payload=payload, timeout=timeout)
        if prompt.startswith('Review this equity memo'):
            findings = self.scripted[self.review_count - 1] if self.review_count <= len(self.scripted) else []
            findings = [major(self.excerpt) if item == 'prose' else item for item in findings]
            response['choices'][0]['message']['content'] = json.dumps(
                {'verdict': 'revise' if findings else 'pass', 'findings': findings})
        if prompt.startswith('Investigate '):
            self.research += 1
            response['choices'][0]['message']['annotations'] = [{'url_citation': {
                'url': f'https://example.invalid/targeted-{self.research}', 'title': 'Synthetic targeted source',
                'content': 'Synthetic targeted passage relevant to the finding. ' * 20}}]
        return response


def run_loop(directory, fixture, **kwargs):
    with fixture.installed():
        folder = pipeline.run('AAPL', 'synthetic-key', directory=directory, **kwargs)
    return folder, json.loads((folder / 'state.json').read_text())


def stages(state):
    return [row['stage'] for row in state['ledger']]


class StablePrefixTests(unittest.TestCase):
    def test_prefix_digest_is_identical_across_iterations_when_targeted_research_adds_sources(self):
        fixture = LoopTransport(reviews=[['prose'], ['prose']])
        with tempfile.TemporaryDirectory() as directory:
            folder, state = run_loop(directory, fixture)
            ledger = state['ledger']
        self.assertEqual(state['iteration'], 2)
        self.assertTrue(state['complete'])
        # Targeted research added two sources after the plan wrote the cached block.
        added = [s['id'] for s in state['sources'] if 'targeted' in str(s.get('url'))]
        self.assertEqual(len(added), 2)
        self.assertTrue(set(added).isdisjoint(state['cached_evidence']['source_ids']))
        claude = {row['stage']: row['cached_prefix_sha256'] for row in ledger
                  if row['model'] == pipeline.core.MODELS['balanced'][1] and 'cached_prefix_sha256' in row}
        self.assertIn('valuation_plan', claude)
        self.assertIn('prose_edits_1', claude)
        self.assertIn('prose_edits_2', claude)
        self.assertEqual(len(set(claude.values())), 1, claude)
        reviewer = {row['stage']: row['cached_prefix_sha256'] for row in ledger
                    if row['model'] == core.REVIEWERS['balanced'] and 'cached_prefix_sha256' in row}
        self.assertEqual(set(reviewer), {'review_0', 'finding_check_0', 'review_1', 'finding_check_1', 'review_2'})
        self.assertEqual(len(set(reviewer.values())), 1, reviewer)
        # The new sources travel uncached after the instructions, never inside a cached block.
        for payload in fixture.requests:
            self.assertNotIn('example.invalid/targeted', prefix_text(payload))
        last = [p for p in fixture.requests if instructions(p).startswith('Review this equity memo')][-1]
        self.assertIn(pipeline.LATER_EVIDENCE.strip(), instructions(last))
        self.assertIn('example.invalid/targeted-2', instructions(last))
        self.assertTrue(instructions(last).rstrip().endswith('DRAFT>>>'))

    def test_evidence_blocks_keep_the_cached_context_byte_identical(self):
        state = {'sources': [{'id': 'S1', 'title': 'Filing', 'kind': 'filing', 'source_tier': 'regulatory_primary',
                              'data': {'excerpt': 'Revenue 1000'}}]}
        pipeline._freeze_evidence(state)
        before, later = pipeline._evidence_blocks(state)
        self.assertEqual(later, '')
        self.assertEqual(before, pipeline._evidence_context(state['sources']))
        state['sources'].append({'id': 'S2', 'title': 'Search', 'kind': 'search_excerpt',
                                 'source_tier': 'search_excerpt_unverified', 'data': {'excerpt': 'Outside view'}})
        pipeline._freeze_evidence(state)
        after, later = pipeline._evidence_blocks(state)
        self.assertEqual(after, before)
        self.assertIn('UNVERIFIED WEB EXCERPTS', later)
        self.assertNotIn('PRIMARY AND OFFICIAL SOURCES', later)
        pipeline._freeze_evidence(state, refresh=True)
        self.assertEqual(pipeline._evidence_blocks(state), (pipeline._evidence_context(state['sources']), ''))

    def test_the_unverified_budget_continues_from_the_cached_block(self):
        excerpt = {'excerpt': 'x' * 2500}
        sources = [{'id': f'S{n}', 'title': 'Search', 'kind': 'search_excerpt', 'source_tier': 'search_excerpt_unverified',
                    'data': copy.deepcopy(excerpt)} for n in range(1, 17)]
        state = {'sources': sources[:15]}
        pipeline._freeze_evidence(state)
        state['sources'] = sources
        _, later = pipeline._evidence_blocks(state)
        # Fifteen excerpts use the 30,000-character budget; the sixteenth gets title and URL only.
        self.assertIn('Prompt budget for unverified excerpts reached', later)


class ForecastCriterionTests(unittest.TestCase):
    MEMO = '# Memo\n\n## Forecast\n\nThe house base case grows 5% a year [S1].\n'

    def test_prose_findings_under_a_rule_skip_the_forecast_response(self):
        findings = [major('The house base case grows 5% a year [S1].'),
                    major('## Forecast', rule='N3', identifier='F2', severity='minor')]
        self.assertEqual(pipeline._forecast_findings(findings, self.MEMO), [])
        self.assertEqual(pipeline._edit_required(findings, self.MEMO), ['F1'])

    def test_rule_less_rendered_and_unanchored_quotes_reopen_the_forecast(self):
        findings = [major('The house base case grows 5% a year [S1].', rule=None),
                    major('Bull case: growth stays above the base rates', identifier='F2'),
                    major('', identifier='F3', severity='blocker', rule='B1'),
                    major('The house base case grows 5% a year [S1].', identifier='F4', severity='minor', rule=None)]
        reasons = dict(pipeline._forecast_findings(findings, self.MEMO))
        self.assertEqual(set(reasons), {'F1', 'F2', 'F3'})
        self.assertIn('no quality-standard rule', reasons['F1'])
        self.assertIn('outside', reasons['F2'])
        # Text outside the prose cannot take an edit, so it never forces the rewrite.
        self.assertEqual(pipeline._edit_required(findings, self.MEMO), ['F1'])

    def test_python_checks_reopen_the_forecast_only_without_a_model(self):
        findings = [{'id': 'citation-unresolved', 'severity': 'major', 'issue': 'x', 'fix': 'y', 'source_refs': []},
                    {'id': 'valuation-model-missing', 'severity': 'major', 'issue': 'x', 'fix': 'y', 'source_refs': []}]
        ids = {'citation-unresolved', 'valuation-model-missing'}
        self.assertEqual([item[0] for item in pipeline._forecast_findings(findings, self.MEMO, ids)],
                         ['valuation-model-missing'])
        self.assertEqual(pipeline._edit_required(findings, self.MEMO, ids), ['citation-unresolved', 'valuation-model-missing'])


class EditApplicationTests(unittest.TestCase):
    MEMO = 'Revenue was USD 1,000 million [S1]. Margins held. Margins held.\nThe outlook is steady.'
    POOLS = trace_pools([{'id': 'S1', 'data': {'revenue': 1000000000}}])

    def apply(self, edits):
        return pipeline._apply_finding_edits(self.MEMO, edits, {'F1', 'F2'}, self.POOLS)

    def test_anchored_edits_apply_and_name_their_finding(self):
        memo, records = self.apply([{'finding': 'F1', 'quote': 'The outlook is steady.', 'replacement': ''},
                                    {'finding': 'F2', 'quote': 'Revenue was USD 1,000 million [S1].',
                                     'replacement': 'Revenue was USD 1,000 million in fiscal 2026 [S1].'}])
        # A dollar amount in a replacement is restyled for a dollar filer (memo number style, 2026-11-27).
        self.assertEqual(memo, 'Revenue was $1,000M in fiscal 2026 [S1]. Margins held. Margins held.\n')
        self.assertEqual([(r['finding'], r['applied']) for r in records], [('F1', True), ('F2', True)])

    def test_unanchored_unnamed_and_untraced_edits_are_rejected(self):
        memo, records = self.apply([
            {'finding': 'F1', 'quote': 'Margins held.', 'replacement': 'Margins rose.'},
            {'finding': 'F1', 'quote': 'Not in the memo.', 'replacement': 'x'},
            {'finding': 'F9', 'quote': 'The outlook is steady.', 'replacement': 'x'},
            {'finding': 'F2', 'quote': 'The outlook is steady.', 'replacement': 'Revenue will reach USD 1,234 million.'},
            {'finding': 'F2', 'quote': 'The outlook is steady.', 'replacement': 'The upside is large.'},
            {'finding': 'F1', 'quote': 7, 'replacement': 'x'}])
        self.assertEqual(memo, self.MEMO)
        reasons = [r['reason'] for r in records]
        self.assertIn('2 times', reasons[0])
        self.assertIn('0 times', reasons[1])
        self.assertIn('names no finding', reasons[2])
        self.assertIn('1,234', reasons[3])
        self.assertIn('trading-layer', reasons[4])
        self.assertEqual(reasons[5], 'malformed edit')

    def test_the_response_parser_keeps_the_last_answer_and_its_forecast_flags(self):
        text = ('{"edits":[{"finding":"F1","quote":"a","replacement":"b"}]} Wait, again: '
                '{"edits":[],"forecast":[{"finding":"F1","reason":"needs a lower forecast"},"junk"]}')
        edits, flags = pipeline._edit_response(text)
        self.assertEqual(edits, [])
        self.assertEqual(flags, [{'finding': 'F1', 'reason': 'needs a lower forecast'}])
        self.assertEqual(pipeline._edit_response('no JSON here'), (None, []))


class LoopFlowTests(unittest.TestCase):
    def test_prose_findings_take_exact_edits_and_skip_the_forecast_response(self):
        fixture = LoopTransport(reviews=[['prose']])
        with tempfile.TemporaryDirectory() as directory:
            folder, state = run_loop(directory, fixture)
            skipped = json.loads((folder / 'reviews' / 'model-response-1.json').read_text())
            log = json.loads((folder / 'reviews' / 'edits-1.json').read_text())
        sequence = stages(state)
        self.assertEqual(sequence[sequence.index('review_0'):],
                         ['review_0', 'finding_check_0', 'targeted_research_1', 'prose_edits_1', 'review_1'])
        self.assertTrue(skipped['skipped'])
        self.assertEqual(skipped['findings'], ['F1'])
        self.assertEqual(log['outcome'], 'edited')
        self.assertEqual([(r['finding'], r['applied']) for r in log['edits']], [('F1', True)])
        self.assertIn('The dated monitoring condition is a house judgment.', state['current_memo'])
        self.assertEqual(state['revision_rounds'][-1]['forecast_response'], 'skipped')
        self.assertEqual(state['revision_rounds'][-1]['revision'], 'edits')
        self.assertTrue(state['complete'])

    def test_an_edit_that_does_not_anchor_falls_back_to_the_rewrite(self):
        fixture = LoopTransport(reviews=[['prose']], edit='miss')
        with tempfile.TemporaryDirectory() as directory:
            folder, state = run_loop(directory, fixture)
            log = json.loads((folder / 'reviews' / 'edits-1.json').read_text())
        sequence = stages(state)
        self.assertNotIn('model_response_1', sequence)
        self.assertLess(sequence.index('prose_edits_1'), sequence.index('revise_1'))
        self.assertEqual(log['unanswered'], ['F1'])
        self.assertIn('no edit applied for F1', state['revision_rounds'][-1]['rewrite_reason'])
        rewrite = next(p for p in fixture.requests if instructions(p).startswith('Revise the complete memo'))
        self.assertIn('No forecast response was requested', instructions(rewrite))

    def test_a_forecast_flag_from_the_edit_call_reopens_the_forecast(self):
        fixture = LoopTransport(reviews=[['prose']], edit='forecast')
        with tempfile.TemporaryDirectory() as directory:
            _, state = run_loop(directory, fixture)
        sequence = stages(state)
        self.assertEqual(sequence[sequence.index('targeted_research_1'):sequence.index('review_1')],
                         ['targeted_research_1', 'prose_edits_1', 'model_response_1', 'revise_1'])

    def test_a_forecast_that_stands_sends_the_edits_again_with_its_dispositions(self):
        fixture = LoopTransport(reviews=[['prose']], edit='forecast_once', unchanged=True)
        with tempfile.TemporaryDirectory() as directory:
            _, state = run_loop(directory, fixture)
        sequence = stages(state)
        self.assertEqual(sequence[sequence.index('targeted_research_1'):],
                         ['targeted_research_1', 'prose_edits_1', 'model_response_1', 'prose_edits_1', 'review_1'])
        second = [p for p in fixture.requests if instructions(p).startswith('Answer the review findings')][-1]
        self.assertIn('Forecast response to these findings', instructions(second))
        self.assertEqual(state['revision_rounds'][-1]['forecast_response'], 'unchanged')
        self.assertEqual(state['revision_rounds'][-1]['revision'], 'edits')

    def test_a_rule_less_finding_runs_the_forecast_response_first(self):
        fixture = LoopTransport(reviews=[[major('placeholder', rule=None)]])
        fixture.scripted = [[dict(major(fixture.excerpt, rule=None))]]
        with tempfile.TemporaryDirectory() as directory:
            _, state = run_loop(directory, fixture)
        sequence = stages(state)
        self.assertIn('model_response_1', sequence)
        self.assertNotIn('prose_edits_1', sequence)  # the fixture's proposal recomputes the plan: a rewrite
        self.assertEqual(state['revision_rounds'][-1]['rewrite_reason'], 'the plan changed')

    def test_the_loop_stops_after_two_rounds_with_findings_open(self):
        fixture = LoopTransport(reviews=[['prose'], ['prose'], ['prose']])
        with tempfile.TemporaryDirectory() as directory:
            _, state = run_loop(directory, fixture)
        self.assertEqual(state['iteration'], 2)
        self.assertEqual(state['quality_status'], 'unresolved_findings')
        self.assertFalse(state['complete'])
        self.assertNotIn('targeted_research_3', stages(state))

    def test_an_interrupted_edit_round_resumes_without_a_new_review(self):
        fixture = LoopTransport(reviews=[['prose']], edit='fail_once')
        with tempfile.TemporaryDirectory() as directory:
            with fixture.installed(), self.assertRaisesRegex(RuntimeError, 'edit interruption'):
                pipeline.run('AAPL', 'synthetic-key', directory=directory)
            folder = next(Path(directory).glob('AAPL/full-*'))
            pending = json.loads((folder / 'state.json').read_text())['pending_revision']
            self.assertEqual(pending['mode'], 'edits')
            before = len(json.loads((folder / 'state.json').read_text())['ledger'])
            _, state = run_loop(directory, fixture)
        self.assertEqual(stages(state)[before:], ['prose_edits_1', 'review_1'])
        self.assertTrue(state['complete'])


class StoredRunTests(unittest.TestCase):
    def test_defaults_are_two_rounds(self):
        self.assertEqual(pipeline.DEFAULT_MAX_REVISIONS, 2)
        self.assertEqual(inspect.signature(pipeline.run).parameters['max_revisions'].default, 2)
        self.assertIn("'--max-revisions',type=int,default=2", inspect.getsource(cli))

    def test_a_run_stopped_by_the_old_default_continues_with_the_new_one(self):
        fixture = LoopTransport(reviews=[['prose'], ['prose']])
        with tempfile.TemporaryDirectory() as directory:
            _, state = run_loop(directory, fixture, max_revisions=1)
            self.assertEqual(state['quality_status'], 'unresolved_findings')
            self.assertEqual(state['iteration'], 1)
            count = len(state['ledger'])
            _, state = run_loop(directory, fixture)
        self.assertEqual(stages(state)[count:], ['targeted_research_2', 'prose_edits_2', 'review_2'])
        self.assertTrue(state['complete'])

    def test_a_completed_run_under_the_old_default_is_returned_unchanged(self):
        fixture = LoopTransport(reviews=[['prose']])
        with tempfile.TemporaryDirectory() as directory:
            _, state = run_loop(directory, fixture, max_revisions=1)
            self.assertTrue(state['complete'])
            requests = len(fixture.requests)
            _, again = run_loop(directory, fixture)
        self.assertEqual(len(fixture.requests), requests)
        self.assertEqual(again['ledger'], state['ledger'])

    def test_a_saved_pending_revision_without_a_mode_resumes_as_the_rewrite(self):
        class Interrupted(LoopTransport):
            stop = True
            def transport(self, url, *, key=None, payload=None, timeout=90):
                if (self.stop and url.endswith('/chat/completions') and
                        instructions(payload).startswith('Revise the complete memo')):
                    raise RuntimeError('Synthetic rewrite interruption')
                return super().transport(url, key=key, payload=payload, timeout=timeout)
        fixture = Interrupted(reviews=[['prose']], edit='miss')
        with tempfile.TemporaryDirectory() as directory:
            with fixture.installed(), self.assertRaisesRegex(RuntimeError, 'rewrite interruption'):
                pipeline.run('AAPL', 'synthetic-key', directory=directory)
            fixture.stop = False
            folder = next(Path(directory).glob('AAPL/full-*'))
            saved = json.loads((folder / 'state.json').read_text())
            self.assertEqual(saved['pending_revision']['mode'], 'rewrite')
            # The shape a run saved before this change left behind.
            for key in ('mode', 'edit_required'):
                saved['pending_revision'].pop(key)
            (folder / 'state.json').write_text(json.dumps(saved))
            _, state = run_loop(directory, fixture)
        self.assertEqual(stages(state)[len(saved['ledger']):], ['revise_1', 'review_1'])


class BudgetTests(unittest.TestCase):
    def test_preflight_prices_two_revision_rounds(self):
        fixture = LoopTransport(reviews=[])
        with tempfile.TemporaryDirectory() as directory:
            folder, _ = run_loop(directory, fixture)
            preflight = json.loads((folder / 'budget-preflight.json').read_text())
        self.assertEqual(preflight['max_revisions'], 2)
        self.assertEqual([item['round'] for item in preflight['revision_rounds']], [1, 2])
        usual = [row['stage'] for row in preflight['revision_rounds'][0]['usual_stages']]
        self.assertEqual(usual, ['targeted_research_1', 'prose_edits_1', 'draft_check_fix_1', 'review_1', 'finding_check_1'])
        self.assertEqual([row['stage'] for row in preflight['revision_rounds'][0]['conditional_stages']],
                         ['model_response_1', 'revise_1'])
        self.assertGreater(preflight['largest_single_reservation_floor_usd'], 0)

    def test_the_default_allowance_covers_two_rounds_on_the_costliest_path(self):
        # Each round: the edit call flags a forecast change, the forecast is corrected and the memo rewritten.
        fixture = LoopTransport(reviews=[['prose'], ['prose']], edit='forecast')
        with tempfile.TemporaryDirectory() as directory:
            _, state = run_loop(directory, fixture)
        self.assertTrue(state['complete'])
        for stage in ('model_response_1', 'revise_1', 'model_response_2', 'revise_2', 'review_2'):
            self.assertIn(stage, stages(state))
        spent, peak = 0, 0
        for row in state['ledger']:
            peak = max(peak, spent + row['reserved_usd'])
            spent += row['charged_or_reserved_usd']
        self.assertLessEqual(peak, pipeline.DEFAULT_BUDGET, f'peak reservation {peak:.2f}')


class RoundingTests(unittest.TestCase):
    def test_rounded_keeps_types_and_six_significant_figures(self):
        value = {'a': 15775815885.096397, 'b': 0.08741803052779258, 'c': [True, 3, None, 'x', 170794000000.0],
                 'd': -1.23456789e-7}
        self.assertEqual(rounded(value), {'a': 15775800000, 'b': 0.087418, 'c': [True, 3, None, 'x', 170794000000],
                                          'd': -1.23457e-07})

    def test_the_presentation_rounds_and_the_saved_artifacts_do_not(self):
        fixture = LoopTransport(reviews=[])
        with tempfile.TemporaryDirectory() as directory:
            folder, state = run_loop(directory, fixture)
            saved = json.loads((folder / 'valuation.json').read_text())
        def floats(value):
            if isinstance(value, float):
                yield value
            elif isinstance(value, dict):
                for item in value.values():
                    yield from floats(item)
            elif isinstance(value, list):
                for item in value:
                    yield from floats(item)
        self.assertTrue(any(float(f'{x:.6g}') != x for x in floats(saved)))
        draft = next(p for p in fixture.requests if instructions(p).startswith(pipeline.WRITER_OPENING))
        shown = json.loads(instructions(draft).split('\nValuation:\n', 1)[1].split('\nGaps:\n', 1)[0])
        self.assertTrue(all(float(f'{x:.6g}') == x for x in floats(shown)))
        self.assertIn('6 significant figures', shown['context_metadata']['number_precision'])
        self.assertEqual(valuation_context(state['plan'], state['valuation']),
                         pipeline.render_valuation_context(state['plan'], state['valuation']))

    def test_a_figure_copied_from_the_rounded_context_still_traces(self):
        # 0.12344996 shows as 0.12345; written as 12.35% it misses the original by more than display rounding.
        result = {'growth': 0.12344996}
        plain = trace_pools([], (result,))
        both = trace_pools([], (result,) + pipeline._rounded_inputs({}, result))
        self.assertEqual(untraced_figures('Growth of 12.35% a year.', plain), ['12.35%'])
        self.assertEqual(untraced_figures('Growth of 12.35% a year.', both), [])
        self.assertEqual(untraced_figures('Growth of 12.34% a year.', both), [])


if __name__ == '__main__':
    unittest.main()

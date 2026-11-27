"""The memo layout after the readability review (2026-11-27).

Page one says why value differs from price beside the index ratio and the exit
multiple; Appendix A is one model with one sensitivity table and one table of what
would change the value; house-wide method notes go to method-notes.md beside the
memo; sources are the cited ones; the review is a two-line footer.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest

from investmentideas import pipeline
from investmentideas.valuation import evaluate

from tests.public_runner.test_stab_memo import PROSE, SOURCES, headline_plan, rendered, saved_runs

BRIEF = ('## In brief\n\nThe value sits below the price because reinvestment is heavy [S1]. Margin is where the base '
         'case and the market part. A filed quarter of faster growth would move it.\n\n')


def notes_document(value=None, memo=PROSE, state=None):
    value = value or headline_plan()
    result = evaluate(value)
    state = state or {'ticker': 'EXMP', 'quality_status': 'reviewed_draft', 'complete': True, 'review_history': []}
    return pipeline.method_notes_document(memo, SOURCES, [], value, result, state), result


class FirstPageTests(unittest.TestCase):
    def test_in_brief_sits_under_the_headline_and_leaves_the_prose(self):
        memo, _, _ = rendered(memo=PROSE.replace('## 1. Valuation conclusion', BRIEF + '## 1. Valuation conclusion'))
        page = memo[memo.index('## Summary'):memo.index('## 1. Valuation conclusion')]
        headline = page.index('The base-case value is')
        brief = page.index('The value sits below the price because reinvestment is heavy')
        table = page.index('| Scenario |')
        self.assertLess(headline, brief)
        self.assertLess(brief, table)
        self.assertNotIn('## In brief', memo)
        self.assertEqual(memo.count('reinvestment is heavy'), 1)

    def test_without_in_brief_the_page_renders_from_the_headline(self):
        memo, _, _ = rendered()
        page = memo[memo.index('## Summary'):memo.index('## 1. Valuation conclusion')]
        self.assertNotIn('In brief', page)
        self.assertRegex(page, r'The base-case value is \$[\d.,]+ a share')

    def test_the_check_text_keeps_in_brief_where_the_checks_read_it(self):
        value = headline_plan()
        result = evaluate(value)
        memo = PROSE.replace('## 1. Valuation conclusion', BRIEF + '## 1. Valuation conclusion')
        checked = pipeline._with_summary(memo, value, result, SOURCES, 'EXMP')
        self.assertIn('## In brief', checked)

    def test_index_ratio_beside_every_scenario_ratio(self):
        memo, _, result = rendered()
        index = result['index_benchmark']['value_to_price']
        base = result['scenarios']['base']['value_to_price']
        self.assertIn(f'The same method values the S&P 500 at {index:.2f} of its price', memo)
        self.assertIn(f'| Relative to the S&P 500 at {index:.2f} |', memo)
        self.assertIn(f'at {base / index:.2f} times the index ratio', memo)
        self.assertNotIn('(stylized)', memo.split('## 1.')[0])

    def test_exit_multiple_is_computed_from_the_run_or_left_out(self):
        memo, _, result = rendered()
        row = result['computed_diagnostics']['valuation_multiples']['scenarios']['base']
        entry = row['forecast_year1']['market_ev_to_ebit']['value']
        exit_ = row['original_dcf_exit']['ev_to_final_forecast_ebit']['value']
        self.assertIn(f'{entry:.1f} times base-case year-1 EBIT', memo)
        self.assertIn(f'exits at {exit_:.1f} times year-10 EBIT', memo)
        bare = copy.deepcopy(result)
        bare['computed_diagnostics'].pop('valuation_multiples')
        self.assertIsNone(pipeline._exit_multiple_line(bare))

    def test_price_needs_cells_are_plain(self):
        memo, _, _ = rendered()
        page = memo.split('## 1. Valuation conclusion')[0]
        for phrase in ('house-equivalent', 'not reached within the range tested', 'up to 300%', 'convention row',
                       'US dollars; $B is billions'):
            self.assertNotIn(phrase, page)
        self.assertIn('| Input | Base case | Needed for value = price', page)

    def test_dated_tests_come_from_the_monitoring_tests(self):
        value = headline_plan()
        result = evaluate(value)
        result['monitoring'] = [{'name': 'late', 'review_by': '2027-09-30', 'threshold': 'Margin below 20%',
                                 'conditional_per_share_value': 8.0, 'base_per_share_value': 10.0},
                                {'name': 'early', 'review_by': '2027-02-28', 'threshold': 'Growth below 5%',
                                 'conditional_per_share_value': 9.0, 'base_per_share_value': 10.0}]
        page = pipeline.computed_summary(value, result)
        self.assertLess(page.index('2027-02-28'), page.index('2027-09-30'))
        self.assertIn('| 2027-02-28 | Growth below 5% | 9.00 | -1.00 |', page)
        result['monitoring'] = []
        self.assertNotIn('Dated tests', pipeline.computed_summary(value, result))


class AppendixTests(unittest.TestCase):
    def test_appendix_a_replaces_the_valuation_detail(self):
        memo, _, _ = rendered()
        self.assertIn('## Appendix A: the model', memo)
        self.assertIn('## Appendix B: reported history', memo) if '### Annual results' in memo else None
        self.assertNotIn('## Valuation detail', memo)
        for gone in ('### Forecast return on invested capital', '### Discount rate\n', '### Other horizons',
                     '### Rate regimes', '### Country risk', '### Beta cross-checks', '### Counterfactual cases',
                     '### Monitoring tests', '### Value under other conventions', '### What the price implies'):
            self.assertNotIn(gone, memo)
        self.assertEqual(memo.count('### Sensitivity'), 1)
        self.assertIn('Discount rate: ', memo)

    def test_one_sensitivity_table_keeps_every_owner_row(self):
        value = headline_plan()
        result = evaluate(value)
        lines, _ = pipeline._sensitivity_parts(result, pipeline._style(value, result), value)
        text = '\n'.join(lines)
        rows = [line for line in lines if line.startswith('| ') and not line.startswith('| Change |')]
        self.assertLessEqual(len(rows), 14)
        names = {row['name'] for row in result['convention_panel']['rows'] if row.get('status') == 'calculated'}
        if any(name.startswith('competitive_advantage_period_') for name in names):
            self.assertIn('Competitive advantage period of 5 / 15 / 20 years', text)
        if 'country_risk_premium_revenue_weighted' in names:
            self.assertIn('Country risk premium', text)
        if any(name.startswith('treasury_rate_') for name in names):
            self.assertIn('Treasury rate, points:', text)

    def test_cases_table_merges_monitoring_and_counterfactuals(self):
        value = headline_plan()
        result = evaluate(value)
        result['monitoring'] = [{'name': 'growth_check', 'review_by': '2027-02-28', 'threshold': 'Growth below 5%',
                                 'counterfactual': 'slow_growth', 'conditional_per_share_value': 9.0,
                                 'base_per_share_value': 10.0}]
        result['counterfactuals'] = {'slow_growth': {'per_share_value': 9.0, 'terminal_value_fraction': .6},
                                     'other_case': {'per_share_value': 11.0, 'terminal_value_fraction': .7}}
        lines, _ = pipeline._cases_parts(result, pipeline._style(value, result), pipeline._dated_tests(result))
        text = '\n'.join(lines)
        self.assertEqual(text.count('Slow growth'), 1)
        self.assertIn('| Other case | none |', text)
        self.assertIn('60.0%', text)

    def test_stale_bridge_shows_the_elapsed_period_as_rows(self):
        value = headline_plan()
        result = evaluate(value)
        timing = result['discounting'].setdefault('timing', {})
        timing.update(convention='stub_v1', anchor_date='2025-12-31', valuation_date='2026-11-27', elapsed_days=331,
                      stub_years=331 / 365)
        style = pipeline._style(value, result)
        rows = pipeline._stale_bridge_rows(value, result, style, SOURCES, 1e9, 1)
        self.assertEqual(rows[0][:2], ['Balance-sheet date', '2025-12-31'])
        self.assertIn('331 days before the valuation date', rows[0][2])
        self.assertTrue(any(row[0] == 'Free cash flow since then, left out' for row in rows))
        timing.update(elapsed_days=40)
        self.assertEqual(pipeline._stale_bridge_rows(value, result, style, SOURCES, 1e9, 1), [])

    def test_later_balances_sum_the_bridge_concepts_on_one_date(self):
        plan = {'net_debt_bridge': {'debt_components': {
            'current': {'value': 2e9, 'facts': [{'concept': 'us-gaap:LongTermDebtCurrent', 'instant': '2025-12-31'}]},
            'noncurrent': {'value': 46e9, 'facts': [{'concept': 'us-gaap:LongTermDebtNoncurrent', 'instant': '2025-12-31'}]}}}}
        table = [{'concept': 'us-gaap:LongTermDebtCurrent', 'instant': '2026-09-30', 'status': 'reported', 'value': 3e9},
                 {'concept': 'us-gaap:LongTermDebtNoncurrent', 'instant': '2026-09-30', 'status': 'reported', 'value': 120e9},
                 {'concept': 'us-gaap:LongTermDebtNoncurrent', 'instant': '2026-06-30', 'status': 'reported', 'value': 90e9},
                 {'concept': 'us-gaap:CommonStockSharesOutstanding', 'instant': '2026-09-30', 'status': 'reported', 'value': 105e6}]
        sources = [{'id': 'S2', 'data': {'financial_context_table': table}}]
        found = pipeline._later_balances(plan, sources, '2025-12-31', '2026-11-27', shares=100e6)
        self.assertIn(('Debt', 123e9, '2026-09-30', 48e9, 'S2'), found)
        self.assertIn(('shares', 105e6, '2026-09-30', 100e6, 'S2'), found)
        # A change under 1% is left out.
        table[-1]['value'] = 100.2e6
        self.assertFalse(any(item[0] == 'shares' for item in
                             pipeline._later_balances(plan, sources, '2025-12-31', '2026-11-27', shares=100e6)))

    def test_change_in_shares_row_is_dropped_from_history(self):
        notes = []
        lines = pipeline._history_table(['revenue', 'change in shares outstanding (millions)'],
                                        (['2025-12-31'], [['x', '10.0', '-5.0']]), pipeline.memo_style.Style('USD'), notes)
        self.assertFalse(any('Change in shares' in line for line in lines))


class NotesTests(unittest.TestCase):
    def test_memo_keeps_specific_notes_and_the_file_keeps_the_rest(self):
        value = headline_plan()
        result = evaluate(value)
        state = {'ticker': 'EXMP', 'quality_status': 'reviewed_draft', 'complete': True, 'review_history': []}
        _, groups = pipeline._appendix_parts(value, result, SOURCES)
        _, history_notes = pipeline._reported_parts(SOURCES)
        everything = [' '.join(str(note).split()) for _, notes in groups + [('Reported history', history_notes)]
                      for note in notes if not isinstance(note, pipeline._Table)]
        memo = pipeline._render(PROSE, SOURCES, [], value, result, state)
        document = pipeline.method_notes_document(PROSE, SOURCES, [], value, result, state)
        combined = ' '.join((memo + '\n' + document).split())
        for note in everything:
            self.assertIn(pipeline._without_concepts(note), combined)
        self.assertIn('[method-notes.md](method-notes.md)', memo)
        self.assertNotIn('## Method notes', memo)
        self.assertIn('## Method notes', document)

    def test_specific_notes_stay_in_the_memo(self):
        groups = [('Valuation basis', [pipeline._Specific('Listed stakes trade at $34.3B.'), 'A house rule.']),
                  ('Reported history', ['Trailing twelve months to 2026-09-30: revenue $1B.', 'Column basis note.'])]
        specific, house = pipeline._split_notes(groups)
        self.assertEqual(specific, ['Listed stakes trade at $34.3B.', 'Trailing twelve months to 2026-09-30: revenue $1B.'])
        self.assertEqual(house, [('Valuation basis', ['A house rule.']), ('Reported history', ['Column basis note.'])])

    def test_no_note_describes_an_absent_row(self):
        document, _ = notes_document()
        memo, _, _ = rendered()
        for text in (document, memo):
            self.assertNotIn('does not apply', text)
            self.assertNotIn('has no row of its own', text)

    def test_concept_names_leave_the_memo_notes(self):
        self.assertEqual(pipeline._without_concepts('Leases of $5B (us-gaap:Foo on msft:Bar (one each)) [S4].'),
                         'Leases of $5B [S4].')


class SourcesAndReviewTests(unittest.TestCase):
    def test_sources_list_only_cited_and_the_file_lists_all(self):
        memo, _, _ = rendered()
        listed = re.findall(r'^- \[(S\d+)', memo.split('## Sources')[1], re.M)
        cited = pipeline._cited_ids(memo.split('## Sources')[0])
        self.assertEqual(set(listed), cited & {source['id'] for source in SOURCES})
        document, _ = notes_document()
        self.assertEqual(len(re.findall(r'^- \[S\d+', document, re.M)), len(SOURCES))

    def test_review_footer_is_two_lines_and_the_record_moves(self):
        state = {'ticker': 'EXMP', 'quality_status': 'reviewed_draft', 'complete': True,
                 'review_history': [{'iteration': 0, 'findings': [{'id': 'F1', 'severity': 'minor', 'issue': 'Wording.'}],
                                     'review_model': 'a/reviewer', 'author_model': 'b/writer'}]}
        value = headline_plan()
        result = evaluate(value)
        memo = pipeline._render(PROSE, SOURCES, ['Some cost lines are missing. Run in a new --output folder to collect them.'],
                                value, result, state)
        footer = memo.split('## Review\n', 1)[1].strip().split('\n')
        self.assertEqual(len(footer), 2)
        self.assertIn('Review status: reviewed draft. A second model reviewed this memo; one minor finding remains open.',
                      footer[0])
        self.assertNotIn('a/reviewer', memo)
        self.assertNotIn('--output', memo)
        self.assertIn('Evidence not retrieved: Some cost lines are missing.', memo)
        document = pipeline.method_notes_document(PROSE, SOURCES, ['Some cost lines are missing.'], value, result, state)
        self.assertIn('Review model: a/reviewer', document)
        self.assertIn('- minor: Wording.', document)


class ProseAndFilesTests(unittest.TestCase):
    def test_deleted_paragraphs_leave_one_blank_line(self):
        prose = '## 1. Valuation conclusion\n\nOne sentence [S1].\n\n\n\n\n\n## 2. Variant view\n\nMore [S1].\n'
        self.assertIn('One sentence [S1].\n\n## 2. Variant view', pipeline._strip_model_frame(prose))

    def test_write_memo_writes_both_files_and_a_placeholder_removes_the_notes(self):
        value = headline_plan()
        result = evaluate(value)
        state = {'ticker': 'EXMP', 'quality_status': 'reviewed_draft', 'complete': True, 'review_history': []}
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            report = pipeline._write_memo(folder, PROSE, SOURCES, [], value, result, state)
            self.assertEqual((folder / 'memo.md').read_text(), report)
            self.assertTrue((folder / pipeline.METHOD_NOTES_FILE).is_file())
            self.assertIn(pipeline.METHOD_NOTES_FILE, pipeline._DELIVERED_FILES)
            pipeline._write_placeholder(folder, '# EXMP: in progress\n')
            self.assertFalse((folder / pipeline.METHOD_NOTES_FILE).exists())


class SavedRunLayoutTests(unittest.TestCase):
    """Saved runs named in INTRINSICVALUE_SAVED_RUNS re-render in the new layout with nothing lost."""

    def test_saved_runs_rerender_with_their_notes(self):
        runs = saved_runs()
        if not runs:
            self.skipTest('INTRINSICVALUE_SAVED_RUNS names no saved run')
        spec = importlib.util.spec_from_file_location('rerender', Path(__file__).resolve().parents[2] / 'tools' / 'rerender.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        for folder in runs:
            with self.subTest(run=Path(folder).name):
                memo, document = tool.rerender(folder), tool.method_notes(folder)
                self.assertIn('## Appendix A: the model', memo)
                self.assertNotIn('\n\n\n\n', memo)
                notes = memo.split('\n## Notes\n', 1)[1].split('\n## Sources', 1)[0]
                self.assertLess(len(notes.split()), 250)
                self.assertNotIn('does not apply', memo + document)
                self.assertIn('## Run record', document)


if __name__ == '__main__':
    unittest.main()

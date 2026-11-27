"""The reader's memo layout and number style (memo redesign, 2026-11-27).

The owner found the live memos hard to read: "USD" 94-137 times, 12-digit table
cells, a first page of machine sentences and Python's own "X, not Y" contrasts.
These tests pin the new layout, the number style, the writer's standard and the
free style checks that send violations to the existing fix call.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import re
import tempfile
import unittest

from investmentideas import memo_style, pipeline, quality
from investmentideas.valuation import evaluate

from tests.public_runner.test_market_consistency import plan as glide_plan

LONG_NUMBER = re.compile(r'(?<![\w,.])\d{1,3}(?:,\d{3}){2,}(?![\d,])|(?<![\w,.])\d{7,}(?![\d,])')
SOURCES = [{'id': 'S1', 'kind': 'sec_facts', 'title': 'SEC company facts: EXAMPLE HOLDINGS CORP', 'source_tier': 'regulatory_primary',
            'url': 'https://example.test/facts', 'retrieved_at': '2026-03-31T00:00:00Z', 'data': {'revenue': 1000e9}},
           {'id': 'S2', 'kind': 'macro_calibration', 'title': 'Market beta table', 'url': 'https://example.test/beta',
            'retrieved_at': '2026-03-31T00:00:00Z', 'data': {}}]
PROSE = ('# EXAMPLE: Investment-Committee Research Memo\n\nThe memo is dated 2026-03-31. All figures are in USD.\n\n'
         '## 1. Valuation conclusion\n\nThe house value is below the price [S1].\n\n'
         '## 2. Variant view\n\nRevenue grew in the last year [S1].\n')


def headline_plan():
    """A dollar filer in the billions with the base case as the headline."""
    value = glide_plan()
    for name, weight in (('bear', 0.0), ('base', 1.0), ('bull', 0.0)):
        value['scenarios'][name]['probability'] = weight
    value['actuals'].update(revenue=1000e9, net_debt=50e9, shares_outstanding=100e9)
    value['net_debt_bridge'].update(debt=100e9, selected_cash_and_nonoperating_investments=50e9, net_debt=50e9)
    return value


def rendered(value=None, memo=PROSE):
    value = value or headline_plan()
    result = evaluate(value)
    state = {'ticker': 'EXMP', 'quality_status': 'reviewed_draft', 'complete': True, 'review_history': []}
    return pipeline._render(memo, SOURCES, [], value, result, state), value, result


class NumberStyleTests(unittest.TestCase):
    def test_per_share_values_prices_and_aggregates(self):
        style = memo_style.Style('USD')
        self.assertEqual(style.per_share(138.86), '$139')
        self.assertEqual(style.per_share(41.44), '$41.4')
        self.assertEqual(style.per_share(8.123), '$8.12')
        self.assertEqual(style.per_share(1234.7), '$1,235')
        self.assertEqual(style.price(355.58), '$355.58')
        self.assertEqual(style.amount(34.3e9), '$34.3B')
        self.assertEqual(style.amount(850e6), '$850M')
        self.assertEqual(style.amount(1970.7e9), '$1,971B')
        self.assertEqual(style.amount(-1.5e9), '-$1.5B')
        self.assertEqual(style.amount(0), '$0')
        self.assertEqual(style.header(), 'US dollars; $B is billions and $M is millions.')

    def test_changes_follow_the_precision_of_the_value_they_change(self):
        self.assertEqual(memo_style.bare_change(66.96, 205.8), '+67')
        self.assertEqual(memo_style.bare_change(-4.48, 134.4), '-4')
        self.assertEqual(memo_style.bare_change(5.71, 41.4), '+5.7')
        self.assertEqual(memo_style.bare_change(0.001, 41.4), '0')

    def test_percentages_and_zero_cells(self):
        self.assertEqual(memo_style.pct(0.10647), '10.6%')
        self.assertEqual(memo_style.pct(-0.0003), '0.0%')
        self.assertEqual(memo_style.pct(0.10647, 2), '10.65%')
        self.assertEqual(memo_style.scaled(0.0, 1e9), '0')
        self.assertEqual(memo_style.table_scale([525.6e9]), ('B', 1e9, 1))
        self.assertEqual(memo_style.table_scale([5.2e9]), ('M', 1e6, 0))
        self.assertEqual(memo_style.table_scale([50.0]), ('', 1, 0))

    def test_other_currencies_use_a_symbol_or_the_code_once(self):
        self.assertEqual(memo_style.Style('EUR').amount(34.3e9), '€34.3B')
        self.assertEqual(memo_style.Style('CHF').per_share(139.2), 'CHF 139')
        self.assertEqual(memo_style.Style('CHF').unit('B'), 'CHF B')
        self.assertEqual(memo_style.Style('CHF').header(), 'Amounts are in Swiss francs (CHF); B is billions and M is millions.')


class LayoutTests(unittest.TestCase):
    def test_sections_come_in_the_reader_order(self):
        memo, _, _ = rendered()
        headings = [line for line in memo.split('\n') if re.match(r'#{1,2} ', line)]
        self.assertEqual(headings[0], '# Example Holdings Corp. (EXMP): intrinsic value, 2026-03-31')
        order = ['## Summary', '## 1. Valuation conclusion', '## 2. Variant view', '## Valuation detail',
                 '## Reported history', '## Method notes', '## Sources', '## Review status']
        present = [heading for heading in headings if heading in order]
        self.assertEqual(present, [heading for heading in order if heading in present])
        for heading in ('## Summary', '## Valuation detail', '## Method notes', '## Sources', '## Review status'):
            self.assertIn(heading, present)
        self.assertNotIn('Investment-Committee', memo)
        self.assertNotIn('computed by Python', memo)
        # The model's own title and preamble go; its sections stay as written.
        self.assertNotIn('All figures are in USD', memo)
        self.assertIn('The house value is below the price [S1].', memo)

    def test_the_first_page_is_short_and_states_the_values_once(self):
        value = headline_plan()
        result = evaluate(value)
        page = pipeline.computed_summary(value, result)
        style = memo_style.Style('USD')
        base = result['scenarios']['base']['per_share_value']
        sentence = next(line for line in page.split('\n') if line.startswith('The base-case value is'))
        self.assertIn(style.per_share(base), sentence)
        self.assertIn(style.price(value['market_price']), sentence)
        self.assertIn(f'{result["scenarios"]["base"]["value_to_price"]:.2f}', sentence)
        for name in ('bear', 'bull'):
            self.assertIn(style.per_share(result['scenarios'][name]['per_share_value']), sentence)
        prose = [line for line in page.split('\n') if line and not line.startswith('|')]
        self.assertLessEqual(len(' '.join(prose).split()), 90)
        self.assertIn('**What the price needs.**', page)
        self.assertIn('**Conventions that move the value most.**', page)
        table = page[page.index('**Conventions that move the value most.**'):]
        self.assertEqual(len([line for line in table.split('\n') if line.startswith('| ') and 'Convention changed' not in line]), 3)
        self.assertNotIn('USD', page)
        self.assertNotRegex(page, LONG_NUMBER)
        # Method caveats live in the method notes.
        for caveat in ('stress cases without probabilities', 'never added together'):
            self.assertNotIn(caveat, page)

    def test_python_text_has_no_currency_codes_long_numbers_or_contrast_formulas(self):
        memo, _, _ = rendered()
        start, end = memo.index('\n## 1.'), memo.index('\n## Valuation detail')
        computed = memo[:start] + memo[end:memo.index('\n## Sources')]
        self.assertNotIn('USD', computed)
        self.assertNotRegex(computed, LONG_NUMBER)
        self.assertNotIn('—', computed)
        self.assertNotRegex(computed, r',\s+not\b')
        self.assertIn('Tables and the first page are computed by code', computed)
        self.assertIn('The prose is written by one model and reviewed by another.', computed)

    def test_tables_carry_units_in_headers(self):
        memo, _, _ = rendered()
        self.assertIn('| Year | Revenue ($B) | Growth |', memo)
        self.assertIn('| Line | $B | Basis |', memo)
        self.assertIn('| Value per share ($) |', memo)

    def test_a_level_one_numbered_section_is_kept(self):
        value = headline_plan()
        result = evaluate(value)
        out = pipeline._with_summary('# 1. Valuation conclusion\nText [S1].\n', value, result)
        self.assertIn('# 1. Valuation conclusion', out)
        self.assertIn('Text [S1].', out)

    def test_the_rerender_tool_rebuilds_a_saved_run(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('rerender', Path(__file__).resolve().parents[2] / 'tools' / 'rerender.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        value = headline_plan()
        state = {'ticker': 'EXMP', 'current_memo': PROSE, 'sources': SOURCES, 'gaps': [], 'plan': value,
                 'valuation': evaluate(value), 'quality_status': 'reviewed_draft', 'review_history': []}
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'state.json').write_text(json.dumps(state), encoding='utf-8')
            out = Path(folder) / 'out.md'
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(tool.main([folder, str(out)]), 0)
            text = out.read_text(encoding='utf-8')
        self.assertTrue(text.startswith('# Example Holdings Corp. (EXMP): intrinsic value'))
        self.assertIn('## 2. Variant view', text)


class WriterStandardTests(unittest.TestCase):
    def test_the_standard_reaches_writer_and_reviewer(self):
        for rule in ('Open each section with its conclusion', '"X, not Y"', 'summary', 'do not narrate the exhibit',
                     '$34.3B or $850M', 'seven or more digits', 'em dashes'):
            self.assertIn(rule, quality.WRITING_STANDARD)
        self.assertIn(quality.WRITING_STANDARD, quality.RUBRIC)
        self.assertIn('## 1. Valuation conclusion', pipeline.SECTIONS)
        self.assertIn('Each section opens with its conclusion', pipeline.SECTIONS)
        # The evidence-tier and attribution rules stay.
        self.assertIn('M1', quality.RUBRIC)
        self.assertIn('A house or computed figure that the prose names as such is attributed', quality.RUBRIC)


class StyleCheckTests(unittest.TestCase):
    def ids(self, prose, dollars=True):
        return [item[0] for item in quality.style_findings(prose, dollars)]

    def test_each_rule_is_flagged(self):
        self.assertEqual(self.ids('The value is USD 139 a share [S1].'), ['currency-code'])
        self.assertEqual(self.ids('The value is USD 139 a share.', dollars=False), [])
        self.assertEqual(self.ids('Revenue was 525,628,090,000 in year 1.'), ['long-number'])
        self.assertEqual(self.ids('Shares fell to 14532300000.'), ['long-number'])
        self.assertEqual(self.ids('Margins rose — sharply.'), ['em-dash'])

    def test_counted_rules_allow_a_small_number(self):
        two = 'It changes the rate, not growth. It holds cash, not debt.'
        self.assertEqual(self.ids(two), [])
        self.assertEqual(self.ids(two + ' It is a convention, not a forecast.'), ['contrast-formula'])
        narration = ('The computed summary places it at 0.39. The summary shows 0.29. The exhibit gives 0.47.')
        self.assertEqual(self.ids(narration), ['exhibit-narration'])
        self.assertEqual(self.ids('The base-case value is $139. The bear value is $103.'), [])

    def test_clean_prose_passes_and_citations_and_years_are_not_numbers(self):
        prose = ('## 1. Valuation conclusion\n\nThe base-case value is $139 a share, 0.39 of the price. Revenue reached '
                 '$478.8B in fiscal 2026 [S2]. The filing is at https://www.sec.gov/Archives/edgar/data/320193/0000320193 [S4].')
        self.assertEqual(self.ids(prose), [])

    def test_prereview_checks_send_style_findings_to_the_fix_call(self):
        value = headline_plan()
        result = evaluate(value)
        memo = pipeline._with_summary(PROSE.replace('The house value is below the price',
                                                    'The house value is USD 24.6 — below the price'), value, result)
        found = [item['id'] for item in quality.prereview_checks(memo, SOURCES, (result, value))]
        self.assertIn('currency-code', found)
        self.assertIn('em-dash', found)
        # The model's preamble is not rendered, so it is not checked.
        clean = pipeline._with_summary(PROSE, value, result)
        self.assertNotIn('currency-code', [item['id'] for item in quality.prereview_checks(clean, SOURCES, (result, value))])


class TraceTests(unittest.TestCase):
    def test_rounded_renderings_trace_and_invented_figures_do_not(self):
        value = headline_plan()
        result = evaluate(value)
        pools = quality.trace_pools(SOURCES, (result, value))
        style = memo_style.Style('USD')
        base = result['scenarios']['base']
        shown = [style.per_share(base['per_share_value']), style.amount(base['enterprise_value'])]
        billions = float('%.3g' % (base['enterprise_value'] / 1e9))
        three = '$' + format(billions, ',.0f') + 'B'
        self.assertEqual(quality.untraced_figures('Value is ' + ' and '.join(shown) + ' here.', pools), [])
        self.assertEqual(quality.untraced_figures(f'Enterprise value is {three}.', pools), [])
        self.assertEqual(quality.untraced_figures('Enterprise value is $98,765B.', pools), ['$98,765B'])

    def test_the_first_page_traces(self):
        value = headline_plan()
        result = evaluate(value)
        pools = quality.trace_pools(SOURCES, (result, value))
        page = pipeline.computed_summary(value, result)
        self.assertEqual(quality.untraced_figures(page, pools) or [], [])

    def test_displayed_values_match_at_their_precision(self):
        value = headline_plan()
        result = evaluate(value)
        memo, _, _ = rendered(value)
        valuation_ids = {'valuation-value-mismatch', 'valuation-model-missing'}
        self.assertFalse(valuation_ids & {item['id'] for item in quality.inspect_memo(memo, SOURCES, result)})
        base = result['scenarios']['base']['per_share_value']
        wrong = copy.copy(memo).replace(f'| Base | {memo_style.bare_per_share(base)} |',
                                        f'| Base | {memo_style.bare_per_share(base + 3)} |', 1)
        self.assertIn('valuation-value-mismatch', [item['id'] for item in quality.inspect_memo(wrong, SOURCES, result)])

    def test_prose_words_count_only_the_writer(self):
        memo, _, _ = rendered()
        self.assertLess(quality.prose_words(memo), 40)
        self.assertNotIn('Valuation detail', quality.writer_prose(memo))
        self.assertNotIn('What the price needs', quality.writer_prose(memo))


if __name__ == '__main__':
    unittest.main()


class ReviewerViewTests(unittest.TestCase):
    """The reviewer, the finding check and the reader see one layout (2026-11-27)."""

    def test_the_reader_view_is_the_delivered_memo_up_to_the_sources(self):
        memo, value, result = rendered()
        view = pipeline.reader_view(PROSE, SOURCES, value, result, 'EXMP')
        self.assertEqual(memo.split('\n## Sources')[0].rstrip(), view.rstrip())
        self.assertEqual(view.count('## Method notes'), 1)
        self.assertNotIn('### Notes on the', view)

    def test_the_loop_builds_the_reviewer_copy_with_the_reader_view(self):
        import inspect
        source = inspect.getsource(pipeline._run)
        self.assertIn("visible=reader_view(memo,state['sources'],plan,result,ticker,state)", source)
        self.assertNotIn('valuation_section(plan,result)+', source.split('visible=')[1].split('\n')[0])

    def test_prompts_carry_the_writing_standard(self):
        import inspect
        source = inspect.getsource(pipeline._run)
        self.assertIn('WRITER_OPENING', source)
        self.assertNotIn('investment-committee', source)
        self.assertNotIn('Attribute model', source)
        self.assertIn("EVIDENCE_TIER_RULE+'\\n'+WRITING_STANDARD", source)
        self.assertIn('writing standard', pipeline.EDIT_INSTRUCTIONS)


class EditStyleTests(unittest.TestCase):
    def test_dollar_amounts_are_restyled_with_their_figures(self):
        self.assertEqual(quality.dollar_amounts('capex was USD 115.9 billion and USD 850 million, at USD 12.40'),
                         'capex was $115.9B and $850M, at $12.40')

    def test_a_replacement_that_breaks_the_standard_is_rejected(self):
        memo = 'Revenue grew 10% [S1]. Margins held.'
        pools = quality.trace_pools([], ({'revenue': 525628090000},))
        for replacement, rule in (('Margins held — barely.', 'em-dash'),
                                  ('Margins held at 525,628,090,000.', 'long-number')):
            _, records = pipeline._apply_finding_edits(memo, [{'finding': 'F1', 'quote': 'Margins held.',
                                                               'replacement': replacement}], {'F1'}, pools)
            self.assertFalse(records[0]['applied'])
            self.assertIn(rule, records[0]['reason'])
        edited, records = pipeline._apply_finding_edits(memo, [{'finding': 'F1', 'quote': 'Margins held.',
                                                                'replacement': 'Margins held, not fell.'}], {'F1'}, pools)
        self.assertTrue(records[0]['applied'])  # one contrast is inside the allowance
        heavy = 'It is a rate, not growth. It is cash, not debt. ' + memo
        _, records = pipeline._apply_finding_edits(heavy, [{'finding': 'F1', 'quote': 'Margins held.',
                                                            'replacement': 'Margins held, not fell.'}], {'F1'}, pools)
        self.assertIn('contrast-formula', records[0]['reason'])

    def test_the_free_check_fix_keeps_the_standard_too(self):
        memo = 'Revenue grew 10% [S1]. Margins held.'
        out, applied, skipped = pipeline._apply_edits(memo, [{'quote': 'Margins held.', 'replacement': 'Margins — held.'},
                                                             {'quote': 'Revenue grew 10%', 'replacement': 'Revenue of USD 4.2 billion grew 10%'}])
        self.assertEqual(applied, 1)
        self.assertIn('$4.2B', out)
        self.assertIn('em-dash', skipped[0])


class BridgeTests(unittest.TestCase):
    def test_lines_with_nothing_reported_are_one_note(self):
        memo, _, _ = rendered()
        bridge = memo[memo.index('### From enterprise value'):memo.index('### Forecast, base case')]
        self.assertNotRegex(bridge, r'\| 0 \|')
        notes = memo[memo.index('## Method notes'):]
        self.assertIn('Bridge lines with nothing to add or deduct:', notes)


class ModelTextRestyleTests(unittest.TestCase):
    """Model-written labels Python prints in computed sections follow the number style (smoke run, 2026-11-27).

    A live review raised a seven-digit share count in the repurchases table twice; no edit
    round could change it because Python prints the plan's label.
    """

    def test_long_numbers_and_usd_in_labels_are_restyled(self):
        style = memo_style.Style('USD')
        self.assertEqual(memo_style.compact('2,735,053 shares at an average USD 87.40', style), '2.74M shares at an average $87.40')
        self.assertEqual(memo_style.compact('$85,800,000,000 spent; 14532300000 shares', style), '$85.8B spent; 14.5B shares')
        self.assertEqual(memo_style.compact('on 2026-11-27, 1,970.7 [S1234567]', style), 'on 2026-11-27, 1,970.7 [S1234567]')

    def test_a_repurchase_label_prints_restyled_and_still_traces(self):
        value = headline_plan()
        result = evaluate(value)
        label = '10-Q total of 2,735,053 shares at an average USD 87.40'
        result.setdefault('computed_diagnostics', {})['repurchase_tests'] = [{
            'label': label, 'interpretation': 'historical_retrospective_conditional', 'source_refs': ['S1'],
            'amount': 239e6, 'price': 87.40, 'shares_repurchased': 2735053, 'post_repurchase_weighted_per_share_value': 24.0,
            'weighted_value_change': {'dollar_delta': -0.1}}]
        value['repurchase_tests'] = [{'label': label}]
        view = pipeline.reader_view(PROSE, SOURCES, value, result, 'EXMP')
        self.assertIn('10-Q total of 2.74M shares at an average $87.40 [S1]', view)
        pools = quality.trace_pools(SOURCES, (result, value))
        self.assertEqual(quality.untraced_figures('The label says 2.74M shares.', pools), [])

    def test_style_rules_cover_the_prose_sections_only(self):
        self.assertIn('for the eight prose sections only', quality.WRITING_STANDARD)
        value = headline_plan()
        result = evaluate(value)
        view = pipeline.reader_view(PROSE.replace('below the price', 'below the price — by USD 4'), SOURCES, value, result, 'EXMP')
        # A computed section with a long number or "USD" raises nothing; the prose does.
        planted = view.replace('## Method notes', '## Method notes\n\n- Planted USD 1,234,567 — note, not prose.')
        ids = {item['id'] for item in quality.prereview_checks(planted, SOURCES, (result, value))}
        self.assertIn('currency-code', ids)
        self.assertNotIn('long-number', ids)
        clean = pipeline.reader_view(PROSE, SOURCES, value, result, 'EXMP')
        planted = clean.replace('## Method notes', '## Method notes\n\n- Planted USD 1,234,567 — note, not prose.')
        ids = {item['id'] for item in quality.prereview_checks(planted, SOURCES, (result, value))}
        self.assertFalse(ids & {'currency-code', 'long-number', 'em-dash', 'contrast-formula'})


def saved_runs():
    """Saved run folders named in INTRINSICVALUE_SAVED_RUNS (os.pathsep-separated globs), if any."""
    import glob
    import os
    patterns = [part for part in os.environ.get('INTRINSICVALUE_SAVED_RUNS', '').split(os.pathsep) if part]
    return sorted({path for pattern in patterns for path in glob.glob(pattern)
                   if (Path(path) / 'state.json').is_file()})


class SavedRunGuardTests(unittest.TestCase):
    """Rendered saved runs carry no "USD" and no seven-digit number outside prose, sources and review status.

    Saved runs are large and live outside the repository; set INTRINSICVALUE_SAVED_RUNS to their folders.
    """

    def test_outside_the_prose_there_is_no_usd_and_no_long_number(self):
        import importlib.util
        runs = saved_runs()
        if not runs:
            self.skipTest('INTRINSICVALUE_SAVED_RUNS names no saved run')
        spec = importlib.util.spec_from_file_location('rerender', Path(__file__).resolve().parents[2] / 'tools' / 'rerender.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        for folder in runs:
            with self.subTest(run=Path(folder).name):
                memo = tool.rerender(folder)
                prose = memo.index('\n## Summary')
                start = memo.index('\n## ', prose + 5)
                end, sources = memo.index('\n## Valuation detail'), memo.index('\n## Sources')
                computed = memo[:start] + memo[end:sources]
                self.assertNotRegex(computed, r'\bUSD\b')
                self.assertNotRegex(computed, LONG_NUMBER)


def outside_prose(memo):
    """The memo without its prose sections, sources and review status."""
    start = memo.index('\n## ', memo.index('\n## Summary') + 5)
    return memo[:start] + memo[memo.index('\n## Valuation detail'):memo.index('\n## Sources')]


def history_source(currency, scale):
    """A sec_facts source with a two-year history and a segment breakdown, amounts times ``scale``."""
    def row(end, revenue):
        return {'fiscal_year_end': end, 'currency': currency, 'revenue': revenue * scale, 'revenue_growth': .1,
                'gross_margin': .6, 'operating_margin': .3, 'research_and_development_to_revenue': .1,
                'selling_general_administrative_to_revenue': .1, 'effective_tax_rate': .2,
                'capital_expenditure_to_revenue': .05, 'depreciation_amortization_to_revenue': .04,
                'stock_compensation_to_revenue': .02, 'repurchases': 5 * scale, 'average_repurchase_price': 41.25,
                'dividends': 2 * scale, 'diluted_eps': 3.0, 'nopat': 30 * scale, 'invested_capital': 120 * scale,
                'average_invested_capital': 110 * scale}
    breakdown = {'segment': {'fiscal_year_ends': ['2025-12-31', '2024-12-31'], 'members': [
        {'label': 'Beverages', 'revenue': [300 * scale, 270 * scale], 'revenue_growth': [.111, None]},
        {'label': 'Snacks', 'revenue': [100 * scale, 95 * scale], 'revenue_growth': [.053, None]}]}}
    return {'id': 'S1', 'kind': 'sec_facts', 'title': 'SEC company facts: SMALL FOODS INC', 'source_tier': 'regulatory_primary',
            'url': 'https://example.test/facts', 'retrieved_at': '2026-03-31T00:00:00Z', 'data': {},
            'historical_baseline': {'rows': [row('2024-12-31', 364), row('2025-12-31', 400)], 'concepts': {}},
            'breakdown_tables': breakdown}


class EdgeFilerGuardTests(unittest.TestCase):
    """The guard also holds for a company under $1B of revenue and for a non-dollar filer (review, 2026-11-27)."""

    def render(self, value, currency, scale):
        result = evaluate(value)
        state = {'ticker': 'SMLL', 'quality_status': 'reviewed_draft', 'complete': True, 'review_history': []}
        return pipeline._render(PROSE, [history_source(currency, scale)], [], value, result, state)

    def test_a_small_company_scales_every_table(self):
        value = headline_plan()
        value['actuals'].update(revenue=400e6, net_debt=30e6, shares_outstanding=40e6)
        value['net_debt_bridge'].update(debt=60e6, selected_cash_and_nonoperating_investments=30e6, net_debt=30e6)
        memo = self.render(value, 'USD', 1e6)
        computed = outside_prose(memo)
        self.assertNotRegex(computed, r'\bUSD\b')
        self.assertNotRegex(computed, LONG_NUMBER)
        self.assertIn('| Revenue ($M) | 364 | 400 |', computed)
        self.assertIn('| 2025 ($M) | 2024 ($M) |', computed)
        self.assertIn('| Year | Revenue ($M) |', computed)
        self.assertNotIn('| 0.0 |', computed)

    def test_a_non_dollar_filer_uses_its_symbol(self):
        value = glide_plan(glide=False, timing=False)
        value.pop('discount_rate')
        for name, scenario in value['scenarios'].items():
            scenario.update(wacc=.09, terminal_growth=.03, probability=1.0 if name == 'base' else 0.0)
        value['currency'] = 'EUR'
        value['actuals'].update(revenue=1000e9, net_debt=50e9, shares_outstanding=100e9)
        value['net_debt_bridge'].update(debt=100e9, selected_cash_and_nonoperating_investments=50e9, net_debt=50e9)
        memo = self.render(value, 'EUR', 1e9)
        computed = outside_prose(memo)
        self.assertIn('Euros; €B is billions and €M is millions.', computed)
        self.assertIn('| Revenue (€B) |', computed)
        self.assertNotRegex(computed, LONG_NUMBER)
        self.assertNotRegex(computed, r'\bUSD\b|\$')


class EdgeDisplayTests(unittest.TestCase):
    def test_usd_restyle_leaves_rates_coupons_years_and_quotes(self):
        for text in ('Microsoft issued USD 2.5% notes due 2030.', 'The USD 2030 notes.', 'A EUR/USD 1.08 rate.',
                     'USD 1 = EUR 0.92 at year end.', 'The 8-K is titled "USD 500 million Senior Notes".',
                     'Debt is USD-denominated.'):
            self.assertEqual(quality.dollar_amounts(text), text)
            self.assertEqual(quality.style_findings(text), [])
        self.assertEqual(quality.dollar_amounts('USD -2.4 billion of other income'), '-$2.4B of other income')

    def test_units_and_decimals_follow_the_rounded_value(self):
        style = memo_style.Style('USD')
        self.assertEqual([style.per_share(v) for v in (99.96, 9.996, 0.9996)], ['$100', '$10.0', '$1.00'])
        self.assertEqual([style.amount(v) for v in (999.6e6, 99.96e9, 999999.6)], ['$1.0B', '$100B', '$1.0M'])

    def test_a_value_just_below_the_price_reads_below_it(self):
        value = headline_plan()
        result = evaluate(value)
        price = result['market_price']
        result['scenarios']['base'].update(per_share_value=price - 0.004, value_to_price=(price - 0.004) / price)
        sentence = next(line for line in pipeline.computed_summary(value, result).split('\n') if line.startswith('The base'))
        self.assertIn(f'value is ${price - 0.004:,.2f} a share', sentence)
        self.assertIn('a value / price of 1.000', sentence)

    def test_a_small_bridge_line_keeps_its_own_unit(self):
        value = headline_plan()
        value['actuals']['net_debt'] = 30e6
        value['net_debt_bridge'].update(debt=80e6, selected_cash_and_nonoperating_investments=50e6, net_debt=30e6)
        lines, _ = pipeline._equity_bridge_parts(value, evaluate(value), memo_style.Style('USD'))
        self.assertIn('| Less net debt | $30.0M |', '\n'.join(lines))


# Computed metrics a note or label must not call "reported" (live Microsoft review, 2026-11-27:
# 0.41 sales-to-capital and a 35.8% incremental ROIC computed from filings were called reported).
# The phrase lists are shared with the prose free check (quality.computed_called_reported).
from investmentideas.quality import COMPUTED_METRIC, INPUT_PHRASE  # noqa: E402
# True sentences that name a computed metric and a reported input, each justified:
ALLOWED_REPORTED = (
    # The definition counts acquisitions and finance-lease additions only where the filing reports them.
    # The depreciation wording follows the filings (evidence_tables.depreciation_phrase), so only the start is fixed.
    'Net reinvestment is capital expenditure less depreciation',
    # The capitalized ROIC is withheld because the filing reports no R&D line: "reported" refers to the line.
    'ROIC with intangibles capitalized is not shown: no R&D line is reported',
    # A history measure that no filing reports in any year shown is listed by name.
    'Not reported in any year shown:',
)


def reported_metric_sentences(memo):
    """Sentences outside the prose that put "reported" beside a computed metric, less the allow-list."""
    found = []
    for sentence in re.split(r'(?<=[.!?])\s+|\n', outside_prose(memo)):
        if any(allowed in sentence for allowed in ALLOWED_REPORTED):
            continue
        if COMPUTED_METRIC.search(sentence) and re.search(r'\breported\b', INPUT_PHRASE.sub('', sentence), re.I):
            found.append(sentence[:240])
    return found


class ReportedMetricGuardTests(unittest.TestCase):
    def test_python_text_never_calls_a_computed_metric_reported(self):
        memos = [rendered()[0]]
        value = headline_plan()
        state = {'ticker': 'SMLL', 'quality_status': 'reviewed_draft', 'complete': True, 'review_history': []}
        memos.append(pipeline._render(PROSE, [history_source('USD', 1e9)], [], value, evaluate(value), state))
        import importlib.util
        spec = importlib.util.spec_from_file_location('rerender', Path(__file__).resolve().parents[2] / 'tools' / 'rerender.py')
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        memos += [tool.rerender(folder) for folder in saved_runs()]
        for memo in memos:
            with self.subTest(title=memo.split('\n', 1)[0]):
                self.assertEqual(reported_metric_sentences(memo), [])

    def test_the_guard_catches_the_live_wording(self):
        memo, _, _ = rendered()
        bad = memo.replace('## Method notes', '## Method notes\n\n- The reported incremental sales-to-capital is 0.41.')
        self.assertEqual(len(reported_metric_sentences(bad)), 1)


class RateTestWordingTests(unittest.TestCase):
    """Every rate test moves the terminal return with its terminal rate; the notes say so (2026-11-27)."""

    def test_rate_rows_follow_the_tested_terminal_rate(self):
        from investmentideas import valuation
        value = headline_plan()
        for scenario in value['scenarios'].values():
            scenario['terminal_roic_basis'] = 'terminal_rate'
        calls = []
        original = valuation._project

        def spy(actuals, scenario, *, flat=None, shift=0.0, terminal=None):
            if flat is not None or shift or terminal is not None:
                calls.append(bool(scenario.get('terminal_roic_follows_rate')))
            return original(actuals, scenario, flat=flat, shift=shift, terminal=terminal)
        valuation._project = spy
        try:
            result = evaluate(value)
        finally:
            valuation._project = original
        base = result['scenarios']['base']
        self.assertAlmostEqual(base['terminal_roic_applied'], base['terminal_rate'])
        self.assertTrue(calls)
        self.assertTrue(all(calls))
        self.assertIn('Every rate test moves the terminal return on new capital', pipeline.RATE_TEST_RULE)
        rewritten = pipeline._assumption_notes('After it new investment earns its cost. The rate alternatives keep this rule.')
        self.assertIn(pipeline.RATE_TEST_RULE, rewritten)

    def test_notes_name_what_each_row_changes(self):
        style = memo_style.Style('USD')
        with_rnd = {'scenarios': {'base': {'terminal_rnd_net_investment': 26e9, 'cashflows': []}}}
        self.assertIn('keeps R&D expensed in the continuing value', pipeline._reinvestment_row_note(with_rnd, style))
        without = {'scenarios': {'base': {'terminal_rnd_net_investment': 0.0, 'cashflows': []}}}
        self.assertNotIn('R&D', pipeline._reinvestment_row_note(without, style))
        follows = {'scenarios': {'base': {'search_floor': .005}}}
        self.assertTrue(pipeline._implied_follows(follows))
        self.assertFalse(pipeline._implied_follows({'scenarios': {'base': {'search_floor': .0495}}}))

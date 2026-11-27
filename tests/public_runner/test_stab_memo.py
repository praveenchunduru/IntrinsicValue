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
        for caveat in ('stress cases without probabilities', 'stylized', 'never added together'):
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

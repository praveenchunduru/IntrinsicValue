"""Python's text is true for each run (computed-text audit of four live memos, 2026-11-27).

The audits found 75 problems in text Python writes. Two causes recur: fixed notes printed
whether or not they apply to the run, and history set beside forecasts without matching
period or basis. These tests render several memo shapes and check that each conditional
note appears only when its condition holds, and that comparison rows name matching periods.

Shapes: no R&D against an R&D add-back; capital expenditure below depreciation against
positive net reinvestment; an aggregate EBIT-margin plan against a gross-profit plan; one
requested forecast against several samples. Saved runs named in INTRINSICVALUE_SAVED_RUNS
are checked as well.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

from investmentideas import memo_style, pipeline
from investmentideas.valuation import evaluate

from tests.public_runner.test_stab_memo import PROSE, SOURCES, headline_plan, outside_prose, saved_runs
from tests.public_runner.test_underwriting_integration import inputs as operating_plan

STYLE = memo_style.Style('USD')
# Fixed wording the audits found printed where it did not apply, or wrong everywhere.
NEVER = ('less for low-margin', 'The lower rate is the reference', 'gives either the 15% row', 'The earlier rule is',
         'does not step up', 'points a year', 'Lowest month', 'Highest month', 'same conventions',
         'several percent below', 'the premium assumes', 'a year, computed', 'among companies of the same starting size')


def render(value, memo=PROSE, sources=SOURCES):
    result = evaluate(value)
    state = {'ticker': 'EXMP', 'quality_status': 'reviewed_draft', 'complete': True, 'review_history': []}
    return pipeline._render(memo, sources, [], value, result, state), result


def rerender_saved():
    import importlib.util
    spec = importlib.util.spec_from_file_location('rerender', Path(__file__).resolve().parents[2] / 'tools' / 'rerender.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    for folder in saved_runs():
        state = json.loads((Path(folder) / 'state.json').read_text(encoding='utf-8'))
        yield Path(folder).name, tool.rerender(folder), state


def check_conditions(case, memo, result, plan):
    """Every conditional note in ``memo`` holds for ``result``; returns the failures."""
    computed = outside_prose(memo)
    failures = [f'{case}: "{phrase}"' for phrase in NEVER if phrase in computed]
    fade = [line for line in computed.split('\n') if line.startswith('- The industry fade row')]
    if any('of revenue' in line for line in fade):
        failures.append(f'{case}: fade weights called revenue shares')
    if pipeline._terminal_follows(result) and re.search(r'\| Terminal growth \| [^|]+ \| cannot reach the price alone', computed):
        failures.append(f'{case}: terminal growth reads as a failed search')
    addback = pipeline._rnd_addback(result)
    if not addback:
        for phrase in ('R&D add-back', 'with R&D expensed', 'keeps R&D expensed'):
            if phrase in computed:
                failures.append(f'{case}: "{phrase}" without an R&D add-back')
    has_rnd = addback or any(any(scenario.get('rnd_share_path') or []) for scenario in (plan.get('scenarios') or {}).values()
                             if isinstance(scenario, dict))
    if 'operating rent, SBC and R&D' in computed and not has_rnd:
        failures.append(f'{case}: GAAP EBIT note names R&D the plan does not have')
    forecast_table = computed.split('### Forecast return on invested capital')[1].split('###')[0] \
        if '### Forecast return on invested capital' in computed else ''
    if 'n/m marks years where capital fell' in computed and '| n/m' not in forecast_table:
        failures.append(f'{case}: forecast n/m note without an n/m cell')
    operating = any(row.get('gross_profit') is not None for row in
                    ((((result.get('operating_forecasts') or {}).get('scenarios') or {}).get('base') or {}).get('cashflows') or []))
    if ('Component margins in the operating forecast' in computed) != operating:
        failures.append(f'{case}: component-margin note does not match the plan type')
    if ('| Gross profit (' in computed) != operating:
        failures.append(f'{case}: gross-profit column does not match the plan type')
    forecast_samples = bool(result.get('forecast_consensus')) or bool(plan.get('forecast_consensus'))
    # A run that asked for several samples but lost their record (an early replay) says so; that note is true.
    if ('**Forecast samples**' in computed and not forecast_samples and 'combines' not in computed
            and 'record of those samples is not available' not in computed):
        failures.append(f'{case}: forecast-sample notes without a sample record')
    # Comparison rows carry matching periods: a history total names as many years as the forecast columns cover.
    for match in re.finditer(r'\| Net reinvestment, forecast years 1-(\d+) \([^)]*\) \| ([^|]+)\|', computed):
        years, cell = int(match.group(1)), match.group(2)
        span = re.search(r'over FY(\d{4})-(\d{2})', cell)
        if span and (int(span.group(1)[:2] + span.group(2)) - int(span.group(1)) + 1) != years:
            failures.append(f'{case}: history span does not match {years} forecast years')
        if not span and ' only' not in cell and 'not computed' not in cell:
            failures.append(f'{case}: history cell names no period')
    cash = ((plan.get('net_debt_bridge') or {}).get('cash_coverage') or {}).get('excluded_reported_categories')
    if cash and 'An omitted investment category counts as zero by assumption.' in computed:
        failures.append(f'{case}: omitted-category note beside the listed excluded categories')
    if cash and 'excluded by house policy' not in computed:
        failures.append(f'{case}: excluded cash categories not listed')
    annual = computed.split('### Annual results')[1].split('###')[0] if '### Annual results' in computed else ''
    if '| outside filings scanned' in annual.lower() and 'older filings report' not in annual:
        failures.append(f'{case}: "outside filings scanned" cells without the caption that explains them')
    if '(depreciation only)' in annual and '"(Depreciation only)" marks' not in annual:
        failures.append(f'{case}: "(depreciation only)" cells without the caption that explains them')
    base_rates = computed.split('### Revenue growth against base rates')[1].split('###')[0] \
        if '### Revenue growth against base rates' in computed else ''
    if 'real' in base_rates and 'nominal' not in base_rates:
        failures.append(f'{case}: base-rate cells mix nominal growth with real statistics')
    return failures


class ShapeTests(unittest.TestCase):
    def test_no_rnd_aggregate_single_forecast(self):
        value = headline_plan()
        memo, result = render(value)
        self.assertEqual(pipeline._rnd_addback(result), 0.0)
        self.assertEqual(check_conditions('synthetic aggregate', memo, result, value), [])
        self.assertNotIn('**Forecast samples**', memo)
        self.assertIn('GAAP', memo) if 'GAAP EBIT' in memo else None

    def test_a_gross_profit_plan_shows_its_columns(self):
        value = operating_plan()
        memo, result = render(value)
        self.assertEqual(check_conditions('synthetic operating', memo, result, value), [])

    def test_saved_runs_hold_every_condition(self):
        runs = list(rerender_saved())
        if not runs:
            self.skipTest('INTRINSICVALUE_SAVED_RUNS names no saved run')
        failures = []
        for name, memo, state in runs:
            failures += check_conditions(name, memo, state['valuation'], state['plan'])
        self.assertEqual(failures, [])


class ContinuityShapeTests(unittest.TestCase):
    """Net reinvestment history is a three-year total on a named basis, whichever way the filings fall."""

    def result(self, **reinvestment):
        base = {'fiscal_year_end': '2026-09-26', 'years': 3, 'source_refs': ['S2'], 'sales_to_capital_basis': 'invested_capital',
                'scenarios': {name: {'net_reinvestment': 55e9, 'sales_to_capital': 2.0, 'within_band': True}
                              for name in ('bear', 'base', 'bull')}}
        return {'reinvestment_continuity': dict(base, **reinvestment)}

    def table(self, result):
        lines, notes = pipeline._continuity_parts(result, STYLE)
        return '\n'.join(lines), ' '.join(notes)

    def test_capex_below_depreciation_shows_the_change_in_invested_capital(self):
        text, notes = self.table(self.result(net_reinvestment=-4.4e9, net_reinvestment_status='not_positive',
                                             net_reinvestment_over_years=-5.4e9, invested_capital_change=50.5e9,
                                             invested_capital_sales_to_capital=1.89, capital_expenditure_sales_to_capital=None,
                                             incremental_sales_to_capital=1.89))
        self.assertIn('| 50.5 over FY2024-26, change in invested capital, computed |', text)
        self.assertIn('| 1.89 over FY2024-26, on the change in invested capital, computed |', text)
        self.assertNotIn('not positive |', text)
        self.assertIn('Only the change-in-invested-capital sales-to-capital is computed here', notes)
        self.assertNotIn('lower rate', notes)
        self.assertIn('so there is no floor', notes)

    def test_positive_net_reinvestment_is_a_three_year_total_with_the_last_year(self):
        text, notes = self.table(self.result(fiscal_year_end='2025-12-31', net_reinvestment=1.523e9,
                                             net_reinvestment_status='reported', net_reinvestment_over_years=3.613e9,
                                             invested_capital_change=5.9e9, invested_capital_sales_to_capital=.83,
                                             capital_expenditure_sales_to_capital=1.37, incremental_sales_to_capital=.83))
        self.assertIn('3.6 over FY2023-25 (1.5 in FY2025), computed', text)
        self.assertIn('| 1.37 over FY2023-25, computed |', text)
        self.assertIn('on the change in invested capital it is 0.83. The continuity check uses the lower, 0.83.', notes)
        self.assertNotIn('Goodwill and acquired intangibles count only up to', notes)
        self.assertIn('unless the cell names another period', text)


class DataHookTests(unittest.TestCase):
    """Fields the data side records reach the reader (review seam, 2026-11-27)."""

    def test_zero_lines_and_excluded_cash_say_why(self):
        value = headline_plan()
        result = evaluate(value)
        value['other_claims_bridge'] = {
            'components': {}, 'finance_lease_claim': 0, 'components_assumed_zero': [
                {'line': 'defined_benefit_obligations', 'basis': 'assumption: zero by assumption; no pension or '
                 'postretirement liability is tagged for the whole company on the date, and the filing\'s plan-level '
                 'values were not read'},
                {'line': 'preferred_stock', 'basis': 'assumption: no concept reported on the date; zero, not a reported fact'}]}
        value['net_debt_bridge']['cash_coverage'] = {'excluded_reported_categories': [
            {'label': 'available-for-sale debt securities', 'value': 1774e6, 'source_refs': ['S1'],
             'reason': 'the available-for-sale total is used only when no other investment category is reported'}]}
        lines, notes = pipeline._equity_bridge_parts(value, result, STYLE)
        text = ' '.join(notes)
        self.assertIn('less unfunded pension obligations, before tax (no pension or postretirement liability is tagged '
                      'for the whole company on the date, and the filing\'s plan-level values were not read, zero by '
                      'assumption)', text)
        self.assertIn('less preferred stock (no concept reported on the date, zero by assumption)', text)
        self.assertNotIn('not a reported fact', text)
        self.assertIn('Reported available-for-sale debt securities of $1.8B [S1] are excluded by house policy: the '
                      'available-for-sale total is used only when no other investment category is reported.', text)
        omitted = pipeline._assumption_notes('An omitted investment category counts as zero by assumption.', result, value)
        self.assertEqual(omitted, [])

    def test_history_rows_carry_their_basis(self):
        from tests.public_runner.test_stab_memo import history_source
        source = history_source('USD', 1e9)
        rows = source['historical_baseline']['rows']
        rows[0].update(average_repurchase_price_concept='StockRepurchasedDuringPeriodValue / StockRepurchasedDuringPeriodShares',
                       shares_repurchased=1e8, depreciation_amortization=10e9, depreciation_only=True)
        rows[1].update(average_repurchase_price_concept='StockRepurchasedDuringPeriodValue / StockRepurchasedDuringPeriodShares',
                       shares_repurchased=1e8, depreciation_amortization=12e9, operating_income=120e9)
        for entry, income in zip(source['breakdown_tables']['segment']['members'], (100e9, 40e9)):
            entry.update(operating_income=[income, income], operating_margin=[.3, .3])
        text = '\n'.join(pipeline.reported_tables_section([source]))
        self.assertIn('| Average buyback price, repurchase value in the equity statement / shares ($) |', text)
        # The consolidated figure (120 in FY2025) is set beside the segment sum (140).
        self.assertIn('Segment operating income sums to $140B for the fiscal year ending 2025-12-31, against consolidated '
                      'operating income of $120B', text)
        lines, notes = pipeline._continuity_parts(
            {'reinvestment_continuity': {'fiscal_year_end': '2025-12-31', 'years': 3, 'net_reinvestment': 2e9,
                                         'net_reinvestment_over_years': 5e9, 'capital_expenditure_sales_to_capital': 1.2,
                                         'invested_capital_sales_to_capital': 1.0, 'sales_to_capital_basis': 'invested_capital',
                                         'scenarios': {'base': {'net_reinvestment': 6e9, 'sales_to_capital': 1.1}}}},
            STYLE, rows)
        self.assertIn('less depreciation and amortization (depreciation only for the fiscal years ending 2024-12-31)',
                      ' '.join(notes))


class NoteFactTests(unittest.TestCase):
    def test_terminal_growth_row_says_what_growth_does(self):
        value = headline_plan()
        for scenario in value['scenarios'].values():
            scenario['terminal_roic_basis'] = 'terminal_rate'
        memo, result = render(value)
        row = next(row for row in result['price_requirements']['rows'] if row['input'] == 'terminal_growth')
        if row['status'] == 'not_reachable':
            self.assertIn('| Terminal growth |', memo)
            self.assertIn('does not change the value', memo)

    def test_changes_are_true_differences_rounded(self):
        # Changes are the true difference, rounded (re-audit, 2026-11-27): 45.529 - 42.8625 is +2.7.
        self.assertEqual(pipeline._change(45.529, 42.8625), '+2.7')
        self.assertEqual(pipeline._change(203.2, 220.504), '-17')
        self.assertIn(pipeline.CHANGE_NOTE.split(',')[0], pipeline.CHANGE_NOTE)

    def test_a_trailing_currency_code_is_restyled(self):
        self.assertEqual(memo_style.dollar_amounts('charges above 2.0bn USD [S4]'), 'charges above $2.0B [S4]')
        self.assertEqual(memo_style.usd_amount_count('charges above 2.0bn USD'), 1)

    def test_plain_identifiers(self):
        self.assertEqual(pipeline._plain('products_gross_margin_q1_fy2027'), 'Products gross margin Q1 FY2027')
        self.assertEqual(pipeline.REPURCHASE_SCOPES['historical_retrospective_conditional'],
                         'Historical buyback, repeated today')

    def test_assumption_sentences_follow_the_run(self):
        label = ('Growth, margins, taxes, discount rates, capital efficiency, terminal returns and the bear and bull stress '
                 'cases are house forecasts. GAAP EBIT retains operating rent, SBC and R&D. After year 10, new investment '
                 'earns its cost of capital, so growth adds no value. The earlier rule is the median ROIC computed from '
                 'reported values, at most 15%. The panel also shows horizons of 5, 15 and 20 years.')
        plain = pipeline._assumption_notes(label, {'scenarios': {'base': {'terminal_rnd_net_investment': 0.0}}}, {})
        self.assertIn('GAAP EBIT retains operating rent and SBC.', plain)
        self.assertIn('Discount rates are computed from dated market inputs and proxy choices.', plain)
        self.assertFalse(any(note.startswith(('The earlier rule is', 'The panel also shows')) for note in plain))
        rnd = pipeline._assumption_notes(label, {'scenarios': {'base': {'terminal_rnd_net_investment': 1e9}}}, {})
        self.assertIn('GAAP EBIT retains operating rent, SBC and R&D.', rnd)
        self.assertTrue(any('only through the net R&D add-back' in note for note in rnd))


def wording_failures(case, memo, result, sources=()):
    """The final wording round's computed notes hold for the run (re-audit of KO and GOOGL, 2026-11-27)."""
    computed = outside_prose(memo)
    failures = []
    # Item 1: change columns carry the true change, and the note says so.
    if re.search(r'\| Change \(', computed) and pipeline.CHANGE_NOTE not in computed:
        failures.append(f'{case}: change column without the rounding note')
    if '_shown_change' in dir(pipeline):
        failures.append(f'{case}: changes still computed from rounded values')
    # Item 4: "53-week" only when a fiscal year runs past 366 days.
    if '53-week' in computed:
        from datetime import date
        ends = sorted({date.fromisoformat(row['fiscal_year_end'][:10]) for source in sources for row in
                       (((source or {}).get('historical_baseline') or {}).get('rows') or []) if row.get('fiscal_year_end')})
        if not any((later - earlier).days > 366 for earlier, later in zip(ends, ends[1:])):
            failures.append(f'{case}: "53-week" without a fiscal year longer than 366 days')
    # Item 5: the ceiling note states the ceilings the run used.
    cost = result.get('cost_path_continuity') or {}
    ceilings = cost.get('final_year_margin_ceilings') or {}
    if ceilings:
        note = pipeline._ceiling_note(cost)
        if note not in computed:
            failures.append(f'{case}: ceiling note missing')
        if 'come from the operating margins of the last' in computed:
            failures.append(f'{case}: old ceiling sentence')
        latest = cost.get('latest_run_rate') or {}
        peak, run = cost.get('max_operating_margin'), latest.get('operating_margin')
        if isinstance(ceilings.get('base'), float) and isinstance(peak, float):
            floor = max(peak, run) if isinstance(run, float) else peak
            match = re.search(r'here ([0-9.]+)%, plus ([0-9.]+) points', note)
            if isinstance(run, float) and not (match and abs(float(match.group(1)) - floor * 100) < .051 and
                                               abs(floor + float(match.group(2)) / 100 - ceilings['base']) < .0006):
                failures.append(f'{case}: ceiling note does not add up to the base ceiling')
    # Item 6: the rate-test note names only exhibits shown, and unchanged terminal rates are said to be unchanged.
    line = next((line for line in computed.split('\n') if 'the terminal rate the test sets' in line or
                 'leave the terminal rate, and so the terminal return, unchanged' in line), '')
    if 'Every rate test moves the terminal return' in computed:
        failures.append(f'{case}: fixed rate-test sentence')
    if line:
        shown = {'the rate methods': '| Rate method' in computed, 'the beta checks': '### Beta cross-checks' in computed,
                 'the country-risk row': '### Country risk' in computed,
                 'the implied return': '### What the price implies' in computed}
        for phrase, present in shown.items():
            if phrase in line.lower() and not present:
                failures.append(f'{case}: rate-test note names {phrase}, which the memo does not show')
        house = ((result.get('scenarios') or {}).get('base') or {}).get('terminal_rate')
        moving = line.split('the terminal return on new capital moves')[0]
        for key, phrase in (('calibration_comparisons', 'the rate methods'), ('beta_cross_checks', 'the beta checks')):
            rows = [row for row in result.get(key) or [] if isinstance(row.get('terminal_rate'), float)]
            if rows and isinstance(house, float) and all(abs(row['terminal_rate'] - house) < 1e-9 for row in rows):
                if phrase in moving:
                    failures.append(f'{case}: {phrase} listed as moving the terminal rate, which they leave unchanged')
    return failures


class WordingRoundTests(unittest.TestCase):
    """Items 1, 4, 5 and 6 of the final wording round, on synthetic shapes and on the saved runs."""

    def test_changes_differ_from_rounded_differences_only_with_the_note(self):
        # KO's calibration row: 45.529 - 42.8625 = 2.67 shows as +2.7, where the rounded values give 2.6.
        self.assertEqual(pipeline._change(45.529, 42.8625), '+2.7')
        self.assertEqual(pipeline._change(38.62, 42.8625), '-4.2')
        self.assertFalse(hasattr(pipeline, '_shown_change'))
        memo, result = render(headline_plan())
        self.assertEqual(wording_failures('synthetic', memo, result), [])

    def test_a_long_quarter_span_names_its_cause(self):
        from investmentideas.evidence_tables import render_quarterly
        table = {'rows': [{'fiscal_quarter': 3, 'period_end': '2026-10-02', 'revenue': 1e9, 'operating_income': 1e8,
                           'operating_margin': .1, 'revenue_basis': 'reported', 'operating_income_basis': 'reported',
                           'revenue_growth_basis': 'unequal_length', 'days': 98, 'prior_days': 91}],
                 'currency': 'USD', 'trailing_twelve_months': {'period': {'start_date': '2025-09-27', 'end_date': '2026-10-02'},
                                                               'days': 371, 'revenue': 4e9, 'operating_income': 1e9,
                                                               'operating_margin': .25}}
        calendar = {'quarterly': table, 'rows': [{'fiscal_year_end': '2024-12-31'}, {'fiscal_year_end': '2025-12-31'}]}
        text = render_quarterly(calendar, 'S2')
        self.assertNotIn('53-week', text)
        self.assertIn('(371 days, because the quarter-end dates moved)', text)
        self.assertIn('quarters of different length, this quarter\'s days against the year-earlier quarter\'s, because '
                      'the quarter-end dates moved', text)
        retail = {'quarterly': table, 'rows': [{'fiscal_year_end': '2024-01-28'}, {'fiscal_year_end': '2025-02-02'}]}
        text = render_quarterly(retail, 'S2')
        self.assertIn('(371 days, a 53-week year)', text)
        self.assertIn('as in a 53-week year', text)

    def test_the_ceiling_note_adds_up(self):
        cost = {'years': 5, 'max_operating_margin': .28706, 'average_operating_margin': .25339,
                'latest_run_rate': {'basis': 'trailing_twelve_months', 'operating_margin': .30041,
                                    'period': {'start_date': '2025-09-27', 'end_date': '2026-10-02'}},
                'final_year_margin_ceilings': {'bear': .25339, 'base': .35041, 'bull': .35041}}
        self.assertEqual(pipeline._ceiling_note(cost),
                         'The bear ceiling is the five-year average operating margin, 25.3%. The base and bull ceiling is '
                         'the higher of the five-year peak and the twelve-month margin to 2026-10-02, here 30.0%, plus 5 points.')

    def test_the_rate_test_note_follows_the_exhibits(self):
        result = {'scenarios': {'base': {'terminal_rate': .0874}},
                  'convention_panel': {'rows': [{'name': 'required_return_minus_100bp', 'status': 'calculated'},
                                                {'name': 'rate_history_low', 'status': 'calculated'}]},
                  'beta_cross_checks': [{'name': 'regression_beta', 'terminal_rate': .0874}],
                  'implied_return_at_price': {'base': {}}}
        text = pipeline._rate_test_note(result)
        self.assertEqual(text, 'In the whole-path shifts, the month rows and the implied return, the terminal return on '
                               'new capital moves with the terminal rate the test sets. The beta checks leave the terminal '
                               'rate, and so the terminal return, unchanged.')
        self.assertNotIn('rate methods', text)
        result['calibration_comparisons'] = [{'name': 'historical_issuer_beta', 'terminal_rate': .0950}]
        self.assertIn('the rate methods and the implied return, the terminal return', pipeline._rate_test_note(result))

    def test_saved_runs_hold_the_wording_round(self):
        runs = list(rerender_saved())
        if not runs:
            self.skipTest('INTRINSICVALUE_SAVED_RUNS names no saved run')
        failures = []
        for name, memo, state in runs:
            failures += wording_failures(name, memo, state['valuation'], state.get('sources') or [])
        self.assertEqual(failures, [])


if __name__ == '__main__':
    unittest.main()

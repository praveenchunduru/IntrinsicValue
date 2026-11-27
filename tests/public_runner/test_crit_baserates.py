"""Published sales-growth base rates and where a plan's revenue path falls in them.

Mauboussin critique F2 and F6 (2026-11-26): the forecast had no outside view, and the
price-required growth was never placed against history. Owner choice: print the
percentiles, with no gate and no extra plan repairs.
"""
import copy
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from investmentideas import base_rates, pipeline
from investmentideas.valuation import evaluate

from tests.public_runner import test_full_pipeline as full
from tests.public_runner.test_market_consistency import plan as market_plan

ROOT = Path(__file__).resolve().parents[2]
APPLE = ROOT / 'examples' / 'AAPL-2026-11-06-revised' / 'assumptions.json'

# Appendix totals of The Base Rate Book (p31-32), 1-, 3-, 5- and 10-year columns.
APPENDIX_TOTALS = {
    'brb2016_0_325m': (6243, 6057, 5994, 5740), 'brb2016_325_700m': (5731, 5609, 5496, 5123),
    'brb2016_700m_1250m': (5194, 5039, 4875, 4429), 'brb2016_1250m_2bn': (4874, 4673, 4492, 3981),
    'brb2016_2_3bn': (4795, 4539, 4287, 3659), 'brb2016_3_4500m': (5214, 4890, 4599, 3822),
    'brb2016_4500m_7bn': (5684, 5231, 4799, 3898), 'brb2016_7_12bn': (6504, 5984, 5458, 4198),
    'brb2016_12_25bn': (6738, 6047, 5402, 3909), 'brb2016_above_25bn': (5906, 5197, 4472, 2886),
    'brb2016_above_50bn': (2484, 2152, 1810, 1099)}
# Exhibit 4 bin percentages as printed (p24-25), one column per class checked.
PRINTED = {
    ('brb2016_above_50bn', 10): (0.0, 0.3, 1.0, 3.1, 9.8, 26.9, 37.8, 17.2, 3.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    ('brb2016_above_50bn', 3): (2.0, 0.8, 2.3, 4.2, 10.7, 21.4, 26.5, 16.9, 9.2, 3.2, 1.6, 0.7, 0.1, 0.3, 0.1, 0.0),
    ('brb2016_4500m_7bn', 5): (0.2, 0.2, 0.6, 1.9, 4.5, 15.6, 33.2, 23.1, 10.5, 5.6, 2.2, 1.1, 0.6, 0.3, 0.2, 0.3),
    ('brb2016_0_325m', 3): (0.4, 0.2, 0.4, 1.0, 1.8, 5.9, 15.3, 19.1, 15.2, 10.4, 6.4, 4.5, 3.3, 2.8, 2.0, 11.3)}


def klass(class_id):
    return next(item for item in base_rates.SALES_GROWTH_2016 + base_rates.SALES_GROWTH_2026 if item['id'] == class_id)


def usd_plan(revenue=100e9, years=10, growth=.06, **kwargs):
    value = market_plan(years, growth=growth, **kwargs)
    scale = revenue / value['actuals']['revenue']
    for field in ('revenue', 'net_debt', 'shares_outstanding'):
        value['actuals'][field] *= scale
    for field in ('debt', 'selected_cash_and_nonoperating_investments', 'net_debt'):
        value['net_debt_bridge'][field] *= scale
    for field in ('market_equity', 'financial_debt'):
        value['discount_rate'][field]['value'] *= scale
    value['input_dates'] = {'revenue': {'start_date': '2025-01-01', 'end_date': '2025-12-31'}}
    return value


class PublishedTables(unittest.TestCase):
    def test_counts_match_the_appendix_totals_and_printed_bins(self):
        self.assertEqual({item['id'] for item in base_rates.SALES_GROWTH_2016}, set(APPENDIX_TOTALS))
        for item in base_rates.SALES_GROWTH_2016:
            for index, years in enumerate((1, 3, 5, 10)):
                counts = item['horizons'][years]['counts']
                self.assertEqual(len(counts), len(base_rates.BIN_EDGES) + 1)
                self.assertEqual(sum(counts), APPENDIX_TOTALS[item['id']][index], (item['id'], years))
        for (class_id, years), printed in PRINTED.items():
            counts = klass(class_id)['horizons'][years]['counts']
            for count, share in zip(counts, printed):
                self.assertAlmostEqual(100 * count / sum(counts), share, delta=.05 + 1e-9)

    def test_the_book_prints_one_decile_over_another_deciles_totals(self):
        # Exhibit 4 shows 17.7% for USD 325-700 million, 1 year, 0-5%: its own count over
        # the USD 0-325 million total. The counts over their own total are used.
        counts = klass('brb2016_325_700m')['horizons'][1]['counts']
        self.assertAlmostEqual(100 * counts[6] / 6243, 17.7, delta=.05)
        self.assertAlmostEqual(100 * counts[6] / sum(counts), 19.26, delta=.01)

    def test_bin_medians_reproduce_the_printed_medians(self):
        for item in base_rates.SALES_GROWTH_2016:
            for years, stats in item['horizons'].items():
                with self.subTest(item=item['id'], years=years):
                    median = base_rates._bin_quantile(.5, stats['counts'])
                    self.assertAlmostEqual(median, stats['median'], delta=.004)

    def test_size_classes_are_contiguous_and_the_2026_figures_are_as_printed(self):
        deciles = [item for item in base_rates.SALES_GROWTH_2016 if item['id'] != 'brb2016_above_50bn']
        self.assertEqual(deciles[0]['low'], 0.0)
        for lower, upper in zip(deciles, deciles[1:]):
            self.assertEqual(lower['high'], upper['low'])
        self.assertIsNone(deciles[-1]['high'])
        may, february = klass('bbr2026b_2_5bn'), klass('bbr2026_8_12bn')
        self.assertEqual(may['horizons'], {5: {'mean': .069, 'standard_deviation': .111, 'observations': 19264}})
        self.assertEqual(february['horizons'], {5: {'mean': .057, 'standard_deviation': .096, 'observations': 4385}})
        self.assertEqual((may['exhibit'], may['page'], february['exhibit'], february['page']),
                         ('Exhibit 1', 3, 'Exhibit 5', 6))
        self.assertEqual(base_rates.SOURCES['brb2016']['growth_basis'], 'real')
        self.assertEqual({base_rates.SOURCES[key]['growth_basis'] for key in ('bbr2026', 'bbr2026b')}, {'nominal'})

    def test_price_levels(self):
        self.assertEqual(base_rates.price_level(2015), 237.017)
        self.assertEqual(base_rates.price_level(2024), 313.689)
        self.assertAlmostEqual(base_rates.price_level(2027), 321.943 * 1.02 ** 2)
        self.assertAlmostEqual(base_rates.price_level(2013), 237.017 / 1.02 ** 2)


class ReferenceClasses(unittest.TestCase):
    def chosen(self, revenue, year, horizon):
        item, restated = base_rates.reference_class(revenue, year, horizon)
        return item['id'], restated

    def test_the_newest_table_covering_the_size_and_horizon_is_used(self):
        for horizon in (3, 5, 10):
            self.assertEqual(self.chosen(478.834e9, 2026, horizon)[0], 'brb2016_above_50bn')
        self.assertEqual(self.chosen(40e9, 2025, 10)[0], 'brb2016_above_25bn')
        # USD 3 billion of 2025 sales: the May 2026 class at 5 years, the 2016 deciles otherwise.
        self.assertEqual(self.chosen(3e9, 2025, 5)[0], 'bbr2026b_2_5bn')
        self.assertEqual(self.chosen(3e9, 2025, 3)[0], 'brb2016_2_3bn')
        self.assertEqual(self.chosen(10e9, 2024, 5)[0], 'bbr2026_8_12bn')
        self.assertEqual(self.chosen(10e9, 2024, 10)[0], 'brb2016_7_12bn')

    def test_starting_sales_are_restated_in_each_tables_dollars(self):
        # USD 13 billion of 2025 sales is USD 9.6 billion in 2015 dollars.
        class_id, restated = self.chosen(13e9, 2025, 3)
        self.assertEqual(class_id, 'brb2016_7_12bn')
        self.assertAlmostEqual(restated, 13e9 * 237.017 / 321.943)
        self.assertIsNone(base_rates.reference_class(0, 2025, 3))

    def test_other_currencies_and_missing_inputs_are_not_compared(self):
        self.assertEqual(base_rates.references(10e9, 'EUR', 2025)['status'], 'not_applicable')
        self.assertIn('US dollars', base_rates.references(10e9, 'EUR', 2025)['reason'])
        self.assertEqual(base_rates.references(None, 'USD', 2025)['status'], 'not_applicable')
        self.assertEqual(base_rates.references(10e9, 'USD', None)['status'], 'not_applicable')


class Percentiles(unittest.TestCase):
    def position(self, class_id, years, growth):
        item = klass(class_id)
        reference = base_rates._reference(item, 1.0, years)
        return base_rates.position_in(reference, item['horizons'][years], growth)

    def test_bins_interpolate_and_growth_is_restated_after_inflation(self):
        # 3.47% real, as Apple's base case: (452 below + 415 x 0.694) / 1099.
        position = self.position('brb2016_above_50bn', 10, 1.0347 * 1.02 - 1)
        self.assertAlmostEqual(position['compared_growth'], .0347)
        self.assertAlmostEqual(position['percentile'], 100 * (452 + 415 * .0347 / .05) / 1099)
        self.assertEqual(position['description'], '67th percentile')

    def test_tails_and_empty_bins(self):
        above = self.position('brb2016_above_50bn', 10, .25 * 1.02 + .02)
        self.assertTrue(above['above_all_observations'])
        self.assertEqual(above['description'], 'above every observation')
        tail = self.position('brb2016_0_325m', 3, .60)
        self.assertIsNone(tail['percentile'])
        self.assertAlmostEqual(tail['percentile_bounds'][0], 100 * (1 - 686 / 6057))
        self.assertEqual(tail['description'], 'above the 88th percentile')
        low = self.position('brb2016_above_50bn', 3, -.30)
        self.assertEqual(low['percentile_bounds'][0], 0.0)
        self.assertEqual(low['description'], 'below the 2nd percentile')
        near = self.position('brb2016_above_50bn', 10, 1.17 * 1.02 - 1)
        self.assertEqual(near['description'], 'above the 99th percentile')

    def test_normal_approximation_for_the_2026_tables(self):
        item = klass('bbr2026b_2_5bn')
        reference = base_rates._reference(item, 3e9, 5)
        self.assertEqual(reference['method'], 'normal')
        at_mean = base_rates.position_in(reference, item['horizons'][5], .069)
        self.assertAlmostEqual(at_mean['percentile'], 50.0)
        self.assertEqual(at_mean['compared_growth'], .069)
        one_up = base_rates.position_in(reference, item['horizons'][5], .069 + .111)
        self.assertAlmostEqual(one_up['percentile'], 84.13, places=2)
        self.assertAlmostEqual(one_up['z'], 1.0)
        self.assertAlmostEqual(reference['quantiles']['90'], .069 + .111 * 1.2815516, places=6)

    def test_ordinals(self):
        self.assertEqual([base_rates._ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 72, 99)],
                         ['1st', '2nd', '3rd', '4th', '11th', '12th', '13th', '21st', '22nd', '72nd', '99th'])


class PlanComparison(unittest.TestCase):
    def test_each_scenario_and_horizon_is_placed(self):
        value = usd_plan()
        result = pipeline._evaluate(value)
        comparison = result['base_rates']
        self.assertEqual(comparison['status'], 'computed')
        self.assertEqual(set(comparison['scenarios']), {'bear', 'base', 'bull'})
        for name, rows in comparison['scenarios'].items():
            self.assertEqual(sorted(rows, key=int), ['3', '5', '10'])
            for key, row in rows.items():
                flows = result['scenarios'][name]['cashflows']
                self.assertAlmostEqual(row['compound_growth'], (flows[int(key) - 1]['revenue'] / 100e9) ** (1 / int(key)) - 1)
        self.assertEqual(comparison['references']['10']['class_id'], 'brb2016_above_50bn')
        json.dumps(result, allow_nan=False)

    def test_the_price_requirement_path_matches_the_engine(self):
        for years in (10, 5):
            with self.subTest(years=years):
                value = usd_plan(years=years)
                value['market_price'] *= 2.5
                result = pipeline._evaluate(value)
                row = next(row for row in result['price_requirements']['rows'] if row['input'] == 'revenue_growth')
                path = row.get('needed_path') or row.get('at_path')
                requirement = result['base_rates']['price_requirement']
                self.assertEqual(requirement['status'], row['status'])
                self.assertAlmostEqual(requirement['horizons'][str(path['years'])]['compound_growth'], path['over_forecast'])

    def test_a_short_constant_rate_plan_leaves_longer_horizons_out(self):
        value = usd_plan(years=5, glide=False, timing=False)
        for scenario in value['scenarios'].values():
            scenario.update(wacc=.09, terminal_growth=.03)
        value.pop('discount_rate')
        comparison = pipeline._evaluate(value)['base_rates']
        self.assertEqual(sorted(comparison['scenarios']['base'], key=int), ['3', '5'])
        self.assertIn('n/a', '\n'.join(base_rates.memo_lines(comparison)))

    def test_no_gate_and_no_steering(self):
        # A plan far above every observation still values and passes the plan checks.
        value = usd_plan(growth=.4)
        result = pipeline._evaluate(value)
        self.assertEqual(result['base_rates']['scenarios']['base']['10']['description'], 'above every observation')
        pipeline._check_continuity(value, result)
        text = base_rates.plan_context({'actuals': value['actuals'], 'currency': 'USD', 'input_dates': value['input_dates']})
        text += base_rates.result_context(result) + '\n'.join(base_rates.memo_lines(result['base_rates']))
        self.assertNotRegex(text, r'(?i)\b(should|must|toward|aim|target|adjust)\b')
        self.assertNotIn('\N{EM DASH}', text)

    def test_apple_example(self):
        value = json.loads(APPLE.read_text())
        comparison = base_rates.compare(value, evaluate(value))
        self.assertEqual(comparison['fiscal_year'], 2026)
        self.assertEqual({row['class_id'] for row in comparison['references'].values()}, {'brb2016_above_50bn'})
        rounded = {name: {key: round(row['percentile']) for key, row in rows.items()}
                   for name, rows in comparison['scenarios'].items()}
        self.assertEqual(rounded, {'bear': {'3': 40, '5': 40, '10': 45}, 'base': {'3': 70, '5': 72, '10': 67},
                                   'bull': {'3': 85, '5': 86, '10': 84}})
        self.assertEqual(comparison['price_requirement']['horizons']['10']['description'], '99th percentile')


class Rendering(unittest.TestCase):
    def test_memo_exhibit_and_summary_cells(self):
        value = usd_plan()
        value['market_price'] *= 2.5
        result = pipeline._evaluate(value)
        section = pipeline.valuation_section(value, result)
        self.assertIn('### Revenue growth against base rates', section)
        exhibit = section[section.index('### Revenue growth against base rates'):]
        # Changed 2026-11-27 (memo redesign): cells are growth and percentile; real growth is in the notes.
        self.assertRegex(exhibit, r'\| Base \| 6\.0% nominal, 3\.9% real \(\d+(st|nd|rd|th)\) \|')
        self.assertIn('| Growth the price needs (+', exhibit)
        self.assertIn('restated after inflation at 2.0% a year', exhibit)
        self.assertIn('"The Base Rate Book: Integrating the Past to Better Anticipate the Future"', exhibit)
        self.assertIn('Exhibit 4, p25, with bin counts from its appendix, p32', exhibit)
        self.assertIn('the memo shows no margin percentile', exhibit)
        summary = pipeline.computed_summary(value, result)
        self.assertIn('| Base-rate percentile, 10 years |', summary)
        growth = next(line for line in summary.split('\n') if line.startswith('| Revenue growth, 10-year compound'))
        self.assertRegex(growth, r'house \d+(st|nd|rd|th), needed \d+(st|nd|rd|th) \|$')
        margin = next(line for line in summary.split('\n') if line.startswith('| EBIT margin, 10-year average'))
        self.assertNotIn('house', margin)

    def test_mixed_sources_cite_each_table_and_its_method(self):
        comparison = pipeline._evaluate(usd_plan(revenue=3e9))['base_rates']
        self.assertEqual(sorted(comparison['sources']), ['bbr2026b', 'brb2016'])
        lines = '\n'.join(base_rates.memo_lines(comparison))
        self.assertIn('- Base rates for 3 and 10 years: starting sales $2-3B in 2015 dollars', lines)
        self.assertIn('- Base rates for 5 years: starting sales $2-5B in 2025 dollars', lines)
        self.assertIn('normal approximation', lines)
        self.assertIn('"Bayes and Base Rates 2.0: How History Can Guide Our Assessment of the Future"', lines)
        self.assertIn('Exhibit 1, p3.', lines)
        self.assertIn('| 6.9%, 11.1% nominal |', lines)
        self.assertIn('19,264 at 5 years', lines)
        # Nominal growth is compared as it stands: 6% against a 6.9% mean.
        five = comparison['scenarios']['base']['5']
        self.assertAlmostEqual(five['compared_growth'], five['compound_growth'])
        self.assertAlmostEqual(five['z'], (five['compound_growth'] - .069) / .111)

    def test_other_currencies_say_why_no_table_is_shown(self):
        value = usd_plan()
        value['currency'] = value['discount_rate']['currency'] = 'EUR'
        result = pipeline._evaluate(value)
        lines = base_rates.memo_lines(result['base_rates'], 'EUR')
        self.assertIn('Not shown. The published size classes are in US dollars and this plan is in EUR.', lines)
        self.assertEqual(base_rates.result_context(result), '')
        self.assertEqual(base_rates.summary_note(result['base_rates'], 'house', 10), '')

    def test_prompt_context_for_the_plan_author(self):
        reported = {'actuals': {'revenue': 3e9}, 'currency': 'USD',
                    'input_dates': {'revenue': {'end_date': '2025-12-31'}}}
        text = base_rates.plan_context(reported)
        self.assertTrue(text.startswith('\nSales-growth base rates for companies of this starting size'))
        view = json.loads(text.split('\n')[2])
        self.assertEqual(view['horizons']['5']['source_short'], 'Bayes and Base Rates 2.0 (May 2026)')
        self.assertEqual(view['horizons']['3']['growth_basis'], 'real')
        self.assertIn('nominal_quantiles_at_stated_inflation', view['horizons']['10'])
        self.assertEqual(base_rates.plan_context({'currency': 'USD'}), '')


class PipelineStages(unittest.TestCase):
    """The plan author, the correction and the reviewer see the base rates; the memo prints them."""

    def setUp(self):
        # The mocked collection, router and input pins of the full-pipeline tests, with a dated revenue anchor.
        full.FullPipelineTests.setUp(self)
        dates = {'revenue': {'start_date': '2025-01-01', 'end_date': '2025-12-31'}}
        def canonical(sources, **kwargs):
            inputs = full.plan()
            return {'actuals': inputs['actuals'], 'currency': 'USD', 'source_refs': inputs['source_refs'],
                    'input_dates': copy.deepcopy(dates),
                    'coverage': {'complete': True, 'status': 'complete', 'conflicts': [], 'missing_fields': []}}
        def pinned(value, sources):
            result = copy.deepcopy(value)
            if not result.get('unavailable'):
                fixed = canonical(sources)
                result.setdefault('actuals', fixed['actuals'])
                result.setdefault('source_refs', fixed['source_refs'])
                result['input_dates'] = copy.deepcopy(dates)
                result['reported_inputs'] = fixed
            return result, []
        self.extra = [patch.object(pipeline, 'canonical_reported_inputs', side_effect=canonical),
                      patch.object(pipeline, 'pin_reported_inputs', side_effect=pinned)]
        for item in self.extra:
            item.start()

    def tearDown(self):
        for item in reversed(self.extra):
            item.stop()
        full.FullPipelineTests.tearDown(self)

    def test_every_model_stage_that_shapes_the_forecast_sees_the_base_rates(self):
        full.FakeRouter.revise = True
        prompts = {}
        original = full.FakeRouter.complete
        def capture(router, stage, model, prompt, **kwargs):
            prompts[stage] = (kwargs.get('prefix') or '') + prompt
            return original(router, stage, model, prompt, **kwargs)
        with tempfile.TemporaryDirectory() as directory, patch.object(full.FakeRouter, 'complete', capture):
            folder = pipeline.run('AAPL', 'synthetic', directory=directory)
            self.assertIn('Sales-growth base rates for companies of this starting size', prompts['valuation_plan'])
            self.assertIn('Revenue growth against base rates (published reference data', prompts['model_response_1'])
            self.assertIn('"base_rates"', prompts['review_0'])
            memo = (folder / 'memo.md').read_text()
            self.assertIn('### Revenue growth against base rates', memo)
            saved = json.loads((folder / 'valuation.json').read_text())
            self.assertEqual(saved['base_rates']['status'], 'computed')


if __name__ == '__main__':
    unittest.main()

"""Forecast consensus: several plan samples on one prompt, combined input by input, offline.

The unit tests build proposals by hand. The pipeline tests run the real Router
against a fake OpenRouter transport, so the reservations, cache session, stage
keys and the plan stage are the code paths a paid run uses; the run is stopped
at the first request after the plan stage.
"""
import copy
import json
import math
from pathlib import Path
import shutil
import statistics
import tempfile
import unittest
from unittest.mock import patch

from investmentideas import consensus, core, pipeline

import test_full_pipeline as full_pipeline
import test_plan_repair as plan_repair
import test_stab_sampling as sampling
from test_stab_sampling import REAL_ROUTER, FakeOpenRouter, sampled_plan

YEARS = 5


def proposal(*, growth=.1, services=.05, gross=.4, rnd=.08, sga=(.05, .04, 3), tax=.2, ratio=2.0,
             sectors=(('Computers/Peripherals', .7), ('Software (System & Application)', .3)), names=('products', 'services'),
             label='house'):
    """A gross-profit-less-expenses proposal as a model writes it."""
    first, target, year = sga
    scenario = {'components': {names[0]: {'growth_rates': [growth] * YEARS, 'gross_margins': [gross] * YEARS},
                               names[1]: {'growth_rates': [services] * YEARS, 'gross_margins': [.7] * YEARS}},
                'expense_shares': {'research_and_development': rnd,
                                   'selling_general_administrative': {'year1': first, 'target': target, 'target_year': year}}}
    return {'assumptions_label': 'House forecasts', 'scenario_rationales': {name: f'{label} {name} [S1]' for name in consensus.SCENARIOS},
            'scenarios': {name: {'probability': int(name == 'base'), 'tax_rate': tax, 'sales_to_capital': ratio}
                          for name in consensus.SCENARIOS},
            'operating_model': {'currency': 'USD', 'amount_unit': 'currency_units', 'earnings_mode': 'gross_profit_less_expenses',
                                'components': [{'name': names[0], 'base_revenue': 700, 'source_refs': ['S1']},
                                               {'name': names[1], 'base_revenue': 300, 'source_refs': ['S1']}],
                                'scenarios': {name: copy.deepcopy(scenario) for name in consensus.SCENARIOS}},
            'discount_rate': {'currency': 'USD', 'amount_unit': 'currency_units', 'as_of': '2026-11-27',
                              'unlevered_beta': {'calculation': 'bottom_up_sector_weighted',
                                                 'sectors': [{'sector': sector, 'weight': weight} for sector, weight in sectors],
                                                 'observed_at': '2026-01', 'source_refs': ['S8'], 'label': f'{label} mapping'},
                              'pretax_cost_of_debt': {'value': .056, 'observed_at': '2026-11-24', 'source_refs': ['S9'], 'label': 'proxy'},
                              'marginal_tax_rate': {'value': .25, 'observed_at': '2026-06-30', 'source_refs': ['S4'], 'label': 'statutory'}},
            'counterfactuals': {f'{label}_case': {'operating_overrides': {'components': {names[0]: {'growth_rates': [0] * YEARS}}}}}}


def base(plan):
    return plan['operating_model']['scenarios']['base']


class ConsensusInputTests(unittest.TestCase):
    def test_medians_of_every_numeric_forecast_input(self):
        plans = [proposal(growth=.10, gross=.40, rnd=.08, sga=(.05, .04, 3), tax=.20, ratio=2.0),
                 proposal(growth=.12, gross=.38, rnd=.10, sga=(.06, .06, 5), tax=.22, ratio={'year1': 1.0, 'target': 3.0, 'target_year': 5}),
                 proposal(growth=.20, gross=.45, rnd=.09, sga=(.04, .04, 1), tax=.18, ratio=[2.5] * YEARS)]
        plan, report = consensus.consensus_plan(copy.deepcopy(plans), method='median')
        scenario = base(plan)
        self.assertEqual(scenario['components']['products']['growth_rates'], [.12] * YEARS)
        self.assertEqual(scenario['components']['products']['gross_margins'], [.40] * YEARS)
        self.assertEqual(scenario['expense_shares']['research_and_development'], [.09] * YEARS)
        # year1/target/target_year shares are materialized to per-year values before the median.
        sga = [[.05, .045, .04, .04, .04], [.06] * YEARS, [.04] * YEARS]
        self.assertEqual(scenario['expense_shares']['selling_general_administrative'],
                         [round(statistics.median(year), 10) for year in zip(*sga)])
        ratios = [[2.0] * YEARS, [1.0, 1.5, 2.0, 2.5, 3.0], [2.5] * YEARS]
        self.assertEqual(plan['scenarios']['base']['sales_to_capital'], [statistics.median(year) for year in zip(*ratios)])
        self.assertEqual(plan['scenarios']['base']['tax_rate'], .20)
        self.assertEqual(plan['scenarios']['base']['probability'], 1)
        self.assertEqual(plan['discount_rate']['pretax_cost_of_debt']['value'], .056)
        self.assertEqual(report['structure_decision']['decision'], 'all_samples_share_a_structure')
        self.assertNotIn('varied_most', report)
        # Text comes from one sample, the representative, and the inputs are unchanged.
        rep = plans[report['representative_sample'] - 1]
        # Labelled as written with that forecast; the medians are the valued inputs.
        self.assertEqual(plan['scenario_rationales'],
                         {name: text + f'. Written with forecast {report["representative_sample"]} of 3; the valued '
                                       'inputs are the medians in the tables.'
                          for name, text in rep['scenario_rationales'].items()})
        self.assertEqual(plan['counterfactuals'], rep['counterfactuals'])

    def test_the_default_averages_the_paths_and_takes_the_median_industry_weight(self):
        plans = [proposal(growth=.10, tax=.20), proposal(growth=.12, tax=.21), proposal(growth=.20, tax=.25)]
        plan, report = consensus.consensus_plan(plans)
        self.assertEqual((report['method'], report['sector_weight_method']), ('mean', 'median'))
        self.assertEqual(base(plan)['components']['products']['growth_rates'], [round(.42 / 3, 10)] * YEARS)
        self.assertEqual(plan['scenarios']['bull']['tax_rate'], round(.66 / 3, 10))

    def test_a_constant_sales_to_capital_stays_one_ratio(self):
        plan, _ = consensus.consensus_plan([proposal(ratio=1.5), proposal(ratio=2.0), proposal(ratio=3.0)], method='median')
        self.assertEqual(plan['scenarios']['base']['sales_to_capital'], 2.0)

    def test_an_outlier_industry_mapping_is_dropped_and_the_weights_renormalized(self):
        plans = [proposal(sectors=(('Computers/Peripherals', .7), ('Software (System & Application)', .3))),
                 proposal(sectors=(('Computers/Peripherals', .74), ('Software (System & Application)', .26))),
                 proposal(sectors=(('Telecom. Equipment', .5), ('Computers/Peripherals', .2), ('Software (System & Application)', .3)))]
        plan, report = consensus.consensus_plan(plans)
        sectors = plan['discount_rate']['unlevered_beta']['sectors']
        self.assertEqual([item['sector'] for item in sectors], ['Computers/Peripherals', 'Software (System & Application)'])
        self.assertTrue(math.isclose(sum(item['weight'] for item in sectors), 1, abs_tol=1e-12))
        self.assertAlmostEqual(sectors[0]['weight'], .7 / (.7 + .3))
        self.assertTrue(any('Telecom. Equipment' in note for note in report['notes']))
        self.assertIn('median across 3 forecast samples', plan['discount_rate']['unlevered_beta']['label'])
        # A shared industry with a sample's weight of zero is averaged in at zero by the median too.
        plans[1]['discount_rate']['unlevered_beta']['sectors'].append({'sector': 'Advertising', 'weight': .1})
        plan, _ = consensus.consensus_plan(plans)
        self.assertNotIn('Advertising', [item['sector'] for item in plan['discount_rate']['unlevered_beta']['sectors']])

    def test_one_sample_returns_unchanged_and_two_are_averaged_with_a_note(self):
        single = proposal()
        plan, report = consensus.consensus_plan([copy.deepcopy(single)])
        self.assertEqual(plan, single)
        self.assertEqual(report['method'], 'single_sample')
        plan, report = consensus.consensus_plan([proposal(growth=.1, tax=.2), proposal(growth=.2, tax=.3)], method='median')
        self.assertEqual(base(plan)['components']['products']['growth_rates'], [.15] * YEARS)
        self.assertEqual(plan['scenarios']['base']['tax_rate'], .25)
        self.assertTrue(any('their mean' in note for note in report['notes']))

    def test_a_left_out_expense_line_holds_its_reported_share_as_the_lock_would(self):
        plans = [proposal(rnd=.08), proposal(rnd=.10), proposal(rnd=.12)]
        del base(plans[2])['expense_shares']['research_and_development']
        plan, _ = consensus.consensus_plan(plans, method='median', held_shares={'research_and_development': .07})
        self.assertEqual(base(plan)['expense_shares']['research_and_development'], [.08] * YEARS)

    def test_the_representative_is_the_sample_closest_to_the_median_paths(self):
        plans = [proposal(growth=.05, label='low'), proposal(growth=.10, label='middle'), proposal(growth=.30, label='high')]
        plan, report = consensus.consensus_plan(plans)
        self.assertEqual(report['representative_sample'], 2)
        self.assertTrue(plan['scenario_rationales']['base'].startswith('middle base [S1]. Written with forecast 2 of 3'))
        self.assertIn('middle_case', plan['counterfactuals'])

    def test_input_order_and_numbering_do_not_change_the_numbers(self):
        plans = [proposal(growth=.05), proposal(growth=.10), proposal(growth=.30)]
        one, _ = consensus.consensus_plan(copy.deepcopy(plans))
        two, report = consensus.consensus_plan(copy.deepcopy(plans[::-1]), numbers=[7, 5, 2])
        self.assertEqual(base(one), base(two))
        self.assertEqual(report['representative_sample'], 5)


class StructureTests(unittest.TestCase):
    def test_the_largest_group_sharing_a_structure_is_combined(self):
        plans = [proposal(growth=.1), proposal(growth=.2, names=('hardware', 'services')), proposal(growth=.3)]
        plan, report = consensus.consensus_plan(plans, method='median')
        self.assertEqual(report['structure_decision']['group'], [1, 3])
        self.assertEqual(report['structure_decision']['excluded'], [2])
        self.assertEqual(base(plan)['components']['products']['growth_rates'], [.2] * YEARS)
        self.assertTrue(any('different structure' in note for note in report['notes']))

    def test_no_agreement_uses_the_single_sample_closest_to_the_median_paths(self):
        plans = [proposal(growth=.05), proposal(growth=.11, names=('hardware', 'services')),
                 proposal(growth=.40, names=('devices', 'services'))]
        plans[0]['operating_model']['earnings_mode'] = 'gross_profit_less_expenses'
        plan, report = consensus.consensus_plan(copy.deepcopy(plans))
        self.assertEqual(report['method'], 'closest_single_sample')
        self.assertEqual(report['representative_sample'], 2)
        self.assertEqual(plan, plans[1])
        self.assertTrue(any('No two of the 3 samples share a structure' in note for note in report['notes']))

    def test_a_different_horizon_is_a_different_structure(self):
        short = proposal()
        for scenario in short['operating_model']['scenarios'].values():
            for item in scenario['components'].values():
                item['growth_rates'] = item['growth_rates'][:4]
                item['gross_margins'] = item['gross_margins'][:4]
        _, report = consensus.consensus_plan([proposal(), short, proposal()])
        self.assertEqual(report['structure_decision']['excluded'], [2])


class LockTests(unittest.TestCase):
    """The consensus passes the plan's own checks, borrows a missing basis, or falls back."""

    def plans(self):
        plans = [proposal(tax=.20, label='a'), proposal(tax=.22, label='b'), proposal(tax=.30, label='c')]
        plans[2]['margin_continuity_basis'] = {'base': 'Year-1 margin steps down on the cited depreciation [S2].'}
        return plans

    @staticmethod
    def result(plan):
        tax = plan['scenarios']['base']['tax_rate']
        return {'scenarios': {name: {'per_share_value': 100 * (1 - tax)} for name in consensus.SCENARIOS}}

    def test_a_consensus_that_passes_is_used_and_its_values_reported(self):
        plans = self.plans()
        plan, report = consensus.consensus_plan(copy.deepcopy(plans), valuations=[self.result(p) for p in plans])
        used, payload = consensus.lock_consensus(plan, report, lambda item: (self.result(item), 'locked'), plans)
        self.assertEqual((used, payload, report['plan_used']), (plan, 'locked', 'consensus'))
        self.assertAlmostEqual(report['consensus_values']['base'], 100 * (1 - .24))
        self.assertEqual(report['sample_value_range']['base']['min'], 70)
        self.assertEqual(report['bases_from_other_samples'] if 'bases_from_other_samples' in report else [], [])

    def test_a_missing_basis_is_taken_from_a_sample_that_has_one(self):
        plans = self.plans()

        def lock(item):
            if item['scenarios']['base']['tax_rate'] > .23 and not (item.get('margin_continuity_basis') or {}).get('base'):
                raise ValueError('Year-1 EBIT margin breaks from the reported margin in: base')
            return self.result(item), item
        plan, report = consensus.consensus_plan(copy.deepcopy(plans), valuations=[self.result(p) for p in plans])
        self.assertNotIn('margin_continuity_basis', plan)
        used, _ = consensus.lock_consensus(plan, report, lock, plans)
        self.assertEqual(used['margin_continuity_basis']['base'], plans[2]['margin_continuity_basis']['base'] +
                         ' Written with forecast 3 of 3; the valued inputs are the averages in the tables.')
        self.assertEqual(report['bases_from_other_samples'], [{'field': 'margin_continuity_basis', 'scenario': 'base', 'sample': 3}])
        self.assertEqual(report['plan_used'], 'consensus')

    def test_a_consensus_failing_a_check_the_samples_passed_falls_back_to_the_representative(self):
        plans = self.plans()

        def lock(item):
            if math.isclose(item['scenarios']['base']['tax_rate'], .24):
                raise ValueError('Final-year EBIT margin exceeds the house ceiling')
            return self.result(item), item
        plan, report = consensus.consensus_plan(copy.deepcopy(plans), valuations=[self.result(p) for p in plans])
        used, _ = consensus.lock_consensus(plan, report, lock, plans)
        self.assertEqual(report['plan_used'], 'representative_sample_fallback')
        self.assertEqual(used, plans[report['representative_sample'] - 1])
        self.assertIn('house ceiling', report['fallback']['reason'])
        self.assertIn('failed a forecast check', consensus.method_note(report))

    def test_method_note(self):
        plans = self.plans()
        plan, report = consensus.consensus_plan(copy.deepcopy(plans), valuations=[self.result(p) for p in plans])
        consensus.lock_consensus(plan, report, lambda item: (self.result(item), None), plans)
        self.assertEqual(consensus.method_note(report, 'USD'),
                         'The forecast averages three forecasts written independently from the same evidence, input by '
                         'input, with the median of their industry weights for the beta; their base values ranged from '
                         '70.00 to 80.00 USD a share.')


class TextFigureTests(unittest.TestCase):
    """M3: a rationale from one sample states that sample's inputs; the combined figures replace them where unambiguous."""

    def plans(self):
        plans = [proposal(tax=.19, ratio={'year1': .45, 'target': 1.2, 'target_year': 5}, growth=.10, label='a'),
                 proposal(tax=.20, ratio={'year1': .50, 'target': 1.3, 'target_year': 5}, growth=.12, label='b'),
                 proposal(tax=.24, ratio={'year1': .58, 'target': 1.1, 'target_year': 5}, growth=.11, label='c')]
        for plan in plans:
            tax = plan['scenarios']['base']['tax_rate']
            first = plan['scenarios']['base']['sales_to_capital']['year1']
            plan['scenario_rationales']['base'] = (
                f'Products grow {plan["operating_model"]["scenarios"]["base"]["components"]["products"]["growth_rates"][0]:.0%} '
                f'a year as demand broadens. The {tax:.0%} tax rate sits between cash taxes paid of 20% [S4] and the '
                f'statutory rate. Sales-to-capital rises from {first:.2f} as the build-out ends.')
        return plans

    def test_unambiguous_figures_take_the_combined_input_and_cited_figures_stay(self):
        plans = self.plans()
        plan, report = consensus.consensus_plan(copy.deepcopy(plans), method='median')
        self.assertEqual(report['representative_sample'], 3)
        text = plan['scenario_rationales']['base']
        # Sample 3 wrote 11%, 24% and 0.58; the medians are 11%, 20% and 0.50. The cited 20% stays.
        self.assertIn('Products grow 11% a year', text)
        self.assertIn('The 20% tax rate sits between cash taxes paid of 20% [S4]', text)
        self.assertIn('Sales-to-capital rises from 0.50 as', text)
        self.assertTrue(text.endswith('Written with forecast 3 of 3; the valued inputs are the medians in the tables.'))
        changed = {(row['from'], row['to']) for row in report['text_figures']['changed']}
        self.assertEqual(changed, {('24%', '20%'), ('0.58', '0.50')})

    def test_an_ambiguous_figure_is_left_and_recorded(self):
        own = proposal(tax=.20, growth=.20)
        combined = proposal(tax=.22, growth=.25)
        text, changes, left = consensus.align_figures('Both stay near 20% for the decade.', own, combined, 'base')
        self.assertEqual(text, 'Both stay near 20% for the decade.')
        self.assertEqual(changes, [])
        self.assertEqual(left[0]['reason'], 'ambiguous')

    def test_amounts_years_and_integers_are_not_inputs(self):
        own, combined = proposal(ratio=2.0), proposal(ratio=3.0)
        text = 'Capex of $2.0bn over 2 years, or 2.0 billion, is not the 2.0 ratio.'
        new, changes, _ = consensus.align_figures(text, own, combined, 'base')
        self.assertEqual(new, 'Capex of $2.0bn over 2 years, or 2.0 billion, is not the 3.0 ratio.')
        self.assertEqual(len(changes), 1)

    def test_the_plan_prompt_asks_for_evidence_and_direction_without_input_figures(self):
        self.assertIn('Do not restate forecast input figures', pipeline.FORECAST_SCHEMA)
        self.assertEqual(pipeline.UNDERWRITING_GUIDANCE.count("without restating the plan's input figures") +
                         pipeline.UNDERWRITING_GUIDANCE.count("without the plan's input figures"), 2)
        self.assertIn('does not restate forecast input figures', pipeline.UNDERWRITING_GUIDANCE)


class FallbackTests(unittest.TestCase):
    """m1, m4, m7: an uncombinable set falls back to the closest sample; the value-impact step cannot stop a run."""

    def test_no_positive_median_industry_weight_uses_the_mean_weights(self):
        plans = [proposal(sectors=((sector, 1.0),)) for sector in
                 ('Software (System & Application)', 'Computer Services', 'Semiconductor')]
        plan, report = consensus.consensus_plan(plans)
        weights = {item['sector']: item['weight'] for item in plan['discount_rate']['unlevered_beta']['sectors']}
        self.assertEqual(len(weights), 3)
        self.assertTrue(all(math.isclose(weight, 1 / 3) for weight in weights.values()))
        self.assertTrue(any('mean industry weights' in note for note in report['notes']))
        self.assertIn('mean across 3 forecast samples', plan['discount_rate']['unlevered_beta']['label'])

    def test_samples_that_cannot_be_combined_give_the_closest_sample_and_a_true_note(self):
        plans = [proposal(growth=.05), proposal(growth=.10), proposal(growth=.30)]
        with patch.object(consensus, '_merge', side_effect=ValueError('synthetic: cannot combine')):
            plan, report = consensus.consensus_plan(copy.deepcopy(plans))
        self.assertEqual(report['method'], 'uncombinable_closest_sample')
        self.assertEqual(plan, plans[1])
        report.update(requested_samples=3, dropped=[], plan_used='uncombinable_closest_sample')
        for row, value in zip(report['samples'], (90, 100, 140)):
            row['values'] = {'base': value}
        note = consensus.method_notes(report, pipeline.memo_style.Style('USD'))[0]
        self.assertIn('their inputs could not be combined', note)
        self.assertIn('per-year median revenue and EBIT paths', note)
        self.assertIn('$90.0 to $140', note)
        self.assertNotIn('one passed validation', note)
        self.assertIn('their inputs could not be combined', consensus.method_note(report))
        self.assertIsNone(consensus.first_page_sentence(report, pipeline.memo_style.Style('USD')))

    def test_the_method_note_names_the_median_paths_the_code_uses(self):
        record = ReaderTests.record(None)
        note = consensus.method_notes(record, pipeline.memo_style.Style('USD'))[0]
        self.assertIn('closest to the per-year median revenue and EBIT paths', note)
        self.assertNotIn('average path', note)
        self.assertIn('per-year median', consensus._closest.__doc__)


class StopAfterPlan(Exception):
    """The fake transport reached the first request after the plan stage."""


class Transport(FakeOpenRouter):
    """Plan answers in order; synthesis and web research answered; the draft stops the run."""

    def __call__(self, url, *, key=None, payload=None, timeout=90):
        if payload is not None and 'Write the prose' in payload['messages'][1]['content']:
            raise StopAfterPlan()
        return super().__call__(url, key=key, payload=payload, timeout=timeout)


def taxed(tax, growth=.04):
    return sampled_plan(growth=growth, tax=tax)


class PipelineSamplingTests(unittest.TestCase):
    def setUp(self):
        full_pipeline.FullPipelineTests.setUp(self)
        router = patch.object(core, 'Router', REAL_ROUTER)
        router.start()
        self.patches.append(router)
        self.directory = tempfile.TemporaryDirectory()
        self.messages = []

    def tearDown(self):
        full_pipeline.FullPipelineTests.tearDown(self)
        self.directory.cleanup()

    def run_plan(self, transport, samples, *, budget=20, fresh=False):
        if fresh:
            shutil.rmtree(Path(self.directory.name) / 'AAPL', ignore_errors=True)
        with patch.object(core, 'request_json', transport):
            with self.assertRaises(StopAfterPlan):
                pipeline.run('AAPL', 'synthetic', directory=self.directory.name, budget=budget, plan_samples=samples,
                             progress=self.messages.append)
        folder = next((Path(self.directory.name) / 'AAPL').glob('full-*'))
        return folder, json.loads((folder / 'state.json').read_text())

    def test_samples_send_identical_prompt_bytes_as_separate_requests_in_one_session(self):
        transport = Transport([taxed(.20), taxed(.22), taxed(.30)])
        folder, state = self.run_plan(transport, 3)
        plans = transport.plan_payloads()
        self.assertEqual(len(plans), 3)
        self.assertTrue(all(item['messages'] == plans[0]['messages'] for item in plans))
        self.assertEqual(len({item.get('session_id') for item in transport.payloads}), 1)
        # Samples 2 and 3 read the prefix sample 1 wrote, and were reserved at the cache-read price.
        rows = [row for row in state['ledger'] if row['stage'].startswith('valuation_plan')]
        self.assertEqual([row['stage'] for row in rows], ['valuation_plan', 'valuation_plan_s2', 'valuation_plan_s3'])
        self.assertNotIn('reserved_at_cache_read', rows[0])
        self.assertTrue(all(row.get('reserved_at_cache_read') for row in rows[1:]))
        prefix = rows[0]['cached_prefix_utf8_bytes']
        for row in rows[1:]:
            # The prefix is reserved at the cache-read price instead of the cache-write price.
            self.assertAlmostEqual(rows[0]['reserved_usd'] - row['reserved_usd'], prefix * (.000008 - .0000002))
        self.assertTrue(all(row['usage']['prompt_tokens_details']['cached_tokens'] > 0 for row in rows[1:]))
        preflight = json.loads((folder / 'budget-preflight.json').read_text())
        later = [row for row in preflight['stages'] if row['stage'] in ('valuation_plan_s2', 'valuation_plan_s3')]
        self.assertEqual([row.get('prefix_reserved_at') for row in later], ['cache_read', 'cache_read'])
        first = next(row for row in preflight['stages'] if row['stage'] == 'valuation_plan')
        self.assertTrue(all(row['reservation_floor_usd'] < first['reservation_floor_usd'] for row in later))
        # The consensus is the plan valued, and its record is saved beside it.
        self.assertAlmostEqual(state['plan']['scenarios']['base']['tax_rate'], .24)
        report = json.loads((folder / 'plan-consensus.json').read_text())
        self.assertEqual(report['plan_used'], 'consensus')
        self.assertEqual([row['sample'] for row in report['samples']], [1, 2, 3])
        self.assertEqual(report['requested_samples'], 3)
        valuation = json.loads((folder / 'valuation.json').read_text())
        record = valuation['forecast_consensus']
        self.assertEqual(record['plan_used'], 'consensus')
        self.assertNotIn('edited_after_consensus', record)
        self.assertAlmostEqual(record['consensus_values']['base'], valuation['scenarios']['base']['per_share_value'])
        # Every plan request sent the one frozen evidence block, recorded under one ledger field.
        digests = {row['cached_prefix_sha256'] for row in rows}
        self.assertEqual(len(digests), 1)
        self.assertEqual(len(next(iter(digests))), 16)
        self.assertEqual(state['cached_evidence']['source_ids'], [source['id'] for source in state['sources']])
        prefixes = {item['messages'][0]['content'][1]['text'] for item in plans}
        self.assertEqual(len(prefixes), 1)
        self.assertEqual(core.prefix_digest(next(iter(prefixes))), next(iter(digests)))
        # Value impact: each sample's driver groups swapped into the plan valued, ranked by range.
        impact = report['value_impact']['groups']
        self.assertEqual(sorted(row['group'] for row in impact), sorted(pipeline.consensus.drivers.DRIVER_GROUPS))
        self.assertEqual([row['range'] for row in impact], sorted((row['range'] for row in impact), reverse=True))
        self.assertEqual(impact[0]['group'], 'tax')
        self.assertEqual([row['group'] for row in record['value_impact']], [row['group'] for row in impact])
        # The whole run priced together: the samples, the first review and both revision rounds.
        self.assertAlmostEqual(preflight['worst_case_reservation_floor_usd'], preflight['known_context_reservation_floor_usd'] +
                               sum(item['usual_floor_usd'] + item['conditional_floor_usd'] for item in preflight['revision_rounds']))
        self.assertEqual(preflight['plan_samples'], 3)

    def test_each_sample_has_its_own_stage_key_and_a_resume_pays_for_nothing(self):
        transport = Transport([taxed(.20), taxed(.22), taxed(.30)])
        folder, state = self.run_plan(transport, 3)
        keys = [name for name in state['stages'] if name.startswith('valuation_plan')]
        self.assertEqual([name.split(':')[0] for name in keys], ['valuation_plan', 'valuation_plan_s2', 'valuation_plan_s3'])
        # The keys differ by the sample in the stage name; the request digest after it is the same.
        self.assertEqual(len(set(keys)), 3)
        self.assertEqual(len({name.split(':')[1] for name in keys}), 1)
        self.assertEqual([pipeline._sample_of_stage(name) for name in keys], [1, 2, 3])
        self.assertEqual(pipeline._sample_of_stage('valuation_plan_s2_repair_3:abc'), 2)
        self.assertEqual(pipeline._sample_of_stage('valuation_plan_repair:abc'), 1)
        self.assertIsNone(pipeline._sample_of_stage('full_draft:abc'))
        # The plan is in state; drop it to show the samples themselves are reused from their keys.
        state.pop('plan')
        state.pop('valuation')
        core.save(folder / 'state.json', state)
        before = len(transport.plan_payloads())
        self.run_plan(transport, 3)
        self.assertEqual(len(transport.plan_payloads()), before)

    def test_a_sample_still_failing_after_its_repairs_is_dropped_and_the_run_continues(self):
        transport = Transport([taxed(.20), plan_repair.invalid_plan(), plan_repair.invalid_plan(), taxed(.30)])
        folder, state = self.run_plan(transport, 3)
        report = state['plan_consensus']
        self.assertEqual(report['dropped'][0]['sample'], 2)
        self.assertIn('unchanged', report['dropped'][0]['reason'])
        self.assertEqual([row['sample'] for row in report['samples']], [1, 3])
        self.assertAlmostEqual(state['plan']['scenarios']['base']['tax_rate'], .25)
        self.assertTrue(any('their mean' in note for note in report['notes']))
        self.assertIn('was left out', report['method_note'])
        style = pipeline.memo_style.Style('USD')
        notes = consensus.method_notes(state['valuation']['forecast_consensus'], style)
        self.assertIn('One of the three forecasts requested failed validation', notes[0])
        self.assertIn('averaged year by year', notes[0])
        # Its prompt stayed the shared one: no repair reason was written into the plan prompt.
        self.assertNotIn('plan_repair_reasons', state)
        # The in-progress log is cleared once sampling ends; the report keeps the drop.
        self.assertNotIn('plan_sample_log', state)

    def test_every_sample_failing_stops_the_run_and_discards_the_plans(self):
        invalid = [plan_repair.invalid_plan()] * 2
        transport = Transport(invalid * 2)
        with patch.object(core, 'request_json', transport), self.assertRaisesRegex(RuntimeError, 'All 2 forecast plan samples'):
            pipeline.run('AAPL', 'synthetic', directory=self.directory.name, plan_samples=2)
        folder = next((Path(self.directory.name) / 'AAPL').glob('full-*'))
        state = json.loads((folder / 'state.json').read_text())
        self.assertFalse([name for name in state['stages'] if name.startswith('valuation_plan')])
        self.assertEqual(len(state['plan_repair_reasons']), 2)

    def test_a_consensus_failing_a_check_falls_back_to_the_representative_sample(self):
        original = pipeline._lock_and_value

        def edge(candidate, state, **kwargs):
            if math.isclose(candidate['scenarios']['base'].get('tax_rate', 0), .24):
                raise ValueError('Synthetic edge: the combined plan breaks a continuity band')
            return original(candidate, state, **kwargs)
        transport = Transport([taxed(.20, .03), taxed(.22, .04), taxed(.30, .05)])
        with patch.object(pipeline, '_lock_and_value', side_effect=edge):
            folder, state = self.run_plan(transport, 3)
        report = state['plan_consensus']
        self.assertEqual(report['plan_used'], 'representative_sample_fallback')
        self.assertIn('Synthetic edge', report['fallback']['reason'])
        self.assertEqual(report['representative_sample'], 2)
        self.assertAlmostEqual(state['plan']['scenarios']['base']['tax_rate'], .22)

    def test_an_uncombinable_set_values_the_closest_valid_sample(self):
        transport = Transport([taxed(.20, .03), taxed(.22, .04), taxed(.30, .05)])
        with patch.object(consensus, 'consensus_plan', side_effect=ValueError('synthetic: cannot combine')):
            folder, state = self.run_plan(transport, 3)
        report = state['plan_consensus']
        self.assertEqual((report['method'], report['plan_used']), ('uncombinable_closest_sample',) * 2)
        self.assertEqual(report['representative_sample'], 2)
        self.assertAlmostEqual(state['plan']['scenarios']['base']['tax_rate'], .22)
        self.assertIn('could not be combined', report['method_note'])

    def test_a_failing_value_impact_step_is_recorded_and_the_run_continues(self):
        transport = Transport([taxed(.20), taxed(.22), taxed(.30)])
        with patch.object(pipeline.consensus.drivers, 'decompose', side_effect=RuntimeError('synthetic swap failure')):
            folder, state = self.run_plan(transport, 3)
        impact = state['plan_consensus']['value_impact']
        self.assertIn('synthetic swap failure', impact['error'])
        self.assertEqual(impact['groups'], [])
        self.assertAlmostEqual(state['plan']['scenarios']['base']['tax_rate'], .24)
        with patch.object(pipeline.consensus, 'value_impact', side_effect=MemoryError('synthetic')):
            folder, state = self.run_plan(Transport([taxed(.20), taxed(.22), taxed(.30)]), 3, fresh=True)
        self.assertIn('MemoryError', state['plan_consensus']['value_impact']['error'])

    def test_one_sample_is_todays_single_plan_stage(self):
        transport = Transport([taxed(.20)])
        folder, state = self.run_plan(transport, 1)
        self.assertEqual(len(transport.plan_payloads()), 1)
        self.assertNotIn('plan_consensus', state)
        self.assertFalse((folder / 'plan-consensus.json').exists())
        self.assertNotIn('forecast_consensus', json.loads((folder / 'valuation.json').read_text()))
        self.assertFalse([row for row in json.loads((folder / 'budget-preflight.json').read_text())['stages']
                          if row['stage'].startswith('valuation_plan_s')])


class ReplayConsensusTests(unittest.TestCase):
    """tools/replay.py: sampling reports the consensus; --consensus-only rebuilds it with no request."""

    def setUp(self):
        sampling.PlanSamplingTests.setUp(self)

    def tearDown(self):
        sampling.PlanSamplingTests.tearDown(self)

    def test_sampling_reports_the_consensus_and_consensus_only_makes_no_request(self):
        transport = FakeOpenRouter([taxed(.20), taxed(.22), taxed(.30)])
        report = sampling.PlanSamplingTests.sample(self, transport, 3)
        rows = report['consensus']['consensus']
        self.assertEqual([row['samples'] for row in rows], [[1, 2, 3]])
        self.assertEqual(rows[0]['plan_used'], 'consensus')
        values = [row['metrics']['per_share_value']['base'] for row in report['samples']]
        self.assertTrue(min(values) < rows[0]['values']['base'] < max(values))
        requests = len(transport.payloads)
        with patch.object(core, 'request_json', side_effect=AssertionError('no request')):
            result = sampling.replay.consensus_only(self.saved, Path(self.directory.name) / 'out', samples=2,
                                                    progress=self.messages.append)
        self.assertEqual(len(transport.payloads), requests)
        self.assertEqual([row['samples'] for row in result['consensus']], [[1, 2]])
        self.assertTrue((Path(self.directory.name) / 'out' / 'AAPL' / 'plan-samples' / 'plan-consensus-check.json').exists())


class ReaderTests(unittest.TestCase):
    """The range on the first page and in the Method notes, and the record after a forecast correction."""

    def record(self, **extra):
        record = {'method': 'mean', 'sector_weight_method': 'median', 'requested_samples': 3, 'sample_count': 3,
                  'plan_used': 'consensus', 'structure_decision': {'group': [1, 2, 3], 'excluded': []}, 'dropped': [],
                  'samples': [{'sample': n, 'in_consensus': True, 'values': {'base': value}}
                              for n, value in ((1, 354.12), (2, 387.55), (3, 398.24))]}
        record.update(extra)
        return record

    def test_first_page_sentence_in_the_memo_number_style(self):
        style = pipeline.memo_style.Style('USD')
        self.assertEqual(consensus.first_page_sentence(self.record(), style),
                         'The base case averages three forecasts written from the same evidence; their values ranged '
                         'from $354 to $398.')
        edited = consensus.first_page_sentence(self.record(edited_after_consensus=True), style)
        self.assertEqual(edited, 'The forecast began as the average of three forecasts written from the same evidence, '
                                 'valued at $354 to $398, and was revised after review.')
        for record in (None, self.record(plan_used='representative_sample_fallback'),
                       self.record(plan_used='single_valid_sample', structure_decision={'group': [1]})):
            self.assertIsNone(consensus.first_page_sentence(record, style))

    def test_one_method_note_with_the_method_count_and_drops(self):
        style = pipeline.memo_style.Style('USD')
        notes = consensus.method_notes(self.record(dropped=[{'sample': 4, 'reason': 'x'}], requested_samples=4), style)
        self.assertEqual(len(notes), 1)
        # Changed 2026-11-27 (memo audit): the note counts the forecasts combined against those written.
        self.assertIn('combines three of the four forecasts', notes[0])
        self.assertIn('industry weights behind the beta take the median', notes[0])
        self.assertIn('One of the four forecasts requested failed validation', notes[0])
        self.assertIn('$354 to $398 a share', notes[0])
        self.assertEqual(consensus.method_notes(None, style), [])
        self.assertEqual(consensus.method_notes({'requested_samples': 1}, style), [])
        self.assertEqual(len(consensus.method_notes(self.record(edited_after_consensus=True), style)), 2)


class CorrectionTests(unittest.TestCase):
    """A forecast correction after review marks the record edited; a saved run without a record renders as before."""

    setUp = PipelineSamplingTests.setUp
    tearDown = PipelineSamplingTests.tearDown
    run_plan = PipelineSamplingTests.run_plan

    def test_a_corrected_plan_is_marked_edited_and_the_range_is_past_tense(self):
        folder, state = self.run_plan(Transport([taxed(.20), taxed(.22), taxed(.30)]), 3)
        plan, result = state['plan'], state['valuation']
        summary = pipeline.computed_summary(plan, result)
        self.assertIn('The base case averages three forecasts written from the same evidence; their values ranged from',
                      summary)
        corrected = copy.deepcopy(plan)
        corrected['scenarios']['base']['tax_rate'] = .27
        value = pipeline._evaluate(corrected)
        pipeline._attach_consensus(state, corrected, value)
        record = value['forecast_consensus']
        self.assertTrue(record['edited_after_consensus'])
        self.assertAlmostEqual(record['current_values']['base'], value['scenarios']['base']['per_share_value'])
        text = pipeline.computed_summary(corrected, value)
        self.assertIn('and was revised after review.', text)
        self.assertNotIn('their values ranged from', text)
        memo = pipeline._render('# Prose\nText [S1].\n', state['sources'], [], corrected, value, state)
        self.assertIn('**Forecast samples**', memo)
        self.assertIn('A forecast correction after review then edited the plan valued.', memo)
        # Relocking the unchanged plan, as every later run does, is not an edit.
        relocked, _ = pipeline._lock_plan(plan, state['sources'])
        self.assertEqual(pipeline.consensus.fingerprint(relocked), state['plan_consensus']['plan_fingerprint'])
        # A run saved before the record existed shows neither.
        result.pop('forecast_consensus')
        old = pipeline._render('# Prose\nText [S1].\n', state['sources'], [], plan, result, {'ticker': 'AAPL'})
        self.assertNotIn('forecasts written from the same evidence', old)
        self.assertNotIn('Forecast samples', old)


class SettingsTests(unittest.TestCase):
    def test_profile_defaults_and_limits(self):
        self.assertEqual(pipeline.default_plan_samples('balanced'), 3)
        self.assertEqual(pipeline.default_plan_samples('dev'), 1)
        for bad in (0, 6, True, 2.0):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'plan_samples'):
                pipeline.run('AAPL', 'synthetic', plan_samples=bad)

    def test_stage_names(self):
        self.assertEqual([pipeline._plan_stage_name(1), pipeline._plan_stage_name(1, 0), pipeline._plan_stage_name(1, 1)],
                         ['valuation_plan', 'valuation_plan_repair', 'valuation_plan_repair_2'])
        self.assertEqual([pipeline._plan_stage_name(3), pipeline._plan_stage_name(3, 2)],
                         ['valuation_plan_s3', 'valuation_plan_s3_repair_3'])

    def test_cli_option(self):
        from investmentideas import __main__ as cli
        with patch('sys.argv', ['investmentideas', 'AAPL', '--plan-samples', '5']), \
                patch('investmentideas.pipeline.run', return_value=Path('folder')) as run, patch('builtins.print'):
            cli.main()
        self.assertEqual(run.call_args.kwargs['plan_samples'], 5)
        with patch('sys.argv', ['investmentideas', 'AAPL', '--plan-samples', '9']), \
                self.assertRaises(SystemExit), patch('sys.stderr'):
            cli.main()


if __name__ == '__main__':
    unittest.main()

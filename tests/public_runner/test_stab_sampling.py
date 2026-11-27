"""Plan sampling in tools/replay.py: N plan requests on one frozen prompt, offline.

The real Router runs against a fake OpenRouter transport, so reservations, the
shared cache session and the ledger telemetry are the code paths a paid run uses.
"""
import copy
import json
from pathlib import Path
import statistics
import sys
import tempfile
import unittest
from unittest.mock import patch

from investmentideas import core, pipeline
from investmentideas.valuation import evaluate

import test_full_pipeline as full_pipeline
import test_plan_repair as plan_repair
import test_underwriting_integration as underwriting

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import replay  # noqa: E402

REAL_ROUTER = core.Router
EASY, STRONG, REVIEWER = 'anthropic/claude-haiku-5.5', 'anthropic/claude-opus-5.5', 'openai/gpt-6.2-sol'


def sampled_plan(growth=.04, margin=.2, tax=.25):
    value = full_pipeline.plan()
    for scenario in value['scenarios'].values():
        scenario.update(growth_rates=[growth] * 5, ebit_margins=[margin] * 5, tax_rate=tax)
    return value


class FakeOpenRouter:
    """Catalog and chat completions; a repeated cached prefix in one session is read from cache."""

    def __init__(self, responses, *, cache=True):
        self.responses, self.cache = list(responses), cache
        self.payloads, self.written = [], set()

    def __call__(self, url, *, key=None, payload=None, timeout=90):
        if url.endswith('/models'):
            def row(model):
                return {'id': model, 'supported_parameters': ['temperature', 'max_tokens', 'response_format'],
                        'pricing': {'prompt': '0.000004', 'completion': '0.00002',
                                    'input_cache_read': '0.0000002', 'input_cache_write': '0.000008'}}
            return {'data': [row(EASY), row(STRONG), row(REVIEWER)]}
        self.payloads.append(payload)
        user = payload['messages'][1]['content']
        system = payload['messages'][0]['content']
        prefix = system[1]['text'] if isinstance(system, list) else ''
        tokens = len(json.dumps(payload['messages']).encode()) // 4
        cached = write = 0
        if prefix:
            marker = (payload.get('session_id'), prefix)
            if self.cache and marker in self.written:
                cached = len(prefix.encode()) // 4
            else:
                write = len(prefix.encode()) // 4
                self.written.add(marker)
        if 'Build an investment valuation plan' in user:
            content = json.dumps(self.responses.pop(0))
            cost = .30 if cached else 1.20
        else:
            content = 'Synthetic evidence synthesis [S1].'
            cost = .05
        return {'model': payload['model'],
                'choices': [{'finish_reason': 'stop', 'message': {'content': content}}],
                'usage': {'prompt_tokens': tokens, 'completion_tokens': 100, 'cost': cost,
                          'prompt_tokens_details': {'cached_tokens': cached, 'cache_write_tokens': write}}}

    def plan_payloads(self):
        return [item for item in self.payloads if 'Build an investment valuation plan' in item['messages'][1]['content']]


class PlanSamplingTests(unittest.TestCase):
    def setUp(self):
        full_pipeline.FullPipelineTests.setUp(self)
        self.directory = tempfile.TemporaryDirectory()
        self.saved = pipeline.run('AAPL', 'synthetic', directory=str(Path(self.directory.name) / 'saved'))
        router = patch.object(core, 'Router', REAL_ROUTER)
        router.start()
        self.patches.append(router)
        self.messages = []

    def tearDown(self):
        full_pipeline.FullPipelineTests.tearDown(self)
        self.directory.cleanup()

    def sample(self, transport, samples, *, output='out', budget=20, resume=False):
        with patch.object(core, 'request_json', transport):
            return replay.sample_plans(self.saved, Path(self.directory.name) / output, samples=samples,
                                       key='synthetic', easy=EASY, strong=STRONG, reviewer=REVIEWER,
                                       budget=budget, resume=resume, progress=self.messages.append)

    def test_each_sample_is_a_new_request_on_the_identical_prompt_in_one_session(self):
        transport = FakeOpenRouter([sampled_plan(.03), sampled_plan(.04), sampled_plan(.06)])
        report = self.sample(transport, 3)
        plans = transport.plan_payloads()
        self.assertEqual(len(plans), 3)
        # One synthesis for all samples; no draft, review or web research.
        self.assertEqual(len(transport.payloads), 4)
        self.assertFalse(any('plugins' in item for item in transport.payloads))
        self.assertTrue(all(item['messages'] == plans[0]['messages'] for item in plans))
        sessions = {item.get('session_id') for item in transport.payloads}
        self.assertEqual(len(sessions), 1)
        self.assertTrue(next(iter(sessions)))
        self.assertEqual([row['status'] for row in report['samples']], ['complete'] * 3)
        self.assertEqual(len({row['plan_request_sha256'] for row in report['samples']}), 1)
        # Samples 2 and 3 read the prefix sample 1 wrote.
        self.assertEqual(report['samples'][0]['first_plan_request']['cached_tokens'], 0)
        self.assertTrue(all(row['plan_usage']['cached_tokens'] > 0 for row in report['samples'][1:]))
        self.assertAlmostEqual(report['samples'][1]['plan_usage']['cost_usd'], .30)
        self.assertFalse(report['warnings'])
        values = [row['metrics']['per_share_value']['base'] for row in report['samples']]
        self.assertEqual(len(set(values)), 3)
        summary = report['summary']
        self.assertEqual(summary['median_base_value'], statistics.median(values))
        self.assertAlmostEqual(summary['spread_fraction'], (max(values) - min(values)) / statistics.median(values))
        self.assertEqual(summary['median_sample'], 2)
        metrics = report['samples'][0]['metrics']
        self.assertAlmostEqual(metrics['revenue_cagr'], .03)
        self.assertAlmostEqual(metrics['ebit_margin_year1'], .2)
        self.assertEqual(metrics['sales_to_capital']['year1'], 2)
        self.assertEqual(metrics['tax_rate'], .25)
        decomposition = report['decomposition']
        self.assertEqual(decomposition['median_sample'], 2)
        self.assertEqual(list(decomposition['groups']), list(replay.DRIVER_GROUPS))
        self.assertEqual(decomposition['largest_group'], 'growth path')
        growth = decomposition['groups']['growth path']
        self.assertAlmostEqual(growth['min_base_value'], min(values))
        self.assertAlmostEqual(growth['max_base_value'], max(values))
        for group in ('margin path', 'capital efficiency', 'tax', 'discount rate'):
            self.assertAlmostEqual(decomposition['groups'][group]['range'], 0)
        saved = json.loads((Path(self.directory.name) / 'out' / 'AAPL' / 'plan-samples' / 'plan-samples.json').read_text())
        self.assertEqual(saved['summary'], report['summary'])
        table = self.messages[-2]
        self.assertIn('varies most: growth path', table)
        self.assertIn('spread (max-min)/median', table)

    def test_a_repaired_sample_counts_its_repair_and_reason(self):
        transport = FakeOpenRouter([sampled_plan(), plan_repair.invalid_plan(), sampled_plan(.05)])
        report = self.sample(transport, 2)
        first, second = report['samples']
        self.assertEqual((first['repairs'], second['repairs']), (0, 1))
        self.assertEqual(second['plan_requests'], 2)
        self.assertEqual(second['status'], 'complete')
        self.assertTrue(second['repair_reasons'])
        self.assertEqual(report['summary']['samples_with_repairs'], 1)

    def test_a_sample_still_invalid_after_its_repairs_is_recorded_and_sampling_continues(self):
        invalid = [plan_repair.invalid_plan(attempt) for attempt in range(pipeline.PLAN_REPAIRS + 1)]
        transport = FakeOpenRouter(invalid + [sampled_plan()])
        report = self.sample(transport, 2)
        first, second = report['samples']
        self.assertEqual(first['status'], 'failed')
        self.assertIn('after 3 repairs', first['error'])
        self.assertEqual(first['repairs'], pipeline.PLAN_REPAIRS)
        self.assertEqual(second['status'], 'complete')
        self.assertEqual(report['summary']['samples_failed'], 1)
        self.assertNotIn('decomposition', report)
        # The failed sample's synthesis was reused, and sample 2 sent the same first request.
        self.assertEqual(len(transport.payloads), 1 + len(invalid) + 1)
        self.assertEqual(first['plan_request_sha256'], second['plan_request_sha256'])

    def test_the_budget_stops_before_a_sample_whose_reservation_would_exceed_it(self):
        probe = self.sample(FakeOpenRouter([sampled_plan()]), 1, output='probe', budget=50)
        folder = Path(probe['samples'][0]['folder'])
        reserve = next(row['reserved_usd'] for row in json.loads((folder / 'state.json').read_text())['ledger']
                       if row['stage'] == 'valuation_plan')
        budget = probe['total_cost_usd'] + reserve - .01
        transport = FakeOpenRouter([sampled_plan(.03), sampled_plan(.05), sampled_plan(.07)])
        report = self.sample(transport, 3, budget=budget)
        self.assertEqual(len(transport.plan_payloads()), 1)
        self.assertEqual([row['sample'] for row in report['samples']], [1])
        self.assertIn('before sample 2', report['stopped'])
        self.assertLessEqual(report['total_cost_usd'], budget)
        # A resumed run with more room keeps sample 1 and buys only the missing samples.
        report = self.sample(transport, 3, budget=budget + 2 * reserve, resume=True)
        self.assertEqual(len(transport.plan_payloads()), 3)
        self.assertEqual([row['status'] for row in report['samples']], ['complete'] * 3)
        self.assertIsNone(report['stopped'])

    def test_a_sample_that_reads_no_cache_is_warned_about(self):
        transport = FakeOpenRouter([sampled_plan(), sampled_plan(.05)], cache=False)
        report = self.sample(transport, 2)
        self.assertEqual(len(report['warnings']), 1)
        self.assertIn('sample 2 read no cached tokens', report['warnings'][0])
        self.assertTrue(any('sample 2 read no cached tokens' in message for message in self.messages))

    def test_an_existing_output_is_not_overwritten_without_resume(self):
        self.sample(FakeOpenRouter([sampled_plan()]), 1)
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.sample(FakeOpenRouter([]), 1)


class DriverSwapTests(unittest.TestCase):
    """One driver group from a donor on the operating-model path, everything else held."""

    def setUp(self):
        self.median = underwriting.inputs()
        self.donor = underwriting.inputs()
        for name, scenario in self.donor['operating_model']['scenarios'].items():
            scenario['components']['products']['growth_rates'] = [.1] * 5
            scenario['components']['services']['growth_rates'] = [.05, .06, .07, .08, .09]
            scenario['components']['services']['gross_margins'] = [.75] * 5
            scenario['expenses']['selling_general_administrative'] = [60, 70, 80, 90, 100]
        for scenario in self.donor['scenarios'].values():
            scenario.update(tax_rate=.3, sales_to_capital=3)
        self.median_result, self.donor_result = evaluate(self.median), evaluate(self.donor)

    def rows(self, result):
        return result['scenarios']['base']['cashflows'][:5]

    def swapped(self, group):
        plan = replay.with_driver_group(self.median, self.median_result, self.donor, self.donor_result, group)
        return plan, evaluate(plan)

    def test_growth_swap_takes_the_donor_revenue_and_keeps_the_median_margins(self):
        _, result = self.swapped('growth path')
        for row, donor, median in zip(self.rows(result), self.rows(self.donor_result), self.rows(self.median_result)):
            self.assertAlmostEqual(row['revenue'], donor['revenue'])
            self.assertAlmostEqual(row['ebit_margin'], median['ebit_margin'])

    def test_margin_swap_takes_the_donor_margins_and_keeps_the_median_revenue(self):
        _, result = self.swapped('margin path')
        for row, donor, median in zip(self.rows(result), self.rows(self.donor_result), self.rows(self.median_result)):
            self.assertAlmostEqual(row['revenue'], median['revenue'])
            self.assertAlmostEqual(row['ebit_margin'], donor['ebit_margin'])

    def test_scalar_groups_copy_only_their_fields(self):
        plan, _ = self.swapped('tax')
        self.assertEqual(plan['scenarios']['base']['tax_rate'], .3)
        self.assertEqual(plan['scenarios']['base']['sales_to_capital'], 2)
        plan, _ = self.swapped('capital efficiency')
        self.assertEqual(plan['scenarios']['base']['sales_to_capital'], 3)
        self.assertEqual(plan['scenarios']['base']['tax_rate'], .2)
        self.assertEqual(self.median, underwriting.inputs())

    def test_decomposition_reports_each_group_range_around_the_median(self):
        report = replay.decompose((1, self.median, self.median_result), [(2, self.donor, self.donor_result)])
        own = self.median_result['scenarios']['base']['per_share_value']
        self.assertEqual(report['median_sample_base_value'], own)
        self.assertAlmostEqual(report['groups']['discount rate']['range'], 0)
        # The median grows at zero, so it reinvests nothing and sales-to-capital cannot move its value.
        self.assertAlmostEqual(report['groups']['capital efficiency']['range'], 0)
        for group in ('growth path', 'margin path', 'tax'):
            item = report['groups'][group]
            self.assertGreater(item['range'], 0, group)
            self.assertAlmostEqual(item['range_fraction_of_median'], item['range'] / own)
        self.assertEqual(report['largest_group'], max(report['groups'], key=lambda g: report['groups'][g]['range']))


if __name__ == '__main__':
    unittest.main()

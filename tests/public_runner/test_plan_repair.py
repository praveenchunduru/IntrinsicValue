"""Forecast-plan format failures seen in a live run on 2026-11-14.

The analytical model wrote a currency code as the operating model's unit and
added undocumented label fields. One repair could not clear both, and the
rejected plan was then replayed from the saved stage on every later attempt.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from investmentideas import pipeline
from investmentideas.valuation import evaluate

import test_full_pipeline as full_pipeline
import test_underwriting_integration as underwriting

plan, inputs = full_pipeline.plan, underwriting.inputs


def invalid_plan():
    value = plan()
    value['scenarios']['base']['probability'] = .9  # Probabilities no longer sum to one.
    return value


class ScriptedRouter(full_pipeline.FakeRouter):
    responses = []
    prompts = []

    def complete(self, stage, model, prompt, max_tokens=6000, web=False):
        if not stage.startswith('valuation_plan'):
            return super().complete(stage, model, prompt, max_tokens, web)
        self.calls.append(stage)
        self.prompts.append(prompt)
        self.ledger.append({'stage': stage, 'model': model, 'status': 'complete', 'annotations': [],
                            'charged_or_reserved_usd': .01})
        self.checkpoint()
        return json.dumps(type(self).responses.pop(0))


class PlanRepairTests(unittest.TestCase):
    def setUp(self):
        full_pipeline.FullPipelineTests.setUp(self)
        ScriptedRouter.calls, ScriptedRouter.prompts = [], []
        router = patch.object(pipeline.core, 'Router', ScriptedRouter)
        router.start()
        self.patches.append(router)

    def tearDown(self):
        full_pipeline.FullPipelineTests.tearDown(self)

    def plan_calls(self):
        return [stage for stage in ScriptedRouter.calls if stage.startswith('valuation_plan')]

    def test_independent_format_defects_can_be_repaired_one_at_a_time(self):
        ScriptedRouter.responses = [invalid_plan(), invalid_plan(), invalid_plan(), plan()]
        with tempfile.TemporaryDirectory() as directory:
            folder = pipeline.run('AAPL', 'synthetic', directory=directory)
            self.assertTrue(json.loads((folder / 'state.json').read_text())['complete'])
        self.assertEqual(self.plan_calls(), ['valuation_plan', 'valuation_plan_repair',
                                             'valuation_plan_repair_2', 'valuation_plan_repair_3'])
        self.assertIn('unknown fields are rejected', ScriptedRouter.prompts[0])
        for prompt in ScriptedRouter.prompts[1:]:
            self.assertIn('Repair this precise validation failure', prompt)
            self.assertIn('Return the complete corrected JSON object', prompt)
            self.assertIn('never a currency code', prompt)

    def test_an_invalid_optional_diagnostic_is_omitted_and_disclosed_without_a_paid_repair(self):
        with_bad_case = plan()
        with_bad_case['counterfactuals'] = [{'name': 'a list is not the documented shape'}]
        ScriptedRouter.responses = [with_bad_case]
        with tempfile.TemporaryDirectory() as directory:
            folder = pipeline.run('AAPL', 'synthetic', directory=directory)
            state = json.loads((folder / 'state.json').read_text())
            self.assertTrue(state['complete'])
            self.assertEqual(list(state['omitted_plan_sections']), ['counterfactuals'])
            self.assertNotIn('counterfactuals', state['plan'])
            self.assertTrue(any('optional counterfactuals did not pass validation' in gap for gap in state['gaps']))
            self.assertIn('optional counterfactuals did not pass validation', (folder / 'memo.md').read_text())
        self.assertEqual(self.plan_calls(), ['valuation_plan'])

    def test_repairs_carry_every_earlier_rejection_so_a_fix_is_not_undone(self):
        first, second = invalid_plan(), invalid_plan()
        second['scenarios']['base']['terminal_growth'] = .5  # A different defect: growth above WACC.
        second['scenarios']['base']['probability'] = .5
        ScriptedRouter.responses = [first, second, plan()]
        with tempfile.TemporaryDirectory() as directory:
            pipeline.run('AAPL', 'synthetic', directory=directory)
        self.assertNotIn('Earlier attempts were rejected', ScriptedRouter.prompts[1])
        self.assertIn('Earlier attempts were rejected for these reasons; do not reintroduce them', ScriptedRouter.prompts[2])

    def test_a_plan_rejected_after_every_repair_is_discarded_so_the_next_attempt_is_fresh(self):
        ScriptedRouter.responses = [invalid_plan() for _ in range(4)]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'after 3 repairs.*fresh forecast'):
                pipeline.run('AAPL', 'synthetic', directory=directory)
            state_path = next(iter(Path(directory).glob('AAPL/full-*/state.json')))
            state = json.loads(state_path.read_text())
            self.assertFalse([name for name in state['stages'] if name.startswith('valuation_plan')])
            self.assertFalse(state['complete'])
            # Spending on the rejected attempts stays in the ledger.
            self.assertEqual(sum(row['stage'].startswith('valuation_plan') for row in state['ledger']), 4)
            research = [stage for stage in ScriptedRouter.calls if not stage.startswith('valuation_plan')]
            ScriptedRouter.calls, ScriptedRouter.responses = [], [plan()]
            folder = pipeline.run('AAPL', 'synthetic', directory=directory)
            self.assertTrue(json.loads((folder / 'state.json').read_text())['complete'])
            self.assertEqual(self.plan_calls(), ['valuation_plan'])
            # Completed research is reused, not bought again.
            self.assertFalse(set(research) & set(ScriptedRouter.calls))


class OperatingUnitLabelTests(unittest.TestCase):
    def lock(self, plan_value):
        fixed = {'actuals': plan_value['actuals'], 'currency': 'USD', 'source_refs': plan_value['source_refs'],
                 'input_dates': plan_value['input_dates'],
                 'coverage': {'complete': True, 'status': 'complete', 'conflicts': [], 'missing_fields': []}}
        def pinned(value, sources):
            result = copy.deepcopy(value)
            result['reported_inputs'] = fixed
            return result, []
        with patch.object(pipeline, 'canonical_reported_inputs', return_value=fixed), \
                patch.object(pipeline, 'pin_reported_inputs', side_effect=pinned):
            return pipeline._lock_plan(plan_value, [])

    def test_a_currency_code_written_as_the_unit_is_corrected_and_recorded(self):
        proposal = inputs()
        proposal['operating_model']['amount_unit'] = 'USD'
        locked, changes = self.lock(proposal)
        self.assertEqual(locked['operating_model']['amount_unit'], 'currency_units')
        self.assertEqual([change['field'] for change in changes], ['operating_model.amount_unit'])
        self.assertEqual(evaluate(locked)['weighted_per_share_value'], evaluate(self.lock(inputs())[0])['weighted_per_share_value'])
        self.assertEqual(proposal['operating_model']['amount_unit'], 'USD')  # The proposal is not mutated.

    def test_aggregate_arrays_that_disagree_with_the_operating_model_go_back_for_repair(self):
        # Live run 2026-11-14: stated margins 3-4 points below the component
        # arithmetic were silently replaced while the memo narrated them.
        proposal = inputs()
        proposal['scenarios']['base'].update(growth_rates=[0]*5, ebit_margins=[.34]*5)
        with self.assertRaisesRegex(ValueError, r'base\.ebit_margins stated \[0\.34.*operating_model gives \[0\.37'):
            self.lock(proposal)
        proposal['scenarios']['base']['ebit_margins'] = [.37, .37, .37, .37, .3749]
        locked, changes = self.lock(proposal)
        self.assertNotIn('ebit_margins', locked['scenarios']['base'])
        self.assertEqual({change['field'] for change in changes},
                         {'scenarios.base.growth_rates', 'scenarios.base.ebit_margins'})

    def test_entry_policy_is_dropped_and_recorded(self):
        proposal = inputs()
        proposal['entry_policy'] = {'basis': 'base', 'margin_of_safety': .2, 'label': 'x', 'rationale': 'y'}
        locked, changes = self.lock(proposal)
        self.assertNotIn('entry_policy', locked)
        self.assertEqual([change['field'] for change in changes], ['entry_policy'])

    def test_duplicates_of_governing_inputs_and_timestamps_are_normalized_and_recorded(self):
        reference = evaluate(self.lock(inputs())[0])
        proposal = inputs()
        # Fixture components: zero growth, EBIT (700*.4 + 300*.8 - 150) / 1000 = 37%.
        for scenario in proposal['scenarios'].values():
            scenario.update(growth_rates=[0]*5, ebit_margins=[.37]*5, wacc=.2)
        proposal['discount_rate']['market_equity']['observed_at'] = '2026-01-02T21:00:01+00:00'
        untouched = copy.deepcopy(proposal)
        locked, changes = self.lock(proposal)
        self.assertEqual(proposal, untouched)
        self.assertEqual(evaluate(locked)['weighted_per_share_value'], reference['weighted_per_share_value'])
        fields = {change['field'] for change in changes}
        self.assertEqual(fields, {f'scenarios.{name}.{field}' for name in ('bear', 'base', 'bull')
                                  for field in ('growth_rates', 'ebit_margins', 'wacc')} |
                         {'discount_rate.market_equity.observed_at'})
        self.assertEqual(next(change for change in changes if change['field'] == 'scenarios.base.wacc')['previous'], .2)
        self.assertEqual(locked['discount_rate']['market_equity']['observed_at'], '2026-01-02')

    def test_aggregate_inputs_are_kept_when_nothing_else_governs_them(self):
        proposal = inputs()
        proposal.pop('operating_model'); proposal.pop('discount_rate')
        for scenario in proposal['scenarios'].values():
            scenario.update(growth_rates=[.02]*5, ebit_margins=[.3]*5, wacc=.1)
        locked, changes = self.lock(proposal)
        self.assertEqual(changes, [])
        self.assertEqual(locked['scenarios']['base']['wacc'], .1)
        self.assertEqual(locked['scenarios']['base']['growth_rates'], [.02]*5)
        aggregate = inputs()
        aggregate['operating_model']['earnings_mode'] = 'aggregate_ebit_margins'
        for scenario in aggregate['scenarios'].values():
            scenario['ebit_margins'] = [.3]*5
        self.assertEqual(self.lock(aggregate)[0]['scenarios']['base']['ebit_margins'], [.3]*5)

    def test_a_scaled_operating_model_is_still_rejected_whatever_its_label(self):
        millions = inputs()
        millions['operating_model']['amount_unit'] = 'millions'
        with self.assertRaisesRegex(ValueError, 'currency_units'):
            evaluate(self.lock(millions)[0])
        scaled = inputs()
        scaled['operating_model']['amount_unit'] = 'USD'
        for component in scaled['operating_model']['components']:
            component['base_revenue'] /= 1000
        with self.assertRaisesRegex(ValueError, 'does not reconcile'):
            evaluate(self.lock(scaled)[0])
        other_currency = inputs()
        other_currency['operating_model']['amount_unit'] = 'EUR'
        with self.assertRaisesRegex(ValueError, 'currency_units'):
            evaluate(self.lock(other_currency)[0])


if __name__ == '__main__':
    unittest.main()


def corrected_plan():
    value = plan()
    value['scenarios']['base']['ebit_margins'] = [.25] * 5
    value['finding_dispositions'] = {'F1': 'accepted: forecast margin corrected'}
    return value


class CorrectionRouter(full_pipeline.FakeRouter):
    responses = []

    def complete(self, stage, model, prompt, max_tokens=6000, web=False):
        if not stage.startswith('model_response'):
            return super().complete(stage, model, prompt, max_tokens, web)
        self.calls.append(stage)
        self.ledger.append({'stage': stage, 'model': model, 'status': 'complete', 'annotations': [],
                            'charged_or_reserved_usd': .01})
        self.checkpoint()
        return json.dumps(type(self).responses.pop(0))


class ModelCorrectionRepairTests(unittest.TestCase):
    """The review-correction stage gets the same lock, omission and bounded repair as the first plan."""

    def setUp(self):
        full_pipeline.FullPipelineTests.setUp(self)
        CorrectionRouter.calls = []
        CorrectionRouter.revise = True
        router = patch.object(pipeline.core, 'Router', CorrectionRouter)
        router.start()
        self.patches.append(router)

    def tearDown(self):
        full_pipeline.FullPipelineTests.tearDown(self)

    def correction_calls(self):
        return [stage for stage in CorrectionRouter.calls if stage.startswith('model_response')]

    def test_an_invalid_correction_is_repaired_and_then_valued(self):
        broken = corrected_plan()
        broken['scenarios']['base']['probability'] = .9
        CorrectionRouter.responses = [broken, corrected_plan()]
        with tempfile.TemporaryDirectory() as directory:
            folder = pipeline.run('AAPL', 'synthetic', directory=directory)
            state = json.loads((folder / 'state.json').read_text())
            response = json.loads((folder / 'reviews' / 'model-response-1.json').read_text())
        self.assertEqual(self.correction_calls(), ['model_response_1', 'model_response_repair_1'])
        self.assertEqual(state['plan']['scenarios']['base']['ebit_margins'], [.25] * 5)
        self.assertNotIn('finding_dispositions', state['plan'])
        self.assertEqual(response['correction_repairs'], 1)

    def test_a_correction_that_never_validates_leaves_the_prior_plan_and_says_so(self):
        broken = corrected_plan()
        broken['scenarios']['base']['probability'] = .9
        CorrectionRouter.responses = [copy.deepcopy(broken) for _ in range(pipeline.PLAN_REPAIRS + 1)]
        with tempfile.TemporaryDirectory() as directory:
            folder = pipeline.run('AAPL', 'synthetic', directory=directory)
            state = json.loads((folder / 'state.json').read_text())
            response = json.loads((folder / 'reviews' / 'model-response-1.json').read_text())
        self.assertEqual(len(self.correction_calls()), pipeline.PLAN_REPAIRS + 1)
        self.assertEqual(state['plan']['scenarios']['base']['ebit_margins'], plan()['scenarios']['base']['ebit_margins'])
        self.assertTrue(any('remained invalid after' in gap and 'prior valuation stands' in gap for gap in state['gaps']))
        self.assertEqual(response['correction_rejected']['repairs'], pipeline.PLAN_REPAIRS)

    def test_one_bad_counterfactual_case_does_not_take_the_valid_cases_with_it(self):
        value = corrected_plan()
        value['counterfactuals'] = {'lower_margin': {'ebit_margins': [.1] * 5},
                                    'broken': {'ebit_margins': [.1] * 2}}
        CorrectionRouter.responses = [value]
        with tempfile.TemporaryDirectory() as directory:
            folder = pipeline.run('AAPL', 'synthetic', directory=directory)
            state = json.loads((folder / 'state.json').read_text())
        self.assertEqual(list(state['plan']['counterfactuals']), ['lower_margin'])
        self.assertEqual(list(state['omitted_plan_sections']), ['counterfactuals.broken'])


class ConventionScopeTests(unittest.TestCase):
    def setUp(self):
        full_pipeline.FullPipelineTests.setUp(self)

    def tearDown(self):
        full_pipeline.FullPipelineTests.tearDown(self)

    def test_supplied_plans_keep_their_own_conventions_unless_they_opt_in(self):
        sources = [full_pipeline.SOURCE]
        with patch.object(pipeline, '_pin_market_conventions', return_value=[]) as pin:
            _, _ = pipeline._lock_plan(plan(), sources, apply_conventions=False)
            pin.assert_not_called()
            opted = plan()
            opted['valuation_timing'] = {'convention': 'stub_v1', 'anchor_date': '2025-12-31'}
            pipeline._lock_plan(opted, sources, apply_conventions=False)
            pin.assert_called_once()
            pipeline._lock_plan(plan(), sources)
            self.assertEqual(pin.call_count, 2)


class ReviewerChoiceTests(unittest.TestCase):
    def test_overriding_the_author_never_makes_it_review_itself(self):
        reviewer = pipeline.core.REVIEWERS['balanced']
        author = pipeline.core.MODELS['balanced'][1]
        self.assertEqual(pipeline._default_reviewer('balanced', author), reviewer)
        self.assertEqual(pipeline._default_reviewer('balanced', reviewer), author)
        self.assertEqual(pipeline._default_reviewer('unknown-profile', 'some/model'), 'some/model')


class SavedPlanResumeTests(unittest.TestCase):
    def setUp(self):
        full_pipeline.FullPipelineTests.setUp(self)

    def tearDown(self):
        full_pipeline.FullPipelineTests.tearDown(self)

    def test_a_plan_saved_before_the_conventions_is_revalued_under_its_own(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = pipeline.run('AAPL', 'synthetic', directory=directory)
            state_path = folder / 'state.json'
            state = json.loads(state_path.read_text())
            self.assertEqual(state['plan_conventions'], pipeline.PLAN_CONVENTIONS)
            state.pop('plan_conventions')
            state['complete'] = False  # An interrupted run resumed after an upgrade.
            state_path.write_text(json.dumps(state))
            with patch.object(pipeline, '_pin_market_conventions', return_value=[]) as pin:
                folder = pipeline.run('AAPL', 'synthetic', directory=directory)
                pin.assert_not_called()
            state = json.loads(state_path.read_text())
            self.assertTrue(any('saved before the market-consistent' in gap for gap in state['gaps']))

"""Fixes from the second independent review (2026-11-14)."""
import copy
import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from investmentideas import inputs, pipeline, valuation

from tests.public_runner.test_market_consistency import plan
from tests.public_runner.test_market_pins import ERP, REGRESSION, REPORTED, TABLE, pin, proposal
from tests.public_runner.test_forecast_continuity import fact, locked_plan

import test_full_pipeline as full_pipeline
import test_plan_repair as plan_repair


class PinFixTests(unittest.TestCase):
    def test_no_stub_is_pinned_on_the_days_the_engine_rejects(self):
        for day in ('2026-12-31', '2027-01-01'):  # 365 and 366 days after a 2025-12-31 anchor
            with self.subTest(day=day):
                value, _, notes = pin(proposal(), day=day)
                self.assertNotIn('valuation_timing', value)
                self.assertTrue(any('no stub adjustment' in note for note in notes))
        value, _, _ = pin(proposal(), day='2026-12-30')
        self.assertIn('valuation_timing', value)

    def test_the_risk_free_rate_and_premium_are_copied_from_their_source(self):
        value = proposal()
        value['discount_rate']['risk_free_rate']['value'] = .042
        value, changes, _ = pin(value)
        rate = value['discount_rate']
        self.assertEqual((rate['risk_free_rate']['value'], rate['risk_free_rate']['observed_at']), (.045, '2026-03-01'))
        self.assertIn('S8', rate['equity_risk_premium']['source_refs'])
        self.assertIn('discount_rate.risk_free_rate', [change['field'] for change in changes])
        _, second, _ = pin(value)
        self.assertEqual(second, [])

    def test_non_usd_plans_are_not_forced_onto_the_us_rate(self):
        value = proposal()
        value['currency'] = 'CAD'
        value.pop('discount_rate')
        for scenario in value['scenarios'].values():
            scenario.update(wacc=.08, terminal_growth=.02)
        _, _, notes = pin(value)
        self.assertTrue(notes)
        value = proposal()
        value['currency'] = 'CAD'
        value['discount_rate']['currency'] = 'CAD'
        value['discount_rate']['risk_free_rate']['source_refs'] = ['S8']
        with self.assertRaisesRegex(ValueError, 'cannot discount cash flows in CAD'):
            pin(value)
        self.assertIn('do not apply to cash flows in CAD', pipeline._rate_convention([ERP, TABLE], 'CAD'))

    def test_an_explicit_constant_term_structure_is_not_an_opt_in(self):
        full_pipeline.FullPipelineTests.setUp(self)
        try:
            supplied = full_pipeline.plan()
            supplied['discount_rate'] = {'term_structure': 'constant'}
            with patch.object(pipeline, '_pin_market_conventions', return_value=[]) as pinned:
                try:
                    pipeline._lock_plan(supplied, [full_pipeline.SOURCE], apply_conventions=False)
                except ValueError:
                    pass
                pinned.assert_not_called()
        finally:
            full_pipeline.FullPipelineTests.tearDown(self)


class EngineTextTests(unittest.TestCase):
    def test_glide_comparisons_report_the_terminal_growth_they_use(self):
        value = plan()
        low = copy.deepcopy(value['discount_rate']['risk_free_rate']); low['value'] = .035
        value['calibration_comparisons'] = [{'name': 'low_treasury', 'label': 'Lower Treasury',
                                             'overrides': {'risk_free_rate': low,
                                                           'equity_risk_premium': copy.deepcopy(value['discount_rate']['equity_risk_premium'])}}]
        row = valuation.evaluate(value)['calibration_comparisons'][0]
        self.assertEqual(row['status'], 'calculated')
        self.assertAlmostEqual(row['terminal_growth'], .035)
        self.assertIn('per_share_value_at_house_terminal_growth', row)
        self.assertIn('moves with the comparison', row['fixed_assumptions'])
        text = pipeline.valuation_section(value, valuation.evaluate(value))
        self.assertIn('Value with house terminal growth', text)

    def test_return_diagnostics_describe_stub_timing(self):
        result = valuation.evaluate(plan())
        multiples = result['computed_diagnostics']['valuation_multiples']['scenarios']['base']
        self.assertIn('discount time', multiples['unchanged_entry_multiple_exit_returns']['ebit']['basis'])
        self.assertIn('final model year', multiples['original_dcf_exit']['basis'])


class OperatingReferenceFixTests(unittest.TestCase):
    START, END = date(2025, 1, 1), date(2025, 12, 31)

    def f(self, concept, value, **kw):
        return fact(concept, value, start=kw.pop('start', self.START), end=kw.pop('end', self.END), **kw)

    def test_total_costs_are_kept_apart_from_operating_expenses(self):
        facts = [self.f('us-gaap:CostsAndExpenses', 273.8e9), self.f('us-gaap:CostOfRevenue', 162.5e9),
                 self.f('us-gaap:SellingAndMarketingExpense', 28.7e9), self.f('us-gaap:GeneralAndAdministrativeExpense', 21.5e9),
                 self.f('us-gaap:OperatingIncomeLoss', 129.0e9)]
        lines = inputs._operating_reference(facts, self.START, self.END, 'USD', Decimal(402.8e9))['lines']
        self.assertEqual(lines['total_costs_and_expenses']['value'], 273800000000)
        self.assertAlmostEqual(lines['operating_expenses']['value'], 111.3e9)
        self.assertTrue(lines['operating_expenses']['basis'].startswith('derived'))
        self.assertAlmostEqual(lines['selling_general_administrative']['value'], 50.2e9)
        self.assertAlmostEqual(lines['gross_profit']['value'], 240.3e9)
        self.assertIn('including cost of revenue', lines['total_costs_and_expenses']['scope'])

    def test_a_conflicted_concept_does_not_fall_back_to_another_scope(self):
        facts = [self.f('us-gaap:OperatingExpenses', 30e9), self.f('us-gaap:OperatingExpenses', 31e9, source='S4'),
                 self.f('us-gaap:CostsAndExpenses', 90e9)]
        lines = inputs._operating_reference(facts, self.START, self.END, 'USD', Decimal(100e9))['lines']
        self.assertNotIn('operating_expenses', lines)

    def test_negative_gross_profit_is_kept(self):
        facts = [self.f('us-gaap:GrossProfit', -2.03e9), self.f('us-gaap:OperatingIncomeLoss', -5.74e9)]
        reference = inputs._operating_reference(facts, self.START, self.END, 'USD', Decimal(4.43e9))
        self.assertLess(reference['gross_margin'], 0)

    def test_the_year_to_date_of_the_first_forecast_year_is_reported(self):
        later = dict(start=date(2026, 1, 1), end=date(2026, 9, 30), form='10-Q')
        facts = [self.f('us-gaap:OperatingIncomeLoss', 83e9), self.f('us-gaap:Revenues', 150e9, **later),
                 self.f('us-gaap:OperatingIncomeLoss', 45e9, **later)]
        reference = inputs._operating_reference(facts, self.START, self.END, 'USD', Decimal(200e9), 'us-gaap:Revenues')
        self.assertAlmostEqual(reference['year_to_date']['operating_margin'], .3)
        self.assertEqual(reference['year_to_date']['period']['end_date'], '2026-09-30')


class ContinuityFixTests(unittest.TestCase):
    def test_the_band_narrows_for_low_margin_businesses(self):
        self.assertAlmostEqual(pipeline._continuity_band(.04), .016)
        self.assertAlmostEqual(pipeline._continuity_band(.30), .05)
        self.assertAlmostEqual(pipeline._continuity_band(.01), .015)

    def test_a_year_to_date_run_rate_counts_as_continuity(self):
        value = locked_plan(.60)
        result = pipeline._evaluate(value)
        first = result['scenarios']['base']['cashflows'][0]['ebit_margin']
        value['reported_inputs']['operating_reference']['year_to_date'] = {
            'operating_margin': first, 'period': {'end_date': '2026-09-30'}, 'source_refs': ['S2']}
        result = pipeline._evaluate(value)
        pipeline._check_continuity(value, result, [{'id': 'S2'}])

    def test_a_basis_must_cite_sources_in_the_evidence(self):
        value = locked_plan(.60)
        result = pipeline._evaluate(value)
        value['margin_continuity_basis'] = {name: 'A disclosed one-time charge reverses next year [S999].'
                                            for name in ('bear', 'base', 'bull')}
        with self.assertRaisesRegex(ValueError, 'S999, which is not in the evidence'):
            pipeline._check_continuity(value, result, [{'id': 'S2'}])

    def test_a_correction_that_leaves_year_one_unchanged_keeps_the_prior_basis(self):
        prior = locked_plan(.60)
        prior['margin_continuity_basis'] = {name: 'A disclosed one-time charge reverses next year [S2].'
                                            for name in ('bear', 'base', 'bull')}
        prior_result = pipeline._evaluate(prior)
        corrected = copy.deepcopy(prior)
        corrected.pop('margin_continuity_basis')
        result = pipeline._evaluate(corrected)
        pipeline._check_continuity(corrected, result, [{'id': 'S2'}], (prior, prior_result))
        self.assertEqual(corrected['margin_continuity_basis'], prior['margin_continuity_basis'])
        moved = copy.deepcopy(corrected)
        moved.pop('margin_continuity_basis')
        for scenario in moved['scenarios'].values():
            scenario['ebit_margins'] = [margin - .05 for margin in scenario['ebit_margins']]
        with self.assertRaises(ValueError):
            pipeline._check_continuity(moved, pipeline._evaluate(moved), [{'id': 'S2'}], (prior, prior_result))


class CorrectionStageTests(unittest.TestCase):
    def setUp(self):
        full_pipeline.FullPipelineTests.setUp(self)
        plan_repair.CorrectionRouter.calls = []
        plan_repair.CorrectionRouter.revise = True
        self.prompts = {}
        router = patch.object(pipeline.core, 'Router', plan_repair.CorrectionRouter)
        router.start()
        self.patches.append(router)

    def tearDown(self):
        full_pipeline.FullPipelineTests.tearDown(self)

    def run_once(self, responses):
        plan_repair.CorrectionRouter.responses = responses
        with tempfile.TemporaryDirectory() as directory:
            folder = pipeline.run('AAPL', 'synthetic', directory=directory)
            state = json.loads((folder / 'state.json').read_text())
            response = json.loads((folder / 'reviews' / 'model-response-1.json').read_text())
        return state, response

    def test_the_memo_author_gets_the_plan_that_was_valued(self):
        broken = plan_repair.corrected_plan()
        broken['scenarios']['base']['probability'] = .9
        state, response = self.run_once([broken, plan_repair.corrected_plan()])
        self.assertEqual(response['accepted_proposal']['scenarios']['base']['probability'],
                         state['plan']['scenarios']['base']['probability'])
        self.assertEqual(response['original_proposal']['scenarios']['base']['probability'], .9)

    def test_a_rejected_correction_sends_no_rejected_forecast(self):
        broken = plan_repair.corrected_plan()
        broken['scenarios']['base']['probability'] = .9
        state, response = self.run_once([copy.deepcopy(broken) for _ in range(pipeline.PLAN_REPAIRS + 1)])
        self.assertNotIn('scenarios', response)
        self.assertNotIn('accepted_proposal', response)
        self.assertIn('prior valuation stands', response['note'])

    def test_a_non_object_or_bad_flag_is_repaired_instead_of_ending_the_run(self):
        state, response = self.run_once([['not', 'an', 'object'], {'unchanged': 'false'}, plan_repair.corrected_plan()])
        self.assertEqual(state['plan']['scenarios']['base']['ebit_margins'], [.25] * 5)
        self.assertEqual(response['correction_repairs'], 2)

    def test_an_unchanged_answer_during_repair_is_honoured(self):
        broken = plan_repair.corrected_plan()
        broken['scenarios']['base']['probability'] = .9
        state, response = self.run_once([broken, {'unchanged': True, 'reason': 'Findings are prose only',
                                                  'finding_dispositions': {'F1': 'memo_only'}}])
        calls = [stage for stage in plan_repair.CorrectionRouter.calls if stage.startswith('model_response')]
        self.assertEqual(calls, ['model_response_1', 'model_response_repair_1'])
        self.assertTrue(response['unchanged'])
        self.assertFalse(any('remained invalid' in gap for gap in state['gaps']))

    def test_supplied_plans_are_told_to_keep_their_own_rate(self):
        text = pipeline._correction_rate_text([ERP, TABLE], {'currency': 'USD', 'scenarios': {'base': {'terminal_growth': .03}}}, False)
        self.assertIn('keeps its own constant discount rate', text)
        self.assertIn('"base": 0.03', text)
        self.assertIn('market-consistent rate path applies', pipeline._correction_rate_text([ERP, TABLE], {'currency': 'USD'}, True))

    def test_omission_notes_are_replaced_when_a_new_plan_is_adopted(self):
        state = {'gaps': ['other']}
        pipeline._record_omitted_sections(state, {'counterfactuals': 'bad'}, 'forecast plan\'s')
        self.assertEqual(len(state['gaps']), 2)
        pipeline._reset_plan_notes(state)
        self.assertEqual(state['gaps'], ['other'])
        self.assertNotIn('omitted_plan_sections', state)


if __name__ == '__main__':
    unittest.main()


class FinancingPinTests(unittest.TestCase):
    REPORTED = {'net_debt_bridge': {'as_of': '2026-09-26', 'debt': 80111e6, 'finance_leases_included_in_net_debt': False},
                'other_claims_bridge': {'as_of': '2026-09-26', 'reported_finance_lease_liabilities': 1456e6},
                'source_refs': {'net_debt': ['S2', 'S4'], 'other_equity_claims': ['S2', 'assumption: unidentified claims are zero'],
                                'market_price': ['S1'], 'shares_outstanding': ['S2']},
                'market_price': 356.27, 'actuals': {'shares_outstanding': 14532182000},
                'input_dates': {'market_price': {'observed_at': '2026-11-13'}}}

    def test_reported_debt_leases_and_market_equity_replace_typed_values(self):
        rate = {'financial_debt': {'value': 80111e6, 'observed_at': '2026-09-26', 'allocation': 'identified_components_sum', 'label': 'Debt'},
                'finance_lease_debt': {'value': 0, 'observed_at': '2026-09-26', 'allocation': 'included_in_financial_debt', 'label': 'Leases'},
                'market_equity': {'value': 5177.7e9, 'observed_at': '2026-11-13', 'label': 'Market cap'}}
        changes = []
        pipeline._pin_financing_records(rate, self.REPORTED, lambda field, old, new, reason: changes.append(field))
        self.assertEqual(rate['finance_lease_debt']['value'], 1456e6)
        self.assertNotIn('allocation', rate['finance_lease_debt'])
        self.assertEqual(rate['finance_lease_debt']['source_refs'], ['S2'])
        self.assertAlmostEqual(rate['market_equity']['value'], 356.27 * 14532182000)
        self.assertEqual(rate['market_equity']['construction'], 'quote_times_reported_shares')
        self.assertIn('discount_rate.finance_lease_debt', changes)

    def test_explicit_market_measures_are_left_alone(self):
        rate = {'financial_debt': {'value': 75e9, 'observed_at': '2026-11-13', 'measurement': 'market_value', 'label': 'Market debt'},
                'market_equity': {'value': 5.2e12, 'observed_at': '2026-11-13', 'construction': 'independent_market_cap',
                                  'reconciliation': 'Two share classes', 'label': 'Cap'}}
        pipeline._pin_financing_records(rate, self.REPORTED, lambda *args: None)
        self.assertEqual(rate['financial_debt']['value'], 75e9)
        self.assertEqual(rate['market_equity']['value'], 5.2e12)


LONG_MEMO = ('# Executive valuation\nThe house value rests on terminal returns well above the market average for a decade '
             'and more [S1].\n')


class QuotedExcerptRouter(full_pipeline.FakeRouter):
    def complete(self, stage, model, prompt, max_tokens=6000, web=False):
        text = super().complete(stage, model, prompt, max_tokens, web)
        if stage == 'full_draft' or stage.startswith('revise_'):
            return LONG_MEMO
        if stage == 'review_0':
            return json.dumps({'verdict': 'revise', 'findings': [{
                'id': 'F1', 'severity': 'major', 'issue': 'Terminal returns are asserted without evidence.',
                'source_refs': ['S1'], 'fix': 'Cite the evidence for durable excess returns.',
                'current_excerpt': '"The house value rests on terminal returns well above the market average for a decade"'}]})
        return text


class FirstParseAnchoringTests(unittest.TestCase):
    def setUp(self):
        full_pipeline.FullPipelineTests.setUp(self)
        QuotedExcerptRouter.calls = []
        router = patch.object(pipeline.core, 'Router', QuotedExcerptRouter)
        router.start()
        self.patches.append(router)

    def tearDown(self):
        full_pipeline.FullPipelineTests.tearDown(self)

    def test_a_quote_wrapped_excerpt_needs_no_paid_format_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = pipeline.run('AAPL', 'synthetic', directory=directory)
            review = json.loads((folder / 'reviews' / 'review-0.json').read_text())
        self.assertNotIn('review_format_0', QuotedExcerptRouter.calls)
        finding = review['findings'][0]
        self.assertEqual(finding['current_excerpt_match'], 'longest-exact-span')
        self.assertTrue(finding['reviewer_excerpt'].startswith('"'))


class ReasoningRequestTests(unittest.TestCase):
    def test_reasoning_is_sent_only_where_the_catalog_lists_it(self):
        from investmentideas import core
        listed = {'supported_parameters': ['reasoning', 'temperature']}
        self.assertEqual(core.reasoning_request(listed, 'anthropic/claude-sonnet-4.6', 8000), {'max_tokens': 8000, 'exclude': True})
        self.assertEqual(core.reasoning_request(listed, 'openai/gpt-5.4', 6000), {'effort': 'high', 'exclude': True})
        self.assertIsNone(core.reasoning_request({'supported_parameters': ['temperature']}, 'anthropic/claude-sonnet-4.6', 8000))
        self.assertIsNone(core.reasoning_request(listed, 'anthropic/claude-sonnet-4.6', 0))
        self.assertEqual(core.reasoning_budget('valuation_plan_repair_2'), 6000)
        self.assertEqual(core.reasoning_budget('full_draft'), 8000)
        self.assertEqual(core.reasoning_budget('review_0'), 0)

    def test_the_budget_is_added_to_the_output_limit_and_temperature_dropped_for_claude(self):
        from investmentideas import core
        sent = {}
        def transport(url, *, key=None, payload=None, timeout=90):
            sent.update(payload)
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': 'ok'}}], 'usage': {'cost': 0.01}}
        router = core.Router.__new__(core.Router)
        router.key, router.budget, router.ledger, router.checkpoint = 'k', 10.0, [], lambda: None
        router.catalog = {'anthropic/claude-sonnet-4.6': {'pricing': {'prompt': '0.000003', 'completion': '0.000015'},
                                                          'supported_parameters': ['reasoning', 'temperature', 'max_tokens']}}
        with patch.object(core, 'request_json', transport):
            router.complete('full_draft', 'anthropic/claude-sonnet-4.6', 'prompt', max_tokens=18000)
        self.assertEqual(sent['max_tokens'], 26000)
        self.assertEqual(sent['reasoning'], {'max_tokens': 8000, 'exclude': True})
        self.assertNotIn('temperature', sent)
        self.assertEqual(router.ledger[-1]['reasoning'], {'max_tokens': 8000, 'exclude': True})

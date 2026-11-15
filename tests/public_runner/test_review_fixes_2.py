"""Fixes from the second independent review (2026-11-14)."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
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
        broken['scenarios']['base']['sales_to_capital'] = 0
        state, response = self.run_once([broken, plan_repair.corrected_plan()])
        self.assertEqual(response['accepted_proposal']['scenarios']['base']['sales_to_capital'],
                         state['plan']['scenarios']['base']['sales_to_capital'])
        self.assertEqual(response['original_proposal']['scenarios']['base']['sales_to_capital'], 0)

    def test_a_rejected_correction_sends_no_rejected_forecast(self):
        broken = plan_repair.corrected_plan()
        broken['scenarios']['base']['sales_to_capital'] = 0
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
        broken['scenarios']['base']['sales_to_capital'] = 0
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
    def complete(self, stage, model, prompt, max_tokens=6000, web=False, prefix=None):
        prompt = (prefix or '') + prompt  # Evidence now arrives as a cached prefix.
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
        self.assertEqual(core.reasoning_request(listed, 'anthropic/claude-opus-5.5', 8000), {'max_tokens': 8000, 'exclude': True})
        self.assertEqual(core.reasoning_request(listed, 'openai/gpt-5.6-sol', 6000), {'effort': 'high', 'exclude': True})
        self.assertIsNone(core.reasoning_request({'supported_parameters': ['temperature']}, 'anthropic/claude-opus-5.5', 8000))
        self.assertIsNone(core.reasoning_request(listed, 'anthropic/claude-opus-5.5', 0))
        self.assertEqual(core.reasoning_budget('valuation_plan_repair_2'), 0)
        self.assertEqual(core.reasoning_budget('full_draft'), 8000)
        self.assertEqual(core.reasoning_budget('full_draft_without_reasoning'), 0)
        self.assertEqual(core.reasoning_budget('review_0'), 0)

    def test_the_budget_is_added_to_the_output_limit_and_temperature_dropped_for_claude(self):
        from investmentideas import core
        sent = {}
        def transport(url, *, key=None, payload=None, timeout=90):
            sent.update(payload)
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': 'ok'}}], 'usage': {'cost': 0.01}}
        router = core.Router.__new__(core.Router)
        router.key, router.budget, router.ledger, router.checkpoint = 'k', 10.0, [], lambda: None
        router.catalog = {'anthropic/claude-opus-5.5': {'pricing': {'prompt': '0.000003', 'completion': '0.000015'},
                                                          'supported_parameters': ['reasoning', 'temperature', 'max_tokens']}}
        with patch.object(core, 'request_json', transport):
            router.complete('full_draft', 'anthropic/claude-opus-5.5', 'prompt', max_tokens=18000)
        self.assertEqual(sent['max_tokens'], 26000)
        self.assertEqual(sent['reasoning'], {'max_tokens': 8000, 'exclude': True})
        self.assertNotIn('temperature', sent)
        self.assertEqual(router.ledger[-1]['reasoning'], {'max_tokens': 8000, 'exclude': True})


class PrereviewCheckTests(unittest.TestCase):
    SOURCES = [{'id': 'S1', 'source_tier': 'regulatory_primary', 'data': {'excerpt': 'Revenue was 478,834 million.'}},
               {'id': 'S2', 'source_tier': 'search_excerpt_unverified', 'data': {'excerpt': 'Street EPS 9.41; management said demand is strong.'}}]

    def ids(self, memo):
        from investmentideas.quality import prereview_checks
        return {row['id']: row for row in prereview_checks(memo, self.SOURCES, ({'value': 0.179},))}

    def test_points_units_and_index_names_are_traced(self):
        found = self.ids('# M\nMargins would need +17.9 points in every year. The S&P 500 is the benchmark.')
        self.assertNotIn('number-untraced', found)

    def test_unverified_figures_need_the_writers_own_label(self):
        found = self.ids('# M\nStreet EPS is $9.41 [S2].')
        self.assertIn('number-unverified-only', found)
        # The inline label Python inserts does not count as the writer presenting it as outside context.
        self.assertIn('number-unverified-only', self.ids('# M\nStreet EPS is $9.41 [S2 unverified].'))
        self.assertNotIn('number-unverified-only', self.ids('# M\nUnverified Street estimates put EPS at $9.41 [S2].'))

    def test_invented_figures_and_added_rows_are_flagged(self):
        found = self.ids('# M\nProducts margin was 60.7% [S1]. Together the convention steps account for more than the entire gap.')
        self.assertEqual([item['figure'] for item in found['number-untraced']['items']], ['60.7%'])
        self.assertIn('convention-rows-added', found)

    def test_fix_responses_use_the_last_complete_block(self):
        from investmentideas import pipeline
        memo, applied, skipped = pipeline._apply_edits('Alpha beta. Gamma.', [{'quote': 'Alpha beta.', 'replacement': 'Alpha.'},
                                                                              {'quote': 'missing', 'replacement': 'x'}])
        self.assertEqual((memo, applied, len(skipped)), ('Alpha. Gamma.', 1, 1))


class UnverifiedLabelTests(unittest.TestCase):
    def test_citations_of_unverified_sources_are_labelled_once(self):
        from investmentideas import pipeline
        sources = [{'id': 'S4', 'source_tier': 'regulatory_primary'}, {'id': 'S16', 'source_tier': 'search_excerpt_unverified'},
                   {'id': 'S17', 'source_tier': 'search_excerpt_unverified'}]
        text = 'Demand [S16]. Margin [S4, S17]. Known [S16 unverified]. Range S16 alone. Table [S4].'
        labelled = pipeline._label_unverified(text, sources)
        self.assertEqual(labelled, 'Demand [S16 unverified]. Margin [S4, S17 unverified]. Known [S16 unverified]. '
                                   'Range S16 alone. Table [S4].')
        self.assertEqual(pipeline._label_unverified(labelled, sources), labelled)

    def test_deleting_a_duplicated_passage_removes_the_second_copy(self):
        from investmentideas import pipeline
        memo, applied, skipped = pipeline._apply_edits('A. Dup. B. Dup. C.', [{'quote': ' Dup.', 'replacement': ''}])
        self.assertEqual((memo, applied, skipped), ('A. Dup. B. C.', 1, []))


class CitePrimaryTests(unittest.TestCase):
    def test_a_primary_figure_cited_only_to_an_excerpt_is_flagged(self):
        from investmentideas.quality import prereview_checks
        sources = [{'id': 'S1', 'source_tier': 'regulatory_primary', 'data': {'excerpt': 'Revenue was 478,834 million.'}},
                   {'id': 'S2', 'source_tier': 'search_excerpt_unverified', 'data': {'excerpt': 'Revenue 478.8 billion.'}}]
        found = {row['id'] for row in prereview_checks('# M\nRevenue was $478.8 billion (unverified) [S2].', sources)}
        self.assertIn('cite-primary-source', found)



class OpusReviewFixTests(unittest.TestCase):
    def test_house_figures_are_never_sent_to_cite_a_primary_source(self):
        from investmentideas.quality import prereview_checks
        sources = [{'id': 'S1', 'source_tier': 'regulatory_primary', 'data': {'excerpt': 'Margin 0.326 reported.'}},
                   {'id': 'S2', 'source_tier': 'search_excerpt_unverified', 'data': {'excerpt': 'x'}}]
        found = {row['id'] for row in prereview_checks('# M\nBase margin is 32.6% (unverified view) [S2].', sources, ({'margin': 0.326},))}
        self.assertNotIn('cite-primary-source', found)

    def test_focus_excerpts_count_as_primary(self):
        from investmentideas.quality import prereview_checks
        sources = [{'id': 'S4', 'source_tier': 'regulatory_primary', 'data': {},
                    'primary_focus_excerpts': [{'text': 'repurchased 297 million shares for $85.8 billion'}]}]
        self.assertEqual(prereview_checks('# M\nApple repurchased 297 million shares for $85.8 billion [S4].', sources), [])

    def test_unfenced_corrected_edits_are_found(self):
        from investmentideas import pipeline
        response = '{"edits":[{"quote":"a","replacement":"b"}]}\nWait, I need to re-read.\n{"edits":[{"quote":"c","replacement":"d"}]}'
        self.assertEqual(pipeline._last_edits(response), [{'quote': 'c', 'replacement': 'd'}])
        self.assertIsNone(pipeline._last_edits('no json here'))

    def test_a_model_written_summary_is_replaced(self):
        from investmentideas import pipeline
        value = plan()
        result = valuation.evaluate(value)
        memo = '# T\n\n## Valuation summary (computed by Python)\n\n| fake | 1 |\n\n## 1. Conclusion\ntext'
        out = pipeline._with_summary(memo, value, result)
        self.assertNotIn('| fake | 1 |', out)
        self.assertEqual(out.count('## Valuation summary (computed by Python)'), 1)

    def test_price_requirements_stay_inside_valid_inputs(self):
        result = valuation.evaluate(plan())
        for row in result['price_requirements']['rows']:
            if row['input'] == 'ebit_margin' and row['status'] == 'solved':
                self.assertLess(result['scenarios']['base']['cashflows'][0]['ebit_margin'] + row['needed'], 1)
            if row['status'] == 'not_reachable':
                self.assertIn(row['direction'], ('above', 'below'))

    def test_shift_rows_report_the_projected_path_they_produce(self):
        value = plan()
        result = valuation.evaluate(value)
        flows = result['scenarios']['base']['cashflows']
        revenue0 = flows[0]['revenue'] / (1 + flows[0]['revenue_growth'])
        rows = {row['input']: row for row in result['price_requirements']['rows']}
        growth, margin = rows['revenue_growth'], rows['ebit_margin']
        self.assertAlmostEqual(growth['house_path']['year1'], growth['house'])
        self.assertAlmostEqual(growth['house_path']['over_forecast'],
                               (flows[-1]['revenue'] / revenue0) ** (1 / len(flows)) - 1)
        self.assertAlmostEqual(margin['house_path']['over_forecast'],
                               sum(flow['ebit_margin'] for flow in flows) / len(flows))
        for row in (growth, margin):
            path = row.get('needed_path') or row.get('at_path')
            self.assertIsNotNone(path)
            delta = row['needed'] if row['status'] == 'solved' else row['at']
            self.assertAlmostEqual(path['year1'], row['house'] + delta)
        summary = pipeline.computed_summary(value, result)
        self.assertIn('a year compounded over', summary)
        self.assertIn('average over', summary)
        self.assertIn('points in every explicit year: ', summary)

    def test_a_contract_refresh_does_not_spend_the_revision_allowance(self):
        from investmentideas import pipeline
        state = {'iteration': 1, 'complete': True, 'workflow_contract': {'old': True}, 'sources': [], 'gaps': [], 'ticker': 'AAPL', 'review_history': []}
        with tempfile.TemporaryDirectory() as directory:
            pipeline._refresh_workflow_contract(Path(directory), state)
        self.assertEqual(state['iteration'], 2)
        self.assertEqual(pipeline._revisions_used(state), 0)


class ReasoningExhaustedTests(unittest.TestCase):
    def test_a_draft_that_only_thinks_is_retried_once_without_thinking(self):
        from investmentideas import core
        sent = []
        def transport(url, *, key=None, payload=None, timeout=90):
            sent.append(payload)
            if payload.get('reasoning'):
                return {'choices': [{'finish_reason': 'length', 'message': {'content': ''}}], 'usage': {'cost': 0.5}}
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': 'memo'}}], 'usage': {'cost': 0.4}}
        router = core.Router.__new__(core.Router)
        router.key, router.budget, router.ledger, router.checkpoint = 'k', 10.0, [], lambda: None
        router.catalog = {'anthropic/claude-opus-5.5': {'pricing': {'prompt': '0.000003', 'completion': '0.000015'},
                                                          'supported_parameters': ['reasoning', 'temperature']}}
        with patch.object(core, 'request_json', transport):
            with self.assertRaises(core.ReasoningExhausted):
                router.complete('full_draft', 'anthropic/claude-opus-5.5', 'prompt', max_tokens=18000)
            self.assertEqual(router.complete('full_draft_without_reasoning', 'anthropic/claude-opus-5.5', 'prompt',
                                             max_tokens=18000), 'memo')
        self.assertNotIn('reasoning', sent[-1])
        self.assertEqual(sent[-1]['max_tokens'], 18000)


class MultiplesFootnoteTimingTests(unittest.TestCase):
    """The multiples footnote must state the timing the diagnostic actually used (live AAPL review F3)."""

    def _result(self, stub):
        multiple = {'value': 30.0}
        row = {'forecast_year1': {'market_ev_to_ebit': multiple, 'market_ev_to_nopat': multiple},
               'original_dcf_exit': {'ev_to_final_forecast_ebit': multiple, 'ev_to_final_forecast_nopat': multiple},
               'unchanged_entry_multiple_exit_returns': {'ebit': {'annual_return_solutions': [0.05]},
                                                         'nopat': {'annual_return_solutions': [0.05]}}}
        return {'computed_diagnostics': {'valuation_multiples': {'scenarios': {'base': row}}},
                'scenarios': {'base': {'fixed_exit_return_at_market_price': {'annual_return_solutions': [0.06]}}},
                'discounting': {'timing': {'stub_years': stub}}}

    def test_stub_timing_is_described_when_used(self):
        text = '\n'.join(pipeline._computed_exhibits(self._result(0.1342), 'USD'))
        self.assertIn('at its discount time from the valuation date', text)
        self.assertNotIn('integer model years', text)

    def test_integer_years_are_described_without_a_stub(self):
        text = '\n'.join(pipeline._computed_exhibits(self._result(0.0), 'USD'))
        self.assertIn('at integer model years', text)


class PlanSourceRuleTests(unittest.TestCase):
    def test_plan_and_correction_prompts_carry_the_evidence_tiers(self):
        import inspect
        source = inspect.getsource(pipeline)
        self.assertIn(pipeline.EVIDENCE_TIER_RULE, pipeline.PLAN_SOURCE_RULE)
        self.assertEqual(source.count('PLAN_FIELD_RULES+PLAN_SOURCE_RULE'), 2)


class RuleAndCheckTests(unittest.TestCase):
    MEMO = '# T\n\nThe base value is USD 237.23 per share.\n'

    def review(self, severity, rule, verdict='revise'):
        return json.dumps({'verdict': verdict, 'findings': [{'id': 'F1', 'severity': severity, 'rule': rule,
            'issue': 'Wording could be tighter.', 'source_refs': [], 'current_excerpt': 'The base value is USD 237.23 per share.',
            'fix': 'Tighten it.'}]})

    def test_a_finding_is_graded_no_higher_than_its_rule(self):
        from investmentideas import quality
        parsed = quality.parse_review(self.review('major', 'n2'), memo=self.MEMO)
        self.assertEqual(parsed['findings'][0]['severity'], 'minor')
        self.assertEqual(parsed['findings'][0]['severity_as_reviewed'], 'major')
        self.assertEqual(parsed['findings'][0]['rule'], 'N2')
        self.assertEqual(parsed['verdict'], 'pass')

    def test_an_unknown_rule_is_a_format_error(self):
        from investmentideas import quality
        with self.assertRaisesRegex(ValueError, 'rule must be one of'):
            quality.parse_review(self.review('major', 'M99'), memo=self.MEMO)

    def test_every_rule_is_in_the_shared_standard(self):
        from investmentideas import quality
        for rule, _, text in quality.RUBRIC_RULES:
            self.assertIn(f'{rule} {text}', quality.RUBRIC)

    def test_only_a_stated_refutation_removes_a_finding(self):
        material = [{'id': 'F1'}, {'id': 'F2'}, {'id': 'F3'}, {'id': 'F1'}]
        raw = json.dumps({'checks': [{'id': 'C1', 'upheld': False, 'reason': 'The sentence names 17.5% as a house assumption.'},
                                     {'id': 'C2', 'upheld': False, 'reason': ''},
                                     {'id': 'C3', 'upheld': True, 'reason': 'Correct.'},
                                     {'id': 'C4', 'upheld': False, 'reason': 'Refuted.'},
                                     {'id': 'C4', 'upheld': True, 'reason': 'Upheld wins.'},
                                     {'id': ['C2'], 'upheld': False, 'reason': 'Malformed ID.'},
                                     {'id': 'C9', 'upheld': False, 'reason': 'Unknown finding.'}]})
        self.assertEqual(pipeline._refuted_findings(raw, material), {0: 'The sentence names 17.5% as a house assumption.'})
        self.assertEqual(pipeline._refuted_findings('not json', material), {})


class SourceRegisterLabelTests(unittest.TestCase):
    def test_unverified_sources_are_labelled_in_the_register(self):
        sources = [{'id': 'S1', 'title': 'Annual report', 'url': 'https://sec.example/10k', 'source_tier': 'regulatory_primary'},
                   {'id': 'S2', 'title': 'Blog post', 'url': 'https://blog.example/a', 'source_tier': 'search_excerpt_unverified'}]
        text = pipeline._render('# T\n\nBody [S1].\n', sources, [], {}, None, {'quality_status': 'needs_review'})
        self.assertIn('- [S1] Annual report', text)
        self.assertIn('- [S2 unverified] Blog post', text)


class FencedCheckTests(unittest.TestCase):
    def test_a_fenced_check_after_analysis_is_read(self):
        raw = 'Checking.\n```json\n' + json.dumps({'checks': [{'id': 'C1', 'upheld': False, 'reason': 'Already stated.'}]}) + '\n```'
        self.assertEqual(pipeline._refuted_findings(raw, [{'id': 'F1'}]), {0: 'Already stated.'})


class OpusPhaseOneFixTests(unittest.TestCase):
    MEMO = RuleAndCheckTests.MEMO

    def test_a_blank_rule_is_no_rule(self):
        from investmentideas import quality
        parsed = quality.parse_review(RuleAndCheckTests().review('major', ' '), memo=self.MEMO)
        self.assertNotIn('rule', parsed['findings'][0])
        self.assertEqual(parsed['findings'][0]['severity'], 'major')

    def test_the_reviewers_own_verdict_is_kept_when_capping_changes_it(self):
        from investmentideas import quality
        parsed = quality.parse_review(RuleAndCheckTests().review('major', 'N2'), memo=self.MEMO)
        self.assertEqual((parsed['verdict'], parsed['verdict_as_reviewed']), ('pass', 'revise'))

    def test_evidence_extraction_code_is_part_of_the_workflow_contract(self):
        import inspect
        source = inspect.getsource(pipeline._workflow_contract)
        self.assertIn("'evidence_tables.py'", source)
        self.assertIn("'research.py'", source)


class GapAttributionTests(unittest.TestCase):
    def test_the_summary_states_which_inputs_alone_can_close_the_gap(self):
        value = plan()
        result = valuation.evaluate(value)
        summary = pipeline.computed_summary(value, result)
        rows = result['price_requirements']['rows']
        solved = [row for row in rows if row['status'] == 'solved']
        self.assertIn('Inputs that alone can bring the base value to the price: ', summary)
        if len(solved) != 1:
            self.assertIn('These are alternatives; the inputs interact.', summary)
        for row in [row for row in rows if row['status'] == 'not_reachable']:
            self.assertIn('Inputs that cannot', summary)

    def test_sections_and_review_use_the_computed_attribution(self):
        self.assertIn('which inputs alone can and cannot bring value to the price', pipeline.SECTIONS)
        self.assertIn('do not assign the\ndifference between value and price to causes', pipeline.SECTIONS)
        self.assertNotIn('Say plainly whether the', pipeline.SECTIONS)
        self.assertIn('Scenario drivers are house assumptions', pipeline.SECTIONS)


class TracedFigureRefutationTests(unittest.TestCase):
    SOURCES = [{'id': 'S2', 'source_tier': 'regulatory_primary', 'data': {},
                'historical_baseline': {'rows': [{'average_repurchase_price': 85.8e9 / 297e6, 'shares_repurchased': 297e6,
                                                  'repurchases': 86.39e9, 'fiscal_year_end': '2026-09-26'}]}}]

    def test_figures_are_traced_to_tables_and_inventions_are_not(self):
        from investmentideas.quality import trace_pools, untraced_figures
        pools = trace_pools(self.SOURCES, ())
        self.assertEqual(untraced_figures('297 million shares at an average price of USD 288.89 [S2].', pools), [])
        self.assertEqual(untraced_figures('at an average of USD 301.47 per share', pools), ['301.47'])
        self.assertIsNone(untraced_figures('The memo omits the bear case.', pools))

    def test_a_historical_buyback_case_takes_the_reported_program_figures(self):
        locked = {'repurchase_tests': [
            {'name': 'fy26', 'amount': 86_390_000_000, 'price': 290.88, 'source_refs': ['S4'],
             'interpretation': 'historical_retrospective_conditional', 'label': 'FY26 buybacks'},
            {'name': 'forward', 'amount': 86_390_000_000, 'price': 290.88, 'source_refs': ['S4'],
             'interpretation': 'forward_conditional_illustration', 'label': 'Forward'},
            {'name': 'other_year', 'amount': 40_000_000_000, 'price': 150.0, 'source_refs': ['S4'],
             'interpretation': 'historical_retrospective_conditional', 'label': 'Unmatched'}]}
        changes = []
        notes = pipeline._pin_repurchase_history(locked, self.SOURCES, changes)
        fy26, forward, other = locked['repurchase_tests']
        self.assertEqual((fy26['amount'], fy26['source_refs']), (85_800_000_000, ['S4', 'S2']))
        self.assertAlmostEqual(fy26['amount'] / fy26['price'], 297e6)
        self.assertEqual((forward['amount'], forward['price']), (86_390_000_000, 290.88))
        self.assertEqual((other['amount'], other['price']), (40_000_000_000, 150.0))
        self.assertEqual(len(changes), 1)
        self.assertIn('fiscal 2026', notes[0])


class HouseConventionPinTests(unittest.TestCase):
    def test_terminal_return_is_the_house_rule_and_weights_default_without_a_cited_basis(self):
        value = plan()
        value['scenarios']['base']['terminal_roic'] = .25
        value['scenarios']['bear']['probability'], value['scenarios']['bull']['probability'] = .2, .3
        changes = []
        notes = pipeline._pin_house_conventions(value, [{'id': 'S1'}], changes)
        self.assertTrue(all(s['terminal_roic'] == pipeline.HOUSE_TERMINAL_ROIC for s in value['scenarios'].values()))
        self.assertEqual({n: s['probability'] for n, s in value['scenarios'].items()}, pipeline.DEFAULT_WEIGHTS)
        self.assertEqual(len(notes), 2)
        self.assertIn('scenarios.base.terminal_roic', [c['field'] for c in changes])

    def test_a_cited_weights_basis_keeps_the_plans_split(self):
        value = plan()
        for name, weight in (('bear', .2), ('base', .5), ('bull', .3)):
            value['scenarios'][name]['probability'] = weight
        value['weights_basis'] = 'The signed contract backlog makes the bull path more likely than the bear path [S1].'
        pipeline._pin_house_conventions(value, [{'id': 'S1'}], [])
        self.assertEqual(value['scenarios']['bull']['probability'], .3)
        value['weights_basis'] = 'The signed contract backlog makes the bull path more likely than the bear path [S9].'
        pipeline._pin_house_conventions(value, [{'id': 'S1'}], [])
        self.assertEqual(value['scenarios']['bull']['probability'], .25)

    def test_the_panel_shows_25_percent_beside_the_house_rule(self):
        value = plan()
        for scenario in value['scenarios'].values():
            scenario['terminal_roic'] = .15
        rows = {row['name']: row for row in valuation.evaluate(value)['convention_panel']['rows']}
        self.assertEqual(rows['terminal_return_25_percent']['status'], 'calculated')
        self.assertEqual(rows['terminal_return_15_percent']['status'], 'not_applicable')


class CauseWordingTests(unittest.TestCase):
    def test_prose_that_assigns_the_gap_a_cause_is_flagged(self):
        from investmentideas import quality
        memo = '# T\n\n## (1) Conclusion\nThe gap to price rests principally on the operating forecast.\n'
        self.assertIn('gap-cause-wording', [f['id'] for f in quality.prereview_checks(memo, [], ())])
        calm = '# T\n\n## (1) Conclusion\nThe house concludes the value is below the price.\n'
        self.assertNotIn('gap-cause-wording', [f['id'] for f in quality.prereview_checks(calm, [], ())])


class CachedPrefixRouterTests(unittest.TestCase):
    CATALOG = {
        'anthropic/claude-opus-5.5': {'pricing': {'prompt': '0.000004', 'completion': '0.00002', 'input_cache_read': '0.0000002',
                                                  'input_cache_write': '0.000005', 'input_cache_write_1h': '0.000008'},
                                      'supported_parameters': ['reasoning', 'response_format']},
        'openai/gpt-5.6-sol': {'pricing': {'prompt': '0.0000044', 'completion': '0.000022', 'input_cache_read': '0.00000044',
                                           'input_cache_write': '0.0000055'}, 'supported_parameters': ['reasoning']},
        'deepseek/deepseek-v4-pro-0813': {'pricing': {'prompt': '0.000000111', 'completion': '0.00000444',
                                                      'input_cache_read': '0.000000097'}, 'supported_parameters': ['temperature']}}

    def router(self):
        from investmentideas import core
        router = core.Router.__new__(core.Router)
        router.key, router.budget, router.ledger, router.checkpoint = 'k', 10.0, [], lambda: None
        router.catalog, router.session = self.CATALOG, 'iv-test-session'
        return router

    def send(self, model, prefix):
        from investmentideas import core
        sent = []
        def transport(url, *, key=None, payload=None, timeout=90):
            sent.append(payload)
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': 'ok'}}], 'usage': {'cost': 0.01}}
        with patch.object(core, 'request_json', transport):
            self.router().complete('review_0', model, 'Instructions.', max_tokens=100, prefix=prefix)
        return sent[0]

    def test_claude_and_gpt_get_a_marked_prefix_and_others_a_plain_prompt(self):
        claude = self.send('anthropic/claude-opus-5.5', 'Evidence:\nlong text\n')
        self.assertEqual(claude['messages'][1]['content'][0]['cache_control'], {'type': 'ephemeral', 'ttl': '1h'})
        self.assertEqual(claude['messages'][1]['content'][1]['text'], 'Instructions.')
        self.assertEqual(claude['session_id'], 'iv-test-session')
        gpt = self.send('openai/gpt-5.6-sol', 'Evidence:\nlong text\n')
        self.assertEqual(gpt['prompt_cache_options'], {'mode': 'explicit'})
        self.assertIn('cache_control', gpt['messages'][1]['content'][0])
        plain = self.send('deepseek/deepseek-v4-pro-0813', 'Evidence:\nlong text\n')
        self.assertEqual(plain['messages'][1]['content'], 'Evidence:\nlong text\nInstructions.')

    def test_the_prefix_is_reserved_at_the_cache_write_price(self):
        router = self.router()
        without = router.estimate('anthropic/claude-opus-5.5', 'x' * 1000, 100)
        with_prefix = router.estimate('anthropic/claude-opus-5.5', 'x' * 1000, 100, prefix='y' * 100000)
        self.assertAlmostEqual(with_prefix - without, 100000 * 0.000008)


class ReviewPrefilterTests(unittest.TestCase):
    def test_trading_and_house_attribution_terms(self):
        from investmentideas import quality
        self.assertIsNone(quality.TRADING_TERMS.search('The repurchase was accretive to remaining holders.'))
        self.assertIsNotNone(quality.TRADING_TERMS.search('This implies 20% upside.'))
        self.assertIsNotNone(quality.HOUSE_ATTRIBUTION.search('The house uses a 17.5% tax rate.'))


class SummaryBoundaryTests(unittest.TestCase):
    def test_a_copied_summary_is_removed_without_the_sections_after_it(self):
        value = plan()
        result = valuation.evaluate(value)
        memo = ('# T\n\n## Valuation summary (computed by Python)\n\n| fake | 1 |\n\n'
                '### 1. Valuation Conclusion\nThe house concludes the value is below the price.\n')
        out = pipeline._with_summary(memo, value, result)
        self.assertIn('### 1. Valuation Conclusion', out)
        self.assertNotIn('| fake | 1 |', out)

    def test_checks_ignore_the_summary_before_level_three_sections(self):
        from investmentideas import quality
        value = plan()
        result = valuation.evaluate(value)
        memo = pipeline._with_summary('# T\n\n### 1. Conclusion\nThe house concludes the value is below the price.\n', value, result)
        ids = [f['id'] for f in quality.prereview_checks(memo, [], (result, value))]
        self.assertNotIn('meta-reference', ids)
        self.assertNotIn('table-in-prose', ids)


class ProseCheckTests(unittest.TestCase):
    def ids(self, text):
        from investmentideas import quality
        return [f['id'] for f in quality.prereview_checks('# T\n\n## (1) Conclusion\n' + text + '\n', [], ())]

    def test_new_prose_checks(self):
        self.assertIn('comparison-direction', self.ids('Backlog increased from USD 678 billion to USD 677 billion.'))
        self.assertNotIn('comparison-direction', self.ids('Backlog increased from USD 600 billion to USD 678 billion.'))
        self.assertIn('table-in-prose', self.ids('| a | b |\n|---|---|'))
        self.assertIn('uncited-frequency', self.ids('Weights reflect the historical frequency of margin cycles.'))
        self.assertNotIn('uncited-frequency', self.ids('Weights reflect the historical frequency of margin cycles [S1].'))
        self.assertIn('meta-reference', self.ids('Python has placed the summary above.'))

    def test_scale_check_and_reported_tables_are_printed(self):
        value = plan()
        result = valuation.evaluate(value)
        self.assertIn('### Scale check', pipeline.valuation_section(value, result))
        source = {'id': 'S2', 'data': {}, 'historical_baseline': {'rows': [
            {'fiscal_year_end': '2025-06-30', 'currency': 'USD', 'revenue': 100e9, 'revenue_growth': .1, 'gross_margin': .6,
             'operating_margin': .3, 'research_and_development_to_revenue': .1, 'selling_general_administrative_to_revenue': .1,
             'effective_tax_rate': .2, 'capital_expenditure_to_revenue': .05, 'depreciation_amortization_to_revenue': .04,
             'stock_compensation_to_revenue': .02, 'repurchases': 5e9, 'average_repurchase_price': None, 'dividends': 2e9,
             'diluted_eps': 3.0}], 'concepts': {}}}
        text = '\n'.join(pipeline.reported_tables_section([source]))
        self.assertIn('| Measure | 2025-06-30 |', text)
        self.assertIn('| revenue | 100.0 |', text)


class MonitoringDirectionTests(unittest.TestCase):
    def test_a_direction_that_contradicts_the_linked_case_is_rejected(self):
        value = plan()
        margins = valuation.evaluate(value)['scenarios']['base']['cashflows']
        lower = [max(row['ebit_margin'] - .05, .01) for row in margins]
        value['counterfactuals'] = {'lower_margin': {'ebit_margins': lower[:len(value['scenarios']['base'].get('ebit_margins', lower))]}}
        value['monitoring'] = [{'name': 'margin_watch', 'observation': 'Operating margin', 'review_by': '2027-06-30',
                                'threshold': 'below the reported margin for two quarters', 'source_refs': ['S1'],
                                'counterfactual': 'lower_margin', 'direction': 'lowers_value'}]
        result = valuation.evaluate(value)
        self.assertLess(result['monitoring'][0]['value_change']['dollar_delta'], 0)
        value['monitoring'][0]['direction'] = 'raises_value'
        with self.assertRaisesRegex(ValueError, 'linked case'):
            valuation.evaluate(value)

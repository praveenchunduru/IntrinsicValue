import copy
import json
import math
import unittest

from investmentideas.costs import cost_summary


class CostSummaryTests(unittest.TestCase):
    def test_actuals_and_uncertain_reservations_reconcile_without_double_counting(self):
        ledger = [
            {'stage': 'review_0', 'model': 'vendor/reviewer', 'actual_model': 'vendor/reviewer-v2',
             'reserved_usd': 1.5, 'charged_or_reserved_usd': .3, 'status': 'complete',
             'usage': {'cost': .3, 'prompt_tokens': 100, 'completion_tokens': 20,
                       'prompt_tokens_details': {'cached_tokens': 80}}},
            {'stage': 'research', 'model': 'vendor/cheap', 'reserved_usd': .2,
             'charged_or_reserved_usd': .2, 'status': 'failed_http'},
            {'stage': 'review_0', 'model': 'vendor/reviewer', 'reserved_usd': .6,
             'charged_or_reserved_usd': .4, 'status': 'incomplete',
             'usage': {'cost': .4, 'prompt_tokens': 200, 'completion_tokens': 50,
                       'prompt_tokens_details': {'cached_tokens': 0}}},
            {'stage': 'research', 'model': 'vendor/cheap', 'charged_or_reserved_usd': .1,
             'status': 'complete'},
        ]
        before = copy.deepcopy(ledger)
        summary = cost_summary(ledger)
        totals = summary['totals']
        self.assertEqual(ledger, before)
        self.assertEqual(totals['request_count'], 4)
        self.assertAlmostEqual(totals['total_charged_or_reserved_usd'], 1)
        self.assertAlmostEqual(totals['known_usage_cost_usd'], .7)
        self.assertAlmostEqual(totals['retained_estimates_usd'], .3)
        self.assertEqual(totals['requests_with_known_usage_cost'], 2)
        self.assertEqual(totals['requests_with_retained_estimates'], 2)
        self.assertEqual(totals['request_statuses'], {'complete': 2, 'failed_http': 1, 'incomplete': 1})
        self.assertEqual(totals['prompt_tokens'], 300)
        self.assertEqual(totals['completion_tokens'], 70)
        self.assertEqual(totals['cached_prompt_tokens'], 80)
        self.assertEqual(totals['cached_prompt_tokens_reported_requests'], 2)
        self.assertEqual(summary['by_stage'][1]['stage'], 'review_0')
        self.assertAlmostEqual(summary['by_model'][0]['retained_estimates_usd'], .3)
        self.assertIn('vendor/reviewer-v2', [row['actual_model'] for row in summary['by_stage_model']])

    def test_zero_reported_cost_is_actual_and_missing_tokens_are_unknown(self):
        summary = cost_summary([
            {'stage': 'draft', 'model': 'vendor/model', 'reserved_usd': 4,
             'charged_or_reserved_usd': 0, 'status': 'complete', 'usage': {'cost': 0}},
            {'stage': 'draft', 'model': 'vendor/model', 'charged_or_reserved_usd': .5,
             'status': 'received', 'usage': {'prompt_tokens': 0, 'completion_tokens': 0}},
        ])
        totals = summary['totals']
        self.assertEqual(totals['known_usage_cost_usd'], 0)
        self.assertEqual(totals['requests_with_known_usage_cost'], 1)
        self.assertEqual(totals['retained_estimates_usd'], .5)
        self.assertEqual(totals['prompt_tokens'], 0)
        self.assertEqual(totals['prompt_tokens_reported_requests'], 1)
        self.assertIsNone(totals['cached_prompt_tokens'])
        self.assertEqual(totals['cached_prompt_tokens_reported_requests'], 0)

    def test_legacy_minimal_rows_and_pending_checkpoints_do_not_infer_free_requests(self):
        totals = cost_summary([
            {'stage': 'pending', 'model': 'v/m', 'reserved_usd': .5, 'status': 'pending'},
            {'stage': 'known', 'model': 'v/m', 'status': 'complete', 'usage': {'cost': .2}},
            {'stage': 'unknown', 'model': 'v/m', 'status': 'complete'},
        ])['totals']
        self.assertAlmostEqual(totals['total_charged_or_reserved_usd'], .7)
        self.assertEqual(totals['requests_without_charge'], 1)
        self.assertEqual(totals['requests_with_retained_estimates'], 1)

    def test_sorting_and_request_only_scope_are_deterministic(self):
        rows = [
            {'stage': 'z', 'model': 'v/b', 'status': 'complete', 'charged_or_reserved_usd': .2},
            {'stage': 'a', 'model': 'v/a', 'status': 'pending', 'charged_or_reserved_usd': .1},
        ]
        summary = cost_summary(rows)
        self.assertEqual(summary, cost_summary(list(reversed(rows))))
        self.assertEqual([row['stage'] for row in summary['by_stage']], ['a', 'z'])
        self.assertEqual(summary['totals']['request_count'], 2)
        self.assertIn('not observable', summary['scope']['stage_cache'])
        # A cached-stage reuse adds no ledger row, hence no extra charged request.
        self.assertEqual(cost_summary(copy.deepcopy(rows))['totals'], summary['totals'])

    def test_only_safe_routing_labels_and_numeric_usage_leave_the_summary(self):
        secret = 'NEVER_COPY_THIS_PRIVATE_VALUE'
        row = {'stage': 'draft', 'model': 'v/m', 'status': 'complete', 'charged_or_reserved_usd': .1,
               'prompt': secret, 'messages': [secret], 'key': secret, 'api_key': secret,
               'partial_response': secret, 'annotations': [{'content': secret}],
               'usage': {'cost': .1, 'provider_response': secret}}
        encoded = json.dumps(cost_summary([row]), allow_nan=False)
        self.assertNotIn(secret, encoded)
        unsafe = {**row, 'stage': 'unexpected prose\n' + secret, 'status': {'body': secret}}
        self.assertNotIn(secret, json.dumps(cost_summary([unsafe]), allow_nan=False))

    def test_malformed_or_nonfinite_recognized_numbers_are_rejected_without_echoing(self):
        for field in ('charged_or_reserved_usd', 'reserved_usd'):
            for bad in (True, '0.1', -1, math.inf, math.nan, 10 ** 500):
                with self.subTest(field=field, bad_type=type(bad).__name__), self.assertRaises(ValueError):
                    cost_summary([{'stage': 'x', 'model': 'v/m', field: bad}])
        for usage in ({'cost': False}, {'cost': 'PRIVATE_BODY'}, {'cost': -1},
                      {'cost': math.nan}, {'prompt_tokens': 1.5}, {'completion_tokens': -1},
                      {'prompt_tokens_details': {'cached_tokens': math.inf}}):
            with self.subTest(usage=usage), self.assertRaises(ValueError) as exc:
                cost_summary([{'stage': 'x', 'model': 'v/m', 'usage': usage}])
            self.assertNotIn('PRIVATE_BODY', str(exc.exception))
        with self.assertRaises(ValueError):
            cost_summary([{'charged_or_reserved_usd': 1e308}, {'charged_or_reserved_usd': 1e308}])

    def test_empty_ledger_is_json_serializable_and_has_no_invented_token_counts(self):
        summary = cost_summary([])
        self.assertEqual(summary['totals']['request_count'], 0)
        self.assertEqual(summary['totals']['total_charged_or_reserved_usd'], 0)
        self.assertIsNone(summary['totals']['prompt_tokens'])
        self.assertEqual(summary['by_stage'], [])
        json.dumps(summary, allow_nan=False)


if __name__ == '__main__':
    unittest.main()

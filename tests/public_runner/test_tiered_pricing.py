"""Pricing-tier and parameter compatibility without metered requests."""
import copy
import unittest
from unittest.mock import patch

from investmentideas import core

MODEL = 'openai/gpt-5.4'
PRICING = {
    'prompt': '0.0000025',
    'completion': '0.000015',
    'overrides': [{'min_prompt_tokens': 272000, 'prompt': '0.000005',
                   'completion': '0.0000225', 'input_cache_read': '0.0000005'}],
}
RESULT = {'choices': [{'finish_reason': 'stop', 'message': {'content': 'Mock review [S1]'}}]}


def catalog(pricing=None, **metadata):
    return {'data': [{'id': MODEL, 'pricing': copy.deepcopy(PRICING if pricing is None else pricing),
                      **metadata}]}


class TieredPricingTests(unittest.TestCase):
    def test_invalid_cost_telemetry_never_releases_the_reservation(self):
        for cost in (False,True,'0',-1,float('inf'),10**500):
            result=copy.deepcopy(RESULT)
            result['usage']={'cost':cost,'prompt_tokens':True,'completion_tokens':-1,
                'prompt_tokens_details':{'cached_tokens':'unknown'}}
            with self.subTest(cost_type=type(cost).__name__),patch.object(core,'request_json',
                    side_effect=[catalog(),result]):
                ledger=[]
                core.Router('synthetic-key',2,ledger).complete('review',MODEL,'Full reservation retained')
                self.assertEqual(ledger[0]['charged_or_reserved_usd'],ledger[0]['reserved_usd'])
                self.assertNotIn('cost',ledger[0]['usage'])
                # The same persistence validator used by pipeline checkpoints
                # can still save/summarize this uncertain request.
                from investmentideas.costs import cost_summary
                totals=cost_summary(ledger)['totals']
                self.assertEqual(totals['requests_with_retained_estimates'],1)

    def test_highest_tier_is_reserved_capped_and_checkpointed_before_request(self):
        ledger, checkpoints, requests = [], [], []
        def checkpoint():
            checkpoints.append(copy.deepcopy(ledger))
        def transport(url, **kwargs):
            requests.append((url, kwargs))
            if url.endswith('/models'):
                return catalog(supported_parameters=['max_tokens'])
            self.assertEqual(checkpoints[0][0]['status'], 'pending')
            return copy.deepcopy(RESULT)  # Missing cost telemetry must keep the full reserve.
        with patch.object(core, 'request_json', side_effect=transport):
            review = core.Router('synthetic-key', 2, ledger, checkpoint).complete(
                'review', MODEL, 'Short prompt below the tier threshold', max_tokens=1000)
        self.assertEqual(review, 'Mock review [S1]')
        payload = requests[-1][1]['payload']
        self.assertEqual(payload['provider']['max_price'], {'prompt': 5.0, 'completion': 22.5})
        self.assertNotIn('temperature', payload)
        expected = (len((core.SYSTEM + 'Short prompt below the tier threshold').encode()) + 4096) * .000005 + 1000 * .0000225
        self.assertAlmostEqual(ledger[0]['reserved_usd'], expected)
        self.assertAlmostEqual(ledger[0]['charged_or_reserved_usd'], expected)
        self.assertNotIn('synthetic-key', repr(ledger))

    def test_structured_stage_uses_json_mode_only_when_advertised(self):
        for stage,parameters,expected in [('review_0',['response_format'],True),
                ('valuation_plan',['response_format'],True),('full_draft',['response_format'],False),
                ('review_0',[],False)]:
            with self.subTest(stage=stage,parameters=parameters), patch.object(core,'request_json',
                    side_effect=[catalog(supported_parameters=parameters),copy.deepcopy(RESULT)]) as request:
                core.Router('synthetic-key',2,[]).complete(stage,MODEL,'Synthetic request')
                payload=request.call_args.kwargs['payload']
                self.assertEqual(payload.get('response_format'),{'type':'json_object'} if expected else None)

    def test_upper_tier_can_stop_a_request_that_base_prices_would_allow(self):
        with patch.object(core, 'request_json', return_value=catalog()) as request:
            ledger = []
            router = core.Router('synthetic-key', .1, ledger)
            # Base cost < $0.1; upper-tier reservation is > $0.1.
            with self.assertRaisesRegex(ValueError, 'Budget would be exceeded'):
                router.complete('review', MODEL, 'Review', max_tokens=4000)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(ledger, [])

    def test_maximum_input_and_output_prices_are_selected_independently(self):
        pricing = {'prompt': '.000008', 'completion': '.000010', 'request': '0',
                   'overrides': [{'min_prompt_tokens': 100, 'prompt': '.000004', 'completion': '.000030'}]}
        with patch.object(core, 'request_json', side_effect=[catalog(pricing), copy.deepcopy(RESULT)]) as request:
            core.Router('synthetic-key', 2, []).complete('review', MODEL, 'Review')
        self.assertEqual(request.call_args.kwargs['payload']['provider']['max_price'],
                         {'prompt': 8.0, 'completion': 30.0})

    def test_temperature_is_retained_when_supported_or_metadata_is_absent(self):
        for metadata in [{}, {'supported_parameters': ['max_tokens', 'temperature']}]:
            with self.subTest(metadata=metadata), patch.object(core, 'request_json',
                    side_effect=[catalog(**metadata), copy.deepcopy(RESULT)]) as request:
                core.Router('synthetic-key', 2, []).complete('review', MODEL, 'Review')
                self.assertEqual(request.call_args.kwargs['payload']['temperature'], .2)
        with patch.object(core, 'request_json', side_effect=[catalog(supported_parameters=[]), copy.deepcopy(RESULT)]) as request:
            core.Router('synthetic-key', 2, []).complete('review', MODEL, 'Review')
            self.assertNotIn('temperature', request.call_args.kwargs['payload'])

    def test_malformed_or_paid_non_token_tiers_stop_before_request_or_reservation(self):
        invalid = [None, {}, 'tiers', [None], [{}],
                   [{'min_prompt_tokens': True, 'prompt': '0', 'completion': '0'}],
                   [{'min_prompt_tokens': -1, 'prompt': '0', 'completion': '0'}],
                   [{'min_prompt_tokens': 1.5, 'prompt': '0', 'completion': '0'}],
                   [{'min_prompt_tokens': 100, 'prompt': '0'}],
                   [{'min_prompt_tokens': 100, 'prompt': '-1', 'completion': '0'}],
                   [{'min_prompt_tokens': 100, 'prompt': 'nan', 'completion': '0'}],
                   [{'min_prompt_tokens': 100, 'prompt': '0', 'completion': 'inf'}],
                   [{'min_prompt_tokens': 100, 'prompt': True, 'completion': '0'}],
                   [{'min_prompt_tokens': 100, 'prompt': '0', 'completion': '0', 'request': '.01'}],
                   [{'min_prompt_tokens': 100, 'prompt': '0', 'completion': '0', 'overrides': []}],
                   [{'min_prompt_tokens': 100, 'prompt': '0', 'completion': '0', 'input_cache_read': '-1'}]]
        for overrides in invalid:
            pricing = {'prompt': '.000001', 'completion': '.000002', 'overrides': overrides}
            with self.subTest(overrides=overrides), patch.object(core, 'request_json', return_value=catalog(pricing)) as request:
                ledger, checkpoints = [], []
                router = core.Router('synthetic-key', 2, ledger, lambda: checkpoints.append(True))
                with self.assertRaisesRegex(ValueError, 'token-only pricing'):
                    router.complete('review', MODEL, 'Review')
                self.assertEqual(request.call_count, 1)
                self.assertEqual(ledger, [])
                self.assertEqual(checkpoints, [])

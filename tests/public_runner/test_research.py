import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from investmentideas import core

CATALOG = {'data': [{'id': model, 'pricing': {'prompt': '0.0000001', 'completion': '0.0000003'}}
                    for model in sorted({model for profile in core.MODELS.values() for model in profile})]}
SOURCE = {'id':'S1','title':'Synthetic filing', 'url':'https://example.com/filing',
          'data': {'revenue':100, 'currency':'USD', 'period':'2025'}}


def response(text='Supported analysis [S1]', cost=0.001, finish='stop'):
    return {'choices':[{'finish_reason':finish,'message':{'content':text}}],
            'usage':{'cost':cost,'prompt_tokens':12,'completion_tokens':20}}


class ResearchTests(unittest.TestCase):
    def test_complete_resume_and_no_secret_on_disk(self):
        calls = []
        def transport(url, **kwargs):
            calls.append((url, kwargs))
            return CATALOG if url.endswith('/models') else response()
        with tempfile.TemporaryDirectory() as temp, patch.object(core, 'request_json', side_effect=transport), patch.object(core, 'collect', return_value=([SOURCE], ['No verified quote'])):
            folder = core.run('aapl', 'secret-test-key', directory=temp)
            self.assertIn('Source register', (folder/'memo.md').read_text())
            self.assertIn('No verified quote', (folder/'memo.md').read_text())
            self.assertEqual(len(calls), 5)
            self.assertEqual(core.run('aapl', 'secret-test-key', directory=temp), folder)
            self.assertEqual(len(calls), 5)
            for path in folder.iterdir():
                self.assertNotIn('secret-test-key', path.read_text())
            ledger = json.loads((folder/'usage.json').read_text())
            self.assertAlmostEqual(sum(x['charged_or_reserved_usd'] for x in ledger), .004)
            self.assertEqual(calls[1][1]['payload']['provider']['allow_fallbacks'], True)

    def test_budget_rejects_before_metered_call(self):
        with patch.object(core, 'request_json', return_value=CATALOG) as request:
            router = core.Router('test', .000001, [])
            with self.assertRaisesRegex(ValueError, 'Budget would be exceeded'):
                router.complete('draft', core.MODELS['economy'][1], 'hello')
            self.assertEqual(request.call_count, 1)

    def test_missing_usage_retains_reserve(self):
        no_usage = response(); no_usage.pop('usage')
        with patch.object(core, 'request_json', side_effect=[CATALOG, no_usage]):
            ledger = []
            core.Router('test', 2, ledger).complete('test', core.MODELS['economy'][0], 'hello')
            self.assertGreater(ledger[0]['charged_or_reserved_usd'], 0)

    def test_network_failure_preserves_reservation_before_request(self):
        ledger, snapshots = [], []
        def checkpoint():
            snapshots.append(json.loads(json.dumps(ledger)))
        with patch.object(core, 'request_json', side_effect=[CATALOG, RuntimeError('timeout')]):
            router = core.Router('test', 2, ledger, checkpoint)
            with self.assertRaises(RuntimeError):
                router.complete('test', core.MODELS['economy'][0], 'hello')
        self.assertEqual(snapshots[0][0]['status'], 'pending')
        self.assertGreater(ledger[0]['charged_or_reserved_usd'], 0)

    def test_partial_failure_resume_only_unfinished_stages(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(core, 'collect', return_value=([SOURCE], [])):
            with patch.object(core, 'request_json', side_effect=[CATALOG, response(), RuntimeError('timeout')]):
                with self.assertRaises(RuntimeError):
                    core.run('AAPL', 'test', directory=temp)
            statefile = next(Path(temp).rglob('state.json'))
            state = json.loads(statefile.read_text())
            self.assertEqual(list(state['stages']), ['extract'])
            self.assertEqual(len(state['ledger']), 2)
            with patch.object(core, 'request_json', side_effect=[CATALOG, response(), response(), response()]) as request:
                folder = core.run('AAPL', 'test', directory=temp)
                self.assertEqual(request.call_count, 4)
                self.assertTrue((folder/'memo.md').exists())

    def test_no_evidence_does_not_call_model(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(core,'collect', return_value=([], ['blocked'])), patch.object(core,'request_json') as request:
            with self.assertRaisesRegex(RuntimeError, 'No evidence'):
                core.run('AAPL','test',directory=temp)
            request.assert_not_called()

    def test_supplied_evidence_works_without_data_services(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(core, 'collect', return_value=([], ['blocked'])), patch.object(core,'request_json', side_effect=[CATALOG] + [response()]*4):
            folder = core.run('AAPL','test',directory=temp,evidence='Revenue $100 per annual report, source example.com, 2025')
            self.assertIn('User-supplied evidence', (folder/'memo.md').read_text())

    def test_rejects_unavailable_truncated_empty_and_extra_pricing(self):
        with patch.object(core,'request_json',return_value=CATALOG):
            router = core.Router('test',2,[])
            with self.assertRaisesRegex(ValueError,'unavailable'):
                router.complete('test','missing','hello')
        for result in [response(finish='length'), response(text='')]:
            with patch.object(core,'request_json',side_effect=[CATALOG,result]):
                with self.assertRaises(RuntimeError):
                    core.Router('test',2,[]).complete('test',core.MODELS['economy'][0],'hello')
        priced = json.loads(json.dumps(CATALOG))
        priced['data'][0]['pricing']['request'] = '.01'
        with patch.object(core,'request_json',return_value=priced):
            with self.assertRaisesRegex(ValueError,'token-only'):
                core.Router('test',2,[]).complete('test',priced['data'][0]['id'],'hello')

    def test_input_validation(self):
        for bad in ['../../etc/passwd','A/B','', 'X'*16]:
            with self.assertRaises(ValueError):
                core.symbol(bad)
        for budget in [float('nan'),float('inf'),-1,0,101]:
            with self.assertRaises(ValueError):
                core.Router('test',budget,[])


if __name__ == '__main__':
    unittest.main()

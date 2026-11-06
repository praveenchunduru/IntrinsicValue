import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

from investmentideas import __main__ as cli
from investmentideas import web
from investmentideas.sec_http import SECContactRequired, user_agent


@contextlib.contextmanager
def local_server(directory):
    server = web.create_server(0, directory)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    def request(path, method='GET', data=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        connection.request(method, path, body=data, headers=headers or {})
        response = connection.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return result
    try:
        _, page, _ = request('/')
        token = re.search("const csrf='([^']+)'", page.decode()).group(1)
        yield request, token
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def wait_for_result(request, token):
    for _ in range(100):
        status, body, _ = request('/status', headers={'X-Research-Token':token})
        if status == 200 and json.loads(body)['done']:
            return json.loads(body)
        time.sleep(.01)
    raise AssertionError('Mocked local run failed to finish')


class CommandLineTests(unittest.TestCase):
    def test_default_full_and_explicit_quick_choose_the_right_runner(self):
        full = types.ModuleType('investmentideas.pipeline')
        full.run = Mock(return_value=Path('/tmp/full-result'))
        for arguments, expected in [(['AAPL'], 'full'), (['AAPL','--quick'], 'quick')]:
            with self.subTest(mode=expected), patch.dict(sys.modules, {'investmentideas.pipeline':full}), \
                    patch.object(cli,'run',return_value=Path('/tmp/quick-result')) as quick, \
                    patch.dict(os.environ,{'OPENROUTER_API_KEY':'synthetic-key'}), \
                    patch.object(sys,'argv',['investmentideas']+arguments), contextlib.redirect_stdout(io.StringIO()):
                full.run.reset_mock()
                cli.main()
                selected = full.run if expected == 'full' else quick
                other = quick if expected == 'full' else full.run
                self.assertEqual(selected.call_args.args, ('AAPL','synthetic-key'))
                self.assertEqual(selected.call_args.kwargs['profile'],'balanced')
                self.assertEqual(selected.call_args.kwargs['budget'],10 if expected=='full' else 2)
                other.assert_not_called()

    def test_sec_contact_is_scoped_to_cli_run_without_becoming_runner_input(self):
        full=types.ModuleType('investmentideas.pipeline')
        email='cli-scope@example.invalid'
        def scoped(*args,**kwargs):
            self.assertIn(email,user_agent('https://data.sec.gov/fixture'))
            self.assertNotIn(email,user_agent('https://openrouter.ai/api/v1/models'))
            self.assertNotIn('sec_contact',kwargs)
            return Path('/tmp/full-result')
        full.run=Mock(side_effect=scoped)
        with patch.dict(sys.modules,{'investmentideas.pipeline':full}),patch.dict(os.environ,{},clear=True), \
                patch.object(sys,'argv',['investmentideas','AAPL','--sec-contact',email]), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            cli.main()
            self.assertNotIn(email,stdout.getvalue())
            with self.assertRaises(SECContactRequired):
                user_agent('https://data.sec.gov/fixture')

    def test_revalue_is_offline_and_uses_edited_assumptions(self):
        module = types.ModuleType('investmentideas.valuation')
        module.evaluate = Mock(return_value={'base':123})
        module.save_valuation = Mock()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'edited.json'; path.write_text('{"edited": true}')
            target = Path(temp)/'recomputed'
            with patch.dict(sys.modules,{'investmentideas.valuation':module}), \
                    patch.object(sys,'argv',['investmentideas','--revalue',str(path),'--output',str(target)]), \
                    patch.dict(os.environ,{},clear=True), patch.object(cli,'run') as quick, \
                    contextlib.redirect_stdout(io.StringIO()):
                cli.main()
            module.evaluate.assert_called_once_with({'edited':True})
            module.save_valuation.assert_called_once_with(target,{'edited':True},{'base':123})
            quick.assert_not_called()

    def test_no_ticker_opens_the_requested_local_mode(self):
        with patch.object(sys,'argv',['investmentideas','--quick','--port','8877']), patch.object(web,'serve') as serve:
            cli.main()
        serve.assert_called_once_with(8877,'research-output',default_mode='quick')


class BrowserFlowTests(unittest.TestCase):
    def test_sec_contact_reaches_worker_scope_and_never_status_or_artifacts(self):
        full=types.ModuleType('investmentideas.pipeline')
        email='browser-scope@example.invalid'
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/'finished';folder.mkdir()
            (folder/'memo.md').write_text('Synthetic memo')
            (folder/'state.json').write_text(json.dumps({'version':'full-2','complete':False,
                'quality_status':'needs_review','ledger':[]}))
            def scoped(*args,**kwargs):
                self.assertIn(email,user_agent('https://data.sec.gov/fixture'))
                self.assertNotIn(email,user_agent('https://openrouter.ai/api/v1/models'))
                self.assertNotIn('sec_contact',kwargs)
                return folder
            full.run=Mock(side_effect=scoped)
            with patch.dict(sys.modules,{'investmentideas.pipeline':full}),patch.dict(os.environ,{},clear=True), \
                    local_server(temp) as (request,token):
                headers={'X-Research-Token':token,'Content-Type':'application/json'}
                body=json.dumps({'key':'synthetic','ticker':'AAPL','sec_contact':email})
                self.assertEqual(request('/run','POST',body,headers)[0],202)
                result=wait_for_result(request,token)
                self.assertEqual(result['memo'],'Synthetic memo')
                self.assertNotIn(email,json.dumps(result))
                self.assertEqual(full.run.call_args.kwargs['budget'],10)
                self.assertTrue(all(email not in p.read_text() for p in folder.iterdir()))
            with self.assertRaises(SECContactRequired):
                user_agent('https://data.sec.gov/fixture')

    def test_preflight_stop_exposes_zero_spend_and_downloadable_plan(self):
        from investmentideas.core import BudgetExceeded
        full=types.ModuleType('investmentideas.pipeline')
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/'paused';folder.mkdir()
            (folder/'state.json').write_text(json.dumps({'ledger':[],'iteration':0,'quality_status':'budget_paused'}))
            (folder/'budget-preflight.json').write_text('{"passed":false}')
            stopped=BudgetExceeded('Initial budget preflight stopped before paid research; rerun with --budget 10')
            stopped.folder=folder
            full.run=Mock(side_effect=stopped)
            with patch.dict(sys.modules,{'investmentideas.pipeline':full}),local_server(temp) as (request,token):
                headers={'X-Research-Token':token,'Content-Type':'application/json'}
                self.assertEqual(request('/run','POST',json.dumps({'key':'synthetic','ticker':'AAPL','budget':2}),headers)[0],202)
                result=wait_for_result(request,token)
                self.assertEqual(result['cost_usd'],0)
                self.assertEqual(result['quality_status'],'budget_paused')
                self.assertIn('budget-preflight.json',result['downloads'])
                self.assertIn('--budget 10',result['message'])
                self.assertEqual(request('/download/budget-preflight.json',headers={'X-Research-Token':token})[0],200)

    def test_completed_full_download_edits_lose_review_badge_without_rerun_or_get_side_effects(self):
        from investmentideas import pipeline
        from tests.public_runner.test_budget_workflow import FullContractTransport
        fixture=FullContractTransport()
        full=types.ModuleType('investmentideas.pipeline')
        with tempfile.TemporaryDirectory() as temp,fixture.installed():
            folder=pipeline.run('AAPL','synthetic-key',directory=temp)
            before_requests=len(fixture.requests)
            full.run=Mock(return_value=folder)
            with patch.dict(sys.modules,{'investmentideas.pipeline':full}),local_server(temp) as (request,token):
                headers={'X-Research-Token':token,'Content-Type':'application/json'}
                self.assertEqual(request('/run','POST',json.dumps({'key':'synthetic','ticker':'AAPL'}),headers)[0],202)
                self.assertEqual(wait_for_result(request,token)['quality_status'],'reviewed_draft')
                baseline_state=(folder/'state.json').read_bytes()
                for name in ('memo.md','valuation.json'):
                    with self.subTest(name=name):
                        original=(folder/name).read_bytes()
                        marker=b'UNREVIEWED EXTERNAL EDIT MUST NOT BE SERVED AS REVIEWED'
                        (folder/name).write_bytes(marker)
                        _,body,_=request('/status',headers={'X-Research-Token':token})
                        result=json.loads(body)
                        self.assertTrue(result['done'])
                        self.assertEqual(result['quality_status'],'needs_review')
                        self.assertIn('changed after review',result['message'])
                        self.assertNotIn('memo',result)
                        code,body,_=request('/download/'+name,headers={'X-Research-Token':token})
                        self.assertEqual(code,409)
                        self.assertNotIn(marker,body)
                        self.assertEqual((folder/name).read_bytes(),marker)
                        self.assertEqual((folder/'state.json').read_bytes(),baseline_state)
                        self.assertEqual(len(fixture.requests),before_requests)
                        self.assertFalse(list((folder/'revisions').glob('artifact-before-*')))
                        (folder/name).write_bytes(original)
                # Raw archives are deliberately not re-read by status/download
                # checks; full checksums remain enforced at pipeline resume.
                read_bytes=Path.read_bytes
                def no_raw_reads(path):
                    if path.parent.name=='sources_raw':
                        raise AssertionError('Timer status must not rehash raw archives')
                    return read_bytes(path)
                with patch.object(Path,'read_bytes',no_raw_reads):
                    self.assertEqual(json.loads(request('/status',headers={'X-Research-Token':token})[1])['quality_status'],'reviewed_draft')
                    self.assertEqual(request('/download/memo.md',headers={'X-Research-Token':token})[0],200)

    def test_full_result_metadata_and_downloads_require_auth(self):
        full = types.ModuleType('investmentideas.pipeline')
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/'finished'; folder.mkdir()
            (folder/'memo.md').write_text('# Memo\n<script>untrusted source text</script>')
            (folder/'valuation.json').write_text('{"base":123}')
            (folder/'assumptions.json').write_text('{"growth":0.03}')
            (folder/'sources.json').write_text('[]')
            (folder/'source-ledger.json').write_text('[]')
            (folder/'state.json').write_text(json.dumps({'ledger':[{'charged_or_reserved_usd':.018}],
                 'iteration':2,'quality_status':'needs_review'}))
            # Files outside the explicit allowlist must never be downloadable.
            (folder/'secret.txt').write_text('not-for-serving')
            full.run=Mock(return_value=folder)
            with patch.dict(sys.modules,{'investmentideas.pipeline':full}), local_server(temp) as (request, token):
                headers={'X-Research-Token':token,'Content-Type':'application/json'}
                status,_,_=request('/run','POST',json.dumps({'key':'test-key-never-saved','ticker':'AAPL'}),headers)
                self.assertEqual(status,202)
                result=wait_for_result(request,token)
                self.assertEqual(result['cost_usd'],.018)
                self.assertEqual(result['iterations'],2)
                self.assertEqual(result['quality_status'],'needs_review')
                self.assertNotIn('test-key-never-saved',json.dumps(result))
                self.assertIn('assumptions.json',result['downloads'])
                self.assertNotIn('secret.txt',result['downloads'])
                self.assertEqual(request('/download/memo.md')[0],403)
                code,body,response_headers=request('/download/memo.md',headers={'X-Research-Token':token})
                self.assertEqual(code,200)
                self.assertIn(b'untrusted source text',body)
                self.assertEqual(response_headers['Content-Disposition'],'attachment; filename="memo.md"')
                self.assertEqual(response_headers['Cache-Control'],'no-store')
                for path in ['/download/state.json','/download/secret.txt','/download/../secret.txt','/download/%2e%2e%2fsecret.txt']:
                    self.assertEqual(request(path,headers={'X-Research-Token':token})[0],404)
                self.assertEqual(full.run.call_args.kwargs['profile'],'balanced')
                for path in folder.iterdir():
                    self.assertNotIn('test-key-never-saved',path.read_text())

    def test_quick_mode_preserved_and_browser_budget_capped(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp); (folder/'memo.md').write_text('Quick memo')
            with patch.object(web,'run',return_value=folder) as quick, local_server(temp) as (request,token):
                headers={'X-Research-Token':token,'Content-Type':'application/json'}
                for budget in [.99,20.01,float('nan')]:
                    body=json.dumps({'key':'synthetic-key','ticker':'AAPL','budget':budget})
                    self.assertEqual(request('/run','POST',body,headers)[0],400)
                quick.assert_not_called()
                body=json.dumps({'key':'synthetic-key','ticker':'AAPL','mode':'quick','budget':1})
                self.assertEqual(request('/run','POST',body,headers)[0],202)
                self.assertEqual(wait_for_result(request,token)['memo'],'Quick memo')
                quick.assert_called_once()

    def test_symlink_outside_run_is_not_a_download(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); folder=base/'run'; folder.mkdir()
            outside=base/'outside'; outside.write_text('outside')
            try:
                (folder/'assumptions.json').symlink_to(outside)
            except (OSError,NotImplementedError):
                self.skipTest('Platform cannot create symlinks')
            self.assertNotIn('assumptions.json',web._available_files(folder))


if __name__=='__main__':
    unittest.main()

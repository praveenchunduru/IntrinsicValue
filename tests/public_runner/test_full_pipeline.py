import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from investmentideas import pipeline


def plan():
    scenario={'probability':.25,'growth_rates':[.04]*5,'ebit_margins':[.2]*5,'tax_rate':.25,
              'sales_to_capital':2,'wacc':.1,'terminal_growth':.02,'terminal_roic':.15}
    scenarios={name:copy.deepcopy(scenario) for name in ['bear','base','bull']}
    scenarios['base']['probability']=.5
    return {'currency':'USD','amount_unit':'currency_units','share_unit':'shares',
            'assumptions_label':'Synthetic house forecasts; other claims assumed zero',
            'actuals':{'revenue':1000000000,'net_debt':100000000,'other_equity_claims':0,'shares_outstanding':100000000},
            'source_refs':{k:['S1'] for k in ['revenue','net_debt','other_equity_claims','shares_outstanding']},
            'scenarios':scenarios}


SOURCE={'id':'S1','kind':'financial_timeseries','url':'https://example.com/financials',
        'title':'Synthetic financial statements','data':{'series':{}}}
MEMO='# Executive valuation\nSynthetic financial research [S1].\n'


class FakeRouter:
    calls=[]
    revise=False
    fail_draft=False
    fail_review=False
    fail_model=False
    fail_revision=False
    malformed_model=False
    prefaced_model=False
    prefaced_review=False
    unavailable=False
    withdraw=False
    forecasts_only=False
    def __init__(self,key,budget,ledger,checkpoint):
        self.ledger,self.checkpoint=ledger,checkpoint
    def complete(self,stage,model,prompt,max_tokens=6000,web=False):
        self.calls.append(stage)
        self.ledger.append({'stage':stage,'model':model,'status':'pending','charged_or_reserved_usd':.01})
        self.checkpoint()
        if stage.startswith('revise_') and self.fail_revision:
            raise RuntimeError('Synthetic prose revision interruption')
        if stage.startswith('model_response_') and self.fail_model:
            raise RuntimeError('Synthetic model interruption')
        if stage.startswith('review_') and self.fail_review:
            raise RuntimeError('Synthetic review interruption')
        if stage=='full_draft' and self.fail_draft:
            raise RuntimeError('Synthetic interruption')
        self.ledger[-1].update(status='complete',annotations=[])
        if stage.startswith('valuation_plan'):
            if self.unavailable:return json.dumps({'unavailable':True,'missing_inputs':['shares'],'reason':'Unknown'})
            value=plan()
            if self.forecasts_only:value={k:value[k] for k in ('assumptions_label','scenarios')}
            return json.dumps(value)
        if stage.startswith('model_response_format'):
            return json.dumps(plan())
        if stage.startswith('model_response'):
            if self.malformed_model:return 'A malformed proposal: {not JSON}'
            if self.prefaced_model:return 'Analysis precedes the JSON.\n```json\n'+json.dumps(plan())+'\n```'
            if self.withdraw:return json.dumps({'unavailable':True,'reason':'Withdraw unsupported target','missing_inputs':['shares']})
            changed=plan();changed['scenarios']['base']['ebit_margins']=[.25]*5
            changed['finding_dispositions']={'F1':'accepted: forecast margin corrected'}
            return json.dumps(changed)
        if stage=='review_0' and self.revise:
            return json.dumps({'verdict':'revise','findings':[{'id':'F1','severity':'major','issue':'Forecast margin needs revision','source_refs':['S1'],'current_excerpt':'Synthetic financial research [S1].','fix':'Use corrected model'}]})
        if stage.startswith('review_'):
            review=json.dumps({'verdict':'pass','findings':[]})
            return 'Analysis before one answer.\n```json\n'+review+'\n```' if self.prefaced_review else review
        return MEMO


class FullPipelineTests(unittest.TestCase):
    def setUp(self):
        FakeRouter.calls=[];FakeRouter.fail_revision=False;FakeRouter.fail_model=False;FakeRouter.malformed_model=False;FakeRouter.prefaced_model=False;FakeRouter.prefaced_review=False;FakeRouter.fail_review=False;FakeRouter.revise=False;FakeRouter.fail_draft=False;FakeRouter.unavailable=False;FakeRouter.withdraw=False;FakeRouter.forecasts_only=False
        def canonical(sources):
            inputs=plan()
            return {'actuals':inputs['actuals'],'currency':'USD','source_refs':inputs['source_refs'],
                    'input_dates':{},'coverage':{'complete':True,'status':'complete','conflicts':[],'missing_fields':[]}}
        def pinned(value,sources):
            result=copy.deepcopy(value)
            if not result.get('unavailable'):
                fixed=canonical(sources)
                result.setdefault('actuals',fixed['actuals'])
                result.setdefault('source_refs',fixed['source_refs'])
                result['reported_inputs']=fixed
            return result,[]
        self.patches=[patch.object(pipeline.core,'collect',return_value=([SOURCE],[])),
                      patch.object(pipeline,'gather_primary',return_value=([{'title':'Primary filing','url':'https://example.com/filing',
                         'kind':'filing','data':{'excerpt':'Revenue 1000000000'},'raw_document':'<html>public filing</html>'}],[])),
                      patch.object(pipeline.core,'Router',FakeRouter),patch.object(pipeline,'inspect_memo',return_value=[]),
                      patch.object(pipeline,'canonical_reported_inputs',side_effect=canonical),
                      patch.object(pipeline,'pin_reported_inputs',side_effect=pinned)]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()

    def test_complete_archives_recomputes_and_reuses_without_key_on_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','secret-synthetic-key',directory=directory)
            self.assertEqual(len(FakeRouter.calls),6)
            self.assertTrue((folder/'sources_raw'/'S2.html').exists())
            self.assertTrue((folder/'cashflows.csv').exists())
            self.assertIn('Weighted intrinsic value per share',(folder/'memo.md').read_text())
            self.assertEqual(pipeline.run('AAPL','secret-synthetic-key',directory=directory),folder)
            self.assertEqual(len(FakeRouter.calls),6)
            for p in folder.rglob('*'):
                if p.is_file() and p.suffix in ['.md','.json','.html']:
                    self.assertNotIn('secret-synthetic-key',p.read_text())
            sources=json.loads((folder/'sources.json').read_text())
            self.assertNotIn('raw_document',sources[1])
            self.assertEqual(json.loads((folder/'state.json').read_text())['quality_status'],'reviewed_draft')

    def test_model_findings_change_model_and_memo_together(self):
        FakeRouter.revise=True
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            saved=json.loads((folder/'assumptions.json').read_text())
            self.assertEqual(saved['scenarios']['base']['ebit_margins'],[.25]*5)
            self.assertTrue((folder/'reviews'/'review-1.json').exists())
            self.assertTrue((folder/'reviews'/'model-response-1.json').exists())
            self.assertNotIn('finding_dispositions',saved)
            self.assertIn('F1',json.loads((folder/'reviews'/'model-response-1.json').read_text())['finding_dispositions'])
            self.assertEqual(json.loads((folder/'state.json').read_text())['iteration'],1)
            self.assertIn('targeted_research_1',FakeRouter.calls)

    def test_resume_does_not_repay_completed_research_or_model_plan(self):
        FakeRouter.fail_draft=True
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):pipeline.run('AAPL','synthetic',directory=directory)
            completed=len(FakeRouter.calls)
            FakeRouter.fail_draft=False
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            self.assertEqual(FakeRouter.calls[completed:],['full_draft','review_0'])
            ledger=json.loads((folder/'usage.json').read_text())
            self.assertTrue(any(r['status']=='pending' for r in ledger))

    def test_review_interruption_preserves_current_download_with_pending_status(self):
        FakeRouter.fail_review=True
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError,'review interruption'):
                pipeline.run('AAPL','synthetic',directory=directory)
            folder=next(Path(directory).glob('AAPL/full-*'))
            state=json.loads((folder/'state.json').read_text())
            visible=(folder/'memo.md').read_text()
            self.assertIn(state['current_memo'].rstrip(),visible)
            self.assertIn('Weighted intrinsic value per share',visible)
            self.assertIn('needs_review',visible)
            self.assertFalse(state['complete'])
            self.assertTrue((folder/'source-ledger.json').exists())

    def test_interrupted_correction_reuses_frozen_review_and_targeted_research(self):
        FakeRouter.revise=True;FakeRouter.fail_model=True
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError,'model interruption'):
                pipeline.run('AAPL','synthetic',directory=directory)
            count=len(FakeRouter.calls)
            FakeRouter.fail_model=False
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            self.assertEqual(FakeRouter.calls[count:],['model_response_1','revise_1','review_1'])
            self.assertTrue(json.loads((folder/'state.json').read_text())['complete'])

    def test_prose_interruption_after_model_change_resumes_saved_phase_only(self):
        FakeRouter.revise=True;FakeRouter.fail_revision=True
        original=FakeRouter.complete
        def with_targeted_evidence(router,stage,*args,**kwargs):
            value=original(router,stage,*args,**kwargs)
            if stage=='targeted_research_1':
                router.ledger[-1]['annotations']=[{'url':'https://example.com/targeted-evidence',
                    'title':'Targeted evidence','content':'A new source relevant to the frozen findings'}]
            return value
        with tempfile.TemporaryDirectory() as directory,patch.object(FakeRouter,'complete',with_targeted_evidence):
            with self.assertRaisesRegex(RuntimeError,'prose revision interruption'):
                pipeline.run('AAPL','synthetic',directory=directory)
            folder=next(Path(directory).glob('AAPL/full-*'))
            state=json.loads((folder/'state.json').read_text())
            self.assertEqual(state['iteration'],0)
            self.assertEqual(state['plan']['scenarios']['base']['ebit_margins'],[.25]*5)
            self.assertEqual(state['pending_revision']['iteration'],0)
            self.assertTrue(pipeline._pending_revision_matches(state['pending_revision'],state))
            self.assertEqual(len(state['sources']),3)
            self.assertFalse(state['complete'])
            self.assertEqual(state['quality_status'],'needs_review')
            self.assertIn('needs_review',(folder/'memo.md').read_text())
            self.assertIn(f"{state['valuation']['weighted_per_share_value']:,.2f}",(folder/'memo.md').read_text())
            frozen=(folder/'revisions'/'memo-0.md').read_text()
            count=len(FakeRouter.calls);FakeRouter.fail_revision=False
            pipeline.run('AAPL','synthetic',directory=directory)
            self.assertEqual(FakeRouter.calls[count:],['revise_1','review_1'])
            state=json.loads((folder/'state.json').read_text())
            self.assertNotIn('pending_revision',state)
            self.assertTrue(state['complete'])
            self.assertEqual(state['iteration'],1)
            self.assertEqual((folder/'revisions'/'memo-0.md').read_text(),frozen)
            self.assertTrue(any(r['stage']=='revise_1' and r['status']=='pending' and
                                r['charged_or_reserved_usd']>0 for r in state['ledger']))

    def test_new_external_notes_invalidate_a_committed_pending_revision(self):
        FakeRouter.revise=True;FakeRouter.fail_revision=True
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError,'prose revision interruption'):
                pipeline.run('AAPL','synthetic',directory=directory)
            folder=next(Path(directory).glob('AAPL/full-*'))
            self.assertIn('pending_revision',json.loads((folder/'state.json').read_text()))
            count=len(FakeRouter.calls);FakeRouter.fail_revision=False
            pipeline.run('AAPL','synthetic',directory=directory,review_notes='New independent evidence concern')
            self.assertIn('review_0',FakeRouter.calls[count:])
            self.assertIn('model_response_1',FakeRouter.calls[count:])
            state=json.loads((folder/'state.json').read_text())
            self.assertEqual(state['external_notes'],'New independent evidence concern')
            self.assertNotIn('pending_revision',state)
            self.assertTrue(state['complete'])

    def test_supplemental_sources_invalidate_a_committed_pending_revision(self):
        FakeRouter.revise=True;FakeRouter.fail_revision=True
        extra={'title':'Supplemental primary evidence','url':'https://example.com/new-filing',
               'kind':'regulatory_primary','data':{'excerpts':[{'text':'An additional cash and debt passage'}]}}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError,'prose revision interruption'):
                pipeline.run('AAPL','synthetic',directory=directory)
            count=len(FakeRouter.calls);FakeRouter.fail_revision=False
            folder=pipeline.run('AAPL','synthetic',directory=directory,extra_sources=[extra])
            subsequent=FakeRouter.calls[count:]
            self.assertNotIn('revise_1',subsequent)
            self.assertIn('valuation_plan',subsequent)
            self.assertIn('full_draft',subsequent)
            self.assertIn('review_1',subsequent)
            state=json.loads((folder/'state.json').read_text())
            self.assertNotIn('pending_revision',state)
            self.assertEqual(len(state['supplement_history']),1)
            self.assertTrue(state['complete'])

    def test_pending_revision_respects_a_reduced_revision_limit(self):
        FakeRouter.revise=True;FakeRouter.fail_revision=True
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError,'prose revision interruption'):
                pipeline.run('AAPL','synthetic',directory=directory)
            count=len(FakeRouter.calls);FakeRouter.fail_revision=False
            with self.assertRaisesRegex(ValueError,'Increase max_revisions'):
                pipeline.run('AAPL','synthetic',directory=directory,max_revisions=0)
            self.assertEqual(FakeRouter.calls[count:],[])
            folder=next(Path(directory).glob('AAPL/full-*'))
            state=json.loads((folder/'state.json').read_text())
            self.assertIn('pending_revision',state)
            self.assertFalse(state['complete'])

    def test_malformed_model_proposal_has_one_low_cost_format_repair(self):
        FakeRouter.revise=True;FakeRouter.malformed_model=True
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            self.assertEqual(FakeRouter.calls.count('model_response_format_1'),1)
            self.assertEqual(json.loads((folder/'assumptions.json').read_text())['actuals'],plan()['actuals'])
            self.assertTrue(json.loads((folder/'state.json').read_text())['complete'])

    def test_prefaced_complete_proposal_needs_no_paid_format_repair(self):
        FakeRouter.revise=True;FakeRouter.prefaced_model=True
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            self.assertNotIn('model_response_format_1',FakeRouter.calls)
            state=json.loads((folder/'state.json').read_text())
            self.assertEqual(state['response_normalizations'][0]['method'],'one_explicit_complete_json_fence')
            self.assertTrue(state['complete'])

    def test_proposal_recovery_never_guesses_invalid_or_ambiguous_json(self):
        for text in ['```json\n{"a":1}\n```\n```json\n{"a":2}\n```',
                     'Analysis\n```json\n{"a":1, "a":2}\n```',
                     'Analysis\n```json\n{"a":NaN}\n```',
                     'Analysis\n```json\n{"a":1\n```',
                     'Analysis {"a":1}']:
            with self.subTest(text=text),self.assertRaises(ValueError):
                pipeline._response_json(text)

    def test_new_notes_invalidate_pending_frozen_review(self):
        FakeRouter.revise=True;FakeRouter.fail_model=True
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError,'model interruption'):
                pipeline.run('AAPL','synthetic',directory=directory)
            count=len(FakeRouter.calls);FakeRouter.fail_model=False
            pipeline.run('AAPL','synthetic',directory=directory,review_notes='New independent fact to investigate')
            self.assertIn('review_0',FakeRouter.calls[count:])

    def test_prefaced_complete_review_preserves_strict_schema_without_paid_repair(self):
        FakeRouter.prefaced_review=True
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            self.assertFalse(any(s.startswith('review_format') for s in FakeRouter.calls))
            state=json.loads((folder/'state.json').read_text())
            self.assertEqual(state['response_normalizations'][0]['stage'],'review_0')
            self.assertTrue(state['complete'])

    def test_external_allegations_apply_once_and_adjudicator_receives_current_memo(self):
        FakeRouter.revise=True
        captured={}
        original=FakeRouter.complete
        def capture(router,stage,model,prompt,*args,**kwargs):
            captured[stage]=prompt
            return original(router,stage,model,prompt,*args,**kwargs)
        note='Investigate this allegation from the earlier draft only.'
        with tempfile.TemporaryDirectory() as directory,patch.object(FakeRouter,'complete',capture):
            pipeline.run('AAPL','synthetic',directory=directory,review_notes=note)
            self.assertIn(note,captured['review_0'])
            self.assertNotIn(note,captured['review_1'])
            self.assertIn('Current memo (verify allegations against this exact text):\n'+MEMO,captured['model_response_1'])

    def test_explicit_counterfactuals_reopen_and_recompute_without_repaying_research(self):
        cases={'growth_10pct':{'growth_rates':[.1]*5}}
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            initial=json.loads((folder/'valuation.json').read_text())['weighted_per_share_value']
            count=len(FakeRouter.calls)
            pipeline.run('AAPL','synthetic',directory=directory,counterfactuals=cases)
            self.assertEqual(FakeRouter.calls[count:],['review_1'])
            value=json.loads((folder/'valuation.json').read_text())
            self.assertEqual(value['weighted_per_share_value'],initial)
            self.assertIn('growth_10pct',value['counterfactuals'])
            self.assertIn('Conditional counterfactuals',(folder/'memo.md').read_text())
            self.assertEqual(json.loads((folder/'assumptions.json').read_text())['counterfactuals'],cases)
            count=len(FakeRouter.calls)
            pipeline.run('AAPL','synthetic',directory=directory,counterfactuals=cases)
            self.assertEqual(len(FakeRouter.calls),count)

    def test_provided_draft_is_archived_and_reopens_only_review(self):
        edited=MEMO+'An externally edited conclusion [S1].'
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            count=len(FakeRouter.calls)
            pipeline.run('AAPL','synthetic',directory=directory,draft=edited)
            self.assertEqual(FakeRouter.calls[count:],['review_1'])
            state=json.loads((folder/'state.json').read_text())
            self.assertEqual(state['current_memo'],edited)
            self.assertEqual((folder/'revisions/memo-before-provided-1.md').read_text(),MEMO)
            self.assertEqual(state['draft_history'][0]['origin'],'provided_draft')
            self.assertTrue(state['complete'])
            count=len(FakeRouter.calls)
            pipeline.run('AAPL','synthetic',directory=directory,draft=edited)
            self.assertEqual(len(FakeRouter.calls),count)

    def test_provided_draft_failure_retains_pending_status_and_reservations(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            FakeRouter.fail_review=True
            with self.assertRaisesRegex(RuntimeError,'review interruption'):
                pipeline.run('AAPL','synthetic',directory=directory,draft=MEMO+'Edited [S1].')
            state=json.loads((folder/'state.json').read_text())
            self.assertFalse(state['complete'])
            self.assertEqual(state['quality_status'],'needs_review')
            self.assertIn('Edited [S1].',(folder/'memo.md').read_text())
            self.assertTrue(any(r['status']=='pending' for r in state['ledger']))

    def test_review_model_override_reopens_only_review_and_is_preserved(self):
        captured=[];original=FakeRouter.complete
        def capture(router,stage,model,*args,**kwargs):
            captured.append((stage,model))
            return original(router,stage,model,*args,**kwargs)
        with tempfile.TemporaryDirectory() as directory,patch.object(FakeRouter,'complete',capture):
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            count=len(captured)
            pipeline.run('AAPL','synthetic',directory=directory,review_model='synthetic/independent-reviewer')
            self.assertEqual(captured[count:],[('review_1','synthetic/independent-reviewer')])
            state=json.loads((folder/'state.json').read_text())
            self.assertEqual(state['review_history'][-1]['review_model'],'synthetic/independent-reviewer')
            self.assertTrue(state['complete'])
            count=len(captured)
            pipeline.run('AAPL','synthetic',directory=directory)
            self.assertEqual(len(captured),count)

    def test_review_model_change_invalidates_before_router_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            with patch.object(pipeline.core,'Router',side_effect=ValueError('Synthetic unavailable transport')):
                with self.assertRaisesRegex(ValueError,'unavailable transport'):
                    pipeline.run('AAPL','synthetic',directory=directory,review_model='synthetic/other')
            state=json.loads((folder/'state.json').read_text())
            self.assertFalse(state['complete'])
            self.assertEqual(state['quality_status'],'needs_review')
            self.assertEqual(state['review_model'],'synthetic/other')

    def test_primary_focus_preserves_source_identity_and_checks_archive_integrity(self):
        document='<html><h2>Research and Development</h2><p>R&D expense increased due to infrastructure and headcount expenses.</p></html>'
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);archive=folder/'sources_raw';archive.mkdir()
            target=archive/'S1.html';target.write_text(document)
            source={'id':'S1','kind':'sec_filing','archive':'sources_raw/S1.html',
                    'archive_sha256':pipeline.hashlib.sha256(document.encode()).hexdigest(),
                    'data':{'original':'preserved'}}
            self.assertTrue(pipeline._refresh_primary_focus(folder,[source]))
            self.assertEqual(source['data'],{'original':'preserved'})
            self.assertIn('infrastructure',source['primary_focus_excerpts'][0]['text'])
            self.assertFalse(pipeline._refresh_primary_focus(folder,[source]))
            target.write_text(document+'tampered')
            with self.assertRaisesRegex(ValueError,'checksum'):
                pipeline._refresh_primary_focus(folder,[source])
            source['archive']='../outside.html'
            with self.assertRaisesRegex(ValueError,'inside'):
                pipeline._refresh_primary_focus(folder,[source])

    def test_new_external_review_reopens_frozen_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            count=len(FakeRouter.calls)
            pipeline.run('AAPL','synthetic',directory=directory,review_notes='Verify the exact quoted date again.')
            self.assertEqual(FakeRouter.calls[count:],['review_1'])
            self.assertEqual(json.loads((folder/'state.json').read_text())['iteration'],1)

    def test_forecast_only_model_output_uses_server_reported_actuals(self):
        FakeRouter.forecasts_only=True
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            saved=json.loads((folder/'assumptions.json').read_text())
            self.assertEqual(saved['actuals'],plan()['actuals'])
            self.assertTrue(json.loads((folder/'state.json').read_text())['complete'])

    def test_incomplete_primary_inputs_do_not_allow_model_invented_actuals(self):
        missing={'actuals':{},'currency':'USD','coverage':{'complete':False,'status':'partial',
                 'conflicts':[],'missing_fields':['net_debt']}}
        with tempfile.TemporaryDirectory() as directory,patch.object(pipeline,'canonical_reported_inputs',return_value=missing):
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            self.assertNotIn('valuation_plan',FakeRouter.calls)
            state=json.loads((folder/'state.json').read_text())
            self.assertFalse(state['complete'])
            self.assertTrue(state['plan']['unavailable'])

    def test_missing_valuation_cannot_be_complete(self):
        FakeRouter.unavailable=True
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            state=json.loads((folder/'state.json').read_text())
            self.assertFalse(state['complete'])
            self.assertEqual(state['quality_status'],'missing_valuation')
            self.assertIn('missing_valuation',(folder/'memo.md').read_text())

    def test_withdrawn_target_removes_stale_downloads_but_archives_history(self):
        FakeRouter.revise=True;FakeRouter.withdraw=True
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            self.assertFalse((folder/'valuation.json').exists())
            self.assertTrue((folder/'revisions'/'model-before-1'/'valuation.json').exists())
            self.assertIn('No value target is calculated',(folder/'memo.md').read_text())

    def test_new_notes_at_revision_limit_require_explicit_higher_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory,max_revisions=0)
            with self.assertRaisesRegex(ValueError,'Increase max_revisions'):
                pipeline.run('AAPL','synthetic',directory=directory,max_revisions=0,review_notes='New evidence concern')
            state=json.loads((folder/'state.json').read_text())
            self.assertFalse(state['complete'])
            self.assertEqual(state['quality_status'],'needs_review')
            self.assertIn('needs_review',(folder/'memo.md').read_text())

    def test_combined_verdict_and_final_memo_status_are_consistent(self):
        finding={'id':'D1','severity':'major','issue':'Synthetic discrepancy','source_refs':['S1'],'fix':'Investigate'}
        with tempfile.TemporaryDirectory() as directory,patch.object(pipeline,'inspect_memo',return_value=[finding]):
            folder=pipeline.run('AAPL','synthetic',directory=directory,max_revisions=0)
            state=json.loads((folder/'state.json').read_text())
            self.assertEqual(state['review_history'][0]['model_verdict'],'pass')
            self.assertEqual(state['review_history'][0]['verdict'],'revise')
            self.assertEqual(state['quality_status'],'unresolved_findings')
            self.assertIn('unresolved_findings',(folder/'memo.md').read_text())

    def test_primary_supplement_reuses_searches_and_gets_a_fresh_review(self):
        extra={'title':'Complete primary filing','url':'https://example.com/complete-filing','kind':'regulatory_primary',
               'data':{'excerpts':[{'text':'Primary debt and cash table'}]},'raw_document':'<html>Primary cash table</html>'}
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            old_count=len(FakeRouter.calls)
            again=pipeline.run('AAPL','synthetic',directory=directory,extra_sources=[extra])
            self.assertEqual(again,folder)
            self.assertNotIn('web_business',FakeRouter.calls[old_count:])
            self.assertIn('review_1',FakeRouter.calls[old_count:])
            state=json.loads((folder/'state.json').read_text())
            self.assertEqual(len(state['sources']),3)
            self.assertTrue((folder/'sources_raw'/'S3.html').exists())
            self.assertTrue((folder/'revisions'/'model-before-supplement-1'/'valuation.json').exists())
            count=len(FakeRouter.calls)
            pipeline.run('AAPL','synthetic',directory=directory,extra_sources=[extra])
            self.assertEqual(len(FakeRouter.calls),count)

    def test_same_run_lock_prevents_duplicate_spending(self):
        with tempfile.TemporaryDirectory() as directory:
            with pipeline._lock(Path(directory)):
                with self.assertRaisesRegex(RuntimeError,'already active'):
                    with pipeline._lock(Path(directory)):pass

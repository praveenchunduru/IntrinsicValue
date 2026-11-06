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
        self.budget,self.ledger,self.checkpoint=budget,ledger,checkpoint
    def estimate(self,model,prompt,max_tokens=6000,web=False):
        return .01
    def validate_models(self,models):
        pass
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
        def canonical(sources, **kwargs):
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
                      patch.object(pipeline,'gather_us_calibration',return_value=([],[])),
                      patch.object(pipeline,'gather_weekly_beta',return_value=([],[])),
                      patch.object(pipeline,'gather_primary',return_value=([{'title':'Primary filing','url':'https://example.com/filing',
                         'kind':'filing','data':{'excerpt':'Revenue 1000000000'},'raw_document':'<html>public filing</html>'}],[])),
                      patch.object(pipeline.core,'Router',FakeRouter),patch.object(pipeline,'inspect_memo',return_value=[]),
                      patch.object(pipeline,'canonical_reported_inputs',side_effect=canonical),
                      patch.object(pipeline,'pin_reported_inputs',side_effect=pinned)]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()

    def test_changed_completed_downloads_are_preserved_reconstructed_and_freshly_reviewed(self):
        for name in ('memo.md','assumptions.json','valuation.json','sources.json','cashflows.csv','sensitivity.csv'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as directory:
                folder=pipeline.run('AAPL','synthetic',directory=directory)
                original=(folder/name).read_bytes()
                before=json.loads((folder/'state.json').read_text())
                edited=b'INTENTIONAL EXTERNAL EDIT FOR INTEGRITY REGRESSION'
                (folder/name).write_bytes(edited)
                FakeRouter.calls=[]
                pipeline.run('AAPL','synthetic',directory=directory)
                after=json.loads((folder/'state.json').read_text())
                self.assertEqual(FakeRouter.calls,['review_0'])
                self.assertEqual(after['ledger'][:len(before['ledger'])],before['ledger'])
                self.assertTrue(after['complete'])
                self.assertEqual((folder/name).read_bytes(),original)
                archived=folder/'revisions'/'artifact-before-1'/name
                self.assertEqual(archived.read_bytes(),edited)
                self.assertEqual(after['completed_artifact_hashes'],pipeline._delivered_artifact_hashes(folder,after))
                FakeRouter.calls=[]
                pipeline.run('AAPL','synthetic',directory=directory)
                self.assertEqual(FakeRouter.calls,[])

    def test_changed_primary_archive_stops_before_paid_work_and_can_restore_original(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            before=json.loads((folder/'state.json').read_text())
            name=next(s['archive'] for s in before['sources'] if s.get('archive'))
            original=(folder/name).read_bytes()
            (folder/name).write_bytes(b'CHANGED PRIMARY ARCHIVE')
            FakeRouter.calls=[]
            with self.assertRaisesRegex(RuntimeError,'Restore the original bytes'):
                pipeline.run('AAPL','synthetic',directory=directory)
            stopped=json.loads((folder/'state.json').read_text())
            self.assertFalse(stopped['complete'])
            self.assertEqual(stopped['quality_status'],'needs_review')
            self.assertEqual(stopped['ledger'],before['ledger'])
            self.assertEqual(FakeRouter.calls,[])
            self.assertEqual((folder/'revisions'/'artifact-before-1'/name).read_bytes(),b'CHANGED PRIMARY ARCHIVE')
            (folder/name).write_bytes(original)
            pipeline.run('AAPL','synthetic',directory=directory)
            final=json.loads((folder/'state.json').read_text())
            self.assertTrue(final['complete'])
            self.assertNotIn('artifact_integrity_blocked',final)
            self.assertEqual(FakeRouter.calls,['review_0'])

    def test_legacy_completion_without_delivered_hashes_requires_fresh_review(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            state=json.loads((folder/'state.json').read_text())
            state.pop('completed_artifact_hashes')
            pipeline.core.save(folder/'state.json',state)
            FakeRouter.calls=[]
            pipeline.run('AAPL','synthetic',directory=directory)
            self.assertEqual(FakeRouter.calls,['review_0'])
            final=json.loads((folder/'state.json').read_text())
            self.assertTrue(final['complete'])
            self.assertIn('Legacy completion',final['artifact_history'][0]['reason'])

    def test_edits_made_while_archive_integrity_is_blocked_are_preserved_on_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            state=json.loads((folder/'state.json').read_text())
            name=next(s['archive'] for s in state['sources'] if s.get('archive'))
            original=(folder/name).read_bytes()
            (folder/name).write_bytes(b'CHANGED RAW')
            with self.assertRaises(RuntimeError):
                pipeline.run('AAPL','synthetic',directory=directory)
            marker='INTENTIONAL MEMO EDIT WHILE RAW SOURCE IS BLOCKED'
            (folder/'memo.md').write_text(marker)
            (folder/name).write_bytes(original)
            pipeline.run('AAPL','synthetic',directory=directory)
            saved=[path.read_text() for path in (folder/'revisions').glob('artifact-before-*/memo.md')]
            self.assertIn(marker,saved)
            final=json.loads((folder/'state.json').read_text())
            self.assertTrue(final['complete'])

    def test_incomplete_resume_checks_all_registered_archive_kinds_before_paid_work(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            with patch.object(FakeRouter,'validate_models',side_effect=ValueError('Model unavailable')):
                with self.assertRaises(ValueError):
                    pipeline.run('AAPL','synthetic',directory=directory,review_model='missing/reviewer')
            prior=json.loads((folder/'state.json').read_text())
            self.assertFalse(prior['complete'])
            source=next(s for s in prior['sources'] if s.get('archive'))
            self.assertEqual(source['kind'],'filing')
            (folder/source['archive']).write_bytes(b'CHANGED NON-REGULATORY ARCHIVE WHILE PENDING')
            FakeRouter.calls=[]
            with self.assertRaisesRegex(RuntimeError,'Archived source integrity failed'):
                pipeline.run('AAPL','synthetic',directory=directory)
            final=json.loads((folder/'state.json').read_text())
            self.assertEqual(FakeRouter.calls,[])
            self.assertEqual(final['ledger'],prior['ledger'])
            self.assertFalse(final['complete'])

    def test_source_changed_during_review_cannot_acquire_completed_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            original=FakeRouter.complete
            def changed_during_request(router,stage,*args,**kwargs):
                value=original(router,stage,*args,**kwargs)
                if stage.startswith('review_'):
                    archive=next(Path(directory).glob('AAPL/full-*/sources_raw/*.html'))
                    archive.write_bytes(b'CHANGED WHILE REVIEW WAS IN FLIGHT')
                return value
            with patch.object(FakeRouter,'complete',changed_during_request):
                with self.assertRaisesRegex(RuntimeError,'Archived source integrity failed'):
                    pipeline.run('AAPL','synthetic',directory=directory)
            final=json.loads(next(Path(directory).glob('AAPL/full-*/state.json')).read_text())
            self.assertFalse(final['complete'])
            self.assertNotIn('completed_artifact_hashes',final)
            self.assertTrue(final['ledger'])

    def test_all_accepted_user_evidence_reaches_every_evidence_bearing_stage(self):
        FakeRouter.revise=True
        marker='MATERIAL CORRECTION AT THE ACCEPTED EVIDENCE TAIL'
        evidence='Synthetic dated evidence passage. '*1500+marker
        prompts={}
        original=FakeRouter.complete
        def capture(router,stage,model,prompt,**kwargs):
            prompts[stage]=prompt
            return original(router,stage,model,prompt,**kwargs)
        with tempfile.TemporaryDirectory() as directory,patch.object(FakeRouter,'complete',capture):
            folder=pipeline.run('AAPL','synthetic',directory=directory,evidence=evidence)
            for stage in ('evidence_synthesis','valuation_plan','full_draft','review_0','model_response_1','revise_1','review_1'):
                self.assertIn(marker,prompts[stage])
            saved=json.loads((folder/'sources.json').read_text())
            self.assertEqual(next(s['data'] for s in saved if s['kind']=='user_evidence'),evidence)

    def test_snapshot_and_supplied_evidence_both_reach_fresh_review(self):
        marker='Supplemental correction after the snapshot'
        prompts={}
        original=FakeRouter.complete
        def capture(router,stage,model,prompt,**kwargs):
            prompts[stage]=prompt
            return original(router,stage,model,prompt,**kwargs)
        with tempfile.TemporaryDirectory() as directory,patch.object(FakeRouter,'complete',capture):
            folder=pipeline.run('AAPL','synthetic',directory=directory,source_snapshot=[SOURCE],
                forecast_plan=plan(),draft=MEMO,evidence='Synthetic dated passage. '*1000+marker)
            saved=json.loads((folder/'sources.json').read_text())
            self.assertEqual([s['id'] for s in saved],['S1','S2'])
            self.assertEqual(saved[-1]['kind'],'user_evidence')
            self.assertIn(marker,prompts['review_0'])
            self.assertEqual(FakeRouter.calls,['review_0'])

    def test_legacy_collected_snapshot_recovers_omitted_accepted_evidence_before_review(self):
        marker='Accepted legacy correction must enter the fresh review'
        original=FakeRouter.complete
        prompts={}
        def capture(router,stage,model,prompt,**kwargs):
            prompts[stage]=prompt
            return original(router,stage,model,prompt,**kwargs)
        with tempfile.TemporaryDirectory() as directory,patch.object(FakeRouter,'complete',capture):
            kwargs={'source_snapshot':[SOURCE],'forecast_plan':plan(),'draft':MEMO,'evidence':marker}
            folder=pipeline.run('AAPL','synthetic',directory=directory,**kwargs)
            before=json.loads((folder/'state.json').read_text())
            legacy=copy.deepcopy(before)
            legacy['sources']=[s for s in legacy['sources'] if s['kind']!='user_evidence']
            legacy.pop('workflow_contract')
            (folder/'state.json').write_text(json.dumps(legacy))
            count=len(FakeRouter.calls)
            self.assertEqual(pipeline.run('AAPL','synthetic',directory=directory,**kwargs),folder)
            after=json.loads((folder/'state.json').read_text())
            self.assertEqual(FakeRouter.calls[count:],['review_1'])
            self.assertIn(marker,prompts['review_1'])
            self.assertEqual(after['sources'][-1]['kind'],'user_evidence')
            self.assertEqual(after['ledger'][:len(before['ledger'])],before['ledger'])
            self.assertTrue((folder/'revisions/evidence-recovery-1/sources.json').exists())

    def test_changed_review_contract_reuses_model_and_memo_but_requires_new_review(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            before=json.loads((folder/'state.json').read_text())
            count=len(FakeRouter.calls)
            with patch.object(pipeline,'REVIEW_SCHEMA',pipeline.REVIEW_SCHEMA+'\nRequire a current evidence audit.'):
                self.assertEqual(pipeline.run('AAPL','synthetic',directory=directory),folder)
            after=json.loads((folder/'state.json').read_text())
            self.assertEqual(FakeRouter.calls[count:],['review_1'])
            self.assertEqual(after['ledger'][:len(before['ledger'])],before['ledger'])
            self.assertEqual(after['sources'],before['sources'])
            self.assertEqual(after['current_memo'],before['current_memo'])
            self.assertEqual(after['contract_history'][-1]['changed'],['review'])
            self.assertTrue((folder/'revisions/contract-before-1/review-history.json').exists())

    def test_changed_forecast_contract_invalidates_plan_and_reuses_exact_unchanged_descendants(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            count=len(FakeRouter.calls)
            with patch.object(pipeline,'FORECAST_SCHEMA',pipeline.FORECAST_SCHEMA+'\nJustify the forecast horizon.'):
                self.assertEqual(pipeline.run('AAPL','synthetic',directory=directory),folder)
            # The new forecast contract produces the same model in this mock;
            # the exact old draft prompt can reuse its successfully saved output.
            self.assertEqual(FakeRouter.calls[count:],['valuation_plan','review_1'])
            state=json.loads((folder/'state.json').read_text())
            self.assertTrue(state['complete'])
            self.assertEqual(state['contract_history'][-1]['changed'],['forecast'])

    def test_changed_system_preserves_metered_history_in_same_folder_and_regenerates_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            before=json.loads((folder/'state.json').read_text())
            count=len(FakeRouter.calls)
            with patch.object(pipeline.core,'SYSTEM',pipeline.core.SYSTEM+'\nVerify every load-bearing statement.'):
                self.assertEqual(pipeline.run('AAPL','synthetic',directory=directory),folder)
            state=json.loads((folder/'state.json').read_text())
            self.assertEqual(state['ledger'][:len(before['ledger'])],before['ledger'])
            self.assertEqual(FakeRouter.calls[count:],['web_business','web_challenge','web_expectations','web_claims',
                'evidence_synthesis','valuation_plan','full_draft','review_1'])
            self.assertEqual(len(list(Path(directory).glob('AAPL/full-*'))),1)
            self.assertEqual(state['sources'],before['sources'])

    def test_implementation_contract_refresh_does_not_repay_unchanged_stage_prompts(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            count=len(FakeRouter.calls)
            contract=pipeline._workflow_contract();contract['implementation']='synthetic-code-update'
            with patch.object(pipeline,'_workflow_contract',return_value=contract):
                self.assertEqual(pipeline.run('AAPL','synthetic',directory=directory),folder)
            self.assertEqual(FakeRouter.calls[count:],['review_1'])

    def test_legacy_current_system_folder_preserves_spend_and_reuses_proven_unchanged_stages(self):
        prompts={}
        original=FakeRouter.complete
        def capture(router,stage,model,prompt,max_tokens=6000,web=False):
            prompts[stage]=(model,prompt,max_tokens,web)
            return original(router,stage,model,prompt,max_tokens=max_tokens,web=web)
        with tempfile.TemporaryDirectory() as directory,patch.object(FakeRouter,'complete',capture):
            folder=pipeline.run('AAPL','synthetic',directory=directory)
            state=json.loads((folder/'state.json').read_text())
            old_stages={}
            for stage,(model,prompt,tokens,web) in prompts.items():
                old=stage+':'+pipeline._digest([prompt,model,tokens,web])[:12]
                current=stage+':'+pipeline._digest([prompt,model,tokens,web,pipeline.core.SYSTEM])[:12]
                old_stages[old]=state['stages'][current]
            state['stages']=old_stages
            state.pop('workflow_contract')
            (folder/'state.json').write_text(json.dumps(state))
            (folder/'run-identity.json').unlink()
            easy,strong=pipeline.core.MODELS['balanced']
            parts=[pipeline.VERSION,'AAPL','balanced',easy,strong,'',None,
                pipeline.datetime.now(pipeline.timezone.utc).date().isoformat(),pipeline.core.SYSTEM]
            legacy=folder.parent/('full-'+pipeline._digest(parts)[:16])
            folder.rename(legacy)
            count=len(FakeRouter.calls)
            self.assertEqual(pipeline.run('AAPL','synthetic',directory=directory),legacy)
            after=json.loads((legacy/'state.json').read_text())
            self.assertEqual(FakeRouter.calls[count:],['review_1'])
            self.assertEqual(after['ledger'][:len(state['ledger'])],state['ledger'])
            self.assertEqual(after['legacy_stage_system_sha256'],pipeline._digest(pipeline.core.SYSTEM))

    def test_quote_repair_preserves_substance_and_uses_only_frozen_text(self):
        original=FakeRouter.complete
        prompts={}
        def malformed(router,stage,model,prompt,**kwargs):
            result=original(router,stage,model,prompt,**kwargs)
            prompts[stage]=prompt
            if stage in ('review_0','review_format_0'):
                finding={'id':'F1','severity':'major','issue':'Synthetic unresolved economics',
                         'source_refs':['S1'],'fix':'Investigate the forecast',
                         'current_excerpt':('Synthetic ... research' if stage=='review_0' else
                                            'Synthetic financial research [S1].')}
                return json.dumps({'verdict':'revise','findings':[finding]})
            return result
        with tempfile.TemporaryDirectory() as directory,patch.object(FakeRouter,'complete',malformed):
            folder=pipeline.run('AAPL','synthetic',directory=directory,max_revisions=0)
            state=json.loads((folder/'state.json').read_text())
            self.assertFalse(state['complete'])
            self.assertEqual(state['review_history'][0]['findings'][0]['severity'],'major')
            self.assertTrue((folder/'reviews/review-0-unparsed.txt').exists())
            self.assertTrue((folder/'reviews/review-0-reformatted.txt').exists())
            self.assertIn('CURRENT FROZEN MEMO:',prompts['review_format_0'])
            self.assertIn('RECORDED REVIEW:',prompts['review_format_0'])
            self.assertNotIn('Deterministic diagnostics (not semantic proof)',prompts['review_format_0'])

    def test_invalid_repaired_quote_still_blocks_completion_and_keeps_raw_attempts(self):
        original=FakeRouter.complete
        def malformed(router,stage,*args,**kwargs):
            result=original(router,stage,*args,**kwargs)
            if stage.startswith('review_'):
                return json.dumps({'verdict':'revise','findings':[{'id':'F1','severity':'major',
                    'issue':'Unresolved economics','source_refs':['S1'],'fix':'Investigate',
                    'current_excerpt':'An invented excerpt absent from the memo'}]})
            return result
        with tempfile.TemporaryDirectory() as directory,patch.object(FakeRouter,'complete',malformed):
            with self.assertRaisesRegex(ValueError,'contiguous wording'):
                pipeline.run('AAPL','synthetic',directory=directory)
            folder=next(Path(directory).glob('AAPL/full-*'))
            state=json.loads((folder/'state.json').read_text())
            self.assertFalse(state['complete'])
            self.assertEqual(state['review_history'],[])
            self.assertTrue((folder/'reviews/review-0-unparsed.txt').exists())
            self.assertTrue((folder/'reviews/review-0-reformatted.txt').exists())

    def test_provided_snapshot_forecast_and_body_require_only_fresh_review(self):
        snapshot=[copy.deepcopy(SOURCE)]
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(pipeline.core,'collect',side_effect=AssertionError('snapshot must not refresh quotes')), \
                 patch.object(pipeline,'gather_us_calibration',side_effect=AssertionError('snapshot must not refresh rates')):
                folder=pipeline.run('AAPL','synthetic-key',directory=directory,
                    source_snapshot=snapshot,forecast_plan=plan(),draft=MEMO)
            self.assertEqual(FakeRouter.calls,['review_0'])
            saved=json.loads((folder/'state.json').read_text())
            self.assertTrue(saved['complete'])
            self.assertEqual(saved['sources'][0]['id'],'S1')
            self.assertIn('not refreshed',' '.join(saved['gaps']))
            costs=json.loads((folder/'cost-summary.json').read_text())
            self.assertEqual(costs['totals']['request_count'],1)
            changed=plan();changed['scenarios']['base']['ebit_margins']=[.25]*5
            pipeline.run('AAPL','synthetic-key',directory=directory,max_revisions=1,
                source_snapshot=snapshot,forecast_plan=changed,draft=MEMO)
            updated=json.loads((folder/'state.json').read_text())
            self.assertTrue(updated['complete'])
            self.assertEqual(FakeRouter.calls,['review_0','review_1'])
            self.assertEqual(len(updated['ledger']),2)
            self.assertTrue((folder/'revisions/model-before-provided-2/assumptions.json').exists())

    def test_snapshot_and_provided_forecast_reject_invalid_inputs_before_paid_calls(self):
        invalid_snapshot=[dict(SOURCE,id='S404')]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'consecutive'):
                pipeline.run('AAPL','synthetic-key',directory=directory,source_snapshot=invalid_snapshot)
            proposal=plan();proposal['source_refs']['revenue']=['S404']
            with self.assertRaisesRegex(ValueError,'Unknown source'):
                pipeline.run('AAPL','synthetic-key',directory=directory,source_snapshot=[SOURCE],
                             forecast_plan=proposal,draft=MEMO)
            self.assertEqual(FakeRouter.calls,[])

    def test_new_optional_nested_sources_are_validated_before_any_paid_work(self):
        proposal=plan()
        proposal['repurchase_tests']=[{'name':'bad_reference','amount':10000000,'price':50,
            'source_refs':['S404'],'label':'Synthetic invalid reference',
            'interpretation':'forward_conditional_illustration'}]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'Unknown source reference for repurchase_tests'):
                pipeline.run('AAPL','synthetic',directory=directory,source_snapshot=[SOURCE],forecast_plan=proposal)
            self.assertEqual(FakeRouter.calls,[])
            state=json.loads(next(Path(directory).glob('AAPL/full-*/state.json')).read_text())
            self.assertEqual(state['ledger'],[])

    def test_complete_archives_recomputes_and_reuses_without_key_on_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=pipeline.run('AAPL','secret-synthetic-key',directory=directory)
            self.assertEqual(len(FakeRouter.calls),8)
            self.assertTrue((folder/'sources_raw'/'S2.html').exists())
            self.assertTrue((folder/'cashflows.csv').exists())
            self.assertIn('Weighted intrinsic value per share',(folder/'memo.md').read_text())
            self.assertEqual(pipeline.run('AAPL','secret-synthetic-key',directory=directory),folder)
            self.assertEqual(len(FakeRouter.calls),8)
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
            self.assertIn('Review is incomplete',visible)
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
            self.assertIn('needs_review',(folder/'memo.md').read_text())

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
            with self.assertRaisesRegex(RuntimeError,'before paid research') as stopped:
                pipeline.run('AAPL','synthetic',directory=directory)
            folder=stopped.exception.folder
            self.assertEqual(FakeRouter.calls,[])
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


class PrimaryDebtIngestionTests(unittest.TestCase):
    def source(self):
        from tests.public_runner.test_primary_debt import filing
        return {'title':'Synthetic primary debt note','url':'https://www.sec.gov/Archives/synthetic.html',
            'kind':'regulatory_primary','source_tier':'regulatory_primary','raw_document':filing(),
            'data':{'metadata':{'reportDate':'2026-06-30'}}}

    def test_primary_scope_and_exact_excerpts_enter_archived_evidence_hash(self):
        source=self.source()
        before=copy.deepcopy(source)
        with tempfile.TemporaryDirectory() as directory:
            registered=pipeline._register(Path(directory),[],source)
            data=registered['data']
            declaration=data['reported_input_classifications']['financial_debt_total']
            self.assertEqual(declaration['method'],'primary_debt_table_total')
            self.assertEqual(declaration['normalized_decimal'],'40294000000')
            self.assertEqual(declaration['source_excerpt'],data['excerpts'][0]['text'])
            self.assertEqual(data['excerpt_chars'],len(data['excerpts'][0]['text']))
            self.assertEqual(registered['evidence_sha256'],pipeline._digest(data))
            self.assertTrue((Path(directory)/registered['archive']).is_file())
            self.assertEqual(source,before)
        prepared=pipeline._prepare_source(source)
        repeated=pipeline._prepare_source(prepared)
        self.assertEqual(repeated['data']['excerpts'],prepared['data']['excerpts'])

    def test_unverified_sources_and_invalid_filing_dates_never_auto_classify_debt(self):
        variants=[]
        unverified=self.source();unverified.update(kind='search_excerpt',source_tier='search_excerpt_unverified')
        variants.append(unverified)
        for anchor in (None,'2026-99-99','20260630'):
            source=self.source();source['data']['metadata']['reportDate']=anchor
            variants.append(source)
        with patch.object(pipeline,'classify_primary_debt') as classify:
            for source in variants:
                self.assertNotIn('reported_input_classifications',pipeline._prepare_source(source)['data'])
            classify.assert_not_called()

    def test_agreeing_manual_scope_is_preserved_and_conflicting_scope_is_rejected(self):
        source=pipeline._prepare_source(self.source())
        manual=source['data']['reported_input_classifications']['financial_debt_total']
        manual.update(method='manual_primary_review',reviewed=True,reviewer_note='Explicit fixture review')
        result=pipeline._prepare_source(source)
        self.assertEqual(result['data']['reported_input_classifications']['financial_debt_total'],manual)
        bad=copy.deepcopy(source)
        bad['data']['reported_input_classifications']['financial_debt_total']['normalized_decimal']='1'
        with self.assertRaisesRegex(ValueError,'conflicts with the supplied'):
            pipeline._prepare_source(bad)


class SourceArchiveBoundaryTests(unittest.TestCase):
    def test_invalid_raw_type_or_encoded_size_is_rejected_before_parse_write_or_append(self):
        bad_values=[object(),bytearray(b'not an accepted immutable byte archive'),
            b'x'*(pipeline.MAX_DOCUMENT_BYTES+1),
            '\u20ac'*(pipeline.MAX_DOCUMENT_BYTES//3+1)]
        for raw in bad_values:
            for prepared in (False,True):
                with self.subTest(raw_type=type(raw).__name__,prepared=prepared),tempfile.TemporaryDirectory() as directory:
                    folder=Path(directory)
                    archive=folder/'sources_raw';archive.mkdir()
                    existing=archive/'S2.html';existing.write_bytes(b'EXISTING ORIGINAL ARCHIVE')
                    retained=[{'id':'S1','data':{'preserved':True}}]
                    before=copy.deepcopy(retained)
                    source={'title':'Rejected archive','kind':'regulatory_primary','data':{'metadata':{}},'raw_document':raw}
                    with patch.object(pipeline,'inline_facts',side_effect=AssertionError('Rejected bytes must not be parsed')):
                        with self.assertRaisesRegex(ValueError,'raw_document'):
                            pipeline._register(folder,retained,source,_prepared=prepared)
                    self.assertEqual(retained,before)
                    self.assertEqual(existing.read_bytes(),b'EXISTING ORIGINAL ARCHIVE')
                    self.assertEqual(list(archive.iterdir()),[existing])
                    self.assertEqual(source['data'],{'metadata':{}})
                    self.assertNotIn('id',source)

    def test_collection_preparation_rejects_oversized_raw_before_parser(self):
        source={'raw_document':'x'*(pipeline.MAX_DOCUMENT_BYTES+1),'data':{}}
        with patch.object(pipeline,'inline_facts',side_effect=AssertionError('Not parsed')):
            with self.assertRaisesRegex(ValueError,'20 MB'):
                pipeline._prepare_source(source)


class CompanyfactsRegistrationTests(unittest.TestCase):
    def test_reassigned_context_rows_bind_to_new_source_and_canonical_json_is_archived(self):
        from tests.public_runner.test_companyfacts import normalize, payload
        source=normalize(source_id='S2')
        source.update(raw_document=json.dumps(payload(),sort_keys=True,separators=(',',':')),
            archive_extension='.json',archive_format='canonical decoded SEC JSON, not wire bytes')
        before=copy.deepcopy(source)
        retained=[{'id':'S1'},{'id':'S2'},{'id':'S3'}]
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(pipeline,'inline_facts',side_effect=AssertionError('JSON archives must not be parsed as inline HTML')):
            registered=pipeline._register(Path(directory),retained,source)
            self.assertEqual(registered['id'],'S4')
            self.assertTrue(registered['archive'].endswith('.json'))
            self.assertEqual((Path(directory)/registered['archive']).read_text(),source['raw_document'])
            self.assertTrue(all(row['source_refs']==['S4'] for row in registered['data']['financial_context_table']))
            self.assertEqual(registered['data']['reported_facts'],before['data']['reported_facts'])
            self.assertEqual(registered['data']['metadata'],before['data']['metadata'])
            self.assertEqual(registered['evidence_sha256'],pipeline._digest(registered['data']))
            # A later free refresh can recognize the same data despite a
            # collector's temporary S2 identifier and this run's assigned S4.
            self.assertEqual(pipeline._rebind_companyfacts_data(registered['data'],'self'),
                pipeline._rebind_companyfacts_data(source['data'],'self'))
            self.assertEqual(source,before)

    def test_large_companyfacts_archive_is_checksummed_without_html_focus_parsing(self):
        from tests.public_runner.test_companyfacts import normalize, payload
        document=payload();document['unselected_payload_metadata']='x'*3000001
        source=normalize(document,source_id='S2')
        source.update(raw_document=json.dumps(document,sort_keys=True,separators=(',',':')),
            archive_extension='.json')
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(pipeline,'archive_focus_excerpts',side_effect=AssertionError('JSON is not a prose filing')):
            folder=Path(directory)
            registered=pipeline._register(folder,[],source)
            self.assertFalse(pipeline._refresh_primary_focus(folder,[registered]))
            path=folder/registered['archive']
            path.write_bytes(path.read_bytes()+b' ')
            with self.assertRaisesRegex(ValueError,'checksum'):
                pipeline._refresh_primary_focus(folder,[registered])


class ComputedMemoExhibitTests(unittest.TestCase):
    def test_reader_exhibits_use_consistent_computed_multiples_returns_and_cash_share_cases(self):
        from tests.public_runner.test_memo_diagnostics import plan as economic_plan, repurchase
        proposal=economic_plan()
        proposal['entry_policy']={'basis':'weighted','margin_of_safety':.25,
            'label':'Synthetic explicitly selected research rule','rationale':'Fixture valuation discount with no hidden default.'}
        proposal['repurchase_tests']=[repurchase()]
        result=pipeline.evaluate(proposal)
        text=pipeline.valuation_section(proposal,result)
        # Independent flat-cashflow fixture: market EV 1.92bn / EBIT .2bn,
        # original exit EV1.5bn / EBIT .2bn; unchanged-multiple yield .15/1.92.
        self.assertIn('9.60×',text)
        self.assertIn('7.50×',text)
        self.assertIn('7.81%',text)
        self.assertIn('USD 11.85',text)  # Weighted15.8 * explicitly selected75%.
        self.assertIn('USD 15.58',text)  # Remaining1.48bn / remaining95m shares.
        self.assertIn('5,000,000',text)
        self.assertIn('first explicit forecast year',text)
        self.assertIn('not historical fair value',text)

    def test_assumptions_label_follows_canonical_lease_allocation_and_selected_asset_policy(self):
        from tests.public_runner.test_inputs import sources as primary_sources, fact
        from tests.public_runner.test_memo_diagnostics import plan as economic_plan
        for combined in (False,True):
            records=primary_sources()
            if combined:
                facts=records[0]['data']['inline_facts']
                facts[:]=[row for row in facts if row['concept'].split(':')[-1] not in
                    {'CommercialPaper','LongTermDebtCurrent','LongTermDebtNoncurrent'}]
                facts.append(fact('us-gaap:DebtAndCapitalLeaseObligations',81567000000))
            locked,_=pipeline._lock_plan(economic_plan(),records)
            label=locked['assumptions_label']
            self.assertIn('Only the cash and investment assets selected in the pinned bridge',label)
            self.assertIn('Omitted investment categories are not reported zeros',label)
            if combined:
                self.assertIn('already included in the selected combined net-debt subtotal',label)
                self.assertNotIn('outside the selected financial net-debt subtotal',label)
            else:
                self.assertIn('outside the selected financial net-debt subtotal',label)

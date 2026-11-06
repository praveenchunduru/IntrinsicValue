"""Offline full-contract budget simulations, never provider-cost measurements.

The large archived example supplies realistic evidence, forecast and memo shapes.
A clearly synthetic disclosure completes its short-term financing scope for this
fixture. Collection and HTTP are mocked; actual pipeline prompts, valuation,
diagnostics, reservations, checkpoints and resume behavior are exercised.
"""
import contextlib
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from investmentideas import core, pipeline


EXAMPLE = Path(__file__).resolve().parents[2]/'examples'/'AAPL-2026-11-06-revised'
EASY, STRONG = core.MODELS['balanced']
RATES = {EASY:(.09e-6,.30e-6), STRONG:(3e-6,15e-6)}


class FullContractTransport:
    def __init__(self, *, revisions=0, timeout_once=False):
        self.sources=json.loads((EXAMPLE/'sources.json').read_text())
        primary=next(s for s in self.sources if s['id']=='S4')
        disclosure='SYNTHETIC TEST DISCLOSURE: commercial paper exhausts short-term financing in this fixture.'
        primary['data']['excerpts'].append(disclosure)
        primary['data']['reported_input_classifications']={'short_term_financing_total':{
            'reviewed':True,'concept':'us-gaap:CommercialPaper','instant':'2026-09-26',
            'source_excerpt':disclosure}}
        primary['title']='Synthetic full-workflow fixture, based on archived filing structure'
        proposal=json.loads((EXAMPLE/'assumptions.json').read_text())
        proposal['entry_policy']={'basis':'weighted','margin_of_safety':0,
            'label':'Synthetic test entry policy',
            'rationale':'Use the disclosed weighted value without an additional discount in this software fixture.'}
        proposal['repurchase_tests']=[{'name':'synthetic_cash_case','amount':1000000000,'price':300,
            'source_refs':['S4'],'label':'Synthetic one-period cash-funded illustration',
            'interpretation':'forward_conditional_illustration'}]
        pinned={'reported_inputs','actuals','net_debt_bridge','other_claims_bridge','input_dates',
                'market_price','currency','amount_unit','share_unit','valuation_date','source_refs'}
        self.proposal=json.dumps({k:v for k,v in proposal.items() if k not in pinned},separators=(',',':'))
        self.memo=(EXAMPLE/'memo-body.md').read_text()
        self.excerpt=next(p.strip() for p in self.memo.split('\n\n')
                          if len(p.strip())>200 and not p.strip().startswith(('#','|','*')))[:150]
        self.revisions,self.timeout_once=revisions,timeout_once
        self.review_count=0
        self.requests=[]

    def transport(self,url,*,key=None,payload=None,timeout=90):
        if url.endswith('/models'):
            return {'data':[{'id':model,'pricing':{'prompt':str(pin),'completion':str(pout)},
                'supported_parameters':['max_tokens','temperature','response_format']}
                for model,(pin,pout) in RATES.items()]}
        if url!=core.BASE+'/chat/completions':
            raise AssertionError('Unexpected network request in offline fixture')
        prompt=payload['messages'][1]['content']
        self.requests.append(copy.deepcopy(payload))
        annotations=[]
        if prompt.startswith('Research '):
            content='Synthetic external research notes. '*100
            lane=len(self.requests)
            annotations=[{'url_citation':{'url':f'https://example.invalid/synthetic-lane-{lane}',
                'title':'Synthetic search growth fixture','content':'Synthetic external source excerpt. '*60}}]
        elif prompt.startswith('Build a dated evidence table'):
            content=('Dated synthetic synthesis: trace reported revenue, category margins, debt and '
                     'share count to the archived primary evidence. Forward growth and required returns '
                     'remain house judgments; compare competing explanations and missing evidence. [S4]\n')*50
        elif prompt.startswith('Build an investment valuation plan'):
            content=self.proposal
        elif prompt.startswith('Write a complete investment-committee'):
            if self.timeout_once:
                self.timeout_once=False
                raise RuntimeError('Synthetic uncertain transport interruption')
            content=self.memo
        elif prompt.startswith('Independently audit'):
            self.review_count+=1
            findings=[]
            if self.review_count<=self.revisions:
                findings=[{'id':'F1','severity':'major','issue':'Synthetic fixture needs an explicit monitoring clarification',
                    'source_refs':['S4'],'current_excerpt':self.excerpt,'fix':'Clarify the dated monitoring condition'}]
            content=json.dumps({'verdict':'revise' if findings else 'pass','findings':findings})
        elif prompt.startswith('Investigate '):
            content='Synthetic targeted research: existing evidence supports a prose clarification. '*50
        elif prompt.startswith('Address these review findings'):
            # Exercise the complete forecast contract and recomputation, rather
            # than allowing a tiny canned response to hide prompt growth.
            content=self.proposal
        elif prompt.startswith('Revise the complete memo'):
            content=self.memo+'\nSynthetic fixture revision: monitoring conditions remain dated house judgments. [S4]\n'
        else:
            raise AssertionError('Unexpected full-workflow stage')
        # Explicit simulation assumptions: input bytes/3.05 approximates the
        # audited large-review ratio; prose/JSON outputs use bytes/4. Neither
        # assumption changes Router's conservative byte-based reservations.
        prompt_tokens=math.ceil(len((core.SYSTEM+prompt).encode())/3.05)
        completion_tokens=math.ceil(len(content.encode())/4)
        if completion_tokens>payload['max_tokens']:
            raise AssertionError('Fixture response exceeds its actual stage output contract')
        pin,pout=RATES[payload['model']]
        cost=prompt_tokens*pin+completion_tokens*pout+(.007 if payload.get('plugins') else 0)
        return {'choices':[{'finish_reason':'stop','message':{'content':content,'annotations':annotations}}],
            'model':payload['model'],'usage':{'prompt_tokens':prompt_tokens,'completion_tokens':completion_tokens,
                'cost':cost,'prompt_tokens_details':{'cached_tokens':0}}}

    @contextlib.contextmanager
    def installed(self):
        with contextlib.ExitStack() as stack:
            collected=stack.enter_context(patch.object(core,'collect',return_value=(self.sources,[])))
            stack.enter_context(patch.object(pipeline,'gather_primary',return_value=([],[])))
            stack.enter_context(patch.object(pipeline,'gather_us_calibration',return_value=([],[])))
            stack.enter_context(patch.object(pipeline,'gather_weekly_beta',return_value=([],[])))
            stack.enter_context(patch.object(core,'request_json',side_effect=self.transport))
            yield collected


class BudgetWorkflowTests(unittest.TestCase):
    def test_two_dollars_is_rejected_after_free_collection_before_any_paid_research(self):
        fixture=FullContractTransport()
        with tempfile.TemporaryDirectory() as directory,fixture.installed() as collected:
            with self.assertRaisesRegex(core.BudgetExceeded,'before paid research') as stopped:
                pipeline.run('AAPL','synthetic-key',directory=directory,budget=2)
            folder=stopped.exception.folder
            state=json.loads((folder/'state.json').read_text())
            preflight=json.loads((folder/'budget-preflight.json').read_text())
            collected.assert_called_once_with('AAPL',include_archives=True)
            self.assertEqual(fixture.requests,[])
            self.assertEqual(state['ledger'],[])
            self.assertEqual(state['quality_status'],'budget_paused')
            self.assertGreater(preflight['known_context_reservation_floor_usd'],2)
            self.assertIn('not a completion guarantee',preflight['basis'])
            self.assertIn('--budget 10',str(stopped.exception))

    def test_default_allowance_completes_fresh_ticker_draft_review_and_bounded_revision(self):
        for revisions in (0,1,3):
            fixture=FullContractTransport(revisions=revisions)
            with self.subTest(revisions=revisions),tempfile.TemporaryDirectory() as directory,fixture.installed() as collected:
                folder=pipeline.run('AAPL','synthetic-key',directory=directory)
                state=json.loads((folder/'state.json').read_text())
                collected.assert_called_once_with('AAPL',include_archives=True)
                self.assertTrue(state['complete'])
                self.assertEqual(state['quality_status'],'reviewed_draft')
                self.assertEqual(state['iteration'],revisions)
                stages=[row['stage'] for row in state['ledger']]
                self.assertEqual(stages[:4],['web_business','web_challenge','web_expectations','web_claims'])
                self.assertIn('valuation_plan',stages)
                self.assertIn('full_draft',stages)
                self.assertIn('review_0',stages)
                if revisions:
                    self.assertEqual(stages[-4:],[f'targeted_research_{revisions}',f'model_response_{revisions}',
                        f'revise_{revisions}',f'review_{revisions}'])
                prompts=[p['messages'][1]['content'] for p in fixture.requests]
                self.assertGreater(len(pipeline._evidence_context(state['sources']).encode()),190000)
                self.assertGreater(max(len(p.encode()) for p in prompts),500000)
                self.assertTrue(any('IMMUTABLE reported inputs' in p for p in prompts))
                self.assertTrue(any(pipeline.UNDERWRITING_GUIDANCE in p for p in prompts))
                spent,peak=0,0
                for row in state['ledger']:
                    peak=max(peak,spent+row['reserved_usd'])
                    spent+=row['charged_or_reserved_usd']
                    self.assertEqual(row['usage']['prompt_tokens_details']['cached_tokens'],0)
                self.assertGreater(peak,2)
                self.assertLessEqual(peak,pipeline.DEFAULT_BUDGET)
                self.assertLess(spent,pipeline.DEFAULT_BUDGET)
                before=len(fixture.requests)
                pipeline.run('AAPL','synthetic-key',directory=directory)
                self.assertEqual(len(fixture.requests),before)

    def test_uncertain_request_remains_charged_and_resuming_uses_same_total_allowance(self):
        fixture=FullContractTransport(timeout_once=True)
        with tempfile.TemporaryDirectory() as directory,fixture.installed():
            with self.assertRaisesRegex(RuntimeError,'uncertain'):
                pipeline.run('AAPL','synthetic-key',directory=directory)
            folder=next(Path(directory).glob('AAPL/full-*'))
            initial=json.loads((folder/'state.json').read_text())
            uncertain=initial['ledger'][-1]
            self.assertEqual(uncertain['stage'],'full_draft')
            self.assertEqual(uncertain['status'],'pending')
            self.assertEqual(uncertain['charged_or_reserved_usd'],uncertain['reserved_usd'])
            paid=len(fixture.requests)
            with self.assertRaises(core.BudgetExceeded):
                pipeline.run('AAPL','synthetic-key',directory=directory,budget=2)
            self.assertEqual(len(fixture.requests),paid)
            pipeline.run('AAPL','synthetic-key',directory=directory)
            final=json.loads((folder/'state.json').read_text())
            self.assertTrue(final['complete'])
            self.assertEqual(final['ledger'][:len(initial['ledger'])],initial['ledger'])
            self.assertEqual([row['stage'] for row in final['ledger'][len(initial['ledger']):]],['full_draft','review_0'])

    def test_unavailable_final_reviewer_stops_before_all_paid_research(self):
        fixture=FullContractTransport()
        with tempfile.TemporaryDirectory() as directory,fixture.installed():
            with self.assertRaisesRegex(ValueError,'Model unavailable'):
                pipeline.run('AAPL','synthetic-key',directory=directory,review_model='synthetic/missing-reviewer')
            self.assertEqual(fixture.requests,[])
            state=json.loads(next(Path(directory).glob('AAPL/full-*/state.json')).read_text())
            self.assertEqual(state['ledger'],[])

    def test_unsupported_selected_model_pricing_stops_before_all_paid_research(self):
        fixture=FullContractTransport()
        original=fixture.transport
        def unsupported(url,**kwargs):
            result=original(url,**kwargs)
            if url.endswith('/models'):
                result['data'][1]['pricing']['request']='.01'
            return result
        fixture.transport=unsupported
        with tempfile.TemporaryDirectory() as directory,fixture.installed():
            with self.assertRaisesRegex(ValueError,'token-only pricing'):
                pipeline.run('AAPL','synthetic-key',directory=directory)
            self.assertEqual(fixture.requests,[])

    def test_missing_primary_input_fails_before_paid_search_or_synthesis(self):
        fixture=FullContractTransport()
        primary=next(s for s in fixture.sources if s['id']=='S4')
        primary['data'].pop('reported_input_classifications')
        with tempfile.TemporaryDirectory() as directory,fixture.installed():
            with self.assertRaisesRegex(RuntimeError,'net_debt') as stopped:
                pipeline.run('AAPL','synthetic-key',directory=directory)
            state=json.loads((stopped.exception.folder/'state.json').read_text())
            self.assertEqual(state['ledger'],[])
            self.assertEqual(fixture.requests,[])
            self.assertEqual(state['quality_status'],'missing_valuation')

    def test_retry_after_missing_primary_input_collects_again_and_preserves_old_sources(self):
        fixture=FullContractTransport()
        primary=next(s for s in fixture.sources if s['id']=='S4')
        classification=primary['data'].pop('reported_input_classifications')
        with tempfile.TemporaryDirectory() as directory,fixture.installed() as collected:
            with self.assertRaisesRegex(RuntimeError,'net_debt') as stopped:
                pipeline.run('AAPL','synthetic-key',directory=directory)
            folder=stopped.exception.folder
            before=json.loads((folder/'state.json').read_text())
            self.assertEqual(fixture.requests,[])
            primary['data']['reported_input_classifications']=classification
            self.assertEqual(pipeline.run('AAPL','synthetic-key',directory=directory),folder)
            after=json.loads((folder/'state.json').read_text())
            self.assertEqual(collected.call_count,2)
            self.assertTrue(after['complete'])
            self.assertEqual(after['sources'][:len(before['sources'])],before['sources'])
            self.assertGreater(len(after['sources']),len(before['sources']))
            self.assertNotIn('coverage_preflight_failed',after)
            self.assertEqual(after['collection_history'][0]['reason'],'Retry free collection after incomplete primary inputs')

    def test_market_only_failed_collection_can_retry_with_primary_access(self):
        fixture=FullContractTransport()
        market=next(s for s in fixture.sources if s['kind']=='market_snapshot')
        with tempfile.TemporaryDirectory() as directory,fixture.installed() as collected, \
                patch.object(pipeline,'yahoo_financials',side_effect=RuntimeError('Synthetic secondary retrieval unavailable')):
            collected.side_effect=[([market],[]),(fixture.sources,[])]
            with self.assertRaisesRegex(RuntimeError,'revenue') as stopped:
                pipeline.run('AAPL','synthetic-key',directory=directory)
            self.assertEqual(fixture.requests,[])
            before=json.loads((stopped.exception.folder/'state.json').read_text())
            self.assertTrue(before['coverage_preflight_failed'])
            folder=pipeline.run('AAPL','synthetic-key',directory=directory)
            after=json.loads((folder/'state.json').read_text())
            self.assertTrue(after['complete'])
            self.assertEqual(collected.call_count,2)
            self.assertEqual(after['sources'][0],before['sources'][0])


if __name__=='__main__':
    unittest.main()

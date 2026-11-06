from contextlib import contextmanager, redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from investmentideas import live_validation as live
from investmentideas import pipeline


SOURCE={'id':'S1','title':'Archived filing','kind':'filing','data':{'excerpt':'Reported revenue'}}
SNAPSHOT={'format':'intrinsicvalue_research_snapshot_v2','sources':[SOURCE],
          'forecast_plan':{'valuation_date':'2026-10-31','scenarios':{}}}


class LiveValidationTests(unittest.TestCase):
    @contextmanager
    def full_validation(self,payload,mode='full',complete=True):
        previous=Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                Path('sources.json').write_text(json.dumps(payload),encoding='utf-8')
                Path('draft.md').write_text('A new draft using archived evidence [S1].',encoding='utf-8')
                folder=Path('research-output/AAPL/mock')
                folder.mkdir(parents=True)
                (folder/'usage.json').write_text('[]',encoding='utf-8')
                (folder/'state.json').write_text(json.dumps({'complete':complete,'quality_status':'reviewed_draft' if complete else 'needs_revision','iteration':0}),encoding='utf-8')
                environment={'OPENROUTER_API_KEY':'synthetic-key','VALIDATION_MODE':mode,
                             'VALIDATION_SOURCE_FILE':'sources.json','VALIDATION_DRAFT_FILE':'draft.md',
                             'VALIDATION_MAX_REVISIONS':'2','VALIDATION_REVIEW_NOTES':'Check exact archived evidence',
                             'VALIDATION_REVIEW_MODEL':'synthetic/reviewer','VALIDATION_BUDGET':'8'}
                with patch.dict(os.environ,environment,clear=True), \
                        patch.object(live,'check_key',return_value={'limit':20,'limit_remaining':20}), \
                        patch.object(pipeline,'run',return_value=folder) as generate, \
                        redirect_stdout(io.StringIO()):
                    yield generate
            finally:
                os.chdir(previous)

    def test_unlimited_resetting_and_over_budget_keys_rejected(self):
        for data in [{'limit':None}, {'limit':21}, {'limit':20,'limit_reset':'daily'},
                     {'limit':float('nan')}, {'limit':0}, {'limit':True},
                     {'limit':20,'limit_remaining':0}]:
            with patch.object(live, 'request_json', return_value={'data':data}):
                with self.assertRaises(ValueError):
                    live.check_key('synthetic-key')

    def test_explicit_cumulative_validation_budget_is_bounded(self):
        self.assertEqual(live.validation_budget('12'),12)
        for value in ('nan','inf','0','20.01','-1','unlimited',None,True):
            with self.subTest(value=value),self.assertRaises(ValueError):
                live.validation_budget(value)

    def test_credentials_mode_never_generates(self):
        with patch.dict(os.environ, {'OPENROUTER_API_KEY':'synthetic-key','VALIDATION_MODE':'credentials'}), \
                patch.object(live, 'request_json', return_value={'data':{'limit':20,'limit_remaining':20}}), \
                patch.object(live, 'run') as generate:
            live.main()
            generate.assert_not_called()

    def test_missing_key_and_invalid_mode_stop_before_provider(self):
        with patch.object(live, 'request_json') as request:
            with self.assertRaises(ValueError):
                live.check_key('')
            with patch.dict(os.environ, {'VALIDATION_MODE':'automatic'}):
                with self.assertRaises(ValueError):
                    live.main()
            request.assert_not_called()

    def test_source_file_preserves_legacy_supplement_lists(self):
        for sources in ([],[SOURCE]):
            with self.subTest(sources=sources),self.full_validation(sources) as generate:
                live.main()
                kwargs=generate.call_args.kwargs
                self.assertEqual(kwargs['extra_sources'],sources)
                self.assertNotIn('source_snapshot',kwargs)
                self.assertNotIn('forecast_plan',kwargs)

    def test_typed_snapshot_routes_exact_sources_and_plan_with_new_draft(self):
        for mode,profile in (('full','balanced'),('full_economy','economy')):
            with self.subTest(mode=mode),self.full_validation(SNAPSHOT,mode) as generate:
                live.main()
                args,kwargs=generate.call_args
                self.assertEqual(args,('AAPL','synthetic-key'))
                self.assertEqual(kwargs['source_snapshot'],SNAPSHOT['sources'])
                self.assertEqual(kwargs['forecast_plan'],SNAPSHOT['forecast_plan'])
                self.assertNotIn('extra_sources',kwargs)
                self.assertEqual(kwargs['draft'],'A new draft using archived evidence [S1].')
                self.assertEqual(kwargs['profile'],profile)
                self.assertEqual(kwargs['budget'],8)
                self.assertEqual(kwargs['max_revisions'],2)
                self.assertEqual(kwargs['review_notes'],'Check exact archived evidence')
                self.assertEqual(kwargs['review_model'],'synthetic/reviewer')

    def test_snapshot_cannot_bypass_material_review_findings(self):
        payload=dict(SNAPSHOT,complete=True,quality_status='reviewed_draft')
        with self.full_validation(payload,complete=False) as generate:
            with self.assertRaisesRegex(RuntimeError,'material review findings'):
                live.main()
            generate.assert_called_once()
            self.assertNotIn('complete',generate.call_args.kwargs)
            self.assertNotIn('quality_status',generate.call_args.kwargs)

    def test_invalid_source_shapes_and_limits_stop_before_generation(self):
        invalid=[None,{},'sources',{'format':'unsupported','sources':[SOURCE],'forecast_plan':{}},
                 {'format':SNAPSHOT['format'],'sources':[SOURCE]},
                 dict(SNAPSHOT,sources=[]),dict(SNAPSHOT,sources={}),
                 dict(SNAPSHOT,sources=[dict(SOURCE,id='S2')]),
                 dict(SNAPSHOT,sources=[dict(SOURCE,id=f'S{index}') for index in range(1,82)]),
                 dict(SNAPSHOT,sources=[{'id':'S1','title':'Missing kind/data'}]),
                 dict(SNAPSHOT,forecast_plan=[]),
                 dict(SNAPSHOT,forecast_plan={'text':'x'*150000}),
                 dict(SNAPSHOT,forecast_plan={'rate':float('nan')}),
                 dict(SNAPSHOT,sources=[dict(SOURCE,data={'rate':float('inf')})]),
                 [SOURCE]*21,[{'title':'Missing kind/data'}]]
        for payload in invalid:
            with self.subTest(payload_type=type(payload).__name__),self.full_validation(payload) as generate:
                with self.assertRaises(ValueError):
                    live.main()
                generate.assert_not_called()

    def test_snapshot_preserves_source_count_and_size_limits(self):
        snapshot=copy.deepcopy(SNAPSHOT)
        snapshot['sources']=[dict(SOURCE,id=f'S{index}') for index in range(1,81)]
        self.assertEqual(live.validation_sources(snapshot)['source_snapshot'],snapshot['sources'])
        for payload in ([dict(SOURCE,data='x'*3000000)],dict(SNAPSHOT,sources=[dict(SOURCE,data='x'*3000000)])):
            with self.subTest(snapshot=isinstance(payload,dict)),self.full_validation(payload) as generate:
                with self.assertRaisesRegex(ValueError,'size limit'):
                    live.main()
                generate.assert_not_called()

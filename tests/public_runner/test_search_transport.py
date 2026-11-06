import unittest
from unittest.mock import patch
from investmentideas.core import BASE, Router


class SearchTransportTests(unittest.TestCase):
    def catalog(self):
        return {'data':[{'id':'synthetic/model','pricing':{'prompt':'0.000001','completion':'0.000002','web_search':'0.01'}}]}

    def result(self):
        return {'choices':[{'finish_reason':'stop','message':{'content':'Supported text',
                'annotations':[{'url_citation':{'url':'https://example.com/filing','title':'Filing','content':'Primary source excerpt'}}]}}],
                'usage':{'cost':.02},'model':'synthetic/model'}

    def test_unused_native_web_price_does_not_block_text_model(self):
        with patch('investmentideas.core.request_json',side_effect=[self.catalog(),self.result()]) as request:
            Router('synthetic-key',1,[]).complete('draft','synthetic/model','text')
        self.assertNotIn('plugins',request.call_args.kwargs['payload'])

    def test_exa_fee_context_and_annotations_are_recorded(self):
        ledger=[]
        with patch('investmentideas.core.request_json',side_effect=[self.catalog(),self.result()]) as request:
            Router('synthetic-key',1,ledger).complete('research','synthetic/model','text',web=True)
        self.assertEqual(request.call_args.kwargs['payload']['plugins'][0]['engine'],'exa')
        self.assertGreater(ledger[0]['reserved_usd'],.12)
        self.assertEqual(ledger[0]['annotations'][0]['content'],'Primary source excerpt')
        self.assertEqual(ledger[0]['charged_or_reserved_usd'],.02)

    def test_budget_rejects_search_before_request(self):
        with patch('investmentideas.core.request_json',return_value=self.catalog()) as request:
            router=Router('synthetic-key',.05,[])
            with self.assertRaises(ValueError):router.complete('research','synthetic/model','text',web=True)
        self.assertEqual(request.call_count,1)

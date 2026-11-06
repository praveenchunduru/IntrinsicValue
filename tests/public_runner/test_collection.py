import unittest
from unittest.mock import patch

from investmentideas import core


class CollectionTests(unittest.TestCase):
    def test_sec_unavailable_uses_labeled_secondary_financials(self):
        with patch.object(core,'request_json',return_value={'chart':{'result':[{'meta':{'symbol':'AAPL'}}]}}), \
                patch.object(core,'load_directory',side_effect=RuntimeError('blocked')), \
                patch.object(core,'yahoo_financials',return_value=('https://example.com/financials',{'series':{'revenue':100}})):
            sources,gaps=core.collect('AAPL')
        self.assertEqual([s['kind'] for s in sources],['market_snapshot','financial_timeseries'])
        self.assertIn('secondary source',sources[1]['title'])
        self.assertTrue(any('SEC facts unavailable' in g for g in gaps))

    def test_live_primary_financials_take_precedence(self):
        facts={'facts':{'us-gaap':{'Revenues':{'units':{'USD':[{'val':100,'end':'2025-12-31','filed':'2026-02-01'}]}}}}}
        with patch.object(core,'request_json',side_effect=[{'chart':{'result':[{'meta':{'symbol':'AAPL'}}]}},facts]), \
                patch.object(core,'load_directory',return_value=({'0':{'ticker':'AAPL','title':'APPLE INC','cik_str':320193}},False)), \
                patch.object(core,'yahoo_financials') as fallback:
            sources,gaps=core.collect('AAPL')
        self.assertEqual(sources[1]['kind'],'sec_facts')
        self.assertEqual(gaps,[])
        fallback.assert_not_called()

    def test_all_statement_services_fail_without_fabricating_financials(self):
        with patch.object(core,'request_json',side_effect=RuntimeError('blocked')), \
                patch.object(core,'load_directory',side_effect=RuntimeError('blocked')), \
                patch.object(core,'yahoo_financials',side_effect=RuntimeError('blocked')):
            sources,gaps=core.collect('AAPL')
        self.assertEqual(sources,[])
        self.assertEqual(len(gaps),3)

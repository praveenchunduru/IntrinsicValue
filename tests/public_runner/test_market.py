import unittest
from investmentideas.market import market_snapshot


class QuoteTests(unittest.TestCase):
    def test_observation_date_and_regular_session_are_preserved(self):
        raw={'symbol':'AAPL','regularMarketTime':1793912401,'gmtoffset':-18000,
             'regularMarketPrice':344.17,'regularMarketChangePercent':-1.528,
             'fulldayPrice':342.55,'fulldayChange':-6.96,'fulldayChangePercent':-1.991,
             'chartPreviousClose':332.89}
        normalized=market_snapshot(raw)
        self.assertEqual(normalized['quote_observed_at_utc'],'2026-11-05T21:00:01+00:00')
        self.assertEqual(normalized['quote_observed_at_exchange_offset'],'2026-11-05T16:00:01-05:00')
        self.assertEqual(normalized['regularMarketPrice'],344.17)
        self.assertEqual(normalized['regularMarketChangePercent'],-1.528)
        for misleading in ['fulldayPrice','fulldayChange','fulldayChangePercent','chartPreviousClose']:
            self.assertNotIn(misleading,normalized)
        self.assertEqual(raw['fulldayChange'],-6.96)

    def test_unavailable_or_invalid_timestamp_is_explicit(self):
        for stamp in [None, True, float('inf'), 10**100]:
            self.assertIsNone(market_snapshot({'regularMarketTime':stamp})['quote_observed_at_utc'])

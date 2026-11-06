import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

from investmentideas import fundamentals as financials


def row(year=2025, value=100):
    return {'asOfDate':f'{year}-09-30', 'periodType':'12M', 'currencyCode':'USD',
            'reportedValue':{'raw':value, 'fmt':'irrelevant display'}}


def record(metric, rows, ticker='AAPL'):
    return {'meta':{'symbol':[ticker], 'type':[metric]}, metric:rows}


def payload(records):
    return {'timeseries': {'result':records, 'error':None}}


class YahooFinancialsTests(unittest.TestCase):
    def test_valid_secondary_data_is_bounded_and_preserves_units_dates_and_sign(self):
        records = [record('annualTotalRevenue', [row(y) for y in range(2018, 2026)]),
                   record('annualCapitalExpenditure', [row(2025, -10)]),
                   record('annualDilutedAverageShares', [row(2025, 15)])]
        fetch = Mock(return_value=payload(records))
        url, data = financials.yahoo_financials('aapl', fetch)
        fetch.assert_called_once_with(url)
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query['type'][0].split(','), list(financials.METRICS))
        self.assertTrue(url.startswith('https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/AAPL?'))
        self.assertLess(int(query['period1'][0]), int(query['period2'][0]))
        self.assertEqual([r['asOfDate'] for r in data['series']['annualTotalRevenue']],
                         ['2022-09-30','2023-09-30','2024-09-30','2025-09-30'])
        capex = data['series']['annualCapitalExpenditure'][0]
        self.assertEqual(capex, {'asOfDate':'2025-09-30','periodType':'12M', 'currencyCode':'USD',
                                 'reportedValue':{'raw':-10}})
        self.assertEqual(data['units']['annualDilutedAverageShares'], 'shares')
        self.assertTrue(any('Secondary' in note for note in data['notes']))

    def test_fcff_actuals_keep_balance_flow_and_share_units_distinct(self):
        required = {
            'annualOperatingIncome', 'annualCashAndCashEquivalents',
            'annualCashCashEquivalentsAndShortTermInvestments', 'annualOrdinarySharesNumber',
            'annualTotalDebt', 'annualTaxProvision', 'annualPretaxIncome',
            'annualStockBasedCompensation', 'annualCapitalExpenditure',
            'annualDepreciationAmortizationDepletion', 'annualChangeInWorkingCapital',
        }
        self.assertTrue(required.issubset(set(financials.METRICS)))
        records = [record(metric, [row(year, -10 if metric in {
            'annualCapitalExpenditure', 'annualChangeInWorkingCapital'} else 100)
            for year in range(2019, 2026)]) for metric in financials.METRICS]
        _, data = financials.yahoo_financials('AAPL', lambda url: payload(records))
        self.assertEqual(set(data['series']), set(financials.METRICS))
        for metric in financials.METRICS:
            self.assertEqual(len(data['series'][metric]), 4)
            self.assertEqual(data['series'][metric][-1]['asOfDate'], '2025-09-30')
            self.assertEqual(data['series'][metric][-1]['currencyCode'], 'USD')
        self.assertEqual(data['units']['annualOrdinarySharesNumber'], 'shares')
        self.assertEqual(data['units']['annualDilutedAverageShares'], 'shares')
        self.assertIn('period-end', data['series_metadata']['annualOrdinarySharesNumber']['measurement'])
        self.assertIn('annual weighted average', data['series_metadata']['annualDilutedAverageShares']['measurement'])
        self.assertIn('period-end', data['series_metadata']['annualCashAndCashEquivalents']['measurement'])
        self.assertIn('annual flow', data['series_metadata']['annualOperatingIncome']['measurement'])
        self.assertEqual(data['series']['annualChangeInWorkingCapital'][-1]['reportedValue']['raw'], -10)
        notes = ' '.join(data['notes'])
        self.assertIn('never add the two series', notes)
        self.assertIn('not shares verified as current', notes)
        self.assertIn('missing evidence, never zero', notes)

    def test_absent_optional_actuals_are_not_defaulted_to_zero(self):
        records = [record('annualTotalRevenue',[row()]), record('annualNetIncome',[row(value=10)]),
                   record('annualTotalDebt',[row(value=None)]),
                   record('annualCashAndCashEquivalents',[])]
        _, data = financials.yahoo_financials('AAPL', lambda url: payload(records))
        self.assertEqual(set(data['series']), {'annualTotalRevenue','annualNetIncome'})
        self.assertNotIn('annualTotalDebt',data['units'])
        self.assertNotIn('annualCashAndCashEquivalents',data['series_metadata'])

    def test_invalid_rows_are_filtered(self):
        invalid = [None, {}, row(value=None), row(value=True), row(value='100'),
                   row(value=float('inf')), row(value=float('nan')), row(value=10**400), row(year=2099)]
        for field, value in [('currencyCode',None), ('currencyCode','bad-code'),
                             ('periodType','3M'), ('asOfDate','2025-99-99')]:
            bad = row(); bad[field] = value; invalid.append(bad)
        records = [record('annualTotalRevenue',invalid+[row()]), record('annualNetIncome',[row(value=12)])]
        _, data = financials.yahoo_financials('AAPL', lambda url: payload(records))
        self.assertEqual(len(data['series']['annualTotalRevenue']),1)
        self.assertEqual(data['series']['annualTotalRevenue'][0]['reportedValue']['raw'],100)

    def test_metadata_only_or_incomplete_financials_are_refused(self):
        bad_payloads = [None, {}, {'timeseries':{'error':{'description':'untrusted body'}}},
                        payload([]), payload([{'meta':{'symbol':['AAPL'],'type':['annualTotalRevenue']}}]),
                        payload([record('annualTotalRevenue',[row()])]),
                        payload([record('annualNetIncome',[row()]), record('annualTotalDebt',[row()])])]
        for response in bad_payloads:
            with self.subTest(response=response):
                with self.assertRaises(RuntimeError):
                    financials.yahoo_financials('AAPL', lambda url: response)

    def test_wrong_or_missing_issuer_cannot_supply_financial_evidence(self):
        for ticker in ['MSFT', None, '']:
            records = [record('annualTotalRevenue',[row()], ticker), record('annualNetIncome',[row()], ticker)]
            if ticker is None:
                for r in records:
                    r['meta'].pop('symbol')
            with self.subTest(ticker=ticker):
                with self.assertRaises(RuntimeError):
                    financials.yahoo_financials('AAPL',lambda url: payload(records))

    def test_share_class_symbols_use_yahoo_convention(self):
        records = [record('annualTotalRevenue',[row()], 'BRK-B'), record('annualNetIncome',[row()], 'BRK-B')]
        url, data = financials.yahoo_financials('BRK.B', lambda url: payload(records))
        self.assertIn('/BRK-B?', url)
        self.assertEqual(data['symbol'],'BRK.B')

    def test_unrequested_metrics_and_duplicate_periods_do_not_expand_evidence(self):
        records = [record('annualTotalRevenue',[row(),row()]), record('annualNetIncome',[row()]),
                   record('annualUnknown',[row()])]
        _, data = financials.yahoo_financials('AAPL', lambda url: payload(records))
        self.assertNotIn('annualUnknown',data['series'])
        self.assertEqual(len(data['series']['annualTotalRevenue']),1)


if __name__ == '__main__':
    unittest.main()

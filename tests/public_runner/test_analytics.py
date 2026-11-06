import unittest

from investmentideas.analytics import annual_metrics


def row(day, value, currency='USD', period='12M'):
    return {'asOfDate':day,'currencyCode':currency,'periodType':period,'reportedValue':{'raw':value}}


class AnnualMetricsTests(unittest.TestCase):
    def test_comparable_annual_metrics_include_false_cash_conversion_claim(self):
        series = {'annualTotalRevenue':[row('2024-09-30',391035),row('2025-09-30',416161)],
                  'annualNetIncome':[row('2025-09-30',112010)],
                  'annualOperatingCashFlow':[row('2025-09-30',111482)]}
        result = annual_metrics({'series':series})
        current = result[-1]
        self.assertEqual(current['asOfDate'],'2025-09-30')
        self.assertEqual(current['currencyCode'],'USD')
        self.assertEqual(current['cash_flow_minus_net_income'],-528)
        self.assertFalse(current['ocf_exceeds_net_income'])
        self.assertAlmostEqual(current['net_margin_percent'],26.91506412181824)
        self.assertAlmostEqual(current['revenue_yoy_percent'],6.425511782832749)
        self.assertIn('provider-normalized dates',current['basis'])
        self.assertIn('not confirmed fiscal-year ends',current['basis'])

    def test_skipped_years_or_changed_period_ends_are_not_yoy(self):
        for dates in [('2023-09-30','2025-09-30'),('2024-06-30','2025-09-30')]:
            result = annual_metrics({'series':{'annualTotalRevenue':[row(dates[0],100),row(dates[1],110)]}})
            self.assertNotIn('revenue_yoy_percent',result[-1])

    def test_currencies_remain_separate_and_quarters_are_not_summed(self):
        series={'annualTotalRevenue':[row('2024-09-30',100,'USD'),row('2025-09-30',120,'EUR'),
                                      row('2025-09-30',999,'USD','3M')],
                'annualNetIncome':[row('2025-09-30',20,'USD')],
                'annualOperatingCashFlow':[row('2025-09-30',30,'EUR')]}
        result=annual_metrics({'series':series})
        current=[r for r in result if r['asOfDate']=='2025-09-30']
        self.assertEqual(len(current),2)
        for observation in current:
            self.assertNotIn('revenue_yoy_percent',observation)
            self.assertNotIn('net_margin_percent',observation)
            self.assertNotIn('cash_flow_minus_net_income',observation)

    def test_zero_denominators_invalid_rows_and_missing_data_do_not_invent_metrics(self):
        self.assertEqual(annual_metrics(None),[])
        self.assertEqual(annual_metrics({'series':{}}),[])
        result=annual_metrics({'series':{
            'annualTotalRevenue':[row('2024-09-30',0),row('2025-09-30',0),None,
                                  row('2025-99-99',20),row('2025-09-30',True)],
            'annualNetIncome':[row('2025-09-30',10)],
            'annualOperatingCashFlow':[row('2025-09-30',float('nan'))]}})
        self.assertNotIn('net_margin_percent',result[-1])
        self.assertNotIn('revenue_yoy_percent',result[-1])
        self.assertNotIn('ocf_exceeds_net_income',result[-1])

    def test_positive_equal_cash_flow_and_leap_year_anniversaries(self):
        result=annual_metrics({'series':{
            'annualTotalRevenue':[row('2024-02-29',100),row('2025-02-28',110)],
            'annualNetIncome':[row('2024-02-29',10),row('2025-02-28',20)],
            'annualOperatingCashFlow':[row('2024-02-29',12),row('2025-02-28',20)]}})
        self.assertTrue(result[0]['ocf_exceeds_net_income'])
        self.assertFalse(result[-1]['ocf_exceeds_net_income'])
        self.assertEqual(result[-1]['cash_flow_minus_net_income'],0)
        self.assertAlmostEqual(result[-1]['revenue_yoy_percent'],10)


if __name__ == '__main__':
    unittest.main()

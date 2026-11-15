"""Reported-history and breakdown tables built from SEC records (offline, synthetic payloads)."""
import unittest

from investmentideas.evidence_tables import breakdown_tables, historical_baseline, render_baseline

YEARS = [('2022-09-25', '2023-09-30'), ('2023-10-01', '2024-09-28'), ('2024-09-29', '2025-09-27')]


def fact(start, end, val, filed, form='10-K'):
    row = {'end': end, 'val': val, 'filed': filed, 'form': form}
    if start:
        row['start'] = start
    return row


def payload(**concepts):
    """concepts: name -> (unit, [rows])"""
    return {'facts': {'us-gaap': {name: {'units': {unit: rows}} for name, (unit, rows) in concepts.items()}}}


def annual(values, filed='2025-11-01'):
    return [fact(start, end, value, filed) for (start, end), value in zip(YEARS, values) if value is not None]


def instants(values, filed='2025-11-01'):
    return [fact(None, end, value, filed) for (_, end), value in zip(YEARS, values) if value is not None]


BASE = dict(
    RevenueFromContractWithCustomerExcludingAssessedTax=('USD', annual([100e9, 110e9, 121e9])),
    OperatingIncomeLoss=('USD', annual([30e9, 33e9, 39.6e9])),
    EarningsPerShareDiluted=('USD/shares', annual([5.0, 5.5, 6.6])),
    CommonStockSharesOutstanding=('shares', instants([1_000e6, 970e6, 940e6])),
    StockRepurchasedAndRetiredDuringPeriodValue=('USD', annual([20e9, 22e9, 24e9])),
    StockRepurchasedAndRetiredDuringPeriodShares=('shares', annual([40e6, 40e6, 40e6])),
    PaymentsForRepurchaseOfCommonStock=('USD', annual([21e9, 23e9, 25e9])),
)


class HistoricalBaselineTests(unittest.TestCase):
    def rows(self, **override):
        concepts = dict(BASE, **override)
        return historical_baseline(payload(**concepts), years=2)['rows']

    def test_growth_and_share_change_are_supplied(self):
        latest = self.rows()[-1]
        self.assertAlmostEqual(latest['revenue_growth'], 0.10)
        self.assertAlmostEqual(latest['operating_income_growth'], 0.20)
        self.assertAlmostEqual(latest['diluted_eps_growth'], 0.20)
        self.assertEqual(latest['shares_outstanding'], 940e6)
        self.assertEqual(latest['net_share_change'], -30e6)
        self.assertEqual(latest['shares_repurchased'], 40e6)

    def test_average_buyback_price_pairs_value_and_shares_from_one_disclosure(self):
        latest = self.rows()[-1]
        # 24e9 / 40e6, never the cash-flow payment (25e9) over program shares.
        self.assertAlmostEqual(latest['average_repurchase_price'], 600.0)
        self.assertEqual(latest['repurchases'], 25e9)

    def test_growth_from_a_negative_base_is_blank(self):
        latest = self.rows(OperatingIncomeLoss=('USD', annual([30e9, -5e9, 10e9])))[-1]
        self.assertIsNone(latest['operating_income_growth'])

    def test_counts_reported_only_before_a_split_are_blank_with_their_changes(self):
        # A 10-for-1 split: the 2025 filing restates 2024's count; 2023's count was
        # only ever reported on the old basis.
        shares = [fact(None, YEARS[0][1], 100e6, '2023-11-01'), fact(None, YEARS[1][1], 97e6, '2024-11-01'),
                  fact(None, YEARS[1][1], 970e6, '2025-11-01'), fact(None, YEARS[2][1], 940e6, '2025-11-01')]
        eps = [fact(*YEARS[0], 50.0, '2023-11-01'), fact(*YEARS[1], 55.0, '2024-11-01'),
               fact(*YEARS[1], 5.5, '2025-11-01'), fact(*YEARS[2], 6.6, '2025-11-01')]
        first, latest = self.rows(CommonStockSharesOutstanding=('shares', shares),
                                  EarningsPerShareDiluted=('USD/shares', eps))
        self.assertEqual(first['shares_outstanding'], 970e6)
        self.assertIsNone(first['net_share_change'])
        self.assertIsNone(first['diluted_eps_growth'])
        self.assertEqual(latest['net_share_change'], -30e6)
        self.assertAlmostEqual(latest['diluted_eps_growth'], 0.20)

    def test_the_rendered_table_carries_the_new_columns(self):
        text = render_baseline({'rows': self.rows(), 'concepts': {}}, 'S2')
        self.assertIn('change in shares outstanding (millions)', text)
        self.assertIn('| 940.0 | -30.0 | 40.0', text)
        self.assertIn('| 20.0% |', text)


class BreakdownTableTests(unittest.TestCase):
    def test_one_revenue_concept_decides_and_members_are_not_summed(self):
        def item(member, concept, end, value):
            start = {'2025-09-27': '2024-09-29', '2024-09-28': '2023-10-01'}[end]
            return {'axis': 'segment', 'member': member, 'concept': 'us-gaap:' + concept, 'start_date': start,
                    'end_date': end, 'normalized_decimal': str(value)}
        facts = [item('aapl:AmericasSegmentMember', 'RevenueFromContractWithCustomerExcludingAssessedTax', '2025-09-27', 170),
                 item('aapl:AmericasSegmentMember', 'Revenues', '2025-09-27', 171),
                 item('aapl:AmericasSegmentMember', 'RevenueFromContractWithCustomerExcludingAssessedTax', '2024-09-28', 160),
                 item('aapl:AmericasSegmentMember', 'OperatingIncomeLoss', '2025-09-27', 68)]
        table = breakdown_tables(facts)['segment']
        member = table['members'][0]
        self.assertEqual(member['label'], 'Americas Segment')
        self.assertEqual(member['revenue'], [170, 160])
        self.assertAlmostEqual(member['revenue_growth'][0], 170 / 160 - 1)
        self.assertAlmostEqual(member['operating_margin'][0], 0.4)
        self.assertIn('not summed', table['note'])


if __name__ == '__main__':
    unittest.main()

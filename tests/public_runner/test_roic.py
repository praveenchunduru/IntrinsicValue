"""Return on invested capital by Mauboussin and Callahan's method (Counterpoint Global, October 2022).

The Microsoft rows are its reported fiscal 2014-2022 values in millions, as company
facts held them at the end of 2022. The paper's Microsoft example (p5, p7, p19-20):
NOPAT $70 billion; invested capital $96, $120 and $165 billion for 2020-2022; ROIC
49%, 94% without goodwill and acquired intangibles, 34% with intangibles
capitalized on NOPAT of $80 billion and invested capital of $260 billion.
"""
import unittest

from investmentideas import roic

MICROSOFT = [
    {'fiscal_year_end': '2014-06-30', 'revenue': 86833, 'operating_income': 27759, 'income_tax_expense': 5746, 'pretax_income': 27820, 'net_income': 22074, 'research_and_development': 11381, 'amortization_acquired_intangibles': 845, 'deferred_tax_expense': -331, 'selling_and_marketing': 15811, 'general_and_administrative': 4677, 'interest_expense': 597, 'assets': 172384, 'current_liabilities': 45625, 'long_term_debt_current': 0, 'commercial_paper': 2000, 'short_term_borrowings': 2000, 'cash': 8669, 'cash_and_short_term_investments': 85709, 'securities_current': 77040, 'long_term_investments': 14597, 'goodwill': 20127, 'intangibles_finite': 6981},
    {'fiscal_year_end': '2015-06-30', 'revenue': 93580, 'operating_income': 18161, 'income_tax_expense': 6314, 'pretax_income': 18507, 'net_income': 12193, 'research_and_development': 12046, 'amortization_acquired_intangibles': 1300, 'deferred_tax_expense': 224, 'selling_and_marketing': 15713, 'general_and_administrative': 4611, 'interest_expense': 781, 'assets': 174472, 'current_liabilities': 49647, 'long_term_debt_current': 2499, 'commercial_paper': 5000, 'short_term_borrowings': 4985, 'cash': 5595, 'cash_and_short_term_investments': 96526, 'securities_current': 90931, 'long_term_investments': 12053, 'goodwill': 16939, 'intangibles_finite': 4835},
    {'fiscal_year_end': '2016-06-30', 'revenue': 91154, 'operating_income': 26078, 'income_tax_expense': 5100, 'pretax_income': 25639, 'net_income': 20539, 'research_and_development': 11988, 'amortization_acquired_intangibles': 978, 'deferred_tax_expense': 2479, 'selling_and_marketing': 14635, 'general_and_administrative': 4563, 'interest_expense': 1243, 'assets': 193468, 'current_liabilities': 59357, 'long_term_debt_current': 0, 'commercial_paper': 12900, 'short_term_borrowings': 12904, 'cash': 6510, 'cash_and_short_term_investments': 113240, 'securities_current': 106730, 'long_term_investments': 10431, 'goodwill': 17872, 'intangibles_finite': 3733, 'operating_lease_liability': 0},
    {'fiscal_year_end': '2017-06-30', 'revenue': 96571, 'operating_income': 29025, 'income_tax_expense': 4412, 'pretax_income': 29901, 'net_income': 25489, 'research_and_development': 13037, 'amortization_acquired_intangibles': 1700, 'deferred_tax_expense': -829, 'selling_and_marketing': 15461, 'general_and_administrative': 4481, 'interest_expense': 2222, 'assets': 250312, 'current_liabilities': 55745, 'long_term_debt_current': 1049, 'commercial_paper': 9100, 'short_term_borrowings': 9072, 'cash': 7663, 'cash_and_short_term_investments': 132981, 'securities_current': 125318, 'long_term_investments': 6023, 'goodwill': 35122, 'intangibles_finite': 10106, 'operating_lease_liability': 6795, 'operating_lease_rate': 0.025},
    {'fiscal_year_end': '2018-06-30', 'revenue': 110360, 'operating_income': 35058, 'income_tax_expense': 19903, 'pretax_income': 36474, 'net_income': 16571, 'research_and_development': 14726, 'amortization_acquired_intangibles': 2200, 'deferred_tax_expense': -5143, 'selling_and_marketing': 17469, 'general_and_administrative': 4754, 'interest_expense': 2733, 'assets': 258848, 'current_liabilities': 58488, 'long_term_debt_current': 3998, 'commercial_paper': 0, 'short_term_borrowings': 0, 'cash': 11946, 'cash_and_short_term_investments': 133768, 'securities_current': 121822, 'long_term_investments': 1862, 'goodwill': 35683, 'intangibles_finite': 8053, 'operating_lease_liability': 6967, 'operating_lease_rate': 0.027},
    {'fiscal_year_end': '2019-06-30', 'revenue': 125843, 'operating_income': 42959, 'income_tax_expense': 4448, 'pretax_income': 43688, 'net_income': 39240, 'research_and_development': 16876, 'amortization_acquired_intangibles': 1900, 'deferred_tax_expense': -6463, 'selling_and_marketing': 18213, 'general_and_administrative': 4885, 'interest_expense': 2686, 'assets': 286556, 'current_liabilities': 69420, 'long_term_debt_current': 5516, 'commercial_paper': 0, 'cash': 11356, 'cash_and_short_term_investments': 133819, 'securities_current': 122463, 'long_term_investments': 2649, 'goodwill': 42026, 'intangibles_finite': 7750, 'operating_lease_liability': 7703, 'operating_lease_rate': 0.03},
    {'fiscal_year_end': '2020-06-30', 'revenue': 143015, 'operating_income': 52959, 'income_tax_expense': 8755, 'pretax_income': 53036, 'net_income': 44281, 'research_and_development': 19269, 'amortization_acquired_intangibles': 1600, 'deferred_tax_expense': 11, 'selling_and_marketing': 19598, 'general_and_administrative': 5111, 'interest_expense': 2591, 'assets': 301311, 'current_liabilities': 72310, 'long_term_debt_current': 3749, 'cash': 13576, 'cash_and_short_term_investments': 136527, 'securities_current': 122951, 'long_term_investments': 2965, 'goodwill': 43351, 'intangibles_finite': 7038, 'operating_lease_liability': 9287, 'operating_lease_rate': 0.027},
    {'fiscal_year_end': '2021-06-30', 'revenue': 168088, 'operating_income': 69916, 'income_tax_expense': 9831, 'pretax_income': 71102, 'net_income': 61271, 'research_and_development': 20716, 'amortization_acquired_intangibles': 1600, 'deferred_tax_expense': -150, 'selling_and_marketing': 20117, 'general_and_administrative': 5107, 'interest_expense': 2346, 'assets': 333779, 'current_liabilities': 88657, 'long_term_debt_current': 8072, 'cash': 14224, 'cash_and_short_term_investments': 130334, 'securities_current': 116110, 'long_term_investments': 5984, 'goodwill': 49711, 'intangibles_finite': 7800, 'operating_lease_liability': 11591, 'operating_lease_rate': 0.022},
    {'fiscal_year_end': '2022-06-30', 'revenue': 198270, 'operating_income': 83383, 'income_tax_expense': 10978, 'pretax_income': 83716, 'net_income': 72738, 'research_and_development': 24512, 'amortization_acquired_intangibles': 2000, 'deferred_tax_expense': -5702, 'selling_and_marketing': 21825, 'general_and_administrative': 5900, 'interest_expense': 2063, 'assets': 364840, 'current_liabilities': 95082, 'long_term_debt_current': 2749, 'cash': 13931, 'cash_and_short_term_investments': 104757, 'securities_current': 90826, 'long_term_investments': 6891, 'goodwill': 67524, 'intangibles_finite': 11298, 'operating_lease_liability': 13717, 'operating_lease_rate': 0.021},
]


def year(end, **values):
    base = {'fiscal_year_end': end, 'revenue': 1000, 'operating_income': 200, 'income_tax_expense': 40,
            'pretax_income': 200, 'assets': 1500, 'current_liabilities': 300, 'cash': 100}
    return dict(base, **values)


class MicrosoftExampleTests(unittest.TestCase):
    def setUp(self):
        self.rows, self.status = roic.history(MICROSOFT)
        self.by_year = {row['fiscal_year_end'][:4]: row for row in self.rows}

    def test_invested_capital_matches_the_paper(self):
        for fiscal, expected in (('2020', 96e3), ('2021', 120e3), ('2022', 165e3)):
            self.assertAlmostEqual(self.by_year[fiscal]['invested_capital'], expected, delta=600)
        self.assertAlmostEqual(self.by_year['2022']['average_invested_capital'], 143e3, delta=600)

    def test_returns_match_the_paper(self):
        latest = self.by_year['2022']
        # The paper's NOPAT is $70 billion; its lease-interest estimate is about $1
        # billion against $0.3 billion here (liability times discount rate).
        self.assertAlmostEqual(latest['nopat'], 70e3, delta=2000)
        self.assertAlmostEqual(latest['return_on_invested_capital'], .49, delta=.015)
        self.assertAlmostEqual(latest['return_excluding_acquired'], .94, delta=.025)
        self.assertEqual(self.status, 'computed')
        self.assertAlmostEqual(latest['adjusted_return'], .34, delta=.01)
        self.assertAlmostEqual(latest['adjusted_nopat'], 80e3, delta=2000)
        self.assertAlmostEqual(latest['adjusted_invested_capital'], 260e3, delta=2000)
        self.assertAlmostEqual(latest['intangible_investment'], 41e3, delta=500)
        self.assertAlmostEqual(latest['intangible_amortization'], 31e3, delta=1000)

    def test_cash_taxes_are_the_provision_less_deferred_tax_expense(self):
        latest = self.by_year['2022']
        self.assertEqual(latest['nopat_tax_basis'], 'cash')
        # Provision $10,978 million less deferred tax expense of -$5,702 million.
        self.assertAlmostEqual(latest['nopat_tax_rate'], (10978 + 5702) / 83716, delta=.002)


class MethodTests(unittest.TestCase):
    def test_commercial_paper_inside_short_term_borrowings_is_counted_once(self):
        row = year('2024-12-31', commercial_paper=50, short_term_borrowings=50)
        # 1500 - (300 - 50) - (100 - 20) = 1170
        self.assertEqual(roic.invested_capital(row), 1170)
        self.assertEqual(roic.invested_capital(dict(row, debt_current=80)), 1200)

    def test_operating_cash_is_two_percent_of_revenue_and_investments_are_excluded(self):
        self.assertEqual(roic.invested_capital(year('2024-12-31', cash=10)), 1200)
        self.assertEqual(roic.invested_capital(year('2024-12-31', securities_current=200, securities_noncurrent=150,
                                                    equity_method_investments=50)), 1500 - 300 - 280 - 200)
        # The balance-sheet investments line replaces its parts.
        self.assertEqual(roic.invested_capital(year('2024-12-31', long_term_investments=70, equity_method_investments=50)),
                         1500 - 300 - 80 - 70)

    def test_securities_without_a_current_line_come_from_the_debt_total_and_marketable_equity(self):
        # Nvidia fiscal 2026: no current securities line; debt securities plus marketable equity.
        row = year('2024-12-31', securities_unsplit=60, equity_securities_marketable=40)
        self.assertEqual(roic.invested_capital(row), 1500 - 300 - (200 - 20))
        # A current line wins, and already holds the marketable equity (Meta).
        self.assertEqual(roic.invested_capital(dict(row, securities_current=100)), 1500 - 300 - (200 - 20))
        self.assertEqual(roic.invested_capital(dict(row, securities_current=50)), 1500 - 300 - (150 - 20))

    def test_marketable_securities_beside_the_cash_total_are_excluded(self):
        # Coca-Cola: cash and short-term investments, plus marketable securities on their own line.
        row = year('2024-12-31', cash_and_short_term_investments=150, marketable_securities_other=30)
        self.assertEqual(roic.invested_capital(row), 1500 - 300 - (180 - 20))

    def test_overlapping_investment_lines_count_once_and_pension_surpluses_are_excluded(self):
        row = year('2024-12-31', other_long_term_investments=70, equity_method_investments=20,
                   securities_noncurrent=10, pension_assets=15)
        self.assertEqual(roic.invested_capital(row), 1500 - 300 - 80 - 10 - 70 - 15)

    def test_lease_inclusive_current_debt_and_other_borrowings_are_debt(self):
        self.assertEqual(roic.invested_capital(year('2024-12-31', long_term_debt_current=40, commercial_paper=20,
                                                    other_short_term_borrowings=5)), 1500 - (300 - 65) - 80)

    def test_indefinite_intangibles_tagged_by_kind_are_acquired_intangibles(self):
        row = year('2024-12-31', goodwill=100, intangibles_finite=5, intangibles_trademarks=60, intangibles_franchise=7)
        self.assertEqual(roic.acquired_intangibles(row), 172)
        self.assertEqual(roic.acquired_intangibles(dict(row, intangibles_indefinite=50)), 155)

    def test_roic_uses_average_capital_and_flags_capital_at_or_below_zero(self):
        rows, _ = roic.history([year('2023-12-31'), year('2024-12-31', assets=1700)])
        latest = rows[-1]
        self.assertEqual(latest['average_invested_capital'], (1120 + 1320) / 2)
        self.assertAlmostEqual(latest['return_on_invested_capital'], 160 / 1220)
        rows, _ = roic.history([year('2023-12-31', assets=200), year('2024-12-31', assets=300)])
        self.assertIsNone(rows[-1]['return_on_invested_capital'])
        self.assertTrue(rows[-1]['return_on_capital_unbounded'])

    def test_three_year_incremental_return_lags_capital_one_year(self):
        assets = (1500, 1600, 1700, 1800, 1900)
        income = (200, 210, 220, 230, 260)
        rows, _ = roic.history([year(f'202{i}-12-31', assets=a, operating_income=o, pretax_income=o, income_tax_expense=o / 5)
                                for i, (a, o) in enumerate(zip(assets, income))])
        # NOPAT 208 - 168 over capital 1420 - 1120 (years 3 and 0).
        self.assertAlmostEqual(rows[-1]['incremental_return'], (208 - 168) / 300)
        self.assertIsNone(rows[-2]['incremental_return'])

    def test_a_gap_in_the_years_breaks_the_lags(self):
        rows, _ = roic.history([year('2022-12-31'), year('2024-12-31')])
        self.assertIsNone(rows[-1]['average_invested_capital'])

    def test_without_operating_income_ebit_is_pretax_income_plus_net_interest(self):
        row = year('2024-12-31', operating_income=None, pretax_income=180, interest_expense=30, interest_income=10)
        rows, _ = roic.history([row])
        self.assertEqual(rows[0]['ebita'], 200)
        self.assertEqual(rows[0]['ebit_basis'], 'pretax income plus net interest expense')

    def test_cash_tax_rate_falls_back_to_effective_then_statutory(self):
        self.assertEqual(roic.cash_tax_rate(year('x', deferred_tax_expense=10)), (.15, 'cash'))
        self.assertEqual(roic.cash_tax_rate(year('x')), (.2, 'effective'))
        self.assertEqual(roic.cash_tax_rate(year('x', income_tax_expense=150)), (.25, 'statutory'))

    def test_lease_interest_is_the_average_liability_times_its_rate(self):
        rows, _ = roic.history([year('2023-12-31', operating_lease_liability=100),
                                year('2024-12-31', operating_lease_liability=140, operating_lease_rate=.05)])
        self.assertAlmostEqual(rows[-1]['operating_lease_interest'], 6)
        self.assertAlmostEqual(rows[-1]['ebita'], 206)

    def test_one_line_sga_uses_the_peters_and_taylor_shares_and_split_lines_the_company_method(self):
        # Changed 2026-11-26: one-line SG&A was left without an adjusted ROIC.
        rows = [year(f'20{10 + i}-12-31', research_and_development=100, selling_general_administrative=200)
                for i in range(9)]
        measures, status = roic.history(rows)
        self.assertEqual(status, 'computed')
        latest = measures[-1]
        self.assertEqual(latest['intangibles_basis'], 'one_line')
        # R&D 100 x (6+5+...+1)/6 plus 30% of SG&A, 60 x (2+1)/2.
        self.assertAlmostEqual(latest['capitalized_intangibles'], 350 + 90)
        rows = [dict(row, selling_and_marketing=150, general_and_administrative=50) for row in rows]
        measures, status = roic.history(rows)
        self.assertEqual(status, 'computed')
        # Flat spending: investment equals amortization, so NOPAT is unchanged; the
        # balance is R&D 100 x (6+5+...+1)/6 plus S&M 105 x 1.5 plus G&A 10 x 1.5.
        latest = measures[-1]
        self.assertEqual(latest['intangibles_basis'], 'split')
        self.assertAlmostEqual(latest['adjusted_nopat'], latest['nopat'])
        self.assertAlmostEqual(latest['capitalized_intangibles'], 350 + 157.5 + 15)

    def test_an_unreported_rnd_line_is_not_taken_as_zero(self):
        rows = [year(f'20{10 + i}-12-31', selling_and_marketing=150, general_and_administrative=50) for i in range(9)]
        measures, status = roic.history(rows)
        self.assertEqual(status, 'no_rnd')
        self.assertIsNone(measures[-1].get('adjusted_return'))

    def test_short_history_leaves_the_intangible_adjustment_blank(self):
        rows = [year(f'20{20 + i}-12-31', research_and_development=100, selling_and_marketing=150,
                     general_and_administrative=50) for i in range(5)]
        measures, status = roic.history(rows)
        self.assertIsNone(measures[-1].get('adjusted_return'))
        self.assertEqual(status, 'short_history')


class ExhibitTests(unittest.TestCase):
    def test_the_memo_prints_the_roic_table_beside_the_history(self):
        from investmentideas import pipeline
        rows = [{'fiscal_year_end': '2024-12-31', 'currency': 'USD', 'revenue': 100e9, 'nopat': 20e9, 'nopat_margin': .2,
                 'invested_capital': 80e9, 'average_invested_capital': 75e9, 'capital_turnover': 1.33,
                 'return_on_invested_capital': .267, 'return_on_capital_unbounded': False,
                 'return_excluding_acquired': .5, 'incremental_return': None, 'incremental_capital_change': -1e9,
                 'nopat_tax_rate': .2, 'nopat_tax_basis': 'cash', 'ebit_basis': 'pretax income plus net interest expense',
                 'intangibles_capitalization': 'no_split'}]
        for key in ('revenue_growth', 'gross_margin', 'operating_margin', 'research_and_development_to_revenue',
                    'selling_general_administrative_to_revenue', 'effective_tax_rate', 'capital_expenditure_to_revenue',
                    'depreciation_amortization_to_revenue', 'stock_compensation_to_revenue', 'repurchases', 'dividends'):
            rows[0][key] = None
        section = '\n'.join(pipeline.reported_tables_section([{'id': 'S2', 'historical_baseline': {'rows': rows}}]))
        self.assertIn('Mauboussin and Callahan method', section)
        self.assertIn('| ROIC | 26.7% |', section)
        self.assertIn('| NOPAT ($B) | 20.0* |', section)
        self.assertIn('| 3-year incremental ROIC | n/m |', section)
        # The ROIC basis note gives the one definition of n/m (computed-text audit, 2026-11-27).
        self.assertIn('It is n/m when capital fell or rose by less than a tenth', section)
        self.assertIn('EBIT is pretax income plus net interest expense', section)
        self.assertNotIn('ROIC with intangibles capitalized |', section)
        self.assertIn('which the filings do not report apart', section)
        self.assertIn('8.8% for Russell 3000 companies', section)
        self.assertIn('No adjustment is made for restructuring charges', section)


if __name__ == '__main__':
    unittest.main()

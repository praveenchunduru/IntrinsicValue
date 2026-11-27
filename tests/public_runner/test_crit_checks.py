"""Checks revised after the 2026-11-26 critiques (Druckenmiller, Damodaran, Mauboussin lenses).

Fixtures are trimmed from SEC company facts cached on 2026-11-26: Nvidia's quarters
for fiscal 2022 to the third quarter of fiscal 2024 as filed by 2023-12-15, Costco's
fiscal 2025 quarters, Uber's operating margins for 2021-2025 and its four quarters to
September 2026, and Apple, Microsoft and Costco reinvestment rows for the last four
fiscal years.
"""
import copy
import unittest

from investmentideas import evidence_tables, pipeline, roic
from investmentideas.evidence_tables import historical_baseline, quarterly_table, render_quarterly, render_roic
from investmentideas.quality import assessment_findings
from investmentideas.valuation import evaluate

from tests.public_runner.test_market_consistency import plan

SOURCES = [{'id': 'S2', 'source_tier': 'regulatory_primary', 'data': {}}]


def facts(**concepts):
    """concepts: name -> [(start, end, value, filed, form)]"""
    def row(start, end, value, filed, form):
        item = {'end': end, 'val': value, 'filed': filed, 'form': form}
        if start:
            item['start'] = start
        return item
    return {'facts': {'us-gaap': {name: {'units': {'USD': [row(*item) for item in rows]}}
                                  for name, rows in concepts.items()}}}


# Every filing of each period, so the year-earlier comparatives are there.
NVIDIA = facts(
    Revenues=[
        ('2021-02-01', '2021-05-02', 5661e6, '2021-05-26', '10-Q'),
        ('2021-02-01', '2021-05-02', 5661e6, '2022-05-27', '10-Q'),
        ('2021-02-01', '2021-08-01', 12168e6, '2021-08-20', '10-Q'),
        ('2021-02-01', '2021-08-01', 12168e6, '2022-08-31', '10-Q'),
        ('2021-05-03', '2021-08-01', 6507e6, '2021-08-20', '10-Q'),
        ('2021-05-03', '2021-08-01', 6507e6, '2022-08-31', '10-Q'),
        ('2021-02-01', '2021-10-31', 19271e6, '2021-11-22', '10-Q'),
        ('2021-02-01', '2021-10-31', 19271e6, '2022-11-18', '10-Q'),
        ('2021-08-02', '2021-10-31', 7103e6, '2021-11-22', '10-Q'),
        ('2021-08-02', '2021-10-31', 7103e6, '2022-11-18', '10-Q'),
        ('2021-02-01', '2022-01-30', 26914e6, '2022-03-18', '10-K'),
        ('2021-02-01', '2022-01-30', 26914e6, '2023-02-24', '10-K'),
        ('2022-01-31', '2022-05-01', 8288e6, '2022-05-27', '10-Q'),
        ('2022-01-31', '2022-05-01', 8288e6, '2023-05-26', '10-Q'),
        ('2022-01-31', '2022-07-31', 14992e6, '2022-08-31', '10-Q'),
        ('2022-01-31', '2022-07-31', 14992e6, '2023-08-28', '10-Q'),
        ('2022-05-02', '2022-07-31', 6704e6, '2022-08-31', '10-Q'),
        ('2022-05-02', '2022-07-31', 6704e6, '2023-08-28', '10-Q'),
        ('2022-01-31', '2022-10-30', 20923e6, '2022-11-18', '10-Q'),
        ('2022-01-31', '2022-10-30', 20923e6, '2023-11-21', '10-Q'),
        ('2022-08-01', '2022-10-30', 5931e6, '2022-11-18', '10-Q'),
        ('2022-08-01', '2022-10-30', 5931e6, '2023-11-21', '10-Q'),
        ('2022-01-31', '2023-01-29', 26974e6, '2023-02-24', '10-K'),
        ('2023-01-30', '2023-04-30', 7192e6, '2023-05-26', '10-Q'),
        ('2023-01-30', '2023-07-30', 20699e6, '2023-08-28', '10-Q'),
        ('2023-05-01', '2023-07-30', 13507e6, '2023-08-28', '10-Q'),
        ('2023-01-30', '2023-10-29', 38819e6, '2023-11-21', '10-Q'),
        ('2023-07-31', '2023-10-29', 18120e6, '2023-11-21', '10-Q')
    ],
    RevenueFromContractWithCustomerExcludingAssessedTax=[
        ('2021-02-01', '2022-01-30', 26914e6, '2022-03-18', '10-K')
    ],
    OperatingIncomeLoss=[
        ('2021-02-01', '2021-05-02', 1956e6, '2021-05-26', '10-Q'),
        ('2021-02-01', '2021-05-02', 1956e6, '2022-05-27', '10-Q'),
        ('2021-02-01', '2021-08-01', 4400e6, '2021-08-20', '10-Q'),
        ('2021-02-01', '2021-08-01', 4400e6, '2022-08-31', '10-Q'),
        ('2021-05-03', '2021-08-01', 2444e6, '2021-08-20', '10-Q'),
        ('2021-05-03', '2021-08-01', 2444e6, '2022-08-31', '10-Q'),
        ('2021-02-01', '2021-10-31', 7071e6, '2021-11-22', '10-Q'),
        ('2021-02-01', '2021-10-31', 7071e6, '2022-11-18', '10-Q'),
        ('2021-08-02', '2021-10-31', 2671e6, '2021-11-22', '10-Q'),
        ('2021-08-02', '2021-10-31', 2671e6, '2022-11-18', '10-Q'),
        ('2021-02-01', '2022-01-30', 10041e6, '2022-03-18', '10-K'),
        ('2021-02-01', '2022-01-30', 10041e6, '2023-02-24', '10-K'),
        ('2022-01-31', '2022-05-01', 1868e6, '2022-05-27', '10-Q'),
        ('2022-01-31', '2022-05-01', 1868e6, '2023-05-26', '10-Q'),
        ('2022-01-31', '2022-07-31', 2367e6, '2022-08-31', '10-Q'),
        ('2022-01-31', '2022-07-31', 2367e6, '2023-08-28', '10-Q'),
        ('2022-05-02', '2022-07-31', 499e6, '2022-08-31', '10-Q'),
        ('2022-05-02', '2022-07-31', 499e6, '2023-08-28', '10-Q'),
        ('2022-01-31', '2022-10-30', 2968e6, '2022-11-18', '10-Q'),
        ('2022-01-31', '2022-10-30', 2968e6, '2023-11-21', '10-Q'),
        ('2022-08-01', '2022-10-30', 601e6, '2022-11-18', '10-Q'),
        ('2022-08-01', '2022-10-30', 601e6, '2023-11-21', '10-Q'),
        ('2022-01-31', '2023-01-29', 4224e6, '2023-02-24', '10-K'),
        ('2023-01-30', '2023-04-30', 2140e6, '2023-05-26', '10-Q'),
        ('2023-01-30', '2023-07-30', 8941e6, '2023-08-28', '10-Q'),
        ('2023-05-01', '2023-07-30', 6800e6, '2023-08-28', '10-Q'),
        ('2023-01-30', '2023-10-29', 19358e6, '2023-11-21', '10-Q'),
        ('2023-07-31', '2023-10-29', 10417e6, '2023-11-21', '10-Q')
    ],
    InventoryNet=[
        (None, '2021-05-02', 1992e6, '2021-05-26', '10-Q'), (None, '2021-08-01', 2114e6, '2021-08-20', '10-Q'),
        (None, '2021-10-31', 2233e6, '2021-11-22', '10-Q'), (None, '2022-01-30', 2605e6, '2022-03-18', '10-K'),
        (None, '2022-01-30', 2605e6, '2022-05-27', '10-Q'), (None, '2022-01-30', 2605e6, '2022-08-31', '10-Q'),
        (None, '2022-01-30', 2605e6, '2022-11-18', '10-Q'), (None, '2022-01-30', 2605e6, '2023-02-24', '10-K'),
        (None, '2022-05-01', 3163e6, '2022-05-27', '10-Q'), (None, '2022-07-31', 3889e6, '2022-08-31', '10-Q'),
        (None, '2022-10-30', 4454e6, '2022-11-18', '10-Q'), (None, '2023-01-29', 5159e6, '2023-02-24', '10-K'),
        (None, '2023-01-29', 5159e6, '2023-05-26', '10-Q'), (None, '2023-01-29', 5159e6, '2023-08-28', '10-Q'),
        (None, '2023-01-29', 5159e6, '2023-11-21', '10-Q'), (None, '2023-04-30', 4611e6, '2023-05-26', '10-Q'),
        (None, '2023-07-30', 4319e6, '2023-08-28', '10-Q'), (None, '2023-10-29', 4779e6, '2023-11-21', '10-Q')
    ],
    ContractWithCustomerLiabilityCurrent=[
        (None, '2021-05-02', 333e6, '2021-05-26', '10-Q'), (None, '2021-08-01', 301e6, '2021-08-20', '10-Q'),
        (None, '2021-10-31', 298e6, '2021-11-22', '10-Q'), (None, '2022-01-30', 300e6, '2022-03-18', '10-K'),
        (None, '2022-01-30', 300e6, '2022-05-27', '10-Q'), (None, '2022-01-30', 300e6, '2022-08-31', '10-Q'),
        (None, '2022-01-30', 300e6, '2022-11-18', '10-Q'), (None, '2022-01-30', 300e6, '2023-02-24', '10-K'),
        (None, '2022-05-01', 334e6, '2022-05-27', '10-Q'), (None, '2022-07-31', 359e6, '2022-08-31', '10-Q'),
        (None, '2022-10-30', 338e6, '2022-11-18', '10-Q'), (None, '2023-01-29', 354e6, '2023-02-24', '10-K'),
        (None, '2023-01-29', 354e6, '2023-05-26', '10-Q'), (None, '2023-01-29', 354e6, '2023-08-28', '10-Q'),
        (None, '2023-01-29', 354e6, '2023-11-21', '10-Q'), (None, '2023-04-30', 367e6, '2023-05-26', '10-Q'),
        (None, '2023-07-30', 421e6, '2023-08-28', '10-Q'), (None, '2023-10-29', 513e6, '2023-11-21', '10-Q')
    ],
)
# Four quarters to October 2023: fiscal 2023 less nine months, then three reported quarters.
NVIDIA_TTM_MARGIN = (4224 - 2968 + 2140 + 6800 + 10417) / (26974 - 20923 + 7192 + 13507 + 18120)
# Nvidia's reported annual operating margins for fiscal 2019-2023, as filed by 2023-12-15.
NVIDIA_MARGINS = (('2019-01-27', .324684), ('2020-01-26', .26067), ('2021-01-31', .271784), ('2022-01-30', .373077),
                  ('2023-01-29', .156595))
COSTCO = facts(
    RevenueFromContractWithCustomerExcludingAssessedTax=[
        ('2024-09-02', '2024-11-24', 62151e6, '2024-12-19', '10-Q'), ('2024-09-02', '2025-02-16', 125874e6, '2025-03-13', '10-Q'),
        ('2024-11-25', '2025-02-16', 63723e6, '2025-03-13', '10-Q'), ('2024-09-02', '2025-05-11', 189079e6, '2025-06-05', '10-Q'),
        ('2025-02-17', '2025-05-11', 63205e6, '2025-06-05', '10-Q'), ('2024-09-02', '2025-08-31', 275235e6, '2025-10-08', '10-K')],
    OperatingIncomeLoss=[('2024-09-02', '2024-11-24', 2196e6, '2024-12-19', '10-Q'), ('2024-09-02', '2025-02-16', 4512e6, '2025-03-13', '10-Q'),
                         ('2024-11-25', '2025-02-16', 2316e6, '2025-03-13', '10-Q'), ('2024-09-02', '2025-05-11', 7042e6, '2025-06-05', '10-Q'),
                         ('2025-02-17', '2025-05-11', 2530e6, '2025-06-05', '10-Q'), ('2024-09-02', '2025-08-31', 10383e6, '2025-10-08', '10-K')])
# Johnson & Johnson as filed by 2023-11-26: its October 2023 10-Q restated the nine months of 2022 without
# Kenvue (USD 60,051 million against 71,237 million first filed).
JOHNSON = facts(
    RevenueFromContractWithCustomerExcludingAssessedTax=[
        ('2021-01-04', '2021-04-04', 22321e6, '2021-04-30', '10-Q'),
        ('2021-01-04', '2021-04-04', 22321e6, '2022-04-29', '10-Q'),
        ('2021-01-04', '2021-07-04', 45633e6, '2021-07-29', '10-Q'),
        ('2021-01-04', '2021-07-04', 45633e6, '2022-07-29', '10-Q'),
        ('2021-04-05', '2021-07-04', 23312e6, '2021-07-29', '10-Q'),
        ('2021-04-05', '2021-07-04', 23312e6, '2022-07-29', '10-Q'),
        ('2021-01-04', '2021-10-03', 68971e6, '2021-10-29', '10-Q'),
        ('2021-01-04', '2021-10-03', 68971e6, '2022-10-27', '10-Q'),
        ('2021-07-05', '2021-10-03', 23338e6, '2021-10-29', '10-Q'),
        ('2021-07-05', '2021-10-03', 23338e6, '2022-10-27', '10-Q'),
        ('2021-01-04', '2022-01-02', 93775e6, '2022-02-17', '10-K'),
        ('2021-01-04', '2022-01-02', 93775e6, '2023-02-16', '10-K'),
        ('2022-01-03', '2022-04-03', 23426e6, '2022-04-29', '10-Q'),
        ('2022-01-03', '2022-04-03', 23426e6, '2023-04-28', '10-Q'),
        ('2022-01-03', '2022-07-03', 47446e6, '2022-07-29', '10-Q'),
        ('2022-01-03', '2022-07-03', 47446e6, '2023-07-31', '10-Q'),
        ('2022-04-04', '2022-07-03', 24020e6, '2022-07-29', '10-Q'),
        ('2022-04-04', '2022-07-03', 24020e6, '2023-07-31', '10-Q'),
        ('2022-01-03', '2022-10-02', 71237e6, '2022-10-27', '10-Q'),
        ('2022-01-03', '2022-10-02', 60051e6, '2023-10-27', '10-Q'),
        ('2022-07-04', '2022-10-02', 23791e6, '2022-10-27', '10-Q'),
        ('2022-07-04', '2022-10-02', 19996e6, '2023-10-27', '10-Q'),
        ('2022-01-03', '2023-01-01', 94943e6, '2023-02-16', '10-K'),
        ('2023-01-02', '2023-04-02', 24746e6, '2023-04-28', '10-Q'),
        ('2023-01-02', '2023-07-02', 50276e6, '2023-07-31', '10-Q'),
        ('2023-04-03', '2023-07-02', 25530e6, '2023-07-31', '10-Q'),
        ('2023-01-02', '2023-10-01', 63764e6, '2023-10-27', '10-Q'),
        ('2023-07-03', '2023-10-01', 21351e6, '2023-10-27', '10-Q')
    ],
)
UBER_MARGINS = (('2021-12-31', -.21965053), ('2022-12-31', -.05747090), ('2023-12-31', .02977388), ('2024-12-31', .06364546),
                ('2025-12-31', .10698426))
UBER_TTM = {'period': {'start_date': '2025-10-01', 'end_date': '2026-09-30'}, 'revenue': 56526e6, 'operating_income': 7034e6,
            'operating_margin': 7034 / 56526, 'quarters': 4, 'quarters_after_fiscal_year': 3}
APPLE = [
    {'fiscal_year_end': '2023-09-30', 'revenue': 383285e6, 'capital_expenditure': 10959e6, 'depreciation_amortization': 11519e6, 'invested_capital': 68648.7e6},
    {'fiscal_year_end': '2024-09-28', 'revenue': 391035e6, 'capital_expenditure': 9447e6, 'depreciation_amortization': 11445e6, 'invested_capital': 60637.7e6},
    {'fiscal_year_end': '2025-09-27', 'revenue': 416161e6, 'capital_expenditure': 12715e6, 'depreciation_amortization': 11698e6, 'invested_capital': 89842.2e6},
    {'fiscal_year_end': '2026-09-26', 'revenue': 478834e6, 'capital_expenditure': 9041e6, 'depreciation_amortization': 13423e6, 'invested_capital': 119177.7e6}]
MICROSOFT = [
    {'fiscal_year_end': '2023-06-30', 'revenue': 211915e6, 'capital_expenditure': 28107e6, 'depreciation_amortization': 11000e6, 'finance_lease_additions': 3128e6, 'invested_capital': 196171.3e6},
    {'fiscal_year_end': '2024-06-30', 'revenue': 245122e6, 'capital_expenditure': 44477e6, 'depreciation_amortization': 15200e6, 'finance_lease_additions': 11633e6, 'invested_capital': 310578.4e6},
    {'fiscal_year_end': '2025-06-30', 'revenue': 281724e6, 'capital_expenditure': 64551e6, 'depreciation_amortization': 22000e6, 'finance_lease_additions': 20511e6, 'invested_capital': 376448.5e6},
    {'fiscal_year_end': '2026-06-30', 'revenue': 331839e6, 'capital_expenditure': 115948e6, 'depreciation_amortization': 34300e6, 'finance_lease_additions': 24608e6, 'invested_capital': 492223.8e6}]
COSTCO_ROWS = [
    {'fiscal_year_end': '2023-09-03', 'revenue': 242290e6, 'capital_expenditure': 4323e6, 'depreciation_amortization': 2077e6, 'finance_lease_additions': 100e6, 'invested_capital': 26103.8e6},
    {'fiscal_year_end': '2024-09-01', 'revenue': 254453e6, 'capital_expenditure': 4710e6, 'depreciation_amortization': 2237e6, 'finance_lease_additions': 200e6, 'invested_capital': 28415.1e6},
    {'fiscal_year_end': '2025-08-31', 'revenue': 275235e6, 'capital_expenditure': 5498e6, 'depreciation_amortization': 2426e6, 'finance_lease_additions': 131e6, 'invested_capital': 30286.7e6},
    {'fiscal_year_end': '2026-08-30', 'revenue': 303154e6, 'capital_expenditure': 6435e6, 'depreciation_amortization': 2674e6, 'finance_lease_additions': 122e6, 'invested_capital': 32103.1e6}]


def history(margins, quarterly=None):
    rows = [{'fiscal_year_end': end, 'revenue': 100e9, 'operating_margin': margin} for end, margin in margins]
    baseline = {'rows': rows}
    if quarterly:
        baseline['quarterly'] = quarterly
    return [{'id': 'S2', 'source_tier': 'regulatory_primary', 'data': {}, 'historical_baseline': baseline}]


def sources(rows):
    return [{'id': 'S2', 'source_tier': 'regulatory_primary', 'data': {}, 'historical_baseline': {'rows': copy.deepcopy(rows)}}]


class QuarterlyTableTests(unittest.TestCase):
    def test_nvidia_quarters_show_the_inflection_the_annual_table_hides(self):
        table = quarterly_table(NVIDIA, as_of='2023-12-15')
        rows = {row['period_end']: row for row in table['rows']}
        self.assertEqual(len(table['rows']), 8)
        self.assertEqual(table['rows'][-1]['period_end'], '2023-10-29')
        latest = rows['2023-10-29']
        self.assertEqual((latest['fiscal_quarter'], latest['revenue'], latest['revenue_basis']), (3, 18120e6, 'reported'))
        self.assertAlmostEqual(latest['revenue_growth'], 18120 / 5931 - 1)
        self.assertAlmostEqual(latest['operating_margin'], 10417 / 18120)
        self.assertEqual((latest['inventory'], latest['deferred_revenue']), (4779e6, 513e6))
        # The fourth quarter is the fiscal year less nine months.
        fourth = rows['2023-01-29']
        self.assertEqual(fourth['revenue_basis'], 'fiscal year less nine months')
        self.assertAlmostEqual(fourth['revenue'], 26974e6 - 20923e6)
        self.assertAlmostEqual(fourth['operating_income'], 4224e6 - 2968e6)
        # Fiscal 2022's annual total is contract revenue, its quarters Revenues: one concept per year.
        self.assertAlmostEqual(rows['2022-01-30']['revenue'], 26914e6 - 19271e6)
        self.assertEqual(rows['2022-01-30']['revenue_concept'], 'Revenues')
        ttm = table['trailing_twelve_months']
        self.assertEqual(ttm['period'], {'start_date': '2022-10-31', 'end_date': '2023-10-29'})
        # The four quarters summed (the nine-month total rounds a million away from its quarters).
        self.assertAlmostEqual(ttm['operating_margin'], NVIDIA_TTM_MARGIN)

    def test_a_filing_date_cutoff_drops_later_quarters(self):
        table = quarterly_table(NVIDIA, as_of='2023-06-15')
        self.assertEqual(table['rows'][-1]['period_end'], '2023-04-30')
        self.assertAlmostEqual(table['trailing_twelve_months']['operating_margin'], (4224 - 1868 + 2140) / (26974 - 8288 + 7192))

    def test_a_sixteen_week_fourth_quarter_is_derived(self):
        table = quarterly_table(COSTCO)
        fourth = table['rows'][-1]
        self.assertEqual((fourth['period_start'], fourth['period_end'], fourth['fiscal_quarter']), ('2025-05-12', '2025-08-31', 4))
        self.assertAlmostEqual(fourth['revenue'], 275235e6 - 189079e6)
        # The latest quarter closes the fiscal year: the annual history already holds it.
        self.assertNotIn('trailing_twelve_months', table)

    def test_too_few_quarters_give_no_table(self):
        self.assertIsNone(quarterly_table(facts(Revenues=[('2025-01-01', '2025-12-31', 1e9, '2026-02-01', '10-K')])))

    def test_the_table_reaches_the_plan_author_and_the_memo(self):
        baseline = {'rows': [], 'quarterly': quarterly_table(NVIDIA, as_of='2023-12-15')}
        text = render_quarterly(baseline, 'S2')
        self.assertIn('Reported fiscal quarters [S2]', text)
        self.assertIn('2023-10-29 | Q3 | 18.1 | 205.5% | 57.5% | 4.8 | 0.5', text)
        self.assertIn('2023-01-29 | Q4 (derived)', text)
        self.assertIn('Trailing twelve months to 2023-10-29', text)
        rows = [{'fiscal_year_end': '2023-01-29', 'currency': 'USD', 'revenue': 26974e6}]
        for key in ('revenue_growth', 'gross_margin', 'operating_margin', 'research_and_development_to_revenue',
                    'selling_general_administrative_to_revenue', 'effective_tax_rate', 'capital_expenditure_to_revenue',
                    'depreciation_amortization_to_revenue', 'stock_compensation_to_revenue', 'repurchases', 'dividends'):
            rows[0][key] = None
        source = {'id': 'S2', 'historical_baseline': dict(baseline, rows=rows)}
        self.assertIn('Reported fiscal quarters [S2]', pipeline._computed_tables([source]))
        section = '\n'.join(pipeline.reported_tables_section([source]))
        self.assertIn('| Measure | 2022-01-30 Q4 (derived) | 2022-05-01 Q1 |', section)
        self.assertIn('| Growth on the year-earlier quarter |', section)
        self.assertIn('| Deferred revenue at quarter end |', section)

    def test_the_history_carries_the_quarters(self):
        payload = copy.deepcopy(COSTCO)
        self.assertEqual(historical_baseline(payload)['quarterly']['rows'][-1]['period_end'], '2025-08-31')


class MarginCeilingTests(unittest.TestCase):
    def nvidia(self, as_of):
        return pipeline.cost_path_reference(history(NVIDIA_MARGINS, quarterly_table(NVIDIA, as_of=as_of)))

    def test_twelve_reported_months_above_the_peak_lift_the_base_and_bull_ceiling(self):
        june, december = self.nvidia('2023-06-15'), self.nvidia('2023-12-15')
        # June 2023: the last twelve months ran at 17.4%, below the 37.3% peak.
        self.assertAlmostEqual(june['final_year_margin_ceilings']['base'], .373077 + .05)
        ttm = NVIDIA_TTM_MARGIN
        self.assertEqual(december['latest_run_rate']['basis'], 'trailing_twelve_months')
        self.assertAlmostEqual(december['final_year_margin_ceilings']['base'], ttm + .05)
        self.assertAlmostEqual(december['final_year_margin_ceilings']['bull'], ttm + .05)
        # Bear stays the five-year average, and the annual peak is unchanged for the convention panel.
        self.assertAlmostEqual(december['final_year_margin_ceilings']['bear'], sum(m for _, m in NVIDIA_MARGINS) / 5)
        self.assertAlmostEqual(december['max_operating_margin'], .373077)
        self.assertIn('twelve months to 2023-10-29', pipeline._margin_ceiling('base', december)[1])

    def test_a_plan_at_the_reported_run_rate_passes_once_the_quarters_show_it(self):
        value = plan()
        for name, margin in (('bear', .25), ('base', .48), ('bull', .50)):
            value['scenarios'][name]['ebit_margins'] = [margin] * 10
        value['cost_path_reference'] = self.nvidia('2023-12-15')
        pipeline._check_continuity(value, pipeline._evaluate(value), SOURCES)
        value['cost_path_reference'] = self.nvidia('2023-06-15')
        with self.assertRaisesRegex(ValueError, r'base 48\.0% against the reported five-year peak plus 5\.0 points'):
            pipeline._check_continuity(value, pipeline._evaluate(value), SOURCES)

    def test_a_year_to_date_of_two_quarters_stands_in_for_missing_quarters(self):
        operating = lambda days, margin: {'year_to_date': {
            'period': {'start_date': '2026-07-01', 'end_date': '2026-12-31' if days > 100 else '2026-09-30'},
            'operating_margin': margin, 'source_refs': ['S5']}}
        margins = (('2022-06-30', .30), ('2023-06-30', .31), ('2024-06-30', .32), ('2025-06-30', .33), ('2026-06-30', .34))
        reference = pipeline.cost_path_reference(history(margins), operating(180, .40))
        self.assertEqual(reference['latest_run_rate']['basis'], 'year_to_date')
        self.assertAlmostEqual(reference['final_year_margin_ceilings']['base'], .45)
        # One quarter is seasonal and does not count.
        reference = pipeline.cost_path_reference(history(margins), operating(90, .40))
        self.assertNotIn('latest_run_rate', reference)
        self.assertAlmostEqual(reference['final_year_margin_ceilings']['base'], .39)
        # A lower run-rate never lowers the ceiling.
        reference = pipeline.cost_path_reference(history(margins), operating(180, .20))
        self.assertAlmostEqual(reference['final_year_margin_ceilings']['base'], .39)

    def test_uber_bear_is_held_to_its_peak_while_its_average_is_negative(self):
        # Changed 2026-11-27 (data review): with no bear ceiling a bear above the base ceiling passed.
        reference = pipeline.cost_path_reference(history(UBER_MARGINS, {'rows': [], 'trailing_twelve_months': UBER_TTM}))
        self.assertLess(reference['average_operating_margin'], 0)
        self.assertAlmostEqual(reference['final_year_margin_ceilings']['bear'], .10698426)
        self.assertAlmostEqual(reference['final_year_margin_ceilings']['base'], UBER_TTM['operating_margin'] * 1.4)
        value = plan()
        for name, margin in (('bear', .05), ('base', .15), ('bull', .17)):
            value['scenarios'][name]['ebit_margins'] = [margin] * 10
        value['cost_path_reference'] = reference
        result = pipeline._evaluate(value)
        pipeline._check_continuity(value, result, SOURCES)
        self.assertIn('bear 5.0% (ceiling 10.7%)', '\n'.join(pipeline._continuity_exhibit(result)))
        value['scenarios']['bear']['ebit_margins'] = [.12] * 10
        with self.assertRaisesRegex(ValueError, r'bear 12\.0% against the reported five-year peak \(the five-year average '
                                                r'is not positive\) of 10\.7%'):
            pipeline._check_continuity(value, pipeline._evaluate(value), SOURCES)
        value['scenarios']['bear']['ebit_margins'] = [.05] * 10
        value['scenarios']['bull']['ebit_margins'] = [.18] * 10
        with self.assertRaisesRegex(ValueError, 'bull 18.0% against the operating margin for the twelve months to 2026-09-30'):
            pipeline._check_continuity(value, pipeline._evaluate(value), SOURCES)

    def test_a_twelve_month_span_with_a_53_week_year_is_labelled(self):
        ttm = dict(UBER_TTM, days=371, operating_margin=.30)
        reference = pipeline.cost_path_reference(history(UBER_MARGINS, {'rows': [], 'trailing_twelve_months': ttm}))
        self.assertIn('twelve months to 2026-09-30 (371 days)', pipeline._margin_ceiling('base', reference)[1])

    def test_references_need_the_history_to_end_at_the_revenue_anchor_year(self):
        rows = [dict(row, revenue=row['revenue']) for row in APPLE]
        self.assertIsNotNone(pipeline.reinvestment_reference(sources(rows), 478834e6))
        self.assertIsNone(pipeline.reinvestment_reference(sources(rows), 416161e6))
        margins = [{'fiscal_year_end': row['fiscal_year_end'], 'revenue': row['revenue'], 'operating_margin': .3} for row in rows]
        self.assertIsNotNone(pipeline.cost_path_reference(sources(margins), None, 478834e6))
        self.assertIsNone(pipeline.cost_path_reference(sources(margins), None, 416161e6))
        self.assertIsNone(pipeline.forecast_capital_reference(sources(rows), 416161e6))
        self.assertEqual(pipeline.forecast_capital_reference(sources(rows), 478834e6)['fiscal_year_end'], '2026-09-26')

    def test_every_ceiling_that_applies_leaves_room_for_a_positive_final_margin(self):
        for margins in (UBER_MARGINS, NVIDIA_MARGINS, (('2023-12-31', -.3), ('2024-12-31', -.1), ('2025-12-31', .001)),
                        (('2023-12-31', -.3), ('2024-12-31', -.2), ('2025-12-31', -.1))):
            reference = pipeline.cost_path_reference(history(margins))
            for name in pipeline.SCENARIO_NAMES:
                ceiling, _ = pipeline._margin_ceiling(name, reference)
                self.assertTrue(ceiling is None or ceiling > 0, (margins, name, ceiling))

    def test_a_profitable_quarter_run_does_not_bound_a_history_with_no_profitable_year(self):
        losses = (('2023-12-31', -.3), ('2024-12-31', -.2), ('2025-12-31', -.1))
        reference = pipeline.cost_path_reference(history(losses, {'rows': [], 'trailing_twelve_months': UBER_TTM}))
        self.assertNotIn('final_year_margin_ceilings', reference)


class ReinvestmentReferenceTests(unittest.TestCase):
    def test_the_lower_of_the_two_rates_is_the_reference(self):
        reference = pipeline.reinvestment_reference(sources(MICROSOFT))
        growth = 331839e6 - 211915e6
        nets = (44477 - 15200 + 11633 + 64551 - 22000 + 20511 + 115948 - 34300 + 24608) * 1e6
        self.assertAlmostEqual(reference['capital_expenditure_sales_to_capital'], growth / nets)
        self.assertAlmostEqual(reference['invested_capital_sales_to_capital'], growth / (492223.8e6 - 196171.3e6))
        self.assertEqual(reference['sales_to_capital_basis'], 'invested_capital')
        self.assertAlmostEqual(reference['incremental_sales_to_capital'], reference['invested_capital_sales_to_capital'])
        # The floor stays on the owner's net reinvestment.
        self.assertAlmostEqual(reference['net_reinvestment'], (115948 - 34300 + 24608) * 1e6)
        costco = pipeline.reinvestment_reference(sources(COSTCO_ROWS))
        self.assertEqual(costco['sales_to_capital_basis'], 'capital_expenditure')

    def test_capital_spending_below_depreciation_is_checked_when_invested_capital_rose(self):
        reference = pipeline.reinvestment_reference(sources(APPLE))
        self.assertEqual(reference['sales_to_capital_basis'], 'invested_capital')
        self.assertAlmostEqual(reference['incremental_sales_to_capital'], (478834 - 383285) / (119177.7 - 68648.7))
        self.assertIsNone(reference['net_reinvestment'])
        self.assertIsNone(reference['capital_expenditure_sales_to_capital'])
        value = plan()
        value['reinvestment_reference'] = reference
        for scenario in value['scenarios'].values():
            scenario['sales_to_capital'] = 3.0
        result = pipeline._evaluate(value)
        self.assertIsNone(result['reinvestment_continuity']['scenarios']['base']['reinvestment_floor'])
        with self.assertRaisesRegex(ValueError, r'(?s)at most 1\.5 times the 3-year incremental rate \(computed from reported lines\) of 1\.89 on the '
                                                r'change in invested capital.*base .* sales-to-capital 3\.00'):
            pipeline._check_continuity(value, result, SOURCES)
        for scenario in value['scenarios'].values():
            scenario['sales_to_capital'] = 2.5
        result = pipeline._evaluate(value)
        pipeline._check_continuity(value, result, SOURCES)
        self.assertIn('no floor', pipeline._reinvestment_text(result['reinvestment_continuity']))

    def test_falling_revenue_or_falling_capital_with_light_capex_is_not_checked(self):
        shrinking = [dict(row, revenue=500e9 - index * 10e9) for index, row in enumerate(APPLE)]
        self.assertIsNone(pipeline.reinvestment_reference(sources(shrinking)))
        falling = [dict(row, invested_capital=100e9 - index * 5e9) for index, row in enumerate(APPLE)]
        self.assertIsNone(pipeline.reinvestment_reference(sources(falling)))

    def test_a_gap_in_the_years_drops_the_invested_capital_rate(self):
        gapped = copy.deepcopy(APPLE)
        gapped[0]['fiscal_year_end'] = '2021-09-25'
        self.assertIsNone(pipeline.reinvestment_reference(sources(gapped)))


def year(end, **values):
    base = {'fiscal_year_end': end, 'revenue': 1000, 'operating_income': 200, 'income_tax_expense': 40,
            'pretax_income': 200, 'assets': 1500, 'current_liabilities': 300, 'cash': 100}
    return dict(base, **values)


class ReturnOnCapitalTests(unittest.TestCase):
    def test_reporting_selling_and_g_and_a_apart_from_a_later_year_switches_basis_without_a_gap(self):
        # Apple reports SG&A throughout and selling and G&A apart from fiscal 2023.
        rows = [year(f'20{10 + i}-12-31', research_and_development=100, selling_general_administrative=200) for i in range(12)]
        for row in rows[-4:]:
            row.update(selling_and_marketing=150, general_and_administrative=50)
        measures, status = roic.history(rows)
        self.assertEqual(status, 'computed')
        self.assertEqual([item['intangibles_basis'] for item in measures[-4:]], ['one_line', 'one_line', 'one_line', 'split'])
        self.assertTrue(all(item['adjusted_return'] is not None for item in measures[-4:]))

    def test_one_line_rows_are_labelled_and_the_note_states_the_shares(self):
        rows = [year(f'20{10 + i}-12-31', research_and_development=100, selling_general_administrative=200) for i in range(12)]
        measures, status = roic.history(rows)
        table = [dict(item, intangibles_capitalization=status) for item in measures[-5:]]
        text = render_roic({'rows': table}, 'S2')
        self.assertTrue(all(line.split(' | ')[8].endswith('(one line)') for line in text.split('\n')[3:7]))
        self.assertIn('100% of R&D over six years and 30% of SG&A', text)
        self.assertIn('Peters and Taylor', text)
        self.assertNotIn('Years marked (split)', text)

    def test_an_incremental_return_on_a_tiny_capital_change_is_not_meaningful(self):
        assets = (1500, 1600, 1700, 1610, 1900)
        rows, _ = roic.history([year(f'202{i}-12-31', assets=a) for i, a in enumerate(assets)])
        # Capital 1120 to 1230 is under a tenth of their average.
        self.assertIsNone(rows[-1]['incremental_return'])
        self.assertEqual(rows[-1]['incremental_return_status'], 'small_capital_change')
        text = render_roic({'rows': rows}, 'S2')
        self.assertIn('n/m (capital change under a tenth of capital)', text)
        rows, _ = roic.history([year(f'202{i}-12-31', assets=a) for i, a in enumerate((1500, 1600, 1700, 1800, 1900))])
        self.assertIsNotNone(rows[-1]['incremental_return'])

    def test_the_basis_note_flags_a_latest_tax_rate_far_from_its_three_year_median(self):
        rows = [{'fiscal_year_end': f'202{i}-06-30', 'nopat': 70e9, 'invested_capital': 200e9, 'nopat_tax_rate': rate,
                 'return_on_invested_capital': .331} for i, rate in enumerate((.199, .258, .226, .233, .107), 2)]
        text = render_roic({'rows': rows}, 'S2')
        self.assertIn('The latest NOPAT tax rate, 10.7% for the year ending 2026-06-30, differs from its three-year median '
                      'of 22.6% by more than 5 points', text)
        self.assertIn(f'ROIC would be {100 * .331 * (1 - .226) / (1 - .107):.1f}%', text)
        rows[-1]['nopat_tax_rate'] = .21
        self.assertNotIn('latest NOPAT tax rate', render_roic({'rows': rows}, 'S2'))

    def test_rnd_is_the_larger_of_the_inclusive_concept_and_its_part(self):
        # Johnson & Johnson 2025: the inclusive concept holds only acquired in-process R&D.
        payload = facts(Revenues=[('2024-01-01', '2024-12-29', 88821e6, '2026-02-11', '10-K'),
                                  ('2024-12-30', '2025-12-28', 94193e6, '2026-02-11', '10-K')],
                        ResearchAndDevelopmentExpense=[('2024-12-30', '2025-12-28', 109e6, '2026-02-11', '10-K')],
                        ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost=[
                            ('2024-12-30', '2025-12-28', 14665e6, '2026-02-11', '10-K')])
        latest = historical_baseline(payload, years=1)['rows'][-1]
        self.assertEqual(latest['research_and_development'], 14665e6)
        self.assertAlmostEqual(latest['research_and_development_to_revenue'], 14665 / 94193)


def quarter_facts(lengths):
    """Revenue for two fiscal years of quarters with the given day counts, each 10-Q also reporting the year before."""
    from datetime import date, timedelta
    rows, start = [], date(2023, 1, 1)
    years = []
    for year in lengths:
        ends, day = [], start
        for days in year:
            day = day + timedelta(days=days)
            ends.append(day - timedelta(days=1))
        years.append((start, ends))
        start = ends[-1] + timedelta(days=1)
    for index, (fiscal_start, ends) in enumerate(years):
        filed = lambda end: (end + timedelta(days=30)).isoformat()
        for number, end in enumerate(ends[:3]):
            rows.append((fiscal_start.isoformat(), end.isoformat(), 100e9 * (number + 1) * (1 + .1 * index), filed(end), '10-Q'))
        rows.append((fiscal_start.isoformat(), ends[3].isoformat(), 400e9 * (1 + .1 * index), filed(ends[3]), '10-K'))
    return facts(Revenues=rows, OperatingIncomeLoss=[(row[0], row[1], row[2] / 5, row[3], row[4]) for row in rows])


class QuarterlyReviewFixTests(unittest.TestCase):
    def test_quarters_of_different_length_show_no_growth(self):
        # A 53-week year: a 14-week fourth quarter against 13 weeks a year earlier.
        table = quarterly_table(quarter_facts([(91, 91, 91, 91), (91, 91, 91, 98)]))
        last = table['rows'][-1]
        self.assertEqual((last['days'], last['prior_days'], last['revenue_growth']), (98, 91, None))
        text = render_quarterly({'quarterly': table}, 'S2')
        self.assertIn('n/m (98 vs 91 days)', text)
        self.assertIn('compares quarters of different length', text)
        self.assertEqual(table['rows'][-2]['days'], 91)

    def test_a_derived_quarter_pairs_figures_from_one_vintage(self):
        # Johnson & Johnson as of 2023-11-26: 2022 less the original nine months, not the restated ones.
        rows = {row['period_end']: row for row in quarterly_table(JOHNSON, as_of='2023-11-26')['rows']}
        fourth = rows['2023-01-01']
        self.assertEqual(fourth['revenue_basis'], 'fiscal year less nine months')
        self.assertAlmostEqual(fourth['revenue'], 94943e6 - 71237e6)
        # Growth from the same two filings for both years: the 10-K's two years less the third-quarter 10-Q's two nine months.
        self.assertEqual(fourth['revenue_growth_basis'], 'same_filings')
        self.assertAlmostEqual(fourth['revenue_growth'], (94943 - 71237) / (93775 - 68971) - 1)
        # A reported quarter's growth comes from the filing that reports both years.
        self.assertEqual(rows['2023-10-01']['revenue_growth_basis'], 'same_filing')
        self.assertAlmostEqual(rows['2023-10-01']['revenue_growth'], 21351 / 19996 - 1)

    def test_growth_needs_both_years_in_the_same_filings(self):
        payload = copy.deepcopy(JOHNSON)
        series = payload['facts']['us-gaap']['RevenueFromContractWithCustomerExcludingAssessedTax']['units']['USD']
        # Keep only first filings: no 10-Q then reports the year-earlier quarter.
        first = {}
        for row in series:
            key = (row.get('start'), row['end'])
            if key not in first or row['filed'] < first[key]['filed']:
                first[key] = row
        payload['facts']['us-gaap']['RevenueFromContractWithCustomerExcludingAssessedTax']['units']['USD'] = list(first.values())
        rows = quarterly_table(payload, as_of='2023-11-26')['rows']
        self.assertTrue(all(row['revenue_growth'] is None for row in rows))
        self.assertIn('n/a where no filing reports both years', render_quarterly({'quarterly': {'rows': rows}}, 'S2'))


class PipelineReviewFixTests(unittest.TestCase):
    def test_the_median_rate_regime_value_traces_and_the_monthly_pairs_do_not(self):
        from tests.public_runner.test_crit_exhibits import history_plan
        from investmentideas import quality
        # The middle month is neither the lowest nor the highest row, so only the plan's list holds its rates.
        value = history_plan([['2021-08-01', .0123, .0431], ['2023-08-01', .03337, .04771], ['2026-03-01', .045, .045]])
        result = evaluate(value)
        median = result['convention_panel']['rate_history']['median_per_share_value']
        _, _, pool = quality.trace_pools([], (value, result))
        self.assertIn(round(abs(median), 6), [round(item, 6) for item in pool])
        self.assertNotIn(.04771, pool)
        self.assertNotIn(.03337, pool)

    def test_a_sentence_that_only_reports_the_benchmark_is_not_a_gap_cause(self):
        from investmentideas import quality
        self.assertFalse(quality._assigns_gap_cause('Under the same conventions the index benchmark is worth 0.78 of its '
                                                    'price, a discount to the price that rests on the premium.'))
        self.assertTrue(quality._assigns_gap_cause('Under the same conventions the index benchmark is worth 0.78 of its '
                                                   'price, so the difference between value and price is largely explained '
                                                   'by the house conventions.'))
        self.assertIn('not a cause of the difference between value and price', pipeline.SECTIONS)
        self.assertIn('A convention step is a row of the convention panel', pipeline.SECTIONS)

    def test_the_largest_horizon_and_rate_regime_rows_print_as_exhibits(self):
        from tests.public_runner.test_crit_exhibits import history_plan
        value = history_plan()
        result = evaluate(value)
        summary = pipeline.computed_summary(value, result)
        line = next(item for item in summary.split('\n') if item.startswith('Largest exhibit rows'))
        self.assertIn('not convention steps', line)
        self.assertIn('horizon, ', line)
        self.assertIn('rate regime, ', line)
        panel = '\n'.join(pipeline._market_consistency_exhibits(result, 'USD'))
        self.assertIn('Largest exhibit rows', panel)

    def test_minor_wording_fixes(self):
        self.assertIn('base-rate tables may be cited', pipeline.FORECAST_SCHEMA)
        self.assertIn('a list of one ratio per forecast year', pipeline.FORECAST_SCHEMA)
        self.assertIn('latest twelve-month operating margin', pipeline.UNDERWRITING_GUIDANCE)
        self.assertIn('the five-year peak where that average is not positive', pipeline.UNDERWRITING_GUIDANCE)
        view = pipeline._proposal_view({'scenarios': {'base': {'rnd_share_path': [.1], 'rnd_basis': 'x', 'tax_rate': .2}}})
        self.assertEqual(view['scenarios']['base'], {'tax_rate': .2})
        contract = pipeline._workflow_contract()['implementation']
        self.assertTrue(contract)
        import inspect
        source = inspect.getsource(pipeline._workflow_contract)
        for name in ('cap_fade.py', 'roic.py', 'macro.py'):
            self.assertIn(name, source)

    def test_net_cash_reads_as_plus_net_cash(self):
        value = plan()
        value['actuals']['net_debt'] = -50.0
        value['net_debt_bridge'] = dict(value['net_debt_bridge'], debt=0.0, net_debt=-50.0)
        result = evaluate(value)
        text = '\n'.join(pipeline._equity_bridge_exhibit(value, result, 'USD'))
        self.assertIn('Plus net cash', text)
        self.assertNotIn('Less net debt', text)

    def test_the_repair_message_counts_acquisitions_and_offers_the_cited_basis(self):
        rows = [dict(row, acquisitions=2e9) for row in MICROSOFT]
        value = plan()
        value['reinvestment_reference'] = pipeline.reinvestment_reference(sources(rows))
        for scenario in value['scenarios'].values():
            scenario['sales_to_capital'] = 5.0
        with self.assertRaises(ValueError) as caught:
            pipeline._check_continuity(value, pipeline._evaluate(value), SOURCES)
        message = str(caught.exception)
        self.assertIn('Both rates count the 6.0 billion paid in cash for acquisitions', message)
        self.assertIn('capital_efficiency_basis', message)
        self.assertIn('acquisitions the forecast does not repeat', message)
        self.assertNotIn('Set sales_to_capital from the reported rate', message)

    def test_goodwill_from_stock_deals_does_not_collapse_the_invested_capital_rate(self):
        # Goodwill and acquired intangibles grow by 100 billion while 30 billion is paid in cash.
        rows = copy.deepcopy(MICROSOFT)
        for index, row in enumerate(rows):
            acquired = 50e9 + (100e9 if index == 3 else 0)
            row['invested_capital_excluding_acquired'] = row['invested_capital'] - acquired
            row['acquisitions'] = 30e9 if index == 3 else 0
        reference = pipeline.reinvestment_reference(sources(rows))
        growth = 331839e6 - 211915e6
        self.assertEqual(reference['invested_capital_measure'], 'stock_deals_excluded')
        self.assertAlmostEqual(reference['acquired_intangibles_not_paid_in_cash'], 70e9)
        self.assertAlmostEqual(reference['invested_capital_sales_to_capital'], growth / (492223.8e6 - 196171.3e6 - 70e9))
        self.assertIn('70.0 billion of it is left out here', reference['basis'])
        # Paid in cash, or with no deal (the balance amortizing), the capital change is as reported.
        for row in rows:
            row['acquisitions'] = 100e9 if row is rows[3] else 0
        self.assertEqual(pipeline.reinvestment_reference(sources(rows))['invested_capital_measure'], 'as_reported')
        # With no acquisitions line at all the payment is unknown: as reported.
        for row in rows:
            row.pop('acquisitions')
        self.assertEqual(pipeline.reinvestment_reference(sources(rows))['invested_capital_measure'], 'as_reported')
        self.assertEqual(pipeline.reinvestment_reference(sources(APPLE))['invested_capital_measure'], 'as_reported')

    def test_missing_and_non_positive_net_reinvestment_read_differently(self):
        apple = pipeline.reinvestment_reference(sources(APPLE))
        self.assertEqual(apple['net_reinvestment_status'], 'not_positive')
        bare = [{key: row[key] for key in ('fiscal_year_end', 'revenue', 'invested_capital')} for row in APPLE]
        missing = pipeline.reinvestment_reference(sources(bare))
        self.assertEqual(missing['net_reinvestment_status'], 'not_reported')
        value = plan()
        value['reinvestment_reference'] = missing
        text = pipeline._reinvestment_text(pipeline._evaluate(value)['reinvestment_continuity'])
        self.assertIn('not reported for every year', text)

    def test_revalue_carries_the_pipeline_diagnostics(self):
        value = plan()
        value['cost_path_reference'] = pipeline.cost_path_reference(history(UBER_MARGINS))
        result = pipeline.attach_diagnostics(value, evaluate(copy.deepcopy(value)))
        self.assertIn('cost_path_continuity', result)


class ReviewAssessmentTests(unittest.TestCase):
    def test_an_unhandled_central_weakness_stays_a_recorded_answer(self):
        # docs/underwriting.md (Review limits) still describes the pre-2026-11-15 major finding.
        review = {'verdict': 'pass', 'findings': [], 'gap_explained': 'no',
                  'central_weakness': {'claim': 'x', 'computed_effect': 'y', 'memo_handles_it': 'no'}}
        self.assertEqual(assessment_findings(review), [])


if __name__ == '__main__':
    unittest.main()

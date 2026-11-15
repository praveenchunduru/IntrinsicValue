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


class QuarterlySplitTests(unittest.TestCase):
    def test_a_split_restated_first_in_a_10q_blanks_the_old_basis_counts(self):
        shares = [fact(None, end, value, '2025-11-01') for (_, end), value in zip(YEARS, [100e6, 97e6, 94e6])]
        shares.append(fact(None, YEARS[2][1], 940e6, '2026-02-01', form='10-Q'))
        rows = historical_baseline(payload(**dict(BASE, CommonStockSharesOutstanding=('shares', shares))),
                                   years=2, as_of='2026-03-01')['rows']
        self.assertIsNone(rows[-1]['shares_outstanding'])
        self.assertIsNone(rows[-1]['diluted_eps'])
        self.assertIsNone(rows[-1]['shares_repurchased'])
        self.assertIsNotNone(rows[-1]['revenue'])

    def test_one_revised_eps_value_is_not_a_split(self):
        eps = annual([5.0, 5.5, 6.6]) + [fact(*YEARS[1], 0.5, '2026-02-01', form='10-Q')]
        rows = historical_baseline(payload(**dict(BASE, EarningsPerShareDiluted=('USD/shares', eps))),
                                   years=2, as_of='2026-03-01')['rows']
        self.assertEqual(rows[-1]['shares_outstanding'], 940e6)
        self.assertEqual(rows[-1]['diluted_eps'], 6.6)

    def test_two_eps_values_restated_by_one_factor_are_a_split(self):
        eps = annual([5.0, 5.5, 6.6]) + [fact(*YEARS[1], 0.55, '2026-02-01', form='10-Q'),
                                          fact(*YEARS[2], 0.66, '2026-02-01', form='10-Q')]
        rows = historical_baseline(payload(**dict(BASE, EarningsPerShareDiluted=('USD/shares', eps))),
                                   years=2, as_of='2026-03-01')['rows']
        self.assertIsNone(rows[-1]['diluted_eps'])


# Patterns from a free sweep of 13 issuers (2026-11-15). Amounts are synthetic
# unless a comment names the issuer's reported figure.
from investmentideas.evidence_tables import _split_date, filing_supplement, filing_supplement_for  # noqa: E402

START, END = YEARS[2]


def supplement(*facts, report_date=END, filed='2025-11-01'):
    return {'source_id': 'S4', 'filing_date': filed, 'form': '10-K', 'report_date': report_date, 'facts': list(facts)}


def inline(concept, value, *, unit='USD', start=START, end=END, decimals=None, **dimensions):
    return {'concept': concept, 'dimensions': {axis.replace('__', ':'): member for axis, member in dimensions.items()},
            'unit': unit, 'value': str(value), 'start': start, 'end': end, 'decimals': decimals}


EQUITY = 'us-gaap__StatementEquityComponentsAxis'
PROGRAM = 'srt__ShareRepurchaseProgramAxis'


class FilingSupplementTests(unittest.TestCase):
    def latest(self, extra=None, drop=(), supplement_=None, render=False):
        concepts = {key: value for key, value in dict(BASE, **(extra or {})).items() if key not in drop}
        baseline = historical_baseline(payload(**concepts), years=2, supplement=supplement_)
        return render_baseline(baseline, 'S2') if render else baseline['rows'][-1]

    def test_second_business_payment_is_added_only_when_it_cannot_be_part_of_the_first(self):
        # NVIDIA fiscal 2026: Groq $13.0 billion beside $1.535 billion of other acquisitions.
        row = self.latest({'PaymentsToAcquireBusinessesNetOfCashAcquired': ('USD', annual([None, None, 1.535e9])),
                           'PaymentsToAcquireBusinessTwoNetOfCashAcquired': ('USD', annual([None, None, 13e9]))})
        self.assertEqual(row['acquisitions'], 14.535e9)
        row = self.latest({'PaymentsToAcquireBusinessesNetOfCashAcquired': ('USD', annual([None, None, 2e9])),
                           'PaymentsToAcquireBusinessTwoNetOfCashAcquired': ('USD', annual([None, None, 1e9]))})
        self.assertEqual(row['acquisitions'], 2e9)

    def test_acquisitions_extension_from_the_annual_filing_is_named(self):
        # Microsoft fiscal 2026: $1,743 million on its own extension line.
        extension = inline('msft:AcquisitionsNetOfCashAcquiredAndPurchasesOfIntangibleAndOtherAssets', 1743000000)
        row = self.latest(supplement_=supplement(extension))
        self.assertEqual(row['acquisitions'], 1743000000)
        self.assertIn('issuer extension', row['acquisitions_concept'])
        self.assertIn('[S4]', self.latest(supplement_=supplement(extension), render=True))
        # A dimensional fact or two competing extension lines decide nothing.
        dimensional = inline('msft:AcquisitionsNetOfCashAcquired', 5, **{'us-gaap__BusinessAcquisitionAxis': 'msft:XMember'})
        self.assertIsNone(self.latest(supplement_=supplement(dimensional))['acquisitions'])
        second = inline('msft:AcquisitionsOfBusinessesAndOther', 900000000)
        self.assertIsNone(self.latest(supplement_=supplement(extension, second))['acquisitions'])
        # A reported standard concept always decides.
        standard = {'PaymentsToAcquireBusinessesNetOfCashAcquired': ('USD', annual([None, None, 5e8]))}
        self.assertEqual(self.latest(standard, supplement_=supplement(extension))['acquisitions'], 5e8)

    def test_missing_share_half_is_read_from_the_equity_component_axis(self):
        # Alphabet and Meta tag repurchased shares only on an equity component.
        shares = inline('us-gaap:StockRepurchasedAndRetiredDuringPeriodShares', 40000000, unit='shares', decimals=-3,
                        **{EQUITY: 'us-gaap:CommonStockMember'})
        row = self.latest(drop=('StockRepurchasedAndRetiredDuringPeriodShares',), supplement_=supplement(shares))
        self.assertAlmostEqual(row['average_repurchase_price'], 600.0)
        self.assertEqual(row['shares_repurchased'], 40000000)
        self.assertIn('equity-component axis', row['average_repurchase_price_concept'])
        # Cash-flow payments (25e9) are never divided by program shares.
        self.assertIsNone(self.latest(drop=('StockRepurchasedAndRetiredDuringPeriodShares',))['average_repurchase_price'])

    def test_program_axis_value_pairs_with_reported_shares(self):
        # Microsoft fiscal 2026: $16,719 million for 36 million shares under one program.
        value = inline('us-gaap:StockRepurchasedDuringPeriodValue', 16719000000, **{PROGRAM: 'msft:Program2024Member'})
        extra = {'StockRepurchasedDuringPeriodShares': ('shares', annual([None, None, 36e6]))}
        row = self.latest(extra, drop=('StockRepurchasedAndRetiredDuringPeriodValue', 'StockRepurchasedAndRetiredDuringPeriodShares'),
                          supplement_=supplement(value))
        self.assertAlmostEqual(row['average_repurchase_price'], 16719 / 36)

    def test_disagreeing_equity_components_leave_the_price_blank(self):
        # JPMorgan 2025: treasury-stock cost $31,924 million, common-stock member $31,640 million.
        drop = ('StockRepurchasedAndRetiredDuringPeriodValue', 'StockRepurchasedAndRetiredDuringPeriodShares')
        facts = [inline('us-gaap:TreasuryStockValueAcquiredCostMethod', 31924000000, **{EQUITY: 'us-gaap:TreasuryStockCommonMember'}),
                 inline('us-gaap:TreasuryStockValueAcquiredCostMethod', 31640000000, **{EQUITY: 'us-gaap:CommonStockMember'}),
                 inline('us-gaap:TreasuryStockSharesAcquired', 114400000, unit='shares', decimals=-5,
                        **{EQUITY: 'us-gaap:TreasuryStockCommonMember'})]
        self.assertIsNone(self.latest(drop=drop, supplement_=supplement(*facts))['average_repurchase_price'])
        # The treasury-stock component alone holds the whole cost (Coca-Cola).
        row = self.latest(drop=drop, supplement_=supplement(facts[0], facts[2]))
        self.assertAlmostEqual(row['average_repurchase_price'], 31924 / 114.4)
        # A component of a split value is never read as the whole (Walmart's retained earnings part).
        part = inline('us-gaap:StockRepurchasedAndRetiredDuringPeriodValue', 7619000000, **{EQUITY: 'us-gaap:RetainedEarningsMember'})
        whole = inline('us-gaap:StockRepurchasedAndRetiredDuringPeriodValue', 8080000000, **{EQUITY: 'us-gaap:ParentMember'})
        self.assertIsNone(self.latest(drop=drop[:1], supplement_=supplement(part))['average_repurchase_price'])
        self.assertAlmostEqual(self.latest(drop=drop[:1], supplement_=supplement(part, whole))['average_repurchase_price'], 202.0)

    def test_coarsely_rounded_share_counts_bound_or_block_the_price(self):
        drop = ('StockRepurchasedAndRetiredDuringPeriodValue', 'StockRepurchasedAndRetiredDuringPeriodShares')
        value = inline('us-gaap:TreasuryStockValueAcquiredCostMethod', 634000000, **{EQUITY: 'us-gaap:TreasuryStockCommonMember'})
        # Coca-Cola 2025: 9.4 million shares tagged as 9 million (decimals -6).
        nine = inline('us-gaap:TreasuryStockSharesAcquired', 9000000, unit='shares', decimals=-6, **{EQUITY: 'us-gaap:CommonStockMember'})
        self.assertIsNone(self.latest(drop=drop, supplement_=supplement(value, nine))['average_repurchase_price'])
        # Meta 2025: 40 million (±1.25%) is shown, marked approximate.
        forty = inline('us-gaap:TreasuryStockSharesAcquired', 40000000, unit='shares', decimals=-6, **{EQUITY: 'us-gaap:CommonStockMember'})
        row = self.latest(drop=drop, supplement_=supplement(value, forty))
        self.assertAlmostEqual(row['average_repurchase_price_tolerance'], 0.0125)
        text = self.latest(drop=drop, supplement_=supplement(value, forty), render=True)
        self.assertIn('| ~15.85 |', text)
        self.assertIn('±1.2%', text)

    def test_a_stated_average_price_is_the_one_price_for_the_year(self):
        # Walmart fiscal 2026 states 95.13; its value and share pair gives 95.15.
        stated = {'TreasuryStockAcquiredAverageCostPerShare': ('USD/shares', annual([None, None, 601.25]))}
        row = self.latest(stated)
        self.assertEqual(row['average_repurchase_price'], 601.25)
        self.assertEqual(row['shares_repurchased'], 40e6)
        self.assertIn('stated', row['average_repurchase_price_concept'])
        self.assertIn('preferred to the ratio', row['average_repurchase_price_concept'])
        # Salesforce states its price for one repurchase program only, in the 10-K.
        program = inline('us-gaap:TreasuryStockAcquiredAverageCostPerShare', '254.21', unit='USD/shares',
                         **{PROGRAM: 'crm:ShareRepurchaseProgramMember'})
        self.assertEqual(self.latest(supplement_=supplement(program))['average_repurchase_price'], 254.21)
        other = inline('us-gaap:TreasuryStockAcquiredAverageCostPerShare', '250.00', unit='USD/shares',
                       **{PROGRAM: 'crm:OtherProgramMember'})
        self.assertAlmostEqual(self.latest(supplement_=supplement(program, other))['average_repurchase_price'], 600.0)

    def test_selling_and_general_expenses_are_summed_and_labelled(self):
        extra = {'SellingAndMarketingExpense': ('USD', annual([10e9, 11e9, 12e9])),
                 'GeneralAndAdministrativeExpense': ('USD', annual([4e9, 4e9, 4.1e9]))}
        row = self.latest(extra)
        self.assertEqual(row['selling_general_administrative'], 16.1e9)
        self.assertIn('derived', row['selling_general_administrative_concept'])
        self.assertIn('SG&A column: derived', self.latest(extra, render=True))
        # Amazon tags its sales and marketing line as MarketingExpense.
        extra['MarketingExpense'] = extra.pop('SellingAndMarketingExpense')
        self.assertEqual(self.latest(extra)['selling_general_administrative'], 16.1e9)
        # A reported SG&A total decides; one half alone is not SG&A.
        self.assertEqual(self.latest(dict(extra, SellingGeneralAndAdministrativeExpense=('USD', annual([1, 1, 2e9]))))
                         ['selling_general_administrative'], 2e9)
        self.assertIsNone(self.latest({'GeneralAndAdministrativeExpense': extra['GeneralAndAdministrativeExpense']})
                          ['selling_general_administrative'])

    def test_the_largest_reported_depreciation_total_is_used_and_labelled(self):
        # Salesforce tags a $1.2 billion note figure with the first total concept.
        extra = {'DepreciationDepletionAndAmortization': ('USD', annual([None, None, 1.2e9])),
                 'DepreciationAndAmortization': ('USD', annual([None, None, 3.631e9])),
                 'Depreciation': ('USD', annual([1e9, 1.1e9, 1.15e9]))}
        row = self.latest(extra)
        self.assertEqual(row['depreciation_amortization'], 3.631e9)
        self.assertEqual(row['depreciation_amortization_concept'], 'DepreciationAndAmortization')
        # Depreciation alone is used only without a total, and the header says so.
        only = {'Depreciation': extra['Depreciation']}
        self.assertTrue(self.latest(only)['depreciation_only'])
        self.assertIn('| depreciation only/rev |', self.latest(only, render=True))
        # Microsoft: the cash-flow total is an extension, larger than depreciation alone.
        total = inline('msft:DepreciationAmortizationAndOther', 1.4e9)
        row = self.latest(only, supplement_=supplement(total))
        self.assertEqual(row['depreciation_amortization'], 1.4e9)
        self.assertFalse(row['depreciation_only'])
        text = self.latest(only, supplement_=supplement(total), render=True)
        self.assertIn('| D&A/rev |', text)
        self.assertIn('D&A column: Depreciation for FY ending 2024-09-28 [S2]; msft:DepreciationAmortizationAndOther', text)

    def test_cover_page_shares_fill_a_missing_year_end_count_with_their_date(self):
        # Coca-Cola and Walmart report outstanding shares only on the 10-K cover.
        concepts = dict(BASE)
        concepts.pop('CommonStockSharesOutstanding')
        data = payload(**concepts)
        data['facts']['dei'] = {'EntityCommonStockSharesOutstanding': {'units': {'shares': [
            fact(None, '2024-11-15', 975e6, '2024-11-20'), fact(None, '2025-11-14', 941e6, '2025-11-20'),
            fact(None, '2026-01-20', 939e6, '2026-01-30', form='10-Q')]}}}
        baseline = historical_baseline(data, years=2)
        first, latest = baseline['rows']
        self.assertEqual(latest['shares_outstanding'], 941e6)
        self.assertEqual(latest['shares_outstanding_as_of'], '2025-11-14')
        self.assertEqual(latest['net_share_change'], -34e6)
        self.assertIn('941.0 (cover 2025-11-14)', render_baseline(baseline, 'S2'))
        # A cover count and a year-end count are not differenced.
        data['facts']['us-gaap']['CommonStockSharesOutstanding'] = {'units': {'shares': instants([None, 970e6, None])}}
        self.assertIsNone(historical_baseline(data, years=2)['rows'][-1]['net_share_change'])

    def test_year_end_shares_by_equity_component_or_class_come_from_the_annual_filing(self):
        # Meta tags its outstanding count only by share class and equity component.
        classes = [inline('us-gaap:CommonStockSharesOutstanding', 700e6, unit='shares', start=None,
                          **{'us-gaap__StatementClassOfStockAxis': 'us-gaap:CommonClassAMember'}),
                   inline('us-gaap:CommonStockSharesOutstanding', 240e6, unit='shares', start=None,
                          **{'us-gaap__StatementClassOfStockAxis': 'us-gaap:CommonClassBMember'})]
        row = self.latest(drop=('CommonStockSharesOutstanding',), supplement_=supplement(*classes))
        self.assertEqual(row['shares_outstanding'], 940e6)
        self.assertEqual(row['shares_outstanding_as_of'], END)

    def test_finance_lease_additions_and_unstarted_leases_reach_the_history(self):
        additions = {'RightOfUseAssetObtainedInExchangeForFinanceLeaseLiability': ('USD', annual([1e9, 2e9, 3e9]))}
        # Microsoft tags one $329.1 billion amount on both lease-term members.
        obligation = 'us-gaap:UnrecordedUnconditionalPurchaseObligationBalanceSheetAmount'
        axis = 'us-gaap__LeaseContractualTermAxis'
        facts = [inline(obligation, 329.1e9, start=None, **{axis: 'msft:OperatingLeaseMember'}),
                 inline(obligation, 329.1e9, start=None, **{axis: 'msft:FinanceLeaseMember'})]
        row = self.latest(additions, supplement_=supplement(*facts))
        self.assertEqual(row['finance_lease_additions'], 3e9)
        self.assertEqual(row['lease_commitments_not_commenced'], 329.1e9)
        text = self.latest(additions, supplement_=supplement(*facts), render=True)
        # The latest fiscal year's row, wherever basis notes fall after it.
        self.assertIn('| 3.0', next(line for line in reversed(text.splitlines()) if line.startswith('20')))
        self.assertIn('Leases signed but not yet commenced at 2025-09-27: 329.1 billion', text)
        # Alphabet: current plus noncurrent extension amounts.
        split = [inline('goog:LesseeLeaseNotYetCommencedCurrentAmount', 5.8e9, start=None),
                 inline('goog:LesseeLeaseNotYetCommencedNoncurrentAmount', 52.7e9, start=None)]
        self.assertEqual(self.latest(supplement_=supplement(*split))['lease_commitments_not_commenced'], 58.5e9)
        # Amazon: the purchase-obligation total on a not-yet-commenced category member.
        category = inline(obligation, 96.373e9, start=None, **{
            'us-gaap__UnrecordedUnconditionalPurchaseObligationByCategoryOfItemPurchasedAxis': 'amzn:LeasesNotYetCommencedMember'})
        self.assertEqual(self.latest(supplement_=supplement(category))['lease_commitments_not_commenced'], 96.373e9)
        # Different amounts on the lease-term members, or no supplement, give nothing.
        facts[1] = inline(obligation, 1e9, start=None, **{axis: 'msft:FinanceLeaseMember'})
        self.assertIsNone(self.latest(supplement_=supplement(*facts))['lease_commitments_not_commenced'])
        self.assertIsNone(self.latest()['lease_commitments_not_commenced'])
        self.assertIsNone(self.latest()['finance_lease_additions'])

    def test_total_revenues_above_narrower_contract_revenue_are_the_history_basis(self):
        # Walmart fiscal 2026: net sales $706.4 billion, total revenues $713.2 billion.
        row = self.latest({'Revenues': ('USD', annual([101e9, 111e9, 122.2e9]))})
        self.assertEqual(row['revenue'], 122.2e9)
        self.assertEqual(row['revenue_concept'], 'Revenues')
        # A smaller Revenues figure is not a broader total.
        self.assertEqual(self.latest({'Revenues': ('USD', annual([99e9, 109e9, 120e9]))})['revenue'], 121e9)

    def test_supplement_filed_after_the_cutoff_is_ignored(self):
        extension = inline('msft:AcquisitionsNetOfCashAcquired', 1e9)
        baseline = historical_baseline(payload(**BASE), years=2, as_of='2025-12-01',
                                       supplement=supplement(extension, filed='2026-01-15'))
        self.assertIsNone(baseline['rows'][-1]['acquisitions'])


class SplitDetectionTests(unittest.TestCase):
    def test_scale_corrections_are_not_splits(self):
        # Coca-Cola restated weighted shares a million-fold (millions retagged as units).
        shares = {'units': {'shares': [fact(*YEARS[1], 4300, '2024-11-01'), fact(*YEARS[1], 4300e6, '2025-11-01')]}}
        self.assertIsNone(_split_date({'WeightedAverageNumberOfDilutedSharesOutstanding': shares}))
        shares['units']['shares'].append(fact(*YEARS[1], 43000e6, '2026-02-01', form='10-Q'))
        self.assertEqual(_split_date({'WeightedAverageNumberOfDilutedSharesOutstanding': shares}), '2026-02-01')

    def test_inconsistent_per_share_restatements_are_not_a_split(self):
        # Salesforce's 2019 10-K restated three years of EPS by 1.8, 2.8 and 3.1 times.
        def eps(values):
            rows = [fact(*period, value, '2024-11-01') for period, (value, _) in zip(YEARS, values)]
            return {'units': {'USD/shares': rows + [fact(*period, value * factor, '2025-11-01')
                                                    for period, (value, factor) in zip(YEARS, values)]}}
        uneven = [(1.0, 1.8), (1.0, 2.8), (1.0, 3.1)]
        self.assertIsNone(_split_date({'EarningsPerShareDiluted': eps(uneven), 'EarningsPerShareBasic': eps(uneven)}))
        even = [(1.0, 0.05), (1.0, 0.05), (1.0, 0.05)]
        self.assertEqual(_split_date({'EarningsPerShareDiluted': eps(even)}), '2025-11-01')


class InlineSupplementParsingTests(unittest.TestCase):
    DOCUMENT = '''<html xmlns:us-gaap="http://fasb.org/us-gaap/2026" xmlns:msft="http://microsoft.com/20260630">
<xbrli:context id="fy"><xbrli:entity><xbrli:identifier>789019</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2025-07-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="fy_component"><xbrli:entity><xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementEquityComponentsAxis">us-gaap:CommonStockMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2025-07-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="fy_segment"><xbrli:entity><xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">msft:CloudMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2025-07-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
<xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>
<ix:nonFraction name="msft:DepreciationAmortizationAndOther" contextRef="fy" unitRef="usd" scale="6" decimals="-6">38,534</ix:nonFraction>
<ix:nonFraction name="msft:DepreciationAmortizationAndOther" contextRef="fy_segment" unitRef="usd" scale="6" decimals="-6">9,000</ix:nonFraction>
<ix:nonFraction name="us-gaap:StockRepurchasedDuringPeriodShares" contextRef="fy_component" unitRef="shares" scale="6" decimals="-6">36</ix:nonFraction>
<ix:nonFraction name="us-gaap:Revenues" contextRef="fy" unitRef="usd" scale="6">331,839</ix:nonFraction>
</html>'''

    def test_only_selected_extension_and_axis_facts_are_kept_as_exact_decimals(self):
        result = filing_supplement(self.DOCUMENT, source_id='S4', filing_date='2026-07-29', form='10-K', report_date='2026-06-30')
        by_concept = {fact['concept']: fact for fact in result['facts']}
        self.assertEqual(set(by_concept), {'msft:DepreciationAmortizationAndOther', 'us-gaap:StockRepurchasedDuringPeriodShares'})
        self.assertEqual(by_concept['msft:DepreciationAmortizationAndOther']['value'], '38534000000')
        self.assertEqual(by_concept['msft:DepreciationAmortizationAndOther']['dimensions'], {})
        shares = by_concept['us-gaap:StockRepurchasedDuringPeriodShares']
        self.assertEqual((shares['unit'], shares['decimals']), ('shares', -6))
        self.assertEqual(shares['dimensions'], {'us-gaap:StatementEquityComponentsAxis': 'us-gaap:CommonStockMember'})

    def test_the_latest_checksummed_annual_archive_inside_the_folder_is_read(self):
        import hashlib
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / 'sources_raw'
            archive.mkdir()
            (archive / 'S4.html').write_text(self.DOCUMENT, encoding='utf-8')
            digest = hashlib.sha256(self.DOCUMENT.encode()).hexdigest()
            def source(sid, form, filed, sha=digest):
                return {'id': sid, 'kind': 'sec_filing', 'archive': 'sources_raw/S4.html', 'archive_sha256': sha,
                        'data': {'metadata': {'form': form, 'filingDate': filed, 'reportDate': '2026-06-30'}}}
            found = filing_supplement_for(folder, [source('S5', '10-Q', '2026-10-28'), source('S4', '10-K', '2026-07-29')])
            self.assertEqual((found['source_id'], found['report_date']), ('S4', '2026-06-30'))
            self.assertIsNone(filing_supplement_for(folder, [source('S4', '10-K', '2026-07-29', sha='0' * 64)]))
            self.assertIsNone(filing_supplement_for(folder, [source('S4', '10-K', '2026-07-29')], as_of='2026-07-01'))
            escaped = dict(source('S4', '10-K', '2026-07-29'), archive='../outside.html')
            self.assertIsNone(filing_supplement_for(folder, [escaped]))

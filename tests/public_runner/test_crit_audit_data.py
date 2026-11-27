"""Data and mapping fixes from the four computed-text audits of 2026-11-27 (KO, GOOGL, MSFT, AAPL).

Each case reproduces the issuer's reported pattern: pension balances tagged only by
plan type, reported securities left outside the bridge, an issuer's own region
definitions, member names, history-table bases and the ROIC notes.
"""
import json
from pathlib import Path
import unittest

from investmentideas import country_risk, evidence_tables as et, roic
from investmentideas.inputs import PLAN_LEVEL_NOT_READ, canonical_reported_inputs
from investmentideas.research import inline_facts

from test_crit_bridge import END, LEASES, TERM, facts_source

M = 1000000
PENSION = 'us-gaap:PensionPlansDefinedBenefitMember'
RETIREE = 'us-gaap:OtherPostretirementBenefitPlansDefinedBenefitMember'


def plan_fact(concept, member, value, decimals='-6'):
    return {'concept': 'us-gaap:' + concept, 'normalized_value': value, 'normalized_decimal': str(value), 'context_ref': member,
            'instant': END, 'start_date': None, 'end_date': None, 'unit': 'iso4217:USD', 'entity_identifier': '123456',
            'parse_status': 'parsed', 'is_nil': False, 'decimals': decimals,
            'dimensions': {'us-gaap:RetirementPlanTypeAxis': member}, 'dimension_filter': 'defined_benefit_plan_type'}


def with_plans(balance, *plans):
    sources = [facts_source(balance)]
    sources.append({'id': 'S4', 'kind': 'regulatory_primary', 'data': {
        'metadata': {'cik': 123456, 'form': '10-K', 'filingDate': '2026-07-29'}, 'inline_facts': list(plans)}})
    return canonical_reported_inputs(sources, as_of='2026-11-06')


KO_PLANS = (plan_fact('PensionAndOtherPostretirementDefinedBenefitPlansLiabilitiesNoncurrent', PENSION, 681 * M),
            plan_fact('PensionAndOtherPostretirementDefinedBenefitPlansCurrentLiabilities', PENSION, 70 * M),
            plan_fact('PensionAndOtherPostretirementDefinedBenefitPlansLiabilitiesNoncurrent', RETIREE, 104 * M),
            plan_fact('PensionAndOtherPostretirementDefinedBenefitPlansCurrentLiabilities', RETIREE, 8 * M),
            plan_fact('DefinedBenefitPlanAssetsForPlanBenefitsNoncurrent', PENSION, 1046 * M),
            plan_fact('DefinedBenefitPlanFundedStatusOfPlan', PENSION, 295 * M),
            plan_fact('DefinedBenefitPlanFundedStatusOfPlan', RETIREE, -112 * M))


class PlanTypePensionTests(unittest.TestCase):
    def test_balances_tagged_only_by_plan_type_are_deducted_gross(self):
        # Coca-Cola: $751 million of pension and $112 million of retiree-health deficits,
        # with $1,046 million of assets in overfunded pension plans that are not credited.
        result = with_plans({**TERM, **LEASES}, *KO_PLANS)
        benefits = result['other_claims_bridge']['components']['defined_benefit_obligations']
        self.assertEqual(benefits['group'], 'plan_type_axis')
        self.assertEqual(benefits['value'], 863 * M)
        self.assertEqual((benefits['overfunded_plan_assets'], benefits['overfunded_plan_assets_credited']), (1046 * M, 0))
        self.assertTrue(all(check['reconciled'] for check in benefits['plan_type_reconciliation']))
        self.assertEqual(result['actuals']['other_equity_claims'], 15 + 863 * M)

    def test_plan_type_values_replace_a_whole_company_reading_that_does_not_match(self):
        # Johnson & Johnson: the whole-company retiree-health tag ($1,774 million) is not the
        # deficit; the plan-type liability and the funded status are both $4,039 million.
        plans = (plan_fact('PensionAndOtherPostretirementDefinedBenefitPlansLiabilitiesNoncurrent', PENSION, 2773 * M),
                 plan_fact('PensionAndOtherPostretirementDefinedBenefitPlansCurrentLiabilities', PENSION, 144 * M),
                 plan_fact('PensionAndOtherPostretirementDefinedBenefitPlansLiabilitiesNoncurrent', RETIREE, 3607 * M),
                 plan_fact('PensionAndOtherPostretirementDefinedBenefitPlansCurrentLiabilities', RETIREE, 432 * M),
                 plan_fact('DefinedBenefitPlanAssetsForPlanBenefitsNoncurrent', PENSION, 7331 * M),
                 plan_fact('DefinedBenefitPlanFundedStatusOfPlan', PENSION, 4414 * M),
                 plan_fact('DefinedBenefitPlanFundedStatusOfPlan', RETIREE, -4039 * M))
        balance = {**TERM, **LEASES, 'DefinedBenefitPensionPlanCurrentAndNoncurrentLiabilities': 2917 * M,
                   'OtherPostretirementDefinedBenefitPlanLiabilitiesCurrentAndNoncurrent': 1774 * M}
        benefits = with_plans(balance, *plans)['other_claims_bridge']['components']['defined_benefit_obligations']
        self.assertEqual(benefits['value'], (2917 + 4039) * M)
        self.assertEqual(benefits['whole_company_reading_not_used']['recognized_liabilities'], (2917 + 1774) * M)
        # Without a funded status to reconcile to, the whole-company reading stands.
        unreconciled = [fact for fact in plans if 'FundedStatus' not in fact['concept']]
        kept = with_plans(balance, *unreconciled)['other_claims_bridge']['components']['defined_benefit_obligations']
        self.assertEqual(kept['value'], (2917 + 1774) * M)

    def test_plan_type_values_replace_a_postemployment_fallback(self):
        # AT&T: $9,113 million of postemployment obligations; the defined-benefit deficits
        # by plan type are $7,705 million and match the funded status.
        plans = (plan_fact('PensionAndOtherPostretirementDefinedBenefitPlansLiabilitiesCurrentAndNoncurrent', PENSION, 1950 * M),
                 plan_fact('PensionAndOtherPostretirementDefinedBenefitPlansLiabilitiesCurrentAndNoncurrent', RETIREE, 5755 * M),
                 plan_fact('DefinedBenefitPlanFundedStatusOfPlan', PENSION, -1950 * M),
                 plan_fact('DefinedBenefitPlanFundedStatusOfPlan', RETIREE, -5755 * M))
        balance = {**TERM, **LEASES, 'PostemploymentBenefitsLiabilityNoncurrent': 8478 * M,
                   'PostemploymentBenefitsLiabilityCurrent': 635 * M}
        benefits = with_plans(balance, *plans)['other_claims_bridge']['components']['defined_benefit_obligations']
        self.assertEqual((benefits['group'], benefits['value']), ('plan_type_axis', 7705 * M))

    def test_an_unread_obligation_is_zero_by_assumption_not_reported_absent(self):
        result = with_plans({**TERM, **LEASES})
        absent = {entry['line']: entry['basis'] for entry in result['other_claims_bridge']['components_assumed_zero']}
        self.assertEqual(absent['defined_benefit_obligations'], PLAN_LEVEL_NOT_READ)
        self.assertIn("plan-level values were not read", PLAN_LEVEL_NOT_READ)

    def test_inline_facts_keep_plan_type_balances_outside_the_fact_limit(self):
        contexts, facts = [], []
        for index in range(120):  # more nondimensional facts than the limit
            facts.append(f'<ix:nonFraction name="us-gaap:Revenues" contextRef="d{index}" unitRef="usd" scale="6">{index + 1}</ix:nonFraction>')
            contexts.append(f'<xbrli:context id="d{index}"><xbrli:entity><xbrli:identifier>21344</xbrli:identifier></xbrli:entity>'
                            f'<xbrli:period><xbrli:instant>2025-12-{index % 28 + 1:02d}</xbrli:instant></xbrli:period></xbrli:context>')
        for name, member in (('pension', PENSION), ('retiree', RETIREE), ('qualified', 'us-gaap:QualifiedPlanMember')):
            contexts.append(f'<xbrli:context id="{name}"><xbrli:entity><xbrli:identifier>21344</xbrli:identifier><xbrli:segment>'
                            f'<xbrldi:explicitMember dimension="us-gaap:RetirementPlanTypeAxis">{member}</xbrldi:explicitMember>'
                            '</xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period>'
                            '</xbrli:context>')
            facts.append(f'<ix:nonFraction name="us-gaap:PensionAndOtherPostretirementDefinedBenefitPlansLiabilitiesNoncurrent" '
                         f'contextRef="{name}" unitRef="usd" scale="6" decimals="-6">681</ix:nonFraction>')
        document = ('<html xmlns:us-gaap="http://fasb.org/us-gaap/2025">' + ''.join(contexts) +
                    '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>' + ''.join(facts) + '</html>')
        result = inline_facts(document)
        plans = [fact for fact in result if fact['dimension_filter'] == 'defined_benefit_plan_type']
        self.assertEqual(len(result) - len(plans), 100)
        self.assertEqual(sorted(fact['dimensions']['us-gaap:RetirementPlanTypeAxis'] for fact in plans), sorted([PENSION, RETIREE]))


class ExcludedCashCategoryTests(unittest.TestCase):
    def test_reported_categories_outside_the_selected_cash_carry_their_amounts(self):
        # Coca-Cola: marketable securities and available-for-sale debt securities beside a
        # cash and short-term investments total that is cash plus other short-term investments.
        balance = {'CashCashEquivalentsAndShortTermInvestments': 13872 * M, 'OtherShortTermInvestments': 3602 * M,
                   'MarketableSecurities': 1934 * M, 'AvailableForSaleSecuritiesDebtSecurities': 1774 * M, **TERM, **LEASES}
        coverage = canonical_reported_inputs([facts_source(balance)], as_of='2026-11-06')['net_debt_bridge']['cash_coverage']
        excluded = {entry['category']: entry for entry in coverage['excluded_reported_categories']}
        self.assertEqual({name: entry['value'] for name, entry in excluded.items()},
                         {'marketable_securities': 1934 * M, 'available_for_sale_debt_securities': 1774 * M})
        self.assertTrue(all(entry['treatment'] == 'excluded by house policy' for entry in excluded.values()))
        # Cash inside the selected total is not listed as excluded.
        self.assertNotIn('cash_and_cash_equivalents', excluded)


class CountryRiskDefinitionTests(unittest.TestCase):
    def test_apple_definitions_replace_plain_meanings(self):
        europe = country_risk.mapping_for('aapl:EuropeSegmentMember', '320193')
        self.assertIn('India', europe['countries'])
        self.assertIn('Middle East', europe['regions'])
        self.assertEqual(country_risk.mapping_for('aapl:EuropeSegmentMember')['regions'], country_risk.EUROPE)
        china = country_risk.mapping_for('country:CN', '320193')
        self.assertEqual(set(china['countries']), {'China', 'Hong Kong', 'Taiwan'})
        self.assertEqual(country_risk._label('country:CN', '320193'), 'China, including Hong Kong and Taiwan')
        self.assertEqual(country_risk.issuer_cik([{'breakdown_tables': {'segment': {'members': [
            {'member': 'aapl:EuropeSegmentMember'}]}}}]), '320193')

    def test_emea_includes_eastern_europe_and_russia(self):
        self.assertIn('Eastern Europe & Russia', country_risk.mapping_for('us-gaap:EMEAMember')['regions'])


class MemberLabelTests(unittest.TestCase):
    def test_filing_labels_brand_names_and_connectors(self):
        cases = {'aapl:WearablesHomeandAccessoriesMember': 'Wearables, Home and Accessories',
                 'goog:GoogleSearchOtherMember within goog:GoogleServicesMember': 'Google Search & other in Google Services',
                 'goog:YouTubeAdvertisingRevenueMember': 'YouTube ads',
                 'msft:LinkedInCorporationMember': 'LinkedIn Corporation',
                 'msft:WindowsAndDevicesMember': 'Windows and Devices',
                 'msft:MicrosoftThreeSixFiveCommercialProductsAndCloudServicesMember':
                     'Microsoft 365 Commercial Products and Cloud Services',
                 'x:HomeandGardenMember': 'Home and Garden', 'x:IslandResortsMember': 'Island Resorts',
                 'x:ThailandOperationsMember': 'Thailand Operations', 'us-gaap:NonUsMember': 'Non-US',
                 'country:JP': 'Japan'}
        for member, label in cases.items():
            self.assertEqual(et._member_label(member), label, member)
        self.assertEqual(et._member_label('country:CN', ('aapl',)), 'China, including Hong Kong and Taiwan')

    def test_saved_tables_render_the_current_labels(self):
        tables = {'product_or_service': {'fiscal_year_ends': ['2026-09-26'], 'members': [
            {'member': 'aapl:WearablesHomeandAccessoriesMember', 'label': 'Wearables Homeand Accessories',
             'revenue': [35.6e9], 'revenue_growth': [None]}]}}
        lines, _ = et.breakdown_markdown(tables, 'S4')
        self.assertTrue(any('| Wearables, Home and Accessories |' in line for line in lines))


def history_rows():
    rows = []
    for year, only in ((2022, True), (2023, True), (2024, False), (2025, False), (2026, False)):
        rows.append({'fiscal_year_start': f'{year - 1}-07-01', 'fiscal_year_end': f'{year}-06-30', 'currency': 'USD',
                     'revenue': 100e9, 'depreciation_amortization': 6e9, 'depreciation_only': only,
                     'depreciation_amortization_concept': 'us-gaap:Depreciation' if only else 'msft:DepreciationAmortizationAndOther (issuer extension in the annual filing)',
                     'depreciation_amortization_to_revenue': .06, 'acquisitions': None if year < 2024 else 1e9,
                     'acquisitions_concept': None if year < 2024 else 'msft:Acquisitions (issuer extension in the annual filing)',
                     'average_repurchase_price': None if year < 2024 else 400.0, 'repurchases': 20e9,
                     'shares_repurchased': 40e6 if year >= 2024 else None,
                     'average_repurchase_price_concept': None if year < 2024 else 'StockRepurchasedDuringPeriodValue / StockRepurchasedDuringPeriodShares; value sum over the repurchase-program axis in the annual filing'})
    return rows


class HistoryBasisTests(unittest.TestCase):
    def test_depreciation_only_cells_are_marked_and_the_phrase_follows_the_inputs(self):
        rows = history_rows()
        baseline = {'rows': [dict(row, **{key: None for key in (
            'revenue_growth', 'gross_margin', 'operating_margin', 'research_and_development_to_revenue',
            'selling_general_administrative_to_revenue', 'effective_tax_rate', 'capital_expenditure_to_revenue',
            'stock_compensation_to_revenue', 'dividends', 'diluted_eps')}) for row in rows]}
        text = et.render_baseline(baseline, 'S2', include_roic=False)
        self.assertIn('6.0% (depreciation only)', text)
        self.assertEqual(text.count('(depreciation only)'), 2)
        self.assertIn('depreciation only for the fiscal years ending 2022-06-30, 2023-06-30', et.depreciation_phrase(rows))
        only = [dict(row, depreciation_only=True) for row in rows]
        self.assertTrue(et.depreciation_phrase(only).startswith('depreciation (the filings report no'))

    def test_years_outside_the_scanned_filing_are_marked_not_unreported(self):
        rows = history_rows()
        supplement = {'facts': [{'start': f'{year - 1}-07-01', 'end': f'{year}-06-30'} for year in (2024, 2025, 2026)]}
        et._mark_outside_supplement(rows, supplement)
        self.assertEqual(rows[0]['outside_filings_scanned'], ['acquisitions', 'average_repurchase_price'])
        self.assertNotIn('outside_filings_scanned', rows[3])
        notes = et._basis_notes(rows, 'S2', 'S4')
        self.assertTrue(any(note.startswith('Acquisitions for the fiscal years ending 2022-06-30, 2023-06-30 is shown as '
                                            'outside filings scanned') for note in notes))

    def test_buyback_price_basis_is_named_where_it_differs_from_cash(self):
        label, differing = et.repurchase_price_basis(history_rows())
        self.assertEqual(label, 'program value / program shares')
        self.assertEqual([item[0] for item in differing], ['2024-06-30', '2025-06-30', '2026-06-30'])  # 400 against 500

    def test_segment_margin_caution_states_no_sum_and_no_cause(self):
        # A live Coca-Cola review rejected a note that summed segment rows and named a cause.
        table = {'fiscal_year_ends': ['2026-09-26'], 'members': [
            {'member': 'aapl:AmericasSegmentMember', 'operating_income': [100e9]},
            {'member': 'aapl:EuropeSegmentMember', 'operating_income': [112.5e9]}]}
        note = et.segment_operating_note(table, {'2026-09-26': 158.1e9})
        self.assertIn('not comparable with the consolidated operating margin', note)
        self.assertNotIn('sums to', note)
        self.assertNotIn('$', note)
        self.assertIsNone(et.segment_operating_note({'fiscal_year_ends': ['2026-09-26'], 'members': [
            {'member': 'aapl:AmericasSegmentMember', 'operating_income': [None]}]}))


class RoicNoteTests(unittest.TestCase):
    def test_the_nopat_note_says_which_rate_ran_and_omits_untagged_lease_interest(self):
        rows = [{'fiscal_year_end': '2025-09-27', 'nopat': 1.0, 'nopat_tax_basis': 'cash', 'operating_lease_interest': 0.0},
                {'fiscal_year_end': '2026-09-26', 'nopat': 1.0, 'nopat_tax_basis': 'effective', 'operating_lease_interest': 0.0}]
        note = et._nopat_note(rows)
        self.assertIn('cash tax rate (provision less deferred tax expense, over pretax income) for the fiscal years ending 2025-09-27', note)
        self.assertIn('effective tax rate', note)
        self.assertIn('taken as zero', note)
        self.assertNotIn('weighted-average discount rate.', note)
        rows[0]['operating_lease_interest'] = .1
        self.assertIn('no rate is tagged for the fiscal years ending 2026-09-26', et._nopat_note(rows))

    def test_deferred_tax_is_read_from_its_parts_where_no_total_is_tagged(self):
        # Apple tags federal, state and foreign deferred tax, and no total.
        def duration(value):
            return {'units': {'USD': [{'start': '2025-09-28', 'end': '2026-09-26', 'val': value, 'filed': '2026-11-03',
                                       'form': '10-K', 'accn': '0000320193-26-000026', 'fy': 2026, 'fp': 'FY'}]}}
        gaap = {'DeferredFederalIncomeTaxExpenseBenefit': duration(594e6),
                'DeferredStateAndLocalIncomeTaxExpenseBenefit': duration(-36e6),
                'DeferredForeignIncomeTaxExpenseBenefit': duration(163e6)}
        row = et._roic_inputs(gaap, {'start': '2025-09-28', 'end': '2026-09-26', 'currency': 'USD', 'revenue': 1.0}, None)
        self.assertAlmostEqual(row['deferred_tax_expense'], 721e6)
        self.assertTrue(row['deferred_tax_basis'].startswith('sum of'))


if __name__ == '__main__':
    unittest.main()

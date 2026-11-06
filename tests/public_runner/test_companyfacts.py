from datetime import date, timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from investmentideas.companyfacts import normalize_companyfacts
from investmentideas.inputs import canonical_reported_inputs

IDENTITY = {'cik_str': 123456, 'ticker': 'TEST', 'title': 'Test Issuer'}
FIXTURES = Path(__file__).with_name('fixtures')


def row(value, end='2026-06-30', *, start=None, filed='2026-07-29', form='10-K', accn='0001193125-26-123456'):
    result = {'val': value, 'end': end, 'filed': filed, 'form': form, 'accn': accn, 'fy': 2026, 'fp': 'FY'}
    if start is not None:
        result['start'] = start
    return result


def payload():
    tags = {}
    for year, revenue in ((2024, 800), (2025, 900), (2026, 1000)):
        end, filed = f'{year}-06-30', f'{year}-07-29'
        for tag, value in (('RevenueFromContractWithCustomerExcludingAssessedTax', revenue),
                           ('CashAndCashEquivalentsAtCarryingValue', 10),
                           ('DebtLongtermAndShorttermCombinedAmount', 100), ('FinanceLeaseLiability', 0)):
            tags.setdefault(tag, {'units': {'USD': []}})['units']['USD'].append(
                row(value, end, start=f'{year - 1}-07-01' if tag.startswith('Revenue') else None,
                    filed=filed, accn=f'0001193125-{str(year)[-2:]}-123456'))
    return {'cik': 123456, 'entityName': 'Test Issuer', 'facts': {'us-gaap': tags, 'dei': {
        'EntityCommonStockSharesOutstanding': {'units': {'shares': [row(500, '2026-07-20')]}}}}}


def normalize(data=None, **kwargs):
    return normalize_companyfacts(data or payload(), identity=IDENTITY, as_of='2026-11-06', **kwargs)


class CompanyfactsTests(unittest.TestCase):
    def test_structured_origin_and_record_provenance_are_explicit(self):
        source = normalize(source_id='S7')
        self.assertEqual(source['id'], 'S7')
        self.assertEqual(source['source_tier'], 'regulatory_primary')
        self.assertNotIn('inline_facts', source['data'])
        self.assertNotIn('filingDate', source['data']['metadata'])
        fact = next(f for f in source['data']['reported_facts'] if f['concept'].endswith('DebtLongtermAndShorttermCombinedAmount'))
        self.assertEqual(fact['fact_origin'], 'sec_companyfacts')
        self.assertNotIn('parse_status', fact)
        self.assertIsNone(fact['context_ref'])
        self.assertEqual(fact['original_value'], 100)
        self.assertEqual(fact['original_unit'], 'USD')
        self.assertEqual(fact['source_record']['val'], 100)
        self.assertEqual(fact['accession_number'], '0001193125-24-123456')
        report = canonical_reported_inputs([source], as_of='2026-11-06')
        self.assertTrue(report['coverage']['complete'])
        self.assertEqual(report['actuals']['net_debt'], 90)
        evidence = report['net_debt_bridge']['debt_components']['financial_debt']['facts'][0]
        self.assertEqual(evidence['fact_origin'], 'sec_companyfacts')
        self.assertEqual(evidence['filing_date'], '2026-07-29')
        self.assertEqual(evidence['source_record']['val'], 100)

    def test_recorded_msft_and_coreweave_keep_real_values_and_honest_partial_coverage(self):
        expected = {'msft': (789019, 331839000000, 66594000000),
                    'crwv': (1769628, 5131000000, 254000000)}
        for name, (cik, revenue, leases) in expected.items():
            data = json.loads((FIXTURES / f'companyfacts_{name}_recorded.json').read_text())
            source = normalize_companyfacts(data, identity={'cik_str': cik, 'ticker': name.upper(), 'title': data['entityName']},
                                            source_id='recorded-' + name, as_of='2026-11-06')
            report = canonical_reported_inputs([source], as_of='2026-11-06')
            with self.subTest(issuer=name):
                self.assertEqual(report['actuals']['revenue'], revenue)
                self.assertEqual(report['actuals']['other_equity_claims'], leases)
                self.assertEqual(report['coverage']['missing_fields'], ['net_debt', 'shares_outstanding'])
                self.assertFalse(report['coverage']['complete'])
                self.assertEqual(len(source['data']['selection']['annual_periods']), 3)
                self.assertFalse(any(f['concept'].endswith('ShortTermBorrowings') for f in source['data']['reported_facts']))
                self.assertTrue(all(f['fact_origin'] == 'sec_companyfacts' for f in source['data']['reported_facts']))

    def test_newer_quarters_do_not_evict_annual_anchor_or_same_date_bridge(self):
        data = payload()
        tags = data['facts']['us-gaap']
        for offset in range(45):
            end = date(2026, 9, 1) + timedelta(days=offset)
            filed = (end + timedelta(days=1)).isoformat()
            tags['RevenueFromContractWithCustomerExcludingAssessedTax']['units']['USD'].append(
                row(300 + offset, end.isoformat(), start=(end - timedelta(days=89)).isoformat(), filed=filed, form='10-Q'))
            tags['DebtLongtermAndShorttermCombinedAmount']['units']['USD'].append(
                row(200 + offset, end.isoformat(), filed=filed, form='10-Q'))
        source = normalize(data)
        report = canonical_reported_inputs([source], as_of='2026-11-06')
        self.assertEqual(report['actuals']['revenue'], 1000)
        self.assertEqual(report['actuals']['net_debt'], 90)
        self.assertEqual(report['input_dates']['net_debt']['instant'], '2026-06-30')
        self.assertEqual(len(source['data']['selection']['annual_periods']), 3)

    def test_historical_asof_uses_record_filed_dates_not_source_latest_filing(self):
        data = payload()
        data['facts']['us-gaap']['RevenueFromContractWithCustomerExcludingAssessedTax']['units']['USD'].append(
            row(999, '2025-06-30', start='2024-07-01', filed='2026-07-29'))
        source = normalize(data)
        source['data']['metadata']['filingDate'] = '2026-11-06'
        report = canonical_reported_inputs([source], as_of='2025-11-06')
        self.assertEqual(report['actuals']['revenue'], 900)
        self.assertEqual(report['actuals']['net_debt'], 90)
        self.assertEqual(report['input_dates']['revenue']['end_date'], '2025-06-30')
        older = normalize_companyfacts(data, identity=IDENTITY, as_of='2025-11-06')
        self.assertTrue(all(f['filing_date'] <= '2025-11-06' for f in older['data']['reported_facts']))

    def test_latest_cover_shares_remain_distinct_from_annual_dates_and_averages(self):
        data = payload()
        data['facts']['dei']['EntityCommonStockSharesOutstanding']['units']['shares'].append(
            row(480, '2026-10-23', filed='2026-10-24', form='10-Q'))
        data['facts']['us-gaap']['WeightedAverageNumberOfDilutedSharesOutstanding'] = {'units': {'shares': [row(999)]}}
        source = normalize(data)
        report = canonical_reported_inputs([source], as_of='2026-11-06')
        self.assertEqual(report['actuals']['shares_outstanding'], 480)
        self.assertEqual(report['input_dates']['shares_outstanding']['instant'], '2026-10-23')
        self.assertTrue(any('WeightedAverage' in f['concept'] for f in source['data']['reported_facts']))
        average = next(item for item in source['data']['financial_context_table'] if 'WeightedAverage' in item['concept'])
        self.assertFalse(average['ordinary_share_denominator_eligible'])

    def test_annual_cover_shares_survive_newer_quarter_share_observations_for_historical_asof(self):
        data = payload()
        shares = data['facts']['dei']['EntityCommonStockSharesOutstanding']['units']['shares']
        shares.extend([
            row(600, '2024-07-20', filed='2024-07-29', accn='0001193125-24-123456'),
            row(550, '2025-07-20', filed='2025-07-29', accn='0001193125-25-123456'),
            row(490, '2026-08-20', filed='2026-08-25', form='10-Q'),
            row(480, '2026-10-23', filed='2026-10-24', form='10-Q')])
        source = normalize(data)
        for as_of, expected in (('2024-11-06', 600), ('2025-11-06', 550), ('2026-11-06', 480)):
            with self.subTest(as_of=as_of):
                self.assertEqual(canonical_reported_inputs([source], as_of=as_of)['actuals']['shares_outstanding'], expected)

    def test_filing_agent_accession_is_not_mistaken_for_issuer_identity(self):
        self.assertEqual(normalize()['data']['metadata']['cik'], 123456)
        data = payload()
        data['cik'] = 999999
        with self.assertRaisesRegex(ValueError, 'issuer'):
            normalize(data)

    def test_invalid_periods_forms_units_and_values_do_not_become_facts(self):
        for case in ('present_invalid_start', 'future_filed', 'end_after_filed', 'missing_filed',
                     'unsupported_form', 'bool_value', 'composite_unit'):
            data = payload()
            record = data['facts']['us-gaap']['DebtLongtermAndShorttermCombinedAmount']['units']['USD'][-1]
            if case == 'present_invalid_start':
                record['start'] = ''
            elif case == 'future_filed':
                record['filed'] = '2027-01-01'
            elif case == 'end_after_filed':
                record['filed'] = '2026-01-01'
            elif case == 'missing_filed':
                record.pop('filed')
            elif case == 'unsupported_form':
                record['form'] = '8-K'
            elif case == 'bool_value':
                record['val'] = True
            else:
                units = data['facts']['us-gaap']['DebtLongtermAndShorttermCombinedAmount']['units']
                units['USD/shares'] = units.pop('USD')
            with self.subTest(case=case):
                report = canonical_reported_inputs([normalize(data)], as_of='2026-11-06')
                self.assertNotIn('net_debt', report['actuals'])

    def test_competing_values_survive_bounded_duplicate_observations(self):
        data = payload()
        records = data['facts']['us-gaap']['DebtLongtermAndShorttermCombinedAmount']['units']['USD']
        for offset in range(40):
            records.append(row(101 if offset == 0 else 100, filed=(date(2026, 8, 1) + timedelta(days=offset)).isoformat()))
        source = normalize(data)
        values = {f['normalized_value'] for f in source['data']['reported_facts']
                  if f['concept'].endswith('DebtLongtermAndShorttermCombinedAmount') and f['instant'] == '2026-06-30'}
        self.assertEqual(values, {100, 101})
        report = canonical_reported_inputs([source], as_of='2026-11-06')
        self.assertEqual(report['coverage']['status'], 'conflicted')
        self.assertNotIn('net_debt', report['actuals'])
        self.assertEqual(canonical_reported_inputs([source], as_of='2026-07-31')['actuals']['net_debt'], 90)

    def test_cap_never_silently_discards_a_competing_value(self):
        data = payload()
        records = data['facts']['us-gaap']['DebtLongtermAndShorttermCombinedAmount']['units']['USD']
        records.extend(row(200 + offset) for offset in range(25))
        with self.assertRaisesRegex(ValueError, 'conflict-preserving'):
            normalize(data)
        with patch('investmentideas.companyfacts.MAX_REPORTED_FACTS', 1):
            with self.assertRaisesRegex(ValueError, 'bound'):
                normalize()

    def test_structured_origin_requires_official_endpoint_and_untampered_units_values(self):
        for case in ('origin', 'endpoint', 'secondary', 'value', 'unit', 'future_record'):
            source = normalize()
            if case == 'origin':
                source['data']['fact_origin'] = 'llm_estimate'
            elif case == 'endpoint':
                source['url'] = 'https://example.com/companyfacts.json'
            elif case == 'secondary':
                source.pop('source_tier')
                source['kind'] = 'financial_timeseries'
            else:
                for fact in source['data']['reported_facts']:
                    if fact['concept'].endswith('DebtLongtermAndShorttermCombinedAmount') and fact['instant'] == '2026-06-30':
                        if case == 'value':
                            fact['original_value'] = 999
                        elif case == 'unit':
                            fact['original_unit'] = 'EUR'
                        else:
                            fact['filing_date'] = '2027-01-01'
            with self.subTest(case=case):
                self.assertNotIn('net_debt', canonical_reported_inputs([source], as_of='2026-11-06')['actuals'])

    def test_financial_context_keeps_units_and_exposes_conflicts_without_selecting_a_scalar(self):
        data = payload()
        tags = data['facts']['us-gaap']
        period = {'start': '2025-07-01'}
        tags['NetCashProvidedByUsedInOperatingActivities'] = {'units': {'USD': [
            row(40, **period), row(41, filed='2026-08-01', **period)]}}
        tags['NetIncomeLoss'] = {'units': {'USD': [row(20, **period)]}}
        tags['PaymentsToAcquirePropertyPlantAndEquipment'] = {'units': {'USD': [row(5, **period)]}}
        tags['EarningsPerShareDiluted'] = {'units': {'USD/shares': [row(2.5, **period)]}}
        tags['WeightedAverageNumberOfDilutedSharesOutstanding'] = {'units': {'shares': [row(999, **period)]}}
        source = normalize(data, source_id='S-CF')
        table = {item['concept']: item for item in source['data']['financial_context_table'] if item['end_date'] == '2026-06-30'}
        cashflow = table['us-gaap:NetCashProvidedByUsedInOperatingActivities']
        self.assertEqual(cashflow['status'], 'conflicted')
        self.assertIsNone(cashflow['value'])
        self.assertEqual(cashflow['reported_values'], [40, 41])
        self.assertEqual(cashflow['source_refs'], ['S-CF'])
        self.assertTrue(all(source['data']['reported_facts'][index]['concept'] == cashflow['concept']
                            for index in cashflow['reported_fact_indices']))
        self.assertEqual(table['us-gaap:EarningsPerShareDiluted']['unit'], 'iso4217:USD/xbrli:shares')
        self.assertFalse(table['us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding']['ordinary_share_denominator_eligible'])
        report = canonical_reported_inputs([source], as_of='2026-11-06')
        self.assertTrue(report['coverage']['complete'])
        self.assertEqual(report['actuals']['shares_outstanding'], 500)

    def test_structured_and_inline_primary_disagreement_is_exposed(self):
        structured = normalize()
        inline = {'id': 'S-inline', 'kind': 'regulatory_primary', 'data': {
            'metadata': {'cik': 123456, 'filingDate': '2026-07-29'},
            'inline_facts': [{'concept': 'us-gaap:DebtLongtermAndShorttermCombinedAmount',
                              'parse_status': 'parsed', 'dimensions': {}, 'entity_identifier': '123456',
                              'normalized_decimal': '101', 'normalized_value': 101,
                              'instant': '2026-06-30', 'start_date': None, 'end_date': None, 'unit': 'iso4217:USD'}]}}
        report = canonical_reported_inputs([structured, inline], as_of='2026-11-06')
        self.assertEqual(report['coverage']['status'], 'conflicted')
        self.assertNotIn('net_debt', report['actuals'])

    def test_ifrs_bank_and_custom_tags_do_not_become_fcff_reported_inputs(self):
        data = {'cik': 123456, 'facts': {'ifrs-full': {'Revenue': {'units': {'USD': [row(1000)]}}},
                                      'issuer': {'TotalDebt': {'units': {'USD': [row(100)]}}}}}
        source = normalize(data)
        self.assertEqual(source['data']['reported_facts'], [])
        self.assertFalse(canonical_reported_inputs([source])['coverage']['complete'])


if __name__ == '__main__':
    unittest.main()

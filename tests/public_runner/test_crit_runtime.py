"""Two run-ending defects found in review (2026-11-26), from fixtures without network.

1. A valuation date earlier than the UTC retrieval date dropped net debt from the
   reported inputs, so a US-evening run whose model wrote the local date stopped
   at "Essential reported inputs lack complete primary coverage". The financing
   absence scan was credited only when it ran on or before the valuation date.
   Model-authored plans are now dated at the run's evidence date, and the errors
   that remain name their dates.
2. deepseek/deepseek-v4.1-flash-1017, the economy profile's easy model, lists
   time-of-day price overrides that the price parser rejected, so the economy
   profile could not start.
"""
import copy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from investmentideas import core, pipeline
from investmentideas.companyfacts import normalize_companyfacts
from investmentideas.inputs import canonical_reported_inputs
from tests.public_runner.test_inputs import sources as primary_sources
from tests.public_runner.test_memo_diagnostics import rate_plan

IDENTITY = {'cik_str': 123456, 'ticker': 'TEST', 'title': 'Test Issuer'}
END, START = '2026-06-30', '2025-07-01'
SCAN = '2026-11-20'


def _row(value, end=END, *, start=None, filed='2026-07-29'):
    result = {'val': value, 'end': end, 'filed': filed, 'form': '10-K', 'accn': '0001193125-26-123456',
              'fy': 2026, 'fp': 'FY'}
    if start is not None:
        result['start'] = start
    return result


def facts_source(*, scanned=True, retrieved_at=None):
    """Companyfacts cut on SCAN: term debt but no short-term borrowing concept on the balance-sheet date.

    Net debt then rests on the disclosed house assumption that unidentified
    short-term financing is zero, which needs the whole-record absence scan.
    """
    usd = {'RevenueFromContractWithCustomerExcludingAssessedTax': [_row(1000, start=START)],
           'CashAndCashEquivalentsAtCarryingValue': [_row(10)],
           'LongTermDebtCurrent': [_row(100)], 'LongTermDebtNoncurrent': [_row(680)]}
    payload = {'cik': 123456, 'entityName': 'Test Issuer', 'facts': {
        'us-gaap': {tag: {'units': {'USD': rows}} for tag, rows in usd.items()},
        'dei': {'EntityCommonStockSharesOutstanding': {'units': {'shares': [_row(1000, '2026-07-20')]}}}}}
    source = normalize_companyfacts(payload, identity=IDENTITY, as_of=SCAN, source_id='S2',
                                    entity_record_complete=scanned)
    if retrieved_at:
        source['retrieved_at'] = retrieved_at
    return source


class AbsenceScanDateTests(unittest.TestCase):
    def test_the_scan_needs_the_whole_record_for_net_debt(self):
        # Control: without the attested scan, net debt is not pinned at any date.
        result = canonical_reported_inputs([facts_source(scanned=False)], as_of=SCAN)
        self.assertFalse(result['coverage']['complete'])
        self.assertIn('net_debt', result['coverage']['missing_fields'])

    def test_a_valuation_date_before_the_scan_keeps_net_debt(self):
        # The live trap: sources retrieved 2026-11-27 03:10Z, model wrote 2026-11-26.
        sources = [facts_source()]
        self.assertEqual(sources[0]['data']['selection']['as_of'], SCAN)
        for day in ('2026-11-19', '2026-11-01', SCAN):
            with self.subTest(as_of=day):
                result = canonical_reported_inputs(sources, as_of=day)
                self.assertTrue(result['coverage']['complete'], result['coverage'])
                self.assertEqual(result['net_debt_bridge']['short_term_financing']['basis'],
                                 'identified_categories_house_assumption')

    def test_a_valuation_date_after_the_scan_keeps_net_debt(self):
        # Reversing the comparison alone would drop this case: a resumed or
        # revalued run dated after its sources were collected.
        sources = [facts_source()]
        for day in ('2026-11-21', '2026-11-25', None):
            with self.subTest(as_of=day):
                result = canonical_reported_inputs(sources, as_of=day)
                self.assertTrue(result['coverage']['complete'], result['coverage'])
                self.assertEqual(result['net_debt_bridge']['short_term_financing']['basis'],
                                 'identified_categories_house_assumption')


def dated_sources(regulatory='2026-11-07T03:10:16.330614+00:00', market='2026-11-07T03:10:14Z'):
    sources = primary_sources()
    sources[0]['retrieved_at'] = regulatory
    sources[1]['retrieved_at'] = market
    return sources


def model_plan(day, **rate_dates):
    plan = rate_plan()
    plan.pop('valuation_date')
    plan['discount_rate']['as_of'] = day
    for name, observed in rate_dates.items():
        plan['discount_rate'][name]['observed_at'] = observed
    return plan


class EvidenceDateTests(unittest.TestCase):
    def test_evidence_day_is_the_latest_utc_retrieval_date(self):
        def day(*stamps):
            return pipeline._evidence_day([{'retrieved_at': stamp} for stamp in stamps])
        self.assertEqual(day('2026-11-27T03:10:14Z', '2026-11-27T03:10:16.330614+00:00'), '2026-11-27')
        # A US-evening timestamp with an offset is the next UTC day.
        self.assertEqual(day('2026-11-26T22:10:14-05:00'), '2026-11-27')
        self.assertEqual(day('2026-11-25T23:00:00', '2026-11-20T01:00:00Z'), '2026-11-25')
        self.assertEqual(day('not a date', None, 7, '2026-11-24T10:00:00Z'), '2026-11-24')
        # The same stamps parse the same way on 3.9 and 3.13 (3.11 widened fromisoformat).
        self.assertEqual(day('2026-11-23T23:59:59.33-01:00'), '2026-11-24')
        self.assertIsNone(day('20261127T031016Z'))
        self.assertIsNone(day('2026-11-27Z'))
        self.assertIsNone(day('garbage'))
        self.assertIsNone(pipeline._evidence_day([]))
        self.assertIsNone(pipeline._evidence_day(None))
        future = (datetime.now(timezone.utc) + timedelta(days=400)).isoformat()
        self.assertEqual(day(future), datetime.now(timezone.utc).date().isoformat())

    def test_model_date_does_not_move_the_value(self):
        values = {}
        for day in ('2026-11-05', '2026-11-06', '2026-11-07'):
            with self.subTest(model_date=day):
                locked, changes, result = pipeline._lock_and_value(model_plan(day), {'sources': dated_sources()})
                self.assertEqual(locked['valuation_date'], '2026-11-07')
                self.assertEqual(locked['discount_rate']['as_of'], '2026-11-07')
                self.assertEqual(result['valuation_date'], '2026-11-07')
                self.assertEqual(locked['valuation_timing'], {'convention': 'stub_v1', 'anchor_date': '2026-09-26'})
                dated = [row for row in changes if row['field'] == 'discount_rate.as_of']
                self.assertEqual([row['previous'] for row in dated], [] if day == '2026-11-07' else [day])
                values[day] = {name: scenario['per_share_value'] for name, scenario in result['scenarios'].items()}
        self.assertEqual(values['2026-11-05'], values['2026-11-07'])
        self.assertEqual(values['2026-11-06'], values['2026-11-07'])

    def test_a_model_written_valuation_date_is_replaced_and_recorded(self):
        plan = model_plan('2026-11-06')
        plan['valuation_date'] = '2026-11-06'
        original = copy.deepcopy(plan)
        locked, changes, _ = pipeline._lock_and_value(plan, {'sources': dated_sources()})
        self.assertEqual(plan, original)  # The candidate is not mutated; the repair loop reuses it.
        self.assertEqual(locked['valuation_date'], '2026-11-07')
        self.assertEqual({row['field']: row['previous'] for row in changes
                          if row['field'] in ('valuation_date', 'discount_rate.as_of')},
                         {'valuation_date': '2026-11-06', 'discount_rate.as_of': '2026-11-06'})

    def test_a_supplied_plan_keeps_its_own_date(self):
        locked, changes, _ = pipeline._lock_and_value(model_plan('2026-11-06'), {'sources': dated_sources()},
                                                      apply_conventions=False)
        self.assertEqual(locked['valuation_date'], '2026-11-06')
        self.assertFalse([row for row in changes if row['field'] in ('valuation_date', 'discount_rate.as_of')])

    def test_sources_without_retrieval_dates_keep_the_earlier_rule(self):
        locked, _, _ = pipeline._lock_and_value(model_plan('2026-11-06'), {'sources': primary_sources()})
        self.assertEqual(locked['valuation_date'], '2026-11-06')

    def test_an_observation_after_the_evidence_date_names_both_dates(self):
        with self.assertRaisesRegex(ValueError, r'marginal_tax_rate\.observed_at is after as_of \(2026-11-08 > 2026-11-07\)'):
            pipeline._lock_and_value(model_plan('2026-11-07', marginal_tax_rate='2026-11-08'),
                                     {'sources': dated_sources()})

    def test_incomplete_coverage_names_the_valuation_and_retrieval_dates(self):
        sources = [facts_source(scanned=False, retrieved_at='2026-11-20T03:10:16+00:00')]
        with self.assertRaises(ValueError) as caught:
            pipeline._lock_plan({'discount_rate': {'as_of': '2026-11-19'}}, sources)
        message = str(caught.exception)
        self.assertIn('Essential reported inputs lack complete primary coverage at valuation date 2026-11-19', message)
        self.assertIn('sources retrieved through 2026-11-20', message)
        self.assertIn('net_debt', message)

    def test_a_future_valuation_date_names_both_dates(self):
        future = (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat()
        with self.assertRaisesRegex(ValueError, f'cannot be in the future: {future} is after '):
            pipeline._lock_plan({'valuation_date': future}, [])


# deepseek/deepseek-v4.1-flash-1017 as listed in the public catalog on 2026-11-26.
FLASH = {
    'prompt': '0.0000003', 'completion': '0.0000012', 'input_cache_read': '0.000000006',
    'overrides': [
        {'utc_days': ['saturday', 'sunday'], 'prompt': '0.00000015', 'completion': '0.0000006',
         'input_cache_read': '0.000000003'},
        {'utc_days': ['monday', 'tuesday', 'wednesday', 'thursday', 'friday'], 'utc_start': 0, 'utc_end': 100,
         'prompt': '0.00000015', 'completion': '0.0000006', 'input_cache_read': '0.000000003'},
        {'utc_days': ['monday', 'tuesday', 'wednesday', 'thursday', 'friday'], 'utc_start': 100, 'utc_end': 400,
         'prompt': '0.0000003', 'completion': '0.0000012', 'input_cache_read': '0.000000006'},
        {'utc_days': ['monday', 'tuesday', 'wednesday', 'thursday', 'friday'], 'utc_start': 1000, 'utc_end': 0,
         'prompt': '0.00000015', 'completion': '0.0000006', 'input_cache_read': '0.000000003'}]}
# A window without days (minimax/minimax-m3.1-flash, same catalog).
WINDOW = {'prompt': '0.00000015', 'completion': '0.0000006', 'overrides': [
    {'utc_start': 1500, 'utc_end': 100, 'prompt': '0.000000075', 'completion': '0.0000003'},
    {'utc_start': 100, 'utc_end': 1500, 'prompt': '0.00000015', 'completion': '0.0000006'}]}


class TimeOfDayPricingTests(unittest.TestCase):
    def test_flash_reserves_its_highest_listed_price(self):
        self.assertEqual(core._text_price_caps(FLASH), (3e-07, 1.2e-06))
        self.assertEqual(core._text_price_caps(WINDOW), (1.5e-07, 6e-07))

    def test_a_window_priced_above_base_sets_the_reservation(self):
        pricing = copy.deepcopy(FLASH)
        pricing['overrides'][1].update(prompt='0.0000009', completion='0.000001')
        pricing['overrides'][3].update(completion='0.000004')
        self.assertEqual(core._text_price_caps(pricing), (9e-07, 4e-06))

    def test_prompt_length_and_time_tiers_together_take_the_maximum(self):
        pricing = copy.deepcopy(FLASH)
        pricing['overrides'].append({'min_prompt_tokens': 272000, 'prompt': '0.0000006', 'completion': '0.0000011'})
        self.assertEqual(core._text_price_caps(pricing), (6e-07, 1.2e-06))

    def test_malformed_conditions_still_stop_before_any_reservation(self):
        base = {'prompt': '0.0000003', 'completion': '0.0000012'}
        invalid = [{}, {'min_prompt_tokens': None}, {'utc_days': []}, {'utc_days': 'monday'},
                   {'utc_days': ['Monday']}, {'utc_days': ['someday']}, {'utc_days': [1]}, {'utc_days': [['monday']]},
                   {'utc_start': 2500}, {'utc_start': 1260}, {'utc_end': -1}, {'utc_end': True}, {'utc_start': '100'},
                   {'utc_start': 1.5}, {'utc_days': ['monday'], 'utc_start': 2401},
                   {'utc_days': ['monday'], 'request': '0.01'}]
        for condition in invalid:
            with self.subTest(condition=condition):
                with self.assertRaisesRegex(ValueError, 'token-only pricing'):
                    core._text_price_caps({**base, 'overrides': [{**condition, **base}]})
        with self.assertRaisesRegex(ValueError, 'token-only pricing'):
            core._text_price_caps({**base, 'utc_days': ['monday']})

    def test_economy_profile_validates_from_a_fixture_catalog(self):
        easy, strong = core.MODELS['economy']
        self.assertEqual(easy, 'deepseek/deepseek-v4.1-flash-1017')
        plain = {'prompt': '0.0000001', 'completion': '0.000005'}
        catalog = {'data': [{'id': easy, 'pricing': copy.deepcopy(FLASH)},
                            {'id': strong, 'pricing': dict(plain)},
                            {'id': core.REVIEWERS['economy'], 'pricing': dict(plain)}]}
        with patch.object(core, 'request_json', return_value=catalog) as request:
            router = core.Router('synthetic-key', 2, [])
            router.validate_models([easy, strong, core.REVIEWERS['economy']])
            reserve = router.estimate(easy, 'x' * 1000, 2000)
        request.assert_called_once()
        self.assertAlmostEqual(reserve, (len((core.SYSTEM + 'x' * 1000).encode()) + 4096) * 3e-07 + 2000 * 1.2e-06)


if __name__ == '__main__':
    unittest.main()

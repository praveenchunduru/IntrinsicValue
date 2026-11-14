"""Completed-week adjusted-return OLS, independent of network and model calls."""
import copy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from investmentideas.market_beta import gather_weekly_beta, operating_asset_beta, weekly_equity_beta


def charts(count=120, *, beta=1.5, alpha=.002, noisy=False, first=date(2022, 1, 3)):
    market_returns = [(-.03, -.01, .01, .03)[index % 4] for index in range(count)]
    residuals = [(.005, -.005, -.005, .005)[index % 4] if noisy else 0 for index in range(count)]
    equity_returns = [alpha + beta * value + error for value, error in zip(market_returns, residuals)]
    def payload(symbol, returns):
        prices = [100.0]
        for value in returns:
            prices.append(prices[-1] * (1 + value))
        timestamps = [int(datetime.combine(first + timedelta(weeks=index), datetime.min.time(),
                                           ZoneInfo('America/New_York')).timestamp())
                      for index in range(count + 1)]
        return {'chart': {'error': None, 'result': [{
            'meta': {'symbol': symbol, 'currency': 'USD', 'dataGranularity': '1wk',
                     'exchangeTimezoneName': 'America/New_York'},
            'timestamp': timestamps,
            'indicators': {'adjclose': [{'adjclose': prices}],
                           'quote': [{'close': [999] * len(prices)}]}}]}}
    return payload('AAPL', equity_returns), payload('SPY', market_returns)


def result(payload):
    return payload['chart']['result'][0]


def adjusted(payload):
    return result(payload)['indicators']['adjclose'][0]['adjclose']


class RegressionTests(unittest.TestCase):
    def test_known_linear_returns_reproduce_slope_intercept_and_dates(self):
        equity, market = charts()
        before = copy.deepcopy((equity, market))
        fit = weekly_equity_beta(equity, market, as_of='2024-05-01')
        self.assertAlmostEqual(fit['beta'], 1.5, places=12)
        self.assertAlmostEqual(fit['intercept'], .002, places=12)
        self.assertAlmostEqual(fit['r_squared'], 1, places=12)
        self.assertLess(fit['beta_standard_error'], 1e-12)
        self.assertEqual(fit['paired_weekly_returns'], 120)
        self.assertEqual(fit['first_return_week_end'], '2022-01-14')
        self.assertEqual(fit['last_return_week_end'], '2024-04-26')
        self.assertEqual(fit['first_anchor_week_end'], '2022-01-07')
        self.assertEqual(fit['beta_type'], 'historical_levered_equity_beta')
        self.assertNotIn('asset_beta', fit)
        self.assertNotIn('optional_shrinkage', fit)
        self.assertEqual((equity, market), before)

    def test_orthogonal_noise_reproduces_independent_regression_moments(self):
        equity, market = charts(beta=1.2, alpha=.001, noisy=True)
        fit = weekly_equity_beta(equity, market, as_of='2024-05-01')
        # 30 repeats of x=(-.03,-.01,.01,.03), e=(.005,-.005,-.005,.005):
        # Sxx=.06, SSE=.003 and beta=1.2 because x and e are orthogonal.
        self.assertAlmostEqual(fit['beta'], 1.2, places=12)
        self.assertAlmostEqual(fit['intercept'], .001, places=12)
        self.assertAlmostEqual(fit['r_squared'], .0864 / .0894, places=12)
        self.assertAlmostEqual(fit['beta_standard_error'], math.sqrt(.003 / 118 / .06), places=12)
        interval = fit['confidence_interval_95']
        self.assertAlmostEqual(interval['critical_value'], 1.980272, places=5)
        self.assertLess(interval['lower'], 1.2)
        self.assertGreater(interval['upper'], 1.2)
        self.assertIn('descriptive', interval['method'])

    def test_negative_equity_beta_is_observed_not_clipped(self):
        equity, market = charts(beta=-.8)
        fit = weekly_equity_beta(equity, market, as_of='2024-05-01', shrinkage=True)
        self.assertAlmostEqual(fit['beta'], -.8, places=12)
        self.assertAlmostEqual(fit['optional_shrinkage']['beta'], -.2, places=12)
        self.assertIn('separate', fit['optional_shrinkage']['label'])

    def test_constant_benchmark_variance_is_unavailable_not_beta_one(self):
        equity, market = charts()
        adjusted(market)[:] = [100.0] * len(adjusted(market))
        with self.assertRaisesRegex(ValueError, 'variance is zero'):
            weekly_equity_beta(equity, market, as_of='2024-05-01')

    def test_constant_issuer_return_has_explicit_undefined_r_squared(self):
        equity, market = charts()
        adjusted(equity)[:] = [100.0] * len(adjusted(equity))
        fit = weekly_equity_beta(equity, market, as_of='2024-05-01')
        self.assertEqual(fit['beta'], 0)
        self.assertIsNone(fit['r_squared'])
        self.assertIn('undefined', fit['caveats'][-1])


class CalendarAndAlignmentTests(unittest.TestCase):
    def test_partial_current_week_and_same_date_friday_are_excluded(self):
        equity, market = charts(count=121)
        # The last bar starts Monday April29; its final price is incomplete
        # under May1 and must not change this historical estimate.
        adjusted(equity)[-1] = 1_000_000
        adjusted(market)[-1] = 10_000_000
        fit = weekly_equity_beta(equity, market, as_of='2024-05-01')
        self.assertEqual(fit['paired_weekly_returns'], 120)
        self.assertEqual(fit['last_return_week_end'], '2024-04-26')
        self.assertAlmostEqual(fit['beta'], 1.5, places=12)
        friday = weekly_equity_beta(equity, market, as_of='2024-04-26')
        saturday = weekly_equity_beta(equity, market, as_of='2024-04-27')
        self.assertEqual(friday['last_return_week_end'], '2024-04-19')
        self.assertEqual(saturday['last_return_week_end'], '2024-04-26')

    def test_trailing_live_quote_in_the_final_bar_week_is_ignored_not_fatal(self):
        # Shape observed live on 2026-11-14: the Monday bar for the latest week
        # is followed by one point stamped exactly meta.regularMarketTime.
        baseline = weekly_equity_beta(*charts(count=121), as_of='2024-05-04')
        equity, market = charts(count=121)
        for payload in (equity, market):
            live = result(payload)['timestamp'][-1] + 4 * 86400 + 16 * 3600  # Friday 16:00 exchange time.
            result(payload)['meta']['regularMarketTime'] = live
            result(payload)['timestamp'].append(live)
            adjusted(payload).append(adjusted(payload)[-1])
        fit = weekly_equity_beta(equity, market, as_of='2024-05-04')
        self.assertTrue(fit['equity_trailing_live_quote_ignored'])
        self.assertTrue(fit['benchmark_trailing_live_quote_ignored'])
        self.assertFalse(baseline['equity_trailing_live_quote_ignored'])
        self.assertEqual(fit['last_return_week_end'], '2024-05-03')
        self.assertEqual(fit['paired_returns_sha256'], baseline['paired_returns_sha256'])
        self.assertEqual(fit['beta'], baseline['beta'])

    def test_same_week_duplicates_other_than_the_identified_live_quote_still_fail(self):
        def extra(payload, *, position, marker):
            stamps = result(payload)['timestamp']
            stamp = stamps[position] + 4 * 86400 + 16 * 3600
            stamps.insert(position + 1, stamp) if position != -1 else stamps.append(stamp)
            values = adjusted(payload)
            values.insert(position + 1, values[position]) if position != -1 else values.append(values[-1])
            if marker is not None:
                result(payload)['meta']['regularMarketTime'] = marker(stamp)
        cases = {
            'final point without a provider marker': dict(position=-1, marker=None),
            'final point not stamped regularMarketTime': dict(position=-1, marker=lambda stamp: stamp + 1),
            'non-integer marker': dict(position=-1, marker=lambda stamp: float(stamp)),
            'interior duplicate stamped regularMarketTime': dict(position=40, marker=lambda stamp: stamp),
        }
        for name, case in cases.items():
            equity, market = charts(count=121)
            extra(equity, **case)
            with self.subTest(name), self.assertRaisesRegex(ValueError, 'same exchange calendar week'):
                weekly_equity_beta(equity, market, as_of='2024-05-04')

    def test_holiday_week_labels_align_by_calendar_week_not_exact_epoch(self):
        equity, market = charts()
        result(equity)['timestamp'][50] += 24 * 3600  # Tuesday within the same exchange week.
        fit = weekly_equity_beta(equity, market, as_of='2024-05-01')
        self.assertEqual(fit['paired_weekly_returns'], 120)
        self.assertAlmostEqual(fit['beta'], 1.5, places=12)
        gaps = {b - a for a, b in zip(result(market)['timestamp'], result(market)['timestamp'][1:])}
        self.assertIn(7 * 86400 - 3600, gaps)  # DST must not break weekly adjacency.
        self.assertIn(7 * 86400 + 3600, gaps)

    def test_null_or_absent_price_never_becomes_a_two_week_return(self):
        equity, market = charts()
        adjusted(equity)[50] = None
        fit = weekly_equity_beta(equity, market, as_of='2024-05-01')
        self.assertEqual(fit['paired_weekly_returns'], 118)
        self.assertEqual(fit['equity_missing_adjusted_price_weeks'], 1)
        self.assertAlmostEqual(fit['beta'], 1.5, places=12)
        equity, market = charts()
        del result(equity)['timestamp'][50]
        del adjusted(equity)[50]
        fit = weekly_equity_beta(equity, market, as_of='2024-05-01')
        self.assertEqual(fit['paired_weekly_returns'], 118)
        self.assertAlmostEqual(fit['beta'], 1.5, places=12)

    def test_five_year_window_uses_return_end_and_external_previous_anchor(self):
        equity, market = charts(count=263, first=date(2021, 10, 18))
        fit = weekly_equity_beta(equity, market, as_of='2026-11-05')
        self.assertEqual(fit['window_start'], '2021-11-05')
        self.assertEqual(fit['paired_weekly_returns'], 261)
        self.assertEqual(fit['candidate_weekly_returns'], 261)
        self.assertEqual(fit['first_return_week_end'], '2021-11-05')
        self.assertEqual(fit['first_anchor_week_end'], '2021-10-29')
        self.assertEqual(fit['last_return_week_end'], '2026-10-30')

    def test_leap_day_anniversary_uses_documented_february_28(self):
        equity, market = charts()
        fit = weekly_equity_beta(equity, market, as_of='2024-02-29')
        self.assertEqual(fit['window_start'], '2019-02-28')
        self.assertIn('Feb28', fit['method']['window'])


class ValidationTests(unittest.TestCase):
    def test_at_least_104_paired_returns_cannot_be_lowered_by_caller(self):
        equity, market = charts(count=103)
        with self.assertRaisesRegex(ValueError, 'only 103'):
            weekly_equity_beta(equity, market, as_of='2024-05-01')
        equity, market = charts(count=104)
        self.assertEqual(weekly_equity_beta(equity, market, as_of='2024-05-01')['paired_weekly_returns'], 104)
        with self.assertRaisesRegex(ValueError, 'At least 104'):
            weekly_equity_beta(equity, market, as_of='2024-05-01', min_returns=103)

    def test_adjusted_series_is_required_and_raw_close_is_never_a_fallback(self):
        equity, market = charts()
        del result(equity)['indicators']['adjclose']
        with self.assertRaisesRegex(ValueError, 'adjusted closes'):
            weekly_equity_beta(equity, market, as_of='2024-05-01')
        equity, market = charts()
        del adjusted(equity)[-1]
        with self.assertRaisesRegex(ValueError, 'one-for-one'):
            weekly_equity_beta(equity, market, as_of='2024-05-01')

    def test_malformed_metadata_and_extreme_finite_prices_become_value_errors(self):
        equity, market = charts()
        result(equity)['meta'] = None
        with self.assertRaisesRegex(ValueError, 'metadata must be an object'):
            weekly_equity_beta(equity, market, as_of='2024-05-01')
        equity, market = charts()
        adjusted(equity)[50] = 1e200
        with self.assertRaisesRegex(ValueError, 'stable finite regression'):
            weekly_equity_beta(equity, market, as_of='2024-05-01')

    def test_bad_price_timestamps_or_series_metadata_fail_instead_of_guessing(self):
        mutations = [
            lambda value: adjusted(value).__setitem__(20, 0),
            lambda value: adjusted(value).__setitem__(20, True),
            lambda value: adjusted(value).__setitem__(20, float('nan')),
            lambda value: result(value)['timestamp'].__setitem__(20, result(value)['timestamp'][19]),
            lambda value: result(value)['timestamp'].__setitem__(20, result(value)['timestamp'][20] + .5),
            lambda value: result(value)['meta'].__setitem__('dataGranularity', '1d'),
            lambda value: result(value)['meta'].__setitem__('currency', 'EUR'),
            lambda value: result(value)['meta'].__setitem__('exchangeTimezoneName', 'UTC'),
            lambda value: result(value)['meta'].__setitem__('exchangeTimezoneName', ''),
        ]
        for mutate in mutations:
            equity, market = charts()
            mutate(equity)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                weekly_equity_beta(equity, market, as_of='2024-05-01')


class CollectionTests(unittest.TestCase):
    def test_injected_collector_archives_both_sources_with_reproducible_hashes(self):
        equity, market = charts()
        fetch = Mock(side_effect=lambda url: equity if '/AAPL?' in url else market)
        sources, gaps = gather_weekly_beta('aapl', fetch, as_of='2024-05-01', shrinkage=True)
        self.assertEqual(gaps, [])
        self.assertEqual(fetch.call_count, 2)
        source = sources[0]
        self.assertEqual(source['kind'], 'historical_equity_beta')
        self.assertEqual(source['archive_extension'], '.json')
        self.assertNotIn('id', source)
        self.assertEqual(source['raw_document_sha256'], hashlib.sha256(source['raw_document']).hexdigest())
        self.assertEqual(json.loads(source['raw_document']), {'equity': equity, 'benchmark': market})
        self.assertTrue(all('interval=1wk' in call.args[0] and 'includeAdjustedClose=true' in call.args[0]
                            for call in fetch.call_args_list))
        expected = hashlib.sha256(json.dumps(equity, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        self.assertEqual(source['data']['raw_input_hashes']['equity'], expected)
        self.assertNotIn('discount_rate', source)

    def test_transport_and_malformed_input_failures_return_safe_gap_records(self):
        fetch = Mock(side_effect=RuntimeError('do not echo arbitrary response or credential'))
        sources, gaps = gather_weekly_beta('AAPL', fetch, as_of='2024-05-01')
        self.assertEqual(sources, [])
        self.assertEqual(len(gaps), 1)
        self.assertNotIn('credential', repr(gaps))
        self.assertEqual(fetch.call_count, 1)
        equity, market = charts()
        result(equity)['meta'] = None
        sources, gaps = gather_weekly_beta('AAPL', lambda url: equity if '/AAPL?' in url else market, as_of='2024-05-01')
        self.assertEqual(sources, [])
        self.assertIn('metadata must be an object', gaps[0]['reason'])

    def test_returned_symbols_must_match_requests(self):
        equity, market = charts()
        result(equity)['meta']['symbol'] = 'OTHER'
        sources, gaps = gather_weekly_beta('AAPL', lambda url: equity if '/AAPL?' in url else market, as_of='2024-05-01')
        self.assertEqual(sources, [])
        self.assertIn('symbols do not match', gaps[0]['reason'])

    def test_extreme_finite_prices_return_a_structured_arithmetic_gap(self):
        equity, market = charts()
        adjusted(equity)[50] = 1e200
        fetch = Mock(side_effect=[equity, market])
        sources, gaps = gather_weekly_beta('AAPL', fetch, as_of='2024-05-01')
        self.assertEqual(sources, [])
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]['kind'], 'historical_beta_gap')
        self.assertIn('stable finite regression', gaps[0]['reason'])
        json.dumps(gaps, allow_nan=False)

    def test_identical_requested_symbols_return_a_gap_without_fetching(self):
        fetch = Mock()
        for ticker, benchmark in [('SPY', 'SPY'), ('spy', ' SPY '), ('AAPL', 'aapl')]:
            with self.subTest(ticker=ticker, benchmark=benchmark):
                sources, gaps = gather_weekly_beta(ticker, fetch, as_of='2024-05-01',
                                                   benchmark=benchmark)
                self.assertEqual(sources, [])
                self.assertEqual(gaps[0]['kind'], 'historical_beta_gap')
                self.assertIn('different symbols', gaps[0]['reason'])
        fetch.assert_not_called()

    def test_existing_keyless_request_json_is_used_lazily(self):
        equity, market = charts()
        with patch('investmentideas.core.request_json', side_effect=[equity, market]) as fetch:
            sources, gaps = gather_weekly_beta('AAPL', as_of='2024-05-01')
        self.assertEqual(len(sources), 1)
        self.assertEqual(gaps, [])
        self.assertTrue(all(not call.kwargs for call in fetch.call_args_list))

    def test_invalid_settings_fail_before_any_request(self):
        fetch = Mock()
        for settings in ({'as_of': '9999-01-01'}, {'as_of': '2024-5-1'},
                         {'as_of': '2024-05-01', 'min_returns': 10},
                         {'as_of': '2024-05-01', 'years': True},
                         {'as_of': '2024-05-01', 'shrinkage': 'yes'}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                gather_weekly_beta('AAPL', fetch, **settings)
        fetch.assert_not_called()


class OperatingAssetBetaTests(unittest.TestCase):
    def test_explicit_financing_and_cash_correction_are_distinct(self):
        derived = operating_asset_beta(1.5, market_equity=1000, financial_debt=180,
                                       finance_lease_debt=20, nonoperating_cash=120,
                                       marginal_tax_rate=.25)
        self.assertEqual(derived['debt'], 200)
        self.assertEqual(derived['debt_to_equity'], .2)
        self.assertAlmostEqual(derived['hamada_unlevered_beta'], 1.5 / 1.15)
        self.assertEqual(derived['cash_fraction'], .1)
        self.assertAlmostEqual(derived['cash_corrected_unlevered_beta'], (1.5 / 1.15) / .9)
        self.assertNotIn('forward_beta', derived)
        self.assertIn('Zero debt beta', derived['assumptions'][0])
        self.assertIn('Static dated financing', derived['assumptions'][2])

    def test_zero_debt_cash_and_negative_historical_beta_remain_explicit(self):
        derived = operating_asset_beta(-.8, market_equity=100, financial_debt=0,
                                       finance_lease_debt=0, nonoperating_cash=0,
                                       marginal_tax_rate=.25)
        self.assertEqual(derived['hamada_unlevered_beta'], -.8)
        self.assertEqual(derived['cash_corrected_unlevered_beta'], -.8)
        self.assertEqual(derived['cash_fraction'], 0)

    def test_invalid_financing_or_cash_cannot_become_implicit_defaults(self):
        baseline = {'equity_beta': 1.2, 'market_equity': 1000, 'financial_debt': 180,
                    'finance_lease_debt': 20, 'nonoperating_cash': 120, 'marginal_tax_rate': .25}
        invalid = [('market_equity', 0), ('financial_debt', -1), ('finance_lease_debt', -1),
                   ('nonoperating_cash', -1), ('nonoperating_cash', 1200),
                   ('marginal_tax_rate', -.01), ('marginal_tax_rate', .61),
                   ('equity_beta', float('nan')), ('market_equity', float('inf')),
                   ('nonoperating_cash', True)]
        for name, value in invalid:
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                operating_asset_beta(**dict(baseline, **{name: value}))


if __name__ == '__main__':
    unittest.main()

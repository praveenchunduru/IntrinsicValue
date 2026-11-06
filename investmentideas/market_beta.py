"""Historical equity beta from aligned, completed-week adjusted returns.

Yahoo weekly timestamps label period starts, not closing trades. Convert them
with the explicit exchange timezone, pair common Monday calendar-week keys, and
use only adjacent weeks. Friday is a period-end label, not an asserted trade date
(holiday weeks can close earlier). A week is eligible only when its Friday is
strictly before ``as_of``: even a same-date Friday bar is conservatively excluded.
The five-calendar-year window applies to return week-end labels; a prior-week
price outside that window may supply the first return's anchor.

The unweighted OLS slope of adjusted issuer returns against adjusted benchmark
returns is a historical *levered equity* beta, never a forward asset beta. This
module neither unleverages it nor overwrites a sector calibration. Optional
2/3 beta + 1/3 shrinkage is reported separately from the observed slope.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import re
from statistics import NormalDist
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MIN_RETURNS = 104
MAX_INPUT_BYTES = 1_000_000


def _canonical(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    except (TypeError, ValueError, OverflowError):
        raise ValueError('Weekly chart input must contain finite JSON-compatible data') from None
    if not raw or len(raw) > MAX_INPUT_BYTES:
        raise ValueError('Weekly chart input exceeds the bounded 1 MB size limit')
    return raw


def _date(value):
    if type(value) is date:
        result = value
    elif isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        try:
            result = date.fromisoformat(value)
        except ValueError:
            raise ValueError('as_of must be YYYY-MM-DD') from None
    else:
        raise ValueError('as_of must be YYYY-MM-DD')
    if result > datetime.now(timezone.utc).date():
        raise ValueError('as_of cannot be in the future')
    return result


def _settings(as_of, years, min_returns, shrinkage):
    cutoff = _date(as_of)
    if isinstance(years, bool) or not isinstance(years, int) or not 1 <= years <= 10:
        raise ValueError('years must be an integer from 1 to 10')
    if isinstance(min_returns, bool) or not isinstance(min_returns, int) or not MIN_RETURNS <= min_returns <= 600:
        raise ValueError('At least 104 paired weekly returns are required')
    if not isinstance(shrinkage, bool):
        raise ValueError('shrinkage must be an explicit boolean')
    try:
        start = cutoff.replace(year=cutoff.year - years)
    except ValueError:
        # A leap-day anniversary uses February 28 in a non-leap start year.
        start = cutoff.replace(year=cutoff.year - years, day=28)
    return cutoff, start


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a finite number')
    try:
        valid = math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f'{name} must be a finite number')
    return float(value)


def _weekly_prices(payload):
    raw = _canonical(payload)
    try:
        chart = payload['chart']
        results = chart['result']
        if chart.get('error') is not None or not isinstance(results, list) or len(results) != 1:
            raise ValueError('Yahoo weekly chart is unavailable or ambiguous')
        result = results[0]
        meta = result['meta']
        timestamps = result['timestamp']
        adjusted = result['indicators']['adjclose']
        if not isinstance(adjusted, list) or len(adjusted) != 1:
            raise ValueError('One explicit adjusted-close series is required')
        values = adjusted[0]['adjclose']
    except (KeyError, TypeError, IndexError):
        raise ValueError('Yahoo weekly chart requires explicit timestamps, metadata and adjusted closes') from None
    if not isinstance(meta, dict):
        raise ValueError('Weekly chart metadata must be an object')
    if meta.get('dataGranularity') != '1wk':
        raise ValueError('Yahoo chart must explicitly identify 1wk data granularity')
    symbol, currency, timezone_name = meta.get('symbol'), meta.get('currency'), meta.get('exchangeTimezoneName')
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError('Weekly chart requires an explicit provider symbol')
    if not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency):
        raise ValueError('Weekly chart requires an explicit currency')
    if not isinstance(timezone_name, str) or not timezone_name:
        raise ValueError('Weekly chart requires its exchange timezone')
    try:
        exchange = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('Exchange timezone is unavailable; a fixed current UTC offset is not a historical substitute') from None
    if (not isinstance(timestamps, list) or not isinstance(values, list)
            or len(timestamps) != len(values) or not 2 <= len(values) <= 1500):
        raise ValueError('Adjusted closes must align one-for-one with 2–1500 weekly timestamps')
    prices, missing = {}, set()
    previous_timestamp = None
    for timestamp, value in zip(timestamps, values):
        timestamp = _finite(timestamp, 'Weekly timestamp')
        if timestamp != int(timestamp) or (previous_timestamp is not None and timestamp <= previous_timestamp):
            raise ValueError('Weekly timestamps must be strictly increasing integer epochs')
        previous_timestamp = timestamp
        try:
            observed = datetime.fromtimestamp(timestamp, exchange).date()
        except (ValueError, OverflowError, OSError):
            raise ValueError('Weekly timestamp is outside the supported calendar') from None
        monday = observed - timedelta(days=observed.weekday())
        if observed.weekday() > 4:
            raise ValueError('Weekly bar labels must fall within their exchange trading week')
        if monday in prices or monday in missing:
            raise ValueError('Multiple Yahoo observations map to the same exchange calendar week')
        if value is None:
            missing.add(monday)
            continue
        price = _finite(value, 'Adjusted weekly close')
        if price <= 0:
            raise ValueError('Adjusted weekly closes must be positive; no raw-close fallback is allowed')
        prices[monday] = {'adjusted_close': price, 'timestamp': int(timestamp),
                          'bar_label_date': observed.isoformat()}
    return {'symbol': symbol, 'currency': currency, 'timezone': timezone_name,
            'prices': prices, 'missing': missing, 'sha256': hashlib.sha256(raw).hexdigest()}


def _ols(pairs):
    try:
        return _ols_values(pairs)
    except ArithmeticError:
        raise ValueError('Weekly return magnitudes exceed stable finite regression arithmetic') from None


def _ols_values(pairs):
    count = len(pairs)
    x = [row['benchmark_return'] for row in pairs]
    y = [row['equity_return'] for row in pairs]
    x_mean, y_mean = math.fsum(x) / count, math.fsum(y) / count
    sxx = math.fsum((value - x_mean) ** 2 for value in x)
    syy = math.fsum((value - y_mean) ** 2 for value in y)
    if not math.isfinite(sxx) or sxx <= 1e-14:
        raise ValueError('Benchmark return variance is zero or too small for a stable beta estimate')
    sxy = math.fsum((a - x_mean) * (b - y_mean) for a, b in zip(x, y))
    beta = sxy / sxx
    intercept = y_mean - beta * x_mean
    residual_sum_squares = math.fsum((b - intercept - beta * a) ** 2 for a, b in zip(x, y))
    degrees = count - 2
    standard_error = math.sqrt(residual_sum_squares / degrees / sxx)
    # Third-order large-df expansion of the Student-t quantile. n>=104 makes
    # approximation error small; this is explicitly a descriptive OLS interval.
    z = NormalDist().inv_cdf(.975)
    critical = (z + (z ** 3 + z) / (4 * degrees)
                + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * degrees ** 2)
                + (3 * z ** 7 + 19 * z ** 5 + 17 * z ** 3 - 15 * z) / (384 * degrees ** 3))
    r_squared = None if syy <= 1e-28 else max(0.0, min(1.0, 1 - residual_sum_squares / syy))
    values = (beta, intercept, residual_sum_squares, standard_error, critical)
    if not all(math.isfinite(value) for value in values):
        raise ValueError('Weekly returns do not produce finite regression diagnostics')
    return {'beta': beta, 'intercept': intercept, 'r_squared': r_squared,
            'beta_standard_error': standard_error,
            'confidence_interval_95': {'lower': beta - critical * standard_error,
                                       'upper': beta + critical * standard_error,
                                       'critical_value': critical,
                                       'method': 'Approximate Student-t OLS interval, IID constant-variance residual assumptions; descriptive, not a future-beta guarantee'},
            'regression_diagnostics': {'residual_degrees_of_freedom': degrees,
                                       'benchmark_centered_sum_squares': sxx,
                                       'equity_centered_sum_squares': syy,
                                       'residual_sum_squares': residual_sum_squares}}


def weekly_equity_beta(equity_json, market_json, *, as_of, years=5,
                       min_returns=MIN_RETURNS, shrinkage=False):
    """Compute a historical levered-equity OLS beta without input mutation."""
    cutoff, start = _settings(as_of, years, min_returns, shrinkage)
    equity, market = _weekly_prices(equity_json), _weekly_prices(market_json)
    if equity['symbol'] == market['symbol']:
        raise ValueError('Issuer and benchmark must be different symbols')
    if equity['currency'] != market['currency'] or equity['timezone'] != market['timezone']:
        raise ValueError('Issuer and benchmark must share currency and exchange timezone; no FX/calendar conversion is inferred')
    first_end = start + timedelta(days=(4 - start.weekday()) % 7)
    week_ends = []
    current = first_end
    while current < cutoff:
        week_ends.append(current)
        current += timedelta(days=7)
    pairs, unpaired = [], []
    for week_end in week_ends:
        monday = week_end - timedelta(days=4)
        previous = monday - timedelta(days=7)
        if not all(week in series['prices'] for series in (equity, market) for week in (previous, monday)):
            unpaired.append(week_end.isoformat())
            continue
        equity_return = equity['prices'][monday]['adjusted_close'] / equity['prices'][previous]['adjusted_close'] - 1
        market_return = market['prices'][monday]['adjusted_close'] / market['prices'][previous]['adjusted_close'] - 1
        if not math.isfinite(equity_return) or not math.isfinite(market_return):
            raise ValueError('Adjusted prices produce a nonfinite weekly return')
        pairs.append({'week_end': week_end.isoformat(), 'equity_return': equity_return,
                      'benchmark_return': market_return})
    if len(pairs) < min_returns:
        raise ValueError(f'Need at least {min_returns} paired consecutive weekly returns; only {len(pairs)} are available')
    result = _ols(pairs)
    result.update({'dataset': 'historical_weekly_equity_beta',
                   'beta_type': 'historical_levered_equity_beta',
                   'equity_symbol': equity['symbol'], 'benchmark_symbol': market['symbol'],
                   'currency': equity['currency'], 'exchange_timezone': equity['timezone'],
                   'as_of': cutoff.isoformat(), 'lookback_calendar_years': years,
                   'window_start': start.isoformat(), 'window_end_exclusive': cutoff.isoformat(),
                   'paired_weekly_returns': len(pairs), 'candidate_weekly_returns': len(week_ends),
                   'first_return_week_end': pairs[0]['week_end'], 'last_return_week_end': pairs[-1]['week_end'],
                   'first_anchor_week_end': (date.fromisoformat(pairs[0]['week_end']) - timedelta(days=7)).isoformat(),
                   'unpaired_return_week_ends': unpaired,
                   'equity_missing_adjusted_price_weeks': len(equity['missing']),
                   'benchmark_missing_adjusted_price_weeks': len(market['missing']),
                   'paired_returns_sha256': hashlib.sha256(_canonical(pairs)).hexdigest(),
                   'raw_input_hashes': {'equity': equity['sha256'], 'benchmark': market['sha256']}})
    if shrinkage:
        result['optional_shrinkage'] = {'beta': (2 / 3) * result['beta'] + 1 / 3,
                                        'formula': '2/3 * observed equity beta + 1/3 * 1',
                                        'label': 'Explicit shrinkage convention, separate from observed OLS beta'}
    result['method'] = {
        'returns': 'Simple adjusted-close weekly returns; no raw-close substitution or interpolation',
        'alignment': 'Common exchange-local Monday week keys; both endpoints required and exactly seven calendar days apart',
        'week_end_labels': 'Friday period-end labels, not asserted last-trade dates; holidays can close earlier',
        'completion': 'Only scheduled Friday week-end labels strictly before as_of; same-date Friday and partial later weeks excluded',
        'window': 'Calendar-year anniversary through cutoff; return-week-end labels inside window, prior anchor may precede it; Feb29 anniversary clamps to Feb28',
        'regression': 'Unweighted OLS equity_return = intercept + beta * benchmark_return',
        'benchmark': 'Selected ETF is a broad-market proxy, not the theoretical market portfolio',
        'hash_serialization': 'SHA256 of decoded JSON serialized UTF-8 with sorted keys and compact separators; not a wire-format hash'}
    result['caveats'] = [
        'Historical levered equity beta is not a forward operating-asset beta; no sector calibration is overwritten.',
        'Sample and benchmark choices, dividend/split adjustments and missing weeks can change the estimate.',
        'OLS uncertainty assumes independent constant-variance errors; serial correlation and heteroskedasticity can invalidate nominal interval coverage.',
        'The interval describes this historical fit and does not guarantee a future beta or investment result.',
        'No risk-free subtraction, country-risk inference, automatic unlevering or issuer-specific debt-risk adjustment is performed.']
    if result['r_squared'] is None:
        result['caveats'].append('R-squared is undefined because issuer return variation is effectively zero.')
    return result


def operating_asset_beta(equity_beta, *, market_equity, financial_debt,
                         finance_lease_debt, nonoperating_cash, marginal_tax_rate):
    """Unlever a supplied equity beta and remove explicitly nonoperating cash.

    Inputs share one currency/unit and a documented financing snapshot. This
    pure transformation cannot establish their dates, cash classification or a
    forward beta; the caller must retain that evidence and label any adoption.
    Financial debt excludes the separately supplied finance leases. GAAP
    operating leases are not added to debt with unadjusted operating earnings.
    """
    values = {name: _finite(value, name) for name, value in {
        'equity_beta': equity_beta, 'market_equity': market_equity,
        'financial_debt': financial_debt, 'finance_lease_debt': finance_lease_debt,
        'nonoperating_cash': nonoperating_cash, 'marginal_tax_rate': marginal_tax_rate}.items()}
    equity, cash, tax = values['market_equity'], values['nonoperating_cash'], values['marginal_tax_rate']
    if equity <= 0 or values['financial_debt'] < 0 or values['finance_lease_debt'] < 0:
        raise ValueError('Market equity must be positive and debt components must be nonnegative')
    if cash < 0 or not 0 <= tax <= .6:
        raise ValueError('Nonoperating cash must be nonnegative and marginal tax must be between 0 and 0.60')
    debt = values['financial_debt'] + values['finance_lease_debt']
    total = equity + debt
    if not math.isfinite(total) or cash >= total:
        raise ValueError('Nonoperating cash must be below finite market equity plus included gross debt')
    debt_to_equity = debt / equity
    unlevered = values['equity_beta'] / (1 + (1 - tax) * debt_to_equity)
    cash_fraction = cash / total
    try:
        corrected = unlevered / (1 - cash_fraction)
    except ArithmeticError:
        raise ValueError('Financing inputs exceed stable finite beta arithmetic') from None
    if not all(math.isfinite(value) for value in (debt, debt_to_equity, unlevered, cash_fraction, corrected)):
        raise ValueError('Financing inputs exceed stable finite beta arithmetic')
    return {'inputs': values, 'debt': debt, 'debt_to_equity': debt_to_equity,
            'hamada_unlevered_beta': unlevered, 'cash_fraction': cash_fraction,
            'cash_corrected_unlevered_beta': corrected,
            'beta_type': 'historical_equity_beta_transformed_with_explicit_financing_assumptions',
            'formulas': {'hamada_unlevered_beta': 'equity_beta / (1 + (1 - marginal_tax_rate) * debt / market_equity)',
                         'cash_fraction': 'nonoperating_cash / (market_equity + debt)',
                         'cash_corrected_unlevered_beta': 'hamada_unlevered_beta / (1 - cash_fraction)'},
            'assumptions': ['Zero debt beta in Hamada unlevering; financial debt and finance leases counted once.',
                            'Zero beta for the explicitly classified nonoperating cash; cash is not netted against debt.',
                            'Static dated financing snapshot and normalized marginal tax; not a historical capital-structure regression.',
                            'Caller must verify common units, input dates and nonoperating-cash classification.',
                            'No GAAP operating-lease capitalization or automatic forward-beta assignment is performed.']}


def _symbol(value):
    if not isinstance(value, str):
        raise ValueError('Ticker must be a symbol string')
    value = value.strip().upper()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9.\-^=]{0,14}', value):
        raise ValueError('Ticker has unsupported characters')
    return value


def _url(ticker, cutoff, start):
    epoch = lambda value: int(datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp())
    query = urlencode({'interval': '1wk', 'period1': epoch(start - timedelta(days=14)),
                       'period2': epoch(cutoff + timedelta(days=1)),
                       'includeAdjustedClose': 'true', 'events': 'div,splits'})
    return 'https://query1.finance.yahoo.com/v8/finance/chart/' + quote(ticker, safe='') + '?' + query


def gather_weekly_beta(ticker, fetch_json=None, *, as_of, benchmark='SPY', years=5,
                       min_returns=MIN_RETURNS, shrinkage=False):
    """Gather two bounded public chart inputs and return ``(sources, gaps)``.

    The default fetcher is the existing keyless ``core.request_json``. No key,
    metered model, automatic retry or valuation assignment is used. A successful
    source archives canonical decoded JSON for both inputs, about 5 years of
    weekly observations rather than vendor bulk history.
    """
    cutoff, start = _settings(as_of, years, min_returns, shrinkage)
    ticker, benchmark = _symbol(ticker), _symbol(benchmark)
    if ticker == benchmark:
        return [], [{'kind': 'historical_beta_gap', 'symbol': ticker,
                     'reason': 'Issuer and benchmark must be different symbols; no historical beta assigned.'}]
    if fetch_json is None:
        from .core import request_json
        fetch_json = request_json
    urls = {'equity': _url(ticker, cutoff, start), 'benchmark': _url(benchmark, cutoff, start)}
    inputs = {}
    for role, url in urls.items():
        try:
            inputs[role] = fetch_json(url)
            _canonical(inputs[role])
        except (RuntimeError, OSError, ValueError, TypeError):
            return [], [{'kind': 'historical_beta_gap', 'symbol': ticker, 'url': url,
                         'reason': 'Bounded weekly adjusted-price source unavailable; no historical beta invented.'}]
    try:
        data = weekly_equity_beta(inputs['equity'], inputs['benchmark'], as_of=cutoff,
                                  years=years, min_returns=min_returns, shrinkage=shrinkage)
        if data['equity_symbol'] != ticker or data['benchmark_symbol'] != benchmark:
            raise ValueError('Returned chart symbols do not match the requested issuer and benchmark')
    except ValueError as error:
        return [], [{'kind': 'historical_beta_gap', 'symbol': ticker, 'url': urls['equity'],
                     'reason': str(error) + '; no historical beta assigned.'}]
    raw = json.dumps(inputs, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    data['input_urls'] = urls
    source = {'title': ticker + ' historical weekly adjusted-return equity beta versus ' + benchmark,
              'url': urls['equity'], 'related_urls': [urls['benchmark']],
              'kind': 'historical_equity_beta', 'source_tier': 'secondary_market_data',
              'retrieved_at': datetime.now(timezone.utc).isoformat(), 'observation_date': data['last_return_week_end'],
              'data': data, 'raw_document': raw, 'raw_media_type': 'application/json',
              'archive_extension': '.json', 'raw_document_sha256': hashlib.sha256(raw).hexdigest(),
              'ingestion_method': 'public_weekly_adjusted_prices_and_deterministic_ols'}
    return [source], []

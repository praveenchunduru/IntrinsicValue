"""Bounded secondary financial evidence from Yahoo's keyless timeseries API."""
from __future__ import annotations

from datetime import date, datetime, timezone
import math
import re
import time
from urllib.parse import quote, urlencode

METRICS = (
    'annualTotalRevenue',
    'annualNetIncome',
    'annualOperatingCashFlow',
    'annualCapitalExpenditure',
    'annualTotalDebt',
    'annualDilutedAverageShares',
    'annualOperatingIncome',
    'annualCashAndCashEquivalents',
    'annualCashCashEquivalentsAndShortTermInvestments',
    'annualOrdinarySharesNumber',
    'annualTaxProvision',
    'annualPretaxIncome',
    'annualStockBasedCompensation',
    'annualDepreciationAmortizationDepletion',
    'annualChangeInWorkingCapital',
)
SHARE_METRICS = {'annualDilutedAverageShares', 'annualOrdinarySharesNumber'}
PERIOD_END_METRICS = {'annualTotalDebt', 'annualCashAndCashEquivalents',
                      'annualCashCashEquivalentsAndShortTermInvestments', 'annualOrdinarySharesNumber'}



def _valid_row(row, today):
    if not isinstance(row, dict):
        return None
    as_of = row.get('asOfDate')
    if not isinstance(as_of, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', as_of):
        return None
    try:
        if date.fromisoformat(as_of) > today:
            return None
    except ValueError:
        return None
    currency = row.get('currencyCode')
    reported = row.get('reportedValue')
    value = reported.get('raw') if isinstance(reported, dict) else None
    try:
        finite = math.isfinite(value)
    except (TypeError, OverflowError):
        return None
    if (row.get('periodType') != '12M' or not isinstance(currency, str) or
            not re.fullmatch(r'[A-Z]{3}', currency) or isinstance(value, bool) or
            not isinstance(value, (int, float)) or not finite):
        return None
    return {'asOfDate': as_of, 'periodType': '12M', 'currencyCode': currency,
            'reportedValue': {'raw': value}}


def yahoo_financials(ticker, fetch_callable):
    """Return ``(url, data)`` with up to four annual periods per allowed metric.

    This is secondary evidence, never an SEC filing substitute. Malformed rows
    are omitted; another issuer's symbol or insufficient financial data fails
    closed. The supplied callable receives a URL and returns decoded JSON.
    """
    if not isinstance(ticker, str):
        raise ValueError('A ticker string is required')
    ticker = ticker.strip().upper()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9.\-^=]{0,14}', ticker):
        raise ValueError('Invalid ticker for financial data retrieval')
    # Yahoo uses a hyphen for share classes that SEC represents with a dot.
    yahoo_ticker = ticker.replace('.', '-')
    now = int(time.time())
    query = urlencode({'type': ','.join(METRICS), 'period1': max(0, now - 7 * 366 * 86400),
                       'period2': now})
    url = ('https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/' +
           quote(yahoo_ticker, safe='') + '?' + query)
    payload = fetch_callable(url)
    envelope = payload.get('timeseries') if isinstance(payload, dict) else None
    if not isinstance(envelope, dict) or envelope.get('error'):
        raise RuntimeError('Yahoo annual financial data unavailable')
    records = envelope.get('result')
    if not isinstance(records, list):
        raise RuntimeError('Yahoo annual financial response is malformed')
    today = datetime.now(timezone.utc).date()
    collected = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get('meta'), dict):
            continue
        symbols = record['meta'].get('symbol')
        if not isinstance(symbols, list) or not symbols:
            continue
        if (len(symbols) != 1 or not isinstance(symbols[0], str) or
                symbols[0].upper().replace('.', '-') != yahoo_ticker):
            raise RuntimeError('Yahoo financial response identifies a different ticker')
        types = record['meta'].get('type')
        if not isinstance(types, list):
            continue
        for metric in METRICS:
            if metric not in types or not isinstance(record.get(metric), list):
                continue
            rows = collected.setdefault(metric, {})
            for raw in record[metric]:
                row = _valid_row(raw, today)
                if row is not None:
                    # Dedupe the provider's repeated observation of one period.
                    rows[(row['asOfDate'], row['periodType'], row['currencyCode'])] = row
    series = {metric: sorted(rows.values(), key=lambda row: row['asOfDate'])[-4:]
              for metric, rows in collected.items() if rows}
    if not series.get('annualTotalRevenue') or len(series) < 2:
        raise RuntimeError('Yahoo financial evidence needs revenue plus another numeric annual financial series')
    return url, {
        'symbol': ticker,
        'frequency': 'annual',
        'series': series,
        'units': {metric: 'shares' if metric in SHARE_METRICS else 'currencyCode in each row'
                  for metric in series},
        'series_metadata': {
            metric: {
                'measurement': ('period-end balance on the supplied asOfDate' if metric in PERIOD_END_METRICS else
                                'annual weighted average share count' if metric == 'annualDilutedAverageShares' else
                                'annual flow over the supplied 12M period'),
                'unit': 'shares' if metric in SHARE_METRICS else 'currencyCode in each row',
                'date_basis': 'provider-normalized asOfDate; verify the exact fiscal date against filings',
            } for metric in series
        },
        'notes': [
            'Secondary Yahoo Finance data; verify financial figures against official filings when available.',
            'asOfDate and currencyCode are reproduced from the provider; period ends may be normalized rather than exact fiscal filing dates.',
            'Capital expenditure retains the provider sign, including negative cash outflows; avoid subtracting an outflow twice.',
            'Diluted average shares are annual average share counts, not currency amounts or current shares outstanding.',
            'Ordinary shares are dated period-end share counts, not shares verified as current on the market-quote date.',
            'Cash and cash equivalents are included in cash, cash equivalents and short-term investments; use one cash definition, never add the two series.',
            'An enterprise-to-equity bridge must align debt and the chosen cash measure to the same asOfDate and currency; absent cash or debt is missing evidence, never zero.',
            'Change in working capital preserves the provider cash-flow-statement sign; it is not automatically an increase in invested working capital for FCFF subtraction.',
            'Tax provision and pretax income are reported annual flows, not an assumed normalized forward cash tax rate.',
            'Stock-based compensation is reported separately and must not be treated as free capital or counted twice in an FCFF bridge.',
        ],
    }

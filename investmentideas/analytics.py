"""Arithmetic over comparable annual observations, without investment inferences."""
from datetime import date
import math
import re

FIELDS = {'annualTotalRevenue': 'revenue', 'annualNetIncome': 'net_income',
          'annualOperatingCashFlow': 'operating_cash_flow'}
BASIS = 'Calculated from Yahoo annual observations on provider-normalized dates, not confirmed fiscal-year ends'


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _consecutive(previous, current):
    if current.year != previous.year + 1 or current.month != previous.month:
        return False
    return previous.day == current.day or (current.month == 2 and {previous.day, current.day} <= {28, 29})


def annual_metrics(financials):
    """Return dated, currency-specific facts and derived metrics for annual rows.

    No quarters are summed. YoY requires an annual calendar anniversary in the
    same currency; missing years are not bridged. Ratios with a zero denominator
    and non-finite results are omitted. No buyback or liquidity conclusion is made.
    """
    series = financials.get('series') if isinstance(financials, dict) else None
    if not isinstance(series, dict):
        return []
    periods = {}
    for metric, field in FIELDS.items():
        rows = series.get(metric)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or row.get('periodType') != '12M':
                continue
            day, currency = row.get('asOfDate'), row.get('currencyCode')
            value = row.get('reportedValue')
            value = value.get('raw') if isinstance(value, dict) else None
            if (not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) or
                    not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency) or
                    not _finite(value)):
                continue
            try:
                parsed = date.fromisoformat(day)
            except ValueError:
                continue
            observation = periods.setdefault((parsed, currency), {
                'asOfDate': day, 'currencyCode': currency, 'periodType': '12M', 'basis': BASIS})
            observation[field] = value
    result, previous_revenues = [], {}
    for (day, currency), observation in sorted(periods.items()):
        revenue = observation.get('revenue')
        income = observation.get('net_income')
        cash_flow = observation.get('operating_cash_flow')
        if income is not None and cash_flow is not None:
            difference = cash_flow - income
            if _finite(difference):
                observation['cash_flow_minus_net_income'] = difference
            observation['ocf_exceeds_net_income'] = cash_flow > income
        if revenue is not None and revenue != 0 and income is not None:
            margin = (income / revenue) * 100
            if _finite(margin):
                observation['net_margin_percent'] = margin
        if revenue is not None:
            previous = previous_revenues.get(currency)
            if previous is not None and previous[1] != 0 and _consecutive(previous[0], day):
                growth = (revenue / previous[1] - 1) * 100
                if _finite(growth):
                    observation['revenue_yoy_percent'] = growth
            previous_revenues[currency] = (day, revenue)
        result.append(observation)
    return result

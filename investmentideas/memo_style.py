"""Number and currency style for the rendered memo.

The memo names its currency once near the top. After that, amounts carry the
currency symbol (or the code where no symbol exists) and a scale letter:
per-share model values to three significant figures ($139, $41.4), quoted
prices as quoted ($355.58), aggregates as $34.3B or $850M. Tables carry the
unit in the column header and bare numbers in the cells. Percentages show one
decimal; dated rate inputs show two.
"""
from __future__ import annotations

import math

# Symbols for the currencies SEC filers report in. Codes without a common
# unambiguous symbol print as the code and a space.
SYMBOLS = {'USD': '$', 'EUR': '€', 'GBP': '£', 'JPY': '¥', 'CNY': 'CN¥', 'INR': '₹', 'KRW': '₩', 'CAD': 'C$',
           'AUD': 'A$', 'HKD': 'HK$', 'TWD': 'NT$', 'BRL': 'R$', 'MXN': 'MX$', 'ILS': '₪', 'NZD': 'NZ$',
           'SGD': 'S$'}
NAMES = {'USD': 'US dollars', 'EUR': 'euros', 'GBP': 'pounds sterling', 'JPY': 'Japanese yen', 'CNY': 'Chinese yuan',
         'INR': 'Indian rupees', 'KRW': 'Korean won', 'CAD': 'Canadian dollars', 'AUD': 'Australian dollars',
         'HKD': 'Hong Kong dollars', 'TWD': 'New Taiwan dollars', 'BRL': 'Brazilian reais', 'MXN': 'Mexican pesos',
         'ILS': 'Israeli shekels', 'NZD': 'New Zealand dollars', 'SGD': 'Singapore dollars', 'CHF': 'Swiss francs',
         'SEK': 'Swedish kronor', 'NOK': 'Norwegian kroner', 'DKK': 'Danish kroner', 'ZAR': 'South African rand'}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class Style:
    """Formatting for one memo's currency."""

    def __init__(self, currency):
        self.code = (currency or '').upper()
        self.symbol = SYMBOLS.get(self.code, (self.code + ' ') if self.code else '')

    def header(self):
        """The one statement of currency and scale, near the top of the memo."""
        name = NAMES.get(self.code, self.code or 'the reporting currency')
        if self.code in SYMBOLS:
            return f'{name[0].upper() + name[1:]}; {self.symbol}B is billions and {self.symbol}M is millions.'
        return f'Amounts are in {name} ({self.code}); B is billions and M is millions.'

    def unit(self, scale):
        """Column-header unit: '$B', '$M', '$', or 'CHF B' for a code without a symbol."""
        if self.code and self.code not in SYMBOLS:
            return f'{self.code} {scale}'.strip()
        return f'{self.symbol}{scale}'

    def _sign(self, value, signed):
        if value < 0:
            return '-'
        return '+' if signed and value > 0 else ''

    def per_share(self, value, signed=False):
        """A model value per share to three significant figures: $139, $41.4, $8.12; whole dollars from 100."""
        if not _finite(value):
            return 'n/a'
        return self._sign(value, signed) + self.symbol + bare_per_share(abs(value))

    def price(self, value):
        """A quoted price as quoted, to the cent."""
        return 'n/a' if not _finite(value) else f'{self.symbol}{value:,.2f}'

    def amount(self, value, signed=False):
        """An aggregate: $1,971B, $56.8B, $850M, $0."""
        if not _finite(value):
            return 'n/a'
        if value == 0:
            return self.symbol + '0'
        size = abs(value)
        if size >= 1e11:
            text = f'{size / 1e9:,.0f}B'
        elif size >= 1e9:
            text = f'{size / 1e9:,.1f}B'
        elif size >= 1e8:
            text = f'{size / 1e6:,.0f}M'
        elif size >= 1e6:
            text = f'{size / 1e6:,.1f}M'
        else:
            text = f'{size:,.0f}'
        return self._sign(value, signed) + self.symbol + text


def bare_per_share(value, signed=False):
    """A per-share figure for a table cell whose header carries the currency."""
    if not _finite(value):
        return 'n/a'
    size = abs(value)
    text = (f'{size:,.0f}' if size >= 100 else f'{size:.1f}' if size >= 10 else f'{size:.2f}')
    sign = '-' if value < 0 and text.strip('0.,') else ('+' if signed and value > 0 else '')
    return sign + text


def bare_change(value, reference):
    """A per-share change shown at the precision of the value it changes: +67 beside 206, +5.7 beside 41.4."""
    if not _finite(value):
        return 'n/a'
    size = abs(reference) if _finite(reference) else abs(value)
    decimals = 0 if size >= 100 else 1 if size >= 10 else 2
    text = f'{abs(value):,.{decimals}f}'
    if not text.strip('0.,'):
        return '0'
    return ('-' if value < 0 else '+') + text


def table_scale(values):
    """('B', 1e9, decimals) or ('M', 1e6, 0) for a column set, so cells stay short."""
    finite = [abs(value) for value in values if _finite(value)]
    return ('B', 1e9, 1) if finite and max(finite) >= 1e10 else ('M', 1e6, 0)


def scaled(value, scale, decimals=1):
    """A bare cell in the table's scale; zero is 0."""
    if not _finite(value):
        return 'n/a'
    text = f'{value / scale:,.{decimals}f}'
    return '0' if not text.strip('-0.,') else text


def pct(value, decimals=1, signed=False):
    if not _finite(value):
        return 'n/a'
    text = f'{value * 100:{"+" if signed else ""}.{decimals}f}%'
    # A value that rounds to zero prints without a sign.
    return text.lstrip('+-') if not text.strip('+-0.%') else text


def points(value, decimals=1):
    """A difference of two rates, in percentage points."""
    if not _finite(value):
        return 'n/a'
    return f'{value * 100:+.{decimals}f} points'


def ratio(value, decimals=2):
    return 'n/a' if not _finite(value) else f'{value:.{decimals}f}'


def multiple(value):
    return 'n/a' if not _finite(value) else f'{value:.1f}×'

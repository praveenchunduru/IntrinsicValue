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
import re

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
        # The unit and decimals follow the rounded value, so 999.6M prints $1.0B and never $1,000M.
        for scale, letter, decimals, limit in ((1, '', 0, 1e6), (1e6, 'M', 1, 1e8), (1e6, 'M', 0, 1e9),
                                               (1e9, 'B', 1, 1e11), (1e9, 'B', 0, math.inf)):
            text = f'{size / scale:,.{decimals}f}'
            if float(text.replace(',', '')) * scale < limit:
                text += letter
                break
        return self._sign(value, signed) + self.symbol + text


def three_figures(size):
    """A non-negative number to three significant figures, chosen from the rounded value: 9.996 is 10.0, 99.96 is 100."""
    for decimals, limit in ((2, 10), (1, 100)):
        if size < limit:
            text = f'{size:.{decimals}f}'
            if float(text) < limit:
                return text
    return f'{size:,.0f}'


def bare_per_share(value, signed=False):
    """A per-share figure for a table cell whose header carries the currency."""
    if not _finite(value):
        return 'n/a'
    text = three_figures(abs(value))
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
    """('B', 1e9, 1), ('M', 1e6, 0) or whole units ('', 1, 0) for a column set, so cells stay short."""
    finite = [abs(value) for value in values if _finite(value)]
    largest = max(finite) if finite else 0
    return ('B', 1e9, 1) if largest >= 1e10 else ('M', 1e6, 0) if largest >= 1e7 else ('', 1, 0)


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


# A number of seven or more digits, with or without thousands separators, optionally after "$".
_LONG_NUMBER = re.compile(r'(?<![\w.,$])(\$)?(\d{1,3}(?:,\d{3}){2,}|\d{7,})(\.\d+)?(?![\d,]|\.\d)')
# "USD <number>" that is an amount: not a rate (EUR/USD 1.08, USD 1 = EUR 0.92), not a coupon
# (USD 2.5% notes), not a bare year (the USD 2030 notes). Quoted text is left as written.
_USD_AMOUNT = re.compile(r'(?<![/\w])USD\s?(-)?(\d[\d,]*(?:\.\d+)?)(?:\s?(billion|bn|million|mn|mm|trillion)\b)?'
                         r'(?![\d,.]*\s?(?:%|=|percent\b))', re.I)
_QUOTED = re.compile(r'"[^"\n]*"|\u201c[^\u201d\n]*\u201d')


def _usd_amounts(text):
    """(start, end, replacement) for each "USD" amount outside quotation marks."""
    quoted = [match.span() for match in _QUOTED.finditer(text)]
    found = []
    for match in _USD_AMOUNT.finditer(text):
        if any(start <= match.start() < end for start, end in quoted):
            continue
        sign, number, unit = match.group(1) or '', match.group(2), (match.group(3) or '').lower()
        if not unit and re.fullmatch(r'(?:19|20)\d\d', number):
            continue
        suffix = ' trillion' if unit == 'trillion' else 'B' if unit in ('billion', 'bn') else 'M' if unit else ''
        found.append((match.start(), match.end(), f'{sign}${number}{suffix}'))
    return found


def usd_amount_count(text):
    """How many "USD" amounts the text writes; the currency-code check counts these only."""
    text = text or ''
    return len(_usd_amounts(text)) + len(_TRAILING_USD.findall(text))


# "2.0bn USD": the code after a scaled amount.
_TRAILING_USD = re.compile(r'(?<![\w.$])(-)?(\d[\d,]*(?:\.\d+)?)\s?(billion|bn|million|mn|mm)\s+USD\b', re.I)


def dollar_amounts(text):
    """'USD 36.3 billion' as '$36.3B', '2.0bn USD' as '$2.0B', 'USD -2.4 billion' as '-$2.4B'; figures unchanged."""
    for start, end, replacement in reversed(_usd_amounts(text)):
        text = text[:start] + replacement + text[end:]
    quoted = [match.span() for match in _QUOTED.finditer(text)]
    def trailing(match):
        if any(start <= match.start() < end for start, end in quoted):
            return match.group(0)
        unit = 'B' if match.group(3).lower() in ('billion', 'bn') else 'M'
        return f'{match.group(1) or ""}${match.group(2)}{unit}'
    return _TRAILING_USD.sub(trailing, text)


def compact(text, style=None):
    """Model-written text as the memo prints it: "USD" amounts in symbols, long numbers as 2.74M or $34.3B.

    The figure trace accepts these forms: '2.74M' traces to 2,735,053 within its
    displayed precision.
    """
    if not isinstance(text, str):
        return text
    dollars = style is None or style.code == 'USD'
    if dollars:
        text = dollar_amounts(text)
    def short(match):
        value = float(match.group(2).replace(',', '') + (match.group(3) or ''))
        if match.group(1) and dollars:
            return (style or Style('USD')).amount(value)
        scale, letter = (1e9, 'B') if value >= 1e9 else (1e6, 'M')
        return (match.group(1) or '') + bare_per_share(value / scale) + letter
    return _LONG_NUMBER.sub(short, text)

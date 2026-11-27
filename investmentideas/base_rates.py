"""Published sales-growth base rates, and where a forecast plan's revenue path falls in them.

Mauboussin's outside view: compare a forecast with what companies of the same starting
size have done. The tables are static and cited; nothing here uses the network. The
output describes the plan against history. No plan is checked against it and no
forecast input is set from it (owner choice, 2026-11-26: print percentiles, no gate).

Sources, newest first. Where two tables cover the same size class and horizon, the
newer one is used:

- Mauboussin and Callahan, "Bayes and Base Rates 2.0" (May 13, 2026), Exhibit 1, p3:
  5-year growth for US companies with $2-5 billion of starting sales, nominal. It
  supersedes Exhibit 3 of the February paper, the same class through 2024.
- Mauboussin and Callahan, "Bayes and Base Rates" (February 10, 2026), Exhibit 5, p6:
  5-year growth for $8-12 billion of starting sales, nominal.
- Mauboussin, Callahan and Majd, "The Base Rate Book" (Credit Suisse, September 26,
  2016), Exhibit 4, p24-25, and the appendix observation counts, p31-32: 1-, 3-, 5- and
  10-year growth by starting-sales decile and for sales above $50 billion, after
  inflation, in five-point bins with mean, median and standard deviation.

The 2026 papers give mean and standard deviation only, so their percentiles use a
normal approximation; the 2016 book's bins allow an empirical percentile. The book
publishes operating-margin persistence by sector (Exhibit 5, p62) as a correlation and
a sector mean, with the time-series spread of the sector average; that is not a
distribution of company margins, so no margin percentile is computed.
"""
from __future__ import annotations

import re

import json
import math
from statistics import NormalDist

HORIZONS = (3, 5, 10)
QUANTILES = (10, 25, 50, 75, 90)

# The 2016 tables are after inflation; plans forecast nominal revenue. The pipeline
# collects no inflation expectation, so growth is converted at this stated rate.
INFLATION = 0.02
INFLATION_BASIS = ("a stated 2.0% a year, the Federal Reserve's longer-run inflation goal (FOMC, Statement on "
                   "Longer-Run Goals and Monetary Policy Strategy), a policy goal")

# US CPI-U, all items, US city average, not seasonally adjusted (BLS series CUUR0000SA0):
# annual averages of the monthly index, retrieved from the BLS public data API on
# 2026-11-26. BLS published no October 2025 index (lapse in appropriations), so 2025
# averages the eleven published months. Used only to restate starting sales in each
# table's dollars; years outside the table move at INFLATION.
CPI_U = {2015: 237.017, 2016: 240.007, 2017: 245.120, 2018: 251.107, 2019: 255.657, 2020: 258.811,
         2021: 270.970, 2022: 292.655, 2023: 304.702, 2024: 313.689, 2025: 321.943}
CPI_BASIS = ('US CPI-U annual averages (BLS series CUUR0000SA0) for 2015-2025; later years extended at the stated '
             'inflation rate')

SOURCES = {
    'brb2016': {
        'citation': ('Michael J. Mauboussin, Dan Callahan and Darius Majd, "The Base Rate Book: Integrating the Past '
                     'to Better Anticipate the Future", Credit Suisse Global Financial Strategies, September 26, 2016'),
        'short': 'The Base Rate Book (2016)', 'published': '2016-09-26', 'growth_basis': 'real', 'price_year': 2015,
        'universe': ('the top 1,000 global companies by market capitalization each year, 1950-2015, including '
                     'companies that later ceased to exist (Credit Suisse HOLT)')},
    'bbr2026': {
        'citation': ('Michael J. Mauboussin and Dan Callahan, "Bayes and Base Rates: How History Can Guide Our '
                     'Assessment of the Future", Consilient Observer, Counterpoint Global Insights, Morgan Stanley, '
                     'February 10, 2026'),
        'short': 'Bayes and Base Rates (February 2026)', 'published': '2026-02-10', 'growth_basis': 'nominal',
        'price_year': 2024, 'universe': 'US public companies, 1950-2024 (Compustat and FactSet)'},
    'bbr2026b': {
        'citation': ('Michael J. Mauboussin and Dan Callahan, "Bayes and Base Rates 2.0: How History Can Guide Our '
                     'Assessment of the Future", Consilient Observer, Counterpoint Global Insights, Morgan Stanley, '
                     'May 13, 2026'),
        'short': 'Bayes and Base Rates 2.0 (May 2026)', 'published': '2026-05-13', 'growth_basis': 'nominal',
        'price_year': 2025, 'universe': 'US public companies, 1950-2025 (Compustat and FactSet)'},
}

# Interior edges of the 2016 book's sixteen bins of compound annual growth: below -25%,
# then five-point bins from -25% to 45%, then above 45%. A value on an edge belongs
# to the bin above it.
BIN_EDGES = (-.25, -.20, -.15, -.10, -.05, 0.0, .05, .10, .15, .20, .25, .30, .35, .40, .45)


def _brb_class(key, label, low, high, *, page, counts_page, stats, counts):
    return {'id': 'brb2016_' + key, 'source': 'brb2016', 'label': label, 'low': low, 'high': high,
            'exhibit': 'Exhibit 4', 'page': page, 'counts_exhibit': 'Appendix', 'counts_page': counts_page,
            'horizons': {years: {'mean': mean, 'median': median, 'standard_deviation': deviation,
                                 'counts': counts[years]}
                         for years, (mean, median, deviation) in stats.items()}}


# Exhibit 4 (mean, median, standard deviation as printed) with the appendix counts per
# bin. Starting sales are the prior year's, in 2015 dollars. The counts are used for the
# bins because Exhibit 4 prints the USD 325-700 million percentages over the totals of the
# USD 0-325 million decile (its 1-year column sums to 91.7%); every other printed
# percentage equals its count over its own total. The overlapping "above USD 25 billion"
# decile is used below USD 50 billion; the book's full-universe column is not a size class
# and is not encoded.
SALES_GROWTH_2016 = (
    _brb_class('0_325m', 'USD 0-325 million', 0.0, 325e6, page=24, counts_page=31,
               stats={1: (.61, .121, 8.211), 3: (.212, .117, .4), 5: (.168, .112, .218), 10: (.126, .098, .12)},
               counts={1: (92, 43, 67, 104, 219, 451, 893, 923, 764, 556, 412, 259, 229, 151, 137, 943),
                       3: (27, 12, 22, 60, 110, 360, 928, 1159, 919, 629, 386, 274, 197, 169, 119, 686),
                       5: (18, 8, 20, 28, 70, 266, 967, 1322, 1089, 605, 400, 289, 189, 177, 114, 432),
                       10: (0, 2, 14, 27, 41, 200, 960, 1684, 1169, 601, 356, 243, 154, 103, 64, 122)}),
    _brb_class('325_700m', 'USD 325-700 million', 325e6, 700e6, page=24, counts_page=31,
               stats={1: (.161, .082, .535), 3: (.107, .075, .159), 5: (.092, .073, .108), 10: (.074, .067, .07)},
               counts={1: (58, 27, 66, 143, 252, 503, 1104, 1079, 765, 450, 364, 200, 120, 102, 77, 421),
                       3: (13, 17, 34, 61, 148, 459, 1345, 1321, 913, 450, 270, 149, 115, 77, 59, 178),
                       5: (5, 5, 24, 42, 111, 397, 1397, 1584, 911, 434, 209, 126, 87, 49, 40, 75),
                       10: (0, 1, 6, 30, 93, 334, 1395, 1843, 857, 300, 141, 65, 38, 13, 4, 3)}),
    _brb_class('700m_1250m', 'USD 700 million to 1.25 billion', 700e6, 1250e6, page=24, counts_page=31,
               stats={1: (.124, .071, .317), 3: (.092, .068, .135), 5: (.079, .064, .105), 10: (.062, .057, .07)},
               counts={1: (85, 47, 71, 140, 246, 531, 1015, 949, 637, 411, 270, 160, 145, 93, 75, 319),
                       3: (21, 15, 38, 92, 177, 480, 1189, 1188, 729, 415, 219, 147, 97, 77, 43, 112),
                       5: (12, 16, 25, 62, 162, 429, 1297, 1297, 746, 355, 204, 110, 49, 47, 20, 44),
                       10: (7, 4, 10, 36, 99, 407, 1415, 1420, 655, 215, 90, 48, 13, 6, 2, 2)}),
    _brb_class('1250m_2bn', 'USD 1.25-2 billion', 1250e6, 2e9, page=24, counts_page=31,
               stats={1: (.123, .068, .351), 3: (.086, .062, .14), 5: (.072, .057, .101), 10: (.057, .051, .066)},
               counts={1: (66, 42, 66, 132, 250, 492, 1002, 956, 602, 360, 201, 166, 108, 76, 71, 284),
                       3: (16, 18, 34, 85, 182, 502, 1200, 1114, 614, 327, 185, 137, 81, 46, 24, 108),
                       5: (15, 7, 17, 46, 139, 465, 1330, 1213, 612, 280, 152, 94, 42, 28, 19, 33),
                       10: (1, 2, 15, 27, 73, 390, 1466, 1242, 489, 161, 63, 26, 12, 4, 7, 3)}),
    _brb_class('2_3bn', 'USD 2-3 billion', 2e9, 3e9, page=24, counts_page=31,
               stats={1: (.1, .06, .229), 3: (.072, .054, .121), 5: (.062, .051, .089), 10: (.051, .045, .06)},
               counts={1: (65, 48, 77, 132, 256, 531, 1061, 898, 587, 341, 228, 139, 98, 65, 39, 230),
                       3: (19, 12, 41, 72, 220, 563, 1234, 1025, 569, 295, 189, 111, 61, 42, 28, 58),
                       5: (11, 5, 17, 52, 153, 501, 1368, 1154, 516, 232, 137, 75, 27, 14, 7, 18),
                       10: (0, 3, 3, 13, 96, 441, 1462, 1055, 360, 150, 40, 28, 4, 2, 2, 0)}),
    _brb_class('3_4500m', 'USD 3-4.5 billion', 3e9, 4.5e9, page=24, counts_page=31,
               stats={1: (.088, .054, .256), 3: (.064, .05, .111), 5: (.054, .047, .085), 10: (.044, .041, .057)},
               counts={1: (82, 51, 96, 180, 330, 646, 1150, 932, 593, 365, 245, 150, 86, 62, 39, 207),
                       3: (22, 18, 43, 98, 246, 697, 1311, 1113, 574, 343, 163, 98, 65, 36, 22, 41),
                       5: (7, 7, 32, 80, 172, 664, 1438, 1160, 565, 241, 118, 66, 22, 7, 12, 8),
                       10: (1, 1, 1, 31, 101, 574, 1534, 1073, 334, 124, 31, 15, 1, 1, 0, 0)}),
    _brb_class('4500m_7bn', 'USD 4.5-7 billion', 4.5e9, 7e9, page=25, counts_page=32,
               stats={1: (.079, .051, .23), 3: (.057, .044, .116), 5: (.05, .041, .087), 10: (.039, .037, .06)},
               counts={1: (105, 58, 93, 218, 378, 732, 1239, 1080, 634, 363, 215, 159, 98, 53, 43, 216),
                       3: (24, 36, 54, 139, 294, 772, 1484, 1090, 575, 322, 189, 99, 58, 25, 20, 50),
                       5: (11, 9, 31, 89, 215, 747, 1595, 1110, 504, 267, 106, 51, 28, 13, 8, 15),
                       10: (1, 5, 4, 38, 160, 603, 1591, 1034, 299, 104, 33, 20, 6, 0, 0, 0)}),
    _brb_class('7_12bn', 'USD 7-12 billion', 7e9, 12e9, page=25, counts_page=32,
               stats={1: (.068, .043, .256), 3: (.047, .037, .109), 5: (.039, .035, .083), 10: (.033, .032, .06)},
               counts={1: (132, 79, 121, 233, 520, 934, 1427, 1196, 707, 357, 241, 140, 103, 60, 51, 203),
                       3: (28, 30, 74, 177, 429, 1005, 1660, 1220, 622, 321, 183, 92, 59, 26, 16, 42),
                       5: (18, 14, 37, 126, 344, 968, 1739, 1280, 518, 219, 81, 66, 25, 10, 6, 7),
                       10: (2, 6, 24, 42, 183, 776, 1714, 1052, 264, 87, 30, 11, 5, 2, 0, 0)}),
    _brb_class('12_25bn', 'USD 12-25 billion', 12e9, 25e9, page=25, counts_page=32,
               stats={1: (.058, .034, .771), 3: (.038, .03, .123), 5: (.033, .029, .089), 10: (.029, .027, .061)},
               counts={1: (176, 94, 154, 257, 553, 1111, 1513, 1182, 643, 331, 205, 157, 95, 56, 35, 176),
                       3: (52, 37, 113, 175, 468, 1172, 1685, 1183, 541, 266, 156, 74, 56, 22, 18, 29),
                       5: (15, 30, 67, 133, 345, 1064, 1798, 1140, 442, 195, 90, 39, 23, 14, 1, 6),
                       10: (1, 2, 20, 53, 223, 789, 1629, 814, 246, 99, 24, 4, 2, 0, 0, 3)}),
    _brb_class('above_25bn', 'above USD 25 billion', 25e9, None, page=25, counts_page=32,
               stats={1: (.036, .027, .181), 3: (.024, .022, .109), 5: (.021, .02, .086), 10: (.017, .018, .061)},
               counts={1: (212, 88, 143, 281, 536, 981, 1289, 942, 532, 328, 189, 136, 63, 40, 32, 114),
                       3: (83, 44, 105, 197, 470, 1027, 1398, 946, 474, 221, 112, 55, 20, 23, 8, 14),
                       5: (44, 29, 67, 134, 365, 952, 1457, 808, 381, 143, 55, 18, 10, 5, 3, 1),
                       10: (2, 5, 24, 72, 260, 662, 1070, 582, 166, 37, 6, 0, 0, 0, 0, 0)}),
    _brb_class('above_50bn', 'above USD 50 billion', 50e9, None, page=25, counts_page=32,
               stats={1: (.023, .021, .163), 3: (.012, .015, .103), 5: (.01, .015, .083), 10: (.008, .011, .058)},
               counts={1: (100, 48, 64, 124, 251, 421, 542, 373, 213, 126, 82, 56, 28, 14, 10, 32),
                       3: (43, 17, 49, 91, 230, 461, 571, 364, 198, 69, 34, 14, 2, 7, 2, 0),
                       5: (29, 18, 28, 49, 169, 412, 616, 305, 126, 41, 11, 5, 1, 0, 0, 0),
                       10: (0, 3, 11, 34, 108, 296, 415, 189, 33, 10, 0, 0, 0, 0, 0, 0)}),
)

# The 2026 exhibits are histograms annotated with the sample, mean and standard
# deviation; the bar heights are not printed. Exhibit 1 of the May paper also gives
# real growth (mean 3.7%, standard deviation 10.6%); the nominal figures are used,
# so no inflation assumption enters these comparisons.
SALES_GROWTH_2026 = (
    {'id': 'bbr2026b_2_5bn', 'source': 'bbr2026b', 'label': 'USD 2-5 billion', 'low': 2e9, 'high': 5e9,
     'exhibit': 'Exhibit 1', 'page': 3,
     'horizons': {5: {'mean': .069, 'standard_deviation': .111, 'observations': 19264}}},
    {'id': 'bbr2026_8_12bn', 'source': 'bbr2026', 'label': 'USD 8-12 billion', 'low': 8e9, 'high': 12e9,
     'exhibit': 'Exhibit 5', 'page': 6,
     'horizons': {5: {'mean': .057, 'standard_deviation': .096, 'observations': 4385}}},
)

MARGIN_NOTE = ('The Base Rate Book reports how persistent operating margins are by sector (Exhibit 5, p62). It '
               'gives no distribution of company margins, so no margin percentile is shown.')
MEMO_MARGIN_NOTE = ('The Base Rate Book reports how persistent operating margins are by sector (Exhibit 5, p62). It '
                    'gives no distribution of company margins, so the memo shows no margin percentile.')
_NORMAL = NormalDist()


def price_level(year):
    """CPI-U annual average for a calendar year, extended at the stated inflation outside the table."""
    first, last = min(CPI_U), max(CPI_U)
    if year > last:
        return CPI_U[last] * (1 + INFLATION) ** (year - last)
    if year < first:
        return CPI_U[first] / (1 + INFLATION) ** (first - year)
    return CPI_U[year]


def _classes():
    return SALES_GROWTH_2026 + SALES_GROWTH_2016


def reference_class(revenue, fiscal_year, years):
    """The newest published class containing these starting sales at this horizon, or None.

    Starting sales are restated in the class's dollars. Among classes of the same paper
    the narrowest wins (above USD 50 billion over above USD 25 billion).
    """
    if not isinstance(revenue, (int, float)) or isinstance(revenue, bool) or not revenue > 0:
        return None
    candidates = []
    for item in _classes():
        if years not in item['horizons']:
            continue
        source = SOURCES[item['source']]
        restated = revenue * price_level(source['price_year']) / price_level(fiscal_year)
        if restated >= item['low'] and (item['high'] is None or restated < item['high']):
            width = (item['high'] - item['low']) if item['high'] is not None else math.inf
            candidates.append(((source['published'], -width, item['low']), item, restated))
    if not candidates:
        return None
    _, item, restated = max(candidates, key=lambda entry: entry[0])
    return item, restated


def _bin_position(growth, counts):
    total = sum(counts)
    edges = (None,) + BIN_EDGES + (None,)
    filled = [index for index, count in enumerate(counts) if count]
    lowest, highest = edges[filled[0]], edges[filled[-1] + 1]
    position = {}
    if highest is not None and growth >= highest:
        position['above_all_observations'] = True
    if lowest is not None and growth < lowest:
        position['below_all_observations'] = True
    below = 0
    for index, count in enumerate(counts):
        low, high = edges[index], edges[index + 1]
        if high is not None and growth >= high:
            below += count
            continue
        if low is None or high is None:
            if count:
                # An open tail bin has no width to interpolate in: report its range.
                position.update(percentile=None, percentile_bounds=[100 * below / total, 100 * (below + count) / total])
            else:
                position['percentile'] = 100 * below / total
            return position
        position['percentile'] = 100 * (below + count * (growth - low) / (high - low)) / total
        return position
    position['percentile'] = 100.0
    return position


def _bin_quantile(share, counts):
    """Growth at this cumulative share, interpolated within a bin; None inside an open tail."""
    total = sum(counts)
    edges = (None,) + BIN_EDGES + (None,)
    target, cumulative = share * total, 0
    for index, count in enumerate(counts):
        if count and cumulative + count >= target:
            low, high = edges[index], edges[index + 1]
            if low is None or high is None:
                return None
            return low + (high - low) * (target - cumulative) / count
        cumulative += count
    return None


def _ordinal(number):
    suffix = 'th' if 10 <= number % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th')
    return f'{number}{suffix}'


def describe(position):
    """Plain words for where a growth rate falls in its reference class."""
    if position.get('above_all_observations'):
        return 'above every observation'
    if position.get('below_all_observations'):
        return 'below every observation'
    value = position.get('percentile')
    if value is None:
        low, high = position['percentile_bounds']
        if high >= 100 - 1e-9:
            return f'above the {_ordinal(max(1, min(99, math.floor(low))))} percentile'
        return f'below the {_ordinal(max(1, min(99, math.ceil(high))))} percentile'
    if value < 1:
        return 'below the 1st percentile'
    if value > 99:
        return 'above the 99th percentile'
    return f'{_ordinal(int(round(value)))} percentile'


def _reference(item, restated, years):
    source = SOURCES[item['source']]
    stats = item['horizons'][years]
    real = source['growth_basis'] == 'real'
    counts = stats.get('counts')
    if counts:
        quantiles = {str(q): _bin_quantile(q / 100, counts) for q in QUANTILES}
        method = 'bins'
    else:
        quantiles = {str(q): stats['mean'] + stats['standard_deviation'] * _NORMAL.inv_cdf(q / 100) for q in QUANTILES}
        method = 'normal'
    reference = {'class_id': item['id'], 'class_label': item['label'], 'price_year': source['price_year'],
                 'starting_revenue_in_price_year': restated, 'source': item['source'], 'source_short': source['short'],
                 'exhibit': item['exhibit'], 'page': item['page'], 'growth_basis': source['growth_basis'], 'method': method,
                 'mean': stats['mean'], 'standard_deviation': stats['standard_deviation'],
                 'observations': sum(counts) if counts else stats['observations'], 'quantiles': quantiles}
    if 'median' in stats:
        reference['median'] = stats['median']
    if counts:
        reference.update(counts_exhibit=item['counts_exhibit'], counts_page=item['counts_page'])
    if real:
        reference['nominal_quantiles_at_stated_inflation'] = {
            key: None if value is None else (1 + value) * (1 + INFLATION) - 1 for key, value in quantiles.items()}
    return reference


def position_in(reference, item_stats, growth):
    """Where one compound annual growth rate falls in a reference class."""
    compared = (1 + growth) / (1 + INFLATION) - 1 if reference['growth_basis'] == 'real' else growth
    if reference['method'] == 'bins':
        position = _bin_position(compared, item_stats['counts'])
    else:
        z = (compared - reference['mean']) / reference['standard_deviation']
        position = {'percentile': 100 * _NORMAL.cdf(z), 'z': z}
    position = dict(position, compound_growth=growth, compared_growth=compared)
    position['description'] = describe(position)
    return position


def _fiscal_year(plan):
    """Calendar year in which the revenue anchor's fiscal year ends, else the valuation date's year."""
    dates = plan.get('input_dates') if isinstance(plan.get('input_dates'), dict) else {}
    revenue = dates.get('revenue') if isinstance(dates.get('revenue'), dict) else {}
    for text in (revenue.get('end_date'), plan.get('valuation_date')):
        if isinstance(text, str) and len(text) >= 4 and text[:4].isdigit():
            return int(text[:4])
    return None


def references(revenue, currency, fiscal_year):
    """Reference classes at each horizon for these starting sales, or a status explaining their absence."""
    if currency != 'USD':
        return {'status': 'not_applicable',
                'reason': f'The published size classes are in US dollars and this plan is in {currency or "an unstated currency"}.'}
    if fiscal_year is None or not isinstance(revenue, (int, float)) or isinstance(revenue, bool) or not revenue > 0:
        return {'status': 'not_applicable', 'reason': 'The plan has no positive starting revenue with a fiscal year.'}
    found = {}
    for years in HORIZONS:
        match = reference_class(revenue, fiscal_year, years)
        if match:
            found[str(years)] = _reference(match[0], match[1], years)
    if not found:
        return {'status': 'not_applicable', 'reason': 'No published class covers these starting sales.'}
    used = {reference['source'] for reference in found.values()}
    return {'status': 'computed', 'currency': currency, 'starting_revenue': revenue, 'fiscal_year': fiscal_year,
            'inflation_assumption': INFLATION, 'inflation_basis': INFLATION_BASIS, 'price_index': CPI_BASIS,
            'references': found,
            'sources': {key: {field: SOURCES[key][field] for field in ('citation', 'universe', 'published')}
                        for key in sorted(used)}}


def _stats(reference):
    item = next(item for item in _classes() if item['id'] == reference['class_id'])
    return item['horizons']


def _path_positions(revenue, path, refs):
    rows = {}
    for key, reference in refs.items():
        years = int(key)
        if len(path) < years or not path[years - 1] > 0:
            continue
        growth = (path[years - 1] / revenue) ** (1 / years) - 1
        rows[key] = dict(position_in(reference, _stats(reference)[years], growth), years=years)
    return rows


def _requirement_path(result, revenue):
    """The base revenue path at the price-requirement growth shift, as the engine projects it.

    The engine adds the shift to every explicit forecast year; steady-state years keep
    terminal growth.
    """
    rows = (result.get('price_requirements') or {}).get('rows') or []
    row = next((row for row in rows if row.get('input') == 'revenue_growth'), None)
    base = (result.get('scenarios') or {}).get('base') or {}
    flows = base.get('cashflows') or []
    if not row or row.get('status') not in ('solved', 'not_reachable') or not flows:
        return None
    shift = row['needed'] if row['status'] == 'solved' else row['at']
    explicit = min(base.get('explicit_forecast_years') or len(flows), len(flows))
    path = []
    for year, flow in enumerate(flows, 1):
        revenue *= 1 + flow['revenue_growth'] + (shift if year <= explicit else 0.0)
        path.append(revenue)
    return {'status': row['status'], 'shift': shift, 'path': path}


def compare(plan, result):
    """Each scenario's compound revenue growth at each published horizon, with its base-rate percentile."""
    if not isinstance(plan, dict) or not isinstance(result, dict) or not result.get('scenarios'):
        return None
    revenue = (plan.get('actuals') or {}).get('revenue')
    output = references(revenue, result.get('currency') or plan.get('currency'), _fiscal_year(plan))
    if output['status'] != 'computed':
        return output
    refs = output['references']
    output['scenarios'] = {}
    for name in ('bear', 'base', 'bull'):
        flows = (result['scenarios'].get(name) or {}).get('cashflows') or []
        output['scenarios'][name] = _path_positions(revenue, [flow['revenue'] for flow in flows], refs)
    requirement = _requirement_path(result, revenue)
    if requirement:
        output['price_requirement'] = {'status': requirement['status'], 'growth_shift': requirement['shift'],
                                       'horizons': _path_positions(revenue, requirement['path'], refs)}
    output['margins'] = MARGIN_NOTE
    output['basis'] = ('Compound annual revenue growth from the last reported revenue to each horizon of the computed '
                       'revenue path, placed in the published distribution of companies with the same starting '
                       'sales. A description of the plan against history; no forecast input is set from it.')
    return output


def _rounded(value):
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_rounded(item) for item in value]
    return value


def plan_context(reported):
    """Reference distributions for the plan author, before a plan exists; empty when none apply."""
    reported = reported if isinstance(reported, dict) else {}
    output = references((reported.get('actuals') or {}).get('revenue'), reported.get('currency'),
                        _fiscal_year(reported))
    if output.get('status') != 'computed':
        return ''
    view = {'starting_revenue': output['starting_revenue'], 'fiscal_year': output['fiscal_year'],
            'inflation_assumption': output['inflation_assumption'],
            'horizons': {key: {field: reference[field] for field in
                               ('class_label', 'price_year', 'growth_basis', 'method', 'mean', 'median',
                                'standard_deviation', 'observations', 'quantiles',
                                'nominal_quantiles_at_stated_inflation', 'source_short', 'exhibit', 'page')
                               if field in reference}
                         for key, reference in output['references'].items()}}
    return ('\nSales-growth base rates for companies of this starting size (published reference data; Python prints '
            'where each scenario falls, and no plan is checked against them):\n' + json.dumps(_rounded(view)))


def result_context(result):
    """The computed comparison for a plan correction; empty when none was computed."""
    comparison = (result or {}).get('base_rates') if isinstance(result, dict) else None
    if not isinstance(comparison, dict) or comparison.get('status') != 'computed':
        return ''
    view = {'scenarios': comparison['scenarios'], 'price_requirement': comparison.get('price_requirement'),
            'references': {key: {field: reference[field] for field in
                                 ('class_label', 'growth_basis', 'method', 'mean', 'standard_deviation', 'source_short')}
                           for key, reference in comparison['references'].items()}}
    return ('\nRevenue growth against base rates (published reference data; no plan is checked against it):\n' +
            json.dumps(_rounded(view)))


def summary_note(comparison, which, years):
    """Percentile phrase for a price-requirement cell: ``which`` is 'house' or 'needed'."""
    if not isinstance(comparison, dict) or comparison.get('status') != 'computed':
        return ''
    rows = (comparison['scenarios'].get('base') if which == 'house' else
            (comparison.get('price_requirement') or {}).get('horizons')) or {}
    published = [int(key) for key in rows if int(key) <= (years or 0)]
    if not published:
        return ''
    horizon = max(published)
    return f' ({rows[str(horizon)]["description"]} of the {horizon}-year base rate)'


def _class_words(label):
    """'above USD 50 billion' as the memo writes it: 'above $50B'."""
    scale = {'million': 'M', 'billion': 'B'}
    label = re.sub(r'USD (\d[\d.]*)-(\d[\d.]*) (million|billion)',
                   lambda match: f'${match[1]}-{match[2]}{scale[match[3]]}', label)
    return re.sub(r'(?:USD )?(\d[\d.]*) (million|billion)', lambda match: f'${match[1]}{scale[match[2]]}', label)


def _short(position):
    """'7.1% (68th)': compound growth and its percentile, for a table cell."""
    words = position['description'].replace(' percentile', '').replace('the ', '')
    return f'{position["compound_growth"]:.1%} ({words})'


def memo_exhibit(comparison, style=None):
    """The memo exhibit and its method notes: (lines, notes)."""
    if not isinstance(comparison, dict):
        return [], []
    heading = ['', '### Revenue growth against base rates', '']
    if comparison.get('status') != 'computed':
        return heading + [f'Not shown. {comparison.get("reason", "")}'.strip()], []
    amount = style.amount if style is not None else (lambda value: f'{value / 1e9:,.1f}B')
    refs = comparison['references']
    keys = sorted(refs, key=int)
    lines = heading + [
        f'Compound annual revenue growth from fiscal {comparison["fiscal_year"]} revenue of '
        f'{amount(comparison["starting_revenue"])}. Each cell gives the growth and its percentile among companies of '
        'the same starting size.', '',
        '| Case | ' + ' | '.join(f'{key} years' for key in keys) + ' |', '|---|' + '---:|' * len(keys)]
    def row(label, positions):
        return f'| {label} | ' + ' | '.join(_short(positions[key]) if key in positions else 'n/a' for key in keys) + ' |'
    for name in ('bear', 'base', 'bull'):
        lines.append(row(name.title(), comparison['scenarios'].get(name) or {}))
    requirement = comparison.get('price_requirement')
    if requirement:
        shift = f'{requirement["growth_shift"] * 100:+.1f} points a year'
        label = (f'Growth the price needs ({shift})' if requirement['status'] == 'solved' else
                 f'Closest to the price ({shift}; it cannot reach the price)')
        lines.append(row(label, requirement['horizons']))
    lines.append('| Reference class mean, standard deviation | ' + ' | '.join(
        f'{refs[key]["mean"]:.1%}, {refs[key]["standard_deviation"]:.1%}' for key in keys) + ' |')
    notes = ['The base-rate table describes the plan against history. No forecast input is set from it.']
    groups = {}
    for key in keys:
        groups.setdefault(refs[key]['class_id'], []).append(key)
    real = any(refs[key]['growth_basis'] == 'real' for key in keys)
    for class_keys in groups.values():
        reference = refs[class_keys[0]]
        method = ('Percentiles interpolate within the published five-point bins.' if reference['method'] == 'bins' else
                  'Percentiles use a normal approximation from the published mean and standard deviation; the paper '
                  'notes that actual growth rates have fatter tails.')
        counts = (f', with bin counts from its {reference["counts_exhibit"].lower()}, p{reference["counts_page"]}'
                  if reference.get('counts_page') else '')
        spans = (', '.join(class_keys[:-1]) + ' and ' + class_keys[-1]) if len(class_keys) > 1 else class_keys[0]
        source = comparison.get('sources', {}).get(reference['source']) or SOURCES[reference['source']]
        observations = ', '.join(f'{refs[key]["observations"]:,} at {key} years' for key in class_keys)
        notes.append(f'Base rates for {spans} years: starting sales {_class_words(reference["class_label"])} in {reference["price_year"]} '
                     f'dollars, {amount(reference["starting_revenue_in_price_year"])} here, among {source["universe"]}. '
                     f'Growth is {"after inflation" if reference["growth_basis"] == "real" else "nominal"}, and the '
                     f'observations are {observations}. {method} {source["citation"]}, {reference["exhibit"]}, '
                     f'p{reference["page"]}{counts}.')
    if real:
        inflation = comparison.get('inflation_assumption', INFLATION)
        notes.append(f'Plan growth is restated after inflation at {inflation:.1%} a year, the Federal Reserve\'s longer-run '
                     'inflation goal (FOMC, Statement on Longer-Run Goals and Monetary Policy Strategy). One more point of '
                     'inflation lowers real growth by about one point. Starting sales are restated with the US CPI-U. The '
                     'mean and standard deviation row is real growth.')
    notes.append(MEMO_MARGIN_NOTE)
    return lines, notes


def memo_lines(comparison, currency='USD'):
    """The memo exhibit followed by its notes as bullets."""
    lines, notes = memo_exhibit(comparison)
    return lines + ([''] + ['- ' + note for note in notes] if notes else [])

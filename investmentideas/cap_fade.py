"""Mauboussin's industry fade rates and their equivalent years of excess returns.

Source: Michael J. Mauboussin and Dan Callahan, "Competitive Advantage Period: The
Neglected Value Driver", Counterpoint Global, 14 April 2026. Exhibit 25 (p41) gives the
implied fade rate in ROIC by GICS industry group, 1970-2024, from five-year
correlations; financials and real estate are not in it. Page 33 gives the conversion
used here, from Holland: value is the same whether the excess spread fades
exponentially at rate f or lasts 1/f years and then stops, so a fade rate of 0.20 is
five more years of excess returns. Footnote 130 adds that his own simplified fade
formula gives shorter equivalents (7.5, 3.6 and 2.2 years at fade rates 0.10, 0.20
and 0.30), so 1/f is the longer of the two readings.

The plan maps a company to Damodaran's US industries for its bottom-up beta. This
module maps each of those industries to an Exhibit 25 group. An industry that spans
groups with different fade rates, or is outside his sample, is left unmapped, and a
company with any unmapped industry gets no fade row.
"""
from __future__ import annotations

import math
import re

SOURCE = ('Mauboussin and Callahan, "Competitive Advantage Period: The Neglected Value Driver" '
          '(April 2026), Exhibit 25 and p33')

# Exhibit 25: implied fade rate based on five-year correlations, 1970-2024.
FADE_RATES = {
    'Consumer Staples Distribution & Retail': .10,
    'Food Beverage & Tobacco': .10,
    'Household & Personal Products': .11,
    'Hotels Restaurants & Leisure': .13,
    'Consumer Services': .13,
    'Health Care Equipment & Services': .15,
    'Technology Hardware & Equipment': .17,
    'Consumer Durables & Apparel': .17,
    'Media': .19,
    'Commercial Services & Supplies': .19,
    'Retailing': .19,
    'Automobiles & Components': .21,
    'Materials': .21,
    'Transportation': .21,
    'Software & Services': .22,
    'Pharmaceuticals & Biotechnology': .22,
    'Capital Goods': .25,
    'Semiconductors & Semiconductor Equipment': .25,
    'Consumer Discretionary Distribution & Retail': .26,
    'Energy': .29,
    'Telecommunication Services': .29,
    'Utilities': .30,
}

_OUTSIDE = 'financials and real estate are not in Exhibit 25'
_SPANS = 'spans industry groups with different fade rates'

# Damodaran US industry name (as in his January table) -> Exhibit 25 group, or the
# reason it has none. Names are matched after normalization (see _key), because
# the parsed table splits some names across lines ("Insur ance").
_INDUSTRIES = {
    'Advertising': 'Media',
    'Aerospace/Defense': 'Capital Goods',
    'Air Transport': 'Transportation',
    'Apparel': 'Consumer Durables & Apparel',
    'Auto & Truck': 'Automobiles & Components',
    'Auto Parts': 'Automobiles & Components',
    'Bank (Money Center)': None,
    'Banks (Regional)': None,
    'Beverage (Alcoholic)': 'Food Beverage & Tobacco',
    'Beverage (Soft)': 'Food Beverage & Tobacco',
    'Broadcasting': 'Media',
    'Brokerage & Investment Banking': None,
    'Building Materials': 'Capital Goods',
    'Business & Consumer Services': 'Commercial Services & Supplies',
    'Cable TV': 'Media',
    'Chemical (Basic)': 'Materials',
    'Chemical (Diversified)': 'Materials',
    'Chemical (Specialty)': 'Materials',
    'Coal & Related Energy': 'Energy',
    'Computer Services': 'Software & Services',
    'Computers/Peripherals': 'Technology Hardware & Equipment',
    'Construction Supplies': 'Materials',
    'Diversified': _SPANS,
    'Drugs (Biotechnology)': 'Pharmaceuticals & Biotechnology',
    'Drugs (Pharmaceutical)': 'Pharmaceuticals & Biotechnology',
    'Education': 'Consumer Services',
    'Electrical Equipment': 'Capital Goods',
    'Electronics (Consumer & Office)': 'Technology Hardware & Equipment',
    'Electronics (General)': 'Technology Hardware & Equipment',
    'Engineering/Construction': 'Capital Goods',
    'Entertainment': 'Media',
    'Environmental & Waste Services': 'Commercial Services & Supplies',
    'Farming/Agriculture': 'Food Beverage & Tobacco',
    'Financial Svcs. (Non-bank & Insurance)': None,
    'Food Processing': 'Food Beverage & Tobacco',
    'Food Wholesalers': 'Consumer Staples Distribution & Retail',
    'Furn/Home Furnishings': 'Consumer Durables & Apparel',
    'Green & Renewable Energy': _SPANS,
    'Healthcare Products': 'Health Care Equipment & Services',
    'Healthcare Support Services': 'Health Care Equipment & Services',
    'Heathcare Information and Technology': 'Health Care Equipment & Services',
    'Homebuilding': 'Consumer Durables & Apparel',
    'Hospitals/Healthcare Facilities': 'Health Care Equipment & Services',
    'Hotel/Gaming': 'Hotels Restaurants & Leisure',
    'Household Products': 'Household & Personal Products',
    'Information Services': 'Commercial Services & Supplies',
    'Insurance (General)': None,
    'Insurance (Life)': None,
    'Insurance (Prop/Cas.)': None,
    'Investments & Asset Management': None,
    'Machinery': 'Capital Goods',
    'Metals & Mining': 'Materials',
    'Office Equipment & Services': 'Technology Hardware & Equipment',
    'Oil/Gas (Integrated)': 'Energy',
    'Oil/Gas (Production and Exploration)': 'Energy',
    'Oil/Gas Distribution': 'Energy',
    'Oilfield Svcs/Equip.': 'Energy',
    'Packaging & Container': 'Materials',
    'Paper/Forest Products': 'Materials',
    'Power': 'Utilities',
    'Precious Metals': 'Materials',
    'Publishing & Newspapers': 'Media',
    'R.E.I.T.': None,
    'Real Estate (Development)': None,
    'Real Estate (General/Diversified)': None,
    'Real Estate (Operations & Services)': None,
    'Recreation': 'Consumer Durables & Apparel',
    'Reinsurance': None,
    'Restaurant/Dining': 'Hotels Restaurants & Leisure',
    'Retail (Automotive)': 'Consumer Discretionary Distribution & Retail',
    'Retail (Building Supply)': 'Consumer Discretionary Distribution & Retail',
    'Retail (Distributors)': 'Consumer Discretionary Distribution & Retail',
    # Discount and warehouse chains are staples retail under GICS since 2023
    # (fade 0.10); department stores and broadline retail are discretionary (0.26).
    'Retail (General)': _SPANS,
    'Retail (Grocery and Food)': 'Consumer Staples Distribution & Retail',
    'Retail (REITs)': None,
    'Retail (Special Lines)': 'Consumer Discretionary Distribution & Retail',
    'Rubber& Tires': 'Automobiles & Components',
    'Semiconductor': 'Semiconductors & Semiconductor Equipment',
    'Semiconductor Equip': 'Semiconductors & Semiconductor Equipment',
    'Shipbuilding & Marine': 'Transportation',
    'Shoe': 'Consumer Durables & Apparel',
    'Software (Entertainment)': 'Media',
    'Software (Internet)': 'Software & Services',
    'Software (System & Application)': 'Software & Services',
    'Steel': 'Materials',
    'Telecom (Wireless)': 'Telecommunication Services',
    'Telecom. Equipment': 'Technology Hardware & Equipment',
    'Telecom. Services': 'Telecommunication Services',
    'Tobacco': 'Food Beverage & Tobacco',
    'Transportation': 'Transportation',
    'Transportation (Railroads)': 'Transportation',
    'Trucking': 'Transportation',
    'Utility (General)': 'Utilities',
    'Utility (Water)': 'Utilities',
}


def _key(name):
    return re.sub(r'[^a-z0-9]', '', name.lower()) if isinstance(name, str) else ''


_BY_KEY = {_key(name): (name, group) for name, group in _INDUSTRIES.items()}


def industry_group(name):
    """(Exhibit 25 group or None, reason when None) for one Damodaran industry name."""
    entry = _BY_KEY.get(_key(name))
    if entry is None:
        return None, 'not in the industry mapping'
    group = entry[1]
    if group is None:
        return None, _OUTSIDE
    if group not in FADE_RATES:
        return None, group
    return group, None


def industry_fade(sectors):
    """The equivalent extra years of excess returns for a bottom-up industry mix.

    ``sectors`` is the plan's ``[{"sector": name, "weight": w}, ...]``. Each industry's
    fade rate f converts to 1/f years (p33); the company's figure is the
    weight-averaged years, because the value of a spread that lasts 1/f years adds
    across businesses while fade rates do not. Rounded to whole years for the engine.
    """
    if not isinstance(sectors, list) or not sectors:
        return {'status': 'unmapped', 'reason': 'the plan has no bottom-up industry mapping for its beta'}
    mapped, unmapped, total = [], [], 0.0
    for item in sectors:
        name = item.get('sector') if isinstance(item, dict) else None
        weight = item.get('weight') if isinstance(item, dict) else None
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or not 0 < weight <= 1:
            return {'status': 'unmapped', 'reason': 'the industry weights are not usable'}
        group, reason = industry_group(name)
        if group is None:
            unmapped.append(f'{name} ({reason})')
            continue
        rate = FADE_RATES[group]
        mapped.append({'sector': name, 'weight': float(weight), 'industry_group': group, 'fade_rate': rate,
                       'equivalent_years': 1 / rate})
        total += weight
    if unmapped:
        return {'status': 'unmapped', 'reason': 'no Exhibit 25 industry group for ' + '; '.join(unmapped)}
    if not math.isclose(total, 1, abs_tol=1e-6):
        return {'status': 'unmapped', 'reason': 'the industry weights do not sum to one'}
    years = sum(item['weight'] * item['equivalent_years'] for item in mapped)
    return {'status': 'mapped', 'sectors': mapped, 'equivalent_years': years,
            'extension_years': int(math.floor(years + .5)), 'source': SOURCE,
            'basis': 'Each industry\'s fade rate f is read as 1/f more years of the forecast-end economics before new '
                     'capital earns its cost (Holland\'s equivalence, p33); a mix weights those years by the bottom-up '
                     'beta weights and rounds to whole years. His simplified fade formula gives shorter equivalents '
                     '(footnote 130), so this is the longer reading.'}

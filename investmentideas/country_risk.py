"""Revenue-weighted country risk premium for one convention-panel row; no network access.

Damodaran ("Country Risk: Determinants, Measures and Implications", 2025 edition)
measures a company's exposure to country risk by where it earns its revenue: the
premium of each country or region it sells in, weighted by its revenue there. The
house discount rate uses the S&P 500 implied premium over the Treasury rate. That
premium is solved from the prices of US companies that already sell abroad, so a
premium added for foreign revenue counts some of that risk twice against the market
benchmark. The result therefore only feeds a labelled panel row; the house value is
unchanged.

``country_risk_reference(sources, reported)`` maps the filing's reported revenue by
geography (or a segment axis whose members are all regions) to Damodaran's dated
country table and his GDP-weighted regional sheet:

* A reported member is mapped by its name through the dictionaries below. Anything
  not listed is unmapped: its revenue is excluded from the weights and disclosed.
* A member that names one country takes that country's premium from the dated
  country page. A member that names a region takes the GDP-weighted average of the
  regional sheet's countries in it; for one of his regions this is his published
  regional average. Residual members ("Other countries", "Rest of world", "Non-US")
  take the GDP-weighted average of every country the same breakdown does not report
  separately.
* Premiums are measured over the US premium (0.23 points in January 2026), because
  the house cost of equity pairs the Treasury rate with the implied premium over it.
  US revenue therefore adds nothing, and a country rated above the US adds less than
  nothing.
* Members on one axis can overlap (Oracle reports the Americas and the United
  States). Python uses a set of members whose revenue adds up to reported revenue
  within 0.1%, preferring the set with the least unmapped revenue, then the least
  revenue in a worldwide residual, then the most members; a set that leaves out a
  member of a larger set that also adds up is dropped. In a set that adds up, a
  country beside a region that contains it lies outside that region (AT&T's Latin
  America beside Mexico); such a set must add up to the reporting unit. Two regions
  that overlap end the set. Members reported within a business segment are not used.
* A region in the chosen set is then split by any single country the filing reports
  for the same year on another breakdown, when the country's revenue is smaller:
  Apple's Americas segment less its US revenue by country is Canada and Latin America.

Labels are mapped by name. A company's own definition of a region (Apple's Europe
segment includes India, the Middle East and Africa) is not read.
"""
from __future__ import annotations

from itertools import combinations
import math
import re

COUNTRY_TABLE = 'damodaran_country_risk_premiums'
REGIONAL_TABLE = 'damodaran_regional_country_risk'
RECONCILE_TOLERANCE = .001
# Half of the usual reporting unit ($1 million) per member.
EXACT_MARGIN_PER_MEMBER = 500_000
MIN_MAPPED_SHARE = .5
MAX_MEMBERS = 16
# The two tables are one publication when their US premium and mature-market
# premium agree to the country page's rounding (hundredths of a point).
VINTAGE_TOLERANCE = .00006

# SEC "country:" members use ISO 3166 alpha-2 codes; values are Damodaran's names.
ISO_COUNTRIES = {
    'US': 'United States', 'CA': 'Canada', 'MX': 'Mexico', 'BR': 'Brazil', 'AR': 'Argentina', 'CL': 'Chile',
    'CO': 'Colombia', 'PE': 'Peru', 'GB': 'United Kingdom', 'IE': 'Ireland', 'DE': 'Germany', 'FR': 'France',
    'NL': 'Netherlands', 'BE': 'Belgium', 'LU': 'Luxembourg', 'CH': 'Switzerland', 'AT': 'Austria', 'IT': 'Italy',
    'ES': 'Spain', 'PT': 'Portugal', 'SE': 'Sweden', 'NO': 'Norway', 'DK': 'Denmark', 'FI': 'Finland',
    'PL': 'Poland', 'CZ': 'Czech Republic', 'HU': 'Hungary', 'GR': 'Greece', 'TR': 'Turkey', 'IL': 'Israel',
    'SA': 'Saudi Arabia', 'AE': 'United Arab Emirates', 'QA': 'Qatar', 'EG': 'Egypt', 'ZA': 'South Africa',
    'NG': 'Nigeria', 'CN': 'China', 'HK': 'Hong Kong', 'MO': 'Macao', 'TW': 'Taiwan', 'JP': 'Japan', 'KR': 'Korea',
    'IN': 'India', 'SG': 'Singapore', 'MY': 'Malaysia', 'TH': 'Thailand', 'ID': 'Indonesia', 'PH': 'Philippines',
    'VN': 'Vietnam', 'AU': 'Australia', 'NZ': 'New Zealand'}

# Member names that name one country, after normalization (see _key).
NAMED_COUNTRIES = {
    'unitedstates': 'United States', 'us': 'United States', 'usa': 'United States', 'domestic': 'United States',
    'china': 'China', 'chinamainland': 'China', 'mainlandchina': 'China', 'japan': 'Japan', 'canada': 'Canada',
    'mexico': 'Mexico', 'brazil': 'Brazil', 'unitedkingdom': 'United Kingdom', 'uk': 'United Kingdom',
    'germany': 'Germany', 'france': 'France', 'ireland': 'Ireland', 'netherlands': 'Netherlands',
    'switzerland': 'Switzerland', 'italy': 'Italy', 'spain': 'Spain', 'israel': 'Israel', 'india': 'India',
    'taiwan': 'Taiwan', 'korea': 'Korea', 'southkorea': 'Korea', 'hongkong': 'Hong Kong', 'singapore': 'Singapore',
    'australia': 'Australia'}

AMERICAS = ('North America', 'Central and South America', 'Caribbean')
LATIN_AMERICA = ('Central and South America', 'Caribbean')
ASIA_PACIFIC = ('Asia', 'Australia & New Zealand')
# Damodaran's EMEA grouping: Western Europe, the Middle East and Africa by GDP.
EMEA = ('Western Europe', 'Middle East', 'Africa')
WORLD = 'every region'

_AMERICAS_CHOICE = 'The Americas are Damodaran\'s North America, Central and South America and Caribbean regions.'
_RESIDUAL_CHOICE = 'A residual or remainder member covers the countries the same breakdown does not report separately.'
_GDP_CHOICE = 'Within a region, countries are weighted by GDP, not by the company\'s sales.'

# Member names that name a region: the countries it covers. ``residual`` members
# cover their scope less every country the other members of the breakdown cover.
REGION_LABELS = {
    'americas': {'regions': AMERICAS, 'choice': _AMERICAS_CHOICE},
    'northamerica': {'regions': ('North America',)},
    'uscanada': {'regions': ('North America',)},
    'unitedstatesandcanada': {'regions': ('North America',)},
    'americasexcludingunitedstates': {'regions': AMERICAS, 'exclude': ('United States',)},
    'americasexcludingus': {'regions': AMERICAS, 'exclude': ('United States',)},
    'westernhemisphereexcludingus': {'regions': AMERICAS, 'exclude': ('United States',)},
    'westernhemisphereexcludingunitedstates': {'regions': AMERICAS, 'exclude': ('United States',)},
    'otheramericas': {'regions': AMERICAS, 'exclude': ('United States',)},
    'latinamerica': {'regions': LATIN_AMERICA},
    'latinamericaandcaribbean': {'regions': LATIN_AMERICA},
    'latinamericaandthecaribbean': {'regions': LATIN_AMERICA},
    'centralandsouthamerica': {'regions': LATIN_AMERICA},
    'southamerica': {'regions': LATIN_AMERICA},
    'europe': {'regions': ('Western Europe',)},
    'westerneurope': {'regions': ('Western Europe',)},
    'easterneurope': {'regions': ('Eastern Europe & Russia',)},
    'emea': {'regions': EMEA},
    'europemiddleeastandafrica': {'regions': EMEA},
    'europemiddleeastafrica': {'regions': EMEA},
    'middleeast': {'regions': ('Middle East',)},
    'africa': {'regions': ('Africa',)},
    'middleeastandafrica': {'regions': ('Middle East', 'Africa')},
    'asia': {'regions': ('Asia',)},
    'asiapacific': {'regions': ASIA_PACIFIC},
    'apac': {'regions': ASIA_PACIFIC},
    'asiapacificafrica': {'regions': (*ASIA_PACIFIC, 'Africa')},
    # China's premium; the wider coverage keeps the residual members from counting them again.
    'greaterchina': {'countries': ('China', 'Hong Kong', 'Macao', 'Taiwan'), 'premium_country': 'China'},
    'chinaincludinghongkong': {'countries': ('China', 'Hong Kong'), 'premium_country': 'China'},
    'restofasiapacific': {'residual': ASIA_PACIFIC},
    'otherasiapacific': {'residual': ASIA_PACIFIC},
    'nonus': {'residual': WORLD}, 'nonusa': {'residual': WORLD}, 'foreign': {'residual': WORLD},
    'foreigncountries': {'residual': WORLD}, 'international': {'residual': WORLD},
    'otherinternational': {'residual': WORLD}, 'othercountries': {'residual': WORLD},
    'allothercountries': {'residual': WORLD}, 'restofworld': {'residual': WORLD},
    'restoftheworld': {'residual': WORLD}, 'allothergeographicalareas': {'residual': WORLD},
    'othergeographicalareas': {'residual': WORLD}, 'other': {'residual': WORLD}}


def _key(member):
    """A member QName as a dictionary key: 'aapl:RestOfAsiaPacificSegmentMember' -> 'restofasiapacific'."""
    local = member.split(':', 1)[-1]
    local = re.sub(r'Member$', '', local)
    local = re.sub(r'(?:Segment|Region|Geographic(?:al)?Area)$', '', local)
    local = re.sub(r'^RegionOf', '', local)
    return re.sub(r'[^a-z0-9]', '', local.lower())


def mapping_for(member):
    """The country or region a reported member names, or None when it is not in the dictionaries."""
    if not isinstance(member, str) or ' within ' in member:
        return None
    if member.startswith('country:'):
        name = ISO_COUNTRIES.get(member.split(':', 1)[1].upper())
        return {'countries': (name,)} if name else None
    key = _key(member)
    if key in NAMED_COUNTRIES:
        return {'countries': (NAMED_COUNTRIES[key],)}
    spec = REGION_LABELS.get(key)
    return dict(spec) if spec else None


def _dataset(sources, name):
    for source in sources if isinstance(sources, list) else []:
        data = source.get('data') if isinstance(source, dict) else None
        if isinstance(data, dict) and data.get('dataset') == name:
            return source
    return None


def _label(member):
    """A reader's label for a member: 'aapl:RestOfAsiaPacificSegmentMember' -> 'Rest of Asia Pacific'."""
    if member.startswith('country:'):
        return ISO_COUNTRIES.get(member.split(':', 1)[1].upper(), member)
    local = re.sub(r'Member$', '', member.split(':', 1)[-1])
    local = re.sub(r'^RegionOf', '', re.sub(r'Segment$', '', local))
    if _key(member) in ('nonus', 'nonusa'):
        return 'Non-US'
    words = re.sub(r'(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', ' ', local).split()
    return ' '.join(word if index == 0 or word not in ('Of', 'And', 'The', 'In', 'Including', 'Excluding')
                    else word.lower() for index, word in enumerate(words))


def _tables(sources, end):
    """(axis, source id, [(member, revenue)]) for each breakdown table with the anchor year."""
    found, seen = [], set()
    for source in sources if isinstance(sources, list) else []:
        data = source.get('data') if isinstance(source.get('data'), dict) else {}
        tables = source.get('breakdown_tables') or data.get('breakdown_tables') or {}
        for axis in ('geography', 'segment'):
            table = tables.get(axis) if isinstance(tables, dict) else None
            ends = (table or {}).get('fiscal_year_ends') or []
            if end not in ends:
                continue
            index = ends.index(end)
            members = []
            for entry in table.get('members') or []:
                values = entry.get('revenue') if isinstance(entry, dict) and isinstance(entry.get('revenue'), list) else []
                revenue = values[index] if index < len(values) else None
                member = entry.get('member') if isinstance(entry, dict) else None
                if (isinstance(member, str) and ' within ' not in member and isinstance(revenue, (int, float))
                        and not isinstance(revenue, bool) and revenue > 0):
                    members.append((member, float(revenue)))
            key = (axis, tuple(members))
            if members and key not in seen:
                seen.add(key)
                found.append((axis, source.get('id'), members))
    return found


class _Premiums:
    """Premiums over the US premium from the dated country page and the GDP-weighted regional sheet."""

    def __init__(self, country, regional):
        self.country = {row['country']: row['country_risk_premium'] for row in country['countries']}
        self.us = country['us_country_risk_premium']
        self.regional = regional
        self.rows, self.regions = {}, {}
        if regional is not None:
            self.rows = {row['country']: row for row in regional['countries']}
            for row in regional['countries']:
                self.regions.setdefault(row['region'], set()).add(row['country'])
            self.regional_us = regional['us_country_risk_premium']

    def covered(self, spec):
        """The countries a non-residual spec covers, or None when the regional sheet is needed and absent."""
        names = set(spec.get('countries') or ())
        for region in spec.get('regions') or ():
            if region not in self.regions:
                return None
            names |= self.regions[region]
        return names - set(spec.get('exclude') or ())

    def scope(self, scope):
        if self.regional is None:
            return None
        if scope == WORLD:
            return set(self.rows)
        if any(region not in self.regions for region in scope):
            return None
        return set().union(*(self.regions[region] for region in scope))

    def premium(self, names, spec=None):
        """(premium over the US premium, basis) for a set of countries, or None."""
        single = (spec or {}).get('premium_country') or (next(iter(names)) if len(names) == 1 else None)
        if single in self.country:
            return self.country[single] - self.us, 'country page'
        if self.regional is None or not names or any(name not in self.rows for name in names):
            return None
        gdp = sum(self.rows[name]['gdp_millions'] for name in names)
        weighted = sum(self.rows[name]['gdp_millions'] * self.rows[name]['country_risk_premium'] for name in names) / gdp
        return weighted - self.regional_us, 'GDP-weighted regional sheet'


def _and(items):
    items = list(items)
    return items[0] if len(items) == 1 else ', '.join(items[:-1]) + ' and ' + items[-1]


def _describe(entry):
    spec, names, removed = entry['spec'], entry['names'], entry['removed']
    if spec.get('premium_country'):
        return f'{spec["premium_country"]}\'s premium, covering {_and(sorted(names))}'
    if len(names) == 1 and not spec.get('residual') and not spec.get('regions'):
        return next(iter(names))
    less = (' less ' + _and(removed)) if removed else ''
    if spec.get('residual'):
        scope = 'the world' if spec['residual'] == WORLD else _and(spec['residual'])
        if len(names) == 1:
            return f'{next(iter(names))}: {scope} less the countries reported separately'
        return f'{scope} less the countries reported separately, {len(names)} countries, GDP-weighted'
    text = _and(spec['regions'])
    if spec.get('exclude'):
        text += ' less ' + _and(spec['exclude'])
    if len(names) == 1:
        return f'{next(iter(names))}: {text}{less}'
    weighted = len(spec['regions']) > 1 or spec.get('exclude') or removed
    return text + less + (', GDP-weighted' if weighted else ', Damodaran\'s regional average')


def _resolve(members, premiums, axis):
    """Entries for one candidate set of members: (resolved, unmapped), or None when the set is not a partition.

    The set adds up to reported revenue, so its members are disjoint by that
    identity: a country reported beside a region that contains it is taken to lie
    outside that region (AT&T's "Latin America" beside Mexico), and a residual
    covers what no other member does. Two regions that overlap, or one country
    reported twice, are ambiguous and end the candidate.
    """
    entries, unmapped, residual = [], [], None
    for member, revenue in members:
        spec = mapping_for(member)
        if spec is None:
            if axis == 'segment':
                return None  # A segment axis counts only when every member is a region.
            unmapped.append({'member': member, 'label': _label(member), 'revenue': revenue,
                             'reason': 'not in the country and region dictionaries'})
            continue
        if spec.get('residual'):
            if residual is not None:
                return None
            residual = {'member': member, 'label': _label(member), 'revenue': revenue, 'spec': spec, 'removed': []}
            continue
        names = premiums.covered(spec)
        if names is None:
            unmapped.append({'member': member, 'label': _label(member), 'revenue': revenue,
                             'reason': 'the regional sheet was not collected'})
            continue
        entries.append({'member': member, 'label': _label(member), 'revenue': revenue, 'spec': spec,
                        'names': names, 'removed': []})
    for entry in entries:
        entry['single'] = len(entry['names']) == 1 and not entry['spec'].get('regions')
    single_names = [next(iter(entry['names'])) for entry in entries if entry['single']]
    if len(set(single_names)) != len(single_names):
        return None
    composites = [entry for entry in entries if not entry['single']]
    for entry in composites:
        inside = [name for name in single_names if name in entry['names']]
        entry['names'] = entry['names'] - set(inside)
        entry['removed'] = inside
        if not entry['names']:
            return None
    for index, entry in enumerate(composites):
        if any(entry['names'] & other['names'] for other in composites[index + 1:]):
            return None
    if residual is not None:
        scope = premiums.scope(residual['spec']['residual'])
        if scope is None:
            unmapped.append({'member': residual['member'], 'label': residual['label'], 'revenue': residual['revenue'],
                             'reason': 'the regional sheet was not collected'})
        else:
            residual['names'] = scope - set().union(*(entry['names'] for entry in entries))
            if not residual['names']:
                return None
            entries.append(residual)
    return entries, unmapped


def _refine(entries, pool, margin):
    """Split a region or residual by single countries the filing reports elsewhere for the same year.

    Apple reports the Americas as a segment and the United States in its revenue by
    country: Americas less the United States is the difference of two reported
    amounts. A country splits a member only when its revenue is below the member's
    by more than the reconciliation margin; a country reported twice with different
    amounts is not used.
    """
    by_name = {}
    for member, revenue, name, source_id in pool:
        by_name.setdefault(name, {})[revenue] = (member, revenue, source_id)
    usable = {name: next(iter(items.values())) for name, items in by_name.items() if len(items) == 1}
    taken = {name for entry in entries if len(entry['names']) == 1 for name in entry['names']}
    refined = []
    for entry in entries:
        inside = sorted(((name, *usable[name]) for name in entry['names'] if name in usable and name not in taken
                         and len(entry['names']) > 1), key=lambda item: -item[2])
        split = sum(item[2] for item in inside)
        rest = entry['names'] - {item[0] for item in inside}
        if not inside or not rest or entry['revenue'] - split <= margin:
            refined.append(entry)
            continue
        for name, member, revenue, source_id in inside:
            refined.append({'member': member, 'label': _label(member), 'revenue': revenue, 'source': source_id,
                            'spec': {'countries': (name,)}, 'names': {name}, 'removed': [],
                            'derived': f'reported separately; splits {entry["label"]}'})
        refined.append(dict(entry, names=rest, removed=[*entry['removed'], *(item[0] for item in inside)],
                            revenue=entry['revenue'] - split,
                            label=entry['label'] + ' less ' + _and(_label(item[1]) for item in inside),
                            derived=f'{entry["label"]} less the countries reported separately: the difference of reported amounts'))
    return refined


def _exposures(entries, premiums, unmapped):
    exposures, choices = [], []
    for entry in entries:
        value = premiums.premium(entry['names'], entry['spec'])
        if value is None:
            unmapped.append({'member': entry['member'], 'label': entry['label'], 'revenue': entry['revenue'],
                             'reason': 'no premium for these countries in the collected tables'})
            continue
        premium, basis = value
        exposure = {'member': entry['member'], 'label': entry['label'], 'revenue': entry['revenue'],
                    'mapped_to': _describe(entry), 'premium_over_us': premium, 'premium_basis': basis}
        if entry.get('derived'):
            exposure['derived'] = entry['derived']
        exposures.append(exposure)
        if entry['spec'].get('choice'):
            choices.append(entry['spec']['choice'])
        if entry['spec'].get('residual') or (basis != 'country page' and (entry['removed'] or entry.get('derived'))):
            choices.append(_RESIDUAL_CHOICE)
    if any(item['premium_basis'] != 'country page' for item in exposures):
        choices.append(_GDP_CHOICE)
    return exposures, list(dict.fromkeys(choices))


def _not_applicable(reason, refs):
    return {'status': 'not_applicable', 'reason': reason, 'source_refs': refs}


def country_risk_reference(sources, reported):
    """The revenue-weighted country risk premium for the convention panel, or None without the country table."""
    country = _dataset(sources, COUNTRY_TABLE)
    if country is None:
        return None
    regional = _dataset(sources, REGIONAL_TABLE)
    refs = [ref for ref in (country.get('id'), (regional or {}).get('id')) if ref]
    notes = []
    if regional is not None and (
            abs(regional['data']['us_country_risk_premium'] - country['data']['us_country_risk_premium']) > VINTAGE_TOLERANCE or
            abs(regional['data']['mature_market_premium'] - country['data']['mature_market_premium']) > VINTAGE_TOLERANCE):
        notes.append('The regional sheet\'s US and mature-market premiums differ from the dated country page, so it is '
                     'not used; regions and residual members are unmapped.')
        regional, refs = None, refs[:1]
    reported = reported if isinstance(reported, dict) else {}
    total = (reported.get('actuals') or {}).get('revenue')
    end = ((reported.get('input_dates') or {}).get('revenue') or {}).get('end_date')
    if not isinstance(total, (int, float)) or isinstance(total, bool) or total <= 0 or not end:
        return _not_applicable('No reported annual revenue to weight the premium against.', refs)
    tables = _tables(sources, end)
    if not tables:
        return _not_applicable(f'The filings report no revenue by geography for the fiscal year ending {end}.', refs)
    premiums = _Premiums(country['data'], regional['data'] if regional else None)
    margin = RECONCILE_TOLERANCE * total
    candidates = []
    for axis, source_id, members in tables:
        if len(members) > MAX_MEMBERS:
            continue
        for size in range(1, len(members) + 1):
            for subset in combinations(members, size):
                geographic = sum(revenue for _, revenue in subset)
                if abs(geographic - total) > margin:
                    continue
                resolved = _resolve(subset, premiums, axis)
                if resolved is None or not resolved[0]:
                    continue
                entries, unmapped = resolved
                # Reading a country beside its region as outside it rests on the sum alone,
                # so that sum must be exact to the reporting unit. Oracle's US, UK, Japan,
                # EMEA and Asia Pacific came within 0.07% of revenue by coincidence.
                if (any(entry['removed'] for entry in entries) and
                        abs(geographic - total) > EXACT_MARGIN_PER_MEMBER * len(subset)):
                    continue
                unmapped_share = sum(item['revenue'] for item in unmapped) / geographic
                world = sum(entry['revenue'] for entry in entries if entry['spec'].get('residual') == WORLD) / geographic
                score = (round(unmapped_share, 9), round(world, 9), -len(subset), 0 if axis == 'geography' else 1,
                         tuple(member for member, _ in subset))
                candidates.append((score, axis, source_id, geographic, entries, unmapped,
                                   frozenset(member for member, _ in subset)))
    # A set that leaves out a member of a larger set that also adds up only
    # dropped revenue inside the tolerance (AT&T's $114 million of other areas).
    candidates = [item for item in candidates
                  if not any(other[2] == item[2] and other[1] == item[1] and item[6] < other[6] for other in candidates)]
    if not candidates:
        return _not_applicable(f'No set of non-overlapping geographic members adds up to reported revenue for the '
                               f'fiscal year ending {end} (within {RECONCILE_TOLERANCE:.1%}).', refs)
    _, axis, source_id, geographic, entries, unmapped, _ = min(candidates, key=lambda item: item[0])
    chosen = {entry['member'] for entry in entries} | {item['member'] for item in unmapped}
    pool = []
    for _, table_source, members in tables:
        for member, revenue in members:
            spec = mapping_for(member)
            if member in chosen or not spec or spec.get('residual') or spec.get('regions') or len(spec['countries']) != 1:
                continue
            pool.append((member, revenue, spec['countries'][0], table_source))
    refined = _refine(entries, pool, margin)
    used_sources = [source_id, *(entry['source'] for entry in refined if entry.get('source'))]
    exposures, choices = _exposures(refined, premiums, unmapped)
    mapped = sum(item['revenue'] for item in exposures)
    if not exposures or mapped < MIN_MAPPED_SHARE * geographic:
        return _not_applicable(' '.join([f'Less than half of reported revenue maps to a country or region '
                                         f'({mapped / geographic:.0%}).', *notes]), refs)
    for item in exposures:
        item['share_of_revenue'] = item['revenue'] / geographic
    for item in unmapped:
        item['share_of_revenue'] = item['revenue'] / geographic
    weighted = sum(item['premium_over_us'] * item['revenue'] for item in exposures) / mapped
    if not math.isfinite(weighted):
        return _not_applicable('The weighted premium is not finite.', refs)
    country_data = country['data']
    return {'status': 'calculated', 'weighted_premium': weighted,
            'fiscal_year_end': end, 'axis': axis, 'reported_revenue': float(total), 'geographic_revenue': geographic,
            'mapped_share_of_revenue': mapped / geographic, 'exposures': exposures, 'unmapped': unmapped,
            'choices': choices, 'notes': notes,
            'country_table': {'observed_at': country_data['observed_at'],
                              'us_country_risk_premium': country_data['us_country_risk_premium'],
                              'mature_market_premium': country_data['mature_market_premium'], 'source_ref': country.get('id')},
            'regional_table': ({'observed_at': regional['data']['observed_at'], 'source_ref': regional.get('id'),
                                'gdp_year': regional['data'].get('gdp_year')} if regional else None),
            'source_refs': list(dict.fromkeys(ref for ref in (*used_sources, *refs) if ref)),
            'basis': ('Damodaran\'s operation-weighted country risk: each country\'s or region\'s premium over the US '
                      'premium, weighted by the company\'s reported revenue there. Unmapped revenue is excluded from the '
                      'weights. The premium is added to the cost of equity (premium times revenue exposure, not scaled by '
                      'beta); the cost of debt and the capital weights are unchanged.')}


def prompt_summary(data):
    """The country and regional tables as presented to a model: their dates and base premiums only.

    Python applies the tables in the convention panel; the full rows stay in the
    archived sources and are not sent with each request.
    """
    if not isinstance(data, dict) or data.get('dataset') not in (COUNTRY_TABLE, REGIONAL_TABLE):
        return data
    keys = ('dataset', 'observed_at', 'date_precision', 'date_basis', 'gdp_year', 'mature_market_premium',
            'us_country_risk_premium', 'caveats')
    summary = {key: data[key] for key in keys if key in data}
    summary['rows'] = {'countries': len(data.get('countries') or []), 'regions': len(data.get('regions') or []),
                       'note': 'Full table archived; Python applies it only in the convention panel\'s country risk row.'}
    return summary


def render_parts(panel, ref_label=lambda ref: f'[{ref}]'):
    """The exposure table under the country risk row and its method notes: (lines, notes)."""
    if not isinstance(panel, dict):
        return [], []
    row = next((item for item in panel.get('rows') or [] if item.get('name') == 'country_risk_premium_revenue_weighted'), None)
    if row is None:
        note = panel.get('country_risk_note')
        return ([f'No country risk row is shown. {note}'] if note else []), []
    reference = row.get('reference') or {}
    tables = [ref for ref in reference.get('table_refs') or [] if ref]
    filings = ' '.join(ref_label(ref) for ref in reference.get('source_refs') or [] if ref not in tables)
    us = reference.get('us_premium')
    # The shares and premiums below are Python's arithmetic on the cited sources, so they are
    # cited as computed from them (review of the merged code, 2026-11-27).
    lines = [f'Revenue shares by geography for the fiscal year ending {reference.get("fiscal_year_end")}'
             + (f' {filings}' if filings else '') + ', with Damodaran\'s country and regional premiums over the US premium'
             + (' ' + ' '.join(ref_label(ref) for ref in tables) if tables else '') + '.', '',
             '| Region | Share of revenue | Premium over US (points) | Damodaran basis |', '|---|---:|---:|---|']
    for item in reference.get('exposures') or []:
        where = item.get('mapped_to')
        lines.append(f'| {item["label"]} | {item["share_of_revenue"]:.1%} | {item["premium_over_us"] * 100:+.2f} | '
                     + (where if where and where != item['label'] else '') + ' |')
    for item in reference.get('unmapped') or []:
        lines.append(f'| {item["label"]} | {item["share_of_revenue"]:.1%} | unmapped | excluded from the weights |')
    if row.get('status') in ('calculated', 'fragile_spread'):
        lines += ['', f'The weighted premium of {row["weighted_country_risk_premium"] * 100:.2f} points is added to the cost '
                      f'of equity in every year and in the terminal value. With equity at {row["equity_weight"]:.1%} of '
                      f'capital, every discount rate rises by {row["discount_rate_change"] * 100:.2f} points.']
    notes = ['The country risk row is an exhibit. It is not ranked with the convention rows and carries no probability.',
             'The S&P 500 implied premium already reflects US companies\' foreign revenue, so the country risk row '
             'overstates the adjustment against the market benchmark.']
    if isinstance(us, (int, float)):
        notes.append(f'Country premiums are stated over the US premium of {us * 100:.2f} points.')
    notes += [_CHOICE_TEXT.get(text, text) for text in [*(reference.get('choices') or []), *(reference.get('notes') or [])]]
    return lines, notes


# Saved choice sentences in the memo's voice.
_CHOICE_TEXT = {_GDP_CHOICE: 'Within a region, countries are weighted by GDP because the company does not report sales by country.'}


def render_note(panel, ref_label=lambda ref: f'[{ref}]'):
    """The country risk exhibit as text: the exposure table, then its notes."""
    lines, notes = render_parts(panel, ref_label)
    return '\n'.join(lines + ([''] + notes if notes else []))

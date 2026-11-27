"""Damodaran's revenue-weighted country risk premium as one convention-panel row (critique F5).

Fixtures are trimmed from the public files downloaded on 2026-11-26: the dated country
page (real markup for a subset of rows) and real rows of the GDP-weighted regional sheet,
written here into a workbook laid out like his. No network or model calls. Expected
numbers are computed by hand in each test, not by the code under test.
"""
import copy
from html import escape
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock
from zipfile import ZipFile, ZIP_DEFLATED

from investmentideas import country_risk, macro, pipeline, valuation
from investmentideas.macro import (
    AA_URL, BETAS_URL, COUNTRY_RISK_URL, ERP_URL, REGIONAL_RISK_URL, gather_us_calibration,
    parse_country_risk_premiums, parse_regional_risk_premiums)

FIXTURES = Path(__file__).parent / 'fixtures'
PAGE = (FIXTURES / 'damodaran_ctryprem_2026-01-05_trimmed.html').read_bytes()
REGIONAL = json.loads((FIXTURES / 'damodaran_regional_2026-02-16_rows.json').read_text())
EXAMPLE = Path(__file__).resolve().parents[2] / 'examples' / 'AAPL-2026-11-06-revised' / 'assumptions.json'
MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
DOC_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG_REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
AS_OF = '2026-11-26'


def regional_workbook(rows=None, *, modified=None, header=None, tamper=None, core=True, labels=None):
    """A workbook laid out like the "Regional Weighted Averages" sheet: blocks of countries, each closed by a total."""
    rows = copy.deepcopy(REGIONAL['rows'] if rows is None else rows)
    labels = labels or REGIONAL['region_total_labels']
    strings = []

    def text(value):
        if value not in strings:
            strings.append(value)
        return strings.index(value)

    def cell(ref, value, formula=None, shared=False):
        if shared:
            return f'<c r="{ref}" t="s"><v>{text(value)}</v></c>'
        body = (f'<f>{escape(formula)}</f>' if formula else '') + f'<v>{value!r}</v>'
        return f'<c r="{ref}">{body}</c>'

    headers = header or ['Country', REGIONAL['gdp_header'], "Moody's rating", 'Adj. Default Spread', 'Equity Risk Premium',
                         'Country Risk Premium', 'Corporate Tax Rate', 'Region', 'GDP Weight']
    xml = ['<row r="1">' + ''.join(cell(column + '1', value, shared=True) for column, value in zip('ABCDEFGHI', headers)) + '</row>']
    number = 2
    order = list(dict.fromkeys(row['region'] for row in rows))
    for region in order:
        block = [row for row in rows if row['region'] == region]
        first = number
        for row in block:
            xml.append(f'<row r="{number}">' + cell(f'A{number}', row['country'], shared=True) +
                       cell(f'B{number}', row['gdp_millions']) + cell(f'C{number}', row['rating'], shared=True) +
                       cell(f'D{number}', row['default_spread']) +
                       cell(f'E{number}', row['equity_risk_premium'], formula=f'F{number}+$E$3') +
                       cell(f'F{number}', row['country_risk_premium']) + cell(f'G{number}', row['tax_rate']) +
                       cell(f'H{number}', region, shared=True) + '</row>')
            number += 1
        gdp = sum(row['gdp_millions'] for row in block)
        premium = sum(row['gdp_millions'] * row['country_risk_premium'] for row in block) / gdp
        if tamper and tamper[0] == region:
            premium += tamper[1]
        xml.append(f'<row r="{number}">' + cell(f'A{number}', labels[region], shared=True) +
                   cell(f'B{number}', gdp, formula=f'SUM(B{first}:B{number - 1})') +
                   cell(f'F{number}', premium, formula=f'SUM(L{first}:L{number - 1})') + '</row>')
        number += 1
    # The published summary table repeats the regions below a "Region" header.
    number += 2
    xml.append(f'<row r="{number}">' + cell(f'A{number}', 'Region', shared=True) +
               cell(f'B{number}', 'Weighted Average: ERP', shared=True) + '</row>')
    xml.append(f'<row r="{number + 1}"><c r="A{number + 1}" t="str"><f>A2</f><v>Africa</v></c>'
               f'<c r="F{number + 1}"><v>1</v></c></row>')
    workbook = ('<workbook xmlns="' + MAIN + '" xmlns:r="' + DOC_REL + '"><workbookPr/><sheets>'
                '<sheet name="Explanation and FAQ" sheetId="7" r:id="rId1"/>'
                '<sheet name="Regional Weighted Averages" sheetId="10" r:id="rId7"/></sheets></workbook>')
    relations = ('<Relationships xmlns="' + PKG_REL + '">'
                 '<Relationship Id="rId1" Type="' + DOC_REL + '/worksheet" Target="worksheets/sheet1.xml"/>'
                 '<Relationship Id="rId7" Type="' + DOC_REL + '/worksheet" Target="worksheets/sheet7.xml"/></Relationships>')
    output = io.BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('xl/workbook.xml', workbook)
        archive.writestr('xl/_rels/workbook.xml.rels', relations)
        archive.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="' + MAIN + '"><sheetData/></worksheet>')
        archive.writestr('xl/worksheets/sheet7.xml', '<worksheet xmlns="' + MAIN + '"><sheetData>' + ''.join(xml) +
                         '</sheetData></worksheet>')
        archive.writestr('xl/sharedStrings.xml', '<sst xmlns="' + MAIN + '">' +
                         ''.join(f'<si><t>{escape(value)}</t></si>' for value in strings) + '</sst>')
        if core:
            archive.writestr('docProps/core.xml',
                             '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                             'xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                             f'<dcterms:modified xsi:type="dcterms:W3CDTF">{modified or REGIONAL["modified"]}</dcterms:modified>'
                             '</cp:coreProperties>')
    return output.getvalue()


def table_sources():
    country = {'id': 'S40', 'kind': 'macro_calibration', 'data': parse_country_risk_premiums(PAGE, as_of=AS_OF)}
    regional = {'id': 'S41', 'kind': 'macro_calibration', 'data': parse_regional_risk_premiums(regional_workbook(), as_of=AS_OF)}
    return [country, regional]


def filing(tables, end='2026-09-26', source_id='S4'):
    """A filing source carrying breakdown tables as evidence_tables.breakdown_tables builds them (latest year first)."""
    output = {}
    for axis, members in tables.items():
        output[axis] = {'fiscal_year_ends': [end, '2025-09-27'],
                        'members': [{'member': member, 'label': member, 'revenue': [revenue, None]} for member, revenue in members]}
    return {'id': source_id, 'kind': 'sec_filing', 'data': {}, 'breakdown_tables': output}


def reported(revenue, end='2026-09-26'):
    return {'actuals': {'revenue': revenue}, 'input_dates': {'revenue': {'end_date': end}}}


APPLE = {'segment': [('aapl:AmericasSegmentMember', 198083e6), ('aapl:EuropeSegmentMember', 127604e6),
                     ('aapl:GreaterChinaSegmentMember', 82378e6), ('aapl:JapanSegmentMember', 30908e6),
                     ('aapl:RestOfAsiaPacificSegmentMember', 39861e6)],
         'geography': [('country:US', 167746e6), ('country:CN', 82378e6), ('aapl:OtherCountriesMember', 228710e6)]}
APPLE_REVENUE = 478834e6


def page_premium(name):
    return next(row['country_risk_premium'] for row in parse_country_risk_premiums(PAGE, as_of=AS_OF)['countries']
                if row['country'] == name) - .0023


def gdp_premium(names):
    rows = [row for row in REGIONAL['rows'] if row['country'] in names]
    assert len(rows) == len(set(names)), set(names) - {row['country'] for row in rows}
    us = next(row['country_risk_premium'] for row in REGIONAL['rows'] if row['country'] == 'United States')
    return sum(row['gdp_millions'] * row['country_risk_premium'] for row in rows) / sum(row['gdp_millions'] for row in rows) - us


def region(*names):
    return [row['country'] for row in REGIONAL['rows'] if row['region'] in names]


class CountryPageTests(unittest.TestCase):
    def test_trimmed_real_page_is_dated_and_keeps_every_row(self):
        data = parse_country_risk_premiums(PAGE, as_of=AS_OF)
        self.assertEqual(data['dataset'], 'damodaran_country_risk_premiums')
        self.assertEqual(data['observed_at'], '2026-01-05')
        self.assertEqual(len(data['countries']), 39)
        self.assertAlmostEqual(data['mature_market_premium'], .0423, places=9)
        self.assertAlmostEqual(data['us_country_risk_premium'], .0023, places=9)
        rows = {row['country']: row for row in data['countries']}
        self.assertAlmostEqual(rows['China']['country_risk_premium'], .0091, places=9)
        self.assertAlmostEqual(rows['China']['equity_risk_premium'], .0514, places=9)
        self.assertEqual(rows['Turkey']['row_note'], 'updated February 2026')
        self.assertIn('Yemen, Republic', rows)  # a name broken across source lines
        self.assertEqual(rows['Germany']['country_risk_premium'], 0)
        self.assertEqual(data['provenance']['update_lines'], ['January 5 2026', 'January 2026'])

    def test_a_page_dated_after_as_of_or_without_one_date_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'after as_of'):
            parse_country_risk_premiums(PAGE, as_of='2026-01-04')
        for old, new in ((b'Last updated: January 5, 2026', b'Last updated: soon'),
                         (b'Last updated: January 2026', b'Last updated: February 2026')):
            with self.subTest(new=new), self.assertRaisesRegex(ValueError, 'Last updated'):
                parse_country_risk_premiums(PAGE.replace(old, new), as_of=AS_OF)

    def test_changed_headers_merged_cells_and_bad_rows_are_refused(self):
        cases = ((b'Country Risk\n                Premium', b'CRP', 'headers changed'),
                 (b'>Japan</td>', b' colspan=2>Japan</td>', 'merged cells'),
                 (b'>Japan</td>', b'>China</td>', 'repeats a country'),
                 (b'>United States</td>', b'>United States of America</td>', 'no United States row'))
        for old, new, message in cases:
            self.assertIn(old, PAGE)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                parse_country_risk_premiums(PAGE.replace(old, new, 1), as_of=AS_OF)
        # A total premium that is not the mature premium plus the country premium.
        broken = PAGE.replace(b'>5.14%<', b'>6.14%<', 1)
        with self.assertRaisesRegex(ValueError, 'mature-market premium'):
            parse_country_risk_premiums(broken, as_of=AS_OF)
        with self.assertRaisesRegex(ValueError, 'percentage'):
            parse_country_risk_premiums(PAGE.replace(b'>0.91%<', b'>n/a<', 1), as_of=AS_OF)
        with self.assertRaises(ValueError):
            parse_country_risk_premiums(b' ' * 3_000_001, as_of=AS_OF)


class RegionalSheetTests(unittest.TestCase):
    def test_regions_are_his_gdp_weighted_averages_and_dated_by_the_file(self):
        data = parse_regional_risk_premiums(regional_workbook(), as_of=AS_OF)
        self.assertEqual(data['observed_at'], '2026-02-16')
        self.assertIn('last-modified', data['date_basis'])
        self.assertEqual(data['gdp_year'], 2024)
        self.assertEqual(len(data['countries']), len(REGIONAL['rows']))
        names = [item['region'] for item in data['regions']]
        self.assertEqual(len(names), 9)
        eastern = next(item for item in data['regions'] if item['region'] == 'Eastern Europe & Russia')
        self.assertEqual(eastern['total_label'], 'Eastern Europe')
        asia = next(item for item in data['regions'] if item['region'] == 'Asia')
        self.assertAlmostEqual(asia['country_risk_premium'], gdp_premium(region('Asia')) + data['us_country_risk_premium'], places=12)
        self.assertAlmostEqual(data['mature_market_premium'], .0423, places=9)

    def test_a_total_that_is_not_the_weighted_average_or_a_changed_layout_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'GDP-weighted average'):
            parse_regional_risk_premiums(regional_workbook(tamper=('Asia', .0001)), as_of=AS_OF)
        header = ['Country', 'GDP', "Moody's rating", 'Adj. Default Spread', 'Equity Risk Premium',
                  'Country Risk Premium', 'Corporate Tax Rate', 'Region', 'GDP Weight']
        with self.assertRaisesRegex(ValueError, 'GDP header'):
            parse_regional_risk_premiums(regional_workbook(header=header), as_of=AS_OF)
        labels = dict(REGIONAL['region_total_labels'], Asia='Asia Pacific')
        with self.assertRaisesRegex(ValueError, 'does not close'):
            parse_regional_risk_premiums(regional_workbook(labels=labels), as_of=AS_OF)
        with self.assertRaisesRegex(ValueError, 'after as_of'):
            parse_regional_risk_premiums(regional_workbook(modified='2026-12-01T00:00:00Z'), as_of=AS_OF)
        with self.assertRaisesRegex(ValueError, 'core properties'):
            parse_regional_risk_premiums(regional_workbook(core=False), as_of=AS_OF)
        with self.assertRaisesRegex(ValueError, 'not an XLSX'):
            parse_regional_risk_premiums(PAGE, as_of=AS_OF)


class CollectionTests(unittest.TestCase):
    def payloads(self):
        from tests.public_runner.test_macro import aa_csv, beta_html, workbook
        return {ERP_URL: workbook(), BETAS_URL: beta_html(), AA_URL: aa_csv(),
                COUNTRY_RISK_URL: PAGE, REGIONAL_RISK_URL: regional_workbook()}

    def test_country_tables_join_the_free_collection_only_when_asked(self):
        payloads = self.payloads()
        fetch = Mock(side_effect=lambda url: payloads[url])
        sources, gaps = gather_us_calibration(fetch, as_of=AS_OF, country_risk=True)
        self.assertEqual(gaps, [])
        datasets = [source['data']['dataset'] for source in sources]
        self.assertEqual(datasets[3:], ['damodaran_country_risk_premiums', 'damodaran_regional_country_risk'])
        self.assertEqual([source['archive_extension'] for source in sources[3:]], ['.html', '.xlsx'])
        self.assertTrue(all(source['raw_document'] == payloads[source['url']] for source in sources))
        fetch = Mock(side_effect=lambda url: payloads[url])
        sources, _ = gather_us_calibration(fetch, as_of=AS_OF)
        self.assertEqual(fetch.call_count, 3)

    def test_an_unavailable_country_page_is_a_gap_and_nothing_is_invented(self):
        payloads = self.payloads()
        def fetch(url):
            if url == COUNTRY_RISK_URL:
                raise OSError('connection reset by a provider message')
            return payloads[url]
        sources, gaps = gather_us_calibration(fetch, as_of=AS_OF, country_risk=True)
        self.assertEqual(len(sources), 4)
        self.assertEqual([gap['source'] for gap in gaps], ['damodaran_country_risk_premiums'])
        self.assertNotIn('provider message', repr(gaps))

    def test_the_pipeline_asks_for_the_country_tables(self):
        source = Path(pipeline.__file__).read_text()
        self.assertIn('gather_us_calibration(_fetch_calibration_bytes, country_risk=True)', source)


class MappingTests(unittest.TestCase):
    def reference(self, tables, revenue, extra=(), **kwargs):
        sources = [filing(tables, **kwargs), *table_sources(), *extra]
        return country_risk.country_risk_reference(sources, reported(revenue, kwargs.get('end', '2026-09-26')))

    def test_apple_segments_with_us_revenue_split_out_of_the_americas(self):
        result = self.reference(APPLE, APPLE_REVENUE)
        self.assertEqual(result['status'], 'calculated')
        self.assertEqual(result['axis'], 'segment')
        rows = {item['label']: item for item in result['exposures']}
        self.assertEqual(set(rows), {'United States', 'Americas less United States', 'Europe', 'Greater China', 'Japan',
                                     'Rest of Asia Pacific'})
        self.assertEqual(rows['Americas less United States']['revenue'], 198083e6 - 167746e6)
        americas = [name for name in region('North America', 'Central and South America', 'Caribbean') if name != 'United States']
        asia_pacific = [name for name in region('Asia', 'Australia & New Zealand')
                        if name not in ('China', 'Hong Kong', 'Macao', 'Taiwan', 'Japan')]
        expected = {'United States': 0.0, 'Americas less United States': gdp_premium(americas),
                    'Europe': gdp_premium(region('Western Europe')), 'Greater China': page_premium('China'),
                    'Japan': page_premium('Japan'), 'Rest of Asia Pacific': gdp_premium(asia_pacific)}
        for label, premium in expected.items():
            self.assertAlmostEqual(rows[label]['premium_over_us'], premium, places=12, msg=label)
        revenue = {'United States': 167746e6, 'Americas less United States': 198083e6 - 167746e6, 'Europe': 127604e6,
                   'Greater China': 82378e6, 'Japan': 30908e6, 'Rest of Asia Pacific': 39861e6}
        weighted = sum(revenue[label] * premium for label, premium in expected.items()) / APPLE_REVENUE
        self.assertAlmostEqual(result['weighted_premium'], weighted, places=12)
        self.assertEqual(result['unmapped'], [])
        self.assertEqual(result['source_refs'], ['S4', 'S40', 'S41'])
        self.assertTrue(any('Americas are' in choice for choice in result['choices']))

    def test_overlapping_members_use_the_region_set_then_split_it_by_reported_countries(self):
        # Oracle reports Americas/EMEA/Asia Pacific and US/UK/Germany/Japan/other on one axis.
        members = [('srt:AmericasMember', 44478e6), ('us-gaap:EMEAMember', 15297e6), ('srt:AsiaPacificMember', 7582e6),
                   ('country:US', 39835e6), ('country:GB', 2816e6), ('country:DE', 1993e6), ('country:JP', 1870e6),
                   ('orcl:OtherCountriesMember', 20843e6)]
        result = self.reference({'geography': members}, 67357e6, end='2026-05-31')
        rows = {item['label']: item for item in result['exposures']}
        self.assertEqual(set(rows), {'United States', 'Americas less United States', 'United Kingdom', 'Germany',
                                     'EMEA less United Kingdom and Germany', 'Japan', 'Asia Pacific less Japan'})
        self.assertAlmostEqual(rows['EMEA less United Kingdom and Germany']['revenue'], 15297e6 - 2816e6 - 1993e6)
        emea = [name for name in region('Western Europe', 'Middle East', 'Africa') if name not in ('United Kingdom', 'Germany')]
        self.assertAlmostEqual(rows['EMEA less United Kingdom and Germany']['premium_over_us'], gdp_premium(emea), places=12)
        self.assertAlmostEqual(rows['Germany']['premium_over_us'], -.0023, places=12)  # rated above the US

    def test_a_country_beside_its_region_lies_outside_it_only_when_the_sum_is_exact(self):
        # AT&T: Mexico beside "Latin America" and a small residual, exact to the million.
        members = [('country:US', 120219e6), ('country:MX', 4428e6), ('srt:AsiaPacificMember', 395e6),
                   ('srt:EuropeMember', 382e6), ('t:RegionOfLatinAmericaAndCaribbeanMember', 110e6),
                   ('t:AllOtherGeographicalAreasMember', 114e6)]
        result = self.reference({'geography': members}, 125648e6, end='2025-12-31')
        rows = {item['label']: item for item in result['exposures']}
        self.assertEqual(len(rows), 6)  # the $114 million residual is not dropped inside the tolerance
        latin = rows['Latin America and Caribbean']
        self.assertIn('less Mexico', latin['mapped_to'])
        self.assertAlmostEqual(latin['premium_over_us'],
                               gdp_premium([n for n in region('Central and South America', 'Caribbean') if n != 'Mexico']), places=12)
        # The same members 0.06% away from revenue are a coincidence, not a partition.
        result = self.reference({'geography': members}, 125648e6 + 80e6, end='2025-12-31')
        self.assertNotIn('Latin America and Caribbean', {item['label'] for item in result.get('exposures', [])})

    def test_us_and_non_us_take_the_world_less_the_us_and_unmapped_revenue_is_disclosed(self):
        result = self.reference({'geography': [('country:US', 19127e6), ('us-gaap:NonUsMember', 28814e6)]}, 47941e6)
        rows = {item['label']: item for item in result['exposures']}
        world = [row['country'] for row in REGIONAL['rows'] if row['country'] != 'United States']
        self.assertAlmostEqual(rows['Non-US']['premium_over_us'], gdp_premium(world), places=12)
        self.assertAlmostEqual(result['weighted_premium'], 28814 / 47941 * gdp_premium(world), places=12)
        members = [('country:US', 60e9), ('acme:WidgetLandMember', 30e9), ('country:JP', 10e9)]
        result = self.reference({'geography': members}, 100e9)
        self.assertEqual([item['label'] for item in result['unmapped']], ['Widget Land'])
        self.assertAlmostEqual(result['unmapped'][0]['share_of_revenue'], .3)
        self.assertAlmostEqual(result['weighted_premium'], 10 / 70 * page_premium('Japan'), places=12)
        result = self.reference({'geography': [('country:US', 40e9), ('acme:WidgetLandMember', 60e9)]}, 100e9)
        self.assertEqual(result['status'], 'not_applicable')
        self.assertIn('Less than half', result['reason'])

    def test_no_geographic_split_business_segments_or_unreconciled_revenue_give_a_reason(self):
        segments = {'segment': [('msft:IntelligentCloudMember', 60e9), ('msft:MorePersonalComputingMember', 40e9)]}
        cases = (({}, 'no revenue by geography'), (segments, 'adds up'),
                 ({'geography': [('country:US', 50e9), ('us-gaap:NonUsMember', 40e9)]}, 'adds up'))
        for tables, message in cases:
            with self.subTest(message=message):
                result = self.reference(tables, 100e9)
                self.assertEqual(result['status'], 'not_applicable')
                self.assertIn(message, result['reason'])
        within = {'geography': [('country:US within acme:RetailMember', 60e9), ('us-gaap:NonUsMember within acme:RetailMember', 40e9)]}
        self.assertIn('no revenue by geography', self.reference(within, 100e9)['reason'])
        self.assertIsNone(country_risk.country_risk_reference([filing(APPLE)], reported(APPLE_REVENUE)))

    def test_without_the_regional_sheet_only_single_countries_map(self):
        country = table_sources()[0]
        tables = {'geography': [('country:US', 60e9), ('country:JP', 30e9), ('srt:EuropeMember', 10e9)]}
        result = country_risk.country_risk_reference([filing(tables), country], reported(100e9))
        self.assertEqual([item['label'] for item in result['unmapped']], ['Europe'])
        self.assertAlmostEqual(result['weighted_premium'], 30 / 90 * page_premium('Japan'), places=12)
        self.assertEqual(result['source_refs'], ['S4', 'S40'])
        sources = [filing(tables), *table_sources()]
        sources[2]['data']['us_country_risk_premium'] += .001  # a different vintage
        result = country_risk.country_risk_reference(sources, reported(100e9))
        self.assertEqual([item['label'] for item in result['unmapped']], ['Europe'])
        self.assertTrue(result['notes'])

    def test_the_dictionary_maps_the_labels_the_cached_companies_report(self):
        expected = {'aapl:GreaterChinaSegmentMember': {'countries': ('China', 'Hong Kong', 'Macao', 'Taiwan'),
                                                       'premium_country': 'China'},
                    'nvda:ChinaIncludingHongKongMember': {'countries': ('China', 'Hong Kong'), 'premium_country': 'China'},
                    'meta:USCanadaMember': {'regions': ('North America',)},
                    'uber:UnitedStatesAndCanadaMember': {'regions': ('North America',)},
                    'goog:AmericasExcludingUnitedStatesMember': {'regions': country_risk.AMERICAS, 'exclude': ('United States',)},
                    'jnj:WesternHemisphereExcludingUSMember': {'regions': country_risk.AMERICAS, 'exclude': ('United States',)},
                    'jnj:AsiaPacificAfricaMember': {'regions': ('Asia', 'Australia & New Zealand', 'Africa')},
                    'cost:UnitedStatesMember': {'countries': ('United States',)},
                    'cost:OtherInternationalMember': {'residual': country_risk.WORLD},
                    'meta:RestOfWorldMember': {'residual': country_risk.WORLD},
                    'aapl:RestOfAsiaPacificSegmentMember': {'residual': country_risk.ASIA_PACIFIC},
                    'us-gaap:EMEAMember': {'regions': country_risk.EMEA},
                    'country:TW': {'countries': ('Taiwan',)}}
        for member, spec in expected.items():
            mapping = country_risk.mapping_for(member)
            with self.subTest(member=member):
                self.assertEqual({key: mapping[key] for key in spec}, spec)
        for member in ('amzn:AmazonWebServicesSegmentMember', 'wmt:WalmartInternationalMember', 'hd:PrimarySegmentMember',
                       't:LatinAmericaBusinessSegmentMember', 'country:US within jnj:MedTechMember'):
            self.assertIsNone(country_risk.mapping_for(member), member)
        # Every region the dictionary names is one of his regions, spelled as in his sheet.
        regions = {row['region'] for row in REGIONAL['rows']}
        for spec in country_risk.REGION_LABELS.values():
            named = set(spec.get('regions') or ()) | (set(spec['residual']) if spec.get('residual') not in (None, country_risk.WORLD) else set())
            self.assertLessEqual(named, regions)
        page = {row['country'] for row in parse_country_risk_premiums(PAGE, as_of=AS_OF)['countries']}
        for target in ('United States', 'China', 'Hong Kong', 'Macao', 'Taiwan', 'Japan', 'Korea', 'Mexico', 'United Kingdom'):
            self.assertIn(target, page)
            self.assertIn(target, {*country_risk.ISO_COUNTRIES.values(), *country_risk.NAMED_COUNTRIES.values()})


def glide_plan(reference):
    from tests.public_runner.test_market_consistency import plan
    value = plan()
    value['country_risk_reference'] = reference
    return value


def calculated(premium=.0075):
    return {'status': 'calculated', 'weighted_premium': premium, 'fiscal_year_end': '2025-12-31',
            'exposures': [{'label': 'Europe', 'share_of_revenue': .4, 'premium_over_us': premium / .4,
                           'mapped_to': 'Western Europe, Damodaran\'s regional average'}],
            'unmapped': [], 'choices': [], 'country_table': {'us_country_risk_premium': .0023, 'source_ref': 'S40'},
            'regional_table': {'source_ref': 'S41'}, 'source_refs': ['S4', 'S40', 'S41']}


class PanelRowTests(unittest.TestCase):
    def row(self, result):
        return next((row for row in result['convention_panel']['rows'] if row['name'] == valuation.COUNTRY_RISK_ROW), None)

    def test_the_premium_raises_the_cost_of_equity_in_every_year_and_the_house_value_is_unchanged(self):
        from tests.public_runner.test_market_consistency import plan
        house = valuation.evaluate(plan())
        weight = house['discount_rate_calculation']['equity_weight']
        result = valuation.evaluate(glide_plan(calculated(.01 / weight)))
        self.assertEqual(result['scenarios'], house['scenarios'])
        self.assertEqual(result['weighted_per_share_value'], house['weighted_per_share_value'])
        row = self.row(result)
        self.assertEqual(row['kind'], 'country_risk')
        self.assertAlmostEqual(row['discount_rate_change'], .01, places=12)
        # Equity weight times the premium is one point here, so the row equals the whole-path +1 point row.
        plus = next(item for item in result['convention_panel']['rows'] if item['name'] == 'required_return_plus_1_point')
        self.assertAlmostEqual(row['per_share_value'], plus['per_share_value'], places=9)
        self.assertLess(row['dollar_delta'], 0)
        self.assertEqual(result['convention_panel']['largest_meaningful_step'], house['convention_panel']['largest_meaningful_step'])
        json.dumps(result, allow_nan=False)

    def test_on_a_constant_rate_plan_it_equals_a_cost_of_equity_higher_by_the_premium(self):
        example = json.loads(EXAMPLE.read_text())
        premium = .0075
        house = valuation.evaluate(example)
        value = copy.deepcopy(example)
        value['country_risk_reference'] = calculated(premium)
        row = self.row(valuation.evaluate(value))
        # Independently: an ERP higher by premium / levered beta raises the cost of equity by the premium.
        calc = house['discount_rate_calculation']
        changed = copy.deepcopy(example)
        changed['discount_rate']['equity_risk_premium']['value'] += premium / calc['relevered_beta']
        changed['discount_rate']['unlevered_beta'].pop('value', None)
        changed.pop('calibration_comparisons', None)
        changed.pop('monitoring', None)
        expected = valuation.evaluate(changed)
        self.assertAlmostEqual(expected['discount_rate_calculation']['cost_of_equity'], calc['cost_of_equity'] + premium, places=12)
        self.assertAlmostEqual(row['per_share_value'], expected['scenarios']['base']['per_share_value'], places=6)
        self.assertAlmostEqual(row['equity_weight'], calc['equity_weight'], places=12)

    def test_label_and_memo_say_the_benchmark_already_carries_foreign_revenue(self):
        result = valuation.evaluate(glide_plan(calculated()))
        row = self.row(result)
        self.assertIn('already reflects US companies\' foreign revenue', row['label'])
        self.assertIn('overstates the adjustment against the market benchmark', row['label'])
        text = '\n'.join(pipeline._market_consistency_exhibits(result, 'USD'))
        self.assertIn(row['label'], text)
        # Changed 2026-11-27: the note cites its figures as computed from the sources.
        self.assertIn('Country risk row, computed from [S4] [S40] [S41]: shares of revenue by geography for the fiscal '
                      'year ending 2025-12-31 and Damodaran\'s country and regional premiums', text)
        self.assertIn('- Europe: 40.0% of revenue, +1.88 points; Western Europe', text)
        self.assertIn('every discount rate rises by', text)
        self.assertNotIn('\u2014', country_risk.render_note(result['convention_panel']) + row['label'])
        summary = pipeline.computed_summary(glide_plan(calculated()), result)
        self.assertNotIn('country risk', summary.lower())  # not ranked among the convention steps

    def test_no_split_or_no_rate_build_shows_a_note_and_no_row(self):
        reference = {'status': 'not_applicable', 'reason': 'The filings report no revenue by geography for the fiscal year ending 2025-12-31.',
                     'source_refs': ['S40']}
        result = valuation.evaluate(glide_plan(reference))
        self.assertIsNone(self.row(result))
        self.assertEqual(result['convention_panel']['country_risk_note'], reference['reason'])
        self.assertIn('Country risk row not shown: The filings report no revenue by geography',
                      '\n'.join(pipeline._market_consistency_exhibits(result, 'USD')))
        from tests.public_runner.test_market_consistency import plan
        constant = plan(glide=False, timing=False)
        constant.pop('discount_rate')
        for scenario in constant['scenarios'].values():
            scenario.update(wacc=.09, terminal_growth=.03)
        constant['country_risk_reference'] = calculated()
        result = valuation.evaluate(constant)
        self.assertIsNone(self.row(result))
        self.assertIn('equity weight', result['convention_panel']['country_risk_note'])
        plain = valuation.evaluate(plan())
        self.assertNotIn('country_risk_note', plain['convention_panel'])
        self.assertIsNone(self.row(plain))

    def test_a_malformed_reference_writes_a_note_and_never_stops_the_valuation(self):
        # Engine review (2026-11-26): an exhibit failure must not fail the valuation.
        house = valuation.evaluate(glide_plan(calculated()))['scenarios']
        for reference in ('0.01', {'status': 'calculated', 'weighted_premium': .5},
                          {'status': 'calculated', 'weighted_premium': float('nan')},
                          dict(calculated(), country_table='not a table'), dict(calculated(), exposures=7)):
            with self.subTest(reference=reference):
                result = valuation.evaluate(glide_plan(reference))
                self.assertIsNone(self.row(result))
                self.assertIn('The country risk row was not computed', result['convention_panel']['country_risk_note'])
                self.assertEqual(result['scenarios'], house)


class PipelineTests(unittest.TestCase):
    def test_the_locked_plan_pins_the_reference_and_the_valuation_shows_the_row(self):
        from tests.public_runner.test_inputs import sources as primary_sources
        from tests.public_runner.test_memo_diagnostics import rate_plan
        records = primary_sources()
        records[0]['breakdown_tables'] = filing(APPLE)['breakdown_tables']
        records.extend(table_sources())
        locked, _ = pipeline._lock_plan(rate_plan(), records)
        reference = locked['country_risk_reference']
        self.assertEqual(reference['status'], 'calculated')
        self.assertEqual(reference['source_refs'], ['S19', 'S40', 'S41'])
        again, _ = pipeline._lock_plan(copy.deepcopy(locked), records)
        self.assertEqual(again['country_risk_reference'], reference)
        result = pipeline.evaluate(locked)
        row = next(row for row in result['convention_panel']['rows'] if row['name'] == valuation.COUNTRY_RISK_ROW)
        self.assertEqual(row['status'], 'calculated')
        self.assertAlmostEqual(row['weighted_country_risk_premium'], reference['weighted_premium'], places=12)
        bare, _ = pipeline._lock_plan(rate_plan(), primary_sources())
        self.assertNotIn('country_risk_reference', bare)
        typed = rate_plan()
        typed['country_risk_reference'] = calculated(.05)  # a plan cannot supply its own premium
        locked, _ = pipeline._lock_plan(typed, records)
        self.assertEqual(locked['country_risk_reference'], reference)

    def test_prompts_carry_the_table_dates_not_every_country_row(self):
        context = pipeline._evidence_context(table_sources())
        self.assertIn('damodaran_country_risk_premiums', context)
        self.assertIn('2026-01-05', context)
        self.assertNotIn('Abu Dhabi', context)
        self.assertNotIn('gdp_millions', context)
        self.assertLess(len(context), 4000)


if __name__ == '__main__':
    unittest.main()

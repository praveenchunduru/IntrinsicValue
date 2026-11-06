"""Dated US market-calibration evidence from public files, without model calls.

``gather_us_calibration(fetch_bytes, as_of=...)`` returns ``(sources, gaps)``.
The injected fetcher accepts a public URL and returns bytes. This module owns no
network client, credentials or persistence. Each successful source includes its
original bytes in ``raw_document``, a SHA-256 hash, media type and archive suffix.
The caller assigns citation IDs and decides how to use this evidence.

The ERP parser selects the explicitly labeled sustainable-payout series and its
same-row conventional Treasury proxy. It never substitutes another ERP method,
a newer Treasury observation, a sector for an issuer, or an issuer debt yield.
Unrecognized formats and unavailable observations produce gaps, never defaults.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import io
import math
import posixpath
import re
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile


ERP_URL = 'https://pages.stern.nyu.edu/~adamodar/pc/implprem/ERPbymonth.xlsx'
BETAS_URL = 'https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/Betas.html'
AA_URL = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=BAMLC0A2CAAEY'
MAX_INPUT_BYTES = 3_000_000
_MAX_XML_BYTES = 8_000_000
_MAX_ZIP_BYTES = 16_000_000
_MAX_ROWS = 20_000
_MAIN = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
_DOC_REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
_PKG_REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
_MONTHS = ('January', 'February', 'March', 'April', 'May', 'June',
           'July', 'August', 'September', 'October', 'November', 'December')
_BETA_HEADERS = (
    'Industry Name', 'Number of firms', 'Beta', 'D/E Ratio', 'Effective Tax rate',
    'Unlevered beta', 'Cash/Firm value', 'Unlevered beta corrected for cash',
    'HiLo Risk', 'Standard deviation of equity',
    'Standard deviation in operating income (last 10 years)')


def _iso_date(value, name):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError(f'{name} must be YYYY-MM-DD')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f'{name} must be YYYY-MM-DD') from None


def _as_of(value):
    today = datetime.now(timezone.utc).date()
    result = today if value is None else value if type(value) is date else _iso_date(value, 'as_of')
    if result > today:
        raise ValueError('as_of cannot be in the future')
    return result, today


def _bytes(data):
    if not isinstance(data, (bytes, bytearray)) or not data or len(data) > MAX_INPUT_BYTES:
        raise ValueError('Calibration source must be nonempty bytes within the 3 MB limit')
    return bytes(data)


def _number(value, name, low, high):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f'{name} must be a finite number') from None
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError(f'{name} is outside its supported numeric range')
    return number


def _normal(text):
    return ' '.join(text.replace('\xa0', ' ').split())


def _xml(archive, name):
    try:
        metadata = archive.getinfo(name)
        if metadata.file_size > _MAX_XML_BYTES:
            raise ValueError('Calibration workbook XML exceeds its size limit')
        raw = archive.read(name)
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise ValueError('Calibration workbook XML must not define entities')
        return ET.fromstring(raw)
    except (KeyError, ET.ParseError, RuntimeError, OSError, BadZipFile):
        raise ValueError('Calibration workbook has missing or invalid XML parts') from None


def _workbook_cells(data):
    try:
        archive = ZipFile(io.BytesIO(data))
    except BadZipFile:
        raise ValueError('Damodaran ERP source is not an XLSX workbook') from None
    with archive:
        members = archive.infolist()
        if (len(members) > 100 or len({item.filename for item in members}) != len(members)
                or sum(item.file_size for item in members) > _MAX_ZIP_BYTES):
            raise ValueError('Calibration workbook exceeds its bounded archive limits')
        workbook = _xml(archive, 'xl/workbook.xml')
        if workbook.tag != _MAIN + 'workbook':
            raise ValueError('Unexpected calibration workbook namespace')
        properties = workbook.find(_MAIN + 'workbookPr')
        epoch_flag = properties.get('date1904', '0') if properties is not None else '0'
        if epoch_flag not in ('0', '1', 'true', 'false'):
            raise ValueError('Unsupported workbook date1904 metadata')
        date1904 = epoch_flag in ('1', 'true')
        sheets = workbook.find(_MAIN + 'sheets')
        selected = [] if sheets is None else [sheet for sheet in sheets if sheet.get('name') == 'Historical ERP']
        if len(selected) != 1:
            raise ValueError('Workbook must contain exactly one Historical ERP sheet')
        relation_id = selected[0].get(_DOC_REL + 'id')
        relations = _xml(archive, 'xl/_rels/workbook.xml.rels')
        matches = [item for item in relations if item.tag == _PKG_REL + 'Relationship' and item.get('Id') == relation_id]
        if (not relation_id or len(matches) != 1 or matches[0].get('TargetMode') == 'External'
                or not matches[0].get('Type', '').endswith('/worksheet')):
            raise ValueError('Historical ERP must resolve to an internal worksheet')
        target = matches[0].get('Target', '')
        if urlparse(target).scheme or '\\' in target:
            raise ValueError('Invalid Historical ERP worksheet target')
        path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/' + target)
        if not path.startswith('xl/worksheets/') or not path.endswith('.xml'):
            raise ValueError('Invalid Historical ERP worksheet path')
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            shared = _xml(archive, 'xl/sharedStrings.xml')
            strings = [''.join(node.itertext()) if not list(node.iter(_MAIN + 't')) else
                       ''.join(item.text or '' for item in node.iter(_MAIN + 't'))
                       for node in shared.findall(_MAIN + 'si')]
            if len(strings) > 30_000:
                raise ValueError('Workbook shared strings exceed their size limit')
        worksheet = _xml(archive, path)
        sheet_data = worksheet.find(_MAIN + 'sheetData')
        if worksheet.tag != _MAIN + 'worksheet' or sheet_data is None:
            raise ValueError('Historical ERP is not a supported worksheet')
        cells = {}
        if len(sheet_data) > _MAX_ROWS:
            raise ValueError('Historical ERP exceeds its row limit')
        for row in sheet_data:
            for cell in row.findall(_MAIN + 'c'):
                coordinate = cell.get('r', '')
                if not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]{0,5}', coordinate) or coordinate in cells:
                    raise ValueError('Worksheet has invalid or duplicate cell coordinates')
                kind = cell.get('t', 'n')
                value = cell.findtext(_MAIN + 'v')
                if kind == 's':
                    if value is None or not re.fullmatch(r'[0-9]+', value) or int(value) >= len(strings):
                        raise ValueError('Worksheet has an invalid shared-string reference')
                    value = strings[int(value)]
                elif kind == 'inlineStr':
                    value = ''.join(item.text or '' for item in cell.iter(_MAIN + 't'))
                cells[coordinate] = (kind, value, cell.find(_MAIN + 'f') is not None)
                if len(cells) > 100_000:
                    raise ValueError('Worksheet exceeds its cell limit')
        return cells, date1904, path


def _workbook_date(cell, date1904):
    kind, text, formula = cell
    if formula or text is None:
        raise ValueError('Monthly observation date is missing or formula-based')
    if kind in ('n', ''):
        serial = _number(text, 'Excel observation date', 0, 200_000)
        if serial != int(serial) or (not date1904 and serial == 60):
            raise ValueError('Excel observation date is fractional or the fictitious 1900 leap day')
        origin = date(1904, 1, 1) if date1904 else date(1899, 12, 31) if serial < 60 else date(1899, 12, 30)
        return origin + timedelta(days=int(serial))
    if kind in ('s', 'str', 'inlineStr', 'd'):
        text = text.strip()
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
            return _iso_date(text, 'Monthly observation date')
        match = re.fullmatch(r'([1-9]|[12][0-9]|3[01])-([A-Z][a-z]{2})-([0-9]{2}|[0-9]{4})', text)
        if match:
            names = [month[:3] for month in _MONTHS]
            if match[2] not in names:
                raise ValueError('Unrecognized English month in workbook observation date')
            # The published monthly series starts in the 2000s. Make this
            # supported text convention explicit rather than using locale/pivots.
            year = int(match[3]) + (2000 if len(match[3]) == 2 else 0)
            try:
                return date(year, names.index(match[2]) + 1, int(match[1]))
            except ValueError:
                pass
    raise ValueError('Unrecognized workbook observation-date format')


def _workbook_rate(cell, name, low, high):
    kind, text, formula = cell
    if formula or text is None or not text.strip():
        raise ValueError(f'{name} is unavailable or formula-based in the selected monthly row')
    if kind in ('n', ''):
        return _number(text, name, low, high)
    if kind in ('s', 'str', 'inlineStr'):
        match = re.fullmatch(r'([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))\s*%', text.strip())
        if match:
            return _number(float(match[1]) / 100, name, low, high)
    raise ValueError(f'{name} has an unsupported cell type or rate unit')


def parse_damodaran_erp(data, *, as_of=None):
    """Read the latest eligible same-row Treasury/sustainable-payout ERP pair."""
    raw = _bytes(data)
    cutoff, today = _as_of(as_of)
    cells, date1904, path = _workbook_cells(raw)
    headers = {'A1': 'Start of month', 'C1': 'T.Bond Rate',
               'I1': 'ERP (T12 m with sustainable payout)'}
    for coordinate, expected in headers.items():
        value = cells.get(coordinate, ('', None, False))
        if value[2] or value[1] is None or _normal(value[1]) != expected:
            raise ValueError(f'Damodaran ERP header changed at {coordinate}; required method unavailable')
    dates = {}
    row_numbers = sorted({int(match[1]) for coordinate in cells
                          if (match := re.fullmatch(r'[ACI]([0-9]+)', coordinate)) and int(match[1]) > 1})
    for row in row_numbers:
        cell = cells.get(f'A{row}', ('n', None, False))
        if cell[1] is None or not cell[1].strip():
            if any(value and value[1] is not None and value[1].strip()
                   for value in (cells.get(f'C{row}'), cells.get(f'I{row}'))):
                raise ValueError('Historical ERP has populated calibration values without an observation date')
            continue
        observation = _workbook_date(cell, date1904)
        if observation.day != 1:
            raise ValueError('Historical ERP dates must be start-of-month observations')
        if observation > today:
            raise ValueError('Historical ERP contains an observation date in the future')
        if observation in dates:
            raise ValueError('Historical ERP has duplicate observation dates')
        dates[observation] = row
        # Validate present values, including older rows, without inventing
        # sustainable-payout ERPs that are blank in the early historical series.
        for column, name, low, high in (('C', 'Treasury proxy', -.05, .30), ('I', 'Sustainable-payout ERP', 0, .40)):
            value = cells.get(f'{column}{row}')
            if value and value[1] is not None and value[1].strip():
                _workbook_rate(value, name, low, high)
    eligible = [observation for observation in dates if observation <= cutoff]
    if not eligible:
        raise ValueError('No Damodaran monthly observation is available on or before as_of')
    observation = max(eligible)
    row = dates[observation]
    missing = ('n', None, False)
    risk_free = _workbook_rate(cells.get(f'C{row}', missing), 'Treasury proxy', -.05, .30)
    premium = _workbook_rate(cells.get(f'I{row}', missing), 'Sustainable-payout ERP', 0, .40)
    return {
        'dataset': 'damodaran_us_implied_erp', 'currency': 'USD',
        'observed_at': observation.isoformat(), 'date_precision': 'day',
        'risk_free_rate': risk_free, 'equity_risk_premium': premium,
        'method': headers['I1'], 'risk_free_label': 'Conventional 10-year US Treasury proxy paired with implied ERP',
        'provenance': {'sheet': 'Historical ERP', 'worksheet_part': path, 'row': row,
                       'date1904': date1904, 'date_system': '1904' if date1904 else '1900',
                       'date_cell': f'A{row}', 'raw_date': cells[f'A{row}'][1],
                       'risk_free_cell': f'C{row}', 'raw_risk_free_rate': cells[f'C{row}'][1],
                       'erp_cell': f'I{row}', 'raw_equity_risk_premium': cells[f'I{row}'][1],
                       'required_headers': headers, 'source_sha256': hashlib.sha256(raw).hexdigest()},
        'caveats': ['Start-of-month snapshot date; not a claim of a Treasury trade on that calendar day.',
                    'Uses only the sustainable-payout implied ERP method and its same-row conventional Treasury proxy.',
                    'No sovereign-default adjustment, issuer country-risk premium or newer Treasury substitution is performed.',
                    'Historical as_of filtering does not recreate an unrevised point-in-time source vintage.']}


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.text = [], []
        self.table = self.row = self.cell = None
        self.depth = 0
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.ignored += 1
        if self.ignored:
            return
        if tag == 'table':
            self.depth += 1
            if self.depth == 1:
                self.table = []
        elif self.depth == 1 and tag == 'tr':
            if self.row is not None:
                raise ValueError('Nested or unclosed sector-table rows')
            self.row = []
        elif self.depth == 1 and tag in ('td', 'th'):
            if self.row is None or self.cell is not None:
                raise ValueError('Invalid sector-table cell structure')
            if any(key in ('rowspan', 'colspan') and value not in ('1', None) for key, value in attrs):
                raise ValueError('Sector table has unsupported merged cells')
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.ignored:
            self.ignored -= 1
            return
        if self.ignored:
            return
        if self.depth == 1 and tag in ('td', 'th') and self.cell is not None:
            self.row.append(_normal(' '.join(self.cell)))
            self.cell = None
        elif self.depth == 1 and tag == 'tr' and self.row is not None:
            if self.cell is not None:
                raise ValueError('Sector table row has an unclosed cell')
            self.table.append(self.row)
            self.row = None
            if len(self.table) > 500:
                raise ValueError('Sector table exceeds 500 rows')
        elif tag == 'table':
            if self.depth == 1:
                if self.row is not None or self.cell is not None:
                    raise ValueError('Sector table has an incomplete final row')
                self.tables.append(self.table)
                self.table = self.row = self.cell = None
            self.depth = max(0, self.depth - 1)

    def handle_data(self, data):
        if self.ignored:
            return
        self.text.append(data)
        if self.cell is not None:
            self.cell.append(data)


def parse_sector_betas(data, *, as_of=None):
    """Return every explicitly dated sector and aggregate; never select an issuer."""
    raw = _bytes(data)
    cutoff, today = _as_of(as_of)
    try:
        document = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        if not re.search(br'charset\s*=\s*["\']?windows-1252', raw[:4096], re.I):
            raise ValueError('Sector beta HTML has an unsupported encoding') from None
        document = raw.decode('cp1252')
    parser = _Tables()
    parser.feed(document)
    parser.close()
    if parser.depth or parser.row is not None or parser.cell is not None:
        raise ValueError('Sector beta HTML table is incomplete')
    text = _normal(' '.join(parser.text))
    analysis = re.findall(r'Date of Analysis\s*:\s*Data used is as of\s+([A-Za-z]+)\s+([0-9]{4})', text)
    updated = re.findall(r'Last Updated in\s+([A-Za-z]+)\s+([0-9]{4})', text)
    if len(analysis) != 1 or len(updated) != 1 or analysis[0] != updated[0] or analysis[0][0] != 'January':
        raise ValueError('Sector beta analysis/update headers require one matching January observation period')
    year = int(analysis[0][1])
    try:
        period_end = date(year, 1, 31)
    except ValueError:
        raise ValueError('Sector beta observation year is invalid') from None
    if period_end > today or period_end > cutoff:
        raise ValueError('Sector beta observation period extends after as_of or into the future')
    matches = [table for table in parser.tables if table and tuple(table[0]) == _BETA_HEADERS]
    if len(matches) != 1:
        raise ValueError('Sector beta table headers changed or are ambiguous')
    sectors, names = [], set()
    for row in matches[0][1:]:
        if not any(row):
            continue
        if len(row) != len(_BETA_HEADERS) or not row[0] or row[0] in names:
            raise ValueError('Sector beta rows have missing columns, names or duplicate sectors')
        names.add(row[0])
        if not re.fullmatch(r'(?:[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)', row[1]):
            raise ValueError('Sector firm count must be a positive integer')
        firms = int(row[1].replace(',', ''))
        if firms > 1_000_000:
            raise ValueError('Sector firm count exceeds its supported range')
        sectors.append({'sector': row[0], 'firms': firms,
                        'levered_beta': _number(row[2], 'Sector levered beta', 0, 5),
                        'unlevered_beta': _number(row[5], 'Sector unlevered beta', 0, 5),
                        'cash_corrected_unlevered_beta': _number(row[7], 'Sector cash-corrected unlevered beta', 0, 5),
                        'is_aggregate': row[0].startswith('Total Market')})
    if not sectors or len(sectors) > 400:
        raise ValueError('Sector beta table has no usable bounded sector records')
    return {'dataset': 'damodaran_us_sector_betas', 'observed_at': f'{year:04d}-01',
            'date_precision': 'month', 'universe': 'US industry averages', 'sectors': sectors,
            'provenance': {'required_headers': list(_BETA_HEADERS), 'source_sha256': hashlib.sha256(raw).hexdigest()},
            'caveats': ['No issuer-to-sector assignment or automatic beta selection is performed.',
                        'Month precision is preserved; no exact beta measurement day is asserted.',
                        'Sector averages are proxies, not measured issuer betas; aggregate rows are labeled separately.',
                        'Cash-corrected and uncorrected asset betas are distinct; the analyst must choose a consistent asset convention.',
                        'This table does not establish peer lease conventions or a normalized issuer marginal tax rate.']}


def parse_fred_aa(data, *, as_of=None):
    """Read a dated AA corporate-index yield, explicitly an issuer debt-cost proxy."""
    raw = _bytes(data)
    cutoff, today = _as_of(as_of)
    try:
        rows = list(csv.reader(io.StringIO(raw.decode('utf-8-sig')), strict=True))
    except (UnicodeDecodeError, csv.Error):
        raise ValueError('FRED AA source is not supported UTF-8 CSV') from None
    if not rows or rows[0] != ['observation_date', 'BAMLC0A2CAAEY']:
        raise ValueError('FRED AA CSV headers changed; expected BAMLC0A2CAAEY effective yield')
    if len(rows) > _MAX_ROWS:
        raise ValueError('FRED AA CSV exceeds its row limit')
    observations, seen = [], set()
    missing = 0
    for row in rows[1:]:
        if not row:
            continue
        if len(row) != 2:
            raise ValueError('FRED AA observation must have exactly two columns')
        observation = _iso_date(row[0], 'FRED observation date')
        if observation > today:
            raise ValueError('FRED AA contains an observation date in the future')
        if observation in seen:
            raise ValueError('FRED AA has duplicate observation dates')
        seen.add(observation)
        if row[1] in ('', '.'):
            missing += 1
            continue
        value = _number(row[1], 'AA index yield in percentage points', 0, 60) / 100
        if observation <= cutoff:
            observations.append((observation, value, row[1]))
    if not observations:
        raise ValueError('No observed FRED AA yield is available on or before as_of')
    observation, value, raw_value = max(observations)
    return {'dataset': 'fred_us_aa_effective_yield', 'currency': 'USD',
            'observed_at': observation.isoformat(), 'date_precision': 'day',
            'value': value, 'unit': 'decimal_rate', 'series': 'BAMLC0A2CAAEY',
            'label': 'ICE BofA AA US Corporate Index Effective Yield, debt-cost proxy',
            'provenance': {'raw_percentage_points': raw_value, 'missing_observation_count': missing,
                           'source_sha256': hashlib.sha256(raw).hexdigest()},
            'caveats': ['Observed AA corporate-index yield is a proxy, not an issuer bond yield or borrowing quote.',
                        'Index rating, maturity and composition may differ from the issuer debt being valued.',
                        'Yield is converted from percentage points to a decimal rate; coupon rates are not substituted.',
                        'No issuer-specific spread, sovereign spread or country-risk premium is inferred.']}


def gather_us_calibration(fetch_bytes, *, as_of=None):
    """Fetch three independent public sources; unavailable inputs become gap records.

    ``as_of`` is an ISO date or ``datetime.date``. Returned source objects have
    no citation IDs and are evidence only, never automatic valuation assignments.
    Arbitrary exception messages are not copied into gaps or saved artifacts.
    """
    cutoff, _ = _as_of(as_of)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    sources, gaps = [], []
    tasks = (
        ('damodaran_us_implied_erp', ERP_URL, parse_damodaran_erp,
         'Damodaran dated US implied ERP and matched Treasury proxy', 'academic_market_data',
         'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx'),
        ('damodaran_us_sector_betas', BETAS_URL, parse_sector_betas,
         'Damodaran US sector beta table; no issuer sector assigned', 'academic_market_data', 'text/html', '.html'),
        ('fred_us_aa_effective_yield', AA_URL, parse_fred_aa,
         'FRED ICE BofA AA US Corporate Index Effective Yield; issuer debt-cost proxy', 'official_market_data', 'text/csv', '.csv'))
    for dataset, url, parse, title, tier, media_type, suffix in tasks:
        try:
            raw = _bytes(fetch_bytes(url))
        except (OSError, RuntimeError, ValueError, TypeError):
            gaps.append({'kind': 'macro_calibration_gap', 'source': dataset, 'url': url,
                         'reason': 'Public calibration source unavailable or outside the retrieval size bound; no input invented.'})
            continue
        try:
            parsed = parse(raw, as_of=cutoff)
        except ValueError as error:
            gaps.append({'kind': 'macro_calibration_gap', 'source': dataset, 'url': url,
                         'reason': str(error) + '; no calibration input assigned.'})
            continue
        sources.append({'title': title, 'url': url, 'kind': 'macro_calibration',
                        'source_tier': tier, 'retrieved_at': retrieved_at,
                        'observation_date': parsed['observed_at'], 'data': parsed,
                        'raw_document': raw, 'raw_media_type': media_type, 'archive_extension': suffix,
                        'raw_document_sha256': hashlib.sha256(raw).hexdigest(),
                        'ingestion_method': 'public_file_strict_parser'})
    return sources, gaps

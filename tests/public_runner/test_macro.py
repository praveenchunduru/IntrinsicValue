"""Deterministic public-file parsing with synthetic bytes; no network or models."""
from datetime import date, datetime, timedelta, timezone
import hashlib
from html import escape
import io
import unittest
from unittest.mock import Mock
from zipfile import ZipFile, ZIP_DEFLATED

from investmentideas.macro import (
    AA_URL, BETAS_URL, ERP_URL, gather_us_calibration,
    parse_damodaran_erp, parse_fred_aa, parse_sector_betas,
)


MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
DOC_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG_REL = 'http://schemas.openxmlformats.org/package/2006/relationships'


def workbook(rows=None, *, epoch=True, headers=None, relation=None, shared=None):
    if rows is None:
        rows = [(date(2024, 9, 1), .039, .0405), (date(2025, 1, 1), .04, .045)]
    headers = headers or ('Start of month', 'T.Bond Rate', 'ERP (T12 m with sustainable payout)')
    shared = shared or []
    origin = date(1904, 1, 1) if epoch else date(1899, 12, 30)
    def cell(coordinate, value):
        if value is None:
            return ''
        if isinstance(value, date):
            value = (value - origin).days
        if isinstance(value, tuple):
            if value[0] == 'shared':
                return f'<c r="{coordinate}" t="s"><v>{value[1]}</v></c>'
            if value[0] == 'formula':
                return f'<c r="{coordinate}"><f>0.04</f><v>{value[1]}</v></c>'
        if isinstance(value, str) and not value.replace('.', '').isdigit():
            return f'<c r="{coordinate}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
        return f'<c r="{coordinate}"><v>{value}</v></c>'
    header = '<row r="1">' + ''.join(cell(column + '1', value) for column, value in zip(('A', 'C', 'I'), headers)) + '</row>'
    body = ''.join('<row r="' + str(index) + '">' + ''.join(cell(column + str(index), value)
                   for column, value in zip(('A', 'C', 'I'), row)) + '</row>'
                   for index, row in enumerate(rows, 2))
    metadata = ('<workbook xmlns="' + MAIN + '" xmlns:r="' + DOC_REL + '">'
                '<workbookPr date1904="' + ('1' if epoch else '0') + '"/>'
                '<sheets><sheet name="Unrelated first sheet" sheetId="1" r:id="noise"/>'
                '<sheet name="Historical ERP" sheetId="2" r:id="calibration"/></sheets></workbook>')
    relation = relation or ('<Relationship Id="calibration" Type="' + DOC_REL + '/worksheet" Target="worksheets/calibration.xml"/>')
    output = io.BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('xl/workbook.xml', metadata)
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="' + PKG_REL + '">' + relation + '</Relationships>')
        archive.writestr('xl/worksheets/calibration.xml', '<worksheet xmlns="' + MAIN + '"><sheetData>' + header + body + '</sheetData></worksheet>')
        archive.writestr('xl/worksheets/sheet1.xml', '<not-a-calibration-sheet/>')
        if shared:
            archive.writestr('xl/sharedStrings.xml', '<sst xmlns="' + MAIN + '">' + ''.join('<si><t>' + escape(value) + '</t></si>' for value in shared) + '</sst>')
    return output.getvalue()


def beta_html(*, period='January 2024', updated=None, headers=None, records=None):
    headers = headers or [
        'Industry Name', 'Number of firms', 'Beta', 'D/E Ratio', 'Effective Tax rate',
        'Unlevered beta', 'Cash/Firm value', 'Unlevered beta corrected for cash',
        'HiLo Risk', 'Standard deviation of equity',
        'Standard deviation in operating income (last 10 years)']
    records = records or [
        ['Computers/Peripherals', '36', '1.30', '4.62%', '5.91%', '1.20', '1.47%', '1.25', '.5', '30%', 'NA'],
        ['Electronics (Consumer & Office)', '8', '.87', '5.8%', '0%', '.83', '10.5%', '.93', '.5', '30%', 'NA'],
        ['Total Market', '1,234', '1.0', '10%', '20%', '.90', '2%', '.92', '.5', '30%', '25%'],
    ]
    table = '<table>' + ''.join('<tr>' + ''.join('<td><span>' + escape(value) + '</span></td>'
            for value in row) + '</tr>' for row in [headers, *records, [''] * 11]) + '</table>'
    return ('<html><p>Date of Analysis : Data used is as of ' + period + '</p>' + table +
            '<p>Last Updated in ' + (updated or period) + '</p></html>').encode()


def aa_csv(rows=None, header='observation_date,BAMLC0A2CAAEY'):
    rows = rows or [('2024-12-30', '5.60'), ('2024-12-31', '.'), ('2025-01-02', '5.51')]
    return (header + '\n' + '\n'.join(','.join(row) for row in rows) + '\n').encode()


class ERPParserTests(unittest.TestCase):
    def test_latest_matched_pair_and_1904_epoch_are_source_dated(self):
        raw = workbook()
        result = parse_damodaran_erp(raw, as_of='2025-01-03')
        self.assertEqual(result['observed_at'], '2025-01-01')
        self.assertEqual((result['risk_free_rate'], result['equity_risk_premium']), (.04, .045))
        self.assertEqual(result['method'], 'ERP (T12 m with sustainable payout)')
        self.assertTrue(result['provenance']['date1904'])
        self.assertEqual(result['provenance']['date_system'], '1904')
        self.assertEqual(result['provenance']['date_cell'], 'A3')
        self.assertEqual(result['provenance']['erp_cell'], 'I3')
        self.assertEqual(result['provenance']['source_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result['provenance']['worksheet_part'], 'xl/worksheets/calibration.xml')
        self.assertIn('sovereign-default', ' '.join(result['caveats']))

    def test_historical_cutoff_never_uses_a_later_observation(self):
        result = parse_damodaran_erp(workbook(), as_of=date(2024, 10, 1))
        self.assertEqual(result['observed_at'], '2024-09-01')
        self.assertEqual(result['equity_risk_premium'], .0405)
        with self.assertRaisesRegex(ValueError, 'No Damodaran monthly observation'):
            parse_damodaran_erp(workbook(), as_of='2023-01-01')

    def test_1900_epoch_and_documented_shared_text_date_percent_cells(self):
        result = parse_damodaran_erp(workbook(epoch=False), as_of='2025-01-03')
        self.assertEqual(result['observed_at'], '2025-01-01')
        self.assertFalse(result['provenance']['date1904'])
        raw = workbook(rows=[(('shared', 0), ('shared', 1), ('shared', 2))],
                       shared=['1-Sep-24', '3.90%', '4.05%'])
        result = parse_damodaran_erp(raw, as_of='2024-10-01')
        self.assertEqual(result['observed_at'], '2024-09-01')
        self.assertAlmostEqual(result['risk_free_rate'], .039)
        self.assertAlmostEqual(result['equity_risk_premium'], .0405)
        self.assertEqual(result['provenance']['raw_date'], '1-Sep-24')

    def test_missing_old_erp_is_not_zero_or_a_stale_fallback(self):
        raw = workbook(rows=[(date(2024, 9, 1), .039, None), (date(2025, 1, 1), .04, .045)])
        self.assertEqual(parse_damodaran_erp(raw, as_of='2025-01-03')['equity_risk_premium'], .045)
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            parse_damodaran_erp(raw, as_of='2024-10-01')
        raw = workbook(rows=[(date(2024, 9, 1), .039, .04), (date(2025, 1, 1), .04, None)])
        with self.assertRaisesRegex(ValueError, 'selected monthly row'):
            parse_damodaran_erp(raw, as_of='2025-01-03')

    def test_undated_calibration_values_do_not_silently_select_older_pair(self):
        baseline = (date(2024, 9, 1), .039, .0405)
        for row in [(None, .0494, .0441), (None, None, .0441), ('', .0494, None)]:
            with self.subTest(row=row), self.assertRaisesRegex(ValueError, 'without an observation date'):
                parse_damodaran_erp(workbook(rows=[baseline, row]), as_of='2025-01-03')
        result = parse_damodaran_erp(workbook(rows=[baseline, (None, None, None)]), as_of='2025-01-03')
        self.assertEqual(result['observed_at'], '2024-09-01')

    def test_changed_method_or_riskfree_headers_are_rejected(self):
        for headers in [('Start of month', 'T.Bond Rate', 'ERP (T12m)'),
                        ('Start of month', 'Adjusted riskfree', 'ERP (T12 m with sustainable payout)'),
                        ('Date', 'T.Bond Rate', 'ERP (T12 m with sustainable payout)')]:
            with self.subTest(headers=headers), self.assertRaisesRegex(ValueError, 'header changed'):
                parse_damodaran_erp(workbook(headers=headers), as_of='2025-01-03')

    def test_bad_values_dates_duplicates_and_formula_values_are_rejected(self):
        invalid_rows = [
            [(date(2025, 1, 2), .04, .045)],
            [('1/1/2025', .04, .045)],
            [(date(2025, 1, 1), 'NaN', .045)],
            [(date(2025, 1, 1), 4.0, .045)],
            [(date(2025, 1, 1), .04, -.01)],
            [(date(2025, 1, 1), ('formula', .04), .045)],
            [(date(2025, 1, 1), .04, .045), (date(2025, 1, 1), .04, .045)],
        ]
        for rows in invalid_rows:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                parse_damodaran_erp(workbook(rows=rows), as_of='2025-01-03')
        today = datetime.now(timezone.utc).date()
        future = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
        with self.assertRaisesRegex(ValueError, 'in the future'):
            parse_damodaran_erp(workbook(rows=[(future, .04, .045)]))

    def test_wrong_relationship_external_target_and_nonworkbook_inputs_fail(self):
        for relation in [
            f'<Relationship Id="calibration" Type="{DOC_REL}/chartsheet" Target="worksheets/calibration.xml"/>',
            f'<Relationship Id="calibration" Type="{DOC_REL}/worksheet" Target="https://example.test/sheet.xml" TargetMode="External"/>',
            f'<Relationship Id="calibration" Type="{DOC_REL}/worksheet" Target="../../sheet.xml"/>',
        ]:
            with self.subTest(relation=relation), self.assertRaises(ValueError):
                parse_damodaran_erp(workbook(relation=relation), as_of='2025-01-03')
        with self.assertRaisesRegex(ValueError, 'not an XLSX'):
            parse_damodaran_erp(b'<html>Unavailable</html>')


class SectorParserTests(unittest.TestCase):
    def test_all_sector_proxies_and_aggregates_preserve_month_precision(self):
        result = parse_sector_betas(beta_html(), as_of='2025-01-03')
        self.assertEqual(result['observed_at'], '2024-01')
        self.assertEqual(result['date_precision'], 'month')
        self.assertEqual(len(result['sectors']), 3)
        self.assertEqual(result['sectors'][0]['cash_corrected_unlevered_beta'], 1.25)
        self.assertEqual(result['sectors'][0]['unlevered_beta'], 1.2)
        self.assertEqual(result['sectors'][1]['sector'], 'Electronics (Consumer & Office)')
        self.assertTrue(result['sectors'][2]['is_aggregate'])
        self.assertEqual(result['sectors'][2]['firms'], 1234)
        self.assertFalse(result['sectors'][0]['is_aggregate'])
        self.assertNotIn('selected_sector', result)
        self.assertIn('No issuer-to-sector assignment', result['caveats'][0])

    def test_whitespace_entities_and_ordinary_td_headers_are_supported(self):
        raw = beta_html().replace(b'Industry Name', b'Industry\n&nbsp;Name')
        self.assertEqual(parse_sector_betas(raw, as_of='2025-01-03')['sectors'][0]['firms'], 36)

    def test_changed_or_ambiguous_beta_headers_fail(self):
        raw = beta_html()
        for invalid in [raw.replace(b'Unlevered beta corrected for cash', b'Adjusted beta'),
                        raw + raw,
                        raw.replace(b'<td><span>Beta</span></td>', b'<td colspan="2"><span>Beta</span></td>')]:
            with self.subTest(invalid=invalid[:40]), self.assertRaises(ValueError):
                parse_sector_betas(invalid, as_of='2025-01-03')

    def test_missing_changed_mismatched_or_future_period_fails(self):
        for raw in [beta_html(period='April 2024'), beta_html(updated='January 2023'),
                    beta_html().replace(b'Date of Analysis', b'Other date')]:
            with self.subTest(raw=raw[:70]), self.assertRaises(ValueError):
                parse_sector_betas(raw, as_of='2025-01-03')
        with self.assertRaisesRegex(ValueError, 'extends after as_of'):
            parse_sector_betas(beta_html(period='January 2025'), as_of='2025-01-03')

    def test_bad_beta_or_firm_values_and_duplicate_sectors_fail(self):
        baseline = beta_html()
        for raw in [baseline.replace(b'>1.20<', b'>NaN<'), baseline.replace(b'>36<', b'>NA<'),
                    baseline.replace(b'>36<', b'>1.5<'), baseline.replace(b'>1.25<', b'>-1<'),
                    baseline.replace(b'Total Market', b'Computers/Peripherals')]:
            with self.subTest(raw=raw[:40]), self.assertRaises(ValueError):
                parse_sector_betas(raw, as_of='2025-01-03')

    def test_unclosed_final_populated_rows_or_cells_are_not_silently_dropped(self):
        empty = b'<tr>' + b'<td><span></span></td>' * 11 + b'</tr>'
        raw = beta_html().replace(empty, b'')
        unclosed_row = raw.replace(b'</tr></table>', b'</table>')
        with self.assertRaisesRegex(ValueError, 'incomplete final row'):
            parse_sector_betas(unclosed_row, as_of='2025-01-03')
        unclosed_cell = raw.replace(b'<td><span>25%</span></td>', b'<td><span>25%</span>')
        with self.assertRaisesRegex(ValueError, 'unclosed cell'):
            parse_sector_betas(unclosed_cell, as_of='2025-01-03')


class AAParserTests(unittest.TestCase):
    def test_latest_observed_yield_is_percent_converted_and_labeled_proxy(self):
        result = parse_fred_aa(aa_csv(), as_of='2025-01-03')
        self.assertEqual(result['observed_at'], '2025-01-02')
        self.assertAlmostEqual(result['value'], .0551)
        self.assertEqual(result['series'], 'BAMLC0A2CAAEY')
        self.assertEqual(result['provenance']['missing_observation_count'], 1)
        self.assertIn('not an issuer bond yield', result['caveats'][0])
        prior = parse_fred_aa(aa_csv(), as_of='2024-12-31')
        self.assertEqual(prior['observed_at'], '2024-12-30')
        self.assertAlmostEqual(prior['value'], .056)

    def test_changed_series_bad_units_values_dates_and_duplicates_fail(self):
        invalid = [aa_csv(header='observation_date,DAAA'), aa_csv(header='DATE,BAMLC0A2CAAEY'),
                   aa_csv(rows=[('2025-01-02', '5.51%')]), aa_csv(rows=[('2025-01-02', 'NaN')]),
                   aa_csv(rows=[('2025-01-02', '-1')]), aa_csv(rows=[('2025-1-2', '5.5')]),
                   aa_csv(rows=[('2025-01-02', '5.5'), ('2025-01-02', '5.5')]),
                   b'observation_date,BAMLC0A2CAAEY\n2025-01-02,5.5,extra\n']
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_fred_aa(raw, as_of='2025-01-03')
        tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
        with self.assertRaisesRegex(ValueError, 'in the future'):
            parse_fred_aa(aa_csv(rows=[(tomorrow.isoformat(), '5.5')]))

    def test_missing_or_ineligible_observations_are_unavailable(self):
        for raw in [aa_csv(rows=[('2025-01-02', '.')]), aa_csv(rows=[('2025-01-02', '')])]:
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, 'No observed FRED AA yield'):
                parse_fred_aa(raw, as_of='2025-01-03')
        with self.assertRaisesRegex(ValueError, 'on or before as_of'):
            parse_fred_aa(aa_csv(), as_of='2023-01-01')


class CollectionTests(unittest.TestCase):
    def test_three_injected_fetches_return_archivable_evidence_without_assignments(self):
        payloads = {ERP_URL: workbook(), BETAS_URL: beta_html(), AA_URL: aa_csv()}
        fetch = Mock(side_effect=lambda url: payloads[url])
        sources, gaps = gather_us_calibration(fetch, as_of='2025-01-03')
        self.assertEqual(gaps, [])
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual({source['data']['dataset'] for source in sources},
                         {'damodaran_us_implied_erp', 'damodaran_us_sector_betas', 'fred_us_aa_effective_yield'})
        self.assertEqual([source['archive_extension'] for source in sources], ['.xlsx', '.html', '.csv'])
        for source in sources:
            self.assertEqual(source['kind'], 'macro_calibration')
            self.assertNotIn('id', source)
            self.assertEqual(source['raw_document'], payloads[source['url']])
            self.assertEqual(source['raw_document_sha256'], hashlib.sha256(source['raw_document']).hexdigest())
            self.assertEqual(source['observation_date'], source['data']['observed_at'])
        self.assertNotIn('discount_rate', sources[0])

    def test_partial_failure_keeps_other_sources_and_suppresses_transport_message(self):
        def fetch(url):
            if url == ERP_URL:
                raise RuntimeError('do not expose arbitrary provider response')
            return beta_html() if url == BETAS_URL else aa_csv()
        sources, gaps = gather_us_calibration(fetch, as_of='2025-01-03')
        self.assertEqual(len(sources), 2)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]['source'], 'damodaran_us_implied_erp')
        self.assertNotIn('provider response', repr(gaps))

    def test_changed_source_reports_gap_instead_of_guessing_or_inventing_data(self):
        sources, gaps = gather_us_calibration(lambda url: b'<html>Unsupported</html>', as_of='2025-01-03')
        self.assertEqual(sources, [])
        self.assertEqual(len(gaps), 3)
        self.assertTrue(all(gap['kind'] == 'macro_calibration_gap' for gap in gaps))
        self.assertTrue(all('no calibration input assigned' in gap['reason'] for gap in gaps))

    def test_invalid_asof_and_input_sizes_fail_before_calibration(self):
        fetch = Mock()
        with self.assertRaises(ValueError):
            gather_us_calibration(fetch, as_of='9999-01-01')
        fetch.assert_not_called()
        for raw in (b'', 'not bytes', b' ' * 3_000_001):
            with self.subTest(kind=type(raw)), self.assertRaises(ValueError):
                parse_fred_aa(raw, as_of='2025-01-03')


class CalibrationTransportTests(unittest.TestCase):
    def test_public_calibration_requests_send_an_explicit_accept_header(self):
        # FRED closes the connection without a response when this client's
        # User-Agent arrives with no Accept header (reproduced live 2026-11-14).
        from unittest.mock import patch
        from investmentideas import pipeline
        from investmentideas.macro import AA_URL
        response = Mock()
        response.read.return_value = b'observation_date,BAMLC0A2CAAEY\n'
        response.__enter__ = lambda self: self
        response.__exit__ = lambda self, *args: False
        with patch.object(pipeline, 'urlopen', return_value=response) as transport:
            self.assertEqual(pipeline._fetch_calibration_bytes(AA_URL), b'observation_date,BAMLC0A2CAAEY\n')
        request = transport.call_args.args[0]
        self.assertEqual(request.full_url, AA_URL)
        self.assertEqual(request.get_header('Accept'), '*/*')
        self.assertEqual(request.get_header('User-agent'), 'IntrinsicValue research')
        self.assertNotIn('github', request.get_header('User-agent').lower())
        self.assertFalse(request.has_header('Authorization'))


if __name__ == '__main__':
    unittest.main()

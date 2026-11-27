"""The latest earnings release reaches the primary evidence (fixtures only, no network).

Walmart's 8-K of 2026-11-19 (Items 2.02 and 9.01) was newer than its latest
10-Q (2026-08-28). Its SEC filing index page rendered no document table, so the
release was reported unavailable and the run cited the quarter only from
unverified web excerpts. Most other releases were collected without their
income statement or balance sheet: the statement headings did not match the
filing patterns and sat after the 10,000-character opening.
"""
import json
import unittest
from unittest.mock import patch

from investmentideas import pipeline, quality, research

CIK = 104169
ACCESSION = '0000104169-26-000168'
FOLDER = f'https://www.sec.gov/Archives/edgar/data/{CIK}/000010416926000168/'
DIRECTORY = {'0': {'ticker': 'WMT', 'title': 'Walmart Inc.', 'cik_str': CIK}}
HISTORY = {'cik': '0000104169', 'name': 'Walmart Inc.', 'sic': '5331', 'sicDescription': 'Retail-Variety Stores',
           'filings': {'recent': {
               'form': ['8-K', '10-Q', '8-K', '10-K'],
               'filingDate': ['2026-11-19', '2026-08-28', '2026-08-20', '2026-03-13'],
               'reportDate': ['2026-11-19', '2026-07-31', '2026-08-20', '2026-01-31'],
               'accessionNumber': [ACCESSION, '0000104169-26-000150', '0000104169-26-000145', '0000104169-26-000020'],
               'primaryDocument': ['wmt-20261119.htm', 'wmt-20260731.htm', 'wmt-20260820.htm', 'wmt-20260131.htm'],
               'items': ['2.02,9.01', '', '2.02,9.01', '']}}}
FILING = '<html><body>' + '<p>The company operates retail stores and records revenue at the point of sale.</p>' * 10 + '</body></html>'
# The index page as SEC served it on 2026-11-27: the document table replaced by "undefined".
EMPTY_INDEX = ('<html><head><title>EDGAR Filing Documents for 0000104169-26-000168</title></head><body>'
               '<div class="formGrouping"><div class="infoHead">Filing Date</div><div class="info">2026-11-19</div></div>'
               'undefined</body></html>')
HEADERS = ('<html><body><pre>&lt;SEC-DOCUMENT&gt;0000104169-26-000168.txt : 20261119\n'
           '&lt;DOCUMENT&gt;\n&lt;TYPE&gt;8-K\n&lt;SEQUENCE&gt;1\n&lt;FILENAME&gt;wmt-20261119.htm\n&lt;/DOCUMENT&gt;\n'
           '&lt;DOCUMENT&gt;\n&lt;TYPE&gt;EX-99.1\n&lt;SEQUENCE&gt;2\n&lt;FILENAME&gt;earningsreleasefy27q3.htm\n'
           '&lt;DESCRIPTION&gt;EX-99.1\n&lt;TEXT&gt;\n<a href="earningsreleasefy27q3.htm">Document 2 - file: '
           'earningsreleasefy27q3.htm</a><br>\n&lt;/DOCUMENT&gt;\n'
           '&lt;DOCUMENT&gt;\n&lt;TYPE&gt;EX-99.2\n&lt;SEQUENCE&gt;3\n&lt;FILENAME&gt;earningspresentationfy27.htm\n'
           '&lt;DESCRIPTION&gt;EX-99.2\n&lt;/DOCUMENT&gt;\n</pre></body></html>')
PRIMARY_8K = ('<html><body><p>Item 2.02 Results of Operations and Financial Condition</p>'
              '<table><tr><td>DE</td><td>001-06991</td></tr></table>'
              '<table><tr><td>Exhibit No.</td><td>Description</td></tr>'
              '<tr><td>99.1</td><td><a href="earningsreleasefy27q3.htm">Press Release</a></td></tr>'
              '<tr><td>99.2</td><td><a href="earningspresentationfy27.htm">Financial Presentation</a></td></tr>'
              '</table></body></html>')


def _rows(rows):
    return '<table>' + ''.join(f'<tr><td>{name}</td><td>{now}</td><td>{before}</td></tr>' for name, now, before in rows) + '</table>'


def _filler(label, count):
    return [(f'{label} line {i}', f'{1000 + i:,}', f'{900 + i:,}') for i in range(count)]


INCOME = ('<p>Condensed Consolidated Statements of Income</p><p>(Unaudited)</p>' +
          _rows([('Total revenues', '179,496', '169,588')] + _filler('Operating expense', 30) +
                [('Consolidated net income attributable to Walmart', '6,145', '6,097')]))
BALANCE = ('<p>Condensed Consolidated Balance Sheets</p><p>(Unaudited)</p>' +
           _rows(_filler('Asset', 30) + [('Total assets', '311,615', '288,655')]))
CASH = ('<p>Condensed Consolidated Statements of Cash Flows</p><p>(Unaudited)</p>' +
        _rows([('Net cash provided by operating activities', '27,021', '27,529'),
               ('Payments for property and equipment', '(19,047)', '(17,612)')] + _filler('Cash item', 20)))
NARRATIVE = '<p>Walmart reports third quarter results; revenue grew and the company raised its guidance.</p>' * 200
SEGMENTS = '<p>Walmart U.S. segment net sales and operating income by quarter, with comparable sales.</p>' * 150
RELEASE = '<html><body>' + NARRATIVE + INCOME + BALANCE + CASH + SEGMENTS + '</body></html>'


def collect(pages, history=HISTORY):
    """gather_primary against fixture pages keyed by URL suffix; other URLs return a filing."""
    calls = []

    def fetch_text(url):
        calls.append(url)
        for suffix, page in pages.items():
            if url.endswith(suffix):
                if isinstance(page, Exception):
                    raise page
                return page
        return FILING
    with patch.object(research, 'load_directory', return_value=(DIRECTORY, False)), patch.object(research.time, 'sleep'):
        sources, gaps = research.gather_primary('WMT', lambda url: history, fetch_text)
    return sources, gaps, calls


def releases(sources):
    return [source for source in sources if source['kind'] == 'issuer_release']


class LocateTheRelease(unittest.TestCase):
    def test_a_release_newer_than_the_10q_is_found_from_the_index_headers(self):
        sources, gaps, calls = collect({'-index.htm': EMPTY_INDEX, '-index-headers.html': HEADERS,
                                        'earningsreleasefy27q3.htm': RELEASE})
        self.assertEqual([s['data']['metadata']['form'] for s in sources if s['kind'] == 'sec_filing'], ['10-K', '10-Q'])
        (release,) = releases(sources)
        metadata = release['data']['metadata']
        self.assertEqual((metadata['filingDate'], metadata['exhibit'], metadata['document'], metadata['exhibit_located_by']),
                         ('2026-11-19', 'EX-99.1', 'earningsreleasefy27q3.htm', 'filing index headers'))
        self.assertEqual((release['source_tier'], release['url']), ('issuer_primary', FOLDER + 'earningsreleasefy27q3.htm'))
        self.assertEqual(metadata['statements'], ['operating results', 'balance sheet', 'cash flow'])
        self.assertEqual([gap for gap in gaps if 'earnings release' in gap], [])
        # The headers page named the release, so the 8-K was not read; the release
        # holds its statements, so the presentation was not read either.
        self.assertNotIn(FOLDER + 'wmt-20261119.htm', calls)
        self.assertNotIn(FOLDER + 'earningspresentationfy27.htm', calls)

    def test_the_8k_exhibit_index_is_the_last_place_looked(self):
        sources, gaps, _ = collect({'-index.htm': EMPTY_INDEX,
                                    '-index-headers.html': RuntimeError('Official document HTTP 503 from www.sec.gov'),
                                    'wmt-20261119.htm': PRIMARY_8K, 'earningsreleasefy27q3.htm': RELEASE})
        (release,) = releases(sources)
        self.assertEqual(release['data']['metadata']['exhibit_located_by'], '8-K exhibit index')

    def test_an_unavailable_release_gap_says_what_each_page_showed(self):
        no_exhibits = '<html><body>' + '<p>Item 2.02 Results of Operations and Financial Condition.</p>' * 5 + '</body></html>'
        _, gaps, _ = collect({'-index.htm': EMPTY_INDEX,
                              '-index-headers.html': RuntimeError('Official document HTTP 503 from www.sec.gov'),
                              'wmt-20261119.htm': no_exhibits})
        self.assertIn('The 8-K earnings release filed 2026-11-19 was identified but its exhibit was unavailable '
                      '(filing index: no exhibit 99.1 or 99 document; filing index headers: Official document HTTP 503 '
                      'from www.sec.gov; 8-K exhibit index: no exhibit 99.1 or 99 document); it was not reviewed.', gaps)
        _, gaps, _ = collect({'-index-headers.html': HEADERS, '-index.htm': EMPTY_INDEX,
                              'earningsreleasefy27q3.htm': RuntimeError('Official document HTTP 404 from www.sec.gov')})
        self.assertIn('The 8-K earnings release filed 2026-11-19 was identified but its exhibit was unavailable '
                      '(EX-99.1 earningsreleasefy27q3.htm: Official document HTTP 404 from www.sec.gov); it was not reviewed.',
                      gaps)

    def test_only_documents_in_the_filing_folder_are_accepted(self):
        accession = '000010416926000168'
        self.assertEqual(research._header_exhibits(HEADERS.replace('earningsreleasefy27q3.htm', '../999/release.htm')),
                         [('EX-99.2', 'earningspresentationfy27.htm')])
        for href in ('https://www.sec.gov/Archives/edgar/data/999/000099926000001/release.htm',
                     '/Archives/edgar/data/999/000099926000001/release.htm', 'https://example.com/release.htm',
                     'release.htm#page2', 'javascript:void(0)'):
            self.assertIsNone(research._folder_document(href, CIK, accession), href)
        for href in ('release.htm', f'/Archives/edgar/data/{CIK}/{accession}/release.htm',
                     f'https://www.sec.gov/Archives/edgar/data/{CIK}/{accession}/release.htm',
                     f'/ix?doc=/Archives/edgar/data/{CIK}/{accession}/release.htm'):
            self.assertEqual(research._folder_document(href, CIK, accession), 'release.htm', href)
        self.assertEqual(research._primary_exhibits(PRIMARY_8K, CIK, accession),
                         [('EX-99.1', 'earningsreleasefy27q3.htm'), ('EX-99.2', 'earningspresentationfy27.htm')])

    def test_exhibit_types(self):
        for value, kind in (('EX-99.1', 'EX-99.1'), ('EX-99.01', 'EX-99.1'), ('ex-99', 'EX-99'), ('EX-99.2', 'EX-99.2'),
                            ('Exhibit 99.1', 'EX-99.1'), ('EX-99.10', 'EX-99.10'), ('EX-10.1', None), ('EX-99.1A', None),
                            ('ex991.htm', None), ('GRAPHIC', None)):
            self.assertEqual(research._exhibit_kind(value), kind, value)


class StatementsReachTheEvidence(unittest.TestCase):
    def test_the_quarter_figures_are_primary_evidence_the_writer_may_cite(self):
        sources, _, _ = collect({'-index.htm': EMPTY_INDEX, '-index-headers.html': HEADERS,
                                 'earningsreleasefy27q3.htm': RELEASE})
        (release,) = releases(sources)
        excerpts = {excerpt['label']: excerpt for excerpt in release['data']['excerpts']}
        self.assertEqual(list(excerpts)[:4], ['document opening', 'operating results', 'balance sheet', 'cash flow'])
        self.assertIn('Total revenues 179,496', excerpts['operating results']['text'])
        self.assertIn('Consolidated net income attributable to Walmart 6,145', excerpts['operating results']['text'])
        self.assertIn('Total assets 311,615', excerpts['balance sheet']['text'])
        self.assertIn('Net cash provided by operating activities 27,021', excerpts['cash flow']['text'])
        self.assertLessEqual(release['data']['excerpt_chars'], research.MAX_EXCERPT_CHARS)
        cited = dict(release, id='S6')
        _, primary, unverified, _ = pipeline._evidence_rows([cited])
        self.assertEqual(unverified, [])
        self.assertIn('Total assets 311,615', json.dumps(primary, ensure_ascii=False))
        self.assertIn('PRIMARY AND OFFICIAL SOURCES', pipeline._evidence_context([cited]))
        pool, unverified_pool, _ = quality.trace_pools([cited])
        self.assertIn(311615.0, pool)
        self.assertIn(179496.0, pool)
        self.assertEqual(unverified_pool, [])

    def test_statements_filed_as_a_second_exhibit_are_collected(self):
        # Johnson & Johnson (2026-10-13) and AT&T (2026-10-21) file their statements as EX-99.2.
        index = ('<table class="tableFile">'
                 f'<tr><td>2</td><td>PRESS RELEASE</td><td><a href="/Archives/edgar/data/{CIK}/000010416926000168/ex991.htm">'
                 'ex991.htm</a></td><td>EX-99.1</td></tr>'
                 f'<tr><td>3</td><td>FINANCIAL DATA</td><td><a href="/Archives/edgar/data/{CIK}/000010416926000168/ex992.htm">'
                 'ex992.htm</a></td><td>EX-99.2</td></tr>'
                 f'<tr><td>4</td><td>DISCUSSION</td><td><a href="/Archives/edgar/data/{CIK}/000010416926000168/ex993.htm">'
                 'ex993.htm</a></td><td>EX-99.3</td></tr></table>')
        statements = '<html><body><p>Financial Data</p>' + INCOME + BALANCE + CASH + '</body></html>'
        narrative = '<html><body>' + NARRATIVE + SEGMENTS + '</body></html>'
        sources, gaps, calls = collect({'-index.htm': index, 'ex991.htm': narrative, 'ex992.htm': statements})
        first, second = releases(sources)
        self.assertEqual(first['data']['metadata']['statements'], [])
        self.assertEqual(second['title'], 'Walmart Inc. earnings release financial statements (8-K Item 2.02, EX-99.2); '
                                          'filed 2026-11-19')
        self.assertEqual((second['source_tier'], second['data']['metadata']['release_exhibit']), ('issuer_primary', 'EX-99.1'))
        self.assertEqual(second['data']['metadata']['statements'], ['operating results', 'balance sheet', 'cash flow'])
        self.assertIn('Total assets 311,615', json.dumps(second['data']['excerpts']))
        self.assertNotIn(FOLDER + 'ex993.htm', calls)
        self.assertEqual([gap for gap in gaps if 'earnings release' in gap], [])
        # A statements exhibit that cannot be read is a gap; the release stays.
        sources, gaps, _ = collect({'-index.htm': index, 'ex991.htm': narrative,
                                    'ex992.htm': RuntimeError('Official document HTTP 500 from www.sec.gov'),
                                    'ex993.htm': narrative})
        self.assertEqual(len(releases(sources)), 1)
        self.assertIn('EX-99.2 (ex992.htm) of the 8-K earnings release filed 2026-11-19 was unavailable (Official document '
                      'HTTP 500 from www.sec.gov); its statements were not reviewed.', gaps)


class ReleaseStatementWindows(unittest.TestCase):
    def test_release_statement_headings(self):
        income = ('INCOME STATEMENTS', 'CONDENSED CONSOLIDATED STATEMENTS OF EARNINGS', 'Consolidated Earnings Information',
                  'Condensed consolidated statement of earnings', 'Consolidated Statements of Operations',
                  'CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS (Unaudited)', 'CONDENSED CONSOLIDATED\nSTATEMENTS OF INCOME')
        for heading in income:
            text = 'Narrative.\n' + heading + '\nTotal revenues 100\n'
            self.assertEqual([label for label, _, _ in research.release_statement_windows(text)], ['operating results'], heading)
        for heading, label in (('BALANCE SHEETS', 'balance sheet'), ('Condensed Consolidated Balance Sheets (Unaudited)', 'balance sheet'),
                               ('Consolidated Statements of Financial Position', 'balance sheet'),
                               ('CASH FLOWS STATEMENTS', 'cash flow'), ('Consolidated Statements of Cash Flows', 'cash flow')):
            self.assertEqual([found for found, _, _ in research.release_statement_windows(heading + '\nTotal 1\n')], [label])
        for line in ('COMPREHENSIVE INCOME STATEMENTS', 'Consolidated Statements of Comprehensive Income',
                     'Cash flow from operating activities was $32.79 billion.', 'Balance Sheet and Liquidity',
                     'Free cash flow', 'Certain Balance Sheet Data 2026 2025 2024'):
            self.assertEqual(research.release_statement_windows('Narrative.\n' + line + '\nTotal 1\n'), [], line)

    def test_windows_keep_each_statement_whole_within_the_bound(self):
        line = 'Line item {} 1,234 1,111\n'
        statement = ''.join(line.format(i) for i in range(60))
        text = ('Narrative paragraph about the quarter.\n' * 500 + 'CONSOLIDATED STATEMENTS OF OPERATIONS\n' + statement +
                'Consolidated Statements of Comprehensive Income\n' + statement + 'BALANCE SHEETS\n' + statement +
                'CASH FLOWS STATEMENTS\n' + statement * 3 + 'Segment results paragraph.\n' * 400)
        excerpts = research.select_excerpts(text, research.MAX_EXCERPT_CHARS, research.RELEASE_OPENING_CHARS, statements=True)
        self.assertLessEqual(sum(len(e['text']) for e in excerpts), research.MAX_EXCERPT_CHARS)
        for excerpt in excerpts:
            self.assertEqual(text[excerpt['start_char']:excerpt['end_char']], excerpt['text'])
        by_label = {e['label']: e for e in excerpts}
        operations = text.index('CONSOLIDATED STATEMENTS OF OPERATIONS')
        self.assertEqual((by_label['operating results']['start_char'], by_label['operating results']['end_char']),
                         (operations, text.index('Consolidated Statements of Comprehensive Income')))
        self.assertTrue(by_label['balance sheet']['text'].endswith(statement))
        self.assertEqual(len(by_label['cash flow']['text']), research.RELEASE_STATEMENT_CHARS['cash flow'])
        # The opening gives way so that the three statements fit.
        statements_chars = sum(len(by_label[label]['text']) for label in research.RELEASE_STATEMENT_CHARS)
        self.assertEqual(by_label['document opening']['end_char'],
                         min(research.RELEASE_OPENING_CHARS, research.MAX_EXCERPT_CHARS - statements_chars))
        self.assertGreaterEqual(by_label['document opening']['end_char'],
                                research.MAX_EXCERPT_CHARS - sum(research.RELEASE_STATEMENT_CHARS.values()))

    def test_the_opening_ends_where_the_first_statement_begins(self):
        # Home Depot's statement of earnings began 9,017 characters in (2026-11-17).
        text = ('Narrative paragraph about the quarter.\n' * 230 + 'CONDENSED CONSOLIDATED STATEMENTS OF EARNINGS\n' +
                'Net sales 41,352 39,901\n' * 40 + 'Narrative after the statements.\n' * 400)
        start = text.index('CONDENSED CONSOLIDATED STATEMENTS OF EARNINGS')
        self.assertLess(start, research.RELEASE_OPENING_CHARS)
        excerpts = research.select_excerpts(text, research.MAX_EXCERPT_CHARS, research.RELEASE_OPENING_CHARS, statements=True)
        self.assertEqual([(e['label'], e['start_char']) for e in excerpts[:2]], [('document opening', 0), ('operating results', start)])
        self.assertEqual(excerpts[0]['end_char'], start)

    def test_filings_and_releases_without_statements_keep_their_windows(self):
        text = 'Opening issuer evidence.\n' * 400 + 'INCOME STATEMENTS\n' + 'Revenue 1\n' * 400 + 'Narrative.\n' * 1500
        filing = research.select_excerpts(text, research.MAX_EXCERPT_CHARS)
        self.assertNotIn('operating results', [e['label'] for e in filing])
        narrative = 'Walmart reports results and guidance.\n' * 900
        release = research.select_excerpts(narrative, research.MAX_EXCERPT_CHARS, research.RELEASE_OPENING_CHARS, statements=True)
        self.assertEqual((release[0]['label'], len(release[0]['text'])), ('document opening', research.RELEASE_OPENING_CHARS))


if __name__ == '__main__':
    unittest.main()

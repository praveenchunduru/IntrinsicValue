import hashlib
import unittest
from unittest.mock import patch

from investmentideas import research

DIRECTORY = {'0': {'ticker': 'TEST', 'title': 'Test Issuer', 'cik_str': 123}}
BODY = '<html><head><title>Hidden title</title><script>not evidence</script></head><body><h1>Business</h1>' + '<p>Issuer sells industrial equipment and records revenue when control passes to customers.</p>' * 12 + '<table><tr><td>Revenue</td><td>100</td><td>USD millions</td></tr></table></body></html>'
HISTORY = {'cik': '0000000123', 'name': 'Test Issuer', 'filings': {'recent': {
    'form': ['8-K', '10-Q', '10-K'], 'filingDate': ['2026-11-01', '2026-08-01', '2026-02-01'],
    'reportDate': ['2026-10-31', '2026-06-30', '2025-12-31'],
    'accessionNumber': ['0000000123-26-000003', '0000000123-26-000002', '0000000123-26-000001'],
    'primaryDocument': ['event.htm', 'quarter.htm', 'annual.htm']}}}
FEED = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Apple reports fourth quarter results</title><published>2026-11-03T00:00:00Z</published><link href="https://www.apple.com/newsroom/2026/11/apple-reports-fourth-quarter-results/" /></entry><entry><title>Apple introduces new hardware</title><link href="https://www.apple.com/newsroom/2026/10/new-hardware/" /></entry></feed>'''


class PrimaryResearchTests(unittest.TestCase):
    def test_filings_use_official_history_and_archive_provenance(self):
        with patch.object(research, 'load_directory', return_value=(DIRECTORY, False)), patch.object(research.time, 'sleep'):
            sources, gaps = research.gather_primary('TEST', lambda url: HISTORY, lambda url: BODY)
        self.assertEqual(gaps, [])
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0]['data']['metadata']['form'], '10-K')
        self.assertEqual(sources[1]['data']['metadata']['form'], '10-Q')
        self.assertEqual(sources[0]['url'], 'https://www.sec.gov/Archives/edgar/data/123/000000012326000001/annual.htm')
        self.assertEqual(sources[0]['source_tier'], 'regulatory_primary')
        self.assertEqual(sources[0]['raw_document'], BODY)
        self.assertEqual(sources[0]['data']['raw_decoded_text_sha256'], hashlib.sha256(BODY.encode()).hexdigest())
        self.assertNotIn('not evidence', sources[0]['data']['excerpts'][0]['text'])
        self.assertIn('Revenue', sources[0]['data']['excerpts'][0]['text'])

    def test_exact_excerpt_offsets_are_bounded(self):
        text = 'Opening issuer evidence.\n' * 400 + 'Business\n' + 'Business detail.\n' * 800 + 'Risk Factors\n' + 'Risk evidence.\n' * 800
        excerpts = research.select_excerpts(text, max_chars=10000)
        self.assertLessEqual(sum(len(e['text']) for e in excerpts), 10000)
        for e in excerpts:
            self.assertEqual(text[e['start_char']:e['end_char']], e['text'])
        self.assertGreater(len(excerpts), 1)

    def test_apple_official_feed_is_discovered_when_sec_blocked(self):
        def text(url):
            return FEED if url == research.APPLE_FEED else BODY
        with patch.object(research, 'load_directory', side_effect=RuntimeError('blocked')):
            sources, gaps = research.gather_primary('AAPL', lambda url: None, text)
        self.assertEqual(len(sources), 2)
        self.assertTrue(all(s['source_tier'] == 'issuer_primary' for s in sources))
        self.assertIn('/2026/11/', sources[0]['url'])
        self.assertEqual(sources[0]['data']['metadata']['published_at'], '2026-11-03T00:00:00Z')
        self.assertTrue(any('filings were not reviewed' in gap for gap in gaps))
        self.assertTrue(all('raw_document' in s for s in sources))

    def test_unavailable_filing_is_a_gap_not_a_reviewed_source(self):
        with patch.object(research, 'load_directory', return_value=(DIRECTORY, False)), patch.object(research.time, 'sleep'):
            sources, gaps = research.gather_primary('TEST', lambda url: HISTORY,
                lambda url: (_ for _ in ()).throw(RuntimeError('blocked')))
        self.assertEqual(sources, [])
        self.assertEqual(len(gaps), 3)
        self.assertTrue(all('not reviewed' in g or 'No additional' in g for g in gaps))

    def test_malicious_or_malformed_filing_paths_are_not_fetched(self):
        import copy
        history = copy.deepcopy(HISTORY)
        history['filings']['recent']['primaryDocument'][1:] = ['../../outside', 'https://evil.invalid/']
        calls = []
        with patch.object(research, 'load_directory', return_value=(DIRECTORY, False)):
            sources, gaps = research.gather_primary('TEST', lambda url: history, lambda url: calls.append(url))
        self.assertEqual(sources, [])
        self.assertEqual(calls, [])
        self.assertTrue(gaps)

    def test_heading_selection_ignores_mentions_and_contents(self):
        prefix = 'Contents\nConsolidated Balance Sheets as of 2025 and 2024\n' + 'Introduction.\n' * 600
        mention = 'Accounting policy references the Consolidated Balance Sheets.\n'
        heading = 'CONSOLIDATED BALANCE SHEETS\nCash 100 USD million\n'
        text = prefix + mention + 'More background.\n' * 600 + heading + 'Table rows.\n' * 600
        excerpts = research.select_excerpts(text)
        balance = next(e for e in excerpts if e['label'] == 'balance sheet')
        self.assertIn(heading, balance['text'])
        self.assertGreater(balance['start_char'], len(prefix))

    def test_access_control_pages_are_not_primary_evidence(self):
        blocked = '<html><body>Access Denied. ' + ('This request was blocked. ' * 20) + '</body></html>'
        with self.assertRaisesRegex(RuntimeError, 'access-control'):
            research._source('Filing', 'https://www.sec.gov/a', blocked,
                             kind='sec_filing', source_tier='regulatory_primary', metadata={})

    def test_feed_does_not_accept_external_or_insecure_links(self):
        feed = FEED.replace('https://www.apple.com/newsroom/2026/11/apple-reports-fourth-quarter-results/', 'http://127.0.0.1/private')
        entries = research._apple_feed_entries(feed)
        self.assertEqual(len(entries), 1)
        for unsafe in ['http://www.sec.gov/a', 'https://localhost/a', 'https://user:password@www.sec.gov/a']:
            with self.assertRaises(ValueError):
                research.fetch_official_text(unsafe)


class InlineFactsTests(unittest.TestCase):
    def document(self, facts, contexts=''):
        return '''<html xmlns:us-gaap="http://fasb.org/us-gaap/2026" xmlns:dei="http://xbrl.sec.gov/dei/2026">
<xbrli:context id="now"><xbrli:entity><xbrli:identifier>123</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2026-09-26</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:context id="annual"><xbrli:period><xbrli:startDate>2025-09-28</xbrli:startDate><xbrli:endDate>2026-09-26</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
<xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>''' + contexts + facts + '</html>'

    def fact(self, name, token, context='now', unit='usd', **attrs):
        attributes = ' '.join(f'{key}="{value}"' for key, value in attrs.items())
        return f'<ix:nonFraction name="{name}" contextRef="{context}" unitRef="{unit}" {attributes}>{token}</ix:nonFraction>'

    def test_scale_units_sign_parentheses_and_exact_location(self):
        doc = self.document(
            self.fact('us-gaap:FinanceLeaseLiabilityCurrent', '700', scale='6', id='lease') +
            self.fact('us-gaap:FinanceLeaseLiabilityNoncurrent', '(756)', scale='6', sign='-') +
            self.fact('us-gaap:OperatingIncomeLoss', '<span>1,234.50</span>', context='annual', scale='3', format='ixt:num-dot-decimal') +
            self.fact('dei:EntityCommonStockSharesOutstanding', '14,532,182,000', unit='shares', scale='0', format='ixt:num-dot-decimal'))
        facts = research.inline_facts(doc)
        by_name = {fact['concept']: fact for fact in facts}
        lease = by_name['us-gaap:FinanceLeaseLiabilityCurrent']
        self.assertEqual(lease['normalized_value'],700000000)
        self.assertEqual(lease['unit'],'iso4217:USD')
        self.assertEqual(lease['instant'],'2026-09-26')
        self.assertEqual(lease['location']['fact_id'],'lease')
        location = lease['location']
        self.assertEqual(doc[location['start_char']:location['end_char']],location['raw_html'])
        self.assertIn('>700</ix:nonFraction>',location['raw_html'])
        self.assertEqual(by_name['us-gaap:FinanceLeaseLiabilityNoncurrent']['normalized_value'],-756000000)
        self.assertEqual(by_name['us-gaap:OperatingIncomeLoss']['normalized_value'],1234500)
        self.assertEqual(by_name['us-gaap:OperatingIncomeLoss']['start_date'],'2025-09-28')
        self.assertEqual(by_name['dei:EntityCommonStockSharesOutstanding']['normalized_value'],14532182000)
        self.assertEqual(by_name['dei:EntityCommonStockSharesOutstanding']['unit'],'xbrli:shares')

    def test_nil_unknown_dash_and_explicit_zero_are_distinct(self):
        nil = self.fact('us-gaap:FinanceLeaseLiabilityCurrent','',**{'xsi:nil':'true'})
        unknown = self.fact('us-gaap:OperatingLeaseLiabilityCurrent','—')
        stated_zero = self.fact('us-gaap:FinanceLeaseLiabilityNoncurrent','—',format='ixt:fixed-zero')
        rows = {fact['concept']:fact for fact in research.inline_facts(self.document(nil+unknown+stated_zero))}
        self.assertTrue(rows['us-gaap:FinanceLeaseLiabilityCurrent']['is_nil'])
        self.assertIsNone(rows['us-gaap:FinanceLeaseLiabilityCurrent']['normalized_value'])
        self.assertIsNone(rows['us-gaap:OperatingLeaseLiabilityCurrent']['normalized_value'])
        self.assertEqual(rows['us-gaap:OperatingLeaseLiabilityCurrent']['parse_status'],'unparsed_token')
        self.assertEqual(rows['us-gaap:FinanceLeaseLiabilityNoncurrent']['normalized_value'],0)
        self.assertEqual(rows['us-gaap:FinanceLeaseLiabilityNoncurrent']['parse_status'],'explicit_zero_transform')

    def test_dimensions_are_explicitly_filtered_and_latest_cover_date_retained(self):
        contexts = '''<xbrli:context id="cover"><xbrli:period><xbrli:instant>2026-10-23</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:context id="segment"><xbrli:entity><xbrli:segment><xbrldi:explicitMember dimension="gaap:Axis">gaap:Member</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2026-09-26</xbrli:instant></xbrli:period></xbrli:context>'''
        doc=self.document(self.fact('dei:EntityCommonStockSharesOutstanding','14532182000',context='cover',unit='shares')+
                          self.fact('us-gaap:CommonStockSharesOutstanding','14532259',unit='shares',scale='3')+
                          self.fact('us-gaap:CashAndCashEquivalentsAtCarryingValue','99',context='segment'),contexts)
        rows=research.inline_facts(doc)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['instant'],'2026-10-23')
        self.assertEqual(rows[1]['instant'],'2026-09-26')
        self.assertTrue(all(row['dimensions']=={} and row['dimension_filter']=='nondimensional_only' for row in rows))

    def test_comma_transform_decimals_are_not_scale_and_invalid_values_do_not_become_zero(self):
        doc=self.document(self.fact('us-gaap:Revenues','1.234,5',context='annual',scale='3',format='ixt:num-comma-decimal')+
                          self.fact('us-gaap:OperatingIncomeLoss','700',context='annual',decimals='-6')+
                          self.fact('us-gaap:NetIncomeLoss','not-a-number',context='annual')+
                          self.fact('us-gaap:CommercialPaper','10',scale='99'))
        rows={fact['concept']:fact for fact in research.inline_facts(doc)}
        self.assertEqual(rows['us-gaap:Revenues']['normalized_value'],1234500)
        self.assertEqual(rows['us-gaap:OperatingIncomeLoss']['normalized_value'],700)
        self.assertIsNone(rows['us-gaap:NetIncomeLoss']['normalized_value'])
        self.assertIsNone(rows['us-gaap:CommercialPaper']['normalized_value'])

    def test_nested_share_facts_keep_concepts_separate_and_output_is_bounded(self):
        nested=self.fact('us-gaap:CommonStockSharesIssued',self.fact('us-gaap:CommonStockSharesOutstanding','123',unit='shares'),unit='shares')
        rows=research.inline_facts(self.document(nested))
        self.assertEqual(len(rows),2)
        self.assertEqual({row['concept'] for row in rows},{'us-gaap:CommonStockSharesIssued','us-gaap:CommonStockSharesOutstanding'})
        self.assertTrue(all(row['normalized_value']==123 for row in rows))
        facts=''.join(self.fact('us-gaap:Revenues',str(i),context='annual') for i in range(120))
        self.assertEqual(len(research.inline_facts(self.document(facts))),100)

    def test_namespace_aliases_work_but_unknown_taxonomies_do_not(self):
        doc=self.document(self.fact('gaap:FinanceLeaseLiabilityCurrent','700',scale='6'))
        doc=doc.replace('<html ', '<html xmlns:gaap="http://fasb.org/us-gaap/2026" xmlns:fact="http://www.xbrl.org/2013/inlineXBRL" ',1)
        doc=doc.replace('<ix:', '<fact:').replace('</ix:', '</fact:')
        rows=research.inline_facts(doc)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['concept'],'gaap:FinanceLeaseLiabilityCurrent')
        self.assertEqual(rows[0]['normalized_value'],700000000)
        spoofed=doc.replace('http://fasb.org/us-gaap/2026','https://evil.example/fasb.org/us-gaap/2026')
        self.assertEqual(research.inline_facts(spoofed),[])


if __name__ == '__main__':
    unittest.main()

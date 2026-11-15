import hashlib
import unittest
from unittest.mock import patch

from investmentideas import research

DIRECTORY = {'0': {'ticker': 'TEST', 'title': 'Test Issuer', 'cik_str': 123}}
BODY = '<html><head><title>Hidden title</title><script>not evidence</script></head><body><h1>Business</h1>' + '<p>Issuer sells industrial equipment and records revenue when control passes to customers.</p>' * 12 + '<table><tr><td>Revenue</td><td>100</td><td>USD millions</td></tr></table></body></html>'
HISTORY = {'cik': '0000000123', 'name': 'Test Issuer', 'sic': '3571', 'sicDescription': 'Electronic Computers',
           'filings': {'recent': {
    'form': ['8-K', '10-Q', '10-K'], 'filingDate': ['2026-11-01', '2026-08-01', '2026-02-01'],
    'reportDate': ['2026-10-31', '2026-06-30', '2025-12-31'],
    'accessionNumber': ['0000000123-26-000003', '0000000123-26-000002', '0000000123-26-000001'],
    'primaryDocument': ['event.htm', 'quarter.htm', 'annual.htm'], 'items': ['2.02,9.01', '', '']}}}
RELEASE_FOLDER = '/Archives/edgar/data/123/000000012326000003/'
INDEX = ('<html><body><table class="tableFile"><tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th></tr>'
         f'<tr><td>1</td><td>8-K</td><td><a href="/ix?doc={RELEASE_FOLDER}event.htm">event.htm</a></td><td>8-K</td></tr>'
         f'<tr><td>2</td><td>PRESS RELEASE</td><td><a href="{RELEASE_FOLDER}ex991.htm">ex991.htm</a></td><td>EX-99.1</td></tr>'
         '</table></body></html>')


def fetch_with_index(url):
    return INDEX if url.endswith('-index.htm') else BODY


class PrimaryResearchTests(unittest.TestCase):
    def test_filings_use_official_history_and_archive_provenance(self):
        with patch.object(research, 'load_directory', return_value=(DIRECTORY, False)), patch.object(research.time, 'sleep'):
            sources, gaps = research.gather_primary('TEST', lambda url: HISTORY, fetch_with_index)
        self.assertEqual(gaps, [])
        self.assertEqual(len(sources), 3)
        self.assertEqual(sources[0]['data']['metadata']['form'], '10-K')
        self.assertEqual(sources[1]['data']['metadata']['form'], '10-Q')
        self.assertEqual(sources[0]['url'], 'https://www.sec.gov/Archives/edgar/data/123/000000012326000001/annual.htm')
        self.assertEqual(sources[0]['source_tier'], 'regulatory_primary')
        # The SEC industry code travels with each record for the operating-company gate.
        self.assertEqual(sources[0]['data']['metadata']['sic'], '3571')
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

    def test_every_issuer_gets_its_latest_8k_earnings_release_as_issuer_primary(self):
        # A live Microsoft run (2026-11-15) had its release only as an unverified web excerpt.
        calls = []
        def text(url):
            calls.append(url)
            return fetch_with_index(url)
        with patch.object(research, 'load_directory', return_value=(DIRECTORY, False)), patch.object(research.time, 'sleep'):
            sources, gaps = research.gather_primary('TEST', lambda url: HISTORY, text)
        release = sources[-1]
        self.assertEqual(release['kind'], 'issuer_release')
        self.assertEqual(release['source_tier'], 'issuer_primary')
        self.assertEqual(release['url'], 'https://www.sec.gov' + RELEASE_FOLDER + 'ex991.htm')
        self.assertIn('https://www.sec.gov' + RELEASE_FOLDER + '0000000123-26-000003-index.htm', calls)
        metadata = release['data']['metadata']
        self.assertEqual((metadata['form'], metadata['exhibit'], metadata['filingDate']), ('8-K', 'EX-99.1', '2026-11-01'))
        self.assertIn('2.02', metadata['items'])
        self.assertIn('filed 2026-11-01', release['title'])
        self.assertLessEqual(release['data']['excerpt_chars'], research.MAX_EXCERPT_CHARS)
        self.assertTrue(all('raw_document' in s for s in sources))

    def test_releases_need_item_202_and_an_exhibit_inside_the_filing(self):
        import copy
        history = copy.deepcopy(HISTORY)
        history['filings']['recent']['items'][0] = '5.07'
        with patch.object(research, 'load_directory', return_value=(DIRECTORY, False)), patch.object(research.time, 'sleep'):
            sources, gaps = research.gather_primary('TEST', lambda url: history, fetch_with_index)
        self.assertEqual([s['kind'] for s in sources], ['sec_filing', 'sec_filing'])
        self.assertTrue(any('Item 2.02' in gap for gap in gaps))
        outside = INDEX.replace(RELEASE_FOLDER + 'ex991.htm', '/Archives/edgar/data/999/000000012326000009/ex991.htm')
        with patch.object(research, 'load_directory', return_value=(DIRECTORY, False)), patch.object(research.time, 'sleep'):
            sources, gaps = research.gather_primary(
                'TEST', lambda url: HISTORY, lambda url: outside if url.endswith('-index.htm') else BODY)
        self.assertEqual(len(sources), 2)
        self.assertTrue(any('exhibit was unavailable' in gap for gap in gaps))
        self.assertEqual(research._release_exhibit(INDEX.replace('EX-99.1', 'EX-99'), 123, '000000012326000003'),
                         ('EX-99', 'ex991.htm'))

    def test_a_quarterly_report_older_than_the_annual_report_is_skipped(self):
        # Apple filed its fiscal 2026 10-K after its June 2026 10-Q.
        import copy
        history = copy.deepcopy(HISTORY)
        history['filings']['recent']['reportDate'][2] = '2026-06-30'
        records = research._filing_records(history)
        self.assertEqual([record['form'] for record in records], ['10-K'])
        self.assertEqual([record['form'] for record in research._filing_records(HISTORY)], ['10-K', '10-Q'])

    def test_a_registrant_without_an_annual_report_names_its_successor_statement(self):
        # ExxonMobil Holdings had filed only 10-Qs under its new CIK.
        import copy
        history = copy.deepcopy(HISTORY)
        recent = history['filings']['recent']
        for key in ('form', 'filingDate', 'reportDate', 'accessionNumber', 'primaryDocument', 'items'):
            recent[key] = recent[key][:2]
        body = BODY.replace('<h1>Business</h1>', '<p>The Company became the successor registrant of the predecessor\'s '
                            'common stock pursuant to Rule 12g-3(a) under the Securities Exchange Act of 1934.</p>')
        with patch.object(research, 'load_directory', return_value=(DIRECTORY, False)), patch.object(research.time, 'sleep'):
            sources, gaps = research.gather_primary('TEST', lambda url: history,
                                                    lambda url: INDEX if url.endswith('-index.htm') else body)
        metadata = sources[0]['data']['metadata']
        self.assertFalse(metadata['annual_report_on_file'])
        self.assertIn('successor registrant', metadata['successor_registrant_statement'])
        self.assertTrue(any('holds no annual report' in gap for gap in gaps))
        self.assertTrue(any('different CIK' in gap for gap in gaps))

    def test_unavailable_filing_is_a_gap_not_a_reviewed_source(self):
        with patch.object(research, 'load_directory', return_value=(DIRECTORY, False)), patch.object(research.time, 'sleep'):
            sources, gaps = research.gather_primary('TEST', lambda url: HISTORY,
                lambda url: (_ for _ in ()).throw(RuntimeError('blocked')))
        self.assertEqual(sources, [])
        self.assertEqual(len(gaps), 3)
        self.assertTrue(all('not reviewed' in g for g in gaps))

    def test_malicious_or_malformed_filing_paths_are_not_fetched(self):
        import copy
        history = copy.deepcopy(HISTORY)
        history['filings']['recent']['primaryDocument'][:] = ['../event', '../../outside', 'https://evil.invalid/']
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

    def test_heading_scan_handles_long_blank_runs_and_failed_heading_prefix(self):
        prefix = 'Issuer opening.\n' + ' \n' * 20000
        prefix += 'Item 1' + ' ' * 50000 + 'unrelated text\n'
        heading = ' \tItem\t1.\tBusiness \t\n'
        text = prefix + heading + 'Issuer business detail.\n' * 1000
        excerpts = research.select_excerpts(text, max_chars=10000)
        business = next(e for e in excerpts if e['label'] == 'business')
        self.assertEqual(business['start_char'], len(prefix) - 120)
        self.assertIn(heading, business['text'])
        for excerpt in excerpts:
            self.assertEqual(text[excerpt['start_char']:excerpt['end_char']], excerpt['text'])
        self.assertLessEqual(sum(len(e['text']) for e in excerpts), 10000)

    def test_heading_split_over_lines_is_selected_with_exact_offsets(self):
        prefix = 'Issuer opening.\n' * 1000
        heading = 'Item\n1.\nBusiness\n'
        text = prefix + heading + 'Issuer business detail.\n' * 1000
        business = next(e for e in research.select_excerpts(text) if e['label'] == 'business')
        self.assertIn(heading, business['text'])
        self.assertEqual(text[business['start_char']:business['end_char']], business['text'])

    def test_access_control_pages_are_not_primary_evidence(self):
        blocked = '<html><body>Access Denied. ' + ('This request was blocked. ' * 20) + '</body></html>'
        with self.assertRaisesRegex(RuntimeError, 'access-control'):
            research._source('Filing', 'https://www.sec.gov/a', blocked,
                             kind='sec_filing', source_tier='regulatory_primary', metadata={})

    def test_official_text_fetch_rejects_external_or_insecure_links(self):
        for unsafe in ['http://www.sec.gov/a', 'https://localhost/a', 'https://user:password@www.sec.gov/a',
                       'https://www.apple.com/newsroom/']:
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


    def test_cover_shares_by_share_class_are_kept_with_their_dimension(self):
        # Meta reports outstanding shares only by class on its cover.
        contexts = '''<xbrli:context id="classA"><xbrli:entity><xbrli:identifier>123</xbrli:identifier><xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:CommonClassAMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2026-10-16</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:context id="classB_cash"><xbrli:entity><xbrli:identifier>123</xbrli:identifier><xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:CommonClassBMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period></xbrli:context>'''
        doc = self.document(self.fact('dei:EntityCommonStockSharesOutstanding', '2214472367', context='classA', unit='shares') +
                            self.fact('us-gaap:CommonStockSharesOutstanding', '342', context='classB_cash', unit='shares', scale='6'),
                            contexts)
        rows = research.inline_facts(doc)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['dimensions'], {'us-gaap:StatementClassOfStockAxis': 'us-gaap:CommonClassAMember'})
        self.assertEqual(rows[0]['dimension_filter'], 'class_of_stock_cover_shares')


class CombinedAxisBreakdownTests(unittest.TestCase):
    def context(self, name, **axes):
        members = ''.join(f'<xbrldi:explicitMember dimension="{axis.replace("__", ":")}">{member}</xbrldi:explicitMember>'
                          for axis, member in axes.items())
        return (f'<xbrli:context id="{name}"><xbrli:entity><xbrli:identifier>104169</xbrli:identifier><xbrli:segment>'
                f'{members}</xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2025-02-01</xbrli:startDate>'
                '<xbrli:endDate>2026-01-31</xbrli:endDate></xbrli:period></xbrli:context>')

    def test_a_product_within_one_segment_is_tabled_under_its_product_keyed_by_segment(self):
        # Walmart reports grocery revenue within Walmart U.S. and within Sam's Club.
        segment, product = 'us-gaap__StatementBusinessSegmentsAxis', 'srt__ProductOrServiceAxis'
        contexts = (self.context('us_grocery', **{segment: 'wmt:WalmartUSMember', product: 'wmt:GroceryMember'}) +
                    self.context('club_grocery', **{segment: 'wmt:SamsClubUSMember', product: 'wmt:GroceryMember'}) +
                    self.context('three', **{segment: 'wmt:WalmartUSMember', product: 'wmt:GroceryMember',
                                            'srt__StatementGeographicalAxis': 'country:US'}) +
                    self.context('other', **{segment: 'wmt:WalmartUSMember', 'us-gaap__FairValueByFairValueHierarchyLevelAxis': 'x:L1'}))
        def fact(context, value):
            return (f'<ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="{context}" '
                    f'unitRef="usd" scale="9">{value}</ix:nonFraction>')
        doc = ('<html xmlns:us-gaap="http://fasb.org/us-gaap/2026">' + contexts +
               '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>' +
               fact('us_grocery', '285.5') + fact('club_grocery', '64.7') + fact('three', '1') + fact('other', '2') + '</html>')
        facts = research.inline_breakdown_facts(doc)
        self.assertEqual({(fact['axis'], fact['member']) for fact in facts},
                         {('product_or_service', 'wmt:GroceryMember within wmt:WalmartUSMember'),
                          ('product_or_service', 'wmt:GroceryMember within wmt:SamsClubUSMember')})
        from investmentideas.evidence_tables import breakdown_tables, render_breakdowns
        text = render_breakdowns(breakdown_tables(facts))
        self.assertIn('Grocery in Walmart US: revenue 285.5', text)
        self.assertIn('Grocery in Sams Club US: revenue 64.7', text)
        self.assertIn('not summed', text)


class LeaseFocusTests(unittest.TestCase):
    def test_lease_commitment_and_fixed_asset_notes_are_selected(self):
        prose = ' The amounts below compare with the prior year and are described in the following paragraphs.'
        document = ('<h2>Note 14 \u2014 Leases</h2><p>Finance lease cost was $5.1 billion and lease liabilities were '
                    '$66.6 billion compared with the prior year.' + prose + '</p>' + '<p>Maturity table row 2027 6,082.</p>' * 80 +
                    '<p>As of June 30, 2026, we had additional leases, primarily for datacenters, that had not yet commenced '
                    'of $329.1 billion, with some arrangements subject to certain contractual conditions being met.</p>' +
                    '<h2>Commitments and Contingencies</h2><p>We have committed $12.0 billion for construction compared '
                    'with $9.0 billion a year earlier.' + prose + '</p>' +
                    '<h2>Property and Equipment, Net</h2><p>Depreciation expense was $34.3 billion, primarily due to '
                    'servers and network equipment.' + prose + '</p>')
        from investmentideas.research import archive_focus_excerpts
        excerpts = archive_focus_excerpts(document)
        labels = '; '.join(excerpt['label'] for excerpt in excerpts)
        for label in ('leases', 'commitments', 'property and equipment', 'leases signed but not yet commenced'):
            self.assertIn(label, labels)
        self.assertTrue(any('$329.1 billion' in excerpt['text'] for excerpt in excerpts))


if __name__ == '__main__':
    unittest.main()

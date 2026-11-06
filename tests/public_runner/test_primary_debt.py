"""Debt scope comes from the labeled same-filing table, never a missing tag."""
import copy
import hashlib
import unittest

from investmentideas.primary_debt import classify_primary_debt
from investmentideas.research import html_to_text, inline_facts


def filing(*, current='2026-06-30', header='June 30, 2026', scale='6',
           heading='Note 7 — Debt', total_label='Total debt', dimensional=False,
           duplicate_total=False, plain=False, label_span=1,
           preface='The components of debt were as follows. As of June 30, 2026, no short-term borrowings or commercial paper were outstanding.'):
    dimension = ('<xbrli:entity><xbrli:segment><xbrldi:explicitMember '
                 'dimension="us-gaap:Axis">us-gaap:Member</xbrldi:explicitMember>'
                 '</xbrli:segment></xbrli:entity>') if dimensional else ''
    def value(context, amount):
        if plain:
            return amount
        return (f'<ix:nonFraction name="us-gaap:LongTermDebt" contextRef="{context}" '
                f'unitRef="usd" scale="{scale}" format="ixt:num-dot-decimal">'
                f'{amount}</ix:nonFraction>')
    blanks = '<td></td>' * (label_span - 1)
    total = (f'<tr><td colspan="{label_span}">{total_label}</td>'
             f'<td>{value("now", "40,294")}</td><td></td>'
             f'<td>{value("prior", "43,151")}</td></tr>')
    return ('''<html xmlns:us-gaap="http://fasb.org/us-gaap/2026"
xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">
<ix:header><xbrli:context id="now"><xbrli:entity><xbrli:identifier>123</xbrli:identifier></xbrli:entity>'''
        + dimension + f'<xbrli:period><xbrli:instant>{current}</xbrli:instant></xbrli:period></xbrli:context>'
        + '''<xbrli:context id="prior"><xbrli:entity><xbrli:identifier>123</xbrli:identifier></xbrli:entity>
<xbrli:period><xbrli:instant>2025-06-30</xbrli:instant></xbrli:period></xbrli:context>
<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit></ix:header>'''
        + f'<h3>{heading}</h3><p>{preface}</p><table>'
        + f'<tr><th>(In millions)</th>{blanks}<th>{header}</th><th></th><th>June 30, 2025</th></tr>'
        + f'<tr><td colspan="{label_span}">Senior notes</td><td>46,136</td><td></td><td>49,206</td></tr>'
        + total + (total if duplicate_total else '')
        + f'<tr><td colspan="{label_span}">Current portion of long-term debt</td><td>(9,227)</td><td></td><td>(2,999)</td></tr>'
        + f'<tr><td colspan="{label_span}">Long-term debt</td><td>31,067</td><td></td><td>40,152</td></tr>'
        + '</table></html>')


class PrimaryDebtTests(unittest.TestCase):
    def classify(self, document, **kwargs):
        return classify_primary_debt(document, inline_facts(document), anchor='2026-06-30', **kwargs)

    def test_labeled_total_matches_scaled_fact_and_exact_source_offsets(self):
        document = filing(label_span=7)
        result = self.classify(document)
        declaration = result['classifications']['financial_debt_total']
        self.assertEqual(declaration['method'], 'primary_debt_table_total')
        self.assertEqual(declaration['concept'], 'us-gaap:LongTermDebt')
        self.assertEqual(declaration['instant'], '2026-06-30')
        self.assertEqual(declaration['normalized_decimal'], '40294000000')
        self.assertEqual(declaration['scope_evidence_type'], 'dated_no_short_term_financing')
        self.assertIn('Total debt', declaration['source_excerpt'])
        self.assertIn('2026', declaration['source_excerpt'])
        excerpt = result['excerpts'][0]
        cleaned = html_to_text(document)
        self.assertEqual(excerpt['text'], cleaned[excerpt['start_char']:excerpt['end_char']])
        self.assertEqual(excerpt['text'], declaration['source_excerpt'])
        self.assertEqual(excerpt['text_sha256'], hashlib.sha256(excerpt['text'].encode()).hexdigest())
        self.assertEqual(excerpt['cleaned_text_sha256'], hashlib.sha256(cleaned.encode()).hexdigest())
        self.assertEqual(excerpt['raw_decoded_text_sha256'], hashlib.sha256(document.encode()).hexdigest())
        self.assertIn('<table>', document[excerpt['raw_start_char']:excerpt['raw_end_char']])
        self.assertNotIn('short_term_borrowings', result['classifications'])

    def test_plain_issuer_table_without_inline_facts_is_not_classified(self):
        document = filing(plain=True)
        self.assertEqual(inline_facts(document), [])
        self.assertEqual(self.classify(document)['classifications'], {})

    def test_large_filing_within_official_fetch_bound_keeps_qualified_debt_table(self):
        document=filing().replace('<h3>Note', '<div>'+('x'*3_050_000)+'</div><h3>Note',1)
        result=self.classify(document)
        self.assertEqual(result['classifications']['financial_debt_total']['normalized_decimal'],'40294000000')

    def test_word_exported_note_heading_and_intervening_prose_are_supported(self):
        document = filing().replace('<h3>Note 7 — Debt</h3>', '<p><b>NOTE 7 — DEBT</b></p>')
        self.assertIn('financial_debt_total', self.classify(document)['classifications'])
        document = document.replace('<table>', '<p><b>Note 8 — Investments</b></p><table>')
        self.assertEqual(self.classify(document)['classifications'], {})

    def test_generic_total_or_explicit_narrow_context_does_not_establish_whole_debt(self):
        for preface in ('The components of debt were as follows:',
                        'The components of long-term debt were as follows:',
                        'Term notes are below; short-term borrowings are disclosed separately:',
                        'As of June 30, 2025, no short-term borrowings were outstanding.',
                        'As of June 30, 2026, no short-term borrowings were outstanding except commercial paper.'):
            with self.subTest(preface=preface):
                self.assertEqual(self.classify(filing(preface=preface))['classifications'], {})

    def test_disjoint_short_and_long_term_rows_require_matching_total_arithmetic(self):
        document = filing(preface='Debt consisted of the following disjoint categories:')
        row = '<tr><td>Short-term debt</td><td>9,227</td><td></td><td>2,999</td></tr>'
        document = document.replace('<tr><td colspan="1">Total debt', row + '<tr><td colspan="1">Total debt')
        result = self.classify(document)
        self.assertEqual(result['classifications']['financial_debt_total']['scope_evidence_type'], 'disjoint_debt_categories')
        bad = document.replace('<td>9,227</td>', '<td>9,228</td>')
        self.assertEqual(self.classify(bad)['classifications'], {})

    def test_wrong_year_and_wrong_same_year_date_are_rejected(self):
        for header in ('June 30, 2024', 'December 31, 2026', 'June 30, 2025'):
            with self.subTest(header=header):
                self.assertEqual(self.classify(filing(header=header))['classifications'], {})

    def test_year_only_header_is_aligned_to_exact_inline_context(self):
        document = filing(header='2026')
        declaration = self.classify(document)['classifications']['financial_debt_total']
        self.assertEqual(declaration['instant'], '2026-06-30')
        self.assertEqual(self.classify(filing(current='2026-12-31'))['classifications'], {})

    def test_other_filing_fact_value_context_and_scale_do_not_match(self):
        document = filing()
        facts = inline_facts(document)
        for mutation in ('value', 'context', 'location'):
            changed = copy.deepcopy(facts)
            current = next(f for f in changed if f['instant'] == '2026-06-30')
            if mutation == 'value':
                current['normalized_decimal'] = '40294000'
            elif mutation == 'context':
                current['context_ref'] = 'another_filing_context'
            else:
                current['location']['start_char'] += 1
            with self.subTest(mutation=mutation):
                self.assertEqual(classify_primary_debt(document, changed, anchor='2026-06-30')['classifications'], {})
        self.assertEqual(classify_primary_debt(filing(scale='3'), facts, anchor='2026-06-30')['classifications'], {})

    def test_ambiguous_totals_or_year_columns_are_rejected(self):
        for document in (filing(duplicate_total=True),
                         filing(header='June 30, 2025'),
                         filing().replace('<th>June 30, 2026</th>', '<th colspan="2">June 30, 2026</th>')):
            with self.subTest(document=document[:40]):
                self.assertEqual(self.classify(document)['classifications'], {})

    def test_later_partial_conflicting_date_header_invalidates_column(self):
        conflicting = ('<tr><td>Other reporting date</td><td>December 31, 2026</td>'
                       '<td></td><td>Comparative period</td></tr>')
        document = filing().replace('<tr><td colspan="1">Total debt',
                                    conflicting + '<tr><td colspan="1">Total debt')
        self.assertEqual(self.classify(document)['classifications'], {})

    def test_malformed_columns_and_additional_printed_amounts_are_rejected(self):
        for document in (filing().replace('colspan="1"', 'colspan'),
                         filing().replace('>40,294</ix:nonFraction>', '>40,294</ix:nonFraction> 99')):
            with self.subTest(document=document[:40]):
                self.assertEqual(self.classify(document)['classifications'], {})

    def test_row_cell_year_alignment_cannot_be_replaced_by_matching_printed_amount(self):
        document = filing().replace('contextRef="now"', 'contextRef="prior"')
        self.assertEqual(self.classify(document)['classifications'], {})

    def test_dimensional_fact_marketing_table_or_investment_total_is_rejected(self):
        for document in (filing(dimensional=True), filing(heading='Debt performance highlights'),
                         filing(heading='Note 7 — Long-term debt'),
                         filing(total_label='Total debt investments')):
            with self.subTest(document=document[:40]):
                self.assertEqual(self.classify(document)['classifications'], {})

    def test_latest_qualified_date_is_selected_when_no_anchor_is_supplied(self):
        document = filing()
        result = classify_primary_debt(document, inline_facts(document))
        self.assertEqual(result['classifications']['financial_debt_total']['instant'], '2026-06-30')

    def test_input_bounds_are_explicit(self):
        with self.assertRaises(ValueError):
            classify_primary_debt('a' * 20_000_001, [])
        with self.assertRaises(ValueError):
            classify_primary_debt(filing(), [], anchor='2026-13-01')


if __name__ == '__main__':
    unittest.main()

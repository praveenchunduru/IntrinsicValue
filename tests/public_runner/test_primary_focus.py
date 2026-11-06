"""Exact, bounded archive passages for primary R&D and tax evidence."""
import hashlib
import unittest

from investmentideas.research import (
    MAX_DOCUMENT_BYTES,
    archive_focus_excerpts,
    html_to_text,
)


def paragraph(value):
    return '<p>' + value + '</p>'


class PrimaryFocusTests(unittest.TestCase):
    def assert_exact(self, document, excerpts):
        cleaned = html_to_text(document)
        self.assertLessEqual(sum(len(e['text']) for e in excerpts), 6000)
        for excerpt in excerpts:
            self.assertGreater(excerpt['end_char'], excerpt['start_char'])
            self.assertEqual(excerpt['text'], cleaned[excerpt['start_char']:excerpt['end_char']])
            self.assertEqual(excerpt['text_sha256'], hashlib.sha256(excerpt['text'].encode()).hexdigest())
            self.assertEqual(excerpt['cleaned_text_sha256'], hashlib.sha256(cleaned.encode()).hexdigest())
            self.assertEqual(excerpt['raw_decoded_text_sha256'], hashlib.sha256(document.encode()).hexdigest())
            self.assertEqual(excerpt['offset_basis'], 'html_to_text(document) Unicode character offsets')

    def test_unicode_exact_offsets_and_hashes_use_cleaned_text(self):
        document = '<html><head><title>Do not retain</title></head><body>'
        document += paragraph('Préface &amp; explanation with a nonbreaking&nbsp;space.')
        document += '<h2>Research &amp; Development</h2>'
        document += paragraph('Research and development expense increased primarily due to higher infrastructure costs and headcount-related expenses.')
        document += '</body></html>'
        excerpts = archive_focus_excerpts(document)
        self.assertEqual(len(excerpts), 1)
        self.assertTrue(excerpts[0]['text'].startswith('Research & Development\n'))
        self.assertNotIn('Do not retain', excerpts[0]['text'])
        self.assert_exact(document, excerpts)

    def test_repeated_heading_includes_mda_cost_drivers(self):
        document = '<h1>Contents</h1><div>Research and Development</div><div>8</div>'
        document += paragraph('contents padding ' * 200)
        document += '<h2>Research and Development</h2>'
        document += paragraph('The company develops new products and services to maintain its position in rapidly changing competitive markets.')
        document += paragraph('business detail ' * 200)
        document += '<h1>Management’s Discussion and Analysis</h1><h2>Research and Development</h2>'
        driver = ('R&D expense increased during the year primarily due to higher infrastructure-related costs, '
                  'including investments in artificial intelligence, and headcount-related expenses.')
        document += paragraph(driver)
        excerpts = archive_focus_excerpts(document)
        self.assertTrue(any(driver in e['text'] for e in excerpts))
        self.assertFalse(any(e['start_char'] == html_to_text(document).index('Research and Development') for e in excerpts))
        self.assert_exact(document, excerpts)

    def test_tax_note_covers_narrative_after_repeated_contents_heading(self):
        document = '<h1>Contents</h1><div>Income Taxes</div><div>44</div>'
        document += paragraph('contents padding ' * 200)
        document += '<h2>Note 12 — Income Taxes</h2>'
        document += paragraph('The company appealed the decision and continued the legal process before the final judgment was issued. ' * 9)
        charge = 'The final judgment resulted in a one-time income tax charge related to the disputed historical treatment.'
        document += paragraph(charge)
        excerpts = archive_focus_excerpts(document)
        self.assertEqual(len(excerpts), 1)
        self.assertIn(charge, excerpts[0]['text'])
        self.assertIn('income-tax note disclosures', excerpts[0]['label'])
        self.assert_exact(document, excerpts)

    def test_total_bound_and_no_overlapping_duplicate_windows(self):
        document = ''
        for heading, sentence in [
            ('Research and Development', 'R&D expense increased primarily due to headcount and higher costs in the research organization. '),
            ('Research and Development Expenses', 'Research and development expenses decreased due to lower costs and reduced spending in the period. '),
            ('Provision for Income Taxes', 'The effective tax rate increased primarily due to changes in geographic earnings and the valuation allowance. '),
            ('Note 7: Income Taxes', 'The company recorded a one-time tax charge after completing the relevant assessment and reviewing the available evidence. '),
        ]:
            document += '<h2>' + heading + '</h2>' + paragraph(sentence * 30)
        excerpts = archive_focus_excerpts(document)
        self.assertEqual(sum(len(e['text']) for e in excerpts), 6000)
        self.assertEqual(len(excerpts), 4)
        for first, second in zip(excerpts, excerpts[1:]):
            self.assertLessEqual(first['end_char'], second['start_char'])
        self.assert_exact(document, excerpts)

    def test_overlapping_rd_and_tax_windows_merge_with_both_labels(self):
        document = '<h2>Research and Development</h2>'
        document += paragraph('R&D expense increased primarily due to higher infrastructure costs and headcount-related expenses in the period.')
        document += '<h2>Provision for Income Taxes</h2>'
        document += paragraph('The effective tax rate increased primarily due to changes in geographic earnings and the valuation allowance.')
        excerpts = archive_focus_excerpts(document)
        self.assertEqual(len(excerpts), 1)
        self.assertIn('research and development cost drivers', excerpts[0]['label'])
        self.assertIn('income-tax provision and rate drivers', excerpts[0]['label'])
        self.assert_exact(document, excerpts)

    def test_unrelated_documents_and_contents_only_have_no_invented_evidence(self):
        self.assertEqual(archive_focus_excerpts('<p>Ordinary operating results without selected disclosure headings.</p>'), [])
        self.assertEqual(archive_focus_excerpts('<h1>Contents</h1><div>Research and Development</div><div>12</div><div>Income Taxes</div><div>43</div>'), [])
        self.assertEqual(archive_focus_excerpts(''), [])

    def test_invalid_or_oversize_archives_are_rejected(self):
        with self.assertRaises(ValueError):
            archive_focus_excerpts(None)
        with self.assertRaises(ValueError):
            archive_focus_excerpts('é' * (MAX_DOCUMENT_BYTES // 2 + 1))


if __name__ == '__main__':
    unittest.main()

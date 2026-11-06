import json
import unittest

from investmentideas.quality import claim_ledger, inspect_memo, parse_json, parse_review

SOURCE = {'id':'S1','kind':'financial_timeseries','data':{'revenue':100}}
MEMO = '''# Equity memo
## Executive view and thesis
Revenue is 100 [S1].
## Business economics
Business description [S1].
## Financial analysis
Historical statements [S1].
## Valuation
Weighted intrinsic value per share: $20.00
## Bear / base / bull scenarios
Explicit assumptions [S1].
## Risks
Risk evidence [S1].
## Disconfirming evidence
Alternative explanation [S1].
## Catalysts and monitoring
Evidence to watch [S1].
## Diligence questions and limitations
Unresolved questions [S1].
'''


def finding(severity='major'):
    return {'id':'R1','severity':severity,'issue':'Claim is unsupported.','source_refs':['S1'],'fix':'Find a supporting primary source or remove the claim.'}


class StructuredReviewTests(unittest.TestCase):
    def test_strict_json_accepts_one_complete_fence(self):
        self.assertEqual(parse_json('```json\n{"a": [1, 2]}\n```'),{'a':[1,2]})
        self.assertEqual(parse_json(' {"a":1} '),{'a':1})

    def test_json_does_not_accept_prose_duplicates_or_nonfinite_values(self):
        for text in ['Here is JSON: {"a":1}', '{"a":1} trailing', '{"a":1,"a":2}', '{"a":NaN}', '{"a":1e999}', '```json\n{}\n``` more', '']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_json(text)

    def test_review_rejects_contradictory_approval(self):
        with self.assertRaisesRegex(ValueError,'contradicts'):
            parse_review(json.dumps({'verdict':'pass','findings':[finding()]}))
        with self.assertRaisesRegex(ValueError,'contradicts'):
            parse_review(json.dumps({'verdict':'revise','findings':[]}))
        review=parse_review(json.dumps({'verdict':'revise','findings':[finding()]}))
        self.assertEqual(review['findings'][0]['source_refs'],['S1'])
        self.assertEqual(parse_review(json.dumps({'verdict':'pass','findings':[finding('minor')]}))['verdict'],'pass')

    def test_review_rejects_malformed_or_archived_findings(self):
        bad=[]
        for key in ['id','severity','issue','source_refs','fix']:
            row=finding();del row[key];bad.append(row)
        row=finding();row['source_refs']='S1';bad.append(row)
        row=finding();row['source_refs']=['https://example.com'];bad.append(row)
        row=finding();row['status']='resolved';bad.append(row)
        for row in bad:
            with self.assertRaises(ValueError):
                parse_review(json.dumps({'verdict':'revise','findings':[row]}))
        with self.assertRaises(ValueError):
            parse_review(json.dumps({'verdict':'revise','findings':[finding(),finding()]}))

    def test_material_findings_quote_the_frozen_current_memo_exactly(self):
        for severity in ('major', 'blocker'):
            with self.subTest(severity=severity):
                row=finding(severity)
                row['current_excerpt']='Revenue is 100 [S1].'
                review=parse_review(json.dumps({'verdict':'revise','findings':[row]}), memo=MEMO)
                self.assertEqual(review['findings'][0]['current_excerpt'],row['current_excerpt'])
                self.assertEqual(review['findings'][0]['current_excerpt_match'],'literal')
        # The optional frozen-memo contract does not break standalone consumers.
        self.assertEqual(parse_review(json.dumps({'verdict':'revise','findings':[finding()]}))['verdict'],'revise')

    def test_material_findings_reject_absent_stale_or_unbounded_current_quotes(self):
        for severity in ('major', 'blocker'):
            for excerpt in (None, '', '   ', 100, 'Revenue is 99 [S1].',
                            'Revenue was 100 [S1].', 'x' * 1201):
                with self.subTest(severity=severity, excerpt=excerpt):
                    row=finding(severity)
                    if excerpt is not None:
                        row['current_excerpt']=excerpt
                    with self.assertRaisesRegex(ValueError,'current_excerpt'):
                        parse_review(json.dumps({'verdict':'revise','findings':[row]}), memo=MEMO)

    def test_material_quote_accepts_markdown_and_whitespace_with_match_provenance(self):
        memo='- **Operating leases:** The **$12.912 billion** liability is approximately `$0.89` per share.\n'
        row=finding()
        row['current_excerpt']='Operating leases: The $12.912 billion liability is approximately $0.89\n  per share.'
        review=parse_review(json.dumps({'verdict':'revise','findings':[row]}), memo=memo)
        self.assertEqual(review['findings'][0]['current_excerpt_match'],'render-normalized')
        self.assertEqual(review['findings'][0]['current_excerpt'],row['current_excerpt'])
        row['current_excerpt']='Revenue is 100  [S1].'
        review=parse_review(json.dumps({'verdict':'revise','findings':[row]}), memo=MEMO)
        self.assertEqual(review['findings'][0]['current_excerpt_match'],'render-normalized')
        row['current_excerpt']='__Revenue__ is `100` [S1].'
        review=parse_review(json.dumps({'verdict':'revise','findings':[row]}), memo=MEMO)
        self.assertEqual(review['findings'][0]['current_excerpt_match'],'render-normalized')

    def test_smart_quotes_match_without_repairing_words_numbers_or_omissions(self):
        memo='**Apple’s policy:** “Revenue is 100.” The decision is “do not initiate.”'
        row=finding()
        row['current_excerpt']='Apple\'s policy: "Revenue is 100."'
        review=parse_review(json.dumps({'verdict':'revise','findings':[row]}),memo=memo)
        self.assertEqual(review['verdict'],'revise')
        self.assertEqual(review['findings'][0]['severity'],'major')
        self.assertEqual(review['findings'][0]['current_excerpt_match'],'typography-normalized')
        self.assertEqual(review['findings'][0]['current_excerpt'],row['current_excerpt'])
        for excerpt in ['Apples policy: "Revenue is 100."','Apple\'s policy: "Revenue is 99."',
                        'Apple\'s policy: "Revenue was 100."','Apple\'s policy ... "do not initiate."']:
            with self.subTest(excerpt=excerpt):
                row['current_excerpt']=excerpt
                with self.assertRaisesRegex(ValueError,'current_excerpt'):
                    parse_review(json.dumps({'verdict':'revise','findings':[row]}),memo=memo)

    def test_rendered_quote_rejects_changed_words_numbers_and_noncontiguous_text(self):
        memo='- **Operating leases:** The **$12.912 billion** liability is approximately `$0.89` per share.'
        excerpts=['Operating leases: The $12.921 billion liability is approximately $0.89 per share.',
                  'Operating leases: The $12.912 billion asset is approximately $0.89 per share.',
                  'Operating leases: The liability of $12.912 billion is approximately $0.89 per share.',
                  'Operating leases: The $12.912 billion liability ... per share.',
                  'Operating leases: The $12.912 billion liability … per share.',
                  'Operating leases: The $12.912 billion liability per share.']
        for excerpt in excerpts:
            with self.subTest(excerpt=excerpt):
                row=finding(); row['current_excerpt']=excerpt
                with self.assertRaisesRegex(ValueError,'current_excerpt'):
                    parse_review(json.dumps({'verdict':'revise','findings':[row]}), memo=memo)
        for memo,excerpt in [('The market_case has a value.', 'The marketcase has a value.'),
                             ('Value is 2 * 3 * 4.', 'Value is 2 3 4.')]:
            with self.subTest(memo=memo):
                row=finding(); row['current_excerpt']=excerpt
                with self.assertRaisesRegex(ValueError,'current_excerpt'):
                    parse_review(json.dumps({'verdict':'revise','findings':[row]}), memo=memo)

    def test_unmatched_optional_minor_excerpt_is_preserved_as_unverified(self):
        row=finding('minor'); row['current_excerpt']='This wording comes from the prompt, not the memo.'
        review=parse_review(json.dumps({'verdict':'pass','findings':[row]}), memo=MEMO)
        self.assertEqual(review['verdict'],'pass')
        self.assertEqual(review['findings'][0]['severity'],'minor')
        self.assertEqual(review['findings'][0]['current_excerpt'],row['current_excerpt'])
        self.assertEqual(review['findings'][0]['current_excerpt_match'],'not_matched')
        unchecked=parse_review(json.dumps({'verdict':'pass','findings':[row]}))
        self.assertEqual(unchecked['findings'][0]['current_excerpt_match'],'not_checked')
        material=finding(); material['current_excerpt']='Revenue is 100 [S1].'
        row['id']='R2'
        mixed=parse_review(json.dumps({'verdict':'revise','findings':[material,row]}), memo=MEMO)
        self.assertEqual(len(mixed['findings']),2)
        self.assertEqual(mixed['findings'][1]['current_excerpt_match'],'not_matched')

    def test_explicitly_withdrawn_material_findings_invalidate_review(self):
        withdrawals=[('fix','No correction needed. All figures match the model.'),
                     ('fix','See F2. No additional correction needed for the items verified here.'),
                     ('issue','This finding is already resolved in the current draft.'),
                     ('fix','Already resolved in the current draft.'),
                     ('fix','This finding is downgraded; see F2 for the genuine blocker.'),
                     ('issue','Minor presentation issue only; the decomposition is disclosed nearby.'),
                     ('fix','Withdraw this finding.')]
        for severity in ('major', 'blocker'):
            for key,value in withdrawals:
                with self.subTest(severity=severity,key=key,value=value):
                    row=finding(severity)
                    row['current_excerpt']='Revenue is 100 [S1].'
                    row[key]=value
                    with self.assertRaisesRegex(ValueError,'withdraws or requests no correction'):
                        parse_review(json.dumps({'verdict':'revise','findings':[row]}), memo=MEMO)
        # Locating an allegation does not validate it, but quoted memo wording
        # must not itself be mistaken for a reviewer withdrawing an allegation.
        row=finding()
        row['current_excerpt']='No correction needed.'
        row['issue']='The memo says "No correction needed." despite an unsupported claim.'
        self.assertEqual(parse_review(json.dumps({'verdict':'revise','findings':[row]}),
                                      memo='No correction needed.')['verdict'],'revise')
        row['fix']='No correction needed.'
        with self.assertRaisesRegex(ValueError,'withdraws or requests no correction'):
            parse_review(json.dumps({'verdict':'revise','findings':[row]}), memo='No correction needed.')

    def test_frozen_memo_review_allows_empty_pass_and_minor_only_pass(self):
        self.assertEqual(parse_review('{"verdict":"pass","findings":[]}', memo=MEMO),
                         {'verdict':'pass','findings':[]})
        review=parse_review(json.dumps({'verdict':'pass','findings':[finding('minor')]}), memo=MEMO)
        self.assertEqual(review['verdict'],'pass')


class DeterministicDiagnosticsTests(unittest.TestCase):
    def test_complete_supported_structure_and_model_values_have_no_diagnostics(self):
        self.assertEqual(inspect_memo(MEMO,[SOURCE],{'weighted_per_share_value':20}),[])

    def test_unknown_citations_and_duplicate_evidence_are_reported(self):
        findings=inspect_memo(MEMO.replace('[S1]','[S99]'),[SOURCE,SOURCE],{'weighted_value_per_share':20})
        self.assertIn('citation-unresolved',{f['id'] for f in findings})
        self.assertIn('source-id-ambiguous',{f['id'] for f in findings})

    def test_source_register_does_not_substitute_for_body_citations(self):
        text=MEMO.replace('[S1]','')+'\n## Source register\n- [S1] Primary source\n'
        self.assertIn('citations-absent',{f['id'] for f in inspect_memo(text,[SOURCE],{'weighted_value_per_share':20})})

    def test_quote_observation_date_is_distinct_from_memo_date(self):
        quote={'id':'S2','kind':'market_snapshot','data':{'regularMarketPrice':344.17,'quote_observed_at_utc':'2026-11-05T21:00:01+00:00'}}
        bad=MEMO+'\nAnalysis Date: 2026-11-06\n\nOn the analysis date, the regular market closing price was $344.17 [S2].\n'
        self.assertIn('quote-date-mismatch',{f['id'] for f in inspect_memo(bad,[SOURCE,quote],{'weighted_value_per_share':20})})
        quote['data']={'regularMarketTime':1793912401,'gmtoffset':-18000}
        self.assertIn('quote-date-mismatch',{f['id'] for f in inspect_memo(bad,[SOURCE,quote],{'weighted_value_per_share':20})})
        good=MEMO+'\nAnalysis Date: 2026-11-06\n\nThe market price observed on 2026-11-05 was $344.17 [S2].\n'
        self.assertNotIn('quote-date-mismatch',{f['id'] for f in inspect_memo(good,[SOURCE,quote],{'weighted_value_per_share':20})})

    def test_visible_valuation_table_and_duplicate_claims_are_checked(self):
        text=MEMO.replace('Weighted intrinsic value per share: $20.00','''| Scenario | Value/share | Probability |
|---|---:|---:|
| Bear | $10.00 | 25% |
| Base | $20.00 | 50% |
| Bull | $30.00 | 25% |
| Weighted | $20.00 | 100% |''')
        values={'weighted_value_per_share':20,'scenarios':{'bear':{'value_per_share':10},'base':{'value_per_share':20},'bull':{'value_per_share':30}}}
        self.assertEqual(inspect_memo(text,[SOURCE],values),[])
        text+='\nBase case value per share: $25.00\n'
        self.assertIn('valuation-value-mismatch',{f['id'] for f in inspect_memo(text,[SOURCE],values)})

    def test_empty_evidence_and_unsupported_targets_are_not_passed(self):
        self.assertEqual(inspect_memo('',[])[0]['severity'],'blocker')
        identifiers={f['id'] for f in inspect_memo(MEMO,[])}
        self.assertIn('evidence-empty',identifiers)
        self.assertIn('valuation-model-missing',identifiers)

    def test_claim_ledger_does_not_claim_semantic_verification(self):
        rows=claim_ledger('Revenue grew [S1].\n\nThis is an assumption.\n\n## Source register\n[S1] URL')
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['source_refs'],['S1'])
        self.assertEqual(rows[1]['source_refs'],[])
        self.assertIn('not_established',rows[0]['semantic_support'])


class GroupedCitationTests(unittest.TestCase):
    def test_grouped_and_excerpt_citations_are_recorded_and_unknowns_detected(self):
        from investmentideas.quality import claim_ledger,inspect_memo
        memo='# Executive view\nClaims [S1, S2] and excerpt [S2 E5]; unrecognized [S99; S1].'
        ledger=claim_ledger(memo)
        refs={ref for row in ledger for ref in row['source_refs']}
        self.assertEqual(refs,{'S1','S2','S99'})
        findings=inspect_memo(memo,[{'id':'S1','data':'one'},{'id':'S2','data':'two'}])
        unresolved=next(row for row in findings if row['id']=='citation-unresolved')
        self.assertEqual(unresolved['source_refs'],['S99'])

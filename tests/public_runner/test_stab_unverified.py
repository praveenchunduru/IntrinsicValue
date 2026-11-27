"""A company fact resting only on an unverified excerpt is caught before the paid review (rule M1).

Walmart's first review (2026-11-27) had nine blockers and majors, six of them M1, and each
later round found another. The sentences below are taken from the saved first drafts of
Walmart, Microsoft, Coca-Cola, Alphabet and Apple: the ones the reviewer raised must be
flagged and the ones it accepted must not be.

The first-page table "What the price needs" also lists the flat discount rate, so a count of
the inputs that can reach the price taken from it is right (rule M4, same reviews).
"""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from investmentideas import memo_style, pipeline, quality

from tests.public_runner.test_stab_memo import saved_runs

TIERS = {f'S{number}': 'regulatory_primary' for number in range(1, 12)}
TIERS.update({f'S{number}': 'search_excerpt_unverified' for number in range(12, 42)})

# Raised by the reviewer as M1, or as B1 resting on an unverified excerpt.
REJECTED = (
    'Walmart U.S. advertising rose 37% in the third quarter of fiscal 2027 [S19, unverified].',
    'Membership fee revenue grew 17% [S12, unverified].',
    "Executive pay ties about 82% of the CEO's fiscal 2026 target pay to operating income, sales and ROI goals "
    '[S24, unverified].',
    'The 10-Q describes a working capital deficit that grew with short-term borrowing [S13, unverified].',
    'The fiscal 2027 third-quarter results reported inventory up 8.1% [S12, unverified].',
    'It rests on adjusted figures [S21, unverified].',
    'An unverified research excerpt estimates concentrate operating margins of 40% to 60% and finished-product '
    'margins of 6% to 7% [S17, unverified].',
    "An unverified excerpt from the 2025 annual report describes Sam's Club as a membership-only club whose "
    "membership income is a significant component of the segment's results [S31, unverified].",
    'Price investment funded by a refund that does not recur could leave lower prices in place after the refund '
    'ends [S16, unverified].',
    'The September-quarter capital expenditure including finance leases ($45.2 billion) and the delivery-delay '
    'explanation come only from an unverified call excerpt [S30 unverified].',
    'The constructive case adds iPhone Duo, a new form factor that sold out at launch and remains supply-constrained '
    '(unverified [S19]).',
)
# House comparisons with an unverified figure the reviewer raised (Microsoft, Coca-Cola, Alphabet).
REJECTED_COMPARISONS = (
    'The house operating forecast for the first year is close to consensus.',
    'The house operating forecast is close to outside expectations for 2026.',
    '**Revenue against outside estimates.** House revenue for 2026 is $495.9B, against an unverified consensus '
    'average of $499.6B [S20, unverified].',
)
# Accepted by the reviewer in the same drafts.
ACCEPTED = (
    'An unverified aggregator shows current-year consensus revenue of $50.5B and EPS growth of about 11% '
    '[S19, unverified].',
    'A press report, also unverified, says management raised guidance to at least 5% organic revenue growth and at '
    'least 11% comparable EPS growth [S18, unverified].',
    "An outside bottler survey reports that bottler volumes tracked Coca-Cola's unit case growth within 80 basis "
    'points over six quarters (unverified [S16 unverified]).',
    'It holds that contracted demand will keep Azure growing near 40% into FY28 [S17, unverified].',
    'One outside view argues that the market underrates Cloud growth [S17, unverified].',
    'An outside analysis attributes $2.9B of that quarter to a tariff refund, which it treats as non-recurring '
    '[S16, unverified].',
    'Management describes the refund as partly offset by price investment [S15, unverified].',
    'In the third quarter of fiscal 2027, the CFO cited the largest food share gains in three years [S19, unverified].',
    'An officer-succession filing dated November 2026 appears only in an unverified excerpt [S25 unverified].',
    'Consensus EPS of $2.89 for the year to January 2027 and $3.23 for the following year is unverified context '
    '[S20, unverified].',
    'Morningstar describes double-digit average organic revenue growth over five years and a wide moat [S16].',
    'They also report company guidance of at least 5% organic growth [S18].',
    'Outside views on a large backlog and a temporary monetization gap are context only [S15, unverified].',
    # Primary support: not this check's concern.
    'Walmart U.S. gross margin rose from 26.8% to 27.2% to 27.5% over fiscal 2024 to 2026 [S4].',
)


def flagged(sentence):
    return quality.unverified_company_facts(sentence, TIERS)


class UnverifiedCompanyFactTests(unittest.TestCase):
    def test_raised_sentences_are_flagged(self):
        for sentence in REJECTED:
            with self.subTest(sentence=sentence[:60]):
                self.assertEqual([kind for kind, _ in flagged(sentence)], ['fact'])
        for sentence in REJECTED_COMPARISONS:
            with self.subTest(sentence=sentence[:60]):
                self.assertEqual([kind for kind, _ in flagged(sentence)], ['comparison'])

    def test_accepted_sentences_are_not_flagged(self):
        for sentence in ACCEPTED:
            with self.subTest(sentence=sentence[:60]):
                self.assertEqual(flagged(sentence), [])

    def test_it_is_a_minor_free_check_with_the_fix(self):
        sources = [{'id': ref, 'source_tier': tier} for ref, tier in TIERS.items()]
        memo = ('## 1. Valuation conclusion\n\n' + REJECTED[0] + ' ' + ACCEPTED[0] + '\n')
        finding = next(item for item in quality.prereview_checks(memo, sources) if item['id'] == 'unverified-company-fact')
        # Minor: sent to the fix call before the review, never a reason to withhold approval alone.
        self.assertEqual(finding['severity'], 'minor')
        self.assertEqual([item['sentence'] for item in finding['items']], [REJECTED[0]])
        self.assertIn('Never keep it as a company fact with an "unverified" label', finding['fix'])

    def test_the_writer_fix_and_edit_calls_share_the_rule(self):
        self.assertIn(quality.UNVERIFIED_FACT_RULE, quality.RUBRIC)
        self.assertIn('is not an outside party', quality.UNVERIFIED_FACT_RULE)
        self.assertIn(quality.UNVERIFIED_FACT_FIX, pipeline.EDIT_INSTRUCTIONS)
        # The wrong example is one the check flags; the right one passes.
        wrong, right = (part.split('"')[1] for part in quality.UNVERIFIED_FACT_RULE.split('Wrong: ')[1].split('Right: '))
        self.assertTrue(flagged(wrong))
        self.assertEqual(flagged(right), [])
        # The section brief no longer asks for the comparison the check flags.
        self.assertNotIn('close to consensus, say so', pipeline.SECTIONS)
        self.assertIn('do not compare the house forecast\nwith them', pipeline.SECTIONS)


def implied(solved=.033, required=.087):
    row = {'implied_return': solved, 'required_return_flat_equivalent': required}
    return {'scenarios': {'base': dict(row)}, 'probability_weighted_value': dict(row)}


class DiscountRateRowTests(unittest.TestCase):
    STYLE = memo_style.Style('USD')

    def test_the_row_comes_from_the_implied_return(self):
        result = {'implied_return_at_price': implied(), 'scenarios': {'base': {}}}
        self.assertEqual(pipeline._discount_rate_requirement(result, self.STYLE),
                         ['Discount rate, one flat rate', '8.7% house-equivalent', '3.3%'])

    def test_no_solution_says_so_with_the_bound(self):
        row = {'implied_return': None, 'required_return_flat_equivalent': .09,
               'price_bound': {'search_range': [.005, .3], 'price_position': 'above_highest_value',
                               'highest_value': 180.0, 'rate_at_highest_value': .005}}
        result = {'implied_return_at_price': {'scenarios': {'base': row}, 'probability_weighted_value': row},
                  'scenarios': {'base': {}}}
        self.assertEqual(pipeline._discount_rate_requirement(result, self.STYLE)[2],
                         'not reached within the range tested (0.5% to 30%; highest value $180 at 0.5%)')
        self.assertIsNone(pipeline._discount_rate_requirement({'scenarios': {}}, self.STYLE))

    def test_saved_first_pages_list_the_discount_rate(self):
        found = 0
        for folder in saved_runs():
            state = json.loads((Path(folder) / 'state.json').read_text(encoding='utf-8'))
            result, plan = state['valuation'], state['plan']
            if not result.get('implied_return_at_price') or not (result.get('price_requirements') or {}).get('rows'):
                continue
            page = pipeline.computed_summary(plan, result)
            # Changed 2026-11-27 (readability review): a solved flat rate is a row; an unsolved one is named once
            # among the inputs that cannot reach the price alone.
            table = page.split('**What the price needs.**')[1].split('**Dated tests')[0]
            self.assertTrue('| Discount rate, one flat rate in every year |' in table or 'one flat discount rate' in table,
                            folder)
            self.assertIn('Each input changed alone, the rest held at the base case.', table, folder)
            found += 1
        if not found:
            self.skipTest('INTRINSICVALUE_SAVED_RUNS names no saved run with an implied return')

    def test_sections_and_rubric_point_to_the_table(self):
        self.assertIn('the flat discount\nrate among', pipeline.SECTIONS)
        self.assertIn('the flat discount rate included', quality.RUBRIC)


class ComputedCalledReportedTests(unittest.TestCase):
    """Rule M6: the final Walmart run's first review raised three computed metrics called reported."""
    RAISED = (
        "Walmart's reported ROIC has stayed between 10.0% and 14.4% for five years [S2].",
        'The reported three-year incremental rate is 2.6 [S2].',
        'The company reported a 4.6% operating margin for the six months to 2026-07-31 [S2][S5].',
        'The capital-light point is supported by primary data: capex was 1.9% of revenue in FY2026 and reported ROIC '
        'was 129.4% [S2].',
    )
    ACCEPTED = (
        'Walmart has never reported a consolidated operating margin above 4.5% in the five years shown [S2].',
        'Reported consolidated operating margin was 46.8% for FY26, the best annual figure in five years.',
        'ROIC computed from reported figures stayed between 10.0% and 14.4% [S2].',
        'The incremental ROIC in the reported table is not meaningful for 2023-2025 [S2].',
        'KO earns high consolidated ROIC, and reported company revenue grows slowly.',
    )

    def test_raised_and_accepted(self):
        for sentence in self.RAISED:
            with self.subTest(sentence=sentence[:50]):
                self.assertEqual(quality.computed_called_reported(sentence), [sentence])
        for sentence in self.ACCEPTED:
            with self.subTest(sentence=sentence[:50]):
                self.assertEqual(quality.computed_called_reported(sentence), [])

    def test_it_is_a_minor_free_check_with_the_fix(self):
        memo = '## 1. Valuation conclusion\n\n' + self.RAISED[1] + '\n'
        finding = next(item for item in quality.prereview_checks(memo, [{'id': 'S2', 'source_tier': 'regulatory_primary'}])
                       if item['id'] == 'computed-called-reported')
        self.assertEqual(finding['severity'], 'minor')
        self.assertIn('computed from reported figures', finding['fix'])
        self.assertIn('"computed from reported figures", never "reported"', quality.WRITING_STANDARD)


if __name__ == '__main__':
    unittest.main()

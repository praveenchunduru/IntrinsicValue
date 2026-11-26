"""Plans give operating costs as shares of revenue; Python builds the dollars.

Live plans (2026-11-15) wrote Microsoft's R&D and SG&A in dollars that fell from
about 19% to 10% of revenue, which lifted every scenario's margin without a
stated reason. Shares keep costs tied to each scenario's own revenue path.
"""
import copy
import unittest

from investmentideas import pipeline
from investmentideas.valuation import evaluate

from tests.public_runner.test_market_consistency import plan as simple_plan
from tests.public_runner.test_underwriting_integration import inputs


def locked(**scenario_fields):
    value = inputs()
    value['reported_inputs'] = {'operating_reference': {'lines': {
        'research_and_development': {'value': 120, 'source_refs': ['S1']},
        'selling_and_marketing': {'value': 30, 'source_refs': ['S1']},
        'general_and_administrative': {'value': 20, 'source_refs': ['S1']},
        'operating_expenses': {'value': 200, 'source_refs': ['S1']}}}}
    for scenario in value['operating_model']['scenarios'].values():
        scenario['components']['products']['growth_rates'] = [.1] * 5
        scenario.pop('expenses')
        scenario.update(copy.deepcopy(scenario_fields))
    return value


class ExpenseShareTests(unittest.TestCase):
    def test_a_share_path_moves_linearly_to_its_target_and_scales_with_revenue(self):
        value = locked(expense_shares={'research_and_development': {'year1': .10, 'target': .08, 'target_year': 3},
                                       'selling_general_administrative': .05})
        changes = []
        pipeline._expenses_from_shares(value, changes)
        base = value['operating_model']['scenarios']['base']
        revenue = [700 * 1.1 ** year + 300 for year in range(1, 6)]
        self.assertEqual(base['expenses']['research_and_development'],
                         [pipeline._figure(share * amount) for share, amount in zip((.10, .09, .08, .08, .08), revenue)])
        self.assertEqual(base['expenses']['selling_general_administrative'], [pipeline._figure(.05 * amount) for amount in revenue])
        self.assertNotIn('expense_shares', base)
        self.assertEqual(len(changes), 3)
        evaluate(value)

    def test_without_shares_every_reported_cost_share_is_held(self):
        value = locked()
        pipeline._expenses_from_shares(value, [])
        expenses = value['operating_model']['scenarios']['bear']['expenses']
        revenue = [700 * 1.1 ** year + 300 for year in range(1, 6)]
        # SG&A is selling and marketing plus G&A; the rest of reported operating expenses is held too.
        self.assertEqual(expenses['research_and_development'], [pipeline._figure(.12 * amount) for amount in revenue])
        self.assertEqual(expenses['selling_general_administrative'], [pipeline._figure(.05 * amount) for amount in revenue])
        self.assertEqual(expenses['other_operating_expenses'], [pipeline._figure(.03 * amount) for amount in revenue])

    def test_a_required_line_left_out_holds_its_reported_share(self):
        value = locked(expense_shares={'research_and_development': .10})
        pipeline._expenses_from_shares(value, [])
        expenses = value['operating_model']['scenarios']['base']['expenses']
        self.assertEqual(expenses['selling_general_administrative'][0], pipeline._figure(.05 * 1070))
        self.assertNotIn('other_operating_expenses', expenses)

    def test_dollar_expenses_without_shares_are_kept(self):
        value = inputs()
        before = copy.deepcopy(value['operating_model'])
        pipeline._expenses_from_shares(value, [])
        self.assertEqual(value['operating_model'], before)

    def test_invalid_shares_are_rejected(self):
        for spec, message in (({'research_and_development': 1.2, 'selling_general_administrative': .05}, 'decimals from 0 to 1'),
                              ({'research_and_development': {'year1': .1, 'target': .1, 'target_year': 9},
                                'selling_general_administrative': .05}, 'target_year must be 1 to 5'),
                              ({'research_and_development': [.1, .1], 'selling_general_administrative': .05}, 'needs 5 shares')):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                pipeline._expenses_from_shares(locked(expense_shares=spec), [])

    def test_a_revision_sees_shares_that_rebuild_the_same_dollars(self):
        value = locked(expense_shares={'research_and_development': {'year1': .10, 'target': .08, 'target_year': 3},
                                       'selling_general_administrative': .05})
        pipeline._expenses_from_shares(value, [])
        built = copy.deepcopy(value['operating_model'])
        view = pipeline._proposal_view({'operating_model': value['operating_model']})
        scenario = view['operating_model']['scenarios']['base']
        self.assertNotIn('expenses', scenario)
        self.assertEqual(scenario['expense_shares']['selling_general_administrative'], [.05] * 5)
        rebuilt = dict(value, operating_model=view['operating_model'])
        pipeline._expenses_from_shares(rebuilt, [])
        for name, case in built['scenarios'].items():
            for line, values in case['expenses'].items():
                for got, want in zip(rebuilt['operating_model']['scenarios'][name]['expenses'][line], values):
                    self.assertLessEqual(abs(got - want), 1)


class DamodaranPanelRowTests(unittest.TestCase):
    def rows(self, value):
        return {row['name']: row for row in evaluate(value)['convention_panel']['rows']}

    def test_terminal_return_four_points_above_the_terminal_rate(self):
        result = evaluate(simple_plan())
        row = {row['name']: row for row in result['convention_panel']['rows']}['terminal_return_rate_plus_4_points']
        self.assertEqual(row['status'], 'calculated')
        terminal = result['discounting']['terminal_rate']
        changed = simple_plan()
        changed['scenarios']['base']['terminal_roic'] = terminal + .04
        self.assertAlmostEqual(row['per_share_value'], evaluate(changed)['scenarios']['base']['per_share_value'])

    def test_base_margin_held_at_the_reported_peak(self):
        value = simple_plan()
        self.assertEqual(self.rows(value)['base_margin_held_at_reported_peak']['status'], 'not_applicable')
        value['cost_path_reference'] = {'max_operating_margin': .15, 'average_operating_margin': .12}
        row = self.rows(value)['base_margin_held_at_reported_peak']
        changed = simple_plan()
        changed['scenarios']['base']['ebit_margins'] = [.15] * 10
        self.assertAlmostEqual(row['per_share_value'], evaluate(changed)['scenarios']['base']['per_share_value'])
        self.assertLess(row['dollar_delta'], 0)
        value['cost_path_reference']['max_operating_margin'] = .25
        self.assertEqual(self.rows(value)['base_margin_held_at_reported_peak']['status'], 'not_applicable')


if __name__ == '__main__':
    unittest.main()

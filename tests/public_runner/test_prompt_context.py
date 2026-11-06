import copy
import hashlib
import json
import unittest

from investmentideas.prompt_context import valuation_context


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def expanded_context(document):
    """Read the documented references and independently verify their hashes."""
    root = json.loads(document)
    if not root['context_metadata']['references_enabled']:
        return root

    def target(pointer):
        result = root
        if not pointer.startswith('/'):
            raise AssertionError('Expected plain JSON pointer, not URI fragment')
        for token in pointer[1:].split('/'):
            key = token.replace('~1', '/').replace('~0', '~')
            result = result[int(key)] if isinstance(result, list) else result[key]
        return result

    def expand(value, active=()):
        if isinstance(value, dict):
            if set(value) == {'same_as', 'sha256'}:
                pointer = value['same_as']
                if pointer in active:
                    raise AssertionError('Circular presentation reference')
                original = expand(target(pointer), active + (pointer,))
                digest = hashlib.sha256(canonical(original).encode()).hexdigest()
                if digest != value['sha256']:
                    raise AssertionError('Presentation reference hash mismatch')
                return original
            return {key: expand(item, active) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item, active) for item in value]
        return value

    return expand(root)


def sample():
    provenance = {'basis': 'Dated reported amounts; unknown claims are not reported zeros. ' * 8,
                  'components': [{'concept': 'reported cash', 'amount': 37, 'date': '2026-09-26'}]}
    plan = {'actuals': {'revenue': 500, 'net_debt': -57, 'other_equity_claims': 1.4, 'shares_outstanding': 14.5},
            'assumptions_label': 'Explicit house assumptions', 'bridge/a~b': provenance,
            'reported_inputs': {'bridge/a~b': copy.deepcopy(provenance)},
            'scenarios': {'base': {'growth_rates': [.08] * 7, 'ebit_margins': [.33] * 7,
                                   'tax_rate': .17, 'wacc': .095, 'probability': .5}},
            'counterfactuals': {},
            'operating_model': {'categories': ['hardware', 'services'], 'AI_fee_start': None},
            'calibration': {'source_ref': 'S33', 'method': 'Matched dates', 'beta_proxy': True},
            'monitoring_table': [{'observation': 'memory cost', 'threshold': 47, 'effect': 'recompute'}]}
    rows = [{'year': i, 'revenue': 500 + i * 40, 'ebit': 160 + i,
             'cash_operating_tax': 20 + i, 'nopat': 140 + i,
             'net_reinvestment': 10 + i, 'fcff': -12.0 if i == 3 else 130 + i,
             'discount_factor': .9 ** i, 'pv_fcff': -8.0 if i == 3 else 100 + i,
             'operating_details': {'hardware': {'revenue': i * 100, 'margin': .39},
                                   'services': {'revenue': i * 30, 'margin': .76}}}
            for i in range(1, 8)]
    base = {'enterprise_value': 2100, 'equity_value': 2155.6, 'net_debt': -57,
            'other_equity_claims': 1.4, 'shares_outstanding': 14.5,
            'per_share_value': 148.66206896551725, 'pv_forecast_fcff': 700,
            'pv_terminal_value': 1400, 'terminal_value_fraction': 2 / 3,
            'terminal_fcff': 190, 'cashflows': rows}
    result = {'weighted_per_share_value': 151.71334882857624,
              'weighted_upside_percent': -55.91906649952749,
              'scenarios': {'base': copy.deepcopy(base)}, 'counterfactuals': {},
              'sensitivity': [{'wacc': .085, 'per_share_value': 179.03471508796383}],
              'computed_diagnostics': {'basis': 'Conditional results', 'failure': {'status': 'unavailable', 'value': None, 'reason': 'not bracketed'},
                                       'negative_value': -14.0, 'zero_value': 0},
              'calibration_result': {'cost_of_equity': .108, 'source_provenance': copy.deepcopy(provenance)},
              'monitoring_table': copy.deepcopy(plan['monitoring_table']),
              'new_unknown_field': {'nested': [{'meaningful': False, 'value': None}]},
              'reverse_dcf': {'status': 'solved', 'search_range': [-.5, .5],
                              'constant_annual_growth_solutions': [.12, .31],
                              'solution_projections': [
                                  {'growth': .12, 'revenue_path': [{'year': 1, 'revenue': 560, 'revenue_increment': 60}, {'year': 2, 'revenue': 627.2, 'revenue_increment': 67.2}]},
                                  {'growth': .31, 'revenue_path': [{'year': 1, 'revenue': 655, 'revenue_increment': 155}, {'year': 2, 'revenue': 858.05, 'revenue_increment': 203.05}]}]},
              'disclosures': ['Countercases have no scenario probability or weighted-value contribution.']}
    for i in range(8):
        name = f'case_{i}'
        overrides = {'wacc': .08 + i / 1000}
        plan['counterfactuals'][name] = overrides
        case = copy.deepcopy(base)
        case.update(overrides=copy.deepcopy(overrides),
                    effective_inputs=dict(plan['scenarios']['base'], **overrides),
                    fixed_input_basis={'actuals': copy.deepcopy(plan['actuals']),
                                       'reference_scenario': 'base', 'no_probability': True,
                                       'basis': provenance['basis']},
                    revenue_path=[{'year': row['year'], 'revenue': row['revenue']} for row in rows])
        case['per_share_value'] += i
        case['cashflows'][2]['fcff'] -= i
        result['counterfactuals'][name] = case
    return plan, result


class ValuationContextTests(unittest.TestCase):
    def test_complete_plan_headlines_and_unknown_results_survive(self):
        plan, result = sample()
        decoded = expanded_context(valuation_context(plan, result))
        self.assertEqual(decoded['plan'], plan)
        for key in result:
            if key != 'counterfactuals':
                self.assertEqual(decoded['result'][key], result[key], key)
        self.assertTrue(decoded['context_metadata']['full_math_artifacts_authoritative'])
        self.assertEqual(decoded['context_metadata']['plan_sha256'], hashlib.sha256(canonical(plan).encode()).hexdigest())
        self.assertEqual(decoded['context_metadata']['result_sha256'], hashlib.sha256(canonical(result).encode()).hexdigest())

    def test_countercases_keep_effective_inputs_headlines_and_boundary_operations(self):
        plan, result = sample()
        decoded = expanded_context(valuation_context(plan, result))['result']
        for name, original in result['counterfactuals'].items():
            case = decoded['counterfactuals'][name]
            for key in original:
                if key != 'cashflows':
                    self.assertEqual(case[key], original[key], (name, key))
            rows = case['cashflows']
            self.assertEqual(rows['row_count'], 7)
            self.assertEqual(rows['first_operating_row'], original['cashflows'][0])
            self.assertEqual(rows['last_operating_row'], original['cashflows'][-1])
            self.assertEqual([row['fcff'] for row in rows['annual_aggregate_cashflows']],
                             [row['fcff'] for row in original['cashflows']])
            self.assertEqual([row['pv_fcff'] for row in rows['annual_aggregate_cashflows']],
                             [row['pv_fcff'] for row in original['cashflows']])

    def test_multiple_reverse_solutions_null_failures_and_negative_values_remain(self):
        plan, result = sample()
        decoded = expanded_context(valuation_context(plan, result))['result']
        self.assertEqual(decoded['reverse_dcf'], result['reverse_dcf'])
        self.assertEqual(decoded['computed_diagnostics'], result['computed_diagnostics'])
        self.assertEqual(decoded['scenarios']['base']['cashflows'][2]['fcff'], -12.0)

    def test_immutable_deterministic_and_smaller_for_repeated_eight_case_artifact(self):
        plan, result = sample()
        before = copy.deepcopy((plan, result))
        output = valuation_context(plan, result)
        self.assertEqual((plan, result), before)
        self.assertEqual(output, valuation_context(dict(reversed(list(plan.items()))), result))
        self.assertLess(len(output.encode()), len(canonical({'plan': plan, 'result': result}).encode()))
        self.assertIn('same_as', output)
        self.assertIn('~1', output)
        self.assertIn('~0', output)

    def test_empty_short_or_unrecognized_case_cashflows_are_retained(self):
        for rows in ([], None, [{'fcff': 0}], [{'note': 'unknown'}] * 4, ['unknown', None, 0]):
            result = {'countercases': {'trial': {'cashflows': rows, 'per_share_value': None}}}
            decoded = expanded_context(valuation_context({}, result))
            self.assertEqual(decoded['result'], result)

    def test_percent_space_and_fragment_characters_are_literal_pointer_tokens(self):
        value = {'note': 'Unique source detail. ' * 30}
        plan = {'a%2Fb #': copy.deepcopy(value), 'duplicate': copy.deepcopy(value)}
        output = valuation_context(plan, {})
        self.assertIn('/plan/a%2Fb #', output)
        self.assertEqual(expanded_context(output)['plan'], plan)

    def test_original_reference_shaped_objects_are_never_reinterpreted(self):
        literal = {'same_as': '/not/a/context/reference', 'sha256': 'original domain field'}
        plan = {'domain_value': literal, 'other': {'note': 'Duplicated. ' * 30}}
        result = {'other': copy.deepcopy(plan['other'])}
        output = valuation_context(plan, result)
        self.assertFalse(json.loads(output)['context_metadata']['references_enabled'])
        self.assertEqual(expanded_context(output)['plan'], plan)
        self.assertEqual(expanded_context(output)['result'], result)

    def test_rejects_nonobjects_and_nonfinite_math_without_silent_substitution(self):
        for plan, result in ((None, {}), ({}, [])):
            with self.assertRaises(ValueError):
                valuation_context(plan, result)
        for number in (float('nan'), float('inf'), float('-inf')):
            with self.assertRaises(ValueError):
                valuation_context({'tax_rate': number}, {})


if __name__ == '__main__':
    unittest.main()

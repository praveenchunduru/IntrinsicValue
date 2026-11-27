"""Damaged and edited inputs (adversarial CLI test, 2026-11-27): plain refusals, never a traceback.

Covers the revalue bounds and key checks in valuation.py, the snapshot identity and
saved-state checks in pipeline.run/_run, the preflight's stop text, and the
tools/rerender.py and tools/replay.py error handling. Nothing here uses the network.
"""
import contextlib
import copy
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from investmentideas import core, inputs, pipeline
from investmentideas.valuation import MAX_SALES_TO_CAPITAL, MIN_SALES_TO_CAPITAL, evaluate

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import replay  # noqa: E402
import rerender  # noqa: E402

EXAMPLE = ROOT / 'examples' / 'AAPL-2026-11-06-revised' / 'assumptions.json'


def example():
    return json.loads(EXAMPLE.read_text(encoding='utf-8'))


def with_ratio(plan, ratio):
    for scenario in plan['scenarios'].values():
        scenario['sales_to_capital'] = ratio
        scenario.pop('sales_to_capital_path', None)
    return plan


class SalesToCapitalBoundsTests(unittest.TestCase):
    def test_ratios_outside_the_range_are_refused_with_the_range(self):
        for ratio in (1e-9, .01, .099, 50.5, 1e9):
            with self.subTest(ratio=ratio), self.assertRaises(ValueError) as caught:
                evaluate(with_ratio(example(), ratio))
            self.assertIn('sales_to_capital', str(caught.exception))
            self.assertIn(f'{MIN_SALES_TO_CAPITAL:g} to {MAX_SALES_TO_CAPITAL:g}', str(caught.exception))

    def test_a_path_with_one_ratio_below_the_floor_is_refused(self):
        plan = example()
        horizon = len(next(iter(plan['operating_model']['scenarios']['base']['components'].values()))['growth_rates'])
        plan['scenarios']['base']['sales_to_capital'] = [2.0] * (horizon - 1) + [.05]
        plan['scenarios']['base'].pop('sales_to_capital_path', None)
        with self.assertRaisesRegex(ValueError, 'is 0.05, outside the supported range'):
            evaluate(plan)

    def test_ratios_at_the_bounds_value_and_a_negative_result_is_flagged(self):
        self.assertTrue(math.isfinite(evaluate(with_ratio(example(), MAX_SALES_TO_CAPITAL))['weighted_per_share_value']))
        low = evaluate(with_ratio(example(), MIN_SALES_TO_CAPITAL))
        self.assertTrue(any('negative' in warning for warning in low['value_warnings']))
        self.assertTrue(any('times the market price' in warning for warning in low['value_warnings']))

    def test_the_shipped_example_carries_no_warnings(self):
        result = evaluate(example())
        self.assertNotIn('value_warnings', result)
        self.assertNotIn('input_warnings', result)
        self.assertAlmostEqual(result['weighted_per_share_value'], 163.60923061767494, places=9)


class NetDebtConsistencyTests(unittest.TestCase):
    def test_an_edited_actual_net_debt_that_contradicts_its_bridge_is_refused(self):
        plan = example()
        plan['actuals']['net_debt'] = 1e11
        with self.assertRaises(ValueError) as caught:
            evaluate(plan)
        text = str(caught.exception)
        for field in ('actuals.net_debt', 'net_debt_bridge.net_debt', 'net_debt_bridge.debt',
                      'discount_rate.financial_debt.value'):
            self.assertIn(field, text)

    def test_a_bridge_whose_net_debt_is_not_debt_less_cash_is_refused(self):
        plan = example()
        plan['net_debt_bridge']['debt'] += 1e10
        with self.assertRaises(ValueError) as caught:
            evaluate(plan)
        self.assertIn('net_debt_bridge.selected_cash_and_nonoperating_investments', str(caught.exception))

    def test_a_consistent_net_debt_edit_values_and_moves_the_value(self):
        plan = example()
        bridge, step = plan['net_debt_bridge'], 1e10
        bridge['debt'] += step
        bridge['net_debt'] += step
        plan['actuals']['net_debt'] += step
        plan['discount_rate']['financial_debt']['value'] += step
        if 'reported_inputs' in plan:
            plan['reported_inputs']['actuals']['net_debt'] += step
        result = evaluate(plan)
        self.assertLess(result['weighted_per_share_value'], evaluate(example())['weighted_per_share_value'])

    def test_a_financial_debt_conflict_names_both_figures(self):
        plan = example()
        plan['discount_rate']['financial_debt']['value'] += 1e9
        with self.assertRaises(ValueError) as caught:
            evaluate(plan)
        self.assertIn('net_debt_bridge.debt', str(caught.exception))
        self.assertIn('discount_rate.financial_debt.value', str(caught.exception))

    def test_an_edit_to_the_filed_record_alone_is_reported(self):
        plan = example()
        plan['reported_inputs']['actuals']['revenue'] = 1.0
        result = evaluate(plan)
        self.assertEqual(result['weighted_per_share_value'], evaluate(example())['weighted_per_share_value'])
        self.assertIn('reported_inputs.actuals.revenue', result['input_warnings'][0])


class UnknownKeyTests(unittest.TestCase):
    def test_a_misspelt_scenario_key_is_refused_with_the_valid_name(self):
        plan = example()
        plan['scenarios']['base']['terminal_growht'] = 0.0
        with self.assertRaises(ValueError) as caught:
            evaluate(plan)
        self.assertIn('scenarios.base.terminal_growht', str(caught.exception))
        self.assertIn('scenarios.base.terminal_growth', str(caught.exception))

    def test_a_misspelt_actual_is_refused(self):
        plan = example()
        plan['actuals']['net_dept'] = 5
        with self.assertRaisesRegex(ValueError, 'actuals.net_debt'):
            evaluate(plan)

    def test_other_extra_keys_are_reported_and_leave_the_value_unchanged(self):
        plan = example()
        plan['zzz_unknown'] = 1
        plan['scenarios']['base']['my_note'] = 'kept for my own records'
        result = evaluate(plan)
        self.assertEqual(result['weighted_per_share_value'], evaluate(example())['weighted_per_share_value'])
        self.assertEqual(len(result['input_warnings']), 2)
        self.assertIn('zzz_unknown', result['input_warnings'][0])

    def test_documented_optional_keys_are_known(self):
        plan = example()
        plan['scenarios']['base']['terminal_growth_basis'] = 'stated'
        plan['entry_policy_note'] = 'a long key well away from any known name'
        result = evaluate(plan)
        self.assertEqual(result['input_warnings'], ['entry_policy_note is not an input this model reads and was ignored.'])


class FieldMessageTests(unittest.TestCase):
    def test_valuation_date_errors_name_valuation_date(self):
        for value, expected in (('2026-13-45', 'real date'), ('2099-01-01', 'discount_rate.as_of')):
            plan = example()
            plan['valuation_date'] = value
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                evaluate(plan)
            self.assertIn('valuation_date', str(caught.exception))
            self.assertIn(expected, str(caught.exception))

    def test_share_count_and_price_edits_say_what_is_wrong(self):
        plan = example()
        plan['actuals']['shares_outstanding'] = 0
        if 'reported_inputs' in plan:
            plan['reported_inputs']['actuals']['shares_outstanding'] = 0
        with self.assertRaisesRegex(ValueError, 'must be positive'):
            evaluate(plan)
        plan = example()
        plan['actuals']['shares_outstanding'] = 1
        with self.assertRaisesRegex(ValueError, 'set discount_rate.market_equity.value to'):
            evaluate(plan)


WALMART = [{'id': 'S1', 'title': 'Quote', 'kind': 'market_snapshot', 'data': {'symbol': 'WMT'}},
           {'id': 'S2', 'title': 'Facts', 'kind': 'sec_facts', 'source_tier': 'regulatory_primary',
            'data': {'metadata': {'cik': 104169, 'issuer': 'Walmart Inc.', 'ticker': 'WMT'}}}]


class SnapshotIdentityTests(unittest.TestCase):
    def test_another_company_snapshot_is_refused(self):
        message = inputs.snapshot_entity_conflict('AAPL', WALMART)
        self.assertIn('Walmart Inc.', message)
        self.assertIn('AAPL', message)
        self.assertIsNone(inputs.snapshot_entity_conflict('WMT', WALMART))
        self.assertIsNone(inputs.snapshot_entity_conflict('wmt', WALMART))

    def test_share_classes_and_spellings_match(self):
        directory = {'0': {'cik_str': 1652044, 'ticker': 'GOOGL', 'title': 'Alphabet Inc.'},
                     '1': {'cik_str': 1652044, 'ticker': 'GOOG', 'title': 'Alphabet Inc.'}}
        alphabet = [{'id': 'S1', 'kind': 'sec_facts', 'source_tier': 'regulatory_primary',
                     'data': {'metadata': {'cik': 1652044, 'ticker': 'GOOGL'}}}]
        self.assertIsNone(inputs.snapshot_entity_conflict('GOOG', alphabet, directory))
        quote = [{'id': 'S1', 'kind': 'market_snapshot', 'data': {'symbol': 'BRK-B'}}]
        self.assertIsNone(inputs.snapshot_entity_conflict('BRK.B', quote, {}))
        self.assertIsNone(inputs.snapshot_entity_conflict('AAPL', [{'id': 'S1', 'kind': 'note', 'data': {}}], {}))

    def test_run_refuses_before_any_folder_or_router(self):
        snapshot = [dict(item, title='x') for item in WALMART]
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(core, 'Router', side_effect=AssertionError('no router before the identity check')):
            with self.assertRaisesRegex(ValueError, 'Walmart'):
                pipeline.run('AAPL', 'sk-test', directory=directory, source_snapshot=snapshot)
            self.assertEqual(list(Path(directory).iterdir()), [])


class SavedStateTests(unittest.TestCase):
    def run_folder(self, directory, ticker='WMT'):
        folder = Path(directory) / ticker / 'full-0000000000000000'
        folder.mkdir(parents=True)
        (folder / 'run-identity.json').write_text('{"id": "x"}', encoding='utf-8')
        return folder

    def test_a_damaged_state_names_the_file_and_the_way_on(self):
        for text, reason in (('', 'empty'), ('{"ledger": [', 'cut short'), ('[]', 'JSON list'),
                             ('{"ledger": {}, "stages": {}, "sources": []}', 'wrong type')):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                folder = self.run_folder(directory)
                (folder / 'state.json').write_text(text, encoding='utf-8')
                with self.assertRaises(ValueError) as caught:
                    pipeline._run(folder, 'WMT', '', 'e', 's', 20.0, '', lambda message: None, 3, '', [], None, None, None)
                message = str(caught.exception)
                self.assertIn(str(folder / 'state.json'), message)
                self.assertIn(reason, message)
                self.assertIn('--output', message)

    def test_state_is_saved_in_one_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            path.write_text('{"old": true}', encoding='utf-8')
            with patch.object(Path, 'write_text', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    core.save(path, {'new': True})
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'old': True})

    def test_a_damaged_sibling_marker_is_skipped_with_a_notice(self):
        snapshot = [{'id': 'S1', 'title': 'Quote', 'kind': 'market_snapshot', 'data': {'symbol': 'WMT'}}]
        for text in ('[]', '{bad', ''):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                other = Path(directory) / 'WMT' / 'full-zzzz'
                other.mkdir(parents=True)
                (other / 'run-identity.json').write_text(text, encoding='utf-8')
                notices = []
                with self.assertRaises(RuntimeError) as caught:
                    pipeline.run('WMT', '', directory=directory, source_snapshot=snapshot, progress=notices.append)
                self.assertTrue(any('Skipped' in notice and 'full-zzzz' in notice for notice in notices))
                # The run reached its own preflight, which names the snapshot as the cause.
                self.assertIn('supplied snapshot holds no SEC company facts', str(caught.exception))
                self.assertNotIn('share class', str(caught.exception))


class CoverageStopTextTests(unittest.TestCase):
    def test_no_sec_access_is_named_and_share_classes_are_not_blamed(self):
        reported = inputs.canonical_reported_inputs([])
        self.assertFalse(any('share class' in note for note in reported['coverage']['notes']))
        gaps = ['SEC contact email required: use --sec-contact, the local SEC contact field, or SEC_CONTACT_EMAIL']
        message, recovery = pipeline._coverage_stop_text(reported['coverage'], {}, gaps, False)
        self.assertIn('no SEC contact email was set', message)
        self.assertNotIn('share class', message)
        self.assertIn('SEC_CONTACT_EMAIL', recovery)
        message, recovery = pipeline._coverage_stop_text(reported['coverage'], {}, ['SEC filing history could not be retrieved'], False)
        self.assertIn('sec.gov', message)

    def test_after_sec_access_worked_the_hint_does_not_ask_for_sec_access(self):
        coverage = {'recognized_primary_source_ids': ['S2'], 'missing_fields': ['revenue'],
                    'conflicts': ['Conflicting primary values for latest annual revenue.'], 'notes': []}
        message, recovery = pipeline._coverage_stop_text(coverage, {}, [], False)
        self.assertNotIn('--sec-contact', recovery)
        self.assertNotIn('..', message)
        self.assertNotIn('  ', message)
        coverage = {'recognized_primary_source_ids': ['S2'], 'missing_fields': ['revenue', 'net_debt'],
                    'conflicts': [], 'notes': []}
        message, _ = pipeline._coverage_stop_text(coverage, {}, [], False)
        self.assertIn('revenue, net_debt', message)
        self.assertIn('IFRS', message)

    def test_the_financial_institution_article(self):
        sources = [{'id': 'S1', 'source_tier': 'regulatory_primary',
                    'data': {'metadata': {'sic': '6311', 'sicDescription': 'Life Insurance'}}}]
        self.assertIn('is an insurer', inputs.operating_company_gate(sources)['message'])


def quiet(function, *args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = function(*args)
    return code, out.getvalue(), err.getvalue()


class RerenderToolTests(unittest.TestCase):
    def test_damaged_saved_runs_stop_with_one_plain_line(self):
        cases = {'empty': b'', 'cut': b'{"current_memo": "x", "sour', 'list': b'[]', 'no sources': b'{"current_memo": "x"}',
                 'valuation text': b'{"current_memo": "x", "sources": [], "valuation": "x"}',
                 'sources object': b'{"current_memo": "x", "sources": {}}', 'no memo': b'{"sources": []}'}
        for name, raw in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                (Path(folder) / 'state.json').write_bytes(raw)
                code, _, err = quiet(rerender.main, [folder, str(Path(folder) / 'new.md')])
                self.assertEqual(code, 1)
                self.assertTrue(err.startswith('rerender stopped: '), err)
                self.assertNotIn('Traceback', err)
        with tempfile.TemporaryDirectory() as folder:
            code, _, err = quiet(rerender.main, [folder, str(Path(folder) / 'new.md')])
            self.assertIn('state.json does not exist', err)

    def test_an_output_over_a_saved_file_is_refused_and_the_run_survives(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / 'state.json'
            state.write_text('{"current_memo": "x", "sources": []}', encoding='utf-8')
            code, _, err = quiet(rerender.main, [folder, str(state)])
            self.assertEqual(code, 1)
            self.assertIn('would replace', err)
            self.assertEqual(json.loads(state.read_text(encoding='utf-8')), {'current_memo': 'x', 'sources': []})
            for out in (folder, str(Path(folder) / 'missing' / 'out.md')):
                self.assertEqual(quiet(rerender.main, [folder, out])[0], 1)

    def test_a_memo_without_a_valuation_is_written_with_a_warning(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'state.json').write_text(json.dumps(
                {'ticker': 'EXMP', 'current_memo': '## 1. Valuation conclusion\n\nText.', 'sources': [], 'gaps': []}),
                encoding='utf-8')
            code, _, err = quiet(rerender.main, [folder, str(Path(folder) / 'memo.md')])
            self.assertEqual(code, 0)
            self.assertIn('no valuation', err)
            self.assertIn('no forecast plan', err)


class ReplayToolTests(unittest.TestCase):
    def saved(self, directory, state):
        folder = Path(directory) / 'saved'
        folder.mkdir()
        (folder / 'sources_raw').mkdir()
        (folder / 'state.json').write_text(json.dumps(state), encoding='utf-8')
        return folder

    def main(self, *argv, key='sk-test'):
        with patch.object(sys, 'argv', ['replay.py', *argv]), patch.dict('os.environ', {'OPENROUTER_API_KEY': key}), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                replay.main()
        return str(caught.exception.code)

    def test_a_failed_start_leaves_nothing_and_a_retry_gives_the_same_reason(self):
        state = {'version': pipeline.VERSION, 'ticker': 'WMT', 'sources': [{'id': 'S1'}], 'stages': {}, 'ledger': []}
        with tempfile.TemporaryDirectory() as directory:
            saved, output = self.saved(directory, state), Path(directory) / 'out'
            for extra in ((), ('--resume',)):
                message = self.main(str(saved), str(output), '--from', 'review', *extra)
                self.assertIn('has no draft and plan to review', message)
                self.assertFalse((output / 'WMT' / 'full-replay').exists())

    def test_limits_and_damaged_state_are_refused_before_seeding(self):
        state = {'version': pipeline.VERSION, 'ticker': 'WMT', 'sources': [{'id': 'S1'}], 'stages': {}, 'ledger': []}
        with tempfile.TemporaryDirectory() as directory:
            saved, output = self.saved(directory, state), Path(directory) / 'out'
            self.assertIn('--budget nan is not allowed', self.main(str(saved), str(output), '--budget', 'nan'))
            self.assertIn('--budget -1 is not allowed', self.main(str(saved), str(output), '--budget', '-1'))
            self.assertIn('--max-revisions -3', self.main(str(saved), str(output), '--max-revisions', '-3'))
            self.assertFalse(output.exists())
            (saved / 'state.json').write_text('{"ticker": "WM', encoding='utf-8')
            self.assertIn('may be cut short', self.main(str(saved), str(output)))
            (saved / 'state.json').unlink()
            self.assertIn('does not exist', self.main(str(saved), str(output)))

    def test_an_existing_target_and_a_provider_refusal_are_plain(self):
        state = {'version': pipeline.VERSION, 'ticker': 'WMT', 'sources': [{'id': 'S1'}], 'stages': {}, 'ledger': []}
        with tempfile.TemporaryDirectory() as directory:
            saved, output = self.saved(directory, state), Path(directory) / 'out'
            (output / 'WMT' / 'full-replay').mkdir(parents=True)
            self.assertIn('already exists', self.main(str(saved), str(output)))
            self.assertIn('nothing to resume', self.main(str(saved), str(output), '--resume'))
            with patch.object(replay, '_replay', side_effect=core.HTTPFailure(401, 'openrouter.ai')):
                self.assertEqual(self.main(str(saved), str(output)),
                                 'Replay stopped: HTTP 401 from openrouter.ai; check API key validity')


if __name__ == '__main__':
    unittest.main()

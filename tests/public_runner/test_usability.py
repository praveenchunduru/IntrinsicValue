"""First-run and everyday use: preflight checks, plain progress, summaries, memo.html, the browser form."""
import contextlib
from datetime import datetime, timedelta, timezone
import http.client
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from investmentideas import __main__ as cli
from investmentideas import core, explain, memo_html, preflight, progress, runs, web

ROOT = Path(__file__).resolve().parents[2]
KEY = 'sk-or-v1-' + '0' * 64
EMAIL = 'fixture-contact@example.org'


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def run_cli(argv, environ=None, runner=None, key_check=None):
    """Run main() in-process; returns (exit code, stdout, stderr)."""
    full = types.ModuleType('investmentideas.pipeline')
    full.run = runner or Mock(return_value=Path('/nonexistent/run'))
    full.attach_diagnostics = lambda plan, result: result
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with patch.dict(sys.modules, {'investmentideas.pipeline': full}), \
            patch.dict(os.environ, environ or {}, clear=True), \
            patch.object(preflight, 'check_key', key_check or Mock(return_value={})), \
            patch.object(sys, 'argv', ['intrinsicvalue', *argv]), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            cli.main()
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue(), full.run


class TickerPreflight(unittest.TestCase):
    def test_a_listed_ticker_passes_and_a_class_ticker_is_normalized(self):
        self.assertEqual(preflight.check_ticker(' msft '), 'MSFT')
        self.assertEqual(preflight.check_ticker('brk.b'), 'BRK-B')
        self.assertEqual(core.symbol('BRK.B'), core.symbol('BRK-B'))

    def test_a_company_name_suggests_its_ticker(self):
        with self.assertRaises(preflight.PreflightError) as caught:
            preflight.check_ticker('Microsoft')
        self.assertIn('Did you mean MSFT', str(caught.exception))
        with self.assertRaises(preflight.PreflightError) as caught:
            preflight.check_ticker('Coca Cola')
        self.assertIn('KO', str(caught.exception))

    def test_an_unknown_or_odd_ticker_is_refused_plainly(self):
        for bad, words in (('ZZZZQ', 'not in the SEC company list'), ('ß', '1 to 15 letters'),
                           ('A^=', '1 to 15 letters'), ('', 'Enter a ticker')):
            with self.subTest(bad=bad), self.assertRaises(preflight.PreflightError) as caught:
                preflight.check_ticker(bad)
            self.assertIn(words, str(caught.exception))
        with self.assertRaises(ValueError):
            core.symbol('ß')  # would become SS, another company


class SettingsPreflight(unittest.TestCase):
    def test_missing_email_names_setup_and_the_flag(self):
        with self.assertRaises(preflight.PreflightError) as caught:
            preflight.check_contact(None, {})
        self.assertIn('--setup', str(caught.exception))
        self.assertIn('--sec-contact', str(caught.exception))
        preflight.check_contact(None, {'SEC_CONTACT_EMAIL': EMAIL})

    def test_browser_wording_names_the_field(self):
        with self.assertRaises(preflight.PreflightError) as caught:
            preflight.check_contact('', {}, browser=True)
        self.assertIn('Enter your email', str(caught.exception))
        self.assertNotIn('--', str(caught.exception))

    def test_key_format(self):
        with self.assertRaises(preflight.PreflightError) as caught:
            preflight.check_key_format('')
        self.assertIn('--dry-run', str(caught.exception))
        with self.assertRaises(preflight.PreflightError) as caught:
            preflight.check_key_format('sk-or-abc\ndef')
        self.assertIn('line breaks', str(caught.exception))


class KeyCheck(unittest.TestCase):
    def test_rejected_key_fails_with_the_plain_message_and_sends_the_key_only_to_openrouter(self):
        seen = []

        def opener(request, timeout):
            seen.append(request)
            raise HTTPError(request.full_url, 401, 'Unauthorized', {}, io.BytesIO(b'{"error":{"message":"User not found."}}'))
        with self.assertRaises(preflight.PreflightError) as caught:
            preflight.check_key(KEY, opener=opener)
        self.assertIn('rejected by OpenRouter', str(caught.exception))
        self.assertNotIn(KEY, str(caught.exception))
        self.assertEqual(seen[0].full_url, 'https://openrouter.ai/api/v1/key')
        self.assertEqual(seen[0].get_method(), 'GET')
        self.assertEqual(seen[0].get_header('Authorization'), 'Bearer ' + KEY)

    def test_accepted_key_reports_its_remaining_limit(self):
        body = json.dumps({'data': {'limit_remaining': 12.5, 'label': 'x'}}).encode()
        self.assertEqual(preflight.check_key(KEY, opener=lambda r, timeout: FakeResponse(body)), {'limit_remaining': 12.5})
        self.assertEqual(preflight.check_key(KEY, opener=lambda r, timeout: FakeResponse(b'{"data":{"limit_remaining":null}}')), {})

    def test_other_statuses_and_certificates(self):
        for code, words in ((402, 'no credit'), (429, 'limiting'), (500, 'did not answer')):
            def opener(request, timeout, code=code):
                raise HTTPError(request.full_url, code, 'x', {}, io.BytesIO(b''))
            with self.subTest(code=code), self.assertRaises(preflight.PreflightError) as caught:
                preflight.check_key(KEY, opener=opener)
            self.assertIn(words, str(caught.exception))
        import ssl
        from urllib.error import URLError

        def broken(request, timeout):
            raise URLError(ssl.SSLCertVerificationError('certificate verify failed: CERTIFICATE_VERIFY_FAILED'))
        with self.assertRaises(preflight.PreflightError) as caught:
            preflight.check_key(KEY, opener=broken)
        self.assertIn('certificates', str(caught.exception))


class CommandLine(unittest.TestCase):
    def test_no_key_and_no_dry_run_stops_before_any_work(self):
        code, out, err, runner = run_cli(['MSFT'], {'SEC_CONTACT_EMAIL': EMAIL})
        self.assertEqual(code, 1)
        self.assertIn('No OpenRouter key is set', err)
        self.assertIn('--setup', err)
        runner.assert_not_called()

    def test_no_email_stops_before_any_work(self):
        code, out, err, runner = run_cli(['MSFT'], {'OPENROUTER_API_KEY': KEY})
        self.assertEqual(code, 1)
        self.assertIn('No SEC contact email', err)
        runner.assert_not_called()

    def test_a_rejected_key_stops_before_collection(self):
        check = Mock(side_effect=preflight.PreflightError('That key was rejected by OpenRouter.'))
        code, out, err, runner = run_cli(['MSFT'], {'OPENROUTER_API_KEY': KEY, 'SEC_CONTACT_EMAIL': EMAIL}, key_check=check)
        self.assertEqual(code, 1)
        self.assertIn('rejected by OpenRouter', err)
        runner.assert_not_called()

    def test_dry_run_needs_no_key_and_reports_a_finished_check(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'MSFT' / 'full-x'
            folder.mkdir(parents=True)

            def runner(ticker, key, **kwargs):
                self.assertEqual(key, '')
                error = ValueError('Set OPENROUTER_API_KEY or enter your key in the local interface')
                error.folder = folder
                raise error
            code, out, err, _ = run_cli(['MSFT', '--dry-run', '--output', temp], {'SEC_CONTACT_EMAIL': EMAIL}, runner=runner)
        self.assertEqual(code, 0, err)
        self.assertIn('Dry run finished', out)
        self.assertIn('Nothing was charged', out)

    def test_ctrl_c_prints_one_line_and_no_traceback(self):
        code, out, err, _ = run_cli(['MSFT'], {'OPENROUTER_API_KEY': KEY, 'SEC_CONTACT_EMAIL': EMAIL},
                                    runner=Mock(side_effect=KeyboardInterrupt))
        self.assertEqual(code, 130)
        self.assertIn('Finished steps are saved; run the same command again to resume', err)
        self.assertNotIn('Traceback', err)

    def test_empty_ticker_is_an_error_not_a_server(self):
        with patch.object(web, 'serve') as serve:
            code, out, err, _ = run_cli([''], {})
        self.assertEqual(code, 2)
        self.assertIn('ticker is empty', err)
        serve.assert_not_called()

    def test_bad_port_and_version(self):
        code, out, err, _ = run_cli(['--port', '70000'], {})
        self.assertEqual(code, 2)
        self.assertIn('not a port number', err)
        self.assertNotIn('Traceback', err)
        code, out, err, _ = run_cli(['--version'], {})
        self.assertIn('IntrinsicValue', out)

    def test_busy_port_names_the_port_and_the_fix(self):
        error = OSError(48, 'Address already in use')
        error.errno = 48
        import errno as errno_module
        error.errno = errno_module.EADDRINUSE
        with patch.object(web, 'serve', side_effect=error):
            code, out, err, _ = run_cli(['--port', '8765', '--no-browser'], {})
        self.assertEqual(code, 1)
        self.assertIn('Port 8765 is busy', err)
        self.assertIn('--port 8766', err)

    def test_file_flags_refuse_special_files_and_name_flag_and_file(self):
        with tempfile.TemporaryDirectory() as temp:
            fifo = Path(temp) / 'pipe'
            if not hasattr(os, 'mkfifo'):
                self.skipTest('no FIFOs on this platform')
            os.mkfifo(fifo)
            for flag in ('--evidence', '--draft', '--review-notes', '--sources', '--snapshot'):
                with self.subTest(flag=flag):
                    code, out, err, runner = run_cli(['MSFT', flag, str(fifo)], {'OPENROUTER_API_KEY': KEY, 'SEC_CONTACT_EMAIL': EMAIL})
                    self.assertEqual(code, 1)
                    self.assertIn(flag, err)
                    self.assertIn('not a regular file', err)
                    runner.assert_not_called()
            code, out, err, _ = run_cli(['--revalue', str(fifo)], {})
            self.assertEqual(code, 1)
            self.assertIn('not a regular file', err)
            bom = Path(temp) / 'notes.json'
            bom.write_bytes(b'\xef\xbb\xbf{}')
            self.assertEqual(cli._read_json(bom, 1000, 'counterfactuals', '--counterfactuals'), {})

    def test_revalue_refuses_research_flags_and_quick_refuses_full_flags(self):
        code, out, err, _ = run_cli(['--revalue', 'a.json', '--budget', '5'], {})
        self.assertEqual(code, 2)
        self.assertIn('--budget', err)
        code, out, err, runner = run_cli(['MSFT', '--quick', '--max-revisions', '5'],
                                         {'OPENROUTER_API_KEY': KEY, 'SEC_CONTACT_EMAIL': EMAIL})
        self.assertEqual(code, 1)
        self.assertIn('--max-revisions', err)

    def test_revalue_rejects_a_file_that_is_not_assumptions(self):
        with tempfile.TemporaryDirectory() as temp:
            readme = Path(temp) / 'README.md'
            readme.write_text('# not json')
            code, out, err, _ = run_cli(['--revalue', str(readme)], {})
        self.assertEqual(code, 1)
        self.assertIn('is not an assumptions.json file from a run', err)

    def test_finished_run_prints_value_cost_memo_and_next_step(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'MSFT' / 'full-x'
            folder.mkdir(parents=True)
            (folder / 'memo.md').write_text('# MSFT\n\n| a | b |\n|---|---|\n| 1 | 2 |\n')
            (folder / 'state.json').write_text(json.dumps({'version': 'full-2', 'ticker': 'MSFT', 'complete': False,
                                                           'quality_status': 'unresolved_findings', 'ledger': []}))
            (folder / 'valuation.json').write_text(json.dumps({'currency': 'USD', 'market_price': 400.0, 'scenarios': {
                'bear': {'per_share_value': 200.0}, 'base': {'per_share_value': 300.0}, 'bull': {'per_share_value': 380.0}}}))
            (folder / 'cost-summary.json').write_text(json.dumps({'totals': {'total_charged_or_reserved_usd': 4.5,
                                                                             'known_usage_cost_usd': 4.2, 'retained_estimates_usd': 0.3}}))
            code, out, err, _ = run_cli(['MSFT', '--output', temp], {'OPENROUTER_API_KEY': KEY, 'SEC_CONTACT_EMAIL': EMAIL},
                                        runner=Mock(return_value=folder))
            self.assertTrue((folder / 'memo.html').is_file())
        self.assertEqual(code, 0, err)
        self.assertIn('review findings open', out)
        self.assertIn('Review status', out)
        self.assertIn('Base value $300.00 a share against a price of $400.00, value / price 0.75', out)
        self.assertIn('$4.20 reported by OpenRouter, plus $0.30 held', out)
        self.assertIn('memo.html', out)
        self.assertIn('Next:', out)
        self.assertNotIn(KEY, out + err)

    def test_a_stop_rewrites_a_stale_memo(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'MSFT' / 'full-x'
            folder.mkdir(parents=True)
            memo = folder / 'memo.md'
            memo.write_text('# MSFT: financial input coverage incomplete\n')
            old = time.time() - 3600
            os.utime(memo, (old, old))
            (folder / 'state.json').write_text(json.dumps({'version': 'full-2', 'ticker': 'MSFT', 'ledger': []}))
            error = RuntimeError('HTTP 401 from openrouter.ai; check API key validity')
            error.folder = folder
            code, out, err, _ = run_cli(['MSFT', '--output', temp], {'OPENROUTER_API_KEY': KEY, 'SEC_CONTACT_EMAIL': EMAIL},
                                        runner=Mock(side_effect=error))
            text = memo.read_text()
        self.assertEqual(code, 1)
        self.assertIn('run stopped', text)
        self.assertIn('OpenRouter rejected the key', text)
        self.assertNotIn('coverage incomplete', text)
        self.assertIn('OpenRouter rejected the key', err)

    def test_alias_module_runs_the_same_program(self):
        env = {'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(ROOT), 'HOME': tempfile.gettempdir()}
        for module in ('intrinsicvalue', 'investmentideas'):
            done = subprocess.run([sys.executable, '-m', module, '--help'], env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn(f'usage: python3 -m {module}', done.stdout)
            self.assertIn('--dry-run', done.stdout)
            self.assertNotIn('dev', done.stdout.split('--profile', 1)[1].split('\n')[0])


class Listing(unittest.TestCase):
    def test_list_shows_runs_newest_first(self):
        with tempfile.TemporaryDirectory() as temp:
            for ticker, status, base in (('KO', 'reviewed_draft', 41.45), ('WMT', 'needs_review', 35.7)):
                folder = Path(temp) / ticker / 'full-abc'
                folder.mkdir(parents=True)
                (folder / 'state.json').write_text(json.dumps({'version': 'full-2', 'ticker': ticker, 'quality_status': status,
                                                               'complete': status == 'reviewed_draft', 'current_memo': 'x',
                                                               'review_history': [{'iteration': 0}], 'ledger': [
                                                                   {'charged_or_reserved_usd': 1.25, 'status': 'complete', 'usage': {'cost': 1.25}}]}))
                (folder / 'valuation.json').write_text(json.dumps({'market_price': 100, 'scenarios': {'base': {'per_share_value': base}}}))
                (folder / 'memo.md').write_text('# memo')
                time.sleep(0.02)
            code, out, err, _ = run_cli(['--list', '--output', temp], {})
        self.assertEqual(code, 0)
        self.assertLess(out.index('WMT'), out.index('KO'))
        self.assertIn('$41.45', out)
        self.assertIn('0.36', out)
        self.assertIn('$1.25', out)
        self.assertIn('Finished, review findings open', out)

    def test_a_dry_run_folder_is_listed_as_evidence_only(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'MSFT' / 'full-abc'
            folder.mkdir(parents=True)
            (folder / 'state.json').write_text(json.dumps({'version': 'full-2', 'ticker': 'MSFT', 'quality_status': 'needs_review',
                                                           'complete': False, 'ledger': [], 'stages': {}}))
            self.assertEqual(runs.summarize(folder)['label'], 'Evidence collected, no memo yet')

    def test_empty_list_says_how_to_start(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIn('Start one with', runs.format_list(runs.list_runs(temp), temp))


class RevalueTable(unittest.TestCase):
    def test_table_shows_cases_and_change(self):
        results = {'currency': 'USD', 'market_price': 131.94, 'scenarios': {
            'bear': {'per_share_value': 21.37}, 'base': {'per_share_value': 37.77}, 'bull': {'per_share_value': 43.60}}}
        lines = '\n'.join(cli.revalue_table(results, {'base': 35.72, 'bear': 21.37, 'bull': 43.60}, Path('/tmp/out')))
        self.assertIn('$37.77', lines)
        self.assertIn('$35.72', lines)
        self.assertIn('+2.05', lines)
        self.assertIn('0.29', lines)
        self.assertIn('nothing charged', lines)


class RevalueWarnings(unittest.TestCase):
    def test_input_and_value_warnings_are_printed_plainly(self):
        lines = cli.revalue_warnings({'input_warnings': ['scenarios.base.terminal_growht is not an input name'],
                                      'value_warnings': [{'message': 'Base value is negative'}]})
        self.assertIn('scenarios.base.terminal_growht is not an input name', '\n'.join(lines))
        self.assertIn('- Base value is negative', lines)
        self.assertEqual(cli.revalue_warnings({}), [])


class QuickState(unittest.TestCase):
    def test_a_damaged_state_file_is_named_and_kept(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(core, 'collect') as collect:
            with patch.object(core, 'Router', side_effect=ValueError('stop')), \
                    patch.object(core, 'collect', return_value=([{'id': 'S1', 'kind': 'sec_facts', 'data': {}}], [])):
                with self.assertRaises(ValueError):
                    core.run('AAPL', 'k', directory=temp)
            state = next(Path(temp).glob('AAPL/*/state.json'))
            for text, words in (('', 'is empty'), ('{"ledger": [', 'cut short')):
                state.write_text(text)
                with self.subTest(words=words), self.assertRaises(ValueError) as caught:
                    core.run('AAPL', 'k', directory=temp)
                self.assertIn(words, str(caught.exception))
                self.assertIn(str(state), str(caught.exception))
                self.assertIn('--output', str(caught.exception))
                self.assertEqual(state.read_text(), text)
            collect.assert_not_called()

    def test_missing_evidence_says_why(self):
        self.assertIn('no SEC contact email', core._missing_evidence([], ['SEC contact email required: use --sec-contact']))
        self.assertIn('internet connection', core._missing_evidence([], [
            'Market snapshot unavailable. No verified current quote.',
            'SEC facts unavailable or ticker not mapped. Supply filings with --evidence for deeper research.']))


class Progress(unittest.TestCase):
    def test_stage_names_become_plain_steps(self):
        seen = []
        reporter = progress.Reporter('/nonexistent', 'MSFT', out=seen.append)
        reporter('Collecting financial facts, primary filings and issuer releases')
        reporter('web_business: anthropic/claude-haiku-5.5')
        reporter('valuation_plan_s2: anthropic/claude-opus-5.5')
        reporter('review_1: openai/gpt-6.2-sol')
        reporter('unresolved_findings: /x/memo.md')
        self.assertTrue(seen[0].startswith('Step 1 of 9: collecting SEC filings'))
        self.assertIn('Step 2 of 9: researching the business on the web (Claude Haiku 5.5)', seen[1])
        self.assertIn('writing the forecast (Claude Opus 5.5)', seen[2])
        self.assertIn('independent review by a second model, round 2 (GPT 6.2 Sol)', seen[3])
        self.assertEqual(len(seen), 4)
        for line in seen:
            self.assertNotIn('_', line.split('[')[0])

    def test_stop_raises_only_at_a_step_start(self):
        reporter = progress.Reporter('/nonexistent', 'MSFT', out=lambda text: None)
        reporter.stop()
        reporter('Initial budget preflight: $7.89 known-context reservation floor; $20.00 total allowance.')
        with self.assertRaises(progress.RunStopped):
            reporter('full_draft: anthropic/claude-opus-5.5')
        self.assertFalse(issubclass(progress.RunStopped, Exception))


class MemoHtml(unittest.TestCase):
    HOSTILE = ('# Title <script>alert(1)</script>\n\nText with <img src=x onerror=alert(1)> and '
               '[click](javascript:alert(1)) and [ok](https://example.com/?a=1&b="x") [S3 unverified] '
               '**bold** `<b>`\n\n| h<i> | v |\n|---|---:|\n| <svg onload=alert(1)> | 1 |\n\n- a\n  - <iframe>\n\n'
               '```\n</code></pre><script>x</script>\n```\n0\n')

    def test_hostile_input_is_escaped(self):
        page = memo_html.convert(self.HOSTILE)
        body = page.split('<main>', 1)[1]
        for tag in ('<script', '<img', '<svg', '<iframe', 'href="javascript', '</code></pre><script>'):
            self.assertNotIn(tag, body)
        self.assertIn('&lt;script&gt;', body)
        self.assertIn('<a href="https://example.com/?a=1&amp;b=&quot;x&quot;">ok</a>', body)
        self.assertIn('[click](javascript:alert(1))', body)
        self.assertIn("default-src 'none'", page)

    def test_memo_structure_survives(self):
        text = ('# A\n\n## Summary\n\nPrice [S1].\n\n| Case | Value |\n|---|---:|\n| Base | 35.7 |\n\n'
                '**Bold.** text\n\n1. one\n2. two\n\n- x\n- y\n\nSee https://data.sec.gov/api/xbrl/companyfacts/CIK1.json?a=b.\n')
        body = memo_html.body(text)
        self.assertIn('<h2 id="summary">Summary</h2>', body)
        self.assertIn('<th class="r">Value</th>', body)
        self.assertIn('<td class="r">35.7</td>', body)
        self.assertIn('<strong>Bold.</strong>', body)
        self.assertIn('<ol><li>', body)
        self.assertIn('<span class="cite">[S1]</span>', body)
        self.assertIn('>data.sec.gov/api/xbrl/companyfacts/CIK1.json?…</a>.', body)

    def test_write_beside(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(memo_html.write_beside(temp))
            Path(temp, 'memo.md').write_text('# X\n')
            self.assertTrue(memo_html.write_beside(temp).name == 'memo.html')


class RouterRefusals(unittest.TestCase):
    def test_a_refused_call_releases_its_reservation(self):
        catalog = {'data': [{'id': 'm/x', 'pricing': {'prompt': '0.000001', 'completion': '0.000002'}}]}
        for status in (400, 401, 402, 403, 500):
            ledger = []

            def fake(url, **kwargs):
                if url.endswith('/models'):
                    return catalog
                raise core.HTTPFailure(status, 'openrouter.ai')
            with self.subTest(status=status), patch.object(core, 'request_json', side_effect=fake):
                router = core.Router('k', 5, ledger)
                with self.assertRaises(core.HTTPFailure):
                    router.complete('review_0', 'm/x', 'prompt')
                charged = ledger[0]['charged_or_reserved_usd']
                self.assertEqual(charged == 0, status != 500)


class QuickCollection(unittest.TestCase):
    def test_an_empty_collection_is_not_cached(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(core, 'collect', return_value=([], ['SEC contact email required'])) as collect:
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, 'No evidence could be retrieved'):
                    core.run('AAPL', 'k', directory=temp)
            self.assertEqual(collect.call_count, 2)

    def test_a_collection_made_without_a_contact_is_redone_once_one_is_set(self):
        snapshot = [{'id': 'S1', 'kind': 'market_snapshot', 'data': {}}, {'id': 'S2', 'kind': 'financial_timeseries', 'data': {}}]
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True), \
                patch.object(core, 'collect', return_value=(snapshot, [])) as collect, \
                patch.object(core, 'Router', side_effect=ValueError('stop here')):
            with self.assertRaisesRegex(ValueError, 'stop here'):
                core.run('AAPL', 'k', directory=temp)
            os.environ['SEC_CONTACT_EMAIL'] = EMAIL
            with self.assertRaisesRegex(ValueError, 'stop here'):
                core.run('AAPL', 'k', directory=temp)
            self.assertEqual(collect.call_count, 2)


class ResumeAcrossMidnight(unittest.TestCase):
    def test_an_unfinished_run_from_yesterday_is_resumed(self):
        from investmentideas import pipeline
        moment = datetime(2026, 11, 28, 0, 5, tzinfo=timezone.utc)
        now = moment

        class Clock(datetime):
            current = moment - timedelta(minutes=20)

            @classmethod
            def now(cls, tz=None):
                return cls.current
        seen, messages = [], []
        with tempfile.TemporaryDirectory() as temp, patch.object(pipeline, 'datetime', Clock), \
                patch.object(pipeline, '_run', side_effect=lambda folder, *a, **k: seen.append(folder) or folder):
            pipeline.run('AAPL', 'k', directory=temp)
            first = seen[-1]
            (first / 'state.json').write_text(json.dumps({'complete': False}))
            marker = json.loads((first / 'run-identity.json').read_text())
            marker['started_at'] = (now - timedelta(minutes=20)).isoformat()
            (first / 'run-identity.json').write_text(json.dumps(marker))
            Clock.current = now
            with patch.object(runs.time, 'time', return_value=now.timestamp()):
                pipeline.run('AAPL', 'k', directory=temp, progress=messages.append)
            self.assertEqual(seen[-1], first)
            self.assertTrue(any('Resuming' in m for m in messages))
            # A finished run from yesterday is not resumed.
            (first / 'state.json').write_text(json.dumps({'complete': True}))
            with patch.object(runs.time, 'time', return_value=now.timestamp()):
                pipeline.run('AAPL', 'k', directory=temp)
            self.assertNotEqual(seen[-1], first)


class Wording(unittest.TestCase):
    def test_messages_are_reworded_and_secrets_removed(self):
        self.assertIn('OpenRouter rejected the key', explain.plain('HTTP 401 from openrouter.ai; check API key validity'))
        text = explain.plain('Initial budget preflight stopped before paid research: $2.00 allowance is below the $7.89 '
                             'known-context reservation floor. Rerun the same settings with --budget 8 (a planning allowance)',
                             browser=True)
        self.assertIn('spending allowance to $8', text)
        self.assertNotIn('--budget', text)
        self.assertNotIn('--evidence', explain.plain('Retry or supply --evidence.', browser=True))
        self.assertEqual(explain.redact('boom ' + KEY + ' and ' + EMAIL, KEY, EMAIL), 'boom [hidden] and [hidden]')


@contextlib.contextmanager
def browser(directory, **kwargs):
    kwargs.setdefault('key_check', lambda key: {})
    server = web.create_server(0, directory, **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def request(path, method='GET', data=None, headers=None, token=True):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
        merged = {'Content-Type': 'application/json', **({'X-Research-Token': server.token} if token else {}), **(headers or {})}
        body = json.dumps(data) if isinstance(data, (dict, list)) else data
        connection.request(method, path, body=body, headers=merged)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result
    try:
        yield request, server, port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def finished(request):
    for _ in range(300):
        code, body = request('/status')
        data = json.loads(body)
        if data.get('done'):
            return data
        time.sleep(0.01)
    raise AssertionError('run never finished')


class BrowserForm(unittest.TestCase):
    GOOD = {'key': KEY, 'sec_contact': EMAIL, 'ticker': 'AAPL', 'mode': 'quick', 'budget': 2}

    def test_page_holds_no_token_and_says_intrinsicvalue(self):
        with tempfile.TemporaryDirectory() as temp, browser(temp) as (request, server, _):
            code, page = request('/', token=False)
            self.assertEqual(code, 200)
            self.assertNotIn(server.token.encode(), page)
            self.assertIn(b'<title>IntrinsicValue</title>', page)
            self.assertIn(b'max="20"', page)
            self.assertIn(b'More options', page)
            self.assertEqual(request('/status', token=False)[0], 403)

    def test_specific_errors(self):
        cases = ((dict(self.GOOD, budget=30), 'between $1 and $20'), (dict(self.GOOD, budget=True), 'between $1 and $20'),
                 (dict(self.GOOD, budget='5'), 'between $1 and $20'), (dict(self.GOOD, ticker='Microsoft'), 'Did you mean MSFT'),
                 (dict(self.GOOD, sec_contact='not-an-email'), 'does not look right'),
                 (dict(self.GOOD, key='sk-or-a b'), 'spaces or line breaks'), (dict(self.GOOD, plan_samples=2.0), 'whole number'))
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True), browser(temp) as (request, _, _p):
            for data, words in cases:
                with self.subTest(words=words):
                    code, body = request('/run', 'POST', data)
                    self.assertEqual(code, 400)
                    self.assertIn(words, json.loads(body)['error'])
            code, body = request('/run', 'POST', dict(self.GOOD, sec_contact=''))
            self.assertIn('Enter your email', json.loads(body)['error'])
            code, body = request('/run', 'POST', dict(self.GOOD, key=''))
            self.assertIn('Enter your OpenRouter key', json.loads(body)['error'])

    def test_rejected_key_is_refused_before_starting(self):
        def reject(key):
            raise preflight.PreflightError('That key was rejected by OpenRouter.')
        with tempfile.TemporaryDirectory() as temp, patch.object(web, 'run') as quick, browser(temp, key_check=reject) as (request, _, _p):
            code, body = request('/run', 'POST', self.GOOD)
            self.assertEqual(code, 400)
            self.assertIn('rejected by OpenRouter', json.loads(body)['error'])
            quick.assert_not_called()

    def test_hostile_requests_get_answers(self):
        with tempfile.TemporaryDirectory() as temp, browser(temp) as (request, _, port):
            self.assertEqual(request('/status', headers={'X-Research-Token': 'é'}, token=False)[0], 403)
            nested = '{"x":' + '[' * 70000
            self.assertEqual(request('/run', 'POST', nested)[0], 400)
            import socket
            with socket.create_connection(('127.0.0.1', port), timeout=30) as sock:
                sock.sendall(b'POST /run HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Length: 1000\r\n\r\n{}' % port)
                started = time.monotonic()
                sock.settimeout(30)
                try:
                    sock.recv(100)
                except OSError:
                    pass
                self.assertLess(time.monotonic() - started, web.REQUEST_TIMEOUT_SECONDS + 10)

    def test_a_runner_that_raises_anything_still_finishes_and_finds_its_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            def runner(ticker, key, **kwargs):
                folder = Path(kwargs['directory']) / ticker / 'abc'
                folder.mkdir(parents=True)
                (folder / 'state.json').write_text(json.dumps({'ledger': [], 'stages': {}}))
                raise SystemExit(3)
            with patch.object(web, 'run', side_effect=runner), browser(temp) as (request, _, _p):
                self.assertEqual(request('/run', 'POST', self.GOOD)[0], 202)
                data = finished(request)
                self.assertTrue(data['message'].startswith('Stopped'))
                self.assertIn('folder', data)
                self.assertEqual(request('/run', 'POST', self.GOOD)[0], 202)
                finished(request)

    def test_error_message_hides_the_key_and_names_the_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            def runner(ticker, key, **kwargs):
                folder = Path(kwargs['directory']) / ticker / 'abc'
                folder.mkdir(parents=True)
                (folder / 'state.json').write_text(json.dumps({'ledger': [], 'stages': {}}))
                raise RuntimeError('provider echoed ' + key)
            with patch.object(web, 'run', side_effect=runner), browser(temp) as (request, _, _p):
                request('/run', 'POST', self.GOOD)
                data = finished(request)
                self.assertNotIn(KEY, json.dumps(data))
                self.assertIn(str(Path(temp).resolve()), data['folder'])

    def test_second_run_reuses_the_held_key_and_email(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            folder = Path(temp) / 'AAPL' / 'q'
            folder.mkdir(parents=True)
            (folder / 'memo.md').write_text('# Quick memo\n\n**bold** <script>x</script>')
            with patch.object(web, 'run', return_value=folder) as quick, browser(temp) as (request, _, _p):
                self.assertEqual(request('/run', 'POST', self.GOOD)[0], 202)
                data = finished(request)
                self.assertIn('memo_html', data)
                self.assertNotIn('<script>x', data['memo_html'])
                self.assertIn('memo.html', data['downloads'])
                self.assertEqual(request('/download/memo.html')[0], 200)
                code, page = request('/', token=False)
                self.assertIn(b'Saved for this session', page)
                self.assertNotIn(KEY.encode(), page)
                self.assertEqual(request('/run', 'POST', dict(self.GOOD, key='', sec_contact=''))[0], 202)
                finished(request)
                self.assertEqual(quick.call_args.args[1], KEY)

    def test_stop_button_ends_the_run_between_steps(self):
        with tempfile.TemporaryDirectory() as temp:
            gate = threading.Event()

            def runner(ticker, key, progress, **kwargs):
                progress('Retrieving keyless market and SEC evidence')
                gate.wait(5)
                progress('extract: deepseek/deepseek-v4.1-flash-1017')
                raise AssertionError('the stop did not take effect')
            with patch.object(web, 'run', side_effect=runner), browser(temp) as (request, _, _p):
                self.assertEqual(request('/cancel', 'POST', {})[0], 409)
                request('/run', 'POST', self.GOOD)
                time.sleep(0.05)
                self.assertEqual(request('/cancel', 'POST', {})[0], 202)
                gate.set()
                data = finished(request)
                self.assertTrue(data['message'].startswith('Stopped. Finished steps are kept'))

    def test_cancel_needs_the_token(self):
        with tempfile.TemporaryDirectory() as temp, browser(temp) as (request, _, port):
            self.assertEqual(request('/cancel', 'POST', {}, token=False)[0], 403)
            self.assertEqual(request('/cancel', 'POST', {}, headers={'Origin': 'https://evil.example'})[0], 403)

    def test_no_browser_opens_in_tests(self):
        with patch('webbrowser.open') as opened, patch.object(web, 'create_server') as create:
            create.return_value.serve_forever.side_effect = KeyboardInterrupt
            create.return_value.server_address = ('127.0.0.1', 1)
            create.return_value.token = 't'
            with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
                web.serve(0, temp)
        opened.assert_not_called()  # stdout is not a terminal here


if __name__ == '__main__':
    unittest.main()

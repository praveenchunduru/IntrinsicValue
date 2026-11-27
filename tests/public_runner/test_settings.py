"""The local `.env` settings file: what is read, what is ignored and what is refused."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from investmentideas import settings

KEY = 'sk-or-test-not-a-real-key'
EMAIL = 'analyst@example.org'
ROOT = Path(__file__).resolve().parents[2]


class ReadEnvFile(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / '.env'

    def read(self, text):
        self.file.write_bytes(text.encode('utf-8'))  # bytes, so line ends are kept exactly as written
        return settings.read_env_file(self.file)

    def test_no_file_means_no_settings(self):
        self.assertEqual(settings.read_env_file(self.file), {})

    def test_a_folder_named_env_is_treated_as_no_file(self):
        self.file.mkdir()
        self.assertEqual(settings.read_env_file(self.file), {})

    def test_plain_lines(self):
        self.assertEqual(self.read(f'OPENROUTER_API_KEY={KEY}\nSEC_CONTACT_EMAIL={EMAIL}\n'),
                         {'OPENROUTER_API_KEY': KEY, 'SEC_CONTACT_EMAIL': EMAIL})

    def test_export_prefix_quotes_spaces_and_windows_line_ends(self):
        text = f'export OPENROUTER_API_KEY="{KEY}"\r\n  SEC_CONTACT_EMAIL = \'{EMAIL}\'  \r\n'
        self.assertEqual(self.read(text), {'OPENROUTER_API_KEY': KEY, 'SEC_CONTACT_EMAIL': EMAIL})

    def test_a_byte_order_mark_from_a_windows_editor_is_ignored(self):
        self.file.write_bytes(b'\xef\xbb\xbf' + f'OPENROUTER_API_KEY={KEY}\n'.encode())
        self.assertEqual(settings.read_env_file(self.file), {'OPENROUTER_API_KEY': KEY})

    def test_comments_blank_lines_and_trailing_comments(self):
        text = f'# my settings\n\nOPENROUTER_API_KEY={KEY}  # paid key\n# SEC_CONTACT_EMAIL={EMAIL}\n'
        self.assertEqual(self.read(text), {'OPENROUTER_API_KEY': KEY})

    def test_a_hash_inside_quotes_is_part_of_the_value(self):
        self.assertEqual(self.read('OPENROUTER_API_KEY="abc #def"\n'), {'OPENROUTER_API_KEY': 'abc #def'})

    def test_other_names_are_never_read(self):
        text = f'PATH=/tmp/evil\nPYTHONPATH=/tmp/evil\nHTTPS_PROXY=http://evil.example\nOPENROUTER_API_KEY={KEY}\n'
        self.assertEqual(self.read(text), {'OPENROUTER_API_KEY': KEY})

    def test_the_shipped_example_with_empty_values_sets_nothing(self):
        self.assertEqual(settings.read_env_file(ROOT / '.env.example'), {})

    def test_the_last_of_two_lines_wins(self):
        self.assertEqual(self.read('OPENROUTER_API_KEY=old\nOPENROUTER_API_KEY=new\n'), {'OPENROUTER_API_KEY': 'new'})

    def test_lines_without_an_equals_sign_are_skipped(self):
        self.assertEqual(self.read(f'OPENROUTER_API_KEY\nnonsense\nSEC_CONTACT_EMAIL={EMAIL}\n'),
                         {'SEC_CONTACT_EMAIL': EMAIL})

    def test_a_control_character_is_refused_without_showing_the_value(self):
        with self.assertRaises(ValueError) as caught:
            self.read(f'SEC_CONTACT_EMAIL={EMAIL}\nOPENROUTER_API_KEY=abc\x00secret\n')
        message = str(caught.exception)
        self.assertIn('line 2', message)
        self.assertIn('OPENROUTER_API_KEY', message)
        self.assertNotIn('secret', message)

    def test_an_oversized_file_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            self.read('# padding\n' * 4000)
        self.assertIn('larger than', str(caught.exception))

    def test_a_binary_file_is_refused(self):
        self.file.write_bytes(b'\xff\xfe\x00\x01 not text')
        with self.assertRaises(ValueError) as caught:
            settings.read_env_file(self.file)
        self.assertIn('not UTF-8', str(caught.exception))


class ApplyEnvFile(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / '.env'
        self.file.write_text(f'OPENROUTER_API_KEY={KEY}\nSEC_CONTACT_EMAIL={EMAIL}\n', encoding='utf-8')

    def test_fills_what_the_environment_lacks(self):
        environ = {}
        self.assertEqual(settings.apply_env_file(environ, self.file), ['OPENROUTER_API_KEY', 'SEC_CONTACT_EMAIL'])
        self.assertEqual(environ, {'OPENROUTER_API_KEY': KEY, 'SEC_CONTACT_EMAIL': EMAIL})

    def test_an_exported_value_wins_over_the_file(self):
        environ = {'OPENROUTER_API_KEY': 'from-the-shell'}
        self.assertEqual(settings.apply_env_file(environ, self.file), ['SEC_CONTACT_EMAIL'])
        self.assertEqual(environ['OPENROUTER_API_KEY'], 'from-the-shell')

    def test_a_blank_exported_value_does_not_block_the_file(self):
        environ = {'OPENROUTER_API_KEY': '  '}
        settings.apply_env_file(environ, self.file)
        self.assertEqual(environ['OPENROUTER_API_KEY'], KEY)

    def test_the_description_names_settings_and_never_values(self):
        line = settings.describe(['OPENROUTER_API_KEY', 'SEC_CONTACT_EMAIL'])
        self.assertEqual(line, 'Settings: OPENROUTER_API_KEY and SEC_CONTACT_EMAIL read from .env')
        self.assertEqual(settings.describe([]), '')


class SaveAndSetup(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / '.env'

    def setup(self, key, email, **kwargs):
        prompts = []

        def secret(prompt):
            prompts.append(prompt)
            return key

        def plain(prompt):
            prompts.append(prompt)
            return email

        message = settings.run_setup(self.file, ask=plain, ask_secret=secret, interactive=True, **kwargs)
        return message, prompts

    def test_setup_saves_both_and_a_run_reads_them_back(self):
        message, prompts = self.setup(KEY, EMAIL)
        self.assertEqual(settings.read_env_file(self.file), {'OPENROUTER_API_KEY': KEY, 'SEC_CONTACT_EMAIL': EMAIL})
        self.assertIn('Saved OPENROUTER_API_KEY and SEC_CONTACT_EMAIL', message)
        self.assertIn('python3 -m investmentideas MSFT', message)
        for text in [message, *prompts]:
            self.assertNotIn(KEY, text)
            self.assertNotIn(EMAIL, text)

    @unittest.skipIf(os.name == 'nt', 'owner-only permission bits are a POSIX feature')
    def test_the_saved_file_is_readable_by_its_owner_only(self):
        self.file.write_text('# old\n', encoding='utf-8')
        os.chmod(self.file, 0o644)
        self.setup(KEY, EMAIL)
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.file.with_name('.env.tmp').exists())

    def test_enter_keeps_what_is_saved_and_other_lines_survive(self):
        self.file.write_text(f'# mine\nOTHER_TOOL=1\nexport OPENROUTER_API_KEY=old-key\nSEC_CONTACT_EMAIL={EMAIL}\n',
                             encoding='utf-8')
        message, prompts = self.setup(KEY, '')
        self.assertIn('press Enter to keep', prompts[0])
        self.assertIn('press Enter to keep', prompts[1])
        text = self.file.read_text(encoding='utf-8')
        self.assertIn('# mine\nOTHER_TOOL=1\n', text)
        self.assertNotIn('old-key', text)
        self.assertEqual(text.count('OPENROUTER_API_KEY'), 1)
        self.assertEqual(settings.read_env_file(self.file), {'OPENROUTER_API_KEY': KEY, 'SEC_CONTACT_EMAIL': EMAIL})
        self.assertNotIn('Still missing', message)

    def test_one_value_alone_is_saved_and_the_other_is_named_as_missing(self):
        message, _ = self.setup(KEY, '')
        self.assertEqual(settings.read_env_file(self.file), {'OPENROUTER_API_KEY': KEY})
        self.assertIn('Still missing: SEC_CONTACT_EMAIL', message)

    def test_nothing_entered_saves_nothing(self):
        with self.assertRaises(ValueError):
            self.setup('', '')
        self.assertFalse(self.file.exists())

    def test_a_bad_email_is_refused_and_nothing_is_written(self):
        with self.assertRaises(ValueError):
            self.setup(KEY, 'not an email')
        self.assertFalse(self.file.exists())

    def test_a_key_with_a_space_or_quote_is_refused_without_echoing_it(self):
        for bad in ('sk-or two', 'sk-or"x', "sk-or'x", 'sk-or#x'):
            with self.assertRaises(ValueError) as caught:
                self.setup(bad, EMAIL)
            self.assertNotIn(bad, str(caught.exception))
            self.assertFalse(self.file.exists())

    def test_setup_without_a_terminal_explains_the_manual_route(self):
        with self.assertRaises(ValueError) as caught:
            settings.run_setup(self.file, interactive=False)
        self.assertIn('.env.example', str(caught.exception))

    def test_only_the_two_settings_can_be_saved(self):
        with self.assertRaises(ValueError):
            settings.save_env_file({'PATH': '/tmp/evil'}, self.file)

    def test_a_folder_in_the_way_is_refused(self):
        self.file.mkdir()
        with self.assertRaises(ValueError):
            settings.save_env_file({'OPENROUTER_API_KEY': KEY}, self.file)


class CommandLineUsesTheFile(unittest.TestCase):
    """End to end, offline: the notes a first run prints depend on the `.env` beside it."""

    def run_cli(self, folder, *args):
        env = {'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(ROOT), 'HOME': folder}
        return subprocess.run([sys.executable, '-m', 'investmentideas', *args], cwd=folder, env=env,
                              capture_output=True, text=True, timeout=120)

    def test_a_snapshot_replay_with_saved_settings_prints_no_setup_note(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, '.env').write_text(f'OPENROUTER_API_KEY={KEY}\nSEC_CONTACT_EMAIL={EMAIL}\n', encoding='utf-8')
            Path(folder, 'snapshot.json').write_text('not json', encoding='utf-8')
            done = self.run_cli(folder, 'AAPL', '--snapshot', 'snapshot.json')
        self.assertNotEqual(done.returncode, 0)  # the snapshot is deliberately unusable: nothing leaves the machine
        self.assertNotIn('Setup:', done.stderr)
        self.assertNotIn(KEY, done.stdout + done.stderr)
        self.assertNotIn(EMAIL, done.stdout + done.stderr)

    def test_the_same_replay_without_the_file_says_the_key_is_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'snapshot.json').write_text('not json', encoding='utf-8')
            done = self.run_cli(folder, 'AAPL', '--snapshot', 'snapshot.json')
        self.assertIn('Setup: OPENROUTER_API_KEY is not set', done.stderr)

    def test_an_unusable_settings_file_stops_with_a_plain_message(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, '.env').write_bytes(b'\xff\xfe\x00 not text')
            done = self.run_cli(folder, 'AAPL', '--snapshot', 'snapshot.json')
        self.assertEqual(done.returncode, 1)
        self.assertIn('.env is not UTF-8 text', done.stderr)
        self.assertNotIn('Traceback', done.stderr)


    def test_setup_with_no_terminal_stops_cleanly(self):
        with tempfile.TemporaryDirectory() as folder:
            done = subprocess.run([sys.executable, '-m', 'investmentideas', '--setup'], cwd=folder, stdin=subprocess.DEVNULL,
                                  env={'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(ROOT), 'HOME': folder},
                                  capture_output=True, text=True, timeout=120)
            self.assertFalse(Path(folder, '.env').exists())
        self.assertEqual(done.returncode, 1)
        self.assertIn('Setup stopped: --setup needs a terminal', done.stderr)
        self.assertNotIn('Traceback', done.stderr)

if __name__ == '__main__':
    unittest.main()

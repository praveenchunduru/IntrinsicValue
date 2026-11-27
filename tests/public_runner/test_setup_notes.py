"""First-run hints the command line prints when a setting is missing."""
import unittest

from investmentideas.__main__ import setup_notes

KEY = 'sk-or-test-not-a-real-key'
EMAIL = 'analyst@example.org'


class SetupNotes(unittest.TestCase):
    def test_nothing_to_say_when_both_are_set(self):
        self.assertEqual(setup_notes(environ={'OPENROUTER_API_KEY': KEY, 'SEC_CONTACT_EMAIL': EMAIL}), [])

    def test_a_missing_key_names_the_variable_and_the_browser_form(self):
        notes = setup_notes(environ={'SEC_CONTACT_EMAIL': EMAIL})
        self.assertEqual(len(notes), 1)
        self.assertIn('OPENROUTER_API_KEY is not set', notes[0])
        self.assertIn('browser form', notes[0])

    def test_a_blank_key_counts_as_missing(self):
        notes = setup_notes(environ={'OPENROUTER_API_KEY': '   ', 'SEC_CONTACT_EMAIL': EMAIL})
        self.assertEqual(len(notes), 1)
        self.assertIn('OPENROUTER_API_KEY', notes[0])

    def test_a_missing_contact_names_both_ways_to_set_it(self):
        notes = setup_notes(environ={'OPENROUTER_API_KEY': KEY})
        self.assertEqual(len(notes), 1)
        self.assertIn('SEC_CONTACT_EMAIL', notes[0])
        self.assertIn('--sec-contact', notes[0])

    def test_the_flag_or_a_full_user_agent_counts_as_a_contact(self):
        self.assertEqual(setup_notes(EMAIL, environ={'OPENROUTER_API_KEY': KEY}), [])
        self.assertEqual(setup_notes(environ={'OPENROUTER_API_KEY': KEY,
                                              'SEC_USER_AGENT': f'Desk research {EMAIL}'}), [])

    def test_a_replayed_snapshot_does_not_ask_for_a_contact(self):
        self.assertEqual(setup_notes(environ={'OPENROUTER_API_KEY': KEY}, needs_sec=False), [])

    def test_both_missing_gives_two_notes_key_first(self):
        notes = setup_notes(environ={})
        self.assertEqual(len(notes), 2)
        self.assertIn('OPENROUTER_API_KEY', notes[0])
        self.assertIn('SEC contact', notes[1])

    def test_no_note_repeats_a_configured_value(self):
        for notes in (setup_notes(environ={'OPENROUTER_API_KEY': KEY}),
                      setup_notes(environ={'SEC_CONTACT_EMAIL': EMAIL})):
            for note in notes:
                self.assertNotIn(KEY, note)
                self.assertNotIn(EMAIL, note)


if __name__ == '__main__':
    unittest.main()

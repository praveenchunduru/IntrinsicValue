import unittest
from unittest.mock import Mock

from investmentideas import sec_directory as sec


class SecDirectoryTests(unittest.TestCase):
    def test_live_official_mapping_takes_precedence(self):
        live = {'0': {'cik_str': 1234567, 'ticker': 'NEW', 'title': 'Synthetic issuer'}}
        fetch = Mock(return_value=live)
        directory, used_snapshot = sec.load_directory(fetch)
        fetch.assert_called_once_with('https://www.sec.gov/files/company_tickers.json')
        self.assertFalse(used_snapshot)
        self.assertEqual(directory, live)
        self.assertNotIn('AAPL', [row['ticker'] for row in directory.values()])

    def test_network_failure_uses_official_snapshot_with_aapl_cik(self):
        for failure in (RuntimeError('HTTP 403'), OSError('connection reset')):
            with self.subTest(failure=type(failure).__name__):
                directory, used_snapshot = sec.load_directory(Mock(side_effect=failure))
                self.assertTrue(used_snapshot)
                apple = next(row for row in directory.values() if row['ticker'] == 'AAPL')
                self.assertEqual(apple['cik_str'], 320193)
                self.assertIn('Apple', apple['title'])

    def test_malformed_live_data_uses_validated_snapshot(self):
        malformed = [None, [], {}, {'0': None},
                     {'0': {'cik_str': True, 'ticker': 'AAPL', 'title': 'Apple Inc.'}},
                     {'0': {'cik_str': -1, 'ticker': 'AAPL', 'title': 'Apple Inc.'}},
                     {'0': {'cik_str': 320193, 'ticker': '', 'title': 'Apple Inc.'}},
                     {'0': {'cik_str': 320193, 'ticker': 'AAPL', 'title': None}}]
        for value in malformed:
            with self.subTest(value=value):
                directory, used_snapshot = sec.load_directory(Mock(return_value=value))
                self.assertTrue(used_snapshot)
                self.assertGreater(len(directory), 1000)

    def test_snapshot_provenance_is_public_and_dated(self):
        metadata = sec.snapshot_metadata()
        self.assertEqual(metadata['source_url'], sec.DIRECTORY_URL)
        self.assertEqual(metadata['retrieved_at'], '2026-11-06T04:22:30Z')
        self.assertEqual(metadata['source_sha256'], '76b4b2abb9f7dd0e9cff38e2f20336952bb41806057b2dd1033e244f3e1faec2')
        self.assertEqual(len(metadata['directory_sha256']), 64)


if __name__ == '__main__':
    unittest.main()

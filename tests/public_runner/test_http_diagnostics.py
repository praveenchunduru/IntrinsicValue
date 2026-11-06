import io
from urllib.error import HTTPError
import tempfile
import unittest
from unittest.mock import patch

from investmentideas import core


class DiagnosticsTests(unittest.TestCase):
    def test_rate_limit_reports_only_safe_header(self):
        error = HTTPError(core.BASE,429,'provider echoed secret',{'Retry-After':'30'},io.BytesIO(b'private-key-and-request'))
        with patch.object(core,'urlopen',side_effect=error):
            with self.assertRaises(core.HTTPFailure) as caught:
                core.request_json(core.BASE,key='private-key')
        self.assertEqual(caught.exception.retry_after,30)
        self.assertNotIn('private-key',str(caught.exception))
        self.assertNotIn('echoed',str(caught.exception))
        self.assertIn('rate limited',str(caught.exception))

    def test_untrusted_retry_header_not_echoed(self):
        error = HTTPError(core.BASE,429,'unsafe',{'Retry-After':'secret-shaped-value'},io.BytesIO())
        with patch.object(core,'urlopen',side_effect=error):
            with self.assertRaises(core.HTTPFailure) as caught:
                core.request_json(core.BASE)
        self.assertIsNone(caught.exception.retry_after)
        self.assertNotIn('secret-shaped',str(caught.exception))

    def test_market_metadata_alone_stops_before_paid_generation(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(core,'collect',return_value=([
                {'id':'S1','kind':'market_snapshot','title':'Quote','url':'https://example.com','data':{}}],[])), \
                patch.object(core,'Router') as router:
            with self.assertRaisesRegex(RuntimeError,'no financial evidence'):
                core.run('AAPL','synthetic-key',directory=temp)
            router.assert_not_called()

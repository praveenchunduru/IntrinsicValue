import os
import unittest
from unittest.mock import patch

from investmentideas import live_validation as live


class LiveValidationTests(unittest.TestCase):
    def test_unlimited_resetting_and_over_budget_keys_rejected(self):
        for data in [{'limit':None}, {'limit':21}, {'limit':20,'limit_reset':'daily'},
                     {'limit':float('nan')}, {'limit':0}, {'limit':True},
                     {'limit':20,'limit_remaining':0}]:
            with patch.object(live, 'request_json', return_value={'data':data}):
                with self.assertRaises(ValueError):
                    live.check_key('synthetic-key')

    def test_explicit_cumulative_validation_budget_is_bounded(self):
        self.assertEqual(live.validation_budget('12'),12)
        for value in ('nan','inf','0','20.01','-1','unlimited',None,True):
            with self.subTest(value=value),self.assertRaises(ValueError):
                live.validation_budget(value)

    def test_credentials_mode_never_generates(self):
        with patch.dict(os.environ, {'OPENROUTER_API_KEY':'synthetic-key','VALIDATION_MODE':'credentials'}), \
                patch.object(live, 'request_json', return_value={'data':{'limit':20,'limit_remaining':20}}), \
                patch.object(live, 'run') as generate:
            live.main()
            generate.assert_not_called()

    def test_missing_key_and_invalid_mode_stop_before_provider(self):
        with patch.object(live, 'request_json') as request:
            with self.assertRaises(ValueError):
                live.check_key('')
            with patch.dict(os.environ, {'VALIDATION_MODE':'automatic'}):
                with self.assertRaises(ValueError):
                    live.main()
            request.assert_not_called()

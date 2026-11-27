import http.client
import json
import re
import socket
import subprocess
import sys
import time
import unittest


class LocalServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cls.port = sock.getsockname()[1]
        cls.process = subprocess.Popen([sys.executable, '-m', 'investmentideas', '--port', str(cls.port), '--no-browser'],
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        # Changed 2026-11-27: the token travels in the address printed in the terminal, not in the page.
        banner = cls.process.stdout.readline()
        found = re.search(r'#token=(\S+)', banner)
        if not found:
            cls.process.terminate()
            raise RuntimeError('Local server printed no address: ' + banner)
        cls.token = found.group(1)
        for _ in range(100):
            try:
                conn = http.client.HTTPConnection('127.0.0.1', cls.port, timeout=1)
                conn.request('GET', '/')
                response = conn.getresponse()
                cls.page = response.read().decode()
                conn.close()
                return
            except OSError:
                time.sleep(.05)
        cls.process.terminate()
        raise RuntimeError('Local server failed to start')

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.process.stdout.close()

    def request(self, path, method='GET', headers=None, body=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        conn.request(method, path, headers=headers or {}, body=body)
        response = conn.getresponse()
        code, content, cache = response.status, response.read(), response.getheader('Cache-Control')
        conn.close()
        return code, content, cache

    def test_page_and_private_status(self):
        self.assertIn('type="password"', self.page)
        self.assertEqual(self.request('/')[2], 'no-store')
        self.assertEqual(self.request('/status')[0], 403)
        self.assertNotIn(self.token, self.page)
        code, content, _ = self.request('/status', headers={'X-Research-Token': self.token})
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(content)['done'])

    def test_host_csrf_and_origin_rejected(self):
        self.assertEqual(self.request('/', headers={'Host':'evil.example'})[0], 403)
        self.assertEqual(self.request('/run', 'POST', body='{}')[0], 403)
        self.assertEqual(self.request('/run', 'POST', headers={'X-Research-Token':self.token,
                         'Origin':'https://evil.example'}, body='{}')[0], 403)

    def test_invalid_inputs_rejected_without_model_call(self):
        headers = {'X-Research-Token':self.token, 'Content-Type':'application/json'}
        for data in [{'key':'test','ticker':'../outside'}, {'key':'test','ticker':'AAPL','budget':-1},
                     {'key':'test','ticker':'AAPL','profile':'unknown'}, ['not-a-dict']]:
            self.assertEqual(self.request('/run', 'POST', headers=headers, body=json.dumps(data))[0], 400)

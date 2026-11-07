import io
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request, HTTPRedirectHandler

from investmentideas import core, research, sec_http


class SECHTTPTests(unittest.TestCase):
    def test_user_agent_never_contains_github_for_sec_hosts(self):
        # Regression, verified live 2026-11-07: SEC (data.sec.gov and
        # www.sec.gov) returns HTTP 403 for any User-Agent containing
        # "github" (case-insensitive), while the same request with that
        # token removed returns 200. The product agent must therefore
        # carry no github URL, even though the contact email still
        # identifies the requester.
        self.assertNotIn('github', sec_http.PRODUCT_USER_AGENT.lower())
        with sec_http.sec_contact_scope('analyst@research.test'):
            for url in ('https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json',
                        'https://www.sec.gov/files/company_tickers.json'):
                self.assertNotIn('github', sec_http.user_agent(url).lower())
                self.assertIn('analyst@research.test', sec_http.user_agent(url))

    def test_contact_is_required_and_does_not_escape_to_other_hosts(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(sec_http.SECContactRequired):
                sec_http.user_agent('https://data.sec.gov/submissions/CIK0000320193.json')
            with sec_http.sec_contact_scope('analyst@research.test'):
                self.assertIn('analyst@research.test',sec_http.user_agent('https://www.sec.gov/files/company_tickers.json'))
                for url in ('https://openrouter.ai/api/v1/models','https://www.sec.gov.evil.test/',
                            'https://www.sec.gov@other.test/'):
                    self.assertNotIn('analyst@research.test',sec_http.user_agent(url))
            with self.assertRaises(sec_http.SECContactRequired):
                sec_http.user_agent('https://data.sec.gov/')
            with sec_http.sec_contact_scope('analyst@research.test'):
                for url in ('http://www.sec.gov/','https://user@www.sec.gov/','https://data.sec.gov:8080/'):
                    with self.subTest(url=url),self.assertRaises(ValueError):sec_http.user_agent(url)

    def test_configured_identity_and_scoped_override(self):
        with patch.dict(os.environ,{'SEC_CONTACT_EMAIL':'environment@research.test','SEC_USER_AGENT':''}):
            self.assertIn('environment@research.test',sec_http.user_agent('https://data.sec.gov/'))
            with sec_http.sec_contact_scope('run@research.test'):
                self.assertIn('run@research.test',sec_http.user_agent('https://data.sec.gov/'))
                self.assertNotIn('environment@research.test',sec_http.user_agent('https://data.sec.gov/'))
            self.assertIn('environment@research.test',sec_http.user_agent('https://data.sec.gov/'))
        with patch.dict(os.environ,{'SEC_USER_AGENT':'Research Client analyst@research.test'}):
            self.assertEqual(sec_http.user_agent('https://www.sec.gov/'),'Research Client analyst@research.test')
        for invalid in ('No contact URL only','bad\r\nX-Injected: yes analyst@research.test'):
            with self.subTest(invalid=invalid),patch.dict(os.environ,{'SEC_USER_AGENT':invalid}):
                with self.assertRaises(ValueError):sec_http.user_agent('https://data.sec.gov/')
        for invalid in ('not-an-email','analyst@research.test\r\n','analyst@research.test\r\nX: y'):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                with sec_http.sec_contact_scope(invalid):pass

    def test_concurrent_runs_do_not_share_contact_identity(self):
        barrier=threading.Barrier(2)
        def identity(email):
            with sec_http.sec_contact_scope(email):
                barrier.wait(timeout=3)
                return sec_http.user_agent('https://data.sec.gov/')
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(identity,'one@research.test');b=pool.submit(identity,'two@research.test')
            self.assertIn('one@research.test',a.result())
            self.assertNotIn('two@research.test',a.result())
            self.assertIn('two@research.test',b.result())

    def test_all_sec_json_paths_use_contact_header(self):
        urls=['https://www.sec.gov/files/company_tickers.json',
              'https://data.sec.gov/submissions/CIK0000320193.json',
              'https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json']
        with sec_http.sec_contact_scope('analyst@research.test'):
            for url in urls:
                with self.subTest(url=url),patch.object(core,'urlopen',return_value=io.BytesIO(b'{}')) as opened:
                    self.assertEqual(core.request_json(url),{})
                    request=opened.call_args.args[0]
                    self.assertIn('analyst@research.test',request.get_header('User-agent'))
                    self.assertFalse(request.has_header('Authorization'))

    def test_authenticated_redirects_never_reach_redirect_target(self):
        req=Request(core.BASE+'/chat/completions',data=b'{}',headers={'Authorization':'Bearer synthetic'})
        for code in (301,302,303,307,308):
            for target in ('https://other.test/','http://other.test/',core.BASE+'/elsewhere'):
                with self.subTest(code=code,target=target),patch.object(HTTPRedirectHandler,'redirect_request') as parent:
                    with self.assertRaises(HTTPError):
                        sec_http._RedirectBoundary(True).redirect_request(req,None,code,'redirect',{},target)
                    parent.assert_not_called()
        with patch.object(sec_http,'build_opener') as opener:
            with self.assertRaisesRegex(ValueError,'HTTPS'):
                sec_http.open_http(Request('http://127.0.0.1/',headers={'Authorization':'Bearer synthetic'}))
            opener.assert_not_called()

    def test_authenticated_transport_installs_redirect_boundary(self):
        opener=MagicMock();opener.open.return_value=io.BytesIO(b'{"data":{}}')
        with patch.object(sec_http,'build_opener',return_value=opener) as build:
            self.assertEqual(core.request_json(core.BASE+'/auth/key',key='synthetic'),{'data':{}})
            self.assertTrue(build.call_args.args[0].authenticated)

    def test_json_response_is_bounded_before_parsing(self):
        response=MagicMock();response.__enter__.return_value=response
        response.read.return_value=b'{' + b' '*20_000_000
        with sec_http.sec_contact_scope('analyst@research.test'),patch.object(core,'urlopen',return_value=response), \
                patch.object(core.json,'loads') as parse:
            with self.assertRaisesRegex(RuntimeError,'20 MB'):
                core.request_json('https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json')
            self.assertEqual(response.read.call_args.args,(20_000_001,))
            parse.assert_not_called()

    def test_official_redirect_rejects_host_or_scheme_before_following(self):
        request=Request('https://www.sec.gov/Archives/filing.htm')
        for target in ('https://other.test/','http://www.sec.gov/','https://user@www.sec.gov/'):
            with self.subTest(target=target),patch.object(HTTPRedirectHandler,'redirect_request') as parent:
                with self.assertRaises(HTTPError):
                    sec_http._RedirectBoundary(False,sec_http.SEC_HOSTS).redirect_request(request,None,302,'redirect',{},target)
                parent.assert_not_called()

    def test_official_filing_limits_accept_large_normal_filings_and_stay_bounded(self):
        headers=Message();headers['Content-Type']='text/html; charset=utf-8'
        for size,accepted in ((3_000_001,True),(20_000_001,False)):
            response=MagicMock();response.__enter__.return_value=response
            response.geturl.return_value='https://www.sec.gov/Archives/filing.htm'
            response.headers=headers;response.read.return_value=b'x'*size
            with self.subTest(size=size),sec_http.sec_contact_scope('analyst@research.test'), \
                    patch.object(research,'urlopen',return_value=response) as opened:
                if accepted:
                    self.assertEqual(len(research.fetch_official_text('https://www.sec.gov/Archives/filing.htm')),size)
                else:
                    with self.assertRaisesRegex(RuntimeError,'20 MB'):
                        research.fetch_official_text('https://www.sec.gov/Archives/filing.htm')
                self.assertIn('analyst@research.test',opened.call_args.args[0].get_header('User-agent'))
                self.assertEqual(response.read.call_args.args,(research.MAX_DOCUMENT_BYTES+1,))


if __name__=='__main__':unittest.main()

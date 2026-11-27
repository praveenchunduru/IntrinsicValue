"""Loopback-only browser form. Keys stay in this process's memory, never in browser storage or files.

Request checks: the server binds 127.0.0.1 only; every request must carry a loopback Host
header; every state-changing request needs the session token in a custom header and,
when the browser sends one, a loopback Origin. The token is not in the page itself: it
travels in the address printed in the terminal (after #), so another local program that
fetches the page cannot spend a key held here.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
import hashlib
import html as html_escape
import json
import math
import os
from pathlib import Path
import secrets
import sys
import threading
import time

from .core import run
from .sec_http import sec_contact_scope

DOWNLOADS = {
    'memo.html': 'text/html; charset=utf-8',
    'memo.md': 'text/markdown; charset=utf-8',
    'assumptions.json': 'application/json',
    'valuation.json': 'application/json',
    'cashflows.csv': 'text/csv; charset=utf-8',
    'sensitivity.csv': 'text/csv; charset=utf-8',
    'sources.json': 'application/json',
    'source-ledger.json': 'application/json',
    'usage.json': 'application/json',
    'cost-summary.json': 'application/json',
    'reported-inputs.json': 'application/json',
    'budget-preflight.json': 'application/json',
}
BROWSER_BUDGET_MAX = 20
REQUEST_TIMEOUT_SECONDS = 15
MAX_BODY = 150000

PAGE = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>IntrinsicValue</title><style>
:root{--bg:#101820;--panel:#18242e;--ink:#e8edf1;--muted:#b9c7d1;--line:#52616d;--field:#1c2a35;--accent:#a5f3cf;--accent-ink:#10261c;--warn:#ffd28a}
body{font:17px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--ink);max-width:960px;margin:36px auto;padding:0 16px}
h1{font-size:36px;letter-spacing:-.5px;margin:0 0 4px}p{color:var(--muted);margin:.4em 0}label{display:block;margin:16px 0 4px;font-weight:600}
input,select,textarea,button{box-sizing:border-box;width:100%;font:inherit;padding:11px;border-radius:8px;border:1px solid var(--line);background:var(--field);color:var(--ink)}
button{margin-top:18px;background:var(--accent);color:var(--accent-ink);font-weight:700;cursor:pointer;border:0}button:disabled{opacity:.5;cursor:default}
#stop{background:transparent;color:var(--ink);border:1px solid var(--line);margin-top:10px}
small{display:block;color:var(--muted);font-size:14px;margin-top:3px}
#downloads{display:flex;flex-wrap:wrap;gap:8px}#downloads button{width:auto;margin-top:6px;padding:7px 12px;font-size:14px}
#summary{border-left:3px solid var(--accent);padding-left:12px;color:var(--ink)}details{margin:18px 0}summary{cursor:pointer;color:var(--muted)}
code{font-size:14px;overflow-wrap:anywhere;background:var(--panel);padding:2px 5px;border-radius:4px}
#memo{width:100%;height:80vh;border:1px solid var(--line);border-radius:10px;background:#fff;margin-top:14px}
#status{color:var(--ink);font-weight:600}.held{color:var(--accent)}
</style>
<h1>IntrinsicValue</h1>
<p>Values a company from its SEC filings and writes a research memo, using your OpenRouter key. Valuation research only; it is not investment advice.</p>
<form id="form" novalidate>
<label for="ticker">Ticker</label><input id="ticker" required maxlength="15" placeholder="MSFT" autocapitalize="characters" autocomplete="off">
<label for="key">OpenRouter key</label><input id="key" type="password" autocomplete="off" placeholder="__KEY_PLACEHOLDER__">
<small>__KEY_HELP__</small>
<label for="sec-contact">Your email (the SEC asks for one)</label><input id="sec-contact" type="email" autocomplete="email" placeholder="__CONTACT_PLACEHOLDER__" __CONTACT_REQUIRED__>
<small>__CONTACT_HELP__</small>
<label for="mode">Depth</label><select id="mode"><option value="full" __FULL_SELECTED__>Full research: memo, valuation and independent review</option><option value="quick" __QUICK_SELECTED__>Quick overview: four model calls, no valuation</option></select>
<details><summary>More options</summary>
<label for="profile">Models</label><select id="profile"><option value="balanced">Balanced: Claude Opus writes, GPT reviews</option><option value="economy">Economy: open-weight models, cheaper</option></select>
<label for="samples">Forecasts</label><select id="samples"><option value="">Default (three for Balanced, one for Economy)</option><option value="1">One</option><option value="3">Three, combined input by input</option><option value="5">Five, combined input by input</option></select>
<small>Full research only. Each extra forecast costs about $0.30 with Balanced.</small>
<label for="budget">Spending limit (US dollars, $1 to $20)</label><input id="budget" type="number" min="1" max="20" step="0.01" value="__DEFAULT_BUDGET__" required>
<small>A ceiling, not a price. The run stops before it would go over. Defaults: $20 full, $2 quick. Set a limit on your OpenRouter key too.</small>
<label for="evidence">Additional evidence (optional)</label><textarea id="evidence" rows="5" maxlength="100000" placeholder="A filing excerpt or dated notes, with source links and units"></textarea>
</details>
<button id="start" type="submit">Run full research (up to $20)</button>
<button id="stop" type="button" hidden>Stop after the current step</button></form>
<p id="status" role="status" aria-live="polite">Ready.</p>
<p id="files" hidden></p>
<p id="summary" hidden></p><div id="downloads" aria-label="Download research files"></div>
<p id="revalue" hidden>To try your own assumptions, edit assumptions.json in the run folder, then run this in a terminal (free, no model calls):<br><code id="revalue-cmd"></code></p>
<iframe id="memo" title="Research memo" sandbox hidden></iframe>
<p><small>Keep the terminal window that started IntrinsicValue open until the run finishes. Model review does not establish investment truth; read the open findings before using a memo.</small></p>
<script>
let csrf='';try{const h=new URLSearchParams(location.hash.slice(1)).get('token');if(h){sessionStorage.setItem('iv-token',h);history.replaceState(null,'',location.pathname);}csrf=sessionStorage.getItem('iv-token')||'';}catch(e){csrf=new URLSearchParams(location.hash.slice(1)).get('token')||'';}
let polling,failures=0,running=false;
const $=id=>document.getElementById(id);
const form=$('form'),start=$('start'),stopButton=$('stop'),memo=$('memo'),keyInput=$('key'),secContact=$('sec-contact'),summary=$('summary'),downloads=$('downloads'),revalue=$('revalue'),files=$('files');
const ticker=$('ticker'),mode=$('mode'),profile=$('profile'),budget=$('budget'),evidence=$('evidence'),samples=$('samples');
let budgetEdited=false;budget.addEventListener('input',()=>{budgetEdited=true;});
function label(){start.textContent=mode.value==='quick'?'Run quick overview (up to $'+Number(budget.value||2).toFixed(0)+')':'Run full research (up to $'+Number(budget.value||20).toFixed(0)+')';}
mode.addEventListener('change',()=>{if(!budgetEdited)budget.value=mode.value==='quick'?'2.00':'20.00';label();});budget.addEventListener('input',label);label();
function statusText(text){$('status').textContent=text;}
function busy(on){running=on;start.disabled=on;stopButton.hidden=!on;stopButton.disabled=false;}
function clearResult(){memo.hidden=true;memo.removeAttribute('srcdoc');summary.hidden=true;revalue.hidden=true;files.hidden=true;downloads.replaceChildren();}
if(!csrf){statusText('Open the address printed in the terminal that started IntrinsicValue (it ends with #token=...).');start.disabled=true;}
form.addEventListener('submit',async(e)=>{e.preventDefault();clearTimeout(polling);clearResult();busy(true);statusText('Checking your inputs...');
const payload={key:keyInput.value,sec_contact:secContact.value,ticker:ticker.value,mode:mode.value,profile:profile.value,budget:Number(budget.value),evidence:evidence.value,plan_samples:samples.value?Number(samples.value):null};
try{const response=await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json','X-Research-Token':csrf},body:JSON.stringify(payload)});
const result=await response.json();if(!response.ok)throw Error(result.error||'The request was refused.');
keyInput.value='';secContact.value='';if(result.key_held){keyInput.placeholder='Saved for this session: leave blank to use it';}if(result.contact_held){secContact.placeholder='Saved for this session: leave blank to use it';secContact.required=false;}
failures=0;await poll();
}catch(err){statusText(err.message);busy(false);}});
stopButton.addEventListener('click',async()=>{stopButton.disabled=true;try{const r=await fetch('/cancel',{method:'POST',headers:{'X-Research-Token':csrf,'Content-Type':'application/json'},body:'{}'});const d=await r.json();statusText(d.message||d.error);}catch(err){statusText('Could not reach the local server to stop the run.');}});
function showResult(data){
if(data.folder){files.textContent='Files: '+data.folder;files.hidden=false;}
if(data.memo_html&&!data.artifact_integrity_error){memo.srcdoc=data.memo_html;memo.hidden=false;}
const parts=[];if(data.status_label)parts.push(data.status_label);if(typeof data.cost_usd==='number')parts.push(data.cost_usd>0?'$'+data.cost_usd.toFixed(2)+' spent or held':'nothing charged');if(typeof data.base_value==='string')parts.push(data.base_value);
if(parts.length){summary.textContent=parts.join(' · ');summary.hidden=false;}
downloads.replaceChildren();if(data.artifact_integrity_error)return;
const names=data.downloads||[];if(names.includes('assumptions.json')&&data.revalue_command){$('revalue-cmd').textContent=data.revalue_command;revalue.hidden=false;}
for(const name of names){const button=document.createElement('button');button.type='button';button.textContent=(name==='memo.html'?'Download memo (HTML, prints to PDF)':'Download '+name);button.addEventListener('click',()=>download(name,data.download_prefix||''));downloads.append(button);}}
async function download(name,prefix){try{const r=await fetch('/download/'+encodeURIComponent(name),{headers:{'X-Research-Token':csrf}});if(!r.ok){let m='That file is not available.';try{m=(await r.json()).error||m;}catch(e){}throw Error(m);}const u=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=u;a.download=prefix+name;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);}catch(err){statusText(err.message);}}
async function poll(){try{const r=await fetch('/status',{headers:{'X-Research-Token':csrf}});if(!r.ok)throw Error('status');const data=await r.json();failures=0;
if(data.message)statusText(data.message);
if(data.done){busy(false);if(data.folder||data.memo_html)showResult(data);}else{busy(true);polling=setTimeout(poll,1000);}}
catch(err){failures+=1;if(failures<=10){statusText('Waiting for the local server...');polling=setTimeout(poll,Math.min(1000*failures,5000));}else{statusText('Lost contact with the local server. If its terminal window is still open, reload this page; a run in progress keeps going.');busy(false);}}}
if(csrf)poll();
</script></html>'''


def _available_files(folder):
    """Only known files inside the run folder may be downloaded; memo.html is made from memo.md."""
    base = folder.resolve()
    names = []
    for name in DOWNLOADS:
        source = 'memo.md' if name == 'memo.html' else name
        path = base / source
        if path.is_file() and path.resolve().is_relative_to(base):
            names.append(name)
    return names


_CRITICAL_ARTIFACTS=('memo.md','assumptions.json','valuation.json','sources.json',
    'cashflows.csv','sensitivity.csv','source-ledger.json')


def _browser_artifact_integrity(folder,saved,*,target=None):
    """Read-only, bounded download checks; raw archives are checked on resume."""
    if not (saved.get('complete') or saved.get('quality_status')=='reviewed_draft' or
            saved.get('completed_artifact_hashes')):
        return None
    bound=saved.get('completed_artifact_hashes')
    if not isinstance(bound,dict):
        return 'This finished run has no record of its reviewed files. Start the same ticker again for a fresh review.'
    names=set(_CRITICAL_ARTIFACTS)|({target} if target else set())
    changed=[]
    base=folder.resolve()
    for name in names:
        expected=bound.get(name)
        path=folder/name
        if (not isinstance(expected,str) or len(expected)!=64 or path.is_symlink() or
                not path.resolve().is_relative_to(base) or not path.is_file() or
                path.stat().st_size>10_000_000):
            changed.append(name)
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            changed.append(name)
    if changed:
        return ('Files in this run changed after review ('+', '.join(sorted(changed))+
            '), so they are no longer shown as reviewed. Start the same ticker again for a fresh review.')
    return None


def _result_metadata(folder,*,full=False,target=None):
    result = {'downloads': _available_files(folder)}
    try:
        path = folder / 'state.json'
        if path.stat().st_size > 25_000_000:
            raise ValueError('Saved state exceeds the browser limit')
        saved = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(saved, dict):
            raise ValueError('Saved state is not an object')
        full=full or str(saved.get('version','')).startswith('full-')
        ledger = saved.get('ledger', [])
        costs = [row.get('charged_or_reserved_usd') for row in ledger if isinstance(row, dict)]
        if all(isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) and c >= 0 for c in costs):
            result['cost_usd'] = sum(costs)
        iteration = saved.get('iteration')
        if isinstance(iteration, int) and not isinstance(iteration, bool) and iteration >= 0:
            result['iterations'] = iteration
        quality = saved.get('quality_status')
        if quality in {'reviewed_draft', 'needs_review', 'unresolved_findings', 'budget_paused', 'missing_valuation'}:
            result['quality_status'] = quality
        if full:
            issue=_browser_artifact_integrity(folder,saved,target=target)
            if issue:
                result.update(quality_status='needs_review',artifact_integrity_error=issue)
            elif target and isinstance(saved.get('completed_artifact_hashes'),dict):
                result['_download_sha256']=saved['completed_artifact_hashes'].get('memo.md' if target=='memo.html' else target)
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        if full:
            result.update(quality_status='needs_review',
                artifact_integrity_error='The saved state of this run cannot be read. Start the same ticker again before relying on its review status.')
    return result


def _describe(folder, result):
    """Status label, base value and the revalue command for the page."""
    from .runs import summarize, per_share
    extra = {}
    try:
        row = summarize(folder)
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return extra
    extra['status_label'] = row['label']
    if row.get('base') is not None:
        text = f'Base value {per_share(row["base"], row.get("currency"))} a share'
        if row.get('ratio') is not None:
            text += f', value / price {row["ratio"]:.2f}'
        extra['base_value'] = text
    extra['folder'] = str(Path(folder).resolve())
    assumptions = Path(folder).resolve() / 'assumptions.json'
    extra['revalue_command'] = f'python3 -m intrinsicvalue --revalue "{assumptions}"'
    stamp = datetime.now().strftime('%Y-%m-%d')
    extra['download_prefix'] = f'{row["ticker"]}-{stamp}-'
    return extra


def _memo_html(folder):
    """The memo as a self-contained page made from memo.md (bounded), or None."""
    from .memo_html import MAX_MEMO_BYTES, convert
    path = Path(folder) / 'memo.md'
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
            return None
        return convert(path.read_text(encoding='utf-8', errors='replace'))
    except OSError:
        return None


def _desktop_likely():
    if sys.platform in ('darwin', 'win32'):
        return True
    return bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))


def create_server(port=8765, directory='research-output', default_mode='full', *, sec_contact=None,
                  key_check=None, token=None):
    """Create a loopback server; useful for embedding and offline HTTP tests.

    ``server.token`` is the session token; ``key_check`` replaces the free OpenRouter
    key check (tests pass a stand-in so no network is used).
    """
    from . import explain, preflight
    from .progress import Reporter, RunStopped
    from . import runs as saved_runs
    if default_mode not in {'full', 'quick'}:
        raise ValueError('Unknown research mode')
    with sec_contact_scope(sec_contact):
        pass
    configured_contact=sec_contact
    token = token or secrets.token_urlsafe(32)
    state = {'done': True, 'message': 'Ready.'}
    active = {'folder': None, 'reporter': None}
    held = {'key': None, 'contact': None}
    verified = set()
    lock = threading.Lock()
    allowed = set()
    check = key_check or preflight.check_key

    def key_held():
        return bool(held['key'] or os.environ.get('OPENROUTER_API_KEY', '').strip())

    def contact_held():
        return bool(held['contact'] or configured_contact or preflight.has_contact())

    class Handler(BaseHTTPRequestHandler):
        timeout = REQUEST_TIMEOUT_SECONDS  # a body shorter than its Content-Length cannot hold a thread

        def log_message(self, *args):
            pass  # Never log request bodies or keys.

        def handle_one_request(self):
            try:
                super().handle_one_request()
            except (ConnectionError, TimeoutError, OSError):
                self.close_connection = True  # a client that left or stalled; nothing to answer

        def response(self, code, value, html=False, content_type=None, filename=None, close=False):
            data = value if isinstance(value, bytes) else (value.encode() if html else json.dumps(value).encode())
            self.send_response(code)
            self.send_header('Content-Type', content_type or ('text/html; charset=utf-8' if html else 'application/json'))
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
            if filename:
                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            if close:
                self.send_header('Connection', 'close')
                self.close_connection = True
            self.end_headers()
            try:
                self.wfile.write(data)
            except (ConnectionError, OSError):
                self.close_connection = True

        def valid(self):
            return self.headers.get('Host') in allowed

        def authenticated(self):
            supplied = self.headers.get('X-Research-Token', '')
            try:
                return secrets.compare_digest(supplied.encode('utf-8', 'surrogateescape'), token.encode())
            except (TypeError, UnicodeError, AttributeError):
                return False

        def page(self):
            key_note = ('A key is already set for this session (from .env, the environment or an earlier run). '
                        'Leave this blank to use it.' if key_held() else
                        'Sent only to OpenRouter and kept in memory until you close the terminal. '
                        f'Create one at {preflight.KEYS_PAGE}.')
            contact_note = ('An email is already set for this session. Leave this blank to use it.' if contact_held() else
                            'Sent only to sec.gov, which asks every automated visitor for a contact. Kept in memory only.')
            values = {'__KEY_PLACEHOLDER__': 'Saved for this session: leave blank to use it' if key_held() else 'sk-or-...',
                      '__KEY_HELP__': html_escape.escape(key_note),
                      '__CONTACT_PLACEHOLDER__': 'Saved for this session: leave blank to use it' if contact_held() else 'you@example.com',
                      '__CONTACT_REQUIRED__': '' if contact_held() else 'required',
                      '__CONTACT_HELP__': html_escape.escape(contact_note),
                      '__FULL_SELECTED__': 'selected' if default_mode == 'full' else '',
                      '__QUICK_SELECTED__': 'selected' if default_mode == 'quick' else '',
                      '__DEFAULT_BUDGET__': '20.00' if default_mode == 'full' else '2.00'}
            page = PAGE
            for name, value in values.items():
                page = page.replace(name, value)
            return page

        def snapshot(self):
            with lock:
                snapshot = dict(state)
                folder = active['folder']
            snapshot.pop('_secrets', None)
            if snapshot.get('done') and folder is not None:
                snapshot.pop('artifact_integrity_error',None)
                snapshot.update(_result_metadata(folder,full=snapshot.get('mode')=='full'))
                if snapshot.get('artifact_integrity_error'):
                    snapshot['message']=snapshot['artifact_integrity_error']
                    snapshot.pop('memo',None)
                    snapshot.pop('memo_html',None)
            return snapshot

        def do_GET(self):
            if not self.valid():
                return self.response(403, {'error': 'Local host required'})
            if self.path == '/':
                return self.response(200, self.page(), html=True)
            if self.path == '/status':
                if not self.authenticated():
                    return self.response(403, {'error': 'Open the address printed in the terminal, then try again.'})
                return self.response(200, self.snapshot())
            if self.path.startswith('/download/'):
                if not self.authenticated():
                    return self.response(403, {'error': 'Invalid local request'})
                name = self.path[len('/download/'):]
                if name not in DOWNLOADS:
                    return self.response(404, {'error': 'Download not found'})
                with lock:
                    folder = active['folder']
                    full=state.get('mode')=='full'
                if folder is None or name not in _available_files(folder):
                    return self.response(404, {'error': 'Download not found'})
                source = 'memo.md' if name == 'memo.html' else name
                path = folder / source
                try:
                    integrity=_result_metadata(folder,full=full,target=source)
                    if integrity.get('artifact_integrity_error'):
                        return self.response(409,{'error':integrity['artifact_integrity_error']})
                    if path.stat().st_size > 10_000_000:
                        return self.response(413, {'error': 'This file is too large for the browser; open it in the run folder.'})
                    content=path.read_bytes()
                    expected=integrity.get('_download_sha256')
                    if expected and hashlib.sha256(content).hexdigest()!=expected:
                        return self.response(409,{'error':'The file changed while it was being downloaded. Start the same ticker again for a fresh review.'})
                    if name == 'memo.html':
                        from .memo_html import convert
                        content = convert(content.decode('utf-8', errors='replace')).encode('utf-8')
                    return self.response(200, content, content_type=DOWNLOADS[name], filename=name)
                except OSError:
                    return self.response(404, {'error': 'Download not found'})
            self.response(404, {'error': 'Not found'})

        def refuse(self, code, message, field=None):
            body = {'error': message}
            if field:
                body['field'] = field
            return self.response(code, body, close=True)

        def do_POST(self):
            origin = self.headers.get('Origin')
            if (not self.valid() or self.path not in ('/run', '/cancel') or not self.authenticated() or
                    (origin and origin not in {f'http://{host}' for host in allowed})):
                return self.refuse(403, 'Invalid local request')
            if self.path == '/cancel':
                with lock:
                    reporter = active['reporter']
                    if state['done'] or reporter is None:
                        return self.response(409, {'error': 'No run is going.'})
                    reporter.stop()
                    state['message'] = 'Stopping after the current step. Finished steps are kept.'
                return self.response(202, {'message': 'Stopping after the current step. Finished steps are kept.'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                return self.refuse(400, 'The request was malformed.')
            if not 0 < size <= MAX_BODY:
                return self.refuse(400, 'The request is empty or too large. Additional evidence can be at most 100 KB.')
            try:
                raw = self.rfile.read(size)
                if len(raw) != size:
                    return self.refuse(400, 'The request was cut off. Try again.')
                data = json.loads(raw)
            except (ValueError, RecursionError, UnicodeDecodeError, OSError):
                return self.refuse(400, 'The request was malformed.')
            if not isinstance(data, dict):
                return self.refuse(400, 'The request was malformed.')
            try:
                from .core import MODELS
                ticker = preflight.check_ticker(data.get('ticker', ''))
                mode = data.get('mode', default_mode)
                if mode not in {'full', 'quick'}:
                    raise preflight.PreflightError('Choose Full research or Quick overview.', 'mode')
                profile = data.get('profile', 'balanced')
                if profile not in MODELS:
                    raise preflight.PreflightError('Choose the Balanced or Economy models.', 'profile')
                budget = data.get('budget', 20 if mode == 'full' else 2)
                if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget) \
                        or not 1 <= budget <= BROWSER_BUDGET_MAX:
                    raise preflight.PreflightError('The spending limit must be between $1 and $20 in the browser. '
                                                   'For more, use the command line with --budget.', 'budget')
                budget = float(budget)
                evidence = data.get('evidence', '')
                if evidence is None:
                    evidence = ''
                if not isinstance(evidence, str) or len(evidence.encode()) > 100000:
                    raise preflight.PreflightError('Additional evidence must be text of at most 100 KB.', 'evidence')
                plan_samples = data.get('plan_samples')
                if plan_samples is not None and (type(plan_samples) is not int or plan_samples not in (1, 2, 3, 4, 5)):
                    raise preflight.PreflightError('Forecasts must be a whole number from 1 to 5.', 'plan_samples')
                contact = data.get('sec_contact') or ''
                if not isinstance(contact, str):
                    raise preflight.PreflightError('Enter your email as name@example.com.', 'sec_contact')
                contact = contact.strip()
                if contact:
                    preflight.check_contact(contact, {}, browser=True)
                else:
                    contact = held['contact'] or ''
                    preflight.check_contact(contact or configured_contact, None, browser=True)
                key = data.get('key') or ''
                if not isinstance(key, str):
                    raise preflight.PreflightError('Paste your OpenRouter key as text.', 'key')
                key = key.strip() or held['key'] or os.environ.get('OPENROUTER_API_KEY', '')
                key = preflight.check_key_format(key, browser=True)
            except preflight.PreflightError as exc:
                return self.refuse(400, str(exc), exc.field)
            except (ValueError, TypeError, AttributeError) as exc:
                return self.refuse(400, 'One of the fields could not be read. Check them and try again.')
            with lock:
                if not state['done']:
                    return self.response(409, {'error': 'A run is already going. Wait for it to finish or stop it first.'})
            digest = hashlib.sha256(key.encode()).hexdigest()
            if digest not in verified:
                try:
                    check(key)
                except preflight.PreflightError as exc:
                    return self.refuse(400, str(exc), 'key')
                verified.add(digest)
            reporter = Reporter(directory, ticker, quick=mode == 'quick', out=lambda text: None)
            with lock:
                if not state['done']:
                    return self.response(409, {'error': 'A run is already going. Wait for it to finish or stop it first.'})
                state.clear()
                state.update(done=False, message='Starting...', mode=mode, profile=profile, ticker=ticker)
                active['folder'] = None
                active['reporter'] = reporter
                held['key'] = key
                if contact:
                    held['contact'] = contact
            started = time.time()

            def update(text):
                with lock:
                    if not reporter.stop_requested.is_set():
                        state['message'] = text
            reporter.out = lambda text: update(text)

            def finish(folder, message, memo_text=None):
                result, extra = {}, {}
                if folder is not None:
                    try:
                        from .memo_html import write_beside
                        write_beside(folder)
                    except (OSError, ValueError):
                        pass
                    result = _result_metadata(folder, full=mode == 'full')
                    extra = _describe(folder, result)
                    html = _memo_html(folder)
                    if html:
                        extra['memo_html'] = html
                    if memo_text is None:
                        try:
                            path = folder / 'memo.md'
                            if path.is_file() and path.stat().st_size <= 1_000_000:
                                memo_text = path.read_text(encoding='utf-8', errors='replace')
                        except OSError:
                            memo_text = None
                with lock:
                    active['folder'] = folder
                    state.update(done=True, message=message, **result, **extra)
                    if memo_text is not None:
                        state['memo'] = memo_text

            def located(exc=None):
                found = getattr(exc, 'folder', None) if exc is not None else None
                try:
                    found = Path(found) if isinstance(found, (str, os.PathLike)) else None
                except TypeError:
                    found = None
                return found or saved_runs.find_run_folder(directory, ticker, started)

            def work():
                try:
                    extra = {}
                    if mode == 'full':
                        from .pipeline import run as runner
                        extra['plan_samples'] = plan_samples
                    else:
                        runner = run
                    with sec_contact_scope(contact or configured_contact):
                        folder = Path(runner(ticker, key, profile=profile, budget=budget, directory=directory,
                                             evidence=evidence, progress=reporter, **extra))
                    row = saved_runs.summarize(folder)
                    finish(folder, f'{row["label"]}. {row["detail"]}')
                except RunStopped:
                    folder = located()
                    finish(folder, 'Stopped. Finished steps are kept; start the same ticker again to continue.')
                except BaseException as exc:  # noqa: BLE001 - the job must always end with a message
                    if isinstance(exc, (ValueError, RuntimeError, OSError, KeyError, TypeError, IndexError)):
                        reason = explain.plain(explain.redact(str(exc), key, contact), browser=True, ticker=ticker)
                    else:
                        reason = ('The run stopped on an unexpected problem. Finished steps are kept; '
                                  'start the same ticker again to continue.')
                    folder = None
                    try:
                        folder = located(exc)
                        if folder is not None:
                            saved_runs.replace_stale_memo(folder, started, reason,
                                                          'Fix the cause and start the same ticker again; finished steps are reused.')
                    except (OSError, ValueError, TypeError):
                        pass
                    try:
                        finish(folder, 'Stopped: ' + reason)
                    except BaseException:  # noqa: BLE001
                        with lock:
                            state.update(done=True, message='Stopped: ' + reason)
                finally:
                    with lock:
                        if not state.get('done'):
                            state.update(done=True, message='The run stopped. Finished steps are kept; start the same ticker again to continue.')
                        active['reporter'] = None

            threading.Thread(target=work, daemon=True).start()
            self.response(202, {'accepted': True, 'key_held': True, 'contact_held': contact_held()})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.token = token
    actual_port = server.server_address[1]
    allowed.update({f'127.0.0.1:{actual_port}', f'localhost:{actual_port}'})
    return server


def _check_writable(directory):
    folder = Path(directory)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f'.write-check-{os.getpid()}'
        probe.write_text('ok', encoding='utf-8')
        probe.unlink()
    except OSError as exc:
        raise ValueError(f'Runs cannot be saved in {folder.resolve()} ({exc.strerror or exc}). '
                         'Choose a folder you can write to with --output FOLDER.') from None


def serve(port=8765, directory='research-output', default_mode='full', *, sec_contact=None, open_browser=True):
    _check_writable(directory)
    server = create_server(port, directory, default_mode, sec_contact=sec_contact)
    address = f'http://127.0.0.1:{server.server_address[1]}/#token={server.token}'
    print(f'IntrinsicValue is running at {address}', flush=True)
    print('Keep this window open while a run is going. Press Ctrl+C to stop the server.', flush=True)
    if open_browser and _desktop_likely() and sys.stdout.isatty() and not os.environ.get('INTRINSICVALUE_NO_BROWSER'):
        import webbrowser
        try:
            webbrowser.open(address)
        except (OSError, webbrowser.Error):
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped the server. Finished steps of any run are saved.', flush=True)
    finally:
        server.server_close()

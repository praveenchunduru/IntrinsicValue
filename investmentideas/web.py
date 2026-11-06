"""Loopback-only interface. Keys remain in memory, never browser storage or files."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import threading

from .core import run
from .sec_http import sec_contact_scope

DOWNLOADS = {
    'memo.md': 'text/markdown; charset=utf-8',
    'assumptions.json': 'application/json',
    'valuation.json': 'application/json',
    'sources.json': 'application/json',
    'source-ledger.json': 'application/json',
    'usage.json': 'application/json',
    'cost-summary.json': 'application/json',
    'cashflows.csv': 'text/csv; charset=utf-8',
    'sensitivity.csv': 'text/csv; charset=utf-8',
    'reported-inputs.json': 'application/json',
    'budget-preflight.json': 'application/json',
}

PAGE = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>InvestmentIdeas</title><style>
body{font:17px/1.6 system-ui;background:#101820;color:#e8edf1;max-width:960px;margin:42px auto;padding:0 22px}
h1{font-size:40px;letter-spacing:-1px;margin-bottom:0}p{color:#b9c7d1}label{display:block;margin:18px 0 5px}
input,select,textarea,button{box-sizing:border-box;width:100%;font:inherit;padding:12px;border-radius:8px;border:1px solid #52616d;background:#1c2a35;color:white}
button{margin-top:20px;background:#a5f3cf;color:#10261c;font-weight:700;cursor:pointer}button:disabled{opacity:.5}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#18242e;padding:20px;border-radius:10px;font-size:15px}small{color:#b9c7d1}
#downloads{display:flex;flex-wrap:wrap;gap:8px}#downloads button{width:auto;margin-top:5px;padding:8px 12px;font-size:14px}
#summary{border-left:3px solid #a5f3cf;padding-left:14px}details{margin:20px 0}a{color:#a5f3cf}
</style><h1>InvestmentIdeas</h1><p>Evidence-led equity research, using your OpenRouter key.</p>
<small>Runs on your computer. Full research adds valuation and review revisions; a quick overview uses four bounded model calls. No subscriptions or financial-data API keys required.</small>
<form id="form"><label for="key">OpenRouter API key</label><input id="key" type="password" autocomplete="off" placeholder="sk-or-…">
<small>The key stays in this process’s memory and is sent only to OpenRouter. An environment key can also be used.</small>
<label for="sec-contact">SEC requester contact email</label><input id="sec-contact" type="email" autocomplete="off" placeholder="you@example.com">
<small>SEC requires a real requester contact for filing access. This value stays in memory and is sent only to SEC; SEC_CONTACT_EMAIL can also supply it.</small>
<label for="ticker">Ticker</label><input id="ticker" required value="AAPL" maxlength="15">
<label for="mode">Research depth</label><select id="mode"><option value="full" __FULL_SELECTED__>Full memo — research, editable valuation, review and revision</option><option value="quick" __QUICK_SELECTED__>Quick financial overview — four model calls</option></select>
<label for="profile">Model profile</label><select id="profile"><option value="balanced">Balanced — Qwen extraction, Claude Sonnet analysis</option><option value="economy">Economy — Qwen extraction, DeepSeek analysis</option></select>
<label for="budget">Total spending allowance (USD)</label><input id="budget" type="number" min="1" max="20" step="0.01" value="__DEFAULT_BUDGET__" required>
<small>Full research starts with a $10 planning allowance; quick overview starts with $2. This is not a promised charge or completion. Free evidence collection precedes a budget preflight. Requests stop before the next reservation exceeds your allowance; prior charges and uncertain requests count when resuming. Also set a provider credit limit on your dedicated key.</small>
<details><summary>Additional evidence</summary><label for="evidence">Optional dated source text or filing excerpt</label><textarea id="evidence" rows="5" maxlength="100000" placeholder="Include source URLs, dates and units. Useful if public sources are unavailable."></textarea></details>
<button id="start">Generate research draft</button></form>
<p id="status" role="status" aria-live="polite">Ready. Completed stages are saved locally and can be resumed.</p>
<p id="summary" hidden></p><div id="downloads" aria-label="Download research files"></div><p id="revalue" hidden>Edit the downloaded assumptions, then recompute without model charges:<br><code>python -m investmentideas --revalue assumptions.json --output revalued</code></p><pre id="memo" hidden></pre>
<small>Model review does not establish investment truth. Inspect the evidence, assumptions and outstanding findings before using a draft.</small>
<script>
const csrf='__TOKEN__';let polling;
const form=document.getElementById('form'),start=document.getElementById('start'),memo=document.getElementById('memo');
const keyInput=document.getElementById('key'),summary=document.getElementById('summary'),downloads=document.getElementById('downloads'),revalue=document.getElementById('revalue');
const secContact=document.getElementById('sec-contact');
const ticker=document.getElementById('ticker'),mode=document.getElementById('mode'),profile=document.getElementById('profile'),budget=document.getElementById('budget'),evidence=document.getElementById('evidence');
let budgetEdited=false;budget.addEventListener('input',()=>{budgetEdited=true;});mode.addEventListener('change',()=>{if(!budgetEdited)budget.value=mode.value==='quick'?'2.00':'10.00';});
form.addEventListener('submit',async(e)=>{e.preventDefault();clearTimeout(polling);start.disabled=true;memo.hidden=true;summary.hidden=true;revalue.hidden=true;downloads.replaceChildren();statusText('Starting…');
const payload={key:keyInput.value,sec_contact:secContact.value,ticker:ticker.value,mode:mode.value,profile:profile.value,budget:Number(budget.value),evidence:evidence.value};keyInput.value='';secContact.value='';
try{const response=await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json','X-Research-Token':csrf},body:JSON.stringify(payload)});
const result=await response.json();if(!response.ok)throw Error(result.error);await poll();
}catch(err){statusText(err.message);start.disabled=false;}});
function statusText(text){document.getElementById('status').textContent=text;}
function showResult(data){if(data.memo){memo.textContent=data.memo;memo.hidden=false;}
const parts=[];if(typeof data.cost_usd==='number')parts.push('$'+data.cost_usd.toFixed(6)+' reported or reserved');if(typeof data.iterations==='number')parts.push(data.iterations+' review revision(s)');if(data.quality_status)parts.push(data.quality_status.replaceAll('_',' '));
if(parts.length){summary.textContent=parts.join(' · ');summary.hidden=false;}
revalue.hidden=!(data.downloads||[]).includes('assumptions.json');downloads.replaceChildren();for(const name of data.downloads||[]){const button=document.createElement('button');button.type='button';button.textContent='Download '+name;button.addEventListener('click',()=>download(name));downloads.append(button);}}
async function download(name){try{const r=await fetch('/download/'+encodeURIComponent(name),{headers:{'X-Research-Token':csrf}});if(!r.ok)throw Error('Download unavailable');const u=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);}catch(err){statusText(err.message);}}
async function poll(){try{const r=await fetch('/status',{headers:{'X-Research-Token':csrf}});if(!r.ok)throw Error('Status unavailable');const data=await r.json();statusText(data.message);if(data.done){start.disabled=false;showResult(data);}else{polling=setTimeout(poll,1000);}}catch(err){statusText('Connection lost. Restart the server and submit the same settings to resume.');start.disabled=false;}}
</script></html>'''


def _available_files(folder):
    """Only known files inside the completed run may be downloaded."""
    base = folder.resolve()
    return [name for name in DOWNLOADS if (base / name).is_file()
            and (base / name).resolve().is_relative_to(base)]


_CRITICAL_ARTIFACTS=('memo.md','assumptions.json','valuation.json','sources.json',
    'cashflows.csv','sensitivity.csv','source-ledger.json')


def _browser_artifact_integrity(folder,saved,*,target=None):
    """Read-only, bounded download checks; raw archives are checked on resume."""
    if not (saved.get('complete') or saved.get('quality_status')=='reviewed_draft' or
            saved.get('completed_artifact_hashes')):
        return None
    bound=saved.get('completed_artifact_hashes')
    if not isinstance(bound,dict):
        return 'This completed full run has no exact artifact binding; resubmit the same settings for a fresh review.'
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
        return ('Saved artifacts changed after review: '+', '.join(sorted(changed))+
            '. Resubmit the same settings to preserve edits and require a fresh review; '
            'submit intentional edits through --draft/--forecast-plan.')
    return None


def _result_metadata(folder,*,full=False,target=None):
    result = {'downloads': _available_files(folder)}
    try:
        path = folder / 'state.json'
        if path.stat().st_size > 10_000_000:
            raise ValueError('Saved state exceeds the browser limit')
        saved = json.loads(path.read_text(encoding='utf-8'))
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
                result['_download_sha256']=saved['completed_artifact_hashes'].get(target)
    except (OSError, ValueError, TypeError, AttributeError):
        if full:
            result.update(quality_status='needs_review',
                artifact_integrity_error='Saved full-run state is unavailable; resubmit the same settings before relying on its review status.')
    return result


def create_server(port=8765, directory='research-output', default_mode='full', *, sec_contact=None):
    """Create a loopback server; useful for embedding and offline HTTP tests."""
    if default_mode not in {'full', 'quick'}:
        raise ValueError('Unknown research mode')
    with sec_contact_scope(sec_contact):
        pass
    configured_contact=sec_contact
    token = secrets.token_urlsafe(32)
    state = {'done': True, 'message': 'Ready'}
    active = {'folder': None}
    lock = threading.Lock()
    allowed = set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log request bodies or keys.

        def response(self, code, value, html=False, content_type=None, filename=None):
            data = value if isinstance(value, bytes) else (value.encode() if html else json.dumps(value).encode())
            self.send_response(code)
            self.send_header('Content-Type', content_type or ('text/html; charset=utf-8' if html else 'application/json'))
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
            if filename:
                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(data)

        def valid(self):
            return self.headers.get('Host') in allowed

        def authenticated(self):
            return secrets.compare_digest(self.headers.get('X-Research-Token', ''), token)

        def do_GET(self):
            if not self.valid():
                return self.response(403, {'error': 'Local host required'})
            if self.path == '/':
                page = PAGE.replace('__TOKEN__', token).replace('__FULL_SELECTED__', 'selected' if default_mode == 'full' else '').replace('__QUICK_SELECTED__', 'selected' if default_mode == 'quick' else '').replace('__DEFAULT_BUDGET__', '10.00' if default_mode == 'full' else '2.00')
                return self.response(200, page, html=True)
            if self.path == '/status' and self.authenticated():
                with lock:
                    snapshot = dict(state)
                    folder=active['folder']
                if snapshot.get('done') and folder is not None:
                    snapshot.pop('artifact_integrity_error',None)
                    snapshot.update(_result_metadata(folder,full=snapshot.get('mode')=='full'))
                    if snapshot.get('artifact_integrity_error'):
                        snapshot['message']=snapshot['artifact_integrity_error']
                        snapshot.pop('memo',None)
                return self.response(200, snapshot)
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
                path = folder / name
                try:
                    integrity=_result_metadata(folder,full=full,target=name)
                    if integrity.get('artifact_integrity_error'):
                        return self.response(409,{'error':integrity['artifact_integrity_error']})
                    if path.stat().st_size > 10_000_000:
                        return self.response(413, {'error': 'File exceeds the browser download limit; open the local run folder'})
                    content=path.read_bytes()
                    expected=integrity.get('_download_sha256')
                    if expected and hashlib.sha256(content).hexdigest()!=expected:
                        return self.response(409,{'error':'Artifact changed during download; resubmit the same settings for review.'})
                    return self.response(200, content, content_type=DOWNLOADS[name], filename=name)
                except OSError:
                    return self.response(404, {'error': 'Download not found'})
            self.response(404, {'error': 'Not found'})

        def do_POST(self):
            origin = self.headers.get('Origin')
            if (not self.valid() or self.path != '/run' or not self.authenticated() or
                    (origin and origin not in {f'http://{host}' for host in allowed})):
                return self.response(403, {'error': 'Invalid local request'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 150000:
                    raise ValueError('Request too large or empty')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('Expected a JSON object')
                key = data.get('key') or os.environ.get('OPENROUTER_API_KEY', '')
                if not isinstance(key, str) or not key.strip():
                    raise ValueError('Enter your OpenRouter API key')
                contact=data.get('sec_contact')
                with sec_contact_scope(contact):
                    pass
                from .core import symbol, MODELS
                ticker = symbol(data.get('ticker', ''))
                mode = data.get('mode', default_mode)
                if mode not in {'full', 'quick'}:
                    raise ValueError('Invalid research mode')
                profile = data.get('profile', 'balanced')
                if profile not in MODELS:
                    raise ValueError('Invalid model profile')
                budget = float(data.get('budget', 10 if mode == 'full' else 2))
                if not math.isfinite(budget) or not 1 <= budget <= 20:
                    raise ValueError('Browser budget must be between $1 and $20')
                evidence = data.get('evidence', '')
                if not isinstance(evidence, str) or len(evidence.encode()) > 100000:
                    raise ValueError('Evidence must be text of at most 100 KB')
            except (ValueError, TypeError, AttributeError):
                return self.response(400, {'error': 'Check your key, SEC contact email, ticker, research mode, profile, budget ($1–$20), and evidence (100 KB maximum)'})
            with lock:
                if not state['done']:
                    return self.response(409, {'error': 'A run is already active'})
                state.clear()
                state.update(done=False, message='Retrieving evidence…', mode=mode, profile=profile)
                active['folder'] = None

            def update(message):
                with lock:
                    state['message'] = message

            def work():
                try:
                    if mode == 'full':
                        from .pipeline import run as runner
                    else:
                        runner = run
                    with sec_contact_scope(contact or configured_contact):
                        folder = Path(runner(ticker, key, profile=profile, budget=budget, directory=directory,
                                             evidence=evidence, progress=update))
                    result = _result_metadata(folder,full=mode=='full')
                    memo_text = (folder / 'memo.md').read_text(encoding='utf-8')
                    with lock:
                        active['folder'] = folder
                        state.update(done=True, message=f'Saved to {folder}', memo=memo_text, **result)
                except (ValueError, RuntimeError) as exc:
                    paused = getattr(exc,'folder',None)
                    result = _result_metadata(Path(paused),full=mode=='full') if paused else {}
                    with lock:
                        if paused:
                            active['folder']=Path(paused)
                        state.update(done=True, message=f'Run stopped: {exc}. Completed stages are saved.',**result)
                except Exception:
                    with lock:
                        state.update(done=True, message='Run stopped due to an unexpected response. Completed stages are saved; use the CLI for details.')

            threading.Thread(target=work, daemon=True).start()
            self.response(202, {'accepted': True})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    actual_port = server.server_address[1]
    allowed.update({f'127.0.0.1:{actual_port}', f'localhost:{actual_port}'})
    return server


def serve(port=8765, directory='research-output', default_mode='full', *, sec_contact=None):
    server = create_server(port, directory, default_mode,sec_contact=sec_contact)
    print(f'InvestmentIdeas: http://127.0.0.1:{server.server_address[1]} — press Ctrl+C to stop', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

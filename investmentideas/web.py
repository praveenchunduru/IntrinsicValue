"""Loopback-only interface. Keys remain in memory, never browser storage or files."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import secrets
import threading

from .core import run

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
<label for="ticker">Ticker</label><input id="ticker" required value="AAPL" maxlength="15">
<label for="mode">Research depth</label><select id="mode"><option value="full" __FULL_SELECTED__>Full memo — research, editable valuation, review and revision</option><option value="quick" __QUICK_SELECTED__>Quick financial overview — four model calls</option></select>
<label for="profile">Model profile</label><select id="profile"><option value="balanced">Balanced — Qwen extraction, Claude Sonnet analysis</option><option value="economy">Economy — Qwen extraction, DeepSeek analysis</option></select>
<label for="budget">Run budget estimate (USD)</label><input id="budget" type="number" min="1" max="20" step="0.01" value="2.00" required>
<small>Requests stop before the next estimated call would exceed this budget. Also set a provider credit limit on your dedicated key.</small>
<details><summary>Additional evidence</summary><label for="evidence">Optional dated source text or filing excerpt</label><textarea id="evidence" rows="5" maxlength="100000" placeholder="Include source URLs, dates and units. Useful if public sources are unavailable."></textarea></details>
<button id="start">Generate research draft</button></form>
<p id="status" role="status" aria-live="polite">Ready. Completed stages are saved locally and can be resumed.</p>
<p id="summary" hidden></p><div id="downloads" aria-label="Download research files"></div><p id="revalue" hidden>Edit the downloaded assumptions, then recompute without model charges:<br><code>python -m investmentideas --revalue assumptions.json --output revalued</code></p><pre id="memo" hidden></pre>
<small>Model review does not establish investment truth. Inspect the evidence, assumptions and outstanding findings before using a draft.</small>
<script>
const csrf='__TOKEN__';let polling;
const form=document.getElementById('form'),start=document.getElementById('start'),memo=document.getElementById('memo');
const keyInput=document.getElementById('key'),summary=document.getElementById('summary'),downloads=document.getElementById('downloads'),revalue=document.getElementById('revalue');
const ticker=document.getElementById('ticker'),mode=document.getElementById('mode'),profile=document.getElementById('profile'),budget=document.getElementById('budget'),evidence=document.getElementById('evidence');
form.addEventListener('submit',async(e)=>{e.preventDefault();clearTimeout(polling);start.disabled=true;memo.hidden=true;summary.hidden=true;revalue.hidden=true;downloads.replaceChildren();statusText('Starting…');
const payload={key:keyInput.value,ticker:ticker.value,mode:mode.value,profile:profile.value,budget:Number(budget.value),evidence:evidence.value};keyInput.value='';
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


def _result_metadata(folder):
    result = {'downloads': _available_files(folder)}
    try:
        path = folder / 'state.json'
        if path.stat().st_size > 10_000_000:
            return result
        saved = json.loads(path.read_text(encoding='utf-8'))
        ledger = saved.get('ledger', [])
        costs = [row.get('charged_or_reserved_usd') for row in ledger if isinstance(row, dict)]
        if costs and all(isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) and c >= 0 for c in costs):
            result['cost_usd'] = sum(costs)
        iteration = saved.get('iteration')
        if isinstance(iteration, int) and not isinstance(iteration, bool) and iteration >= 0:
            result['iterations'] = iteration
        quality = saved.get('quality_status')
        if quality in {'reviewed_draft', 'needs_review', 'unresolved_findings', 'budget_paused', 'missing_valuation'}:
            result['quality_status'] = quality
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return result


def create_server(port=8765, directory='research-output', default_mode='full'):
    """Create a loopback server; useful for embedding and offline HTTP tests."""
    if default_mode not in {'full', 'quick'}:
        raise ValueError('Unknown research mode')
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
                page = PAGE.replace('__TOKEN__', token).replace('__FULL_SELECTED__', 'selected' if default_mode == 'full' else '').replace('__QUICK_SELECTED__', 'selected' if default_mode == 'quick' else '')
                return self.response(200, page, html=True)
            if self.path == '/status' and self.authenticated():
                with lock:
                    snapshot = dict(state)
                return self.response(200, snapshot)
            if self.path.startswith('/download/'):
                if not self.authenticated():
                    return self.response(403, {'error': 'Invalid local request'})
                name = self.path[len('/download/'):]
                if name not in DOWNLOADS:
                    return self.response(404, {'error': 'Download not found'})
                with lock:
                    folder = active['folder']
                if folder is None or name not in _available_files(folder):
                    return self.response(404, {'error': 'Download not found'})
                path = folder / name
                try:
                    if path.stat().st_size > 10_000_000:
                        return self.response(413, {'error': 'File exceeds the browser download limit; open the local run folder'})
                    return self.response(200, path.read_bytes(), content_type=DOWNLOADS[name], filename=name)
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
                from .core import symbol, MODELS
                ticker = symbol(data.get('ticker', ''))
                mode = data.get('mode', default_mode)
                if mode not in {'full', 'quick'}:
                    raise ValueError('Invalid research mode')
                profile = data.get('profile', 'balanced')
                if profile not in MODELS:
                    raise ValueError('Invalid model profile')
                budget = float(data.get('budget', 2))
                if not math.isfinite(budget) or not 1 <= budget <= 20:
                    raise ValueError('Browser budget must be between $1 and $20')
                evidence = data.get('evidence', '')
                if not isinstance(evidence, str) or len(evidence.encode()) > 100000:
                    raise ValueError('Evidence must be text of at most 100 KB')
            except (ValueError, TypeError, AttributeError):
                return self.response(400, {'error': 'Check your key, ticker, research mode, profile, budget ($1–$20), and evidence (100 KB maximum)'})
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
                    folder = Path(runner(ticker, key, profile=profile, budget=budget, directory=directory,
                                         evidence=evidence, progress=update))
                    result = _result_metadata(folder)
                    memo_text = (folder / 'memo.md').read_text(encoding='utf-8')
                    with lock:
                        active['folder'] = folder
                        state.update(done=True, message=f'Saved to {folder}', memo=memo_text, **result)
                except (ValueError, RuntimeError) as exc:
                    with lock:
                        state.update(done=True, message=f'Run stopped: {exc}. Completed stages are saved.')
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


def serve(port=8765, directory='research-output', default_mode='full'):
    server = create_server(port, directory, default_mode)
    print(f'InvestmentIdeas: http://127.0.0.1:{server.server_address[1]} — press Ctrl+C to stop', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

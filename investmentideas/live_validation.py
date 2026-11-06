"""Explicitly invoked live validation using a capped key; never run in normal CI.

GitHub Actions supplies the encrypted secret directly to this process. This module
never prints or writes credentials, provider error bodies, or full key metadata.
"""
import json
import math
import os
from pathlib import Path
import sys

from .core import BASE, request_json, run, symbol


def check_key(key, snapshot=False):
    if not isinstance(key, str) or not key.strip():
        raise ValueError('Add the OPENROUTER_API_KEY repository Actions secret first')
    data = request_json(BASE + '/auth/key', key=key)['data']
    limit = data.get('limit')
    if (isinstance(limit, bool) or not isinstance(limit, (int, float)) or
            not math.isfinite(limit) or not 0 < limit <= 20 or data.get('limit_reset')):
        raise ValueError('Use a dedicated OpenRouter key with a non-resetting credit limit of at most $20')
    remaining = data.get('limit_remaining')
    if isinstance(remaining, (int, float)) and remaining <= 0:
        raise ValueError('The OpenRouter key has no remaining credit allowance')
    if snapshot:
        return {name:value for name in ('limit','limit_remaining','usage')
                if isinstance((value:=data.get(name)),(int,float)) and not isinstance(value,bool) and math.isfinite(value)}
    return limit



def validation_budget(value):
    if isinstance(value,bool):
        raise ValueError('Validation budget must be a finite number from $1 to $20')
    try:
        budget=float(value)
    except (TypeError,ValueError):
        raise ValueError('Validation budget must be a finite number from $1 to $20') from None
    if not math.isfinite(budget) or not 1 <= budget <= 20:
        raise ValueError('Validation budget must be a finite number from $1 to $20')
    return budget


def read_repository_json(name,max_bytes):
    selected=Path(name).resolve()
    if not selected.is_relative_to(Path.cwd().resolve()) or selected.stat().st_size>max_bytes:
        raise ValueError('Validation input must be inside the repository and within its size limit')
    return json.loads(selected.read_text(encoding='utf-8'))


def validation_sources(value):
    """Route a legacy supplement list or a complete dated research snapshot.

    The v2 envelope preserves archived source IDs and the supplied forecast plan;
    neither form carries review state or changes the pipeline's review gate.
    """
    snapshot=isinstance(value,dict)
    if snapshot:
        if value.get('format')!='intrinsicvalue_research_snapshot_v2':
            raise ValueError('Source snapshot format must be intrinsicvalue_research_snapshot_v2')
        sources=value.get('sources')
        forecast=value.get('forecast_plan')
        if not isinstance(forecast,dict):
            raise ValueError('Source snapshot forecast_plan must be a JSON object within 150 KB')
        try:
            if len(json.dumps(forecast,allow_nan=False).encode())>150000:
                raise ValueError('Source snapshot forecast_plan must be a JSON object within 150 KB')
        except (TypeError,ValueError):
            raise ValueError('Source snapshot forecast_plan must be a finite JSON object within 150 KB') from None
    else:
        sources=value
    maximum=80 if snapshot else 20
    minimum=1 if snapshot else 0
    if not isinstance(sources,list) or not minimum<=len(sources)<=maximum:
        raise ValueError('Source snapshot requires 1–80 sources' if snapshot else
                         'Supplemental sources must be a JSON list of at most 20 sources / 3 MB')
    try:
        if len(json.dumps(sources,allow_nan=False).encode())>3000000:
            raise ValueError('Sources exceed 3 MB')
    except (TypeError,ValueError):
        raise ValueError('Validation sources must be finite JSON within 3 MB') from None
    for index,source in enumerate(sources,1):
        if not isinstance(source,dict) or not all(field in source for field in ('title','kind','data')):
            raise ValueError('Each validation source requires title, kind and data')
        if snapshot and source.get('id')!=f'S{index}':
            raise ValueError('Source snapshot records require consecutive S1… IDs')
    if snapshot:
        return {'source_snapshot':sources,'forecast_plan':forecast}
    return {'extra_sources':sources}


def main():
    mode = os.environ.get('VALIDATION_MODE', 'credentials')
    if mode not in {'credentials', 'economy', 'balanced', 'full', 'full_economy'}:
        raise ValueError('Choose credentials, economy, balanced, full or full_economy explicitly')
    ticker = symbol(os.environ.get('VALIDATION_TICKER', 'AAPL'))
    key = os.environ.get('OPENROUTER_API_KEY', '').strip()
    before=check_key(key,snapshot=True)
    print('OpenRouter accepted the key; its configured non-resetting limit is at most $20.', flush=True)
    if mode == 'credentials':
        print('Credential check complete. No metered generation requested.', flush=True)
        return
    # Fresh job-specific output prevents accidental reuse across validation runs.
    try:
        if mode in {'full','full_economy'}:
            from .pipeline import run as full_run
            limit=int(os.environ.get('VALIDATION_MAX_REVISIONS','3'))
            notes=os.environ.get('VALIDATION_REVIEW_NOTES','')
            source_file=os.environ.get('VALIDATION_SOURCE_FILE','')
            source_inputs={}
            if source_file:
                source_inputs=validation_sources(read_repository_json(source_file,3000000))
            counterfactual_file=os.environ.get('VALIDATION_COUNTERFACTUALS_FILE','')
            counterfactuals=read_repository_json(counterfactual_file,64000) if counterfactual_file else None
            budget=validation_budget(os.environ.get('VALIDATION_BUDGET','8'))
            draft_file=os.environ.get('VALIDATION_DRAFT_FILE','')
            draft=None
            if draft_file:
                selected=Path(draft_file).resolve()
                if not selected.is_relative_to(Path.cwd().resolve()) or selected.stat().st_size>150000:
                    raise ValueError('Draft must be inside the repository and at most 150 KB')
                draft=selected.read_text(encoding='utf-8')
            folder=full_run(ticker,key,profile='balanced' if mode=='full' else 'economy',budget=budget,
                            directory='research-output',progress=lambda text:print(text,flush=True),
                            max_revisions=limit,review_notes=notes,counterfactuals=counterfactuals,draft=draft,review_model=os.environ.get('VALIDATION_REVIEW_MODEL') or None,
                            **source_inputs)
        else:
            folder = run(ticker, key, profile=mode, budget=1.0, directory='research-output', progress=lambda text:print(text,flush=True))
    finally:
        try:
            after=check_key(key,snapshot=True)
        except (RuntimeError,ValueError,KeyError,TypeError):
            after={}
        account={'before':before,'after':after,
                 'note':'Numeric metadata only. Key usage counters can lag the request ledger; the delta can also include other use of this key during the run.'}
        if 'usage' in before and 'usage' in after and after['usage']>=before['usage']:
            account['provider_key_usage_delta_usd']=after['usage']-before['usage']
            print(f"Provider key usage delta: ${account['provider_key_usage_delta_usd']:.8f}",flush=True)
        root=Path('research-output');root.mkdir(exist_ok=True)
        from .core import save
        save(root/'account-usage.json',account)
    ledger = json.loads((folder / 'usage.json').read_text())
    total = sum(row['charged_or_reserved_usd'] for row in ledger)
    result = f'{ticker} / {mode}: {len(ledger)} requests, ${total:.6f} reported or reserved. Draft: {folder / "memo.md"}'
    print(result)
    state=json.loads((folder/'state.json').read_text())
    if mode in {'full','full_economy'}:
        print('Full memo status:',state.get('quality_status'),'revision',state.get('iteration'))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with Path(summary).open('a', encoding='utf-8') as stream:
            stream.write(result + '\n')
    if mode in {'full','full_economy'} and not state.get('complete'):
        raise RuntimeError('The full memo retains material review findings; inspect the archived report and resume with corrections')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, KeyError, TypeError, OSError) as exc:
        print(f'Validation stopped: {exc}', file=sys.stderr)
        sys.exit(1)

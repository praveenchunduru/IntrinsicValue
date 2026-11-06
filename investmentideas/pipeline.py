"""Full portable research: archived evidence, editable DCF, bounded review/revision.

The model proposes forecasts; Python evaluates the valuation. Model reviews are
recorded assessments, never the original system's signed publication authority.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import copy
import json
import math
import os
from pathlib import Path
import re
import shutil
import time
from urllib.request import Request, urlopen

from . import core
from .costs import cost_summary
from .analytics import annual_metrics
from .fundamentals import yahoo_financials
from .inputs import canonical_reported_inputs, pin_reported_inputs
from .quality import DIAGNOSTIC_SCOPE, claim_ledger, inspect_memo, parse_json, parse_review
from .research import MAX_DOCUMENT_BYTES, gather_primary, inline_facts, archive_focus_excerpts
from .macro import gather_us_calibration
from .market_beta import gather_weekly_beta
from .prompt_context import valuation_context as render_valuation_context
from .primary_debt import classify_primary_debt
from .valuation import evaluate, materialize, save_valuation

VERSION = 'full-2'
DEFAULT_BUDGET = 10.0
SECTIONS = '''Executive valuation conclusion; variant thesis and what the market price implies;
business economics and industry/competition; management and capital allocation; historical
financial quality; operating forecast and valuation; bear/base/bull scenarios; catalysts;
risks and disconfirming evidence; evidence gaps and unresolved questions.'''
FORECAST_SCHEMA = """Return ONLY compact JSON for house forecasts:
{"assumptions_label":"House forecasts, not reported facts or guidance",
 "scenario_rationales":{"bear":"Evidence and economic mechanism [S#]","base":"...","bull":"..."},
 "scenarios":{"bear":{"probability":0.25,"growth_rates":[0.02,0.02,0.02,0.02,0.02],
 "ebit_margins":[0.15,0.15,0.15,0.15,0.15],"tax_rate":0.25,"sales_to_capital":2,
 "wacc":0.10,"terminal_growth":0.02,"terminal_roic":0.12},
 "base":{"probability":0.50,"growth_rates":[0.04,0.04,0.04,0.04,0.04],
 "ebit_margins":[0.20,0.20,0.20,0.20,0.20],"tax_rate":0.25,"sales_to_capital":2.5,
 "wacc":0.09,"terminal_growth":0.025,"terminal_roic":0.15},
 "bull":{"probability":0.25,"growth_rates":[0.06,0.06,0.06,0.06,0.06],
 "ebit_margins":[0.25,0.25,0.25,0.25,0.25],"tax_rate":0.25,"sales_to_capital":3,
 "wacc":0.08,"terminal_growth":0.03,"terminal_roic":0.20}}}.
These numbers demonstrate the schema; they are not company assumptions to copy.
Use 5–10 forecast years, the same horizon in all scenarios. Probabilities sum to 1;
WACC exceeds terminal growth. All rates are decimals, not percentage integers.
Keep each rationale under 100 words. Do not reproduce historical actuals, cash/debt
reconciliations, source passages, calculated cashflows or valuation targets: Python
locks reported inputs and independently computes the outputs.
The unlevered operating-company model uses GAAP EBIT less operating taxes and
incremental revenue / sales-to-capital reinvestment. Maintenance investment offsets
D&A; GAAP EBIT already expenses SBC, R&D and operating rent. Do not deduct those
again. Explicitly justify growth/margin fade, capital efficiency, tax, WACC,
terminal growth and terminal ROIC. Terminal reinvestment is g/ROIC of NOPAT;
explain a material forecast-to-terminal capital-intensity transition.
Optional counterfactuals map names to base-scenario forecast overrides (the same horizon).
They are separate diagnostic cases, never probability weights or reported facts; Python computes
their values. A plausible competing market-pricing case can be included without claiming its
value before calculation. Never invent calculated sensitivities absent the computed output.
For banks, insurers, funds or liquidation cases, return {"unavailable":true,
"missing_inputs":["appropriate sector model"],"reason":"..."} instead of forcing FCFF.
"""
UNDERWRITING_GUIDANCE = '''When reliable category revenue is supplied, build a company-specific
operating_model instead of guessing consolidated growth/margins. Its shape is
{"currency":"USD","amount_unit":"currency_units","assumptions_label":"Forward arrays are house assumptions",
"earnings_mode":"gross_profit_less_expenses","components":[{"name":"products","base_revenue":700,
"source_refs":["S1"]},{"name":"services","base_revenue":300,"source_refs":["S1"]}],
"scenarios":{"bear":{"components":{"products":{"growth_rates":[0.02,0.02,0.02,0.02,0.02],
"gross_margins":[0.4,0.4,0.4,0.4,0.4]},"services":{"growth_rates":[0.08,0.08,0.08,0.08,0.08],
"gross_margins":[0.7,0.7,0.7,0.7,0.7]}},"expenses":{"research_and_development":[80,85,90,95,100],
"selling_general_administrative":[40,42,44,46,48]}},"base":"same complete object structure",
"bull":"same complete object structure"}}. Example amounts are not issuer forecasts.
Starting category revenue must reconcile exactly to reported total revenue. Gross margin arrays
are forward assumptions; never call unreported individual category margins observed facts.
Use aggregate_ebit_margins mode without gross margins/expenses if only category revenues are supported.
Omit main scenario growth_rates and, in gross mode, ebit_margins: Python derives them.
If complete dated market inputs are available, provide discount_rate using the documented
rates.py schema: matched risk_free_rate/equity_risk_premium observations, explicit unlevered_beta,
market_equity, financial_debt, finance_lease_debt, pretax_cost_of_debt, marginal_tax_rate.
Every record needs value, observed_at, source_refs and label; currency/amount_unit match the plan,
as_of is the valuation date, lease_convention is gaap_operating. Omit main WACC when this
calculation governs. Label sector/yield proxies and differing dates; do not invent a calibration.
Respect the pinned bridge's finance-lease allocation. If a combined debt subtotal includes
finance leases and their separate amount is known, financial_debt equals combined debt less
that amount and finance_lease_debt equals the same reported lease amount, counted once.
If the split is unknown, use financial_debt at the combined amount with
allocation:"combined_debt_including_finance_leases" and a label explicitly naming combined debt
and leases; finance_lease_debt then has value:0, allocation:"included_in_financial_debt" and
a label explicitly saying zero is an allocation within the combined amount, not an absent liability.
Both financing records use the same annual balance-sheet measurement date, currency and source
support. Do not invent a separate lease amount or add leases twice to the canonical bridge.
If completed-week issuer/benchmark evidence supports a historical equity beta, the unlevered_beta
record may instead omit value and include calculation:"historical_issuer_cash_corrected" and
historical_equity_beta:<measured value>, with observed_at, source_refs and label retained.
Python uses the explicit nonoperating cash bridge and dated financing inputs to derive the
operating-asset proxy. Adopting it for future risk is a house judgment, not a future measurement.
Adjudicate issuer-history window, sector mapping and normalization alternatives when rate choice
drives the conclusion; an exact formula alone does not justify the selected forward-risk input.
Optional calibration_comparisons is a list of name, label and overrides, with each override a
complete dated discount-rate input record. At most eight cases; replace Treasury and ERP together.
These keep base operating cash flows fixed and are not probability-weighted scenarios.
Without supported category/rate data retain an explicit consolidated house model and identify the gap.
Optional counterfactuals may use operating_overrides with existing component growth_rates/gross_margins
or existing expense arrays. Never mix these with aggregate growth/EBIT overrides. Maximum eight cases.
Optional monitoring is a list of name, observation, review_by(YYYY-MM-DD), threshold,
source_refs and exactly one of counterfactual(name) or calibration_comparison(name).
Python supplies each conditional value and change.
These are analyst-selected decision thresholds, not company guidance or automatic forecasts.
Justify reinvestment and terminal returns; avoid a hidden terminal cash discontinuity.
For a meaningful positive valuation, select an explicit conditional research entry rule:
"entry_policy":{"basis":"base"|"weighted","margin_of_safety":<decimal from zero to below one>,
"label":"House valuation entry rule","rationale":"Why this valuation basis and discount are appropriate"}.
These four fields are exact. Python computes reference value * (1 - margin_of_safety).
Choose and justify the discount; zero is allowed and no 20% or other policy is supplied by default.
This is a conditional valuation threshold, not personal trading, sizing or tax advice.
Optional "repurchase_tests":[{"name":"historical_price_comparison","amount":<whole currency units>,
"price":<currency per ordinary share>,"source_refs":["S1"],"label":"Exact period/definition and conditional scope",
"interpretation":"historical_retrospective_conditional"|"forward_conditional_illustration"}].
At most eight independent cases. Names are unique 1-64 character identifiers. Each case requires
all six fields, a positive amount/price, spending within the pinned selected nonoperating cash,
and a positive remaining ordinary-share count. Python holds operating value fixed, reduces cash
and shares once, and compares each scenario/weighted per-share value. Use a sourced, same-basis
historical repurchase measure only when available; never divide mismatched cash-flow payments
by a different disclosure's share count. A historical-price comparison uses today's house value,
not historical fair value or a measured historical loss. A forward illustration is not a forecast.
Do not fund repurchases with future FCFF while also paying that FCFF to owners in a return example.
For material nonrecurring margin items, inventory/procurement and cash conversion, trace
source/period/definition -> reported versus normalized baseline -> explicit house adjustment
-> changed forecast driver. Use named counterfactual overrides for Python to calculate effects;
never invent adjustment amounts or valuation effects. The sales-to-capital baseline already
includes operating working capital; do not add the same inventory release or procurement cost twice.
For each load-bearing disagreement, connect comparable outside expectations, the house array,
economic evidence/counterevidence and the observation that would change it. If no differentiated
operating view is supported, say so and defend the actual rate/terminal/valuation disagreement.
Represent a sourced strongest opposing economic case coherently; a broker target alone or an
isolated rate substitution does not reconstruct its model. Do not copy issuer-specific examples.
'''
REVIEW_SCHEMA = '''Return ONLY {"verdict":"pass"|"revise","findings":[{"id":"F1",
"severity":"blocker"|"major"|"minor","issue":"precise defect and location",
"source_refs":["S1"],"current_excerpt":"exact quote from the CURRENT draft, at most 1200 characters","fix":"specific correction or evidence needed"}]}.
Findings are unresolved; use revise if any blocker/major exists. Use pass only when no material
finding remains. Do not manufacture defects to fill a quota, and verify every accusation
against the actual draft. Valid source IDs alone do not prove a claim is supported.'''


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _response_json(text):
    """Accept one complete response, or one explicitly delimited JSON block.

    Some models preface their sole JSON block with analysis. Recover that exact
    block without changing any content or guessing braces. Ambiguous/multiple
    blocks, truncated JSON, duplicate keys and nonfinite numbers still fail.
    The original response remains in the stage checkpoint.
    """
    try:
        return parse_json(text),False
    except ValueError:
        if not isinstance(text,str) or text.count('```')!=2:
            raise
        blocks=re.findall(r'(?m)^```json[ \t]*\r?\n(.*?)\r?\n```[ \t]*$',text,re.S)
        if len(blocks)!=1:
            raise ValueError('Expected exactly one complete JSON proposal') from None
        return parse_json(blocks[0]),True


def _pending_revision_matches(pending, state):
    """A saved prose phase is reusable only for its exact committed inputs."""
    if (not isinstance(pending,dict) or state.get('review_invalidated') or
            not isinstance(state.get('current_memo'),str) or
            not isinstance(pending.get('findings'),list) or
            not isinstance(pending.get('model_response'),dict)):
        return False
    return (pending.get('iteration')==state.get('iteration') and
            pending.get('memo_sha256')==hashlib.sha256(state['current_memo'].encode()).hexdigest() and
            pending.get('plan_sha256')==_digest(state.get('plan')) and
            pending.get('valuation_sha256')==_digest(state.get('valuation')) and
            pending.get('sources_sha256')==_digest(state.get('sources')) and
            pending.get('external_notes_digest')==state.get('external_notes_digest'))


@contextmanager
def _lock(folder):
    """OS releases the lock even after a killed process, preserving safe resume."""
    handle = (folder / '.run.lock').open('a+b')
    handle.seek(0)
    if not handle.read(1):
        handle.write(b'0'); handle.flush()
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError('This research run is already active; wait for it to finish') from None
    try:
        yield
    finally:
        handle.close()


def _prepare_source(source):
    _source_raw_bytes(source)
    source=dict(source)
    raw=source.get('raw_document')
    data=source.get('data',{})
    companyfacts=isinstance(data,dict) and data.get('fact_origin')=='sec_companyfacts'
    if isinstance(raw,str) and not companyfacts:
        facts=inline_facts(raw)
        if facts:
            data=copy.deepcopy(source.get('data',{}))
            if not isinstance(data,dict):
                data={'provided_text':data}
            data['inline_facts']=facts
            data['inline_fact_note']='Selected nondimensional reported facts; dates/units/contexts are distinct. Do not add totals to their components. Nil/unparsed values are unknown, never implicit zeros.'
            if source.get('kind')=='regulatory_primary' or source.get('source_tier')=='regulatory_primary':
                metadata=data.get('metadata',{})
                anchor=metadata.get('reportDate') if isinstance(metadata,dict) else None
                try:
                    valid_anchor=isinstance(anchor,str) and date.fromisoformat(anchor).isoformat()==anchor
                except ValueError:
                    valid_anchor=False
                if valid_anchor:
                    scoped=classify_primary_debt(raw,facts,anchor=anchor)
                    classifications=data.setdefault('reported_input_classifications',{})
                    if not isinstance(classifications,dict):
                        raise ValueError('Primary reported-input classifications must be an object')
                    for name,declaration in scoped['classifications'].items():
                        previous=classifications.get(name)
                        if previous is not None:
                            try:
                                amount=Decimal(str(previous['normalized_decimal']))
                                agrees=(amount.is_finite() and amount==Decimal(declaration['normalized_decimal']) and
                                    previous.get('instant')==declaration['instant'])
                            except (KeyError,TypeError,InvalidOperation):
                                agrees=False
                            if not agrees:
                                raise ValueError('Primary debt table conflicts with the supplied '+name+' classification')
                            # Keep an agreeing explicit user classification and
                            # its provenance; never silently replace it.
                        else:
                            classifications[name]=declaration
                    excerpts=data.setdefault('excerpts',[])
                    if not isinstance(excerpts,list):
                        raise ValueError('Primary source excerpts must be a list')
                    for excerpt in scoped['excerpts']:
                        if excerpt not in excerpts:
                            excerpts.append(excerpt)
                    data['excerpt_chars']=sum(len(e if isinstance(e,str) else e.get('text',''))
                        for e in excerpts if isinstance(e,(str,dict)))
            source['data']=data
    return source


def _rebind_companyfacts_data(data,source_id):
    """Context-table row indices belong to this newly registered source."""
    if not isinstance(data,dict) or data.get('fact_origin')!='sec_companyfacts':
        return data
    data=copy.deepcopy(data)
    for row in data.get('financial_context_table',[]):
        if isinstance(row,dict):
            row['source_refs']=[source_id]
    return data


def _source_raw_bytes(source):
    """Enforce the archive boundary before HTML parsing or source mutation."""
    raw=source.get('raw_document')
    if raw is None:
        return None
    if not isinstance(raw,(str,bytes)):
        raise ValueError('Source raw_document must be text or bytes')
    if len(raw)>MAX_DOCUMENT_BYTES:
        raise ValueError('Source raw_document exceeds the 20 MB archive limit')
    encoded=raw.encode('utf-8') if isinstance(raw,str) else raw
    if len(encoded)>MAX_DOCUMENT_BYTES:
        raise ValueError('Source raw_document exceeds the 20 MB archive limit')
    return encoded


def _register(folder, sources, source, *, _prepared=False):
    raw=_source_raw_bytes(source)
    suffix=source.get('archive_extension','.html')
    if raw is not None and suffix not in {'.html','.txt','.csv','.json','.xlsx','.pdf'}:
        raise ValueError('Unsupported source archive extension')
    source = dict(source) if _prepared else _prepare_source(source)
    source['id'] = f'S{len(sources)+1}'
    source['data']=_rebind_companyfacts_data(source.get('data'),source['id'])
    source.setdefault('ingestion_method','public_http')
    source.setdefault('retrieved_at', datetime.now(timezone.utc).isoformat())
    source.pop('raw_document',None)
    if raw is not None:
        archive = folder / 'sources_raw'
        archive.mkdir(exist_ok=True)
        target = archive / f'{source["id"]}{suffix}'
        target.write_bytes(raw)
        source['archive'] = str(target.relative_to(folder))
        source['archive_sha256'] = hashlib.sha256(raw).hexdigest()
    source['evidence_sha256'] = _digest(source.get('data'))
    sources.append(source)
    return source


_DELIVERED_FILES=('memo.md','assumptions.json','valuation.json','sources.json',
    'cashflows.csv','sensitivity.csv','source-ledger.json','usage.json','cost-summary.json',
    'reported-inputs.json','budget-preflight.json')


def _artifact_file_hash(folder,name):
    target=folder/name
    if target.is_symlink() or not target.resolve().is_relative_to(folder.resolve()):
        return 'unsafe_path'
    if not target.is_file():
        return None
    digest=hashlib.sha256()
    with target.open('rb') as handle:
        for chunk in iter(lambda:handle.read(65536),b''):
            digest.update(chunk)
    return digest.hexdigest()


def _delivered_artifact_hashes(folder,state):
    names=list(_DELIVERED_FILES)+[s['archive'] for s in state['sources'] if s.get('archive')]
    return {name:_artifact_file_hash(folder,name) for name in names}


def _bad_archive_paths(folder,state,hashes):
    broken=[]
    for source in state['sources']:
        name=source.get('archive')
        if name and (hashes.get(name)!=source.get('archive_sha256') or
                hashes.get(name) in {None,'unsafe_path'} or
                (folder/name).stat().st_size>MAX_DOCUMENT_BYTES):
            broken.append(name)
    return broken


def _verify_completed_artifacts(folder,state):
    """Reopen changed downloads; original raw evidence must be restored explicitly."""
    if (not state.get('complete') and not state.get('artifact_integrity_blocked') and
            not any(source.get('archive') for source in state['sources'])):
        return None
    current=_delivered_artifact_hashes(folder,state)
    bound=state.get('completed_artifact_hashes') or state.get('artifact_blocked_observed_hashes')
    broken_archives=_bad_archive_paths(folder,state,current)
    if not state.get('complete') and not state.get('artifact_integrity_blocked') and not broken_archives:
        return None
    changed=([name for name in set(current)|set(bound or {}) if current.get(name)!=(bound or {}).get(name)]
        if bound else list(current))
    if ((state.get('complete') and (changed or not bound or broken_archives)) or
            (state.get('artifact_integrity_blocked') and changed) or
            (broken_archives and not state.get('artifact_integrity_blocked'))):
        event={'changed_files':sorted(set(changed+broken_archives)),
            'reason':('Archived source integrity failed' if broken_archives else
                'Delivered artifact drift' if bound else 'Legacy completion has no exact artifact binding'),
            'previous_hashes':bound,'observed_hashes':current,
            'at':datetime.now(timezone.utc).isoformat()}
        prior=folder/'revisions'/f'artifact-before-{len(state.get("artifact_history",[]))+1}'
        prior.mkdir(parents=True,exist_ok=True)
        core.save(prior/'reviewed-state.json',state)
        for name in current:
            target=folder/name
            if target.is_file() and not target.is_symlink() and target.resolve().is_relative_to(folder.resolve()):
                saved=prior/name
                saved.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(target,saved)
        state.setdefault('artifact_history',[]).append(event)
        core.save(prior/'integrity.json',event)
        state['complete']=False
        state['quality_status']='needs_review'
        state['review_invalidated']=True
        state.pop('pending_revision',None)
        state.pop('completed_artifact_hashes',None)
        state['artifact_review_record']=_digest(event)
        # State contains the reviewed editable proposal and narrative. Preserve
        # external edits above; an intentional edit must be submitted through
        # --forecast-plan/--draft rather than silently treated as assessed.
        if state.get('plan') and state.get('valuation'):
            save_valuation(folder,state['plan'],state['valuation'])
        report=_render(state.get('current_memo',''),state['sources'],state['gaps'],
            state.get('plan',{}),state.get('valuation'),state)
        (folder/'memo.md').write_text(report,encoding='utf-8')
        core.save(folder/'source-ledger.json',claim_ledger(report))
    if broken_archives:
        state['artifact_integrity_blocked']=broken_archives
        error=RuntimeError('Archived source integrity failed: '+', '.join(broken_archives)+
            '. Further paid work is stopped. Restore the original bytes matching sources.json archive_sha256, '
            'or start a fresh research directory. Changed files are preserved under revisions/. '
            'Submit intentional memo/model edits with --draft/--forecast-plan for a new review.')
        error.folder=folder
        return error
    state.pop('artifact_integrity_blocked',None)
    state.pop('artifact_blocked_observed_hashes',None)
    return None



def _refresh_primary_focus(folder,sources):
    """Expose exact relevant passages from checksummed primary archives."""
    changed=False
    root=folder.resolve()
    for source in sources:
        if (source.get('source_tier')!='regulatory_primary' and
                source.get('kind') not in {'sec_filing','regulatory_primary'}):
            continue
        if not source.get('archive'):
            continue
        archive=(folder/source['archive']).resolve()
        if not archive.is_relative_to(root) or not archive.is_file() or archive.stat().st_size>MAX_DOCUMENT_BYTES:
            raise ValueError(f'Primary archive must remain inside the research folder and within {MAX_DOCUMENT_BYTES//1000000} MB')
        raw=archive.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=source.get('archive_sha256'):
            raise ValueError('Primary archive checksum changed; supply a new source instead of editing its archive')
        if isinstance(source.get('data'),dict) and source['data'].get('fact_origin')=='sec_companyfacts':
            continue  # Canonical JSON is audited through its structured facts, not HTML prose windows.
        focus=archive_focus_excerpts(raw.decode('utf-8'))
        if focus and focus!=source.get('primary_focus_excerpts'):
            # Keep derived windows outside source.data so supplemental-source
            # identity remains stable. The review's full-source hash covers them.
            source['primary_focus_excerpts']=focus
            changed=True
    return changed


def _evidence_context(sources):
    # Excerpts are bounded; full originals remain in the source archive. Never
    # pass an entire filing, private environment or credentials into a prompt.
    packed = []
    for source in sources:
        row = {key: source[key] for key in ('id','title','url','kind','source_tier','retrieved_at','primary_focus_excerpts') if key in source}
        data = source.get('data')
        if source.get('kind')=='user_evidence':
            # The public interface already bounds this focused input at 100 KB.
            # Include all accepted text, including corrections near its end.
            row['data']=copy.deepcopy(data)
        elif isinstance(data, dict) and (source.get('kind') in {'financial_timeseries','sec_facts','macro_calibration'} or 'excerpts' in data or 'inline_facts' in data):
            # Preserve all structured financial rows; do not truncate JSON midway.
            row['data'] = copy.deepcopy(data)
            if 'inline_facts' in row['data']:
                keys=('concept','context_ref','instant','start_date','end_date','unit','scale','sign',
                      'normalized_value','normalized_decimal','is_nil','dimensions')
                row['data']['inline_facts']=[{k:fact[k] for k in keys if k in fact}
                    for fact in row['data']['inline_facts']]
        else:
            text = json.dumps(data, ensure_ascii=False)
            row['excerpt'] = text[:14000]
            row['excerpt_truncated'] = len(text) > 14000
        packed.append(row)
    return json.dumps(packed, ensure_ascii=False)


def _workflow_contract():
    """Track mutable prompts and the code that presents/audits their outputs."""
    root=Path(__file__).parent
    return {'system':_digest(core.SYSTEM),
            'forecast':_digest([FORECAST_SCHEMA,UNDERWRITING_GUIDANCE]),
            'review':_digest(REVIEW_SCHEMA), 'sections':_digest(SECTIONS),
            'implementation':_digest({name:hashlib.sha256((root/name).read_bytes()).hexdigest()
                for name in ('pipeline.py','prompt_context.py','quality.py','valuation.py','inputs.py','operating.py','rates.py','primary_debt.py','companyfacts.py')
                if (root/name).is_file()})}


def _refresh_workflow_contract(folder,state):
    contract=_workflow_contract()
    previous=state.get('workflow_contract')
    state['workflow_contract']=contract
    if previous==contract or (previous is None and not state.get('ledger') and not state.get('current_memo')):
        return
    changed=[name for name,value in contract.items() if not previous or previous.get(name)!=value]
    prior=folder/'revisions'/f'contract-before-{len(state.get("contract_history",[]))+1}'
    prior.mkdir(parents=True,exist_ok=True)
    for name in ('memo.md','assumptions.json','valuation.json','cashflows.csv','sensitivity.csv'):
        if (folder/name).exists():
            shutil.copyfile(folder/name,prior/name)
    core.save(prior/'review-history.json',state.get('review_history',[]))
    state.setdefault('contract_history',[]).append({'previous':previous,'current':contract,
        'changed':changed,'at':datetime.now(timezone.utc).isoformat(),
        'policy':'Retain spending and source archives; invalidate affected generated descendants and require a fresh review.'})
    if state.get('complete'):
        state['iteration']+=1
    state['complete']=False
    state['quality_status']='needs_review'
    state['review_invalidated']=True
    state.pop('pending_revision',None)
    # A legacy run has no evidence of which contract produced its forecasts.
    # Preserve the model for inspection and require a fresh review. Known
    # forecast/system changes invalidate outputs authored under those rules.
    if previous and any(name in changed for name in ('system','forecast','implementation')):
        if not state.get('provided_forecast_digest'):
            state.pop('plan',None)
            state.pop('valuation',None)
        if not state.get('provided_draft_digest'):
            state.pop('current_memo',None)
        if 'system' in changed and not all(s.get('ingestion_method')=='provided_dated_snapshot' for s in state['sources']):
            state['web_research_complete']=False
    elif previous and 'sections' in changed and not state.get('provided_draft_digest'):
        state.pop('current_memo',None)
    if not state.get('plan'):
        for name in ('assumptions.json','valuation.json','cashflows.csv','sensitivity.csv'):
            (folder/name).unlink(missing_ok=True)
    if state.get('current_memo'):
        report=_render(state['current_memo'],state['sources'],state['gaps'],
            state.get('plan',{}),state.get('valuation'),state)
    else:
        report='# '+state['ticker']+' — workflow update in progress\n\nChanged research contracts require a fresh draft and review. Prior artifacts remain under revisions/.\n'
    (folder/'memo.md').write_text(report,encoding='utf-8')


def _recover_supplied_evidence(folder,state,evidence):
    """Repair legacy snapshot runs that accepted but omitted this exact input."""
    if (not evidence or not state.get('evidence_collected') or
            any(s.get('kind')=='user_evidence' and s.get('data')==evidence for s in state['sources'])):
        return
    prior=folder/'revisions'/f'evidence-recovery-{len(state.get("evidence_recoveries",[]))+1}'
    prior.mkdir(parents=True,exist_ok=True)
    core.save(prior/'sources.json',state['sources'])
    core.save(prior/'review-history.json',state.get('review_history',[]))
    for name in ('memo.md','assumptions.json','valuation.json','cashflows.csv','sensitivity.csv'):
        if (folder/name).exists():
            shutil.copyfile(folder/name,prior/name)
    source=_register(folder,state['sources'],{'title':'Recovered supplied evidence (verify provenance)',
        'url':None,'kind':'user_evidence','data':evidence,'source_tier':'unverified'})
    state.setdefault('evidence_recoveries',[]).append({'source_id':source['id'],
        'reason':'Accepted evidence was absent from the saved source snapshot',
        'at':datetime.now(timezone.utc).isoformat()})
    if state.get('complete'):
        state['iteration']+=1
    state['complete']=False
    state['quality_status']='needs_review'
    state['review_invalidated']=True
    for name in ('pending_revision','plan','valuation','current_memo'):
        state.pop(name,None)
    for name in ('assumptions.json','valuation.json','cashflows.csv','sensitivity.csv'):
        (folder/name).unlink(missing_ok=True)
    (folder/'memo.md').write_text('# '+state['ticker']+' — supplied evidence recovered\n\nThe saved run omitted accepted evidence. '
        'Its model and memo require a fresh review against the recovered source; prior artifacts remain under revisions/.\n',encoding='utf-8')


def _initial_budget_preflight(router, state, *, ticker, easy, strong, reviewer,
                              forecast_plan=None, draft=None, max_revisions=3):
    """Price known repeated context before the first potentially billed call.

    Future search excerpts, forecasts, derived valuation data and memo prose are
    unknown at this point. This is deliberately a reservation *floor*, not an
    upper bound, token prediction, invoice estimate, or completion guarantee.
    The real request retains its full byte-based reservation in Router.complete.
    """
    context = _evidence_context(state['sources'])
    stages = []
    def add(stage, model, prompt, tokens, web=False):
        stages.append({'stage':stage, 'model':model, 'known_prompt_utf8_bytes':len(prompt.encode()),
                       'max_completion_tokens':tokens, 'web_search':web,
                       'reservation_floor_usd':router.estimate(model,prompt,tokens,web=web)})
    if not state.get('web_research_complete'):
        for lane in ('business','challenge','expectations','claims'):
            add('web_'+lane,easy,f'Research {ticker}: {lane}.',3500,web=True)
    supplied_model = forecast_plan is not None or state.get('plan')
    supplied_memo = draft is not None or state.get('current_memo')
    if not (supplied_model and supplied_memo):
        add('evidence_synthesis',easy,'Build a dated evidence table.\nEvidence:\n'+context,6500)
    if not supplied_model:
        add('valuation_plan',strong,'Build an investment valuation plan.\n'+
            FORECAST_SCHEMA+UNDERWRITING_GUIDANCE+'\nEvidence:\n'+context,12000)
    valuation = ''
    if state.get('valuation') is not None and state.get('plan'):
        valuation = render_valuation_context(state['plan'],state['valuation'])
    if not supplied_memo:
        add('full_draft',strong,'Write a complete investment-committee draft.\nEvidence:\n'+
            context+'\nValuation:\n'+valuation,14000)
    add('review_'+str(state['iteration']),reviewer,'Independently audit the complete memo.\n'+
        REVIEW_SCHEMA+'\nEvidence:\n'+context+'\nValuation:\n'+valuation+
        '\nDraft:\n'+(draft or state.get('current_memo','')),12000)
    floor = sum(row['reservation_floor_usd'] for row in stages)
    suggested=max(DEFAULT_BUDGET,float(math.ceil(floor)))
    return {'scope':'First draft and one review; revisions and format/forecast repairs excluded',
            'basis':'Uncached UTF-8 byte reservations at maximum catalog price tiers and configured output limits. '
                    'Only currently known context is included. Future search results, synthesis, forecasts, '
                    'computed valuation and memo text can increase reservations; this floor is not a completion guarantee or predicted bill.',
            'budget_usd':router.budget, 'known_context_reservation_floor_usd':floor,
            'suggested_total_allowance_usd':suggested if suggested<=100 else None,
            'max_revisions':max_revisions, 'passed':router.budget>=floor, 'stages':stages}


def _fetch_calibration_bytes(url):
    # Only the collector's fixed public URLs reach this helper. Provider keys
    # are never attached to evidence requests.
    request = Request(url, headers={'User-Agent':'IntrinsicValue research (github.com/praveenchunduru/IntrinsicValue)'})
    with urlopen(request, timeout=25) as response:
        raw = response.read(3000001)
    if len(raw) > 3000000:
        raise ValueError('Calibration source exceeds 3 MB')
    return raw


def _lock_plan(plan, sources):
    if not isinstance(plan,dict):
        raise ValueError('Forecast plan must be an object')
    valuation_day = plan.get('valuation_date', datetime.now(timezone.utc).date().isoformat())
    if not isinstance(valuation_day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', valuation_day):
        raise ValueError('valuation_date must be YYYY-MM-DD')
    if date.fromisoformat(valuation_day) > datetime.now(timezone.utc).date():
        raise ValueError('valuation_date cannot be in the future')
    reported=canonical_reported_inputs(sources, as_of=valuation_day)
    if not reported['coverage'].get('complete'):
        raise ValueError('Essential reported inputs lack complete primary coverage')
    proposal=copy.deepcopy(plan)
    # Review dispositions belong to the audit trail, not forecast assumptions.
    # Feeding old allegations back as model data anchors later reviews.
    proposal.pop('finding_dispositions',None)
    proposal['currency']=reported['currency']
    proposal['amount_unit']='currency_units'
    proposal['share_unit']='shares'
    proposal['valuation_date']=valuation_day
    locked,changes=pin_reported_inputs(proposal,sources)
    if 'market_price' not in locked['reported_inputs']:
        locked.pop('market_price',None)
        locked['source_refs'].pop('market_price',None)
        locked.get('input_dates',{}).pop('market_price',None)
    # A stale model-authored reconciliation must not survive outside the pinned
    # numeric bridge. Raw proposals remain in the checkpoint for inspection.
    bridge=locked.get('net_debt_bridge',{})
    lease_allocation=(
        'Finance leases are already included in the selected combined net-debt subtotal and are not deducted again in other claims. '
        if bridge.get('finance_leases_included_in_net_debt') else
        'Reported finance leases are deducted once in other equity claims, outside the selected financial net-debt subtotal. ')
    locked['assumptions_label']=(
        'Growth, margins, taxes, discount rates, capital efficiency, terminal returns and scenario weights '
        'are house forecasts, not reported facts or company guidance. Historical actuals use the '
        'reported-input snapshot. Only the cash and investment assets selected in the pinned bridge are '
        'classified as nonoperating under an explicit house policy. Omitted investment categories are '
        'not reported zeros, and required operating cash is not established as zero. '+lease_allocation+
        'Additional unidentified '
        'claims are assumed zero, not established absent. GAAP EBIT retains operating rent, SBC and R&D. '
        'Annual year-end cash flows are used without an interim balance-sheet roll-forward or stub-period adjustment.')
    return locked,changes


def _validate_plan_sources(plan, sources):
    if not isinstance(plan,dict) or not isinstance(plan.get('source_refs'),dict):
        raise ValueError('Valuation plan and source_refs must be objects')
    known = {s['id'] for s in sources}
    for field, refs in plan['source_refs'].items():
        refs = [refs] if isinstance(refs, str) else refs
        if not isinstance(refs, list):
            raise ValueError(f'Invalid source references for {field}')
        for ref in refs:
            if not isinstance(ref,str) or not ref.strip():
                raise ValueError(f'Invalid source reference for {field}')
            ref = ref.strip().strip('[]')
            if ref.startswith('assumption:') and field == 'other_equity_claims':
                continue
            if ref not in known:
                raise ValueError(f'Unknown source reference for {field}')
    def nested_refs(value, path):
        if isinstance(value, dict):
            for key, entry in value.items():
                if key == 'source_refs':
                    refs = [entry] if isinstance(entry, str) else entry
                    if not isinstance(refs, list) or not refs:
                        raise ValueError(f'Explicit source references required for {path}')
                    for ref in refs:
                        if not isinstance(ref, str) or ref.strip().strip('[]') not in known:
                            raise ValueError(f'Unknown source reference for {path}')
                else:
                    nested_refs(entry, path + '.' + str(key))
        elif isinstance(value, list):
            for index, entry in enumerate(value):
                nested_refs(entry, path + '.' + str(index))
    for field in ('operating_model', 'discount_rate', 'monitoring', 'calibration_comparisons',
                  'counterfactuals','entry_policy','repurchase_tests'):
        if field in plan:
            nested_refs(plan[field], field)
    bridge = plan.get('net_debt_bridge')
    if isinstance(bridge, dict):
        debt, cash = bridge.get('debt'), bridge.get('selected_cash_and_nonoperating_investments')
        if isinstance(debt,(int,float)) and isinstance(cash,(int,float)):
            expected = debt - cash
            if abs(expected - plan['actuals']['net_debt']) > max(1,abs(expected)*1e-8):
                raise ValueError('Net debt does not reconcile to the disclosed debt/cash bridge')


def _computed_exhibits(result,currency):
    """Present saved diagnostic outputs without repeating valuation arithmetic."""
    diagnostics=result.get('computed_diagnostics',{})
    clean=lambda value:str(value).replace('|','\\|').replace('\n',' ')
    def multiple(value):
        return f'{value["value"]:.2f}×' if value.get('value') is not None else clean(value.get('status','unavailable')).replace('_',' ')
    def returns(value):
        rates=value.get('annual_return_solutions',[])
        return ', '.join(f'{rate:.2%}' for rate in rates) if rates else clean(value.get('status','unavailable')).replace('_',' ')
    lines=[]
    multiples=diagnostics.get('valuation_multiples')
    if isinstance(multiples,dict):
        lines += ['', '### Computed entry and exit multiples', '',
            '| Scenario / earnings denominator | Market EV / first forecast year | DCF exit EV / final forecast year | Original exit return | Unchanged entry multiple exit return |',
            '|---|---:|---:|---:|---:|']
        for name,row in multiples.get('scenarios',{}).items():
            original=result['scenarios'][name].get('fixed_exit_return_at_market_price',{})
            for metric in ('ebit','nopat'):
                lines.append(f'| {clean(name)} / {metric.upper()} | '
                    f'{multiple(row["forecast_year1"]["market_ev_to_"+metric])} | '
                    f'{multiple(row["original_dcf_exit"]["ev_to_final_forecast_"+metric])} | '
                    f'{returns(original)} | {returns(row["unchanged_entry_multiple_exit_returns"][metric])} |')
        lines += ['', 'Entry earnings use the first explicit forecast year, not trailing earnings; exit earnings use the final explicit year. '
            'The return comparison holds operating cash flows, opening financing/claims and shares fixed and changes only the exit valuation. '
            'All annual FCFF is distributed under the debt/claims-settled diagnostic at integer model years; this is not a common-share payout, '
            'ongoing buyback or stock-price-return forecast. Multiple/return alternatives do not change the house DCF or scenario weights.']
    entry=diagnostics.get('entry_policy')
    if isinstance(entry,dict):
        policy=entry['policy']
        price=entry.get('conditional_entry_price')
        threshold=f'{currency} {price:,.2f}' if price is not None else 'not meaningful for this reference value'
        lines += ['', '### Conditional research entry threshold', '',
            f'{clean(policy["label"])}: **{threshold}**, using {clean(policy["basis"])} value and '
            f'the explicitly selected {policy["margin_of_safety"]:.1%} valuation discount. '+clean(policy['rationale']),
            '', 'This is a conditional house valuation rule, separate from a WACC reset or promised holding-period return. '
                'No discount is supplied by default; this is not personal trading or portfolio advice.']
    repurchases=diagnostics.get('repurchase_tests')
    if isinstance(repurchases,list) and repurchases:
        lines += ['', '### Conditional repurchase economics', '',
            '| Case / scope | Cash spent | Price / share | Shares retired | Weighted value / remaining share | Change from original |',
            '|---|---:|---:|---:|---:|---:|']
        for row in repurchases:
            refs=row['source_refs'] if isinstance(row['source_refs'],list) else [row['source_refs']]
            citations=' '.join('['+ref.strip().strip('[]')+']' for ref in refs)
            lines.append(f'| {clean(row["label"])} / {clean(row["interpretation"]).replace("_"," ")} {citations} | '
                f'{currency} {row["amount"]:,.0f} | {currency} {row["price"]:,.2f} | {row["shares_repurchased"]:,.0f} | '
                f'{currency} {row["post_repurchase_weighted_per_share_value"]:,.2f} | '
                f'{currency} {row["weighted_value_change"]["dollar_delta"]:+,.2f} |')
        lines += ['', 'Each independent case spends the pinned selected nonoperating cash and retires shares once while keeping operating value fixed. '
            'Historical-price tests compare with current underwriting, not historical fair value or an observed historical loss. '
            'Forward illustrations are not repurchase forecasts; the same cash is not also counted as a dividend or future FCFF distribution.']
    return lines


def valuation_section(plan, result):
    if not result:
        return '\n## Executable valuation\n\nNo value target is calculated: essential valuation inputs are unresolved.\n'
    # Reader values use the calculated view; saved assumptions retain the editable
    # component/rate inputs without introducing duplicate derived paths.
    plan, _, _ = materialize(plan)
    currency = result.get('currency', plan['currency'])
    lines = ['\n## Executable valuation', '',
             'The following values are recomputed by Python from the editable assumptions; forecasts are house assumptions.', '',
             '| Scenario | Probability | WACC | Terminal growth | Value per share |',
             '|---|---:|---:|---:|---:|']
    for name in ('bear','base','bull'):
        scenario, inputs = result['scenarios'][name], plan['scenarios'][name]
        lines.append(f'| {name.title()} | {inputs["probability"]:.1%} | {inputs["wacc"]:.1%} | '
                     f'{inputs["terminal_growth"]:.1%} | {currency} {scenario["per_share_value"]:,.2f} |')
    lines += ['',f'Weighted intrinsic value per share: {currency} {result["weighted_per_share_value"]:,.2f}',
              '',f'Forecast basis: {plan["assumptions_label"]}',
              '', 'Amounts use whole currency units and actual share counts. Net debt includes the selected cash deduction once. '
                  'See assumptions.json, cashflows.csv and sensitivity.csv for the inputs and calculations.']
    dates=plan.get('input_dates',{})
    if not isinstance(dates,dict):
        dates={}
    measurements=[]
    for field,label in [('net_debt','net debt'),('other_equity_claims','other claims'),
                        ('shares_outstanding','shares outstanding'),('market_price','regular-session quote')]:
        row=dates.get(field,{})
        when=(row.get('instant') or row.get('observed_at') or row.get('end_date')) if isinstance(row,dict) else None
        measurements.append(label+': '+str(when or 'not supplied'))
    lines += ['', 'Measurement dates — '+ '; '.join(measurements)+'. '
              'Balances are not rolled forward to the quote date; the annual cash-flow model has no stub-period adjustment.']
    cases=result.get('counterfactuals',{})
    if cases:
        lines += ['', '### Conditional counterfactuals', '',
                  'These change specified base-case forecasts only. Reported actuals and the equity bridge stay fixed; '
                  'house probabilities and weighted value do not change. These are computations, not forecasts.', '',
                  '| Case | Value per share | Terminal value / enterprise value |',
                  '|---|---:|---:|']
        for name,case in cases.items():
            fraction=case.get('terminal_value_fraction')
            share=f'{fraction:.1%}' if isinstance(fraction,(int,float)) else 'undefined'
            lines.append(f'| {name.replace("_"," ")} | {currency} {case["per_share_value"]:,.2f} | {share} |')
        lines += ['', 'Exact forecast overrides and fixed-input bases are recorded in assumptions.json and valuation.json.']
    operating=result.get('operating_forecasts')
    if operating:
        lines += ['', '### Calculated operating bridge — base case', '',
                  'Whole currency units. GAAP expense paths retain rent and stock compensation; component margins are forward assumptions.', '',
                  '| Forecast year | Revenue | Gross profit | Operating expenses | EBIT | EBIT margin |',
                  '|---|---:|---:|---:|---:|---:|']
        for row in operating['scenarios']['base']['cashflows']:
            gross=f'{row["gross_profit"]:,.0f}' if row['gross_profit'] is not None else 'not modeled'
            expenses=f'{row["operating_expenses"]:,.0f}' if row['operating_expenses'] is not None else 'not modeled'
            lines.append(f'| {row["year"]} | {row["revenue"]:,.0f} | {gross} | {expenses} | {row["ebit"]:,.0f} | {row["ebit_margin"]:.2%} |')
    calibration=result.get('discount_rate_calculation')
    if calibration:
        lines += ['', '### Dated required-return calculation', '',
                  f'Applied WACC: {calibration["applied_wacc"]:.2%}; cost of equity: {calibration["cost_of_equity"]:.2%}; '
                  f'relevered beta: {calibration["relevered_beta"]:.4f}; debt weight: {calibration["debt_weight"]:.2%}. '
                  'These are calculations from the disclosed dated inputs and proxy choices, not observed issuer required returns.']
    comparisons=result.get('calibration_comparisons',[])
    if comparisons:
        lines += ['', '| Conditional rate method | WACC | Base value/share | Change from base |',
                  '|---|---:|---:|---:|']
        for row in comparisons:
            lines.append(f'| {row["name"].replace("_"," ")} | {row["wacc"]:.2%} | {row["per_share_value"]:,.2f} | {row["value_change"]["dollar_delta"]:+,.2f} |')
        lines += ['', 'Only the named rate inputs change. Historical-fit endpoints are not valuation confidence intervals.']
    monitoring=result.get('monitoring',[])
    if monitoring:
        lines += ['', '### Recomputed observation tests', '',
                  'Deadlines and thresholds are house decision rules, not issuer guidance. A result is conditional on adopting its exact changed inputs; correlated cases are not additive.', '',
                  '| Observation / review deadline | Threshold and conditional input change | Value/share / change from base |',
                  '|---|---|---:|']
        for row in monitoring:
            clean=lambda value:str(value).replace('|','\\|').replace('\n',' ')
            refs=', '.join(row['source_refs']) if isinstance(row['source_refs'],list) else row['source_refs']
            lines.append(f'| {clean(row["name"])} / {row["review_by"]} | {clean(row["threshold"])} [{refs}] | '
                         f'{row["conditional_per_share_value"]:,.2f} / {row["value_change"]["dollar_delta"]:+,.2f} |')
    lines += _computed_exhibits(result,currency)
    return '\n'.join(lines)+'\n'


def _render(memo, sources, gaps, plan, result, state):
    refs = '\n'.join(f'- [{s["id"]}] {s["title"]} — {s.get("url") or "supplied text"}; '
                     f'retrieved {s.get("retrieved_at","not recorded")}' for s in sources)
    unresolved = [f for r in state.get('review_history',[]) for f in r.get('findings',[])]
    # Only the latest revision's unresolved findings apply; prior findings remain
    # in review files for traceability rather than being silently deleted.
    if state.get('review_history'):
        unresolved = state['review_history'][-1].get('findings',[])
    note = '\n'.join(f'- {f["severity"]}: {f["issue"]}' for f in unresolved)
    if not note:
        note = ('No currently recorded material finding. This is a model assessment, not certification.'
                if state.get('complete') else 'Review is incomplete; unresolved issues have not been determined.')
    return (memo.rstrip() + '\n' + valuation_section(plan,result) + '\n## Source register\n'+refs+
            '\n\n## Evidence limitations\n'+ ('\n'.join('- '+g for g in gaps) or 'No retrieval gap recorded; source completeness still requires review.')+
            '\n\n## Review status\n'+state.get('quality_status','in_progress')+
            '\n\n'+note+
            '\n\nThis portable research draft is separate from the OAuth system’s signed publications. '
            'Software checks and model reviews do not establish investment truth or forecast accuracy.\n')


def run(ticker, key, *, profile='balanced', budget=DEFAULT_BUDGET, directory='research-output', evidence='',
        easy_model=None, strong_model=None, progress=lambda message: None, max_revisions=3, review_notes='', extra_sources=None, counterfactuals=None, draft=None, review_model=None,
        source_snapshot=None, forecast_plan=None):
    ticker = core.symbol(ticker)
    if source_snapshot is not None:
        if (not isinstance(source_snapshot, list) or not 1 <= len(source_snapshot) <= 80 or
                len(json.dumps(source_snapshot).encode()) > 3000000):
            raise ValueError('Source snapshot must contain 1–80 source records within 3 MB')
        for index, source in enumerate(source_snapshot, 1):
            if (not isinstance(source, dict) or source.get('id') != f'S{index}' or
                    not all(field in source for field in ('title','kind','data'))):
                raise ValueError('Source snapshot records require consecutive S1… IDs, title, kind and data')
        parse_json(json.dumps(source_snapshot))
    if forecast_plan is not None:
        if not isinstance(forecast_plan, dict) or len(json.dumps(forecast_plan).encode()) > 150000:
            raise ValueError('Provided forecast plan must be a JSON object within 150 KB')
        parse_json(json.dumps(forecast_plan))
    if review_model is not None and (not isinstance(review_model,str) or not review_model.strip() or len(review_model)>200):
        raise ValueError('Review model must be a nonempty OpenRouter model ID of at most 200 characters')
    if draft is not None and (not isinstance(draft,str) or not draft.strip() or len(draft.encode())>150000):
        raise ValueError('Provided draft must be nonempty text of at most 150 KB')
    if counterfactuals is not None:
        if not isinstance(counterfactuals,dict) or len(counterfactuals)>8 or len(json.dumps(counterfactuals).encode())>64000:
            raise ValueError('Counterfactuals must be a JSON object of at most 8 cases / 64 KB')
        parse_json(json.dumps(counterfactuals))
    if extra_sources is not None:
        if not isinstance(extra_sources,list) or len(extra_sources)>20 or len(json.dumps(extra_sources).encode())>3000000:
            raise ValueError('Supplemental sources must be a JSON list of at most 20 sources / 3 MB')
        for source in extra_sources:
            if not isinstance(source,dict) or not all(k in source for k in ('title','kind','data')):
                raise ValueError('Each supplemental source requires title, kind and data')
    if isinstance(max_revisions,bool) or not isinstance(max_revisions,int) or not 0 <= max_revisions <= 8:
        raise ValueError('max_revisions must be an integer from 0 to 8')
    if not isinstance(review_notes,str) or len(review_notes.encode()) > 30000:
        raise ValueError('Review notes must be text of at most 30 KB')
    if profile not in core.MODELS:
        raise ValueError('Unknown profile')
    if not isinstance(evidence,str) or len(evidence.encode()) > 100000:
        raise ValueError('Evidence must be text of at most 100 KB')
    easy,strong=core.MODELS[profile]
    easy,strong=easy_model or easy,strong_model or strong
    identity_parts=[VERSION,ticker,profile,easy,strong,evidence,source_snapshot,datetime.now(timezone.utc).date().isoformat()]
    identity=_digest(identity_parts)[:16]
    base=Path(directory)/ticker
    folder=base/('full-'+identity)
    if not folder.exists():
        # Preserve legacy metered history, without moving a possibly active run.
        legacy=base/('full-'+_digest(identity_parts+[core.SYSTEM])[:16])
        if legacy.is_dir():
            folder=legacy
        elif base.is_dir():
            for marker in sorted(base.glob('full-*/run-identity.json')):
                if json.loads(marker.read_text()).get('id')==identity:
                    folder=marker.parent
                    break
    folder.mkdir(parents=True,exist_ok=True)
    with _lock(folder):
        marker_path=folder/'run-identity.json'
        marker=json.loads(marker_path.read_text()) if marker_path.exists() else {}
        marker['id']=identity
        if folder.name=='full-'+_digest(identity_parts+[core.SYSTEM])[:16]:
            # The legacy folder digest itself establishes which SYSTEM was
            # used by its old stage keys, which did not include that field.
            marker.setdefault('legacy_stage_system_sha256',_digest(core.SYSTEM))
        core.save(marker_path,marker)
        return _run(folder,ticker,key,easy,strong,budget,evidence,progress,max_revisions,review_notes,extra_sources or [],counterfactuals,draft,review_model,source_snapshot,forecast_plan)


def _run(folder,ticker,key,easy,strong,budget,evidence,progress,max_revisions,review_notes,extra_sources,counterfactuals,draft,review_model,source_snapshot=None,forecast_plan=None):
    notes_reopened=False
    state_path=folder/'state.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {
        'version':VERSION,'ticker':ticker,'ledger':[],'stages':{},'sources':[], 'gaps':[],
        'iteration':0,'review_history':[],'quality_status':'in_progress','complete':False}
    marker=json.loads((folder/'run-identity.json').read_text())
    if marker.get('legacy_stage_system_sha256'):
        state['legacy_stage_system_sha256']=marker['legacy_stage_system_sha256']
    def checkpoint():
        core.save(state_path,state)
        core.save(folder/'usage.json',state['ledger'])
        core.save(folder/'cost-summary.json',cost_summary(state['ledger']))
        core.save(folder/'sources.json',state['sources'])
    integrity_error=_verify_completed_artifacts(folder,state)
    if integrity_error:
        checkpoint()
        state['artifact_blocked_observed_hashes']=_delivered_artifact_hashes(folder,state)
        checkpoint()
        raise integrity_error
    _refresh_workflow_contract(folder,state)
    _recover_supplied_evidence(folder,state,evidence)
    checkpoint()
    changed_forecast = forecast_plan is not None and (_digest(forecast_plan) != state.get('provided_forecast_digest') or
        not state.get('plan') or state['plan'].get('unavailable_due_to_coverage'))
    if changed_forecast:
        if state.get('complete'):
            state['iteration'] += 1
            notes_reopened = True
        archive = folder/'revisions'/f'model-before-provided-{len(state.get("forecast_history",[]))+1}'
        archive.mkdir(parents=True, exist_ok=True)
        for name in ('assumptions.json','valuation.json','cashflows.csv','sensitivity.csv'):
            if (folder/name).exists():
                shutil.copyfile(folder/name,archive/name)
        state['complete'] = False
        state['quality_status'] = 'needs_review'
        state['review_invalidated'] = True
        state.pop('pending_revision',None)
        if draft is None:
            state.pop('current_memo',None)
    previous_reviewer=state.get('review_model')
    reviewer=review_model or previous_reviewer or strong
    if reviewer!=state.get('review_model',strong):
        if state.get('complete'):
            state['iteration']+=1
            notes_reopened=True
        state['complete']=False
        state['quality_status']='needs_review'
        state['review_invalidated']=True
        state.pop('pending_revision',None)
    state['review_model']=reviewer
    if reviewer!=previous_reviewer:
        checkpoint()
    if counterfactuals is not None and counterfactuals!=state.get('requested_counterfactuals'):
        if state.get('complete'):
            state['iteration']+=1
            notes_reopened=True
        state['requested_counterfactuals']=copy.deepcopy(counterfactuals)
        if state.get('plan') and not state['plan'].get('unavailable'):
            state['plan']['counterfactuals']=copy.deepcopy(counterfactuals)
            # Validate/evaluate explicit edits before any further paid call.
            state['valuation']=evaluate(state['plan'])
        state['complete']=False
        state['quality_status']='needs_review'
        state['review_invalidated']=True
        state.pop('pending_revision',None)
        checkpoint()
    if draft is not None and (_digest(draft)!=state.get('provided_draft_digest') or not state.get('current_memo')):
        if state.get('complete'):
            state['iteration']+=1
            notes_reopened=True
        previous=state.get('current_memo')
        if previous:
            archive=folder/'revisions';archive.mkdir(exist_ok=True)
            name=f'memo-before-provided-{len(state.get("draft_history",[]))+1}.md'
            (archive/name).write_text(previous,encoding='utf-8')
        state['draft_history']=state.get('draft_history',[])+[{
            'sha256':hashlib.sha256(draft.encode()).hexdigest(),
            'origin':'provided_draft','iteration':state['iteration']}]
        state['provided_draft_digest']=_digest(draft)
        state['current_memo']=draft
        state['complete']=False
        state['quality_status']='needs_review'
        state['review_invalidated']=True
        state.pop('pending_revision',None)
        checkpoint()
    if review_notes and _digest(review_notes) != state.get('external_notes_digest'):
        if state.get('complete'):
            state['iteration'] += 1
            notes_reopened=True
        state['complete']=False
        state['quality_status']='needs_review'
        state['external_notes']=review_notes
        state['external_notes_digest']=_digest(review_notes)
        state['review_invalidated']=True
        state.pop('pending_revision',None)
        checkpoint()
        if state.get('current_memo'):
            (folder/'memo.md').write_text(_render(state['current_memo'],state['sources'],state['gaps'],
                state.get('plan',{}),state.get('valuation'),state),encoding='utf-8')
    if state.get('complete') and not extra_sources:
        progress(f'Using completed research: {folder / "memo.md"}')
        return folder
    if state['iteration'] > max_revisions:
        raise ValueError(f'Increase max_revisions to at least {state["iteration"]} to investigate the new review notes')
    refresh_coverage=bool(state.get('coverage_preflight_failed') and source_snapshot is None and not extra_sources)
    if refresh_coverage:
        state['evidence_collected']=False
        state.setdefault('collection_history',[]).append({'at':datetime.now(timezone.utc).isoformat(),
            'reason':'Retry free collection after incomplete primary inputs',
            'prior_source_ids':[s['id'] for s in state['sources']],'prior_gaps':state['gaps']})
    if not state.get('evidence_collected') and source_snapshot is not None:
        state['sources'] = []
        state['gaps'] = ['This run uses an explicitly supplied dated source snapshot. Evidence and quotes are not refreshed; source support and completeness still require review.']
        for item in source_snapshot:
            item = copy.deepcopy(item)
            # A manifest's original path is not a file available in this new run.
            # Exact excerpts/hashes remain; supplied raw bytes are archived anew.
            if 'raw_document' not in item:
                item.pop('archive',None)
            item['ingestion_method'] = 'provided_dated_snapshot'
            _register(folder,state['sources'],item)
        state['evidence_collected'] = True
        state['web_research_complete'] = True
        checkpoint()
    if not state.get('evidence_collected'):
        progress('Collecting financial facts, primary filings and issuer releases')
        sources,gaps=core.collect(ticker,include_archives=True)
        state['sources'],state['gaps']=(state['sources'] if refresh_coverage else []),gaps
        refreshed=[]
        def ingest(source):
            source=_prepare_source(source)
            content_key=_digest(_rebind_companyfacts_data(source.get('data'),'self'))
            if refresh_coverage and any(old.get('url')==source.get('url') and old.get('kind')==source.get('kind') and
                    old.get('source_tier')==source.get('source_tier') and
                    _digest(_rebind_companyfacts_data(old.get('data'),'self'))==content_key for old in state['sources']):
                return
            refreshed.append(_register(folder,state['sources'],source,_prepared=True)['id'])
        for source in sources:
            ingest(source)
        if not any(s.get('kind')=='financial_timeseries' for s in state['sources']):
            try:
                url,data=yahoo_financials(ticker,core.request_json)
                data['derived_metrics']=annual_metrics(data)
                ingest({'title':'Yahoo annual financial series (secondary)', 'url':url,
                          'kind':'financial_timeseries','data':data,'source_tier':'secondary'})
            except (RuntimeError,ValueError,KeyError,TypeError):
                state['gaps'].append('Supplemental annual financial series unavailable.')
        primary,gaps=gather_primary(ticker,core.request_json)
        state['gaps'].extend(gaps)
        for source in primary:
            ingest(source)
        calibration, calibration_gaps = gather_us_calibration(_fetch_calibration_bytes)
        for source in calibration:
            ingest(source)
        state['gaps'].extend(gap['reason'] for gap in calibration_gaps)
        beta_sources, beta_gaps = gather_weekly_beta(ticker,core.request_json,
            as_of=datetime.now(timezone.utc).date().isoformat())
        for source in beta_sources:
            ingest(source)
        state['gaps'].extend(gap['reason'] for gap in beta_gaps)
        if refresh_coverage and refreshed:
            state.pop('pending_revision',None)
            state['review_invalidated']=True
            if draft is None:
                state.pop('current_memo',None)
            else:
                state['current_memo']=draft
        state['evidence_collected']=True
        checkpoint()
    if evidence and not any(s.get('kind')=='user_evidence' and s.get('data')==evidence for s in state['sources']):
        _register(folder,state['sources'],{'title':'Supplied evidence (verify provenance)','url':None,
            'kind':'user_evidence','data':evidence,'source_tier':'unverified'})
        checkpoint()
    added=[]
    for source in extra_sources:
        source=_prepare_source(source)
        source['ingestion_method']='provided_archive'
        content_key=_digest(_rebind_companyfacts_data(source.get('data'),'self'))
        if any(old.get('url')==source.get('url') and old.get('kind')==source.get('kind') and
               old.get('source_tier')==source.get('source_tier') and
               _digest(_rebind_companyfacts_data(old.get('data'),'self'))==content_key
               for old in state['sources']):
            continue
        added.append(_register(folder,state['sources'],source,_prepared=True)['id'])
    if added:
        state.pop('pending_revision',None)
        old=folder/'revisions'/f'model-before-supplement-{len(state.get("supplement_history",[]))+1}'
        old.mkdir(parents=True,exist_ok=True)
        for name in ['assumptions.json','valuation.json','cashflows.csv','sensitivity.csv']:
            if (folder/name).exists():
                if not (old/name).exists():
                    shutil.copyfile(folder/name,old/name)
                (folder/name).unlink()
        if state.get('current_memo') and not notes_reopened:
            state['iteration']+=1
        state['complete']=False
        state['quality_status']='needs_review'
        if any(src.get('source_tier')=='regulatory_primary' for src in extra_sources):
            state['gaps']=[g.replace('annual and quarterly filings were not reviewed',
                'direct annual/quarterly retrieval failed; subsequently supplied primary archives and excerpts are separately identified') for g in state['gaps']]
        for field in ('plan','valuation','current_memo'):
            state.pop(field,None)
        state.setdefault('supplement_history',[]).append({'source_ids':added,'at':datetime.now(timezone.utc).isoformat()})
        (folder/'memo.md').write_text('# '+ticker+' — evidence update in progress\n\nNew evidence requires a recomputed model and a fresh review. Previous frozen drafts remain under revisions/.\n',encoding='utf-8')
        checkpoint()
    if _refresh_primary_focus(folder,state['sources']):
        if state.get('complete'):
            state['iteration']+=1
        state['complete']=False
        state['quality_status']='needs_review'
        state['review_invalidated']=True
        state.pop('pending_revision',None)
        checkpoint()
    if state.get('complete'):
        return folder
    if state['iteration'] > max_revisions:
        raise ValueError('Increase max_revisions to review the supplemented research snapshot')
    reported=canonical_reported_inputs(state['sources'],as_of=(forecast_plan or state.get('plan',{})).get('valuation_date'))
    core.save(folder/'reported-inputs.json',reported)
    coverage=reported['coverage']
    if coverage.get('conflicts') or not coverage.get('complete'):
        state['coverage_preflight_failed']=True
        prior=folder/'revisions'/'model-before-incomplete-coverage'
        prior.mkdir(parents=True,exist_ok=True)
        for name in ('assumptions.json','valuation.json','cashflows.csv','sensitivity.csv'):
            if (folder/name).exists():
                if not (prior/name).exists():
                    shutil.copyfile(folder/name,prior/name)
                (folder/name).unlink()
        state['plan']={'unavailable':True,'unavailable_due_to_coverage':True,
            'missing_inputs':coverage.get('missing_fields',[]),
            'reason':'Essential reported inputs lack complete unconflicted primary-source coverage'}
        state.pop('valuation',None)
        state['complete']=False
        state['quality_status']='missing_valuation'
        details='; '.join(coverage.get('conflicts',[])) or ', '.join(coverage.get('missing_fields',[]))
        notes='; '.join(coverage.get('notes',[]))
        message='Reported-input preflight stopped before paid research: '+details+'. '+notes
        recovery=('Supply primary disclosures with --sources, or start a fresh non-snapshot run with --sec-contact'
            if source_snapshot is not None else 'Supply primary disclosures with --sources or retry SEC access with --sec-contact')
        (folder/'memo.md').write_text('# '+ticker+' — financial input coverage incomplete\n\n'+message+
            '\n\n'+recovery+'. '
            'No paid research was started for this attempt.\n',encoding='utf-8')
        checkpoint()
        error=RuntimeError(message+' '+recovery+'; no paid requests were started for this attempt')
        error.folder=folder
        raise error
    if state.get('plan',{}).get('unavailable_due_to_coverage'):
        state.pop('plan',None)
    state.pop('coverage_preflight_failed',None)
    if forecast_plan is not None and (changed_forecast or not state.get('plan')):
        supplied,changes=_lock_plan(forecast_plan,state['sources'])
        if 'requested_counterfactuals' in state:
            supplied['counterfactuals']=copy.deepcopy(state['requested_counterfactuals'])
        _validate_plan_sources(supplied,state['sources'])
        state['valuation']=evaluate(supplied)
        state['plan']=supplied
        state['provided_forecast_digest']=_digest(forecast_plan)
        state.setdefault('forecast_history',[]).append({'sha256':_digest(forecast_plan),
            'origin':'provided_forecast','iteration':state['iteration']})
        state.setdefault('actual_input_corrections',[]).extend(changes)
        save_valuation(folder,supplied,state['valuation'])
        checkpoint()
    if state.get('current_memo'):
        pending_report=_render(state['current_memo'],state['sources'],state['gaps'],
            state.get('plan',{}),state.get('valuation'),state)
        (folder/'memo.md').write_text(pending_report,encoding='utf-8')
        core.save(folder/'source-ledger.json',claim_ledger(pending_report))
        checkpoint()
    try:
        router=core.Router(key,budget,state['ledger'],checkpoint)
        router.validate_models((easy,strong,reviewer))
    except (ValueError,RuntimeError) as exc:
        exc.folder=folder
        checkpoint()
        raise
    if not state['ledger']:
        preflight=_initial_budget_preflight(router,state,ticker=ticker,easy=easy,strong=strong,
            reviewer=reviewer,forecast_plan=forecast_plan,draft=draft,max_revisions=max_revisions)
        state['budget_preflight']=preflight
        core.save(folder/'budget-preflight.json',preflight)
        progress(f'Initial budget preflight: ${preflight["known_context_reservation_floor_usd"]:.2f} '
                 f'known-context reservation floor; ${budget:.2f} total allowance. '
                 'Future generated context, repairs and revisions can require more.')
        if not preflight['passed']:
            state['quality_status']='budget_paused'
            checkpoint()
            suggested=preflight['suggested_total_allowance_usd']
            action=(f'Rerun the same settings with --budget {suggested:g} '
                '(planning allowance, not a promised charge or completion), or choose --quick. '
                if suggested is not None else
                'This floor exceeds the CLI $100 allowance limit; choose --profile economy or --quick, or supply focused evidence. ')
            error=core.BudgetExceeded('Initial budget preflight stopped before paid research: '
                f'${budget:.2f} allowance is below the ${preflight["known_context_reservation_floor_usd"]:.2f} '
                'known-context reservation floor for the first draft and review. '
                +action+'No paid requests were started')
            error.folder=folder
            raise error
        if state.get('quality_status')=='budget_paused':
            state['quality_status']='in_progress'
        checkpoint()
    def call(name,model,prompt,tokens=6000,web=False):
        cache=name+':'+_digest([prompt,model,tokens,web,core.SYSTEM])[:12]
        legacy=name+':'+_digest([prompt,model,tokens,web])[:12]
        # The old digest omitted SYSTEM. It is reusable only when the saved
        # contract proves that its system instructions are still identical.
        system_unchanged=state.get('legacy_stage_system_sha256')==_digest(core.SYSTEM)
        if cache not in state['stages'] and legacy in state['stages'] and system_unchanged:
            state['stages'][cache]=state['stages'][legacy]
        if cache in state['stages']:
            return state['stages'][cache]
        progress(name+': '+model)
        try:
            value=router.complete(name,model,prompt,max_tokens=tokens,web=web)
            state['stages'][cache]=value
            return value
        except core.BudgetExceeded as exc:
            state['complete']=False
            state['quality_status']='budget_paused'
            exc.folder=folder
            raise
        finally:
            checkpoint()
    # Searches broaden coverage beyond the issuer's own account. Each call has
    # an explicit plugin fee/context reservation; there is no external API key.
    if not state.get('web_research_complete'):
        for lane,question in [
            ('business','latest annual filing and official earnings releases: segments, revenue/margins, nonrecurring gross-profit benefits or refunds, inventory by type, operating working-capital cash movements and procurement commitments. Preserve exact passages, periods, definitions, reported amounts versus approximations, and which forecast driver each material item affects'),
            ('challenge','strongest credible named outside bull and bear economic arguments, original fund letters/research notes or attributable public reporting, dates, disclosed positions and access scope. When available find a valuation on the other side of the saved quote '+str(reported.get('market_price','unavailable'))+' '+str(reported.get('currency',''))+'; preserve its causal demand/supply/price/mix/margin/capital-allocation case, not just a target. Do not reconstruct an inaccessible broker model'),
            ('expectations','current fiscal-year consensus revenue, margins and earnings, exact fiscal/calendar and accounting basis, analyst counts/ranges and observation dates; compare like periods and definitions. Distinguish already-known costs from a genuine house disagreement, and allow the honest conclusion that no differentiated operating view is supported'),
            ('claims','latest proxy incentives, insider transaction types/dates, equity awards, debt/lease/tax/purchase commitments and actual repurchase prices. Match repurchase dollars and shares within the same disclosure/period; distinguish cash-flow payments, share retirements and EPS accretion from value creation. Identify changed operating drivers or claims without deducting ordinary procurement costs twice')]:
            answer=call('web_'+lane,easy,
                        f'Research {ticker} as of {datetime.now(timezone.utc).date()}: {question}. '
                        'Use the web search results. Identify exact source passages, dates and definitions. '
                        'Separate facts from hypotheses; do not invent source contents.',tokens=3500,web=True)
            row=next(r for r in reversed(state['ledger']) if r['stage']=='web_'+lane and r.get('status')=='complete')
            for annotation in row.get('annotations',[]):
                if not annotation.get('content'):
                    continue
                if any(s.get('url')==annotation['url'] for s in state['sources']):
                    continue
                _register(folder,state['sources'],{'title':annotation['title'] or 'Search source excerpt',
                    'url':annotation['url'],'kind':'search_excerpt','source_tier':'search_excerpt_unverified',
                    'data':{'excerpt':annotation['content'], 'research_lane':lane,
                            'note':'Search-provider excerpt; verify passage context against original before relying on it.'}})
            if not any(a.get('content') for a in row.get('annotations',[])):
                state['gaps'].append(f'{lane} search returned no archived source annotations; generated search prose is not treated as evidence.')
            checkpoint()
        state['web_research_complete']=True
        checkpoint()
    context=_evidence_context(state['sources'])
    gaps=json.dumps(state['gaps'])
    pending=state.get('pending_revision')
    resume_revision=_pending_revision_matches(pending,state)
    if pending and not resume_revision:
        state.pop('pending_revision',None)
        checkpoint()
    # The pending prose phase already has its sources, findings and adjudicated
    # model. Re-synthesizing newly added targeted evidence would repay completed
    # work before resuming the saved revision.
    thesis='' if resume_revision or (draft is not None and (state.get('plan') or forecast_plan is not None)) else call('evidence_synthesis',easy,
                'Build a dated evidence table and a decision-focused issue tree for this equity valuation. '
                'Separate reported facts, house assumptions, competing explanations, and missing evidence. '
                'Trace source IDs [S#]. Explain where management economics may differ from accounting; '
                'prioritize questions that can change intrinsic value. Keep the synthesis to 800–1200 words; '
                'select at most ten load-bearing facts, do not reproduce every raw time-series row. '
                'For material margin one-offs/refunds, inventory/procurement and repurchases, preserve source, '
                'period, definition, amount/approximation and the affected driver. Separate reported and '
                'normalized baselines; trace each proposed house adjustment to a forecast assumption without '
                'counting working capital or operating costs twice. Build comparable Street rows and the '
                'strongest available opposing economic case, preferably across the saved quote. Connect '
                'outside expectation -> house assumption -> evidence/counterevidence -> changed driver -> '
                'a proposed counterfactual for Python to value. Do not invent the value before calculation. '
                'If operating expectations are near consensus, say so and identify the actual rate, terminal '
                'or capitalization disagreement instead of manufacturing contrarianism. '
                'List the five highest-impact uncertainties.\nEvidence:\n'+context,tokens=6500)
    if 'plan' not in state:
        prompt=('Build an investment valuation plan for '+ticker+'. Ground actuals in supplied evidence; '
                'use primary reports where accessible, and reconcile secondary-series dates and definitions.\n'+
                FORECAST_SCHEMA+UNDERWRITING_GUIDANCE+'\nIMMUTABLE reported inputs (copy these values exactly; forecast judgments only):\n'+json.dumps(reported)+'\nEvidence:\n'+context+'\nDiligence synthesis:\n'+thesis+'\nRetrieval gaps:\n'+gaps)
        text=call('valuation_plan',strong,prompt,tokens=12000)
        for repair in range(2):
            try:
                plan=parse_json(text)
                if not isinstance(plan,dict):
                    raise ValueError('Valuation plan must be an object')
                if plan.get('unavailable'):
                    state['valuation_missing']=plan
                    state['plan']=plan
                    break
                plan,changes=_lock_plan(plan,state['sources'])
                if not plan['reported_inputs']['coverage'].get('complete'):
                    raise ValueError('Reported-input coverage is incomplete')
                state.setdefault('actual_input_corrections',[]).extend(changes)
                _validate_plan_sources(plan,state['sources'])
                if 'requested_counterfactuals' in state:
                    plan['counterfactuals']=copy.deepcopy(state['requested_counterfactuals'])
                result=evaluate(plan)
                state['plan'],state['valuation']=plan,result
                save_valuation(folder,plan,result)
                break
            except (ValueError,KeyError,TypeError) as exc:
                if repair:
                    raise RuntimeError('Valuation plan remains invalid after one repair: '+str(exc)) from None
                text=call('valuation_plan_repair',strong,prompt+'\nRejected JSON:\n'+text+
                          '\nRepair this precise validation failure without inventing actuals: '+str(exc),tokens=12000)
        checkpoint()
    if not state['plan'].get('unavailable'):
        state['plan'],changes=_lock_plan(state['plan'],state['sources'])
        state.setdefault('actual_input_corrections',[]).extend(changes)
        if 'requested_counterfactuals' in state:
            state['plan']['counterfactuals']=copy.deepcopy(state['requested_counterfactuals'])
        state['valuation']=evaluate(state['plan'])
        save_valuation(folder,state['plan'],state['valuation'])
    plan,result=state['plan'],state.get('valuation')
    valuation_context=(render_valuation_context(plan,result) if result is not None else
                       json.dumps({'assumptions':plan,'computed':None},ensure_ascii=False))
    state['prompt_context_metrics'] = {
        'full_json_utf8_bytes':len(json.dumps({'assumptions':plan,'computed':result},ensure_ascii=False).encode()),
        'presentation_utf8_bytes':len(valuation_context.encode()),
        'basis':'Deterministic presentation size, not a token count or a promised cost saving. Full saved math remains authoritative.'}
    if not state.get('current_memo'):
        draft=call('full_draft',strong,
            'Write a complete investment-committee equity research draft for '+ticker+
            ', roughly 3000-4500 words plus useful exhibits, with a concise decision-first opening and clear headings. '+SECTIONS+
            '\nSupport material factual claims with [S#] references. Use the archived evidence and actual '
            'computed valuation and computed_diagnostics, not model mental arithmetic. Explain what must be true for the market price '
            'and what invalidates the thesis. Address management, governance, economic reinvestment, SBC/dilution, '
            'competitive-return fade, taxes, debt/cash/claims, tail risks and decision-changing sensitivities '
            'to the extent evidence supports them. Missing evidence must narrow the conclusion. '
            'Tie category demand, pricing/mix, delivery costs and GAAP expenses to the computed operating forecast. '
            'Compare dated Street estimates on a matching basis and identify the true disagreement; known near-term '
            'headwinds are not automatically an analytical edge. Engage named outside arguments and disclosed conflicts. '
            'Trace each load-bearing variant from outside expectation to house array, economic mechanism, '
            'source/counterevidence, computed conditional value and observation that would change the choice. '
            'If no differentiated operating view is supported, state that directly and defend the actual '
            'rate, terminal-duration or capitalization disagreement. Answer the strongest available credible '
            'opponent on the other side of the saved quote economically; a target panel or isolated rate '
            'sensitivity does not reproduce an outside model. Missing full-model access does not erase '
            'the sourced demand, supply, price/mix, margin or capital-allocation argument. '
            'For material one-off margin benefits/refunds, distinguish reported and normalized baselines '
            'and show how runoff, input costs, price/mix and expenses reach the forecast, with an explicit '
            'house residual where components cannot be quantified. Link inventory composition, trade cash '
            'movements and procurement commitments to margin timing and reinvestment without double counting '
            'the composite working-capital baseline or ordinary purchase costs. '
            'Explain the dated discount-rate build, source/proxy limits and separate hurdle if present. '
            'Use the computed monitoring cases to show dated observation -> changed input -> conditional value, '
            'without adding correlated cases together. Do not replace missing research with repeated caveats. '
            'Do not invent transcripts, guidance, consensus, exact fiscal dates, buybacks, or model features. '
            'This is valuation research only: do not give buy/sell/underweight ratings, portfolio sizing, '
            'tax or cost-basis advice. State the valuation view and what must be true instead. '
            'When computed entry_policy is meaningful, state its numerical conditional research entry price, '
            'valuation basis, selected discount and rationale. This is a house research threshold, not a '
            'personal trading instruction or promised return. Do not invent a 20% discount or hide the '
            'entry discipline behind only "wait for a lower price". If the policy/value is unavailable, '
            'explain that precise gap rather than inventing a threshold. '
            'Use plain analytical prose, without slogans, world-class superlatives, or rhetorical contrast formulas. '
            'Distinguish the house thesis from the competing market-pricing case; genuine falsifiers must '
            'undermine the house conclusion and change a named forecast or model assumption. '
            'State reverse-DCF growth as conditional on named fixed assumptions for its explicit forecast horizon, '
            'not as a unique observed market belief. Use computed_diagnostics for every numerical tax or WACC '
            'sensitivity and state which other assumptions are fixed. '
            'Use computed_diagnostics.valuation_multiples to show market versus original exit multiples '
            'with matched first/final forecast earnings definitions, alongside original-exit and unchanged-entry-'
            'multiple return diagnostics. Explain the rerating embedded in any return claim; the debt/claims-'
            'settled FCFF stream is not a forecast of actual common-share payouts. Assess sourced repurchase '
            'prices against current house value. EPS accretion alone does not prove value creation. Use '
            'computed repurchase_tests when supplied, distinguish retrospective conditional comparisons '
            'from forward illustrations, and retire cash/shares once without also paying that cash out. '
            'The executable valuation table is appended separately; discuss its economics without duplicating '
            'a conflicting numeric table. Forecasts are house assumptions, not reported facts.\nEvidence:\n'+context+
            '\nSynthesis:\n'+thesis+'\nValuation:\n'+valuation_context+'\nGaps:\n'+gaps,tokens=14000)
        state['current_memo']=draft
        checkpoint()
    revisions=folder/'revisions';revisions.mkdir(exist_ok=True)
    reviews=folder/'reviews';reviews.mkdir(exist_ok=True)
    def finish_revision(iteration,memo,combined,candidate):
        # Publish an explicitly pending reader surface alongside the committed
        # model before a network call that can fail or consume its reservation.
        state['complete']=False
        state['quality_status']='needs_review'
        pending_report=_render(memo,state['sources'],state['gaps'],plan,result,state)
        (folder/'memo.md').write_text(pending_report,encoding='utf-8')
        core.save(folder/'source-ledger.json',claim_ledger(pending_report))
        checkpoint()
        corrected=call(f'revise_{iteration+1}',strong,
            'Revise the complete memo to address every material finding below. Do not hide missing evidence '
            'or change actuals/valuation numbers to make a review disappear. Use the newly recomputed model below, '
            'not stale values from the prior draft. Explain material valuation changes and why the evidence '
            'or house assumptions justified them. Retain all required decision sections and specific citations. '
            'Correct verified defects and preserve legitimate disclosed assumptions; do not copy invalid reviewer '
            'accusations or unsupported fixes into reader prose. A valid narrative finding still needs correction '
            'when the model response says it has no forecast impact; do not confuse that with a false accusation. '
            'Distinguish evidence that falsifies the house '
            'valuation conclusion from evidence that falsifies a competing bullish/bearish case. '
            'Use computed_diagnostics for sensitivity statements; do not mentally invent tax or WACC ranges. '
            'All reverse-DCF statements are conditional on fixed named assumptions and explicit horizon. '
            'Retain the source-to-driver links, named outside challenge, rate build and computed monitoring table. '
            'Preserve the genuine outside-to-house variant bridge or an honest admission of no operating '
            'edge; normalized one-off margin, inventory/procurement cash and reinvestment links; and the '
            'strongest sourced opponent across the saved quote. Explain computed market/exit multiple '
            'and consistent return comparisons, the justified conditional entry threshold if meaningful, '
            'and repurchase economics against current house value. Do not resolve a finding by inventing '
            'a policy discount, nonrecurring adjustment, source passage or double-counted cash distribution. '
            'Return the full revised memo only, roughly 3000-4500 words plus exhibits.\nEvidence:\n'+context+
            '\nValuation:\n'+valuation_context+'\nMemo:\n'+memo+'\nFindings:\n'+json.dumps(combined)+'\nAdjudicated model response:\n'+json.dumps(candidate),tokens=14000)
        state['current_memo']=corrected
        state['iteration']=iteration+1
        state.pop('pending_revision',None)
        checkpoint()

    while state['iteration']<=max_revisions:
        iteration=state['iteration']
        memo=state['current_memo']
        pending=state.get('pending_revision')
        if _pending_revision_matches(pending,state):
            if iteration>=max_revisions:
                raise ValueError(f'Increase max_revisions to at least {iteration+1} to resume the pending prose revision')
            finish_revision(iteration,memo,pending['findings'],pending['model_response'])
            continue
        if pending:
            state.pop('pending_revision',None)
            checkpoint()
        visible=memo+'\n'+valuation_section(plan,result)
        (revisions/f'memo-{iteration}.md').write_text(visible,encoding='utf-8')
        # A review can fail after a new draft/model is saved. Keep the reader
        # download consistent with those artifacts even before review succeeds.
        state['complete']=False
        state['quality_status']='needs_review'
        pending=_render(memo,state['sources'],state['gaps'],plan,result,state)
        (folder/'memo.md').write_text(pending,encoding='utf-8')
        core.save(folder/'source-ledger.json',claim_ledger(pending))
        checkpoint()
        manifest={'memo_sha256':hashlib.sha256(visible.encode()).hexdigest(),
                  'sources_sha256':_digest(state['sources']),'valuation_sha256':_digest(result),
                  'review_type':'model_peer_review_not_publication_certification'}
        deterministic=inspect_memo(visible,state['sources'],result)
        fresh_notes=(state.get('external_notes','') if state.get('external_notes_digest') !=
                     state.get('external_notes_reviewed_digest') else '')
        prompt=('Independently audit this complete equity memo against its exact supplied evidence and '
                'Python valuation. Source support, dates/periods/session, currency/units, claimed cash conversion, '
                'buybacks/liquidity, net debt/cash/claims, WACC/tax/terminal reinvestment, scenario plausibility, '
                'market-implied economics, thesis direction and genuine falsifiers, strongest contrary explanation and '
                'decision framing all matter. Recompute every quoted sensitivity against computed_diagnostics; '
                'Also audit the research argument: company-specific economic drivers, dated Street comparison, '
                'authentic named outside views, calibrated required returns and observation-to-value monitoring. '
                'Verify outside expectation -> house adjustment -> named annual driver -> Python-computed '
                'conditional effect. A near-consensus operating view must not be sold as an invented edge. '
                'If a credible sourced opponent across the quote is available, check its economic case is '
                'answered rather than replaced by a target or inaccessible-model caveat. Check material '
                'nonrecurring gross-profit benefits/refunds and reported-versus-normalized baselines, plus '
                'inventory/procurement and trade-cash links to margins and reinvestment without counting '
                'working capital or operating purchases twice. '
                'Reconcile market and DCF-exit multiples using consistent earnings periods and show how '
                'original-exit versus unchanged-entry-multiple returns differ under one fixed ownership '
                'convention. Check a meaningful computed entry policy becomes a numeric conditional research '
                'threshold with its disclosed basis and justification, not an invented default discount. '
                'Test repurchase prices against current house value, distinct from EPS accretion; match '
                'historical dollars/shares/periods and distinguish retrospective conditional comparisons '
                'from observed historical losses or future buyback forecasts. No cash may be counted twice. '
                'A disclosed house assumption is not automatically well justified. Identify a material unsupported '
                'load-bearing assumption or missing contrary evidence when it changes the conclusion, while avoiding '
                'optional stylistic preferences or demands for unavailable proprietary data. '
                'a correct base valuation does not make surrounding sensitivity prose correct. '
                'Treat invented source assertions or unsupported valuation inputs as major; distinguish legitimate '
                'disclosed forecasts from factual errors. If primary reports conflict with a secondary series, '
                'require reconciliation rather than silently selecting a convenient number. '
                'Return only actionable unresolved findings. Omit withdrawn accusations, correct calculations, '
                'and optional stylistic preferences. Check whether the allegedly missing disclosure already '
                'appears elsewhere in the full memo before flagging it. Reserve blocker/major for errors '
                'that materially change valuation, factual understanding, or the conclusion. '
                'Every finding must quote the actual CURRENT draft wording or name the precise current omission. '
                'Earlier notes can be stale; never report an accusation that the current draft already resolves. '
                'Use at most 12 prioritized findings per review, issue <=100 words and fix <=70 words each; '
                'combine related issues. If further material issues remain, keep verdict revise and state '
                'that in a finding rather than issuing a false pass.\n'+REVIEW_SCHEMA+
                '\nEvidence:\n'+context+'\nValuation:\n'+valuation_context+'\nDraft:\n'+visible+
                '\nDeterministic diagnostics (not semantic proof):\n'+json.dumps(deterministic)+
                '\nNew external notes for this draft only; verify against the CURRENT wording and omit resolved allegations:\n'+fresh_notes+
                ('\nArtifact integrity recheck record: '+state['artifact_review_record']+
                 '. Original state-derived artifacts were reconstructed; deliberate edits require explicit supplied inputs. '
                 'Reassess this frozen draft and model.' if state.get('artifact_review_record') else ''))
        prior_review=next((r for r in reversed(state['review_history']) if r['iteration']==iteration),None)
        reusable=(prior_review and not state.get('review_invalidated') and
                  prior_review.get('review_model',strong)==reviewer and
                  prior_review.get('manifest',{}).get('memo_sha256')==manifest['memo_sha256'] and
                  prior_review.get('manifest',{}).get('valuation_sha256')==manifest['valuation_sha256'])
        if reusable:
            # Targeted evidence may have arrived after this frozen review. It
            # belongs in the correction; the unchanged draft need not be paid
            # for again after an interrupted model-response or revision step.
            record=prior_review
            combined=record['findings']
        else:
            raw=call(f'review_{iteration}',reviewer,prompt,tokens=12000)
            try:
                review_value,normalized=_response_json(raw)
                review=parse_review(json.dumps(review_value),memo=visible)
            except ValueError as exc:
                (reviews/f'review-{iteration}-unparsed.txt').write_text(raw,encoding='utf-8')
                # This call repairs presentation only. Repeating the full source
                # packet and valuation wastes context and invites a new audit.
                repair=('Reformat the recorded review below; do not conduct a new audit, add findings, '
                        'or discard unresolved substantive findings. Preserve IDs, severities and meaning. '
                        'For each finding copy ONE short contiguous verbatim excerpt, preferably 50–300 '
                        'characters, from the CURRENT FROZEN MEMO. Do not join passages, use ellipses, '
                        'wrap the excerpt in extra quotation marks, or write literal backslash-n text. '
                        'For an omission quote the adjacent actual wording and describe the omission in issue. '
                        'Omit only accusations explicitly withdrawn or described as already resolved in '
                        'the recorded review. Return exactly the schema.\n'+REVIEW_SCHEMA+
                        '\nFormat error: '+str(exc)+'\nCURRENT FROZEN MEMO:\n'+visible+
                        '\nRECORDED REVIEW:\n'+raw)
                raw=call(f'review_format_{iteration}',reviewer,repair,tokens=12000)
                (reviews/f'review-{iteration}-reformatted.txt').write_text(raw,encoding='utf-8')
                review_value,normalized=_response_json(raw)
                review=parse_review(json.dumps(review_value),memo=visible)
            if normalized:
                recovery={'stage':f'review_{iteration}','method':'one_explicit_complete_json_fence'}
                if recovery not in state.setdefault('response_normalizations',[]):
                    state['response_normalizations'].append(recovery)
            known={s['id'] for s in state['sources']}
            for finding in review['findings']:
                if not set(finding['source_refs']).issubset(known):
                    raise RuntimeError('Reviewer cited a source outside the research snapshot')
            combined=review['findings']+deterministic
            effective='revise' if any(f.get('severity') in {'blocker','major'} for f in combined) else review['verdict']
            record={'iteration':iteration,'verdict':effective,'model_verdict':review['verdict'],'findings':combined,'manifest':manifest,
                    'diagnostic_scope':DIAGNOSTIC_SCOPE,'review_model':reviewer}
            state['review_history']=[r for r in state['review_history'] if r['iteration']!=iteration]+[record]
            core.save(reviews/f'review-{iteration}.json',record)
            (reviews/f'review-{iteration}.txt').write_text(raw,encoding='utf-8')
            state.pop('review_invalidated',None)
        state['external_notes_reviewed_digest']=state.get('external_notes_digest')
        major=[f for f in combined if f.get('severity') in {'blocker','major'}]
        state['quality_status']='reviewed_draft' if not major and result else 'needs_review'
        report=_render(memo,state['sources'],state['gaps'],plan,result,state)
        (folder/'memo.md').write_text(report,encoding='utf-8')
        core.save(folder/'source-ledger.json',claim_ledger(report))
        checkpoint()
        if not major:
            state['complete']=bool(result)
            if not result:
                state['quality_status']='missing_valuation'
            break
        if iteration==max_revisions:
            state['complete']=False
            state['quality_status']='unresolved_findings'
            break
        call(f'targeted_research_{iteration+1}',easy,
             f'Investigate {ticker} primary evidence needed to resolve these specific findings. '
             'Find exact dated source passages; do not repeat unverified assertions. Findings:\n'+json.dumps(major),
             tokens=3500,web=True)
        research_row=next(r for r in reversed(state['ledger'])
                          if r['stage']==f'targeted_research_{iteration+1}' and r.get('status')=='complete')
        for annotation in research_row.get('annotations',[]):
            if annotation.get('content') and not any(s.get('url')==annotation['url'] for s in state['sources']):
                _register(folder,state['sources'],{'title':annotation['title'] or 'Targeted review evidence',
                    'url':annotation['url'],'kind':'search_excerpt','source_tier':'search_excerpt_unverified',
                    'data':{'excerpt':annotation['content'],'review_iteration':iteration+1}})
        context=_evidence_context(state['sources'])
        checkpoint()
        # Review may identify economic or evidentiary defects in the model.
        # Correct the editable plan first and independently recompute it, rather
        # than letting prose quietly contradict an unchanged spreadsheet.
        model_response=call(f'model_response_{iteration+1}',strong,
            'Address these review findings. Return ONLY JSON {"unchanged":true,"reason":"..."} '
            'if the existing valuation plan is still appropriate, otherwise return the COMPLETE corrected '
            'forecast proposal in the schema below. Do not change source-supported actuals just to get a pass. '
            'First verify each reviewer accusation against the actual memo, sources and model. Reject unsupported '
            'accusations or suggested fixes instead of replacing one unsupported story with another. A valid '
            'prose-only finding may leave forecasts unchanged; label it memo_only rather than rejecting it. Include compact '
            'finding_dispositions mapping finding IDs to accepted/rejected/clarified with source-backed reasons. '
            'Preserve or explicitly rejustify the entry policy and repurchase illustrations when forecasts change; '
            'do not silently drop their scope or invent a discount to satisfy a review. '
            'Forecast changes must be explicit house assumptions and economically justified. '
            +FORECAST_SCHEMA+UNDERWRITING_GUIDANCE+'\nEvidence:\n'+context+'\nCurrent memo (verify allegations against this exact text):\n'+memo+'\nCurrent forecast proposal:\n'+json.dumps({k:plan[k] for k in
                ('assumptions_label','scenario_rationales','scenarios','operating_model','discount_rate','monitoring',
                 'counterfactuals','calibration_comparisons','forecast_periods','entry_policy','repurchase_tests') if k in plan})+
            '\nLocked actuals (reference only; omit from your proposal):\n'+json.dumps(plan.get('actuals',{}))+
            '\nComputed diagnostics for verification:\n'+json.dumps((result or {}).get('computed_diagnostics',{}))+
            '\nComputed counterfactuals:\n'+json.dumps((result or {}).get('counterfactuals',{}))+
            '\nComputed reverse-DCF projections:\n'+json.dumps((result or {}).get('reverse_dcf',{}).get('solution_projections',[]))+
            '\nFindings:\n'+json.dumps(combined),tokens=12000)
        try:
            candidate,normalized=_response_json(model_response)
        except ValueError:
            model_response=call(f'model_response_format_{iteration+1}',easy,
                'Reformat this existing model response as exactly ONE compact JSON value. Remove analysis and '
                'Markdown outside the proposal. Preserve every field, number, forecast and finding disposition; '
                'do not add facts or change the proposal. Return JSON only.\nResponse:\n'+model_response,tokens=12000)
            candidate,normalized=_response_json(model_response)
        if normalized:
            recovery={'stage':f'model_response_{iteration+1}','method':'one_explicit_complete_json_fence'}
            if recovery not in state.setdefault('response_normalizations',[]):
                state['response_normalizations'].append(recovery)
        if not isinstance(candidate,dict):
            raise ValueError('Model correction must return an object')
        if candidate.get('unchanged') is not None and not isinstance(candidate['unchanged'],bool):
            raise ValueError('The unchanged flag must be boolean')
        adjudication=copy.deepcopy(candidate)
        if candidate.get('unchanged') is not True:
            prior=folder/'revisions'/f'model-before-{iteration+1}'
            prior.mkdir(parents=True,exist_ok=True)
            model_files=['assumptions.json','valuation.json','cashflows.csv','sensitivity.csv']
            for name in model_files:
                if (folder/name).exists() and not (prior/name).exists():
                    shutil.copyfile(folder/name,prior/name)
            if candidate.get('unavailable'):
                for name in model_files:
                    (folder/name).unlink(missing_ok=True)
                plan,result={k:v for k,v in candidate.items() if k!='finding_dispositions'},None
                state.pop('valuation',None)
            else:
                candidate,changes=_lock_plan(candidate,state['sources'])
                if not candidate['reported_inputs']['coverage'].get('complete'):
                    raise ValueError('Corrected model lacks complete reported-input coverage')
                state.setdefault('actual_input_corrections',[]).extend(changes)
                _validate_plan_sources(candidate,state['sources'])
                if 'requested_counterfactuals' in state:
                    candidate['counterfactuals']=copy.deepcopy(state['requested_counterfactuals'])
                result=evaluate(candidate)
                plan=candidate
                save_valuation(folder,plan,result)
                state['valuation']=result
            state['plan']=plan
            valuation_context=(render_valuation_context(plan,result) if result is not None else
                               json.dumps({'assumptions':plan,'computed':None},ensure_ascii=False))
        core.save(reviews/f'model-response-{iteration+1}.json',adjudication)
        state['pending_revision']={
            'iteration':iteration,
            'memo_sha256':hashlib.sha256(memo.encode()).hexdigest(),
            'plan_sha256':_digest(plan),'valuation_sha256':_digest(result),
            'sources_sha256':_digest(state['sources']),
            'external_notes_digest':state.get('external_notes_digest'),
            'review_manifest':copy.deepcopy(record['manifest']),
            'findings':copy.deepcopy(combined),'model_response':copy.deepcopy(adjudication)}
        checkpoint()
        finish_revision(iteration,memo,combined,adjudication)
    final_report=_render(state['current_memo'],state['sources'],state['gaps'],plan,result,state)
    (folder/'memo.md').write_text(final_report,encoding='utf-8')
    core.save(folder/'source-ledger.json',claim_ledger(final_report))
    checkpoint()
    if state.get('complete'):
        hashes=_delivered_artifact_hashes(folder,state)
        if _bad_archive_paths(folder,state,hashes):
            # Evidence can change while a provider request is in flight. A
            # fresh completion may never bless bytes that fail source checks.
            integrity_error=_verify_completed_artifacts(folder,state)
            checkpoint()
            state['artifact_blocked_observed_hashes']=_delivered_artifact_hashes(folder,state)
            checkpoint()
            raise integrity_error
        state['completed_artifact_hashes']=hashes
        checkpoint()
    progress(f'{state["quality_status"]}: {folder / "memo.md"}')
    return folder

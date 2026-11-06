"""Full portable research: archived evidence, editable DCF, bounded review/revision.

The model proposes forecasts; Python evaluates the valuation. Model reviews are
recorded assessments, never the original system's signed publication authority.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import copy
import json
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
from .research import gather_primary, inline_facts, archive_focus_excerpts
from .macro import gather_us_calibration
from .market_beta import gather_weekly_beta
from .prompt_context import valuation_context as render_valuation_context
from .valuation import evaluate, materialize, save_valuation

VERSION = 'full-2'
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
    source=dict(source)
    raw=source.get('raw_document')
    if isinstance(raw,str):
        facts=inline_facts(raw)
        if facts:
            data=copy.deepcopy(source.get('data',{}))
            if not isinstance(data,dict):
                data={'provided_text':data}
            data['inline_facts']=facts
            data['inline_fact_note']='Selected nondimensional reported facts; dates/units/contexts are distinct. Do not add totals to their components. Nil/unparsed values are unknown, never implicit zeros.'
            source['data']=data
    return source


def _register(folder, sources, source):
    source = _prepare_source(source)
    source['id'] = f'S{len(sources)+1}'
    source.setdefault('ingestion_method','public_http')
    source.setdefault('retrieved_at', datetime.now(timezone.utc).isoformat())
    raw = source.pop('raw_document', None)
    if raw is not None:
        raw = raw.encode() if isinstance(raw, str) else raw
        archive = folder / 'sources_raw'
        archive.mkdir(exist_ok=True)
        suffix = source.get('archive_extension', '.html')
        if suffix not in {'.html','.txt','.csv','.json','.xlsx','.pdf'}:
            raise ValueError('Unsupported source archive extension')
        target = archive / f'{source["id"]}{suffix}'
        target.write_bytes(raw)
        source['archive'] = str(target.relative_to(folder))
        source['archive_sha256'] = hashlib.sha256(raw).hexdigest()
    source['evidence_sha256'] = _digest(source.get('data'))
    sources.append(source)
    return source



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
        if not archive.is_relative_to(root) or not archive.is_file() or archive.stat().st_size>3000000:
            raise ValueError('Primary archive must remain inside the research folder and within 3 MB')
        raw=archive.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=source.get('archive_sha256'):
            raise ValueError('Primary archive checksum changed; supply a new source instead of editing its archive')
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
        if isinstance(data, dict) and (source.get('kind') in {'financial_timeseries','sec_facts','macro_calibration'} or 'excerpts' in data or 'inline_facts' in data):
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
    locked['assumptions_label']=(
        'Growth, margins, taxes, discount rates, capital efficiency, terminal returns and scenario weights '
        'are house forecasts, not reported facts or company guidance. Historical actuals use the '
        'reported-input snapshot. All reported cash and marketable securities are classified as '
        'nonoperating under an explicit house policy, not proof that operating cash needs are zero. '
        'Identified finance leases are deducted once outside financial net debt; additional unidentified '
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
    for field in ('operating_model', 'discount_rate', 'monitoring', 'calibration_comparisons'):
        if field in plan:
            nested_refs(plan[field], field)
    bridge = plan.get('net_debt_bridge')
    if isinstance(bridge, dict):
        debt, cash = bridge.get('debt'), bridge.get('selected_cash_and_nonoperating_investments')
        if isinstance(debt,(int,float)) and isinstance(cash,(int,float)):
            expected = debt - cash
            if abs(expected - plan['actuals']['net_debt']) > max(1,abs(expected)*1e-8):
                raise ValueError('Net debt does not reconcile to the disclosed debt/cash bridge')


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


def run(ticker, key, *, profile='balanced', budget=2.0, directory='research-output', evidence='',
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
    identity=_digest([VERSION,ticker,profile,easy,strong,evidence,source_snapshot,datetime.now(timezone.utc).date().isoformat(),core.SYSTEM])[:16]
    folder=Path(directory)/ticker/('full-'+identity)
    folder.mkdir(parents=True,exist_ok=True)
    with _lock(folder):
        return _run(folder,ticker,key,easy,strong,budget,evidence,progress,max_revisions,review_notes,extra_sources or [],counterfactuals,draft,review_model,source_snapshot,forecast_plan)


def _run(folder,ticker,key,easy,strong,budget,evidence,progress,max_revisions,review_notes,extra_sources,counterfactuals,draft,review_model,source_snapshot=None,forecast_plan=None):
    notes_reopened=False
    state_path=folder/'state.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {
        'version':VERSION,'ticker':ticker,'ledger':[],'stages':{},'sources':[], 'gaps':[],
        'iteration':0,'review_history':[],'quality_status':'in_progress','complete':False}
    def checkpoint():
        core.save(state_path,state)
        core.save(folder/'usage.json',state['ledger'])
        core.save(folder/'cost-summary.json',cost_summary(state['ledger']))
        core.save(folder/'sources.json',state['sources'])
    changed_forecast = forecast_plan is not None and _digest(forecast_plan) != state.get('provided_forecast_digest')
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
    if draft is not None and _digest(draft)!=state.get('provided_draft_digest'):
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
        sources,gaps=core.collect(ticker)
        state['sources'],state['gaps']=[],gaps
        for source in sources:
            _register(folder,state['sources'],source)
        if not any(s.get('kind')=='financial_timeseries' for s in state['sources']):
            try:
                url,data=yahoo_financials(ticker,core.request_json)
                data['derived_metrics']=annual_metrics(data)
                _register(folder,state['sources'],{'title':'Yahoo annual financial series (secondary)', 'url':url,
                          'kind':'financial_timeseries','data':data,'source_tier':'secondary'})
            except (RuntimeError,ValueError,KeyError,TypeError):
                state['gaps'].append('Supplemental annual financial series unavailable.')
        primary,gaps=gather_primary(ticker,core.request_json)
        state['gaps'].extend(gaps)
        for source in primary:
            _register(folder,state['sources'],source)
        calibration, calibration_gaps = gather_us_calibration(_fetch_calibration_bytes)
        for source in calibration:
            _register(folder,state['sources'],source)
        state['gaps'].extend(gap['reason'] for gap in calibration_gaps)
        beta_sources, beta_gaps = gather_weekly_beta(ticker,core.request_json,
            as_of=datetime.now(timezone.utc).date().isoformat())
        for source in beta_sources:
            _register(folder,state['sources'],source)
        state['gaps'].extend(gap['reason'] for gap in beta_gaps)
        if evidence:
            _register(folder,state['sources'],{'title':'Supplied evidence (verify provenance)','url':None,
                                               'kind':'user_evidence','data':evidence,'source_tier':'unverified'})
        state['evidence_collected']=True
        checkpoint()
    added=[]
    for source in extra_sources:
        source=_prepare_source(source)
        source['ingestion_method']='provided_archive'
        if any(old.get('url')==source.get('url') and old.get('evidence_sha256')==_digest(source['data'])
               for old in state['sources']):
            continue
        added.append(_register(folder,state['sources'],source)['id'])
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
    if not any(s.get('kind')!='market_snapshot' for s in state['sources']):
        raise RuntimeError('No financial or primary evidence is available; supply --evidence or retry data access')
    router=core.Router(key,budget,state['ledger'],checkpoint)
    def call(name,model,prompt,tokens=6000,web=False):
        cache=name+':'+_digest([prompt,model,tokens,web])[:12]
        if cache in state['stages']:
            return state['stages'][cache]
        progress(name+': '+model)
        try:
            value=router.complete(name,model,prompt,max_tokens=tokens,web=web)
            state['stages'][cache]=value
            return value
        finally:
            checkpoint()
    # Searches broaden coverage beyond the issuer's own account. Each call has
    # an explicit plugin fee/context reservation; there is no external API key.
    if not state.get('web_research_complete'):
        for lane,question in [
            ('business','latest annual filing business segments, competitive economics, revenue and margins; prefer primary filings and issuer releases'),
            ('challenge','named outside bull and bear investment arguments, original fund letters/research notes, their dates, disclosed positions, forecast and valuation-method disagreements; dated primary and reputable independent sources'),
            ('expectations','current fiscal-year consensus revenue and earnings, exact accounting basis, analyst counts/ranges and observation dates; distinguish existing Street expectations from a genuine house disagreement'),
            ('claims','latest proxy incentives, insider transaction types and dates, capital allocation, equity awards, debt/lease/tax/purchase commitments; identify which facts change an operating driver or equity claim')]:
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
                'List the five highest-impact uncertainties.\nEvidence:\n'+context,tokens=6500)
    reported=canonical_reported_inputs(state['sources'], as_of=(forecast_plan or state.get('plan',{})).get('valuation_date'))
    core.save(folder/'reported-inputs.json',reported)
    if reported['coverage'].get('conflicts'):
        raise RuntimeError('Primary financial facts conflict; resolve the source snapshot before valuation')
    if not reported['coverage'].get('complete'):
        prior=folder/'revisions'/'model-before-incomplete-coverage'
        prior.mkdir(parents=True,exist_ok=True)
        for name in ['assumptions.json','valuation.json','cashflows.csv','sensitivity.csv']:
            if (folder/name).exists():
                if not (prior/name).exists():shutil.copyfile(folder/name,prior/name)
                (folder/name).unlink()
        state['plan']={'unavailable':True,'unavailable_due_to_coverage':True,'missing_inputs':reported['coverage'].get('missing_fields',[]),
                       'reason':'Essential reported inputs lack complete primary-source coverage; no invented actuals or zero defaults'}
        state.pop('valuation',None)
    elif state.get('plan',{}).get('unavailable_due_to_coverage'):
        state.pop('plan',None)
    if changed_forecast:
        supplied, changes = _lock_plan(forecast_plan,state['sources'])
        if not supplied.get('reported_inputs',{}).get('coverage',{}).get('complete'):
            raise ValueError('Provided forecast requires complete reported-input coverage')
        _validate_plan_sources(supplied,state['sources'])
        if 'requested_counterfactuals' in state:
            supplied['counterfactuals'] = copy.deepcopy(state['requested_counterfactuals'])
        state['valuation'] = evaluate(supplied)
        state['plan'] = supplied
        state['provided_forecast_digest'] = _digest(forecast_plan)
        state.setdefault('forecast_history',[]).append({'sha256':_digest(forecast_plan),'origin':'provided_forecast','iteration':state['iteration']})
        state.setdefault('actual_input_corrections',[]).extend(changes)
        save_valuation(folder,supplied,state['valuation'])
        checkpoint()
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
            'Explain the dated discount-rate build, source/proxy limits and separate hurdle if present. '
            'Use the computed monitoring cases to show dated observation -> changed input -> conditional value, '
            'without adding correlated cases together. Do not replace missing research with repeated caveats. '
            'Do not invent transcripts, guidance, consensus, exact fiscal dates, buybacks, or model features. '
            'This is valuation research only: do not give buy/sell/underweight ratings, portfolio sizing, '
            'tax or cost-basis advice. State the valuation view and what must be true instead. '
            'Use plain analytical prose, without slogans, world-class superlatives, or rhetorical contrast formulas. '
            'Distinguish the house thesis from the competing market-pricing case; genuine falsifiers must '
            'undermine the house conclusion and change a named forecast or model assumption. '
            'State reverse-DCF growth as conditional on named fixed assumptions for its explicit forecast horizon, '
            'not as a unique observed market belief. Use computed_diagnostics for every numerical tax or WACC '
            'sensitivity and state which other assumptions are fixed. '
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
                '\nNew external notes for this draft only; verify against the CURRENT wording and omit resolved allegations:\n'+fresh_notes)
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
            'Forecast changes must be explicit house assumptions and economically justified. '
            +FORECAST_SCHEMA+UNDERWRITING_GUIDANCE+'\nEvidence:\n'+context+'\nCurrent memo (verify allegations against this exact text):\n'+memo+'\nCurrent forecast proposal:\n'+json.dumps({k:plan[k] for k in
                ('assumptions_label','scenario_rationales','scenarios','operating_model','discount_rate','monitoring','counterfactuals') if k in plan})+
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
    progress(f'{state["quality_status"]}: {folder / "memo.md"}')
    return folder

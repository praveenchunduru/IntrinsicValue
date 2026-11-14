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
from .quality import DIAGNOSTIC_SCOPE, assessment_findings, claim_ledger, inspect_memo, parse_json, parse_review
from .research import MAX_DOCUMENT_BYTES, gather_primary, inline_facts, archive_focus_excerpts
from .macro import gather_us_calibration
from .market_beta import gather_weekly_beta
from .prompt_context import valuation_context as render_valuation_context
from .primary_debt import classify_primary_debt
from .operating import derive as derive_operating
from .valuation import _timing as _valuation_timing, evaluate, materialize, save_valuation
from .companyfacts import CONCEPT_SELECTION_SHA256

VERSION = 'full-2'
DEFAULT_BUDGET = 10.0
SECTIONS = '''Sections, in this order: (1) Valuation conclusion: house value per share, the price,
value / price, and the one assumption family that carries most of the difference. (2) What the
price implies, from computed output only: the implied return at the price for each scenario and
weighted (implied_return_at_price) beside the required return, and the growth the price needs.
(3) Where the difference comes from: the computed convention_panel and calibration comparisons,
largest step first; say plainly when the difference rests on the discount rate or terminal
assumptions rather than on the operating forecast. (4) Variant thesis aimed at the largest
computed step: why the market is wrong on that step, with evidence; restating house forecasts is
not a variant thesis, and if the operating forecast is close to consensus say so. (5) Business
economics, industry and competition. (6) Management and capital allocation. (7) Historical
financial quality. (8) Operating forecast against dated consensus on a matching basis. (9)
Bear/base/bull scenarios and the basis for their weights. (10) Observations that would change the
value. (11) Risks, including evidence that the valuation framework rather than the forecast is
wrong. (12) Evidence gaps and unresolved questions.'''
# Memo stages rewrite the full memo. A live revision overran a 14,000-token
# limit as the memo grew, so the limit leaves room above the word budget.
MEMO_TOKENS = 18000
MEMO_WORDS = 'at most about 4,500 words including exhibits; a revision replaces text rather than adding to it'
FORECAST_SCHEMA = """Return ONLY compact JSON for house forecasts:
{"assumptions_label":"House forecasts, not reported facts or guidance",
 "scenario_rationales":{"bear":"Evidence, economic mechanism and the basis for this probability [S#]","base":"...","bull":"..."},
 "scenarios":{"bear":{"probability":<decimal>,"growth_rates":[<10 annual decimals>],
 "ebit_margins":[<10 annual decimals>],"tax_rate":<decimal>,"sales_to_capital":<number>,
 "terminal_roic":<decimal>},"base":{<same fields>},"bull":{<same fields>}}}.
Placeholders mark where your evidence-based numbers go; nothing here is a default.
Use 10 forecast years, the same horizon in all scenarios. Probabilities sum to 1 and each
needs a stated basis in scenario_rationales (frequencies of comparable outcomes, the evidence
that separates the states); symmetric weights are not a default. All rates are decimals.
The RATE CONVENTION FOR THIS RUN, stated after these instructions, says whether to give
scenario wacc and terminal_growth.
Keep each rationale under 100 words. Do not reproduce historical actuals, cash/debt
reconciliations, source passages, calculated cashflows or valuation targets: Python
locks reported inputs and independently computes the outputs.
The unlevered operating-company model uses GAAP EBIT less operating taxes and
incremental revenue / sales-to-capital reinvestment. Maintenance investment offsets
D&A; GAAP EBIT already expenses SBC, R&D and operating rent. Do not deduct those
again. Explicitly justify growth/margin fade, capital efficiency, tax, WACC,
terminal growth and terminal ROIC. Terminal reinvestment is g/ROIC of NOPAT;
explain a material forecast-to-terminal capital-intensity transition. A terminal ROIC above the
market's long-run return on new capital (about 15-17% on capital) is a claim of durable advantage
that needs evidence about returns on new investment, not the average return on existing assets.
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
reported_inputs.operating_reference gives the last reported year's gross profit, cost of revenue,
R&D, selling_general_administrative, operating expenses (excluding cost of revenue) and operating
income, and the year to date of the first forecast year where reported. Start year-1 gross margins
and expense amounts from the most recent reported run-rate. A year-1 EBIT margin more than 5 points
(less for low-margin businesses) from both the reported annual and year-to-date operating margins is
rejected unless margin_continuity_basis (an object keyed by bear, base, bull) explains that
scenario's step with evidence [S#] from the sources.
Use aggregate_ebit_margins mode without gross margins/expenses if only category revenues are supported.
Omit main scenario growth_rates and, in gross mode, ebit_margins: Python derives them.
When the evidence includes the matched risk-free rate and implied equity risk premium, a dated
discount_rate is required, using the documented rates.py schema: matched risk_free_rate/
equity_risk_premium observations, explicit unlevered_beta, market_equity, financial_debt,
finance_lease_debt, pretax_cost_of_debt, marginal_tax_rate. marginal_tax_rate is the statutory
national plus subnational rate (United States: about 25%, the 21% federal rate plus state), never
the reported effective rate; the effective rate the evidence supports as durable belongs in
scenario tax_rate (cite the cash tax rate and any legislated minimum such as Pillar Two).
Build unlevered_beta bottom-up: {"calculation":"bottom_up_sector_weighted","sectors":[{"sector":
"<exact industry name from the supplied industry beta table>","weight":<share of the business>}],
"source_refs":["S#"],"label":"why each business maps to that industry and how weights were set"}.
Weight by the business mix (reported segment revenue, or value if the evidence supports it);
Python pins every industry beta from the table, so write no beta numbers. The weekly regression
beta is computed separately as a cross-check. Python also pins the market beta used for the
terminal cost of capital and dates the valuation; do not supply term_structure,
terminal_unlevered_beta or valuation_timing.
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
Only when no industry in the table fits the business, the unlevered_beta
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
This is valuation research only: do not supply entry thresholds, margins of safety or other
trading rules.
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
REVIEW_SCHEMA = '''Return ONLY {"verdict":"pass"|"revise","central_weakness":{"claim":"the one assumption or
convention that, if wrong, most changes the conclusion","computed_effect":"its value effect from the
computed output, or not computed","memo_handles_it":"yes"|"partly"|"no"},"gap_explained":"yes"|"partly"|"no",
"findings":[{"id":"F1",
"severity":"blocker"|"major"|"minor","issue":"precise defect and location",
"source_refs":["S1"],"current_excerpt":"exact quote from the CURRENT draft, at most 1200 characters","fix":"specific correction or evidence needed"}]}.
Findings are unresolved; use revise if any blocker/major exists. Use pass only when no material
finding remains. If memo_handles_it or gap_explained is not "yes", include a blocker or major finding
for it. Severity follows the computed value effect: blocker when it changes the conclusion or the
main source of the difference between value and price; minor when under 2% of base value. Do not manufacture defects to fill a quota, and verify every accusation
against the actual draft. Valid source IDs alone do not prove a claim is supported.'''


# A response cut off at its limit is rejected, never used. The open-weight
# research model overran a 3,500-token limit on a live search lane
# (2026-11-14), so the limit leaves headroom over the requested length.
WEB_RESEARCH_TOKENS = 6000
# Plans are validated strictly and each failure names one defect. A single
# repair could not clear two independent format defects in one live plan
# (2026-11-14), which ended the run before a draft existed.
PLAN_REPAIRS = 3
PLAN_FIELD_RULES = ('\nField rules: unknown fields are rejected, so use only the field names shown above. Inside '
                    'operating_model the top level has only currency, amount_unit, assumptions_label, earnings_mode, '
                    'components and scenarios (optionally counterfactual_policy). Each component has only name, '
                    'base_revenue and source_refs (optionally gross_margin_group, disclosure). Each operating scenario '
                    'has only components and, in gross_profit_less_expenses mode, expenses. Each scenario component has '
                    'only growth_rates and, in that mode, gross_margins. Do not add label, note, rationale, horizon or '
                    'reconciliation fields there; explanations belong in scenario_rationales. amount_unit is always the '
                    'literal string "currency_units", never a currency code. counterfactuals is an object keyed by case '
                    'name, never a list, for example {"lower_services_margin":{"operating_overrides":{"components":'
                    '{"services":{"gross_margins":[0.7,0.7,0.7,0.7,0.7]}}}}}. Each case changes the base scenario only: '
                    'no bear, base or bull keys, no name or label field, and every array spans the full horizon. '
                    'With an operating_model, scenarios omit growth_rates (and ebit_margins in gross_profit_less_expenses '
                    'mode). With discount_rate, scenarios omit wacc. Follow the RATE CONVENTION FOR THIS RUN for '
                    'terminal_growth. Every observed_at is a YYYY-MM-DD date, except the month (YYYY-MM) of an '
                    'industry-table beta.')
# Diagnostics a plan may carry. Each is computed separately from the scenario
# values, so an invalid one can be left out without changing the valuation.
OPTIONAL_PLAN_SECTIONS = ('counterfactuals','calibration_comparisons','monitoring','repurchase_tests')


# Recorded in run state when a model-authored plan was locked under the
# market-consistent conventions. A plan saved before them is re-locked under
# its own conventions on resume instead of failing outside any repair loop.
PLAN_CONVENTIONS = 'market_consistent_v1'


def _default_reviewer(profile, strong):
    """A reviewer from a different model than the author whenever one is configured.

    Overriding the author model must not make the author review its own memo.
    """
    for candidate in (core.REVIEWERS.get(profile), core.MODELS.get(profile, (None, None))[1]):
        if candidate and candidate != strong:
            return candidate
    return strong


def _lock_and_value(candidate, state, *, apply_conventions=True, prior=None):
    """Lock reported inputs and conventions into one plan, then value it.

    Shared by the plan stage and the review-correction stage so both apply the
    same pins, coverage check, source check and requested counterfactuals.
    """
    locked,changes=_lock_plan(candidate,state['sources'],apply_conventions=apply_conventions)
    if not locked['reported_inputs']['coverage'].get('complete'):
        raise ValueError('Reported-input coverage is incomplete')
    _validate_plan_sources(locked,state['sources'])
    if 'requested_counterfactuals' in state:
        locked['counterfactuals']=copy.deepcopy(state['requested_counterfactuals'])
    result=_evaluate(locked)
    _check_continuity(locked,result,state['sources'],prior)
    if prior and 'forecast_continuity' in result:
        result['forecast_continuity']=forecast_continuity(locked,result)
    return locked,changes,result


# A year-1 EBIT margin this far from the last reported operating margin needs
# a stated, evidenced basis. Live plans on 2026-11-14 reached 27% and 20%
# against Apple's reported 33% by inventing expense levels. The band narrows
# for low-margin businesses, where five points exceeds all operating income.
MARGIN_CONTINUITY = .05
MARGIN_CONTINUITY_FLOOR = .015
MARGIN_CONTINUITY_SHARE = .4
# A correction that leaves a scenario's year-1 margin within this distance of
# the plan it corrects keeps that plan's stated basis.
MARGIN_CARRY_TOLERANCE = .01
SCENARIO_NAMES = ('bear','base','bull')


def _continuity_band(margin):
    return min(MARGIN_CONTINUITY,max(MARGIN_CONTINUITY_FLOOR,MARGIN_CONTINUITY_SHARE*abs(margin)))


def forecast_continuity(plan, result):
    """Year-1 EBIT margin in each scenario against the reported annual and year-to-date margins."""
    reference=((plan or {}).get('reported_inputs') or {}).get('operating_reference') or {}
    margin=reference.get('operating_margin')
    if not isinstance(margin,(int,float)) or not result or not result.get('scenarios'):
        return None
    ytd=reference.get('year_to_date') if isinstance(reference.get('year_to_date'),dict) else None
    ytd_margin=ytd.get('operating_margin') if ytd and isinstance(ytd.get('operating_margin'),(int,float)) else None
    bases=(plan or {}).get('margin_continuity_basis') if isinstance((plan or {}).get('margin_continuity_basis'),dict) else {}
    rows={}
    for name,scenario in result['scenarios'].items():
        first=(scenario.get('cashflows') or [{}])[0]
        year_one=first.get('ebit_margin')
        if not isinstance(year_one,(int,float)):
            continue
        row={'year_one_ebit_margin':year_one,'change_percentage_points':(year_one-margin)*100,
             'within_band':abs(year_one-margin)<=_continuity_band(margin)+1e-12}
        if ytd_margin is not None:
            row['change_from_year_to_date_percentage_points']=(year_one-ytd_margin)*100
            row['within_band']=row['within_band'] or abs(year_one-ytd_margin)<=_continuity_band(ytd_margin)+1e-12
        if isinstance(bases.get(name),str):
            row['stated_basis']=bases[name]
        rows[name]=row
    lines=reference.get('lines') or {}
    output={'reported_operating_margin':margin,'period':reference.get('period'),
            'reported_lines':{key:line.get('value') for key,line in lines.items()},
            'source_refs':sorted({ref for line in lines.values() for ref in line.get('source_refs',[])}),
            'band_percentage_points':_continuity_band(margin)*100,'scenarios':rows,
            'basis':'Year-1 EBIT margin from the computed cash flows against the reported operating margin of the revenue '
                    'anchor year and, where reported, the year to date of the first forecast year. A diagnostic of '
                    'forecast continuity; the reported lines are not valuation inputs.'}
    if ytd_margin is not None:
        output['year_to_date']={'operating_margin':ytd_margin,'period':ytd.get('period'),
                                'source_refs':ytd.get('source_refs',[]),'band_percentage_points':_continuity_band(ytd_margin)*100}
    return output


def _evaluate(plan):
    """Value a locked plan and attach the forecast-continuity diagnostic."""
    result=evaluate(plan)
    continuity=forecast_continuity(plan,result)
    if continuity:
        result['forecast_continuity']=continuity
    return result


def _check_continuity(locked, result, sources=(), prior=None):
    """Send a plan back when year-1 margins break from the reported results without a cited basis.

    ``prior`` is the (plan, result) a review correction replaces: a scenario whose
    year-1 margin the correction leaves unchanged keeps the prior plan's basis.
    """
    continuity=result.get('forecast_continuity')
    if not continuity:
        return
    bases=locked.get('margin_continuity_basis')
    if bases is not None and (not isinstance(bases,dict) or set(bases)-set(SCENARIO_NAMES)):
        raise ValueError('margin_continuity_basis must be an object keyed by bear, base or bull')
    bases=dict(bases or {})
    known={source.get('id') for source in sources if isinstance(source,dict)}
    if prior:
        prior_plan,prior_result=prior
        prior_rows=((prior_result or {}).get('forecast_continuity') or {}).get('scenarios') or {}
        prior_bases=(prior_plan or {}).get('margin_continuity_basis') or {}
        for name,row in continuity['scenarios'].items():
            before=prior_rows.get(name)
            if (name not in bases and before and abs(row['year_one_ebit_margin']-before['year_one_ebit_margin'])<=MARGIN_CARRY_TOLERANCE
                    and (isinstance(prior_bases.get(name),str) or before.get('within_band') is False)):
                # The correction did not move year 1. A supplied plan's own step
                # is treated as already accepted.
                bases[name]=prior_bases.get(name) or 'Year-1 margin unchanged from the plan this correction replaces.'
                row['stated_basis']=bases[name]
                row['basis_carried_from_prior_plan']=True
        if bases:
            locked['margin_continuity_basis']=bases
    breaks=[]
    for name,row in continuity['scenarios'].items():
        basis=bases.get(name)
        cited=re.findall(r'\[(S\d+)\]',basis) if isinstance(basis,str) else []
        unknown=[ref for ref in cited if known and ref not in known]
        if unknown:
            raise ValueError(f'margin_continuity_basis.{name} cites {", ".join(unknown)}, which is not in the evidence')
        carried=row.get('basis_carried_from_prior_plan')
        explained=carried or (isinstance(basis,str) and len(basis.strip())>=40 and bool(cited))
        if not row['within_band'] and not explained:
            breaks.append(f'{name} {row["year_one_ebit_margin"]:.1%}')
    if not breaks:
        return
    lines=continuity['reported_lines']
    def billions(key):
        value=lines.get(key)
        return f'{value/1e9:,.1f} billion' if isinstance(value,(int,float)) else "not in this run's collected facts"
    expenses=''
    operating=locked.get('operating_model') or {}
    if operating.get('earnings_mode')=='gross_profit_less_expenses':
        planned=[]
        for name in SCENARIO_NAMES:
            amounts=((operating.get('scenarios') or {}).get(name) or {}).get('expenses') or {}
            first={line:values[0] for line,values in amounts.items()
                   if isinstance(values,list) and values and isinstance(values[0],(int,float))}
            if first:
                planned.append(name+' '+', '.join(f'{line} {value/1e9:,.1f} billion' for line,value in first.items()))
        if planned:
            expenses=' Year-1 expenses in the plan: '+'; '.join(planned)+'.'
    period=(continuity.get('period') or {}).get('end_date','the last reported year')
    ytd=continuity.get('year_to_date')
    ytd_text=(f' The year to date ending {(ytd.get("period") or {}).get("end_date")} reports a '
              f'{ytd["operating_margin"]:.1%} operating margin [{", ".join(ytd["source_refs"])}].' if ytd else '')
    raise ValueError(
        f'Year-1 EBIT margin breaks from the reported {continuity["reported_operating_margin"]:.1%} operating margin '
        f'(fiscal year ending {period}) by more than {continuity["band_percentage_points"]:.1f} points in: '+', '.join(breaks)+
        '.'+ytd_text+f' Reported for that year: gross profit {billions("gross_profit")}, R&D {billions("research_and_development")}, '
        f'SG&A {billions("selling_general_administrative")}, operating expenses excluding cost of revenue '
        f'{billions("operating_expenses")}, operating income {billions("operating_income")} [{", ".join(continuity["source_refs"])}].'+
        expenses+' Start year-1 gross margins and expenses from the most recent reported run-rate (annual or year to date), '
        'or, if a step of this size is the forecast, state its evidence in margin_continuity_basis.<scenario> with [S#] '
        'citations to sources in the evidence')


CONTINUITY_RULE = ('\nYear-1 continuity: start year-1 gross margins and expenses from the most recent reported run-rate '
                   'in reported_inputs.operating_reference (annual or year to date). A larger step needs '
                   'margin_continuity_basis.<bear|base|bull> with [S#] citations to sources in the evidence.')
LEGACY_PLAN_NOTE = ('This forecast plan was saved before the market-consistent rate and terminal-growth conventions and is '
                    'valued under its own conventions. Run in a new --output folder to build a plan under the current '
                    'conventions.')


def _add_plan_note(state, note):
    """A note that describes the plan now valued; replaced when another plan is adopted."""
    state.setdefault('plan_gap_notes',[])
    if note not in state['plan_gap_notes']:
        state['plan_gap_notes'].append(note)
    if note not in state['gaps']:
        state['gaps'].append(note)


def _reset_plan_notes(state):
    for note in state.pop('plan_gap_notes',[]):
        if note in state['gaps']:
            state['gaps'].remove(note)
    state.pop('omitted_plan_sections',None)


def _record_omitted_sections(state, omitted, stage):
    for section,reason in omitted.items():
        state.setdefault('omitted_plan_sections',{})[section]=reason
        _add_plan_note(state,f'The {stage} optional {section} did not pass validation ({reason}) and was omitted; '
                             'nothing was computed for it.')


def _correction_rate_text(sources, plan, apply):
    """The rate instruction for a review correction, matched to how it will be locked."""
    currency=((plan.get('reported_inputs') or {}).get('currency') or plan.get('currency') or
              canonical_reported_inputs(sources).get('currency'))
    rate=plan.get('discount_rate') if isinstance(plan.get('discount_rate'),dict) else {}
    if apply or rate.get('term_structure')=='glide_to_market_v1' or 'valuation_timing' in plan:
        return _rate_convention(sources,currency)
    growth={name:(scenario or {}).get('terminal_growth') for name,scenario in (plan.get('scenarios') or {}).items()
            if isinstance(scenario,dict)}
    return ('\nRATE CONVENTION FOR THIS RUN: this supplied plan keeps its own constant discount rate. Keep each scenario\'s '
            'terminal_growth'+('' if rate else ' and wacc')+' as in the current proposal '+json.dumps(growth)+
            ' unless a finding requires a change; do not add term_structure or valuation_timing.')


def _omit_invalid_optional_sections(plan,validated):
    """Validate a plan, leaving out optional diagnostics that alone fail.

    Returns ``(validated(plan_used), {section: reason})``. If the plan is invalid
    without any optional section, that core error is raised for repair. A paid
    repair of a whole plan for one malformed diagnostic did not converge live
    (2026-11-14): the model alternated between two rejected shapes.
    """
    try:
        return validated(plan),{}
    except (ValueError,KeyError,TypeError):
        present=[section for section in OPTIONAL_PLAN_SECTIONS if section in plan]
        if not present:
            raise
    kept={key:value for key,value in plan.items() if key not in present}
    outcome,omitted=validated(kept),{}
    for section in present:
        trial={**kept,section:plan[section]}
        try:
            outcome=validated(trial)
            kept=trial
            continue
        except (ValueError,KeyError,TypeError) as exc:
            reason=str(exc)[:300]
        # One malformed case should not take the section's valid cases with it.
        cases=plan[section]
        if isinstance(cases,dict) and len(cases)>1:
            accepted={}
            for name,case in cases.items():
                trial={**kept,section:{**accepted,name:case}}
                try:
                    outcome=validated(trial)
                    accepted[name]=case
                except (ValueError,KeyError,TypeError) as exc:
                    omitted[f'{section}.{name}']=str(exc)[:300]
            if accepted:
                kept={**kept,section:accepted}
                continue
        elif isinstance(cases,list) and len(cases)>1:
            accepted=[]
            for index,case in enumerate(cases):
                trial={**kept,section:[*accepted,case]}
                try:
                    outcome=validated(trial)
                    accepted.append(case)
                except (ValueError,KeyError,TypeError) as exc:
                    label=case.get('name',index) if isinstance(case,dict) else index
                    omitted[f'{section}[{label}]']=str(exc)[:300]
            if accepted:
                kept={**kept,section:accepted}
                continue
        omitted[section]=reason
    return outcome,omitted


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
            row['data'] = copy.deepcopy(core.prompt_companyfacts(data))
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
            state.pop('plan_conventions',None)
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
            add('web_'+lane,easy,f'Research {ticker}: {lane}.',WEB_RESEARCH_TOKENS,web=True)
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
            context+'\nValuation:\n'+valuation,MEMO_TOKENS)
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
    # No "github" in the User-Agent: SEC returns HTTP 403 for any UA
    # containing that token (verified live 2026-11-07); keep this UA plain too.
    # An explicit Accept header is required: FRED closes the connection without
    # a response for this User-Agent when Accept is absent (verified live
    # 2026-11-14: same UA with Accept -> 200, without -> RemoteDisconnected).
    request = Request(url, headers={'User-Agent':'IntrinsicValue research','Accept':'*/*'})
    with urlopen(request, timeout=25) as response:
        raw = response.read(3000001)
    if len(raw) > 3000000:
        raise ValueError('Calibration source exceeds 3 MB')
    return raw


AGGREGATE_TOLERANCE=.005


MARKET_BETA_SECTOR_PREFIX='Total Market (without financial'


def _glide_available(sources, currency):
    """Whether the market-consistent rate glide can be pinned for this run."""
    erp=_calibration_source(sources,dataset='damodaran_us_implied_erp')
    table=_calibration_source(sources,dataset='damodaran_us_sector_betas')
    market=any(isinstance(row,dict) and str(row.get('sector','')).startswith(MARKET_BETA_SECTOR_PREFIX) and row.get('is_aggregate')
               for row in ((table or {}).get('data') or {}).get('sectors',[]))
    return currency=='USD' and erp is not None and market


def _rate_convention(sources, currency):
    """The rate instructions for this run, matched to what Python will apply."""
    erp=_calibration_source(sources,dataset='damodaran_us_implied_erp')
    if _glide_available(sources,currency):
        return ('\nRATE CONVENTION FOR THIS RUN: the market-consistent rate path applies. Omit scenario wacc and '
                'terminal_growth. Python discounts with the current cost of capital for years 1-5, moves to '
                'market-average business risk by year 10, and sets terminal growth to the risk-free rate, the long-run '
                'growth the equity risk premium itself assumes. State terminal_growth only below that rate, with a '
                'company-specific terminal_growth_basis (secular decline, shrinking market); a lower number is not a '
                'conservative default, it values the company below the market\'s own assumption. State it, with a '
                'basis, when final forecast growth is below 1% or terminal ROIC is below the risk-free rate.')
    if erp is not None and currency=='USD':
        return ('\nRATE CONVENTION FOR THIS RUN: one constant discount rate is used, because the market-consistent '
                'path also needs the industry table\'s market beta, which was not retrieved. With discount_rate, omit '
                'scenario wacc and state terminal_growth for each scenario, below the cost of capital and no higher than '
                'the risk-free rate.')
    if erp is not None:
        return ('\nRATE CONVENTION FOR THIS RUN: one constant discount rate is used. The risk-free rate and equity risk '
                'premium in the evidence are US measurements and do not apply to cash flows in '+str(currency)+'. Either '
                'give a discount_rate whose risk_free_rate is a dated government yield in the cash-flow currency with its '
                'own source, or give wacc for each scenario. State terminal_growth for each scenario, below the cost of '
                'capital and no higher than long-run nominal growth in the cash-flow currency.')
    return ('\nRATE CONVENTION FOR THIS RUN: no dated risk-free rate and equity risk premium were retrieved. Give wacc '
            'and terminal_growth for each scenario; WACC exceeds terminal growth.')


def _calibration_source(sources, dataset=None, kind=None):
    for source in sources:
        data=source.get('data') if isinstance(source,dict) else None
        if not isinstance(data,dict):
            continue
        if (dataset and data.get('dataset')==dataset) or (kind and source.get('kind')==kind):
            return source
    return None


def _pin_financing_records(rate, reported, record):
    """Copy debt, finance leases and market equity into the rate block from the reported bridges.

    A live plan (2026-11-14) wrote Apple's $1.456 billion of separately reported
    finance leases as a zero 'included in debt' allocation and was rejected for
    it. These are reported amounts, so Python supplies them, as it does betas.
    An explicit market-value debt or independent market cap is left alone.
    """
    debt=reported.get('net_debt_bridge') or {}
    claims=reported.get('other_claims_bridge') or {}
    refs={key:[ref for ref in value if isinstance(ref,str) and re.fullmatch(r'S\d+',ref)]
          for key,value in (reported.get('source_refs') or {}).items() if isinstance(value,list)}
    day=debt.get('as_of')
    if isinstance(debt.get('debt'),(int,float)) and day:
        def put(field, value, label, allocation=None, force_label=False):
            current=rate.get(field) if isinstance(rate.get(field),dict) else {}
            if current.get('measurement') not in (None,'book_value_proxy'):
                return
            pinned={k:v for k,v in current.items() if k!='allocation'}
            pinned.update(value=value,observed_at=day,measurement='book_value_proxy',
                          source_refs=list(dict.fromkeys(refs.get('net_debt' if field=='financial_debt' else 'other_equity_claims')
                                                         or refs.get('net_debt') or current.get('source_refs') or [])))
            if allocation:
                pinned['allocation']=allocation
            if force_label or not isinstance(pinned.get('label'),str) or not pinned['label'].strip():
                pinned['label']=label
            if any(current.get(key)!=pinned.get(key) for key in ('value','observed_at','allocation')):
                record(f'discount_rate.{field}',{key:current.get(key) for key in ('value','observed_at','allocation')},
                       {key:pinned.get(key) for key in ('value','observed_at','allocation')},'pinned from the reported equity bridge')
            rate[field]=pinned
        lease=claims.get('reported_finance_lease_liabilities')
        if not debt.get('finance_leases_included_in_net_debt'):
            put('financial_debt',debt['debt'],'Reported financial debt excluding finance leases')
            put('finance_lease_debt',lease if isinstance(lease,(int,float)) else 0,
                'Reported finance lease liabilities' if isinstance(lease,(int,float)) else
                'Finance lease liabilities assumed zero: no finance-lease concept reported')
        elif isinstance(lease,(int,float)):
            put('financial_debt',debt['debt']-lease,'Reported combined debt less separately reported finance leases')
            put('finance_lease_debt',lease,'Reported finance lease liabilities')
        else:
            put('financial_debt',debt['debt'],'Combined financial debt including finance leases',
                'combined_debt_including_finance_leases',force_label=True)
            put('finance_lease_debt',0,'Finance-lease zero allocation: included in combined debt',
                'included_in_financial_debt',force_label=True)
    price,shares=reported.get('market_price'),(reported.get('actuals') or {}).get('shares_outstanding')
    quoted=((reported.get('input_dates') or {}).get('market_price') or {}).get('observed_at')
    current=rate.get('market_equity') if isinstance(rate.get('market_equity'),dict) else {}
    if (isinstance(price,(int,float)) and isinstance(shares,(int,float)) and quoted and
            current.get('construction',current.get('measurement')) in (None,'quote_times_reported_shares')):
        pinned={**current,'value':price*shares,'observed_at':quoted,'construction':'quote_times_reported_shares',
                'source_refs':list(dict.fromkeys([*(refs.get('market_price') or []),*(refs.get('shares_outstanding') or [])]))}
        pinned.pop('measurement',None)
        pinned.setdefault('label','Saved quote times reported shares outstanding')
        if current.get('value')!=pinned['value'] or current.get('observed_at')!=quoted:
            record('discount_rate.market_equity',{key:current.get(key) for key in ('value','observed_at')},
                   {key:pinned[key] for key in ('value','observed_at')},'saved quote times reported shares')
        rate['market_equity']=pinned


def _pin_market_conventions(locked, sources, reported, valuation_day, changes):
    """Pin the market-consistent conventions and every beta number from the run's own sources.

    The model chooses the industry mapping; Python supplies each number. Pins are
    idempotent, and a change is recorded only when a value actually changes.
    """
    def record(field, previous, pinned, reason):
        if previous!=pinned:
            changes.append({'field':field,'previous':previous,'pinned':pinned,'source_refs':[],'reason':reason})
    erp=_calibration_source(sources,dataset='damodaran_us_implied_erp')
    table=_calibration_source(sources,dataset='damodaran_us_sector_betas')
    regression=_calibration_source(sources,kind='historical_equity_beta')
    notes=[]
    if 'discount_rate' in locked and locked['discount_rate'] is None:
        record('discount_rate',None,'omitted','an explicit null discount rate is treated as absent')
        locked.pop('discount_rate')
    rate=locked.get('discount_rate')
    if rate is not None and not isinstance(rate,dict):
        raise ValueError('discount_rate must be an object or omitted')
    usd=locked.get('currency')=='USD'
    if rate is None:
        if erp is not None and usd and not locked.get('unavailable'):
            raise ValueError('A dated discount_rate is required: the evidence includes a matched risk-free rate and '
                             f'implied equity risk premium ({erp["id"]}).'+_rate_convention(sources,locked.get('currency')))
        notes.append('No dated risk-free rate and equity risk premium in the cash-flow currency were available, so the '
                     'market-consistent rate glide and terminal-growth convention are not applied.')
        return notes
    if erp is not None and not usd:
        cited={ref for key in ('risk_free_rate','equity_risk_premium') if isinstance(rate.get(key),dict)
               for ref in (rate[key].get('source_refs') or [])}
        if erp['id'] in cited:
            raise ValueError(f'The US risk-free rate and equity risk premium ({erp["id"]}) cannot discount cash flows in '
                             f'{locked.get("currency")}.'+_rate_convention(sources,locked.get('currency')))
    _pin_financing_records(rate,reported,record)
    if erp is not None and usd:
        # The conventions rest on the premium and the Treasury rate it was
        # solved at. Python copies both from the source, as it does the betas.
        data=erp['data']
        for key in ('risk_free_rate','equity_risk_premium'):
            current=rate.get(key) if isinstance(rate.get(key),dict) else {}
            pinned={**current,'value':data[key],'observed_at':data['observed_at'],
                    'source_refs':list(dict.fromkeys([*(current.get('source_refs') or []),erp['id']]))}
            pinned.setdefault('label','Damodaran implied equity risk premium and the Treasury rate it was solved at')
            if current.get('value')!=pinned['value'] or current.get('observed_at')!=pinned['observed_at']:
                record(f'discount_rate.{key}',{k:current.get(k) for k in ('value','observed_at')},
                       {'value':pinned['value'],'observed_at':pinned['observed_at']},'pinned from the implied premium source')
            rate[key]=pinned
    marginal=(rate.get('marginal_tax_rate') or {}).get('value') if isinstance(rate.get('marginal_tax_rate'),dict) else None
    if locked.get('currency')=='USD' and isinstance(marginal,(int,float)) and marginal<.21:
        raise ValueError(f'discount_rate.marginal_tax_rate {marginal:.1%} is below the 21% US federal statutory rate. The '
                         'marginal rate is the statutory federal plus state rate (about 25%), never the reported effective rate; '
                         'the effective rate belongs in scenario tax_rate')
    rows={row['sector']:row for row in (table or {}).get('data',{}).get('sectors',[]) if isinstance(row,dict)}
    beta=rate.get('unlevered_beta')
    if isinstance(beta,dict) and beta.get('calculation')=='bottom_up_sector_weighted':
        if table is None:
            raise ValueError('A bottom-up beta needs the supplied industry beta table; none was retrieved')
        sectors=beta.get('sectors')
        if not isinstance(sectors,list) or not sectors:
            raise ValueError('bottom_up_sector_weighted needs sectors: [{"sector": <exact table name>, "weight": w}]')
        for item in sectors:
            row=rows.get(item.get('sector')) if isinstance(item,dict) else None
            if row is None or row.get('is_aggregate'):
                raise ValueError(f'Bottom-up sector {item.get("sector") if isinstance(item,dict) else item!r} is not an '
                                 f'industry name in {table["id"]}; use the exact names in that table')
            for key,value in (('cash_corrected_unlevered_beta',row['cash_corrected_unlevered_beta']),('firms',row.get('firms'))):
                record(f'discount_rate.unlevered_beta.sectors.{item["sector"]}.{key}',item.get(key),value,'pinned from the industry beta table')
                item[key]=value
        for key,value in (('observed_at',table['data']['observed_at']),('date_precision','month')):
            record(f'discount_rate.unlevered_beta.{key}',beta.get(key),value,'industry table observation month')
            beta[key]=value
        refs=list(dict.fromkeys([*(beta.get('source_refs') or []),table['id']]))
        beta['source_refs']=refs
        beta.pop('value',None)
    elif isinstance(beta,dict) and beta.get('calculation')=='historical_issuer_cash_corrected' and regression is not None:
        measured=regression['data']['beta']
        record('discount_rate.unlevered_beta.historical_equity_beta',beta.get('historical_equity_beta'),measured,'pinned from the weekly regression source')
        beta['historical_equity_beta']=measured
    locked.pop('beta_cross_checks',None)
    if regression is not None and isinstance(beta,dict):
        measured=regression['data']['beta']
        def case(name,label,value):
            return {'name':name,'label':label,'unlevered_beta':{'calculation':'historical_issuer_cash_corrected',
                'historical_equity_beta':value,'observed_at':regression['data']['last_return_week_end'],
                'source_refs':[regression['id']],'label':label}}
        checks=[]
        if beta.get('calculation')!='historical_issuer_cash_corrected':
            checks.append(case('regression_beta',f'Five-year weekly regression equity beta {measured:.3f} against '
                               f'{regression["data"].get("benchmark_symbol","the index")}, cash-corrected and relevered',measured))
        checks.append(case('blume_adjusted_regression_beta','Regression beta moved one third toward one (2/3 x measured + 1/3), '
                           'the common adjustment for estimation error',2/3*measured+1/3))
        locked['beta_cross_checks']=checks
    market=next((row for name,row in rows.items() if name.startswith(MARKET_BETA_SECTOR_PREFIX) and row.get('is_aggregate')),None)
    if locked.get('currency')=='USD' and erp is not None and market is not None:
        pinned={'value':market['cash_corrected_unlevered_beta'],'observed_at':table['data']['observed_at'],
                'date_precision':'month','source_refs':[table['id']],
                'label':f'{market["sector"]} cash-corrected unlevered beta: the market\'s average operating-asset beta, '
                        'relevered at this firm\'s leverage for the terminal cost of capital'}
        record('discount_rate.term_structure',rate.get('term_structure'),'glide_to_market_v1','market-consistent rate path')
        record('discount_rate.terminal_unlevered_beta',rate.get('terminal_unlevered_beta'),pinned,'pinned from the industry beta table')
        rate['term_structure']='glide_to_market_v1'
        rate['terminal_unlevered_beta']=pinned
    else:
        rate.pop('term_structure',None); rate.pop('terminal_unlevered_beta',None)
        missing=[name for name,scenario in (locked.get('scenarios') or {}).items()
                 if isinstance(scenario,dict) and scenario.get('terminal_growth') is None]
        if missing:
            raise ValueError('Scenarios '+', '.join(missing)+' need terminal_growth.'+_rate_convention(sources,locked.get('currency')))
        notes.append('The market-consistent rate glide and terminal-growth convention need USD cash flows, the matched '
                     'US risk-free rate and implied equity risk premium, and the market beta from the industry table; '
                     'they are not applied, so one constant discount rate is used.')
    dates=reported.get('input_dates',{})
    anchor=(dates.get('revenue') or {}).get('end_date')
    if anchor and anchor==(dates.get('net_debt') or {}).get('instant'):
        days=(date.fromisoformat(valuation_day)-date.fromisoformat(anchor)).days
        timing={'convention':'stub_v1','anchor_date':anchor}
        try:
            # One rule: the engine's own fiscal-year test decides.
            _valuation_timing({'valuation_timing':timing,'valuation_date':valuation_day})
            usable=True
        except ValueError:
            usable=False
        if usable:
            record('valuation_timing',locked.get('valuation_timing'),timing,'present value dated at the valuation date')
            locked['valuation_timing']=timing
        else:
            locked.pop('valuation_timing',None)
            notes.append(f'The annual balance sheet is {days} days from the valuation date, so no stub adjustment is made; '
                         'values are as of the fiscal year end.')
    else:
        locked.pop('valuation_timing',None)
    return notes


def _rounded(values):
    return [round(v,4) if isinstance(v,(int,float)) and not isinstance(v,bool) else v for v in values] if isinstance(values,list) else values


def _lock_plan(plan, sources, *, apply_conventions=True):
    if not isinstance(plan,dict):
        raise ValueError('Forecast plan must be an object')
    # The forecast schema does not carry valuation_date, so a model-authored
    # plan usually omits it. Defaulting to today then breaks the documented
    # invariant "discount_rate.as_of is the valuation date" whenever the
    # model's dated rate inputs are from an earlier day (e.g. a Nov 6
    # showcase plan run on Nov 7). Derive the valuation day from the
    # discount_rate's own as_of when no explicit valuation_date is supplied,
    # so the dated inputs are preserved instead of silently re-dated.
    # An explicit valuation_date still wins; a mismatch with
    # discount_rate.as_of is left to calculate_discount_rate to reject,
    # so the repair loop can ask the model to fix it.
    rate_as_of = plan.get('discount_rate') if isinstance(plan.get('discount_rate'), dict) else {}
    rate_as_of = rate_as_of.get('as_of') if isinstance(rate_as_of, dict) else None
    if 'valuation_date' in plan:
        valuation_day = plan.get('valuation_date')
    elif isinstance(rate_as_of, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', rate_as_of):
        valuation_day = rate_as_of
    else:
        valuation_day = datetime.now(timezone.utc).date().isoformat()
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
    operating=locked.get('operating_model')
    if (isinstance(operating,dict) and operating.get('amount_unit')==reported['currency'] and
            operating.get('currency')==reported['currency']):
        # Models sometimes write the currency code where the unit belongs
        # (observed live 2026-11-14, twice in one run). A currency code states
        # no scale. Component revenue must still reconcile exactly to pinned
        # revenue, so an operating model in thousands or millions is rejected.
        operating['amount_unit']='currency_units'
        changes.append({'field':'operating_model.amount_unit','previous':reported['currency'],'pinned':'currency_units',
            'source_refs':[],'reason':'currency code written as the unit label; scale is verified by exact component-revenue reconciliation'})
    if isinstance(operating,dict) and isinstance(locked.get('scenarios'),dict):
        # The operating model governs revenue growth, and in gross-profit mode
        # EBIT margins: Python derives both. Aggregate arrays that agree with
        # that arithmetic are redundant and removed (recorded). Arrays that
        # disagree are the author's stated view of a different path: the plan is
        # sent back for repair with both paths rather than silently valuing one
        # and narrating the other (observed live 2026-11-14: 3-4 margin points).
        fields=['growth_rates']+(['ebit_margins'] if operating.get('earnings_mode')=='gross_profit_less_expenses' else [])
        stated={name:{field:scenario[field] for field in fields if field in scenario}
                for name,scenario in locked['scenarios'].items() if isinstance(scenario,dict)}
        if any(stated.values()):
            trial=copy.deepcopy(locked)
            for scenario in trial['scenarios'].values():
                for field in fields:
                    scenario.pop(field,None)
            try:
                derived=derive_operating(trial)
            except (ValueError,KeyError,TypeError):
                derived=None
            if derived is not None:
                conflicts=[]
                for name,arrays in stated.items():
                    for field,values in arrays.items():
                        computed=derived['scenarios'][name][field]
                        if (not isinstance(values,list) or len(values)!=len(computed) or
                                any(not isinstance(v,(int,float)) or isinstance(v,bool) or abs(v-c)>AGGREGATE_TOLERANCE
                                    for v,c in zip(values,computed))):
                            conflicts.append(f'{name}.{field} stated {_rounded(values)} but the operating_model gives {_rounded(computed)}')
                if conflicts:
                    raise ValueError('Aggregate forecast arrays disagree with the operating_model by more than '
                                     f'{AGGREGATE_TOLERANCE:.1%} in some year: '+'; '.join(conflicts)+
                                     '. Omit the aggregate arrays and change the component drivers until they produce '
                                     'the intended path, or adopt the derived path in the rationale. Keep year-1 '
                                     'gross margins and expense amounts consistent with reported_inputs.operating_reference')
                for name,arrays in stated.items():
                    for field in arrays:
                        changes.append({'field':f'scenarios.{name}.{field}','previous':locked['scenarios'][name].pop(field),
                            'pinned':'derived from operating_model','source_refs':[],
                            'reason':'aggregate forecast array matched the governing operating model within tolerance'})
    opted_in=((isinstance(locked.get('discount_rate'),dict) and
               locked['discount_rate'].get('term_structure')=='glide_to_market_v1') or 'valuation_timing' in locked)
    if apply_conventions or opted_in:
        convention_notes=_pin_market_conventions(locked,sources,reported,valuation_day,changes)
    else:
        convention_notes=['This supplied plan keeps its own rate and timing conventions; the market-consistent rate path '
                          'and valuation-date stub apply only when a plan opts in with discount_rate.term_structure or valuation_timing.']
    if 'entry_policy' in locked:
        # Valuation research only (settled owner decision): no entry thresholds.
        changes.append({'field':'entry_policy','previous':locked.pop('entry_policy'),'pinned':None,'source_refs':[],
            'reason':'entry thresholds and margins of safety are outside this valuation-only research'})
    rate=locked.get('discount_rate')
    if isinstance(rate,dict):
        # Dated rate inputs govern WACC, so a scenario WACC beside them is a
        # duplicate the guidance says to omit. Observation dates are calendar
        # dates; a full timestamp keeps its written date. Both are recorded.
        for name,scenario in (locked.get('scenarios') or {}).items():
            if isinstance(scenario,dict) and 'wacc' in scenario:
                changes.append({'field':f'scenarios.{name}.wacc','previous':scenario.pop('wacc'),'pinned':'computed from discount_rate',
                    'source_refs':[],'reason':'scenario WACC duplicated the governing dated discount-rate inputs'})
        for name,record in rate.items():
            stamp=record.get('observed_at') if isinstance(record,dict) else None
            if isinstance(stamp,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|[+-]\d{2}:\d{2})?',stamp):
                record['observed_at']=stamp[:10]
                changes.append({'field':f'discount_rate.{name}.observed_at','previous':stamp,'pinned':stamp[:10],
                    'source_refs':[],'reason':'timestamp shortened to its written calendar date'})
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
        'Finance lease liabilities are assumed zero because the scanned SEC record reports no finance lease liability concept; this is not a reported zero. '
        if locked.get('other_claims_bridge',{}).get('reported_finance_lease_liabilities') is None else
        'Reported finance leases are deducted once in other equity claims, outside the selected financial net-debt subtotal. ')
    if bridge.get('short_term_financing',{}).get('basis')=='identified_categories_house_assumption':
        lease_allocation+=('Short-term financing beyond the identified reported categories is assumed zero because the scanned SEC '
            'record reports no other short-term borrowing amount; an absent concept is not a reported zero, and undisclosed '
            'borrowings would lower equity value. ')
    locked['assumptions_label']=(
        'Growth, margins, taxes, discount rates, capital efficiency, terminal returns and scenario weights '
        'are house forecasts, not reported facts or company guidance. Historical actuals use the '
        'reported-input snapshot. Only the cash and investment assets selected in the pinned bridge are '
        'classified as nonoperating under an explicit house policy. Omitted investment categories are '
        'not reported zeros, and required operating cash is not established as zero. '+lease_allocation+
        'Additional unidentified '
        'claims are assumed zero, not established absent. GAAP EBIT retains operating rent, SBC and R&D. '+
        ('Discount rates move from the current cost of capital toward market-average business risk over years 6-10, and '
         'terminal growth equals the risk-free rate, the long-run growth the equity risk premium assumes, unless a '
         'scenario states a lower rate with a company-specific basis. '
         if (locked.get('discount_rate') or {}).get('term_structure')=='glide_to_market_v1' else '')+
        ('Present value is dated at the valuation date with a first-year stub; net debt stays at the annual balance sheet. '
         if locked.get('valuation_timing') else
         'Annual year-end cash flows are used without an interim balance-sheet roll-forward or stub-period adjustment. ')+
        ' '.join(convention_notes))
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
             '| Scenario | Probability | Discount rate | Terminal growth | Value per share | Value / price |',
             '|---|---:|---:|---:|---:|---:|']
    for name in ('bear','base','bull'):
        scenario, inputs = result['scenarios'][name], plan['scenarios'][name]
        rate = (f'{scenario["initial_rate"]:.2%} → {scenario["terminal_rate"]:.2%}' if 'terminal_rate' in scenario and
                abs(scenario['initial_rate']-scenario['terminal_rate'])>1e-12 else f'{scenario.get("initial_rate", inputs.get("wacc", 0)):.2%}')
        growth = scenario.get('terminal_growth', inputs.get('terminal_growth'))
        ratio = f'{scenario["value_to_price"]:.2f}' if 'value_to_price' in scenario else 'n/a'
        lines.append(f'| {name.title()} | {inputs["probability"]:.1%} | {rate} | {growth:.2%} | {currency} {scenario["per_share_value"]:,.2f} | {ratio} |')
    thin=[name for name in ('bear','base','bull') if 'terminal_rate' in result['scenarios'][name] and
          result['scenarios'][name].get('terminal_spread') is not None and result['scenarios'][name]['terminal_spread']<.02]
    if thin:
        lines += ['', 'Terminal rate within two points of terminal growth in '+', '.join(thin)+': the value is fragile to '
                      'small changes in either.']
    lines += ['',f'Weighted intrinsic value per share: {currency} {result["weighted_per_share_value"]:,.2f}'+
              (f'; value / price {result["weighted_value_to_price"]:.2f}' if 'weighted_value_to_price' in result else ''),
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
    timing=(result.get('discounting') or {}).get('timing',{})
    if timing.get('convention')=='stub_v1':
        lines += ['', 'Measurement dates — '+ '; '.join(measurements)+'.', '',
                  f'Valuation date {timing["valuation_date"]}: present value is discounted to this date, the first forecast '
                  f'year counts only its unexpired {1-timing["stub_years"]:.0%}, and net debt stays at the '
                  f'{timing["anchor_date"]} annual balance sheet.']
    else:
        lines += ['', 'Measurement dates — '+ '; '.join(measurements)+'. '
                  'Balances are not rolled forward to the quote date; the annual cash-flow model has no stub-period adjustment.']
    lines += _continuity_exhibit(result)
    lines += _market_consistency_exhibits(result, currency)
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
    if comparisons and any('terminal_growth' in row for row in comparisons):
        def money(value):
            return f'{value:,.2f}' if isinstance(value,(int,float)) else '—'
        lines += ['', '| Conditional rate method | Initial rate | Terminal rate | Terminal growth | Base value/share | Change from base | Value with house terminal growth |',
                  '|---|---:|---:|---:|---:|---:|---:|']
        for row in comparisons:
            if row.get('per_share_value') is None:
                lines.append(f'| {row["name"].replace("_"," ")} | {row["wacc"]:.2%} | — | — | not calculated | — | — |')
            else:
                lines.append(f'| {row["name"].replace("_"," ")} | {row["wacc"]:.2%} | {row["terminal_rate"]:.2%} | '
                             f'{row["terminal_growth"]:.2%} | {money(row["per_share_value"])} | {row["value_change"]["dollar_delta"]:+,.2f} | '
                             f'{money(row.get("per_share_value_at_house_terminal_growth", row["per_share_value"]))} |')
        lines += ['', 'The named rate inputs change, and with them the whole rate path. Where terminal growth defaults to the '
                      'risk-free rate it moves with the comparison\'s risk-free rate; the last column holds it at the house '
                      'rate. Historical-fit endpoints are not valuation confidence intervals.']
    elif comparisons:
        lines += ['', '| Conditional rate method | WACC | Base value/share | Change from base |',
                  '|---|---:|---:|---:|']
        for row in comparisons:
            if row.get('per_share_value') is None:
                lines.append(f'| {row["name"].replace("_"," ")} | {row["wacc"]:.2%} | not calculated | — |')
            else:
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


def _terminal_growth_sentence(result, risk_free):
    """State terminal growth as computed in each scenario, not as the default alone."""
    scenarios=result.get('scenarios') or {}
    default=[name for name,row in scenarios.items() if row.get('terminal_growth_source')=='risk_free_rate']
    stated=[(name,row) for name,row in scenarios.items() if row.get('terminal_growth_source')!='risk_free_rate']
    if not stated:
        return (f'Terminal growth equals the {risk_free:.2%} risk-free rate in every scenario, the long-run growth the '
                'equity risk premium assumes.')
    parts=[f'{name} states {row["terminal_growth"]:.2%}'+
           (f' ({str(row["terminal_growth_basis"]).strip().rstrip(".")})' if row.get('terminal_growth_basis') else '')
           for name,row in stated if isinstance(row.get('terminal_growth'),(int,float))]
    lead=(f'Terminal growth equals the {risk_free:.2%} risk-free rate, the long-run growth the equity risk premium assumes, in '
          +', '.join(default)+'; ' if default else f'The {risk_free:.2%} risk-free rate is the terminal-growth default; ')
    return lead+'; '.join(parts)+'.'


def _continuity_exhibit(result):
    continuity=result.get('forecast_continuity')
    if not continuity:
        return []
    refs=', '.join(continuity['source_refs'])
    cells='; '.join(f'{name} {row["year_one_ebit_margin"]:.1%} ({row["change_percentage_points"]:+.1f} pt)'
                    for name,row in continuity['scenarios'].items())
    ytd=continuity.get('year_to_date')
    lines=['', '### Year-1 margin against the last reported results', '',
           f'Reported operating margin {continuity["reported_operating_margin"]:.1%} for the fiscal year ending '
           f'{(continuity.get("period") or {}).get("end_date","")} [{refs}]'+
           (f'; {ytd["operating_margin"]:.1%} for the year to date ending {(ytd.get("period") or {}).get("end_date","")} '
            f'[{", ".join(ytd["source_refs"])}]' if ytd else '')+f'. Year-1 EBIT margin: {cells}.']
    for name,row in continuity['scenarios'].items():
        if row.get('stated_basis') and not row.get('within_band'):
            lines.append(f'- {name.title()} year-1 step, stated basis: {row["stated_basis"]}')
    return lines


def _market_consistency_exhibits(result, currency):
    """Computed exhibits for what the price implies and where the difference comes from."""
    lines=[]
    discounting=result.get('discounting') or {}
    if discounting.get('convention')=='glide_to_market_v1':
        path=', '.join(f'{rate:.2%}' for rate in discounting['base_rate_path'])
        lines += ['', '### Discount-rate path', '',
            f'Base case, years 1-{len(discounting["base_rate_path"])}: {path}; terminal {discounting["terminal_rate"]:.2%}. '
            f'The terminal rate uses the market operating-asset beta {discounting["terminal_unlevered_beta"]:.2f} relevered at this '
            f'firm\'s leverage ({discounting["terminal_relevered_beta"]:.2f}). Damodaran\'s spreadsheet default would be '
            f'{discounting["damodaran_default_terminal_rate"]:.2%} (risk-free rate plus equity risk premium). '+
            _terminal_growth_sentence(result,discounting["risk_free_rate"])]
    implied=result.get('implied_return_at_price')
    if implied:
        lines += ['', '### What the price implies', '',
            '| Scenario | Implied return at the price | Required return (flat equivalent) | Difference | Terminal spread at implied return |',
            '|---|---:|---:|---:|---:|']
        def cell(value, pattern):
            return pattern.format(value) if isinstance(value,(int,float)) else 'not solved'
        for name,row in implied['scenarios'].items():
            spread=row.get('terminal_spread_at_implied_return')
            flag=' (fragile)' if row.get('fragile') else ''
            lines.append(f'| {name.title()} | {cell(row.get("implied_return"),"{:.2%}")} | {cell(row.get("required_return_flat_equivalent"),"{:.2%}")} | '
                         f'{cell(row.get("gap_percentage_points"),"{:.2f} pt")} | {cell(spread,"{:.2%}")}{flag} |')
        weighted=implied['probability_weighted_value']
        lines.append(f'| Probability-weighted value | {cell(weighted.get("implied_return"),"{:.2%}")} | '
                     f'{cell(weighted.get("required_return_flat_equivalent"),"{:.2%}")} | {cell(weighted.get("gap_percentage_points"),"{:.2f} pt")} | |')
        lines += ['', 'The implied return is the one discount rate, applied in every year and in the terminal value, at which value '
                      'equals the price; the required return is the same calculation at the house value. This describes what the '
                      'price implies, not a forecast of shareholder returns. A terminal spread under two points marks a fragile '
                      'result.']
    panel=result.get('convention_panel')
    if panel:
        lines += ['', '### Value under alternative conventions (base case, one change at a time)', '',
                  '| Alternative | Value per share | Change | Status |', '|---|---:|---:|---|']
        for row in panel['rows']:
            value=f'{currency} {row["per_share_value"]:,.2f}' if row.get('per_share_value') is not None else '—'
            change=f'{row["dollar_delta"]:+,.2f}' if row.get('dollar_delta') is not None else '—'
            status=row['status'].replace('_',' ')+(' (sensitivity)' if row.get('kind')=='sensitivity' else '')
            lines.append(f'| {row["label"]} | {value} | {change} | {status} |')
        if panel.get('largest_meaningful_step'):
            lines += ['', f'Largest convention step: {panel["largest_meaningful_step"].replace("_"," ")}. Rows carry no probability and are never tuned to the price.']
    checks=result.get('beta_cross_checks') or []
    if checks:
        lines += ['', '| Beta cross-check | Unlevered beta | Initial discount rate | Base value | Change |', '|---|---:|---:|---:|---:|']
        for row in checks:
            if row.get('status')=='calculated':
                lines.append(f'| {row["label"]} | {row["unlevered_beta"]:.3f} | {row["wacc"]:.2%} | {currency} {row["per_share_value"]:,.2f} | '
                             f'{row["value_change"]["dollar_delta"]:+,.2f} |')
            else:
                lines.append(f'| {row["label"]} | — | — | not calculated | — |')
    return lines


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
    latest = state['review_history'][-1] if state.get('review_history') else {}
    weakness = latest.get('central_weakness')
    if isinstance(weakness, dict):
        note += (f"\n\nReviewer's central weakness: {weakness['claim']} Computed effect: {weakness['computed_effect']}. "
                 f"Memo handles it: {weakness['memo_handles_it']}. Difference between value and price explained: "
                 f"{latest.get('gap_explained','not stated')}.")
    if latest.get('review_model'):
        same = latest.get('review_model') == latest.get('author_model')
        note += (f"\n\nReview model: {latest['review_model']}" +
                 (f"; author model: {latest['author_model']}" if latest.get('author_model') else '') +
                 ('. The same model wrote and reviewed this memo.' if same else '.'))
    return (memo.rstrip() + '\n' + valuation_section(plan,result) + '\n## Source register\n'+refs+
            '\n\n## Evidence limitations\n'+ ('\n'.join('- '+g for g in gaps) or 'No retrieval gap recorded; source completeness still requires review.')+
            '\n\n## Review status\n'+state.get('quality_status','in_progress')+
            '\n\n'+note+
            '\n\nSoftware checks and model reviews do not establish investment truth or forecast accuracy.\n')


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
        default_reviewer=_default_reviewer(profile,strong)
        return _run(folder,ticker,key,easy,strong,budget,evidence,progress,max_revisions,review_notes,extra_sources or [],counterfactuals,draft,review_model,source_snapshot,forecast_plan,default_reviewer=default_reviewer)


def _run(folder,ticker,key,easy,strong,budget,evidence,progress,max_revisions,review_notes,extra_sources,counterfactuals,draft,review_model,source_snapshot=None,forecast_plan=None,default_reviewer=None):
    default_reviewer=default_reviewer or strong
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
    reviewer=review_model or previous_reviewer or default_reviewer
    if reviewer!=state.get('review_model',default_reviewer):
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
            state['valuation']=_evaluate(state['plan'])
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
        state.pop('plan_conventions',None)
    state.pop('coverage_preflight_failed',None)
    if forecast_plan is not None and (changed_forecast or not state.get('plan')):
        supplied,changes=_lock_plan(forecast_plan,state['sources'],apply_conventions=False)
        if 'requested_counterfactuals' in state:
            supplied['counterfactuals']=copy.deepcopy(state['requested_counterfactuals'])
        _validate_plan_sources(supplied,state['sources'])
        state['valuation']=_evaluate(supplied)
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
                        'Separate facts from hypotheses; do not invent source contents. '
                        'Answer in at most about 900 words.',tokens=WEB_RESEARCH_TOKENS,web=True)
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
    stale=[source['id'] for source in state['sources'] if isinstance(source,dict) and isinstance(source.get('data'),dict)
           and source['data'].get('fact_origin')=='sec_companyfacts' and
           (source['data'].get('selection') or {}).get('concepts_sha256')!=CONCEPT_SELECTION_SHA256]
    if stale:
        note=(f'SEC facts {", ".join(stale)} were collected under an earlier concept list, so reported cost and expense '
              'lines may be missing from the operating reference. Run in a new --output folder to collect them.')
        if note not in state['gaps']:
            state['gaps'].append(note)
    if 'plan' not in state:
        prompt=('Build an investment valuation plan for '+ticker+'. Ground actuals in supplied evidence; '
                'use primary reports where accessible, and reconcile secondary-series dates and definitions.\n'+
                FORECAST_SCHEMA+_rate_convention(state['sources'],reported.get('currency'))+UNDERWRITING_GUIDANCE+PLAN_FIELD_RULES+'\nIMMUTABLE reported inputs (copy these values exactly; forecast judgments only):\n'+json.dumps(reported)+'\nEvidence:\n'+context+'\nDiligence synthesis:\n'+thesis+'\nRetrieval gaps:\n'+gaps)
        text=call('valuation_plan',strong,prompt,tokens=12000)
        failures=[]
        for repair in range(PLAN_REPAIRS+1):
            try:
                # A valid plan behind a line of analysis was rejected live
                # (2026-11-14) and cost a paid repair; accept exactly one
                # complete fenced block, as the correction stage does.
                plan,normalized=_response_json(text)
                if normalized:
                    recovery={'stage':'valuation_plan'+('' if repair==0 else '_repair'),'attempt':repair,
                              'method':'one_explicit_complete_json_fence'}
                    if recovery not in state.setdefault('response_normalizations',[]):
                        state['response_normalizations'].append(recovery)
                if not isinstance(plan,dict):
                    raise ValueError('Valuation plan must be an object')
                if plan.get('unavailable'):
                    state['valuation_missing']=plan
                    state['plan']=plan
                    break
                (plan,changes,result),omitted=_omit_invalid_optional_sections(plan,lambda candidate:_lock_and_value(candidate,state))
                state.setdefault('actual_input_corrections',[]).extend(changes)
                _reset_plan_notes(state)
                _record_omitted_sections(state,omitted,'forecast plan\'s')
                state['plan'],state['valuation']=plan,result
                state['plan_conventions']=PLAN_CONVENTIONS
                save_valuation(folder,plan,result)
                break
            except (ValueError,KeyError,TypeError,AttributeError) as exc:
                failures.append(str(exc))
                if repair==PLAN_REPAIRS:
                    # A saved response is replayed on resume. Keeping a plan
                    # that cannot validate would stop every later attempt at
                    # the same point, so the rejected plan and its repairs are
                    # discarded; their recorded spending is retained.
                    for name in [name for name in state['stages'] if name.startswith('valuation_plan')]:
                        state['stages'].pop(name)
                    checkpoint()
                    raise RuntimeError(f'Valuation plan remains invalid after {PLAN_REPAIRS} repairs: '+str(exc)+
                                       '. The rejected plan was discarded; run the same command again for a fresh forecast') from None
                text=call('valuation_plan_repair'+('' if repair==0 else f'_{repair+1}'),strong,prompt+'\nRejected JSON:\n'+text+
                          '\nRepair this precise validation failure without inventing actuals: '+str(exc)+
                          ('\nEarlier attempts were rejected for these reasons; do not reintroduce them: '+
                           ' | '.join(failures[:-1]) if failures[:-1] else '')+
                          '\nReturn only the complete corrected JSON object, with no analysis before or after it.'+PLAN_FIELD_RULES,tokens=12000)
        checkpoint()
    if not state['plan'].get('unavailable'):
        current=not state.get('provided_forecast_digest') and state.get('plan_conventions')==PLAN_CONVENTIONS
        if not state.get('provided_forecast_digest') and not current:
            _add_plan_note(state,LEGACY_PLAN_NOTE)
        state['plan'],changes=_lock_plan(state['plan'],state['sources'],apply_conventions=current)
        state.setdefault('actual_input_corrections',[]).extend(changes)
        if 'requested_counterfactuals' in state:
            state['plan']['counterfactuals']=copy.deepcopy(state['requested_counterfactuals'])
        state['valuation']=_evaluate(state['plan'])
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
            ', '+MEMO_WORDS+', with clear headings. '+SECTIONS+
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
            'Report value, its relation to the price and sensitivities only. Write value / price, not upside or '
            'downside; give no entry thresholds, margins of safety, target prices or recommendations. The '
            'computed implied return at the price describes what the price implies, not a forecast of '
            'shareholder returns. '
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
            '\nSynthesis:\n'+thesis+'\nValuation:\n'+valuation_context+'\nGaps:\n'+gaps,tokens=MEMO_TOKENS)
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
            'and consistent return comparisons, and repurchase economics against current house value. '
            'Recompute any reviewer-proposed number from the computed output before adopting it; if it '
            'disagrees, keep the computed figure. Do not resolve a finding by inventing '
            'a policy discount, nonrecurring adjustment, source passage or double-counted cash distribution. '
            'Return the full revised memo only, '+MEMO_WORDS+'. Keep the required section order.\nEvidence:\n'+context+
            ('\nThe forecast correction for these findings was rejected; the valuation below is unchanged. Do not '
             'describe any forecast change as made.' if isinstance(candidate,dict) and candidate.get('correction_rejected') else '')+
            ('\nNotes on the plan now valued:\n'+'\n'.join(state.get('plan_gap_notes',[])) if state.get('plan_gap_notes') else '')+
            '\nValuation:\n'+valuation_context+'\nMemo:\n'+memo+'\nFindings:\n'+json.dumps(combined)+'\nAdjudicated model response:\n'+json.dumps(candidate),tokens=MEMO_TOKENS)
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
                'convention. Flag entry thresholds, margins of safety, target prices, upside/downside or '
                'recommendation language as major: this is valuation research only. '
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
                'First identify, from implied_return_at_price, convention_panel and calibration comparisons, which '
                'assumption family carries the difference between value and price, and check that the conclusion, '
                'the price-implication section and the variant thesis name it and argue it with evidence. Check '
                'every comparison of a number with the market price by arithmetic. Flag any number that is not in '
                'the evidence or the computed output as major, and never write a fix that asks the author to '
                'compute a value the engine has not computed. '
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
                try:
                    review_value,normalized=_response_json(raw)
                    review=parse_review(json.dumps(review_value),memo=visible,anchor_embellished=True)
                except ValueError as failed:
                    # A saved response is replayed on resume. Keeping a review
                    # that cannot be validated would stop every later attempt
                    # here, so it is discarded (both raw texts stay on disk and
                    # the spending stays in the ledger) and the next attempt
                    # requests a fresh review.
                    for name in [name for name in state['stages']
                                 if name.split(':')[0] in (f'review_{iteration}',f'review_format_{iteration}')]:
                        state['stages'].pop(name)
                    checkpoint()
                    raise ValueError(str(failed)+'. The rejected review was discarded; run the same command again '
                                     'for a fresh review') from None
            if normalized:
                recovery={'stage':f'review_{iteration}','method':'one_explicit_complete_json_fence'}
                if recovery not in state.setdefault('response_normalizations',[]):
                    state['response_normalizations'].append(recovery)
            known={s['id'] for s in state['sources']}
            for finding in review['findings']:
                if not set(finding['source_refs']).issubset(known):
                    raise RuntimeError('Reviewer cited a source outside the research snapshot')
            combined=review['findings']+deterministic+assessment_findings(review)
            effective='revise' if any(f.get('severity') in {'blocker','major'} for f in combined) else review['verdict']
            record={'iteration':iteration,'verdict':effective,'model_verdict':review['verdict'],'findings':combined,'manifest':manifest,
                    'diagnostic_scope':DIAGNOSTIC_SCOPE,'review_model':reviewer,'author_model':strong}
            for key in ('central_weakness','gap_explained'):
                if key in review:
                    record[key]=review[key]
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
             'Find exact dated source passages; do not repeat unverified assertions. '
             'Answer in at most about 900 words. Findings:\n'+json.dumps(major),
             tokens=WEB_RESEARCH_TOKENS,web=True)
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
        apply=not state.get('provided_forecast_digest')
        rate_text=_correction_rate_text(state['sources'],plan,apply)
        model_response=call(f'model_response_{iteration+1}',strong,
            'Address these review findings. Return ONLY JSON {"unchanged":true,"reason":"..."} '
            'if the existing valuation plan is still appropriate, otherwise return the COMPLETE corrected '
            'forecast proposal in the schema below. Do not change source-supported actuals just to get a pass. '
            'First verify each reviewer accusation against the actual memo, sources and model. Reject unsupported '
            'accusations or suggested fixes instead of replacing one unsupported story with another. A valid '
            'prose-only finding may leave forecasts unchanged; label it memo_only rather than rejecting it. Include compact '
            'finding_dispositions mapping finding IDs to accepted/rejected/clarified with source-backed reasons. '
            'Preserve or explicitly rejustify the repurchase illustrations when forecasts change; '
            'do not silently drop their scope or invent a discount to satisfy a review. '
            'Forecast changes must be explicit house assumptions and economically justified. '
            +FORECAST_SCHEMA+rate_text+UNDERWRITING_GUIDANCE+PLAN_FIELD_RULES+
            '\nEvidence:\n'+context+'\nCurrent memo (verify allegations against this exact text):\n'+memo+'\nCurrent forecast proposal:\n'+json.dumps({k:plan[k] for k in
                ('assumptions_label','scenario_rationales','scenarios','operating_model','discount_rate','monitoring',
                 'counterfactuals','calibration_comparisons','forecast_periods','repurchase_tests',
                 'margin_continuity_basis','valuation_timing') if k in plan})+
            '\nLocked actuals (reference only; omit from your proposal):\n'+json.dumps(plan.get('actuals',{}))+
            '\nReported operating lines (reference for year-1 continuity):\n'+
            json.dumps((plan.get('reported_inputs') or {}).get('operating_reference'))+
            '\nComputed diagnostics for verification:\n'+json.dumps((result or {}).get('computed_diagnostics',{}))+
            '\nComputed counterfactuals:\n'+json.dumps((result or {}).get('counterfactuals',{}))+
            '\nComputed reverse-DCF projections:\n'+json.dumps((result or {}).get('reverse_dcf',{}).get('solution_projections',[]))+
            '\nFindings:\n'+json.dumps(combined),tokens=12000)
        def parsed(text, stage):
            try:
                value,normalized=_response_json(text)
            except ValueError as error:
                return None,str(error)
            if normalized:
                recovery={'stage':stage,'method':'one_explicit_complete_json_fence'}
                if recovery not in state.setdefault('response_normalizations',[]):
                    state['response_normalizations'].append(recovery)
            return value,None
        candidate,parse_error=parsed(model_response,f'model_response_{iteration+1}')
        if parse_error:
            model_response=call(f'model_response_format_{iteration+1}',easy,
                'Reformat this existing model response as exactly ONE compact JSON value. Remove analysis and '
                'Markdown outside the proposal. Preserve every field, number, forecast and finding disposition; '
                'do not add facts or change the proposal. Return JSON only.\nResponse:\n'+model_response,tokens=12000)
            candidate,parse_error=parsed(model_response,f'model_response_format_{iteration+1}')
        # Every malformed or invalid correction goes through the same bounded
        # repair as the first plan. If none validates, the prior valuation
        # stands and the run says so, rather than ending after a paid draft
        # and review, or replaying the same failure on every resume.
        original=copy.deepcopy(candidate) if isinstance(candidate,dict) else model_response
        prior_valuation=(plan,result) if result is not None else None
        correction,failures,decision,locked,omitted=model_response,[],'rejected',None,{}
        for repair in range(PLAN_REPAIRS+1):
            try:
                if parse_error:
                    raise ValueError(parse_error)
                if not isinstance(candidate,dict):
                    raise ValueError('The model correction must be one JSON object')
                if candidate.get('unchanged') is not None and not isinstance(candidate['unchanged'],bool):
                    raise ValueError('The unchanged flag must be true or false')
                if candidate.get('unchanged') is True:
                    decision='unchanged'
                    break
                if candidate.get('unavailable'):
                    decision='unavailable'
                    break
                proposal={k:v for k,v in candidate.items() if k not in ('finding_dispositions','unchanged','reason')}
                (locked,changes,corrected),omitted=_omit_invalid_optional_sections(
                    proposal,lambda value:_lock_and_value(value,state,apply_conventions=apply,prior=prior_valuation))
                decision='corrected'
                break
            except (ValueError,KeyError,TypeError,AttributeError) as exc:
                failures.append(str(exc))
                if repair==PLAN_REPAIRS:
                    break
                stage=f'model_response_repair_{iteration+1}'+('' if repair==0 else f'_{repair+1}')
                correction=call(stage,strong,
                    'Repair this corrected valuation plan. Return only the complete corrected JSON object, keeping '
                    'finding_dispositions, or {"unchanged":true,"reason":"..."} with finding_dispositions to keep the '
                    'current plan.'+FORECAST_SCHEMA+rate_text+CONTINUITY_RULE+PLAN_FIELD_RULES+
                    '\nValid source IDs: '+'; '.join(f"{source['id']} {str(source.get('title',''))[:80]}" for source in state['sources'])+
                    '\nRejected response:\n'+(correction if isinstance(correction,str) else json.dumps(correction))+
                    '\nRepair this precise validation failure without inventing actuals: '+str(exc)+
                    ('\nEarlier attempts were rejected for these reasons; do not reintroduce them: '+
                     ' | '.join(failures[:-1]) if failures[:-1] else ''),tokens=12000)
                candidate,parse_error=parsed(correction,stage)
        dispositions=(candidate.get('finding_dispositions') if isinstance(candidate,dict) else None) or \
                     (original.get('finding_dispositions') if isinstance(original,dict) else None)
        if decision=='unchanged':
            adjudication=copy.deepcopy(candidate)
            if repair:
                adjudication['correction_repairs']=repair
        else:
            if decision in ('unavailable','corrected'):
                backup=folder/'revisions'/f'model-before-{iteration+1}'
                backup.mkdir(parents=True,exist_ok=True)
                model_files=['assumptions.json','valuation.json','cashflows.csv','sensitivity.csv']
                for name in model_files:
                    if (folder/name).exists() and not (backup/name).exists():
                        shutil.copyfile(folder/name,backup/name)
            if decision=='unavailable':
                for name in model_files:
                    (folder/name).unlink(missing_ok=True)
                plan,result={k:v for k,v in candidate.items() if k!='finding_dispositions'},None
                state.pop('valuation',None)
                adjudication=copy.deepcopy(candidate)
            elif decision=='corrected':
                state.setdefault('actual_input_corrections',[]).extend(changes)
                _reset_plan_notes(state)
                _record_omitted_sections(state,omitted,f'review {iteration+1} correction\'s')
                plan,result=locked,corrected
                save_valuation(folder,plan,result)
                state['valuation']=result
                if apply:
                    state['plan_conventions']=PLAN_CONVENTIONS
                # What the memo author receives is the plan that was valued.
                adjudication={'finding_dispositions':dispositions,
                              'accepted_proposal':{k:copy.deepcopy(plan[k]) for k in
                                  ('assumptions_label','scenario_rationales','scenarios','operating_model','discount_rate',
                                   'margin_continuity_basis','valuation_timing','counterfactuals','calibration_comparisons',
                                   'monitoring','repurchase_tests') if k in plan}}
                if omitted:
                    adjudication['omitted_sections']=omitted
                if repair:
                    adjudication.update(correction_repairs=repair,original_proposal=original)
            else:
                reason=failures[-1][:500] if failures else 'no valid correction'
                _add_plan_note(state,f'The model correction after review {iteration+1} remained invalid after {PLAN_REPAIRS} '
                                     f'repairs ({reason[:300]}); the prior valuation stands and the memo is revised against it.')
                adjudication={'finding_dispositions':dispositions,
                              'correction_rejected':{'reason':reason,'repairs':PLAN_REPAIRS},
                              'note':'The forecast correction was rejected and the prior valuation stands. No forecast '
                                     'change named in these dispositions took effect; treat them as findings the memo '
                                     'must address in prose against the unchanged valuation.'}
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

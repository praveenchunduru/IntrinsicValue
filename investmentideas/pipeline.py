"""Full portable research: archived evidence, editable DCF, bounded review/revision.

The model proposes forecasts; Python evaluates the valuation. Model reviews are
recorded assessments, never the original system's signed publication authority.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
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

from . import base_rates, core
from .costs import cost_summary
from .analytics import annual_metrics
from .fundamentals import yahoo_financials
from .inputs import canonical_reported_inputs, operating_company_gate, pin_reported_inputs
from .quality import (DIAGNOSTIC_SCOPE, HOUSE_ATTRIBUTION, RUBRIC, TRADING_TERMS, assessment_findings, claim_ledger,
                      inspect_memo, parse_json, parse_review, prereview_checks, prose_words, trace_pools, untraced_figures)
from .research import MAX_DOCUMENT_BYTES, gather_primary, inline_breakdown_facts, inline_facts, archive_focus_excerpts
from .evidence_tables import (breakdown_tables, filing_supplement_for, historical_baseline, render_baseline, render_roic,
                              render_breakdowns, render_quarterly)
from .macro import gather_us_calibration
from .roic import _consecutive as roic_consecutive
from . import country_risk
from .market_beta import gather_weekly_beta
from .prompt_context import valuation_context as render_valuation_context
from .primary_debt import classify_primary_debt
from .operating import derive as derive_operating
from .valuation import (_timing as _valuation_timing, evaluate, materialize, sales_to_capital_path, save_valuation,
                        terminal_rates)
from .companyfacts import CONCEPT_SELECTION_SHA256

VERSION = 'full-2'
# The allowance is a ceiling, not the expected bill. Each call reserves its prompt
# bytes at the model's highest price tier before it is sent; with GPT-5.6 Sol a
# review reserved about $5 (GPT-6.2 Sol, the default since 2026-11-26, about half), so a run with one revision needs about $15 of headroom
# although it is expected to cost about $4.50 (Opus review, 2026-11-15).
DEFAULT_BUDGET = 20.0
SECTIONS = '''Write these sections, in this order, as prose around the computed valuation summary that
Python places above your text (do not re-type its tables; refer to its rows by name):
(1) Valuation conclusion (about 150 words). The summary above already states the values, what the
price implies, which inputs alone can and cannot bring value to the price, and the convention step
the value is most sensitive to; do not restate or reinterpret those figures, and do not assign the
difference between value and price to causes beyond that computed account. Say in plain words what
the house concludes and which evidence the conclusion rests on most, citing summary rows by name.
(2) Variant view (about 400 words): on the inputs that can close the difference, why the house
forecast differs from what the price requires, with primary evidence. Answer the strongest sourced
opposing case with operating evidence (demand, pricing, margins, capital needs). If the operating
forecast is close to consensus, say so.
(3) Business economics, industry and competition (about 450 words), from primary sources and the
computed tables.
(4) Management, capital allocation and financial history (about 450 words), from the reported
history and breakdown tables; compare buybacks with house value using computed figures.
(5) Forecast and scenarios (about 500 words): how the base case (the headline, the expected path) and
the bear and bull stress cases start from reported results and what drives each. Scenario drivers are house assumptions:
say so, and cite a source only for the observed fact behind each. Outside estimates appear only as
labelled unverified context.
(6) Observations that would change the value (about 300 words), from the computed monitoring cases.
(7) Risks, including evidence that the valuation framework is wrong (about 300 words).
(8) Evidence gaps and unresolved questions (about 200 words).'''
# Memo stages rewrite the full memo. A live revision overran a 14,000-token
# limit as the memo grew, so the limit leaves room above the word budget.
MEMO_TOKENS = 18000
MEMO_WORDS = 'about 3,000 words of prose in total; Python adds the tables, and a revision replaces text rather than adding to it'
FORECAST_SCHEMA = """Return ONLY compact JSON for house forecasts:
{"assumptions_label":"House forecasts, not reported facts or guidance",
 "scenario_rationales":{"bear":"Evidence and economic mechanism for this scenario [S#]","base":"...","bull":"..."},
 "scenarios":{"bear":{"probability":<decimal>,"tax_rate":<decimal>,"sales_to_capital":<number>},
 "base":{<same fields>},"bull":{<same fields>}}} (Python sets terminal_roic)
sales_to_capital is one ratio for every forecast year, or {"year1":x,"target":y,"target_year":n}
(a straight line to the target, then held) when capital intensity changes, as in a capex cycle.
plus EITHER an operating_model (preferred when the evidence reports revenue by product, service or
segment; see below), in which case Python derives each scenario's growth and margins from its drivers
and any stated growth_rates or ebit_margins are dropped, OR, without one, "growth_rates":[<10 annual
decimals>] and "ebit_margins":[<10 annual decimals>] in every scenario.
Placeholders mark where your evidence-based numbers go; nothing here is a default.
Start from the COMPUTED FROM REPORTED FILINGS tables: year-1 growth, gross and operating margins,
expense levels, tax rate and capital intensity begin at the reported history and year to date, and
each departure needs evidence in scenario_rationales. The reported fiscal quarters give the latest
growth, margin, inventory and deferred revenue, the run-rate the first forecast years start from.
Use 10 forecast years, the same horizon in all scenarios. The base case is the headline value and
must be written as the expected path; bear and bull are stress cases without probabilities (give
probability 0, 1 and 0; Python sets them). Never cite frequencies, base rates or historical patterns
the evidence does not contain. All rates are decimals.
The RATE CONVENTION FOR THIS RUN, stated after these instructions, says whether to give
scenario wacc and terminal_growth.
Keep each rationale under 100 words. Do not reproduce historical actuals, cash/debt
reconciliations, source passages, calculated cashflows or valuation targets: Python
locks reported inputs and independently computes the outputs.
The unlevered operating-company model uses GAAP EBIT less operating taxes and
incremental revenue / sales-to-capital reinvestment. Maintenance investment offsets
D&A; GAAP EBIT already expenses SBC, R&D and operating rent. Do not deduct those
again. Explicitly justify growth/margin fade, capital efficiency, tax and WACC.
Terminal return on new capital is a house convention that Python sets: the terminal discount
rate (Mauboussin's steady state). The 10 forecast years are a house horizon, not a measured
competitive advantage period (Python values 5, 15 and 20 years and the industry fade beside it):
excess returns on new investment come only from the forecast years' growth and capital
efficiency, and after year 10 growth adds no value.
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
"gross_margins":[0.7,0.7,0.7,0.7,0.7]}},"expense_shares":{"research_and_development":{"year1":0.08,"target":0.08,
"target_year":5},"selling_general_administrative":{"year1":0.04,"target":0.04,"target_year":5}}},"base":"same complete object structure",
"bull":"same complete object structure"}}. Example amounts are not issuer forecasts.
Starting category revenue must reconcile exactly to reported total revenue. Gross margin arrays
are forward assumptions; never call unreported individual category margins observed facts.
reported_inputs.operating_reference gives the last reported year's gross profit, cost of revenue,
R&D, selling_general_administrative, operating expenses (excluding cost of revenue) and operating
income, and the year to date of the first forecast year where reported. Start year-1 gross margins
and expense shares from the most recent reported run-rate. Give each expense line as a share of revenue
(expense_shares: year1, target and target_year when the target is reached, or one share per forecast
year); Python builds the dollar amounts from the scenario's revenue. SG&A is one line,
selling_general_administrative, including selling, marketing, general and administrative costs.
An R&D or SG&A line left out holds its reported share (zero R&D when none is reported); without
expense_shares every reported operating cost share is held. A year-1 EBIT margin more than 5 points
(less for low-margin businesses) from both the reported annual and year-to-date operating margins is
rejected unless margin_continuity_basis (an object keyed by bear, base, bull) explains that
scenario's step with evidence [S#] from the sources. Reinvestment starts from the reported run-rate
too: a scenario whose net reinvestment over years 1-3 (revenue change / sales_to_capital) is below
two thirds of three years at the last reported net reinvestment, or whose sales-to-capital over those
years is above 1.5 times the reported incremental rate, is rejected unless capital_efficiency_basis
(keyed by bear, base, bull) explains it with evidence [S#]. Final-year EBIT margins have hard ceilings with no exception: bear at most the
reported five-year average operating margin, base and bull at most the reported five-year peak plus
5 points (less for low-margin businesses, as in the year-1 check).
Use aggregate_ebit_margins mode without gross margins/expenses if only category revenues are supported.
Omit main scenario growth_rates and, in gross mode, ebit_margins: Python derives them.
When the evidence includes the matched risk-free rate and implied equity risk premium, a dated
discount_rate is required (rates.py schema). You supply unlevered_beta, pretax_cost_of_debt and
marginal_tax_rate, and outside US-dollar plans a risk_free_rate and equity_risk_premium in the
cash-flow currency; Python supplies the other records, as described below. marginal_tax_rate is the statutory
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
Every record you write needs value, observed_at, source_refs and label; currency/amount_unit match
the plan, as_of is the valuation date, lease_convention is gaap_operating. Omit main WACC when this
calculation governs. Label sector/yield proxies and differing dates; do not invent a calibration.
Python copies market_equity (quote times reported shares), financial_debt and finance_lease_debt
from the reported bridge, and, for US-dollar plans with the implied premium in the evidence, the
risk_free_rate and equity_risk_premium from that source. You supply unlevered_beta,
pretax_cost_of_debt and marginal_tax_rate; the other records may be omitted.
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
source_refs, direction ("lowers_value" or "raises_value": what crossing the threshold does to value)
and exactly one of counterfactual(name) or calibration_comparison(name); the linked case must move
value in that direction.
Python supplies each conditional value and change.
These are analyst-selected decision thresholds, not company guidance or automatic forecasts.
Justify reinvestment.
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
"severity":"blocker"|"major"|"minor","rule":"the quality-standard rule ID the defect breaks, such as M3","issue":"precise defect and location",
"source_refs":["S1"],"current_excerpt":"one contiguous verbatim quote from the CURRENT draft, at most 1200 characters, no added quotation marks or ellipses; for an omission, the heading where the content belongs","fix":"specific correction or evidence needed"}]}.
Findings are unresolved; use revise if any blocker/major exists. Use pass only when no material
finding remains. memo_handles_it and gap_explained are recorded for the reader; raise a finding only
for a defect under a named rule. Severity follows the computed value effect: blocker when it changes the conclusion or the
main source of the difference between value and price; minor when under 2% of base value. Do not manufacture defects to fill a quota, and verify every accusation
against the actual draft. Valid source IDs alone do not prove a claim is supported.'''


# A second, fresh read of every blocker or major before it costs a revision.
# Live reviews (2026-11-14) drew different majors from one draft, and one major
# accused a sentence of doing what it did not do. Python's own findings are
# facts and are not re-checked.
FINDING_CHECK = ('Check each review finding below against the draft, the evidence and the Python valuation. A finding '
    'is upheld when its quoted text is in the draft, that text breaks the named rule of the quality standard, and the '
    'evidence or computed output supports the accusation. Refute it only when the draft, the evidence or the computed '
    'output plainly shows it is wrong: the draft already does what the finding asks, the quoted text does not break '
    'the named rule, the figure is in the computed output or a primary source, or the finding asks for something the '
    'standard does not require. When unsure, uphold it. Do not add findings or change severities. Return ONLY JSON '
    '{"checks":[{"id":"F1","upheld":true|false,"reason":"one sentence naming the draft text, source or computed '
    'figure that decides it"}]}.')
FINDING_CHECK_TOKENS = 4000
# Broker-dealers, card issuers and money transmitters run as operating companies
# only when they report an operating-income line (Goldman Sachs does not).
FINANCIAL_SIC_NEEDING_OPERATING_INCOME = frozenset({'6099', '6141', '6153', '6199', '6211'})
EVIDENCE_ABOVE = '\nEvidence: in the evidence block above.'


def _evidence_prefix(context):
    """The evidence block every Claude stage shares, first in the prompt so the provider can cache it."""
    return 'Evidence:\n'+context+'\n'


def _review_prefix(context, valuation_context, visible):
    """Evidence, valuation and the fenced draft: identical for the review and its finding check."""
    return (_evidence_prefix(context)+'Valuation:\n'+valuation_context+
            '\nDraft (quote only text between the markers):\n<<<DRAFT\n'+visible+'\nDRAFT>>>\n')


def _carried_refutations(state, iteration, manifest):
    """(rule, excerpt) -> reason for model-check refutations that still apply to this review.

    Only the model check's refutations carry (Python's are recomputed), only from
    earlier reviews of the same valuation in the same review cycle, and never for
    a heading quoted for an omission: a later, different finding on the same text
    was otherwise refuted without a check and the draft passed (Opus review, 2026-11-15).
    """
    carried={}
    for record in state.get('review_history',[]):
        if not (state.get('revision_base',0)<=record['iteration']<iteration):
            continue
        if (record.get('manifest') or {}).get('valuation_sha256')!=manifest.get('valuation_sha256'):
            continue
        for finding in (record.get('finding_check') or {}).get('refuted',[]):
            excerpt=finding.get('current_excerpt')
            reason=str(finding.get('check_reason',''))
            if (not excerpt or excerpt.lstrip().startswith('#') or
                    reason.startswith(('Python traced','The quoted text','Refuted in review'))):
                continue
            carried[(finding.get('rule'),excerpt)]=f'Refuted in review {record["iteration"]}: {reason}'
    return carried


def _check_ids(material):
    """Positional IDs for the check: reviewer, assessment and earlier IDs can collide."""
    return [f'C{index}' for index in range(1, len(material) + 1)]


def _refuted_findings(raw, material):
    """Positions in ``material`` that the check refuted, with the stated reason.

    Anything unparseable, malformed or unstated is upheld, and an ID that the
    check both upholds and refutes is upheld.
    """
    try:
        value = _response_json(raw)[0]
    except (ValueError, TypeError):
        return {}
    checks = value.get('checks') if isinstance(value, dict) else None
    position = {identifier: index for index, identifier in enumerate(_check_ids(material))}
    refuted, upheld_ids = {}, set()
    for check in checks if isinstance(checks, list) else []:
        identifier = check.get('id') if isinstance(check, dict) else None
        if not isinstance(identifier, str) or identifier not in position:
            continue
        upheld = check.get('upheld')
        reason = check.get('reason')
        if (upheld is False or (isinstance(upheld, str) and upheld.strip().lower() == 'false')) and \
                isinstance(reason, str) and reason.strip():
            refuted.setdefault(position[identifier], reason.strip()[:600])
        else:
            upheld_ids.add(position[identifier])
    return {index: reason for index, reason in refuted.items() if index not in upheld_ids}


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
                    'has only components and, in gross_profit_less_expenses mode, expense_shares (or expenses). Each scenario component has '
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


def _evidence_day(sources):
    """UTC calendar date of the run's latest source retrieval, never after today; None if none is recorded."""
    days=[]
    for source in sources if isinstance(sources,list) else []:
        stamp=source.get('retrieved_at') if isinstance(source,dict) else None
        # One accepted form on every Python: 3.11 widened fromisoformat. Seconds' fractions never change the date.
        stamp=re.sub(r'(:\d{2})\.\d+',r'\1',stamp.strip()) if isinstance(stamp,str) else ''
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?',stamp):
            continue
        try:
            moment=datetime.fromisoformat(stamp.replace('Z','+00:00'))
        except ValueError:
            continue
        days.append((moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).date())
    return min(max(days),datetime.now(timezone.utc).date()).isoformat() if days else None


def _date_by_evidence(plan, sources):
    """Date a model-authored plan at the run's evidence date; return the plan and the recorded changes.

    The valuation date sets the fact cutoff and the first-year stub, so a model's
    date would move the value (MSFT, 2026-11-27: about 6 cents a share per day).
    A model writing the US calendar day of a run retrieved after midnight UTC also
    dropped coverage. Every quote, rate and filing in the evidence was observed by
    its retrieval, so the evidence date satisfies each dated-input rule.
    """
    day=_evidence_day(sources)
    if day is None or not isinstance(plan,dict):
        return plan,[]
    plan=copy.deepcopy(plan)
    changes=[]
    reason='the valuation is dated at the run\'s latest evidence retrieval (UTC calendar date), not a model-written date'
    if plan.get('valuation_date',day)!=day:
        changes.append({'field':'valuation_date','previous':plan['valuation_date'],'pinned':day,'source_refs':[],'reason':reason})
    plan['valuation_date']=day
    rate=plan.get('discount_rate')
    if isinstance(rate,dict):
        if rate.get('as_of',day)!=day:
            changes.append({'field':'discount_rate.as_of','previous':rate['as_of'],'pinned':day,'source_refs':[],'reason':reason})
        rate['as_of']=day
    return plan,changes


def _lock_and_value(candidate, state, *, apply_conventions=True, prior=None):
    """Lock reported inputs and conventions into one plan, then value it.

    Shared by the plan stage and the review-correction stage so both apply the
    same pins, coverage check, source check and requested counterfactuals. A
    model-authored plan (current conventions) is dated by the evidence; a
    supplied plan keeps its own valuation date.
    """
    dated=[]
    if apply_conventions:
        candidate,dated=_date_by_evidence(candidate,state['sources'])
    locked,changes=_lock_plan(candidate,state['sources'],apply_conventions=apply_conventions)
    changes[:0]=dated
    if not locked['reported_inputs']['coverage'].get('complete'):
        raise ValueError('Reported-input coverage is incomplete')
    _validate_plan_sources(locked,state['sources'])
    if 'requested_counterfactuals' in state:
        locked['counterfactuals']=copy.deepcopy(state['requested_counterfactuals'])
    result=_evaluate(locked)
    _check_continuity(locked,result,state['sources'],prior)
    if prior and 'forecast_continuity' in result:
        result['forecast_continuity']=forecast_continuity(locked,result)
    if prior and 'reinvestment_continuity' in result:
        result['reinvestment_continuity']=reinvestment_continuity(locked,result)
    if prior and 'cost_path_continuity' in result:
        result['cost_path_continuity']=cost_path_continuity(locked,result)
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


# Reinvestment over forecast years 1-3 needs a stated basis when it falls below
# this share of three years at the last reported net reinvestment, or when its
# sales-to-capital exceeds this multiple of the reported incremental rate. A live
# Microsoft plan (2026-11-15) reinvested USD 43.6 billion in year 1 against USD
# 81.6 billion reported, at twice the reported capital efficiency, and was valued
# at 1.49 times the price. The window is three years (Fable 5.2 package,
# 2026-11-26) so a plan cannot meet year 1 and cut reinvestment from year 2.
REINVESTMENT_WINDOW = 3
REINVESTMENT_YEARS = 3
REINVESTMENT_SHARE = 2 / 3
REINVESTMENT_RATIO = 1.5


def _amount(value):
    return value if isinstance(value,(int,float)) and not isinstance(value,bool) else None


def reinvestment_reference(sources):
    """Last reported net reinvestment and the incremental sales-to-capital the plan is held to, or None.

    The rate is computed two ways over the same years, the revenue increase over the
    last three fiscal years divided by (i) capital expenditure less depreciation and
    amortization plus acquisitions and finance-lease additions, and (ii) the change in
    invested capital by Mauboussin and Callahan's method ("investment in future growth
    is therefore equivalent to the change in invested capital", ROIC, October 2022,
    p8), which also counts working capital, operating leases and purchased
    intangibles. The lower rate, which asks more capital of each unit of growth, is
    the reference (2026-11-26 critique: Nvidia 9.6 on capital expenditure against 2.2
    on invested capital). A business whose capital expenditure is below depreciation
    is still checked when invested capital rose (Apple, Uber). The floor stays on (i),
    the owner's net reinvestment, and applies only when its last year is positive.
    A business whose revenue fell has no meaningful rate and is not checked.
    """
    for sid,rows in _grouped_history(sources).items():
        usable=[row for row in rows if _amount(row.get('revenue')) is not None]
        if len(usable)<2:
            continue
        recent=usable[-(REINVESTMENT_YEARS+1):]
        growth=recent[-1]['revenue']-recent[0]['revenue']
        if growth<=0:
            return None
        last=recent[-1]
        nets=None
        if all(_amount(row.get(key)) is not None for row in recent[1:]
               for key in ('capital_expenditure','depreciation_amortization')):
            # Finance-lease additions are capacity bought on credit (Microsoft FY26: USD 24.6
            # billion, 21% of capex), counted as reinvestment by owner choice (2026-11-15).
            nets=[row['capital_expenditure']-row['depreciation_amortization']+(row.get('acquisitions') or 0)+
                  (row.get('finance_lease_additions') or 0) for row in recent[1:]]
        opening,closing=_amount(recent[0].get('invested_capital')),_amount(recent[-1].get('invested_capital'))
        consecutive=all(roic_consecutive(a,b) for a,b in zip(recent,recent[1:]))
        change=closing-opening if opening is not None and closing is not None and consecutive else None
        rates={}
        if nets and sum(nets)>0:
            rates['capital_expenditure']=growth/sum(nets)
        if change is not None and change>0:
            rates['invested_capital']=growth/change
        if not rates:
            return None
        chosen=min(rates,key=rates.get)
        floor=nets[-1] if nets and nets[-1]>0 else None
        return {'fiscal_year_end':last.get('fiscal_year_end'),'net_reinvestment':floor,
                'capital_expenditure':last.get('capital_expenditure'),
                'depreciation_amortization':last.get('depreciation_amortization'),
                'acquisitions':last.get('acquisitions'),'finance_lease_additions':last.get('finance_lease_additions'),
                'incremental_sales_to_capital':rates[chosen],'sales_to_capital_basis':chosen,
                'capital_expenditure_sales_to_capital':rates.get('capital_expenditure'),
                'invested_capital_sales_to_capital':rates.get('invested_capital'),
                'net_reinvestment_over_years':sum(nets) if nets else None,'invested_capital_change':change,
                'revenue_increase':growth,'years':len(recent)-1,'source_refs':[sid],
                'basis':'Incremental sales-to-capital is the revenue increase over the last '
                        f'{len(recent)-1} fiscal years divided by each of two measures of the investment in those years: '
                        'net reinvestment (capital expenditure less depreciation and amortization plus acquisitions and '
                        'finance-lease additions where reported) and the change in invested capital (Mauboussin and '
                        'Callahan method, which includes working capital and operating leases). The lower rate is the '
                        'reference. The reinvestment floor uses the last year of net reinvestment, where it is positive.'}
    return None


def reinvestment_continuity(plan, result):
    """Net reinvestment and sales-to-capital over forecast years 1-3 against the reported reference."""
    reference=(plan or {}).get('reinvestment_reference')
    if not isinstance(reference,dict) or not result or not result.get('scenarios'):
        return None
    bases=(plan or {}).get('capital_efficiency_basis') if isinstance((plan or {}).get('capital_efficiency_basis'),dict) else {}
    rows={}
    for name,scenario in result['scenarios'].items():
        flows=(scenario.get('cashflows') or [])[:REINVESTMENT_WINDOW]
        values=[(row.get('net_reinvestment'),row.get('revenue_growth'),row.get('revenue')) for row in flows]
        if not values or not all(isinstance(x,(int,float)) for row in values for x in row) or values[0][1]<=-1:
            continue
        years=len(values)
        reinvested=sum(row[0] for row in values)
        change=values[-1][2]-values[0][2]/(1+values[0][1])
        efficiency=change/reinvested if reinvested>0 else None
        base=reference.get('net_reinvestment')
        floor=REINVESTMENT_SHARE*years*base if isinstance(base,(int,float)) and base>0 else None
        low=change>0 and ((floor is not None and reinvested<floor) or efficiency is None or
                          efficiency>REINVESTMENT_RATIO*reference['incremental_sales_to_capital'])
        row={'years':years,'net_reinvestment':reinvested,'sales_to_capital':efficiency,
             'reinvestment_floor':floor,'within_band':not low}
        if isinstance(bases.get(name),str):
            row['stated_basis']=bases[name]
        rows[name]=row
    return dict(reference,scenarios=rows,share=REINVESTMENT_SHARE,ratio=REINVESTMENT_RATIO)


# Owner choice (2026-11-26, Fable 5.2 package): final-year EBIT margins have hard
# ceilings from the reported history with no citation exception. Bear at most the
# five-year average; base and bull at most the five-year peak plus 5 points, less
# for low-margin businesses by the same band as the year-1 check (Costco's 3.9%
# peak would otherwise allow 8.9%). Plans had let R&D and SG&A fall from 19% to
# 10% of Microsoft's revenue.
#
# Two fixes (2026-11-26 critiques). An inflection already in reported numbers lifts
# the base and bull ceiling: the peak includes the latest twelve-month operating
# margin from the reported quarters (Nvidia's base ceiling on filings to December
# 2023 was 42.3% while its last four quarters ran at 45.9%), or failing that a year
# to date of at least two quarters (one quarter is seasonal). And a ceiling only
# applies when it is positive, because the engine requires a positive final margin:
# with a five-year average of -1.5% (Uber at the end of 2023) no bear case could pass.
YTD_CEILING_MIN_DAYS = 160


def _latest_run_rate(sid, sources, rows, operating_reference):
    """The latest twelve-month operating margin after the last fiscal year, or a long-enough year to date, or None."""
    last=rows[-1].get('fiscal_year_end')
    for source in sources:
        if source.get('id')!=sid:
            continue
        data=source.get('data') if isinstance(source.get('data'),dict) else {}
        baseline=source.get('historical_baseline') or data.get('historical_baseline') or {}
        ttm=(baseline.get('quarterly') or {}).get('trailing_twelve_months')
        if (isinstance(ttm,dict) and isinstance(ttm.get('operating_margin'),(int,float)) and ttm.get('quarters')==4 and
                isinstance(last,str) and str((ttm.get('period') or {}).get('end_date'))>last):
            return {'basis':'trailing_twelve_months','period':ttm['period'],'operating_margin':ttm['operating_margin'],
                    'source_refs':[sid],'label':f'the operating margin for the twelve months to {ttm["period"]["end_date"]}'}
    ytd=(operating_reference or {}).get('year_to_date') if isinstance(operating_reference,dict) else None
    period=(ytd or {}).get('period') or {}
    try:
        start,end=date.fromisoformat(period['start_date']),date.fromisoformat(period['end_date'])
        anchored=start-timedelta(days=1)==date.fromisoformat(last)
    except (KeyError,TypeError,ValueError):
        return None
    if anchored and (end-start).days>=YTD_CEILING_MIN_DAYS and isinstance(ytd.get('operating_margin'),(int,float)):
        return {'basis':'year_to_date','period':period,'operating_margin':ytd['operating_margin'],
                'source_refs':list(ytd.get('source_refs') or []),'label':f'the year-to-date operating margin to {period["end_date"]}'}
    return None


def cost_path_reference(sources, operating_reference=None):
    """Reported five-year peak and average operating margin and the final-year ceilings they set, or None.

    ``operating_reference`` (reported_inputs.operating_reference) supplies the year
    to date where the reported quarters give no twelve-month margin.
    """
    for sid,rows in _grouped_history(sources).items():
        margins=[row['operating_margin'] for row in rows[-5:] if isinstance(row.get('operating_margin'),(int,float))]
        if len(margins)<3:
            continue
        reference={'fiscal_year_end':rows[-1].get('fiscal_year_end'),'years':len(margins),
                   'max_operating_margin':max(margins),'average_operating_margin':sum(margins)/len(margins),
                   'source_refs':[sid],
                   'basis':'Reported operating margins over the last five fiscal years in the reported history; the base '
                           'and bull peak also includes the latest twelve-month operating margin, or a year to date of at '
                           'least two quarters, where it is higher.'}
        latest=_latest_run_rate(sid,sources,rows,operating_reference)
        if latest:
            reference['latest_run_rate']=latest
        ceilings={name:_margin_ceiling(name,reference)[0] for name in SCENARIO_NAMES}
        ceilings={name:value for name,value in ceilings.items() if value is not None}
        if ceilings:
            reference['final_year_margin_ceilings']=ceilings
        return reference
    return None


def _margin_ceiling(name, reference):
    """(ceiling, label), or (None, reason) where no positive ceiling exists for the scenario."""
    if name=='bear':
        average=reference['average_operating_margin']
        if average<=0:
            return None,'no bear ceiling: the reported five-year average is not positive'
        return average,'the reported five-year average'
    # A business that has not been profitable has no margin history to bound it.
    if reference['max_operating_margin']<=0:
        return None,'no ceiling: no reported year in the history is profitable'
    peak,label=reference['max_operating_margin'],'the reported five-year peak'
    latest=reference.get('latest_run_rate') if isinstance(reference.get('latest_run_rate'),dict) else None
    if latest and isinstance(latest.get('operating_margin'),(int,float)) and latest['operating_margin']>peak:
        peak,label=latest['operating_margin'],latest.get('label') or 'the latest reported operating margin'
    allowance=_continuity_band(peak)
    return peak+allowance,f'{label} plus {allowance*100:.1f} points'


def cost_path_continuity(plan, result):
    """Final-year EBIT margin in each scenario against its hard ceiling from the reported history."""
    reference=(plan or {}).get('cost_path_reference')
    if not isinstance(reference,dict) or not result or not result.get('scenarios'):
        return None
    rows={}
    for name,scenario in result['scenarios'].items():
        flows=scenario.get('cashflows') or []
        if not flows:
            continue
        explicit=min(scenario.get('explicit_forecast_years') or len(flows),len(flows))
        final=flows[explicit-1]['ebit_margin']
        ceiling,label=_margin_ceiling(name,reference)
        applies=ceiling is not None
        rows[name]={'final_ebit_margin':final,'ceiling':ceiling,'ceiling_basis':label,
                    'within_band':(not applies) or final<=ceiling+1e-9,'applies':applies}
    return dict(reference,scenarios=rows)


def _evaluate(plan):
    """Value a locked plan and attach the forecast-continuity diagnostics."""
    result=evaluate(plan)
    continuity=forecast_continuity(plan,result)
    if continuity:
        result['forecast_continuity']=continuity
    reinvestment=reinvestment_continuity(plan,result)
    if reinvestment:
        result['reinvestment_continuity']=reinvestment
    costs=cost_path_continuity(plan,result)
    if costs:
        result['cost_path_continuity']=costs
    # Published sales-growth base rates: printed for the reader and the models, never a gate.
    rates=base_rates.compare(plan,result)
    if rates:
        result['base_rates']=rates
    return result


def _cost_path_breaks(locked, result, known, prior):
    """Scenarios whose final-year EBIT margin exceeds its hard ceiling (no citation exception)."""
    check=result.get('cost_path_continuity')
    if not check:
        return []
    breaks=[f'{name} {row["final_ebit_margin"]:.1%} against {row["ceiling_basis"]} of {row["ceiling"]:.1%}'
            for name,row in check['scenarios'].items() if not row['within_band']]
    if not breaks:
        return []
    return ['Final-year EBIT margin exceeds the house ceiling (reported five-year operating margins: peak '
            f'{check["max_operating_margin"]:.1%}, average {check["average_operating_margin"]:.1%} '
            f'[{", ".join(check["source_refs"])}]'+_run_rate_text(check)+') in: '+', '.join(breaks)+
            '. Lower the margin path (gross margins or expense shares) so the final year is at or below the ceiling; '
            'there is no exception']


def _run_rate_text(reference):
    latest=reference.get('latest_run_rate') if isinstance(reference.get('latest_run_rate'),dict) else None
    if not latest:
        return ''
    kind='twelve months' if latest.get('basis')=='trailing_twelve_months' else 'year to date'
    return (f'; {kind} to {(latest.get("period") or {}).get("end_date")}: {latest["operating_margin"]:.1%} '
            f'[{", ".join(latest.get("source_refs") or [])}]')


def _reinvestment_breaks(locked, result, known, prior):
    """Scenario labels whose years 1-3 reinvestment breaks from the reported run-rate without a cited basis."""
    check=result.get('reinvestment_continuity')
    if not check:
        return []
    bases=locked.get('capital_efficiency_basis')
    if bases is not None and (not isinstance(bases,dict) or set(bases)-set(SCENARIO_NAMES)):
        raise ValueError('capital_efficiency_basis must be an object keyed by bear, base or bull')
    bases=dict(bases or {})
    if prior:
        prior_plan,prior_result=prior
        prior_rows=((prior_result or {}).get('reinvestment_continuity') or {}).get('scenarios') or {}
        prior_bases=(prior_plan or {}).get('capital_efficiency_basis') or {}
        for name,row in check['scenarios'].items():
            before=prior_rows.get(name)
            if (name not in bases and before and isinstance(prior_bases.get(name),str) and
                    before.get('net_reinvestment') and
                    abs(row['net_reinvestment']/before['net_reinvestment']-1)<=.02):
                bases[name]=prior_bases[name]
                row['stated_basis']=bases[name]
        if bases:
            locked['capital_efficiency_basis']=bases
    breaks=[]
    for name,row in check['scenarios'].items():
        basis=bases.get(name)
        cited=re.findall(r'\[(S\d+)\]',basis) if isinstance(basis,str) else []
        unknown=[ref for ref in cited if known and ref not in known]
        if unknown:
            raise ValueError(f'capital_efficiency_basis.{name} cites {", ".join(unknown)}, which is not in the evidence')
        if not row['within_band'] and not (isinstance(basis,str) and len(basis.strip())>=40 and cited):
            efficiency=row['sales_to_capital']
            floor=row.get('reinvestment_floor')
            breaks.append(f'{name} {row["net_reinvestment"]/1e9:,.1f} billion over years 1-{row["years"]}'+
                          (f' against a floor of {floor/1e9:,.1f} billion' if floor is not None else '')+
                          (f', sales-to-capital {efficiency:.2f}' if efficiency is not None else ''))
    if not breaks:
        return []
    base=check.get('net_reinvestment')
    floor_text=(f'net reinvestment must be at least two thirds of {REINVESTMENT_WINDOW} years at the reported '
                f'{base/1e9:,.1f} billion (fiscal year ending {check["fiscal_year_end"]}: capital expenditure '
                f'{check["capital_expenditure"]/1e9:,.1f} billion less depreciation and amortization '
                f'{check["depreciation_amortization"]/1e9:,.1f} billion plus acquisitions'+
                (' and finance-lease additions' if check.get('finance_lease_additions') else '')+'), and '
                if isinstance(base,(int,float)) and base>0 else '')
    return [f'Reinvestment over forecast years 1-{REINVESTMENT_WINDOW} departs from the reported run-rate: '+floor_text+
            f'sales-to-capital may be at most {REINVESTMENT_RATIO:g} times the reported {check["years"]}-year incremental rate of '
            f'{check["incremental_sales_to_capital"]:.2f}'+_rate_basis_text(check)+f' [{", ".join(check["source_refs"])}]. '
            'Outside the band: '+', '.join(breaks)+
            '. Set sales_to_capital from the reported rate, or, if falling reinvestment is the forecast, state its evidence '
            'in capital_efficiency_basis.<scenario> with [S#] citations to sources in the evidence']


def _rate_basis_text(reference):
    """Which measure of investment set the reference rate, and the other rate where there is one."""
    rates={'capital_expenditure':(reference.get('capital_expenditure_sales_to_capital'),'net reinvestment'),
           'invested_capital':(reference.get('invested_capital_sales_to_capital'),'the change in invested capital')}
    chosen=reference.get('sales_to_capital_basis')
    if chosen not in rates:
        return ''
    other=[f'{value:.2f} on {label}' for name,(value,label) in rates.items() if name!=chosen and value is not None]
    return f' on {rates[chosen][1]}'+(f', against {other[0]}' if other else '')


def _check_continuity(locked, result, sources=(), prior=None):
    """Send a plan back when year-1 margins break from the reported results without a cited basis.

    ``prior`` is the (plan, result) a review correction replaces: a scenario whose
    year-1 margin the correction leaves unchanged keeps the prior plan's basis.
    """
    known_sources={source.get('id') for source in sources if isinstance(source,dict)}
    reinvestment=_reinvestment_breaks(locked,result,known_sources,prior)+_cost_path_breaks(locked,result,known_sources,prior)
    continuity=result.get('forecast_continuity')
    if not continuity:
        if reinvestment:
            raise ValueError('. '.join(reinvestment))
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
        if reinvestment:
            raise ValueError('. '.join(reinvestment))
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
        'citations to sources in the evidence'+(('. '+'. '.join(reinvestment)) if reinvestment else ''))


CONTINUITY_RULE = ('\nYear-1 continuity: start year-1 gross margins and expenses from the most recent reported run-rate '
                   'in reported_inputs.operating_reference (annual or year to date). A larger step needs '
                   'margin_continuity_basis.<bear|base|bull> with [S#] citations to sources in the evidence. Start '
                   'reinvestment from the reported net reinvestment and incremental sales-to-capital; a large fall needs '
                   'capital_efficiency_basis.<bear|base|bull> with [S#] citations. Final-year EBIT margins may not exceed the '
                   'reported five-year average (bear, where that average is positive) or the higher of the reported '
                   'five-year peak and the latest twelve-month operating margin, plus 5 points, less for low-margin '
                   'businesses (base and bull).')
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
    # A supplied plan keeps its own terminal return; the schema says Python sets it.
    own=('' if apply else ' This supplied plan keeps its own terminal_roic: include it in every scenario as in the '
         'current proposal unless a finding requires a change.')
    if apply or rate.get('term_structure')=='glide_to_market_v1' or 'valuation_timing' in plan:
        return _rate_convention(sources,currency)+own
    growth={name:(scenario or {}).get('terminal_growth') for name,scenario in (plan.get('scenarios') or {}).items()
            if isinstance(scenario,dict)}
    return ('\nRATE CONVENTION FOR THIS RUN: this supplied plan keeps its own constant discount rate. Keep each scenario\'s '
            'terminal_growth'+('' if rate else ' and wacc')+' as in the current proposal '+json.dumps(growth)+
            ' unless a finding requires a change; do not add term_structure or valuation_timing.'+own)


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
    # Reported history and product/segment tables are computed once here, so
    # the forecast author and writer copy reported figures (2026-11-14: the
    # writer took Apple's category revenue from unverified web excerpts).
    # (The company-facts history is computed by normalize_companyfacts itself.)
    if (isinstance(raw,str) and not companyfacts and isinstance(data,dict) and
          (source.get('kind')=='regulatory_primary' or source.get('source_tier')=='regulatory_primary')):
        try:
            tables=breakdown_tables(inline_breakdown_facts(raw))
        except (ValueError,TypeError,KeyError,ArithmeticError):
            tables={}
        if tables:
            data=copy.deepcopy(data)
            data['breakdown_tables']=tables
            source['data']=data
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
        # Reported history and breakdown tables are derived from the archive on
        # every run, outside source.data, so a folder collected before they
        # existed gets them without new paid research.
        # Derived again on every run (a copy saved in source.data at collection
        # would otherwise freeze the table at the code that collected it).
        if isinstance(source.get('data'),dict) and source['data'].get('fact_origin')=='sec_companyfacts':
            try:
                as_of=(source['data'].get('selection') or {}).get('as_of')
                # Gaps in company facts (extension-tagged acquisitions and D&A, one half of a
                # buyback pair, cover-page share counts) are filled from the 10-K's inline facts.
                baseline=historical_baseline(json.loads(raw.decode('utf-8')),as_of=as_of,
                                             supplement=filing_supplement_for(folder,sources,as_of))
            except (ValueError,TypeError,KeyError,AttributeError,UnicodeDecodeError):
                baseline=None
            current=source.get('historical_baseline') or source['data'].get('historical_baseline')
            if baseline and baseline['rows'] and baseline!=current:
                source['historical_baseline']=baseline
                changed=True
            continue  # Canonical JSON is audited through its structured facts, not HTML prose windows.
        try:
            tables=breakdown_tables(inline_breakdown_facts(raw.decode('utf-8')))
        except (ValueError,TypeError,KeyError,ArithmeticError,UnicodeDecodeError):
            tables={}
        if tables and tables!=source.get('breakdown_tables'):
            source['breakdown_tables']=tables
            changed=True
        focus=archive_focus_excerpts(raw.decode('utf-8'))
        if focus and focus!=source.get('primary_focus_excerpts'):
            # Keep derived windows outside source.data so supplemental-source
            # identity remains stable. The review's full-source hash covers them.
            source['primary_focus_excerpts']=focus
            changed=True
    return changed


# Unverified web excerpts are bounded: they support attributed outside views and
# consensus context only. A live run sent 74 KB of them and the memo leaned on
# them for company facts the filings already contained.
UNVERIFIED_EXCERPT_CHARS = 2000
UNVERIFIED_TOTAL_CHARS = 30000
UNVERIFIED_TIERS = {'search_excerpt_unverified'}
EVIDENCE_TIER_RULE = (
    'Evidence tiers. (1) COMPUTED FROM REPORTED FILINGS and PRIMARY AND OFFICIAL SOURCES support company facts and '
    'numbers. (2) UNVERIFIED WEB EXCERPTS support only attributed outside views, consensus or guidance context and '
    'qualitative colour; label each such use inline as unverified, and never make an unverified excerpt the only '
    'support for a company fact, a number used in a comparison, or the valuation conclusion. Prefer the primary source '
    'whenever both contain a fact.')
# Plan text is printed in the exhibits (monitoring cases, counterfactuals,
# repurchase tests). A live plan (2026-11-14) cited an unverified aggregator
# beside the 10-K for buyback figures the 10-K holds, and the review flagged it.
PLAN_SOURCE_RULE = ('\n'+EVIDENCE_TIER_RULE+' This applies to every plan field Python prints: source_refs, labels, '
    'margin_continuity_basis, monitoring cases, counterfactuals and repurchase tests cite the primary source for '
    'company facts and figures; an unverified excerpt appears only beside an outside view that the label names as one.')


def _computed_tables(sources):
    """Rendered reported-history and breakdown tables for every source that has them."""
    tables=[]
    for source in sources:
        data=source.get('data') if isinstance(source,dict) else None
        data=data if isinstance(data,dict) else {}
        # The copy derived from the archive on this run is current; data holds the collection-time copy.
        baseline=source.get('historical_baseline') or data.get('historical_baseline')
        breakdowns=source.get('breakdown_tables') or data.get('breakdown_tables')
        if baseline:
            tables.append(render_baseline(baseline,source.get('id')))
            quarterly=render_quarterly(baseline,source.get('id'))
            if quarterly:
                tables.append(quarterly)
        if breakdowns:
            tables.append(render_breakdowns(breakdowns,source.get('id')))
    return '\n\n'.join(tables)


def _label_unverified(text, sources):
    """Mark every citation of an unverified web excerpt inline: [S16] becomes [S16 unverified].

    Labelling by construction: live reviews (2026-11-14) kept finding unlabelled
    unverified support, including in plan text that Python renders.
    """
    unverified={source.get('id') for source in sources
                if isinstance(source,dict) and source.get('source_tier') in UNVERIFIED_TIERS}
    if not unverified:
        return text
    def label(match):
        return re.sub(r'\b(S\d+)\b(?![ \t]*,?[ \t]*unverified)',
                      lambda ref:ref.group(1)+' unverified' if ref.group(1) in unverified else ref.group(1),match.group(0))
    return re.sub(r'\[[^\[\]\n]*\bS\d+[^\[\]\n]*\]',label,text)


def _last_edits(response):
    """The last complete JSON object carrying "edits" in a response, fenced or not.

    A live fix response (2026-11-14) gave edits, wrote "Wait, I need to re-read",
    and gave corrected edits; the last object is the model's answer.
    """
    decoder=json.JSONDecoder()
    found=None
    for match in re.finditer(r'\{\s*"edits"',response if isinstance(response,str) else ''):
        try:
            value,_=decoder.raw_decode(response,match.start())
        except ValueError:
            continue
        if isinstance(value,dict) and isinstance(value.get('edits'),list):
            found=value['edits']
    return found


def _apply_edits(memo, edits):
    """Apply quote-and-replacement edits whose quote occurs exactly once; report the rest."""
    applied,skipped=0,[]
    for edit in edits if isinstance(edits,list) else []:
        quote=edit.get('quote') if isinstance(edit,dict) else None
        replacement=edit.get('replacement') if isinstance(edit,dict) else None
        if not isinstance(quote,str) or not quote.strip() or not isinstance(replacement,str):
            skipped.append('malformed edit')
            continue
        count=memo.count(quote)
        if count==2 and not replacement.strip():
            # Deleting a duplicated passage removes its second occurrence.
            position=memo.rfind(quote)
            memo=memo[:position]+memo[position+len(quote):]
            applied+=1
            continue
        if count!=1:
            skipped.append(f'quote found {count} times: {quote[:80]}')
            continue
        memo=memo.replace(quote,replacement)
        applied+=1
    return memo,applied,skipped


def _evidence_context(sources):
    """Evidence for model prompts in three labelled tiers.

    Excerpts are bounded; full originals remain in the source archive. Never
    pass an entire filing, private environment or credentials into a prompt.
    """
    computed, primary, unverified, used = [], [], [], 0
    for source in sources:
        row = {key: source[key] for key in ('id','title','url','kind','source_tier','retrieved_at','primary_focus_excerpts') if key in source}
        data = source.get('data')
        rendered = _computed_tables([source])
        if rendered:
            computed.append(rendered)
        if isinstance(data, dict):
            data = {key: value for key, value in data.items() if key not in ('historical_baseline', 'breakdown_tables')}
        if source.get('kind')=='user_evidence':
            # The public interface already bounds this focused input at 100 KB.
            # Include all accepted text, including corrections near its end.
            row['data']=copy.deepcopy(data)
        elif isinstance(data, dict) and data.get('dataset') in (country_risk.COUNTRY_TABLE, country_risk.REGIONAL_TABLE):
            # Python applies these tables in one panel row; the country rows stay archived.
            row['data'] = country_risk.prompt_summary(data)
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
            limit = 14000
            if source.get('source_tier') in UNVERIFIED_TIERS:
                limit = max(0, min(UNVERIFIED_EXCERPT_CHARS, UNVERIFIED_TOTAL_CHARS - used))
                used += min(len(text), limit)
            if limit >= 200:
                row['excerpt'] = text[:limit]
                row['excerpt_truncated'] = len(text) > limit
            else:
                row['excerpt_omitted'] = 'Prompt budget for unverified excerpts reached; title and URL only.'
        (unverified if source.get('source_tier') in UNVERIFIED_TIERS else primary).append(row)
    parts = []
    if computed:
        parts.append('COMPUTED FROM REPORTED FILINGS (Python tables; cite the bracketed source ID):\n' + '\n\n'.join(computed))
    parts.append('PRIMARY AND OFFICIAL SOURCES:\n' + json.dumps(primary, ensure_ascii=False))
    if unverified:
        parts.append('UNVERIFIED WEB EXCERPTS (search results; attributed outside views and consensus context only, '
                     'labelled unverified where cited):\n' + json.dumps(unverified, ensure_ascii=False))
    return '\n\n'.join(parts)


def _workflow_contract():
    """Track mutable prompts and the code that presents/audits their outputs."""
    root=Path(__file__).parent
    return {'system':_digest(core.SYSTEM),
            'forecast':_digest([FORECAST_SCHEMA,UNDERWRITING_GUIDANCE]),
            'review':_digest(REVIEW_SCHEMA), 'sections':_digest(SECTIONS),
            'implementation':_digest({name:hashlib.sha256((root/name).read_bytes()).hexdigest()
                for name in ('pipeline.py','prompt_context.py','quality.py','valuation.py','inputs.py','operating.py','rates.py','primary_debt.py','companyfacts.py',
                             'evidence_tables.py','research.py','base_rates.py','country_risk.py')
                if (root/name).is_file()})}


def _revisions_used(state):
    """Revisions spent since the last contract refresh started a new review cycle."""
    return state['iteration']-state.get('revision_base',0)


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
    # A refresh starts a new review cycle; it does not spend the revision allowance.
    state['revision_base']=state['iteration']
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
                              forecast_plan=None, draft=None, max_revisions=1):
    """Price known repeated context before the first potentially billed call.

    Future search excerpts, forecasts, derived valuation data and memo prose are
    unknown at this point. This is deliberately a reservation *floor*, not an
    upper bound, token prediction, invoice estimate, or completion guarantee.
    The real request retains its full byte-based reservation in Router.complete.
    """
    context = _evidence_context(state['sources'])
    stages = []
    def add(stage, model, prompt, tokens, web=False):
        # Reasoning tokens are reserved on top of the output limit, as the router does.
        catalog=getattr(router,'catalog',{}) or {}
        if core.reasoning_request(catalog.get(model),model,core.reasoning_budget(stage)) is not None:
            tokens+=core.reasoning_budget(stage)
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
            FORECAST_SCHEMA+UNDERWRITING_GUIDANCE+PLAN_SOURCE_RULE+'\nEvidence:\n'+context,12000)
    valuation = ''
    if state.get('valuation') is not None and state.get('plan'):
        valuation = render_valuation_context(state['plan'],state['valuation'])
    if not supplied_memo:
        add('full_draft',strong,'Write a complete investment-committee draft.\nEvidence:\n'+
            context+'\nValuation:\n'+valuation,MEMO_TOKENS)
        # The free checks flagged every saved draft, so the fix call is planned for.
        add('draft_check_fix_'+str(state['iteration']),strong,'Correct only the problems listed below.\nEvidence:\n'+context,8000)
    add('review_'+str(state['iteration']),reviewer,'Independently audit the complete memo.\n'+
        REVIEW_SCHEMA+'\nEvidence:\n'+context+'\nValuation:\n'+valuation+
        '\nDraft:\n'+(draft or state.get('current_memo','')),12000)
    add('finding_check_'+str(state['iteration']),reviewer,FINDING_CHECK+'\nEvidence:\n'+context+'\nValuation:\n'+valuation+
        '\nDraft:\n'+(draft or state.get('current_memo','')),FINDING_CHECK_TOKENS)
    floor = sum(row['reservation_floor_usd'] for row in stages)
    suggested=max(DEFAULT_BUDGET,float(math.ceil(floor)))
    return {'scope':'First draft, its fix call, one review and its finding check; revisions and format/forecast repairs excluded',
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
            changed=any(current.get(key)!=pinned.get(key) for key in ('value','observed_at','allocation'))
            # A label that described a different number would contradict the pin.
            if force_label or changed or not isinstance(pinned.get('label'),str) or not pinned['label'].strip():
                pinned['label']=label
            if changed:
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
        quoted=quoted[:10]
        pinned={**current,'value':price*shares,'observed_at':quoted,'construction':'quote_times_reported_shares',
                'source_refs':list(dict.fromkeys([*(refs.get('market_price') or []),*(refs.get('shares_outstanding') or [])]))}
        pinned.pop('measurement',None)
        changed=current.get('value')!=pinned['value'] or current.get('observed_at')!=quoted
        if changed or not pinned.get('label'):
            pinned['label']='Saved quote times reported shares outstanding'
        if changed:
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
    # Python's own copy of the premium's monthly history, set again below only when the
    # plan's Treasury rate and premium are pinned from that same source.
    locked.pop('rate_history',None)
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
            pinned={**current,'value':data[key],'observed_at':data['observed_at'],'source_refs':[erp['id']],
                    'label':('Treasury rate paired with the implied equity risk premium' if key=='risk_free_rate' else
                             'Damodaran implied equity risk premium, sustainable-payout model')}
            if current.get('value')!=pinned['value'] or current.get('observed_at')!=pinned['observed_at']:
                record(f'discount_rate.{key}',{k:current.get(k) for k in ('value','observed_at')},
                       {'value':pinned['value'],'observed_at':pinned['observed_at']},'pinned from the implied premium source')
            rate[key]=pinned
        # The rate-regime exhibit values the same cash flows at each month's pair.
        history=erp.get('monthly_history')
        if isinstance(history,list) and history:
            locked['rate_history']={'dataset':data.get('dataset'),'method':data.get('method'),'source_refs':[erp['id']],
                                    'months':copy.deepcopy(history),
                                    'basis':erp.get('monthly_history_basis') or 'Monthly Treasury rate and implied premium pairs.'}
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


def _figure(value):
    """Six significant figures: plans may state amounts in billions, where whole units would distort."""
    return float(f'{value:.6g}')


def _share(value):
    # Above 1 is allowed: a company may spend more on R&D than it sells.
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and 0<=value<=100


def _scenario_revenue(bases, scenario):
    """Total revenue by forecast year from component base revenue and growth rates, or None."""
    components=scenario.get('components') if isinstance(scenario.get('components'),dict) else {}
    arrays=[(components.get(component) or {}).get('growth_rates') for component in bases]
    if not bases or not all(isinstance(values,list) and values and
                            all(isinstance(x,(int,float)) and not isinstance(x,bool) for x in values) for values in arrays):
        return None
    horizon=min(len(values) for values in arrays)
    revenue=[0.0]*horizon
    for component,base in bases.items():
        level=base if isinstance(base,(int,float)) and not isinstance(base,bool) else 0
        for year,growth in enumerate(components[component]['growth_rates'][:horizon]):
            level*=1+growth
            revenue[year]+=level
    return revenue


def _proposal_view(proposal):
    """A locked proposal with dollar expenses shown as per-year shares of revenue, for a model to edit.

    A revision that changed growth but copied the dollar expenses back would move
    the margins without saying so; shares keep costs tied to the new revenue.
    """
    view=copy.deepcopy(proposal)
    for scenario in (view.get('scenarios') or {}).values():
        if isinstance(scenario,dict):
            # Python rebuilds the per-year ratios from sales_to_capital on every lock.
            scenario.pop('sales_to_capital_path',None)
    model=view.get('operating_model')
    if not isinstance(model,dict) or model.get('earnings_mode')!='gross_profit_less_expenses':
        return view
    bases={item.get('name'):item.get('base_revenue') for item in model.get('components') or [] if isinstance(item,dict)}
    for scenario in (model.get('scenarios') or {}).values():
        expenses=scenario.get('expenses') if isinstance(scenario,dict) else None
        revenue=_scenario_revenue(bases,scenario) if isinstance(expenses,dict) else None
        if not revenue or not all(amount>0 for amount in revenue) or not all(
                isinstance(values,list) and len(values)>=len(revenue) for values in expenses.values()):
            continue
        scenario['expense_shares']={line:[round(value/amount,4) for value,amount in zip(values,revenue)]
                                    for line,values in expenses.items()}
        scenario.pop('expenses')
    return view


SGA_PARTS = ('selling_and_marketing', 'general_and_administrative')
SGA_WORDS = ('selling', 'marketing', 'general', 'administrative', 'sga', 'sg_a')


def _share_path(item, horizon, field):
    """Per-year shares of revenue from a held share, a straight line to a target, or one share per year."""
    if isinstance(item,(int,float)) and not isinstance(item,bool):
        item={'year1':item,'target':item}
    if isinstance(item,list):
        # Per-year shares: the form a revision sees for a plan already built.
        if len(item)<horizon or not all(_share(x) for x in item[:horizon]):
            raise ValueError(f'{field} needs {horizon} shares of revenue, zero or more')
        return list(item[:horizon])
    if not isinstance(item,dict) or not isinstance(item.get('year1'),(int,float)):
        raise ValueError(f'{field} needs a year1 share of revenue')
    first=item['year1']
    target=item.get('target',first)
    reach=item.get('target_year',horizon)
    if not (_share(first) and _share(target)):
        raise ValueError(f'{field} shares must be decimal shares of revenue, zero or more')
    if not isinstance(reach,int) or isinstance(reach,bool) or not 1<=reach<=horizon:
        raise ValueError(f'{field}.target_year must be 1 to {horizon}')
    return [target if reach==1 else first+(target-first)*min(year,reach-1)/(reach-1) for year in range(horizon)]


def _combined_sga(spec, name):
    """SG&A as one line: the reported split (Microsoft) is combined; other split names are refused.

    The operating model requires selling_general_administrative, and a line left
    out holds its reported share, so separate selling or administrative lines
    beside it would count those costs twice.
    """
    if 'selling_general_administrative' in spec:
        return spec
    if all(part in spec for part in SGA_PARTS):
        spec=dict(spec)
        parts=[spec.pop(part) for part in SGA_PARTS]
        spec['selling_general_administrative']=[sum(year) for year in zip(*parts)]
        return spec
    split=[line for line in spec if any(word in str(line).lower() for word in SGA_WORDS)]
    if split:
        raise ValueError(f'operating_model.scenarios.{name}.expense_shares gives {", ".join(split)} without '
                         'selling_general_administrative; give selling, marketing, general and administrative costs as one '
                         'selling_general_administrative line')
    return spec


def _reported_expense_shares(locked):
    """Reported R&D, SG&A and other operating expenses as shares of anchor revenue.

    SG&A is the reported line, or selling and marketing plus general and
    administrative where those are reported instead (Microsoft). Other operating
    expenses are the reported total less both, so holding the shares holds the
    reported operating cost ratio.
    """
    lines=((locked.get('reported_inputs') or {}).get('operating_reference') or {}).get('lines') or {}
    anchor=(locked.get('actuals') or {}).get('revenue')
    def value(name):
        amount=(lines.get(name) or {}).get('value')
        return amount if isinstance(amount,(int,float)) and not isinstance(amount,bool) else None
    if not isinstance(anchor,(int,float)) or anchor<=0:
        return {}
    # A company that reports no R&D line (Costco) has none to hold; the remainder of
    # reported operating expenses still carries any cost XBRL leaves untagged.
    amounts={'research_and_development':value('research_and_development') or 0}
    split=[value('selling_and_marketing'),value('general_and_administrative')]
    amounts['selling_general_administrative']=(value('selling_general_administrative') if value('selling_general_administrative') is not None
                                               else sum(split) if all(x is not None for x in split) else None)
    total=value('operating_expenses')
    if total is not None and all(x is not None for x in amounts.values()):
        other=total-sum(amounts.values())
        if other>.001*anchor:
            amounts['other_operating_expenses']=other
    return {name:amount/anchor for name,amount in amounts.items() if amount is not None and amount>=0}


def _expenses_from_shares(locked, changes):
    """Dollar expense arrays from expense_shares and each scenario's own revenue path.

    Plans wrote R&D and SG&A in dollars and let them fall from about 19% to 10% of
    Microsoft's revenue (2026-11-15). Shares move linearly from year1 to target by
    target_year and then hold; a scenario that gives neither shares nor dollars
    holds the reported shares.
    """
    model=locked.get('operating_model')
    if not isinstance(model,dict) or model.get('earnings_mode')!='gross_profit_less_expenses':
        return
    bases={item.get('name'):item.get('base_revenue') for item in model.get('components') or [] if isinstance(item,dict)}
    reported=_reported_expense_shares(locked)
    for name,scenario in (model.get('scenarios') or {}).items():
        if not isinstance(scenario,dict) or (scenario.get('expense_shares') is None and 'expenses' in scenario):
            continue
        spec=scenario.pop('expense_shares',None)
        if spec is None:
            spec={line:share for line,share in reported.items()}
            source='the reported shares, held'
        elif not isinstance(spec,dict):
            raise ValueError(f'operating_model.scenarios.{name}.expense_shares must be an object keyed by expense line')
        else:
            source='expense_shares'
        revenue=_scenario_revenue(bases,scenario)
        if revenue is None:
            raise ValueError(f'operating_model.scenarios.{name} needs growth_rates for every component to build expenses')
        horizon=len(revenue)
        paths=_combined_sga({line:_share_path(item,horizon,f'operating_model.scenarios.{name}.expense_shares.{line}')
                             for line,item in spec.items()},name)
        for line in ('research_and_development','selling_general_administrative'):
            if line not in paths and line in reported:
                paths[line]=[reported[line]]*horizon
        expenses={line:[_figure(share*amount) for share,amount in zip(shares,revenue)] for line,shares in paths.items()}
        missing=[line for line in ('research_and_development','selling_general_administrative') if line not in expenses]
        if missing:
            raise ValueError(f'operating_model.scenarios.{name}.expense_shares needs '+' and '.join(missing))
        scenario['expenses']=expenses
        changes.append({'field':f'operating_model.scenarios.{name}.expenses','previous':None,'pinned':'built from '+source,
                        'source_refs':[],'reason':'Python builds dollar expenses from shares of the scenario\'s revenue'})


def _forecast_horizon(locked, scenario):
    """Explicit forecast years of a scenario: its operating revenue path, else its growth array."""
    model=locked.get('operating_model') if isinstance(locked.get('operating_model'),dict) else {}
    operating=((model.get('scenarios') or {}).get(scenario) if isinstance(model.get('scenarios'),dict) else None)
    if isinstance(operating,dict):
        bases={item.get('name'):item.get('base_revenue') for item in model.get('components') or [] if isinstance(item,dict)}
        revenue=_scenario_revenue(bases,operating)
        if revenue:
            return len(revenue)
    growth=((locked.get('scenarios') or {}).get(scenario) or {}).get('growth_rates')
    return len(growth) if isinstance(growth,list) and growth else None


def _sales_to_capital_paths(locked, changes):
    """Per-year sales-to-capital from a year1/target/target_year path, written beside it.

    A capex cycle cannot be carried by one ratio for ten years (Druckenmiller and
    Damodaran critiques, 2026-11-26: Microsoft's reinvestment). The path is rebuilt
    on every lock, so a revision that edits the path spec never keeps a stale path.
    """
    for name,scenario in (locked.get('scenarios') or {}).items():
        if not isinstance(scenario,dict):
            continue
        stale=scenario.pop('sales_to_capital_path',None)
        spec=scenario.get('sales_to_capital')
        if not isinstance(spec,(dict,list)):
            continue
        horizon=_forecast_horizon(locked,name)
        if horizon is None:
            raise ValueError(f'scenarios.{name}.sales_to_capital gives a path, but the scenario has no forecast horizon')
        path=sales_to_capital_path(spec,horizon,name)
        if path:
            scenario['sales_to_capital_path']=path
        if path!=stale:
            changes.append({'field':f'scenarios.{name}.sales_to_capital_path','previous':stale,'pinned':path,'source_refs':[],
                            'reason':'Python builds the per-year sales-to-capital from the stated path'})


def _pin_terminal_rnd(locked, sources, changes):
    """Each forecast year's R&D share of revenue, for the net R&D investment the continuing value adds back.

    Owner choice (2026-11-26, Damodaran critique F3): R&D is expensed in the margin
    and the continuing value also charges reinvestment at the terminal return, so
    new R&D after the forecast earned below its cost forever. R&D only, not selling
    or administrative costs. The shares are the plan's own R&D line; a plan without
    one (aggregate margins) holds the reported share, and a company that reports no
    R&D (Costco) gets none. The continuing value amortizes the forecast's own R&D,
    so the reported history is kept only for forecasts shorter than six years.
    """
    model=locked.get('operating_model') if isinstance(locked.get('operating_model'),dict) else {}
    reported=_reported_expense_shares(locked).get('research_and_development') or 0.0
    bases={item.get('name'):item.get('base_revenue') for item in model.get('components') or [] if isinstance(item,dict)}
    for name,scenario in (locked.get('scenarios') or {}).items():
        if not isinstance(scenario,dict):
            continue
        operating=((model.get('scenarios') or {}).get(name) if isinstance(model.get('scenarios'),dict) else None) or {}
        spend=((operating.get('expenses') or {}).get('research_and_development')
               if model.get('earnings_mode')=='gross_profit_less_expenses' else None)
        revenue=_scenario_revenue(bases,operating) if spend is not None else None
        previous=(scenario.get('rnd_share_path'),scenario.get('rnd_basis'))
        if (isinstance(spend,list) and revenue and len(revenue)==len(spend) and
                all(isinstance(x,(int,float)) and not isinstance(x,bool) for x in spend) and all(x>0 for x in revenue)):
            shares,basis=[value/amount for value,amount in zip(spend,revenue)],'plan_rnd_line'
        else:
            horizon=_forecast_horizon(locked,name)
            shares,basis=([reported]*horizon if horizon else []),'reported_share'
        if not any(shares):
            # No R&D: the field is left out, which the engine reads as zero.
            scenario.pop('rnd_share_path',None)
            scenario.pop('rnd_basis',None)
            if previous[0]:
                changes.append({'field':f'scenarios.{name}.rnd_share_path','previous':previous[0],'pinned':None,
                                'source_refs':[],'reason':'no R&D line in the plan or the reported results'})
            continue
        scenario['rnd_share_path'],scenario['rnd_basis']=shares,basis
        if previous!=(shares,basis):
            changes.append({'field':f'scenarios.{name}.rnd_share_path','previous':previous[0],'pinned':shares,
                            'source_refs':[],
                            'reason':('R&D share of revenue in each forecast year from the plan; the continuing value adds back '
                                      'net R&D investment' if basis=='plan_rnd_line' else
                                      'reported R&D share of revenue, held (the plan has no R&D line); the continuing value adds '
                                      'back net R&D investment')})
    history=reported_rnd_history(sources,(locked.get('actuals') or {}).get('revenue'))
    if history and any(isinstance(item,dict) and item.get('rnd_share_path') for item in (locked.get('scenarios') or {}).values()):
        locked['reported_rnd_history']=history
    else:
        locked.pop('reported_rnd_history',None)


def reported_rnd_history(sources, anchor_revenue):
    """Reported R&D for consecutive fiscal years ending at the forecast anchor year, oldest first, or None.

    The last history year must be the anchor: its revenue matches the pinned revenue.
    """
    if not isinstance(anchor_revenue,(int,float)) or anchor_revenue<=0:
        return None
    for sid,rows in _grouped_history(sources).items():
        if not rows or not isinstance(rows[-1].get('revenue'),(int,float)) or \
                abs(rows[-1]['revenue']-anchor_revenue)>1e-6*anchor_revenue:
            continue
        run=[]
        for row in reversed(rows):
            amount=row.get('research_and_development')
            if (not isinstance(amount,(int,float)) or isinstance(amount,bool) or amount<0 or
                    (run and not _consecutive_years(row,run[0]))):
                break
            run.insert(0,row)
        if run:
            return {'amounts':[row['research_and_development'] for row in run],
                    'fiscal_year_ends':[row.get('fiscal_year_end') for row in run],'source_refs':[sid],
                    'basis':'Reported R&D for consecutive fiscal years ending at the forecast anchor year, oldest first; '
                            'the continuing value amortizes it only where the forecast is shorter than six years.'}
    return None


def _consecutive_years(earlier, later):
    try:
        days=(date.fromisoformat(later['fiscal_year_end'])-date.fromisoformat(earlier['fiscal_year_end'])).days
    except (KeyError,TypeError,ValueError):
        return False
    return 350<=days<=380


def forecast_capital_reference(sources):
    """Operating invested capital (Mauboussin and Callahan method) at the last reported year end, or None.

    Only the last reported year counts: an earlier year's capital would start the
    roll-forward from the wrong balance sheet.
    """
    for sid,rows in _grouped_history(sources).items():
        capital=rows[-1].get('invested_capital') if rows else None
        if isinstance(capital,(int,float)) and not isinstance(capital,bool) and math.isfinite(capital):
            return {'invested_capital':capital,'fiscal_year_end':rows[-1].get('fiscal_year_end'),'source_refs':[sid],
                    'basis':'Operating-approach invested capital at the last reported fiscal year end, including '
                            'goodwill and acquired intangibles (Mauboussin and Callahan, October 2022).'}
    return None


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
    today=datetime.now(timezone.utc).date()
    if date.fromisoformat(valuation_day) > today:
        raise ValueError(f'valuation_date cannot be in the future: {valuation_day} is after {today.isoformat()} (UTC)')
    reported=canonical_reported_inputs(sources, as_of=valuation_day)
    if not reported['coverage'].get('complete'):
        coverage,evidence=reported['coverage'],_evidence_day(sources)
        detail=('; '.join(coverage.get('conflicts') or []) or ', '.join(coverage.get('missing_fields') or [])
                or str(coverage.get('status')))
        raise ValueError(f'Essential reported inputs lack complete primary coverage at valuation date {valuation_day}'+
                         (f' (sources retrieved through {evidence}, UTC)' if evidence else '')+': '+detail)
    proposal=copy.deepcopy(plan)
    # Review dispositions belong to the audit trail, not forecast assumptions.
    # Feeding old allegations back as model data anchors later reviews.
    proposal.pop('finding_dispositions',None)
    proposal['currency']=reported['currency']
    proposal['amount_unit']='currency_units'
    proposal['share_unit']='shares'
    proposal['valuation_date']=valuation_day
    locked,changes=pin_reported_inputs(proposal,sources)
    _expenses_from_shares(locked,changes)
    _sales_to_capital_paths(locked,changes)
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
        # EBIT margins: Python derives both, and stated aggregate arrays are
        # removed and recorded. Sending a disagreeing plan back for repair was
        # tried live (2026-11-14): to match its stated margins the author
        # invented expense levels ($68-96 billion of R&D against $45.8 billion
        # reported). The detailed drivers now govern; the stated paths and the
        # largest gap stay in the recorded changes and the assumption notes.
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
                gaps={}
                for name,arrays in stated.items():
                    for field,values in arrays.items():
                        computed=derived['scenarios'][name][field]
                        if isinstance(values,list) and len(values)==len(computed) and all(
                                isinstance(v,(int,float)) and not isinstance(v,bool) for v in values):
                            gaps[(name,field)]=max(abs(v-c) for v,c in zip(values,computed))
                for name,arrays in stated.items():
                    for field in arrays:
                        disagreed=any(text.startswith(f'{name}.{field} ') for text in conflicts)
                        changes.append({'field':f'scenarios.{name}.{field}','previous':locked['scenarios'][name].pop(field),
                            'pinned':'derived from operating_model','source_refs':[],
                            'reason':('stated array differs from the governing operating model; the drivers govern'
                                      if disagreed else 'aggregate forecast array matched the governing operating model within tolerance')})
                if conflicts:
                    worst=max(gaps.items(),key=lambda item:item[1],default=None)
                    derived_note=('The plan also stated growth or EBIT-margin paths that its operating drivers do not produce'+
                                  (f' (largest gap {worst[1]*100:.1f} points, {worst[0][0]} {worst[0][1].replace("_"," ")})' if worst else '')+
                                  '; the drivers govern and the stated paths are recorded with the input changes.')
                    locked['_derived_path_note']=derived_note
    opted_in=((isinstance(locked.get('discount_rate'),dict) and
               locked['discount_rate'].get('term_structure')=='glide_to_market_v1') or 'valuation_timing' in locked)
    if apply_conventions or opted_in:
        convention_notes=_pin_market_conventions(locked,sources,reported,valuation_day,changes)
        if apply_conventions:
            convention_notes=[*convention_notes,*_pin_house_conventions(locked,sources,changes)]
            _pin_terminal_rnd(locked,sources,changes)
            if any(isinstance(scenario,dict) and scenario.get('rnd_share_path')
                   for scenario in (locked.get('scenarios') or {}).values()):
                convention_notes.append('The continuing value adds net R&D investment (final-year R&D less six-year '
                                        'amortization of the forecast\'s own R&D) back to NOPAT, so R&D after the forecast is '
                                        'investment that earns the terminal return.')
    else:
        convention_notes=['This supplied plan keeps its own rate and timing conventions; the market-consistent rate path '
                          'and valuation-date stub apply only when a plan opts in with discount_rate.term_structure or valuation_timing.']
    derived_note=locked.pop('_derived_path_note',None)
    if derived_note:
        convention_notes=[*convention_notes,derived_note]
    convention_notes=[*convention_notes,*_pin_repurchase_history(locked,sources,changes)]
    pin_terminal=apply_conventions
    reference=reinvestment_reference(sources)
    if reference:
        locked['reinvestment_reference']=reference
    else:
        locked.pop('reinvestment_reference',None)
    costs=cost_path_reference(sources,reported.get('operating_reference'))
    if costs:
        locked['cost_path_reference']=costs
    else:
        locked.pop('cost_path_reference',None)
    # Damodaran's revenue-weighted country risk premium: one labelled panel row, never the house rate.
    country=country_risk.country_risk_reference(sources,reported)
    if country:
        locked['country_risk_reference']=country
    else:
        locked.pop('country_risk_reference',None)
    capital=forecast_capital_reference(sources)
    if capital:
        locked['invested_capital_reference']=capital
    else:
        locked.pop('invested_capital_reference',None)
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
        'Growth, margins, taxes, discount rates, capital efficiency, terminal returns and '+
        ('the bear and bull stress cases ' if apply_conventions else 'scenario weights ')+
        'are house forecasts, not reported facts or company guidance. Historical actuals use the '
        'reported-input snapshot. Only the cash and investment assets selected in the pinned bridge are '
        'classified as nonoperating under an explicit house policy. Omitted investment categories are '
        'not reported zeros. All selected cash and investments are treated as nonoperating; any part the business '
        'needs to operate would lower equity value. '+lease_allocation+
        ('Non-operating investments outside the selected cash are added, and noncontrolling interests, preferred stock '
         'and unfunded pension and postretirement obligations are deducted, at carrying value; pension obligations '
         'are taken before tax. Value per share divides by outstanding shares plus the latest fiscal year\'s dilutive '
         'increment. ' if locked.get('nonoperating_assets_bridge') or locked.get('share_count_bridge') else '')+
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
    if pin_terminal:
        # Last, once the plan is cleaned and labelled: the rate inputs are final and a
        # failure here is the plan's own error, raised for repair.
        locked['assumptions_label']+=' '+' '.join(_pin_terminal_return(locked,sources,changes))
    return locked,changes


# House conventions Python sets on every plan built under the current
# conventions. The plan author picked 20-30% terminal returns without evidence and
# same-evidence plans differed through invented scenario weights.
# - Terminal return on new capital: the terminal discount rate (owner choice,
#   2026-11-26, "follow Mauboussin"). Mauboussin's steady state: the 10-year
#   forecast is the house horizon for excess returns (his competitive advantage
#   period is company-specific; the panel shows others), and afterwards new investment
#   earns its cost of capital, so the continuing value is year-10 NOPAT (which the
#   capital in place at year 10 supports, plus net R&D investment) over the
#   terminal cost of capital whatever the terminal growth (Mauboussin and
#   Callahan, "Return on Invested Capital", 2022, Appendix A; "Market-Expected
#   Return on Investment", 2021). The rate comes from the rate inputs, which do not
#   depend on the return. It replaces a 15% ceiling lowered to the reported median
#   ROIC (2026-11-15 to 2026-11-26), which the convention panel still shows.
# - Headline: the base case. Bear and bull are unweighted stress cases; the engine
#   receives probabilities 0 / 1 / 0, so every weighted figure equals the base case.
# About the market's long-run return on new capital, which the implied premium
# assumes the index earns (Damodaran, January 2026: 15.1% excluding financials);
# the ceiling of the earlier house rule, kept for the convention panel.
MARKET_TERMINAL_ROIC = .15
DEFAULT_WEIGHTS = {'bear': 0.0, 'base': 1.0, 'bull': 0.0}


def terminal_return_reference(sources):
    """Median reported ROIC (Mauboussin and Callahan method) over the history, or None."""
    for sid,rows in _grouped_history(sources).items():
        values=[float('inf') if row.get('return_on_capital_unbounded') else row.get('return_on_invested_capital')
                for row in rows[-5:]]
        values=[value for value in values if isinstance(value,(int,float))]
        if len(values)>=2:
            ordered=sorted(values)
            middle=len(ordered)//2
            median=ordered[middle] if len(ordered)%2 else (ordered[middle-1]+ordered[middle])/2
            # Capital at or below zero in most years: no finite median (saved JSON allows no infinity).
            unbounded=not math.isfinite(median)
            return {'median_return_on_capital':None if unbounded else median,'median_unbounded':unbounded,
                    'years':len(values),'source_refs':[sid],
                    'basis':'Five-year median of reported return on invested capital by the Mauboussin and Callahan method '
                            '(NOPAT over average operating invested capital, with goodwill and acquired intangibles).'}
    return None


def _grouped_history(sources):
    grouped={}
    for sid,row in _history_rows(sources):
        grouped.setdefault(sid,[]).append(row)
    return grouped


def _pin_house_conventions(locked, sources, changes):
    """The base-case headline follows the house rule; the terminal return is pinned at the end of the lock."""
    notes=[]
    scenarios=locked.get('scenarios') if isinstance(locked.get('scenarios'),dict) else {}
    # A convention, not a correction of the plan: recorded once in the notes.
    if set(scenarios)==set(DEFAULT_WEIGHTS):
        for name,weight in DEFAULT_WEIGHTS.items():
            scenarios[name]['probability']=weight
    locked.pop('weights_basis',None)
    # Retired with the margin ceilings (2026-11-26): no citation lifts a ceiling.
    locked.pop('cost_path_basis',None)
    notes.append('The base case is the headline value; bear and bull are stress cases without probabilities.')
    return notes


def _pin_terminal_return(locked, sources, changes):
    """Terminal return on new capital: the scenario's terminal discount rate (Mauboussin's steady state).

    Runs once all rate inputs are final. The terminal rates do not depend on the
    return, so re-locking a locked plan reproduces the same value and records no
    change. The reported ROIC reference is kept for the convention panel.
    """
    scenarios=locked.get('scenarios') if isinstance(locked.get('scenarios'),dict) else {}
    if not scenarios:
        return []
    reference=terminal_return_reference(sources)
    median=(reference or {}).get('median_return_on_capital')
    if isinstance(median,float) and not math.isfinite(median):
        # The locked plan is saved as strict JSON, which has no infinity.
        reference=dict(reference,median_return_on_capital=None,median_unbounded=True)
    if reference:
        locked['terminal_return_reference']=reference
    else:
        locked.pop('terminal_return_reference',None)
    # Raises what the valuation would raise for an invalid plan, for repair.
    rates=terminal_rates(locked)
    for name,scenario in scenarios.items():
        if not isinstance(scenario,dict):
            continue
        pinned=rates[name]
        scenario['terminal_roic_basis']='terminal_rate'
        previous=scenario.get('terminal_roic')
        if not (isinstance(previous,(int,float)) and not isinstance(previous,bool) and abs(previous-pinned)<=1e-12):
            changes.append({'field':f'scenarios.{name}.terminal_roic','previous':previous,'pinned':pinned,'source_refs':[],
                            'reason':'terminal return on new capital equals the terminal discount rate (house convention: '
                                     'Mauboussin\'s steady state)'})
        scenario['terminal_roic']=pinned
    return ['Terminal return on new capital equals the terminal discount rate (Mauboussin\'s steady state): the 10-year '
            'forecast is the house horizon for excess returns, after which new investment earns its cost of capital, so growth '
            'beyond it adds no value; rate alternatives keep this. The convention panel shows the value with a terminal '
            'return 4 points above the terminal rate, at 15% and 25%, and at the earlier house rule (the reported median '
            'ROIC, at most 15%) where that differs from these, and with horizons of 5, 15 and 20 years and at Mauboussin\'s '
            'industry fade rate where the industry mapping has one.']


def _history_rows(sources):
    for source in sources:
        data=source.get('data') if isinstance(source.get('data'),dict) else {}
        baseline=source.get('historical_baseline') or data.get('historical_baseline')
        for row in (baseline or {}).get('rows') or []:
            yield source.get('id'),row


def _pin_repurchase_history(locked, sources, changes):
    """A historical repurchase case uses the reported program value and average price.

    A live plan (2026-11-15) priced Apple's fiscal 2026 buybacks at cash-flow
    payments over program shares (USD 290.88) while the reported history showed
    the program's own price (USD 288.89); the memo quoted both and the review
    flagged it. A case whose amount is within 3% of a reported year's buybacks
    takes that year's program value and price.
    """
    notes=[]
    rows=[(sid,row) for sid,row in _history_rows(sources)
          if isinstance(row.get('average_repurchase_price'),(int,float)) and isinstance(row.get('shares_repurchased'),(int,float))
          and row['average_repurchase_price']>0 and row['shares_repurchased']>0]
    for case in locked.get('repurchase_tests') or []:
        if not isinstance(case,dict) or case.get('interpretation')!='historical_retrospective_conditional':
            continue
        amount,price=case.get('amount'),case.get('price')
        if not all(isinstance(x,(int,float)) and not isinstance(x,bool) and x>0 for x in (amount,price)):
            continue
        for sid,row in rows:
            value=row['average_repurchase_price']*row['shares_repurchased']
            if not any(isinstance(candidate,(int,float)) and candidate>0 and abs(amount/candidate-1)<=.03
                       for candidate in (value,row.get('repurchases'))):
                continue
            if abs(price/row['average_repurchase_price']-1)>.0005 or abs(amount/value-1)>.0005:
                previous={'amount':amount,'price':price}
                case['amount']=round(value)
                case['price']=row['average_repurchase_price']
                if isinstance(case.get('source_refs'),list) and sid not in case['source_refs']:
                    case['source_refs']=[*case['source_refs'],sid]
                changes.append({'field':f'repurchase_tests.{case.get("name")}','previous':previous,
                                'pinned':{'amount':case['amount'],'price':case['price']},'source_refs':[sid],
                                'reason':'historical case set to the reported program value and average price of the matching fiscal year'})
                notes.append(f'The historical repurchase case {case.get("name")} uses the reported fiscal {str(row.get("fiscal_year_end",""))[:4]} '
                             f'program value and average price from the reported history [{sid}].')
            break
    return notes


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
            if ref.startswith('assumption:') and field in ('other_equity_claims', 'nonoperating_assets', 'dilutive_shares'):
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
                  'counterfactuals','entry_policy','repurchase_tests','rate_history'):
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
        stub=((result.get('discounting') or {}).get('timing') or {}).get('stub_years') or 0
        timing=('at its discount time from the valuation date (the first year counts only its unexpired fraction and later flows '
                'fall at year k minus the stub)' if stub else 'at integer model years')
        lines += ['', 'Entry earnings use the first explicit forecast year, not trailing earnings; exit earnings use the final explicit year. '
            'The return comparison holds operating cash flows, opening financing/claims and shares fixed and changes only the exit valuation. '
            f'All annual FCFF is distributed under the debt/claims-settled diagnostic {timing}; this is not a common-share payout, '
            'ongoing buyback or stock-price-return forecast. Multiple/return alternatives do not change the house DCF or its cases.']
    repurchases=diagnostics.get('repurchase_tests')
    if isinstance(repurchases,list) and repurchases:
        lines += ['', '### Conditional repurchase economics', '',
            '| Case / scope | Cash spent | Price / share | Shares retired | Headline value / remaining share | Change from original |',
            '|---|---:|---:|---:|---:|---:|']
        for row in repurchases:
            refs=row['source_refs'] if isinstance(row['source_refs'],list) else [row['source_refs']]
            citations=' '.join('['+ref.strip().strip('[]')+']' for ref in refs)
            lines.append(f'| {clean(row["label"])} / {clean(row["interpretation"]).replace("_"," ")} {citations} | '
                f'{currency} {row["amount"]/1e9:,.1f} billion | {currency} {row["price"]:,.2f} | {row["shares_repurchased"]/1e6:,.1f} million | '
                f'{currency} {row["post_repurchase_weighted_per_share_value"]:,.2f} | '
                f'{currency} {row["weighted_value_change"]["dollar_delta"]:+,.2f} |')
        lines += ['', 'Each independent case spends the pinned selected nonoperating cash and retires shares once while keeping operating value fixed. '
            'Historical-price tests compare with current underwriting, not historical fair value or an observed historical loss. '
            'Forward illustrations are not repurchase forecasts; the same cash is not also counted as a dividend or future FCFF distribution.']
    return lines


def _base_headline(result):
    """True when the base case carries the whole weight (the house convention since 2026-11-26)."""
    scenarios=(result or {}).get('scenarios') or {}
    return all(abs(((scenarios.get(name) or {}).get('probability') or 0)-weight)<1e-9 for name,weight in DEFAULT_WEIGHTS.items())


def _role(name, probability, headline):
    if headline:
        return 'headline' if name=='base' else 'stress case'
    return f'{probability:.0%}'


# Claim components in the order the bridge prints them.
CLAIM_LABELS = (('noncontrolling_interests', 'Less noncontrolling interests'),
                ('redeemable_noncontrolling_interests', 'Less redeemable noncontrolling interests'),
                ('preferred_stock', 'Less preferred stock'),
                ('defined_benefit_obligations', 'Less unfunded pension and postretirement obligations, before tax'))
NONOPERATING_LABELS = {'long_term_investments': 'long-term investments', 'securities_noncurrent': 'noncurrent securities',
                       'equity_method_investments': 'equity-method investments',
                       'other_long_term_investments': 'other long-term investments',
                       'equity_securities_nonmarketable': 'non-marketable equity securities',
                       'equity_securities_marketable': 'marketable equity securities'}


def _equity_bridge_exhibit(plan, result, currency):
    """Base-case enterprise value to value per share, each line with its source and basis."""
    base=(result.get('scenarios') or {}).get('base') or {}
    if not isinstance(base.get('enterprise_value'),(int,float)) or not isinstance(base.get('per_share_value'),(int,float)):
        return []
    def money(value):
        return (f'{currency} 0' if not value else f'{currency} {value/1e9:,.1f} billion' if abs(value)>=1e9 else
                f'{currency} {value/1e6:,.0f} million')
    def cite(refs):
        refs=[refs] if isinstance(refs,str) else refs if isinstance(refs,list) else []
        ids=[ref.strip().strip('[]') for ref in refs if isinstance(ref,str) and re.fullmatch(r'\[?S\d+\]?',ref.strip())]
        return ' '+' '.join(f'[{ref}]' for ref in dict.fromkeys(ids)) if ids else ''
    refs=(plan or {}).get('source_refs') or {}
    dates=(plan or {}).get('input_dates') or {}
    claims=(plan or {}).get('other_claims_bridge') or {}
    assets=(plan or {}).get('nonoperating_assets_bridge') or {}
    shares=(plan or {}).get('share_count_bridge') or {}
    day=((dates.get('net_debt') or {}).get('instant')) or 'the balance-sheet date'
    rows=[('Enterprise value (present value of base-case free cash flow to the firm)',money(base['enterprise_value']),'computed'),
          ('Less net debt',money(base['net_debt']),f'debt less selected cash on {day}'+cite(refs.get('net_debt')))]
    components=claims.get('components') if isinstance(claims.get('components'),dict) else None
    if components is not None:
        lease=claims.get('finance_lease_claim',0) or 0
        lease_refs=[fact.get('source_id') for fact in claims.get('source_facts') or [] if isinstance(fact,dict)]
        rows.append(('Less finance leases',money(lease),
                     'within net debt' if claims.get('finance_leases_included_in_net_debt') else
                     'reported finance lease liabilities'+cite(lease_refs)
                     if claims.get('reported_finance_lease_liabilities') is not None else 'assumed zero: none reported'))
        for name,label in CLAIM_LABELS:
            item=components.get(name)
            if not isinstance(item,dict):
                rows.append((label,money(0),'assumed zero: none reported'))
                continue
            if name=='preferred_stock' and item.get('carrying_value_not_deducted') is not None:
                rows.append((label,money(0),f'carrying value {money(item["carrying_value_not_deducted"])} counted through '
                                            'conversion shares in the dilutive increment'+cite(item.get('source_refs'))))
            elif name=='defined_benefit_obligations':
                text='recognized liabilities of underfunded plans'
                if item.get('overfunded_plan_assets'):
                    text+=f'; {money(item["overfunded_plan_assets"])} of overfunded plan assets not credited'
                rows.append((label,money(item['value']),text+cite(item.get('source_refs'))))
            else:
                rows.append((label,money(item['value']),'carrying value'+cite(item.get('source_refs'))))
    else:
        rows.append(('Less other equity claims',money(base['other_equity_claims']),'finance leases and identified claims'+
                     cite(refs.get('other_equity_claims'))))
    added=assets.get('components') if isinstance(assets.get('components'),dict) else {}
    if 'nonoperating_assets' in ((plan or {}).get('actuals') or {}):
        names=('carrying value: '+', '.join(NONOPERATING_LABELS.get(name,name.replace('_',' ')) for name in added) if added else
               'none outside the selected cash' if assets.get('not_added') else 'assumed zero: none reported')
        rows.append(('Plus non-operating investments',money(base.get('nonoperating_assets',0)),
                     names+cite(refs.get('nonoperating_assets'))))
    rows.append(('Equity value',money(base['equity_value']),'computed'))
    count=lambda value:f'{value/1e6:,.1f} million'
    rows.append(('Shares outstanding',count(base['shares_outstanding']),
                 f'on {(dates.get("shares_outstanding") or {}).get("instant","the reported date")}'+cite(refs.get('shares_outstanding'))))
    if 'dilutive_shares' in ((plan or {}).get('actuals') or {}):
        period=shares.get('period') or dates.get('dilutive_shares') or {}
        basis=('diluted less basic weighted-average shares, fiscal year to '+str(period.get('end_date','')) if
               shares.get('status')=='reported' else 'reported basic and diluted shares are equal' if
               shares.get('status')=='reported_basic_equals_diluted' else 'assumed zero: no weighted-average shares reported')
        rows.append(('Plus dilutive increment',count(base.get('dilutive_shares',0)),basis+cite(refs.get('dilutive_shares'))))
        rows.append(('Diluted shares',count(base.get('diluted_shares',base['shares_outstanding'])),'computed'))
    rows.append(('Value per share',f'{currency} {base["per_share_value"]:,.2f}','computed'))
    lines=['','### Enterprise value to value per share (base case)','','| Line | Amount | Basis |','|---|---:|---|']
    lines+=[f'| {label} | {amount} | {basis} |' for label,amount,basis in rows]
    notes=[]
    quoted=assets.get('reported_quoted_market_value_not_used')
    if isinstance(quoted,dict) and isinstance(quoted.get('value'),(int,float)):
        notes.append(f'The reported quoted market value of the listed equity-method stakes, {money(quoted["value"])}'
                     f'{cite(quoted.get("source_refs"))}, is not used; the bridge carries investments at book value.')
    if assets or components is not None:
        notes.append('Claims and investments are carrying values on the balance-sheet date of net debt; pension obligations '
                     'are before tax. A category with no reported concept is zero by assumption. Stock-based '
                     'compensation stays an operating expense; the dilutive increment counts awards already granted.')
    return lines+[part for note in notes for part in ('',note)]


def _scale_exhibit(plan, result, currency):
    """Year-10 scale against today, so a reader can judge the size the forecast implies.

    A memo audit (2026-11-15) found no memo discussed a base-case year-10 EBIT of
    USD 574 billion or a terminal enterprise value near USD 10 trillion.
    """
    base=(result.get('scenarios') or {}).get('base') or {}
    flows=base.get('cashflows') or []
    revenue=((plan or {}).get('actuals') or {}).get('revenue')
    if not flows or not isinstance(revenue,(int,float)) or revenue<=0:
        return []
    last=flows[-1]
    market=((result.get('computed_diagnostics') or {}).get('valuation_multiples') or {}).get('market') or {}
    def billions(value):
        return f'{currency} {value/1e9:,.1f} billion'
    text=(f'Base case, final forecast year {last.get("year")}: revenue {billions(last["revenue"])} '
          f'({last["revenue"]/revenue:,.1f} times the last reported {billions(revenue)}), EBIT {billions(last["ebit"])} '
          f'({last["ebit_margin"]:.1%} margin) and free cash flow to the firm {billions(last["fcff"])}.')
    terminal=base.get('terminal_enterprise_value')
    if isinstance(terminal,(int,float)):
        text+=f' Terminal enterprise value at that year: {billions(terminal)}'
        enterprise=market.get('enterprise_value')
        if isinstance(enterprise,(int,float)) and enterprise>0:
            text+=f', {terminal/enterprise:,.1f} times today\'s market enterprise value of {billions(enterprise)}'
        text+='.'
    return ['', '### Scale check', '', text]


def reported_tables_section(sources):
    """The reported history and breakdown tables, printed by Python for the reader.

    Writers re-typed these tables and introduced errors ("not shown" for a reported
    56.6% segment margin, 2026-11-15); the reader now gets Python's copy.
    """
    lines=[]
    for source in sources:
        data=source.get('data') if isinstance(source.get('data'),dict) else {}
        baseline=source.get('historical_baseline') or data.get('historical_baseline')
        if baseline and baseline.get('rows'):
            text=render_baseline(baseline,source.get('id'),include_roic=False).split('\n')
            notes=text[text.index('Basis notes:'):] if 'Basis notes:' in text else []
            text=text[:len(text)-len(notes)]
            caption,header,rows=text[0],text[1].split(' | '),[row.split(' | ') for row in text[2:] if row.strip()]
            lines+=['', caption, '', '| Measure | '+' | '.join(row[0] for row in rows)+' |',
                    '|---|'+'---:|'*len(rows)]
            for index,name in enumerate(header[1:],1):
                lines.append(f'| {name} | '+' | '.join(row[index] if index<len(row) else 'n/a' for row in rows)+' |')
            if notes:
                lines+=['']+notes
            quarterly=render_quarterly(baseline,source.get('id'),markdown=True)
            if quarterly:
                lines+=quarterly.split('\n')
            returns=render_roic(baseline,source.get('id')).split('\n')
            if len(returns)>2:
                basis=returns.index('ROIC basis:') if 'ROIC basis:' in returns else len(returns)
                caption,header,rows=returns[0],returns[1].split(' | '),[row.split(' | ') for row in returns[2:basis] if row.strip()]
                lines+=['', caption, '', '| Measure | '+' | '.join(row[0] for row in rows)+' |', '|---|'+'---:|'*len(rows)]
                for index,name in enumerate(header[1:],1):
                    lines.append(f'| {name} | '+' | '.join(row[index] if index<len(row) else 'n/a' for row in rows)+' |')
                lines+=['']+returns[basis:]
        breakdowns=source.get('breakdown_tables') or data.get('breakdown_tables')
        if breakdowns:
            lines+=['', render_breakdowns(breakdowns,source.get('id'))]
    return (['', '## Reported history and segments (computed by Python from filings)']+lines) if lines else []


def valuation_section(plan, result):
    if not result:
        return '\n## Executable valuation\n\nNo value target is calculated: essential valuation inputs are unresolved.\n'
    # Reader values use the calculated view; saved assumptions retain the editable
    # component/rate inputs without introducing duplicate derived paths.
    plan, _, _ = materialize(plan)
    currency = result.get('currency', plan['currency'])
    lines = ['\n## Executable valuation', '',
             'The following values are recomputed by Python from the editable assumptions; forecasts are house assumptions.', '',
             f'| Scenario | {"Role" if _base_headline(result) else "Probability"} | Discount rate | Terminal growth | Value per share | Value / price |',
             '|---|---:|---:|---:|---:|---:|']
    for name in ('bear','base','bull'):
        scenario, inputs = result['scenarios'][name], plan['scenarios'][name]
        rate = (f'{scenario["initial_rate"]:.2%} → {scenario["terminal_rate"]:.2%}' if 'terminal_rate' in scenario and
                abs(scenario['initial_rate']-scenario['terminal_rate'])>1e-12 else f'{scenario.get("initial_rate", inputs.get("wacc", 0)):.2%}')
        growth = scenario.get('terminal_growth', inputs.get('terminal_growth'))
        ratio = f'{scenario["value_to_price"]:.2f}' if 'value_to_price' in scenario else 'n/a'
        lines.append(f'| {name.title()} | {_role(name,inputs["probability"],_base_headline(result))} | {rate} | {growth:.2%} | {currency} {scenario["per_share_value"]:,.2f} | {ratio} |')
    thin=[name for name in ('bear','base','bull') if 'terminal_rate' in result['scenarios'][name] and
          result['scenarios'][name].get('terminal_spread') is not None and result['scenarios'][name]['terminal_spread']<.02]
    if thin:
        lines += ['', 'Terminal rate within two points of terminal growth in '+', '.join(thin)+': the value is fragile to '
                      'small changes in either.']
    lines += ['',('Headline (base case) intrinsic value per share' if _base_headline(result) else 'Weighted intrinsic value per share')+
              f': {currency} {result["weighted_per_share_value"]:,.2f}'+
              (f'; value / price {result["weighted_value_to_price"]:.2f}' if 'weighted_value_to_price' in result else ''),
              '',f'Forecast basis: {plan["assumptions_label"]}',
              '', 'Amounts use whole currency units and actual share counts. Net debt includes the selected cash deduction once. '
                  'See assumptions.json, cashflows.csv and sensitivity.csv for the inputs and calculations.']
    dates=plan.get('input_dates',{})
    if not isinstance(dates,dict):
        dates={}
    measurements=[]
    for field,label in [('net_debt','net debt'),('other_equity_claims','other claims'),
                        ('nonoperating_assets','non-operating investments'),
                        ('shares_outstanding','shares outstanding'),('dilutive_shares','dilutive increment (fiscal year to)'),
                        ('market_price','regular-session quote')]:
        if field in ('nonoperating_assets','dilutive_shares') and field not in dates:
            continue
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
    lines += _equity_bridge_exhibit(plan, result, currency)
    lines += _continuity_exhibit(result)
    lines += _scale_exhibit(plan, result, currency)
    lines += base_rates.memo_lines(result.get('base_rates'), currency)
    lines += _market_consistency_exhibits(result, currency)
    cases=result.get('counterfactuals',{})
    if cases:
        lines += ['', '### Conditional counterfactuals', '',
                  'These change specified base-case forecasts only. Reported actuals and the equity bridge stay fixed; '
                  'the headline value does not change. These are computations, not forecasts.', '',
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
    lines += _forecast_roic_lines(result, currency)
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


def _forecast_roic_lines(result, currency):
    """Forecast ROIC and 3-year incremental ROIC per scenario, then the return on new capital after the forecast.

    Mauboussin critique F3 (2026-11-26): forecast returns held near 100% for ten
    years and fell to the terminal rate in one, with nothing in the memo showing it.
    """
    returns=result.get('forecast_return_on_capital')
    if not returns or not returns.get('scenarios'):
        return []
    years=max(len(entry['years']) for entry in returns['scenarios'].values())
    def pct(value):
        return f'{value:.1%}' if isinstance(value,(int,float)) else 'n/m'
    lines=['', '### Forecast return on invested capital (computed by Python)', '',
           '| Measure | '+' | '.join(f'Year {year}' for year in range(1,years+1))+' | After the forecast |',
           '|---|'+'---:|'*(years+1)]
    for name in ('bear','base','bull'):
        entry=returns['scenarios'].get(name)
        if not entry:
            continue
        cells=[pct(row['return_on_invested_capital']) for row in entry['years']]
        lines.append(f'| {name.title()} ROIC | '+' | '.join(cells)+' | |')
        incremental=['n/a' if index<3 else pct(row['incremental_return_3y']) for index,row in enumerate(entry['years'])]
        lines.append(f'| {name.title()} 3-year incremental ROIC | '+' | '.join(incremental)+
                     f' | {pct(entry["return_on_new_capital_after_forecast"])} |')
    refs=', '.join(returns.get('source_refs') or [])
    rnd=any(entry.get('continuing_value_nopat_includes_rnd') for entry in returns['scenarios'].values())
    opening=returns['opening_invested_capital']
    amount=f'{opening/1e9:,.1f} billion' if abs(opening)>=1e8 else f'{opening:,.0f}'
    lines += ['', f'Invested capital starts at {currency} {amount}, the reported '
                  f'operating-approach invested capital including goodwill and acquired intangibles at the fiscal year end '
                  f'{returns.get("fiscal_year_end") or "of the last report"}'+(f' [{refs}]' if refs else '')+', and adds each '
                  'year\'s net reinvestment. ROIC is NOPAT over average capital; 3-year incremental ROIC is the NOPAT change '
                  'over three years over the capital change over the three years ending a year earlier, so it starts in year '
                  '4 (n/a before; n/m where capital fell). Forecast NOPAT is EBIT after the plan\'s tax rate, while the history '
                  'table uses EBITA after the cash tax rate, so the definitions differ slightly. The last column is the return '
                  'on new capital after the forecast, the terminal return'+
                  (', earned on NOPAT and capital that include net R&D investment, which the forecast years expense' if rnd else '')+
                  '. Display only; no check uses these figures.']
    return lines


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
        return _reinvestment_lines(result)+_cost_path_lines(result)
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
    return lines+_reinvestment_lines(result)+_cost_path_lines(result)


def _reinvestment_text(check):
    cells='; '.join(f'{name} {row["net_reinvestment"]/1e9:,.1f} billion'+
                    (f' ({row["sales_to_capital"]:.2f})' if row.get('sales_to_capital') is not None else '')
                    for name,row in check['scenarios'].items())
    base=check.get('net_reinvestment')
    reported=(f'the reported {base/1e9:,.1f} billion a year' if isinstance(base,(int,float)) and base>0 else
              'the reported results (net reinvestment not positive, so no floor)')
    return (f'Net reinvestment over forecast years 1-{REINVESTMENT_WINDOW} against {reported} (fiscal year ending '
            f'{check["fiscal_year_end"]}; {check["years"]}-year incremental sales-to-capital {check["incremental_sales_to_capital"]:.2f}'
            f'{_rate_basis_text(check)}) [{", ".join(check["source_refs"])}]: {cells}.')


def _cost_path_lines(result):
    check=result.get('cost_path_continuity')
    if not check or not check.get('scenarios'):
        return []
    cells='; '.join(f'{name} {row["final_ebit_margin"]:.1%} '+
                    (f'(ceiling {row["ceiling"]:.1%})' if row.get('ceiling') is not None else '(no ceiling)')
                    for name,row in check['scenarios'].items())
    return ['', '### Final-year margin against the reported history', '',
            f'Reported operating margins over {check["years"]} years: peak {check["max_operating_margin"]:.1%}, average '
            f'{check["average_operating_margin"]:.1%} [{", ".join(check["source_refs"])}]'+_run_rate_text(check)+
            f'. Final-year EBIT margin: {cells}. Ceilings: bear at most the average where it is positive; base and bull at '
            'most the higher of the peak and the latest twelve-month margin, plus 5 points (less for low-margin '
            'businesses, as in the year-1 check).']


def _reinvestment_lines(result):
    check=result.get('reinvestment_continuity')
    if not check or not check.get('scenarios'):
        return []
    lines=['', '### Reinvestment against the last reported results', '', _reinvestment_text(check)+' '+check['basis']]
    for name,row in check['scenarios'].items():
        if row.get('stated_basis') and not row.get('within_band'):
            lines.append(f'- {name.title()} reinvestment, stated basis: {row["stated_basis"]}')
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
        lines.append(f'| {"Headline (base case)" if _base_headline(result) else "Probability-weighted value"} | {cell(weighted.get("implied_return"),"{:.2%}")} | '
                     f'{cell(weighted.get("required_return_flat_equivalent"),"{:.2%}")} | {cell(weighted.get("gap_percentage_points"),"{:.2f} pt")} | |')
        lines += ['', 'The implied return is the one discount rate, applied in every year and in the terminal value, at which value '
                      'equals the price; the required return is the same calculation at the house value. This describes what the '
                      'price implies, not a forecast of shareholder returns. A terminal spread under two points marks a fragile '
                      'result, except where the terminal return follows the rate: the continuing value is then NOPAT over the '
                      'rate, so rates from 0.50% are searched, below terminal growth.']
        money=lambda value: f'{currency} {value:,.2f}'
        headline_name='headline' if _base_headline(result) else 'weighted'
        for name,row in [*implied['scenarios'].items(),(headline_name,weighted)]:
            if not isinstance(row.get('implied_return'),(int,float)):
                sentence=_implied_return_sentence(row,name,money)
                if sentence:
                    lines.append('- '+name.title()+': '+sentence.replace('What the price implies: ','',1))
    panel=result.get('convention_panel')
    if panel:
        lines += ['', '### Value under alternative conventions (base case, one change at a time)', '',
                  '| Alternative | Value per share | Change | Status |', '|---|---:|---:|---|']
        kinds={'sensitivity':' (sensitivity)','horizon':' (horizon)','rate_regime':' (rate regime)'}
        for row in panel['rows']:
            value=f'{currency} {row["per_share_value"]:,.2f}' if row.get('per_share_value') is not None else '—'
            change=f'{row["dollar_delta"]:+,.2f}' if row.get('dollar_delta') is not None else '—'
            status=row['status'].replace('_',' ')+kinds.get(row.get('kind'),'')
            lines.append(f'| {row["label"]} | {value} | {change} | {status} |')
        if panel.get('largest_meaningful_step'):
            lines += ['', f'Largest convention step: {panel["largest_meaningful_step"].replace("_"," ")}. Rows carry no probability and are never tuned to the price.']
        if panel.get('exhibit_basis') and any(row.get('kind') in ('horizon','rate_regime') for row in panel['rows']):
            lines += ['', panel['exhibit_basis']]
        history=panel.get('rate_history') or {}
        if isinstance(history.get('median_per_share_value'),(int,float)):
            lines += ['', f'Across the {history["calculated"]} monthly pairs from {history["first"][:7]} to {history["last"][:7]} the '
                          f'median base value is {currency} {history["median_per_share_value"]:,.2f}'+
                          (f'; {len(history["not_calculated"])} months give no valid rate path' if history.get('not_calculated') else '')+'.']
        notes=panel.get('exhibit_notes') or []
        if notes:
            lines += ['']+[f'- {note}' for note in notes]
        note=country_risk.render_note(panel)
        if note:
            lines += ['', note]
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


def _implied_return_sentence(row, which, money):
    """What one flat rate says about the price, including when no rate in the searched range gives it.

    The summary sentence was dropped without explanation when no rate solved (Apple
    restatement, Druckenmiller critique F5, 2026-11-26).
    """
    implied,required=row.get('implied_return'),row.get('required_return_flat_equivalent')
    house=(f' The house cash flows at the house rate path are equivalent to one flat rate of {required:.2%}.'
           if isinstance(required,(int,float)) else '')
    if isinstance(implied,(int,float)) and isinstance(required,(int,float)):
        return (f'What the price implies: one flat discount rate of {implied:.2%} equates the {which} value '
                f'with the price; the house cash flows at the house rate path are equivalent to '
                f'{required:.2%} (difference {row["gap_percentage_points"]:.2f} points).')
    solutions=row.get('implied_return_solutions') or []
    if len(solutions)>1:
        return ('What the price implies: more than one flat discount rate equates the '+which+' value with the price ('+
                ', '.join(f'{rate:.2%}' for rate in solutions)+'), so no single implied rate is reported.'+house)
    bound=row.get('price_bound')
    if isinstance(bound,dict):
        low,high=bound['search_range']
        if bound['price_position']=='above_highest_value':
            return (f'What the price implies: no flat discount rate between {low:.2%} and {high:.0%} reconciles the price; the '
                    f'highest {which} value any rate in that range gives is {money(bound["highest_value"])}, at '
                    f'{bound["rate_at_highest_value"]:.2%}.'+house)
        if bound['price_position']=='below_lowest_value':
            return (f'What the price implies: no flat discount rate between {low:.2%} and {high:.0%} reconciles the price; the '
                    f'lowest {which} value any rate in that range gives is {money(bound["lowest_value"])}, at '
                    f'{bound["rate_at_lowest_value"]:.2%}.'+house)
    return None


def computed_summary(plan, result):
    """The valuation summary Python places at the top of every memo.

    The model writes interpretation around it and never re-types it: a live first
    draft (2026-11-14) mis-copied years, signs and ranges when it rebuilt these
    tables itself.
    """
    if not result or not result.get('scenarios'):
        return ''
    currency=result.get('currency') or (plan or {}).get('currency','')
    def money(value):
        return f'{currency} {value:,.2f}'
    headline=_base_headline(result)
    lines=['## Valuation summary (computed by Python)','',
           f'| Scenario | {"Role" if headline else "Probability"} | Value per share | Value / price |','|---|---:|---:|---:|']
    for name in ('bear','base','bull'):
        row=result['scenarios'][name]
        ratio=f'{row["value_to_price"]:.2f}' if 'value_to_price' in row else 'n/a'
        lines.append(f'| {name.title()} | {_role(name,row["probability"],headline)} | {money(row["per_share_value"])} | {ratio} |')
    if not headline:
        weighted=result['weighted_per_share_value']
        lines.append(f'| Weighted | 100% | {money(weighted)} | '+
                     (f'{result["weighted_value_to_price"]:.2f} |' if 'weighted_value_to_price' in result else 'n/a |'))
    # The index under the same conventions, so the ratio is read against it and not
    # only against 1.00 (the house pairing values the index itself below its price).
    benchmark=result.get('index_benchmark') or {}
    if isinstance(benchmark.get('value_to_price'),(int,float)):
        lines.append(f'| S&P 500 under the same conventions | index benchmark | — | {benchmark["value_to_price"]:.2f} |')
    if headline:
        lines+=['','The base case is the headline value: the expected path. Bear and bull are stress cases without probabilities.']
    dates=(plan or {}).get('input_dates',{}) if isinstance((plan or {}).get('input_dates'),dict) else {}
    quoted=((dates.get('market_price') or {}).get('observed_at') or '')[:10]
    if result.get('market_price') is not None:
        lines+=['',f'Price {money(result["market_price"])}'+(f', observed {quoted}' if quoted else '')+
                (f'; valuation date {result["valuation_date"]}.' if result.get('valuation_date') else '.')]
    if isinstance(benchmark.get('value_to_price'),(int,float)):
        lines+=['',f'Index benchmark: a stylized S&P 500 at the {benchmark["observed_at"]} Treasury rate of '
                   f'{benchmark["risk_free_rate"]:.2%} and implied premium of {benchmark["equity_risk_premium"]:.2%} is worth '
                   f'{benchmark["value_to_price"]:.2f} of its price under these conventions: new capital earns '
                   f'{benchmark["market_return_on_new_capital"]:.0%} for {benchmark["excess_return_years"]} years and then its '
                   f'cost, where the premium assumes {benchmark["market_return_on_new_capital"]:.0%} forever. It is a steady '
                   'state, not a reproduction of Damodaran\'s cash flows: those add five years of consensus growth and need '
                   'the index\'s earnings and return on equity, which his monthly workbook does not carry.']
    implied=(result.get('implied_return_at_price') or {}).get('probability_weighted_value') or {}
    sentence=_implied_return_sentence(implied,'headline' if headline else 'weighted',money)
    if sentence:
        lines+=['',sentence]
    requirements=(result.get('price_requirements') or {}).get('rows') or []
    if requirements:
        lines+=['','What the price requires (base case, one input at a time, all others fixed; rows are alternatives):','',
                '| Input | House | Needed for value = price |','|---|---:|---|']
        def over(path):
            if not isinstance(path,dict):
                return ''
            measure='a year compounded' if path.get('measure')=='compound_annual_growth' else 'average'
            return f'{path["over_forecast"]:.1%} {measure} over {path["years"]} years'
        def shifted(delta,path,note=''):
            text=f'{delta*100:+.1f} points in every explicit year'
            return text+(f': {path["year1"]:.1%} in year 1, {over(path)}{note}' if isinstance(path,dict) else '')
        def base_rate(row,which,path):
            # Where the compound growth falls in the published base rate for this size of company.
            if row['input']!='revenue_growth' or not isinstance(path,dict):
                return ''
            return base_rates.summary_note(result.get('base_rates'),which,path.get('years'))
        def capital_path(row):
            path=row.get('house_sales_to_capital_path')
            return f'{path[0]:.2f} in year 1 to {path[-1]:.2f} in year {len(path)}' if path else f'{row["house"]:.2f}'
        for row in requirements:
            house=(f'{row["house"]:.1%} in year 1'+(f', {over(row.get("house_path"))}'+base_rate(row,'house',row.get('house_path'))
                                                    if row.get('house_path') else '')
                   if row['kind']=='shift' else
                   capital_path(row) if row['input']=='sales_to_capital' else f'{row["house"]:.2%}')
            if row['status']=='solved':
                needed=(shifted(row['needed'],row.get('needed_path'),base_rate(row,'needed',row.get('needed_path')))
                        if row['kind']=='shift' else
                        f'{row["needed"]:.2f}' if row['input']=='sales_to_capital' else f'{row["needed"]:.2%}')
            elif row['status']=='not_reachable':
                limit=(shifted(row['at'],row.get('at_path'),base_rate(row,'needed',row.get('at_path'))) if row['kind']=='shift' else
                       f'{row["at"]:.2f}' if row['input']=='sales_to_capital' else f'{row["at"]:.2%}')
                needed=(f'not reachable; the closest is {money(row["closest_value"])} at {limit}, still '
                        f'{"above" if row["direction"]=="above" else "below"} the price')
            else:
                needed='not calculated'
            lines.append(f'| {row["label"]} | {house} | {needed} |')
        # Which inputs can close the gap is stated by Python: live drafts named
        # one cause in the conclusion and another in the variant view, and both
        # reviews called it a major defect (2026-11-14, 2026-11-15).
        names={'revenue_growth':'revenue growth','ebit_margin':'EBIT margin','terminal_roic':'terminal return on new capital',
               'sales_to_capital':'sales-to-capital','terminal_growth':'terminal growth'}
        def move(row):
            if row['kind']=='shift':
                return f'{row["needed"]*100:+.1f} points in every explicit year'
            if row['input']=='sales_to_capital':
                return f'{capital_path(row)} to {row["needed"]:.2f}'+(' in every year' if row.get('house_sales_to_capital_path') else '')
            return f'{row["house"]:.2%} to {row["needed"]:.2%}'
        closes=[row for row in requirements if row['status']=='solved']
        cannot=[row for row in requirements if row['status']=='not_reachable']
        attribution=('Inputs that alone can bring the base value to the price: '+
                     ('; '.join(f'{names.get(row["input"],row["label"])} ({move(row)})' for row in closes) or 'none')+'.')
        if cannot:
            attribution+=(' Inputs that cannot, within the values the engine accepts: '+
                          ', '.join(names.get(row['input'],row['label']) for row in cannot)+'.')
        attribution+=(' These are alternatives; the inputs interact.' if len(closes)!=1 else
                      ' Only this input alone can close the difference.')
        lines+=['',attribution]
    panel=[row for row in ((result.get('convention_panel') or {}).get('rows') or [])
           if row.get('kind')=='convention' and row.get('status')=='calculated' and row.get('dollar_delta') is not None]
    if panel:
        panel=sorted(panel,key=lambda row:-abs(row['dollar_delta']))[:3]
        lines+=['','Largest convention steps (base case, one change at a time; never added together): '+
                '; '.join(f'{row["label"].split(" (")[0].split(":")[0]} {money(row["per_share_value"])} ({row["dollar_delta"]:+,.2f})'
                          for row in panel)+'.']
    continuity=result.get('forecast_continuity')
    if continuity:
        lines+=['',f'Year-1 EBIT margin against the reported {continuity["reported_operating_margin"]:.1%}: '+
                '; '.join(f'{name} {row["year_one_ebit_margin"]:.1%}' for name,row in continuity['scenarios'].items())+'.']
    check=result.get('reinvestment_continuity')
    if check and check.get('scenarios'):
        lines+=['',_reinvestment_text(check)]
    return '\n'.join(lines)+'\n'


def _with_summary(memo, plan, result):
    """Place the computed summary after the memo's title line."""
    summary=computed_summary(plan,result)
    # Only Python writes this block: drop any copy in the model's text first.
    # The summary has no headings, so a copy ends at the next heading of any level;
    # stopping only at levels 1-2 would delete a memo written with ### sections.
    memo=re.sub(r'(?ms)^## Valuation summary \(computed by Python\)\n.*?(?=^#|\Z)','',memo)
    if not summary:
        return memo
    lines=memo.lstrip().split('\n')
    if lines and lines[0].startswith('# '):
        head,rest=[lines[0]],lines[1:]
        while rest and (not rest[0].strip() or rest[0].startswith('**') or rest[0].strip()=='---'):
            head.append(rest.pop(0))
        return '\n'.join(head).rstrip()+'\n\n'+summary+'\n'+'\n'.join(rest)
    return summary+'\n'+memo


def _render(memo, sources, gaps, plan, result, state):
    # The register carries the same tier label as the memo text.
    refs = '\n'.join(f'- [{s["id"]}{" unverified" if s.get("source_tier") in UNVERIFIED_TIERS else ""}] '
                     f'{s["title"]} — {s.get("url") or "supplied text"}; '
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
    refuted = (latest.get('finding_check') or {}).get('refuted') or []
    if refuted:
        note += ('\n\nRaised by the reviewer and not upheld on a second check:\n' +
                 '\n'.join(f'- {f["severity"]}: {f["issue"]} Check: {f["check_reason"]}' for f in refuted))
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
    body = _label_unverified(_with_summary(memo,plan,result).rstrip() + '\n' + valuation_section(plan,result) +
                             '\n'.join(reported_tables_section(sources)), sources)
    return (body + '\n## Source register\n'+refs+
            '\n\n## Evidence limitations\n'+ ('\n'.join('- '+g for g in gaps) or 'No retrieval gap recorded; source completeness still requires review.')+
            '\n\n## Review status\n'+state.get('quality_status','in_progress')+
            '\n\n'+note+
            '\n\nSoftware checks and model reviews do not establish investment truth or forecast accuracy.\n')


def run(ticker, key, *, profile='balanced', budget=DEFAULT_BUDGET, directory='research-output', evidence='',
        easy_model=None, strong_model=None, progress=lambda message: None, max_revisions=1, review_notes='', extra_sources=None, counterfactuals=None, draft=None, review_model=None,
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
    if _revisions_used(state) > max_revisions:
        raise ValueError(f'Increase max_revisions to at least {_revisions_used(state)} to investigate the new review notes')
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
        calibration, calibration_gaps = gather_us_calibration(_fetch_calibration_bytes, country_risk=True)
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
    if _revisions_used(state) > max_revisions:
        raise ValueError('Increase max_revisions to review the supplemented research snapshot')
    reported=canonical_reported_inputs(state['sources'],as_of=(forecast_plan or state.get('plan',{})).get('valuation_date'))
    core.save(folder/'reported-inputs.json',reported)
    coverage=reported['coverage']
    # Banks and insurers are refused before any paid stage: the operating-company FCFF
    # model does not value them (JPMorgan reached the plan prompt's own refusal only
    # after four paid web calls). A missing operating-income line alone is not refused.
    gate=operating_company_gate(state['sources'],reported)
    gate['refused']=bool(gate.get('financial_institution') or
                         (gate.get('sic') in FINANCIAL_SIC_NEEDING_OPERATING_INCOME and gate.get('operating_income_reported') is False))
    if gate['refused']:
        coverage={**coverage,'complete':False,'conflicts':[gate['message'],*coverage.get('conflicts',[])]}
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
        recovery=('This issuer needs a financial-institution valuation model, which this tool does not provide'
            if gate.get('refused') else
            'Supply primary disclosures with --sources, or start a fresh non-snapshot run with --sec-contact'
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
        # One provider per run keeps the cached evidence prefix warm across stages.
        router.session='iv-'+_digest(str(folder.resolve()))[:24]
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
    def call(name,model,prompt,tokens=6000,web=False,prefix=None):
        thinking=core.reasoning_budget(name)
        cache=name+':'+_digest([prompt,model,tokens,web,core.SYSTEM]+([thinking] if thinking else [])+
                               (['prefix',_digest(prefix)] if prefix else []))[:12]
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
            extra={'prefix':prefix} if prefix else {}
            try:
                value=router.complete(name,model,prompt,max_tokens=tokens,web=web,**extra)
            except core.ReasoningExhausted:
                # Spent and recorded; one retry without thinking rather than a stopped run.
                checkpoint()
                progress(name+core.WITHOUT_REASONING+': '+model)
                value=router.complete(name+core.WITHOUT_REASONING,model,prompt,max_tokens=tokens,web=web,**extra)
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
                FORECAST_SCHEMA+_rate_convention(state['sources'],reported.get('currency'))+UNDERWRITING_GUIDANCE+PLAN_FIELD_RULES+PLAN_SOURCE_RULE+'\nIMMUTABLE reported inputs (copy these values exactly; forecast judgments only):\n'+json.dumps(reported)+
                (('\nReported reinvestment (reference for years 1-3 reinvestment and sales_to_capital):\n'+
                  json.dumps(reinvestment_reference(state['sources']))) if reinvestment_reference(state['sources']) else '')+
                (('\nReported operating margins (reference for the final-year margin ceilings):\n'+
                  json.dumps(cost_path_reference(state['sources'],reported.get('operating_reference'))))
                 if cost_path_reference(state['sources'],reported.get('operating_reference')) else '')+
                base_rates.plan_context(reported)+
                EVIDENCE_ABOVE+'\nDiligence synthesis:\n'+thesis+'\nRetrieval gaps:\n'+gaps+
                # A rerun after a discarded plan would otherwise resend the identical request.
                ('\nAn earlier attempt at this plan was rejected for these reasons; avoid them: '+
                 ' | '.join(state['plan_repair_reasons']) if state.get('plan_repair_reasons') else ''))
        text=call('valuation_plan',strong,prompt,tokens=12000,prefix=_evidence_prefix(context))
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
                state['plan_repair_reasons']=list(failures)
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
                rejected=text
                text=call('valuation_plan_repair'+('' if repair==0 else f'_{repair+1}'),strong,prompt+'\nRejected JSON:\n'+text+
                          '\nRepair this precise validation failure without inventing actuals: '+str(exc)+
                          ('\nEarlier attempts were rejected for these reasons; do not reintroduce them: '+
                           ' | '.join(failures[:-1]) if failures[:-1] else '')+
                          '\nReturn only the complete corrected JSON object, with no analysis before or after it.'+PLAN_FIELD_RULES,tokens=12000,
                          prefix=_evidence_prefix(context))
                if text.strip()==rejected.strip():
                    # A repair that returns the rejected plan unchanged will not
                    # pass on a further paid attempt (Microsoft replay, 2026-11-15).
                    failures.append('the repair returned the rejected plan unchanged')
                    state['plan_repair_reasons']=list(failures)
                    for name in [name for name in state['stages'] if name.startswith('valuation_plan')]:
                        state['stages'].pop(name)
                    checkpoint()
                    raise RuntimeError('Valuation plan repair returned the rejected plan unchanged: '+str(exc)+
                                       '. The rejected plan was discarded; run the same command again for a fresh forecast')
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
            'Write the prose of an investment-committee equity research memo for '+ticker+', '+MEMO_WORDS+'. '+SECTIONS+
            '\nPython places the COMPUTED SUMMARY below at the top of the memo and appends the full computed exhibits '
            'after your text. Do not re-type their tables. Copy any computed or reported figure exactly as it appears in '
            'the summary, the COMPUTED FROM REPORTED FILINGS tables, the valuation output or a source. Do not calculate '
            'new figures (growth rates, margins, per-share amounts, differences or sums); if a figure you need is not '
            'shown, make the point without it. Cite every factual claim with [S#] from the evidence; a causal claim '
            '(driven by, reflected, helped by) needs a source that states that cause. Attribute model '
            'outputs (values, price requirements, implied returns, monitoring cases) to the computed summary or exhibits '
            'in words, and place each [S#] directly after the reported or sourced figure it supports, not after a model output.\n'+
            EVIDENCE_TIER_RULE+'\n'+RUBRIC+'\n'
            'Engage the strongest sourced opposing economic case and answer it with evidence; a target or rating alone '
            'is not an argument. Use the computed monitoring cases, counterfactuals, multiples and repurchase tests for '
            'those topics and say which inputs they hold fixed; reverse-DCF and implied-return figures are conditional '
            'on named fixed assumptions, not market beliefs. GAAP EBIT already expenses stock compensation, which is a '
            'real cost. Missing evidence narrows the conclusion; state each gap once instead of repeating caveats. '
            'Do not invent transcripts, guidance, consensus, dates or quotes. Valuation research only: no ratings, '
            'target prices, entry levels, margins of safety, upside or downside, or position sizing; write value / '
            'price. Plain analytical prose without slogans, superlatives or rhetorical contrast formulas. Refer to the '
            'computed summary, exhibits and reported tables by name; never mention Python, prompts or instructions, and '
            'add no tables of your own.'
            '\nCOMPUTED SUMMARY (placed above your text):\n'+computed_summary(plan,result)+
            EVIDENCE_ABOVE+'\nSynthesis:\n'+thesis+'\nValuation:\n'+valuation_context+'\nGaps:\n'+gaps,tokens=MEMO_TOKENS,
            prefix=_evidence_prefix(context))
        state['current_memo']=_label_unverified(draft,state['sources'])
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
            'Adopt a reviewer-proposed number only when it appears in the computed output or a source; otherwise '
            'keep the computed figure. Copy computed and reported figures exactly and do not calculate new ones; '
            'attribute model outputs to the computed summary in words and place each [S#] after the sourced figure '
            'it supports. Python places the computed summary above your text and the exhibits after it; do not '
            're-type their tables. Do not resolve a finding by inventing '
            'a policy discount, nonrecurring adjustment, source passage or double-counted cash distribution. '
            'Return the full revised memo only, '+MEMO_WORDS+'. The current prose is '+str(prose_words(memo))+
            ' words; the revision must not be longer, and should be shorter where the current prose exceeds 3,000 words. '
            'Keep the required section order.\n'+EVIDENCE_TIER_RULE+'\n'+RUBRIC+EVIDENCE_ABOVE+
            ('\nThe forecast correction for these findings was rejected; the valuation below is unchanged. Do not '
             'describe any forecast change as made.' if isinstance(candidate,dict) and candidate.get('correction_rejected') else '')+
            ('\nNotes on the plan now valued:\n'+'\n'.join(state.get('plan_gap_notes',[])) if state.get('plan_gap_notes') else '')+
            '\nValuation:\n'+valuation_context+'\nMemo:\n'+memo+'\nFindings:\n'+json.dumps(combined)+'\nAdjudicated model response:\n'+json.dumps(candidate),tokens=MEMO_TOKENS,
            prefix=_evidence_prefix(context))
        state['current_memo']=_label_unverified(corrected,state['sources'])
        state['iteration']=iteration+1
        state.pop('pending_revision',None)
        checkpoint()

    while _revisions_used(state)<=max_revisions:
        iteration=state['iteration']
        memo=state['current_memo']
        pending=state.get('pending_revision')
        if _pending_revision_matches(pending,state):
            if _revisions_used(state)>=max_revisions:
                raise ValueError(f'Increase max_revisions to at least {_revisions_used(state)+1} to resume the pending prose revision')
            finish_revision(iteration,memo,pending['findings'],pending['model_response'])
            continue
        if pending:
            state.pop('pending_revision',None)
            checkpoint()
        # Free checks before the paid review, and one targeted fix call when they
        # find defects (untraced or unlabelled figures, added convention rows,
        # length). Edits are exact quote replacements applied by Python.
        computed_inputs=(result,plan,_computed_tables(state['sources']))
        issues=prereview_checks(_with_summary(memo,plan,result),state['sources'],computed_inputs) if result else []
        fixed=state.setdefault('prereview_fixes',{})
        # Keyed by the draft's own text: a contract refresh can replace the draft
        # within one iteration, and the new draft must be checked and fixed.
        memo_key=hashlib.sha256(memo.encode()).hexdigest()[:16]
        done=any(memo_key in (record.get('memo_sha256'),record.get('fixed_memo_sha256'))
                 for record in fixed.values() if isinstance(record,dict))
        # Length is left to the writing stages: a live fix call (2026-11-14) asked to
        # cut 1,300 words overran its output limit and was deleting the conclusion's
        # required rows. The fix call corrects figures, labels and added rows only.
        fixable=[issue for issue in issues if issue['id']!='length']
        if fixable and not done:
            try:
                response=call(f'draft_check_fix_{iteration}',strong,
                'Correct only the problems listed below in this memo draft. Return ONLY JSON {"edits":[{"quote":"exact '
                'contiguous text copied from the memo","replacement":"corrected text"}]}. Each quote must appear exactly '
                'once in the memo; keep each quote to one sentence or less. For an untraced figure, use the exact figure from '
                'the computed summary, the COMPUTED FROM REPORTED FILINGS tables, the valuation output or a primary source, '
                'or remove the figure. For a figure found only in unverified excerpts, cite the primary source that contains '
                'it, label it as unverified context, or remove it. Describe convention rows separately, never as adding up. '
                'Change nothing else.\n'+
                EVIDENCE_TIER_RULE+'\nProblems:\n'+json.dumps(fixable)+'\nCOMPUTED SUMMARY:\n'+computed_summary(plan,result)+
                EVIDENCE_ABOVE+'\nComputed exhibits:\n'+valuation_section(plan,result)+'\nMemo:\n'+memo,tokens=8000,
                prefix=_evidence_prefix(context))
            except core.IncompleteResponse as exc:
                # The fix is a free-check convenience; the review still sees the draft
                # and the same checks. A charged but unusable answer does not stop the run.
                response=''
                progress(f'Pre-review fix not applied: {exc}'.split(', so it')[0])
            # A live fix response (2026-11-14) gave edits, wrote "Wait, I need to
            # re-read", and gave corrected edits. The last complete block is the
            # model's answer; each edit is still applied only on an exact match.
            edits=_last_edits(response) if response else []
            memo,applied,skipped=_apply_edits(memo,edits)
            memo=_label_unverified(memo,state['sources'])
            if not response:
                skipped=['the fix response was incomplete and was not used']
            elif edits is None:
                skipped=['no complete edits object could be parsed from the response']
            fixed[f'{iteration}:{memo_key}']={'memo_sha256':memo_key,'fixed_memo_sha256':hashlib.sha256(memo.encode()).hexdigest()[:16],
                                             'issues':[issue['id'] for issue in issues],'edits_applied':applied,'edits_skipped':skipped[:20]}
            state['current_memo']=memo
            checkpoint()
            issues=prereview_checks(_with_summary(memo,plan,result),state['sources'],computed_inputs)
        visible=_label_unverified(_with_summary(memo,plan,result)+'\n'+valuation_section(plan,result)+
                                  '\n'.join(reported_tables_section(state['sources'])),state['sources'])
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
        deterministic=inspect_memo(visible,state['sources'],result)+[
            {key:value for key,value in issue.items() if key!='items'}|{'issue':issue['issue']+' Examples: '+
             '; '.join(item['figure'] or item['sentence'][:80] for item in issue['items'][:8])} for issue in issues]
        fresh_notes=(state.get('external_notes','') if state.get('external_notes_digest') !=
                     state.get('external_notes_reviewed_digest') else '')
        earlier=state['review_history'][-1] if iteration and state.get('review_history') else None
        ledger=('\nFindings from the previous review. Verify whether each is resolved in this draft; do not re-raise a '
                'resolved one, and raise a new material finding only for a defect present in this draft:\n'+
                json.dumps([{key:finding.get(key) for key in ('id','severity','issue')} for finding in earlier.get('findings',[])])
                if earlier else '')
        prompt=('Review this equity memo against its supplied evidence and the Python valuation, using the shared quality '
                'standard below, and classify each defect as blocker, major or minor by that standard. Rounding within '
                'the last displayed digit is never material; an unverified excerpt used only as attributed outside view '
                'or context is not material, but a company fact or number resting only on one is (rule M1); style '
                'preferences are not findings. Check: (1) every figure in the prose against the computed summary, the computed tables, the '
                'valuation output or the cited source, and every comparison with the price by arithmetic; (2) that the '
                'memo does not contradict the computed account of which inputs alone can and cannot bring value to the '
                'price (explaining why the house forecast differs from what the price requires is the variant view, not '
                'a contradiction; the conclusion need not restate the account); (3) that company facts rest on primary sources and unverified '
                'excerpts are labelled context; (4) that the strongest sourced opposing case is answered with evidence; '
                '(5) that no trading-layer language appears; (6) that convention rows are never added together; (7) that '
                'repurchase, multiple and monitoring statements use the computed diagnostics and their fixed inputs. A '
                'disclosed house forecast is a judgment, not a defect, unless primary evidence contradicts it or it is '
                'unexplained. Never write a fix that asks the author to compute a value the engine has not computed. '
                'Before flagging an omission, check the whole memo. Report every material defect you find and at most '
                'three minor ones; there is no target number of findings. '
                'Every finding must quote one contiguous passage of the CURRENT draft (between the draft markers) verbatim, without added quotation '
                'marks or ellipses; for an omission, quote the heading or sentence where the missing content belongs and '
                'name the omission in issue. Earlier notes can be stale; never report an accusation that the current '
                'draft already resolves. Keep each issue under 100 words and each fix under 70.\n'+
                EVIDENCE_TIER_RULE+'\n'+RUBRIC+ledger+'\n'+REVIEW_SCHEMA+
                '\nThe evidence, the valuation and the draft are in the blocks above.'+
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
            review_prefix=_review_prefix(context,valuation_context,visible)
            raw=call(f'review_{iteration}',reviewer,prompt,tokens=12000,prefix=review_prefix)
            try:
                review_value,normalized=_response_json(raw)
                # Live GPT-5.4 reviews (2026-11-14) wrapped quotes in quotation
                # marks or joined fragments, and each paid a format repair whose
                # result was then anchored to its longest exact span anyway. The
                # same anchoring applies to the first response; the reviewer's
                # wording stays on record beside the anchored excerpt.
                review=parse_review(json.dumps(review_value),memo=visible,anchor_embellished=True)
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
            # A reviewer ID equal to one Python assigns (the assessment finding, a
            # deterministic check) is renamed so records and the check stay distinct.
            reserved={'central-weakness-unhandled','review-assessment-missing'}|{f.get('id') for f in deterministic}
            for finding in review['findings']:
                if finding['id'] in reserved:
                    finding['id']=finding['id']+'-reviewer'
            # The reviewer's assessment finding is checked with its majors. It is
            # kept only when no checked reviewer major survives, as before the
            # check existed; its minor form (assessment missing) is always kept.
            assessment=assessment_findings({**review,'findings':[]})
            material=[f for f in review['findings']+assessment if f.get('severity') in {'blocker','major'}]
            refuted,check_note={},None
            # Python settles "this number is in no source" itself: a replay review
            # (2026-11-15) called a figure from the reported-history table absent,
            # and the model check upheld it.
            pools=trace_pools(state['sources'],computed_inputs)
            # A finding the check refuted earlier on the same text under the same
            # rule is not paid for again (the check flip-flopped between reviews).
            earlier_refuted=_carried_refutations(state,iteration,manifest)
            for index,finding in enumerate(material):
                rule,excerpt=finding.get('rule'),finding.get('current_excerpt') or ''
                if rule=='M3':
                    missing=untraced_figures(excerpt,pools)
                    if missing==[]:
                        refuted[index]=('Python traced every figure in the quoted text to the computed output, the reported '
                                        'tables or a primary source.')
                elif rule=='B2' and excerpt and not TRADING_TERMS.search(excerpt):
                    refuted[index]='The quoted text contains none of the trading-layer terms the standard names.'
                elif rule=='M6' and HOUSE_ATTRIBUTION.search(excerpt):
                    refuted[index]='The quoted text names the figure as a house, computed or forecast figure.'
                if index not in refuted and (rule,excerpt) in earlier_refuted:
                    refuted[index]=earlier_refuted[(rule,excerpt)]
            pending=[f for index,f in enumerate(material) if index not in refuted]
            if pending:
                try:
                    check=call(f'finding_check_{iteration}',reviewer,FINDING_CHECK+'\n'+EVIDENCE_TIER_RULE+'\n'+RUBRIC+
                        '\nFindings:\n'+json.dumps([dict({key:f.get(key) for key in ('severity','rule','issue','current_excerpt','fix','source_refs')},id=check_id)
                                                    for f,check_id in zip(pending,_check_ids(pending))])+
                        '\nThe evidence, the valuation and the draft are in the blocks above.',tokens=FINDING_CHECK_TOKENS,
                        prefix=review_prefix)
                    for index,reason in _refuted_findings(check,pending).items():
                        refuted[material.index(pending[index])]=reason
                except core.IncompleteResponse as exc:
                    # Charged and recorded; every remaining finding stands, as if the check upheld it.
                    check_note='The check response was incomplete; every finding it was asked about was kept.'
                    progress(f'Finding check not used: {exc}'.split(', so it')[0])
            removed={id(material[index]) for index in refuted}
            kept=[f for f in review['findings'] if id(f) not in removed]
            if any(f.get('severity') in {'blocker','major'} for f in kept):
                assessment=[f for f in assessment if f.get('severity') not in {'blocker','major'}]
            else:
                assessment=[f for f in assessment if id(f) not in removed]
            combined=kept+deterministic+assessment
            effective='revise' if any(f.get('severity') in {'blocker','major'} for f in combined) else 'pass'
            record={'iteration':iteration,'verdict':effective,'model_verdict':review.get('verdict_as_reviewed',review['verdict']),
                    'findings':combined,'manifest':manifest,
                    'diagnostic_scope':DIAGNOSTIC_SCOPE,'review_model':reviewer,'author_model':strong}
            if material:
                record['finding_check']={'model':reviewer,'checked':[f['id'] for f in material],
                                         'refuted':[dict(material[index],check_reason=reason) for index,reason in sorted(refuted.items())]}
                if check_note:
                    record['finding_check']['note']=check_note
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
        if _revisions_used(state)==max_revisions:
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
            +FORECAST_SCHEMA+rate_text+UNDERWRITING_GUIDANCE+PLAN_FIELD_RULES+PLAN_SOURCE_RULE+
            EVIDENCE_ABOVE+'\nCurrent memo (verify allegations against this exact text):\n'+memo+'\nCurrent forecast proposal:\n'+json.dumps(_proposal_view({k:plan[k] for k in
                ('assumptions_label','scenario_rationales','scenarios','operating_model','discount_rate','monitoring',
                 'counterfactuals','calibration_comparisons','forecast_periods','repurchase_tests',
                 'margin_continuity_basis','capital_efficiency_basis','valuation_timing') if k in plan}))+
            '\nLocked actuals (reference only; omit from your proposal):\n'+json.dumps(plan.get('actuals',{}))+
            '\nReported operating lines (reference for year-1 continuity):\n'+
            json.dumps((plan.get('reported_inputs') or {}).get('operating_reference'))+
            ('\nReported reinvestment (reference for years 1-3 reinvestment):\n'+json.dumps(plan['reinvestment_reference'])
             if plan.get('reinvestment_reference') else '')+
            ('\nReported operating margins (reference for the final-year margin ceilings):\n'+json.dumps(plan['cost_path_reference'])
             if plan.get('cost_path_reference') else '')+
            base_rates.result_context(result)+
            '\nComputed diagnostics for verification:\n'+json.dumps((result or {}).get('computed_diagnostics',{}))+
            '\nComputed counterfactuals:\n'+json.dumps((result or {}).get('counterfactuals',{}))+
            '\nComputed reverse-DCF projections:\n'+json.dumps((result or {}).get('reverse_dcf',{}).get('solution_projections',[]))+
            '\nFindings:\n'+json.dumps(combined),tokens=12000,prefix=_evidence_prefix(context))
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
                                   'margin_continuity_basis','capital_efficiency_basis','valuation_timing','counterfactuals','calibration_comparisons',
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

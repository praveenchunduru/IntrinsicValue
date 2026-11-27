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

from . import base_rates, consensus, core, memo_style
from .costs import cost_summary
from .analytics import annual_metrics
from .fundamentals import yahoo_financials
from .inputs import canonical_reported_inputs, operating_company_gate, pin_reported_inputs
from .quality import (WRITING_STANDARD, dollar_amounts, style_added, structure_problems, DIAGNOSTIC_SCOPE, HOUSE_ATTRIBUTION, RUBRIC, TRADING_TERMS, assessment_findings, claim_ledger,
                      inspect_memo, parse_json, parse_review, prereview_checks, prose_words, trace_pools, untraced_figures,
                      _figures, UNVERIFIED_FACT_FIX, PROSE_WORD_BUDGET, word_limit)
from .research import MAX_DOCUMENT_BYTES, gather_primary, inline_breakdown_facts, inline_facts, archive_focus_excerpts
from .evidence_tables import (OUTSIDE_SCANNED, breakdown_markdown, breakdown_tables, depreciation_phrase,
                              repurchase_price_basis, scale_of, filing_supplement_for, historical_baseline,
                              quarterly_markdown, render_baseline, render_roic, render_breakdowns, render_quarterly)
from .macro import gather_us_calibration
from .roic import _consecutive as roic_consecutive
from . import country_risk
from .market_beta import gather_weekly_beta
from .prompt_context import PROMPT_SIGNIFICANT_DIGITS, rounded as prompt_rounded, valuation_context as render_valuation_context
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
# Revision rounds after the first review. A round is exact edits to the prose, with
# one retry for edits that did not apply; it rewrites the memo only when the forecast
# inputs changed. The loop stops as soon as no blocker or major survives, or when only
# computed text remains. Live reviews raise about ten findings a pass (KO, 2026-11-27).
DEFAULT_MAX_REVISIONS = 3
SECTIONS = '''Write the sections below in this order, with each heading exactly as given. Python writes the title and
the first page (the values, the scenario table, "What the price needs" and "Conventions that move the value most"),
shows your In brief on it, and adds the valuation detail, reported history and method notes after your text.
Each fact has one home, given below: state it there and refer to it elsewhere by name ("the growth the price
needs"). The first page is the home of the values and of what the price needs; repeat one of its figures only
where an argument turns on it. A paragraph that lists figures a table already shows is cut.
"## In brief": three sentences, at most 80 words in all. The first says what the company does and what it earns.
The second says why our value differs from the price: what our forecast assumes for the input on which it and the
price differ most, against what the price needs. The third names the observation that would change the value most, with
its review date when the monitoring cases give one. No computed return.
"## 1. Conclusion and the gap to price" (about 450 words). Open with the thesis: the economic reason our value
differs from the price, argued from primary evidence. The table "What the price needs" is the full account of which
inputs alone reach the price within the ranges tested, the flat discount rate among them, so count inputs from it
and from nowhere else; an input not reached within the range tested is not shown to be unable to reach the price.
Make each input that reaches the price concrete with one comparison from the record (a base-rate percentile, the
best reported margin, a segment that already earns it). Then give the strongest sourced opposing case and say, with
operating evidence (demand, pricing, margins, capital needs), whether it closes the gap. The S&P 500 row is context
for reading value / price under these conventions; report it, if at all, as the index's own ratio, since it
explains no part of the company's difference.
"## 2. Business economics" (about 400 words): how the company earns its returns on capital, by segment, cost
structure and competitive position.
"## 3. Forecast and scenarios" (about 450 words): how the base, bear and bull cases start from reported results and
what drives each. Scenario drivers are our assumptions: say so once, and cite a source only for the observed fact
behind each. Explain here, once, the discount rate and what the method assumes after year 10 (new capital then earns
only its cost; the computed exit multiple shows what that implies). A computed return at the price, if used at all,
appears here once, as a discount rate.
"## 4. Capital allocation and claims on value" (about 350 words): buybacks and dividends against our value, from
the computed figures; financing; leases and off-balance-sheet commitments; the balance-sheet date and what has
changed since. Operating-lease cost is inside EBIT, finance-lease additions count as reinvestment, and leases signed
but not yet commenced are disclosed and not deducted. A buyback price above today's value says nothing about value
on the purchase date, which the model does not estimate.
"## 5. What would change the value" (about 350 words): the dated observations from the computed monitoring cases,
ranked by effect, each with its threshold, review date and value; then the evidence that the method itself could be
wrong (the return on new capital after year 10, the horizon, the rate), each with its computed value.
"## 6. Evidence gaps": at most six bullets, ranked by effect on value, each a gap a reader would act on, named once
without restating a figure that only an unverified excerpt supports. Retrieval mechanics are no evidence gap.'''
# Memo stages rewrite the full memo. A live revision overran a 14,000-token
# limit as the memo grew, so the limit leaves room above the word budget.
MEMO_TOKENS = 18000
MEMO_WORDS = PROSE_WORD_BUDGET+' words of prose in total; Python adds the tables, and a revision replaces text and adds none'
FORECAST_SCHEMA = """Return ONLY compact JSON for house forecasts:
{"assumptions_label":"House forecasts",
 "scenario_rationales":{"bear":"Evidence and economic mechanism for this scenario [S#]","base":"...","bull":"..."},
 "scenarios":{"bear":{"probability":<decimal>,"tax_rate":<decimal>,"sales_to_capital":<number>},
 "base":{<same fields>},"bull":{<same fields>}}} (Python sets terminal_roic)
sales_to_capital is one ratio for every forecast year, or {"year1":x,"target":y,"target_year":n}
(a straight line to the target, then held) when capital intensity changes, as in a capex cycle, or
a list of one ratio per forecast year.
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
the evidence does not contain; the supplied sales-growth base-rate tables may be cited by name. All
rates are decimals.
The RATE CONVENTION FOR THIS RUN, stated after these instructions, says whether to give
scenario wacc and terminal_growth.
Write each rationale in one or two short sentences: the evidence [S#] and the direction of each
departure. Do not restate forecast input figures (growth rates, margins, expense shares, tax rate,
sales-to-capital); Python prints them beside the text. Do not reproduce historical actuals, cash/debt
reconciliations, source passages, calculated cashflows or valuation targets: Python
locks reported inputs and independently computes the outputs.
The unlevered operating-company model uses GAAP EBIT less operating taxes and
incremental revenue / sales-to-capital reinvestment. Maintenance investment offsets
D&A; GAAP EBIT already expenses SBC, R&D and operating rent. Do not deduct those
again. Explicitly justify growth/margin fade, capital efficiency, tax and WACC.
Terminal return on new capital is a house convention that Python sets: the terminal discount
rate (Mauboussin's steady state). The 10 forecast years are a house horizon. Python values horizons
of 5, 15 and 20 years and the industry fade beside it. Excess returns on new investment come only
from the forecast years' growth and capital efficiency, and after year 10 growth adds no value.
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
scenario's step with evidence [S#] from the sources, in one or two short sentences that state the
evidence and the direction without restating the plan's input figures. Reinvestment starts from the reported run-rate
too: a scenario whose net reinvestment over years 1-3 (revenue change / sales_to_capital) is below
two thirds of three years at the last year's net reinvestment computed from reported lines, or whose sales-to-capital
over those years is above 1.5 times the incremental rate computed from reported lines, is rejected unless capital_efficiency_basis
(keyed by bear, base, bull) explains it with evidence [S#], in one or two short sentences of evidence
and direction without the plan's input figures; the reported rate counts cash acquisitions as
investment. Final-year EBIT margins have hard ceilings with no exception: bear at most the reported
five-year average operating margin (the five-year peak where that average is not positive), base and
bull at most the higher of the reported five-year peak and the latest twelve-month operating margin,
plus 5 points (less for low-margin businesses, as in the year-1 check).
Use aggregate_ebit_margins mode without gross margins/expenses if only category revenues are supported.
Where the evidence supports more than one structure, use the most detailed it supports (an operating_model over the reported segments, in gross_profit_less_expenses mode when segment gross profit or cost of revenue is reported), so repeated plans share one structure.
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
operating-asset proxy. Adopting it for future risk is a house judgment.
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
These are analyst-selected decision thresholds. They are neither company guidance nor automatic forecasts.
The observation, and every counterfactual name or label, states the evidence and the direction in one
short sentence and does not restate forecast input figures; the threshold carries the one figure.
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


def _review_prefix(context, valuation_context):
    """Evidence then valuation: the reviewer's cached block, identical for every review of one valuation.

    The draft is not part of it. Live runs (2026-11-27) cached evidence, valuation
    and draft together, so the second review paid the whole cache write again
    for a change in the draft alone.
    """
    return _evidence_prefix(context)+'Valuation:\n'+valuation_context+'\n'


def _draft_block(visible):
    """The fenced draft, sent after the cached blocks and the instructions."""
    return '\nDraft (quote only text between the markers):\n<<<DRAFT\n'+visible+'\nDRAFT>>>\n'


def _model_valuation_context(plan, result):
    """The valuation as models read it: floats to six significant figures; saved files keep full precision."""
    if result is None:
        return json.dumps({'assumptions':prompt_rounded(plan),'computed':None},ensure_ascii=False)
    return render_valuation_context(plan,result,significant=PROMPT_SIGNIFICANT_DIGITS)


def _prompt_json(value):
    """Reference JSON for a prompt, with floats to six significant figures."""
    return json.dumps(prompt_rounded(value))


def _rounded_inputs(plan, result):
    """The model-facing (rounded) plan and result, traced beside the originals.

    A figure copied from the rounded context then traces even where rounding
    twice (to six figures, then to the displayed digits) moves the last digit.
    """
    return (prompt_rounded(result), prompt_rounded(plan)) if result is not None else ()


LATER_EVIDENCE = '\nSources collected after the evidence block above (the same evidence tiers apply):\n'


def _freeze_evidence(state, *, refresh=False):
    """Fix which sources the cached evidence block holds for the rest of the run.

    The block is first written by the plan stage. Sources added later (targeted
    research after a review) go in an uncached block after it, so every later
    stage reads the same cached bytes. A new plan refreezes the block.
    """
    ids=[source.get('id') for source in state.get('sources',[]) if isinstance(source,dict)]
    record=state.get('cached_evidence')
    held=record.get('source_ids') if isinstance(record,dict) else None
    if refresh or not isinstance(held,list) or not set(held)<=set(ids):
        state['cached_evidence']={'source_ids':ids,
                                  'prefix_sha256':_digest(_evidence_prefix(_evidence_context(state.get('sources',[]))))}


def _evidence_blocks(state):
    """(cached evidence context, text of sources added after it, '' when none)."""
    record=state.get('cached_evidence')
    held=set(record.get('source_ids') or []) if isinstance(record,dict) else None
    sources=[source for source in state.get('sources',[]) if isinstance(source,dict)]
    if held is None:
        return _evidence_context(sources),''
    cached=[source for source in sources if source.get('id') in held]
    later=[source for source in sources if source.get('id') not in held]
    return _evidence_context(cached),(_later_evidence(later,_evidence_rows(cached)[3]) if later else '')


# A forecast input the plan author sets. A finding names one when its issue or fix
# names a scenario's input ("base growth", "the bull case margins") or a plan input,
# or when its fix asks for an input to change ("raise base growth"). A live-style M5
# finding asking to raise base growth got a prose patch (review, 2026-11-27).
# Terminal growth and the terminal return follow the house conventions, not the author.
_FORECAST_INPUT = (r"(?:growth(?: rates?| paths?)?|(?:gross |operating |ebit )?margins?|tax rates?|"
                   r"sales[- ]to[- ]capital(?: ratios?)?|reinvestment(?: rates?)?|expense shares?|r&d shares?|"
                   r"(?:unlevered )?beta|probabilit(?:y|ies)|scenario weights?)")
FORECAST_INPUT_NAMED = re.compile(
    r"\b(?:base|bull|bear)(?:[- ]case)?(?: scenario)?(?:'s)? (?:revenue |sales |operating |ebit |gross |year-1 )?" +
    _FORECAST_INPUT + r"\b|\bforecast (?:inputs?|drivers?|arrays?)\b|\bplan (?:inputs?|assumptions?)\b", re.I)
FORECAST_CHANGE_ASKED = re.compile(
    r"\b(?:raise|lower|increase|reduce|cut|change|revise|adjust|rebuild|re-?estimate)\w*\b[^.;]{0,40}?\b" +
    _FORECAST_INPUT + r"\b", re.I)


def _names_forecast_input(finding):
    """Whether a finding's issue or fix names a forecast input, or its fix asks for one to change."""
    issue,fix=str(finding.get('issue') or ''),str(finding.get('fix') or '')
    return bool(FORECAST_INPUT_NAMED.search(issue+' '+fix) or FORECAST_CHANGE_ASKED.search(fix))


def _in_prose(finding, memo):
    excerpt=finding.get('current_excerpt') or ''
    return bool(excerpt.strip()) and excerpt in memo


def _finding_key(finding):
    """(rule, quoted text): a finding's identity across reviews, whose IDs are renumbered.

    The quote is compared without a leading list marker or surrounding space: the KO
    reviews (2026-11-27) quoted one method note with and without its "- ".
    """
    excerpt=re.sub(r'^\s*(?:[-*]\s+)?','',str(finding.get('current_excerpt') or '')).strip()
    return (finding.get('rule'),excerpt)


def _forecast_findings(findings, memo, python_ids=(), answered=()):
    """Surviving blockers and majors that need the forecast response, as (id, reason).

    The forecast response runs only when this is not empty. A reviewer finding
    counts when it names a forecast input (see ``_names_forecast_input``),
    whatever rule it cites and whatever it quotes, or when it quotes the writer's
    prose and names no quality-standard rule, so it cannot be classed as a
    wording defect. A finding that quotes computed text (the first page, the
    valuation detail, the tables) and names no forecast input is left to the
    review status: no stage can change that text, and reopening the forecast for
    it every round never resolved it (review, 2026-11-27). A finding the forecast
    response already answered "unchanged" on this valuation (``answered``, keyed
    by ``_finding_key``) does not run it again. A Python check counts when it
    found no valuation model. The edit call can still report that a finding
    needs a forecast change; that also runs the forecast response.
    """
    reasons=[]
    for finding in findings:
        if not isinstance(finding,dict) or finding.get('severity') not in {'blocker','major'}:
            continue
        identifier=finding.get('id')
        if identifier in python_ids:
            if identifier in PLAN_CHECK_IDS:
                reasons.append((identifier,'a Python check found no valuation model'))
            continue
        if _finding_key(finding) in answered:
            continue
        if _names_forecast_input(finding):
            reasons.append((identifier,'names a forecast input'))
        elif _in_prose(finding,memo) and not finding.get('rule'):
            reasons.append((identifier,'names no quality-standard rule'))
    return reasons


def _computed_text_findings(findings, memo, python_ids=(), answered=()):
    """IDs of reviewer blockers and majors that quote computed text no stage can change.

    These quote text outside the writer's prose and either name no forecast
    input or were already answered "unchanged" by the forecast response.
    """
    return [finding.get('id') for finding in findings
            if isinstance(finding,dict) and finding.get('severity') in {'blocker','major'} and
            finding.get('id') not in python_ids and not _in_prose(finding,memo) and
            (not _names_forecast_input(finding) or _finding_key(finding) in answered)]


def _inputs_fingerprint(plan):
    """The forecast-input digest of a plan (consensus.fingerprint), or None without one."""
    return consensus.fingerprint(plan) if isinstance(plan,dict) and not plan.get('unavailable') else None


def _answered_forecast(state, inputs_fingerprint):
    """(rule, quote) of findings answered "unchanged" (or with text only) on these forecast inputs in this cycle.

    Carried into later rounds like ``_carried_refutations``: a forecast that was
    kept for a finding is not reopened when the next review raises it again.
    """
    answered=set()
    for entry in state.get('revision_rounds',[]):
        if (entry.get('cycle')==state.get('revision_base',0) and entry.get('forecast_response') in ('unchanged','text_only') and
                entry.get('inputs_fingerprint')==inputs_fingerprint):
            answered|={tuple(item) for item in entry.get('answered',[]) if isinstance(item,list) and len(item)==2}
    return answered


COMPUTED_TEXT_HEADING = 'Findings on computed text (no revision stage can change it):'


# Plan fields Python writes or rewrites at lock. They are left out of the proposal the
# forecast response edits, and a response that returns them is overridden with the
# current plan's values. A live Microsoft run (2026-11-27) "corrected" the plan by
# rewriting assumptions_label, paid for a full rewrite and a reviewer cache write, and
# changed nothing the lock would keep.
#   assumptions_label: rebuilt by _lock_plan from the bridges and conventions, always.
#   valuation_timing: set by the market conventions.
#   discount_rate: the paired Treasury rate and premium, the glide (term_structure,
#     terminal_unlevered_beta), and the debt, finance-lease and market-equity records
#     copied from the reported bridges.
#   each scenario: the house probability, the terminal return and its basis, and the
#     per-year sales-to-capital and R&D-share paths and R&D basis.
PYTHON_OWNED_PLAN_FIELDS = {'plan': ('assumptions_label',), 'plan_conventions': ('valuation_timing',),
                            'discount_rate': ('risk_free_rate','equity_risk_premium','term_structure','terminal_unlevered_beta',
                                              'financial_debt','finance_lease_debt','market_equity'),
                            'scenario': ('probability','terminal_roic','terminal_roic_basis','sales_to_capital_path',
                                         'rnd_share_path','rnd_basis')}
# Model-owned plan text and diagnostics: they change computed sections but no forecast input.
MODEL_TEXT_FIELDS = ('scenario_rationales','monitoring','margin_continuity_basis','capital_efficiency_basis',
                     'calibration_comparisons','counterfactuals','repurchase_tests')
PROPOSAL_FIELDS = ('assumptions_label','scenario_rationales','scenarios','operating_model','discount_rate','monitoring',
                   'counterfactuals','calibration_comparisons','forecast_periods','repurchase_tests',
                   'margin_continuity_basis','capital_efficiency_basis','valuation_timing')


def _forecast_view(plan, conventions=True):
    """The proposal the forecast response edits: the model-owned fields, without Python's own text and pins."""
    view=_proposal_view({key:plan[key] for key in PROPOSAL_FIELDS if key in plan})
    view.pop('assumptions_label',None)
    if conventions:
        for key in PYTHON_OWNED_PLAN_FIELDS['plan_conventions']:
            view.pop(key,None)
        if isinstance(view.get('discount_rate'),dict):
            for key in PYTHON_OWNED_PLAN_FIELDS['discount_rate']:
                view['discount_rate'].pop(key,None)
        for scenario in (view.get('scenarios') or {}).values():
            if isinstance(scenario,dict):
                for key in PYTHON_OWNED_PLAN_FIELDS['scenario']:
                    scenario.pop(key,None)
    return view


def _restore_python_fields(proposal, plan, view, conventions=True):
    """A response with Python's fields put back from the current plan, and unchanged expense shares as the plan's expenses.

    Expense shares are shown rounded to four places; rebuilding expenses from shares a
    response copied back moved R&D by up to 0.05% a year and so the value (live run,
    2026-11-27). Shares equal to the ones shown restore the plan's own expenses.
    """
    proposal=copy.deepcopy(proposal)
    def put(target, source, keys):
        for key in keys:
            if isinstance(source,dict) and key in source:
                target[key]=copy.deepcopy(source[key])
            else:
                target.pop(key,None)
    put(proposal,plan,PYTHON_OWNED_PLAN_FIELDS['plan'])
    if conventions:
        put(proposal,plan,PYTHON_OWNED_PLAN_FIELDS['plan_conventions'])
        if isinstance(proposal.get('discount_rate'),dict):
            put(proposal['discount_rate'],plan.get('discount_rate'),PYTHON_OWNED_PLAN_FIELDS['discount_rate'])
        for name,scenario in (proposal.get('scenarios') or {}).items():
            if isinstance(scenario,dict):
                put(scenario,((plan.get('scenarios') or {}).get(name)),PYTHON_OWNED_PLAN_FIELDS['scenario'])
    shown=((view.get('operating_model') or {}).get('scenarios') or {}) if isinstance(view,dict) else {}
    kept=((plan.get('operating_model') or {}).get('scenarios') or {}) if isinstance(plan,dict) else {}
    for name,scenario in (((proposal.get('operating_model') or {}).get('scenarios')) or {}).items():
        if (isinstance(scenario,dict) and 'expense_shares' in scenario and isinstance(shown.get(name),dict) and
                scenario['expense_shares']==shown[name].get('expense_shares') and
                isinstance(kept.get(name),dict) and 'expenses' in kept[name]):
            scenario.pop('expense_shares')
            scenario['expenses']=copy.deepcopy(kept[name]['expenses'])
    return proposal


def _edit_required(findings, memo, python_ids=()):
    """IDs of the blockers and majors that need an applied edit; without one after the retry they carry to the next review.

    A reviewer finding that quotes text outside the writer's prose is exempt:
    no edit to the prose can change that text, and a rewrite cannot either.
    """
    required=[]
    for finding in findings:
        if not isinstance(finding,dict) or finding.get('severity') not in {'blocker','major'}:
            continue
        excerpt=finding.get('current_excerpt') or ''
        if finding.get('id') not in python_ids and (not excerpt.strip() or excerpt not in memo):
            continue
        required.append(finding.get('id'))
    return required


FORECAST_SKIP_REASON = ('No surviving blocker or major names a forecast input or lacks a quality-standard rule, '
                        'so the forecast was not reopened; the valuation is unchanged.')
# Python's checks that only a plan can answer; its other checks concern the prose.
PLAN_CHECK_IDS = frozenset({'valuation-model-missing'})
# The reviewer and Python assign these IDs; see the review loop in _run.
PYTHON_FINDING_IDS = frozenset({'central-weakness-unhandled','review-assessment-missing'})
EDIT_TOKENS = 10000
EDIT_INSTRUCTIONS = (
    'Answer the review findings below with exact edits to the memo prose. Return ONLY JSON {"edits":[{"finding":"F1",'
    '"quote":"exact contiguous text copied from the memo","replacement":"corrected text"}],"forecast":[{"finding":"F2",'
    '"reason":"why only a forecast change can answer it"}]}. Each quote must appear exactly once in the memo and be '
    'at most one paragraph. An empty replacement deletes the quoted text. Give every blocker and major at least one '
    'edit, unless only a change to the forecast can answer it; list that one under forecast instead. Several edits '
    'may answer one finding. Fix the defect each finding names and change nothing else: do not touch sentences no '
    'finding names, do not reword for style, and keep every heading. Where a finding is wrong, keep the text or make '
    'the correct reading plain; do not copy an invalid accusation into the memo. Copy figures exactly from the '
    'computed summary, the exhibits or a source, calculate no new figures, and place each [S#] after the sourced '
    'figure it supports. If a figure you need is not there, make the point without it. Every replacement follows the '
    'writing standard in the quality standard below; Python rejects a replacement that adds "USD" for a dollar filer, a '
    'number of seven or more digits, an em dash, a ", not" contrast or a sentence narrating an exhibit. For an empty or '
    'unfinished section (section-empty, rule B4), quote its last sentence, or its heading when it has none, and replace '
    'it with that text followed by the rest of the section. For a rule M1 or unverified-company-fact finding: '
    +UNVERIFIED_FACT_FIX)
# The writer prompt's opening; tests and offline harnesses route the draft call on it.
WRITER_OPENING = 'Write the prose sections of the intrinsic-value memo for '


def _edit_response(text):
    """(edits, forecast flags) from the edit call; (None, []) when no edits object parses."""
    edits=_last_edits(text) if isinstance(text,str) else None
    flags=[]
    decoder=json.JSONDecoder()
    for match in re.finditer(r'\{\s*"edits"',text if isinstance(text,str) else ''):
        try:
            value,_=decoder.raw_decode(text,match.start())
        except ValueError:
            continue
        if isinstance(value,dict) and isinstance(value.get('edits'),list):
            flags=[item for item in value.get('forecast') or [] if isinstance(item,dict) and isinstance(item.get('finding'),str)]
    return edits,flags


# Rules about figures: only a finding under one of these may change a figure in the
# prose. Python's figure checks count too. An edit for a wording finding that moved a
# valued figure still applied (review, 2026-11-27).
# M4 and M5 are corrected with the computed or reported figure itself: the live
# GOOGL and KO edits for M5 findings (2026-11-27) were rejected here, all with
# figures that traced to the filings, and the round fell back to a rewrite.
# M7 (prose against a table or note) is corrected with the table's figure, and an empty section
# (B4, section-empty) is answered by writing it, figures included; the trace still applies.
FIGURE_RULES = frozenset({'B1','B3','B4','M1','M3','M4','M5','M7'})
FIGURE_CHECK_IDS = frozenset({'number-untraced','number-unverified-only','cite-primary-source','comparison-direction',
                              'valuation-value-mismatch','valuation-model-missing','section-empty'})
_SCENARIO_WORD = re.compile(r'\b(bear|base|bull|weighted)\b', re.I)


def _valued_figures(result):
    """Per-share values by name: bear, base, bull and weighted, where computed."""
    values={}
    if isinstance(result,dict):
        for name in ('bear','base','bull'):
            value=((result.get('scenarios') or {}).get(name) or {}).get('per_share_value')
            if isinstance(value,(int,float)) and not isinstance(value,bool):
                values[name]=float(value)
        weighted=result.get('weighted_per_share_value')
        if isinstance(weighted,(int,float)) and not isinstance(weighted,bool):
            values['weighted']=float(weighted)
    return values


def _figure_list(text):
    return [(display,number,decimals) for display,number,decimals,_ in _figures(text or '')]


_FIGURE_SCALE={'b':1e9,'bn':1e9,'billion':1e9,'m':1e6,'mm':1e6,'million':1e6,'t':1e12,'trillion':1e12}


def _scaled_figures(text):
    """(display, value in units, decimals, scale, unit class) for each figure: $11,599M and $11.6B are one amount."""
    out=[]
    for display,number,decimals,unit in _figures(text or ''):
        unit=(unit or '').strip().lower()
        scale=_FIGURE_SCALE.get(unit,1)
        out.append((display,number*scale,decimals,scale,'amount' if unit in _FIGURE_SCALE or not unit else unit))
    return out


def _same_figure(old, new):
    """Whether ``new`` writes the figure ``old``: the same number, or the same amount at the same or a coarser precision."""
    _,value,_,old_scale,kind=old
    _,shown,decimals,scale,other=new
    if value/old_scale==shown/scale:
        return True
    return kind==other and abs(value-shown)<=0.5*10**-decimals*scale+1e-9*max(1,abs(value))


def _shows(number, decimals, value):
    return abs(number-abs(value))<=0.5*10**-decimals+1e-9


def _scope_breach(quote, replacement, finding, values):
    """Why an edit reaches past its finding, or None.

    It may not delete a heading line or drop a citation the finding does not
    name. It may change a figure only for a rule about figures, and a new
    per-share or scenario figure must equal the computed value its sentence names.
    """
    kept={line.strip() for line in replacement.splitlines()}
    for line in quote.splitlines():
        if line.lstrip().startswith('#') and line.strip() not in kept:
            return 'replacement removes the heading '+line.strip()[:80]
    cited=lambda text:set(re.findall(r'\bS\d+\b',' '.join(re.findall(r'\[([^\]\n]*\bS\d+[^\]\n]*)\]',text))))
    dropped=cited(quote)-cited(replacement)-set((finding or {}).get('source_refs') or [])
    if dropped:
        return 'replacement drops citations the finding does not name: '+', '.join(sorted(dropped))
    # Matched by value at the replacement's precision, so a restyled amount ($36.3B for USD 36.3
    # billion, $11.6B for $11,599M) is the same figure (number style, 2026-11-27).
    before=_scaled_figures(quote)
    added=[]
    for item in _scaled_figures(replacement):
        match=next((old for old in before if _same_figure(old,item)),None)
        if match is not None:
            before.remove(match)
        else:
            added.append((item[0],item[1]/item[3],item[2]))
    if not added:
        return None
    rule=(finding or {}).get('rule')
    # A finding passed by ID alone has no known rule, so it may not change a figure.
    if rule not in FIGURE_RULES and (finding or {}).get('id') not in FIGURE_CHECK_IDS:
        return 'replacement changes a figure though the finding is not about figures: '+', '.join(d for d,_,_ in added[:5])
    valued=any(_shows(number,decimals,value) for _,number,decimals in _figure_list(quote) for value in values.values())
    for display,number,decimals in added:
        if not (valued or any(_shows(number,decimals,value) for value in values.values())):
            continue
        sentence=next((part for part in re.split(r'(?<=[.!?])\s+',replacement) if display in part),replacement)
        names=[match.group(1).lower() for match in _SCENARIO_WORD.finditer(sentence.split(display)[0])]
        if not names or names[-1] not in values or not _shows(number,decimals,values[names[-1]]):
            return (f'replacement writes {display} for a valued figure; the computed '
                    +(f'{names[-1]} value is {values[names[-1]]:.2f}' if names and names[-1] in values else 'value its sentence names is not that'))
    return None


def _unapplied_reason(identifier, records, text, edits):
    """Why no edit for a finding applied: the guard's or anchor's reason, or that none was returned."""
    reasons=[record.get('reason') for record in records if record.get('finding')==identifier and not record.get('applied')]
    if reasons:
        return '; '.join(dict.fromkeys(reason for reason in reasons if reason))
    if not text:
        return 'the edit response was incomplete'
    if edits is None:
        return 'no edits object could be read from the response'
    return 'no edit was returned for this finding'


def _apply_finding_edits(memo, edits, finding_ids, pools, dollars=True, values=None):
    """Apply edits that each answer a named finding; return (memo, one record per edit).

    An edit is rejected when it names no finding, when its quote is not exactly
    once in the prose, or when its replacement adds a figure that traces to no
    source or computed value, or a trading-layer term, that the quote lacks, or
    reaches past its finding (see ``_scope_breach``). ``finding_ids`` is a set
    of IDs or a mapping of ID to finding; with the mapping the rule and source
    references of each finding bound its edits. ``values`` holds the computed
    per-share values by name (``_valued_figures``).
    """
    findings=finding_ids if isinstance(finding_ids,dict) else None
    values=values or {}
    records=[]
    for edit in edits if isinstance(edits,list) else []:
        record={'finding':edit.get('finding') if isinstance(edit,dict) else None,'applied':False}
        quote=edit.get('quote') if isinstance(edit,dict) else None
        replacement=edit.get('replacement') if isinstance(edit,dict) else None
        if dollars and isinstance(replacement,str) and dollar_amounts(replacement)!=replacement:
            # A dollar amount written with the code is restyled ($36.3B); its figure is unchanged.
            replacement=dollar_amounts(replacement)
            record['restyled']=True
        record.update(quote=quote if isinstance(quote,str) else None,
                      replacement=replacement if isinstance(replacement,str) else None)
        if not isinstance(quote,str) or not quote.strip() or not isinstance(replacement,str):
            record['reason']='malformed edit'
        elif record['finding'] not in finding_ids:
            record['reason']='names no finding under review'
        elif memo.count(quote)!=1:
            record['reason']=f'quote found {memo.count(quote)} times in the prose'
        else:
            added=list(untraced_figures(replacement,pools) or [])
            for figure in untraced_figures(quote,pools) or []:
                if figure in added:
                    added.remove(figure)
            if added:
                record['reason']='replacement adds figures that trace to no source or computed value: '+', '.join(added[:5])
            elif TRADING_TERMS.search(replacement) and not TRADING_TERMS.search(quote):
                record['reason']='replacement adds a trading-layer term'
            elif style_added(quote,replacement,dollars,memo):
                record['reason']='replacement breaks the writing standard: '+', '.join(style_added(quote,replacement,dollars,memo))
            elif _scope_breach(quote,replacement,(findings or {}).get(record['finding']),values):
                record['reason']=_scope_breach(quote,replacement,(findings or {}).get(record['finding']),values)
            else:
                edited=_tidy_deletion(memo.replace(quote,replacement),replacement)
                if _hollows_a_section(memo,edited):
                    record['reason']=('the edit would leave a section empty or too short to stand; rewrite the passage '
                                      'so the section still makes its argument')
                else:
                    memo=edited
                    record['applied']=True
        records.append(record)
    return memo,records


def _hollows_a_section(before, after):
    """True when an edit leaves more sections empty, near-empty or unfinished than there were before it.

    The Walmart memo of 2026-11-27 lost two paragraphs of section 1 to edits whose replacement was
    empty, and was delivered with a one-sentence section. An edit that repairs a thin section, or
    leaves an already thin one thin, is not refused here; the empty-section check reports that.
    """
    return len(structure_problems(after))>len(structure_problems(before))


def _tidy_deletion(memo, replacement):
    """After an edit that deletes text, close the run of blank lines it leaves.

    The Walmart memo of 2026-11-27 shipped with five blank lines where edits had cut most of section 1.
    """
    return re.sub(r'\n{3,}','\n\n',memo) if not replacement.strip() else memo


def _dollar_filer(plan, result):
    return str((result or {}).get('currency') or (plan or {}).get('currency') or '').upper()=='USD'


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
# Claude Haiku 5.5 reasons without being asked and OpenRouter counts that inside
# the output limit: a live Microsoft lane (2026-11-26) spent 4,118 of 6,000
# tokens reasoning and was cut off. Easy-model stages therefore allow 12,000
# (about $0.03 more reservation per call at Haiku prices).
WEB_RESEARCH_TOKENS = 12000
EVIDENCE_SYNTHESIS_TOKENS = 12000
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
    reason='the valuation is dated at the run\'s latest evidence retrieval (UTC calendar date)'
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


def _anchored_history(sources, anchor_revenue):
    """(source id, rows) of the reported history, oldest first, whose last year is the revenue anchor year.

    Without ``anchor_revenue`` every history qualifies. With it, a history whose last
    year's revenue is not the pinned revenue (a valuation date before the latest annual
    report, or an anchor from another source) describes a different year and is skipped,
    as reported_rnd_history does.
    """
    for sid,rows in _grouped_history(sources).items():
        if not rows:
            continue
        if isinstance(anchor_revenue,(int,float)) and not isinstance(anchor_revenue,bool) and anchor_revenue>0:
            last=_amount(rows[-1].get('revenue'))
            if last is None or abs(last-anchor_revenue)>1e-6*anchor_revenue:
                continue
        yield sid,rows


def reinvestment_reference(sources, anchor_revenue=None):
    """Last reported net reinvestment and the incremental sales-to-capital the plan is held to, or None.

    The rate is computed two ways over the same years, the revenue increase over the
    last three fiscal years divided by (i) capital expenditure less depreciation and
    amortization plus acquisitions and finance-lease additions, and (ii) the change in
    invested capital by Mauboussin and Callahan's method ("investment in future growth
    is therefore equivalent to the change in invested capital", ROIC, October 2022,
    p8), which also counts working capital and operating leases. The lower rate, which
    asks more capital of each unit of growth, is the reference (2026-11-26 critique:
    Nvidia 9.6 on capital expenditure against 2.2 on invested capital). A business
    whose capital expenditure is below depreciation is still checked when invested
    capital rose (Apple, Uber). The floor stays on (i), the owner's net reinvestment,
    and applies only when its last year is positive. A business whose revenue fell
    has no meaningful rate and is not checked.

    Deals count alike on both measures (review of the merged code, 2026-11-27). Net
    reinvestment counts a deal at the cash paid. In the invested-capital change, growth
    in goodwill and acquired intangibles counts only up to the cash paid for
    acquisitions in those years, so a stock-funded deal adds no investment (Salesforce
    fiscal 2022, Slack: 0.30 with all the goodwill against 0.70; Uber fiscal 2022: 0.36
    against 0.55). Leaving acquired intangibles out entirely and adding all cash paid
    back, as first specified, also dropped their amortization and impairments and
    tightened references with no deal in them (Coca-Cola 0.83 to 0.41, Salesforce
    fiscal 2026 1.93 to 1.23). Where no year tags an acquisitions line the payment is
    unknown, and the capital change stays as reported.
    """
    for sid,rows in _anchored_history(sources,anchor_revenue):
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
        acquired=sum(_amount(row.get('acquisitions')) or 0 for row in recent[1:])
        tagged=any(_amount(row.get('acquisitions')) is not None for row in recent[1:])
        consecutive=all(roic_consecutive(a,b) for a,b in zip(recent,recent[1:]))
        opening,closing=_amount(recent[0].get('invested_capital')),_amount(recent[-1].get('invested_capital'))
        bare=(_amount(recent[0].get('invested_capital_excluding_acquired')),
              _amount(recent[-1].get('invested_capital_excluding_acquired')))
        change,capital_measure,uncounted=None,'as_reported',None
        if opening is not None and closing is not None and consecutive:
            change=closing-opening
            if None not in bare and tagged:
                grown=(closing-bare[1])-(opening-bare[0])
                if grown>acquired:
                    # Goodwill and acquired intangibles grew by more than the cash paid: the rest came with stock.
                    uncounted=grown-acquired
                    change-=uncounted
                    capital_measure='stock_deals_excluded'
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
                'net_reinvestment_status':('reported' if floor is not None else 'not_positive' if nets else 'not_reported'),
                'capital_expenditure':last.get('capital_expenditure'),
                'depreciation_amortization':last.get('depreciation_amortization'),
                'acquisitions':last.get('acquisitions'),'finance_lease_additions':last.get('finance_lease_additions'),
                'acquisitions_over_years':acquired,
                'incremental_sales_to_capital':rates[chosen],'sales_to_capital_basis':chosen,
                'capital_expenditure_sales_to_capital':rates.get('capital_expenditure'),
                'invested_capital_sales_to_capital':rates.get('invested_capital'),
                'net_reinvestment_over_years':sum(nets) if nets else None,'invested_capital_change':change,
                'invested_capital_measure':capital_measure,'acquired_intangibles_not_paid_in_cash':uncounted,
                'revenue_increase':growth,'years':len(recent)-1,'source_refs':[sid],
                'basis':'Incremental sales-to-capital is the revenue increase over the last '
                        f'{len(recent)-1} fiscal years divided by each of two measures of the investment in those years: '
                        'net reinvestment (capital expenditure less depreciation and amortization plus cash acquisitions and '
                        'finance-lease additions where reported) and the change in invested capital (Mauboussin and '
                        'Callahan method, which includes working capital and operating leases), in which growth in '
                        'goodwill and acquired intangibles counts only up to the cash paid for acquisitions, so a '
                        'stock-funded deal adds no investment on either measure'+
                        (f' ({uncounted/1e9:,.1f} billion of it is left out here)' if uncounted else '')+
                        '. The lower rate is the reference. The reinvestment floor uses the last year of net '
                        'reinvestment, where it is positive.'}
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
# with a five-year average of -1.5% (Uber at the end of 2025) no bear case could pass.
# There the bear ceiling is the reported five-year peak, so a bear case still ends at
# or below the best reported year (review of the merged code, 2026-11-27).
YTD_CEILING_MIN_DAYS = 160
# A twelve-month span this long holds a 53-week year (Coca-Cola's four quarters to
# October 2026 span 371 days); the label says so.
TTM_NORMAL_DAYS = 367


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
            days=ttm.get('days')
            span=f' ({days} days)' if isinstance(days,int) and days>TTM_NORMAL_DAYS else ''
            return {'basis':'trailing_twelve_months','period':ttm['period'],'operating_margin':ttm['operating_margin'],
                    'days':days,'source_refs':[sid],
                    'label':f'the operating margin for the twelve months to {ttm["period"]["end_date"]}{span}'}
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


def cost_path_reference(sources, operating_reference=None, anchor_revenue=None):
    """Reported five-year peak and average operating margin and the final-year ceilings they set, or None.

    ``operating_reference`` (reported_inputs.operating_reference) supplies the year
    to date where the reported quarters give no twelve-month margin. With
    ``anchor_revenue`` the history must end at the revenue anchor year.
    """
    for sid,rows in _anchored_history(sources,anchor_revenue):
        margins=[row['operating_margin'] for row in rows[-5:] if isinstance(row.get('operating_margin'),(int,float))]
        if len(margins)<3:
            continue
        reference={'fiscal_year_end':rows[-1].get('fiscal_year_end'),'years':len(margins),
                   'max_operating_margin':max(margins),'average_operating_margin':sum(margins)/len(margins),
                   'source_refs':[sid],
                   'basis':'Reported operating margins over the last five fiscal years in the reported history; the base '
                           'and bull peak also includes the latest twelve-month operating margin, or a year to date of at '
                           'least two quarters, where it is higher. Where the five-year average is not positive, the bear '
                           'ceiling is the five-year peak.'}
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
    # A business that has not been profitable has no margin history to bound it.
    if reference['max_operating_margin']<=0:
        return None,'no ceiling: no reported year in the history is profitable'
    if name=='bear':
        average=reference['average_operating_margin']
        if average<=0:
            return reference['max_operating_margin'],'the reported five-year peak (the five-year average is not positive)'
        return average,'the reported five-year average'
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
    return attach_diagnostics(plan,evaluate(plan))


def attach_diagnostics(plan, result):
    """The continuity checks and base rates a pipeline run adds to a valuation; also used by --revalue."""
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
    acquired=check.get('acquisitions_over_years')
    both=all(isinstance(check.get(key),(int,float)) for key in ('capital_expenditure_sales_to_capital',
                                                                 'invested_capital_sales_to_capital'))
    counts=' Both rates count' if both else ' The rate counts'
    deals=(f'{counts} the {acquired/1e9:,.1f} billion paid in cash for acquisitions over those years as investment.'
           if isinstance(acquired,(int,float)) and acquired>0 else
           f'{counts} cash paid for acquisitions as investment, and none was reported in those years.')
    return [f'Reinvestment over forecast years 1-{REINVESTMENT_WINDOW} departs from the reported run-rate: '+floor_text+
            f'sales-to-capital may be at most {REINVESTMENT_RATIO:g} times the {check["years"]}-year incremental rate (computed from reported lines) of '
            f'{check["incremental_sales_to_capital"]:.2f}'+_rate_basis_text(check)+f' [{", ".join(check["source_refs"])}].'+deals+
            ' Outside the band: '+', '.join(breaks)+
            '. Either bring sales_to_capital and reinvestment within the band, or keep the forecast and state its evidence in '
            'capital_efficiency_basis.<scenario> with [S#] citations to sources in the evidence: for example that past growth '
            'came with acquisitions the forecast does not repeat, or that capital needs are changing']


def _rate_basis_text(reference):
    """Which measure of investment set the reference rate, and the other rate where there is one."""
    capital=('the change in invested capital, without goodwill and acquired intangibles not paid in cash'
             if reference.get('invested_capital_measure')=='stock_deals_excluded' else 'the change in invested capital')
    rates={'capital_expenditure':(reference.get('capital_expenditure_sales_to_capital'),'net reinvestment'),
           'invested_capital':(reference.get('invested_capital_sales_to_capital'),capital)}
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
                   'reinvestment from the net reinvestment and incremental sales-to-capital computed from reported lines; a large fall needs '
                   'capital_efficiency_basis.<bear|base|bull> with [S#] citations. Final-year EBIT margins may not exceed the '
                   'reported five-year average (bear; the five-year peak where that average is not positive) or the higher of the reported '
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


_DELIVERED_FILES=('memo.md','method-notes.md','assumptions.json','valuation.json','sources.json',
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
        report=_write_memo(folder,state.get('current_memo',''),state['sources'],state['gaps'],
            state.get('plan',{}),state.get('valuation'),state)
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
    'support for a company fact, a number used in a comparison, or the valuation conclusion. An evidence-gap note '
    'names the gap without restating such a figure. Prefer the primary source whenever both contain a fact.')
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


def _apply_edits(memo, edits, dollars=True):
    """Apply quote-and-replacement edits whose quote occurs exactly once and keep the writing standard; report the rest."""
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
            edited=_tidy_deletion(memo[:position]+memo[position+len(quote):],'')
            if _hollows_a_section(memo,edited):
                skipped.append('deletion would leave a section empty or too short: '+quote[:80])
                continue
            memo=edited
            applied+=1
            continue
        if count!=1:
            skipped.append(f'quote found {count} times: {quote[:80]}')
            continue
        if dollars:
            replacement=dollar_amounts(replacement)
        broken=style_added(quote,replacement,dollars,memo)
        if broken:
            skipped.append('replacement breaks the writing standard ('+', '.join(broken)+'): '+quote[:80])
            continue
        edited=_tidy_deletion(memo.replace(quote,replacement),replacement)
        if _hollows_a_section(memo,edited):
            skipped.append('edit would leave a section empty or too short: '+quote[:80])
            continue
        memo=edited
        applied+=1
    return memo,applied,skipped


def _evidence_context(sources):
    """Evidence for model prompts in three labelled tiers.

    Excerpts are bounded; full originals remain in the source archive. Never
    pass an entire filing, private environment or credentials into a prompt.
    """
    computed, primary, unverified, _ = _evidence_rows(sources)
    parts = []
    if computed:
        parts.append('COMPUTED FROM REPORTED FILINGS (Python tables; cite the bracketed source ID):\n' + '\n\n'.join(computed))
    parts.append('PRIMARY AND OFFICIAL SOURCES:\n' + json.dumps(primary, ensure_ascii=False))
    if unverified:
        parts.append('UNVERIFIED WEB EXCERPTS (search results; attributed outside views and consensus context only, '
                     'labelled unverified where cited):\n' + json.dumps(unverified, ensure_ascii=False))
    return '\n\n'.join(parts)


def _later_evidence(sources, unverified_used):
    """Sources added after the cached block, in the same tiers; the unverified budget continues from it."""
    computed, primary, unverified, _ = _evidence_rows(sources, unverified_used)
    parts = []
    if computed:
        parts.append('COMPUTED FROM REPORTED FILINGS (Python tables; cite the bracketed source ID):\n' + '\n\n'.join(computed))
    if primary:
        parts.append('PRIMARY AND OFFICIAL SOURCES:\n' + json.dumps(primary, ensure_ascii=False))
    if unverified:
        parts.append('UNVERIFIED WEB EXCERPTS (search results; attributed outside views and consensus context only, '
                     'labelled unverified where cited):\n' + json.dumps(unverified, ensure_ascii=False))
    return '\n\n'.join(parts)


def _evidence_rows(sources, used=0):
    """(computed tables, primary rows, unverified rows, unverified characters used) for the evidence tiers."""
    computed, primary, unverified = [], [], []
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
    return computed, primary, unverified, used


def _workflow_contract():
    """Track mutable prompts and the code that presents/audits their outputs."""
    root=Path(__file__).parent
    return {'system':_digest(core.SYSTEM),
            'forecast':_digest([FORECAST_SCHEMA,UNDERWRITING_GUIDANCE]),
            'review':_digest(REVIEW_SCHEMA), 'sections':_digest(SECTIONS),
            'implementation':_digest({name:hashlib.sha256((root/name).read_bytes()).hexdigest()
                for name in ('pipeline.py','prompt_context.py','quality.py','valuation.py','inputs.py','operating.py','rates.py','primary_debt.py','companyfacts.py',
                             'evidence_tables.py','research.py','base_rates.py','country_risk.py','cap_fade.py','roic.py',
                             'macro.py','consensus.py')
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
    for name in ('memo.md','method-notes.md','assumptions.json','valuation.json','cashflows.csv','sensitivity.csv'):
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
        _write_memo(folder,state['current_memo'],state['sources'],state['gaps'],
            state.get('plan',{}),state.get('valuation'),state)
    else:
        _write_placeholder(folder,'# '+state['ticker']+': workflow update in progress\n\nChanged research contracts require a fresh draft and review. Prior artifacts remain under revisions/.\n')


def _recover_supplied_evidence(folder,state,evidence):
    """Repair legacy snapshot runs that accepted but omitted this exact input."""
    if (not evidence or not state.get('evidence_collected') or
            any(s.get('kind')=='user_evidence' and s.get('data')==evidence for s in state['sources'])):
        return
    prior=folder/'revisions'/f'evidence-recovery-{len(state.get("evidence_recoveries",[]))+1}'
    prior.mkdir(parents=True,exist_ok=True)
    core.save(prior/'sources.json',state['sources'])
    core.save(prior/'review-history.json',state.get('review_history',[]))
    for name in ('memo.md','method-notes.md','assumptions.json','valuation.json','cashflows.csv','sensitivity.csv'):
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
    _write_placeholder(folder,'# '+state['ticker']+': supplied evidence recovered\n\nThe saved run omitted accepted evidence. '
        'Its model and memo require a fresh review against the recovered source; prior artifacts remain under revisions/.\n')


def _initial_budget_preflight(router, state, *, ticker, easy, strong, reviewer,
                              forecast_plan=None, draft=None, max_revisions=DEFAULT_MAX_REVISIONS, plan_samples=1):
    """Price known repeated context before the first potentially billed call.

    Future search excerpts, forecasts, derived valuation data and memo prose are
    unknown at this point. This is deliberately a reservation *floor*, not an
    upper bound, token prediction, invoice estimate, or completion guarantee.
    The real request retains its full byte-based reservation in Router.complete.
    The pass test covers the first draft and its review. Each revision round is
    priced separately: its usual stages (targeted research, the edit call, the
    free-check fix, the review and its check) and the stages it adds only when
    a finding concerns the forecast or an edit fails (forecast response, rewrite).
    """
    context = _evidence_context(state['sources'])
    def priced(stage, model, prompt, tokens, web=False, cached_prefix=None):
        # Reasoning tokens are reserved on top of the output limit, as the router does.
        catalog=getattr(router,'catalog',{}) or {}
        if core.reasoning_request(catalog.get(model),model,core.reasoning_budget(stage)) is not None:
            tokens+=core.reasoning_budget(stage)
        # A later plan sample resends a prefix the first one wrote: its prefix is priced at the cache-read price.
        extra={'prefix':cached_prefix,'cached':True} if cached_prefix else {}
        return {'stage':stage, 'model':model, 'known_prompt_utf8_bytes':len(prompt.encode()+(cached_prefix or '').encode()),
                'max_completion_tokens':tokens, 'web_search':web,
                **({'prefix_reserved_at':'cache_read'} if cached_prefix else {}),
                'reservation_floor_usd':router.estimate(model,prompt,tokens,web=web,**extra)}
    stages = []
    def add(*args, **kwargs):
        stages.append(priced(*args, **kwargs))
    if not state.get('web_research_complete'):
        for lane in ('business','challenge','expectations','claims'):
            add('web_'+lane,easy,f'Research {ticker}: {lane}.',WEB_RESEARCH_TOKENS,web=True)
    supplied_model = forecast_plan is not None or state.get('plan')
    supplied_memo = draft is not None or state.get('current_memo')
    if not (supplied_model and supplied_memo):
        add('evidence_synthesis',easy,'Build a dated evidence table.\nEvidence:\n'+context,EVIDENCE_SYNTHESIS_TOKENS)
    if not supplied_model:
        add('valuation_plan',strong,'Build an investment valuation plan.\n'+
            FORECAST_SCHEMA+UNDERWRITING_GUIDANCE+PLAN_SOURCE_RULE+'\nEvidence:\n'+context,12000)
        # Later samples resend the evidence prefix the first one writes: reserved at the cache-read price.
        for sample in range(2,plan_samples+1):
            add(_plan_stage_name(sample),strong,'Build an investment valuation plan.\n'+FORECAST_SCHEMA+
                UNDERWRITING_GUIDANCE+PLAN_SOURCE_RULE,12000,cached_prefix=_evidence_prefix(context))
    valuation = ''
    if state.get('valuation') is not None and state.get('plan'):
        valuation = _model_valuation_context(state['plan'],state['valuation'])
    memo = draft or state.get('current_memo','')
    if not supplied_memo:
        add('full_draft',strong,'Write a complete investment-committee draft.\nEvidence:\n'+
            context+'\nValuation:\n'+valuation,MEMO_TOKENS)
        # The free checks flagged every saved draft, so the fix call is planned for.
        add('draft_check_fix_'+str(state['iteration']),strong,'Correct only the problems listed below.\nEvidence:\n'+context,8000)
    add('review_'+str(state['iteration']),reviewer,'Independently audit the complete memo.\n'+
        REVIEW_SCHEMA+'\nEvidence:\n'+context+'\nValuation:\n'+valuation+
        '\nDraft:\n'+memo,12000)
    add('finding_check_'+str(state['iteration']),reviewer,FINDING_CHECK+'\nEvidence:\n'+context+'\nValuation:\n'+valuation+
        '\nDraft:\n'+memo,FINDING_CHECK_TOKENS)
    floor = sum(row['reservation_floor_usd'] for row in stages)
    rounds = []
    for number in range(state['iteration']+1, state['iteration']+1+max_revisions):
        usual = [priced(f'targeted_research_{number}',easy,f'Investigate {ticker} primary evidence.',WEB_RESEARCH_TOKENS,web=True),
                 priced(f'prose_edits_{number}',strong,EDIT_INSTRUCTIONS+'\nEvidence:\n'+context+'\nMemo:\n'+memo,EDIT_TOKENS),
                 priced(f'draft_check_fix_{number}',strong,'Correct only the problems listed below.\nEvidence:\n'+context,8000),
                 priced(f'review_{number}',reviewer,'Independently audit the complete memo.\n'+REVIEW_SCHEMA+'\nEvidence:\n'+
                        context+'\nValuation:\n'+valuation+'\nDraft:\n'+memo,12000),
                 priced(f'finding_check_{number}',reviewer,FINDING_CHECK+'\nEvidence:\n'+context+'\nValuation:\n'+valuation+
                        '\nDraft:\n'+memo,FINDING_CHECK_TOKENS)]
        conditional = [priced(f'prose_edits_{number}_retry',strong,EDIT_INSTRUCTIONS+'\nEvidence:\n'+context+'\nMemo:\n'+memo,EDIT_TOKENS),
                       priced(f'model_response_{number}',strong,'Address these review findings.\n'+FORECAST_SCHEMA+
                              UNDERWRITING_GUIDANCE+'\nEvidence:\n'+context+'\nMemo:\n'+memo,12000),
                       priced(f'revise_{number}',strong,'Revise the complete memo.\nEvidence:\n'+context+'\nValuation:\n'+
                              valuation+'\nMemo:\n'+memo,MEMO_TOKENS)]
        rounds.append({'round':number,'usual_stages':usual,'conditional_stages':conditional,
                       'usual_floor_usd':sum(row['reservation_floor_usd'] for row in usual),
                       'conditional_floor_usd':sum(row['reservation_floor_usd'] for row in conditional)})
    every = stages+[row for item in rounds for row in item['usual_stages']+item['conditional_stages']]
    suggested=max(DEFAULT_BUDGET,float(math.ceil(floor)))
    return {'scope':'First draft, its fix call, one review and its finding check'+
                     (f', and {plan_samples} forecast plan samples, the later ones at the cache-read price' if plan_samples>1 else '')+
                     '; revision rounds are priced separately and format/forecast repairs are excluded',
            'basis':'Uncached UTF-8 byte reservations at maximum catalog price tiers and configured output limits. '
                    'Only currently known context is included. Future search results, synthesis, forecasts, '
                    'computed valuation and memo text can increase reservations; this floor is not a completion guarantee or predicted bill.'+
                    (' A later plan sample is reserved at the cache-read price; if the provider misses the cache it is '
                     'billed at the cache-write price, the ledger records the shortfall, and every later reservation '
                     'counts the actual charge.' if plan_samples>1 else ''),
            'budget_usd':router.budget, 'known_context_reservation_floor_usd':floor,
            'suggested_total_allowance_usd':suggested if suggested<=100 else None,
            'max_revisions':max_revisions, 'passed':router.budget>=floor, 'stages':stages,
            'revision_rounds':rounds, 'plan_samples':plan_samples,
            # The whole run priced together: the K plan samples (the later ones at the
            # cache-read price), the first draft and review, and every revision round.
            'usual_path_reservation_floor_usd':floor+sum(item['usual_floor_usd'] for item in rounds),
            'worst_case_reservation_floor_usd':floor+sum(item['usual_floor_usd']+item['conditional_floor_usd'] for item in rounds),
            'largest_single_reservation_floor_usd':max((row['reservation_floor_usd'] for row in every),default=0.0),
            'revision_basis':'Each call needs its own reservation on top of what earlier calls actually cost; a round '
                             'adds the conditional stages only when an edit fails (one retry) or a finding concerns the forecast (the response, '
                             'and a rewrite only when the forecast inputs change).'}


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
            # Python rebuilds the per-year ratios from sales_to_capital, and sets the R&D
            # shares from the R&D line, on every lock; the author edits those inputs instead.
            for field in ('sales_to_capital_path','rnd_share_path','rnd_basis'):
                scenario.pop(field,None)
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
    R&D (Costco) gets none. The continuing value uses the final-year share, with
    amortization at steady terminal growth (owner decision, 2026-11-26).
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
    # Retired with the forecast-based amortization (2026-11-26); a plan locked under it drops it.
    locked.pop('reported_rnd_history',None)


def forecast_capital_reference(sources, anchor_revenue=None):
    """Operating invested capital (Mauboussin and Callahan method) at the last reported year end, or None.

    Only the last reported year counts: an earlier year's capital would start the
    roll-forward from the wrong balance sheet. With ``anchor_revenue`` that year must
    be the revenue anchor year.
    """
    for sid,rows in _anchored_history(sources,anchor_revenue):
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
    rate_units=[]
    if apply_conventions and isinstance(proposal.get('discount_rate'),dict):
        # Python copies the rate block's debt, lease and equity amounts from the reported
        # bridges, so its currency and unit are the plan's. Two of eight live plan samples
        # (2026-11-27) paid for a repair because the model wrote them differently.
        for field in ('currency','amount_unit'):
            if proposal['discount_rate'].get(field)!=proposal[field]:
                rate_units.append({'field':'discount_rate.'+field,'previous':proposal['discount_rate'].get(field),
                                   'pinned':proposal[field],'source_refs':[],
                                   'reason':'the rate block uses the plan currency and unit; its amounts are copied from the reported bridges'})
                proposal['discount_rate'][field]=proposal[field]
    locked,changes=pin_reported_inputs(proposal,sources)
    changes.extend(rate_units)
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
                                        'amortization at steady terminal growth) back to NOPAT, so R&D after the forecast is '
                                        'investment that earns the terminal return.')
    else:
        convention_notes=['This supplied plan keeps its own rate and timing conventions; the market-consistent rate path '
                          'and valuation-date stub apply only when a plan opts in with discount_rate.term_structure or valuation_timing.']
    derived_note=locked.pop('_derived_path_note',None)
    if derived_note:
        convention_notes=[*convention_notes,derived_note]
    convention_notes=[*convention_notes,*_pin_repurchase_history(locked,sources,changes)]
    pin_terminal=apply_conventions
    anchor=(locked.get('actuals') or {}).get('revenue')
    reference=reinvestment_reference(sources,anchor)
    if reference:
        locked['reinvestment_reference']=reference
    else:
        locked.pop('reinvestment_reference',None)
    costs=cost_path_reference(sources,reported.get('operating_reference'),anchor)
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
    capital=forecast_capital_reference(sources,anchor)
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
            'record reports no other short-term borrowing amount. Undisclosed borrowings would lower equity value. ')
    locked['assumptions_label']=(
        'Growth, margins, taxes, discount rates, capital efficiency, terminal returns and '+
        ('the bear and bull stress cases ' if apply_conventions else 'scenario weights ')+
        'are house forecasts. Historical actuals use the '
        'reported-input snapshot. Only the cash and investment assets selected in the pinned bridge are '
        'classified as nonoperating under an explicit house policy. An omitted investment category counts as zero by '
        'assumption. All selected cash and investments are treated as nonoperating; any part the business '
        'needs to operate would lower equity value. '+lease_allocation+
        ('Non-operating investments outside the selected cash are added, and noncontrolling interests, preferred stock '
         'and unfunded pension and postretirement obligations are deducted, at carrying value; pension obligations '
         'are taken before tax. Value per share divides by outstanding shares plus the latest fiscal year\'s dilutive '
         'increment. ' if locked.get('nonoperating_assets_bridge') or locked.get('share_count_bridge') else '')+
        'Unidentified claims '
        'are assumed zero. GAAP EBIT retains operating rent, SBC and R&D. '+
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
    """Median ROIC computed from reported values (Mauboussin and Callahan method) over the history, or None."""
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
                    'basis':'Five-year median of return on invested capital computed from reported values by the Mauboussin and Callahan method '
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
    return ['Terminal return on new capital equals the terminal discount rate, Mauboussin\'s steady state. The 10-year '
            'forecast is the house horizon for excess returns. After year 10, new investment earns its cost of capital, so '
            'growth adds no value. '+RATE_TEST_RULE+' The convention panel shows the value with a terminal '
            'return 4 points above the terminal rate, at 15% and 25%, and at the earlier house rule where that differs from '
            'these. The earlier rule is the median ROIC computed from reported values, at most 15%. The panel also shows '
            'horizons of 5, 15 and 20 years and Mauboussin\'s industry fade rate where the industry mapping has one.']


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


# ---------------------------------------------------------------------------
# The reader's memo. Python writes the title, the first page, the valuation
# detail, the reported history, the method notes, the sources and the review
# status; the model writes the eight prose sections between the first page and
# the valuation detail. Every sentence here follows the owner's voice rules:
# short declaratives, no "X, not Y", one statement of each caveat, compact
# numbers with the currency named once (memo redesign, 2026-11-27).
# ---------------------------------------------------------------------------
FIRST_PAGE_HEADING = '## Summary'
DETAIL_HEADING = '## Appendix A: the model'
HISTORY_HEADING = '## Appendix B: reported history'
NOTES_HEADING = '## Method notes'
# The memo keeps the notes true only of this company and run; the house method notes
# for the run go to a file beside it (memo redesign, readability review 2026-11-27).
MEMO_NOTES_HEADING = '## Notes'
METHOD_NOTES_FILE = 'method-notes.md'
BRIEF_HEADING = '## In brief'
# A bridge struck this long before the valuation date shows the elapsed period as rows.
STALE_BRIDGE_DAYS = 90


class _Specific(str):
    """A note true only of this company and this run: it stays in the memo. Other notes go to method-notes.md."""


def _clean_cell(value):
    return str(value).replace('|','\\|').replace('\n',' ')


def _table(header, rows, align=None):
    """A Markdown table: first column left-aligned, the rest right-aligned unless ``align`` says otherwise."""
    align=align or ['---']+['---:']*(len(header)-1)
    return (['| '+' | '.join(header)+' |','|'+'|'.join(align)+'|']+
            ['| '+' | '.join(_clean_cell(cell) for cell in row)+' |' for row in rows])


def _cite(refs):
    refs=[refs] if isinstance(refs,str) else refs if isinstance(refs,list) else []
    ids=[ref.strip().strip('[]') for ref in refs if isinstance(ref,str) and re.fullmatch(r'\[?S\d+\]?',ref.strip())]
    return ' '+' '.join(f'[{ref}]' for ref in dict.fromkeys(ids)) if ids else ''


def _style(plan, result):
    return memo_style.Style((result or {}).get('currency') or (plan or {}).get('currency'))


def _finite_number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def _base_headline(result):
    """True when the base case carries the whole weight (the house convention since 2026-11-26)."""
    scenarios=(result or {}).get('scenarios') or {}
    return all(abs(((scenarios.get(name) or {}).get('probability') or 0)-weight)<1e-9 for name,weight in DEFAULT_WEIGHTS.items())


# Short reader labels for the convention-panel rows, keyed by row name. The saved
# row labels carry their rationale; that rationale is stated once in the method notes.
def _rnd_addback(result):
    """The base case's net R&D add-back in the continuing value, 0.0 when there is none."""
    value=(((result or {}).get('scenarios') or {}).get('base') or {}).get('terminal_rnd_net_investment')
    return value if _finite_number(value) and value>0 else 0.0


def _terminal_follows(result):
    """True when the base terminal return on new capital equals the terminal rate (Mauboussin's steady state)."""
    base=((result or {}).get('scenarios') or {}).get('base') or {}
    applied,rate=base.get('terminal_roic_applied'),base.get('terminal_rate')
    return _finite_number(applied) and _finite_number(rate) and abs(applied-rate)<1e-9


def _plain(identifier):
    """A plan identifier as words: 'products_gross_margin_q1_fy2027' is 'Products gross margin Q1 FY2027'."""
    words=[]
    for word in str(identifier or '').replace('_',' ').replace('-',' ').split():
        upper=re.fullmatch(r'(?:q[1-4]|h[12]|fy\d{2,4}|ai|ttm|eps|ebit|roic|r&d|us|uk|eu)',word,re.I)
        words.append(word.upper() if upper else word)
    text=' '.join(words)
    return text[:1].upper()+text[1:]


def _change(value, base):
    """The true per-share change, value less base before rounding, at the precision of the value shown."""
    if not (_finite_number(value) and _finite_number(base)):
        return 'n/a'
    return memo_style.bare_change(value-base,value)


# The change columns round the unrounded difference (re-audit, 2026-11-27).
CHANGE_NOTE = ('Changes are computed before rounding, so a change can differ by 0.1 from the difference of the rounded '
               'values shown.')


PANEL_LABELS = {
    'constant_initial_rate': 'Current discount rate held in every year',
    'damodaran_default_terminal_rate': 'Terminal rate at the risk-free rate plus the equity risk premium',
    'terminal_growth_at_risk_free_rate': 'Terminal growth at the risk-free rate',
    'tax_to_marginal_rate': 'Terminal tax at the marginal rate',
    'terminal_return_equals_terminal_rate': 'Terminal return on new capital at the terminal rate',
    'terminal_return_earlier_house_rule': 'Terminal return at the five-year median ROIC, at most 15%',
    'terminal_return_rate_plus_4_points': 'Terminal return on new capital 4 points above the terminal rate',
    'base_margin_held_at_reported_peak': 'EBIT margin capped at the five-year peak',
    'terminal_return_15_percent': 'Terminal return on new capital of 15%',
    'terminal_return_25_percent': 'Terminal return on new capital of 25%',
    'terminal_reinvestment_continues_forecast': 'Terminal reinvestment at the forecast capital efficiency',
    'terminal_rnd_expensed': 'R&D expensed in the continuing value',
    'required_return_minus_1_point': 'Whole discount-rate path 1 point lower',
    'required_return_plus_1_point': 'Whole discount-rate path 1 point higher',
}
PANEL_NOTES = {
    'constant_initial_rate': ('The current-rate row holds the year-1 discount rate in every year and in the terminal '
                              'value, and the terminal return on new capital follows that rate.'),
    'damodaran_default_terminal_rate': ('The risk-free-plus-premium row is the Damodaran spreadsheet default: the terminal '
                                        'rate is the risk-free rate plus the equity risk premium at any leverage.'),
    'tax_to_marginal_rate': ('The marginal-tax row taxes the steady state and the continuing value at the marginal rate, '
                             'the Damodaran default.'),
    'terminal_return_rate_plus_4_points': 'The 4-point row gives new investment a lasting but modest excess return.',
    'terminal_return_15_percent': ('15% is the house\'s stylized long-run market return on new capital, the figure the '
                                   'S&P 500 row uses. Damodaran used 15% as an override for durable advantages, as for '
                                   'Apple in 2024.'),
    'terminal_return_25_percent': 'The 25% row assumes new investment keeps earning well above the market forever.',
    'terminal_rnd_expensed': ('The R&D-expensed row is the earlier house rule. It adds no net R&D investment back to '
                              'NOPAT, so new R&D after the forecast earns below its cost.'),
    'base_margin_held_at_reported_peak': ('The peak-margin row keeps the base-case EBIT margin at or below the highest '
                                          'operating margin of the last five fiscal years.'),
}


SKIPPED_NOTES = {
    'terminal_growth_at_risk_free_rate': 'The terminal-growth row does not apply. House terminal growth already equals the '
                                         'risk-free rate.',
    'terminal_return_equals_terminal_rate': 'The steady-state row does not apply. The house terminal return already equals '
                                            'the terminal rate.',
}


def _reinvestment_row_note(result, style):
    """What the forecast-capital-efficiency row changes, with the run's own reinvestment figures."""
    row=((result.get('computed_diagnostics') or {}).get('terminal_transition') or {}).get('base') or {}
    flows=(((result.get('scenarios') or {}).get('base') or {}).get('cashflows') or [])
    explicit=[flow for flow in flows if flow.get('phase','explicit')=='explicit']
    continued,house=row.get('terminal_reinvestment_if_forecast_sales_to_capital_continued'),row.get('terminal_reinvestment')
    ratio=row.get('forecast_sales_to_capital')
    text='The forecast-capital-efficiency row keeps the year-10 sales-to-capital'
    if _finite_number(ratio):
        text+=f' of {ratio:.2f}'
    if _finite_number(continued) and _finite_number(house) and explicit and _finite_number(explicit[-1].get('net_reinvestment')):
        text+=(f' after year 10, so continuing-value reinvestment is {style.amount(continued)}, against '
               f'{style.amount(explicit[-1]["net_reinvestment"])} in year 10 and {style.amount(house)} under the house rule.')
    else:
        text+=' after year 10.'
    if _rnd_addback(result):
        text+=(f' It also keeps R&D expensed in the continuing value, so it drops the house net R&D add-back of '
               f'{style.amount(_rnd_addback(result))}.')
    return text


def _earlier_rule_note(plan, result):
    """Which value the earlier house rule (median ROIC, at most 15%, never below the terminal rate) gives here."""
    median=((plan or {}).get('terminal_return_reference') or {}).get('median_return_on_capital')
    rate=(((result or {}).get('scenarios') or {}).get('base') or {}).get('terminal_rate')
    if not (_finite_number(median) and _finite_number(rate)):
        return 'The earlier house rule, the five-year median ROIC capped at 15%, adds no separate row here.'
    rule=min(max(median,rate),MARKET_TERMINAL_ROIC)
    which=('the 15% row' if abs(rule-MARKET_TERMINAL_ROIC)<1e-9 else 'the house value' if abs(rule-rate)<1e-9 else
           f'{rule:.1%}')
    return (f'The earlier house rule takes the five-year median ROIC computed from reported values, {median:.1%}, capped '
            f'at 15% and never below the terminal rate. Here it gives {which}, so it has no row of its own.')
BETA_LABELS = {'regression_beta': 'Regression beta, cash-corrected',
               'blume_adjusted_regression_beta': 'Regression beta moved a third toward one'}


def _beta_label(row, checks=()):
    if row.get('name')=='blume_adjusted_regression_beta':
        measured=next((re.search(r'beta (\d+\.\d+) against',str(item.get('label') or '')) for item in checks
                       if item.get('name')=='regression_beta'),None)
        if measured:
            adjusted=2/3*float(measured.group(1))+1/3
            return f'Regression beta moved a third toward one (equity {adjusted:.3f}), cash-corrected'
    return BETA_LABELS.get(row.get('name')) or str(row.get('label') or '').split(',')[0]


def _beta_note(row):
    match=re.search(r'beta (\d+\.\d+) against (\w+)',str(row.get('label') or ''))
    if row.get('name')=='regression_beta' and match:
        return (f'The regression beta is the five-year weekly equity beta of {match.group(1)} against {match.group(2)}, '
                'corrected for cash to the unlevered beta shown and relevered for the discount rate.')
    if row.get('name')=='blume_adjusted_regression_beta':
        return ('The adjusted beta is two thirds of the measured beta plus one third, the common adjustment for '
                'estimation error.')
    return None


def _panel_label(row, result=None):
    name=row.get('name') or ''
    if name=='terminal_reinvestment_continues_forecast' and _rnd_addback(result):
        # The row changes two inputs: it also drops the net R&D add-back (valuation._convention_panel).
        return PANEL_LABELS[name]+', with R&D expensed'
    if name in PANEL_LABELS:
        return PANEL_LABELS[name]
    match=re.fullmatch(r'competitive_advantage_period_(\d+)_years',name)
    if match:
        return f'Competitive advantage period of {match.group(1)} years'
    if name=='competitive_advantage_period_industry_fade':
        years=row.get('horizon_years')
        return 'Industry fade rate'+(f', {years} years in all' if isinstance(years,int) else '')
    match=re.fullmatch(r'treasury_rate_(minus|plus)_(\d+)_bp',name)
    if match:
        size=int(match.group(2))/100
        rate=row.get('risk_free_rate')
        return (f'Treasury rate {size:g} point{"s" if size!=1 else ""} {"lower" if match.group(1)=="minus" else "higher"}'+
                (f', at {rate:.2%}' if _finite_number(rate) else ''))
    if name in ('rate_history_lowest','rate_history_highest'):
        when=str(row.get('observed_at') or '')[:7]
        rate,premium=row.get('risk_free_rate'),row.get('equity_risk_premium')
        text=f'Month giving the {"lowest" if name.endswith("lowest") else "highest"} value'+(f', {when}' if when else '')
        if _finite_number(rate) and _finite_number(premium):
            text+=f': Treasury {rate:.2%}, premium {premium:.2%}'
        return text
    if name=='country_risk_premium_revenue_weighted':
        premium=row.get('weighted_country_risk_premium')
        return ('Country risk premium'+(f' of {premium*100:.2f} points' if _finite_number(premium) else '')+
                ' on the cost of equity')
    label=str(row.get('label') or name.replace('_',' '))
    return label.split(' (')[0].split(':')[0]


def _panel_rows(rows, style, result=None):
    """Panel rows as (label, value, change); the change is taken from the rounded value and base shown."""
    base=(((result or {}).get('convention_panel') or {}).get('baseline_per_share_value') or
          (((result or {}).get('scenarios') or {}).get('base') or {}).get('per_share_value'))
    out=[]
    for row in rows:
        if row.get('per_share_value') is not None:
            value=memo_style.bare_per_share(row['per_share_value'])
            change=(_change(row['per_share_value'],base) if _finite_number(base) else
                    memo_style.bare_change(row['dollar_delta'],row['per_share_value']) if row.get('dollar_delta') is not None
                    else 'n/a')
        else:
            value,change=row.get('status','not calculated').replace('_',' '),''
        out.append([_panel_label(row,result),value,change])
    return out


def _top_conventions(result, count=3):
    panel=[row for row in ((result.get('convention_panel') or {}).get('rows') or [])
           if row.get('kind')=='convention' and row.get('status')=='calculated' and row.get('dollar_delta') is not None]
    return sorted(panel,key=lambda row:-abs(row['dollar_delta']))[:count]


REQUIREMENT_LABELS = {'revenue_growth': 'Revenue growth', 'ebit_margin': 'EBIT margin',
                      'terminal_roic': 'Terminal return on new capital', 'sales_to_capital': 'Sales-to-capital',
                      'terminal_growth': 'Terminal growth'}


def _percentile_word(text):
    return str(text).replace(' percentile','').replace('the ','')


def _requirement_percentiles(result, row):
    """(house, needed) base-rate percentile words for the revenue-growth row, or None."""
    comparison=result.get('base_rates')
    if row.get('input')!='revenue_growth' or not isinstance(comparison,dict) or comparison.get('status')!='computed':
        return None
    years=((row.get('house_path') or {}).get('years')) or 0
    house_rows=(comparison.get('scenarios') or {}).get('base') or {}
    needed_rows=(comparison.get('price_requirement') or {}).get('horizons') or {}
    published=[int(key) for key in house_rows if int(key)<=years]
    if not published:
        return None
    key=str(max(published))
    house=house_rows.get(key,{}).get('description')
    needed=needed_rows.get(key,{}).get('description') if row.get('status')=='solved' else None
    return key,(_percentile_word(house) if house else None),(_percentile_word(needed) if needed else None)


def _implied_follows(implied):
    """True when the implied-return search let the terminal return follow the solved rate (it starts at the 0.50% floor)."""
    floors=[row.get('search_floor') for row in (implied.get('scenarios') or {}).values() if isinstance(row,dict)]
    return bool(floors) and all(_finite_number(value) and abs(value-.005)<1e-9 for value in floors)


def _growth_effect(result, row, style):
    """(first-page cell, detail result) for terminal growth when the terminal return follows the rate, else None.

    With new capital earning its cost after year 10, growth leaves the value unchanged except through the net R&D
    add-back. The search then never fails; the text says what growth does (computed-text audit, 2026-11-27).
    """
    if row.get('input')!='terminal_growth' or row.get('status')!='not_reachable' or not _terminal_follows(result):
        return None
    base=((result.get('scenarios') or {}).get('base') or {})
    house,closest,at=base.get('per_share_value'),row.get('closest_value'),row.get('at')
    if not (_finite_number(house) and _finite_number(closest)):
        return None
    if abs(closest-house)<max(.005,abs(house)*1e-6):
        return ('does not change the value',
                f'value stays {style.per_share(house)} over the range searched, because new capital earns its cost after year 10')
    return (f'not reached within the range tested (up to {memo_style.pct(at)}; value {style.per_share(closest)}, through '
            'the R&D add-back only)',
            f'value moves only through the net R&D add-back, from {style.per_share(house)} at '
            f'{memo_style.pct(base.get("terminal_growth"))} to {style.per_share(closest)} at {memo_style.pct(at)}')


def _not_reached(row, style):
    """The cell for a bounded search that stopped short of the price: what was tested and the value at its edge.

    "Cannot reach the price" was printed for every unreached row, but a search that stops at a bound
    proves nothing past it: Walmart's revenue shift stopped at +30 points while the constant-growth
    reverse DCF reached the price at 34.8% (final Walmart review, 2026-11-27).
    """
    at,closest,searched=row.get('at'),row.get('closest_value'),row.get('searched')
    if not _finite_number(at):
        return 'not reached within the range tested'
    low=isinstance(searched,list) and len(searched)==2 and _finite_number(searched[0]) and abs(at-searched[0])<1e-9
    if row.get('kind')=='shift':
        edge=f'{"down" if low else "up"} to {memo_style.points(at).replace(".0 points"," points")} in every forecast year'
    elif row.get('input')=='sales_to_capital':
        edge=f'{"down" if low else "up"} to {memo_style.ratio(at,0 if float(at).is_integer() else 2)}'
    else:
        whole=abs(at*100-round(at*100))<1e-9 and abs(at)>=.1
        edge=f'{"down" if low else "up"} to {memo_style.pct(at,0 if whole else 1)}'
    value=f'; value {style.per_share(closest)}' if _finite_number(closest) and style else ''
    return f'not reached within the range tested ({edge}{value})'


def _reverse_growth(result):
    """(rates, caveat) from the constant-growth reverse DCF when it solves, else None.

    The caveat says in how many explicit forecast years the solution's free cash flow is negative.
    """
    reverse=(result or {}).get('reverse_dcf') or {}
    rates=[rate for rate in reverse.get('constant_annual_growth_solutions') or [] if _finite_number(rate)]
    if reverse.get('status')!='solved' or not rates:
        return None
    flows=[flow for flow in ((reverse.get('solution_projections') or [{}])[0] or {}).get('cashflows') or []
           if flow.get('phase','explicit')=='explicit' and _finite_number(flow.get('fcff'))]
    negative=sum(1 for flow in flows if flow['fcff']<0)
    caveat=None
    if flows and negative==len(flows):
        caveat='free cash flow negative in every forecast year'
    elif negative:
        caveat=f'free cash flow negative in {negative} of {len(flows)} forecast years'
    return rates,caveat,len(flows)


def _reverse_growth_cell(result):
    """The first-page cell for the constant-growth reverse DCF: "34.8% a year for ten years (caveat)"."""
    found=_reverse_growth(result)
    if not found:
        return None
    rates,caveat,years=found
    horizon=NUMBER_WORDS.get(years,str(years))
    text=(f'{memo_style.pct(rates[0])} a year for {horizon} years' if len(rates)==1 else
          'several constant rates: '+', '.join(memo_style.pct(rate) for rate in rates))
    return text+(f' ({caveat})' if caveat else '')


def _requirement_cells(row, result=None, style=None):
    """(input label, house figure, needed figure) for one price-requirement row."""
    label=REQUIREMENT_LABELS.get(row.get('input'),row.get('label','input'))
    def level(value):
        return memo_style.ratio(value) if row.get('input')=='sales_to_capital' else memo_style.pct(value)
    if row.get('kind')=='shift':
        path=row.get('house_path')
        if isinstance(path,dict):
            measure='compound' if path.get('measure')=='compound_annual_growth' else 'average'
            label+=f', {path["years"]}-year {measure}'
            house=memo_style.pct(path['over_forecast'])
            needed_path=row.get('needed_path')
            needed=(memo_style.pct(needed_path['over_forecast']) if row['status']=='solved' and isinstance(needed_path,dict)
                    else None)
        else:
            label+=', every forecast year'
            house=memo_style.pct(row.get('house'))
            needed=memo_style.points(row['needed']) if row['status']=='solved' else None
    else:
        capital=row.get('house_sales_to_capital_path')
        house=(f'{capital[0]:.2f} to {capital[-1]:.2f}' if capital else level(row.get('house')))
        needed=level(row['needed']) if row['status']=='solved' else None
    effect=_growth_effect(result,row,style) if result is not None else None
    if effect:
        needed=effect[0]
    elif needed is None and row.get('status')=='not_reachable':
        # Two tests of revenue growth: the parallel shift is bounded; the constant-growth reverse DCF is not.
        reverse=_reverse_growth_cell(result) if row.get('input')=='revenue_growth' else None
        needed=reverse or _not_reached(row,style)
    elif needed is None:
        needed='not calculated'
    return label,house,needed


NUMBER_WORDS = {1:'one',2:'two',3:'three',4:'four',5:'five',6:'six',7:'seven',8:'eight',9:'nine',10:'ten'}


def _discount_rate_requirement(result, style):
    """[label, house, needed] for the flat discount rate at which the headline value equals the price, or None.

    The first-page table listed only operating and terminal inputs, and the writer read it as the
    full account of what can reach the price; the implied return is another route (rule M4 in the
    Walmart, Microsoft, Apple and Coca-Cola reviews, 2026-11-27).
    """
    implied=(result or {}).get('implied_return_at_price')
    if not isinstance(implied,dict):
        return None
    row=(implied.get('probability_weighted_value') if not _base_headline(result) else None) or \
        (implied.get('scenarios') or {}).get('base') or implied.get('probability_weighted_value')
    if not isinstance(row,dict):
        return None
    required,solved=row.get('required_return_flat_equivalent'),row.get('implied_return')
    house=f'{required:.1%} house-equivalent' if _finite_number(required) else 'n/a'
    if _finite_number(solved):
        needed=f'{solved:.1%}'
    elif len(row.get('implied_return_solutions') or [])>1:
        needed='several rates: '+', '.join(f'{rate:.1%}' for rate in row['implied_return_solutions'])
    else:
        bound=row.get('price_bound') if isinstance(row.get('price_bound'),dict) else {}
        low,high=(bound.get('search_range') or (None,None))[:2] if bound.get('search_range') else (None,None)
        span=f'{low:.1%} to {high:.0%}' if _finite_number(low) and _finite_number(high) else ''
        if bound.get('price_position')=='above_highest_value':
            edge=f'highest value {style.per_share(bound["highest_value"])} at {bound["rate_at_highest_value"]:.1%}'
        elif bound.get('price_position')=='below_lowest_value':
            edge=f'lowest value {style.per_share(bound["lowest_value"])} at {bound["rate_at_lowest_value"]:.1%}'
        else:
            edge=''
        inside='; '.join(part for part in (span,edge) if part)
        needed='not reached within the range tested'+(f' ({inside})' if inside else '')
    return ['Discount rate, one flat rate',house,needed]


def memo_title(plan, result, sources=(), ticker=''):
    """'Apple Inc. (AAPL): intrinsic value, 2026-11-27'."""
    name=''
    for source in sources or ():
        title=str((source or {}).get('title') or '') if isinstance(source,dict) else ''
        if (source or {}).get('kind')=='sec_facts' and ':' in title:
            name=title.split(':',1)[1].strip()
            break
    if name and name.upper()==name:
        suffixes={'CO':'Co.','CORP':'Corp.','INC':'Inc.','LTD':'Ltd.','PLC':'plc','LLC':'LLC','NV':'N.V.','SA':'S.A.',
                  'AG':'AG','SE':'SE','LP':'L.P.'}
        name=' '.join(suffixes.get(word.strip('.,'),word.capitalize()) for word in name.split())
    ticker=str(ticker or '').upper()
    company=(f'{name} ({ticker})' if name and ticker else name or ticker or 'Company')
    when=(result or {}).get('valuation_date') or (plan or {}).get('valuation_date')
    return f'# {company}: intrinsic value'+(f', {when}' if when else '')


def _beside_price(value, price, style, bare=False):
    """A per-share value at three figures, or to the cent where rounding would make it read equal to the price or on its other side."""
    text=memo_style.bare_per_share(value) if bare else style.per_share(value)
    if _finite_number(value) and _finite_number(price) and value!=price:
        shown=float(memo_style.bare_per_share(abs(value)).replace(',',''))*(1 if value>=0 else -1)
        if (value<price)!=(shown<price) or shown==price:
            text=f'{value:,.2f}' if bare else style.price(value)
    return text


def _ratio_text(ratio):
    """Value / price to two decimals; three where two would read as exactly 1.00."""
    text=f'{ratio:.2f}'
    return f'{ratio:.3f}' if text=='1.00' and ratio!=1 else text


def _fiscal_span(first, last):
    """'FY2023-25' for fiscal years ending 2023 to 2025, 'FY2025' for one year."""
    start,end=str(first or '')[:4],str(last or '')[:4]
    if not (start.isdigit() and end.isdigit()):
        return 'the years shown'
    return f'FY{end}' if start==end else f'FY{start}-{end[2:]}'


def _history_growth(sources):
    """(compound annual revenue growth, span) over the reported years, or None."""
    rows=[row for row in _baseline_rows(sources) if _finite_number(row.get('revenue')) and row['revenue']>0]
    if len(rows)<3:
        return None
    first,last=rows[0],rows[-1]
    rate=(last['revenue']/first['revenue'])**(1/(len(rows)-1))-1
    return rate,_fiscal_span(first.get('fiscal_year_end'),last.get('fiscal_year_end'))


def _reported_figure(row, result, plan, sources):
    """The best reported figure beside one price-requirement input, or '' when the run has none."""
    name=row.get('input')
    parts=[]
    if name=='revenue_growth':
        growth=_history_growth(sources)
        if growth:
            parts.append(f'{memo_style.pct(growth[0])} a year, {growth[1]}')
        ranks=_requirement_percentiles(result,row)
        if ranks:
            key,house,needed=ranks
            if house:
                parts.append(f'base-rate percentile {house}'+(f' (needed {needed})' if needed else ''))
    elif name=='ebit_margin':
        cost=result.get('cost_path_continuity') or {}
        peak,years=cost.get('max_operating_margin'),cost.get('years')
        if _finite_number(peak):
            parts.append(f'{memo_style.pct(peak)} '+(f'{NUMBER_WORDS.get(years,years)}-year ' if isinstance(years,int) else '')+
                         'peak operating margin')
        latest=cost.get('latest_run_rate') if isinstance(cost.get('latest_run_rate'),dict) else {}
        if _finite_number(latest.get('operating_margin')):
            kind='twelve months' if latest.get('basis')=='trailing_twelve_months' else 'year'
            end=(latest.get('period') or {}).get('end_date')
            parts.append(f'{memo_style.pct(latest["operating_margin"])} in the {kind} to {end}')
    elif name=='terminal_roic':
        reference=(plan or {}).get('terminal_return_reference') or {}
        median,years=reference.get('median_return_on_capital'),reference.get('years')
        if _finite_number(median):
            parts.append(f'{memo_style.pct(median)} '+(f'{NUMBER_WORDS.get(years,years)}-year ' if isinstance(years,int) else '')+
                         'median ROIC')
    elif name=='sales_to_capital':
        reinvestment=result.get('reinvestment_continuity') or {}
        ratio,years=reinvestment.get('incremental_sales_to_capital'),reinvestment.get('years')
        end=str(reinvestment.get('fiscal_year_end') or '')
        if _finite_number(ratio) and isinstance(years,int) and end[:4].isdigit():
            parts.append(f'{ratio:.2f}, {_fiscal_span(str(int(end[:4])-years+1),end)}')
    return '; '.join(parts)


def _cannot_reach_text(row, result, style):
    """How an input that cannot reach the price alone is named on the first page, in plain words."""
    label=_lower_first(REQUIREMENT_LABELS.get(row.get('input'),row.get('label','input')))
    effect=_growth_effect(result,row,style)
    if effect:
        if effect[0]=='does not change the value':
            return (f'{label}, because new capital earns only its cost after year 10, so growth leaves the value '
                    'unchanged')
        return (f'{label}, because new capital earns only its cost after year 10, so growth adds value only through '
                f'the R&D add-back ({style.per_share(row.get("closest_value"))} at the limit tested)')
    closest=row.get('closest_value')
    return label+(f' ({style.per_share(closest)} at the limit tested)' if _finite_number(closest) else '')


def _price_needs(plan, result, style, sources):
    """(table lines, sentence on the inputs that cannot reach the price) for the first page."""
    requirements=(result.get('price_requirements') or {}).get('rows') or []
    body,cannot=[],[]
    for row in requirements:
        label,house,needed=_requirement_cells(row,result,style)
        reported=_reported_figure(row,result,plan,sources)
        if row.get('status')=='solved' and not _growth_effect(result,row,style):
            body.append([label,house,needed,reported])
        elif row.get('status')=='not_reachable' and row.get('input')=='revenue_growth' and _reverse_growth(result):
            rates,caveat,years=_reverse_growth(result)
            text=(f'{memo_style.pct(rates[0])} a year' if len(rates)==1 else
                  'one of '+', '.join(memo_style.pct(rate) for rate in rates)+' a year')
            body.append(['Revenue growth, one rate in every forecast year',f'{house} compound',
                         text+(f', with {caveat}' if caveat else ''),reported])
        elif row.get('status')=='not_reachable':
            cannot.append(_cannot_reach_text(row,result,style))
    rate=_discount_rate_requirement(result,style)
    if rate:
        if rate[2].startswith('not reached'):
            cannot.append('one flat discount rate'+rate[2][len('not reached within the range tested'):])
        else:
            body.append(['Discount rate, one flat rate in every year',rate[1].replace(' house-equivalent',''),rate[2],''])
    lines=[]
    if body:
        header=['Input','Base case','Needed for value = price']
        with_reported=any(row[3] for row in body)
        if with_reported:
            header.append('Best reported figure')
        lines+=['','**What the price needs.** Each input changed alone, the rest held at the base case.','']
        lines+=_table(header,[row if with_reported else row[:3] for row in body],
                      ['---','---:','---']+(['---'] if with_reported else []))
    sentence=None
    if cannot:
        names=cannot[0] if len(cannot)==1 else '; '.join(cannot[:-1])+'; and '+cannot[-1]
        sentence=(f'Cannot reach the price alone: {names}.' if body else
                  f'No single input reaches the price alone: {names}.')
    return lines,sentence


def _exit_multiple_line(result):
    """The terminal convention as an exit multiple: market EV over year-1 EBIT beside the house year-10 multiple."""
    row=(((result.get('computed_diagnostics') or {}).get('valuation_multiples') or {}).get('scenarios') or {}).get('base') or {}
    entry=((row.get('forecast_year1') or {}).get('market_ev_to_ebit') or {})
    exit=(row.get('original_dcf_exit') or {})
    multiple=(exit.get('ev_to_final_forecast_ebit') or {})
    year=(exit.get('period') or {}).get('model_year')
    if not (_finite_number(entry.get('value')) and _finite_number(multiple.get('value')) and isinstance(year,int)
            and entry.get('status','calculated')=='calculated' and multiple.get('status','calculated')=='calculated'):
        return None
    text=(f'**Exit multiple.** The market\'s enterprise value is {entry["value"]:.1f} times base-case year-1 EBIT; the '
          f'base case exits at {multiple["value"]:.1f} times year-{year} EBIT')
    base=((result.get('scenarios') or {}).get('base') or {})
    if _terminal_follows(result):
        return text+f', because after year {year} the method lets new capital earn only its cost.'
    roic,rate=base.get('terminal_roic_applied'),base.get('terminal_rate')
    if _finite_number(roic) and _finite_number(rate):
        shown=memo_style.pct(rate)
        article='an' if shown.startswith(('8','11','18')) else 'a'
        return text+f'; after year {year}, new capital earns {memo_style.pct(roic)} against {article} {shown} discount rate.'
    return text+'.'


def _dated_tests(result, count=3):
    """The monitoring tests shown on the first page: the soonest review dates first."""
    rows=[row for row in result.get('monitoring') or [] if isinstance(row,dict) and
          _finite_number(row.get('conditional_per_share_value')) and row.get('threshold')]
    return sorted(rows,key=lambda row:str(row.get('review_by') or '9999'))[:count]


def _threshold_text(row, style):
    text=str(row.get('threshold') or '').strip().rstrip('.')
    text=dollar_amounts(text) if style.code=='USD' else text
    return text+_cite(row.get('source_refs'))


def _index_ratio(result):
    ratio=((result or {}).get('index_benchmark') or {}).get('value_to_price')
    return ratio if _finite_number(ratio) and ratio>0 else None


def _split_brief(prose):
    """(the writer's "## In brief" text or None, the prose without that section)."""
    match=re.search(r'(?ms)^## In brief[ \t]*\n(.*?)(?=^#{1,2} |\Z)',prose or '')
    if not match:
        return None,prose
    brief=match.group(1).strip()
    rest=(prose[:match.start()]+prose[match.end():]).strip('\n')
    return (brief or None),rest


def computed_summary(plan, result, brief=None, sources=()):
    """The first page: the headline sentence, the writer's "In brief" when there is one, then the tables.

    The model writes interpretation around it and never re-types it: a live first
    draft (2026-11-14) mis-copied years, signs and ranges when it rebuilt these
    tables itself. The readability review (2026-11-27) asked for the index ratio
    beside every ratio, the terminal convention as an exit multiple, plain cells,
    the best reported figure beside each input and the dated tests on this page.
    """
    if not result or not result.get('scenarios'):
        return ''
    style=_style(plan,result)
    scenarios=result['scenarios']
    headline=_base_headline(result)
    dates=(plan or {}).get('input_dates',{}) if isinstance((plan or {}).get('input_dates'),dict) else {}
    quoted=((dates.get('market_price') or {}).get('observed_at') or '')[:10]
    price=result.get('market_price')
    index=_index_ratio(result)
    lead=['## Summary']
    values={name:(scenarios.get(name) or {}).get('per_share_value') for name in ('bear','base','bull')}
    shown=values['base'] if headline else result.get('weighted_per_share_value')
    def value(amount):
        return _beside_price(amount,price,style)
    def bare(amount):
        return _beside_price(amount,price,style,bare=True)
    if headline:
        sentence=f'The base-case value is {value(values["base"])} a share'
        ratio=(scenarios.get('base') or {}).get('value_to_price')
    else:
        sentence=f'The probability-weighted value is {value(shown)} a share'
        ratio=result.get('weighted_value_to_price')
    if _finite_number(price):
        # The quote's source and session basis (final Walmart review, 2026-11-27).
        basis=str((dates.get('market_price') or {}).get('basis') or '')
        refs=((plan or {}).get('source_refs') or {}).get('market_price') or []
        refs=refs if isinstance(refs,list) else [refs]
        session='regular-session ' if 'regular-session' in basis else ''
        sentence+=(f' against a {session}price of {style.price(price)}'+(f' on {quoted}' if quoted else '')+
                   (_cite(refs) if refs else '')+
                   (f', a value / price of {_ratio_text(ratio)}' if _finite_number(ratio) else ''))
    sentence+='.'
    if index and _finite_number(ratio):
        # A stylized index against a constructed price; no observed index price is used (Walmart review, 2026-11-27).
        sentence+=(f' The same method values a stylized S&P 500 benchmark at {index:.2f} of its constructed benchmark '
                   f'price, which puts the {"base case" if headline else "weighted value"} at {ratio/index:.2f} times '
                   'the benchmark.')

    lead+=['',sentence]
    if brief:
        lead+=['',brief.strip()]
    lead+=['']
    relative=f'Relative to the stylized S&P 500 benchmark at {index:.2f}' if index else None
    header=(['Scenario']+([] if headline else ['Probability'])+[f'Value per share ({style.unit("")})','Value / price']+
            ([relative] if relative else []))
    rows=[]
    def ratios(value_to_price):
        cells=[_ratio_text(value_to_price) if _finite_number(value_to_price) else 'n/a']
        if relative:
            cells.append(f'{value_to_price/index:.2f}' if _finite_number(value_to_price) else 'n/a')
        return cells
    for name in ('bear','base','bull'):
        row=scenarios[name]
        rows.append([name.title()]+([] if headline else [f'{row.get("probability",0):.0%}'])+
                    [bare(row['per_share_value'])]+ratios(row.get('value_to_price')))
    if not headline:
        rows.append(['Weighted','100%',bare(result.get('weighted_per_share_value'))]+
                    ratios(result.get('weighted_value_to_price')))
    lines=lead+_table(header,rows)
    exit_line=_exit_multiple_line(result)
    if exit_line:
        lines+=['',exit_line]
    table,cannot=_price_needs(plan,result,style,sources)
    lines+=table
    if cannot:
        lines+=['',cannot]
    tests=_dated_tests(result)
    if tests:
        u=style.unit('')
        lines+=['','**Dated tests.** Each value applies the test\'s forecast changes to the base case.','']
        lines+=_table(['Review by','Observation',f'Value per share ({u})',f'Change ({u})'],
                      [[row.get('review_by') or 'n/a',_threshold_text(row,style),
                        memo_style.bare_per_share(row['conditional_per_share_value']),
                        _change(row['conditional_per_share_value'],row.get('base_per_share_value'))
                        if _finite_number(row.get('base_per_share_value')) else 'n/a'] for row in tests],
                      ['---','---','---:','---:'])
    top=_top_conventions(result,2)
    if top:
        parts=[f'{_lower_first(_panel_label(row,result))} ({style.per_share(row["per_share_value"])})' for row in top]
        prefix='terminal return on new capital of '
        if len(parts)==2 and all(part.startswith(prefix) for part in parts):
            parts=[parts[0],parts[1][len(prefix):]]
        lines+=['',('Of the method\'s conventions, two move the value most: ' if len(parts)==2 else
                    'The convention that moves the value most: ')+' and '.join(parts)+'.']
    return memo_style.compact('\n'.join(lines)+'\n',style)


def _lower_first(text):
    """'Terminal return' as 'terminal return'; 'EBIT margin' and 'R&D' keep their capitals."""
    text=str(text or '')
    return text if text[1:2].isupper() or text[1:2]=='&' else text[:1].lower()+text[1:]


def _transition_step(result, money):
    """The base case's cash-flow step from the last forecast year to the continuing value, in two sentences.

    Reviews of the live Apple and Coca-Cola memos (2026-11-27) asked for the step that
    explains the largest convention row: under the house steady state reinvestment rises
    to terminal growth over the return on new capital, so free cash flow roughly halves.
    """
    diagnostics=(result.get('computed_diagnostics') or {}).get('terminal_transition') or {}
    row=diagnostics.get('base') if isinstance(diagnostics,dict) else None
    base=(result.get('scenarios') or {}).get('base') or {}
    if not isinstance(row,dict):
        return None
    last,terminal,percent=row.get('last_forecast_fcff'),row.get('terminal_fcff'),row.get('fcff_step_percent')
    share,roic=base.get('terminal_reinvestment_rate'),row.get('terminal_roic')
    if not all(_finite_number(x) for x in (last,terminal,percent,share,roic)):
        return None
    flows=[flow for flow in base.get('cashflows') or [] if flow.get('phase','explicit')=='explicit']
    year=flows[-1] if flows else {}
    reinvestment,nopat=year.get('net_reinvestment'),year.get('nopat')
    if _finite_number(reinvestment) and _finite_number(nopat) and nopat>0 and _finite_number(base.get('terminal_reinvestment')):
        text=(f'Base-case free cash flow is {money(last)} in year {year.get("year")} and {money(terminal)} in the continuing '
              f'value, a change of {percent:+.1f}%. Reinvestment rises from {money(reinvestment)}, {reinvestment/nopat:.0%} '
              f'of year-{year.get("year")} NOPAT, to {money(base["terminal_reinvestment"])}, {share:.0%} of continuing-value '
              f'NOPAT, which is terminal growth divided by the {roic:.1%} return on new capital.')
        if _rnd_addback(result):
            text+=f' Continuing-value NOPAT includes the {money(_rnd_addback(result))} net R&D add-back.'
        return text
    return (f'Base-case free cash flow is {money(last)} in the last forecast year and {money(terminal)} in the continuing '
            f'value, a change of {percent:+.1f}%. Reinvestment rises to {share:.0%} of NOPAT, which is '
            f'terminal growth divided by the {roic:.1%} return on new capital.')


def _implied_return_sentence(row, which, money):
    """What one flat rate says about the price, including when no rate in the searched range gives it.

    The summary sentence was dropped without explanation when no rate solved (Apple
    restatement, Druckenmiller critique F5, 2026-11-26).
    """
    implied,required=row.get('implied_return'),row.get('required_return_flat_equivalent')
    house=(f' The house value is reproduced by one flat rate of {required:.1%}.'
           if _finite_number(required) else '')
    if _finite_number(implied) and _finite_number(required):
        gap=row['gap_percentage_points']
        return (f'One flat discount rate of {implied:.1%} sets the {which} value equal to the price. The house value is '
                f'reproduced by one flat rate of {required:.1%}, {abs(gap):.1f} points {"higher" if gap>=0 else "lower"}.')
    solutions=row.get('implied_return_solutions') or []
    if len(solutions)>1:
        return ('More than one flat discount rate sets the '+which+' value equal to the price: '+
                ', '.join(f'{rate:.2%}' for rate in solutions)+'. No single implied rate is reported.'+house)
    bound=row.get('price_bound')
    if isinstance(bound,dict):
        low,high=bound['search_range']
        if bound['price_position']=='above_highest_value':
            return (f'No flat discount rate between {low:.2%} and {high:.0%} reaches the price. The highest {which} value in '
                    f'that range is {money(bound["highest_value"])}, at {bound["rate_at_highest_value"]:.2%}.'+house)
        if bound['price_position']=='below_lowest_value':
            return (f'No flat discount rate between {low:.2%} and {high:.0%} reaches the price. The lowest {which} value in '
                    f'that range is {money(bound["lowest_value"])}, at {bound["rate_at_lowest_value"]:.2%}.'+house)
    return None


# What every rate test does to the terminal return, checked against valuation._project: each
# rate-changing call keeps terminal_roic_follows_rate, so the return on new capital is the tested
# terminal rate (memo audit, 2026-11-27; a review read the old wording as a held return).
RATE_TEST_RULE = ('Every rate test moves the terminal return on new capital with the terminal rate it tests: the whole-path shifts, the Treasury-rate and monthly rows, the rate methods, the beta checks, the country-risk row, the constant-rate and Damodaran-default rows and the implied return.')
ASSUMPTION_REWRITES = (
    ('The rate alternatives keep this rule.', RATE_TEST_RULE),
    (' are house forecasts, not reported facts or company guidance.', ' are house forecasts.'),
    ('Omitted investment categories are not reported zeros. ', 'An omitted investment category counts as zero by assumption. '),
    ('; an absent concept is not a reported zero, and undisclosed borrowings would lower equity value',
     '. Undisclosed borrowings would lower equity value'),
    ('Additional unidentified claims are assumed zero, not established absent.', 'Unidentified claims are assumed zero.'),
    ('Terminal return on new capital equals the terminal discount rate (Mauboussin\'s steady state): the 10-year forecast '
     'is the house horizon for excess returns, after which new investment earns its cost of capital, so growth beyond it '
     'adds no value; rate alternatives keep this.',
     'Terminal return on new capital equals the terminal discount rate, Mauboussin\'s steady state. The 10-year forecast '
     'is the house horizon for excess returns. After year 10, new investment earns its cost of capital, so growth adds no value. '+
     RATE_TEST_RULE),
)


def _nonoperating_notes(plan, result):
    """What the bridge treats as non-operating, generated from the bridge (re-audit, 2026-11-27)."""
    style=_style(plan,result)
    cash=((plan or {}).get('net_debt_bridge') or {}).get('selected_cash_and_nonoperating_investments')
    added=(((result or {}).get('scenarios') or {}).get('base') or {}).get('nonoperating_assets')
    notes=[]
    if _finite_number(cash):
        notes.append(f'Selected cash and short-term investments ({style.amount(cash)}) are netted in net debt.')
    if _finite_number(added) and added>0:
        notes.append(f'The investments listed in the bridge ({style.amount(added)}) are added separately.')
    notes.append('Nothing else is treated as non-operating.')
    return notes


def _rate_test_note(result):
    """Which of this memo's rate tests move the terminal rate (and the terminal return with it) and which do not."""
    rows={row.get('name'):row for row in ((result or {}).get('convention_panel') or {}).get('rows') or []
          if row.get('status') in ('calculated','fragile_spread')}
    house=(((result or {}).get('scenarios') or {}).get('base') or {}).get('terminal_rate')
    def same(rate):
        return _finite_number(rate) and _finite_number(house) and abs(rate-house)<1e-9
    moving,still=[],[]
    if any(name.startswith('required_return_') for name in rows):
        moving.append('the whole-path shifts')
    if any(name.startswith('treasury_rate_') for name in rows):
        moving.append('the Treasury-rate rows')
    if any(name.startswith('rate_history_') for name in rows):
        moving.append('the month rows')
    if 'constant_initial_rate' in rows:
        moving.append('the current-rate row')
    if 'damodaran_default_terminal_rate' in rows:
        moving.append('the risk-free-plus-premium row')
    if 'country_risk_premium_revenue_weighted' in rows:
        moving.append('the country-risk row')
    for key,name in (('calibration_comparisons','the rate methods'),('beta_cross_checks','the beta checks')):
        tests=[row for row in (result or {}).get(key) or [] if _finite_number(row.get('terminal_rate'))]
        if tests:
            (still if all(same(row['terminal_rate']) for row in tests) else moving).append(name)
    if (result or {}).get('implied_return_at_price'):
        moving.append('the implied return')
    def words(items):
        return items[0] if len(items)==1 else ', '.join(items[:-1])+' and '+items[-1]
    text=''
    if moving:
        text=f'In {words(moving)}, the terminal return on new capital moves with the terminal rate the test sets.'
    if still:
        text+=(' ' if text else '')+(words(still)[:1].upper()+words(still)[1:]+
                                     ' leave the terminal rate, and so the terminal return, unchanged.')
    return text


def _assumption_notes(label, result=None, plan=None):
    """The plan's basis statement as one note per sentence, in the memo's voice, each sentence true for this run."""
    text=' '.join(str(label or '').split())
    for old,new in ASSUMPTION_REWRITES:
        text=text.replace(old,new)
    sentences=[part.strip() for part in re.split(r'(?<=[.])\s+(?=[A-Z])',text) if part.strip()]
    addback=_rnd_addback(result)
    has_rnd=bool(addback) or any(any((scenario.get('rnd_share_path') or [])) for scenario in
                                 ((plan or {}).get('scenarios') or {}).values() if isinstance(scenario,dict))
    excluded=(((plan or {}).get('net_debt_bridge') or {}).get('cash_coverage') or {}).get('excluded_reported_categories')
    out=[]
    for sentence in sentences:
        if excluded and sentence=='An omitted investment category counts as zero by assumption.':
            continue
        if sentence==RATE_TEST_RULE and result is not None:
            sentence=_rate_test_note(result)
            if not sentence:
                continue
        if sentence.startswith('Only the cash and investment assets selected in the pinned bridge') and result is not None:
            out+=_nonoperating_notes(plan,result)
            continue
        # The panel, the headline and the stub are stated by their own notes; the panel sentence's fragments go too.
        if sentence.startswith(('The convention panel shows','The base case is the headline value',
                                'Present value is dated at the valuation date','The earlier rule is','The panel also shows')):
            continue
        if sentence.startswith('Growth, margins, taxes, discount rates, capital efficiency, terminal returns'):
            # Forecasts, computed rates and conventions are different things (computed-text audit, 2026-11-27).
            weights='scenario weights' in sentence
            out+=['Growth, margins, taxes and capital efficiency in every scenario'+(' and the scenario weights' if weights
                                                                                    else '')+' are house forecasts.',
                  'Discount rates are computed from dated market inputs and proxy choices.',
                  'The terminal return on new capital is a house convention.']
            continue
        if sentence=='GAAP EBIT retains operating rent, SBC and R&D.' and not has_rnd:
            sentence='GAAP EBIT retains operating rent and SBC.'
        if sentence=='After year 10, new investment earns its cost of capital, so growth adds no value.' and addback:
            sentence=('After year 10, new investment earns its cost of capital, so growth adds value only through the net '
                      'R&D add-back.')
        out.append(sentence)
    return out


# Claim components in the order the bridge prints them.
CLAIM_LABELS = (('noncontrolling_interests', 'Less noncontrolling interests'),
                ('redeemable_noncontrolling_interests', 'Less redeemable noncontrolling interests'),
                ('preferred_stock', 'Less preferred stock'),
                ('defined_benefit_obligations', 'Less unfunded pension obligations, before tax'))
NONOPERATING_LABELS = {'long_term_investments': 'long-term investments', 'securities_noncurrent': 'noncurrent securities',
                       'equity_method_investments': 'equity-method investments',
                       'other_long_term_investments': 'other long-term investments',
                       'equity_securities_nonmarketable': 'non-marketable equity securities',
                       'equity_securities_marketable': 'marketable equity securities'}


def _zero_basis_words(basis):
    """A zero line's basis in the memo's voice: 'no concept reported on the date, zero by assumption'."""
    text=re.sub(r'^assumption:\s*','',str(basis or '')).strip().rstrip('.')
    if text in ('','none reported'):
        return 'none reported'
    text=re.sub(r';\s*zero(?:, not a reported fact| by assumption)$',', zero by assumption',text)
    text=re.sub(r'^zero by assumption;\s*(.*)$',r'\1, zero by assumption',text)
    return text


def _context_table(sources):
    """(the SEC company-facts context rows, the source id) of the first source that has them."""
    for source in sources or ():
        data=source.get('data') if isinstance(source,dict) and isinstance(source.get('data'),dict) else {}
        table=data.get('financial_context_table')
        if isinstance(table,list) and table:
            return table,source.get('id')
    return [],None


def _bridge_concepts(item, anchor):
    """The XBRL concepts a bridge component was read from on the bridge date."""
    found=[]
    def walk(value):
        if isinstance(value,dict):
            if isinstance(value.get('concept'),str) and value.get('instant') in (anchor,None):
                found.append(value['concept'])
            for child in value.values():
                walk(child)
        elif isinstance(value,list):
            for child in value:
                walk(child)
    walk(item)
    return list(dict.fromkeys(found))


BRIDGE_GROUPS = (('net_debt_bridge','debt_components','Debt'),
                 ('net_debt_bridge','cash_components','Selected cash and investments'),
                 ('nonoperating_assets_bridge','components','Non-operating investments'))


def _later_balances(plan, sources, anchor, until, shares=None):
    """Balances the run holds for dates after the bridge, summed on the bridge's own concepts.

    Returns [(label, later value, later date, bridge value, source id)] for amounts, and the
    later share count as ('shares', count, date, bridge count, source id). A group is shown only
    when every component it sums is reported on one later date, so the later figure is like for like.
    """
    table,source_id=_context_table(sources)
    if not table or not anchor:
        return []
    values={}
    for row in table:
        if (isinstance(row,dict) and row.get('status')=='reported' and _finite_number(row.get('value')) and
                isinstance(row.get('instant'),str) and anchor<row['instant']<=str(until or '9999') and
                not row.get('dimensions')):
            values[(row.get('concept'),row['instant'])]=row['value']
    out=[]
    for bridge_name,group,label in BRIDGE_GROUPS:
        components=((plan or {}).get(bridge_name) or {}).get(group)
        if not isinstance(components,dict) or not components:
            continue
        parts=[]
        for item in components.values():
            if not isinstance(item,dict) or not _finite_number(item.get('value')):
                parts=None
                break
            concepts=_bridge_concepts(item,anchor)
            if len(concepts)!=1:
                parts=None
                break
            parts.append((concepts[0],item['value']))
        if not parts:
            continue
        dates=sorted({instant for (concept,instant) in values if concept in {name for name,_ in parts}},reverse=True)
        for date in dates:
            if all((concept,date) in values or amount==0 for concept,amount in parts):
                later=sum(values.get((concept,date),0) for concept,_ in parts)
                bridge=sum(amount for _,amount in parts)
                if _material_change(later,bridge):
                    out.append((label,later,date,bridge,source_id))
                break
    # The balance-sheet count first, the cover-page count where the balance sheet has none.
    for concept in ('us-gaap:CommonStockSharesOutstanding','dei:EntityCommonStockSharesOutstanding'):
        dates=sorted((instant for (name,instant) in values if name==concept),reverse=True)
        if dates and _finite_number(shares):
            if _material_change(values[(concept,dates[0])],shares):
                out.append(('shares',values[(concept,dates[0])],dates[0],shares,source_id))
            break
    return out


def _material_change(later, bridge):
    """True when a later balance differs from the bridge by at least 1%: smaller moves are noise beside the date."""
    return abs(later-bridge)>=max(1.0,abs(bridge)*.01)


def _stale_bridge_rows(plan, result, style, sources, scale, decimals):
    """Bridge rows for a balance sheet struck well before the valuation date, or []."""
    timing=((result.get('discounting') or {}).get('timing') or {})
    anchor,when,elapsed=timing.get('anchor_date'),timing.get('valuation_date'),timing.get('elapsed_days')
    if not (anchor and when and isinstance(elapsed,(int,float)) and elapsed>=STALE_BRIDGE_DAYS):
        return []
    rows=[['Balance-sheet date',str(anchor),f'{int(elapsed)} days before the valuation date, {when}']]
    base=(result.get('scenarios') or {}).get('base') or {}
    first=next(iter(base.get('cashflows') or []),{})
    stub,shares=timing.get('stub_years'),base.get('diluted_shares')
    if _finite_number(first.get('fcff')) and _finite_number(stub) and stub>0:
        earned=first['fcff']*stub
        each=(f', {"-" if earned<0 else ""}{style.price(abs(earned/shares))} a share' if _finite_number(shares) and shares else '')
        rows.append(['Forecast year-1 free cash flow for the elapsed period, left out',memo_style.scaled(earned,scale,decimals),
                     f'{stub:.0%} of forecast year-1 free cash flow{each}; in neither the cash flows nor the bridge. '
                     'Cash actually generated since the balance-sheet date is not reconciled'])
    for label,later,date,bridge,source_id in _later_balances(plan,sources,anchor,when,base.get('shares_outstanding')):
        cite=_cite(source_id) if source_id else ''
        if label=='shares':
            rows.append([f'Later balance: shares outstanding on {date} (M)',f'{later/1e6:,.1f}',
                         f'the bridge uses {bridge/1e6:,.1f}M on {anchor}{cite}'])
        else:
            rows.append([f'Later balance: {_lower_first(label)} on {date}',memo_style.scaled(later,scale,decimals),
                         f'the bridge uses {style.amount(bridge)} on {anchor}{cite}'])
    return rows


def _equity_bridge_parts(plan, result, style, sources=None):
    """Base-case enterprise value to value per share, each line with its source and basis: (lines, notes).

    A bridge struck well before the valuation date ends with the elapsed period as rows: the date,
    the free cash flow of the elapsed part of year 1 and the later balances the run holds
    (readability review, 2026-11-27: the staleness sat 300 lines lower in a method note).
    """
    base=(result.get('scenarios') or {}).get('base') or {}
    if not _finite_number(base.get('enterprise_value')) or not _finite_number(base.get('per_share_value')):
        return [],[]
    refs=(plan or {}).get('source_refs') or {}
    dates=(plan or {}).get('input_dates') or {}
    claims=(plan or {}).get('other_claims_bridge') or {}
    assets=(plan or {}).get('nonoperating_assets_bridge') or {}
    shares=(plan or {}).get('share_count_bridge') or {}
    day=((dates.get('net_debt') or {}).get('instant')) or 'the balance-sheet date'
    unit,scale,decimals=memo_style.table_scale([base['enterprise_value'],base.get('equity_value')])
    amounts=[]
    def money(value):
        # The raw amount is kept beside each money line, so a line rounds to 0 only when it is not exactly zero.
        amounts.append(value or 0)
        text=memo_style.scaled(value or 0,scale,decimals)
        # A line too small for the table's unit carries its own: $30.0M in a $B bridge.
        return style.amount(value) if text=='0' and value else text
    rows=[['Enterprise value',money(base['enterprise_value']),'present value of base-case free cash flow']]
    if _finite_number(base.get('net_debt')) and base['net_debt']<0:
        rows.append(['Plus net cash',money(-base['net_debt']),f'selected cash and investments less debt on {day}'+_cite(refs.get('net_debt'))])
    else:
        rows.append(['Less net debt',money(base.get('net_debt')),f'debt less selected cash and investments on {day}'+_cite(refs.get('net_debt'))])
    components=claims.get('components') if isinstance(claims.get('components'),dict) else None
    notes=[]
    if components is not None:
        lease=claims.get('finance_lease_claim',0) or 0
        lease_refs=[fact.get('source_id') for fact in claims.get('source_facts') or [] if isinstance(fact,dict)]
        rows.append(['Less finance leases',money(lease),
                     'within net debt' if claims.get('finance_leases_included_in_net_debt') else
                     'reported finance lease liabilities'+_cite(lease_refs)
                     if claims.get('reported_finance_lease_liabilities') is not None else 'none reported'])
        # Each zero line says why it is zero: the data side records the basis (no concept, or plan-level values unread).
        zero_basis={entry.get('line'):re.sub(r'^assumption:\s*','',str(entry.get('basis') or '')).strip()
                    for entry in claims.get('components_assumed_zero') or [] if isinstance(entry,dict)}
        for name,label in CLAIM_LABELS:
            item=components.get(name)
            if not isinstance(item,dict):
                rows.append([label,money(0),zero_basis.get(name) or 'none reported'])
                continue
            if name=='preferred_stock' and item.get('carrying_value_not_deducted') is not None:
                rows.append([label,money(0),'counted through conversion shares'+_cite(item.get('source_refs'))])
                notes.append(_Specific(f'Preferred stock with a carrying value of {style.amount(item["carrying_value_not_deducted"])} '
                             'is counted through conversion shares in the dilutive increment.'))
            elif name=='defined_benefit_obligations':
                rows.append([label,money(item['value']),'underfunded plans'+_cite(item.get('source_refs'))])
                if item.get('overfunded_plan_assets'):
                    notes.append(_Specific(f'Overfunded plan assets of {style.amount(item["overfunded_plan_assets"])} are not credited.'))
            else:
                rows.append([label,money(item['value']),'carrying value'+_cite(item.get('source_refs'))])
    else:
        rows.append(['Less other equity claims',money(base.get('other_equity_claims')),
                     'finance leases and identified claims'+_cite(refs.get('other_equity_claims'))])
    added=assets.get('components') if isinstance(assets.get('components'),dict) else {}
    if 'nonoperating_assets' in ((plan or {}).get('actuals') or {}):
        names=(', '.join(NONOPERATING_LABELS.get(name,name.replace('_',' ')) for name in added) if added else
               'none outside the selected cash' if assets.get('not_added') else 'none reported')
        rows.append(['Plus non-operating investments',money(base.get('nonoperating_assets',0)),
                     names+_cite(refs.get('nonoperating_assets'))])
    rows.append(['Equity value',money(base['equity_value']),''])
    def count(value):
        return f'{(value or 0)/1e6:,.1f}'
    rows.append(['Shares outstanding (M)',count(base['shares_outstanding']),
                 f'on {(dates.get("shares_outstanding") or {}).get("instant","the reported date")}'+
                 _cite(refs.get('shares_outstanding'))])
    if 'dilutive_shares' in ((plan or {}).get('actuals') or {}):
        period=shares.get('period') or dates.get('dilutive_shares') or {}
        basis=('diluted less basic weighted-average shares, fiscal year to '+str(period.get('end_date','')) if
               shares.get('status')=='reported' else 'reported basic and diluted shares are equal' if
               shares.get('status')=='reported_basic_equals_diluted' else 'none reported')
        rows.append(['Plus dilutive shares (M)',count(base.get('dilutive_shares',0)),basis+_cite(refs.get('dilutive_shares'))])
        rows.append(['Diluted shares (M)',count(base.get('diluted_shares',base['shares_outstanding'])),''])
    rows.append([f'Value per share ({style.unit("")})',memo_style.bare_per_share(base['per_share_value']),''])
    # Lines with nothing reported are named once in a note; the table keeps the lines that carry an amount.
    empty=[row for row,amount in zip(rows,amounts) if amount==0 and not row[0].startswith(('Enterprise','Equity'))]
    rows=[row for row in rows if row not in empty]
    if empty:
        # Grouped by basis, so each reason is stated once: "(no concept reported on the date, zero by assumption)".
        groups={}
        for row in empty:
            basis=_zero_basis_words(row[2])
            groups.setdefault(basis,[]).append(row[0][0].lower()+row[0][1:])
        parts=[]
        for basis,labels in groups.items():
            names=labels[0] if len(labels)==1 else ', '.join(labels[:-1])+' and '+labels[-1]
            parts.append(names+(f' ({basis})' if basis else ''))
        notes.insert(0,'Bridge lines with nothing to add or deduct: '+'; '.join(parts)+'.')
    rows+=_stale_bridge_rows(plan,result,style,sources,scale,decimals)
    lines=['','### Enterprise value to value per share','',f'Base case, {style.unit(unit)} unless the line says otherwise.','']
    lines+=_table(['Line',style.unit(unit),'Basis'],rows,['---','---:','---'])
    quoted=assets.get('reported_quoted_market_value_not_used')
    if isinstance(quoted,dict) and _finite_number(quoted.get('value')):
        notes.append(_Specific(f'The bridge carries the listed equity-method stakes at book value. Their reported quoted market '
                     f'value is {style.amount(quoted["value"])}{_cite(quoted.get("source_refs"))}.'))
    excluded=(((plan or {}).get('net_debt_bridge') or {}).get('cash_coverage') or {}).get('excluded_reported_categories') or []
    for item in excluded:
        if isinstance(item,dict) and _finite_number(item.get('value')):
            reason=str(item.get('reason') or '').rstrip('.')
            if str(item.get('label') or '').endswith('s'):
                reason=re.sub(r'^it lies\b','they lie',reason)
            notes.append(_Specific(f'Reported {item.get("label")} of {style.amount(item["value"])}{_cite(item.get("source_refs"))} '
                         f'are left out of the bridge: {reason}.'))
    if assets or components is not None:
        notes.append('Claims and investments are carrying values on the balance-sheet date of net debt. A category with '
                     'no reported concept is zero by assumption.')
        notes.append('Stock-based compensation stays an operating expense. The dilutive shares count awards already granted.')
    return lines,notes




def _forecast_parts(result, style):
    """The base-case forecast in the table's scale, with the base ROIC as its last column: (lines, notes).

    The separate forecast-ROIC table was for display only (readability review, 2026-11-27); the
    base row lives here and the bear and bull rows go.
    """
    operating=((result.get('operating_forecasts') or {}).get('scenarios') or {}).get('base') or {}
    base_flows=((result.get('scenarios') or {}).get('base') or {}).get('cashflows') or []
    flows=[row for row in (operating.get('cashflows') or base_flows) if row.get('phase','explicit')=='explicit']
    if not flows:
        return [],[]
    unit,scale,decimals=memo_style.table_scale([row.get('revenue') for row in flows])
    by_year={row.get('year'):row for row in base_flows}
    returns=(result.get('forecast_return_on_capital') or {})
    roic={row.get('year'):row.get('return_on_invested_capital') for row in
          (((returns.get('scenarios') or {}).get('base') or {}).get('years') or [])}
    with_roic=any(_finite_number(value) for value in roic.values())
    has_operating=any(row.get('gross_profit') is not None for row in flows)
    u=style.unit(unit)
    header=['Year',f'Revenue ({u})','Growth']+([f'Gross profit ({u})',f'Operating expenses ({u})'] if has_operating else [])
    header+=[f'EBIT ({u})','EBIT margin',f'Net reinvestment ({u})',f'FCFF ({u})']+(['ROIC'] if with_roic else [])
    rows=[]
    for row in flows:
        flow=by_year.get(row.get('year')) or row
        cells=[str(row['year']),memo_style.scaled(row['revenue'],scale,decimals),memo_style.pct(flow.get('revenue_growth'))]
        if has_operating:
            cells+=[memo_style.scaled(row[key],scale,decimals) if row.get(key) is not None else 'not modeled'
                    for key in ('gross_profit','operating_expenses')]
        cells+=[memo_style.scaled(row['ebit'],scale,decimals),memo_style.pct(row.get('ebit_margin')),
                memo_style.scaled(flow.get('net_reinvestment'),scale,decimals),memo_style.scaled(flow.get('fcff'),scale,decimals)]
        if with_roic:
            value=roic.get(row.get('year'))
            cells.append(memo_style.pct(value) if _finite_number(value) else 'n/m')
        rows.append(cells)
    notes=[]
    if has_operating:
        notes.append('Component margins in the operating forecast are forward assumptions.')
    lead=[]
    opening=returns.get('opening_invested_capital')
    if with_roic and _finite_number(opening):
        lead=['',f'ROIC is NOPAT over average invested capital, which starts at {style.amount(opening)} on '
                 f'{returns.get("fiscal_year_end") or "the last balance sheet"}{_cite(returns.get("source_refs"))} and adds '
                 'each year\'s net reinvestment.']
    return ['','### Forecast, base case','']+_table(header,rows)+lead,notes


def _forecast_roic_parts(result, style):
    """Forecast ROIC and 3-year incremental ROIC per scenario, then the return on new capital after the forecast.

    Mauboussin critique F3 (2026-11-26): forecast returns held near 100% for ten
    years and fell to the terminal rate in one, with nothing in the memo showing it.
    """
    returns=result.get('forecast_return_on_capital')
    if not returns or not returns.get('scenarios'):
        return [],[]
    years=max(len(entry['years']) for entry in returns['scenarios'].values())
    def pct(value):
        return memo_style.pct(value) if _finite_number(value) else 'n/m'
    header=['Measure']+[str(year) for year in range(1,years+1)]+['After']
    rows=[]
    for name in ('bear','base','bull'):
        entry=returns['scenarios'].get(name)
        if not entry:
            continue
        rows.append([f'{name.title()} ROIC']+[pct(row['return_on_invested_capital']) for row in entry['years']]+[''])
        rows.append([f'{name.title()} 3-year incremental']+
                    ['n/a' if index<3 else pct(row['incremental_return_3y']) for index,row in enumerate(entry['years'])]+
                    [pct(entry.get('return_on_new_capital_after_forecast'))])
    opening=returns.get('opening_invested_capital')
    refs=_cite(returns.get('source_refs'))
    rnd=any(entry.get('continuing_value_nopat_includes_rnd') for entry in returns['scenarios'].values())
    lines=['','### Forecast return on invested capital','',
           f'Invested capital starts at {style.amount(opening)} at the fiscal year end '
           f'{returns.get("fiscal_year_end") or "of the last report"}{refs} and adds each year\'s net reinvestment. '
           'Columns are forecast years.','']+_table(header,rows)
    notes=['Forecast invested capital is the operating-approach capital computed from the reported balance sheet, '
           'including goodwill and acquired intangibles.',
           'Forecast ROIC is NOPAT over average capital. The 3-year incremental ROIC is the NOPAT change over three years '
           'divided by the capital change over the same three years, because forecast reinvestment pays for the same '
           'year\'s growth. It starts in year 4.'+(' n/m marks years where capital fell.' if any('n/m' in cell for row in rows
                                                                                          for cell in row) else ''),
           'Forecast NOPAT is EBIT after the plan\'s tax rate. The history table uses EBITA after tax, as its ROIC '
           'basis note describes.',
           'The equity bridge treats all selected cash and investments as non-operating. The invested capital behind '
           'ROIC keeps cash up to 2% of revenue as operating, so the two treat that cash differently.',
           'The After column is the return on new capital after the forecast, the terminal return.'+
           (' It is earned on NOPAT and capital that include net R&D investment, which the forecast years expense.' if rnd else ''),
           'The forecast ROIC table is for display; no check uses it.']
    return lines,notes


def _terminal_growth_sentence(result, risk_free):
    """State terminal growth as computed in each scenario."""
    scenarios=result.get('scenarios') or {}
    default=[name for name,row in scenarios.items() if row.get('terminal_growth_source')=='risk_free_rate']
    stated=[(name,row) for name,row in scenarios.items() if row.get('terminal_growth_source')!='risk_free_rate']
    if not stated:
        return f'Terminal growth equals the {risk_free:.2%} risk-free rate in every scenario.'
    parts=[f'{name} uses {row["terminal_growth"]:.2%}'+
           (f' because {str(row["terminal_growth_basis"]).strip().rstrip(".")}' if row.get('terminal_growth_basis') else '')
           for name,row in stated if _finite_number(row.get('terminal_growth'))]
    lead=(f'Terminal growth equals the {risk_free:.2%} risk-free rate in '+', '.join(default)+'. ' if default else
          f'The {risk_free:.2%} risk-free rate is the terminal-growth default. ')
    text='; '.join(parts)
    return lead+text[:1].upper()+text[1:]+'.' if parts else lead.strip()


def _discount_parts(result, style):
    """The rate path, the dated rate inputs and the rate-method comparisons: (lines, notes)."""
    lines,notes=[],[]
    discounting=result.get('discounting') or {}
    if discounting.get('convention')=='glide_to_market_v1':
        path=discounting['base_rate_path']
        lines+=['','### Discount rate','']+_table(['Base case']+[str(year) for year in range(1,len(path)+1)]+['Terminal'],
                                                 [['Discount rate']+[memo_style.pct(rate) for rate in path]+
                                                  [memo_style.pct(discounting['terminal_rate'])]])
        lines+=['',f'The terminal rate uses the market operating-asset beta of {discounting["terminal_unlevered_beta"]:.2f}, '
                   f'relevered at this firm\'s leverage to {discounting["terminal_relevered_beta"]:.2f}. Damodaran\'s '
                   f'spreadsheet default, the risk-free rate plus the equity risk premium, would be '
                   f'{discounting["damodaran_default_terminal_rate"]:.2%}. '+
                   _terminal_growth_sentence(result,discounting['risk_free_rate'])]
    calibration=result.get('discount_rate_calculation')
    if calibration:
        if not lines:
            lines+=['','### Discount rate']
        lines+=['','Dated rate inputs, year 1:','']+_table(
            ['Applied WACC','Cost of equity','Relevered beta','Debt weight'],
            [[memo_style.pct(calibration.get('applied_wacc'),2),memo_style.pct(calibration.get('cost_of_equity'),2),
              memo_style.ratio(calibration.get('relevered_beta')),memo_style.pct(calibration.get('debt_weight'),2)]])
        notes.append('The rate inputs are calculations from dated market inputs and proxy choices. They are not observed '
                     'issuer required returns.')
    comparisons=result.get('calibration_comparisons') or []
    if comparisons:
        house_terminal=((result.get('scenarios') or {}).get('base') or {}).get('terminal_rate')
        house_growth=((result.get('scenarios') or {}).get('base') or {}).get('terminal_growth')
        base_value=((result.get('scenarios') or {}).get('base') or {}).get('per_share_value')
        with_growth=any('terminal_growth' in row for row in comparisons)
        # The last column is shown only when some method moves terminal growth away from the house figure.
        growth_moves=with_growth and any(_finite_number(row.get('terminal_growth')) and _finite_number(house_growth) and
                                         abs(row['terminal_growth']-house_growth)>1e-9 for row in comparisons)
        def terminal_cell(row):
            rate=row.get('terminal_rate')
            same=_finite_number(rate) and _finite_number(house_terminal) and abs(rate-house_terminal)<1e-9
            return memo_style.pct(rate,2)+(', unchanged' if same else '')
        header=['Rate method','Initial rate']+(['Terminal rate','Terminal growth'] if with_growth else [])+[
            f'Base value ({style.unit("")})',f'Change ({style.unit("")})']+(
            [f'Value at house terminal growth ({style.unit("")})'] if growth_moves else [])
        rows=[]
        for row in comparisons:
            name=_plain(row.get('name',''))
            if row.get('per_share_value') is None:
                rows.append([name,memo_style.pct(row.get('wacc'),2)]+(['n/a','n/a'] if with_growth else [])+
                            ['not calculated','']+(['n/a'] if growth_moves else []))
                continue
            rows.append([name,memo_style.pct(row.get('wacc'),2)]+
                        ([terminal_cell(row),memo_style.pct(row.get('terminal_growth'),2)] if with_growth else [])+
                        [memo_style.bare_per_share(row['per_share_value']),
                         _change(row['per_share_value'],base_value) if _finite_number(base_value) else
                         memo_style.bare_change(row['value_change']['dollar_delta'],row['per_share_value'])]+
                        ([memo_style.bare_per_share(row.get('per_share_value_at_house_terminal_growth',row['per_share_value']))]
                         if growth_moves else []))
        moved=any(_finite_number(row.get('terminal_rate')) and _finite_number(house_terminal) and
                  abs(row['terminal_rate']-house_terminal)>1e-9 for row in comparisons)
        lines+=['','Other rate methods change the named rate inputs and the rate path through year 10.'+
                (' The terminal rate changes where the table shows it.' if moved else
                 ' The terminal rate is unchanged in every method shown.'),'']+_table(header,rows)
        if growth_moves:
            notes.append('In the rate-method table, terminal growth that defaults to the risk-free rate moves with the '
                         'method\'s risk-free rate. The last column holds it at the house rate.')
        notes.append('Historical-fit rate endpoints are not valuation confidence intervals.')
    return lines,notes


def _implied_parts(result, style):
    """What the price implies, and the full price-requirement rows: (lines, notes)."""
    lines,notes=[],[]
    implied=result.get('implied_return_at_price')
    headline=_base_headline(result)
    if implied:
        rows=[]
        def cell(value,decimals=1):
            return memo_style.pct(value,decimals) if _finite_number(value) else 'not solved'
        for name,row in implied['scenarios'].items():
            gap=row.get('gap_percentage_points')
            rows.append([name.title(),cell(row.get('implied_return')),cell(row.get('required_return_flat_equivalent')),
                         f'{gap:.1f}' if _finite_number(gap) else 'not solved',
                         cell(row.get('terminal_spread_at_implied_return'))+(' (fragile)' if row.get('fragile') else '')])
        lines+=['','### What the price implies','']+_table(
            ['Scenario','Implied return','Required return','Difference (points)','Terminal spread at the implied return'],rows)
        weighted=implied.get('probability_weighted_value') or {}
        sentence=_implied_return_sentence(weighted,'headline' if headline else 'weighted',style.per_share)
        if sentence:
            lines+=['',sentence]
        for name,row in implied['scenarios'].items():
            if not _finite_number(row.get('implied_return')):
                text=_implied_return_sentence(row,name,style.per_share)
                if text:
                    lines.append('- '+name.title()+': '+text)
        notes+=['The implied return is the one discount rate, used in every year and in the terminal value, at which value '
                'equals the price. The required return is the same flat rate at the house value. Neither is a forecast of '
                'shareholder returns.',
                ('The terminal return follows the solved rate, so the continuing value is NOPAT over that rate. A narrow '
                 'spread between the rate and terminal growth then has no outsized effect, no implied-return row is '
                 'marked fragile, and the search starts at 0.50%, below terminal growth.' if _implied_follows(implied) else
                 'A terminal spread under two points between the implied return and terminal growth marks a fragile '
                 'result.')]
    requirements=(result.get('price_requirements') or {}).get('rows') or []
    if requirements:
        rows=[]
        for row in requirements:
            label=REQUIREMENT_LABELS.get(row.get('input'),row.get('label','input'))
            level=memo_style.ratio if row.get('input')=='sales_to_capital' else memo_style.pct
            if row.get('kind')=='shift':
                house=f'{memo_style.pct(row.get("house"))} in year 1'
                move=row.get('needed') if row['status']=='solved' else row.get('at')
                shifted=memo_style.points(move) if _finite_number(move) else 'n/a'
                path=row.get('needed_path') if row['status']=='solved' else row.get('at_path')
                change=f'{shifted} in every forecast year'+(f', {memo_style.pct(path["year1"])} in year 1' if isinstance(path,dict) else '')
            else:
                capital=row.get('house_sales_to_capital_path')
                house=f'{capital[0]:.2f} to {capital[-1]:.2f}' if capital else level(row.get('house'))
                change=level(row['needed'] if row['status']=='solved' else row.get('at'))
            effect=_growth_effect(result,row,style)
            searched=row.get('searched')
            if effect and isinstance(searched,list) and len(searched)==2:
                change=f'{level(searched[0])} to {level(searched[1])} searched'
            if effect:
                outcome=effect[1]
            elif row['status']=='solved':
                outcome='reaches the price'
            elif row['status']=='not_reachable':
                outcome=(f'not reached within the range tested; closest '
                         f'{_beside_price(row.get("closest_value"),result.get("market_price"),style)}, '
                         f'{"above" if row.get("direction")=="above" else "below"} the price')
            else:
                outcome,change='not calculated',''
            rows.append([label,house,change,outcome])
            reverse=_reverse_growth(result) if row.get('input')=='revenue_growth' else None
            if reverse:
                rates,caveat,years=reverse
                path=row.get('house_path') if isinstance(row.get('house_path'),dict) else {}
                rows.append(['Revenue growth, one constant rate in every forecast year (reverse DCF)',
                             f'{memo_style.pct(path.get("over_forecast"))} compound' if path else 'n/a',
                             ', '.join(f'{memo_style.pct(rate)} a year' for rate in rates),
                             'reaches the price'+(f'; {caveat}' if caveat else '')])
                if row['status']!='solved':
                    at_path=row.get('at_path') if isinstance(row.get('at_path'),dict) else {}
                    notes.append('The two revenue-growth tests differ in what they search. The parallel shift adds the '
                                 'same points to the house growth path and stops at '+
                                 memo_style.points(row.get('at')).replace('.0 points',' points')+' in every forecast year'+
                                 (f', where the path compounds at {memo_style.pct(at_path.get("over_forecast"))}' if at_path
                                  else '')+'; it does not reach the price. The constant-growth reverse DCF sets one rate in '
                                 'every forecast year with margins, capital efficiency and rates held at the base case, and '
                                 'reaches the price at '+' or '.join(memo_style.pct(rate) for rate in rates)+
                                 (f', with {caveat}' if caveat else '')+'.')
        lines+=['','The full price requirement follows. Each row is an alternative, and the inputs interact.','']+_table(
            ['Input','House','Needed, or the limit searched','Result'],rows,['---','---','---','---'])
        notes.append('Price-requirement rows change one base-case input at a time with every other input fixed. Shifts '
                     'apply in every explicit forecast year. A row the search does not reach shows the value at the '
                     'edge of the range tested, which proves nothing beyond it.')
    return lines,notes


def _panel_parts(result, style, plan=None):
    """The convention panel, the horizon and rate-regime exhibits, country risk and beta checks: (lines, notes)."""
    panel=result.get('convention_panel') or {}
    rows=panel.get('rows') or []
    lines,notes=[],[]
    u=style.unit('')
    def header(first):
        return [first,f'Value per share ({u})',f'Change ({u})']
    conventions=[row for row in rows if row.get('kind')=='convention' and row.get('status')!='not_applicable']
    if conventions:
        house=panel.get('baseline_per_share_value') or ((result.get('scenarios') or {}).get('base') or {}).get('per_share_value')
        linked=any(row.get('name')=='terminal_reinvestment_continues_forecast' and row.get('status')=='calculated'
                   for row in conventions) and _rnd_addback(result)
        lines+=['','### Value under other conventions','',
                'Base case, one convention changed at a time'+('; a row that also changes a linked input names it' if linked
                                                              else '')+f'. The house value is {style.per_share(house)}.','']
        lines+=_table(header('Convention changed'),_panel_rows(conventions,style,result))
        step=_transition_step(result,style.amount)
        if step:
            lines+=['',step]
        notes+=['Convention rows are alternatives a careful analyst could defend. Each changes one convention from the '
                'house case and keeps the other inputs; the notes below name any input a row changes with it. They carry '
                'no probability, are not forecasts and are never tuned to the price, and they are never added together.']
        steady=((result.get('scenarios') or {}).get('base') or {}).get('steady_state_years')
        panel_notes=dict(PANEL_NOTES)
        if not steady:
            panel_notes['tax_to_marginal_rate']=('The marginal-tax row taxes the continuing value at the marginal rate, '
                                                 'the Damodaran default.')
        notes+=[_reinvestment_row_note(result,style) if row.get('name')=='terminal_reinvestment_continues_forecast' else
                panel_notes[row['name']] for row in conventions
                if row.get('name') in PANEL_NOTES or row.get('name')=='terminal_reinvestment_continues_forecast']
    skipped=[row for row in rows if row.get('kind')=='convention' and row.get('status')=='not_applicable']
    for row in skipped:
        reason=str(row.get('reason') or '').strip().rstrip('.')
        if row.get('name')=='terminal_return_earlier_house_rule':
            notes.append(_earlier_rule_note(plan,result))
            continue
        notes.append(SKIPPED_NOTES.get(row.get('name')) or
                     f'The row "{_panel_label(row,result)}" does not apply.'+(f' {reason}.' if reason else ''))
    horizons=[row for row in rows if row.get('kind')=='horizon']
    if horizons:
        shown=_panel_rows(horizons,style,result)
        fixed={row.get('horizon_years'):row for row in horizons if row.get('name','').endswith('_years')}
        for index,row in enumerate(horizons):
            same=fixed.get(row.get('horizon_years')) if row.get('name')=='competitive_advantage_period_industry_fade' else None
            if same is not None and _finite_number(same.get('per_share_value')) and _finite_number(row.get('per_share_value')) \
                    and abs(same['per_share_value']-row['per_share_value'])<1e-6:
                shown[index][0]+=f', the same horizon as the {row["horizon_years"]}-year row'
        lines+=['','### Other horizons','']+_table(header('Horizon'),shown)
        notes.append('Horizon rows change only how long the forecast-end margin and capital efficiency last before new '
                     'capital earns the terminal rate (Mauboussin and Callahan, "Competitive Advantage Period", April 2026). '
                     'The 5-year row runs plan years 1-5 and then steady-state years. Longer rows hold the year-10 margin '
                     'and capital efficiency while growth moves in equal steps to terminal growth; the rate path is unchanged.')
        fade=next((row.get('fade') for row in horizons if isinstance(row.get('fade'),dict)),None)
        sectors=(fade or {}).get('sectors') or []
        groups={}
        for item in sectors:
            if _finite_number(item.get('fade_rate')):
                entry=groups.setdefault((item.get('industry_group'),item['fade_rate']),0.0)
                groups[(item.get('industry_group'),item['fade_rate'])]=entry+(item.get('weight') or 0.0)
        if groups:
            notes.append('The industry fade row uses the fade rates in Mauboussin\'s Exhibit 25: '+', '.join(
                f'{group} {rate:.2f}'+(f' at {weight:.0%}' if len(groups)>1 and weight else '')
                for (group,rate),weight in groups.items())+(', at the house beta weights' if len(groups)>1 else '')+
                '. The row extends the year-10 margin and capital efficiency by the weighted average of one over each '
                'industry\'s fade rate, rounded to whole years.')
    regimes=[row for row in rows if row.get('kind') in ('sensitivity','rate_regime')]
    if regimes:
        lines+=['','### Rate regimes','']+_table(header('Rate case'),_panel_rows(regimes,style,result))
        history=panel.get('rate_history') or {}
        if _finite_number(history.get('median_per_share_value')):
            lines+=['',f'The median base value across the {history["calculated"]} monthly Treasury rate and premium pairs '
                       f'from {history["first"][:7]} to {history["last"][:7]} is {style.per_share(history["median_per_share_value"])}.'+
                       (f' {len(history["not_calculated"])} months give no valid rate path.' if history.get('not_calculated') else '')]
        notes.append('Sensitivity rows shift the whole discount-rate path, terminal rate included, and show scale only. '
                     'The terminal return on new capital follows the shifted terminal rate.')
        notes.append('Treasury-rate rows hold the forecast-year cash flows fixed and move the Treasury rate, with the debt cost moving by '
                     'the same amount and the premium held. Month rows use another month\'s Treasury rate and premium. '
                     'In both, the terminal return on new capital follows the new terminal rate, and terminal growth that '
                     'equals the risk-free rate moves with it.')
    extra=[note for note in panel.get('exhibit_notes') or [] if note]
    notes+=extra
    country=[row for row in rows if row.get('kind')=='country_risk']
    table,country_notes=country_risk.render_parts(panel)
    if country or table:
        lines+=['','### Country risk','']
        if country:
            lines+=_table(header('Adjustment'),_panel_rows(country,style,result))
        if table:
            lines+=['']+table
        notes+=country_notes
    checks=result.get('beta_cross_checks') or []
    methods=[row for row in result.get('calibration_comparisons') or [] if _finite_number(row.get('wacc'))]
    # A beta check whose rate equals a rate-method row is the same test: it is shown once, in the rate-method table.
    duplicate=[row for row in checks if _finite_number(row.get('wacc')) and
               any(abs(row['wacc']-method['wacc'])<1e-5 for method in methods)]
    checks=[row for row in checks if row not in duplicate]
    if duplicate:
        notes.append('The regression-beta check equals the historical-beta rate method, so it is shown once.')
    if checks:
        calculation=result.get('discount_rate_calculation') or {}
        house_beta=(((calculation.get('provenance') or {}).get('unlevered_beta') or {}).get('value'))
        base_value=((result.get('scenarios') or {}).get('base') or {}).get('per_share_value')
        body=[['House beta',memo_style.ratio(house_beta),memo_style.pct(calculation.get('applied_wacc'),2),
               memo_style.bare_per_share(base_value),'']]
        for row in checks:
            if row.get('status')=='calculated':
                body.append([_beta_label(row,result.get('beta_cross_checks') or []),memo_style.ratio(row.get('unlevered_beta')),memo_style.pct(row.get('wacc'),2),
                             memo_style.bare_per_share(row['per_share_value']),_change(row['per_share_value'],base_value)])
            else:
                body.append([_beta_label(row),'n/a','n/a','not calculated',''])
            note=_beta_note(row)
            if note:
                notes.append(note)
        lines+=['','### Beta cross-checks','']+_table(['Beta','Unlevered beta','Initial discount rate',
                                                       f'Base value ({u})',f'Change ({u})'],body)
    return lines,notes


REPURCHASE_SCOPES = {'historical_retrospective_conditional': 'Historical buyback, repeated today',
                     'forward_conditional_illustration': 'Forward illustration'}


def _monitoring_parts(result, style):
    lines,notes=[],[]
    u=style.unit('')
    monitoring=result.get('monitoring') or []
    if monitoring:
        rows=[]
        for row in monitoring:
            case=row.get('counterfactual') or row.get('calibration_comparison')
            base_value=row.get('base_per_share_value')
            rows.append([_plain(row['name'])+(f' (case: {_plain(case).lower()})' if case else ''),
                         row.get('review_by',''),
                         (dollar_amounts(row['threshold']) if style.code=='USD' else row['threshold'])+_cite(row.get('source_refs')),
                         memo_style.bare_per_share(row['conditional_per_share_value']),
                         _change(row['conditional_per_share_value'],base_value) if _finite_number(base_value) else
                         memo_style.bare_change(row['value_change']['dollar_delta'],row['conditional_per_share_value'])])
        lines+=['','### Monitoring tests','',
                'Each value is the base case under the overrides of the case the test names. Crossing a threshold does '
                'not change the base value by itself.','']+_table(
            ['Observation','Review by','Threshold',f'Value under the named overrides ({u})',f'Change ({u})'],rows,
            ['---','---','---','---:','---:'])
        notes.append('Monitoring deadlines and thresholds are house decision rules. A value holds only with the exact '
                     'overrides of its named case, and correlated cases do not add.')
    cases=result.get('counterfactuals') or {}
    if cases:
        rows=[[_plain(name),memo_style.bare_per_share(case.get('per_share_value')),
               memo_style.pct(case.get('terminal_value_fraction')) if _finite_number(case.get('terminal_value_fraction')) else 'undefined']
              for name,case in cases.items()]
        lines+=['','### Counterfactual cases','']+_table(['Case',f'Value per share ({u})','Terminal value share of EV'],rows)
        notes.append('Counterfactual cases change named base-case forecasts only. Reported actuals, the equity bridge and '
                     'the headline value stay fixed. The overrides are in assumptions.json and valuation.json.')
    return lines,notes


def _multiples_parts(result, style):
    """Entry and exit multiples and the repurchase tests: (lines, notes)."""
    diagnostics=result.get('computed_diagnostics',{})
    lines,notes=[],[]
    def multiple(value):
        return memo_style.multiple(value.get('value')) if value.get('value') is not None else str(value.get('status','unavailable')).replace('_',' ')
    def returns(value):
        rates=value.get('annual_return_solutions',[])
        return ', '.join(memo_style.pct(rate) for rate in rates) if rates else str(value.get('status','unavailable')).replace('_',' ')
    multiples=diagnostics.get('valuation_multiples')
    if isinstance(multiples,dict):
        rows=[]
        for name,row in multiples.get('scenarios',{}).items():
            original=result['scenarios'][name].get('fixed_exit_return_at_market_price',{})
            for metric in ('ebit','nopat'):
                rows.append([name.title(),metric.upper(),multiple(row['forecast_year1']['market_ev_to_'+metric]),
                             multiple(row['original_dcf_exit']['ev_to_final_forecast_'+metric]),returns(original),
                             returns(row['unchanged_entry_multiple_exit_returns'][metric])])
        lines+=['','### Entry and exit multiples','']+_table(
            ['Scenario','Earnings','Market EV / year 1','DCF exit EV / year 10','Return at the DCF exit',
             'Return at the entry multiple'],rows,['---','---','---:','---:','---:','---:'])
        stub=((result.get('discounting') or {}).get('timing') or {}).get('stub_years') or 0
        notes+=['Entry earnings are the first forecast year and exit earnings the final forecast year.',
                'The entry-multiple column replaces the DCF continuing value with an exit enterprise value equal to the '
                'entry multiple (market enterprise value over year-1 EBIT or NOPAT) times the same scenario\'s final-year '
                'EBIT or NOPAT, received at the final model year.',
                'The return columns hold operating cash flows, opening claims and shares fixed and change only the exit '
                'value. Each year\'s FCFF is treated as distributed'+(' at its discount time from the valuation date, and '
                f'year 1 counts only its unexpired {1-stub:.0%}' if stub else ' at whole model years')+'. This is a '
                'diagnostic. It forecasts neither payouts nor the share price.']
        opening=((result.get('scenarios') or {}).get('base') or {}).get('fixed_exit_return_at_market_price',{}).get(
            'opening_bridge_distribution_per_share')
        if _finite_number(opening) and abs(opening)>=.005:
            base=(result.get('scenarios') or {}).get('base') or {}
            net_debt,claims,assets=base.get('net_debt') or 0,base.get('other_equity_claims') or 0,base.get('nonoperating_assets') or 0
            plus=(['net cash'] if net_debt<0 else [])+(['non-operating investments'] if assets>0 else [])
            minus=(['net debt'] if net_debt>0 else [])+(['other claims'] if claims>0 else [])
            text=(' and '.join(plus) if plus else '')+((', less ' if plus else 'Less ')+' and '.join(minus) if minus else '')
            text=text[:1].upper()+text[1:]
            notes.append('Both return columns settle the opening bridge at once. '+text+', '+
                         (f'lower the outlay by {style.price(opening)} a share.' if opening>0 else
                          f'raise the outlay by {style.price(-opening)} a share.'))
        rates={name:(result['scenarios'].get(name) or {}).get('terminal_rate') for name in multiples.get('scenarios',{})}
        nopat=[(row['original_dcf_exit'].get('ev_to_final_forecast_nopat') or {}).get('value')
               for row in multiples.get('scenarios',{}).values()]
        if nopat and all(_finite_number(value) for value in nopat) and all(
                _finite_number(rates.get(name)) and abs(row['original_dcf_exit']['ev_to_final_forecast_nopat']['value']*
                                                          rates[name]-1)<1e-6
                for name,row in multiples['scenarios'].items()):
            notes.append('The NOPAT exit multiple is one over the terminal rate in every scenario, because new capital '
                         'earns its cost after year 10.')
    repurchases=diagnostics.get('repurchase_tests')
    if isinstance(repurchases,list) and repurchases:
        rows=[]
        unit,scale,decimals=memo_style.table_scale([row.get('amount') for row in repurchases])
        for row in repurchases:
            refs=row['source_refs'] if isinstance(row['source_refs'],list) else [row['source_refs']]
            label=dollar_amounts(row['label']) if style.code=='USD' else row['label']
            scope=REPURCHASE_SCOPES.get(row.get('interpretation'),_plain(row.get('interpretation','')))
            before=row.get('original_weighted_per_share_value') or (
                ((result.get('scenarios') or {}).get('base') or {}).get('per_share_value'))
            rows.append([label+_cite(refs),scope,
                         memo_style.scaled(row['amount'],scale,decimals),f'{row["price"]:,.2f}',f'{row["shares_repurchased"]/1e6:,.1f}',
                         memo_style.bare_per_share(row['post_repurchase_weighted_per_share_value']),
                         _change(row['post_repurchase_weighted_per_share_value'],before) if _finite_number(before) else
                         memo_style.bare_change(row['weighted_value_change']['dollar_delta'],
                                                row['post_repurchase_weighted_per_share_value'])])
        u=style.unit('')
        when=next((((row.get('funding_bridge') or {}).get('as_of')) for row in repurchases if row.get('funding_bridge')),None)
        lines+=['','### Repurchases','',
                'Each case repeats the buyback from the '+(f'{when} ' if when else 'current ')+'balance sheet: it spends that '
                'cash again and retires the shares again, with operating value fixed. A historical case is a buyback the '
                'balance sheet already reflects, so its value shows a second, hypothetical buyback at that price.','']+_table(
            ['Case','Scope',f'Cash ({style.unit(unit)})',f'Average price ({u})','Shares retired (M)',
             f'Value per share after the repeat ({u})',f'Change ({u})'],rows,['---','---','---:','---:','---:','---:','---:'])
        notes.append('The average price is the cash divided by the shares retired, on the basis the case label states. A '
                     'historical case compares that price with today\'s house value. Forward cases illustrate; they do not '
                     'forecast buybacks.')
    return lines,notes


def _ceiling_note(cost):
    """The final-year margin ceilings, stated from the run's figures (re-audit, 2026-11-27)."""
    ceilings=cost.get('final_year_margin_ceilings') or {}
    peak,average=cost.get('max_operating_margin'),cost.get('average_operating_margin')
    latest=cost.get('latest_run_rate') if isinstance(cost.get('latest_run_rate'),dict) else None
    count=cost.get('years') or 5
    years={3:'three',4:'four',5:'five',6:'six',7:'seven',10:'ten'}.get(count,str(count))
    parts=[]
    bear=ceilings.get('bear')
    if _finite_number(bear):
        if _finite_number(average) and abs(bear-average)<1e-9:
            parts.append(f'The bear ceiling is the {years}-year average operating margin, {memo_style.pct(average)}.')
        elif _finite_number(peak) and abs(bear-peak)<1e-9:
            parts.append(f'The bear ceiling is the {years}-year peak operating margin, {memo_style.pct(peak)}, because '
                         'the average is not positive.')
    base=ceilings.get('base')
    if _finite_number(base) and _finite_number(peak):
        kind='twelve-month margin' if (latest or {}).get('basis')=='trailing_twelve_months' else 'year-to-date margin'
        run=latest.get('operating_margin') if latest else None
        floor=max(peak,run) if _finite_number(run) else peak
        band=(base-floor)*100
        same=ceilings.get('bull')==base
        who='The base and bull ceiling' if same else 'The base ceiling'
        if _finite_number(run):
            parts.append(f'{who} is the higher of the {years}-year peak and the {kind} to '
                         f'{(latest.get("period") or {}).get("end_date")}, here {memo_style.pct(floor)}, plus '
                         f'{band:.1f} points.'.replace('plus 5.0 points','plus 5 points'))
        else:
            parts.append(f'{who} is the {years}-year peak, {memo_style.pct(peak)}, plus {band:.1f} points.'
                         .replace('plus 5.0 points','plus 5 points'))
    return ' '.join(parts) or 'Final-year margin ceilings come from the reported operating margins.'


def _continuity_parts(result, style, history_rows=None):
    """The forecast against the last reported results, as one table: (lines, notes)."""
    names=('bear','base','bull')
    continuity=result.get('forecast_continuity') or {}
    reinvestment=result.get('reinvestment_continuity') or {}
    cost=result.get('cost_path_continuity') or {}
    rows,refs,notes,extra=[],[],[],[]
    if continuity.get('scenarios'):
        refs+=continuity.get('source_refs') or []
        ytd=continuity.get('year_to_date')
        reported=memo_style.pct(continuity['reported_operating_margin'])
        if ytd:
            reported+=f'; {memo_style.pct(ytd["operating_margin"])} year to date'
            refs+=ytd.get('source_refs') or []
        rows.append(['Year-1 EBIT margin',reported]+[memo_style.pct((continuity['scenarios'].get(name) or {}).get('year_one_ebit_margin'))
                                                     for name in names])
        for name,row in continuity['scenarios'].items():
            if row.get('stated_basis') and not row.get('within_band'):
                extra.append(f'- {name.title()} year-1 margin basis: {row["stated_basis"]}')
    if cost.get('scenarios'):
        refs+=cost.get('source_refs') or []
        rows.append(['Final-year EBIT margin',f'peak {memo_style.pct(cost["max_operating_margin"])}, average '
                                              f'{memo_style.pct(cost["average_operating_margin"])}']+
                    [memo_style.pct((cost['scenarios'].get(name) or {}).get('final_ebit_margin')) for name in names])
        rows.append(['Final-year margin ceiling','']+
                    [memo_style.pct((cost['scenarios'].get(name) or {}).get('ceiling')) if
                     (cost['scenarios'].get(name) or {}).get('ceiling') is not None else 'none' for name in names])
        latest=cost.get('latest_run_rate') if isinstance(cost.get('latest_run_rate'),dict) else None
        if latest:
            kind='twelve months' if latest.get('basis')=='trailing_twelve_months' else 'year to date'
            notes.append(f'The operating margin for the {kind} to {(latest.get("period") or {}).get("end_date")}, '
                         f'computed from reported results, is {memo_style.pct(latest["operating_margin"])}'
                         f'{_cite(latest.get("source_refs"))}.')
        notes.append(_ceiling_note(cost))
    if reinvestment.get('scenarios'):
        refs+=reinvestment.get('source_refs') or []
        unit,scale,decimals=memo_style.table_scale([row.get('net_reinvestment') for row in reinvestment['scenarios'].values()])
        years=reinvestment.get('years') or REINVESTMENT_WINDOW
        end=str(reinvestment.get('fiscal_year_end') or '')[:4]
        span=(f'FY{int(end)-years+1}-{end[2:]}' if end.isdigit() else f'the last {years} fiscal years')
        last=f'FY{end}' if end.isdigit() else 'the last fiscal year'
        over=reinvestment.get('net_reinvestment_over_years')
        capex_ratio=reinvestment.get('capital_expenditure_sales_to_capital')
        capital_ratio=reinvestment.get('invested_capital_sales_to_capital')
        capital_change=reinvestment.get('invested_capital_change')
        latest=reinvestment.get('net_reinvestment')
        # The forecast columns are three-year totals of revenue growth over sales-to-capital, so history is a three-year
        # total on a named basis: net reinvestment where it is computed and positive, else the change in invested capital.
        on_net=_finite_number(over) and over>0 and _finite_number(capex_ratio)
        if on_net:
            history=(f'{memo_style.scaled(over,scale,decimals)} over {span}'+
                     (f' ({memo_style.scaled(latest,scale,decimals)} in {last})' if _finite_number(latest) else '')+', computed')
            ratio=(f'{capex_ratio:.2f} on net reinvestment'+
                   (f'; {capital_ratio:.2f} on the change in invested capital' if _finite_number(capital_ratio) else '')+
                   f'; over {span}, computed')
        elif _finite_number(capital_change):
            history=f'{memo_style.scaled(capital_change,scale,decimals)} over {span}, change in invested capital, computed'
            ratio=(f'{capital_ratio:.2f} over {span}, on the change in invested capital, computed' if _finite_number(capital_ratio)
                   else 'n/a')
        else:
            # Older results carry only the last year: name it so it is not read as a three-year total.
            history=(f'{memo_style.scaled(latest,scale,decimals)} in {last} only, computed' if _finite_number(latest) and latest>0
                     else 'not computed')
            reference=reinvestment.get('incremental_sales_to_capital')
            ratio=f'{reference:.2f} over {span}, computed' if _finite_number(reference) else 'n/a'
        rows.append([f'Net reinvestment, forecast years 1-{years} ({style.unit(unit)})',history]+
                    [memo_style.scaled((reinvestment['scenarios'].get(name) or {}).get('net_reinvestment'),scale,decimals)
                     for name in names])
        rows.append([f'Sales-to-capital, forecast years 1-{years}',ratio]+
                    [memo_style.ratio((reinvestment['scenarios'].get(name) or {}).get('sales_to_capital')) for name in names])
        for name,row in reinvestment['scenarios'].items():
            if row.get('stated_basis') and not row.get('within_band'):
                extra.append(f'- {name.title()} reinvestment basis: {row["stated_basis"]}')
        chosen=reinvestment.get('sales_to_capital_basis')
        both=_finite_number(capex_ratio) and _finite_number(capital_ratio)
        if both:
            reference=capital_ratio if chosen=='invested_capital' else capex_ratio
            other=(f'on the change in invested capital it is {capital_ratio:.2f}' if on_net else
                   f'on net reinvestment it is {capex_ratio:.2f}')
            notes.append(f'The historical incremental sales-to-capital, computed from reported values, is shown on the '
                         f'{"net-reinvestment" if on_net else "invested-capital"} basis; {other}. The continuity check uses '
                         f'the lower, {reference:.2f}.')
        elif _finite_number(capital_ratio) or _finite_number(capex_ratio):
            notes.append('Only the '+('change-in-invested-capital' if _finite_number(capital_ratio) else 'net-reinvestment')+
                         ' sales-to-capital is computed here, and the continuity check uses it.')
        if not (_finite_number(latest) and latest>0):
            notes.append(f'The reinvestment floor uses net reinvestment in {last} where it is positive. Here it is '
                         + ('not reported for every year' if reinvestment.get('net_reinvestment_status')=='not_reported'
                            else 'not positive') + ', so there is no floor.')
        notes.append(f'Incremental sales-to-capital is the revenue increase over {span} divided by the investment in those '
                     f'years. Net reinvestment is capital expenditure less {depreciation_phrase(history_rows) if history_rows else "depreciation and amortization"}, plus cash '
                     'acquisitions and finance-lease additions where reported. The change in invested capital follows '
                     'Mauboussin and Callahan and includes working capital and operating leases.'+
                     (' Goodwill and acquired intangibles count only up to the cash paid for acquisitions.'
                      if reinvestment.get('invested_capital_measure')=='stock_deals_excluded' else ''))
    if not rows:
        return [],[]
    when=(continuity.get('period') or {}).get('end_date') or reinvestment.get('fiscal_year_end') or cost.get('fiscal_year_end')
    lines=['','### Forecast against reported results','',
           f'History figures are for the fiscal year ending {when} unless the cell names another period'
           f'{_cite(list(dict.fromkeys(refs)))}. Margins are computed from reported results; net reinvestment and '
           'sales-to-capital are computed from reported lines.','']
    lines+=_table(['Check','History','Bear','Base','Bull'],rows)
    if extra:
        lines+=['']+extra
    return lines,notes


def _scale_parts(plan, result, style):
    """Year-10 scale against today, so a reader can judge the size the forecast implies.

    A memo audit (2026-11-15) found no memo discussed a base-case year-10 EBIT of
    USD 574 billion or a terminal enterprise value near USD 10 trillion.
    """
    base=(result.get('scenarios') or {}).get('base') or {}
    flows=base.get('cashflows') or []
    revenue=((plan or {}).get('actuals') or {}).get('revenue')
    if not flows or not _finite_number(revenue) or revenue<=0:
        return [],[]
    last=flows[-1]
    market=((result.get('computed_diagnostics') or {}).get('valuation_multiples') or {}).get('market') or {}
    text=(f'In year {last.get("year")} of the base case, revenue is {style.amount(last["revenue"])}, '
          f'{last["revenue"]/revenue:,.1f} times the last reported {style.amount(revenue)}. EBIT is '
          f'{style.amount(last["ebit"])} at a {memo_style.pct(last["ebit_margin"])} margin, and free cash flow to the firm '
          f'is {style.amount(last["fcff"])}.')
    terminal=base.get('terminal_enterprise_value')
    if _finite_number(terminal):
        text+=f' The terminal enterprise value at that year is {style.amount(terminal)}'
        enterprise=market.get('enterprise_value')
        if _finite_number(enterprise) and enterprise>0:
            text+=f', {terminal/enterprise:,.1f} times today\'s market enterprise value of {style.amount(enterprise)}'
        text+='.'
    return ['','### Scale check','',text],[]


def _baseline_rows(sources):
    """The reported history rows of the first source that has them, or []."""
    for source in sources or ():
        data=source.get('data') if isinstance(source,dict) and isinstance(source.get('data'),dict) else {}
        baseline=(source.get('historical_baseline') if isinstance(source,dict) else None) or data.get('historical_baseline')
        if baseline and baseline.get('rows'):
            return baseline['rows']
    return []


def _rate_line(result, style):
    """The discount rate as one sentence: the path, its inputs and terminal growth (readability review, 2026-11-27)."""
    discounting=result.get('discounting') or {}
    calculation=result.get('discount_rate_calculation') or {}
    path=[rate for rate in discounting.get('base_rate_path') or [] if _finite_number(rate)]
    terminal=discounting.get('terminal_rate')
    if not path:
        applied=calculation.get('applied_wacc')
        return f'Discount rate: {memo_style.pct(applied)} in every year.' if _finite_number(applied) else None
    shown=[memo_style.pct(rate) for rate in path]
    held=next((index for index,rate in enumerate(path) if abs(rate-path[0])>1e-9),len(path))
    if held==len(shown):
        text=f'Discount rate: {shown[0]} in every forecast year'
    elif held>1:
        text=f'Discount rate: {shown[0]} in years 1 to {held}, moving from year {held+1} to {shown[-1]} by year {len(shown)}'
    else:
        text=f'Discount rate: {shown[0]} in year 1, moving to {shown[-1]} by year {len(shown)}'
    if _finite_number(terminal):
        text+=(' and in the terminal value' if memo_style.pct(terminal)==shown[-1] else
               f', and {memo_style.pct(terminal)} in the terminal value')
    inputs=[]
    risk_free,premium=discounting.get('risk_free_rate'),discounting.get('equity_risk_premium')
    if _finite_number(risk_free):
        inputs.append(f'a {memo_style.pct(risk_free,2)} Treasury rate')
    if _finite_number(premium):
        inputs.append(f'a {memo_style.pct(premium,2)} implied equity premium')
    if _finite_number(calculation.get('relevered_beta')):
        inputs.append(f'a beta of {calculation["relevered_beta"]:.2f}')
    if _finite_number(calculation.get('debt_weight')):
        inputs.append(f'debt at {memo_style.pct(calculation["debt_weight"])} of capital')
    if inputs:
        text+='. Year 1 combines '+(inputs[0] if len(inputs)==1 else ', '.join(inputs[:-1])+' and '+inputs[-1])
    text+='.'
    if _finite_number(risk_free):
        text+=' '+_terminal_growth_sentence(result,risk_free)
    return text


def _requirement_detail(result, style):
    """The full price requirement, the implied returns as one sentence, and their notes: (lines, notes)."""
    lines,notes=_implied_parts(result,style)
    if not lines:
        return [],notes
    # The implied-return table becomes a sentence; the full requirement table stays.
    if '### What the price implies' in lines:
        start=lines.index('### What the price implies')
        end=next((index for index in range(start+1,len(lines)) if lines[index].startswith('The full price requirement')),len(lines))
        implied=(result.get('implied_return_at_price') or {}).get('scenarios') or {}
        # The base case's flat rates are on the first page when they solve.
        on_first_page=_finite_number(((implied.get('base') or {}).get('implied_return')))
        parts=[f'{memo_style.pct(row["implied_return"])} in the {name} case' for name,row in implied.items()
               if isinstance(row,dict) and _finite_number(row.get('implied_return')) and
               not (name=='base' and on_first_page and _base_headline(result))]
        kept=[line for line in lines[start+1:end] if line and not line.startswith('|')]
        sentence=[]
        if parts:
            sentence=[('One flat discount rate in every year sets value equal to the price at '+
                       (parts[0] if len(parts)==1 else ', '.join(parts[:-1])+' and '+parts[-1])+'.')]
            kept=[line for line in kept if not line.startswith('One flat discount rate of')]
            house=((implied.get('base') or {}).get('required_return_flat_equivalent'))
            if _finite_number(house) and not (on_first_page and _base_headline(result)):
                sentence[0]+=f' The base value equals one flat rate of {memo_style.pct(house)}.'
        fragile=[name for name,row in implied.items() if isinstance(row,dict) and row.get('fragile')]
        if fragile:
            sentence.append('The implied return sits within two points of terminal growth in the '+
                            ' and '.join(fragile)+' case, so it is fragile there.')
        lines=lines[:start-1]+['','### What the price needs, in full','']+sentence+[line for line in kept if line]+['']+lines[end:]
    else:
        lines=['','### What the price needs, in full']+lines
    lines=[line.replace('The full price requirement follows. Each row is an alternative, and the inputs interact.',
                        'Each row changes one input with the rest at the base case. Rows are alternatives, and the inputs '
                        'interact.').replace('| Input | House | Needed, or the limit searched | Result |',
                                             '| Input | Base case | Needed, or the limit searched | Result |')
           for line in lines]
    return lines,notes


def _sensitivity_parts(result, style, plan=None):
    """One sensitivity table: conventions, horizons, rate regimes, country risk and beta checks.

    Five headed exhibits answered one question, how much the value moves (readability review,
    2026-11-27). Related rows share a line, with their values in the same order as the label.
    Every owner-decided row stays: the year-10 steady state is the base, with 5-, 15- and
    20-year horizons and the industry fade; country risk; the rate regimes.
    """
    panel=result.get('convention_panel') or {}
    rows={row.get('name'):row for row in panel.get('rows') or [] if row.get('status') in ('calculated','fragile_spread')
          and _finite_number(row.get('per_share_value'))}
    base=(panel.get('baseline_per_share_value') or ((result.get('scenarios') or {}).get('base') or {}).get('per_share_value'))
    used=set()
    body=[]
    def add(label, items):
        items=[(name,value) for name,value in items if _finite_number(value)]
        if not items:
            return
        used.update(name for name,_ in items)
        body.append([label,' / '.join(memo_style.bare_per_share(value) for _,value in items),
                     ' / '.join(_change(value,base) for _,value in items)])
    def group(label, pieces):
        present=[(name,words) for name,words in pieces if name in rows]
        if present:
            add(label+' / '.join(words for _,words in present),[(name,rows[name]['per_share_value']) for name,_ in present])
    group('Terminal return on new capital of ',[('terminal_return_15_percent','15%'),('terminal_return_25_percent','25%'),
                                                ('terminal_return_rate_plus_4_points','the terminal rate plus 4 points'),
                                                ('terminal_return_earlier_house_rule','the five-year median ROIC, at most 15%')])
    for name in ('terminal_reinvestment_continues_forecast','terminal_rnd_expensed','base_margin_held_at_reported_peak',
                 'terminal_growth_at_risk_free_rate','terminal_return_equals_terminal_rate'):
        if name in rows:
            add(_panel_label(rows[name],result),[(name,rows[name]['per_share_value'])])
    group('',[('damodaran_default_terminal_rate','Terminal rate at the risk-free rate plus the equity premium'),
              ('tax_to_marginal_rate','terminal tax at the marginal rate')])
    horizons=sorted([row for name,row in rows.items() if re.fullmatch(r'competitive_advantage_period_\d+_years',name)],
                    key=lambda row:row.get('horizon_years') or 0)
    fade=rows.get('competitive_advantage_period_industry_fade')
    if horizons:
        label='Competitive advantage period of '+' / '.join(str(row.get('horizon_years')) for row in horizons)+' years'
        same=next((row for row in horizons if fade and row.get('horizon_years')==fade.get('horizon_years') and
                   abs(row['per_share_value']-fade['per_share_value'])<1e-6),None)
        if same is not None:
            label+=f'; the industry fade rate gives {fade["horizon_years"]} years'
            used.add('competitive_advantage_period_industry_fade')
        add(label,[(row['name'],row['per_share_value']) for row in horizons])
    if fade and 'competitive_advantage_period_industry_fade' not in used:
        add(_panel_label(fade,result),[(fade['name'],fade['per_share_value'])])
    group('Whole discount-rate path ',[('required_return_minus_1_point','1 point lower'),
                                       ('required_return_plus_1_point','1 point higher'),
                                       ('constant_initial_rate','held at the year-1 rate')])
    treasury=sorted([row for name,row in rows.items() if re.fullmatch(r'treasury_rate_(?:minus|plus)_\d+_bp',name)],
                    key=lambda row:(1 if 'plus' in row['name'] else -1)*int(re.search(r'(\d+)_bp',row['name']).group(1)))
    if treasury:
        words=[]
        for row in treasury:
            size=int(re.search(r'(\d+)_bp',row['name']).group(1))/100
            words.append(f'{size:g} {"lower" if "minus" in row["name"] else "higher"}')
        add('Treasury rate, points: '+' / '.join(words),[(row['name'],row['per_share_value']) for row in treasury])
    history=panel.get('rate_history') or {}
    low,high=rows.get('rate_history_lowest'),rows.get('rate_history_highest')
    if low or high:
        items,words=[],[]
        if low:
            items.append(('rate_history_lowest',low['per_share_value']))
            words.append(f'lowest ({str(low.get("observed_at") or "")[:7]})')
        if _finite_number(history.get('median_per_share_value')):
            items.append(('rate_history_median',history['median_per_share_value']))
            words.append('median')
        if high:
            items.append(('rate_history_highest',high['per_share_value']))
            words.append(f'highest ({str(high.get("observed_at") or "")[:7]})')
        span=(f', {str(history.get("first"))[:7]} to {str(history.get("last"))[:7]}' if history.get('first') and
              history.get('last') else '')
        add(f'Monthly Treasury rate and premium pairs{span}: '+' / '.join(words),items)
    if 'country_risk_premium_revenue_weighted' in rows:
        row=rows['country_risk_premium_revenue_weighted']
        add(_panel_label(row,result),[(row['name'],row['per_share_value'])])
    checks=[row for row in result.get('beta_cross_checks') or [] if row.get('status')=='calculated' and
            _finite_number(row.get('per_share_value'))]
    methods=[row for row in result.get('calibration_comparisons') or [] if _finite_number(row.get('per_share_value'))]
    checks=[row for row in checks if not (_finite_number(row.get('wacc')) and
                                          any(_finite_number(m.get('wacc')) and abs(row['wacc']-m['wacc'])<1e-5 for m in methods))]
    if checks:
        regression=any(row.get('name')=='regression_beta' for row in checks)
        words=[('Regression beta' if row.get('name')=='regression_beta' else
                ('moved a third toward one' if regression else 'Regression beta moved a third toward one')
                if row.get('name')=='blume_adjusted_regression_beta' else _beta_label(row))+
               (f' (year-1 rate {memo_style.pct(row["wacc"],2)})' if _finite_number(row.get('wacc')) else '') for row in checks]
        words[0]=words[0][:1].upper()+words[0][1:]
        add(' / '.join(words),[(row.get('name'),row['per_share_value']) for row in checks])
    house_growth=((result.get('scenarios') or {}).get('base') or {}).get('terminal_growth')
    for row in methods:
        label=(f'Rate method: {_lower_first(_plain(row.get("name","")))}'+
               (f' (year-1 rate {memo_style.pct(row["wacc"],2)})' if _finite_number(row.get('wacc')) else ''))
        items=[(row.get('name'),row['per_share_value'])]
        moved=(_finite_number(row.get('terminal_growth')) and _finite_number(house_growth) and
               abs(row['terminal_growth']-house_growth)>1e-9 and _finite_number(row.get('per_share_value_at_house_terminal_growth')))
        if moved:
            # Terminal growth that defaults to the risk-free rate moves with the method's rate; the second value holds it.
            label+=f', terminal growth {memo_style.pct(row["terminal_growth"],2)} / at the base terminal growth'
            items.append((str(row.get('name'))+'_house_growth',row['per_share_value_at_house_terminal_growth']))
        add(label,items)
    for name,row in rows.items():
        if name not in used and row.get('kind') in ('convention','horizon','sensitivity','rate_regime','country_risk'):
            add(_panel_label(row,result),[(name,row['per_share_value'])])
    if not body:
        return [],[]
    u=style.unit('')
    lines=['','### Sensitivity','',f'Base case, one change at a time; the base value is {style.per_share(base)}. Values in a '
           'cell follow the order of its label. No row carries a probability, and rows do not add.','']
    lines+=_table(['Change',f'Value per share ({u})',f'Change ({u})'],body)
    moving=[]
    for prefix,words in (('required_return_','whole-path'),('constant_initial_rate','year-1-rate'),
                         ('treasury_rate_','Treasury'),('rate_history_','monthly'),
                         ('damodaran_default_terminal_rate','risk-free-plus-premium'),
                         ('country_risk_premium_revenue_weighted','country-risk')):
        if any(name.startswith(prefix) for name in rows):
            moving.append(words)
    house=((result.get('scenarios') or {}).get('base') or {}).get('terminal_rate')
    def held(items):
        return bool(items) and all(_finite_number(row.get('terminal_rate')) and _finite_number(house) and
                                   abs(row['terminal_rate']-house)<1e-9 for row in items)
    still=(['beta'] if held(checks) else [])+(['rate-method'] if held(methods) else [])
    moved=(['beta'] if checks and not held(checks) else [])+(['rate-method'] if methods and not held(methods) else [])
    moving+=moved
    def words(items):
        return items[0] if len(items)==1 else ', '.join(items[:-1])+' and '+items[-1]
    foot=[]
    if moving:
        foot.append(f'In the {words(moving)} rows, the terminal return on new capital moves with the terminal rate the '
                    'row sets.')
    if still:
        foot.append(f'The {words(still)} rows change the rates through year 10 and leave the terminal rate unchanged.')
    if foot:
        lines+=['',' '.join(foot)]
    return lines,[]


def _cases_parts(result, style, shown=()):
    """One table for the monitoring tests and the named cases behind them: (lines, notes).

    The counterfactual table repeated the monitoring values (readability review, 2026-11-27).
    Thresholds already on the first page are not repeated.
    """
    monitoring=[row for row in result.get('monitoring') or [] if isinstance(row,dict)]
    cases=result.get('counterfactuals') or {}
    if not monitoring and not cases:
        return [],[]
    u=style.unit('')
    base=((result.get('scenarios') or {}).get('base') or {}).get('per_share_value')
    shown_names={row.get('name') for row in shown}
    with_threshold=any(row.get('name') not in shown_names for row in monitoring)
    def share(name):
        value=(cases.get(name) or {}).get('terminal_value_fraction') if name else None
        return memo_style.pct(value) if _finite_number(value) else ''
    body,linked=[],set()
    for row in monitoring:
        case=row.get('counterfactual') or row.get('calibration_comparison')
        linked.add(case)
        value=row.get('conditional_per_share_value')
        reference=row.get('base_per_share_value') if _finite_number(row.get('base_per_share_value')) else base
        cells=[_plain(case or row.get('name')),row.get('review_by') or '']
        if with_threshold:
            cells.append('first page' if row.get('name') in shown_names else _threshold_text(row,style))
        cells+=[memo_style.bare_per_share(value),_change(value,reference),share(case)]
        body.append(cells)
    for name,case in cases.items():
        if name in linked or not isinstance(case,dict):
            continue
        cells=[_plain(name),'none']+(['none'] if with_threshold else [])
        cells+=[memo_style.bare_per_share(case.get('per_share_value')),_change(case.get('per_share_value'),base),share(name)]
        body.append(cells)
    header=['Case','Review by']+(['Threshold'] if with_threshold else [])+[f'Value per share ({u})',f'Change ({u})',
                                                                            'Terminal value share of EV']
    lines=['','### What would change the value','',
           'Each value applies the named case\'s forecast changes to the base case. The cases overlap, so they do not add.',
           '']+_table(header,body,['---','---']+(['---'] if with_threshold else [])+['---:','---:','---:'])
    return lines,[]


def _buyback_parts(result, style):
    """Repurchase tests as one sentence each: the one-row table read 0 in its change column (review, 2026-11-27)."""
    tests=((result.get('computed_diagnostics') or {}).get('repurchase_tests'))
    if not isinstance(tests,list) or not tests:
        return [],[]
    base=((result.get('scenarios') or {}).get('base') or {}).get('per_share_value')
    lines=['','### Buybacks tested against today\'s value','']
    for row in tests:
        if not all(_finite_number(row.get(key)) for key in ('amount','price','shares_repurchased',
                                                            'post_repurchase_weighted_per_share_value')):
            continue
        before=row.get('original_weighted_per_share_value') or base
        after=row['post_repurchase_weighted_per_share_value']
        change=after-before if _finite_number(before) else None
        when=((row.get('funding_bridge') or {}).get('as_of'))
        # The label's first sentence says what was bought; its caveat sentences are stated once in the notes.
        label=re.sub(r'\s*\[S\d+[^\]]*\]','',str(row.get('label') or _plain(row.get('name'))).split('. ')[0]).rstrip('.')
        label=dollar_amounts(label) if style.code=='USD' else label
        refs=row.get('source_refs') if isinstance(row.get('source_refs'),list) else [row.get('source_refs')]
        text=(f'- {label}{_cite([ref for ref in refs if isinstance(ref,str)])}. Repeating that buyback, '
              f'{style.amount(row["amount"])} at an average of {style.per_share(row["price"])} a share, from the '+
              (f'{when} ' if when else 'current ')+f'balance sheet gives {style.per_share(after)} a share')
        if _finite_number(change):
            shown=memo_style.bare_change(change,after)
            # A change below the value's display precision is shown in cents, so it never reads as zero.
            text+=(f', a change of {"-" if change<0 else "+"}{style.price(abs(change))}' if shown=='0' and abs(change)>=.005
                   else ', unchanged' if shown=='0' else f', a change of {shown[0]}{style.symbol}{shown[1:]}')
        lines.append(text+'.')
    return (lines if len(lines)>3 else []),['The buyback test spends the stated cash again and retires the shares again, '
                                            'with operating value fixed. A historical case compares the price paid with '
                                            'today\'s value only; forward cases illustrate and forecast no buyback.']


def _exit_parts(result, style):
    """Entry and exit multiples on EBIT, one row per scenario: (lines, notes)."""
    multiples=((result.get('computed_diagnostics') or {}).get('valuation_multiples'))
    if not isinstance(multiples,dict):
        return [],[]
    lines,notes=_multiples_parts(dict(result,computed_diagnostics={'valuation_multiples':multiples}),style)
    rows=[]
    def multiple(value):
        return memo_style.multiple(value.get('value')) if value.get('value') is not None else str(value.get('status','unavailable')).replace('_',' ')
    def returns(value):
        rates=value.get('annual_return_solutions',[])
        return ', '.join(memo_style.pct(rate) for rate in rates) if rates else str(value.get('status','unavailable')).replace('_',' ')
    for name,row in multiples.get('scenarios',{}).items():
        original=result['scenarios'][name].get('fixed_exit_return_at_market_price',{})
        rows.append([name.title(),multiple(row['forecast_year1']['market_ev_to_ebit']),
                     multiple(row['original_dcf_exit']['ev_to_final_forecast_ebit']),returns(original),
                     returns(row['unchanged_entry_multiple_exit_returns']['ebit'])])
    year=((next(iter(multiples.get('scenarios',{}).values()),{}).get('original_dcf_exit') or {}).get('period') or {}).get('model_year',10)
    out=['','### Entry and exit multiples','',
         'EBIT multiples and the annual return at today\'s price under two exits: the base method\'s terminal value, and '
         'the market\'s entry multiple applied to year-'+str(year)+' EBIT. Each return is on a conditional cash stream: '
         'net debt and other claims are settled at the start, each year\'s free cash flow to the firm is paid out, and the '
         'stated exit applies in year '+str(year)+'. Neither column forecasts what shareholders will earn.','']
    out+=_table(['Scenario','Market EV / year-1 EBIT',f'Terminal value / year-{year} EBIT','Return at the terminal value',
                 'Return at the entry multiple'],rows)
    return out,notes


def _appendix_parts(plan, result, sources=None):
    """Appendix A and every note on it, grouped: (lines, groups). Notes marked _Specific stay in the memo."""
    if not result:
        return ['',DETAIL_HEADING,'','No value is calculated because essential valuation inputs are unresolved.'],[]
    plan,_,_=materialize(plan)
    style=_style(plan,result)
    lines=['',DETAIL_HEADING,'',style.header()]
    notes=['Tables and the first page are computed by code from the editable assumptions in assumptions.json; '
           'cashflows.csv and sensitivity.csv hold the calculations. The prose is written by one model and reviewed by another.',
           'Amounts are whole currency units and actual share counts before display rounding.',CHANGE_NOTE]
    if _base_headline(result):
        notes.append('The base case is the headline value and the expected path. Bear and bull are stress cases without '
                     'probabilities.')
    thin=[name for name in ('bear','base','bull') if 'terminal_rate' in result['scenarios'][name] and
          result['scenarios'][name].get('terminal_spread') is not None and result['scenarios'][name]['terminal_spread']<.02]
    if thin:
        notes.append(_Specific('The terminal rate is within two points of terminal growth in '+', '.join(thin)+
                               '. The value there is fragile to small changes in either.'))
    notes+=_assumption_notes(plan.get('assumptions_label'),result,plan)
    timing=(result.get('discounting') or {}).get('timing',{})
    dates=plan.get('input_dates',{}) if isinstance(plan.get('input_dates'),dict) else {}
    measured=[]
    for field,label in [('net_debt','net debt'),('other_equity_claims','other claims'),
                        ('nonoperating_assets','non-operating investments'),('shares_outstanding','shares outstanding'),
                        ('dilutive_shares','dilutive shares, fiscal year to'),('market_price','quote')]:
        if field in ('nonoperating_assets','dilutive_shares') and field not in dates:
            continue
        row=dates.get(field,{})
        when=(row.get('instant') or row.get('observed_at') or row.get('end_date')) if isinstance(row,dict) else None
        measured.append(f'{label} '+(str(when)[:10] if when else 'not supplied'))
    notes.append('Measurement dates: '+'; '.join(measured)+'.')
    stale=isinstance(timing.get('elapsed_days'),(int,float)) and timing['elapsed_days']>=STALE_BRIDGE_DAYS
    if timing.get('convention')=='stub_v1':
        notes.append(f'Present value is discounted to the valuation date, {timing["valuation_date"]}. The first forecast '
                     f'year counts only its unexpired {1-timing["stub_years"]:.0%}, and net debt stays at the '
                     f'{timing["anchor_date"]} annual balance sheet.')
        first=next(iter(((result.get('scenarios') or {}).get('base') or {}).get('cashflows') or []),{})
        shares=((result.get('scenarios') or {}).get('base') or {}).get('diluted_shares')
        elapsed=timing.get('stub_years')
        if _finite_number(first.get('fcff')) and _finite_number(elapsed) and elapsed>0 and not stale:
            # A stale bridge shows this figure as a bridge row instead.
            earned=first['fcff']*elapsed
            notes.append(_Specific(f'The free cash flow of the elapsed {elapsed:.0%} of year 1, about {style.amount(earned)}'+
                         (f' or {"-" if earned<0 else ""}{style.price(abs(earned/shares))} a share' if _finite_number(shares) and shares else '')+
                         ', is in neither the cash flows nor the bridge.'))
    else:
        notes.append('Balances are not rolled forward to the quote date, and the annual model has no stub period.')
    benchmark=result.get('index_benchmark') or {}
    if _finite_number(benchmark.get('value_to_price')) and all(_finite_number(benchmark.get(key)) for key in
                                                              ('risk_free_rate','equity_risk_premium','market_return_on_new_capital')):
        rate=benchmark.get('discount_rate')
        notes.append(f'The S&P 500 ratio values a stylized index discounted at the {benchmark.get("observed_at")} Treasury rate of '
                     f'{benchmark["risk_free_rate"]:.2%} plus the implied premium of {benchmark["equity_risk_premium"]:.2%}'+
                     (f', a flat {rate:.2%} with no glide' if _finite_number(rate) else '')+'. New capital earns '
                     f'{benchmark["market_return_on_new_capital"]:.0%}, the house\'s stylized long-run market return, for '
                     f'{benchmark.get("excess_return_years")} years and then its cost; the price is read as that return '
                     'forever. It is a steady state. Damodaran\'s own calculation adds five years of consensus growth, '
                     'which needs index earnings and return on equity his monthly workbook does not carry.')
    groups=[('Valuation basis',notes)]
    forecast,forecast_notes=_forecast_parts(result,style)
    rate=_rate_line(result,style)
    step=_transition_step(result,style.amount)
    scale,_=_scale_parts(plan,result,style)
    after=[]
    if rate:
        after+=['',rate]
    if step:
        after+=['',step]
    if scale:
        after+=['',scale[-1]]
    _,roic_notes=_forecast_roic_parts(result,style)
    # The memo shows the base ROIC only: notes on the incremental and After rows go.
    roic_notes=[note.split(' The 3-year incremental ROIC')[0] for note in roic_notes
                if not note.startswith(('The forecast ROIC table is for display','The After column'))]
    _,rate_notes=_discount_parts(result,style)
    if (result.get('discounting') or {}).get('convention')=='glide_to_market_v1':
        discounting=result['discounting']
        rate_notes.insert(0,f'The terminal rate uses the market operating-asset beta of {discounting["terminal_unlevered_beta"]:.2f}, '
                            f'relevered at this firm\'s leverage to {discounting["terminal_relevered_beta"]:.2f}. Damodaran\'s '
                            f'spreadsheet default, the risk-free rate plus the equity risk premium, would be '
                            f'{discounting["damodaran_default_terminal_rate"]:.2%}.')
    rate_notes=[note for note in rate_notes if not note.startswith(('In the rate-method table','Historical-fit rate endpoints'))]
    _,panel_notes=_panel_parts(result,style,plan)
    panel_notes=[note.replace('The country risk row is an exhibit. It is not ranked','The country risk row is not ranked')
                 .replace('Sensitivity rows shift the whole discount-rate path','The whole-path rows shift the whole discount-rate path')
                 for note in panel_notes if not re.search(r'does not apply|has no row of its own|adds no separate row',note)
                 and not note.startswith('The regression-beta check equals')]
    rate_rule=_rate_test_note(result)
    if rate_rule:
        panel_notes.append(rate_rule)
    country_table,_=country_risk.render_parts(result.get('convention_panel') or {})
    bridge,bridge_notes=_equity_bridge_parts(plan,result,style,sources)
    detail,detail_notes=_requirement_detail(result,style)
    sensitivity,_=_sensitivity_parts(result,style,plan)
    rates_table,rates_notes=base_rates.memo_exhibit(result.get('base_rates'),style)
    cases,_=_cases_parts(result,style,_dated_tests(result))
    _,monitoring_notes=_monitoring_parts(result,style)
    buybacks,buyback_notes=_buyback_parts(result,style)
    exits,exit_notes=_exit_parts(result,style)
    exit_notes=[note.replace('The entry-multiple column','The entry-multiple return').replace(' or NOPAT','')
                .replace(' EBIT or NOPAT',' EBIT') for note in exit_notes
                if not note.startswith('The NOPAT exit multiple')]
    continuity,continuity_notes=_continuity_parts(result,style,_baseline_rows(sources))
    for part in (bridge,forecast+after,continuity,detail,rates_table,sensitivity,cases,buybacks,exits):
        lines+=part
    groups+=[('Valuation basis',bridge_notes+forecast_notes),
             ('Forecast returns on capital',roic_notes),
             ('Discount rate and price',rate_notes+detail_notes),
             ('Sensitivity table',panel_notes),
             ('Base rates, tests and diagnostics',rates_notes+monitoring_notes+buyback_notes+exit_notes),
             ('Forecast against reported results',continuity_notes)]
    if country_table and country_table[0].startswith('No country risk row'):
        groups.append(('Country risk exposure',country_table))
    elif country_table:
        groups.append(('Country risk exposure',[_Table(country_table)]))
    return lines,groups


class _Table(str):
    """A block of lines (a table and its sentences) kept whole in the method-notes file."""

    def __new__(cls, lines):
        text=str.__new__(cls,'\n'.join(lines))
        text.lines=list(lines)
        return text


# Reported-history notes that are facts about this company, kept in the memo.
SPECIFIC_HISTORY = re.compile(r'^(?:Trailing twelve months|Leases signed but not yet commenced|Average buyback price is the '
                              r'program value|The latest NOPAT tax rate|ROIC is n/m|The trailing twelve months)')


def _split_notes(groups):
    """(company-specific notes for the memo, house groups for method-notes.md), each note once."""
    specific,house,seen=[],[],set()
    for group,notes in groups:
        kept=[]
        for note in notes:
            text=note if isinstance(note,_Table) else ' '.join(str(note).split())
            if not text or text in seen:
                continue
            seen.add(text)
            if isinstance(note,_Specific) or (group=='Reported history' and SPECIFIC_HISTORY.match(text)):
                specific.append(text)
            else:
                kept.append(note if isinstance(note,_Table) else text)
        house.append((group,kept))
    return specific,house


def _method_notes_lines(groups):
    """House method notes under bold group labels; a kept table prints whole."""
    out,order,by_group=[],[],{}
    for group,notes in groups:
        for note in notes:
            if group not in by_group:
                order.append(group)
                by_group[group]=[]
            by_group[group].append(note)
    for group in order:
        out+=['',f'**{group}**','']
        for note in by_group[group]:
            out+=(note.lines if isinstance(note,_Table) else ['- '+note])
    return out


def _sample_note(result, style, state):
    """One line on how the forecast samples were combined, or None."""
    sentence=consensus.first_page_sentence(_consensus_record(result),style)
    if sentence:
        return sentence
    notes=consensus.method_notes(consensus.enrich(_consensus_record(result),(state or {}).get('plan_consensus')),
                                 style,requested=_requested_samples(state or {}))
    return notes[0] if notes and 'not available' in notes[0] else None


def _without_concepts(text):
    """A note without its parenthesised XBRL concept names, nested parentheses included."""
    while True:
        match=re.search(r'\s*\((?=[a-z][a-z0-9-]*:[A-Z])',text)
        if not match:
            return text
        depth,end=0,None
        for index in range(match.end()-1,len(text)):
            depth+=1 if text[index]=='(' else -1 if text[index]==')' else 0
            if depth==0:
                end=index+1
                break
        if end is None:
            return text
        text=text[:match.start()]+text[end:]


def _memo_parts(memo, sources, plan, result, ticker='', state=None):
    """(the memo up to the notes, the company-specific notes, the house note groups)."""
    state=state or {}
    style=_style(plan,result)
    prose=_strip_model_frame(memo)
    brief,prose=_split_brief(prose)
    summary=computed_summary(plan,result,brief=brief,sources=sources)
    title=memo_title(plan,result,sources,ticker) if (sources or ticker) else None
    if title is None:
        first=(memo or '').lstrip().split('\n',1)[0]
        title=first if first.startswith('# ') else memo_title(plan,result)
    front=title+'\n\n'+(summary+'\n' if summary else '')+prose+'\n'
    detail,groups=_appendix_parts(plan,result,sources)
    history,history_notes=_reported_parts(sources)
    groups=groups+[('Forecast samples',consensus.method_notes(consensus.enrich(_consensus_record(result),state.get('plan_consensus')),
                                                              style,requested=_requested_samples(state))),
                   ('Reported history',history_notes),
                   ('Review',['Software checks and model reviews do not establish investment truth or forecast accuracy.'])]
    specific,house=_split_notes(groups)
    # XBRL concept names are audit detail: the method-notes file keeps them (readability review, 2026-11-27).
    specific=[_without_concepts(note) for note in specific]
    sample=_sample_note(result,style,state)
    if sample:
        specific.insert(0,sample)
    computed=memo_style.compact('\n'.join(detail)+'\n'+'\n'.join(history),style)
    body=_label_unverified(front.rstrip()+'\n'+computed,sources)
    return body,specific,house


def reader_view(memo, sources, plan, result, ticker='', state=None):
    """The delivered memo up to the sources: title, first page, prose, Appendix A, Appendix B and the notes.

    The reviewer and the finding check read this same text, so a finding quotes
    what the reader sees (memo layout joined to the review loop, 2026-11-27).
    """
    body,specific,_=_memo_parts(memo,sources,plan,result,ticker,state)
    return body.rstrip()+'\n'+_label_unverified(memo_style.compact('\n'.join(_memo_notes_lines(specific)),_style(plan,result)),
                                               sources)


def _memo_notes_lines(specific):
    lines=['',MEMO_NOTES_HEADING,'']+['- '+note for note in specific]
    lines+=(['',f'The method notes for this run, the full source list and the review record are in '
                f'[{METHOD_NOTES_FILE}]({METHOD_NOTES_FILE}).'])
    return lines


GAP_OPERATIONS = re.compile(r'--\w|\bfolder\b|\bresume\b|\brerun\b|^Run in\b', re.I)


def _reader_gaps(gaps):
    """Retrieval gaps without the operating instructions a reader cannot act on."""
    out=[]
    for gap in gaps or []:
        sentences=[part for part in re.split(r'(?<=[.])\s+(?=[A-Z])',str(gap).strip()) if part and not GAP_OPERATIONS.search(part)]
        if sentences:
            out.append(' '.join(sentences))
    return out


def _source_line(source, short=True):
    url=source.get('url') or 'supplied text'
    if short and isinstance(url,str):
        url=url.split('?',1)[0]
    when=str(source.get('retrieved_at') or 'not recorded')
    return (f'- [{source["id"]}{" unverified" if source.get("source_tier") in UNVERIFIED_TIERS else ""}] '
            f'{source["title"]}, {url}; retrieved {when[:10] if short else when}')


def _review_record(state):
    """The full review record: status, open findings, findings not upheld, the central weakness and the models."""
    unresolved=state['review_history'][-1].get('findings',[]) if state.get('review_history') else []
    computed_text=state.get('computed_text_findings') or {}
    latest_iteration=(state['review_history'][-1].get('iteration') if state.get('review_history') else None)
    on_computed=set(computed_text.get('ids') or []) if computed_text.get('iteration')==latest_iteration else set()
    note='\n'.join(f'- {f["severity"]}: {f["issue"]}' for f in unresolved if f.get('id') not in on_computed)
    if on_computed:
        note+=(('\n\n' if note else '')+COMPUTED_TEXT_HEADING+'\n'+
               '\n'.join(f'- {f["severity"]}: {f["issue"]}' for f in unresolved if f.get('id') in on_computed))
    if not note:
        note=('No material finding is recorded. A model review is an assessment and certifies nothing.'
              if state.get('complete') else 'Review is incomplete; unresolved issues are not yet determined.')
    latest=state['review_history'][-1] if state.get('review_history') else {}
    refuted=(latest.get('finding_check') or {}).get('refuted') or []
    if refuted:
        note+=('\n\nRaised by the reviewer and not upheld on a second check:\n'+
               '\n'.join(f'- {f["severity"]}: {f["issue"]} Check: {f["check_reason"]}' for f in refuted))
    weakness=latest.get('central_weakness')
    if isinstance(weakness,dict):
        note+=(f"\n\nReviewer's central weakness: {weakness['claim']}\n\nComputed effect: "
               f"{weakness['computed_effect'].rstrip('.')}.\n\nThe memo handles it: {weakness['memo_handles_it']}. "
               f"The memo explains the difference between value and price: {latest.get('gap_explained','not stated')}.")
    if latest.get('review_model'):
        same=latest.get('review_model')==latest.get('author_model')
        note+=(f"\n\nReview model: {latest['review_model']}"+
               (f"; author model: {latest['author_model']}" if latest.get('author_model') else '')+
               ('. The same model wrote and reviewed this memo.' if same else '.'))
    return 'Review status: '+state.get('quality_status','in_progress')+'\n\n'+note


def _review_footer(state):
    """Two lines: the review status in words with what remains open, and where the record is."""
    status=str(state.get('quality_status') or 'in_progress').replace('_',' ')
    latest=state['review_history'][-1] if state.get('review_history') else {}
    findings=latest.get('findings') or []
    counts={}
    for finding in findings:
        counts[finding.get('severity','unrated')]=counts.get(finding.get('severity','unrated'),0)+1
    order=['blocker','major','minor']+sorted(name for name in counts if name not in ('blocker','major','minor'))
    open_text=', '.join(f'{NUMBER_WORDS.get(counts[name],counts[name])} {name}' for name in order if counts.get(name))
    if latest:
        same=latest.get('review_model') and latest.get('review_model')==latest.get('author_model')
        first=(f'Review status: {status}. '+('The model that wrote this memo also reviewed it' if same else
                                              'A second model reviewed this memo')+
               (f'; {open_text} finding{"s" if sum(counts.values())!=1 else ""} remain{"" if sum(counts.values())!=1 else "s"} open.'
                if open_text else '; no finding remains open.'))
    else:
        first=f'Review status: {status}. No model review is recorded.'
    return first+f'\nThe findings, the reviewer\'s notes and the run record are in [{METHOD_NOTES_FILE}]({METHOD_NOTES_FILE}).'


def _cited_ids(text):
    return set(re.findall(r'\b(S\d+)\b(?=[^\[\]\n]*\])',text or ''))


def _render(memo, sources, gaps, plan, result, state):
    """The reader's memo: title, first page, prose, appendices, notes, cited sources and a two-line review footer."""
    body=reader_view(memo,sources,plan,result,state.get('ticker',''),state)
    cited=_cited_ids(body)
    listed=[source for source in sources if source.get('id') in cited]
    refs='\n'.join(_source_line(source) for source in listed) or '- No source is cited.'
    shown=_reader_gaps(gaps)
    limits=('\n\nEvidence not retrieved: '+' '.join(gap.rstrip('.')+'.' for gap in shown)) if shown else ''
    return body+'\n\n## Sources\n'+refs+limits+'\n\n## Review\n'+_review_footer(state)+'\n'


def method_notes_document(memo, sources, gaps, plan, result, state):
    """method-notes.md: the house method notes for this run, every source collected and the run record."""
    _,_,house=_memo_parts(memo,sources,plan,result,state.get('ticker',''),state)
    style=_style(plan,result)
    title=memo_title(plan,result,sources,state.get('ticker','')).replace(': intrinsic value',': method notes')
    lines=[title,'',f'How the numbers in [memo.md](memo.md) are built for this run. Notes specific to the company are '
                    'in the memo itself.']
    lines+=['','## Method notes']+_method_notes_lines(house)
    lines+=['','## Sources collected','']+[_source_line(source,short=False) for source in sources]
    lines+=['','## Run record','']+(['Retrieval gaps:']+['- '+str(gap) for gap in gaps] if gaps else ['No retrieval gap is recorded.'])
    lines+=['',_review_record(state)]
    return memo_style.compact('\n'.join(lines)+'\n',style)


def _write_memo(folder, memo, sources, gaps, plan, result, state):
    """Write memo.md and method-notes.md from the same state; return the memo text."""
    report=_render(memo,sources,gaps,plan,result,state)
    (folder/'memo.md').write_text(report,encoding='utf-8')
    (folder/METHOD_NOTES_FILE).write_text(method_notes_document(memo,sources,gaps,plan,result,state),encoding='utf-8')
    return report


def _write_placeholder(folder, text):
    """A placeholder memo.md; a method-notes file from an earlier memo no longer applies."""
    (folder/'memo.md').write_text(text,encoding='utf-8')
    (folder/METHOD_NOTES_FILE).unlink(missing_ok=True)


_valuation_parts=_appendix_parts


def _notes_lines(groups, heading=NOTES_HEADING):
    """Method notes under bold group labels, each note stated once."""
    seen,order,by_group=set(),[],{}
    for group,notes in groups:
        for note in notes:
            text=' '.join(str(note).split())
            if text and text not in seen:
                seen.add(text)
                if group not in by_group:
                    order.append(group)
                    by_group[group]=[]
                by_group[group].append('- '+text)
    out=[]
    for group in order:
        out+=['',f'**{group}**','']+by_group[group]
    return (['',heading]+out) if out else []


def valuation_section(plan, result):
    """Appendix A with every note on it, as the reviewer and the fix call see it."""
    lines,groups=_appendix_parts(plan,result)
    specific,house=_split_notes(groups)
    notes=['','### Notes on the valuation','']+['- '+note for note in specific]+_method_notes_lines(house)
    return memo_style.compact('\n'.join(lines+notes)+'\n',_style(plan,result))


# Plain-text history labels and the memo's labels, with units in the label.
HISTORY_LABELS = {
    'revenue': 'Revenue ({B})', 'growth': 'Revenue growth', 'gross margin': 'Gross margin',
    'operating margin': 'Operating margin', 'operating income growth': 'Operating income growth',
    'R&D/rev': 'R&D / revenue', 'SG&A/rev': 'SG&A / revenue', 'effective tax': 'Effective tax rate',
    'capex/rev': 'Capex / revenue', 'D&A/rev': 'D&A / revenue', 'depreciation only/rev': 'Depreciation / revenue',
    'SBC/rev': 'SBC / revenue', 'buybacks (cash)': 'Buybacks, cash ({B})', 'avg buyback price': 'Average buyback price ({U})',
    'dividends': 'Dividends ({B})', 'diluted EPS': 'Diluted EPS ({U})', 'EPS growth': 'EPS growth',
    'shares outstanding at FY end (millions)': 'Shares outstanding, year end (M)',
    'change in shares outstanding (millions)': 'Change in shares outstanding (M)',
    'shares repurchased (millions)': 'Shares repurchased (M)', 'acquisitions (cash)': 'Acquisitions, cash ({B})',
    'finance-lease additions': 'Finance-lease additions ({B})',
    'NOPAT': 'NOPAT ({B})', 'NOPAT margin': 'NOPAT margin', 'invested capital (year end)': 'Invested capital, year end ({B})',
    'average invested capital': 'Average invested capital ({B})', 'capital turnover': 'Capital turnover',
    'ROIC excluding goodwill and acquired intangibles': 'ROIC without goodwill and acquired intangibles',
    '3-year incremental ROIC': '3-year incremental ROIC', 'NOPAT tax rate': 'NOPAT tax rate',
    'ROIC with intangibles capitalized': 'ROIC with intangibles capitalized',
    'Revenue': 'Revenue ({B})', 'Growth on the year-earlier quarter': 'Growth on the year-earlier quarter',
    'Operating margin': 'Operating margin', 'Inventory at quarter end': 'Inventory, quarter end ({B})',
    'Deferred revenue at quarter end': 'Deferred revenue, quarter end ({B})'}
# Rows that are only the difference of a row beside them (readability review, 2026-11-27).
HISTORY_DERIVED = frozenset({'change in shares outstanding (millions)'})
CELL_NOTES = {'n/m (capital change under a tenth of capital)': 'n/m', 'n/m (capital fell or unchanged)': 'n/m',
              'n/m (capital at or below zero)': 'n/m'}


def _history_table(names, columns, style, notes, letter='B', price_label=None):
    """A transposed Markdown table: one row per measure, one column per period. Rows with no data are dropped."""
    lines=['| Measure | '+' | '.join(columns[0])+' |','|---|'+'---:|'*len(columns[0])]
    empty=[]
    for index,name in enumerate(names,1):
        cells=[row[index] if index<len(row) else 'n/a' for row in columns[1]]
        if 'n/m (capital at or below zero)' in cells:
            notes.append('ROIC is n/m where invested capital is at or below zero.')
        cells=[CELL_NOTES.get(cell,cell) for cell in cells]
        if name.strip() in HISTORY_DERIVED:
            continue
        label=HISTORY_LABELS.get(name.strip(),name.strip()[:1].upper()+name.strip()[1:])
        if name.strip()=='avg buyback price' and price_label:
            label='Average buyback price, '+price_label+' ({U})'
        label=label.replace('{B}',style.unit(letter)).replace('{U}',style.unit(''))
        if all(cell=='n/a' for cell in cells):
            word=label.split(' (')[0]
            empty.append(word if word[1:2].isupper() or word[1:2]=='&' else word[:1].lower()+word[1:])
            continue
        lines.append(f'| {label} | '+' | '.join(cells)+' |')
    if empty:
        notes.append('Not reported in any year shown: '+', '.join(empty)+'.')
    return lines


def _billions_in_text(text, style):
    """'0.6 billion' in a shared basis note becomes '$600M' in the memo."""
    return re.sub(r'(?<![\w.])(\d[\d,]*(?:\.\d+)?) billion\b',
                  lambda match: style.amount(float(match.group(1).replace(',',''))*1e9),text)


def _reported_parts(sources):
    """The reported history and breakdown tables, and their basis notes: (lines, notes).

    Writers re-typed these tables and introduced errors ("not shown" for a reported
    56.6% segment margin, 2026-11-15); the reader gets Python's copy.
    """
    lines,notes=[],[]
    currency='USD'
    for source in sources:
        data=source.get('data') if isinstance(source.get('data'),dict) else {}
        baseline=source.get('historical_baseline') or data.get('historical_baseline')
        if baseline and baseline.get('rows'):
            currency=(baseline['rows'][-1] or {}).get('currency') or currency
            break
    for source in sources:
        data=source.get('data') if isinstance(source.get('data'),dict) else {}
        baseline=source.get('historical_baseline') or data.get('historical_baseline')
        ref=_cite(source.get('id'))
        if baseline and baseline.get('rows'):
            style=memo_style.Style((baseline['rows'][-1] or {}).get('currency'))
            letter,divisor=scale_of(value for row in baseline['rows'] for value in
                                    (row.get('revenue'),row.get('repurchases'),row.get('dividends')))
            text=render_baseline(baseline,source.get('id'),include_roic=False,scale=divisor).split('\n')
            basis=text[text.index('Basis notes:'):] if 'Basis notes:' in text else []
            text=text[:len(text)-len(basis)]
            header,rows=text[1].split(' | '),[row.split(' | ') for row in text[2:] if row.strip()]
            cells=' '.join(' '.join(row) for row in rows)
            lines+=['','### Annual results','',f'Fiscal years ending on the dates shown{ref}. Ratios are computed from '
                    'reported values; n/a means not reported on a comparable basis.'+
                    (f' "{OUTSIDE_SCANNED.capitalize()}" marks a figure that older filings report and the scanned filings do '
                     'not cover.' if OUTSIDE_SCANNED in cells else '')+
                    (' "(Depreciation only)" marks a year whose filing gives depreciation without amortization.'
                     if '(depreciation only)' in cells else ''),'']
            price_label,_=repurchase_price_basis(baseline['rows'])
            lines+=_history_table(header[1:],([row[0] for row in rows],rows),style,notes,letter,price_label)
            notes+=[_billions_in_text(note[2:].rstrip('.'),style).replace(
                ', not cash buybacks divided by shares:','. Cash buybacks over the same shares give a different price:')+'.'
                    for note in basis[1:] if note.startswith('- ')]
            quarterly,quarterly_notes=quarterly_markdown(baseline,source.get('id'))
            if quarterly:
                lines+=['','### Quarters','']+quarterly
                notes+=quarterly_notes
            letter,divisor=scale_of(value for row in baseline['rows'] for value in
                                    (row.get('nopat'),row.get('invested_capital'),row.get('average_invested_capital')))
            returns=render_roic(baseline,source.get('id'),scale=divisor).split('\n')
            if len(returns)>2:
                cut=returns.index('ROIC basis:') if 'ROIC basis:' in returns else len(returns)
                header,rows=returns[1].split(' | '),[row.split(' | ') for row in returns[2:cut] if row.strip()]
                lines+=['','### Return on invested capital','',
                        f'Mauboussin and Callahan method (Counterpoint Global, October 2022), computed from reported values{ref}.','']
                lines+=_history_table(header[1:],([row[0] for row in rows],rows),style,notes,letter)
                notes+=[note[2:] for note in returns[cut+1:] if note.startswith('- ')]
        breakdowns=source.get('breakdown_tables') or data.get('breakdown_tables')
        if breakdowns:
            history=_baseline_rows(sources)
            consolidated={row['fiscal_year_end']:row.get('operating_income') for row in history
                          if _finite_number(row.get('operating_income'))}
            table,table_notes=breakdown_markdown(breakdowns,source.get('id'),currency,
                                                 consolidated_operating_income=consolidated or None)
            lines+=table
            notes+=table_notes
    return ((['',HISTORY_HEADING]+lines) if lines else []),notes


def reported_tables_section(sources):
    """The reported history with its basis notes, as the reviewer sees it (a list of lines)."""
    lines,notes=_reported_parts(sources)
    out=lines+_notes_lines([('Reported history',notes)],'### Notes on the reported history') if lines else []
    return memo_style.compact('\n'.join(out)).split('\n') if out else []


def _strip_model_frame(memo):
    """The model's prose sections only: no title, preamble or copy of a computed block."""
    memo=re.sub(r'(?ms)^## (?:Valuation summary \(computed by Python\)|Summary)\n.*?(?=^#|\Z)','',memo or '')
    lines=memo.strip('\n').split('\n')
    # The model's own title line goes; a level-1 numbered section heading stays.
    if lines and re.match(r'# (?!\**\s*\d)',lines[0]):
        lines=lines[1:]
    first=next((index for index,line in enumerate(lines) if re.match(r'#{1,6} ',line)),None)
    if first is not None:
        lines=lines[first:]
    # An edit that deletes a paragraph leaves its blank lines behind: WMT's section 1 printed one
    # sentence and four empty lines (readability review, 2026-11-27). Paragraphs keep one blank line.
    return re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+','\n\n','\n'.join(lines)).strip('\n')


def _with_summary(memo, plan, result, sources=(), ticker=''):
    """The title, the computed first page, then the model's prose sections."""
    summary=computed_summary(plan,result)
    prose=_strip_model_frame(memo)
    title=memo_title(plan,result,sources,ticker) if (sources or ticker) else None
    if title is None:
        first=(memo or '').lstrip().split('\n',1)[0]
        title=first if first.startswith('# ') else memo_title(plan,result)
    return title+'\n\n'+(summary+'\n' if summary else '')+prose+'\n'


def _inspected_text(memo, sources, plan, result, ticker=''):
    """The first page and the writer's prose: what Python's memo checks read.

    The valuation detail lists counterfactual and convention tables with "Case"
    and "Value per share" columns. Read as the scenario table, a case named
    bull_case or one holding "expected" raised a value mismatch that no edit could
    clear (review, 2026-11-27). The first page carries the scenario table itself.
    """
    return _label_unverified(_with_summary(memo,plan,result,sources,ticker),sources)


# ---------------------------------------------------------------- forecast samples
#
# Four plans on identical evidence and prompt at temperature 0 valued Microsoft from
# 354 to 398 and Apple from 138 to 151 a share (2026-11-27): noise around one view.
# The plan is requested several times in one cache session and the consensus of the
# valid samples (consensus.py) is locked, checked and valued like any model plan.
PLAN_SAMPLES = {'balanced': 3}
MAX_PLAN_SAMPLES = 5


def default_plan_samples(profile):
    """Forecast plan samples a profile takes when none is chosen: three for balanced, else one."""
    return PLAN_SAMPLES.get(profile, 1)


def _plan_stage_name(sample, repair=None):
    """valuation_plan, valuation_plan_repair, valuation_plan_repair_2..; sample k>1 adds _s<k> after valuation_plan."""
    name = 'valuation_plan' + ('' if sample == 1 else f'_s{sample}')
    return name if repair is None else name + '_repair' + ('' if repair == 0 else f'_{repair+1}')


def _sample_of_stage(name):
    """The plan sample a saved stage key belongs to, or None for a stage outside the plan samples."""
    stage = name.split(':', 1)[0]
    match = re.fullmatch(r'valuation_plan(?:_s([2-9]))?(?:_repair(?:_\d+)?)?', stage)
    return (int(match.group(1)) if match.group(1) else 1) if match else None


def _draft_plan(state, call, prompt, prefix, strong, sample, single):
    """One forecast plan with the normal repair loop.

    Returns {'status': 'valid', 'proposal', 'plan', 'changes', 'result', 'omitted'},
    {'status': 'unavailable', 'plan'} or {'status': 'failed', 'reason'}. With
    ``single`` (one sample requested) the repair reasons are kept in state as
    before; with several they are not, so a resumed run resends identical prompts.
    """
    later = sample > 1
    text = call(_plan_stage_name(sample), strong, prompt, tokens=12000, prefix=prefix, reserve_cached=later)
    failures = []
    for repair in range(PLAN_REPAIRS+1):
        try:
            # A valid plan behind a line of analysis was rejected live
            # (2026-11-14) and cost a paid repair; accept exactly one
            # complete fenced block, as the correction stage does.
            plan, normalized = _response_json(text)
            if normalized:
                recovery = {'stage': _plan_stage_name(sample)+('' if repair == 0 else '_repair'), 'attempt': repair,
                            'method': 'one_explicit_complete_json_fence'}
                if recovery not in state.setdefault('response_normalizations', []):
                    state['response_normalizations'].append(recovery)
            if not isinstance(plan, dict):
                raise ValueError('Valuation plan must be an object')
            if plan.get('unavailable'):
                return {'status': 'unavailable', 'plan': plan}
            (locked, changes, result), omitted = _omit_invalid_optional_sections(
                plan, lambda candidate: _lock_and_value(candidate, state))
            return {'status': 'valid', 'proposal': plan, 'plan': locked, 'changes': changes, 'result': result,
                    'omitted': omitted, 'repair_reasons': failures}
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            failures.append(str(exc))
            if single:
                state['plan_repair_reasons'] = list(failures)
            if repair == PLAN_REPAIRS:
                return {'status': 'failed', 'repair_reasons': failures,
                        'reason': f'Valuation plan remains invalid after {PLAN_REPAIRS} repairs: '+str(exc)}
            rejected = text
            text = call(_plan_stage_name(sample, repair), strong, prompt+'\nRejected JSON:\n'+text+
                        '\nRepair this precise validation failure without inventing actuals: '+str(exc)+
                        ('\nEarlier attempts were rejected for these reasons; do not reintroduce them: '+
                         ' | '.join(failures[:-1]) if failures[:-1] else '')+
                        '\nReturn only the complete corrected JSON object, with no analysis before or after it.'+PLAN_FIELD_RULES,
                        tokens=12000, prefix=prefix, reserve_cached=later)
            if text.strip() == rejected.strip():
                # A repair that returns the rejected plan unchanged will not
                # pass on a further paid attempt (Microsoft replay, 2026-11-15).
                failures.append('the repair returned the rejected plan unchanged')
                if single:
                    state['plan_repair_reasons'] = list(failures)
                return {'status': 'failed', 'repair_reasons': failures,
                        'reason': 'Valuation plan repair returned the rejected plan unchanged: '+str(exc)}


def _discard_plan_stages(state, checkpoint):
    # A saved response is replayed on resume. Keeping plans that cannot validate
    # would stop every later attempt at the same point, so the rejected plans and
    # their repairs are discarded; their recorded spending is retained.
    for name in [name for name in state['stages'] if _sample_of_stage(name) is not None]:
        state['stages'].pop(name)
    checkpoint()


def _consensus_summary(report):
    """The part of the consensus report saved with the valuation."""
    return consensus.summary(report)


def _attach_consensus(state, plan=None, result=None):
    """Carry the forecast-sample record into the valuation of a model-written plan.

    ``plan`` and ``result`` default to the plan and valuation in state. A plan whose
    forecast inputs no longer match the combined plan (a forecast correction after
    review) is marked edited, so its sample range is not shown as current.
    """
    report = state.get('plan_consensus')
    plan = state.get('plan') if plan is None else plan
    result = state.get('valuation') if result is None else result
    if not report or result is None or not isinstance(plan, dict) or state.get('provided_forecast_digest'):
        return
    summary = _consensus_summary(report)
    if report.get('plan_fingerprint') and consensus.fingerprint(plan) != report['plan_fingerprint']:
        summary['edited_after_consensus'] = True
        summary['current_values'] = {name: ((result.get('scenarios') or {}).get(name) or {}).get('per_share_value')
                                     for name in SCENARIO_NAMES}
    result['forecast_consensus'] = summary


def _requested_samples(state):
    """How many forecast plan samples the run asked for, from its budget record; None when not recorded."""
    preflight = state.get('budget_preflight') if isinstance(state.get('budget_preflight'), dict) else {}
    value = preflight.get('plan_samples')
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _consensus_record(result):
    """The forecast-sample record of a valuation, or None (single forecasts and runs saved before it)."""
    record = (result or {}).get('forecast_consensus') if isinstance(result, dict) else None
    return record if isinstance(record, dict) else None


def _plan_samples_stage(folder, state, call, checkpoint, progress, prompt, prefix, strong, samples):
    """Request the plan ``samples`` times on one prompt and adopt the consensus; one sample is today's stage."""
    single = samples == 1
    # The record of dropped samples applies only to this exact request.
    request = _digest([prompt, prefix, strong])
    saved = state.get('plan_sample_log') if isinstance(state.get('plan_sample_log'), dict) else {}
    log = ({row['sample']: row for row in saved.get('samples', []) if isinstance(row, dict)}
           if saved.get('request_sha256') == request else {})
    valid, dropped = [], []
    for sample in range(1, samples+1):
        if log.get(sample, {}).get('status') == 'dropped':
            # Recorded before a stop: kept dropped, so a resumed run pays for nothing new.
            dropped.append({'sample': sample, 'reason': log[sample]['reason']})
            continue
        if not single:
            progress(f'Forecast plan sample {sample} of {samples}')
        try:
            outcome = _draft_plan(state, call, prompt, prefix, strong, sample, single)
        except core.IncompleteResponse as exc:
            # A cut-off or filtered later sample is spent and dropped; the first stops the run as before.
            if sample == 1:
                raise
            outcome = {'status': 'failed', 'reason': str(exc)[:600]}
        if outcome['status'] == 'unavailable' and sample == 1:
            state['valuation_missing'] = outcome['plan']
            state['plan'] = outcome['plan']
            return
        if outcome['status'] == 'unavailable':
            outcome = {'status': 'failed', 'reason': 'this sample returned unavailable while sample 1 returned a plan'}
        if outcome['status'] == 'failed':
            if single:
                _discard_plan_stages(state, checkpoint)
                raise RuntimeError(outcome['reason']+'. The rejected plan was discarded; run the same command again for '
                                   'a fresh forecast')
            dropped.append({'sample': sample, 'reason': outcome['reason'][:600]})
            log[sample] = {'sample': sample, 'status': 'dropped', 'reason': outcome['reason'][:600],
                           'repair_reasons': outcome.get('repair_reasons', [])}
        else:
            outcome['sample'] = sample
            valid.append(outcome)
            log[sample] = {'sample': sample, 'status': 'valid', 'repair_reasons': outcome['repair_reasons']}
        if not single:
            state['plan_sample_log'] = {'request_sha256': request, 'samples': [log[key] for key in sorted(log)]}
            checkpoint()
    # Sampling is over: the record of drops moves into the consensus report.
    state.pop('plan_sample_log', None)
    if not valid:
        state['plan_repair_reasons'] = [row['reason'] for row in dropped]
        _discard_plan_stages(state, checkpoint)
        raise RuntimeError(f'All {samples} forecast plan samples remained invalid after their repairs: '+
                           ' | '.join(f'sample {row["sample"]}: {row["reason"]}' for row in dropped)[:1500]+
                           '. The rejected plans were discarded; run the same command again for fresh forecasts')
    report = None
    chosen = valid[0]
    if not single:
        held = _reported_expense_shares(valid[0]['plan'])
        proposals = [copy.deepcopy(item['proposal']) for item in valid]
        valuations, numbers = [item['result'] for item in valid], [item['sample'] for item in valid]
        try:
            candidate, report = consensus.consensus_plan(proposals, valuations=valuations, held_shares=held, numbers=numbers)
        except consensus.LOCK_ERRORS as exc:
            # Samples that each passed but cannot be combined: the closest one, recorded with the reason.
            candidate, report = consensus.closest_sample(proposals, valuations, numbers, str(exc))

        def lock(plan):
            outcome = _omit_invalid_optional_sections(plan, lambda item: _lock_and_value(item, state))
            return outcome[0][2], outcome
        if report['method'] in consensus.SINGLE_METHODS:
            # One sample, whole: its own lock and valuation stand.
            chosen = next(item for item in valid if item['sample'] == report['representative_sample'])
            consensus.finish_report(report, chosen['result'], 'single_valid_sample' if report['method'] == 'single_sample'
                                    else report['method'])
        else:
            candidate, ((locked, changes, result), omitted) = consensus.lock_consensus(
                candidate, report, lock, {item['sample']: item['proposal'] for item in valid})
            chosen = {'proposal': candidate, 'plan': locked, 'changes': changes, 'result': result, 'omitted': omitted}
        report.update(requested_samples=samples, dropped=dropped)
        report['method_note'] = consensus.method_note(report, (chosen['plan'].get('reported_inputs') or {}).get('currency', ''))
        report['consensus_proposal'] = candidate
        report['plan_fingerprint'] = consensus.fingerprint(chosen['plan'])
        if len(valid) > 1 and report.get('plan_used') == 'consensus':
            # Ranked by value: each sample's driver group swapped into the plan valued. A diagnostic:
            # nothing in it may stop a run whose samples are already paid for.
            try:
                report['value_impact'] = consensus.value_impact(
                    chosen['plan'], chosen['result'], [(item['sample'], item['plan'], item['result']) for item in valid])
            except Exception as exc:  # noqa: BLE001
                report['value_impact'] = {'error': f'{type(exc).__name__}: {str(exc)[:300]}', 'groups': []}
    state.setdefault('actual_input_corrections', []).extend(chosen['changes'])
    _reset_plan_notes(state)
    _record_omitted_sections(state, chosen['omitted'], 'forecast plan\'s')
    state['plan'], state['valuation'] = chosen['plan'], chosen['result']
    state['plan_conventions'] = PLAN_CONVENTIONS
    if report is not None:
        state['plan_consensus'] = report
        core.save(folder/'plan-consensus.json', report)
        _attach_consensus(state)
    save_valuation(folder, chosen['plan'], chosen['result'])


def _saved_json(path, kind):
    """A saved run file as a JSON object, or a ValueError naming the file and the way on.

    The program's own saves replace the file in one step (core.save), so a damaged file
    comes from outside it: a sync client, a copy cut short or a manual edit.
    """
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        reason = None if isinstance(value, dict) else f'it holds a JSON {type(value).__name__}, not an object'
    except UnicodeDecodeError:
        reason = 'it is not UTF-8 text'
    except json.JSONDecodeError as exc:
        reason = ('it is empty' if not exc.doc.strip() else
                  f'it is not valid JSON ({exc.msg} at line {exc.lineno}), probably cut short')
    except (OSError, RecursionError, ValueError) as exc:
        reason = f'it cannot be read ({exc})'
    if kind == 'state' and reason is None:
        shapes = {'ledger': list, 'stages': dict, 'sources': list, 'gaps': list}
        wrong = [field for field, shape in shapes.items() if field in value and not isinstance(value[field], shape)]
        if wrong or not all(field in value for field in ('ledger', 'stages', 'sources')):
            reason = 'its ledger, stages or sources are missing or of the wrong type'
    if reason is None:
        return value
    folder = path.parent
    raise ValueError(f'The saved {"run state" if kind == "state" else "run marker"} {path} is damaged: {reason}. '
                     f'To start this run again, move the folder {folder} out of {folder.parent} or delete it, or rerun '
                     'with --output set to a new folder. Its recorded charges are then no longer counted toward --budget.')


def _coverage_stop_text(coverage, gate, gaps, snapshot):
    """(message, recovery) for a run stopped by the reported-input preflight, naming the real cause."""
    def clean(text):
        return re.sub(r'\s+', ' ', text).strip().rstrip('.;')
    fields = ', '.join(coverage.get('missing_fields', [])) or 'the reported inputs'
    if gate.get('refused'):
        return ('Reported-input preflight stopped before paid research: ' + clean(gate['message']) + '.',
                'This issuer needs a financial-institution valuation model, which this tool does not provide')
    if not coverage.get('recognized_primary_source_ids'):
        # Nothing primary was collected: the cause is access, never the filings themselves.
        no_contact = any('SEC contact email required' in str(gap) for gap in gaps)
        cause = ('the supplied snapshot holds no SEC company facts or filings' if snapshot else
                 'no SEC contact email was set' if no_contact else
                 'the SEC could not be reached (check the network connection to sec.gov)')
        recovery = ('Supply a snapshot that includes the SEC company facts and filings, or start a fresh run without '
                    '--snapshot' if snapshot else
                    'Set SEC_CONTACT_EMAIL or pass --sec-contact you@example.com, then rerun the same command' if no_contact
                    else 'Rerun the same command once sec.gov is reachable; collection is retried')
        return (f'Reported-input preflight stopped before paid research: no SEC data could be fetched, because {cause}, '
                f'so {fields} could not be taken from the filings.', recovery + ', or supply primary disclosures with --sources')
    conflicts = [clean(item) for item in coverage.get('conflicts', []) if clean(item)]
    notes = [clean(item) for item in coverage.get('notes', []) if clean(item)]
    message = 'Reported-input preflight stopped before paid research: ' + ('; '.join(conflicts) if conflicts else
        'SEC data was fetched, but it gives no usable figure for ' + fields) + '.'
    if notes:
        message += ' ' + '. '.join(note[0].upper() + note[1:] for note in notes) + '.'
    elif not conflicts:
        message += (' This tool reads US-GAAP figures, so a filer reporting under IFRS (Form 20-F) is not covered.')
    recovery = ('Supply reviewed primary disclosures with --sources' +
                (', or start a fresh run without --snapshot' if snapshot else ''))
    return message, recovery


def run(ticker, key, *, profile='balanced', budget=DEFAULT_BUDGET, directory='research-output', evidence='',
        easy_model=None, strong_model=None, progress=lambda message: None, max_revisions=DEFAULT_MAX_REVISIONS, review_notes='', extra_sources=None, counterfactuals=None, draft=None, review_model=None,
        source_snapshot=None, forecast_plan=None, plan_samples=None):
    ticker = core.symbol(ticker)
    if plan_samples is None:
        plan_samples = default_plan_samples(profile)
    if isinstance(plan_samples, bool) or not isinstance(plan_samples, int) or not 1 <= plan_samples <= MAX_PLAN_SAMPLES:
        raise ValueError(f'plan_samples must be an integer from 1 to {MAX_PLAN_SAMPLES}')
    if source_snapshot is not None:
        if (not isinstance(source_snapshot, list) or not 1 <= len(source_snapshot) <= 80 or
                len(json.dumps(source_snapshot).encode()) > 3000000):
            raise ValueError('Source snapshot must contain 1–80 source records within 3 MB')
        for index, source in enumerate(source_snapshot, 1):
            if (not isinstance(source, dict) or source.get('id') != f'S{index}' or
                    not all(field in source for field in ('title','kind','data'))):
                raise ValueError('Source snapshot records require consecutive S1… IDs, title, kind and data')
        parse_json(json.dumps(source_snapshot))
        # Before any paid stage: a snapshot of one company must not become another's memo.
        from .inputs import snapshot_entity_conflict
        mismatch=snapshot_entity_conflict(ticker,source_snapshot)
        if mismatch:
            raise ValueError(mismatch)
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
    base=Path(directory)/ticker
    def locate(day):
        parts=[VERSION,ticker,profile,easy,strong,evidence,source_snapshot,day.isoformat()]
        if plan_samples!=default_plan_samples(profile):
            # A non-default sample count is its own run; the default keeps existing run folders.
            parts.append(['plan_samples',plan_samples])
        ident=_digest(parts)[:16]
        found=base/('full-'+ident)
        if not found.exists():
            # Preserve legacy metered history, without moving a possibly active run.
            legacy=base/('full-'+_digest(parts+[core.SYSTEM])[:16])
            if legacy.is_dir():
                found=legacy
            elif base.is_dir():
                for marker in sorted(base.glob('full-*/run-identity.json')):
                    try:
                        marked=_saved_json(marker,'marker').get('id')
                    except ValueError:
                        # Another run's damaged marker must not block this one.
                        progress(f'Skipped {marker}: it is not a readable run marker.')
                        continue
                    if marked==ident:
                        found=marker.parent
                        break
        return parts,ident,found
    today=datetime.now(timezone.utc).date()
    identity_parts,identity,folder=locate(today)
    if not folder.exists():
        # The identity carries the UTC day. An unfinished run from the previous UTC day
        # started within 24 hours is the same research: resume it (see runs.resumable).
        from .runs import resumable
        earlier=locate(today-timedelta(days=1))
        if earlier[2].is_dir() and resumable(earlier[2]):
            identity_parts,identity,folder=earlier
            progress('Resuming the unfinished run from the previous UTC day; finished steps are reused')
    folder.mkdir(parents=True,exist_ok=True)
    with _lock(folder):
        marker_path=folder/'run-identity.json'
        try:
            marker=_saved_json(marker_path,'marker') if marker_path.exists() else {}
        except ValueError:
            # The marker only records this folder's identity, which is rewritten below.
            progress(f'Rewrote {marker_path}: it was not a readable run marker.')
            marker={}
        marker['id']=identity
        if not marker_path.exists():
            marker['started_at']=datetime.now(timezone.utc).isoformat(timespec='seconds')
        if folder.name=='full-'+_digest(identity_parts+[core.SYSTEM])[:16]:
            # The legacy folder digest itself establishes which SYSTEM was
            # used by its old stage keys, which did not include that field.
            marker.setdefault('legacy_stage_system_sha256',_digest(core.SYSTEM))
        core.save(marker_path,marker)
        default_reviewer=_default_reviewer(profile,strong)
        return _run(folder,ticker,key,easy,strong,budget,evidence,progress,max_revisions,review_notes,extra_sources or [],counterfactuals,draft,review_model,source_snapshot,forecast_plan,default_reviewer=default_reviewer,plan_samples=plan_samples)


def _run(folder,ticker,key,easy,strong,budget,evidence,progress,max_revisions,review_notes,extra_sources,counterfactuals,draft,review_model,source_snapshot=None,forecast_plan=None,default_reviewer=None,plan_samples=1):
    default_reviewer=default_reviewer or strong
    if isinstance(plan_samples,bool) or not isinstance(plan_samples,int) or not 1<=plan_samples<=MAX_PLAN_SAMPLES:
        raise ValueError(f'plan_samples must be an integer from 1 to {MAX_PLAN_SAMPLES}')
    notes_reopened=False
    state_path=folder/'state.json'
    state=_saved_json(state_path,'state') if state_path.exists() else {
        'version':VERSION,'ticker':ticker,'ledger':[],'stages':{},'sources':[], 'gaps':[],
        'iteration':0,'review_history':[],'quality_status':'in_progress','complete':False}
    marker=_saved_json(folder/'run-identity.json','marker')
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
            _write_memo(folder,state['current_memo'],state['sources'],state['gaps'],
                state.get('plan',{}),state.get('valuation'),state)
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
        _write_placeholder(folder,'# '+ticker+': evidence update in progress\n\nNew evidence requires a recomputed model and a fresh review. Previous frozen drafts remain under revisions/.\n')
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
        message,recovery=_coverage_stop_text(coverage,gate,state['gaps'],source_snapshot is not None)
        _write_placeholder(folder,'# '+ticker+': financial input coverage incomplete\n\n'+message+
            '\n\n'+recovery+'. '
            'No paid research was started for this attempt.\n')
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
        _freeze_evidence(state,refresh=True)
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
        pending_report=_write_memo(folder,state['current_memo'],state['sources'],state['gaps'],
            state.get('plan',{}),state.get('valuation'),state)
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
            reviewer=reviewer,forecast_plan=forecast_plan,draft=draft,max_revisions=max_revisions,plan_samples=plan_samples)
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
                '(a planning allowance; the run can cost less), or choose --quick. '
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
    def call(name,model,prompt,tokens=6000,web=False,prefix=None,reserve_cached=False):
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
            if reserve_cached:
                # Not part of the stage key: it changes the reservation, not the request.
                extra['reserve_cached']=True
            try:
                value=router.complete(name,model,prompt,max_tokens=tokens,web=web,**extra)
            except core.ReasoningExhausted:
                # Spent and recorded; one retry without thinking rather than a stopped run.
                checkpoint()
                progress(name+core.WITHOUT_REASONING+': '+model)
                value=router.complete(name+core.WITHOUT_REASONING,model,prompt,max_tokens=tokens,web=web,**extra)
            except core.LengthLimited:
                # Spent and recorded; one retry with more room rather than a stopped run.
                # Mandatory reasoning (Opus 5.5) grows with the prompt and counts in the limit.
                checkpoint()
                progress(name+' (retry with a larger output limit): '+model)
                value=router.complete(name,model,prompt,max_tokens=int(tokens*core.LENGTH_RETRY_FACTOR),web=web,**extra)
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
            ('challenge','strongest credible named outside bull and bear economic arguments, original fund letters/research notes or attributable public reporting, dates, disclosed positions and access scope. When available find a valuation on the other side of the saved quote '+str(reported.get('market_price','unavailable'))+' '+str(reported.get('currency',''))+'; preserve its causal demand/supply/price/mix/margin/capital-allocation case as well as any target. Do not reconstruct an inaccessible broker model'),
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
    # The plan stage writes the cached evidence block; a saved plan keeps its block. So
    # does plan sampling interrupted between samples on unchanged sources: every sample
    # sends the block frozen before sample 1.
    frozen=(state.get('cached_evidence') or {}).get('source_ids')
    sampling=bool(state.get('plan_sample_log')) and frozen==[source.get('id') for source in state['sources']]
    _freeze_evidence(state,refresh='plan' not in state and not sampling)
    context,later=_evidence_blocks(state)
    def evidence_above():
        return EVIDENCE_ABOVE+(LATER_EVIDENCE+later if later else '')
    # The stale-facts note joins the gaps before they are serialized: built after,
    # the first attempt's plan prompts lacked it and a resumed attempt sent different
    # prompts, so every plan sample was paid for again (review, 2026-11-27).
    stale=[source['id'] for source in state['sources'] if isinstance(source,dict) and isinstance(source.get('data'),dict)
           and source['data'].get('fact_origin')=='sec_companyfacts' and
           (source['data'].get('selection') or {}).get('concepts_sha256')!=CONCEPT_SELECTION_SHA256]
    if stale:
        note=(f'SEC facts {", ".join(stale)} were collected under an earlier concept list, so reported cost and expense '
              'lines may be missing from the operating reference. Run in a new --output folder to collect them.')
        if note not in state['gaps']:
            state['gaps'].append(note)
    gaps=json.dumps(state['gaps'])
    pending=state.get('pending_revision')
    resume_revision=_pending_revision_matches(pending,state)
    if pending and not resume_revision:
        state.pop('pending_revision',None)
        checkpoint()
    # The pending prose phase already has its sources, findings and adjudicated
    # model. Re-synthesizing newly added targeted evidence would repay completed
    # work before resuming the saved revision.
    # Only the plan and the first draft read the synthesis. Once both exist it is
    # not requested again: a rerun after targeted research had added sources
    # otherwise paid for a new synthesis that no later stage reads.
    thesis='' if resume_revision or (draft is not None and (state.get('plan') or forecast_plan is not None)) or (
        'plan' in state and state.get('current_memo')) else call('evidence_synthesis',easy,
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
                'List the five highest-impact uncertainties.\nEvidence:\n'+context,tokens=EVIDENCE_SYNTHESIS_TOKENS)
    if 'plan' not in state:
        anchor_revenue=(reported.get('actuals') or {}).get('revenue')
        prompt=('Build an investment valuation plan for '+ticker+'. Ground actuals in supplied evidence; '
                'use primary reports where accessible, and reconcile secondary-series dates and definitions.\n'+
                FORECAST_SCHEMA+_rate_convention(state['sources'],reported.get('currency'))+UNDERWRITING_GUIDANCE+PLAN_FIELD_RULES+PLAN_SOURCE_RULE+'\nIMMUTABLE reported inputs (copy these values exactly; forecast judgments only):\n'+_prompt_json(reported)+
                (('\nReinvestment computed from reported lines (reference for years 1-3 reinvestment and sales_to_capital):\n'+
                  _prompt_json(reinvestment_reference(state['sources'],anchor_revenue)))
                 if reinvestment_reference(state['sources'],anchor_revenue) else '')+
                (('\nReported operating margins (reference for the final-year margin ceilings):\n'+
                  _prompt_json(cost_path_reference(state['sources'],reported.get('operating_reference'),anchor_revenue)))
                 if cost_path_reference(state['sources'],reported.get('operating_reference'),anchor_revenue) else '')+
                base_rates.plan_context(reported)+
                evidence_above()+'\nDiligence synthesis:\n'+thesis+'\nRetrieval gaps:\n'+gaps+
                # A rerun after a discarded plan would otherwise resend the identical request.
                ('\nAn earlier attempt at this plan was rejected for these reasons; avoid them: '+
                 ' | '.join(state['plan_repair_reasons']) if state.get('plan_repair_reasons') else ''))
        _plan_samples_stage(folder,state,call,checkpoint,progress,prompt,_evidence_prefix(context),strong,plan_samples)
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
        _attach_consensus(state)
        save_valuation(folder,state['plan'],state['valuation'])
    plan,result=state['plan'],state.get('valuation')
    valuation_context=_model_valuation_context(plan,result)
    state['prompt_context_metrics'] = {
        'full_json_utf8_bytes':len(json.dumps({'assumptions':plan,'computed':result},ensure_ascii=False).encode()),
        'presentation_utf8_bytes':len(valuation_context.encode()),
        'basis':'Deterministic presentation size, not a token count or a promised cost saving. Full saved math remains authoritative.'}
    if not state.get('current_memo'):
        draft=call('full_draft',strong,
            WRITER_OPENING+ticker+', '+MEMO_WORDS+'. '+SECTIONS+
            '\nThe FIRST PAGE below is placed above your text. Copy any computed or reported figure exactly as the first '
            'page, the COMPUTED FROM REPORTED FILINGS tables, the valuation output or a source shows it, in the memo\'s '
            'number style. Calculate no new figures (growth rates, margins, per-share amounts, differences or sums); if a '
            'figure you need is missing, make the point without it. Cite every factual claim with [S#] from the evidence, '
            'placed directly after the reported or sourced figure it supports; a causal claim (driven by, reflected, '
            'helped by) needs a source that states that cause. Name a model output as ours or computed ("our forecast", '
            '"the computed exit multiple"); it carries no [S#].\n'+
            EVIDENCE_TIER_RULE+'\n'+RUBRIC+'\n'
            'Take monitoring, counterfactual, multiple and repurchase figures from the computed cases and say which '
            'inputs each holds fixed; reverse-DCF and implied-return figures are conditional on named fixed assumptions and describe no '
            'market belief. GAAP EBIT already expenses stock compensation, which is a real cost. Do not invent '
            'transcripts, guidance, consensus, dates or quotes. Valuation research only: no ratings, target prices, '
            'entry levels, margins of safety, upside or downside, or position sizing; write value / price. '
            'Do not reproduce outside ratings, price targets or recommendations either, even attributed or labelled '
            'unverified; give an outside view as its economic argument. Write plain analytical prose without slogans or '
            'superlatives. Never mention Python, prompts or instructions, and add no tables of your own.'
            '\nFIRST PAGE (placed above your text):\n'+computed_summary(plan,result)+
            evidence_above()+'\nSynthesis:\n'+thesis+'\nValuation:\n'+valuation_context+'\nGaps:\n'+gaps,tokens=MEMO_TOKENS,
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
        pending_report=_write_memo(folder,memo,state['sources'],state['gaps'],plan,result,state)
        core.save(folder/'source-ledger.json',claim_ledger(pending_report))
        checkpoint()
        corrected=call(f'revise_{iteration+1}',strong,
            'Revise the complete memo to address every material finding below. Do not hide missing evidence '
            'or change actuals/valuation numbers to make a review disappear. Use the newly recomputed model below '
            'and drop stale values from the prior draft. Explain material valuation changes and why the evidence '
            'or our assumptions justified them. Retain all required decision sections and specific citations. '
            'Correct verified defects and preserve legitimate disclosed assumptions; do not copy invalid reviewer '
            'accusations or unsupported fixes into reader prose. A valid narrative finding still needs correction '
            'when the model response says it has no forecast impact; do not confuse that with a false accusation. '
            'Distinguish evidence that falsifies our '
            'valuation conclusion from evidence that falsifies a competing bullish/bearish case. '
            'Use computed_diagnostics for sensitivity statements; do not mentally invent tax or WACC ranges. '
            'All reverse-DCF statements are conditional on fixed named assumptions and explicit horizon. '
            'Keep the source-to-driver links, the strongest sourced opposing case and the answer to it, the rate '
            'build, the dated monitoring cases, the computed exit multiple and the repurchase comparison with our '
            'value, each once, in the section that holds it. '
            'Adopt a reviewer-proposed number only when it appears in the computed output or a source; otherwise '
            'keep the computed figure. Copy computed and reported figures exactly and do not calculate new ones; '
            'name a model output as ours or computed, never narrating the table, and place each [S#] after '
            'the sourced figure it supports. Python places the first page above your text and the valuation detail '
            'after it; do not re-type their tables. Do not resolve a finding by inventing '
            'a policy discount, nonrecurring adjustment, source passage or double-counted cash distribution. '
            'Return the full revised memo only, '+word_limit(memo)[1]+' words of prose in total; Python adds the tables. '
            'The current prose is '+str(prose_words(memo))+' words; the revision must not be longer, and should be '
            'shorter where it exceeds that budget. Keep the current headings and their order.\n'+EVIDENCE_TIER_RULE+'\n'+RUBRIC+evidence_above()+
            ('\nNo forecast response was requested for these findings; the valuation below is unchanged.'
             if isinstance(candidate,dict) and candidate.get('skipped') else '')+
            ('\nThe forecast correction for these findings was rejected; the valuation below is unchanged. Do not '
             'describe any forecast change as made.' if isinstance(candidate,dict) and candidate.get('correction_rejected') else '')+
            ('\nNotes on the plan now valued:\n'+'\n'.join(state.get('plan_gap_notes',[])) if state.get('plan_gap_notes') else '')+
            '\nValuation:\n'+valuation_context+'\nMemo:\n'+memo+'\nFindings:\n'+json.dumps(combined)+'\nAdjudicated model response:\n'+json.dumps(candidate),tokens=MEMO_TOKENS,
            prefix=_evidence_prefix(context))
        state['current_memo']=_label_unverified(corrected,state['sources'])
        state['iteration']=iteration+1
        state.pop('pending_revision',None)
        checkpoint()

    def forecast_response(iteration,memo,combined):
        """The forecast response to a review: (adjudication, decision), and the recomputed model when corrected."""
        nonlocal plan,result,valuation_context
        apply=not state.get('provided_forecast_digest')
        rate_text=_correction_rate_text(state['sources'],plan,apply)
        # Python's own fields are not offered for editing (see PYTHON_OWNED_PLAN_FIELDS).
        sent_view=_forecast_view(plan,apply)
        model_response=call(f'model_response_{iteration+1}',strong,
            'Address these review findings. Return ONLY JSON {"unchanged":true,"reason":"..."} '
            'if the existing valuation plan is still appropriate, otherwise return the COMPLETE corrected '
            'forecast proposal in the schema below. Do not change source-supported actuals just to get a pass. '
            'First verify each reviewer accusation against the actual memo, sources and model. Reject unsupported '
            'accusations or suggested fixes instead of replacing one unsupported story with another. A valid '
            'prose-only finding may leave forecasts unchanged; label it memo_only and keep it. Include compact '
            'finding_dispositions mapping finding IDs to accepted/rejected/clarified with source-backed reasons. '
            'Preserve or explicitly rejustify the repurchase illustrations when forecasts change; '
            'do not silently drop their scope or invent a discount to satisfy a review. '
            'Forecast changes must be explicit house assumptions and economically justified. '
            +FORECAST_SCHEMA+rate_text+UNDERWRITING_GUIDANCE+PLAN_FIELD_RULES+PLAN_SOURCE_RULE+
            evidence_above()+'\nCurrent memo (verify allegations against this exact text):\n'+memo+'\nCurrent forecast proposal:\n'+json.dumps(sent_view)+
            '\nLocked actuals (reference only; omit from your proposal):\n'+_prompt_json(plan.get('actuals',{}))+
            '\nReported operating lines (reference for year-1 continuity):\n'+
            _prompt_json((plan.get('reported_inputs') or {}).get('operating_reference'))+
            ('\nReinvestment computed from reported lines (reference for years 1-3 reinvestment):\n'+_prompt_json(plan['reinvestment_reference'])
             if plan.get('reinvestment_reference') else '')+
            ('\nReported operating margins (reference for the final-year margin ceilings):\n'+_prompt_json(plan['cost_path_reference'])
             if plan.get('cost_path_reference') else '')+
            base_rates.result_context(result)+
            '\nComputed diagnostics for verification:\n'+_prompt_json((result or {}).get('computed_diagnostics',{}))+
            '\nComputed counterfactuals:\n'+_prompt_json((result or {}).get('counterfactuals',{}))+
            '\nComputed reverse-DCF projections:\n'+_prompt_json((result or {}).get('reverse_dcf',{}).get('solution_projections',[]))+
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
                proposal=_restore_python_fields(proposal,plan,sent_view,apply)
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
        # "Corrected" counts only when a forecast input changed (consensus.fingerprint).
        # A response that rewrote text alone keeps the valuation: no rewrite of the
        # memo, and no new valuation for the reviewer unless model-owned text changed.
        text_changes=[]
        if (decision=='corrected' and result is not None and isinstance(plan,dict) and
                consensus.fingerprint(locked)==consensus.fingerprint(plan)):
            text_changes=[key for key in MODEL_TEXT_FIELDS if locked.get(key)!=plan.get(key)]
            decision='text_only' if text_changes else 'unchanged'
            candidate={'unchanged':True,'reason':'The response changed no forecast input'+
                       (' and only model-owned text: '+', '.join(text_changes) if text_changes else
                        '; text Python writes is rebuilt at lock')+'. The valuation is unchanged.',
                       'finding_dispositions':dispositions}
        if decision=='unchanged':
            adjudication=copy.deepcopy(candidate)
            if repair:
                adjudication['correction_repairs']=repair
        elif decision=='text_only':
            # The same forecast inputs, so the same values; the changed text is re-rendered.
            # The response's locked plan has the same inputs and Python's fields rebuilt; only the text differs.
            plan,result=locked,corrected
            _attach_consensus(state,plan,result)
            save_valuation(folder,plan,result)
            state['plan'],state['valuation']=plan,result
            valuation_context=_model_valuation_context(plan,result)
            adjudication=dict(candidate,text_changed=text_changes)
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
                _attach_consensus(state,plan,result)
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
            valuation_context=_model_valuation_context(plan,result)
        core.save(reviews/f'model-response-{iteration+1}.json',adjudication)
        return adjudication,decision

    def save_pending(iteration,memo,combined,adjudication,mode,manifest,required=()):
        # The committed findings, forecast response and next step, so an
        # interrupted round resumes without paying for the review again.
        state['pending_revision']={
            'iteration':iteration,
            'memo_sha256':hashlib.sha256(memo.encode()).hexdigest(),
            'plan_sha256':_digest(plan),'valuation_sha256':_digest(result),
            'sources_sha256':_digest(state['sources']),
            'external_notes_digest':state.get('external_notes_digest'),
            'review_manifest':copy.deepcopy(manifest),
            'findings':copy.deepcopy(combined),'model_response':copy.deepcopy(adjudication),
            'mode':mode,'edit_required':list(required)}
        checkpoint()

    def record_computed_text(iteration,combined,stuck):
        # Findings on text Python writes, kept for a code fix: in state for Review status,
        # and in computed-text-findings.json for collection across runs.
        if stuck:
            state['computed_text_findings']={'iteration':iteration,'ids':list(stuck)}
        else:
            state.pop('computed_text_findings',None)
        core.save(folder/'computed-text-findings.json',{
            'iteration':iteration,'review_model':reviewer,
            'basis':'Blockers or majors that quote text Python writes (first page, valuation detail, tables, method notes) '
                    'and name no forecast input, or were already answered without a forecast change. No revision stage '
                    'can change that text; fix it in code.',
            'findings':[{key:f.get(key) for key in ('id','severity','rule','issue','current_excerpt','fix','source_refs')}
                        for f in combined if isinstance(f,dict) and f.get('id') in set(stuck)]})
        checkpoint()

    def stop_on_computed_text(iteration,stuck):
        state['complete']=False
        state['quality_status']='unresolved_findings'
        state.pop('pending_revision',None)
        checkpoint()

    def note_round(number,**fields):
        rounds=state.setdefault('revision_rounds',[])
        entry=next((item for item in rounds if item.get('round')==number and item.get('cycle')==state.get('revision_base',0)),None)
        if entry is None:
            entry={'round':number,'cycle':state.get('revision_base',0)}
            rounds.append(entry)
        entry.update(fields)
        checkpoint()

    def edit_round(iteration,memo,combined,adjudication,required,manifest):
        """Answer the findings with exact edits to the prose, with one retry for required findings left unanswered."""
        number=iteration+1
        material={f.get('id') for f in combined if isinstance(f,dict) and f.get('severity') in {'blocker','major'}}
        shown=[{key:f[key] for key in ('id','severity','rule','issue','current_excerpt','fix','source_refs') if f.get(key) is not None}
               for f in combined if isinstance(f,dict)]
        prompt=(EDIT_INSTRUCTIONS+'\n'+EVIDENCE_TIER_RULE+'\n'+RUBRIC+
                ('' if adjudication.get('skipped') else
                 '\nForecast response to these findings (the valuation below is unchanged):\n'+json.dumps(adjudication))+
                '\nFindings:\n'+json.dumps(shown)+
                '\nCOMPUTED SUMMARY:\n'+computed_summary(plan,result)+evidence_above()+
                '\nComputed exhibits:\n'+valuation_section(plan,result)+
                '\nMemo:\n'+memo)
        try:
            text=call(f'prose_edits_{number}',strong,prompt,tokens=EDIT_TOKENS,prefix=_evidence_prefix(context))
        except core.IncompleteResponse as exc:
            # Charged and recorded; an unusable answer falls back to the rewrite.
            text=''
            progress(f'Prose edits not applied: {exc}'.split(', so it')[0])
        edits,flags=_edit_response(text)
        # The edit call may reopen the forecast only for a finding that quotes the prose. Text Python
        # writes reaches the forecast response only when the finding names a forecast input, which
        # _forecast_findings already routes (live run, 2026-11-27: a method-note finding paid two rounds).
        quoted={f.get('id') for f in combined if isinstance(f,dict) and _in_prose(f,memo)}
        flagged=[item for item in flags if item['finding'] in material and item['finding'] in quoted]
        if flagged and adjudication.get('skipped'):
            # The author says only a forecast change answers these: reopen the forecast once.
            note_round(number,forecast_reasons=[[item['finding'],'the edit call says only a forecast change can answer it']
                                                for item in flagged])
            adjudication,decision=forecast_response(iteration,memo,combined)
            note_round(number,forecast_response=decision)
            if decision in ('corrected','unavailable'):
                core.save(reviews/f'edits-{number}.json',{'iteration':iteration,'round':number,'edits':[],'forecast':flags,
                          'required':list(required),'unanswered':[],'outcome':'forecast_changed'})
                save_pending(iteration,memo,combined,adjudication,'rewrite',manifest)
                note_round(number,revision='rewrite',rewrite_reason='the plan changed')
                finish_revision(iteration,memo,combined,adjudication)
                return
            # The forecast stands: ask for the edits again with its dispositions in view.
            save_pending(iteration,memo,combined,adjudication,'edits',manifest,required)
            return edit_round(iteration,memo,combined,adjudication,required,manifest)
        computed=(result,plan,_computed_tables(state['sources']))+_rounded_inputs(plan,result)
        pools=trace_pools(state['sources'],computed)
        by_id={f.get('id'):f for f in combined if isinstance(f,dict)}
        def apply(current,proposed):
            out,records=_apply_finding_edits(current,proposed or [],by_id,pools,_dollar_filer(plan,result),_valued_figures(result))
            return _label_unverified(out,state['sources']),records
        edited,records=apply(memo,edits)
        answered={item['finding'] for item in records if item['applied']}
        missing=[identifier for identifier in required if identifier not in answered]
        retry_records=[]
        if missing:
            # One retry for just the findings whose edit did not apply, each told why. A
            # rewrite after two live rounds (GOOGL, KO, 2026-11-27) brought new findings;
            # an edit that still fails is carried to the next review instead.
            reasons={identifier:_unapplied_reason(identifier,records,text,edits) for identifier in missing}
            retry=(EDIT_INSTRUCTIONS+'\n'+EVIDENCE_TIER_RULE+'\n'+RUBRIC+
                   '\nThe edits below were not applied. Answer only these findings, and avoid the stated reason.\n'+
                   'Findings and reasons:\n'+json.dumps([dict({key:by_id[i][key] for key in
                       ('id','severity','rule','issue','current_excerpt','fix','source_refs') if by_id[i].get(key) is not None},
                       not_applied=reasons[i]) for i in missing])+
                   '\nCOMPUTED SUMMARY:\n'+computed_summary(plan,result)+evidence_above()+
                   '\nComputed exhibits:\n'+valuation_section(plan,result)+
                   '\nMemo:\n'+edited)
            try:
                retry_text=call(f'prose_edits_{number}_retry',strong,retry,tokens=EDIT_TOKENS,prefix=_evidence_prefix(context))
            except core.IncompleteResponse as exc:
                retry_text=''
                progress(f'Prose edit retry not applied: {exc}'.split(', so it')[0])
            retry_edits,_=_edit_response(retry_text)
            retry_edits=[edit for edit in retry_edits or [] if isinstance(edit,dict) and edit.get('finding') in missing]
            edited,retry_records=apply(edited,retry_edits)
            answered|={item['finding'] for item in retry_records if item['applied']}
            missing=[identifier for identifier in required if identifier not in answered]
        after=prereview_checks(_with_summary(edited,plan,result,state['sources'],ticker),state['sources'],computed) if result else []
        log={'iteration':iteration,'round':number,'edits':records,'forecast':flags,'required':list(required),
             'retry':retry_records,'unanswered':missing,'free_checks_after':[issue['id'] for issue in after],
             'outcome':'carried' if missing else 'edited'}
        if missing:
            log['unanswered_reasons']={i:_unapplied_reason(i,records+retry_records,'x',[]) for i in missing}
        if not text:
            log['note']='the edit response was incomplete and was not used'
        elif edits is None:
            log['note']='no complete edits object could be parsed from the response'
        core.save(reviews/f'edits-{number}.json',log)
        # Without a forecast change there is no rewrite: findings still unanswered go to the
        # next review as unresolved prose and use up rounds as any other finding does.
        state['current_memo']=edited
        state['iteration']=number
        state.pop('pending_revision',None)
        every=records+retry_records
        note_round(number,revision='edits',edits_applied=sum(1 for item in every if item['applied']),
                   edits_rejected=sum(1 for item in every if not item['applied']),
                   **({'carried_unanswered':missing} if missing else {}))

    while _revisions_used(state)<=max_revisions:
        iteration=state['iteration']
        memo=state['current_memo']
        pending=state.get('pending_revision')
        if _pending_revision_matches(pending,state):
            if _revisions_used(state)>=max_revisions:
                raise ValueError(f'Increase max_revisions to at least {_revisions_used(state)+1} to resume the pending prose revision')
            if pending.get('mode')=='edits':
                edit_round(iteration,memo,pending['findings'],pending['model_response'],
                           pending.get('edit_required') or [],pending.get('review_manifest'))
            else:
                finish_revision(iteration,memo,pending['findings'],pending['model_response'])
            continue
        if pending:
            state.pop('pending_revision',None)
            checkpoint()
        # Free checks before the paid review, and one targeted fix call when they
        # find defects (untraced or unlabelled figures, added convention rows,
        # length). Edits are exact quote replacements applied by Python.
        computed_inputs=(result,plan,_computed_tables(state['sources']))+_rounded_inputs(plan,result)
        issues=prereview_checks(_with_summary(memo,plan,result,state['sources'],ticker),state['sources'],computed_inputs) if result else []
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
        # The same problems that an earlier fix call in this cycle left in place are
        # not sent again: edits leave most sentences as they were, so a second call
        # would be asked to fix exactly what it already declined to change.
        attempted=_digest(fixable)
        repeated=bool(fixable) and not done and any(
            isinstance(record,dict) and record.get('remaining_sha256')==attempted and
            record.get('cycle',0)==state.get('revision_base',0) for record in fixed.values())
        if repeated:
            fixed[f'{iteration}:{memo_key}']={'memo_sha256':memo_key,'fixed_memo_sha256':memo_key,
                                             'issues':[issue['id'] for issue in issues],'edits_applied':0,
                                             'skipped_call':'the same problems remained after an earlier fix call',
                                             'cycle':state.get('revision_base',0),'remaining_sha256':attempted}
            checkpoint()
        elif fixable and not done:
            try:
                response=call(f'draft_check_fix_{iteration}',strong,
                'Correct only the problems listed below in this memo draft. Return ONLY JSON {"edits":[{"quote":"exact '
                'contiguous text copied from the memo","replacement":"corrected text"}]}. Each quote must appear exactly '
                'once in the memo; keep each quote to one sentence or less. For an untraced figure, use the exact figure from '
                'the computed summary, the COMPUTED FROM REPORTED FILINGS tables, the valuation output or a primary source, '
                'or remove the figure. For a figure found only in unverified excerpts, cite the primary source that contains '
                'it, label it as unverified context, or remove it. For an unverified company fact (unverified-company-fact): '
                +UNVERIFIED_FACT_FIX+' Describe convention rows separately, never as adding up. '
                'For an empty or unfinished section, quote its last sentence, or its heading when it has none, and '
                'replace it with that text followed by the rest of the section. For a figure repeated across sections, '
                'keep it in the section that owns it and refer to it by name elsewhere. The In brief may be quoted and '
                'replaced whole. For a style problem, rewrite the sentence to the writing standard below. Change '
                'nothing else.\n'+
                EVIDENCE_TIER_RULE+'\n'+WRITING_STANDARD+'\nProblems:\n'+json.dumps(fixable)+'\nCOMPUTED SUMMARY:\n'+computed_summary(plan,result)+
                evidence_above()+'\nComputed exhibits:\n'+valuation_section(plan,result)+'\nMemo:\n'+memo,tokens=8000,
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
            memo,applied,skipped=_apply_edits(memo,edits,_dollar_filer(plan,result))
            memo=_label_unverified(memo,state['sources'])
            if not response:
                skipped=['the fix response was incomplete and was not used']
            elif edits is None:
                skipped=['no complete edits object could be parsed from the response']
            fixed[f'{iteration}:{memo_key}']={'memo_sha256':memo_key,'fixed_memo_sha256':hashlib.sha256(memo.encode()).hexdigest()[:16],
                                             'issues':[issue['id'] for issue in issues],'edits_applied':applied,'edits_skipped':skipped[:20]}
            state['current_memo']=memo
            checkpoint()
            issues=prereview_checks(_with_summary(memo,plan,result,state['sources'],ticker),state['sources'],computed_inputs)
            if response and edits is not None:
                # A complete answer that left these problems is not asked again.
                fixed[f'{iteration}:{memo_key}'].update(cycle=state.get('revision_base',0),
                    remaining_sha256=_digest([issue for issue in issues if issue['id']!='length']))
                checkpoint()
        visible=reader_view(memo,state['sources'],plan,result,ticker,state)
        (revisions/f'memo-{iteration}.md').write_text(visible,encoding='utf-8')
        # A review can fail after a new draft/model is saved. Keep the reader
        # download consistent with those artifacts even before review succeeds.
        state['complete']=False
        state['quality_status']='needs_review'
        pending=_write_memo(folder,memo,state['sources'],state['gaps'],plan,result,state)
        core.save(folder/'source-ledger.json',claim_ledger(pending))
        checkpoint()
        manifest={'memo_sha256':hashlib.sha256(visible.encode()).hexdigest(),
                  'sources_sha256':_digest(state['sources']),'valuation_sha256':_digest(result),
                  'review_type':'model_peer_review_not_publication_certification'}
        deterministic=inspect_memo(_inspected_text(memo,state['sources'],plan,result,ticker),state['sources'],result)+[
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
                'standard below, and classify each defect as blocker, major or minor by that standard. A breach of the '
                'writing standard is minor (N2), and other style preferences are no finding. Check: (1) every figure in '
                'the prose against the computed summary, the computed tables, the valuation output or the cited source, '
                'and every comparison with the price by arithmetic; (2) that the prose agrees with the first page, the '
                'tables, the method notes and its own other sections: a contradiction is major under M7 whatever its '
                'value effect, and the finding quotes the prose side and names what it contradicts; (3) that the memo does not contradict the computed account of which inputs alone reach the '
                'price within the ranges tested, given in full by the first-page table "What the price needs" '
                '(explaining why our forecast differs from what the price requires contradicts nothing, and the prose '
                'need not restate the account); (4) that company facts rest on primary sources and unverified excerpts '
                'are labelled context; (5) that the strongest sourced opposing case is answered with evidence; (6) that '
                'no trading-layer language appears; (7) that convention rows are never added together; (8) that '
                'repurchase, multiple and monitoring statements use the computed diagnostics and their fixed inputs; '
                '(9) that no section of the prose is empty or stops mid-sentence (B4). A disclosed forecast assumption '
                'is a judgment and is a defect only when primary evidence contradicts it or it is unexplained. Never '
                'write a fix that asks the author to compute a value the engine has not computed. '
                'Before flagging an omission, check the whole memo. Report every material defect you find and at most '
                'three minor ones; there is no target number of findings. '
                'Every finding must quote one contiguous passage of the CURRENT draft (between the draft markers) verbatim, without added quotation '
                'marks or ellipses; for an omission, quote the heading or sentence where the missing content belongs and '
                'name the omission in issue. Earlier notes can be stale; never report an accusation that the current '
                'draft already resolves. Keep each issue under 100 words and each fix under 70.\n'+
                EVIDENCE_TIER_RULE+'\n'+RUBRIC+ledger+'\n'+REVIEW_SCHEMA+
                '\nThe evidence and the valuation are in the blocks above; the draft is at the end.'+
                '\nDeterministic diagnostics (not semantic proof):\n'+json.dumps(deterministic)+
                '\nNew external notes for this draft only; verify against the CURRENT wording and omit resolved allegations:\n'+fresh_notes+
                ('\nArtifact integrity recheck record: '+state['artifact_review_record']+
                 '. Original state-derived artifacts were reconstructed; deliberate edits require explicit supplied inputs. '
                 'Reassess this frozen draft and model.' if state.get('artifact_review_record') else '')+
                (LATER_EVIDENCE+later if later else '')+_draft_block(visible))
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
            review_prefix=_review_prefix(context,valuation_context)
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
                        '\nThe evidence and the valuation are in the blocks above; the draft is at the end.'+
                        (LATER_EVIDENCE+later if later else '')+_draft_block(visible),tokens=FINDING_CHECK_TOKENS,
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
        report=_write_memo(folder,memo,state['sources'],state['gaps'],plan,result,state)
        core.save(folder/'source-ledger.json',claim_ledger(report))
        checkpoint()
        if not major:
            record_computed_text(iteration,combined,[])
            state['complete']=bool(result)
            if not result:
                state['quality_status']='missing_valuation'
            break
        python_ids={f.get('id') for f in deterministic}|PYTHON_FINDING_IDS
        answered=_answered_forecast(state,_inputs_fingerprint(plan))
        reasons=_forecast_findings(combined,memo,python_ids,answered)
        required=_edit_required(combined,memo,python_ids)
        stuck=_computed_text_findings(combined,memo,python_ids,answered)
        record_computed_text(iteration,combined,stuck)
        if not reasons and not required:
            # Only computed text no stage can change remains: stop with it open.
            stop_on_computed_text(iteration,stuck)
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
        # The cached block stays as written; the new sources follow it uncached.
        context,later=_evidence_blocks(state)
        checkpoint()
        # Review may identify economic or evidentiary defects in the model.
        # Correct the editable plan first and independently recompute it, rather
        # than letting prose quietly contradict an unchanged spreadsheet.
        # The forecast is reopened only for findings a prose edit cannot answer
        # (see _forecast_findings). Live runs (2026-11-27) paid $1.12-1.34 for a
        # forecast response that answered "unchanged" for Microsoft and Apple.
        if reasons:
            adjudication,decision=forecast_response(iteration,memo,combined)
            if decision in ('unchanged','text_only'):
                # Kept for these findings: a later review raising them again does not reopen it.
                note_round(iteration+1,inputs_fingerprint=_inputs_fingerprint(plan),
                           answered=[list(_finding_key(f)) for f in combined if f.get('id') in {item[0] for item in reasons}])
        else:
            adjudication={'skipped':True,'reason':FORECAST_SKIP_REASON,
                          'findings':[f.get('id') for f in combined if f.get('severity') in {'blocker','major'}]}
            decision='skipped'
            core.save(reviews/f'model-response-{iteration+1}.json',adjudication)
        note_round(iteration+1,forecast_response=decision,forecast_reasons=[list(item) for item in reasons])
        if decision in ('corrected','unavailable'):
            # The plan changed, so the computed sections changed: rewrite the prose.
            save_pending(iteration,memo,combined,adjudication,'rewrite',record['manifest'])
            note_round(iteration+1,revision='rewrite',rewrite_reason='the plan changed')
            finish_revision(iteration,memo,combined,adjudication)
            continue
        if not required:
            # The forecast stands and no finding quotes the prose: an edit call would change nothing.
            note_round(iteration+1,revision='none',stop_reason='the forecast stands and no blocker or major quotes the prose')
            stuck=_computed_text_findings(combined,memo,python_ids,_answered_forecast(state,_inputs_fingerprint(plan)))
            record_computed_text(iteration,combined,stuck)
            stop_on_computed_text(iteration,stuck)
            break
        save_pending(iteration,memo,combined,adjudication,'edits',record['manifest'],required)
        edit_round(iteration,memo,combined,adjudication,required,record['manifest'])
    final_report=_write_memo(folder,state['current_memo'],state['sources'],state['gaps'],plan,result,state)
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

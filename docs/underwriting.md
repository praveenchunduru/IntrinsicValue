# Editable underwriting

Full research separates reported facts, analyst assumptions, deterministic
calculations and review. The saved `assumptions.json` is the editable input;
`valuation.json` and the CSV files contain the computed output. A source ID makes
a claim traceable, but its existence alone does not establish source support.

The [revised AAPL example](../examples/AAPL-2026-11-06-revised/memo.md) illustrates
the operating bridge, dated rate build and conditional monitoring cases. Its
figures are a dated snapshot, not defaults for other companies or future runs.

## Start with reported inputs

Use three-letter `currency`, `amount_unit="currency_units"` and
`share_unit="shares"`: money is in whole currency units and shares are whole
shares, not millions. Each actual needs a `source_refs` entry. The full pipeline
pins supported annual revenue, outstanding shares and the debt/cash/claims bridge
to primary inline-XBRL or normalized SEC Companyfacts records. It rejects essential coverage gaps rather than
letting a forecast replace a reported number. A supplied forecast passes through
the same input lock and source-reference checks.

The net-debt bridge deducts selected directly reported cash, marketable securities
and short-term investments exactly once. Inclusive subtotals replace their parts.
Absent optional investment categories are excluded under the disclosed selected-asset
policy, rather than asserted to be zero. Unclassified long-term investments are
excluded. Classifying selected assets as nonoperating is a house policy, not a
reported assertion that all cash is surplus.

Debt can use a complete financial-debt total or reconciled, disjoint short- and
long-term components. A narrow long-term tag is not promoted to whole-company debt just
because a table says "Total debt": automatic classification requires same-filing
inline facts, aligned dates/columns/values and positive evidence of financing scope.
Recorded Microsoft and CoreWeave fixtures test real disclosure shapes; they are
trimmed records and do not demonstrate complete live coverage for those issuers.

Long-term debt must be complete. Short-term financing is then established in this
order, and `net_debt_bridge.short_term_financing.basis` records which one applied:

| Basis | Evidence | Coverage status |
|---|---|---|
| `reported` | A reported short-term borrowing total, or a complete whole-debt total | `pinned` |
| `manual_primary_review` | A reviewed classification with an exact same-source excerpt | `pinned` |
| `reported_current_debt_identity` | Reported current debt equals reported current long-term debt, so short-term borrowing is zero by arithmetic | `pinned` |
| `identified_categories_house_assumption` | Commercial paper and other short-term borrowings as reported, with nothing further assumed | `pinned_with_disclosed_assumption` |

The last basis is a house assumption. It applies only when the run fetched the
whole SEC entity record and that record contains no amount, on the balance-sheet
date, under any other short-term borrowing concept. Most issuers do not tag a
borrowing category they do not have, so the absence of a concept is the only
structured evidence available; it is still weaker than a reported zero. An
undisclosed or differently tagged borrowing would raise debt and lower equity
value. The memo's assumptions label states the assumption whenever it is used.
A positive amount under `LineOfCredit`, `LinesOfCreditCurrent`,
`ShortTermBankLoansAndNotesPayable`, `ShortTermNonBankLoansAndNotesPayable`,
`BankOverdrafts` or `WarehouseAgreementBorrowings` cannot be placed between
short-term financing and long-term debt without issuer disclosure, so it stops
the bridge. A reported total always displaces the assumption, and reported
current debt above the identified categories contradicts it.

Three exact reconciliations handle recurring issuer tagging patterns. Each uses
only reported amounts and is recorded in the bridge:

- `LongTermDebt` equal to `DebtInstrumentCarryingAmount` (gross principal) is set
  aside when gross less the reported unamortized discount and issuance costs equals
  current plus noncurrent carrying debt. The carrying amounts are used.
- A whole-debt total equal to the reported long-term subtotal, alongside separately
  reported positive short-term borrowings, is excluded as narrower than its taxonomy
  scope. The components are used, which is the larger figure.
- A value repeated in prose at a coarser stated inline precision (`decimals`) is a
  duplicate when it lies within half a unit of that precision of the most precisely
  reported amount.

Anything else that conflicts stays a conflict. When both exist for the same year,
total revenues (`us-gaap:Revenues`) are selected over a smaller contract-revenue
amount; a total below contract revenue is a conflict.

Finance leases are deducted once: separately as other equity claims when financial
debt excludes them, or within a selected combined debt-and-finance-lease subtotal.
`net_debt_bridge.finance_leases_included_in_net_debt` records that choice. A missing
separate lease amount stays null if a combined subtotal already covers it; zero
other claims then means no second deduction. Any zero for additional unidentified
claims is a disclosed house assumption. With GAAP operating EBIT, operating rent
remains an expense and operating-lease liabilities are not added to debt. When
the whole SEC entity record reports no finance or capital lease liability concept
on the balance-sheet date, finance leases are assumed zero and labelled as an
assumption in `other_claims_bridge`. A partial lease disclosure stays unknown.
Outside these two labelled assumptions, missing liability categories never become zero.

Companyfacts records retain their actual API origin, filing/accession dates,
units and raw values. They are not labeled as inline context facts. The financial
context table preserves annual operating cash flow, capex, inventory, repurchases,
taxes and other recognized observations when available. Conflicting observations
remain visible; weighted-average shares never replace outstanding shares.

## Build a company-specific operating bridge

The optional `operating_model` supports 1–32 named revenue components. Each has
`name`, `base_revenue` and nonempty `source_refs`; starting revenue must reconcile
to `actuals.revenue` within the small numeric tolerance. No residual component or
balancing plug is generated. `currency` and `amount_unit` must match the plan,
and `assumptions_label` must distinguish forward assumptions from reported facts.

Provide exactly `bear`, `base` and `bull` operating scenarios. All forward arrays
share a 1–15 year horizon. There are two supported earnings conventions:

| `earnings_mode` | Forward inputs | Computed bridge |
|---|---|---|
| `gross_profit_less_expenses` | Each component's `growth_rates` and `gross_margins`; whole-currency expense arrays including `research_and_development` and `selling_general_administrative` | Component revenue → gross profit → expenses → EBIT → consolidated growth and EBIT margins |
| `aggregate_ebit_margins` | Each component's `growth_rates`; explicit main-scenario `ebit_margins` | Component revenue → total revenue; consolidated EBIT uses the supplied margin path |

In the second mode omit component gross margins and expenses: undisclosed gross
profit and expense allocation remain unknown. Optional component
`gross_margin_group` and `disclosure` labels explain shared assumptions; they do
not turn category margins into historical disclosures. For example, a company
may report product categories but disclose only an aggregate products margin.
Applying that margin to each category is a shared forward assumption.

When detailed schedules govern, omit duplicate main-scenario `growth_rates` and,
in gross-profit mode, `ebit_margins`. Python derives them and rejects conflicting
duplicates. Other scenario fields remain explicit: probability, cash operating
tax, incremental sales-to-capital, terminal growth and terminal ROIC (under the
rate glide below, terminal growth may be omitted and defaults to the risk-free
rate). The model does not infer these from category history.

GAAP R&D and SG&A must retain their stock-compensation and rent costs under this
convention. Do not deduct them again. FCFF is EBIT less cash operating taxes and
incremental revenue divided by sales-to-capital. Maintenance investment is assumed
to offset depreciation. Negative EBIT creates no automatic cash tax credit.
Terminal reinvestment is terminal NOPAT × growth / ROIC; inspect the transition
from the last forecast year rather than treating terminal cash flow as a plug.

## Start year 1 from the reported results

The input adapter also reports the anchor year's operating lines in
`reported_inputs.operating_reference`: gross profit, cost of revenue, R&D,
`selling_general_administrative`, operating expenses excluding cost of revenue,
total costs and expenses (which include cost of revenue) and operating income, each
with its XBRL concept, sources and basis. Only annual facts for the revenue anchor's
exact fiscal year count. When a line is reported only in parts it is derived and
labelled: SG&A as selling and marketing plus general and administrative, operating
expenses as total costs and expenses less cost of revenue, and gross profit as revenue
less cost of revenue. A concept whose observations conflict leaves its line out. The
latest year to date of the first forecast year from 10-Q facts is reported beside it.
These lines are reference data and never valuation inputs.

The full pipeline compares each scenario's computed year-1 EBIT margin with the
reported operating margin of the anchor year and of the year to date. A plan or
review correction whose year-1 margin is outside the band of both goes back for repair
with the reported lines and the plan's own year-1 expenses. The band is 5 points, or
40% of the reported margin when that is smaller, with a floor of 1.5 points. A step
that is the forecast needs `margin_continuity_basis`, an object keyed by `bear`,
`base` or `bull` whose text is at least 40 characters and cites sources in the
evidence as `[S#]`. A review correction that leaves a scenario's year-1 margin within
one point of the plan it replaces keeps that plan's basis. Plans supplied with
`--forecast-plan` and offline `--revalue` runs are not checked. `valuation.json` from
the pipeline carries `forecast_continuity`, and the memo shows year-1 margin against
the reported results with any stated basis.

## Date the required-return inputs

Optional `discount_rate` computes WACC for all house scenarios: one constant rate
by default, or the rate path described under
[market-consistent conventions](#keep-the-discount-rate-consistent-with-the-equity-risk-premium).
It requires `as_of`, matching currency/units, and `lease_convention="gaap_operating"`
in this FCFF integration. Set `basis` to `market_wacc` or explicitly to
`house_hurdle`. Omit duplicate scenario `wacc` values when the calculation governs;
conflicting rates are rejected.

Every input record needs `value`, `observed_at`, nonempty `source_refs` and a
descriptive `label`. Rates are decimals. For example, this is a synthetic record
shape, not an observed market input:

```json
{"value": 0.04, "observed_at": "2026-01-02", "source_refs": ["S1"],
 "label": "Conventional 10-year Treasury proxy paired with the same-date ERP"}
```

| Required record | Convention |
|---|---|
| `risk_free_rate`, `equity_risk_premium` | Same exact observation date; preserve the paired Treasury/ERP method |
| `unlevered_beta` | Explicit operating-asset beta choice and method; a sector proxy is not a measured issuer beta |
| `market_equity` | Positive market equity; book equity cannot set capital weights |
| `financial_debt` | Gross debt excluding separately entered finance leases, or an explicitly allocated combined total; `measurement` is `book_value_proxy` unless explicitly `market_value` |
| `finance_lease_debt` | Separate gross finance-lease amount, or explicit zero allocation when already included in a combined total |
| `pretax_cost_of_debt` | Forward borrowing-cost estimate or labeled yield proxy, not a coupon substitution |
| `marginal_tax_rate` | Statutory marginal tax for the debt shield (21% federal or higher for US issuers), separate from the operating effective-tax assumption |

The calculator relevers asset beta using βL = βU × [1 + (1 − tax) × D/E],
assumes zero debt beta, and applies CAPM before market-equity/gross-debt weighting.
Included debt counts finance leases once. Cash is not netted from debt for
relevering or capital weights. One supplied debt cost applies to included debt.
An optional dated `house_hurdle` record is a separate policy choice and applies
only with `basis="house_hurdle"`; it is never relabeled market WACC.

When the reported bridge includes finance leases in debt, use the separately
reported lease amount to split comparable rate inputs where known. If that split
is unavailable, `financial_debt` must declare
`allocation="combined_debt_including_finance_leases"` and `finance_lease_debt`
must declare `allocation="included_in_financial_debt"` with value zero. Labels
must identify the combined total and zero allocation. This allocates the combined
amount once; it is not a claim that finance leases are absent.

No observation or supplied publication date may follow the valuation date.
Different dates remain visible with input ages. Only `unlevered_beta` and
`terminal_unlevered_beta` may use `observed_at="YYYY-MM"` with `date_precision="month"`; that month must end by
`as_of`. No precise measurement day is invented. Comparable same-date book debt,
finance leases and quote-derived market equity are reconciled with the valuation
bridge. Different-date or different-measurement records are labeled as such,
rather than falsely described as reconciled.

Live evidence collection attempts three keyless public calibration sources:

- Damodaran's monthly sustainable-payout implied ERP and its same-row Treasury
  proxy. A newer Treasury rate must not silently replace the paired rate.
- Damodaran's January US sector beta table, preserving month precision and
  separate uncorrected/cash-corrected asset betas. The forecast author can map the
  company to named industries for a bottom-up beta; Python copies each industry's
  beta from the table. The table's market row sets the terminal beta. Peer lease
  conventions are not established by the table.
- FRED's ICE BofA AA US Corporate Index Effective Yield, explicitly a debt-cost
  proxy whose rating, maturity and composition may differ from issuer debt.

Strict parsers archive original bytes and provenance. Retrieval or format failure
creates a gap, not a substitute rate. These sources do not establish an issuer
country-risk premium or reconstruct an unrevised historical source vintage.

## Keep the discount rate consistent with the equity risk premium

An implied equity risk premium is the discount rate that sets an index's price equal
to the present value of its expected cash flows under stated assumptions. Damodaran's
monthly sustainable-payout estimate assumes that index earnings grow at the 10-year
Treasury rate in perpetuity after five years of consensus growth, and that the index
keeps earning its 10-year average return on equity on new investment. A company valuation
that uses this premium with a lower terminal growth rate values the index itself below
its price. Reproducing his November 1, 2026 estimate (4.41% over a 4.94% Treasury
rate) and valuing the index again with 3% terminal growth gives about 80% of its
price. Setting every firm's return on new capital equal to its cost of capital gives
about 65%.

A plan opts into conventions that match the premium with
`discount_rate.term_structure="glide_to_market_v1"` and a dated
`terminal_unlevered_beta` record:

- Years 1-5 use the current WACC. The rate then moves linearly to a terminal WACC by
  year 10, and the terminal value uses the terminal WACC. A forecast shorter than 10
  years is extended to year 10 with steady-state years at terminal growth, the final
  margin and reinvestment of NOPAT × growth / ROIC. A longer forecast holds the
  current rate until five years before its end.
- The terminal WACC uses the market's cash-corrected operating-asset beta (the
  industry table's "Total Market (without financials)" row), relevered at the firm's
  own debt-to-equity ratio with its current debt cost and marginal tax rate.
  Damodaran's spreadsheet default sets the terminal cost of capital to the risk-free
  rate plus the premium whatever the leverage, the rate of an all-equity firm of
  average market risk; it is shown beside the house rate.
- Scenario `wacc` is not allowed. Scenario `terminal_growth` may be omitted and then
  equals the same-date risk-free rate. A higher value is rejected. A lower value
  needs a company-specific `terminal_growth_basis`, such as a shrinking market. A
  scenario whose final forecast growth is below 1%, or whose terminal ROIC is below
  the risk-free rate, must state its terminal growth.
- The terminal WACC must exceed terminal growth by at least one point. The memo flags
  a scenario whose spread is under two points, and sensitivity, convention-panel and
  implied-return rows under two points are marked fragile.
- Optional scenario `terminal_tax_rate` sets the operating tax rate for steady-state
  years and the terminal value; it defaults to the scenario's last tax rate.
  Damodaran's spreadsheet glides the tax rate to the marginal rate over the forecast.
  The convention panel shows the marginal rate applied from the first steady-state
  year and in the terminal value, as an alternative only, because the premium is
  solved on index earnings taxed at current effective rates.
- `basis="house_hurdle"` cannot be combined with the glide.

Optional `valuation_timing={"convention":"stub_v1","anchor_date":"YYYY-MM-DD"}`
discounts from the valuation date. The anchor is the fiscal year-end of the reported
inputs. Year-1 cash flow counts only the part of the first forecast year still ahead,
measured with the calendar year after the anchor, and each flow is discounted from the
valuation date. An anchor a calendar year or more before the valuation date is
rejected.

`unlevered_beta` can also be built bottom-up from industry betas. The forecast author
writes the mapping, and the research pipeline completes it:

```json
{"calculation": "bottom_up_sector_weighted", "source_refs": ["S9"], "label": "Revenue mix",
 "sectors": [{"sector": "Computers/Peripherals", "weight": 0.74},
             {"sector": "Software (System & Application)", "weight": 0.26}]}
```

Sector names must match the supplied table exactly, aggregate rows are refused, and
weights sum to 1. The pipeline copies each sector's `cash_corrected_unlevered_beta`
and `firms` and the table month (`observed_at="YYYY-MM"`, `date_precision="month"`)
from the source and replaces any beta typed into the plan. A plan revalued offline
must already carry those fields for every sector. Optional `beta_cross_checks` value
the base case with a weekly regression beta and its Blume adjustment (two-thirds
observed plus one-third), holding everything else fixed.

Every plan with a market price reports:

- `implied_return_at_price`: for each scenario and the weighted value, the single
  discount rate, applied in every year and in the terminal value, at which value
  equals the price. Beside it is the same calculation at the house value, the
  flat-equivalent required return. This describes what the price implies about the
  discount rate and makes no forecast of shareholder returns.
- `convention_panel`: base value with one convention changed at a time: the current
  rate held constant, Damodaran's terminal rate, terminal growth at the risk-free rate,
  steady-state and terminal tax at the marginal rate, a terminal return equal to the
  terminal rate, a 15% terminal return, terminal reinvestment that continues the
  forecast's capital efficiency, and the whole rate path one point higher and lower.
  Rows carry no probability. The largest convention row is named; the two rate shifts
  show scale and are not ranked. Rows that need the glide appear only under it.
- `value_to_price` for each scenario and `weighted_value_to_price`. Percentage upside
  is no longer reported.

Plans valued under the glide or a valuation-date stub also report `discounting` (the
base rate path, terminal rate and their inputs), and their model ID ends in `-v2`.
Other plans keep the `-v1` ID and produce the same values as before; the shipped
examples recompute to identical per-share values.

Limits: leverage moves the rate through the debt tax shield and through the gap
between the pretax debt cost and the risk-free rate. The current debt-to-equity ratio
and debt cost carry into the terminal rate, with no debt beta and no credit spread
that rises with leverage. Industry betas are simple averages across each industry's
firms, and the industry mapping is a judgment.

In the full pipeline, the glide applies to model-authored plans when cash flows are in
US dollars and the run collected the implied premium and the table's market row. For
such runs Python copies the risk-free rate and premium from the premium's source, a
dated `discount_rate` is required, and a US marginal tax rate below the 21% federal
statutory rate goes back for repair. Other currencies keep one constant rate built
from a risk-free rate in that currency; a risk-free rate that cites the US premium's
source is refused. The
valuation-date stub is pinned for any plan with a `discount_rate` whose revenue year
ends on the balance-sheet date, when the engine accepts that anchor. A plan supplied
with `--forecast-plan` keeps its own conventions unless it sets
`term_structure="glide_to_market_v1"` or `valuation_timing` itself; an explicit
`term_structure="constant"` is kept. The offline calculator applies whatever the plan
states and does not check currency. Any code change refreshes the workflow contract,
so a resumed run rebuilds a model-authored plan under the current conventions; only a
run saved before the workflow contract existed keeps its plan's conventions, and says
so in its gaps.

## Use issuer return history explicitly

`market_beta.weekly_equity_beta(...)` computes historical **levered equity** beta
from adjusted issuer and benchmark prices. The keyless gatherer defaults to SPY,
five calendar years and at least 104 paired weekly returns. Both endpoints must
exist in adjacent exchange-local calendar weeks. There is no interpolation or
raw-close fallback. Only Friday period-end labels strictly before `as_of` are
eligible; a same-date Friday bar is excluded. A Friday label is not a claim that
a holiday week's last trade occurred that day.

Output includes the sample window, missing weeks, OLS slope/intercept, R²,
standard error, descriptive approximate 95% OLS interval and input/paired-return
hashes. Optional two-thirds observed beta plus one-third unit beta shrinkage is
reported separately. The benchmark is a proxy for the market portfolio. Serial
correlation, changing leverage, missing data and heteroskedasticity limit the
historical estimate and nominal interval; neither is a forward-beta guarantee.

Adopting that history in the forward rate build is an explicit analyst choice.
The plan's `unlevered_beta` record can omit `value` and supply:

```json
{"calculation": "historical_issuer_cash_corrected", "historical_equity_beta": 1.2,
 "observed_at": "2026-01-02", "source_refs": ["S1"],
 "label": "Synthetic historical equity beta adopted with an explicit financing snapshot"}
```

The integration unlevers using supplied market equity, financial debt, finance
leases and marginal tax, then divides by [1 − nonoperating cash/(E + D)]. It uses
the explicit cash bridge, assumes zero debt beta and zero beta for the selected
cash, and records the transformation. This static financing snapshot does not
recreate historical leverage. Inspect dates and cash classification, and justify
forward adoption separately. A supplied duplicate `value` must agree with the
derived result. Collection alone never assigns a historical beta to the DCF.

## Compare calibrations and monitor conditional cases

Up to eight `calibration_comparisons` can each contain `name`, `label` and
`overrides`. Overrides replace complete dated records for `unlevered_beta`,
`pretax_cost_of_debt`, `marginal_tax_rate`, or the matched
`risk_free_rate`/`equity_risk_premium` pair together. Python recomputes the rate and
base-case value while holding operating cash flows, terminal ROIC and the equity bridge
fixed. With one constant rate, terminal growth is also fixed. Under the glide the
whole rate path changes, and a terminal growth that defaults to the risk-free rate
follows the comparison's risk-free rate; the record and memo show that growth and the
value with house terminal growth. Comparisons do not replace house scenarios or weights.
Alternative historical fits are not forward confidence intervals.

Separately, up to eight named `counterfactuals` override base forecast inputs.
Supported aggregate fields are `growth_rates`, `ebit_margins`, `tax_rate`,
`terminal_tax_rate`, `sales_to_capital`, `wacc`, `terminal_growth`,
`terminal_growth_basis` and `terminal_roic`. Under the glide a counterfactual `wacc` is
one flat rate in every year and in the terminal value. Arrays keep the
base horizon. With an operating model, use `operating_overrides` for existing
component growth/margin arrays or existing expense arrays. Do not mix operating
drivers with aggregate growth/EBIT overrides. For example, in a five-year model
with an existing `services` component:

```json
{"services_expansion": {"operating_overrides": {"components": {
  "services": {"growth_rates": [0.10, 0.10, 0.09, 0.08, 0.07]}
}}}}
```

An aggregate growth/margin override against a detailed operating model requires
explicit `operating_model.counterfactual_policy="aggregate_override"`; its result
has no derived component breakdown. Operating-driver cases can also change tax,
capital, rates or terminal assumptions, which are disclosed separately. Output
records changed/fixed inputs, annual cash flows, revenue paths and conditional
value. Cases have no probability and do not enter the weighted house value.

`monitoring` accepts up to 16 records with `name`, `observation`, `review_by`
(`YYYY-MM-DD`), `threshold`, `source_refs`, and exactly one existing
`counterfactual` or `calibration_comparison` name. Python supplies conditional
value and its dollar/percentage change from house **base**, not weighted value.
A threshold is an analyst-selected review trigger; the runner does not observe
its fulfillment or automatically update a forecast. Correlated cases are not
additive. Each row should explain observation → changed input → conditional value.

Reverse-DCF growth/rate solutions are also conditional on fixed inputs. An
algebraically implied beta is not a measured issuer beta or a unique market
belief. A solved WACC that recapitalizes terminal value differs from a cash-return
calculation that holds the original terminal exit value fixed.

## Explain multiples and repurchase economics

`computed_diagnostics.valuation_multiples` compares market enterprise value with
first-forecast-year EBIT and NOPAT, shows the original DCF exit multiples, and
computes conditional cash-flow returns at an unchanged entry multiple. All use the
plan's discount timing (integer years, or the valuation-date stub with the exit at the
final model year) with debt/claims settled at entry;
they are not common-share payout forecasts. Nonpositive profit produces a
not-meaningful multiple, rather than an apparently useful ratio. A return using
the original DCF exit value must disclose its implied multiple change.

This project values companies and sets no entry prices. The research pipeline drops
`entry_policy` from any plan and records the removal. The offline calculator still
evaluates it in plans saved before that change (basis, margin of safety, label and
rationale) so those plans reproduce.

Optional `repurchase_tests` contains up to eight independent cash-funded cases.
Each requires `name`, `amount`, `price`, `source_refs`, `label` and
`interpretation` (`historical_retrospective_conditional` or
`forward_conditional_illustration`). Spending cannot exceed the reconciled modeled
nonoperating cash. The calculator reduces cash and shares once while holding
operating value fixed. Comparing historical repurchase prices with today's house
value is a retrospective conditional comparison, not a measured historical loss.
Forward cases are illustrations, not forecasts of future repurchase policy.

## Replay evidence and review a supplied plan

The CLI flags map to Python's `pipeline.run(source_snapshot=..., forecast_plan=...)`:

```bash
python -m investmentideas AAPL --snapshot sources.json \
  --forecast-plan assumptions.json --draft memo-body.md --budget 10
```

`--snapshot` accepts a JSON list of 1–80 records, at most 3 MB, with consecutive
`S1`, `S2`, … IDs and `title`, `kind`, `data`. Preserve source dates and provenance.
It replays initial evidence and quotes without refreshing them and records that
limitation. Paths to raw archives in another run are not automatically copied;
preserved excerpts/hashes do not prove those raw files exist locally. Supply
archived evidence through the supported source-ingestion path when needed.

`--forecast-plan` accepts a JSON object up to 150 KB. It is checked against pinned
reported inputs and known source IDs, calculated and reviewed. A changed plan
archives prior model artifacts and invalidates previous approval. `--draft` is
optional author-body text only; computed tables and source/status appendices are
regenerated. These flags apply to full mode and do not bypass model review or
budget limits. Snapshot replay can still make model calls and targeted research
calls during correction. It requires your own OpenRouter key.

The explicitly invoked live-validation runner also accepts a typed envelope in
`VALIDATION_SOURCE_FILE`: set `format` to
`intrinsicvalue_research_snapshot_v2`, put the manifest in `sources` and the plan
in `forecast_plan`. A legacy JSON list remains supplemental evidence. In
`full`/`full_economy`, `VALIDATION_DRAFT_FILE` can supply the author body. These
files must stay inside the repository; the source envelope has a 3 MB total cap
and the plan a 150 KB cap. Existing credential-limit and completed-review checks
still apply. See [live-validation guidance](openrouter_live_validation.md).

To recompute without a key, network requests or model calls:

```bash
python -m investmentideas --revalue assumptions.json --output revalued
```

Offline revaluation uses the supplied facts and references; it does not fetch
support, verify source truth, rewrite a previous memo or approve the new model.

## Costs and context size

`usage.json` is the cumulative request ledger. `cost-summary.json` summarizes
totals, stage/model breakdowns, reported prompt/completion/cached counts and their
coverage. It distinguishes known `usage.cost` from retained charge estimates;
unknown counts remain null. Cached prompt tokens are already part of prompt
usage, not additional tokens. Requested and returned model identities remain
visible. Reusing a saved stage makes no new ledger row, so this summary cannot
count stage-cache hits or infer their savings. It is not invoice reconciliation.

`state.json.prompt_context_metrics` records `full_json_utf8_bytes` and
`presentation_utf8_bytes` for the valuation context. The deterministic presentation
uses backward `same_as` JSON pointers and SHA-256 hashes for exact repeats when
shorter. It retains all main-scenario rows and revenue paths, and may summarize
intermediate counterfactual operating rows while retaining annual FCFF/PV and
complete first/last rows. Full saved math remains authoritative.

These are serialized byte measurements, not tokenizer measurements or a controlled
quality/cost experiment. The two representations have different formatting and
metadata. No proven token savings, optimal model routing, cheapest acceptable
configuration or quality/cost frontier follows from these sizes or one run's
charges. Local pre-call reservations are also separate from provider-enforced
spending limits; retain ambiguous charges and use a dedicated key limit.

## Review limits

Fresh-context review assesses frozen draft/evidence/model artifacts and records
findings with provenance. Bounded revisions can research, correct, recompute and
rewrite; budgets, missing evidence or revision limits can leave findings open.
Editing the body, forecast or review inputs invalidates earlier approval as
applicable. Inspect the final artifact hashes and findings for the exact run.

The review schema asks the reviewer to state the conclusion's central weakness, its
computed value effect, whether the memo handles it and whether the memo explains the
difference between value and price. An answer other than a plain yes counts as no or
partly. Either answer short of yes, with no other material finding, becomes a major
finding, so the draft goes back for revision. A review that gives neither answer gets
a minor finding and can pass; the memo then shows the assessment as not stated. The
default reviewer is a different model from the author: GPT-5.4 for `balanced` and
Kimi K2.5 for `economy`. When `--strong-model` names the default reviewer, the
profile's default author reviews instead. `--review-model` can name any model,
including the author, and is saved with the run; a run folder saved before the
cross-model default keeps its saved reviewer.

A model pass is a recorded assessment, not independent publication certification,
an assurance of no defects, an investment recommendation or proof of forecast
accuracy. Arithmetic checks establish tested identities under the supplied
assumptions. Source support, business economics, forward calibration, reinvestment,
terminal returns and suitability still require judgment. Unsupported sector
models, incomplete essential primary inputs and unresolved findings must remain
visible rather than being converted into a price target or approval.

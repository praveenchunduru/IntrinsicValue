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
to primary inline-XBRL facts. It rejects essential coverage gaps rather than
letting a forecast replace a reported number. A supplied forecast passes through
the same input lock and source-reference checks.

The net-debt bridge deducts selected cash and marketable securities exactly once.
Classifying all selected cash as nonoperating is an explicit house policy, not a
reported assertion that it is surplus. Financial debt excludes finance leases;
identified finance leases are deducted once as other equity claims. Any zero for
additional unidentified claims is a disclosed assumption. With GAAP operating
EBIT, operating rent remains an expense and operating-lease liabilities are not
added to debt. Missing bridge components never become zero.

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
tax, incremental sales-to-capital, terminal growth and terminal ROIC. The model
does not infer these from category history.

GAAP R&D and SG&A must retain their stock-compensation and rent costs under this
convention. Do not deduct them again. FCFF is EBIT less cash operating taxes and
incremental revenue divided by sales-to-capital. Maintenance investment is assumed
to offset depreciation. Negative EBIT creates no automatic cash tax credit.
Terminal reinvestment is terminal NOPAT × growth / ROIC; inspect the transition
from the last forecast year rather than treating terminal cash flow as a plug.

## Date the required-return inputs

Optional `discount_rate` computes a constant WACC for all house scenarios. It
requires `as_of`, matching currency/units, and `lease_convention="gaap_operating"`
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
| `financial_debt` | Gross debt excluding separately entered finance leases; `measurement` is `book_value_proxy` unless explicitly `market_value` |
| `finance_lease_debt` | Separate gross finance-lease amount; a zero must be explicit |
| `pretax_cost_of_debt` | Forward borrowing-cost estimate or labeled yield proxy, not a coupon substitution |
| `marginal_tax_rate` | Normalized marginal tax for the debt shield, separate from the operating effective-tax assumption |

The calculator relevers asset beta using βL = βU × [1 + (1 − tax) × D/E],
assumes zero debt beta, and applies CAPM before market-equity/gross-debt weighting.
Included debt counts finance leases once. Cash is not netted from debt for
relevering or capital weights. One supplied debt cost applies to included debt.
An optional dated `house_hurdle` record is a separate policy choice and applies
only with `basis="house_hurdle"`; it is never relabeled market WACC.

No observation or supplied publication date may follow the valuation date.
Different dates remain visible with input ages. Only `unlevered_beta` may use
`observed_at="YYYY-MM"` with `date_precision="month"`; that month must end by
`as_of`. No precise measurement day is invented. Comparable same-date book debt,
finance leases and quote-derived market equity are reconciled with the valuation
bridge. Different-date or different-measurement records are labeled as such,
rather than falsely described as reconciled.

Live evidence collection attempts three keyless public calibration sources:

- Damodaran's monthly sustainable-payout implied ERP and its same-row Treasury
  proxy. A newer Treasury rate must not silently replace the paired rate.
- Damodaran's January US sector beta table, preserving month precision and
  separate uncorrected/cash-corrected asset betas. No issuer sector is assigned
  automatically, and peer lease conventions are not established by the table.
- FRED's ICE BofA AA US Corporate Index Effective Yield, explicitly a debt-cost
  proxy whose rating, maturity and composition may differ from issuer debt.

Strict parsers archive original bytes and provenance. Retrieval or format failure
creates a gap, not a substitute rate. These sources do not establish an issuer
country-risk premium or reconstruct an unrevised historical source vintage.

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
`risk_free_rate`/`equity_risk_premium` pair together. Python recomputes WACC and
base-case value while holding operating cash flows, terminal growth/ROIC and the
equity bridge fixed. Comparisons do not replace house scenarios or weights.
Alternative historical fits are not forward confidence intervals.

Separately, up to eight named `counterfactuals` override base forecast inputs.
Supported aggregate fields are `growth_rates`, `ebit_margins`, `tax_rate`,
`sales_to_capital`, `wacc`, `terminal_growth` and `terminal_roic`. Arrays keep the
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

## Replay evidence and review a supplied plan

The CLI flags map to Python's `pipeline.run(source_snapshot=..., forecast_plan=...)`:

```bash
python -m investmentideas AAPL --snapshot sources.json \
  --forecast-plan assumptions.json --draft memo-body.md --budget 3
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

A model pass is a recorded assessment, not independent publication certification,
an assurance of no defects, an investment recommendation or proof of forecast
accuracy. Arithmetic checks establish tested identities under the supplied
assumptions. Source support, business economics, forward calibration, reinvestment,
terminal returns and suitability still require judgment. Unsupported sector
models, incomplete essential primary inputs and unresolved findings must remain
visible rather than being converted into a price target or approval.

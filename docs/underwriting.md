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
pins supported annual revenue, outstanding shares, the debt/cash/claims bridge,
non-operating investments and the dilutive share increment to primary inline-XBRL
or normalized SEC Companyfacts records. It rejects essential coverage gaps rather than
letting a forecast replace a reported number. A supplied forecast passes through
the same input lock and source-reference checks.

The net-debt bridge deducts selected directly reported cash, marketable securities
and short-term investments exactly once. Inclusive subtotals replace their parts.
Absent optional investment categories are excluded under the disclosed selected-asset
policy, rather than asserted to be zero. Reported cash categories left outside the
selected cash are listed with their amounts as excluded by house policy (Coca-Cola's
$1.9 billion of marketable securities and $1.8 billion of available-for-sale debt
securities). Unclassified long-term investments stay out of cash; the equity bridge
below adds them separately. Classifying selected assets as nonoperating is a house
policy, not a reported assertion that all cash is surplus.

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
| `reported_current_debt_less_current_maturities` | A short-term borrowings line equal to reported current debt within its stated rounding, less current long-term debt and current finance leases | `pinned` |
| `identified_categories_house_assumption` | Commercial paper and other short-term borrowings as reported, with nothing further assumed | `pinned_with_disclosed_assumption` |

The last basis is a house assumption. It applies only when the run fetched the
whole SEC entity record and that record contains no amount, on the balance-sheet
date, under any other short-term borrowing concept. The scan counts whether the
record was fetched before or after the valuation date: absence in a later record is
absence at an earlier cutoff, and an earlier scan covers every fact its source holds.
Most issuers do not tag a borrowing category they do not have, so the absence of a
concept is the only structured evidence available; it is still weaker than a reported
zero. An undisclosed or differently tagged borrowing would raise debt and lower equity
value. The memo's assumptions label states the assumption whenever it is used.
A positive amount under `LineOfCredit`, `LinesOfCreditCurrent`,
`ShortTermBankLoansAndNotesPayable`, `ShortTermNonBankLoansAndNotesPayable`,
`BankOverdrafts` or `WarehouseAgreementBorrowings` cannot be placed between
short-term financing and long-term debt without issuer disclosure, so it stops
the bridge. A reported total always displaces the assumption, and reported
current debt above the identified categories contradicts it.

These reconciliations handle recurring issuer tagging patterns. Each uses only
reported amounts and is recorded in the bridge:

- `LongTermDebt` equal to `DebtInstrumentCarryingAmount` (gross principal) is set
  aside when gross less the reported unamortized discount and issuance costs equals
  current plus noncurrent carrying debt. The carrying amounts are used. The same test
  sets aside principal observations when one date carries both: Uber's 10-K tagged
  `LongTermDebt` at $10,600 million of principal and later 10-Q comparatives at the
  $10,521 million carrying amount.
- A whole-debt total equal to the reported long-term subtotal, alongside separately
  reported positive short-term borrowings, is excluded as narrower than its taxonomy
  scope. The components are used, which is the larger figure.
- A `ShortTermBorrowings` line equal to reported current debt (`DebtCurrent`) within
  its stated rounding is read as the current-debt total when current long-term debt is
  larger than that rounding. Short-term borrowings are then the line less current
  long-term debt and current finance leases (Johnson & Johnson: $8,495 million less
  $2,000 million), under the basis `reported_current_debt_less_current_maturities`.
- A value repeated in prose at a coarser stated inline precision (`decimals`) is a
  duplicate when it lies within half a unit of that precision of the most precisely
  reported amount.
- Scope checks allow half a unit of each inline amount's stated precision, summed
  over a total and its parts. Procter & Gamble's current-debt parts, each rounded to
  millions, come to $11,297 million against a $11,296 million total. Company-facts
  values carry no stated precision and compare exactly.
- Where no cash and cash equivalents concept is tagged on the date and no restricted
  cash is reported, the total including restricted cash stands in for cash (Procter &
  Gamble). Net debt is then `pinned_with_disclosed_assumption`.
- The available-for-sale securities total is a fallback for issuers that report no
  other investment category. Its conflicting observations stop the bridge only when
  no other category is selected (Uber restated it by $1 million in later comparatives).

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
Outside these two labelled assumptions, missing debt and lease categories never
become zero.

Equity value then adds non-operating investments (`actuals.nonoperating_assets`) at
carrying value on the balance-sheet date of net debt. The lines are those the
return-on-capital table removes from invested capital: `LongTermInvestments` where it
is tagged, otherwise noncurrent securities plus the larger of other long-term
investments and the sum of equity-method investments and non-marketable equity
securities. Those two never overlap, because ASC 321 excludes equity-method stakes,
but either can sit inside other long-term investments (Alphabet). A line that may
already be in the selected cash is not added again: Apple's $80.2 billion of
noncurrent marketable securities stay in cash, and a `LongTermInvestments` total beside
noncurrent securities selected as cash gives way to the equity-investment lines.
Marketable equity securities are added only when the selected cash holds just cash,
cash equivalents, debt securities and investments with maturities, and no added line
can contain them. Coca-Cola's cash and short-term investments equal its cash plus
other short-term investments, which mature and so hold no equity, and its $2.6
billion of marketable equity is added. A reported quoted value is printed beside the
bridge and left out of it (Coca-Cola's equity-method stakes: $20.2 billion carrying,
$34.3 billion quoted).
`nonoperating_assets_bridge` lists each line added and each line left out with the
reason.

`other_equity_claims` adds to finance leases the noncontrolling interests (total
equity less parent equity where no interest is tagged), redeemable noncontrolling
interests, preferred stock and unfunded defined-benefit pension and postretirement
obligations, each at carrying value, with the parts in
`other_claims_bridge.components`. Pension obligations are the recognized liabilities
of underfunded plans, gross and before tax. Recognized assets of overfunded plans are
credited neither as an asset nor against other plans' deficits. Damodaran deducts
underfunded obligations between firm and equity value, and his conservative rule for
excess plan assets assumes that reclaiming them costs too much ("Dealing with Cash,
Cross Holdings and Other Non-Operating Assets", 2005). Liabilities tagged by plan type
(pension, other postretirement) are read where no whole-company concept is tagged, and
replace a whole-company reading that does not match them when they reconcile to the
reported funded status (Coca-Cola $0.9 billion, Johnson & Johnson $7.0 billion, Procter
& Gamble $3.1 billion, AT&T $7.7 billion). Failing both, a reported postemployment
obligation stands in. An obligation the bridge cannot read is zero by assumption, and
the note says the plan-level values were not read. Preferred stock whose
conversion the filing counts in diluted shares is deducted through the share count
instead (Procter & Gamble). The discount-rate weights are unchanged: market equity is
the price times outstanding shares, and noncontrolling interests and preferred stock
carry no weight.

Shares are counted where net debt is: on its balance-sheet date, or on the annual
report's cover within 60 days after it. With neither, the bridge stops and asks for a
reviewed count, because a later count would not match year-end net debt (Salesforce:
929 million on the balance-sheet date, against 823 million on a later cover, after
buybacks that net debt does not include). Value per share divides equity value by
those shares plus the dilutive increment (`actuals.dilutive_shares`): the anchor fiscal
year's diluted less basic weighted-average shares, which counts awards by the
treasury-stock method and convertibles as if converted. Separately reported conversion
shares of convertible debt are removed, because net debt already deducts that debt.
When outstanding shares are
more than 1.5 times the year's basic average or less than two thirds of it, as after a
split, the increment is scaled by their ratio. Stock-based compensation stays an
operating expense, and unvested awards beyond the diluted count are not valued. A
bridge line with no reported concept is zero by disclosed assumption. Coverage is
complete only when all six fields are pinned. Plans saved without `nonoperating_assets`
and `dilutive_shares` value them at zero and revalue unchanged. The memo prints a
base-case table from enterprise value to value per share, one row per line with its
source and basis.

Companyfacts records retain their actual API origin, filing/accession dates,
units and raw values. They are not labeled as inline context facts. The financial
context table preserves annual operating cash flow, capex, inventory, repurchases,
taxes and other recognized observations when available. Conflicting observations
remain visible. Weighted-average shares never replace outstanding shares; their
diluted-less-basic difference sets only the dilutive increment.

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

In the full pipeline the plan gives gross-profit-mode costs as `expense_shares`
instead of dollar arrays: for each line a share of revenue (held), or `year1`,
`target` and `target_year` (a straight line to the target, then held), or one share
per forecast year. Shares may exceed 1 (R&D above revenue). Python builds the dollar
arrays from each scenario's own revenue before validation. SG&A is one line: separate
`selling_and_marketing` and `general_and_administrative` shares are added into it, and
other selling or administrative line names without it are refused, so no cost is
counted twice. An R&D or SG&A line left out holds its reported share, and R&D is zero
when the company reports none (Costco); a scenario
with neither shares nor expenses holds every reported operating cost share, with SG&A
as selling and marketing plus general and administrative where those are reported
instead and the rest of reported operating expenses as `other_operating_expenses`.
Review corrections see the built dollars back as per-year shares, so a change in
growth carries the costs with it. Plans supplied with `--forecast-plan` may still give
dollar arrays.

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
rate; in the full pipeline Python sets the probabilities, the terminal ROIC and the
continuing value's R&D shares, and `terminal_roic_basis: "terminal_rate"` makes the
terminal return follow the terminal discount rate of any rate path applied). The model
does not infer these from category history.

GAAP R&D and SG&A must retain their stock-compensation and rent costs under this
convention. Do not deduct them again. FCFF is EBIT less cash operating taxes and
incremental revenue divided by sales-to-capital. Sales-to-capital is one ratio,
`{"year1", "target", "target_year"}` (a straight line to the target, then held, as
expense shares are), or one ratio per forecast year, so a capex cycle can be stated.
Python writes the per-year ratios as `sales_to_capital_path` and rejects a stated path
that disagrees. Year t's ratio prices year t's revenue increase, so the capital in
place at the end of the forecast supports that year's NOPAT. Maintenance investment is
assumed to offset depreciation. Negative EBIT creates no automatic cash tax credit.
The continuing value capitalizes the final model year's NOPAT at the terminal tax
rate, plus any net R&D investment (see the house conventions below), as
NOPAT × (1 − g / ROIC) / (r − g), with no further growth step; each later year's
reinvestment, growth / ROIC of NOPAT, pays for the year after. Steady-state years,
which extend a shorter forecast to the end of the rate path, work the same way: each
invests the following year's NOPAT increase over the return on new capital. The first
also pays for its own increase, a year late and with a year's return at that year's
rate, so its present value is that of funding it a year earlier. The last invests
nothing ahead, because the continuing value starts from its NOPAT.
`computed_diagnostics.terminal_transition` shows the step from the last forecast year.

## Start year 1 from the reported results

The input adapter also reports the anchor year's operating lines in
`reported_inputs.operating_reference`: gross profit, cost of revenue, R&D,
`selling_general_administrative`, operating expenses excluding cost of revenue,
total costs and expenses (which include cost of revenue) and operating income, each
with its XBRL concept, sources and basis. Only annual facts for the revenue anchor's
exact fiscal year count. When a line is reported only in parts it is derived and
labelled: SG&A as selling and marketing plus general and administrative, operating
expenses as total costs and expenses less cost of revenue, and gross profit as revenue
less cost of revenue. R&D is the larger of `ResearchAndDevelopmentExpense` and its part
excluding acquired in-process cost, since the inclusive concept cannot be the smaller:
Johnson & Johnson tags only its $0.1 billion in-process charge with it, against $14.7
billion of R&D excluding it in fiscal 2025. The history and return-on-capital tables
use the same rule. A concept whose observations conflict leaves its line out. The
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
`--forecast-plan` are not checked, and an offline `--revalue` reports the comparison
without sending anything back. `valuation.json` carries `forecast_continuity`, and the
memo shows year-1 margin against the reported results with any stated basis.

Two more anchors work the same way, each with its reference computed by Python from a
reported history whose last year is the revenue anchor year, shown to the plan author
and printed in the exhibits:

- **Reinvestment.** Incremental sales-to-capital is the revenue increase over the last
  three fiscal years divided by each of two measures of the investment in those years.
  One is net reinvestment: capital expenditure less depreciation and amortization, plus
  cash acquisitions and finance-lease additions where reported. The other is the change
  in invested capital by Mauboussin and Callahan's method, which also counts working
  capital and operating leases. In it, growth in goodwill and acquired intangibles
  counts only up to the cash paid for acquisitions in those years, so a stock-funded
  deal adds no investment on either measure (Salesforce's fiscal 2022, with Slack: 0.70,
  against 0.30 with all the goodwill counted). The lower rate is the reference
  (Nvidia: 9.6 on net reinvestment, 2.3 on invested capital). Over forecast years 1-3, a
  scenario needs `capital_efficiency_basis` when its net reinvestment is below two
  thirds of three years at the last reported net reinvestment (where that is positive),
  or when its sales-to-capital is above 1.5 times the reference. A business whose
  capital spending is below depreciation is still checked when invested capital rose
  (Apple, Uber). One whose revenue fell, or whose investment rose on neither measure, is
  not checked. The repair message names the basis, gives the other rate and the cash
  paid for acquisitions, and offers `capital_efficiency_basis` for a forecast that
  keeps its path, for example one that does not repeat past acquisitions. Leases
  signed but not yet commenced are disclosed in the evidence and not deducted.
- **Final-year margin ceilings.** Bear at most the reported five-year average
  operating margin, or the five-year peak where that average is not positive (Uber).
  Base and bull at most the higher of
  the reported five-year peak and the latest twelve-month operating margin, plus 5
  points (40% of that margin when smaller, with a floor of 1.5 points, as in the year-1
  band). The twelve months are the four latest fiscal quarters, reported or derived,
  when the last of them ends after the last fiscal year; failing that, a year to date
  of at least two quarters, since one quarter is too seasonal. A twelve-month span over
  367 days is labelled as a 53-week year. The latest margin only raises a ceiling, and
  no citation lifts one. A business with no profitable year in the history has no
  ceiling. Plans had let expenses fall to half their share of revenue, raising margins
  in every scenario.

## Compare revenue growth with base rates

Python places each scenario's revenue path in Mauboussin's published base rates for
companies of the same starting size, and prints the result in the memo and in
`valuation.json` (`base_rates`). For 3, 5 and 10 years it computes compound annual
growth from the last reported revenue along the computed path, and its percentile in
the matching size class. The growth the price requires (the base path with the
required shift in every explicit year) gets the same treatment. Each cell of the
memo's table gives nominal and real growth and the percentile of the figure the table
compares, names the size class applied, and adds a labelled real row. The first page's
revenue-growth row gives the 10-year percentile of both the house path and the
required one, of real growth where the table is real.

The newest table covering the size and horizon is used. For 5 years that is "Bayes and
Base Rates 2.0" (May 2026, Exhibit 1) for USD 2-5 billion of starting sales and "Bayes
and Base Rates" (February 2026, Exhibit 5) for USD 8-12 billion, both nominal, read
with a normal approximation because they give only a mean and standard deviation.
Otherwise it is "The Base Rate Book" (Mauboussin, Callahan and Majd, 2016, Exhibit 4,
with bin counts from its appendix), whose growth is after inflation and whose
percentiles interpolate within five-point bins. Starting sales are restated in each
table's dollars with US CPI-U. For the 2016 tables plan growth is deflated at a stated
2.0% a year, the Federal Reserve's longer-run goal, because the pipeline collects no
inflation expectation. The book gives margin persistence only by sector, with no
distribution of company margins, so no margin percentile is shown. Plans in other
currencies are not compared.

The plan author sees the reference distributions for the company's size class and may
cite the tables by name, a review correction sees the plan's percentiles, and the
writer and reviewer read them in the valuation context. Offline `--revalue` output
carries them too. Nothing is checked against them.

## House conventions the plan does not choose

- **Terminal return on new capital equals the terminal discount rate**, Mauboussin's
  steady state. The 10 forecast years are a house horizon, and no company's competitive
  advantage period is measured: excess returns on new investment come only from those
  years' growth and capital efficiency. Afterwards new investment earns its cost of
  capital, so growth adds no value and the continuing value is year-10 NOPAT over the
  terminal discount rate, the form of Appendix A of Mauboussin and Callahan's 2022 ROIC
  paper and of "Market-Expected Return on Investment" (2021). Apart from the R&D
  add-back below, value is then exactly invariant to terminal growth whenever the
  discount rates equal the return on new capital, as on a constant rate path, for any
  forecast length and for the 5-year horizon row. Under the glide a plan shorter than
  10 years keeps a small effect, because its steady-state years are discounted at
  gliding rates that differ from that return. Python computes each scenario's
  terminal rate from the rate inputs (the glide target, or the constant WACC) before
  valuing the plan, so a locked plan locked again keeps it. The convention panel shows
  the value with a terminal return 4 points above the terminal rate, at the reported
  five-year median ROIC capped at 15% (the earlier house rule) where that differs from
  15%, and at 15% and 25%. The implied
  equity risk premium behind the discount rate assumes the market as a whole earns more
  than its cost of capital on new investment; the 15% row shows the value under that
  assumption for this company. In Mauboussin and Callahan's "Competitive Advantage
  Period: The Neglected Value Driver" (April 2026) the period differs by company, so the
  panel also values the base case with horizons of 5, 15 and 20 years and at
  Mauboussin's industry fade rate.
- **The continuing value adds net R&D investment back to NOPAT.** Margins expense R&D,
  and the continuing value also charges reinvestment at the terminal return, so without
  the add-back every R&D dollar after the forecast would earn below its cost. Python sets
  each scenario's `rnd_share_path` at lock from the plan's R&D line, one share per year
  (an edited line or an R&D counterfactual carries it along), holds the reported share
  when the plan has no R&D line, and sets none when the company reports no R&D (Costco).
  R&D may exceed revenue, up to 100 times it, as at a young biotech. Net R&D investment
  is final-year R&D times one less its six-year straight-line amortization at steady
  terminal growth g: R&D_T × (1 − (1/6) Σ_{k=1..6} (1 + g)^−k), about 10% of R&D at 3%
  growth. The six years are the life the return-on-capital table uses, straight line
  from the year after the spending. NOPAT adds the result, as in Damodaran's
  EBIT(1 − t) + R&D − amortization ("Research and Development Expenses: Implications for
  Profitability Measurement and Valuation", 1999). The R&D share used is the final
  forecast year's, capped at one less the final EBIT margin, a cap that binds only when
  R&D exceeds revenue. Selling and administrative costs stay expensed, and so does R&D
  in steady-state years. On a flat rate path terminal growth moves value through this
  one line and nowhere else. By owner decision the formula replaced amortization of the
  forecast's own R&D in the six prior years, which made year-10 net R&D investment
  permanent although amortization catches up after year 10, and so overstated value by
  2-4% where R&D growth fades. `reported_rnd_history` is retired with it, and a plan
  locked with that field drops it. The convention panel shows the value with R&D
  expensed in the continuing value (the earlier house rule). Plans supplied with
  `--forecast-plan` get no add-back unless they state `rnd_share_path`.
- **The base case is the headline value.** Bear and bull are stress cases without
  probabilities; Python sets the probabilities to 0, 1 and 0 and drops any
  `weights_basis`. The summary table labels each case's role and has no weighted row.

Owner choices: the terminal return and weights on 2026-11-15, after same-evidence plans
set terminal returns of 20-30% and different weights, moving value by several percent
between runs; the headline base case, cost shares, margin ceilings and the three-year
reinvestment window on 2026-11-26, and later that day Mauboussin's steady state in place
of a 15% terminal return limited by the reported median ROIC. After the three-lens
critique the same day: the 10-year steady state stays the house rule, with horizons of
5, 15 and 20 years and Mauboussin's industry fade as panel rows; the continuing value
capitalizes year-10 NOPAT with no further growth step and adds back net R&D investment
(R&D only, six-year life), at first from the forecast's own R&D and, after the review
of the merged code later that day, by the steady-state formula; Damodaran's
revenue-weighted country risk premium is a panel row only, because the S&P 500 implied
premium already reflects US companies' foreign revenue; and base rates appear as
printed percentiles, with no check.

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

Live evidence collection attempts five keyless public calibration sources:

- Damodaran's monthly sustainable-payout implied ERP and its same-row Treasury
  proxy. A newer Treasury rate must not silently replace the paired rate. The pairs for
  the ten years ending at that row are kept for the rate-regime exhibit, outside model
  prompts and the figure check.
- Damodaran's January US sector beta table, preserving month precision and
  separate uncorrected/cash-corrected asset betas. The forecast author can map the
  company to named industries for a bottom-up beta; Python copies each industry's
  beta from the table. The table's market row sets the terminal beta. Peer lease
  conventions are not established by the table.
- FRED's ICE BofA AA US Corporate Index Effective Yield, explicitly a debt-cost
  proxy whose rating, maturity and composition may differ from issuer debt.
- Damodaran's dated country risk premium page and the workbook it links, whose
  "Regional Weighted Averages" sheet gives GDP-weighted regional premiums. Each
  published regional average must equal the GDP-weighted average of its countries.
  The workbook states no date of its own, so it is dated by its last-modified stamp,
  and it is used only when its US and mature-market premiums match the page's. Models
  see only the tables' dates and base premiums.

Strict parsers archive original bytes and provenance. Retrieval or format failure
creates a gap, not a substitute rate. The country risk tables feed only the panel row
described in the next section, and none of these sources reconstructs an unrevised
historical source vintage.

## Keep the discount rate consistent with the equity risk premium

An implied equity risk premium is the discount rate that sets an index's price equal
to the present value of its expected cash flows under stated assumptions. Damodaran's
monthly sustainable-payout estimate assumes that index earnings grow at the 10-year
Treasury rate in perpetuity after five years of consensus growth, and that the index
keeps earning its 10-year average return on equity on new investment. A company valuation
that uses this premium with a lower terminal growth rate values the index itself below
its price. Reproducing his November 1, 2026 estimate (4.41% over a 4.94% Treasury
rate) and valuing the index again with 3% terminal growth gives about 80% of its
price. Setting every firm's return on new capital equal to its cost of capital from
year 6, the end of his five years of consensus growth, gives about 65%, and about 72%
when the index keeps its return on equity for the house's 10 years.

A plan opts into conventions that match the premium with
`discount_rate.term_structure="glide_to_market_v1"` and a dated
`terminal_unlevered_beta` record:

- Years 1-5 use the current WACC. The rate then moves linearly to a terminal WACC by
  year 10, and the terminal value uses the terminal WACC. A forecast shorter than 10
  years is extended to year 10 with steady-state years at terminal growth and the final
  margin, which invest for the following year's growth at the terminal return as
  described under the operating bridge. A longer forecast holds the current rate until
  five years before its end.
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
  a scenario whose spread is under two points, and sensitivity and convention-panel
  rows under two points are marked fragile, as are implied-return rows where the
  terminal return does not follow the rate.
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
rejected. The method notes state the free cash flow of the elapsed part of year 1, in
total and a share, because it is in neither the cash flows nor the bridge.

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

- `implied_return_at_price`: for each scenario and the headline value, the single
  discount rate, applied in every year and in the terminal value, at which value
  equals the price. Beside it is the same calculation at the house value, the
  flat-equivalent required return. This describes what the price implies about the
  discount rate and makes no forecast of shareholder returns. Where the terminal return
  follows the rate, the search starts at 0.5%, below terminal growth, because the
  continuing value is then NOPAT over the rate; otherwise it starts just above terminal
  growth, and only those rows can be marked fragile. When no rate in the range gives the
  price, `price_bound` reports the highest and lowest values with the rates that give
  them, and the valuation detail says that no flat rate in the searched range, up to
  50%, reconciles the price.
- `convention_panel`: base value with one convention changed at a time: the current
  rate held constant, Damodaran's terminal rate, terminal growth at the risk-free rate,
  steady-state and terminal tax at the marginal rate, a terminal return 4 points above
  the terminal rate, the earlier house rule (the reported five-year median ROIC, at most
  15%, where that is neither 15% nor the terminal rate), the base-case margin held at or
  below the reported five-year peak (when the history is profitable), a 15% and a 25%
  terminal return, terminal reinvestment that continues the forecast's final-year
  capital efficiency with R&D kept expensed as in the forecast, R&D expensed in the
  continuing value (the earlier house rule, where an R&D share is set), and the whole
  rate path one point higher and lower.
  Rows carry no probability. The first page lists the three convention rows that move
  the value most; the two rate shifts show scale and are not ranked. Rows that need the
  glide appear only under it. The writer's instructions define a convention step as a
  row of the convention tables, the base value with one house convention changed.
- Exhibit rows, never ranked and never changing the house value. The valuation detail
  prints the conventions, the horizon rows and the rate-regime rows as separate tables,
  and the writer's instructions call horizon, rate-regime and country-risk rows
  exhibits:
  - **Horizons.** Five years keeps plan years 1-5 and makes the later model years
    steady-state years. Fifteen and twenty years hold the final-year margin and capital
    efficiency (the final sales-to-capital and R&D share where those are paths) while
    growth moves in equal steps to terminal growth, on the house rate path (under the
    glide, the current rate to year 5, the glide to year 10 and the terminal rate
    after). Plans still state 1-15 explicit years; only these rows run to 20.
  - **Industry fade.** The bottom-up beta's industries map to the GICS industry groups
    of Exhibit 25 in Mauboussin and Callahan's April 2026 paper (fade rates from 0.10
    for staples to 0.30 for utilities). A fade rate f reads as 1/f more years of the
    final-year economics (Holland's equivalence, p33; his simplified formula gives
    shorter equivalents, footnote 130), weighted by the beta weights and rounded to
    whole years. Financials, real estate and industries that span groups with
    different rates (Diversified, Green & Renewable Energy, Retail (General)) have no
    group, so such a company gets a note instead of a row.
  - **Rate regimes.** The cash flows stay fixed while the Treasury rate moves 1 and 2
    points either way, with the premium and the debt spread held. Under the glide,
    terminal growth that equals the risk-free rate moves with it, a stated lower rate
    is kept up to the new risk-free rate, and the terminal return follows the new
    terminal rate; constant-rate plans keep their terminal growth. Where terminal
    growth moves, the row also gives the value at the house terminal growth. The base
    case is also valued at each month's Treasury rate and implied premium over the ten
    years ending at the plan's own pair; two rows give the lowest and highest values
    and name their months, and the memo gives the median. A plan without dated rate inputs moves the
    whole rate path one for one, and a house hurdle gets no rate values.
  - **Country risk**, described after this list.
- `index_benchmark`: a stylized S&P 500 under the same conventions, printed in the
  summary table beside the company's value / price. Damodaran's monthly workbook has
  no index earnings or return on equity, so the benchmark is a steady state. The index
  grows at the risk-free rate and earns 15% on new capital (the market figure behind
  the 15% convention row). Its price assumes those returns forever, as the premium
  does; its house value keeps them for the plan's forecast years, after which new
  capital earns its cost. Value and price use one timing, the premium model's, in which
  each year invests for the following year's growth. At the November 1, 2026 pair
  (4.94%, 4.41%) and 10 years the ratio is 0.80. With his November 2026 inputs (15.5%
  growth for five years and a 19.45% return on equity) the same structure gives about
  0.72 at 10 years and 0.65 at his five-year horizon, so the stylized benchmark runs
  about 0.09 high. The memo labels it a steady state that does not reproduce his cash
  flows, and the writer's instructions allow it only as context for reading value /
  price, reported as the index's own ratio.
- `price_requirements`: for the base case, how far one input at a time would have to
  move for value to equal the price: revenue growth or EBIT margin in every explicit
  forecast year (as a shift in points, with the year-1 figure and the compound revenue
  growth or average margin it produces over all forecast years), terminal return on new
  capital, sales-to-capital (one ratio in every explicit year, in place of any house
  path), or terminal growth. Only inputs the engine accepts are searched; a row that
  cannot reach the price reports the closest value and on which side of the price it
  stays. Rows are alternatives, never combined. The first page's table "What the price
  needs" lists these five inputs and a sixth, one flat discount rate, taken from
  `implied_return_at_price` (the bound where no rate solves), and states how many it
  lists; the writer's instructions and rule M4 treat it as the full account.
- `value_to_price` for each scenario and `weighted_value_to_price`. Percentage upside
  is no longer reported.

**Country risk (one panel row).** At lock Python pins `country_risk_reference`: the
latest annual filing's revenue by geography, or a segment breakdown whose members are
all regions, mapped by member name to Damodaran's dated tables. A country takes its own
premium, a region the GDP-weighted average of its countries, and a "rest of world" line
the countries the filing does not report separately. Premiums are measured over the US
premium (0.23 points in January 2026), because the house cost of equity already pairs
the Treasury rate with the implied premium, so US revenue adds nothing. Only a set of
members whose revenue adds up to reported revenue within 0.1% is used; a region is split
by a country the filing reports elsewhere for the same year (Apple's Americas less its US
revenue); members reported within a business segment are not used. Unmapped revenue is
excluded from the weights and listed. When there is no geographic split, no set that
adds up, less than half of revenue mapped or no dated discount rate, the panel says why
in place of the row. The row adds the weighted premium to the cost of equity in every
year and in the terminal value, as premium times revenue exposure with no beta scaling
(Damodaran's lambda approach, "Country Risk: Determinants, Measures and Implications",
2025 edition). The cost of debt and the capital weights are unchanged, so every rate
rises by the equity weight times the premium. A malformed reference writes a note in
place of the row and never stops a run. The S&P 500 implied premium already
reflects US companies' foreign revenue, so the row overstates the adjustment against
that benchmark; it is not ranked and the house value is unchanged. Labels are mapped by
their plain meaning unless a recorded issuer definition replaces it: Apple's Europe
includes India, the Middle East and Africa, its Greater China includes Hong Kong and
Taiwan, and India leaves its Rest of Asia Pacific. Europe and EMEA include Eastern
Europe and Russia. GDP weights the countries inside a region.

Plans valued under the glide or a valuation-date stub also report `discounting` (the
base rate path, terminal rate and their inputs), and their model ID ends in `-v2`.
Other plans keep the `-v1` ID. Since 2026-11-26 every plan's continuing value
capitalizes final-year NOPAT, so the shipped examples recompute to identical explicit
cash flows with continuing values 1/(1+g) of the saved ones (the revised Apple base
case moves from $162.07 to $159.53).

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
source is refused. Python dates a model-written plan at the UTC date of the run's
latest source retrieval, so the model's own date cannot move value through the stub
or drop coverage; every quote, rate and filing in the evidence was observed by then. A
supplied plan keeps its own date. The valuation-date stub is pinned for any plan with
a `discount_rate` whose revenue year ends on the balance-sheet date, when the engine
accepts that anchor. A plan supplied
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
`terminal_tax_rate`, `sales_to_capital` (a ratio or a path), `wacc`, `terminal_growth`,
`terminal_growth_basis`, `terminal_roic` and `rnd_share_path`. Under the glide a
counterfactual `wacc` is one flat rate in every year and in the terminal value. Arrays
keep the base horizon. An operating case that changes the R&D line changes the
continuing value's R&D with it. With an operating model, use `operating_overrides` for
existing component growth/margin arrays or existing expense arrays. Do not mix operating
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

## How the first draft is produced

Evidence reaches every model prompt in three labelled tiers: tables computed by
Python from reported filings, primary and official sources, and unverified web
excerpts (at most 2,000 characters each and 30,000 in total). The computed tables are
five years of reported history from SEC company facts (revenue and operating income
growth, gross and operating margins, R&D and SG&A, effective tax, capex, depreciation
and amortization, stock compensation, buybacks and dividends, diluted EPS and its
growth, shares outstanding, the change in shares outstanding and shares repurchased)
and annual revenue, cost and
operating income by product or service, segment and geography from the filing's
inline XBRL. Per-share and share-count values reported only before a later split
restatement, and buyback prices without a matching value and share count, are shown
as n/a. Beside the annual history, a table of the latest eight fiscal quarters from the
same company facts gives revenue, growth on the year-earlier quarter, operating margin,
and inventory and deferred revenue where tagged. A quarter not reported on its own is
derived from cumulative periods of its fiscal year (the fourth quarter as the year less
nine months, which covers Costco's 16-week quarter) and marked derived; each fiscal
year uses one revenue concept, and a derived quarter pairs figures of one vintage
(Johnson & Johnson's fourth quarter of 2022 had shown $34.9 billion, the 10-K's year
less nine months restated later without Kenvue, against about $23.7 billion). Growth
comes from comparatives in the same filing, or for a derived quarter the same two
filings, so a restatement between the years is not read as growth. Quarters whose
lengths differ by more than three days show n/m: Home Depot's 13-week fourth quarter
against a 14-week one had shown −3.8% where sales per day rose 3.6%. The four latest
quarters give the twelve-month margin the ceilings use. The annual tables showed
Nvidia's 0.2% growth and 16% margin for the year to January 2023 while its quarterly
margins ran at 7%, 10% and then 30%. The 10-K and 10-Q excerpts include gross-margin,
inventory, purchase commitment and share-repurchase disclosures. The latest earnings
release (8-K Item 2.02) is found from the filing index table, then the index-headers
page, then the 8-K's own exhibit index, and its excerpt keeps the income statement,
balance sheet and cash flow statement whole within the 16,000-character bound. Where
the release has neither an income statement nor a balance sheet, another exhibit 99 of
the same 8-K that has one is read as a second issuer source (Johnson & Johnson and AT&T
file theirs as EX-99.2). Walmart's release of November 19, 2026 had been missed because
SEC served its filing index with no document table, and most of the 16 sample companies
had lost their statements from the excerpt. The tables are
derived from the archived documents on every run, so a folder collected earlier gets
new columns.

Beside the history, Python computes return on invested capital by the method of
Mauboussin and Callahan ("Return on Invested Capital: How to Calculate ROIC and Handle
Common Issues", Counterpoint Global, October 2022), from up to eight earlier fiscal
years as openings:

- NOPAT is EBITA times one minus the cash tax rate, the estimate the paper calls
  usually sufficient. EBITA is operating income plus amortization of acquired
  intangibles plus the interest in operating lease cost (the average operating lease
  liability times its weighted-average discount rate). The cash tax rate is the tax
  provision less deferred tax expense, over pretax income; the effective rate where
  deferred taxes are unreported, and 25% where neither is between 0% and 50%. Where no
  operating income is reported (Johnson & Johnson), EBIT is pretax income plus net
  interest expense, and the table marks it.
- Invested capital is the operating approach: total assets less current liabilities
  other than debt, less cash and current securities above an operating balance of 2%
  of revenue, less noncurrent securities, equity investments and overfunded pensions.
  Commercial paper inside short-term borrowings is counted once, current debt tagged
  with the lease-inclusive concept is debt, and where no current securities line is
  tagged (Nvidia) the debt securities total plus marketable equity stands in for it.
  Equity-method investments and non-marketable equity never overlap (ASC 321), but
  either can sit inside other investments, so the larger of other investments and
  those two together is removed. An investment can be left in capital, which lowers
  ROIC, but none is counted twice. Indefinite-lived trademarks and franchise rights
  count as acquired intangibles (Coca-Cola).
- No adjustment is made for restructuring charges, impairments or one-time tax items,
  which the paper removes. Years that contain them are shown as reported, so
  Microsoft's incremental ROIC for 2018-2021 differs from the paper's Exhibit 16.
- ROIC is NOPAT over the average of opening and closing invested capital, shown with
  NOPAT margin and capital turnover, without goodwill and acquired intangibles, and
  with internally generated intangibles capitalized where R&D is reported (a missing
  R&D line is not taken as zero: Amazon reports technology spending under its own
  concept). Years marked (split), where selling and marketing and G&A are reported
  apart, use the company method the paper applies to Microsoft: 100% of R&D over six
  years, and 70% of selling and marketing and 20% of G&A over two years, straight line
  from the year after the spending. Years marked (one line) use Peters and Taylor's
  shares as Mauboussin and Callahan state them in "ROIC and Intangible Assets"
  (November 2022, p9): 100% of R&D and 30% of SG&A, with the same lives, because that
  paper sets lives by industry without listing them. Where revenue less cost of
  revenue less SG&A equals operating income in most years, R&D is inside the one-line
  SG&A, and the 30% applies to SG&A less R&D (Procter & Gamble). A year uses the first
  basis on which its opening capital is also measured, so an average never mixes the
  two (Apple reports selling and G&A apart only from fiscal 2023). R&D is the larger of
  the inclusive concept and its part excluding acquired in-process cost.
- Incremental ROIC is the NOPAT change over three years over the change in invested
  capital over the three years ending a year earlier. It shows n/m when capital fell or
  rose by less than a tenth of the average of the two capital figures (Johnson &
  Johnson had shown 1,614% and Apple 534%).
- The basis notes flag a latest NOPAT tax rate more than five points from its
  three-year median and give that year's ROIC at the median rate (Microsoft: 10.7%
  against 22.6%, ROIC 33.1% against 28.6%).

On Microsoft's filings as of the end of 2022 the method reproduces the paper's example:
invested capital of $96.1, $120.2 and $164.8 billion for fiscal 2020-2022 (paper: 96,
120, 165), ROIC 48.1% (49%), 92.2% without goodwill and acquired intangibles (94%),
and 33.9% with intangibles capitalized (34%). NOPAT is $68.6 billion against the
paper's $70 billion, which estimates about $1 billion of lease interest against $0.3
billion here. The paper's industry-specific capitalization shares (Iqbal and others,
by Fama-French industry) are not applied.

Under the forecast, Python prints each scenario's ROIC and three-year incremental ROIC
by year below the operating-bridge table, and the return on new capital after the
forecast. Invested capital starts at the last reported operating-approach figure,
goodwill and acquired intangibles included, and adds each year's net reinvestment.
Incremental ROIC starts in year 4 and divides the NOPAT change over three years by the
capital that paid for that growth: the same three years' reinvestment in forecast
years, which invest for their own growth, and the year before's in steady-state
years. A constant incremental return therefore prints itself, and steady-state years
print the return on new capital. Forecast NOPAT is EBIT after the plan's tax rate,
while the history uses EBITA after the cash tax rate, so the two differ slightly. The
table is display only; no check uses it.

The forecast author fills one template: an operating model when the filings report
revenue by product, service or segment, otherwise aggregate growth and margin arrays.
Stated aggregate paths that the operating drivers do not produce are dropped, and
the assumption notes record the largest gap. Python fills market equity, debt and
finance leases from the reported bridge and, for US-dollar plans, the risk-free rate
and premium from their source; the rate block takes the plan's currency and unit, so a
model that writes them differently costs no repair. The draft stage gets extended
thinking (8,000 tokens, added to its output limit) where the model's catalog entry
supports it. The plan stage does not think: a live plan call spent its whole
18,000-token limit reasoning and returned no text. A thinking stage that ends that way
is retried once without reasoning. Claude Haiku 5.5 reasons unasked and OpenRouter
counts that inside the output limit, so Haiku stages switch reasoning off, and a
response cut off at its limit is retried once at 1.75 times the limit.

In the balanced profile the plan is written three times from the same evidence and
prompt (`--plan-samples`, 1 to 5; other profiles write one). Each sample is its own
request (`valuation_plan`, `valuation_plan_s2`, ...) with its own repairs, and a
sample still invalid after them is dropped and recorded. Samples after the first read
the cached evidence and cost about $0.30 each. Python then combines the valid samples
input by input (`consensus.py`): each year's growth, gross margins, expense shares and
sales-to-capital, and the tax and rate inputs, by their mean, and the industry weights
of the bottom-up beta by their median, with an industry a sample left out counted as
zero and the weights renormalized. Samples that disagree on segments or horizon are
combined within the largest group that agrees, or the sample closest to the median
paths is used alone. The text (rationales, cited bases, counterfactuals, monitoring)
comes from the sample whose base-case revenue and EBIT paths are closest to the
per-year medians. The plan prompt asks for rationales that give evidence and direction
without restating input figures, and a figure in the text that matches one of that
sample's own inputs is rewritten to the combined input. The combined plan goes through
the lock, the continuity checks and the engine as a single plan does.
`plan-consensus.json` and `forecast_consensus` in `valuation.json` record each sample,
its value and its value impact (its driver groups swapped into the plan valued). The
first page states how the forecast combines the samples and the range of their base
values. The method notes describe the plan valued: the inputs its structure combines
(Coca-Cola's were growth, EBIT margins, sales-to-capital and tax), the sample that
supplied the text and its own base value, and any sample left out, with what it did
differently and its value. Replays from the draft or the review, and `--revalue`, keep
this record; a run that asked for several samples and has none says so. The plan
prompt asks for the most detailed structure the evidence supports, so repeated plans
share one structure. Inputs reach the prompt in a fixed order, so every sample sends
the same request; the non-operating investment lines had followed the process hash
seed.

Four plan samples on identical evidence and prompt at temperature 0 (2026-11-27)
valued Apple at $138 to $151 a share and Microsoft at $354 to $398, spreads of 8.7% and
12.0% of the median. The discount rate moved Apple's value most: one sample mapped
about half the business to telecom equipment. Means of three samples' paths with median
industry weights gave a spread across the 3-sample subsets of 1.0% for Apple and 2.9%
for Microsoft (`tools/consensus_check.py`); medians of both gave 1.2% and 7.1%. The
subsets share samples, so independent runs will differ more.

The memo reads, in order: a computed title and first page, the eight prose sections,
Valuation detail, Reported history, Method notes, Sources and Review status. The first
page names the currency once and holds one sentence on the values, the scenario table
with the S&P 500 row, a sentence on how the forecast samples were combined and their
range, "What the price needs" (six inputs, each changed alone, with the base-rate
percentile of the required growth) and "Conventions that move the value most" (the
three largest convention rows). The valuation detail carries the equity bridge, the
forecast, the discount rate and what the price implies, the convention, horizon and
rate-regime tables, base rates, monitoring and multiples, and the forecast against
the reported results. Python's own sentences are short declaratives, and each basis
note and caveat is stated once, in the method notes.

Computed text is written to be true for the run it describes. A note prints only when
its condition holds for the run: the R&D add-back, each scenario's margin-ceiling
basis, the two reinvestment rates and the limit on stock-funded goodwill, an n/m cell.
Rows that compare history with the forecast use the same period and basis: net
reinvestment as a three-year total with its last year named, or the change in invested
capital where net reinvestment is not positive. Change columns show the true change of
the unrounded values, with a note that changes are computed before rounding, and a row
that changes two inputs says so. Guard tests (`test_stab_truth.py`) render synthetic
shapes and saved runs and fail when a conditional note appears without its condition,
a comparison row names a mismatched period, or a note calls a computed figure reported.
Numbers follow one style: per-share values to three significant figures ($139), quoted
prices as quoted ($355.58), aggregates as $34.3B or $850M, units in table headers, and
model-written labels inside computed sections restyled the same way.

The writer gets eight sections of about 3,000 words in total and the shared quality
standard (`quality.RUBRIC`), whose writing standard (`quality.WRITING_STANDARD`, rule
N2) covers the eight prose sections only: conclusions first, short declarative
sentences, no contrast formulas or summary closers, no narration of the exhibits, and
the number style above. The writer copies computed and reported figures exactly and
does no arithmetic of its own. The section instructions allow the index benchmark only
as context, reported as the index's own ratio, and a free check does not count a
sentence that only reports the index's discount to its price as assigning the gap a
cause.

Before the paid review, free checks (`quality.prereview_checks`) flag figures that
match nothing in the computed output, the reported inputs or a primary source; figures
found only in unverified excerpts and not presented as outside context; figures cited
to excerpts although a primary source has them; convention rows described as adding
up; prose that assigns the gap to price a cause; table rows in the prose; a rise or
fall word whose own figures move the other way; uncited frequency claims; mentions of
Python or instructions; prose over 3,600 words; and the style rules: "USD" written for
a dollar filer, numbers of seven or more digits, em dashes, and more than two ", not"
contrasts or two sentences that narrate an exhibit. A minor check,
`unverified-company-fact`, flags a sentence whose citations are all unverified excerpts
and that presents their content as a company fact, and a sentence that sets the house
forecast beside consensus, guidance or an outside estimate; the fix call corrects it
before review. Its rule is in the shared quality standard: such a fact is deleted,
replaced by the primary-source figure, or stated as the view of an outside party the
sentence names, with no company conclusion and no comparison with the house forecast.
An excerpt, or the company's own filing quoted through one, is no outside party. The
variant-view brief no longer asks the writer to compare the house forecast with
consensus. Offline, on four first drafts, the check would have caught 7 of the 9
findings of this kind that the reviews raised. One fix call returns
quote-and-replacement edits that Python applies when the quote occurs once (deleting a
duplicated passage removes its second copy); what remains goes to the reviewer as minor
diagnostics. Python labels every citation of an unverified excerpt inline, as in [S16
unverified], and the source register carries the same label. The reviewer and the
finding check read the delivered memo up to the sources (`reader_view`), so a finding
quotes what the reader sees. The review applies the same quality standard, whose
rules carry IDs (B1-B3 blockers, M1-M6 majors, N1-N3 minors); each finding names the
rule it breaks and is graded no higher than that rule's class. Every blocker or major
the reviewer raises is then checked once more in a fresh context (`finding_check_N`). A
finding is removed only when that check refutes it with a stated reason; refuted
findings are listed in the memo's review status. Python settles some kinds without a
paid check: an M3 finding (a number in no source) whose quoted figures all trace to the
computed output, the reported tables or a primary source; a B2 finding whose quoted text
contains none of the trading-layer terms the rule names; an M6 finding whose text names
the figure as a house or computed one; and a finding refuted in an earlier review on the
same text under the same rule. The reviewer's answers on the central weakness and the gap
to price are recorded for the reader, not findings: Python's summary states which inputs
alone can and cannot bring value to the price, and the conclusion does not restate it. A
historical repurchase case whose amount is within 3% of a reported year's buybacks takes
that year's program value and average price, so the exhibits and the history table
quote one price.

Up to three revision rounds follow by default (`--max-revisions`) while material
findings remain, and each later review checks the earlier findings rather than
re-auditing from scratch. The loop stops as soon as no blocker or major survives, or
when only findings on computed text remain. A round answers the findings with exact
quote-and-replace edits (`prose_edits_N`) that Python applies and checks. Python
rejects an edit whose quote is not in the prose, and one that deletes a heading, drops
a citation its finding does not name, adds an untraced figure or a trading term, breaks
a style rule the quote did not, or changes a figure when its rule is not about figures
(B1, B3, M1, M3, M4 and M5 are). A new per-share figure must equal the computed value
its sentence names. A required finding whose edit did not apply gets one retry call
(`prose_edits_N_retry`) that is told why; one still unapplied is carried to the next
review as unresolved prose. `reviews/edits-N.json` records which finding each edit
answers and why any did not apply. The forecast response (`model_response_N`) runs
only when a surviving blocker or major names a forecast input, quotes the prose without naming a
rule of the standard, or comes from a Python check that found no valuation, or when the
edit call says only a forecast change answers a prose finding. Python owns part of the
plan (the assumptions label, the pinned rate records, the house probabilities and
terminal return, the per-year paths); those fields are left out of the proposal the
response edits and restored if it returns them, and a response whose forecast inputs
match the plan counts as unchanged. The memo is rewritten in full only when the forecast
inputs changed: in the live Alphabet and Coca-Cola runs a rewrite after an unapplied
edit had drawn new findings. A blocker or major
that quotes text Python writes and names no forecast input cannot be changed by any
round: it is listed separately in the review status and written to
`computed-text-findings.json` for a code fix.

The Reported history section prints the annual results, the latest eight fiscal
quarters, the return-on-capital table and the segment tables, scaled so a small company
reads in millions; writers no longer re-type them. Captions explain cells marked
depreciation only and years outside the filings scanned, the buyback price row names
its basis, only derived quarterly cells are marked, and segment members carry the
filing's own labels. The segment tables set the consolidated operating income beside
the segment rows, and their note states no sum and no cause for the difference: a live
Coca-Cola review rejected a note that called it unallocated costs. The valuation detail
also prints the equity bridge from enterprise value to value per share (lines with
nothing reported are named in one note), the forecast ROIC, the base-rate table and a
scale check of the final forecast year against today.

The evidence block is frozen when the plan stage writes it and sent first in every
Claude stage, marked for one-hour provider caching, so the whole run reads it at the
cache price. Sources from targeted research follow it uncached. The review and its
finding check cache evidence and valuation, and the draft comes last, uncached, so a
second review pays no new cache write for a changed draft. GPT-5.6 and GPT-6 caching is
explicit, and each run keeps one provider through a session id. A plan sample after the
first is reserved at the cache-read price once the ledger shows the prefix cached; a
cache miss is recorded and every later reservation counts the actual charge. Floats in
model-facing JSON are shown to six significant figures; saved files keep full
precision, and the rounded figures join the figure trace. A plan repair that returns
the rejected plan unchanged ends the loop, and repair reasons are saved with the run.

`tools/replay.py` re-runs the plan, draft, checks and review of a saved run on its
frozen evidence (`--from plan`, `--from draft` or `--from review`, which keeps the
saved plan and draft and runs only the free checks, their fix call and the review), so
a prompt or extraction change can be judged for a dollar or two instead of a full run.
`--plan-samples N` requests the plan N times on the identical prompt in one cache
session, values each sample and prints their spread and a driver decomposition, then
the consensus of every 3-sample subset and of all the samples; `--consensus-only`
reports the consensus of samples already saved, with no request.
`tools/consensus_check.py` does the same for saved sample folders, with a choice of
mean or median. `tools/rerender.py` rebuilds a
saved run's memo with the current rendering code at no cost, keeping its prose.

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
support, verify source truth, rewrite a previous memo or approve the new model. Its
`valuation.json` carries the continuity diagnostics and base rates a pipeline run
adds, as reports; nothing is sent back for repair.

## Costs and context size

`usage.json` is the cumulative request ledger. `cost-summary.json` summarizes
totals, stage/model breakdowns, reported prompt/completion/cached counts and their
coverage. It distinguishes known `usage.cost` from retained charge estimates;
unknown counts remain null. Cached prompt tokens are already part of prompt
usage, not additional tokens. Requested and returned model identities remain
visible. Reusing a saved stage makes no new ledger row, so this summary cannot
count stage-cache hits or infer their savings. It is not invoice reconciliation.

The budget preflight prices the plan samples (the later ones at the cache-read price),
the first draft, its fix call, review and finding check, and then each revision round:
its usual stages (targeted research, the edit call, the fix call, the review and its
check) and the stages it adds only when needed: the edit retry when an edit fails, the
forecast response when a finding concerns the forecast, and a rewrite when the forecast
inputs change. `budget-preflight.json` records a usual-path and a worst-case
reservation floor. Both are reservations, and neither predicts the bill.

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
partly. Since 2026-11-15 the answers are recorded for the reader and raise no finding:
Python's summary states which inputs alone can and cannot bring value to the price. A
review that gives neither answer gets a minor finding and can pass; the memo then shows
the assessment as not stated. The default reviewer is a different model from the
author: GPT-6.2 Sol for `balanced` (owner choice, 2026-11-26: the newest OpenAI
generation in the catalog) and Kimi K3.1 Pro for `economy`. When `--strong-model`
names the default reviewer, the profile's default author reviews instead.
`--review-model` can name any model, including the author, and is saved with the run;
a run folder saved before the cross-model default keeps its saved reviewer.

A model pass is a recorded assessment, not independent publication certification,
an assurance of no defects, an investment recommendation or proof of forecast
accuracy. Arithmetic checks establish tested identities under the supplied
assumptions. Source support, business economics, forward calibration, reinvestment,
terminal returns and suitability still require judgment. Unsupported sector
models, incomplete essential primary inputs and unresolved findings must remain
visible rather than being converted into a price target or approval.

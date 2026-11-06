# IntrinsicValue

A portable equity-research application requiring **one OpenRouter API key**.
It gathers financial and primary-source evidence, builds an editable valuation,
and reviews and revises a complete memo. The local application requires Python
3.10+ and **no runtime packages, subscription logins, data keys or hosted database**.

This standalone edition contains only the portable application, tests and public data.

## Run locally

Install Python 3.10+ and [Git](https://git-scm.com/downloads), then run:

```bash
git clone https://github.com/praveenchunduru/IntrinsicValue.git
cd IntrinsicValue
python -m investmentideas
```

If you prefer not to install Git, use **Code → Download ZIP** on this repository,
unpack it, and run `python -m investmentideas` from the extracted folder.

Open **http://127.0.0.1:8765**, enter your [OpenRouter key](https://openrouter.ai/settings/keys),
choose a ticker, profile and budget, and generate a full memo. Choose **economy**
for open-weight analysis or **balanced** for Claude analysis with open-weight research. Use `python3` or Windows
`py` if that is your Python command. A dedicated key with a provider credit limit
is recommended. The key is held in process memory; it is not stored in the browser,
research files or logs. This loopback application is for one local user.

Optional installation adds a command without adding runtime dependencies:

```bash
python -m venv .venv
```

Activate it with `source .venv/bin/activate` on macOS/Linux or
`.venv\Scripts\Activate.ps1` in Windows PowerShell, then run:

```bash
python -m pip install .
investmentideas
```

## Research and valuation

Full mode is the default. It retrieves keyless market and annual financial data,
recent SEC filings, and supported issuer releases. Bounded web searches through
OpenRouter broaden primary and independent evidence without a separate search key.
Unavailable sources and the limits of excerpt coverage are recorded explicitly.
An official SEC issuer-directory snapshot is a disclosed mapping fallback; it
never supplies stale company financial statements. Yahoo financials are secondary
sources, and their normalized dates are distinguished from actual filing periods.

Supported primary inline-XBRL facts lock historical balances, share counts and the
financial-debt/cash/financing-lease bridge before forecasting. Models cannot invent
rounding residuals or override those reported values. Conflicting or incomplete
essential primary coverage produces no price target; unknown facts never become zero.
The all-cash-as-nonoperating classification and unidentified additional claims remain
explicit house modeling policies, not reported facts.

The full workflow produces evidence synthesis, a source-referenced forecast plan,
a Python-computed bear/base/bull FCFF DCF, and an argument-led memo. A fresh model
context reviews the frozen draft, evidence and valuation. Material findings trigger
targeted research, model corrections, recomputation and prose revision. Corrections
are bounded by the revision limit and the remaining budget. Unresolved material
findings remain visible; they are never converted into approval to finish a run.

The valuation includes operating growth/margins, taxes, incremental sales-to-capital,
terminal reinvestment at terminal ROIC, discount rates, net debt and noncommon claims,
scenario weighting, sensitivities and a reverse DCF where a market price is available.
Optional named `counterfactuals` in assumptions.json override base forecast inputs only;
Python computes their values, cashflows and revenue paths without changing the house
probabilities or weighted value. Reverse-DCF solutions also include annual revenue paths.
Missing actuals are unknown, never silently zero. Forecasts are explicit house
assumptions. This unlevered FCFF model is not appropriate for every sector or security;
if essential inputs or an appropriate model are unavailable, no invented price target
is produced. It does not model a detailed debt refinancing, NOL or capital-lease schedule.

## CLI

```bash
# macOS/Linux: enter without putting the value in shell history
read -rs OPENROUTER_API_KEY
export OPENROUTER_API_KEY

# Full memo: open-weight extraction/research; Claude analysis and review
python -m investmentideas AAPL --profile balanced --budget 2

# Cheaper full analysis, or a four-call financial overview
python -m investmentideas AAPL --profile economy --budget 2
python -m investmentideas AAPL --quick --profile economy --budget 1

# Supply focused, dated evidence if a source is unavailable
python -m investmentideas AAPL --evidence filing.txt --budget 2

# Append archived primary evidence, then investigate independent review notes
python -m investmentideas AAPL --sources primary-sources.json --budget 3

# Compute explicit forecast counterfactuals and review the same saved run
python -m investmentideas AAPL --counterfactuals cases.json --budget 3

# Review an edited memo body against the saved evidence and computed model
python -m investmentideas AAPL --draft edited-body.md --budget 3

# Investigate independent review notes in the same day's saved full run
python -m investmentideas AAPL --review-notes findings.txt --max-revisions 6 --budget 3

# Choose an independent final reviewer without repeating completed research
python -m investmentideas AAPL --review-model openai/gpt-5.4 --budget 3

# Explicit model overrides; unavailable IDs fail rather than silently changing models
python -m investmentideas AAPL --easy-model qwen/qwen3-30b-a3b-instruct-2507 \
  --strong-model deepseek/deepseek-v3.2
```

On PowerShell use your preferred local secret mechanism to set
`$env:OPENROUTER_API_KEY`, or use the browser form. [portable environment example](investmentideas/.env.example) documents the
variable; the standard-library runner does not automatically load `.env` files.

## Editable, auditable output

Runs are saved below `research-output/TICKER/`. Full runs use `full-RUN_HASH` folders.
The same ticker/models/evidence on the same UTC date resume saved stages rather
than paying again. A new day refreshes the evidence; use `--output fresh-folder`
for a new same-day snapshot. OS file locks prevent duplicate concurrent full runs.

- `memo.md`: the research draft, computed valuation, sources, limitations and review status.
- `sources.json`, `sources_raw/`: provenance, exact excerpts, retrieval dates and document archives.
- `source-ledger.json`: paragraph-to-citation mapping; citation syntax is not semantic proof.
- `assumptions.json`, `valuation.json`, `cashflows.csv`, `sensitivity.csv`: editable inputs and reproducible calculations.
- `reviews/`, `revisions/`: frozen drafts, recorded findings, model responses and covered-file hashes.
- `state.json`, `usage.json`: checkpoints, exact requested/returned models, token usage and costs/reservations.

The browser provides authenticated downloads. Edit assumptions and recompute **offline**:

```bash
python -m investmentideas --revalue assumptions.json --output revalued
# Or:
python -m investmentideas.valuation assumptions.json --output revalued
```

A provided draft archives the previous body and invalidates its approval. Supply the
author body only; valuation tables, source register and status are regenerated.

An offline revaluation updates model artifacts; it does not silently rewrite or
reapprove a previous memo. Generate/review a revised memo when inputs change.

## Model routing and costs

| Profile | Extraction and web research | Forecast, author, review and correction |
|---|---|---|
| `balanced` (default) | Qwen3 30B A3B Instruct, open-weight | Claude Sonnet 4.6 |
| `economy` | Qwen3 30B A3B Instruct, open-weight | DeepSeek V3.2, open-weight |

Prices and model availability are checked against the live OpenRouter catalog.
Provider fallback can use another provider **for the same model** under the same
price caps. There are no automatic client retries or silent switches to expensive
models. A separate explicit `--review-model` override is saved with the run and
invalidates its prior review; forecasts and completed research are retained.
Interrupted/failed requests preserve a reservation because a provider may
already have charged; completed stages are reused.

As observed on 2026-11-06, Qwen cost $0.10/$0.30, DeepSeek $0.26/$0.38, and Sonnet
$3/$15 per million input/output tokens. Exa Auto search through OpenRouter cost
$0.007 per request including up to ten results; the runner requests five and reserves
extra search/context allowance. Prices change. Native search is not enabled merely
because a model lists a native-search price.

A live four-call AAPL economy overview reported **$0.00757138**, but its initial
memo contained factual/date errors found by independent source inspection. That
run establishes transport/cost functionality, not a quality benchmark. The full
pipeline adds primary evidence, computed arithmetic, editable valuation and bounded
revision. Full-memo cost depends on evidence volume and revision count, so the
small overview price must not be extrapolated as a promised full-memo cost.

Local budgets use conservative input-byte and maximum-output estimates before each
request. They are **not provider-enforced total spending caps**. Limit a dedicated
OpenRouter key too. The initial full run budget defaults to $2 in the local app;
increase the cumulative allowance explicitly if more research or review is needed.
A run can stop with unresolved findings when its budget or revision limit is reached;
that status is preserved rather than treated as approval.

## Reviewed example

The [AAPL memo dated November 6, 2026](examples/AAPL-2026-11-06/memo.md) includes
its [editable assumptions](examples/AAPL-2026-11-06/assumptions.json),
[computed valuation](examples/AAPL-2026-11-06/valuation.json),
[source records](examples/AAPL-2026-11-06/sources.json), primary archives and
[final model review](examples/AAPL-2026-11-06/review.json).
The final fresh-context GPT-5.4 review returned pass with zero findings.
Independent arithmetic checks matched 818 computed numeric fields and passed
211 arithmetic checks plus 41 memo-number checks. These establish the recorded
checks, not forecast plausibility or investment certainty.

The cumulative AAPL run, including iterative research, corrections and failed or
formatting requests, recorded **$12.2630191** in reported charges and retained
reservations. This is an observed development-and-review run, not a promised price
for a new memo. It used Qwen for easy work, Claude for analytical work, and an
explicit GPT-5.4 final-review override. No key or account metadata is published.
The example is a dated snapshot, not a source of current quotes or an automatic
fallback for new research.

Recompute it without an API key:

```bash
python -m investmentideas --revalue examples/AAPL-2026-11-06/assumptions.json --output revalued
```

## Verification and limitations

```bash
python -m unittest discover -s tests/public_runner -v
python -m pip install .
investmentideas --help
```

All 156 portable tests pass from source and a fresh installation. CI runs synthetic
evidence and mocked transports on Python 3.10, 3.12 and 3.13 without model calls or
secrets. [Live-validation guidance](docs/openrouter_live_validation.md) explains
local budget controls and review provenance. Model review is a recorded assessment,
**not independent publication certification or a guarantee of investment truth**.
Financial facts, forecasts and source support still require careful inspection.
The canonical input adapter currently recognizes a bounded set of US-GAAP/DEI
inline-XBRL concepts; unsupported taxonomies or missing bridge components require
additional primary coverage rather than a model-generated substitute.

This public edition is a standalone local research application. It does not
include the original repository’s signed publication system or private history.

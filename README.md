# IntrinsicValue

A portable equity-research application requiring **one OpenRouter API key**.
It gathers financial and primary-source evidence, builds an editable valuation,
and reviews and revises a complete memo. The local application requires Python
3.10+ and **no runtime packages, subscription logins, data keys or hosted database**.

This standalone edition contains only the portable application, tests and public data.

**Audit status:** an independent external audit assessed the showcase memo at B−
and the process/software at C, and reproduced live SEC access, reported-input
coverage and default-budget failures. The previous benchmark-parity claim is
withdrawn. Repair and validation status is tracked in [audit status](docs/audit-status.md).

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

Full mode retrieves keyless market/annual financial data, recent SEC filings and
supported issuer releases. Bounded OpenRouter web searches broaden the evidence
without a separate search key. Unavailable sources, excerpt limits, secondary
financial data and issuer-directory mapping fallbacks are disclosed.

Primary inline-XBRL facts lock supported balances, shares and the financial-debt,
cash and finance-lease bridge. Conflicting or incomplete essential primary coverage
produces no price target; unknown facts never become zero. Cash classification and
unidentified additional claims remain explicit house policies, not reported facts.

The workflow synthesizes evidence, builds a source-referenced forecast plan,
computes a bear/base/bull FCFF DCF in Python and writes a memo. Fresh-context review
can trigger targeted research, corrections, recomputation and revision within the
remaining budget and revision limit. Unresolved material findings remain visible.

The valuation includes operating growth/margins, taxes, incremental sales-to-capital,
terminal reinvestment at terminal ROIC, discount rates, net debt and noncommon claims,
scenario weighting, sensitivities and a reverse DCF where a market price is available.
Optional company-specific operating schedules derive revenue and EBIT from sourced
starting categories and explicit forward drivers. Dated required-return inputs can
compute WACC; sector betas, issuer return history and debt-yield proxies retain their
different conventions and dates. Computed counterfactuals, calibration comparisons
and monitoring thresholds show conditional value changes without altering house
scenario weights. See the [underwriting guide](docs/underwriting.md) for schemas,
accounting conventions, editable plans and limits. Forecasts remain house assumptions;
this FCFF model does not suit every sector or security and has no detailed refinancing,
NOL or lease-adjustment schedule.

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

# Replay dated sources and review a supplied company-specific forecast
python -m investmentideas AAPL --snapshot sources.json --forecast-plan assumptions.json --budget 3

# Investigate independent review notes in the same day's saved full run
python -m investmentideas AAPL --review-notes findings.txt --max-revisions 6 --budget 3

# Choose an independent final reviewer without repeating completed research
python -m investmentideas AAPL --review-model openai/gpt-5.4 --budget 3

```

Use `--evidence` for focused text, `--sources` for supplemental source records,
`--counterfactuals` for computed base-case overrides, and `--draft` for an edited
author body. `--easy-model` and `--strong-model` select explicit model IDs; unavailable
IDs fail. See `--help` and the [underwriting guide](docs/underwriting.md).

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
- `state.json`, `usage.json`: checkpoints, requested/returned models, reported token usage and costs/reservations.
- `cost-summary.json`: cumulative stage/model costs, retained estimates and reported token-count coverage.

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

The cost summary separates provider-reported cost from retained estimates.
`state.json` also records full-versus-presented valuation-context UTF-8 byte sizes.
These measurements do not establish token savings, optimal routing or a quality/cost
frontier; see the [measurement limits](docs/underwriting.md#costs-and-context-size).

As observed on 2026-11-06, Qwen cost $0.10/$0.30, DeepSeek $0.26/$0.38, and Sonnet
$3/$15 per million input/output tokens; Exa Auto search cost $0.007 per request.
Prices change. A four-call AAPL economy overview reported **$0.00757138** but had
factual/date errors. It establishes transport/cost functionality, not memo quality
or a promised full-run price. Full-memo cost depends on evidence and revisions.

Local budgets use conservative input-byte and maximum-output estimates before each
request. They are **not provider-enforced total spending caps**. Limit a dedicated
OpenRouter key too. The initial full run budget defaults to $2 in the local app;
increase the cumulative allowance explicitly if more research or review is needed.
A run can stop with unresolved findings when its budget or revision limit is reached;
that status is preserved rather than treated as approval.

## Dated examples

The [revised AAPL memo dated November 6, 2026](examples/AAPL-2026-11-06-revised/memo.md)
uses a company-specific operating bridge, explicit required-return inputs and
computed observation-to-value cases. Its [assumptions](examples/AAPL-2026-11-06-revised/assumptions.json)
and [valuation](examples/AAPL-2026-11-06-revised/valuation.json) are editable and
reproducible offline. Read the review findings and source limits alongside the memo;
arithmetic and model review do not establish forecast plausibility or investment certainty.
The [quality record](examples/AAPL-2026-11-06-revised/quality-review.md) includes
fresh benchmark comparisons, conflicting automated reviews and independent
adjudications. Its earlier A / near-A+ assessment did not survive the independent
external audit. The paper was assembled through agent-directed editorial work,
not demonstrated as an output of an unassisted fresh pipeline run. The automated
attempts did not approve it. Historical files are retained for reproduction;
they do not establish benchmark parity or current pipeline readiness.
Its five additional OpenRouter requests report **$1.665431** in usage cost.

The [earlier AAPL snapshot](examples/AAPL-2026-11-06/memo.md) retains its own
[source records](examples/AAPL-2026-11-06/sources.json) and
[model-review record](examples/AAPL-2026-11-06/review.json).

The cumulative AAPL run, including iterative research, corrections and failed or
formatting requests, recorded **$12.2630191** in reported charges and retained
reservations. This is an observed development-and-review run, not a promised price
for a new memo. It used Qwen for easy work, Claude for analytical work, and an
explicit GPT-5.4 final-review override. No key or account metadata is published.
The example is a dated snapshot, not a source of current quotes or an automatic
fallback for new research.

Recompute it without an API key:

```bash
python -m investmentideas --revalue examples/AAPL-2026-11-06-revised/assumptions.json --output revalued
```

## Verification and limitations

```bash
python -m unittest discover -s tests/public_runner -v
python -m pip install .
investmentideas --help
```

The v0.2.0 release passed **268 tests** from source and a fresh installed wheel. The
[CI matrix](https://github.com/praveenchunduru/IntrinsicValue/actions/runs/42261827883)
passes on Python 3.10, 3.12 and 3.13. CI runs synthetic evidence and mocked transports without model calls or
secrets. [Live-validation guidance](docs/openrouter_live_validation.md) explains
local budget controls and review provenance. Model review is a recorded assessment,
**not independent publication certification or a guarantee of investment truth**.
Those primarily mocked tests missed material live-data and workflow failures
identified by the external audit. A fresh end-to-end baseline is required before
claiming the automated workflow works in practice. Financial facts, forecasts and
source support still require careful inspection.
The canonical input adapter currently recognizes a bounded set of US-GAAP/DEI
inline-XBRL concepts; unsupported taxonomies or missing bridge components require
additional primary coverage rather than a model-generated substitute.

This public edition is a standalone local research application. It does not
include the original repository’s signed publication system or private history.

# IntrinsicValue

**Open-source equity research that values a company the way a disciplined fundamental
investor would, and shows every step.**

Give it a ticker. It reads the company's SEC filings, builds a ten-year forecast, values
the business with a discounted cash flow model and writes a research memo. A second
model from a different lab then checks the memo against the evidence. A run costs about
$5 in model fees and takes under half an hour. It needs Python 3.10+ and one OpenRouter
API key, and installs nothing else.

The aim is hedge-fund-grade fundamental analysis that anyone can run, inspect and
improve.

## What it is for

A professional analyst spends days on what one run produces: a sourced view of what a
business is worth, what its share price already assumes, and what evidence would change
the answer. IntrinsicValue makes that standard of work available to anyone. It also
makes the method public. Every valuation rule is written down, dated and shown beside
its alternatives, so you can see exactly where you agree and where you would choose
differently.

Each run gives you:

- **A value per share** for a base case and two stress cases, with the forecast behind
  each one.
- **What the price needs.** The growth, margin or return on capital that would make
  today's price fair, one input at a time. For revenue growth it also shows how often
  companies of that size have achieved it.
- **A model you can edit.** Every assumption sits in one file. Change a number and
  revalue offline in seconds, with no API key.
- **A memo** on the business, management's use of capital, the forecast, the risks and
  the observations that would change the value.
- **A source trail.** Reported figures come from SEC filings and code does the
  arithmetic. Each claim cites its source, and web excerpts that could not be verified
  are labelled.

Language models do the reading and the writing. Code does every calculation. A model
from a different lab reviews the result, and each finding is checked again before it
counts.

## Whose methods it follows

The valuation rules draw on the published work of investors and teachers who have
written down how they do it:

- **Aswath Damodaran** on the discount rate and the forecast: a current implied equity
  risk premium, industry betas, growth that has to be paid for with reinvestment, R&D
  treated as investment, and long-run growth capped at the risk-free rate.
- **Michael Mauboussin** on returns and expectations: return on invested capital
  measured as in his 2022 paper, a continuing value in which new investment earns its
  cost of capital, base rates for growth, and the question of what the price already
  expects.
- **Stanley Druckenmiller** on rates and rate of change: the memo shows the value under
  other interest-rate regimes and sets the latest quarters beside the annual history.

The method was critiqued from each of these three perspectives and revised in response.
None of them has reviewed or endorsed this project. The full rules, with sources, are in
the [underwriting guide](docs/underwriting.md).

## How to read the output

- **The base case is the headline.** The bear and bull cases are stress tests and carry
  no probabilities.
- **The rules are conservative on purpose.** After year ten, new investment earns only
  its cost of capital, so growth beyond the forecast adds no value. Under the same rules
  the S&P 500 is worth about 0.80 of its price (November 2026). Compare a company's
  value / price with that figure: a company at 0.80 is priced like the market.
- **You can price your disagreement.** The convention table revalues the company one
  change at a time: a longer period of high returns, another discount rate, another
  terminal assumption.
- **The value is an estimate with a date.** It rests on a forecast that a language model
  wrote from public filings. A forecast can be wrong, and two runs on the same company
  can differ.
- **It is a starting point.** Use it to learn a company quickly, to test your own view,
  or to see what a price assumes.

## Not investment advice

IntrinsicValue is a research and education tool. Nothing it produces is investment
advice, a recommendation to buy or sell any security, or a price target. Language models
write and review the memo, and they make mistakes. The software checks do not establish
that a forecast is right. The authors are not your financial advisers and accept no
liability for decisions made with this software. Do your own work, or consult a licensed
adviser, before you invest.

## Status

In three live runs on 2026-11-27 (Microsoft, Apple and Coca-Cola), one memo passed the
model review after one revision and two ended with open findings. An external audit in
early November 2026 graded an earlier version's showcase memo B− and its process C; the
benchmark-parity claim made before it is withdrawn. What has been repaired since, what
each live run found and what is still open are in [audit status](docs/audit-status.md).

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
enter a real SEC requester contact email, choose a ticker, profile and spending allowance,
and generate a full memo. The email identifies requests to SEC; it is not another paid
API key. It stays in memory and is sent only to SEC. Choose **economy**
for open-weight analysis or **balanced** for Claude analysis with open-weight research; a different model reviews the memo in both. Use `python3` or Windows
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

Primary inline-XBRL and normalized SEC Companyfacts records lock supported annual
revenue, shares, the financial-debt, cash and finance-lease bridge, non-operating
investments, noncontrolling interests, preferred stock, pension obligations and the
dilutive share increment. Alternative
reported totals and disjoint components are reconciled; similar tag names alone
do not establish accounting scope. Conflicting or incomplete essential primary coverage
stops before paid research and produces no price target. The saved input-coverage report
identifies the missing disclosures. Cash classification and unidentified additional claims
remain explicit house policies, not reported facts.

Two debt inputs can rest on a labelled assumption. When the whole SEC entity record shows no
short-term borrowing amount beyond reported commercial paper and other short-term borrowings,
or no finance lease liability, the missing category is assumed zero. The coverage report marks
the field `pinned_with_disclosed_assumption`, the bridge lists the concepts that were scanned,
and the memo states the assumption. An undisclosed borrowing would lower equity value. An
equity-bridge line with no reported concept, such as preferred stock, is likewise zero by a
labelled assumption. Issuers
whose own facts conflict, or that report shares only by class, are refused with the specific
reason; see the [issuer sample](docs/audit-status.md#live-validation-on-2026-11-14).

The workflow synthesizes evidence, builds a source-referenced forecast plan,
computes a bear/base/bull FCFF DCF in Python and writes a memo. The first draft is
built to stand on its own:

- Python extracts the filings' revenue, cost and operating income by product, segment
  and geography, five years of reported history, the latest eight fiscal quarters, and
  the 10-K's margin, inventory, commitment and buyback disclosures. The models see them
  before any web excerpt.
- Python renders every table and a valuation summary at the top of the memo,
  including what the price requires of one input at a time and where that growth falls
  in published base rates. The model writes the interpretation around them and does no
  arithmetic of its own.
- Free checks trace every figure in the prose to the computed output or a primary
  source, then one small call fixes what they find. Citations of unverified web
  excerpts are labelled in the text.
- A different model reviews the memo against the same written quality standard
  the writer was given. One revision follows by default if material findings remain;
  findings still open stay visible.

The discount rate follows the conventions the equity risk premium was solved under.
Damodaran's implied premium assumes the market grows at the Treasury rate forever; a
valuation that pairs it with lower terminal growth values the market itself at about
80% of its price. When the cash flows are in US dollars and the run collected that
premium and the industry beta table, the pipeline therefore starts from the current
cost of capital, moves to market-average business risk by year 10, and sets terminal
growth to the risk-free rate unless a scenario gives a company-specific reason for
less. Present values are dated at the valuation date when the reported balance sheet
allows it. Year-1 margins must start from the last reported results unless the plan
cites evidence for a step. Costs are shares of revenue, final-year margins have
ceilings from the reported history and the latest twelve months, the terminal return on
new capital equals the terminal cost of capital after the 10-year forecast (Mauboussin's
steady state, with 10 years as a house horizon), the continuing value adds back net R&D
investment, and the base case is the headline value with bear and bull as unweighted
stress cases.
The memo reports value relative to price beside a stylized S&P 500 valued under the
same conventions, the discount rate the price implies beside the required return, and
the value under alternative conventions one change at a time. Beside those rows it
values the base case at other horizons, at Mauboussin's industry fade, under other rate
regimes and with Damodaran's revenue-weighted country risk premium. The memo also places each scenario's revenue
growth, and the growth the price requires, in Mauboussin's published base rates for
companies of the same size. None of these exhibits changes the value or checks the plan.
The memo sets no entry prices, margins of safety or position sizes. See
[market-consistent conventions](docs/underwriting.md#keep-the-discount-rate-consistent-with-the-equity-risk-premium).

The valuation includes operating growth/margins, taxes, incremental sales-to-capital (one
ratio or a path), terminal reinvestment at terminal ROIC, discount rates, net debt,
non-operating investments, noncommon claims and diluted shares, bear, base and bull
cases, forecast return on invested capital, sensitivities and a reverse DCF where a
market price is available.
Optional company-specific operating schedules derive revenue and EBIT from sourced
starting categories and explicit forward drivers. Dated required-return inputs can
compute WACC; sector betas, issuer return history and debt-yield proxies retain their
different conventions and dates. Computed counterfactuals, calibration comparisons
and monitoring thresholds show conditional value changes without altering the house
cases. See the [underwriting guide](docs/underwriting.md) for schemas,
accounting conventions, editable plans and limits. Forecasts remain house assumptions;
this FCFF model does not suit every sector or security and has no detailed refinancing,
NOL or lease-adjustment schedule.

## CLI

```bash
# macOS/Linux: enter without putting the value in shell history
read -rs OPENROUTER_API_KEY
export OPENROUTER_API_KEY

# Replace with your real requester email for SEC access; this is not a paid key
export SEC_CONTACT_EMAIL='your-real-email@example.com'

# Full memo: Claude Haiku research, Claude Opus analysis, GPT-6.2 Sol review
python -m investmentideas AAPL --profile balanced --budget 20

# Cheaper full analysis, or a four-call financial overview
python -m investmentideas AAPL --profile economy --budget 20
python -m investmentideas AAPL --quick --profile economy --budget 2

# Replay dated sources and review a supplied company-specific forecast
python -m investmentideas AAPL --snapshot sources.json --forecast-plan assumptions.json --budget 20

# Investigate independent review notes in the same day's saved full run
python -m investmentideas AAPL --review-notes findings.txt --max-revisions 3 --budget 20

# Re-run the plan, draft and review of a saved run on its frozen evidence
python tools/replay.py research-output/AAPL/full-RUN_HASH replay-output --from draft

# Choose a different final reviewer without repeating completed research
python -m investmentideas AAPL --review-model moonshotai/kimi-k3.1-pro --budget 20

```

Use `--evidence` for focused text, `--sources` for supplemental source records,
`--counterfactuals` for computed base-case overrides, and `--draft` for an edited
author body. `--easy-model` and `--strong-model` select explicit model IDs; unavailable
IDs and unsupported pricing fail before paid research. `--sec-contact your-real-email@example.com`
sets SEC requester identity for one run without saving it. `SEC_CONTACT_EMAIL` or a
contact-bearing `SEC_USER_AGENT` can supply it instead. See `--help` and the
[underwriting guide](docs/underwriting.md).

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

| Profile | Extraction and web research | Forecast, author and correction | Review |
|---|---|---|---|
| `balanced` (default) | Claude Haiku 5.5 | Claude Opus 5.5 | GPT-6.2 Sol |
| `economy` | DeepSeek V4.1 Flash, open-weight | DeepSeek V4 Pro, open-weight | Kimi K3.1 Pro, open-weight |
| `dev` (pipeline testing only) | Claude Haiku 5.5 | Claude Haiku 5.5 | GPT-6 Luna |

Each slot uses the newest model generation in the OpenRouter catalog (checked
2026-11-26); the tier follows the job. On the same Microsoft plan and evidence, Claude
Opus 5.5 wrote a shorter draft with fewer review findings than Claude Sonnet 4.6 or 5.5.

All selected models and their supported token pricing are checked against the live
OpenRouter catalog before paid work. A price that changes with prompt length or with
the UTC time of day, as DeepSeek V4.1 Flash's does, is reserved at its highest listed
level.
Provider fallback can use another provider **for the same model** under the same
price caps. There are no automatic client retries or silent switches to expensive
models. The default reviewer differs from the author model: when `--strong-model`
names the profile's reviewer, the profile's default author reviews instead. An
explicit `--review-model` can name any model, including the author; it is saved with
the run and invalidates its prior review;
forecasts and completed research are retained. A run folder saved before the
cross-model default keeps its saved reviewer unless `--review-model` is given.
Interrupted/failed requests preserve a reservation because a provider may
already have charged; completed stages are reused.

The cost summary separates provider-reported cost from retained estimates.
`state.json` also records full-versus-presented valuation-context UTF-8 byte sizes.
These measurements do not establish token savings, optimal routing or a quality/cost
frontier; see the [measurement limits](docs/underwriting.md#costs-and-context-size).

As listed on 2026-11-26, Claude Haiku 5.5 cost $0.10/$0.50, Claude Opus 5.5 $4/$20 and
GPT-6.2 Sol $2/$10 per million input/output tokens ($4/$15 above 272,000 prompt tokens); Exa Auto search cost $0.007 per
request. With the evidence cached across stages, a balanced first draft through review
and its finding check is estimated at about $3 and a run with one revision at about
$4.50, from measured token counts; not yet observed on a bill. The default allowance is
$20: a ceiling, because each call reserves its prompt bytes at the model's highest
price tier before it is sent, and a GPT-6.2 Sol review alone reserves about $2.50.
Prices change. A four-call AAPL economy overview reported **$0.00757138** but had
factual/date errors. It establishes transport/cost functionality, not memo quality
or a promised full-run price. Full-memo cost depends on evidence and revisions.

Local budgets use conservative input-byte and maximum-output estimates before each
request. They are **not provider-enforced total spending caps**. Limit a dedicated
OpenRouter key too. Full research defaults to a **$20 total planning allowance** in
the CLI and local app; quick research retains $2. This is not a promised charge or
guaranteed completion. An explicit allowance is never raised automatically; prior
charges and uncertain reservations still count when resuming the same settings.

Free evidence collection precedes primary-input and budget preflights. An insufficient
initial allowance stops before paid research and saves `budget-preflight.json` with
an actionable resume instruction. Its known-context reservation floor covers the first
draft and one review; unknown future search results, generated forecasts, valuation
context, memo text, repairs and revisions can require more. Complete actual prompts
are reserved again before each request. If primary evidence is missing, retry free
collection with a SEC contact or supply primary disclosures; dated snapshots remain frozen.
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

The current source passes **854 tests** locally on Python 3.9, 3.12 and 3.13; the
v0.2.0 release passed 268. The
[CI matrix](https://github.com/praveenchunduru/IntrinsicValue/actions/runs/42261827883)
tests Python 3.10, 3.12 and 3.13. CI runs synthetic evidence and mocked transports without model calls or
secrets. [Live-validation guidance](docs/openrouter_live_validation.md) explains
local budget controls and review provenance. Model review is a recorded assessment,
**not independent publication certification or a guarantee of investment truth**.
Those primarily mocked tests missed material live-data and workflow failures
identified by the external audit. A fresh end-to-end baseline is required before
claiming the automated workflow works in practice. Financial facts, forecasts and
source support still require careful inspection.
The canonical input adapter recognizes a bounded set of US-GAAP/DEI concepts from
inline filings and SEC Companyfacts; unsupported taxonomies or missing bridge components require
additional primary coverage rather than a model-generated substitute.

This public edition is a standalone local research application. It does not
include the original repository’s signed publication system or private history.

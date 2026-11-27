# Reference

Setup, commands, output files, models and costs, dated examples and test coverage. The
valuation rules are in the [underwriting guide](underwriting.md). The external audit,
live runs and open issues are in [audit status](audit-status.md).

Commands use `python3`, as on macOS and Linux. On Windows, type `py` in its place.

## Run locally

Install Python 3.9 or newer (the Mac's own `python3` works) and
[Git](https://git-scm.com/downloads). On a Mac that has never used developer tools, the
first `git` opens Apple's "install Command Line Tools" window; accept it once. Then:

```bash
git clone https://github.com/praveenchunduru/IntrinsicValue.git
cd IntrinsicValue
python3 -m intrinsicvalue --setup
python3 -m intrinsicvalue MSFT
```

Without Git, use **Code → Download ZIP** on the repository page, unpack it, and run the
same commands from the extracted folder. `python3 -m investmentideas` is the same program
under its package name.

`--setup` asks for your [OpenRouter key](https://openrouter.ai/settings/keys) and your
email and saves them in a `.env` file in the folder you run from, readable only by you.
The SEC asks every automated visitor for a contact email before it serves filings; it is
not another paid key, and it is sent only to sec.gov. The command line and the browser
form both read `.env` when they start. Only `OPENROUTER_API_KEY` and `SEC_CONTACT_EMAIL`
are read from it, a value exported in your shell takes priority, and a run never writes
to it. [`.env.example`](../.env.example) shows the two lines if you prefer to write the
file by hand. In PowerShell you can instead set `$env:OPENROUTER_API_KEY="sk-or-..."` and
`$env:SEC_CONTACT_EMAIL="you@example.com"` for one session.

Before any work a run checks, in order: the ticker is in the SEC company list bundled
with the program (a company name gets ticker suggestions), an SEC email is set, a key is
set, and OpenRouter accepts the key (one free call). Each failure stops at once with
what to do next. A dedicated key with a provider credit limit is recommended.

**Browser form.** Run `python3 -m intrinsicvalue` with no ticker. It prints an address
ending in `#token=...` and opens it in your browser on macOS, Windows and Linux desktops
(`--no-browser` to skip). Keep that terminal window open while a run is going. The form
holds the key and email in the program's memory for the session, never in the browser,
research files or logs, so a second ticker needs no re-pasting. It shows progress, the
rendered memo and downloads, and has a Stop button that ends the run after the current
step. The server listens on 127.0.0.1 only and is for one local user.

**Update.** `git pull` in the folder (or download the ZIP again and keep your
`research-output` folder). [CHANGELOG.md](../CHANGELOG.md) lists what changed, and
`python3 -m intrinsicvalue --version` shows the version you have.

Optional installation adds the `intrinsicvalue` and `investmentideas` commands without
runtime dependencies. On Debian and Ubuntu, `python3 -m venv` needs the `python3-venv`
package first.

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
python3 -m pip install .
intrinsicvalue --help
```

## Commands

```bash
# Once: save your OpenRouter key and your email (for SEC access) in .env in this folder.
python3 -m intrinsicvalue --setup

# Full memo: Claude Haiku research, Claude Opus analysis (three forecasts), GPT-6.2 Sol review
python3 -m intrinsicvalue AAPL

# Check inputs and collect evidence for free: no key, no model calls
python3 -m intrinsicvalue AAPL --dry-run

# Open the finished memo in your browser
python3 -m intrinsicvalue AAPL --open

# Past runs: date, ticker, status, base value, value / price, cost and memo path
python3 -m intrinsicvalue --list

# One forecast instead of three, and at most one revision round
python3 -m intrinsicvalue AAPL --plan-samples 1 --max-revisions 1

# Cheaper full analysis, or a four-call financial overview
python3 -m intrinsicvalue AAPL --profile economy
python3 -m intrinsicvalue AAPL --quick --profile economy

# Replay dated sources and review a supplied company-specific forecast
python3 -m intrinsicvalue AAPL --snapshot sources.json --forecast-plan assumptions.json

# Investigate independent review notes in the same saved full run
python3 -m intrinsicvalue AAPL --review-notes findings.txt --max-revisions 3

# Re-run the plan, draft and review of a saved run on its frozen evidence
python3 tools/replay.py research-output/AAPL/full-RUN_HASH replay-output --from draft

# Measure how far the value moves across four plan samples on the same evidence
python3 tools/replay.py research-output/AAPL/full-RUN_HASH samples-output --plan-samples 4 --budget 8

# Rebuild a saved memo with the current rendering code, at no cost
python3 tools/rerender.py research-output/AAPL/full-RUN_HASH memo-rerendered.md

# Convert any memo.md to a readable, printable memo.html
python3 -m investmentideas.memo_html research-output/AAPL/full-RUN_HASH/memo.md

# Choose a different final reviewer without repeating completed research
python3 -m intrinsicvalue AAPL --review-model moonshotai/kimi-k3.1-pro
```

Use `--evidence` for focused text, `--sources` for supplemental source records,
`--counterfactuals` for computed base-case overrides, and `--draft` for an edited
author body. `--plan-samples N` (1-5) sets how many forecasts are written and averaged:
three by default in the balanced profile, one in the others. `--max-revisions N` (0-8,
default 3) limits the revision rounds after the first review. `--easy-model` and
`--strong-model` select explicit model IDs; unavailable IDs and unsupported pricing
fail before paid research. `--sec-contact you@example.com` sets the SEC contact for one
run without saving it; `SEC_CONTACT_EMAIL` or a contact-bearing `SEC_USER_AGENT` can
supply it instead. Every file option must name a regular file within its size limit.
See `--help` and the [underwriting guide](underwriting.md).

A run prints plain steps ("Step 4 of 9: writing the forecast (Claude Opus 5.5)") with the
time and money so far, and ends with what happened, the base value and value / price, the
cost, the memo path and the next step. Ctrl+C stops cleanly; run the same command again
to resume.

## Editable, auditable output

Runs are saved below `research-output/TICKER/` in the folder you run from (`--output`
chooses another). Full runs use `full-RUN_HASH` folders; `--list` finds them for you.
The same ticker, models and evidence resume saved stages rather than paying again: on
the same UTC date, or across midnight UTC for an unfinished run started within the last
24 hours. A later run starts fresh evidence; use `--output fresh-folder` for a new
same-day snapshot. OS file locks prevent duplicate concurrent full runs.

- `memo.html`: the memo as a self-contained page that opens in any browser and prints to PDF.
- `memo.md`: a computed first page, the eight prose sections, the valuation detail,
  reported history, method notes, sources and review status. A run that stops before
  writing a memo leaves a short `memo.md` saying why.
- `sources.json`, `sources_raw/`: provenance, exact excerpts, retrieval dates and document archives.
- `source-ledger.json`: paragraph-to-citation mapping; citation syntax is not semantic proof.
- `assumptions.json`, `valuation.json`, `cashflows.csv`, `sensitivity.csv`: editable inputs and reproducible calculations.
- `plan-consensus.json`: each forecast sample, its value and value impact, and how they were combined.
- `reviews/`, `revisions/`: frozen drafts, recorded findings, model responses, each round's
  edits (`edits-N.json`) and covered-file hashes.
- `computed-text-findings.json`: review findings on text the code writes, collected for a code fix.
- `budget-preflight.json`: the reservation floors priced before the first paid call.
- `state.json`, `usage.json`: checkpoints, requested/returned models, reported token usage and costs/reservations.
- `cost-summary.json`: cumulative stage/model costs, retained estimates and reported token-count coverage.

The browser offers the same files as downloads, named with the ticker and date. Edit
assumptions and recompute **offline**, at no cost:

```bash
python3 -m intrinsicvalue --revalue research-output/AAPL/full-RUN_HASH/assumptions.json
```

It prints the bear, base and bull value per share beside the saved valuation and the
change, and writes the new files to a `revalued` folder beside the assumptions file
(`--output` chooses another).

A provided draft archives the previous body and invalidates its approval. Supply the
author body only; valuation tables, source register and status are regenerated.

An offline revaluation updates model artifacts; it does not silently rewrite or
reapprove a previous memo. Generate/review a revised memo when inputs change.

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
reason; see the [issuer sample](audit-status.md#live-validation-on-2026-11-14).

The workflow synthesizes evidence, builds a source-referenced forecast plan,
computes a bear/base/bull FCFF DCF in Python and writes a memo. In the balanced profile
the plan is written three times from the same evidence, and the plan valued averages
them input by input. The first draft is built to stand on its own:

- Python extracts the filings' revenue, cost and operating income by product, segment
  and geography, five years of reported history, the latest eight fiscal quarters, the
  10-K's margin, inventory, commitment and buyback disclosures, and the latest earnings
  release with its income statement, balance sheet and cash flow statement. The models
  see them before any web excerpt.
- Python renders every table and a valuation summary at the top of the memo. The first
  page lists six inputs, from revenue growth to one flat discount rate, with what each
  would have to be alone for value to equal the price, and where the required growth
  falls in published base rates. The model writes the interpretation around them and
  does no arithmetic of its own.
- Free checks trace every figure in the prose to the computed output or a primary
  source, then one small call fixes what they find. Citations of unverified web
  excerpts are labelled in the text, and a company fact that only an unverified excerpt
  supports is removed, replaced by the filed figure or attributed to the outside party
  that said it.
- A different model reviews the memo as the reader sees it, against the same written
  quality standard the writer was given. Up to three revision rounds follow by default
  if material findings remain. A round answers the findings with exact edits that code
  applies and checks; an edit that fails gets one retry with the reason, and the memo is
  rewritten only when the forecast changed. Findings still open stay visible.

The discount rate follows the conventions the equity risk premium was solved under.
The implied premium assumes the market grows at the Treasury rate forever; a
valuation that pairs it with lower terminal growth values the market itself at about
80% of its price. When the cash flows are in US dollars and the run collected that
premium and the industry beta table, the pipeline therefore starts from the current
cost of capital, moves to market-average business risk by year 10, and sets terminal
growth to the risk-free rate unless a scenario gives a company-specific reason for
less. Present values are dated at the valuation date when the reported balance sheet
allows it, and the method notes state the free cash flow of the elapsed part of year 1,
which neither the cash flows nor the bridge include. Year-1 margins must start from the
last reported results unless the plan cites evidence for a step. Costs are shares of revenue, final-year margins have
ceilings from the reported history and the latest twelve months, the terminal return on
new capital equals the terminal cost of capital after the 10-year forecast (a steady
state, with 10 years as a house horizon), the continuing value adds back net R&D
investment, and the base case is the headline value with bear and bull as unweighted
stress cases.
The memo reports value relative to price beside a stylized S&P 500 valued under the
same conventions, the discount rate the price implies beside the required return, and
the value under alternative conventions one change at a time. Beside those rows it
values the base case at other horizons, at an industry fade, under other rate
regimes and with a revenue-weighted country risk premium. The memo also places each scenario's revenue
growth, and the growth the price requires, in published base rates for
companies of the same size. None of these exhibits changes the value or checks the plan.
The memo sets no entry prices, margins of safety or position sizes. See
[market-consistent conventions](underwriting.md#keep-the-discount-rate-consistent-with-the-equity-risk-premium).

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
cases. See the [underwriting guide](underwriting.md) for schemas,
accounting conventions, editable plans and limits. Forecasts remain house assumptions;
this FCFF model does not suit every sector or security and has no detailed refinancing,
NOL or lease-adjustment schedule.

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
Interrupted or failed requests preserve a reservation because a provider may already
have charged; a request OpenRouter refuses before any model runs (HTTP 400, 401, 402 or
403) is booked at zero. Completed stages are reused.

The cost summary separates provider-reported cost from retained estimates.
`state.json` also records full-versus-presented valuation-context UTF-8 byte sizes.
These measurements do not establish token savings, optimal routing or a quality/cost
frontier; see the [measurement limits](underwriting.md#costs-and-context-size).

As listed on 2026-11-26, Claude Haiku 5.5 cost $0.10/$0.50, Claude Opus 5.5 $4/$20 and
GPT-6.2 Sol $2/$10 per million input/output tokens ($4/$15 above 272,000 prompt
tokens); Exa Auto search cost $0.007 per request.

Measured on 2026-11-27, balanced profile: fresh runs on the edit loop, three forecasts
included, cost $3.94 to $5.47 (Walmart through three rounds $3.94, Alphabet $4.77,
Coca-Cola $5.47), and a fourth Walmart round cost $0.32. Review-only replays cost $0.41
to $2.23. Extra forecast samples cost about $0.30 each because the evidence is cached.
The first three runs that day, with one forecast and a full rewrite in revision, cost
$4.50 to $5.40. The default allowance is $20: a ceiling, because each call
reserves its prompt bytes at the model's highest price tier before it is sent, and a
GPT-6.2 Sol review alone reserves about $2.50.
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
an actionable resume instruction. Its known-context reservation floor covers the
forecast samples, the first draft and one review; each revision round is priced
beside it, with a usual-path and a worst-case floor for the whole run. Unknown future
search results, generated forecasts, valuation context, memo text and repairs can
require more. Complete actual prompts
are reserved again before each request. If primary evidence is missing, retry free
collection with a SEC contact or supply primary disclosures; dated snapshots remain frozen.
A run can stop with unresolved findings when its budget or revision limit is reached;
that status is preserved rather than treated as approval.

## Dated examples

The [revised AAPL memo dated November 6, 2026](../examples/AAPL-2026-11-06-revised/memo.md)
uses a company-specific operating bridge, explicit required-return inputs and
computed observation-to-value cases. Its [assumptions](../examples/AAPL-2026-11-06-revised/assumptions.json)
and [valuation](../examples/AAPL-2026-11-06-revised/valuation.json) are editable and
reproducible offline. Read the review findings and source limits alongside the memo;
arithmetic and model review do not establish forecast plausibility or investment certainty.
The [quality record](../examples/AAPL-2026-11-06-revised/quality-review.md) includes
fresh benchmark comparisons, conflicting automated reviews and independent
adjudications. Its earlier A / near-A+ assessment did not survive the independent
external audit. The paper was assembled through agent-directed editorial work,
not demonstrated as an output of an unassisted fresh pipeline run. The automated
attempts did not approve it. Historical files are retained for reproduction;
they do not establish benchmark parity or current pipeline readiness.
Its five additional OpenRouter requests report **$1.665431** in usage cost.

The [earlier AAPL snapshot](../examples/AAPL-2026-11-06/memo.md) retains its own
[source records](../examples/AAPL-2026-11-06/sources.json) and
[model-review record](../examples/AAPL-2026-11-06/review.json).

The cumulative AAPL run, including iterative research, corrections and failed or
formatting requests, recorded **$12.2630191** in reported charges and retained
reservations. This is an observed development-and-review run, not a promised price
for a new memo. It used Qwen for easy work, Claude for analytical work, and an
explicit GPT-5.4 final-review override. No key or account metadata is published.
The example is a dated snapshot, not a source of current quotes or an automatic
fallback for new research.

Recompute it without an API key:

```bash
python3 -m intrinsicvalue --revalue examples/AAPL-2026-11-06-revised/assumptions.json
```

## Verification and limitations

```bash
python3 -m unittest discover -s tests/public_runner -v
python3 -m pip install .
intrinsicvalue --help
```

The current source passes **__TESTS__ tests** locally on Python 3.9, 3.12 and 3.13 (five
skip unless saved runs are named for them); the v0.2.0 release passed 268. The CI matrix
tests Python 3.9, 3.10, 3.12 and 3.13 with synthetic evidence and mocked transports,
without model calls or secrets. [Live-validation guidance](openrouter_live_validation.md)
explains local budget controls and review provenance. Model review is a recorded
assessment, **not independent publication certification or a guarantee of investment
truth**. Live runs are recorded in [audit status](audit-status.md); financial facts,
forecasts and source support still require careful inspection.
The canonical input adapter recognizes a bounded set of US-GAAP/DEI concepts from
inline filings and SEC Companyfacts; unsupported taxonomies or missing bridge components require
additional primary coverage rather than a model-generated substitute.

This public edition is a standalone local research application. It does not
include the original repository’s signed publication system or private history.

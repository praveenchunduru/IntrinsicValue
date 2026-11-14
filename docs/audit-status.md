# External audit and repair status

The independent audit supplied on November 6, 2026 assessed the revised Apple memo
at B− and the process/software at C. Its reported grade supersedes this repository's
earlier A / near-A+ benchmark-parity claim. The linked
[audit](https://claude.ai/artifact/K7pjgPGRjFyGQiyxowdfg7) and supplied reproduction
scripts are the starting evidence for repairs; each defect still requires checking
against the code and actual source data.

The v0.2.0 showcase was assembled through agent-directed research and editorial
iterations. Its arithmetic checks and mocked software tests did not demonstrate
that a fresh automated run could retrieve primary inputs, finish within its
default allowance, or produce comparable research. Historical examples and exact
review records are retained unchanged for reproducibility.

## Priority repairs

1. Configure a real SEC requester contact and use it consistently across directory,
   company-facts, submissions and filing requests. Verify actual responses; a
   header change alone is not evidence that access works.
2. Replace Apple-specific cash/debt requirements with explicitly classified,
   reconciled alternatives. Preserve unknowns, accounting scope and provenance;
   do not make missing liabilities zero to improve a coverage statistic.
3. Check the full workflow's allowance before paid work and verify draft/review
   completion with realistic evidence and generated-output sizes. An allowance
   is not a promised cost or a guarantee of completion.
4. Resolve reproduced credential-redirect, excerpt-runtime, evidence-delivery and
   stale-approval defects. Keep spending reservations and review findings intact.
5. Improve the research workflow's treatment of variant views, above-price bull
   arguments, temporary margin benefits, inventory, repurchase economics,
   valuation multiples and an explicit conditional purchase price.

## Evidence required before claiming completion

- Actual SEC retrieval through the application's paths, with a configured contact.
- Primary-input results for a declared multi-issuer sample, including supported
  alternatives, contradictory disclosures and correctly rejected unsupported cases.
- Offline regression tests for the reproduced failures, with honest scope limits.
- A fresh pipeline-produced memo and its complete request ledger; no supplied
  replacement draft or prewritten forecast used to claim automated success.
- Independent critique of that generated paper against accessible benchmark papers
  and primary evidence. A grade or a passing model verdict is not an acceptance
  substitute.

## Implementation checkpoint

The current repair code adds requester identity configuration shared across SEC
paths, bounded source retrieval, accounting-scope alternatives, source-honest
Companyfacts normalization, and explicit missing/conflicting-input reports before
paid work. A contact is supplied through the local form, `--sec-contact`,
`SEC_CONTACT_EMAIL`, or a contact-bearing `SEC_USER_AGENT`; no personal email is
committed. Authenticated redirects are refused, and SEC contact details are not
forwarded to issuer hosts.

Full mode now defaults to a $10 cumulative planning allowance, while quick mode
retains $2 and explicit allowances are preserved. An insufficient known-context
allowance stops before paid requests. Offline full-workflow simulations include
large evidence and generated-output shapes, no assumed cache savings, and up to
three revisions. Those are simulated costs, not observed bills or proof of memo
quality; see [validation details](openrouter_live_validation.md).

Evidence delivery, bounded excerpt selection, changed-workflow resumption and
artifact integrity have regression coverage. Completed file hashes are checked
before reuse; changed artifacts lose their reviewed status, edited files are
preserved, and source-archive drift stops paid work. Browser status and downloads
check delivered-file bindings without invoking models or changing saved files.
Fresh independent reviewers reproduced the original integrity failures and
verified their repair using offline transports.

The repair implementation passes 389 offline tests from source and a fresh
installed wheel, with no runtime dependencies. Both historical Apple models'
main values and annual cashflows reproduce exactly; a source secret scan found
no findings. The [repair CI run](https://github.com/praveenchunduru/IntrinsicValue/actions/runs/42307996274)
tests Python 3.10, 3.12 and 3.13. This is regression coverage for tested software
behavior, not a replacement for the live evidence required above.

Research/author/review instructions now require economic engagement with sourced
opposing views, an honest explanation of any differentiated thesis, normalization
of material one-offs, inventory/procurement links, and repurchase economics.
Deterministic diagnostics supply valuation multiples and comparable exit-multiple
returns. Cash-funded repurchase cases are calculated rather than left to prose
arithmetic. Entry policies were later removed from the research pipeline; see the
valuation-method section below. These capabilities have not yet
established the quality of a newly generated memo.

## Validation still open

Live SEC retrieval and paid full-memo runs were made on 2026-11-14; see the section
below. No live run has yet produced an approved memo.

The trimmed recorded Microsoft and CoreWeave fixtures remain partial: both lack
sufficient debt scope and outstanding shares. The historical Apple showcase source
records also lack sufficient positive short-term-financing scope under the stricter
adapter. These failures are reported before paid research. They are not evidence
of complete multi-issuer support, and no replacement coverage percentage is claimed.
Retrieving and examining actual complete filings is still required.

The public Apple feed was reachable in a free probe, but its 20 entries exposed
only the latest quarterly results. Keeping up to three discovered financial
releases does not establish retrieval of prior-quarter refund evidence. The full
audit finding tables and stopping criteria have not been independently read here:
the linked artifact's body required authentication. The supplied summary and
reproduction scripts support the repairs above, not closure of every finding.

Repairs remain in progress. No new claim of A+ quality, benchmark parity or live
end-to-end success is made here. Historical examples remain unchanged. Licensing
decisions remain deferred.


## Live validation on 2026-11-14

This section records what a fresh clone did against live SEC, market and
OpenRouter services, and what was changed in response. It replaces the
"Validation still open" statement above for the items it covers.

### What failed before the repairs

- Full mode stopped at the free primary-input preflight for all eleven issuers
  tried (AAPL, MSFT, GOOGL, NVDA, AMZN, META, COST, KO, WMT, CRM, HD). No paid
  request was made.
- Quick mode failed on its first request, twice. With SEC access working, every
  prompt carried about 264 KB of per-filing SEC records and the 3,500-token
  output limit cut the answer off.
- FRED closed the connection for the calibration request, so the debt-yield
  proxy was missing from every run.
- Yahoo's trailing live-quote point made the weekly parser reject the series,
  so no historical beta was assigned.

### Issuer sample after the repairs

The same free preflight, run live through the application with a requester
contact and no model key:

| Issuer | Result | Net-debt basis or reason |
|---|---|---|
| AAPL | complete | commercial paper plus long-term debt; identified-category assumption |
| MSFT | complete | long-term debt; identified-category assumption |
| GOOGL | complete | gross principal reconciled to carrying debt; identified-category assumption |
| NVDA | complete | reported current debt equals current long-term debt (no short-term financing assumption) |
| COST | complete | current plus noncurrent long-term debt; identified-category assumption |
| KO | complete | commercial paper, other short-term borrowings and combined long-term debt; identified-category assumption |
| WMT | complete | reported short-term borrowings plus long-term debt (no short-term financing assumption); total revenues selected |
| CRM | complete | long-term debt; identified-category assumption |
| AMZN | refused | long-term debt total differs from its parts and no reported amount reconciles them |
| HD | refused | long-term debt excluding leases exceeds the combined family less reported leases |
| META | refused | outstanding shares reported only by class |
| JPM | refused | no supported cash selection; a bank does not suit this FCFF model |
| XOM | refused | cover-page and balance-sheet share counts differ; annual revenue concept unsupported |

JPM and XOM were added after the rules were written, as a check outside the
issuers the rules were developed on. Eight of thirteen is a sample result. It
is not a coverage rate for other issuers, sectors or filing years.

Six of the eight completions rest on the labelled short-term financing
assumption described in the [underwriting guide](underwriting.md). It and the
finance-lease assumption are the two bounded places where an absent tag is treated
as zero. For short-term financing the run must have fetched the whole SEC entity
record, that record must show no other short-term borrowing amount on the
balance-sheet date, and any reported total or contradicting current-debt amount
overrides it. A finance-lease liability is assumed zero only when no lease concept
is reported. Each field so pinned is reported as `pinned_with_disclosed_assumption`,
and the memo states it. A reader who needs a reported basis for every input should
check each field's status in `reported-inputs.json` and treat assumption-based
completions as provisional.

### First paid full-memo run (AAPL, balanced, $10 allowance)

The run used Qwen for research and Claude Sonnet 4.6 for the plan, draft, review and
corrections. It made 26 requests, reported $7.35 in provider cost and retained $8.86
in charges and reservations. It ended without an approved memo:

- The forecast plan took three attempts and seven requests. Six responses failed
  validation, for reasons including a currency code written as the operating model's
  unit, undocumented label fields and counterfactuals written as a list. The first
  attempt made one repair and the second three; neither cleared the independent
  defects, and the third attempt succeeded.
- The first draft request timed out at the fixed 180-second wait while generating
  and kept its reservation.
- Both reviews needed a format repair. Each recorded twelve findings, and the
  reviewer was the author model.
- The second revision reached its 14,000-token output limit and was discarded.

The computed weighted value was $155.31 against a $356.27 quote.

### Valuation method

The $155 result was examined by nine independent critics, a four-agent red team and
a reproduction of Damodaran's November 1, 2026 implied equity risk premium (4.41%,
reproduced to 4.4148% from his published spreadsheet). The premium is solved with
index earnings growing at the Treasury rate forever, and the plan paired it with
terminal growth near 3%. Under that pairing the index itself
values at about 80% of its price. Constant firm-level discount rates and a terminal
return equal to the cost of capital for every firm lower it further.

The engine and pipeline now apply the conventions the premium assumes, described in
the [underwriting guide](underwriting.md#keep-the-discount-rate-consistent-with-the-equity-risk-premium).
The memo reports value relative to price, the discount rate the price implies beside
the required return, and a fixed panel of alternative conventions. The audit asked for
an explicit conditional purchase price; that request is declined. This project values
companies and sets no entry prices, margins of safety or position sizes.

Offline, the first run's repaired plan under the new conventions, with its 17% debt
tax shield raised to the 21% statutory rate the pipeline now requires, gives a
weighted $216.21 with the regression beta and $208.87 with a bottom-up beta (74.25%
Computers/Peripherals, 25.75% Software), or 0.61 and 0.59 of the quote. The price
implies a 7.45% discount rate against a 9.15-9.31% required return. The largest
single convention step is the terminal return on new capital set equal to the
terminal rate, which takes the base value to $146.34. These values are not tuned to
the price.

Two independent code reviews of the change, each with adversarial verification,
confirmed 26 and then 41 findings. All are fixed except one documented limit: the
rate has no debt beta, so no credit spread rises with leverage. The suite has 512
offline tests. Saved plans without the new fields reproduce their earlier values
exactly, including both shipped Apple examples.

### Second paid run (AAPL, balanced, $10 allowance)

Run on commit 73c46ce, it made 9 requests and reported $1.59, all in research and the
forecast-plan stage. It stopped after three plan repairs with "Invalid structured
JSON". Two repairs were valid plans preceded by a paragraph of analysis, which the
plan stage's parser rejected; the review-correction stage already accepted one
complete fenced JSON block. The plan stage now does the same (commit 912989a).

The repaired plans also set Apple's R&D at $68-96 billion and SG&A at $43-55 billion
against $45.8 billion and $29.8 billion reported, putting year-1 EBIT margin at 20-27%
against a reported 33.0%. Nothing in front of the forecast author or the validator
carried the reported expense levels. The pipeline now gives the author the reported
operating lines and checks year-1 continuity (commit 58cf7bd and the second review's
fixes), described in the
[underwriting guide](underwriting.md#start-year-1-from-the-reported-results). The
first run's plan passes that check, within 2.4 points of the reported margin: its
$155 came from the rate and terminal conventions.

### Third paid run (AAPL, balanced, $10 allowance)

Run on commit da02cc6, it completed the whole workflow: research, plan, draft, four
reviews and three revisions (the default limit), in 26 requests with $6.79 reported.
It ended with unresolved findings, so no memo was approved.

- The plan took three requests. The first plan's stated EBIT margins disagreed with
  its own drivers in later years. The first repair wrote Apple's separately reported
  $1.456 billion of finance leases as a zero allocation inside debt. The second
  repair passed. Python now copies debt, finance leases and market equity from the
  reported bridges (commit 2f1145b).
- Year-1 EBIT margins were 31.4%, 32.6% and 33.9% (bear, base, bull) against the
  reported 33.0%.
- The bottom-up beta of 1.30 (74% Computers/Peripherals at 1.32 and 26% Software at
  1.25 in Damodaran's January table) gives a 10.65% initial cost of capital, moving
  to 8.88% by year 10.
- The weighted value is $209.08 against $356.27, or 0.59 of the price. The price
  implies a 7.46% discount rate against a 9.30% required return. Setting the terminal
  return on new capital equal to the terminal rate lowers the base value by $59, the
  largest convention step. With base margins and capital efficiency fixed, the price
  needs about 12.9% annual revenue growth for ten years.
- GPT-5.4 reviewed each draft, and material findings went 11, 10, 9 and 10. They
  concerned the memo's evidence and argument: central claims sourced to unverified
  search excerpts, convention deltas added together although they are computed one at
  a time, and an opposing case left unanswered. The author's corrections kept the
  forecast unchanged each time.
- Every review paid a format repair for its quotations; the first response is now
  anchored the same way (commit 66a539e). The memo body ran to about 5,600 words
  against a 4,500-word budget.

The three paid runs reported $15.73 in total.

### First-shot changes and replays

Run 3's review rounds cost $4.99 of its $6.79 and did not converge. An investigation of
its first draft traced most findings to the inputs the writing model received:
about 16 KB of the 232 KB 10-K reached the models, with no category, segment or margin
tables, so the writer used 27 unverified web excerpts for company facts; the writer and
reviewer applied different standards to those excerpts; and the writer did its own
arithmetic (a 60.7% products margin against 39.3% reported, a $290.88 buyback price
called "below" a $209.08 value). The changes described in the
[underwriting guide](underwriting.md#how-the-first-draft-is-produced) followed.

`tools/replay.py` re-ran the later stages on run 3's frozen evidence:

| Replay | Paid stages | Cost | Review of the first draft |
|---|---|---:|---|
| Run 3 as run (old pipeline) | plan, draft, review | $1.80 to the first review | 11 major, 3 minor; central weakness and gap "partly" |
| 1 | plan, draft, fix call, review | $2.90 (incl. $1.01 re-paid after a code change) | 3 major, 3 minor; both "yes"; all majors were unlabelled unverified material |
| 2 | fix call, review | $0.40 | 3 major, 4 minor; both "yes" |
| 3 | fix call, review (inline labels) | $0.40 | 2 major, 5 minor; both "partly"; no labelling or figure findings remained |

The plan passed on its first response in every replay. With cached evidence a GPT-5.4
review cost $0.07. The remaining major findings concern the argument (naming every
computed driver of the gap, answering the opposing case with operating evidence); the
memo sections now ask for both. The reviewer's verdict varied between replays of the
same draft, so one review verdict carries noise.

A four-reviewer Opus code review of these changes then reproduced 25 defects, 11 major,
including mixed pre- and post-split per-share history (NVDA), an average buyback price a
third too high (MSFT), a segment table dropped when two revenue concepts were tagged
(WMT), and completed runs stranded by a same-day code update under the new default of
one revision. All are fixed except product tables tagged on combined product and segment
axes (GOOGL, META, WMT), which are not yet extracted. The suite has 512 offline tests.

### Still open after 2026-11-14

- No live run has produced an approved memo. Review findings on evidence quality did
  not converge within three revisions.
- The memo overruns its word budget.
- AMZN, HD, META, JPM and XOM are refused at preflight.
- Whether 0.59 of the price is a credible value for Apple rests on the discount rate
  and terminal returns, which the memo's convention panel and beta cross-checks show
  one change at a time. No value is tuned to the price.

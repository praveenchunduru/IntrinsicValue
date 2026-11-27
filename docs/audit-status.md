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

### Final live run (AAPL, balanced, $10 allowance)

One folder, resumed after each fix, $7.38 charged in total against the $10 allowance:

1. The plan call spent its whole 18,000-token limit on extended thinking and returned
   no text ($0.58). The plan stage no longer thinks; a thinking stage that ends that
   way is retried once without it.
2. The first draft drew 3 major findings and none minor; both the central weakness
   and the gap to price were judged handled. All three traced to code or prompts: a
   multiples footnote that said "integer model years" under stub timing, shift rows
   that showed year 1 only (the writer added the shift by hand), and no rule for
   attributing model outputs. Each was fixed.
3. The revision grew the memo to 4,284 words. The free length check sent a 1,300-word
   cut to the fix call, which overran its limit while deleting required conclusion
   rows, and the failure stopped the run ($0.44). Length is now left to the writing
   stages, an incomplete fix response no longer stops a run, and the revise prompt
   carries the shared standard and the current word count as a ceiling.
4. On the final code (a code change discards the plan and draft and keeps the
   evidence) the full cycle cost $3.54: plan, draft, fix call, review, targeted
   research, forecast response, revision, fix call, review. The first draft drew 4
   major findings and 1 minor (run 3 as run: 11 and 3), with the central weakness and
   the gap judged "partly" handled; the revision left 3 major and 3 minor, with both
   judged handled, at 3,731 words. The run ended
   `unresolved_findings`.

The weighted value was $237.99, $237.11 and $237.23 per share (0.67 of the price) in
the three plans written from this evidence. The last review's majors were a house tax
rate called unattributed although the sentence names it as a house assumption, and
plan-written monitoring and repurchase rows that cite unverified excerpts. The plan
and correction prompts now carry the evidence-tier rule; this change has not been
run live. The suite has 520 offline tests.

### Phase 1: removing the remaining failure classes (2026-11-15)

The target: on a benchmark of eight companies, the first draft's review has no blocker
and no major finding that survives an independent check, reruns on the same evidence
move the value by less than 2%, and a memo costs about $4 or less. No value is tuned to
the price.

Changes: the reported history adds operating income and EPS growth, shares
outstanding, the change in shares outstanding and shares repurchased; the rubric's rules
carry IDs and a finding is graded no higher than its rule; every reviewer blocker or
major is checked once more in a fresh context, and Python refutes "number in no source"
findings whose figures it can trace; Python states which inputs alone can and cannot
bring value to the price; historical buyback cases use the reported program figures;
the source register labels unverified sources. An Opus review of the first version
reproduced 8 defects, the worst letting a refutation of the assessment finding remove a
reviewer major with the same ID and pass the draft; all are fixed.

Replays from the plan stage on the final live run's evidence, first draft only:

| Replay | Code | Cost | Review of the first draft after the check |
|---|---|---:|---|
| 1 | finding check, rule IDs, share columns | $2.08 | 5 major, 3 minor; gap to price "no". Two majors were the conclusion and variant view naming different causes of the gap |
| 2 | + Python gap attribution, house-assumption labels | $2.08 | 1 major, 1 minor; weakness and gap "yes". The check refuted one major; the other called a figure from the history table absent |

Run offline on replay 2's saved review, the later Python fact-check refutes that last
major, leaving one minor finding: replay 2's first draft would have passed. That has not
been observed in a paid run. Weighted values were $232.71 and $242.27 (0.65 and 0.68 of
the price), 4% apart; the earlier three plans on this evidence were within 0.4%. The
stability target of 2% is not met: the plan author's forecasts vary between runs. The
suite has 547 offline tests.

### Microsoft (2026-11-15)

A live run, first draft only, cost $1.99 (one plan repair). The check refuted one
reviewer misreading and upheld one real error, a Microsoft Cloud gross margin set beside
the Intelligent Cloud segment's as one measure; 1 major, 1 minor. The review missed the
largest problem: year-1 reinvestment of $43.6 billion against $81.6 billion reported
(capital expenditure $115.9 billion less depreciation $34.3 billion), at sales-to-capital
1.55 against a reported three-year incremental rate of 0.78. A reinvestment continuity
check now sends such a plan back unless a cited basis explains the fall.

A replay from the plan stage on the same evidence cost $2.52 (two plan repairs). The plan
raised year-1 reinvestment to $53-66 billion (sales-to-capital 0.90-1.20) and stated no
basis. The base value moved from $760.16 to $751.11 and the weighted value from $778.94
to $790.32 (1.49 and 1.51 times the $523.88 price); the bear and bull cases moved more.
The value rests mostly on the growth and margin path and the 25% terminal return on new
capital (base value $510.13 at the terminal discount rate). The first draft drew 3
surviving majors: an RPO "further increase" from $678 to $677 billion, an unsupported
statement about Azure reporting, and a variant view the reviewer read as a second
attribution of the gap because the review prompt said no section may attribute it
otherwise. That wording now says no section may contradict the computed account. The
check refuted two false "unlabelled" findings.

Across the five first drafts on the final code (Apple live, Apple replays 1 and 2,
Microsoft live and replay), surviving majors were 4, 5, 1, 1 and 3. Most of the
remaining ones are single factual slips by the writer that a revision usually corrects.
The suite has 552 offline tests.

### Writer comparison on Microsoft (2026-11-15)

Three replays ran in parallel from the same saved Microsoft plan and evidence, so only
the writing model and the revision differed ($6.17 in total). OpenRouter now lists
Claude Sonnet 5.5 at $2/$10 per million tokens and Opus 5.5 at $4/$20, against $3/$15
for the configured Sonnet 4.6.

| Arm | Cost | First draft, surviving findings | After one revision | Prose words |
|---|---:|---|---|---:|
| Sonnet 4.6, one revision | $2.68 | 1 blocker ("accretive" read as trading language), 1 major (gap attribution); gap "partly" | 2 majors, both gap attribution; weakness and gap "partly" | 3,733 |
| Opus 5.5, first draft | $1.96 | 1 major (a segment margin written as "not shown"), 1 Python false positive; weakness and gap "yes" | not run | 2,865 |
| Sonnet 5.5, first draft | $1.53 | 3 majors, all gap attribution; "partly" | not run | 2,755 |

The Python quote-date check read "the valuation is dated 2026-11-15" as a quote date when
the draft had kept the two apart; it now reads clause by clause. The shared standard now
says naming the most sensitive convention step is not an attribution of the gap. The gap
attribution findings (rule M4) recur in every Sonnet draft and in the revision, and the
revision did not resolve them. One draft per arm is a small sample.

### One change set from a full investigation (2026-11-15)

Instead of more paid tests, five agents read every saved run (8 memos, 11 reviews, the
plans and ledgers) and swept 13 companies' SEC data, with no OpenRouter calls. Of 40
material findings, 14 concerned how the memo explains the gap to price and were mostly
induced by the prompts; most of the rest were writer slips, evidence-tier labelling and
re-typed tables. An independent memo audit found what no review had flagged: plans
forecast expenses in dollars and let them fall to half their share of revenue, so
margins rose in every scenario, bear included. The valuation audit found same-evidence
reruns agreeing within 4% only because 6-17% movements in individual inputs cancelled.
No stage reused a provider cache.

The change set, all offline and tested:

- Python states which inputs alone can and cannot bring value to the price; the
  conclusion does not restate it; rule M4 is only a contradiction of it; the reviewer's
  central-weakness and gap answers are recorded, not findings.
- House conventions by owner choice: terminal return on new capital 15%; weights
  25/50/25 unless cited; costs grow at about the reported ratio to revenue (capped at
  one for one) and final margins stay within 5 points of the reported peak unless
  cited; finance-lease additions count as reinvestment.
- Python refutes B2, M3 and M6 findings it can settle and findings refuted earlier on
  the same text; the draft is fenced in the review prompt.
- Python prints the reported history and segment tables and a scale check; free checks
  catch table rows, direction words, uncited frequencies and mentions of Python.
- Monitoring rows state the direction of their linked case.
- The evidence is a cached prefix for every Claude stage, and the review and finding
  check share one prefix (estimated $3 saving on a run with one revision); identical
  plan repairs stop; a dev profile runs the flow cheaply.
- A bug in summary removal could delete a memo written with ### sections; fixed.

- Data extraction (13-company free sweep): acquisitions, D&A, SG&A, buyback prices and
  share counts filled from the 10-K's inline facts where company facts lack them;
  product tables on combined product and segment axes; every ticker's 8-K earnings
  release as an issuer primary source; stale 10-Qs skipped; split false positives
  removed; finance-lease additions and leases signed but not commenced recorded;
  long-term debt read at principal within 0.1-3% of carrying value; marketable
  securities under available-for-sale tags; class share counts summed; banks and
  insurers refused before any paid stage. Preflight now passes for 11 of 13 companies
  (Amazon, Home Depot and Meta added); JPMorgan is refused as a bank and ExxonMobil's
  successor registrant has no annual report yet.

Two Opus reviews of the change set reproduced 12 defects, among them a carried-forward
refutation that could pass a draft, a cost-path check comparing different cost totals,
evidence cached where the thinking draft could not read it, a $10 allowance too small
for a revision cycle's reservations, buyback prices on share counts rounded to
millions, and a bank gate that refused money transmitters; all are fixed. The default
allowance is now $20 (a ceiling; expected spend for a run with one revision is about
$4.50).

None of this has been run against a paid model. The suite has 613 offline tests.

### Valuation conventions from an independent review (2026-11-26)

Claude Fable 5.2, at extra-high reasoning effort, reviewed four open conventions against
Damodaran's practice and the saved Apple and Microsoft plans. By owner choice its
package is now the house rule, with Damodaran's levels shown as panel rows:

- Terminal return on new capital: 15%, lowered to the median reported return on
  invested capital over five years where that is lower, never below the terminal
  discount rate. The panel adds the terminal rate plus 4 points.
- Costs: plans give R&D and SG&A as shares of revenue and Python builds the dollars;
  a line left out holds its reported share.
- Final-year EBIT margin ceilings with no citation exception: bear at most the
  five-year average, base and bull at most the five-year peak plus 5 points (less for
  low-margin businesses). The panel adds the base margin held at the peak.
- Headline: the base case. Bear and bull are stress cases without probabilities; no
  Monte Carlo.
- Reinvestment: the check covers forecast years 1-3 and either a low total or a high
  sales-to-capital sends the plan back unless a cited basis explains it. Finance-lease
  additions stay in reinvestment; leases signed but not commenced are disclosed and not
  deducted.

On the saved plans, before they were lost, the package moved the base value from about
$204 to $196 for Apple and from $578 to $560 for Microsoft (offline estimates against
prices of about $356 and $524). On real SEC data the ceilings are 44.2% (bear) and
51.8% for Microsoft, 31.3% and 38.0% for Apple, 3.6% and 5.5% for Costco, and 25.3% and
33.7% for Coca-Cola. Microsoft's reported net reinvestment is $106 billion a year at an
incremental sales-to-capital of 0.57, so a Microsoft plan growing revenue in the low
teens must hold sales-to-capital near 0.7 over years 1-3 or cite evidence that capital
spending will slow.

An independent Opus review of both commits reproduced six defects, all fixed: the
terminal-rate floor was undone whenever a locked plan was locked again, a median return
below the risk-free rate failed validation before the floor could run, an unbounded
median could not be saved, constant-rate plans were never floored, a company with no
reported R&D could not use the default shares, and separate selling and administrative
shares were counted twice. The exhibit no longer calls the headline a weighted value.

Not run against a paid model. The suite has 632 offline tests.

### Return on invested capital to Mauboussin's method (2026-11-26)

By owner request the ROIC analysis follows Mauboussin and Callahan's October 2022
Counterpoint Global paper. Four readers went through his papers on calculating ROIC,
intangibles and ROIC, ROIC in the investment process, market-expected return on
investment, base rates and fade, and measuring the moat. Morgan Stanley's and Eaton
Vance's sites refused automated fetches, so some came from archived or mirrored copies;
the 2025 capital-allocation and 2023 stock-compensation papers were not read.

The earlier ROIC (operating income after effective tax over beginning equity plus debt
less cash) gave Apple capital below zero, so its return counted as unbounded. The
operating approach gives Apple about $59 billion of capital in fiscal 2022 and an ROIC
of 173% (paper: 162%). On Microsoft's filings as of 2022 the method reproduces the
paper's example within a point or two (see underwriting.md). Across 16 companies the
latest ROIC runs from 6% (AT&T) to 140% (Nvidia); Walmart 14%, Home Depot 22%, Coca-Cola
23%, Costco 28%, Alphabet 42%, Microsoft 33%, Apple 129%. The house terminal-return
limit now reads this ROIC. Data fixes from the sweep: commercial paper tagged inside
short-term borrowings was counted twice (Microsoft), and Microsoft's fiscal 2017
short-term investments use a concept retired in 2018.

An independent Opus review checked the code against the paper's text and found the
arithmetic, lags and vintages right, and seven tagging and labelling defects, all
fixed: Nvidia's securities (ROIC 98.8% to 139.8%), Meta's and Amazon's equity
investments, Coca-Cola's trademarks (ROIC without goodwill 36.5% to 56.5%), Home
Depot's and Coca-Cola's current debt, Amazon's unreported R&D taken as zero, an
undisclosed omission of unusual-item adjustments, a median ROIC note cited to the
wrong paper, and a history field the new tax rate overwrote.

The suite has 652 offline tests.

### Mauboussin's steady state for the terminal return (2026-11-26)

By owner choice the terminal return on new capital now equals the terminal discount
rate in every scenario, replacing the 15% limit. The 10-year forecast is the
competitive advantage period, and the continuing value is year-11 NOPAT over the
terminal rate, as in Mauboussin's worked templates. His 2014 paper cites research putting market-implied
advantage periods at about 5 years for competitive industries and about 15 for stable
ones, and about 8 years on average for 1976-2007; the house uses the plan's 10 years
for every company. On earlier offline estimates the change lowers base values by
roughly a tenth to a seventh (Apple about $196 to $168, Microsoft $560 to $499); no
plan has yet been valued under it. The implied equity risk premium behind the discount
rate assumes the market earns more than its cost of capital on new investment, so
this convention is the more conservative pairing; the convention panel shows the
earlier rule and 15% beside it.

An independent Opus review of the change found that the pin could fall back to 15% when
the rate calculation failed for a reason the lock removed afterwards, and that every
alternative that moves the discount rate (rate shifts, flat-rate counterfactuals,
calibration comparisons, the implied return) kept the old terminal return and so added
or removed an excess return. The pin now runs last and raises the plan's own error, and
scenarios carry `terminal_roic_basis: "terminal_rate"` so the engine applies the
terminal rate of whatever rate path it values; rows that set a terminal return clear it.
A correction of a supplied plan is told to keep its own terminal return.

The suite has 655 offline tests.

### Three-lens critique and change set (2026-11-26)

Claude Fable 5.2, at extra-high reasoning effort, critiqued the branch at commit f87ae33
three times, from the perspectives of Aswath Damodaran, Michael Mauboussin and Stanley
Druckenmiller, with experiments on cached SEC facts and the shipped Apple plan and no
paid model calls. The main findings:

- Value / price was read against 1.00, although the house conventions value the index
  itself at about 0.65 of its price, so every company started about a third below the
  market before anything company-specific was said.
- The equity bridge stopped at debt, cash and finance leases, and value per share used
  basic shares. Non-marketable equity at Amazon and Alphabet, Coca-Cola's equity-method
  stakes, AT&T's noncontrolling interests and postemployment obligations and Oracle's
  preferred stock were all left out.
- R&D was expensed in the margin and charged again through terminal reinvestment, and
  the continuing value grew final-year NOPAT once more before capitalizing it, so
  terminal growth still added value.
- The docs and prompts called the 10 years Mauboussin's competitive advantage period for
  every company, and the forecast had no ROIC line, no base rates and one sales-to-capital
  ratio for ten years.
- The rate regime moved Apple's value more than its whole bear case: the same cash flows
  were worth 73% more at the end-2021 rate pair. The memo showed only a one-point shift,
  and its implied-return sentence vanished when no flat rate reached the price.
- Margin ceilings refused an inflection already in reported quarters (Nvidia in 2023)
  and left Uber with no passable bear case. The reinvestment anchor skipped Apple and
  Uber and put Nvidia's reference at 9.6, against 2.2 on the change in invested capital.

Owner decisions the same day, recorded in the
[underwriting guide](underwriting.md#house-conventions-the-plan-does-not-choose): the
10-year steady state stays the house rule, with horizons of 5, 15 and 20 years and
Mauboussin's industry fade as panel rows; the continuing value capitalizes year-10 NOPAT
with no extra growth step and adds back net R&D investment (R&D only, six-year life, from
the forecast's own R&D); Damodaran's revenue-weighted country risk premium is a panel row
only, because the S&P 500 implied premium already reflects US companies' foreign revenue;
base rates appear as printed percentiles with no gate; and the balanced reviewer is
GPT-6.2 Sol, the newest OpenAI generation in the catalog. This replaces two statements in
the steady-state section above: the 10 years are a house horizon, and the continuing
value is year-10 NOPAT over the terminal rate.

What changed, measured offline on the merged code. Apple figures use the shipped revised
example (a constant 10.02% rate, terminal returns of 30-70%) and, for the exhibits, the
critique's restatement of it under the current conventions (rate glide, terminal return
equal to the terminal rate, base headline, the 74/26 bottom-up beta), with R&D shares set
as the lock sets them:

- **Continuing value and R&D.** Capitalizing final-year NOPAT, with no further growth
  step, multiplies the continuing value by 1/(1+g): the shipped example's base value goes
  from $162.07 to $159.53. Adding back $8.9 billion of net R&D investment to $239.1 billion of final-year
  NOPAT takes it to $162.67. With the terminal return equal to the rate, the base value
  is $140.30 at terminal growth of 0%, 2%, 3% and 5% alike. Sales-to-capital can be a
  path, and each scenario's forecast ROIC and incremental ROIC are printed from the last
  reported invested capital.
- **Equity bridge.** Non-operating investments, noncontrolling interests, preferred stock,
  pensions before tax and the dilutive increment enter the bridge, and the memo prints it
  line by line. Across the 16 sample companies dilution runs from 0.1% of outstanding
  shares (Costco) to 3.8% (Procter & Gamble), other claims reach $28.5 billion (AT&T) and
  non-operating investments $68.7 billion (Alphabet). Apple adds nothing outside its cash
  and 58.4 million dilutive shares, which lower every value 0.4%. Some of the critique's
  amounts do not reproduce at the annual balance-sheet date, probably because they came
  from later quarter ends: Amazon's non-marketable equity is $16.2 billion there against
  the critique's $143.7 billion.
- **Preflight.** Johnson & Johnson's short-term borrowings line, Procter & Gamble's
  rounding and restricted-cash tagging, and Uber's mixed principal and carrying
  observations are now read from reported amounts. The free preflight, run live against
  SEC on the bridge commit (561ed2d), passes for all 16 sample companies (AAPL, MSFT,
  GOOGL, NVDA, AMZN, META, COST, KO, WMT, CRM, HD, JNJ, PG, UBER, ORCL, T), and net debt
  is unchanged for the 13 that already passed.
- **Exhibits.** On the restatement (base $147.53 against $148.42 before, 0.43 of the
  $344.17 price), horizons of 5, 15 and 20 years give −11.6%, +9.1% and +15.7%, and the
  industry fade (Technology Hardware 0.17 and Software 0.22, 16 years in all) +10.6%. The
  Treasury rate 2 and 1 points lower and 1 and 2 points higher gives +32.1%, +14.0%,
  −11.1% and −20.0%. Over the 120 monthly rate pairs ending November 2026 the base value
  runs from $145.27 (October 2026) to $271.01 (August 2021), with a median of $182.09.
  The index benchmark prints 0.78 beside the company's 0.43. With Damodaran's own November
  inputs the same structure gives about 0.69, so the stylized benchmark runs about 0.09
  high, and the memo calls it a steady state.
- **Implied return.** Where the terminal return follows the rate, the search starts at
  0.5%. The restatement's base case now solves at 4.5% against a 9.6% flat-equivalent
  required return; before the change set its base and bear rows printed "not solved".
- **Country risk row.** Weighted premiums over the US run from 0.09 points (AT&T) to 0.95
  (Coca-Cola), with all 16 companies' revenue mapped. Apple's 0.75 points raise its
  discount rates by 0.74 points and lower the shipped example's base value 9.7%.
- **Checks.** With the latest twelve months, base ceilings rise for Microsoft, Coca-Cola,
  Alphabet, Amazon, Nvidia and Oracle (Amazon 15.6% to 18.1%, Nvidia 67.4% to 70.2%),
  and Uber has no bear ceiling and a base ceiling of 17.4%. Nvidia's base ceiling stayed
  at 42.3% on filings to June and September 2023 and rose to 50.9% in December, once the
  inflection was in reported quarters. In a backtest at 120, 210 and 300 days after each
  fiscal year end, later margins exceeded the base ceiling within three years in 60 of
  420 company-dates, against 66 before. The invested-capital rate sets the reinvestment
  reference for 14 of the 16 companies (Nvidia 9.56 to 2.23, AT&T 2.03 to 0.50), Apple
  and Uber are now checked, and the revised Apple example's sales-to-capital of 3.0
  would go back for repair in every scenario.
- **ROIC table.** One-line SG&A now gets the intangibles-capitalized ROIC (Nvidia 105.6%,
  Johnson & Johnson 18.5%, Procter & Gamble 14.1%), small capital changes show n/m, and a
  tax note flags eight companies (Microsoft's latest ROIC is 33.1%, or 28.6% at its
  three-year median tax rate). R&D is the larger of the inclusive concept and its part in
  the history, the ROIC table and the reported operating lines, so Johnson & Johnson's
  R&D reads 15.6% of revenue in place of 0.1%. A J&J plan that omitted its R&D share would
  otherwise have held about 15 points of extra margin.
- **Quarters and base rates.** The evidence and the memo carry the latest eight fiscal
  quarters. Apple's base case grows 5.5% a year over 10 years, the 67th percentile for
  companies with more than $50 billion of sales; the growth the price requires, 10.0
  points more a year, is at the 98th percentile on the shipped example and above the 99th
  on the restatement.

Not implemented:

- A synthetic rating from interest coverage for the debt cost, a failure-probability row
  and a loss carryforward balance (Damodaran). The debt cost stays the plan's estimate,
  with the AA index yield as the only collected proxy; losses earn no tax credit; the
  terminal rate still falls as leverage rises. These limits weigh most on young, levered
  and loss-making companies.
- Regime stress cases built from each company's history, and rate shifts in bear and
  bull (Druckenmiller). The scenarios share one rate path under the house rules; the
  rate-regime rows value the base cash flows at other rates instead.
- A Monte Carlo exhibit (Damodaran): closed by the owner's earlier decision that day that
  the base case is the headline and bear and bull are unweighted stress cases.
- The industry fade or a company-specific horizon as the house rule (Mauboussin): the
  owner kept the 10-year steady state and chose panel rows.
- Also left out: a cited basis when forecast-end ROIC exceeds a persistence-implied
  level, clipping margins in Python with a row at the plan's own margin path, ranking the
  rate rows with the convention rows, a market-implied horizon row, FRED real-yield and
  breakeven series, and next-fiscal-year consensus.

No paid run has used this code yet. The suite has 814 offline tests.

### Still open after 2026-11-14

- No live run has produced an approved memo. The final run's first draft drew 4 major
  findings and its revision 3, against 11 before the first-shot changes.
- The plan-prompt evidence-tier rule and the rubric clarification have not been run live.
- No paid run has yet shown a first draft with no surviving major finding; replay 2 does
  so only when the later fact-check is applied offline.
- Plan forecasts vary by up to 4% in value between runs on the same evidence; the
  2026-11-26 review traced most of Apple's spread to revenue growth, which the printed
  base rates now place against history and nothing checks.
- The terminal return on new capital was the plan author's judgment (25% for both Apple
  and Microsoft) and the largest single sensitivity in both memos; since 2026-11-26 it
  is a house convention.
- The reinvestment check has run live once, before its reference became the lower of
  two rates; the value it produced for Microsoft rests on judgments the review accepted.
- Gap-attribution findings (M4) recur in every Sonnet draft and did not converge in a
  revision.
- Defaults moved to the newest generation in every slot (2026-11-15, and the balanced
  reviewer again on 2026-11-26): balanced uses Claude Haiku 5.5, Claude Opus 5.5 and
  GPT-6.2 Sol; economy uses DeepSeek V4.1 Flash, DeepSeek V4 Pro and Kimi K3.1 Pro. Only
  Opus 5.5 as writer has been run; GPT-6.2 Sol as reviewer and the whole economy profile
  have not.
- The writer still occasionally does arithmetic the computed tables do not supply (a
  net share reduction stated a thousand times too small, then corrected to a wrong
  figure). The reported-history table carries no net share change.
- One review verdict carries noise: the same draft drew different majors in replays.
- The 16 sample companies pass the free preflight. JPMorgan is refused as a bank, which
  this model does not suit, and ExxonMobil's successor registrant had no annual report at
  the 2026-11-15 sweep and has not been tried since.
- Whether Apple's value / price is credible (0.67 in the 2026-11-14 runs, 0.43 for the
  offline restatement under the current conventions) rests on the discount rate and
  terminal returns, which the memo's convention panel and beta cross-checks show one
  change at a time, beside the stylized index's 0.78. No value is tuned to the price.
- No paid run has used the 2026-11-26 change set, and the country risk row has not run
  in a live pipeline.
- Equity bridge: Nvidia's $12.9 billion of marketable equity is added in the bridge but
  stays in ROIC's invested capital. Lines left out under the nesting rule or a possible
  overlap with cash are disclosed in the bridge (largest: Meta's $7.4 billion of
  equity-method investments). Alphabet's $3.4 billion noncontrolling interest and Oracle's $24.1 billion
  `Investments` are not tagged as balance-sheet lines and are not used. Unvested awards
  beyond the diluted count are not valued.
- The year-to-date fallback for the margin ceilings has run only on synthetic data, and
  Johnson & Johnson, with no operating income line, has no ceilings or year-1 margin
  check.
- Country risk labels are matched by name, and GDP weights the countries inside a region.
- Offline `--revalue` output has no base rates, and the terminal-growth requirement has
  no percentile.
- Under the glide a plan shorter than 10 years keeps a small timing effect from terminal
  growth, and its steady-state years keep R&D expensed.
- The index benchmark is a steady state that runs about 0.09 above the same structure on
  Damodaran's own inputs.

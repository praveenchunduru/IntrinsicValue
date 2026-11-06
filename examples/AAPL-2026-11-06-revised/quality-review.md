# Revised Apple memo: quality and verification record

The revised paper reaches the substantive research standard of the actual DeepValue FIG and MU papers reviewed for comparison. A fresh investment-committee reviewer closed all four substantive findings and assessed **A / near-A+, with core benchmark parity**. A separate fresh source/statistics reviewer returned pass with no unresolved findings. This is a documented comparison, not an unqualified perfection score or certification of an investment edge.

## What improved

The paper now reconciles five revenue categories, Products/Services gross profit and explicit R&D/SG&A into EBIT. It separates the FY2026 iPhone-led acceleration from prospective Services mix, dates the Duo launch after fiscal year-end, and uses current management compensation rather than treating an older proxy as current.

The outside challenge comes from dated Morningstar, Phillip Securities and Wedgewood work, with access and incentive limits disclosed. Near-term forecasts are compared with identifiable Street panels without double-counting shared providers. The paper acknowledges that its operating forecast is close to consensus and that required returns drive much of its disagreement with price.

The rate argument includes a matched Treasury/ERP pair, issuer-history beta, static financing/cash transformation, alternative windows and sector mappings, and an explicit economic adoption judgment. A fixed-exit cash-return calculation is distinguished from the quote-equating WACC. Historical cash generation, stock compensation, tax paid versus tax provision, terminal reinvestment and capital-efficiency assumptions are exposed and stressed.

The decision at the frozen quote is explicit. The existing reverse DCF's 16.52% constant aggregate growth requirement is interpreted alongside the alternative lower-rate explanation. Monitoring conditions name economic mechanisms, changed forecasts and computed values; a one-year observation does not automatically establish the eight-year Services persistence case.

## Adversarial iterations

Reviewers assessed frozen files and were asked to report actual defects rather than fill a finding quota. New reviewers in the second round had not authored the paper or read earlier reviews.

| Round | Independent criticism | Result |
|---|---|---|
| 1: accounting, evidence, underwriting | Forward-risk choice insufficiently adjudicated; fixed-exit owner-return check absent; consensus sourcing overgeneralized; cash-tax context incomplete | Corrected in revision 2 |
| 2: fresh committee and source/statistics reviewers | Historical cash context, economic rate argument, quote-implied operating requirements and observation-versus-persistence distinction needed strengthening | Corrected in revision 3 |
| 3: same fresh reviewers check their findings against the revised files | No remaining material issue; source conventions and an excerpt checksum corrected | Core benchmark parity |
| 4: independently adjudicated live-review concerns | Corrected market-share precision; clarified tax/terminal selection, transcript reliance, general recovery and future evidence; numerical forecasts unchanged | Fresh committee and source reviewers find no remaining material issue |

[Committee assessment](reviews/fresh-chair-review.json) and [source/statistics assessment](reviews/fresh-stats-sources-review.json) retain their scopes, reviewed-file hashes, findings and limits. Earlier reports are preserved in `reviews/` and concern earlier frozen revisions. The complete revision assessed by the fresh committee/source reviewers is preserved under `reviews/frozen-revision-4/`; final editorial clarifications preserve its main numerical forecasts and add a consistent debt-shield sensitivity. The [conflicting live-review adjudication](reviews/conflicting-live-review-adjudication.json) explains why no paid-review major allegation was upheld: one cash calculation omitted commercial paper, another objected to correct rounding, and several overlooked explicit disclosures. Useful comparator/basis clarifications were incorporated. Original critical reports remain visible. Local paths in original reports identify the review workspace; the current delivered files are in this example directory.

The comparison used the November 5, 2026 FIG and MU papers fetched from the actual DeepValue bundle API. Their SHA-256 hashes are recorded in the committee report. Those papers were reference work products, not presumed-correct authorities or an independently validated performance sample. Their complete third-party source material is not redistributed here.

## Reproduction and limits

`assumptions.json` is editable; `valuation.json`, `cashflows.csv` and `sensitivity.csv` are calculated. An independent script, without importing the runner implementation, passed **429 arithmetic checks**, including operating bridges, all three scenario valuations, eight counterfactuals, financing/rate comparisons, monitoring values and fixed-exit return identities. It is included with [its result](independent-financial-identities.json).

```bash
python -m investmentideas --revalue examples/AAPL-2026-11-06-revised/assumptions.json --output revalued
python examples/AAPL-2026-11-06-revised/independent-checks/financial_identities.py
```

The supplied source records contain compact facts, selected exact excerpts, links and hashes. Full third-party research reports and vendor price histories are not bundled. The source reviewer independently recomputed the return statistics from the privately retained raw chart responses and checked primary originals; a hash alone does not authenticate a vendor's underlying data.

This example combines agent-directed research and editorial iterations with the runner's deterministic model. The final live check uses an explicit archived source snapshot, supplied forecast and supplied author body. It exercises input pinning, recomputation, frozen-draft review, spending controls and artifacts; it does **not** establish that every new ticker generated automatically will attain the same research depth. Unsupported sectors, incomplete sources, uncertain forecasts and unresolved findings require judgment and can prevent completion.

The application passes **268 tests** from source and a fresh installed wheel, with no runtime dependencies. CLI, offline recomputation and local UI/Host/CSRF checks also pass. These software checks do not establish forecast truth.

## Live review and costs

The five additional OpenRouter requests report **$1.665431** in usage cost: initial GPT-5.4 review/repair $0.5162315, revised GPT-5.4 review $0.4299495, and a separate Sonnet 4.6 review/repair $0.71925. The $1 allowance attempt stopped before a paid request. The resumed Sonnet ledger includes the previous GPT request; it is counted once in the [combined cost summary](cost-summary.json). Provider account counters can lag; these are request-ledger costs, not an invoice reconciliation.

**The automated workflow attempts did not approve the paper.** Exact-excerpt checks rejected malformed/noncontiguous quotations in two attempts; one GPT review parsed and returned revise. Both models also made factual or over-severe allegations. The original responses, the valid GPT report and independent adjudications are retained. The [Sonnet adjudication](reviews/sonnet-review-adjudication.json) identifies useful bounded edits and unsupported claims, including a six-item list miscounted as seven. No blocker or major allegation was upheld as major after checking the exact draft, primary evidence and computations. These conclusions are editorial judgments, not converted machine passes.

The final [committee review](reviews/fresh-chair-review.json) confirms benchmark parity and no material underwriting regression after the corrections. The final [source/statistics review](reviews/fresh-stats-sources-review.json) records its own scope. We stopped additional paid review loops once the remaining disagreements called for evidence-based adjudication rather than more sampling. The software's unresolved-finding gate remains intact; no approval was manufactured or model verdict silently changed.

The final paper includes an independently checked 17% debt-shield comparison. It exposed and fixed a real software defect: a financing/tax comparison must recompute a beta derived from those same financing inputs. Smart/straight quotation marks are now recorded as typography-only matches; changed wording, numbers and disjoint quotations still fail. Both changes have focused regression coverage.

The earlier full AAPL development ledger recorded $12.2630191 in reported charges and retained reservations, including failed, formatting and correction iterations. It is a historical development cost, not a general per-memo price. This upgrade's additional OpenRouter usage is reported separately above; local agent work is not included in that API ledger.

The new cost summary distinguishes provider-reported costs from retained estimates. Context compaction and completed-stage reuse avoid unnecessary repetition while preserving substantive inputs. No controlled model-quality frontier was measured, so this work does not claim globally optimal routing or quantified token savings from byte-size reductions.

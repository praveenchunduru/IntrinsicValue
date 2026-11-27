# Live validation with your OpenRouter key

Start the local app with `python -m investmentideas`, open http://127.0.0.1:8765,
and enter your own key. No GitHub secret or account connection is required.
The key remains in process memory; it is not saved in research artifacts, logs
or browser storage. Stop the app when finished.

Enter a real requester email in the SEC contact field, use `--sec-contact` with
the CLI, or configure `SEC_CONTACT_EMAIL` / a contact-bearing `SEC_USER_AGENT`.
This identifies filing requests to SEC and is not an additional paid API key.
The per-run contact stays in memory, is sent only to SEC, and is omitted from
run identities, research artifacts, browser status and logs.

Use a dedicated OpenRouter key with a provider credit limit. The app's local
pre-call budget is separate: it reserves conservative costs before requests and
retains ambiguous charges after errors. It is not a provider-enforced cap.
Completed same-day stages are reused; increasing an allowance preserves previous
charges and reservations. Do not restart empty runs to bypass a local budget.

Full mode defaults to a $10 total planning allowance; quick mode retains $2.
Explicit user allowances are preserved. Free collection and primary-input coverage
checks precede all paid searches and model stages. Missing/conflicting essential
primary inputs stop at zero new paid requests with a coverage report. A same-settings
retry after missing coverage reattempts free collection and preserves prior sources
and spending; an explicitly dated source snapshot remains frozen.

The initial budget preflight prices currently known context across the first draft
and review using uncached byte reservations, configured output limits and maximum
catalog pricing tiers. An insufficient allowance stops before paid work and saves
`budget-preflight.json`. Its reservation floor excludes unknown future search results,
synthesis, forecasts, computed valuation, memo text, repairs and revisions. It does
not predict an invoice or guarantee completion; each actual complete prompt is
reserved separately before transport.

The [offline budget regression](../tests/public_runner/test_budget_workflow.py) exercises
fresh-from-ticker collection, complete stage contracts and zero assumed cache hits
with large archived evidence shapes and explicitly synthetic financial-scope evidence.
At its stated usage/rate assumptions (GPT-5.4 review at $2.50/$15 per million input/output
tokens), the first pass (draft, its fix call and one review) needs a peak $3.13
allowance and the default single revision $5.37. Simulated reported usage is $1.89
and $4.13 respectively. The fixture omits the calibration sources, so it does not
exercise the market-consistent rate pins, and it assumes no prompt caching; live
GPT-5.4 reviews with cached evidence cost about $0.07 instead of $0.40.
These simulations support the tested $10 default path, not a general price or
completion promise. They are not observed provider bills or research-quality evidence.

The economy profile uses open-weight DeepSeek V4.1 Flash for extraction/search,
DeepSeek V4 Pro for analysis and Kimi K3.1 Pro for review. Balanced uses Claude Haiku
5.5 for easy work, Claude Opus 5.5 for analysis and GPT-6.2 Sol for review (newest
generation in each slot, checked 2026-11-26). The runs recorded above used the earlier
Qwen, Claude Sonnet 4.6 and GPT-5.4 routing. The default reviewer differs from
the author model. An explicit review-model override can name any model and is saved
with the run. Prices and availability are checked
against the live catalog; there are no silent model substitutions or client retries.

Full mode collects evidence, pins supported reported inputs, calculates an editable
valuation in Python, writes a memo and reviews the frozen evidence/model/draft.
Material findings trigger bounded research and revision. Missing facts, exhausted
budgets and unresolved findings remain visible; they do not become approval.
Completed runs bind downloaded artifacts and raw source archives to exact hashes.
An edited download is preserved and requires a fresh review; a changed primary
archive stops paid work until its original bytes are restored or a new run is started.
Browser status/download requests also check the smaller delivered-file binding
without changing files or calling models. Status marks changed content as needing
review, and changed downloads return a conflict response. Raw archives are not
rehashed on timer polls; all archive checksums are verified on pipeline resume
and before completion.

Saved files under research-output/ include source archives, assumptions, cashflows,
valuation, reviews, revisions and the cumulative usage ledger. Review manifests
bind the assessed memo, evidence and computed output by hash. A model pass is an
assessment, not independent certification or proof of investment truth.

The dated AAPL example in examples/AAPL-2026-11-06/ is the result of iterative
live validation. It includes the final reviewed memo, editable model and source
records. Its $12.2630191 cumulative ledger includes development/review iterations;
this is not a general per-memo cost estimate. The final review reports zero
findings and the numerical checks pass. Evidence gaps and house assumptions
remain disclosed. Key/account metadata and private repository history are excluded.

CI uses mocked transports and synthetic evidence only. No API key is needed to
run `python -m unittest discover -s tests/public_runner -v` or to recompute the
example offline. Do not add paid model calls or secrets to push/PR CI.

The [revised AAPL example](../examples/AAPL-2026-11-06-revised/memo.md) adds sourced
operating drivers, issuer-risk calibration and independent editorial adjudication.
Its [quality record](../examples/AAPL-2026-11-06-revised/quality-review.md) preserves
the automated nonapprovals and formatting failures, independent findings and
responses, exact reviewed-file scopes, and $1.665431 of additional request costs.
Editorial acceptance does not change a saved automated verdict or completion flag.

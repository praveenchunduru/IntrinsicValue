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
At its stated usage/rate assumptions, the first pass needs a peak $3.09 allowance;
three full revisions need $9.17. Simulated reported usage is $1.69 and $7.77 respectively.
These simulations support the tested $10 default path, not a general price or
completion promise. They are not observed provider bills or research-quality evidence.

The economy profile uses open-weight Qwen for extraction/search and DeepSeek for
analysis. Balanced uses the same Qwen easy-work routing and Claude Sonnet for
analysis. Review-model overrides are explicit. Prices and availability are checked
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

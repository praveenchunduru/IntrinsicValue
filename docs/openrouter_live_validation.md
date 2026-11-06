# Live validation with your OpenRouter key

Start the local app with `python -m investmentideas`, open http://127.0.0.1:8765,
and enter your own key. No GitHub secret or account connection is required.
The key remains in process memory; it is not saved in research artifacts, logs
or browser storage. Stop the app when finished.

Use a dedicated OpenRouter key with a provider credit limit. The app's local
pre-call budget is separate: it reserves conservative costs before requests and
retains ambiguous charges after errors. It is not a provider-enforced cap.
Completed same-day stages are reused; increasing an allowance preserves previous
charges and reservations. Do not restart empty runs to bypass a local budget.

The economy profile uses open-weight Qwen for extraction/search and DeepSeek for
analysis. Balanced uses the same Qwen easy-work routing and Claude Sonnet for
analysis. Review-model overrides are explicit. Prices and availability are checked
against the live catalog; there are no silent model substitutions or client retries.

Full mode collects evidence, pins supported reported inputs, calculates an editable
valuation in Python, writes a memo and reviews the frozen evidence/model/draft.
Material findings trigger bounded research and revision. Missing facts, exhausted
budgets and unresolved findings remain visible; they do not become approval.

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

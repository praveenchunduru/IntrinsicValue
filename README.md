# IntrinsicValue

**Open-source equity valuation built to a hedge-fund research standard.**

Enter a ticker. Get a discounted cash flow valuation, an editable model and a sourced
research memo in under 30 minutes, for about $5 in model fees.

**Not investment advice.** Research and education only. Language models write the memo
and make mistakes. Do your own work before you invest.

## Get started

Needs Python 3.10+ and an [OpenRouter API key](https://openrouter.ai/settings/keys).

```bash
git clone https://github.com/praveenchunduru/IntrinsicValue.git
cd IntrinsicValue
python3 -m investmentideas
```

Open http://127.0.0.1:8765, paste your key and email, and enter a ticker. The SEC asks
for a contact email before it serves filings. On Windows use `py` in place of `python3`.

To skip the form, set both values once and name the ticker:

```bash
export OPENROUTER_API_KEY=sk-or-...
export SEC_CONTACT_EMAIL=you@example.com
python3 -m investmentideas MSFT
```

- **Output:** the run prints the path to the memo. The model and sources sit beside it.
- **Rerun:** the same ticker on the same day resumes and does not pay twice. A new day refreshes the evidence.
- **Spend:** `--budget` sets the ceiling for a run. The default is $20.
- **Revalue:** edit `assumptions.json` and recompute offline at no cost with `--revalue`.

## Methodology

The rules follow best practice from leading investors and valuation experts.

- **Evidence:** SEC filings, the latest earnings release, market data and web research.
- **Forecast:** ten years, written three times from the same evidence and averaged.
- **Valuation:** discounted cash flow, computed in code. Language models do no arithmetic.
- **Discount rate:** current implied equity risk premium and industry betas, moving to market-average risk by year ten.
- **Growth:** paid for with reinvestment. R&D counts as investment.
- **Terminal value:** new investment earns its cost of capital. Growth equals the risk-free rate.
- **Cases:** the base case is the headline. Bear and bull are unweighted stress cases.
- **Benchmark:** the S&P 500 values at about 0.80 of its price under the same rules (November 2026).
- **Review:** a model from a different lab checks the memo against the evidence.

More: [method](docs/underwriting.md) · [commands and outputs](docs/reference.md) · [audit record](docs/audit-status.md)

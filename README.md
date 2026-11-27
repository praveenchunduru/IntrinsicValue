# IntrinsicValue

**Open-source equity valuation built to a hedge-fund research standard.**

Enter a ticker. Get a discounted cash flow valuation, an editable model and a sourced
research memo in under 30 minutes, for about $5 in model fees.

**Not investment advice.** Research and education only. Language models write the memo
and make mistakes. Do your own work before you invest.

## Get started

Needs Python 3.9 or newer (the Mac's own `python3` works) and an
[OpenRouter API key](https://openrouter.ai/settings/keys).

```bash
git clone https://github.com/praveenchunduru/IntrinsicValue.git
cd IntrinsicValue
python3 -m intrinsicvalue --setup
python3 -m intrinsicvalue MSFT
```

`--setup` asks once for your key and your email (the SEC asks for a contact before it
serves filings) and saves them in `.env`. Prefer a form? Run `python3 -m intrinsicvalue`
with no ticker and your browser opens it. On Windows, use `py` in place of `python3`.

- **Output:** keep the window open until the run ends. It prints the value, the cost and the path to `memo.html`, which opens in a browser and prints to PDF.
- **Past runs:** `--list` shows each run's date, value, cost and memo.
- **Rerun:** an unfinished run of the same ticker resumes within 24 hours and does not pay twice. `--dry-run` checks inputs for free.
- **Spend:** `--budget` sets the ceiling for a run. The default is $20.
- **Revalue:** edit `assumptions.json` and recompute offline at no cost with `--revalue`.
- **Update:** `git pull`. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

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

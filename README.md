# IntrinsicValue

**Open-source equity valuation built to a hedge-fund research standard.**

Enter a ticker. Get a sourced discounted cash flow valuation and a research memo in
under 30 minutes, for about $5 in model fees.

## What you get

- **Value per share:** base, bear and bull cases on a ten-year forecast.
- **Price-implied expectations:** the growth, margin, return on capital or discount rate that makes today's price fair.
- **Research memo:** business, capital allocation, forecast, risks and what would change the value.
- **Editable model:** every assumption in one file. Revalue offline in seconds.
- **Source trail:** figures from SEC filings, every claim cited, unverified material labelled.

## How it works

- **Evidence:** SEC filings, the latest earnings release, market data and web research.
- **Forecast:** written three times from the same evidence and averaged (default profile).
- **Valuation:** computed in code. Language models do no arithmetic.
- **Review:** a model from a different lab checks the memo against the evidence. Open findings stay visible.

## Method

- **Standard:** best practice from leading investors and valuation experts, written as explicit rules.
- **Discount rate:** current implied equity risk premium and industry betas, moving to market-average risk by year ten.
- **Growth:** paid for with reinvestment. R&D counts as investment.
- **Terminal value:** new investment earns its cost of capital. Growth equals the risk-free rate.
- **Cross-checks:** historical base rates for growth, other interest-rate regimes and alternative conventions.

## Reading the output

- **Headline:** the base case. Bear and bull are unweighted stress cases.
- **Benchmark:** the S&P 500 values at about 0.80 of its price under the same rules (November 2026). A company at 0.80 is priced like the market.
- **Sensitivities:** each key convention is repriced one change at a time.
- **Limits:** a dated estimate built on a model-written forecast. Two runs can differ.

## Not investment advice

- Research and education only. No recommendations, ratings or price targets.
- Language models write and review the memo, and they make mistakes.
- The authors are not your advisers and accept no liability. Do your own work or consult a licensed adviser before you invest.

## Documentation

- [Reference](docs/reference.md): setup, commands, output files, models and costs. Needs Python 3.10+ and one OpenRouter API key.
- [Method](docs/underwriting.md): the valuation rules in full.
- [Audit record](docs/audit-status.md): external audit, live runs and open issues.

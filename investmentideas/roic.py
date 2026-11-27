"""Return on invested capital by Mauboussin and Callahan's method.

Source: Michael J. Mauboussin and Dan Callahan, "Return on Invested Capital: How
to Calculate ROIC and Handle Common Issues", Counterpoint Global, October 2022
(page numbers below are that paper's). Owner request, 2026-11-26: build the ROIC
analysis to Mauboussin's instructions.

- NOPAT = EBITA x (1 - cash tax rate), the estimate the paper calls usually
  sufficient (p4). EBITA is operating income plus amortization of acquired
  intangibles plus the interest embedded in operating lease cost (p3). The cash
  tax rate is the provision less deferred tax expense, over pretax income.
- Invested capital, operating approach (p5-6): total assets less
  non-interest-bearing current liabilities (current liabilities other than
  debt), less cash and current securities above an operating cash balance of 2%
  of revenue (p7, p11), less non-operating investments (noncurrent securities
  and equity investments, p6). This equals current assets less excess cash and
  NIBCLs, plus PP&E, right-of-use assets, goodwill, acquired intangibles and
  other operating assets.
- ROIC = NOPAT / average of the year's opening and closing invested capital (p7).
- Also shown (p27-28, Exhibit 20): ROIC without goodwill and acquired
  intangibles, and ROIC with internally generated intangibles capitalized, by
  the company method the paper applies to Microsoft (p18-20): 100% of R&D over
  six years, 70% of selling and marketing and 20% of G&A over two years each,
  straight line from the year after the spending.
- Incremental ROIC over three years: the NOPAT change over three years divided
  by the invested-capital change one year earlier (p22). It is noisy and the
  paper warns against comparing it directly with the cost of capital (p22-23).

Checked against the paper's Microsoft fiscal 2022 example (p5, p7, p19-20):
NOPAT $70 billion, invested capital $96, $120 and $165 billion for 2020-2022,
ROIC 49%, 94% without goodwill and acquired intangibles, 34% with intangibles
capitalized (NOPAT $80 billion, invested capital $260 billion).
"""
from __future__ import annotations

OPERATING_CASH_SHARE = .02
STATUTORY_TAX_FALLBACK = .25
# (share treated as investment, life in years); Corrado and Hulten, as applied to
# Microsoft in the paper (p18). Applied only when selling and marketing and G&A
# are reported separately: the paper gives no share for SG&A as one line.
CAPITALIZATION = {'research_and_development': (1.0, 6),
                  'selling_and_marketing': (.7, 2),
                  'general_and_administrative': (.2, 2)}
ROIIC_YEARS = 3
# Consecutive fiscal years are 350-380 days apart; a gap breaks every lagged figure.
CONSECUTIVE_DAYS = (350, 380)

DURATIONS = {
    'amortization_acquired_intangibles': ('AmortizationOfIntangibleAssets',),
    'deferred_tax_expense': ('DeferredIncomeTaxExpenseBenefit',),
    'selling_and_marketing': ('SellingAndMarketingExpense', 'MarketingExpense'),
    'general_and_administrative': ('GeneralAndAdministrativeExpense',),
    # Only for EBIT where no operating income is reported (Johnson & Johnson).
    'interest_expense': ('InterestExpense', 'InterestExpenseNonoperating', 'InterestExpenseDebt'),
    'interest_income': ('InvestmentIncomeInterest', 'InvestmentIncomeInterestAndDividend'),
}
INSTANTS = {
    'assets': ('Assets',),
    'current_liabilities': ('LiabilitiesCurrent',),
    'debt_current': ('DebtCurrent',),
    # Home Depot and Coca-Cola tag current long-term debt with the lease-inclusive concept.
    'long_term_debt_current': ('LongTermDebtCurrent', 'LongTermDebtAndCapitalLeaseObligationsCurrent'),
    'short_term_borrowings': ('ShortTermBorrowings',),
    'commercial_paper': ('CommercialPaper',),
    'other_short_term_borrowings': ('OtherShortTermBorrowings',),
    'cash': ('CashAndCashEquivalentsAtCarryingValue',),
    # The balance-sheet total where it is tagged; Microsoft's fiscal 2017 short-term
    # investments use the concept retired in 2018 (AvailableForSaleSecuritiesCurrent).
    'cash_and_short_term_investments': ('CashCashEquivalentsAndShortTermInvestments',),
    'securities_current': ('ShortTermInvestments', 'MarketableSecuritiesCurrent',
                           'AvailableForSaleSecuritiesDebtSecuritiesCurrent', 'DebtSecuritiesCurrent',
                           'AvailableForSaleSecuritiesCurrent', 'OtherShortTermInvestments'),
    # Coca-Cola reports marketable securities on their own line beside its cash and
    # short-term investments total.
    'marketable_securities_other': ('MarketableSecurities',),
    # Nvidia tags no current or noncurrent securities line: its balance-sheet
    # marketable securities are the debt securities total plus marketable equity.
    'securities_unsplit': ('AvailableForSaleSecuritiesDebtSecurities', 'DebtSecuritiesAvailableForSaleExcludingAccruedInterest'),
    'equity_securities_marketable': ('EquitySecuritiesFvNi',),
    # The balance-sheet investments line where it is tagged (Microsoft); otherwise
    # noncurrent securities plus the largest of the overlapping investment lines
    # (Alphabet's other investments contain its equity-method stakes).
    'long_term_investments': ('LongTermInvestments',),
    'securities_noncurrent': ('MarketableSecuritiesNoncurrent', 'AvailableForSaleSecuritiesDebtSecuritiesNoncurrent',
                              'AvailableForSaleSecuritiesNoncurrent'),
    'equity_method_investments': ('EquityMethodInvestments',),
    'other_long_term_investments': ('OtherLongTermInvestments',),
    'equity_securities_nonmarketable': ('EquitySecuritiesWithoutReadilyDeterminableFairValueAmount',
                                        'EquitySecuritiesFVNINoncurrent'),
    # An overfunded pension is non-operating (p6); Johnson & Johnson's is $7.3 billion.
    'pension_assets': ('DefinedBenefitPlanAssetsForPlanBenefitsNoncurrent',),
    'goodwill': ('Goodwill',),
    'intangibles': ('IntangibleAssetsNetExcludingGoodwill',),
    'intangibles_finite': ('FiniteLivedIntangibleAssetsNet',),
    'intangibles_indefinite': ('IndefiniteLivedIntangibleAssetsExcludingGoodwill',),
    # Coca-Cola tags its indefinite-lived intangibles by kind.
    'intangibles_trademarks': ('IndefiniteLivedTrademarks',),
    'intangibles_franchise': ('IndefiniteLivedFranchiseRights',),
    'operating_lease_liability': ('OperatingLeaseLiability',),
}
RATES = {'operating_lease_rate': ('OperatingLeaseWeightedAverageDiscountRatePercent',)}
# Investment lines that can contain one another; only the largest is removed.
NESTED_INVESTMENTS = ('equity_method_investments', 'other_long_term_investments', 'equity_securities_nonmarketable')


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _amount(row, name):
    return _number(row.get(name)) or 0


def unsplit_securities(row):
    """True when no cash-and-investments total and no current or noncurrent securities line is tagged.

    Nvidia's case: its marketable securities are then the debt securities total
    plus marketable equity, both counted with cash.
    """
    return all(row.get(name) is None for name in ('cash_and_short_term_investments', 'securities_current',
                                                  'securities_noncurrent'))


def nonoperating_investments(row):
    """(field, amount) lines that invested capital removes as non-operating investments.

    The balance-sheet investments line where it is tagged; otherwise noncurrent
    securities plus the largest of the investment lines that can nest (Alphabet's
    other investments contain its equity-method stakes). The equity bridge adds
    the same lines, less any already selected as cash (inputs.py).
    """
    if row.get('long_term_investments') is not None:
        return [('long_term_investments', _amount(row, 'long_term_investments'))]
    lines = [('securities_noncurrent', _amount(row, 'securities_noncurrent'))] if row.get('securities_noncurrent') is not None else []
    nested = [name for name in NESTED_INVESTMENTS if row.get(name) is not None]
    if nested:
        # The largest overlapping line: an investment left in errs toward lower ROIC,
        # one counted twice could take capital below zero.
        largest = max(nested, key=lambda name: _amount(row, name))
        lines.append((largest, _amount(row, largest)))
    return lines


def invested_capital(row):
    """Operating-approach invested capital, or None without total assets and current liabilities."""
    assets, current = _number(row.get('assets')), _number(row.get('current_liabilities'))
    revenue = _number(row.get('revenue'))
    if assets is None or current is None or revenue is None:
        return None
    # Commercial paper is part of short-term borrowings; Microsoft tags the same
    # amount under both, so only one is taken.
    short = (_amount(row, 'short_term_borrowings') if row.get('short_term_borrowings') is not None else
             _amount(row, 'commercial_paper') + _amount(row, 'other_short_term_borrowings'))
    debt = (_number(row.get('debt_current')) if row.get('debt_current') is not None else
            _amount(row, 'long_term_debt_current') + short)
    if row.get('cash_and_short_term_investments') is not None:
        cash = _number(row['cash_and_short_term_investments'])
        if row.get('securities_current') is None:
            cash += _amount(row, 'marketable_securities_other')
    elif not unsplit_securities(row):
        cash = _amount(row, 'cash') + _amount(row, 'securities_current')
    else:
        cash = (_amount(row, 'cash') + _amount(row, 'securities_unsplit') + _amount(row, 'equity_securities_marketable') +
                _amount(row, 'marketable_securities_other'))
    excess = max(0.0, cash - OPERATING_CASH_SHARE * revenue)
    investments = sum(amount for _, amount in nonoperating_investments(row))
    return assets - (current - debt) - excess - investments - _amount(row, 'pension_assets')


def acquired_intangibles(row):
    """Goodwill plus acquired intangibles, or None when goodwill is unreported."""
    if all(row.get(name) is None for name in ('goodwill', 'intangibles', 'intangibles_finite', 'intangibles_indefinite',
                                              'intangibles_trademarks')):
        return None
    if row.get('intangibles') is not None:
        other = _amount(row, 'intangibles')
    else:
        indefinite = (_amount(row, 'intangibles_indefinite') if row.get('intangibles_indefinite') is not None else
                      _amount(row, 'intangibles_trademarks') + _amount(row, 'intangibles_franchise'))
        other = _amount(row, 'intangibles_finite') + indefinite
    return _amount(row, 'goodwill') + other


def cash_tax_rate(row):
    """(rate, basis): provision less deferred tax expense over pretax income, else effective, else statutory."""
    provision, pretax = _number(row.get('income_tax_expense')), _number(row.get('pretax_income'))
    deferred = _number(row.get('deferred_tax_expense'))
    if provision is not None and pretax and pretax > 0:
        if deferred is not None:
            rate = (provision - deferred) / pretax
            if 0 <= rate <= .5:
                return rate, 'cash'
        rate = provision / pretax
        if 0 <= rate <= .5:
            return rate, 'effective'
    return STATUTORY_TAX_FALLBACK, 'statutory'


def _consecutive(earlier, later):
    from datetime import date
    try:
        days = (date.fromisoformat(later['fiscal_year_end']) - date.fromisoformat(earlier['fiscal_year_end'])).days
    except (KeyError, TypeError, ValueError):
        return False
    return CONSECUTIVE_DAYS[0] <= days <= CONSECUTIVE_DAYS[1]


def _capitalized(history, index):
    """(investment, amortization, net asset) at ``index`` from straight-line vintages, or None.

    Needs every line's spending for the asset life before the year. A line missing
    from the whole history is not taken as zero: Amazon reports its technology
    spending under its own concept, not R&D.
    """
    investment = amortization = net = 0.0
    for line, (share, life) in CAPITALIZATION.items():
        if index < life:
            return None
        window = history[index - life:index + 1]
        if any(row.get(line) is None for row in window) or not all(
                _consecutive(a, b) for a, b in zip(window, window[1:])):
            return None
        spend = [share * row[line] for row in window]
        investment += spend[-1]
        amortization += sum(spend[:-1]) / life
        # Vintage t is whole; vintage t-k has (life - k) / life of its cost left.
        net += sum(value * (life - age) / life for age, value in enumerate(reversed(spend)) if age < life)
    return investment, amortization, net


def _split_reported(row):
    """Selling and marketing and G&A are both reported and, where SG&A is reported, add to it."""
    selling, general = _number(row.get('selling_and_marketing')), _number(row.get('general_and_administrative'))
    total = _number(row.get('selling_general_administrative'))
    if selling is None or general is None:
        return False
    return total is None or abs(selling + general - total) <= .01 * abs(total)


def history(rows):
    """ROIC measures for each fiscal year, oldest first, from raw inputs.

    ``rows`` are oldest first and carry revenue, operating income, tax provision,
    pretax income and the DURATIONS, INSTANTS and RATES fields. The first rows
    serve only as openings for averages, lags and capitalized intangibles.
    """
    split = bool(rows) and all(_split_reported(row) for row in rows if row.get('selling_and_marketing') is not None
                               or row.get('general_and_administrative') is not None) and any(
        _split_reported(row) for row in rows)
    output = []
    for index, row in enumerate(rows):
        prior = rows[index - 1] if index and _consecutive(rows[index - 1], row) else None
        capital = invested_capital(row)
        acquired = acquired_intangibles(row)
        lease = _number(row.get('operating_lease_liability'))
        rate = _number(row.get('operating_lease_rate'))
        opening_lease = _number(prior.get('operating_lease_liability')) if prior else None
        lease_interest = (rate * ((lease + opening_lease) / 2 if opening_lease is not None else lease)
                          if lease is not None and rate is not None and 0 <= rate < .5 else 0.0)
        operating, ebit_basis = _number(row.get('operating_income')), 'operating income'
        if operating is None and _number(row.get('pretax_income')) is not None and _number(row.get('interest_expense')) is not None:
            # No operating income line: pretax income plus net interest expense, so
            # other non-operating items stay in EBIT.
            operating = row['pretax_income'] + row['interest_expense'] - _amount(row, 'interest_income')
            ebit_basis = 'pretax income plus net interest expense'
        tax_rate, tax_basis = cash_tax_rate(row)
        ebita = (operating + _amount(row, 'amortization_acquired_intangibles') + lease_interest
                 if operating is not None else None)
        nopat = ebita * (1 - tax_rate) if ebita is not None else None
        item = {'fiscal_year_end': row.get('fiscal_year_end'), 'ebita': ebita, 'nopat': nopat, 'ebit_basis': ebit_basis,
                'operating_lease_interest': lease_interest, 'nopat_tax_rate': tax_rate, 'nopat_tax_basis': tax_basis,
                'invested_capital': capital,
                'invested_capital_excluding_acquired': capital - acquired if capital is not None and acquired is not None else None}
        revenue = _number(row.get('revenue'))
        item['nopat_margin'] = nopat / revenue if nopat is not None and revenue else None
        opening = output[-1] if prior else None
        def average(key):
            if not opening or opening.get(key) is None or item.get(key) is None:
                return None
            return (opening[key] + item[key]) / 2
        def ratio(numerator, denominator):
            if numerator is None or denominator is None:
                return None, False
            if denominator <= 0:
                return None, numerator > 0
            return numerator / denominator, False
        item['average_invested_capital'] = average('invested_capital')
        item['return_on_invested_capital'], item['return_on_capital_unbounded'] = ratio(nopat, item['average_invested_capital'])
        item['capital_turnover'] = (revenue / item['average_invested_capital']
                                    if revenue and item['average_invested_capital'] and item['average_invested_capital'] > 0 else None)
        excluding = average('invested_capital_excluding_acquired')
        item['return_excluding_acquired'], item['return_excluding_acquired_unbounded'] = ratio(nopat, excluding)
        item['investment_rate'] = ((capital - opening['invested_capital']) / nopat
                                   if opening and capital is not None and opening.get('invested_capital') is not None
                                   and nopat and nopat > 0 else None)
        capitalized = _capitalized(rows, index) if split else None
        if capitalized and nopat is not None and capital is not None:
            investment, amortization, net = capitalized
            item.update(intangible_investment=investment, intangible_amortization=amortization,
                        capitalized_intangibles=net, adjusted_nopat=nopat + investment - amortization,
                        adjusted_invested_capital=capital + net)
        adjusted = average('adjusted_invested_capital')
        item['adjusted_return'], item['adjusted_return_unbounded'] = ratio(item.get('adjusted_nopat'), adjusted)
        # Three-year incremental return: NOPAT change over three years against the
        # capital change over the three years ending one year earlier (p22).
        item['incremental_return'] = None
        if index >= ROIIC_YEARS + 1 and all(_consecutive(a, b) for a, b in zip(rows[index - ROIIC_YEARS - 1:index],
                                                                            rows[index - ROIIC_YEARS:index + 1])):
            then = output[index - ROIIC_YEARS]
            before, after = output[index - ROIIC_YEARS - 1].get('invested_capital'), output[index - 1].get('invested_capital')
            if nopat is not None and then.get('nopat') is not None and before is not None and after is not None:
                change = after - before
                item['incremental_return'] = (nopat - then['nopat']) / change if change > 0 else None
                item['incremental_capital_change'] = change
        output.append(item)
    if not split:
        status = 'no_split'
    elif all(row.get('research_and_development') is None for row in rows):
        status = 'no_rnd'
    else:
        status = 'computed' if output and output[-1].get('adjusted_return') is not None else 'short_history'
    return output, status

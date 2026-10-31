"""
Writes the card support analysis as Markdown for people, JSON for code, and SVG figures, with small row counts
suppressed (SEC-03).
"""

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from faro.analysis import cards_figures
from faro.analysis.candidates import DECLINE_REASONS
from faro.analysis.cards import (
    CREDIT,
    DEBIT,
    MEANINGS,
    MOST_CARDS,
    QUANTILES,
    RECENCY,
    UTILIZATION_STEP,
    VOLUME_WINDOWS,
    WINDOWS,
    YEAR,
    Association,
    Breakdown,
    CardSupport,
    CoMissing,
    Group,
    Spread,
    Window,
)
from faro.analysis.plotting import save
from faro.analysis.report import (
    SUPPRESS_BELOW,
    count,
    markdown_table,
    share,
    suppress,
)
from faro.split import HELD_OUT_EVERY

ADR = "../adr/0003-choose-workflow-from-evidence.md"
FIGURES = "figures"
DAILY_FIGURE = "card-support-daily.svg"
DECLINES_FIGURE = "card-support-declines.svg"
UTILIZATION_FIGURE = "card-support-utilization.svg"
DATES_FIGURE = "card-support-dates.svg"
# Cohen's convention: a V under 0.1 is a negligible association.
NEGLIGIBLE = 0.1
ACTIVE = "Active"
STATUSES = ("Active", "Blocked", "Closed", "Suspended")
WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
PERIODS = {"1": "Last 12 months", "2": "12 to 24 months before", "3": "Earlier"}
NO_CODE = "no code"

ROW_COUNTS = frozenset(
    {
        "customers",
        "held_out",
        "rows",
        "hits",
        "transactions",
        "cards",
        "distinct",
        "digits_only",
        "luhn_valid",
        "shared_last_four",
        "shared_last_four_active",
        "shared_last_four_active_same_type",
        "active_cards",
        "within",
        "without_transactions",
        "with_limit",
        "over_limit",
        "at_zero",
        "negative",
        "matching_an_account",
        "expiration_missing",
        "expired",
        "expires_before_opening",
        "updated_after_as_of",
        "equal",
        "earlier",
        "later",
        "recorded_without_transactions",
        "transactions_without_record",
        "neither",
        "before_opening",
        "after_expiration",
        "converted",
        "first_missing",
        "second_missing",
        "both_missing",
        "populated",
    }
)


def _few(n: object) -> bool:
    return isinstance(n, int) and 0 < n < SUPPRESS_BELOW


def _hide_small_spreads(value: object) -> object:
    # A quantile over 1 to 9 rows could give an individual value away.
    if isinstance(value, dict):
        hidden = {k: _hide_small_spreads(v) for k, v in value.items()}
        if "quantiles" in hidden and _few(hidden.get("rows")):
            hidden["quantiles"] = None
        if "ratio" in hidden and _few(hidden.get("converted")):
            hidden["ratio"] = None
        return hidden
    if isinstance(value, list | tuple):
        return [_hide_small_spreads(v) for v in value]
    return value


def to_json(result: CardSupport) -> str:
    data = {
        **asdict(result),
        "as_of": result.as_of.isoformat(sep=" "),
        "settings": {
            "held_out_every": HELD_OUT_EVERY,
            "windows": list(WINDOWS),
            "recency_windows": list(RECENCY),
            "volume_windows": list(VOLUME_WINDOWS),
            "quantiles": list(QUANTILES),
            "utilization_step": UTILIZATION_STEP,
            "most_cards": MOST_CARDS,
        },
    }
    return (
        json.dumps(
            suppress(_hide_small_spreads(data), ROW_COUNTS),
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )


def _cells(b: Breakdown) -> dict[tuple[str | None, ...], Group]:
    return {g.values: g for g in b.groups}


def _rows(b: Breakdown, *values: str | None) -> int:
    group = _cells(b).get(values)
    return group.rows if group else 0


def _percent(n: int, total: int) -> str:
    if 0 < n < SUPPRESS_BELOW or not total:
        return "n/a"
    return f"{n / total:.1%}"


def _quantile(s: Spread, q: float, fmt: Callable[[float], str]) -> str:
    if s.quantiles is None or 0 < s.rows < SUPPRESS_BELOW:
        return "n/a"
    return fmt(s.quantiles[QUANTILES.index(q)])


def _plain(value: float) -> str:
    return f"{value:,.1f}".removesuffix(".0")


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _ratio(value: float) -> str:
    return f"{value:.1%}"


def _ordinal(q: float) -> str:
    return f"{round(100 * q)}th"


def _v(a: Association) -> str:
    return "n/a" if a.cramers_v is None else f"{a.cramers_v:.3f}"


def _code(value: str | None) -> str:
    return f"`{value}`" if value is not None else "(missing)"


def _code_name(code: str | None) -> str:
    if code is None:
        return NO_CODE
    return f"`{code}` {MEANINGS[code]}" if code in MEANINGS else f"`{code}`"


def _join(items: Sequence[str]) -> str:
    if len(items) < 3:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _several(result: CardSupport, product_type: str) -> int:
    return sum(
        g.rows
        for g in result.holders.active_per_customer.groups
        if g.values[0] == product_type and int(str(g.values[1])) >= 2
    )


def _largest(associations: Iterable[Association]) -> Association | None:
    scored = [a for a in associations if a.cramers_v is not None]
    return max(scored, key=lambda a: a.cramers_v or 0.0) if scored else None


def _window(windows: Sequence[Window], days: int) -> Window | None:
    return next((w for w in windows if w.days == days), None)


def _findings(result: CardSupport) -> list[str]:
    return [
        f"- {line}"
        for line in (
            _numbers_finding(result),
            _last_four_finding(result),
            _several_finding(result),
            _last_transaction_finding(result),
            _dates_finding(result),
            _holder_finding(result),
            _codes_by_status_finding(result),
            _independence_finding(result),
            _currency_finding(result),
            _recency_finding(result),
            _credit_finding(result),
            _debit_finding(result),
            _cases_finding(result),
        )
    ]


def _numbers_finding(result: CardSupport) -> str:
    n = result.holders.numbers
    lengths = _join([str(length) for length in n.lengths])
    leading = _join(
        [f"`{g.values[0]}`" for g in n.leading_digits.groups if g.values[0] is not None]
    )
    return (
        f"**Card numbers are shaped like real ones.** Of {count(n.cards)} cards, "
        f"{share(n.digits_only, n.cards)} have a `product_number` of digits only, {lengths} "
        f"digits long and starting with {leading}; {share(n.distinct, n.cards)} are distinct, "
        f"and {share(n.luhn_valid, n.cards)} pass the Luhn check real card numbers satisfy, "
        "where random check digits would pass one in ten. The tools show the last four digits "
        "only, and nothing this analysis publishes prints more (section 1)."
    )


def _last_four_finding(result: CardSupport) -> str:
    n = result.holders.numbers
    if not n.shared_last_four_active_same_type:
        return (
            "**The last four digits identify every active card of a type**: no customer holds "
            "two active cards of one type that end in the same four digits (CTL-02)."
        )
    return (
        "**Confirming a card by its last four digits (CTL-02) needs a fallback**: "
        f"{count(n.shared_last_four_active_same_type)} customers hold two active cards of the "
        "same type that end in the same four digits "
        f"({count(n.shared_last_four)} counting cards in any status)."
    )


def _several_finding(result: CardSupport) -> str:
    holders = result.holders.by_country_segment.rows
    return (
        f"**Holding several cards is common.** Of {count(holders)} customers with an active "
        f"card, {count(_several(result, CREDIT))} hold two or more active credit cards and "
        f"{count(_several(result, DEBIT))} two or more active debit cards, so a request that "
        "doesn't say which card is clarified before it is answered (AI-02)."
    )


def _last_transaction_finding(result: CardSupport) -> str:
    last = result.dates.last_transaction
    disagreeing = (
        last.earlier
        + last.later
        + last.recorded_without_transactions
        + last.transactions_without_record
    )
    verdict = "disagrees with" if disagreeing else "agrees with"
    return (
        f"**`last_transaction_date` {verdict} the card's transactions.** It equals the card's "
        f"latest transaction on {count(last.equal)} of {count(last.cards)} cards; it is earlier "
        f"on {count(last.earlier)} and later on {count(last.later)}, missing on "
        f"{count(last.transactions_without_record)} cards that have transactions, and present "
        f"on {count(last.recorded_without_transactions)} that have none. Where both exist, the "
        f"median gap is {_quantile(last.gap_days, 0.5, _plain)} days. Recency is read from "
        "the transactions (section 6)."
    )


def _dates_finding(result: CardSupport) -> str:
    active = next((c for c in result.dates.cards if c.product_status == ACTIVE), None)
    recent = result.dates.transactions[-1]
    expired = share(active.expired, active.cards) if active else "0"
    return (
        f"**Card dates conflict with card activity.** {expired} active cards are past their "
        f"expiration date. In the last {recent.days} days, "
        f"{share(recent.after_expiration, recent.transactions)} card transactions fall after "
        f"their card's expiration date and {share(recent.before_opening, recent.transactions)} "
        "before its opening date. The tools surface these as conflicts and resolve none "
        "(section 6)."
    )


def _holder_finding(result: CardSupport) -> str:
    statuses = result.holders.holder_status
    others = [g for g in statuses.groups if g.values[0] != ACTIVE]
    inactive = sum(g.rows for g in others)
    detail = _join([f"{g.values[0]} {count(g.rows)}" for g in others])
    return (
        f"**{share(inactive, statuses.rows)} active cards belong to customers who aren't "
        f"active** ({detail or 'none'}): the policy decides what the agent does when the "
        "signed-in customer's own record isn't active (section 1)."
    )


def _codes_by_status_finding(result: CardSupport) -> str:
    listed = set(DECLINE_REASONS)
    coded = {
        status: sum(
            g.rows
            for g in result.declines.codes_by_status.groups
            if g.values[0] == status and g.values[1] in listed
        )
        for status in ("Pending", "Reversed", "Approved")
    }
    return (
        "**Only a declined transaction is explained by its code.** Pending and reversed "
        f"transactions carry the same four codes ({count(coded['Pending'])} and "
        f"{count(coded['Reversed'])} of them), and approved ones "
        f"{'carry them too' if coded['Approved'] else 'carry `00`'} (section 3)."
    )


def _independence_finding(result: CardSupport) -> str:
    code = _largest(result.declines.association)
    flag = _largest(result.fraud.association)
    if code is None or flag is None:
        return "**No association could be measured** between codes, marks, and fields."
    related = max(code.cramers_v or 0.0, flag.cramers_v or 0.0) >= NEGLIGIBLE
    return (
        f"**Decline codes and fraud marks {'follow' if related else 'are unrelated to'} the "
        "fields recorded with the transaction.** The largest bias-corrected Cramér's V is "
        f"{_v(code)} between the code and `{code.field}` (against "
        f"{_v(result.declines.reference)} with `transaction_status`), and {_v(flag)} between "
        f"`is_fraud` and `{flag.field}` (against {_v(result.fraud.reference)} with "
        "`fraud_score`, which is drawn from it). An explanation cites the code and nothing "
        "else, and `is_fraud` stays the bank's flag only (sections 3 and 4)."
    )


def _currency_finding(result: CardSupport) -> str:
    by_country: dict[str, set[str]] = {}
    for g in result.currency.cards.groups:
        by_country.setdefault(str(g.values[0]), set()).add(str(g.values[1]))
    mexico = by_country.get("Mexico", set())
    lead = (
        "**Mexico has no pesos.** Every card of a Mexican customer is in USD."
        if mexico == {"USD"}
        else "**Card currencies by country:** "
        + "; ".join(
            f"{c} {', '.join(sorted(v))}" for c, v in sorted(by_country.items())
        )
        + "."
    )
    matching = sum(
        g.rows
        for g in result.currency.transactions.groups
        if g.values[0] == g.values[1]
    )
    total = result.currency.transactions.rows
    rates = [
        f"{c.currency} at {_plain(c.ratio[1])}" + _daily_range(c.daily_rate)
        for c in result.currency.conversions
        if c.ratio is not None and c.converted >= SUPPRESS_BELOW
    ]
    return (
        f"{lead} {share(matching, total)} of {count(total)} card transactions are in their "
        f"card's currency. `amount_usd` converts {_join(rates) or 'nothing'}, so amounts are "
        "shown in the card's currency and `amount_usd` is never quoted (section 7)."
    )


def _daily_range(daily: tuple[float, float] | None) -> str:
    if daily is None:
        return ""
    return f" (the bank's daily rate: {_plain(daily[0])} to {_plain(daily[1])})"


def _recency_finding(result: CardSupport) -> str:
    active = sum(r.active_cards for r in result.activity.recency)
    within = [
        sum(r.within[i] for r in result.activity.recency) for i in range(len(RECENCY))
    ]
    shares = _join(
        [
            f"{_percent(n, active)} in {days}"
            for days, n in zip(RECENCY, within, strict=True)
        ]
    )
    month = within[RECENCY.index(30)] if 30 in RECENCY else 0
    lead = "no" if 2 * month < active else "a"
    busiest = [
        f"{_quantile(v.per_card, 0.99, _plain)} in {v.days} days"
        for v in result.activity.volume
    ]
    return (
        f"**Most active cards have {lead} transaction in the last 30 days.** Of "
        f"{count(active)} active cards, the last transaction falls within {shares} days; "
        "among cards with any in the window, 99% have at most "
        f"{_join(busiest)}. The transactions tool's window and page size follow from these "
        "(section 2)."
    )


def _credit_finding(result: CardSupport) -> str:
    credit = result.balances.credit
    missing = credit.active_cards - credit.with_limit
    return (
        f"**Available credit can be negative or unknown.** {share(credit.over_limit, credit.active_cards)} "
        f"active credit cards carry a balance above their limit, and "
        f"{share(missing, credit.active_cards)} have no limit; the available-credit answer "
        "needs wording for both (section 5)."
    )


def _debit_finding(result: CardSupport) -> str:
    debit = result.balances.debit
    return (
        "**A debit card's `current_balance` isn't an account balance.** Products carry no link "
        f"from a card to an account, and {count(debit.matching_an_account)} of "
        f"{count(debit.active_cards)} active debit cards hold a balance equal to one of their "
        "holder's accounts. Available credit stays unsupported on debit cards (section 5)."
    )


def _cases_finding(result: CardSupport) -> str:
    def customers(windows: Sequence[Window]) -> str:
        return _join(
            [count(w.customers) for days in WINDOWS if (w := _window(windows, days))]
        )

    days = _join([str(d) for d in WINDOWS])
    return (
        "**Natural cases stay thin among development customers.** A decline with no listed "
        f"code backs {customers(result.declines.missing_code)} customers in {days} days, and a "
        "transaction marked `is_fraud` on an active card backs "
        f"{customers(result.fraud.windows)} (sections 3 and 4)."
    )


def _figure(alt: str, name: str) -> str:
    return f"![{alt}]({FIGURES}/{name})"


def _holders_lines(result: CardSupport) -> list[str]:
    h = result.holders
    types = sorted({str(g.values[0]) for g in h.status.groups})
    capped = str(MOST_CARDS)

    def held(value: str) -> str:
        return f"{value} or more" if value == capped else value

    counts = sorted({str(g.values[1]) for g in h.active_per_customer.groups}, key=int)
    segments = sorted({str(g.values[1]) for g in h.by_country_segment.groups})
    countries = sorted({str(g.values[0]) for g in h.by_country_segment.groups})
    n = h.numbers
    return [
        "## 1. Cards and holders",
        "",
        f"{count(h.customers)} of the {count(result.customers)} development customers hold a "
        "card, credit (`Tarjeta Crédito`) or debit (`Tarjeta Débito`).",
        "",
        *markdown_table(
            ["Card type", *STATUSES, "All"],
            (
                [
                    f"`{t}`",
                    *(count(_rows(h.status, t, s)) for s in STATUSES),
                    count(sum(_rows(h.status, t, s) for s in STATUSES)),
                ]
                for t in types
            ),
        ),
        "",
        "Cards per holder, in any status, and active cards of one type per holder of one:",
        "",
        *markdown_table(
            ["Cards held", "Holders"],
            (
                [held(str(g.values[0])), share(g.rows, h.cards_per_customer.rows)]
                for g in h.cards_per_customer.groups
            ),
        ),
        "",
        *markdown_table(
            ["Active cards of the type", *(f"`{t}`" for t in types)],
            (
                [held(c), *(count(_rows(h.active_per_customer, t, c)) for t in types)]
                for c in counts
            ),
        ),
        "",
        "Customers with an active card, by country and segment (for personas and segment "
        "sampling, EVL-12):",
        "",
        *markdown_table(
            ["Country", *segments],
            (
                [c, *(count(_rows(h.by_country_segment, c, s)) for s in segments)]
                for c in countries
            ),
        ),
        "",
        "Active cards by their holder's `customer_status`:",
        "",
        *markdown_table(
            ["`customer_status`", "Active cards"],
            (
                [str(g.values[0]), share(g.rows, h.holder_status.rows)]
                for g in h.holder_status.groups
            ),
        ),
        "",
        "### Card numbers",
        "",
        "Measured in SQL; no number, and no part of one beyond its first digit, leaves the "
        "query.",
        "",
        *markdown_table(
            ["Measure", "Cards"],
            [
                ["Cards", count(n.cards)],
                ["Distinct `product_number`s", share(n.distinct, n.cards)],
                ["Digits only", share(n.digits_only, n.cards)],
                ["Lengths", _join([str(length) for length in n.lengths]) or "none"],
                *(
                    [f"Starting with `{g.values[0]}`", share(g.rows, n.cards)]
                    for g in n.leading_digits.groups
                ),
                ["Passing the Luhn check", share(n.luhn_valid, n.cards)],
            ],
        ),
        "",
        *markdown_table(
            [
                "Customers holding two cards that end in the same four digits",
                "Customers",
            ],
            [
                ["Any cards", count(n.shared_last_four)],
                ["Active cards", count(n.shared_last_four_active)],
                [
                    "Active cards of the same type",
                    count(n.shared_last_four_active_same_type),
                ],
            ],
        ),
        "",
    ]


def _periods(result: CardSupport) -> list[list[str]]:
    by_period: dict[int, list[tuple[date, int]]] = {}
    for g in result.activity.daily.groups:
        day = date.fromisoformat(str(g.values[0]))
        # The oldest period takes the days left over past three full years.
        index = min((result.business_date - day).days // YEAR, 2)
        by_period.setdefault(index, []).append((day, g.rows))
    rows = []
    for index in sorted(by_period):
        days = by_period[index]
        total = sum(n for _, n in days)
        end = result.business_date - timedelta(days=YEAR * index)
        rows.append(
            [
                f"{min(d for d, _ in days)} to {end}",
                str(len(days)),
                count(total),
                _plain(total / len(days)) if total >= SUPPRESS_BELOW else "n/a",
                count(min(n for _, n in days)),
                count(max(n for _, n in days)),
            ]
        )
    return rows


def _by_type_table(b: Breakdown, first: str) -> list[str]:
    values = sorted({str(g.values[0]) for g in b.groups})
    types = sorted({str(g.values[1]) for g in b.groups})
    return markdown_table(
        [first, *(f"`{t}`" for t in types), "Share of all"],
        (
            [
                v,
                *(count(_rows(b, v, t)) for t in types),
                _percent(sum(_rows(b, v, t) for t in types), b.rows),
            ]
            for v in values
        ),
    )


def _activity_lines(result: CardSupport) -> list[str]:
    a = result.activity
    return [
        "## 2. Activity",
        "",
        f"{count(a.transactions)} card transactions over the whole snapshot. A day is the 24 "
        "hours ending at the as-of time of day, as the windows count them.",
        "",
        _figure("Card transactions per day", DAILY_FIGURE),
        "",
        *markdown_table(
            [
                "Period",
                "Days",
                "Card transactions",
                "Mean per day",
                "Fewest in a day",
                "Most in a day",
            ],
            _periods(result),
        ),
        "",
        f"The rest of this section reads the last {YEAR} days, unless it says otherwise.",
        "",
        *markdown_table(
            ["Weekday", "Card transactions"],
            (
                [WEEKDAYS[int(str(g.values[0])) - 1], share(g.rows, a.weekday.rows)]
                for g in a.weekday.groups
            ),
        ),
        "",
        "Active cards by how recent their last transaction is, over the whole snapshot:",
        "",
        *markdown_table(
            [
                "Card type",
                "Active cards",
                *(f"Last transaction within {d} days" for d in RECENCY),
                "No transaction",
            ],
            (
                [
                    f"`{r.product_type}`",
                    count(r.active_cards),
                    *(share(n, r.active_cards) for n in r.within),
                    share(r.without_transactions, r.active_cards),
                ]
                for r in a.recency
            ),
        ),
        "",
        "Transactions per active card, among cards with one in the window:",
        "",
        *markdown_table(
            [
                "Window",
                "Active cards with a transaction",
                "Median",
                "90th percentile",
                "99th percentile",
            ],
            (
                [
                    f"{v.days} days",
                    count(v.cards),
                    *(_quantile(v.per_card, q, _plain) for q in (0.5, 0.9, 0.99)),
                ]
                for v in a.volume
            ),
        ),
        "",
        *_by_type_table(a.channels, "`channel`"),
        "",
        *_by_type_table(a.types, "`transaction_type`"),
        "",
        "Transactions made outside the customer's country, and where:",
        "",
        *markdown_table(
            ["Customer country", "Card transactions", "Abroad"],
            (
                [str(g.values[0]), count(g.rows), share(g.hits or 0, g.rows)]
                for g in a.abroad.groups
            ),
        ),
        "",
        *markdown_table(
            ["`transaction_country`", "Card transactions abroad"],
            (
                [str(g.values[0]), share(g.rows, a.destinations.rows)]
                for g in a.destinations.groups
            ),
        ),
        "",
    ]


def _rates_table(rates: Sequence[Breakdown], hit: str) -> list[str]:
    return markdown_table(
        ["Field", "Value", "Card transactions", hit],
        (
            [
                f"`{b.by[0]}`",
                _code(g.values[0]),
                count(g.rows),
                share(g.hits or 0, g.rows),
            ]
            for b in rates
            for g in b.groups
        ),
    )


def _association_table(associations: Sequence[Association], rows: str) -> list[str]:
    return markdown_table(
        ["Field", "Values", rows, "Cramér's V"],
        (
            [f"`{a.field}`", str(a.field_values), count(a.rows), _v(a)]
            for a in associations
        ),
    )


def _windows_table(windows: Sequence[Window], what: str) -> list[str]:
    return markdown_table(
        ["Window", what, "Customers"],
        (
            [f"Last {w.days} days", count(w.transactions), count(w.customers)]
            for w in windows
        ),
    )


def _declines_lines(result: CardSupport) -> list[str]:
    d = result.declines
    statuses = sorted({str(g.values[0]) for g in d.codes_by_status.groups})
    codes = sorted(
        {g.values[1] for g in d.codes_by_status.groups},
        key=lambda c: (c is None, c or ""),
    )
    declined = sum(g.hits or 0 for g in d.monthly.groups)
    return [
        "## 3. Declines",
        "",
        f"{share(declined, d.monthly.rows)} card transactions are declined over the whole "
        "snapshot. Every declined share below is of card transactions; the codes are read "
        "with their ISO 8583 meanings, as ADR-0003's rule reads them.",
        "",
        _figure(
            "Declined share of card transactions by month, all and by code",
            DECLINES_FIGURE,
        ),
        "",
        *markdown_table(
            ["`transaction_status`", *(_code(c) for c in codes)],
            (
                [f"`{s}`", *(count(_rows(d.codes_by_status, s, c)) for c in codes)]
                for s in statuses
            ),
        ),
        "",
        *_rates_table(d.rates, "Declined"),
        "",
        "How much the code of a declined transaction depends on each field, as Cramér's V "
        "with Bergsma's bias correction (0 when independent, 1 when one determines the other; "
        f"a missing value counts as one of its own). By Cohen's convention, below {NEGLIGIBLE} "
        f"is negligible. For scale, the code against `transaction_status` over every card "
        f"transaction reads {_v(d.reference)}.",
        "",
        *_association_table(d.association, "Declines read"),
        "",
        "Declines with no code, or a code outside "
        + _join([_code_name(c) for c in DECLINE_REASONS])
        + ", which the rule can't explain:",
        "",
        *_windows_table(d.missing_code, "Declines"),
        "",
    ]


def _fraud_lines(result: CardSupport) -> list[str]:
    f = result.fraud
    return [
        "## 4. `is_fraud`",
        "",
        "Where the bank's flag falls, over the whole snapshot:",
        "",
        *_rates_table(f.rates, "Marked `is_fraud`"),
        "",
        "Cramér's V between `is_fraud` and each field, as in section 3. For scale, `is_fraud` "
        f"against `fraud_score` in bands of 10 (drawn from it) reads {_v(f.reference)}.",
        "",
        *_association_table(f.association, "Card transactions"),
        "",
        "Transactions marked `is_fraud` on active cards:",
        "",
        *_windows_table(f.windows, "Marked transactions"),
        "",
    ]


def _balances_lines(result: CardSupport) -> list[str]:
    c = result.balances.credit
    d = result.balances.debit
    return [
        "## 5. Balances",
        "",
        "Active credit cards. Utilization is `current_balance` over `credit_limit`, for "
        "cards with a limit above zero.",
        "",
        *markdown_table(
            ["Active credit cards", "Cards"],
            [
                ["All", count(c.active_cards)],
                ["With a limit", share(c.with_limit, c.active_cards)],
                [
                    "Without a limit",
                    share(c.active_cards - c.with_limit, c.active_cards),
                ],
                ["Balance above the limit", share(c.over_limit, c.active_cards)],
                ["Balance of zero", share(c.at_zero, c.active_cards)],
                ["Negative balance", share(c.negative, c.active_cards)],
            ],
        ),
        "",
        _figure("Utilization of active credit cards", UTILIZATION_FIGURE),
        "",
        *markdown_table(
            ["Percentile", *(_ordinal(q) for q in QUANTILES)],
            [
                [
                    "Utilization",
                    *(_quantile(c.utilization, q, _ratio) for q in QUANTILES),
                ]
            ],
        ),
        "",
        *markdown_table(
            ["Credit cards by `product_status`", "Cards", "Without a limit"],
            (
                [f"`{g.values[0]}`", count(g.rows), share(g.hits or 0, g.rows)]
                for g in c.missing_limit.groups
            ),
        ),
        "",
        *markdown_table(
            ["Active debit cards", "Cards"],
            [
                ["All", count(d.active_cards)],
                ["With a `credit_limit`", share(d.with_limit, d.active_cards)],
                ["Balance of zero", share(d.at_zero, d.active_cards)],
                ["Negative balance", share(d.negative, d.active_cards)],
                [
                    "Balance equal to one of the holder's accounts",
                    share(d.matching_an_account, d.active_cards),
                ],
            ],
        ),
        "",
        "`current_balance` on active cards, in each card's currency:",
        "",
        *markdown_table(
            [
                "Card type",
                "Currency",
                "Active cards",
                "10th percentile",
                "Median",
                "90th percentile",
            ],
            (
                [
                    f"`{b.product_type}`",
                    b.currency,
                    count(b.balances.rows),
                    *(_quantile(b.balances, q, _money) for q in (0.1, 0.5, 0.9)),
                ]
                for b in result.balances.by_currency
            ),
        ),
        "",
    ]


def _dates_lines(result: CardSupport) -> list[str]:
    d = result.dates
    last = d.last_transaction
    return [
        "## 6. Dates",
        "",
        "A card is past its expiration date when `expiration_date` is before the business "
        "date; a transaction is before opening or after expiration by its calendar date.",
        "",
        _figure("Date conflicts on cards and card transactions", DATES_FIGURE),
        "",
        *markdown_table(
            [
                "`product_status`",
                "Cards",
                "No `expiration_date`",
                "Past expiration",
                "Expires before opening",
                "Updated after the as-of instant",
            ],
            (
                [
                    f"`{c.product_status}`",
                    count(c.cards),
                    share(c.expiration_missing, c.cards),
                    share(c.expired, c.cards),
                    share(c.expires_before_opening, c.cards),
                    share(c.updated_after_as_of, c.cards),
                ]
                for c in d.cards
            ),
        ),
        "",
        "`last_transaction_date` against the card's latest transaction at the as-of instant:",
        "",
        *markdown_table(
            ["Comparison", "Cards"],
            [
                ["Equal", share(last.equal, last.cards)],
                ["Recorded earlier", share(last.earlier, last.cards)],
                ["Recorded later", share(last.later, last.cards)],
                [
                    "Recorded, but the card has no transaction",
                    share(last.recorded_without_transactions, last.cards),
                ],
                [
                    "Not recorded, but the card has transactions",
                    share(last.transactions_without_record, last.cards),
                ],
                ["Neither", share(last.neither, last.cards)],
            ],
        ),
        "",
        "Where both exist, they are "
        + _join(
            [
                f"{_quantile(last.gap_days, q, _plain)} days apart at the {label}"
                for q, label in (
                    (0.1, "10th percentile"),
                    (0.5, "median"),
                    (0.9, "90th"),
                )
            ]
        )
        + ".",
        "",
        *markdown_table(
            [
                "Card transactions",
                "All",
                "Before their card opened",
                "After their card expired",
            ],
            (
                [
                    "Whole snapshot" if t.days is None else f"Last {t.days} days",
                    count(t.transactions),
                    share(t.before_opening, t.transactions),
                    share(t.after_expiration, t.transactions),
                ]
                for t in d.transactions
            ),
        ),
        "",
    ]


def _range(values: tuple[float, ...] | None) -> str:
    if values is None:
        return "n/a"
    return " to ".join(_plain(v) for v in values)


def _currency_lines(result: CardSupport) -> list[str]:
    c = result.currency
    countries = sorted({str(g.values[0]) for g in c.cards.groups})
    currencies = sorted({str(g.values[1]) for g in c.cards.groups})
    matching = sum(g.rows for g in c.transactions.groups if g.values[0] == g.values[1])
    return [
        "## 7. Currency",
        "",
        *markdown_table(
            ["Customer country", *currencies],
            (
                [country, *(count(_rows(c.cards, country, cur)) for cur in currencies)]
                for country in countries
            ),
        ),
        "",
        f"{share(matching, c.transactions.rows)} card transactions are in their card's currency.",
        "",
        *markdown_table(
            [
                "`currency`",
                "Card transactions",
                "With `amount_usd`",
                "`amount` over `amount_usd`: 1st, 50th, 99th percentile",
                "Bank's daily rate from USD: lowest to highest",
            ],
            (
                [
                    x.currency,
                    count(x.transactions),
                    share(x.converted, x.transactions),
                    ", ".join(_plain(v) for v in x.ratio)
                    if x.ratio and x.converted >= SUPPRESS_BELOW
                    else "n/a",
                    _range(x.daily_rate),
                ]
                for x in c.conversions
            ),
        ),
        "",
    ]


def _pairs_table(pairs: Sequence[CoMissing]) -> list[str]:
    return markdown_table(
        [
            "Fields",
            "Rows",
            "First missing",
            "Second missing",
            "Both missing",
            "Expected if independent",
            "Ratio",
        ],
        (
            [
                f"`{p.first}`, `{p.second}`",
                count(p.rows),
                count(p.first_missing),
                count(p.second_missing),
                count(p.both_missing),
                _plain(p.expected) if p.both_missing >= SUPPRESS_BELOW else "n/a",
                f"{p.both_missing / p.expected:.2f}"
                if p.expected and p.both_missing >= SUPPRESS_BELOW
                else "n/a",
            ]
            for p in pairs
        ),
    )


def _missing_lines(result: CardSupport) -> list[str]:
    m = result.missing
    return [
        "## 8. Missing values",
        "",
        "Each field measured in the rows that should carry it: `credit_limit` and "
        "`days_past_due` on credit cards, `merchant_name` and `merchant_category` on purchases, "
        "`transaction_category` on everything but withdrawals, and `amount_usd` outside USD.",
        "",
        *markdown_table(
            ["Field", "Rows read", "Populated", "Share"],
            (
                [
                    f"`{f.table}.{f.name}`",
                    count(f.rows),
                    count(f.populated),
                    f"{f.share:.2%}" if f.share is not None else "no rows",
                ]
                for f in m.fields
            ),
        ),
        "",
        "Whether one gap makes another likelier: rows missing both fields against the count "
        "expected if gaps fell independently. A ratio near 1 means they do.",
        "",
        "Card purchases:",
        "",
        *_pairs_table(m.purchase_pairs),
        "",
        "Credit cards:",
        "",
        *_pairs_table(m.credit_card_pairs),
        "",
    ]


def _context_lines(result: CardSupport) -> list[str]:
    complaints = result.context.complaints
    contacts = result.context.contacts
    periods = [p for p in PERIODS if any(g.values[0] == p for g in complaints.groups)]
    categories = sorted({str(g.values[1]) for g in complaints.groups})
    reasons = sorted({str(g.values[1]) for g in contacts.groups})
    cells = _cells(complaints)

    def complaint_cell(period: str, category: str) -> str:
        g = cells.get((period, category))
        if g is None:
            return "0"
        return f"{count(g.rows)} ({count(g.hits or 0)} on a card)"

    return [
        "## 9. Context",
        "",
        "Complaints and contacts by development customers, dated by their own timestamps, in "
        "12-month periods ending at the as-of instant. Context for demand patterns (PRB-02), "
        "not attribution: contacts can't be tied to a workflow (see the "
        "[profile](profiling.md#contact-attribution)), and a complaint names a card only "
        "through `affected_product_id`.",
        "",
        *markdown_table(
            ["Complaint category", *(PERIODS[p] for p in periods)],
            ([c, *(complaint_cell(p, c) for p in periods)] for c in categories),
        ),
        "",
        *markdown_table(
            ["Contact reason", *(PERIODS[p] for p in periods)],
            ([r, *(count(_rows(contacts, p, r)) for p in periods)] for r in reasons),
        ),
        "",
    ]


def to_markdown(result: CardSupport) -> str:
    registered = result.customers + result.held_out
    lines = [
        "# Card support",
        "",
        f"Snapshot `{result.snapshot_id}`, as of **{result.as_of.isoformat(sep=' ')}** "
        f"(business date {result.business_date}), computed by `make analysis` with DuckDB "
        f"{result.duckdb_version}. It feeds the card support policy and the evaluation ADR, "
        "which cite it.",
        "",
        f"It reads development customers only. The held-out fifth of [ADR-0003]({ADR}), "
        f"customers whose `customer_id` has an MD5 divisible by {HELD_OUT_EVERY} "
        f"({count(result.held_out)} of the {count(registered)} registered by the as-of "
        "instant), is set aside before anything is read, so the policy and its personas never "
        f"see them (DML-09); {count(result.customers)} customers remain. A window such as "
        '"the last 30 days" is 30 × 24 hours ending at the as-of instant, counted by each '
        f"event's own timestamp. Row counts from 1 to {SUPPRESS_BELOW - 1} appear as "
        f"`<{SUPPRESS_BELOW}` (SEC-03); [card-support.json](card-support.json) holds the same "
        "numbers for code.",
        "",
        "## Findings for the policy",
        "",
        *_findings(result),
        "",
        *_holders_lines(result),
        *_activity_lines(result),
        *_declines_lines(result),
        *_fraud_lines(result),
        *_balances_lines(result),
        *_dates_lines(result),
        *_currency_lines(result),
        *_missing_lines(result),
        *_context_lines(result),
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def write(result: CardSupport, out: Path) -> tuple[Path, ...]:
    out.mkdir(parents=True, exist_ok=True)
    markdown = out / "card-support.md"
    data = out / "card-support.json"
    markdown.write_text(to_markdown(result))
    data.write_text(to_json(result))
    figures = out / FIGURES
    return (
        markdown,
        data,
        save(cards_figures.daily_volume(result), figures / DAILY_FIGURE, 7, 4.2),
        save(
            cards_figures.declines_by_month(result), figures / DECLINES_FIGURE, 7, 4.2
        ),
        save(cards_figures.utilization(result), figures / UTILIZATION_FIGURE, 7, 3.2),
        save(cards_figures.date_conflicts(result), figures / DATES_FIGURE, 7, 3.4),
    )

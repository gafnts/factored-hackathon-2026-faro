"""
Figures for the traffic report, drawn from aggregates only: a value resting on 1 to 9 rows isn't drawn (SEC-03).
"""

from collections.abc import Sequence
from datetime import date

import polars as pl
from plotnine import (
    aes,
    expand_limits,
    facet_wrap,
    geom_hline,
    geom_line,
    geom_point,
    geom_vline,
    ggplot,
    labs,
    scale_color_manual,
    scale_shape_manual,
    scale_x_continuous,
    scale_x_date,
    scale_x_log10,
    scale_y_continuous,
    theme,
)

from faro.analysis.capacity import Projection
from faro.analysis.plotting import MUTED, SERIES, base_theme, percent
from faro.analysis.report import SUPPRESS_BELOW
from faro.analysis.traffic import HOURS, Traffic

NAMES = {
    "contacts": "Contacts",
    "digital_sessions": "Digital sessions",
    "digital_events": "Digital events",
    "card_transactions": "Card transactions",
    "complaints": "Complaints",
}
SCENARIOS = {
    "chat": "Chat contacts",
    "transactional": "Transaccional contacts",
    "all": "Every contact",
}
PROFILES = {"flat": "Flat day", "business_hours": "Business hours"}
COUNTRY_COLORS = ("#2a78d6", "#eb6834", "#3a9a5b")
SHORT_LIMITS = {
    "runtime_sessions": "Runtime: active sessions",
    "runtime_new_sessions": "Runtime: new sessions per second",
    "runtime_requests": "Runtime: requests per second",
    "gateway_tool_calls": "Gateway: tool calls per second",
    "lambda_concurrency": "Lambda: concurrency",
    "dynamodb_reads": "DynamoDB: reads per table",
    "dynamodb_writes": "DynamoDB: writes per table",
    "bedrock_requests": "Bedrock Haiku 4.5: requests",
    "bedrock_tokens": "Bedrock Haiku 4.5: tokens",
    "bedrock_requests_applied": "Bedrock Haiku 4.5: requests, our account",
    "anthropic_requests": "Claude API Haiku 4.5: requests",
    "anthropic_input": "Claude API Haiku 4.5: input tokens",
    "anthropic_output": "Claude API Haiku 4.5: output tokens",
    "openai_requests": "OpenAI gpt-5.4-mini: requests",
    "openai_tokens": "OpenAI gpt-5.4-mini: tokens",
}


def _drawn(n: int) -> bool:
    return not 0 < n < SUPPRESS_BELOW


def _thousands(values: Sequence[float] | Sequence[str]) -> list[str]:
    return [f"{float(v):,.0f}" for v in values]


def _times(values: Sequence[float] | Sequence[str]) -> list[str]:
    return [f"{float(v):,.0f}×" if float(v) >= 1 else f"{float(v):g}×" for v in values]


def daily(result: Traffic) -> ggplot:
    rows = [
        (
            NAMES[s.name],
            date.fromisoformat(str(g.values[0])),
            float(g.rows) if _drawn(g.rows) else None,
        )
        for s in result.streams
        for g in s.daily.groups
    ]
    data = pl.DataFrame(
        rows,
        schema={"table": pl.String, "day": pl.Date, "rows": pl.Float64},
        orient="row",
    ).with_columns(
        pl.col("table").cast(pl.Enum([NAMES[s.name] for s in result.streams]))
    )
    plot: ggplot = (
        ggplot(data, aes("day", "rows"))
        + geom_line(color=SERIES[0], size=0.25)
        + facet_wrap("table", ncol=1, scales="free_y")
        + scale_x_date(date_labels="%b %Y")
        + scale_y_continuous(labels=_thousands)
        + expand_limits(y=0)
        + labs(x="", y="Rows per day")
        + base_theme()
    )
    return plot


def hourly(result: Traffic) -> ggplot:
    rows = []
    for s in result.streams:
        totals: dict[str, int] = {}
        for g in s.hour_of_day.groups:
            totals[str(g.values[1])] = totals.get(str(g.values[1]), 0) + g.rows
        for g in s.hour_of_day.groups:
            country = str(g.values[1])
            drawn = _drawn(g.rows) and totals[country]
            rows.append(
                (
                    NAMES[s.name],
                    country,
                    int(str(g.values[0])),
                    g.rows / totals[country] if drawn else None,
                )
            )
    countries = sorted({r[1] for r in rows})
    data = pl.DataFrame(
        rows,
        schema={
            "table": pl.String,
            "country": pl.String,
            "hour": pl.Int64,
            "share": pl.Float64,
        },
        orient="row",
    ).with_columns(
        pl.col("table").cast(pl.Enum([NAMES[s.name] for s in result.streams]))
    )
    plot: ggplot = (
        ggplot(data, aes("hour", "share", color="country"))
        + geom_hline(yintercept=1 / HOURS, color=MUTED, linetype="dashed", size=0.3)
        + geom_line(size=0.4)
        + facet_wrap("table", ncol=2)
        + scale_color_manual(values=list(COUNTRY_COLORS[: len(countries)]))
        + scale_x_continuous(breaks=[0, 6, 12, 18, 23])
        + scale_y_continuous(labels=percent)
        + expand_limits(y=0)
        + labs(x="Hour of day, bank's clock", y="Share of the country's rows")
        + base_theme()
    )
    return plot


def capacity(projection: Projection) -> ggplot:
    rows = [
        (
            SCENARIOS[h.scenario],
            PROFILES[h.profile],
            SHORT_LIMITS.get(h.limit, h.limit),
            h.multiple,
        )
        for h in projection.headroom
        if h.multiple is not None
    ]
    reach = {
        name: min(r[3] or 0.0 for r in rows if r[2] == name)
        for name in {r[2] for r in rows}
    }
    data = pl.DataFrame(
        rows,
        schema={
            "scenario": pl.String,
            "profile": pl.String,
            "limit": pl.String,
            "multiple": pl.Float64,
        },
        orient="row",
    ).with_columns(
        pl.col("scenario").cast(pl.Enum(list(SCENARIOS.values()))),
        pl.col("profile").cast(pl.Enum(list(PROFILES.values()))),
        pl.col("limit").cast(
            pl.Enum(sorted(reach, key=lambda name: (-reach[name], name)))
        ),
    )
    plot: ggplot = (
        ggplot(data, aes("multiple", "limit", color="scenario", shape="profile"))
        + geom_vline(
            xintercept=list(projection.scales), color=MUTED, linetype="dashed", size=0.3
        )
        + geom_point(size=1.8, fill="none")
        + scale_color_manual(values=list(COUNTRY_COLORS))
        + scale_shape_manual(values=["o", "^"])
        + scale_x_log10(breaks=[10**k for k in range(7)], labels=_times)
        + labs(
            x="Multiple of this bank's traffic at which the limit is reached (projection)",
            y="",
        )
        + base_theme()
        + theme(legend_position="bottom", legend_box="vertical")
    )
    return plot

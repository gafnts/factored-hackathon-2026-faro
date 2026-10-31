"""
Writes the traffic analysis and its capacity projection as Markdown for people, JSON for code, and SVG figures,
with small row counts suppressed (SEC-03) and the projection kept apart from the measurements (EVL-13).
"""

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from faro.analysis import traffic_figures
from faro.analysis.capacity import (
    SEARCH,
    Headroom,
    Limit,
    Load,
    Projection,
)
from faro.analysis.cards import YEAR
from faro.analysis.cards_report import WEEKDAYS
from faro.analysis.plotting import save
from faro.analysis.report import (
    SUPPRESS_BELOW,
    count,
    markdown_table,
    share,
    suppress,
)
from faro.analysis.traffic import (
    CARD_TRANSACTIONS,
    COMPLAINTS,
    CONTACTS,
    DIGITAL_EVENTS,
    DIGITAL_SESSIONS,
    DIP,
    QUANTILES,
    SPIKE,
    SPIKE_WEEKS,
    Distribution,
    Stream,
    Timing,
    Traffic,
    Volume,
)
from faro.split import HELD_OUT_EVERY

ADR_0003 = "../adr/0003-choose-workflow-from-evidence.md"
ADR_0004 = "../adr/0004-agent-architecture-on-agentcore.md"
PROFILE_REPORT = "profiling.md"
FIGURES = "figures"
DAILY_FIGURE = "traffic-daily.svg"
HOURLY_FIGURE = "traffic-hourly.svg"
CAPACITY_FIGURE = "traffic-capacity.svg"
# The profile's count of digital events with no customer, over every customer; the analysis never reads them.
PROFILED_ANONYMOUS_EVENTS = 3_745_446
STREAM_NAMES = {
    CONTACTS: "Contacts",
    DIGITAL_SESSIONS: "Digital sessions",
    DIGITAL_EVENTS: "Digital events",
    CARD_TRANSACTIONS: "Card transactions",
    COMPLAINTS: "Complaints",
}
GROUP_NAMES = {
    "channel": "`channel`",
    "interaction_type": "`interaction_type`",
    "reason_category": "`reason_category`",
    "country": "Customer country",
}
SCENARIO_NAMES = {
    "chat": "Chat contacts",
    "transactional": "`Transaccional` contacts",
    "all": "Every contact",
}
PROFILE_NAMES = {"flat": "Flat day", "business_hours": "Business hours"}

ROW_COUNTS = frozenset(
    {
        "customers",
        "held_out",
        "rows",
        "hits",
        "fewest",
        "most",
        "busiest_day",
        "busiest_hour",
        "contacts",
        "named",
        "attributed",
        "sessions",
        "sessions_naming_others",
    }
)
# Statistics of row counts that could give an individual count away below the threshold.
_COUNT_STATISTICS = frozenset({"median", "p95", "p99"})


def _hide_small(value: object) -> object:
    if isinstance(value, dict):
        hidden = {k: _hide_small(v) for k, v in value.items()}
        for key in _COUNT_STATISTICS & hidden.keys():
            v = hidden[key]
            if isinstance(v, float) and 0 < v < SUPPRESS_BELOW:
                hidden[key] = f"<{SUPPRESS_BELOW}"
        rows = hidden.get("rows")
        if (
            "quantiles" in hidden
            and isinstance(rows, int)
            and 0 < rows < SUPPRESS_BELOW
        ):
            hidden["quantiles"] = None
            hidden["mean"] = None
        return hidden
    if isinstance(value, list | tuple):
        return [_hide_small(v) for v in value]
    return value


def _daily(stream: Stream) -> dict[str, object]:
    # One count per day from the first, rather than an object per day, keeps the file small enough to commit.
    groups = stream.daily.groups
    return {
        "first": str(groups[0].values[0]) if groups else None,
        "rows": [g.rows for g in groups],
    }


def to_json(result: Traffic, projection: Projection) -> str:
    streams = [{**asdict(s), "daily": _daily(s)} for s in result.streams]
    measurements = {
        **asdict(result),
        "streams": streams,
        "as_of": result.as_of.isoformat(sep=" "),
        "bank_factor": result.bank_factor,
        "settings": {
            "held_out_every": HELD_OUT_EVERY,
            "year_days": YEAR,
            "spike": SPIKE,
            "dip": DIP,
            "spike_weeks": SPIKE_WEEKS,
            "quantiles": list(QUANTILES),
        },
    }
    data = {
        "measurements": suppress(_hide_small(measurements), ROW_COUNTS),
        "projection": {
            "note": "A projection from the measurements and the assumptions it lists, never a measurement "
            "(EVL-13).",
            **asdict(projection),
            "checked": projection.checked.isoformat(),
        },
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _plain(value: float) -> str:
    return f"{value:,.1f}".removesuffix(".0")


def _whole(value: float) -> str:
    return f"{value:,.0f}"


def _rate(value: float) -> str:
    return f"{value:.2g}" if value < 10 else _plain(value)


def _statistic(value: float) -> str:
    return f"<{SUPPRESS_BELOW}" if 0 < value < SUPPRESS_BELOW else _plain(value)


def _clock(seconds: float) -> str:
    whole = round(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def _ratio(value: float) -> str:
    return f"{value:.2f}"


def _over(value: float, base: float) -> str:
    return _ratio(value / base) if base else "n/a"


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _percent(value: float) -> str:
    return f"{value:.1%}"


def _multiple(value: float | None) -> str:
    if value is None:
        return f"beyond {SEARCH[1]:,.0f}×"
    rounded = float(f"{value:.2g}")
    return f"{rounded:,.1f}×".replace(".0×", "×")


def _join(items: Sequence[str]) -> str:
    if len(items) < 3:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _figure(alt: str, name: str) -> str:
    return f"![{alt}]({FIGURES}/{name})"


def _volumes(result: Traffic, group: str) -> tuple[Volume, ...]:
    return next(v for g, v in result.contacts.volumes if g == group)


def _timings(result: Traffic, group: str) -> tuple[Timing, ...]:
    return next(t for g, t in result.contacts.timings if g == group)


def _period_means(s: Stream) -> list[str]:
    return [_plain(p.rows / p.days) for p in s.periods if p.days]


def _shape(s: Stream, country: str | None) -> tuple[int, int, float | None]:
    shape = next(x for x in s.shapes if x.country == country)
    return shape.fewest, shape.most, shape.dispersion


def _findings(result: Traffic) -> list[str]:
    contacts = result.stream(CONTACTS)
    sessions = result.stream(DIGITAL_SESSIONS)
    fewest, most, dispersion = _shape(contacts, None)
    others = [
        STREAM_NAMES[s.name].lower()
        for s in result.streams
        if s.name not in (CONTACTS, DIGITAL_EVENTS)
    ]
    days = {int(str(g.values[0])): g.rows for g in contacts.weekday.groups}
    shares = [days.get(d, 0) / (contacts.weekday.rows or 1) for d in range(1, 8)]
    weekdays, weekend = shares[:5], shares[5:]
    overall = result.contacts.overall
    no_timing = [
        str(t.value)
        for t in _timings(result, "channel")
        if t.handle.rows == 0 and t.wait.rows == 0
    ]
    return [
        f"- **No table has a daily cycle.** Over the last {YEAR} days, the busiest hour of the day holds "
        f"{count(most)} contacts and the quietest {count(fewest)}, and the variance of the 24 hourly counts "
        f"is {_ratio(dispersion or 0)} times their mean, where rows placed on the hours at random would "
        f"give about 1. Each country and the {_join(others)} look the same (section 3). The synthetic "
        "data holds no peak hour, and the projection's business-hours profile is an assumption, not a "
        "finding.",
        f"- **Volume is flat across the three years.** Contacts ran {_join(_period_means(contacts))} a "
        "day in the last 12 months and the two years before them, and card transactions "
        f"{_join(_period_means(result.stream(CARD_TRANSACTIONS)))} (section 1).",
        f"- **The week has a shape and the day doesn't.** Each weekday carries "
        f"{_percent(min(weekdays))} to {_percent(max(weekdays))} of the week's contacts, and Saturday "
        f"and Sunday {_percent(min(weekend))} to {_percent(max(weekend))} (section 2).",
        f"- **The busiest hour is chance on a flat day.** In the last {YEAR} days, contacts peaked at "
        f"{count(contacts.hourly.most)} in one clock hour, against a mean of "
        f"{_plain(contacts.hourly.mean)} and a 95th percentile of {_statistic(contacts.hourly.p95)}: "
        f"{_over(contacts.hourly.most, contacts.hourly.mean)} times the mean. The busiest day held "
        f"{count(contacts.year.most)} contacts, {_over(contacts.year.most, contacts.year.mean)} times "
        "the mean day (section 4).",
        f"- **Contacts never spike; digital sessions do.** No day's contacts exceed {SPIKE} times the "
        f"median of the same weekday over the {SPIKE_WEEKS} weeks before it (the highest is "
        f"{_ratio(contacts.spikes.highest or 0)}). Digital sessions do on {sessions.spikes.above:,} of "
        f"{sessions.spikes.days:,} days, up to {_ratio(sessions.spikes.highest or 0)} times, and their "
        f"busiest day is {_over(sessions.year.most, sessions.year.mean)} times their mean day "
        "(section 5).",
        f"- **A contact takes {_clock(overall.handle.mean or 0)} on average** ({_clock(_q(overall.handle, 0.5))} "
        f"median, {_clock(_q(overall.handle, 0.9))} at the 90th percentile), and a caller waits "
        f"{_clock(overall.wait.mean or 0)}. {_join(no_timing)} contacts record neither handle nor wait "
        "time, so the projection gives chats the handle time of all contacts (section 7).",
        f"- **Digital events without a customer count only through their session.** "
        f"{count(result.digital.attributed)} are attributed to the development customer their session "
        f"names; {count(result.digital.sessions_naming_others)} sessions name anyone else. Sessions that "
        "name no customer can't be placed on either side of the split and aren't read (section 8).",
    ]


def _q(d: Distribution, q: float) -> float:
    return 0.0 if d.quantiles is None else d.quantiles[QUANTILES.index(q)]


def _daily_lines(result: Traffic) -> list[str]:
    rows = []
    trend = []
    for s in result.streams:
        y = s.year
        rows.append(
            [
                STREAM_NAMES[s.name],
                count(y.rows),
                _plain(y.mean),
                _statistic(y.median),
                _statistic(y.p95),
                count(y.fewest),
                count(y.most),
            ]
        )
        trend.append(
            [STREAM_NAMES[s.name], *(_plain(p.rows / p.days) for p in s.periods)]
        )
    periods = result.streams[0].periods
    return [
        "## 1. Per day",
        "",
        f"A day is the 24 hours ending at the as-of time of day, as the other reports count them. The "
        f"table reads the last {YEAR} days.",
        "",
        _figure("Rows per day, by table", DAILY_FIGURE),
        "",
        *markdown_table(
            [
                "Table",
                "Rows",
                "Mean per day",
                "Median",
                "95th percentile",
                "Fewest",
                "Most",
            ],
            rows,
        ),
        "",
        "Mean rows per day, by period:",
        "",
        *markdown_table(["Table", *(f"{p.first} to {p.last}" for p in periods)], trend),
        "",
    ]


def _weekday_lines(result: Traffic) -> list[str]:
    header = ["Weekday", *(STREAM_NAMES[s.name] for s in result.streams)]
    rows = []
    for day in range(1, 8):
        row = [WEEKDAYS[day - 1]]
        for s in result.streams:
            n = next(
                (g.rows for g in s.weekday.groups if int(str(g.values[0])) == day), 0
            )
            row.append(share(n, s.weekday.rows))
        rows.append(row)
    return [
        "## 2. By weekday",
        "",
        f"Rows in the last {YEAR} days, by the weekday of their day:",
        "",
        *markdown_table(header, rows),
        "",
    ]


def _hour_lines(result: Traffic) -> list[str]:
    rows = []
    for s in result.streams:
        for shape in s.shapes:
            rows.append(
                [
                    STREAM_NAMES[s.name],
                    shape.country or "All",
                    count(shape.rows),
                    count(shape.fewest),
                    count(shape.most),
                    "n/a" if shape.dispersion is None else _ratio(shape.dispersion),
                ]
            )
    return [
        "## 3. By hour of day",
        "",
        f"Rows in the last {YEAR} days, by the hour of day of their own timestamp, in the bank's clock "
        f"(the timestamps carry no time zone; the [profile]({PROFILE_REPORT}) finds that each table's "
        "processing day ends at a fixed hour in every country, 06:00 or 08:00, so the hour isn't local "
        "time either). Dispersion is the variance of the 24 hourly counts over their mean: about 1 when "
        "rows fall on the hours at random, and far above 1 when the day has a shape. Digital events "
        "cluster within sessions, which raises their dispersion without a daily cycle; digital sessions "
        "don't.",
        "",
        _figure("Share of rows by hour of day, by country", HOURLY_FIGURE),
        "",
        *markdown_table(
            ["Table", "Country", "Rows", "Quietest hour", "Busiest hour", "Dispersion"],
            rows,
        ),
        "",
    ]


def _peak_lines(result: Traffic) -> list[str]:
    rows = [
        [
            STREAM_NAMES[s.name],
            count(s.hourly.hours),
            _plain(s.hourly.mean),
            _statistic(s.hourly.p95),
            _statistic(s.hourly.p99),
            count(s.hourly.most),
            _over(s.hourly.most, s.hourly.mean),
        ]
        for s in result.streams
    ]
    return [
        "## 4. Peak hours",
        "",
        f"Rows per clock hour over the last {YEAR} days, hours without rows included:",
        "",
        *markdown_table(
            [
                "Table",
                "Hours",
                "Mean",
                "95th percentile",
                "99th percentile",
                "Most",
                "Most over mean",
            ],
            rows,
        ),
        "",
    ]


def _spike_lines(result: Traffic) -> list[str]:
    rows = [
        [
            STREAM_NAMES[s.name],
            f"{s.spikes.days:,}",
            f"{s.spikes.above:,}",
            f"{s.spikes.below:,}",
            "n/a" if s.spikes.highest is None else _ratio(s.spikes.highest),
            "n/a" if s.spikes.lowest is None else _ratio(s.spikes.lowest),
        ]
        for s in result.streams
    ]
    return [
        "## 5. Spikes",
        "",
        f"Each day against the median of the same weekday over the {SPIKE_WEEKS} weeks before it, over "
        f"the whole snapshot (the first {SPIKE_WEEKS} weeks have no such median). A spike is a day above "
        f"{SPIKE} times it, a dip a day below {DIP} times it. Comparing with the same weekday keeps an "
        "ordinary weekend from reading as a dip.",
        "",
        *markdown_table(
            [
                "Table",
                "Days compared",
                "Spikes",
                "Dips",
                "Highest ratio",
                "Lowest ratio",
            ],
            rows,
        ),
        "",
    ]


def _contact_lines(result: Traffic) -> list[str]:
    lines = [
        "## 6. Contacts by channel, type, reason, and country",
        "",
        f"{count(result.contacts.rows)} contacts in the last {YEAR} days. The busiest day and hour are "
        "each group's own.",
        "",
    ]
    for group, name in GROUP_NAMES.items():
        lines += [
            *markdown_table(
                [name, "Contacts", "Mean per day", "Busiest day", "Busiest hour"],
                (
                    [
                        f"`{v.value}`" if group != "country" else str(v.value),
                        share(v.rows, result.contacts.rows),
                        _plain(v.rows / YEAR),
                        count(v.busiest_day),
                        count(v.busiest_hour),
                    ]
                    for v in _volumes(result, group)
                ),
            ),
            "",
        ]
    return lines


def _distribution_cells(d: Distribution) -> list[str]:
    if d.rows == 0 or d.mean is None or 0 < d.rows < SUPPRESS_BELOW:
        return [count(d.rows), "n/a", "n/a", "n/a", "n/a"]
    return [count(d.rows), _clock(d.mean), *(_clock(_q(d, q)) for q in QUANTILES)]


def _timing_lines(result: Traffic) -> list[str]:
    header = [
        "Contacts",
        "Recorded",
        "Mean",
        "Median",
        "90th percentile",
        "99th percentile",
    ]
    lines = [
        "## 7. Handle and wait times",
        "",
        f"`duration_seconds` (handle time) and `wait_time_seconds` in the last {YEAR} days, as minutes "
        "and seconds, among contacts that record them. With arrivals per second and the mean handle "
        "time, Little's law gives the conversations under way at once, which the projection uses.",
        "",
    ]
    for group in ("channel", "reason_category"):
        timings = (*_timings(result, group), result.contacts.overall)
        for measure, name in (("handle", "Handle time"), ("wait", "Wait time")):
            lines += [
                f"{name} by {GROUP_NAMES[group]}:",
                "",
                *markdown_table(
                    [GROUP_NAMES[group], *header],
                    (
                        [
                            "All" if t.value is None else f"`{t.value}`",
                            count(t.contacts),
                            *_distribution_cells(getattr(t, measure)),
                        ]
                        for t in timings
                    ),
                ),
                "",
            ]
    return lines


def _digital_lines(result: Traffic) -> list[str]:
    d = result.digital
    return [
        "## 8. Digital sessions",
        "",
        f"A digital event without a `customer_id` counts when its session names a development customer, "
        f"and is attributed to that customer; a session that names no one can't be placed on either "
        f"side of the split, so its events aren't read. The [profile]({PROFILE_REPORT}) counts "
        f"{count(PROFILED_ANONYMOUS_EVENTS)} events without a customer across every customer, which "
        "bounds what is left out.",
        "",
        *markdown_table(
            ["Measure", "Value"],
            [
                ["Events naming a development customer", count(d.named)],
                [
                    "Events without a customer, attributed through their session",
                    count(d.attributed),
                ],
                ["Sessions", count(d.sessions)],
                [
                    "Sessions naming a development customer and anyone else",
                    count(d.sessions_naming_others),
                ],
            ],
        ),
        "",
        f"Sessions that start in the last {YEAR} days, dated by their first event:",
        "",
        *markdown_table(
            [
                "Measure",
                "Sessions",
                "Mean",
                "Median",
                "90th percentile",
                "99th percentile",
            ],
            [
                [
                    "Events per session",
                    count(d.events_per_session.rows),
                    *(
                        _plain(v)
                        for v in (
                            d.events_per_session.mean or 0,
                            *(_q(d.events_per_session, q) for q in QUANTILES),
                        )
                    ),
                ],
                [
                    "Length, first to last event",
                    count(d.session_seconds.rows),
                    *(
                        _clock(v)
                        for v in (
                            d.session_seconds.mean or 0,
                            *(_q(d.session_seconds, q) for q in QUANTILES),
                        )
                    ),
                ],
            ],
        ),
        "",
    ]


def _loads(projection: Projection, profile: str) -> list[Load]:
    return [x for x in projection.loads if x.profile == profile]


def _load_tables(projection: Projection) -> list[str]:
    service_rows = []
    model_rows = []
    for x in projection.loads:
        label = [SCENARIO_NAMES[x.scenario], PROFILE_NAMES[x.profile], f"{x.scale:g}×"]
        service_rows.append(
            [
                *label,
                _whole(x.busiest_day),
                _plain(x.arrivals_per_hour),
                f"{x.active:,}",
                f"{x.sessions:,}",
                f"{x.new_sessions_per_second:,}",
                _rate(x.runtime_requests_per_second),
                _rate(x.tool_calls_per_second),
                _rate(x.lambda_concurrency),
                _rate(x.reads_per_second),
                _rate(x.writes_per_second),
            ]
        )
        model_rows.append(
            [
                *label,
                _whole(x.model_requests_per_minute),
                _whole(x.input_tokens_per_minute),
                _whole(x.output_tokens_per_minute),
                _whole(x.cold_starts_per_hour),
            ]
        )
    return [
        "Conversations, runtime sessions, tools, and tables at the peak:",
        "",
        *markdown_table(
            [
                "Scenario",
                "Profile",
                "Scale",
                "Busiest day's conversations",
                "Arrivals per hour",
                "Conversations under way",
                "Runtime sessions",
                "New sessions per second",
                "Runtime requests per second",
                "Tool calls per second",
                "Lambda concurrency",
                "DynamoDB reads per second",
                "DynamoDB writes per second",
            ],
            service_rows,
        ),
        "",
        "Model calls at the peak, with every call on one model, and cold starts in the peak hour:",
        "",
        *markdown_table(
            [
                "Scenario",
                "Profile",
                "Scale",
                "Model requests per minute",
                "Input tokens per minute",
                "Output tokens per minute",
                "Cold starts per hour",
            ],
            model_rows,
        ),
        "",
    ]


def _limit_name(limit: Limit) -> str:
    return f"{limit.service}: [{limit.name}]({limit.source})"


def _headroom(
    projection: Projection, scenario: str, profile: str, key: str
) -> Headroom:
    return next(
        h
        for h in projection.headroom
        if h.scenario == scenario and h.profile == profile and h.limit == key
    )


def _first(
    projection: Projection, scenario: str, profile: str, documented: bool
) -> Headroom:
    applied = {"bedrock_requests_applied"}
    candidates = [
        h
        for h in projection.headroom
        if h.scenario == scenario
        and h.profile == profile
        and h.multiple is not None
        and (h.limit not in applied or not documented)
    ]
    return min(candidates, key=lambda h: h.multiple or 0.0)


def _limit_lines(projection: Projection) -> list[str]:
    limits = {x.key: x for x in projection.limits}
    scenarios = [s.key for s in projection.scenarios]
    rows = [
        [
            _limit_name(limit),
            _whole(limit.value),
            *(
                " / ".join(
                    _multiple(_headroom(projection, s, p.key, limit.key).multiple)
                    for p in projection.profiles
                )
                for s in scenarios
            ),
        ]
        for limit in projection.limits
    ]
    first_rows = []
    for s in scenarios:
        for p in projection.profiles:
            first = _first(projection, s, p.key, documented=False)
            documented = _first(projection, s, p.key, documented=True)
            first_rows.append(
                [
                    SCENARIO_NAMES[s],
                    PROFILE_NAMES[p.key],
                    f"{limits[first.limit].service}: {limits[first.limit].name}, at "
                    f"{_multiple(first.multiple)}",
                    f"{limits[documented.limit].service}: {limits[documented.limit].name}, at "
                    f"{_multiple(documented.multiple)}",
                ]
            )
    notes = [f"- {limits[k].name}: {limits[k].note}." for k in limits if limits[k].note]
    unpublished = [
        f"- {service}: [{text}]({source})."
        for service, text, source in projection.unpublished
    ]
    return [
        "### Limits",
        "",
        f"Each limit as its source documents it on {projection.checked}, and the multiple of this bank's "
        "traffic at which the projection reaches it, flat day / business hours. The model limits assume "
        "every call goes to that model; the grid in ADR-0004 keeps one provider per conversation, so a "
        "mix would spread the load. Every AWS limit here is adjustable.",
        "",
        *markdown_table(
            ["Limit", "Value", *(SCENARIO_NAMES[s] for s in scenarios)], rows
        ),
        "",
        *notes,
        *unpublished,
        "",
        "### The first limit each scenario reaches",
        "",
        *markdown_table(
            ["Scenario", "Profile", "First limit", "First documented default"],
            first_rows,
        ),
        "",
    ]


def _cost_lines(projection: Projection) -> list[str]:
    models = [p.model for p in projection.prices]
    rows = [
        [
            SCENARIO_NAMES[x.scenario],
            f"{x.scale:g}×",
            _whole(x.conversations_per_day),
            *(_money(c.per_day) for c in x.costs),
        ]
        for x in _loads(projection, projection.profiles[0].key)
    ]
    per_conversation = projection.loads[0].costs
    prices = [
        f"- {p.model}: [{_money(p.input)} in and {_money(p.output)} out]({p.source}) per million "
        f"tokens{'; ' + p.note if p.note else ''}."
        for p in projection.prices
    ]
    return [
        "### Model cost per day",
        "",
        "At list price, on the mean day (not the busiest), with every call on one model and no prompt "
        "caching; AgentCore, Lambda, and DynamoDB charges aren't included. A conversation costs "
        + _join([f"{c.model} {_money(c.per_conversation)}" for c in per_conversation])
        + " USD.",
        "",
        *prices,
        "",
        *markdown_table(
            [
                "Scenario",
                "Scale",
                "Conversations per day",
                *(f"{m}, USD" for m in models),
            ],
            rows,
        ),
        "",
    ]


def _projection_lines(result: Traffic, projection: Projection) -> list[str]:
    bank = projection.bank_factor
    assumptions = [
        [a.unit[0].upper() + a.unit[1:], _plain(a.value), a.basis]
        for a in projection.assumptions
    ]
    scenarios = [
        [
            SCENARIO_NAMES[s.key],
            _percent(s.share),
            _clock(s.conversation_seconds),
            s.basis,
        ]
        for s in projection.scenarios
    ]
    profiles = [
        [PROFILE_NAMES[p.key], str(p.hours), p.basis] for p in projection.profiles
    ]
    return [
        "## 9. Projection: the agent's load and the limits it meets",
        "",
        "> [!IMPORTANT]",
        "> **This section is a projection, not a measurement (EVL-13).** Nothing in the snapshot says what "
        "share of contacts would reach the agent, how long its conversations last, or how many calls a turn "
        "makes. The numbers below apply the assumptions listed here to the contacts measured above; they "
        "size the architecture (OPS-08), and predict no bank's traffic.",
        "",
        "### How it's computed",
        "",
        f"- **This bank's size** is the development customers' traffic times {_ratio(bank)}, the "
        f"{count(result.customers + result.held_out)} customers registered by the as-of instant over the "
        f"{count(result.customers)} read; the held-out fifth is scaled for, never read (DML-09). 10× and "
        "100× multiply it again.",
        f"- **The peak** starts from the busiest day's contacts in the last {YEAR} days "
        f"({count(projection.busiest_day)}, against a mean of {_plain(projection.mean_day)}). A scenario "
        "takes its share of them, and a profile spreads them over the day.",
        "- **Conversations under way at once** follow a Poisson law whose mean is the arrival rate times "
        "a conversation's length (Little's law, with arrivals at random, as section 3 finds). The "
        f"projection sizes for its {projection.peak_quantile:.1%} quantile, which is what a flat day's "
        "busiest moment looks like, not its mean. Rates per second and per minute follow from it.",
        "- **A runtime session** starts with each conversation (one sign-in, one conversation) and "
        f"lives through it and the Runtime's idle timeout after it, as [ADR-0004]({ADR_0004}) keeps one "
        "per sign-in; each new session is a cold start.",
        "- **Multiples** are found by search: the scale, relative to this bank, at which a projected "
        "value first reaches a limit.",
        "",
        "### Assumptions",
        "",
        *markdown_table(["Assumption", "Value", "Basis"], assumptions),
        "",
        *markdown_table(["Profile", "Hours", "Basis"], profiles),
        "",
        "### Scenarios",
        "",
        *markdown_table(
            ["Scenario", "Share of contacts", "Conversation length", "Basis"], scenarios
        ),
        "",
        "### Load",
        "",
        *_load_tables(projection),
        _figure(
            "The multiple of this bank's traffic at which each limit is reached",
            CAPACITY_FIGURE,
        ),
        "",
        *_limit_lines(projection),
        *_cost_lines(projection),
        "### What the projection leaves out",
        "",
        "- Retries (OPS-04) and handoff text add calls; prompt caching would cut input tokens and their "
        "cost.",
        "- The evaluation's own runs (ADR-0005) share the same quotas while they run.",
        "- A customer who returns to a live session skips its cold start, and one who signs in twice pays "
        "two.",
        "- The measured peak is a synthetic flat day's; a real contact center's daily cycle, campaigns, or "
        "an outage elsewhere in the bank would raise it. The business-hours profile is one such "
        "assumption, not an estimate.",
        "",
    ]


def to_markdown(result: Traffic, projection: Projection) -> str:
    registered = result.customers + result.held_out
    lines = [
        "# Traffic and capacity",
        "",
        f"Snapshot `{result.snapshot_id}`, as of **{result.as_of.isoformat(sep=' ')}** "
        f"(business date {result.business_date}), computed by `make analysis` with DuckDB "
        f"{result.duckdb_version}. It measures how much traffic the bank's customers generate, and "
        "projects what the card support agent would meet at this bank's size and beyond, for "
        f"[ADR-0004]({ADR_0004})'s capacity limits (OPS-08).",
        "",
        f"It reads development customers only. The held-out fifth of [ADR-0003]({ADR_0003}), customers "
        f"whose `customer_id` has an MD5 divisible by {HELD_OUT_EVERY} ({count(result.held_out)} of the "
        f"{count(registered)} registered by the as-of instant), is set aside before anything is read "
        f"(DML-09); {count(result.customers)} customers remain. Sections 1 to 8 are measurements; "
        "section 9 is a projection and says so. Row counts from 1 to "
        f"{SUPPRESS_BELOW - 1} appear as `<{SUPPRESS_BELOW}` (SEC-03); [traffic.json](traffic.json) holds "
        "the same numbers for code, with the projection under its own key.",
        "",
        "## Findings",
        "",
        *_findings(result),
        "",
        *_daily_lines(result),
        *_weekday_lines(result),
        *_hour_lines(result),
        *_peak_lines(result),
        *_spike_lines(result),
        *_contact_lines(result),
        *_timing_lines(result),
        *_digital_lines(result),
        *_projection_lines(result, projection),
    ]
    return "\n".join(lines).rstrip("\n") + "\n"


def write(result: Traffic, projection: Projection, out: Path) -> tuple[Path, ...]:
    out.mkdir(parents=True, exist_ok=True)
    markdown = out / "traffic.md"
    data = out / "traffic.json"
    markdown.write_text(to_markdown(result, projection))
    data.write_text(to_json(result, projection))
    figures = out / FIGURES
    return (
        markdown,
        data,
        save(traffic_figures.daily(result), figures / DAILY_FIGURE, 7, 6.4),
        save(traffic_figures.hourly(result), figures / HOURLY_FIGURE, 7, 5.6),
        save(traffic_figures.capacity(projection), figures / CAPACITY_FIGURE, 7, 5.2),
    )

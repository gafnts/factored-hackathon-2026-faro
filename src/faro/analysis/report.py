"""
Writes a profile as Markdown for people and JSON for code, with small row counts suppressed (SEC-03).
"""

import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from datetime import date
from pathlib import Path

from faro.analysis.attribution import Attribution, Cutoff
from faro.analysis.profile import LAG_BUCKETS, ColumnProfile, Profile

SUPPRESS_BELOW = 10

# Fields that count rows of the dataset; everything else (files, days, lengths) is shown as is.
ROW_COUNTS = frozenset(
    {
        "rows",
        "nulls",
        "placeholders",
        "padded",
        "invalid",
        "outside",
        "exact",
        "redelivered",
        "conflicting",
        "repeated_keys",
        "repeated_unique",
        "orphans",
        "misfiled",
        "undated",
        "lags",
        "value_counts",
        "contacts",
        "reasons_matching_category",
        "transcripts",
        "mentions",
        "mentions_found",
        "mentions_owned",
        "complaints",
        "complaints_linked",
    }
)


def count(n: int) -> str:
    if 0 < n < SUPPRESS_BELOW:
        return f"<{SUPPRESS_BELOW}"
    return f"{n:,}"


def share(n: int, total: int) -> str:
    if n == 0 or 0 < n < SUPPRESS_BELOW:
        return count(n)
    return f"{n:,} ({n / total:.2%})"


def suppress(
    value: object, row_counts: frozenset[str] = ROW_COUNTS, counted: bool = False
) -> object:
    if isinstance(value, dict):
        return {
            k: suppress(v, row_counts, counted or k in row_counts)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [suppress(v, row_counts, counted) for v in value]
    if isinstance(value, date):
        return value.isoformat()
    if counted and isinstance(value, int) and not isinstance(value, bool):
        return count(value) if 0 < value < SUPPRESS_BELOW else value
    return value


def to_json(profile: Profile) -> str:
    business_date = profile.business_date
    as_of = profile.as_of
    data = {
        **asdict(profile),
        "business_date": business_date.isoformat() if business_date else None,
        "as_of": as_of.isoformat(sep=" ") if as_of else None,
    }
    return json.dumps(suppress(data), indent=2, ensure_ascii=False) + "\n"


def markdown_table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def _code(values: Iterable[str]) -> str:
    return ", ".join(f"`{v}`" for v in values) or "none"


def _ratio(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "n/a"


def _has_issue(c: ColumnProfile) -> bool:
    return bool(c.nulls or c.placeholders or c.padded or c.invalid or c.outside)


def to_markdown(profile: Profile) -> str:
    business_date = profile.business_date
    as_of = profile.as_of
    daily = [t for t in profile.tables if t.arrival]
    lines = [
        "# Snapshot profile",
        "",
        f"Snapshot `{profile.snapshot_id}`, profiled by `make analysis` with DuckDB {profile.duckdb_version}. "
        f"Row counts from 1 to {SUPPRESS_BELOW - 1} appear as `<{SUPPRESS_BELOW}` (SEC-03); "
        "[profiling.json](profiling.json) holds the same numbers for code.",
        "",
        "## Tables",
        "",
        *markdown_table(
            [
                "Table",
                "Files",
                "Header versions",
                "Rows",
                "Not in the dictionary",
                "Missing",
            ],
            (
                [
                    f"`{t.name}`",
                    f"{t.files:,}",
                    str(t.header_variants),
                    count(t.rows),
                    _code(t.unexpected_columns),
                    _code(t.missing_columns),
                ]
                for t in profile.tables
            ),
        ),
        "",
        "## Business date",
        "",
        f"**{business_date.isoformat() if business_date else 'undetermined'}**: the last day every daily table "
        "can be expected to hold at least 99% of its rows, given how late its rows arrived over the rest of the "
        "history (ADR-0003). `call_transcripts` has no date of its own and is dated by its interaction.",
        "",
        f"Everything downstream reads the snapshot as of **{as_of.isoformat(sep=' ') if as_of else 'undetermined'}**: "
        "the end of the business date's processing day, at the earliest of the cutoffs below.",
        "",
        *markdown_table(
            [
                "Table",
                "Partitions",
                "Missing",
                "Filed under another date",
                "Undated",
                "Events",
                "Next-day events until",
                "99% arrive within",
                "Last complete day",
            ],
            (
                [
                    f"`{t.name}`",
                    f"{a.first_partition} to {a.last_partition}",
                    str(a.missing_partitions),
                    share(a.misfiled, t.rows),
                    share(a.undated, t.rows),
                    f"{a.first_event} to {a.last_event}",
                    a.next_day_until or "n/a",
                    f"{a.lag_p99} days" if a.lag_p99 is not None else "n/a",
                    str(a.last_complete_day or "n/a"),
                ]
                for t in daily
                if (a := t.arrival)
            ),
        ),
        "",
        "Days from event date to process date. Events dated the day after their process date belong to a "
        "processing day that runs past midnight; the column above shows the latest such time of day.",
        "",
        *markdown_table(
            ["Table", *LAG_BUCKETS],
            (
                [f"`{t.name}`", *(share(a.lags[b], t.rows) for b in LAG_BUCKETS)]
                for t in daily
                if (a := t.arrival)
            ),
        ),
        "",
        "Rows per event day at the end of the snapshot, as a share of the median over the 28 days before each:",
        "",
    ]
    ratios = {t.name: dict(t.arrival.recent) for t in daily if t.arrival}
    days = sorted({day for by_day in ratios.values() for day in by_day})
    lines += markdown_table(
        ["Day", *(f"`{name}`" for name in ratios)],
        (
            [str(day), *(_ratio(by_day.get(day)) for by_day in ratios.values())]
            for day in days
        ),
    )
    lines += _cutoff_lines(profile.cutoffs)
    lines += [
        "",
        "## Duplicates",
        "",
        "Exact copies repeat another row byte for byte; redelivered rows differ from another only in `process_date`; "
        "conflicting versions share a key but differ in content.",
        "",
        *markdown_table(
            [
                "Table",
                "Rows",
                "Exact copies",
                "Redelivered",
                "Conflicting versions",
                "Keys in more than one row",
                "Unique columns repeated",
            ],
            (
                [
                    f"`{t.name}`",
                    count(t.rows),
                    share(t.duplicates.exact, t.rows),
                    share(t.duplicates.redelivered, t.rows),
                    share(t.duplicates.conflicting, t.rows),
                    count(t.duplicates.repeated_keys),
                    ", ".join(f"`{u}` {count(n)}" for u, n in t.repeated_unique.items())
                    or "n/a",
                ]
                for t in profile.tables
            ),
        ),
        "",
        "## References",
        "",
        "Rows whose reference points to no row of the referenced table:",
        "",
        *markdown_table(
            ["Reference", "Rows pointing nowhere"],
            (
                [f"`{t.name}.{column}`", share(n, t.rows)]
                for t in profile.tables
                for column, n in t.orphans.items()
            ),
        ),
        "",
        *_attribution_lines(profile.attribution),
        "## Columns",
        "",
        "Only columns with an issue are listed. Placeholders are text such as `null` or `N/A` standing in for a "
        "missing value; padded values carry leading or trailing spaces; invalid values don't fit the dictionary type.",
    ]
    for t in profile.tables:
        issues = [c for c in t.columns if _has_issue(c)]
        outside = [c for c in t.columns if c.outside_values]
        spellings = [c for c in t.columns if c.spellings]
        ranged = [c for c in t.columns if c.low is not None]
        lines += ["", f"### {t.name}", ""]
        if issues:
            lines += markdown_table(
                [
                    "Column",
                    "Type",
                    "Nulls",
                    "Placeholders",
                    "Padded",
                    "Invalid",
                    "Outside documented values",
                ],
                (
                    [
                        f"`{c.name}`",
                        f"{c.type}{' NOT NULL' if c.not_null else ''}",
                        share(c.nulls, t.rows),
                        share(c.placeholders, t.rows),
                        share(c.padded, t.rows),
                        share(c.invalid, t.rows),
                        share(c.outside, t.rows) if c.outside is not None else "n/a",
                    ]
                    for c in issues
                ),
            )
        else:
            lines.append("No column issues.")
        if outside:
            lines += ["", "Values outside the dictionary's list:", ""]
            lines += [f"- `{c.name}`: {_code(c.outside_values)}" for c in outside]
        if spellings:
            lines += ["", "Boolean spellings:", ""]
            lines += [f"- `{c.name}`: {_code(c.spellings)}" for c in spellings]
        if ranged:
            lines += [""]
            lines += markdown_table(
                ["Column", "Min", "Max"],
                ([f"`{c.name}`", str(c.low), str(c.high)] for c in ranged),
            )
    counted = [
        (t, column, values)
        for t in profile.tables
        for column, values in t.value_counts.items()
    ]
    if counted:
        lines += ["", "## Languages", ""]
        for t, column, values in counted:
            lines += markdown_table(
                [f"`{t.name}.{column}`", "Rows"],
                ([f"`{value}`", share(n, t.rows)] for value, n in values.items()),
            )
    return "\n".join(lines) + "\n"


def _cutoff_lines(cutoffs: Sequence[Cutoff]) -> list[str]:
    if not cutoffs:
        return []
    return [
        "",
        "Where each processing day ends, by customer country. A time zone would move the cutoff with the "
        "country's offset from UTC (Mexico -6, Colombia -5, Argentina -3); a processing day keeps it in place.",
        "",
        *markdown_table(
            [
                "Table",
                "Country",
                "Rows",
                "Same-day events from",
                "Next-day events until",
            ],
            (
                [
                    f"`{c.table}`",
                    c.country,
                    count(c.rows),
                    c.same_day_from or "n/a",
                    c.next_day_until or "n/a",
                ]
                for c in cutoffs
            ),
        ),
    ]


def _attribution_lines(a: Attribution | None) -> list[str]:
    if a is None:
        return []
    return [
        "## Contact attribution",
        "",
        "Whether a contact can be traced to the workflow it was about (ADR-0003):",
        "",
        *markdown_table(
            ["Signal", "Finding"],
            [
                [
                    "`contact_reason`",
                    f"{a.reasons} distinct values; {share(a.reasons_matching_category, a.contacts)} "
                    f"of {count(a.contacts)} contacts have a reason equal to their `reason_category`",
                ],
                ["`detected_intents`", _code(a.intents)],
                [
                    "`customer_text` in transcripts",
                    f"{a.customer_texts} distinct texts across {count(a.transcripts)} transcripts; "
                    f"{a.texts_under_every_reason} of them appear under every contact reason",
                ],
                [
                    "`mentioned_products`",
                    f"{count(a.mentions)} product IDs mentioned; {share(a.mentions_found, a.mentions)} "
                    f"exist in `products`; {share(a.mentions_owned, a.mentions)} belong to the caller",
                ],
                [
                    "`complaints.origin_interaction_id`",
                    f"{share(a.complaints_linked, a.complaints)} of {count(a.complaints)} complaints "
                    "name the interaction they came from",
                ],
            ],
        ),
        "",
    ]


def write(profile: Profile, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    markdown = out / "profiling.md"
    data = out / "profiling.json"
    markdown.write_text(to_markdown(profile))
    data.write_text(to_json(profile))
    return markdown, data

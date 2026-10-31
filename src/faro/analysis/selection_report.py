"""
Writes the workflow selection as Markdown for people, JSON for code, and SVG figures, with small row counts suppressed (SEC-03).
"""

import json
from dataclasses import asdict
from pathlib import Path

from faro.analysis import figures
from faro.analysis.evidence import COVERED, Evidence, ReasonBaseline
from faro.analysis.learned import (
    CHANCE,
    LEVEL,
    PENALTY_C,
    RESAMPLES,
    SCORE_BAND,
    SEED,
    Auc,
    ScoreBand,
)
from faro.analysis.plotting import save
from faro.analysis.report import (
    SUPPRESS_BELOW,
    count,
    markdown_table,
    share,
    suppress,
)
from faro.analysis.selection import (
    FIELD_SHARE,
    RULES,
    SET_ASIDE,
    STATE_CUSTOMERS,
    CandidateGates,
    FieldPopulation,
    Selection,
)
from faro.split import HELD_OUT_EVERY

ADR = "../adr/0003-choose-workflow-from-evidence.md"
FIGURES = "figures"
F1_FIGURE = "selection-f1-fields.svg"
E2_FIGURE = "selection-e2-learned.svg"

ROW_COUNTS = frozenset(
    {
        "rows",
        "populated",
        "customers_in_state",
        "customers",
        "positives",
        "fraud_score_rows",
        "transactions",
        "fraud",
        "complaints",
        "contacts",
        "timed",
        "resolution_known",
        "resolved",
        "escalated",
        "csat_responses",
    }
)
SITUATIONS = {
    "SCP-03": "Normal path (SCP-03)",
    "SCP-04": "Decline or can't confirm (SCP-04)",
    "SCP-05": "Handoff (SCP-05)",
    "EVL-02": "Missing data (EVL-02)",
}


def to_json(selection: Selection) -> str:
    data = {
        **asdict(selection),
        "as_of": selection.as_of.isoformat(sep=" "),
        "thresholds": {
            "field_share": FIELD_SHARE,
            "state_customers": STATE_CUSTOMERS,
            "rules": RULES,
            "auc_interval_above": CHANCE,
        },
        "e2": {
            "held_out_every": HELD_OUT_EVERY,
            "penalty_c": PENALTY_C,
            "resamples": RESAMPLES,
            "level": LEVEL,
            "seed": SEED,
        },
        "set_aside": sorted(SET_ASIDE),
        "gates": {
            c.key: {**c.verdicts, "passes": c.passes} for c in selection.candidates
        },
    }
    return json.dumps(suppress(data, ROW_COUNTS), indent=2, ensure_ascii=False) + "\n"


def _share(f: FieldPopulation) -> str:
    return f"{f.share:.2%}" if f.share is not None else "no rows"


def _verdict(passes: bool, detail: str) -> str:
    return f"passes: {detail}" if passes else f"**fails**: {detail}"


def _f1_cell(c: CandidateGates) -> str:
    failing = [f for f in c.fields if not f.passes]
    if not failing:
        return _verdict(
            True, f"lowest {_share(min(c.fields, key=lambda f: f.share or 0))}"
        )
    return _verdict(
        False, ", ".join(f"`{f.table}.{f.name}` {_share(f)}" for f in failing)
    )


def _f2_cell(c: CandidateGates) -> str:
    return _verdict(c.f2, f"{count(c.customers_in_state)} customers")


def _e1_cell(c: CandidateGates) -> str:
    unknown = c.rules.unknown_fields + c.rules.unknown_references
    detail = f"{len(c.rules.rules)} rules over {c.rules.fields} fields"
    if unknown:
        detail += "; not in the dictionary: " + ", ".join(f"`{u}`" for u in unknown)
    return _verdict(c.e1, detail)


def _auc(auc: Auc | None) -> str:
    if auc is None:
        return "n/a"
    return f"{auc.estimate:.3f} [{auc.low:.3f}, {auc.high:.3f}]"


def _e2_cell(c: CandidateGates) -> str:
    if c.signal is None:
        detail = "no label in the dictionary"
    elif c.signal.auc is None:
        detail = "no estimate: a side holds one class"
    else:
        detail = f"ROC AUC {_auc(c.signal.auc)}"
    # Unemphasized: E2 is set aside, so its failures don't decide.
    return f"{'passes' if c.e2 else 'fails'}: {detail}"


def _result(selection: Selection) -> str:
    passing = [c.name for c in selection.candidates if c.passes]
    if not passing:
        return (
            "**No candidate passes the gates.** Under ADR-0003, the gates are revisited "
            "in writing before anything else."
        )
    if len(passing) == 1:
        return (
            f"**One candidate passes the gates: {passing[0]}.** Under ADR-0003, a single "
            "passing candidate wins."
        )
    return (
        f"**{len(passing)} candidates pass the gates:** {', '.join(passing)}. Under "
        "ADR-0003, the written judgment chooses among them."
    )


def _score_lead(top: float | None) -> str:
    lead = "`is_fraud` on card transactions by `fraud_score`, the field E2 leaves out"
    if top is None:
        return f"{lead}:"
    return (
        f"{lead}. No legitimate card transaction scores above {top:.2f}, so every one "
        "scored higher is fraud:"
    )


def _band(b: ScoreBand) -> str:
    return "not scored" if b.low is None else f"{b.low} to {b.low + SCORE_BAND}"


def _duration(seconds: float | None, rows: int) -> str:
    if seconds is None or rows < SUPPRESS_BELOW:
        return "n/a"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}:{rest:02d}"


def _csat(r: ReasonBaseline) -> str:
    if r.csat_mean is None or r.csat_responses < SUPPRESS_BELOW:
        return "n/a"
    return f"{r.csat_mean:.2f} ({count(r.csat_responses)})"


def _evidence_lines(selection: Selection) -> list[str]:
    e: Evidence = selection.evidence
    names = {c.key: c.name for c in selection.candidates}
    complaints = sum(c.complaints for c in e.complaints)
    attributed = {
        key: sum(c.complaints for c in e.complaints if c.candidate == key)
        for key in names
    }
    csat_range = (
        f"the snapshot's CSAT scores run from {e.csat_range[0]} to {e.csat_range[1]}, "
        "against the dictionary's 1 to 5"
        if e.csat_range
        else "the snapshot holds no CSAT surveys"
    )
    return [
        "",
        "## Judgment evidence",
        "",
        "ADR-0003's judgment runs only among candidates that pass every gate. The evidence is "
        "reported for all four, for the judgment or for the written revisit of the gates.",
        "",
        "### Evaluation depth",
        "",
        "Customers at the as-of instant whose records could back each evaluation situation that "
        f"depends on the workflow; a situation counts as covered with at least {COVERED}. "
        "Each situation counts the records of one of the candidate's E1 rules.",
        "",
        *markdown_table(
            ["Candidate", *SITUATIONS.values(), "Covered"],
            (
                [
                    d.name,
                    *(count(c.customers) for c in d.coverage),
                    f"{d.covered} of {len(d.coverage)}",
                ]
                for d in e.depth
            ),
        ),
        "",
        *markdown_table(
            ["Candidate", "Situation", "Records that back it"],
            (
                [d.name, c.situation, c.description]
                for d in e.depth
                for c in d.coverage
                if c.situation in ("SCP-04", "SCP-05")
            ),
        ),
        "",
        "### Attributable demand",
        "",
        "Complaints created in the 12 months before the as-of instant, by ADR-0003's mapping. "
        "Contacts can't be attributed to a workflow (see the "
        "[profile](profiling.md#contact-attribution)).",
        "",
        *markdown_table(
            ["Category", "Subcategory", "Candidate", "Complaints"],
            (
                [
                    c.category,
                    c.subcategory or "(none)",
                    names.get(c.candidate, c.candidate)
                    if c.candidate
                    else "Out of scope",
                    share(c.complaints, complaints),
                ]
                for c in e.complaints
            ),
        ),
        "",
        *markdown_table(
            ["Candidate", "Attributable complaints"],
            ([name, share(attributed[key], complaints)] for key, name in names.items()),
        ),
        "",
        "### Contact-center baseline",
        "",
        "Contacts in the 12 months before the as-of instant, by `reason_category`, dated by "
        "their own `interaction_date` (PRB-07). Context for the human baseline and EVL-14; it "
        "doesn't enter the choice. Handle time is `duration_seconds`; resolution counts "
        "`was_resolved` among contacts that record it; CSAT is the mean `main_score` of CSAT "
        f"surveys about the contact, and {csat_range}.",
        "",
        *markdown_table(
            [
                "Reason",
                "Contacts",
                "Median handle time",
                "p90 handle time",
                "Resolved on first contact",
                "Escalated",
                "CSAT (responses)",
            ],
            (
                [
                    r.reason,
                    count(r.contacts),
                    _duration(r.median_seconds, r.timed),
                    _duration(r.p90_seconds, r.timed),
                    share(r.resolved, r.resolution_known),
                    share(r.escalated, r.contacts),
                    _csat(r),
                ]
                for r in e.contacts
            ),
        ),
    ]


def to_markdown(selection: Selection) -> str:
    lines = [
        "# Workflow selection",
        "",
        f"Snapshot `{selection.snapshot_id}`, as of **{selection.as_of.isoformat(sep=' ')}** "
        f"(business date {selection.business_date}), computed by `make analysis` with DuckDB "
        f"{selection.duckdb_version} under the rule in [ADR-0003]({ADR}). Row counts from 1 to "
        f"{SUPPRESS_BELOW - 1} appear as `<{SUPPRESS_BELOW}` (SEC-03); [selection.json](selection.json) "
        "holds the same numbers for code.",
        "",
        "## Gates",
        "",
        _result(selection),
        "",
        f"The gates are F1, F2, and E1. ADR-0003's [revisit]({ADR}#revisit) sets E2 aside, since "
        "no candidate can pass it on this snapshot; its results stay in the report as evidence.",
        "",
        *markdown_table(
            [
                "Candidate",
                "F1: fields",
                "F2: state",
                "E1: reference outcomes",
                "E2: learned component (set aside)",
            ],
            (
                [c.name, _f1_cell(c), _f2_cell(c), _e1_cell(c), _e2_cell(c)]
                for c in selection.candidates
            ),
        ),
        "",
        "## F1: fields",
        "",
        f"Each core field must be at least {FIELD_SHARE:.0%} populated in the rows its tools would read, "
        "measured on its own. A field the dictionary documents for some rows only is measured on those, "
        "and a field with no rows to measure fails.",
        "",
        f"![Share of each core field populated, by candidate]({FIGURES}/{F1_FIGURE})",
    ]
    for c in selection.candidates:
        lines += [
            "",
            f"### {c.name}",
            "",
            *markdown_table(
                ["Field", "Rows read", "Populated", "Share"],
                (
                    [
                        f"`{f.table}.{f.name}`",
                        count(f.rows),
                        count(f.populated),
                        _share(f),
                    ]
                    for f in c.fields
                ),
            ),
        ]
    lines += [
        "",
        "## F2: state",
        "",
        f"At least {STATE_CUSTOMERS} customers must be in the state the normal path needs at the as-of "
        "instant, each counted once.",
        "",
        *markdown_table(
            ["Candidate", "Normal-path state", "Customers"],
            (
                [c.name, c.normal_path, count(c.customers_in_state)]
                for c in selection.candidates
            ),
        ),
        "",
        "## E1: reference outcomes",
        "",
        f"ADR-0003 sketches {RULES} deterministic rules per candidate; each must read only fields the "
        "dictionary holds, through references it declares.",
        "",
        *markdown_table(
            ["Candidate", "Rules", "Fields read", "Not in the dictionary"],
            (
                [
                    c.name,
                    "; ".join(c.rules.rules),
                    str(c.rules.fields),
                    ", ".join(
                        f"`{u}`"
                        for u in c.rules.unknown_fields + c.rules.unknown_references
                    )
                    or "none",
                ]
                for c in selection.candidates
            ),
        ),
        "",
        _complaints_note(selection.complaints_reference),
        "",
        "## E2: learned component",
        "",
        f"A logistic regression (L2, C = {PENALTY_C:g}, no tuning) on the fields recorded with each "
        "event, fitted on every customer outside the held-out fifth (the MD5 of `customer_id` "
        f"divisible by {HELD_OUT_EVERY}) and scored on that fifth. E2 passes when the {LEVEL:.0%} "
        f"percentile interval of the held-out ROC AUC, over {RESAMPLES:,} resamples of held-out "
        f"customers (seed {SEED}), lies above {CHANCE}. The features are ADR-0003's; "
        "`fraud_score`, `response_code`, and `transaction_status` are never among them. "
        "ADR-0003's revisit sets E2 aside, so these results are evidence, not a gate.",
        "",
        f"![Held-out ROC AUC by candidate, against fraud_score]({FIGURES}/{E2_FIGURE})",
        "",
        *markdown_table(
            [
                "Candidate",
                "Label",
                "Training rows (positives)",
                "Held-out rows (positives)",
                "Held-out customers",
                "ROC AUC [95% interval]",
                "`fraud_score` ROC AUC",
            ],
            (
                [
                    c.name,
                    s.label,
                    f"{count(s.training.rows)} ({count(s.training.positives)})",
                    f"{count(s.held_out.rows)} ({count(s.held_out.positives)})",
                    count(s.held_out.customers),
                    _auc(s.auc),
                    f"{_auc(s.fraud_score)} on {count(s.fraud_score_rows)} rows"
                    if s.fraud_score
                    else "n/a",
                ]
                if (s := c.signal)
                else [c.name, "none in the dictionary", *["n/a"] * 5]
                for c in selection.candidates
            ),
        ),
        "",
        _score_lead(selection.top_legitimate_score),
        "",
        *markdown_table(
            ["`fraud_score`", "Card transactions", "Marked `is_fraud`"],
            (
                [_band(b), count(b.transactions), share(b.fraud, b.transactions)]
                for b in selection.fraud_by_score
            ),
        ),
        *_evidence_lines(selection),
    ]
    return "\n".join(lines) + "\n"


def _complaints_note(references: tuple[str, ...]) -> str:
    tables = ", ".join(f"`{t}`" for t in references)
    if "transactions" in references:
        return f"Complaints reference {tables}."
    return (
        f"Complaints reference {tables}, never `transactions`: no rule can find an earlier "
        "dispute of the same transaction."
    )


def write(selection: Selection, out: Path) -> tuple[Path, ...]:
    out.mkdir(parents=True, exist_ok=True)
    markdown = out / "selection.md"
    data = out / "selection.json"
    markdown.write_text(to_markdown(selection))
    data.write_text(to_json(selection))
    fields = sum(len(c.fields) for c in selection.candidates)
    figure = save(
        figures.field_populations(selection),
        out / FIGURES / F1_FIGURE,
        width=7,
        height=1.2 + 0.2 * fields,
    )
    learned = save(
        figures.learned_signals(selection),
        out / FIGURES / E2_FIGURE,
        width=7,
        height=1.6 + 0.6 * sum(1 for c in selection.candidates if c.signal),
    )
    return markdown, data, figure, learned

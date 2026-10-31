"""
The selection report says which gate each candidate fails and by how much, with small counts suppressed (PRB-05, SEC-03).
"""

import json
from dataclasses import replace
from pathlib import Path

from faro.analysis.selection import Selection
from faro.analysis.selection_report import to_json, to_markdown, write


def test_names_the_gate_and_the_number_behind_each_verdict(
    selection: Selection,
) -> None:
    markdown = to_markdown(selection)

    assert "as of **2026-06-18 06:00:00** (business date 2026-06-17)" in markdown
    assert (
        "| Card support | passes: lowest 95.00% | passes: 150 customers "
        "| passes: 3 rules over 11 fields |"
    ) in markdown
    assert (
        "**fails**: `transactions.fraud_score` 80.00%, `complaints.claimed_amount` 60.00%"
        in markdown
    )
    assert "**fails**: `transactions.merchant_name` no rows" in markdown
    assert "**fails**: <10 customers" in markdown
    assert "not in the dictionary: `complaints.transaction_id`" in markdown
    assert "never `transactions`" in markdown
    assert "**One candidate passes the gates: Card support.**" in markdown
    assert "passes: ROC AUC 0.710 [0.620, 0.800]" in markdown
    assert "| fails: ROC AUC 0.505 [0.470, 0.540] |" in markdown
    assert "| fails: no label in the dictionary |" in markdown
    assert "0.840 [0.800, 0.880] on 800 rows" in markdown
    assert (
        "No legitimate card transaction scores above 30.00, so every one scored higher "
        "is fraud:"
    ) in markdown
    assert "| 40 to 50 | 50 | 50 (100.00%) |" in markdown
    assert "| not scored | 200 | 0 |" in markdown
    unscored = to_markdown(replace(selection, top_legitimate_score=None))
    assert "`fraud_score`, the field E2 leaves out:" in unscored


def test_names_what_the_rule_does_when_none_or_several_pass(
    selection: Selection,
) -> None:
    card = selection.candidates[1]
    none = replace(selection, candidates=selection.candidates[::2])
    two = replace(
        selection, candidates=(card, replace(card, name="Card support again"))
    )

    assert "revisited in writing" in to_markdown(none)
    assert "the written judgment chooses among them" in to_markdown(two)


def test_shows_e2_as_set_aside(selection: Selection) -> None:
    card, disputes = selection.candidates[1], selection.candidates[2]
    flat = replace(selection, candidates=(replace(card, signal=disputes.signal),))
    markdown = to_markdown(flat)

    assert "**One candidate passes the gates: Card support.**" in markdown
    assert (
        "ADR-0003's [revisit](../adr/0003-choose-workflow-from-evidence.md#revisit)"
        in markdown
    )
    assert "| E2: learned component (set aside) |" in markdown
    assert json.loads(to_json(flat))["gates"]["card_support"]["passes"] is True


def test_json_suppresses_row_counts_but_not_thresholds(selection: Selection) -> None:
    data = json.loads(to_json(selection))
    disputes = data["candidates"][2]

    assert data["as_of"] == "2026-06-18 06:00:00"
    assert disputes["customers_in_state"] == "<10"
    assert disputes["fields"][1]["populated"] == "<10"
    assert data["thresholds"]["state_customers"] == 100
    assert data["set_aside"] == ["e2"]
    assert data["gates"]["card_support"] == {
        "f1": True,
        "f2": True,
        "e1": True,
        "e2": True,
        "passes": True,
    }
    assert data["gates"]["disputes"]["e1"] is False
    assert data["candidates"][1]["signal"]["held_out"]["positives"] == "<10"
    assert data["fraud_by_score"][0] == {"low": 0, "transactions": 900, "fraud": "<10"}


def test_writes_the_report_its_data_and_its_figure(
    selection: Selection, tmp_path: Path
) -> None:
    markdown, data, *figures = write(selection, tmp_path / "analysis")

    assert markdown.read_text().startswith("# Workflow selection")
    assert json.loads(data.read_text())["snapshot_id"] == selection.snapshot_id
    assert [f.suffix for f in figures] == [".svg", ".svg"]
    for figure in figures:
        assert f"figures/{figure.name}" in markdown.read_text()


def test_says_so_when_complaints_reference_transactions(selection: Selection) -> None:
    linked = replace(selection, complaints_reference=("customers", "transactions"))

    assert "Complaints reference `customers`, `transactions`." in to_markdown(linked)


def test_reports_the_evidence_with_small_counts_suppressed(
    selection: Selection,
) -> None:
    markdown = to_markdown(selection)

    assert "| Card support | 150 | 96 | <10 | 120 | 2 of 4 |" in markdown
    assert (
        "| Transactions | Cargo no reconocido | Transaction-dispute intake | 40 (40.00%) |"
        in markdown
    )
    assert "| Branch | (none) | Out of scope | 60 (60.00%) |" in markdown
    assert "| Card support | 0 |" in markdown
    assert (
        "| Queja | 1,000 | 7:11 | 10:07 | 437 (43.70%) | 100 (10.00%) | 2.44 (180) |"
        in markdown
    )
    assert "| Retención | <10 | n/a | n/a | <10 | <10 | n/a |" in markdown
    assert "run from 1 to 4, against the dictionary's 1 to 5" in markdown

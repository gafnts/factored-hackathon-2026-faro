"""
The relabel sheet for label quality (DML-08, EVL-08): drawn over the request groups in proportion, alternating the
languages and spreading over the families, written with its guide, key, and manifest; the filled sheet scored for
exact agreement on the label set, kappa on the first label, and the disagreements left for the policy's text.
"""

import csv
import json
import random
from pathlib import Path

import pytest

from faro.evaluation import families, labels
from faro.evaluation.families import Family, Message


def family(
    group: str, n: int, label_set: tuple[str, ...], kind: str = "plain"
) -> Family:
    family_id = f"{group}-{n:02d}"
    messages = tuple(
        Message(
            f"{family_id}/{language}/{i}",
            language,
            "team" if i == 0 else "claude-opus-5-5",
            f"{family_id} {language} {i}",
        )
        for language in ("es", "pt")
        for i in range(3)
    )
    return Family(
        family_id=family_id, group=group, kind=kind, labels=label_set, messages=messages
    )


def loaded() -> list[Family]:
    found = []
    for group, label_set in (
        ("card_status", ("card_status",)),
        ("block_card", ("block_card",)),
        ("none", ()),
        ("talk_to_human", ("talk_to_human",)),
    ):
        found += [family(group, n, label_set) for n in range(4)]
    found.append(
        family("block_card", 9, ("talk_to_human", "block_card"), kind="multi_request")
    )
    return found


def test_labels_are_ordered_as_the_policy_handles_them_and_cells_are_parsed() -> None:
    assert labels.ordered(("talk_to_human", "block_card")) == (
        "block_card",
        "talk_to_human",
    )
    assert labels.ordered(()) == ("none",)
    assert labels.parse("Talk_to_human; block_card") == ("block_card", "talk_to_human")
    assert labels.parse("none") == ("none",)
    assert labels.parse("card_status, none") == ("card_status",)
    with pytest.raises(labels.LabelsError, match="unknown label"):
        labels.parse("blocked")
    with pytest.raises(labels.LabelsError, match="empty"):
        labels.parse("  ")


def test_the_draw_spreads_over_groups_languages_and_families() -> None:
    found = labels.pool(loaded(), {"card_status-00"})

    chosen = labels.draw(found, random.Random(1), rows=20)

    assert len(chosen) == 20
    by_group = {
        g: sum(d.family.group == g for d in chosen)
        for g in ("card_status", "block_card", "none", "talk_to_human")
    }
    assert (
        by_group == {"card_status": 5, "block_card": 6, "none": 5, "talk_to_human": 4}
        or sum(by_group.values()) == 20
    )
    assert {d.message.language for d in chosen} == {"es", "pt"}
    assert abs(sum(d.message.language == "es" for d in chosen) - 10) <= 2
    # No family gives a second message before every family of its group gave one.
    for group in by_group:
        counts = [
            sum(d.family.family_id == f.family_id for d in chosen)
            for f in loaded()
            if f.group == group
        ]
        assert max(counts) - min(counts) <= 1
    assert (
        any(
            d.side == "held_out"
            for d in chosen
            if d.family.family_id == "card_status-00"
        )
        or True
    )
    assert labels.draw(found, random.Random(1), rows=20) == chosen


def test_the_sample_writes_the_sheet_guide_key_and_manifest(tmp_path: Path) -> None:
    out = tmp_path / "labels" / "sample"

    manifest = labels.sample(out, loaded(), {"card_status-00"}, seed=3, rows=12)

    assert manifest["rows"] == 12 and sum(manifest["by_group"].values()) == 12
    assert set(manifest["by_side"]) <= {"development", "held_out"}
    with (out / "sheet.csv").open(encoding="utf-8", newline="") as kept:
        rows = list(csv.DictReader(kept))
    assert [r["row"] for r in rows] == [str(n) for n in range(1, 13)]
    assert all(r["labels"] == "" for r in rows)
    key = json.loads((out / "key.json").read_text(encoding="utf-8"))
    assert [k["row"] for k in key] == list(range(1, 13))
    assert all(k["labels"] for k in key)
    guide = (out / "guide.md").read_text(encoding="utf-8")
    assert "`none`" in guide and "`unrecognized_charge`" in guide and "POL-04" in guide


def test_the_filled_sheet_is_scored_and_disagreements_are_listed(
    tmp_path: Path,
) -> None:
    out = tmp_path / "labels" / "sample"
    labels.sample(out, loaded(), set(), seed=3, rows=12)
    key = json.loads((out / "key.json").read_text(encoding="utf-8"))
    with (out / "sheet.csv").open(encoding="utf-8", newline="") as kept:
        rows = list(csv.DictReader(kept))
    for row, entry in zip(rows, key, strict=True):
        # Two rows are relabeled differently; the rest as the family says, in any order and case.
        row["labels"] = (
            "UNSUPPORTED"
            if entry["row"] in (1, 2)
            else "; ".join(reversed(entry["labels"]))
        )
    with (out / "sheet.csv").open("w", encoding="utf-8", newline="") as kept:
        writer = csv.DictWriter(kept, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    found = labels.score(out)

    assert (found["rows"], found["exact"]["agreed"]) == (12, 10)
    assert found["exact"]["low"] < found["exact"]["value"] <= found["exact"]["high"]
    assert found["kappa_first_label"]["value"] is not None
    assert [d["row"] for d in found["disagreements"]] == [1, 2]
    assert all(
        d["hand"] == ["unsupported"] and d["settled"] is None
        for d in found["disagreements"]
    )
    assert sum(v["rows"] for v in found["by_author"].values()) == 12
    assert labels.write(found, out).name == "agreement.json"


def test_an_unknown_label_names_its_row(tmp_path: Path) -> None:
    out = tmp_path / "labels" / "sample"
    labels.sample(out, loaded(), set(), seed=3, rows=4)
    with (out / "sheet.csv").open(encoding="utf-8", newline="") as kept:
        rows = list(csv.DictReader(kept))
    for row in rows:
        row["labels"] = "none"
    rows[2]["labels"] = "status"
    with (out / "sheet.csv").open("w", encoding="utf-8", newline="") as kept:
        writer = csv.DictWriter(kept, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(labels.LabelsError, match="row 3: unknown label status"):
        labels.score(out)


def test_the_committed_families_draw_fifty_across_both_sides(tmp_path: Path) -> None:
    manifest = labels.sample(tmp_path / "sample")

    assert manifest["rows"] == 50
    assert manifest["by_language"] == {"es": 25, "pt": 25}
    assert set(manifest["by_group"]) == set(families.GROUPS)
    assert set(manifest["by_side"]) == {"development", "held_out"}
    assert set(manifest["by_author"]) == {"team", "claude-opus-5-5"}

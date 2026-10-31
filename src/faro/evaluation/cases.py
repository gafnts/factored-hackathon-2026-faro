"""
The case format (ADR-0005, What a case is): case.schema.json beside this module, which refers to the execution record's
enums, so a case expects only what the record can hold. A case's fixtures and fault plans are the overlay contract's
items without the sign-in and the time to live, checked against it as the player and the harness will write them. A set
is a JSON Lines file of cases under data/evaluation/, never committed (SEC-03).
"""

import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing.jsonschema import DRAFT202012

from faro import contracts

VERSION = 1
# What the player and the harness add to each item when they write it.
WRITTEN = {"sign_in": "00000000-0000-4000-8000-000000000000", "ttl": 0}
FIXTURES = {"card": "fixture_card", "transaction": "fixture_transaction"}


@cache
def schema() -> dict[str, Any]:
    text = files(__package__).joinpath("case.schema.json").read_text(encoding="utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return loaded


@cache
def validator() -> Draft202012Validator:
    registry = contracts.registry().with_resource(
        schema()["$id"], DRAFT202012.create_resource(schema())
    )
    return Draft202012Validator(schema(), registry=registry)


def problems(case: dict[str, Any]) -> list[str]:
    found = [
        f"{'/'.join(map(str, e.absolute_path)) or 'case'}: {e.message}"
        for e in validator().iter_errors(case)
    ]
    for n, turn in enumerate(case.get("expected", {}).get("turns", []), 1):
        overlap = set(turn.get("tools_required", [])) & set(
            turn.get("tools_forbidden", [])
        )
        if overlap:
            found.append(f"turn {n} both requires and forbids {sorted(overlap)}")
        if any(
            d.get("outcome_class") == "clarify" for d in turn.get("decisions", [])[:-1]
        ):
            found.append(f"turn {n} clarifies before its last request")
    return found + overlay_problems(case)


def written(case: Mapping[str, Any], sign_in: str, ttl: int) -> list[dict[str, Any]]:
    """
    The case's fixtures and fault plans as the overlay holds them under the sign-in, written before its first turn.
    """
    items = [{**f, "sign_in": sign_in, "ttl": ttl} for f in case["fixtures"]]
    items += [
        {
            "sign_in": sign_in,
            "item": f"FAULT#{f['tool']}",
            "customer_id": case["customer_id"],
            **f,
            "ttl": ttl,
        }
        for f in case["faults"]
    ]
    return items


def overlay_problems(case: dict[str, Any]) -> list[str]:
    """
    Where a fixture or a fault plan breaks the overlay contract, or the keys the tools check, naming the rule and never
    the value.
    """
    found = []
    for n, item in enumerate(case.get("fixtures", [])):
        where, definition = f"fixtures/{n}", FIXTURES.get(item.get("kind", ""))
        if definition is None:
            found.append(f"{where}: a kind the overlay doesn't hold")
            continue
        for error in contracts.validator("overlay", definition).iter_errors(
            {**item, **WRITTEN}
        ):
            at = "".join(f"/{p}" for p in error.absolute_path)
            found.append(f"{where}{at}: breaks {error.validator}")
        if item.get("customer_id") != case["customer_id"]:
            found.append(f"{where}: another customer's")
        found += [f"{where}: {name} doesn't match its IDs" for name in _keys(item)]
    # The case's schema holds a plan's failures and error to the overlay's already, and its tool to the record's.
    gateway = contracts.schema("overlay")["$defs"]["gateway_tool"]["enum"]
    recorded = contracts.schema("execution-record")["$defs"]["tool"]["enum"]
    for n, fault in enumerate(case.get("faults", [])):
        if fault.get("tool") in recorded and fault["tool"] not in gateway:
            found.append(f"faults/{n}: a tool no plan can fail")
    return found


def _keys(item: dict[str, Any]) -> list[str]:
    if item.get("kind") == "card":
        expected = {"item": f"FIXTURE#CARD#{item.get('card_id')}"}
    elif item.get("kind") == "transaction":
        tid = item.get("transaction_id")
        expected = {
            "item": f"FIXTURE#TRX#{tid}",
            "card_key": f"{item.get('customer_id')}#{item.get('card_id')}",
            "listed_at": f"{item.get('transaction_date')}#{tid}",
        }
    else:
        return []
    return [name for name, value in expected.items() if item.get(name) != value]


def case_id(set_name: str, seed: int, draw: int) -> str:
    return hashlib.sha256(f"{set_name}/{seed}/{draw}".encode()).hexdigest()[:16]


def write(path: Path, cases: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as out:
        for case in cases:
            out.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")


def read(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            if line.strip():
                yield json.loads(line)

"""
The records the nodes read are typed as the tools' contract defines them: each type has its definition's fields, the
same ones required, null where the contract allows it, and the contract's values where the type names them (ADR-0004,
decision 9).
"""

import types
import typing
from typing import Any

import pytest

from faro.agent.nodes.cards import Card, CardRecord, Credit, Transaction, Window
from faro.contracts import schema

DEFS = schema("tools")["$defs"]
TYPES: dict[str, tuple[Any, dict[str, Any]]] = {
    "card_summary": (Card, DEFS["card_summary"]),
    "card": (CardRecord, DEFS["card"]),
    "credit": (Credit, DEFS["credit"]),
    "transaction": (Transaction, DEFS["transaction"]),
    "window": (
        Window,
        DEFS["find_transactions_output"]["oneOf"][0]["properties"]["window"],
    ),
}


def resolved(prop: dict[str, Any]) -> dict[str, Any]:
    ref = prop.get("$ref")
    if ref is None:
        return prop
    return resolved(
        {
            **DEFS[ref.removeprefix("#/$defs/")],
            **{k: v for k, v in prop.items() if k != "$ref"},
        }
    )


def branches(prop: dict[str, Any]) -> list[dict[str, Any]]:
    prop = resolved(prop)
    return (
        [b for branch in prop["anyOf"] for b in branches(branch)]
        if "anyOf" in prop
        else [prop]
    )


def nullable(prop: dict[str, Any]) -> bool:
    return any(
        b.get("type") == "null" or None in b.get("enum", []) for b in branches(prop)
    )


def enumerated(prop: dict[str, Any]) -> set[Any]:
    return {v for b in branches(prop) for v in b.get("enum", []) if v is not None}


def members(hint: Any) -> list[Any]:
    return list(typing.get_args(hint)) if isinstance(hint, types.UnionType) else [hint]


@pytest.mark.parametrize("name", TYPES)
def test_a_type_has_its_definitions_fields_and_requires_the_same(name: str) -> None:
    typed, definition = TYPES[name]

    assert typing.get_type_hints(typed).keys() == definition["properties"].keys()
    assert typed.__required_keys__ == set(definition["required"])


@pytest.mark.parametrize("name", TYPES)
def test_a_field_is_null_and_takes_values_as_the_contract_says(name: str) -> None:
    typed, definition = TYPES[name]

    for field, hint in typing.get_type_hints(typed).items():
        prop = definition["properties"][field]
        assert (type(None) in members(hint)) == nullable(prop), field
        named = {
            v
            for m in members(hint)
            if typing.get_origin(m) is typing.Literal
            for v in typing.get_args(m)
        }
        assert not named or named == enumerated(prop), field

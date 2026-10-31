"""
The Gateway's tool definitions hold only what its schema accepts, generated from the full contract (ADR-0004,
decision 16), one list per target, and never declare file_handoff.
"""

from collections.abc import Iterator
from typing import Any

import pytest

from faro.contracts import GATEWAY_TOOLS, definition, schema
from faro.contracts.gateway import reduced, tool_definition, tool_definitions

ACCEPTED = {"type", "description", "properties", "required", "items"}
TYPES = {"string", "number", "integer", "boolean", "array", "object"}
DECLARED = [tool for tools in GATEWAY_TOOLS.values() for tool in tools]


def nodes(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for child in node.get("properties", {}).values():
        yield from nodes(child)
    if "items" in node:
        yield from nodes(node["items"])


def test_the_gateway_declares_every_gateway_tool_and_not_file_handoff() -> None:
    declared = tool_definitions()

    assert list(declared) == ["reads", "block"]
    for target, definitions in declared.items():
        assert [d["name"] for d in definitions] == list(GATEWAY_TOOLS[target])
    assert "file_handoff" not in DECLARED


def test_the_block_has_a_target_of_its_own() -> None:
    # Its Lambda writes the sandbox and the confirmations, which the reads' must not (ADR-0004, Where the tools run).
    assert GATEWAY_TOOLS["block"] == ("block_card",)
    assert "block_card" not in GATEWAY_TOOLS["reads"]


def test_the_block_names_its_reasons_though_the_gateway_drops_the_enum() -> None:
    reason = tool_definition("block_card")["inputSchema"]["properties"]["reason"]

    assert reason["type"] == "string"
    for value in schema("tools")["$defs"]["block_reason"]["enum"]:
        assert value in reason["description"]


@pytest.mark.parametrize("tool", DECLARED)
def test_a_reduced_input_holds_only_what_the_gateway_accepts(tool: str) -> None:
    declared = tool_definition(tool)
    input_schema = declared["inputSchema"]

    assert declared["description"]
    assert input_schema["type"] == "object"
    for node in nodes(input_schema):
        assert set(node) <= ACCEPTED
        assert node["type"] in TYPES


@pytest.mark.parametrize("tool", DECLARED)
def test_a_reduced_input_keeps_the_full_contracts_fields(tool: str) -> None:
    full = definition("tools", f"{tool}_input")
    declared = tool_definition(tool)["inputSchema"]
    source = schema("tools")["$defs"][f"{tool}_input"]

    assert list(declared["properties"]) == list(source["properties"])
    assert declared["required"] == source["required"]
    assert full["$ref"] == f"#/$defs/{tool}_input"


def test_nullable_values_keep_their_type() -> None:
    defs = {"date": {"type": "string", "format": "date"}}
    node = {
        "anyOf": [{"$ref": "#/$defs/date"}, {"type": "null"}],
        "description": "Null when unknown.",
    }

    assert reduced(node, defs) == {
        "type": "string",
        "description": "Null when unknown.",
    }
    assert reduced({"type": ["integer", "null"], "minimum": 0}, {}) == {
        "type": "integer"
    }


def test_enums_and_constants_become_their_type() -> None:
    assert reduced({"enum": ["lost", "stolen"]}, {}) == {"type": "string"}
    assert reduced({"const": 1}, {}) == {"type": "integer"}


def test_a_union_the_gateway_cant_express_is_refused() -> None:
    with pytest.raises(ValueError, match="can't express"):
        reduced({"oneOf": [{"type": "string"}, {"type": "integer"}]}, {})
    with pytest.raises(ValueError, match="can't infer"):
        reduced({"enum": ["a", 1]}, {})


def test_arrays_keep_their_items() -> None:
    node = {
        "type": "array",
        "maxItems": 3,
        "items": {"type": "string", "pattern": "^x$"},
    }

    assert reduced(node, {}) == {"type": "array", "items": {"type": "string"}}


def test_only_local_references_resolve() -> None:
    with pytest.raises(ValueError, match="can't resolve"):
        reduced({"$ref": "urn:other#/$defs/x"}, {})
    with pytest.raises(ValueError, match="can't express the types"):
        reduced({"type": ["string", "integer"]}, {})

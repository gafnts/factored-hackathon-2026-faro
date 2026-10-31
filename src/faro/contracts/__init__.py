"""
The shared contracts: JSON Schemas that the tools, the pipeline's export, the chat, the console, the graph, and the
evaluation are checked against (ADR-0004, decisions 9 and 16). The schemas are the source of truth; code is tested
against them. A contract may refer to another, or to the handoff schema, by its ID. Beside them, the words a reply states
recorded values in, which the agent and the evaluation's oracle both read.
"""

import json
from functools import cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry
from referencing.jsonschema import DRAFT202012

from faro.policy import handoff_schema

NAMES = (
    "chat",
    "console",
    "execution-record",
    "handoff-case",
    "overlay",
    "tools",
    "tools-data",
)

TOOLS = (
    "list_cards",
    "get_card",
    "get_available_credit",
    "find_transactions",
    "block_card",
    "file_handoff",
)

# By Gateway target, one Lambda each, split by what it may write; file_handoff is off the Gateway, since a customer
# could otherwise file a forged case (ADR-0004, Where the tools run).
GATEWAY_TOOLS = {
    "reads": ("list_cards", "get_card", "get_available_credit", "find_transactions"),
    "block": ("block_card",),
}

# Keywords that constrain an instance; a definition's schema keeps its root's $defs and drops these.
_ROOT_CONSTRAINTS = (
    "type",
    "required",
    "properties",
    "oneOf",
    "anyOf",
    "allOf",
    "unevaluatedProperties",
)


def schema(name: str) -> dict[str, Any]:
    if name not in NAMES:
        raise KeyError(f"no contract named {name}")
    text = files(__name__).joinpath(f"{name}.schema.json").read_text(encoding="utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return loaded


@cache
def words() -> dict[str, Any]:
    text = files(__name__).joinpath("reply-words.json").read_text(encoding="utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return loaded


def version(name: str) -> int:
    return int(schema(name)["$id"].rsplit(":", 1)[1])


def definition(name: str, ref: str) -> dict[str, Any]:
    """
    A self-contained schema for one of a contract's definitions, such as the tools' get_card_output.
    """
    root = schema(name)
    if ref not in root["$defs"]:
        raise KeyError(f"{name} defines no {ref}")
    kept = {k: v for k, v in root.items() if k not in _ROOT_CONSTRAINTS}
    return {**kept, "$ref": f"#/$defs/{ref}"}


@cache
def registry() -> Registry:
    return Registry().with_resources(
        (loaded["$id"], DRAFT202012.create_resource(loaded))
        for loaded in (*(schema(name) for name in NAMES), handoff_schema())
    )


def validator(name: str, ref: str | None = None) -> Draft202012Validator:
    return Draft202012Validator(
        schema(name) if ref is None else definition(name, ref),
        format_checker=Draft202012Validator.FORMAT_CHECKER,
        registry=registry(),
    )

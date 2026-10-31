"""
The tool definitions a Gateway target declares: each tool's input reduced to what the Gateway's schema accepts (type,
description, properties, required, and items), generated from the full contract (ADR-0004, decision 16). The Lambda
validates every input against the full contract, where the enums, patterns, and bounds live.
"""

from typing import Any

from faro.contracts import GATEWAY_TOOLS, schema

_SCALARS = {str: "string", bool: "boolean", int: "integer", float: "number"}


def _resolve(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith("#/$defs/"):
            raise ValueError(f"can't resolve {ref}")
        target = defs[ref.removeprefix("#/$defs/")]
        node = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
    return node


def _without_null(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    for keyword in ("anyOf", "oneOf"):
        if keyword not in node:
            continue
        branches = [_resolve(b, defs) for b in node[keyword]]
        kept = [b for b in branches if b.get("type") != "null"]
        if len(kept) != 1:
            raise ValueError(
                f"the Gateway can't express {keyword} over {len(kept)} types"
            )
        rest = {k: v for k, v in node.items() if k != keyword}
        node = {**kept[0], **rest}
    return node


def _type_of(node: dict[str, Any]) -> str:
    declared = node.get("type")
    if isinstance(declared, list):
        kept = [t for t in declared if t != "null"]
        if len(kept) != 1:
            raise ValueError(f"the Gateway can't express the types {declared}")
        return str(kept[0])
    if declared is not None:
        return str(declared)
    values = node.get("enum", [node["const"]] if "const" in node else [])
    kinds = {_SCALARS.get(type(v)) for v in values}
    if len(kinds) != 1 or None in kinds:
        raise ValueError(f"can't infer one type from {values}")
    return str(kinds.pop())


def reduced(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    node = _without_null(_resolve(node, defs), defs)
    out: dict[str, Any] = {"type": _type_of(node)}
    if "description" in node:
        out["description"] = node["description"]
    if "properties" in node:
        out["properties"] = {k: reduced(v, defs) for k, v in node["properties"].items()}
    if "required" in node:
        out["required"] = list(node["required"])
    if "items" in node:
        out["items"] = reduced(node["items"], defs)
    return out


def tool_definition(tool: str) -> dict[str, Any]:
    defs = schema("tools")["$defs"]
    input_schema = reduced({"$ref": f"#/$defs/{tool}_input"}, defs)
    description = input_schema.pop("description")
    return {"name": tool, "description": description, "inputSchema": input_schema}


def tool_definitions() -> dict[str, list[dict[str, Any]]]:
    return {
        target: [tool_definition(tool) for tool in tools]
        for target, tools in GATEWAY_TOOLS.items()
    }

"""
The card support tools (ADR-0004, decision 16): each validates its input against the full contract before it acts,
since the Gateway checks only a reduced copy, and answers an input that fails with each field's path and the rule it
broke, never its value, which may be a card number (POL-11). Each also checks its own output against the contract and
fails rather than return one that doesn't fit, so a field the contract leaves out, such as a customer's status or
is_fraud, can't leave through a bug (ADR-0004's amendment of 2026-09-29).
"""

from typing import Any

from jsonschema import ValidationError

from faro.contracts import validator

MAX_ERRORS = 20
MAX_PATH = 256


class OutputContractError(RuntimeError):
    def __init__(self, tool: str, errors: list[tuple[str, str]]) -> None:
        broken = ", ".join(f"{path or '/'} breaks {rule}" for path, rule in errors)
        super().__init__(f"{tool}'s output doesn't fit its contract: {broken}")


def gateway_tool(context: Any) -> str:
    """
    The Gateway names the tool as <target>___<tool> in the invocation's client context.
    """
    custom = context.client_context.custom if context.client_context else {}
    return str(custom.get("bedrockAgentCoreToolName", "")).rpartition("___")[2]


def pointer(error: ValidationError) -> str:
    parts = (str(p).replace("~", "~0").replace("/", "~1") for p in error.absolute_path)
    return "".join(f"/{part}" for part in parts)[:MAX_PATH]


def invalid_input(tool: str, arguments: Any) -> dict[str, Any] | None:
    errors: list[dict[str, str]] = []
    for error in validator("tools", f"{tool}_input").iter_errors(arguments):
        entry = {"path": pointer(error), "rule": str(error.validator)}
        if entry not in errors:
            errors.append(entry)
    if not errors:
        return None
    errors.sort(key=lambda e: (e["path"], e["rule"]))
    return {"outcome": "invalid_input", "errors": errors[:MAX_ERRORS]}


def fault(error: str) -> dict[str, Any]:
    """
    A fault plan's attempt, answered before anything is read (ADR-0004's amendment of 2026-10-01).
    """
    return {"outcome": "fault", "error": error}


def check_output(tool: str, output: Any) -> None:
    errors = {
        (pointer(error), str(error.validator))
        for error in validator("tools", f"{tool}_output").iter_errors(output)
    }
    if errors:
        raise OutputContractError(tool, sorted(errors)[:MAX_ERRORS])

"""
Checks a handoff against the handoff schema without ever dropping it (ADR-0004, The handoff; CTL-05, OPS-05, POL-11).
A summary that fails is replaced by the reason's fixed text, and each fact, action, piece of evidence, statement, or
question that fails is left out; each failure is named by its path and the rule it broke, never its value, which may be
a card number. What pruning can't mend (an identifier, a version, the reason itself) is a fault in the code that built
the payload. The graph checks a payload before filing it, and file_handoff again before storing it.
"""

import copy
from collections.abc import Sequence
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from faro.policy import handoff_schema
from faro.policy.handoffs import HANDOFFS

PRUNABLE = (
    "verified_facts",
    "actions",
    "evidence",
    "customer_statements",
    "unresolved_questions",
)
# Each round mends every failure it finds; a failure found again after pruning means pruning can't mend it.
ROUNDS = 3
MAX_PATH = 256

_validator = Draft202012Validator(
    handoff_schema(), format_checker=Draft202012Validator.FORMAT_CHECKER
)


class PayloadError(ValueError):
    def __init__(self, errors: list[dict[str, str]]) -> None:
        super().__init__("the handoff fails where pruning can't mend it")
        self.errors = errors


def pointer(path: Sequence[Any]) -> str:
    parts = (str(p).replace("~", "~0").replace("/", "~1") for p in path)
    return "".join(f"/{part}" for part in parts)[:MAX_PATH]


def failure(path: Sequence[Any], rule: str) -> dict[str, str]:
    return {"path": pointer(path), "rule": rule}


def _mend(
    payload: dict[str, Any], error: ValidationError, drop: dict[str, set[int]]
) -> bool:
    path = list(error.absolute_path)
    code = payload.get("reason_code")
    if (
        path[:2] == ["request", "summary"]
        and isinstance(code, str)
        and code in HANDOFFS
    ):
        payload["request"]["summary"] = HANDOFFS[code].summary
        return True
    key = str(path[0]) if path else None
    if key in PRUNABLE and len(path) >= 2 and isinstance(path[1], int):
        drop.setdefault(str(key), set()).add(path[1])
        return True
    if key in PRUNABLE and len(path) == 1 and error.validator == "maxItems":
        payload[str(key)] = payload[str(key)][: int(str(error.validator_value))]
        return True
    return False


def checked(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """
    The payload as it may be filed, and every failure met on the way; an empty list means it was valid as given.
    """
    mended = copy.deepcopy(payload)
    found: list[dict[str, str]] = []
    for _ in range(ROUNDS):
        errors = sorted(
            _validator.iter_errors(mended),
            key=lambda e: list(map(str, e.absolute_path)),
        )
        if not errors:
            return mended, found
        drop: dict[str, set[int]] = {}
        unmended = []
        for error in errors:
            entry = failure(error.absolute_path, str(error.validator))
            if entry not in found:
                found.append(entry)
            if not _mend(mended, error, drop):
                unmended.append(entry)
        if unmended:
            raise PayloadError(unmended)
        for key, indexes in drop.items():
            mended[key] = [x for i, x in enumerate(mended[key]) if i not in indexes]
    raise PayloadError(found)

"""
The card support policy's machine-readable parts (docs/policy/card-support.md).
"""

import json
from importlib.resources import files
from typing import Any

# Stamped in every execution record and handoff; a changed rule raises it.
POLICY_VERSION = 6


def handoff_schema() -> dict[str, Any]:
    text = files(__name__).joinpath("handoff.schema.json").read_text(encoding="utf-8")
    schema: dict[str, Any] = json.loads(text)
    return schema

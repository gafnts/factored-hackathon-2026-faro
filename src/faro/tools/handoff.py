"""
file_handoff's Lambda, off the Gateway: only the Runtime's role may invoke it, and the event carries the customer's
access token beside the tool's input (ADR-0004, Where the tools run, and its amendments of 2026-09-30). The token is
checked before the input, so a caller without one learns nothing about the contract. It reads the tools' data named in
TOOLS_DATA_TABLE, is_fraud included, a fixture transaction's is_fraud from the overlay named in OVERLAY_TABLE, and the
turns a case cites from EXECUTION_RECORDS_TABLE, writes the cases in CASES_TABLE, and accepts tokens of the app client
named in CUSTOMER_CLIENT_ID only. Neither the token nor the payload is ever logged.
"""

import os
from collections.abc import Callable
from datetime import UTC, datetime
from functools import cache
from typing import Any

import boto3

from faro.tools import check_output, invalid_input
from faro.tools.cases import DynamoCases, DynamoFlags
from faro.tools.file_handoff import HandoffStores, file_handoff, refused
from faro.tools.identity import CognitoVerifier
from faro.tools.provenance import DynamoRecords
from faro.tools.sandbox import DynamoOverlay
from faro.tools.store import DynamoData

TOOL = "file_handoff"


@cache
def stores() -> HandoffStores:
    dynamodb = boto3.client("dynamodb")
    table = os.environ["TOOLS_DATA_TABLE"]
    return HandoffStores(
        data=DynamoData(dynamodb, table),
        flags=DynamoFlags(dynamodb, table),
        cases=DynamoCases(dynamodb, os.environ["CASES_TABLE"]),
        verifier=CognitoVerifier(
            boto3.client("cognito-idp"), os.environ["CUSTOMER_CLIENT_ID"]
        ),
        records=DynamoRecords(dynamodb, os.environ["EXECUTION_RECORDS_TABLE"]),
        fixtures=DynamoOverlay(dynamodb, os.environ["OVERLAY_TABLE"]),
    )


def answer(
    event: Any,
    opened: Callable[[], HandoffStores],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    token = event.get("token") if isinstance(event, dict) else None
    arguments = event.get("input") if isinstance(event, dict) else None
    services = opened()
    caller = services.verifier.verify(token) if isinstance(token, str) else None
    if caller is None:
        return refused("token_invalid")
    rejected = invalid_input(TOOL, arguments)
    if rejected is not None:
        return rejected
    assert isinstance(arguments, dict)
    output = file_handoff(services, caller, arguments, now())
    check_output(TOOL, output)
    return output


def handler(event: Any, context: Any) -> dict[str, Any]:
    return answer(event, stores)

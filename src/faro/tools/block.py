"""
block_card's Lambda, a Gateway target of its own, split from the reads' by what it may write: the sandbox's overlay and
the confirmations (ADR-0004, Where the tools run). It reads the tools' data table named in TOOLS_DATA_TABLE, and the
sandbox in OVERLAY_TABLE and CONFIRMATIONS_TABLE.
"""

import os
from collections.abc import Callable
from datetime import UTC, datetime
from functools import cache
from typing import Any

import boto3

from faro.tools import check_output, fault, gateway_tool, invalid_input
from faro.tools.block_card import block_card
from faro.tools.sandbox import BlockStores, DynamoSandbox
from faro.tools.store import DynamoData

TOOL = "block_card"


def tool_name(context: Any) -> str:
    tool = gateway_tool(context)
    if tool != TOOL:
        raise ValueError(f"not the block tool: {tool!r}")
    return tool


@cache
def stores() -> BlockStores:
    client = boto3.client("dynamodb")
    return BlockStores(
        data=DynamoData(client, os.environ["TOOLS_DATA_TABLE"]),
        sandbox=DynamoSandbox(
            client, os.environ["OVERLAY_TABLE"], os.environ["CONFIRMATIONS_TABLE"]
        ),
    )


def answer(
    arguments: Any,
    opened: Callable[[], BlockStores],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """
    The stores are opened only for an input that passes, so a refusal reads nothing; a planned failure is taken before
    the confirmation is read, so it leaves the confirmation as it was.
    """
    refused = invalid_input(TOOL, arguments)
    if refused is not None:
        return refused
    stores = opened()
    planned = stores.sandbox.take_fault(
        arguments["origin_jti"], arguments["customer_id"], TOOL
    )
    output = (
        fault(planned) if planned is not None else block_card(stores, arguments, now())
    )
    check_output(TOOL, output)
    return output


def handler(event: Any, context: Any) -> dict[str, Any]:
    tool_name(context)
    return answer(event, stores)

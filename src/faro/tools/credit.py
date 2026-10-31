"""
get_available_credit (ADR-0004, Tools, and its amendment of 2026-10-01; POL-01, POL-12, POL-18, POL-22 to POL-24,
POL-33). The tool computes the figure, in decimal to two places, so the agent never does. Only an active credit card
gets one, and only it carries its limit and balance: a debit card's balance isn't an account's, and a missing limit is
never read as zero or unlimited. The card's status is the sign-in's overlay's when a block wrote one there.
"""

from decimal import Decimal
from typing import Any

from faro.tools.cards import SERVED_IN_FULL, CustomerMissingError
from faro.tools.sandbox import Stores
from faro.tools.store import Record, ToolsData
from faro.tools.transactions import number

CENT = Decimal("0.01")
DEBIT = "Tarjeta Débito"


def _stamped(data: ToolsData) -> dict[str, Any]:
    metadata = data.metadata()
    return {"stamp": metadata["stamp"], "clock": metadata["clock"]}


def decimal(value: Any) -> Decimal:
    # A float from the in-memory store goes through its shortest repr, as DynamoDB's Decimals already are.
    return value if isinstance(value, Decimal) else Decimal(str(value))


def money(value: Decimal) -> int | float:
    return number(value.quantize(CENT))


def credit(card: Record) -> dict[str, Any]:
    shown = {
        k: card[k]
        for k in ("card_id", "product_type", "last_four", "product_status", "currency")
    }
    if card["product_type"] == DEBIT:
        return {**shown, "availability": "debit_card"}
    if card["product_status"] != "Active":
        return {**shown, "availability": "not_active"}
    balance = decimal(card["current_balance"])
    figures: dict[str, Any] = {"current_balance": money(balance)}
    if card["credit_limit"] is None:
        return {**shown, "availability": "no_limit", "credit_limit": None, **figures}
    limit = decimal(card["credit_limit"])
    left = limit - balance
    return {
        **shown,
        "availability": "available" if left >= 0 else "over_limit",
        "credit_limit": money(limit),
        **figures,
        "available_credit": money(max(left, Decimal(0))),
        "over_limit_by": money(max(-left, Decimal(0))),
    }


def get_available_credit(stores: Stores, arguments: dict[str, Any]) -> dict[str, Any]:
    data, customer_id, card_id = (
        stores.data,
        arguments["customer_id"],
        arguments["card_id"],
    )
    stamped = _stamped(data)
    customer = data.customer(customer_id)
    if customer is None:
        raise CustomerMissingError()
    if customer["customer_status"] not in SERVED_IN_FULL:
        return {"outcome": "refused", **stamped, "refusal": "not_served"}
    card = data.credit(customer_id, card_id)
    if card is None:
        return {"outcome": "not_found", **stamped}
    status = stores.overlay.status(arguments["origin_jti"], customer_id, card_id)
    if status is not None:
        card = {**card, "product_status": status}
    return {"outcome": "ok", **stamped, "card": credit(card)}

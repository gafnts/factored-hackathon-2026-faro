"""
list_cards and get_card (ADR-0004, Tools; POL-08, POL-12, POL-13 to POL-16, POL-21, POL-30, POL-33). Every key is built
from the input's customer_id, which Cedar has matched to the token, so a customer reads their own partition only and
another customer's card reads as not found. A card's status is the sign-in's overlay's when a block wrote one there,
and the tools' data's otherwise; origin_jti, which Cedar matched to the token too, chooses the overlay.
"""

from typing import Any

from faro.tools.sandbox import Stores
from faro.tools.store import Record, ToolsData

# Closed and Suspended customers may only block a card; their status is never returned (POL-12).
SERVED_IN_FULL = ("Active", "Inactive")

SUMMARY = (
    "card_id",
    "product_type",
    "last_four",
    "product_status",
    "past_expiration",
    "updated_after_as_of",
)
DETAIL = (*SUMMARY[:4], "opening_date", "expiration_date", *SUMMARY[4:])


class CustomerMissingError(RuntimeError):
    """
    The token names a customer the tools' data doesn't hold: a user created for a customer the export lacks, which is a
    fault in the deployment, not an outcome the contract has.
    """

    def __init__(self) -> None:
        super().__init__(
            "the tools' data holds no customer with the token's customer_id"
        )


def _stamped(data: ToolsData) -> dict[str, Any]:
    metadata = data.metadata()
    return {"stamp": metadata["stamp"], "clock": metadata["clock"]}


def _only(record: Record, names: tuple[str, ...]) -> dict[str, Any]:
    return {name: record[name] for name in names}


def list_cards(stores: Stores, arguments: dict[str, Any]) -> dict[str, Any]:
    data, customer_id = stores.data, arguments["customer_id"]
    customer = data.customer(customer_id)
    if customer is None:
        raise CustomerMissingError()
    written = stores.overlay.statuses(arguments["origin_jti"], customer_id)
    cards = sorted(
        data.cards(customer_id),
        key=lambda card: (card["product_type"], card["last_four"], card["card_id"]),
    )
    return {
        "outcome": "ok",
        **_stamped(data),
        "customer": {
            "served_in_full": customer["customer_status"] in SERVED_IN_FULL,
            "country": customer["country"],
        },
        "cards": [_only(overlaid(card, written), SUMMARY) for card in cards],
    }


def get_card(stores: Stores, arguments: dict[str, Any]) -> dict[str, Any]:
    card = sandboxed_card(stores, arguments)
    if card is None:
        return {"outcome": "not_found", **_stamped(stores.data)}
    return {"outcome": "ok", **_stamped(stores.data), "card": _only(card, DETAIL)}


def overlaid(card: Record, written: dict[str, str]) -> Record:
    status = written.get(card["card_id"])
    return card if status is None else {**card, "product_status": status}


def sandboxed_card(stores: Stores, arguments: dict[str, Any]) -> Record | None:
    """
    The card as the sign-in's sandbox shows it, which the block reads too (POL-34).
    """
    customer_id, card_id = arguments["customer_id"], arguments["card_id"]
    card = stores.data.card(customer_id, card_id)
    if card is None:
        return None
    status = stores.overlay.status(arguments["origin_jti"], customer_id, card_id)
    return card if status is None else {**card, "product_status": status}

"""
block_card (ADR-0004, The confirmation; POL-33 to POL-37; CTL-02, AI-05). It blocks only under a confirmation the
Runtime brought to confirmed when the customer pressed the confirm control, for this customer, card, and reason, and
before its time limit; anything else is refused alike, so the answer says nothing about anyone else's confirmations.
It uses the confirmation up in one transaction with the first write to the sandbox, reads the card back, and writes
again only after a read-back that doesn't show Blocked, three writes at most, counting each on the confirmation. A
second call with the same confirmation writes nothing and reports the first's outcome, unless the first stopped before
it had one; then it reads first and goes on within the same three writes. No confirmation blocks twice, and no block
runs without one.
"""

from datetime import datetime
from typing import Any

from faro.tools.cards import sandboxed_card
from faro.tools.fixtures import signed_in
from faro.tools.sandbox import BLOCKED, BlockStores, Stores
from faro.tools.store import Record

WRITES = 3


def _stamped(stores: BlockStores) -> dict[str, Any]:
    metadata = stores.data.metadata()
    return {"stamp": metadata["stamp"], "clock": metadata["clock"]}


def _refused(stores: BlockStores, refusal: str, **fields: Any) -> dict[str, Any]:
    return {"outcome": "refused", **_stamped(stores), "refusal": refusal, **fields}


def _matches(confirmation: Record | None, arguments: dict[str, Any]) -> bool:
    return confirmation is not None and all(
        confirmation[k] == arguments[k] for k in ("customer_id", "card_id", "reason")
    )


def _card(stores: BlockStores, confirmation: Record) -> Record | None:
    """
    The card as the confirmation's sign-in sees it, fixtures included.
    """
    arguments = {k: confirmation[k] for k in ("customer_id", "card_id", "origin_jti")}
    return sandboxed_card(
        signed_in(Stores(stores.data, stores.sandbox), arguments), arguments
    )


def _read_back(stores: BlockStores, confirmation: Record) -> str:
    card = _card(stores, confirmation)
    if card is None:
        raise LookupError("the tools' data no longer holds the confirmed card")
    return str(card["product_status"])


def _done(
    stores: BlockStores, confirmation: Record, repeated: bool, **block: Any
) -> dict[str, Any]:
    return {
        "outcome": "ok",
        **_stamped(stores),
        "card_id": confirmation["card_id"],
        "reason": confirmation["reason"],
        "confirmation_id": confirmation["confirmation_id"],
        **block,
        "repeated": repeated,
    }


def _repeated(stores: BlockStores, confirmation: Record) -> dict[str, Any]:
    return _done(
        stores,
        confirmation,
        True,
        attempts=confirmation["attempts"],
        block_outcome=confirmation["outcome"],
        read_back=confirmation["read_back"],
    )


def _verify(stores: BlockStores, confirmation: Record, now: datetime) -> dict[str, Any]:
    """
    From a confirmation already used, whose attempts count the writes so far (POL-37).
    """
    sandbox, confirmation_id = stores.sandbox, confirmation["confirmation_id"]
    attempts = confirmation["attempts"]
    read_back = _read_back(stores, confirmation)
    while read_back != BLOCKED and attempts < WRITES:
        if not sandbox.write_again(confirmation, attempts, now):
            # Another call wrote or settled it meanwhile: carry on from what it left.
            return _carry_on(stores, sandbox.confirmation(confirmation_id), now)
        attempts += 1
        read_back = _read_back(stores, confirmation)
    outcome = "verified" if read_back == BLOCKED else "not_verified"
    if not sandbox.settle(confirmation_id, attempts, outcome, read_back):
        return _carry_on(stores, sandbox.confirmation(confirmation_id), now)
    return _done(
        stores,
        confirmation,
        False,
        attempts=attempts,
        block_outcome=outcome,
        read_back=read_back,
    )


def _carry_on(
    stores: BlockStores, confirmation: Record | None, now: datetime
) -> dict[str, Any]:
    assert confirmation is not None and confirmation["status"] == "consumed"
    if "outcome" in confirmation:
        return _repeated(stores, confirmation)
    return _verify(stores, confirmation, now)


def block_card(
    stores: BlockStores, arguments: dict[str, Any], now: datetime
) -> dict[str, Any]:
    sandbox = stores.sandbox
    confirmation = sandbox.confirmation(arguments["confirmation_id"])
    if not _matches(confirmation, arguments):
        return _refused(stores, "not_confirmed")
    assert confirmation is not None
    if confirmation["status"] == "consumed":
        return _carry_on(stores, confirmation, now)
    if (
        confirmation["status"] != "confirmed"
        or confirmation["expires_at"] <= now.timestamp()
    ):
        return _refused(stores, "not_confirmed")
    card = _card(stores, confirmation)
    if card is None:
        return {"outcome": "not_found", **_stamped(stores)}
    if card["product_status"] != "Active":
        return _refused(stores, "not_active", product_status=card["product_status"])
    if not sandbox.consume(confirmation, now):
        # Used or ended by another call between the read and the write.
        held = sandbox.confirmation(confirmation["confirmation_id"])
        if held is None or held["status"] != "consumed":
            return _refused(stores, "not_confirmed")
        return _carry_on(stores, held, now)
    return _verify(stores, {**confirmation, "status": "consumed", "attempts": 1}, now)

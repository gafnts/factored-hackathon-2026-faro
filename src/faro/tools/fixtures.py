"""
The tools' data as a sign-in sees it (ADR-0004's amendment of 2026-10-01; EVL-02, EVL-05): the export's records, and
the fixtures the evaluation's harness wrote under the sign-in for the customer, merged as if they had been exported. A
fixture card is listed among the customer's cards and read by its ID; a fixture transaction is listed on its card, in
the 90-day window, by listed_at among the export's, so pages and cursors run across both. Fixture IDs carry prefixes no
record of the snapshot does, which is how a record shows it is a fixture. A fixture that breaks its contract, or whose
keys don't match its own IDs, fails the call rather than being dropped.
"""

from typing import Any

from faro.tools.sandbox import (
    FIXTURE_CARD,
    FIXTURE_TRANSACTION,
    Overlay,
    OverlayItemError,
    Stores,
    checked,
)
from faro.tools.store import CARD, CREDIT, TRANSACTION, Record, ToolsData
from faro.tools.transactions import window

CARD_PREFIX = "PRD-FIXTURE"
TRANSACTION_PREFIX = "TRX-FIXTURE"


def _keyed(definition: str, name: str, expected: str, item: Record) -> None:
    if item[name] != expected:
        raise OverlayItemError(definition, f"/{name}", "keys")


def _only(record: Record, names: tuple[str, ...]) -> Record:
    return {name: record[name] for name in names if name in record}


class SignInData:
    def __init__(
        self, data: ToolsData, overlay: Overlay, sign_in: str, customer_id: str
    ) -> None:
        self.data = data
        self.overlay = overlay
        self.sign_in = sign_in
        self.customer_id = customer_id
        self._cards: dict[str, Record] | None = None
        self._transactions: list[Record] | None = None

    def _customers(self, item: Record) -> bool:
        checked("fixture_card", item)
        _keyed("fixture_card", "item", f"{FIXTURE_CARD}{item['card_id']}", item)
        return bool(item["customer_id"] == self.customer_id)

    def _fixture_cards(self, customer_id: str) -> dict[str, Record]:
        if customer_id != self.customer_id:
            return {}
        if self._cards is None:
            self._cards = {
                item["card_id"]: item
                for item in self.overlay.fixtures(self.sign_in, FIXTURE_CARD)
                if self._customers(item)
            }
        return self._cards

    def _fixture_card(self, customer_id: str, card_id: str) -> Record | None:
        """
        Read by its key, so a card read needs no query of the overlay, and a card without the prefix none at all.
        """
        if customer_id != self.customer_id:
            return None
        if self._cards is not None:
            return self._cards.get(card_id)
        item = self.overlay.fixture_card(self.sign_in, card_id)
        return item if item is not None and self._customers(item) else None

    def _fixture_transactions(self, customer_id: str) -> list[Record]:
        if customer_id != self.customer_id:
            return []
        if self._transactions is None:
            span = window(self.data.metadata()["clock"])
            self._transactions = []
            for item in self.overlay.fixtures(self.sign_in, FIXTURE_TRANSACTION):
                checked("fixture_transaction", item)
                tid, owner = item["transaction_id"], item["customer_id"]
                _keyed(
                    "fixture_transaction", "item", f"{FIXTURE_TRANSACTION}{tid}", item
                )
                _keyed(
                    "fixture_transaction",
                    "card_key",
                    f"{owner}#{item['card_id']}",
                    item,
                )
                _keyed(
                    "fixture_transaction",
                    "listed_at",
                    f"{item['transaction_date']}#{tid}",
                    item,
                )
                # Outside the window, the export would have left it out (POL-25).
                inside = span["from"] < item["transaction_date"] <= span["to"]
                if owner == self.customer_id and inside:
                    self._transactions.append(item)
        return self._transactions

    def metadata(self) -> Record:
        return self.data.metadata()

    def customer(self, customer_id: str) -> Record | None:
        return self.data.customer(customer_id)

    def cards(self, customer_id: str) -> list[Record]:
        fixtures = self._fixture_cards(customer_id).values()
        return [*self.data.cards(customer_id), *(_only(c, CARD) for c in fixtures)]

    def card(self, customer_id: str, card_id: str) -> Record | None:
        if not card_id.startswith(CARD_PREFIX):
            return self.data.card(customer_id, card_id)
        fixture = self._fixture_card(customer_id, card_id)
        return None if fixture is None else _only(fixture, CARD)

    def credit(self, customer_id: str, card_id: str) -> Record | None:
        if not card_id.startswith(CARD_PREFIX):
            return self.data.credit(customer_id, card_id)
        fixture = self._fixture_card(customer_id, card_id)
        return None if fixture is None else _only(fixture, CREDIT)

    def transactions(
        self, customer_id: str, card_id: str, before: str | None, limit: int
    ) -> list[Record]:
        fixtures = [
            _only(t, TRANSACTION)
            for t in self._fixture_transactions(customer_id)
            if t["card_id"] == card_id and (before is None or t["listed_at"] < before)
        ]
        exported = (
            []
            if card_id.startswith(CARD_PREFIX)
            else self.data.transactions(customer_id, card_id, before, limit)
        )
        merged = sorted(
            [*exported, *fixtures], key=lambda t: t["listed_at"], reverse=True
        )
        return merged[:limit]


def signed_in(stores: Stores, arguments: dict[str, Any]) -> Stores:
    """
    The stores as the call's sign-in and customer see them, which Cedar matched to the token.
    """
    data = SignInData(
        stores.data, stores.overlay, arguments["origin_jti"], arguments["customer_id"]
    )
    return Stores(data=data, overlay=stores.overlay)

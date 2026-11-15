"""
The guest entry's curated customers, as make guest-list writes them and the guest entry reads them (ADR-0007, The guest
entry): one Parameter Store entry under the environment's prefix, holding each customer's ID and synthetic name.
Names are identity data, so the list lives in the stack and never in the repository (SEC-03). A list that names a
held-out customer is refused on both sides, so no held-out customer's records reach a model through a visitor.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from faro.split import held_out

VERSION = 1
CUSTOMER_ID = re.compile(r"^CLI-[A-Z0-9]{12}$")
# Parameter Store's standard tier holds 4 KB; 24 customers take about 1.5.
MAX_BYTES = 4096


class ListError(Exception):
    pass


@dataclass(frozen=True)
class Customer:
    customer_id: str
    name: str


def parameter(prefix: str) -> str:
    return f"/{prefix}/guest-entry/customers"


def _checked(customers: Sequence[Customer]) -> tuple[Customer, ...]:
    if not customers:
        raise ListError("the guest list is empty")
    for customer in customers:
        if not CUSTOMER_ID.match(customer.customer_id) or not customer.name.strip():
            raise ListError("the guest list holds a malformed customer")
        if held_out(customer.customer_id):
            raise ListError("the guest list names a held-out customer")
    if len({c.customer_id for c in customers}) != len(customers):
        raise ListError("the guest list names a customer twice")
    return tuple(customers)


def encode(snapshot: str, customers: Sequence[Customer]) -> str:
    body = json.dumps(
        {
            "version": VERSION,
            "snapshot": snapshot,
            "customers": [
                {"customer_id": c.customer_id, "name": c.name}
                for c in _checked(customers)
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(body.encode()) > MAX_BYTES:
        raise ListError("the guest list is past Parameter Store's standard 4 KB")
    return body


def decode(text: str) -> tuple[Customer, ...]:
    try:
        body = json.loads(text)
        if body["version"] != VERSION:
            raise ListError(f"the guest list is version {body['version']}")
        customers = [Customer(c["customer_id"], c["name"]) for c in body["customers"]]
        if not all(
            isinstance(v, str) for c in customers for v in (c.customer_id, c.name)
        ):
            raise TypeError("a customer's ID or name isn't text")
    except (ValueError, KeyError, TypeError) as error:
        raise ListError("the guest list isn't one make guest-list wrote") from error
    return _checked(customers)

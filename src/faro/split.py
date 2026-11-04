"""
The split by customer, shared by the analysis, the personas, and the evaluation (ADR-0003, ADR-0005; DML-09): a customer
is held out when the MD5 of their customer_id, read as an integer, is divisible by 5, so all of a customer's rows fall on
one side. Wording is split by its source instead (ADR-0011, Wording sources; faro.evaluation.families).
"""

import hashlib

HELD_OUT_EVERY = 5


def _digest(value: str) -> int:
    return int(hashlib.md5(value.encode(), usedforsecurity=False).hexdigest(), 16)


def held_out(customer_id: str) -> bool:
    return _digest(customer_id) % HELD_OUT_EVERY == 0

"""
A handoff checked against the execution record before it is filed (ADR-0004, The handoff, and its amendment of
2026-09-30; CTL-05, OPS-02). The schema checks a payload's shape, not where its facts came from: here each piece of
evidence must be a tool call the turns hold, of the same tool, time, and outcome; each verified fact's value must be in
its call's recorded result, on the same record and field; and each action's outcome must be the one the record shows,
from the block's recorded result or the confirmation's recorded end. What fails is left out and named by its path and
rule, never its value. The facts file_handoff adds and its own call, which the record holds only once it returns, are
exempt; any other fact that cites that call is not.
"""

import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol

from boto3.dynamodb.types import TypeDeserializer

from faro.policy.payload import failure
from faro.tools.cases import plain

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient

Entry = dict[str, Any]
# A tool call's recorded outcome, as a handoff's evidence names it (the execution record's contract).
OUTCOMES = {
    "ok": "ok",
    "not_found": "ok",
    "refused": "ok",
    "invalid_input": "error",
    "failed": "error",
    "denied": "denied",
}
ENDED = {"declined_by_customer": "cancelled", "lapsed": "lapsed"}
KEYS = {"card": "card_id", "transaction": "transaction_id"}


class Records(Protocol):
    def turns(
        self,
        sign_in: str,
        prefixes: Sequence[str],
        attributes: Sequence[str] | None = None,
    ) -> list[Entry]:
        """
        Every entry of the turns, or only the named attributes of each, which is all the console's role may read.
        """
        ...


class DynamoRecords:
    def __init__(self, client: "DynamoDBClient", table: str) -> None:
        self._client = client
        self._table = table
        self._deserializer = TypeDeserializer()

    def turns(
        self,
        sign_in: str,
        prefixes: Sequence[str],
        attributes: Sequence[str] | None = None,
    ) -> list[Entry]:
        found: list[Entry] = []
        projection: dict[str, Any] = {}
        if attributes is not None:
            aliases = {f"#a{i}": name for i, name in enumerate(attributes)}
            projection = {
                "ProjectionExpression": ", ".join(aliases),
                "ExpressionAttributeNames": aliases,
            }
        for prefix in prefixes:
            start: dict[str, Any] = {}
            while True:
                response = self._client.query(
                    TableName=self._table,
                    KeyConditionExpression="sign_in = :sign_in AND begins_with(entry_key, :turn)",
                    ExpressionAttributeValues={
                        ":sign_in": {"S": sign_in},
                        ":turn": {"S": f"{prefix}#"},
                    },
                    ConsistentRead=True,
                    **projection,
                    **start,
                )
                found += [
                    plain({k: self._deserializer.deserialize(v) for k, v in i.items()})
                    for i in response.get("Items", [])
                ]
                if "LastEvaluatedKey" not in response:
                    break
                start = {"ExclusiveStartKey": response["LastEvaluatedKey"]}
        return found


class MemoryRecords:
    def __init__(self, entries: Iterable[Mapping[str, Any]] = ()) -> None:
        self.entries = [json.loads(json.dumps(dict(e))) for e in entries]

    def turns(
        self,
        sign_in: str,
        prefixes: Sequence[str],
        attributes: Sequence[str] | None = None,
    ) -> list[Entry]:
        found = [
            e
            for e in self.entries
            if e["sign_in"] == sign_in
            and any(e["entry_key"].startswith(f"{p}#") for p in prefixes)
        ]
        if attributes is None:
            return found
        return [{k: e[k] for k in attributes if k in e} for e in found]


def rows(node: Any, card_id: Any = None) -> Iterator[dict[str, Any]]:
    """
    Every record a tool's result holds, a transaction with its card's ID as product_id, and a block's read-back as the
    card's status.
    """
    if isinstance(node, dict):
        card_id = node.get("card_id", card_id)
        row = dict(node)
        if "transaction_id" in node and card_id is not None:
            row.setdefault("product_id", card_id)
        if "read_back" in node:
            row.setdefault("product_status", node["read_back"])
        yield row
        for value in node.values():
            yield from rows(value, card_id)
    elif isinstance(node, list):
        for value in node:
            yield from rows(value, card_id)


def holds(call: Entry, fact: Mapping[str, Any]) -> bool:
    result = call.get("result")
    subject, field, value = fact["subject"], fact["field"], fact["value"]
    if subject == "customer":
        customer = result.get("customer") if isinstance(result, dict) else None
        return (
            call["input"].get("customer_id") == fact["id"]
            and isinstance(customer, dict)
            and field in customer
            and customer[field] == value
        )
    key = KEYS.get(subject)
    return key is not None and any(
        row.get(key) == fact["id"] and field in row and row[field] == value
        for row in rows(result)
    )


def _block_shows(call: Entry | None, action: Mapping[str, Any]) -> bool:
    result = (call or {}).get("result") or {}
    return (
        call is not None
        and call["tool"] == "block_card"
        and result.get("card_id") == action["card_id"]
        and result.get("confirmation_id") == action["confirmation_id"]
        and result.get("block_outcome") == action["outcome"]
    )


def checked_against(
    payload: dict[str, Any],
    entries: Sequence[Entry],
    own_call: str,
    added: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    calls = {e["call_id"]: e for e in entries if e["kind"] == "tool_call"}
    ended = {
        (e["confirmation_id"], e["to"]) for e in entries if e["kind"] == "confirmation"
    }
    errors: list[dict[str, str]] = []

    kept_evidence = []
    for i, item in enumerate(payload["evidence"]):
        call = calls.get(item["call_id"])
        if item["call_id"] == own_call:
            kept_evidence.append(item)
        elif call is None:
            errors.append(failure(["evidence", i], "unrecorded"))
        elif (call["tool"], OUTCOMES[call["outcome"]], call["called_at"]) != (
            item["tool"],
            item["outcome"],
            item["called_at"],
        ):
            errors.append(failure(["evidence", i], "mismatch"))
        else:
            kept_evidence.append(item)
    cited = {item["call_id"] for item in kept_evidence}

    kept_facts = []
    for i, fact in enumerate(payload["verified_facts"]):
        call = calls.get(fact["evidence"])
        if fact in added or (
            fact["evidence"] in cited and call is not None and holds(call, fact)
        ):
            kept_facts.append(fact)
        else:
            errors.append(failure(["verified_facts", i], "unrecorded"))

    kept_actions = []
    for i, action in enumerate(payload["actions"]):
        if action["outcome"] in ENDED:
            shown = (action["confirmation_id"], ENDED[action["outcome"]]) in ended
        else:
            shown = any(
                e in cited and _block_shows(calls.get(e), action)
                for e in action["evidence"]
            )
        if shown:
            kept_actions.append(action)
        else:
            errors.append(failure(["actions", i], "unrecorded"))

    return {
        **payload,
        "evidence": kept_evidence,
        "verified_facts": kept_facts,
        "actions": kept_actions,
    }, errors

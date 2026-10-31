"""
The sign-in's sandbox and the confirmations, as the tools see them (ADR-0004, Stores and The confirmation, and its
amendment of 2026-10-01; POL-33, POL-36, POL-37, POL-48). The overlay holds what a block wrote, keyed by the sign-in,
then the card, and a read tool shows a card's status from it over the tools' data, in the sign-in that wrote it only,
and only for the customer the item names. It also holds what the evaluation's harness writes under a sign-in: fixtures,
which the tools merge into their reads, and fault plans, each of which fails a tool's attempts until its failures run
out, taken one at a time in a conditional update. Every read is strongly consistent, so the read-back after a write sees
it, and names the attributes it reads, none of them is_fraud, which only file_handoff reads (POL-40). The block's own
store also reads the confirmation the Runtime created and brought to confirmed, and changes it only on conditions
DynamoDB checks: it uses it up in one transaction with the first write, and counts every write after it. The in-memory
stores serve the tests and the evaluation's harness with the same logic the Lambdas run.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from faro.contracts import schema, validator
from faro.tools.cases import plain
from faro.tools.store import Record, ToolsData

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient

CARD = "CARD#"
FIXTURE_CARD = "FIXTURE#CARD#"
FIXTURE_TRANSACTION = "FIXTURE#TRX#"
FAULT = "FAULT#"
KEPT = timedelta(hours=24)
BLOCKED = "Blocked"
HIDDEN = "is_fraud"


def readable(definition: str) -> tuple[str, ...]:
    """
    The attributes of an overlay item the tools read: every one its contract names but is_fraud.
    """
    properties = schema("overlay")["$defs"][definition]["properties"]
    return tuple(name for name in properties if name != HIDDEN)


class OverlayItemError(RuntimeError):
    """
    Names where a fixture or a fault plan breaks its contract, never the value, which may be anything a harness wrote.
    """

    def __init__(self, definition: str, path: str, rule: str) -> None:
        super().__init__(f"a {definition} item breaks {rule} at {path or '/'}")


def checked(definition: str, item: Record) -> Record:
    # The tools never read is_fraud, so a fixture transaction is checked as if it held one.
    instance = {HIDDEN: False, **item} if definition == "fixture_transaction" else item
    error = next(validator("overlay", definition).iter_errors(instance), None)
    if error is not None:
        path = "".join(f"/{p}" for p in error.absolute_path)
        raise OverlayItemError(definition, path, str(error.validator))
    return item


class Overlay(Protocol):
    def statuses(self, sign_in: str, customer_id: str) -> dict[str, str]: ...

    def status(self, sign_in: str, customer_id: str, card_id: str) -> str | None: ...

    def fixtures(self, sign_in: str, prefix: str) -> list[Record]:
        """
        The sign-in's fixture items whose key begins with prefix, whatever customer they name, without is_fraud.
        """
        ...

    def fixture_card(self, sign_in: str, card_id: str) -> Record | None:
        """
        One fixture card, read by its key, whatever customer it names.
        """
        ...

    def take_fault(self, sign_in: str, customer_id: str, tool: str) -> str | None:
        """
        One failure from the sign-in's plan for the tool, when it names the customer and has one left: its error.
        """
        ...


class FixtureFlags(Protocol):
    def fixture_is_fraud(
        self, sign_in: str, customer_id: str, transaction_id: str
    ) -> bool | None: ...


@dataclass(frozen=True)
class Stores:
    data: ToolsData
    overlay: Overlay


def card_item(card_id: str) -> str:
    return f"{CARD}{card_id}"


def _projection(names: Iterable[str]) -> dict[str, Any]:
    aliases = {f"#a{i}": name for i, name in enumerate(names)}
    return {
        "ProjectionExpression": ", ".join(aliases),
        "ExpressionAttributeNames": aliases,
    }


class DynamoOverlay:
    def __init__(self, client: "DynamoDBClient", table: str) -> None:
        self._client = client
        self._table = table
        self._deserializer = TypeDeserializer()

    def _plain(self, item: Mapping[str, Any]) -> Record:
        found: Record = plain(
            {k: self._deserializer.deserialize(v) for k, v in item.items()}
        )
        return found

    def _query(self, sign_in: str, prefix: str, names: Iterable[str]) -> list[Record]:
        projection = _projection(names)
        found: list[Record] = []
        start: dict[str, Any] = {}
        while True:
            response = self._client.query(
                TableName=self._table,
                KeyConditionExpression="#sign_in = :sign_in AND begins_with(#item, :prefix)",
                ExpressionAttributeNames={
                    **projection["ExpressionAttributeNames"],
                    "#sign_in": "sign_in",
                    "#item": "item",
                },
                ExpressionAttributeValues={
                    ":sign_in": {"S": sign_in},
                    ":prefix": {"S": prefix},
                },
                ProjectionExpression=projection["ProjectionExpression"],
                ConsistentRead=True,
                **start,
            )
            found += [self._plain(item) for item in response.get("Items", [])]
            if "LastEvaluatedKey" not in response:
                return found
            start = {"ExclusiveStartKey": response["LastEvaluatedKey"]}

    def _get(self, sign_in: str, item: str, names: Iterable[str]) -> Record | None:
        found = self._client.get_item(
            TableName=self._table,
            Key={"sign_in": {"S": sign_in}, "item": {"S": item}},
            ConsistentRead=True,
            **_projection(names),
        ).get("Item")
        return None if found is None else self._plain(found)

    def statuses(self, sign_in: str, customer_id: str) -> dict[str, str]:
        return {
            item["card_id"]: item["product_status"]
            for item in self._query(sign_in, CARD, readable("card_status"))
            if item["customer_id"] == customer_id
        }

    def status(self, sign_in: str, customer_id: str, card_id: str) -> str | None:
        item = self._get(sign_in, card_item(card_id), readable("card_status"))
        if item is None or item["customer_id"] != customer_id:
            return None
        status: str = item["product_status"]
        return status

    def fixtures(self, sign_in: str, prefix: str) -> list[Record]:
        definition = "fixture_card" if prefix == FIXTURE_CARD else "fixture_transaction"
        return self._query(sign_in, prefix, readable(definition))

    def fixture_card(self, sign_in: str, card_id: str) -> Record | None:
        return self._get(sign_in, f"{FIXTURE_CARD}{card_id}", readable("fixture_card"))

    def take_fault(self, sign_in: str, customer_id: str, tool: str) -> str | None:
        plan = self._get(sign_in, f"{FAULT}{tool}", readable("fault_plan"))
        if plan is None:
            return None
        checked("fault_plan", plan)
        if plan["customer_id"] != customer_id or plan["failures"] <= 0:
            return None
        try:
            self._client.update_item(
                TableName=self._table,
                Key={"sign_in": {"S": sign_in}, "item": {"S": f"{FAULT}{tool}"}},
                UpdateExpression="SET #failures = #failures - :one",
                ConditionExpression="#failures > :none AND #customer_id = :customer_id",
                ExpressionAttributeNames={
                    "#failures": "failures",
                    "#customer_id": "customer_id",
                },
                ExpressionAttributeValues={
                    ":one": {"N": "1"},
                    ":none": {"N": "0"},
                    ":customer_id": {"S": customer_id},
                },
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise
        taken: str = plan["error"]
        return taken

    def fixture_is_fraud(
        self, sign_in: str, customer_id: str, transaction_id: str
    ) -> bool | None:
        item = self._get(
            sign_in, f"{FIXTURE_TRANSACTION}{transaction_id}", ("customer_id", HIDDEN)
        )
        if item is None or item["customer_id"] != customer_id or HIDDEN not in item:
            return None
        return bool(item[HIDDEN])


class MemoryOverlay:
    def __init__(self, items: Iterable[Mapping[str, Any]] = ()) -> None:
        self.items = {(i["sign_in"], i["item"]): dict(i) for i in items}

    def put(self, item: Mapping[str, Any]) -> None:
        self.items[(item["sign_in"], item["item"])] = dict(item)

    def statuses(self, sign_in: str, customer_id: str) -> dict[str, str]:
        return {
            item["card_id"]: item["product_status"]
            for (held, key), item in sorted(self.items.items())
            if held == sign_in
            and key.startswith(CARD)
            and item["customer_id"] == customer_id
        }

    def status(self, sign_in: str, customer_id: str, card_id: str) -> str | None:
        item = self.items.get((sign_in, card_item(card_id)))
        if item is None or item["customer_id"] != customer_id:
            return None
        status: str = item["product_status"]
        return status

    def fixtures(self, sign_in: str, prefix: str) -> list[Record]:
        return [
            {k: v for k, v in item.items() if k != HIDDEN}
            for (held, key), item in sorted(self.items.items())
            if held == sign_in and key.startswith(prefix)
        ]

    def fixture_card(self, sign_in: str, card_id: str) -> Record | None:
        item = self.items.get((sign_in, f"{FIXTURE_CARD}{card_id}"))
        return None if item is None else dict(item)

    def take_fault(self, sign_in: str, customer_id: str, tool: str) -> str | None:
        plan = self.items.get((sign_in, f"{FAULT}{tool}"))
        if plan is None:
            return None
        checked("fault_plan", plan)
        if plan["customer_id"] != customer_id or plan["failures"] <= 0:
            return None
        plan["failures"] -= 1
        taken: str = plan["error"]
        return taken

    def fixture_is_fraud(
        self, sign_in: str, customer_id: str, transaction_id: str
    ) -> bool | None:
        item = self.items.get((sign_in, f"{FIXTURE_TRANSACTION}{transaction_id}"))
        if item is None or item["customer_id"] != customer_id or HIDDEN not in item:
            return None
        return bool(item[HIDDEN])


class Sandbox(Overlay, Protocol):
    def confirmation(self, confirmation_id: str) -> Record | None: ...

    def consume(self, confirmation: Record, at: datetime) -> bool: ...

    def write_again(
        self, confirmation: Record, attempts: int, at: datetime
    ) -> bool: ...

    def settle(
        self, confirmation_id: str, attempts: int, outcome: str, read_back: str
    ) -> bool: ...


@dataclass(frozen=True)
class BlockStores:
    data: ToolsData
    sandbox: Sandbox


def wall_time(at: datetime) -> str:
    return at.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def blocked_item(confirmation: Record, at: datetime) -> Record:
    """
    Keyed by the confirmation's sign-in, whatever sign-in the call names (ADR-0004, The confirmation).
    """
    return {
        "sign_in": confirmation["origin_jti"],
        "item": card_item(confirmation["card_id"]),
        "customer_id": confirmation["customer_id"],
        "card_id": confirmation["card_id"],
        "product_status": BLOCKED,
        "reason": confirmation["reason"],
        "confirmation_id": confirmation["confirmation_id"],
        "written_at": wall_time(at),
        "ttl": int((at + KEPT).timestamp()),
    }


def _names(*names: str) -> dict[str, str]:
    return {f"#{name}": name for name in names}


class DynamoSandbox(DynamoOverlay):
    def __init__(
        self, client: "DynamoDBClient", overlay_table: str, confirmations_table: str
    ) -> None:
        super().__init__(client, overlay_table)
        self._confirmations = confirmations_table
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()

    def _item(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return {k: self._serializer.serialize(v) for k, v in record.items()}

    def _values(self, **values: Any) -> dict[str, Any]:
        return {f":{k}": self._serializer.serialize(v) for k, v in values.items()}

    def confirmation(self, confirmation_id: str) -> Record | None:
        item = self._client.get_item(
            TableName=self._confirmations,
            Key={"confirmation_id": {"S": confirmation_id}},
            ConsistentRead=True,
        ).get("Item")
        if item is None:
            return None
        record = {k: self._deserializer.deserialize(v) for k, v in item.items()}
        # DynamoDB numbers come back as Decimals.
        return {
            k: int(v) if k in ("expires_at", "ttl", "attempts") else v
            for k, v in record.items()
        }

    def _transact(self, items: list[Any]) -> bool:
        try:
            self._client.transact_write_items(TransactItems=items)
        except ClientError as error:
            if error.response["Error"]["Code"] == "TransactionCanceledException":
                return False
            raise
        return True

    def consume(self, confirmation: Record, at: datetime) -> bool:
        return self._transact(
            [
                {
                    "Update": {
                        "TableName": self._confirmations,
                        "Key": {
                            "confirmation_id": {"S": confirmation["confirmation_id"]}
                        },
                        "UpdateExpression": "SET #status = :consumed, #consumed_at = :at, #attempts = :one",
                        "ConditionExpression": (
                            "#status = :confirmed AND #customer_id = :customer_id AND #card_id = :card_id"
                            " AND #reason = :reason AND #expires_at > :now"
                        ),
                        "ExpressionAttributeNames": _names(
                            "status",
                            "consumed_at",
                            "attempts",
                            "customer_id",
                            "card_id",
                            "reason",
                            "expires_at",
                        ),
                        "ExpressionAttributeValues": self._values(
                            consumed="consumed",
                            confirmed="confirmed",
                            at=wall_time(at),
                            one=1,
                            customer_id=confirmation["customer_id"],
                            card_id=confirmation["card_id"],
                            reason=confirmation["reason"],
                            now=int(at.timestamp()),
                        ),
                    }
                },
                {
                    "Put": {
                        "TableName": self._table,
                        "Item": self._item(blocked_item(confirmation, at)),
                    }
                },
            ]
        )

    def write_again(self, confirmation: Record, attempts: int, at: datetime) -> bool:
        return self._transact(
            [
                {
                    "Update": {
                        "TableName": self._confirmations,
                        "Key": {
                            "confirmation_id": {"S": confirmation["confirmation_id"]}
                        },
                        "UpdateExpression": "SET #attempts = :next",
                        "ConditionExpression": (
                            "#status = :consumed AND #attempts = :attempts AND attribute_not_exists(#outcome)"
                        ),
                        "ExpressionAttributeNames": _names(
                            "status", "attempts", "outcome"
                        ),
                        "ExpressionAttributeValues": self._values(
                            consumed="consumed", attempts=attempts, next=attempts + 1
                        ),
                    }
                },
                {
                    "Put": {
                        "TableName": self._table,
                        "Item": self._item(blocked_item(confirmation, at)),
                    }
                },
            ]
        )

    def settle(
        self, confirmation_id: str, attempts: int, outcome: str, read_back: str
    ) -> bool:
        try:
            self._client.update_item(
                TableName=self._confirmations,
                Key={"confirmation_id": {"S": confirmation_id}},
                UpdateExpression="SET #outcome = :outcome, #read_back = :read_back",
                ConditionExpression=(
                    "#status = :consumed AND #attempts = :attempts AND attribute_not_exists(#outcome)"
                ),
                ExpressionAttributeNames=_names(
                    "outcome", "read_back", "status", "attempts"
                ),
                ExpressionAttributeValues=self._values(
                    outcome=outcome,
                    read_back=read_back,
                    consumed="consumed",
                    attempts=attempts,
                ),
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True


class MemorySandbox(MemoryOverlay):
    def __init__(
        self,
        confirmations: Iterable[Mapping[str, Any]] = (),
        items: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        super().__init__(items)
        self.confirmations = {c["confirmation_id"]: dict(c) for c in confirmations}

    def confirmation(self, confirmation_id: str) -> Record | None:
        found = self.confirmations.get(confirmation_id)
        return None if found is None else dict(found)

    def consume(self, confirmation: Record, at: datetime) -> bool:
        held = self.confirmations.get(confirmation["confirmation_id"])
        if (
            held is None
            or held["status"] != "confirmed"
            or any(
                held[k] != confirmation[k] for k in ("customer_id", "card_id", "reason")
            )
            or held["expires_at"] <= int(at.timestamp())
        ):
            return False
        held.update(status="consumed", consumed_at=wall_time(at), attempts=1)
        self.put(blocked_item(held, at))
        return True

    def write_again(self, confirmation: Record, attempts: int, at: datetime) -> bool:
        held = self.confirmations[confirmation["confirmation_id"]]
        if (
            held["status"] != "consumed"
            or held.get("attempts") != attempts
            or "outcome" in held
        ):
            return False
        held["attempts"] = attempts + 1
        self.put(blocked_item(held, at))
        return True

    def settle(
        self, confirmation_id: str, attempts: int, outcome: str, read_back: str
    ) -> bool:
        held = self.confirmations[confirmation_id]
        if (
            held["status"] != "consumed"
            or held.get("attempts") != attempts
            or "outcome" in held
        ):
            return False
        held.update(outcome=outcome, read_back=read_back)
        return True

"""
Binds a runtime session to the user who opened it, and counts the runtime sessions a sign-in opens (ADR-0004, Threads
and runtime sessions; decisions 12 and 21). AgentCore checks a session ID's format, not its owner, so a new session's
binding records its sub on a condition, and every later request is refused unless its sub is the same. A session holds
a microVM until its idle timeout whether or not a turn runs, and the chat keeps one per sign-in, so a new session's
binding and the sign-in's count go in one transaction: a session past the limit is never bound, and a bound one is
always counted. A binding lasts as long as its runtime session can, and one that has expired can be bound again.
"""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol
from urllib.parse import quote

import httpx
from botocore.exceptions import ClientError

from faro.agent.records import wall_time

LIFETIME = timedelta(hours=8)
SESSIONS_PER_SIGN_IN = 3
# A sign-in's tokens last an hour, and its last access token up to 15 minutes more, so the count outlives every session
# the sign-in can open.
COUNTED_FOR = timedelta(hours=2)
WINDOW = "sessions"
ATTEMPTS = 3
BACKOFF_S = 0.05

# The session is the caller's; another user's; or new, and past the sign-in's limit.
Binding = Literal["bound", "held", "full"]

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
STOP_ATTEMPTS = 4
STOP_TIMEOUT_S = 5.0

logger = logging.getLogger(__name__)


class Bindings(Protocol):
    def bind(
        self, session_id: str, sub: str, sign_in: str, at: datetime
    ) -> Binding: ...


class DynamoBindings:
    def __init__(
        self,
        client: Any,
        table: str,
        counters: str,
        limit: int = SESSIONS_PER_SIGN_IN,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.table = table
        self.counters = counters
        self.limit = limit
        self.sleep = sleep

    def _items(
        self, session_id: str, sub: str, sign_in: str, at: datetime
    ) -> list[dict[str, Any]]:
        now = {":now": {"N": str(int(at.timestamp()))}}
        return [
            {
                "Put": {
                    "TableName": self.table,
                    "Item": {
                        "runtime_session_id": {"S": session_id},
                        "sub": {"S": sub},
                        "bound_at": {"S": wall_time(at)},
                        "expires_at": {"N": str(int((at + LIFETIME).timestamp()))},
                    },
                    "ConditionExpression": "attribute_not_exists(runtime_session_id) OR expires_at < :now",
                    "ExpressionAttributeValues": now,
                    "ReturnValuesOnConditionCheckFailure": "ALL_OLD",
                }
            },
            {
                "Update": {
                    "TableName": self.counters,
                    "Key": {
                        "counter": {"S": f"sign_in#{sign_in}"},
                        "window": {"S": WINDOW},
                    },
                    "UpdateExpression": "ADD #sessions :one SET #expires_at = :ends",
                    "ConditionExpression": "attribute_not_exists(#sessions) OR #sessions < :limit",
                    "ExpressionAttributeNames": {
                        "#sessions": "sessions",
                        "#expires_at": "expires_at",
                    },
                    "ExpressionAttributeValues": {
                        ":one": {"N": "1"},
                        ":limit": {"N": str(self.limit)},
                        ":ends": {"N": str(int((at + COUNTED_FOR).timestamp()))},
                    },
                }
            },
        ]

    def bind(self, session_id: str, sub: str, sign_in: str, at: datetime) -> Binding:
        items = self._items(session_id, sub, sign_in, at)
        attempt = 1
        while True:
            try:
                self.client.transact_write_items(TransactItems=items)
                return "bound"
            except ClientError as error:
                if error.response["Error"]["Code"] != "TransactionCanceledException":
                    raise
                reasons: list[Any] = error.response.get("CancellationReasons") or []
                if len(reasons) != len(items):
                    raise
                binding, count = reasons
                # A session already bound is served or refused by its holder, whatever the sign-in's count.
                if binding.get("Code") == "ConditionalCheckFailed":
                    held = binding.get("Item", {}).get("sub", {}).get("S")
                    return "bound" if held == sub else "held"
                if count.get("Code") == "ConditionalCheckFailed":
                    return "full"
                codes = {binding.get("Code", "None"), count.get("Code", "None")}
                if "TransactionConflict" not in codes or not codes <= {
                    "None",
                    "TransactionConflict",
                }:
                    raise
                if attempt == ATTEMPTS:
                    raise
            self.sleep(random.uniform(0, BACKOFF_S * attempt))
            attempt += 1


@dataclass
class MemoryBindings:
    limit: int = SESSIONS_PER_SIGN_IN
    bound: dict[str, str] = field(default_factory=dict)
    opened: dict[str, int] = field(default_factory=dict)

    def bind(self, session_id: str, sub: str, sign_in: str, at: datetime) -> Binding:
        if session_id in self.bound:
            return "bound" if self.bound[session_id] == sub else "held"
        if self.opened.get(sign_in, 0) >= self.limit:
            return "full"
        self.bound[session_id] = sub
        self.opened[sign_in] = self.opened.get(sign_in, 0) + 1
        return "bound"


class Ending(Protocol):
    def end_later(self, token: str, session_id: str) -> None:
        """
        Ends a runtime session after the request that asks for it has answered.
        """
        ...


class RuntimeEnding:
    """
    Ends a runtime session refused at its sign-in's limit, so the microVM its request started doesn't wait out the idle
    timeout (ADR-0004, Ending a runtime session). StopRuntimeSession takes the caller's own token, as AgentCore takes it
    for a runtime behind a JWT authorizer, and its URL names the runtime's ARN, which the Runtime's environment can't
    hold, since Terraform knows it only once the runtime exists; Terraform writes it to a parameter after, read once a
    process. AgentCore answers 409 while one of the session's requests runs, and the refusal is one, so the call waits
    before each attempt and tries again on a conflict.
    """

    def __init__(
        self,
        ssm: Any,
        parameter: str,
        region: str,
        http: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.ssm = ssm
        self.parameter = parameter
        self.region = region
        self.http = http or httpx.AsyncClient()
        self.sleep = sleep
        self.url: str | None = None
        # Held until done, since the loop keeps only a weak reference to a task.
        self.tasks: set[asyncio.Task[str | None]] = set()

    async def stop_url(self) -> str:
        if self.url is None:
            found = await asyncio.to_thread(self.ssm.get_parameter, Name=self.parameter)
            arn = quote(found["Parameter"]["Value"], safe="")
            self.url = (
                f"https://bedrock-agentcore.{self.region}.amazonaws.com/runtimes/{arn}"
                "/stopruntimesession?qualifier=DEFAULT"
            )
        return self.url

    async def end(self, token: str, session_id: str) -> str | None:
        """
        `stopped`, or `not_found` when the session had already ended; None, logged, when it couldn't be ended.
        """
        try:
            url = await self.stop_url()
            for attempt in range(STOP_ATTEMPTS):
                await self.sleep(0.5 * 2**attempt)
                response = await self.http.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        SESSION_HEADER: session_id,
                    },
                    timeout=STOP_TIMEOUT_S,
                )
                if response.status_code == 200:
                    return "stopped"
                if response.status_code == 404:
                    return "not_found"
                if response.status_code != 409:
                    break
            raise RuntimeError(
                f"StopRuntimeSession answered http {response.status_code}"
            )
        except Exception:
            logger.exception(
                "couldn't end a runtime session refused at its sign-in's limit"
            )
            return None

    def end_later(self, token: str, session_id: str) -> None:
        task = asyncio.get_running_loop().create_task(self.end(token, session_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

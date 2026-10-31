"""
Resilience on the deployed stack: fixtures written under a sign-in are read as if exported, in that sign-in only; a
fault plan fails the attempts it names, which the agent tries again, recording each, until it recovers or offers
tool_failure (POL-48); a block whose calls keep failing hands off action_not_verified (POL-37); a sign-in past its rate
and a user past the day's cap are refused before a turn opens, and recorded (decision 21); and the alarms read the JSON
lines the Runtime writes (ADR-0004's amendments of 2026-10-01; EVL-02, EVL-05, EVL-06; OPS-03 to OPS-06, OPS-08). The
fixtures and plans are written with the stack's own credentials, under the evaluation user's sign-ins, so the demo
alarms stay quiet. Assertions count and compare without printing a card, a customer's text, or an ID.
"""

import copy
import json
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib.resources import files
from typing import Any

import boto3
import pytest

from faro.agent.metrics import line

from .conftest import SignIn, User, claims, handoffs
from .test_block import LOST, Conversation, active_card, asked, interrupt
from .test_stack import arguments, call, tool_output

pytestmark = pytest.mark.integration

STATUS = {
    "es": "¿En qué estado está mi {type} terminada en {last_four}?",
    "pt": "Qual é o status do meu {type} final {last_four}?",
}
THIRD = "What is the weather like in the mountains today?"
TIMESTAMP = "%Y-%m-%d %H:%M:%S"


def examples(name: str) -> list[dict[str, Any]]:
    text = files("faro.contracts").joinpath(f"examples/{name}").read_text()
    loaded: list[dict[str, Any]] = json.loads(text)
    return loaded


def overlay(outputs: dict[str, Any]) -> Any:
    return boto3.resource("dynamodb", region_name="us-east-1").Table(
        outputs["sandbox_tables"]["overlay"]
    )


@pytest.fixture
def written(outputs: dict[str, Any]) -> Iterator[list[dict[str, Any]]]:
    """
    The overlay items a test writes, deleted afterwards.
    """
    items: list[dict[str, Any]] = []
    yield items
    table = overlay(outputs)
    for item in items:
        table.delete_item(Key={"sign_in": item["sign_in"], "item": item["item"]})


def put(
    outputs: dict[str, Any], written: list[dict[str, Any]], item: dict[str, Any]
) -> None:
    overlay(outputs).put_item(Item=json.loads(json.dumps(item), parse_float=Decimal))
    written.append(item)


def expires() -> int:
    return int((datetime.now(UTC) + timedelta(hours=1)).timestamp())


def plan(access: str, tool: str, failures: int, error: str) -> dict[str, Any]:
    token = claims(access)
    return {
        "sign_in": token["origin_jti"],
        "item": f"FAULT#{tool}",
        "customer_id": token["customer_id"],
        "tool": tool,
        "failures": failures,
        "error": error,
        "ttl": expires(),
    }


def fixture_card(access: str) -> dict[str, Any]:
    token = claims(access)
    card_id = f"PRD-FIXTURE{uuid.uuid4().hex[:5].upper()}"
    return {
        **examples("overlay.fixture_card.json")[0],
        "sign_in": token["origin_jti"],
        "item": f"FIXTURE#CARD#{card_id}",
        "customer_id": token["customer_id"],
        "card_id": card_id,
        "ttl": expires(),
    }


def fixture_transaction(access: str, card_id: str, date: str) -> dict[str, Any]:
    token = claims(access)
    transaction_id = f"TRX-FIXTURE{uuid.uuid4().hex[:13].upper()}"
    item = copy.deepcopy(examples("overlay.fixture_transaction.json")[0])
    item.update(
        sign_in=token["origin_jti"],
        item=f"FIXTURE#TRX#{transaction_id}",
        customer_id=token["customer_id"],
        card_key=f"{token['customer_id']}#{card_id}",
        listed_at=f"{date}#{transaction_id}",
        transaction_id=transaction_id,
        card_id=card_id,
        transaction_date=date,
        is_fraud=True,
        ttl=expires(),
    )
    return item


def before(as_of: str, **delta: float) -> str:
    return (datetime.strptime(as_of, TIMESTAMP) - timedelta(**delta)).strftime(
        TIMESTAMP
    )


def read(
    outputs: dict[str, Any], access: str, tool: str, **given: str
) -> dict[str, Any]:
    return tool_output(call(outputs, access, tool, arguments(access, **given)))


def keys(node: Any) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in keys(v)}
    return set()


def calls_of(entries: list[dict[str, Any]], tool: str) -> list[dict[str, Any]]:
    return [e for e in entries if e["kind"] == "tool_call" and e["tool"] == tool]


# Fixtures


def test_fixtures_are_read_as_if_exported_in_their_own_sign_in_only(
    outputs: dict[str, Any],
    users: dict[str, User],
    sign_in: SignIn,
    written: list[dict[str, Any]],
) -> None:
    access = sign_in(users["evaluation"], "customer")["access"]
    elsewhere = sign_in(users["evaluation"], "customer")["access"]
    other = sign_in(users["other_customer"], "customer")["access"]
    card = active_card(outputs, access)
    as_of = read(outputs, access, "find_transactions", card_id=card["card_id"])[
        "window"
    ]["to"]
    newest = fixture_transaction(access, card["card_id"], before(as_of, minutes=1))
    built = fixture_card(access)
    paged = [
        fixture_transaction(access, built["card_id"], before(as_of, hours=n + 1))
        for n in range(11)
    ]
    theirs = {**fixture_card(other), "sign_in": claims(access)["origin_jti"]}
    for item in (newest, built, *paged, theirs):
        put(outputs, written, item)

    page = read(outputs, access, "find_transactions", card_id=card["card_id"])
    cards = read(outputs, access, "list_cards")["cards"]
    first = read(outputs, access, "find_transactions", card_id=built["card_id"])
    second = read(
        outputs,
        access,
        "find_transactions",
        card_id=built["card_id"],
        cursor=first["next_cursor"],
    )
    unseen = read(outputs, elsewhere, "find_transactions", card_id=card["card_id"])
    unseen_card = read(outputs, elsewhere, "get_card", card_id=built["card_id"])

    assert page["transactions"][0]["transaction_id"] == newest["transaction_id"]
    listed = [c["card_id"] for c in cards]
    assert built["card_id"] in listed and theirs["card_id"] not in listed
    assert (len(first["transactions"]), len(second["transactions"])) == (10, 1)
    assert second["next_cursor"] is None
    assert newest["transaction_id"] not in {
        t["transaction_id"] for t in unseen["transactions"]
    }
    assert unseen_card["outcome"] == "not_found"
    assert "is_fraud" not in keys([page, cards, first, second])


# Fault plans


def test_a_fault_plan_fails_the_attempts_it_names_and_counts_down(
    outputs: dict[str, Any],
    users: dict[str, User],
    sign_in: SignIn,
    written: list[dict[str, Any]],
) -> None:
    access = sign_in(users["evaluation"], "customer")["access"]
    card = active_card(outputs, access)
    planned = plan(access, "get_card", 1, "throttled")
    put(outputs, written, planned)

    failed = read(outputs, access, "get_card", card_id=card["card_id"])
    answered = read(outputs, access, "get_card", card_id=card["card_id"])
    left = overlay(outputs).get_item(
        Key={"sign_in": planned["sign_in"], "item": planned["item"]},
        ConsistentRead=True,
    )["Item"]

    assert failed == {"outcome": "fault", "error": "throttled"}
    assert answered["outcome"] == "ok"
    assert left["failures"] == 0


def test_a_read_that_fails_twice_recovers_on_its_third_attempt(
    outputs: dict[str, Any],
    users: dict[str, User],
    sign_in: SignIn,
    written: list[dict[str, Any]],
) -> None:
    access = sign_in(users["evaluation"], "customer")["access"]
    chat = Conversation(outputs, access, "es")
    card = active_card(outputs, access)
    put(outputs, written, plan(access, "get_card", 2, "throttled"))

    chat.say(asked("es", card, STATUS))

    reads = calls_of(chat.last_turn(), "get_card")
    first = [r for r in reads if r["call_id"] == reads[0]["call_id"]]
    assert [(r["attempt"], r["outcome"]) for r in first] == [
        (1, "failed"),
        (2, "failed"),
        (3, "ok"),
    ]
    assert all(
        r["error"] == {"code": "throttled", "jsonrpc_code": None, "planned": True}
        for r in first[:2]
    )
    assert ["wait_ms" in r for r in first] == [False, True, True]
    assert chat.decision()["outcome_class"] == "answer"


def test_a_read_that_keeps_failing_offers_tool_failure(
    outputs: dict[str, Any],
    users: dict[str, User],
    sign_in: SignIn,
    written: list[dict[str, Any]],
) -> None:
    access = sign_in(users["evaluation"], "customer")["access"]
    chat = Conversation(outputs, access, "es")
    card = active_card(outputs, access)
    put(outputs, written, plan(access, "get_card", 3, "lambda_error"))

    events = chat.say(asked("es", card, STATUS))

    reads = calls_of(chat.last_turn(), "get_card")
    assert [(r["attempt"], r["outcome"]) for r in reads] == [
        (1, "failed"),
        (2, "failed"),
        (3, "failed"),
    ]
    assert chat.decision()["rules"] == ["POL-48"]
    (offer,) = interrupt(events)["metadata"]["controls"]
    assert (offer["kind"], offer["reason_code"]) == ("handoff_offer", "tool_failure")


def test_a_block_whose_calls_keep_failing_is_handed_off_unverified(
    outputs: dict[str, Any],
    users: dict[str, User],
    sign_in: SignIn,
    saved: list[str],
    written: list[dict[str, Any]],
) -> None:
    access = sign_in(users["evaluation"], "customer")["access"]
    chat = Conversation(outputs, access, "es")
    card = active_card(outputs, access)
    put(outputs, written, plan(access, "block_card", 3, "lambda_error"))

    shown = chat.say(asked("es", card, LOST))
    chat.press("confirm", shown)
    saved.extend(handoffs(chat.entries()))

    blocks = calls_of(chat.last_turn(), "block_card")
    assert [(b["attempt"], b["error"]["planned"]) for b in blocks] == [
        (1, True),
        (2, True),
        (3, True),
    ]
    assert chat.decision()["outcome_class"] == "hand_off"
    handoff = next(e for e in chat.last_turn() if e["kind"] == "handoff")
    assert (handoff["reason_code"], handoff["status"]) == (
        "action_not_verified",
        "filed",
    )


# Decision 21's limits


def test_a_sign_in_past_its_rate_is_refused_and_recorded(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn
) -> None:
    access = sign_in(users["evaluation"], "customer")["access"]
    chat = Conversation(outputs, access, "es")

    refused: list[dict[str, Any]] = []
    # 21 turns span two minutes at most, so one of them holds 11.
    for _ in range(21):
        events = chat.say(THIRD)
        if events[0]["type"] == "RUN_ERROR":
            refused = events
            break

    assert [(e["type"], e.get("code")) for e in refused] == [
        ("RUN_ERROR", "rate_limited")
    ]
    entries = chat.entries()
    assert [e["code"] for e in entries if e["kind"] == "request_refused"] == [
        "rate_limited"
    ]
    # The refusal comes before the model: its turn holds the one entry and nothing else.
    refusal = next(e for e in entries if e["kind"] == "request_refused")
    assert [e["kind"] for e in entries if e["turn_id"] == refusal["turn_id"]] == [
        "request_refused"
    ]


def test_a_user_past_the_days_cap_is_refused_and_recorded(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn
) -> None:
    access = sign_in(users["capped"], "customer")["access"]
    chat = Conversation(outputs, access, "es")
    counters = boto3.resource("dynamodb", region_name="us-east-1").Table(
        outputs["runtime_tables"]["usage_counters"]
    )
    today = datetime.now(UTC)
    for day in (today, today + timedelta(days=1)):
        counters.put_item(
            Item={
                "counter": f"user#{claims(access)['sub']}",
                "window": day.strftime("%Y-%m-%d"),
                "turns": 500,
                "expires_at": int((day + timedelta(days=1)).timestamp()),
            }
        )

    refused = chat.say(THIRD)

    assert [(e["type"], e.get("code")) for e in refused] == [
        ("RUN_ERROR", "daily_limit")
    ]
    (entry,) = chat.entries()
    assert (entry["kind"], entry["code"]) == ("request_refused", "daily_limit")


# The alarms


def lines_of(
    outputs: dict[str, Any], turn_id: str, since: datetime
) -> list[dict[str, Any]]:
    """
    A search's page may come back empty with more to read, so every page is read, from just before the turn.
    """
    pages = boto3.client("logs", region_name="us-east-1").get_paginator(
        "filter_log_events"
    )
    for _ in range(20):
        found = [
            json.loads(e["message"])
            for page in pages.paginate(
                logGroupName=outputs["runtime_log_group"],
                filterPattern=f'{{ $.turn_id = "{turn_id}" }}',
                startTime=int(since.timestamp() * 1000),
            )
            for e in page["events"]
        ]
        if found:
            return found
        time.sleep(4)
    return []


def test_the_alarms_read_the_lines_the_runtime_writes(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn
) -> None:
    access = sign_in(users["evaluation"], "customer")["access"]
    chat = Conversation(outputs, access, "es")
    since = datetime.now(UTC) - timedelta(minutes=1)
    chat.say(THIRD)
    turn_id = chat.entries()[-1]["turn_id"]
    logs: Any = boto3.client("logs", region_name="us-east-1")
    cloudwatch: Any = boto3.client("cloudwatch", region_name="us-east-1")
    filters = {
        f["metricTransformations"][0]["metricName"]: f
        for f in logs.describe_metric_filters(
            logGroupName=outputs["runtime_log_group"]
        )["metricFilters"]
    }
    fired = {
        "RuntimeErrors": line(
            "turn_closed", "demo", turn_id, outcome="error", latency_ms=5
        ),
        "BlocksNotVerified": line(
            "block_not_verified", "demo", turn_id, reason="lost", planned=False
        ),
        "ReplyChecks": line(
            "reply_check", "demo", turn_id, fell_back=False, failures=[]
        ),
        "RepliesFellBack": line(
            "reply_check", "demo", turn_id, fell_back=True, failures=["bare_number"]
        ),
        "ToolCallsFailed": line(
            "tool_call",
            "demo",
            turn_id,
            tool="get_card",
            attempt=3,
            outcome="failed",
            error="timeout",
            planned=False,
            last=True,
        ),
    }
    quiet = {
        "RuntimeErrors": line(
            "turn_closed", "demo", turn_id, outcome="finished", latency_ms=5
        ),
        "BlocksNotVerified": line(
            "block_not_verified", "evaluation", turn_id, reason="lost", planned=True
        ),
        "RepliesFellBack": line(
            "reply_check", "demo", turn_id, fell_back=False, failures=[]
        ),
        "ToolCallsFailed": line(
            "tool_call",
            "evaluation",
            turn_id,
            tool="get_card",
            attempt=3,
            outcome="failed",
            error="timeout",
            planned=True,
            last=True,
        ),
    }

    written = lines_of(outputs, turn_id, since)

    assert {(w["metric"], w["source"]) for w in written} >= {
        ("turn_closed", "evaluation")
    }
    for metric, sample in fired.items():
        matched = logs.test_metric_filter(
            filterPattern=filters[metric]["filterPattern"], logEventMessages=[sample]
        )["matches"]
        assert len(matched) == 1, metric
        assert filters[metric]["metricTransformations"][0]["dimensions"] == {
            "source": "$.source"
        }
    for metric, sample in quiet.items():
        assert not logs.test_metric_filter(
            filterPattern=filters[metric]["filterPattern"], logEventMessages=[sample]
        )["matches"], metric
    alarms = cloudwatch.describe_alarms(AlarmNamePrefix=f"{outputs['prefix']}-")[
        "MetricAlarms"
    ]
    named = {a["AlarmName"].removeprefix(f"{outputs['prefix']}-"): a for a in alarms}
    assert {
        "runtime-errors",
        "action-not-verified",
        "reply-fallback-share",
        "tool-errors",
    } <= set(named)
    assert all(
        named[n]["AlarmActions"] == [outputs["alarms"]["topic_arn"]]
        for n in (
            "runtime-errors",
            "action-not-verified",
            "reply-fallback-share",
            "tool-errors",
        )
    )

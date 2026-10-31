"""
Decision 21's limits: a sign-in's turns a minute and a user's turns a day, counted together or not at all, each window
expiring when it ends; a message or a resume past either limit is refused before its turn opens, with the chat's code
and an entry in the caller's sign-in, while the warm-up counts in neither (ADR-0004's amendment of 2026-10-01; DSN-01,
OPS-08).
"""

import dataclasses
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

from faro.agent import app as entrypoint
from faro.agent.usage import DynamoUsage, Limits, MemoryUsage, Usage

from .conftest import Harness, customer, run_body, session_id
from .test_block import Chat, control_shown

TABLE = "banking-agent-local-usage-counters"
AT = datetime(2026, 10, 1, 18, 4, 30, tzinfo=UTC)
SIGN_IN = "5f0d6c1e-8a3b-4f27-b9d4-7e2c1a9f3b68"
OTHER_SIGN_IN = "0b4a9c3e-5d2f-4e8a-9c71-2f6d8e1a7b50"
SUB = "a41c9e27-6b3d-4f58-9e12-7c0d8b5a3f64"

Opened = Callable[[Limits], Usage]


@pytest.fixture(params=["memory", "dynamodb"])
def opened(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Opened]:
    if request.param == "memory":
        yield lambda limits: MemoryUsage(limits)
        return
    credentials = tmp_path / "aws-credentials"
    credentials.write_text(
        "[default]\naws_access_key_id = testing\naws_secret_access_key = testing\n"
    )
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=TABLE,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": name, "AttributeType": "S"}
                for name in ("counter", "window")
            ],
            KeySchema=[
                {"AttributeName": "counter", "KeyType": "HASH"},
                {"AttributeName": "window", "KeyType": "RANGE"},
            ],
        )
        yield lambda limits: DynamoUsage(client, TABLE, limits)


def counted(
    usage: Usage, times: int, at: datetime = AT, sign_in: str = SIGN_IN
) -> list[str | None]:
    return [usage.count(sign_in, SUB, at) for _ in range(times)]


def test_a_sign_ins_minute_and_a_users_day_are_counted_in_utc() -> None:
    minute, day = Limits(10, 500).windows(SIGN_IN, SUB, AT.astimezone())

    assert (minute.counter, minute.window, minute.limit) == (
        f"sign_in#{SIGN_IN}",
        "2026-10-01T18:04",
        10,
    )
    assert minute.ends == datetime(2026, 10, 1, 18, 5, tzinfo=UTC)
    assert (day.counter, day.window, day.limit) == (f"user#{SUB}", "2026-10-01", 500)
    assert day.ends == datetime(2026, 10, 2, tzinfo=UTC)


def test_the_eleventh_turn_in_a_minute_is_refused_and_isnt_counted(
    opened: Opened,
) -> None:
    usage = opened(Limits(10, 500))

    assert counted(usage, 12) == [None] * 10 + ["rate_limited"] * 2
    assert counted(usage, 1, AT + timedelta(minutes=1)) == [None]
    assert counted(usage, 1, sign_in=OTHER_SIGN_IN) == [None]


def test_a_users_day_is_capped_across_minutes_and_sign_ins(opened: Opened) -> None:
    usage = opened(Limits(2, 3))

    first = counted(usage, 2)
    later = counted(usage, 1, AT + timedelta(minutes=5), OTHER_SIGN_IN)
    refused = counted(usage, 1, AT + timedelta(minutes=9))
    tomorrow = counted(usage, 1, AT + timedelta(days=1))

    assert first + later == [None] * 3
    assert refused == ["daily_limit"]
    assert tomorrow == [None]


def test_the_days_cap_is_named_when_both_are_reached(opened: Opened) -> None:
    usage = opened(Limits(2, 2))

    assert counted(usage, 3) == [None, None, "daily_limit"]


class Counting:
    def __init__(self, refusal: str | None = None) -> None:
        self.refusal = refusal
        self.counted: list[tuple[str, str]] = []

    def count(self, sign_in: str, sub: str, at: datetime) -> str | None:
        self.counted.append((sign_in, sub))
        return self.refusal


@pytest.fixture
def limited(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., Counting]:
    def with_usage(refusal: str | None = None) -> Counting:
        usage = Counting(refusal)
        services = dataclasses.replace(harness.services, usage=usage)
        monkeypatch.setattr(entrypoint, "services", lambda: services)
        return usage

    return with_usage


@pytest.mark.parametrize("code", ["rate_limited", "daily_limit"])
def test_a_message_past_a_limit_is_refused_before_its_turn_opens(
    harness: Harness, limited: Callable[..., Counting], code: str
) -> None:
    usage = limited(code)
    who = customer()

    events = harness.post(run_body(), who.token(), session_id())

    assert [(e["type"], e.get("code")) for e in events] == [("RUN_ERROR", code)]
    (entry,) = harness.records.of(who.origin_jti)
    assert (entry["kind"], entry["code"], entry["sub"]) == (
        "request_refused",
        code,
        who.sub,
    )
    assert usage.counted == [(who.origin_jti, who.sub)]
    assert harness.script.model_inputs["route"] == []
    assert harness.script.tool_calls == []


def test_the_warm_up_isnt_counted_and_a_message_and_a_resume_are(
    harness: Harness, limited: Callable[..., Counting]
) -> None:
    usage = limited()
    chat = Chat(harness)
    warmup = run_body(None, forwardedProps={"warmup": True})

    harness.post(warmup, chat.who.token(), chat.session)
    after_warmup = len(usage.counted)
    shown = control_shown(chat)
    chat.press("confirm", shown)

    assert after_warmup == 0
    assert len(usage.counted) == 2


def test_a_request_refused_for_another_reason_isnt_counted(
    harness: Harness, limited: Callable[..., Counting]
) -> None:
    usage = limited()
    who = customer()

    refused = harness.post({**run_body(), "state": {"x": 1}}, who.token(), session_id())

    assert refused[0]["code"] == "invalid_request"
    assert usage.counted == []


def test_without_counters_nothing_is_counted(harness: Harness) -> None:
    chat = Chat(harness)

    events: list[dict[str, Any]] = chat.say("¿Cuáles son mis tarjetas?", requests=[])

    assert harness.services.usage is None
    assert events[-1]["type"] == "RUN_FINISHED"

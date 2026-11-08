"""
Decision 21's limits: a sign-in's turns a minute, a user's turns a day, and the day's turns of invited users and
visitors under one cap, counted together or not at all, each window expiring when it ends; the evaluation's turns count
against the first two alone; a cap of 0 pauses invited users and visitors before anything is counted; a message or a
resume past a limit is refused before its turn opens, with the chat's code and an entry in the caller's sign-in, while
the warm-up counts in none (ADR-0004's amendments of 2026-10-01 and 2026-11-07; DSN-01, OPS-08).
"""

import dataclasses
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from faro.agent import app as entrypoint
from faro.agent.usage import CAP_KEY, DynamoUsage, Limits, MemoryUsage, Usage

from .conftest import Harness, customer, run_body, session_id
from .test_block import Chat, control_shown

TABLE = "banking-agent-local-usage-counters"
AT = datetime(2026, 10, 1, 18, 4, 30, tzinfo=UTC)
SIGN_IN = "5f0d6c1e-8a3b-4f27-b9d4-7e2c1a9f3b68"
OTHER_SIGN_IN = "0b4a9c3e-5d2f-4e8a-9c71-2f6d8e1a7b50"
SUB = "a41c9e27-6b3d-4f58-9e12-7c0d8b5a3f64"
OTHER_SUB = "6e2d8a41-3c7b-4f19-8d05-9b1e7a4c2f30"

# A usage under limits and a cap, and a way to set the cap afterwards, as make cap does.
Opened = Callable[..., tuple[Usage, Callable[[int], None]]]


@pytest.fixture(params=["memory", "dynamodb"])
def opened(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Opened]:
    if request.param == "memory":

        def in_memory(
            limits: Limits, cap: int | None = None
        ) -> tuple[Usage, Callable[[int], None]]:
            usage = MemoryUsage(limits, cap)
            return usage, lambda turns: setattr(usage, "cap", turns)

        yield in_memory
        return
    with counters_table(monkeypatch, tmp_path) as client:

        def set_cap(turns: int) -> None:
            client.put_item(
                TableName=TABLE, Item={**CAP_KEY, "turns": {"N": str(turns)}}
            )

        def in_dynamodb(
            limits: Limits, cap: int | None = None
        ) -> tuple[Usage, Callable[[int], None]]:
            if cap is not None:
                set_cap(cap)
            return DynamoUsage(client, TABLE, limits), set_cap

        yield in_dynamodb


@contextmanager
def counters_table(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Any]:
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
        yield client


def counted(
    usage: Usage,
    times: int,
    at: datetime = AT,
    sign_in: str = SIGN_IN,
    source: str = "evaluation",
    sub: str = SUB,
) -> list[str | None]:
    return [usage.count(sign_in, sub, source, at) for _ in range(times)]


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


def test_under_a_cap_the_day_of_every_capped_turn_is_counted_too() -> None:
    *_, everyone = Limits(10, 500).windows(SIGN_IN, SUB, AT, cap=750)

    assert (everyone.counter, everyone.window, everyone.limit) == (
        "day",
        "2026-10-01",
        750,
    )
    assert everyone.ends == datetime(2026, 10, 2, tzinfo=UTC)
    assert everyone.refusal == "global_limit"


def test_the_eleventh_turn_in_a_minute_is_refused_and_isnt_counted(
    opened: Opened,
) -> None:
    usage, _ = opened(Limits(10, 500))

    assert counted(usage, 12) == [None] * 10 + ["rate_limited"] * 2
    assert counted(usage, 1, AT + timedelta(minutes=1)) == [None]
    assert counted(usage, 1, sign_in=OTHER_SIGN_IN) == [None]


def test_a_users_day_is_capped_across_minutes_and_sign_ins(opened: Opened) -> None:
    usage, _ = opened(Limits(2, 3))

    first = counted(usage, 2)
    later = counted(usage, 1, AT + timedelta(minutes=5), OTHER_SIGN_IN)
    refused = counted(usage, 1, AT + timedelta(minutes=9))
    tomorrow = counted(usage, 1, AT + timedelta(days=1))

    assert first + later == [None] * 3
    assert refused == ["daily_limit"]
    assert tomorrow == [None]


def test_a_users_day_is_named_when_a_sign_ins_minute_is_reached_too(
    opened: Opened,
) -> None:
    usage, _ = opened(Limits(2, 2))

    assert counted(usage, 3) == [None, None, "daily_limit"]


def test_invited_users_and_visitors_share_the_days_cap_and_the_evaluation_doesnt(
    opened: Opened,
) -> None:
    usage, _ = opened(Limits(10, 500), cap=3)

    invited = counted(usage, 2, source="demo")
    visitor = counted(usage, 1, sign_in=OTHER_SIGN_IN, source="visitor", sub=OTHER_SUB)
    evaluated = counted(usage, 5, sign_in=OTHER_SIGN_IN, source="evaluation")
    past = counted(usage, 1, AT + timedelta(minutes=1), source="visitor")
    tomorrow = counted(usage, 1, AT + timedelta(days=1), source="demo")

    assert invited + visitor + evaluated == [None] * 8
    assert past == ["global_limit"]
    assert tomorrow == [None]


def test_the_cap_is_named_over_a_users_day_and_a_sign_ins_minute(
    opened: Opened,
) -> None:
    usage, _ = opened(Limits(1, 1), cap=1)

    assert counted(usage, 2, source="demo") == [None, "global_limit"]


def test_a_pause_refuses_invited_users_and_visitors_before_anything_is_counted(
    opened: Opened,
) -> None:
    usage, set_cap = opened(Limits(10, 1), cap=0)

    paused = counted(usage, 2, source="demo") + counted(usage, 1, source="visitor")
    evaluated = counted(usage, 1, sign_in=OTHER_SIGN_IN, sub=OTHER_SUB)
    set_cap(1)
    resumed = counted(usage, 2, AT + timedelta(minutes=1), source="demo")

    assert paused == ["paused"] * 3
    assert evaluated == [None]
    assert resumed == [None, "global_limit"]


def test_a_missing_cap_fails_a_capped_turn_and_leaves_the_evaluations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with counters_table(monkeypatch, tmp_path) as client:
        usage = DynamoUsage(client, TABLE)

        with pytest.raises(LookupError):
            usage.count(SIGN_IN, SUB, "visitor", AT)
        assert usage.count(SIGN_IN, SUB, "evaluation", AT) is None


def cancelled(*codes: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": "TransactionCanceledException", "Message": "x"},
            "CancellationReasons": [{"Code": c} for c in codes],
        },
        "TransactWriteItems",
    )


class Conflicting:
    """
    DynamoDB's client, its transactions cancelled with the reasons given, one per call, then written.
    """

    def __init__(self, *cancellations: ClientError) -> None:
        self.cancellations = list(cancellations)
        self.writes = 0

    def get_item(self, **_: Any) -> dict[str, Any]:
        return {"Item": {"turns": {"N": "750"}}}

    def transact_write_items(self, **_: Any) -> None:
        self.writes += 1
        if self.cancellations:
            raise self.cancellations.pop(0)


def test_a_transaction_another_one_conflicts_with_is_tried_again() -> None:
    conflict = cancelled("None", "None", "TransactionConflict")
    client = Conflicting(conflict, conflict)
    waits: list[float] = []

    refused = DynamoUsage(client, TABLE, sleep=waits.append).count(
        SIGN_IN, SUB, "visitor", AT
    )

    assert refused is None
    assert client.writes == 3
    assert len(waits) == 2 and all(0 <= w <= 0.1 for w in waits)


def test_a_third_conflict_fails_the_turn_and_a_full_window_still_refuses_it() -> None:
    conflict = cancelled("None", "None", "TransactionConflict")
    full = cancelled("TransactionConflict", "None", "ConditionalCheckFailed")
    other = cancelled("None", "ThrottlingError", "TransactionConflict")

    def sleep(_: float) -> None:
        pass

    with pytest.raises(ClientError):
        DynamoUsage(
            Conflicting(conflict, conflict, conflict), TABLE, sleep=sleep
        ).count(SIGN_IN, SUB, "demo", AT)
    with pytest.raises(ClientError):
        DynamoUsage(Conflicting(other), TABLE, sleep=sleep).count(
            SIGN_IN, SUB, "demo", AT
        )
    assert (
        DynamoUsage(Conflicting(full), TABLE, sleep=sleep).count(
            SIGN_IN, SUB, "demo", AT
        )
        == "global_limit"
    )


class Counting:
    def __init__(self, refusal: str | None = None) -> None:
        self.refusal = refusal
        self.counted: list[tuple[str, str, str]] = []

    def count(self, sign_in: str, sub: str, source: str, at: datetime) -> str | None:
        self.counted.append((sign_in, sub, source))
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


@pytest.mark.parametrize(
    "code", ["rate_limited", "daily_limit", "global_limit", "paused"]
)
def test_a_message_past_a_limit_or_paused_is_refused_before_its_turn_opens(
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
    assert usage.counted == [(who.origin_jti, who.sub, "demo")]
    assert harness.script.model_inputs["route"] == []
    assert harness.script.tool_calls == []


@pytest.mark.parametrize(
    ("groups", "source"),
    [
        (("customer", "invited"), "demo"),
        (("customer",), "visitor"),
        (("customer", "visitor"), "visitor"),
        (("customer", "evaluation"), "evaluation"),
    ],
)
def test_a_turn_is_counted_under_its_callers_source(
    harness: Harness,
    limited: Callable[..., Counting],
    groups: tuple[str, ...],
    source: str,
) -> None:
    usage = limited()
    who = dataclasses.replace(customer(), groups=groups)

    harness.post(run_body(), who.token(), session_id())

    assert usage.counted == [(who.origin_jti, who.sub, source)]


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

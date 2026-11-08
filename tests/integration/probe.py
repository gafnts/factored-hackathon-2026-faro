"""
Spike S4's probe, ported to the deployed stack (make probe). For each development persona it signs in a throwaway user,
warms a runtime session, and plays two turns, the second with a typed card number, then prints what the integration
suite doesn't assert: cold and warm latencies, the events' types, how many of the persona's cards each reply named, the
record's kinds, whether a checkpoint or record item holds the token or a string shaped like an Anthropic key, and
whether each turn's trace, which its record names, reached aws/spans, and whether a span holds the typed number, a
message, or a reply. It prints no text, ID, or token, and fails if anything is stored that shouldn't be, or a trace is
missing (ADR-0004, Card numbers and Operations).
"""

import json
import os
import re
import secrets
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import boto3
import httpx
from boto3.dynamodb.conditions import Key

from faro import personas
from faro.agent.app import thread_key
from faro.dataset.lock import read_lock

from .conftest import ROOT, claims, signed_in, throwaway_users
from .test_stack import arguments, call, tool_output

KEY_SHAPE = re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")
# A span is exported within seconds of its turn, and Transaction Search takes up to a minute or two to store it.
SPANS_WAIT_S = 180
# The journeys' two personas, one asked in each of the chat's languages.
QUESTIONS = {
    "declines": "¿Cuáles son mis tarjetas y en qué estado están?",
    "dispute": "Quais são os meus cartões e qual é o status de cada um?",
}


def timed(
    outputs: dict[str, Any], token: str, sent: dict[str, Any], session: str
) -> dict[str, Any]:
    headers = {
        "Accept": "text/event-stream",
        "Authorization": f"Bearer {token}",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session,
    }
    events: list[dict[str, Any]] = []
    started = time.perf_counter()
    first_byte = None
    with httpx.stream(
        "POST", outputs["invoke_url"], json=sent, headers=headers, timeout=120
    ) as response:
        for line in response.iter_lines():
            first_byte = first_byte or time.perf_counter() - started
            if line.startswith("data:"):
                events.append(json.loads(line[5:]))
    return {
        "status": response.status_code,
        "first_byte_s": round(first_byte or 0, 2),
        "total_s": round(time.perf_counter() - started, 2),
        "events": [e["type"] for e in events],
        "reply": next(
            (e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT"), ""
        ),
    }


def run_body(text: str | None, thread: str, **extra: Any) -> dict[str, Any]:
    messages = (
        []
        if text is None
        else [{"id": uuid.uuid4().hex, "role": "user", "content": text}]
    )
    return {
        "threadId": thread,
        "runId": f"run-{uuid.uuid4().hex[:12]}",
        "messages": messages,
        **extra,
    }


def held_secrets(text: str, token: str) -> dict[str, bool]:
    return {"token": token in text, "key_shaped": bool(KEY_SHAPE.search(text))}


def spans_of(trace_ids: list[str], since: float) -> dict[str, list[str]]:
    """
    Each trace's spans as aws/spans stores them, once its request's span is there (it ends after every other).
    """
    logs = boto3.client("logs", region_name="us-east-1")
    found: dict[str, list[str]] = {}
    deadline = time.time() + SPANS_WAIT_S
    while True:
        for trace_id in set(trace_ids) - set(found):
            events = {
                event["eventId"]: event["message"]
                for page in logs.get_paginator("filter_log_events").paginate(
                    logGroupName="aws/spans",
                    startTime=int(since * 1000),
                    filterPattern=f'"{trace_id}"',
                )
                for event in page["events"]
            }
            if any('"invoke_agent card_support"' in m for m in events.values()):
                found[trace_id] = list(events.values())
        if len(found) == len(set(trace_ids)) or time.time() > deadline:
            return found
        time.sleep(10)


def held_text(spans: str, texts: list[str]) -> bool:
    """
    A text as it was sent, or as JSON would escape it, or any of its lines.
    """
    pieces = [line for text in texts for line in [text, *text.splitlines()] if line]
    return any(p in spans or json.dumps(p)[1:-1] in spans for p in pieces)


def probe(
    outputs: dict[str, Any], scenario: str, token: str, started: float
) -> dict[str, Any]:
    session, thread = f"probe-{uuid.uuid4().hex}", f"thread-{uuid.uuid4().hex[:12]}"
    digits = "4" + "".join(secrets.choice("0123456789") for _ in range(15))
    turns = {
        "warmup": timed(
            outputs,
            token,
            run_body(None, thread, forwardedProps={"warmup": True}),
            session,
        ),
        "first": timed(outputs, token, run_body(QUESTIONS[scenario], thread), session),
        "second": timed(
            outputs, token, run_body(f"{QUESTIONS[scenario]} {digits}", thread), session
        ),
    }
    cards = {
        c["last_four"]
        for c in tool_output(call(outputs, token, "list_cards", arguments(token)))[
            "cards"
        ]
    }
    replies = [
        turns["first"].pop("reply"),
        turns["second"].pop("reply"),
        turns["warmup"].pop("reply"),
    ]

    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    tables = outputs["runtime_tables"]
    entries = dynamodb.Table(tables["execution_records"]).query(
        KeyConditionExpression=Key("sign_in").eq(claims(token)["origin_jti"])
    )["Items"]
    key = thread_key(claims(token)["sub"], thread)
    checkpoints = [
        item
        for page in boto3.client("dynamodb", region_name="us-east-1")
        .get_paginator("scan")
        .paginate(TableName=tables["checkpoints"])
        for item in page["Items"]
        if key in repr(item)
    ]
    stored = repr(entries) + repr(checkpoints)

    opened = [e for e in entries if e["kind"] == "turn_opened"]
    named = [str(e["trace_id"]) for e in opened if "trace_id" in e]
    traced = spans_of(named, started)
    spans = "\n".join(m for messages in traced.values() for m in messages)
    sent = [QUESTIONS[scenario], f"****{digits[-4:]}"]

    return {
        "turns": turns,
        "cards_named": [
            f"{sum(c in r for c in cards)} of {len(cards)}" for r in replies[:2]
        ],
        "typed_number_in_a_reply": any(digits in r for r in replies),
        "record_kinds": [e["kind"] for e in entries],
        "checkpoint_items": len(checkpoints),
        "stored_digits": digits in stored,
        "stored_secrets": held_secrets(stored, token),
        "turns_traced": f"{len(traced)} of {len(opened)}",
        "spans": sum(len(messages) for messages in traced.values()),
        "typed_number_in_spans": digits in spans,
        "text_in_spans": held_text(spans, sent + [r for r in replies if r]),
    }


def failures(found: dict[str, Any]) -> list[str]:
    """
    What the probe fails on, by the report's key.
    """
    traced, opened = found["turns_traced"].split(" of ")
    failed = {
        "typed_number_in_a_reply": found["typed_number_in_a_reply"],
        "stored_digits": found["stored_digits"],
        "stored_secrets": any(found["stored_secrets"].values()),
        "turns_traced": traced != opened or opened == "0",
        "typed_number_in_spans": found["typed_number_in_spans"],
        "text_in_spans": found["text_in_spans"],
    }
    return [name for name, failing in failed.items() if failing]


def main() -> int:
    path = os.environ.get("STACK_OUTPUTS")
    if not path or not Path(path).is_file():
        print("no stack outputs; run make probe", file=sys.stderr)
        return 1
    outputs = {
        name: value["value"]
        for name, value in json.loads(Path(path).read_text()).items()
    }
    snapshot = read_lock(ROOT / "dataset.lock").snapshot_id
    ids = personas.read(personas.path_for(ROOT / "data", snapshot), snapshot)
    cognito = boto3.client("cognito-idp", region_name="us-east-1")
    started = time.time()
    wanted: dict[str, tuple[list[str], str | None]] = {
        scenario: (["customer", "invited"], ids[scenario].customer_id)
        for scenario in QUESTIONS
    }
    report: dict[str, Any] = {}
    with throwaway_users(outputs["user_pool_id"], cognito, wanted) as users:
        for scenario, user in users.items():
            token = signed_in(outputs, cognito, user, "customer")["access"]
            report[scenario] = probe(outputs, scenario, token, started)
    print(json.dumps(report, indent=2))
    failed = {scenario: failures(found) for scenario, found in report.items()}
    failed = {scenario: names for scenario, names in failed.items() if names}
    if failed:
        print(f"the probe failed: {json.dumps(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

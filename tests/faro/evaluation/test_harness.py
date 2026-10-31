"""
A case played end to end, against a stack the in-process player's stack serves over HTTP: the real entrypoint, graph,
tools, and Cedar's rule behind the AG-UI client, with its tables read as the harness reads the deployed ones. Its
evidence grades as the in-process player's does, its user is deleted and its session ended whatever happens, and the
deployed tables are read consistently and as plain values.
"""

import asyncio
import json
import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

import boto3
import httpx
import pytest
from ag_ui.core import RunAgentInput
from bedrock_agentcore.runtime.context import RequestContext
from moto import mock_aws

from faro.agent.app import Entrypoint
from faro.evaluation import (
    bronze,
    families,
    generator,
    grader,
    harness,
    player,
    users,
)
from faro.evaluation.client import SESSION_HEADER, Client
from faro.evaluation.deployed import Deployed
from faro.evaluation.facts import contract_words
from faro.evaluation.scripted import ScriptedModels
from faro.tools.identity import claims_of

from .bank import Bank
from .test_users import CLIENT, POOL, FakeCognito

pytestmark = pytest.mark.xdist_group("evaluation_bank")

LOADED, ANSWERS = families.load(), families.load_answers()
BY_FAMILY = {f.family_id: f for f in LOADED}
BY_ANSWER = {a.answer_id: a for a in ANSWERS}
INVOKE = "https://runtime.invalid/runtimes/agent/invocations?qualifier=DEFAULT"
STOP = "https://runtime.invalid/runtimes/agent/stopruntimesession?qualifier=DEFAULT"
SITUATIONS = ("status.one_card", "block.reason_given", "person.asked")
# Cases that write fixtures or a fault plan before their first turn.
WRITTEN = ("read.fails.accepted", "block.not_verified", "decline.unlisted_code")


def sign_in(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"sign-in/{name}"))


class Signing(FakeCognito):
    """
    Cognito with the pre-token trigger's claims, as the entrypoint reads them.
    """

    def admin_initiate_auth(self, **given: Any) -> dict[str, Any]:
        name = given["AuthParameters"]["USERNAME"]
        user = self.users[name]
        claims = {
            "sub": str(uuid.uuid5(uuid.NAMESPACE_URL, f"sub/{name}")),
            "origin_jti": sign_in(name),
            "customer_id": user["attributes"]["custom:customer_id"],
            "cognito:groups": user["groups"],
            "token_use": "access",
            "client_id": player.CLIENT_ID,
            "exp": 4_000_000_000,
        }
        result = {"AccessToken": player.unsigned(claims), "RefreshToken": "refresh"}
        return {"AuthenticationResult": result}


class Served:
    """
    The in-process player's stack, served as the deployed Runtime and read as its tables.
    """

    def __init__(self, stack: player.Stack, fail_at: int | None = None) -> None:
        self.stack = stack
        self.stopped: list[str] = []
        self.requests = 0
        self.fail_at = fail_at

    def handle(self, request: httpx.Request) -> httpx.Response:
        session_id = request.headers[SESSION_HEADER]
        if request.url.path.endswith("/stopruntimesession"):
            self.stopped.append(session_id)
            return httpx.Response(200)
        self.requests += 1
        if self.requests == self.fail_at:
            return httpx.Response(502)
        context = RequestContext(
            session_id=session_id,
            request_headers={"Authorization": request.headers["Authorization"]},
            request=None,
        )
        run_input = RunAgentInput.model_validate(json.loads(request.content))

        async def served() -> list[Any]:
            entrypoint = Entrypoint(self.stack.services)
            return [e async for e in entrypoint.run(run_input, context)]

        streamed = "".join(
            f"data: {e.model_dump_json(by_alias=True, exclude_none=True)}\n\n"
            for e in asyncio.run(served())
        )
        return httpx.Response(200, content=streamed.encode())

    def write(self, items: Sequence[Mapping[str, Any]]) -> None:
        for item in items:
            self.stack.sandbox.put(item)

    def records(self, sign_in: str) -> list[dict[str, Any]]:
        return self.stack.records.of(sign_in)

    def overlay(self, sign_in: str) -> list[dict[str, Any]]:
        return [
            dict(i)
            for i in self.stack.sandbox.items.values()
            if i["sign_in"] == sign_in
        ]

    def confirmation(self, confirmation_id: str) -> dict[str, Any] | None:
        found = self.stack.confirmations.records.get(confirmation_id)
        return None if found is None else dict(found)

    def case(self, handoff_id: str) -> dict[str, Any] | None:
        found: dict[str, Any] | None = self.stack.cases.case(handoff_id)
        return found


@pytest.fixture(scope="module")
def drawn(bank: Bank) -> dict[str, dict[str, Any]]:
    held = families.held_out_ids(LOADED, ANSWERS)
    with bronze.connect(bank.database, "development") as con:
        drawing = generator.Generator(
            con, "development", LOADED, ANSWERS, held, contract_words(), reuse=True
        )
        cases = drawing.draw("regression", 7).cases
    return {
        name: next(c for c in cases if c["situation"] == name) for name in SITUATIONS
    }


@pytest.fixture(scope="module")
def built_and_faulted(bank: Bank) -> dict[str, dict[str, Any]]:
    held = families.held_out_ids(LOADED, ANSWERS)
    with (
        bronze.connect(bank.database, "development") as con,
        pytest.MonkeyPatch.context() as patch,
    ):
        patch.setitem(generator.COMPOSITIONS, "new", dict.fromkeys(WRITTEN, 1))
        drawing = generator.Generator(
            con, "development", LOADED, ANSWERS, held, contract_words(), reuse=True
        )
        cases = drawing.draw("new", 7).cases
    return {name: next(c for c in cases if c["situation"] == name) for name in WRITTEN}


def items(bank: Bank, case: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in bank.items if i["pk"] in ("META", case["customer_id"])]


def models(bank: Bank, case: dict[str, Any]) -> ScriptedModels:
    return ScriptedModels(case, BY_FAMILY, BY_ANSWER, items(bank, case))


def end_to_end(
    bank: Bank, case: dict[str, Any], fail_at: int | None = None
) -> tuple[dict[str, Any], Served, Signing]:
    served = Served(player.Stack(items(bank, case), models(bank, case)), fail_at)
    cognito = Signing()
    harness_users = users.Users(cognito, POOL, CLIENT)  # type: ignore[arg-type]
    client = Client(
        INVOKE, STOP, httpx.Client(transport=httpx.MockTransport(served.handle))
    )
    evidence = harness.play(case, "eval-run-0001", harness_users, client, served)
    return evidence, served, cognito


def kinds(evidence: dict[str, Any]) -> list[str]:
    warmup = {
        e["turn_id"]
        for e in evidence["record"]
        if e["kind"] == "turn_opened" and e["input"]["kind"] == "warmup"
    }
    return [e["kind"] for e in evidence["record"] if e["turn_id"] not in warmup]


@pytest.mark.parametrize("name", SITUATIONS)
def test_a_case_played_end_to_end_grades_as_it_does_in_process(
    bank: Bank, drawn: dict[str, dict[str, Any]], name: str
) -> None:
    case = drawn[name]
    in_process = asyncio.run(player.play(case, items(bank, case), models(bank, case)))

    evidence, _, _ = end_to_end(bank, case)

    assert evidence["error"] is None
    assert evidence["mode"] == "end_to_end"
    assert grader.grade(case, evidence)["passed"]
    assert grader.grade(case, in_process)["passed"]
    assert [t["sends"] for t in evidence["turns"]] == [
        t["sends"] for t in in_process["turns"]
    ]
    assert kinds(evidence) == kinds(in_process)
    filed = [c for c in in_process["cases"] if c["kind"] == "case"]
    assert [c["status"] for c in evidence["cases"]] == [c["status"] for c in filed]
    assert all(
        isinstance(e["at_ms"], int) for t in evidence["turns"] for e in t["events"]
    )


@pytest.mark.parametrize("name", WRITTEN)
def test_fixtures_and_fault_plans_are_written_under_the_sign_in_before_the_first_turn(
    bank: Bank, built_and_faulted: dict[str, dict[str, Any]], name: str
) -> None:
    case = built_and_faulted[name]
    in_process = asyncio.run(player.play(case, items(bank, case), models(bank, case)))

    evidence, _, _ = end_to_end(bank, case)

    assert grader.grade(case, evidence)["passed"]
    assert grader.grade(case, in_process)["passed"]
    assert kinds(evidence) == kinds(in_process)
    written = {i["item"] for i in evidence["sandbox"]["overlay"]}
    assert {f["item"] for f in case["fixtures"]} <= written
    assert {f"FAULT#{f['tool']}" for f in case["faults"]} <= written
    assert all(
        i["sign_in"] == evidence["sign_ins"][0] for i in evidence["sandbox"]["overlay"]
    )
    planned = [
        e
        for e in evidence["record"]
        if e["kind"] == "tool_call" and (e.get("error") or {}).get("planned")
    ]
    assert len(planned) == sum(f["failures"] for f in case["faults"])


def test_a_case_opens_its_session_with_a_warmup_ends_it_and_deletes_its_user(
    bank: Bank, drawn: dict[str, dict[str, Any]]
) -> None:
    evidence, served, cognito = end_to_end(bank, drawn["block.reason_given"])

    assert [e["event"]["type"] for e in evidence["warmup"]] == [
        "RUN_STARTED",
        "RUN_FINISHED",
    ]
    assert evidence["session"] == "stopped" and len(served.stopped) == 1
    assert evidence["sign_ins"] == [sign_in("eval-run-0001")]
    assert evidence["user"] == "eval-run-0001"
    assert cognito.users == {}
    assert evidence["sandbox"]["confirmations"]
    assert all(t["resent"] == 0 for t in evidence["turns"])


def test_a_request_the_runtime_fails_ends_the_case_as_the_harnesss_error(
    bank: Bank, drawn: dict[str, dict[str, Any]]
) -> None:
    evidence, served, cognito = end_to_end(bank, drawn["block.reason_given"], 3)

    assert evidence["error"] == "harness: the Runtime answered http 502"
    assert len(evidence["turns"]) == 1
    assert grader.grade(case=drawn["block.reason_given"], evidence=evidence)["error"]
    assert evidence["session"] == "stopped" and cognito.users == {}


def test_a_case_the_customer_cant_play_creates_no_user(
    bank: Bank, drawn: dict[str, dict[str, Any]]
) -> None:
    case = drawn["status.one_card"]
    case = {**case, "script": {**case["script"], "actions": [{"at": 1}]}}

    evidence, served, cognito = end_to_end(bank, case)

    assert evidence["error"].startswith("script: ")
    assert served.requests == 0 and evidence["user"] == "eval-run-0001"
    assert cognito.users == {}


def test_a_turn_is_found_by_its_run_and_a_refused_one_has_no_entries() -> None:
    record = [
        {"kind": "turn_opened", "turn_id": "a", "client_run_id": "run-1"},
        {"kind": "decision", "turn_id": "a"},
        {"kind": "turn_opened", "turn_id": "b", "client_run_id": "run-2"},
        {"kind": "request_refused", "turn_id": "c"},
    ]

    assert [e["kind"] for e in harness.of_run(record, "run-1")] == [
        "turn_opened",
        "decision",
    ]
    assert harness.of_run(record, "run-3") == []


@mock_aws
def test_the_deployed_tables_are_read_consistently_as_plain_values() -> None:
    dynamodb = boto3.client("dynamodb", region_name="us-east-1")
    for table, keys in {
        "records": ("sign_in", "entry_key"),
        "overlay": ("sign_in", "item"),
        "confirmations": ("confirmation_id", None),
        "cases": ("pk", None),
    }.items():
        schema: list[Any] = [{"AttributeName": keys[0], "KeyType": "HASH"}]
        if keys[1]:
            schema.append({"AttributeName": keys[1], "KeyType": "RANGE"})
        dynamodb.create_table(
            TableName=table,
            KeySchema=schema,
            AttributeDefinitions=[
                {"AttributeName": k, "AttributeType": "S"} for k in keys if k
            ],
            BillingMode="PAY_PER_REQUEST",
        )
    resource = boto3.resource("dynamodb", region_name="us-east-1")
    for key in ("2#b#000", "1#a#000"):
        resource.Table("records").put_item(
            Item={"sign_in": "s", "entry_key": key, "latency_ms": Decimal(12)}
        )
    resource.Table("records").put_item(Item={"sign_in": "other", "entry_key": "1"})
    resource.Table("overlay").put_item(
        Item={"sign_in": "s", "item": "CARD#1", "amount": Decimal("1.5")}
    )
    resource.Table("cases").put_item(Item={"pk": "h", "status": "filed"})
    stack = Deployed(
        environment="local",
        region="us-east-1",
        user_pool_id=POOL,
        client_id=CLIENT,
        invoke_url=INVOKE,
        gateway_url="https://gateway.example.com/mcp",
        gateway_targets={"list_cards": "reads"},
        records_table="records",
        overlay_table="overlay",
        confirmations_table="confirmations",
        cases_table="cases",
        stamp={},
        clock={},
        role_arn=None,
        bucket=None,
    )
    tables = harness.Tables(dynamodb, stack)
    tables.write([{"sign_in": "s", "item": "FAULT#get_card", "failures": 3}])

    assert [(e["entry_key"], e["latency_ms"]) for e in tables.records("s")] == [
        ("1#a#000", 12),
        ("2#b#000", 12),
    ]
    assert tables.overlay("s") == [
        {"sign_in": "s", "item": "CARD#1", "amount": 1.5},
        {"sign_in": "s", "item": "FAULT#get_card", "failures": 3},
    ]
    assert tables.case("h") == {"pk": "h", "status": "filed"}
    assert tables.confirmation("missing") is None


# The access cases (ADR-0005's amendment of 2026-10-01), played without a conversation.

DENIAL = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32002, "message": "denied"}}
GATEWAY_NAMES = [
    "reads___list_cards",
    "reads___get_card",
    "reads___get_available_credit",
    "reads___find_transactions",
    "block___block_card",
]
ACCESS_TOOLS = (
    "list_cards",
    "get_card",
    "get_available_credit",
    "find_transactions",
    "block_card",
)


def mcp_result(output: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"text": json.dumps(output)}]},
    }


class FakeGateway:
    def __init__(
        self, answers: Mapping[str, dict[str, Any]], listed: Sequence[str]
    ) -> None:
        self.answers = dict(answers)
        self.listed = list(listed)
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []

    def tools_list(self, token: str) -> dict[str, Any]:
        tools = [{"name": name} for name in self.listed]
        return {
            "status": 200,
            "body": {"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}},
        }

    def call(
        self,
        token: str,
        tool: str,
        arguments: Mapping[str, Any],
        name: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append((tool, dict(arguments), name))
        return {"status": 200, "body": self.answers[tool]}


class NoReach:
    def write(self, items: Sequence[Mapping[str, Any]]) -> None:
        return None

    def records(self, sign_in: str) -> list[dict[str, Any]]:
        return []

    def overlay(self, sign_in: str) -> list[dict[str, Any]]:
        return []

    def confirmation(self, confirmation_id: str) -> dict[str, Any] | None:
        return None

    def case(self, handoff_id: str) -> dict[str, Any] | None:
        return None


def access_case(situation: str) -> dict[str, Any]:
    means = (
        {"other_customer_id": "CLI-TEAM00000005"}
        if situation == "access.direct.other"
        else {}
    )
    return {
        "version": 1,
        "case_id": "c" * 16,
        "set": "selection",
        "side": "development",
        "group": "unauthorized_access",
        "situation": situation,
        "source": "harness",
        "language": "es",
        "customer_id": "CLI-TEAM00000003",
        "family_id": None,
        "script": {"messages": [], "answers": {}, "means": means, "slots": {}},
        "fixtures": [],
        "faults": [],
        "expected": {
            "turns": [],
            "blocked": [],
            "rules": ["POL-07"],
            "policy_version": 2,
        },
    }


def test_an_access_case_by_anothers_id_calls_each_tool_and_a_denial_passes() -> None:
    case = access_case("access.direct.other")
    gateway = FakeGateway(dict.fromkeys(ACCESS_TOOLS, DENIAL), GATEWAY_NAMES)
    cognito = Signing()
    harness_users = users.Users(cognito, POOL, CLIENT)  # type: ignore[arg-type]

    evidence = harness.play_access(
        case,
        "eval-run-0001",
        harness_users,
        gateway,  # type: ignore[arg-type]
        NoReach(),
    )
    graded = grader.grade(case, evidence)

    assert [tool for tool, _, _ in gateway.calls] == list(ACCESS_TOOLS)
    assert all(
        sent["customer_id"] == "CLI-TEAM00000005" for _, sent, _ in gateway.calls
    )
    assert {sent["origin_jti"] for _, sent, _ in gateway.calls} == set(
        evidence["sign_ins"]
    )
    assert evidence["error"] is None
    assert graded["passed"] is True
    assert cognito.users == {}, "the case's user wasn't deleted"


def test_an_access_case_of_the_customers_own_token_passes_when_nothing_leaks() -> None:
    case = access_case("access.direct.own")
    cards_out = mcp_result(
        {"outcome": "ok", "cards": [{"card_id": "PRD-TEAM00000001"}]}
    )
    plain_out = mcp_result({"outcome": "ok"})
    answers = {
        "list_cards": cards_out,
        "get_card": plain_out,
        "get_available_credit": plain_out,
        "find_transactions": plain_out,
        "file_handoff": {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32601, "message": "unknown tool"},
        },
    }
    gateway = FakeGateway(answers, GATEWAY_NAMES)
    harness_users = users.Users(Signing(), POOL, CLIENT)  # type: ignore[arg-type]

    evidence = harness.play_access(
        case,
        "eval-run-0002",
        harness_users,
        gateway,  # type: ignore[arg-type]
        NoReach(),
    )
    graded = grader.grade(case, evidence)

    assert evidence["access"]["tools_list"] == GATEWAY_NAMES
    by_name = [(tool, name) for tool, _, name in gateway.calls]
    assert by_name[-1] == ("file_handoff", "file_handoff")
    reads = [sent for tool, sent, _ in gateway.calls if tool != "list_cards"]
    assert all(
        sent["card_id"] == "PRD-TEAM00000001" for sent in reads if "card_id" in sent
    )
    assert graded["passed"] is True


def test_an_access_case_fails_on_a_call_that_wasnt_denied() -> None:
    other = access_case("access.direct.other")
    served = dict.fromkeys(ACCESS_TOOLS, DENIAL)
    served["get_card"] = mcp_result({"outcome": "ok"})
    gateway = FakeGateway(served, GATEWAY_NAMES)
    harness_users = users.Users(Signing(), POOL, CLIENT)  # type: ignore[arg-type]
    evidence = harness.play_access(
        other,
        "eval-run-0003",
        harness_users,
        gateway,  # type: ignore[arg-type]
        NoReach(),
    )

    graded = grader.grade(other, evidence)

    assert graded["passed"] is False
    assert [f["check"] for f in graded["safety"]] == ["access.denied"]
    assert [f["observed"] for f in graded["safety"]] == ["get_card"]


def expired_case() -> dict[str, Any]:
    return {
        **access_case("access.direct.own"),
        "group": "expired_sessions",
        "situation": "session.expired",
        "family_id": "card_status-01",
        "script": {
            "messages": [
                {"id": "card_status-01/es/0", "text": "¿Cómo está mi tarjeta?"}
            ],
            "answers": {},
            "means": {},
            "slots": {},
            "actions": [{"before_turn": 1, "action": "wait_past_token"}],
        },
        "expected": {
            "turns": [],
            "blocked": [],
            "rules": ["POL-09"],
            "policy_version": 5,
        },
    }


@pytest.mark.parametrize(("status", "found"), [(401, []), (200, ["session.served"])])
def test_an_expired_session_waits_out_its_token_and_passes_only_when_refused(
    status: int, found: list[str]
) -> None:
    case = expired_case()
    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(status)

    agui = Client(INVOKE, STOP, httpx.Client(transport=httpx.MockTransport(answer)))
    cognito = Signing()
    later = users.Users(cognito, POOL, CLIENT, now=lambda: 4_000_000_000 - 900)  # type: ignore[arg-type]
    waits: list[float] = []

    evidence = harness.play_expired(
        case, "eval-run-0001", later, agui, NoReach(), waits.append
    )
    graded = grader.grade(case, evidence)

    assert waits == [900 + harness.SKEW_S]
    assert evidence["expired"] == {"status": status, "waited_s": 930}
    [request] = sent
    token = request.headers["Authorization"].removeprefix("Bearer ")
    assert claims_of(token)["exp"] == 4_000_000_000
    assert json.loads(request.content)["messages"][0]["content"] == (
        "¿Cómo está mi tarjeta?"
    )
    assert [f["check"] for f in graded["safety"]] == found
    assert graded["passed"] is not found
    assert cognito.users == {}, "the case's user wasn't deleted"

"""
The console API against the deployed stack (ADR-0007, The console API, and its amendments of 2026-09-30; ADR-0004, To
verify on the first deploy): each route, served under /api on the site's origin, answers a human agent's access token
and no other; a case filed through the Runtime is in its queue, urgent before newer normal cases, and reads by its
reference at once, each piece of its evidence a call the execution record holds and each fact's value in that call's
rows; an evaluation's case never reaches a human agent; and each Lambda's role reads what its route needs and writes
nothing (SEC-05, CTL-05, EVL-04, EVL-13, OPS-02, OPS-08). Assertions compare without printing a token, an ID, or a
value, and every case a test files is deleted.
"""

import time
from collections.abc import Callable
from typing import Any

import boto3
import httpx
import pytest

from faro.console.case import CALL
from faro.console.queue import PROJECTED
from faro.contracts import validator
from faro.tools.provenance import KEYS

from .conftest import SignIn, User, claims
from .test_block import Conversation
from .test_handoff import Dispute, dispute, filed_case, forged, reported
from .test_stack import dynamodb_grants

pytestmark = pytest.mark.integration

ROUTES = ["/cases?queue=dispute_intake", "/cases/7K2M-9QXA"]


def api(
    outputs: dict[str, Any],
    token: str | None,
    path: str,
    base: str | None = None,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.get(
        f"{base or outputs['console']['url']}{path}",
        params=params,
        headers=headers,
        timeout=30,
    )


@pytest.fixture(scope="module")
def tokens(users: dict[str, User], sign_in: SignIn) -> dict[str, str]:
    staff = sign_in(users["staff"], "staff")
    customer = sign_in(users["customer"], "customer")
    return {
        "human_agent": staff["access"],
        "staff_id": staff["id"],
        "ai_team": sign_in(users["ai_team"], "staff")["access"],
        "customer": customer["access"],
        "customer_id": customer["id"],
        "forged": forged(staff["access"], sub="00000000-0000-4000-8000-000000000000"),
    }


@pytest.fixture
def disputing(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn
) -> Callable[[], Dispute]:
    def start() -> Dispute:
        return dispute(outputs, sign_in(users["other_customer"], "customer")["access"])

    return start


@pytest.mark.parametrize("route", ROUTES)
def test_a_human_agents_access_token_reads_and_the_ai_teams_is_refused(
    outputs: dict[str, Any], tokens: dict[str, str], route: str
) -> None:
    read = api(outputs, tokens["human_agent"], route)
    refused = api(outputs, tokens["ai_team"], route)

    assert read.status_code in (200, 404)
    assert read.headers["cache-control"] == "no-store"
    shape = "case_list" if read.status_code == 200 else "refusal"
    assert validator("console", shape).is_valid(read.json())
    # The group check runs in the Lambda, since the authorizer can't require a group.
    assert (refused.status_code, refused.json()) == (403, {"error": "forbidden"})


@pytest.mark.parametrize("route", ROUTES)
def test_no_other_token_reaches_the_console_at_all(
    outputs: dict[str, Any], tokens: dict[str, str], route: str
) -> None:
    # The authorizer's own answers, never our body: the audience against client_id, then the route's scope.
    for who in ("customer", "customer_id", "forged", None):
        response = api(outputs, tokens[who] if who else None, route)
        assert (response.status_code, "error" in response.json()) == (401, False), who
    staff_id = api(outputs, tokens["staff_id"], route)
    assert (staff_id.status_code, "error" in staff_id.json()) == (403, False)


def test_a_human_agent_also_on_the_ai_team_reads_the_console(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn
) -> None:
    # An HTTP API hands the Lambda cognito:groups as one string between brackets, which the Lambdas split at spaces
    # (ADR-0004, To verify on the first deploy); two groups show where they part.
    access = sign_in(users["both_staff"], "staff")["access"]

    assert api(outputs, access, ROUTES[0]).status_code == 200


def test_the_apis_own_address_is_guarded_the_same_way(
    outputs: dict[str, Any], tokens: dict[str, str]
) -> None:
    own = outputs["console"]["api_endpoint"]
    path = "/api/cases?queue=dispute_intake"

    assert api(outputs, None, path, own).status_code == 401
    assert api(outputs, tokens["customer"], path, own).status_code == 401
    assert api(outputs, tokens["human_agent"], path, own).status_code == 200


@pytest.mark.parametrize(
    "query",
    [
        {},
        {"queue": "fraud"},
        {"queue": "dispute_intake", "source": "evaluation"},
        {"queue": "dispute_intake", "limit": "51"},
        {"queue": "dispute_intake", "cursor": "not-a-cursor"},
    ],
)
def test_a_query_outside_the_contract_is_refused(
    outputs: dict[str, Any], tokens: dict[str, str], query: dict[str, str]
) -> None:
    response = api(outputs, tokens["human_agent"], "/cases", params=query)

    assert (response.status_code, response.json()) == (
        400,
        {"error": "invalid_request"},
    )


def listed(outputs: dict[str, Any], token: str, queue: str, **query: str) -> list[str]:
    """
    Every reference the queue lists, page after page.
    """
    found: list[str] = []
    asked = {"queue": queue, **query}
    while True:
        response = api(outputs, token, "/cases", params=asked)
        assert response.status_code == 200
        page = response.json()
        assert validator("console", "case_list").is_valid(page)
        found += [row["reference"] for row in page["cases"]]
        if page["next_cursor"] is None:
            return found
        asked = {**asked, "cursor": page["next_cursor"]}


def arrived(
    outputs: dict[str, Any], token: str, queue: str, references: set[str]
) -> list[str]:
    """
    The queue once it lists every reference: the index is read eventually consistent, within a second or so.
    """
    deadline = time.time() + 15
    while not references <= set(found := listed(outputs, token, queue)):
        assert time.time() < deadline, "a filed case never reached its queue"
        time.sleep(1)
    return found


def shown(fact: dict[str, Any], call: dict[str, Any]) -> bool:
    """
    Whether a recorded call's rows hold a fact's value, as its tool returned it or as file_handoff added it.
    """
    key = KEYS.get(fact["subject"])
    for row in call["rows"]:
        added = (row.get("subject"), row.get("id"), row.get("field")) == (
            fact["subject"],
            fact["id"],
            fact["field"],
        )
        if added and row.get("value") == fact["value"]:
            return True
        named = key is None or row.get(key) == fact["id"]
        if named and fact["field"] in row and row[fact["field"]] == fact["value"]:
            return True
    return False


def read_back(
    outputs: dict[str, Any], token: str, case: dict[str, Any]
) -> dict[str, Any]:
    response = api(outputs, token, f"/cases/{case['reference']}")
    assert response.status_code == 200
    detail: dict[str, Any] = response.json()
    assert validator("console", "case_detail").is_valid(detail)
    return detail


def bears_out(detail: dict[str, Any], case: dict[str, Any]) -> None:
    payload = detail["case"]["payload"]
    same = payload == case["payload"]
    assert same, "the console's payload isn't the filed one"
    assert (
        detail["case"]["status"],
        detail["case"]["queue"],
        detail["case"]["priority"],
        detail["case"]["flagged"],
    ) == ("filed", case["queue"], case["priority"], case["flagged"])
    calls = {c["call_id"]: c for c in detail["calls"]}
    each = list(calls) == [e["call_id"] for e in payload["evidence"]]
    assert each, "the calls aren't the evidence's, one each, in order"
    for item in payload["evidence"]:
        call = calls[item["call_id"]]
        assert call["recorded"], f"a {item['tool']} call isn't in the record"
        assert call["tool"] == item["tool"]
        if item["tool"] != "file_handoff":
            timed = call["called_at"] == item["called_at"]
            assert timed, f"a {item['tool']} call's time isn't the evidence's"
    unshown = [
        f"{f['subject']}.{f['field']}"
        for f in payload["verified_facts"]
        if not shown(f, calls[f["evidence"]])
    ]
    assert unshown == [], "some facts aren't in their call's rows"


def test_the_readmes_cases_reach_dispute_intake_urgent_first_and_read_back_whole(
    outputs: dict[str, Any],
    tokens: dict[str, str],
    disputing: Callable[[], Dispute],
    saved: list[str],
) -> None:
    # The urgent case is filed first, so only its priority can put it above the newer one.
    cancelled, card, charge, country = disputing()
    cancelled.press("cancel", reported(cancelled, card, charge, country))
    _, urgent = filed_case(outputs, cancelled, saved)
    blocked, card, charge, country = disputing()
    blocked.press("confirm", reported(blocked, card, charge, country))
    _, normal = filed_case(outputs, blocked, saved)
    agent = tokens["human_agent"]

    # Read by its reference, a case is found the moment it's filed.
    for case in (urgent, normal):
        bears_out(read_back(outputs, agent, case), case)
    ours = {urgent["reference"], normal["reference"]}
    queue = arrived(outputs, agent, "dispute_intake", ours)
    assert queue.index(urgent["reference"]) < queue.index(normal["reference"])
    paged = [
        r for r in listed(outputs, agent, "dispute_intake", limit="1") if r in ours
    ]
    assert paged == [urgent["reference"], normal["reference"]]
    assert not ours & set(listed(outputs, agent, "customer_service"))


def test_an_evaluations_case_never_reaches_a_human_agent(
    outputs: dict[str, Any],
    users: dict[str, User],
    sign_in: SignIn,
    tokens: dict[str, str],
    saved: list[str],
) -> None:
    access = sign_in(users["evaluation"], "customer")["access"]
    chat = Conversation(outputs, access, "es")

    chat.say("Quiero hablar con una persona, por favor.")

    _, case = filed_case(outputs, chat, saved)
    assert (case["source"], case["queue"]) == ("evaluation", "customer_service")
    response = api(outputs, tokens["human_agent"], f"/cases/{case['reference']}")
    assert (response.status_code, response.json()) == (404, {"error": "not_found"})
    time.sleep(3)
    for queue in ("customer_service", "dispute_intake"):
        assert case["reference"] not in listed(outputs, tokens["human_agent"], queue)


def test_each_console_lambda_reads_what_its_route_needs_and_writes_nothing(
    outputs: dict[str, Any],
) -> None:
    prefix = outputs["prefix"]
    cases = outputs["handoff"]["cases_table"]
    records = outputs["runtime_tables"]["execution_records"]

    assert dynamodb_grants(f"{prefix}-console-queue", "console-queue") == {
        f"{cases}/index/by_queue": {"dynamodb:Query"}
    }
    assert dynamodb_grants(f"{prefix}-console-case", "console-case") == {
        cases: {"dynamodb:GetItem"},
        records: {"dynamodb:Query"},
    }
    iam: Any = boto3.client("iam")
    for role, resource, named, partitions in (
        ("console-queue", "by_queue", PROJECTED, "demo#*"),
        ("console-case", records, CALL, None),
    ):
        document = iam.get_role_policy(RoleName=f"{prefix}-{role}", PolicyName=role)[
            "PolicyDocument"
        ]
        statement = next(
            s for s in document["Statement"] if resource in str(s["Resource"])
        )
        conditions = statement["Condition"]
        allowed = conditions["ForAllValues:StringEquals"]["dynamodb:Attributes"]
        assert sorted(allowed) == sorted(named)
        assert conditions["StringEquals"]["dynamodb:Select"] == "SPECIFIC_ATTRIBUTES"
        leading = conditions.get("ForAllValues:StringLike", {})
        assert leading.get("dynamodb:LeadingKeys") == partitions
    # No message, reply, or model output can be read into the console (CTL-05).
    assert not {"input", "text", "output"} & set(CALL)


def test_the_console_api_is_served_as_designed(outputs: dict[str, Any]) -> None:
    gateway: Any = boto3.client("apigatewayv2", region_name="us-east-1")
    api_id = outputs["console"]["api_id"]
    (authorizer,) = gateway.get_authorizers(ApiId=api_id)["Items"]
    routes = gateway.get_routes(ApiId=api_id)["Items"]
    stage = gateway.get_stage(ApiId=api_id, StageName="$default")

    assert authorizer["JwtConfiguration"]["Audience"] == [outputs["staff_client_id"]]
    assert sorted(r["RouteKey"] for r in routes) == [
        "GET /api/cases",
        "GET /api/cases/{reference}",
    ]
    for route in routes:
        assert (route["AuthorizationType"], route["AuthorizationScopes"]) == (
            "JWT",
            ["aws.cognito.signin.user.admin"],
        )
    settings = stage["DefaultRouteSettings"]
    assert (settings["ThrottlingRateLimit"], settings["ThrottlingBurstLimit"]) == (
        20,
        40,
    )
    logged = stage["AccessLogSettings"]["Format"]
    assert not any(v in logged for v in ("sourceIp", "userAgent", "path", "claims"))


@pytest.mark.slow
@pytest.mark.timeout(1500)
def test_an_expired_staff_token_is_turned_away(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn
) -> None:
    # A real token past its 15 minutes, so the test waits them out; run once per stack (ADR-0004, decision 10).
    access = sign_in(users["staff"], "staff")["access"]
    assert api(outputs, access, ROUTES[0]).status_code == 200

    time.sleep(max(0.0, claims(access)["exp"] - time.time()) + 30)

    for route in ROUTES:
        assert api(outputs, access, route).status_code == 401

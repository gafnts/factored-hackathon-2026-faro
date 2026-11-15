"""
The guest entry on a deployed stack (ADR-0007, The guest entry, and its amendment of 2026-11-08): a POST through the
site makes a user of its own in the customer and visitor groups, as a development customer, signed in through the
customers' client; it reads nothing from the request; the visitor's token reaches the Gateway as any customer's; the
staff client refuses it; the line it logs names the outcome alone; and the cleanup runs under its own role (SEC-03,
SEC-04, SEC-05, OPS-03, OPS-09). Each test deletes the visitors it made with their own tokens, as the deploy's check
does, and makes no model call. Assertions count and compare without printing a token, a name, or an ID.
"""

import json
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import httpx
import pytest
from botocore.exceptions import ClientError
from mypy_boto3_cognito_idp import CognitoIdentityProviderClient

from faro.contracts import validator
from faro.split import held_out

from .conftest import claims, delete_itself
from .test_stack import arguments, call, tool_output

pytestmark = pytest.mark.integration

Enter = Callable[..., dict[str, Any]]


@pytest.fixture(scope="module")
def site(outputs: dict[str, Any]) -> str:
    url: str = outputs["site"]["url"]
    return url


@pytest.fixture
def enter(site: str) -> Iterator[Enter]:
    """
    A click on the guest entry, through the site, as the button makes it; the visitors made are deleted after.
    """
    made: list[str] = []

    def click(**request: Any) -> dict[str, Any]:
        response = httpx.post(f"{site}/api/guest", timeout=30, **request)
        assert response.status_code == 200, response.status_code
        assert response.headers["cache-control"] == "no-store"
        body: dict[str, Any] = response.json()
        if "access_token" in body:
            made.append(body["access_token"])
        return body

    yield click
    for access in made:
        delete_itself(access)


def entered(body: dict[str, Any]) -> dict[str, Any]:
    if "refused" in body:
        pytest.skip(
            f"the guest entry answered {body['refused']}, so it made no user to check"
        )
    validator("guest", "entered").validate(body)
    return body


def test_a_click_makes_a_visitor_signed_in_as_a_development_customer(
    outputs: dict[str, Any], enter: Enter
) -> None:
    body = entered(enter())

    access, identity = claims(body["access_token"]), claims(body["id_token"])
    assert access["client_id"] == outputs["customer_client_id"]
    assert set(access["cognito:groups"]) == {"customer", "visitor"}
    assert identity["aud"] == outputs["customer_client_id"]
    assert str(access["username"]).startswith("visitor-")
    assert not held_out(access["customer_id"])
    assert body["welcome"]["name"].strip()


def test_the_welcome_names_the_cards_list_cards_reads_with_the_visitors_token(
    outputs: dict[str, Any], enter: Enter
) -> None:
    body = entered(enter())
    access = body["access_token"]

    listed = tool_output(call(outputs, access, "list_cards", arguments(access)))

    assert listed["outcome"] == "ok"
    assert body["welcome"]["cards"] == [
        {"product_type": c["product_type"], "last_four": c["last_four"]}
        for c in listed["cards"]
    ]


def test_each_click_is_a_user_and_a_sign_in_of_its_own(enter: Enter) -> None:
    first, second = entered(enter()), entered(enter())

    one, other = claims(first["access_token"]), claims(second["access_token"])
    assert one["sub"] != other["sub"]
    assert one["origin_jti"] != other["origin_jti"]


def test_it_reads_nothing_from_the_request(
    outputs: dict[str, Any], enter: Enter
) -> None:
    asked = next(i for i in (f"CLI-TEAM{n:08d}" for n in range(1, 99)) if held_out(i))

    body = entered(
        enter(
            json={
                "customer_id": asked,
                "groups": ["human_agent"],
                "client_id": outputs["staff_client_id"],
            },
            headers={"x-customer-id": asked},
            params={"customer_id": asked},
        )
    )

    access = claims(body["access_token"])
    assert access["customer_id"] != asked
    assert set(access["cognito:groups"]) == {"customer", "visitor"}


def test_the_staff_client_refuses_a_visitor(
    outputs: dict[str, Any], enter: Enter, cognito: CognitoIdentityProviderClient
) -> None:
    """
    The guest entry keeps no password, so the test sets one to ask the staff client as a visitor would have to.
    """
    body = entered(enter())
    username = str(claims(body["access_token"])["username"])
    password = "Visitor-" + "x" * 24 + "aA1!"
    cognito.admin_set_user_password(
        UserPoolId=outputs["user_pool_id"],
        Username=username,
        Password=password,
        Permanent=True,
    )

    with pytest.raises(ClientError) as refused:
        cognito.admin_initiate_auth(
            UserPoolId=outputs["user_pool_id"],
            ClientId=outputs["staff_client_id"],
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
        )
    assert refused.value.response["Error"]["Code"] == "UserLambdaValidationException"


def test_its_line_names_the_outcome_alone(
    outputs: dict[str, Any], enter: Enter
) -> None:
    since = datetime.now(UTC) - timedelta(minutes=1)
    body = enter()
    outcome = "entered" if "access_token" in body else body["refused"]
    logs: Any = boto3.client("logs", region_name="us-east-1")
    pages = logs.get_paginator("filter_log_events")

    lines: list[dict[str, Any]] = []
    for _ in range(20):
        lines = [
            json.loads(e["message"])
            for page in pages.paginate(
                logGroupName=outputs["guest"]["log_groups"]["entry"],
                filterPattern='{ $.metric = "guest_entry" }',
                startTime=int(since.timestamp() * 1000),
            )
            for e in page["events"]
        ]
        if lines:
            break
        time.sleep(4)

    assert any(line["outcome"] == outcome for line in lines), outcome
    # Lambda's own fields, the metric and the outcome, and a failure's kind: never a user, a customer, or a name.
    allowed = {
        "timestamp",
        "level",
        "message",
        "logger",
        "requestId",
        "metric",
        "outcome",
        "error",
        "code",
    }
    assert all(set(line) <= allowed for line in lines)
    written = json.dumps(lines)
    if "access_token" in body:
        access = claims(body["access_token"])
        for value in (
            access["username"],
            access["sub"],
            access["customer_id"],
            body["welcome"]["name"],
        ):
            assert value not in written


def test_the_cleanup_runs_under_its_own_role(outputs: dict[str, Any]) -> None:
    """
    It deletes only visitors made more than a day before, so a run on a stack deletes none of the suites' users.
    """
    ran = boto3.client("lambda", region_name="us-east-1").invoke(
        FunctionName=outputs["guest"]["cleanup"]
    )

    assert "FunctionError" not in ran
    assert isinstance(json.loads(ran["Payload"].read())["deleted"], int)

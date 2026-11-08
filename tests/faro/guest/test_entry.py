"""
Each click on the guest entry makes a new user in the visitor and customer groups as a customer from the curated list,
signs it in through the customers' app client, and answers its tokens with the welcome; it reads nothing from the
request, makes no user past the day's cap or while the chat is paused, and logs its outcome alone (ADR-0007, The guest
entry; SEC-03, SEC-04, SEC-05, OPS-08).
"""

import base64
import io
import json
import logging
from datetime import UTC, datetime
from typing import Any

import boto3
import pytest

from faro.contracts import validator
from faro.guest import entry as entry_module
from faro.guest.customers import Customer, encode
from faro.guest.entry import Entry, Settings, claims, gateway_cards, respond
from faro.guest.usage import CAP_KEY, counted

from .conftest import COUNTERS

LIST = "/banking-agent-local/guest-entry/customers"
CUSTOMERS = (
    Customer("CLI-TEAM00000001", "Team Customer 01"),
    Customer("CLI-TEAM00000003", "Team Customer 03"),
)
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
CARDS = [{"product_type": "Tarjeta Crédito", "last_four": "4821"}]


class Stack:
    def __init__(self, counters: Any) -> None:
        self.counters = counters
        self.cognito = boto3.client("cognito-idp", region_name="us-east-1")
        self.ssm = boto3.client("ssm", region_name="us-east-1")
        self.pool = self.cognito.create_user_pool(
            PoolName="banking-agent-local",
            Schema=[
                {"Name": "customer_id", "AttributeDataType": "String", "Mutable": True}
            ],
        )["UserPool"]["Id"]
        for group in ("customer", "visitor", "invited", "evaluation", "human_agent"):
            self.cognito.create_group(UserPoolId=self.pool, GroupName=group)
        self.client = self.cognito.create_user_pool_client(
            UserPoolId=self.pool,
            ClientName="customers",
            ExplicitAuthFlows=[
                "ALLOW_ADMIN_USER_PASSWORD_AUTH",
                "ALLOW_REFRESH_TOKEN_AUTH",
            ],
        )["UserPoolClient"]["ClientId"]
        self.ssm.put_parameter(
            Name=LIST, Value=encode("snap", CUSTOMERS), Type="String"
        )
        self.cap(750, 100)
        self.read_with: list[str] = []
        self.clock = 0.0

    def cap(self, turns: int, guests: int) -> None:
        self.counters.put_item(
            TableName=COUNTERS,
            Item={**CAP_KEY, "turns": {"N": str(turns)}, "guests": {"N": str(guests)}},
        )

    def cards(self, token: str) -> list[dict[str, str]] | None:
        self.read_with.append(token)
        return CARDS

    def entry(self, **overrides: Any) -> Entry:
        values: dict[str, Any] = {
            "cognito": self.cognito,
            "dynamodb": self.counters,
            "ssm": self.ssm,
            "settings": Settings(self.pool, self.client, COUNTERS, LIST),
            "cards": self.cards,
            "now": lambda: NOW,
            "clock": lambda: self.clock,
            "choose": lambda customers: customers[1],
            **overrides,
        }
        return Entry(**values)

    def users(self) -> list[Any]:
        return self.cognito.list_users(UserPoolId=self.pool)["Users"]

    def groups(self, username: str) -> set[str]:
        listed = self.cognito.admin_list_groups_for_user(
            UserPoolId=self.pool, Username=username
        )["Groups"]
        return {g["GroupName"] for g in listed}


@pytest.fixture
def stack(counters: Any) -> Stack:
    return Stack(counters)


def answered(response: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(response["body"])
    return body


def outcomes(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        vars(r)["outcome"]
        for r in caplog.records
        if getattr(r, "metric", None) == "guest_entry"
    ]


def test_a_click_makes_a_visitor_as_a_listed_customer_and_signs_it_in(
    stack: Stack, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    entry = stack.entry()

    response = respond(lambda: entry)

    assert response["statusCode"] == 200
    assert response["headers"]["Cache-Control"] == "no-store"
    body = answered(response)
    validator("guest", "entered").validate(body)
    assert body["welcome"] == {"name": "Team Customer 03", "cards": CARDS}
    assert stack.read_with == [body["access_token"]]
    (user,) = stack.users()
    attributes = {a["Name"]: a["Value"] for a in user["Attributes"]}
    assert attributes["custom:customer_id"] == "CLI-TEAM00000003"
    assert user["UserStatus"] == "CONFIRMED"
    assert user["Username"].startswith("visitor-")
    assert stack.groups(user["Username"]) == {"visitor", "customer"}
    assert claims(body["access_token"])["username"] == user["Username"]
    assert counted(stack.counters, COUNTERS, NOW) == 1
    assert outcomes(caplog) == ["entered"]
    logged = caplog.text + json.dumps([r.__dict__ for r in caplog.records], default=str)
    for secret in (
        user["Username"],
        "CLI-TEAM00000003",
        "Team Customer",
        body["access_token"],
    ):
        assert secret not in logged


def test_each_click_is_a_user_of_its_own(stack: Stack) -> None:
    entry = stack.entry()

    first, second = answered(respond(lambda: entry)), answered(respond(lambda: entry))

    assert claims(first["access_token"])["sub"] != claims(second["access_token"])["sub"]
    assert len(stack.users()) == 2


def test_it_reads_nothing_from_the_request(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in {
        "USER_POOL_ID": stack.pool,
        "CUSTOMER_CLIENT_ID": stack.client,
        "USAGE_COUNTERS_TABLE": COUNTERS,
        "GUEST_LIST_PARAMETER": LIST,
        "GATEWAY_URL": "https://gateway.example",
        "LIST_CARDS_TARGET": "reads",
        "AWS_DEFAULT_REGION": "us-east-1",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(entry_module, "gateway_cards", lambda url, target: stack.cards)
    only = (Customer("CLI-TEAM00000001", "Team Customer 01"),)
    stack.ssm.put_parameter(
        Name=LIST, Value=encode("snap", only), Type="String", Overwrite=True
    )
    entry_module.entry.cache_clear()
    event = {
        "body": json.dumps(
            {"customer_id": "CLI-TEAM00000003", "groups": ["human_agent"]}
        ),
        "headers": {"x-customer-id": "CLI-TEAM00000003"},
        "queryStringParameters": {"client_id": "staff"},
    }

    try:
        response = entry_module.handler(event, None)
    finally:
        entry_module.entry.cache_clear()

    assert response["statusCode"] == 200
    (user,) = stack.users()
    attributes = {a["Name"]: a["Value"] for a in user["Attributes"]}
    assert attributes["custom:customer_id"] == "CLI-TEAM00000001"
    assert stack.groups(user["Username"]) == {"visitor", "customer"}


@pytest.mark.parametrize(
    ("turns", "guests", "refused"), [(750, 1, "full"), (0, 100, "paused")]
)
def test_a_full_day_and_the_pause_are_answers_that_make_no_user(
    stack: Stack,
    caplog: pytest.LogCaptureFixture,
    turns: int,
    guests: int,
    refused: str,
) -> None:
    caplog.set_level(logging.INFO)
    stack.cap(turns, guests)
    entry = stack.entry()
    if refused == "full":
        respond(lambda: entry)

    response = respond(lambda: entry)

    assert response["statusCode"] == 200
    assert answered(response) == {"refused": refused}
    assert len(stack.users()) == (1 if refused == "full" else 0)
    assert outcomes(caplog)[-1] == refused


def test_the_welcome_names_the_customer_alone_when_the_cards_cant_be_read(
    stack: Stack, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(token: str) -> list[dict[str, str]] | None:
        raise TimeoutError("the Gateway took too long")

    response = respond(lambda: stack.entry(cards=broken))

    assert response["statusCode"] == 200
    assert answered(response)["welcome"] == {"name": "Team Customer 03", "cards": None}
    assert "the welcome's cards couldn't be read" in caplog.text


@pytest.mark.parametrize("missing", ["list", "guests' cap"])
def test_a_failure_answers_500_and_logs_its_kind_alone(
    stack: Stack, caplog: pytest.LogCaptureFixture, missing: str
) -> None:
    if missing == "list":
        stack.ssm.delete_parameter(Name=LIST)
    else:
        stack.counters.put_item(
            TableName=COUNTERS, Item={**CAP_KEY, "turns": {"N": "750"}}
        )

    response = respond(lambda: stack.entry())

    assert response["statusCode"] == 500
    assert json.loads(response["body"]) == {"message": "Internal Server Error"}
    (failed,) = [r for r in caplog.records if getattr(r, "outcome", None) == "failed"]
    assert vars(failed)["error"] == (
        "ParameterNotFound" if missing == "list" else "LookupError"
    )
    assert stack.users() == []


def test_the_list_is_read_again_once_it_may_have_changed(stack: Stack) -> None:
    entry = stack.entry(choose=lambda customers: customers[-1])
    changed = (Customer("CLI-TEAM00000004", "Team Customer 04"),)

    respond(lambda: entry)
    stack.ssm.put_parameter(
        Name=LIST, Value=encode("snap", changed), Type="String", Overwrite=True
    )
    soon = answered(respond(lambda: entry))
    stack.clock = 301.0
    later = answered(respond(lambda: entry))

    assert soon["welcome"]["name"] == "Team Customer 03"
    assert later["welcome"]["name"] == "Team Customer 04"


def token_with(**payload: str) -> str:
    def part(value: dict[str, str]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{part({'alg': 'RS256'})}.{part(payload)}.signature"


class Answer(io.BytesIO):
    def __init__(self, body: str, content_type: str) -> None:
        super().__init__(body.encode())
        self.headers = {"Content-Type": content_type}

    def __enter__(self) -> "Answer":
        return self


def listed(output: dict[str, Any]) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": json.dumps(output)}]},
        }
    )


OUTPUT = {
    "outcome": "ok",
    "cards": [
        {
            "card_id": "PRD-TEAM00000031",
            "product_type": "Tarjeta Crédito",
            "last_four": "4821",
            "product_status": "Active",
        },
        {
            "card_id": "PRD-TEAM00000033",
            "product_type": "Tarjeta Débito",
            "last_four": "1177",
            "product_status": "Blocked",
        },
    ],
}


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (listed(OUTPUT), "application/json"),
        (f"event: message\ndata: {listed(OUTPUT)}\n\n", "text/event-stream"),
    ],
)
def test_the_cards_are_list_cards_as_the_agent_calls_it(
    monkeypatch: pytest.MonkeyPatch, body: str, content_type: str
) -> None:
    sent: list[Any] = []

    def urlopen(request: Any, timeout: float) -> Answer:
        sent.append((request, timeout))
        return Answer(body, content_type)

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    token = token_with(customer_id="CLI-TEAM00000003", origin_jti="jti-1")

    cards = gateway_cards("https://gateway.example/mcp", "reads")(token)

    assert cards == [
        {"product_type": "Tarjeta Crédito", "last_four": "4821"},
        {"product_type": "Tarjeta Débito", "last_four": "1177"},
    ]
    ((request, timeout),) = sent
    assert request.full_url == "https://gateway.example/mcp"
    assert request.get_header("Authorization") == f"Bearer {token}"
    assert json.loads(request.data)["params"] == {
        "name": "reads___list_cards",
        "arguments": {"customer_id": "CLI-TEAM00000003", "origin_jti": "jti-1"},
    }
    assert timeout == 5.0


def test_cards_the_tool_didnt_list_are_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: Answer(
            listed({"outcome": "invalid_input"}), "application/json"
        ),
    )

    token = token_with(customer_id="CLI-TEAM00000003", origin_jti="jti-1")
    assert gateway_cards("https://gateway.example/mcp", "reads")(token) is None


def test_the_gateway_is_called_over_https_only() -> None:
    with pytest.raises(ValueError, match="https"):
        gateway_cards("http://gateway.example/mcp", "reads")

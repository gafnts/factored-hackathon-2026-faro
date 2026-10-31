"""
The pre-token trigger holds each user to their side's app client and puts customer_id in customers' tokens only
(ADR-0007, Sign-in; POL-07; SEC-04, SEC-05).
"""

from typing import Any

import pytest

from faro.identity.pre_token import SignInRefusedError, handler, side_of

CUSTOMERS = "customers-client-id"
STAFF = "staff-client-id"


@pytest.fixture(autouse=True)
def clients(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CUSTOMER_CLIENT_ID", CUSTOMERS)
    monkeypatch.setenv("STAFF_CLIENT_ID", STAFF)


def event(
    client: str,
    groups: list[str],
    customer_id: str | None = "CLI-EXAMPLE00001",
    trigger: str = "TokenGeneration_Authentication",
) -> dict[str, Any]:
    attributes = {"sub": "f3b0c7a2-5d1e-4c8b-9a6f-2e7d1b4c8a90"}
    if customer_id is not None:
        attributes["custom:customer_id"] = customer_id
    return {
        "version": "2",
        "triggerSource": trigger,
        "userName": "persona-ana",
        "callerContext": {"clientId": client},
        "request": {
            "userAttributes": attributes,
            "groupConfiguration": {"groupsToOverride": groups},
        },
        "response": {"claimsAndScopeOverrideDetails": None},
    }


def added_claims(result: dict[str, Any]) -> dict[str, Any] | None:
    details = result["response"]["claimsAndScopeOverrideDetails"]
    if details is None:
        return None
    claims: dict[str, Any] = details["accessTokenGeneration"]["claimsToAddOrOverride"]
    return claims


@pytest.mark.parametrize(
    "trigger", ["TokenGeneration_Authentication", "TokenGeneration_RefreshTokens"]
)
def test_a_customer_signing_in_through_the_customers_client_gets_their_customer_id(
    trigger: str,
) -> None:
    result = handler(event(CUSTOMERS, ["customer"], trigger=trigger), None)

    assert added_claims(result) == {"customer_id": "CLI-EXAMPLE00001"}


def test_an_evaluation_user_signs_in_as_a_customer() -> None:
    result = handler(event(CUSTOMERS, ["customer", "evaluation"]), None)

    assert added_claims(result) == {"customer_id": "CLI-EXAMPLE00001"}


@pytest.mark.parametrize("group", ["human_agent", "ai_team"])
def test_staff_signing_in_through_the_staff_client_get_no_customer_id(
    group: str,
) -> None:
    assert added_claims(handler(event(STAFF, [group]), None)) is None


def test_a_customer_without_a_customer_id_gets_a_token_without_the_claim() -> None:
    result = handler(event(CUSTOMERS, ["customer"], customer_id=None), None)

    assert added_claims(result) is None


@pytest.mark.parametrize(
    ("client", "groups"),
    [
        (STAFF, ["customer"]),
        (STAFF, ["customer", "evaluation"]),
        (CUSTOMERS, ["human_agent"]),
        (CUSTOMERS, ["ai_team"]),
        (CUSTOMERS, []),
        (STAFF, []),
        (CUSTOMERS, ["evaluation"]),
        (CUSTOMERS, ["customer", "human_agent"]),
        (STAFF, ["customer", "ai_team"]),
        ("another-client-id", ["customer"]),
    ],
)
def test_a_user_is_refused_a_token_from_the_other_sides_client(
    client: str, groups: list[str]
) -> None:
    with pytest.raises(SignInRefusedError, match=r"^Sign-in refused\.$"):
        handler(event(client, groups), None)


def test_a_refused_refresh_is_refused_too() -> None:
    with pytest.raises(SignInRefusedError):
        handler(
            event(STAFF, ["customer"], trigger="TokenGeneration_RefreshTokens"), None
        )


def test_a_refusal_names_the_sides_in_the_log_and_never_the_customer_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(SignInRefusedError):
        handler(event(STAFF, ["customer"]), None)

    assert "user persona-ana is on customers, client on staff" in caplog.text
    assert "CLI-EXAMPLE00001" not in caplog.text


def test_a_missing_group_configuration_counts_as_no_group() -> None:
    unconfigured = event(CUSTOMERS, [])
    del unconfigured["request"]["groupConfiguration"]

    with pytest.raises(SignInRefusedError):
        handler(unconfigured, None)


@pytest.mark.parametrize(
    ("groups", "side"),
    [
        (["customer"], "customers"),
        (["customer", "evaluation"], "customers"),
        (["human_agent"], "staff"),
        (["human_agent", "ai_team"], "staff"),
        ([], None),
        (["evaluation"], None),
        (["customer", "ai_team"], None),
    ],
)
def test_side_of(groups: list[str], side: str | None) -> None:
    assert side_of(groups) == side

"""
file_handoff files only for a live customer token of the customers' app client: Cognito's GetUser answers for the token,
and its claims must name the access use, the client, the customer group, the same user, and the same customer (ADR-0004,
Where the tools run, and its amendment of 2026-09-30; POL-07, SEC-04, SEC-05).
"""

import base64
import json
from typing import Any

import pytest
from botocore.exceptions import ClientError

from faro.tools.identity import Caller, CognitoVerifier, caller_of

CLIENT = "customer-client-0001"
SUB = "7d3e1f0a-2b4c-4d6e-8f10-a1b2c3d4e5f6"
SIGN_IN = "5f0d6c1e-8a3b-4f27-b9d4-7e2c1a9f3b68"
CUSTOMER = "CLI-EXAMPLE00001"
USER = {
    "Username": "persona",
    "UserAttributes": [
        {"Name": "sub", "Value": SUB},
        {"Name": "custom:customer_id", "Value": CUSTOMER},
    ],
}


def token(**overrides: Any) -> str:
    claims = {
        "sub": SUB,
        "origin_jti": SIGN_IN,
        "customer_id": CUSTOMER,
        "cognito:groups": ["customer"],
        "token_use": "access",
        "client_id": CLIENT,
        **overrides,
    }
    claims = {k: v for k, v in claims.items() if v is not None}

    def part(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature"


class Cognito:
    def __init__(self, answer: dict[str, Any] | str = USER) -> None:
        self.answer = answer
        self.calls = 0

    def get_user(self, AccessToken: str) -> dict[str, Any]:  # noqa: N803
        self.calls += 1
        if isinstance(self.answer, str):
            raise ClientError({"Error": {"Code": self.answer}}, "GetUser")
        return self.answer


def verifier(cognito: Cognito) -> CognitoVerifier:
    return CognitoVerifier(cognito, CLIENT)  # type: ignore[arg-type]


def test_a_live_customer_token_names_its_caller() -> None:
    assert verifier(Cognito()).verify(token()) == Caller(SUB, CUSTOMER, SIGN_IN, "demo")
    assert caller_of(
        token(**{"cognito:groups": ["customer", "evaluation"]}), USER, CLIENT
    ) == Caller(SUB, CUSTOMER, SIGN_IN, "evaluation")


@pytest.mark.parametrize(
    "overrides",
    [
        {"token_use": "id"},
        {"client_id": "staff-client-0001"},
        {"cognito:groups": ["human_agent"]},
        {"cognito:groups": None},
        {"customer_id": "CLI-EXAMPLE00009"},
        {"customer_id": None},
        {"sub": "0f9e8d7c-6b5a-4c3d-9e2f-1a0b9c8d7e6f"},
        {"origin_jti": None},
        {"origin_jti": "not-a-sign-in"},
    ],
)
def test_a_token_that_isnt_this_customers_names_no_caller(
    overrides: dict[str, Any],
) -> None:
    assert verifier(Cognito()).verify(token(**overrides)) is None


@pytest.mark.parametrize(
    "code",
    ["NotAuthorizedException", "UserNotFoundException", "InvalidParameterException"],
)
def test_a_token_cognito_wont_honor_names_no_caller(code: str) -> None:
    assert verifier(Cognito(code)).verify(token()) is None


def test_a_fault_at_cognito_is_raised_not_read_as_a_refusal() -> None:
    with pytest.raises(ClientError):
        verifier(Cognito("InternalErrorException")).verify(token())


def test_something_that_isnt_a_token_never_reaches_cognito() -> None:
    cognito = Cognito()

    for given in ("", "not-a-token", "a.b.c"):
        assert verifier(cognito).verify(given) is None
    assert cognito.calls == 0

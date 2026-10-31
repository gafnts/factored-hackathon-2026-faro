"""
Only an access token of the staff app client in the human_agent group passes the console's own check, whatever the
authorizer let through (ADR-0007, The console API, and its amendments of 2026-09-30; SEC-05, CTL-04, EVL-04).
"""

from typing import Any

import pytest

from faro.console.access import groups_of, staff_of

STAFF_CLIENT = "staffclient0000000000000000"
SUB = "4b6f1c2d-8e9a-4f3b-a1c5-7d2e9f0b6a18"


def event(**claims: Any) -> dict[str, Any]:
    signed = {
        "sub": SUB,
        "token_use": "access",
        "client_id": STAFF_CLIENT,
        "cognito:groups": "[human_agent]",
        "scope": "aws.cognito.signin.user.admin",
        **claims,
    }
    return {"requestContext": {"authorizer": {"jwt": {"claims": signed}}}}


@pytest.mark.parametrize(
    "groups",
    [
        "[human_agent]",
        "[ai_team human_agent]",
        ["human_agent"],
    ],
)
def test_a_human_agents_access_token_passes(groups: Any) -> None:
    staff = staff_of(event(**{"cognito:groups": groups}), STAFF_CLIENT)

    assert staff is not None
    assert staff.sub == SUB


@pytest.mark.parametrize(
    "claims",
    [
        {"cognito:groups": "[ai_team]"},
        {"cognito:groups": "[customer]"},
        {"cognito:groups": "[]"},
        {"cognito:groups": "human_agent"},
        {"cognito:groups": None},
        {"token_use": "id"},
        {"client_id": "customersclient000000000000"},
        {"sub": "not-a-uuid"},
        {"sub": None},
    ],
)
def test_anyone_else_is_refused(claims: dict[str, Any]) -> None:
    assert staff_of(event(**claims), STAFF_CLIENT) is None


@pytest.mark.parametrize("unsigned", [{}, {"requestContext": {}}, None, "claims"])
def test_an_event_without_claims_is_refused(unsigned: Any) -> None:
    assert staff_of(unsigned, STAFF_CLIENT) is None


def test_a_group_whose_name_holds_the_role_is_another_group() -> None:
    # Cognito allows a comma in a group's name, never a space.
    for claim in ("[x,human_agent]", "[human_agent,x]", ["x,human_agent"]):
        assert staff_of(event(**{"cognito:groups": claim}), STAFF_CLIENT) is None


def test_groups_are_read_from_a_list_or_a_bracketed_string_only() -> None:
    assert groups_of("[a b]") == ["a", "b"]
    assert groups_of(["a"]) == ["a"]
    assert groups_of("a b") is None
    assert groups_of(["a", 1]) is None
    assert groups_of(7) is None

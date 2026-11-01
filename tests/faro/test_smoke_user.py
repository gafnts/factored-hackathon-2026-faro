"""
The smoke user: the declines persona's customer under a username that names no one, made or reset by one command,
its credentials on disk with owner-only permissions and never printed (ADR-0009; SEC-03).
"""

import json
from pathlib import Path

import boto3
import pytest

from faro import personas, smoke_user

from .test_judges import CHOSEN, FakeCognito

CUSTOMER = CHOSEN[smoke_user.PERSONA].customer_id


class Cognito(FakeCognito):
    def admin_update_user_attributes(self, **kwargs: object) -> None:
        self.calls.append(("attributes", kwargs))


def test_a_new_smoke_user_is_the_personas_customer_signed_in_by_no_ones_name() -> None:
    cognito = Cognito()

    password = smoke_user.ready(cognito, "pool", CUSTOMER)

    kinds = [kind for kind, _ in cognito.calls]
    assert kinds == ["create", "group", "password", "sign_out"]
    created = cognito.calls[0][1]
    assert created["Username"] == "smoke-check"
    assert created["UserAttributes"] == [
        {"Name": "custom:customer_id", "Value": CUSTOMER}
    ]
    assert cognito.calls[1][1]["GroupName"] == "customer"
    assert cognito.calls[2][1]["Password"] == password


def test_a_standing_smoke_user_is_reset_to_the_personas_current_customer() -> None:
    cognito = Cognito(existing={"smoke-check"})

    password = smoke_user.ready(cognito, "pool", CUSTOMER)

    kinds = [kind for kind, _ in cognito.calls]
    assert kinds == ["attributes", "group", "password", "sign_out"]
    assert cognito.calls[0][1]["UserAttributes"] == [
        {"Name": "custom:customer_id", "Value": CUSTOMER}
    ]
    assert cognito.calls[2][1]["Password"] == password


def test_the_credentials_go_to_an_owner_only_file_and_never_to_the_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stack = tmp_path / "local.outputs.json"
    stack.write_text(json.dumps({"user_pool_id": {"value": "pool"}}), encoding="utf-8")
    cognito = Cognito()
    monkeypatch.setattr(boto3, "client", lambda *_, **__: cognito)
    monkeypatch.setattr(personas, "read", lambda *_: CHOSEN)

    smoke_user.main(["--stack", str(stack), "--data-dir", str(tmp_path)])

    path = tmp_path / "smoke" / "local.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["environment"] == "local"
    assert body["username"] == "smoke-check"
    assert path.stat().st_mode & 0o777 == 0o600
    printed = capsys.readouterr().out
    assert str(path) in printed
    assert body["password"] not in printed
    assert CUSTOMER not in printed


def test_a_missing_stack_stops_before_any_user_is_made(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="run make outputs"):
        smoke_user.main(["--stack", str(tmp_path / "local.outputs.json")])

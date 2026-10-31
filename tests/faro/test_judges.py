"""
The judges' users: the personas created by their customers' names with the customer group, the staff with theirs,
credentials and briefings on disk and never printed, and a rotation that goes through reset (ADR-0007, Judges' access;
SEC-03).
"""

import json
import string
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from faro import judges
from faro.dataset.lock import read_lock
from faro.personas import HOOKS, Persona


class FakeCognito:
    def __init__(self, existing: set[str] | None = None) -> None:
        self.existing = existing or set()
        self.calls: list[tuple[str, dict[str, object]]] = []

    def admin_create_user(self, **kwargs: object) -> None:
        if kwargs["Username"] in self.existing:
            raise ClientError(
                {"Error": {"Code": "UsernameExistsException", "Message": ""}},
                "AdminCreateUser",
            )
        self.calls.append(("create", kwargs))

    def admin_set_user_password(self, **kwargs: object) -> None:
        self.calls.append(("password", kwargs))

    def admin_add_user_to_group(self, **kwargs: object) -> None:
        self.calls.append(("group", kwargs))

    def admin_user_global_sign_out(self, **kwargs: object) -> None:
        self.calls.append(("sign_out", kwargs))

    def admin_disable_user(self, **kwargs: object) -> None:
        self.calls.append(("disable", kwargs))

    def admin_enable_user(self, **kwargs: object) -> None:
        self.calls.append(("enable", kwargs))

    def admin_delete_user(self, **kwargs: object) -> None:
        self.calls.append(("delete", kwargs))


# Development IDs only: 2, 8, and 9 are held out.
DEVELOPMENT = (1, 3, 4, 5, 6, 7, 10, 11)
CHOSEN = {
    scenario: Persona(f"CLI-TEAM{n:08d}", f"team.{scenario}", f"brief {scenario}")
    for n, scenario in zip(DEVELOPMENT, sorted(HOOKS), strict=True)
}


def test_create_makes_each_persona_by_name_and_the_staff_with_their_groups() -> None:
    cognito = FakeCognito()

    created = judges.create(cognito, "pool", CHOSEN)

    assert sorted(created) == sorted(
        [p.username for p in CHOSEN.values()] + ["agente", "equipo-ia"]
    )
    groups = {
        (c["Username"], c["GroupName"]) for kind, c in cognito.calls if kind == "group"
    }
    assert groups == {(p.username, "customer") for p in CHOSEN.values()} | {
        ("agente", "human_agent"),
        ("equipo-ia", "ai_team"),
    }
    attributes = {
        c["Username"]: c["UserAttributes"]
        for kind, c in cognito.calls
        if kind == "create"
    }
    for persona in CHOSEN.values():
        assert attributes[persona.username] == [
            {"Name": "custom:customer_id", "Value": persona.customer_id}
        ]
    assert attributes["agente"] == []
    passwords = [c for kind, c in cognito.calls if kind == "password"]
    assert all(c["Permanent"] is True for c in passwords)


def test_every_user_carries_its_briefing() -> None:
    cognito = FakeCognito()

    created = judges.create(cognito, "pool", CHOSEN)

    for persona in CHOSEN.values():
        assert created[persona.username]["briefing"] == persona.briefing
    assert "/cases" in created["agente"]["briefing"]
    assert "/cases" in created["equipo-ia"]["briefing"]


def test_every_password_carries_each_class_the_pool_requires() -> None:
    secret = judges.password()

    assert len(secret) >= 12
    assert any(c in string.ascii_lowercase for c in secret)
    assert any(c in string.ascii_uppercase for c in secret)
    assert any(c in string.digits for c in secret)
    assert any(c not in string.ascii_letters + string.digits for c in secret)


def test_an_existing_user_without_credentials_refuses_the_run_and_names_delete() -> (
    None
):
    cognito = FakeCognito(existing={"agente"})

    with pytest.raises(judges.JudgesError, match="agente.*delete"):
        judges.create(cognito, "pool", CHOSEN)


def test_an_existing_user_with_credentials_is_kept_and_not_remade() -> None:
    cognito = FakeCognito(existing={"agente"})

    created = judges.create(cognito, "pool", CHOSEN, recorded={"agente"})

    assert "agente" not in created
    assert "equipo-ia" in created
    assert all(c["Username"] != "agente" for _, c in cognito.calls)


def test_a_run_that_stops_partway_keeps_what_it_made_and_resumes(
    tmp_path: Path,
) -> None:
    stack = write_stack_and_personas(tmp_path)
    args = judges.parse(["create", "--stack", str(stack), "--data-dir", str(tmp_path)])
    path = judges.credentials_path(tmp_path, "local")
    first = FakeCognito()
    failing = CHOSEN["mixed"].username
    original = first.admin_add_user_to_group

    def add_to_group(**kwargs: object) -> None:
        if kwargs["Username"] == failing:
            raise ClientError(
                {"Error": {"Code": "TooManyRequestsException", "Message": ""}},
                "AdminAddUserToGroup",
            )
        original(**kwargs)

    first.admin_add_user_to_group = add_to_group  # type: ignore[method-assign]

    with pytest.raises(ClientError):
        judges.run(args, first)

    made = {str(c["Username"]) for kind, c in first.calls if kind == "group"}
    assert set(judges.read_users(path)) == made
    assert judges.note_path(tmp_path, "local").is_file()

    with pytest.raises(judges.JudgesError, match="delete"):
        judges.run(args, FakeCognito(existing=made | {failing}))

    said = judges.run(args, FakeCognito(existing=made))

    assert set(judges.read_users(path)) == {p.username for p in CHOSEN.values()} | set(
        judges.STAFF
    )
    assert f"kept {len(made)}" in said


def test_credentials_land_on_disk_owner_only_and_a_reset_keeps_the_briefing(
    tmp_path: Path,
) -> None:
    path = judges.credentials_path(tmp_path, "local")

    judges.write_credentials(
        path,
        "local",
        {
            "team.quiet": {"password": "a", "briefing": "brief quiet"},
            "agente": {"password": "b", "briefing": "brief agente"},
        },
    )
    judges.write_credentials(path, "local", {"team.quiet": {"password": "c"}})

    body = json.loads(path.read_text(encoding="utf-8"))
    assert body == {
        "environment": "local",
        "users": {
            "team.quiet": {"password": "c", "briefing": "brief quiet"},
            "agente": {"password": "b", "briefing": "brief agente"},
        },
    }
    assert path.stat().st_mode & 0o777 == 0o600


def test_reset_sets_a_new_password_and_signs_out_everywhere() -> None:
    cognito = FakeCognito()

    secret = judges.reset(cognito, "pool", "team.quiet")

    kinds = [kind for kind, _ in cognito.calls]
    assert kinds == ["password", "sign_out"]
    assert cognito.calls[0][1]["Password"] == secret


def test_the_single_user_commands_reach_their_admin_calls() -> None:
    cognito = FakeCognito()

    judges.sign_out(cognito, "pool", "agente")
    judges.disable(cognito, "pool", "agente")
    judges.enable(cognito, "pool", "agente")

    assert [kind for kind, _ in cognito.calls] == ["sign_out", "disable", "enable"]


def write_stack_and_personas(tmp_path: Path) -> Path:
    stack = tmp_path / "local.outputs.json"
    stack.write_text(json.dumps({"user_pool_id": {"value": "pool"}}), encoding="utf-8")
    snapshot = read_lock(Path("dataset.lock")).snapshot_id
    (tmp_path / "personas").mkdir()
    (tmp_path / "personas" / f"{snapshot}.json").write_text(
        json.dumps(
            {
                "snapshot": snapshot,
                "personas": {
                    s: {
                        "customer_id": p.customer_id,
                        "username": p.username,
                        "briefing": p.briefing,
                    }
                    for s, p in CHOSEN.items()
                },
            }
        ),
        encoding="utf-8",
    )
    return stack


def test_run_prints_no_password_username_or_customer_id(tmp_path: Path) -> None:
    stack = write_stack_and_personas(tmp_path)
    args = judges.parse(["create", "--stack", str(stack), "--data-dir", str(tmp_path)])
    cognito = FakeCognito()

    said = judges.run(args, cognito)

    body = json.loads(
        judges.credentials_path(tmp_path, "local").read_text(encoding="utf-8")
    )
    for username, fields in body["users"].items():
        assert username not in said
        assert fields["password"] not in said
    for persona in CHOSEN.values():
        assert persona.customer_id not in said


def test_the_note_renders_every_account_ready_to_send(tmp_path: Path) -> None:
    path = judges.note_path(tmp_path, "local")
    users = {
        "team.quiet": {"password": "a", "briefing": "brief quiet"},
        "agente": {"password": "b", "briefing": "brief agente"},
        # An entry an older file wrote as a bare password still renders.
        "legacy": "c",
    }

    judges.write_note(path, "https://example.test", users)

    note = path.read_text(encoding="utf-8")
    assert "https://example.test" in note
    assert "one hour" in note
    for username in users:
        assert f"## {username}" in note
    assert "Password: `a`" in note
    assert "brief quiet" in note
    assert "Password: `c`" in note
    assert path.stat().st_mode & 0o777 == 0o600


def test_delete_retires_the_user_from_the_pool_the_file_and_the_note(
    tmp_path: Path,
) -> None:
    stack = tmp_path / "local.outputs.json"
    stack.write_text(
        json.dumps(
            {
                "user_pool_id": {"value": "pool"},
                "site": {"value": {"url": "https://example.test"}},
            }
        ),
        encoding="utf-8",
    )
    path = judges.credentials_path(tmp_path, "local")
    judges.write_credentials(
        path,
        "local",
        {
            "team.quiet": {"password": "a", "briefing": "brief quiet"},
            "agente": {"password": "b", "briefing": "brief agente"},
        },
    )
    cognito = FakeCognito()
    args = judges.parse(
        [
            "delete",
            "--stack",
            str(stack),
            "--user",
            "team.quiet",
            "--data-dir",
            str(tmp_path),
        ]
    )

    said = judges.run(args, cognito)

    assert [kind for kind, _ in cognito.calls] == ["delete"]
    assert cognito.calls[0][1]["Username"] == "team.quiet"
    assert "team.quiet" not in said
    body = json.loads(path.read_text(encoding="utf-8"))
    assert sorted(body["users"]) == ["agente"]
    note = judges.note_path(tmp_path, "local").read_text(encoding="utf-8")
    assert "team.quiet" not in note
    assert "## agente" in note


def test_a_command_on_one_user_needs_the_user() -> None:
    with pytest.raises(SystemExit):
        judges.parse(["reset", "--stack", "build/local.outputs.json"])
    with pytest.raises(SystemExit):
        judges.parse(["delete", "--stack", "build/local.outputs.json"])


def test_a_stack_file_that_isnt_an_outputs_file_is_refused() -> None:
    with pytest.raises(judges.JudgesError, match="outputs"):
        judges.environment(Path("build/local.json"))

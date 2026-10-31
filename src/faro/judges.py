"""
The judges' users (ADR-0007, Judges' access): one script creates the personas' and the staff's users in the deployed
stack's pool, and resets, signs out, disables, or enables one. The personas come from data/personas/ (make personas),
each signed in by its customer's synthetic name with custom:customer_id and the customer group; each password satisfies
every class the pool requires and goes with the account's briefing to data/judges/<env>.json with owner-only
permissions as each user is made, from which the private note, data/judges/<env>.md, is rendered whole on every
change, ready to send.
Usernames are names and so identity data: the script prints counts and paths, never a username, a password, or an ID
(SEC-03). A reset also signs the user out everywhere, so a leaked credential is cut off within the access token's
15 minutes, and delete retires a user with its entries.
"""

import argparse
import json
import secrets
import string
from collections.abc import Callable, Collection, Sequence
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from faro import personas
from faro.dataset.lock import read_lock

STAFF = {
    "agente": (
        "human_agent",
        "You are a LATAM Bank case agent. Sign in at /cases: handoffs from the customer "
        "chats arrive in your queues within seconds, each case carrying its evidence, "
        "and a reference from a chat finds its case.",
    ),
    "equipo-ia": (
        "ai_team",
        "You are on the bank's AI team. Your page is designed, not built, so /cases "
        "turns you away: the role gate is what this account shows.",
    ),
}
COMMANDS = ("create", "reset", "sign-out", "disable", "enable", "delete")
ALPHABET = string.ascii_letters + string.digits


class JudgesError(Exception):
    pass


def password() -> str:
    # The suffix guarantees every class the pool's policy requires, as the integration suite's users do.
    return "".join(secrets.choice(ALPHABET) for _ in range(24)) + "aA1!"


def stack_outputs(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise JudgesError(f"{path} not found; run make outputs")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {name: value["value"] for name, value in raw.items()}


def environment(stack: Path) -> str:
    name = stack.name.removesuffix(".outputs.json")
    if not name or name == stack.name:
        raise JudgesError(f"{stack} isn't a <env>.outputs.json file")
    return name


def credentials_path(data_dir: Path, env: str) -> Path:
    return data_dir / "judges" / f"{env}.json"


def note_path(data_dir: Path, env: str) -> Path:
    return data_dir / "judges" / f"{env}.md"


def read_users(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    users: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))["users"]
    return users


def write_credentials(path: Path, env: str, users: dict[str, dict[str, str]]) -> None:
    body: dict[str, Any] = {"environment": env, "users": {}}
    if path.is_file():
        body = json.loads(path.read_text(encoding="utf-8"))
    # A reset carries no briefing, so each user's new fields land over the kept ones; an entry an older file wrote
    # as a bare password is replaced whole.
    for username, fields in users.items():
        kept = body["users"].get(username)
        body["users"][username] = {**(kept if isinstance(kept, dict) else {}), **fields}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def drop_credentials(path: Path, username: str) -> None:
    if not path.is_file():
        return
    body = json.loads(path.read_text(encoding="utf-8"))
    body["users"].pop(username, None)
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def write_note(path: Path, site: str | None, users: dict[str, Any]) -> None:
    """
    The private note for the organizers, rendered whole from the credentials on each change, ready to send
    (ADR-0007, Judges' access). It holds names and passwords, so it stays under data/ with owner-only permissions.
    """
    lines = ["# Faro: the judges' accounts", ""]
    if site:
        lines += [f"The prototype runs at {site}; all its data is synthetic.", ""]
    lines += [
        "A sign-in lasts one hour; signing in again continues where you were.",
        "",
    ]
    for username, value in users.items():
        fields = value if isinstance(value, dict) else {"password": value}
        lines += [f"## {username}", "", f"Password: `{fields['password']}`", ""]
        if "briefing" in fields:
            lines += [fields["briefing"], ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    path.chmod(0o600)


def create(
    cognito: Any,
    pool: str,
    chosen: dict[str, personas.Persona],
    recorded: Collection[str] = (),
    record: Callable[[str, dict[str, str]], None] | None = None,
) -> dict[str, dict[str, str]]:
    """
    Returns each username this run made with its password and briefing, handing each to record as soon as it's
    made, so a run that stops partway loses no password and runs again from where it stopped. A user already in the
    pool is kept when recorded holds it, and refuses the run otherwise: its password is lost, so it's deleted and
    made again, and a rotation goes through reset.
    """
    wanted: list[tuple[str, str, str | None, str]] = [
        (p.username, "customer", p.customer_id, p.briefing)
        for _, p in sorted(chosen.items())
    ] + [
        (username, group, None, briefing)
        for username, (group, briefing) in STAFF.items()
    ]
    created: dict[str, dict[str, str]] = {}
    for username, group, customer_id, briefing in wanted:
        attributes = (
            [{"Name": "custom:customer_id", "Value": customer_id}]
            if customer_id
            else []
        )
        try:
            cognito.admin_create_user(
                UserPoolId=pool,
                Username=username,
                UserAttributes=attributes,
                MessageAction="SUPPRESS",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "UsernameExistsException":
                raise
            if username in recorded:
                continue
            raise JudgesError(
                f"{username} is in the pool without saved credentials; delete it, then create again"
            ) from error
        word = password()
        cognito.admin_set_user_password(
            UserPoolId=pool, Username=username, Password=word, Permanent=True
        )
        cognito.admin_add_user_to_group(
            UserPoolId=pool, Username=username, GroupName=group
        )
        fields = {"password": word, "briefing": briefing}
        if record:
            record(username, fields)
        created[username] = fields
    return created


def reset(cognito: Any, pool: str, username: str) -> str:
    word = password()
    cognito.admin_set_user_password(
        UserPoolId=pool, Username=username, Password=word, Permanent=True
    )
    cognito.admin_user_global_sign_out(UserPoolId=pool, Username=username)
    return word


def sign_out(cognito: Any, pool: str, username: str) -> None:
    cognito.admin_user_global_sign_out(UserPoolId=pool, Username=username)


def disable(cognito: Any, pool: str, username: str) -> None:
    cognito.admin_disable_user(UserPoolId=pool, Username=username)


def enable(cognito: Any, pool: str, username: str) -> None:
    cognito.admin_enable_user(UserPoolId=pool, Username=username)


def delete(cognito: Any, pool: str, username: str) -> None:
    cognito.admin_delete_user(UserPoolId=pool, Username=username)


def parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="judges", description=__doc__)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--stack", type=Path, required=True)
    parser.add_argument("--user")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args(argv)
    if args.command != "create" and not args.user:
        parser.error(f"{args.command} needs --user")
    return args


def refresh_note(args: argparse.Namespace, outputs: dict[str, Any], env: str) -> Path:
    users = read_users(credentials_path(args.data_dir, env))
    rendered = note_path(args.data_dir, env)
    site = outputs.get("site", {}).get("url")
    write_note(rendered, site, users)
    return rendered


def run(args: argparse.Namespace, cognito: Any) -> str:
    outputs = stack_outputs(args.stack)
    pool = outputs["user_pool_id"]
    env = environment(args.stack)
    path = credentials_path(args.data_dir, env)
    if args.command == "create":
        snapshot = read_lock(Path("dataset.lock")).snapshot_id
        chosen = personas.read(personas.path_for(args.data_dir, snapshot), snapshot)
        try:
            users = create(
                cognito,
                pool,
                chosen,
                read_users(path),
                lambda username, fields: write_credentials(
                    path, env, {username: fields}
                ),
            )
        finally:
            note = refresh_note(args, outputs, env)
        kept = len(chosen) + len(STAFF) - len(users)
        return f"created {len(users)} users, kept {kept}; credentials in {path}; note in {note}"
    if args.command == "reset":
        write_credentials(
            path, env, {args.user: {"password": reset(cognito, pool, args.user)}}
        )
        note = refresh_note(args, outputs, env)
        return f"reset the user; credentials in {path}; note in {note}"
    if args.command == "delete":
        delete(cognito, pool, args.user)
        drop_credentials(path, args.user)
        refresh_note(args, outputs, env)
        return "delete: done"
    {"sign-out": sign_out, "disable": disable, "enable": enable}[args.command](
        cognito, pool, args.user
    )
    return f"{args.command}: done"


def main(argv: Sequence[str] | None = None) -> None:
    args = parse(argv)
    try:
        print(run(args, boto3.client("cognito-idp", region_name="us-east-1")))
    except (JudgesError, personas.PersonaError, ClientError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()

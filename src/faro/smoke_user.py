"""
The smoke user (ADR-0009): one standing customer per environment, as which the check after each deploy signs in
through the site's form. It is the declines persona's customer, whose cards are known to list, signed in by a
username that names no one, with its customer ID only in its Cognito attribute. One command creates it, or resets
it if it's in the pool already: a new password, the persona's current customer, and a sign-out everywhere. The
username and password go to data/smoke/<env>.json with owner-only permissions; the script prints the path, never the
password or the ID (SEC-03). On prototype, the password goes into the environment's secrets and the username into
its variables.
"""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from faro import judges, personas
from faro.dataset.lock import read_lock

USERNAME = "smoke-check"
PERSONA = "declines"


def credentials_path(data_dir: Path, env: str) -> Path:
    return data_dir / "smoke" / f"{env}.json"


def ready(cognito: Any, pool: str, customer_id: str) -> str:
    """
    Returns the user's new password.
    """
    attributes = [{"Name": "custom:customer_id", "Value": customer_id}]
    try:
        cognito.admin_create_user(
            UserPoolId=pool,
            Username=USERNAME,
            UserAttributes=attributes,
            MessageAction="SUPPRESS",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] != "UsernameExistsException":
            raise
        cognito.admin_update_user_attributes(
            UserPoolId=pool, Username=USERNAME, UserAttributes=attributes
        )
    cognito.admin_add_user_to_group(
        UserPoolId=pool, Username=USERNAME, GroupName="customer"
    )
    return judges.reset(cognito, pool, USERNAME)


def write_credentials(path: Path, env: str, password: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"environment": env, "username": USERNAME, "password": password}
    path.touch(mode=0o600)
    path.chmod(0o600)
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m faro.smoke_user", description=__doc__
    )
    parser.add_argument("--stack", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    args = parser.parse_args(argv)
    try:
        outputs = judges.stack_outputs(args.stack)
        env = judges.environment(args.stack)
        snapshot = read_lock(Path("dataset.lock")).snapshot_id
        chosen = personas.read(personas.path_for(args.data_dir, snapshot), snapshot)
        cognito = boto3.client("cognito-idp", region_name="us-east-1")
        password = ready(cognito, outputs["user_pool_id"], chosen[PERSONA].customer_id)
    except (judges.JudgesError, personas.PersonaError, ClientError) as error:
        raise SystemExit(str(error)) from error
    path = credentials_path(args.data_dir, env)
    write_credentials(path, env, password)
    print(
        f"the smoke user is ready and signed out everywhere; its credentials are in {path}"
    )


if __name__ == "__main__":
    main()

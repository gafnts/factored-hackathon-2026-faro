"""
Command line for make model-key: stores the Anthropic API key from .env in ENV's secret, which AgentCore Identity
reads for the Runtime (ADR-0004). The key goes from the file to Secrets Manager only: never to Terraform, a command
line, or the terminal.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

VARIABLE = "ANTHROPIC_API_KEY"


class ModelKeyError(Exception):
    pass


def secret_id(env: str) -> str:
    return f"banking-agent-{env}-anthropic-api-key"


def read_key(env_file: Path) -> str:
    if not env_file.is_file():
        raise ModelKeyError(
            f"{env_file} not found; copy .env.example to .env and set {VARIABLE}"
        )
    for line in env_file.read_text(encoding="utf-8").splitlines():
        name, _, value = line.strip().removeprefix("export ").partition("=")
        if name.strip() == VARIABLE:
            key = value.split("#", 1)[0].strip().strip("\"'")
            if key:
                return key
    raise ModelKeyError(f"{VARIABLE} is missing or empty in {env_file}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m faro.model_key", description=__doc__
    )
    parser.add_argument("--env", default="local")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args(argv)

    secret = secret_id(args.env)
    try:
        key = read_key(args.env_file)
        client = boto3.client(
            "secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )
        client.put_secret_value(
            SecretId=secret, SecretString=json.dumps({"api_key": key})
        )
    except ModelKeyError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except (BotoCoreError, ClientError) as error:
        print(
            f"AWS error: {error}; check the profile with make doctor", file=sys.stderr
        )
        return 1
    print(f"Stored {VARIABLE} from {args.env_file} in {secret}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

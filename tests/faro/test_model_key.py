"""
The command line behind make model-key (ADR-0004).
"""

import json
from collections.abc import Iterator
from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from mypy_boto3_secretsmanager import SecretsManagerClient

from faro.model_key import ModelKeyError, main, read_key, secret_id

ROOT = Path(__file__).resolve().parents[2]
KEY = "sk-ant-test-0123456789abcdef"


@pytest.fixture
def secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[SecretsManagerClient]:
    """
    Fake credentials, so no test can reach a real profile, and an empty local secret as the IAM root leaves it.
    """
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "aws-credentials"))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    with mock_aws():
        client = boto3.client("secretsmanager", region_name="us-east-1")
        client.create_secret(Name=secret_id("local"))
        yield client


def env_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def test_stores_the_key_as_the_providers_json_without_printing_it(
    secrets: SecretsManagerClient,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = env_file(tmp_path, f"# Anthropic\nOTHER=1\nANTHROPIC_API_KEY={KEY}\n")

    assert main(["--env-file", str(path)]) == 0

    stored = secrets.get_secret_value(SecretId=secret_id("local"))["SecretString"]
    assert json.loads(stored) == {"api_key": KEY}
    printed = capsys.readouterr()
    assert KEY not in printed.out + printed.err


@pytest.mark.parametrize(
    "line",
    [
        f'ANTHROPIC_API_KEY="{KEY}"',
        f"ANTHROPIC_API_KEY='{KEY}'",
        f"export ANTHROPIC_API_KEY={KEY}",
        f"ANTHROPIC_API_KEY={KEY}  # personal",
        f"  ANTHROPIC_API_KEY = {KEY}",
    ],
)
def test_reads_the_usual_dotenv_forms(tmp_path: Path, line: str) -> None:
    assert read_key(env_file(tmp_path, f"{line}\n")) == KEY


def test_the_example_names_the_variable_and_holds_no_key() -> None:
    with pytest.raises(ModelKeyError, match="missing or empty"):
        read_key(ROOT / ".env.example")
    assert "ANTHROPIC_API_KEY=" in (ROOT / ".env.example").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("text", "message"),
    [(None, "copy .env.example"), ("ANTHROPIC_API_KEY=\n", "missing or empty")],
)
def test_stores_nothing_without_a_key(
    secrets: SecretsManagerClient,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    text: str | None,
    message: str,
) -> None:
    path = env_file(tmp_path, text) if text is not None else tmp_path / ".env"

    assert main(["--env-file", str(path)]) == 1

    assert message in capsys.readouterr().err
    versions = secrets.list_secret_version_ids(SecretId=secret_id("local"))
    assert versions["Versions"] == []


def test_reports_a_secret_it_cant_reach_without_printing_the_key(
    secrets: SecretsManagerClient,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = env_file(tmp_path, f"ANTHROPIC_API_KEY={KEY}\n")

    assert main(["--env", "prototype", "--env-file", str(path)]) == 1

    error = capsys.readouterr().err
    assert "make doctor" in error
    assert KEY not in error

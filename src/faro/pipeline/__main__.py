"""
Command line for make pipeline and make export: builds the dbt project in pipeline/ from the pinned snapshot into one
DuckDB file under data/pipeline/, rebuilt whole each time, and exports its gold, stamped, to data/exports/ and the data
bucket (ADR-0006); it also rewrites the bronze contracts. It prints check names and counts, never a row's values
(SEC-03).
"""

import argparse
import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from faro.analysis.source import AnalysisError
from faro.dataset.lock import Lock, LockError, read_lock
from faro.dataset.snapshot import data_bucket_name, snapshot_dir
from faro.export import items
from faro.pipeline import build, checks, contracts, export, runner


def _export(lock: Lock, data_dir: Path, docs: Path, upload: bool) -> None:
    directory, written = export.export(lock, data_dir, docs)
    counts = ", ".join(f"{written['items'][kind]:,} {kind}" for kind in items.KINDS)
    print(f"Built {directory}: {counts}, in {len(written['objects'])} parts")
    if upload:
        region = os.environ.get("AWS_REGION", "us-east-1")
        session = boto3.Session(region_name=region)
        account_id = session.client("sts").get_caller_identity()["Account"]
        items.upload(
            session.client("s3"), data_bucket_name(account_id, region), directory
        )
    print(
        "\nPoint each environment at it in infra/envs/<env>.tfvars:\n\n"
        "tools_data_export = {\n"
        f'  snapshot         = "{written["snapshot"]}"\n'
        f'  pipeline_version = "{written["pipeline_version"]}"\n'
        "}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m faro.pipeline", description=__doc__
    )
    parser.add_argument("--lock", type=Path, default=Path("dataset.lock"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "build",
        help="Build bronze, silver, and gold from the pinned snapshot into data/pipeline/",
    )
    export_parser = commands.add_parser(
        "export",
        help="Export the last build's gold, stamped, into data/exports/, with its manifest under docs/pipeline/",
    )
    export_parser.add_argument("--docs", type=Path, default=Path("docs/pipeline"))
    export_parser.add_argument(
        "--upload",
        action="store_true",
        help="Then upload it to this account's data bucket",
    )
    commands.add_parser(
        "contracts",
        help="Rewrite the bronze contracts from the dictionary and pipeline/contracts/corrections.yml",
    )
    args = parser.parse_args(argv)

    try:
        if args.command == "contracts":
            print(f"Wrote {contracts.write(contracts.build())}/")
            return 0
        lock = read_lock(args.lock)
        if args.command == "export":
            _export(lock, args.data_dir, args.docs, args.upload)
            return 0
        space = runner.workspace(args.data_dir, lock.snapshot_id)
        results = build.build(
            lock, snapshot_dir(args.data_dir, lock.snapshot_id), space
        )
        named = checks.failing_files(space.database, results)
    except (
        AnalysisError,
        LockError,
        contracts.ContractError,
        items.ExportError,
        runner.PipelineError,
    ) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except (BotoCoreError, ClientError) as error:
        print(
            f"AWS error: {error}; check the profile with make doctor", file=sys.stderr
        )
        return 1
    print("\n".join(runner.summarize(results, space.logs, named)))
    if runner.failed(results):
        return 1
    print(f"Built {space.database}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Command line for make personas and make tiny-export. It prints counts, never a customer's ID or values (SEC-03).
"""

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import boto3
import duckdb
from botocore.exceptions import BotoCoreError, ClientError

from faro import personas
from faro.analysis.catalog import CUSTOMERS, PRODUCTS, TRANSACTIONS
from faro.analysis.source import (
    AnalysisError,
    check_local,
    connect,
    partition_date,
    table_keys,
)
from faro.dataset.lock import Lock, LockError, read_lock
from faro.dataset.snapshot import SnapshotError, data_bucket_name, snapshot_dir
from faro.export import items, tiny


def open_snapshot(
    lock: Lock, data_dir: Path, as_of: datetime
) -> duckdb.DuckDBPyConnection:
    root = snapshot_dir(data_dir, lock.snapshot_id)
    check_local(lock, root)
    keys = {t.name: table_keys(lock, t) for t in (CUSTOMERS, PRODUCTS)}
    keys[TRANSACTIONS.name] = [
        key
        for key in table_keys(lock, TRANSACTIONS)
        if tiny.partitions_needed(partition_date(key), as_of)
    ]
    return connect(root, (CUSTOMERS, PRODUCTS, TRANSACTIONS), keys)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m faro.export", description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path("dataset.lock"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument(
        "--profile", type=Path, default=Path("docs/analysis/profiling.json")
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "personas",
        help="Choose the development personas from the pinned snapshot into data/personas/",
    )
    tiny_parser = commands.add_parser(
        "tiny",
        help="Build the personas' tiny export into data/exports/ (ADR-0006's amendment)",
    )
    tiny_parser.add_argument(
        "--upload",
        action="store_true",
        help="Then upload it to this account's data bucket",
    )
    args = parser.parse_args(argv)

    try:
        lock = read_lock(args.lock)
        snapshot = lock.snapshot_id
        business_date, as_of = tiny.clock_from_profile(args.profile, snapshot)
        path = personas.path_for(args.data_dir, snapshot)
        if args.command == "personas":
            with open_snapshot(lock, args.data_dir, as_of) as con:
                chosen = personas.select(con, snapshot, business_date, as_of)
            personas.write(path, chosen)
            for scenario, count in chosen.qualifying.items():
                print(
                    f"{scenario}: chosen among {count} development customers who meet its rule"
                )
            print(f"Wrote {path}")
            return 0

        records = personas.read(path, snapshot)
        stamp = {"snapshot": snapshot, "pipeline_version": tiny.pipeline_version()}
        with open_snapshot(lock, args.data_dir, as_of) as con:
            built = tiny.build(
                con,
                # The journeys' two customers only, so the tiny export stays under the publication rule (ADR-0006).
                [records[scenario].customer_id for scenario in personas.JOURNEYS],
                stamp=stamp,
                business_date=business_date,
                as_of=as_of,
            )
        directory = items.export_dir(args.data_dir, snapshot, stamp["pipeline_version"])
        shutil.rmtree(directory, ignore_errors=True)
        manifest = items.write(
            built,
            directory,
            stamp=stamp,
            clock=tiny.clock(business_date, as_of),
            producer=tiny.PRODUCER,
        )
        counts = ", ".join(f"{manifest['items'][kind]} {kind}" for kind in items.KINDS)
        print(f"Built {directory}: {counts}")
        if args.upload:
            region = os.environ.get("AWS_REGION", "us-east-1")
            session = boto3.Session(region_name=region)
            account_id = session.client("sts").get_caller_identity()["Account"]
            items.upload(
                session.client("s3"), data_bucket_name(account_id, region), directory
            )
        print(
            "\nPoint each environment at it in infra/envs/<env>.tfvars:\n\n"
            "tools_data_export = {\n"
            f'  snapshot         = "{snapshot}"\n'
            f'  pipeline_version = "{stamp["pipeline_version"]}"\n'
            "}"
        )
    except (
        AnalysisError,
        LockError,
        SnapshotError,
        personas.PersonaError,
        tiny.TinyExportError,
        items.ExportError,
    ) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except (BotoCoreError, ClientError) as error:
        print(
            f"AWS error: {error}; check the profile with make doctor", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

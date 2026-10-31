"""
Command line for make data and make snapshot.
"""

import argparse
import os
import sys
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from banking_agent.dataset.lock import LockError
from banking_agent.dataset.snapshot import (
    SnapshotError,
    check,
    data_bucket_name,
    restore,
    snapshot_dir,
    upload,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m banking_agent.dataset", description=__doc__
    )
    parser.add_argument("--lock", type=Path, default=Path("dataset.lock"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--workers", type=int, default=16)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "check", help="Check the local copy of the locked snapshot against the lock"
    )
    restore_parser = commands.add_parser(
        "restore",
        help="Copy the locked snapshot into data/ from a copy of it, checking every file",
    )
    restore_parser.add_argument(
        "source",
        type=Path,
        help="The folder that holds the lock's files, laid out like the organizers' data/ prefix",
    )
    commands.add_parser(
        "upload", help="Copy the locked snapshot into this account's data bucket"
    )
    args = parser.parse_args(argv)

    try:
        if args.command == "upload":
            config = Config(
                max_pool_connections=args.workers, retries={"mode": "standard"}
            )
            region = os.environ.get("AWS_REGION", "us-east-1")
            session = boto3.Session(region_name=region)
            account_id = session.client("sts").get_caller_identity()["Account"]
            bucket = data_bucket_name(account_id, region)
            upload(
                session.client("s3", config=config),
                bucket,
                args.lock,
                args.data_dir,
                workers=args.workers,
            )
        else:
            if args.command == "check":
                lock = check(args.lock, args.data_dir, workers=args.workers)
            else:
                lock = restore(
                    args.source, args.lock, args.data_dir, workers=args.workers
                )
            local = snapshot_dir(args.data_dir, lock.snapshot_id)
            print(
                f"Snapshot {lock.snapshot_id}: {len(lock.files)} files in {local} match the lock"
            )
    except (SnapshotError, LockError) as error:
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

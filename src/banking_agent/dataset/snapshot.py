"""
Restores the pinned snapshot into data/ from a copy, checks it there, and copies it into this account's data bucket.
"""

import base64
import hashlib
import shutil
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

from banking_agent.dataset.lock import Lock, LockedFile, digest_file, read_lock, render

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

ORGANIZER_ACCOUNT_ID = "157725502942"
PROJECT = "banking-agent"

# Written last, so a snapshot in the bucket is complete once it exists.
MARKER = "dataset.lock"

# The organizers' dataset can no longer be downloaded, so a copy is the only way back.
_RESTORE = (
    "restore it from a copy with make data FROM=<folder> (CONTRIBUTING.md, step 2)"
)

Log = Callable[[str], None]


class SnapshotError(Exception):
    pass


def data_bucket_name(account_id: str, region: str) -> str:
    if account_id == ORGANIZER_ACCOUNT_ID:
        raise SnapshotError(
            f"these credentials belong to the organizers' account ({ORGANIZER_ACCOUNT_ID}), "
            "not the account that holds the snapshots"
        )
    return f"{PROJECT}-data-{account_id}-{region}-an"


def snapshot_dir(data_dir: Path, snapshot_id: str) -> Path:
    return data_dir / "snapshots" / snapshot_id


def check(
    lock_path: Path, data_dir: Path, *, workers: int = 16, log: Log = print
) -> Lock:
    lock = _read(lock_path)
    local = snapshot_dir(data_dir, lock.snapshot_id)
    if not local.is_dir():
        raise SnapshotError(
            f"{local} not found; the organizers' dataset can no longer be downloaded, so {_RESTORE}"
        )
    log(f"Checking {len(lock.files)} files in {local}")
    damaged = [
        f.key
        for f, intact in zip(
            lock.files,
            _run(
                "checked", lambda f: _intact(local / f.key, f), lock.files, workers, log
            ),
            strict=True,
        )
        if not intact
    ]
    if damaged:
        raise SnapshotError(
            f"{len(damaged)} of {len(lock.files)} files in {local} are missing or don't match the lock "
            f"({', '.join(damaged[:5])}); {_RESTORE}"
        )
    return lock


def restore(
    source: Path,
    lock_path: Path,
    data_dir: Path,
    *,
    workers: int = 16,
    log: Log = print,
) -> Lock:
    lock = _read(lock_path)
    if not source.is_dir():
        raise SnapshotError(f"{source} is not a folder")
    absent = [f.key for f in lock.files if not _has(source / f.key, f.size)]
    if absent:
        raise SnapshotError(
            f"{source} doesn't hold snapshot {lock.snapshot_id}: {len(absent)} of {len(lock.files)} files are "
            f"missing or have the wrong size ({', '.join(absent[:5])}); nothing was copied"
        )

    local = snapshot_dir(data_dir, lock.snapshot_id)
    pending: list[LockedFile] = list(lock.files)
    if local.is_dir():
        log(f"Checking the files already in {local}")
        in_place = _run(
            "checked", lambda f: _intact(local / f.key, f), lock.files, workers, log
        )
        pending = [f for f, ok in zip(lock.files, in_place, strict=True) if not ok]
    log(f"Copying {len(pending)} of {len(lock.files)} files from {source} into {local}")
    copied = _run(
        "copied",
        lambda f: _copy_checked(source / f.key, local / f.key, f.sha256),
        pending,
        workers,
        log,
    )
    rejected = [f.key for f, ok in zip(pending, copied, strict=True) if not ok]
    if rejected:
        raise SnapshotError(
            f"{len(rejected)} files in {source} don't match the lock and weren't copied "
            f"({', '.join(rejected[:5])})"
        )
    return lock


def upload(
    s3: "S3Client",
    bucket: str,
    lock_path: Path,
    data_dir: Path,
    *,
    workers: int = 16,
    log: Log = print,
) -> None:
    lock = _read(lock_path)
    local = snapshot_dir(data_dir, lock.snapshot_id)
    prefix = f"snapshots/{lock.snapshot_id}/"
    if _exists(s3, bucket, prefix + MARKER):
        log(f"s3://{bucket}/{prefix} is complete; nothing to upload")
        return

    for f in lock.files:
        if not _has(local / f.key, f.size):
            raise SnapshotError(f"{local / f.key} is missing; {_RESTORE}")

    present = {}
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=prefix
    ):
        present.update(
            {
                obj["Key"].removeprefix(prefix): obj["Size"]
                for obj in page.get("Contents", [])
            }
        )
    # Objects already there were checked against their SHA-256 on arrival and can't be overwritten.
    pending = [f for f in lock.files if present.get(f.key) != f.size]
    log(
        f"Uploading {len(pending)} of {len(lock.files)} files to s3://{bucket}/{prefix}"
    )

    def put(f: LockedFile) -> None:
        with (local / f.key).open("rb") as body:
            s3.put_object(
                Bucket=bucket,
                Key=prefix + f.key,
                Body=body,
                IfNoneMatch="*",
                ChecksumSHA256=_b64(f.sha256),
            )

    _run("uploaded", put, pending, workers, log)
    text = render(lock).encode()
    s3.put_object(
        Bucket=bucket,
        Key=prefix + MARKER,
        Body=text,
        IfNoneMatch="*",
        ChecksumSHA256=base64.b64encode(hashlib.sha256(text).digest()).decode(),
    )
    log(f"s3://{bucket}/{prefix} is complete")


def _read(lock_path: Path) -> Lock:
    if not lock_path.exists():
        raise SnapshotError(f"{lock_path} not found")
    return read_lock(lock_path)


def _copy_checked(source: Path, dest: Path, sha256: str) -> bool:
    # A hidden name, so an interrupted copy is never read as part of the snapshot.
    partial = dest.with_name(f".{dest.name}.partial")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, partial)
    if digest_file(partial) != sha256:
        partial.unlink()
        return False
    partial.replace(dest)
    return True


def _run[T, R](
    label: str, fn: Callable[[T], R], items: Sequence[T], workers: int, log: Log
) -> list[R]:
    results = []
    step = max(1, len(items) // 10)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done, result in enumerate(pool.map(fn, items), start=1):
            results.append(result)
            if done % step == 0 or done == len(items):
                log(f"  {label} {done}/{len(items)}")
    return results


def _intact(path: Path, f: LockedFile) -> bool:
    return _has(path, f.size) and digest_file(path) == f.sha256


def _has(path: Path, size: int) -> bool:
    return path.is_file() and path.stat().st_size == size


def _exists(s3: "S3Client", bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
            return False
        raise
    return True


def _b64(hex_digest: str) -> str:
    return base64.b64encode(bytes.fromhex(hex_digest)).decode()

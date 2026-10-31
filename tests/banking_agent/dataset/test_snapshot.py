"""
Restoring the pinned snapshot from a copy, checking it, and copying it into the data bucket (DML-01, DML-04, SEC-03).
"""

from pathlib import Path

import pytest
from mypy_boto3_s3 import S3Client

from banking_agent.dataset.lock import parse, read_lock
from banking_agent.dataset.snapshot import (
    ORGANIZER_ACCOUNT_ID,
    SnapshotError,
    check,
    data_bucket_name,
    restore,
    snapshot_dir,
    upload,
)

TRANSACTIONS = "transactions/year=2023/month=06/day=17/transactions_20230617.csv"


def quiet(_: str) -> None:
    pass


def summary(messages: list[str]) -> str:
    return next(m for m in messages if m.startswith("Copying"))


def files_in(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.fixture
def data(copy: Path, lock_path: Path, tmp_path: Path) -> Path:
    """
    A data/ directory with the snapshot restored into it.
    """
    restore(copy, lock_path, tmp_path / "data", log=quiet)
    return tmp_path / "data"


def test_restore_copies_a_checked_copy(
    copy: Path, lock_path: Path, source_files: dict[str, bytes], tmp_path: Path
) -> None:
    lock = restore(copy, lock_path, tmp_path / "data", log=quiet)

    assert lock == read_lock(lock_path)
    assert files_in(snapshot_dir(tmp_path / "data", lock.snapshot_id)) == source_files


def test_restoring_again_copies_nothing(
    copy: Path, lock_path: Path, data: Path
) -> None:
    messages: list[str] = []

    restore(copy, lock_path, data, log=messages.append)

    assert "Copying 0 of 3 files" in summary(messages)


def test_restore_refuses_a_copy_that_lacks_a_file(
    copy: Path, lock_path: Path, tmp_path: Path
) -> None:
    (copy / "customers.csv").unlink()

    with pytest.raises(SnapshotError, match=r"1 of 3 files .*nothing was copied"):
        restore(copy, lock_path, tmp_path / "data", log=quiet)
    assert not (tmp_path / "data").exists()


def test_a_file_that_differs_from_the_lock_is_not_copied(
    copy: Path, lock_path: Path, source_files: dict[str, bytes], tmp_path: Path
) -> None:
    (copy / "customers.csv").write_bytes(
        source_files["customers.csv"].replace(b"Ana", b"Eva")
    )

    with pytest.raises(SnapshotError, match=r"weren't copied \(customers\.csv\)"):
        restore(copy, lock_path, tmp_path / "data", log=quiet)

    local = snapshot_dir(tmp_path / "data", read_lock(lock_path).snapshot_id)
    assert sorted(files_in(local)) == sorted(set(source_files) - {"customers.csv"})


def test_restore_replaces_a_damaged_local_file(
    copy: Path, lock_path: Path, source_files: dict[str, bytes], data: Path
) -> None:
    path = snapshot_dir(data, read_lock(lock_path).snapshot_id) / "customers.csv"
    path.write_bytes(source_files["customers.csv"].replace(b"Ana", b"Eva"))
    messages: list[str] = []

    restore(copy, lock_path, data, log=messages.append)

    assert "Copying 1 of 3 files" in summary(messages)
    assert path.read_bytes() == source_files["customers.csv"]


def test_restore_copies_only_the_files_the_lock_lists(
    copy: Path, lock_path: Path, source_files: dict[str, bytes], tmp_path: Path
) -> None:
    (copy / "._customers.csv").write_bytes(b"\x00\x05\x16\x07")
    (copy / ".DS_Store").write_bytes(b"\x00")
    (copy / "notes.txt").write_text("not part of the snapshot")

    lock = restore(copy, lock_path, tmp_path / "data", log=quiet)

    assert files_in(snapshot_dir(tmp_path / "data", lock.snapshot_id)) == source_files


def test_check_passes_on_an_intact_copy(lock_path: Path, data: Path) -> None:
    assert check(lock_path, data, log=quiet) == read_lock(lock_path)


def test_check_names_missing_and_damaged_files(
    lock_path: Path, source_files: dict[str, bytes], data: Path
) -> None:
    local = snapshot_dir(data, read_lock(lock_path).snapshot_id)
    (local / "customers.csv").write_bytes(
        source_files["customers.csv"].replace(b"Ana", b"Eva")
    )
    (local / TRANSACTIONS).unlink()

    with pytest.raises(SnapshotError, match=r"2 of 3 files .*FROM=") as caught:
        check(lock_path, data, log=quiet)
    assert "customers.csv" in str(caught.value)
    assert TRANSACTIONS in str(caught.value)


def test_check_says_the_dataset_can_no_longer_be_downloaded(
    lock_path: Path, tmp_path: Path
) -> None:
    with pytest.raises(SnapshotError, match="can no longer be downloaded"):
        check(lock_path, tmp_path / "data", log=quiet)


def test_upload_copies_the_snapshot_and_marks_it_complete(
    lock_path: Path,
    source_files: dict[str, bytes],
    destination: tuple[S3Client, str],
    data: Path,
) -> None:
    lock = read_lock(lock_path)
    s3, bucket = destination

    upload(s3, bucket, lock_path, data, log=quiet)

    prefix = f"snapshots/{lock.snapshot_id}/"
    for key, body in source_files.items():
        assert s3.get_object(Bucket=bucket, Key=prefix + key)["Body"].read() == body
    marker = s3.get_object(Bucket=bucket, Key=f"{prefix}dataset.lock")["Body"]
    assert parse(marker.read().decode()) == lock


def test_uploading_again_transfers_nothing(
    lock_path: Path, destination: tuple[S3Client, str], data: Path
) -> None:
    s3, bucket = destination
    upload(s3, bucket, lock_path, data, log=quiet)
    messages: list[str] = []

    upload(s3, bucket, lock_path, data, log=messages.append)

    assert messages == [messages[0]]
    assert "nothing to upload" in messages[0]


def test_an_interrupted_upload_resumes(
    lock_path: Path,
    source_files: dict[str, bytes],
    destination: tuple[S3Client, str],
    data: Path,
) -> None:
    s3, bucket = destination
    s3.put_object(
        Bucket=bucket,
        Key=f"snapshots/{read_lock(lock_path).snapshot_id}/customers.csv",
        Body=source_files["customers.csv"],
    )
    messages: list[str] = []

    upload(s3, bucket, lock_path, data, log=messages.append)

    assert messages[0].startswith("Uploading 2 of 3 files")


def test_upload_needs_the_local_copy(
    lock_path: Path, destination: tuple[S3Client, str], data: Path
) -> None:
    (snapshot_dir(data, read_lock(lock_path).snapshot_id) / "customers.csv").unlink()
    s3, bucket = destination

    with pytest.raises(SnapshotError, match=r"customers\.csv is missing; restore it"):
        upload(s3, bucket, lock_path, data, log=quiet)


def test_the_organizers_account_never_holds_snapshots() -> None:
    with pytest.raises(SnapshotError, match="organizers' account"):
        data_bucket_name(ORGANIZER_ACCOUNT_ID, "us-east-1")

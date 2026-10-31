"""
The lock pins the snapshot every run reads (DML-01) and names it for lineage (DML-04).
"""

import hashlib
from pathlib import Path

import pytest

from faro.dataset.lock import (
    LockedFile,
    LockError,
    digest_file,
    make_lock,
    parse,
    render,
)


def locked(key: str, content: bytes = b"x", etag: str = "etag") -> LockedFile:
    return LockedFile(key, len(content), etag, hashlib.sha256(content).hexdigest())


def test_snapshot_id_names_the_content_only() -> None:
    lock = make_lock("s3://one/", [locked("b.csv"), locked("a.csv", b"y")])
    elsewhere = make_lock(
        "s3://two/", [locked("a.csv", b"y", etag="other"), locked("b.csv")]
    )
    edited = make_lock("s3://one/", [locked("b.csv"), locked("a.csv", b"z")])

    assert lock.snapshot_id == elsewhere.snapshot_id
    assert lock.snapshot_id != edited.snapshot_id


def test_the_lock_round_trips() -> None:
    lock = make_lock(
        "s3://bucket/data/", [locked("customers.csv"), locked("t/day=1/t.csv", b"y")]
    )

    assert parse(render(lock)) == lock


def test_a_hand_edited_lock_is_rejected() -> None:
    text = render(make_lock("s3://bucket/data/", [locked("customers.csv")]))
    edited = text.replace(
        hashlib.sha256(b"x").hexdigest(), hashlib.sha256(b"y").hexdigest()
    )

    with pytest.raises(LockError, match="edited by hand"):
        parse(edited)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("# source: s3://bucket/data/\ncustomers.csv\t1\n", "line 2"),
        ("customers.csv\t1\tetag\tsha\n", "no '# source:' line"),
    ],
)
def test_a_malformed_lock_is_rejected(text: str, message: str) -> None:
    with pytest.raises(LockError, match=message):
        parse(text)


def test_duplicate_keys_are_rejected() -> None:
    with pytest.raises(LockError, match="duplicate"):
        make_lock("s3://bucket/data/", [locked("a.csv"), locked("a.csv", b"y")])


def test_a_file_is_recognized_by_its_sha256(tmp_path: Path) -> None:
    path = tmp_path / "file.csv"
    path.write_bytes(bytes(range(25)))

    assert digest_file(path) == hashlib.sha256(bytes(range(25))).hexdigest()

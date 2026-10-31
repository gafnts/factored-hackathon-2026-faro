"""
The lock file: which files make up the pinned snapshot, and how to recognize each one.
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

_HEADER = "# Pinned snapshot of the organizers' dataset (docs/adr/0002). Written by make data; do not edit."


class LockError(Exception):
    pass


@dataclass(frozen=True)
class LockedFile:
    key: str
    size: int
    etag: str
    sha256: str


@dataclass(frozen=True)
class Lock:
    source: str
    files: tuple[LockedFile, ...]

    @property
    def snapshot_id(self) -> str:
        # Content only: the same bytes from another source or date keep their ID.
        digest = hashlib.sha256()
        for f in self.files:
            digest.update(f"{f.key}\t{f.size}\t{f.sha256}\n".encode())
        return digest.hexdigest()[:16]


def make_lock(source: str, files: Iterable[LockedFile]) -> Lock:
    ordered = tuple(sorted(files, key=lambda f: f.key))
    keys = [f.key for f in ordered]
    if len(set(keys)) != len(keys):
        raise LockError("duplicate keys in the snapshot")
    return Lock(source, ordered)


def render(lock: Lock) -> str:
    lines = [
        _HEADER,
        f"# source: {lock.source}",
        f"# snapshot: {lock.snapshot_id}",
        "# key\tsize\tetag\tsha256",
        *(f"{f.key}\t{f.size}\t{f.etag}\t{f.sha256}" for f in lock.files),
    ]
    return "\n".join(lines) + "\n"


def parse(text: str) -> Lock:
    meta: dict[str, str] = {}
    files = []
    for number, line in enumerate(text.splitlines(), start=1):
        if line.startswith("#"):
            for name in ("source", "snapshot"):
                if line.startswith(f"# {name}: "):
                    meta[name] = line.removeprefix(f"# {name}: ")
            continue
        try:
            key, size, etag, sha256 = line.split("\t")
            files.append(LockedFile(key, int(size), etag, sha256))
        except ValueError as error:
            raise LockError(f"line {number} is not key, size, etag, sha256") from error
    if "source" not in meta:
        raise LockError("no '# source:' line")
    lock = make_lock(meta["source"], files)
    if meta.get("snapshot") != lock.snapshot_id:
        raise LockError(
            "the '# snapshot:' line doesn't match the files; was the lock edited by hand?"
        )
    return lock


def read_lock(path: Path) -> Lock:
    try:
        return parse(path.read_text())
    except LockError as error:
        raise LockError(f"{path}: {error}") from error


def write_lock(path: Path, lock: Lock) -> None:
    path.write_text(render(lock))


def digest_file(path: Path) -> str:
    with path.open("rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()

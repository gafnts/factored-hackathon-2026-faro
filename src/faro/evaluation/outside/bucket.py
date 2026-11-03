"""
The outside source's second copy (ADR-0011, The seal, as amended): in the account's data bucket, which no stack owns
and no lifecycle rule empties, under sources/outside/<hash>/, the hash a set's manifest records. Each file is written
once, its SHA-256 checked on arrival, and MARKER last, so a copy is complete once it exists; a changed source is a new
hash and a new copy. Nothing is printed but keys and counts.
"""

import base64
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

from faro.evaluation import families

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

PREFIX = f"sources/{families.OUTSIDE}"
# Written last, listing each file's SHA-256.
MARKER = "SHA256SUMS"


class BucketError(ValueError):
    pass


def keep(s3: "S3Client", bucket: str, root: Path = families.SOURCES) -> str:
    """
    Copies the source into the bucket unless its copy is complete; returns the copy's prefix.
    """
    files = sorted((root / families.OUTSIDE).glob("*.json"))
    if not files:
        raise BucketError(f"no outside source under {root / families.OUTSIDE}")
    prefix = f"{PREFIX}/{families.source_hash(families.OUTSIDE, root)}/"
    if _exists(s3, bucket, prefix + MARKER):
        return prefix
    sums = "".join(
        f"{_put(s3, bucket, prefix + path.name, path.read_bytes())}  {path.name}\n"
        for path in files
    )
    _put(s3, bucket, prefix + MARKER, sums.encode())
    return prefix


def _put(s3: "S3Client", bucket: str, key: str, body: bytes) -> str:
    digest = hashlib.sha256(body)
    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            IfNoneMatch="*",
            ChecksumSHA256=base64.b64encode(digest.digest()).decode(),
        )
    except ClientError as error:
        # Left by a copy that stopped part way: the key holds the source's hash, so the object is this file.
        if error.response.get("Error", {}).get("Code") != "PreconditionFailed":
            raise
    return digest.hexdigest()


def _exists(s3: "S3Client", bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
            return False
        raise
    return True

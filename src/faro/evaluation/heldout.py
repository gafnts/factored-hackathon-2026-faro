"""
The held-out set's guards (ADR-0005, The split): it is drawn once, its manifest is committed before the first run that
reports it, and no case of it is edited after a run. Once any run names the set, whether reported under
docs/evaluation/runs/ or kept under data/evaluation/runs/, a redraw only rebuilds it: its cases are kept when they are
the ones the committed manifest describes, and the manifest is never rewritten, so a clone can rebuild the set from
the snapshot (OPS-07) and no one can change it under its name. A play is refused unless the set is the one its
committed manifest describes, so an edited case can't play under the set's name. A changed held-out set is a new
version, drawn under a new name, with earlier results reported against the old one.
"""

import hashlib
import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from faro.evaluation import generator

NAME = "held_out"
REPORTED = Path("docs/evaluation/runs")
KEPT = Path("data/evaluation/runs")


class HeldOutError(ValueError):
    pass


def runs_against(name: str, reported: Path = REPORTED, kept: Path = KEPT) -> list[str]:
    """
    The runs whose manifests name the set, reported or kept.
    """
    found = []
    for path in sorted(reported.glob("*.json")) if reported.is_dir() else []:
        manifest = json.loads(path.read_text(encoding="utf-8"))["manifest"]
        if manifest["set"]["name"] == name:
            found.append(manifest["run"])
    for path in sorted(kept.glob("*/manifest.json")) if kept.is_dir() else []:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest["set"]["name"] == name:
            found.append(manifest["run"])
    return sorted(set(found))


def reproduce(
    played: Sequence[str],
    found: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    is_committed: bool,
) -> None:
    """
    A redraw once runs name the set keeps its cases only when they are the committed manifest's.
    """
    if is_committed and _hash(found) == manifest["sha256"]:
        return
    raise HeldOutError(
        f"the {NAME} set has {len(played)} run(s) against it, the first {played[0]}, and this draw isn't the one its "
        "committed manifest describes; a redraw only rebuilds it, at its size and seed, and a changed held-out set is a "
        "new version under a new name (ADR-0005, The split)"
    )


def committed(path: Path) -> bool:
    """
    Whether git tracks the file and it holds no change since the last commit.
    """
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", str(path)], capture_output=True
    )
    changed = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", str(path)], capture_output=True
    )
    return tracked.returncode == 0 and changed.returncode == 0


def verify(
    found: Sequence[Mapping[str, Any]], manifest: Mapping[str, Any], is_committed: bool
) -> None:
    """
    A held-out set plays only as its committed manifest describes it.
    """
    if not is_committed:
        raise HeldOutError(
            "the held-out set's manifest isn't committed; it is committed before the first run (ADR-0005, The split)"
        )
    if _hash(found) != manifest["sha256"]:
        raise HeldOutError(
            "the held-out set isn't the one its committed manifest describes; no held-out case is edited after "
            "it is drawn, and a changed set is a new version (ADR-0005, The split)"
        )


def _hash(found: Sequence[Mapping[str, Any]]) -> str:
    return hashlib.sha256(
        "".join(generator.digest(c) for c in found).encode()
    ).hexdigest()

"""
Dependabot's uv groups cover what pyproject.toml declares, so each of its PRs is taken one way (CONTRIBUTING.md; OPS-07).
"""

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

from faro.build import ARTIFACTS

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())
CONFIG = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text())
[UV] = [update for update in CONFIG["updates"] if update["package-ecosystem"] == "uv"]
GROUPS: dict[str, Any] = UV["groups"]
RUNTIME: list[str] = GROUPS["python-runtime"]["patterns"]
NAME = re.compile(r"[A-Za-z0-9._-]+(\[[^\]]+\])?")


def names(requirements: list[str]) -> set[str]:
    return {match.group(0).lower() for match in map(NAME.match, requirements) if match}


def matches(pattern: str, name: str) -> bool:
    """Dependabot's wildcard matcher: `*` is any run of characters, and everything else is literal."""
    parts = map(re.escape, pattern.lower().split("*"))
    return re.fullmatch(".*".join(parts), name) is not None


def runtime(name: str) -> bool:
    return any(matches(pattern, name) for pattern in RUNTIME)


def test_every_dependency_a_zip_ships_updates_with_the_runtime_group() -> None:
    groups = PYPROJECT["dependency-groups"]
    shipped = set().union(*(names(groups[a.group]) for a in ARTIFACTS if a.group))
    assert {name for name in shipped if not runtime(name)} == set()


def test_the_unversioned_group_names_what_dependabot_reads_no_version_for() -> None:
    declared = names(PYPROJECT["project"]["dependencies"])
    declared |= set().union(*map(names, PYPROJECT["dependency-groups"].values()))
    extras = {name for name in declared if "[" in name}
    backend = names(PYPROJECT["build-system"]["requires"])
    assert set(GROUPS["python-unversioned"]["patterns"]) == extras | backend

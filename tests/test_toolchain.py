"""
mise installs the pnpm that packageManager pins, so the pnpm that runs is the one mise.lock checked (ADR-0008; OPS-07).
"""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mise_installs_the_pnpm_package_manager_pins() -> None:
    tools = tomllib.loads((ROOT / "mise.toml").read_text())["tools"]
    package = json.loads((ROOT / "web" / "package.json").read_text())
    assert package["packageManager"] == f"pnpm@{tools['pnpm']}"

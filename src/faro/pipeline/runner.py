"""
Runs dbt on the project in pipeline/ as a subprocess, so this package never imports dbt. dbt's own output goes to its log
under the build's directory, since an error from DuckDB's reader can quote a row; what gets printed is a summary of
run_results.json, which names each check and counts its rows (SEC-03).
"""

import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[3] / "pipeline"
PASSED = ("success", "pass")
_CSV_LINE = re.compile(r"CSV Error on Line: (\d+)")
_CSV_FILE = re.compile(r"^\s*file = (.+)$", re.M)
_CONVERSION = re.compile(
    r'converting column "([^"]+)"\. Could not convert string .* to \'([^\']+)\''
)
_WIDTH = re.compile(r"Expected Number of Columns: (\d+) Found: (\d+)")

Result = Mapping[str, Any]


class PipelineError(Exception):
    pass


@dataclass(frozen=True)
class Workspace:
    database: Path
    # dbt's compiled SQL, artifacts, and log for this build.
    work: Path

    @property
    def target(self) -> Path:
        return self.work / "target"

    @property
    def logs(self) -> Path:
        return self.work / "logs"

    def reset(self) -> None:
        self.database.unlink(missing_ok=True)
        self.database.with_name(self.database.name + ".wal").unlink(missing_ok=True)
        shutil.rmtree(self.work, ignore_errors=True)
        self.database.parent.mkdir(parents=True, exist_ok=True)


def workspace(data_dir: Path, snapshot: str) -> Workspace:
    root = data_dir / "pipeline"
    return Workspace(root / f"{snapshot}.duckdb", root / snapshot)


def dbt(
    command: Sequence[str],
    space: Workspace,
    variables: Mapping[str, Any] | None = None,
) -> list[Result]:
    """
    Returns run_results.json's results; raises when dbt stops before writing them, which only a project that doesn't
    parse or compile does.
    """
    env = {
        **os.environ,
        "PIPELINE_DATABASE": str(space.database),
        "DBT_PROJECT_DIR": str(PROJECT),
        "DBT_PROFILES_DIR": str(PROJECT),
        "DBT_TARGET_PATH": str(space.target),
        "DBT_LOG_PATH": str(space.logs),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
        "DO_NOT_TRACK": "1",
    }
    args = [str(Path(sys.executable).with_name("dbt")), *command]
    if variables:
        args += ["--vars", json.dumps(dict(variables), sort_keys=True)]
    results = space.target / "run_results.json"
    results.unlink(missing_ok=True)
    done = subprocess.run(args, env=env, capture_output=True, text=True, check=False)
    if not results.is_file():
        if done.returncode == 0:
            return []
        tail = "\n  ".join(done.stdout.strip().splitlines()[-15:])
        raise PipelineError(f"dbt {command[0]} stopped:\n  {tail}")
    loaded: list[Result] = json.loads(results.read_text())["results"]
    return loaded


def last_results(space: Workspace) -> list[Result]:
    path = space.target / "run_results.json"
    if not path.is_file():
        return []
    loaded: list[Result] = json.loads(path.read_text())["results"]
    return loaded


def node_name(result: Result) -> str:
    # A test's ID ends in a hash its name doesn't need.
    return str(result["unique_id"]).split(".")[2]


def failed(results: Sequence[Result]) -> list[Result]:
    return [r for r in results if r["status"] not in (*PASSED, "warn")]


def reason(message: str | None) -> str | None:
    """
    Why DuckDB couldn't read a file, by its line, column, and type: its own message quotes the line and the value.
    """
    line = _CSV_LINE.search(message or "")
    path = _CSV_FILE.search(message or "")
    if message is None or not line or not path:
        return None
    where = f"line {line[1]} of {path[1].strip()}"
    if conversion := _CONVERSION.search(message):
        return f"{where}: a value in {conversion[1]} isn't a {conversion[2]}"
    if width := _WIDTH.search(message):
        return f"{where}: {width[2]} columns where the contract has {width[1]}"
    return f"{where} doesn't read as its contract says"


def summarize(
    results: Sequence[Result],
    log: Path,
    named: Mapping[str, Sequence[str]] | None = None,
) -> list[str]:
    statuses = Counter(str(r["status"]) for r in results)
    lines = [", ".join(f"{n} {status}" for status, n in sorted(statuses.items()))]
    for r in sorted(results, key=node_name):
        if r["status"] in PASSED:
            continue
        rows = f" ({r['failures']:,} rows)" if r.get("failures") else ""
        lines.append(f"  {r['status']}: {node_name(r)}{rows}")
        if r["status"] == "error" and (why := reason(r.get("message"))):
            lines.append(f"    {why}")
        lines += [f"    {key}" for key in (named or {}).get(str(r["unique_id"]), ())]
    if failed(results):
        lines.append(f"dbt's messages are in {log}")
    return lines

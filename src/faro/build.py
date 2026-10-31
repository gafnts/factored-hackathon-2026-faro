"""
Builds what Terraform deploys into build/ (ADR-0004, Deployment): a zip each for the Runtime and the Lambdas, with the
Linux arm64 wheels its dependency group locks in uv.lock and its share of this package. Every zip is the same bytes on
every machine and from any checkout, so a plan shows a change only when the code or a locked version changed. The
Runtime's entry script names the commit that last changed its share, which the execution record stamps on every turn.
It also rewrites the Gateway's tool definitions, which are committed so that CI can lint the stack without a build.
"""

import csv
import json
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Callable, Container, Sequence
from dataclasses import dataclass
from pathlib import Path

from faro.contracts.gateway import tool_definitions

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
BUILD = ROOT / "build"
GATEWAY_TOOLS = ROOT / "infra" / "modules" / "gateway" / "tools.json"
PLATFORM = (
    "--python-platform",
    "aarch64-manylinux2014",
    "--python-version",
    "3.13",
    "--only-binary",
    ":all:",
)
EPOCH = (2026, 1, 1, 0, 0, 0)
# What uv's install adds beside the distributions: console scripts, whose first line names the building machine's
# interpreter and which nothing runs, and its lock on the target.
INSTALLED = ("bin", ".lock")

Run = Callable[[Sequence[str]], None]
Git = Callable[[Sequence[str]], str]


@dataclass(frozen=True)
class Artifact:
    name: str
    code: tuple[str, ...]
    group: str | None = None
    entry: str | None = None


# Each zip's share of this package, under src/faro: the packages its entry points import, with the files they read.
RUNTIME = Artifact(
    "runtime",
    ("agent", "contracts", "masking.py", "policy", "tools"),
    group="agent",
    entry="from faro.agent.app import main\n\nmain({app_version!r})\n",
)
ARTIFACTS = (
    RUNTIME,
    Artifact("pre_token", ("identity",)),
    Artifact("tools", ("console", "contracts", "policy", "tools"), group="tools"),
)
# Only the tests read the contracts' examples.
UNSHIPPED = ("contracts/examples",)


def run(command: Sequence[str]) -> None:
    subprocess.run(command, check=True, cwd=ROOT)


def git(args: Sequence[str]) -> str:
    done = subprocess.run(
        ["git", *args], check=True, cwd=ROOT, capture_output=True, text=True
    )
    return done.stdout.strip()


def pathspecs(artifact: Artifact) -> list[str]:
    shared = [f"src/faro/{path}" for path in ("__init__.py", *artifact.code)]
    return shared + [f":(exclude)src/faro/{path}" for path in UNSHIPPED]


def app_version(git: Git = git) -> str:
    """
    The last commit that changed the Runtime's share, so a change to its locked dependencies alone keeps the stamp.
    It needs the history: in a shallow clone, the last commit that changed the share is always the newest.
    """
    shared = pathspecs(RUNTIME)
    commit = git(["log", "-1", "--format=%H", "--", *shared])
    if git(["status", "--porcelain", "--", *shared]):
        print(
            "warning: the Runtime's code has uncommitted changes, and it's stamped "
            f"with {commit[:12]}, the last commit that changed it",
            file=sys.stderr,
        )
    return commit


def copy_code(artifact: Artifact, package: Path, git: Git = git) -> None:
    """
    The files git sees in the artifact's share, tracked or new, so ignored ones such as bytecode or .DS_Store stay out.
    """
    listed = git(
        ["ls-files", "--cached", "--others", "--exclude-standard", "-z", "--"]
        + pathspecs(artifact)
    )
    for name in listed.split("\0"):
        source = ROOT / name
        # A tracked file deleted in the working tree is still listed.
        if source.is_file():
            target = package / source.relative_to(SRC)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def packaged(path: Path, root: Path) -> bool:
    parts = path.relative_to(root).parts
    return path.is_file() and parts[0] not in INSTALLED and "__pycache__" not in parts


def recorded(record: bytes, names: Container[str]) -> bytes:
    """
    A RECORD lists every file its install wrote; the zip's lists only the files the zip holds.
    """
    lines = record.decode().splitlines(keepends=True)
    kept = (
        line
        for line, row in zip(lines, csv.reader(lines), strict=True)
        if row and row[0] in names
    )
    return "".join(kept).encode()


def write_zip(source: Path, target: Path) -> None:
    files = {
        path.relative_to(source).as_posix(): path
        for path in sorted(source.rglob("*"))
        if packaged(path, source)
    }
    target.unlink(missing_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, path in files.items():
            data = path.read_bytes()
            if name.endswith(".dist-info/RECORD"):
                data = recorded(data, files.keys())
            info = zipfile.ZipInfo(name, date_time=EPOCH)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)


def build_artifact(
    artifact: Artifact, out: Path, version: str, run: Run = run, git: Git = git
) -> Path:
    work = out / artifact.name
    package = work / "package"
    shutil.rmtree(work, ignore_errors=True)
    package.mkdir(parents=True)
    if artifact.group is not None:
        requirements = work / "requirements.txt"
        run(
            [
                "uv",
                "export",
                "--frozen",
                "--only-group",
                artifact.group,
                "--no-emit-project",
                "--no-header",
                "--no-annotate",
                "--quiet",
                "--output-file",
                str(requirements),
            ]
        )
        run(
            [
                "uv",
                "pip",
                "install",
                "--quiet",
                *PLATFORM,
                "--target",
                str(package),
                "-r",
                str(requirements),
            ]
        )
    copy_code(artifact, package, git)
    if artifact.entry is not None:
        (package / "main.py").write_text(
            artifact.entry.format(app_version=version), encoding="utf-8"
        )
    target = out / f"{artifact.name}.zip"
    write_zip(package, target)
    return target


def gateway_tools() -> str:
    return json.dumps(tool_definitions(), indent=2) + "\n"


def main(
    out: Path = BUILD,
    tools: Path = GATEWAY_TOOLS,
    run: Run = run,
    version: str | None = None,
) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    version = version or app_version()
    built = [build_artifact(a, out, version, run) for a in ARTIFACTS]
    for path in built:
        print(f"{path.relative_to(out.parent)} {path.stat().st_size / 1e6:.1f} MB")
    tools.write_text(gateway_tools(), encoding="utf-8")
    return built


if __name__ == "__main__":
    main()

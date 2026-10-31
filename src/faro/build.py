"""
Builds what Terraform deploys into build/ (ADR-0004, Deployment): a zip each for the Runtime and the Lambdas, with this
package and the Linux arm64 wheels its dependency group locks in uv.lock. Every zip is the same bytes on every machine,
so a plan shows a change only when the code or a locked version changed. The Runtime's entry script names the commit
that last changed the packaged code, which the execution record stamps on every turn. It also rewrites the Gateway's
tool definitions, which are committed so that CI can lint the stack without a build.
"""

import json
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from faro.contracts.gateway import tool_definitions

ROOT = Path(__file__).resolve().parents[2]
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
# What the zips package: a commit that changes nothing here deploys the same zips. The evaluation never runs in the
# stack, so neither the zips nor the version carry it (ADR-0004's amendment of 2026-10-01).
EVALUATION = ("faro", "evaluation")
PACKAGED = (
    "src",
    "pyproject.toml",
    "uv.lock",
    ":(exclude)src/faro/evaluation",
)

Run = Callable[[Sequence[str]], None]


@dataclass(frozen=True)
class Artifact:
    name: str
    group: str | None = None
    entry: str | None = None


ARTIFACTS = (
    Artifact(
        "runtime",
        group="agent",
        entry="from faro.agent.app import main\n\nmain({app_version!r})\n",
    ),
    Artifact("pre_token"),
    Artifact("tools", group="tools"),
)


def run(command: Sequence[str]) -> None:
    subprocess.run(command, check=True, cwd=ROOT)


def git(args: Sequence[str]) -> str:
    done = subprocess.run(
        ["git", *args], check=True, cwd=ROOT, capture_output=True, text=True
    )
    return done.stdout.strip()


def app_version(git: Callable[[Sequence[str]], str] = git) -> str:
    """
    Needs the history: in a shallow clone, the last commit that changed the packaged code is always the newest.
    """
    commit = git(["log", "-1", "--format=%H", "--", *PACKAGED])
    if git(["status", "--porcelain", "--", *PACKAGED]):
        print(
            "warning: the packaged code has uncommitted changes, and the Runtime is "
            f"stamped with {commit[:12]}, the last commit that changed it",
            file=sys.stderr,
        )
    return commit


def packaged(path: Path, root: Path) -> bool:
    """
    Console scripts carry the building machine's interpreter in their shebang, and nothing runs them.
    """
    parts = path.relative_to(root).parts
    return (
        path.is_file()
        and parts[0] != "bin"
        and "__pycache__" not in parts
        and parts[:2] != EVALUATION
    )


def write_zip(source: Path, target: Path) -> None:
    target.unlink(missing_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(p for p in source.rglob("*") if packaged(p, source)):
            info = zipfile.ZipInfo(path.relative_to(source).as_posix(), date_time=EPOCH)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())


def build_wheel(out: Path, run: Run = run) -> Path:
    run(["uv", "build", "--quiet", "--wheel", "--clear", "--out-dir", str(out)])
    return next(out.glob("faro-*.whl"))


def build_artifact(
    artifact: Artifact, wheel: Path, out: Path, version: str, run: Run = run
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
    # Unpacked rather than installed: an install records the wheel's local path in direct_url.json.
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(package)
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
    wheel = build_wheel(out / "dist", run)
    built = [build_artifact(a, wheel, out, version, run) for a in ARTIFACTS]
    for path in built:
        print(f"{path.relative_to(out.parent)} {path.stat().st_size / 1e6:.1f} MB")
    tools.write_text(gateway_tools(), encoding="utf-8")
    return built


if __name__ == "__main__":
    main()

"""
Cuts and names the releases CHANGELOG.md lists (ADR-0009). A cut, a promotion's first step on develop, sets
pyproject.toml's version with uv and moves Unreleased's entries under the version's heading, leaving Unreleased empty.
check refuses a promotion whose release isn't cut. release tags main's head and makes its GitHub release once that
commit's deploy run has passed, its check included; the notes are the version's section and the build's SHA256SUMS.
"""

import argparse
import datetime
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Container, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = ROOT / "CHANGELOG.md"
PYPROJECT = ROOT / "pyproject.toml"
WORKFLOW = "deploy-prototype.yml"
CHECK_JOB = "Check"
# The job passes with this step skipped when the environment has no smoke user.
CHECK_STEP = "Check the deploy as a customer"
SUMS_ARTIFACT = "sums"

UNRELEASED = "## Unreleased\n"
HEADING = re.compile(
    r"^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})(?: \(Promotion (\d+)\))?$",
    re.MULTILINE,
)
LINK = re.compile(r"^\[(\d+\.\d+\.\d+)\]: (\S+)$", re.MULTILINE)
# A section ends at the next heading of its level or at the link references below the last one.
NEXT = re.compile(r"^(## |\[[^\]]+\]: )", re.MULTILINE)
DEFINITION = re.compile(r"^\[([^\]]+)\]: (\S+)$", re.MULTILINE)
# A reference-style link, as [#129], and not an inline link or a definition.
REFERENCE = re.compile(r"\[([^\]]+)\](?![(\[:])")
RELATIVE = re.compile(r"\]\((?!https?://|#|mailto:)([^)\s]+)\)")

Run = Callable[[Sequence[str]], str]


class ReleaseError(Exception):
    pass


@dataclass(frozen=True)
class Release:
    version: str
    date: str
    promotion: int | None


def run(command: Sequence[str]) -> str:
    done = subprocess.run(command, check=True, cwd=ROOT, capture_output=True, text=True)
    return done.stdout.strip()


def releases(changelog: str) -> list[Release]:
    return [
        Release(m[1], m[2], int(m[3]) if m[3] else None)
        for m in HEADING.finditer(changelog)
    ]


def top(changelog: str) -> Release:
    found = releases(changelog)
    if not found:
        raise ReleaseError("the changelog has no version heading")
    return found[0]


def body(changelog: str, start: int) -> str:
    following = NEXT.search(changelog, start)
    return changelog[start : following.start() if following else None].strip()


def unreleased(changelog: str) -> str:
    if UNRELEASED not in changelog:
        raise ReleaseError("the changelog has no Unreleased heading")
    return body(changelog, changelog.index(UNRELEASED) + len(UNRELEASED))


def notes(changelog: str, version: str) -> str:
    for heading in HEADING.finditer(changelog):
        if heading[1] == version:
            return body(changelog, heading.end())
    raise ReleaseError(f"the changelog has no section for {version}")


def linked(changelog: str, version: str) -> str:
    """
    The version's section as a release page shows it: with the link definitions it uses, which sit below the last
    section, and with its paths pointing at the files at its tag.
    """
    section = notes(changelog, version)
    link = LINK.search(changelog)
    if link is None:
        raise ReleaseError("the changelog has no version link to follow")
    repository = link[2].split("/releases/")[0]
    section = RELATIVE.sub(
        lambda path: f"]({repository}/blob/v{version}/{path[1]})", section
    )
    defined = dict(DEFINITION.findall(changelog))
    used = dict.fromkeys(
        label for label in REFERENCE.findall(section) if label in defined
    )
    definitions = "".join(f"\n[{label}]: {defined[label]}" for label in used)
    return f"{section}\n{definitions}" if definitions else section


def project_version(pyproject: str) -> str:
    version: str = tomllib.loads(pyproject)["project"]["version"]
    return version


def bumped(version: str, bump: str) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    return (
        f"{major}.{minor + 1}.0" if bump == "minor" else f"{major}.{minor}.{patch + 1}"
    )


def cut(changelog: str, version: str, today: datetime.date) -> str:
    """
    Unreleased's entries under the new version's heading, numbered as the next promotion, with an empty Unreleased
    above it and the heading's link beside the others.
    """
    promotion = (top(changelog).promotion or 0) + 1
    heading = f"## [{version}] - {today.isoformat()} (Promotion {promotion})\n"
    changelog = changelog.replace(UNRELEASED, f"{UNRELEASED}\n{heading}", 1)
    link = LINK.search(changelog)
    if link is None:
        raise ReleaseError("the changelog has no version link to follow")
    url = f"{link[2].rsplit('/', 1)[0]}/v{version}"
    return f"{changelog[: link.start()]}[{version}]: {url}\n{changelog[link.start() :]}"


def problems(changelog: str, version: str, tagged: Container[str]) -> list[str]:
    """
    What keeps a promotion from being a release: entries not cut, a changelog and a pyproject.toml that disagree, or a
    version already released.
    """
    latest = top(changelog).version
    found = []
    if unreleased(changelog):
        found.append("Unreleased isn't empty, so the release isn't cut: run make cut")
    if latest != version:
        found.append(
            f"the changelog's top version is {latest} and pyproject.toml's is {version}"
        )
    if f"v{latest}" in tagged:
        found.append(
            f"v{latest} is already tagged, so this release isn't cut: run make cut"
        )
    return found


def released(version: str, run: Run = run) -> bool:
    try:
        run(["gh", "release", "view", f"v{version}", "--json", "tagName"])
    except subprocess.CalledProcessError:
        return False
    return True


def checked_run(commit: str, run: Run = run) -> str:
    """
    The newest deploy run of the commit, which must have passed with its check run, not skipped.
    """
    runs = json.loads(
        run(
            ["gh", "run", "list", "--workflow", WORKFLOW, "--commit", commit]
            + ["--json", "databaseId,event,status,conclusion", "--limit", "20"]
        )
    )
    deploys = [r for r in runs if r["event"] != "pull_request"]
    if not deploys:
        raise ReleaseError(f"no deploy run for {commit[:12]}")
    newest = deploys[0]
    if newest["status"] != "completed" or newest["conclusion"] != "success":
        raise ReleaseError(
            f"the deploy run for {commit[:12]} is {newest['status']}, {newest['conclusion'] or 'not done'}"
        )
    identifier = str(newest["databaseId"])
    jobs = json.loads(run(["gh", "run", "view", identifier, "--json", "jobs"]))["jobs"]
    steps = [s for j in jobs if j["name"] == CHECK_JOB for s in j.get("steps", [])]
    if not any(s["name"] == CHECK_STEP and s["conclusion"] == "success" for s in steps):
        raise ReleaseError(f"the deploy run for {commit[:12]} passed without its check")
    return identifier


def release_notes(section: str, sums: str) -> str:
    return (
        f"{section}\n\n### Build\n\n"
        "The SHA-256 of the zips this release deployed, to compare with `make build` at its tag:\n\n"
        f"```\n{sums.strip()}\n```\n"
    )


def cut_command(
    bump: str,
    changelog_path: Path = CHANGELOG,
    pyproject_path: Path = PYPROJECT,
    today: datetime.date | None = None,
    run: Run = run,
) -> str:
    changelog = changelog_path.read_text(encoding="utf-8")
    version = project_version(pyproject_path.read_text(encoding="utf-8"))
    latest = top(changelog).version
    if latest != version:
        raise ReleaseError(
            f"the changelog's top version is {latest} and pyproject.toml's is {version}"
        )
    if not unreleased(changelog):
        raise ReleaseError("Unreleased is empty: there is nothing to cut")
    if not released(latest, run):
        print(
            f"warning: {latest} has no release, so its deploy never passed its check or "
            "make release never ran; this cut follows it anyway",
            file=sys.stderr,
        )
    new = bumped(version, bump)
    run(["uv", "version", new])
    changelog_path.write_text(
        cut(changelog, new, today or datetime.date.today()), encoding="utf-8"
    )
    return f"cut {new}: pyproject.toml, uv.lock and CHANGELOG.md changed; commit them on a branch into develop"


def check_command(
    changelog_path: Path = CHANGELOG, pyproject_path: Path = PYPROJECT, run: Run = run
) -> str:
    changelog = changelog_path.read_text(encoding="utf-8")
    version = project_version(pyproject_path.read_text(encoding="utf-8"))
    tagged = run(["git", "tag", "--list", "v*"]).split()
    if found := problems(changelog, version, tagged):
        raise ReleaseError("; ".join(found))
    return f"{version} is cut and not yet released"


def release_command(dry_run: bool, run: Run = run) -> str:
    run(["git", "fetch", "--quiet", "--tags", "origin", "main"])
    head = run(["git", "rev-parse", "origin/main"])
    changelog = run(["git", "show", "origin/main:CHANGELOG.md"])
    version = project_version(run(["git", "show", "origin/main:pyproject.toml"]))
    tagged = run(["git", "tag", "--list", "v*"]).split()
    if found := problems(changelog, version, tagged):
        raise ReleaseError(
            f"main's head, {head[:12]}, isn't a release: {'; '.join(found)}"
        )
    identifier = checked_run(head, run)
    with tempfile.TemporaryDirectory() as folder:
        run(
            [
                "gh",
                "run",
                "download",
                identifier,
                "--name",
                SUMS_ARTIFACT,
                "--dir",
                folder,
            ]
        )
        sums = (Path(folder) / "SHA256SUMS").read_text(encoding="utf-8")
    text = release_notes(linked(changelog, version), sums)
    tag = f"v{version}"
    if dry_run:
        return (
            f"would tag {head[:12]} as {tag} and release it with these notes:\n\n{text}"
        )
    run(
        [
            "gh",
            "release",
            "create",
            tag,
            "--target",
            head,
            "--title",
            tag,
            "--notes",
            text,
        ]
    )
    return f"released {tag} on {head[:12]}"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m faro.release", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    cutting = commands.add_parser("cut", help="cut Unreleased into the next version")
    cutting.add_argument("--bump", choices=("minor", "patch"), default="minor")
    commands.add_parser("check", help="refuse unless the release is cut")
    releasing = commands.add_parser("release", help="tag and release main's head")
    releasing.add_argument("--dry-run", action="store_true")
    showing = commands.add_parser(
        "notes", help="print a version's section, as a release shows it"
    )
    showing.add_argument("version")
    args = parser.parse_args(argv)
    try:
        if args.command == "cut":
            print(cut_command(args.bump))
        elif args.command == "check":
            print(check_command())
        elif args.command == "release":
            print(release_command(args.dry_run))
        else:
            print(linked(CHANGELOG.read_text(encoding="utf-8"), args.version))
    except ReleaseError as error:
        raise SystemExit(f"error: {error}") from error
    except subprocess.CalledProcessError as error:
        failed = " ".join(error.cmd[:3])
        raise SystemExit(
            f"error: {failed} failed: {(error.stderr or '').strip()}"
        ) from error


if __name__ == "__main__":
    main()

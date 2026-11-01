"""
A release is cut from the changelog first and named last, once main's deploy has passed its check (ADR-0009).
"""

import datetime
import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from faro import release

REPO = "https://github.com/owner/repo"
HEAD = "f" * 40
CHANGELOG = f"""# Changelog

What changed.

## Unreleased

### Added

- A check after each deploy ([#2]).

## [1.5.0] - 2026-10-31 (Promotion 11)

### Changed

- The chat runs on AG-UI 1.0.

## [1.0.0] - 2026-10-05

The prototype as submitted.

[1.5.0]: {REPO}/releases/tag/v1.5.0
[1.0.0]: {REPO}/releases/tag/v1.0.0
[#2]: {REPO}/pull/2
"""
CUT = f"""# Changelog

What changed.

## Unreleased

## [1.6.0] - 2026-11-02 (Promotion 12)

### Added

- A check after each deploy ([#2]).

## [1.5.0] - 2026-10-31 (Promotion 11)

### Changed

- The chat runs on AG-UI 1.0.

## [1.0.0] - 2026-10-05

The prototype as submitted.

[1.6.0]: {REPO}/releases/tag/v1.6.0
[1.5.0]: {REPO}/releases/tag/v1.5.0
[1.0.0]: {REPO}/releases/tag/v1.0.0
[#2]: {REPO}/pull/2
"""
TODAY = datetime.date(2026, 11, 2)


def pyproject(version: str) -> str:
    return f'[project]\nname = "faro"\nversion = "{version}"\n'


def test_a_cut_moves_unreleased_under_the_next_promotions_heading() -> None:
    assert release.cut(CHANGELOG, "1.6.0", TODAY) == CUT
    assert release.unreleased(CUT) == ""
    assert release.top(CUT) == release.Release("1.6.0", "2026-11-02", 12)


def test_a_promotion_is_a_minor_release_and_one_that_only_fixes_is_a_patch() -> None:
    assert release.bumped("1.5.0", "minor") == "1.6.0"
    assert release.bumped("1.6.0", "patch") == "1.6.1"
    assert release.bumped("1.6.1", "minor") == "1.7.0"


def test_a_versions_notes_are_its_section() -> None:
    assert (
        release.notes(CHANGELOG, "1.5.0")
        == "### Changed\n\n- The chat runs on AG-UI 1.0."
    )
    assert release.notes(CHANGELOG, "1.0.0") == "The prototype as submitted."
    with pytest.raises(release.ReleaseError, match="no section for 1.4.0"):
        release.notes(CHANGELOG, "1.4.0")


def test_a_releases_notes_carry_the_links_their_section_uses() -> None:
    changelog = CUT.replace(
        "- A check after each deploy ([#2]).",
        "- A check after each deploy ([ADR-0009](docs/adr/0009-release.md#decision), [#2]).",
    )

    assert release.linked(changelog, "1.6.0") == (
        "### Added\n\n- A check after each deploy "
        f"([ADR-0009]({REPO}/blob/v1.6.0/docs/adr/0009-release.md#decision), [#2]).\n\n"
        f"[#2]: {REPO}/pull/2"
    )
    assert release.linked(changelog, "1.0.0") == "The prototype as submitted."


def test_a_promotion_is_refused_until_its_release_is_cut() -> None:
    uncut, released = release.problems(CHANGELOG, "1.5.0", ["v1.5.0"])
    assert "Unreleased isn't empty" in uncut
    assert "v1.5.0 is already tagged" in released
    assert release.problems(CUT, "1.6.0", ["v1.0.0", "v1.5.0"]) == []
    (disagree,) = release.problems(CUT, "1.5.0", [])
    assert "top version is 1.6.0 and pyproject.toml's is 1.5.0" in disagree
    (tagged,) = release.problems(CUT, "1.6.0", ["v1.6.0"])
    assert "v1.6.0 is already tagged" in tagged


def test_the_changelogs_top_version_is_the_projects() -> None:
    changelog = release.CHANGELOG.read_text(encoding="utf-8")
    headings = [line for line in changelog.splitlines() if line.startswith("## [")]

    assert len(release.releases(changelog)) == len(headings)
    assert release.top(changelog).version == release.project_version(
        release.PYPROJECT.read_text(encoding="utf-8")
    )


def test_a_cut_sets_the_version_and_warns_when_the_last_has_no_release(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    changelog, project = tmp_path / "CHANGELOG.md", tmp_path / "pyproject.toml"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    project.write_text(pyproject("1.5.0"), encoding="utf-8")
    commands: list[list[str]] = []

    def run(command: Sequence[str]) -> str:
        commands.append(list(command))
        if command[:2] == ["gh", "release"]:
            raise subprocess.CalledProcessError(1, command)
        return ""

    release.cut_command("minor", changelog, project, TODAY, run)

    assert changelog.read_text(encoding="utf-8") == CUT
    assert ["uv", "version", "1.6.0"] in commands
    assert "1.5.0 has no release" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("text", "version", "refusal"),
    [
        (CUT.replace("## Unreleased\n\n", ""), "1.6.0", "no Unreleased heading"),
        (CUT, "1.6.0", "Unreleased is empty"),
        (CHANGELOG, "1.4.0", "top version is 1.5.0"),
    ],
)
def test_a_cut_refuses_without_entries_or_when_the_versions_disagree(
    tmp_path: Path, text: str, version: str, refusal: str
) -> None:
    changelog, project = tmp_path / "CHANGELOG.md", tmp_path / "pyproject.toml"
    changelog.write_text(text, encoding="utf-8")
    project.write_text(pyproject(version), encoding="utf-8")

    with pytest.raises(release.ReleaseError, match=refusal):
        release.cut_command("minor", changelog, project, TODAY, lambda _: "")


def test_the_promotion_guard_reads_the_tags(tmp_path: Path) -> None:
    changelog, project = tmp_path / "CHANGELOG.md", tmp_path / "pyproject.toml"
    changelog.write_text(CUT, encoding="utf-8")
    project.write_text(pyproject("1.6.0"), encoding="utf-8")

    assert "1.6.0 is cut" in release.check_command(
        changelog, project, lambda _: "v1.5.0"
    )
    with pytest.raises(release.ReleaseError, match="already tagged"):
        release.check_command(changelog, project, lambda _: "v1.5.0\nv1.6.0")


def deploy(
    conclusion: str = "success", check: str = "success", event: str = "push"
) -> tuple[Callable[[Sequence[str]], str], list[list[str]]]:
    calls: list[list[str]] = []

    def run(command: Sequence[str]) -> str:
        calls.append(list(command))
        match list(command[:3]):
            case ["git", "rev-parse", _]:
                return HEAD
            case ["git", "show", "origin/main:CHANGELOG.md"]:
                return CUT
            case ["git", "show", "origin/main:pyproject.toml"]:
                return pyproject("1.6.0")
            case ["git", "tag", "--list"]:
                return "v1.0.0\nv1.5.0"
            case ["gh", "run", "list"]:
                runs = [{"databaseId": 7, "event": event, "status": "completed"}]
                return json.dumps([{**runs[0], "conclusion": conclusion}])
            case ["gh", "run", "view"]:
                steps = [{"name": release.CHECK_STEP, "conclusion": check}]
                checked = {"name": "Check", "conclusion": "success", "steps": steps}
                return json.dumps({"jobs": [{"name": "Build", "steps": []}, checked]})
            case ["gh", "run", "download"]:
                folder = Path(command[list(command).index("--dir") + 1])
                (folder / "SHA256SUMS").write_text(
                    "ab12  runtime.zip\n", encoding="utf-8"
                )
        return ""

    return run, calls


def test_a_release_tags_mains_head_once_its_deploy_and_check_passed() -> None:
    run, calls = deploy()

    previewed = release.release_command(True, run)
    assert "would tag ffffffffffff as v1.6.0" in previewed
    assert "- A check after each deploy ([#2])." in previewed
    assert "ab12  runtime.zip" in previewed
    assert not [c for c in calls if c[:3] == ["gh", "release", "create"]]

    release.release_command(False, run)
    (created,) = [c for c in calls if c[:3] == ["gh", "release", "create"]]
    assert created[3:7] == ["v1.6.0", "--target", HEAD, "--title"]
    notes = created[created.index("--notes") + 1]
    assert notes.startswith("### Added\n\n- A check after each deploy")
    assert notes.endswith("```\nab12  runtime.zip\n```\n")
    assert f"[#2]: {REPO}/pull/2\n\n### Build" in notes


@pytest.mark.parametrize(
    ("deployed", "refusal"),
    [
        ({"conclusion": "failure"}, "is completed, failure"),
        ({"check": "skipped"}, "passed without its check"),
        ({"event": "pull_request"}, "no deploy run"),
    ],
)
def test_a_release_refuses_a_deploy_that_failed_or_skipped_its_check(
    deployed: dict[str, str], refusal: str
) -> None:
    run, calls = deploy(**deployed)

    with pytest.raises(release.ReleaseError, match=refusal):
        release.release_command(False, run)
    assert not [c for c in calls if c[:2] == ["gh", "release"]]


def test_the_command_line_prints_a_section_or_a_refusal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    release.main(["notes", "1.0.0"])
    assert "Hackathon" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="no section for 0.0.1"):
        release.main(["notes", "0.0.1"])


def test_the_release_reads_the_job_step_and_artifact_the_deploy_workflow_names() -> (
    None
):
    workflow = (release.ROOT / ".github" / "workflows" / release.WORKFLOW).read_text(
        encoding="utf-8"
    )

    assert f"name: {release.CHECK_JOB}\n" in workflow
    assert f"name: {release.CHECK_STEP}\n" in workflow
    assert f"name: {release.SUMS_ARTIFACT}\n" in workflow

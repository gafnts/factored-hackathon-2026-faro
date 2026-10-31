"""
The build writes the same bytes on every machine, from uv.lock's arm64 wheels and this package (ADR-0004, Deployment;
OPS-07).
"""

import json
import os
import re
import subprocess
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pytest

from faro import build
from faro.contracts.gateway import tool_definitions

COMMIT = "0123456789abcdef0123456789abcdef01234567"


def tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def fake_wheel(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("faro/__init__.py", "")
        archive.writestr("faro-0.1.0.dist-info/METADATA", "Name: faro\n")
    return path


class Recorder:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.commands: list[list[str]] = []

    def __call__(self, command: Sequence[str]) -> None:
        self.commands.append(list(command))
        if command[:2] == ["uv", "build"]:
            fake_wheel(self.out / "dist" / "faro-0.1.0-py3-none-any.whl")


def test_a_zip_is_the_same_bytes_whenever_it_is_written(tmp_path: Path) -> None:
    source = tree(tmp_path / "src", {"b.py": "b", "a/c.py": "c"})
    build.write_zip(source, tmp_path / "one.zip")
    (source / "b.py").touch()
    build.write_zip(source, tmp_path / "two.zip")

    assert (tmp_path / "one.zip").read_bytes() == (tmp_path / "two.zip").read_bytes()
    with zipfile.ZipFile(tmp_path / "one.zip") as archive:
        infos = archive.infolist()
    assert [i.filename for i in infos] == ["a/c.py", "b.py"]
    assert {i.date_time for i in infos} == {build.EPOCH}
    assert {i.external_attr >> 16 for i in infos} == {0o644}


def test_console_scripts_and_bytecode_stay_out(tmp_path: Path) -> None:
    source = tree(
        tmp_path / "src",
        {
            "bin/uvicorn": "#!/Users/someone/.venv/bin/python",
            "pkg/__pycache__/m.cpython-313.pyc": "x",
            "pkg/m.py": "m",
            "pkg/bin/data.txt": "kept",
        },
    )
    build.write_zip(source, tmp_path / "out.zip")

    with zipfile.ZipFile(tmp_path / "out.zip") as archive:
        assert archive.namelist() == ["pkg/bin/data.txt", "pkg/m.py"]


def test_the_evaluation_package_stays_out(tmp_path: Path) -> None:
    source = tree(
        tmp_path / "src",
        {
            "faro/agent/app.py": "a",
            "faro/evaluation/oracle.py": "o",
            "faro/evaluation/families/none.json": "{}",
        },
    )
    build.write_zip(source, tmp_path / "out.zip")

    with zipfile.ZipFile(tmp_path / "out.zip") as archive:
        assert archive.namelist() == ["faro/agent/app.py"]


def test_the_runtime_gets_its_groups_wheels_this_package_and_an_entry_script(
    tmp_path: Path,
) -> None:
    recorder = Recorder(tmp_path)
    wheel = fake_wheel(tmp_path / "dist" / "faro-0.1.0-py3-none-any.whl")

    target = build.build_artifact(build.ARTIFACTS[0], wheel, tmp_path, COMMIT, recorder)

    export, install = recorder.commands
    assert export[:2] == ["uv", "export"]
    assert export[export.index("--only-group") + 1] == "agent"
    assert "--no-emit-project" in export
    assert install[:3] == ["uv", "pip", "install"]
    assert list(build.PLATFORM) == install[4 : 4 + len(build.PLATFORM)]
    with zipfile.ZipFile(target) as archive:
        assert set(archive.namelist()) == {
            "faro-0.1.0.dist-info/METADATA",
            "faro/__init__.py",
            "main.py",
        }
        assert archive.read("main.py").decode() == (
            f"from faro.agent.app import main\n\nmain('{COMMIT}')\n"
        )


def test_a_lambda_without_a_group_carries_this_package_only(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path)
    wheel = fake_wheel(tmp_path / "dist" / "faro-0.1.0-py3-none-any.whl")
    pre_token = next(a for a in build.ARTIFACTS if a.name == "pre_token")

    target = build.build_artifact(pre_token, wheel, tmp_path, COMMIT, recorder)

    assert recorder.commands == []
    with zipfile.ZipFile(target) as archive:
        assert "main.py" not in archive.namelist()


def test_the_committed_gateway_tools_are_the_contracts_reduced_copy() -> None:
    assert build.GATEWAY_TOOLS.read_text(encoding="utf-8") == build.gateway_tools(), (
        "infra/modules/gateway/tools.json is stale; run make build"
    )
    assert json.loads(build.gateway_tools()) == tool_definitions()


def test_main_builds_every_artifact_and_rewrites_the_gateway_tools(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out, tools = tmp_path / "build", tmp_path / "tools.json"
    built = build.main(out, tools, Recorder(out), COMMIT)

    assert [p.name for p in built] == [f"{a.name}.zip" for a in build.ARTIFACTS]
    assert tools.read_text(encoding="utf-8") == build.gateway_tools()
    assert "build/runtime.zip" in capsys.readouterr().out


@pytest.mark.parametrize("dirty", ["", " M src/faro/agent/app.py"])
def test_the_runtime_is_stamped_with_the_last_commit_of_its_code(
    dirty: str, capsys: pytest.CaptureFixture[str]
) -> None:
    asked: list[list[str]] = []

    def git(args: Sequence[str]) -> str:
        asked.append(list(args))
        return COMMIT if args[0] == "log" else dirty

    assert build.app_version(git) == COMMIT
    assert asked[0] == ["log", "-1", "--format=%H", "--", *build.PACKAGED]
    assert ("uncommitted" in capsys.readouterr().err) == bool(dirty)


def test_the_stamp_names_a_commit_of_this_repository() -> None:
    assert re.fullmatch("[0-9a-f]{40}", build.app_version())


def test_a_commit_to_the_evaluation_package_alone_keeps_the_stamp(
    tmp_path: Path,
) -> None:
    # A git hook exports GIT_DIR and its kin, which would point these commands at the repository running the tests.
    alone = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

    def git(args: Sequence[str]) -> str:
        done = subprocess.run(
            ["git", *args],
            check=True,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            env=alone,
        )
        return done.stdout.strip()

    def commit(files: dict[str, str]) -> None:
        tree(tmp_path, files)
        git(["add", "."])
        git(
            ["-c", "user.name=t", "-c", "user.email=t@example.com"]
            + ["-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "c"]
        )

    git(["init", "--quiet"])
    commit({"src/faro/agent/app.py": "a"})
    stamped = build.app_version(git)
    commit({"src/faro/evaluation/oracle.py": "o"})

    assert build.app_version(git) == stamped

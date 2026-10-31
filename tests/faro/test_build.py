"""
The build writes the same bytes on every machine, from uv.lock's arm64 wheels and each zip's share of this package
(ADR-0004, Deployment; OPS-07).
"""

import json
import os
import re
import subprocess
import sys
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


class Recorder:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command: Sequence[str]) -> None:
        self.commands.append(list(command))


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


def test_what_the_install_adds_and_bytecode_stay_out(tmp_path: Path) -> None:
    source = tree(
        tmp_path / "src",
        {
            "bin/uvicorn": "#!/Users/someone/.venv/bin/python",
            ".lock": "",
            "pkg/__pycache__/m.cpython-313.pyc": "x",
            "pkg/m.py": "m",
            "pkg/bin/data.txt": "kept",
        },
    )
    build.write_zip(source, tmp_path / "out.zip")

    with zipfile.ZipFile(tmp_path / "out.zip") as archive:
        assert archive.namelist() == ["pkg/bin/data.txt", "pkg/m.py"]


def test_a_record_lists_only_what_its_zip_holds(tmp_path: Path) -> None:
    source = tree(
        tmp_path / "src",
        {
            "bin/uvicorn": "#!/Users/someone/.venv/bin/python",
            "uvicorn/main.py": "m",
            "uvicorn-1.0.dist-info/RECORD": (
                "bin/uvicorn,sha256=machine,35\n"
                '"uvicorn/main.py",sha256=m,1\n'
                "uvicorn-1.0.dist-info/RECORD,,\n"
            ),
        },
    )
    build.write_zip(source, tmp_path / "out.zip")

    with zipfile.ZipFile(tmp_path / "out.zip") as archive:
        assert archive.read("uvicorn-1.0.dist-info/RECORD").decode() == (
            '"uvicorn/main.py",sha256=m,1\nuvicorn-1.0.dist-info/RECORD,,\n'
        )


def test_the_runtime_gets_its_groups_wheels_its_share_and_an_entry_script(
    tmp_path: Path,
) -> None:
    recorder = Recorder()

    target = build.build_artifact(build.RUNTIME, tmp_path, COMMIT, recorder)

    export, install = recorder.commands
    assert export[:2] == ["uv", "export"]
    assert export[export.index("--only-group") + 1] == "agent"
    assert "--no-emit-project" in export
    assert install[:3] == ["uv", "pip", "install"]
    assert list(build.PLATFORM) == install[4 : 4 + len(build.PLATFORM)]
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        assert archive.read("main.py").decode() == (
            f"from faro.agent.app import main\n\nmain('{COMMIT}')\n"
        )
    assert {name.split("/")[0] for name in names} == {"faro", "main.py"}
    assert {name.split("/")[1] for name in names if name.startswith("faro/")} == {
        "__init__.py",
        *build.RUNTIME.code,
    }
    assert "faro/agent/prompts/reply.md" in names
    assert "faro/contracts/execution-record.schema.json" in names
    assert not [name for name in names if name.startswith("faro/contracts/examples/")]


def test_a_lambda_without_a_group_carries_its_share_alone(tmp_path: Path) -> None:
    recorder = Recorder()
    pre_token = next(a for a in build.ARTIFACTS if a.name == "pre_token")

    target = build.build_artifact(pre_token, tmp_path, COMMIT, recorder)

    assert recorder.commands == []
    with zipfile.ZipFile(target) as archive:
        assert archive.namelist() == [
            "faro/__init__.py",
            "faro/identity/__init__.py",
            "faro/identity/pre_token.py",
        ]


@pytest.mark.parametrize("artifact", build.ARTIFACTS, ids=lambda a: a.name)
def test_each_share_imports_on_its_own(
    artifact: build.Artifact, tmp_path: Path
) -> None:
    # The deployed code imports at the top of its modules, so importing each module finds every module it needs.
    build.copy_code(artifact, tmp_path)
    modules = [
        ".".join(path.relative_to(tmp_path).with_suffix("").parts).removesuffix(
            ".__init__"
        )
        for path in sorted(tmp_path.rglob("*.py"))
    ]
    # A fresh interpreter, with the share ahead of the editable install of the whole package.
    script = (
        "import importlib, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "for module in sys.argv[2:]:\n"
        "    importlib.import_module(module)\n"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", script, str(tmp_path), *modules],
        capture_output=True,
        text=True,
    )

    assert done.returncode == 0, done.stderr


def test_the_committed_gateway_tools_are_the_contracts_reduced_copy() -> None:
    assert build.GATEWAY_TOOLS.read_text(encoding="utf-8") == build.gateway_tools(), (
        "infra/modules/gateway/tools.json is stale; run make build"
    )
    assert json.loads(build.gateway_tools()) == tool_definitions()


def test_main_builds_every_artifact_and_rewrites_the_gateway_tools(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out, tools = tmp_path / "build", tmp_path / "tools.json"
    built = build.main(out, tools, Recorder(), COMMIT)

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
    assert asked[0] == [
        "log",
        "-1",
        "--format=%H",
        "--",
        *build.pathspecs(build.RUNTIME),
    ]
    assert ("uncommitted" in capsys.readouterr().err) == bool(dirty)


def test_the_stamp_names_a_commit_of_this_repository() -> None:
    assert re.fullmatch("[0-9a-f]{40}", build.app_version())


def test_only_a_commit_to_the_runtimes_share_moves_the_stamp(tmp_path: Path) -> None:
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
    for elsewhere in (
        {"src/faro/evaluation/oracle.py": "o"},
        {"src/faro/console/case.py": "c"},
        {"src/faro/contracts/examples/chat.json": "{}"},
        {"pyproject.toml": "dev tools", "uv.lock": "dev tools", "README.md": "docs"},
    ):
        commit(elsewhere)
        assert build.app_version(git) == stamped
    commit({"src/faro/tools/cards.py": "t"})

    assert build.app_version(git) != stamped

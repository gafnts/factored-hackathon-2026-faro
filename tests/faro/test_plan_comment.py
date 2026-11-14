"""
A promotion's plan comment always shows the totals and every action, and says where the whole plan is when it's cut.
"""

import ast
import sys
from pathlib import Path

import pytest

from faro import plan_comment
from faro.plan_comment import MISSING, comment

RUN = "https://github.com/owner/repo/actions/runs/1"
# What GitHub accepts in a comment, in characters.
LIMIT = 65_536
PLAN = """TF_DATA_DIR=.terraform/prototype terraform -chdir=infra plan

module.site.aws_s3_bucket.site: Refreshing state... [id=site]
module.site.data.aws_iam_policy_document.site: Reading...
module.site.data.aws_iam_policy_document.site: Read complete after 0s [id=1]

Terraform will perform the following actions:

  # module.guest.aws_lambda_function.entry will be created
  + resource "aws_lambda_function" "entry" {
      + function_name = "guest"
        # (12 unchanged attributes hidden)
    }

  # module.suites.data.aws_iam_policy_document.suites will be read during apply
  # (config refers to values not yet known)
 <= data "aws_iam_policy_document" "suites" {
      + json = (known after apply)
    }

  # module.runtime.aws_s3_object.runtime must be replaced
-/+ resource "aws_s3_object" "runtime" {
      ~ etag = "a" -> "b" # forces replacement
    }

Plan: 2 to add, 0 to change, 1 to destroy.
"""


def long_plan(resources: int) -> str:
    blocks = [
        f"  # aws_ssm_parameter.entry[{index}] will be created\n"
        f'  + resource "aws_ssm_parameter" "entry" {{\n'
        f'      + value = "{"v" * 200}"\n'
        f"    }}\n"
        for index in range(resources)
    ]
    return "\n".join([*blocks, f"Plan: {resources} to add, 0 to change, 0 to destroy."])


def test_the_totals_and_every_action_come_before_the_plan() -> None:
    summary, details = comment(PLAN, "prototype", False, RUN).split("<details>")
    assert summary.splitlines() == [
        "#### Terraform Plan · `prototype` ✅",
        "",
        "~~~",
        "Plan: 2 to add, 0 to change, 1 to destroy.",
        "",
        "module.guest.aws_lambda_function.entry will be created",
        "module.suites.data.aws_iam_policy_document.suites will be read during apply",
        "module.runtime.aws_s3_object.runtime must be replaced",
        "~~~",
        "",
    ]
    assert '+ function_name = "guest"' in details
    assert "Cut to fit" not in details


def test_the_lines_terraform_prints_while_it_refreshes_are_dropped() -> None:
    posted = comment(PLAN, "prototype", False, RUN)
    assert "Refreshing state" not in posted
    assert "Reading..." not in posted
    assert "Read complete" not in posted


def test_a_plan_past_the_budget_is_cut_at_a_line_and_names_the_run() -> None:
    plan = long_plan(400)
    posted = comment(plan, "prototype", False, RUN)
    assert len(posted) <= LIMIT
    assert "Plan: 400 to add" in posted.split("<details>")[0]
    assert posted.split("<details>")[0].count("will be created") == 400
    details = posted.split("<details>")[1]
    shown = details.split("~~~")[1].strip("\n").splitlines()
    assert shown == plan.splitlines()[: len(shown)]
    assert 0 < len(shown) < len(plan.splitlines())
    assert f"its first {len(shown):,} of {len(plan.splitlines()):,} lines" in details
    assert f"[this run]({RUN})" in details


def test_a_failed_plan_past_the_budget_keeps_its_end() -> None:
    plan = "\n".join(
        [
            *long_plan(400).splitlines()[:-1],
            "╷",
            "│ Error: Unsupported argument",
            "│ ",
            "│   on main.tf line 3",
            "╵",
        ]
    )
    posted = comment(plan, "prototype", True, RUN)
    assert posted.startswith("#### Terraform Plan · `prototype` ❌")
    assert "Error: Unsupported argument" in posted.split("<details>")[0]
    shown = posted.split("<details>")[1].split("~~~")[1].strip("\n").splitlines()
    assert shown == plan.splitlines()[-len(shown) :]
    assert "its last" in posted


def test_a_plan_with_nothing_to_do_says_so_first() -> None:
    plan = "No changes. Your infrastructure matches the configuration.\n"
    posted = comment(plan, "prototype", False, RUN)
    assert posted.split("<details>")[0].splitlines()[2:5] == [
        "~~~",
        "No changes. Your infrastructure matches the configuration.",
        "~~~",
    ]


def test_more_actions_than_a_comment_holds_still_fit() -> None:
    posted = comment(long_plan(2000), "prototype", False, RUN)
    assert len(posted.encode()) <= LIMIT
    assert "Cut to fit a comment" in posted


def test_a_job_that_stopped_before_planning_says_where_to_look(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_comment.main(
        [
            str(tmp_path / "plan.txt"),
            "--environment=prototype",
            "--result=failure",
            f"--run-url={RUN}",
        ]
    )
    assert capsys.readouterr().out == (
        f"#### Terraform Plan · `prototype` ❌\n\n{MISSING}\n"
    )


def test_the_command_prints_the_comment_for_the_plan_it_is_given(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "plan.txt"
    path.write_text(PLAN, encoding="utf-8")
    plan_comment.main(
        [str(path), "--environment=prototype", "--result=success", f"--run-url={RUN}"]
    )
    assert capsys.readouterr().out == comment(PLAN, "prototype", False, RUN)


def test_the_script_imports_the_standard_library_alone() -> None:
    source = Path(plan_comment.__file__).read_text(encoding="utf-8")
    imports = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import | ast.ImportFrom)
    ]
    assert not [node for node in imports if getattr(node, "level", 0)]
    names = {
        (node.module or "" if isinstance(node, ast.ImportFrom) else alias.name).split(
            "."
        )[0]
        for node in imports
        for alias in node.names
    }
    assert names <= sys.stdlib_module_names

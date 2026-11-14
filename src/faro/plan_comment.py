"""
A promotion's plan as the comment its reviewers read (ADR-0009): the totals and every action first, then the plan
without the lines Terraform prints while it refreshes. A comment ends at 65,536 characters, so a plan that doesn't fit
is cut where a line ends, and the comment says so and names the run that keeps the whole file.

The comment job runs this file with nothing installed, beside a token that writes to pull requests, so it imports
the standard library alone.
"""

import argparse
import re
from collections.abc import Sequence
from pathlib import Path

# In bytes, which never undercount characters, with room left for the comment's own markup.
BUDGET = 60_000
REFRESH = re.compile(r": (Refreshing state\.\.\.|Reading\.\.\.|Read complete)")
TOTAL = re.compile(r"^(?:│ )?(Plan: .*|No changes\. .*|Error: .*)")
# A line in parentheses under an action gives its reason, and isn't one.
ACTION = re.compile(r"^  # (?!\()(.*)")
MISSING = "The plan job stopped before planning; its log says why."


def fitted(lines: Sequence[str], room: int, from_end: bool = False) -> list[str]:
    """
    The lines that fit in the room, from the plan's start, or from its end, where a failed plan says why.
    """
    ordered = lines[::-1] if from_end else lines
    kept = 0
    for line in ordered:
        room -= len(line.encode()) + 1
        if room < 0:
            break
        kept += 1
    return list(lines[len(lines) - kept :] if from_end else lines[:kept])


def fenced(lines: Sequence[str]) -> str:
    return "\n".join(["~~~", *lines, "~~~"])


def comment(plan: str | None, environment: str, failed: bool, run_url: str) -> str:
    heading = f"#### Terraform Plan · `{environment}` {'❌' if failed else '✅'}"
    if plan is None:
        return f"{heading}\n\n{MISSING}\n"
    lines = [line for line in plan.splitlines() if not REFRESH.search(line)]
    totals = [found.group(1) for line in lines if (found := TOTAL.match(line))]
    actions = [found.group(1) for line in lines if (found := ACTION.match(line))]
    summary = [*totals, *([""] if totals and actions else []), *actions]

    first = fitted(summary, BUDGET)
    shown = fitted(lines, BUDGET - len(fenced(first).encode()), from_end=failed)
    parts = [heading]
    if first:
        parts.append(fenced(first))
    parts.append(
        f"<details><summary>Show plan</summary>\n\n{fenced(shown)}\n\n</details>"
    )
    if len(first) < len(summary) or len(shown) < len(lines):
        which = "its last" if failed else "its first"
        parts.append(
            f"Cut to fit a comment: {which} {len(shown):,} of {len(lines):,} lines are shown. "
            f"The whole plan is the `plan` artifact of [this run]({run_url})."
        )
    return "\n\n".join(parts) + "\n"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m faro.plan_comment", description=__doc__
    )
    parser.add_argument(
        "plan", type=Path, help="the plan job's output, if it wrote any"
    )
    parser.add_argument("--environment", required=True)
    parser.add_argument("--result", choices=("success", "failure"), required=True)
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args(argv)
    plan = args.plan.read_text(encoding="utf-8") if args.plan.exists() else None
    print(
        comment(plan, args.environment, args.result == "failure", args.run_url), end=""
    )


if __name__ == "__main__":
    main()

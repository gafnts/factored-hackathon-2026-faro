"""
The commands that call models outside the system under test, and the relabel, which __main__ adds to its own.
relabel-sample draws the 50 messages of a source relabeled by hand for label quality and relabel-agreement scores the
filled sheet (labels.py); neither calls a model. router compares the registered router candidates on one side of the
split by source, keeping its readings, errors, calls, and report under data/evaluation/router/. With --estimate a command prices its calls and makes none; otherwise it reads the
Anthropic key from the env file, as the live language check does, and sends it to the client only. Each prints
counts, never a reply, a value, or the key (SEC-03).
"""

import argparse
import asyncio
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from faro.evaluation import (
    baseline,
    classifier,
    families,
    labels,
    router,
    runs,
)
from faro.model_key import ModelKeyError

COMMANDS = (
    "relabel-sample",
    "relabel-agreement",
    "router",
)

# Each maker takes the env file and the list the candidate records its calls into, and reads no key until it calls.
Make = Callable[[Path, list[dict[str, Any]]], router.Candidate]
CANDIDATES: dict[str, Make] = {
    "keyword": lambda env_file, calls: router.routed("keyword", baseline.route),
    "haiku": classifier.haiku,
    "sonnet": classifier.sonnet,
}


def add(commands: Any) -> None:
    comparing = commands.add_parser(
        "router",
        help="Compare router candidates per language on one side of the family split, paired over families",
    )
    comparing.add_argument(
        "--candidate", action="append", required=True, choices=sorted(CANDIDATES)
    )
    comparing.add_argument("--side", choices=router.SIDES, default="development")
    comparing.add_argument(
        "--folds",
        type=int,
        default=None,
        help="Cross-validate over this many family folds",
    )
    comparing.add_argument("--parallel", type=int, default=router.PARALLEL)
    comparing.add_argument("--env-file", type=Path, default=Path(".env"))
    comparing.add_argument("--out", type=Path, default=router.OUT)
    comparing.add_argument(
        "--estimate", action="store_true", help="Price it without calling a model"
    )
    relabeling = commands.add_parser(
        "relabel-sample",
        help="Draw the messages relabeled by hand for label quality, and the sheet",
    )
    relabeling.add_argument(
        "--source", choices=sorted(families.SIDES), default=families.OUTSIDE
    )
    relabeling.add_argument("--out", type=Path, default=labels.OUT)
    relabeling.add_argument("--seed", type=int, default=labels.SEED)
    relabeling.add_argument("--rows", type=int, default=labels.ROWS)
    relabeled = commands.add_parser(
        "relabel-agreement",
        help="Score the filled relabel sheet against the families' labels",
    )
    relabeled.add_argument(
        "--sample",
        type=Path,
        required=True,
        help="A sample under data/evaluation/labels/",
    )


def run_relabel_sample(args: argparse.Namespace) -> None:
    out = args.out / runs.run_id()
    manifest = labels.sample(out, args.source, seed=args.seed, rows=args.rows)
    print(
        f"{manifest['rows']} messages: by language {manifest['by_language']}, by group {manifest['by_group']}, "
        f"by side {manifest['by_side']}, by author {manifest['by_author']}"
    )
    print(f"label {out / 'sheet.csv'} blind, reading {out / 'guide.md'} first")


def run_relabel_agreement(args: argparse.Namespace) -> None:
    found = labels.score(args.sample)
    exact, kappa = found["exact"], found["kappa_first_label"]
    print(
        f"{found['rows']} rows: exact agreement {exact['value']} [{exact['low']}, {exact['high']}] "
        f"({exact['agreed']} agreed); kappa on the first label {kappa['value']} [{kappa['low']}, {kappa['high']}]; "
        f"{len(found['disagreements'])} to settle by the policy's text"
    )
    for name in ("by_author", "by_side", "by_kind"):
        print(
            f"  {name}: "
            + ", ".join(
                f"{k} {v['agreed']} of {v['rows']}" for k, v in found[name].items()
            )
        )
    print(f"kept in {labels.write(found, args.sample)}")


def run_router(args: argparse.Namespace, code: dict[str, Any]) -> None:
    calls: list[dict[str, Any]] = []
    chosen = [CANDIDATES[name](args.env_file, calls) for name in args.candidate]
    if args.estimate:
        found, _ = router.side_messages(args.side)
        for name, priced in router.estimate(chosen, found).items():
            print(
                f"{name}: {priced['calls']} calls, about {priced['cost_usd']:.2f} USD"
            )
        return
    out = args.out / runs.run_id()
    report = asyncio.run(
        router.compare(chosen, args.side, out, code, calls, args.folds, args.parallel)
    )
    for code_name, part in report["by_language"].items():
        print(
            f"{code_name}: {part['messages']} messages from {part['families']} families"
        )
        for name, figures in part["candidates"].items():
            f1, gate = figures["macro_f1"], figures["gate_accuracy"]
            print(
                f"  {name}: macro F1 {f1['value']} [{f1['low']}, {f1['high']}], "
                f"gate {gate['value']} [{gate['low']}, {gate['high']}]"
            )
        for pair, compared in part["differences"].items():
            print(
                f"  {pair}: {', '.join(f'{k} {v["beats"]}' for k, v in compared.items())}"
            )
    print(f"{report['totals']}; kept in {out}")


def main(args: argparse.Namespace, code: dict[str, Any]) -> int:
    try:
        if args.command == "relabel-sample":
            run_relabel_sample(args)
        elif args.command == "relabel-agreement":
            run_relabel_agreement(args)
        elif args.command == "router":
            run_router(args, code)
    except (
        ModelKeyError,
        labels.LabelsError,
        router.RouterError,
    ) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0

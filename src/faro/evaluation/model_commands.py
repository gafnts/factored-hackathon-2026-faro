"""
The commands that call models outside the system under test, and the judge's validation, which __main__ adds to its
own. judge has Claude Opus 5.5 grade a run's replies, or a sample's, through the batch API, keeping its items,
attempts, judgments, and manifest under data/evaluation/judge/. judge-sample draws the blind sample and its sheet from
a run's replies, and judge-agreement scores a judge run against the filled sheet; neither calls a model.
relabel-sample draws the 50 messages relabeled by hand for label quality and relabel-agreement scores the filled
sheet (labels.py); neither calls a model either. router
compares the registered router candidates on one side of the family split, keeping its readings, errors, calls, and
report under data/evaluation/router/. With --estimate a command prices its calls and makes none; otherwise it reads the
Anthropic key from the env file, as the live language check does, and sends it to the client only. Each prints
counts, never a reply, a value, or the key (SEC-03).
"""

import argparse
import asyncio
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anthropic

from faro.evaluation import (
    agreement,
    baseline,
    blind,
    classifier,
    families,
    judge,
    labels,
    router,
    rubric,
    runs,
)
from faro.model_key import ModelKeyError, read_key

COMMANDS = (
    "judge",
    "judge-sample",
    "judge-agreement",
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
    sampling = commands.add_parser(
        "judge-sample",
        help="Draw the judge's blind sample from a run's replies, with seeded failing ones, and its sheet",
    )
    sampling.add_argument(
        "--run", type=Path, required=True, help="A run under data/evaluation/runs/"
    )
    sampling.add_argument("--out", type=Path, default=blind.OUT)
    sampling.add_argument("--seed", type=int, default=blind.SEED)
    sampling.add_argument("--per-language", type=int, default=blind.PER_LANGUAGE)
    sampling.add_argument(
        "--seeded",
        type=int,
        default=None,
        help="Seeded replies per question: the bar's count, or more, never fewer",
    )
    agreeing = commands.add_parser(
        "judge-agreement",
        help="Score the judge's agreement and kappa against a filled blind sheet, with intervals",
    )
    agreeing.add_argument(
        "--sample",
        type=Path,
        required=True,
        help="A sample under data/evaluation/judge/samples/",
    )
    agreeing.add_argument(
        "--judged",
        type=Path,
        required=True,
        help="The judge run over the sample's items",
    )
    relabeling = commands.add_parser(
        "relabel-sample",
        help="Draw the messages relabeled by hand for label quality, and the sheet",
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
    judging = commands.add_parser(
        "judge",
        help="Have the judge grade a run's replies, or a sample's, through the batch API",
    )
    source = judging.add_mutually_exclusive_group(required=True)
    source.add_argument("--run", type=Path, help="A run under data/evaluation/runs/")
    source.add_argument(
        "--items", type=Path, help="Items to judge, as a sample keeps them"
    )
    judging.add_argument("--env-file", type=Path, default=Path(".env"))
    judging.add_argument("--out", type=Path, default=judge.OUT)
    judging.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Judge this many, each question covered first",
    )
    judging.add_argument(
        "--estimate", action="store_true", help="Price it without calling the model"
    )


def run_judge(args: argparse.Namespace, code: dict[str, Any]) -> None:
    loaded = rubric.load()
    found = judge.run_items(args.run) if args.run else judge.read_items(args.items)
    if args.limit is not None:
        found = judge.chosen(found, loaded, args.limit)
    if args.estimate:
        priced = judge.estimate(found, loaded)
        print(
            f"{priced['items']} replies: about {priced['input_tokens']} input and {priced['output_tokens']} "
            f"output tokens, about {priced['cost_usd']:.2f} USD at the batch's list price of {priced['priced_on']}"
        )
        return
    client = anthropic.Anthropic(api_key=read_key(args.env_file))
    started = datetime.now(UTC)
    judged = judge.judge(found, loaded, judge.AnthropicBatches(client))
    out = args.out / runs.run_id()
    manifest = judge.keep(
        out,
        found,
        loaded,
        judged,
        judge.source(args.run, args.items, found),
        code,
        (started, datetime.now(UTC)),
    )
    print(
        f"judged {manifest['items']} replies in {manifest['attempts']} attempts; outcomes {manifest['outcomes']}; "
        f"{manifest['by_hand']} judgments for a person; {manifest['totals']['cost_usd']:.4f} USD; kept in {out}"
    )


def run_sample(args: argparse.Namespace) -> None:
    out = args.out / runs.run_id()
    manifest = blind.sample(
        args.run, out, rubric.load(), args.seed, args.per_language, args.seeded
    )
    source = manifest["source"]
    print(
        f"{manifest['rows']} rows from the {source['set']} set played {source['mode']}: natural "
        f"{manifest['natural']}, by outcome {manifest['natural_by_outcome']}; seeded {manifest['seeded']}"
    )
    if manifest["seeded_short"]:
        print(f"questions short of seeded replies: {manifest['seeded_short']}")
    if not manifest["validates"]:
        print(
            "only the selection set played end to end validates the judge; this sample checks the tooling"
        )
    print(f"grade {out / 'sheet.csv'} blind, reading {out / 'guide.md'} first")


def run_agreement(args: argparse.Namespace) -> None:
    found = agreement.report(args.sample, args.judged, rubric.load())
    for q in found["questions"]:
        share, kappa = q["agreement"], q["kappa"]
        print(
            f"{q['question']}: {q['pairs']} graded ({q['natural']} natural, {q['seeded']} seeded); agreement "
            f"{share['value']} [{share['low']}, {share['high']}]; {q['kappa']['kind']} kappa {kappa['value']} "
            f"[{kappa['low']}, {kappa['high']}]; {sum(q['deserve_no'].values())} deserve a no, caught "
            f"{q['caught']['natural']['caught'] + q['caught']['seeded']['caught']}; {q['verdict']}"
        )
    if not found["validates"]:
        print(
            "this sample doesn't validate the judge: its replies aren't the selection set's played end to end"
        )
    print(f"kept in {agreement.write(found, args.sample)}")


def run_relabel_sample(args: argparse.Namespace) -> None:
    out = args.out / runs.run_id()
    manifest = labels.sample(out, seed=args.seed, rows=args.rows)
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
        loaded, answers = families.load(), families.load_answers()
        found = router.messages(
            loaded, families.held_out_ids(loaded, answers), args.side
        )
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
        if args.command == "judge":
            run_judge(args, code)
        elif args.command == "judge-sample":
            run_sample(args)
        elif args.command == "judge-agreement":
            run_agreement(args)
        elif args.command == "relabel-sample":
            run_relabel_sample(args)
        elif args.command == "relabel-agreement":
            run_relabel_agreement(args)
        elif args.command == "router":
            run_router(args, code)
    except (
        ModelKeyError,
        blind.SampleError,
        agreement.AgreementError,
        labels.LabelsError,
        router.RouterError,
    ) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0

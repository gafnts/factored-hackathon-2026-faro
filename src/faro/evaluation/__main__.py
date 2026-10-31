"""
The evaluation's commands. generate draws the development regression and selection sets from the pinned snapshot's
bronze (the last pipeline build) into data/evaluation/sets/, and the held-out set when asked (once; after a run, a
redraw only rebuilds it, heldout.py), and writes their manifests to docs/evaluation/sets/: case IDs, hashes, and
counts. play plays a drawn set in process with the scripted models, or the deterministic baseline's, and grades it,
keeping the evidence, grades, and manifest under data/evaluation/runs/. run plays a drawn set end to end against a
deployed stack and grades it, keeping each case's results under data/evaluation/runs/ and in the stack's evaluation
bucket; cleanup deletes the test users a stopped run left behind. disagreements regenerates the
disagreement log's page. language runs the real prompts over the development side's paraphrases and answers and writes
the language check's report and page under docs/evaluation/. Each prints counts, situations, and checks, never an ID, a
token, or a value (SEC-03).
"""

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from faro.agent.models import Models, anthropic_factory
from faro.dataset.lock import LockError, read_lock
from faro.evaluation import (
    bronze,
    cases,
    deployed,
    disagreements,
    endtoend,
    families,
    generator,
    heldout,
    language,
    model_commands,
    oracle,
    report,
    runs,
    state,
)
from faro.evaluation.facts import contract_words
from faro.model_key import ModelKeyError, read_key
from faro.pipeline import runner

SETS = ("regression", "selection")
PLAYED = (*SETS, heldout.NAME)
SET_DOCS = Path("docs/evaluation/sets")
FAMILIES = Path(families.__file__).parent


def commit() -> str:
    """
    The last commit to change the evaluation package, so a set's own commit, which holds only its manifest, doesn't
    move it.
    """
    head = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", "src/faro/evaluation"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", "src/faro/evaluation"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return f"{head}+dirty" if dirty else head


def families_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted(FAMILIES.glob("*.json")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def generate(
    lock_path: Path, data_dir: Path, docs: Path, seed: int, held_out: int | None
) -> None:
    """
    held_out is the held-out set's size, when it is drawn too: once, at the freeze, and after a run names it only as
    the committed manifest describes it, with the manifest left as it is.
    """
    lock = read_lock(lock_path)
    database = runner.workspace(data_dir, lock.snapshot_id).database
    if not database.is_file():
        raise LockError(f"no pipeline build at {database}; run make pipeline first")
    played = (
        heldout.runs_against(heldout.NAME, kept=data_dir / "evaluation" / "runs")
        if held_out is not None
        else []
    )
    manifest_path = docs / f"{heldout.NAME}.json"
    if played and not manifest_path.is_file():
        raise LockError(
            f"runs name the held-out set, and it has no manifest at {manifest_path}"
        )
    loaded, answers = families.load(), families.load_answers()
    held = families.held_out_ids(loaded, answers)

    def keep(set_name: str, drawn: generator.Drawn) -> None:
        cases.write(data_dir / "evaluation" / "sets" / f"{set_name}.jsonl", drawn.cases)
        written = generator.manifest(set_name, seed, drawn, versions)
        docs.mkdir(parents=True, exist_ok=True)
        (docs / f"{set_name}.json").write_text(
            json.dumps(written, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(
            f"{set_name}: {len(drawn.cases)} cases; by group "
            f"{generator.counts(drawn.cases, 'group')}; short in {len(drawn.short)} situation and language pairs"
        )

    with bronze.connect(database, "development") as con:
        built, clock = bronze.stamp(con)
        if clock != (state.BUSINESS_DATE, state.AS_OF):
            raise LockError(f"the build's clock {clock} isn't the policy's")
        versions = {
            **built,
            "generator": commit(),
            "families": families_hash(),
            "policy": oracle.POLICY_VERSION,
            "case_format": cases.VERSION,
        }
        drawing = generator.Generator(
            con, "development", loaded, answers, held, contract_words()
        )
        for set_name in SETS:
            keep(set_name, drawing.draw(set_name, seed))
    if held_out is None:
        return
    with bronze.connect(database, "held_out") as con:
        drawing = generator.Generator(
            con, "held_out", loaded, answers, held, contract_words()
        )
        drawn = drawing.draw(heldout.NAME, seed, generator.held_out(held_out))
    if not played:
        keep(heldout.NAME, drawn)
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    heldout.reproduce(played, drawn.cases, manifest, heldout.committed(manifest_path))
    cases.write(data_dir / "evaluation" / "sets" / f"{heldout.NAME}.jsonl", drawn.cases)
    print(
        f"{heldout.NAME}: {len(drawn.cases)} cases, the ones its committed manifest describes; the manifest is unchanged"
    )


def headline(found: dict[str, Any]) -> str:
    def counted(rate: dict[str, Any]) -> str:
        return f"{rate['count']} of {rate['cases']}"

    m03 = found["M-03"]
    return (
        f"M-01 {counted(found['M-01'])} resolved ({counted(found['M-01']['attempted'])} attempted); "
        f"M-02 {counted(found['M-02'])} without a transfer; M-03 {counted(m03)} transferred right, "
        f"{counted(m03['missed'])} missed, {counted(m03['unnecessary'])} unnecessary; "
        f"M-04 {counted(found['M-04'])} unsafe"
    )


def play(lock_path: Path, data_dir: Path, set_name: str, models: str) -> None:
    lock = read_lock(lock_path)
    database = runner.workspace(data_dir, lock.snapshot_id).database
    set_path = data_dir / "evaluation" / "sets" / f"{set_name}.jsonl"
    if not database.is_file() or not set_path.is_file():
        raise LockError(
            "no pipeline build or no drawn set; run make pipeline and make eval-sets"
        )
    with bronze.connect(database, "development") as con:
        built, _ = bronze.stamp(con)
    versions = {**built, "evaluation": commit(), "policy": oracle.POLICY_VERSION}
    out = data_dir / "evaluation" / "runs" / runs.run_id()
    held = None
    if set_name == heldout.NAME:
        manifest_path = SET_DOCS / f"{set_name}.json"
        if not manifest_path.is_file():
            raise LockError("no held-out manifest; it is drawn at the freeze")
        written = json.loads(manifest_path.read_text(encoding="utf-8"))
        held = (written, heldout.committed(manifest_path))
    summary = runs.play_set(set_path, database, out, versions, models, tree(), held)
    print(
        f"{set_name} with the {models} models: {summary['passed']} of {summary['cases']} passed; "
        f"{summary['diverged']} diverged, {summary['unsafe']} unsafe, {summary['errors']} not played; "
        f"{summary['set_aside']} set aside; kept in {out}"
    )
    print(headline(summary["metrics"]))
    print(f"findings matched by open entries: {summary['covered']}")
    print(f"findings no entry matches, by situation and check: {summary['uncovered']}")
    if summary["safety"]:
        print(f"safety checks failed: {summary['safety']}")


def check_language(
    env_file: Path, reports: Path, page: Path, parallel: int, only: list[str]
) -> None:
    """
    The key goes from the file to the client only, as make model-key sends it to the secret. A check narrowed to some
    families is printed and not kept, since the page reports whole runs.
    """
    loaded, answers = families.load(), families.load_answers()
    held = families.held_out_ids(loaded, answers)
    items = language.development_items(loaded, answers, held, only)
    costs: list[float] = []

    async def record(kind: str, **fields: Any) -> None:
        costs.append(fields.get("cost_usd") or 0.0)

    models = Models(anthropic_factory(read_key(env_file)), record)
    results = asyncio.run(language.check(models, items, parallel))
    found = language.report(results, sum(costs))
    for name, counts in found["by_language"].items():
        print(f"{name}: {counts['read']} of {counts['items']} read as expected")
    for miss in found["misses"]:
        print(f"  {miss['id']}: expected {miss['expected']}, said {miss['said']}")
    reasons = found["block_reasons"]
    print(f"block reasons: {reasons['read']} of {reasons['items']} read as expected")
    for miss in reasons["misses"]:
        print(f"  {miss['id']}: expected {miss['expected']}, said {miss['said']}")
    placed = found["cards_by_place"]
    print(f"cards by place: {placed['read']} of {placed['items']} read as expected")
    for miss in placed["misses"]:
        print(f"  {miss['id']}: expected {miss['expected']}, said {miss['said']}")
    if only:
        print(
            f"{len(found['misses'])} misses over {found['items']} items; {found['cost_usd']:.4f} USD; not kept"
        )
        return
    written = language.write(found, reports, page)
    print(
        f"{len(found['misses'])} misses over {found['items']} items; {found['cost_usd']:.4f} USD; "
        f"kept in {written} and {page}"
    )


def write_report(args: argparse.Namespace) -> None:
    """
    The results page from stored grades alone (ADR-0005, Reporting): the runs' set is read for its cases' fields, the
    snapshot for the customers' segment and country, and nothing of either is printed.
    """
    lock = read_lock(args.lock)
    database = runner.workspace(args.data_dir, lock.snapshot_id).database
    if not database.is_file():
        raise LockError(f"no pipeline build at {database}; run make pipeline first")
    sets = args.data_dir / "evaluation" / "sets"
    loaded = [report.load_run(path, sets) for path in args.run]
    baseline = None if args.baseline is None else report.load_run(args.baseline, sets)
    set_manifest = json.loads(
        (args.docs / f"{loaded[0].set_name}.json").read_text(encoding="utf-8")
    )
    sides = {c["side"] for run in loaded for c, _ in run.graded}
    if len(sides) != 1:
        raise report.ReportError("the runs' cases are of one side of the split")
    customers = report.profiles(
        database,
        sides.pop(),
        {c["customer_id"] for run in loaded for c, _ in run.graded},
    )
    found = report.build(
        loaded,
        set_manifest,
        customers,
        disagreements.load(),
        baseline,
        args.judged,
        args.agreement,
        labels=args.labels,
    )
    out = report.write(found, args.out, args.page)
    rep = found["repeated"]
    print(
        f"{len(loaded)} run(s) of {loaded[0].set_name} reported in {out} and {args.page}; "
        f"{rep['cases']} cases played every time"
    )
    print(headline(loaded[0].summary["metrics"]) + " (the first run)")


def tree() -> dict[str, Any]:
    """
    The commit a run's code is at, and whether the tree held changes besides.
    """
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return {"commit": head, "clean": not dirty}


def run(
    data_dir: Path,
    docs: Path,
    set_name: str,
    stack_path: Path,
    selection: dict[str, Any],
    parallel: int,
) -> None:
    set_path = data_dir / "evaluation" / "sets" / f"{set_name}.jsonl"
    manifest_path = docs / f"{set_name}.json"
    if not set_path.is_file() or not manifest_path.is_file():
        raise LockError("no drawn set; run make eval-sets")
    set_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    out = data_dir / "evaluation" / "runs"
    run_id, summary = endtoend.run(
        set_path,
        set_manifest,
        stack_path,
        out,
        tree(),
        selection,
        parallel,
        heldout.committed(manifest_path),
    )
    latency = summary["latency_ms"]
    print(
        f"{set_name} end to end: {summary['passed']} of {summary['cases']} passed; {summary['diverged']} diverged, "
        f"{summary['unsafe']} unsafe, {summary['errors']} not played; kept in {out / run_id}"
    )
    print(
        f"turn latency in ms, first turns {latency['first']}, later turns {latency['later']}; "
        f"{summary['resent']} resent, {summary['not_stopped']} sessions not stopped"
    )
    print(headline(summary["metrics"]))
    print(f"findings matched by open entries: {summary['covered']}")
    print(f"findings no entry matches, by situation and check: {summary['uncovered']}")
    if summary["safety"]:
        print(f"safety checks failed: {summary['safety']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m faro.evaluation", description=__doc__
    )
    parser.add_argument("--lock", type=Path, default=Path("dataset.lock"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    commands = parser.add_subparsers(dest="command", required=True)
    drawing = commands.add_parser(
        "generate",
        help="Draw the development regression and selection sets, and the held-out set when asked",
    )
    drawing.add_argument("--seed", type=int, default=20261001)
    drawing.add_argument("--docs", type=Path, default=SET_DOCS)
    drawing.add_argument(
        "--held-out",
        type=int,
        choices=generator.HELD_OUT_SIZES,
        default=None,
        help="Draw the held-out set too, at this size; once a run names it, only a draw that rebuilds it is kept",
    )
    playing = commands.add_parser(
        "play",
        help="Play a drawn set in process with the scripted or the baseline's models, and grade it",
    )
    playing.add_argument("--set", dest="set_name", choices=PLAYED, default="regression")
    playing.add_argument("--models", choices=runs.MODELS, default="scripted")
    running = commands.add_parser(
        "run",
        help="Play a drawn set end to end against a deployed stack, and grade it",
    )
    running.add_argument("--set", dest="set_name", choices=PLAYED, default="regression")
    running.add_argument("--stack", type=Path, required=True, help="Terraform outputs")
    running.add_argument("--docs", type=Path, default=SET_DOCS)
    running.add_argument("--situation", action="append", default=[])
    running.add_argument("--language", action="append", default=[])
    running.add_argument("--limit", type=int, default=None)
    running.add_argument("--parallel", type=int, default=2)
    cleaning = commands.add_parser(
        "cleanup",
        help="Delete the test users a stopped run left in the evaluation group",
    )
    cleaning.add_argument("--stack", type=Path, required=True, help="Terraform outputs")
    cleaning.add_argument("--run", default=None)
    commands.add_parser(
        "disagreements", help="Regenerate the disagreement log's page from its entries"
    )
    commands.add_parser(
        "index", help="Regenerate the run index from the committed manifests"
    )
    reporting = commands.add_parser(
        "report",
        help="Write the results page from the reported runs' stored grades, with the baseline and the judge where given",
    )
    reporting.add_argument(
        "--run",
        action="append",
        default=[],
        type=Path,
        required=True,
        help="A run of the set, repeatable",
    )
    reporting.add_argument("--baseline", type=Path, default=None)
    reporting.add_argument(
        "--judged",
        action="append",
        default=[],
        type=Path,
        help="A judge run over one of the runs, repeatable",
    )
    reporting.add_argument(
        "--agreement", type=Path, default=None, help="The agreement report's JSON"
    )
    reporting.add_argument(
        "--labels", type=Path, default=None, help="The relabel agreement's JSON"
    )
    reporting.add_argument("--docs", type=Path, default=SET_DOCS)
    reporting.add_argument("--out", type=Path, default=report.RESULTS)
    reporting.add_argument("--page", type=Path, default=report.PAGE)
    checking = commands.add_parser(
        "language",
        help="Run the real prompts over the development paraphrases and answers, and report each language",
    )
    checking.add_argument("--env-file", type=Path, default=Path(".env"))
    checking.add_argument("--reports", type=Path, default=language.REPORTS)
    checking.add_argument("--page", type=Path, default=language.PAGE)
    checking.add_argument("--parallel", type=int, default=4)
    checking.add_argument(
        "--only",
        action="append",
        default=[],
        help="A family to check alone, repeatable",
    )
    model_commands.add(commands)
    args = parser.parse_args(argv)
    if args.command in model_commands.COMMANDS:
        return model_commands.main(args, tree())
    if args.command == "language":
        try:
            check_language(
                args.env_file, args.reports, args.page, args.parallel, args.only
            )
        except ModelKeyError as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1
        return 0
    if args.command == "index":
        print(f"{runs.index()} reported runs in {runs.INDEX_PAGE}")
        return 0
    if args.command == "report":
        try:
            write_report(args)
        except (LockError, report.ReportError) as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1
        return 0
    if args.command == "disagreements":
        found = disagreements.problems(disagreements.load())
        for problem in found:
            print(f"Error: {problem}", file=sys.stderr)
        if not found:
            disagreements.write()
        return 1 if found else 0
    try:
        if args.command == "play":
            play(args.lock, args.data_dir, args.set_name, args.models)
        elif args.command == "run":
            selection = {
                "situations": args.situation,
                "languages": args.language,
                "limit": args.limit,
            }
            run(
                args.data_dir,
                args.docs,
                args.set_name,
                args.stack,
                selection,
                args.parallel,
            )
        elif args.command == "cleanup":
            deleted = endtoend.cleanup(args.stack, args.run)
            print(f"deleted {deleted} test users")
        else:
            generate(args.lock, args.data_dir, args.docs, args.seed, args.held_out)
    except (
        LockError,
        runs.PlayError,
        deployed.StackError,
        endtoend.RunError,
        heldout.HeldOutError,
    ) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Command line for make analysis.
"""

import argparse
import sys
from pathlib import Path

from faro.analysis import cards_report, selection_report, traffic_report
from faro.analysis.capacity import project
from faro.analysis.cards import card_support
from faro.analysis.catalog import TABLES
from faro.analysis.profile import profile
from faro.analysis.report import write
from faro.analysis.selection import select
from faro.analysis.source import AnalysisError, check_local
from faro.analysis.traffic import traffic
from faro.dataset.lock import LockError, read_lock
from faro.dataset.snapshot import snapshot_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m faro.analysis", description=__doc__
    )
    parser.add_argument("--lock", type=Path, default=Path("dataset.lock"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("docs/analysis"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "profile",
        help="Profile the data quality of the pinned snapshot into profiling.md and profiling.json",
    )
    commands.add_parser(
        "select",
        help="Profile the snapshot, then compute ADR-0003's gates into selection.md and selection.json",
    )
    commands.add_parser(
        "cards",
        help="Profile the snapshot, then analyze the development customers' cards into card-support.md "
        "and card-support.json",
    )
    commands.add_parser(
        "traffic",
        help="Profile the snapshot, then measure the development customers' traffic and project the "
        "agent's capacity into traffic.md and traffic.json",
    )
    commands.add_parser(
        "all",
        help="Profile the snapshot once, then write the selection, the card support analysis, and the "
        "traffic analysis",
    )
    args = parser.parse_args(argv)

    def log(line: str) -> None:
        print(line, file=sys.stderr)

    try:
        lock = read_lock(args.lock)
        root = snapshot_dir(args.data_dir, lock.snapshot_id)
        check_local(lock, root)
        profiled = profile(lock, root, TABLES, log=log)
        written: tuple[Path, ...] = write(profiled, args.out)
        if args.command != "profile":
            business_date, as_of = profiled.business_date, profiled.as_of
            if business_date is None or as_of is None:
                raise AnalysisError(
                    "the profile found no business date to read the snapshot as of"
                )
            if args.command in ("select", "all"):
                written += selection_report.write(
                    select(lock, root, TABLES, business_date, as_of, log=log), args.out
                )
            if args.command in ("cards", "all"):
                written += cards_report.write(
                    card_support(lock, root, TABLES, business_date, as_of, log=log),
                    args.out,
                )
            if args.command in ("traffic", "all"):
                measured = traffic(lock, root, TABLES, business_date, as_of, log=log)
                written += traffic_report.write(measured, project(measured), args.out)
    except (AnalysisError, LockError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print("Wrote " + ", ".join(str(path) for path in written))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
M-01 to M-05 from graded cases (ADR-0005, Reporting): each rate with its sample size and a Wilson interval, eligible
cases resolved without a transfer, transfers right, missed, and unnecessary, unsafe outcomes by kind with the rule of
three, efficiency with "not defined" wherever the run can't measure it, and pass^k over repeated runs.
"""

import inspect
import re
from typing import Any

from faro.evaluation import grader, metrics


def case(
    n: int,
    final: str = "answer",
    handoff: str | None = None,
    language: str = "es",
    situation: str = "status.one_card",
) -> dict[str, Any]:
    turn: dict[str, Any] = {"decisions": [{"outcome_class": final}]}
    if handoff is not None:
        turn["handoff"] = {"trigger": handoff}
    return {
        "case_id": f"{n:016x}",
        "situation": situation,
        "language": language,
        "expected": {"turns": [turn]},
    }


def grade(
    passed: bool = True,
    transferred: bool = False,
    decided: tuple[str, ...] = ("answer",),
    failures: tuple[str, ...] = (),
    safety: tuple[str, ...] = (),
    diverged_at: int | None = None,
) -> dict[str, Any]:
    return {
        "passed": passed,
        "transferred": transferred,
        "decided": list(decided),
        "failures": [{"check": c} for c in failures],
        "safety": [{"check": c} for c in safety],
        "diverged_at": diverged_at,
        "error": None,
    }


TOTALS = {
    "turns": 8,
    "model_calls": 12,
    "input_tokens": 0,
    "output_tokens": 0,
    "cost_usd": 0.0,
}
WORKLOAD = {"turns": 8, "repeats": 1, "parallelism": 1}


def test_a_rate_has_its_sample_size_and_wilson_interval() -> None:
    assert metrics.rate(9, 10) == {
        "count": 9,
        "cases": 10,
        "rate": 0.9,
        "interval": [0.5958, 0.9821],
    }
    assert metrics.wilson(0, 10) == [0.0, 0.2775]
    assert metrics.rate(0, 0)["rate"] == metrics.rate(0, 0)["interval"] == "not defined"


def test_m01_counts_eligible_cases_resolved_over_all_and_by_outcome_class() -> None:
    graded = [
        (case(1), grade()),
        (case(2), grade(passed=False, failures=("fact",))),
        (case(3, "decline"), grade(decided=("decline",))),
        (
            case(4, "hand_off", "required"),
            grade(transferred=True, decided=("hand_off",)),
        ),
    ]

    found = metrics.compute(graded, TOTALS, WORKLOAD)["M-01"]

    assert (found["count"], found["cases"], found["eligible"]) == (2, 4, 3)
    assert found["attempted"]["count"] == 3
    assert found["by_outcome_class"]["answer"]["count"] == 1
    assert found["by_outcome_class"]["answer"]["cases"] == 2
    assert found["by_outcome_class"]["decline"]["count"] == 1
    assert found["by_language"]["pt"]["cases"] == 0


def test_m03_counts_transfers_right_missed_and_unnecessary() -> None:
    graded = [
        (case(1, "hand_off", "required"), grade(transferred=True)),
        (case(2, "hand_off", "required"), grade(passed=False, diverged_at=1)),
        (
            case(3, "hand_off", "accepted_offer"),
            grade(passed=False, transferred=True, failures=("handoff.queue",)),
        ),
        (case(4), grade(passed=False, transferred=True, diverged_at=1)),
    ]

    found = metrics.compute(graded, TOTALS, WORKLOAD)

    assert (found["M-03"]["count"], found["M-03"]["cases"]) == (1, 3)
    assert (found["M-03"]["missed"]["count"], found["M-03"]["missed"]["cases"]) == (
        1,
        3,
    )
    assert (
        found["M-03"]["unnecessary"]["count"]
        == found["M-03"]["unnecessary"]["cases"]
        == 1
    )
    assert found["M-02"]["count"] == 1


def test_m04_counts_unsafe_cases_by_kind_and_bounds_zero_by_the_rule_of_three() -> None:
    graded = [
        (case(1), grade(passed=False, safety=("withheld_status",))),
        (case(2), grade(passed=False, safety=("blocked_twice",))),
        (case(3), grade(passed=False, failures=("extra_figure",))),
        (case(4, "hand_off", "required"), grade(passed=False, diverged_at=1)),
        (case(5, situation="access.direct.other"), grade(safety=("access.denied",))),
    ]
    safe = [(case(n), grade()) for n in range(1, 11)]

    found = metrics.compute(graded, TOTALS, WORKLOAD)["M-04"]
    none = metrics.compute(safe, TOTALS, WORKLOAD)["M-04"]

    assert (found["count"], found["cases"]) == (5, 5)
    assert {k: v["count"] for k, v in found["by_kind"].items()} == {
        "disclosure": 2,
        "action": 1,
        "incorrect": 2,
        "unverified": 0,
    }
    assert found["bound"] == "not defined"
    assert (none["count"], none["bound"]) == (0, 0.3)


def test_access_cases_count_in_m04_alone() -> None:
    graded = [
        (case(1), grade()),
        (case(2, situation="access.direct.own"), grade(decided=())),
    ]

    found = metrics.compute(graded, TOTALS, WORKLOAD)

    assert found["M-01"]["cases"] == found["M-02"]["cases"] == 1
    assert found["M-04"]["cases"] == 2


def test_m05_says_not_defined_for_what_the_run_cant_measure() -> None:
    failed = [(case(1), grade(passed=False, failures=("fact",)))]
    counted = {**TOTALS, "cost_usd": None}

    in_process = metrics.compute(failed, TOTALS, WORKLOAD)["M-05"]
    unpriced = metrics.compute(failed, counted, WORKLOAD)["M-05"]

    assert in_process["latency_ms"] == "not defined"
    assert in_process["cost_usd"] == {
        "models": 0.0,
        "aws": "not defined",
        "per_attempted_case": 0.0,
        "per_resolution": "not defined",
    }
    assert in_process["per_case"]["turns"] == 8
    assert unpriced["cost_usd"]["per_attempted_case"] == "not defined"
    assert metrics.compute(failed, TOTALS, WORKLOAD)["by_segment"] == "not defined"


def test_every_safety_check_the_grader_names_has_a_kind() -> None:
    source = inspect.getsource(grader.safety) + inspect.getsource(
        grader.access_findings
    )
    named = set(re.findall(r'finding\(\s*[^,]+,\s*"([a-z_.]+)"', source))

    assert named
    assert named <= set(metrics.KINDS)


def test_repeated_runs_pass_only_when_every_run_does_and_are_unsafe_when_any_is() -> (
    None
):
    first = [(case(1), grade()), (case(2), grade())]
    second = [
        (case(1), grade()),
        (case(2), grade(passed=False, safety=("internal_flag",))),
    ]

    found = metrics.repeated([first, second])

    assert (found["runs"], found["cases"]) == (2, 2)
    assert found["M-01"]["pass_all"]["count"] == 1
    assert found["M-01"]["spread"] == [0.5, 1.0]
    assert found["M-04"]["count"] == 1

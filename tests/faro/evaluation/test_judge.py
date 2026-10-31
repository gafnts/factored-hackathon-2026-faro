"""
The judge reads a reply's turn from stored evidence, asks only the questions the turn calls for in one structured,
batched call, sends a failed request again, records every attempt with what the manifest needs, and leaves the
verdict and the confidence that sends a judgment to a person to code (ADR-0005, Grading, as amended on 2026-10-02;
EVL-10). No test calls the API.
"""

import json
import re
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anthropic
import pytest

from faro.evaluation import __main__ as cli
from faro.evaluation import judge, rubric
from faro.evaluation.judge import Item
from faro.evaluation.rubric import Facts

RUBRIC = rubric.load()
CUSTOM_ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def entry(turn: int, seq: int, kind: str, **fields: Any) -> dict[str, Any]:
    return {
        "entry_key": f"2026-10-02T10:00:0{turn}.000Z#turn-{turn}#{seq:04d}",
        "turn_id": f"turn-{turn}",
        "kind": kind,
        **fields,
    }


def decided(
    turn: int, seq: int, outcome: str, awaiting: str, rules: list[str]
) -> dict[str, Any]:
    return entry(
        turn,
        seq,
        "decision",
        request_label="block_card",
        pending_labels=[],
        outcome_class=outcome,
        awaiting=awaiting,
        rules=rules,
        language="es",
    )


def evidence() -> dict[str, Any]:
    """
    A block cancelled with the control, which POL-39 hands off: two turns, the second filing the case.
    """
    record = [
        entry(
            1,
            0,
            "turn_opened",
            input={"kind": "message", "text": "Vi un cargo extraño."},
        ),
        decided(1, 3, "block", "confirm_control", ["POL-35", "POL-36", "POL-31"]),
        entry(
            1,
            2,
            "reply",
            language="es",
            fixed_texts=["confirm_prompt"],
            text="Confirme con el botón.",
        ),
        entry(
            2,
            0,
            "turn_opened",
            input={
                "kind": "resume",
                "resume": {"kind": "cancel", "confirmation_id": "c"},
            },
        ),
        entry(2, 1, "confirmation", to="cancelled", cause="control"),
        entry(2, 2, "handoff", handoff_id="h-1", status="filed"),
        entry(
            2,
            3,
            "reply",
            language="es",
            fixed_texts=[],
            text="No bloqueé su tarjeta. Referencia AB12-CD34.",
        ),
        decided(2, 4, "hand_off", "none", ["POL-36", "POL-39", "POL-45"]),
    ]
    case = {
        "kind": "case",
        "status": "filed",
        "handoff_id": "h-1",
        "record": {
            "turns": [
                "2026-10-02T10:00:01.000Z#turn-1",
                "2026-10-02T10:00:02.000Z#turn-2",
            ]
        },
        "payload": {
            "reason_code": "unrecognized_charge",
            "queue": "dispute_intake",
            "priority": "urgent",
            "request": {
                "label": "block_card",
                "summary": "Pide bloquear por un cargo.",
            },
            "customer_statements": ["Vio un cargo extraño."],
            "unresolved_questions": [],
            "verified_facts": [
                {
                    "subject": "card",
                    "field": "last_four",
                    "value": "4379",
                    "id": "PRD-X",
                    "evidence": "e",
                }
            ],
            "actions": [
                {
                    "action": "block_card",
                    "outcome": "declined_by_customer",
                    "reason": "unrecognized_charge",
                    "card_id": "PRD-X",
                }
            ],
        },
    }
    return {
        "case_id": "0123456789abcdef",
        "record": record,
        "cases": [case],
        "error": None,
    }


def test_items_hold_each_reply_with_its_turn_and_the_facts_that_pick_questions() -> (
    None
):
    first, second = judge.items(evidence())

    assert (first.item_id, first.turn, first.language) == (
        "0123456789abcdef-t01",
        1,
        "es",
    )
    assert first.earlier == ()
    assert first.facts == Facts(rules=frozenset({"POL-35", "POL-36", "POL-31"}))
    assert first.handoff is None
    assert second.sent == "[pressed the cancel button]"
    assert second.earlier == (("Vi un cargo extraño.", "Confirme con el botón."),)
    assert second.facts.handoff_filed and second.facts.confirmation_ended
    assert second.decisions[0]["outcome"] == "hand_off"
    assert second.handoff is not None
    assert second.handoff["customer_sent"] == [
        "Vi un cargo extraño.",
        "[pressed the cancel button]",
    ]
    assert "id" not in second.handoff["verified_facts"][0]
    assert "card_id" not in second.handoff["actions"][0]
    assert all(CUSTOM_ID.match(i.item_id) for i in (first, second))
    assert Item.from_json(json.loads(json.dumps(second.to_json()))) == second


def test_a_run_s_evidence_is_read_in_either_mode(tmp_path: Path) -> None:
    played = tmp_path / "in_process"
    played.mkdir()
    (played / "evidence.jsonl").write_text(
        json.dumps(evidence()) + "\n", encoding="utf-8"
    )
    ended = tmp_path / "end_to_end" / "cases"
    ended.mkdir(parents=True)
    failed = {**evidence(), "error": "harness: Timeout"}
    for n, e in enumerate([evidence(), failed], 1):
        (ended / f"{n:04d}.json").write_text(
            json.dumps({"evidence": e, "grade": {}}), encoding="utf-8"
        )

    assert len(judge.run_items(played)) == 2
    assert len(judge.run_items(ended.parent)) == 2


def test_a_request_asks_only_the_turn_s_questions_in_one_structured_call() -> None:
    _, second = judge.items(evidence())
    system = judge.system_prompt(RUBRIC)
    made = judge.request(second, RUBRIC, system)
    params = made["params"]
    asked = [q.id for q in RUBRIC.applicable(second.facts)]

    assert made["custom_id"] == second.item_id
    assert params["model"] == judge.MODEL == "claude-opus-5-5"
    assert params["output_config"]["effort"] == judge.EFFORT
    assert params["output_config"]["format"]["schema"]["required"] == asked
    assert params["system"][0]["text"] == system
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "temperature" not in params and "thinking" not in params
    content = params["messages"][0]["content"]
    for tag in ("earlier", "sent", "decision", "reply", "handoff", "questions"):
        assert f"<{tag}>" in content
    assert ", ".join(asked) in content
    for q in RUBRIC.questions:
        assert f"### {q.id} " in system
    assert "{questions}" not in system


def answers(item: Item, confidence: str = "high", **given: Any) -> dict[str, Any]:
    found = {}
    for q in RUBRIC.applicable(item.facts):
        default = {"choice": "es_usted", "yes_no": "yes", "score": 4}[q.type]
        found[q.id] = {
            "reason": "Visto.",
            "answer": given.get(q.id, default),
            "confidence": confidence,
        }
    return found


def succeeded(body: Any, stop: str = "end_turn") -> dict[str, Any]:
    return {
        "type": "succeeded",
        "message": {
            "model": judge.MODEL,
            "stop_reason": stop,
            "content": [
                {"type": "thinking", "thinking": ""},
                {"type": "text", "text": json.dumps(body)},
            ],
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 2000,
            },
        },
    }


class FakeBatches:
    def __init__(self, rounds: list[dict[str, dict[str, Any]]]) -> None:
        self.rounds = rounds
        self.sent: list[list[dict[str, Any]]] = []
        self.polled = 0

    def create(self, requests: list[dict[str, Any]]) -> str:
        self.sent.append(requests)
        return f"batch-{len(self.sent)}"

    def status(self, batch_id: str) -> str:
        self.polled += 1
        return "ended" if self.polled % 2 == 0 else "in_progress"

    def results(self, batch_id: str) -> Iterable[tuple[str, dict[str, Any]]]:
        return list(self.rounds[len(self.sent) - 1].items())


def five() -> list[Item]:
    _, second = judge.items(evidence())
    return [replace(second, item_id=f"item-{n}") for n in range(5)]


def test_failed_requests_go_again_and_every_attempt_is_recorded() -> None:
    found = five()
    ok = answers(found[0])
    first = {
        "item-0": succeeded(ok),
        "item-1": {
            "type": "errored",
            "error": {"type": "error", "error": {"type": "api_error"}},
        },
        "item-2": {"type": "expired"},
        "item-3": succeeded({"language": "nonsense"}),
        "item-4": {
            "type": "errored",
            "error": {"type": "error", "error": {"type": "invalid_request_error"}},
        },
    }
    second = {i: succeeded(ok) for i in ("item-1", "item-2", "item-3")}
    batches = FakeBatches([first, second])
    slept: list[float] = []

    judged = judge.judge(found, RUBRIC, batches, sleep=slept.append)

    assert [len(s) for s in batches.sent] == [5, 3]
    assert judged.batches == ["batch-1", "batch-2"]
    assert slept == [judge.POLL_S, judge.POLL_S]
    assert judged.outcomes == {
        "item-0": "ok",
        "item-1": "ok",
        "item-2": "ok",
        "item-3": "ok",
        "item-4": "error",
    }
    assert len(judged.attempts) == 8
    third = [a for a in judged.attempts if a["item_id"] == "item-3"]
    assert [(a["attempt"], a["outcome"]) for a in third] == [
        (1, "invalid_output"),
        (2, "ok"),
    ]
    done = third[-1]
    assert done["model_requested"] == done["model_returned"] == judge.MODEL
    assert done["prompt_version"] == judge.prompt_version(RUBRIC)
    assert done["rubric"] == RUBRIC.version
    assert done["usage"] == {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_tokens": 2000,
        "cache_write_tokens": 0,
    }
    assert done["cost_usd"] == pytest.approx(
        (1000 * 4 + 500 * 20 + 2000 * 0.2) / 2 / 1_000_000
    )
    assert [a["cost_usd"] for a in judged.attempts if a["item_id"] == "item-4"] == [
        None
    ]


def test_a_refusal_or_a_cut_answer_is_never_read_as_one() -> None:
    found = five()[:2]
    rounds = [
        {
            "item-0": succeeded(answers(found[0]), stop="refusal"),
            "item-1": succeeded(answers(found[1]), stop="max_tokens"),
        },
        {"item-1": succeeded(answers(found[1]), stop="max_tokens")},
        {"item-1": succeeded(answers(found[1]), stop="max_tokens")},
    ]

    judged = judge.judge(found, RUBRIC, FakeBatches(rounds), sleep=lambda s: None)

    assert judged.outcomes == {"item-0": "refusal", "item-1": "invalid_output"}
    assert judged.answers == {}
    assert len(judged.attempts) == 1 + judge.ATTEMPTS


def test_code_gives_the_verdict_and_sends_unsure_or_missing_judgments_to_a_person() -> (
    None
):
    found = five()[:3]
    rounds = [
        {
            "item-0": succeeded(answers(found[0], language="es_tu", clear=2)),
            "item-1": succeeded(answers(found[1], confidence="low")),
            "item-2": {
                "type": "errored",
                "error": {"type": "error", "error": {"type": "invalid_request_error"}},
            },
        }
    ]
    judged = judge.judge(found, RUBRIC, FakeBatches(rounds), sleep=lambda s: None)

    kept = {j["item_id"]: j for j in judge.judgments(found, RUBRIC, judged)}

    first = kept["item-0"]["questions"]
    assert first["language"]["passes"] is False
    assert first["clear"]["passes"] is False
    assert first["decision"]["passes"] is True
    assert not any(q["by_hand"] for q in first.values())
    assert all(q["by_hand"] for q in kept["item-1"]["questions"].values())
    missing = kept["item-2"]
    assert missing["outcome"] == "error"
    assert all(
        q == {"answer": None, "passes": None, "by_hand": True}
        for q in missing["questions"].values()
    )


def test_keep_writes_the_results_and_a_manifest_that_hashes_them(
    tmp_path: Path,
) -> None:
    found = five()[:1]
    judged = judge.judge(
        found,
        RUBRIC,
        FakeBatches([{"item-0": succeeded(answers(found[0]))}]),
        sleep=lambda s: None,
    )
    times = (datetime(2026, 10, 2, tzinfo=UTC), datetime(2026, 10, 2, 1, tzinfo=UTC))

    manifest = judge.keep(
        tmp_path / "run",
        found,
        RUBRIC,
        judged,
        judge.source(None, None, found),
        {"commit": "c", "clean": True},
        times,
    )

    assert set(manifest["results"]) == {
        "items.jsonl",
        "attempts.jsonl",
        "judgments.jsonl",
    }
    block = manifest["judge"]
    assert block["model_requested"] == judge.MODEL and block["model_returned"] == [
        judge.MODEL
    ]
    assert block["rubric"] == {"version": RUBRIC.version, "sha256": RUBRIC.sha256}
    assert (
        block["price"]["on"] == judge.PRICED_ON and block["price"]["batch_share"] == 0.5
    )
    assert block["seed"] is None and block["settings"]["batch"] is True
    assert manifest["totals"]["cost_usd"] == pytest.approx(
        judged.attempts[0]["cost_usd"]
    )
    assert judge.read_items(tmp_path / "run" / "items.jsonl") == found
    written = json.loads(
        (tmp_path / "run" / "manifest.json").read_text(encoding="utf-8")
    )
    assert written == manifest


def test_a_smoke_test_covers_every_question_it_can_first() -> None:
    first, second = judge.items(evidence())

    assert judge.chosen([first, second], RUBRIC, 1) == [first]
    assert judge.chosen([first, second], RUBRIC, 5) == [first, second]


def test_the_estimate_prices_the_items_without_a_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "evidence.jsonl").write_text(json.dumps(evidence()) + "\n", encoding="utf-8")

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("an estimate makes no call")

    monkeypatch.setattr(anthropic, "Anthropic", refuse)

    assert cli.main(["judge", "--run", str(run), "--estimate"]) == 0
    printed = capsys.readouterr().out
    assert printed.startswith("2 replies:") and "USD" in printed
    priced = judge.estimate(judge.run_items(run), RUBRIC)
    assert priced["cost_usd"] > 0
    assert priced["output_tokens"] >= 2 * judge.THINKING_PER_REPLY


def test_a_missing_key_stops_the_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "evidence.jsonl").write_text(json.dumps(evidence()) + "\n", encoding="utf-8")

    assert (
        cli.main(["judge", "--run", str(run), "--env-file", str(tmp_path / "none.env")])
        == 1
    )
    assert "Error:" in capsys.readouterr().err


class Answering:
    """
    A batch API that answers every request at once from its own schema: each question's first allowed answer.
    """

    def __init__(self, client: Any) -> None:
        self.requests: list[dict[str, Any]] = []

    def create(self, requests: list[dict[str, Any]]) -> str:
        self.requests = requests
        return "batch-1"

    def status(self, batch_id: str) -> str:
        return "ended"

    def results(self, batch_id: str) -> Iterable[tuple[str, dict[str, Any]]]:
        for made in self.requests:
            schema = made["params"]["output_config"]["format"]["schema"]
            body = {
                q: {
                    "reason": "r",
                    "answer": part["properties"]["answer"]["enum"][0],
                    "confidence": "high",
                }
                for q, part in schema["properties"].items()
            }
            yield made["custom_id"], succeeded(body)


def test_the_command_judges_a_run_and_keeps_what_it_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "evidence.jsonl").write_text(json.dumps(evidence()) + "\n", encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-test\n", encoding="utf-8")
    monkeypatch.setattr(judge, "AnthropicBatches", Answering)
    out = tmp_path / "judged"

    code = cli.main(
        ["judge", "--run", str(run), "--env-file", str(env), "--out", str(out)]
    )

    printed = capsys.readouterr().out
    assert code == 0
    assert printed.startswith("judged 2 replies in 2 attempts")
    assert "sk-test" not in printed
    (kept,) = out.iterdir()
    manifest = json.loads((kept / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["outcomes"] == {"ok": 2}
    assert manifest["source"]["run"] == "run"
    assert manifest["code"]["commit"]

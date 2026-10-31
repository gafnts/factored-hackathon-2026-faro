"""
The scripted customer (ADR-0005, The scripted customer): code, not a model, that plays a case's script while reacting to
what the agent waits for. It reads the turn's last decision entry, never the reply's prose, and the interrupt the run
ended at, whose controls it presses; it answers from the script, says it doesn't know when the script holds no answer,
and stops once nothing is left to send, or at twice the turns the oracle expects. The in-process player and the harness
share it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

ASKED = ("card", "reason", "transaction")
CONTROLS = {
    "confirm_control": ("block_confirmation", "confirmation_id"),
    "handoff_control": ("handoff_offer", "offer_id"),
}


class ScriptError(ValueError):
    """
    A case the customer can't play as written, which is the player's error, never the agent's.
    """


@dataclass(frozen=True)
class Send:
    """
    What the customer sends in a turn, under the case's name for it (a turn's `sends`): a message's text, or a control's
    answer as the chat's resume entry.
    """

    sends: str
    text: str | None = None
    text_id: str | None = None
    resume: dict[str, Any] | None = None


class Customer:
    def __init__(self, case: Mapping[str, Any]) -> None:
        script = case["script"]
        if script.get("actions"):
            raise ScriptError("a case with harness actions plays only end to end")
        if not script["messages"]:
            raise ScriptError("the script opens with no message")
        self.messages = list(script["messages"])
        self.answers: Mapping[str, Any] = script["answers"]
        self.limit = 2 * len(case["expected"]["turns"])
        self.turns = 0
        self.typed = False
        self.asided = False

    def first(self) -> Send:
        return self._sent(self._message())

    def next(
        self, awaiting: str | None, interrupt: Mapping[str, Any] | None
    ) -> Send | None:
        """
        awaiting is the turn's last decision entry's, None when the turn recorded no decision (a refused request, or an
        error), which ends the conversation; interrupt is the one the run finished at, if any.
        """
        if awaiting is None or self.turns >= self.limit:
            return None
        if awaiting in ASKED:
            # A message that doesn't answer the question, sent once before the answer (POL-06).
            aside = self.answers.get("aside")
            if aside is not None and not self.asided:
                self.asided = True
                return self._sent(Send("aside", aside["text"], aside["id"]))
            return self._sent(self._answer(awaiting))
        if awaiting == "confirm_control":
            typed = self.answers.get("typed_yes")
            if typed is not None and not self.typed:
                self.typed = True
                return self._sent(Send("typed_yes", typed["text"], typed["id"]))
        if awaiting in CONTROLS:
            choice = self.answers.get(awaiting, "ignore")
            if choice != "ignore":
                return self._sent(self._press(awaiting, choice, interrupt))
        return self._sent(self._message()) if self.messages else None

    def _sent(self, send: Send) -> Send:
        self.turns += 1
        return send

    def _message(self) -> Send:
        message = self.messages.pop(0)
        return Send("message", message["text"], message["id"])

    def _answer(self, asked: str) -> Send:
        sends = asked if asked in self.answers else "dont_know"
        answer = self.answers.get(sends)
        if answer is None:
            raise ScriptError(
                f"the script holds no answer to {asked}, nor one that doesn't know"
            )
        return Send(sends, answer["text"], answer["id"])

    def _press(
        self, awaiting: str, choice: str, interrupt: Mapping[str, Any] | None
    ) -> Send:
        kind, key = CONTROLS[awaiting]
        controls = (interrupt or {}).get("metadata", {}).get("controls", [])
        control = next((c for c in controls if c["kind"] == kind), None)
        if interrupt is None or control is None:
            raise ScriptError(
                f"the turn awaits the {kind} control, which the run didn't show"
            )
        return Send(
            choice,
            resume={
                "interruptId": interrupt["id"],
                "status": "resolved",
                "payload": {"kind": choice, key: control[key]},
            },
        )

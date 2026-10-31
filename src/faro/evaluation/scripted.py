"""
Models scripted from a case (ADR-0005, The development regression set: scripted model outputs, no provider). Each call
gets the output a model that read the message correctly would give: the router the family's labels, the extraction
the family's fields with the case's slots, or an answer's fields from the record the customer means, the choice the
listed transactions that fit what the customer said, and a reply that writes each placeholder once, as the prompt lays
them out. So a case played
with them tests the graph's control logic, not a model. A text that isn't the case's is kept in `unplaced` and fails
the call, and the player reports the case as its own error, never the agent's.
"""

import re
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import Runnable, RunnableLambda

from faro.agent.check import LISTS
from faro.agent.models import (
    REASON_CODES,
    HandoffText,
    RequestDetails,
    RouterOutput,
    TransactionChoice,
)
from faro.evaluation.families import Answer, Family
from faro.masking import mask

MODEL = "scripted"
USAGE = {
    "input_tokens": 0,
    "output_tokens": 0,
    "total_tokens": 0,
    "input_token_details": {"cache_read": 0, "cache_creation": 0},
}
EMPTY = dict.fromkeys(RequestDetails.model_fields)
WHEN = {"today": 0, "yesterday": 1, "day_before": 2}
REASONS = {
    "reason_lost": "lost",
    "reason_stolen": "stolen",
    "reason_unrecognized_charge": "unrecognized_charge",
    "reason_customer_request": "customer_request",
}
# The families and the answers hold POL-35's codes; the model says its own name for any other reason.
MODEL_REASONS = {code: value for value, code in REASON_CODES.items()}
PLACEHOLDER = re.compile(r"^- (\{[a-z_.]+\}):", re.MULTILINE)
# The prompt puts a decline's reason on the line under its transaction, so the two read as one list.
UNDER = (
    "{transaction}\n\n{transaction.meaning}",
    "{transaction}\n{transaction.meaning}",
)
LISTED = re.compile(r"^(\d+)\. ")
HANDOFF_TEXT = {
    "summary": "El cliente pidió ayuda con su tarjeta por el chat.",
    "customer_statements": ["El cliente escribió al chat sobre su tarjeta."],
    "unresolved_questions": [],
}


class UnplacedError(LookupError):
    pass


def spoken(family: Family, message_id: str) -> str:
    """
    The language a model would say a family's message is in: other for a third language, unclear for a message the
    family marks so, else the language it is filed under.
    """
    if family.kind == "third_language":
        return "other"
    message = next(m for m in family.messages if m.id == message_id)
    return message.language if message.clear else "unclear"


def raw() -> AIMessage:
    return AIMessage(
        content="{}", usage_metadata=USAGE, response_metadata={"model_name": MODEL}
    )


def laid_out(written: list[str]) -> str:
    """
    The facts in one sentence, then each list on a line of its own, a decline's reason right under its transaction.
    """
    told = [name for name in written if name.strip("{}") not in LISTS]
    paragraphs = [f"{', '.join(told)}."] if told else []
    paragraphs += [name for name in written if name.strip("{}") in LISTS]
    return "\n\n".join(paragraphs).replace(*UNDER)


def structured(parsed: Any) -> dict[str, Any]:
    return {"raw": raw(), "parsed": parsed, "parsing_error": None}


def listed(context: str) -> list[tuple[int, str, Decimal, str]]:
    """
    The transactions the choice reads, as the graph lists them: number, date, amount, and merchant.
    """
    found = []
    for line in context.splitlines():
        number = LISTED.match(line)
        if number is None:
            continue
        rest = line.rsplit(", ", 2)[0]
        head, _, amount, merchant = rest.split(", ", 3)
        on = head.split(" ")[2]
        found.append(
            (int(number.group(1)), on, Decimal(amount.rsplit(" ", 1)[0]), merchant)
        )
    return found


class ScriptedModels:
    # What the record names on every call (models.Factory): no provider runs.
    provider: str | None = None
    model = MODEL

    def __init__(
        self,
        case: Mapping[str, Any],
        families: Mapping[str, Family],
        answers: Mapping[str, Answer],
        items: Iterable[Mapping[str, Any]],
    ) -> None:
        script = case["script"]
        self.slots: Mapping[str, str] = script.get("slots", {})
        means = script["means"]
        held = list(items)
        meta = next(i for i in held if i["pk"] == "META")
        self.business_date = date.fromisoformat(meta["clock"]["business_date"])
        own = {i["sk"]: i for i in held if i["pk"] == case["customer_id"]}
        # A fixture is one of the customer's records, keyed as the export keys it.
        for f in case["fixtures"]:
            key = (
                f"CARD#{f['card_id']}"
                if f["kind"] == "card"
                else f"TXN#{f['transaction_id']}"
            )
            own[key] = f
        self.card = (
            own.get(f"CARD#{means['product_id']}") if "product_id" in means else None
        )
        self.transaction = (
            own.get(f"TXN#{means['transaction_id']}")
            if "transaction_id" in means
            else None
        )
        # What each text is, and the language a model reading it would say (POL-50, POL-51).
        self.said: dict[str, tuple[Family | str, str]] = {}
        for message in script["messages"]:
            family = families[message["id"].split("/")[0]]
            self.said[mask(message["text"])] = (family, spoken(family, message["id"]))
        for answer in script["answers"].values():
            if isinstance(answer, dict):
                answer_id, language = answer["id"].split("/")
                self.said[mask(answer["text"])] = (answers[answer_id].kind, language)
        self.unplaced: list[str] = []

    def __call__(self, purpose: str) -> Runnable[Any, Any]:
        chosen = {
            "route": self.route,
            "extract": self.extract,
            "choose": self.choose,
            "handoff_text": self.handoff_text,
        }.get(purpose, self.reply)
        return RunnableLambda(chosen)

    def placed(self, messages: list[BaseMessage]) -> tuple[Family | str, str]:
        text = messages[-1].text
        found = self.said.get(text)
        if found is None:
            self.unplaced.append(text)
            raise UnplacedError("a text the case doesn't hold")
        return found

    async def route(self, messages: list[BaseMessage]) -> dict[str, Any]:
        said, language = self.placed(messages)
        if isinstance(said, str):
            return structured(
                RouterOutput.model_validate(
                    {
                        "requests": [],
                        "has_request": False,
                        "complaint": False,
                        "language": language,
                    }
                )
            )
        return structured(
            RouterOutput.model_validate(
                {
                    "requests": list(said.labels),
                    "has_request": bool(said.labels),
                    "complaint": said.complaint,
                    "language": language,
                }
            )
        )

    async def extract(self, messages: list[BaseMessage]) -> dict[str, Any]:
        said, language = self.placed(messages)
        details: dict[str, Any] = {**EMPTY, "language": language}
        if isinstance(said, Family):
            details |= said.extract
            if "last_four" in said.slots:
                details["last_four"] = self.slots["last_four"]
        elif said == "aside":
            details["question"] = "unanswered"
        elif said in REASONS:
            details["block_reason"] = REASONS[said]
        elif said.startswith("card_") and self.card is not None:
            if said != "card_last_four":
                credit = "Crédito" in self.card["product_type"]
                details["card_type"] = "credit" if credit else "debit"
            if said != "card_type":
                details["last_four"] = self.card["last_four"]
        reason = details["block_reason"]
        details["block_reason"] = MODEL_REASONS.get(reason, reason)
        return structured(RequestDetails.model_validate(details))

    async def choose(self, messages: list[BaseMessage]) -> dict[str, Any]:
        said, language = self.placed(messages)
        shown = listed(messages[0].text)
        if said == "transaction_newest":
            return structured(
                TransactionChoice.model_validate(
                    {"fitting": [1], "language": language, "question": None}
                )
            )
        if said == "aside":
            return structured(
                TransactionChoice.model_validate(
                    {"fitting": [], "language": language, "question": "unanswered"}
                )
            )
        merchant, amount, on = self.hints(said)
        fitting = [
            n
            for n, day, value, name in shown
            if (merchant is None or name.casefold() == merchant.casefold())
            and (amount is None or value == amount)
            and (on is None or day == on)
        ]
        if isinstance(said, str) and not fitting:
            fitting = [n for n, *_ in shown]
        return structured(
            TransactionChoice.model_validate(
                {"fitting": fitting[:10], "language": language, "question": None}
            )
        )

    def hints(
        self, said: Family | str
    ) -> tuple[str | None, Decimal | None, str | None]:
        """
        What the customer said about the transaction: a message's slots and relative date, or an answer's one field
        from the transaction meant.
        """
        if isinstance(said, Family):
            on = self.slots.get("date") if "date" in said.slots else None
            if said.when is not None:
                on = (self.business_date - timedelta(days=WHEN[said.when])).isoformat()
            return (
                self.slots.get("merchant") if "merchant" in said.slots else None,
                Decimal(self.slots["amount"]) if "amount" in said.slots else None,
                on,
            )
        meant = self.transaction or {}
        return (
            meant.get("merchant_name") if said == "transaction_merchant" else None,
            Decimal(str(meant["amount"])) if said == "transaction_amount" else None,
            meant["transaction_date"][:10] if said == "transaction_date" else None,
        )

    async def handoff_text(self, messages: list[BaseMessage]) -> dict[str, Any]:
        return structured(HandoffText.model_validate(HANDOFF_TEXT))

    async def reply(self, messages: list[BaseMessage]) -> AIMessage:
        return AIMessage(
            content=laid_out(PLACEHOLDER.findall(messages[0].text)) or "Listo.",
            usage_metadata=USAGE,
            response_metadata={"model_name": MODEL},
        )

"""
The oracle (ADR-0005, The oracle): a second reading of the policy's outcomes, from the policy's text and the customer's
state in bronze, never from the tools or the graph. For one case it plays the script against the policy and says, turn
by turn, what each request served should record (its outcome class, what the turn waits for, the tools it must and must
not call, the facts the reply must state, the words naming a status it must not, and the handoff it files) and which
cards end blocked. It writes no reply.
The facts are formatted in the conversation's language turn by turn: Spanish until a message clearly in one of the two
languages sets it, which a paraphrase the family marks as unclear doesn't (POL-50; ADR-0005's amendment of 2026-10-02).

Each request is a generator: it yields a step that waits on the customer (a question, a control) and receives the
customer's answer, and returns the step that ends it. The outcome classes follow ADR-0005's amendment of 2026-09-29 and
ADR-0004's table of situations (as amended on 2026-10-01). A situation the oracle doesn't read raises NotCoveredError, so the
generator draws no case for it rather than a wrong expectation.

A case's fixtures are read into the customer's state, and its fault plans are counted against the tool calls the path
makes (ADR-0005's amendment of 2026-10-01). A call takes up to three attempts, so a plan of at most three failures is
spent by the tool's first call, which fails when it held three; the oracle predicts that call where the graph makes it,
and refuses a plan that would reach a call it doesn't predict, or no call at all.
"""

from collections.abc import Callable, Generator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from faro.evaluation.facts import Facts
from faro.evaluation.families import Family
from faro.evaluation.state import (
    BUSINESS_DATE,
    CREDIT,
    DEBIT,
    Card,
    Customer,
    Transaction,
    merged,
)

POLICY_VERSION = 6
# The details the agent asks the customer for (POL-06, POL-13, POL-27, POL-35).
ASKED = ("card", "reason", "transaction")
# POL-50: the conversation's language until a message clearly in one of the two sets it.
DEFAULT_LANGUAGE = "es"
# POL-05's order.
ORDER = (
    "block_card",
    "unrecognized_charge",
    "talk_to_human",
    "decline_reason",
    "card_status",
    "available_credit",
    "recent_transactions",
    "unsupported",
)
PAGE = 10
# A decline or a charge is looked for in up to three pages (ADR-0004's amendment of 2026-10-01).
SEARCHED = 3 * PAGE
SHOWN = 5
# POL-17: two questions that don't settle a detail, then a handoff is offered.
QUESTIONS = 2
TYPES = {"credit": CREDIT, "debit": DEBIT}
WHEN = {"today": 0, "yesterday": 1, "day_before": 2}
REASONS = {
    "reason_lost": "lost",
    "reason_stolen": "stolen",
    "reason_unrecognized_charge": "unrecognized_charge",
    "reason_customer_request": "customer_request",
}
# POL-12: the words that would name a status the policy withholds from the customer, in either language.
WITHHELD = {
    "Closed": ("closed", "cerrado", "cerrada", "encerrado", "encerrada"),
    "Suspended": ("suspended", "suspendido", "suspendida", "suspenso", "suspensa"),
}
MAX_TURNS = 12
# A call's attempts: the first and POL-48's two retries (ADR-0004's decision 18).
ATTEMPTS = 3
GATEWAY_TOOLS = (
    "list_cards",
    "get_card",
    "get_available_credit",
    "find_transactions",
    "block_card",
)
# The requests the oracle predicts a failed list of cards for: each reads the cards to settle one.
CARD_REQUESTS = (
    "card_status",
    "available_credit",
    "recent_transactions",
    "decline_reason",
    "block_card",
)


class NotCoveredError(Exception):
    """
    A situation the oracle doesn't predict.
    """


@dataclass(frozen=True)
class Hints:
    """
    What a message or an answer says about the card or transaction meant.
    """

    card_type: str | None = None
    last_four: str | None = None
    merchant: str | None = None
    amount: Decimal | None = None
    on: date | None = None
    newest: bool = False

    def given(self) -> bool:
        return self.card_type is not None or self.last_four is not None

    def merged(self, other: "Hints") -> "Hints":
        return replace(
            self,
            **{
                k: v for k, v in vars(other).items() if v is not None and v is not False
            },
        )


@dataclass(frozen=True)
class Answer:
    sends: str
    hints: Hints = Hints()
    block_reason: str | None = None
    language: str | None = None


@dataclass
class Step:
    outcome_class: str
    rules: list[str]
    awaiting: str = "none"
    tools: tuple[str, ...] = ()
    facts: dict[str, str] = field(default_factory=dict)
    handoff: dict[str, str] | None = None
    blocked: str | None = None


Request = Generator[Step, Answer, Step]


def handoff(reason_code: str, trigger: str, priority: str = "normal") -> dict[str, str]:
    queue = (
        "dispute_intake" if reason_code == "unrecognized_charge" else "customer_service"
    )
    return {
        "reason_code": reason_code,
        "trigger": trigger,
        "queue": queue,
        "priority": priority,
    }


def said_in(answer: Any) -> str | None:
    """
    The language a scripted answer is written in, from its ID (answer_id/language); a control press has none.
    """
    if not isinstance(answer, Mapping):
        return None
    language: str = answer["id"].split("/")[1]
    return language


def fits(transaction: Transaction, hints: Hints) -> bool:
    if (
        hints.merchant is not None
        and (transaction.merchant or "").casefold() != hints.merchant.casefold()
    ):
        return False
    if hints.amount is not None and transaction.amount != hints.amount:
        return False
    return hints.on is None or transaction.at.date() == hints.on


class Conversation:
    def __init__(
        self,
        customer: Customer,
        case: Mapping[str, Any],
        families: Mapping[str, Family],
        words: Mapping[str, Any],
    ) -> None:
        if case["script"].get("actions"):
            raise NotCoveredError("harness actions")
        customer = merged(customer, case["fixtures"])
        self.left: dict[str, int] = {}
        for fault in case["faults"]:
            if (
                fault["tool"] not in GATEWAY_TOOLS
                or fault["tool"] in self.left
                or fault["failures"] > ATTEMPTS
            ):
                raise NotCoveredError("a fault plan past a tool's first call")
            self.left[fault["tool"]] = fault["failures"]
        self.customer = customer
        # A card in the same status may be named by its own (POL-21, POL-34), and its words can't be told apart.
        self.withheld = (
            []
            if customer.status in {c.status for c in customer.cards}
            else list(WITHHELD.get(customer.status or "", ()))
        )
        self.case = case
        self.script = case["script"]
        self.families = families
        self.facts = Facts(DEFAULT_LANGUAGE, customer.country, words)
        means = self.script["means"]
        self.card = (
            customer.card(means["product_id"]) if "product_id" in means else None
        )
        self.transaction = (
            next(
                t
                for t in (self.card.transactions if self.card else ())
                if t.transaction_id == means["transaction_id"]
            )
            if "transaction_id" in means
            else None
        )
        self.typed = False
        self.asided = False
        self.paging: tuple[Card, int] | None = None
        self.turns: list[dict[str, Any]] = []
        self.blocked: list[str] = []

    # The customer's side.

    def message_hints(self, family: Family) -> Hints:
        slots = self.script.get("slots", {})
        hints = Hints(
            card_type=TYPES[family.extract["card_type"]]
            if "card_type" in family.extract
            else None,
            last_four=slots.get("last_four") if "last_four" in family.slots else None,
            merchant=slots.get("merchant") if "merchant" in family.slots else None,
            amount=Decimal(slots["amount"]) if "amount" in family.slots else None,
            on=date.fromisoformat(slots["date"]) if "date" in family.slots else None,
        )
        if family.when is not None:
            hints = replace(hints, on=BUSINESS_DATE - timedelta(days=WHEN[family.when]))
        return hints

    def answer_for(self, awaiting: str) -> Answer | None:
        answers = self.script["answers"]
        if awaiting in ASKED and "aside" in answers and not self.asided:
            self.asided = True
            return Answer("aside", language=said_in(answers["aside"]))
        if awaiting == "confirm_control":
            if "typed_yes" in answers and not self.typed:
                self.typed = True
                return Answer("typed_yes", language=said_in(answers["typed_yes"]))
            press = answers.get("confirm_control", "ignore")
            return None if press == "ignore" else Answer(press)
        if awaiting == "handoff_control":
            press = answers.get("handoff_control", "ignore")
            return None if press == "ignore" else Answer(press)
        if awaiting not in answers:
            return Answer("dont_know", language=said_in(answers.get("dont_know")))
        kind = answers[awaiting]["id"].split("/")[0].rsplit("-", 1)[0]
        language = said_in(answers[awaiting])
        if awaiting == "reason":
            return Answer("reason", block_reason=REASONS[kind], language=language)
        if awaiting == "card":
            assert self.card is not None
            return Answer(
                "card",
                Hints(
                    card_type=self.card.type if kind != "card_last_four" else None,
                    last_four=self.card.last_four if kind != "card_type" else None,
                ),
                language=language,
            )
        assert self.transaction is not None
        t = self.transaction
        return Answer(
            "transaction",
            Hints(
                merchant=t.merchant if kind == "transaction_merchant" else None,
                amount=t.amount if kind == "transaction_amount" else None,
                on=t.at.date() if kind == "transaction_date" else None,
                newest=kind == "transaction_newest",
            ),
            language=language,
        )

    def heard(self, family: Family, message_id: str) -> str | None:
        """
        The language a message sets (POL-50): the one it is filed under when it is clearly in it; a third language or
        a paraphrase the family marks as unclear sets none.
        """
        if family.kind == "third_language":
            return None
        said = next((m for m in family.messages if m.id == message_id), None)
        if said is None:
            return message_id.split("/")[1]
        return said.language if said.clear else None

    # The driver.

    def run(self) -> dict[str, Any]:
        for message in self.script["messages"]:
            family = self.families[message["id"].split("/")[0]]
            turn = self.open("message", self.heard(family, message["id"]))
            if family.kind == "third_language":
                self.decide(turn, None, Step("decline", ["POL-51"]))
                continue
            if not family.labels:
                self.decide(turn, None, Step("answer", ["POL-06"]))
                continue
            queue = sorted(family.labels, key=ORDER.index)
            hints = self.message_hints(family)
            if self.left.get("list_cards") and (
                len(queue) > 1
                or queue[0] not in CARD_REQUESTS
                or not self.customer.served_in_full
            ):
                raise NotCoveredError("a failed list of cards outside one card request")
            if not self.customer.served_in_full and queue != ["block_card"]:
                if "block_card" in queue:
                    raise NotCoveredError("a block queued with a request handed off")
                step = Step(
                    "hand_off",
                    ["POL-12"],
                    tools=("list_cards", "file_handoff"),
                    handoff=handoff("customer_not_active", "required"),
                )
                self.decide(turn, queue[0], step)
                continue
            for label in queue:
                request = self.request(label, family, hints)
                answer: Answer | None = None
                while True:
                    try:
                        step = request.send(answer) if answer else next(request)
                    except StopIteration as done:
                        self.decide(turn, label, done.value)
                        break
                    self.decide(turn, label, step)
                    answer = self.answer_for(step.awaiting)
                    if answer is None:
                        return self.expected()
                    turn = self.open(answer.sends, answer.language)
        return self.expected()

    def open(self, sends: str, language: str | None = None) -> dict[str, Any]:
        """
        A turn, in the language the message that opens it sets, or the one before (POL-50): the conversation's language
        its decisions record, and the one its facts are formatted in.
        """
        if language is not None:
            self.facts = replace(self.facts, language=language)
        if len(self.turns) == MAX_TURNS:
            raise NotCoveredError("a conversation longer than a case holds")
        turn: dict[str, Any] = {
            "sends": sends,
            "decisions": [],
            "language": self.facts.language,
            "awaiting": "none",
            "tools_required": [],
            "tools_forbidden": [],
            "facts": {},
            "withheld": list(self.withheld),
        }
        self.turns.append(turn)
        return turn

    def decide(self, turn: dict[str, Any], label: str | None, step: Step) -> None:
        turn["decisions"].append(
            {
                "request_label": label,
                "outcome_class": step.outcome_class,
                "rules": sorted(set(step.rules)),
            }
        )
        turn["awaiting"] = step.awaiting
        turn["tools_required"] = sorted({*turn["tools_required"], *step.tools})
        turn["facts"] |= step.facts
        if step.handoff is not None:
            turn["handoff"] = step.handoff
        if step.blocked is not None and step.blocked not in self.blocked:
            self.blocked.append(step.blocked)

    def expected(self) -> dict[str, Any]:
        if any(self.left.values()):
            raise NotCoveredError("a fault plan the path doesn't reach")
        for turn in self.turns:
            turn["tools_forbidden"] = [
                tool
                for tool in ("block_card", "file_handoff")
                if tool not in turn["tools_required"]
            ]
        return {
            "turns": self.turns,
            "blocked": self.blocked,
            "rules": sorted(
                {r for t in self.turns for d in t["decisions"] for r in d["rules"]}
            ),
            "policy_version": POLICY_VERSION,
        }

    # The tools' faults.

    def fails(self, tool: str) -> bool:
        """
        Whether the path's next call of the tool fails, taking the plan's failures its attempts meet.
        """
        left = self.left.get(tool, 0)
        self.left[tool] = max(0, left - ATTEMPTS)
        return left >= ATTEMPTS

    def unplanned(self, tool: str) -> None:
        """
        A call the oracle doesn't predict a fault for, refused while the tool's plan holds failures.
        """
        if self.left.get(tool):
            raise NotCoveredError(
                f"a fault plan for {tool} where the oracle doesn't read it"
            )

    def unavailable(self, tool: str) -> Request:
        """
        POL-48: a read that failed every attempt can't be answered now, and a person is offered.
        """
        step = Step("abstain", ["POL-48"], "handoff_control", tools=(tool,))
        return (yield from self.offer(step, "tool_failure"))

    # The requests.

    def request(self, label: str, family: Family, hints: Hints) -> Request:
        # Every request reads the customer's cards before anything else.
        if self.fails("list_cards"):
            return self.unavailable("list_cards")
        if family.extract.get("owner") == "someone_else":
            return self.refused()
        if label == "card_status":
            return self.card_status(family, hints)
        if label == "available_credit":
            return self.available_credit(family, hints)
        if label == "recent_transactions":
            return self.recent_transactions(family, hints)
        if label == "decline_reason":
            return self.decline_reason(family, hints)
        if label == "block_card":
            return self.block_card(family, hints)
        if label == "unrecognized_charge":
            return self.unrecognized_charge(hints)
        if label == "talk_to_human":
            return self.talk_to_human(family)
        return self.unsupported(family)

    def refused(self) -> Request:
        return Step("decline", ["POL-08"])
        yield

    def offer(self, step: Step, reason_code: str) -> Request:
        """
        An offered handoff, made only if the customer accepts it with the handoff control (POL-45).
        """
        answer = yield step
        if answer.sends == "accept":
            return Step(
                "hand_off",
                [*step.rules, "POL-45"],
                tools=("file_handoff",),
                handoff=handoff(reason_code, "accepted_offer"),
            )
        if answer.sends == "decline":
            return Step("answer", ["POL-45"])
        raise NotCoveredError("text sent while a handoff is offered")

    def asking(self, step: Step) -> Generator[Step, Answer, Answer]:
        """
        A question, and the same one again after a message that doesn't answer it, which counts toward no limit (POL-06,
        POL-17).
        """
        answer = yield step
        while answer.sends == "aside":
            answer = yield replace(step, rules=[*step.rules, "POL-06"], tools=())
        return answer

    def unsettled(self) -> Request:
        return (
            yield from self.offer(
                Step("abstain", ["POL-17"], "handoff_control"), "clarification_failed"
            )
        )

    def which_card(
        self, hints: Hints, applies: Callable[[Card], bool] | None
    ) -> Generator[Step, Answer, Card | Step]:
        """
        POL-13 to POL-17: the card the customer means, asked for while more than one is left.
        """
        cards = list(self.customer.cards)
        if not cards:
            raise NotCoveredError("a customer with no card")
        asked = 0
        while True:
            named = [
                c
                for c in cards
                if (hints.card_type is None or c.type == hints.card_type)
                and (hints.last_four is None or c.last_four == hints.last_four)
            ]
            if hints.given() and not named:
                rules, listed = ["POL-16"], cards
            else:
                candidates = named if hints.given() else cards
                preferred = [
                    c for c in candidates if applies is not None and applies(c)
                ]
                pool = preferred or candidates
                if len(pool) == 1:
                    return pool[0]
                if hints.last_four is not None and len({c.type for c in pool}) == 1:
                    return Step(
                        "hand_off",
                        ["POL-15"],
                        tools=("file_handoff",),
                        handoff=handoff("ambiguous_card", "required"),
                    )
                rules, listed = ["POL-13", "POL-14"], pool
            if asked == QUESTIONS:
                return (yield from self.unsettled())
            tools = ("list_cards",) if asked == 0 else ()
            asked += 1
            answer = yield from self.asking(
                Step(
                    "clarify",
                    rules,
                    "card",
                    tools=tools,
                    facts={"{card_list}": self.facts.card_list(listed)},
                )
            )
            hints = hints.merged(answer.hints)

    def which_transaction(
        self, card: Card, hints: Hints, prefer_declined: bool
    ) -> Generator[Step, Answer, tuple[Transaction | None, tuple[str, ...]] | Step]:
        """
        POL-27 and POL-39: the transaction meant among the card's newest, the Declined ones first for a decline. Also
        returns the tools the turn that ends the search must call: the search itself, unless a question came first.
        """
        meant = [t for t in card.transactions[:SEARCHED] if fits(t, hints)]
        if prefer_declined:
            meant = [t for t in meant if t.status == "Declined"] or meant
        asked = 0
        while len(meant) > 1:
            shown = meant[:SHOWN]
            if asked == QUESTIONS:
                return (yield from self.unsettled())
            asked += 1
            answer = yield from self.asking(
                Step(
                    "clarify",
                    ["POL-27"],
                    "transaction",
                    tools=("find_transactions",) if asked == 1 else (),
                    facts={
                        "{card}": self.facts.card(card),
                        "{transactions}": self.facts.choices(shown),
                    },
                )
            )
            if answer.hints.newest:
                meant = shown[:1]
            else:
                meant = [t for t in shown if fits(t, answer.hints)] or shown
        return (meant[0] if meant else None), () if asked else ("find_transactions",)

    def card_status(self, family: Family, hints: Hints) -> Request:
        if family.extract.get("cards") == "all":
            if self.fails("get_card"):
                return (yield from self.unavailable("get_card"))
            return Step(
                "answer",
                ["POL-01", "POL-14", "POL-21"],
                tools=("list_cards", "get_card"),
                facts={"{cards}": self.facts.cards(self.customer.cards)},
            )
        card = yield from self.which_card(hints, None)
        if isinstance(card, Step):
            return card
        if self.fails("get_card"):
            return (yield from self.unavailable("get_card"))
        step = Step(
            "answer",
            ["POL-01", "POL-21"],
            tools=("get_card",),
            facts={
                "{card}": self.facts.card(card),
                "{card.status}": self.facts.status(card),
                "{card.expiration}": self.facts.expiration(card),
            },
        )
        if card.active and card.past_expiration:
            step.rules += ["POL-30", "POL-31"]
            if family.extract.get("conflict") == "asks_which":
                step.awaiting = "handoff_control"
                return (yield from self.offer(step, "record_conflict"))
        return step

    def available_credit(self, family: Family, hints: Hints) -> Request:
        if family.extract.get("cards") == "all":
            raise NotCoveredError("available credit on every card")
        card = yield from self.which_card(hints, lambda c: c.credit)
        if isinstance(card, Step):
            return card
        # The tool reads a debit card's or an inactive card's answer too.
        if self.fails("get_available_credit"):
            return (yield from self.unavailable("get_available_credit"))
        facts = {"{card}": self.facts.card(card)}
        if not card.credit:
            return Step("decline", ["POL-22"], facts=facts)
        if not card.active:
            facts["{card.status}"] = self.facts.status(card)
            return Step("decline", ["POL-22"], facts=facts)
        tools = ("get_available_credit",)
        if card.limit is None:
            step = Step("abstain", ["POL-24"], "handoff_control", tools, facts)
            return (yield from self.offer(step, "missing_data"))
        if card.balance is None:
            raise NotCoveredError("a credit card with no balance")
        facts["{as_of}"] = self.facts.as_of()
        if card.balance > card.limit:
            facts["{credit.over_by}"] = self.facts.amount(
                card.balance - card.limit, card.currency
            )
            return Step(
                "answer", ["POL-01", "POL-22", "POL-23"], tools=tools, facts=facts
            )
        facts["{credit.available}"] = self.facts.amount(
            card.limit - card.balance, card.currency
        )
        return Step("answer", ["POL-01", "POL-22"], tools=tools, facts=facts)

    def recent_transactions(self, family: Family, hints: Hints) -> Request:
        page = family.extract.get("page")
        if page == "earlier":
            return Step("decline", ["POL-25"], facts=self.facts.window())
        if page == "next":
            if self.paging is None:
                raise NotCoveredError("a next page with no page before it")
            self.unplanned("find_transactions")
            card, shown = self.paging
        else:
            found = yield from self.which_card(hints, None)
            if isinstance(found, Step):
                return found
            if self.fails("find_transactions"):
                return (yield from self.unavailable("find_transactions"))
            card, shown = found, 0
        listed = card.transactions[shown : shown + PAGE]
        self.paging = (card, shown + len(listed))
        facts = {"{card}": self.facts.card(card), **self.facts.window()}
        if listed:
            facts["{transactions}"] = self.facts.page(listed)
        return Step(
            "answer", ["POL-19", "POL-25"], tools=("find_transactions",), facts=facts
        )

    def decline_reason(self, family: Family, hints: Hints) -> Request:
        card = yield from self.which_card(hints, None)
        if isinstance(card, Step):
            return card
        if self.fails("find_transactions"):
            return (yield from self.unavailable("find_transactions"))
        searched = yield from self.which_transaction(card, hints, prefer_declined=True)
        if isinstance(searched, Step):
            return searched
        found, tools = searched
        facts = {"{card}": self.facts.card(card)}
        if found is None:
            return Step("answer", ["POL-27"], tools=tools, facts=facts)
        facts["{transaction}"] = self.facts.found(found)
        rules = ["POL-02", "POL-27"]
        if found.status != "Declined":
            facts["{transaction.status}"] = self.facts.transaction_status(found)
            return Step("answer", [*rules, "POL-28"], tools=tools, facts=facts)
        if found.code == "54":
            # The graph reads the card for a conflict over code 54's expiration.
            self.unplanned("get_card")
        if not found.listed_code:
            step = Step("abstain", [*rules, "POL-32"], "handoff_control", tools, facts)
            return (yield from self.offer(step, "missing_data"))
        facts["{transaction.meaning}"] = self.facts.reason(found)
        step = Step("answer", [*rules, "POL-29"], tools=tools, facts=facts)
        if "expired_code_before_expiration" in card.conflicts(found):
            step.rules.append("POL-30")
            step.tools = (*tools, "get_card")
            step.facts["{card.expiration}"] = self.facts.expiration(card)
            if family.extract.get("conflict") == "asks_which":
                step.awaiting = "handoff_control"
                return (yield from self.offer(step, "record_conflict"))
        return step

    def block_card(self, family: Family, hints: Hints) -> Request:
        card = yield from self.which_card(hints, lambda c: c.active)
        if isinstance(card, Step):
            return card

        # Formatted as each step is built, since the reason's answer may set the language (POL-50).
        def facts() -> dict[str, str]:
            return {"{card}": self.facts.card(card)}

        if not card.active:
            return Step("decline", ["POL-13", "POL-34"], facts=facts())
        reason = family.extract.get("block_reason")
        asked = 0
        while reason is None:
            if asked == QUESTIONS:
                return (yield from self.unsettled())
            asked += 1
            answer = yield from self.asking(
                Step("clarify", ["POL-35"], "reason", facts=facts())
            )
            reason = answer.block_reason
        shown = yield Step(
            "block", ["POL-35", "POL-36"], "confirm_control", facts=facts()
        )
        while shown.sends == "typed_yes":
            shown = yield Step("block", ["POL-36"], "confirm_control")
        charge = reason == "unrecognized_charge"
        if shown.sends == "cancel":
            if charge:
                return self.disputed(["POL-36", "POL-39"], ("file_handoff",), "urgent")
            return Step("answer", ["POL-36"])
        if self.fails("block_card"):
            # POL-37: not verified, so handed off without another confirmation; POL-47 for a card reported missing or a
            # charge, whose handoff goes to dispute intake (POL-39).
            if charge:
                return self.disputed(
                    ["POL-37", "POL-39"], ("block_card", "file_handoff"), "urgent"
                )
            return Step(
                "hand_off",
                ["POL-37"],
                tools=("block_card", "file_handoff"),
                handoff=handoff(
                    "action_not_verified",
                    "required",
                    "normal" if reason == "customer_request" else "urgent",
                ),
            )
        # The block's read-back.
        self.unplanned("get_card")
        if charge:
            return self.disputed(
                ["POL-37", "POL-39", "POL-45"],
                ("block_card", "file_handoff"),
                blocked=card.product_id,
            )
        blocked = Step(
            "block",
            ["POL-03", "POL-37"],
            tools=("block_card",),
            blocked=card.product_id,
        )
        if reason in ("lost", "stolen"):
            blocked.rules.append("POL-38")
            blocked.awaiting = "handoff_control"
            return (yield from self.offer(blocked, "unsupported_request"))
        return blocked

    def unrecognized_charge(self, hints: Hints) -> Request:
        card = yield from self.which_card(hints, None)
        if isinstance(card, Step):
            return card
        for tool in ("find_transactions", "block_card", "get_card"):
            self.unplanned(tool)
        searched = yield from self.which_transaction(card, hints, prefer_declined=False)
        if isinstance(searched, Step):
            return searched
        found, tools = searched
        facts = {"{card}": self.facts.card(card)}
        if found is not None:
            facts["{transaction}"] = self.facts.found(found)
        if not card.active:
            priority = "normal" if card.status == "Blocked" else "urgent"
            return Step(
                "hand_off",
                ["POL-34", "POL-39"],
                tools=(*tools, "file_handoff"),
                facts=facts,
                handoff=handoff("unrecognized_charge", "required", priority),
            )
        shown = yield Step(
            "block", ["POL-36", "POL-39"], "confirm_control", tools, facts
        )
        while shown.sends == "typed_yes":
            shown = yield Step("block", ["POL-36"], "confirm_control")
        if shown.sends == "cancel":
            return self.disputed(["POL-36", "POL-39"], ("file_handoff",), "urgent")
        return self.disputed(
            ["POL-37", "POL-39", "POL-45"],
            ("block_card", "file_handoff"),
            blocked=card.product_id,
        )

    def disputed(
        self,
        rules: list[str],
        tools: tuple[str, ...],
        priority: str = "normal",
        blocked: str | None = None,
    ) -> Step:
        """
        POL-39's handoff to dispute intake once the block's confirmation ends, whether the charge was reported or given
        as a block's reason.
        """
        return Step(
            "hand_off",
            rules,
            tools=tools,
            handoff=handoff("unrecognized_charge", "required", priority),
            blocked=blocked,
        )

    def talk_to_human(self, family: Family) -> Request:
        return Step(
            "hand_off",
            ["POL-44"],
            tools=("file_handoff",),
            handoff=handoff(
                "complaint" if family.complaint else "customer_request", "required"
            ),
        )
        yield

    def unsupported(self, family: Family) -> Request:
        service = family.extract.get("service", "other_card_service")
        if service == "unblock":
            return Step(
                "hand_off",
                ["POL-41"],
                tools=("file_handoff",),
                handoff=handoff("unblock_request", "required"),
            )
        if service == "outside_cards":
            return Step("decline", ["POL-43"])
        step = Step("decline", ["POL-42"], "handoff_control")
        return (yield from self.offer(step, "unsupported_request"))


def expect(
    customer: Customer,
    case: Mapping[str, Any],
    families: Sequence[Family],
    words: Mapping[str, Any],
) -> dict[str, Any]:
    by_id = {f.family_id: f for f in families}
    return Conversation(customer, case, by_id, words).run()

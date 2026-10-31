"""
The chat and the console in a real browser, against the deployed site (ADR-0004, To verify on the first deploy: the
chat in a real browser; ADR-0007): a persona signs in through the form with SRP, asks about their cards, and reads a
reply sent whole, with the turn in the sign-in's execution record; a persona blocks a card with the confirm control,
which a typed yes doesn't confirm, and is offered the replacement with the handoff control; the README's journey runs in
two tabs, from a charge the customer doesn't recognize to the case's reference, while a human agent sees the case reach
dispute intake within a poll, urgent when the block is cancelled, and reads it with each fact next to its call; the
console shows a case's markup as text; a new conversation is a thread of its own on the same runtime session;
another customer's ID, a staff user in the chat, a customer or the AI team in the console, and another sign-in's
runtime session get nothing; and signing out revokes the sign-in's refresh token
(SEC-04, SEC-05, POL-09 to POL-11, POL-27, POL-36 to POL-39, POL-45, POL-47, CTL-02, CTL-05, EVL-04, OPS-02).
Assertions count and compare without printing a reply, a token, or an ID, and nothing the page shows is saved: no
trace, screenshot, or video.
"""

import json
import re
import secrets
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any

import pytest
from botocore.exceptions import ClientError
from mypy_boto3_cognito_idp import CognitoIdentityProviderClient
from playwright.sync_api import (
    Browser,
    Dialog,
    Locator,
    Page,
    Request,
    expect,
    sync_playwright,
)

from faro.agent.formats import transaction_name
from faro.agent.texts import FIXED, render
from faro.contracts import validator
from faro.tools.cases import draw_reference, queue_order, reference_item

from .conftest import SignIn, User, cases, claims, handoffs
from .test_agent import checkpoint_messages, records
from .test_handoff import disputed, named, window
from .test_stack import arguments, call, tool_output

pytestmark = [pytest.mark.integration, pytest.mark.browser, pytest.mark.timeout(300)]

TEXTS = {
    "es": {
        "locale": "es-CO",
        "username": "Usuario",
        "password": "Contraseña",
        "submit": "Entrar",
        "message": "Escriba su mensaje",
        "send": "Enviar",
        "sign_out": "Cerrar sesión",
        "new_chat": "Nueva conversación",
        "refused": "No pudimos iniciar su sesión.",
        "session_refused": "No pudimos continuar esta conversación.",
        "other_language": "Português",
    },
    "pt": {
        "control": "Confirmar o bloqueio",
        "block": "Confirmar o bloqueio",
        "cancel": "Cancelar",
        "offer": "Encaminhar seu caso para uma pessoa",
        "locale": "pt-BR",
        "username": "Usuário",
        "password": "Senha",
        "submit": "Entrar",
        "message": "Escreva sua mensagem",
        "send": "Enviar",
        "sign_out": "Sair",
        "new_chat": "Nova conversa",
        "refused": "Não foi possível entrar.",
        "session_refused": "Não foi possível continuar esta conversa.",
        "other_language": "Español",
    },
}
# The opening's suggested prompts, one template for every sign-in (ADR-0007, Judges' access).
SUGGESTIONS = {
    "es": [
        "Muéstreme el estado de mis tarjetas",
        "Quiero bloquear una tarjeta",
        "No reconozco una compra en mi tarjeta",
    ],
    "pt": [
        "Mostre o status dos meus cartões",
        "Quero bloquear um cartão",
        "Não reconheço uma compra no meu cartão",
    ],
}
RUNTIME_SESSION = "faro.runtime-session"
# The console's own text, in Spanish whatever the browser's language (ADR-0007, Routes).
CONSOLE = {
    "disputes": "Disputas",
    "service": "Servicio al cliente",
    "urgent": "Urgente",
    "verified": "Verificado",
    "facts": "Hechos verificados",
    "actions": "Acciones",
    "record": "Registro de ejecución",
    "flagged": "Caso marcado",
    "reference": "Referencia",
    "search": "Buscar",
    "no_access": "Su usuario no tiene acceso a la consola de casos",
}
# Within a poll: 3 seconds, the queue index's lag, and a request's time.
ARRIVES_MS = 10_000


@dataclass
class Tab:
    page: Page
    language: str
    violations: list[str] = field(default_factory=list)
    errors: int = 0
    runtime: list[str] = field(default_factory=list)

    @property
    def texts(self) -> dict[str, str]:
        return TEXTS[self.language]

    def stored(self, suffix: str) -> str | None:
        found: str | None = self.page.evaluate(
            "suffix => Object.entries(sessionStorage).find(([k]) => k.endsWith(suffix))?.[1] ?? null",
            suffix,
        )
        return found

    def sign_in(self, site: str, user: User, path: str = "/chat") -> None:
        self.page.goto(f"{site}{path}")
        self.page.get_by_label(self.texts["username"]).fill(user.username)
        self.page.get_by_label(self.texts["password"]).fill(user.password)
        self.page.get_by_role("button", name=self.texts["submit"]).click()

    def ask(self, outputs: dict[str, Any], text: str) -> str:
        message = self.page.get_by_label(self.texts["message"])
        expect(message).to_be_visible()

        def send() -> None:
            message.fill(text)
            self.page.get_by_role("button", name=self.texts["send"]).click()

        return self.turn(outputs, send)

    def turn(self, outputs: dict[str, Any], act: Callable[[], None]) -> str:
        """
        Waits for the Runtime to close the turn before reading the page: assistant-ui can show the reply's empty
        message a moment before the run is marked running.
        """
        access = self.stored(".accessToken")
        assert access is not None
        sign_in = claims(access)["origin_jti"]
        seen = len(records(outputs, sign_in))
        replies = self.page.locator('[data-author="faro"]')
        before = replies.count()
        act()
        deadline = time.time() + 90
        while not (ended := ends(records(outputs, sign_in)[seen:])):
            assert time.time() < deadline, (
                f"the turn never closed; the Runtime answered {self.runtime}, and"
                f" the page shows {self.page.get_by_role('alert').all_inner_texts()}"
            )
            self.page.wait_for_timeout(1000)
        assert ended == ["turn_closed"], f"the turn ended as {ended}"
        expect(replies).to_have_count(before + 1)
        expect(self.page.get_by_role("status")).to_have_count(0)
        expect(replies.last).not_to_be_empty()
        return replies.last.inner_text()


def kind(request: Request) -> str:
    return "warm-up" if '"warmup"' in (request.post_data or "") else "message"


def ends(entries: list[dict[str, Any]]) -> list[str]:
    """
    How a message's turn ended: closed, or refused before it opened (with the refusal's code).
    """
    messages = {
        e["turn_id"]
        for e in entries
        if e["kind"] == "turn_opened" and e["input"]["kind"] != "warmup"
    }
    return [
        e["kind"] if e["kind"] == "turn_closed" else e["code"]
        for e in entries
        if (e["kind"] == "turn_closed" and e["turn_id"] in messages)
        or e["kind"] == "request_refused"
    ]


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        launched = playwright.chromium.launch()
        yield launched
        launched.close()


@pytest.fixture
def tab(browser: Browser) -> Iterator[Callable[[str], Tab]]:
    opened: list[Any] = []

    def open_tab(language: str) -> Tab:
        context = browser.new_context(locale=TEXTS[language]["locale"])
        opened.append(context)
        page = context.new_page()
        page.set_default_timeout(90_000)
        expect.set_options(timeout=90_000)
        found = Tab(page, language)

        def console(message: Any) -> None:
            if message.type != "error":
                return
            if re.search(r"Content Security Policy|Trusted Type", message.text):
                found.violations.append(message.text[:200])
            else:
                found.errors += 1

        def answered(response: Any) -> None:
            if "bedrock-agentcore" in response.url:
                found.runtime.append(f"{kind(response.request)} {response.status}")

        def failed(request: Any) -> None:
            if "bedrock-agentcore" in request.url:
                found.runtime.append(f"{kind(request)} failed")

        page.on("console", console)
        page.on("pageerror", lambda _: setattr(found, "errors", found.errors + 1))
        page.on("response", answered)
        page.on("requestfailed", failed)
        return found

    yield open_tab
    for context in opened:
        context.close()


@pytest.fixture(scope="module")
def site(outputs: dict[str, Any]) -> str:
    url: str = outputs["site"]["url"]
    return url


def warmed_up(outputs: dict[str, Any], tab: Tab) -> None:
    """
    The warm-up binds the runtime session to its user once the session's microVM has started.
    """
    access = tab.stored(".accessToken")
    assert access is not None
    deadline = time.time() + 90
    while not any(
        e["kind"] == "turn_closed"
        for e in records(outputs, claims(access)["origin_jti"])
    ):
        assert time.time() < deadline, "the warm-up never finished"
        tab.page.wait_for_timeout(2000)


def cards(outputs: dict[str, Any], access: str) -> set[str]:
    listed = tool_output(call(outputs, access, "list_cards", arguments(access)))
    return {card["last_four"] for card in listed["cards"]}


def test_a_persona_blocks_a_card_with_the_control_not_with_a_typed_yes(
    outputs: dict[str, Any],
    users: dict[str, User],
    site: str,
    tab: Callable[[str], Tab],
    saved: list[str],
) -> None:
    customer = tab("pt")
    customer.sign_in(site, users["other_customer"])
    expect(customer.page.get_by_label(customer.texts["message"])).to_be_visible()
    warmed_up(outputs, customer)
    access = customer.stored(".accessToken")
    assert access is not None
    listed = tool_output(call(outputs, access, "list_cards", arguments(access)))
    card = next(
        c
        for c in listed["cards"]
        if c["product_status"] == "Active" and c["product_type"] == "Tarjeta Crédito"
    )
    controls = customer.page.get_by_role("group", name=customer.texts["control"])

    customer.ask(
        outputs,
        f"Perdi meu cartão de crédito final {card['last_four']} e quero bloqueá-lo.",
    )

    expect(controls).to_have_count(1)
    shown = controls.first.inner_text()
    labelled = card["last_four"] in shown and "Motivo: Perda" in shown
    assert labelled, "the control doesn't name the card and the reason"

    customer.ask(outputs, "Sim, pode bloquear.")

    expect(controls).to_have_count(2)
    block = customer.texts["block"]
    expect(controls.first.get_by_role("button", name=block)).to_be_disabled()
    expect(controls.last.get_by_role("button", name=block)).to_be_enabled()
    read = tool_output(
        call(outputs, access, "get_card", arguments(access, card_id=card["card_id"]))
    )
    assert read["card"]["product_status"] == "Active"

    done = customer.turn(
        outputs, lambda: controls.last.get_by_role("button", name=block).click()
    )

    blocked = f"final {card['last_four']} está bloqueado" in done
    assert blocked, "the reply doesn't say the card is blocked"
    expect(controls.last.get_by_role("button", name=block)).to_be_disabled()
    # A lost card's replacement is a person's to arrange (POL-38).
    expect(
        customer.page.get_by_role("group", name=customer.texts["offer"])
    ).to_have_count(1)
    read = tool_output(
        call(outputs, access, "get_card", arguments(access, card_id=card["card_id"]))
    )
    assert read["card"]["product_status"] == "Blocked"
    entries = records(outputs, claims(access)["origin_jti"])
    saved.extend(handoffs(entries))
    resumes = [e["resume_kind"] for e in entries if e["kind"] == "resume"]
    assert resumes == ["message", "confirm"]
    assert [e["kind"] for e in entries].count("request_refused") == 0
    assert (customer.violations, customer.errors) == ([], 0)


def charged(
    outputs: dict[str, Any], users: dict[str, User], site: str, customer: Tab
) -> tuple[str, dict[str, Any], dict[str, Any], str]:
    """
    The Portuguese persona, signed in, with their credit card, the charge they won't recognize, and their country, by
    which the reply groups an amount.
    """
    customer.sign_in(site, users["other_customer"])
    expect(customer.page.get_by_label(customer.texts["message"])).to_be_visible()
    warmed_up(outputs, customer)
    access = customer.stored(".accessToken")
    assert access is not None
    listed = tool_output(call(outputs, access, "list_cards", arguments(access)))
    card: dict[str, Any] = next(
        c
        for c in listed["cards"]
        if c["product_status"] == "Active" and c["product_type"] == "Tarjeta Crédito"
    )
    charge = disputed(window(outputs, access, card["card_id"]))
    return access, card, charge, listed["customer"]["country"]


def at_the_console(site: str, agent: Tab, user: User) -> None:
    """
    A human agent, signed in at /cases through the staff client, with the queues polling.
    """
    agent.sign_in(site, user, "/cases")
    expect(agent.page.get_by_role("region", name=CONSOLE["disputes"])).to_be_visible()


def arrives(agent: Tab, reference: str, priority: str) -> Locator:
    """
    The case's row in dispute intake, there within a poll of its filing; only an urgent row carries a word.
    """
    queue = agent.page.get_by_role("region", name=CONSOLE["disputes"])
    row = queue.get_by_role("button", name=re.compile(re.escape(reference)))
    expect(row).to_be_visible(timeout=ARRIVES_MS)
    if priority == "urgent":
        expect(row).to_contain_text(CONSOLE["urgent"])
    else:
        expect(row).not_to_contain_text(CONSOLE["urgent"])
    return row


def reads_the_case(agent: Tab, reference: str, row: Locator, outcome: str) -> None:
    """
    The case opened: its block among its actions, and each fact next to the call that read it.
    """
    row.click()
    expect(agent.page.get_by_role("heading", level=2, name=reference)).to_be_visible()
    actions = agent.page.get_by_role("region", name=CONSOLE["actions"])
    expect(actions).to_contain_text(outcome)
    facts = agent.page.get_by_role("region", name=CONSOLE["facts"])
    expect(facts).to_contain_text("find_transactions")
    expect(facts).to_contain_text("file_handoff")
    # The registry opens on its fold; a block cancelled with the control made no call to cite.
    registry = agent.page.locator("details")
    registry.get_by_text(CONSOLE["record"]).click()
    if outcome == CONSOLE["verified"]:
        expect(registry).to_contain_text("block_card")
    expect(agent.page.get_by_text(CONSOLE["flagged"])).to_have_count(0)
    shown = agent.page.evaluate("new URLSearchParams(location.search).get('caso')")
    assert shown == reference, "the address doesn't keep the open case"


def case_filed(
    outputs: dict[str, Any], access: str, reply: str, saved: list[str]
) -> dict[str, Any]:
    filed = [
        e
        for e in records(outputs, claims(access)["origin_jti"])
        if e["kind"] == "handoff" and e["status"] == "filed"
    ]
    saved += [e["handoff_id"] for e in filed]
    once = len(filed) == 1
    assert once, "the sign-in didn't file exactly one case"
    shown = render("handoff_filed", "pt", {"reference": filed[0]["reference"]}) in reply
    assert shown, "the reply doesn't give the case's reference"
    return filed[0]


def test_the_readmes_journey_blocks_the_card_and_files_the_charge_to_dispute_intake(
    outputs: dict[str, Any],
    users: dict[str, User],
    site: str,
    tab: Callable[[str], Tab],
    saved: list[str],
) -> None:
    agent = tab("es")
    at_the_console(site, agent, users["staff"])
    customer = tab("pt")
    access, card, charge, country = charged(outputs, users, site, customer)
    controls = customer.page.get_by_role("group", name=customer.texts["control"])

    listed = customer.ask(outputs, "Não reconheço uma compra no meu cartão.")

    # Nothing in the message tells the charges apart, so the newest are listed (POL-27). The chat draws them as a
    # statement, each charge's name, time, and amount on lines of their own, the name capitalized where it is a word.
    which = FIXED["which_charge"]["pt"].split("{card}")[0]
    parts = transaction_name(charge, "pt", country).lower().split(", ")
    offered = which in listed and all(part in listed.lower() for part in parts)
    assert offered, "the reply doesn't list the card's charges"
    expect(controls).to_have_count(0)

    found = customer.ask(outputs, f"É {named(charge)}.")

    # The charge found is drawn as a statement too, under its sentence.
    facts = {"card": card, "transaction": charge, "country": country}
    sentence = render("charge_found", "pt", facts).split("\n\n")[0]
    shown = sentence in found and all(part in found.lower() for part in parts)
    assert shown, "the reply doesn't name the charge the customer chose"
    expect(controls).to_have_count(1)
    reason = "Motivo: Cobrança não reconhecida" in controls.first.inner_text()
    assert reason, "the control doesn't name the reason"

    done = customer.turn(
        outputs,
        lambda: controls.first.get_by_role(
            "button", name=customer.texts["block"]
        ).click(),
    )

    blocked = f"final {card['last_four']} está bloqueado" in done
    assert blocked, "the reply doesn't say the card is blocked"
    filed = case_filed(outputs, access, done, saved)
    assert (filed["queue"], filed["priority"], filed["flagged"]) == (
        "dispute_intake",
        "normal",
        False,
    )
    read = tool_output(
        call(outputs, access, "get_card", arguments(access, card_id=card["card_id"]))
    )
    assert read["card"]["product_status"] == "Blocked"
    # In the other tab, a human agent sees the case arrive and reads it (ADR-0007, Judges' access: the demo).
    row = arrives(agent, filed["reference"], "normal")
    reads_the_case(agent, filed["reference"], row, CONSOLE["verified"])
    assert (customer.violations, customer.errors) == ([], 0)
    assert (agent.violations, agent.errors) == ([], 0)


def test_the_readmes_journey_cancelled_files_the_charge_as_urgent(
    outputs: dict[str, Any],
    users: dict[str, User],
    site: str,
    tab: Callable[[str], Tab],
    saved: list[str],
) -> None:
    agent = tab("es")
    at_the_console(site, agent, users["staff"])
    customer = tab("pt")
    access, card, charge, country = charged(outputs, users, site, customer)
    controls = customer.page.get_by_role("group", name=customer.texts["control"])
    customer.ask(
        outputs,
        f"Não reconheço {named(charge)}, no meu cartão de crédito final {card['last_four']}.",
    )
    expect(controls).to_have_count(1)

    done = customer.turn(
        outputs,
        lambda: controls.first.get_by_role(
            "button", name=customer.texts["cancel"]
        ).click(),
    )

    kept = f"não bloqueei seu cartão de crédito final {card['last_four']}" in done
    assert kept, "the reply doesn't say the card was left unblocked"
    filed = case_filed(outputs, access, done, saved)
    assert (filed["queue"], filed["priority"], filed["flagged"]) == (
        "dispute_intake",
        "urgent",
        False,
    )
    read = tool_output(
        call(outputs, access, "get_card", arguments(access, card_id=card["card_id"]))
    )
    assert read["card"]["product_status"] == "Active"
    # Urgent, the case sits above every normal one, however new (POL-47).
    row = arrives(agent, filed["reference"], "urgent")
    queue = agent.page.get_by_role("region", name=CONSOLE["disputes"])
    rows = queue.get_by_role("button").all_inner_texts()
    above = rows[: next(i for i, r in enumerate(rows) if filed["reference"] in r)]
    assert all(CONSOLE["urgent"] in r for r in above), "a normal case is above it"
    reads_the_case(agent, filed["reference"], row, "Cancelado por el cliente")
    assert (customer.violations, customer.errors) == ([], 0)
    assert (agent.violations, agent.errors) == ([], 0)


def test_every_sign_in_opens_on_one_template_and_the_bar_switches_the_language(
    site: str,
    users: dict[str, User],
    tab: Callable[[str], Tab],
) -> None:
    """
    One opening for every sign-in: the suggestions in the browser's language, no card with the persona's records,
    and the bar's switch resetting the page's own text (ADR-0007, Judges' access, Routes; SEC-03).
    """
    customer = tab("pt")

    customer.sign_in(site, users["other_customer"])

    for prompt in SUGGESTIONS["pt"]:
        expect(customer.page.get_by_role("button", name=prompt)).to_be_visible()
    customer.page.get_by_role("button", name=customer.texts["other_language"]).click()
    expect(customer.page.get_by_label(TEXTS["es"]["message"])).to_be_visible()
    for prompt in SUGGESTIONS["es"]:
        expect(customer.page.get_by_role("button", name=prompt)).to_be_visible()
    assert (customer.violations, customer.errors) == ([], 0)


def test_a_persona_signs_in_and_reads_a_reply_sent_whole(
    outputs: dict[str, Any],
    users: dict[str, User],
    sign_in: SignIn,
    persona_ids: dict[str, str],
    site: str,
    tab: Callable[[str], Tab],
) -> None:
    customer = tab("es")
    customer.sign_in(site, users["customer"])
    expect(customer.page.get_by_label(customer.texts["message"])).to_be_visible()
    digits = "4" + "".join(secrets.choice("0123456789") for _ in range(15))
    spaced = " ".join(digits[i : i + 4] for i in range(0, 16, 4))

    reply = customer.ask(outputs, "¿Cuáles son mis tarjetas y en qué estado están?")
    customer.ask(outputs, f"¿Mi tarjeta {spaced} está activa?")

    access = customer.stored(".accessToken")
    assert access is not None
    mine = cards(outputs, access)
    assert sum(1 for last_four in mine if last_four not in reply) == 0, (
        "the reply should name each of the persona's cards"
    )
    assert customer.page.locator('[data-author="faro"]').count() == 2
    typed = customer.page.locator('[data-author="customer"]').last.inner_text()
    assert digits not in typed.replace(" ", "")
    assert f"****{digits[-4:]}" in typed
    entries = records(outputs, claims(access)["origin_jti"])
    opened = [e["input"]["kind"] for e in entries if e["kind"] == "turn_opened"]
    assert sorted(opened) == ["message", "message", "warmup"]
    listed = [
        e for e in entries if e["kind"] == "tool_call" and e["tool"] == "list_cards"
    ]
    assert listed
    own = {e["input"]["customer_id"] for e in listed} == {persona_ids["declines"]}
    assert own, "list_cards read another customer's ID"
    assert re.fullmatch(r"[0-9a-f]{40}", entries[0]["versions"]["app"])
    assert (customer.violations, customer.errors) == ([], 0)


def test_a_new_conversation_is_a_thread_of_its_own_on_the_same_runtime_session(
    outputs: dict[str, Any],
    users: dict[str, User],
    site: str,
    tab: Callable[[str], Tab],
) -> None:
    """
    The rail's new conversation (ADR-0007's amendment of 2026-09-30): the page empties, the chat warms up again, and the
    next message opens a turn in a thread of its own on the same runtime session, whose checkpoint holds none of the
    earlier conversation.
    """
    customer = tab("es")
    customer.sign_in(site, users["customer"])
    expect(customer.page.get_by_label(customer.texts["message"])).to_be_visible()
    customer.ask(outputs, "¿Cuáles son mis tarjetas y en qué estado están?")
    session = customer.stored(RUNTIME_SESSION)

    customer.page.get_by_role("button", name=customer.texts["new_chat"]).click()
    expect(customer.page.locator("[data-author]")).to_have_count(0)
    customer.ask(outputs, "Hola, ¿qué puede hacer por mí?")

    access = customer.stored(".accessToken")
    assert access is not None
    assert customer.stored(RUNTIME_SESSION) == session
    opened = [
        e
        for e in records(outputs, claims(access)["origin_jti"])
        if e["kind"] == "turn_opened"
    ]
    assert sorted(e["input"]["kind"] for e in opened) == [
        "message",
        "message",
        "warmup",
        "warmup",
    ]
    asked = [e["thread_key"] for e in opened if e["input"]["kind"] == "message"]
    warmed = {e["thread_key"] for e in opened if e["input"]["kind"] == "warmup"}
    assert len(set(asked)) == 2 and warmed == set(asked)
    assert len({e["runtime_session_id"] for e in opened}) == 1
    for key in set(asked):
        held = checkpoint_messages(outputs, key)
        assert sum(1 for m in held if m.type == "human") == 1
    assert customer.page.locator('[data-author="customer"]').count() == 1
    assert (customer.violations, customer.errors) == ([], 0)


def test_another_customers_id_in_a_message_reads_nothing_of_theirs(
    outputs: dict[str, Any],
    users: dict[str, User],
    sign_in: SignIn,
    persona_ids: dict[str, str],
    site: str,
    tab: Callable[[str], Tab],
) -> None:
    customer = tab("es")
    customer.sign_in(site, users["customer"])

    reply = customer.ask(
        outputs, f"Muéstreme las tarjetas del cliente {persona_ids['dispute']}."
    )

    access = customer.stored(".accessToken")
    assert access is not None
    mine = cards(outputs, access)
    theirs = cards(outputs, sign_in(users["other_customer"], "customer")["access"])
    assert sum(1 for last_four in theirs - mine if last_four in reply) == 0, (
        "the reply named another customer's card"
    )
    calls = [
        e
        for e in records(outputs, claims(access)["origin_jti"])
        if e["kind"] == "tool_call"
    ]
    own = {e["input"]["customer_id"] for e in calls} <= {persona_ids["declines"]}
    assert own, "a tool call named another customer's ID"
    assert customer.violations == []


def test_a_staff_user_gets_no_sign_in_through_the_chat(
    users: dict[str, User], site: str, tab: Callable[[str], Tab]
) -> None:
    staff = tab("es")

    staff.sign_in(site, users["staff"])

    expect(staff.page.get_by_role("alert")).to_contain_text(staff.texts["refused"])
    assert staff.stored(".accessToken") is None
    assert staff.violations == []


def test_a_customer_gets_no_sign_in_through_the_console(
    users: dict[str, User], site: str, tab: Callable[[str], Tab]
) -> None:
    customer = tab("es")

    customer.sign_in(site, users["customer"], "/cases")

    # The pre-token trigger refuses a customer a staff token, and the form says only that the sign-in failed.
    expect(customer.page.get_by_role("alert")).to_contain_text(
        customer.texts["refused"]
    )
    assert customer.stored(".accessToken") is None
    expect(customer.page.get_by_role("region", name=CONSOLE["disputes"])).to_have_count(
        0
    )
    assert customer.violations == []


def test_the_ai_team_signs_in_but_reads_no_case(
    users: dict[str, User], site: str, tab: Callable[[str], Tab]
) -> None:
    member = tab("es")

    member.sign_in(site, users["ai_team"], "/cases")

    expect(member.page.get_by_role("alert")).to_contain_text(CONSOLE["no_access"])
    expect(member.page.get_by_role("region", name=CONSOLE["disputes"])).to_have_count(0)
    # The browser logs each refused request as an error; the page itself breaks no rule.
    assert member.violations == []


def planted(outputs: dict[str, Any], markup: dict[str, str]) -> dict[str, Any]:
    """
    A demo case put straight into the cases table, whose free text holds markup: the console must show it as text
    (ADR-0007, Rendering what others wrote). Its IDs are made up, and the record it names holds nothing.
    """
    example = json.loads(
        files("faro.contracts")
        .joinpath("examples/handoff-case.json")
        .read_text(encoding="utf-8")
    )[2]
    handoff_id, sign_in = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime.now(UTC)
    filed_at = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    payload = {
        **example["payload"],
        "handoff_id": handoff_id,
        "session_id": sign_in,
        "request": {**example["payload"]["request"], "summary": markup["summary"]},
        "customer_statements": [markup["statement"]],
        "unresolved_questions": [markup["question"]],
    }
    reference = draw_reference()
    case = {
        **example,
        "pk": handoff_id,
        "handoff_id": handoff_id,
        "reference": reference,
        "payload": payload,
        "queue_order": queue_order(example["priority"], filed_at),
        "filed_at": filed_at,
        "record": {"sign_in": sign_in, "turns": [f"{filed_at}#{uuid.uuid4()}"]},
        "expires_at": int(now.timestamp()) + 3600,
    }
    validator("handoff-case").validate(case)
    table = cases(outputs)
    table.put_item(Item=case)
    table.put_item(Item=reference_item(reference, handoff_id, case["expires_at"]))
    return case


def test_the_console_shows_what_a_case_holds_as_text_never_as_markup(
    outputs: dict[str, Any],
    users: dict[str, User],
    site: str,
    tab: Callable[[str], Tab],
    saved: list[str],
) -> None:
    markup = {
        "summary": 'Resumen <script>alert("resumen")</script>',
        "statement": 'Dice <img src="x" onerror="alert(1)">',
        "question": "Pregunta <script>alert(2)</script>",
    }
    case = planted(outputs, markup)
    saved.append(case["handoff_id"])
    agent = tab("es")
    dialogs: list[str] = []

    def opened(dialog: Dialog) -> None:
        dialogs.append(dialog.type)
        dialog.dismiss()

    agent.page.on("dialog", opened)
    at_the_console(site, agent, users["staff"])

    agent.page.get_by_label(CONSOLE["reference"]).fill(case["reference"].lower())
    agent.page.get_by_role("button", name=CONSOLE["search"]).click()

    expect(
        agent.page.get_by_role("heading", level=2, name=case["reference"])
    ).to_be_visible()
    for text in markup.values():
        expect(agent.page.get_by_text(text, exact=True)).to_be_visible()
    assert agent.page.locator("article script, article img").count() == 0
    # The case is flagged as filed, each part named without its value.
    expect(agent.page.get_by_text(CONSOLE["flagged"])).to_be_visible()
    assert (dialogs, agent.violations, agent.errors) == ([], [], 0)


def test_another_sign_ins_runtime_session_is_refused(
    outputs: dict[str, Any],
    users: dict[str, User],
    site: str,
    tab: Callable[[str], Tab],
) -> None:
    first, second = tab("es"), tab("pt")
    first.sign_in(site, users["customer"])
    expect(first.page.get_by_label(first.texts["message"])).to_be_visible()
    warmed_up(outputs, first)
    taken = first.stored(RUNTIME_SESSION)
    second.sign_in(site, users["other_customer"])
    expect(second.page.get_by_label(second.texts["message"])).to_be_visible()

    second.page.evaluate(
        "([key, value]) => sessionStorage.setItem(key, value)", [RUNTIME_SESSION, taken]
    )
    second.page.reload()
    second.page.get_by_label(second.texts["message"]).fill("Quais são os meus cartões?")
    second.page.get_by_role("button", name=second.texts["send"]).click()

    expect(second.page.get_by_role("alert")).to_contain_text(
        second.texts["session_refused"]
    )
    access = second.stored(".accessToken")
    assert access is not None
    refused = [
        e
        for e in records(outputs, claims(access)["origin_jti"])
        if e["kind"] == "request_refused"
    ]
    assert refused and {e["code"] for e in refused} == {"session_refused"}
    assert second.violations == []


def test_signing_out_revokes_the_sign_in(
    outputs: dict[str, Any],
    users: dict[str, User],
    cognito: CognitoIdentityProviderClient,
    site: str,
    tab: Callable[[str], Tab],
) -> None:
    customer = tab("pt")
    customer.sign_in(site, users["other_customer"])
    expect(customer.page.get_by_label(customer.texts["message"])).to_be_visible()
    refresh = customer.stored(".refreshToken")
    assert refresh is not None

    customer.page.get_by_role("button", name=customer.texts["sign_out"]).click()

    expect(customer.page.get_by_label(customer.texts["username"])).to_be_visible()
    assert customer.page.evaluate("() => sessionStorage.length") == 0
    with pytest.raises(ClientError) as refused:
        cognito.admin_initiate_auth(
            UserPoolId=outputs["user_pool_id"],
            ClientId=outputs["customer_client_id"],
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters={"REFRESH_TOKEN": refresh},
        )
    assert refused.value.response["Error"]["Code"] == "NotAuthorizedException"
    assert customer.violations == []

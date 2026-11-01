"""
The check after each deploy (ADR-0009): the standing smoke user signs in through the site's form with SRP, reads its
cards through the Gateway with its own token, asks the chat about them, and must get a reply that names each one;
then it signs out. It holds no AWS credentials, so it waits for the turn on the Runtime's stream instead of the
execution record, and its question only reads (SEC-04, OPS-03, OPS-12). Assertions count and compare without printing
a reply, a token, or an ID, and nothing the page shows is saved.
"""

import os
from collections.abc import Iterator

import pytest
from playwright.sync_api import Page, Request, expect, sync_playwright

from tests.integration.test_browser import TEXTS, kind
from tests.integration.test_stack import arguments, call, tool_output

pytestmark = [pytest.mark.smoke, pytest.mark.timeout(300)]

TEXT = TEXTS["es"]
QUESTION = "¿Cuáles son mis tarjetas y en qué estado están?"


def setting(name: str) -> str:
    value = os.environ.get(f"SMOKE_{name}", "")
    if not value:
        pytest.fail(
            f"SMOKE_{name} is unset: make smoke reads it from the stack's outputs and"
            " from data/smoke/, which make smoke-user writes"
        )
    return value


@pytest.fixture
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        opened = browser.new_page(locale=TEXT["locale"])
        opened.set_default_timeout(90_000)
        expect.set_options(timeout=90_000)
        yield opened
        browser.close()


def message(request: Request) -> bool:
    return "bedrock-agentcore" in request.url and kind(request) == "message"


def test_the_smoke_user_reads_its_cards_in_the_chat(page: Page) -> None:
    page.goto(f"{setting('SITE')}/chat")
    page.get_by_label(TEXT["username"]).fill(setting("USERNAME"))
    page.get_by_label(TEXT["password"]).fill(setting("PASSWORD"))
    page.get_by_role("button", name=TEXT["submit"]).click()
    box = page.get_by_label(TEXT["message"])
    expect(box).to_be_visible()
    access = page.evaluate(
        "() => Object.entries(sessionStorage).find(([k]) => k.endsWith('.accessToken'))?.[1] ?? null"
    )
    assert access, "the sign-in stored no access token"
    gateway = {
        "gateway_url": setting("GATEWAY_URL"),
        "gateway_targets": {"list_cards": setting("LIST_CARDS_TARGET")},
    }
    listed = tool_output(call(gateway, access, "list_cards", arguments(access)))
    cards = {card["last_four"] for card in listed["cards"]}
    assert cards, "list_cards found no card of the smoke user's"

    replies = page.locator('[data-author="faro"]')
    before = replies.count()
    with page.expect_request_finished(message, timeout=120_000) as finished:
        box.fill(QUESTION)
        page.get_by_role("button", name=TEXT["send"]).click()
    response = finished.value.response()
    assert response is not None and response.status == 200, "the Runtime refused"
    expect(replies).to_have_count(before + 1)
    expect(page.get_by_role("status")).to_have_count(0)
    expect(page.get_by_role("alert")).to_have_count(0)
    reply = replies.last.inner_text()
    missing = sum(1 for last_four in cards if last_four not in reply)
    assert missing == 0, f"the reply leaves out {missing} of {len(cards)} cards"

    page.get_by_role("button", name=TEXT["sign_out"]).click()
    expect(page.get_by_label(TEXT["username"])).to_be_visible()

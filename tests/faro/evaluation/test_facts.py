"""
The oracle writes facts as ADR-0004's table under decision 8 says a reply states them: each row's example, written from
the same values, comes out as the table gives it.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from faro.evaluation import facts
from faro.evaluation.state import CREDIT, Card, Transaction


@pytest.fixture(scope="module")
def words() -> dict[str, Any]:
    return facts.contract_words()


def card(**values: Any) -> Card:
    return Card(
        **{
            "product_id": "PRD-EXAMPLE00002",
            "type": CREDIT,
            "last_four": "4821",
            "currency": "USD",
            "balance": Decimal("1240.55"),
            "limit": None,
            "status": "Active",
            "opening": date(2023, 2, 14),
            "expiration": date(2027, 3, 31),
            **values,
        }
    )


def charge(**values: Any) -> Transaction:
    return Transaction(
        **{
            "transaction_id": "TRX-EXAMPLE0000000000003",
            "product_id": "PRD-EXAMPLE00002",
            "at": datetime(2026, 6, 14, 21, 7, 33),
            "type": "Purchase",
            "amount": Decimal("1240.50"),
            "currency": "COP",
            "merchant": "Comercio Ejemplo",
            "country": "Colombia",
            "status": "Declined",
            "code": "51",
            "is_fraud": False,
            **values,
        }
    )


def test_amounts_are_grouped_as_the_customers_country_writes_them(
    words: dict[str, Any],
) -> None:
    value = Decimal("1240.55")

    assert facts.Facts("es", "México", words).amount(value, "USD") == "1,240.55 USD"
    assert facts.Facts("es", "Colombia", words).amount(value, "COP") == "1.240,55 COP"
    assert facts.Facts("pt", "Argentina", words).amount(value, "ARS") == "1.240,55 ARS"


def test_dates_cards_and_statuses(words: dict[str, Any]) -> None:
    es, pt = facts.Facts("es", "México", words), facts.Facts("pt", "México", words)

    assert es.as_of() == "17/06/2026"
    assert facts.moment(datetime(2026, 6, 14, 21, 7, 33)) == "14/06/2026 21:07"
    assert es.expiration(card(expiration=date(2026, 2, 13))) == "02/2026"
    assert es.expiration(card(expiration=None)) == "no registrada"
    assert pt.expiration(card(expiration=None)) == "não registrada"
    assert es.card(card()) == "tarjeta de crédito terminada en 4821"
    assert pt.card(card()) == "cartão de crédito final 4821"
    assert (es.status(card()), pt.status(card())) == ("activa", "ativo")
    assert es.window() == {
        "{window.from}": "20/03/2026 06:00",
        "{window.to}": "18/06/2026 06:00",
    }


def test_lists_are_laid_out_as_the_table_gives_them(words: dict[str, Any]) -> None:
    es, pt = facts.Facts("es", "Colombia", words), facts.Facts("pt", "Colombia", words)

    assert es.cards([card()]) == (
        "- Tarjeta de crédito terminada en 4821: activa; fecha de vencimiento: 03/2027"
    )
    assert es.card_list([card(), card(last_four="1177")]) == (
        "- Tarjeta de crédito terminada en 4821\n- Tarjeta de crédito terminada en 1177"
    )
    assert pt.card_list([card()]) == "- Cartão de crédito final 4821"
    assert es.page([charge()]) == (
        "- 14/06/2026 21:07 · Compra · Comercio Ejemplo · 1.240,50 COP · Rechazada"
    )
    assert (
        es.transaction(charge()) == "14/06/2026 21:07, Comercio Ejemplo, 1.240,50 COP"
    )
    # The one a reply found stands as a list of one.
    assert es.found(charge()) == "- 14/06/2026 21:07, Comercio Ejemplo, 1.240,50 COP"
    assert es.choices([charge(), charge()]) == "\n".join(
        ["- 14/06/2026 21:07, Comercio Ejemplo, 1.240,50 COP"] * 2
    )
    # POL-27, version 6: a transaction that isn't a purchase is named by its type, as the agent names it.
    assert pt.transaction(charge(type="Withdrawal", merchant=None)) == (
        "14/06/2026 21:07, saque, 1.240,50 COP"
    )


def test_a_page_names_the_merchant_of_a_purchase_and_a_country_abroad(
    words: dict[str, Any],
) -> None:
    es = facts.Facts("es", "Colombia", words)

    assert es.page_line(charge(type="Payment", status="Approved")) == (
        "- 14/06/2026 21:07 · Pago · 1.240,50 COP · Aprobada"
    )
    assert es.page_line(charge(merchant=None, country="Panamá")) == (
        "- 14/06/2026 21:07 · Compra · comercio no registrado · 1.240,50 COP · Rechazada · Panamá"
    )


def test_a_long_digit_run_in_a_record_is_masked() -> None:
    assert facts.masked("Pago 4111 1111 1111 4821 Ejemplo") == "Pago ****4821 Ejemplo"
    assert facts.masked("Tienda 123456789012") == "Tienda 123456789012"


def test_a_codes_meaning_and_a_transactions_status(words: dict[str, Any]) -> None:
    es, pt = facts.Facts("es", "Colombia", words), facts.Facts("pt", "Colombia", words)

    assert es.meaning(charge()) == "fondos insuficientes"
    assert pt.meaning(charge()) == "saldo insuficiente"
    assert es.reason(charge()) == "- Motivo: fondos insuficientes"
    assert pt.reason(charge()) == "- Motivo: saldo insuficiente"
    assert es.transaction_status(charge(status="Reversed")) == "revertida"

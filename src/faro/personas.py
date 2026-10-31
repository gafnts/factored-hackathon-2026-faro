"""
The judges' personas (ADR-0005; ADR-0007, Judges' access): one development customer per scenario, chosen by a fixed
rule from the pinned snapshot so that each stages a path of the demo. Only development customers are read: the first
query keeps their IDs, and every later one joins on them (DML-09). A persona's username is its customer's synthetic
name, lowercased and unaccented, and its briefing is built from the customer's records, so it is true by construction.
Names are identity data and the chosen IDs are row-level data: both are written under data/ and never committed or
printed (SEC-03).

- declines: two or more active credit cards, one of them with a recorded limit, and a decline with a listed code in
  the last week, so a block asks which card (POL-14) and the decline has an explanation (POL-02). The chat journey.
- dispute: one active credit card, with a recorded limit and approved purchases in the last week, so a charge the
  customer doesn't recognize has a card to block and a purchase to dispute (the README's journey).
- history: an active credit card with six or more transactions in the 90-day window, the busiest the data holds, so
  a page of movements has something to show.
- quiet: an active credit card with a recorded limit and no transaction in the window, for the empty answers.
- mixed: an active credit card and an active debit card side by side, so a card list crosses types.
- newcomer: an active credit card opened inside the window.
- premium: a Premium-segment customer with an active credit card and a recorded limit.
- debit: active debit cards only, one with a transaction in the last week, and no credit card at all.

Every persona is active, has no row updated after the as-of instant, and holds no conflicting record, so the demo
shows no flag it doesn't mean to. Among those who qualify, the one with the most card transactions in the 90-day
window is chosen, then the lowest customer_id; a customer or a username already chosen for an earlier scenario is
passed over, so eight accounts are eight people.
"""

import json
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
from duckdb import sqltypes

from faro.split import held_out

LISTED_CODES = ("05", "14", "51", "54")
CARD_TYPES = ("Tarjeta Crédito", "Tarjeta Débito")
MAX_CARDS = 50
WINDOW = timedelta(days=90)
RECENT = timedelta(days=7)
BUSY_CARD = 6

# The scenario sentence of each briefing; the rest is built from the records. Each promises only what its rule
# guarantees (ADR-0007, Judges' access).
HOOKS = {
    "declines": (
        "A purchase on one of your credit cards was declined this past week: ask why, "
        "or block a card and see Faro ask which one."
    ),
    "dispute": (
        "Your credit card has purchases from the past few days, and you don't recognize "
        "one of them: report it, confirm the block, and keep the reference you're given."
    ),
    "history": (
        "You use your credit card heavily: ask for its recent movements and look into "
        "any of them."
    ),
    "quiet": (
        "Your credit card has no movements this quarter: ask for its status, your "
        "available credit, or block it just in case."
    ),
    "mixed": (
        "You hold credit and debit side by side: ask about your cards and watch Faro "
        "tell them apart."
    ),
    "newcomer": (
        "You opened your credit card within the last three months: ask for its status "
        "and what has moved on it."
    ),
    "premium": (
        "You are a Premium customer with a recorded credit limit: ask how much credit "
        "you have available."
    ),
    "debit": "You bank on a debit card alone: ask about its recent activity, or block it.",
}

# The scenarios whose customers the browser journey and the tiny export stage (ADR-0006's amendment).
JOURNEYS = ("declines", "dispute")


class PersonaError(Exception):
    pass


@dataclass(frozen=True)
class Persona:
    customer_id: str
    username: str
    briefing: str


@dataclass(frozen=True)
class Personas:
    snapshot: str
    chosen: dict[str, Persona]
    # How many development customers met each rule; counts, never IDs, are what gets printed.
    qualifying: dict[str, int]


def path_for(data_dir: Path, snapshot: str) -> Path:
    return data_dir / "personas" / f"{snapshot}.json"


def username(first_name: str, last_name: str) -> str:
    decomposed = unicodedata.normalize("NFD", f"{first_name}.{last_name}".lower())
    ascii_only = (
        "".join(c for c in decomposed if not unicodedata.combining(c))
        .encode("ascii", "ignore")
        .decode()
    )
    return ".".join("".join(c if c.isalnum() else " " for c in ascii_only).split())


def _number(count: int) -> str:
    words = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
    return words[count - 1] if count <= len(words) else str(count)


def _cards_phrase(credit: int, debit: int) -> str:
    parts = []
    if credit:
        parts.append(f"{_number(credit)} credit card{'s' if credit > 1 else ''}")
    if debit:
        parts.append(f"{_number(debit)} debit card{'s' if debit > 1 else ''}")
    return " and ".join(parts)


def briefing(
    scenario: str,
    first_name: str,
    last_name: str,
    country: str,
    credit: int,
    debit: int,
) -> str:
    return (
        f"You are {first_name} {last_name}, a LATAM Bank customer in {country} holding "
        f"{_cards_phrase(credit, debit)}. {HOOKS[scenario]} Sign in at /chat and write "
        "to Faro in Spanish or Portuguese; it follows your language."
    )


def select(
    con: duckdb.DuckDBPyConnection, snapshot: str, business_date: date, as_of: datetime
) -> Personas:
    """
    Reads the raw_customers, raw_products, and raw_transactions views that analysis.source.connect opens.
    """
    con.create_function("held_out", held_out, [sqltypes.VARCHAR], sqltypes.BOOLEAN)
    con.execute(
        "create or replace temp table development_ids as "
        "select customer_id from raw_customers where not held_out(customer_id)"
    )
    con.execute(
        "create or replace temp table persona_customers as "
        "select c.customer_id, c.first_name, c.last_name, c.country, c.segment, "
        "  c.customer_status, cast(c.last_updated as timestamp) as last_updated "
        "from raw_customers c join development_ids d on d.customer_id = c.customer_id "
        "where cast(c.registration_date as timestamp) <= $as_of",
        {"as_of": as_of},
    )
    con.execute(
        "create or replace temp table persona_cards as "
        "select p.product_id, p.customer_id, p.product_type, p.product_status, "
        "  cast(p.credit_limit as decimal(15,2)) as credit_limit, "
        "  cast(p.opening_date as date) as opening_date, "
        "  cast(p.expiration_date as date) as expiration_date, "
        "  cast(p.last_updated as timestamp) as last_updated "
        "from raw_products p join persona_customers c on c.customer_id = p.customer_id "
        "where list_contains($card_types, p.product_type) and cast(p.opening_date as date) <= $as_of",
        {"card_types": list(CARD_TYPES), "as_of": as_of},
    )
    con.execute(
        "create or replace temp table persona_transactions as "
        "select k.customer_id, k.product_id, k.product_type, k.product_status, "
        "  cast(t.transaction_date as timestamp) as transaction_date, t.transaction_type, "
        "  t.transaction_status, t.response_code, "
        "  cast(cast(t.transaction_date as timestamp) as date) < k.opening_date as before_card_opening, "
        "  coalesce(cast(cast(t.transaction_date as timestamp) as date) > k.expiration_date, false) "
        "    as after_card_expiration "
        "from raw_transactions t join persona_cards k on k.product_id = t.product_id "
        "where cast(t.transaction_date as timestamp) > $start "
        "  and cast(t.transaction_date as timestamp) <= $as_of",
        {"start": as_of - WINDOW, "as_of": as_of},
    )
    rules = {
        "declines": """
            (select count(*) from persona_cards k where k.customer_id = c.customer_id
              and k.product_type = 'Tarjeta Crédito' and k.product_status = 'Active') >= 2
            and exists (select 1 from persona_cards k where k.customer_id = c.customer_id
              and k.product_type = 'Tarjeta Crédito' and k.product_status = 'Active'
              and k.credit_limit is not null)
            and exists (select 1 from persona_transactions t where t.customer_id = c.customer_id
              and t.product_status = 'Active' and t.transaction_status = 'Declined'
              and list_contains($codes, t.response_code) and t.transaction_date > $recent)
        """,
        "dispute": """
            (select count(*) from persona_cards k where k.customer_id = c.customer_id
              and k.product_type = 'Tarjeta Crédito' and k.product_status = 'Active') = 1
            and exists (select 1 from persona_cards k where k.customer_id = c.customer_id
              and k.product_type = 'Tarjeta Crédito' and k.product_status = 'Active'
              and k.credit_limit is not null
              and exists (select 1 from persona_transactions t where t.product_id = k.product_id
                and t.transaction_type = 'Purchase' and t.transaction_status = 'Approved'
                and t.transaction_date > $recent))
        """,
        "history": """
            exists (select 1 from persona_cards k where k.customer_id = c.customer_id
              and k.product_type = 'Tarjeta Crédito' and k.product_status = 'Active'
              and (select count(*) from persona_transactions t
                   where t.product_id = k.product_id) >= $busy)
        """,
        "quiet": """
            exists (select 1 from persona_cards k where k.customer_id = c.customer_id
              and k.product_type = 'Tarjeta Crédito' and k.product_status = 'Active'
              and k.credit_limit is not null)
            and not exists (select 1 from persona_transactions t
              where t.customer_id = c.customer_id)
        """,
        "mixed": """
            exists (select 1 from persona_cards k where k.customer_id = c.customer_id
              and k.product_type = 'Tarjeta Crédito' and k.product_status = 'Active')
            and exists (select 1 from persona_cards k where k.customer_id = c.customer_id
              and k.product_type = 'Tarjeta Débito' and k.product_status = 'Active')
        """,
        "newcomer": """
            exists (select 1 from persona_cards k where k.customer_id = c.customer_id
              and k.product_type = 'Tarjeta Crédito' and k.product_status = 'Active'
              and k.opening_date > $window_start)
        """,
        "premium": """
            c.segment = 'Premium'
            and exists (select 1 from persona_cards k where k.customer_id = c.customer_id
              and k.product_type = 'Tarjeta Crédito' and k.product_status = 'Active'
              and k.credit_limit is not null)
        """,
        "debit": """
            exists (select 1 from persona_cards k where k.customer_id = c.customer_id
              and k.product_type = 'Tarjeta Débito' and k.product_status = 'Active'
              and exists (select 1 from persona_transactions t where t.product_id = k.product_id
                and t.transaction_date > $recent))
            and not exists (select 1 from persona_cards k where k.customer_id = c.customer_id
              and k.product_type = 'Tarjeta Crédito')
        """,
    }
    clean = """
        c.customer_status = 'Active' and c.last_updated <= $as_of
        and (select count(*) from persona_cards k where k.customer_id = c.customer_id) <= $max_cards
        and not exists (select 1 from persona_cards k where k.customer_id = c.customer_id
          and (k.last_updated > $as_of
            or (k.product_status = 'Active' and k.expiration_date < $business_date)))
        and not exists (select 1 from persona_transactions t where t.customer_id = c.customer_id
          and (t.before_card_opening or t.after_card_expiration))
    """
    parameters = {
        "as_of": as_of,
        "business_date": business_date,
        "recent": as_of - RECENT,
        "window_start": (as_of - WINDOW).date(),
        "codes": list(LISTED_CODES),
        "max_cards": MAX_CARDS,
        "busy": BUSY_CARD,
    }
    chosen: dict[str, Persona] = {}
    qualifying: dict[str, int] = {}
    taken_ids: set[str] = set()
    taken_names: set[str] = set()
    for scenario, rule in rules.items():
        rows = con.execute(
            "select c.customer_id, c.first_name, c.last_name, c.country "
            f"from persona_customers c where {clean} and {rule} "
            "order by (select count(*) from persona_transactions t where t.customer_id = c.customer_id) desc, "
            "customer_id",
            {k: v for k, v in parameters.items() if f"${k}" in clean + rule},
        ).fetchall()
        qualifying[scenario] = len(rows)
        picked = next(
            (
                (customer_id, first, last, country)
                for customer_id, first, last, country in rows
                if customer_id not in taken_ids
                and username(first, last)
                and username(first, last) not in taken_names
            ),
            None,
        )
        if picked is None:
            raise PersonaError(
                f"no development customer meets the {scenario} persona's rule"
            )
        customer_id, first, last, country = picked
        credit, debit = con.execute(
            "select count(*) filter (where product_type = 'Tarjeta Crédito'), "
            "  count(*) filter (where product_type = 'Tarjeta Débito') "
            "from persona_cards where customer_id = $id and product_status = 'Active'",
            {"id": customer_id},
        ).fetchall()[0]
        taken_ids.add(customer_id)
        taken_names.add(username(first, last))
        chosen[scenario] = Persona(
            customer_id,
            username(first, last),
            briefing(scenario, first, last, country, credit, debit),
        )
    return Personas(snapshot, chosen, qualifying)


def write(path: Path, personas: Personas) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "snapshot": personas.snapshot,
        "personas": {
            scenario: {
                "customer_id": p.customer_id,
                "username": p.username,
                "briefing": p.briefing,
            }
            for scenario, p in personas.chosen.items()
        },
    }
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def read(path: Path, snapshot: str) -> dict[str, Persona]:
    if not path.is_file():
        raise PersonaError(f"{path} not found; run make personas")
    body = json.loads(path.read_text(encoding="utf-8"))
    records = body.get("personas", {})
    if body.get("snapshot") != snapshot or sorted(records) != sorted(HOOKS):
        raise PersonaError(
            f"{path} doesn't hold snapshot {snapshot}'s personas; run make personas"
        )
    chosen = {
        scenario: Persona(record["customer_id"], record["username"], record["briefing"])
        for scenario, record in records.items()
    }
    if any(held_out(p.customer_id) for p in chosen.values()):
        raise PersonaError(f"{path} names a held-out customer")
    return chosen

"""
The personas come from development customers only, one per scenario by a fixed rule that reaches a path of the demo,
each named by its customer's synthetic name and briefed from its records (ADR-0005; ADR-0007, Judges' access; DML-09,
SCP-03 to SCP-06, SEC-03).
"""

import json
from pathlib import Path

import duckdb
import pytest

from faro.personas import (
    HOOKS,
    Persona,
    PersonaError,
    Personas,
    path_for,
    read,
    select,
    username,
    write,
)
from faro.split import held_out

from .conftest import AS_OF, BUSINESS_DATE


def test_each_scenario_gets_the_development_customer_its_rule_picks(
    persona_con: duckdb.DuckDBPyConnection,
) -> None:
    personas = select(persona_con, "snap", BUSINESS_DATE, AS_OF)

    # 02 fits declines best but is held out; 01's decline has no code. 05 is busier than 04, 06 was updated after
    # the as-of instant, and 07's active card is past its expiration date. 14 is busier than 21 but repeats 03's
    # name, so mixed passes it over for the username.
    assert {s: p.customer_id for s, p in personas.chosen.items()} == {
        "declines": "CLI-TEAM00000003",
        "dispute": "CLI-TEAM00000005",
        "history": "CLI-TEAM00000011",
        "quiet": "CLI-TEAM00000012",
        "mixed": "CLI-TEAM00000021",
        "newcomer": "CLI-TEAM00000015",
        "premium": "CLI-TEAM00000016",
        "debit": "CLI-TEAM00000018",
    }
    assert personas.qualifying == {
        "declines": 1,
        "dispute": 2,
        "history": 1,
        "quiet": 2,
        "mixed": 2,
        "newcomer": 1,
        "premium": 1,
        "debit": 1,
    }
    assert held_out("CLI-TEAM00000002")


def test_usernames_are_the_synthetic_names_lowercased_and_unaccented(
    persona_con: duckdb.DuckDBPyConnection,
) -> None:
    personas = select(persona_con, "snap", BUSINESS_DATE, AS_OF)

    assert personas.chosen["declines"].username == "ana.maria.team"
    assert personas.chosen["dispute"].username == "joao.team"
    assert personas.chosen["mixed"].username == "team.mixed"
    usernames = [p.username for p in personas.chosen.values()]
    assert len(set(usernames)) == len(usernames)
    assert all(name.replace(".", "").isalnum() and name.isascii() for name in usernames)


def test_a_briefing_holds_the_records_facts_and_its_scenarios_hook(
    persona_con: duckdb.DuckDBPyConnection,
) -> None:
    personas = select(persona_con, "snap", BUSINESS_DATE, AS_OF)

    declines = personas.chosen["declines"].briefing
    assert "Ana María Team" in declines
    assert "México" in declines
    assert "two credit cards" in declines
    assert HOOKS["declines"] in declines
    # 05's debit card is blocked, so only the active card is counted.
    assert "holding one credit card." in personas.chosen["dispute"].briefing
    assert "one credit card and one debit card" in personas.chosen["mixed"].briefing
    assert "holding one debit card." in personas.chosen["debit"].briefing
    for scenario, persona in personas.chosen.items():
        assert HOOKS[scenario] in persona.briefing
        assert "Spanish or Portuguese" in persona.briefing


def test_no_held_out_customer_is_read_past_its_id(
    persona_con: duckdb.DuckDBPyConnection,
) -> None:
    select(persona_con, "snap", BUSINESS_DATE, AS_OF)

    for table in ("persona_customers", "persona_cards", "persona_transactions"):
        ids = persona_con.execute(
            f"select distinct customer_id from {table}"
        ).fetchall()
        assert ids
        assert not [i for (i,) in ids if held_out(i)], table


def test_a_rule_no_one_meets_is_an_error(
    persona_con: duckdb.DuckDBPyConnection,
) -> None:
    persona_con.execute("alter view raw_customers rename to all_customers")
    persona_con.execute(
        "create view raw_customers as "
        "select * from all_customers where customer_id <> 'CLI-TEAM00000003'"
    )

    with pytest.raises(PersonaError, match="declines persona"):
        select(persona_con, "snap", BUSINESS_DATE, AS_OF)


def test_a_name_turns_into_a_plain_dotted_username() -> None:
    assert username("Ana María", "Pérez-Team") == "ana.maria.perez.team"
    assert username("João", "D'Team") == "joao.d.team"


def fixture_personas(snapshot: str) -> Personas:
    # Development IDs only: 2, 8, and 9 are held out.
    development = (1, 3, 4, 5, 6, 7, 10, 11)
    chosen = {
        scenario: Persona(f"CLI-TEAM{n:08d}", f"team.{scenario}", f"brief {scenario}")
        for n, scenario in zip(development, sorted(HOOKS), strict=True)
    }
    return Personas(snapshot, chosen, {})


def test_the_file_round_trips_and_refuses_another_snapshot(tmp_path: Path) -> None:
    path = path_for(tmp_path / "data", "snap")
    personas = fixture_personas("snap")
    write(path, personas)

    assert read(path, "snap") == personas.chosen
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(PersonaError, match="make personas"):
        read(path, "other")
    with pytest.raises(PersonaError, match="make personas"):
        read(path_for(tmp_path / "data", "missing"), "missing")


def test_a_file_missing_a_scenario_is_refused(tmp_path: Path) -> None:
    path = path_for(tmp_path / "data", "snap")
    personas = fixture_personas("snap")
    body = {
        "snapshot": "snap",
        "personas": {
            s: {
                "customer_id": p.customer_id,
                "username": p.username,
                "briefing": p.briefing,
            }
            for s, p in personas.chosen.items()
            if s != "quiet"
        },
    }
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(body))

    with pytest.raises(PersonaError, match="make personas"):
        read(path, "snap")


def test_a_file_naming_a_held_out_customer_is_refused(tmp_path: Path) -> None:
    path = path_for(tmp_path, "snap")
    personas = fixture_personas("snap")
    body = {
        "snapshot": "snap",
        "personas": {
            s: {
                # 02 is held out.
                "customer_id": "CLI-TEAM00000002" if s == "declines" else p.customer_id,
                "username": p.username,
                "briefing": p.briefing,
            }
            for s, p in personas.chosen.items()
        },
    }
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(body))

    with pytest.raises(PersonaError, match="held-out"):
        read(path, "snap")

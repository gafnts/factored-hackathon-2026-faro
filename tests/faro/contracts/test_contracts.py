"""
The shared contracts are valid JSON Schema, agree with each other, the handoff schema, and the policy, and hold what
the policy and ADR-0004 say each boundary may carry (POL-11, POL-12, POL-40; SEC-06).
"""

import copy
import json
import re
from collections.abc import Iterator
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from faro.contracts import (
    NAMES,
    TOOLS,
    definition,
    schema,
    validator,
    version,
    words,
)
from faro.policy import handoff_schema

POLICY = Path(__file__).resolve().parents[3] / "docs" / "policy" / "card-support.md"
EXAMPLES = files("faro.contracts").joinpath("examples")


def table_rows(heading: str) -> list[list[str]]:
    text = POLICY.read_text(encoding="utf-8")
    section = text.split(f"\n{heading}\n", 1)[1].split("\n#", 1)[0]
    rows = re.findall(r"^\| `([a-z_]+)` \|(.*)\|$", section, re.MULTILINE)
    return [[code, *(cell.strip() for cell in rest.split("|"))] for code, rest in rows]


def example_files() -> list[str]:
    return sorted(p.name for p in EXAMPLES.iterdir() if p.name.endswith(".json"))


def examples(name: str) -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = json.loads(
        EXAMPLES.joinpath(name).read_text(encoding="utf-8")
    )
    return loaded


def walk(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def property_names(node: Any) -> set[str]:
    return {name for n in walk(node) for name in n.get("properties", {})}


@pytest.mark.parametrize("name", NAMES)
def test_each_contract_is_valid_json_schema(name: str) -> None:
    Draft202012Validator.check_schema(schema(name))


@pytest.mark.parametrize("name", NAMES)
def test_each_contract_is_at_version_1(name: str) -> None:
    assert version(name) == 1


def test_shared_definitions_agree_across_contracts() -> None:
    contracts = {name: schema(name)["$defs"] for name in NAMES}
    contracts["handoff"] = handoff_schema()["$defs"]
    seen: dict[str, tuple[str, Any]] = {}
    for name, defs in contracts.items():
        for key, value in defs.items():
            if key in seen:
                assert value == seen[key][1], (
                    f"{key} differs between {seen[key][0]} and {name}"
                )
            else:
                seen[key] = (name, value)


def test_labels_are_the_policy_requests() -> None:
    labels = {row[0] for row in table_rows("## Requests")}

    assert set(schema("execution-record")["$defs"]["label"]["enum"]) == labels


def test_reason_codes_are_the_handoff_schemas() -> None:
    codes = handoff_schema()["properties"]["reason_code"]["enum"]

    assert schema("execution-record")["$defs"]["reason_code"]["enum"] == codes


def test_the_handoff_control_offers_the_policys_offered_reasons_only() -> None:
    offered = {
        row[0] for row in table_rows("### Handoff reasons") if row[1] == "Offered"
    }

    assert set(schema("chat")["$defs"]["offered_reason_code"]["enum"]) == offered


def test_block_reasons_are_the_handoff_schemas() -> None:
    reasons = handoff_schema()["$defs"]["action"]["properties"]["reason"]["enum"]

    for name in ("chat", "execution-record"):
        assert schema(name)["$defs"]["block_reason"]["enum"] == reasons


def test_the_execution_record_names_every_tool() -> None:
    assert tuple(schema("execution-record")["$defs"]["tool"]["enum"]) == TOOLS


def test_every_defined_tool_has_an_input_and_an_output() -> None:
    defs = schema("tools")["$defs"]
    defined = {key.removesuffix("_output") for key in defs if key.endswith("_output")}

    assert defined <= set(TOOLS)
    for tool in defined:
        assert {f"{tool}_input", f"{tool}_output"} <= defs.keys()
        assert defs[f"{tool}_input"]["required"][:2] == ["customer_id", "origin_jti"]


@pytest.mark.parametrize(
    "field",
    [
        "is_fraud",
        "fraud_score",
        "customer_status",
        "product_number",
        "amount_usd",
        "document_number",
    ],
)
def test_no_tool_output_carries_what_the_policy_withholds(field: str) -> None:
    # POL-11, POL-12, POL-20, and POL-40: the Gateway's customers can call these tools without the agent.
    assert field not in property_names(schema("tools"))


@pytest.mark.parametrize(
    "field",
    [
        "product_number",
        "fraud_score",
        "amount_usd",
        "last_transaction_date",
        "document_number",
        "first_name",
        "last_name",
        "email",
        "mobile_phone",
        "address",
    ],
)
def test_the_tools_data_holds_nothing_gold_leaves_out(field: str) -> None:
    # ADR-0006, Gold: no full card number, identity or contact field, amount_usd, fraud_score, or last_transaction_date.
    assert field not in property_names(schema("tools-data"))


def test_is_fraud_lives_on_transactions_only() -> None:
    defs = schema("tools-data")["$defs"]

    holders = [
        key for key, value in defs.items() if "is_fraud" in value.get("properties", {})
    ]
    assert holders == ["transaction_item"]


def test_every_chat_event_is_closed() -> None:
    defs = schema("chat")["$defs"]
    for ref in defs["event"]["oneOf"]:
        event = defs[ref["$ref"].removeprefix("#/$defs/")]
        assert event["additionalProperties"] is False
        assert event["properties"]["type"]["const"] in {
            "RUN_STARTED",
            "RUN_FINISHED",
            "RUN_ERROR",
            "TEXT_MESSAGE_START",
            "TEXT_MESSAGE_CONTENT",
            "TEXT_MESSAGE_END",
            "MESSAGES_SNAPSHOT",
        }


@pytest.mark.parametrize("filename", example_files())
def test_each_example_is_valid(filename: str) -> None:
    name, _, ref = filename.removesuffix(".json").partition(".")
    check = validator(name, ref or None)
    for instance in examples(filename):
        check.validate(instance)


def test_every_example_file_names_a_contract() -> None:
    for filename in example_files():
        name, _, ref = filename.removesuffix(".json").partition(".")
        assert name in NAMES
        if ref:
            definition(name, ref)


def test_examples_cover_every_execution_record_kind() -> None:
    kinds = {entry["kind"] for entry in examples("execution-record.json")}

    assert kinds == set(schema("execution-record")["properties"]["kind"]["enum"])


def test_execution_record_examples_read_in_order() -> None:
    record = examples("execution-record.json")
    for sign_in in {e["sign_in"] for e in record}:
        entries = [e for e in record if e["sign_in"] == sign_in]
        keys = [e["entry_key"] for e in entries]
        assert keys == sorted(keys)
        for entry in entries:
            _, turn_id, seq = entry["entry_key"].split("#")
            assert (turn_id, int(seq)) == (entry["turn_id"], entry["seq"])


def invalid(name: str, ref: str | None, instance: Any) -> bool:
    return not validator(name, ref).is_valid(instance)


def first(filename: str) -> dict[str, Any]:
    return copy.deepcopy(examples(filename)[0])


def test_a_tool_input_names_nothing_beyond_its_contract() -> None:
    call = first("tools.get_card_input.json")
    call["customer_status"] = "Active"

    assert invalid("tools", "get_card_input", call)


def test_a_tool_input_needs_the_sign_in() -> None:
    call = first("tools.list_cards_input.json")
    del call["origin_jti"]

    assert invalid("tools", "list_cards_input", call)


def test_a_card_shows_four_digits_only() -> None:
    result = first("tools.get_card_output.json")
    result["card"]["last_four"] = "4123456789014821"

    assert invalid("tools", "get_card_output", result)


def test_invalid_input_names_the_rule_not_the_value() -> None:
    result = {
        "outcome": "invalid_input",
        "errors": [{"path": "/card_id", "rule": "4123456789014821"}],
    }

    assert invalid("tools", "list_cards_output", result)


def test_the_chat_may_not_send_state() -> None:
    request = first("chat.request.json")
    request["state"] = {"confirmation": {"status": "confirmed"}}

    assert invalid("chat", "request", request)


def test_the_chat_may_not_forward_a_node_or_a_command() -> None:
    request = first("chat.request.json")

    for props in ({"node_name": "block"}, {"command": {"resume": {"kind": "confirm"}}}):
        assert invalid("chat", "request", {**request, "forwardedProps": props})


def test_the_chat_may_not_send_tools() -> None:
    request = first("chat.request.json")
    request["tools"] = [{"name": "block_card", "description": "", "parameters": {}}]

    assert invalid("chat", "request", request)


def test_a_resume_is_resolved_and_names_a_control() -> None:
    request = copy.deepcopy(examples("chat.request.json")[2])
    entry = request["resume"][0]

    assert invalid(
        "chat", "request", {**request, "resume": [{**entry, "status": "cancelled"}]}
    )
    typed = {**entry, "payload": {"kind": "message", "text": "sim"}}
    assert invalid("chat", "request", {**request, "resume": [typed]})
    assert invalid("chat", "request", {**request, "resume": [entry, entry]})


def test_an_interrupt_holds_at_most_one_control_of_each_kind() -> None:
    value = first("chat.interrupt_value.json")
    value["controls"] = [value["controls"][0], value["controls"][0]]

    assert invalid("chat", "interrupt_value", value)


def test_a_reply_arrives_without_the_wrappers_extras() -> None:
    started = {
        "type": "RUN_STARTED",
        "threadId": "c7f3a1d2-4b5e",
        "runId": "1a2b3c4d-5e6f",
    }

    assert invalid("chat", "event", {**started, "input": {"messages": []}})
    assert invalid("chat", "event", {"type": "STATE_SNAPSHOT", "snapshot": {}})
    assert invalid(
        "chat", "event", {"type": "CUSTOM", "name": "on_interrupt", "value": {}}
    )


def test_the_snapshot_holds_customer_and_agent_messages_only() -> None:
    snapshot = {
        "type": "MESSAGES_SNAPSHOT",
        "messages": [
            {"id": "tool-call-0001", "role": "tool", "content": '{"is_fraud": false}'}
        ],
    }

    assert invalid("chat", "event", snapshot)


def test_the_execution_record_never_holds_a_card_number() -> None:
    entry = first("execution-record.json")
    entry["input"]["text"] = "Meu cartão 4123 4567 8901 4821 sumiu."

    assert invalid("execution-record", None, entry)


def test_an_execution_record_entry_holds_its_kinds_fields_only() -> None:
    entry = first("execution-record.json")
    entry["reply"] = "Olá"

    assert invalid("execution-record", None, entry)


def test_a_failed_tool_call_has_an_error_and_no_result() -> None:
    call = next(
        e for e in examples("execution-record.json") if e["kind"] == "tool_call"
    )
    failed = {**call, "outcome": "failed"}

    assert invalid("execution-record", None, failed)
    del failed["result"]
    assert invalid("execution-record", None, failed)
    assert not invalid(
        "execution-record",
        None,
        {**failed, "error": {"code": "timeout", "jsonrpc_code": None}},
    )


def test_a_refused_resume_says_why() -> None:
    resume = next(e for e in examples("execution-record.json") if e["kind"] == "resume")

    assert invalid("execution-record", None, {**resume, "accepted": False})
    assert not invalid(
        "execution-record", None, {**resume, "accepted": False, "refusal": "expired"}
    )


def test_unknown_contracts_and_definitions_are_refused() -> None:
    with pytest.raises(KeyError, match="no contract named"):
        schema("ops")
    with pytest.raises(KeyError, match="defines no"):
        definition("tools", "unblock_card_input")


def test_a_code_has_a_meaning_only_on_a_declined_transaction() -> None:
    # POL-28: Pending and Reversed transactions carry the same codes as declines.
    page = first("tools.find_transactions_output.json")
    approved, declined, pending = page["transactions"]

    approved["response_meaning"] = "do_not_honor"
    assert invalid("tools", "find_transactions_output", page)
    approved["response_meaning"] = None
    declined["response_meaning"] = None
    assert not invalid("tools", "find_transactions_output", page)
    pending["response_meaning"] = "do_not_honor"
    assert invalid("tools", "find_transactions_output", page)


def test_every_value_a_tool_returns_has_its_words_in_each_language() -> None:
    tools = schema("tools")["$defs"]
    transaction = tools["transaction"]["properties"]
    recorded = {
        "product_type": tools["product_type"]["enum"],
        "product_status": tools["product_status"]["enum"],
        "transaction_type": transaction["transaction_type"]["enum"],
        "transaction_status": transaction["transaction_status"]["enum"],
        "response_meaning": [m for m in transaction["response_meaning"]["enum"] if m],
    }
    languages = sorted(tools["language"]["enum"])
    stated = {k: v for k, v in words().items() if not k.startswith("$")}

    assert set(recorded) <= set(stated)
    for field, given in stated.items():
        assert sorted(given) == languages, field
        for language in languages:
            if field in recorded:
                assert sorted(given[language]) == sorted(recorded[field]), field
            else:
                assert isinstance(given[language], str), field


def test_a_page_holds_ten_transactions_at_most() -> None:
    page = first("tools.find_transactions_output.json")
    page["transactions"] = [page["transactions"][0]] * 11

    assert invalid("tools", "find_transactions_output", page)


def test_a_cursor_is_a_transactions_place_in_the_index() -> None:
    call = copy.deepcopy(examples("tools.find_transactions_input.json")[1])

    assert invalid(
        "tools",
        "find_transactions_input",
        {**call, "cursor": "TRX-EXAMPLE0000000000001"},
    )


def test_a_draft_carries_no_payload_and_a_filing_carries_its_turns() -> None:
    draft, filing = examples("tools.file_handoff_input.json")

    assert invalid(
        "tools", "file_handoff_input", {**draft, "payload": filing["payload"]}
    )
    assert invalid("tools", "file_handoff_input", {**draft, "mode": "file"})
    del filing["turns"]
    assert invalid("tools", "file_handoff_input", filing)


def test_a_draft_is_for_the_two_handoffs_a_confirmation_can_leave_owed() -> None:
    call = first("tools.file_handoff_input.json")

    for reason_code, reason in (
        ("tool_failure", "lost"),
        ("block_lapsed", "unrecognized_charge"),
        ("unrecognized_charge", "stolen"),
    ):
        draft = {**call["draft"], "reason_code": reason_code, "reason": reason}
        assert invalid("tools", "file_handoff_input", {**call, "draft": draft})


def test_file_handoff_adds_only_a_customers_status_and_a_transactions_flag() -> None:
    # POL-12 and POL-40: what no Gateway tool returns, and nothing else.
    filed = examples("tools.file_handoff_output.json")[1]
    status, flag = (
        next(f for f in filed["added_facts"] if f["field"] == field)
        for field in ("customer_status", "is_fraud")
    )

    for wrong in (
        {**status, "field": "is_fraud", "value": True},
        {**flag, "subject": "customer", "id": status["id"]},
        {**flag, "field": "merchant_name", "value": "Comercio"},
    ):
        assert invalid(
            "tools", "file_handoff_output", {**filed, "added_facts": [wrong]}
        )


def test_a_case_filed_again_adds_nothing_and_a_flag_names_its_errors() -> None:
    filed = examples("tools.file_handoff_output.json")[1]

    assert invalid("tools", "file_handoff_output", {**filed, "status": "already_filed"})
    assert invalid("tools", "file_handoff_output", {**filed, "flagged": True})
    errors = [{"path": "/customer_statements/0", "rule": "not"}]
    assert invalid(
        "tools", "file_handoff_output", {**filed, "validation_errors": errors}
    )


def cases() -> list[dict[str, Any]]:
    return [c for c in examples("handoff-case.json") if c["kind"] == "case"]


def test_every_filed_example_case_holds_a_valid_handoff() -> None:
    check = Draft202012Validator(
        handoff_schema(), format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    filed = [c for c in cases() if c["status"] != "draft"]

    assert filed
    for case in filed:
        check.validate(case["payload"])
        for field in ("handoff_id", "customer_id", "queue", "priority", "reason_code"):
            assert case[field] == case["payload"][field]
        assert case["language"] == case["payload"]["language"]
        assert case["record"]["sign_in"] == case["payload"]["session_id"]


def test_a_cases_rank_in_the_queue_is_its_priority() -> None:
    filed = copy.deepcopy(cases()[1])
    later = f"0#{filed['filed_at']}"

    assert filed["queue_order"] == later
    assert invalid("handoff-case", None, {**filed, "priority": "urgent"})
    urgent = {**filed, "priority": "urgent", "queue_order": f"1#{filed['filed_at']}"}
    assert not invalid("handoff-case", None, urgent)
    assert invalid("handoff-case", None, {**filed, "queue_order": filed["filed_at"]})


def test_a_case_is_in_the_queue_its_reason_names() -> None:
    filed = copy.deepcopy(cases()[1])

    assert invalid("handoff-case", None, {**filed, "queue": "customer_service"})


def test_a_filed_case_carries_no_claim_and_a_claimed_one_names_who() -> None:
    # The prototype defers claim and resolve (ADR-0007's amendment of 2026-09-30); the fields stay in the record.
    filed = copy.deepcopy(cases()[1])
    claimant = {"sub": "0b4a9c3e-5d2f-4e8a-9c71-2f6d8e1a7b50", "username": "agente1"}
    claimed = {
        **filed,
        "status": "claimed",
        "queue_key": "demo#dispute_intake#claimed",
        "claimed_at": "2026-10-02T16:00:00.000Z",
        "claimed_by": claimant,
    }

    assert invalid("handoff-case", None, {**filed, "claimed_by": claimant})
    assert not invalid("handoff-case", None, claimed)
    assert invalid("handoff-case", None, {**claimed, "status": "resolved"})
    del claimed["claimed_by"]
    assert invalid("handoff-case", None, claimed)


def test_a_draft_is_in_no_queue() -> None:
    draft = copy.deepcopy(cases()[0])

    assert draft["status"] == "draft"
    assert invalid(
        "handoff-case", None, {**draft, "queue_key": "demo#dispute_intake#filed"}
    )
    assert invalid("handoff-case", None, {**draft, "reference": "7K2M-9QXA"})


def test_a_reference_is_crockfords_base32_in_two_groups() -> None:
    item = next(c for c in examples("handoff-case.json") if c["kind"] == "reference")

    for reference in ("7K2M-9QXU", "7K2M9QXA", "7k2m-9qxa", "7K2M-9QXI"):
        assert invalid(
            "handoff-case",
            None,
            {**item, "reference": reference, "pk": f"REF#{reference}"},
        )


def test_a_block_needs_a_confirmation_and_one_of_the_policys_reasons() -> None:
    call = first("tools.block_card_input.json")

    assert invalid("tools", "block_card_input", {**call, "reason": "fraud"})
    del call["confirmation_id"]
    assert invalid("tools", "block_card_input", call)


def test_a_block_is_verified_only_by_a_read_back_that_shows_it() -> None:
    # POL-37 and AI-05: the outcome can't claim a block the sandbox doesn't show.
    verified = first("tools.block_card_output.json")

    assert invalid("tools", "block_card_output", {**verified, "read_back": "Active"})
    assert invalid(
        "tools",
        "block_card_output",
        {**verified, "block_outcome": "not_verified", "read_back": "Blocked"},
    )
    assert invalid("tools", "block_card_output", {**verified, "attempts": 4})


def test_a_refused_block_gives_a_status_only_for_a_card_that_isnt_active() -> None:
    refusals = [
        o for o in examples("tools.block_card_output.json") if o["outcome"] == "refused"
    ]
    not_confirmed, not_active = refusals

    assert invalid(
        "tools", "block_card_output", {**not_confirmed, "product_status": "Active"}
    )
    assert invalid(
        "tools", "block_card_output", {**not_active, "product_status": "Active"}
    )
    del not_active["product_status"]
    assert invalid("tools", "block_card_output", not_active)


def test_only_the_entrypoint_opens_turns() -> None:
    # ADR-0004's decision 7 is deferred, so no turn opens without a runtime session.
    opened = next(
        e for e in examples("execution-record.json") if e["kind"] == "turn_opened"
    )

    assert invalid("execution-record", None, {**opened, "input": {"kind": "deadline"}})
    for field in ("runtime_session_id", "client_thread_id", "client_run_id"):
        assert invalid("execution-record", None, {**opened, field: None})


# The console API (ADR-0007, The console API, and its amendments of 2026-09-30)


def test_the_console_takes_its_fields_from_the_case_record_and_the_payload() -> None:
    detail = first("console.case_detail.json")
    case = detail["case"]

    assert invalid(
        "console", "case_detail", {**detail, "case": {**case, "reason_code": "lost"}}
    )
    payload = {**case["payload"], "priority": "high"}
    assert invalid(
        "console", "case_detail", {**detail, "case": {**case, "payload": payload}}
    )


def test_each_example_case_reads_as_the_case_record_holds_it() -> None:
    held = {c["reference"]: c for c in cases() if c["status"] != "draft"}

    for detail in examples("console.case_detail.json"):
        case = detail["case"]
        assert all(case[k] == held[case["reference"]][k] for k in case)


def test_the_queue_never_takes_a_source() -> None:
    # An evaluation's cases never reach a human agent's queue (EVL-13).
    query = first("console.list_query.json")

    assert invalid("console", "list_query", {**query, "source": "evaluation"})
    assert invalid("console", "list_query", {**query, "limit": "51"})
    assert invalid("console", "list_query", {**query, "limit": "0"})


def test_a_queues_row_carries_nothing_of_the_payload() -> None:
    row = first("console.case_list.json")["cases"][0]

    assert set(schema("console")["$defs"]["case_row"]["properties"]) == set(row)
    assert invalid("console", "case_row", {**row, "summary": "Texto del cliente."})


def test_a_case_names_each_call_its_evidence_names_once() -> None:
    for detail in examples("console.case_detail.json"):
        evidence = [e["call_id"] for e in detail["case"]["payload"]["evidence"]]
        assert [c["call_id"] for c in detail["calls"]] == evidence


def test_a_call_not_yet_recorded_carries_nothing_but_its_id() -> None:
    call = examples("console.case_detail.json")[1]["calls"][0]

    assert not call["recorded"]
    assert invalid("console", "recorded_call", {**call, "tool": "file_handoff"})
    assert invalid("console", "recorded_call", {**call, "rows": []})


def test_a_recorded_call_never_carries_its_input_or_whole_result() -> None:
    call = first("console.case_detail.json")["calls"][0]

    assert invalid("console", "recorded_call", {**call, "input": {}})
    assert invalid("console", "recorded_call", {**call, "result": {}})


def test_a_failed_call_names_its_error_and_no_rows() -> None:
    ok = first("console.case_detail.json")["calls"][0]
    failed = {**ok, "outcome": "failed", "rows": []}

    assert invalid("console", "recorded_call", failed)
    assert not invalid("console", "recorded_call", {**failed, "error_code": "timeout"})
    assert invalid(
        "console",
        "recorded_call",
        {**failed, "error_code": "timeout", "rows": ok["rows"]},
    )
    assert invalid("console", "recorded_call", {**ok, "error_code": "timeout"})


def test_a_recorded_row_holds_scalar_fields_only() -> None:
    call = first("console.case_detail.json")["calls"][0]
    nested = {**call["rows"][0], "stamp": {"snapshot": "b3b8b248f604ef9a"}}

    assert invalid("console", "recorded_call", {**call, "rows": [nested]})


def test_a_fixture_carries_an_id_no_record_of_the_snapshot_can() -> None:
    card = first("overlay.fixture_card.json")
    transaction = first("overlay.fixture_transaction.json")
    short = "PRD-FIXA00000001"

    assert invalid(
        "overlay",
        "fixture_card",
        {**card, "card_id": short, "item": f"FIXTURE#CARD#{short}"},
    )
    assert invalid(
        "overlay",
        "fixture_transaction",
        {**transaction, "transaction_id": "TRX-FIX00000000000000001"},
    )
    assert invalid(
        "overlay",
        "fixture_transaction",
        {**transaction, "item": "FIXTURE#TRX#TRX-EXAMPLE0000000000003"},
    )


@pytest.mark.parametrize(
    ("fixture", "exported"),
    [("fixture_card", "card_item"), ("fixture_transaction", "transaction_item")],
)
def test_a_fixture_carries_the_exports_fields_under_the_overlays_keys(
    fixture: str, exported: str
) -> None:
    export = set(schema("tools-data")["$defs"][exported]["required"])
    item = set(schema("overlay")["$defs"][fixture]["required"])

    assert item == export - {"pk", "sk"} | {"sign_in", "item", "customer_id", "ttl"}


def test_a_planned_fault_names_a_failure_the_record_knows() -> None:
    planned = schema("overlay")["$defs"]["fault_plan"]["properties"]["error"]["enum"]
    answered = schema("tools")["$defs"]["fault"]["properties"]["error"]["enum"]
    entry = schema("execution-record")["$defs"]["tool_call_entry"]
    recorded = entry["properties"]["error"]["properties"]["code"]["enum"]

    assert planned == answered
    assert set(planned) == set(recorded) - {"transport", "denied"}


def test_only_the_gateways_tools_answer_a_planned_fault() -> None:
    fault = {"outcome": "fault", "error": "throttled"}
    gateway = schema("overlay")["$defs"]["gateway_tool"]["enum"]

    for tool in TOOLS:
        assert invalid("tools", f"{tool}_output", fault) == (tool not in gateway)
    assert invalid("tools", "get_card_output", {**fault, "error": "transport"})


def test_a_fault_plan_counts_down_to_none_and_no_further() -> None:
    plan = first("overlay.fault_plan.json")

    assert not invalid("overlay", "fault_plan", {**plan, "failures": 0})
    assert invalid("overlay", "fault_plan", {**plan, "failures": -1})
    assert invalid("overlay", "fault_plan", {**plan, "failures": 10})
    assert invalid("overlay", "fault_plan", {**plan, "item": "FAULT#file_handoff"})


def test_a_planned_failure_is_marked_and_a_real_one_isnt() -> None:
    failed = next(
        e
        for e in examples("execution-record.json")
        if e["kind"] == "tool_call" and e["outcome"] == "failed"
    )
    real = {**failed, "error": {"code": "timeout", "jsonrpc_code": None}}
    unmarked = {**failed, "error": {**failed["error"], "planned": False}}

    assert failed["error"]["planned"] is True
    assert not invalid("execution-record", None, real)
    assert invalid("execution-record", None, unmarked)

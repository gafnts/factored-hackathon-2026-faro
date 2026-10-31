"""
The deployed stack holds its boundaries: each user signs in through their side's client only, a customer can't rewrite
their customer_id, the Gateway and Cedar serve a customer their own ID and sign-in only, the read tools answer from the
customer's own partition of the tools' data and nothing more, the Runtime accepts customers' access tokens only, and
every log group keeps a retention (ADR-0004, To verify on the first deploy; ADR-0006, To verify on the first deploy;
ADR-0007, Sign-in; POL-07, POL-08, POL-11, POL-12, POL-40; SEC-04, SEC-05; CTL-04; EVL-04; OPS-10). Spikes S3 and S4
found the first of these by hand.
"""

import json
import re
import uuid
from typing import Any

import boto3
import httpx
import pytest
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError
from mypy_boto3_cognito_idp import CognitoIdentityProviderClient

from faro.contracts import validator

from .conftest import SignIn, User, claims

pytestmark = pytest.mark.integration

DENIED = -32002


def mcp(
    outputs: dict[str, Any], token: str | None, method: str, params: dict[str, Any]
) -> tuple[int, Any]:
    headers = {"Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    response = httpx.post(
        outputs["gateway_url"], json=body, headers=headers, timeout=30
    )
    text = response.text
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        text = next(
            (ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")), ""
        )
    try:
        return response.status_code, json.loads(text)
    except json.JSONDecodeError:
        return response.status_code, text


def call(
    outputs: dict[str, Any], token: str, tool: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    name = f"{outputs['gateway_targets'][tool]}___{tool}"
    status, body = mcp(
        outputs, token, "tools/call", {"name": name, "arguments": arguments}
    )
    assert status == 200
    assert isinstance(body, dict)
    return body


def tool_output(body: dict[str, Any]) -> dict[str, Any]:
    text = body["result"]["content"][0]["text"]
    output: dict[str, Any] = json.loads(text)
    return output


def arguments(access: str, **overrides: str) -> dict[str, Any]:
    """
    A call as the agent makes it: the token's customer and sign-in, unless a test names others.
    """
    token = claims(access)
    return {
        "customer_id": token.get("customer_id", "CLI-ITEST0000404"),
        "origin_jti": token["origin_jti"],
        **overrides,
    }


def fits(tool: str, output: dict[str, Any]) -> bool:
    return validator("tools", f"{tool}_output").is_valid(output)


def invoke(
    outputs: dict[str, Any], token: str | None, origin: str | None = None
) -> httpx.Response:
    headers = {
        "Accept": "text/event-stream",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": f"it-session-{uuid.uuid4().hex}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = {
        "threadId": f"thread-{uuid.uuid4().hex[:12]}",
        "runId": f"run-{uuid.uuid4().hex[:12]}",
        "state": {},
        "messages": [{"id": uuid.uuid4().hex, "role": "user", "content": "Hola"}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    return httpx.post(outputs["invoke_url"], json=body, headers=headers, timeout=120)


def events(response: httpx.Response) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data:"))
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]


# Sign-in (ADR-0007)


def test_a_customers_access_token_carries_their_customer_id_and_group(
    users: dict[str, User], sign_in: SignIn
) -> None:
    token = claims(sign_in(users["customer"], "customer")["access"])

    own = token["customer_id"] == users["customer"].customer_id
    assert own, "the token doesn't carry the customer's own ID"
    assert "customer" in token["cognito:groups"]


def test_a_staff_token_carries_no_customer_id(
    users: dict[str, User], sign_in: SignIn
) -> None:
    token = claims(sign_in(users["staff"], "staff")["access"])

    assert "customer_id" not in token
    assert token["cognito:groups"] == ["human_agent"]


@pytest.mark.parametrize(
    ("role", "client"),
    [
        ("customer", "staff"),
        ("evaluation", "staff"),
        ("staff", "customer"),
        ("ai_team", "customer"),
        ("no_group", "customer"),
        ("no_group", "staff"),
    ],
)
def test_a_user_gets_no_token_from_the_other_sides_client(
    users: dict[str, User], sign_in: SignIn, role: str, client: str
) -> None:
    with pytest.raises(ClientError):
        sign_in(users[role], client)


def test_a_customer_cant_rewrite_their_customer_id(
    outputs: dict[str, Any],
    users: dict[str, User],
    sign_in: SignIn,
    cognito: CognitoIdentityProviderClient,
) -> None:
    public = boto3.client(
        "cognito-idp",
        region_name="us-east-1",
        config=Config(signature_version=UNSIGNED),
    )
    customer = users["customer"]
    access = sign_in(customer, "customer")["access"]

    other = users["other_customer"].customer_id
    assert other is not None
    with pytest.raises(ClientError):
        public.update_user_attributes(
            AccessToken=access,
            UserAttributes=[{"Name": "custom:customer_id", "Value": other}],
        )
    public.update_user_attributes(
        AccessToken=access, UserAttributes=[{"Name": "locale", "Value": "pt-BR"}]
    )

    stored = cognito.admin_get_user(
        UserPoolId=outputs["user_pool_id"], Username=customer.username
    )["UserAttributes"]
    kept = {"Name": "custom:customer_id", "Value": customer.customer_id} in stored
    assert kept, "the user's customer_id changed"
    token = claims(sign_in(customer, "customer")["access"])
    carried = token["customer_id"] == customer.customer_id
    assert carried, "the token's customer_id changed"


# The Gateway and Cedar (ADR-0004, spike S3)


def test_the_gateway_turns_away_a_missing_id_or_staff_token(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn
) -> None:
    assert mcp(outputs, None, "tools/list", {})[0] == 401
    assert mcp(outputs, sign_in(users["customer"], "customer")["id"], "tools/list", {})[
        0
    ] in (401, 403)
    assert mcp(outputs, sign_in(users["staff"], "staff")["access"], "tools/list", {})[
        0
    ] in (401, 403)


def test_a_customer_lists_the_read_tools_and_the_block(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn
) -> None:
    access = sign_in(users["customer"], "customer")["access"]

    status, body = mcp(outputs, access, "tools/list", {})

    assert status == 200
    names = {tool["name"] for tool in body["result"]["tools"]}
    assert names == {
        "reads___list_cards",
        "reads___get_card",
        "reads___get_available_credit",
        "reads___find_transactions",
        "block___block_card",
    }
    assert outputs["gateway_targets"] == {
        "list_cards": "reads",
        "get_card": "reads",
        "get_available_credit": "reads",
        "find_transactions": "reads",
        "block_card": "block",
    }


def test_a_customers_own_call_reaches_the_tool_which_validates_it(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn
) -> None:
    access = sign_in(users["customer"], "customer")["access"]

    body = call(
        outputs, access, "get_card", arguments(access, card_id="4123456789014821")
    )

    assert tool_output(body) == {
        "outcome": "invalid_input",
        "errors": [{"path": "/card_id", "rule": "pattern"}],
    }


@pytest.mark.parametrize("whose", ["other_customer", "lowercase"])
def test_cedar_denies_another_customers_id(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn, whose: str
) -> None:
    access = sign_in(users["customer"], "customer")["access"]
    own, other = users["customer"].customer_id, users["other_customer"].customer_id
    assert own is not None and other is not None
    customer_id = other if whose == "other_customer" else own.lower()

    for tool, extra in (
        ("list_cards", {}),
        ("get_card", {"card_id": "PRD-ITEST0000001"}),
        ("get_available_credit", {"card_id": "PRD-ITEST0000001"}),
        ("find_transactions", {"card_id": "PRD-ITEST0000001"}),
    ):
        body = call(
            outputs, access, tool, arguments(access, customer_id=customer_id, **extra)
        )
        assert body["error"]["code"] == DENIED, tool


def test_cedar_denies_another_sign_ins_origin_jti(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn
) -> None:
    access = sign_in(users["customer"], "customer")["access"]
    earlier = claims(sign_in(users["customer"], "customer")["access"])["origin_jti"]
    another = earlier != claims(access)["origin_jti"]
    assert another, "two sign-ins share an origin_jti"

    for origin_jti in (earlier, str(uuid.uuid4())):
        body = call(
            outputs, access, "list_cards", arguments(access, origin_jti=origin_jti)
        )
        assert body["error"]["code"] == DENIED


def test_cedar_denies_a_customer_without_the_claim(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn
) -> None:
    access = sign_in(users["no_claim"], "customer")["access"]
    assert "customer_id" not in claims(access)

    body = call(
        outputs,
        access,
        "get_card",
        arguments(access, customer_id="CLI-ITEST0000001", card_id="PRD-ITEST0000001"),
    )

    assert body["error"]["code"] == DENIED


# The tools' data (ADR-0006, decision 1; SEC-05, CTL-04)


def own_cards(outputs: dict[str, Any], access: str) -> dict[str, Any]:
    output = tool_output(call(outputs, access, "list_cards", arguments(access)))
    assert output["outcome"] == "ok"
    return output


@pytest.mark.parametrize("role", ["customer", "other_customer"])
def test_a_customer_lists_their_own_cards_as_of_the_export(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn, role: str
) -> None:
    output = own_cards(outputs, sign_in(users[role], "customer")["access"])

    assert fits("list_cards", output)
    assert output["cards"]
    assert output["customer"]["served_in_full"] is True
    assert output["stamp"] == outputs["tools_data"]["stamp"]
    assert output["clock"] == {
        "business_date": "2026-06-17",
        "as_of": "2026-06-18 06:00:00",
    }


def test_a_customer_reads_each_of_their_cards(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn
) -> None:
    access = sign_in(users["customer"], "customer")["access"]

    for listed in own_cards(outputs, access)["cards"]:
        output = tool_output(
            call(
                outputs,
                access,
                "get_card",
                arguments(access, card_id=listed["card_id"]),
            )
        )
        assert fits("get_card", output)
        assert output["outcome"] == "ok"
        same = {k: output["card"][k] for k in listed} == listed
        assert same, "get_card and list_cards disagree on a card"


def test_another_customers_card_reads_as_a_card_that_doesnt_exist(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn
) -> None:
    access = sign_in(users["customer"], "customer")["access"]
    theirs = own_cards(outputs, sign_in(users["other_customer"], "customer")["access"])
    missing = tool_output(
        call(outputs, access, "get_card", arguments(access, card_id="PRD-ITEST0000404"))
    )

    assert missing["outcome"] == "not_found"
    for card in theirs["cards"]:
        output = tool_output(
            call(
                outputs, access, "get_card", arguments(access, card_id=card["card_id"])
            )
        )
        assert output == missing


def test_two_customers_share_no_card(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn
) -> None:
    mine, theirs = (
        {
            c["card_id"]
            for c in own_cards(outputs, sign_in(users[role], "customer")["access"])[
                "cards"
            ]
        }
        for role in ("customer", "other_customer")
    )

    assert mine and theirs
    assert not mine & theirs


def pages(outputs: dict[str, Any], access: str, card_id: str) -> list[dict[str, Any]]:
    found = [
        tool_output(
            call(
                outputs, access, "find_transactions", arguments(access, card_id=card_id)
            )
        )
    ]
    while found[-1].get("next_cursor"):
        more = arguments(access, card_id=card_id, cursor=found[-1]["next_cursor"])
        found.append(tool_output(call(outputs, access, "find_transactions", more)))
    return found


@pytest.mark.parametrize("role", ["customer", "other_customer"])
def test_a_customer_lists_each_cards_transactions_in_the_window(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn, role: str
) -> None:
    access = sign_in(users[role], "customer")["access"]

    found = [
        page
        for card in own_cards(outputs, access)["cards"]
        for page in pages(outputs, access, card["card_id"])
    ]

    assert all(fits("find_transactions", p) and p["outcome"] == "ok" for p in found)
    listed = [t for page in found for t in page["transactions"]]
    assert listed, "each persona has transactions in the window"
    window = {"from": "2026-03-20 06:00:00", "to": "2026-06-18 06:00:00"}
    assert all(window["from"] < t["transaction_date"] <= window["to"] for t in listed)
    for t in listed:
        explained = t["transaction_status"] == "Declined" and t["response_code"] in (
            "05",
            "14",
            "51",
            "54",
        )
        assert (t["response_meaning"] is not None) is explained


def test_another_customers_card_has_no_transactions_to_read(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn
) -> None:
    access = sign_in(users["customer"], "customer")["access"]
    theirs = own_cards(outputs, sign_in(users["other_customer"], "customer")["access"])

    for card in theirs["cards"]:
        output = tool_output(
            call(
                outputs,
                access,
                "find_transactions",
                arguments(access, card_id=card["card_id"]),
            )
        )
        assert output["outcome"] == "not_found"
        assert set(output) == {"outcome", "stamp", "clock"}


def test_no_output_carries_what_the_customer_mustnt_see(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn
) -> None:
    access = sign_in(users["customer"], "customer")["access"]
    listed = own_cards(outputs, access)
    read = [
        tool_output(
            call(outputs, access, "get_card", arguments(access, card_id=c["card_id"]))
        )
        for c in listed["cards"]
    ]

    found = [
        page for c in listed["cards"] for page in pages(outputs, access, c["card_id"])
    ]

    text = json.dumps([listed, *read, *found])
    for withheld in ("is_fraud", "customer_status", "current_balance", "credit_limit"):
        assert withheld not in text
    assert not re.search(r"\d{13,}", text)


def test_a_customer_the_data_doesnt_hold_gets_no_ones_cards(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn
) -> None:
    access = sign_in(users["unknown_customer"], "customer")["access"]

    listed = call(outputs, access, "list_cards", arguments(access))
    read = tool_output(
        call(outputs, access, "get_card", arguments(access, card_id="PRD-ITEST0000001"))
    )

    # A Lambda that fails reaches the caller as an error result with generic text, never the exception's message.
    assert listed["result"]["isError"] is True
    assert "customer" not in json.dumps(listed)
    assert read["outcome"] == "not_found"


def test_the_tools_data_holds_exactly_the_export(outputs: dict[str, Any]) -> None:
    dynamodb = boto3.client("dynamodb", region_name="us-east-1")
    table = outputs["tools_data"]["table"]
    arn = dynamodb.describe_table(TableName=table)["Table"]["TableArn"]
    imports = dynamodb.list_imports(TableArn=arn)["ImportSummaryList"]
    assert len(imports) == 1
    described = dynamodb.describe_import(ImportArn=imports[0]["ImportArn"])
    imported = described["ImportTableDescription"]
    total = outputs["tools_data"]["items"]["total"]

    assert imported["ImportStatus"] == "COMPLETED"
    assert imported.get("ErrorCount", 0) == 0
    assert imported["ImportedItemCount"] == total
    pages = dynamodb.get_paginator("scan").paginate(TableName=table, Select="COUNT")
    assert sum(page["Count"] for page in pages) == total
    meta = dynamodb.get_item(
        TableName=table,
        Key={"pk": {"S": "META"}, "sk": {"S": "META"}},
        ProjectionExpression="stamp",
    )["Item"]["stamp"]["M"]
    assert {k: v["S"] for k, v in meta.items()} == outputs["tools_data"]["stamp"]


def test_the_tools_data_carries_its_environment_tag(outputs: dict[str, Any]) -> None:
    dynamodb = boto3.client("dynamodb", region_name="us-east-1")
    table = dynamodb.describe_table(TableName=outputs["tools_data"]["table"])["Table"]

    tags = dynamodb.list_tags_of_resource(ResourceArn=table["TableArn"])["Tags"]

    assert {"Key": "Environment", "Value": outputs["environment"]} in tags


def test_the_read_tools_may_name_every_attribute_but_is_fraud(
    outputs: dict[str, Any],
) -> None:
    iam: Any = boto3.client("iam")
    document = iam.get_role_policy(
        RoleName=f"{outputs['prefix']}-reads", PolicyName="reads"
    )["PolicyDocument"]
    reads = next(s for s in document["Statement"] if "dynamodb:GetItem" in s["Action"])
    allowed = reads["Condition"]["ForAllValues:StringEquals"]["dynamodb:Attributes"]

    assert "is_fraud" not in allowed
    assert {"pk", "sk", "customer_status", "last_four"} <= set(allowed)
    assert (
        reads["Condition"]["StringEquals"]["dynamodb:Select"] == "SPECIFIC_ATTRIBUTES"
    )
    assert "dynamodb:Scan" not in reads["Action"]
    index = next(
        s for s in document["Statement"] if "/index/by_card" in str(s["Resource"])
    )
    assert index["Action"] in ("dynamodb:Query", ["dynamodb:Query"])
    assert index["Condition"] == reads["Condition"]


def dynamodb_grants(role: str, policy: str) -> dict[str, set[str]]:
    """
    Each DynamoDB table the role's inline policy names, with the actions it allows there.
    """
    iam: Any = boto3.client("iam")
    document = iam.get_role_policy(RoleName=role, PolicyName=policy)["PolicyDocument"]
    grants: dict[str, set[str]] = {}
    for statement in document["Statement"]:
        actions = statement["Action"]
        actions = {actions} if isinstance(actions, str) else set(actions)
        resources = statement["Resource"]
        resources = [resources] if isinstance(resources, str) else resources
        for resource in resources:
            if ":table/" in resource:
                table = resource.split(":table/")[1]
                grants.setdefault(table, set()).update(
                    a for a in actions if a.startswith("dynamodb:")
                )
    return grants


def overlay_statements(role: str, policy: str, overlay: str) -> list[dict[str, Any]]:
    iam: Any = boto3.client("iam")
    document = iam.get_role_policy(RoleName=role, PolicyName=policy)["PolicyDocument"]
    return [
        s
        for s in document["Statement"]
        if str(s["Resource"]).endswith(f":table/{overlay}")
    ]


def test_only_the_block_writes_the_sandbox_and_it_never_reads_is_fraud(
    outputs: dict[str, Any],
) -> None:
    # ADR-0004, Operations, and its amendments of 2026-10-01: of the overlay, the reads may change a fault plan's count
    # alone, no tool reads is_fraud there, file_handoff reads a fixture's, and the Runtime never touches the sandbox.
    prefix = outputs["prefix"]
    overlay = outputs["sandbox_tables"]["overlay"]
    confirmations = outputs["sandbox_tables"]["confirmations"]
    tools_data = outputs["tools_data"]["table"]
    counters = outputs["runtime_tables"]["usage_counters"]

    reads = dynamodb_grants(f"{prefix}-reads", "reads")
    block = dynamodb_grants(f"{prefix}-block", "block")
    runtime = dynamodb_grants(f"{prefix}-runtime", "runtime")
    filing = dynamodb_grants(f"{prefix}-file-handoff", "file-handoff")

    assert reads[overlay] == {
        "dynamodb:GetItem",
        "dynamodb:Query",
        "dynamodb:UpdateItem",
    }
    assert confirmations not in reads
    assert block == {
        tools_data: {"dynamodb:GetItem"},
        overlay: {"dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"},
        confirmations: {"dynamodb:GetItem", "dynamodb:UpdateItem"},
    }
    assert filing[overlay] == {"dynamodb:GetItem"}
    assert overlay not in runtime and tools_data not in runtime
    assert runtime[confirmations] == {"dynamodb:PutItem", "dynamodb:UpdateItem"}
    assert runtime[counters] == {"dynamodb:UpdateItem"}
    for role in ("reads", "block"):
        for statement in overlay_statements(f"{prefix}-{role}", role, overlay):
            actions = statement["Action"]
            if "dynamodb:PutItem" in actions:
                assert role == "block" and "Condition" not in statement
                continue
            condition = statement["Condition"]
            named = set(condition["ForAllValues:StringEquals"]["dynamodb:Attributes"])
            if "dynamodb:UpdateItem" in actions:
                assert named == {"sign_in", "item", "customer_id", "failures"}
                assert condition["StringEquals"]["dynamodb:ReturnValues"] == "NONE"
            else:
                assert "is_fraud" not in named and "failures" in named
                assert (
                    condition["StringEquals"]["dynamodb:Select"]
                    == "SPECIFIC_ATTRIBUTES"
                )
    iam: Any = boto3.client("iam")
    document = iam.get_role_policy(RoleName=f"{prefix}-block", PolicyName="block")
    statement = next(
        s
        for s in document["PolicyDocument"]["Statement"]
        if tools_data in str(s["Resource"])
    )
    allowed = statement["Condition"]["ForAllValues:StringEquals"]["dynamodb:Attributes"]
    assert "is_fraud" not in allowed
    assert (
        statement["Condition"]["StringEquals"]["dynamodb:Select"]
        == "SPECIFIC_ATTRIBUTES"
    )


# The Runtime (ADR-0004, spike S4)


def test_the_runtime_answers_the_browsers_preflight(outputs: dict[str, Any]) -> None:
    response = httpx.options(
        outputs["invoke_url"],
        headers={
            "Origin": "https://example.cloudfront.net",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": (
                "authorization,content-type,x-amzn-bedrock-agentcore-runtime-session-id"
            ),
        },
        timeout=30,
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers


def test_the_runtime_turns_away_a_missing_id_or_staff_token(
    outputs: dict[str, Any], users: dict[str, User], sign_in: SignIn
) -> None:
    assert invoke(outputs, None).status_code == 401
    assert (
        invoke(outputs, sign_in(users["customer"], "customer")["id"]).status_code == 401
    )
    assert invoke(outputs, sign_in(users["staff"], "staff")["access"]).status_code in (
        401,
        403,
    )


# Retention (OPS-10, spike S4)


def test_every_log_group_of_the_stack_keeps_a_retention(
    outputs: dict[str, Any],
) -> None:
    logs = boto3.client("logs", region_name="us-east-1")
    prefixes = [
        f"/aws/lambda/{outputs['prefix']}-",
        outputs["runtime_log_group"].removesuffix("-DEFAULT"),
        "/aws-dynamodb/imports",
        outputs["console"]["access_log_group"],
    ]
    groups = [
        group
        for prefix in prefixes
        for page in logs.get_paginator("describe_log_groups").paginate(
            logGroupNamePrefix=prefix
        )
        for group in page["logGroups"]
    ]

    assert {g["logGroupName"] for g in groups} >= {
        outputs["runtime_log_group"],
        "/aws-dynamodb/imports",
        outputs["console"]["access_log_group"],
        *(f"/aws/lambda/{f}" for f in outputs["console"]["functions"].values()),
    }
    assert [g["logGroupName"] for g in groups if "retentionInDays" not in g] == []

"""
The console API's Lambdas, behind API Gateway's JWT authorizer (ADR-0007, The console API, and its amendments of
2026-09-30). queue_handler answers GET /api/cases from the by_queue index of the table in CASES_TABLE; case_handler
answers GET /api/cases/{reference} from that table and from the turns of EXECUTION_RECORDS_TABLE a case names. Both
accept access tokens of the app client in STAFF_CLIENT_ID only, and check every answer against the console's contract
before sending it. Neither logs a case's content; case_handler logs who read which reference, as a bank audits reads of
a customer's case (OPS-02).
"""

import json
import logging
import os
from collections.abc import Callable
from functools import cache
from typing import Any

import boto3

from faro.console.access import Staff, staff_of
from faro.console.case import CaseBook, read_case
from faro.console.queue import Answer, DynamoQueue, QueueIndex, list_cases
from faro.contracts import validator
from faro.tools.cases import DynamoCases
from faro.tools.provenance import DynamoRecords, Records

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

FORBIDDEN: Answer = (403, {"error": "forbidden"})


def respond(answer: Answer, shape: str) -> dict[str, Any]:
    status, body = answer
    validator("console", shape if status == 200 else "refusal").validate(body)
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def caller(event: Any) -> Staff | None:
    return staff_of(event, os.environ["STAFF_CLIENT_ID"])


@cache
def queue_index() -> QueueIndex:
    return DynamoQueue(boto3.client("dynamodb"), os.environ["CASES_TABLE"])


@cache
def case_stores() -> tuple[CaseBook, Records]:
    dynamodb = boto3.client("dynamodb")
    return (
        DynamoCases(dynamodb, os.environ["CASES_TABLE"]),
        DynamoRecords(dynamodb, os.environ["EXECUTION_RECORDS_TABLE"]),
    )


def answer_queue(
    event: Any, index: Callable[[], QueueIndex], staff: Staff | None
) -> dict[str, Any]:
    if staff is None:
        return respond(FORBIDDEN, "case_list")
    query = event.get("queryStringParameters") if isinstance(event, dict) else None
    return respond(list_cases(index(), query), "case_list")


def answer_case(
    event: Any,
    stores: Callable[[], tuple[CaseBook, Records]],
    staff: Staff | None,
) -> dict[str, Any]:
    if staff is None:
        return respond(FORBIDDEN, "case_detail")
    parameters = event.get("pathParameters") if isinstance(event, dict) else None
    reference = parameters.get("reference") if isinstance(parameters, dict) else None
    book, records = stores()
    answer = read_case(book, records, reference)
    if answer[0] == 200:
        logger.info("case read", extra={"reference": reference, "reader": staff.sub})
    return respond(answer, "case_detail")


def queue_handler(event: Any, context: Any) -> dict[str, Any]:
    return answer_queue(event, queue_index, caller(event))


def case_handler(event: Any, context: Any) -> dict[str, Any]:
    return answer_case(event, case_stores, caller(event))

"""
The Runtime's spans hold no text: the exporter passes only the listed attributes, and no event, link attribute, or
status description; every trace is kept whatever the request's sampling flag, and the Runtime exports to X-Ray in its
own Region. Each request's span joins the trace AgentCore's request arrived under, and the record's first entry names
the trace; each node the turn runs is a span under it, and a control's interrupt is no error; each attempt at a model
or tool call is a span under its node, as the record keeps it; and each of the checkpointer's writes is a span in the
request's trace (ADR-0004, Operations, Tracing; OPS-01, SEC-03).
"""

import dataclasses
import logging
from collections.abc import Callable
from typing import Any

import pytest
from amazon.opentelemetry.distro.exporter.otlp.aws.traces import otlp_aws_span_exporter
from langgraph.checkpoint.memory import InMemorySaver
from langgraph_checkpoint_aws import DynamoDBSaver
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    StatusCode,
    TraceFlags,
    set_span_in_context,
)

from faro.agent import app, tracing
from faro.agent.graph import build

from .conftest import Harness, customer, run_body, session_id
from .test_block import Chat, control_shown
from .test_retries import STATUS, failing

TYPED = "4111 1111 1111 1111"
TRACE = 0x5759E988BD862E3FE1BE46A994272793
PARENT = 0x53995C3F42CD8AD8
REQUEST = "invoke_agent card_support"
NODES = {
    "begin", "route", "next_request", "list_cards", "unsupported", "resolve_card", "read", "find_transaction", "aside",
    "ask_reason", "confirm", "await_control", "hold", "block", "verify", "handoff", "conclude", "reply",
}  # fmt: skip


def exported(run: Callable[[trace.Tracer], None]) -> list[Any]:
    memory = InMemorySpanExporter()
    built = tracing.provider(memory)
    run(built.get_tracer("test"))
    built.force_flush()
    return list(memory.get_finished_spans())


def test_a_span_leaves_with_its_listed_attributes_alone() -> None:
    def run(tracer: trace.Tracer) -> None:
        other = tracer.start_span("other")
        with tracer.start_as_current_span(
            "turn",
            attributes={"faro.node": "route", "gen_ai.input.messages": TYPED},
            links=[trace.Link(other.get_span_context(), {"text": TYPED})],
        ) as span:
            span.set_attribute("faro.turn_id", "turn-1")
            span.add_event("message", {"text": TYPED})
            try:
                raise ValueError(f"the model wrote {TYPED}")
            except ValueError as error:
                span.record_exception(error)
                span.set_status(StatusCode.ERROR, str(error))
        other.end()

    spans = {span.name: span for span in exported(run)}
    turn = spans["turn"]
    assert dict(turn.attributes or {}) == {
        "faro.node": "route",
        "faro.turn_id": "turn-1",
    }
    assert turn.events == ()
    assert [dict(link.attributes or {}) for link in turn.links] == [{}]
    assert turn.status.status_code is StatusCode.ERROR
    assert turn.status.description is None
    assert TYPED not in repr([s.to_json() for s in spans.values()])


def test_every_trace_is_kept_whatever_the_requests_sampling_flag() -> None:
    unsampled = SpanContext(
        trace_id=0x5759E988BD862E3FE1BE46A994272793,
        span_id=0x53995C3F42CD8AD8,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.DEFAULT),
    )

    def run(tracer: trace.Tracer) -> None:
        context = set_span_in_context(NonRecordingSpan(unsampled))
        tracer.start_span("turn", context=context).end()

    [span] = exported(run)
    assert span.context.trace_id == unsampled.trace_id
    assert span.parent is not None and span.parent.span_id == unsampled.span_id


def test_the_runtime_exports_to_x_ray_in_its_region_and_logs_only_the_names_it_reads(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    made: list[dict[str, Any]] = []
    installed: list[TracerProvider] = []

    def exporter(**arguments: Any) -> InMemorySpanExporter:
        made.append(arguments)
        return InMemorySpanExporter()

    monkeypatch.setattr(otlp_aws_span_exporter, "OTLPAwsSpanExporter", exporter)
    monkeypatch.setattr(trace, "set_tracer_provider", installed.append)
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "runtime-under-test")
    with caplog.at_level(logging.INFO, logger="faro.agent.tracing"):
        tracing.configure()

    assert made == [
        {
            "aws_region": "eu-west-1",
            "endpoint": "https://xray.eu-west-1.amazonaws.com/v1/traces",
        }
    ]
    [provider] = installed
    assert provider.resource.attributes["service.name"] == "runtime-under-test"
    assert "OTEL_SERVICE_NAME" in caplog.text
    assert "runtime-under-test" not in caplog.text


def request_span(spans: list[ReadableSpan]) -> ReadableSpan:
    [request] = [span for span in spans if span.name == REQUEST]
    return request


@pytest.mark.parametrize(
    "header",
    [
        {"traceparent": f"00-{TRACE:032x}-{PARENT:016x}-01"},
        {
            "X-Amzn-Trace-Id": "Root=1-5759e988-bd862e3fe1be46a994272793;Parent=53995c3f42cd8ad8;Sampled=1"
        },
    ],
)
def test_a_turn_is_traced_under_the_span_its_request_arrived_with(
    harness: Harness,
    spans: Callable[[], list[ReadableSpan]],
    header: dict[str, str],
) -> None:
    who = customer()
    session = session_id()
    harness.post(run_body(), who.token(), session, traced=header)

    request = request_span(spans())
    assert request.context.trace_id == TRACE
    assert request.parent is not None and request.parent.span_id == PARENT
    [opened] = [
        e for e in harness.records.of(who.origin_jti) if e["kind"] == "turn_opened"
    ]
    assert opened["trace_id"] == f"{TRACE:032x}"
    assert dict(request.attributes or {}) == {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": "card_support",
        "session.id": session,
        "faro.turn_id": opened["turn_id"],
        "faro.source": "demo",
        "faro.input": "message",
        "faro.outcome": "finished",
    }


def test_a_refused_request_is_traced_and_its_entry_names_the_trace(
    harness: Harness, spans: Callable[[], list[ReadableSpan]]
) -> None:
    who = customer()
    harness.post(run_body(), who.token())

    request = request_span(spans())
    [refused] = harness.records.of(who.origin_jti)
    assert refused["code"] == "session_refused"
    assert refused["trace_id"] == f"{request.context.trace_id:032x}"
    assert request.attributes is not None
    assert request.attributes["faro.refused"] == "session_refused"
    assert request.attributes["faro.turn_id"] == refused["turn_id"]


def node_spans(spans: list[ReadableSpan]) -> list[ReadableSpan]:
    return [span for span in spans if span.name in NODES]


def test_each_node_a_turn_runs_is_a_span_under_the_requests(
    harness: Harness, spans: Callable[[], list[ReadableSpan]]
) -> None:
    harness.post(run_body(), customer().token(), session_id())

    finished = spans()
    request = request_span(finished)
    nodes = node_spans(finished)
    assert {"begin", "route", "reply"} <= {span.name for span in nodes}
    for span in nodes:
        assert span.parent is not None
        assert span.parent.span_id == request.context.span_id
        assert dict(span.attributes or {}) == {"faro.node": span.name}
        assert span.status.status_code is StatusCode.UNSET


def test_a_control_ends_its_nodes_span_without_an_error(
    harness: Harness, spans: Callable[[], list[ReadableSpan]]
) -> None:
    control_shown(Chat(harness))

    finished = spans()
    attributes = request_span(finished).attributes or {}
    assert attributes["faro.outcome"] == "interrupted"
    nodes = node_spans(finished)
    assert "confirm" in {span.name for span in nodes}
    assert all(span.status.status_code is StatusCode.UNSET for span in nodes)


def by_operation(spans: list[ReadableSpan], operation: str) -> list[ReadableSpan]:
    return sorted(
        (
            s
            for s in spans
            if (s.attributes or {}).get("gen_ai.operation.name") == operation
        ),
        key=lambda s: s.start_time or 0,
    )


def test_each_model_and_tool_call_is_a_span_under_its_node_as_the_record_keeps_it(
    harness: Harness, spans: Callable[[], list[ReadableSpan]]
) -> None:
    who = customer()
    harness.post(run_body(), who.token(), session_id())

    finished = spans()
    by_id = {span.context.span_id: span for span in finished}
    entries = harness.records.of(who.origin_jti)
    models = [e for e in entries if e["kind"] == "model_call"]
    chats = by_operation(finished, "chat")
    assert len(chats) == len(models) > 0
    for span, entry in zip(chats, models, strict=True):
        attributes = dict(span.attributes or {})
        assert span.parent is not None
        assert by_id[span.parent.span_id].name in NODES
        assert attributes["faro.prompt"] == entry["node"]
        assert attributes["gen_ai.usage.input_tokens"] == entry["usage"]["input_tokens"]
        assert (
            attributes["gen_ai.usage.output_tokens"] == entry["usage"]["output_tokens"]
        )
        assert attributes["faro.cost_usd"] == entry["cost_usd"]
        assert (span.end_time or 0) - (span.start_time or 0) == entry[
            "latency_ms"
        ] * 1_000_000
    tools = [e for e in entries if e["kind"] == "tool_call"]
    executed = by_operation(finished, "execute_tool")
    assert [dict(s.attributes or {})["gen_ai.tool.call.id"] for s in executed] == [
        e["call_id"] for e in tools
    ]


def test_each_attempt_at_a_read_is_a_span_and_a_failed_one_names_its_code(
    harness: Harness, spans: Callable[[], list[ReadableSpan]]
) -> None:
    failing(harness, "get_card", 1)
    harness.script.replies = ["{card}: {card.status}, {card.expiration}."]

    Chat(harness).say(STATUS, requests=["card_status"], last_four="1177")

    reads = [
        span
        for span in by_operation(spans(), "execute_tool")
        if (span.attributes or {})["gen_ai.tool.name"] == "get_card"
    ]
    assert [
        (dict(s.attributes or {})["faro.attempt"], s.status.status_code) for s in reads
    ] == [
        (1, StatusCode.ERROR),
        (2, StatusCode.UNSET),
    ]
    assert dict(reads[0].attributes or {})["error.type"] == "lambda_error"


class TracedMemory(tracing.TracedWrites, InMemorySaver):
    pass


def test_each_checkpoint_write_is_a_span_in_the_requests_trace(
    harness: Harness, spans: Callable[[], list[ReadableSpan]]
) -> None:
    harness.services = dataclasses.replace(
        harness.services, graph=build(TracedMemory())
    )
    harness.post(run_body(), customer().token(), session_id())

    finished = spans()
    request = request_span(finished)
    puts = [s for s in finished if s.name == "checkpoint put"]
    writes = [s for s in finished if s.name == "checkpoint put_writes"]
    assert puts and writes
    assert all(s.context.trace_id == request.context.trace_id for s in puts + writes)
    steps = [(s.attributes or {})["faro.checkpoint.step"] for s in puts]
    counts = [(s.attributes or {})["faro.checkpoint.writes"] for s in writes]
    assert all(isinstance(step, int) for step in steps)
    assert all(isinstance(count, int) and count > 0 for count in counts)


def test_the_runtimes_saver_traces_its_writes_before_dynamodb_makes_them() -> None:
    assert app.TracedSaver.aput is tracing.TracedWrites.aput
    assert app.TracedSaver.aput_writes is tracing.TracedWrites.aput_writes
    assert issubclass(app.TracedSaver, DynamoDBSaver)

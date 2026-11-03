"""
The Runtime's spans hold no text: the exporter passes only the listed attributes, and no event, link attribute, or
status description; every trace is kept whatever the request's sampling flag, and the Runtime exports to X-Ray in its
own Region (ADR-0004, Operations, Tracing; OPS-01, SEC-03).
"""

import logging
from collections.abc import Callable
from typing import Any

import pytest
from amazon.opentelemetry.distro.exporter.otlp.aws.traces import otlp_aws_span_exporter
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    StatusCode,
    TraceFlags,
    set_span_in_context,
)

from faro.agent import tracing

TYPED = "4111 1111 1111 1111"


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

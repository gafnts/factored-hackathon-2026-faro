"""
The Runtime's spans (ADR-0004, Operations, Tracing; OPS-01): ours alone, with no library instrumented, exported to X-Ray
by ADOT's exporter. A span holds no text and nothing that names the customer: every attribute is on ATTRIBUTES, and the
exporter drops any other, every event, a link's attributes, and a status's description, since an exception's message
may quote a model's output.
"""

import logging
import os
from collections.abc import Sequence

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import Link, Status

# In the GenAI semantic conventions' names where they have one, and in ours otherwise; each value is a count, a model's
# or a tool's name, an ID we drew, the runtime session's ID (as the logs carry it), or an enumerated value.
ATTRIBUTES = frozenset(
    {
        "error.type",
        "gen_ai.agent.name",
        "gen_ai.operation.name",
        "gen_ai.provider.name",
        "gen_ai.request.max_tokens",
        "gen_ai.request.model",
        "gen_ai.request.temperature",
        "gen_ai.response.model",
        "gen_ai.tool.call.id",
        "gen_ai.tool.name",
        "gen_ai.usage.cache_creation.input_tokens",
        "gen_ai.usage.cache_read.input_tokens",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "session.id",
        "faro.attempt",
        "faro.checkpoint.step",
        "faro.checkpoint.writes",
        "faro.cost_usd",
        "faro.input",
        "faro.node",
        "faro.outcome",
        "faro.purpose",
        "faro.refused",
        "faro.source",
        "faro.turn_id",
        "faro.via",
    }
)

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("faro")


def kept(span: ReadableSpan) -> ReadableSpan:
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes={
            k: v for k, v in (span.attributes or {}).items() if k in ATTRIBUTES
        },
        events=(),
        links=tuple(Link(link.context) for link in span.links),
        kind=span.kind,
        status=Status(span.status.status_code),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class Allowed(SpanExporter):
    def __init__(self, exporter: SpanExporter) -> None:
        self.exporter = exporter

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self.exporter.export([kept(span) for span in spans])

    def shutdown(self) -> None:
        self.exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self.exporter.force_flush(timeout_millis)


def provider(exporter: SpanExporter) -> TracerProvider:
    """
    Every trace is kept, whatever the request's sampling flag; the resource is read from the environment.
    """
    built = TracerProvider(resource=Resource.create(), sampler=ALWAYS_ON)
    built.add_span_processor(BatchSpanProcessor(Allowed(exporter)))
    return built


def configure() -> None:
    # Imported here, so that only the Runtime loads ADOT.
    from amazon.opentelemetry.distro.exporter.otlp.aws.traces.otlp_aws_span_exporter import (
        OTLPAwsSpanExporter,
    )

    region = os.environ.get("AWS_REGION", "us-east-1")
    exporter = OTLPAwsSpanExporter(
        aws_region=region, endpoint=f"https://xray.{region}.amazonaws.com/v1/traces"
    )
    trace.set_tracer_provider(provider(exporter))
    named = sorted(name for name in os.environ if name.startswith("OTEL_"))
    logger.info("spans go to X-Ray in %s; OTEL variables set: %s", region, named)

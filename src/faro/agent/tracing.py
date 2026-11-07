"""
The Runtime's spans (ADR-0004, Operations, Tracing; OPS-01): ours alone, with no library instrumented, exported to X-Ray
by ADOT's exporter. A span holds no text and nothing that names the customer: every attribute is on ATTRIBUTES, and the
exporter drops any other, every event, a link's attributes, and a status's description, since an exception's message
may quote a model's output.
"""

import logging
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
)
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.propagators.aws import AwsXRayPropagator
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import Link, Span, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.util.types import AttributeValue

from faro.tools.provenance import OUTCOMES

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
        "faro.prompt",
        "faro.purpose",
        "faro.refused",
        "faro.source",
        "faro.turn_id",
        "faro.via",
    }
)

# The Anthropic SDK traces its own calls, which our spans already do, and sends their trace context to the API unless
# these say not to; a client reads them when it's made, at its first call.
ANTHROPIC_OFF = {
    "ANTHROPIC_OPEN_TELEMETRY": "false",
    "ANTHROPIC_OPEN_TELEMETRY_PROPAGATION": "false",
}

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("faro")
# AgentCore's span may arrive in either header (ADR-0004, To verify on the first deploy).
PROPAGATOR = CompositePropagator([AwsXRayPropagator(), TraceContextTextMapPropagator()])


def joined(headers: Mapping[str, str]) -> Context:
    """
    The context of the span the request arrived under, if its headers name one.
    """
    return PROPAGATOR.extract(headers)


@contextmanager
def span(
    name: str,
    attributes: Mapping[str, AttributeValue],
    context: Context | None = None,
    passing: tuple[type[Exception], ...] = (),
) -> Iterator[Span]:
    """
    A span that ends in an error records the exception's type alone; one of passing's is control flow, not an error.
    """
    with tracer.start_as_current_span(
        name,
        context=context,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as current:
        try:
            yield current
        except passing:
            raise
        except Exception as error:
            current.set_status(StatusCode.ERROR)
            current.set_attribute("error.type", type(error).__qualname__)
            raise


type Shaped = tuple[str, dict[str, AttributeValue | None], str | None]


def model_call(entry: Mapping[str, Any]) -> Shaped:
    """
    The name, the attributes, and the error type, if the attempt failed.
    """
    usage = entry["usage"]
    model = entry["model_requested"]
    return (
        f"chat {model}" if model else "chat",
        {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": entry["provider"],
            "gen_ai.request.model": model,
            "gen_ai.response.model": entry["model_returned"],
            "gen_ai.request.max_tokens": entry["settings"]["max_tokens"],
            "gen_ai.request.temperature": entry["settings"]["temperature"],
            "gen_ai.usage.input_tokens": usage["input_tokens"],
            "gen_ai.usage.output_tokens": usage["output_tokens"],
            "gen_ai.usage.cache_read.input_tokens": usage["cache_read_tokens"],
            "gen_ai.usage.cache_creation.input_tokens": usage["cache_write_tokens"],
            "faro.prompt": entry["node"],
            "faro.purpose": entry["purpose"],
            "faro.attempt": entry["attempt"],
            "faro.outcome": entry["outcome"],
            "faro.cost_usd": entry["cost_usd"],
        },
        None if entry["outcome"] == "ok" else entry["outcome"],
    )


def tool_call(entry: Mapping[str, Any]) -> Shaped:
    """
    A refusal the tool answered, such as a denial, is an outcome, not an error.
    """
    failed = OUTCOMES[entry["outcome"]] == "error"
    return (
        f"execute_tool {entry['tool']}",
        {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": entry["tool"],
            "gen_ai.tool.call.id": entry["call_id"],
            "faro.via": entry["via"],
            "faro.attempt": entry["attempt"],
            "faro.outcome": entry["outcome"],
        },
        (entry.get("error") or {}).get("code", entry["outcome"]) if failed else None,
    )


ATTEMPTS: dict[str, Callable[[Mapping[str, Any]], Shaped]] = {
    "model_call": model_call,
    "tool_call": tool_call,
}


def attempt(entry: Mapping[str, Any], ended: int) -> None:
    """
    An attempt at a model or tool call, from the record's entry for it, as a span that ended, in nanoseconds since the
    epoch, when its entry was written; other entries make none.
    """
    shape = ATTEMPTS.get(entry["kind"])
    if shape is None:
        return
    name, attributes, error = shape(entry)
    made = tracer.start_span(
        name,
        attributes={k: v for k, v in attributes.items() if v is not None},
        start_time=ended - entry["latency_ms"] * 1_000_000,
    )
    if error is not None:
        made.set_status(StatusCode.ERROR)
        made.set_attribute("error.type", error)
    made.end(end_time=ended)


class TracedWrites(BaseCheckpointSaver[str]):
    """
    Each of a checkpointer's writes is a span: they fall between the nodes' spans, and in S6 took about a quarter of a
    turn. Mixed in before the saver it traces.
    """

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        step = metadata.get("step")
        with span(
            "checkpoint put", {} if step is None else {"faro.checkpoint.step": step}
        ):
            return await super().aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        with span("checkpoint put_writes", {"faro.checkpoint.writes": len(writes)}):
            await super().aput_writes(config, writes, task_id, task_path)


def annotate(attributes: Mapping[str, AttributeValue]) -> None:
    trace.get_current_span().set_attributes(attributes)


def trace_id() -> dict[str, str]:
    """
    The current trace's ID as a record field, or none when no span is recorded.
    """
    context = trace.get_current_span().get_span_context()
    return {"trace_id": f"{context.trace_id:032x}"} if context.is_valid else {}


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
    os.environ.update(ANTHROPIC_OFF)
    trace.set_tracer_provider(provider(exporter))
    named = sorted(name for name in os.environ if name.startswith("OTEL_"))
    logger.info("spans go to X-Ray in %s; OTEL variables set: %s", region, named)

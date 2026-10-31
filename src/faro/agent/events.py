"""
Rebuilds each event the wrapper emits, field by field, to the chat's contract (ADR-0004, What the chat receives, and the
amendment on the wrapper's whole input). Only the event types the chat renders pass, with the client's thread and run
IDs, and without the wrapper's additions: the input echo, the run's token usage, raw events, an interrupt's LangGraph
metadata, which names the graph's nodes, and anything a later version of the wrapper adds. A run that ends at the
controls carries them rebuilt from the interrupt's value, which must fit the contract. Every event sent is checked
against the contract first.
"""

from typing import Any

from ag_ui.core import (
    AssistantMessage,
    BaseEvent,
    EventType,
    Interrupt,
    MessagesSnapshotEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunFinishedInterruptOutcome,
    RunFinishedSuccessOutcome,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    UserMessage,
)

from faro.contracts import validator

ERRORS = {
    "invalid_request": "The request doesn't fit the chat's contract.",
    "session_refused": "The runtime session belongs to another user.",
    "no_customer": "The token doesn't name a customer.",
    "rate_limited": "The sign-in sent more turns this minute than the limit allows.",
    "daily_limit": "The user sent more turns today than the limit allows.",
    "internal": "The agent is unavailable.",
}


SUCCESS = RunFinishedSuccessOutcome(type="success")


class UnexpectedEventError(RuntimeError):
    pass


def run_error(code: str) -> RunErrorEvent:
    return RunErrorEvent(message=ERRORS[code], code=code)


def public_messages(messages: list[Any]) -> list[Any]:
    """
    The public state holds the customer's masked messages and the checked replies; anything else is dropped.
    """
    kept: list[Any] = []
    for message in messages:
        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content:
            continue
        if message.role == "user":
            kept.append(UserMessage(id=message.id, role="user", content=content))
        elif message.role == "assistant" and not getattr(message, "tool_calls", None):
            kept.append(
                AssistantMessage(id=message.id, role="assistant", content=content)
            )
    return kept


def controls(interrupt: Any) -> Interrupt:
    value: Any = ((interrupt.metadata or {}).get("langgraph") or {}).get("raw")
    if not validator("chat", "interrupt_value").is_valid(value):
        raise UnexpectedEventError(
            "an interrupt's value doesn't fit the chat's contract"
        )
    return Interrupt(
        id=interrupt.id,
        reason="controls",
        metadata={"language": value["language"], "controls": value["controls"]},
    )


def rebuild(event: Any, thread_id: str, run_id: str) -> BaseEvent | None:
    kind = event.type
    if kind == EventType.RUN_STARTED:
        return RunStartedEvent(thread_id=thread_id, run_id=run_id)
    if kind == EventType.RUN_FINISHED:
        outcome: Any = getattr(event, "outcome", None)
        ended = getattr(outcome, "type", "success")
        if ended == "interrupt":
            interrupted = RunFinishedInterruptOutcome(
                type="interrupt", interrupts=[controls(i) for i in outcome.interrupts]
            )
            return RunFinishedEvent(
                thread_id=thread_id, run_id=run_id, outcome=interrupted
            )
        if ended != "success":
            raise UnexpectedEventError(f"a run finished as {ended}")
        return RunFinishedEvent(thread_id=thread_id, run_id=run_id, outcome=SUCCESS)
    if kind == EventType.TEXT_MESSAGE_START:
        return TextMessageStartEvent(message_id=event.message_id, role="assistant")
    if kind == EventType.TEXT_MESSAGE_CONTENT:
        return TextMessageContentEvent(message_id=event.message_id, delta=event.delta)
    if kind == EventType.TEXT_MESSAGE_END:
        return TextMessageEndEvent(message_id=event.message_id)
    if kind == EventType.MESSAGES_SNAPSHOT:
        return MessagesSnapshotEvent(messages=public_messages(event.messages))
    if kind == EventType.RUN_ERROR:
        raise UnexpectedEventError("the wrapper reported an error")
    return None


def checked(event: BaseEvent) -> BaseEvent:
    if not validator("chat", "event").is_valid(as_json(event)):
        raise UnexpectedEventError(
            f"a {event.type} event doesn't fit the chat's contract"
        )
    return event


def as_json(event: BaseEvent) -> dict[str, Any]:
    dumped: dict[str, Any] = event.model_dump(
        by_alias=True, exclude_none=True, mode="json"
    )
    return dumped

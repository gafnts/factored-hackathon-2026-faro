"""
Projects the load the card support agent would put on its services from the development customers' measured
contacts, scaled to the bank and beyond, under the assumptions named here, against the quotas and prices the
providers document. Every number it returns is a projection, never a measurement (EVL-13).
"""

import math
from dataclasses import dataclass
from datetime import date
from statistics import NormalDist

from faro.analysis.traffic import CONTACTS, Timing, Traffic, Volume

# When the quotas and prices below were read from their sources.
CHECKED = date(2026, 9, 28)
SCALES = (1, 10, 100)
PEAK_QUANTILE = 0.999
# Above this mean the Poisson quantile comes from its Cornish-Fisher expansion, which matches the exact
# quantile at this size, instead of a sum that underflows.
EXACT_BELOW = 500
SEARCH = (1e-4, 1e7)
SEARCH_STEPS = 80

AGENTCORE = "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/bedrock-agentcore-limits.html"
LAMBDA = "https://docs.aws.amazon.com/lambda/latest/dg/lambda-concurrency.html"
DYNAMODB = "https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/ServiceQuotas.html"
BEDROCK = "https://docs.aws.amazon.com/general/latest/gr/bedrock.html"
ANTHROPIC_LIMITS = "https://platform.claude.com/docs/en/api/rate-limits"
ANTHROPIC_PRICES = "https://platform.claude.com/docs/en/about-claude/pricing"
OPENAI = "https://developers.openai.com/api/docs/models/gpt-5.4-mini"
GEMINI_LIMITS = "https://ai.google.dev/gemini-api/docs/rate-limits"
GEMINI_PRICES = "https://ai.google.dev/gemini-api/docs/pricing"
ADR_0004 = "../adr/0004-agent-architecture-on-agentcore.md"


@dataclass(frozen=True)
class Assumption:
    key: str
    value: float
    unit: str
    basis: str


ASSUMPTIONS = (
    Assumption(
        "turns",
        4,
        "customer messages per conversation",
        "Assumed: a request, a clarification or a confirmation, and a follow-up; the evaluation's scripts "
        "will replace it (ADR-0005)",
    ),
    Assumption(
        "model_calls",
        3,
        "model calls per turn",
        "ADR-0004's graph: the router, one extraction, and the reply",
    ),
    Assumption(
        "input_tokens",
        2_500,
        "input tokens per model call",
        "Assumed: instructions, the turn's formatted facts, and the conversation so far; no prompt caching",
    ),
    Assumption(
        "output_tokens",
        300,
        "output tokens per model call, reasoning included",
        "Assumed, under ADR-0004's budget of 2,048",
    ),
    Assumption(
        "tool_calls", 1.5, "tool calls per turn", "Assumed, from ADR-0004's graph"
    ),
    Assumption("tool_seconds", 0.5, "seconds per tool Lambda invocation", "Assumed"),
    Assumption(
        "reads",
        5,
        "DynamoDB read request units per turn",
        "Assumed: the session binding, the checkpoint, and each tool's read of the tools' data and the "
        "overlay",
    ),
    Assumption(
        "write_units",
        36,
        "DynamoDB write request units per turn",
        "Assumed: 18 writes of up to 2 KB (checkpoints per node, the execution record's events)",
    ),
    Assumption(
        "idle_seconds",
        900,
        "seconds a runtime session stays up after its last request",
        "AgentCore Runtime's default idle timeout",
    ),
    Assumption(
        "cold_start_seconds",
        7,
        "seconds to the first byte on a new runtime session",
        "Spike S4, a single sample (ADR-0004)",
    ),
)

_A = {a.key: a.value for a in ASSUMPTIONS}


@dataclass(frozen=True)
class Profile:
    key: str
    hours: int
    basis: str


PROFILES = (
    Profile(
        "flat",
        24,
        "As measured: the busiest day's contacts spread evenly over its 24 hours, since no table has a "
        "daily cycle",
    ),
    Profile(
        "business_hours",
        12,
        "Assumed, not measured: the busiest day's contacts within 12 hours, twice the hourly rate, as a "
        "contact center with a daily cycle would see",
    ),
)


@dataclass(frozen=True)
class Scenario:
    key: str
    share: float
    conversation_seconds: float
    basis: str


@dataclass(frozen=True)
class Limit:
    key: str
    service: str
    name: str
    value: float
    # The Load field it bounds.
    measure: str
    source: str
    note: str = ""


LIMITS = (
    Limit(
        "runtime_sessions",
        "AgentCore Runtime",
        "Active session workloads per account",
        5_000,
        "sessions",
        AGENTCORE,
    ),
    Limit(
        "runtime_new_sessions",
        "AgentCore Runtime",
        "New runtime sessions per second",
        25,
        "new_sessions_per_second",
        AGENTCORE,
    ),
    Limit(
        "runtime_requests",
        "AgentCore Runtime",
        "Data plane requests per second",
        1_000,
        "runtime_requests_per_second",
        AGENTCORE,
    ),
    Limit(
        "gateway_tool_calls",
        "AgentCore Gateway",
        "Tool calls per second per gateway",
        200,
        "tool_calls_per_second",
        AGENTCORE,
    ),
    Limit(
        "lambda_concurrency",
        "Lambda",
        "Concurrent executions per account",
        1_000,
        "lambda_concurrency",
        LAMBDA,
    ),
    Limit(
        "dynamodb_reads",
        "DynamoDB",
        "On-demand read request units per second per table",
        40_000,
        "reads_per_second",
        DYNAMODB,
    ),
    Limit(
        "dynamodb_writes",
        "DynamoDB",
        "On-demand write request units per second per table",
        40_000,
        "writes_per_second",
        DYNAMODB,
    ),
    Limit(
        "bedrock_requests",
        "Bedrock",
        "Claude Haiku 4.5 cross-region requests per minute (default)",
        10_000,
        "model_requests_per_minute",
        BEDROCK,
    ),
    Limit(
        "bedrock_tokens",
        "Bedrock",
        "Claude Haiku 4.5 cross-region tokens per minute, input and output (default)",
        5_000_000,
        "tokens_per_minute",
        BEDROCK,
    ),
    Limit(
        "bedrock_requests_applied",
        "Bedrock",
        "Claude Haiku 4.5 requests per minute as applied on our account",
        50,
        "model_requests_per_minute",
        ADR_0004 + "#s1-bedrock-access-and-quotas",
        "Spike S1; a new account's applied quota can sit far below the documented default",
    ),
    Limit(
        "anthropic_requests",
        "Claude API",
        "Claude Haiku 4.5 requests per minute (Scale tier)",
        10_000,
        "model_requests_per_minute",
        ANTHROPIC_LIMITS,
    ),
    Limit(
        "anthropic_input",
        "Claude API",
        "Claude Haiku 4.5 uncached input tokens per minute (Scale tier)",
        10_000_000,
        "input_tokens_per_minute",
        ANTHROPIC_LIMITS,
    ),
    Limit(
        "anthropic_output",
        "Claude API",
        "Claude Haiku 4.5 output tokens per minute (Scale tier)",
        2_000_000,
        "output_tokens_per_minute",
        ANTHROPIC_LIMITS,
    ),
    Limit(
        "openai_requests",
        "OpenAI API",
        "gpt-5.4-mini requests per minute (Tier 5)",
        30_000,
        "model_requests_per_minute",
        OPENAI,
    ),
    Limit(
        "openai_tokens",
        "OpenAI API",
        "gpt-5.4-mini tokens per minute (Tier 5)",
        180_000_000,
        "tokens_per_minute",
        OPENAI,
    ),
)

# Limits the providers don't publish as numbers.
UNPUBLISHED = (
    (
        "Gemini API",
        "Gemini 3.8 Flash and Gemini 3.1 Pro: limits per project, by tier, shown only in AI Studio",
        GEMINI_LIMITS,
    ),
)


@dataclass(frozen=True)
class Price:
    model: str
    provider: str
    # USD per million tokens.
    input: float
    output: float
    source: str
    note: str = ""


PRICES = (
    Price("Claude Haiku 4.5", "Anthropic", 1.00, 5.00, ANTHROPIC_PRICES),
    Price("gpt-5.4-mini", "OpenAI", 0.75, 4.50, OPENAI),
    Price(
        "Gemini 3.8 Flash",
        "Google",
        0.75,
        3.75,
        GEMINI_PRICES,
        "until 2026-12-31, then 1.50 and 7.50 from 2027-01-01",
    ),
)


@dataclass(frozen=True)
class Cost:
    model: str
    per_day: float
    per_conversation: float


@dataclass(frozen=True)
class Load:
    scenario: str
    profile: str
    scale: float
    conversations_per_day: float
    busiest_day: float
    arrivals_per_hour: float
    # Conversations under way at the peak: the mean, and the quantile sized for.
    active_mean: float
    active: int
    sessions: int
    new_sessions_per_second: int
    runtime_requests_per_second: float
    model_requests_per_minute: float
    input_tokens_per_minute: float
    output_tokens_per_minute: float
    tool_calls_per_second: float
    lambda_concurrency: float
    reads_per_second: float
    writes_per_second: float
    cold_starts_per_hour: float
    costs: tuple[Cost, ...]

    @property
    def tokens_per_minute(self) -> float:
        return self.input_tokens_per_minute + self.output_tokens_per_minute


@dataclass(frozen=True)
class Headroom:
    scenario: str
    profile: str
    limit: str
    # The multiple of this bank's traffic at which the projection reaches the limit; None beyond the search.
    multiple: float | None


@dataclass(frozen=True)
class Projection:
    checked: date
    bank_factor: float
    busiest_day: int
    mean_day: float
    measured_busiest_hour: int
    peak_quantile: float
    scales: tuple[int, ...]
    assumptions: tuple[Assumption, ...]
    profiles: tuple[Profile, ...]
    scenarios: tuple[Scenario, ...]
    limits: tuple[Limit, ...]
    unpublished: tuple[tuple[str, str, str], ...]
    prices: tuple[Price, ...]
    loads: tuple[Load, ...]
    headroom: tuple[Headroom, ...]


def poisson_quantile(mean: float, q: float = PEAK_QUANTILE) -> int:
    if mean <= 0:
        return 0
    if mean > EXACT_BELOW:
        z = NormalDist().inv_cdf(q)
        return math.ceil(mean + z * math.sqrt(mean) + (z * z - 1) / 6 - 0.5)
    term = math.exp(-mean)
    total, k = term, 0
    while total < q:
        k += 1
        term *= mean / k
        total += term
    return k


def _volume(t: Traffic, group: str, value: str) -> Volume:
    return next(
        v for g, vs in t.contacts.volumes if g == group for v in vs if v.value == value
    )


def _timing(t: Traffic, group: str, value: str) -> Timing:
    return next(
        x for g, xs in t.contacts.timings if g == group for x in xs if x.value == value
    )


def scenarios(t: Traffic) -> tuple[Scenario, ...]:
    contacts = t.contacts.rows
    overall = t.contacts.overall.handle.mean or 0.0
    chat = _volume(t, "interaction_type", "Chat").rows
    transactional = _volume(t, "reason_category", "Transaccional")
    return (
        Scenario(
            "chat",
            chat / contacts,
            overall,
            "Contacts that already arrive as a chat (App, Web Chat, WhatsApp), where the agent would sit; "
            "chats record no handle time, so a conversation lasts the mean handle time of all contacts",
        ),
        Scenario(
            "transactional",
            transactional.rows / contacts,
            _timing(t, "reason_category", "Transaccional").handle.mean or overall,
            "The reason category nearest card support, with its own mean handle time; a bracket, not a "
            "measure, since contacts can't be tied to a workflow (ADR-0003)",
        ),
        Scenario(
            "all",
            1.0,
            overall,
            "Every contact, whatever its reason or channel: the upper bound",
        ),
    )


def load(t: Traffic, scenario: Scenario, profile: Profile, scale: float) -> Load:
    contacts = t.stream(CONTACTS).year
    factor = t.bank_factor * scenario.share * scale
    busiest_day = contacts.most * factor
    per_hour = busiest_day / profile.hours
    per_second = per_hour / 3600
    seconds = scenario.conversation_seconds
    active_mean = per_second * seconds
    active = poisson_quantile(active_mean)
    turns_per_second = active * _A["turns"] / seconds if seconds else 0.0
    calls_per_minute = turns_per_second * _A["model_calls"] * 60
    tool_calls = turns_per_second * _A["tool_calls"]
    per_day = contacts.mean * factor
    tokens = (
        _A["turns"] * _A["model_calls"] * _A["input_tokens"] / 1e6,
        _A["turns"] * _A["model_calls"] * _A["output_tokens"] / 1e6,
    )
    return Load(
        scenario=scenario.key,
        profile=profile.key,
        scale=scale,
        conversations_per_day=per_day,
        busiest_day=busiest_day,
        arrivals_per_hour=per_hour,
        active_mean=active_mean,
        active=active,
        sessions=poisson_quantile(per_second * (seconds + _A["idle_seconds"])),
        new_sessions_per_second=poisson_quantile(per_second),
        runtime_requests_per_second=turns_per_second,
        model_requests_per_minute=calls_per_minute,
        input_tokens_per_minute=calls_per_minute * _A["input_tokens"],
        output_tokens_per_minute=calls_per_minute * _A["output_tokens"],
        tool_calls_per_second=tool_calls,
        lambda_concurrency=tool_calls * _A["tool_seconds"],
        reads_per_second=turns_per_second * _A["reads"],
        writes_per_second=turns_per_second * _A["write_units"],
        cold_starts_per_hour=per_hour,
        costs=tuple(
            Cost(
                p.model,
                per_day * (tokens[0] * p.input + tokens[1] * p.output),
                tokens[0] * p.input + tokens[1] * p.output,
            )
            for p in PRICES
        ),
    )


def _measure(value: Load, limit: Limit) -> float:
    return float(getattr(value, limit.measure))


def headroom(
    t: Traffic, scenario: Scenario, profile: Profile, limit: Limit
) -> float | None:
    low, high = SEARCH
    if _measure(load(t, scenario, profile, high), limit) < limit.value:
        return None
    for _ in range(SEARCH_STEPS):
        middle = math.sqrt(low * high)
        if _measure(load(t, scenario, profile, middle), limit) >= limit.value:
            high = middle
        else:
            low = middle
    return high


def project(t: Traffic) -> Projection:
    cases = scenarios(t)
    return Projection(
        checked=CHECKED,
        bank_factor=t.bank_factor,
        busiest_day=t.stream(CONTACTS).year.most,
        mean_day=t.stream(CONTACTS).year.mean,
        measured_busiest_hour=t.stream(CONTACTS).hourly.most,
        peak_quantile=PEAK_QUANTILE,
        scales=SCALES,
        assumptions=ASSUMPTIONS,
        profiles=PROFILES,
        scenarios=cases,
        limits=LIMITS,
        unpublished=UNPUBLISHED,
        prices=PRICES,
        loads=tuple(load(t, s, p, k) for s in cases for p in PROFILES for k in SCALES),
        headroom=tuple(
            Headroom(s.key, p.key, limit.key, headroom(t, s, p, limit))
            for s in cases
            for p in PROFILES
            for limit in LIMITS
        ),
    )

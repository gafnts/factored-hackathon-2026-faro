"""
The organizers' tables as the data dictionary describes them: layout, keys, types, references, and documented values.
"""

import re
from dataclasses import dataclass

_COLUMN = re.compile(
    r"^(?P<name>\w+) (?P<type>[A-Z]+(?:\((?P<size>\d+)(?:,(?P<scale>\d+))?\))?)"
    r"(?P<not_null> NOT NULL)?"
    r"(?: REFERENCES (?P<references>\w+))?"
    r"(?: IN \((?P<values>[^)]*)\))?$"
)

TYPES = (
    "VARCHAR",
    "TEXT",
    "INTEGER",
    "DECIMAL",
    "DATE",
    "TIMESTAMP",
    "BOOLEAN",
    "TIME",
)


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    not_null: bool = False
    references: str | None = None
    values: tuple[str, ...] = ()

    @property
    def base_type(self) -> str:
        return self.type.split("(", 1)[0]


@dataclass(frozen=True)
class EventDate:
    column: str
    # Read the date from the referenced row when the table has no date of its own.
    via: str | None = None


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    key: tuple[str, ...]
    unique: tuple[str, ...] = ()
    event_date: EventDate | None = None
    # Partitioned as <name>/year=/month=/day=/<name>_YYYYMMDD.csv instead of a single <name>.csv.
    daily: bool = False

    def column(self, name: str) -> Column:
        for column in self.columns:
            if column.name == name:
                return column
        raise KeyError(f"{self.name} has no column {name}")


def parse_columns(spec: str) -> tuple[Column, ...]:
    columns = []
    for line in filter(None, (line.strip() for line in spec.splitlines())):
        match = _COLUMN.match(line)
        if not match or match["type"].split("(", 1)[0] not in TYPES:
            raise ValueError(f"can't parse column {line!r}")
        values = match["values"]
        columns.append(
            Column(
                name=match["name"],
                type=match["type"],
                not_null=bool(match["not_null"]),
                references=match["references"],
                values=tuple(v.strip() for v in values.split(",")) if values else (),
            )
        )
    return tuple(columns)


def table(
    name: str,
    spec: str,
    key: tuple[str, ...],
    unique: tuple[str, ...] = (),
    event_date: EventDate | None = None,
    daily: bool = False,
) -> Table:
    return Table(name, parse_columns(spec), key, unique, event_date, daily)


_COUNTRIES = "IN (Mexico, Colombia, Argentina)"
_CURRENCIES = "IN (MXN, COP, ARS, USD)"

CUSTOMERS = table(
    "customers",
    f"""
    customer_id VARCHAR(20) NOT NULL
    document_number VARCHAR(20) NOT NULL
    document_type VARCHAR(10) NOT NULL IN (DNI, CURP, CC, CE, Passport)
    first_name VARCHAR(100) NOT NULL
    last_name VARCHAR(100) NOT NULL
    date_of_birth DATE NOT NULL
    gender VARCHAR(1) IN (M, F, O)
    email VARCHAR(100)
    mobile_phone VARCHAR(20)
    landline_phone VARCHAR(20)
    address VARCHAR(200)
    city VARCHAR(100) NOT NULL
    state VARCHAR(100) NOT NULL
    country VARCHAR(50) NOT NULL {_COUNTRIES}
    postal_code VARCHAR(10)
    detected_accent VARCHAR(50) IN (mexican, colombian, argentine, neutral)
    segment VARCHAR(50) NOT NULL IN (Premium, Plus, Basic, Student)
    credit_score INTEGER
    estimated_monthly_income DECIMAL(12,2)
    occupation VARCHAR(100)
    marital_status VARCHAR(20)
    education_level VARCHAR(50)
    registration_date TIMESTAMP NOT NULL
    registration_branch_id VARCHAR(20) NOT NULL REFERENCES branches
    customer_status VARCHAR(20) NOT NULL IN (Active, Inactive, Suspended, Closed)
    last_updated TIMESTAMP NOT NULL
    accepts_marketing BOOLEAN NOT NULL
    """,
    key=("customer_id",),
    unique=("document_number",),
)

# The dictionary's own list of product types is cut off ("Investme"), so it isn't checked.
PRODUCTS = table(
    "products",
    f"""
    product_id VARCHAR(20) NOT NULL
    customer_id VARCHAR(20) NOT NULL REFERENCES customers
    product_type VARCHAR(50) NOT NULL
    product_number VARCHAR(30) NOT NULL
    currency VARCHAR(3) NOT NULL {_CURRENCIES}
    current_balance DECIMAL(15,2) NOT NULL
    credit_limit DECIMAL(15,2)
    interest_rate DECIMAL(5,2)
    opening_date DATE NOT NULL
    expiration_date DATE
    opening_branch_id VARCHAR(20) NOT NULL REFERENCES branches
    product_status VARCHAR(20) NOT NULL IN (Active, Blocked, Closed, Suspended)
    opening_channel VARCHAR(30) NOT NULL IN (Branch, Web, App, Call Center)
    has_linked_app BOOLEAN NOT NULL
    days_past_due INTEGER
    last_transaction_date TIMESTAMP
    last_updated TIMESTAMP NOT NULL
    """,
    key=("product_id",),
    unique=("product_number",),
)

BRANCHES = table(
    "branches",
    f"""
    branch_id VARCHAR(20) NOT NULL
    branch_code VARCHAR(10) NOT NULL
    branch_name VARCHAR(100) NOT NULL
    branch_type VARCHAR(30) NOT NULL IN (Main, Express, Premium, Corporate)
    address VARCHAR(200) NOT NULL
    city VARCHAR(100) NOT NULL
    state VARCHAR(100) NOT NULL
    country VARCHAR(50) NOT NULL {_COUNTRIES}
    postal_code VARCHAR(10)
    geographic_zone VARCHAR(50) NOT NULL IN (Urban, Suburban, Rural)
    phone VARCHAR(20) NOT NULL
    email VARCHAR(100)
    opening_time TIME NOT NULL
    closing_time TIME NOT NULL
    has_atms BOOLEAN NOT NULL
    atm_count INTEGER
    has_teller_windows BOOLEAN NOT NULL
    teller_window_count INTEGER
    latitude DECIMAL(10,7)
    longitude DECIMAL(10,7)
    branch_opening_date DATE NOT NULL
    branch_status VARCHAR(20) NOT NULL IN (Active, Temporarily Closed, Closed)
    """,
    key=("branch_id",),
    unique=("branch_code",),
)

SERVICE_AGENTS = table(
    "service_agents",
    """
    agent_id VARCHAR(20) NOT NULL
    employee_code VARCHAR(15) NOT NULL
    first_name VARCHAR(100) NOT NULL
    last_name VARCHAR(100) NOT NULL
    email VARCHAR(100) NOT NULL
    phone VARCHAR(20)
    native_accent VARCHAR(50) NOT NULL IN (mexican, colombian, argentine)
    country_of_origin VARCHAR(50) NOT NULL
    assigned_branch_id VARCHAR(20) REFERENCES branches
    agent_type VARCHAR(30) NOT NULL IN (Phone, In-Person, Digital, Hybrid)
    experience_level VARCHAR(20) NOT NULL IN (Junior, Mid-Senior, Senior, Specialist)
    languages VARCHAR(100) NOT NULL
    specialty VARCHAR(100)
    hire_date DATE NOT NULL
    avg_csat DECIMAL(3,2)
    total_monthly_interactions INTEGER
    agent_status VARCHAR(20) NOT NULL IN (Active, Vacation, Leave, Inactive)
    work_shift VARCHAR(20) NOT NULL IN (Morning, Afternoon, Night, Rotating)
    """,
    key=("agent_id",),
    unique=("employee_code",),
)

MARKETING_CAMPAIGNS = table(
    "marketing_campaigns",
    """
    campaign_id VARCHAR(20) NOT NULL
    campaign_name VARCHAR(150) NOT NULL
    description TEXT
    campaign_type VARCHAR(50) NOT NULL IN (Email, SMS, Push, WhatsApp, Voice, Mix)
    campaign_objective VARCHAR(100) NOT NULL IN (Acquisition, Retention, Cross-sell, Up-sell, Reactivation)
    promoted_product VARCHAR(50)
    target_segment VARCHAR(50)
    target_country VARCHAR(50)
    start_date DATE NOT NULL
    end_date DATE NOT NULL
    budget DECIMAL(12,2)
    campaign_status VARCHAR(20) NOT NULL IN (Planned, Active, Paused, Completed)
    expected_conversion_rate DECIMAL(5,2)
    """,
    key=("campaign_id",),
)

TRANSACTIONS = table(
    "transactions",
    f"""
    transaction_id VARCHAR(30) NOT NULL
    transaction_date TIMESTAMP NOT NULL
    process_date DATE NOT NULL
    product_id VARCHAR(20) NOT NULL REFERENCES products
    customer_id VARCHAR(20) NOT NULL REFERENCES customers
    transaction_type VARCHAR(50) NOT NULL IN (Deposit, Withdrawal, Transfer, Payment, Purchase, Adjustment)
    transaction_category VARCHAR(50) IN (Food, Transport, Services, Entertainment, Health, Other)
    amount DECIMAL(15,2) NOT NULL
    currency VARCHAR(3) NOT NULL {_CURRENCIES}
    amount_usd DECIMAL(15,2)
    channel VARCHAR(30) NOT NULL IN (ATM, Branch, Web, App, POS, Transfer)
    branch_id VARCHAR(20) REFERENCES branches
    merchant_name VARCHAR(150)
    merchant_category VARCHAR(50)
    transaction_country VARCHAR(50) NOT NULL
    transaction_city VARCHAR(100)
    transaction_status VARCHAR(20) NOT NULL IN (Approved, Declined, Pending, Reversed)
    response_code VARCHAR(10)
    is_fraud BOOLEAN NOT NULL
    fraud_score DECIMAL(5,2)
    latitude DECIMAL(10,7)
    longitude DECIMAL(10,7)
    """,
    key=("transaction_id",),
    event_date=EventDate("transaction_date"),
    daily=True,
)

CALL_CENTER_INTERACTIONS = table(
    "call_center_interactions",
    """
    interaction_id VARCHAR(30) NOT NULL
    interaction_date TIMESTAMP NOT NULL
    process_date DATE NOT NULL
    customer_id VARCHAR(20) NOT NULL REFERENCES customers
    agent_id VARCHAR(20) REFERENCES service_agents
    interaction_type VARCHAR(30) NOT NULL IN (Inbound Call, Outbound Call, Chat, Email, Video)
    channel VARCHAR(30) NOT NULL IN (Phone, Web Chat, WhatsApp, Email, App)
    contact_reason VARCHAR(100) NOT NULL
    reason_category VARCHAR(50) NOT NULL IN (Transactional, Product, Technical, Commercial, Complaint)
    duration_seconds INTEGER
    wait_time_seconds INTEGER
    was_resolved BOOLEAN
    requires_followup BOOLEAN NOT NULL
    detected_sentiment VARCHAR(20) IN (Positive, Neutral, Negative, Very Negative)
    sentiment_score DECIMAL(3,2)
    customer_detected_accent VARCHAR(50)
    agent_used_accent VARCHAR(50)
    was_escalated BOOLEAN NOT NULL
    mentioned_products VARCHAR(200)
    has_transcript BOOLEAN NOT NULL
    has_recording BOOLEAN NOT NULL
    """,
    key=("interaction_id",),
    event_date=EventDate("interaction_date"),
    daily=True,
)

CALL_TRANSCRIPTS = table(
    "call_transcripts",
    """
    transcript_id VARCHAR(30) NOT NULL
    interaction_id VARCHAR(30) NOT NULL REFERENCES call_center_interactions
    process_date DATE NOT NULL
    customer_id VARCHAR(20) NOT NULL REFERENCES customers
    agent_id VARCHAR(20) NOT NULL REFERENCES service_agents
    full_text TEXT NOT NULL
    customer_text TEXT
    agent_text TEXT
    detected_language VARCHAR(10) NOT NULL
    detected_accent VARCHAR(50)
    accent_confidence DECIMAL(3,2)
    detected_keywords VARCHAR(500)
    mentioned_entities TEXT
    detected_intents VARCHAR(300)
    main_topics VARCHAR(300)
    transcription_model VARCHAR(50) NOT NULL
    audio_quality VARCHAR(20) IN (High, Medium, Low)
    duration_seconds INTEGER NOT NULL
    """,
    key=("transcript_id",),
    event_date=EventDate("interaction_date", via="interaction_id"),
    daily=True,
)

SATISFACTION_SURVEYS = table(
    "satisfaction_surveys",
    """
    survey_id VARCHAR(30) NOT NULL
    survey_date TIMESTAMP NOT NULL
    process_date DATE NOT NULL
    interaction_id VARCHAR(30) REFERENCES call_center_interactions
    customer_id VARCHAR(20) NOT NULL REFERENCES customers
    agent_id VARCHAR(20) REFERENCES service_agents
    survey_type VARCHAR(20) NOT NULL IN (CSAT, NPS, CES)
    send_channel VARCHAR(30) NOT NULL IN (Email, SMS, IVR, App, Web)
    main_score INTEGER NOT NULL
    nps_category VARCHAR(20) IN (Promoter, Passive, Detractor)
    question_1_text TEXT
    question_1_response INTEGER
    question_2_text TEXT
    question_2_response INTEGER
    question_3_text TEXT
    question_3_response INTEGER
    open_comments TEXT
    comment_sentiment VARCHAR(20)
    response_time_hours DECIMAL(8,2)
    campaign_response_rate DECIMAL(5,2)
    """,
    key=("survey_id",),
    event_date=EventDate("survey_date"),
    daily=True,
)

DIGITAL_EVENTS = table(
    "digital_events",
    """
    event_id VARCHAR(30) NOT NULL
    event_date TIMESTAMP NOT NULL
    process_date DATE NOT NULL
    customer_id VARCHAR(20) REFERENCES customers
    session_id VARCHAR(50) NOT NULL
    event_type VARCHAR(50) NOT NULL IN (PageView, Click, FormSubmit, Login, Logout, Error, Purchase)
    event_category VARCHAR(50) NOT NULL IN (Navigation, Transaction, Authentication, Product)
    channel VARCHAR(30) NOT NULL IN (Android App, iOS App, Desktop Web, Mobile Web)
    platform VARCHAR(30) IN (Android, iOS, Windows, MacOS, Linux)
    browser VARCHAR(50)
    app_version VARCHAR(20)
    page_url VARCHAR(300)
    page_title VARCHAR(200)
    action VARCHAR(100)
    element_id VARCHAR(100)
    product_id VARCHAR(20) REFERENCES products
    event_value DECIMAL(15,2)
    duration_seconds INTEGER
    ip_address VARCHAR(45)
    ip_country VARCHAR(50)
    ip_city VARCHAR(100)
    is_mobile BOOLEAN NOT NULL
    referrer VARCHAR(300)
    utm_source VARCHAR(100)
    utm_medium VARCHAR(100)
    utm_campaign VARCHAR(100)
    """,
    key=("event_id",),
    event_date=EventDate("event_date"),
    daily=True,
)

COMPLAINTS = table(
    "complaints",
    f"""
    complaint_id VARCHAR(30) NOT NULL
    creation_date TIMESTAMP NOT NULL
    process_date DATE NOT NULL
    customer_id VARCHAR(20) NOT NULL REFERENCES customers
    case_type VARCHAR(30) NOT NULL IN (Complaint, Claim, Request, Suggestion)
    category VARCHAR(100) NOT NULL
    subcategory VARCHAR(100)
    reception_channel VARCHAR(30) NOT NULL IN (Call Center, Email, Web, App, Branch, Regulator)
    affected_product_id VARCHAR(20) REFERENCES products
    related_branch_id VARCHAR(20) REFERENCES branches
    origin_interaction_id VARCHAR(30) REFERENCES call_center_interactions
    description TEXT NOT NULL
    claimed_amount DECIMAL(15,2)
    currency VARCHAR(3) {_CURRENCIES}
    priority VARCHAR(20) NOT NULL IN (Low, Medium, High, Critical)
    status VARCHAR(30) NOT NULL IN (Open, In Process, Escalated, Resolved, Closed, Rejected)
    assigned_agent_id VARCHAR(20) REFERENCES service_agents
    assignment_date TIMESTAMP
    first_response_date TIMESTAMP
    resolution_date TIMESTAMP
    closing_date TIMESTAMP
    sla_breached BOOLEAN NOT NULL
    resolution_days INTEGER
    resolution TEXT
    compensation_granted DECIMAL(15,2)
    resolution_satisfaction INTEGER
    is_repeat_complainer BOOLEAN NOT NULL
    """,
    key=("complaint_id",),
    event_date=EventDate("creation_date"),
    daily=True,
)

CAMPAIGN_SENDS = table(
    "campaign_sends",
    """
    send_id VARCHAR(30) NOT NULL
    send_date TIMESTAMP NOT NULL
    process_date DATE NOT NULL
    campaign_id VARCHAR(20) NOT NULL REFERENCES marketing_campaigns
    customer_id VARCHAR(20) NOT NULL REFERENCES customers
    send_channel VARCHAR(30) NOT NULL IN (Email, SMS, Push, WhatsApp, Voice)
    template_used VARCHAR(100)
    subject VARCHAR(200)
    send_status VARCHAR(20) NOT NULL IN (Sent, Failed, Bounced, Blocked)
    was_delivered BOOLEAN NOT NULL
    was_opened BOOLEAN
    open_date TIMESTAMP
    was_clicked BOOLEAN
    click_date TIMESTAMP
    click_count INTEGER
    had_conversion BOOLEAN NOT NULL
    conversion_date TIMESTAMP
    conversion_value DECIMAL(15,2)
    open_device VARCHAR(30)
    open_country VARCHAR(50)
    failure_reason VARCHAR(200)
    send_cost DECIMAL(10,4)
    """,
    key=("send_id",),
    event_date=EventDate("send_date"),
    daily=True,
)

DAILY_EXCHANGE_RATES = table(
    "daily_exchange_rates",
    f"""
    date DATE NOT NULL
    source_currency VARCHAR(3) NOT NULL {_CURRENCIES}
    target_currency VARCHAR(3) NOT NULL {_CURRENCIES}
    exchange_rate DECIMAL(12,6) NOT NULL
    buy_rate DECIMAL(12,6)
    sell_rate DECIMAL(12,6)
    source VARCHAR(50)
    """,
    key=("date", "source_currency", "target_currency"),
    event_date=EventDate("date"),
)

TABLES = (
    BRANCHES,
    CUSTOMERS,
    PRODUCTS,
    SERVICE_AGENTS,
    MARKETING_CAMPAIGNS,
    DAILY_EXCHANGE_RATES,
    TRANSACTIONS,
    CALL_CENTER_INTERACTIONS,
    CALL_TRANSCRIPTS,
    SATISFACTION_SURVEYS,
    COMPLAINTS,
    DIGITAL_EVENTS,
    CAMPAIGN_SENDS,
)

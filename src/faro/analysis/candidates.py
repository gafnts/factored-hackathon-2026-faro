"""
The four candidate workflows as ADR-0003 fixes them, written as SQL over the tables staged at the as-of instant.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

ACCOUNTS = ("Cuenta Corriente", "Cuenta Ahorro")
CARDS = ("Tarjeta Crédito", "Tarjeta Débito")
CREDIT_PRODUCTS = ("Tarjeta Crédito", "Préstamo Personal", "Préstamo Hipotecario")

DISPUTES = "disputes"
# The response codes card support explains, read with their ISO 8583 meanings (ADR-0003).
DECLINE_REASONS = ("05", "14", "51", "54")

# ADR-0003's mapping from complaint category and subcategory to candidate; complaints
# without a subcategory follow their category.
COMPLAINT_MAPPING: Mapping[tuple[str, str], str | None] = {
    ("Transactions", "Cargo no reconocido"): DISPUTES,
    ("Fees", "Cobro indebido"): DISPUTES,
    ("Technical", "Problema con app"): None,
    ("Service", "Calidad de servicio"): None,
    ("Branch", "Atención en sucursal"): None,
}


def sql_list(values: Sequence[str]) -> str:
    return "(" + ", ".join("'" + v.replace("'", "''") + "'" for v in values) + ")"


IS_ACCOUNT = f"product_type in {sql_list(ACCOUNTS)}"
IS_CARD = f"product_type in {sql_list(CARDS)}"
IS_CREDIT = f"product_type in {sql_list(CREDIT_PRODUCTS)}"
IS_PURCHASE = "transaction_type = 'Purchase'"
IS_APPROVED_PURCHASE = f"{IS_PURCHASE} and transaction_status = 'Approved'"
IS_DISPUTE = "category in " + sql_list(
    sorted(
        {category for (category, _), c in COMPLAINT_MAPPING.items() if c == DISPUTES}
    )
)


@dataclass(frozen=True)
class Scope:
    table: str
    rows: str
    fields: tuple[str, ...]
    # Fields the dictionary documents for only some rows, with the rows it names.
    only: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Rule:
    name: str
    # As table.field, like the references it follows (a product to its customer).
    reads: tuple[str, ...]
    follows: tuple[str, ...] = ()


@dataclass(frozen=True)
class Situation:
    # The E1 rule whose records it counts, as the report names it.
    description: str
    # Selects the customer_id of every customer whose records could back the situation.
    customers: str


@dataclass(frozen=True)
class Label:
    description: str
    # Which of ADR-0003's feature lists the rows carry: "transaction" or "credit".
    kind: str
    # Selects customer_id, label, and the fields the features come from, one row per example.
    rows: str


@dataclass(frozen=True)
class Candidate:
    key: str
    name: str
    normal_path: str
    scopes: tuple[Scope, ...]
    # Selects the customer_id of every customer in the normal-path state (F2).
    state: str
    rules: tuple[Rule, ...]
    # A request the workflow must decline or can't confirm (SCP-04), and a handoff (SCP-05).
    unsupported: Situation
    handoff: Situation
    # None when the dictionary holds no label for the candidate, which then fails E2.
    label: Label | None = None


def _transaction_rows(where: str) -> str:
    return (
        "select t.customer_id, t.is_fraud as label, t.fraud_score, t.transaction_type, "
        "t.transaction_category, t.channel, t.merchant_category, t.currency, t.amount, "
        "t.transaction_country, c.country as customer_country, t.transaction_date, "
        "t.product_type from transactions t "
        f"left join customers c on c.customer_id = t.customer_id where {where} "
        "order by t.transaction_id"
    )


def _active_with_recent(kind: str) -> str:
    return (
        f"select p.customer_id from products p where p.{kind} and p.product_status = 'Active' "
        "and exists (select 1 from transactions t where t.product_id = p.product_id "
        "and within(t.transaction_date, 30))"
    )


def _reads(table: str, *fields: str) -> tuple[str, ...]:
    return tuple(f"{table}.{f}" for f in fields)


ACCOUNT_INQUIRIES = Candidate(
    key="account_inquiries",
    name="Account and payment inquiries",
    normal_path="An active checking or savings account with a transaction in the 30 days before the business date",
    scopes=(
        Scope(
            "products",
            IS_ACCOUNT,
            ("product_type", "product_status", "current_balance", "currency"),
        ),
        Scope(
            "transactions",
            f"{IS_ACCOUNT} and within(transaction_date, 30)",
            (
                "transaction_date",
                "amount",
                "transaction_type",
                "transaction_status",
                "merchant_name",
                "channel",
            ),
            {"merchant_name": IS_PURCHASE},
        ),
    ),
    state=_active_with_recent(IS_ACCOUNT),
    rules=(
        Rule(
            "Balance and recent movements",
            _reads(
                "products", "customer_id", "product_type", "current_balance", "currency"
            )
            + _reads(
                "transactions",
                "product_id",
                "transaction_date",
                "amount",
                "transaction_type",
                "transaction_status",
                "merchant_name",
                "channel",
            ),
            ("products.customer_id", "transactions.product_id"),
        ),
        Rule(
            "Movement not found",
            _reads("transactions", "amount", "transaction_date"),
            ("transactions.product_id",),
        ),
        Rule(
            "Failed payment handed off",
            _reads("transactions", "transaction_type", "transaction_status"),
            ("transactions.product_id",),
        ),
    ),
    unsupported=Situation(
        "Movement not found: any account holder can ask about a movement that isn't there",
        f"select customer_id from products where {IS_ACCOUNT}",
    ),
    handoff=Situation(
        "Failed payment handed off: a declined or reversed payment in the last 30 days",
        f"select customer_id from transactions where {IS_ACCOUNT} "
        "and transaction_type = 'Payment' and transaction_status in ('Declined', 'Reversed') "
        "and within(transaction_date, 30)",
    ),
)

CARD_SUPPORT = Candidate(
    key="card_support",
    name="Card support",
    normal_path="An active credit or debit card with a transaction in the 30 days before the business date",
    scopes=(
        Scope(
            "products",
            IS_CARD,
            (
                "product_type",
                "product_status",
                "credit_limit",
                "current_balance",
                "expiration_date",
            ),
            {"credit_limit": "product_type = 'Tarjeta Crédito'"},
        ),
        Scope(
            "transactions",
            f"{IS_CARD} and within(transaction_date, 30)",
            (
                "transaction_status",
                "response_code",
                "is_fraud",
                "merchant_name",
                "channel",
                "transaction_country",
            ),
            {"merchant_name": IS_PURCHASE},
        ),
    ),
    state=_active_with_recent(IS_CARD),
    rules=(
        Rule(
            "Card status and available credit",
            _reads(
                "products",
                "customer_id",
                "product_type",
                "product_status",
                "credit_limit",
                "current_balance",
            ),
            ("products.customer_id",),
        ),
        Rule(
            "Declined transaction explained",
            _reads("transactions", "transaction_status", "response_code"),
            ("transactions.product_id",),
        ),
        Rule(
            "Card block, handed off over fraud",
            _reads("products", "product_status")
            + _reads(
                "transactions",
                "is_fraud",
                "merchant_name",
                "channel",
                "transaction_country",
            ),
            ("products.customer_id", "transactions.product_id"),
        ),
    ),
    unsupported=Situation(
        "Declined transaction explained: a decline in the last 30 days with a missing "
        "or unlisted `response_code`",
        f"select customer_id from transactions where {IS_CARD} "
        "and transaction_status = 'Declined' and within(transaction_date, 30) "
        f"and (response_code is null or response_code not in {sql_list(DECLINE_REASONS)})",
    ),
    handoff=Situation(
        "Card block, handed off over fraud: a transaction marked `is_fraud` in the last "
        "30 days on an active card",
        f"select customer_id from transactions t where {IS_CARD} and is_fraud "
        "and within(transaction_date, 30) and exists (select 1 from products p "
        "where p.product_id = t.product_id and p.product_status = 'Active')",
    ),
    label=Label(
        "`is_fraud` on card transactions", "transaction", _transaction_rows(IS_CARD)
    ),
)

DISPUTE_INTAKE = Candidate(
    key=DISPUTES,
    name="Transaction-dispute intake",
    normal_path="An approved purchase in the 60 days before the business date",
    scopes=(
        Scope(
            "transactions",
            f"{IS_APPROVED_PURCHASE} and within(transaction_date, 60)",
            (
                "transaction_date",
                "amount",
                "currency",
                "transaction_status",
                "merchant_name",
                "is_fraud",
                "fraud_score",
            ),
        ),
        Scope("complaints", IS_DISPUTE, ("case_type", "claimed_amount", "status")),
    ),
    state=(
        f"select customer_id from transactions "
        f"where {IS_APPROVED_PURCHASE} and within(transaction_date, 60)"
    ),
    rules=(
        Rule(
            "Eligibility to dispute",
            _reads(
                "transactions",
                "customer_id",
                "transaction_type",
                "transaction_status",
                "transaction_date",
            ),
            ("transactions.customer_id",),
        ),
        Rule(
            "Case facts from the transaction",
            _reads(
                "transactions",
                "transaction_id",
                "transaction_date",
                "amount",
                "currency",
                "merchant_name",
            ),
            ("transactions.customer_id",),
        ),
        Rule(
            "Fraud or merchant route",
            _reads("transactions", "is_fraud"),
            ("transactions.customer_id",),
        ),
    ),
    unsupported=Situation(
        "Eligibility to dispute: a transaction in the last 60 days that isn't an approved "
        "purchase",
        "select customer_id from transactions where within(transaction_date, 60) "
        f"and not ({IS_APPROVED_PURCHASE})",
    ),
    handoff=Situation(
        "Fraud or merchant route: an approved purchase in the last 60 days marked "
        "`is_fraud`, routed to human review",
        f"select customer_id from transactions where {IS_APPROVED_PURCHASE} "
        "and within(transaction_date, 60) and is_fraud",
    ),
    label=Label(
        "`is_fraud` on the transactions a customer could dispute (approved purchases)",
        "transaction",
        _transaction_rows(IS_APPROVED_PURCHASE),
    ),
)

CREDIT_ELIGIBILITY = Candidate(
    key="credit",
    name="Credit information and eligibility",
    normal_path="An active customer with `credit_score` and `estimated_monthly_income` populated",
    scopes=(
        Scope(
            "customers",
            "true",
            ("credit_score", "estimated_monthly_income", "segment", "customer_status"),
        ),
        Scope(
            "products",
            IS_CREDIT,
            ("product_type", "credit_limit", "interest_rate", "days_past_due"),
        ),
    ),
    state=(
        "select customer_id from customers where customer_status = 'Active' "
        "and credit_score is not null and estimated_monthly_income is not null"
    ),
    rules=(
        Rule(
            "Held credit products",
            _reads(
                "products",
                "customer_id",
                "product_type",
                "credit_limit",
                "interest_rate",
                "days_past_due",
            ),
            ("products.customer_id",),
        ),
        Rule(
            "Simulated eligibility",
            _reads(
                "customers",
                "customer_status",
                "segment",
                "credit_score",
                "estimated_monthly_income",
            )
            + _reads("products", "days_past_due"),
            ("products.customer_id",),
        ),
        Rule(
            "Review path",
            _reads("customers", "credit_score", "estimated_monthly_income"),
        ),
    ),
    unsupported=Situation(
        "Simulated eligibility, negative: an active, scored customer with a credit product "
        "30 or more days past due",
        "select c.customer_id from customers c where c.customer_status = 'Active' "
        "and c.credit_score is not null and c.estimated_monthly_income is not null "
        "and exists (select 1 from products p where p.customer_id = c.customer_id "
        f"and {IS_CREDIT} and p.days_past_due >= 30)",
    ),
    handoff=Situation(
        "Review path: an active customer missing `credit_score` or "
        "`estimated_monthly_income`",
        "select customer_id from customers where customer_status = 'Active' "
        "and (credit_score is null or estimated_monthly_income is null)",
    ),
    label=Label(
        "`days_past_due` of 30 or more on credit products",
        "credit",
        "select p.customer_id, p.days_past_due >= 30 as label, c.segment, c.country, "
        "c.credit_score, c.estimated_monthly_income, c.date_of_birth, c.registration_date, "
        "p.product_type, p.interest_rate, p.credit_limit, p.opening_date from products p "
        f"left join customers c on c.customer_id = p.customer_id where {IS_CREDIT} "
        "and p.days_past_due is not null order by p.product_id",
    ),
)

CANDIDATES = (ACCOUNT_INQUIRIES, CARD_SUPPORT, DISPUTE_INTAKE, CREDIT_ELIGIBILITY)

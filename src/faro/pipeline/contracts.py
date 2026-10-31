"""
The bronze contracts (ADR-0006, Contracts), written from the data dictionary (analysis/catalog.py) and
pipeline/contracts/corrections.yml: each column's name, order, type, nullability, accepted values, and personal-data
tag; the typed read that holds every file to them; and bronze's models with their checks, each at the severity ADR-0006
fixes (Quality checks). The files are committed; `python -m faro.pipeline contracts` rewrites them, and a test
fails when the committed copies aren't what this module writes.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from faro.analysis.catalog import TABLES, Column, Table
from faro.pipeline.runner import PROJECT

CORRECTIONS = PROJECT / "contracts" / "corrections.yml"
MODELS = PROJECT / "models" / "bronze"
SOURCES = "_sources.yml"
BRONZE = "_bronze.yml"
SOURCE = "snapshot"
TAGS = ("none", "identity", "contact", "financial", "card_number")
HEADER = (
    "# Written by `python -m faro.pipeline contracts` from the data dictionary "
    "(src/faro/analysis/catalog.py)\n# and contracts/corrections.yml. Don't edit it by hand.\n"
)
CATALOG = {t.name: t for t in TABLES}

# The rules that stop the build (ADR-0006, Quality checks), besides keys, the partition day, and the files and records
# read. Every other rule the dictionary states is counted as a warning: the delivery breaks several of them, among them
# registration_branch_id, which names no branch in 149,995 of 150,000 customers (profile).
REQUIRED_REFERENCES = frozenset(
    {("transactions", "product_id"), ("products", "customer_id")}
)
REQUIRED_VALUES = frozenset(
    {
        ("branches", "branch_status"),
        ("customers", "customer_status"),
        ("products", "product_status"),
        ("products", "product_type"),
        ("service_agents", "agent_status"),
        ("marketing_campaigns", "campaign_status"),
        ("transactions", "transaction_type"),
        ("transactions", "transaction_status"),
        ("complaints", "status"),
        ("campaign_sends", "send_status"),
    }
)
# dbt's unique and accepted_values tests return a row per value; the manifest counts rows.
ROWS = "coalesce(sum(n_records), 0)"


class ContractError(Exception):
    pass


@dataclass(frozen=True)
class ColumnContract:
    name: str
    # As the dictionary writes it, for the record.
    dictionary: str
    type: str
    nullable: bool
    dictionary_not_null: bool
    values: tuple[str, ...]
    dictionary_values: tuple[str, ...]
    references: str | None
    personal_data: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class TableContract:
    table: Table
    columns: tuple[ColumnContract, ...]
    # How many days after its process date a daily table's row may be dated: its processing day runs to a cutoff the
    # next morning (ADR-0003).
    days_after: int = 1

    @property
    def name(self) -> str:
        return self.table.name

    def column(self, name: str) -> ColumnContract:
        for column in self.columns:
            if column.name == name:
                return column
        raise KeyError(f"{self.name} has no column {name}")


def load(path: Path = CORRECTIONS) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded


def dictionary_line(column: Column) -> str:
    line = column.type + (" NOT NULL" if column.not_null else "")
    if column.references:
        line += f" REFERENCES {column.references}"
    if column.values:
        line += f" IN ({', '.join(column.values)})"
    return line


def duckdb_type(column: Column) -> str:
    if column.base_type in ("VARCHAR", "TEXT"):
        return "VARCHAR"
    if column.base_type == "DECIMAL":
        return column.type
    return column.base_type


def _tags(table: Table, tagged: Mapping[str, Sequence[str]] | None) -> dict[str, str]:
    tags: dict[str, str] = {}
    for tag, names in (tagged or {}).items():
        if tag not in TAGS or tag == "none":
            raise ContractError(f"{table.name}: {tag} isn't a personal-data tag")
        for name in names:
            table.column(name)
            if name in tags:
                raise ContractError(f"{table.name}.{name} has two personal-data tags")
            tags[name] = tag
    return tags


def _column(
    column: Column, correction: Mapping[str, Any] | None, tag: str
) -> ColumnContract:
    values = column.values
    nullable = not column.not_null
    reasons: tuple[str, ...] = ()
    if correction is not None:
        if not correction.get("reason"):
            raise ContractError(f"the correction to {column.name} gives no reason")
        changes = set(correction) - {"reason"}
        if not changes or changes - {"values", "nullable"}:
            raise ContractError(
                f"the correction to {column.name} changes {sorted(changes)}; "
                "a correction changes values or nullable"
            )
        values = tuple(correction.get("values", values))
        nullable = bool(correction.get("nullable", nullable))
        reasons = (" ".join(str(correction["reason"]).split()),)
    return ColumnContract(
        name=column.name,
        dictionary=dictionary_line(column),
        type=duckdb_type(column),
        nullable=nullable,
        dictionary_not_null=column.not_null,
        values=values,
        dictionary_values=column.values,
        references=column.references,
        personal_data=tag,
        reasons=reasons,
    )


def build(
    tables: Sequence[Table] = TABLES, loaded: Mapping[str, Any] | None = None
) -> tuple[TableContract, ...]:
    loaded = load() if loaded is None else loaded
    known = {t.name: t for t in tables}
    corrections: Mapping[str, Mapping[str, Any]] = loaded.get("corrections") or {}
    tagged: Mapping[str, Mapping[str, Sequence[str]]] = (
        loaded.get("personal_data") or {}
    )
    dated: Mapping[str, Mapping[str, Any]] = loaded.get("dated_after_processing") or {}
    for name in (*corrections, *tagged, *dated):
        if name not in known:
            raise ContractError(f"{name} isn't a table of the dictionary")
    for name, correction in dated.items():
        if not known[name].daily or not correction.get("reason"):
            raise ContractError(
                f"{name}'s processing days need a daily table and a reason"
            )
    contracts = []
    for table in tables:
        corrected = corrections.get(table.name) or {}
        for name in corrected:
            table.column(name)
        tags = _tags(table, tagged.get(table.name))
        contracts.append(
            TableContract(
                table,
                tuple(
                    _column(c, corrected.get(c.name), tags.get(c.name, "none"))
                    for c in table.columns
                ),
                int((dated.get(table.name) or {}).get("days", 1)),
            )
        )
    return tuple(contracts)


def pattern(table: Table) -> str:
    if table.daily:
        return f"$root/{table.name}/**/*.csv"
    return f"$root/{table.name}.csv"


def location(contract: TableContract) -> str:
    """
    The typed read, with $root for the snapshot's directory: the contract's columns and types, never inferred ones, and
    no TRY_CAST, so a value that doesn't fit stops the build; each row keeps the file it came from. The partitions'
    year=/month=/day= directories aren't columns.
    """
    columns = ", ".join(f"'{c.name}': '{c.type}'" for c in contract.columns)
    return (
        f"read_csv('{pattern(contract.table)}', columns = {{{columns}}}, header = true, "
        "auto_detect = false, delim = ',', quote = '\"', escape = '\"', strict_mode = true, "
        "hive_partitioning = false, filename = true)"
    )


def _describe_table(table: Table) -> str:
    layout = (
        f"Daily partitions, {table.name}/year=/month=/day=/{table.name}_YYYYMMDD.csv"
        if table.daily
        else f"One file, {table.name}.csv"
    )
    return f"{layout}; keyed by {', '.join(table.key)}."


def _describe_column(column: ColumnContract) -> str:
    return " ".join(
        [f"Dictionary: {column.dictionary}."]
        + [f"Corrected: {reason}" for reason in column.reasons]
    )


def sources(contracts: Iterable[TableContract]) -> dict[str, Any]:
    return {
        "sources": [
            {
                "name": SOURCE,
                "description": "The pinned snapshot's files (ADR-0002), each read with its table's contract.",
                "config": {
                    "meta": {
                        "root": "{{ var('snapshot_root') }}",
                        "formatter": "template",
                    }
                },
                "tables": [
                    {
                        "name": contract.name,
                        "description": _describe_table(contract.table),
                        "config": {"meta": {"external_location": location(contract)}},
                        "columns": [
                            {
                                "name": column.name,
                                "data_type": column.type,
                                "description": _describe_column(column),
                                "config": {
                                    "meta": {"personal_data": column.personal_data}
                                },
                            }
                            for column in contract.columns
                        ],
                    }
                    for contract in contracts
                ],
            },
            {
                "name": "checks",
                "description": "What the build loads before dbt reads a file: the lock's files, and each file's "
                "records as Python's csv module counts them.",
                "schema": "checks",
                "tables": [
                    {"name": "lock_files", "description": "Every file the lock lists."},
                    {
                        "name": "record_counts",
                        "description": "Each locked file's records, counted apart from DuckDB's reader.",
                    },
                ],
            },
        ]
    }


def _test(
    kind: str,
    name: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    warn: bool = False,
    rows: bool = False,
    store: bool = False,
) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if warn:
        config["severity"] = "warn"
    if rows:
        config["fail_calc"] = ROWS
    # Only checks whose rows are file keys keep them, so the build can name the file.
    if store:
        config["store_failures"] = True
    entry: dict[str, Any] = {"name": name}
    if arguments:
        entry["arguments"] = dict(arguments)
    if config:
        entry["config"] = config
    return {kind: entry}


def _column_tests(
    contract: TableContract, column: ColumnContract
) -> list[dict[str, Any]]:
    table, key = contract.table, contract.table.key
    name = f"bronze_{table.name}_{column.name}"
    tests = []
    if column.name in key:
        tests.append(_test("not_null", f"{name}_key_not_null"))
        if len(key) == 1:
            tests.append(_test("unique", f"{name}_key_unique", rows=True))
    elif column.dictionary_not_null:
        tests.append(_test("not_null", f"{name}_not_null", warn=True))
    if (table.name, column.name) in REQUIRED_VALUES:
        if not column.values:
            raise ContractError(f"{table.name}.{column.name} has no values to accept")
        tests.append(
            _test(
                "accepted_values",
                f"{name}_accepted_values",
                {"values": list(column.values)},
                rows=True,
            )
        )
    elif column.dictionary_values:
        tests.append(
            _test(
                "accepted_values",
                f"{name}_dictionary_values",
                {"values": list(column.dictionary_values)},
                warn=True,
                rows=True,
            )
        )
    if column.name in table.unique:
        tests.append(_test("unique", f"{name}_unique", warn=True, rows=True))
    if column.references:
        parent = CATALOG[column.references]
        tests.append(
            _test(
                "relationships",
                f"{name}_references_{parent.name}",
                {"to": f"ref('bronze_{parent.name}')", "field": parent.key[0]},
                warn=(table.name, column.name) not in REQUIRED_REFERENCES,
            )
        )
    return tests


def _model_tests(contract: TableContract) -> list[dict[str, Any]]:
    table = contract.table
    name = f"bronze_{table.name}"
    tests = []
    if len(table.key) > 1:
        tests.append(
            _test(
                "unique_combination",
                f"{name}_key_unique",
                {"columns": list(table.key)},
                rows=True,
            )
        )
    if table.daily and table.event_date is not None:
        event = table.event_date
        arguments: dict[str, Any] = {
            "event_date": event.column,
            "days_after": contract.days_after,
        }
        if event.via is not None:
            via = table.column(event.via)
            arguments |= {
                "via": via.name,
                "parent": f"ref('bronze_{via.references}')",
                "parent_date": event.column,
            }
        tests.append(
            _test(
                "processed_on_its_day",
                f"{name}_processed_on_its_day",
                arguments,
                rows=True,
                store=True,
            )
        )
    tests += [
        _test(
            "files_match_the_lock",
            f"{name}_files_match_the_lock",
            {"table": table.name},
            store=True,
        ),
        _test(
            "rows_match_the_records",
            f"{name}_rows_match_the_records",
            {"table": table.name},
            store=True,
        ),
    ]
    return tests


def models(contracts: Iterable[TableContract]) -> dict[str, Any]:
    return {
        "models": [
            {
                "name": f"bronze_{contract.name}",
                "description": f"Every row of {contract.name}, typed by its contract, with the file it came from "
                "and the snapshot; nothing dropped, deduplicated, or corrected.",
                "data_tests": _model_tests(contract),
                "columns": [
                    {
                        "name": column.name,
                        "data_type": column.type,
                        "config": {"meta": {"personal_data": column.personal_data}},
                        **(
                            {"data_tests": tests}
                            if (tests := _column_tests(contract, column))
                            else {}
                        ),
                    }
                    for column in contract.columns
                ]
                + [
                    {
                        "name": "source_file",
                        "data_type": "VARCHAR",
                        "description": "The file's key in the lock, which ties the row to its SHA-256 and the "
                        "organizers' object (ADR-0006, Lineage).",
                        "config": {"meta": {"personal_data": "none"}},
                    },
                    {
                        "name": "snapshot_id",
                        "data_type": "VARCHAR",
                        "config": {"meta": {"personal_data": "none"}},
                    },
                ],
            }
            for contract in contracts
        ]
    }


class _Dumper(yaml.SafeDumper):
    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow, False)


def _string(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    # SQL quotes with ', which YAML's single-quoted style would double.
    style = '"' if "'" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _string)


def _yaml(document: Mapping[str, Any]) -> str:
    return HEADER + yaml.dump(
        document, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=10_000
    )


def render(contracts: Sequence[TableContract]) -> dict[str, str]:
    """
    Every file under models/bronze/, by name.
    """
    rendered = {SOURCES: _yaml(sources(contracts)), BRONZE: _yaml(models(contracts))}
    for contract in contracts:
        rendered[f"bronze_{contract.name}.sql"] = (
            f"{{{{ bronze('{contract.name}') }}}}\n"
        )
    return rendered


def write(contracts: Sequence[TableContract], directory: Path | None = None) -> Path:
    directory = directory or MODELS
    directory.mkdir(parents=True, exist_ok=True)
    rendered = render(contracts)
    for stale in directory.iterdir():
        if stale.is_file() and stale.name not in rendered:
            stale.unlink()
    for name, text in rendered.items():
        (directory / name).write_text(text, encoding="utf-8")
    return directory

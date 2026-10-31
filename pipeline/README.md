# Pipeline

The dbt project that builds the tools' data from the pinned snapshot ([ADR-0006](../docs/adr/0006-batch-medallion-pipeline.md)): a full rebuild per snapshot, in one DuckDB file on the machine that holds the snapshot, with only gold leaving it.

| Layer | Holds | Read by |
|---|---|---|
| Bronze | All 13 tables, every row typed by its contract, with the key of its file and the snapshot ID | Silver; the evaluation's oracle |
| Silver | Card support at the as-of instant (customers, cards, card transactions), flagged and split, and the business clock | Gold; the evaluation's case generator |
| Gold | What the tools read: customers, cards, the 90-day window of card transactions, and one metadata row with the stamp and the clock | `make export` |

## Running it

```bash
make data        # The pinned snapshot, if it isn't under data/ yet
make pipeline    # Build every layer and run every check
make export      # Write gold's items and manifest, and upload them (needs AWS_PROFILE)
```

`make pipeline` and `make export` take `DATA_DIR` (default `data`). The build prints each check that didn't pass with its row count, never a value (SEC-03); dbt's own log can quote a row, so it stays under `data/pipeline/<snapshot>/logs/`. `make contracts` rewrites bronze's YAML from the dictionary and `contracts/corrections.yml`.

| Written to | What |
|---|---|
| `data/pipeline/<snapshot>.duckdb` | Every layer, rebuilt from nothing by each `make pipeline` |
| `data/exports/<snapshot>/<version>/` | Gold's items in parts, with the manifest beside them; the upload sends the manifest last |
| `docs/pipeline/<snapshot>-<version>.json` | The same manifest, committed: the stamp, the clock, rows per model, a content hash per item kind, the export's objects, and every check's result with its rows and share, counts under 10 suppressed |

The version hashes everything that shapes an export: this directory (without this README and dbt's working directories), `src/faro/pipeline/`, the clock's rule, the export's writer, the tools' data contract, and the installed dbt-core, dbt-duckdb, and DuckDB. `make export` refuses gold that other code built.

## Checks

Before dbt reads a file, the build checks that the snapshot holds exactly the lock's files and that each header is its contract's, and counts each file's records with Python's `csv` module. The checks that stop the build are the ones ADR-0006 lists: keys; each transaction's product and each product's customer; the accepted statuses and types; each row in its processing day's partition; the files and rows read against the lock and the counts; a value that doesn't cast to its type; a card transaction whose customer isn't its card's; and gold's window and contract. Every other rule of the dictionary is a warning: counted in rows, never fixed, and recorded in the manifest.

The pre-push hook and CI build the [team-generated fixture](../tests/fixtures/team-generated/README.md) instead of the snapshot, with dbt's unit tests and the update-correctness tests (DML-06).

## Layout

| Path | Holds |
|---|---|
| `contracts/corrections.yml` | Where the delivery differs from the dictionary, each difference with its reason, and each column's personal-data tag |
| `models/bronze/` | One model per table, and the sources and tests that `make contracts` writes |
| `models/silver/`, `models/gold/` | The models, their enforced contracts, and silver's unit tests |
| `macros/` | The bronze read, the schema names, and silver's text rules |
| `tests/` | The checks dbt doesn't ship, and the tests of one rule each |

The CLI behind the targets is `src/faro/pipeline/`, tested under `tests/faro/pipeline/`.

## On the pinned snapshot

Snapshot `b3b8b248f604ef9a`, pipeline version `795ff66b819516bf`, on an Apple M2 Pro with 16 GB, on 2026-09-30:

| Step | Time | Result |
|---|---:|---|
| `make pipeline` | 40 s | 23,495,188 rows in bronze; 79 checks that stop the build and 3 unit tests passed; 19 of the 205 warnings found rows, as the [profile](../docs/analysis/profiling.md) measured |
| `make export`, before the upload | 83 s | 419,571 items in 9 parts of about 2.2 MB; business date 2026-06-17, as of 2026-06-18 06:00 |

Its manifest is [docs/pipeline/b3b8b248f604ef9a-795ff66b819516bf.json](../docs/pipeline/b3b8b248f604ef9a-795ff66b819516bf.json). Two builds of the snapshot gave the same manifest, byte for byte.

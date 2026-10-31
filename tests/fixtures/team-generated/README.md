# Team-generated fixture snapshots

Miniature snapshots of the 13 tables, written by the team for the pipeline's tests (SEC-02). They hold no organizer data: every row was made up, every ID carries `TEAM`, and every name, email, and phone is a placeholder. The files follow the delivery's layout, headers, byte order mark, and ID formats, so the pipeline reads them as it reads the pinned snapshot.

`python -m tests.faro.pipeline.fixture` writes each version's files and lock from `tests/faro/pipeline/fixture.py`, and a test fails when the committed copies differ from what it writes.

| Version | What it holds |
|---|---|
| `base` | Every table, with one settled partition (2026-05-01) and the last few days of each daily table. Complaints end a day before the others, so the business date is 2026-06-16 and the as-of instant 2026-06-17 06:00. Customers and cards cover the cases the tools' data decides: a held-out customer, a customer and a card updated after the as-of instant, a card opened on the as-of day and one after it, an active card past its expiration, a blocked card, a savings account, transactions at each edge of the 90-day window and of the as-of instant, and a Mexican charge spelled without its accent. |
| `update` | The next delivery: the base with complaints' partition for 2026-06-17, which completes that day, and transactions' for 2026-06-18, whose one charge falls after the new as-of instant. The business date moves to 2026-06-17 and the as-of instant to 2026-06-18 06:00. Card `PRD-TEAM00000041` was blocked after that instant. |
| `update-extra-column` | The update, with a column added to transactions' 2026-06-18 partition. |
| `update-renamed-column` | The update, with `status` renamed in complaints' 2026-06-17 partition and the column count unchanged. |

The two versions and the broken variants are ADR-0006's fixture for update correctness (DML-06); `tests/faro/pipeline/test_update.py` builds them all.

# Architecture Decision Records

The decisions someone could question later, each with its context, the alternatives it beat, and the consequences we accepted. Each ADR describes its subject as built: a correction found while building is applied in the section it concerns and listed, dated, under the ADR's Status, so that list is the map from the design to the build. The index below only says how far the amendments run.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-deploy-to-us-east-1.md) | Deploy to us-east-1 | Accepted 2026-09-25 |
| [0002](0002-mirror-dataset-into-pinned-snapshots.md) | Mirror the organizer dataset into pinned snapshots | Accepted 2026-09-26; amended 2026-10-30 |
| [0003](0003-choose-workflow-from-evidence.md) | Choose the workflow from evidence | Accepted 2026-09-27; amended by ADR-0004 |
| [0004](0004-agent-architecture-on-agentcore.md) | Explicit LangGraph workflow on AgentCore, with policy enforced in Gateway tools and Cedar | Accepted 2026-09-29; amended through 2026-10-31 |
| [0005](0005-offline-scenario-evaluation.md) | Offline scenario evaluation against an independent policy oracle | Accepted 2026-09-29; amended through 2026-10-03 |
| [0006](0006-batch-medallion-pipeline.md) | Batch medallion pipeline in dbt-duckdb, exported per snapshot to the tools' store | Accepted 2026-09-29; amended through 2026-09-30 |
| [0007](0007-role-gated-web-app.md) | Role-gated web app on one CloudFront origin, with a polled handoff console | Accepted 2026-09-29; amended through 2026-10-03 |
| [0008](0008-pin-toolchain-by-layer.md) | Pin the toolchain by layer, with mise for the dev environment | Accepted 2026-10-31 |
| [0009](0009-release-checked-deploys.md) | Release only checked deploys of credential-free builds | Proposed 2026-10-31 |

## Conventions

| Element | Rule |
|---|---|
| File name | `NNNN-short-slug.md`: a sequential 4-digit number and a few words from the title, without articles |
| Title | A formal phrase naming the decision, as a noun phrase or an imperative: *Offline scenario evaluation against an independent policy oracle*, *Choose the workflow from evidence* |
| Status | `Proposed (date)`, then `Accepted (date)`; a reversed decision becomes `Superseded by ADR-NNNN`, and the old ADR stays |
| Amendments | A correction to an accepted ADR is applied in the section it corrects, which then reads as built, and listed under Status as a dated line naming that section; when a later ADR makes the correction, the line links to it |
| Open decisions | A proposed ADR marks each **Open** where it arises and lists them with the option we lean towards; acceptance settles them under *Settled at acceptance*, each stating the choice and the alternative not taken |

## Template

```markdown
# ADR-NNNN: Title

## Status

Proposed (YYYY-MM-DD).

## Context

The forces at play: the problem, the constraints, and the evidence that makes a decision necessary.

## Decision

What we will do, stated in full sentences.

## Alternatives considered

Each option that was on the table and why it lost.

## Consequences

Positive:
- ...

Negative:
- ...
```

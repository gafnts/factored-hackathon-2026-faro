# Documentation

What we decided about Faro, why, and the evidence behind it. Every document cites the requirement IDs in [prerequisites.md](prerequisites.md), and the policy's rule IDs wherever a rule applies. Terms are in the [glossary](glossary.md).

## Reading order

The first four take twenty minutes. The rest is the appendix: the full reasoning behind each decision, and the alternatives it beat.

1. [The product brief](product/brief.md): what Faro is for.
2. [The architecture](architecture.md): the whole system on one page.
3. [The evaluation report](evaluation/report.md): what we measured.
4. [The limitations](evaluation/limitations.md): what that leaves out.
5. [ADR-0003](adr/0003-choose-workflow-from-evidence.md) and the [selection report](analysis/selection.md): why card support.
6. [The policy](policy/card-support.md): the rules Faro follows.
7. [ADR-0004](adr/0004-agent-architecture-on-agentcore.md): what runs where, and what enforces each rule.
8. [ADR-0005](adr/0005-offline-scenario-evaluation.md) and [ADR-0011](adr/0011-pre-registered-studies.md): how we know it works.
9. [ADR-0006](adr/0006-batch-medallion-pipeline.md) and [ADR-0007](adr/0007-role-gated-web-app.md): the data pipeline and the web app.

## Contents

| Path | Holds |
|---|---|
| [adr/](adr/README.md) | The architecture decision records and their index |
| [analysis/](analysis/) | The profiling, traffic, workflow selection, and card support reports, with their JSON and figures, from `make analysis` |
| [architecture.md](architecture.md) | The system on one page |
| [evaluation/](evaluation/) | The [report](evaluation/report.md), the [results](evaluation/results.md), the [limitations](evaluation/limitations.md), the request [families](evaluation/families.md) and the [outside source](evaluation/outside.md), the [studies](evaluation/studies/README.md), the [disagreement log](evaluation/disagreements.md), the [run index](evaluation/runs.md), the [language check](evaluation/language.md), and the manifests of the sets and the reported runs |
| [glossary.md](glossary.md) | The repository's own terms, in plain words |
| [images/](images/) | The README's banner, screenshots, and diagrams |
| [pipeline/](pipeline/) | The manifest of each gold export, from `make export` ([the pipeline](../pipeline/README.md)) |
| [policy/](policy/) | The [card support policy](policy/card-support.md): what Faro answers, does, and refuses, one ID per rule |
| [prerequisites.md](prerequisites.md) | What the hackathon's organizers evaluated, with the stable requirement IDs our documents cite |
| [product/](product/) | The [product brief](product/brief.md) and the [identity guide](product/identity.md) |
| [slides.pdf](slides.pdf) | The pitch as presented at the award ceremony, on 2026-10-23; its figures are that day's |

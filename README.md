<p align="center">
  <a href="https://faro.gabriel.com.gt"><img src="docs/images/banner.png" alt="Faro" width="100%"></a>
</p>
<p align="center">
  <strong>A card support agent for LATAM Bank, in Spanish and Portuguese. <br>
  It explains the records, blocks a card only when its holder confirms, and hands off with a case file.</strong>
</p>
<p align="center">
  <a href="#introduction"><strong>Introduction</strong></a> ·
  <a href="#architecture"><strong>Architecture</strong></a> ·
  <a href="#evaluation"><strong>Evaluation</strong></a> ·
  <a href="#limitations"><strong>Limitations</strong></a> ·
  <a href="#self-hosting"><strong>Self-hosting</strong></a> ·
  <a href="#contributing"><strong>Contributing</strong></a>
</p>
<p align="center">
  <a href="https://github.com/gafnts/factored-hackathon-2026-faro/actions/workflows/quality-gates.yml"><img src="https://github.com/gafnts/factored-hackathon-2026-faro/actions/workflows/quality-gates.yml/badge.svg" alt="Quality gates"></a>
  <a href="https://github.com/gafnts/factored-hackathon-2026-faro/actions/workflows/advisories.yml"><img src="https://github.com/gafnts/factored-hackathon-2026-faro/actions/workflows/advisories.yml/badge.svg" alt="Advisories"></a>
  <a href="https://github.com/gafnts/factored-hackathon-2026-faro/actions/workflows/deploy-prototype.yml"><img src="https://github.com/gafnts/factored-hackathon-2026-faro/actions/workflows/deploy-prototype.yml/badge.svg" alt="Deploy prototype"></a>
  <a href="https://faro.gabriel.com.gt"><img src="https://img.shields.io/website?url=https%3A%2F%2Ffaro.gabriel.com.gt&label=prototype&up_message=live&down_message=down" alt="Prototype"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License"></a>
</p>

> [!NOTE]
> **For the judges.** The prototype runs at [faro.gabriel.com.gt](https://faro.gabriel.com.gt). The credentials for its customer personas and the human agent reached the organizers with the submission and are in no file here. The [slides](docs/slides.pdf) are in this repository and the [video pitch](https://www.youtube.com/watch?v=3lSxTLhZ7FY) is unlisted on YouTube. Anyone else can deploy their own copy: see [Self-hosting](#self-hosting).

---

## Introduction

Built for the Factored AI &amp; Data Hackathon 2026. In Spanish, *faro* is a lighthouse; in Portuguese, *ter faro* is to have a nose for things. Guidance in one language, judgment in the other: Faro grounds every answer in the cardholder's own records, enforces permissions in code rather than in the prompt, and gives a human agent a structured case file whenever a request needs one.

A LATAM Bank customer spots a charge on their credit card that they didn't make, and writes to the [bank's chat](https://faro.gabriel.com.gt/chat) in Portuguese: *Não reconheço uma compra no meu cartão.* Faro finds the charge among the card's transactions and offers to block the card. A typed *sim* does nothing: the chat shows a button that names the card and the reason, and only that button confirms. Once the customer presses it, the tool reads the card back, and Faro reports the block because that read shows it. Then it files the case to dispute intake and gives the customer a reference.

<p align="center">
  <a href="https://faro.gabriel.com.gt"><img src="docs/images/chat.png" alt="The chat: the charge found, the block confirmed with the button, the reference given" width="100%"></a>
</p>

In the [console](https://faro.gabriel.com.gt/cases), a human agent sees the case within seconds: the request, every verified fact next to the tool call that read it, the confirmed block, and the customer's own words kept apart from the facts. Nothing to read back, nothing to ask twice. Had the customer cancelled the block, the card would still be live with a charge they don't recognize, so the same case would have arrived marked urgent, for a person to act on first.

<p align="center">
  <a href="https://faro.gabriel.com.gt/cases"><img src="docs/images/cases.png" alt="The console: the case with its evidence, seconds after the chat filed it" width="100%"></a>
</p>

> [!IMPORTANT]
> LATAM Bank and every customer in it are synthetic, from the organizers' dataset. This journey runs end to end on the prototype, and the browser suite replays it as a test, in Portuguese, against each deployed stack before we promote it ([ADR-0007](docs/adr/0007-role-gated-web-app.md#judges-access)).

### Why Faro exists

A card is declined, goes missing, or shows a charge its holder didn't make. At this bank, a person answers every one of those moments. The contact center logs about 500 contacts a day; a customer waits about two minutes to be heard and then keeps a person for five more, and 35% of all contacts fall in the category nearest card support. Faro resolves 65% of held-out card contacts without a person, for under a cent each in model calls. For every thousand contacts, that is about 650 answered on the spot and 37 hours of a person's time freed. At 1 to 5 USD per human contact, it is 640 to 3,240 USD saved: a projection, since the data holds no cost per contact (see [the ROI section of the report](docs/evaluation/report.md#roi-a-projection)).

### What sets Faro apart

- **A lighthouse doesn't steer the ship.** Faro shows what the records say and proposes the one action it can take. The customer's button decides; typed text never does.
- **Judgment in code, not in the prompt.** The tools and Cedar decide every access and action, so a fully compromised model still can't read another customer's card or block one unconfirmed.
- **Done means verified.** A block is reported only after the card is read back, and handed to a person when the read doesn't show it.
- **Candid about its records.** A missing field is reported as not recorded; conflicting records are stated side by side, never reconciled.
- **Graded by an independent oracle.** Expected outcomes come from the policy applied to the frozen bank, in code that shares nothing with Faro's tools, and results are reported with their failures.

The [product brief](docs/product/brief.md) says who Faro serves and what it refuses to do; the [policy](docs/policy/card-support.md) holds every rule it follows, one ID per rule; the [glossary](docs/glossary.md) explains the terms; [docs/](docs/README.md) indexes the rest.

---

## Architecture

One backend on Amazon Bedrock AgentCore. A Runtime serves the chat and runs an explicit LangGraph workflow: code chooses every step and tool, and the model only labels, extracts, and writes words around placeholders that code fills. A Gateway exposes the banking tools as Lambda functions under Cedar policies, so every call must name the customer and the sign-in in the token. DynamoDB holds every store, and a dbt pipeline builds the tools' data from a pinned snapshot of the bank.

<p align="center">
  <img src="docs/images/architecture.png" alt="The system" width="100%">
</p>

The stack: AgentCore Runtime and Gateway, LangGraph, Cognito, Cedar, DynamoDB, Claude Haiku 4.5, dbt with DuckDB, React with assistant-ui, and Terraform for all of it. [architecture.md](docs/architecture.md) is the system on one page; the [architecture decision records](docs/adr/README.md) hold the reasoning.

---

## Evaluation

Offline: scripted conversations played against the deployed stack and graded by code against an oracle, our own code that applies the policy to the frozen bank and shares nothing with Faro's tools. The cases come from request families we wrote, in both languages; a third of the families and a fifth of the customers were held out and never read during development. Every run writes a manifest with its code, data, policy, prompt, and model versions.

<p align="center">
  <img src="docs/images/evaluation.png" alt="The evaluation" width="100%">
</p>

The held-out set, 608 cases played three times against the frozen agent, beside a deterministic baseline on the same cases:

| ID | Metric | Faro | Baseline |
|---|---|---|---|
| M-01 | Safe automated resolution, of 602 conversation cases | 65% | 43% |
| M-02 | Ended without a transfer, of 602 | 78% | 85% |
| M-03 | Required handoffs transferred right, of 136 | 98% | 60% |
| M-04 | Disclosures or unauthorized actions, of 608 | 0 | 0 |
| M-04 | Materially incorrect outcomes | 5 | 49 |
| M-05 | Median turn latency | 4.1 s | |
| M-05 | Model cost per resolution | 1.3 cents | 0 |

Ended without a transfer counts a case that stayed in the chat whether or not it was solved, so it is read beside safe resolution, never alone: the baseline keeps more customers in the chat and resolves fewer of their requests. The three runs agree within half a point. Every number is an offline measurement on our own cases, never a production figure (EVL-13).

> [!NOTE]
> **Two things the table doesn't show.** The ceiling for safe resolution is 77%, not 100%: 136 of the 602 cases are handoffs the policy requires, and a case handed off counts as zero by definition. And 56 of the 80 failing cases share one misread, fixed in the agent's code after the freeze; on the patched build, 55 of those 56 pass, which would put safe resolution near 75%. The table reports the frozen code as measured. We didn't re-run the set after the fix, because those cases are the ones that prompted it ([what failed and why](docs/evaluation/report.md#what-failed-and-why)).

- [report.md](docs/evaluation/report.md): the results in plain words, with the failures and the ROI.
- [results.md](docs/evaluation/results.md): every number, by run, language, segment, and rule.
- [limitations.md](docs/evaluation/limitations.md): what the evaluation leaves out, and the risks we accepted.
- [families.md](docs/evaluation/families.md), [disagreements.md](docs/evaluation/disagreements.md), [runs.md](docs/evaluation/runs.md): the requests, the disagreements, the runs.

The design is in [ADR-0005](docs/adr/0005-offline-scenario-evaluation.md).

---

## Limitations

The bank is synthetic, and it shows: contacts can't be tied to a workflow, no supplied label carries signal, about half of all active cards are past their expiration date and still transact, and about 5% of each core field is missing at random. The workflow was chosen for being feasible on this data, not for being in demand ([ADR-0003](docs/adr/0003-choose-workflow-from-evidence.md)). No customer in the data writes Portuguese, so Portuguese rests on messages we wrote, and results are reported per language.

Faro serves what this bank's data supports, and no more: recent transactions are a card's last 90 days, ten at a time, with no filter ([product brief](docs/product/brief.md#out-of-scope)). Three pieces are designed and not built: a handoff filed after a confirmation's deadline when the customer has left, claim and resolve in the console, and the AI team's page. The tools mock the bank's systems of record over a frozen snapshot ([ADR-0004](docs/adr/0004-agent-architecture-on-agentcore.md#the-tools-as-the-seam-to-the-banks-systems)). The full account, with the remaining deployment work and risks, is in [limitations.md](docs/evaluation/limitations.md).

Since late October 2026, the organizers' dataset can no longer be downloaded, and it isn't ours to share. Our results stand as recorded: every run names the exact data and code it used. But without a copy of the dataset, a fork can rerun only the checks that use our own test data, such as `make regression` ([step 2](CONTRIBUTING.md#2-restore-the-dataset)).

---

## Self-hosting

A fork stands up in an AWS account you control, with Terraform and nothing tied to our accounts:

| You want to | You need |
|---|---|
| Change code, tests, or docs | Only the toolchain |
| Explore or process the organizers' dataset | A copy of it, since it [can no longer be downloaded](CONTRIBUTING.md#2-restore-the-dataset) |
| Run the whole stack in your own AWS account | Admin access to an AWS account, and your own fork |
| [Check our reported numbers](CONTRIBUTING.md#reproduce-the-results) | The toolchain to start; a copy of the dataset and your own stack for the later checks |

With the [toolchain](CONTRIBUTING.md#1-install-the-toolchain) installed:

```bash
make install   # Python and web deps, pre-commit hooks, tflint plugins
make check     # Every hook against every file, as CI runs them
```

> [!TIP]
> [CONTRIBUTING.md](CONTRIBUTING.md) takes it from there: the dataset snapshot, the pipeline, the deploy roles, the `local` and `prototype` environments, reproducing our results, and teardown.

---

## Contributing

Feature branches merge into `develop`, the default branch, which runs the quality gates and deploys nothing. `main` mirrors what is live on the prototype and only accepts merges from `develop`. Commit subjects are imperative, PRs cite the requirement IDs they cover, and a decision someone could question later gets an architecture decision record. The [contributing guide](CONTRIBUTING.md#day-to-day-workflow) has the details, and `make help` lists every command.

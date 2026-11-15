# Hackathon requirements

What the organizers evaluate, distilled from the problem statement (`PS p.N`, by page) and the kickoff slides (`SL N`, by slide), both under `docs/hackathon/` and gitignored. Every requirement carries a stable ID, and the docs, the tests, and the evaluation reports cite the ID instead of paraphrasing the source.

> [!NOTE]
> **The submission's record.** The hackathon is over, and Faro won first place. This page stays as we wrote it during the build, present tense included, because the docs, the tests, and the reports still cite its IDs.

## Contents

- [Key dates](#key-dates)
- [Submission deliverables](#submission-deliverables)
- [How we are scored](#how-we-are-scored)
- [Requirement catalogue](#requirement-catalogue)
- [Metric definitions](#metric-definitions)
- [Explicitly not required](#explicitly-not-required)
- [Reading between the lines](#reading-between-the-lines)

---

## Key dates

| Date | Milestone |
|---|---|
| 2026-09-25 | Challenge launch; the 10-day build begins |
| 2026-10-05 | Submissions close |
| 2026-10-15 | Finalists announced |
| 2026-10-16 | Award ceremony |

---

## Submission deliverables

All four are required for the submission to count (`SL 18`), sent to `hackathon.admin@factored.ai`: "submit your tool no matter what".

| ID | Deliverable |
|---|---|
| SUB-01 | Public GitHub repository named `factored-hackathon-2026-[team name]` |
| SUB-02 | Link to where the tool is deployed |
| SUB-03 | A 4 to 6 slide presentation with details on the tool |
| SUB-04 | A short, **mandatory** video pitch that demonstrates the working solution and explains the core architectural decisions |

---

## How we are scored

"First and foremost, [your] solution should work" (`SL 20`). Judges score five areas:

| Area | What the judges look at | Main requirement groups |
|---|---|---|
| Rationale and documentation | Overall project rationale and documentation | PRB, DSN, OPS |
| AI Engineering | Backend, frontend, and deployment | AI, CTL, SEC, OPS |
| Data Analytics | Data quality and relevant insights from the solution | PRB, EVL |
| Data Engineering | How we handle extraction and transformation of the data | DML-01 to DML-06 |
| Machine Learning | Model selection, optimization, implementation, and tracking | DML-07 to DML-12, EVL |

- Depth, demonstrated behavior, and engineering judgment determine the score; more workflows earn no bonus (`PS p.3`).
- Every team is assessed on data engineering and AI/ML rigor, whatever its architecture (`PS p.4`).
- No discipline is mandatory, but each has suggested tasks (`SL 14`): AI (production backend, structured JSON handoffs); ML (LLM/RAG orchestration, prompt injection defense); Data Engineering (ETL/ELT pipeline, customer record isolation); Data Analysis (demand patterns, cost-per-resolution ROI).
- The takeaway (`SL 15`): build something that works, prove that it works, know when it should not act, and show what it would take to make it real.

---

## Requirement catalogue

The `Check` column says where each item is verified:

| Tag | Where |
|---|---|
| `test` | An automated test against the code or the running service |
| `eval` | The output of the evaluation harness |
| `doc` | The documentation |
| `demo` | The deployed tool or the video |

### Scope (SCP)

| ID | Requirement | Check | Source |
|---|---|---|---|
| SCP-01 | Pick one focused, coherent workflow (e.g. account or payment inquiries, card-service support, transaction-dispute intake, credit-product information and eligibility). These are examples, not tracks | `doc` | PS p.2, SL 10 |
| SCP-02 | Deliver an end-to-end working prototype that handles complex interactions beyond a surface-level demo: "don't build a chatbot, build a customer-service system" | `demo` | PS p.2, SL 10 |
| SCP-03 | Demonstrate a normal path: policy-compliant automated resolution, verified account queries, authorized self-service | `demo` `eval` | PS p.3, SL 11 |
| SCP-04 | Demonstrate an ambiguous or unsupported request: clarifying questions or safe abstention for missing parameters or unsupported requests | `demo` `eval` | PS p.3, SL 11 |
| SCP-05 | Demonstrate a case requiring human intervention: structured handoff (see CTL-05) | `demo` `eval` | PS p.3, SL 11 |
| SCP-06 | Demonstrate interactions in **Spanish and Portuguese** | `demo` `eval` | PS p.3, SL 10 |
| SCP-07 | Report limitations in the supplied data and in language coverage | `doc` | PS p.3, SL 15 |
| SCP-08 | Provide evidence of production readiness and an honest account of the work required before deployment (not expected to run a live banking service) | `doc` | PS p.2 |

### 1. A problem supported by data (PRB)

| ID | Requirement | Check | Source |
|---|---|---|---|
| PRB-01 | Analyze contact reasons | `doc` | PS p.3 |
| PRB-02 | Analyze relevant demand patterns | `doc` | PS p.3, SL 14 |
| PRB-03 | Analyze data quality | `doc` | PS p.3, SL 20 |
| PRB-04 | Analyze operational constraints | `doc` | PS p.3 |
| PRB-05 | Use that evidence to prioritize the chosen workflow, from reproducible analysis ("justify workflow selection using reproducible logs") | `doc` `test` | PS p.3, SL 13 |
| PRB-06 | Define the intended customer and business outcomes | `doc` | PS p.3 |
| PRB-07 | Use the supplied data to establish a baseline | `eval` | PS p.2 |

### 2. A functioning AI system (AI)

The system should Understand, Decide, Act, Verify, and Escalate (`SL 11`).

| ID | Requirement | Check | Source |
|---|---|---|---|
| AI-01 | Maintain relevant conversational context | `test` `eval` | PS p.3, SL 11 |
| AI-02 | Clarify ambiguous requests | `test` `eval` | PS p.3, SL 11 |
| AI-03 | Ground factual responses in permitted account, transaction, or policy information (trusted retrieval) | `test` `eval` | PS p.3, SL 11, SL 13 |
| AI-04 | Use tools when they serve the workflow, and use them securely | `test` | PS p.3, SL 11 |
| AI-05 | Report only actions whose outcomes the system has verified | `test` `eval` | PS p.3, SL 11 |
| AI-06 | Execute the appropriate service workflow | `test` `demo` | PS p.2, SL 11 |

### 3. Controlled automation (CTL)

Key idea: "AI should not be autonomous just because it can be" (`SL 11`).

| ID | Requirement | Check | Source |
|---|---|---|---|
| CTL-01 | Define which requests the system can answer | `doc` `test` | PS p.3 |
| CTL-02 | Define which actions require customer confirmation, and enforce it | `doc` `test` | PS p.3 |
| CTL-03 | Define when the system must abstain or transfer to a human ("know when NOT to act") | `doc` `test` | PS p.3, SL 11 |
| CTL-04 | Enforce permissions and policy outside model-generated prose, in code, not in the prompt | `test` | PS p.3, SL 13 |
| CTL-05 | Hand the human agent a structured payload (JSON) with: the request, verified facts, actions taken, supporting evidence, and unresolved questions; no raw transcript dump | `test` | PS p.3, SL 11, SL 14 |

### Data and execution boundaries (SEC)

| ID | Requirement | Check | Source |
|---|---|---|---|
| SEC-01 | Use only organizer-approved data and permitted external resources | `doc` | PS p.5 |
| SEC-02 | Label every input as real, de-identified, synthetic, or team-generated, and follow the published data-use terms | `doc` | PS p.5 |
| SEC-03 | Never include private customer records, credentials, or restricted data in the public submission **or in external model requests** | `test` `doc` | PS p.5 |
| SEC-04 | Authenticate with a trusted test session or identity service; a national ID or customer number alone does not prove identity | `test` | PS p.5, SL 15 |
| SEC-05 | Enforce access to each customer's records and action permissions in the service or tool layer (customer record isolation) | `test` | PS p.5, SL 14 |
| SEC-06 | Sandbox services and mock banking tools are allowed only with documented contracts and limitations | `doc` | PS p.5 |
| SEC-07 | No live lending decisions and no movement of money | `test` | PS p.5 |

### Credit workflows (CRD), only for a credit workflow

Not ours: [ADR-0003](adr/0003-choose-workflow-from-evidence.md) chose card support and set credit aside, with these in view.

| ID | Requirement | Check | Source |
|---|---|---|---|
| CRD-01 | Separate conversation handling, predictive risk estimates, and eligibility policy | `doc` `test` | PS p.5 |
| CRD-02 | Produce a simulated eligibility outcome from approved rules or a clearly labeled synthetic policy service | `test` | PS p.5 |
| CRD-03 | The conversational model must not invent eligibility rules or independently approve credit | `test` `eval` | PS p.5 |
| CRD-04 | Show explanations, uncertainty, and review paths for missing data or borderline cases | `demo` `test` | PS p.5 |

### 4. Sound data and ML practice (DML)

| ID | Requirement | Check | Source |
|---|---|---|---|
| DML-01 | Repeatable, deterministic data preparation (ETL/ELT) | `test` | PS p.3, SL 12, SL 13 |
| DML-02 | Data contracts with strict input schema enforcement | `test` | PS p.3, SL 12 |
| DML-03 | Data quality checks | `test` | PS p.3, SL 12 |
| DML-04 | Lineage | `doc` `test` | PS p.3 |
| DML-05 | An update and freshness policy; choose batch, incremental, or streaming by the supplied inputs and the workflow's latency and freshness needs, and justify it | `doc` | PS p.3, PS p.4 |
| DML-06 | If only static data is supplied, demonstrate update correctness with a clearly labeled test fixture | `test` | PS p.4 |
| DML-07 | Evaluate at least one learned component against an appropriate baseline | `eval` | PS p.3, SL 12 |
| DML-08 | Use valid labels or relevance judgments (grounded ground truth) | `doc` `eval` | PS p.3, SL 12 |
| DML-09 | Prevent leakage (strict train/eval isolation) | `test` | PS p.3, SL 12 |
| DML-10 | Justify representations, metrics, thresholds, and evaluation splits (realistic held-out distributions) | `doc` | PS p.3, SL 12 |
| DML-11 | For pretrained or retrieval-based components: justify component selection, intent or relevance labels, representations, held-out evaluation, and error analysis | `doc` `eval` | PS p.4 |
| DML-12 | Model selection, optimization, and tracking (experiment and version tracking) | `doc` | SL 20 |

### 5. Measured quality and failure handling (EVL)

| ID | Requirement | Check | Source |
|---|---|---|---|
| EVL-01 | Compare baseline and proposed system on the **same held-out workload** | `eval` | PS p.3, PS p.5, SL 12 |
| EVL-02 | Held-out cases include incorrect or missing data | `eval` | PS p.3 |
| EVL-03 | Held-out cases include expired sessions | `eval` | PS p.3 |
| EVL-04 | Held-out cases include unauthorized access attempts | `eval` | PS p.3 |
| EVL-05 | Held-out cases include prompt injection | `eval` | PS p.4, SL 13 |
| EVL-06 | Held-out cases include tool failures | `eval` | PS p.4 |
| EVL-07 | Held-out cases include multilingual ambiguity | `eval` | PS p.4 |
| EVL-08 | Report the number and mix of cases, label quality, model and prompt versions, and repeated-run variability | `eval` | PS p.5 |
| EVL-09 | Include failures in the results | `eval` | PS p.5 |
| EVL-10 | If a model judges answers, document its rubric and validate a sample against human or deterministic judgments | `doc` `eval` | PS p.5 |
| EVL-11 | Report metrics M-01 to M-05 with sample sizes and limitations | `eval` | PS p.4, PS p.6 |
| EVL-12 | Compare outcomes by language and by authorized customer segment, state small-sample limitations, and investigate disparities | `eval` | PS p.6 |
| EVL-13 | Label offline measurements, simulations, and projected business savings separately; never describe an offline comparison as a measured production improvement | `doc` | PS p.6 |
| EVL-14 | Cost-per-resolution ROI analysis | `doc` | SL 14 |

### 6. A credible route to operation (OPS)

| ID | Requirement | Check | Source |
|---|---|---|---|
| OPS-01 | Tracing | `test` | PS p.4, SL 15 |
| OPS-02 | Execution records (audit logs); explanations come from sources, policy rules, and execution records, never from hidden chain-of-thought | `test` | PS p.4, SL 13, SL 15 |
| OPS-03 | Monitoring | `doc` | PS p.4, SL 15 |
| OPS-04 | Bounded retries | `test` | PS p.4, SL 15 |
| OPS-05 | Safe fallback | `test` | PS p.4, SL 15 |
| OPS-06 | Tool failure handling | `test` | SL 15 |
| OPS-07 | Reproducible setup: setup instructions, versioning, repeatable evaluation | `doc` `test` | PS p.4, SL 15 |
| OPS-08 | Explain capacity limits | `doc` | PS p.4, SL 15 |
| OPS-09 | Explain access controls | `doc` | PS p.4, SL 15 |
| OPS-10 | Explain data retention | `doc` | PS p.4, SL 15 |
| OPS-11 | Explain remaining deployment work and remaining risks | `doc` | PS p.4, SL 15 |
| OPS-12 | Deployed backend and frontend, reachable from the submitted link | `demo` | SL 18, SL 20 |

### Design justification (DSN)

| ID | Requirement | Check | Source |
|---|---|---|---|
| DSN-01 | Design for privacy, explainability, fairness, reliability, and scalability | `doc` | PS p.2 |
| DSN-02 | Make explicit trade-offs across autonomy, accuracy, latency, cost, and human oversight | `doc` | PS p.2 |
| DSN-03 | Justify where AI is appropriate and where deterministic logic is preferable | `doc` | PS p.2 |
| DSN-04 | Explain how the system is evaluated for quality and safety | `doc` | PS p.2 |
| DSN-05 | Justify the chosen combination of conventional ML, pretrained models, retrieval, deterministic workflows, or agents | `doc` | PS p.4 |

---

## Metric definitions

Quoted closely from `PS p.6` and computed as stated. The slides single out M-01, M-04, and cost efficiency (M-05) as the key metrics (`SL 12`).

| ID | Metric | Definition | Reporting rules |
|---|---|---|---|
| M-01 | Safe automated resolution | An eligible case reaches the correct, policy-compliant outcome without human intervention | Rate over **all in-scope test cases**, plus the share of cases on which automation was attempted |
| M-02 | Containment | A case ends without transfer | Never report alone; containment does not show the problem was solved |
| M-03 | Escalation quality | Cases that require escalation are transferred correctly and include useful handoff context | Report both missed transfers and unnecessary transfers where reference labels permit |
| M-04 | Unsafe outcomes | Unauthorized disclosures or actions, or materially incorrect outcomes | Counts **and** denominators; zero observed failures in a small test set does not establish zero risk |
| M-05 | Operating efficiency | End-to-end p50 and p95 latency; cost per attempted case; cost per successful automated resolution | State workload, sample size, and cost assumptions; write "not defined" when there are no successful resolutions |

All metrics are reported per language and per authorized customer segment as well as overall (EVL-12).

---

## Explicitly not required

These earn no points by themselves (`PS p.3`, `PS p.4`):

- Training a new model (a training pipeline is one way, not the only way, to show ML rigor)
- Multiple agents, or reaching a tool-count target
- Streaming (incremental file delivery does not by itself require it)
- Demand forecasting
- A dashboard
- More than one workflow
- Operating a live banking service, live lending decisions, or moving money

Any language and tooling is allowed; Azure, Snowflake, AWS, and Databricks are suggested resources (`SL 19`).

---

## Reading between the lines

Our interpretation, not organizer text.

- **Honesty is graded.** The documents keep asking for limitations, failures, "not defined", denominators, and offline claims kept apart from production ones. Overclaiming likely costs more than a modest, well-measured result.
- **Judges will attack the deployed tool.** Expect prompt injection, impersonation with only a customer number, reads of another customer's records, and reuse of an expired session. SEC-04, SEC-05, and CTL-04 must hold at the tool layer with the model fully compromised.
- **The handoff payload is a first-class artifact.** It appears in both documents and in the suggested AI tasks, so it gets a schema, tests, and a place in the demo.
- **A frontend is scored.** A dashboard is optional, but "Backend, Frontend and Deployment" means the deployed tool needs a usable interface.
- **The video carries the demo.** It shows the three paths (SCP-03 to SCP-05) in both languages (SCP-06) and the core architectural decisions (SUB-04).

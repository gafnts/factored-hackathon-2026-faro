# Architecture

Faro on one page: what runs where, how a turn flows, what the graph decides, where each rule is enforced, how the data reaches the tools, and how we measure the result. Each section names the record that holds the full reasoning. The requirement IDs are from [prerequisites.md](prerequisites.md), and the terms are in the [glossary](glossary.md).

> [!NOTE]
> **As built.** This page describes the deployed prototype. The evaluation's numbers were measured on app stamp `8aa77fa`; the prototype runs the code after the one post-freeze fix the [report](evaluation/report.md#what-failed-and-why) describes. Three pieces are designed and not built, each a stated limitation: a handoff filed after a confirmation's deadline when the customer has left ([ADR-0004](adr/0004-agent-architecture-on-agentcore.md#the-confirmation), decision 7); claim and resolve in the console, so every case stays `filed` ([ADR-0007](adr/0007-role-gated-web-app.md#the-console-api)); and the AI team's page, whose report lives in [docs/evaluation/](evaluation/report.md) instead.

## The system

One backend on Amazon Bedrock AgentCore. A Runtime serves the chat and runs an explicit LangGraph workflow. A Gateway exposes Lambda tools under Cedar policies. DynamoDB holds every store. Nothing the model writes reaches a tool's `customer_id`, a confirmation, or a handoff's facts ([ADR-0004](adr/0004-agent-architecture-on-agentcore.md)).

```mermaid
flowchart LR
  subgraph Browser["Browser (S3 and CloudFront)"]
    CHAT["/chat: the customer"]
    AGT["/cases: the human agent"]
  end
  COG["Cognito: customers' and staff app clients, pre-token trigger"]
  RT["AgentCore Runtime: entrypoint and graph"]
  GW["AgentCore Gateway: JWT authorizer and Cedar (ENFORCE)"]
  READS["Lambda: the four read tools"]
  BLOCK["Lambda: block_card"]
  FILE["Lambda: file_handoff"]
  API["Console API: API Gateway and two reading Lambdas, under /api"]
  DB[("DynamoDB: tools' data, sandbox overlay, confirmations, session bindings, checkpoints, execution records, handoff cases, usage counters")]
  LLM["Claude Haiku 4.5 (Anthropic API, key in Secrets Manager)"]
  PIPE["Pipeline: dbt-duckdb, stamped gold export"]
  ALM["CloudWatch: metric filters, four alarms, SNS"]
  COG -.->|"tokens"| Browser
  CHAT -->|"AG-UI, customer token"| RT
  AGT -->|"polls, staff token"| API
  RT -->|"the customer's own token"| GW
  GW --> READS
  GW --> BLOCK
  RT -->|"IAM, off the Gateway"| FILE
  RT -->|"route, extract, write"| LLM
  RT -.->|"JSON log lines"| ALM
  READS --> DB
  BLOCK --> DB
  FILE --> DB
  RT --> DB
  API --> DB
  PIPE -->|"imported by Terraform"| DB
```

Two layers decide access and actions, and neither trusts the one above it (CTL-04, SEC-05). Cedar checks that every tool call names the customer and the sign-in in the token; each tool checks the rest: the card's status, the confirmation, the window. So the graph can route wrongly without a customer seeing another's records, and the model can answer wrongly without a card being blocked unconfirmed. `file_handoff` stays off the Gateway, since a customer's own token could otherwise file a forged case: the Runtime invokes it with a role no customer holds, and the Lambda checks the token with Cognito and the payload against the execution record before filing.

The site is one static app with a route per role. Customers sign in through one app client and staff through another, and a pre-token trigger refuses a token when the client doesn't match the user's group. The console API, under `/api` on the site's own origin, accepts the staff client only and reads cases and the tool calls their evidence names; no console route changes a card ([ADR-0007](adr/0007-role-gated-web-app.md)).

## A turn

```mermaid
sequenceDiagram
  participant C as Chat
  participant A as Runtime authorizer
  participant E as Entrypoint
  participant G as Graph
  participant W as Gateway and Cedar
  participant T as Tool Lambda
  participant R as Execution record
  C->>A: message, access token, thread ID, runtime session ID
  A-->>C: 401 RUN_ERROR when the token is missing, expired, or not a customer's (SEC-04)
  A->>E: verified claims (sub, customer_id, origin_jti)
  E->>E: bind the runtime session to sub, derive the thread key from sub, count the turn against the limits, mask card numbers, turn text sent during a control into a resume
  E-->>C: RUN_ERROR when the sign-in is past 10 turns a minute or the user past 500 a day
  E->>R: turn opened
  E->>G: run
  G->>W: tool call with the token's customer_id and origin_jti
  W-->>G: denied (-32002) when the call names another customer or sign-in
  W->>T: validated call
  T-->>G: result from the customer's partition and the sign-in's overlay
  G->>R: model calls, tool calls, interrupts, a decision per request served
  G-->>C: reply sent whole, or a confirm or handoff control
```

The chat opens the runtime session at sign-in with a warm-up the entrypoint answers without running the graph, so the first message doesn't pay the cold start. Each turn has 60 seconds; a model or tool call that fails is tried up to three times, and one that still fails ends the turn with a fixed reply and a person offered (POL-48).

The execution record is the trace, the audit log, and the evaluation's evidence in one (OPS-01, OPS-02): every model call with its prompt version, tokens, latency, and cost; every tool call with each attempt; every interrupt and resume; and a decision entry per request served, naming the outcome class and what the agent waits for next. It is append-only, and explanations come from it, never from hidden reasoning.

## The graph

A workflow with model steps, not an agent loop (DSN-03, DSN-05). Code chooses every node and tool. The model does five things: it labels a message among eight requests and says whether it holds one at all; it extracts what a card request names, among allowed values; it says which listed transactions fit what the customer said; it writes the words of a read's answer around placeholders; and it writes a handoff's three free-text fields. The calls that read a message also say which language it is mostly in, and code applies POL-50 and POL-51 to that reading. The policy's rules are the router's labels and the nodes' branches ([the policy](policy/card-support.md), CTL-01).

```mermaid
flowchart LR
  MSG(["A message"])
  ROUTE["Route: the requests it holds, in order"]
  CARD["Which card or charge, and what the request names"]
  subgraph R["No. 1 RESOLVE"]
    READ["Read: status, credit, transactions, a decline"]
    CONFIRM["Confirm control: the card and the reason"]
    BLOCK["Block, then read the card back; unverified, it is handed off"]
  end
  subgraph A["No. 2 ASK OR DECLINE"]
    ASK["Ask: which card, which charge, why block"]
    DECLINE["Decline what card support doesn't serve"]
  end
  subgraph H["No. 3 HAND OFF"]
    HANDOFF["A case with the evidence, for a person"]
  end
  REPLY["Reply: the model's words, code's facts, checked"]
  OUT(["Sent whole"])
  MSG --> ROUTE
  ROUTE -->|"a card request"| CARD
  CARD --> READ
  CARD -->|"a block, with its reason"| CONFIRM
  CONFIRM -->|"only the control confirms"| BLOCK
  CARD -->|"unclear"| ASK
  ROUTE -->|"outside card support"| DECLINE
  CARD -->|"someone else's, missing data, a conflict"| HANDOFF
  READ --> REPLY
  BLOCK --> REPLY
  ASK --> REPLY
  DECLINE --> REPLY
  HANDOFF --> REPLY
  REPLY --> OUT
  classDef model stroke:#8b5cf6,stroke-width:3px
  class ROUTE,CARD,REPLY,HANDOFF model
```

The model takes part only in the steps outlined in purple. Node by node:

```mermaid
flowchart TD
  IN(["message or resume"])
  BEGIN["begin: a pending control"]
  ROUTE["route: the labels, in POL-05's order"]
  LIST["list_cards: is the customer served in full"]
  UNSUP["unsupported: decline, or hand off an unblock"]
  CARD["resolve_card: which card, and what the request names"]
  READ["read: status, credit, transactions, a decline"]
  FIND["find_transaction: which listed one"]
  ASIDE["aside: a message that doesn't answer the question, read as a new one"]
  REASON["ask_reason"]
  CONFIRM["confirm: create the record, show the control"]
  CONCLUDE["conclude: the request's reply part and decision"]
  NEXT["next_request: the queue"]
  REPLY["reply: fill placeholders, check, send whole"]
  AWAIT["await_control: interrupt"]
  BLOCK["block: use the confirmation"]
  VERIFY["verify: read the card back"]
  HANDOFF["handoff: build, validate, file"]
  OUT(["events to the chat"])
  IN --> BEGIN
  BEGIN -->|"new message"| ROUTE
  BEGIN -->|"answer to a question"| CARD
  BEGIN -->|"answer to a question"| FIND
  ROUTE -->|"a card request"| CARD
  ROUTE -->|"unsupported, talk_to_human"| LIST
  ROUTE -->|"no request, third language"| CONCLUDE
  LIST -->|"not active, talk_to_human"| HANDOFF
  LIST -->|"served in full"| UNSUP
  UNSUP --> HANDOFF
  UNSUP --> CONCLUDE
  CARD --> READ
  CARD -->|"unrecognized_charge, decline_reason"| FIND
  CARD -->|"block_card, no reason"| REASON
  CARD -->|"block_card, with a reason"| CONFIRM
  CARD -->|"ambiguous, someone else's, not active"| HANDOFF
  CARD -->|"which card?"| CONCLUDE
  READ --> CONCLUDE
  READ -->|"missing data, conflict, failure"| HANDOFF
  FIND -->|"active card"| CONFIRM
  FIND --> HANDOFF
  FIND --> CONCLUDE
  CARD -->|"no answer to its question"| ASIDE
  FIND -->|"no answer to its question"| ASIDE
  ASIDE -->|"no request: a short reply, the question again"| CONCLUDE
  ASIDE -->|"a new request"| CARD
  ASIDE --> LIST
  ASIDE -->|"after a charge's question: the dispute handoff, then the new request"| HANDOFF
  REASON --> CONCLUDE
  CONFIRM --> CONCLUDE
  CONCLUDE -->|"another request queued"| NEXT
  NEXT --> CARD
  NEXT --> LIST
  CONCLUDE --> REPLY
  REPLY -->|"a control is pending"| AWAIT
  REPLY --> OUT
  AWAIT -->|"confirmed"| BLOCK
  AWAIT -->|"accepted"| HANDOFF
  AWAIT -->|"cancelled, declined, or a new request"| CARD
  AWAIT -->|"typed text: hold, then the control again"| AWAIT
  BLOCK --> VERIFY
  VERIFY -->|"verified"| CONCLUDE
  VERIFY -->|"not verified, or POL-39"| HANDOFF
  HANDOFF --> CONCLUDE
```

One message can hold several requests. Code puts them in POL-05's order, serves the first, and keeps the rest: when a request ends on a question or a control, a fixed sentence names what is left, and the turn that answers it serves the rest. Each request served writes its own decision entry.

Three rules make a reply safe to send (AI-03, AI-05, CTL-02):

- **Typed text never confirms.** `confirm` creates a confirmation record and shows a control; only the control's resume, naming that record, confirms or cancels. `block_card` consumes the record in one DynamoDB transaction and reads the card back up to three times. The customer hears "blocked" only when the read-back shows it; otherwise the case is handed off as `action_not_verified`. An offered handoff is filed the same way, only when its control accepts it.
- **Facts as placeholders.** The model writes `{card.expiration}`, never the value; code fills it in the conversation's language and the customer's number format. Outcomes (blocked, verified, a handoff's reference) are fixed sentences chosen by code. The model writes the answer of the four reads only; every question, decline, abstention, refusal, and offer is fixed text.
- **The reply check.** Before a reply leaves, code checks every placeholder, digit, internal flag, and withheld status. A reply that fails is replaced by a fixed one built from the same facts, and the fallback is counted (OPS-05).

The handoff is a structured case, not a transcript (CTL-05): the request, each verified fact tied to the tool call that read it, the actions taken, the customer's own statements kept apart, and the unresolved questions. The graph validates it against [handoff.schema.json](../src/faro/policy/handoff.schema.json); `file_handoff` validates it again, checks each fact against the execution record, adds the `is_fraud` and customer status facts the model never sees, raises the priority to `urgent` where POL-47 requires it, and files it with a reference the customer hears. The console shows the case within a 3-second poll.

## Where each rule holds

| Layer | Decides | Holds even if |
|---|---|---|
| Cognito and the pre-token trigger | Who the customer is; customers get tokens from the customers' app client only, staff from the staff client only | The browser is not ours |
| The Runtime's and the Gateway's JWT authorizers | Only a customer's valid token reaches the agent or a tool | The model is fully compromised |
| The entrypoint | The runtime session belongs to the caller; the thread key derives from the verified `sub`; card numbers are masked before anything reads them; a sign-in's turns are limited | The client sends another customer's IDs |
| Cedar | Every call names the token's `customer_id` and `origin_jti` | The graph routes wrongly |
| The tools | A read returns the customer's partition and never `is_fraud`; a block needs a confirmed, unexpired confirmation for that card and reason; a handoff's facts must be in the execution record | The graph is wrong |
| The graph | Which card, when to ask, when to abstain, decline, or hand off; the queue, the retries, and the question limit | The model mislabels or misextracts |
| The prompt | Nothing that access or an action depends on | |

The full map, one row per policy rule, is in [ADR-0004](adr/0004-agent-architecture-on-agentcore.md#where-each-rule-is-enforced).

## The data

A batch rebuild per pinned snapshot, on the machine that holds it; only the stamped gold export leaves it ([ADR-0002](adr/0002-mirror-dataset-into-pinned-snapshots.md), [ADR-0006](adr/0006-batch-medallion-pipeline.md); DML-01 to DML-06).

```mermaid
flowchart LR
  SNAP[("Pinned snapshot: 13 CSV tables, dataset.lock")]
  BR["Bronze: every row, typed by contract, nothing dropped"]
  SI["Silver: card support at the as-of instant, flagged, split by customer"]
  GO["Gold: what the tools read, last four digits only, 90-day window"]
  EXP[("Gold export, stamped with the snapshot and pipeline version")]
  TD[("Tools' data table")]
  OR["Evaluation oracle and generator"]
  SNAP --> BR --> SI --> GO -->|"make export"| EXP -->|"Terraform import"| TD
  BR --> OR
```

The state the tools read is a frozen master snapshot with an event cutoff: customers and cards as delivered, transactions dated at or before the as-of instant (business date 2026-06-17, read as of 2026-06-18 06:00). Rows updated after that instant are flagged, never corrected, and results are reported with and without them. `prototype` serves export `4a1bc9e15462b739` of snapshot `b3b8b248f604ef9a`; its manifest, with every check's result, is in [docs/pipeline/](pipeline/), and [the pipeline's README](../pipeline/README.md) says how to rebuild it.

Writes never touch the export. A block lands in a sandbox keyed by the sign-in, so each judge and each evaluation case starts clean. The sandbox also carries the evaluation's fixtures (a record the snapshot lacks) and fault plans (a tool made to fail), honored only in the sign-in that wrote them.

## How we know it works

Offline, on scripted conversations played against the deployed stack and graded by code against an oracle: our own code that applies the policy to the frozen snapshot and shares nothing with the tools ([ADR-0005](adr/0005-offline-scenario-evaluation.md); EVL-01 to EVL-14, DML-07 to DML-12). The [report](evaluation/report.md) reads the results and [results.md](evaluation/results.md) holds every number.

```mermaid
flowchart LR
  FAM["108 request families, both languages, a third held out"]
  GEN["Generator: cases from one side of the split"]
  CASES[("Case sets: manifests committed, cases kept out of git")]
  CUST["Scripted customer: reacts to decision entries and controls, never to prose"]
  SYS["Faro on the deployed stack, a test user per case"]
  PROC["In process: CI's regression set with scripted models, and the baseline"]
  REC[("Evidence: the stream, the execution record, the sandbox, the cases filed")]
  ORC["Oracle: the policy over bronze"]
  GRADE["Grader: code, from stored evidence"]
  JUDGE["Judge: wording only, where it agreed with hand grades"]
  OUT["Report, run index, disagreement log"]
  FAM --> GEN --> CASES --> CUST
  CUST --> SYS --> REC
  CUST --> PROC --> REC
  REC --> GRADE
  REC --> JUDGE --> OUT
  ORC --> GRADE --> OUT
```

- **The families** ([families.md](evaluation/families.md)): seeds we wrote and paraphrases a model wrote, checked for slots, digits, language, and closeness; team-generated (SEC-02).
- **The split** holds by customer, by family, and by time (DML-09), with tests that no development artifact holds a held-out customer or message.
- **Three sets**, with their manifests in [sets/](evaluation/sets/): a regression set of 71 cases that CI plays in process on a team-generated bank, a selection set of 157, and a held-out set of 608 drawn once at the freeze.
- **The harness** plays a case end to end against the deployed stack: a test user of its own, the chat's own client paced to the usage limits, fixtures and fault plans written under its sign-in, write-once evidence in the evaluation bucket. The unauthorized-access cases call the Gateway's tools directly and expect Cedar to deny them.
- **The grader** is a function of the case and its stored evidence, so a later version regrades without running the system. Where it and the oracle disagree, the question goes to the [disagreement log](evaluation/disagreements.md), and CI's regression gate lets a finding through only while an open entry matches it.
- **The baseline** is the same graph with a keyword router, pattern extraction, and templated replies, so no model runs; it played the same held-out cases once (EVL-01).
- **The judge**, Claude Opus 5.5, reads only wording, form of address, and clarity. It was validated against our blind hand grades on 136 replies before any held-out verdict was read, and is used on the one question of nine that met the bar (EVL-10).
- **The tests** ([tests/README.md](../tests/README.md)) check what must hold on every turn, apart from how well the agent does: each thing we expect a judge to try, from another customer's number to a typed yes, has an integration test against a deployed stack.
- **The result**, three runs of the frozen agent: 65% of 602 conversation cases resolved without a person, against the baseline's 43%; 133 of 136 required handoffs right; no disclosure and no unauthorized action in 608 cases, which bounds the rate at 0.6% without proving it zero (M-04); about 4 seconds a turn and 1.3 cents a resolution. The runs agree within half a point. One misread accounted for 56 of the 80 failing cases and was fixed in code after the freeze; the numbers stand as measured. Every reported run's manifest is indexed in [runs.md](evaluation/runs.md).

## Where the reasoning lives

| Record | Decides |
|---|---|
| [ADR-0003](adr/0003-choose-workflow-from-evidence.md) | Card support, from the [selection report](analysis/selection.md) |
| [The policy](policy/card-support.md) | What Faro answers, does, refuses, and hands off, one ID per rule |
| [ADR-0004](adr/0004-agent-architecture-on-agentcore.md) | The agent, the tools, the stores, the confirmation, the handoff, operations |
| [ADR-0005](adr/0005-offline-scenario-evaluation.md) | The evaluation, the oracle, the baseline, the judge, reporting |
| [ADR-0006](adr/0006-batch-medallion-pipeline.md) | The pipeline, its contracts and checks, freshness, lineage |
| [ADR-0007](adr/0007-role-gated-web-app.md) | The web app, sign-in, the console API, handoffs as cases |
| [docs/analysis/](analysis/) | The profile, the traffic, and the card support numbers the policy and the ADRs cite |
| [docs/evaluation/](evaluation/) | The [report](evaluation/report.md), the [results](evaluation/results.md), the [limitations](evaluation/limitations.md), the families, the disagreement log, the sets, and the run index |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | How to stand the stack up in another account and run every check |

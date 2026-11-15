# Product brief

Faro is the card support agent we built for LATAM Bank. This brief says what problem it takes on, who it serves, what it does and refuses to do, what sets it apart, and the outcomes we intend (PRB-06). The details are decided in the policy and the ADRs; this brief links to them.

> [!IMPORTANT]
> **A synthetic bank (SEC-02).** LATAM Bank is the regional bank the organizers' dataset simulates, in Mexico, Colombia, and Argentina; every customer, card, and transaction in it is synthetic. Every number here comes from that snapshot or from offline evaluation on our own cases; none is a production result (EVL-13).

## Contents

- [In one sentence](#in-one-sentence)
- [The problem](#the-problem)
- [Who it's for](#who-its-for)
- [What Faro does](#what-faro-does)
- [What Faro won't do](#what-faro-wont-do)
- [What sets it apart](#what-sets-it-apart)
- [Intended outcomes](#intended-outcomes)
- [Out of scope](#out-of-scope)
- [Where the decisions live](#where-the-decisions-live)

---

## In one sentence

> **Faro is LATAM Bank's card support agent, in Spanish and Portuguese. It explains what the records say, blocks a card only when its holder confirms, and hands off to a person with a structured case file.**

In Spanish, *faro* is a lighthouse; in Portuguese, *ter faro* is to have a nose for things. Guidance in one language, judgment in the other. The [identity guide](identity.md) says how Faro sounds and looks.

---

## The problem

A card is declined at the till, goes missing, or shows a charge its holder didn't make. The customer wants to know why and to stop the damage. The bank wants to act only on the right card, for the right person, when that person asks. Today a person on the phone does both, after a wait; an agent that acts on whatever the conversation says does it fast and unsafely. Faro is built to do both, in the customer's language.

What those moments cost at this bank, from the snapshot:

- **About 500 contacts a day**, flat across three years. 35% are `Transaccional`, the reason category nearest card support: 64,032 a year, 175 a day. One contact in ten already arrives as a chat ([traffic analysis](../analysis/traffic.md#6-contacts-by-channel-type-reason-and-country)).
- **A contact takes 5:22 on average, after a 2:00 wait.** A `Transaccional` contact takes 3:41 ([traffic analysis](../analysis/traffic.md#7-handle-and-wait-times)).
- **Declines are routine.** 62,242 card transactions (5.02%) are declined over the snapshot, each with a code the bank can explain ([card support analysis](../analysis/card-support.md#3-declines)).
- **Unrecognized charges become complaints.** 4,175 in the twelve months before the as-of instant (18.57% of complaints), which belong to dispute intake ([selection report](../analysis/selection.md#attributable-demand)).
- **"Which card?" comes first.** 13,420 of the 65,796 customers with an active card hold two or more active credit cards (POL-14).
- **The records are imperfect.** 47.69% of active cards are past their expiration date and still transact, and about 5% of each core field is missing. Faro reports what the record says, gaps and conflicts included (POL-30 to POL-32).

What the data can't show is how many contacts are about cards: contacts carry six coarse reasons and templated transcripts, and none can be tied to a workflow ([ADR-0003](../adr/0003-choose-workflow-from-evidence.md)). So the `Transaccional` figures are a bracket, and the saving in the [report's ROI](../evaluation/report.md#roi-a-projection) is a projection whose parameter is the cost of a human contact. Card support is the one workflow this data supports end to end. It is also the front door to all three moments above; a dispute is one handoff out of it.

---

## Who it's for

| Who | Their moment | What they get | Where |
|---|---|---|---|
| **The cardholder** | Their card was declined, is missing, or shows a charge they didn't make | An answer from their own records, in their language; a block they confirmed and Faro verified; a reference when a person takes over | `/chat` |
| **The human agent** | A case lands in their queue | A case file: the request, each verified fact next to the tool call that read it, the actions with their verified outcomes, the customer's own words kept apart, and the open questions. No transcript to read back | `/cases` |
| **The AI team** | Deciding whether Faro is safe to keep running | The evaluation report, failures and denominators included, labeled as an offline measurement | [docs/evaluation/](../evaluation/report.md) |

The cardholder has signed in to the bank's chat; Spanish is the bank's language and Portuguese the customer's choice in the session ([policy](../policy/card-support.md#language)). The human agent works in dispute intake or customer service and takes what Faro shouldn't do: disputes, unblocks, replacements, and anything a record can't settle. A case arrives urgent when the customer reported a card lost or stolen, or a charge they don't recognize, and that card isn't verified blocked (POL-47).

---

## What Faro does

The hackathon asked for a system that understands, decides, acts, verifies, and escalates (`SL 11`). Faro recognizes [eight kinds of request](../policy/card-support.md#requests), all about cards, and takes one action: a block, written to a sandbox over the frozen bank. Every request ends on one of three paths:

| Path | The customer says | Faro |
|---|---|---|
| **No. 1 Resolve** (SCP-03) | *¿Por qué me rechazaron la tarjeta ayer?* | Reads the card's transactions, finds yesterday's declined purchase, and gives its date, merchant, amount, and the meaning of its code: `51`, insufficient funds. It infers no cause and gives no advice (POL-02, POL-27, POL-29) |
| **No. 2 Ask or decline** (SCP-04) | *Quiero bloquear mi tarjeta*, from a customer with two active credit cards | Asks which, naming each by type and last four digits, then asks for a reason, then shows the confirm control (POL-14, POL-35, POL-36). *Quero aumentar meu limite* is declined with the reason, and a person is offered (POL-42) |
| **No. 3 Hand off** (SCP-05) | *Não reconheço uma compra no meu cartão* | Finds the charge and offers to block the card with the confirm control. Once the customer confirms, the tool reads the card back to verify the block, and Faro files the case to dispute intake and gives its reference (POL-37, POL-39, POL-45) |

---

## What Faro won't do

Knowing when not to act is half the product (CTL-03):

- **Act on typed text.** A block happens only through the confirm control, which names the card and the reason. A *sí* or *sim* typed in the chat never confirms one (POL-36).
- **Serve anyone but the signed-in customer.** A card number, customer ID, or name typed in the chat never changes whose cards Faro serves (POL-07, POL-08).
- **Undo what lowers risk.** Unblocking restores spending power and needs stronger proof of identity than a chat session gives, so it goes to a person (POL-41).
- **Judge a charge.** Faro doesn't open disputes, promise refunds, or say whether a charge is fraud (POL-39). The bank's `is_fraud` flag is never shown and decides nothing (POL-40).
- **Guess.** A missing field is reported as not recorded, and conflicting records are stated side by side, never reconciled (POL-30, POL-32).
- **Promise.** The reply to a handoff says a person will follow up and gives a reference; it promises no outcome or time (POL-45).
- **Move money.** Nothing Faro does moves money or changes the bank's records; its one write lands in the session's sandbox (POL-33, SEC-07).

---

## What sets it apart

1. **A lighthouse doesn't steer the ship.** Faro shows the customer what the records say and proposes the one thing it can do. The customer's button decides; the model never confirms anything on their behalf.
2. **Judgment in code, not in the prompt.** The model classifies, extracts, and writes. Code decides each step, and the tools and Cedar decide every access and action, so a fully compromised model still can't read another customer's card or block one unconfirmed ([ADR-0004](../adr/0004-agent-architecture-on-agentcore.md)). The evaluation tested this as a hypothesis that one counterexample would refute; none appeared in 608 held-out cases (M-04).
3. **Done means verified.** After a block, the tool reads the card back. Faro says the card is blocked only when that read shows it, and hands the case to a person when it doesn't (POL-37, AI-05).
4. **Candid about its records.** This bank's records have gaps and conflicts, and Faro says so: a missing field is not recorded, and two records that disagree are both stated (POL-30 to POL-32).
5. **Graded by an independent oracle.** Expected outcomes come from the policy applied to the frozen bank, in code written apart from the tools, so a bug in Faro's data shows up as a disagreement instead of agreeing with itself ([ADR-0005](../adr/0005-offline-scenario-evaluation.md#the-oracle)).

---

## Intended outcomes

The outcomes we intend for the cardholder and for the bank (PRB-06), each with the measure it is judged by: offline, on held-out cases, as [ADR-0005](../adr/0005-offline-scenario-evaluation.md) defines the metrics.

| For | We intend | Measured by |
|---|---|---|
| The cardholder | An answer from their own records, in their language, without waiting for a person | M-01, safe automated resolution, per language (EVL-12) |
| The cardholder | Exposure stopped when they ask: a block in the same conversation, verified | M-01 on blocks; M-04's unsafe actions |
| The cardholder | Not having to repeat themselves when a person takes over | M-03: the right queue and priority, and a payload that holds the expected facts |
| The bank | No unsafe outcome, as the condition for everything else | M-04, as counts with denominators and a rule-of-three bound |
| The bank | People's time spent on disputes and judgment calls, not lookups | M-01, with M-03's missed and unnecessary transfers |
| The bank | A resolved case that costs less than a contact handled by a person | M-05 and the cost-per-resolution ROI, labeled a projection (EVL-14) |

What we found, on 608 held-out cases played three times: 65% resolved without a person against the baseline's 43%, 98% of required handoffs right, no disclosure and no unauthorized action, about 4 seconds a turn and 1.3 cents a resolution in model calls. The [evaluation report](../evaluation/report.md) reads the numbers and their failures. We don't claim that cards drive most of the bank's contacts, which the data can't say; any saving as measured in production; or that Portuguese works as well as Spanish for customers, since the per-language results compare our Portuguese with our Spanish.

---

## Out of scope

- **Other workflows.** Faro hands a disputed charge to dispute intake with a payload and performs none of that workflow's steps (SCP-01, [ADR-0003](../adr/0003-choose-workflow-from-evidence.md)).
- **A statement.** Recent transactions are the card's transactions in the 90 days before the as-of instant, newest first, ten at a time, with no filter by period, merchant, or amount (POL-25): 66.2% of active cards have a transaction in that window and 99% of those have four or fewer, so one page almost always holds them all ([card support analysis](../analysis/card-support.md#2-activity)). A decline or an unrecognized charge is found by how the customer describes it (POL-27, POL-39). A real card with hundreds of transactions would need the targeted question and a link to the statement.
- **A person joining the chat.** Handoffs are asynchronous: a case in a queue, not a live transfer ([ADR-0007](../adr/0007-role-gated-web-app.md#in-a-bank-ops-11)).
- **The bank's real systems.** The tools read a frozen snapshot and write to a sandbox. What they stand in for, and what replacing them would take, is in [ADR-0004](../adr/0004-agent-architecture-on-agentcore.md#the-tools-as-the-seam-to-the-banks-systems).

---

## Where the decisions live

| Question | Where |
|---|---|
| Why card support | [ADR-0003](../adr/0003-choose-workflow-from-evidence.md) and the [selection report](../analysis/selection.md) |
| What Faro answers, does, and refuses | The [card support policy](../policy/card-support.md) |
| What runs where, and what enforces each rule | [ADR-0004](../adr/0004-agent-architecture-on-agentcore.md) |
| How we know it works | [ADR-0005](../adr/0005-offline-scenario-evaluation.md) and the [evaluation report](../evaluation/report.md) |
| How the data reaches the tools | [ADR-0006](../adr/0006-batch-medallion-pipeline.md) |
| The web app and the human agent's console | [ADR-0007](../adr/0007-role-gated-web-app.md) |
| How Faro sounds and looks | The [identity guide](identity.md) |

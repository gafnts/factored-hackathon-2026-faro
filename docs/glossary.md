# Glossary

The words this repository uses in its own way, in plain terms. The policy's rule IDs (`POL-NN`) are in [the policy](policy/card-support.md); the requirement IDs and the metrics `M-01` to `M-05` in [prerequisites.md](prerequisites.md).

## In the chat

| Term | Meaning |
|---|---|
| Control | A button shown in place of a typed answer. The confirm control names the card and the reason and is the only thing that confirms a block; the handoff control accepts or declines an offered handoff |
| Confirmation | The record a confirm control creates: one card, one reason, used up by one block, ended unused when the customer cancels, asks for something else, or lets it lapse |
| Read-back | After a block, the tool reads the card again; Faro says "blocked" only when that read shows it |
| Handoff, case | What a person receives when Faro stops: the request, each verified fact beside the tool call that read it, the actions taken, the customer's own words kept apart, and the open questions. No transcript. In the console at `/cases`, a filed handoff is a case |
| Sandbox | Where writes land, keyed by the sign-in. A block changes the sandbox, never the bank's data, so every sign-in starts clean |

## Under the hood

| Term | Meaning |
|---|---|
| Runtime, Gateway | Two AgentCore services: the Runtime runs the workflow and serves the chat; the Gateway exposes the banking tools to it under Cedar policies, which make every call name the customer and the sign-in in the token |
| Graph | The workflow, in code. Code chooses every step; the model only labels, extracts, and writes words |
| Router, extraction | The model calls that read a message: which requests it holds, and which card, reason, or transaction it names |
| Placeholder | A fact in a reply, such as `{card.expiration}`, that the model writes by name and code fills with the value. The model never writes a figure |
| Reply check | Code that inspects every reply before it is sent; a reply that fails is replaced by a fixed one, and the replacement is counted |
| Execution record | The append-only log of a conversation: every model call, tool call, and control, and a decision entry per request served with its outcome class (answered, clarified, abstained, declined, blocked, handed off). The audit log and the evaluation's evidence in one; each turn's first entry names its trace |
| Snapshot, as-of instant | A pinned, verified copy of the dataset, read as of one moment: business date 2026-06-17, as of 2026-06-18 06:00. No tool reads the wall clock |
| App stamp | The commit of the code the deployed Runtime runs, carried by every execution record |

## In the evaluation

| Term | Meaning |
|---|---|
| Family | One request we wrote, with its paraphrases in both languages; 108 in all ([families.md](evaluation/families.md)) |
| Held out | A fifth of the customers, and wording another model family wrote, never read while building; the split guards are the tests that keep it so. Until 2026-11-02 a third of our own families were held out instead |
| Case | One scripted conversation: a customer, a situation, and what the scripted customer sends, turn by turn |
| Oracle | Our own code that applies the policy to the frozen bank and computes each case's expected outcome; it shares nothing with Faro's tools |
| Grader | Code that compares a case's stored evidence with the oracle's expectation |
| Baseline | The same graph with a keyword router, pattern extraction, and templated replies, so no model runs |
| Judge | A model, Claude Opus 5.5, that graded wording in the submission's measurement, used only on the one question of nine where it agreed with our blind hand grades; dropped since, and code grades every reply ([ADR-0011](adr/0011-pre-registered-studies.md)) |
| Run | One play of a set, with a manifest naming its code, data, policy, prompt, and model versions ([runs.md](evaluation/runs.md)) |
| Disagreement, `D-NNN` | A case where Faro and the oracle differ, triaged in writing before anything changes ([disagreements.md](evaluation/disagreements.md)) |
| Freeze | The moment after which the agent's prompts stopped moving, so the held-out runs measure one configuration |

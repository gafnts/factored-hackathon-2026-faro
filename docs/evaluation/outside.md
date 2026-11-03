# The outside source

> [!IMPORTANT]
> **Model-generated (SEC-02).** Every message in this source is written by an OpenAI model from a guide we wrote. No customer wrote any of them, and the model reads no message of ours and nothing from the snapshot.

The outside source is the held-out side's wording: the requests and answers the scripted customer sends in a held-out set, written by another model family than ours or the agent's, from a guide to the policy rather than from our messages ([ADR-0011](../adr/0011-pre-registered-studies.md#wording-sources)). The first set drawn from it is `held_out_2` ([its study](studies/held-out-2.md)). Our own wording, all development, is [families.md](families.md).

Its text stays out of the repository until that set is played, so nobody reads it while the agent is tuned ([the seal](../adr/0011-pre-registered-studies.md#the-seal)). It's kept in `data/evaluation/sources/outside/` on the maintainer's machine and in the account's data bucket under `sources/outside/<hash>/`, which `make outside-keep` writes once, and the set's manifest records its hash. This page and the code that asks for it are committed.

## What we asked for

Two files in [`src/faro/evaluation/outside/`](../../src/faro/evaluation/outside/) say what the model writes, and neither holds a message of ours:

- **The guide** (`guide.md`), sent with every request: who writes (customers in Colombia, Mexico, Argentina, and Brazil), the policy's eight requests, what each field of a request means (the kinds of message, the extraction fields, the slots, a relative day, a complaint), what a message must never hold (anything the request doesn't give, a digit outside a slot, a name or an identifier, words that leave the language in doubt), and how a family's phrasings must differ.
- **The requests** (`requests.json`): 70 families and 11 kinds of answer. Each family request gives its labels, kind, extraction, slots, relative day, and complaint, and a sentence saying what its message says; each answer request gives the moment the customer answers and what they reply. The families are one per shape the generator's situations play (requests, kind, extraction, slots, day, and complaint), plus more where our families split one shape into several intents (a credit card's status and its expiration; four ways of holding no request; a loan and a savings account; English and French). The answers are the kinds the situations ask for.

A written family takes every field from its request and only its text from the model, so it fits the situations its request was written for: `borrowed` reads zero by construction, and a test checks that the requests word every situation in both languages. Another test checks that no line of the guide or the requests is close to, or quotes, a message of ours.

Each family has six phrasings per language, a first message and five more; each kind of answer has five per language ([the study's decision 1](studies/held-out-2.md#settled-before-registering)). Every family and answer names its author, the model as OpenAI's API returned it.

## Checks

The families' checks in `faro.evaluation.families.problems`, as ours pass them: exactly its request's slots, no digit, brace, or identifier outside a slot, the language it's filed under, no repeated message, a character 3-gram similarity under 0.6 to every development message, and under 0.6 between two phrasings of a family or two answers of a kind, 0.7 when either has four words or fewer. Then the label sample: 50 of its messages labeled blind against the policy's request list and scored with `relabel-agreement` (EVL-08), before any set draws from it. The sample is the study's one look, recorded in its Record.

## Limits

- **The guide is still ours.** What a label means, the shapes, and the slots come from us, so the cases measure wording we didn't write, on requests we defined.
- **A model's messages.** They are the model's idea of how customers write, not customers' (EVL-13).
- **One family per shape** for most shapes: a situation's cases all come from one family's six phrasings in each language.

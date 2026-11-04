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

## How it was written

On 2026-11-02 and 2026-11-03, by `make outside-write`: OpenAI's Responses API with the guide as the developer message, a strict JSON schema for the reply, reasoning at `medium`, and nothing stored on OpenAI's side. Four runs made 175 calls for 0.71 USD at list price, and the API named `gpt-6.1-sol` as the model in every reply. Every failed reply broke the same rule, the bar against our wording, mostly in short messages, whose obvious phrasings ours already use. After the first run, which left four families unwritten, the retry was changed from rewriting the whole group to keeping the phrasings that passed and naming the places to write anew; the last three runs wrote the four. The result is 70 families (828 messages: 408 in Spanish, 408 in Portuguese, 6 in English, 6 in French) and 55 answers, which `families.problems` finds nothing wrong with.

## Checks

The families' checks in `faro.evaluation.families.problems`, as ours pass them: exactly its request's slots, no digit, brace, or identifier outside a slot, the language it's filed under, no repeated message, a character 3-gram similarity under 0.6 to every development message, and under 0.6 between two phrasings of a family or two answers of a kind, 0.7 when either has four words or fewer. Then the label sample: 50 of its messages labeled blind against the policy's request list and scored with `relabel-agreement` (EVL-08), before any set draws from it. The sample is the study's one look, recorded in its Record: exact agreement 0.88 (44 of 50) and kappa on the first label 0.955; the six disagreements, settled by the policy's text, were each the guide's gap, not a wrong label, so 0 of 50 labels were wrong (at most 7.1% at the 95% Wilson bound) and no message was dropped ([held-out-2.md](studies/held-out-2.md#record)).

## Limits

- **Our intents, in another model's words.** What a label means, the shapes, and the slots come from us, and so does each request's sentence: we wrote each from the intent of one of our families, and a few are close to translations of our seeds. The check against our messages compares character trigrams, so it can't catch a translation. The cases measure wording we didn't write for the requests we defined, not requests we didn't think of.
- **Leaning away from our wording.** Only messages under the 0.6 bar against ours were kept, as ADR-0005 requires, and a retry asked for "less common ways to say it", so the set leans away from phrasing customers might share with ours. Both agents face the same cases, so the paired comparison is fair to each, but the rates on their own may read lower than on customers' wording.
- **A model's messages.** They are the model's idea of how customers write, not customers' (EVL-13).
- **One family per shape** for most shapes: a situation's cases all come from one family's six phrasings in each language.

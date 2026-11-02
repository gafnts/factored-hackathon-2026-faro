# ADR-0011: Pre-registered evaluation studies over wording sources

## Status

Proposed (2026-11-02). It amends [ADR-0005](0005-offline-scenario-evaluation.md): once this record is accepted, each section of ADR-0005 it changes is corrected as built when the code that builds it lands, and listed under ADR-0005's Status with a link here ([What changes in ADR-0005](#what-changes-in-adr-0005)).

One decision is still open. It is marked **Open** where it arises and listed under [Open decisions](#open-decisions) with the option we lean towards, which the rest of this record assumes; we settle it before accepting this record.

## Context

ADR-0005 designed the evaluation for one measurement, the submission's, and it records two things at once: a framework (cases, an oracle independent of the tools, a scripted customer that reacts to structured signals, grading from stored evidence, run manifests, a split by customer) and that measurement's choices (where the held-out wording came from, a model judge, the sizes, the budget). Its eighteen dated amendments in five days were mostly the second kind. Now a second measurement is due, and others will follow, so we need a framework that lets each measurement choose without amending the framework every time:

- **The held-out set is spent.** Its 608 cases were played three times at the freeze, the 56 that D-013 failed were played again for the patch's check, and we read the failures to write the report. A fix made now would be measured on the cases that prompted it.
- **The framework fixed where held-out wording comes from.** It held out a third of each label's families and of each kind of answer by MD5, and the first measurement showed what that costs. With three phrasings per kind of answer, one held-out phrasing per kind meant one misread reached every case of its kind: 56 in every run (D-013), and M-01 read 65% where it would have read near 75% without it ([report](../evaluation/report.md)). The hash doesn't look at a family's shape, so fifteen situations had no held-out family and 134 of the 608 cases borrowed development wording, and all three families that ask about another customer's card or ID fell on the held-out side, so development never played one. And one model family wrote both sides: we wrote the seeds and Claude Opus 5.5 the paraphrases, so the held-out side measured phrasing we didn't tune on, not phrasing we didn't write ([limitations](../evaluation/limitations.md#what-the-evaluation-cant-say)), which may also have favored the router comparison's Anthropic candidates (DML-11). ADR-0005 designed an outside set, written by another family from a guide to the labels, and dropped it for time.
- **The judge didn't pay its way.** One of its nine questions met the validation bar (EVL-10), and the other eight went unused; the hand grading ADR-0005 prescribes in their place, 3,820 held-out replies, wasn't done, and the 51 cases it flagged on the one question it kept were never read. It cost 12 to 13 USD a run, besides 136 replies graded by hand to validate it, and where it and our grader disagreed, one grader couldn't say which was right ([The judge](../evaluation/report.md#the-judge)).
- **Real messages are coming.** Once Faro opens, visitors will write to it: the first phrasing in this project that nobody wrote for an evaluation. Using it later depends on choices made before the first visitor writes, such as which conversations nobody reads.
- **One maintainer.** Faro has one maintainer now, so no step may rely on a second grader or a second labeler: where independence would need one, the look is recorded and the report says so.

## Decision

Separate the framework from the measurements. The framework stays in ADR-0005, as amended here. The wording a set uses comes from named sources, and the held-out side's from sources other than ours. Each reported measurement is a study, whose protocol is committed before its set is drawn. Code grades every reply, with no model judge.

### Three layers

| Layer | What it holds | Where it's recorded | What changes it |
|---|---|---|---|
| Framework | Cases, the oracle, the scripted customer, running a case, grading, manifests, the split, the seal | ADR-0005, as amended here | An ADR |
| Instruments | Wording sources and the grader's checks | Code, and a page per source, as [families.md](../evaluation/families.md) is ours | A PR, documented on its page; an ADR only for a new kind of data or of grader |
| Studies | One reported measurement: the systems played, the set, the analysis, the bar, the cost | A protocol under [docs/evaluation/studies/](../evaluation/studies/) | Nothing after registration, which is the point |

A new kind of data is one the framework doesn't yet handle, such as text written by strangers; a new kind of grader is one that isn't code, such as a model.

### Wording sources

A source is a body of messages with an author (us, a named model, visitors), a label under SEC-02, the side it serves, and the page that says how it was written. The scripted customer's opening messages and its answers both come from sources.

- **The split by family becomes a split by source** (DML-09). Development wording is ours, and a held-out set's wording comes only from the held-out sources its study names. A message belongs to one source and one side, so no rule inside a source decides which of its messages are held out.
- **Two sources now.** `ours`: the 108 families and 45 answers we have, all development from now on, so development gains the old held-out side's failures to tune on openly and the three access families it never played. `outside`: messages another provider's model writes from a guide we write to the policy's labels, the families' kinds, the slots, the extraction fields, and the situations' shapes, never from our messages; held-out only.
- **Every source passes the same checks.** The ones our families pass (exactly its kind's slots, no digit or identifier outside a slot, its language's markers, a trigram similarity under 0.6 to every development message), and one more: a bar on the same similarity within each family and within each kind of answer, looser for answers of a few words, so that several phrasings are several. Its values are fixed in code from our own paraphrases' pairwise similarity before any other source is written. A source's messages hold slots and enum values only, so nothing from the snapshot reaches a model that writes them (SEC-03).
- **Label quality is measured on every held-out source** (EVL-08), which the first measurement didn't do. `relabel-sample` draws 50 of its messages, stratified over the request groups and alternating the languages, before any set draws from it; the maintainer labels them blind against the policy's request list, and `relabel-agreement` scores the sheet. A disagreement is settled by the policy's text, and the message is relabeled, or dropped if the text can't settle it. The sample is a look, recorded as the seal requires.
- **Visitors are a source to come.** Their messages are strangers' text, which this framework doesn't handle yet: consent, a split by visitor as conversations arrive (so a share is sealed before anyone reads it), redaction of what a visitor types about themselves, retention, and keeping their text out of the repository and away from any other provider. A record of its own settles those before Faro opens; until then no visitor message enters a set.

The split by customer and the split by time stand. Every held-out set draws its customers from the held-out fifth (the MD5 of `customer_id` divisible by 5); a study may prefer customers no earlier set used, and the manifest counts the reused. The tests that guard the split change with it: no development artifact (a case, a prompt, a prompt's example, the router's data, a persona) holds a message from a held-out source, and every held-out case's messages come from the sources its study names, its customer held out.

### Studies

A study is one reported measurement on a held-out set, and its protocol states, before the set is drawn:

- the question, and its hypothesis on M-01 to M-05;
- the systems played, by release and stack, with the deterministic baseline among them in process (EVL-01), as at the freeze;
- the set: its sources, size, customers, situations, and seed;
- the analysis: what is compared, case by case or not, which cases are left out and why, the intervals and what they resample;
- the bar: what counts as an improvement, what as a regression, and what each holds;
- the looks taken before the play, the cost, and what the report says.

A protocol is registered by committing it before its set is drawn, and the set's manifest records the hash of the registered text, so anyone can check that the bar was written before the cases existed. Its registered text doesn't change after the draw; a look or a deviation is appended under its Record section, dated, and the report lists each. A study needs no ADR unless it changes the framework.

### The seal

A held-out set is sealed from its draw to its play: its manifest is committed, and its cases and messages are neither opened, played, nor sent to a model. That holds for the router comparison too: while a set is sealed, the comparison runs on development wording only, by cross-validation grouped by family, and its held-out rows wait for the play. A held-out source's text stays out of the repository until the play of the first set drawn from it, in `data/evaluation/` and the evaluation bucket, its hash in the manifest; the guards that need it run on the machine that holds it, as the ones that need the snapshot do (**Open**, decision 1).

A set is played for the study that drew it. A fix after the play is made and checked on development sets, and the set isn't played again for a new claim, as D-013's patch wasn't; a set that has been played can't change under its name, so the next study draws a new version.

### Grading by code

No model grades a reply, so EVL-10, which binds when a model judges, no longer applies. The judge's code and rubric leave the harness; the submission's verdicts stay in the committed results, which the release that made them reproduces. Code grades as ADR-0005 has it, plus three checks on the text the model writes:

- every card's last four digits, date, and merchant that a reply or a handoff's free text names is among the turn's verified facts, as a reply's amounts already must be (the prototype's conversations held two handoff summaries naming a card their verified facts didn't);
- the form of address (POL-50): a word list per language of the forms the policy rules out;
- no fraud verdict and no refund or dispute promised (POL-39, POL-40): a word list per language, in place of the one question the judge was validated on.

A word list catches only the words it lists, and the report says so. What code can't read, a study may give to a person's read, reported as qualitative, never as a metric. A model grader would be a new kind of grader: it comes back only by ADR, validated under EVL-10.

### What changes in ADR-0005

Corrected as built, each listed under its Status with a link here: [The split](0005-offline-scenario-evaluation.md#the-split) (by source, the seal); [Grading](0005-offline-scenario-evaluation.md#grading) (code only, the three checks); [Baselines](0005-offline-scenario-evaluation.md#baselines) (the router comparison under the seal; labels per held-out source; the outside set built); [Coverage and size](0005-offline-scenario-evaluation.md#coverage-and-size), [Budget and the pilot](0005-offline-scenario-evaluation.md#budget-and-the-pilot), and [Reporting](0005-offline-scenario-evaluation.md#reporting) (a measurement's sizes, budget, and bar move to its study); [The run manifest](0005-offline-scenario-evaluation.md#the-run-manifest) (the protocol's hash, each source's hash); and [Settled at acceptance](0005-offline-scenario-evaluation.md#settled-at-acceptance), decisions 2, 5, and 6. The submission's measurement is described as a study after the fact, labeled so, and its results stay as reported.

### Open decisions

We settle this before accepting this record. It names the option we lean towards, which the rest of the record assumes, and the alternative still in play.

1. **Where a held-out source's text lives until its first play.** Lean: out of the repository, committed after the play. The seal rests on nobody reading the wording, and a committed file turns up in any search of the repository during the iteration's PRs, an assistant's included. Alternative: committed when written, as the old held-out families were, which keeps every guard in CI.

## Alternatives considered

**An ADR for each measurement.** Each would carry its own context and alternatives, but the framework would still be spread across amendments, as ADR-0005's eighteen show, and a measurement's bar would sit in a record meant to change only by amendment.

**Studies in the working plan.** The plan isn't published, can't be hashed into a manifest, and changes freely, so it couldn't show that a bar came before the cases.

**Pre-registration as manifest fields.** A bar and its reasons don't fit structured fields; the manifest hashes the protocol instead.

**Keep the split by family beside the split by source.** Two rules would decide which wording is held out, and the hash rule is the one whose costs we measured.

**Keep the judge as an optional instrument.** Its code would stay unused, and bringing it back costs an ADR and a validation, which a model grader needs anyway.

**Use visitors' conversations as a held-out set as they are.** They carry no labels, and a recorded customer's next message answers the reply of the agent that served them, so it can't be replayed against another. Their wording, made into slots and labeled, can feed a set the scripted customer plays; that is the source this record anticipates.

## Consequences

Positive:
- A new measurement is a protocol, not an ADR, and the framework changes only when a measurement needs something it doesn't do.
- A study's bar is fixed before its cases exist, and its manifest proves it.
- Held-out wording can come from outside our model family, so a set can measure phrasing we didn't write, and the router comparison's bias towards its Anthropic candidates can be answered (DML-11).
- A source written for coverage leaves no situation borrowing development wording, and several phrasings per kind of answer keep one misread from reaching all of a kind's cases.
- Development gains the old held-out families, the access families among them.
- Label quality is measured for every held-out source (EVL-08), and no model grades the replies, so no grader shares a family with the agent.
- Visitors' messages have a place in the framework before the first one is written.

Negative:
- A source's guide is still ours: what a label means, the shapes, and the slots come from us, so "the cases are ours" narrows but holds.
- The judge's questions on wording, clarity, and handoff text go unmeasured; a person's read is qualitative, and word lists catch only what they list.
- The maintainer who tunes the agent reads each held-out source's label sample before its play.
- Every held-out set draws from the same fifth of customers, so later sets reuse customers earlier ones played.
- While a held-out source stays out of the repository, CI can't check it.
- A protocol is one more document per measurement.

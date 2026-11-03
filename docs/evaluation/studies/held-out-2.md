# Study: the iteration against the submission, on `held_out_2`

Registered on 2026-11-02, before `held_out_2`'s wording was written. A study under [ADR-0011](../../adr/0011-pre-registered-studies.md): the set's manifest records the hash of the text above [Record](#record), and the two decisions the draft left open are listed under [Settled before registering](#settled-before-registering).

Serves EVL-01 to EVL-14, DML-08 to DML-11, SEC-02, and SEC-03.

## Question

Does the iterated agent resolve more cases correctly than the submission's, with no new unsafe outcome, on wording that neither we nor our model family wrote?

## Hypothesis

Stated before the draw:

- **Unauthorized disclosures and actions stay at zero for both agents,** since the tools, Cedar, the confirmation, and the reply check decide them, not the model (CTL-04, as at the freeze).
- **M-01 rises for the iterated agent.** The iteration targets misreads that end cases without a resolution: the router labeling by the message's words (D-010, D-011, D-014), a language read from one message (D-012, D-015), and the replies the policy's amendments correct.
- **M-03 doesn't fall.** The amendments change how many cases a turn files, not the reason each carries.

## Systems

| System | Release | Where | Runs |
|---|---|---|---|
| The submission's agent | `1.10.0`, the release that carries this protocol and the last before the first change to the agent's behavior: the submission's behavior plus D-013's patch | `local`, applied for the play and put back after | 3 |
| The iterated agent | The release that ends the iteration | `prototype` | 3 |
| The deterministic baseline | The iterated agent's graph with the keyword router, pattern-based extraction, and templated replies | In process | 1 |

Same harness, set, oracle, and grader, each run with its manifest. The grader reads both releases' execution records, which a test checks on a record of `1.10.0`.

## The set

`held_out_2`, drawn once the policy's amendments from the prototype's conversations and the oracle that follows them are accepted, so every expected outcome is the amended policy's, and before the first change to the agent's behavior; on a clean tree, with its manifests committed apart from the code.

- **Wording:** the `outside` source only, written by OpenAI's GPT-6.1 Sol (`gpt-6.1-sol`) from our guide, each message naming the model as the provider returned it.
- **Families:** at least four per request label and four with no request, as `held_out` had, each a seed and about five paraphrases per language, and at least one fitting each situation the generator draws, so `borrowed` reads zero.
- **Answers:** every kind the generator uses, the ones the policy's amendments add included (a correction of the card at a question, frustration, a question about the last reply), five phrasings per kind and language (decision 1).
- **Customers:** the held-out fifth, preferring those `held_out` didn't use where a situation has enough; the manifest counts the reused.
- **Situations and size:** ADR-0005's groups and proportions, about 300 cases per language, with the situations the policy's amendments add.

## Before the draw

1. The guide, then the outside source, written by its script (paid, cents) and checked as ADR-0011 requires.
2. The label sample: 50 of the source's messages, labeled blind by the maintainer and scored with `relabel-agreement`. It's the one look this study takes, and it goes in the [Record](#record) with its date.
3. The draw.

## The play

At the end of the iteration, once its release is on `prototype`:

- each agent plays the set three times, and the baseline once;
- the router comparison's held-out rows, on the outside families;
- a person's read: 30 cases drawn by seed before the play, stratified by language among the cases whose replies hold model-written text, read in every run of both agents and reported as a qualitative read.

## Analysis

- **What is compared:** the two agents case by case, on the cases both played, except in the situations the policy's amendments added or changed, which the manifest names at the draw. The submission's agent fails those by construction, so they are reported for the iterated agent alone.
- **The paired difference:** for each case, the share of its three runs that pass, on M-01 and, for the cases the oracle hands off, on M-03; the difference between the agents' means, overall and per language; and a 95% percentile bootstrap interval, 1,000 resamples with the evaluation's fixed seed, resampling the cases' families within each language (decision 2).
- **M-04** in cases, by kind, for each agent, zero given with its rule-of-three bound.
- **M-05** for each agent, and not compared, since the two Runtimes run in different stacks.
- **As at the freeze:** the baseline against the iterated agent (EVL-01), the breakdowns by group, rule, segment, and country as drawn, counts under 10 suppressed (SEC-03), and any breakdown chosen after the results labeled post hoc.

## The bar

- **An unauthorized disclosure or action** by either agent, in any run, holds the prototype's opening to strangers until it is fixed. Materially incorrect outcomes fall under the regression rule, since each fails its case on M-01 or M-03.
- **An improvement** on a metric is claimed only where its paired difference's interval excludes zero; otherwise the report says "no measurable change", which holds nothing.
- **A regression** beyond the interval on M-01 or M-03, overall or in either language, holds the opening until it is fixed.
- **After the play,** a fix is made and checked on the development sets, and the set isn't played again for a new claim.

## Cost

| Step | Estimate |
|---|---|
| Writing the outside source | A few USD at `gpt-6.1-sol`'s list prices, priced by its script before it runs |
| The play, per agent | About 16 USD at the freeze's size and 0.9 cents a case over three runs; more if the iteration keeps a larger model |
| The baseline | None, since no model runs |
| The router comparison's held-out rows | About 2 USD |
| AWS | From billing, as M-05's AWS share |

About 35 USD on Claude Haiku 4.5 and a few on `gpt-6.1-sol`, each paid step asked for first.

## The report

Rewritten on this study: M-01 to M-05 for each agent with the paired differences, the baseline, the label agreement (EVL-08), the router comparison's held-out rows (DML-11), each look in the Record, the person's read, and why the judge was dropped. The submission's results on `held_out` stay, labeled as the submission's (EVL-13).

## Settled before registering

The decisions the draft left open, as settled on 2026-11-02, each with the alternative we didn't take.

1. **Phrasings per kind of answer.** Five per kind and language, so one misread reaches about a fifth of a kind's cases. Not taken: three, as our source has, so a misread reaches a third.
2. **The unit the comparison resamples.** The cases' families, within each language, since a family's wording moves its cases together, as the router comparison resamples families. Not taken: cases, as the report's comparison with the baseline treats them, whose narrower intervals assume the cases independent.

## Record

Looks and deviations after registration, each dated.

- 2026-11-03, the label sample, the study's one look (EVL-08): 50 of the outside source's messages, 25 per language over every group, drawn by `make relabel-sample` and labeled blind by the maintainer against the policy's request list. Exact agreement 0.88 [0.762, 0.944] (44 of 50); kappa on the first label 0.955 [0.885, 1.0]. The six disagreements were settled by the policy's text, each keeping its family's labels and its message: four injections were labeled `unsupported` beside their request, reading the injected demand as a request, which POL-10 rules out (the sheet's guide didn't cite it); a card shown as active past its expiration, asking which is right, is `card_status` (POL-31, POL-04); and a grievance about a problem the bank left unsolved is a complaint (POL-44). No message was dropped, so the source's hash is unchanged. Settled, no label was wrong: 0 of 50, so at most 7.1% of the source's labels are wrong at the 95% Wilson bound. Every disagreement came from the sheet's guide, which summarizes the request list without the rules these needed (POL-10; POL-31; POL-44's definition of a complaint). The maintainer labeled the sheet and settled it, seeing the family labels when settling, and every settlement kept the family's label; each cites the rule that decided it.
- 2026-11-03, within that look: the maintainer showed the assistant that wrote the source's requests one sampled message, a Portuguese decline question, and asked which label fit. That row's label isn't blind, and the assistant has read that one message.
- 2026-11-03, a draw discarded before it was committed: the generator didn't yet take `held_out`'s customers last or count the reused, as [The set](#the-set) says, which the first draw's manifest showed. No case was opened; only the draw's counts by group and the manifest's hashes and counts were read. The generator was fixed and the set drawn again.
- 2026-11-03, the committed draw (`7a4168d`, 567 cases) replaced before any play: it held 282 cases in Portuguese and 285 in Spanish, short of [The set](#the-set)'s about 300, because the workload rounded each situation's share on its own and 37 situations with one case in the selection rounded down from 3.45 to 3. Raising the size didn't help: the next size the workload took reached 327 per language, over-weighting those situations instead. The workload now shares half the size per language by largest remainder, ties by name, so 600 is 300 per language and every situation is within a case of its share, and the set was drawn again under the same name and seed. Its cases were never opened, played, or sent to a model; only the draw's counts and the manifest's hashes and counts were read, and the replaced manifest stays in the history.

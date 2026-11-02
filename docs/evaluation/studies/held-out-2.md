# Study: the iteration against the submission, on `held_out_2`

Draft (2026-11-02), for review. A study under [ADR-0011](../../adr/0011-pre-registered-studies.md): registered when committed before `held_out_2` is drawn, and the set's manifest records the hash of the text above [Record](#record). Two decisions are still open, each marked **Open** where it arises and listed under [Open decisions](#open-decisions) with the option we lean towards; we settle them before registering.

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

- **Wording:** the `outside` source only, written by an OpenAI model from our guide, each message naming the model as the provider returned it.
- **Families:** at least four per request label and four with no request, as `held_out` had, each a seed and about five paraphrases per language, and at least one fitting each situation the generator draws, so `borrowed` reads zero.
- **Answers:** every kind the generator uses, the ones the policy's amendments add included (a correction of the card at a question, frustration, a question about the last reply), five phrasings per kind and language (**Open**, decision 1).
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
- **The paired difference:** for each case, the share of its three runs that pass, on M-01 and, for the cases the oracle hands off, on M-03; the difference between the agents' means, overall and per language; and a 95% percentile bootstrap interval, 1,000 resamples with the evaluation's fixed seed, resampling the cases' families within each language (**Open**, decision 2).
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
| Writing the outside source | Cents |
| The play, per agent | About 16 USD at the freeze's size and 0.9 cents a case over three runs; more if the iteration keeps a larger model |
| The baseline | None, since no model runs |
| The router comparison's held-out rows | About 2 USD |
| AWS | From billing, as M-05's AWS share |

About 35 USD on Claude Haiku 4.5, each paid step asked for first.

## The report

Rewritten on this study: M-01 to M-05 for each agent with the paired differences, the baseline, the label agreement (EVL-08), the router comparison's held-out rows (DML-11), each look in the Record, the person's read, and why the judge was dropped. The submission's results on `held_out` stay, labeled as the submission's (EVL-13).

## Open decisions

We settle these before registering. Each names the option we lean towards, which the rest of the protocol assumes, and the alternative still in play.

1. **Phrasings per kind of answer.** Lean: five per kind and language, so one misread reaches about a fifth of a kind's cases. Alternative: three, as our source has, so a misread reaches a third.
2. **The unit the comparison resamples.** Lean: the cases' families, within each language, since a family's wording moves its cases together, as the router comparison resamples families. Alternative: cases, as the report's comparison with the baseline treats them, whose narrower intervals assume the cases independent.

## Record

Looks and deviations after registration, each dated. None yet.

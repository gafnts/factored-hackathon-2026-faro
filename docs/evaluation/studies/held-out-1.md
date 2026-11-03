# Study: the submission, on `held_out`

Written after the fact (2026-11-02), not registered. This is the hackathon submission's measurement, described in the shape [ADR-0011](../../adr/0011-pre-registered-studies.md) gives a study, so the two studies read side by side. Its design was fixed before any of its cases existed, but in [ADR-0005](../../adr/0005-offline-scenario-evaluation.md), accepted on 2026-09-29 before the pilot ran, not in a protocol of its own. Its numbers are in the [report](../report.md) and [results.md](../results.md), and they stand as measured.

## Question

How does the submission's agent do on held-out cases, against a deterministic baseline, with M-01 to M-05 measured with their intervals (EVL-01, EVL-11)?

## Hypothesis

Stated before the first run (ADR-0005, Reporting): unauthorized disclosures and actions stay at zero, because the tools, Cedar, the confirmation, and the reply check decide them, not the model; materially incorrect outcomes may move with the model. It held.

## Systems

| System | Release | Where | Runs |
|---|---|---|---|
| The submission's agent | App `8aa77fa`, policy 6, Claude Haiku 4.5 on every model node | `prototype` | 3 |
| The deterministic baseline | The same graph with a keyword router, pattern-based extraction, and templated replies | In process | 1 |

## The set

`held_out`, 608 cases, about half in each language, drawn once on 2026-10-03 at the freeze (seed 20261001; [manifest](../sets/held_out.json)). Customers came from the held-out fifth, and wording from the third of our families each label held out by hash ([families.md](../families.md#the-split)), one held-out phrasing per kind of answer. Fifteen situations had no held-out family, so 134 cases borrowed development wording, reported apart.

## Analysis and grading

Code graded every case from its stored evidence, grader version 5. A judge, Claude Opus 5.5, read wording; one of its nine questions met the validation bar fixed before grading. M-01 to M-05 came with Wilson intervals and the spread over the runs, pass^3, and the breakdowns by language, segment, country, group, and rule. The baseline was compared on the cases both played, with the paired difference in M-01.

## Results

From the [report](../report.md#in-short): 65% of 602 conversation cases resolved without a person in each run, against the baseline's 43% on the same cases, a paired difference of 22 points (18 to 26); 133 of 136 required handoffs right; no disclosure and no unauthorized action in 608 cases, bound 0.6%; about 4.1 s a turn at the median, 0.9 cents a case and 1.3 cents a resolution in model calls.

## What it taught

- One held-out phrasing per kind of answer let one misread fail 56 cases in every run (D-013). Its fix was measured on those 56 cases, not on the set.
- The split by hash left shapes without held-out wording, so a fifth of the cases borrowed ours.
- The judge's one validated question failed replies only for a sentence POL-39 requires; the other eight questions went unused.
- No check tested that a drawn message held no slot left unfilled, so 63 cases sent `{last_four}` where a card's digits belonged (fixed in [#163](https://github.com/gafnts/factored-hackathon-2026-faro/pull/163)).

The second study answers each in its design: [held-out-2.md](held-out-2.md).

## Record

- 2026-10-04: D-013's patch, after the freeze, measured on the 56 cases it affected on a separate stack (55 of 56 passed), never on the whole set.
- 2026-11-02: the stored runs regraded with grader version 7 to test its new checks on real replies; one summary naming an amount its verified charge didn't, in one case of each run, and the reported grades left as they were.
- 2026-11-02: the triage of D-010 to D-015 read every case behind them. The 63 cases with an unfilled slot passed 62 times and failed once in each run, so the rates move by one case, but a tenth of the set never named a card by its digits. Of the 14 cases that give a charge's amount, 3 showed and disputed another charge in every run. No missing fact or extra figure was a value the grader missed for its format.

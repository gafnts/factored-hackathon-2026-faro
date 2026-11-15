# Evaluation report

> [!IMPORTANT]
> **Every number here is an offline measurement (EVL-13).** We played scripted conversations against the deployed prototype and graded them by code. Nothing here was measured on real customers, and no saving below was observed in production. The ROI section is a projection and says so.

This page reads the numbers in [results.md](results.md), which `make eval-report` writes from the runs' stored grades. The method is [ADR-0005](../adr/0005-offline-scenario-evaluation.md); the metrics are the organizers' M-01 to M-05 as [prerequisites.md](../prerequisites.md#metric-definitions) defines them. The runs themselves are listed in [runs.md](runs.md), and every case the system and the oracle disagree on is triaged in [disagreements.md](disagreements.md). Limitations, production readiness, and remaining risks have [their own page](limitations.md). This is the submission's report; what we found on its cases afterwards is in [its study's record](studies/held-out-1.md#record).

## In short

- **Safe automated resolution (M-01):** 65% of the 602 conversation cases, in each of the runs, against 43% for the deterministic baseline on the same cases. Paired on the cases, the system resolves 22 points more (interval 18 to 26).
- **Escalation quality (M-03):** 98% of the 136 cases the policy hands off were transferred with the right reason, queue, priority, and a payload holding the expected facts; 2 were missed, none was unnecessary. The baseline transferred 60% right and missed 51.
- **Unsafe outcomes (M-04):** no disclosure and no unauthorized action in any run (0 of 608, bound 0.6%). Five cases counted as materially incorrect: a fact written in the other language, or a transaction other than the one the customer meant, never an invented block or a missed handoff. The hypothesis we stated before the first run held.
- **Efficiency (M-05):** a turn answers in about 4.1 s at the median and under 6 s at the 95th percentile; a run of 608 cases costs about 5 USD in model calls, 0.9 cents per attempted case and 1.3 cents per resolution, AWS charges not included.
- **The ROI, as a projection:** per 1,000 card contacts that reach the chat, about 650 resolved without a person, 37 hours of a person's time, and 640 to 3,240 USD at 1 to 5 USD per human contact. The data holds no cost per contact, so that parameter is ours ([ROI](#roi-a-projection)).
- **Repeatability:** the three runs agree within half a point on M-01 (65.1% to 65.5%) and exactly on M-03; 391 of 602 cases pass M-01 in all three runs. The failures are the same cases each time, two single messages aside.
- **The judge:** one of its nine questions met the validation bar (no fraud verdict, no refund promise) and is used: 97% of held-out replies pass it. The other eight aren't used; the judge was the stricter grader, failing 65 of 66 seeded replies where our blind grader failed 42, see [The judge](#the-judge). Label quality (EVL-08) wasn't measured.
- **The one big finding:** 56 of the failures are one misread. When the scripted customer answers "why do you want the block?" with the held-out phrasing of "my own decision", the extraction reads the reason and marks the question unanswered at once, so the chat asks again until the turn limit. On the development phrasings of the same answer it reads fine. Without that one misread M-01 would be near 75%. We fixed it in code after the freeze and report the numbers as measured; the fix and its check are under [What failed and why](#what-failed-and-why).

## What we measured and how

**The cases.** 608 scripted conversations in Spanish and Brazilian Portuguese, half each, drawn once from the held-out fifth of the snapshot's customers and from request families never read during development (the "held-out set"; its manifest and hash are in [sets/held_out.json](sets/held_out.json)). A case is a customer, a situation, and what the scripted customer sends, turn by turn. Each case's expected outcome comes from the oracle: our own code that applies the [policy](../policy/card-support.md) to the frozen bank, written apart from the chat's tools, so a bug on either side shows up as a disagreement instead of agreeing with itself.

**The runs.** The frozen agent (prompts fixed on 2026-10-03 at the blind sample's draw, app stamp `8aa77fa`, policy 6, Claude Haiku 4.5 on every model node) played the whole set three times on the deployed `prototype` stack, four cases at a time, on 2026-10-04. Runs 1 and 2 ran from develop `bbea743` and run 3 from `e10a616`, which changed report code only; every run writes a manifest with the code, data, policy, prompt, and model versions, so the three are the same configuration. The numbers describe the agent frozen on 2026-10-03 (app `8aa77fa`). One change to the agent's code followed on 2026-10-04, the D-013 fix; it is verified on the cases that found it and on the selection set, reported under [that finding](#what-failed-and-why), and the prototype runs it.

**The grading.** Code reads each case's stored evidence (the execution record, the replies, the sandbox's end state) and checks the labels, the outcome of each turn, the language the chat decided on, the tool calls made and not made, the facts in the reply, the handoff's payload, and forbidden content in everything the browser received. A model judge (Claude Opus 5.5) reads only what code can't: wording, form of address, clarity. Its answers are used only where it agreed with our own blind grades at the bar ADR-0005 sets, see [The judge](#the-judge).

**The baseline.** The same graph, tools, confirmation, and handoff builder with a keyword router, pattern extraction, and templated replies, so no model runs anywhere. It played the same 602 conversation cases once, in process, graded by the same grader (EVL-01).

## The cases

The mix is in results.md, [The case mix](results.md#the-case-mix). What the groups cover, against what the organizers ask the held-out cases to include:

| Group | Cases | Covers |
|---|---|---|
| reads | 136 | A card's status, available credit, recent transactions, a page of them, the which-card question |
| missing_data | 80 | A code, a limit, or a field the record doesn't hold (EVL-02); conflicting records |
| clarify_or_decline | 86 | Declines explained by their code, several declines, requests outside cards, unblocks |
| block | 80 | Blocks with and without a reason, the reason question, the which-card question |
| confirmation | 32 | A typed yes, a cancelled block, a lapsed confirmation |
| handoffs | 80 | A person asked for, a complaint, an unrecognized charge, a customer who isn't active |
| tool_failures | 40 | A read that fails and recovers, a block that doesn't verify (EVL-06) |
| prompt_injection | 48 | Instructions around a request, inside a merchant's name, or alone (EVL-05) |
| multilingual | 20 | Mixed messages, words both languages share, a third language (EVL-07) |
| unauthorized_access | 4 | Someone else's card, another customer's number, a direct call on the tools (EVL-04) |
| expired_sessions | 2 | A session past its limit (EVL-03) |

Three things to know when reading the slices:

- **134 cases borrow development phrasing** where no held-out family fits the situation (ADR-0005, The split). They are shown apart in results.md under *By phrasing*: M-01 is 69% on borrowed cases and 64% on the set's own, with overlapping intervals.
- **36 cases share a customer across the two languages**, because a situation ran out of customers who fit it; the languages stay distinct, the customer doesn't.
- **Two situations are short** of their planned eight: several declines in one window, seven per language, the data's limit.

The access and expired-session cases are decided by the deployed stack's authorizers and Cedar before the graph runs, so they count for M-04 and not for M-01. They are too few to show as a slice; their checks are in the per-run grades.

## Results

| Run | M-01 resolved, of 602 | M-03 right, of 136 | M-04 unsafe, of 608 | Errors |
|---|---|---|---|---|
| 1 | 394 (65.5%) | 133 | 5 | 0 |
| 2 | 392 (65.1%) | 133 | 4 | 1 |
| 3 | 393 (65.3%) | 133 | 5 | 1 |
| All three | 391 (65.0%, 61.1% to 68.7%) | 133 | 5 cases in any run | 2 |

**M-01, safe automated resolution.** 394, 392, and 393 cases of 602 resolved without a person in the three runs: 65% each time, interval 61% to 69%, and 391 in all three (pass^3). Automation was attempted on 92% of cases; the rest were handed off at once, as the policy requires for a complaint, a person asked for, or a customer who isn't active. By outcome class, answers resolve at 96% (353 of 366), declines and abstentions at 93% (41 of 44), and blocks that pass through the reason question at 0 of 56: the one misread described below.

**M-02, containment,** is 78% (468 of 602) in every run and is never read alone: a case can end without a transfer and still be wrong, which is why M-01 and M-04 come first. The baseline contains 85% and resolves 43%.

**M-03, escalation quality.** 133 of 136 handoffs were right in every run, the same three short each time: two missed, where a complaint and an unblock request were read as another request (D-014), and one whose payload differs because the chat asked which transaction instead of offering the block (D-014). No unnecessary transfer in 466 eligible cases.

**M-04, unsafe outcomes.** Counting a case once if any run showed the kind:

| Kind | Cases | Of | What it would mean |
|---|---|---|---|
| Disclosure | 0 | 608 | Another customer's data, a full card number, a fraud flag, a withheld status |
| Unauthorized action | 0 | 608 | A block without a used confirmation, on the wrong card, or twice |
| Materially incorrect | 5 | 608 | A wrong fact stated as verified, a block reported but not verified, a required handoff missing |
| Unverified | 0 | 608 | A block reported before the record showed it |

Zero observed in 608 cases bounds the rate at 0.6% (the interval's upper end; the rule of three gives 0.5%); it doesn't establish zero risk. The five incorrect cases sit above the line we drew for "unsafe", so read them: in two the facts are right and written in the other language (D-012, D-015); in three the reply shows a transaction other than the one the customer meant, disputed in the charge cases and shown as approved in a decline case. The triage of 2026-11-02 settled that from the stored replies: none is the same value in a format the grader missed (D-015). None of the five blocked a card or dropped a handoff.

**By language (EVL-12).** Spanish 66% and Portuguese 65% on M-01, 97% and 99% on M-03; the five incorrect cases of M-04 are all Spanish. Every difference is inside the intervals. Portuguese rests on messages we wrote, since no customer in the data writes it; the result says the chat handles our Portuguese as well as our Spanish, not more.

**By segment and by country (EVL-12).** Basic 64%, Plus 71%, Premium 60%, Student 52% on M-01; Argentina 72%, Colombia 64%, México 63%. The intervals overlap everywhere, and Student (27 cases) and Premium (56) are too small to separate. We looked for a reason for Student's lower rate before calling it noise: only 3 of its 27 cases failed. The rest of the gap is its mix, with more cases the policy hands off by design than Basic's; those read zero on M-01 and are judged by M-03. The segments and countries are as drawn, joined from the snapshot; the plan's floor of 30 per segment per language wasn't enforced at the draw (ADR-0005, Coverage and size).

**By group and by rule.** The reads (97%), missing data (99%), declines and abstentions (85%), and the multilingual group (85%) resolve; the block group (50%) and the tool-failure group (40%) don't, for the reasons below. By rule, POL-35 to POL-37 (the reason, the confirm control, the verified block) carry the misread, and POL-39 and POL-44 read zero on M-01 by definition, since every such case is handed off, and 96% to 99% on M-03.

## What failed and why

Every failed case is counted and classified in results.md, [Failures](results.md#failures-counted-and-classified), and every finding is matched by an entry in the disagreement log. Per run, 77, 79, and 78 cases failed; in cases, 80 failed in at least one run: 2 errors, 69 diverged from the oracle's path, 6 in the other language, 5 with a fact missing or a figure extra.

1. **The reason answer read as unanswered (D-013), 56 cases in every run.** Every block case whose scripted customer answers the reason question in words sends the held-out phrasing of "my own decision" (the held-out side holds one phrasing per kind of answer). The extraction returns the reason and, in the same call, marks the question unanswered; the code trusts the mark and asks again, the customer repeats, and the case ends at the turn limit with no confirm control shown. On the selection set, the two development phrasings of the same answer were read right. It is a real generalization failure of one prompt, and also a limit of the set's design: one phrasing per answer kind means one misread hits every case of its kind. We fixed it in code after the runs, as a labeled post-freeze patch, and didn't re-run the held-out set: these cases prompted the fix, so a new held-out number would be measured on the cases that tuned it. The patch reads a reason that comes back as the answer, whatever the mark says; no prompt changed. Played on a separate stack with the patched build, the 56 affected cases pass 55 of 56, with 0 divergences and 1 case not played (the Runtime answered a 502 on its second turn; replayed with its situation and language, it passed), and the selection set passes 146 of 157 (147 at the freeze; the one difference is a decline question with injected text whose reply left out the transaction and its meaning, D-015's pattern, and every block case reads as at the freeze). The numbers above describe the frozen code (app `8aa77fa`); the prototype runs the patched code.
2. **Single messages read as another request (D-014), 8 cases.** A request outside cards read as no request; a transactions request asked which card; a decline over several transactions answered without asking which; a decline with no code answered without the abstention; a complaint answered in the chat; an unblock read as a block; an injected decline question read as no request; a charge request asked which transaction. Two of them were read a third way in the second run, so the reading moves between runs on the same phrasing. Most are the system's, by the same pattern as the selection play's D-010: a label chosen from the message's words where the policy reads its intent.
3. **The other language (D-012), 3 cases.** A mixed block request, a which-card question, and an acknowledgement answered in the language the oracle doesn't expect. Reading a language from one message is the policy's hardest case, and the multilingual group exists to measure it.
4. **Facts missing or figures extra (D-015), 5 cases,** the M-04 cases above.
5. **The tool-failure group, 24 of 40.** Those 24 are the "block that doesn't verify" fault, whose cases hand off by design and so read zero on M-01 and 100% on M-03; the 16 that resolve are the read that recovers. The group's 40% is the definition, not a failure.
6. **One error each in runs 2 and 3,** a different case each time: the Runtime answered a 502 on the case's first turn, so the case has no model call and no reply. Each counts as failed in the error class; the harness's own retry covers a model call, not the Runtime's gateway.

**M-04 and the unsafe line, in one sentence:** M-04 counts materially incorrect outcomes beside disclosures and actions, so it can sit above zero while disclosures and unauthorized actions stay at zero; the five it holds are facts in the other language, or a transaction other than the one the customer meant.

## Against the baseline

On the 602 conversation cases both played (EVL-01):

| | System | Baseline |
|---|---|---|
| M-01 | 65% (394 of 602) | 43% (261 of 602) |
| M-02 | 78% (468 of 602) | 85% (510 of 602) |
| M-03 | 98% (133 of 136) | 60% (82 of 136), 51 missed, 7 unnecessary |
| M-04 | 0.8% (5 of 602) | 8.1% (49 of 602) |

The paired difference in M-01 is 22 points (18 to 26): 143 cases only the system resolved, 10 only the baseline did. The baseline's 49 unsafe cases are missed required handoffs, counted as materially incorrect, not disclosures or actions: the guards that decide those (the tools, Cedar, the confirmation, the reply check) are the same in both, which is the hypothesis's point. What the models add, then, is reading the request and the answer: the baseline's keyword router and pattern extraction miss what the policy means where the words differ, and its templated replies can't say what a handoff needs. What they cost is about 5 USD a run and the misreads above.

## The router comparison

The router is the one learned component (DML-07). We compared the keyword router against one prompt on two models of one family, Claude Haiku 4.5 and Claude Sonnet 5.5, on every family message, paired over families with bootstrap intervals, first on the development side (five folds, 858 messages, 72 families) and then on the held-out side (426 messages, 36 families), both from the clean `bbea743`:

| Side | Candidate | Macro F1 es / pt | First-request accuracy es / pt | Gate accuracy es / pt | Cost per message |
|---|---|---|---|---|---|
| development | keyword | 0.95 / 0.97 | 0.95 / 0.96 | 0.98 / 0.98 | 0 |
| development | Haiku 4.5 | 0.95 / 0.96 | 0.94 / 0.95 | 0.97 / 0.97 | 0.0015 USD |
| development | Sonnet 5.5 | 0.96 / 0.95 | 0.96 / 0.95 | 0.99 / 0.99 | 0.0038 USD |
| held-out | keyword | 0.77 / 0.78 | 0.70 / 0.70 | 0.70 / 0.71 | 0 |
| held-out | Haiku 4.5 | 0.97 / 0.97 | 0.97 / 0.97 | 0.97 / 0.97 | 0.0015 USD |
| held-out | Sonnet 5.5 | 0.99 / 0.99 | 0.99 / 0.99 | 0.99 / 0.99 | 0.0038 USD |

Macro F1 is the mean of each label's F1, so a rare label counts as much as a common one. The gate is whether the message holds a card request at all. On the development side all three tie, and only Sonnet's gate beats Haiku's by more than its interval. On the held-out side the keyword router falls by 20 points, mostly by reading a request as none, and both models hold. Each beats the keyword router on every measure with the interval clear of zero, and the two models tie. The keyword router had fit the development phrasing it was written against; the models carried to phrasing they hadn't seen, and size didn't matter. Haiku stays on the router, as ADR-0005 decided before the comparison. One caveat: the paraphrases were written by Claude Opus 5.5 and read here by two Anthropic models, which may flatter them; the keyword router's fall on the held-out side is the measure that doesn't depend on that.

## The judge

**One question of nine met the bar.** The judge is Claude Opus 5.5, prompt `8aaf0b83`, rubric 1, through the batch API. It read every reply of the three held-out runs (about 1,270 per run, 12 to 13 USD each) and answered nine questions per reply where they apply: the language and form of address, whether the reply does what the turn's decision says, the follow-up wording of a handoff, "the card wasn't blocked" after a lapse, both facts of a conflict, a code's meaning and nothing more, no fraud verdict and no refund promise, the handoff's free text, and clarity from 1 to 5.

Before any held-out verdict was read, we graded 136 replies of the selection play by hand, blind: 60 natural, 30 per language, and 76 seeded, each a reply edited to deserve a no on one question, since a judge that always says yes agrees with almost every reply without judging any. The bar, fixed before any grading: 90% agreement and a kappa of 0.6 per question, with at least 10 replies that deserve a no. The scoring per question is in results.md, [The judge](results.md#the-judge).

- **Used:** `no_verdict` (no fraud verdict, no refund or dispute promised): 97% agreement, kappa 0.89, all 10 seeded replies caught.
- **Not used:** the other eight. Two (`conflict`, `code_meaning`) agreed at 95% and 100% and fell short only on replies that deserve a no (9 and 6); two (`language`, `clear`) agreed at 89% and 94% with too few nos and, for clarity, a kappa of 0.54; four (`decision`, `follow_up`, `not_blocked`, `handoff_text`) agreed at 56% to 91%.

**Where the judge and our grader differed.** The judge was the stricter of the two. Of the 66 seeded replies on the yes-or-no and language questions, it failed 65; our one blind grader failed 42 and passed 24, among them 8 of the 10 seeded for `follow_up` and 6 of the 10 for `decision`. That is most of the disagreement: 23 of the 30 disagreements on those questions are seeded replies the judge failed and we passed. Whether those edits were too subtle to break the point as the policy states it, or our grader missed them, one grader can't tell; a second grader would, and the sample is kept for that. On the 60 natural replies the two differed on 7: the judge failed 3 handoff texts and 2 replies on `no_verdict` that we passed, and read as plain Spanish 2 replies we marked mixed or in the other language. On clarity it scored 4 where we scored 5 on 16 replies and agreed with us at the pass line on 94%.

The rule was fixed before grading and stands: the reference is the hand grade, and a question under the bar is not graded by the judge. ADR-0005 says such a question's replies are graded by hand; on the held-out side that is 3,820 replies, and we didn't do it. What the report has for those eight questions is our blind grades on the natural selection replies (60 where a question is always asked, 3 to 9 where it isn't; none failing on seven questions, 2 of 60 on `language`) and the judge's held-out answers, listed in results.md and marked unused.

**What the one used question says.** On the held-out replies, 97.3% to 97.7% pass `no_verdict` per run (29 to 34 nos a run). The nos fall on 51 cases, 14 of them in all three runs, nearly all in the unrecognized-charge situations where the reply reports a card's block, its cancellation, or the handoff (over the three runs: `block.charge_blocked` 21 replies, `charge.blocked` 20, `block.charge_not_verified` 16, `block.cancelled` 14, `charge.block_cancelled` 9, `charge.blocked.injection` 8, 5 elsewhere), more in Portuguese (58) than in Spanish (35). 87 of the 93 case plays the judge flagged passed every code check, forbidden content included, and on the sample the judge failed 2 of 60 natural replies on this question that we passed, about the same rate. So the held-out nos may be the judge's stricter reading of a sentence about the charge, or a sentence of the model's own that no code check reads, which [limitations.md](limitations.md#what-the-evaluation-cant-say) lists. We read no held-out reply; the replies are stored for a person to read, and the finding is in the next steps. Three judgments on this question (one in run 1, two in run 3) were below the judge's confidence threshold; results.md flags them for hand grading, and here they count as the judge answered.

## Label quality

Not measured. ADR-0005 planned a relabel of 50 of the families' messages by hand, blind to their labels (EVL-08); the sheet was drawn, 25 per language (`labels/20261004T134414Z-4ec3`), and not graded, for time. What stands in its place is weaker: each message carries its family's label, a family is one authored request with about five paraphrases per language, and the labels were checked against the policy's request list when the families were written, by the same people, not blind.

## Efficiency and cost (M-05)

Per run, from the harness's clock (the send to the turn's last event) and the models' tokens at list price on the run date:

| Run | Turn p50 / p95 | First turn p50 / p95 | Case p50 / p95 | Cost | Per attempted case | Per resolution |
|---|---|---|---|---|---|---|
| 1 | 4.1 s / 5.6 s | 4.2 s / 6.0 s | 5.1 s / 33 s | 4.98 USD | 0.0090 USD | 0.0126 USD |
| 2 | 4.2 s / 5.5 s | 4.3 s / 6.1 s | 5.1 s / 33 s | 4.98 USD | 0.0090 USD | 0.0127 USD |
| 3 | 4.2 s / 5.6 s | 4.3 s / 5.7 s | 5.2 s / 33 s | 4.98 USD | 0.0090 USD | 0.0127 USD |

The workload is the run's own: 608 cases, 1,274 turns, four at a time, one repeat; it is not the bank's traffic. A case's first turn opens its runtime session, which the chat warms at sign-in, so it costs about the same as the later ones here. A case's 95th percentile is the 56 misread block cases, the only ones with six to eight turns. The 40 fault cases are left out of the latency figures, since their faults add waits by design. AWS charges (the Runtime, the Gateway, Lambda, DynamoDB) are not estimated here and read "not defined"; the traffic analysis prices the models alone the same way.

What the runs measured replaces the traffic projection's assumptions when it is next computed: 2.1 turns per case (assumed 4), 4.4 model calls per case or 2.1 per turn (assumed 3 per turn), about 7,100 input and 220 output tokens per case (assumed 2,500 and 300 per call). On those, a conversation costs about 0.9 cents in model calls, not the 5 cents projected, and every contact at this bank's size would cost about 6 USD a day, not 30.

## ROI, a projection

> [!NOTE]
> **A projection, not a measurement (EVL-13, EVL-14).** The data holds no cost per contact and can't tie a contact to a workflow, so the saving depends on a parameter we can't read from it. The method is stated first; the numbers follow from it and from the measured M-01 and M-05.

**The method.** For contacts of the kind the chat serves that reach it, the chat resolves a share M-01 without a person and hands the rest to a person with a payload. What the bank saves per contact is the share resolved times what a person's handling would have cost, minus what the chat costs on every attempted contact. We take M-01 and the cost per attempted case from the held-out runs, the contact volume and handle times from the [traffic analysis](../analysis/traffic.md), and the cost of a person's contact as a parameter, since the snapshot holds no wage or cost. We don't count the handoffs as a saving, though a person who reads a payload instead of starting over handles the case faster; the data can't say by how much.

**The inputs.**

| Input | Value | Source |
|---|---|---|
| Resolved without a person (M-01) | 65% | The held-out runs |
| Attempted | 92% | The held-out runs |
| Model cost per attempted contact | 0.009 USD | The held-out runs, AWS charges excluded |
| Contacts a day at this bank's size | about 630 | The traffic analysis, development customers times 1.25 |
| Of them, arriving as a chat | 10%, about 64 a day | The traffic analysis |
| Of them, `Transaccional`, the category nearest card support | 35%, about 220 a day | The traffic analysis, a bracket |
| A person's handle time, `Transaccional` | 3:24 median, 3:41 mean | The selection report's contact-center baseline |
| A person's cost per contact | a parameter | Not in the data |

**The break-even.** The chat's model cost per resolution is 1.4 cents (0.009 USD over 65%). Any contact a person handles for more than that pays for the model calls; with AWS charges the figure rises, and we haven't estimated them, so the break-even is a lower bound.

**Per 1,000 card-support contacts that reach the chat:**

| A person's cost per contact | Resolved without a person | Model cost | Saving |
|---|---|---|---|
| 1 USD | 650 | 9 USD | about 640 USD |
| 3 USD | 650 | 9 USD | about 1,940 USD |
| 5 USD | 650 | 9 USD | about 3,240 USD |

In people's time, at the `Transaccional` median handle time, 650 resolved contacts are about 37 hours a person doesn't spend on lookups. At this bank's size, if every chat contact were a card request (they aren't; the data can't say how many are), that is about 42 resolved a day and 2.4 hours a day.

**What would change it.** The request mix that reaches the chat is unknown and ours is a design, not a measurement; a mix heavier in blocks meets the misread above and resolves less, a mix heavier in reads resolves more. The saving per contact is linear in the parameter, so the table is the whole sensitivity. The D-013 patch, made after the freeze, would move M-01 toward 75% on this mix; it is verified on the affected cases, not re-measured on the set. And a chat that resolves a contact which a person would have resolved in a different, better way isn't a saving; M-04 and M-03 are what bound that risk, not this table.

## What we would do next

In the order the findings point:

1. **A fresh held-out answer pool.** The post-freeze patch stops the re-ask when a reason came back (D-013); what remains is the set's design. Draw a held-out answer pool with several phrasings per kind, so one misread can't take a whole situation, and measure the patched agent on it.
2. **The router on intent, not words.** The eight single misreads (D-014) and the selection play's five (D-010) are the same pattern: a label from the message's words. Few-shot examples of the policy's intent per label, chosen from development families only, then the router comparison again.
3. **A language read from the conversation,** not the message alone, for words both languages share (D-012; the live language check reads 2 of 10 shared-word messages as unclear).
4. **AWS charges in M-05,** from the Runtime's, the Gateway's, Lambda's, and DynamoDB's metered usage per run.
5. **The model grid** ADR-0005 designed and didn't run, now that the harness, the judge, and the sets exist: three models on the selection set, ranked on unsafe outcomes, then M-01, then cost.
6. **A second grader on the blind sample,** to settle whether the 24 seeded replies our grader passed and the judge failed were broken as intended, and a person's read of the 51 held-out cases the judge flagged on `no_verdict`; then the judge's validation scored again on the same verdicts, with no re-prompting, and the relabel of 50 messages (EVL-08) that time left undone.

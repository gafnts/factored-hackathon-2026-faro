# Limitations, production readiness, and remaining risks

What Faro can't claim, what the data and the evaluation leave out, what a bank would still have to build, and the risks we accepted to ship a prototype in ten days (SCP-07, SCP-08, OPS-11). The [evaluation report](report.md) holds the results this page qualifies; the [product brief](../product/brief.md#out-of-scope) holds what Faro doesn't do by design.

## The data (SCP-07)

- **The bank is synthetic, and it behaves like one.** Contacts can't be tied to a workflow or a customer's request, so no supplied label says what customers ask about cards; the workflow was chosen for being feasible on this data, not for being in demand ([ADR-0003](../adr/0003-choose-workflow-from-evidence.md)). About half of all active cards are past their expiration date and still transact, and about 5% of each core field is missing at random; the policy treats both as facts to report, not errors to fix ([profiling report](../analysis/profiling.md)).
- **No customer in the data writes Portuguese.** Every Portuguese message in the evaluation was written by us or paraphrased by a model from our seeds, and so was every Spanish one. Results per language say how Faro handles our phrasing in each language, not how it would handle customers' ([families.md](families.md)).
- **The contact-center baseline is context, not a comparison.** Handle times, resolution, and escalation per reason category come from contacts that can't be tied to a card request, so the ROI uses them as brackets and is labeled a projection ([selection report](../analysis/selection.md#contact-center-baseline)).
- **Recent transactions are a card's last 90 days, ten at a time,** with no filter by period, merchant, or amount. Enough for this bank, where 99% of cards with a transaction in that window have four or fewer, and not a statement.
- **The dataset can no longer be downloaded.** It was available through the hackathon, judging included; since late October 2026 it isn't, and it isn't ours to share. Our numbers stand as recorded: `dataset.lock` pins every file by its SHA-256, and each run's manifest names its commit, snapshot, and pipeline version. Without a copy, though, someone outside the project can rerun only the checks that run on team-written fixtures, such as `make regression`; the pipeline, the analysis, the eval sets, and a stack of their own need one ([CONTRIBUTING](../../CONTRIBUTING.md#2-restore-the-dataset), [ADR-0002](../adr/0002-mirror-dataset-into-pinned-snapshots.md)).

## Language coverage (SCP-07)

- Faro serves Spanish and Brazilian Portuguese and declines a third language in both (POL-50, POL-51). It reads the language from the customer's latest message alone. A message made of words both languages share ("bloquear", "humano") is the hard case: the live language check reads 2 of 10 such development messages as unclear and the rest as one language, and three held-out cases were answered in the other language (D-012). A read from the conversation, not the message, is the next step.
- Mixed messages (a Spanish request with a Portuguese word for the card) are decided by which language most words are in (POL-50); the multilingual group measures it, at 85% resolved on 20 cases.
- The form of address ("usted", "você") is checked by the judge, not by code, and the judge didn't meet the validation bar on that question. What we have is our blind grades on 60 natural selection replies (2 off) and the judge's held-out answers, unused.

## What the evaluation can't say

- **The cases are ours.** 108 request families, seeds by us and paraphrases by Claude Opus 5.5, with a third of the families and a fifth of the customers held out. The held-out side measures generalization to phrasing we didn't tune on, not to customers' phrasing. 134 of the 608 held-out cases borrow development phrasing where no held-out family fits, shown apart; 36 share a customer across the two languages; two situations are short at seven of eight per language.
- **One phrasing per answer kind on the held-out side.** The scripted customer's answers to the chat's questions (which card, a reason, which transaction) have one held-out phrasing each, so one misread hits every case of its kind. That is what happened with the reason answer (D-013, 56 cases; fixed in code after the freeze, below). The design should have held several.
- **The judge is from the same family as the system's model** and as the author of the paraphrases: Claude Opus 5.5 judging Claude Haiku 4.5's replies, since the other providers' keys weren't obtained. The validation against our blind grades is the mitigation, and a question that misses the bar isn't used: one of nine met it. Our grader passed 24 of the 66 seeded replies the judge failed, so six questions fell short of ten replies that deserve a no, and one grader can't say which side was right. The hand grading ADR-0005 prescribes for a question under the bar was done on the sample's 60 natural replies, not on the 3,820 held-out replies.
- **The model configuration wasn't selected by measurement.** Claude Haiku 4.5 serves every model node as a time decision; the grid over three models that ADR-0005 designed wasn't run. The router comparison is the measured evidence on models, on one node, within one family: it measures size, not family.
- **Label quality (EVL-08) wasn't measured.** The relabel sheet of 50 messages was drawn and not graded, for time; the labels are the families', checked against the policy when written and never blind.
- **One change to the agent's code was made after the freeze.** D-013's re-ask was fixed in code on 2026-10-04, with no prompt changed, and verified on a separate stack on the 56 affected held-out cases (55 pass, 1 not played for a Runtime error and passing when replayed) and on the selection set (146 of 157, against 147 at the freeze, the one difference outside the block flow). The held-out set wasn't re-run: those cases prompted the fix, so a new number would be measured on them. The numbers in results.md are the frozen code's (app `8aa77fa`); the prototype runs the patched code.
- **M-05 counts model tokens only.** AWS charges (the Runtime, the Gateway, Lambda, DynamoDB) aren't estimated, and there is no per-segment breakdown of cost or latency.
- **M-04 counts materially incorrect outcomes beside disclosures and actions,** so it reads above zero while no card was blocked wrongly and no data left the customer's own records; the five cases it holds are facts in the other language or in another format (D-015). Zero disclosures in 608 cases bounds the rate at 0.6% and doesn't establish zero risk.
- **The grader's own gaps.** A handoff's verified facts are checked against the oracle's but not each traced to a tool result; the forbidden-tool check covers the two write tools only; one expired-session situation of the table's four is played; the withheld-status check is blind when a card shares its customer's status; the oracle expects no answer about every card's credit, so the credit list is covered by unit tests and the reply check alone; and a sentence of the model's own that states no figure is read by no check.
- **The live language check and the "by place" card read** (an answer that names a card by its position in the list, 8 of 8) were measured on the development answers the prompts were tuned on.
- **A conversation resumed after the Runtime's 15-minute idle timeout** is documented by AWS and not exercised by any case.

## Designed and not built

Each is a stated gap, with where its design lives:

- **A handoff filed after a confirmation's deadline** when the customer has left (the deadline Lambda; [ADR-0004](../adr/0004-agent-architecture-on-agentcore.md)). Today a lapsed confirmation is handled on the customer's next message.
- **Claim and resolve in the agent console**, and the operations page (`/ops`); the console is a read-only queue ([ADR-0007](../adr/0007-role-gated-web-app.md#the-console-api)).
- **A stop button** for a reply in progress.
- **Earlier conversations are out of the customer's reach** after a new one or a reload ([ADR-0007](../adr/0007-role-gated-web-app.md#routes)), and a draft typed while a control shows isn't kept anywhere.
- **The naive agent** baseline, the outside message set, a small model (Jev) as a judge, and the model grid, all dropped for time (ADR-0005, Baselines).

## Production readiness and remaining work (SCP-08, OPS-11)

Faro is a prototype on a frozen snapshot, deployed end to end on AWS (AgentCore Runtime and Gateway, Lambda, DynamoDB, Cognito, Cedar) with a web app, an evaluation harness, and alarms. [ADR-0007's table](../adr/0007-role-gated-web-app.md#in-a-bank-ops-11) lists what a bank replaces: its own customer and workforce identity, a backend for the frontend holding tokens, its case system in place of our queue, a live transfer to a person, and its observability stack. Beyond that table:

- **The tools mock the bank's systems of record** over the snapshot and write to a sandbox. What each stands in for, and what replacing it takes, is in [ADR-0004](../adr/0004-agent-architecture-on-agentcore.md#the-tools-as-the-seam-to-the-banks-systems); the policy's facts and the handoff schema don't change with it.
- **The graph's typing** is the first refactor a real codebase needs: one 2,900-line module, a `dict[str, Any]` state, and case strings where types belong.
- **Quotas.** The account's applied Bedrock quota, not a documented limit, binds first under this bank's traffic; raising it and deciding what the agent does when throttled are decisions 17 to 19 of ADR-0004.
- **One alarm's threshold.** The fallback-share alarm fires at 20% of checked replies over an hour, a figure with no data behind it; it is to be set from a measured share ([ADR-0004](../adr/0004-agent-architecture-on-agentcore.md#operations)).
- **The findings above.** D-013 was fixed in code after the freeze; D-010 to D-012, D-014, and D-015 are the report's [next steps](report.md#what-we-would-do-next).

## Risks accepted in the scaffold (OPS-11)

Each was weighed and left as it is for a solo ten-day build; none is hidden:

- Both environments (`local`, `prototype`) share one AWS account, with partial tag isolation; the deploy roles carry `PowerUserAccess`; the evaluation harness's role trusts the account root.
- The Runtime runs with `network_mode = PUBLIC`; the evaluation, runtime, and site buckets have `force_destroy`; the secrets' recovery window is zero days, so a deleted secret is gone.
- CI applies Terraform with no smoke test after the apply; five trivy findings are ignored, each marked inline in the Terraform with its rule.
- The four `*.tfbackend` files hold AWS account IDs: identifiers, not credentials, and accepted as such.
- No single source for tool versions (Terraform's version in two places; tflint and trivy only in CI; uv unpinned); pre-commit hooks pinned by tag, not SHA; no zizmor linting of the workflows.
- The full setup has run from one account only; a reproduction from a fork in a fresh account is untested.
- The repository's history was scanned for secrets, and secret scanning and push protection are on, before it went public.

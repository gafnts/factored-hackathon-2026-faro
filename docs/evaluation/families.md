# Request families

> [!IMPORTANT]
> **Team-generated (SEC-02).** Every message here was written for this evaluation: the seeds by us, the paraphrases by a model. No customer wrote any of them, and the snapshot holds no real request in any language ([ADR-0005](../adr/0005-offline-scenario-evaluation.md), Negative).

The families are the requests the evaluation's scripted customer sends ([ADR-0005](../adr/0005-offline-scenario-evaluation.md), decision 2, as amended on 2026-10-01). They are the wording source `ours`, all on the development side since 2026-11-02: held-out wording comes from another source ([ADR-0011](../adr/0011-pre-registered-studies.md#wording-sources)). They live in [`src/faro/evaluation/families/`](../../src/faro/evaluation/families/), one file per label of the [policy's request table](../policy/card-support.md#requests) and one, `none`, for messages that hold no card request.

## What a family is

One request, written in Spanish and in Portuguese: a seed in each language, then about five paraphrases of it. A family carries:

- its **labels** in POL-05's order (two for a message with two requests; none in `none`), and, for `talk_to_human`, whether it is a **complaint** (POL-44's two reason codes);
- its **kind**: `plain`, `terse`, `indirect`, `access` (someone else's card, or another customer's number), `multi_request`, `injection` (instructions around a request, or alone in `none`), `mixed_language` (filed under the language it mostly is), `follow_up` (the next page, sent only after a first one), `no_request`, or `third_language` (English or French, for POL-51);
- what the agent's **extraction** should read from it, in the graph's own fields (`card_type`, `block_reason`, `cards`, `page`, `owner`, `conflict`, `service`; `block_reason` holds POL-35's code, and the scripted models say the model's name for any other reason, `other_reason`, which the graph maps back), and the **relative date** it names, among those the oracle resolves from the business date (today, yesterday, the day before);
- its **slots**: `{last_four}`, `{merchant}`, `{amount}`, `{date}`, `{other_customer_id}`. The generator fills them from a case's customer, so no family holds an identifier or a value from the records (SEC-03);
- which of its paraphrases aren't **clearly in one language** (`unclear`, by position in each language's list): a bare word both languages share, such as `bloquear`, `humano`, or `operador`. The scripted models say `unclear` for them, the oracle keeps the conversation's language for the turn they open (POL-50), and the live language check expects `unclear` from the model for them.

`answers.json` holds what the customer says when the agent asks: which card, a reason, which listed transaction, that it doesn't know, a message that doesn't answer (a greeting, thanks, or what else the chat does, POL-06), and a typed yes (which never confirms a block, POL-36). Since 2026-11-02 it also holds what customers sent on the prototype and our sets never did, as the policy's version 7 reads it: frustration with the exchange, a question about the last reply (POL-06), and a correction of the card at a question about a transaction, naming the card or saying only that it's another one (POL-13). Three phrasings per kind, in both languages.

108 families (12 per label and 12 in `none`) and 57 answers (19 kinds), 1,284 family messages in all.

## How they were written

1. **Seeds, by us.** For each label we chose twelve variations on what the policy reads (which hint the message gives, register and length, a terse and an indirect form, two requests at once, an injection, mixed languages) and wrote one request for each in both languages.
2. **Paraphrases, by Claude Opus 5.5,** from each seed, in one authoring session on 2026-10-01, with these instructions: keep the request, its labels, and its slots exactly; vary wording, register, length, and regional words (Colombian, Mexican, and Argentine Spanish; Brazilian Portuguese); mostly usted in Spanish, with some tú, as customers write, and você in Portuguese; write no digit, name, or identifier outside a slot; and keep each paraphrase a message a customer would send in a chat.
3. **Checks on every message** (`faro.evaluation.families.problems`, which the tests run): its slots are exactly the family's; no digit, brace, or identifier sits outside a slot; it reads as the language it's filed under (mixed, terse, third-language, and marked messages excepted); it repeats no other message of its family; no held-out message has a character 3-gram Jaccard similarity of 0.6 or more with any development message, ADR-0005's bar for a message too close to count as unseen; and no two messages of a family, or two answers of a kind, reach that bar between them, or 0.7 when either has four words or fewer, so several phrasings are several ([ADR-0011](../adr/0011-pre-registered-studies.md#wording-sources)). Several drafts failed the check against the other side, and one of the two messages was reworded each time. When the check within a family was added, on 2026-11-02, three pairs of ours failed it (in `available_credit-04` and `recent_transactions-09`), and the later message of each was reworded. The same day, POL-50's reading of a loanword and of spelling marked one terse status request unclear and reworded one mixed block request that held as many words of each language (D-016).
4. **By hand,** before any case uses them: the seeds, and a sample of paraphrases, one family per label.

Each message names its author: the first in each language is the seed (`team`), the rest are paraphrases (`claude-opus-5-5`).

## The split

Every family and answer here is development wording. Held-out wording is another source's, written by another model family from a guide we write, so it's neither our wording nor our model family's ([ADR-0011](../adr/0011-pre-registered-studies.md#wording-sources), [the outside source](outside.md)). Its messages pass the checks above against these, and a test, run where that source is kept, checks that no development artifact (cases, prompts, the baseline's keywords, the router's data) holds one of its messages ([split guards](../adr/0005-offline-scenario-evaluation.md#the-split)).

Until 2026-11-02, a third of these were held out: within each label and each kind of answer, the third whose IDs had the lowest MD5, so four families per label, 36 in all, and one answer per kind. The first held-out set, `held_out`, was drawn under that rule, and the commit that drew it rebuilds it. The rule took hashes, not shapes, so it left fifteen situations with no held-out family, put the one follow-up family and all three `access` families (someone else's card, another customer's number) on one side, and gave each kind of answer a single held-out phrasing, which one misread could fail throughout (D-013).

## Limits

- **All of it is ours.** Our phrasing may be easier than customers' would be; the router comparison's results describe these families, not real traffic (EVL-13).
- **A model's paraphrases.** Claude Opus 5.5 is an Anthropic model, and so are the router comparison's LLM candidates, Claude Haiku 4.5 and Claude Sonnet 5.5: the bias favors neither over the other, but may favor both over the keyword router. The report says so beside the comparison (DML-11).
- **One follow-up family.** The next page has a single family here; a case's follow-up comes from its own side, so a held-out source writes its own.
- **Label quality** of these wasn't measured. The blind relabel of 50 messages was drawn and not graded, for time ([report](report.md#label-quality), DML-08); it's now drawn from a held-out source before any set uses it ([ADR-0011](../adr/0011-pre-registered-studies.md#wording-sources)).

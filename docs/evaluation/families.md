# Request families

> [!IMPORTANT]
> **Team-generated (SEC-02).** Every message here was written for this evaluation: the seeds by us, the paraphrases by a model. No customer wrote any of them, and the snapshot holds no real request in any language ([ADR-0005](../adr/0005-offline-scenario-evaluation.md), Negative).

The families are the requests the evaluation's scripted customer sends ([ADR-0005](../adr/0005-offline-scenario-evaluation.md), decision 2, as amended on 2026-10-01). They live in [`src/faro/evaluation/families/`](../../src/faro/evaluation/families/), one file per label of the [policy's request table](../policy/card-support.md#requests) and one, `none`, for messages that hold no card request.

## What a family is

One request, written in Spanish and in Portuguese: a seed in each language, then about five paraphrases of it. A family carries:

- its **labels** in POL-05's order (two for a message with two requests; none in `none`), and, for `talk_to_human`, whether it is a **complaint** (POL-44's two reason codes);
- its **kind**: `plain`, `terse`, `indirect`, `access` (someone else's card, or another customer's number), `multi_request`, `injection` (instructions around a request, or alone in `none`), `mixed_language` (filed under the language it mostly is), `follow_up` (the next page, sent only after a first one), `no_request`, or `third_language` (English or French, for POL-51);
- what the agent's **extraction** should read from it, in the graph's own fields (`card_type`, `block_reason`, `cards`, `page`, `owner`, `conflict`, `service`; `block_reason` holds POL-35's code, and the scripted models say the model's name for any other reason, `other_reason`, which the graph maps back), and the **relative date** it names, among those the oracle resolves from the business date (today, yesterday, the day before);
- its **slots**: `{last_four}`, `{merchant}`, `{amount}`, `{date}`, `{other_customer_id}`. The generator fills them from a case's customer, so no family holds an identifier or a value from the records (SEC-03);
- which of its paraphrases aren't **clearly in one language** (`unclear`, by position in each language's list): a bare word both languages share, such as `bloquear`, `humano`, or `operador`. The scripted models say `unclear` for them, the oracle keeps the conversation's language for the turn they open (POL-50), and the live language check expects `unclear` from the model for them.

`answers.json` holds what the customer says when the agent asks: which card, a reason, which listed transaction, that it doesn't know, a message that doesn't answer (a greeting, thanks, or what else the chat does, POL-06), and a typed yes (which never confirms a block, POL-36). Three phrasings per kind, in both languages.

108 families (12 per label and 12 in `none`) and 39 answers, 1,284 family messages in all.

## How they were written

1. **Seeds, by us.** For each label we chose twelve variations on what the policy reads (which hint the message gives, register and length, a terse and an indirect form, two requests at once, an injection, mixed languages) and wrote one request for each in both languages.
2. **Paraphrases, by Claude Opus 5.5,** from each seed, in one authoring session on 2026-10-01, with these instructions: keep the request, its labels, and its slots exactly; vary wording, register, length, and regional words (Colombian, Mexican, and Argentine Spanish; Brazilian Portuguese); mostly usted in Spanish, with some tú, as customers write, and você in Portuguese; write no digit, name, or identifier outside a slot; and keep each paraphrase a message a customer would send in a chat.
3. **Checks on every message** (`faro.evaluation.families.problems`, which the tests run): its slots are exactly the family's; no digit, brace, or identifier sits outside a slot; it reads as the language it's filed under (mixed, terse, third-language, and marked messages excepted); it repeats no other message of its family; and no held-out message has a character 3-gram Jaccard similarity of 0.6 or more with any development message, ADR-0005's bar for a message too close to count as unseen. Several drafts failed the last check, and one of the two messages was reworded each time.
4. **By hand,** before any case uses them: the seeds, and a sample of paraphrases, one family per label.

Each message names its author: the first in each language is the seed (`team`), the rest are paraphrases (`claude-opus-5-5`).

## The split

Within each label, and within each kind of answer, the third whose IDs have the lowest MD5 is held out (`split.held_out_families`): four families per label, 36 in all, and one answer per kind. The rule is shared code, like the split by customer, so no one chooses which families are held out. Development cases, prompts, the baseline's keywords, and the router's tuning use development families only, and a test checks that no development artifact holds a held-out message ([split guards](../adr/0005-offline-scenario-evaluation.md#the-split)).

## Limits

- **All of it is ours.** Our phrasing may be easier than customers' would be; the router comparison's results describe these families, not real traffic (EVL-13).
- **A model's paraphrases.** Claude Opus 5.5 is an Anthropic model, and so are the router comparison's LLM candidates, Claude Haiku 4.5 and Claude Sonnet 5.5: the bias favors neither over the other, but may favor both over the keyword router. The report says so beside the comparison (DML-11).
- **One follow-up family.** The next page has a single family, which the split put on the development side, so held-out cases can't ask for a next page in held-out words.
- **No development access family.** The split put all three `access` families (someone else's card, another customer's number) on the held-out side, so no development case asks in words about another customer's records; the harness's access cases (another customer's thread or session, a tool called directly) cover access on the development side.
- **Label quality** wasn't measured. The blind relabel of 50 messages was drawn and not graded, for time ([report](report.md#label-quality), DML-08).

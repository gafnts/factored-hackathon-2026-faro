# Tests

The tests check what must hold on every turn: who may read what, what only a confirmation may change, and what a reply or a tool may carry. How often the agent gets a conversation right is a separate question, which the [evaluation](../docs/evaluation/report.md) measures. Each test cites the requirement IDs it covers ([prerequisites.md](../docs/prerequisites.md)) in its docstring, and [CONTRIBUTING.md](../CONTRIBUTING.md#run-the-quality-gates) says how to run each suite.

## The suites

Counts as of 2026-10-04.

| Suite | Where | Runs | Checks |
|---|---|---|---|
| Unit: 1,852 | `faro/`, mirroring `src/` | On every push and in CI, with an 80% coverage floor | Each module with every outside service replaced: a scripted model, a clock the tests move, in-memory stores, a mocked Gateway, and AWS mocked by moto. The graph's branches, the reply check, the confirmation, the handoff, the tools, the contracts against the policy, the pipeline on team-written [fixtures](fixtures/team-generated/README.md), and the evaluation's oracle and grader |
| Web: 125 | [`web/tests/`](../web/tests/), mirroring `web/src/` | On every push and in CI, with an 80% coverage floor | The chat with its frames and controls, the console, sign-in per role, the AG-UI client, and the generated contract types against their source |
| Regression set: 71 cases | `faro/evaluation/test_regression.py` | In CI | The regression set played in process with scripted models on a team-written bank, and graded; any safety finding fails it |
| Pipeline checks: 287 | [`pipeline/`](../pipeline/README.md), its models and `tests/` | In every build | Nulls, keys, references, dictionary values, the delivery against its lock, and the 90-day window. On the build `prototype` serves, every blocking check passed, and 19 warnings count what the delivered data holds, such as cards active past their expiration, which silver flags and never corrects ([manifest](../docs/pipeline/)) |
| Integration: 104 | `integration/` | `make integration`, against a deployed stack | The boundaries on the real services (Cognito, the Runtime, the Gateway and Cedar, the Lambdas, DynamoDB, the site), with throwaway users |
| Browser: 13 | `integration/test_browser.py` | `make browser`, against a deployed site | The chat and the console in Chromium, as a customer and a human agent use them in two tabs, the README's journey included |
| Smoke: 1 | `smoke/` | `make smoke`, against a deployed site; in CI after each deploy to `prototype` | The smoke user signs in through the form, reads its cards through the Gateway, and gets a reply that names each one, with no AWS credentials ([ADR-0009](../docs/adr/0009-release-checked-deploys.md)) |
| Live language check | `make language-check` | By hand; it calls the model, so it costs money | The model's reading of each message's language and of a block's reason, on every development paraphrase ([results](../docs/evaluation/language.md)) |

## What a judge might try

Each row is an integration test against a deployed stack, with real tokens; none relies on the model behaving.

| Try | What happens | Test |
|---|---|---|
| Ask the chat for another customer's cards by their number | The reply names none of their cards, and every tool call names the signed-in customer | [test_browser.py](integration/test_browser.py): `test_another_customers_id_in_a_message_reads_nothing_of_theirs` |
| Call a tool directly with another customer's ID, or another sign-in's | Cedar denies it; `file_handoff`, off the Gateway, refuses it too | [test_stack.py](integration/test_stack.py): `test_cedar_denies_another_customers_id`, `test_cedar_denies_another_sign_ins_origin_jti`; [test_handoff.py](integration/test_handoff.py): `test_file_handoff_refuses_another_customers_id_or_sign_in` |
| Read another customer's card by its ID | It reads as a card that doesn't exist, with no transactions | [test_stack.py](integration/test_stack.py): `test_another_customers_card_reads_as_a_card_that_doesnt_exist`, `test_another_customers_card_has_no_transactions_to_read` |
| Change your own `customer_id` in Cognito | Refused; the stored value stays | [test_stack.py](integration/test_stack.py): `test_a_customer_cant_rewrite_their_customer_id` |
| Send the chat a request beyond its contract, such as a state naming another customer | Nothing changes, and the turn is recorded | [test_agent.py](integration/test_agent.py): `test_a_request_beyond_the_contract_changes_nothing_and_is_recorded` |
| Open another customer's thread or runtime session | Nothing comes back | [test_agent.py](integration/test_agent.py): `test_another_customers_thread_and_runtime_session_get_nothing`; [test_browser.py](integration/test_browser.py): `test_another_sign_ins_runtime_session_is_refused` |
| Use a staff token, or one without a customer ID, on the chat's side | The Runtime and the Gateway turn it away | [test_agent.py](integration/test_agent.py): `test_a_token_without_a_customer_id_is_refused_and_recorded`; [test_stack.py](integration/test_stack.py): `test_the_runtime_turns_away_a_missing_id_or_staff_token`, `test_the_gateway_turns_away_a_missing_id_or_staff_token` |
| Sign in on the other role's page | No sign-in: each role has its own app client | [test_browser.py](integration/test_browser.py): `test_a_staff_user_gets_no_sign_in_through_the_chat`, `test_a_customer_gets_no_sign_in_through_the_console`; [test_stack.py](integration/test_stack.py): `test_a_user_gets_no_token_from_the_other_sides_client` |
| Reuse a sign-in after signing out, or a staff token past its 15 minutes | The refresh is refused, and the console turns the expired token away | [test_browser.py](integration/test_browser.py): `test_signing_out_revokes_the_sign_in`; [test_console.py](integration/test_console.py): `test_an_expired_staff_token_is_turned_away` (with `SLOW=1`) |
| Put instructions to the model in a message | The tools it names are never called and nothing leaks; the turn is served and recorded | [test_agent.py](integration/test_agent.py): `test_an_injected_instruction_in_a_message_reaches_no_tool_it_names` |
| Type "yes" instead of pressing the control | Nothing is confirmed, and the control shows again | [test_block.py](integration/test_block.py): `test_a_typed_yes_shows_the_control_again_and_the_cancel_ends_it`; [test_browser.py](integration/test_browser.py): `test_a_persona_blocks_a_card_with_the_control_not_with_a_typed_yes` |
| Replay an old control, or another sign-in's | Nothing changes; another sign-in's ends the confirmation unused | [test_block.py](integration/test_block.py): `test_a_stale_control_changes_nothing`, `test_another_sign_ins_confirmation_is_refused_and_ends` |
| Call `block_card` without a confirmation | Nothing is blocked | [test_block.py](integration/test_block.py): `test_block_card_through_the_gateway_blocks_nothing_without_a_confirmation` |
| Type a full card number | It is masked to its last four digits before anything stores or logs it | [test_agent.py](integration/test_agent.py): `test_a_typed_card_number_is_masked_and_never_logged` |
| Look for the fraud flag or a full card number in the tools' answers | No read carries `is_fraud`, a full card number, or the fields the policy withholds (the customer's status, the balance, the limit) | [test_stack.py](integration/test_stack.py): `test_no_output_carries_what_the_customer_mustnt_see` |
| Put markup in a case's text | The console shows it as text | [test_browser.py](integration/test_browser.py): `test_the_console_shows_what_a_case_holds_as_text_never_as_markup` |
| Flood the chat | Past 10 turns a minute per sign-in or 500 a day per user, a turn is refused before it opens, and recorded | [test_resilience.py](integration/test_resilience.py): `test_a_sign_in_past_its_rate_is_refused_and_recorded`, `test_a_user_past_the_days_cap_is_refused_and_recorded` |

## Covered elsewhere

- **How often each of these goes right in conversation.** The held-out set plays 48 prompt-injection cases, 4 unauthorized-access cases, and 2 expired sessions, a message sent after the token expired, which the Runtime refuses ([report](../docs/evaluation/report.md)).
- **The model's reading of language and reasons.** The scripted models can't test it, so the live language check does.
- **Load.** There is no load test; the [limitations](../docs/evaluation/limitations.md) name the quota that binds first.

## How the tests behave

- Assertions count and compare. None prints a card number, a customer's text, a token, or an ID, and `make integration` keeps tracebacks short, since a long one prints a failing helper's arguments.
- Integration and browser tests create throwaway users and delete them, with the drafts and cases their conversations leave.
- The browser suite saves no trace, screenshot, or video.
- Every fixture is team-written; no organizer data is in the repository (SEC-02).

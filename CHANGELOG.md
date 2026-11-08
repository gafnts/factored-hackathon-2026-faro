# Changelog

What changed in the prototype, one section per release, newest first. A release is a promotion from `develop` to `main`, tagged once its deploy is checked ([ADR-0009](docs/adr/0009-release-checked-deploys.md)): a minor version for each promotion, a patch for one that only fixes a release, and a major version for a new agent, measured on a fresh held-out set and released with its report. `Unreleased` is what's merged into `develop` and not in a release yet. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

History before 2026-10-27 is in the [merged pull requests](https://github.com/gafnts/factored-hackathon-2026-faro/pulls?q=is%3Apr+is%3Amerged). Work we mean to do later is in [GitHub Issues](https://github.com/gafnts/factored-hackathon-2026-faro/issues).

## Unreleased

### Added

- `make suites`, which runs the browser suite and then the integration suite against an environment: the full check of a promotion that changes the app, now a step of CONTRIBUTING's promotion. With `ENV=prototype`, the Makefile refuses the `local` profile, which can't reach it ([#193]).
- A role of the suites' own in each environment, which they assume from the stack's outputs with credentials refreshed as they go, and which grants what their tests do and nothing more; before, they ran as whoever started them, `local`'s deploy role or an administrator on `prototype`, and a run on exported keys failed once they expired. The tests that read role policies run as the caller, since the boundary every role of the stack carries excludes IAM ([ADR-0004](docs/adr/0004-agent-architecture-on-agentcore.md)'s amendment of 2026-11-07, [#193]).
- A staff allowlist: the staff client issues a token only to a user with an entry in Parameter Store, named by the user's `sub`, and refuses anyone in `visitor`, so a guest entry gone wrong can't make staff. `make judges` and the suites put the staff they make on it, and `make staff` puts one on or takes one off. The pre-token trigger's zip now carries the SDK its group locks, to read it. Parameter Store joins S3 and CloudFront in the deny by name between environments, an IAM root change ([ADR-0004](docs/adr/0004-agent-architecture-on-agentcore.md)'s amendment of 2026-11-07, [#195]).
- `make label-users`, run once per environment for the users made before the labels: it labels each customer with none `invited`, moves the smoke user to `evaluation`, and puts every staff user on the allowlist. It refuses a pool that holds a visitor ([#195]).
- A cap on the day's turns of invited users and visitors together, since a limit per user doesn't bound how many users there are once anyone can enter. It starts at 750, about 5 USD a day at the freeze's 95th percentile cost of a turn, a tenth of the model's monthly limit; the evaluation's turns and the smoke check's don't count. A turn past it gets `global_limit`, and the `cap-reached` alarm fires. Terraform writes the cap once, and `make cap` reads and sets it, so a deploy never undoes a change; 0 pauses the chat, with its own fixed text (`paused`). The Runtime reads the cap and can't write it, and no test can: the suites' role now writes users' counters alone ([ADR-0004](docs/adr/0004-agent-architecture-on-agentcore.md)'s amendment of 2026-11-07, [#201]).
- A bar at the foot of the customers' sign-in that says what we keep of a visit, and that entering means accepting it: conversations up to 12 months, which we may read, with the full list in a dialog (Anthropic processes the messages, no one follows up on the cases Faro files, and the IP address is kept up to 90 days). It takes the synthetic-data label's place there; the console's sign-in keeps the label ([ADR-0007](docs/adr/0007-role-gated-web-app.md)'s amendment of 2026-11-07, [#202]).
- The site's access logs: CloudFront delivers a line per request to a log group of the stack, kept 30 days, with the client's IP address and the referrer, and never the path, the query string, or cookies. The suites' role may read which fields the delivery keeps ([ADR-0004](docs/adr/0004-agent-architecture-on-agentcore.md)'s amendment of 2026-11-07, [#202]).
- `SECURITY.md`: how to report a vulnerability privately, what's in scope, and how to test against the prototype ([#202]).

### Changed

- Each environment keeps its own Terraform working directory, `infra/.terraform/<env>`, so `ENV=prototype` targets that only read, such as `make outputs`, run without re-initializing `local`'s. A checkout initialized before needs `make init` once ([#193]).
- Customers carry a label that sets their source, and a missing one fails closed: `invited` customers are recorded as `demo`, and any customer with no label as a `visitor`, where it was `demo`, so a visitor's case never reaches the human agent's queue. `make judges` labels its customers `invited`, the smoke user moves to `evaluation`, and the alarms on the agent's own failures read visitors' turns beside invited users' ([ADR-0007](docs/adr/0007-role-gated-web-app.md)'s amendment of 2026-11-07, [#195]).
- A major release is a new agent, measured on a held-out set no earlier agent was measured on and released with its report, where it was a change in who can use the prototype. A changed agent waits on `develop` until it's measured, and a fix that can't wait is promoted as an exception, with notes that say the agent isn't measured yet ([ADR-0009](docs/adr/0009-release-checked-deploys.md)'s amendment of 2026-11-07, [#194]).
- The second study opens Faro before its play, on the agent the submission's report measured, and plays the iterated agent on `local` before its release, as its Record says; its registered text and hash are unchanged ([held-out-2.md](docs/evaluation/studies/held-out-2.md), [#194]).
- Each user's turns a day fall from 500 to 40, about a twentieth of the day's cap, so one visitor who keeps going stops well before the day does. The busiest of the suites' users takes at most 22 in a run ([#201]).
- The chat's text for a user past the day's limit gives the time the UTC day ends in the browser's own time, where it said "tomorrow", which was wrong in the Americas from the late afternoon ([#201]).
- A model call that fails for a cause that fails every turn until someone acts (a spend limit, the key, the model's ID, or the request we build) gets a fixed reply of its own, `out_of_service`, which promises no time, where `unavailable` asked to try again in a few minutes; the handoff offer still follows. No case the report measured meets those causes, and the agent's other replies are unchanged ([#201]).
- A turn whose count meets another turn's at the same moment is counted again, up to three times, where it failed; with every turn of invited users and visitors now writing the day's count, such meetings become likelier ([#201]).
- The agent's Python dependencies: AgentCore 1.25.2, LangGraph 1.2.19 and boto3 1.43.123. The Runtime, the pre-token Lambda and the five Lambdas built from `tools.zip` redeploy; the Runtime's stamp stays, since its own code didn't change. `boto3[crt]` now requires 1.43.123, the version locked ([#203]).
- The test and build tools: boto3-stubs 1.43.123 and types-PyYAML 6.0.12.20261030; ESLint 10.14.0 in the web app's tooling; mise-action 5.2.5 in CI and pre-commit-terraform 1.109.4 in the hooks ([#203]).

## [1.11.0] - 2026-11-07 (Promotion 17)

### Added

- An alarm, `model-failing`, for a model call that fails for a cause that fails every turn until someone acts: a spend limit reached, a rejected key, a model ID the API doesn't know, or a request it refuses. It counts every source, since visitors and the evaluation share the model's workspace ([#174]).

### Changed

- A model call that fails for a reason no retry fixes is named in the execution record by what a person has to do (`spend_limit`, `key_rejected`, `model_not_found`, `invalid_request`), where every 4xx but a 429 was `error`, and a model's refusal is `refused`, read before its output. Neither the usage tier's spend cap, a 429 sent with no wait, nor a refusal is tried again ([ADR-0004](docs/adr/0004-agent-architecture-on-agentcore.md)'s amendment of 2026-11-03, [#174]).
- An evaluation run stops at the first model call that fails for a reason no retry fixes, and is kept as void with the cause and what to do before playing again; `make eval-report` and `make eval-compare` refuse it. The language check and the router comparison stop at the same failures and keep nothing ([#174]).
- The Anthropic SDK is held below 2 until no call sends a temperature, since its 2.x sends an API version that refuses one on every model ([#174]).
- The Anthropic SDK 1.17.0, from 1.9.0. Since 1.14 it traces its own calls whenever the process has a tracer, with the attributes our own span for each call already carries, so `tokens-by-turn` would count each call and its tokens twice; it also sends the trace's context to the API. The Runtime turns both off. The API version it sends is unchanged ([#183]).
- The agent's other Python dependencies: AgentCore 1.25.1, langchain-anthropic 1.7.7, LangGraph 1.2.18 and boto3 1.43.121. The Runtime and the five Lambdas built from `tools.zip` redeploy; the pre-token Lambda doesn't. `boto3[crt]` now requires 1.43.121, the version locked ([#183], [#188]).
- The test and build tools: pytest 9.2.0, ruff 0.17.3, mypy 2.4.1, moto 5.2.5, boto3-stubs 1.43.121 and `uv-build` 0.13.6 or later. The web app's assistant-ui 0.15.32 and its AG-UI adapter 0.1.1 (fixes only), and typescript-eslint 8.72.1 in its tooling; mise-action 5.2.4 in CI; and Terraform's AWS provider 6.71.0, which plans no change of its own ([#183], [#188]).
- Dependabot groups the Anthropic SDK and OpenTelemetry with the runtime's updates, and gives the four dependencies it reads no version for (`boto3[crt]`, `boto3-stubs`, `moto` and `uv-build`) a group of their own. Each of those came alone before, so this round had five Python PRs open, Dependabot's limit, and the `boto3-stubs` update never opened. A test keeps both groups in step with `pyproject.toml` ([#183]).
- ADOT waits a week for a new release like every other dependency, since 0.22.0 is now a week old; its exception ended on 2026-11-06. The version locked and the zips don't change ([#190]).

## [1.10.0] - 2026-11-03 (Promotion 16)

### Added

- Studies: each reported measurement has a protocol, committed before its held-out set is drawn, and the set's manifest holds its hash, so the bar provably came first ([ADR-0011](docs/adr/0011-pre-registered-studies.md)). The second study's protocol is drafted, and the submission's measurement is described as the first, after the fact ([#163]).
- `make eval-compare`, which compares two systems' runs of one set case by case, with an interval over families within each language ([#163]).
- Grader checks on what the model writes: a card ending or a date a reply names must be one the conversation read, a handoff's summary must agree with its verified facts, and word lists catch a ruled-out form of address and a fraud verdict or a promised refund ([#163]).
- The evaluation plays what customers sent on the prototype: frustration and a question about the last reply at a question, three such messages that end it, a correction of the card, a charge reported again, and a complaint at a question or while the confirm control shows. Eleven situations in the development sets, now 93 and 179 cases, which the current agent fails until it follows the policy's version 7; their findings are open in the disagreement log. ([#164])
- Grader checks for version 7: one case a turn, a joined case citing POL-44, and no list of what the chat does after an answer that doesn't know, frustration, or a question about the last reply. ([#164])
- The second held-out set, `held_out_2`: 603 cases worded by OpenAI's GPT-6.1 Sol from a guide we wrote, drawn under its registered protocol and sealed until the iteration ends. A blind reading of 50 of its messages agreed with their labels on 44, and none was wrong once the six were settled by the policy's text ([#165]).
- `make outside-write`, which has OpenAI's model write the held-out wording from the guide, and `make outside-keep`, which copies it into the account's data bucket under its hash; the wording stays out of git until the set is played ([#165]).
- A trace of each turn in CloudWatch: the Runtime's own spans for the turn, each node, each attempt at a model call, a tool call, or the handoff's filing, and each checkpoint write, with latency and tokens, under AgentCore's span for the request, in the account's `aws/spans`. A span holds no text and nothing that names the customer: its attributes come from one list, which the exporter enforces. The turn's first record entry names its trace, and three saved Logs Insights queries read a turn's latency, its time by node, and its tokens and cost ([ADR-0004](docs/adr/0004-agent-architecture-on-agentcore.md#operations), [#168]).

### Changed

- A held-out draw takes the customers of the earlier held-out set last, and its manifest counts those it reuses; it shares its size among the situations by largest remainder, so it draws the size asked for; and its manifest names the situations a comparison on it leaves out, which `make eval-compare` reads from there. The relabel sheet's guide states the three rules its first sample missed (POL-10, POL-31, and POL-44's complaint) ([#165]).
- Held-out wording comes from a source another model family writes, kept out of the repository until its first play; every family and answer of ours is development, and the router comparison reads no held-out wording until then. Phrasings within a family or a kind of answer must differ, and three of ours were reworded. Nothing deployed changes ([#163]).
- The card support policy's version 7, from the triage of two conversations on the prototype and of the freeze's open findings: a message at a question that answers nothing gets the question again, not the list of what the chat does, and the third ends it; a correction of the card moves the request; a charge reported again answers the question, and one charge files one case; a complaint joins the turn's required case, and while the confirm control shows it's handed off at once, leaving the block to confirm; a handoff's reply says what the case is about; and four readings made explicit (a card's expiration, the kind of transaction, a complaint, a loanword). The evaluation's oracle follows it; the agent follows after the held-out set is drawn, and still stamps version 6. ([#164])
- `make probe` reads each turn's spans from the trace its record names, and fails if a span holds the typed number, a message, or a reply, if a turn has no trace, or if anything is stored that shouldn't be; before, it read only the share of traces X-Ray indexed and printed what it found ([#168]).
- Transaction Search, set by hand in the console until now, is in the IAM root, with `aws/spans`' retention of 30 days; an account that turned it on by hand imports `aws/spans` once ([CONTRIBUTING.md](CONTRIBUTING.md#traces), [#168]).
- The Runtime's zip carries ADOT 0.22.0 and the OpenTelemetry SDK, 43 MB from 34. ADOT alone is let past the week's wait for new releases until 2026-11-06, since 0.21.0 pins an OpenTelemetry with an advisory (GHSA-xcq7-p3pf-p88h) in the parsing of a header the Runtime reads ([#168]).

### Removed

- The model judge, its rubric, and its validation sample: code grades every reply ([#163]).

## [1.9.0] - 2026-11-01 (Promotion 15)

### Added

- A monthly budget for the AWS account, 40 USD unless `monthly_budget_usd` sets another amount, which notifies a topic when its forecast passes 80% and when the month's charges pass the amount; CONTRIBUTING says how to subscribe to it ([#156]).
- A monthly spend limit with alerts on the project's Anthropic workspace, under a limit for the organization; CONTRIBUTING says where each is set ([#156]).

### Changed

- The agent's graph is a module per flow, under `faro.agent.nodes`, and `graph.py` keeps its overview and wiring; nothing the agent does changed. The next deploy updates the Runtime alone, under a new stamp, since its code moved ([#158]).
- The graph's state is typed: each of its keys has a type of its own, a request's decision has one constructor, and mypy refuses an explicit `Any` in the nodes and the graph. Nothing the agent does changed, and a conversation open during the deploy still reads its checkpoint, since the state is stored as before. The next deploy updates the Runtime alone, under a new stamp ([#159]).
- The agent's nodes write each rule in one place: the replies that end a request come from one table, a request and a turn each start from one named state, and a handoff's block action, the reply about a card that isn't active, and the decision while a confirmation waits each have one builder. Nothing the agent does changed, and a conversation open during the deploy still reads its checkpoint. The next deploy updates the Runtime alone, under a new stamp ([#160]).

## [1.8.0] - 2026-11-01 (Promotion 14)

### Changed

- An AWS account per environment is no longer planned for this project; CONTRIBUTING says why both environments stay in one ([#147]).

## [1.7.0] - 2026-10-31 (Promotion 13)

### Changed

- The agent's Python dependencies: AgentCore 1.25.0, FastAPI 0.162.0 and LangGraph 1.2.17. Only the Runtime redeploys, and its stamp stays, since its own code didn't change ([#132]).
- The test and type-checking dependencies: moto 5.2.4 and boto3-stubs 1.43.118 ([#133], [#135]). `boto3[crt]` now requires 1.43.118, the version already locked ([#134]).

## [1.6.0] - 2026-10-31 (Promotion 12)

### Security

- source-map-js 1.2.2 fixes a denial of service through indexed source maps ([GHSA-68fv-2mgg-jv7q], high), and fast-uri 3.1.9 fixes four flaws in how it reads a URI's scheme, host, and path ([GHSA-w74x-v6f9-j3xg], [GHSA-wjr9-j795-jxcr], moderate; [GHSA-xhq7-xmqj-769h], [GHSA-333x-w4r6-6crp], low). Both come in through the web app's build and test tools, and the site's files are unchanged ([#141]).
- minimatch's denial of service through glob patterns ([GHSA-g3hv-75wg-m249], moderate) is accepted until 2027-01-15: ESLint matches only our own config's patterns with it, and no fixed version fits ESLint's range ([#141]).

### Added

- CI checks `uv.lock` and `web/pnpm-lock.yaml` for known advisories with osv-scanner (`make advisories`) on every PR, every push, and every Monday, and fails on any severity; `main` and `develop` require it. An advisory we accept is listed in `osv-scanner.toml` with its reason and an end date at most 90 days away ([ADR-0010](docs/adr/0010-gate-merges-on-advisories.md)) ([#141]).
- `develop` takes a commit only once the quality gates and the advisory scan have passed on it, so every change, small docs ones included, goes through a PR ([#141]).
- zizmor audits the workflows on every commit that touches `.github/`, and in CI with its online audits too ([#136]).
- Releases ([ADR-0009](docs/adr/0009-release-checked-deploys.md)). A promotion is cut on `develop` first with `make cut`, which numbers it and dates its section, and named last with `make release`, which tags the merge commit once its deploy has passed and makes a GitHub release whose notes list the build's SHA-256. The submission is `1.0.0`, Promotions 7 to 11 are `1.1.0` to `1.5.0`, and `pyproject.toml`'s version follows the last cut ([#139]).
- After each deploy to `prototype`, CI checks that each Lambda and the Runtime run the build's zips, then signs a standing smoke user in through the site and asks about its cards; either failing turns the run red, and a red run gets no release ([#139]).
- A promotion into `main` is refused unless its release is cut ([#139]).
- CodeQL scans the Python, the TypeScript, and the workflows, a free extra on this public repository and not a required check.

### Changed

- The toolchain is pinned by layer ([ADR-0008](docs/adr/0008-pin-toolchain-by-layer.md)). mise installs Python 3.13.17, uv, Node, pnpm, Terraform, tflint, and trivy at the exact versions in `mise.toml`, each download checked against `mise.lock`, on our machines and in CI. Setup now starts with installing mise; `.python-version`, `.nvmrc`, and `.terraform-version` are gone ([#136]).
- CI installs that toolchain through one composite action instead of six setup actions, so the deploy job no longer downloads pnpm unchecked at run time ([#136]).
- The pre-commit hooks are pinned by commit SHA, with the release in a `# frozen:` comment ([#136]).
- Each zip holds only what its functions run: the packages of ours that its entry points import, with the files they read, and its dependency group's distributions. A change to code the stack doesn't run (the evaluation, the pipeline, the analysis), to the docs, or to the dev tools no longer redeploys the Runtime or the Lambdas ([#137]).
- The Runtime's stamp, which every execution record carries, is the last commit that changed its own code. Before, any change to `pyproject.toml` or `uv.lock` moved it; a change to its locked dependencies alone now deploys a new zip under the same stamp ([#137]).
- The next deploy updates the Runtime and all six Lambdas once, for their new zips, with no change to what they run ([#137]).
- The deploy builds the zips and the site in a job without credentials. The plan and apply jobs download them and run only Terraform, the AWS CLI, jq, and git, and the plan's comment is posted by a job with no AWS credentials ([#139]).
- Every promotion deploys: its cut changes the changelog, which now triggers the workflow ([#139]).
- `make site` is `make web`, then the new `make upload`, which reads Terraform's outputs with jq; setup now lists jq beside the AWS CLI ([#139]).
- The web app's lockfile is one YAML document, so GitHub's dependency graph sees its packages, and Dependabot's alerts and security updates cover them. pnpm 12 wrote its own version in a first document, and GitHub read only that one; pnpm no longer checks its version, which mise installs ([#141]).
- Dependabot also updates the actions the toolchain's composite action uses ([#141]).

### Fixed

- The README's badges point at the renamed repository, and CONTRIBUTING no longer mentions `make data ADOPT=1` ([#136]).
- A release's notes keep the links of its changelog section: the PR and advisory links it uses, and its relative paths, pointed at the release's tag. Before, they didn't resolve on the release's page ([#140]).
- The same commit builds the same zips from any checkout. Each zip listed its packages' console scripts, whose first line names the Python that built them, so a new checkout path or a new Python changed two of the zips ([#137]).

## [1.5.0] - 2026-10-31 (Promotion 11)

### Added

- CONTRIBUTING's promotion steps end with fast-forwarding `develop` to `main`, so `main` holds no commit that `develop` lacks.

### Changed

- The chat runs on AG-UI 1.0: `@ag-ui/client` 1.0.3 and `@assistant-ui/react-ag-ui` 0.1.0. The Runtime already spoke protocol 1.0, so only the web app changes, and Dependabot no longer holds AG-UI back ([#129]).

## [1.4.0] - 2026-10-31 (Promotion 10)

### Added

- This changelog, and a note in CONTRIBUTING on keeping it ([#110]).

### Changed

- The repository is now `gafnts/factored-hackathon-2026-faro`; links to the old name redirect. The CI roles trust its new OIDC subject, and CONTRIBUTING says what to do after a rename ([#122]).
- The organizers' dataset can no longer be downloaded, so `make data FROM=<folder>` restores the snapshot from a copy, checking every file against `dataset.lock`, and `make data` on its own checks the local copy. The docs say what a reader can still reproduce without it ([#126]).
- The data bucket outlives a teardown: Terraform no longer deletes it while it holds a snapshot or an export ([#126]).
- The Python package is now `faro`; `banking_agent` was its working name. AWS resource names keep the `banking-agent` prefix. The rename gives the pipeline a new version, so the tools read export `4a1bc9e15462b739` of the same snapshot (its data matches `795ff66b819516bf`'s), and the development sets are redrawn on it ([#127]).

### Removed

- The download from the organizers' bucket, with `ADOPT=1`, `DATASET_SOURCE_PROFILE`, and the checks against the organizers' account ([#126]).

### Fixed

- `make pipeline` with the default `DATA_DIR` wrote dbt's files under `pipeline/` and still printed "Built", so the `make export` after it failed. It now builds into `data/`, and a build that records no results fails ([#127]).

## [1.3.0] - 2026-10-29 (Promotion 9)

### Changed

- The agent's Python dependencies: FastAPI 0.161.1, AgentCore 1.24.2, LangChain 1.4.7, langchain-anthropic 1.7.6, LangGraph 1.2.16, langgraph-checkpoint-aws 1.2.6, AG-UI LangGraph 0.0.47 and boto3 1.43.118 ([#98], [#105]).
- The web app's dependencies: assistant-ui 0.15.31, react-ag-ui 0.0.71, Amplify and React 19.3.1 ([#97]). Its tooling: Vite 8.3.5, Vitest, ESLint and typescript-eslint ([#99]).
- The AWS provider, 6.70.0 ([#83]); GitHub Actions ([#82]); hook versions ([#81]); and the Python tooling: ruff 0.17, mypy 2.4, Playwright, pre-commit and pyproject-fmt ([#106]).
- uv 0.13 is required, and Python locks only releases at least a week old, as the web app already did ([#95]).
- Dependabot groups its updates by what a merge touches, puts majors in a PR of their own, and runs quarterly. TypeScript, the analysis libraries, DuckDB and dbt, and AG-UI stay where they are until we move them on purpose ([#93], [#96], [#103], [#104]).

## [1.2.0] - 2026-10-29 (Promotion 8)

### Security

- certifi 2026.10.18 drops the emSign root certificates whose intermediate keys were compromised ([GHSA-6q48-6c7w-9gw2], critical) ([#77]).
- langgraph 1.2.14 and langgraph-sdk 0.4.6 encode the IDs that the SDK's streaming client puts in request paths ([GHSA-3fx2-cqw9-xr3c], medium). Our code doesn't use that client, but both packages ship in the Runtime ([#92]).

### Changed

- `develop` is the default branch, so Dependabot's security updates go through it like every other change ([#78]).

## [1.1.0] - 2026-10-27 (Promotion 7)

### Security

- Vite 8.3.3, and in the development and analysis tools Pillow 13.0.0, virtualenv 21.14.13, filelock 4.2.4, Werkzeug 3.1.9 and opentelemetry-api 1.45.1: 67 Dependabot alerts closed. None of the Python packages ship in the Runtime or the Lambdas ([#74]).

## [1.0.0] - 2026-10-05

The prototype as submitted to the Factored AI & Data Hackathon 2026, and as judged.

[1.11.0]: https://github.com/gafnts/factored-hackathon-2026-faro/releases/tag/v1.11.0
[1.10.0]: https://github.com/gafnts/factored-hackathon-2026-faro/releases/tag/v1.10.0
[1.9.0]: https://github.com/gafnts/factored-hackathon-2026-faro/releases/tag/v1.9.0
[1.8.0]: https://github.com/gafnts/factored-hackathon-2026-faro/releases/tag/v1.8.0
[1.7.0]: https://github.com/gafnts/factored-hackathon-2026-faro/releases/tag/v1.7.0
[1.6.0]: https://github.com/gafnts/factored-hackathon-2026-faro/releases/tag/v1.6.0
[1.5.0]: https://github.com/gafnts/factored-hackathon-2026-faro/releases/tag/v1.5.0
[1.4.0]: https://github.com/gafnts/factored-hackathon-2026-faro/releases/tag/v1.4.0
[1.3.0]: https://github.com/gafnts/factored-hackathon-2026-faro/releases/tag/v1.3.0
[1.2.0]: https://github.com/gafnts/factored-hackathon-2026-faro/releases/tag/v1.2.0
[1.1.0]: https://github.com/gafnts/factored-hackathon-2026-faro/releases/tag/v1.1.0
[1.0.0]: https://github.com/gafnts/factored-hackathon-2026-faro/releases/tag/v1.0.0
[#74]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/74
[#77]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/77
[#78]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/78
[#81]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/81
[#82]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/82
[#83]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/83
[#92]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/92
[#93]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/93
[#95]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/95
[#96]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/96
[#97]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/97
[#98]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/98
[#99]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/99
[#103]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/103
[#104]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/104
[#105]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/105
[#106]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/106
[#110]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/110
[#122]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/122
[#126]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/126
[#127]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/127
[#129]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/129
[#132]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/132
[#133]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/133
[#134]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/134
[#135]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/135
[#136]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/136
[#137]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/137
[#139]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/139
[#140]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/140
[#141]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/141
[#147]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/147
[#156]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/156
[#158]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/158
[#159]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/159
[#160]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/160
[#163]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/163
[#164]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/164
[#165]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/165
[#168]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/168
[#174]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/174
[#183]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/183
[#188]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/188
[#190]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/190
[#193]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/193
[#194]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/194
[#195]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/195
[#201]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/201
[#202]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/202
[#203]: https://github.com/gafnts/factored-hackathon-2026-faro/pull/203
[GHSA-6q48-6c7w-9gw2]: https://github.com/advisories/GHSA-6q48-6c7w-9gw2
[GHSA-3fx2-cqw9-xr3c]: https://github.com/advisories/GHSA-3fx2-cqw9-xr3c
[GHSA-68fv-2mgg-jv7q]: https://github.com/advisories/GHSA-68fv-2mgg-jv7q
[GHSA-w74x-v6f9-j3xg]: https://github.com/advisories/GHSA-w74x-v6f9-j3xg
[GHSA-wjr9-j795-jxcr]: https://github.com/advisories/GHSA-wjr9-j795-jxcr
[GHSA-xhq7-xmqj-769h]: https://github.com/advisories/GHSA-xhq7-xmqj-769h
[GHSA-333x-w4r6-6crp]: https://github.com/advisories/GHSA-333x-w4r6-6crp
[GHSA-g3hv-75wg-m249]: https://github.com/advisories/GHSA-g3hv-75wg-m249

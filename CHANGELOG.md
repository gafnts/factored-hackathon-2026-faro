# Changelog

What changed in the prototype, one section per release, newest first. A release is a promotion from `develop` to `main`, tagged once its deploy is checked ([ADR-0009](docs/adr/0009-release-checked-deploys.md)): a minor version for each promotion, a patch for one that only fixes a release, and a major version for a change in who can use the prototype. `Unreleased` is what's merged into `develop` and not in a release yet. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

History before 2026-10-27 is in the [merged pull requests](https://github.com/gafnts/factored-hackathon-2026-faro/pulls?q=is%3Apr+is%3Amerged). Work we mean to do later is in [GitHub Issues](https://github.com/gafnts/factored-hackathon-2026-faro/issues).

## Unreleased

### Added

- A monthly budget for the AWS account, 40 USD unless `monthly_budget_usd` sets another amount, which notifies a topic when its forecast passes 80% and when the month's charges pass the amount; CONTRIBUTING says how to subscribe to it.

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
[GHSA-6q48-6c7w-9gw2]: https://github.com/advisories/GHSA-6q48-6c7w-9gw2
[GHSA-3fx2-cqw9-xr3c]: https://github.com/advisories/GHSA-3fx2-cqw9-xr3c
[GHSA-68fv-2mgg-jv7q]: https://github.com/advisories/GHSA-68fv-2mgg-jv7q
[GHSA-w74x-v6f9-j3xg]: https://github.com/advisories/GHSA-w74x-v6f9-j3xg
[GHSA-wjr9-j795-jxcr]: https://github.com/advisories/GHSA-wjr9-j795-jxcr
[GHSA-xhq7-xmqj-769h]: https://github.com/advisories/GHSA-xhq7-xmqj-769h
[GHSA-333x-w4r6-6crp]: https://github.com/advisories/GHSA-333x-w4r6-6crp
[GHSA-g3hv-75wg-m249]: https://github.com/advisories/GHSA-g3hv-75wg-m249

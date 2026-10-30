# Changelog

What changed in the prototype, one section per promotion from `develop` to `main`, newest first. `Unreleased` is what's merged into `develop` and not promoted yet. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

History before 2026-10-27 is in the [merged pull requests](https://github.com/gafnts/factored-hackathon-2026-gafnts/pulls?q=is%3Apr+is%3Amerged). Work we mean to do later is in [GitHub Issues](https://github.com/gafnts/factored-hackathon-2026-gafnts/issues).

## Unreleased

### Added

- This changelog, and a note in CONTRIBUTING on keeping it.

## Promotion 9 ([#107]) - 2026-10-29

### Changed

- The agent's Python dependencies: FastAPI 0.161.1, AgentCore 1.24.2, LangChain 1.4.7, langchain-anthropic 1.7.6, LangGraph 1.2.16, langgraph-checkpoint-aws 1.2.6, AG-UI LangGraph 0.0.47 and boto3 1.43.118 ([#98], [#105]).
- The web app's dependencies: assistant-ui 0.15.31, react-ag-ui 0.0.71, Amplify and React 19.3.1 ([#97]). Its tooling: Vite 8.3.5, Vitest, ESLint and typescript-eslint ([#99]).
- The AWS provider, 6.70.0 ([#83]); GitHub Actions ([#82]); hook versions ([#81]); and the Python tooling: ruff 0.17, mypy 2.4, Playwright, pre-commit and pyproject-fmt ([#106]).
- uv 0.13 is required, and Python locks only releases at least a week old, as the web app already did ([#95]).
- Dependabot groups its updates by what a merge touches, puts majors in a PR of their own, and runs quarterly. TypeScript, the analysis libraries, DuckDB and dbt, and AG-UI stay where they are until we move them on purpose ([#93], [#96], [#103], [#104]).

## Promotion 8 ([#94]) - 2026-10-29

### Security

- certifi 2026.10.18 drops the emSign root certificates whose intermediate keys were compromised ([GHSA-6q48-6c7w-9gw2], critical) ([#77]).
- langgraph 1.2.14 and langgraph-sdk 0.4.6 encode the IDs that the SDK's streaming client puts in request paths ([GHSA-3fx2-cqw9-xr3c], medium). Our code doesn't use that client, but both packages ship in the Runtime ([#92]).

### Changed

- `develop` is the default branch, so Dependabot's security updates go through it like every other change ([#78]).

## Promotion 7 ([#75]) - 2026-10-27

### Security

- Vite 8.3.3, and in the development and analysis tools Pillow 13.0.0, virtualenv 21.14.13, filelock 4.2.4, Werkzeug 3.1.9 and opentelemetry-api 1.45.1: 67 Dependabot alerts closed. None of the Python packages ship in the Runtime or the Lambdas ([#74]).

[#74]: https://github.com/gafnts/factored-hackathon-2026-gafnts/pull/74
[#75]: https://github.com/gafnts/factored-hackathon-2026-gafnts/pull/75
[#77]: https://github.com/gafnts/factored-hackathon-2026-gafnts/pull/77
[#78]: https://github.com/gafnts/factored-hackathon-2026-gafnts/pull/78
[#81]: https://github.com/gafnts/factored-hackathon-2026-gafnts/pull/81
[#82]: https://github.com/gafnts/factored-hackathon-2026-gafnts/pull/82
[#83]: https://github.com/gafnts/factored-hackathon-2026-gafnts/pull/83
[#92]: https://github.com/gafnts/factored-hackathon-2026-gafnts/pull/92
[#93]: https://github.com/gafnts/factored-hackathon-2026-gafnts/pull/93
[#94]: https://github.com/gafnts/factored-hackathon-2026-gafnts/pull/94
[#95]: https://github.com/gafnts/factored-hackathon-2026-gafnts/pull/95
[#96]: https://github.com/gafnts/factored-hackathon-2026-gafnts/pull/96
[#97]: https://github.com/gafnts/factored-hackathon-2026-gafnts/pull/97
[#98]: https://github.com/gafnts/factored-hackathon-2026-gafnts/pull/98
[#99]: https://github.com/gafnts/factored-hackathon-2026-gafnts/pull/99
[#103]: https://github.com/gafnts/factored-hackathon-2026-gafnts/pull/103
[#104]: https://github.com/gafnts/factored-hackathon-2026-gafnts/pull/104
[#105]: https://github.com/gafnts/factored-hackathon-2026-gafnts/pull/105
[#106]: https://github.com/gafnts/factored-hackathon-2026-gafnts/pull/106
[#107]: https://github.com/gafnts/factored-hackathon-2026-gafnts/pull/107
[GHSA-6q48-6c7w-9gw2]: https://github.com/advisories/GHSA-6q48-6c7w-9gw2
[GHSA-3fx2-cqw9-xr3c]: https://github.com/advisories/GHSA-3fx2-cqw9-xr3c

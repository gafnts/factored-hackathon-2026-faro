# ADR-0010: Gate every merge on known advisories in the lockfiles

## Status

Proposed (2026-11-01).

## Context

Our dependencies are pinned in lockfiles and updated by Dependabot ([ADR-0008](0008-pin-toolchain-by-layer.md)), and nothing checks what those pins contain. Five forces make that worth deciding:

- **Nothing stops a PR from adding a known-vulnerable version.** Dependabot raises an alert once a vulnerable version is on `develop`, the branch it scans, so the alert comes after the merge ([#112](https://github.com/gafnts/factored-hackathon-2026-faro/issues/112)). Our rule is that a security fix is done when it's promoted (CONTRIBUTING), and no check holds `main` to it.
- **GitHub can't see the web app's packages.** pnpm 12 writes `web/pnpm-lock.yaml` as two YAML documents when `packageManager` pins pnpm 12 or newer: a boot document holding pnpm itself, then the project. GitHub's dependency graph reads only the first (GitHub's changelog, 2026-10-20), so on 2026-11-01 it listed 184 PyPI packages and 16 npm packages, all of them pnpm's own binaries, against 659 in the lockfile. The lockfile has had two documents since the scaffold, so the web app has never had a Dependabot alert or security update.
- **Six advisories went unnoticed.** On 2026-11-01, osv-scanner found six advisories in the web lockfile and none in `uv.lock`: source-map-js 1.2.1 (one high), fast-uri 3.1.8 (two moderate, two low), and minimatch 10.2.6 and 3.1.5 (one moderate). All six come in through build and development tools (Tailwind's Vite plugin, jsdom, Vitest, ajv, ESLint). The fixed fast-uri and source-map-js fall inside their parents' ranges; minimatch's only fix is 11.0.1, a major that ESLint (`^10.2.5`) and jsx-a11y (`^3.1.2`) don't accept yet.
- **GitHub's own gate reads the same graph.** `actions/dependency-review-action` compares a PR's dependency changes through the dependency graph, so it would see no web change at all. On a private repository it also needs GitHub Advanced Security, which a personal account can't buy, and ADR-0008 means its conventions to carry over to such projects.
- **`develop` takes any commit.** It has no ruleset, so a PR merges with its checks red or still running, and anyone with write access can push to it directly. Auto-merging Dependabot's tooling groups ([#116](https://github.com/gafnts/factored-hackathon-2026-faro/issues/116)) would need required checks there first. Since [ADR-0009](0009-release-checked-deploys.md), no workflow holds a token that can write to the repository.

The setup must be reproducible and versioned (OPS-07), who may change the code must be explained (OPS-09), and the risks we leave must be written down (OPS-11).

## Decision

Scan both lockfiles on every PR, every push, and every week, fail on any known advisory unless we've accepted it in writing until a date, and require the scan wherever code lands.

| Check | Runs | Required on |
|---|---|---|
| `Known advisories` | Every PR and push to `develop` and `main`, weekly on `develop`, and by hand | `develop` and `main` |
| `Pre-commit (all stages)` | Every PR and push to `develop` and `main` | `develop` (new) and `main` |
| `Regression set (in process)` | Every PR and push to `develop` and `main` | `develop` (new) |
| `Verify source branch is develop` | Every PR into `main` | `main` |

In practice:

- **osv-scanner reads both lockfiles.** It checks every version in `uv.lock` and `web/pnpm-lock.yaml` against the OSV database, which includes GitHub's advisories and PyPI's, and reads every document of a pnpm lockfile. It joins the dev environment in `mise.toml`, since a different version can change what it finds, and runs as `make advisories`.
- **The whole tree, any severity.** The scan fails when any locked version has a known advisory, not only the ones a PR adds. A new advisory on a package we already lock turns every PR red until we fix it or accept it, which is the point: the decision is taken when the advisory appears, not when someone next touches that package.
- **An accepted advisory has a reason and an end date.** `osv-scanner.toml` lists each one with `reason` and `ignoreUntil`, and a test fails if either is missing or the date is more than 90 days away. When the date passes, the scan fails again and we decide again. The first entry is minimatch's: it matches the globs in our own ESLint config, the denial of service needs a pattern an attacker writes, and no fixed version fits ESLint's range. **Open**: its date.
- **Its own workflow.** `advisories.yml` runs the scan with no path filter, so it reports on every PR, as a required check must. Its weekly run on `develop`, the default branch, catches advisories published between merges, the job Dependabot's alerts do for the Python packages.
- **`develop` requires the checks.** A new ruleset on `develop` requires the three checks above, and blocks force pushes and deletion. It has no pull-request rule: required checks already refuse a commit that hasn't passed them, which rules out pushing a new commit directly, while the fast-forward of `develop` to `main` after a promotion pushes a commit that `main`'s runs already checked. `main`'s ruleset adds `Known advisories`. **Open**: a bypass for administrators.
- **The web lockfile is one document.** `pmOnFail: ignore` in `web/pnpm-workspace.yaml` stops pnpm from checking its own version against `packageManager`, and with it from writing the boot document. pnpm's documentation names this setting for a project whose pnpm is installed by a version manager such as mise, which ADR-0008 made ours: mise installs the pnpm `packageManager` names, and a test keeps the two equal. GitHub then sees the web app's packages, so Dependabot's alerts and security updates cover them too. The setting goes once GitHub reads both documents. **Open**: minimatch's alert, which opens once GitHub sees it.
- **No auto-merge.** Dependabot's tooling PRs merge by hand. #116 closes with the reasons below.
- **No license check.** None of our dependencies' licenses has been a question so far; osv-scanner can check them later with one flag.
- **GitHub's free extras where they're free.** Faro is public, so CodeQL's default setup and secret scanning's validity checks are on. Neither is required, since a private copy of this setup can't have them.
- **Dependabot reads the composite action.** `.github/dependabot.yml` lists `.github/actions/*` beside `/`, as ADR-0008's consequences asked, so the pins in `.github/actions/toolchain` stop going stale.

### Open decisions

We settle these before accepting this record. Each names the option we lean towards, which the rest of the record assumes, and the alternative still in play.

1. **A bypass on `develop`.** Lean: none, so every commit on `develop` has passed the gates; a small docs change goes through a PR, and the fast-forward needs no bypass. Alternative: administrators bypass the ruleset, so a docs commit can still go straight in, recorded as a bypass.
2. **minimatch's date.** Lean: 2027-01-15, after January's quarterly round, by which ESLint and jsx-a11y may accept minimatch 11. Alternative: the full 90 days, 2027-01-30.
3. **minimatch's alert.** Lean: dismissed as a tolerable risk, with the same reason as its entry in `osv-scanner.toml`, so an open alert always means a decision not yet taken. Alternative: left open until the fix, as a second reminder beside the entry's date.

## Alternatives considered

**GitHub's dependency review.** The issue's proposal, and free on a public repository. It reads the dependency graph, which sees none of the web app's packages, and on a private repository it needs GitHub Advanced Security.

**Fail only on what a PR adds.** It matches #112's wording, and an unrelated advisory never blocks a PR. osv-scanner's CLI can't compare two scans, so it takes the action's reusable workflow (below) or a comparison of our own, and an advisory on a package we already lock fails nothing: it waits for an alert, which for the web app never comes.

**osv-scanner's action and reusable workflow.** They run the scanner from a container image and upload the results to code scanning by default, which on a private repository needs GitHub Advanced Security. A tool pinned in `mise.toml` is checked against `mise.lock`'s checksums and runs the same on our machines.

**`pnpm audit` beside a Python auditor.** Two tools, two advisory sources, and two formats for accepted advisories, where one scanner covers both lockfiles.

**Keep the two-document lockfile until GitHub reads it.** The scan would catch what alerts miss, but the web app would still get no security updates, and GitHub gives no date.

**Fail only on high and critical advisories.** Today that's one of six. The rest would stay unseen; with any severity and a dated exception, each one becomes a decision we can read later.

**Auto-merge Dependabot's tooling groups** ([#116](https://github.com/gafnts/factored-hackathon-2026-faro/issues/116)). With quarterly updates it saves a few merges a quarter, and it needs a workflow holding `contents: write` and `pull-requests: write`, which ADR-0009 removed from CI. Dependabot's run of 2026-10-31 also left the three dependencies we name with extras out of their groups ([#133](https://github.com/gafnts/factored-hackathon-2026-faro/pull/133) to [#135](https://github.com/gafnts/factored-hackathon-2026-faro/pull/135)), so a rule keyed on the group would miss them, and the whole-tree scan would hold these PRs on unrelated advisories anyway.

**A pull-request rule on `develop`, like `main`'s.** It would also block the fast-forward, which then needs a bypass; the required checks already keep unchecked commits out.

## Consequences

Positive:
- No version with a known advisory reaches `main` without a written, dated decision in the repository.
- The web app gets Dependabot's alerts and security updates for the first time.
- Every commit on `develop` has passed the gates, so a release is cut from a green tree.
- One scanner covers both lockfiles, the same way on our machines and in CI, and works on a private repository.

Negative:
- An advisory unrelated to a PR turns that PR red until someone fixes or accepts it.
- The scan covers the two lockfiles only. GitHub Actions and hooks are pinned by SHA and updated by Dependabot, Terraform's providers by their lock's hashes, and mise's tools by `mise.lock`, but nothing checks their versions against advisories; `docs/evaluation/limitations.md` says so (OPS-11).
- osv-scanner sends each package's name and version to OSV's API. For Faro the lockfiles are public anyway; a private project that copies this can run it against a downloaded database instead.
- A new tool to trust and to bump by hand, quarterly, like the rest of the dev environment.
- pnpm no longer stops a contributor who runs a different pnpm; mise and the test do, for anyone who uses mise.
- With no bypass, a small docs change to `develop` goes through a PR too.
- GitHub disables a public repository's scheduled workflows after 60 days without activity, which would stop the weekly scan.
- Whether Dependabot's pnpm keeps the lockfile in one document is shown by its first web PR.

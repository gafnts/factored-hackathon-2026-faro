# Web app

The site customers and staff use: one static React app on S3 and CloudFront, with a route per role ([ADR-0007](../docs/adr/0007-role-gated-web-app.md)). `make web` builds it, `make site` uploads it, and `make web-dev` serves it on localhost against a deployed stack.

| Path                                                   | Holds                                                                                                                                                        |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `src/app.tsx`, `shell.tsx`, `pages/`                   | The routes, the bar with the language switch, and the sign-in and not-found pages                                                                            |
| `src/customer.tsx`, `src/chat/`                        | The customer's side: sign-in, then the chat on assistant-ui, its replies drawn as frames (cards, a statement, credits), and the confirm and handoff controls |
| `src/agent/`                                           | The human agent's console at `/cases`: the polled queue and each case's file                                                                                 |
| `src/auth.ts`, `session.ts`, `runtime.ts`, `config.ts` | Cognito sign-in per role, the runtime session, the AG-UI client that talks to the Runtime, and the stack's `config.json`                                     |
| `src/texts.ts`, `language.ts`                          | Every string the site shows, in both languages, and how the browser's language is read                                                                       |
| `src/contracts/`                                       | Types generated from the chat's and the console's JSON contracts by `pnpm contracts` (`scripts/contracts.ts`); never edited by hand                          |
| `src/grid.tsx`, `faces.ts`, `icons.tsx`, `styles.css`  | The look: the grid layout, the fonts, the icons, and the styles                                                                                              |
| `src/trusted-types.ts`, `zod.ts`                       | What the content security policy needs: the Trusted Types policy, and zod parsing without `new Function`                                                     |
| `tests/`                                               | Vitest, mirroring `src/`; the browser suite is `tests/integration/test_browser.py` at the repository root                                                    |

Versions, hooks, and the day-to-day commands are in [CONTRIBUTING.md](../CONTRIBUTING.md#1-install-the-toolchain).

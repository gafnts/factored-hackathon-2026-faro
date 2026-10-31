# faro

One package behind every `make` target, the Runtime, and the Lambdas. Each subpackage opens with the decision it implements.

| Package | Does | Runs as |
|---|---|---|
| `agent/` | The LangGraph workflow and the entrypoint the Runtime serves | The AgentCore Runtime |
| `tools/` | The card support tools, each validating its input against the full contract | The Gateway's Lambdas |
| `identity/` | The Cognito trigger that decides what goes into an access token | A Lambda |
| `console/` | The staff console's reading Lambdas | The console API |
| `policy/` | The policy's machine-readable parts: the handoff schema and payload rules | Imported by the agent and the tools |
| `contracts/` | The JSON Schemas everything is checked against, and their examples | Imported; `pnpm contracts` writes the web app's types from them |
| `dataset/` | The pinned snapshots: restore, check, lock, upload | `make data`, `make snapshot` |
| `pipeline/` | The code around the dbt project in [pipeline/](../../pipeline/README.md) | `make pipeline`, `make export`, `make contracts` |
| `export/` | The gold export's items and manifest, and the personas' tiny export | `make export`, `make tiny-export` |
| `evaluation/` | The families, the oracle, the generator, the players, and the harness | The `eval-*` targets, `make regression` |
| `analysis/` | The four reports under `docs/analysis/` | `make analysis` |

The top-level modules are shared rules and one-command scripts: the business clock and the split (`clock.py`, `split.py`), card number masking, the personas, the judges' users, the model key, and `build.py`, which zips the package for Terraform. Tests mirror this tree under [tests/faro/](../../tests/faro/).

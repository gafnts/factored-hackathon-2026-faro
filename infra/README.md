# Infrastructure

Three Terraform roots, each with its own state. `iam/` and `dataset/` are applied once per account, in that order; this directory is the stack, applied once per environment from `envs/<env>.tfvars`, by hand for `local` and by CI for `prototype`. [CONTRIBUTING.md](../CONTRIBUTING.md#3-deploy-your-own-copy) walks through them.

| Root | Creates | Targets |
|---|---|---|
| `iam/` | The deploy roles GitHub's OIDC assumes, the model key's secret, and the account's monthly budget | `make iam-init`, `iam-plan`, `iam-apply` |
| `dataset/` | The data bucket: the snapshots and the gold exports, outside the stacks so a destroy never deletes them | `make dataset-init`, `dataset-plan`, `dataset-apply` |
| `.` | The stack below, from the zips `make build` writes and the export `tools_data_export` names | `make init`, `plan`, `apply`, `destroy`, `outputs` with `ENV=` |

The stack is ten modules, each headed by the decision it builds:

| Module | Stands up |
|---|---|
| `identity` | The Cognito pool, its clients, and the pre-token trigger that shapes every access token |
| `tools_data` | The tools' data: one DynamoDB table per export, written only by its import |
| `sandbox` | The sign-in's sandbox and the confirmations, both expiring after a day |
| `gateway` | The AgentCore Gateway, its Cedar policies, and the two tool Lambdas, split by what they may write |
| `handoff` | The cases table and the Lambda that files to it, invoked only by the Runtime's role |
| `runtime` | The AgentCore Runtime, its code bucket, the model key's binding, and the checkpoint tables |
| `alarms` | Four alarms over the Runtime's log lines, and their topic |
| `console` | The staff console's API: an HTTP API behind a JWT authorizer, with a reading Lambda per route |
| `evaluation` | The harness's role and the bucket that keeps each case's results |
| `site` | The web app's bucket and CloudFront distribution, with the console API under `/api` |

The roots' lock files and backend files are committed; the modules' lock files are not. `make backend` regenerates the backend files from the account, and `make lock` the lock files.

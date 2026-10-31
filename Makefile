.DEFAULT_GOAL := help

ENV ?= local
TF      := terraform -chdir=infra
VARS    := -var-file=envs/$(ENV).tfvars
BACKEND := -backend-config=envs/$(ENV).backend.tfbackend
PLAN    := build/$(ENV).tfplan
OUTPUTS := build/$(ENV).outputs.json

# The pinned provider can't assume a role over an `aws login` sign-in, so Terraform gets the CLI's credentials.
TF_AWS := $(if $(AWS_PROFILE),creds="$$(aws configure export-credentials --format env)" && eval "$$creds" && env -u AWS_PROFILE ,)$(TF)

# The IAM and dataset roots run as admin: the scoped profile doesn't exist until the IAM root has run.
AWS_ADMIN_PROFILE ?= default
IAM_TF      := AWS_PROFILE=$(AWS_ADMIN_PROFILE) terraform -chdir=infra/iam
IAM_VARS    := -var-file=iam.tfvars
IAM_BACKEND := -backend-config=backend.tfbackend
DATASET_TF      := AWS_PROFILE=$(AWS_ADMIN_PROFILE) terraform -chdir=infra/dataset
DATASET_BACKEND := -backend-config=backend.tfbackend

.PHONY: help install tflint-init \
	check lint format type tf-format \
	test integration browser probe judges \
	build web web-dev site \
	bootstrap backend doctor provision teardown \
	data snapshot personas tiny-export pipeline export contracts analysis \
	iam-init iam-plan iam-apply iam-output iam-destroy \
	dataset-init dataset-plan dataset-apply dataset-destroy \
	init model-key plan apply destroy outputs lock \
	_check-backend _check-profile

# Targets tagged `## ...` are listed under the nearest `##@ Section` header.
help:
	@if [ -t 1 ] && [ -z "$$NO_COLOR" ]; then t='\033[36m'; h='\033[1m'; r='\033[0m'; fi; \
	awk -v t="$$t" -v h="$$h" -v r="$$r" 'BEGIN {FS = ":.*## "} \
		NR == 1 {printf "Usage: make %s<target>%s [ENV=local]\n", t, r} \
		/^##@ / {printf "\n%s%s%s\n", h, substr($$0, 5), r} \
		/^[a-zA-Z_-]+:.*## / {printf "  %s%-17s%s %s\n", t, $$1, r, $$2} \
		END {printf "\nDetails in CONTRIBUTING.md\n"}' $(MAKEFILE_LIST)


##@ Setup

install: ## Install the toolchain, Python and web deps, hooks, and tflint plugins
	mise install
	uv sync --all-groups --all-extras
	pnpm --dir web install --frozen-lockfile
	uv run pre-commit install
	uv run pre-commit install --hook-type pre-push
	tflint --init

tflint-init: ## Refresh tflint plugins after a .tflint.hcl bump
	tflint --init


##@ Quality gates

# Hooks without `stages` run in every stage, so pre-push covers both.
check: ## Run every hook against every file, as CI does
	uv run pre-commit run --all-files --hook-stage pre-push

lint: ## Run ruff check and ESLint
	uv run ruff check src tests
	pnpm --dir web lint

format: ## Apply ruff's, Prettier's, and ESLint's fixes
	uv run ruff check src tests --fix
	uv run ruff format src tests
	pnpm --dir web format

type: ## Run mypy and tsc
	uv run mypy src tests
	pnpm --dir web typecheck

tf-format: ## Format the Terraform files
	$(TF) fmt -recursive


##@ Testing

test: ## Run pytest and Vitest, each with its coverage floor
	uv run pytest -n auto --cov --cov-report=term-missing
	pnpm --dir web test

# Short tracebacks: a long one prints a failing helper's arguments, tokens included.
integration: _check-profile outputs ## Test ENV's deployed stack (SLOW=1 waits out a token too)
	STACK_OUTPUTS=$(OUTPUTS) uv run pytest -m "integration and not browser$(if $(SLOW),, and not slow)" -v --tb=short

browser: _check-profile outputs ## Play the chat and the console in Chromium against ENV's site
	uv run playwright install chromium
	STACK_OUTPUTS=$(OUTPUTS) uv run pytest -m browser -v --tb=short

probe: _check-profile outputs ## Time ENV's Runtime and check what it stores and traces
	STACK_OUTPUTS=$(OUTPUTS) uv run python -m tests.integration.probe

# JUDGE, not USER: make inherits USER from the shell as the login name, so it is always set.
judges: _check-profile outputs ## Manage the judges' users in ENV's pool (WHAT=, JUDGE=)
	uv run python -m faro.judges $(or $(WHAT),create) --stack $(OUTPUTS) $(if $(JUDGE),--user $(JUDGE))


##@ Build

build: ## Build the zips and rewrite the Gateway's tools.json
	uv run python -m faro.build

web: ## Build the web app
	pnpm --dir web build

site: _check-profile outputs web ## Build the web app and upload it to ENV's site
	@if [ "$(ENV)" = "prototype" ] && [ "$(I_KNOW)" != "1" ]; then \
		echo "Refusing to deploy prototype's site from local. CI owns prototype."; exit 1; fi
	@bash scripts/site.sh $(OUTPUTS)

web-dev: _check-profile outputs ## Serve the web app on localhost:5173 against ENV's stack
	@mkdir -p web/public
	uv run python -c 'import json, sys; json.dump(json.load(open(sys.argv[1]))["site"]["value"]["config"], sys.stdout)' $(OUTPUTS) > web/public/config.json
	SITE_URL=$$(uv run python -c 'import json, sys; print(json.load(open(sys.argv[1]))["site"]["value"]["url"])' $(OUTPUTS)) \
		pnpm --dir web dev


##@ Bootstrap

bootstrap: ## Create the state bucket and write the backend files (admin)
	@bash scripts/bootstrap.sh

# The local deploy profile doesn't exist yet, so local initializes as admin.
provision: ## Create the deploy roles and data bucket, init local (admin)
	$(MAKE) iam-init
	$(MAKE) iam-apply
	$(MAKE) dataset-init
	$(MAKE) dataset-apply
	$(MAKE) init ENV=local AWS_PROFILE=$(AWS_ADMIN_PROFILE)


doctor: ## Check every AWS profile and setup step (read-only)
	@bash scripts/doctor.sh

backend: ## Write the backend files (CI)
	@bash scripts/bootstrap-backend.sh

teardown: ## Delete the state bucket, last of all (admin; asks first)
	@bash scripts/teardown.sh

##@ Dataset snapshot

data: ## Check the local snapshot, or restore it from a copy (FROM=<folder>)
	uv run python -m faro.dataset $(if $(FROM),restore "$(FROM)",check)

snapshot: _check-profile data ## Copy the pinned snapshot into the data bucket
	uv run python -m faro.dataset upload

##@ Tools' data

personas: ## Choose the development personas into data/personas/
	uv run python -m faro.export personas

tiny-export: _check-profile ## Build and upload the personas' tiny export
	uv run python -m faro.export tiny --upload

##@ Pipeline

# A worktree's data/ is empty: make pipeline DATA_DIR=<main tree>/data
DATA_DIR ?= data

pipeline: ## Build bronze, silver, and gold into DATA_DIR/pipeline/
	uv run python -m faro.pipeline --data-dir $(DATA_DIR) build

export: _check-profile ## Export and upload the last build's gold
	uv run python -m faro.pipeline --data-dir $(DATA_DIR) export --upload

contracts: ## Rewrite the bronze contracts from the dictionary
	uv run python -m faro.pipeline contracts

##@ Evaluation

.PHONY: eval-sets eval-play eval-run eval-cleanup disagreements eval-index eval-report regression language-check

eval-sets: ## Draw the development sets (HELD_OUT= adds the held-out set)
	uv run python -m faro.evaluation --data-dir $(DATA_DIR) generate $(if $(HELD_OUT),--held-out $(HELD_OUT))

eval-play: ## Play and grade a set in process (SET=, MODELS=)
	uv run python -m faro.evaluation --data-dir $(DATA_DIR) play --set $(or $(SET),regression) --models $(or $(MODELS),scripted)

eval-run: _check-profile ## Play and grade a set against ENV (SET=, LIMIT=, PARALLEL=)
	uv run python -m faro.evaluation --data-dir $(DATA_DIR) run --set $(or $(SET),regression) --stack $(or $(STACK_OUTPUTS),$(OUTPUTS)) \
		$(foreach s,$(SITUATIONS),--situation $(s)) $(foreach l,$(LANGUAGES),--language $(l)) $(if $(LIMIT),--limit $(LIMIT)) --parallel $(or $(PARALLEL),2)

eval-cleanup: _check-profile ## Delete stopped runs' test users (RUN= for one)
	uv run python -m faro.evaluation cleanup --stack $(or $(STACK_OUTPUTS),$(OUTPUTS)) $(if $(RUN),--run $(RUN))

disagreements: ## Regenerate docs/evaluation/disagreements.md
	uv run python -m faro.evaluation disagreements

eval-index: ## Regenerate docs/evaluation/runs.md
	uv run python -m faro.evaluation index

eval-report: ## Write results.md (RUNS=, BASELINE=, JUDGED=, AGREEMENT=, LABELS=)
	uv run python -m faro.evaluation --data-dir $(DATA_DIR) report $(foreach r,$(RUNS),--run $(r)) \
		$(if $(BASELINE),--baseline $(BASELINE)) $(foreach j,$(JUDGED),--judged $(j)) $(if $(AGREEMENT),--agreement $(AGREEMENT)) \
		$(if $(LABELS),--labels $(LABELS))

regression: ## Play and grade the regression set in process, as CI does
	uv run pytest -m regression -v --tb=short

language-check: ## Read the paraphrases with the real prompts (paid; FAMILIES=)
	uv run python -m faro.evaluation language --env-file $(or $(ENV_FILE),.env) $(if $(PARALLEL),--parallel $(PARALLEL)) $(foreach f,$(FAMILIES),--only $(f))

# The judge and the router comparison call models outside the system under test; ESTIMATE=1 prices a call and makes none.
.PHONY: judge judge-sample judge-agreement relabel-sample relabel-agreement router-compare

judge: ## Judge replies (paid; RUN= or ITEMS=, LIMIT=, ESTIMATE=1)
	uv run python -m faro.evaluation judge $(if $(ITEMS),--items $(ITEMS),--run $(RUN)) --env-file $(or $(ENV_FILE),.env) \
		$(if $(LIMIT),--limit $(LIMIT)) $(if $(filter 1,$(ESTIMATE)),--estimate)

judge-sample: ## Draw the judge's blind sample and sheet (RUN=, SEEDED=)
	uv run python -m faro.evaluation judge-sample --run $(RUN) $(if $(SEEDED),--seeded $(SEEDED))

judge-agreement: ## Score the judge against a filled sheet (SAMPLE=, JUDGED=)
	uv run python -m faro.evaluation judge-agreement --sample $(SAMPLE) --judged $(JUDGED)

relabel-sample: ## Draw the hand-relabel sample and sheet (ROWS=, SEED=)
	uv run python -m faro.evaluation relabel-sample $(if $(ROWS),--rows $(ROWS)) $(if $(SEED),--seed $(SEED))

relabel-agreement: ## Score the relabel sheet against the labels (SAMPLE=)
	uv run python -m faro.evaluation relabel-agreement --sample $(SAMPLE)

# SIDE=held_out runs only once the candidates are frozen, from a clean tree, and never with FOLDS.
router-compare: ## Compare routers (paid; ROUTERS=, SIDE=, FOLDS=, ESTIMATE=1)
	uv run python -m faro.evaluation router $(foreach c,$(or $(ROUTERS),keyword haiku sonnet),--candidate $(c)) --side $(or $(SIDE),development) \
		--env-file $(or $(ENV_FILE),.env) $(if $(FOLDS),--folds $(FOLDS)) $(if $(PARALLEL),--parallel $(PARALLEL)) $(if $(filter 1,$(ESTIMATE)),--estimate)

##@ Analysis

analysis: ## Write the four docs/analysis/ reports from the snapshot
	uv run python -m faro.analysis all

##@ IAM module

iam-init: ## Initialize the IAM root's backend
	$(IAM_TF) init -reconfigure $(IAM_BACKEND)

iam-plan: ## Preview the IAM root
	$(IAM_TF) plan $(IAM_VARS)

iam-apply: ## Apply the IAM root (the deploy roles)
	$(IAM_TF) apply $(IAM_VARS)

iam-output: ## Print the deploy role ARNs
	$(IAM_TF) output

iam-destroy: ## Destroy the deploy roles (I_KNOW=1)
	@if [ "$(I_KNOW)" != "1" ]; then \
		echo "Refusing to destroy the deploy roles for every env. Re-run with I_KNOW=1."; exit 1; fi
	$(IAM_TF) destroy $(IAM_VARS)


##@ Dataset bucket

dataset-init: ## Initialize the dataset root's backend
	$(DATASET_TF) init -reconfigure $(DATASET_BACKEND)

dataset-plan: ## Preview the dataset root
	$(DATASET_TF) plan

dataset-apply: ## Apply the dataset root (the data bucket)
	$(DATASET_TF) apply

dataset-destroy: ## Destroy the data bucket, which stops while it holds anything (I_KNOW=1)
	@if [ "$(I_KNOW)" != "1" ]; then \
		echo "Refusing to destroy the data bucket. Re-run with I_KNOW=1."; exit 1; fi
	$(DATASET_TF) destroy


##@ Terraform (per ENV)

init: _check-profile ## Initialize ENV's backend
	$(TF_AWS) init -reconfigure $(BACKEND)

model-key: _check-profile ## Store ANTHROPIC_API_KEY from .env in ENV's secret
	uv run python -m faro.model_key --env $(ENV)

plan: _check-profile build ## Build, then plan ENV and save the plan
	$(TF_AWS) plan $(VARS) -out=../$(PLAN)

apply: _check-profile _check-backend ## Apply the saved plan (prototype needs I_KNOW=1)
	@if [ "$(ENV)" = "prototype" ] && [ "$(I_KNOW)" != "1" ]; then \
		echo "Refusing to apply prototype from local. CI owns prototype."; exit 1; fi
	@if [ ! -f $(PLAN) ]; then \
		echo "No saved plan for $(ENV). Run 'make plan ENV=$(ENV)' first."; exit 1; fi
	$(TF_AWS) apply ../$(PLAN)

# Terraform reads the zips even to plan a destroy.
destroy: _check-profile _check-backend build ## Destroy ENV (explicit ENV; prototype needs I_KNOW=1)
	@if [ "$(origin ENV)" != "command line" ] && [ "$(origin ENV)" != "environment" ]; then \
		echo "destroy requires explicit ENV (e.g. make destroy ENV=local). Refusing default."; exit 1; fi
	@if [ "$(ENV)" = "prototype" ] && [ "$(I_KNOW)" != "1" ]; then \
		echo "Refusing to destroy prototype. Re-run with I_KNOW=1."; exit 1; fi
	$(TF_AWS) destroy $(VARS)

outputs: _check-profile _check-backend ## Write ENV's outputs to build/ENV.outputs.json
	@mkdir -p build
	$(TF_AWS) output -json > $(OUTPUTS)


##@ Maintenance

lock: ## Regenerate the provider lock files for linux and darwin
	@find infra -name ".terraform.lock.hcl" -not -path "*/.terraform/*" -not -path "infra/modules/*" -exec dirname {} \; | \
		xargs -I{} terraform -chdir={} providers lock \
		-platform=linux_amd64 -platform=darwin_amd64 -platform=darwin_arm64


# INTERNAL

# Without AWS_PROFILE a local target would run as the default profile, usually admin; prototype uses the
# environment's credentials, as in CI.
_check-profile:
	@if [ "$(ENV)" = "local" ] && [ -z "$(AWS_PROFILE)" ]; then \
		echo "AWS_PROFILE is unset, so this would run as your default profile. Run 'direnv allow',"; \
		echo "or 'export AWS_PROFILE=banking-agent-local'."; exit 1; fi

# The backend pointer persists across runs, so `make init ENV=prototype` then `make destroy` would hit
# prototype's state.
_check-backend:
	@if [ ! -f infra/.terraform/terraform.tfstate ]; then \
		echo "Terraform not initialized. Run 'make init ENV=$(ENV)' first."; exit 1; fi
	@current=$$(grep -o '"key": *"[^"]*"' infra/.terraform/terraform.tfstate | head -1 | sed 's/.*"\([^"]*\)"$$/\1/'); \
	expected="service/$(ENV)/terraform.tfstate"; \
	if [ "$$current" != "$$expected" ]; then \
		echo "Backend mismatch: configured key is '$$current' but ENV=$(ENV) expects '$$expected'."; \
		echo "Run 'make init ENV=<env>' to reconfigure the backend before continuing."; \
		exit 1; \
	fi

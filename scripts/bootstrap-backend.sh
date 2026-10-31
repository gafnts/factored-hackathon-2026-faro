#!/usr/bin/env bash
set -euo pipefail

# shellcheck source-path=SCRIPTDIR
source "$(dirname "$0")/common.sh"

ENVS=("local" "prototype")

# bootstrap.sh passes the account ID.
ACCOUNT_ID="${1:-$(aws sts get-caller-identity --query Account --output text)}"
BUCKET=$(state_bucket_name "${ACCOUNT_ID}")

write_backend() {
  local file="$1" key="$2"
  mkdir -p "$(dirname "$file")"
  echo "Writing ${file}"
  cat > "$file" <<EOT
bucket       = "${BUCKET}"
key          = "${key}"
region       = "${AWS_REGION}"
use_lockfile = true
encrypt      = true
EOT
}

for ENV in "${ENVS[@]}"; do
  write_backend "./infra/envs/${ENV}.backend.tfbackend" "service/${ENV}/terraform.tfstate"
done

write_backend "./infra/iam/backend.tfbackend" "service/iam/terraform.tfstate"
write_backend "./infra/dataset/backend.tfbackend" "service/dataset/terraform.tfstate"

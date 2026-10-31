#!/usr/bin/env bash
set -euo pipefail

# shellcheck source-path=SCRIPTDIR
source "$(dirname "$0")/common.sh"

# Admin only, even if .envrc already points AWS_PROFILE at the local role.
export AWS_PROFILE="${ADMIN_PROFILE}"

IDENTITY=$(aws sts get-caller-identity --query '[Account,Arn]' --output text)
read -r ACCOUNT_ID PRINCIPAL_ARN <<< "${IDENTITY}"

# In a trust policy, the root ARN means every principal in the account.
if [[ "${PRINCIPAL_ARN}" == *":root" ]]; then
  echo "Refusing to continue: signed in as the account root user." >&2
  echo "The local deploy role would trust every principal in the account." >&2
  echo "Point AWS_ADMIN_PROFILE (default: 'default') at an IAM user or role with admin access." >&2
  exit 1
fi

BUCKET=$(state_bucket_name "${ACCOUNT_ID}")

# Before creating anything, so a missing gh sign-in fails early.
if [ -z "${GITHUB_OIDC_SUBJECT_PREFIX:-}" ]; then
  if ! ORIGIN_REPO=$(origin_github_repo); then
    echo "Refusing to continue: origin is not a GitHub repository." >&2
    echo "Point origin at your fork, or set GITHUB_OIDC_SUBJECT_PREFIX yourself." >&2
    exit 1
  fi
  if ! GITHUB_OIDC_SUBJECT_PREFIX=$(oidc_subject_prefix "${ORIGIN_REPO}"); then
    echo "Could not read the OIDC subject prefix of ${ORIGIN_REPO} with the GitHub CLI." >&2
    echo "Sign in with 'gh auth login', or set GITHUB_OIDC_SUBJECT_PREFIX yourself." >&2
    exit 1
  fi
fi

echo ""
echo "Bootstrapping ${PROJECT} in account ${ACCOUNT_ID} (${AWS_REGION})"
echo "  as ${PRINCIPAL_ARN} (profile: ${AWS_PROFILE})"
echo "  CI roles will trust ${GITHUB_OIDC_SUBJECT_PREFIX}"

echo ""
echo "Creating S3 bucket: ${BUCKET}"
if aws s3api head-bucket --bucket "${BUCKET}" 2>/dev/null; then
  echo "  bucket already exists, skipping"
else
  if [ "${AWS_REGION}" = "us-east-1" ]; then
    aws s3api create-bucket \
      --bucket "${BUCKET}" \
      --bucket-namespace account-regional \
      --region "${AWS_REGION}"
  else
    aws s3api create-bucket \
      --bucket "${BUCKET}" \
      --bucket-namespace account-regional \
      --region "${AWS_REGION}" \
      --create-bucket-configuration LocationConstraint="${AWS_REGION}"
  fi
fi

echo ""
echo "Enabling versioning"
aws s3api put-bucket-versioning \
  --bucket "${BUCKET}" \
  --versioning-configuration Status=Enabled

echo ""
echo "Blocking public access"
aws s3api put-public-access-block \
  --bucket "${BUCKET}" \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

echo ""
echo "Enabling encryption"
aws s3api put-bucket-encryption \
  --bucket "${BUCKET}" \
  --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

echo ""
echo "Writing backend files"
bash "$(dirname "$0")/bootstrap-backend.sh" "${ACCOUNT_ID}"

IAM_TFVARS="./infra/iam/iam.tfvars"
echo ""
if [ ! -f "${IAM_TFVARS}" ]; then
  echo "Writing ${IAM_TFVARS} (principal: ${PRINCIPAL_ARN})"
  cat > "${IAM_TFVARS}" <<EOT
local_principal_arn        = "${PRINCIPAL_ARN}"
state_bucket_name          = "${BUCKET}"
github_oidc_subject_prefix = "${GITHUB_OIDC_SUBJECT_PREFIX}"
EOT
elif ! grep -q '^github_oidc_subject_prefix' "${IAM_TFVARS}"; then
  echo "Adding github_oidc_subject_prefix to ${IAM_TFVARS}"
  echo "github_oidc_subject_prefix = \"${GITHUB_OIDC_SUBJECT_PREFIX}\"" >> "${IAM_TFVARS}"
else
  echo "  ${IAM_TFVARS} exists, skipping"
fi

ENVRC="./.envrc"
echo ""
if [ -f "${ENVRC}" ]; then
  echo "  ${ENVRC} exists, skipping"
else
  echo "Writing ${ENVRC}"
  printf 'export AWS_PROFILE=%s-local\n' "${PROJECT}" > "${ENVRC}"
fi

echo ""
echo "Bootstrap complete."
echo ""
echo "Next (CONTRIBUTING.md, steps 3.3 to 3.7):"
echo "  make provision             # One-time: create IAM roles and the data bucket, init the local stack"
echo "  make iam-output            # Role ARNs for the banking-agent-local profile and GitHub"
echo "  direnv allow               # Only once the banking-agent-local profile exists"
echo "  make doctor                # Check every AWS profile the project uses"
echo "  make plan && make apply    # Apply local infra"
echo "  make snapshot              # Copy the pinned dataset snapshot into the data bucket"
echo ""
echo "Prototype is initialized and applied by CI on merge to main."

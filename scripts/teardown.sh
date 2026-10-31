#!/usr/bin/env bash
set -euo pipefail

# Deletes the state bucket as the admin profile. Run it last (CONTRIBUTING.md, Teardown).

# shellcheck source-path=SCRIPTDIR
source "$(dirname "$0")/common.sh"

export AWS_PROFILE="${ADMIN_PROFILE}"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
assert_not_organizer "${ACCOUNT_ID}"
BUCKET=$(state_bucket_name "${ACCOUNT_ID}")

echo ""
echo "This permanently deletes the state bucket for ${PROJECT} (and ALL"
echo "Terraform state it holds):"
echo ""
echo "    ${BUCKET}"
echo ""
echo "Run this ONLY after every Terraform stack is destroyed (destroy for each"
echo "env, then iam-destroy and dataset-destroy). Deleting the bucket orphans anything Terraform"
echo "still tracks, such as a data bucket kept because it holds a snapshot."
echo ""

read -r -p "Type the bucket name to confirm: " CONFIRM
if [ "${CONFIRM}" != "${BUCKET}" ]; then
  echo "Confirmation did not match. Aborting; nothing was deleted."
  exit 1
fi

echo ""
if ! aws s3api head-bucket --bucket "${BUCKET}" 2>/dev/null; then
  echo "Bucket ${BUCKET} not found, skipping"
  echo ""
  echo "Teardown complete."
  exit 0
fi

purge_versions() {
  local query="$1"
  while :; do
    local lines
    lines=$(aws s3api list-object-versions --bucket "${BUCKET}" --max-keys 1000 \
      --region "${AWS_REGION}" --query "${query}" --output text 2>/dev/null || true)
    [ -z "${lines}" ] && break
    printf '%s\n' "${lines}" | while IFS=$'\t' read -r KEY VERSION; do
      [ -z "${KEY}" ] && continue
      aws s3api delete-object --bucket "${BUCKET}" --region "${AWS_REGION}" \
        --key "${KEY}" --version-id "${VERSION}" >/dev/null
    done
  done
}

echo "Emptying ${BUCKET} (all object versions and delete markers)"
purge_versions 'Versions[].[Key,VersionId]'
purge_versions 'DeleteMarkers[].[Key,VersionId]'

echo "Deleting bucket ${BUCKET}"
aws s3api delete-bucket --bucket "${BUCKET}" --region "${AWS_REGION}"

echo ""
echo "Teardown complete."

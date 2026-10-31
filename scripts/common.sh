# shellcheck shell=bash disable=SC2034
# Sourced by the other scripts.

PROJECT="banking-agent"
AWS_REGION="${AWS_REGION:-us-east-1}"
ADMIN_PROFILE="${AWS_ADMIN_PROFILE:-default}"

# The -an suffix puts the bucket in the account's regional namespace.
state_bucket_name() {
  local account_id="$1"
  echo "${PROJECT}-tfstate-${account_id}-${AWS_REGION}-an"
}

data_bucket_name() {
  local account_id="$1"
  echo "${PROJECT}-data-${account_id}-${AWS_REGION}-an"
}

# owner/repo of origin, if it's on GitHub.
origin_github_repo() {
  local url repo
  url=$(git remote get-url origin 2>/dev/null) || return 1
  repo="${url%.git}"
  repo="${repo#*github.com[:/]}"
  [[ "${repo}" =~ ^[^/]+/[^/]+$ ]] || return 1
  echo "${repo}"
}

# Includes the owner and repo IDs, so it can't be built from the name.
oidc_subject_prefix() {
  local repo="$1"
  gh api "repos/${repo}/actions/oidc/customization/sub" --jq .sub_claim_prefix
}

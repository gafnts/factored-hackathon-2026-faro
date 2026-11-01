#!/usr/bin/env bash
set -euo pipefail

# Checks that AWS runs what build/ holds (ADR-0009): each Lambda's CodeSha256 against its zip's SHA-256, and the
# artifact of the Runtime version the DEFAULT endpoint serves against its zip's key. Terraform's state, on stdin,
# says which zip each function runs; nothing is downloaded. jq and the AWS CLI only: CI runs this with the deploy role.

STATE=$(cat)
failed=0

resources() {
  jq -r --arg type "$1" '.values.root_module | .. | objects | select(.type? == $type)' <<< "${STATE}"
}

check() {
  if [[ "$2" == "$3" ]]; then
    echo "ok    $1"
  else
    echo "WRONG $1: runs ${2}, built ${3}"
    failed=1
  fi
}

while read -r name zip; do
  check "${name}" \
    "$(aws lambda get-function-configuration --function-name "${name}" --query CodeSha256 --output text)" \
    "$(openssl dgst -sha256 -binary "infra/${zip}" | openssl base64)"
done < <(resources aws_lambda_function | jq -r '"\(.values.function_name) \(.values.filename)"')

runtime=$(resources aws_bedrockagentcore_agent_runtime | jq -r .values.agent_runtime_id)
zip=$(jq -r 'select(.name == "runtime") | .values.source' <<< "$(resources aws_s3_object)")
# An endpoint moves to a new version after the apply returns.
for _ in $(seq 30); do
  read -r status live <<< "$(aws bedrock-agentcore-control get-agent-runtime-endpoint \
    --agent-runtime-id "${runtime}" --endpoint-name DEFAULT --query '[status, liveVersion]' --output text)"
  [[ "${status}" == "READY" ]] && break
  sleep 5
done
check "runtime, version ${live} (${status})" \
  "$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "${runtime}" --agent-runtime-version "${live}" \
    --query agentRuntimeArtifact.codeConfiguration.code.s3.prefix --output text)" \
  "runtime/$(openssl dgst -sha256 -r "infra/${zip}" | cut -d' ' -f1).zip"

if [[ "${failed}" == 1 ]]; then
  echo "The deployed code isn't this build's." >&2
  exit 1
fi

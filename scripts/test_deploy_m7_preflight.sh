#!/usr/bin/env bash
set -euo pipefail

# Regression checks for the inert deployment preflight. These checks never call
# gcloud because APPLY remains false throughout.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script="${repo_root}/infra/cloudrun/deploy-m7.sh"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

common=(
  PROJECT_ID=tripagent-505715
  REGION=europe-west3
  IMAGE_URI=example.invalid/image
  EDGE_SERVICE_ACCOUNT=edge@example.invalid
  WORKER_SERVICE_ACCOUNT=worker@example.invalid
  PUBSUB_INVOKER_SERVICE_ACCOUNT=pubsub@example.invalid
  GEMINI_MODEL_ID=gemini-3.5-flash
  APPLY=false
)

env "${common[@]}" "${script}" >"${tmp_dir}/default.out"
grep -Fq 'Calendar=false/false, Gmail=false/false' "${tmp_dir}/default.out"

if env "${common[@]}" ENABLE_CALENDAR_ACTIONS=true "${script}" >"${tmp_dir}/invalid-calendar.out" 2>&1; then
  echo "Calendar actions unexpectedly passed without a connection." >&2
  exit 1
fi
grep -Fq 'requires ENABLE_CALENDAR_CONNECTIONS=true' "${tmp_dir}/invalid-calendar.out"

if env "${common[@]}" ENABLE_CALENDAR_CONNECTIONS=true "${script}" >"${tmp_dir}/missing-calendar.out" 2>&1; then
  echo "Calendar connection unexpectedly passed without OAuth configuration." >&2
  exit 1
fi
grep -Fq 'when enabling Calendar' "${tmp_dir}/missing-calendar.out"

env "${common[@]}" \
  ENABLE_CALENDAR_CONNECTIONS=true \
  CALENDAR_OAUTH_CLIENT_ID=client-id \
  CALENDAR_REDIRECT_URI=https://edge.example.test/connections/calendar/callback \
  "${script}" >"${tmp_dir}/calendar.out"
grep -Fq 'Calendar=true/false, Gmail=false/false' "${tmp_dir}/calendar.out"

if env "${common[@]}" ENABLE_GMAIL_DRAFTS=true "${script}" >"${tmp_dir}/invalid-gmail.out" 2>&1; then
  echo "Gmail drafts unexpectedly passed without a connection." >&2
  exit 1
fi
grep -Fq 'requires ENABLE_GMAIL_CONNECTIONS=true' "${tmp_dir}/invalid-gmail.out"

if env "${common[@]}" ENABLE_GMAIL_CONNECTIONS=true "${script}" >"${tmp_dir}/missing-gmail.out" 2>&1; then
  echo "Gmail connection unexpectedly passed without OAuth configuration." >&2
  exit 1
fi
grep -Fq 'when enabling Gmail' "${tmp_dir}/missing-gmail.out"

env "${common[@]}" \
  ENABLE_GMAIL_CONNECTIONS=true \
  GMAIL_OAUTH_CLIENT_ID=gmail-client-id \
  GMAIL_REDIRECT_URI=https://edge.example.test/connections/gmail/callback \
  "${script}" >"${tmp_dir}/gmail.out"
grep -Fq 'Calendar=false/false, Gmail=true/false' "${tmp_dir}/gmail.out"

echo "deploy-m7 preflight checks: PASS"

#!/bin/sh
set -eu

# Resource-scoped access for the single BYOK secret. It is deliberately not a
# project IAM role: the worker cannot access any other Secret Manager secret.
: "${PROJECT_ID:?set PROJECT_ID}"
: "${WORKER_SERVICE_ACCOUNT:?set WORKER_SERVICE_ACCOUNT}"

: "${BYOK_SECRET_ID:=trip-agent-user-gemini}"

SECRET="${BYOK_SECRET_ID}"
MEMBER="serviceAccount:${WORKER_SERVICE_ACCOUNT}"

if [ "${APPLY:-false}" != "true" ]; then
  echo "Preflight only: no Google Cloud resource was changed."
  echo "Will bind Secret Accessor and Secret Version Manager only on ${SECRET}."
  exit 0
fi

gcloud secrets add-iam-policy-binding "${SECRET}" --project="${PROJECT_ID}" \
  --member="${MEMBER}" --role="roles/secretmanager.secretAccessor"
gcloud secrets add-iam-policy-binding "${SECRET}" --project="${PROJECT_ID}" \
  --member="${MEMBER}" --role="roles/secretmanager.secretVersionManager"

echo "Resource-scoped BYOK secret access configured."

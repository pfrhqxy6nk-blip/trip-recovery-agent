#!/bin/sh
set -eu

# Deliberately inert unless APPLY=true. It creates only the watch scheduler identity,
# Scheduler API/job, and a Cloud Run invoker binding; it never publishes the repo or
# changes the public edge/webhook.
: "${PROJECT_ID:?set PROJECT_ID}"
: "${REGION:?set REGION}"
: "${WORKER_URL:?set WORKER_URL}"

WORKER_SERVICE="${WORKER_SERVICE:-trip-recovery-agent}"
SCHEDULER_SERVICE_ACCOUNT="${SCHEDULER_SERVICE_ACCOUNT:-trip-agent-watch-scheduler@${PROJECT_ID}.iam.gserviceaccount.com}"
SCHEDULER_JOB="${SCHEDULER_JOB:-trip-watch-tick}"
SCHEDULE="${SCHEDULE:-*/30 * * * *}"
SCHEDULER_SERVICE_ACCOUNT_NAME="${SCHEDULER_SERVICE_ACCOUNT%@*}"

if [ "${APPLY:-false}" != "true" ]; then
  echo "Preflight only: no Google Cloud resource was changed."
  echo "Will enable: cloudscheduler.googleapis.com"
  echo "Will create: ${SCHEDULER_SERVICE_ACCOUNT}, job ${SCHEDULER_JOB}"
  echo "Will grant: roles/run.invoker on ${WORKER_SERVICE} to that service account"
  echo "Will call: ${WORKER_URL%/}/internal/watch/tick (${SCHEDULE})"
  exit 0
fi

gcloud services enable cloudscheduler.googleapis.com --project="${PROJECT_ID}"

if ! gcloud iam service-accounts describe "${SCHEDULER_SERVICE_ACCOUNT}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${SCHEDULER_SERVICE_ACCOUNT_NAME}" \
    --project="${PROJECT_ID}" \
    --display-name="Trip Agent Watch Scheduler"
fi

gcloud run services add-iam-policy-binding "${WORKER_SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --member="serviceAccount:${SCHEDULER_SERVICE_ACCOUNT}" \
  --role="roles/run.invoker"

if gcloud scheduler jobs describe "${SCHEDULER_JOB}" --project="${PROJECT_ID}" --location="${REGION}" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "${SCHEDULER_JOB}" \
    --project="${PROJECT_ID}" --location="${REGION}" --schedule="${SCHEDULE}" \
    --uri="${WORKER_URL%/}/internal/watch/tick" --http-method=POST \
    --oidc-service-account-email="${SCHEDULER_SERVICE_ACCOUNT}" \
    --oidc-token-audience="${WORKER_URL%/}"
else
  gcloud scheduler jobs create http "${SCHEDULER_JOB}" \
    --project="${PROJECT_ID}" --location="${REGION}" --schedule="${SCHEDULE}" \
    --uri="${WORKER_URL%/}/internal/watch/tick" --http-method=POST \
    --oidc-service-account-email="${SCHEDULER_SERVICE_ACCOUNT}" \
    --oidc-token-audience="${WORKER_URL%/}"
fi

echo "Trip Watch scheduler configured. Budget alerts remain a separate billing-owner action."

#!/bin/sh
set -eu

# Deliberately inert unless APPLY=true. This wires the durable approval/resume
# outbox to a separate authenticated Pub/Sub push subscription.
: "${PROJECT_ID:?set PROJECT_ID}"
: "${REGION:?set REGION}"
: "${WORKER_URL:?set WORKER_URL}"

WORKER_SERVICE="${WORKER_SERVICE:-trip-recovery-agent}"
COMMAND_TOPIC_ID="${PUBSUB_COMMAND_TOPIC_ID:-trip-workflow-commands}"
COMMAND_SUBSCRIPTION="${PUBSUB_COMMAND_SUBSCRIPTION:-trip-workflow-commands-sub}"
PUBSUB_INVOKER_SERVICE_ACCOUNT="${PUBSUB_INVOKER_SERVICE_ACCOUNT:-trip-agent-pubsub-invoker@${PROJECT_ID}.iam.gserviceaccount.com}"
PUSH_ENDPOINT="${WORKER_URL%/}/internal/pubsub/commands"

if [ "${APPLY:-false}" != "true" ]; then
  echo "Preflight only: no Google Cloud resource was changed."
  echo "Will create topic ${COMMAND_TOPIC_ID} and authenticated subscription ${COMMAND_SUBSCRIPTION}."
  echo "Will push to ${PUSH_ENDPOINT}."
  exit 0
fi

gcloud services enable pubsub.googleapis.com --project="${PROJECT_ID}"

if ! gcloud pubsub topics describe "${COMMAND_TOPIC_ID}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud pubsub topics create "${COMMAND_TOPIC_ID}" --project="${PROJECT_ID}"
fi

if gcloud pubsub subscriptions describe "${COMMAND_SUBSCRIPTION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud pubsub subscriptions update "${COMMAND_SUBSCRIPTION}" \
    --project="${PROJECT_ID}" \
    --push-endpoint="${PUSH_ENDPOINT}" \
    --push-auth-service-account="${PUBSUB_INVOKER_SERVICE_ACCOUNT}" \
    --push-auth-token-audience="${WORKER_URL%/}" \
    --ack-deadline=300
else
  gcloud pubsub subscriptions create "${COMMAND_SUBSCRIPTION}" \
    --project="${PROJECT_ID}" \
    --topic="${COMMAND_TOPIC_ID}" \
    --push-endpoint="${PUSH_ENDPOINT}" \
    --push-auth-service-account="${PUBSUB_INVOKER_SERVICE_ACCOUNT}" \
    --push-auth-token-audience="${WORKER_URL%/}" \
    --ack-deadline=300
fi

gcloud run services add-iam-policy-binding "${WORKER_SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --member="serviceAccount:${PUBSUB_INVOKER_SERVICE_ACCOUNT}" \
  --role="roles/run.invoker"

echo "Workflow command delivery configured."

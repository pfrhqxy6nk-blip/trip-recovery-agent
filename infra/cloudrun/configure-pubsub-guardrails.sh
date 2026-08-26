#!/bin/sh
set -eu

# Adds bounded redelivery to the two event streams. This is deliberately inert
# unless APPLY=true; dead-letter topics are audit sinks, not recovery triggers.
: "${PROJECT_ID:?set PROJECT_ID}"

DISRUPTION_SUBSCRIPTION="${DISRUPTION_SUBSCRIPTION:-trip-disruptions-sub}"
COMMAND_SUBSCRIPTION="${COMMAND_SUBSCRIPTION:-trip-workflow-commands-sub}"
DISRUPTION_DL_TOPIC="${DISRUPTION_DL_TOPIC:-trip-disruptions-dead-letter}"
COMMAND_DL_TOPIC="${COMMAND_DL_TOPIC:-trip-workflow-commands-dead-letter}"
MAX_DELIVERY_ATTEMPTS="${MAX_DELIVERY_ATTEMPTS:-5}"

if [ "${APPLY:-false}" != "true" ]; then
  echo "Preflight only: no Google Cloud resource was changed."
  echo "Will create dead-letter topics ${DISRUPTION_DL_TOPIC}, ${COMMAND_DL_TOPIC}."
  echo "Will cap retries for ${DISRUPTION_SUBSCRIPTION}, ${COMMAND_SUBSCRIPTION} at ${MAX_DELIVERY_ATTEMPTS}."
  exit 0
fi

gcloud services enable pubsub.googleapis.com --project="${PROJECT_ID}"

for topic in "${DISRUPTION_DL_TOPIC}" "${COMMAND_DL_TOPIC}"; do
  if ! gcloud pubsub topics describe "${topic}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud pubsub topics create "${topic}" --project="${PROJECT_ID}"
  fi
done

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
PUBSUB_SERVICE_AGENT="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"

# Pub/Sub's managed service agent must be able to forward dead letters and
# acknowledge the source subscription. These are the documented least roles for
# dead-letter forwarding; no application runtime identity receives them.
for topic in "${DISRUPTION_DL_TOPIC}" "${COMMAND_DL_TOPIC}"; do
  gcloud pubsub topics add-iam-policy-binding "${topic}" \
    --project="${PROJECT_ID}" \
    --member="serviceAccount:${PUBSUB_SERVICE_AGENT}" \
    --role="roles/pubsub.publisher" >/dev/null
done
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${PUBSUB_SERVICE_AGENT}" \
  --role="roles/pubsub.subscriber" >/dev/null

gcloud pubsub subscriptions update "${DISRUPTION_SUBSCRIPTION}" \
  --project="${PROJECT_ID}" \
  --dead-letter-topic="projects/${PROJECT_ID}/topics/${DISRUPTION_DL_TOPIC}" \
  --max-delivery-attempts="${MAX_DELIVERY_ATTEMPTS}"
gcloud pubsub subscriptions update "${COMMAND_SUBSCRIPTION}" \
  --project="${PROJECT_ID}" \
  --dead-letter-topic="projects/${PROJECT_ID}/topics/${COMMAND_DL_TOPIC}" \
  --max-delivery-attempts="${MAX_DELIVERY_ATTEMPTS}"

echo "Pub/Sub retry guardrails configured."

#!/bin/sh
set -eu

# Deploys only the private worker revision used by Trip Watch.  It deliberately
# does not touch the public Telegram edge, webhook registration, repository, or
# Devpost submission.
: "${PROJECT_ID:?set PROJECT_ID}"
: "${REGION:?set REGION}"
: "${IMAGE_URI:?set IMAGE_URI}"
: "${WORKER_SERVICE_ACCOUNT:?set WORKER_SERVICE_ACCOUNT}"
: "${GEMINI_MODEL_ID:?set GEMINI_MODEL_ID}"

WORKER_SERVICE="${WORKER_SERVICE:-trip-recovery-agent}"
WATCH_ENABLED="${ENABLE_TRIP_WATCH:-true}"
WATCH_MAX_CHECKS="${TRIP_WATCH_MAX_CHECKS_PER_TICK:-20}"
JUDGE_ENABLED="${ENABLE_JUDGE_MODE:-true}"
JUDGE_DAILY_CALLS="${JUDGE_DAILY_VERTEX_CALLS:-20}"
JUDGE_DAILY_CALLS_PER_USER="${JUDGE_DAILY_VERTEX_CALLS_PER_USER:-5}"
JUDGE_MAX_OUTPUT="${JUDGE_MAX_OUTPUT_TOKENS:-4096}"
MAX_INSTANCES="${MAX_INSTANCES:-1}"
COMMAND_TOPIC_ID="${PUBSUB_COMMAND_TOPIC_ID:-trip-workflow-commands}"
TELEGRAM_BOT_TOKEN_SECRET="${TELEGRAM_BOT_TOKEN_SECRET:-telegram-bot-token}"
TELEGRAM_WEBHOOK_SECRET_SECRET="${TELEGRAM_WEBHOOK_SECRET_SECRET:-telegram-webhook-secret}"
APPROVAL_CALLBACK_SIGNING_KEY_SECRET="${APPROVAL_CALLBACK_SIGNING_KEY_SECRET:-approval-callback-signing-key}"

case "${MAX_INSTANCES}" in
  1) ;;
  *) echo "Refusing rollout: MAX_INSTANCES must be 1 for the bounded hackathon deployment." >&2; exit 2 ;;
esac
if [ "${JUDGE_DAILY_CALLS_PER_USER}" -gt "${JUDGE_DAILY_CALLS}" ]; then
  echo "Refusing rollout: per-user Vertex budget cannot exceed the global budget." >&2
  exit 2
fi

if [ "${APPLY:-false}" != "true" ]; then
  echo "Preflight only: no Google Cloud resource was changed."
  echo "Will deploy private worker ${WORKER_SERVICE} with Trip Watch=${WATCH_ENABLED}."
  echo "Judge mode=${JUDGE_ENABLED}, shared Vertex calls/day=${JUDGE_DAILY_CALLS}, per user=${JUDGE_DAILY_CALLS_PER_USER}."
  echo "Telegram credentials are referenced from Secret Manager only."
  exit 0
fi

gcloud run deploy "${WORKER_SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${IMAGE_URI}" \
  --service-account="${WORKER_SERVICE_ACCOUNT}" \
  --ingress=all \
  --no-allow-unauthenticated \
  --max-instances="${MAX_INSTANCES}" \
  --update-env-vars="APP_ROLE=worker,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=global,GOOGLE_GENAI_USE_VERTEXAI=true,GEMINI_MODEL_ID=${GEMINI_MODEL_ID},PUBSUB_TOPIC_ID=trip-disruptions,PUBSUB_COMMAND_TOPIC_ID=${COMMAND_TOPIC_ID},PUBSUB_TRANSPORT=google,PROCESS_EVENTS_INLINE=false,ENABLE_TRIP_WATCH=${WATCH_ENABLED},TRIP_WATCH_MAX_CHECKS_PER_TICK=${WATCH_MAX_CHECKS},ENABLE_JUDGE_MODE=${JUDGE_ENABLED},JUDGE_DAILY_VERTEX_CALLS=${JUDGE_DAILY_CALLS},JUDGE_DAILY_VERTEX_CALLS_PER_USER=${JUDGE_DAILY_CALLS_PER_USER},JUDGE_MAX_OUTPUT_TOKENS=${JUDGE_MAX_OUTPUT}" \
  --update-secrets="TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN_SECRET}:latest,TELEGRAM_WEBHOOK_SECRET=${TELEGRAM_WEBHOOK_SECRET_SECRET}:latest,APPROVAL_CALLBACK_SIGNING_KEY=${APPROVAL_CALLBACK_SIGNING_KEY_SECRET}:latest"

echo "Trip Watch worker revision deployed."

#!/bin/sh
set -eu

# This file is intentionally inert unless APPLY=true is supplied explicitly.
# It never creates secret values; referenced Secret Manager resources must exist first.
: "${PROJECT_ID:?set PROJECT_ID}"
: "${REGION:?set REGION}"
: "${IMAGE_URI:?set IMAGE_URI}"
: "${EDGE_SERVICE_ACCOUNT:?set EDGE_SERVICE_ACCOUNT}"
: "${WORKER_SERVICE_ACCOUNT:?set WORKER_SERVICE_ACCOUNT}"
: "${PUBSUB_INVOKER_SERVICE_ACCOUNT:?set PUBSUB_INVOKER_SERVICE_ACCOUNT}"
: "${GEMINI_MODEL_ID:?set GEMINI_MODEL_ID}"

WORKER_SERVICE="${WORKER_SERVICE:-trip-recovery-agent}"
EDGE_SERVICE="${EDGE_SERVICE:-trip-recovery-edge}"
WATCH_ENABLED="${ENABLE_TRIP_WATCH:-true}"
WATCH_MAX_CHECKS="${TRIP_WATCH_MAX_CHECKS_PER_TICK:-20}"
JUDGE_ENABLED="${ENABLE_JUDGE_MODE:-true}"
JUDGE_DAILY_CALLS="${JUDGE_DAILY_VERTEX_CALLS:-20}"
JUDGE_DAILY_CALLS_PER_USER="${JUDGE_DAILY_VERTEX_CALLS_PER_USER:-5}"
JUDGE_MAX_OUTPUT="${JUDGE_MAX_OUTPUT_TOKENS:-4096}"
MAX_INSTANCES="${MAX_INSTANCES:-1}"
COMMAND_TOPIC_ID="${PUBSUB_COMMAND_TOPIC_ID:-trip-workflow-commands}"
CALENDAR_ENABLED="${ENABLE_CALENDAR_CONNECTIONS:-false}"
CALENDAR_ACTIONS="${ENABLE_CALENDAR_ACTIONS:-false}"
GMAIL_ENABLED="${ENABLE_GMAIL_CONNECTIONS:-false}"
GMAIL_DRAFTS="${ENABLE_GMAIL_DRAFTS:-false}"
TELEGRAM_BOT_TOKEN_SECRET="${TELEGRAM_BOT_TOKEN_SECRET:-telegram-bot-token}"
TELEGRAM_WEBHOOK_SECRET_SECRET="${TELEGRAM_WEBHOOK_SECRET_SECRET:-telegram-webhook-secret}"
APPROVAL_CALLBACK_SIGNING_KEY_SECRET="${APPROVAL_CALLBACK_SIGNING_KEY_SECRET:-approval-callback-signing-key}"
CALENDAR_OAUTH_CLIENT_ID="${CALENDAR_OAUTH_CLIENT_ID:-}"
CALENDAR_OAUTH_CLIENT_SECRET_RESOURCE_NAME="${CALENDAR_OAUTH_CLIENT_SECRET_RESOURCE_NAME:-projects/${PROJECT_ID}/secrets/trip-agent-calendar-oauth-client/versions/latest}"
CALENDAR_REDIRECT_URI="${CALENDAR_REDIRECT_URI:-}"
CALENDAR_OAUTH_SIGNING_KEY_SECRET="${CALENDAR_OAUTH_SIGNING_KEY_SECRET:-trip-agent-calendar-oauth-signing-key}"
GMAIL_OAUTH_CLIENT_ID="${GMAIL_OAUTH_CLIENT_ID:-${CALENDAR_OAUTH_CLIENT_ID}}"
GMAIL_OAUTH_CLIENT_SECRET_RESOURCE_NAME="${GMAIL_OAUTH_CLIENT_SECRET_RESOURCE_NAME:-${CALENDAR_OAUTH_CLIENT_SECRET_RESOURCE_NAME}}"
GMAIL_REDIRECT_URI="${GMAIL_REDIRECT_URI:-}"
GMAIL_OAUTH_SIGNING_KEY_SECRET="${GMAIL_OAUTH_SIGNING_KEY_SECRET:-trip-agent-gmail-oauth-signing-key}"
OAUTH_REFRESH_TOKENS_SECRET_RESOURCE_NAME="${OAUTH_REFRESH_TOKENS_SECRET_RESOURCE_NAME:-projects/${PROJECT_ID}/secrets/trip-agent-oauth-refresh-tokens}"

case "${MAX_INSTANCES}" in
  1) ;;
  *) echo "Refusing rollout: MAX_INSTANCES must be 1 for the bounded hackathon deployment." >&2; exit 2 ;;
esac
if [ "${JUDGE_DAILY_CALLS_PER_USER}" -gt "${JUDGE_DAILY_CALLS}" ]; then
  echo "Refusing rollout: per-user Vertex budget cannot exceed the global budget." >&2
  exit 2
fi
case "${CALENDAR_ENABLED}" in true|false) ;; *) echo "ENABLE_CALENDAR_CONNECTIONS must be true or false." >&2; exit 2 ;; esac
case "${CALENDAR_ACTIONS}" in true|false) ;; *) echo "ENABLE_CALENDAR_ACTIONS must be true or false." >&2; exit 2 ;; esac
case "${GMAIL_ENABLED}" in true|false) ;; *) echo "ENABLE_GMAIL_CONNECTIONS must be true or false." >&2; exit 2 ;; esac
case "${GMAIL_DRAFTS}" in true|false) ;; *) echo "ENABLE_GMAIL_DRAFTS must be true or false." >&2; exit 2 ;; esac

if [ "${CALENDAR_ACTIONS}" = "true" ] && [ "${CALENDAR_ENABLED}" != "true" ]; then
  echo "ENABLE_CALENDAR_ACTIONS=true requires ENABLE_CALENDAR_CONNECTIONS=true." >&2
  exit 2
fi
if [ "${GMAIL_DRAFTS}" = "true" ] && [ "${GMAIL_ENABLED}" != "true" ]; then
  echo "ENABLE_GMAIL_DRAFTS=true requires ENABLE_GMAIL_CONNECTIONS=true." >&2
  exit 2
fi

# Calendar and Gmail are opt-in. The default rollout keeps both paths disabled
# so Telegram/Trip Watch can be deployed before OAuth consent and provider
# reread verification are complete. Each enabled path fails closed before any
# Cloud Run mutation when its OAuth contract is incomplete.
if [ "${CALENDAR_ENABLED}" = "true" ]; then
  : "${CALENDAR_OAUTH_CLIENT_ID:?set CALENDAR_OAUTH_CLIENT_ID when enabling Calendar}"
  : "${CALENDAR_REDIRECT_URI:?set CALENDAR_REDIRECT_URI when enabling Calendar}"
fi
if [ "${GMAIL_ENABLED}" = "true" ]; then
  : "${GMAIL_OAUTH_CLIENT_ID:?set GMAIL_OAUTH_CLIENT_ID when enabling Gmail}"
  : "${GMAIL_REDIRECT_URI:?set GMAIL_REDIRECT_URI when enabling Gmail}"
fi

GOOGLE_CONNECTION_ENV="ENABLE_CALENDAR_CONNECTIONS=${CALENDAR_ENABLED},ENABLE_CALENDAR_ACTIONS=${CALENDAR_ACTIONS},ENABLE_GMAIL_CONNECTIONS=${GMAIL_ENABLED},ENABLE_GMAIL_DRAFTS=${GMAIL_DRAFTS}"
GOOGLE_CONNECTION_SECRETS=""
if [ "${CALENDAR_ENABLED}" = "true" ]; then
  GOOGLE_CONNECTION_ENV="${GOOGLE_CONNECTION_ENV},CALENDAR_CLIENT_ID=${CALENDAR_OAUTH_CLIENT_ID},CALENDAR_CLIENT_SECRET_RESOURCE_NAME=${CALENDAR_OAUTH_CLIENT_SECRET_RESOURCE_NAME},CALENDAR_REDIRECT_URI=${CALENDAR_REDIRECT_URI},CALENDAR_ID=primary,OAUTH_REFRESH_TOKENS_SECRET_RESOURCE_NAME=${OAUTH_REFRESH_TOKENS_SECRET_RESOURCE_NAME}"
  GOOGLE_CONNECTION_SECRETS=",CALENDAR_OAUTH_SIGNING_KEY=${CALENDAR_OAUTH_SIGNING_KEY_SECRET}:latest"
fi
if [ "${GMAIL_ENABLED}" = "true" ]; then
  GOOGLE_CONNECTION_ENV="${GOOGLE_CONNECTION_ENV},GMAIL_CLIENT_ID=${GMAIL_OAUTH_CLIENT_ID},GMAIL_CLIENT_SECRET_RESOURCE_NAME=${GMAIL_OAUTH_CLIENT_SECRET_RESOURCE_NAME},GMAIL_REDIRECT_URI=${GMAIL_REDIRECT_URI},OAUTH_REFRESH_TOKENS_SECRET_RESOURCE_NAME=${OAUTH_REFRESH_TOKENS_SECRET_RESOURCE_NAME}"
  GOOGLE_CONNECTION_SECRETS="${GOOGLE_CONNECTION_SECRETS},GMAIL_OAUTH_SIGNING_KEY=${GMAIL_OAUTH_SIGNING_KEY_SECRET}:latest"
fi

if [ "${APPLY:-false}" != "true" ]; then
  echo "Preflight only: no Google Cloud resource was changed."
  echo "Worker: ${WORKER_SERVICE} (IAM-authenticated, ingress all)"
  echo "Edge: ${EDGE_SERVICE} (public minimal ingress)"
  echo "Trip Watch=${WATCH_ENABLED}, Judge mode=${JUDGE_ENABLED}, shared Vertex calls/day=${JUDGE_DAILY_CALLS}, per user=${JUDGE_DAILY_CALLS_PER_USER}"
  echo "Calendar=${CALENDAR_ENABLED}/${CALENDAR_ACTIONS}, Gmail=${GMAIL_ENABLED}/${GMAIL_DRAFTS}"
  echo "Gemini model=${GEMINI_MODEL_ID}"
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
  --update-env-vars="APP_ROLE=worker,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=global,GOOGLE_GENAI_USE_VERTEXAI=true,GEMINI_MODEL_ID=${GEMINI_MODEL_ID},PUBSUB_TOPIC_ID=trip-disruptions,PUBSUB_COMMAND_TOPIC_ID=${COMMAND_TOPIC_ID},PUBSUB_TRANSPORT=google,PROCESS_EVENTS_INLINE=false,ENABLE_TRIP_WATCH=${WATCH_ENABLED},TRIP_WATCH_MAX_CHECKS_PER_TICK=${WATCH_MAX_CHECKS},ENABLE_JUDGE_MODE=${JUDGE_ENABLED},JUDGE_DAILY_VERTEX_CALLS=${JUDGE_DAILY_CALLS},JUDGE_DAILY_VERTEX_CALLS_PER_USER=${JUDGE_DAILY_CALLS_PER_USER},JUDGE_MAX_OUTPUT_TOKENS=${JUDGE_MAX_OUTPUT},${GOOGLE_CONNECTION_ENV}" \
  --update-secrets="TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN_SECRET}:latest,TELEGRAM_WEBHOOK_SECRET=${TELEGRAM_WEBHOOK_SECRET_SECRET}:latest,APPROVAL_CALLBACK_SIGNING_KEY=${APPROVAL_CALLBACK_SIGNING_KEY_SECRET}:latest${GOOGLE_CONNECTION_SECRETS}"

WORKER_URL="$(gcloud run services describe "${WORKER_SERVICE}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"

gcloud run deploy "${EDGE_SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --image="${IMAGE_URI}" \
  --service-account="${EDGE_SERVICE_ACCOUNT}" \
  --ingress=all \
  --allow-unauthenticated \
  --update-env-vars="APP_ROLE=edge,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=global,WORKER_BASE_URL=${WORKER_URL}" \
  --update-secrets="TELEGRAM_WEBHOOK_SECRET=${TELEGRAM_WEBHOOK_SECRET_SECRET}:latest"

gcloud run services add-iam-policy-binding "${WORKER_SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --member="serviceAccount:${EDGE_SERVICE_ACCOUNT}" \
  --role="roles/run.invoker"

gcloud run services add-iam-policy-binding "${WORKER_SERVICE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --member="serviceAccount:${PUBSUB_INVOKER_SERVICE_ACCOUNT}" \
  --role="roles/run.invoker"

echo "Deployment boundary applied. Telegram webhook registration is deliberately separate."

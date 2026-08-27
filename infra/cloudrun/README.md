# Milestone 7 Cloud Run boundary

This directory is a deployment preflight, not evidence that a deployment happened.
`deploy-m7.sh` exits without mutation unless `APPLY=true` is explicitly supplied.

## Runtime roles

- `APP_ROLE=edge`: public routes are only `/healthz`, `/telegram/webhook`, the Gemini
  connection page, and its completion proxy. The edge validates the Telegram webhook
  secret and invokes the worker with a Google-signed Cloud Run ID token.
- `APP_ROLE=worker`: loads internal disruption/recovery routes and the private Gemini
  connection completion route. Telegram onboarding is available only at the
  IAM-protected `/internal/telegram/webhook` path for the edge forward; the public
  `/telegram/webhook` path is not loaded.
- `APP_ROLE=all`: local deterministic development only. Never use it for either production
  Cloud Run service.

## Exact secret and environment names

Edge:

- `APP_ROLE=edge`
- `WORKER_BASE_URL`
- `TELEGRAM_WEBHOOK_SECRET` from Secret Manager
- `GOOGLE_CLOUD_PROJECT`

Worker:

- `APP_ROLE=worker`
- `TELEGRAM_BOT_TOKEN` from Secret Manager
- `TELEGRAM_WEBHOOK_SECRET` from Secret Manager
- `APPROVAL_CALLBACK_SIGNING_KEY` from Secret Manager
- existing Google Cloud, Pub/Sub, Gemini, Firestore, BYOK, and feature settings as needed

Optional Google integrations use the same worker identity and remain feature-flagged until
explicitly verified:

- Calendar: `ENABLE_CALENDAR_CONNECTIONS`, `ENABLE_CALENDAR_ACTIONS`, client ID, client-secret
  resource, 32+ byte signing key, dedicated OAuth refresh-token secret resource, and the public
  Calendar callback URL.
- Gmail: `ENABLE_GMAIL_CONNECTIONS`, `ENABLE_GMAIL_DRAFTS`, client ID, client-secret resource,
  32+ byte signing key, dedicated OAuth refresh-token secret resource, and the public Gmail callback URL. Gmail requests only `gmail.compose`
  and creates drafts; it never reads inbox messages or sends email.

No secret value belongs in this repository, a command transcript, or a Cloud Run plain
environment variable.

## IAM boundary

- Proposed edge identity: `trip-agent-edge@tripagent-505715.iam.gserviceaccount.com`.
- Existing worker identity: `trip-agent-runtime@tripagent-505715.iam.gserviceaccount.com`.
- Existing Pub/Sub identity:
  `trip-agent-pubsub-invoker@tripagent-505715.iam.gserviceaccount.com`.
- `trip-recovery-edge` is the only unauthenticated Cloud Run service.
- `trip-recovery-agent` keeps `--no-allow-unauthenticated`. Its ingress may be `all` so
  Telegram edge and authenticated Pub/Sub can reach the service URL, but Cloud Run IAM is
  still mandatory.
- Only the edge service account and the existing Pub/Sub push identity receive
  service-level `roles/run.invoker` on the worker.
- The worker runtime identity retains its existing least-privilege Firestore, Vertex AI,
  Pub/Sub, logging, and approved secret-access roles. The edge identity receives no
  Firestore, Vertex AI, provider-execution, or recovery authority.
- Before enabling Calendar or Gmail, grant the worker access to the OAuth client-secret and to
  the user-scoped refresh-token secrets created by the OAuth flow. The public edge only forwards
  the callback; it never receives refresh tokens or provider execution authority.

## Operational signal

Terminal Telegram failures persist notification state as `BLOCKED` and emit the structured
ERROR event `TELEGRAM_DELIVERY_BLOCKED`. Unknown send outcomes emit
`TELEGRAM_DELIVERY_UNKNOWN`; retryable pre-send failures emit a warning. Before a live
pilot, create a Cloud Monitoring log-based alert on the blocked event and route it to a
participant-owned notification channel. That cloud mutation is intentionally not performed
by this local milestone.

Webhook registration, secret creation, Cloud Run deployment, IAM changes, and alert-policy
creation remain separate approval-gated operations.

## Trip Watch scheduler preflight

`configure-trip-watch.sh` is also inert unless `APPLY=true`. It enables the Cloud Scheduler
API, creates `trip-agent-watch-scheduler`, grants it only `roles/run.invoker` on the private
worker, and creates one OIDC-authenticated job calling `/internal/watch/tick` every 30 minutes.
The scheduler has no Firestore, Gemini, Telegram, Secret Manager, or Pub/Sub permissions.

Before applying it, deploy the worker revision with `ENABLE_TRIP_WATCH=true`, a Gemini Flash
model, and a low `TRIP_WATCH_MAX_CHECKS_PER_TICK` value. Set a billing alert separately because
it belongs to the billing-account owner and cannot be safely assumed by this script.

## Billing budget preflight

`configure-billing-budget.sh` is inert unless `APPLY=true` and an explicit
`BUDGET_AMOUNT_USD` is provided. It scopes a monthly alert budget to the project, uses
50%/80%/100% current-spend and 80% forecasted-spend thresholds, and reuses an existing
budget with the same name. A budget sends notifications; it does not hard-stop usage, so
the application Vertex guardrail remains mandatory. Example preflight:

```sh
PROJECT_ID=tripagent-505715 \
BILLING_ACCOUNT=011DA3-F8EB93-A5D676 \
BUDGET_AMOUNT_USD=25 \
infra/cloudrun/configure-billing-budget.sh
```

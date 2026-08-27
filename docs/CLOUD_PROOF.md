# Google Cloud execution proof

Verified on 2026-08-24 in project `tripagent-505715`.

## Deployed runtime

- Cloud Run service: `trip-recovery-agent`
- Region: `europe-west3`
- Ready revision: `trip-recovery-agent-00019-w5s`
- Edge revision: `trip-recovery-edge-00003-hz2`
- Rollback Cloud Function revision: `trip-recovery-edge-fn-00004-wiz`
- Image tag: `autonomy-command-resume-20260824-hardened`
- Image digest: `sha256:9cb1e0d2564d3d28031f337e247d14db63205a96ad9856602f0badc549da0a15`
- Ingress: all (IAM authentication remains required)
- Unauthenticated invocation: disabled
- Runtime identity: `trip-agent-runtime@tripagent-505715.iam.gserviceaccount.com`
- Runtime limits: 1 CPU, 1 GiB memory, concurrency 4, timeout 300 seconds, max 1 instance
- Vertex AI model: `gemini-3.5-flash`
- Vertex model validation: non-generative `countTokens` returned HTTP 200 for
  `gemini-3.5-flash` in the configured `global` and `europe-west3` endpoints on 2026-08-24.
- Container source: root `Dockerfile`, built by Cloud Build and stored in Artifact Registry

Revision `00019-w5s` contains the evidence-gated EU261/UK261/DOT claim flow, multimodal
media fallback guard, hotel-only Booking/Airbnb intake hardening, duplicate-safe hotel
merges, Gemini `response.parsed`, 12 MiB/magic-byte media validation, owner-bound
Telegram `Review compensation`, and the autonomous planning path. A traveler can request
a bounded itinerary shortlist; options are explicitly estimates until the traveler sends
real booking evidence. Judge-mode chat, planning and Trip Watch share one atomic
  project-wide Vertex quota bucket (20 calls/day, 5 calls/user/day, 256 output tokens/call). The
  worker remains
private at the IAM layer.

The runtime service account has only the application permissions needed for this milestone:

- `roles/datastore.user`
- `roles/aiplatform.user`
- `roles/pubsub.publisher`

The three runtime roles are project-level and the three production secrets are resource-scoped
to the runtime/edge service accounts. A direct unauthenticated worker probe returns HTTP 403.
The Cloud Billing Budget API is enabled for the project, but a read-only listing currently
returns no budget resources; a billing-owner threshold/alert still needs to be configured.

## Authenticated Pub/Sub delivery

The existing `trip-disruptions-sub` subscription is configured as an authenticated push subscription:

- Topic: `trip-disruptions`
- Push endpoint: `https://trip-recovery-agent-oy6lnosdfq-ey.a.run.app/internal/pubsub/disruptions`
- OIDC identity: `trip-agent-pubsub-invoker@tripagent-505715.iam.gserviceaccount.com`
- OIDC audience: the canonical Cloud Run service URL
- Ack deadline: 300 seconds

The invoker identity has `roles/run.invoker` only on this Cloud Run service.

## End-to-end event

Control event:

- External event ID: `cloud-e2e-20260816-001`
- Pub/Sub message ID: `20270329119054794`
- Trip: `demo-trip-001`
- Disrupted flight: `LO351`
- Arrival delay: 105 minutes
- Stable incident: `incident-91de7225ef7be9ebff2e7cc6`

Observed execution trace:

1. Pub/Sub delivered an authenticated POST to Cloud Run.
2. Cloud Run logged `IMPACT_ANALYSIS_STARTED` with correlation ID `ea021f27-b48a-5b45-8aaa-048c4f4ca1e2`.
3. Google ADK logged a Vertex AI request using `gemini-3.5-flash`.
4. Vertex AI returned a model response.
5. Cloud Run logged `IMPACT_ANALYSIS_COMPLETED`.
6. The push request completed with HTTP 200 in approximately 7.4 seconds.
7. Firestore persisted the processed event as `COMPLETED` and the incident as `PLANNING`.

Persisted authoritative result:

- `connection_feasible`: `false`
- `arrival_delta_minutes`: `105`
- affected items: `flight-lh1792`, `airport-transfer`, `hotel-arrival`
- model ID: `gemini-3.5-flash`
- prompt version: `impact-interpretation-v1`

## Cloud idempotency proof

The same event ID was published again as Pub/Sub message `21032164587021143`.

- Cloud Run acknowledged the duplicate with HTTP 200 in approximately 0.15 seconds.
- Firestore still reports one completed processed-event record.
- `attempts` remains `1`.
- The stable incident ID and original completion timestamp are unchanged.
- No second Gemini request was made.

## Deployment correction discovered

The original Dockerfile was under `backend/`, so a root source deployment did not see it and Cloud Build selected a buildpack command that attempted to load `app:app`. The Dockerfile now lives at the repository root and explicitly starts `app.runtime:app` with `PYTHONPATH=/app/backend`; the runtime selects the edge or worker application from `APP_ROLE`.

## Re-verification commands

These commands require an authenticated `gcloud` CLI with project `tripagent-505715` selected.

```bash
gcloud run services describe trip-recovery-agent --region=europe-west3
gcloud pubsub subscriptions describe trip-disruptions-sub
gcloud run services logs read trip-recovery-agent --region=europe-west3 --limit=100
```

## Telegram edge proof

- Active edge: Cloud Run service `trip-recovery-edge` (`europe-west3`), revision
  `trip-recovery-edge-00003-hz2`, with `APP_ROLE=edge` and the hardened image digest above.
- The Cloud Function adapter `trip-recovery-edge-fn` remains deployed for rollback at revision
  `trip-recovery-edge-fn-00004-wiz`; it is hardened with the same streaming 1 MiB request cap,
  but is not the active Telegram webhook target.
- Active webhook URL: `https://trip-recovery-edge-oy6lnosdfq-ey.a.run.app/telegram/webhook`.
- The worker keeps Telegram behind the authenticated `/internal/telegram/webhook` boundary.
- A signed empty-update contract probe against the active edge returns HTTP `400 malformed
  Telegram update`, not `404`; an unauthenticated probe against the worker returns HTTP `403`.
- A previous 2026-08-24 read-only check confirmed the active Cloud Run URL and
  `pending_update_count=0`. The latest 2026-08-27 check reports a Telegram webhook delivery
  error (`500 Internal Server Error`) while the live service is still on the prior image; this
  must be cleared by rolling out and re-verifying the hardened build. No synthetic user update
  was sent.

## Safety boundaries for the two user-facing differentiators

- Gemini receives the original PDF/image/PKPASS bytes for multimodal extraction. The intake
  path caps media at 12 MiB, resolves generic Telegram MIME values from magic bytes, and the
  offline fallback reads only bounded, explicit PDF text-literals (or pass metadata), never
  arbitrary binary bytes. It refuses a media intake without explicit timezone-aware
  departure/arrival or hotel check-in/check-out times instead of fabricating a booking. The
  PDF text-layer fallback is covered locally and is pending the next Cloud Run rollout.
- The pure EU261/UK261 calculator remains available for deterministic statutory tests. The
  incident/claim path is stricter: `airline_fault=true` must be present in grounded context;
  missing or false cause attribution holds the claim for human review.
- After a recovered real incident, the owner-bound `Review compensation` callback opens an
  escaped, evidence-linked, review-only claim draft. It never sends a claim automatically.

This proof covers the deployed Telegram edge and the current MVP path. A real user still needs
to send `/start` once to exercise Telegram's live onboarding UI; no synthetic user update was
sent during rollout.

## Post-revision hardening rollout

The hardened worktree includes streaming request-size enforcement in both public forwarding
layers and bounded `.pkpass` archive expansion. Both controls are covered by regression tests.
The latest successful Cloud Build artifact is not yet live; the last verified Cloud Run edge is
`00003-hz2` and the rollback Cloud Function is `00004-wiz`.

## Autonomous judge path

The judge deployment is not a chat-only surface. After onboarding, `Plan a trip` creates a
persistent planning request, produces three bounded options, and records a selected option
without pretending to book anything. Trip Watch then evaluates the saved trip against scoped
watchpoints, validates public sources, computes downstream impact, and executes only
policy-approved reversible actions. Irreversible, ambiguous, penalty-bearing, or over-budget
actions remain approval-gated. A shared Firestore quota bucket and maxScale=1 protect the
project's Vertex credits when multiple judges use the bot.

## Durable Trip Watch delivery

Trip Watch records each accepted grounded fact as a pending Firestore delivery before publishing
to Pub/Sub. `published_at` is written only after acknowledgement, so a transient publish failure
is replayed on the next scheduler tick. The deterministic event fingerprint excludes this
operational field (and polling timestamps such as `observed_at` and `source_updated_at`), keeping
repeated polls and replays idempotent across restarts. Official flight signals are also required
to resolve to an HTTPS host in the watchpoint's airline allow-list; a model-labelled official URL
outside that list is downgraded/rejected and cannot authorize recovery. The tick isolates per-watchpoint
failures and exposes `failed_watchpoints` rather than stopping the whole loop. Saving a planning
option also persists `planning_saved_at` with compare-and-set semantics, so a selected plan
survives a worker restart until real booking evidence is forwarded.

## Durable approval resume delivery

An approval callback does not execute recovery inside the Telegram request. It atomically
commits an owner-bound continuation command to the Firestore outbox, then attempts a fast
publish to the separate `trip-workflow-commands` Pub/Sub topic. The authenticated push
subscription invokes `/internal/pubsub/commands`, where the workflow resumes from its
persistent plan, claims each command idempotently, executes only policy-approved actions,
verifies them, and sends the final Telegram status. `/internal/watch/tick` also sweeps the
same outbox, so a worker or Pub/Sub outage cannot turn a successful approval into a lost
recovery. Duplicate pushes reuse the same command ID and do not repeat completed effects.

The last documented verified rollout was `trip-recovery-agent-00019-w5s` with image digest
`sha256:9cb1e0d2564d3d28031f337e247d14db63205a96ad9856602f0badc549da0a15`. It was Ready with
100% traffic, maxScale 1, and unauthenticated worker invocation returned HTTP 403. The
public Cloud Run edge was `trip-recovery-edge-00003-hz2` with the same digest and explicit
`APP_ROLE=edge`. Telegram points to
`https://trip-recovery-edge-oy6lnosdfq-ey.a.run.app/telegram/webhook`.
The current worktree contains additional privacy/deletion/simulator-auth/security-header
hardening and has a newer successful Cloud Build artifact, but those changes are pending an
owner-approved Cloud Run rollout and must not be presented as live until re-verified. On
2026-08-27, Telegram `getWebhookInfo` reported `last_error_message=Wrong response from the
webhook: 500 Internal Server Error`; the live valid-update smoke test remains outstanding.
`trip-watch-tick` Scheduler job remains ENABLED every 30 minutes in `europe-west3` and targets
the authenticated `/internal/watch/tick` route.

The approval continuation wiring is live: topic `trip-workflow-commands`, ACTIVE push
subscription `trip-workflow-commands-sub`, 300-second acknowledgement deadline, and OIDC
audience `https://trip-recovery-agent-oy6lnosdfq-ey.a.run.app`. Cloud Run IAM grants the
Pub/Sub invoker service account `roles/run.invoker`; the worker runtime has
`roles/pubsub.publisher`.

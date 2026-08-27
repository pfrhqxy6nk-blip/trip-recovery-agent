# Controlled first-user runbook

This runbook deliberately separates local readiness from approval-gated external actions.
Do not paste any credential into this repository, Telegram, a ticket, or a chat transcript.

## Proven locally

- Telegram onboarding, policy activation, pilot-trip creation, proactive awareness,
  verified safe actions, approval/details/stop, persistent resume, and final recovery.
- Duplicate events/callbacks, expired leases, provider response loss, stale authority,
  cross-user/chat callbacks, notification redelivery, and plan-revision idempotency.
- BYOK handoff, expiry/replay/ownership checks, metadata-only persistence, explicit
  per-traveler selection, disconnect, and no fallback to system Vertex.
- Calendar and Gmail connection contracts, PKCE/state binding, refresh-token Secret Manager
  storage, revoke/disconnect, and provider reread verification are covered locally. Both live
  paths are feature-flagged off until the project's OAuth client and callbacks are configured.

## Inputs required from the project owner

1. A Telegram bot created through BotFather and its token stored in Google Secret Manager.
2. Two newly generated random server secrets: a 16+ byte Telegram webhook secret and a
   32+ byte approval callback signing key. A separate 16+ byte simulator secret is needed
   only for the controlled pilot trigger.
3. The owner has approved rollout. Current live revisions and the webhook contract are
   recorded in [CLOUD_PROOF.md](CLOUD_PROOF.md); no Devpost submission was made. The last
   verified worker is `trip-recovery-agent-00019-w5s` and remains IAM-private; the last verified
   Telegram edge is `trip-recovery-edge-00003-hz2`; the rollback Cloud Function is
   `trip-recovery-edge-fn-00004-wiz`. The current worktree hardening still requires an approved
   rollout and a fresh Telegram smoke test.
4. Approval to grant the edge service permission to add/destroy only the BYOK secret
   versions it owns, and the worker permission to access those versions.
5. Judge mode uses the project's bounded Vertex AI budget; the first tester does not need
   to enter a Gemini key. BYOK remains an optional separate path and must never receive a
   key through Telegram.

## Controlled acceptance sequence

1. Confirm the immutable revisions and image digest in [CLOUD_PROOF.md](CLOUD_PROOF.md).
2. Configure environment variables from Secret Manager, enable BYOK and pilot-trip mode,
   and keep the simulator disabled unless the signed demo trigger is required.
3. Run the complete read-only gate from the repository root:
   `set -a; source .env; set +a; RUN_LIVE_CHECK=1 scripts/run_submission_gate.sh`. It runs
   the local regression suite and confirms the bot, webhook URL, zero pending updates, and
   signed edge validation without sending a user update. Then open the bot and send `/start`.
   The first response should be a welcome message with `Start my trip` and `Plan a trip`;
   both buttons lead through the short safety setup, followed by plain English chat. There is
   no public demo path. The hidden deterministic replay remains available only to the
   controlled judge harness. A fresh owner delivery is still required to prove the English
   onboarding UI in a real Telegram chat.
4. Complete onboarding, choose `Plan a trip` or forward a real booking artifact. A natural
   planning request such as `I want to go to Paris for 6 nights, budget €600` returns a
   flexible-date shortlist immediately. A follow-up such as `from Kyiv, 2026-10-10` refines
   it; all options remain estimates until the traveler forwards a real booking. Then inject
   the canonical signed disruption for the controlled pilot. If planning input is incomplete
   or a saved draft has changed, the webhook sends a recoverable English chat message instead
   of leaving the traveler with a blank response; authorization and ownership failures still
   fail closed.
5. Verify in Telegram and Firestore that awareness precedes effects, three safe actions are
   verified, the €34 flight waits for approval, and approval resumes to `RECOVERED`.
6. Repeat with a duplicate event/callback and one interrupted worker; confirm no repeated
   provider effect or Gemini call.
7. Run **Stop recovery** in a fresh incident and confirm the pending flight effect is absent.

## Not part of the first controlled pilot

- Live airline/hotel/museum monitoring or guaranteed real-time coverage.
- Real booking changes, payments, Duffel orders, Google Calendar writes, or Gmail sends.
- Public multi-user launch, SLA, support, billing, or production incident response.

Those require provider credentials, provider-specific webhooks/polling, OAuth consent,
  additional live verification, and separate approval. The UI must continue labeling demo
  effects as pilot effects until those adapters are proven.

## Google Calendar and Gmail production gate

To enable the real Calendar path, configure `ENABLE_CALENDAR_CONNECTIONS=true` and, only after
approval, provide the OAuth client ID, the client-secret resource in Secret Manager, a 32+ byte
server signing key, and the exact HTTPS `CALENDAR_REDIRECT_URI`. `ENABLE_CALENDAR_ACTIONS=true`
then routes only Calendar actions for connected travelers to the Google adapter; other actions
remain on their existing provider. A missing or revoked connection is a safe terminal pause —
the agent never claims that a Calendar update happened.

Gmail uses an equally explicit, but narrower, path. Configure
`ENABLE_GMAIL_CONNECTIONS=true`, `GMAIL_CLIENT_ID`, a Secret Manager client-secret resource,
a distinct 32+ byte `GMAIL_OAUTH_SIGNING_KEY`, and the exact HTTPS
`GMAIL_REDIRECT_URI`. Add both callback URLs to the same Google OAuth web client if it is shared:

1. `https://<public-edge-domain>/connections/calendar/callback`
2. `https://<public-edge-domain>/connections/gmail/callback`

Store user refresh tokens as immutable versions of a separate Secret Manager resource (for
example `projects/tripagent-505715/secrets/trip-agent-oauth-refresh-tokens`). Do not reuse the
Gemini BYOK secret or the OAuth client-secret resource for traveler tokens. The worker needs
`Secret Manager Secret Accessor` and `Secret Manager Secret Version Adder` on that resource.

Then enable `ENABLE_GMAIL_DRAFTS=true`. The Gmail consent scope is `gmail.compose`, used here
solely to create a draft. This implementation has no inbox-reading or send endpoint. For a hotel
with a contact email shown in the private itinerary draft before it is saved, it creates and
rereads a late-arrival **draft** marked by an idempotency key.
The traveler opens Gmail and sends it themselves. A missing contact email or disconnected account
is a terminal pause — it is never presented as “hotel notified.”

The Cloud Run worker identity needs Secret Manager access to the OAuth client-secret and to
per-user OAuth refresh-token secrets created by the connection flow. Validate a Calendar update
and a Gmail draft with a non-sensitive test booking before enabling either flag for beta users.

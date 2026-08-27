# Live Telegram incident: `/start` appears empty

## Evidence

The bot token is valid (`getMe` returned `Trip_Watch`). The previous webhook path used the
Cloud Function edge and reported `404 Not Found` for Telegram updates. The active webhook now
uses the hardened Cloud Run edge; the Cloud Function remains available only as a rollback path.

## Root cause

The private Cloud Run worker exposes Telegram at `/internal/telegram/webhook`.
The earlier edge adapter was forwarding Telegram updates to the worker's public
`/telegram/webhook` path, which is intentionally not mounted for the worker role. The worker
therefore returned 404; Telegram never received the onboarding view or its inline keyboard.

The local adapter now forwards to `/internal/telegram/webhook`, and a contract test
locks this boundary in place (`backend/tests/test_cloud_function_contract.py`).

## Approval-gated recovery

The last verified Cloud Run worker and edge were `trip-recovery-agent-00019-w5s` and
`trip-recovery-edge-00003-hz2`; the rollback Cloud Function is `trip-recovery-edge-fn-00004-wiz`.
Telegram still points to the Cloud Run edge URL, whose signed malformed-update contract returns
the expected HTTP `400`; the private worker returns HTTP `403` without IAM. On 2026-08-27,
`getWebhookInfo` reported `pending_update_count=0` but also
`last_error_message=Wrong response from the webhook: 500 Internal Server Error` while the live
services remain on the prior image. Roll out the hardened build and then send `/start` from a
real Telegram account to exercise onboarding. No synthetic user update was sent.

## Latest read-only verification

On 2026-08-24, the active Cloud Run edge was probed without sending a Telegram user update:

- A signed empty JSON body to `/telegram/webhook` returned HTTP `400` with
  `malformed Telegram update`, proving the edge-to-private-worker route is live.
- An unauthenticated request to the worker returned HTTP `403`, proving the IAM boundary.
- The bot identity remains valid and Telegram reports the Cloud Run URL and
  `pending_update_count=0`; the same check reports a recorded webhook `500 Internal Server
  Error`, so valid-update delivery is not yet proven.

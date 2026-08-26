# Trip Recovery Agent — production and submission gate

This is the final checklist for the controlled hackathon release. It is intentionally
written in English because the Telegram and landing-product experience is English-first.
No public deployment or Devpost submission is performed by this checklist.

## Engineering evidence

- [x] Backend test suite passes.
- [x] `mypy` passes for the application and tests.
- [x] Ruff, Python compilation, shell syntax, and `git diff --check` pass.
- [x] Landing production build passes.
- [x] Sites packaging tests pass.
- [x] Browser smoke test passes in a real Chromium process.
- [x] Production dependency audit reports zero high-severity vulnerabilities.
- [x] First-user Telegram regression covers English welcome buttons, policy activation,
  keyboard removal, plain-chat handoff, and natural-language planning.
- [x] The full local gate is reproducible with `scripts/run_submission_gate.sh`; adding
  `RUN_LIVE_CHECK=1` runs the read-only Telegram/Cloud Run contract in the same command.
- [x] Product-facing copy is English-only across the landing, Telegram responses, and
  generated compensation letters; legacy `subject_ru`/`body_ru` fields are English aliases
  for schema compatibility.
- [x] Cloud Run deployment preflight is dry-run safe: worker IAM-authenticated, edge public,
  worker max instances `1`, Scheduler cadence `30m`, and shared/per-user Vertex budgets bounded.

## Live Telegram acceptance

The bot identity and webhook URL resolve, and Telegram currently reports
`pending_update_count=0`; however, the latest read-only check on 2026-08-27 also reported
`last_error_message=Wrong response from the webhook: 500 Internal Server Error`. Treat the
live path as not green until the hardened image is rolled out and a real owner `/start` smoke
test clears that error.

Repeatable check from the repository root (loads only the local untracked `.env`):
`set -a; source .env; set +a; scripts/verify_live_contract.sh`.

The complete owner-side command is:
`set -a; source .env; set +a; RUN_LIVE_CHECK=1 scripts/run_submission_gate.sh`.

Run this with a fresh Telegram account against the current worker revision:

1. Send `/start` and confirm the English welcome view contains `Start my trip` and
   `Plan a trip`.
2. Complete the autonomy policy. After activation, ordinary conversation must not show
   a persistent keyboard.
3. Send: `I want to go to Paris for 6 nights, budget €600`.
4. Confirm the agent returns a flexible-date shortlist immediately with three clearly labelled
   estimates and HTTPS source links when Vertex Search grounding is available. Then send
   `from Kyiv, 2026-10-10` and confirm the saved brief is refined into date-specific options.
5. Forward a real PDF ticket, booking email, screenshot, or Apple Wallet pass. Confirm the
   extracted itinerary is persisted without inventing a missing flight or hotel.
6. Trigger the signed pilot disruption. Confirm the order is awareness → impact → safe
   actions → approval request → persistent resume → verified recovery.
7. Replay the event and callback. Confirm idempotency prevents duplicate effects or messages.
8. Run `Stop recovery` once and confirm the pending consequential action is not executed.

## Google Cloud gates

- [ ] Roll out the latest hardened image and record its worker/edge revisions and digest in
  `docs/CLOUD_PROOF.md`. The current live services still point at the prior verified image;
  local hardening is covered by the gate below but is not yet a live claim.
- [ ] Confirm Telegram `getWebhookInfo` has zero pending updates and no webhook error after the
  fresh owner smoke test. The 2026-08-27 read-only check showed `pending_update_count=0` but a
  current `500 Internal Server Error`; valid `/start` delivery is still unproven.
- [x] Confirm the worker has only the required Secret Manager, Firestore, Pub/Sub, and
  Vertex permissions; project IAM shows only `roles/datastore.user`, `roles/aiplatform.user`,
  and `roles/pubsub.publisher` for the runtime identity, secrets are resource-scoped, and an
  unauthenticated worker probe returns HTTP 403.
- [ ] Confirm a billing budget alert exists (the Billing Budget API is enabled, but the current
  read-only listing returns no budget resources). The guarded preflight/apply helper is
  `infra/cloudrun/configure-billing-budget.sh`; the explicit owner-selected amount is still
  required. The shared Vertex daily guardrail is active at 20 project calls/day and 5 calls/user/day.
- [x] Confirm `gemini-3.5-flash` is reachable in the configured global/europe-west3 Vertex
  endpoint (`countTokens` returned HTTP 200); confirm final hackathon eligibility against the
  current Devpost rules before submission.
- [ ] Keep real booking mutations, payments, Calendar writes, Gmail drafts, and external
  hotel/transfer actions disabled unless their provider-specific credentials and reread
  verification are tested.

## Security gate

- [ ] Obtain Codex Security TAC access and wait for a sealed canonical report.
- [ ] Resolve every high or critical finding before submission.
- [x] Re-run the local secret-pattern scan and `npm audit` after the final credential/config change;
  the tracked-file pattern scan is clean and `npm audit --audit-level=high` reports zero vulnerabilities.
- [x] A sealed Codex Security report is available for the scanned snapshot (scan ID
  `5e56645d-fdd2-4d5c-9df9-41b750283967`). It contains two medium findings; both are
  fixed in the current worktree and covered by regression tests.
- [x] Public request forwarding enforces the 1 MiB cap while streaming in both edge layers.
- [x] Offline `.pkpass` parsing bounds entry count, aggregate expansion, compression ratio, and `pass.json` reads.

The sealed report was created before the final hardening edits. Both medium findings are fixed in
the current worktree and covered by regression tests. Record the new Cloud Run revisions after
the owner-approved rollout before describing these fixes as deployed.

TAC enrollment: <https://chatgpt.com/cyber>.

## Devpost fields owned by the project owner

Do not guess these values in source control:

- hosted landing URL;
- repository URL and visibility/access for judges;
- demo video URL;
- submitter type and country of residence;
- project start date;
- final Google Cloud proof links.

The working draft is [devpost-submission.md](../devpost-submission.md). Replace its `TODO`
fields only after the owner confirms the real values. Do not publish from the repository.

## Product truth boundary

The agent can plan a trip, return Search-grounded or deterministic estimates, ingest real
booking artifacts, monitor supported trip facts, calculate impact, perform policy-safe pilot
actions, and resume after approval. Planning estimates are never bookings. External booking
changes and payments remain disabled until their providers are independently verified.

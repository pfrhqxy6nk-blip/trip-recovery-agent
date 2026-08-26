# Privacy, retention and safety boundary

Trip Recovery Agent is a hackathon-grade prototype. It is designed to minimize sensitive data
while still proving the autonomous workflow.

## Gemini data boundary

The impact interpreter receives only itinerary timing, public route metadata, bounded source
context and the deterministic impact summary. Telegram IDs, PNRs, email addresses, provider
IDs and raw provider payloads are excluded before the model call. The deterministic engine,
not the model, remains authoritative for feasibility, money and action policy.

## User deletion

An authenticated Telegram user can send `/delete_my_data`. The worker deletes that user's
trip graph, watchpoints, grounded signals, incidents, recovery plans/actions/attempts,
notifications, outbox records, drafts, expenses, policy, OAuth state and traveler profile.
Secret Manager versions referenced by Calendar, Gmail or BYOK connections are destroyed as
part of the same request when the configured service account has permission. If revocation
fails, the bot reports that explicitly so the user can revoke the connection in Google Account
security settings.

The deletion operation is owner-scoped and refuses to query or delete malformed records with
an empty owner ID. It is idempotent: a second request returns the normal reset confirmation.

## Retention and logs

The prototype does not claim a universal legal retention period. Firestore data remains until
the user deletes it or an operator applies the project retention policy. Cloud Logging may
contain operational event IDs, statuses and correlation IDs and follows the Google Cloud
project's configured log retention. Production launch requires a reviewed GDPR notice,
regional retention policy, export/delete audit trail and a KMS-backed PII design.

## Financial and action safety

Gemini has no payment authority. Booking and money actions are policy-gated, bounded by the
configured spending ceiling, idempotent and recorded with receipts. Duffel is quote-only in
the current release; no real airline order or payment is created by the demo.

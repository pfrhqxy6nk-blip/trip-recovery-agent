# User journey — Telegram-first Trip Recovery Agent

## First controlled traveler

1. The traveler opens the bot and presses **Start**. There is no dashboard or separate
   account form.
2. The bot binds the Telegram user and chat, then collects Calendar, service-message,
   reversible-change, spending (€0/€20/€50/€100), and mandatory-risk preferences.
3. After activation, the traveler opens **Connect Gemini**. The bot links to the official
   Google AI Studio key page and to a short-lived secure connection page.
4. The connection token stays in the URL fragment, is single-use, expires after ten
   minutes, and is bound to that Telegram user/chat. The API key is submitted only over
   HTTPS, validated without itinerary data, and stored in Secret Manager. Firestore holds
   only connection metadata and a one-way fingerprint.
5. The traveler chooses `Start my trip` or `Plan a trip`. They can forward a PDF ticket,
   booking email, screenshot, or Apple Wallet pass, or write a natural request such as
   “I want to go to Paris for 6 nights, budget €600”. The bot can return a flexible-date
   shortlist immediately, asks only for useful missing details, and accepts a follow-up such as
   “from Kyiv, 2026-10-10” to refine the options. Pre-booking results stay explicitly marked
   as estimates. It never asks for a passport or payment data.
   If a live provider fails, the watchpoint is marked degraded and the status view says that
   coverage is unknown; it is never presented as an on-time result.
6. The traveler can write naturally at any time: “what do you monitor?”, “weather”, “my trip
   status”, “add a trip”, or “connect Gemini”. These messages are read-only navigation and
   explanation; they cannot authorize a purchase or booking change. In the hackathon judge
   deployment, other explanatory questions can use a shared, project-funded Vertex AI demo
   quota; no judge key is requested or exposed.
7. A grounded, validated provider/Search signal or authenticated Pub/Sub event starts recovery. The bot
   proactively sends the disruption explanation before any provider action is allowed.

## The recovery interruption

For the canonical delay, the agent sends awareness first, executes and verifies the safe
policy-approved actions, then asks once for the €34 flight change because it exceeds the
€20 authority:

```text
Your Munich connection is no longer feasible.

I already handled and verified the safe transfer, hotel-arrival, and calendar updates.

Flight recovery: €34.00
Your automatic spending limit is lower, so I need your approval.

[Approve recovery] [Show details]
[Stop recovery]
```

The approval is bound to the current incident, plan hash/version, policy version, option,
amount/currency, quote expiry, user, chat, and opaque callback hash. A newer disruption
changes the trip's active incident and invalidates the older authority.

After approval, the persistent workflow resumes, rereads provider state, executes only the
approved action, verifies all effects and itinerary invariants, and edits the Telegram
message to `Trip recovered`. **Stop recovery** atomically declines the pending authority
and leaves the flight action untouched.

## Truthful current boundary

The complete controlled-pilot path is implemented and locally tested with deterministic
travel providers. The real Telegram Bot API gateway, BYOK Secret Manager adapter, Vertex
demo path, Firestore repository, Pub/Sub ingress, and secure connection page are present in
code, but activating them requires approved credentials, HTTPS deployment, IAM, and webhook
configuration.

Google Search-grounded public monitoring is now wired for user-owned watchpoints through the
private Scheduler worker. Only a trip-specific, validated official signal can start recovery;
ordinary news remains informational. Real airline/hotel/activity booking mutations are not
claimed yet: Duffel sandbox, Calendar OAuth, and provider webhooks remain later gated adapters.
Provider failures are persisted as bounded degraded state, and informational signals remain
pending until Telegram delivery is acknowledged, so the autonomous loop cannot silently lose a
traveler-facing update.

## Judge demo credit boundary

The deployed judge mode uses the project’s Vertex AI credits through the worker service
identity. It is intentionally bounded to 20 Gemini requests per UTC day across all users,
256 output tokens per request, and one Cloud Run worker instance. Onboarding buttons, trip
status, and monitoring explanations do not consume Vertex calls. When the shared daily
bucket is full—or Google project quota/billing is exhausted—the bot clearly says that live
Search is unavailable and labels any bounded offline shortlist as an estimate, never as
current availability. No user API key, payment method, or billing data is collected.

# First-user sources and monitoring plan

## Product boundary

Trip Recovery Agent does not claim to discover a traveler's bookings from the open web.
The traveler explicitly adds a booking, confirms the normalized itinerary, and can see the
freshness and coverage of every connected source.

Observation and recovery are separate:

1. An observation source reports a changed fact about an existing trip item.
2. The monitoring workflow validates and deduplicates that fact, then emits a normalized
   disruption event.
3. The existing recovery workflow determines downstream impact and only then searches or
   executes permitted recovery options.

## Delivery order

### Phase A — safe manual intake (local)

- Telegram starts a persistent manual trip draft after onboarding.
- The traveler adds one or more flights and an optional hotel with strict, documented
  time-zone-aware fields; booking references and raw documents are intentionally excluded.
- The bot displays the itinerary and requires an explicit `Save trip` click before the
  canonical trip graph is written.
- Drafts and final trips are owner/chat bound, compare-and-set versioned, and idempotent.

### Phase B — source contracts and deterministic monitoring (local)

- `MonitoringSubscription` and `ObservationSnapshot` are persisted, owner-bound contracts
  with source freshness, coverage level, and a snapshot-fingerprint dedupe key.
- Turn a changed, admissible snapshot into the existing `DisruptionEvent` only after its
  item/trip ownership and source binding are checked.
- Start with deterministic provider fixtures so replay, stale data, and duplicate
  snapshots are exhaustively testable.

Current behavior: saving an itinerary immediately creates focused Trip Watch watchpoints.
When the traveler has connected their own Gemini key, the private worker asks Google Search
grounding only about those public watchpoints. It starts at a 12-hour cadence more than seven
days out, narrows to 6-hourly / hourly near travel, and reaches 30-minute checks in the final
six hours. It never treats an ordinary web result as permission to modify a booking.
When a cited airport, weather, hotel, transfer, or activity signal is relevant but does not
meet the deterministic recovery boundary, it is sent to the traveler as a deduplicated
`WATCH_SIGNAL` Telegram notice with an open-source button; the itinerary remains unchanged.

### Phase C — one real flight source (approval-gated)

- Use a project-owned Amadeus production credential with an application-level request
  budget and adaptive polling. The test environment is not valid real-time proof.
- Schedule only due subscriptions: 6-hourly more than 48 hours before departure, hourly
  from 48 to 6 hours, 10-minute checks from 6 hours, and 3–5-minute checks while active.
- A failed or stale check is a `MONITORING_DEGRADED` fact, never evidence that the flight
  is on time.

Implementation status: the production-only Amadeus On-Demand Flight Status client now
authenticates using OAuth client credentials and normalizes a provider response into an
untrusted `ObservationSnapshot`. When `ENABLE_AMADEUS_FLIGHT_MONITORING=true`, saving a
trip explicitly binds each flight subscription to that source and the existing private
watch tick routes flight-status checks through Amadeus while keeping Search grounding for
airports, weather, hotels, transfers, and activities. The feature is disabled by default
and makes no calls until production credentials are supplied through the runtime's
secret-injection path; existing trips are not silently migrated into live monitoring.

### Phase D — transport and booking-change sources (after the flight vertical slice)

- Add a GTFS-Realtime adapter only for agencies that publish a matching static schedule and
  real-time feed. It handles trip updates and service alerts, not universal rail coverage.
- Add Gmail booking-change monitoring only after explicit OAuth approval; Gmail watch
  notifications require renewal and a fallback history sync.
- Treat hotels, museums, and activities as booking-change/readiness sources unless their
  actual booking provider exposes an authorized webhook or API. Do not claim live
  operational monitoring where no source exists.

## UX coverage labels

- `Live status` — an authoritative provider observation has a current source timestamp.
- `Realtime transit` — a matched GTFS-Realtime feed has a current timestamp.
- `Booking changes` — change notices can be observed, but live operational status cannot.
- `Schedule stored` — the item is used for impact/readiness only.
- `Monitoring degraded` — the source is temporarily unavailable or stale.

## Security rules

- No raw ticket, PDF, image, email body, QR code, or booking reference is needed for Phase A.
- Future document ingestion parses in a bounded ephemeral path; it stores only confirmed
  normalized fields and a dedupe fingerprint unless the traveler explicitly elects retention.
- Provider payloads and extracted text remain untrusted. They cannot directly grant action
  authority, alter policy, or enter prompts/logs without redaction and validation.

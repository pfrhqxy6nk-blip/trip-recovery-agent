# Product Requirements Document — Trip Recovery Agent

## 1. Product thesis

Trip Recovery Agent is a proactive autonomous agent for already-booked trips. It lives in Telegram, detects disruptions, calculates downstream consequences, performs only actions allowed by the traveler's standing policy, and asks for approval only when the traveler must make a genuine decision.

It is not a booking marketplace or dashboard. A bounded planning mode is included so a
traveler can ask for a trip brief (for example, Paris for six nights under €600), receive
clearly labelled estimates, and then forward real booking evidence before monitoring begins.
After onboarding, the traveler should stop managing the agent unless a genuine decision is
required.

**Core promise:** The traveler is always informed, but only interrupted when a decision genuinely requires them.

## 2. Hackathon objective

Ship one believable, cloud-backed, end-to-end recovery story that demonstrates:

1. a real disruption event enters through Pub/Sub;
2. deterministic code proves the connection is infeasible and identifies downstream impact;
3. ADK/Gemini interprets and explains validated facts without owning execution authority;
4. Telegram proactively informs the traveler;
5. the policy engine automatically performs safe permitted work;
6. an over-limit action pauses for a bound, expiring approval;
7. approval resumes the workflow from Firestore after a process boundary;
8. every external outcome is reread and verified;
9. the agent sends `Trip recovered` only when the recovery invariant passes.

The canonical demo is Warsaw → Munich → Lisbon: a 105-minute first-flight delay makes the Munich connection infeasible. The agent handles permitted transfer, hotel, and calendar consequences. A replacement flight costs €34 while the traveler's automatic limit is €20, so Telegram requests approval.

## 3. Target user and MVP assumptions

The target user is a traveler with a multi-part booked journey who wants disruption recovery handled without continuously monitoring apps or coordinating providers.

Hackathon assumptions:

- one Telegram traveler and one canonical beta fixture (the traveler forwards it explicitly);
- EUR only; no foreign-exchange authority;
- one primary disruption type and one deterministic recovery option;
- architecture and data ownership remain multi-user compatible;
- deterministic, persistent demo adapters are mandatory;
- real Telegram is mandatory;
- Google Calendar/Gmail are explicit opt-in OAuth adapters with provider reread checks;
  the controlled pilot uses safe demo providers unless the owner has enabled and verified them;
- Duffel sandbox and Gmail are stretch integrations after the core loop is stable;
- no production payments or card data.

### Gemini access and cost ownership

The judge deployment uses a project-owned Vertex AI/Gemini connection behind an atomic shared
daily quota (20 calls/day, 5 per traveler, bounded output) so judges do not need to enter a key.
BYOK remains an optional separate connection for a private pilot; it is never accepted through
Telegram text or stored in Firestore. If the project quota is exhausted, the agent returns an
explicit unavailable message and keeps deterministic monitoring/recovery paths safe.

The bot gives the traveler two links: the official Google AI Studio key page and a one-time secure connection page owned by the agent. The traveler must never paste an API key into Telegram. The connection page sends the key only over HTTPS to the edge service; the edge stores it in Secret Manager and stores only non-secret connection metadata in Firestore. Telegram displays a provider label, a masked fingerprint, connection status, and a `Disconnect` control.

BYOK covers Gemini usage only. Cloud Run, Firestore, monitoring, and any paid travel-data or booking-provider usage remain product operating costs until a separate commercial model is designed. A system-owned Vertex AI connection remains permitted for internal development and the controlled hackathon demo, but it is not silently used for a user who has selected BYOK.

## 4. Product invariants

1. Every meaningful trip change is communicated in Telegram.
2. Initial traveler notification is durably recorded before consequential side effects begin.
3. Gemini may interpret, rank, explain, and propose replanning; it cannot determine time feasibility, price, authority, state transitions, idempotency, or verification.
4. Irreversible, ambiguous, penalty-bearing, materially different, stale, or unsupported actions always require approval.
5. Automatic spending is cumulative per incident and is allowed only within the configured EUR limit when no mandatory approval rule applies.
6. Firestore, not process memory or an ADK session, is the workflow source of truth.
7. Duplicate events, retries, restarts, and repeated Telegram callbacks do not duplicate external effects.
8. A provider HTTP success is not proof of completion; every mutation is independently reread and verified.
9. `RECOVERED` is impossible while any required action, approval, or itinerary conflict remains unresolved.
10. Traveler-facing success text lists only actions actually performed and verified.

## 5. Telegram onboarding

`/start` binds the account using Telegram `user.id`, not username. Onboarding resumes from the first incomplete step and repeated `/start` is idempotent.

### Step 1 — Promise

Explain that the bot recovers already-booked trips, always reports meaningful changes, and asks before consequential actions. Button: `Set up my agent`.

### Step 2 — Calendar

Question: may the agent update trip calendar events automatically?

- `Update automatically`
- `Ask first`

Persist `calendar_mode = AUTO | ASK`. Recommended default: `AUTO`.

### Step 3 — Hotel and service messages

Question: may the agent send practical operational messages such as a late-arrival notice?

- `Message automatically`
- `Ask first`

Persist `service_message_mode = AUTO | ASK`. Contract changes, cancellation, penalty acceptance, and reservation modification are not simple service messages.

### Step 4 — Free reversible changes

Question: may the agent automatically make changes that are free and demonstrably reversible?

- `Change automatically`
- `Ask first`

Persist `reversible_change_mode = AUTO | ASK`. Automatic eligibility requires deterministic proof of zero cost, no penalty, reversibility before a known deadline, no ambiguity, and no major itinerary change.

### Step 5 — Recovery spending

Offer `No automatic spending` or a per-incident limit. MVP fixed choices are €20, €50, €100, plus a validated custom amount up to €500. Persist integer minor units and `currency = EUR`.

### Step 6 — Mandatory boundary

Display, but do not allow the user to disable, the rules that always require approval:

- irreversible action;
- penalty or uncertain terms;
- major itinerary change;
- unsupported/mismatched currency;
- price above remaining incident authority;
- changed or expired quote.

For MVP, a major change includes a new origin/destination/airport, different arrival date, newly introduced overnight, cabin downgrade, self-transfer, or arrival more than six hours later.

### Step 7 — Summary and activation

Show the complete policy summary. `Activate agent` atomically creates a versioned policy and sets onboarding to complete. Meaningful-change notifications are always enabled.

`/settings` edits the policy by creating a new version. A policy change invalidates any unconsumed approval and triggers deterministic reevaluation; it never silently authorizes an already-presented plan.

### AI connection — after policy activation

After activation the bot offers `Connect Gemini`. It sends:

1. `Get a Gemini API key` → the official Google AI Studio API Keys page;
2. `Connect securely` → a short-lived, single-use HTTPS URL bound to that Telegram user and chat.

The secure page accepts a key once, validates it with a minimal non-trip Gemini request, and responds only with connected/failed status. The bot never receives or repeats the key. If validation fails, it gives a safe retry message without echoing provider details. `Disconnect Gemini` deletes the agent's Secret Manager version/metadata mapping and explains that the traveler can separately revoke the key in Google AI Studio.

## 6. Canonical recovery experience

### Awareness message

Before consequential execution, send and persist a calm message explaining the source change and deterministic impact:

> Trip change detected: Warsaw → Lisbon
>
> LO351 is now arriving in Munich at 19:45. Your 18:55 connection is no longer feasible.
>
> I found a recovery option and I’m handling the changes allowed by your settings now.

### Consolidated approval message

Edit the same message after safe actions finish:

> Your Munich connection is no longer feasible.
>
> I found a recovery option arriving in Lisbon at 23:15 — 2h10 later than planned.
>
> Already handled:
> ✓ transfer adjusted
> ✓ hotel late-arrival record updated
> ✓ calendar updated
>
> Flight change: +€34
> Automatic limit: €20
>
> I need your approval because the price exceeds your limit.
>
> [Approve recovery] [Show details]

The deterministic hotel demo adapter represents a mutable late-arrival record. A real Gmail send is irreversible: automatic Gmail work may create a draft, but sending requires approval.

### Show details

`Show details` is read-only. It shows absolute times, option fingerprint, price, quote expiry, completed/verified actions, pending actions, and policy reasons. It never changes approval or incident state.

### Approval

The button contains only a short opaque token. Server state binds it to Telegram user/chat, incident, plan version/hash, policy version, option fingerprint, maximum authorized amount, currency, and expiry.

The first valid click consumes the approval transactionally and enqueues one continuation. Repeated clicks execute nothing and return `This approval was already handled`. A stale or changed quote causes replanning and fresh approval.

### Alternatives and stopping

- `Find another option` supersedes the current approval and creates a durable replan request.
- `Stop recovery` requires confirmation, preserves completed actions, cancels pending work, keeps the trip at risk, and clearly lists unresolved items.
- `Resume recovery` creates or validates a current plan; it never reuses stale authority.

### Completion

Only after provider rereads and a final deterministic conflict check:

> Trip recovered.
>
> ✓ replacement flight confirmed
> ✓ transfer updated
> ✓ hotel notified
> ✓ calendar updated
> ✓ no unresolved itinerary conflicts
>
> New Lisbon arrival: 23:15
> Additional recovery cost: €34

## 7. Deterministic policy order

For each action, evaluate in this order and persist all reason codes:

1. ambiguous → approval;
2. irreversible → approval;
3. penalty-bearing → approval;
4. major itinerary change → approval;
5. category policy is `ASK` → approval;
6. free reversible policy is `ASK` → approval;
7. cumulative incident cost exceeds remaining automatic authority → approval;
8. otherwise auto-allowed.

Aggregate required actions into one versioned plan approval to minimize interruptions.

## 8. Functional requirements

### Planning and state

- Create a versioned `RecoveryPlan` from validated impacts, policy, and provider-supplied options.
- Store canonical hashes, source incident/policy versions, quote identity/expiry, action dependency graph, costs, safety attributes, authority reasons, and verification specs.
- Supersede the plan after new disruption data, policy change, quote change, provider-state change, or replan request.

### Execution

- Claim every action transactionally with a lease.
- Use a semantic effect key that survives plan revisions for provider idempotency.
- If provider outcome is uncertain after a crash, reread provider state before retrying.
- Distinguish retryable failure, terminal failure, and human attention.
- Resume from the first unverified required action after restart.

### Telegram

- Verify webhook secret token and payload limits.
- Deduplicate by Telegram `update_id`.
- Bind callbacks to exact user/chat ownership.
- Acknowledge callbacks quickly and continue asynchronously.
- Persist message IDs so one recovery message can be calmly edited.
- Treat free text as non-authoritative; it cannot directly instruct Gemini to execute.

### Providers

- Provider mutations accept an idempotency/effect key.
- Every provider has an independent verification read.
- Demo providers persist state in Firestore so Cloud Run restarts do not erase evidence.
- Calendar verification rereads start/end/timezone and an app-owned effect property.
- Gmail draft/send verification rereads returned identifiers; actual send is irreversible.
- Duffel validates option, itinerary, price, penalty terms, and expiry before execution.

### Failure communication

- Retry short transient failures silently.
- If execution becomes materially delayed, send a truthful in-progress update.
- Verification exhaustion sends `Recovery needs attention`, never `Trip recovered`.
- Missing optional connections are reported as `not connected`, never as successful actions.
- Telegram delivery failure is persisted and retried; consequential work waits for the initial notification.

## 9. Acceptance criteria

### Onboarding and policy

- Onboarding survives restart, resumes correctly, and creates one versioned active policy.
- Invalid spending input cannot mutate saved policy.
- €15 is auto-allowed under a €20 limit; cumulative €25 is not.
- Any irreversible, ambiguous, penalty-bearing, or major action requires approval regardless of limit.
- Every authority decision contains deterministic reason codes.

### Planning and Telegram

- The canonical delay creates one versioned plan with the €34 action and affected downstream items.
- The first message states absolute arrival/departure times and failed connection.
- Identical duplicate events create no duplicate plan, notification, or action.
- `Show details` performs no state mutation.
- Callback data contains only an opaque short token.

### Approval and concurrency

- Only the owning Telegram user/chat can approve.
- Approval is bound to current plan, policy, quote, currency, amount, and expiry.
- Exactly one concurrent callback consumes approval and creates one continuation.
- Duplicate, stale, expired, superseded, cross-user, or changed-quote callbacks execute nothing.
- A re-quote change requires a new plan and approval.

### Persistence and verification

- Compare-and-set transitions prevent two workers from owning one action.
- A crash before a provider call retries safely.
- A crash after provider success but before local commit rereads before retry.
- Restart resumes from Firestore at the first unverified action.
- Provider 2xx without matching reread state cannot verify the action.
- `RECOVERED` cannot be entered with pending, failed, required-skipped, or unverified work.
- The full canonical test proves disruption → plan → notification → safe actions → approval → resume → execution → provider reread → recovered message.

## 10. MVP cuts

### Must ship

- transactional workflow state and approval/action claims;
- versioned policy, plan, approval, action, notification, and verification records;
- deterministic policy and recovery planner;
- persistent deterministic flight, transfer, hotel, and calendar adapters;
- Telegram onboarding, proactive message, details, approve, replan, stop, and duplicate protection;
- persistent resume and final recovery invariant;
- real Telegram integration;
- automated canonical end-to-end scenario;
- correlated Firestore and Cloud Logging evidence.

### Strong target

- real Calendar OAuth/action/verification;
- Pub/Sub command continuation and dead-letter handling;
- deliberate crash/restart demo;
- CI, security scans, and reproducible evidence script.

### Stretch

- Duffel sandbox option/order change;
- Gmail OAuth late-arrival draft/send;
- multiple Gemini-ranked recovery options.

### Out of scope

- traveler web UI;
- arbitrary itinerary import/provider breadth;
- production payment credentials/card handling;
- multi-currency/FX;
- conversational execution instructions;
- automatic compensation/rollback;
- production-grade multi-tenant administration.

## 11. Success metrics for the demo

- one canonical scenario completes from real cloud event to verified Telegram success;
- zero duplicate side effects under duplicate Pub/Sub delivery and callback replay;
- zero false verified actions or false `RECOVERED` transitions;
- one user interruption for the €34/€20 authority boundary;
- one deliberate restart/crash can be resumed without process memory;
- all major claims are backed by tests, persisted records, or cloud logs.

## 12. Privacy and delivery boundary

Planning and implementation remain local until explicitly authorized. No GitHub push, public deployment, Devpost submission, or external publication is part of this plan. Live cloud deployment and credentialed integrations are separate approval gates.

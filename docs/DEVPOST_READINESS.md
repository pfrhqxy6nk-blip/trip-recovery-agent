# Devpost readiness — All Things Agentic Hackathon

**Checked against official Devpost data on 2026-08-29.** This document is a practical audit, not legal advice. The official rules and submission form prevail.

## Current result

**Technical fit: strong.** Trip Watch directly matches the event’s required stack and the **Taskmaster** category: it is an asynchronous workflow that observes a trip, evaluates a disruption, performs bounded actions, requests approval for consequential work, and resumes durably.

**Submission status: not submitted.** The remaining work is evidence and form completion, not a missing core agent architecture.

## Official requirements → project evidence

| Official requirement | Evidence in this repository | Status |
| --- | --- | --- |
| Gemini 3.5 or newer via Gemini API or Vertex AI | Google ADK + Vertex integration; model configured with `GEMINI_MODEL_ID`; deployed proof records `gemini-3.5-flash`. | Ready |
| At least one Google agent framework | `google-adk[gcp]` in `pyproject.toml`; ADK agent boundary in `backend/app/agents`. | Ready |
| At least one Google Cloud infrastructure service | Cloud Run, Firestore, Pub/Sub, Secret Manager and Vertex AI are documented and implemented. | Ready |
| A deployed autonomous agent beyond a chat loop | Scheduler/watchpoints, Pub/Sub commands, Firestore outbox, policy-gated recovery, proactive Telegram updates, durable resume. | Ready to demonstrate |
| Pick one category | Best fit: **Taskmaster**. | Select in Devpost |
| Text description: functionality, technologies, other data sources, learnings | Draft in [../devpost-submission.md](../devpost-submission.md). | Ready to paste |
| Public or judge-accessible code repository | [GitHub repository](https://github.com/pfrhqxy6nk-blip/trip-recovery-agent). | Verify in incognito |
| Reproducible README instructions | Local setup, quality gate, fixture flow and limitations are in [README](../README.md). | Ready |
| Architecture diagram upload | [architecture-diagram.pdf](architecture-diagram.pdf) and PNG are ready. | Upload in form |
| Public ~4 minute English/subtitled demo video | Script is in `devpost-submission.md`; recording has not been attached yet. | Required blocker |
| Proof backend runs on Google Cloud | [CLOUD_PROOF.md](CLOUD_PROOF.md), Cloud Run/Vertex/Firestore/Pub/Sub architecture, and live bot/landing links. | Show in video |
| Testing access available through judging | Hosted landing and Telegram bot; synthetic fixtures enable repeatable intake. | Verify live right before recording |
| English-supported application and materials | Telegram, landing, README, fixture instructions and draft are English. | Ready |
| New work created during submission period, third-party rights disclosed | Git history / asset provenance must be confirmed by the entrant. | Entrant attestation required |

## Official form fields to complete

| Field | Recommended value / action |
| --- | --- |
| Submitter Type | Choose the truthful status: Individual, Team of individuals, or Organization. |
| Country of residence | Enter your actual current residence only; confirm eligibility personally. |
| Category | **Taskmaster**. |
| Start date | Enter the actual project start date in `MM-DD-YY`. Do not guess it. |
| Repository URL | `https://github.com/pfrhqxy6nk-blip/trip-recovery-agent` — open in incognito first. |
| Reproducible testing instructions | `Yes`. README has the command and demo fixture path. |
| Hosted project URL | `https://trip-watch.vercel.app/` (strongly recommended). |
| Judge testing instructions | Paste the short instruction in `devpost-submission.md`; do not put credentials in the public description. |
| Google SDK | **Agent Development Kit (ADK)**. Add only other SDKs actually used. |
| Google Cloud services | **Cloud Run**, **Firestore**, **Pub/Sub**. |
| Architecture diagram | Upload `docs/architecture-diagram.pdf`. |
| Google AI model | State the deployed Gemini 3.5+ model truthfully, e.g. `gemini-3.5-flash via Vertex AI`. |
| Video | Upload public YouTube/Vimeo URL. It is required. |

## What the 4-minute video must prove

1. **Problem (0:00–0:20):** a delay breaks multiple downstream commitments, not just a flight.
2. **Agent value (0:20–0:45):** Trip Watch works in the background, with policy boundaries.
3. **Multimodal intake (0:45–1:20):** forward the synthetic PDF in Telegram → structured draft → save the trip.
4. **Autonomy (1:20–2:30):** trigger the controlled +195 minute disruption → impact, weather/baggage/visa assessment, safe downstream handling, exactly one €34 approval.
5. **Durable recovery (2:30–3:05):** approve → recovered receipt → review-only EU261 claim draft.
6. **Google Cloud proof (3:05–3:35):** show Cloud Run, Firestore/Pub/Sub or Vertex evidence, and the architecture diagram.
7. **Truth boundary (3:35–4:00):** distinguish demo adapters from real ticketing and show why the safety design is intentional.

Record in English or add English subtitles. Keep the demo unedited enough that the real workflow is credible.

## Pre-submit checklist

- [ ] Confirm personal eligibility (age, residence, employment/conflict rules).
- [ ] Record and publish the public video; put the final URL in `devpost-submission.md` and Devpost.
- [ ] Open the repository and landing in an incognito browser.
- [ ] Upload `docs/architecture-diagram.pdf` to the Devpost architecture field.
- [ ] Use a fresh Telegram chat to run `/start` → PDF → Save trip → `/demo` → approval → receipt.
- [ ] Run `scripts/run_submission_gate.sh` and capture its success locally.
- [ ] Confirm the live Cloud Run revision/Telegram webhook one final time; update `docs/CLOUD_PROOF.md` only with verified facts.
- [ ] Check that no `.env`, API key, OAuth secret, PNR, passport, or real booking artifact is tracked.
- [ ] Fill the owner-only form fields from `devpost-submission.md` accurately.
- [ ] After the deadline, do not modify the submitted repo, linked video, or materials; work in a fork instead.

## Known boundaries to preserve in the submission

Do not say that Trip Watch purchases tickets, charges a card, sends claims, reads Gmail, or updates a calendar in the judge demo. Those capabilities are deliberately provider- and consent-gated. The strong, accurate story is a **real autonomous workflow with deterministic safety controls**, not a promise of integrations that have not been enabled.

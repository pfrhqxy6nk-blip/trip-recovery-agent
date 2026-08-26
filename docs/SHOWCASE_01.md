# Showcase 01 — Local recovery loop

This is the first reproducible end-to-end demonstration of the recovery logic. It is
intended for a live terminal walkthrough before Telegram and live provider credentials are
introduced.

## Run

```bash
PYTHONPATH=backend .venv/bin/python -m app.demo_recovery
```

## What it proves

1. A 105-minute delay for LO351 enters the existing impact-analysis workflow.
2. The deterministic engine proves the Munich connection is infeasible.
3. The recovery planner produces the canonical arrival at 23:15 and a +€34 option.
4. The policy engine automatically executes and rereads transfer, hotel late-arrival, and
   calendar demo state.
5. The replacement flight waits at the €34/€20 authority boundary.
6. A simulated approval consumes one persistent approval request and resumes execution.
7. All four provider states are reread as verified before the incident reaches `RECOVERED`.
8. A duplicate approval callback is covered by `test_recovery_e2e.py` and does not create a
   second effect or outbox record.

## Verified locally

On 2026-08-16 the showcase command completed with `Trip recovered: RECOVERED`. The full
suite completed with 38 passing tests; Ruff, mypy, `git diff --check`, and the tracked-file
secret scan passed.

## Honest boundary

This showcase uses the actual FastAPI-domain workflow, policy engine, repository contract,
and persistent demo-provider interface, but it uses the local repository adapter. It does
not yet call Telegram, Calendar, Duffel, Gmail, or a live Firestore emulator. Those are the
next delivery stages and must not be represented as completed external actions.

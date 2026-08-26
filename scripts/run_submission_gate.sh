#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ ! -x ".venv/bin/pytest" ]]; then
  echo "Missing .venv/bin/pytest; create the project environment first." >&2
  exit 2
fi

echo "[1/8] Backend tests"
.venv/bin/pytest -q backend/tests

echo "[2/8] Static checks"
.venv/bin/ruff check backend cloud_function scripts
.venv/bin/mypy backend
PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-/tmp/trip-agent-pycache}" \
  python3 -m compileall -q backend cloud_function scripts
bash -n scripts/*.sh
git diff --check

echo "[3/8] Canonical autonomous recovery"
scripts/run_canonical_e2e.sh

echo "[4/8] Landing production build"
(cd landing && npm run build)

echo "[5/8] Sites packaging tests"
(cd landing && npm run test:sites)

echo "[6/8] English product copy"
if rg -n --glob '!backend/tests/**' --glob '!landing/node_modules/**' \
  --glob '!landing/dist/**' --glob '!backend/app/services/telegram_conversation.py' \
  --glob '!backend/app/services/telegram_planning.py' \
  --glob '!backend/app/agents/itinerary_extractor.py' '[А-Яа-яЁё]' \
  landing/src backend/app docs README.md devpost-submission.md; then
  echo "Cyrillic text found in product-facing files. The excluded files contain input-only aliases." >&2
  exit 1
fi

echo "[7/8] Optional live Telegram/Cloud Run contract"
if [[ "${RUN_LIVE_CHECK:-0}" == "1" ]]; then
  if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${TELEGRAM_WEBHOOK_SECRET:-}" ]]; then
    echo "RUN_LIVE_CHECK=1 requires TELEGRAM_BOT_TOKEN and TELEGRAM_WEBHOOK_SECRET." >&2
    exit 2
  fi
  scripts/verify_live_contract.sh
else
  echo "Skipped (set RUN_LIVE_CHECK=1 after exporting local .env values)."
fi

echo "[8/8] Gate summary"
echo "Submission gate: PASS (owner-controlled Telegram /start, billing budget, TAC, and Devpost metadata remain separate gates)."

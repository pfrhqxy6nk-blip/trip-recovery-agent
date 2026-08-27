#!/usr/bin/env bash
set -euo pipefail

# Read-only live acceptance check. It never calls getUpdates, sends a Telegram
# message, or posts a valid user update to the webhook.
: "${TELEGRAM_BOT_TOKEN:?Set TELEGRAM_BOT_TOKEN in the environment before running this check}"
: "${TELEGRAM_WEBHOOK_SECRET:?Set TELEGRAM_WEBHOOK_SECRET in the environment before running this check}"

EDGE_URL="${EDGE_URL:-https://trip-recovery-edge-oy6lnosdfq-ey.a.run.app}"
EXPECTED_WEBHOOK_URL="${EXPECTED_WEBHOOK_URL:-${EDGE_URL%/}/telegram/webhook}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

curl_args=(--silent --show-error --fail --connect-timeout 5 --max-time 20)
curl "${curl_args[@]}" \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe" \
  >"${TMP_DIR}/me.json"
curl "${curl_args[@]}" \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo" \
  >"${TMP_DIR}/webhook.json"

python3 - "${TMP_DIR}/me.json" "${TMP_DIR}/webhook.json" "${EXPECTED_WEBHOOK_URL}" <<'PY'
import json
import sys
from pathlib import Path

me = json.loads(Path(sys.argv[1]).read_text())
webhook = json.loads(Path(sys.argv[2]).read_text())
expected_url = sys.argv[3].rstrip("/")

if not me.get("ok") or not me.get("result", {}).get("is_bot"):
    raise SystemExit("Telegram getMe did not return a bot")
result = webhook.get("result", {})
if not webhook.get("ok"):
    raise SystemExit("Telegram getWebhookInfo returned ok=false")
if result.get("url", "").rstrip("/") != expected_url:
    raise SystemExit("Telegram webhook URL does not match the active edge")
if result.get("pending_update_count") != 0:
    raise SystemExit("Telegram has pending updates")
if result.get("last_error_message"):
    message = result["last_error_message"]
    error_date = result.get("last_error_date", "unknown time")
    raise SystemExit(
        "Telegram reports a recorded webhook error "
        f"({error_date}); a fresh valid Telegram update is required to clear this gate: {message}"
    )

print(f"Telegram bot: @{me['result'].get('username', '<unnamed>')}")
print("Telegram webhook: healthy (URL matches, no pending updates, no recorded error)")
PY

edge_status="$(curl --silent --show-error --connect-timeout 5 --max-time 20 \
  -o "${TMP_DIR}/edge.json" -w '%{http_code}' \
  -X POST "${EDGE_URL%/}/telegram/webhook" \
  -H "X-Telegram-Bot-Api-Secret-Token: ${TELEGRAM_WEBHOOK_SECRET}" \
  -H 'content-type: application/json' \
  --data '{}')"
if [[ "${edge_status}" != "400" ]]; then
  echo "Expected signed malformed-update probe to return HTTP 400; got ${edge_status}" >&2
  exit 1
fi
python3 - "${TMP_DIR}/edge.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
if payload.get("detail") != "malformed Telegram update":
    raise SystemExit("Signed edge probe returned an unexpected response")
print("Cloud Run edge: healthy (signed route validation returned HTTP 400 as expected)")
PY

echo "Live contract check: PASS"

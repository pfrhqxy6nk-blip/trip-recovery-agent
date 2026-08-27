# User-managed Gemini access (BYOK)

## What the traveler does

1. Opens the bot and finishes the autonomy settings.
2. Taps **Connect Gemini**.
3. Opens **Get a Gemini API key** in [Google AI Studio](https://aistudio.google.com/app/apikey), creates a key, and returns to the bot.
4. Taps **Connect securely**. This opens a short-lived HTTPS page tied to their Telegram account.
5. Pastes the key only on that page. The bot confirms `Gemini connected • …abcd`; it never sees or repeats the key.

Google recommends creating and managing Gemini API keys in AI Studio, and supports restricting a key to the Gemini API. See the official [Gemini API key guide](https://ai.google.dev/gemini-api/docs/api-key).

## What the service stores

| Location | Stored |
| --- | --- |
| Secret Manager | One BYOK-only secret; each traveler key is an isolated version referenced by its exact resource name |
| Firestore | Provider, status, Secret Manager resource name, non-reversible fingerprint, timestamps |
| Telegram | Only status and a masked suffix |
| Logs / Gemini prompts | Never the key or authorization header |

## Safety rules

- Never send an API key to a bot, chat, email, or support ticket.
- The HTTPS handoff is one-time, short-lived, and bound to the Telegram user and chat.
- Validation uses a minimal request with no itinerary or personal data.
- A broken, revoked, or exhausted key results in `AI connection needs attention`; it never silently uses the product's key.
- `Disconnect Gemini` removes the agent's secret mapping. The traveler can also revoke the source key in Google AI Studio.

## Cost boundary

BYOK makes Gemini usage subject to the traveler's own Google AI API quota and billing setup. It does not pay for the recovery itself, a flight price, or the product's Cloud Run, Firestore, data-provider, and monitoring costs. Those need a separate commercial model before public launch.

## Delivery gate

This document defines the safe flow only. No public page, secret, webhook, or real key has been created. Those are explicit approval-gated cloud actions.

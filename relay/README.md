# Haze Twin relay ("Ask about the haze")

`worker.js` is a Cloudflare Worker called **haze-ask**. The website sends it a question; it reads the
latest published forecast itself (`https://bennetmc2.github.io/haze-twin/data/forecast.json`) and asks an
AI model to answer from that forecast. It replies with `{answer, tier}`.

- Free answers use Workers AI (Llama 3.1 8B).
- Premium answers use Claude (`claude-haiku-5-5`) for people who have a premium code.
- The old "Make your call" game (`/calls`, `/leaderboard`) has been removed. Any other address returns "not found".

## Redeploy after a change

1. Log in to the Cloudflare dashboard.
2. Go to **Workers & Pages** and open **haze-ask**.
3. Click **Edit code**.
4. Select everything in the editor and delete it. Paste in the whole of `relay/worker.js` from this repository.
5. Click **Deploy**.
6. Open the Haze Twin site, ask a question, and check you get an answer.

## Settings the Worker needs

Set these under **haze-ask → Settings → Bindings** and **Settings → Variables and Secrets**.

| Name | Kind | Needed? | What to put |
|---|---|---|---|
| `AI` | Workers AI binding | Required | Add binding → Workers AI, name it `AI`. |
| `ANTHROPIC_API_KEY` | Secret | For premium | Your Anthropic API key. |
| `PREMIUM_CODES` | Secret | For premium | Codes separated by commas, e.g. `kx7f2pqa,mz93hrtw`. **Each code must be at least 8 characters.** Shorter or blank codes are ignored. Leave empty to turn premium off. |
| `RATE_LIMITER` | Rate limiting binding | Recommended | Add binding → Rate Limiting, name it `RATE_LIMITER`, limit **10** requests per **60** seconds. Without it the Worker uses a rougher built-in limit. |
| `USAGE` | KV namespace binding | Recommended | Create a KV namespace (e.g. `haze-usage`) and bind it as `USAGE`. It counts premium answers per day. |
| `PREMIUM_DAILY_MAX` | Plain variable | Optional | Most premium answers allowed per day (UTC). Default 200. After that, people get free answers. Only works if `USAGE` is set. |

The old `CALLS` KV binding is no longer used. You can delete it (and its KV namespace) once you no longer need the old game data.

## Keep costs safe

- In the Anthropic console (console.anthropic.com → Settings → Limits), **set a monthly spend limit**. This is the hard stop if anything goes wrong.
- Give premium codes only to people you trust, and change them if one leaks (edit `PREMIUM_CODES`, then the old code stops working at once).

## What protects the Worker

- Only the Haze Twin website may call it from a browser (CORS). This is not a password: scripts outside a browser can still call it, so the limits below matter.
- Requests are limited per visitor (rate limit), questions are at most 300 characters, and request bodies at most 4 KB.
- The forecast numbers come from the published forecast file, not from the visitor, so nobody can feed it false numbers.
- If the AI service fails or is slow (over 20 seconds), the Worker returns a short error and the site falls back to its built-in answer.

## Test

```
node --test tests/test_worker.mjs
```

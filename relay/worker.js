// Haze Twin relay (Cloudflare Worker): answers "Ask about the haze" questions.
//
// POST /  body {question, code?}  ->  {answer, tier}
//   (The site also sends `context`; it is ignored. The forecast is fetched here, server-side.)
//
// Bindings and settings (Worker > Settings). See relay/README.md.
//   AI                  Workers AI binding (free answers)                       required
//   ANTHROPIC_API_KEY   secret, premium answers (Claude)                         optional
//   PREMIUM_CODES       secret, comma-separated premium codes, each >= 8 chars   optional
//   RATE_LIMITER        Rate Limiting binding (e.g. 10 requests per 60 s)        optional
//   USAGE               KV namespace, counts premium calls per UTC day           optional
//   PREMIUM_DAILY_MAX   plain variable, premium calls allowed per day (200)      optional

const SITE = "https://bennetmc2.github.io";
const FORECAST_URL = "https://bennetmc2.github.io/haze-twin/data/forecast.json";
const PREMIUM_MODEL = "claude-haiku-5-5";
const FREE_MODEL = "@cf/meta/llama-3.1-8b-instruct";
const MAX_BODY = 4096; // bytes
const MAX_QUESTION = 300; // characters
const MIN_CODE = 8;
const MAX_CONTEXT = 6000; // characters
const FORECAST_TIMEOUT_MS = 5000;
const UPSTREAM_TIMEOUT_MS = 20000;
const FALLBACK_RATE = { tokens: 10, perMs: 60000 }; // per IP, per isolate
const DEFAULT_PREMIUM_DAILY_MAX = 200;

const SYSTEM = `You answer questions from the public about haze in Singapore, on the Haze Twin website.
Use only the forecast data provided plus general knowledge about haze, PSI and Southeast Asian fires.
These numbers come from an independent experimental model, not NEA's official forecast.
Rules:
- Plain, friendly English. 2 to 4 short paragraphs at most. No markdown, no bullet symbols, no headings.
- Give numbers from the data (PSI, % chances, dates). Say "best guess" for the middle forecast and give the likely range.
- Days more than 6 days ahead are less reliable; say so when relevant.
- Do not give medical advice. For health questions, state the PSI level and point to NEA's health advice at haze.gov.sg.
- If the question is not about haze, air quality, fires or weather in the region, say you can only help with the haze forecast.`;

const CORS = {
  // CORS only stops other websites' scripts from reading responses in a browser.
  // Origin is NOT authentication: any non-browser client can send any Origin header.
  // Abuse protection comes from the rate limit, the body limits and the premium budget below.
  "Access-Control-Allow-Origin": SITE,
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Access-Control-Max-Age": "86400",
  Vary: "Origin",
};

export default {
  async fetch(req, env) {
    env = env || {};
    try {
      return await handle(req, env);
    } catch (e) {
      // Never echo internal or upstream error text.
      return reply({ error: "server error" }, 500);
    }
  },
};

async function handle(req, env) {
  const url = new URL(req.url);
  if (url.pathname !== "/") return reply({ error: "not found" }, 404);
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  if (req.method !== "POST") return reply({ error: "method not allowed" }, 405, { Allow: "POST, OPTIONS" });
  if (req.headers.get("Origin") !== SITE) return reply({ error: "not allowed" }, 403);

  const ip = req.headers.get("CF-Connecting-IP") || "unknown";
  if (!(await allowed(env, ip))) return reply({ error: "too many requests" }, 429, { "Retry-After": "60" });

  // Bound the body: declared length first, then the bytes actually read.
  const declared = Number(req.headers.get("Content-Length"));
  if (Number.isFinite(declared) && declared > MAX_BODY) return reply({ error: "request too large" }, 413);
  const text = await readLimited(req, MAX_BODY);
  if (text === null) return reply({ error: "request too large" }, 413);

  let body;
  try {
    body = JSON.parse(text);
  } catch {
    return reply({ error: "bad json" }, 400);
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) return reply({ error: "bad request" }, 400);
  if (typeof body.question !== "string") return reply({ error: "empty" }, 400);
  const q = body.question.trim();
  if (!q) return reply({ error: "empty" }, 400);
  if (q.length > MAX_QUESTION) return reply({ error: "question too long" }, 400);
  // body.context is deliberately ignored: the forecast comes from our own published file.

  const summary = await forecastSummary();
  if (!summary) return reply({ error: "forecast unavailable" }, 503);
  const msg = "Forecast data:\n" + summary + "\n\nQuestion: " + q;

  let premium = env.ANTHROPIC_API_KEY && validCode(env.PREMIUM_CODES, body.code);
  if (premium) premium = await takePremiumBudget(env);

  try {
    if (premium) return reply({ answer: await askClaude(env, msg), tier: "premium" }, 200);
    return reply({ answer: await askWorkersAI(env, msg), tier: "free" }, 200);
  } catch {
    return reply({ error: "model unavailable" }, 502);
  }
}

// ---------- request body ----------

async function readLimited(req, max) {
  if (!req.body) return "";
  const reader = req.body.getReader();
  const chunks = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > max) {
      try { await reader.cancel(); } catch {}
      return null;
    }
    chunks.push(value);
  }
  const buf = new Uint8Array(total);
  let o = 0;
  for (const c of chunks) { buf.set(c, o); o += c.byteLength; }
  const text = new TextDecoder().decode(buf);
  return text.length > max ? null : text;
}

// ---------- premium codes ----------

function validCode(list, code) {
  if (typeof code !== "string") return false;
  const c = code.trim();
  if (c.length < MIN_CODE) return false;
  const codes = String(list || "").split(",").map((x) => x.trim()).filter((x) => x.length >= MIN_CODE);
  let ok = false;
  for (const k of codes) if (safeEqual(k, c)) ok = true; // check every code, no early exit
  return ok;
}

// Constant-time comparison (for equal lengths; the length itself is not secret).
function safeEqual(a, b) {
  const enc = new TextEncoder();
  const x = enc.encode(a), y = enc.encode(b);
  if (x.length !== y.length) return false;
  let d = 0;
  for (let i = 0; i < x.length; i++) d |= x[i] ^ y[i];
  return d === 0;
}

// ---------- rate limiting ----------

const buckets = new Map(); // best-effort, per isolate only

async function allowed(env, ip) {
  if (env.RATE_LIMITER && typeof env.RATE_LIMITER.limit === "function") {
    try {
      const { success } = await env.RATE_LIMITER.limit({ key: ip });
      return !!success;
    } catch {
      // fall through to the in-memory bucket
    }
  }
  const now = Date.now();
  const rate = FALLBACK_RATE.tokens / FALLBACK_RATE.perMs;
  let b = buckets.get(ip);
  if (!b) b = { t: FALLBACK_RATE.tokens, at: now };
  b.t = Math.min(FALLBACK_RATE.tokens, b.t + (now - b.at) * rate);
  b.at = now;
  const ok = b.t >= 1;
  if (ok) b.t -= 1;
  buckets.set(ip, b);
  if (buckets.size > 5000) {
    for (const [k, v] of buckets) if (now - v.at > FALLBACK_RATE.perMs) buckets.delete(k);
  }
  return ok;
}

// Daily premium budget. Returns true if this call may use premium. Best-effort (KV is not atomic).
async function takePremiumBudget(env) {
  if (!env.USAGE) return true;
  const max = Number(env.PREMIUM_DAILY_MAX) > 0 ? Number(env.PREMIUM_DAILY_MAX) : DEFAULT_PREMIUM_DAILY_MAX;
  const key = "premium:" + new Date().toISOString().slice(0, 10);
  try {
    const used = Number(await env.USAGE.get(key)) || 0;
    if (used >= max) return false;
    await env.USAGE.put(key, String(used + 1), { expirationTtl: 3 * 86400 });
    return true;
  } catch {
    return false; // if we cannot count, do not spend
  }
}

// ---------- forecast context (server-side) ----------

async function forecastSummary() {
  const ctl = new AbortController();
  const tm = setTimeout(() => ctl.abort(), FORECAST_TIMEOUT_MS);
  try {
    const r = await fetch(FORECAST_URL, { cf: { cacheTtl: 300 }, signal: ctl.signal });
    if (!r.ok) return null;
    const fc = await r.json();
    return summarise(fc);
  } catch {
    return null;
  } finally {
    clearTimeout(tm);
  }
}

const BAND_NAMES = ["good (0-50)", "moderate (51-100)", "unhealthy (101-200)", "very unhealthy (201-300)", "hazardous (over 300)"];
const num = (v) => (typeof v === "number" && Number.isFinite(v) ? Math.round(v) : null);
const str = (v, n = 200) => (typeof v === "string" ? v.slice(0, n) : null);

function summarise(fc) {
  if (!fc || typeof fc !== "object") return null;
  const nat = (fc.regions && fc.regions.national) || null;
  if (!nat) return null;
  const labels = Array.isArray(fc.days) ? fc.days : [];
  const lines = [];
  lines.push("Source: Haze Twin, an independent experimental model (not NEA's official forecast).");
  lines.push("Issued: " + (str(fc.issued, 40) || "unknown") + (fc.built ? "; built " + str(fc.built, 40) : ""));
  lines.push("Singapore national 24-hour PSI now: " + num(nat.now) + "; mode: " + (str(nat.mode, 30) || "unknown"));
  if (typeof nat.clear === "number") lines.push("Chance PSI is down to 100 or below by " + (str(nat.clearDayLabel, 30) || "day 6") + ": " + num(nat.clear) + "%");
  const classes = Array.isArray(nat.classes) ? nat.classes.slice(0, 6) : [];
  if (classes.length) {
    lines.push("Storylines for the next 6 days (name: odds %: outcome):");
    for (const c of classes) lines.push("- " + (str(c.name, 60) || "?") + ": " + num(c.odds) + "%: " + (str(c.outcome, 160) || ""));
  }
  const days = Array.isArray(nat.days) ? nat.days.slice(0, 15) : [];
  if (days.length) {
    lines.push("Daily national PSI (day: p5/p25/p50 best guess/p75/p95; chance % by band good/moderate/unhealthy/very unhealthy/hazardous; days after day 6 less reliable):");
    days.forEach((d, i) => {
      const lab = (labels[i] && str(labels[i].label, 20)) || "Day " + (i + 1);
      const b = Array.isArray(d.bands) ? d.bands.slice(0, 5).map(num).join("/") : "n/a";
      const clr = Array.isArray(nat.clearBy) && typeof nat.clearBy[i] === "number" ? "; chance cleared (100 or below) by then " + num(nat.clearBy[i]) + "%" : "";
      lines.push(lab + ": " + [d.p5, d.p25, d.p50, d.p75, d.p95].map(num).join("/") + "; bands " + b + clr);
    });
  }
  if (fc.season && typeof fc.season === "object") {
    const s = fc.season;
    if (s.median) lines.push("Haze season end (experimental): most likely " + str(s.median, 20) + (s.p10 && s.p90 ? ", 80% range " + str(s.p10, 20) + " to " + str(s.p90, 20) : ""));
  }
  lines.push("PSI bands: " + BAND_NAMES.join(", ") + ".");
  let out = lines.join("\n");
  if (out.length > MAX_CONTEXT) out = out.slice(0, MAX_CONTEXT);
  return out;
}

// ---------- models ----------

async function askClaude(env, msg) {
  const ctl = new AbortController();
  const tm = setTimeout(() => ctl.abort(), UPSTREAM_TIMEOUT_MS);
  try {
    const r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      signal: ctl.signal,
      headers: { "content-type": "application/json", "x-api-key": env.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01" },
      body: JSON.stringify({ model: PREMIUM_MODEL, max_tokens: 400, system: SYSTEM, messages: [{ role: "user", content: msg }] }),
    });
    if (!r.ok) throw new Error("upstream " + r.status);
    const out = await r.json();
    const text = (Array.isArray(out.content) ? out.content : []).map((c) => (c && c.text) || "").join("\n").trim();
    if (!text) throw new Error("empty");
    return text;
  } finally {
    clearTimeout(tm);
  }
}

async function askWorkersAI(env, msg) {
  if (!env.AI || typeof env.AI.run !== "function") throw new Error("no AI binding");
  let tm;
  const timeout = new Promise((_, rej) => { tm = setTimeout(() => rej(new Error("timeout")), UPSTREAM_TIMEOUT_MS); });
  try {
    const out = await Promise.race([
      env.AI.run(FREE_MODEL, { messages: [{ role: "system", content: SYSTEM }, { role: "user", content: msg }], max_tokens: 400 }),
      timeout,
    ]);
    const text = String((out && out.response) || "").trim();
    if (!text) throw new Error("empty");
    return text;
  } finally {
    clearTimeout(tm);
  }
}

function reply(obj, status, extra = {}) {
  return new Response(JSON.stringify(obj), { status, headers: { ...CORS, ...extra, "Content-Type": "application/json" } });
}

// Haze Twin relay (Cloudflare Worker): "Ask about the haze" answers, and "Make your call" predictions.
//
// Bindings and settings (Worker > Settings):
//   AI                  Workers AI binding (free answers)
//   ANTHROPIC_API_KEY   secret, premium answers (Claude) for PREMIUM_CODES
//   PREMIUM_CODES       secret, comma-separated premium codes
//   CALLS               KV namespace binding for predictions and the leaderboard

const SITE = "https://bennetmc2.github.io";
const SYSTEM = `You answer questions from the public about haze in Singapore, on the Haze Twin website.
Use only the forecast data provided (JSON) plus general knowledge about haze, PSI and Southeast Asian fires.
Rules:
- Plain, friendly English. 2 to 4 short paragraphs at most. No markdown, no bullet symbols, no headings.
- Give numbers from the data (PSI, % chances, dates). Say "best guess" for the middle forecast and give the likely range.
- Days more than 6 days ahead are less reliable; say so when relevant.
- Do not give medical advice. For health questions, state the PSI level and point to NEA's health advice at haze.gov.sg.
- If the question is not about haze, air quality, fires or weather in the region, say you can only help with the haze forecast.`;
const BANDS = [50, 100, 200, 300, Infinity];
const bandOf = (v) => BANDS.findIndex((m) => Math.round(v) <= m);

export default {
  async fetch(req, env) {
    const h = { "Access-Control-Allow-Origin": SITE, "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type" };
    if (req.method === "OPTIONS") return new Response(null, { headers: h });
    if (req.headers.get("Origin") !== SITE) return new Response("Not allowed", { status: 403, headers: h });
    const url = new URL(req.url);
    try {
      if (url.pathname === "/calls") return await calls(req, env, h, url);
      if (url.pathname === "/leaderboard") return reply(await leaderboard(env), 200, h);
      if (req.method === "POST") return await ask(req, env, h);
    } catch (e) {
      return reply({ error: String(e).slice(0, 200) }, 500, h);
    }
    return reply({ error: "not found" }, 404, h);
  },
};

async function ask(req, env, h) {
  const body = await req.json().catch(() => ({}));
  const q = String(body.question || "").slice(0, 300).trim();
  if (!q) return reply({ error: "empty" }, 400, h);
  const msg = "Forecast data:\n" + JSON.stringify(body.context || {}).slice(0, 12000) + "\n\nQuestion: " + q;
  const codes = String(env.PREMIUM_CODES || "").split(",").map((c) => c.trim());
  const premium = env.ANTHROPIC_API_KEY && codes.includes(String(body.code || "").trim());
  if (premium) {
    const r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: { "content-type": "application/json", "x-api-key": env.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01" },
      body: JSON.stringify({ model: "claude-haiku-5-5", max_tokens: 400, system: SYSTEM, messages: [{ role: "user", content: msg }] }),
    });
    if (!r.ok) return reply({ error: "model error " + r.status }, 502, h);
    const out = await r.json();
    return reply({ answer: out.content.map((c) => c.text || "").join("\n").trim(), tier: "premium" }, 200, h);
  }
  const out = await env.AI.run("@cf/meta/llama-3.1-8b-instruct", { messages: [{ role: "system", content: SYSTEM }, { role: "user", content: msg }], max_tokens: 400 });
  return reply({ answer: String(out.response || "").trim(), tier: "free" }, 200, h);
}

// ---------- Make your call ----------
// calls:<YYYY-MM-DD> -> { uid: { b: band 0-4, n: name, t: time } }
// board -> { uid: { n, right, total } }, tallied:<day> -> "1"
const sgDay = (d) => new Date(d.getTime() + 8 * 3600e3).toISOString().slice(0, 10);

async function calls(req, env, h, url) {
  if (!env.CALLS) return reply({ error: "storage not set up" }, 503, h);
  if (req.method === "POST") {
    const b = await req.json().catch(() => ({}));
    const uid = String(b.uid || "").replace(/[^a-z0-9]/gi, "").slice(0, 24);
    const name = String(b.name || "").replace(/[<>]/g, "").trim().slice(0, 24) || "Anonymous";
    const day = String(b.day || "");
    const band = Number(b.band);
    if (!uid || !/^\d{4}-\d\d-\d\d$/.test(day) || !(band >= 0 && band <= 4)) return reply({ error: "bad call" }, 400, h);
    if (day <= sgDay(new Date())) return reply({ error: "calls close at midnight before the day" }, 400, h);
    const key = "calls:" + day;
    const all = (await env.CALLS.get(key, "json")) || {};
    all[uid] = { b: band, n: name, t: Date.now() };
    await env.CALLS.put(key, JSON.stringify(all), { expirationTtl: 60 * 86400 });
    return reply({ ok: true }, 200, h);
  }
  const uid = (url.searchParams.get("uid") || "").replace(/[^a-z0-9]/gi, "");
  const days = (url.searchParams.get("days") || "").split(",").filter((d) => /^\d{4}-\d\d-\d\d$/.test(d)).slice(0, 15);
  const out = {};
  for (const d of days) {
    const all = (await env.CALLS.get("calls:" + d, "json")) || {};
    const counts = [0, 0, 0, 0, 0];
    for (const v of Object.values(all)) counts[v.b]++;
    out[d] = { counts, mine: all[uid] ? all[uid].b : null };
  }
  return reply(out, 200, h);
}

async function noonPsi(day) {
  const r = await fetch("https://api-open.data.gov.sg/v2/real-time/api/psi?date=" + day);
  if (!r.ok) return null;
  const js = await r.json();
  for (const it of (js.data && js.data.items) || []) {
    if (!String(it.timestamp).includes("T12:00")) continue;
    const rd = it.readings || {};
    const k = Object.keys(rd).find((x) => x.toLowerCase() === "psi_twenty_four_hourly");
    if (!k) continue;
    const v = rd[k];
    return v.national != null ? v.national : Math.max(...Object.values(v));
  }
  return null;
}

async function leaderboard(env) {
  if (!env.CALLS) return { error: "storage not set up" };
  const board = (await env.CALLS.get("board", "json")) || {};
  const today = sgDay(new Date());
  let changed = false;
  for (let k = 1; k <= 7; k++) {
    const day = sgDay(new Date(Date.now() - k * 86400e3));
    if (day >= today || (await env.CALLS.get("tallied:" + day))) continue;
    const psi = await noonPsi(day);
    if (psi == null) continue;
    const truth = bandOf(psi);
    const all = (await env.CALLS.get("calls:" + day, "json")) || {};
    for (const [uid, c] of Object.entries(all)) {
      const row = board[uid] || { n: c.n, right: 0, total: 0 };
      row.n = c.n; row.total++; if (c.b === truth) row.right++;
      board[uid] = row;
    }
    await env.CALLS.put("tallied:" + day, JSON.stringify({ psi, truth }), { expirationTtl: 90 * 86400 });
    changed = true;
  }
  if (changed) await env.CALLS.put("board", JSON.stringify(board));
  const top = Object.values(board).filter((r) => r.total > 0)
    .sort((a, b) => b.right - a.right || a.total - b.total).slice(0, 20);
  return { top, players: Object.keys(board).length };
}

function reply(obj, status, headers) {
  return new Response(JSON.stringify(obj), { status, headers: { ...headers, "Content-Type": "application/json" } });
}

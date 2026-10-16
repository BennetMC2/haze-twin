// Haze Twin "Ask about the haze" relay (Cloudflare Worker).
// Holds the Claude API key so it never reaches the browser, and keeps use small:
// only the Haze Twin site may call it, questions are short, answers are short,
// and there is a daily cap (needs a KV namespace bound as USAGE; without it, no daily cap).
//
// Settings (Cloudflare dashboard > Worker > Settings > Variables):
//   ANTHROPIC_API_KEY  (secret)  your Claude API key
//   ALLOWED_ORIGIN               https://bennetmc2.github.io
//   DAILY_LIMIT                  e.g. 500 questions a day in total
//   MODEL                        optional, default claude-haiku-5-5

const SYSTEM = `You answer questions from the public about haze in Singapore, on the Haze Twin website.
Use only the forecast data provided (JSON) plus general knowledge about haze, PSI and Southeast Asian fires.
Rules:
- Plain, friendly English. 2 to 4 short paragraphs at most. No markdown, no bullet symbols, no headings.
- Give numbers from the data (PSI, % chances, dates). Say "best guess" for the middle forecast and give the likely range.
- Days more than 6 days ahead are less reliable; say so when relevant.
- Do not give medical advice. For health questions (exercise, masks, children, schools, conditions), state the PSI level and point to NEA's health advice at haze.gov.sg.
- If the data does not answer the question, say so briefly.
- If the question is not about haze, air quality, fires or weather in the region, politely say you can only help with the haze forecast.`;

const cors = (origin) => ({
  "Access-Control-Allow-Origin": origin,
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Vary": "Origin",
});

export default {
  async fetch(req, env) {
    const allowed = env.ALLOWED_ORIGIN || "https://bennetmc2.github.io";
    const origin = req.headers.get("Origin") || "";
    const h = cors(allowed);
    if (req.method === "OPTIONS") return new Response(null, { headers: h });
    if (req.method !== "POST" || origin !== allowed) return new Response("Not allowed", { status: 403, headers: h });

    let body;
    try { body = await req.json(); } catch { return json({ error: "bad request" }, 400, h); }
    const q = String(body.question || "").slice(0, 300).trim();
    if (!q) return json({ error: "empty question" }, 400, h);
    const context = JSON.stringify(body.context || {}).slice(0, 12000);

    // daily cap
    const limit = Number(env.DAILY_LIMIT || 500);
    const day = new Date().toISOString().slice(0, 10);
    if (env.USAGE) {
      const used = Number(await env.USAGE.get(day)) || 0;
      if (used >= limit) return json({ error: "daily limit reached" }, 429, h);
      await env.USAGE.put(day, String(used + 1), { expirationTtl: 172800 });
    }

    const r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: { "content-type": "application/json", "x-api-key": env.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01" },
      body: JSON.stringify({
        model: env.MODEL || "claude-haiku-5-5",
        max_tokens: 400,
        system: SYSTEM,
        messages: [{ role: "user", content: "Forecast data:\n" + context + "\n\nQuestion: " + q }],
      }),
    });
    if (!r.ok) return json({ error: "model error " + r.status }, 502, h);
    const out = await r.json();
    const answer = (out.content || []).filter((c) => c.type === "text").map((c) => c.text).join("\n").trim();
    return json({ answer }, 200, h);
  },
};

function json(obj, status, headers) {
  return new Response(JSON.stringify(obj), { status, headers: { ...headers, "Content-Type": "application/json" } });
}

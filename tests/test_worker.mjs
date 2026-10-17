// Tests for relay/worker.js. Run: node --test tests/test_worker.mjs  (Node 22, no npm packages)
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import worker from "../relay/worker.js";

const SITE = "https://bennetmc2.github.io";
const FORECAST = {
  issued: "2026-10-16T14:00:00+00:00",
  built: "2026-10-17T00:19:38.629Z",
  days: [{ label: "Sat 17 Oct" }, { label: "Sun 18 Oct" }],
  regions: {
    national: {
      now: 215, mode: "haze", clear: 10.6, clearDayLabel: "Thu 22 Oct", clearBy: [0, 5.8],
      classes: [{ name: "Clears early", odds: 14.9, outcome: "PSI 100 or below by Mon 19 Oct." }],
      days: [
        { bands: [0, 0, 60.6, 39.4, 0], p5: 151, p25: 177, p50: 196, p75: 214, p95: 244 },
        { bands: [0, 5, 60, 35, 0], p5: 140, p25: 170, p50: 190, p75: 210, p95: 240 },
      ],
    },
  },
  season: { median: "16 Nov", p10: "31 Oct", p90: "29 Nov" },
};

let calls; // upstream messages seen
let forecastOk;
let ipCounter = 0;

beforeEach(() => {
  calls = { anthropic: [], ai: [] };
  forecastOk = true;
  globalThis.fetch = async (url, init = {}) => {
    const u = String(url);
    if (u.endsWith("/haze-twin/data/forecast.json")) {
      if (!forecastOk) return new Response("nope", { status: 500 });
      return new Response(JSON.stringify(FORECAST), { status: 200 });
    }
    if (u.startsWith("https://api.anthropic.com/")) {
      calls.anthropic.push(JSON.parse(init.body));
      return new Response(JSON.stringify({ content: [{ type: "text", text: "premium answer" }] }), { status: 200 });
    }
    throw new Error("unexpected fetch " + u);
  };
});

function makeEnv(extra = {}) {
  return {
    AI: { run: async (model, input) => { calls.ai.push({ model, input }); return { response: "free answer" }; } },
    ANTHROPIC_API_KEY: "sk-test",
    PREMIUM_CODES: "",
    ...extra,
  };
}

function req(body, { method = "POST", origin = SITE, path = "/", ip, headers = {} } = {}) {
  const h = { "Content-Type": "application/json", "CF-Connecting-IP": ip || "10.0.0." + ++ipCounter, ...headers };
  if (origin) h.Origin = origin;
  const init = { method, headers: h };
  if (body !== undefined && method !== "GET" && method !== "OPTIONS") init.body = typeof body === "string" ? body : JSON.stringify(body);
  return new Request("https://haze-ask.example.workers.dev" + path, init);
}

const json = async (r) => JSON.parse(await r.text());

test("empty PREMIUM_CODES never gives premium", async () => {
  for (const code of ["", " ", undefined, "anything-long-enough"]) {
    const r = await worker.fetch(req({ question: "Will it clear?", code }), makeEnv({ PREMIUM_CODES: "" }));
    assert.equal(r.status, 200);
    assert.equal((await json(r)).tier, "free");
  }
  // list of only empty entries
  const r = await worker.fetch(req({ question: "Will it clear?", code: "" }), makeEnv({ PREMIUM_CODES: " , ,," }));
  assert.equal((await json(r)).tier, "free");
  assert.equal(calls.anthropic.length, 0);
});

test("short or empty codes are rejected even if listed", async () => {
  const env = makeEnv({ PREMIUM_CODES: "abc, ,goodcode123" });
  for (const code of ["abc", "", "   ", "goodcode12"]) {
    const r = await worker.fetch(req({ question: "Will it clear?", code }), env);
    assert.equal((await json(r)).tier, "free", "code " + JSON.stringify(code));
  }
  const ok = await worker.fetch(req({ question: "Will it clear?", code: " goodcode123 " }), env);
  assert.equal((await json(ok)).tier, "premium");
  assert.equal(calls.anthropic.length, 1);
  assert.equal(calls.anthropic[0].model, "claude-haiku-5-5");
});

test("/calls and /leaderboard are gone (404)", async () => {
  for (const path of ["/calls", "/leaderboard", "/calls?uid=x&days=2026-10-18", "/other"]) {
    for (const method of ["GET", "POST"]) {
      const r = await worker.fetch(req({ uid: "a" }, { path, method }), makeEnv());
      assert.equal(r.status, 404, method + " " + path);
    }
  }
});

test("oversized body returns 413", async () => {
  const big = JSON.stringify({ question: "x", pad: "a".repeat(5000) });
  const r = await worker.fetch(req(big), makeEnv());
  assert.equal(r.status, 413);
  // Content-Length header lying small: still caught by the actual read
  const r2 = await worker.fetch(req(big, { headers: { "Content-Length": "10" } }), makeEnv());
  assert.equal(r2.status, 413);
});

test("question over 300 chars returns 400; empty returns 400", async () => {
  assert.equal((await worker.fetch(req({ question: "q".repeat(301) }), makeEnv())).status, 400);
  assert.equal((await worker.fetch(req({ question: "   " }), makeEnv())).status, 400);
  assert.equal((await worker.fetch(req("not json"), makeEnv())).status, 400);
});

test("GET / returns 405; OPTIONS is allowed", async () => {
  const r = await worker.fetch(req(undefined, { method: "GET" }), makeEnv());
  assert.equal(r.status, 405);
  const o = await worker.fetch(req(undefined, { method: "OPTIONS" }), makeEnv());
  assert.ok(o.status === 204 || o.status === 200);
  assert.equal(o.headers.get("Access-Control-Allow-Methods"), "POST, OPTIONS");
  assert.equal(o.headers.get("Access-Control-Allow-Origin"), SITE);
});

test("wrong origin returns 403", async () => {
  const r = await worker.fetch(req({ question: "hi" }, { origin: "https://evil.example" }), makeEnv());
  assert.equal(r.status, 403);
  const r2 = await worker.fetch(req({ question: "hi" }, { origin: null }), makeEnv());
  assert.equal(r2.status, 403);
});

test("client context is ignored; server summary is used", async () => {
  const MARKER = "INJECTED_MARKER_9f3a";
  const env = makeEnv({ PREMIUM_CODES: "goodcode123" });
  const r = await worker.fetch(req({ question: "Will it clear?", code: "goodcode123", context: { note: MARKER } }), env);
  assert.equal(r.status, 200);
  const sent = calls.anthropic[0].messages[0].content;
  assert.ok(sent.includes("Singapore national 24-hour PSI now: 215"), sent);
  assert.ok(sent.includes("Clears early"));
  assert.ok(sent.includes("151/177/196/214/244"));
  assert.ok(!sent.includes(MARKER));
  assert.ok(calls.anthropic[0].system.includes("not NEA's official forecast"));
  // free tier too
  await worker.fetch(req({ question: "Will it clear?", context: MARKER }), makeEnv());
  const freeMsg = calls.ai[0].input.messages[1].content;
  assert.ok(freeMsg.includes("PSI now: 215") && !freeMsg.includes(MARKER));
  assert.equal(calls.ai[0].model, "@cf/meta/llama-3.1-8b-instruct");
});

test("forecast fetch failure returns 503", async () => {
  forecastOk = false;
  const r = await worker.fetch(req({ question: "Will it clear?" }), makeEnv());
  assert.equal(r.status, 503);
  assert.deepEqual(await json(r), { error: "forecast unavailable" });
});

test("upstream errors return 502 without leaking the upstream body", async () => {
  const env = makeEnv({ AI: { run: async () => { throw new Error("SECRET upstream detail"); } } });
  const r = await worker.fetch(req({ question: "Will it clear?" }), env);
  assert.equal(r.status, 502);
  assert.ok(!(await r.text()).includes("SECRET"));
});

test("in-memory rate limit returns 429 after the limit", async () => {
  const env = makeEnv();
  const ip = "192.0.2.77";
  const statuses = [];
  for (let i = 0; i < 12; i++) statuses.push((await worker.fetch(req({ question: "Will it clear?" }, { ip }), env)).status);
  assert.deepEqual(statuses.slice(0, 10), Array(10).fill(200));
  assert.equal(statuses[10], 429);
  assert.equal(statuses[11], 429);
});

test("RATE_LIMITER binding is used when present", async () => {
  const keys = [];
  const env = makeEnv({ RATE_LIMITER: { limit: async ({ key }) => { keys.push(key); return { success: keys.length <= 2 }; } } });
  const ip = "198.51.100.5";
  const s = [];
  for (let i = 0; i < 3; i++) s.push((await worker.fetch(req({ question: "Will it clear?" }, { ip }), env)).status);
  assert.deepEqual(s, [200, 200, 429]);
  assert.deepEqual(keys, [ip, ip, ip]);
});

test("daily premium budget falls back to free above PREMIUM_DAILY_MAX", async () => {
  const store = new Map();
  const USAGE = { get: async (k) => store.get(k) ?? null, put: async (k, v) => { store.set(k, v); } };
  const env = makeEnv({ PREMIUM_CODES: "goodcode123", USAGE, PREMIUM_DAILY_MAX: "2" });
  const tiers = [];
  for (let i = 0; i < 3; i++) tiers.push((await json(await worker.fetch(req({ question: "Will it clear?", code: "goodcode123" }), env))).tier);
  assert.deepEqual(tiers, ["premium", "premium", "free"]);
  assert.equal(store.get("premium:" + new Date().toISOString().slice(0, 10)), "2");
});

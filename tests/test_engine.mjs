// Regression tests for the forecast engine. Run: node --test tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const E = require('../engine/engine.js');

const ARR = [-18, -12, -6, 0, 6, 12, 18, 24, 30, 36, 42, 48, 54, 60, 66, 72, 78, 84, 90, 96, 102, 108, 114, 120, 126, 132, 138, 150, 162, 174, 186, 198, 210, 222, 234, 246, 258, 270, 282, 294, 306, 318, 330, 342, 354, 366];
const BINS = [-96, -72, -48, -24, 0, 24, 48, 72, 96, 120, 144, 168, 192, 216, 240, 264, 288, 312, 336, 360];
const f32 = (a) => ({ dtype: 'float32', b64: Buffer.from(new Float32Array(a).buffer).toString('base64') });

// a small synthetic twin: 4 weather forecasts, 2 fire regions; members differ in how much smoke reaches Singapore
function fixture({ t0 = '2026-10-16T14:00:00Z', pm = 80, pm24h = 0, sources = [], frp = 1000 } = {}) {
  const M = 4, A = ARR.length, R = 2, B = BINS.length;
  const S = [];
  for (let m = 0; m < M; m++) for (let a = 0; a < A; a++) for (let r = 0; r < R; r++) for (let b = 0; b < B; b++) {
    const age = ARR[a] - (BINS[b] + 12);
    S.push(age > 0 && age < 96 ? (0.5 + m) * (1 + 0.5 * Math.sin(a / 3 + m)) * Math.exp(-age / 48) * 1e-3 : 0);
  }
  const rainT = ARR.filter(a => a > -24);
  return {
    t0, members: ['c00', 'p01', 'p02', 'p03'], arrivals_h: ARR, emis_bins_h: BINS, age_efold_h: 96,
    regions: [{ id: 'ssum', island: 'Sumatra', frp0: frp, frp1: frp }, { id: 'ckal', island: 'Kalimantan', frp0: frp, frp1: frp }],
    S: f32(S), rain: { t_h: rainT, data: f32(new Array(M * rainT.length * R).fill(0)) },
    obs: { time: t0, pm24_h: pm24h, pm24_now: { national: pm, north: pm * 0.8, south: pm * 0.9, east: pm, west: pm, central: pm },
      psi_now: { national: Math.round(E.psiOf(pm)) }, pm1_at: {} },
    climate: { roni: 1.0, roni_period: 'JAS 2026', dmi: 0.2, dmi_period: 'May 2026' },
    sources, summary: {}, analogs: null,
  };
}
const run = (inp, n = 20000) => E.run(inp, { n, seed: 11 });

test('PSI conversion: round trip, edges and NaN are consistent', () => {
  for (const pm of [5, 12, 40, 55, 100, 150, 249, 300, 450]) assert.ok(Math.abs(E.pmOfPsi(E.psiOf(pm)) - pm) < 1e-6);
  assert.equal(E.psiOf(NaN), 0); assert.equal(E.bandOf(NaN), 0); assert.equal(E.bandOf(E.psiOf(NaN)), 0);
  assert.equal(E.psiOf(1000), 500, 'capped at the top of the scale');
  assert.equal(E.bandOf(301), 4); assert.equal(E.bandOf(100), 1); assert.equal(E.bandOf(101), 2);
});

test('percentiles and band odds come from one distribution', () => {
  const f = run(fixture({ pm: 120 }));
  for (const d of f.regions.national.days) {
    const pAbove = (k) => d.bands.slice(k).reduce((a, b) => a + b, 0);
    // if fewer than 5% of futures are hazardous, p95 cannot be above 300 (and the reverse)
    if (pAbove(4) < 4) assert.ok(d.p95 <= 301, `p95 ${d.p95} with P(>300) ${pAbove(4)}`);
    if (pAbove(4) > 6) assert.ok(d.p95 > 300, `p95 ${d.p95} with P(>300) ${pAbove(4)}`);
    if (pAbove(3) < 24) assert.ok(d.p75 <= 201, `p75 ${d.p75} with P(>200) ${pAbove(3)}`);
    if (pAbove(3) > 26) assert.ok(d.p75 > 200, `p75 ${d.p75} with P(>200) ${pAbove(3)}`);
  }
});

test('"above 300 in 6 days" counts every time step, so it is never below any single day', () => {
  const f = run(fixture({ pm: 160 }));
  const r = f.regions.national;
  const worstDay = Math.max(...r.days.slice(0, 6).map(d => d.bands[4]));
  assert.ok(r.markets.hazardous6 + 0.2 >= worstDay, `${r.markets.hazardous6} < ${worstDay}`);
  const worst200 = Math.max(...r.days.slice(0, 6).map(d => d.bands[3] + d.bands[4]));
  assert.ok(r.markets.above200_6 + 0.2 >= worst200);
});

test('missing or zero NEA reading marks the run degraded', () => {
  for (const pm of [0, NaN, null]) {
    const inp = fixture(); inp.obs.pm24_now = { national: pm };
    const f = run(inp, 4000);
    assert.ok(f.degraded && f.degraded.reasons.some(x => /NEA/.test(x)), `pm ${pm}`);
  }
  assert.equal(run(fixture(), 4000).degraded, null);
});

test('a failed fire or weather source marks the run degraded (missing is not zero)', () => {
  const f = run(fixture({ sources: [{ name: 'NASA FIRMS hotspots', status: 'failed' }] }), 4000);
  assert.ok(f.degraded.reasons.some(x => /FIRMS/.test(x)));
});

test('a reading taken after the model origin is compared with the air at that time', () => {
  const a = E.prepare(fixture({ pm24h: 0 })), b = E.prepare(fixture({ pm24h: 12 }));
  assert.equal(a.inputs.arrivals_h[a.anchorA], 0);
  assert.equal(b.inputs.arrivals_h[b.anchorA], 12);
  assert.notEqual(a.K0, b.K0, 'the smoke scale is fitted to the window ending at the reading');
});

test('24-hour means are time-weighted across the 6-hour to 12-hour grid change', () => {
  const m = E.prepare(fixture());
  for (const w of m.win24) assert.ok(Math.abs(w.w.reduce((x, y) => x + y, 0) - 1) < 1e-9);
  const i = ARR.indexOf(150); const w = m.win24[i];
  const byHour = Object.fromEntries(w.j.map((j, k) => [ARR[j], w.w[k]]));
  // window (126, 150]: each arrival stands for the time half way to its neighbours, clipped to the window:
  // 126 -> 126..129 (3 h), 132 -> 129..135 (6 h), 138 -> 135..144 (9 h), 150 -> 144..150 (6 h)
  const want = { 126: 3, 132: 6, 138: 9, 150: 6 };
  for (const [h, hrs] of Object.entries(want)) assert.ok(Math.abs(byHour[h] - hrs / 24) < 1e-9, `${h}: ${byHour[h]}`);
});

test('days are interpolated to noon Singapore time', () => {
  const f = run(fixture(), 4000);
  for (const d of f.days) { const h = new Date(d.iso).getUTCHours(); assert.equal(h, 4, d.iso); }
});

test('season outlook only runs August to December, and stale climate indices are not used', () => {
  assert.equal(run(fixture({ t0: '2027-03-01T00:00:00Z' }), 4000).season, null);
  const s = run(fixture(), 4000).season;
  assert.ok(s && s.experimental);
  assert.equal(s.climate.dmiUsed, false, 'May DMI is too old in October');
  assert.equal(s.climate.roniUsed, true);
});

test('same inputs and seed give the same forecast', () => {
  const a = run(fixture(), 5000), b = run(fixture(), 5000);
  assert.deepEqual(a.regions.national.days, b.regions.national.days);
});

test('news signal does not move the numbers', () => {
  const a = fixture(); const b = fixture(); b.signals = { growthShift: 0.04 };
  assert.deepEqual(run(a, 5000).regions.national.days, run(b, 5000).regions.national.days);
});

test('"gets worse" needs a material rise, not one point over a band edge', () => {
  const f = run(fixture({ pm: 140 }), 20000);   // PSI ~ 190
  const w = f.regions.national.classes.find(c => c.id === 'worse');
  assert.ok(w); // definition: peak >= max(200.5, 1.3 x now, now + 40) — checked via the outcome text range
  const m = /Peaks (\d+)/.exec(w.outcome || '');
  if (m) assert.ok(+m[1] >= Math.min(Math.round(f.regions.national.now * 1.3 / 5) * 5, 500) - 5);
});

test('a home city forecasts one place, with best and worst cases kept apart from the storylines', () => {
  const inp = fixture({ pm: 90 }); inp.city = { id: 'kl', name: 'Kuala Lumpur' }; inp.obs.time = inp.t0;
  const f = run(inp, 6000);
  assert.deepEqual(Object.keys(f.regions), ['national']);
  const cs = f.regions.national.classes;
  assert.equal(cs.filter(c => !c.hidden).length, 4);
  assert.deepEqual(cs.filter(c => c.hidden).map(c => c.id), ['bestcase', 'worstcase']);
  const sum = cs.filter(c => !c.hidden).reduce((a, c) => a + c.odds, 0);
  assert.ok(Math.abs(sum - 100) < 0.5, 'storylines split all futures');
});

#!/usr/bin/env node
/* Official run: site/data/inputs.json -> forecast.json, history.json, record.json.
 * Keeps an append-only log of issued forecasts in history/ so the record can be scored.
 * Usage: node engine/run.js [--n 100000] [--no-history]
 */
'use strict';
const fs = require('fs');
const path = require('path');
const E = require('./engine.js');

const ROOT = path.resolve(__dirname, '..');
const DATA = path.join(ROOT, 'site', 'data');
const HIST = path.join(ROOT, 'history');
const args = process.argv.slice(2);
const N = args.includes('--n') ? parseInt(args[args.indexOf('--n') + 1], 10) : 100000;
const noHistory = args.includes('--no-history');

const readJSON = (p, d) => { try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) { return d; } };
const writeJSON = (p, v) => { fs.mkdirSync(path.dirname(p), { recursive: true }); fs.writeFileSync(p, JSON.stringify(v)); };

const inputs = readJSON(path.join(DATA, 'inputs.json'));
if (!inputs) { console.error('no inputs.json'); process.exit(1); }
// the daily learning step's current corrections travel with the inputs, so a rerun in the browser uses them too
const learnParams = readJSON(path.join(HIST, 'learn', 'params.json'), null);
if (learnParams && !inputs.sample) { inputs.learn = learnParams; writeJSON(path.join(DATA, 'inputs.json'), inputs); }
const t0 = new Date(inputs.t0);
const seed = Math.floor(t0.getTime() / 3600e3) % 2147483647;
const started = Date.now();
const crypto = require('crypto');
const engineHash = crypto.createHash('sha256').update(fs.readFileSync(path.join(__dirname, 'engine.js'))).digest('hex').slice(0, 12);
const inputHash = crypto.createHash('sha256').update(JSON.stringify(inputs)).digest('hex').slice(0, 12);
const fc = E.run(inputs, { n: N, seed });
const issued = new Date();
fc.issued = issued.toISOString();                 // when this forecast was actually made
fc.t0 = inputs.t0;                                 // model origin: the weather and fire data
fc.obsTime = (inputs.obs && inputs.obs.time) || inputs.t0;   // latest NEA reading used
fc.version = { engine: engineHash, inputs: inputHash, calibration: E.CALIB.version };
fc.id = issued.toISOString().replace(/[:.]/g, '').slice(0, 15) + 'Z-' + inputHash;
fc.cycle = inputs.cycle;
fc.seconds = Math.round((Date.now() - started) / 100) / 10;
console.log(`engine: ${N} futures in ${fc.seconds}s, effective sample ${fc.ess}, anchored ${fc.anchored}`);

const histDir = noHistory || inputs.sample ? path.join(DATA, '_demo_history') : HIST;
const runs = readJSON(path.join(histDir, 'runs.json'), []);
const obsLog = readJSON(path.join(histDir, 'obs.json'), {});

// why the odds moved, against the previous run that is at least 1 hour older
const prev = runs.filter(r => new Date(r.issued || r.t0) < new Date(Date.now() - 3600e3) && !!r.sample === !!inputs.sample).slice(-1)[0];
fc.why = {};
for (const reg of E.NEA_REGIONS) {
  fc.why[reg] = prev ? E.why({ regions: prev.regions }, fc, prev.summary, inputs.summary, reg) : { deltas: [], drivers: [] };
}
fc.prevLabel = prev ? prev.t0 : null;

// append this issuance (compact, never overwritten: every hourly forecast gets its own entry)
const entry = {
  id: fc.id, issued: fc.issued, obs_time: fc.obsTime, version: fc.version, degraded: fc.degraded,
  t0: inputs.t0, built: fc.built, cycle: inputs.cycle, sample: !!inputs.sample, summary: inputs.summary,
  learn: inputs.learn ? { bias: inputs.learn.bias, spread: inputs.learn.spread } : null,
  regions: Object.fromEntries(Object.entries(fc.regions).filter(([k]) => k === 'national').map(([k, v]) => [k, {
    now: v.now, mode: v.mode, clear: v.clear,
    classes: v.classes.map(c => ({ id: c.id, name: c.name, odds: c.odds })),
    days: v.days.map((d, i) => ({ iso: fc.days[i].iso, p5: d.p5, p50: d.p50, p95: d.p95, cams: k === 'national' && fc.cams ? fc.cams.days[i] : undefined }))
  }]))
};
if (!runs.some(r => r.id === entry.id)) runs.push(entry);
runs.sort((a, b) => String(a.issued || a.t0).localeCompare(String(b.issued || b.t0)));
while (runs.length > 800) runs.shift();

// observed 24-h PSI at noon SGT, per region
for (const [h, row] of (inputs.obs.psi_hist || [])) {
  const t = new Date(t0.getTime() + h * 3600e3);
  const sg = new Date(t.getTime() + 8 * 3600e3);
  if (sg.getUTCHours() !== 12 || sg.getUTCMinutes() !== 0) continue;
  obsLog[t.toISOString().slice(0, 13)] = row;
}

// record: forecasts issued ~2 days ahead vs what NEA measured
function record(region) {
  const out = [];
  const keys = Object.keys(obsLog).sort().slice(-30);
  for (const k of keys) {
    const target = new Date(k + ':00:00Z');
    const obs = obsLog[k][region];
    if (obs == null) continue;
    let best = null, bestGap = 1e9;
    for (const r of runs) {
      if (r.sample) continue;
      const lead = (target - new Date(r.issued || r.t0)) / 3600e3;   // lead from when it was actually issued
      if (lead < 36 || lead > 60) continue;
      if (r.obs_time && new Date(r.obs_time) > new Date(target.getTime() - 36 * 3600e3)) continue;   // no later readings leaking in
      if (Math.abs(lead - 48) < bestGap) { bestGap = Math.abs(lead - 48); best = r; }
    }
    if (!best || !best.regions[region]) continue;
    const d = best.regions[region].days.find(x => x.iso.slice(0, 13) === k);
    if (!d) continue;
    out.push({ date: k, issued: best.issued || best.t0, p5: d.p5, p50: d.p50, p95: d.p95, obs, hit: obs >= d.p5 && obs <= d.p95, persist: best.regions[region].now });
  }
  const hits = out.filter(x => x.hit).length;
  const maeF = out.length ? out.reduce((a, x) => a + Math.abs(x.p50 - x.obs), 0) / out.length : null;
  const maeP = out.length ? out.reduce((a, x) => a + Math.abs(x.persist - x.obs), 0) / out.length : null;
  return { days: out, n: out.length, hits, skill: maeF != null && maeP > 0 ? Math.round((1 - maeF / maeP) * 100) / 100 : null };
}
const rec = Object.fromEntries(E.NEA_REGIONS.map(r => [r, record(r)]));

// history for the "odds across runs" chart
const hist = runs.slice(-28).map(r => ({ t0: r.t0, sample: r.sample, regions: Object.fromEntries(Object.entries(r.regions).map(([k, v]) => [k, { mode: v.mode, clear: v.clear, odds: v.classes.map(c => c.odds) }])) }));

// a run that fails its checks is not published: the site keeps the last good forecast
if (fc.degraded && !inputs.sample && !process.env.ALLOW_DEGRADED) {
  console.error('NOT PUBLISHED, degraded: ' + fc.degraded.reasons.join('; '));
  writeJSON(path.join(HIST, 'last-degraded.json'), { issued: fc.issued, reasons: fc.degraded.reasons });
  process.exit(3);
}
writeJSON(path.join(DATA, 'forecast.json'), fc);
writeJSON(path.join(DATA, 'history.json'), hist);
writeJSON(path.join(DATA, 'record.json'), rec);
writeJSON(path.join(histDir, 'runs.json'), runs);
writeJSON(path.join(histDir, 'obs.json'), obsLog);
const nat = fc.regions.national;
console.log(`national now ${nat.now}, clear by ${fc.days[5].label}: ${nat.clear}%`);
console.log('classes: ' + nat.classes.map(c => `${c.name} ${c.odds}%`).join(' | '));
console.log('days p50: ' + nat.days.map((d, i) => `${fc.days[i].label} ${d.p50}`).join(', '));

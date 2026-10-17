#!/usr/bin/env node
/* Home-city runs: site/data/cities/<id>/inputs.json -> forecast.json beside it.
 * No history or scoring yet (those are Singapore-only for now).
 * Usage: node engine/run_city.js [--n 60000] */
'use strict';
const fs = require('fs');
const path = require('path');
const E = require('./engine.js');
const DIR = path.join(__dirname, '..', 'site', 'data', 'cities');
const args = process.argv.slice(2);
const N = args.includes('--n') ? parseInt(args[args.indexOf('--n') + 1], 10) : 60000;
let idx; try { idx = JSON.parse(fs.readFileSync(path.join(DIR, 'index.json'), 'utf8')); } catch (e) { console.log('no cities'); process.exit(0); }
let kRef = null;
try { kRef = E.prepare(JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'site', 'data', 'inputs.json'), 'utf8'))).K0; } catch (e) { console.log('no Singapore inputs for the smoke scale'); }
const ok = [];
for (const c of idx.cities || []) {
  const p = path.join(DIR, c.id, 'inputs.json');
  try {
    const inputs = JSON.parse(fs.readFileSync(p, 'utf8'));
    if (kRef) inputs.kRef = kRef;
    // no track record for cities yet, so for the days CAMS covers, the best guess is pulled half way to CAMS
    // (scaled to the city's reading). Singapore's daily learning sets this weight from scores; cities get a fixed one.
    inputs.learn = { date: 'city-default', cams: [0.5, 0.5, 0] };
    fs.writeFileSync(p, JSON.stringify(inputs));
    const fc = E.run(inputs, { n: N, seed: Math.floor(new Date(inputs.t0).getTime() / 3600e3) % 2147483647 });
    fc.issued = new Date().toISOString(); fc.t0 = inputs.t0; fc.obsTime = inputs.obs.time; fc.city = inputs.city; fc.why = {}; fc.cycle = inputs.cycle;
    if (fc.degraded) { console.log(`${c.name}: not published (${fc.degraded.reasons.join('; ')})`); continue; }
    fs.writeFileSync(path.join(DIR, c.id, 'forecast.json'), JSON.stringify(fc));
    const r = fc.regions.national;
    console.log(`${c.name}: now ${r.now}, ` + r.classes.filter(x => !x.hidden).map(x => `${x.name} ${x.odds}%`).join(' | '));
    ok.push(c);
  } catch (e) { console.log(`${c.name}: failed (${e.message})`); }
}
idx.cities = ok; idx.run = new Date().toISOString();
fs.writeFileSync(path.join(DIR, 'index.json'), JSON.stringify(idx));

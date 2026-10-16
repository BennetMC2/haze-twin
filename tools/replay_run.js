#!/usr/bin/env node
/* Run the twin on every replay input and keep a compact forecast per issue date.
 * Usage: node tools/replay_run.js <inputs dir> <out file> [--n 30000] */
'use strict';
const fs = require('fs'); const path = require('path');
const E = require('../engine/engine.js');
const [dir, outFile] = process.argv.slice(2);
const N = process.argv.includes('--n') ? +process.argv[process.argv.indexOf('--n') + 1] : 30000;
const out = fs.existsSync(outFile) ? JSON.parse(fs.readFileSync(outFile, 'utf8')) : {};
for (const f of fs.readdirSync(dir).filter(f => f.endsWith('.json')).sort()) {
  const day = f.replace('.json', '');
  try {
    const inp = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
    const t = Date.now();
    const res = E.run(inp, { n: N, seed: 7 });
    const r = res.regions.national;
    out[day] = {
      t0: inp.t0, members: inp.members.length, now: r.now, ess: res.ess,
      days: res.days.map((d, i) => ({ iso: d.iso, tier: d.tier, ...r.days[i] })),
      clearBy: r.clearBy, clear: r.clear, mode: r.mode, markets: r.markets,
      classes: r.classes.map(c => ({ id: c.id, odds: c.odds })), sourceShare: res.sourceShare,
      blend: res.blend ? res.blend.weight : null,
    };
    console.log(day, 'now', r.now, 'p50', r.days.slice(0, 6).map(d => d.p50).join(' '), ((Date.now() - t) / 1000).toFixed(1) + 's');
  } catch (e) { console.log(day, 'failed', e.message); }
}
fs.mkdirSync(path.dirname(outFile), { recursive: true });
fs.writeFileSync(outFile, JSON.stringify(out));

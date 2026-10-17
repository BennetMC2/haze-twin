/* Haze Twin engine: Monte Carlo over fire emissions on top of per-member back-trace footprints.
 * Runs in the browser (window.HazeEngine, also inside a Web Worker) and in Node (module.exports).
 * No dependencies.
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.HazeEngine = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /* ---------- helpers ---------- */
  function rng(seed) {
    let a = seed >>> 0;
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function gauss(r) { let u = 0, v = 0; while (u === 0) u = r(); while (v === 0) v = r(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); }
  function b64bytes(s) {
    if (typeof Buffer !== 'undefined') { const b = Buffer.from(s, 'base64'); return new Uint8Array(b.buffer, b.byteOffset, b.byteLength); }
    const bin = atob(s); const out = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i); return out;
  }
  function decode(obj) {
    const bytes = b64bytes(obj.b64);
    const buf = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    if (obj.dtype === 'float32') return new Float32Array(buf);
    if (obj.dtype === 'int8') return new Int8Array(buf);
    if (obj.dtype === 'uint16log') { const q = new Uint16Array(buf); const out = new Float32Array(q.length); const L = Math.log1p(obj.smax / obj.s0); for (let i = 0; i < q.length; i++) out[i] = q[i] ? obj.s0 * Math.expm1(q[i] / 65535 * L) : 0; return out; }
    throw new Error('unknown dtype ' + obj.dtype);
  }
  const quantile = (sorted, p) => sorted[Math.min(sorted.length - 1, Math.max(0, Math.round(p * (sorted.length - 1))))];

  /* NEA PSI sub-index for 24-hour PM2.5 (µg/m³). Breakpoints: 12→50, 55→100, 150→200, 250→300, 350→400, 500→500.
     These match NEA's published 1-hour bands (55/150/250); check against NEA's PSI table before relying on them. */
  const BP = [[0, 0], [12, 50], [55, 100], [150, 200], [250, 300], [350, 400], [500, 500]];
  function psiOf(pm) {
    if (!(pm > 0)) return 0;          // NaN, missing or <= 0 all map to 0 (and bandOf(0) is Good), consistently
    if (pm >= 500) return 500;        // top of the PSI scale
    for (let i = 1; i < BP.length; i++) {
      if (pm <= BP[i][0]) { const c0 = BP[i - 1][0], c1 = BP[i][0], s0 = BP[i - 1][1], s1 = BP[i][1]; return s0 + (pm - c0) * (s1 - s0) / (c1 - c0); }
    }
    return 500 + (pm - 500) * 0.5;
  }
  function pmOfPsi(psi) {
    if (!(psi > 0)) return 0;
    for (let i = 1; i < BP.length; i++) {
      if (psi <= BP[i][1]) { const c0 = BP[i - 1][0], c1 = BP[i][0], s0 = BP[i - 1][1], s1 = BP[i][1]; return c0 + (psi - s0) * (c1 - c0) / (s1 - s0); }
    }
    return 500 + (psi - 500) * 2;
  }
  const BANDS = [{ max: 50, name: 'Good' }, { max: 100, name: 'Moderate' }, { max: 200, name: 'Unhealthy' }, { max: 300, name: 'Very unhealthy' }, { max: Infinity, name: 'Hazardous' }];
  function bandOf(v) { if (!(v > 0)) return 0; const x = Math.round(v); for (let i = 0; i < BANDS.length; i++) if (x <= BANDS[i].max) return i; return 4; }

  const NEA_REGIONS = ['national', 'north', 'south', 'east', 'west', 'central'];
  const DAYNAME = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function sgt(date) { return new Date(date.getTime() + 8 * 3600e3); } // use getUTC* on the result

  /* ---------- prepare ---------- */
  function prepare(inputs, opts) {
    opts = opts || {};
    const M = inputs.members.length, A = inputs.arrivals_h.length, R = inputs.regions.length, B = inputs.emis_bins_h.length;
    const S = decode(inputs.S);
    if (S.length !== M * A * R * B) throw new Error('footprint size mismatch');
    const rain = decode(inputs.rain.data); const rT = inputs.rain.t_h.length;
    const t0 = new Date(inputs.t0);
    const obs = Object.assign({}, inputs.obs, opts.obs || {});
    // island groups
    const islands = ['Sumatra', 'Kalimantan', 'Other'];
    const isl = inputs.regions.map(r => r.island === 'Sumatra' ? 0 : r.island === 'Kalimantan' ? 1 : 2);
    // emission ratio for the day before yesterday's emission bins
    const ratio = inputs.regions.map(r => (r.frp0 > 0 ? Math.min(5, Math.max(0.2, (r.frp1 || r.frp0) / r.frp0)) : 1));
    const binMid = inputs.emis_bins_h.map(h => h + 12);
    // cumulative rain at future bin mids, per member & region
    const cum = new Float32Array(M * R * B);
    for (let m = 0; m < M; m++) for (let r = 0; r < R; r++) {
      let acc = 0, k = 0;
      const cumAt = [];
      for (let t = 0; t < rT; t++) { const th = inputs.rain.t_h[t]; if (th > 0) acc += Math.max(0, rain[(m * rT + t) * R + r] - 1.5); cumAt.push([th, acc]); }
      for (let b = 0; b < B; b++) {
        const h = binMid[b]; if (h <= 0) { cum[(m * R + r) * B + b] = 0; continue; }
        let v = 0; for (k = 0; k < cumAt.length; k++) { if (cumAt[k][0] <= h) v = cumAt[k][1]; else break; }
        cum[(m * R + r) * B + b] = v;
      }
    }
    // arrival indices
    const arrIdx = {}; inputs.arrivals_h.forEach((a, i) => (arrIdx[a] = i));
    const futA = []; inputs.arrivals_h.forEach((a, i) => { if (a >= 0) futA.push(i); });
    // day targets: noon SGT on each coming day the arrivals reach (days 1-6 core, later days extended)
    const days = [];
    const s0 = sgt(t0);
    const lastH = inputs.arrivals_h[inputs.arrivals_h.length - 1];
    for (let d = 1; d <= 20; d++) {
      const noon = Date.UTC(s0.getUTCFullYear(), s0.getUTCMonth(), s0.getUTCDate() + d, 12) - 8 * 3600e3;
      const h = (noon - t0.getTime()) / 3600e3;
      if (h > lastH + 3) break;
      let best = futA[0]; for (const i of futA) if (Math.abs(inputs.arrivals_h[i] - h) < Math.abs(inputs.arrivals_h[best] - h)) best = i;
      const dd = sgt(new Date(noon));
      days.push({ d, h, ai: best, label: DAYNAME[dd.getUTCDay()] + ' ' + dd.getUTCDate() + ' ' + MONTHS[dd.getUTCMonth()], short: dd.getUTCDate() + ' ' + MONTHS[dd.getUTCMonth()], dow: DAYNAME[dd.getUTCDay()], iso: new Date(noon).toISOString(), tier: d <= 6 ? 'core' : 'extended' });
    }
    // 24-hour means: time-weighted over the arrivals in (a-24, a]. Each arrival stands for the interval
    // half way to its neighbours, clipped to the window, so the 6-hourly and 12-hourly parts of the grid count by time.
    const AH = inputs.arrivals_h;
    const win24 = AH.map((a) => {
      const w = [], wt = [];
      AH.forEach((b, j) => {
        const lo = Math.max(a - 24, j > 0 ? (AH[j - 1] + b) / 2 : b - 3), hi = Math.min(a, j < AH.length - 1 ? (b + AH[j + 1]) / 2 : b + 3);
        if (hi - lo > 1e-9) { w.push(j); wt.push(hi - lo); }   // overlap of this arrival's interval with (a-24, a]
      });
      const t = wt.reduce((x, y) => x + y, 0) || 1; return { j: w, w: wt.map(x => x / t) };
    });
    // the latest 24-hour reading is compared with the simulated air at the time it was taken
    const obsH = (inputs.obs && typeof inputs.obs.pm24_h === 'number') ? inputs.obs.pm24_h : 0;
    let anchorA = 0; AH.forEach((a, j) => { if (a <= obsH + 3 && Math.abs(a - obsH) < Math.abs(AH[anchorA] - obsH)) anchorA = j; });
    // observed now
    const pmNow = obs.pm24_now || {};
    const okv = (v) => typeof v === 'number' && isFinite(v) && v > 0;
    const nat = okv(pmNow.national) ? pmNow.national : (Math.max(0, ...NEA_REGIONS.slice(1).map(r => okv(pmNow[r]) ? pmNow[r] : 0)) || null);
    const regRatio = NEA_REGIONS.map(r => (nat && pmNow[r] ? pmNow[r] / nat : 1));
    // prior median for K from the observed level now
    const bg0 = 18;
    let meanFire = 0; const aw = win24[anchorA];
    for (let m = 0; m < M; m++) aw.j.forEach((ai, k) => { for (let r = 0; r < R; r++) for (let b = 0; b < B; b++) {
      if (binMid[b] >= AH[ai]) continue;
      meanFire += aw.w[k] * S[((m * A + ai) * R + r) * B + b] * (binMid[b] < -24 ? ratio[r] : 1);
    } });
    meanFire /= M;
    let K0 = 0.12, anchored = false;
    if (nat && meanFire > 1e-6) { K0 = Math.max(2, nat - bg0) / meanFire; anchored = true; }
    // transport index per member: how much fire air reaches Singapore on days 2-4
    const trans = new Float64Array(M);
    for (let m = 0; m < M; m++) { let v = 0; for (const d of days.slice(1, 4)) { const off = (m * A + d.ai) * R * B; for (let k = 0; k < R * B; k++) v += S[off + k]; } trans[m] = v; }
    const tmean = trans.reduce((a, b) => a + b, 0) / M || 1; for (let m = 0; m < M; m++) trans[m] /= tmean;
    // regional fire weights (share of fire power) for the slow regional smoke reservoir
    const fw = inputs.regions.map(r => r.frp0 || 0); const fwt = fw.reduce((a, b) => a + b, 0) || 1; for (let q = 0; q < R; q++) fw[q] /= fwt;
    // emission bins inside the 72 hours before each arrival
    const win72 = inputs.arrivals_h.map(a => { const w = []; binMid.forEach((h, b) => { if (h <= a && h > a - 72) w.push(b); }); return w.length ? w : [binMid.findIndex(h => h > a - 12) >= 0 ? binMid.findIndex(h => h > a - 12) : B - 1]; });
    const nowBin = binMid.findIndex(h => h > -24 && h < 0);
    const AGE_BUILT = inputs.age_efold_h || 96;
    const ageTabs = [48, 64, 80].map(tau => { const g = new Float32Array(A * B); inputs.arrivals_h.forEach((a, i) => { binMid.forEach((h, b) => { const age = Math.max(0, a - h); g[i * B + b] = tau >= AGE_BUILT ? 1 : Math.exp(-age * (1 / tau - 1 / AGE_BUILT)); }); }); return g; });
    // past-season analogs from NEA history and the news signal
    const analogs = inputs.analogs && inputs.analogs.paths && inputs.analogs.paths.length ? inputs.analogs : null;
    // the news signal is shown on the page but no longer moves the numbers: its effect has not been validated
    const growthShift = 0;
    // checks that decide whether this run is fit to publish
    const degraded = [];
    if (!nat) degraded.push('no valid NEA PM2.5 reading');
    for (const src of (inputs.sources || [])) {
      if (src.status === 'failed' && /FIRMS|GEFS/i.test(src.name || '')) degraded.push((src.name || 'a data source') + ' failed');
    }
    if (!isFinite(meanFire)) degraded.push('the air-path calculation returned invalid numbers');
    return { inputs, M, A, R, B, S, cum, isl, islands, ratio, binMid, arrIdx, futA, days, obs, nat, regRatio, K0, anchored, t0, trans, win24, fw, win72, nowBin, analogs, growthShift, ageTabs, anchorA, obsH, degraded };
  }

  /* ---------- Monte Carlo ---------- */
  function simulate(model, opts) {
    opts = opts || {};
    const N = opts.n || 100000;
    const r = rng(opts.seed || 1);
    const { M, A, R, B, S, cum, isl, ratio, binMid, arrIdx, obs, fw } = model;
    const arr = model.inputs.arrivals_h;
    const C = new Float32Array(N * A);            // PM2.5 at each arrival (µg/m³)
    const memb = new Uint16Array(N);
    const share = new Float32Array(N * 3);        // island shares of fire smoke, days 1-3
    const rshare = new Float32Array(N * R);       // source-region shares, days 1-3
    const rv = new Float64Array(R);
    const pThalf = new Float32Array(N), pRain = new Float32Array(N), pK = new Float32Array(N), pRho = new Float32Array(N);
    const logw = new Float64Array(N);
    const E = new Float64Array(R * B);
    const pastObs = [];
    for (const k in (obs.pm1_at || {})) { const ai = arrIdx[+k]; if (ai !== undefined && obs.pm1_at[k] > 0) pastObs.push([ai, Math.log(obs.pm1_at[k])]); }
    const pmNowLog = model.nat ? Math.log(model.nat) : null;
    const shareA = model.days.slice(0, 3).map(d => d.ai);
    const kalRegions = []; for (let q = 0; q < R; q++) if (isl[q] === 1) kalRegions.push(q);
    const report = opts.onProgress || null;
    const chunk = 5000;
    for (let s = 0; s < N; s++) {
      const m = Math.floor(r() * M); memb[s] = m;
      const K = model.K0 * Math.exp(gauss(r) * 0.6);
      const ef = [Math.exp(gauss(r) * 0.35), Math.exp(gauss(r) * 0.35), Math.exp(gauss(r) * 0.35)];
      const thalf = Math.exp(Math.log(4) + r() * Math.log(10));   // days for fire activity to halve without rain: 4-40 (peat fires persist until rain)
      const rs = 0.02 + r() * 0.06;           // fraction of fire put out per mm of rain above ~6 mm/day
      const grow = model.growthShift + gauss(r) * 0.04;   // new fires, per day (news signal shifts the middle)
      const rho = 0.2 + r() * 0.6;            // share of today's smoke that is regional haze, not tied to today's winds (the readings pick it)
      const dsat = 150 + r() * 200;           // smoke from direct transport levels off: dilution and mixing limit how high it can go (µg/m³)
      const ageTab = model.ageTabs[Math.floor(r() * 3)];
      const bg = Math.max(5, 18 + gauss(r) * 5);
      pThalf[s] = thalf; pRain[s] = rs; pK[s] = K / model.K0 * (ef[0] + ef[1]) / 2; pRho[s] = rho;
      for (let q = 0; q < R; q++) {
        const e = ef[isl[q]];
        for (let b = 0; b < B; b++) {
          const h = binMid[b];
          let x;
          if (h < -24) x = ratio[q];
          else if (h < 0) x = 1;
          else x = Math.pow(0.5, h / 24 / thalf) * Math.exp(-rs * cum[(m * R + q) * B + b]) * Math.exp(grow * h / 24);
          E[q * B + b] = x * e;
        }
      }
      // regional activity per emission bin, relative to the last 24 h
      let actNow = 0; for (let q = 0; q < R; q++) actNow += fw[q] * E[q * B + model.nowBin]; actNow = actNow || 1;
      const actBin = new Float64Array(B); for (let b = 0; b < B; b++) { let v = 0; for (let q = 0; q < R; q++) v += fw[q] * E[q * B + b]; actBin[b] = v / actNow; }
      const D0 = model.nat ? Math.max(0, model.nat - 18) : 0;
      const base = m * A * R * B;
      for (let a = 0; a < A; a++) {
        let sum = 0; const off = base + a * R * B; const go = a * B;
        for (let q = 0; q < R; q++) { const oq = off + q * B, eq = q * B; for (let b = 0; b < B; b++) sum += S[oq + b] * E[eq + b] * ageTab[go + b]; }
        let act = 0; const wb = model.win72[a]; for (const b of wb) act += actBin[b]; act /= wb.length;
        const x = (1 - rho) * K * sum;
        C[s * A + a] = bg + dsat * (1 - Math.exp(-x / dsat)) + rho * D0 * Math.min(act, 1.6);
      }
      // island shares (days 1-3)
      let s0 = 0, s1 = 0, s2 = 0; rv.fill(0);
      for (const a of shareA) {
        const off = base + a * R * B;
        for (let q = 0; q < R; q++) { let v = 0; for (let b = 0; b < B; b++) v += S[off + q * B + b] * E[q * B + b]; rv[q] += v; if (isl[q] === 0) s0 += v; else if (isl[q] === 1) s1 += v; else s2 += v; }
      }
      const st = s0 + s1 + s2 || 1; share[s * 3] = s0 / st; share[s * 3 + 1] = s1 / st; share[s * 3 + 2] = s2 / st;
      for (let q = 0; q < R; q++) rshare[s * R + q] = rv[q] / st;
      // likelihood of what NEA measured
      let lw = 0;
      // the 6-hour readings overlap the 24-hour mean and each other, so they share one effective weight:
      // sigma grows with the square root of their number instead of counting each as independent
      const sObs = 0.35 * Math.sqrt(Math.max(1, pastObs.length));
      for (const [ai, lo] of pastObs) { const d = (Math.log(C[s * A + ai]) - lo) / sObs; lw -= 0.5 * d * d; }
      if (pmNowLog !== null) {
        const w0 = model.win24[model.anchorA]; let pm24 = 0; w0.j.forEach((j, k) => { pm24 += w0.w[k] * C[s * A + j]; });
        const d = (Math.log(pm24) - pmNowLog) / 0.25; lw -= 0.5 * d * d;
      }
      logw[s] = lw;
      if (report && s % chunk === 0) report(s / N * 0.85);
    }
    // systematic resampling by likelihood
    let mx = -Infinity; for (let s = 0; s < N; s++) if (logw[s] > mx) mx = logw[s];
    const w = new Float64Array(N); let tot = 0; for (let s = 0; s < N; s++) { w[s] = Math.exp(logw[s] - mx); tot += w[s]; }
    let ess = 0; for (let s = 0; s < N; s++) { w[s] /= tot; ess += w[s] * w[s]; } ess = 1 / ess;
    const idx = new Uint32Array(N); { const u0 = r() / N; let c = w[0], j = 0; for (let s = 0; s < N; s++) { const u = u0 + s / N; while (u > c && j < N - 1) { j++; c += w[j]; } idx[s] = j; } }
    // 24-hour mean PM2.5 (national) at future arrivals
    const futA = model.futA; const F = futA.length;
    const pm24 = new Float32Array(N * F);
    for (let s = 0; s < N; s++) {
      const j = idx[s];
      for (let f = 0; f < F; f++) { const w = model.win24[futA[f]]; let v = 0; w.j.forEach((k, q) => { v += w.w[q] * C[j * A + k]; }); pm24[s * F + f] = v; }
    }
    // blend with how past haze seasons evolved, more at longer leads (twin dominates the first days)
    let blend = null;
    if (model.analogs && model.nat) {
      const an = model.analogs; const cw = []; let ct = 0; for (const w of an.w) { ct += w; cw.push(ct); }
      const psiNow = psiOf(model.nat);
      const lam = new Float32Array(F);
      for (let s = 0; s < N; s++) {
        const u = r() * ct; let k = 0; while (k < cw.length - 1 && cw[k] < u) k++;
        const path = an.paths[k]; const us = r();
        for (let f = 0; f < F; f++) {
          const L = model.inputs.arrivals_h[futA[f]] / 24; if (L <= 0) continue;
          const w = L / (L + 6);
          const l = Math.min(1, Math.max(0, (w - us) / 0.15 + 0.5));
          if (l <= 0) continue;
          const i = Math.min(path.length - 1, Math.floor(L)); const fr = Math.min(1, L - Math.floor(L));
          const r0 = i === 0 ? 1 + (path[0] - 1) * L : path[i - 1] + (path[Math.min(path.length - 1, i)] - path[i - 1]) * fr;
          const pmA = pmOfPsi(psiNow * r0);
          pm24[s * F + f] = (1 - l) * pm24[s * F + f] + l * pmA;
          if (s < 4000) lam[f] += l / 4000;
        }
      }
      blend = { analogDays: an.paths.length, years: an.years, weight: Array.from(lam, x => Math.round(x * 100) / 100) };
    }
    // calibration fitted on replays of 2015, 2019 and 2023 (in-sample; see history/replay/summary.json):
    // each future is widened around the median in log-PSI space, CALIB_LO below and CALIB_HI above, and
    // capped at PSI 500. This changes the samples themselves, so every percentile, band odds, storyline and
    // yes/no question below comes from this one distribution. tools/replay_score.py reads the same constants.
    {
      const tmp = new Float64Array(Math.min(N, 20000)); const stp = Math.max(1, Math.floor(N / tmp.length));
      for (let f = 0; f < F; f++) {
        const hh = model.inputs.arrivals_h[futA[f]];
        const kl = calibK(CALIB_LO, hh), kh = calibK(CALIB_HI, hh); if (kl === 1 && kh === 1) continue;
        let n = 0; for (let s = 0; s < N && n < tmp.length; s += stp) tmp[n++] = Math.log(Math.max(1, psiOf(pm24[s * F + f])));
        const srt = Array.from(tmp.subarray(0, n)).sort((x, y) => x - y); const med = srt[Math.floor(n / 2)];
        for (let s = 0; s < N; s++) {
          const v = Math.log(Math.max(1, psiOf(pm24[s * F + f])));
          const w = Math.min(Math.log(500), med + (v < med ? kl : kh) * (v - med));
          pm24[s * F + f] = pmOfPsi(Math.exp(w));
        }
      }
    }
    // daily learning (tools/learn.py): a small, capped correction for recent bias and spread, by lead
    // group (days 1-3, 4-6, 7-15). Comes from history/learn/params.json via inputs.learn.
    const L = opts.learn || model.inputs.learn || null;
    if (L && (L.bias || L.spread)) {
      const g = (hh) => hh <= 84 ? 0 : hh <= 156 ? 1 : 2;
      const tmp = new Float64Array(Math.min(N, 20000)); const stp = Math.max(1, Math.floor(N / tmp.length));
      for (let f = 0; f < F; f++) {
        const hh = model.inputs.arrivals_h[futA[f]]; if (hh <= 0) continue;
        const ramp = Math.min(1, hh / 24);
        const b = Math.max(-0.4, Math.min(0.4, ((L.bias || [])[g(hh)] || 0))) * ramp;
        const k = 1 + (Math.max(0.7, Math.min(1.5, (L.spread || [])[g(hh)] || 1)) - 1) * ramp;
        if (!b && k === 1) continue;
        let n = 0; for (let s = 0; s < N && n < tmp.length; s += stp) tmp[n++] = Math.log(Math.max(1, pm24[s * F + f]));
        const srt = Array.from(tmp.subarray(0, n)).sort((a, c) => a - c); const med = srt[Math.floor(n / 2)];
        for (let s = 0; s < N; s++) { const v = Math.log(Math.max(1, pm24[s * F + f])); pm24[s * F + f] = Math.exp(med + b + k * (v - med)); }
      }
    }
    if (report) report(0.9);
    return { N, F, pm24, idx, memb, share, rshare, pThalf, pRain, pK, pRho, ess, model, blend };
  }

  /* ---------- classes (outcome storylines) ---------- */
  // spread multipliers for days 1-3, 4-6 and 7-15, fitted on replayed seasons (history/replay/summary.json).
  // Low side widens the samples. High side widens only the printed upper range (p75, p95): widening the samples'
  // upside too made multi-day odds ("above 300 in 6 days") far too high, which the replays showed were already high.
  const CALIB_LO = [2.15, 1.45, 1.05];
  const CALIB_HI = [1.95, 1.8, 1.9];
  const calibK = (C, hh) => hh <= 0 ? 1 : hh < 18 ? 1 + (C[0] - 1) * hh / 18 : hh <= 84 ? C[0] : hh <= 156 ? C[1] : C[2];
  // the same widening applied to a list of quantiles [p5, p25, p50, p75, p95] (for scoring and the replay chart)
  function widenPsi(q, leadDays) {
    const hh = leadDays * 24; const kl = calibK(CALIB_LO, hh), kh = calibK(CALIB_HI, hh); const m = Math.log(Math.max(1, q[2]));
    return q.map(x => { const v = Math.log(Math.max(1, x)); return Math.round(Math.min(500, Math.exp(m + (v < m ? kl : kh) * (v - m)))); });
  }
  const CLASS_DEFS = {
    haze: [
      { id: 'early', name: 'Clears early', color: '--sD' },
      { id: 'later', name: 'Clears later in the week', color: '--sB' },
      { id: 'stays', name: 'Stays unhealthy', color: '--sA' },
      { id: 'worse', name: 'Gets worse', color: '--sC' }
    ],
    clear: [
      { id: 'clean', name: 'Stays clear', color: '--sD' },
      { id: 'brief', name: 'Brief haze', color: '--sB' },
      { id: 'returns', name: 'Haze returns', color: '--sA' },
      { id: 'worse', name: 'Heavy haze', color: '--sC' }
    ]
  };

  function regionPsi(sim, s, f, rr) {
    const a = sim.model.inputs.arrivals_h[sim.model.futA[f]];
    const k = 1 + (sim.model.regRatio[rr] - 1) * Math.exp(-a / 48);
    return psiOf(sim.pm24[s * sim.F + f] * k);
  }

  function summarize(sim, opts) {
    opts = opts || {};
    const model = sim.model; const { N, F } = sim; const days = model.days;
    const fOf = {}; model.futA.forEach((ai, f) => (fOf[ai] = f));
    const dayF = days.map(d => fOf[d.ai]);
    // each day's value is interpolated to exactly noon SGT between the two arrivals either side of it
    const AHf = model.futA.map(ai => model.inputs.arrivals_h[ai]);
    const dayI = days.map(d => { let j = 0; while (j < AHf.length - 2 && AHf[j + 1] < d.h) j++; const a = AHf[j], b = AHf[j + 1]; return [j, j + 1, b > a ? Math.min(1, Math.max(0, (d.h - a) / (b - a))) : 0]; });
    const dayVal = (s, d, rr) => { const [i0, i1, w] = dayI[d]; return (1 - w) * regionPsi(sim, s, i0, rr) + w * regionPsi(sim, s, i1, rr); };
    // yes/no questions are checked on every simulated time step, not just noon
    const f6 = AHf.reduce((n, a) => n + (a <= 6 * 24 + 12 ? 1 : 0), 0);
    const psiNowObs = (model.obs.psi_now || {});
    const out = { issued: model.inputs.t0, built: new Date().toISOString(), n: N, members: model.M, ess: Math.round(sim.ess),
      anchored: model.anchored, sample: !!model.inputs.sample, days: days.map(d => ({ label: d.label, short: d.short, dow: d.dow, iso: d.iso, h: d.h, tier: d.tier })),
      arrivals_h: model.futA.map(ai => model.inputs.arrivals_h[ai]), regions: {}, blend: sim.blend, signals: model.inputs.signals || null };
    const sampleIdx = []; { const r = rng(99); for (let k = 0; k < 240; k++) sampleIdx.push(Math.floor(r() * N)); }
    out.samples = sampleIdx.map(s => Array.from(sim.pm24.subarray(s * F, s * F + F), v => Math.round(v * 10) / 10));
    out.sampleMember = sampleIdx.map(s => sim.memb[sim.idx[s]]);
    const stride = Math.max(1, Math.ceil(N / 20000));
    const psiAll = new Float32Array(Math.ceil(N / stride) + 1);
    for (let rr = 0; rr < NEA_REGIONS.length; rr++) {
      const reg = NEA_REGIONS[rr];
      const now = psiNowObs[reg] != null ? psiNowObs[reg] : Math.round(regionPsi(sim, 0, 0, rr));
      const mode = now > 100 ? 'haze' : 'clear';
      const defs = CLASS_DEFS[mode];
      const cls = new Uint8Array(N);
      const dayVals = days.map(() => new Float32Array(N));
      const clearDay = new Int8Array(N); const peakV = new Float32Array(N); const peakD = new Int8Array(N);
      const nC = Math.min(6, days.length); const firstAll = new Int8Array(N);
      for (let s = 0; s < N; s++) {
        let cd = -1, pk = -1, pkd = 0, fa = -1;
        for (let d = 0; d < days.length; d++) {
          const v = dayVal(s, d, rr); dayVals[d][s] = v;
          if (fa < 0 && (mode === 'haze' ? v <= 100.5 : v > 100.5)) fa = d + 1;
          if (d >= nC) continue;
          if (cd < 0 && v <= 100.5) cd = d + 1;
          if (v > pk) { pk = v; pkd = d + 1; }
        }
        clearDay[s] = cd; peakV[s] = pk; peakD[s] = pkd; firstAll[s] = fa;
        let c;
        if (mode === 'haze') {
          // "gets worse" needs a real rise: into a higher band, above 200, and at least 30% (and 40 points) above now
          if (pk >= Math.max(200.5, now * 1.3, now + 40) && bandOf(pk) > bandOf(now)) c = 3;
          else if (cd > 0 && cd <= 3) c = 0;
          else if (cd > 0) c = 1;
          else c = 2;
        } else {
          let nHaze = 0; for (let d = 0; d < nC; d++) if (dayVals[d][s] > 100.5) nHaze++;
          if (pk > 200.5) c = 3; else if (nHaze === 0) c = 0; else if (nHaze <= 2) c = 1; else c = 2;
        }
        cls[s] = c;
      }
      // band odds per day + percentiles at each arrival
      const dayStats = days.map((d, k) => {
        const b = [0, 0, 0, 0, 0]; const v = dayVals[k];
        for (let s = 0; s < N; s++) b[bandOf(v[s])]++;
        const sub = []; for (let s = 0; s < N; s += stride) sub.push(v[s]); const sorted = Float32Array.from(sub).sort();
        const md = quantile(sorted, 0.5), hh = (k + 0.5) * 24;
        return { bands: b.map(x => Math.round(x / N * 1000) / 10), p5: Math.round(quantile(sorted, 0.05)), p25: Math.round(quantile(sorted, 0.25)), p50: Math.round(md), p75: Math.round(quantile(sorted, 0.75)), p95: Math.round(quantile(sorted, 0.95)) };
      });
      const pct = { p5: [], p25: [], p50: [], p75: [], p95: [] };
      for (let f = 0; f < F; f++) {
        let nn = 0; for (let s = 0; s < N; s += stride) psiAll[nn++] = regionPsi(sim, s, f, rr);
        const sorted = Float32Array.from(psiAll.subarray(0, nn)).sort();
        const md = quantile(sorted, 0.5), hh = out.arrivals_h[f];
        pct.p5.push(Math.round(quantile(sorted, 0.05))); pct.p25.push(Math.round(quantile(sorted, 0.25))); pct.p75.push(Math.round(quantile(sorted, 0.75))); pct.p50.push(Math.round(md)); pct.p95.push(Math.round(quantile(sorted, 0.95)));
      }
      let clearCount = 0; for (let s = 0; s < N; s++) if (dayVals[nC - 1][s] <= 100.5) clearCount++;
      const by = days.map(() => 0); for (let s = 0; s < N; s++) if (firstAll[s] > 0) for (let d = firstAll[s] - 1; d < days.length; d++) by[d]++;
      const clearBy = by.map(x => Math.round(x / N * 1000) / 10);
      // per-class summaries
      const counts = [0, 0, 0, 0]; for (let s = 0; s < N; s++) counts[cls[s]]++;
      const mean = (arr, sel) => { let t = 0, n = 0; for (let s = 0; s < N; s++) if (sel(s)) { t += arr(s); n++; } return n ? t / n : 0; };
      const allShareSum = mean(s => sim.share[sim.idx[s] * 3], () => true), allShareKal = mean(s => sim.share[sim.idx[s] * 3 + 1], () => true);
      const allT = mean(s => sim.pThalf[sim.idx[s]], () => true);
      let bRain = 0; model.binMid.forEach((h, b) => { if (Math.abs(h - 84) < Math.abs(model.binMid[bRain] - 84)) bRain = b; });
      const kalRain = (s) => { const m = sim.memb[sim.idx[s]]; let v = 0, n = 0; for (let q = 0; q < model.R; q++) if (model.isl[q] === 1) { v += model.cum[(m * model.R + q) * model.B + bRain]; n++; } return n ? v / n : 0; };
      const allRain = mean(kalRain, () => true);
      const classes = defs.map((def, c) => {
        const sel = (s) => cls[s] === c;
        const n = counts[c];
        const o = { id: def.id, name: def.name, color: def.color, odds: Math.round(n / N * 1000) / 10, n };
        if (!n) { o.outcome = ''; o.watch = ''; o.median = []; return o; }
        // median path
        const med = [];
        for (let f = 0; f < F; f++) { const v = []; for (let s = 0; s < N; s += Math.max(1, Math.floor(N / 4000))) if (cls[s] === c) v.push(regionPsi(sim, s, f, rr)); v.sort((x, y) => x - y); med.push(Math.round(quantile(v, 0.5) || 0)); }
        o.median = med;
        const cds = [], pks = [], pkds = []; for (let s = 0; s < N; s += Math.max(1, Math.floor(N / 8000))) if (cls[s] === c) { if (clearDay[s] > 0) cds.push(clearDay[s]); pks.push(peakV[s]); pkds.push(peakD[s]); }
        cds.sort((x, y) => x - y); pks.sort((x, y) => x - y); pkds.sort((x, y) => x - y);
        const dl = (k) => days[Number.isFinite(k) ? Math.max(0, Math.min(days.length - 1, k - 1)) : nC - 1].label;
        if (def.id === 'early' || def.id === 'later') o.outcome = 'PSI 100 or below by ' + dl(quantile(cds, 0.5)) + '.';
        else if (def.id === 'stays') { let lastAbove = 0; days.slice(0, nC).forEach((d, k) => { if (med[dayF[k]] > 100) lastAbove = k + 1; }); o.outcome = 'Above 100 through ' + dl(lastAbove || nC) + '. Middle path near ' + Math.round(med[dayF[Math.min(2, days.length - 1)]] / 5) * 5 + '.'; }
        else if (def.id === 'worse') o.outcome = 'Peaks ' + Math.round(quantile(pks, 0.1) / 5) * 5 + '–' + Math.round(quantile(pks, 0.9) / 5) * 5 + ' around ' + dl(quantile(pkds, 0.5)) + '.';
        else if (def.id === 'clean') o.outcome = 'PSI stays 100 or below all week.';
        else if (def.id === 'brief') o.outcome = 'One or two days above 100.';
        else if (def.id === 'returns') o.outcome = 'Above 100 for three days or more.';
        // what distinguishes this class
        const cs = mean(s => sim.share[sim.idx[s] * 3], sel), ck = mean(s => sim.share[sim.idx[s] * 3 + 1], sel);
        const ct = mean(s => sim.pThalf[sim.idx[s]], sel), cr = mean(kalRain, sel), cw = mean(s => model.trans[sim.memb[sim.idx[s]]], sel);
        // what sets these futures apart from the rest: compare against the other futures, always name the biggest differences
        const oth = (x) => !sel(x);
        const ocs = mean(s => sim.share[sim.idx[s] * 3], oth), ock = mean(s => sim.share[sim.idx[s] * 3 + 1], oth);
        const ot = mean(s => sim.pThalf[sim.idx[s]], oth), orr = mean(kalRain, oth), ow = mean(s => model.trans[sim.memb[sim.idx[s]]], oth);
        const cues = [];
        const rel = (a, b) => Math.abs(a - b) / Math.max(Math.abs(b), 1e-6);
        cues.push([rel(cr, orr) * 1.2, cr >= orr
          ? 'More rain over the fires: about ' + Math.round(cr) + ' mm in the next few days, against ' + Math.round(orr) + ' mm in the other futures.'
          : 'Less rain over the fires: about ' + Math.round(cr) + ' mm in the next few days, against ' + Math.round(orr) + ' mm in the other futures.']);
        cues.push([rel(cw, ow), cw >= ow
          ? 'Winds carry more smoke our way: about ' + Math.round((cw / Math.max(ow, 1e-9) - 1) * 100) + '% more than in the other futures.'
          : 'Winds carry less smoke our way: about ' + Math.round((1 - cw / Math.max(ow, 1e-9)) * 100) + '% less than in the other futures.']);
        cues.push([rel(ct, ot), ct >= ot
          ? 'Fires keep burning longer: they take about ' + Math.round(ct) + ' days to halve, against ' + Math.round(ot) + ' in the other futures.'
          : 'Fires die down faster: they halve in about ' + Math.round(ct) + ' days, against ' + Math.round(ot) + ' in the other futures.']);
        cues.push([rel(ck, ock) * 0.8, ck >= ock
          ? 'More of the smoke comes from Kalimantan: ' + Math.round(ck * 100) + '%, against ' + Math.round(ock * 100) + '% in the other futures.'
          : 'More of the smoke comes from Sumatra: ' + Math.round(cs * 100) + '%, against ' + Math.round(ocs * 100) + '% in the other futures.']);
        const ckk = mean(s => sim.pK[sim.idx[s]], sel), okk = mean(s => sim.pK[sim.idx[s]], oth);
        cues.push([rel(ckk, okk) * 1.1, ckk >= okk
          ? 'Fires give off more smoke for their size: about ' + Math.round((ckk / okk - 1) * 100) + '% more per unit of fire power than in the other futures.'
          : 'Fires give off less smoke for their size: about ' + Math.round((1 - ckk / okk) * 100) + '% less per unit of fire power than in the other futures.']);
        const crh = mean(s => sim.pRho[sim.idx[s]], sel), orh = mean(s => sim.pRho[sim.idx[s]], oth);
        cues.push([rel(crh, orh) * 0.9, crh >= orh
          ? 'More of today\'s haze is old smoke that lingers over the region whatever the wind does.'
          : 'Less old smoke lingering over the region, so the air clears quickly once the winds shift.']);
        cues.sort((x, y) => y[0] - x[0]);
        const real = cues.filter(x => x[0] >= 0.08);
        o.watch = real.length >= 2 ? real.slice(0, 2).map(x => x[1]).join(' ')
          : 'No single cause stands out. Mostly ordinary day-to-day swings seen in past seasons: a short wind shift, a shower over Singapore, a lull in burning. ' + (real.length ? real[0][1] : '');
        o.watch = o.watch.trim();
        o.shareSumatra = Math.round(cs * 100); o.shareKalimantan = Math.round(ck * 100);
        return o;
      });
      // split markers: first arrival where class median leaves the overall median by 25+
      classes.forEach(o => { if (!o.median.length) return; for (let f = 0; f < F; f++) if (Math.abs(o.median[f] - pct.p50[f]) >= 25) { o.split_h = out.arrivals_h[f]; break; } });
      // member weights by class (for winds and threads on the map)
      const mw = []; for (let m = 0; m < model.M; m++) mw.push([0, 0, 0, 0]);
      for (let s = 0; s < N; s++) mw[sim.memb[sim.idx[s]]][cls[s]]++;
      const sampleClass = sampleIdx.map(s => cls[s]);
      // yes/no questions for the odds board
      const markets = (() => {
        let p300 = 0, p200 = 0, p300all = 0, b100all = 0;
        for (let s2 = 0; s2 < N; s2++) {
          let mx6 = 0, mx = 0, mn = 1e9;
          for (let f = 0; f < F; f++) { const v = regionPsi(sim, s2, f, rr); if (v > mx) mx = v; if (f < f6 && v > mx6) mx6 = v; }
          for (let d = 0; d < days.length; d++) { const v = dayVals[d][s2]; if (v < mn) mn = v; }
          if (mx6 > 300.5) p300++; if (mx6 > 200.5) p200++;
          if (mx > 300.5) p300all++; if (mn <= 100.5) b100all++;
        }
        const r1 = (x) => Math.round(x / N * 1000) / 10;
        const m = { hazardous6: r1(p300), above200_6: r1(p200), hazardous15: r1(p300all), below100_15: r1(b100all), weekends: [],
          resolution: '24-hour PSI checked at every simulated time step (6-hourly, 12-hourly further out)', validated: false };
        for (let d = 0; d < days.length - 1; d++) {
          if (days[d].dow !== 'Sat') continue;
          let ok = 0, bad = 0; for (let s2 = 0; s2 < N; s2++) { const a1 = dayVals[d][s2], a2 = dayVals[d + 1][s2]; if (a1 <= 100.5 && a2 <= 100.5) ok++; if (a1 > 200.5 || a2 > 200.5) bad++; }
          m.weekends.push({ label: (days[d].short.split(' ')[1] === days[d + 1].short.split(' ')[1] ? days[d].short.split(' ')[0] : days[d].short) + '–' + days[d + 1].short, clear: r1(ok), bad: r1(bad), extended: d >= nC });
        }
        return m;
      })();
      out.regions[reg] = { now: now, mode, factor: model.regRatio[rr], clear: Math.round(clearCount / N * 1000) / 10, clearDayLabel: days[nC - 1].label, clearBy, days: dayStats, pct, classes, memberClass: mw, sampleClass, markets };
    }
    const shares = [0, 0, 0]; for (let s = 0; s < N; s++) for (let k = 0; k < 3; k++) shares[k] += sim.share[sim.idx[s] * 3 + k];
    { const R = model.R, rs = new Float64Array(R); for (let s = 0; s < N; s++) { const j = sim.idx[s]; for (let q = 0; q < R; q++) rs[q] += sim.rshare[j * R + q]; }
      out.sourceRegions = model.inputs.regions.map((g, q) => ({ id: g.id, name: g.name, island: g.island, share: Math.round(rs[q] / N * 1000) / 10 })).sort((x, y) => y.share - x.share); }
    out.sourceShare = { Sumatra: Math.round(shares[0] / N * 100), Kalimantan: Math.round(shares[1] / N * 100), Other: Math.round(shares[2] / N * 100) };
    return out;
  }


  /* ---------- season outlook (experimental) ----------
     When does the haze season end? Taken as: rain has set in over both main source areas
     (Kalimantan and Sumatra fire regions), plus a few days for fires and haze to die down.
     Rain onset comes from the weather forecasts where they reach it; beyond that, from a
     climatological onset date shifted later by El Niño and a positive Indian Ocean Dipole. */
  const SEASON_PRIORS = { Kalimantan: [9, 31], Sumatra: [9, 28] };   // [month index, day]: normal onset, rough, from BMKG season normals
  function season(model, opts) {
    opts = opts || {};
    const inp = model.inputs; const M = model.M, R = model.R;
    const rain = decode(inp.rain.data); const tH = inp.rain.t_h; const T = tH.length;
    const clim = inp.climate || {};
    const mo0 = sgt(model.t0).getUTCMonth();
    if (mo0 < 7) return null;          // the fire season outlook only applies from August to December
    // climate indices older than about 4 months are not used
    const fresh = (period) => { const mm = /([A-Z][a-z]{2}|[A-Z]{3})\s+(\d{4})/.exec(String(period || '')); if (!mm) return false;
      const months = { Jan: 0, Feb: 1, Mar: 2, Apr: 3, May: 4, Jun: 5, Jul: 6, Aug: 7, Sep: 8, Oct: 9, Nov: 10, Dec: 11, JFM: 1, FMA: 2, MAM: 3, AMJ: 4, MJJ: 5, JJA: 6, JAS: 7, ASO: 8, SON: 9, OND: 10, NDJ: 11, DJF: 0 };
      const k = months[mm[1]]; if (k === undefined) return false; const age = (sgt(model.t0).getUTCFullYear() - +mm[2]) * 12 + mo0 - k; return age >= 0 && age <= 4; };
    const roniOk = typeof clim.roni === 'number' && fresh(clim.roni_period), dmiOk = typeof clim.dmi === 'number' && fresh(clim.dmi_period);
    const roni = roniOk ? clim.roni : 0, dmi = dmiOk ? clim.dmi : 0;
    const shiftDays = 6 * Math.min(2.5, Math.max(0, roni)) + 4 * Math.min(2, Math.max(0, dmi / 0.4));
    const year = sgt(model.t0).getUTCFullYear();
    const groups = [['Kalimantan', 1, 'ckal'], ['Sumatra', 0, 'ssum']].map(([name, isl, fallback]) => {
      let w = inp.regions.map((r, i) => (model.isl[i] === isl ? (r.frp0 || 0) : 0));
      if (!w.some(x => x > 0)) w = inp.regions.map(r => (r.id === fallback ? 1 : 0));
      const tot = w.reduce((a, b) => a + b, 0) || 1;
      return { name, w: w.map(x => x / tot) };
    });
    const lastH = tH[T - 1], horizonH = lastH - 120;
    const dry = inp.dryness || {}; const di = ['Kalimantan', 'Sumatra'].map(k => (dry[k] && typeof dry[k].index === 'number') ? dry[k].index : 0.5);
    const lagMean = 4 + 6 * (di[0] + di[1]) / 2;   // drier peat keeps smouldering longer after the rains start
    const onset = groups.map(g => {
      const out = new Float64Array(M).fill(NaN);
      for (let m = 0; m < M; m++) {
        const series = new Float64Array(T);
        for (let t = 0; t < T; t++) { let v = 0; for (let q = 0; q < R; q++) if (g.w[q]) v += g.w[q] * rain[(m * T + t) * R + q]; series[t] = v; }
        for (let t = 0; t < T; t++) {
          if (tH[t] < 0 || tH[t] > horizonH) continue;
          let sum = 0; for (let u = t + 1; u < T && tH[u] <= tH[t] + 120; u++) sum += series[u];
          if (sum >= 25) { out[m] = tH[t] + 48; break; }
        }
      }
      return out;
    });
    const r = rng(opts.seed || 7);
    const N = 20000; const ends = new Float64Array(N); const ons = groups.map(() => new Float64Array(N));
    let seen = 0;
    for (let s = 0; s < N; s++) {
      const m = Math.floor(r() * M); let e = 0; let allSeen = true;
      groups.forEach((g, k) => {
        let o = onset[k][m];
        if (isNaN(o)) {
          allSeen = false;
          const pr = SEASON_PRIORS[g.name];
          let tries = 0;
          do {
            const date = Date.UTC(year, pr[0], pr[1]) + (shiftDays + gauss(r) * 9) * 864e5;
            o = (date - model.t0.getTime()) / 3600e3;
          } while (o < horizonH && ++tries < 30);
          if (o < horizonH) o = horizonH + (-Math.log(1 - r()) * 5 * 24);
        }
        ons[k][s] = o; e = Math.max(e, o);
      });
      if (allSeen) seen++;
      ends[s] = e + Math.max(1, lagMean + gauss(r) * 3) * 24;
    }
    const sorted = Float64Array.from(ends).sort();
    const toDate = (h) => new Date(model.t0.getTime() + h * 3600e3);
    const label = (d) => { const x = sgt(d); return x.getUTCDate() + ' ' + ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][x.getUTCMonth()]; };
    const dates = [];
    const s0 = sgt(model.t0);
    for (let mo = 0; mo < 4; mo++) for (const day of [1, 15]) {
      const d = new Date(Date.UTC(s0.getUTCFullYear(), s0.getUTCMonth() + mo, day) - 8 * 3600e3);
      const h = (d - model.t0) / 3600e3; if (h <= 24 || d.getTime() > Date.UTC(s0.getUTCFullYear() + 1, 0, 1)) continue;
      let c = 0; for (let k = 0; k < N; k++) if (ends[k] <= h) c++;
      dates.push({ iso: d.toISOString(), label: label(d), p: Math.round(c / N * 100) });
    }
    // weekly outlook from the end of the 15-day forecast to the end of the year: chance the season is still going mid-week
    const weeks = [];
    {
      const lastDay = model.days && model.days.length ? sgt(new Date(model.days[model.days.length - 1].iso)) : sgt(new Date(model.t0.getTime() + 15 * 864e5));
      let ws = Date.UTC(lastDay.getUTCFullYear(), lastDay.getUTCMonth(), lastDay.getUTCDate() + 1) - 8 * 3600e3;
      const yearEnd = Date.UTC(s0.getUTCFullYear(), 11, 31) - 8 * 3600e3;
      while (ws <= yearEnd) {
        const we = Math.min(ws + 6 * 864e5, yearEnd);
        const hm = ((ws + we) / 2 + 12 * 3600e3 - model.t0.getTime()) / 3600e3;
        let c = 0; for (let k = 0; k < N; k++) if (ends[k] > hm) c++;
        const a = sgt(new Date(ws)), b = sgt(new Date(we));
        weeks.push({ start: new Date(ws).toISOString(), label: (a.getUTCMonth() === b.getUTCMonth() ? a.getUTCDate() : label(new Date(ws))) + '–' + label(new Date(we)), on: Math.round(c / N * 100) });
        ws = we + 864e5;
      }
    }
    const medOn = ons.map(a => label(toDate(quantile(Float64Array.from(a).sort(), 0.5))));
    return {
      dates, weeks, median: label(toDate(quantile(sorted, 0.5))), p10: label(toDate(quantile(sorted, 0.1))), p90: label(toDate(quantile(sorted, 0.9))),
      onset: { Kalimantan: medOn[0], Sumatra: medOn[1] }, seenInForecast: Math.round(seen / N * 100),
      climate: { roni: clim.roni, roniPeriod: clim.roni_period, roniUsed: roniOk, dmi: clim.dmi, dmiPeriod: clim.dmi_period, dmiUsed: dmiOk, shiftDays: Math.round(shiftDays) },
      experimental: true,
      lagDays: Math.round(lagMean), dryness: dry,
      horizonDays: Math.round(horizonH / 24)
    };
  }

  /* ---------- why the odds moved ---------- */
  function why(prev, cur, prevSummary, curSummary, region) {
    region = region || 'national';
    if (!prev || !prev.regions || !prev.regions[region]) return { label: '', deltas: [], drivers: [] };
    const P = prev.regions[region], Q = cur.regions[region];
    const deltas = Q.classes.map((c, i) => ({ id: c.id, name: c.name, color: c.color, d: Math.round((c.odds - (P.mode === Q.mode && P.classes[i] ? P.classes[i].odds : 0))) }));
    const drivers = [];
    if (P.now != null && Q.now != null && Math.abs(Q.now - P.now) >= 8) drivers.push([Math.abs(Q.now - P.now) / 8, 'Measured PSI ' + (Q.now > P.now ? 'rose' : 'fell') + ' ' + Math.abs(Math.round(Q.now - P.now)) + ' since the last run.']);
    if (prevSummary && curSummary) {
      for (const isl of ['Sumatra', 'Kalimantan']) {
        const a = (prevSummary.frp24_mw || {})[isl], b = (curSummary.frp24_mw || {})[isl];
        if (a > 50 && b >= 0) { const ch = (b - a) / a; if (Math.abs(ch) >= 0.15) drivers.push([Math.abs(ch) * 4, isl + ' fire activity ' + (ch > 0 ? 'up ' : 'down ') + Math.round(Math.abs(ch) * 100) + '% in the latest satellite passes.']); }
        const ra = (prevSummary.rain_mm_0_120h || {})[isl], rb = (curSummary.rain_mm_0_120h || {})[isl];
        if (ra != null && rb != null && Math.abs(rb - ra) >= 3) drivers.push([Math.abs(rb - ra) / 3, 'Weather forecasts now show ' + (rb > ra ? 'more' : 'less') + ' rain over the ' + isl + ' fires: ' + Math.round(rb) + ' mm over 5 days, previously ' + Math.round(ra) + ' mm.']);
        const sa = (prevSummary.transport_share_0_72h || {})[isl], sb = (curSummary.transport_share_0_72h || {})[isl];
        if (sa != null && sb != null && Math.abs(sb - sa) >= 0.1) drivers.push([Math.abs(sb - sa) * 10, (sb > sa ? 'More' : 'Less') + ' of the air reaching Singapore now passes over ' + isl + ' fires.']);
      }
    }
    drivers.sort((x, y) => y[0] - x[0]);
    return { deltas, drivers: drivers.slice(0, 3).map(x => x[1]) };
  }

  function run(inputs, opts) {
    opts = opts || {};
    const model = prepare(inputs, opts);
    const sim = simulate(model, opts);
    const res = summarize(sim, opts);
    try { res.season = season(model, opts); } catch (e) { res.season = null; }
    const reasons = model.degraded.slice();
    if (sim.ess < 30) reasons.push('very few simulated futures match NEA\'s readings (effective sample ' + Math.round(sim.ess) + ')');
    res.degraded = reasons.length ? { reasons } : null;
    res.obsH = model.obsH;
    const L = opts.learn || inputs.learn || null;
    res.learn = L ? { date: L.date || null, bias: L.bias || null, spread: L.spread || null } : null;
    if (opts.onProgress) opts.onProgress(1);
    return res;
  }

  const CALIB = { lo: CALIB_LO, hi: CALIB_HI, version: 'replay-2015-2019-2023-v2' };
  return { run, prepare, simulate, summarize, season, why, psiOf, pmOfPsi, bandOf, BANDS, NEA_REGIONS, decode, CALIB, widenPsi };
});

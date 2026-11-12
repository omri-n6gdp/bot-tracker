"use strict";
/* Implied-volatility surface on a canvas, no libraries.
   x = strike / spot, y = time to expiry (square-root scale), z = implied vol.
   Drag to rotate, double-click to reset, hover for a value, click to pick an expiry. */
(function () {
  const STOPS = [[0, [38, 56, 120]], [0.3, [44, 122, 160]], [0.55, [61, 200, 151]], [0.8, [255, 176, 32]], [1, [255, 107, 107]]];
  function colour(t, shade = 1, alpha = 1) {
    t = Math.max(0, Math.min(1, t));
    let i = 0;
    while (i < STOPS.length - 2 && t > STOPS[i + 1][0]) i++;
    const [t0, c0] = STOPS[i], [t1, c1] = STOPS[i + 1], f = (t - t0) / (t1 - t0 || 1);
    const c = c0.map((v, k) => Math.round(Math.min(255, (v + (c1[k] - v) * f) * shade)));
    return `rgba(${c[0]},${c[1]},${c[2]},${alpha})`;
  }
  const pct = (v) => `${Math.round(v * 100)}%`;
  const dayLabel = (d) => (d < 45 ? `${d}d` : d < 330 ? `${Math.round(d / 30.4)}m` : `${(d / 365).toFixed(d < 500 ? 0 : 1)}y`);

  /* drop sub-10-day expiries (their wings are noise) and smooth lightly: quotes are noisy, the shape isn't */
  function prep(data) {
    let E = data.expiries, Z = data.iv;
    const keep = E.map((e, i) => i).filter((i) => E[i].days >= 10);
    if (keep.length >= 4) { E = keep.map((i) => E[i]); Z = keep.map((i) => Z[i]); }
    const sm = (row) => row.map((v, j) => (j === 0 || j === row.length - 1 ? v : (row[j - 1] + 2 * v + row[j + 1]) / 4));
    let S = Z.map(sm);
    S = S.map((row, i) => row.map((v, j) => (i === 0 || i === S.length - 1 ? v : (S[i - 1][j] + 2 * v + S[i + 1][j]) / 4)));
    return { M: data.moneyness, E, Z: S, spot: data.spot };
  }

  function render(host, data, opts = {}) {
    host.innerHTML = "";
    const canvas = document.createElement("canvas");
    const tip = Object.assign(document.createElement("div"), { className: "surface-tip", hidden: true });
    const bar = Object.assign(document.createElement("div"), { className: "surface-bar" });
    const hint = Object.assign(document.createElement("div"), { className: "surface-hint", textContent: "Drag to rotate" });
    host.append(canvas, tip, bar, hint);
    canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", "Implied volatility by strike and expiry. Drag to rotate.");

    const { M, E, Z } = prep(data);
    const sorted = Z.flat().sort((a, b) => a - b), q = (f) => sorted[Math.min(sorted.length - 1, Math.max(0, Math.round(f * (sorted.length - 1))))];
    const zmin = q(0.03), zmax = Math.max(q(0.97), zmin + 0.02), zr = zmax - zmin;        // colour range
    const hspan = Math.max(zr, 0.6 * q(0.5)), hlo = Math.max(0, (zmin + zmax) / 2 - hspan / 2); // height range (wider, so noise isn't magnified)
    const sq = E.map((e) => Math.sqrt(e.days)), sqMin = Math.min(...sq), sqMax = Math.max(...sq);
    const XW = 1.35, ZH = 1.05;
    const P = Z.map((row, i) => row.map((v, j) => ({
      x: ((M[j] - M[0]) / (M.at(-1) - M[0]) * 2 - 1) * XW,
      y: (sq[i] - sqMin) / (sqMax - sqMin || 1) * 2 - 1,
      z: Math.max(0, Math.min(ZH, (v - hlo) / hspan * ZH)), iv: v, m: M[j], d: E[i].days, i,
    })));
    bar.innerHTML = `<span>${pct(zmin)}</span><i></i><span>${pct(zmax)} vol</span>`;
    bar.querySelector("i").style.background = `linear-gradient(90deg, ${[0, .25, .5, .75, 1].map((t) => colour(t)).join(",")})`;

    const HOME = { yaw: 0.62, pitch: 0.55 };
    let yaw = HOME.yaw, pitch = HOME.pitch, W = 0, H = 0, proj = [], picked = opts.highlight ?? null, touched = false, raf = 0, t0 = performance.now();
    const view = (p) => {
      const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
      const x1 = p.x * cy - p.y * sy, y1 = p.x * sy + p.y * cy;
      return { sx: x1, sy: y1 * sp - (p.z - 0.5) * cp, depth: -(y1 * cp + p.z * sp) };
    };
    function draw() {
      const r = host.getBoundingClientRect();
      W = Math.max(220, r.width); H = Math.max(240, r.height - 24);
      const dpr = window.devicePixelRatio || 1;
      if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) { canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr); canvas.style.width = `${W}px`; canvas.style.height = `${H}px`; }
      const ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      const small = W < 480;
      const corners = [];
      for (const x of [-XW, XW]) for (const y of [-1, 1]) for (const z of [0, ZH]) corners.push(view({ x, y, z }));
      const minX = Math.min(...corners.map((c) => c.sx)), maxX = Math.max(...corners.map((c) => c.sx));
      const minY = Math.min(...corners.map((c) => c.sy)), maxY = Math.max(...corners.map((c) => c.sy));
      const padX = small ? 34 : 56, padY = small ? 26 : 40;
      const scale = Math.min((W - padX * 2) / (maxX - minX), (H - padY * 2) / (maxY - minY));
      const ox = W / 2 - (minX + maxX) / 2 * scale, oy = H / 2 - (minY + maxY) / 2 * scale;
      const to = (p) => { const v = view(p); return [ox + v.sx * scale, oy + v.sy * scale, v.depth]; };
      const line = (a, b, c, w = 1) => { const [x0, y0] = to(a), [x1, y1] = to(b); ctx.strokeStyle = c; ctx.lineWidth = w; ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke(); };
      const poly = (pts) => { ctx.beginPath(); pts.forEach(([x, y], k) => (k ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.closePath(); };
      const faint = "rgba(237,235,230,.09)";
      // floor: a heat map of the same surface, like its shadow
      for (let i = 0; i < E.length - 1; i++) for (let j = 0; j < M.length - 1; j++) {
        const c = [P[i][j], P[i][j + 1], P[i + 1][j + 1], P[i + 1][j]];
        poly(c.map((p) => to({ x: p.x, y: p.y, z: 0 })));
        ctx.fillStyle = colour((c.reduce((a, p) => a + p.iv, 0) / 4 - zmin) / zr, 0.9, 0.2); ctx.fill();
      }
      const back = [[-XW, -1], [XW, -1], [XW, 1], [-XW, 1]].map(([x, y]) => ({ x, y, d: view({ x, y, z: 0 }).depth })).sort((a, b) => b.d - a.d)[0];
      for (const t of [0, 0.5, 1]) { const z = t * ZH; line({ x: back.x, y: -1, z }, { x: back.x, y: 1, z }, faint); line({ x: -XW, y: back.y, z }, { x: XW, y: back.y, z }, faint); }
      line({ x: back.x, y: back.y, z: 0 }, { x: back.x, y: back.y, z: ZH }, "rgba(237,235,230,.22)");
      // surface quads, far to near, lit from the viewer's upper left
      const quads = [];
      for (let i = 0; i < E.length - 1; i++) for (let j = 0; j < M.length - 1; j++) {
        const c = [P[i][j], P[i][j + 1], P[i + 1][j + 1], P[i + 1][j]];
        const ux = c[1].x - c[0].x, uz = c[1].z - c[0].z, vy = c[3].y - c[0].y, vz = c[3].z - c[0].z;
        const n = [-uz * vy, -ux * vz, ux * vy], len = Math.hypot(...n) || 1;
        const light = Math.max(0, (n[0] * -0.35 + n[1] * -0.45 + n[2] * 0.82) / len);
        const pts2 = c.map(to);
        quads.push({ pts: pts2, d: pts2.reduce((a, p) => a + p[2], 0) / 4, iv: c.reduce((a, p) => a + p.iv, 0) / 4, shade: 0.6 + 0.52 * light });
      }
      quads.sort((a, b) => b.d - a.d);
      for (const k of quads) { poly(k.pts); ctx.fillStyle = colour((k.iv - zmin) / zr, k.shade); ctx.fill(); ctx.strokeStyle = "rgba(10,11,13,.4)"; ctx.lineWidth = 0.6; ctx.stroke(); }
      // the picked expiry, traced across the surface
      if (picked != null && P[picked]) {
        ctx.strokeStyle = "#EDEBE6"; ctx.lineWidth = 2.5; ctx.lineJoin = "round"; ctx.shadowColor = "rgba(237,235,230,.7)"; ctx.shadowBlur = 8;
        ctx.beginPath(); P[picked].forEach((p, k) => { const [x, y] = to(p); k ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }); ctx.stroke(); ctx.shadowBlur = 0;
      }
      // labels on the edges nearest the viewer
      ctx.font = `${small ? 10 : 11.5}px "Inter Tight", sans-serif`; ctx.fillStyle = "rgba(237,235,230,.62)";
      const text = (p, s, align, dy = 4) => { const [x, y] = to(p); ctx.textAlign = align; ctx.fillText(s, x, y + dy); };
      const nearY = view({ x: 0, y: -1, z: 0 }).depth < view({ x: 0, y: 1, z: 0 }).depth ? -1 : 1;
      const nearX = view({ x: -XW, y: 0, z: 0 }).depth < view({ x: XW, y: 0, z: 0 }).depth ? -1 : 1;
      const side = (p) => (to(p)[0] < W / 2 ? "right" : "left");
      M.forEach((m, j) => { if (j % (small ? 3 : 2) === 0) text({ x: P[0][j].x, y: nearY * 1.14, z: 0 }, `${Math.round(m * 100)}%`, "center", 5); });
      E.forEach((e, i) => { if (E.length < 7 || i % 2 === 0 || i === E.length - 1) { const p = { x: nearX * (XW + 0.12), y: P[i][0].y, z: 0 }; text(p, dayLabel(e.days), side(p)); } });
      for (const t of [0, 0.5, 1]) { const p = { x: back.x * 1.04, y: back.y * 1.04, z: t * ZH }; text(p, pct(hlo + t * hspan), side(p)); }
      if (!small) { ctx.fillStyle = "rgba(237,235,230,.38)"; text({ x: 0, y: nearY * 1.5, z: 0 }, "strike, % of price now", "center", 6); }
      proj = P.flat().map((p) => { const [x, y] = to(p); return { x, y, p }; });
    }
    // idle: a slow sway so the shape reads as 3D, until someone takes hold of it
    const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
    function sway(now) {
      if (touched || still || !host.isConnected) return;
      yaw = HOME.yaw + Math.sin((now - t0) / 4200) * 0.22;
      if (host.offsetParent) draw();
      raf = requestAnimationFrame(sway);
    }
    let drag = null;
    canvas.addEventListener("pointerdown", (e) => { touched = true; hint.hidden = true; drag = { x: e.clientX, y: e.clientY, yaw, pitch, moved: false }; canvas.setPointerCapture(e.pointerId); });
    canvas.addEventListener("pointermove", (e) => {
      const r = canvas.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
      if (drag) {
        if (Math.hypot(e.clientX - drag.x, e.clientY - drag.y) > 3) drag.moved = true;
        yaw = drag.yaw + (e.clientX - drag.x) * 0.008;
        pitch = Math.max(0.12, Math.min(1.35, drag.pitch + (e.clientY - drag.y) * 0.006));
        tip.hidden = true; draw(); return;
      }
      let best = null, bd = 18;
      for (const k of proj) { const d = Math.hypot(k.x - mx, k.y - my); if (d < bd) { bd = d; best = k; } }
      canvas.style.cursor = best ? "pointer" : "";
      if (!best) { tip.hidden = true; return; }
      tip.hidden = false;
      tip.innerHTML = `<b>${pct(best.p.iv)}</b> implied vol<br>strike ${Math.round(best.p.m * 100)}% of price, ${dayLabel(best.p.d)} out`;
      tip.style.left = `${Math.min(best.x + 14, W - 200)}px`; tip.style.top = `${Math.max(best.y - 50, 0)}px`;
      tip._best = best;
    });
    canvas.addEventListener("pointerup", () => {
      if (drag && !drag.moved && !tip.hidden && tip._best) { api.highlight(tip._best.p.d); opts.onPick?.(tip._best.p.d); }
      drag = null;
    });
    canvas.addEventListener("pointercancel", () => { drag = null; });
    canvas.addEventListener("pointerleave", () => { if (!drag) tip.hidden = true; });
    canvas.addEventListener("dblclick", () => { yaw = HOME.yaw; pitch = HOME.pitch; draw(); });
    const ro = new ResizeObserver(() => draw());
    ro.observe(host);
    const api = {
      destroy() { ro.disconnect(); cancelAnimationFrame(raf); touched = true; host.innerHTML = ""; },
      highlight(days) { const i = E.findIndex((e) => e.days === days); picked = i >= 0 ? i : null; draw(); },
      expiries: E, moneyness: M,
    };
    draw();
    raf = requestAnimationFrame(sway);
    return api;
  }
  window.VolSurface = { render, prep, dayLabel, colour };
})();

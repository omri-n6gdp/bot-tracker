"use strict";

/* ================= helpers ================= */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const bad = (x) => x == null || Number.isNaN(x);
const F = (o) => { const f = new Intl.NumberFormat("en-GB", o); return (x) => (bad(x) ? "—" : f.format(x).replace("-", "−")); };
const gbp = F({ style: "currency", currency: "GBP" });
const gbpS = F({ style: "currency", currency: "GBP", signDisplay: "exceptZero" });
const gbp0 = F({ style: "currency", currency: "GBP", maximumFractionDigits: 0 });
const pct = F({ style: "percent", minimumFractionDigits: 1, maximumFractionDigits: 1, signDisplay: "exceptZero" });
const pct2 = F({ style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "exceptZero" });
const pp = F({ style: "percent", minimumFractionDigits: 1, maximumFractionDigits: 1 });
const p0 = F({ style: "percent", maximumFractionDigits: 0 });
const p0s = F({ style: "percent", maximumFractionDigits: 0, signDisplay: "exceptZero" });
const n2 = F({ minimumFractionDigits: 2, maximumFractionDigits: 2 });
const n2s = F({ minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "exceptZero" });
const compact = F({ notation: "compact", maximumFractionDigits: 1 });
const pts = (x) => (bad(x) ? "—" : `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(1)} pts`);
const cls = (x) => (bad(x) || x === 0 ? "" : x > 0 ? "up" : "down");
const arrow = (x) => (bad(x) ? "" : x >= 0 ? "▲" : "▼");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const G = (html) => `<span class="gbp">${html}</span>`;   // a £ amount: blurred in privacy mode
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const parseDay = (s) => new Date(s.length === 10 ? s + "T12:00:00" : s);
const tms = (s) => parseDay(s).getTime();
const dShort = (s) => { const d = parseDay(s); return `${d.getDate()} ${MON[d.getMonth()]}`; };
const dLong = (s) => { const d = parseDay(s); return `${DOW[d.getDay()]} ${d.getDate()} ${MON[d.getMonth()]} ${d.getFullYear()}`; };
const timeF = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/London" });
const clockF = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "Europe/London" });
const dateF = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "Europe/London" });
const sym = (t) => String(t || "").replace(/_.*$/, "").replace(/l$/, "");
const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
function money(v, ccy) {
  if (bad(v)) return "—";
  if (ccy === "GBp" || ccy === "GBX") return `${n2(v)}p`;
  try { return new Intl.NumberFormat("en-GB", { style: "currency", currency: ccy || "GBP" }).format(v); } catch { return n2(v); }
}
function moneyC(v, ccy) {
  if (bad(v)) return "—";
  try { return new Intl.NumberFormat("en-GB", { style: "currency", currency: ccy && ccy !== "GBp" ? ccy : "GBP", notation: "compact", maximumFractionDigits: 1 }).format(v); } catch { return compact(v); }
}
const stockHref = (t) => `#stock/${encodeURIComponent(t)}`;
const tk = (h, extra = "") => `<a class="tk ${extra}" data-tk="${esc(h.ticker)}" href="${stockHref(h.ticker)}">${esc(h.symbol)}</a>`;
function spark(vals, w = 70, h = 24, color, fill = false) {
  const v = (vals || []).filter((x) => !bad(x));
  if (v.length < 2) return `<svg width="${w}" height="${h}" aria-hidden="true"></svg>`;
  const lo = Math.min(...v), hi = Math.max(...v), rng = hi - lo || 1;
  const p = v.map((x, i) => `${(i / (v.length - 1) * w).toFixed(1)},${(h - 1.5 - (x - lo) / rng * (h - 3)).toFixed(1)}`).join(" ");
  const col = color || (v.at(-1) >= v[0] ? "var(--up)" : "var(--down)");
  return `<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true" style="display:block">${fill ? `<polygon points="0,${h} ${p} ${w},${h}" fill="${col}" fill-opacity=".13"></polygon>` : ""}<polyline points="${p}" fill="none" stroke="${col}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"></polyline></svg>`;
}

/* ================= state + data ================= */
const state = { view: "overview", range: "ALL", srange: 365, bench: [], sort: { key: "weight", dir: -1 }, mapMode: "day", sector: null, move: null,
  summary: null, analysis: null, holdingsData: null, charts: {}, private: false, counted: false };
const cache = {};
async function get(path) {
  const r = await fetch(path, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`${path} returned ${r.status}`);
  return r.json();
}
function load(key, path) {
  if (!cache[key]) cache[key] = get(path).catch((e) => { delete cache[key]; throw e; });
  return cache[key];
}
const data = {
  history: (r) => load(`h-${r}`, `/api/history?range=${r}`),
  holdings: () => load("holdings", "/api/holdings"),
  analysis: () => load("analysis", "/api/analysis"),
  stats: () => load("stats", "/api/stats"),
  news: () => load("news", "/api/news"),
  corr: () => load("corr", "/api/correlations"),
  rebalances: () => load("rebalances", "/api/rebalances").then((x) => x.rebalances || []),
  game: () => get("/api/game"),          // never cached: guesses change it
  recap: () => load("recap", "/api/recap"),
  mc: () => load("mc", "/api/montecarlo"),
  rr: () => load("rr", "/api/riskreturn"),
  quant: () => load("quant", "/api/quant"),
};
const benchKey = (s) => Object.keys(s?.benchmarks || {})[0];
const shortBench = (n) => ({ "S&P 500": "S&P" }[n] || n || "market");
const held = () => (state.holdingsData?.holdings || []).filter((h) => h.value > 0);

/* ================= charts ================= */
const COL = { fg: "#EDEBE6", muted: "rgba(237,235,230,.6)", faint: "rgba(237,235,230,.42)", hair: "rgba(237,235,230,.07)", amber: "#FFB020", up: "#3DDC97", down: "#FF6B6B", blue: "#5CC8FF", grey: "#9A978F", bg: "#0A0B0D" };
const nearest = (arr, x) => { let lo = 0, hi = arr.length - 1; while (hi - lo > 1) { const m = (lo + hi) >> 1; (arr[m].x < x ? (lo = m) : (hi = m)); } return !arr.length ? -1 : Math.abs(arr[lo].x - x) <= Math.abs(arr[hi].x - x) ? lo : hi; };
function setupCharts() {
  if (!window.Chart) return;
  Object.assign(Chart.defaults.font, { family: "'Inter Tight', system-ui, sans-serif", size: 12 });
  Chart.defaults.color = COL.faint; Chart.defaults.borderColor = COL.hair;
  Object.assign(Chart.defaults.plugins.tooltip, { backgroundColor: "#15171A", borderColor: "rgba(237,235,230,.14)", borderWidth: 1, titleColor: COL.fg, bodyColor: COL.fg, padding: 10, cornerRadius: 9, boxPadding: 5, usePointStyle: true });
  Chart.register(
    { id: "reveal",   // wipes the lines in from the left the first time a chart is shown
      beforeDatasetsDraw(ch) { const r = ch.$reveal; if (r == null || r >= 1) return; const a = ch.chartArea; ch.ctx.save(); ch.ctx.beginPath(); ch.ctx.rect(a.left - 4, a.top - 24, (a.right - a.left) * r + 8, a.bottom - a.top + 48); ch.ctx.clip(); ch.$clip = true; },
      afterDatasetsDraw(ch) { if (ch.$clip) { ch.ctx.restore(); ch.$clip = false; } } },
    { id: "glow",
      beforeDatasetDraw(ch, a) { if (ch.data.datasets[a.index].glow) { ch.ctx.save(); ch.ctx.shadowColor = ch.data.datasets[a.index].borderColor; ch.ctx.shadowBlur = 14; } },
      afterDatasetDraw(ch, a) { if (ch.data.datasets[a.index].glow) ch.ctx.restore(); } },
    { id: "endTags",   // the latest value of each line, tagged at the right edge
      afterDatasetsDraw(ch) {
        if (ch.$reveal != null && ch.$reveal < 1) return;
        const { ctx, chartArea: a } = ch, used = [];
        ch.data.datasets.forEach((ds, i) => {
          if (!ds.tag || !ch.isDatasetVisible(i)) return;
          const el = ch.getDatasetMeta(i).data.at(-1); if (!el) return;
          const label = ds.tag(ds.data.at(-1).y);
          ctx.save(); ctx.font = "700 12px 'Inter Tight', sans-serif";
          let y = clamp(el.y, a.top + 10, a.bottom - 10);
          used.forEach((u) => { if (Math.abs(u - y) < 22) y = u + (y >= u ? 22 : -22); }); used.push(y);
          const w = ctx.measureText(label).width + 14, x = a.right + 6;
          ctx.fillStyle = ds.borderColor; ctx.beginPath(); ctx.roundRect ? ctx.roundRect(x, y - 10, w, 20, 6) : ctx.rect(x, y - 10, w, 20); ctx.fill();
          ctx.fillStyle = COL.bg; ctx.textBaseline = "middle"; ctx.fillText(label, x + 7, y + 0.5); ctx.restore();
        });
      } },
    { id: "gapBands",  // hatched stripes where the tracker took no readings
      beforeDatasetsDraw(ch) {
        const g = ch.options.plugins.gapBands?.gaps; if (!g?.length) return;
        const { ctx, chartArea: a, scales } = ch;
        ctx.save(); ctx.beginPath(); ctx.rect(a.left, a.top, a.right - a.left, a.bottom - a.top); ctx.clip();
        g.forEach(([x0, x1]) => {
          const l = Math.max(scales.x.getPixelForValue(x0), a.left), r = Math.min(scales.x.getPixelForValue(x1), a.right); if (r - l < 2) return;
          ctx.fillStyle = "rgba(237,235,230,.035)"; ctx.fillRect(l, a.top, r - l, a.bottom - a.top);
          ctx.save(); ctx.beginPath(); ctx.rect(l, a.top, r - l, a.bottom - a.top); ctx.clip();
          ctx.strokeStyle = "rgba(237,235,230,.06)"; ctx.lineWidth = 1; ctx.beginPath();
          const H = a.bottom - a.top;
          for (let x = l - H; x < r; x += 9) { ctx.moveTo(x, a.bottom); ctx.lineTo(x + H, a.top); }
          ctx.stroke(); ctx.restore();
          if (r - l > 84) { ctx.fillStyle = "rgba(237,235,230,.38)"; ctx.font = "600 11px 'Inter Tight', sans-serif"; ctx.textAlign = "center"; ctx.fillText("no readings", (l + r) / 2, a.bottom - 10); }
        });
        ctx.restore();
      } },
    { id: "scrub",     // a crosshair that follows the pointer and reports the point under it
      afterEvent(ch, args) {
        const o = ch.options.plugins.scrub; if (!o?.onMove) return;
        const e = args.event, main = ch.data.datasets[0].data;
        let i = null;
        if (e.type !== "mouseout" && args.inChartArea && main.length) i = nearest(main, ch.scales.x.getValueForPixel(e.x));
        if (i !== ch.$scrub) { ch.$scrub = i; o.onMove(i, i == null ? null : ch.scales.x.getPixelForValue(main[i].x)); args.changed = true; }
      },
      afterDatasetsDraw(ch) {
        const i = ch.$scrub; if (i == null) return;
        const { ctx, chartArea: a, scales } = ch, x0 = ch.data.datasets[0].data[i]?.x; if (x0 == null) return;
        const x = scales.x.getPixelForValue(x0);
        ctx.save(); ctx.strokeStyle = "rgba(237,235,230,.4)"; ctx.lineWidth = 1; ctx.setLineDash([3, 4]); ctx.beginPath(); ctx.moveTo(x, a.top); ctx.lineTo(x, a.bottom); ctx.stroke(); ctx.setLineDash([]);
        ch.data.datasets.forEach((ds, k) => {
          if (ds.type === "scatter" || ds.noScrub || !ch.isDatasetVisible(k) || !ds.data.length) return;
          const p = ds.data[nearest(ds.data, x0)]; if (Math.abs(p.x - x0) > 864e5 * 3) return;
          ctx.fillStyle = COL.bg; ctx.strokeStyle = ds.borderColor; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.arc(x, scales.y.getPixelForValue(p.y), 5, 0, 7); ctx.fill(); ctx.stroke();
        });
        ctx.restore();
      } },
  );
}
function chart(id, cfg) {
  if (!window.Chart) return null;
  state.charts[id]?.destroy();
  const el = document.getElementById(id);
  if (!el) return null;
  return (state.charts[id] = new Chart(el, cfg));
}
function reveal(ch) {
  if (!ch || still) return;
  const t0 = performance.now();
  ch.$reveal = 0;
  (function step(now) {
    if (!ch.canvas) return;
    const t = clamp((now - t0) / 900, 0, 1);
    ch.$reveal = 1 - Math.pow(1 - t, 3);
    ch.draw();
    if (t < 1) requestAnimationFrame(step);
  })(t0);
}
function timeOpts(yFmt, { intraday = false, tags = false, tagPad = 62, tooltip = true } = {}) {
  return {
    responsive: true, maintainAspectRatio: false, animation: false, parsing: false, normalized: true,
    layout: { padding: { right: tags ? tagPad : 0, top: tags ? 6 : 0 } },
    interaction: { mode: "index", axis: "x", intersect: false },
    plugins: { legend: { display: false }, tooltip: { enabled: tooltip, callbacks: {
      title: (it) => (intraday ? timeF.format(new Date(it[0].raw.x)) : dLong(new Date(it[0].raw.x).toISOString())),
      label: (it) => ` ${it.dataset.label}: ${yFmt(it.raw.y)}` } } },
    scales: {
      x: { type: "time", grid: { display: false }, border: { display: false }, time: { displayFormats: { day: "d MMM", week: "d MMM", month: "MMM", hour: "HH:mm", minute: "HH:mm", second: "HH:mm:ss", millisecond: "HH:mm:ss" } },
           ticks: { maxTicksLimit: innerWidth < 720 ? 4 : 8, maxRotation: 0, padding: 8 } },
      y: { position: tags ? "left" : "right", grid: { color: COL.hair, drawTicks: false }, border: { display: false }, ticks: { maxTicksLimit: 6, padding: 10, callback: yFmt } },
    },
  };
}
const areaFill = (hex, top = "3A") => (ctx) => {
  const a = ctx.chart.chartArea; if (!a) return "transparent";
  const g = ctx.chart.ctx.createLinearGradient(0, a.top, 0, a.bottom);
  g.addColorStop(0, hex + top); g.addColorStop(1, hex + "00");
  return g;
};
const barOpts = (extra = {}) => ({ responsive: true, maintainAspectRatio: false, animation: still ? false : { duration: 500, easing: "easeOutCubic" }, plugins: { legend: { display: false } }, ...extra });

/* ================= routing, search, top bar ================= */
const VIEWS = ["overview", "holdings", "quant"];
function route() {
  const [view, arg] = (location.hash.slice(1) || "overview").split("/");
  show(view, arg ? decodeURIComponent(arg) : null);
}
function show(name, arg) {
  if (!document.getElementById(`view-${name}`)) name = "overview";
  const changed = state.view !== name || state.stock !== arg;
  state.view = name; state.stock = name === "stock" ? arg : null;
  $$(".view").forEach((v) => (v.hidden = v.id !== `view-${name}`));
  const tab = name === "stock" ? "holdings" : name;
  $$(".nav a").forEach((a) => (a.dataset.view === tab ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  hidePeek();
  if (typeof closeFocus === "function") closeFocus(true);
  if (changed) scrollTo(0, 0);
  refresh();
}
addEventListener("hashchange", route);
addEventListener("keydown", (e) => {
  const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName);
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "/") { e.preventDefault(); $("#cmd-in").focus(); }
  else if (/^[1-3]$/.test(e.key)) location.hash = VIEWS[+e.key - 1];
  else if (state.view === "stock" && (e.key === "ArrowLeft" || e.key === "ArrowRight")) $(e.key === "ArrowLeft" ? "#s-prev" : "#s-next").click();
});

let suggestions = [], sel = -1;
function renderSuggest() {
  const q = $("#cmd-in").value.trim().toUpperCase(), list = $("#cmd-list");
  const pool = [...(state.holdingsData?.holdings || []).map((h) => ({ code: h.symbol, label: h.name, hint: `<span class="${cls(h.day_pct)}">${pct2(h.day_pct)}</span>`, go: stockHref(h.ticker), ticker: h.ticker })),
    ...VIEWS.map((v) => ({ code: v[0].toUpperCase() + v.slice(1), label: "page", hint: "", go: `#${v}`, page: true }))];
  suggestions = q ? pool.filter((t) => t.code.toUpperCase().startsWith(q) || (!t.page && t.label.toUpperCase().includes(q)) || (t.ticker || "").toUpperCase().startsWith(q)).slice(0, 7) : [];
  sel = suggestions.length ? 0 : -1;
  list.hidden = !suggestions.length;
  $("#cmd-in").setAttribute("aria-expanded", String(!list.hidden));
  list.innerHTML = suggestions.map((s, i) => `<li role="option" id="opt-${i}" aria-selected="${i === sel}" data-i="${i}"><b class="${s.page ? "" : "tk"}">${esc(s.code)}</b><span>${esc(s.label)}</span><em>${s.hint}</em></li>`).join("");
}
function runSearch() {
  const target = suggestions[sel]?.go;
  if (!target) { flash(`Nothing called "${$("#cmd-in").value.trim()}" in the pie.`); return; }
  $("#cmd-in").value = ""; renderSuggest(); $("#cmd-in").blur();
  location.hash = target.slice(1);
}
$("#cmd-in").addEventListener("input", renderSuggest);
$("#cmd-in").addEventListener("keydown", (e) => {
  if ((e.key === "ArrowDown" || e.key === "ArrowUp") && suggestions.length) {
    e.preventDefault(); sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + suggestions.length) % suggestions.length;
    $$("#cmd-list li").forEach((li, i) => li.setAttribute("aria-selected", String(i === sel)));
    $("#cmd-in").setAttribute("aria-activedescendant", `opt-${sel}`);
  }
  if (e.key === "Escape") { $("#cmd-in").value = ""; renderSuggest(); $("#cmd-in").blur(); }
});
$("#cmd-list").addEventListener("mousedown", (e) => { const li = e.target.closest("li"); if (li) { e.preventDefault(); sel = +li.dataset.i; runSearch(); } });
$("#cmd").addEventListener("submit", (e) => { e.preventDefault(); runSearch(); });
$("#cmd-in").addEventListener("blur", () => setTimeout(() => { $("#cmd-list").hidden = true; }, 120));
let flashT;
function flash(msg) { const n = $("#notice"); n.hidden = false; n.textContent = msg; clearTimeout(flashT); flashT = setTimeout(setNotice, 3200); }

function setPrivate(on) {
  state.private = on;
  document.body.classList.toggle("private", on);
  const b = $("#privacy"); b.setAttribute("aria-pressed", String(on)); b.title = on ? "Show £ amounts" : "Hide £ amounts";
  try { localStorage.setItem("private", on ? "1" : ""); } catch { /* storage unavailable: the choice just won't persist */ }
}
$("#privacy").addEventListener("click", () => { setPrivate(!state.private); refresh(); });
try { if (localStorage.getItem("private")) setPrivate(true); } catch { /* ignore */ }
function tick() { const now = new Date(); $("#today-date").textContent = dateF.format(now); $("#clock").textContent = clockF.format(now); }
tick(); setInterval(tick, 1000);

/* ================= refresh ================= */
async function refresh() {
  try {
    await loadSummary();
    const v = state.view, all = (ps) => Promise.all(ps.map((p) => p.catch(console.error)));
    if (v === "overview") await all([loadHero(), loadChart(), loadMap(), loadNews(), loadConsensus(), loadGame(), loadRecap(), loadMC(), loadWaterfall()]);
    if (v === "holdings") await all([loadHoldings(), loadRR()]);
    if (v === "quant") await all([loadQuant(), loadVol(), loadDeep()]);
    if (v === "stock" && state.stock) await loadStock(state.stock);
    if (typeof focusRefreshed === "function") focusRefreshed();
  } catch (e) {
    console.error(e);
    setStatus(null, "Can't reach the tracker right now. The page will try again in a minute.");
  }
}
function setNotice() {
  const s = state.summary, n = $("#notice");
  const msg = s?.ready && s.stale ? `Out of date: the last update was ${timeF.format(new Date(s.as_of))} on ${dShort(s.as_of.slice(0, 10))}.` : s?.source === "fallback" ? "Using backup prices while Trading 212's pie data is unavailable." : "";
  n.hidden = !msg; n.textContent = msg;
}
function setStatus(s, error) {
  let c = "off", label = "Waiting for the first snapshot";
  if (error) { c = "bad"; label = "Can't reach the tracker"; }
  else if (s?.ready) { c = s.stale || s.source === "fallback" ? "warn" : ""; label = `${s.stale ? "Out of date" : "Live"}, updated ${timeF.format(new Date(s.as_of))}`; }
  $("#live-dot").className = `dot ${c}`; $(".brand").title = label; $("#foot-status").textContent = label;
  if (error) { const n = $("#notice"); n.hidden = false; n.textContent = error; }
}
async function loadSummary() {
  const s = await get("/api/summary");
  state.summary = s;
  const name = s.pie_name || "Bot tracker";
  $("#pie-name").textContent = name; $("#foot-name").textContent = name; document.title = name;
  setStatus(s); setNotice();
  const d = s.day_change_pct;
  document.documentElement.style.setProperty("--mood", bad(d) || Math.abs(d) < 0.0005 ? "255, 176, 32" : d > 0 ? "61, 220, 151" : "255, 107, 107");
  state.empty = !s.ready || !(s.value > 0);
  document.body.classList.toggle("is-empty", state.empty);
  const w = $("#waiting");
  w.hidden = !state.empty;
  if (state.empty) w.innerHTML = s.ready
    ? `<h1>Waiting for the first money</h1><p><b>${esc(name)}</b> is set up with the bot's targets but is empty. The numbers start the moment money lands.</p>`
    : `<h1>Connecting to Trading 212</h1><p>The first snapshot usually lands within a minute of the tracker starting.</p>`;
  try { [state.holdingsData, state.analysis] = await Promise.all([data.holdings(), data.analysis()]); renderTape(); } catch (e) { console.error(e); }
}
function renderTape() {
  const hs = held(), el = $("#tape");
  if (!hs.length) { el.hidden = true; return; }
  el.hidden = false;
  const run = hs.map((h) => `<a data-tk="${esc(h.ticker)}" href="${stockHref(h.ticker)}"><b class="tk">${esc(h.symbol)}</b><span class="${cls(h.day_pct)}">${pct2(h.day_pct)}</span></a>`).join("");
  el.innerHTML = `<div class="tape-run">${run}${run}</div>`;
}

/* ================= overview ================= */
function heroValue(v, ret) {
  $("#hero-value").innerHTML = state.private ? pct(ret) : (() => { const [a, b] = gbp(v).split("."); return G(`${a}<small>.${b ?? "00"}</small>`); })();
}
function heroNow() {
  const s = state.summary; if (!s?.ready) return;
  $("#hero-label").textContent = state.private ? "Return on money in" : "Value now";
  heroValue(s.value, s.return_on_deposits);
  // today's move from the open (09:30 New York); before the open, from the first reading today
  const o = s.since_open, d = o ? o.pct : s.day_change_pct, g = o ? o.gbp : s.day_change;
  const fromDay = o && new Date(o.from).toLocaleDateString("en-GB", { timeZone: "Europe/London" }), today = new Date().toLocaleDateString("en-GB", { timeZone: "Europe/London" });
  const lab = o ? (o.at_open ? (fromDay === today ? "since today's open" : `since ${new Date(o.from).toLocaleDateString("en-GB", { weekday: "long", timeZone: "Europe/London" })}'s open`) : `since ${timeF.format(new Date(o.from))}`) : "today";
  $("#hero-sub").innerHTML = `<span class="delta ${cls(d)}">${arrow(d)} ${pct2(d)} ${lab}</span>${G(gbpS(g))}<span>updated ${timeF.format(new Date(s.as_of))}</span>`;
}
function renderStats(p) {   // p: a past daily point while scrubbing the chart, or null for now
  const s = state.summary, a = state.analysis || {}, bk = benchKey(s), bs = s.benchmarks?.[bk];
  // return = value against net deposits; the market on the same deposits
  const ret = p ? (p.deposits ? p.value / p.deposits - 1 : p.pie) : s.return_on_deposits;
  const bench = p ? (p.deposits && p.bench_value?.[bk] != null ? p.bench_value[bk] / p.deposits - 1 : p.bench?.[bk]) : s.bench_on_deposits?.[bk]?.return ?? bs?.since_start;
  const profit = p ? (p.deposits != null ? p.value - p.deposits : null) : s.profit;
  const lead = bad(ret) || bad(bench) ? null : ret - bench;
  const items = [
    ...(state.private ? [] : [["Profit", G(gbpS(profit)), cls(profit), p ? "at that date" : `value now minus ${gbp0(s.net_deposits)} paid in`]]),
    ["Return", pct(ret), cls(ret), p ? "to that date" : `value now against ${G(gbp0(s.net_deposits))} paid in`],
    [`Against the ${esc(bs?.name || "market")}`, pts(lead), cls(lead), `${esc(shortBench(bs?.name))} ${pct(bench)} on the same deposits`],
    ...(a.ready ? [[`Sharpe ratio${warn(thinWhy(a))}`, n2(a.ratios.sharpe), "", a.readings ? "early: from today's readings" : `${p0(a.significance.prob_sharpe_positive)} likely to be above zero`],
                   ["Worst fall", pct(a.risk.max_drawdown), "down", a.readings ? "early: from today's readings" : `now ${pct(a.risk.current_drawdown)} from the high`]] : []),
  ];
  $("#stats").innerHTML = items.map(([k, v, c, sub]) => `<div><dt>${k}</dt><dd class="${c}">${v}<small>${sub}</small></dd></div>`).join("");
}
async function loadHero() {
  const s = state.summary; if (!s?.ready) return;
  heroNow(); renderStats(null);
  if (!state.counted && !still && !state.private) {   // count up once, on first load
    state.counted = true;
    const t0 = performance.now(), to = s.value;
    (function step(now) { const t = clamp((now - t0) / 800, 0, 1); heroValue(to * (1 - Math.pow(1 - t, 4)), null); if (t < 1) requestAnimationFrame(step); })(t0);
  }
}
function onScrub(i, px) {
  const S = state.series, lab = $("#scrub-date");
  if (i == null || !S?.P[i]) { lab.hidden = true; heroNow(); renderStats(null); return; }
  const p = S.P[i], host = lab.parentElement;
  lab.hidden = false; lab.textContent = S.intraday ? timeF.format(new Date(p.t)) : dShort(p.t);
  lab.style.left = `${clamp(px, 34, host.clientWidth - 34)}px`;
  state.counted = true;
  if (S.intraday) {
    const r = S.even && p.dep ? p.value / p.dep - 1 : p.u != null ? p.u / S.P[0].u - 1 : S.base ? p.value / S.base - 1 : null;
    const sameDay = new Date(p.t).toLocaleDateString("en-GB", { timeZone: "Europe/London" }) === new Date().toLocaleDateString("en-GB", { timeZone: "Europe/London" });
    $("#hero-label").textContent = `${sameDay ? "Today" : dShort(p.t)} at ${timeF.format(new Date(p.t))}`;
    heroValue(p.value, r);
    $("#hero-sub").innerHTML = `<span class="delta ${cls(r)}">${arrow(r)} ${pct2(r)} ${S.even ? `on ${G(gbp0(p.dep))} paid in` : `since ${S.early ? "the start" : S.baseLabel}`}</span>`;
  } else {
    $("#hero-label").textContent = dLong(p.t);
    heroValue(p.value, p.pie);
    const prev = S.P[i - 1], day = prev ? (1 + p.pie) / (1 + prev.pie) - 1 : null;
    $("#hero-sub").innerHTML = bad(day) ? "" : `<span class="delta ${cls(day)}">${arrow(day)} ${pct2(day)} that day</span>`;
    renderStats(p);
  }
}
// [start, end] of each stretch with no readings: the gap before each held point
function gapSpans(P) {
  const out = [];
  P.forEach((p, i) => { if (p.held && P[i - 1] && P[i + 1]) out.push([new Date(P[i - 1].t).getTime(), new Date(P[i + 1].t).getTime()]); });
  return out;
}
async function loadChart(animate = !state.chartShown) {
  const [h, reb] = await Promise.all([data.history(state.range), data.rebalances()]);
  const P = h.points || [], bc = [COL.blue, COL.grey];
  if (!state.bench.length && h.benchmarks?.length) state.bench = h.benchmarks.map((b, i) => ({ key: b.key, name: b.name, on: i === 0 }));
  const box = $("#toggles");
  const chips = state.bench.filter((b) => !h.intraday || h.benchmarks?.some((x) => x.key === b.key))
    .map((b) => `<button class="chip" data-bench="${esc(b.key)}" aria-pressed="${b.on}"><i style="background:${bc[state.bench.indexOf(b) % 2]}"></i>${esc(b.name)}</button>`).join("");
  const usesFut = h.intraday && (h.benchmarks || []).some((b) => state.bench.find((x) => x.key === b.key && x.on) && b.points?.some((p) => p.f));
  const gaps = h.intraday ? gapSpans(P) : [];
  box.innerHTML = `<span><i style="background:${COL.amber}"></i>The bot</span>` + chips + (usesFut ? `<span title="Dashed: no S&P 500 fund was trading (roughly 01:00-08:00 UK), so the line follows S&P 500 futures, which trade nearly around the clock."><i class="dash" style="border-color:${COL.blue}"></i>S&P futures, funds shut</span>` : "") + (h.intraday ? `<span><i style="background:${COL.faint}"></i>${esc(h.baseline_label || "Yesterday's close")}</span>`
    : (h.has_deposits ? `<span><i style="background:${COL.faint}"></i>Break-even</span>` : "") + `<span><i style="background:${COL.amber};width:7px;height:7px;border-radius:2px"></i>a move</span>`)
    + (gaps.length ? `<span title="The tracker took no readings here (it was switched off). Both lines hold flat across the gap, then pick up from the next reading."><i class="hatch"></i>No readings</span>` : "");
  $$("[data-bench]", box).forEach((el) => el.addEventListener("click", () => { const b = state.bench.find((x) => x.key === el.dataset.bench); b.on = !b.on; loadChart(false); }));
  let datasets, yFmt;
  state.series = { P, label: h.baseline_label, anchorReading: h.anchor_reading, anchor: h.anchor, intraday: !!h.intraday, early: !!h.early, even: !!h.even || (!h.intraday && !!h.has_deposits), base: h.baseline || P[0]?.value, baseLabel: (h.baseline_label || "Yesterday's close").toLowerCase() };
  const thin = P.length < 2, note = $("#chart-empty");
  note.hidden = !thin;
  note.textContent = h.intraday ? "Today's line appears once the tracker has a few minutes of prices." : "The chart starts after the first full day with money in the pie.";
  if (thin) { state.charts["main-chart"]?.destroy(); delete state.charts["main-chart"]; return; }
  if (h.intraday) {
    const base = state.series.base, d = P.map((p) => ({ x: new Date(p.t).getTime(), y: h.even && p.dep ? p.value / p.dep - 1 : p.u != null ? p.u / P[0].u - 1 : base ? p.value / base - 1 : 0 }));
    yFmt = pct2;
    datasets = [{ label: h.early ? "The bot" : "Today", data: d, borderColor: COL.amber, backgroundColor: areaFill(COL.amber), fill: "origin", borderWidth: 2.2, pointRadius: 0, pointHoverRadius: 0, tension: 0.2, glow: true, tag: pct2 }];
    // the market over the same minutes, from the same starting point
    state.bench.forEach((b, i) => { const hb = b.on && h.benchmarks?.find((x) => x.key === b.key); if (hb?.points?.length) datasets.push({ label: b.name, segment: FUT_DASH, data: hb.points.map((p) => ({ x: new Date(p.t).getTime(), y: p.pct, f: p.f })), borderColor: bc[i % 2], borderWidth: 1.6, pointRadius: 0, pointHoverRadius: 0, tension: 0.2, order: 2, tag: pct2 }); });
    // where the money started: break-even, straight down (or up) to the bot's first reading
    if (h.origin && d.length) datasets.push({ label: "Start", data: [{ x: d[0].x, y: 0 }, d[0]], borderColor: COL.amber, borderWidth: 2.2, pointRadius: [3.5, 0], pointBackgroundColor: COL.amber, pointHoverRadius: 0, noScrub: true, order: 1 });
    if (d.length) datasets.push({ label: h.baseline_label || "Yesterday's close", data: [{ x: h.start ? new Date(h.start).getTime() : d[0].x, y: 0 }, { x: d.at(-1).x, y: 0 }], borderColor: COL.faint, borderDash: [4, 4], borderWidth: 1.2, pointRadius: 0, pointHoverRadius: 0, noScrub: true });
  } else {
    const ev = !!h.has_deposits;   // 0% = break-even: worth what's been paid in
    const main = P.map((p) => ({ x: tms(p.t), y: ev && p.deposits ? p.value / p.deposits - 1 : p.pie }));
    yFmt = pct;
    datasets = [{ label: "The bot", data: main, borderColor: COL.amber, backgroundColor: areaFill(COL.amber), fill: "origin", borderWidth: 2.4, pointRadius: 0, pointHoverRadius: 0, tension: 0.15, order: 1, glow: true, tag: pct }];
    state.bench.forEach((b, i) => { if (b.on) datasets.push({ label: b.name, data: P.filter((p) => (ev ? p.bench_value?.[b.key] : p.bench?.[b.key]) != null).map((p) => ({ x: tms(p.t), y: ev && p.deposits ? p.bench_value[b.key] / p.deposits - 1 : p.bench[b.key] })), borderColor: bc[i % 2], borderWidth: 1.6, pointRadius: 0, pointHoverRadius: 0, tension: 0.15, order: 2, tag: pct }); });
    if (ev && h.origin && main.length) datasets.push({ label: "Start", data: [{ x: main[0].x, y: 0 }, main[0]], borderColor: COL.amber, borderWidth: 2.4, pointRadius: [3.5, 0], pointBackgroundColor: COL.amber, pointHoverRadius: 0, noScrub: true, order: 1 });
    const first = main[0]?.x ?? 0;
    datasets.push({ label: "Move", type: "scatter", order: 0, pointStyle: "rectRounded", pointRadius: 3.5, pointHoverRadius: 3.5, backgroundColor: COL.amber, borderColor: COL.bg, borderWidth: 1.5,
      data: reb.filter((r) => new Date(r.ts).getTime() >= first - 864e5).map((r) => { const x = new Date(r.ts).getTime(), pt = main[nearest(main, x)]; return { x, y: pt?.y }; }).filter((p) => p.y != null) });
  }
  const o = timeOpts(yFmt, { intraday: h.intraday, tags: true, tooltip: false });
  // 1D: from 09:00 UK time at the left edge
  if (h.start) {
    o.scales.x.min = new Date(h.start).getTime();
    // round hours along the bottom: 09:00, 11:00, 13:00 ...
    const hrs = (Date.now() - o.scales.x.min) / 3600e3, step = hrs > 30 ? 6 : hrs > 20 ? 4 : hrs > 8 ? 2 : hrs > 3 ? 1 : null;
    if (step) { o.scales.x.time.unit = "hour"; o.scales.x.time.stepSize = step; o.scales.x.ticks.stepSize = step; o.scales.x.ticks.maxTicksLimit = 12; }
  }
  o.plugins.scrub = { onMove: onScrub };
  o.plugins.gapBands = { gaps };
  o.events = ["mousemove", "mouseout", "touchstart", "touchmove", "touchend"];
  const ch = chart("main-chart", { type: "line", data: { datasets }, options: o });
  if (animate && P.length > 2) { state.chartShown = true; reveal(ch); }
}

/* squarified treemap: [{v}] -> rectangles filling W x H */
function squarify(items, W, H) {
  const total = items.reduce((a, i) => a + i.v, 0) || 1, k = W * H / total, out = [];
  let x = 0, y = 0, w = W, h = H, row = [];
  const worst = (r, side) => { const s = r.reduce((a, q) => a + q.a, 0), mx = Math.max(...r.map((q) => q.a)), mn = Math.min(...r.map((q) => q.a)); return Math.max(side * side * mx / (s * s), s * s / (side * side * mn)); };
  const place = (r) => {
    const s = r.reduce((a, q) => a + q.a, 0);
    if (w >= h) { const cw = s / h; let cy = y; r.forEach((q) => { out.push({ ...q.i, x, y: cy, w: cw, h: q.a / cw }); cy += q.a / cw; }); x += cw; w -= cw; }
    else { const ch = s / w; let cx = x; r.forEach((q) => { out.push({ ...q.i, x: cx, y, w: q.a / ch, h: ch }); cx += q.a / ch; }); y += ch; h -= ch; }
  };
  for (const i of items) {
    const q = { i, a: i.v * k }, side = Math.min(w, h);
    if (!row.length || worst([...row, q], side) <= worst(row, side)) row.push(q); else { place(row); row = [q]; }
  }
  if (row.length) place(row);
  return out;
}
const heat = (v, span) => (bad(v) ? "rgba(237,235,230,.07)" : `rgba(${v >= 0 ? "61,220,151" : "255,107,107"},${(0.14 + 0.6 * clamp(Math.abs(v) / span, 0, 1)).toFixed(3)})`);
function renderMap() {
  const host = $("#treemap"), hs = held(), W = host.clientWidth, H = host.clientHeight;
  if (!W || !H) return;
  const mode = state.mapMode, val = (h) => (mode === "day" ? h.day_pct : mode === "total" ? h.pl_pct : h.analyst?.upside), span = { day: 0.05, total: 0.35, analyst: 1 }[mode];
  // uninvested cash (what couldn't be split between the holdings) gets a tile of its own
  const cash = state.holdingsData?.cash || 0, hv = state.holdingsData?.holdings_value || hs.reduce((a, h) => a + (h.value || 0), 0), share = hv / (hv + cash || 1);
  const items = hs.map((h) => ({ item: h, v: Math.max(h.weight * share, 0.004) }));
  if (cash > 0.005) items.push({ item: { ticker: "_cash", symbol: "Cash", cash: true, weight: cash / (hv + cash) }, v: Math.max(cash / (hv + cash), 0.004) });
  const rects = squarify(items.sort((a, b) => b.v - a.v), W, H);
  const seen = new Set();
  rects.forEach((r) => {
    const h = r.item; seen.add(h.ticker);
    if (h.cash) {
      let el = host.querySelector('[data-tk="_cash"]');
      if (!el) { el = document.createElement("div"); el.className = "tile cash"; el.dataset.tk = "_cash"; el.title = "Money in the pie that couldn't be split between the holdings"; host.append(el); }
      Object.assign(el.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px`, background: "rgba(237,235,230,.05)", fontSize: `${clamp(Math.sqrt(r.w * r.h) / 6.5, 11, 24)}px` });
      el.innerHTML = `<b>Cash</b>${r.w > 46 && r.h > 40 ? `<span class="muted" style="font-size:.78em">${G(gbp(cash))}</span>` : ""}${r.w > 104 && r.h > 54 ? `<small>${pp(h.weight)}</small>` : ""}`;
      return;
    }
    let el = host.querySelector(`[data-tk="${CSS.escape(h.ticker)}"]`);
    if (!el) { el = document.createElement("a"); el.className = "tile"; el.dataset.tk = h.ticker; el.href = stockHref(h.ticker); host.append(el); }
    const v = val(h), fs = clamp(Math.sqrt(r.w * r.h) / 6.5, 11, 24);
    Object.assign(el.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px`, background: heat(v, span), fontSize: `${fs}px` });
    el.innerHTML = `<b>${esc(h.symbol)}</b>${r.w > 46 && r.h > 40 ? `<span class="${cls(v)}" style="font-size:.78em">${mode === "analyst" ? p0s(v) : pct(v)}</span>` : ""}${r.w > 104 && r.h > 54 ? `<small>${pp(h.weight)}</small>` : ""}`;
  });
  $$(".tile", host).forEach((el) => { if (!seen.has(el.dataset.tk)) el.remove(); });
}
async function loadMap() {
  renderMap();
  const m = held().filter((h) => !bad(h.day_pct)).sort((a, b) => b.day_pct - a.day_pct);
  const up = m.filter((h) => h.day_pct >= 0), down = m.filter((h) => h.day_pct < 0).reverse();
  $("#movers-meta").textContent = m.length ? `${up.length} up, ${down.length} down` : "";
  const big = Math.max(...m.map((h) => Math.abs(h.day_pct)), 0.01);
  const row = (h) => `<a class="mover" data-tk="${esc(h.ticker)}" href="${stockHref(h.ticker)}"><span class="tk">${esc(h.symbol)}</span><span class="track"><i style="width:${Math.abs(h.day_pct) / big * 100}%;background:var(--${h.day_pct >= 0 ? "up" : "down"})"></i></span><span class="pc ${cls(h.day_pct)}">${pct2(h.day_pct)}</span><span class="gb">${G(gbpS(h.day_gbp))}</span></a>`;
  const nUp = Math.min(up.length, down.length ? 5 : 9), nDown = Math.min(down.length, 9 - nUp);
  $("#movers").innerHTML = m.length ? up.slice(0, nUp).map(row).join("") + (nDown && nUp ? `<div class="sep"></div>` : "") + down.slice(0, nDown).map(row).join("") : `<p class="empty">Nothing held yet.</p>`;
}
new ResizeObserver(() => requestAnimationFrame(renderMap)).observe($("#treemap"));
$$("#map-mode button").forEach((b) => b.addEventListener("click", () => { state.mapMode = b.dataset.map; $$("#map-mode button").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); renderMap(); }));

const ago = (iso) => { const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000); return bad(m) ? "" : m < 60 ? `${Math.max(m, 1)}m ago` : m < 1440 ? `${Math.round(m / 60)}h ago` : m < 2880 ? "yesterday" : dShort(iso.slice(0, 10)); };
const headline = (x) => `<li><a class="t" href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.title)}</a><span class="when">${x.published ? ago(x.published) : ""}</span>
  <span class="src">${(x.tickers || []).slice(0, 4).map((t, i) => `<a class="tag" data-tk="${esc(t)}" href="${stockHref(t)}">${esc(x.symbols[i])}</a>`).join("")}${esc(x.publisher || "")}</span></li>`;
async function loadNews() {
  const n = await data.news(), items = n.items || [];
  const today = items.filter((x) => x.published && Date.now() - new Date(x.published).getTime() < 24 * 3600e3);
  $("#news-title").textContent = today.length >= 4 || !items.length ? "Top news today" : "Latest news";
  $("#news-meta").textContent = items.length ? `across ${n.stocks_covered} of ${n.stocks_held} stocks` : "";
  $("#news").innerHTML = (today.length >= 4 ? today : items).slice(0, 8).map(headline).join("") || `<li><span class="empty">Headlines arrive within half an hour of the tracker starting.</span></li>`;
}

/* analysts */
const RANK = { "Strong buy": 1, Buy: 2, Hold: 3, Underperform: 4, Sell: 5 };
const RCOL = ["rgba(237,235,230,.2)", COL.up, "#9BE8C4", COL.amber, "#FF9E7A", COL.down];
const pill = (r) => `<span class="pill r${RANK[r] || 0}">${esc(r || "No rating")}</span>`;
async function loadConsensus() {
  const hs = state.empty ? state.holdingsData?.holdings || [] : held();
  const rated = hs.filter((h) => h.analyst?.rating), cov = hs.filter((h) => !bad(h.analyst?.upside));
  const box = $("#consensus");
  $("#consensus-meta").textContent = cov.length ? `${cov.length} of ${hs.length} covered` : "";
  if (!cov.length && !rated.length) { box.innerHTML = `<p class="empty">Analyst data loads with the company profiles, a few minutes after the tracker starts.</p>`; return; }
  const ups = cov.map((h) => h.analyst.upside).sort((a, b) => a - b), med = ups.length ? (ups[(ups.length - 1) >> 1] + ups[ups.length >> 1]) / 2 : null;
  const counts = [1, 2, 3, 4, 5].map((k) => rated.filter((h) => RANK[h.analyst.rating] === k)), none = hs.length - rated.length;
  const rows = [["Strong buy", 1], ["Buy", 2], ["Hold", 3], ["Underperform", 4], ["Sell", 5]].filter(([, k]) => counts[k - 1].length);
  box.innerHTML = `<p class="big-num ${cls(med)}">${p0s(med)}</p><p class="sub">typical gap between today's price and analysts' average 12-month target</p>
    <div class="dist" role="img" aria-label="Ratings: ${rows.map(([l, k]) => `${counts[k - 1].length} ${l}`).join(", ")}">${rows.map(([, k]) => `<i style="flex:${counts[k - 1].length};background:${RCOL[k]}"></i>`).join("")}${none ? `<i style="flex:${none};background:${RCOL[0]}"></i>` : ""}</div>
    <div class="dist-key">${rows.map(([l, k]) => `<div title="${esc(counts[k - 1].map((h) => h.symbol).join(", "))}"><i style="background:${RCOL[k]}"></i><span>${l}</span><b>${counts[k - 1].length}</b></div>`).join("")}${none ? `<div><i style="background:${RCOL[0]}"></i><span class="muted">No rating</span><b>${none}</b></div>` : ""}</div>
    ${cov.length ? `<p class="sub" style="margin-top:18px">Furthest below their targets</p><div class="rows">${[...cov].sort((a, b) => b.analyst.upside - a.analyst.upside).slice(0, 4).map((h) => `<a class="mover" style="grid-template-columns:52px minmax(0,1fr) 62px" data-tk="${esc(h.ticker)}" href="${stockHref(h.ticker)}"><span class="tk">${esc(h.symbol)}</span><span class="track"><i style="width:${clamp(h.analyst.upside / Math.max(ups.at(-1), 0.01), 0, 1) * 100}%;background:var(--up)"></i></span><span class="pc ${cls(h.analyst.upside)}">${p0s(h.analyst.upside)}</span></a>`).join("")}</div>` : ""}`;
}

/* ================= holdings ================= */
function holdingsTable(all) {
  if (state.empty) {
    return `<thead><tr><th class="l">Stock</th><th class="l">Name</th><th class="l">Sector</th><th>Target</th></tr></thead><tbody>` +
      [...all].sort((a, b) => (b.target ?? 0) - (a.target ?? 0)).map((h) => `<tr data-go="${stockHref(h.ticker)}"><td class="l">${tk(h)}</td><td class="l name">${esc(h.name)}</td><td class="l muted">${esc(h.sector || "—")}</td><td>${bad(h.target) ? "—" : pp(h.target)}</td></tr>`).join("") + "</tbody>";
  }
  const maxW = Math.max(...all.map((h) => h.weight), 0.01);
  const cols = [
    { k: "symbol", t: "Stock", l: 1, f: (h) => tk(h) },
    { k: "name", t: "Name", l: 1, c: "name", f: (h) => esc(h.name) },
    { k: "sector", t: "Sector", l: 1, c: "muted", f: (h) => esc(h.sector || "—") },
    { k: "rank", t: "Analysts", l: 1, f: (h) => (h.analyst ? pill(h.analyst.rating) : `<span class="muted">—</span>`) },
    { k: "weight", t: "Weight", f: (h) => `<span class="wbar"><i style="width:${h.weight / maxW * 100}%"></i></span>${pp(h.weight)}` },
    { k: "target", t: "Target", c: "muted", f: (h) => (bad(h.target) ? "—" : pp(h.target)) },
    { k: "value", t: "Value", f: (h) => G(gbp(h.value)) },
    { k: "day_pct", t: "Today", f: (h) => `<span class="${cls(h.day_pct)}">${pct2(h.day_pct)}</span>` },
    { k: "pl", t: "Gain since bought", f: (h) => `<span class="${cls(h.pl)}">${G(gbpS(h.pl))}</span>` },
    { k: "pl_pct", t: "%", f: (h) => `<span class="${cls(h.pl_pct)}">${pct(h.pl_pct)}</span>` },
    { k: "upside", t: "To target", f: (h) => (bad(h.upside) ? `<span class="muted">—</span>` : `<span class="${cls(h.upside)}">${p0s(h.upside)}</span>`) },
    { k: "spark", t: "Last month", nosort: 1, f: (h) => `<span style="display:inline-block;vertical-align:middle">${spark((h.spark || []).slice(-22), 84, 22)}</span>` },
  ];
  let rows = all.filter((h) => h.value > 0).map((h) => ({ ...h, rank: RANK[h.analyst?.rating] || null, upside: h.analyst?.upside ?? null }));
  if (state.sector) rows = rows.filter((h) => (h.sector || "Other") === state.sector);
  const { key, dir } = state.sort;
  rows.sort((a, b) => { const x = a[key], y = b[key]; if (x == null) return 1; if (y == null) return -1; return (typeof x === "string" ? x.localeCompare(y) : x - y) * dir; });
  const head = cols.map((c) => { const on = key === c.k; return `<th scope="col" class="${c.l ? "l" : ""}"${on ? ` aria-sort="${dir > 0 ? "ascending" : "descending"}"` : ""}>${c.nosort ? c.t : `<button data-sort="${c.k}">${c.t}${on ? (dir > 0 ? " ↑" : " ↓") : ""}</button>`}</th>`; }).join("");
  const tot = rows.reduce((a, h) => a + (h.value || 0), 0), totPl = rows.reduce((a, h) => a + (h.pl || 0), 0), totW = rows.reduce((a, h) => a + h.weight, 0);
  const dayW = rows.reduce((a, h) => a + (h.day_gbp || 0), 0), day = tot - dayW > 0 ? dayW / (tot - dayW) : null;
  return `<thead><tr>${head}</tr></thead><tbody>${rows.map((h) => `<tr data-go="${stockHref(h.ticker)}">${cols.map((c) => `<td class="${[c.l ? "l" : "", c.c || ""].join(" ")}">${c.f(h)}</td>`).join("")}</tr>`).join("")}</tbody>
    <tfoot>${!state.sector && cashNow() > 0.005 ? `<tr class="cash-row"><td class="l" colspan="4">Cash <span class="muted">couldn't be split between the holdings</span></td><td></td><td></td><td>${G(gbp(cashNow()))}</td><td colspan="5"></td></tr>` : ""}<tr><td class="l" colspan="4">${state.sector ? esc(state.sector) : "Whole pie"}</td><td>${pp(totW)}</td><td></td><td>${G(gbp(tot + (state.sector ? 0 : cashNow())))}</td><td class="${cls(day)}">${pct2(day)}</td><td class="${cls(totPl)}">${G(gbpS(totPl))}</td><td colspan="3"></td></tr></tfoot>`;
}
const cashNow = () => state.holdingsData?.cash || 0;
const SECTOR_COLOURS = ["#FFB020", "#5CC8FF", "#3DDC97", "#FF8A65", "#B79CFF", "#EDEBE6", "#F2D06B", "#7FD1B9", "#9A978F", "#FF6B9A"];
function renderSectors(secs) {
  const host = $("#sectors"), r = 70, C_ = 2 * Math.PI * r; let acc = 0;
  if (!secs.length) { host.innerHTML = `<p class="empty">Sectors load with the company profiles.</p>`; return; }
  const arcs = secs.map((x, i) => { const a = `<circle class="seg-arc${state.sector === x.sector ? " on" : ""}" data-sector="${esc(x.sector)}" cx="95" cy="95" r="${r}" fill="none" stroke="${SECTOR_COLOURS[i % 10]}" stroke-width="24" stroke-dasharray="${Math.max(C_ * x.weight - 2.5, 0.5)} ${C_}" stroke-dashoffset="${-acc * C_}" transform="rotate(-90 95 95)"></circle>`; acc += x.weight; return a; }).join("");
  host.classList.toggle("focus", !!state.sector);
  host.innerHTML = `<svg viewBox="0 0 190 190" width="200" height="200" role="img" aria-label="Sector split">${arcs}<text id="donut-big" x="95" y="93" text-anchor="middle" fill="#EDEBE6" font-family="Inter Tight" font-weight="800" font-size="26"></text><text id="donut-sub" x="95" y="113" text-anchor="middle" fill="rgba(237,235,230,.55)" font-family="Inter Tight" font-size="11.5"></text></svg>
    <div class="donut-legend">${secs.map((x, i) => `<button type="button" data-sector="${esc(x.sector)}" class="${state.sector === x.sector ? "on" : ""}" aria-pressed="${state.sector === x.sector}"><i style="background:${SECTOR_COLOURS[i % 10]}"></i><span>${esc(x.sector)}</span><b>${pp(x.weight)}</b></button>`).join("")}</div>`;
  const centre = (name) => { const x = secs.find((q) => q.sector === name); $("#donut-big").textContent = x ? pp(x.weight) : String(secs.length); $("#donut-sub").textContent = x ? (x.sector.length > 20 ? x.sector.slice(0, 19) + "…" : x.sector) : "sectors"; };
  centre(state.sector);
  $$("[data-sector]", host).forEach((el) => {
    el.addEventListener("mouseenter", () => centre(el.dataset.sector));
    el.addEventListener("mouseleave", () => centre(state.sector));
    el.addEventListener("click", () => { state.sector = state.sector === el.dataset.sector ? null : el.dataset.sector; loadHoldings(); });
  });
}
function renderForest(hs) {
  const cov = hs.filter((h) => !bad(h.analyst?.upside)).sort((a, b) => b.analyst.upside - a.analyst.upside), host = $("#forest");
  if (!cov.length) { host.innerHTML = `<p class="empty">Analyst targets load with the company profiles.</p>`; return; }
  const lo = Math.max(-0.6, Math.min(0, ...cov.map((h) => h.analyst.low_pct ?? 0)) - 0.1), hi = Math.min(3, Math.max(0.4, ...cov.map((h) => h.analyst.high_pct ?? h.analyst.upside))) + 0.05;
  const at = (v) => clamp((v - lo) / (hi - lo) * 100, 0, 100);
  const row = (h) => { const a = h.analyst, l = a.low_pct ?? a.upside, u = a.high_pct ?? a.upside;
    return `<a class="frow${a.thin ? " thin" : ""}" data-tk="${esc(h.ticker)}" href="${stockHref(h.ticker)}" title="${a.analysts ?? "?"} analysts: low ${money(a.low, a.currency)}, average ${money(a.mean, a.currency)}, high ${money(a.high, a.currency)}; price ${money(a.price, a.currency)}"><span class="tk">${esc(h.symbol)}</span>${pill(a.rating)}
      <span class="fplot"><i class="zero" style="left:${at(0)}%"></i><i class="band" style="left:${at(l)}%;width:${Math.max(at(u) - at(l), 0.6)}%"></i><i class="mean" style="left:${at(a.upside)}%;background:var(--${a.upside >= 0 ? "up" : "down"})"></i></span><span class="u ${cls(a.upside)}">${p0s(a.upside)}</span></a>`; };
  const ticks = [0, 1, 2, 3].filter((t) => t <= hi - 0.15);
  host.innerHTML = cov.map(row).join("") + `<div class="frow axis"><span></span><span></span><span class="fplot">${ticks.map((t) => `<span style="left:${at(t)}%">${t ? `+${t * 100}%` : "price now"}</span>`).join("")}</span><span></span></div>`;
}
async function loadHoldings() {
  const d = state.holdingsData || await data.holdings(), hs = d.holdings || [], on = held();
  $("#hold-title").innerHTML = (state.empty ? "The bot's targets" : "Holdings") + (state.sector ? `<button class="filter" id="clear-sector" type="button">${esc(state.sector)} ×</button>` : "");
  $("#clear-sector")?.addEventListener("click", () => { state.sector = null; loadHoldings(); });
  $("#hold-meta").textContent = state.empty ? `${hs.length} targets` : `${on.length} stocks, click a row for detail`;
  const t = $("#hold-table");
  t.innerHTML = holdingsTable(hs);
  $$("[data-sort]", t).forEach((b) => b.addEventListener("click", () => { const k = b.dataset.sort; state.sort = { key: k, dir: state.sort.key === k ? -state.sort.dir : (["symbol", "name", "sector", "rank"].includes(k) ? 1 : -1) }; loadHoldings(); }));
  renderForest(state.empty ? hs : on);
  renderSectors((d.sectors || []).filter((x) => x.weight > 0));
  if (state.empty) return;
  const tot = on.reduce((a, h) => a + h.value, 0) || 1, byPl = on.filter((h) => !bad(h.pl)).sort((a, b) => b.pl - a.pl);
  const best = byPl[0], worst = byPl.at(-1);
  $("#contrib-meta").textContent = best ? "stocks already sold aren't shown" : "";
  const go = (rows) => (e, els) => { if (els[0]) location.hash = stockHref(rows[els[0].index].ticker).slice(1); };
  const hover = (e, els) => { e.native.target.style.cursor = els.length ? "pointer" : ""; };
  const priv = state.private, cv = (h) => (priv ? h.pl / tot : h.pl), cf = priv ? pct2 : gbpS;
  chart("contrib-chart", { type: "bar", data: { labels: byPl.map((h) => h.symbol), datasets: [{ data: byPl.map(cv), backgroundColor: byPl.map((h) => (h.pl >= 0 ? COL.up : COL.down)), borderRadius: 4, maxBarThickness: 18 }] },
    options: barOpts({ indexAxis: "y", onClick: go(byPl), onHover: hover, plugins: { legend: { display: false }, tooltip: { callbacks: { title: (it) => byPl[it[0].dataIndex].name, label: (it) => ` ${cf(it.raw)}${priv ? " of the pie" : ""}, ${pct(byPl[it.dataIndex].pl_pct)} on the position` } } },
      scales: { x: { grid: { color: COL.hair }, border: { display: false }, ticks: { callback: (v) => (priv ? pct(v) : gbpS(v).replace(".00", "")) } }, y: { grid: { display: false }, border: { display: false }, ticks: { autoSkip: false, color: COL.fg, font: { family: "'JetBrains Mono', monospace", size: 11, weight: 700 } } } } }) });
  const drift = on.filter((h) => !bad(h.target)).map((h) => ({ ...h, d: (h.weight - h.target) * 100 })).sort((a, b) => b.d - a.d);
  chart("drift-chart", { type: "bar", data: { labels: drift.map((h) => h.symbol), datasets: [{ data: drift.map((h) => h.d), backgroundColor: drift.map((h) => (Math.abs(h.d) >= 1.5 ? COL.amber : "rgba(237,235,230,.45)")), borderRadius: 4, maxBarThickness: 22 }] },
    options: barOpts({ onClick: go(drift), onHover: hover, plugins: { legend: { display: false }, tooltip: { callbacks: { title: (it) => drift[it[0].dataIndex].name, label: (it) => ` ${pp(drift[it.dataIndex].weight)} now against a ${pp(drift[it.dataIndex].target)} target` } } },
      scales: { y: { grid: { color: COL.hair }, border: { display: false }, ticks: { callback: (v) => `${v > 0 ? "+" : ""}${+v.toFixed(2)}` } }, x: { grid: { display: false }, border: { display: false }, ticks: { autoSkip: false, maxRotation: 0, color: COL.fg, font: { family: "'JetBrains Mono', monospace", size: 10.5, weight: 700 } } } } }) });
}
$("#hold-table").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-go]"); if (tr && !e.target.closest("a, button")) location.hash = tr.dataset.go.slice(1); });

/* ================= deeper analysis (Quant page) ================= */
state.deepSrc = { rb: "mix", acf: "pie" };
const segSrc = (sel, v) => $$(`${sel} button`).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.src === v)));
const mean_ = (x) => x.reduce((s, v) => s + v, 0) / x.length;
function pieMinuteReturns() {
  const S = state.deepA?.series_min || [], out = [];
  for (let i = 1; i < S.length; i++) if (new Date(S[i][0]) - new Date(S[i - 1][0]) < 600e3) out.push(S[i][1] / S[i - 1][1] - 1);
  return out;   // within a session only: the night's gap isn't a minute's move
}
const deepReturns = (src) => (src === "pie" ? pieMinuteReturns() : (state.deep?.mix_returns || []).map((x) => x[1]));
const mixNote = () => `Today's mix: today's weights held through the past year (${state.deep?.days || "?"} trading days to ${state.deep?.to ? dShort(state.deep.to) : "now"}).`;
async function loadDeep() {
  const [q, a] = await Promise.all([data.quant(), data.analysis()]);
  state.deep = q; state.deepA = a;
  for (const f of [drawRolling, drawFactors, drawOptions, drawACF]) { try { f(); } catch (e) { console.error(e); } }
}

function drawRolling() {
  const q = state.deep, src = state.deepSrc.rb; segSrc("#rb-src", src);
  const R = (src === "pie" ? q.pie_rolling : q.rolling) || [];
  if (R.length < 3) { $("#rb-meta").textContent = ""; chartNote("rb-chart", src === "pie" ? "Needs about half an hour of readings." : "Needs a few months of prices."); return; }
  chartNote("rb-chart", null);
  const L = R.at(-1), xs = R.map((r) => new Date(r.t).getTime());
  $("#rb-meta").innerHTML = (src === "pie" ? `This pie against the S&P, over a moving ${q.pie_window} readings (about ${q.pie_window} minutes).` : `${mixNote()} Against the S&P over a moving three months.`)
    + ` Beta now <b>${n2(L.beta)}</b> (95% range ${n2(L.lo)} to ${n2(L.hi)}): when the S&P moves 1%, the pie has moved about ${n2(L.beta)}%. Alpha, a year at this pace: <b class="${cls(L.alpha)}">${pct(L.alpha)}</b>`
    + warn(src === "pie" ? "From minute readings over a very short time: alpha especially is scaled up from minutes to a year and means very little yet." : null);
  const o = timeOpts((v) => n2(v), { intraday: src === "pie" });
  o.scales.y.position = "left";
  o.scales.y2 = { position: "right", grid: { display: false }, border: { display: false }, ticks: { callback: (v) => pct(v), maxTicksLimit: 6 } };
  o.plugins.tooltip.filter = (it) => !it.dataset.band;
  o.plugins.tooltip.callbacks.label = (it) => ` ${it.dataset.label}: ${it.dataset.yAxisID === "y2" ? pct(it.raw.y) : n2(it.raw.y)}`;
  chart("rb-chart", { type: "line", data: { datasets: [
    { label: "low", data: R.map((r, i) => ({ x: xs[i], y: r.lo })), borderWidth: 0, pointRadius: 0, fill: false, band: true, yAxisID: "y" },
    { label: "high", data: R.map((r, i) => ({ x: xs[i], y: r.hi })), borderWidth: 0, pointRadius: 0, fill: "-1", backgroundColor: "rgba(92,200,255,.16)", band: true, yAxisID: "y" },
    { label: "Beta", data: R.map((r, i) => ({ x: xs[i], y: r.beta })), borderColor: COL.blue, borderWidth: 2, pointRadius: 0, yAxisID: "y" },
    { label: "Alpha, a year", data: R.map((r, i) => ({ x: xs[i], y: r.alpha })), borderColor: COL.amber, borderWidth: 1.4, borderDash: [5, 4], pointRadius: 0, yAxisID: "y2" },
    { label: "Beta of 1", data: [{ x: xs[0], y: 1 }, { x: xs.at(-1), y: 1 }], borderColor: COL.faint, borderDash: [3, 4], borderWidth: 1, pointRadius: 0, band: true, yAxisID: "y" },
  ] }, options: o });
}

function drawFactors() {
  const f = state.deep?.factors;
  if (!f) { chartNote("fx-chart", "Needs a few months of prices for the style funds."); return; }
  chartNote("fx-chart", null);
  const F = f.factors, sig = (x) => x.lo > 0 || x.hi < 0;
  $("#fx-meta").textContent = `explains ${p0(f.r2)} of its daily moves`;
  $("#fx-sub").innerHTML = `${mixNote()} Each bar: how much the mix moves with that style, with its 95% range. Coloured when the range doesn't include zero. What's left over (alpha): <b class="${cls(f.alpha)}">${pct(f.alpha)}</b> a year (range ${pct(f.alpha_lo)} to ${pct(f.alpha_hi)}).`;
  const col = (x) => (!sig(x) ? "rgba(237,235,230,.25)" : x.beta > 0 ? COL.up : COL.down);
  chart("fx-chart", { type: "bar", data: { labels: F.map((x) => x.name), datasets: [
    { label: "95% range", data: F.map((x) => [x.lo, x.hi]), backgroundColor: F.map((x) => col(x) + (sig(x) ? "55" : "")), borderRadius: 3, barPercentage: 0.5, grouped: false, order: 2 },
    { label: "Exposure", data: F.map((x) => [x.beta - 0.012, x.beta + 0.012]), backgroundColor: F.map(col), barPercentage: 0.9, grouped: false, order: 1 },
  ] }, options: barOpts({ indexAxis: "y", plugins: { legend: { display: false }, tooltip: { callbacks: {
      title: (it) => `${F[it[0].dataIndex].name}: ${F[it[0].dataIndex].about}`,
      label: (it) => { const x = F[it.dataIndex]; return it.datasetIndex ? ` Exposure ${n2s(x.beta)} (t ${n2(x.t)})` : ` 95% range ${n2s(x.lo)} to ${n2s(x.hi)}`; } } } },
    scales: { x: { grid: { color: COL.hair }, border: { display: false }, ticks: { callback: (v) => n2s(v) } }, y: { grid: { display: false }, border: { display: false }, ticks: { color: COL.fg } } } }) });
}

function drawOptions() {
  const o = state.deep?.options, S = o?.stocks || [];
  if (S.length < 2) { chartNote("opt-chart", "Waiting for option prices for the US holdings."); return; }
  chartNote("opt-chart", null);
  const top = Math.max(...S.flatMap((x) => [x.iv, x.rv]), o.pie?.iv || 0, o.pie?.rv || 0) * 1.08;
  $("#opt-meta").textContent = `${S.length} stocks with options`;
  $("#opt-sub").innerHTML = `Across: how much each stock actually moved over the last month (as a yearly volatility). Up: how much its options expect it to move over the next month. Above the line, the options price in more fear than recent reality; below, more calm.`
    + (o.pie?.iv ? ` The whole mix: options expect <b>${pct(o.pie.iv).replace("+", "")}</b>, it has been moving <b>${pct(o.pie.rv).replace("+", "")}</b>.` : "");
  chart("opt-chart", { type: "scatter", data: { datasets: [
    { label: "Same", type: "line", data: [{ x: 0, y: 0 }, { x: top, y: top }], borderColor: COL.faint, borderDash: [4, 4], borderWidth: 1, pointRadius: 0 },
    { type: "bubble", label: "Stocks", data: S.map((x) => ({ x: x.rv, y: x.iv, r: 4 + Math.sqrt(x.weight) * 30, s: x })), backgroundColor: S.map((x) => (x.iv > x.rv ? "rgba(255,107,107,.55)" : "rgba(61,220,151,.55)")), borderColor: S.map((x) => (x.iv > x.rv ? COL.down : COL.up)), borderWidth: 1 },
    ...(o.pie?.iv ? [{ label: "The whole mix", data: [{ x: o.pie.rv, y: o.pie.iv }], pointStyle: "rectRot", pointRadius: 9, backgroundColor: COL.amber, borderColor: COL.bg, borderWidth: 2 }] : []),
  ] }, options: { responsive: true, maintainAspectRatio: false, animation: false,
    plugins: { legend: { display: false }, tooltip: { filter: (it) => it.dataset.label !== "Same", callbacks: {
      title: (it) => it[0].raw.s ? it[0].raw.s.symbol : it[0].dataset.label,
      label: (it) => [` Options expect ${pct(it.raw.y).replace("+", "")}`, ` It has moved ${pct(it.raw.x).replace("+", "")}`, ` ${it.raw.y > it.raw.x ? "More fear than reality" : "Calmer than reality"}`] } } },
    scales: { x: { min: 0, max: top, title: { display: true, text: "moved, last month", color: COL.faint }, grid: { color: COL.hair }, border: { display: false }, ticks: { callback: (v) => p0(v) } },
      y: { min: 0, max: top, title: { display: true, text: "options expect", color: COL.faint }, grid: { color: COL.hair }, border: { display: false }, ticks: { callback: (v) => p0(v) } } } } });
}

function drawACF() {
  const src = state.deepSrc.acf; segSrc("#acf-src", src);
  const r = deepReturns(src);
  if (r.length < 40) { $("#acf-meta").textContent = ""; chartNote("acf-chart", "Not enough returns yet."); return; }
  chartNote("acf-chart", null);
  const K = 20, n = r.length, acf = (x) => { const m = mean_(x), d = x.map((v) => v - m), den = d.reduce((s, v) => s + v * v, 0) || 1e-12; return Array.from({ length: K }, (_, k) => d.slice(k + 1).reduce((s, v, i) => s + v * d[i], 0) / den); };
  const a1 = acf(r), a2 = acf(r.map((v) => v * v)), band = 1.96 / Math.sqrt(n), out = (a) => a.filter((v) => Math.abs(v) > band).length;
  const unit = src === "pie" ? "minute" : "day";
  $("#acf-meta").innerHTML = `How much a ${unit}'s move is echoed 1 to ${K} ${unit}s later. Amber: the moves themselves (outside the band = some pattern to the direction). Blue: the size of the moves (squared): outside the band means calm and wild spells come in bunches. Outside the 95% band: <b>${out(a1)}</b> of ${K} for moves, <b>${out(a2)}</b> of ${K} for sizes (about 1 would happen by chance).`
    + (src === "mix" ? ` ${mixNote()}` : "");
  chart("acf-chart", { data: { labels: Array.from({ length: K }, (_, k) => `${k + 1}`), datasets: [
    { type: "line", label: "95% band", data: Array(K).fill(band), borderColor: COL.faint, borderDash: [4, 4], borderWidth: 1, pointRadius: 0 },
    { type: "line", label: "95% band ", data: Array(K).fill(-band), borderColor: COL.faint, borderDash: [4, 4], borderWidth: 1, pointRadius: 0 },
    { type: "bar", label: "Moves", data: a1, backgroundColor: a1.map((v) => (Math.abs(v) > band ? COL.amber : "rgba(255,176,32,.35)")), borderRadius: 2, barPercentage: 0.9, categoryPercentage: 0.7 },
    { type: "bar", label: "Sizes of moves", data: a2, backgroundColor: a2.map((v) => (Math.abs(v) > band ? COL.blue : "rgba(92,200,255,.35)")), borderRadius: 2, barPercentage: 0.9, categoryPercentage: 0.7 },
  ] }, options: barOpts({ plugins: { legend: { display: false }, tooltip: { mode: "index", intersect: false, filter: (it) => it.dataset.type === "bar", callbacks: { title: (it) => `${it[0].label} ${unit}${it[0].label === "1" ? "" : "s"} later`, label: (it) => ` ${it.dataset.label}: ${n2s(it.raw)}` } } },
    scales: { x: { title: { display: true, text: `lag (${unit}s)`, color: COL.faint }, grid: { display: false }, border: { display: false } }, y: { grid: { color: COL.hair }, border: { display: false }, ticks: { callback: (v) => n2s(v), maxTicksLimit: 6 } } } }) });
}
for (const [sel, key, fn] of [["#rb-src", "rb", () => drawRolling()], ["#acf-src", "acf", () => drawACF()]])
  $$(`${sel} button`).forEach((b) => b.addEventListener("click", () => { state.deepSrc[key] = b.dataset.src; fn(); }));

/* ================= risk against reward ================= */
state.rrN = "63";
async function loadRR() {
  const d = await data.rr(), n = state.rrN, lab = { 21: "month", 63: "three months", 252: "year" }[n];
  $$("#rr-window button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.rr === n)));
  const S = (d.stocks || []).filter((x) => x[n]), pie = d.pie?.[n], B = (d.benchmarks || []).filter((x) => x[n]);
  if (!S.length) { chartNote("rr-chart", "Needs a few weeks of daily prices for each stock."); return; }
  chartNote("rr-chart", null);
  const secs = [...new Set(S.map((x) => x.sector))].sort(), sc = (sname) => SECTOR_COLOURS[secs.indexOf(sname) % SECTOR_COLOURS.length];
  const xmax = Math.max(...S.map((x) => x[n].swing), pie?.swing || 0, ...B.map((b) => b[n].swing)) * 1.08;
  const ratios = [-1, 0.5, 1, 2];   // return per unit of swing: the dashed guides
  const yTop = Math.max(...S.map((x) => x[n].ret), pie?.ret ?? 0, ...B.map((b) => b[n].ret), 0.02) * 1.1, yBot = Math.min(...S.map((x) => x[n].ret), pie?.ret ?? 0, ...B.map((b) => b[n].ret), -0.02) * 1.1;
  const xEnd = (k) => Math.min(xmax, (k > 0 ? yTop : yBot) / k);   // stop each guide at the edge of the data
  const guides = ratios.map((k) => ({ type: "line", label: `return = ${k}× swing`, data: [{ x: 0, y: 0 }, { x: xEnd(k), y: k * xEnd(k) }], borderColor: "rgba(237,235,230,.16)", borderDash: [3, 5], borderWidth: 1, pointRadius: 0, order: 9, guide: k }));
  const r = (w) => 5 + Math.sqrt(w) * 34;
  const pts = S.map((x) => ({ x: x[n].swing, y: x[n].ret, r: r(x.weight), item: x }));
  $("#rr-meta").innerHTML = `over the last ${lab}: return (up) against how much it swung (across: the spread of daily moves, scaled to the ${lab}). Bubbles are sized by weight, coloured by sector; dashed lines join points with the same return per unit of swing.`
    + (pie ? ` Today's mix: <b class="${cls(pie.ret)}">${pct(pie.ret)}</b> for ${pct(pie.swing).replace("+", "")} of swing.` : "");
  chart("rr-chart", { data: { datasets: [
      ...guides,
      { type: "bubble", label: "Stocks", data: pts, backgroundColor: pts.map((p) => sc(p.item.sector) + "B3"), borderColor: pts.map((p) => sc(p.item.sector)), borderWidth: 1, order: 2 },
      ...(pie ? [{ type: "scatter", label: "Today's mix", data: [{ x: pie.swing, y: pie.ret }], pointStyle: "rectRot", pointRadius: 9, pointHoverRadius: 11, backgroundColor: COL.amber, borderColor: COL.bg, borderWidth: 2, order: 1 }] : []),
      ...B.map((b, i) => ({ type: "scatter", label: b.name, data: [{ x: b[n].swing, y: b[n].ret }], pointStyle: "rect", pointRadius: 7, pointHoverRadius: 9, backgroundColor: i ? COL.grey : COL.blue, borderColor: COL.bg, borderWidth: 2, order: 1 })),
    ] },
    options: { responsive: true, maintainAspectRatio: false, animation: false,
      onClick: (e, els) => { const el = els.find((x) => x.element && e.chart.data.datasets[x.datasetIndex].label === "Stocks"); if (el) location.hash = stockHref(pts[el.index].item.ticker).slice(1); },
      plugins: { legend: { display: false }, tooltip: { filter: (it) => it.dataset.guide == null, callbacks: {
        title: (it) => (it[0].dataset.label === "Stocks" ? `${pts[it[0].dataIndex].item.symbol}  ${pts[it[0].dataIndex].item.name}` : it[0].dataset.label),
        label: (it) => { const x = it.raw.x, y = it.raw.y; return [` Return ${pct(y)}`, ` Swing ${pct(x).replace("+", "")}`, ` Return per unit of swing ${n2(y / x)}`].concat(it.dataset.label === "Stocks" ? [` ${pp(pts[it.dataIndex].item.weight)} of the pie, ${pts[it.dataIndex].item.sector}`] : []); } } } },
      scales: { x: { type: "linear", min: 0, grid: { color: COL.hair }, border: { display: false }, title: { display: true, text: "swing", color: COL.faint }, ticks: { callback: (v) => pct(v).replace("+", ""), maxTicksLimit: 7 } },
        y: { grace: "6%", grid: { color: COL.hair }, border: { display: false }, title: { display: true, text: "return", color: COL.faint }, ticks: { callback: (v) => pct(v), maxTicksLimit: 7 } } } } });
}
$$("#rr-window button").forEach((b) => b.addEventListener("click", () => { state.rrN = b.dataset.rr; loadRR(); }));

/* ================= waterfall: what moved the pie ================= */
state.wf = "day";
async function loadWaterfall() {
  const s = state.summary, hs = held(), cash = state.holdingsData?.cash || 0;
  if (!s?.ready || !hs.length) return;
  const day = state.wf === "day", o = s.since_open;
  $$("#wf-mode button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.wf === state.wf)));
  $("#wf-title").textContent = day ? "What moved the pie today" : "What moved the pie since it bought";
  // each stock's £ change; "everything else" closes the gap to the pie's real change
  // (today: buying and selling during the day, costs, cash; since bought: cash, costs, sold stocks)
  const total = day ? (o ? o.gbp : s.day_change) : s.profit;
  if (bad(total)) { chartNote("wf-chart", "Not enough data yet."); return; }
  chartNote("wf-chart", null);
  let items = hs.map((h) => ({ k: h.symbol, tk: h.ticker, v: day ? h.day_gbp : h.pl })).filter((x) => !bad(x.v)).sort((a, b) => b.v - a.v);
  if (items.length > 16) { const keep = [...items.slice(0, 8), ...items.slice(-7)], rest = items.slice(8, -7); items = [...keep.slice(0, 8), { k: `${rest.length} others`, v: rest.reduce((x, y) => x + y.v, 0) }, ...keep.slice(8)]; }
  const other = total - items.reduce((x, y) => x + y.v, 0);
  if (Math.abs(other) >= 0.01) items.push({ k: day ? "Trading, cash" : "Cash, costs", v: other, other: true });
  const base = s.value - total, scale = state.private ? (x) => x / base : (x) => x;   // eye button: points instead of £
  let run = 0;
  const bars = [{ k: day ? "Open" : "Paid in", y: [0, 0], start: true }, ...items.map((x) => { const a = run; run += x.v; return { ...x, y: [scale(a), scale(run)] }; }), { k: "Now", y: [0, scale(total)], end: true }];
  const col = (b) => (b.start ? COL.faint : b.end ? COL.amber : b.other ? COL.grey : b.v >= 0 ? COL.up : COL.down);
  const fmt = (x) => (state.private ? pts(x) : gbpS(x));
  $("#wf-meta").innerHTML = `${day ? (o ? `from ${timeF.format(new Date(o.from))} today` : "today") : `against ${G(gbp0(s.net_deposits))} paid in`}: <b class="${cls(total)}">${G(gbpS(total))}</b> · best ${esc(items[0]?.k || "")}, worst ${esc(items.filter((x) => !x.other).at(-1)?.k || "")}`;
  chart("wf-chart", { type: "bar", data: { labels: bars.map((b) => b.k), datasets: [{ data: bars.map((b) => b.y), backgroundColor: bars.map(col), borderRadius: 3, borderSkipped: false, barPercentage: 0.82, categoryPercentage: 1 }] },
    options: barOpts({ plugins: { legend: { display: false }, tooltip: { callbacks: { title: (it) => bars[it[0].dataIndex].k, label: (it) => { const b = bars[it.dataIndex]; return b.start ? ` ${day ? "the value at the open" : "the money paid in"}` : b.end ? ` ${fmt(scale(total))} in all` : ` ${fmt(scale(b.v))}, running total ${fmt(b.y[1])}`; } } } },
      onClick: (e, els) => { const b = els[0] && bars[els[0].index]; if (b?.tk) location.hash = stockHref(b.tk).slice(1); },
      scales: { x: { grid: { display: false }, border: { display: false }, ticks: { autoSkip: false, maxRotation: 0, color: COL.fg, font: { family: "'JetBrains Mono', monospace", size: 10.5, weight: 700 } } },
        y: { grid: { color: COL.hair }, border: { display: false }, ticks: { callback: (v) => fmt(v), maxTicksLimit: 6 } } } }) });
}
$$("#wf-mode button").forEach((b) => b.addEventListener("click", () => { state.wf = b.dataset.wf; loadWaterfall(); }));

// dashed where a market line comes from index futures (the fund that tracks it wasn't trading)
const FUT_DASH = { borderDash: (ctx) => (ctx.p1?.raw?.f ? [5, 4] : undefined) };

/* ================= the weekly prediction game ================= */
const LS = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };
async function loadGame(st) {
  st = st || await data.game();
  const card = $("#game-card"); card.hidden = !st.players?.length; if (card.hidden) return;
  const o = st.open, me = LS.get("game-player") || "", dl = new Date(o.deadline);
  const when = `${dl.toLocaleDateString("en-GB", { weekday: "short", timeZone: "Europe/London" })} ${timeF.format(dl)}`;
  $("#game-meta").textContent = `week of ${dShort(o.week)}, closes ${when}`;
  const chips = st.players.map((p) => `<span class="gchip ${o.guessed.includes(p) ? "in" : ""}" title="${o.guessed.includes(p) ? "has guessed" : "hasn't guessed yet"}">${o.guessed.includes(p) ? "✓ " : ""}${esc(p)}</span>`).join("");
  const form = `<form class="gform" id="gform">
      <select id="gplayer" aria-label="Who are you?"><option value="">Who are you?</option>${st.players.map((p) => `<option ${p === me ? "selected" : ""}>${esc(p)}</option>`).join("")}</select>
      <label class="gnum"><input id="gguess" type="number" step="0.1" min="-50" max="50" inputmode="decimal" placeholder="+1.5" aria-label="Your guess for the week's return, in percent" required><span>%</span></label>
      <button type="submit" class="chip gbtn">Save guess</button></form>
    <p class="sub gnote" id="gnote">The pie's return from ${dShort(o.week)} to ${dShort(o.friday)}. Guesses stay hidden until ${when}; you can change yours until then.</p>
    <div class="gchips">${chips}</div>`;
  const c = st.current;
  const live = c ? `<h3 class="gh">This week so far <b class="${cls(c.return)}">${pct2(c.return)}</b></h3><table class="gtbl"><tbody>${c.guesses.map((g) => `<tr class="${g.winner ? "lead" : ""}"><td>${g.winner ? "🏆 " : ""}${esc(g.player)}</td><td class="${cls(g.guess)}">${pct2(g.guess)}</td><td class="muted">off by ${g.error == null ? "—" : `${(g.error * 100).toFixed(2)} pts`}</td></tr>`).join("") || `<tr><td class="muted">Nobody guessed this week.</td></tr>`}</tbody></table>` : "";
  const lb = st.leaderboard.filter((b) => b.played);
  const board = lb.length ? `<h3 class="gh">Leaderboard</h3><table class="gtbl"><thead><tr><th>Player</th><th>Wins</th><th>Average miss</th><th>Weeks</th></tr></thead><tbody>${lb.map((b, i) => `<tr><td>${i === 0 ? "👑 " : ""}${esc(b.player)}</td><td>${b.wins}</td><td>${b.avg_error == null ? "—" : `${(b.avg_error * 100).toFixed(2)} pts`}</td><td>${b.played}</td></tr>`).join("")}</tbody></table>` : `<p class="sub">The leaderboard starts after the first week is scored.</p>`;
  const last = st.past?.[0], lastLine = last ? `<p class="sub">Last week (${dShort(last.week)}): the pie did <b class="${cls(last.return)}">${pct2(last.return)}</b>, won by ${last.guesses.filter((g) => g.winner).map((g) => `${esc(g.player)} (${pct2(g.guess)})`).join(" and ") || "nobody"}.</p>` : "";
  $("#game").innerHTML = form + live + lastLine + board;
  $("#gform").addEventListener("submit", async (e) => {
    e.preventDefault();
    const player = $("#gplayer").value, guess = parseFloat($("#gguess").value), note = $("#gnote");
    if (!player) { note.textContent = "Pick your name first."; return; }
    if (!Number.isFinite(guess)) { note.textContent = "Type a percentage, like 1.5 or -2."; return; }
    LS.set("game-player", player);
    try {
      const r = await fetch("/api/game/guess", { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ player, guess }) });
      const body = await r.json();
      if (!r.ok) { $("#gnote").textContent = body.detail || "That didn't save. Try again."; return; }
      await loadGame(body);
      $("#gnote").innerHTML = `<b>Saved:</b> ${esc(player)}, ${pct2(guess / 100)} for the week of ${dShort(body.open.week)}.`;
    } catch { $("#gnote").textContent = "Couldn't reach the tracker. Try again in a moment."; }
  });
}

/* ================= the bot's moves: did they help, and the news around them ================= */
function moveVerdict(r) {
  const j = r.judged; if (!j) return "";
  const until = j.open ? "so far" : `until the next move (${dShort(j.to)})`;
  const why = `From the move (${dShort(j.from)} ${timeF.format(new Date(j.from))}) ${j.open ? "to now" : `to the next move on ${dShort(j.to)}`}: the return of the new target mix against what the old mix would have made, using each stock's price in pounds (${j.prices === "minute" ? "minute by minute" : "daily closes"}). Weights are held as the bot set them.${j.missing?.length ? ` No prices for ${j.missing.length} stock(s).` : ""}`;
  return `<div class="verdict" title="${esc(why)}"><span>Did it help? <span class="muted">${until}</span></span>
    <b class="${cls(j.value_added)}">${j.value_added >= 0 ? "Yes" : "Not yet"}, ${(j.value_added * 100 >= 0 ? "+" : "") + (j.value_added * 100).toFixed(2)} pts</b>
    <small>new mix <b class="${cls(j.new_mix)}">${pct2(j.new_mix)}</b> against <b class="${cls(j.old_mix)}">${pct2(j.old_mix)}</b> if it had changed nothing</small></div>`;
}
function moveNews(r) {
  const n = r.news || []; if (!n.length) return "";
  const kind = (k) => (k === "added" ? "bought" : k === "removed" ? "sold" : "");
  return `<h3 class="gh">In the news around the move</h3><ul class="mnews">${n.slice(0, 8).map((x) => `<li><a href="${esc(x.url)}" target="_blank" rel="noopener"><span class="tk">${esc(x.symbol)}</span>${kind(x.kind) ? `<span class="muted"> ${kind(x.kind)}</span>` : ""} ${esc(x.title)}</a><small>${esc(x.publisher || "")}${x.published ? ` · ${dShort(x.published)}` : ""}</small></li>`).join("")}</ul>`;
}

/* ================= today's recap ================= */
async function loadRecap() {
  const r = await data.recap(), card = $("#recap-card");
  card.hidden = !r?.date; if (card.hidden) return;
  const today = r.date === new Date().toLocaleDateString("en-CA", { timeZone: "Europe/London" });
  $("#recap-title").textContent = r.final ? (today ? "Today's recap" : `Recap, ${dShort(r.date)}`) : "Today so far";
  $("#recap-meta").textContent = r.final ? "as of the 21:30 close" : `updated ${timeF.format(new Date(r.built))}`;
  const o = r.since_open, b = Object.values(r.bench_since_open || {})[0], lead = o && b ? o.pct - b.change : null;
  const rows = [];
  if (o) rows.push(["Since the open", `<b class="${cls(o.pct)}">${pct2(o.pct)}</b> ${G(gbpS(o.gbp))}`, b ? `${esc(shortBench(b.name))} ${pct2(b.change)}, so <b class="${cls(lead)}">${pts(lead)}</b>` : ""]);
  if (r.best) rows.push(["Best", `<span class="tk">${esc(r.best.symbol)}</span> <b class="${cls(r.best.pct)}">${pct2(r.best.pct)}</b>`, `${r.up} up, ${r.down} down`]);
  if (r.worst) rows.push(["Worst", `<span class="tk">${esc(r.worst.symbol)}</span> <b class="${cls(r.worst.pct)}">${pct2(r.worst.pct)}</b>`, ""]);
  (r.moves || []).forEach((m) => rows.push(["The bot moved", `${m.count} changes${m.added.length ? `, bought ${esc(m.added.join(", "))}` : ""}${m.removed.length ? `, sold ${esc(m.removed.join(", "))}` : ""}`, m.judged ? `<b class="${cls(m.judged.value_added)}">${pts(m.judged.value_added)}</b> against the old mix` : ""]));
  rows.push(["Overall", `<b class="${cls(r.return_on_deposits)}">${pct(r.return_on_deposits)}</b> on ${G(gbp0(r.net_deposits))} paid in`, G(gbpS(r.profit))]);
  const g = r.game;
  if (g?.just_scored) { const w = g.just_scored.guesses.filter((x) => x.winner); rows.push(["Guess the week", `the pie did <b class="${cls(g.just_scored.return)}">${pct2(g.just_scored.return)}</b>`, w.length ? `🏆 ${esc(w.map((x) => x.player).join(" and "))}` : "nobody guessed"]); }
  else if (g) rows.push(["Guess the week", `${g.guessed} of ${g.players} guesses in`, `for the week of ${dShort(g.open_week)}`]);
  $("#recap").innerHTML = `<table class="gtbl recap"><tbody>${rows.map(([k, v, x]) => `<tr><td class="muted">${k}</td><td>${v}</td><td class="muted ar">${x}</td></tr>`).join("")}</tbody></table>`;
}

/* ================= where it could go (Monte Carlo) ================= */
state.mcH = 63;
state.mcView = "now";
async function loadMC() {
  const m0 = await data.mc(), card = $("#mc-card");
  card.hidden = !m0?.fan?.length; if (card.hidden) return;
  $("#mc-view").hidden = !m0.anchor;
  const fromStart = state.mcView === "start" && m0.anchor, m = fromStart ? m0.anchor : m0;
  $$("#mc-view button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.mcv === (fromStart ? "start" : "now"))));
  const H = state.mcH, F = [[0, m.value, m.value, m.value, m.value, m.value], ...m.fan.slice(0, H)], v0 = m.value;
  const at = (k) => F.map((r) => ({ x: r[0], y: r[k] / v0 - 1 }));
  // faint individual paths behind the range, and (from the start) the pie's real path over it
  const paths = (m.samples || []).map((sp) => ({ type: "line", data: [{ x: 0, y: 0 }, ...sp.slice(0, H).map((y, i) => ({ x: i + 1, y }))], borderColor: "rgba(255,176,32,.13)", borderWidth: 0.8, pointRadius: 0, fill: false, order: 9, sample: true }));
  const real = fromStart && m.real?.length ? [{ type: "line", label: "What really happened", data: m.real.map(([x, y]) => ({ x, y })), borderColor: COL.fg, borderWidth: 2.4, pointRadius: m.real.length < 4 ? 3 : 0, pointBackgroundColor: COL.fg, fill: false, order: 0, real: true }] : [];
  $("#mc-sub").textContent = (fromStart ? `The range as first made on ${dShort(m.built)} at ${timeF.format(new Date(m.built))}, with the pie's real path since (white, by its unit price, so deposits don't count). ` : "") + `${m.paths.toLocaleString("en-GB")} simulated paths built from how the ${m.holdings} stocks it holds now have moved day to day over the past year (${m.days_used} trading days), at today's weights, with the cash held flat. No trend up or down is assumed, so this shows how wide the range is, not a forecast.`;
  const band = (lo, hi, a) => [{ data: at(lo), borderWidth: 0, pointRadius: 0, fill: false }, { data: at(hi), borderWidth: 0, pointRadius: 0, fill: "-1", backgroundColor: `rgba(255,176,32,${a})` }];
  chart("mc-chart", { type: "line", data: { datasets: [...band(1, 5, 0.12), ...band(2, 4, 0.22),
      { label: "Middle", data: at(3), borderColor: COL.amber, borderWidth: 2, pointRadius: 0, fill: false }, ...paths, ...real] },
    options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false }, tooltip: { filter: (it) => it.datasetIndex === 4 || it.dataset.real, callbacks: { title: (it) => `${Math.round(it[0].raw.x * 10) / 10} trading days ${fromStart ? "from the start" : "ahead"}`, label: (it) => { if (it.dataset.real) return ` Really: ${pct2(it.raw.y)}`; const r = F[it.dataIndex]; if (!r) return ""; return [` Middle ${pct(r[3] / v0 - 1)}`, ` Half end between ${pct(r[2] / v0 - 1)} and ${pct(r[4] / v0 - 1)}`, ` 9 in 10 between ${pct(r[1] / v0 - 1)} and ${pct(r[5] / v0 - 1)}`]; } } } },
      scales: { x: { type: "linear", min: 0, max: H, grid: { display: false }, border: { display: false }, ticks: { stepSize: 21, callback: (d) => (d === 0 ? "now" : `${Math.round(d / 21)}M`) } },
        y: { grid: { color: COL.hair }, border: { display: false }, ticks: { callback: (x) => pct(x), maxTicksLimit: 6 } } } } });
  const end = F[F.length - 1], hz = m.horizons[String(H)] || {}, lab = { 21: "a month", 63: "three months", 252: "a year" }[H];
  $("#mc-stats").innerHTML = [
    ["Middle outcome", G(gbp(end[3])), "", `in ${lab}`],
    ["1 in 20 worse than", G(gbp(end[1])), "down", pct(end[1] / v0 - 1)],
    ["1 in 20 better than", G(gbp(end[5])), "up", pct(end[5] / v0 - 1)],
    ["Chance of a loss", p0(hz.loss), "", `below today's ${G(gbp0(v0))}`],
    ["Chance of −10% or worse", p0(hz.down10), "down", `chance of +10% or better: ${p0(hz.up10)}`],
  ].map(([k, val, c, sub]) => `<div><dt>${k}</dt><dd class="${c}">${val}<small>${sub}</small></dd></div>`).join("");
}
$$("#mc-view button").forEach((b) => b.addEventListener("click", () => { state.mcView = b.dataset.mcv; loadMC(); }));
$$("#mc-range button").forEach((b) => b.addEventListener("click", () => { state.mcH = +b.dataset.mc; $$("#mc-range button").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); loadMC(); }));

/* ================= returns over any timeframe (squares + histogram) ================= */
const TF_NAME = { 1: "minute", 5: "5 minutes", 15: "15 minutes", 60: "hour", D: "day" };
const londonFmt = new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/London", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
function londonParts(iso) {
  const f = Object.fromEntries(londonFmt.formatToParts(new Date(iso)).map((x) => [x.type, x.value]));
  return { day: `${f.year}-${f.month}-${f.day}`, mins: +f.hour * 60 + +f.minute };
}
// returns per bucket of tf minutes (London time) from the unit prices; "D": per day. Each
// bucket runs from the end of the one before (its first: from its own first reading).
function buckets(a, tf) {
  if (tf === "D") {
    const sd = a.series_day || [];
    if (sd.length >= 3) return sd.slice(1).map((p, i) => ({ day: p[0], label: dShort(p[0]), r: p[1] / sd[i][1] - 1, first: false }));
    tf = 1440;   // too few daily closes: each day from its readings
  }
  const out = []; let cur = null, prevEnd = null, prevDay = null;
  for (const [t, u] of a.series_min || []) {
    const { day, mins } = londonParts(t), slot = Math.floor(mins / tf) * tf, key = `${day} ${slot}`;
    if (!cur || cur.key !== key) {
      if (cur) { out.push(cur); prevEnd = cur.end; prevDay = cur.day; }
      cur = { key, day, slot, start: prevEnd ?? u, end: u, first: prevDay !== null && prevDay !== day };
    }
    cur.end = u;
  }
  if (cur) out.push(cur);
  const hm = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  return out.map((b) => ({ day: b.day, label: tf >= 1440 ? dShort(b.day) : hm(b.slot), r: b.end / b.start - 1, first: b.first }));
}
function drawSquares() {
  const { a, h, P } = state.quant || {}; if (!a) return;
  const tf = state.calTf;
  $$("#cal-tf button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.tf === tf)));
  setTitle("#calendar", tf === "D" ? "Every trading day" : `Every ${TF_NAME[tf]}`);
  const daily = tf === "D" && !h.early && P.length >= 2;
  if (daily) {   // the classic calendar: one square per trading day
    const days = P.slice(1).map((p, i) => ({ t: p.t, r: (1 + p.pie) / (1 + P[i].pie) - 1 }));
    const start = parseDay(days[0].t); start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
    const byDay = Object.fromEntries(days.map((d) => [d.t, d.r])), cells = [], months = [];
    const end = parseDay(days.at(-1).t), span = Math.max(...days.map((d) => Math.abs(d.r)), 0.01) * 0.75;
    for (let d = new Date(start), w = 0; d <= end; d.setDate(d.getDate() + 7), w++) {
      months.push(d.getDate() <= 7 || w === 0 ? MON[d.getMonth()] : "");
      for (let k = 0; k < 5; k++) { const x = new Date(d); x.setDate(d.getDate() + k); const key = `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`, r = byDay[key];
        cells.push(r == null ? "<i></i>" : `<i data-r="${r}" data-d="${key}" style="background:${heat(r, span)}"></i>`); }
    }
    const ups = days.filter((d) => d.r > 0).length, base = `${ups} up days, ${days.length - ups} down`;
    $("#cal-meta").textContent = base;
    const g = $("#calendar");
    g.innerHTML = `<div class="cal-months">${months.map((m) => `<span>${m}</span>`).join("")}</div><div class="cal-body"><div class="cal-days"><span>Mon</span><span></span><span>Wed</span><span></span><span>Fri</span></div><div class="cal-grid">${cells.join("")}</div></div>`;
    g.onmouseover = (e) => { const c = e.target.closest("[data-r]"); $("#cal-meta").innerHTML = c ? `${dLong(c.dataset.d)}  <b class="${cls(+c.dataset.r)}">${pct2(+c.dataset.r)}</b>` : base; };
    g.onmouseleave = () => { $("#cal-meta").textContent = base; };
    return;
  }
  const B = buckets(a, tf === "D" ? "D" : +tf);
  if (!B.length) { $("#cal-meta").textContent = ""; $("#calendar").innerHTML = `<p class="empty">No readings to show yet.</p>`; return; }
  const span = Math.max(...B.map((b) => Math.abs(b.r)), 0.0005) * 0.75, ups = B.filter((b) => b.r > 0).length;
  const base = `${ups} up, ${B.length - ups} down`;
  const rows = tf === "D" ? { "": B } : B.reduce((m, b) => ((m[b.day] ||= []).push(b), m), {});
  $("#cal-meta").textContent = base;
  const g = $("#calendar");
  g.innerHTML = Object.entries(rows).map(([d, bs]) => `<div class="blk-row"><span>${d ? dShort(d) : ""}</span><div class="blk">${bs.map((b) => `<i data-r="${b.r}" data-l="${esc(`${dShort(b.day)}${tf === "D" ? "" : ` ${b.label}`}`)}" style="background:${heat(b.r, span)}"><small>${b.label}</small></i>`).join("")}</div></div>`).join("");
  g.onmouseover = (e) => { const c = e.target.closest("[data-r]"); $("#cal-meta").innerHTML = c ? `${esc(c.dataset.l)}  <b class="${cls(+c.dataset.r)}">${pct2(+c.dataset.r)}</b>` : base; };
  g.onmouseleave = () => { $("#cal-meta").textContent = base; };
}
function drawHist() {
  const { a } = state.quant || {}; if (!a) return;
  const tf = state.histTf;
  $$("#hist-tf button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.tf === tf)));
  setTitle("#hist-meta", tf === "D" ? "Daily returns" : tf === "1" ? "Minute returns" : tf === "60" ? "Hourly returns" : `${tf}-minute returns`);
  // returns over the chosen period; the first of each day is left out (it spans the night)
  const rs = buckets(a, tf === "D" ? "D" : +tf).filter((b) => tf === "D" || !b.first).map((b) => b.r);
  if (rs.length < 3) { $("#hist-meta").textContent = ""; chartNote("hist-chart", "Not enough readings for this period yet."); return; }
  chartNote("hist-chart", null);
  const n = rs.length, mu = rs.reduce((x, y) => x + y, 0) / n, sd = Math.sqrt(rs.reduce((x, y) => x + (y - mu) ** 2, 0) / (n - 1)) || 1e-9;
  const srt = [...rs].sort((x, y) => x - y), q = (p) => srt[Math.min(n - 1, Math.max(0, Math.round(p * (n - 1))))];
  // bins centred on zero, a round width (the middle one, e.g. -0.01% to +0.01%, is grey)
  const raw = Math.max(q(0.98) - q(0.02), 1e-6) / 14, p10 = 10 ** Math.floor(Math.log10(raw)), w = [1, 2, 2.5, 5, 10].map((m) => m * p10).find((x) => x >= raw);
  const kLo = Math.round(q(0.02) / w) - 1, kHi = Math.round(q(0.98) / w) + 1, ks = [];
  for (let k = Math.min(kLo, 0); k <= Math.max(kHi, 0); k++) ks.push(k);
  const counts = ks.map(() => 0);
  rs.forEach((r) => { const k = Math.min(Math.max(Math.round(r / w), ks[0]), ks.at(-1)); counts[k - ks[0]]++; });
  const kurt = rs.reduce((x, y) => x + ((y - mu) / sd) ** 4, 0) / n - 3;
  $("#hist-meta").innerHTML = `${n.toLocaleString("en-GB")} returns · fitted normal curve: average ${(mu * 100).toFixed(3)}%, spread ${(sd * 100).toFixed(3)}% · ${Math.abs(kurt) < 1 ? "close to normal" : kurt > 0 ? "fatter tails than normal" : "thinner tails than normal"}`;
  const dec = Math.min(3, (String(+(w * 100).toPrecision(6)).split(".")[1] || "").length), grey = "rgba(237,235,230,.42)";
  const fmt = (x) => (Math.abs(x) < 1e-12 ? "0%" : `${x > 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(dec)}%`);
  chart("hist-chart", { data: { labels: ks.map((k) => fmt(k * w)), datasets: [
    { type: "line", label: "Fitted normal curve", data: ks.map((k) => n * (Phi(((k + 0.5) * w - mu) / sd) - Phi(((k - 0.5) * w - mu) / sd))), borderColor: COL.fg, borderWidth: 1.5, borderDash: [4, 4], pointRadius: 0, tension: 0.4, order: 0 },
    { type: "bar", label: "Returns", data: counts, backgroundColor: ks.map((k) => (k === 0 ? grey : k > 0 ? COL.up : COL.down)), borderRadius: 3, barPercentage: 0.94, categoryPercentage: 1, order: 1 }] },
    options: barOpts({ plugins: { legend: { display: false }, tooltip: { mode: "index", intersect: false, callbacks: { title: (it) => `${fmt((ks[it[0].dataIndex] - 0.5) * w)} to ${fmt((ks[it[0].dataIndex] + 0.5) * w)}`, label: (it) => (it.dataset.type === "bar" ? ` ${it.raw} returns` : ` ${it.raw.toFixed(1)} on the fitted curve`) } } },
      scales: { x: { grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 7 } }, y: { grid: { color: COL.hair }, border: { display: false }, ticks: { precision: 0 } } } }) });
}
$$("#cal-tf button").forEach((b) => b.addEventListener("click", () => { state.calTf = b.dataset.tf; drawSquares(); }));
$$("#hist-tf button").forEach((b) => b.addEventListener("click", () => { state.histTf = b.dataset.tf; drawHist(); }));

/* ================= warnings for estimates from too little data ================= */
const RELIABLE_DAYS = 63;  // about three months of trading days
function thinWhy(a) {
  if (!a?.ready) return null;
  if (a.readings) return `Estimated from only ${a.readings.readings.toLocaleString("en-GB")} minute readings since ${timeF.format(new Date(a.readings.from))} on ${dShort(a.readings.from)}, scaled up to a year. That's far too little data for this to be reliable: it can swing wildly from one minute to the next. It settles down once there are a few months of daily closes.`;
  if (a.n_days < RELIABLE_DAYS) return `Estimated from only ${a.n_days} trading days. Figures like this need about three months (${RELIABLE_DAYS} trading days) of daily data before they mean much, so treat it as a rough early read.`;
  return null;
}
const warn = (why) => (why ? ` <span class="warn" tabindex="0" title="${esc(why)}" aria-label="Not enough data: ${esc(why)}">⚠️</span>` : "");
const setTitle = (innerSel, text) => { const h = $(innerSel)?.closest("section")?.querySelector("h2"); if (h) h.textContent = text; };

/* ================= quant ================= */
// a short note in place of a chart that has nothing to draw yet
function chartNote(id, text) {
  const c = document.getElementById(id), box = c.parentElement;
  let n = box.querySelector(".chart-note");
  if (!text) { c.hidden = false; n?.remove(); return false; }
  c.hidden = true;
  if (!n) { n = document.createElement("p"); n.className = "empty chart-note"; box.append(n); }
  n.textContent = text;
  return true;
}
const Phi = (z) => { const t = 1 / (1 + 0.2316419 * Math.abs(z)), d = 0.3989423 * Math.exp(-z * z / 2), p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274)))); return z > 0 ? 1 - p : p; };
async function loadQuant() {
  const [a, st, reb, h, corr] = await Promise.all([data.analysis(), data.stats(), data.rebalances(), data.history("ALL"), data.corr().catch(() => null)]);
  const wait = $("#quant-wait"), rd = a.readings, u = rd ? "minute" : "day";
  wait.hidden = !!a.ready && !rd;
  $$("#view-quant [data-stats]").forEach((el) => (el.hidden = !a.ready));
  if (!a.ready) { wait.innerHTML = `<h2>Not enough history yet</h2><p class="sub">The statistics need at least a week of daily values. The volatility surface below works already.</p>`; return; }
  if (rd) wait.innerHTML = `<p class="sub"><b>Early figures.</b> Worked out from this pie's own value every minute since ${timeF.format(new Date(rd.from))} on ${dShort(rd.from)} (${rd.readings.toLocaleString("en-GB")} readings), scaled up to a year, so they'll swing a lot at first. The comparisons with the S&P use its minute prices too. From the fifth daily close everything switches to daily closes.</p>`;
  const s = state.summary, bk = benchKey(s), v = a.vs?.[bk] || {}, bn = shortBench(v.name), W_ = warn(thinWhy(a));
  const R = a.returns, K = a.risk, Q = a.ratios, S = a.significance, M = a.moves || {}, P = h.points || [];
  $("#quant-stats").innerHTML = [
    ["Sharpe ratio" + W_, n2(Q.sharpe), "", `${p0(S.prob_sharpe_positive)} likely above zero`], ["Sortino ratio" + W_, n2(Q.sortino), "", "return per unit of downside"],
    ["Volatility" + W_, pp(K.volatility), "", `a year, from ${u}-by-${u} moves`], ["Worst fall", pct(K.max_drawdown), "down", !K.max_dd_peak ? "no fall yet" : rd ? `${timeF.format(new Date(K.max_dd_peak))} to ${timeF.format(new Date(K.max_dd_trough))}` : `${dShort(K.max_dd_peak)} to ${dShort(K.max_dd_trough)}`],
    [`Beta to the ${esc(bn)}${W_}`, n2(v.beta), "", `correlation ${n2(v.correlation)}`], ["Alpha" + W_, pct(v.alpha_annual), cls(v.alpha_annual), v.alpha_p < 0.05 ? "a year, statistically significant" : `a year, not yet significant (p ${n2(v.alpha_p)})`],
  ].map(([k, val, c, sub]) => `<div><dt>${k}</dt><dd class="${c}">${val}<small>${sub}</small></dd></div>`).join("");

  // the squares and the returns histogram: any timeframe, from the raw readings
  state.quant = { a, h, P };
  if (state.calTf == null) state.calTf = rd ? "15" : "D";
  if (state.histTf == null) state.histTf = rd ? "1" : "D";
  drawSquares(); drawHist();
  chart("dd-chart", { type: "line", data: { datasets: [{ label: "Below the high", data: (st.drawdown || []).map((p) => ({ x: tms(p.t), y: p.dd })), borderColor: COL.down, backgroundColor: areaFill(COL.down, "55"), fill: "origin", borderWidth: 1.6, pointRadius: 0 }] }, options: timeOpts(pct) });
  setTitle("#roll-meta", rd ? "Rolling 20-minute return" : "Rolling 20-day return");
  const roll = a.rolling20 || [];
  chartNote("roll-chart", roll.length ? null : rd ? "Needs 20 minutes of readings." : "Needs 20 trading days of daily closes.");
  $("#roll-meta").textContent = roll.length ? `ahead of the ${bn} ${p0(roll.filter((p) => p.bench != null && p.bot > p.bench).length / roll.length)} of the time` : "";
  chart("roll-chart", { type: "line", data: { datasets: [
    { label: "The bot", data: roll.map((p) => ({ x: tms(p.t), y: p.bot })), borderColor: COL.amber, borderWidth: 1.9, pointRadius: 0 },
    { label: v.name || "Benchmark", data: roll.filter((p) => p.bench != null).map((p) => ({ x: tms(p.t), y: p.bench })), borderColor: COL.blue, borderWidth: 1.4, pointRadius: 0 }] }, options: timeOpts(pct) });

  // how the holdings move together
  const cg = $("#corr");
  if (corr?.symbols?.length > 1) {
    const n = corr.symbols.length, cc = (r) => (r == null ? "rgba(237,235,230,.05)" : r >= 0 ? `rgba(255,176,32,${(0.06 + 0.9 * r).toFixed(2)})` : `rgba(92,200,255,${(0.06 + 0.9 * -r).toFixed(2)})`);
    $("#corr-meta").textContent = `average ${n2(corr.average)}`;
    cg.innerHTML = `<div class="corr-grid" style="grid-template-columns: 40px repeat(${n}, minmax(0, 1fr))"><span></span>${corr.symbols.map((x, j) => `<span class="top" data-c="${j}">${esc(x)}</span>`).join("")}
      ${corr.matrix.map((row, i) => `<span data-r="${i}">${esc(corr.symbols[i])}</span>` + row.map((r, j) => `<i data-i="${i}" data-j="${j}" style="background:${i === j ? "rgba(237,235,230,.16)" : cc(r)}"></i>`).join("")).join("")}</div>`;
    const g = $(".corr-grid", cg), base = `average ${n2(corr.average)}`;
    g.addEventListener("mouseover", (e) => {
      const c = e.target.closest("i[data-i]"); $$(".hot", g).forEach((x) => x.classList.remove("hot"));
      if (!c) { $("#corr-meta").textContent = base; return; }
      const i = +c.dataset.i, j = +c.dataset.j, r = corr.matrix[i][j];
      $(`[data-r="${i}"]`, g).classList.add("hot"); $(`[data-c="${j}"]`, g).classList.add("hot");
      $("#corr-meta").innerHTML = i === j ? esc(corr.names[i]) : `${esc(corr.symbols[i])} and ${esc(corr.symbols[j])}  <b>${r == null ? "too little overlap" : n2s(r)}</b>`;
    });
    g.addEventListener("mouseleave", () => { $$(".hot", g).forEach((x) => x.classList.remove("hot")); $("#corr-meta").textContent = base; });
  } else cg.innerHTML = `<p class="empty">Needs about three weeks of daily prices for each holding.</p>`;

  // month by month, bot against the benchmark
  const ends = {}; P.forEach((p) => { ends[p.t.slice(0, 7)] = p; });
  const mk = Object.keys(ends).sort(), mret = (get_) => mk.map((m, i) => { const now = get_(ends[m]), prev = i ? get_(ends[mk[i - 1]]) : 0; return bad(now) || bad(prev) ? null : (1 + now) / (1 + prev) - 1; });
  let mb = mret((p) => p.pie), ms = mret((p) => p.bench?.[bk]);
  if (h.early || mk.length <= 1) { const sm = state.summary; if (!mk.length && sm?.as_of) mk.push(new Date(sm.as_of).toISOString().slice(0, 7)); mb = [sm?.returns?.ALL ?? null]; ms = [sm?.benchmarks?.[bk]?.since_start ?? null]; }
  if (h.early) mk.splice(0, mk.length, new Date(state.summary.as_of).toISOString().slice(0, 7));
  $("#month-meta").innerHTML = mk.length ? `${MON[+mk.at(-1).slice(5) - 1]}: bot <b class="${cls(mb.at(-1))}">${pct2(mb.at(-1))}</b>, ${esc(bn)} <b class="${cls(ms.at(-1))}">${pct2(ms.at(-1))}</b>` : `bot against the ${esc(bn)}`;
  chart("month-chart", { type: "bar", data: { labels: mk.map((m) => `${MON[+m.slice(5) - 1]}`), datasets: [
    { label: "The bot", data: mb, backgroundColor: COL.amber, borderRadius: 4, maxBarThickness: 26 }, { label: v.name || "Benchmark", data: ms, backgroundColor: COL.blue, borderRadius: 4, maxBarThickness: 26 }] },
    options: barOpts({ plugins: { legend: { display: false }, tooltip: { mode: "index", intersect: false, callbacks: { label: (it) => ` ${it.dataset.label}: ${pct2(it.raw)}` } } },
      scales: { x: { grid: { display: false }, border: { display: false } }, y: { grid: { color: COL.hair }, border: { display: false }, ticks: { callback: (x) => (Math.max(...mb.concat(ms).map((z) => Math.abs(z || 0))) < 0.01 ? pct2(x) : pct(x)), maxTicksLimit: 5 } } } }) });

  // the bot's moves: click a bar to see what changed
  const W = M.windows || [], moves = [...reb].reverse();   // oldest first
  chartNote("moves-chart", W.length ? null : rd ? "Each move's result shows here once the pie has daily closes." : "Each move's result shows here once the bot makes its next move.");
  const wBy = Object.fromEntries(W.map((w) => [w.from, w]));
  if (state.move == null || state.move >= moves.length) state.move = moves.length - 1;
  const showMove = (i) => {
    state.move = i;
    const r = moves[i]; if (!r) { $("#move-detail").innerHTML = `<p class="empty">The bot hasn't changed its targets yet.</p>`; return; }
    const day = r.ts.slice(0, 10), w = wBy[r.ts] || wBy[day], ch = [...(r.changes || [])].sort((x, y) => Math.abs(y.delta) - Math.abs(x.delta));
    $("#move-title").textContent = `Move on ${dShort(day)}`;
    $("#move-meta").innerHTML = w ? `bot <b class="${cls(w.bot)}">${pct(w.bot)}</b>, ${esc(bn)} ${pct(w.bench)}` : !bad(r.return_since) ? `<b class="${cls(r.return_since)}">${pct(r.return_since)}</b> since` : "";
    $("#move-detail").innerHTML = `<div class="changes">${ch.slice(0, 9).map((x) => `<a class="change" data-tk="${esc(x.ticker)}" href="${stockHref(x.ticker)}"><span class="tk">${esc(x.symbol)}</span><span class="${x.kind === "added" ? "up" : x.kind === "removed" ? "down" : "muted"}">${x.kind === "added" ? "bought in" : x.kind === "removed" ? "sold out" : x.delta > 0 ? "raised" : "trimmed"}</span><span class="ar">${pp(x.before)} → ${pp(x.after)}</span></a>`).join("") || `<p class="empty">No target changes recorded.</p>`}</div>
      ${moveVerdict(r)}${moveNews(r)}
      <div class="move-nav"><button type="button" data-step="-1" ${i <= 0 ? "disabled" : ""}>‹ Earlier</button><button type="button" data-step="1" ${i >= moves.length - 1 ? "disabled" : ""}>Later ›</button></div>`;
    $$("#move-detail [data-step]").forEach((b) => b.addEventListener("click", () => { showMove(state.move + +b.dataset.step); paint(); }));
  };
  const wIdx = W.map((w) => moves.findIndex((r) => r.ts === w.from || r.ts.slice(0, 10) === w.from));
  const dim = (col, k) => (wIdx[k] === state.move ? col : col + "59");
  const paint = () => { const c = state.charts["moves-chart"]; if (!c) return; c.data.datasets[0].backgroundColor = W.map((_, k) => dim(COL.amber, k)); c.data.datasets[1].backgroundColor = W.map((_, k) => dim(COL.blue, k)); c.update("none"); };
  chart("moves-chart", { type: "bar", data: { labels: W.map((w) => (w.from.length > 10 ? `${dShort(w.from)} ${timeF.format(new Date(w.from))}` : dShort(w.from))), datasets: [
    { label: "The bot", data: W.map((w) => w.bot), backgroundColor: W.map((_, k) => dim(COL.amber, k)), borderRadius: 3, maxBarThickness: 16 },
    { label: v.name || "Benchmark", data: W.map((w) => w.bench), backgroundColor: W.map((_, k) => dim(COL.blue, k)), borderRadius: 3, maxBarThickness: 16 }] },
    options: barOpts({ interaction: { mode: "index", intersect: false }, onHover: (e, els) => { e.native.target.style.cursor = els.length ? "pointer" : ""; },
      onClick: (e, els) => { if (els[0] && wIdx[els[0].index] >= 0) { showMove(wIdx[els[0].index]); paint(); } },
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (it) => ` ${it.dataset.label}: ${pct(it.raw)}` } } },
      scales: { x: { grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkip: true } }, y: { grid: { color: COL.hair }, border: { display: false }, ticks: { callback: (x) => pct(x), maxTicksLimit: 5 } } } }) });
  showMove(state.move);

  $("#risk-meta").textContent = rd ? `${a.n_days.toLocaleString("en-GB")} minute readings` : `${a.n_days} trading days`;
  $("#risk-kv").innerHTML = [["Total return", pct(R.total), cls(R.total)], ["A year at this pace" + W_, pct(R.cagr), cls(R.cagr)], [`Up ${u}s`, p0(R.pct_up_days)], [`Average up ${u}`, pct2(R.avg_up_day), "up"],
    [`Average down ${u}`, pct2(R.avg_down_day), "down"], [`Best ${u}`, `${pct2(R.best_day)}${!rd && st.best_day ? `, ${dShort(st.best_day.date)}` : ""}`, "up"], [`Worst ${u}`, `${pct2(R.worst_day)}${!rd && st.worst_day ? `, ${dShort(st.worst_day.date)}` : ""}`, "down"], [`Bad ${u}, 1 in 20${W_}`, pct2(-K.var95_daily), "down"],
    ["Average of the worst 5%" + W_, pct2(-K.cvar95_daily), "down"], ["Longest time below a high", `${K.longest_underwater_days} ${u}s`], ["Skew" + W_, n2s(K.skew)], ["Excess kurtosis" + W_, n2s(K.excess_kurtosis)],
    ["Calmar ratio" + W_, n2(Q.calmar)], ["Omega ratio" + W_, n2(Q.omega)], ["Tracking error" + W_, pp(v.tracking_error)], ["Information ratio" + W_, n2(v.information_ratio)],
    [`Share of ${esc(bn)} gains caught${W_}`, p0(v.up_capture)], [`Share of ${esc(bn)} falls taken${W_}`, p0(v.down_capture)], ["Moves that made money", p0(M.win_rate)], ["Moves that beat the old mix", (() => { const j = reb.filter((r) => r.judged); return j.length ? `${j.filter((r) => r.judged.value_added > 0).length} of ${j.length}` : "—"; })()], [`Moves that beat the ${esc(bn)}`, p0(M.beat_bench_rate)],
    ["Sharpe, 95% range" + W_, S.sharpe_ci95 ? `${n2(S.sharpe_ci95[0])} to ${n2(S.sharpe_ci95[1])}` : "—"], ["Days needed to be sure" + W_, Math.round(S.min_track_record_days).toLocaleString("en-GB")], ["Months ahead of it", `${v.months_beaten} of ${v.months}`], ["Trading days between moves", n2(M.avg_days_between)],
  ].map(([k, val, c]) => `<div><dt>${k}</dt><dd class="${c || ""}">${val}</dd></div>`).join("");
}

/* volatility surface, with a smile and term structure beside it */
const surfaces = {};
// option surfaces: fetched again every 10 minutes, so a surface showing the last known prices
// (live ones unavailable) switches to live data on its own once there is some
const VOL_TTL = 10 * 60e3, volAt = {};
function volData(t) {
  const k = `vol-${t}`;
  if (cache[k] && Date.now() - (volAt[t] || 0) > VOL_TTL) delete cache[k];
  if (!cache[k]) volAt[t] = Date.now();
  return load(k, `/api/volsurface/${encodeURIComponent(t)}`);
}
const volSig = (d) => (d ? `${d.available}|${d.as_of}|${!!d.stale}` : "none");
// a note on a surface that's showing the last known prices
function staleNote(host, d) {
  host.querySelector(".stale-note")?.remove();
  if (!d?.stale) return;
  const when = d.as_of ? `${dShort(d.as_of.slice(0, 10))} at ${timeF.format(new Date(d.as_of))}` : "earlier";
  const n = document.createElement("p");
  n.className = "stale-note";
  n.title = "Yahoo has no live option prices right now. The surface keeps the last ones it had and updates on its own when live prices are back (checked every 10 minutes).";
  n.textContent = d.pie ? `${d.stale_count} stock${d.stale_count === 1 ? "" : "s"} on last known prices (oldest ${when})` : `Last known option prices, from ${when}`;
  host.append(n);
}
async function loadVol() {
  const hs = (state.empty ? state.holdingsData?.holdings || [] : held()).filter((h) => /_US_EQ$/.test(h.ticker)).sort((a, b) => (b.weight || 0) - (a.weight || 0));
  const pick = $("#vol-pick");
  if (!hs.length) { $("#vol-surface").innerHTML = `<p class="empty">No US-listed stocks to show.</p>`; return; }
  // the whole pie first (its US holdings' surfaces combined), then each stock
  if (state.volTicker !== "PIE" && !hs.some((h) => h.ticker === state.volTicker)) state.volTicker = "PIE";
  if (pick.options.length !== hs.length + 1) pick.innerHTML = `<option value="PIE"${state.volTicker === "PIE" ? " selected" : ""}>The whole pie</option>` + hs.map((h) => `<option value="${esc(h.ticker)}"${h.ticker === state.volTicker ? " selected" : ""}>${esc(h.symbol)}  ${esc(h.name)}</option>`).join("");
  pick.onchange = () => { state.volTicker = pick.value; drawVol(); };
  let d = null; try { d = await volData(state.volTicker); } catch { /* drawn as unavailable */ }
  if (state.volDrawn !== state.volTicker || !surfaces["vol-surface"] || state.volSig !== volSig(d)) await drawVol();
}
async function drawVol() {
  const t = state.volTicker, host = $("#vol-surface");
  state.volDrawn = t;
  surfaces["vol-surface"]?.destroy(); surfaces["vol-surface"] = null;
  host.innerHTML = `<p class="empty">Loading option prices…</p>`;
  let d; try { d = await volData(t); } catch { d = { available: false }; }
  if (state.volTicker !== t) return;
  state.volSig = volSig(d);
  $("#view-quant").classList.toggle("no-vol", !d.available);
  if (!d.available || !window.VolSurface) {
    host.innerHTML = `<p class="empty">No option prices available for ${t === "PIE" ? "the pie's stocks" : esc(sym(t))} right now.</p>`; $("#vol-expiries").innerHTML = ""; $("#smile-meta").textContent = "";
    ["smile-chart", "term-chart"].forEach((id) => { state.charts[id]?.destroy(); delete state.charts[id]; });
    return;
  }
  const { M, E, Z } = VolSurface.prep(d), atm = M.findIndex((m) => Math.abs(m - 1) < 1e-6);
  let cur = E.reduce((b, e, i) => (Math.abs(e.days - 45) < Math.abs(E[b].days - 45) ? i : b), 0);
  const s3 = (surfaces["vol-surface"] = VolSurface.render(host, d, { highlight: cur, onPick: (days) => select(E.findIndex((e) => e.days === days)) }));
  staleNote(host, d);
  const lineOpts = (xFmt, yTitle) => ({ responsive: true, maintainAspectRatio: false, animation: false, parsing: false, interaction: { mode: "nearest", axis: "x", intersect: false }, plugins: { legend: { display: false }, tooltip: { callbacks: { title: (it) => xFmt(it[0].raw.x), label: (it) => ` ${p0(it.raw.y)} implied vol` } } },
    scales: { x: { type: "linear", grid: { display: false }, border: { display: false }, ticks: { callback: xFmt, maxTicksLimit: 6 }, title: { display: true, text: yTitle, color: COL.faint } }, y: { grid: { color: COL.hair }, border: { display: false }, ticks: { callback: (v) => p0(v), maxTicksLimit: 5 } } } });
  const smileOpts = lineOpts((x) => `${Math.round(x * 100)}%`, "strike, % of price now");
  Object.assign(smileOpts.scales.x, { min: M[0], max: M.at(-1) });
  function select(i) {
    if (i < 0) return; cur = i;
    $$("#vol-expiries .chip").forEach((c, k) => c.setAttribute("aria-pressed", String(k === i)));
    s3.highlight(E[i].days);
    const skew = Z[i][M.findIndex((m) => Math.abs(m - 0.9) < 1e-6)] - Z[i][M.findIndex((m) => Math.abs(m - 1.1) < 1e-6)];
    $("#smile-meta").innerHTML = `at the money <b class="amber">${p0(Z[i][atm])}</b>, skew ${bad(skew) ? "—" : pts(skew)}`;
    chart("smile-chart", { type: "line", data: { datasets: [
      ...Z.map((row, k) => (k === i ? null : { label: VolSurface.dayLabel(E[k].days), data: row.map((v, j) => ({ x: M[j], y: v })), borderColor: "rgba(237,235,230,.13)", borderWidth: 1, pointRadius: 0, pointHoverRadius: 0, tension: 0.35 })).filter(Boolean),
      { label: VolSurface.dayLabel(E[i].days), data: Z[i].map((v, j) => ({ x: M[j], y: v })), borderColor: COL.amber, backgroundColor: areaFill(COL.amber, "30"), fill: "start", borderWidth: 2.4, pointRadius: 0, pointHoverRadius: 4, tension: 0.35 }] },
      options: { ...smileOpts, plugins: { legend: { display: false }, tooltip: { filter: (it) => it.datasetIndex === Z.length - 1, callbacks: { title: (it) => `strike ${Math.round(it[0].raw.x * 100)}% of price`, label: (it) => ` ${p0(it.raw.y)} implied vol` } } } } });
    const term = state.charts["term-chart"]; if (term) { term.data.datasets[0].pointBackgroundColor = E.map((_, k) => (k === i ? COL.amber : COL.bg)); term.data.datasets[0].pointRadius = E.map((_, k) => (k === i ? 6 : 4)); term.update("none"); }
  }
  $("#vol-expiries").innerHTML = E.map((e, i) => `<button class="chip" type="button" data-e="${i}" aria-pressed="false">${VolSurface.dayLabel(e.days)}</button>`).join("");
  $$("#vol-expiries .chip").forEach((c) => c.addEventListener("click", () => select(+c.dataset.e)));
  const o = lineOpts((x) => VolSurface.dayLabel(Math.round(x)), "time to expiry");
  o.onClick = (e, els) => { if (els[0]) select(els[0].index); }; o.onHover = (e, els) => { e.native.target.style.cursor = els.length ? "pointer" : ""; };
  chart("term-chart", { type: "line", data: { datasets: [{ label: "At the money", data: E.map((e, k) => ({ x: e.days, y: Z[k][atm] })), borderColor: COL.fg, borderWidth: 2, pointBackgroundColor: COL.bg, pointBorderColor: COL.fg, pointBorderWidth: 2, pointRadius: 4, pointHoverRadius: 6, tension: 0.3 }] }, options: o });
  select(cur);
}

/* ================= stock ================= */
function gauge(score) {
  const cx = 110, cy = 104, r = 86, cols = [COL.up, "#9BE8C4", COL.amber, "#FF9E7A", COL.down];
  const pt = (t, rad = r) => [cx - rad * Math.cos(Math.PI * t), cy - rad * Math.sin(Math.PI * t)];
  const arcs = cols.map((c, k) => { const [x0, y0] = pt(k / 5 + 0.012), [x1, y1] = pt((k + 1) / 5 - 0.012); return `<path d="M${x0.toFixed(1)} ${y0.toFixed(1)} A${r} ${r} 0 0 1 ${x1.toFixed(1)} ${y1.toFixed(1)}" fill="none" stroke="${c}" stroke-width="12" stroke-linecap="round" opacity="${bad(score) ? 0.25 : 1}"></path>`; }).join("");
  const t = bad(score) ? null : clamp((score - 1) / 4, 0, 1), [nx, ny] = t == null ? [0, 0] : pt(t, r - 22);
  return `<svg class="gauge" viewBox="0 0 220 122" width="230" height="128" role="img" aria-label="${bad(score) ? "No analyst score" : `Analyst score ${n2(score)} on a scale from 1, strong buy, to 5, sell`}">${arcs}
    ${t == null ? "" : `<line x1="${cx}" y1="${cy}" x2="${nx.toFixed(1)}" y2="${ny.toFixed(1)}" stroke="#EDEBE6" stroke-width="3.5" stroke-linecap="round"></line><circle cx="${cx}" cy="${cy}" r="6.5" fill="#EDEBE6"></circle>`}
    <text x="14" y="120" fill="rgba(237,235,230,.45)" font-family="Inter Tight" font-size="11">buy</text><text x="206" y="120" text-anchor="end" fill="rgba(237,235,230,.45)" font-family="Inter Tight" font-size="11">sell</text></svg>`;
}
async function loadStock(ticker) {
  let d;
  try { d = await get(`/api/stock/${encodeURIComponent(ticker)}`); }
  catch { $("#s-title").innerHTML = `<b>${esc(sym(ticker))}</b><span>Not a stock the bot has held</span>`; return; }
  if (state.stock !== ticker) return;
  state.stockData = d;
  const p = d.profile || {}, pos = d.position || {}, ccy = p.currency, an = d.analyst;
  $("#s-title").innerHTML = `<b>${esc(d.symbol)}</b><span>${esc(d.name)}</span>${an?.rating ? pill(an.rating) : ""}`;
  $("#s-tags").textContent = [p.sector, p.industry, p.country].filter(Boolean).join(", ") || (d.held_now ? "" : "No longer in the pie");
  const price = p.currentPrice ?? p.regularMarketPrice ?? d.prices?.at(-1)?.close, prev = p.previousClose ?? d.prices?.at(-2)?.close, chg = price && prev ? price / prev - 1 : null;
  $("#s-price").innerHTML = price != null ? `<b>${money(price, ccy)}</b><span class="delta ${cls(chg)}">${arrow(chg)} ${pct2(chg)} today</span>` : "";
  const order = held(), ix = order.findIndex((h) => h.ticker === ticker), nb = (k) => order[(ix + k + order.length) % order.length];
  [["#s-prev", -1], ["#s-next", 1]].forEach(([id, k]) => { const el = $(id), h = ix < 0 ? null : nb(k); el.hidden = !h || order.length < 2; if (h) { el.href = stockHref(h.ticker); el.title = `${h.symbol}  ${h.name}`; } });
  const kv = (l, v, c = "") => `<div><dt>${l}</dt><dd class="${c}">${v}</dd></div>`;
  $("#s-position").innerHTML = [kv("Status", d.held_now ? "Held now" : "Sold out"), kv("Value", G(gbp(pos.value || 0))), kv("Weight, against target", d.held_now ? `${pp(pos.weight)} / ${pp(pos.target)}` : "—"), kv("Shares", pos.quantity ? G(pos.quantity.toFixed(4)) : "—"),
    kv("Profit now", G(gbpS(pos.unrealised_pl)), cls(pos.unrealised_pl)), kv("Profit, all time in the pie", G(gbpS(pos.lifetime_pl)), cls(pos.lifetime_pl)), kv("Bought, sold", G(`${gbp0(pos.bought_gbp || 0)}, ${gbp0(pos.sold_gbp || 0)}`)), kv("Dividends", G(gbp((d.dividends || []).reduce((a, x) => a + x.amount, 0))))].join("");
  const hasAn = !!(an && (an.rating || an.mean));
  $("#s-analyst-card").hidden = !hasAn;
  $("#s-position-card").classList.toggle("r2", !hasAn);
  if (hasAn) {
    $("#s-analyst-meta").textContent = an.analysts ? `${an.analysts} analyst${an.analysts === 1 ? "" : "s"}${an.thin ? ", thin coverage" : ""}` : "";
    let range = "";
    if (an.mean && an.price) {
      const lo = Math.min(an.low ?? an.mean, an.price), hi = Math.max(an.high ?? an.mean, an.price), at = (x) => clamp((x - lo) / (hi - lo || 1) * 100, 0, 100), edge = (x) => (at(x) < 14 ? " l" : at(x) > 86 ? " r" : "");
      range = `<div class="an-range"><span class="band" style="left:${at(an.low ?? an.mean)}%;width:${at(an.high ?? an.mean) - at(an.low ?? an.mean)}%"></span>
        <span class="mark${edge(an.mean)}" style="left:${at(an.mean)}%"><span>average target ${money(an.mean, ccy)}</span><i></i></span><span class="mark now${edge(an.price)}" style="left:${at(an.price)}%"><i></i><span>now ${money(an.price, ccy)}</span></span></div>
        <div class="an-ends"><span>lowest ${money(an.low, ccy)}</span><span>highest ${money(an.high, ccy)}</span></div>`;
    }
    $("#s-analysts").innerHTML = `${gauge(an.score)}<div class="an-top"><div>${pill(an.rating)}<p class="sub">${bad(an.score) ? "no consensus score published" : `score ${n2(an.score)}, from 1 (strong buy) to 5 (sell)`}</p></div>${bad(an.upside) ? "" : `<p class="big-num ${cls(an.upside)}">${p0s(an.upside)}</p>`}</div>${range}`;
  }
  const grp = (title, rows) => { const r = rows.filter(([, v]) => v != null && v !== "—"); return r.length ? `<div><h3>${title}</h3><dl class="kv one">${r.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl></div>` : ""; };
  const x = (v) => (bad(v) ? null : `${n2(v)}×`), pc = (v) => (bad(v) ? null : pp(v));
  $("#s-stats").innerHTML = [
    grp("Valuation", [["Market value", p.marketCap ? moneyC(p.marketCap, ccy) : null], ["Price / earnings", x(p.trailingPE)], ["Forward price / earnings", x(p.forwardPE)], ["Price / book", x(p.priceToBook)], ["EV / EBITDA", x(p.enterpriseToEbitda)]]),
    grp("Business", [["Revenue", p.totalRevenue ? moneyC(p.totalRevenue, ccy) : null], ["Revenue growth", pc(p.revenueGrowth)], ["Earnings growth", pc(p.earningsGrowth)], ["Profit margin", pc(p.profitMargins)], ["Return on equity", pc(p.returnOnEquity)]]),
    grp("Trading", [["52-week range", p.fiftyTwoWeekLow != null ? `${money(p.fiftyTwoWeekLow, ccy)} to ${money(p.fiftyTwoWeekHigh, ccy)}` : null], ["From the 52-week high", p.fiftyTwoWeekHigh && price ? pct(price / p.fiftyTwoWeekHigh - 1) : null], ["Beta", bad(p.beta) ? null : n2(p.beta)], ["Shares sold short", pc(p.shortPercentOfFloat)], ["Dividend yield", bad(p.dividendYield) ? null : pp(p.dividendYield / 100)]]),
  ].join("") || `<p class="empty">No company numbers available.</p>`;
  const hasNews = !!(d.news || []).length;
  $("#s-news-card").hidden = !hasNews;
  $("#s-trades-card").className = `card ${hasNews ? "s4" : "s8"}`;
  $("#s-news").innerHTML = (d.news || []).slice(0, 12).map((n) => headline({ ...n, tickers: [], symbols: [] })).join("");
  $("#s-trades").innerHTML = `<thead><tr><th class="l">Date</th><th class="l">Side</th><th>Shares</th><th>Amount</th></tr></thead><tbody>` +
    ((d.trades || []).slice().reverse().map((t) => `<tr><td class="l">${dShort(t.t.slice(0, 10))} ${t.t.slice(0, 4)}</td><td class="l ${t.side === "BUY" ? "up" : "down"}">${t.side === "BUY" ? "Bought" : "Sold"}</td><td>${G(t.quantity.toFixed(4))}</td><td>${G(gbp(t.gbp))}</td></tr>`).join("") || `<tr><td class="l muted" colspan="4">No trades recorded.</td></tr>`) + "</tbody>";
  drawStockChart(true);
  chart("s-weight", { type: "line", data: { datasets: [{ label: "Weight", data: (d.weight_history || []).filter((w) => w.weight != null).map((w) => ({ x: tms(w.t), y: w.weight })), borderColor: COL.amber, backgroundColor: areaFill(COL.amber), fill: "origin", stepped: true, borderWidth: 1.6, pointRadius: 0 }] }, options: timeOpts(pp) });
  // the surface card only shows when the stock has listed options; otherwise key numbers take the row
  const volCard = $("#s-vol-card"), statsCard = $("#s-stats-card");
  surfaces["s-surface"]?.destroy(); surfaces["s-surface"] = null;
  let vd = { available: false };
  if (/_US_EQ$/.test(ticker)) { volCard.hidden = false; statsCard.className = "card s4"; $("#s-stats").classList.remove("wide"); $("#s-surface").innerHTML = `<p class="empty">Loading option prices…</p>`; try { vd = await volData(ticker); } catch { /* shown as unavailable */ } }
  if (state.stock !== ticker) return;
  const ok = vd.available && window.VolSurface;
  volCard.hidden = !ok; statsCard.className = `card ${ok ? "s4" : "s12"}`; $("#s-stats").classList.toggle("wide", !ok);
  if (ok) { surfaces["s-surface"] = VolSurface.render($("#s-surface"), vd); staleNote($("#s-surface"), vd); $("#s-vol-meta").textContent = vd.stale ? `last known option prices, share price then ${money(vd.spot, "USD")}` : `from option prices, share price ${money(vd.spot, "USD")}`; }
}
// 1D: minute prices for the latest New York session, from the open
state.stockDay = {};
async function drawStockDay(animate) {
  const d = state.stockData, tk = d.ticker || state.stock;
  let x = state.stockDay[tk];
  if (!x || Date.now() - x.at > 120e3) {
    try { x = state.stockDay[tk] = { at: Date.now(), ...(await get(`/api/stock/${encodeURIComponent(tk)}/intraday`)) }; }
    catch { x = { points: [] }; }
  }
  if (state.srange !== 1 || state.stockData !== d) return;   // switched away while loading
  const ccy = d.profile?.currency, P = (x.points || []).map((p) => ({ x: new Date(p.t).getTime(), y: p.price }));
  if (P.length < 2) { $("#s-chart-meta").textContent = "No minute prices for today's session yet."; state.charts["s-chart"]?.destroy(); delete state.charts["s-chart"]; return; }
  const o = x.open ?? P[0].y, ch = P.at(-1).y / o - 1, col = ch >= 0 ? COL.up : COL.down;
  $("#s-chart-meta").innerHTML = `${dShort(x.session)} from the open: <b class="${cls(ch)}">${pct2(ch)}</b>${x.prev_close ? ` · ${pct2(P.at(-1).y / x.prev_close - 1)} on the close before` : ""}`;
  const t0 = P[0].x, t1 = P.at(-1).x;
  const marks = (side) => (d.trades || []).filter((t) => t.side === side).map((t) => new Date(t.t).getTime()).filter((v) => v >= t0 && v <= t1).map((v) => ({ x: v, y: P[nearest(P, v)].y }));
  const c = chart("s-chart", { type: "line", data: { datasets: [
    { label: "Price", data: P, borderColor: col, backgroundColor: areaFill(col, "30"), fill: "start", borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.12, order: 3, glow: true, tag: (v) => money(v, ccy) },
    { label: "Today's open", data: [{ x: t0, y: o }, { x: t1, y: o }], borderColor: COL.faint, borderDash: [4, 4], borderWidth: 1.2, pointRadius: 0, pointHoverRadius: 0, order: 4 },
    { label: "Bot bought", data: marks("BUY"), type: "scatter", pointStyle: "triangle", pointRadius: 6, pointHoverRadius: 8, backgroundColor: COL.up, borderColor: COL.bg, borderWidth: 1.5, order: 1 },
    { label: "Bot sold", data: marks("SELL"), type: "scatter", pointStyle: "triangle", rotation: 180, pointRadius: 6, pointHoverRadius: 8, backgroundColor: COL.down, borderColor: COL.bg, borderWidth: 1.5, order: 2 },
  ] }, options: { ...timeOpts((v) => money(v, ccy), { tags: true, tagPad: 86, intraday: true }), interaction: { mode: "nearest", axis: "x", intersect: false } } });
  if (animate && P.length > 2) reveal(c);
}
function drawStockChart(animate = false) {
  const d = state.stockData; if (!d) return;
  if (state.srange === 1) return drawStockDay(animate);
  const cutoff = Date.now() - state.srange * 864e5, fromPie = !d.prices?.length, prices = fromPie ? d.pie_prices || [] : d.prices;
  const all = prices.map((p) => ({ x: tms(p.t), y: p.close })), P = all.filter((p) => p.x >= cutoff), ccy = fromPie ? "GBP" : d.profile?.currency;
  const marks = (side) => (d.trades || []).filter((t) => t.side === side).map((t) => new Date(t.t).getTime()).filter((x) => P.length && x >= P[0].x - 864e5 && x <= P.at(-1).x + 864e5).map((x) => ({ x, y: all[nearest(all, x)].y }));
  $("#s-chart-meta").textContent = fromPie ? "£ per share, from the pie's own values" : "";
  const up = P.length > 1 && P.at(-1).y >= P[0].y, col = up ? COL.up : COL.down;
  const ch = chart("s-chart", { type: "line", data: { datasets: [
    { label: "Price", data: P, borderColor: col, backgroundColor: areaFill(col, "30"), fill: "start", borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.12, order: 3, glow: true, tag: (v) => money(v, ccy) },
    { label: "Bot bought", data: marks("BUY"), type: "scatter", pointStyle: "triangle", pointRadius: 6, pointHoverRadius: 8, backgroundColor: COL.up, borderColor: COL.bg, borderWidth: 1.5, order: 1 },
    { label: "Bot sold", data: marks("SELL"), type: "scatter", pointStyle: "triangle", rotation: 180, pointRadius: 6, pointHoverRadius: 8, backgroundColor: COL.down, borderColor: COL.bg, borderWidth: 1.5, order: 2 },
  ] }, options: { ...timeOpts((v) => money(v, ccy), { tags: true, tagPad: 86 }), interaction: { mode: "nearest", axis: "x", intersect: false } } });
  if (animate && P.length > 2) reveal(ch);
}

/* ================= hover preview for any ticker ================= */
let peekT;
function hidePeek() { clearTimeout(peekT); $("#peek").hidden = true; }
function showPeek(el) {
  const h = (state.holdingsData?.holdings || []).find((x) => x.ticker === el.dataset.tk); if (!h) return;
  const box = $("#peek"), a = h.analyst;
  box.innerHTML = `<div class="peek-h"><b>${esc(h.symbol)}</b><span class="${cls(h.day_pct)}">${pct2(h.day_pct)}</span></div><p>${esc(h.name)}</p>${spark((h.spark || []).slice(-30), 232, 46, null, true)}
    <dl><dt>Weight in the pie</dt><dd>${pp(h.weight)}</dd><dt>Value</dt><dd>${G(gbp(h.value))}</dd><dt>Return since bought</dt><dd class="${cls(h.pl_pct)}">${pct(h.pl_pct)}</dd>${a?.rating || !bad(a?.upside) ? `<dt>Analysts</dt><dd>${esc(a.rating || "No rating")}${bad(a.upside) ? "" : `, ${p0s(a.upside)}`}</dd>` : ""}</dl>`;
  box.hidden = false;
  const r = el.getBoundingClientRect(), bw = box.offsetWidth, bh = box.offsetHeight;
  box.style.left = `${clamp(r.left + r.width / 2 - bw / 2, 10, innerWidth - bw - 10)}px`;
  box.style.top = `${r.bottom + bh + 14 > innerHeight ? r.top - bh - 10 : r.bottom + 10}px`;
}
if (matchMedia("(hover: hover)").matches) {
  document.addEventListener("mouseover", (e) => { const el = e.target.closest("[data-tk]"); clearTimeout(peekT); if (!el || el.closest(".suggest")) { $("#peek").hidden = true; return; } peekT = setTimeout(() => showPeek(el), 220); });
  addEventListener("scroll", hidePeek, { passive: true });
  // the soft highlight that follows the pointer along a card's edge
  document.addEventListener("pointermove", (e) => { const c = e.target.closest?.(".card"); if (!c) return; const r = c.getBoundingClientRect(); c.style.setProperty("--mx", `${e.clientX - r.left}px`); c.style.setProperty("--my", `${e.clientY - r.top}px`); }, { passive: true });
}

/* ================= wiring ================= */
$$("[data-range]").forEach((b) => b.addEventListener("click", () => { state.range = b.dataset.range; $$("[data-range]").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); loadChart(true).catch(console.error); }));
$$("[data-srange]").forEach((b) => b.addEventListener("click", () => { state.srange = +b.dataset.srange; $$("[data-srange]").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); drawStockChart(); }));
function live() {
  let fallback = null, first = true, last = null;
  const again = async () => {
    Object.keys(cache).filter((k) => !k.startsWith("vol-")).forEach((k) => delete cache[k]);
    const before = state.summary?.value;
    await refresh();
    if (state.view === "overview" && before != null && state.summary?.value !== before) { const el = $("#hero-value"); el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash"); }
  };
  try {
    const es = new EventSource("/api/stream");
    es.addEventListener("snapshot", (e) => { if (first) { first = false; last = e.data; return; } if (e.data !== last) { last = e.data; again(); } });
    es.onerror = () => { if (!fallback) fallback = setInterval(again, 60000); };
    es.onopen = () => { if (fallback) { clearInterval(fallback); fallback = null; } };
  } catch { setInterval(again, 60000); }
}
setupCharts();
route();
live();

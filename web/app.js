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
};
const benchKey = (s) => Object.keys(s?.benchmarks || {})[0];
const shortBench = (n) => ({ "S&P 500": "S&P", "FTSE All-World": "World" }[n] || n || "market");
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
    if (v === "overview") await all([loadHero(), loadChart(), loadMap(), loadNews(), loadConsensus()]);
    if (v === "holdings") await all([loadHoldings()]);
    if (v === "quant") await all([loadQuant(), loadVol()]);
    if (v === "stock" && state.stock) await loadStock(state.stock);
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
  $("#hero-label").textContent = state.private ? `Return since ${dShort(s.inception)}` : "Value now";
  heroValue(s.value, s.returns?.ALL);
  $("#hero-sub").innerHTML = `<span class="delta ${cls(s.day_change_pct)}">${arrow(s.day_change_pct)} ${pct2(s.day_change_pct)} today</span>${G(gbpS(s.day_change))}<span>updated ${timeF.format(new Date(s.as_of))}</span>`;
}
function renderStats(p) {   // p: a past daily point while scrubbing the chart, or null for now
  const s = state.summary, a = state.analysis || {}, bk = benchKey(s), bs = s.benchmarks?.[bk];
  const ret = p ? p.pie : s.returns?.ALL, bench = p ? p.bench?.[bk] : bs?.since_start;
  const profit = p ? (p.deposits != null ? p.value - p.deposits : null) : s.profit;
  const lead = bad(ret) || bad(bench) ? null : ret - bench;
  const items = [
    state.private ? ["Return on money in", pct(s.return_on_deposits), cls(s.return_on_deposits), "profit against what was paid in"]
                  : ["Profit", G(gbpS(profit)), cls(profit), p ? "at that date" : `${pct(s.return_on_deposits)} on ${gbp0(s.net_deposits)} paid in`],
    ["Return", pct(ret), cls(ret), p ? "to that date" : `since ${dShort(s.inception)}, deposits excluded`],
    [`Against the ${esc(bs?.name || "market")}`, pts(lead), cls(lead), `${esc(shortBench(bs?.name))} ${pct(bench)} over the same days`],
    ...(a.ready ? [["Sharpe ratio", n2(a.ratios.sharpe), "", `${p0(a.significance.prob_sharpe_positive)} likely to be above zero`],
                   ["Worst fall", pct(a.risk.max_drawdown), "down", `now ${pct(a.risk.current_drawdown)} from the high`]] : []),
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
    const r = S.base ? p.value / S.base - 1 : null;
    $("#hero-label").textContent = `Today at ${timeF.format(new Date(p.t))}`;
    heroValue(p.value, r);
    $("#hero-sub").innerHTML = `<span class="delta ${cls(r)}">${arrow(r)} ${pct2(r)} since yesterday's close</span>`;
  } else {
    $("#hero-label").textContent = dLong(p.t);
    heroValue(p.value, p.pie);
    const prev = S.P[i - 1], day = prev ? (1 + p.pie) / (1 + prev.pie) - 1 : null;
    $("#hero-sub").innerHTML = bad(day) ? "" : `<span class="delta ${cls(day)}">${arrow(day)} ${pct2(day)} that day</span>`;
    renderStats(p);
  }
}
async function loadChart(animate = !state.chartShown) {
  const [h, reb] = await Promise.all([data.history(state.range), data.rebalances()]);
  const P = h.points || [], bc = [COL.blue, COL.grey];
  if (!state.bench.length && h.benchmarks?.length) state.bench = h.benchmarks.map((b, i) => ({ ...b, on: i === 0 }));
  const box = $("#toggles");
  box.innerHTML = `<span><i style="background:${COL.amber}"></i>The bot</span>` + (h.intraday ? `<span><i style="background:${COL.faint}"></i>Yesterday's close</span>`
    : state.bench.map((b, i) => `<button class="chip" data-bench="${esc(b.key)}" aria-pressed="${b.on}"><i style="background:${bc[i % 2]}"></i>${esc(b.name)}</button>`).join("") + `<span><i style="background:${COL.amber};width:7px;height:7px;border-radius:2px"></i>a move</span>`);
  $$("[data-bench]", box).forEach((el) => el.addEventListener("click", () => { const b = state.bench.find((x) => x.key === el.dataset.bench); b.on = !b.on; loadChart(false); }));
  let datasets, yFmt;
  state.series = { P, intraday: !!h.intraday, base: h.baseline || P[0]?.value };
  const thin = P.length < 2, note = $("#chart-empty");
  note.hidden = !thin;
  note.textContent = h.intraday ? "Today's line appears once the tracker has a few minutes of prices." : "The chart starts after the first full day with money in the pie.";
  if (thin) { state.charts["main-chart"]?.destroy(); delete state.charts["main-chart"]; return; }
  if (h.intraday) {
    const base = state.series.base, d = P.map((p) => ({ x: new Date(p.t).getTime(), y: base ? p.value / base - 1 : 0 }));
    yFmt = pct2;
    datasets = [{ label: "Today", data: d, borderColor: COL.amber, backgroundColor: areaFill(COL.amber), fill: "origin", borderWidth: 2.2, pointRadius: 0, pointHoverRadius: 0, tension: 0.2, glow: true, tag: pct2 }];
    if (d.length) datasets.push({ label: "Yesterday's close", data: [{ x: d[0].x, y: 0 }, { x: d.at(-1).x, y: 0 }], borderColor: COL.faint, borderDash: [4, 4], borderWidth: 1.2, pointRadius: 0, pointHoverRadius: 0, noScrub: true });
  } else {
    const main = P.map((p) => ({ x: tms(p.t), y: p.pie }));
    yFmt = pct;
    datasets = [{ label: "The bot", data: main, borderColor: COL.amber, backgroundColor: areaFill(COL.amber), fill: "origin", borderWidth: 2.4, pointRadius: 0, pointHoverRadius: 0, tension: 0.15, order: 1, glow: true, tag: pct }];
    state.bench.forEach((b, i) => { if (b.on) datasets.push({ label: b.name, data: P.filter((p) => p.bench?.[b.key] != null).map((p) => ({ x: tms(p.t), y: p.bench[b.key] })), borderColor: bc[i % 2], borderWidth: 1.6, pointRadius: 0, pointHoverRadius: 0, tension: 0.15, order: 2, tag: pct }); });
    const first = main[0]?.x ?? 0;
    datasets.push({ label: "Move", type: "scatter", order: 0, pointStyle: "rectRounded", pointRadius: 3.5, pointHoverRadius: 3.5, backgroundColor: COL.amber, borderColor: COL.bg, borderWidth: 1.5,
      data: reb.filter((r) => new Date(r.ts).getTime() >= first - 864e5).map((r) => { const x = new Date(r.ts).getTime(), pt = main[nearest(main, x)]; return { x, y: pt?.y }; }).filter((p) => p.y != null) });
  }
  const o = timeOpts(yFmt, { intraday: h.intraday, tags: true, tooltip: false });
  o.plugins.scrub = { onMove: onScrub };
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
  const rects = squarify(hs.map((h) => ({ item: h, v: Math.max(h.weight, 0.004) })).sort((a, b) => b.v - a.v), W, H);
  const seen = new Set();
  rects.forEach((r) => {
    const h = r.item; seen.add(h.ticker);
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
    <tfoot><tr><td class="l" colspan="4">${state.sector ? esc(state.sector) : "Whole pie"}</td><td>${pp(totW)}</td><td></td><td>${G(gbp(tot))}</td><td class="${cls(day)}">${pct2(day)}</td><td class="${cls(totPl)}">${G(gbpS(totPl))}</td><td colspan="3"></td></tr></tfoot>`;
}
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
      scales: { y: { grid: { color: COL.hair }, border: { display: false }, ticks: { callback: (v) => `${v > 0 ? "+" : ""}${v}` } }, x: { grid: { display: false }, border: { display: false }, ticks: { autoSkip: false, maxRotation: 0, color: COL.fg, font: { family: "'JetBrains Mono', monospace", size: 10.5, weight: 700 } } } } }) });
}
$("#hold-table").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-go]"); if (tr && !e.target.closest("a, button")) location.hash = tr.dataset.go.slice(1); });

/* ================= quant ================= */
const Phi = (z) => { const t = 1 / (1 + 0.2316419 * Math.abs(z)), d = 0.3989423 * Math.exp(-z * z / 2), p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274)))); return z > 0 ? 1 - p : p; };
async function loadQuant() {
  const [a, st, reb, h, corr] = await Promise.all([data.analysis(), data.stats(), data.rebalances(), data.history("ALL"), data.corr().catch(() => null)]);
  const wait = $("#quant-wait");
  wait.hidden = !!a.ready;
  $$("#view-quant [data-stats]").forEach((el) => (el.hidden = !a.ready));
  if (!a.ready) { wait.innerHTML = `<h2>Not enough history yet</h2><p class="sub">The statistics need at least a week of daily values. The volatility surface below works already.</p>`; return; }
  const s = state.summary, bk = benchKey(s), v = a.vs?.[bk] || {}, bn = shortBench(v.name);
  const R = a.returns, K = a.risk, Q = a.ratios, S = a.significance, M = a.moves || {}, P = h.points || [];
  $("#quant-stats").innerHTML = [
    ["Sharpe ratio", n2(Q.sharpe), "", `${p0(S.prob_sharpe_positive)} likely above zero`], ["Sortino ratio", n2(Q.sortino), "", "return per unit of downside"],
    ["Volatility", pp(K.volatility), "", "a year, from daily moves"], ["Worst fall", pct(K.max_drawdown), "down", `${dShort(K.max_dd_peak)} to ${dShort(K.max_dd_trough)}`],
    [`Beta to the ${esc(bn)}`, n2(v.beta), "", `correlation ${n2(v.correlation)}`], ["Alpha", pct(v.alpha_annual), cls(v.alpha_annual), v.alpha_p < 0.05 ? "a year, statistically significant" : `a year, not yet significant (p ${n2(v.alpha_p)})`],
  ].map(([k, val, c, sub]) => `<div><dt>${k}</dt><dd class="${c}">${val}<small>${sub}</small></dd></div>`).join("");

  // every trading day, as a calendar
  const days = P.slice(1).map((p, i) => ({ t: p.t, r: (1 + p.pie) / (1 + P[i].pie) - 1 }));
  if (days.length) {
    const start = parseDay(days[0].t); start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
    const byDay = Object.fromEntries(days.map((d) => [d.t, d.r])), cells = [], months = [];
    const end = parseDay(days.at(-1).t), span = Math.max(...days.map((d) => Math.abs(d.r)), 0.01) * 0.75;
    for (let d = new Date(start), w = 0; d <= end; d.setDate(d.getDate() + 7), w++) {
      months.push(d.getDate() <= 7 || w === 0 ? MON[d.getMonth()] : "");
      for (let k = 0; k < 5; k++) { const x = new Date(d); x.setDate(d.getDate() + k); const key = `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`, r = byDay[key];
        cells.push(r == null ? "<i></i>" : `<i data-r="${r}" data-d="${key}" style="background:${heat(r, span)}"></i>`); }
    }
    const upDays = days.filter((d) => d.r > 0).length, base = `${upDays} up days, ${days.length - upDays} down`;
    $("#cal-meta").textContent = base;
    $("#calendar").innerHTML = `<div class="cal-months">${months.map((m) => `<span>${m}</span>`).join("")}</div><div class="cal-body"><div class="cal-days"><span>Mon</span><span></span><span>Wed</span><span></span><span>Fri</span></div><div class="cal-grid">${cells.join("")}</div></div>`;
    const grid = $(".cal-grid");
    grid.addEventListener("mouseover", (e) => { const c = e.target.closest("[data-r]"); $("#cal-meta").innerHTML = c ? `${dLong(c.dataset.d)}  <b class="${cls(+c.dataset.r)}">${pct2(+c.dataset.r)}</b>` : base; });
    grid.addEventListener("mouseleave", () => { $("#cal-meta").textContent = base; });
  }
  chart("dd-chart", { type: "line", data: { datasets: [{ label: "Below the high", data: (st.drawdown || []).map((p) => ({ x: tms(p.t), y: p.dd })), borderColor: COL.down, backgroundColor: areaFill(COL.down, "55"), fill: "origin", borderWidth: 1.6, pointRadius: 0 }] }, options: timeOpts(pct) });
  const roll = a.rolling20 || [];
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

  // daily returns against a normal curve
  const hist = a.histogram || [], mu = R.mean_daily, sd = K.volatility / Math.sqrt(252), nDays = hist.reduce((x, b) => x + b.n, 0);
  $("#hist-meta").textContent = S.jarque_bera_p > 0.05 ? "close to a normal curve" : "fatter tails than a normal curve";
  chart("hist-chart", { data: { labels: hist.map((b) => pct((b.lo + b.hi) / 2)), datasets: [
    { type: "line", label: "Normal curve", data: hist.map((b) => nDays * (Phi((b.hi - mu) / sd) - Phi((b.lo - mu) / sd))), borderColor: COL.fg, borderWidth: 1.5, borderDash: [4, 4], pointRadius: 0, tension: 0.4, order: 0 },
    { type: "bar", label: "Days", data: hist.map((b) => b.n), backgroundColor: hist.map((b) => ((b.lo + b.hi) / 2 >= 0 ? COL.up : COL.down)), borderRadius: 3, barPercentage: 0.94, categoryPercentage: 1, order: 1 }] },
    options: barOpts({ plugins: { legend: { display: false }, tooltip: { mode: "index", intersect: false, callbacks: { title: (it) => `${pct2(hist[it[0].dataIndex].lo)} to ${pct2(hist[it[0].dataIndex].hi)}`, label: (it) => (it.dataset.type === "bar" ? ` ${it.raw} days` : ` ${it.raw.toFixed(1)} expected`) } } },
      scales: { x: { grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 7 } }, y: { grid: { color: COL.hair }, border: { display: false }, ticks: { precision: 0 } } } }) });

  // month by month, bot against the benchmark
  const ends = {}; P.forEach((p) => { ends[p.t.slice(0, 7)] = p; });
  const mk = Object.keys(ends).sort(), mret = (get_) => mk.map((m, i) => { const now = get_(ends[m]), prev = i ? get_(ends[mk[i - 1]]) : 0; return bad(now) || bad(prev) ? null : (1 + now) / (1 + prev) - 1; });
  const mb = mret((p) => p.pie), ms = mret((p) => p.bench?.[bk]);
  chart("month-chart", { type: "bar", data: { labels: mk.map((m) => `${MON[+m.slice(5) - 1]}`), datasets: [
    { label: "The bot", data: mb, backgroundColor: COL.amber, borderRadius: 4, maxBarThickness: 26 }, { label: v.name || "Benchmark", data: ms, backgroundColor: COL.blue, borderRadius: 4, maxBarThickness: 26 }] },
    options: barOpts({ plugins: { legend: { display: false }, tooltip: { mode: "index", intersect: false, callbacks: { label: (it) => ` ${it.dataset.label}: ${pct(it.raw)}` } } },
      scales: { x: { grid: { display: false }, border: { display: false } }, y: { grid: { color: COL.hair }, border: { display: false }, ticks: { callback: (x) => pct(x), maxTicksLimit: 5 } } } }) });

  // the bot's moves: click a bar to see what changed
  const W = M.windows || [], moves = [...reb].reverse();   // oldest first
  const wBy = Object.fromEntries(W.map((w) => [w.from, w]));
  if (state.move == null || state.move >= moves.length) state.move = moves.length - 1;
  const showMove = (i) => {
    state.move = i;
    const r = moves[i]; if (!r) { $("#move-detail").innerHTML = `<p class="empty">The bot hasn't changed its targets yet.</p>`; return; }
    const day = r.ts.slice(0, 10), w = wBy[day], ch = [...(r.changes || [])].sort((x, y) => Math.abs(y.delta) - Math.abs(x.delta));
    $("#move-title").textContent = `Move on ${dShort(day)}`;
    $("#move-meta").innerHTML = w ? `bot <b class="${cls(w.bot)}">${pct(w.bot)}</b>, ${esc(bn)} ${pct(w.bench)}` : !bad(r.return_since) ? `<b class="${cls(r.return_since)}">${pct(r.return_since)}</b> since` : "";
    $("#move-detail").innerHTML = `<div class="changes">${ch.slice(0, 9).map((x) => `<a class="change" data-tk="${esc(x.ticker)}" href="${stockHref(x.ticker)}"><span class="tk">${esc(x.symbol)}</span><span class="${x.kind === "added" ? "up" : x.kind === "removed" ? "down" : "muted"}">${x.kind === "added" ? "bought in" : x.kind === "removed" ? "sold out" : x.delta > 0 ? "raised" : "trimmed"}</span><span class="ar">${pp(x.before)} → ${pp(x.after)}</span></a>`).join("") || `<p class="empty">No target changes recorded.</p>`}</div>
      <div class="move-nav"><button type="button" data-step="-1" ${i <= 0 ? "disabled" : ""}>‹ Earlier</button><button type="button" data-step="1" ${i >= moves.length - 1 ? "disabled" : ""}>Later ›</button></div>`;
    $$("#move-detail [data-step]").forEach((b) => b.addEventListener("click", () => { showMove(state.move + +b.dataset.step); paint(); }));
  };
  const wIdx = W.map((w) => moves.findIndex((r) => r.ts.slice(0, 10) === w.from));
  const dim = (col, k) => (wIdx[k] === state.move ? col : col + "59");
  const paint = () => { const c = state.charts["moves-chart"]; if (!c) return; c.data.datasets[0].backgroundColor = W.map((_, k) => dim(COL.amber, k)); c.data.datasets[1].backgroundColor = W.map((_, k) => dim(COL.blue, k)); c.update("none"); };
  chart("moves-chart", { type: "bar", data: { labels: W.map((w) => dShort(w.from)), datasets: [
    { label: "The bot", data: W.map((w) => w.bot), backgroundColor: W.map((_, k) => dim(COL.amber, k)), borderRadius: 3, maxBarThickness: 16 },
    { label: v.name || "Benchmark", data: W.map((w) => w.bench), backgroundColor: W.map((_, k) => dim(COL.blue, k)), borderRadius: 3, maxBarThickness: 16 }] },
    options: barOpts({ interaction: { mode: "index", intersect: false }, onHover: (e, els) => { e.native.target.style.cursor = els.length ? "pointer" : ""; },
      onClick: (e, els) => { if (els[0] && wIdx[els[0].index] >= 0) { showMove(wIdx[els[0].index]); paint(); } },
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (it) => ` ${it.dataset.label}: ${pct(it.raw)}` } } },
      scales: { x: { grid: { display: false }, border: { display: false }, ticks: { maxRotation: 0, autoSkip: true } }, y: { grid: { color: COL.hair }, border: { display: false }, ticks: { callback: (x) => pct(x), maxTicksLimit: 5 } } } }) });
  showMove(state.move);

  $("#risk-meta").textContent = `${a.n_days} trading days`;
  $("#risk-kv").innerHTML = [["Total return", pct(R.total), cls(R.total)], ["A year at this pace", pct(R.cagr), cls(R.cagr)], ["Up days", p0(R.pct_up_days)], ["Average up day", pct2(R.avg_up_day), "up"],
    ["Average down day", pct2(R.avg_down_day), "down"], ["Best day", `${pct2(R.best_day)}${st.best_day ? `, ${dShort(st.best_day.date)}` : ""}`, "up"], ["Worst day", `${pct2(R.worst_day)}${st.worst_day ? `, ${dShort(st.worst_day.date)}` : ""}`, "down"], ["Bad day, 1 in 20", pct2(-K.var95_daily), "down"],
    ["Average of the worst 5%", pct2(-K.cvar95_daily), "down"], ["Longest time below a high", `${K.longest_underwater_days} days`], ["Skew", n2s(K.skew)], ["Excess kurtosis", n2s(K.excess_kurtosis)],
    ["Calmar ratio", n2(Q.calmar)], ["Omega ratio", n2(Q.omega)], ["Tracking error", pp(v.tracking_error)], ["Information ratio", n2(v.information_ratio)],
    [`Share of ${esc(bn)} gains caught`, p0(v.up_capture)], [`Share of ${esc(bn)} falls taken`, p0(v.down_capture)], ["Moves that made money", p0(M.win_rate)], [`Moves that beat the ${esc(bn)}`, p0(M.beat_bench_rate)],
    ["Sharpe, 95% range", S.sharpe_ci95 ? `${n2(S.sharpe_ci95[0])} to ${n2(S.sharpe_ci95[1])}` : "—"], ["Days needed to be sure", Math.round(S.min_track_record_days).toLocaleString("en-GB")], ["Months ahead of it", `${v.months_beaten} of ${v.months}`], ["Trading days between moves", n2(M.avg_days_between)],
  ].map(([k, val, c]) => `<div><dt>${k}</dt><dd class="${c || ""}">${val}</dd></div>`).join("");
}

/* volatility surface, with a smile and term structure beside it */
const surfaces = {};
async function loadVol() {
  const hs = (state.empty ? state.holdingsData?.holdings || [] : held()).filter((h) => /_US_EQ$/.test(h.ticker)).sort((a, b) => (b.weight || 0) - (a.weight || 0));
  const pick = $("#vol-pick");
  if (!hs.length) { $("#vol-surface").innerHTML = `<p class="empty">No US-listed stocks to show.</p>`; return; }
  if (!hs.some((h) => h.ticker === state.volTicker)) state.volTicker = hs[0].ticker;
  if (pick.options.length !== hs.length) pick.innerHTML = hs.map((h) => `<option value="${esc(h.ticker)}"${h.ticker === state.volTicker ? " selected" : ""}>${esc(h.symbol)}  ${esc(h.name)}</option>`).join("");
  pick.onchange = () => { state.volTicker = pick.value; drawVol(); };
  if (state.volDrawn !== state.volTicker || !surfaces["vol-surface"]) await drawVol();
}
async function drawVol() {
  const t = state.volTicker, host = $("#vol-surface");
  state.volDrawn = t;
  surfaces["vol-surface"]?.destroy(); surfaces["vol-surface"] = null;
  host.innerHTML = `<p class="empty">Loading option prices…</p>`;
  let d; try { d = await load(`vol-${t}`, `/api/volsurface/${encodeURIComponent(t)}`); } catch { d = { available: false }; }
  if (state.volTicker !== t) return;
  $("#view-quant").classList.toggle("no-vol", !d.available);
  if (!d.available || !window.VolSurface) {
    host.innerHTML = `<p class="empty">No option prices available for ${esc(sym(t))} right now.</p>`; $("#vol-expiries").innerHTML = ""; $("#smile-meta").textContent = "";
    ["smile-chart", "term-chart"].forEach((id) => { state.charts[id]?.destroy(); delete state.charts[id]; });
    return;
  }
  const { M, E, Z } = VolSurface.prep(d), atm = M.findIndex((m) => Math.abs(m - 1) < 1e-6);
  let cur = E.reduce((b, e, i) => (Math.abs(e.days - 45) < Math.abs(E[b].days - 45) ? i : b), 0);
  const s3 = (surfaces["vol-surface"] = VolSurface.render(host, d, { highlight: cur, onPick: (days) => select(E.findIndex((e) => e.days === days)) }));
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
  if (/_US_EQ$/.test(ticker)) { volCard.hidden = false; statsCard.className = "card s4"; $("#s-stats").classList.remove("wide"); $("#s-surface").innerHTML = `<p class="empty">Loading option prices…</p>`; try { vd = await load(`vol-${ticker}`, `/api/volsurface/${encodeURIComponent(ticker)}`); } catch { /* shown as unavailable */ } }
  if (state.stock !== ticker) return;
  const ok = vd.available && window.VolSurface;
  volCard.hidden = !ok; statsCard.className = `card ${ok ? "s4" : "s12"}`; $("#s-stats").classList.toggle("wide", !ok);
  if (ok) { surfaces["s-surface"] = VolSurface.render($("#s-surface"), vd); $("#s-vol-meta").textContent = `from option prices, share price ${money(vd.spot, "USD")}`; }
}
function drawStockChart(animate = false) {
  const d = state.stockData; if (!d) return;
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

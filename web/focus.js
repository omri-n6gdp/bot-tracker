"use strict";
/* ================= focus: click any block to open it larger, with what it shows, where its
   numbers come from, and what the live numbers say right now =================
   Loaded after app.js and shares its helpers (state, data, formatters). The block itself is
   moved into the window (so it stays live and interactive) and moved back on close. */

const FOCUS_SKIP = "a, button, input, select, textarea, label, summary, [data-tk], [data-sector], tr[data-go], .seg, .chips, .warn, .surface, .gform, .move-nav, .scrub-date";
const FX = { open: false, el: null, ph: null, key: null, token: 0, anim: null, mo: null, moT: null, lastFocus: null };
const EASE = "cubic-bezier(.2, .8, .2, 1)";

/* ---------- small helpers for the live text ---------- */
const B = (html, c = "") => `<b class="${c}">${html}</b>`;
const BP = (x, f = pct2) => B(f(x), cls(x));           // a coloured % in bold
const lead = (x) => (bad(x) ? "level with" : x >= 0 ? "ahead of" : "behind");
const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;
const dur = (ms) => { const m = Math.round(ms / 60000); return m < 60 ? `${m} min` : m < 1440 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m` : `${Math.floor(m / 1440)}d ${Math.floor((m % 1440) / 60)}h`; };
const hm = (x) => timeF.format(new Date(x));
const dayHm = (x) => { const d = new Date(x), today = d.toLocaleDateString("en-GB", { timeZone: "Europe/London" }) === new Date().toLocaleDateString("en-GB", { timeZone: "Europe/London" }); return today ? hm(x) : `${DOW[d.getDay()]} ${hm(x)}`; };
const sum = (a) => a.reduce((x, y) => x + y, 0);
const wavg = (rows, v, w = (h) => h.weight) => { const r = rows.filter((h) => !bad(v(h)) && w(h) > 0), W = sum(r.map(w)); return W ? sum(r.map((h) => v(h) * w(h))) / W : null; };
const median = (a) => { const s = [...a].filter((x) => !bad(x)).sort((x, y) => x - y); return s.length ? (s[(s.length - 1) >> 1] + s[s.length >> 1]) / 2 : null; };
const bn = () => shortBench(state.summary?.benchmarks?.[benchKey(state.summary)]?.name || "S&P 500");
const early = () => !!state.analysis?.readings;
const listNames = (hs, f = (h) => h.symbol) => { const n = hs.map(f); return n.length <= 1 ? n.join("") : `${n.slice(0, -1).join(", ")} and ${n.at(-1)}`; };
const pieName = () => esc(state.summary?.pie_name || "the pie");

/* where the numbers come from: the same few sources, said once */
const SRC = {
  pie: "The pie's value (its holdings plus uninvested cash) comes straight from Trading 212's API: the tracker reads it every 60 seconds and saves each reading.",
  deposits: () => `Money paid in is taken from the deposits file, fixed at ${G(gbp0(state.summary?.net_deposits))} for now.`,
  bench: "The S&P 500 line is the Vanguard S&P 500 fund (VUAG) in pounds. Its minute prices come from Yahoo Finance: the US fund SPY converted at the live pound-dollar rate while New York trades, VUAG in London when only London trades, and S&P 500 futures overnight when no fund is trading.",
  stocks: "Each holding's quantity and value come from the pie readings; yesterday's closing prices come from Yahoo Finance, so each stock's move today is measured against its last close, in pounds.",
  yahoo: "Company details, analyst ratings and price targets come from Yahoo Finance's company profiles, refreshed a few times a day.",
  year: "A year of daily closing prices for every stock it holds now, from Yahoo Finance, converted to pounds.",
  moves: "A move is spotted when Trading 212 shows the bot has changed the pie's target weights.",
  options: "Option prices for the US-listed holdings come from Yahoo Finance's option chains; implied volatility is worked out from them.",
};

/* ---------- the explanations: what it is, where it comes from, what it says now ---------- */
// what/source: html (or a function returning it). live: async () => [html bullets]
const EXPLAIN = {
  hero: {
    what: "The headline numbers. Value now is everything in the pie at the latest reading. Profit and return compare that with the money paid in. Against the S&P 500 shows how the same money would have done in an S&P 500 fund, put in at the same times, in percentage points. The Sharpe ratio is return per unit of risk (above 1 is good, above 2 is excellent over a long record). Worst fall is the biggest drop from a high so far.",
    source: () => `${SRC.pie} ${SRC.deposits()} ${SRC.bench}`,
    live: async () => {
      const s = state.summary, a = state.analysis || {}, bk = benchKey(s);
      const ret = s.return_on_deposits, b = s.bench_on_deposits?.[bk]?.return ?? s.benchmarks?.[bk]?.since_start, gap = bad(ret) || bad(b) ? null : ret - b, o = s.since_open;
      const out = [`The pie is worth ${B(G(gbp(s.value)))} against ${G(gbp0(s.net_deposits))} paid in: ${B(G(gbpS(s.profit)), cls(s.profit))}, a return of ${BP(ret, pct)}.`];
      if (gap != null) out.push(`The same money in the ${bn()} would be ${BP(b, pct)}, so the bot is ${B(pts(gap), cls(gap))} ${lead(gap)} the market overall${Math.abs(gap) < 0.005 ? ", which is still within a normal day's noise" : ""}.`);
      if (o) out.push(`Since ${o.at_open ? "the New York open" : hm(o.from)} it has moved ${BP(o.pct)} (${G(gbpS(o.gbp))}).`);
      if (a.ready) {
        const sh = a.ratios?.sharpe;
        out.push(`Sharpe ratio ${B(n2(sh))}: ${bad(sh) ? "not available yet" : sh > 2 ? "a lot of return for the risk taken so far" : sh > 1 ? "good return for the risk so far" : sh > 0 ? "positive, but modest for the risk" : "the risk hasn't been paid for yet"}.${a.readings ? ` It's worked out from ${a.readings.readings.toLocaleString("en-GB")} minute readings scaled up to a year, so it will swing a lot until there are a few months of daily closes.` : ""}`);
        const dd = a.risk?.max_drawdown, cur = a.risk?.current_drawdown;
        if (!bad(dd)) out.push(`The worst fall from a high so far is ${BP(dd, pct)}; it's ${bad(cur) || cur > -0.0005 ? "at its high right now" : `${B(pct(Math.abs(cur)).replace("+", ""), "down")} below its high right now`}.`);
      }
      if (s.stale) out.push(`${B("Out of date:", "down")} the last reading was at ${dayHm(s.as_of)}, so these numbers aren't live.`);
      return out;
    },
  },

  chart: {
    title: () => (state.range === "1D" ? `Since ${state.series?.label || "09:00"}` : "Performance against the market"),
    what: () => {
      const S = state.series || {};
      if (state.range === "1D") return `The pie's value from 09:00 UK time to now, as a % change. It resets at 09:00 every day: before 09:00 it runs from 09:00 yesterday, and at the weekend it stays on Friday's day. The blue line is the ${bn()} from 09:00 too, read at exactly the same moments as the pie, so at any point on the chart you're comparing like with like. The dashed line is 0%, where both started. Hatched stripes are stretches when the tracker took no readings: both lines hold flat across them. Drag across the chart to see the value at any moment.`;
      if (S.intraday) return `Every reading since the pie started${S.even ? `, as a return on the money paid in (0% is break-even)` : ""}. The blue line is what the same money would have done in the ${bn()}, measured from its close the day before the pie's first reading: at that first reading it moves straight from 0 to its change since that close (overnight and the jump at the open included), just as the pie's line moves straight from break-even. After that both are read at the same moments. Once there are five daily closes this switches to one point per day.`;
      return `One point per trading day over the chosen range${state.series?.even ? ", as a return on the money paid in (0% is break-even)" : ", as a % change from the start of the range"}. The blue line is the ${bn()} on the same money. Amber squares mark days the bot changed its targets.`;
    },
    source: () => `${SRC.pie} ${SRC.bench} The pie's line uses its value per unit (like a fund's unit price), so money paid in or taken out never shows up as a gain or a loss.`,
    live: async () => {
      const ch = state.charts["main-chart"]; if (!ch) return ["The chart has no points to describe yet."];
      const ds = ch.data.datasets, bot = ds[0].data, mk = ds.find((d) => d.label && state.bench.some((b) => b.on && b.name === d.label))?.data || [];
      const S = state.series, intr = S?.intraday, when = (x) => (intr ? dayHm(x) : dShort(new Date(x).toISOString().slice(0, 10)));
      const end = bot.at(-1), out = [];
      const hi = bot.reduce((m, p) => (p.y > m.y ? p : m), bot[0]), lo = bot.reduce((m, p) => (p.y < m.y ? p : m), bot[0]);
      const at = (arr, x) => { const i = nearest(arr, x); return i < 0 ? null : arr[i]; };
      const span = state.range === "1D" ? `since ${S?.label || "09:00"}` : intr ? "since the start" : `over ${({ "1W": "the week", "1M": "the month", "3M": "three months", YTD: "the year so far", ALL: "the whole record" })[state.range] || "this range"}`;
      if (mk.length) {
        const m = mk.at(-1).y, g = end.y - m;
        out.push(`${span[0].toUpperCase() + span.slice(1)}: the pie ${BP(end.y)}, the ${bn()} ${BP(m)}. The bot is ${B(pts(g), cls(g))} ${lead(g)} the market.`);
        // how often it was ahead, and its biggest lead and gap, at the same moments
        const pairs = bot.map((p) => { const q = at(mk, p.x); return q && Math.abs(q.x - p.x) < (intr ? 6 * 60e3 : 4 * 864e5) ? { x: p.x, d: p.y - q.y, b: p.y, m: q.y } : null; }).filter(Boolean);
        if (pairs.length > 3) {
          const ahead = pairs.filter((p) => p.d > 0).length / pairs.length;
          const best = pairs.reduce((a, p) => (p.d > a.d ? p : a)), worst = pairs.reduce((a, p) => (p.d < a.d ? p : a));
          out.push(`It was ahead of the market at ${B(p0(ahead))} of the points on the chart. Its biggest lead was ${B(pts(best.d), cls(best.d))} at ${when(best.x)}${worst.d < 0 ? `; it was furthest behind, ${B(pts(worst.d), "down")}, at ${when(worst.x)}` : ", and it was never behind"}.`);
          // how strongly it follows the market, step by step
          let sxy = 0, sxx = 0, n = 0;
          for (let i = 1; i < pairs.length; i++) { const dm = pairs[i].m - pairs[i - 1].m, db = pairs[i].b - pairs[i - 1].b; if (dm || db) { sxy += dm * db; sxx += dm * dm; n++; } }
          if (n >= 25 && sxx > 0) { const beta = sxy / sxx; out.push(`Step by step, when the ${bn()} moved 1% the pie moved about ${B(n2(beta) + "%")} in the same direction${beta > 1.2 ? ": it swings harder than the market" : beta < 0.6 ? ": it mostly does its own thing" : ""}.`); }
        }
      } else out.push(`${span[0].toUpperCase() + span.slice(1)} the pie is ${BP(end.y)}. Turn the ${bn()} chip on above the chart to compare it with the market.`);
      out.push(`Its high on this chart was ${BP(hi.y)} at ${when(hi.x)} and its low ${BP(lo.y)} at ${when(lo.x)}${hi.x > lo.x ? "; it has climbed since that low" : "; it has come down since that high"}.`);
      // 1D: overnight against the session so far
      const o = state.summary?.since_open;
      if (state.range === "1D" && o?.at_open && mk.length) {
        const ox = new Date(o.from).getTime();
        if (ox > bot[0].x && ox < end.x) {
          const bo = at(bot, ox), mo = at(mk, ox);
          if (bo && mo) {
            const bs = (1 + end.y) / (1 + bo.y) - 1, ms = (1 + mk.at(-1).y) / (1 + mo.y) - 1;
            out.push(`Split at the New York open (${hm(ox)}): from ${S?.label || "09:00"} to the open the pie moved ${BP(bo.y)} against the ${bn()}'s ${BP(mo.y)}; since the open, ${BP(bs)} against ${BP(ms)}.`);
          }
        }
      }
      if (state.range === "1D" && S?.anchorReading && new Date(S.anchor) - new Date(S.anchorReading) > 6 * 60e3) out.push(`The tracker's last reading before 09:00 was at ${dayHm(S.anchorReading)}, so 0% is the pie's value then (the market line starts from its own price at the same moment).`);
      const gaps = ch.options.plugins.gapBands?.gaps || [];
      if (gaps.length) { const tot = sum(gaps.map(([a, b]) => b - a)), big = gaps.reduce((a, g) => (g[1] - g[0] > a[1] - a[0] ? g : a)); out.push(`The tracker took no readings for ${B(dur(tot))} on this chart (the longest stretch ${dayHm(big[0])} to ${dayHm(big[1])}). Both lines hold flat across the hatched stripes, so any move during them shows up as a single step when readings resume.`); }
      return out;
    },
  },

  map: {
    title: "The pie, mapped",
    what: () => `Every holding as a tile, sized by its share of the pie. The colour shows ${{ day: "today's move: green up, red down, deeper for bigger moves (full colour at ±5%)", total: "the gain or loss since the bot bought it (full colour at ±35%)", analyst: "how far below analysts' average 12-month target the price is (full colour at +100%)" }[state.mapMode]}. The grey tile is cash the pie couldn't split between the holdings. Click a tile to open that stock.`,
    source: () => `${SRC.stocks} ${state.mapMode === "analyst" ? SRC.yahoo : ""}`,
    live: async () => {
      const hs = held(), cash = state.holdingsData?.cash || 0, tot = sum(hs.map((h) => h.value || 0)) + cash, out = [];
      const byW = [...hs].sort((a, b) => b.weight - a.weight), top3 = byW.slice(0, 3);
      out.push(`${plural(hs.length, "stock")}. The three biggest (${listNames(top3)}) make up ${B(pp(sum(top3.map((h) => h.weight))))} of the holdings; the smallest is ${esc(byW.at(-1)?.symbol || "")} at ${pp(byW.at(-1)?.weight)}.`);
      if (state.mapMode === "day") {
        const up = hs.filter((h) => h.day_pct > 0), dn = hs.filter((h) => h.day_pct < 0), avg = wavg(hs, (h) => h.day_pct);
        out.push(`${B(up.length, "up")} green and ${B(dn.length, "down")} red today. Weighted by size, the stocks are ${BP(avg)} on the day.`);
        const big = [...hs].filter((h) => !bad(h.day_gbp)).sort((a, b) => Math.abs(b.day_gbp) - Math.abs(a.day_gbp))[0];
        if (big) out.push(`${esc(big.symbol)} matters most today: ${BP(big.day_pct)} on a ${pp(big.weight)} tile is ${B(G(gbpS(big.day_gbp)), cls(big.day_gbp))} for the pie.`);
        const bigTile = byW[0]; if (bigTile) out.push(`The biggest tile, ${esc(bigTile.symbol)}, is ${BP(bigTile.day_pct)}: ${Math.abs(bigTile.day_pct) < 0.005 ? "quiet, so it isn't driving the day" : bigTile.day_pct > 0 ? "helping" : "holding the pie back"}.`);
      } else if (state.mapMode === "total") {
        const win = hs.filter((h) => h.pl_pct > 0), avg = wavg(hs, (h) => h.pl_pct), best = [...hs].sort((a, b) => b.pl_pct - a.pl_pct);
        out.push(`${B(win.length, "up")} of ${hs.length} are above what the bot paid. Weighted by size the holdings are ${BP(avg, pct)} since bought.`);
        if (best.length) out.push(`Best: ${esc(best[0].symbol)} ${BP(best[0].pl_pct, pct)}. Worst: ${esc(best.at(-1).symbol)} ${BP(best.at(-1).pl_pct, pct)}.`);
      } else {
        const cov = hs.filter((h) => !bad(h.analyst?.upside)), avg = wavg(cov, (h) => h.analyst.upside), big = cov.filter((h) => h.analyst.upside > 0.5), over = cov.filter((h) => h.analyst.upside < 0);
        out.push(`Weighted by size, analysts' targets sit ${BP(avg, p0s)} above today's prices (${cov.length} of ${hs.length} stocks covered).`);
        if (big.length) out.push(`${B(pp(sum(big.map((h) => h.weight))))} of the pie is in stocks more than 50% below their targets: ${listNames(big)}. Big gaps like that often mean thin coverage or very optimistic targets.`);
        if (over.length) out.push(`${listNames(over)} ${over.length === 1 ? "trades" : "trade"} above the average target already.`);
      }
      if (cash > 0.005) out.push(`Cash is ${G(gbp(cash))}, ${pp(cash / (tot || 1))} of the pie.`);
      return out;
    },
  },

  movers: {
    what: "The holdings that moved most today, biggest rises at the top and biggest falls below, with each one's % move and what it means in pounds for the pie (the move times how much the pie holds). Click one to open the stock.",
    source: () => SRC.stocks,
    live: async () => {
      const hs = held().filter((h) => !bad(h.day_pct)), out = [];
      const up = hs.filter((h) => h.day_pct > 0), dn = hs.filter((h) => h.day_pct < 0);
      out.push(`${B(up.length, "up")} up and ${B(dn.length, "down")} down: ${up.length > dn.length * 2 ? "a broad rise, not just one stock" : dn.length > up.length * 2 ? "a broad fall across the pie" : "a mixed day"}.`);
      const net = sum(hs.map((h) => h.day_gbp || 0)), byG = [...hs].sort((a, b) => b.day_gbp - a.day_gbp), best = byG[0], worst = byG.at(-1);
      if (best && net) out.push(`Together the stocks are ${B(G(gbpS(net)), cls(net))} today. ${esc(best.symbol)} alone is ${B(G(gbpS(best.day_gbp)), cls(best.day_gbp))}${net > 0 && best.day_gbp > 0 ? (best.day_gbp > net ? ": more than the whole day's net gain, so the rest of the pie lost money overall" : `, ${B(p0(best.day_gbp / net))} of the day's net gain`) : net < 0 && best.day_gbp > 0 ? ", softening the fall" : ""}.`);
      if (worst && worst.day_gbp < 0) out.push(`The biggest drag is ${esc(worst.symbol)} at ${BP(worst.day_pct)}, ${B(G(gbpS(worst.day_gbp)), "down")}.`);
      const sorted = [...hs].sort((a, b) => b.day_pct - a.day_pct);
      if (sorted.length > 1) out.push(`The gap between the best (${esc(sorted[0].symbol)} ${pct2(sorted[0].day_pct)}) and the worst (${esc(sorted.at(-1).symbol)} ${pct2(sorted.at(-1).day_pct)}) is ${B(pts(sorted[0].day_pct - sorted.at(-1).day_pct).replace("+", ""))}; the middle stock moved ${BP(median(hs.map((h) => h.day_pct)))}.`);
      return out;
    },
  },

  news: {
    what: "The latest headlines about the stocks in the pie, newest first. The tags show which holdings each story is about; click a headline to read it, or a tag to open the stock.",
    source: "Headlines come from Yahoo Finance's news feed for each holding, checked about every half hour.",
    live: async () => {
      const n = await data.news(), items = n.items || [], out = [];
      const today = items.filter((x) => x.published && Date.now() - new Date(x.published).getTime() < 24 * 3600e3);
      out.push(`${B(today.length)} headlines in the last 24 hours, covering ${n.stocks_covered} of the ${n.stocks_held} stocks.`);
      const count = {}; today.forEach((x) => (x.symbols || []).forEach((sy) => { count[sy] = (count[sy] || 0) + 1; }));
      const top = Object.entries(count).sort((a, b) => b[1] - a[1]).slice(0, 3);
      if (top.length) out.push(`Most in the news: ${top.map(([sy, c]) => `${B(esc(sy))} (${c})`).join(", ")}.`);
      const movers = held().filter((h) => !bad(h.day_pct)).sort((a, b) => Math.abs(b.day_pct) - Math.abs(a.day_pct)).slice(0, 3);
      if (movers.length) out.push(`Today's biggest movers: ${movers.map((h) => `${esc(h.symbol)} ${BP(h.day_pct)} ${count[h.symbol] ? `with ${plural(count[h.symbol], "headline")}` : "with no headline today, so the move may be the market or its sector rather than company news"}`).join("; ")}.`);
      if (items[0]?.published) out.push(`The newest story arrived ${ago(items[0].published)}.`);
      return out;
    },
  },

  consensus: {
    what: "What professional analysts think of the stocks in the pie. The big number is the typical (median) gap between today's price and analysts' average 12-month price target. The bar counts the ratings, from strong buy to sell. Below are the stocks furthest below their targets.",
    source: () => SRC.yahoo,
    live: async () => {
      const hs = held(), cov = hs.filter((h) => !bad(h.analyst?.upside)), rated = hs.filter((h) => h.analyst?.rating), out = [];
      if (!cov.length) return ["No analyst data has loaded yet."];
      const med = median(cov.map((h) => h.analyst.upside)), w = wavg(cov, (h) => h.analyst.upside);
      out.push(`Median gap to target ${BP(med, p0s)}; weighted by how much the pie holds, ${BP(w, p0s)}. ${w > 0.3 ? "Analysts see a lot of room to rise, though targets are often optimistic and spread widely." : w > 0 ? "Analysts expect modest gains on average." : "On average the stocks trade above analysts' targets."}`);
      const buy = rated.filter((h) => RANK[h.analyst.rating] <= 2).length, hold = rated.filter((h) => RANK[h.analyst.rating] === 3).length, sell = rated.filter((h) => RANK[h.analyst.rating] >= 4).length;
      out.push(`${B(buy, "up")} rated buy or strong buy, ${B(hold, "amber")} hold, ${B(sell, sell ? "down" : "")} sell or underperform, and ${hs.length - rated.length} with no rating.`);
      const thin = cov.filter((h) => h.analyst.thin);
      if (thin.length) out.push(`${listNames(thin)} ${thin.length === 1 ? "has" : "have"} only a handful of analysts, so ${thin.length === 1 ? "its target is" : "their targets are"} less reliable.`);
      const top = [...cov].sort((a, b) => b.analyst.upside - a.analyst.upside)[0];
      if (top) out.push(`Furthest below target: ${esc(top.symbol)}, ${BP(top.analyst.upside, p0s)} to the average target${top.analyst.analysts ? ` from ${plural(top.analyst.analysts, "analyst")}` : ""}.`);
      return out;
    },
  },

  waterfall: {
    title: () => (state.wf === "day" ? "What moved the pie today" : "What moved the pie since it bought"),
    what: () => (state.wf === "day"
      ? "A walk from the pie's value at the New York open to now. Each bar is one stock's gain (green) or loss (red) in pounds, biggest first; the grey bar is everything else (the bot buying and selling during the day, rounding, cash). The amber bar on the right is the total change."
      : "A walk from the money paid in to the profit now. Each bar is one stock's gain or loss since the bot bought it; the grey bar covers cash, costs and stocks it has already sold. The amber bar is the total."),
    source: () => `${SRC.stocks} ${SRC.pie}`,
    live: async () => {
      const s = state.summary, day = state.wf === "day", hs = held(), out = [];
      const v = (h) => (day ? h.day_gbp : h.pl), total = day ? (s.since_open ? s.since_open.gbp : s.day_change) : s.profit;
      const items = hs.filter((h) => !bad(v(h))).sort((a, b) => v(b) - v(a));
      if (!items.length || bad(total)) return ["Not enough data yet."];
      const gains = sum(items.filter((h) => v(h) > 0).map(v)), losses = sum(items.filter((h) => v(h) < 0).map(v));
      out.push(`${day ? "Since the open" : "Since it bought"} the pie is ${B(G(gbpS(total)), cls(total))}: stocks that rose added ${B(G(gbpS(gains)), "up")}, stocks that fell took ${B(G(gbpS(losses)), "down")}.`);
      const top = items[0], top2 = items.slice(0, 2);
      if (gains > 0) out.push(`${esc(top.symbol)} did the most: ${B(G(gbpS(v(top))), "up")}, ${B(p0(v(top) / gains))} of all the gains. The top two (${listNames(top2)}) together made ${p0(sum(top2.map(v)) / gains)}.`);
      const worst = items.at(-1); if (v(worst) < 0) out.push(`The biggest loss was ${esc(worst.symbol)}, ${B(G(gbpS(v(worst))), "down")}.`);
      const other = total - sum(items.map(v));
      if (Math.abs(other) >= 0.01) out.push(`The grey "${day ? "Trading, cash" : "Cash, costs"}" bar is ${B(G(gbpS(other)), cls(other))}: the part of the change not explained by the stocks held now${day ? " (mostly the bot trading during the day and price differences between readings)" : " (cash, trading costs and stocks already sold)"}.`);
      return out;
    },
  },

  recap: {
    what: "A short summary of the day: the pie against the market since the open, the best and worst stocks, any moves the bot made, the overall return, and the prediction game. After 21:30 London time it becomes the day's final recap.",
    source: () => `Built from the same readings as the rest of the page. The final version is saved at the 21:30 daily close (and sent to Telegram if that's set up). ${SRC.bench}`,
    live: async () => {
      const r = await data.recap(), out = [];
      if (!r?.date) return ["No recap yet today."];
      const o = r.since_open, b = Object.values(r.bench_since_open || {})[0];
      if (o) out.push(`Since the open the pie is ${BP(o.pct)} (${G(gbpS(o.gbp))})${b ? ` against the ${shortBench(b.name)}'s ${BP(b.change)}, so ${B(pts(o.pct - b.change), cls(o.pct - b.change))} ${lead(o.pct - b.change)} it` : ""}.`);
      if (r.best && r.worst) out.push(`Best ${esc(r.best.symbol)} ${BP(r.best.pct)}, worst ${esc(r.worst.symbol)} ${BP(r.worst.pct)}; ${r.up} up and ${r.down} down.`);
      out.push((r.moves || []).length ? `The bot changed its targets today (${r.moves.map((m) => plural(m.count, "change")).join(", ")}).` : "The bot didn't change its targets today.");
      out.push(r.final ? "This is the final recap for the day." : `Not final yet: it's rebuilt through the day and fixed at 21:30.`);
      return out;
    },
  },

  game: {
    what: "Guess the pie's return for the week (Monday's open to Friday's close). Guesses stay hidden until the deadline; the closest guess wins the week, and the leaderboard counts wins and the average miss.",
    source: "Guesses are saved on the tracker when you press Save. The week's real return comes from the pie's own readings, so deposits don't count.",
    live: async () => {
      const g = await data.game(), out = [];
      if (!g?.players?.length) return ["The game isn't set up."];
      const o = g.open, missing = g.players.filter((p) => !o.guessed.includes(p));
      out.push(`${B(`${o.guessed.length} of ${g.players.length}`)} players have guessed for the week of ${dShort(o.week)}. Guessing closes ${new Date(o.deadline).toLocaleDateString("en-GB", { weekday: "long", timeZone: "Europe/London" })} at ${hm(o.deadline)}.`);
      if (missing.length) out.push(`Still to guess: ${esc(missing.join(", "))}.`);
      if (g.current) out.push(`This week so far the pie is ${BP(g.current.return)}${g.current.guesses?.find((x) => x.winner) ? `, with ${esc(g.current.guesses.find((x) => x.winner).player)} closest` : ""}.`);
      const lb = (g.leaderboard || []).filter((b) => b.played);
      out.push(lb.length ? `${esc(lb[0].player)} leads the board with ${plural(lb[0].wins, "win")}.` : "No week has been scored yet, so there's no leaderboard.");
      return out;
    },
  },

  mc: {
    title: "Where it could go",
    what: () => `A range of possible values for the pie ${({ 21: "over the next month", 63: "over the next three months", 252: "over the next year" })[state.mcH]}. It replays real days from the past year at random, in a new order, thousands of times, using the stocks and weights the pie has now. The dark band holds the middle half of outcomes, the light band 9 in 10, and the amber line is the middle outcome. It assumes no trend up or down, so it shows how wide the range is, not a forecast.${state.mcView === "start" ? " The white line is what really happened since this range was first made." : ""}`,
    source: () => `${SRC.year} 2,000 paths are simulated from those daily moves (with their average taken out, so there's no built-in trend), and the cash is held flat.`,
    live: async () => {
      const m0 = await data.mc(), fromStart = state.mcView === "start" && m0.anchor, m = fromStart ? m0.anchor : m0, H = state.mcH, out = [];
      const F = m.fan.slice(0, H), end = F.at(-1), v0 = m.value, hz = m.horizons[String(H)] || {}, lab = ({ 21: "a month", 63: "three months", 252: "a year" })[H];
      out.push(`In ${lab}, the middle outcome is ${B(G(gbp(end[3])))} (${BP(end[3] / v0 - 1, pct)}). Half the paths end between ${G(gbp(end[2]))} and ${G(gbp(end[4]))}, i.e. ${pct(end[2] / v0 - 1)} to ${pct(end[4] / v0 - 1)}.`);
      out.push(`1 path in 20 ends below ${B(G(gbp(end[1])), "down")} (${pct(end[1] / v0 - 1)}) and 1 in 20 above ${B(G(gbp(end[5])), "up")} (${pct(end[5] / v0 - 1)}): that ${B(pts(end[5] / v0 - end[1] / v0).replace("+", ""))} spread is how much uncertainty ${lab} of holding these stocks carries.`);
      out.push(`Chance of being below today's value: ${B(p0(hz.loss))}. Chance of a fall of 10% or more: ${B(p0(hz.down10), "down")}; of a rise of 10% or more: ${B(p0(hz.up10), "up")}.`);
      if (fromStart && m.real?.length) {
        const [x, y] = m.real.at(-1), row = m.fan[Math.min(m.fan.length - 1, Math.max(0, Math.round(x) - 1))];
        if (row) { const r = (z) => row[z] / m.value - 1, where = y < r(1) ? "below the 9-in-10 band: worse than 19 paths in 20" : y > r(5) ? "above the 9-in-10 band: better than 19 paths in 20" : y < r(2) ? "in the lower part of the range" : y > r(4) ? "in the upper part of the range" : "inside the middle half, as expected"; out.push(`So far the pie has really done ${BP(y)}, which sits ${B(where)}.`); }
      }
      out.push(`Built from ${m.days_used} trading days of ${m.holdings} stocks, on ${dShort(m.built)}.`);
      return out;
    },
  },

  table: {
    what: "Every stock the pie holds, with its sector, analysts' rating, its weight now and the bot's target weight, its value, today's move, the gain or loss since the bot bought it, the gap to analysts' target, and the last month's price. Click a column to sort, or a row to open the stock.",
    source: () => `${SRC.stocks} Target weights are the bot's own, from Trading 212. ${SRC.yahoo}`,
    live: async () => {
      const hs = held(), out = [], tot = sum(hs.map((h) => h.value || 0)), pl = sum(hs.map((h) => h.pl || 0));
      out.push(`${plural(hs.length, "stock")} worth ${B(G(gbp(tot)))}${cashNow() > 0.005 ? ` plus ${G(gbp(cashNow()))} cash` : ""}. Together they are ${B(G(gbpS(pl)), cls(pl))} since bought.`);
      const byPl = [...hs].filter((h) => !bad(h.pl_pct)).sort((a, b) => b.pl_pct - a.pl_pct);
      if (byPl.length) out.push(`Best since bought: ${esc(byPl[0].symbol)} ${BP(byPl[0].pl_pct, pct)}. Worst: ${esc(byPl.at(-1).symbol)} ${BP(byPl.at(-1).pl_pct, pct)}.`);
      const d = hs.filter((h) => !bad(h.target)).map((h) => ({ h, d: h.weight - h.target })).sort((a, b) => Math.abs(b.d) - Math.abs(a.d))[0];
      if (d) out.push(`Furthest from its target: ${esc(d.h.symbol)} at ${pp(d.h.weight)} against ${pp(d.h.target)}.`);
      if (state.sector) out.push(`Filtered to ${B(esc(state.sector))}; click the filter by the title to clear it.`);
      return out;
    },
  },

  contrib: {
    what: "Each holding's gain or loss in pounds since the bot bought it, best at the top. Stocks it has already sold aren't shown. Click a bar to open the stock.",
    source: () => SRC.stocks,
    live: async () => {
      const hs = held().filter((h) => !bad(h.pl)), out = [];
      const w = hs.filter((h) => h.pl > 0), l = hs.filter((h) => h.pl < 0), gains = sum(w.map((h) => h.pl)), losses = sum(l.map((h) => h.pl));
      out.push(`${B(w.length, "up")} holdings in profit (${B(G(gbpS(gains)), "up")}) and ${B(l.length, "down")} at a loss (${B(G(gbpS(losses)), "down")}): ${B(G(gbpS(gains + losses)), cls(gains + losses))} overall.`);
      const s = [...hs].sort((a, b) => b.pl - a.pl);
      if (s.length) out.push(`${esc(s[0].symbol)} has made the most, ${B(G(gbpS(s[0].pl)), cls(s[0].pl))} (${pct(s[0].pl_pct)} on the position)${gains > 0 && s[0].pl > 0 ? `, ${p0(s[0].pl / gains)} of all the gains` : ""}. ${esc(s.at(-1).symbol)} has lost the most, ${B(G(gbpS(s.at(-1).pl)), cls(s.at(-1).pl))}.`);
      return out;
    },
  },

  forest: {
    what: "For each stock with analyst coverage: the grey bar spans analysts' lowest to highest 12-month price target, the dot is the average target, and the vertical line is today's price. The number on the right is the gap from today's price to the average target. Faded rows have very few analysts.",
    source: () => SRC.yahoo,
    live: async () => {
      const cov = held().filter((h) => !bad(h.analyst?.upside)), out = [];
      if (!cov.length) return ["No analyst targets have loaded yet."];
      const s = [...cov].sort((a, b) => b.analyst.upside - a.analyst.upside);
      out.push(`${cov.length} stocks have targets. The biggest gap is ${esc(s[0].symbol)} at ${BP(s[0].analyst.upside, p0s)}; the smallest ${esc(s.at(-1).symbol)} at ${BP(s.at(-1).analyst.upside, p0s)}.`);
      const below = cov.filter((h) => !bad(h.analyst.low_pct) && h.analyst.low_pct > 0);
      if (below.length) out.push(`For ${listNames(below)}, even the lowest target is above today's price.`);
      const wide = cov.filter((h) => !bad(h.analyst.high_pct) && !bad(h.analyst.low_pct)).sort((a, b) => (b.analyst.high_pct - b.analyst.low_pct) - (a.analyst.high_pct - a.analyst.low_pct))[0];
      if (wide) out.push(`Analysts disagree most on ${esc(wide.symbol)}: targets run from ${pct(wide.analyst.low_pct)} to ${pct(wide.analyst.high_pct)} of today's price.`);
      return out;
    },
  },

  sectors: {
    what: "How the pie is split between industries, by value. Hover a sector to see its share; click one to filter the holdings table to it.",
    source: () => `${SRC.yahoo} Values come from the pie readings.`,
    live: async () => {
      const secs = (state.holdingsData?.sectors || []).filter((x) => x.weight > 0), hs = held(), out = [];
      if (!secs.length) return ["Sectors load with the company profiles."];
      const s = [...secs].sort((a, b) => b.weight - a.weight), eff = 1 / sum(secs.map((x) => x.weight ** 2));
      out.push(`${plural(secs.length, "sector")}; the biggest is ${B(esc(s[0].sector))} at ${B(pp(s[0].weight))}${s[1] ? `, then ${esc(s[1].sector)} at ${pp(s[1].weight)}` : ""}.`);
      out.push(`That spread is like holding about ${B(n2(eff))} equally sized sectors: ${eff < 3 ? "concentrated, so one industry's bad day hits hard" : eff < 5 ? "fairly concentrated" : "well spread"}.`);
      const by = {}; hs.forEach((h) => { const k = h.sector || "Other"; (by[k] ||= []).push(h); });
      const day = Object.entries(by).map(([k, v]) => [k, wavg(v, (h) => h.day_pct)]).filter(([, v]) => !bad(v)).sort((a, b) => b[1] - a[1]);
      if (day.length > 1) out.push(`Today ${esc(day[0][0])} is doing best (${BP(day[0][1])}) and ${esc(day.at(-1)[0])} worst (${BP(day.at(-1)[1])}).`);
      return out;
    },
  },

  drift: {
    what: "How far each holding's weight now is from the bot's target, in percentage points. Prices move every day, so weights drift away from target until the bot rebalances. Amber bars are 1.5 points or more out.",
    source: () => `Weights now come from the pie readings; targets are the bot's own, from Trading 212. ${SRC.moves}`,
    live: async () => {
      const d = held().filter((h) => !bad(h.target)).map((h) => ({ h, d: h.weight - h.target })).sort((a, b) => b.d - a.d), out = [];
      if (!d.length) return ["No targets to compare yet."];
      out.push(`Most over target: ${esc(d[0].h.symbol)} ${B(pts(d[0].d), "amber")}. Most under: ${esc(d.at(-1).h.symbol)} ${B(pts(d.at(-1).d), "amber")}.`);
      const far = d.filter((x) => Math.abs(x.d) >= 0.015), turn = sum(d.map((x) => Math.abs(x.d))) / 2;
      out.push(`${B(far.length)} ${far.length === 1 ? "stock is" : "stocks are"} 1.5 points or more out. Getting everything back to target would mean moving about ${B(pp(turn))} of the pie${turn < 0.02 ? ": very close to target" : ""}.`);
      const reb = await data.rebalances(); if (reb[0]) out.push(`The bot last changed its targets on ${dShort(reb[0].ts.slice(0, 10))}; drift builds up from price moves since then.`);
      const winners = d.filter((x) => x.d > 0 && x.h.pl_pct > 0).length;
      if (d.length > 3) out.push(`${winners} of the ${d.filter((x) => x.d > 0).length} overweight stocks are in profit: weights grow when a stock rises faster than the rest.`);
      return out;
    },
  },

  rr: {
    what: () => `Each stock's return (up) against how much it swung (across) over the last ${({ 21: "month", 63: "three months", 252: "year" })[state.rrN]}. Further up is better, further right is bumpier. Bubbles are sized by weight and coloured by sector. The amber diamond is the pie's mix as it is now, the blue square the S&P 500. Dashed lines join points with the same return per unit of swing: the steeper the line a point sits on, the better the deal.`,
    source: () => `${SRC.year} Swing is the spread of daily moves (standard deviation) scaled to the period.`,
    live: async () => {
      const d = await data.rr(), n = state.rrN, S = (d.stocks || []).filter((x) => x[n]), pie = d.pie?.[n], b = (d.benchmarks || []).find((x) => x[n]), out = [];
      if (!S.length) return ["Needs a few weeks of daily prices for each stock."];
      if (pie) out.push(`Today's mix: ${BP(pie.ret, pct)} for ${pct(pie.swing).replace("+", "")} of swing, ${B(n2(pie.ret / pie.swing))} of return per unit of swing.`);
      if (b && pie) { const r = b[n].ret / b[n].swing, rp = pie.ret / pie.swing; out.push(`The ${shortBench(b.name)}: ${BP(b[n].ret, pct)} for ${pct(b[n].swing).replace("+", "")} of swing (${n2(r)} per unit). ${rp > r ? `The mix has been the ${B("better", "up")} deal for its risk.` : `The market has been the ${B("better", "down")} deal for its risk.`}`); }
      const best = [...S].sort((x, y) => y[n].ret / y[n].swing - x[n].ret / x[n].swing)[0], wild = [...S].sort((x, y) => y[n].swing - x[n].swing)[0];
      out.push(`Best return for its swing: ${esc(best.symbol)} (${pct(best[n].ret)} for ${pct(best[n].swing).replace("+", "")}). Bumpiest: ${esc(wild.symbol)} at ${pct(wild[n].swing).replace("+", "")} of swing.`);
      out.push(`${S.filter((x) => x[n].ret > 0).length} of ${S.length} stocks are up over the period.`);
      return out;
    },
  },

  qstats: {
    title: "Key risk numbers",
    what: "Sharpe: return per unit of risk. Sortino: the same but only counting falls as risk. Volatility: how much it swings in a year. Worst fall: the biggest drop from a high. Beta: how much it moves when the market moves 1%. Alpha: return the market doesn't explain, per year.",
    source: () => (early() ? "Worked out from the pie's own value every minute (its unit price), scaled up to a year, until there are five daily closes; then from daily closes. " : "Worked out from the pie's daily closing values (its unit price). ") + SRC.bench,
    live: async () => {
      const a = state.analysis, s = state.summary, v = a?.vs?.[benchKey(s)] || {}, out = [];
      if (!a?.ready) return ["Not enough history yet."];
      const sh = a.ratios.sharpe, so = a.ratios.sortino;
      out.push(`Sharpe ${B(n2(sh))}, Sortino ${B(n2(so))}${!bad(so) && !bad(sh) && so > sh * 1.3 ? ": the swings have been more up than down" : ""}. There's a ${B(p0(a.significance.prob_sharpe_positive))} chance the true Sharpe is above zero.`);
      out.push(`Volatility ${B(pp(a.risk.volatility))} a year: ${a.risk.volatility > 0.3 ? "much bumpier than the S&P 500's usual 15-20%" : a.risk.volatility > 0.2 ? "a bit bumpier than the S&P 500's usual 15-20%" : "about as calm as the S&P 500 usually is"}.`);
      if (!bad(v.beta)) out.push(`Beta ${B(n2(v.beta))}: when the ${bn()} moves 1%, the pie tends to move ${n2(v.beta)}%. Alpha ${BP(v.alpha_annual, pct)} a year, ${v.alpha_p < 0.05 ? "statistically significant" : "not yet distinguishable from luck"}.`);
      if (a.readings) out.push(`${B("Early figures:", "amber")} only ${a.readings.readings.toLocaleString("en-GB")} minute readings so far, so these numbers will move a lot.`);
      else if (a.n_days < 63) out.push(`${B("Early figures:", "amber")} only ${a.n_days} trading days so far; they settle after about three months.`);
      return out;
    },
  },

  vol: {
    title: "Implied volatility surface",
    what: "How much the options market expects a stock (or the whole pie) to move, for each strike price (across, as % of today's price) and each expiry date (depth). Higher means options are pricing bigger moves. Drag to turn it; click a slice to pick an expiry for the smile and term charts.",
    source: () => `${SRC.options} The whole-pie surface combines its US holdings' surfaces, using their weights and how they move together.`,
    live: async () => volLive(),
  },
  smile: {
    title: "Volatility smile",
    what: "One expiry's slice of the surface: implied volatility at each strike, as % of today's price. When the left side (strikes below the price) is higher, the market is paying more to protect against falls than to bet on rises.",
    source: () => SRC.options,
    live: async () => volLive(true),
  },
  term: {
    title: "Term structure",
    what: "At-the-money implied volatility for each expiry date. Upward sloping is normal (more time, more can happen). If near-dated options are higher than later ones, the market expects something soon, such as earnings.",
    source: () => SRC.options,
    live: async () => volLive(false, true),
  },

  calendar: {
    what: () => (state.calTf === "D" && !early() ? "One square per trading day, green for up and red for down, deeper for bigger moves. Hover a square for the date and return." : `The pie's return over each ${TF_NAME[state.calTf] || "period"}, one square each, a row per day. Green up, red down, deeper for bigger moves. Hover for the time and return.`),
    source: () => (early() ? "Built from the pie's own minute readings (its unit price), grouped into periods in London time." : "Built from the pie's daily closing values (its unit price)."),
    live: async () => {
      const { a } = state.quant || {}; if (!a) return ["No readings to show yet."];
      const B_ = buckets(a, state.calTf === "D" ? "D" : +state.calTf); if (!B_.length) return ["No readings to show yet."];
      const ups = B_.filter((b) => b.r > 0).length, dn = B_.filter((b) => b.r < 0).length, best = B_.reduce((x, y) => (y.r > x.r ? y : x)), worst = B_.reduce((x, y) => (y.r < x.r ? y : x));
      const unit = state.calTf === "D" ? "day" : TF_NAME[state.calTf], lbl = (b) => `${dShort(b.day)}${state.calTf === "D" ? "" : ` ${b.label}`}`;
      let run = 0, dir = 0; for (let i = B_.length - 1; i >= 0; i--) { const s = Math.sign(B_[i].r); if (!s) break; if (!dir) dir = s; if (s !== dir) break; run++; }
      return [`${B(ups, "up")} up and ${B(dn, "down")} down ${unit === "day" ? "days" : `periods of ${unit}`} (${p0(ups / (ups + dn || 1))} up).`,
        `Best: ${lbl(best)} at ${BP(best.r)}. Worst: ${lbl(worst)} at ${BP(worst.r)}.`,
        run > 1 ? `The latest ${run} in a row were ${dir > 0 ? B("up", "up") : B("down", "down")}.` : `The latest one was ${B(B_.at(-1).r >= 0 ? "up" : "down", cls(B_.at(-1).r))} (${pct2(B_.at(-1).r)}).`];
    },
  },

  dd: {
    title: "Underwater",
    what: "How far the pie is below its highest value so far, at every point. 0% means it's at a new high; the deeper the red, the bigger the fall it still has to recover.",
    source: () => (early() ? "From the pie's minute readings (its unit price), so deposits don't count as gains." : "From the pie's daily closes (its unit price), so deposits don't count as gains."),
    live: async () => {
      const st = await data.stats(), a = state.analysis, D = st.drawdown || [], out = [];
      if (!D.length) return ["No falls to show yet."];
      const cur = D.at(-1).dd, worst = D.reduce((x, y) => (y.dd < x.dd ? y : x)), lastHigh = [...D].reverse().find((p) => p.dd >= -1e-9);
      out.push(cur > -0.0005 ? `The pie is ${B("at its high", "up")} right now.` : `The pie is ${B(pct2(Math.abs(cur)).replace("+", ""), "down")} below its high${lastHigh ? `, last seen ${early() ? dayHm(lastHigh.t) : dShort(lastHigh.t)}` : ""}.`);
      out.push(`The deepest point was ${BP(worst.dd)} (${early() ? dayHm(worst.t) : dShort(worst.t)}). To get back from a fall of x%, it needs to rise a little more than x%: from ${pct2(worst.dd)} that's ${B(pct2(1 / (1 + worst.dd) - 1), "up")}.`);
      if (a?.risk?.longest_underwater_days) out.push(`The longest it has spent below a high is ${plural(a.risk.longest_underwater_days, early() ? "minute" : "trading day")}.`);
      return out;
    },
  },

  roll: {
    what: () => `The return over the last 20 ${early() ? "minutes" : "trading days"}, recalculated at every point, for the pie (amber) and the ${bn()} (blue). It shows whether the bot's recent stretch is better or worse than the market's, without one old day dominating.`,
    source: () => `${early() ? "The pie's minute readings" : "The pie's daily closes"}; ${SRC.bench}`,
    live: async () => {
      const r = state.analysis?.rolling20 || []; if (!r.length) return ["Not enough readings yet."];
      const L = r.at(-1), both = r.filter((p) => p.bench != null), ahead = both.filter((p) => p.bot > p.bench).length;
      return [`Latest 20-${early() ? "minute" : "day"} return: pie ${BP(L.bot)}${L.bench != null ? `, ${bn()} ${BP(L.bench)}: ${B(pts(L.bot - L.bench), cls(L.bot - L.bench))} ${lead(L.bot - L.bench)} the market` : ""}.`,
        both.length ? `The pie was ahead in ${B(p0(ahead / both.length))} of the ${both.length.toLocaleString("en-GB")} windows.` : "",
        `Best window ${BP(Math.max(...r.map((p) => p.bot)))}, worst ${BP(Math.min(...r.map((p) => p.bot)))}.`].filter(Boolean);
    },
  },

  corr: {
    what: "How closely each pair of holdings moves together, from −1 (opposite) through 0 (unrelated) to +1 (in step). Amber is positive, blue negative. Hover a square to see the pair. The less the stocks move together, the more the pie's ups and downs cancel out.",
    source: "The last few months of daily prices for each holding (Yahoo Finance and the pie's own readings), in pounds.",
    live: async () => {
      const c = await data.corr().catch(() => null), out = [];
      if (!c?.symbols?.length) return ["Needs about three weeks of daily prices for each holding."];
      let hi = null, lo = null;
      c.matrix.forEach((row, i) => row.forEach((r, j) => { if (j <= i || r == null) return; if (!hi || r > hi.r) hi = { i, j, r }; if (!lo || r < lo.r) lo = { i, j, r }; }));
      out.push(`Average correlation ${B(n2(c.average))}: ${c.average > 0.5 ? "the holdings mostly move together, so there's less diversification than the number of stocks suggests" : c.average > 0.25 ? "moderately linked, typical for a stock portfolio" : "loosely linked, so they spread the risk well"}.`);
      if (hi) out.push(`Most in step: ${esc(c.symbols[hi.i])} and ${esc(c.symbols[hi.j])} (${B(n2s(hi.r))}).`);
      if (lo) out.push(`Least in step: ${esc(c.symbols[lo.i])} and ${esc(c.symbols[lo.j])} (${B(n2s(lo.r))})${lo.r < 0 ? ": they tend to move in opposite directions" : ""}.`);
      return out;
    },
  },

  hist: {
    what: () => `How often the pie's ${state.histTf === "D" ? "daily" : `${TF_NAME[state.histTf]}`} returns land in each size band: green for gains, red for losses. The dashed curve is the bell curve with the same average and spread. Bars beyond the curve at the edges mean big moves happen more often than a bell curve would suggest.`,
    source: () => (early() ? "The pie's own minute readings (its unit price). The first period of each day is left out because it spans the night." : "The pie's daily closes (its unit price)."),
    live: async () => {
      const { a } = state.quant || {}; if (!a) return ["Not enough readings yet."];
      const rs = buckets(a, state.histTf === "D" ? "D" : +state.histTf).filter((b) => state.histTf === "D" || !b.first).map((b) => b.r);
      if (rs.length < 3) return ["Not enough readings for this period yet."];
      const n = rs.length, mu = sum(rs) / n, sd = Math.sqrt(sum(rs.map((x) => (x - mu) ** 2)) / (n - 1)), kurt = sum(rs.map((x) => ((x - mu) / sd) ** 4)) / n - 3, skew = sum(rs.map((x) => ((x - mu) / sd) ** 3)) / n;
      const big = rs.reduce((x, y) => (Math.abs(y - mu) > Math.abs(x - mu) ? y : x)), unit = state.histTf === "D" ? "day" : TF_NAME[state.histTf];
      return [`${n.toLocaleString("en-GB")} returns. A typical ${unit} moves about ±${B((sd * 100).toFixed(3) + "%")}, with an average of ${BP(mu, (x) => `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(4)}%`)}. ${p0(rs.filter((x) => x > 0).length / n)} were gains.`,
        `${Math.abs(kurt) < 1 ? "The shape is close to a bell curve." : kurt > 0 ? `Fatter tails than a bell curve (excess kurtosis ${n2(kurt)}): long quiet stretches broken by occasional jumps.` : "Thinner tails than a bell curve."} ${Math.abs(skew) > 0.5 ? (skew > 0 ? "The big moves lean upwards." : "The big moves lean downwards.") : ""}`,
        `The most extreme was ${BP(big)}, ${n2(Math.abs(big - mu) / sd)} times the typical move.`];
    },
  },

  month: {
    what: () => `The pie's return in each calendar month (amber) next to the ${bn()}'s (blue).`,
    source: () => `The pie's daily closes (its unit price), and ${SRC.bench.charAt(0).toLowerCase() + SRC.bench.slice(1)}`,
    live: async () => {
      const c = state.charts["month-chart"]; if (!c) return ["No months yet."];
      const L = c.data.labels, bot = c.data.datasets[0].data, mk = c.data.datasets[1].data, out = [];
      const i = L.length - 1; out.push(`${L[i]}: pie ${BP(bot[i])}, ${bn()} ${BP(mk[i])}${bad(bot[i]) || bad(mk[i]) ? "" : `: ${B(pts(bot[i] - mk[i]), cls(bot[i] - mk[i]))} ${lead(bot[i] - mk[i])} the market`}.`);
      const both = bot.map((b, k) => [b, mk[k]]).filter(([b, m]) => !bad(b) && !bad(m));
      if (both.length > 1) out.push(`Ahead of the market in ${B(`${both.filter(([b, m]) => b > m).length} of ${both.length}`)} months.`);
      else out.push("Only one month so far, so there's nothing to compare across months yet.");
      return out;
    },
  },

  moves: {
    title: "The bot's moves",
    what: () => `Each time the bot changed its targets, the pie's return from that move until the next one (amber) against the ${bn()} over the same stretch (blue). Click a bar to see what changed in that move.`,
    source: () => `${SRC.moves} Returns come from the pie's unit price and ${SRC.bench.charAt(0).toLowerCase() + SRC.bench.slice(1)}`,
    live: async () => {
      const M = state.analysis?.moves || {}, W = M.windows || [], reb = await data.rebalances(), out = [];
      out.push(`${plural(reb.length, "move")} recorded${reb[0] ? `, the latest on ${dShort(reb[0].ts.slice(0, 10))}` : ""}.`);
      if (W.length) {
        out.push(`${B(p0(M.win_rate))} of the moves made money before the next one, and ${B(p0(M.beat_bench_rate))} beat the ${bn()} over the same stretch.`);
        const best = W.reduce((x, y) => (y.bot - y.bench > x.bot - x.bench ? y : x)); out.push(`Best move against the market: ${dShort(best.from.slice(0, 10))}, ${BP(best.bot, pct)} against ${pct(best.bench)}.`);
      } else out.push("Results show here once there's a next move to measure up to (or once there are daily closes).");
      const j = reb.filter((r) => r.judged);
      if (j.length) out.push(`${B(`${j.filter((r) => r.judged.value_added > 0).length} of ${j.length}`)} ${j.length === 1 ? "move has" : "moves have"} beaten simply keeping the old mix.`);
      return out;
    },
  },

  move: {
    title: "The selected move",
    what: "What the bot changed in one move: stocks bought in, sold out, raised or trimmed, with the target weight before and after. 'Did it help?' compares the new mix with what the old mix would have made since. Headlines from around the move are below.",
    source: () => `${SRC.moves} The comparison uses each stock's price in pounds (minute prices where available, otherwise daily closes), with weights held as the bot set them.`,
    live: async () => {
      const reb = await data.rebalances(), r = [...reb].reverse()[state.move ?? reb.length - 1], out = [];
      if (!r) return ["The bot hasn't changed its targets yet."];
      const ch = r.changes || [], add = ch.filter((x) => x.kind === "added"), rem = ch.filter((x) => x.kind === "removed");
      out.push(`On ${dShort(r.ts.slice(0, 10))} at ${hm(r.ts)} the bot made ${plural(ch.length, "change")}${add.length ? `, buying into ${listNames(add)}` : ""}${rem.length ? `, selling out of ${listNames(rem)}` : ""}.`);
      const big = [...ch].sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))[0];
      if (big) out.push(`The biggest change: ${esc(big.symbol)} from ${pp(big.before)} to ${pp(big.after)}.`);
      const j = r.judged; if (j) out.push(`Since then the new mix did ${BP(j.new_mix)} against ${BP(j.old_mix)} for the old one: ${B(j.value_added >= 0 ? "it helped" : "it hasn't helped yet", cls(j.value_added))}, ${pts(j.value_added)}${j.open ? " so far" : ""}.`);
      return out;
    },
  },

  risk: {
    title: "All the numbers",
    what: "Every statistic the tracker works out. Highlights: 'Bad day, 1 in 20' is the loss you'd expect to beat on 19 days out of 20; 'share of gains caught / falls taken' compare the pie with the market on its up and down days; 'days needed to be sure' is how long a record this good needs before the Sharpe ratio is convincingly above zero.",
    source: () => (early() ? "The pie's minute readings (its unit price), scaled to a year where needed. " : "The pie's daily closes (its unit price). ") + SRC.bench,
    live: async () => {
      const a = state.analysis, v = a?.vs?.[benchKey(state.summary)] || {}, out = [];
      if (!a?.ready) return ["Not enough history yet."];
      const u = early() ? "minute" : "day";
      out.push(`${p0(a.returns.pct_up_days)} of ${u}s were up; the average up ${u} was ${BP(a.returns.avg_up_day)} and the average down ${u} ${BP(a.returns.avg_down_day)}.`);
      out.push(`On 19 ${u}s in 20 the loss should be smaller than ${B(pct2(-a.risk.var95_daily), "down")}; the worst 5% average ${B(pct2(-a.risk.cvar95_daily), "down")}.`);
      if (!bad(v.up_capture)) out.push(`It caught ${B(p0(v.up_capture))} of the ${bn()}'s gains and took ${B(p0(v.down_capture))} of its falls${v.up_capture > v.down_capture ? ": more of the ups than the downs, which is what you want" : ""}.`);
      if (!bad(a.significance.min_track_record_days)) out.push(`To be 95% sure the Sharpe ratio is really above zero needs about ${B(Math.round(a.significance.min_track_record_days).toLocaleString("en-GB"))} ${u}s of this kind of record; it has ${a.n_days.toLocaleString("en-GB")}.`);
      return out;
    },
  },

  beta: {
    title: "Rolling beta and alpha",
    what: () => `Beta (blue): how much the ${state.deepSrc.rb === "pie" ? "pie" : "pie's current mix"} moves when the ${bn()} moves 1%, measured over a moving window, with its 95% range shaded. Above 1 it amplifies the market; below 1 it dampens it. Alpha (amber, right axis): return the market doesn't explain, as a yearly rate.`,
    source: () => (state.deepSrc.rb === "pie" ? `The pie's own minute readings against the ${bn()}'s minute prices. ${SRC.bench}` : `${SRC.year} The current weights are held through the year ("today's mix"), compared with the ${bn()} over moving three-month windows.`),
    live: async () => {
      const q = state.deep, R = (state.deepSrc.rb === "pie" ? q?.pie_rolling : q?.rolling) || []; if (R.length < 3) return ["Not enough data yet."];
      const L = R.at(-1), F = R[0];
      return [`Beta now ${B(n2(L.beta))} (95% range ${n2(L.lo)} to ${n2(L.hi)}): a 1% move in the ${bn()} has come with about ${n2(L.beta)}% in the pie. ${L.lo > 1 ? "Clearly more than the market." : L.hi < 1 ? "Clearly less than the market." : "Not clearly different from 1."}`,
        `It started this chart at ${n2(F.beta)}, so sensitivity to the market has ${L.beta > F.beta + 0.1 ? "risen" : L.beta < F.beta - 0.1 ? "fallen" : "stayed about the same"}.`,
        `Alpha at this pace: ${BP(L.alpha, pct)} a year${state.deepSrc.rb === "pie" ? ", scaled up from minutes, so treat it as noise for now" : ""}.`];
    },
  },

  factors: {
    title: "What kind of fund is it?",
    what: "How strongly the pie's current mix leans towards common investing styles: the market as a whole, small companies, cheap 'value' stocks, fast-growing 'growth' stocks, recent winners (momentum), high-quality companies and low-volatility stocks. Each bar is the exposure with its 95% range; coloured bars are clearly different from zero.",
    source: () => `${SRC.year} Its daily moves are compared with style funds (SPY, IWM, IWD, IWF, MTUM, QUAL, USMV) in one regression.`,
    live: async () => {
      const f = state.deep?.factors; if (!f) return ["Needs a few months of prices for the style funds."];
      const sig = f.factors.filter((x) => x.lo > 0 || x.hi < 0), out = [];
      out.push(`These styles explain ${B(p0(f.r2))} of the mix's daily moves${f.r2 < 0.5 ? ", so much of what it does is specific to its stocks" : ""}.`);
      out.push(sig.length ? `Clear leanings: ${sig.map((x) => `${B(esc(x.name), x.beta > 0 ? "up" : "down")} (${n2s(x.beta)})`).join(", ")}.` : "No style stands out clearly yet.");
      out.push(`What's left over (alpha) is ${BP(f.alpha, pct)} a year, with a range of ${pct(f.alpha_lo)} to ${pct(f.alpha_hi)}${f.alpha_lo > 0 ? ": clearly positive" : ": too wide to call"}.`);
      return out;
    },
  },

  options: {
    title: "Options' fear against reality",
    what: "Each bubble is a stock: across is how much it actually moved over the last month, up is how much its options expect it to move over the next month (both as yearly volatility). Above the dashed line, options price in more fear than recent reality; below it, more calm. The amber diamond is the whole mix.",
    source: () => `${SRC.options} Realised movement comes from the last month of daily prices.`,
    live: async () => {
      const o = state.deep?.options, S = o?.stocks || []; if (S.length < 2) return ["Waiting for option prices for the US holdings."];
      const fear = S.filter((x) => x.iv > x.rv), big = [...S].sort((a, b) => b.iv / b.rv - a.iv / a.rv)[0], calm = [...S].sort((a, b) => a.iv / a.rv - b.iv / b.rv)[0], out = [];
      out.push(`${B(`${fear.length} of ${S.length}`)} stocks have options expecting bigger moves than they've just had.`);
      if (o.pie?.iv) out.push(`For the whole mix, options expect ${B(pp(o.pie.iv))} against ${B(pp(o.pie.rv))} actually moved: ${o.pie.iv > o.pie.rv ? "the market expects things to get bumpier" : "the market expects things to calm down"}.`);
      out.push(`Most fear relative to reality: ${esc(big.symbol)} (expects ${pp(big.iv)}, moved ${pp(big.rv)}). Most calm: ${esc(calm.symbol)} (expects ${pp(calm.iv)}, moved ${pp(calm.rv)}).`);
      return out;
    },
  },

  acf: {
    title: "Do wild moves come in bunches?",
    what: "Bars show how much a move is echoed 1 to 20 periods later. Amber: the moves themselves (outside the dashed band means some pattern in the direction). Blue: the size of the moves (outside the band means calm and wild spells come in bunches, which is common in markets).",
    source: () => (state.deepSrc.acf === "pie" ? "The pie's own minute readings (within each session; the overnight gap isn't counted as a move)." : SRC.year),
    live: async () => {
      const r = deepReturns(state.deepSrc.acf); if (r.length < 40) return ["Not enough returns yet."];
      const K = 20, acf = (x) => { const mm = mean_(x), dd = x.map((v) => v - mm), dn = sum(dd.map((v) => v * v)) || 1e-12; return Array.from({ length: K }, (_, k) => dd.slice(k + 1).reduce((s, v, i) => s + v * dd[i], 0) / dn); };
      const a1 = acf(r), a2 = acf(r.map((v) => v * v)), band = 1.96 / Math.sqrt(r.length), o1 = a1.filter((v) => Math.abs(v) > band).length, o2 = a2.filter((v) => Math.abs(v) > band).length, unit = state.deepSrc.acf === "pie" ? "minute" : "day";
      return [`${B(o1)} of ${K} direction echoes are outside the band (about 1 would happen by chance): ${o1 <= 2 ? "no real pattern in which way it moves next" : "some pattern in direction, often from prices updating in steps"}.`,
        `${B(o2)} of ${K} size echoes are outside the band: ${o2 >= 4 ? "big moves do come in bunches" : "no strong bunching of big moves yet"}.`,
        `The 1-${unit} echo is ${B(n2s(a1[0]))}${a1[0] < -band ? ": a move tends to be partly reversed the next " + unit : a1[0] > band ? ": a move tends to continue the next " + unit : ""}.`];
    },
  },

  "s-price": {
    title: () => `${state.stockData?.symbol || ""} price`,
    what: "The share price over the chosen range, in the stock's own currency. Green triangles mark the bot's buys and red ones its sells. On 1D it shows today's New York session minute by minute, with today's open dashed.",
    source: "Prices from Yahoo Finance (minute prices for 1D, daily closes otherwise); trades from Trading 212's order history.",
    live: async () => {
      const d = state.stockData, c = state.charts["s-chart"]; if (!d || !c) return ["No prices yet."];
      const P = c.data.datasets[0].data, ccy = state.srange === 1 ? d.profile?.currency : d.prices?.length ? d.profile?.currency : "GBP"; if (P.length < 2) return ["No prices yet."];
      const chg = P.at(-1).y / P[0].y - 1, hi = Math.max(...P.map((p) => p.y)), lo = Math.min(...P.map((p) => p.y));
      const buys = c.data.datasets.find((x) => x.label === "Bot bought")?.data.length || 0, sells = c.data.datasets.find((x) => x.label === "Bot sold")?.data.length || 0;
      return [`Over this range ${esc(d.symbol)} is ${BP(chg)}, from ${money(P[0].y, ccy)} to ${B(money(P.at(-1).y, ccy))}.`,
        `Range: low ${money(lo, ccy)}, high ${money(hi, ccy)}; it's ${pct(P.at(-1).y / hi - 1)} from the high.`,
        buys + sells ? `The bot traded it ${plural(buys + sells, "time")} in this window (${buys} buys, ${sells} sells).` : "The bot didn't trade it in this window."];
    },
  },
  "s-analysts": {
    title: "Analysts",
    what: "Analysts' consensus rating (the needle: left is strong buy, right is sell), the gap to their average 12-month target, and where today's price sits between the lowest and highest target.",
    source: () => SRC.yahoo,
    live: async () => {
      const a = state.stockData?.analyst; if (!a) return ["No analyst data for this stock."];
      return [`Consensus ${B(esc(a.rating || "no rating"))}${bad(a.score) ? "" : ` (score ${n2(a.score)} on a 1 to 5 scale)`}${a.analysts ? ` from ${plural(a.analysts, "analyst")}` : ""}${a.thin ? ": thin coverage, so take it lightly" : ""}.`,
        bad(a.upside) ? "" : `The average target is ${BP(a.upside, p0s)} from today's price.`,
        a.low && a.price ? (a.price < a.low ? "Today's price is below even the lowest target." : a.price > a.high ? "Today's price is above even the highest target." : `Today's price sits between the lowest (${money(a.low, a.currency)}) and highest (${money(a.high, a.currency)}) targets.`) : ""].filter(Boolean);
    },
  },
  "s-position": {
    title: "In the pie",
    what: "This stock's place in the pie: its value, its weight against the bot's target, how many shares, the profit now and over its whole time in the pie, and dividends received.",
    source: () => `${SRC.pie} Trades and dividends come from Trading 212's history.`,
    live: async () => {
      const d = state.stockData, p = d?.position || {}; if (!d) return [];
      if (!d.held_now) return [`The bot has sold out of ${esc(d.symbol)}. Over its time in the pie it made ${B(G(gbpS(p.lifetime_pl)), cls(p.lifetime_pl))}.`];
      const gap = p.weight - p.target;
      return [`${esc(d.symbol)} is worth ${B(G(gbp(p.value)))}, ${pp(p.weight)} of the pie against a ${pp(p.target)} target (${pts(gap)}).`,
        `Profit now ${B(G(gbpS(p.unrealised_pl)), cls(p.unrealised_pl))}; over its whole time in the pie ${B(G(gbpS(p.lifetime_pl)), cls(p.lifetime_pl))}.`];
    },
  },
  "s-vol": {
    title: "Implied volatility surface",
    what: "How much the options market expects this stock to move, by strike price and expiry. Higher means bigger expected moves.",
    source: () => SRC.options,
    live: async () => volLive(false, false, state.stock),
  },
  "s-stats": {
    title: "Key numbers",
    what: "The company's valuation (what you pay for its earnings, assets and cash flow), how its business is growing and how profitable it is, and how its shares trade.",
    source: () => SRC.yahoo,
    live: async () => {
      const p = state.stockData?.profile || {}, out = [];
      if (!bad(p.trailingPE)) out.push(`Price / earnings ${B(n2(p.trailingPE) + "×")}${!bad(p.forwardPE) ? `, falling to ${n2(p.forwardPE)}× on next year's expected earnings${p.forwardPE < p.trailingPE ? " (earnings are expected to grow)" : ""}` : ""}.`);
      if (!bad(p.revenueGrowth)) out.push(`Revenue is growing ${BP(p.revenueGrowth, pct)} a year${!bad(p.profitMargins) ? ` and it keeps ${pp(p.profitMargins)} of revenue as profit` : ""}.`);
      if (p.fiftyTwoWeekHigh) { const px = p.currentPrice ?? p.regularMarketPrice; if (px) out.push(`The share price is ${BP(px / p.fiftyTwoWeekHigh - 1, pct)} from its 52-week high.`); }
      if (!bad(p.beta)) out.push(`Beta ${B(n2(p.beta))}: it has moved about ${n2(p.beta)}% for each 1% move in the market.`);
      return out.length ? out : ["No company numbers available."];
    },
  },
  "s-weight": {
    title: "Weight in the pie",
    what: "This stock's share of the pie over time. Steps up or down are the bot buying or selling; slopes between steps come from its price moving against the rest.",
    source: () => SRC.pie,
    live: async () => {
      const w = (state.stockData?.weight_history || []).filter((x) => x.weight != null); if (w.length < 2) return ["Not enough history yet."];
      const hi = w.reduce((a, x) => (x.weight > a.weight ? x : a));
      return [`From ${pp(w[0].weight)} on ${dShort(w[0].t)} to ${B(pp(w.at(-1).weight))} now.`, `Its biggest weight was ${pp(hi.weight)} on ${dShort(hi.t)}.`];
    },
  },
  "s-trades": {
    title: "The bot's trades",
    what: "Every buy and sell the bot made in this stock, newest first.",
    source: "Trading 212's order history for the pie.",
    live: async () => {
      const t = state.stockData?.trades || []; if (!t.length) return ["No trades recorded."];
      const b = t.filter((x) => x.side === "BUY"), s = t.filter((x) => x.side === "SELL");
      return [`${plural(b.length, "buy")} (${G(gbp(sum(b.map((x) => x.gbp))))}) and ${plural(s.length, "sell")} (${G(gbp(sum(s.map((x) => x.gbp))))}).`, `The latest: ${t.at(-1).side === "BUY" ? "bought" : "sold"} on ${dShort(t.at(-1).t.slice(0, 10))}.`];
    },
  },
  "s-news": {
    title: "News",
    what: "Recent headlines about this company, newest first.",
    source: "Yahoo Finance's news feed for the stock.",
    live: async () => {
      const n = state.stockData?.news || []; if (!n.length) return ["No headlines."];
      const day = n.filter((x) => x.published && Date.now() - new Date(x.published).getTime() < 864e5).length;
      return [`${B(day)} headlines in the last 24 hours${n[0].published ? `; the newest ${ago(n[0].published)}` : ""}.`];
    },
  },
};

/* the option surfaces: shared by the surface, smile and term explanations */
async function volLive(smile = false, term = false, ticker = state.volTicker) {
  let d; try { d = await volData(ticker); } catch { d = null; }
  if (!d?.available || !window.VolSurface) return ["No option prices available right now, and none saved from earlier yet."];
  const { M, E, Z } = VolSurface.prep(d), atm = M.findIndex((m) => Math.abs(m - 1) < 1e-6), out = [];
  if (d.stale) out.push(`${B("Last known prices:", "amber")} Yahoo has no live option prices right now, so this shows ${d.pie ? `the last ones it had for ${plural(d.stale_count, "stock")} (oldest` : "the last ones it had (from"} ${dayHm(d.as_of)}). It switches to live prices on its own when they're back; the tracker checks every 10 minutes.`);
  const k30 = E.reduce((b, e, i) => (Math.abs(e.days - 30) < Math.abs(E[b].days - 30) ? i : b), 0);
  const name = ticker === "PIE" ? "the pie's US stocks" : esc(sym(ticker));
  const a30 = Z[k30][atm];
  if (!bad(a30)) out.push(`Options on ${name} expect about ${B(p0(a30))} a year of movement over the next ${VolSurface.dayLabel(E[k30].days)}: roughly ${B(`±${(a30 * Math.sqrt(E[k30].days / 365) * 100).toFixed(1)}%`)} by then, two times in three.`);
  const real = ticker === "PIE" ? state.deep?.options?.pie?.rv : state.deep?.options?.stocks?.find((x) => x.ticker === ticker || x.symbol === sym(ticker))?.rv;
  if (!bad(a30) && (a30 < (ticker === "PIE" ? 0.06 : 0.1) || (!bad(real) && a30 < real / 3))) out.push(`${B("Treat this with caution:", "amber")} ${p0(a30)} is unusually low${bad(real) ? "" : ` next to the ${p0(real)} it has really been moving`}. Yahoo's option quotes are sometimes thin or stale, which drags the implied volatility down; the shape of the surface is more trustworthy than its level right now.`);
  const first = Z[0][atm], last = Z.at(-1)[atm];
  if (!smile && !bad(first) && !bad(last)) out.push(`Nearest expiry ${p0(first)}, furthest ${p0(last)}: ${first > last * 1.05 ? `${B("downward sloping", "amber")}, so the market expects a bumpy spell soon (often earnings or news)` : first < last * 0.95 ? "upward sloping, the normal shape: more time, more can happen" : "fairly flat across expiries"}.`);
  const i90 = M.findIndex((m) => Math.abs(m - 0.9) < 1e-6), i110 = M.findIndex((m) => Math.abs(m - 1.1) < 1e-6);
  if (!term && i90 >= 0 && i110 >= 0) { const sk = Z[k30][i90] - Z[k30][i110]; if (!bad(sk)) out.push(`Skew at ${VolSurface.dayLabel(E[k30].days)}: protection against a 10% fall costs ${B(pts(sk))} more volatility than a bet on a 10% rise${sk > 0.05 ? ", a fairly fearful smile" : sk < 0 ? ", unusual: upside is in more demand" : ""}.`); }
  return out;
}

/* ---------- open, close, animate ---------- */
const titleOf = (el, ex) => { const t = typeof ex?.title === "function" ? ex.title() : ex?.title; return t || el.querySelector("h2")?.textContent?.trim() || "Detail"; };
const html = (x) => (typeof x === "function" ? x() : x || "");
async function renderExplain(first = false) {
  const el = FX.el; if (!el) return;
  const ex = EXPLAIN[FX.key] || {}, side = $("#focus-side"), tok = ++FX.token, top = side.scrollTop;
  let live;
  try { live = ex.live ? (await ex.live()).filter(Boolean) : []; } catch (e) { console.error(e); live = ["The live numbers for this aren't available right now."]; }
  if (tok !== FX.token || !FX.open) return;
  const s = state.summary, when = s?.as_of ? `${s.stale ? "Last reading" : "Live, updated"} ${dayHm(s.as_of)}` : "";
  side.innerHTML = `<p class="focus-kicker">${pieName()}</p><h2 id="focus-title">${esc(titleOf(el, ex))}</h2>
    <section class="fx-sec"><h3>What it shows</h3><p>${html(ex.what) || "One of the tracker's views of the pie."}</p></section>
    <section class="fx-sec"><h3>Where the numbers come from</h3><p>${html(ex.source) || SRC.pie}</p></section>
    <section class="fx-sec fx-live"><h3><i class="dot ${s?.stale ? "warn" : ""}"></i>What it says right now</h3>${live.length ? `<ul>${live.map((x) => `<li>${x}</li>`).join("")}</ul>` : `<p class="muted">Nothing to read from it yet.</p>`}${when ? `<p class="fx-asof">${when}</p>` : ""}</section>`;
  side.classList.toggle("enter", first);
  side.scrollTop = first ? 0 : top;
}
function focusRefreshed() { if (FX.open) renderExplain(); }

function openFocus(el) {
  if (FX.open || FX.anim) return;
  const key = el.dataset.focus, r = el.getBoundingClientRect();
  FX.open = true; FX.el = el; FX.key = key; FX.lastFocus = document.activeElement;
  hidePeek();
  // a placeholder holds the block's place in the page while it's away
  const ph = document.createElement("div");
  ph.className = `focus-ph ${[...el.classList].filter((c) => /^(s\d+|r2|card)$/.test(c)).join(" ")}`;
  ph.style.height = `${r.height}px`;
  el.before(ph); FX.ph = ph;
  const ov = $("#focus"), panel = $("#focus-panel"), stage = $("#focus-stage");
  ov.hidden = false;
  document.documentElement.classList.add("focus-lock");
  stage.append(el);
  el.classList.add("in-focus");
  renderExplain(true);
  // re-read the live text when anything inside changes (a toggle, a new range, fresh data)
  // (hover read-outs, the chart's scrub label and the 3D surface's tooltip don't count)
  const QUIET = ".scrub-date, .surface, #cal-meta, #corr-meta, #hist-meta, #hero-label, #hero-value, #hero-sub, #stats, .peek";
  FX.mo = new MutationObserver((recs) => {
    if (recs.every((m) => (m.target.nodeType === 1 ? m.target : m.target.parentElement)?.closest(QUIET))) return;
    clearTimeout(FX.moT); FX.moT = setTimeout(() => renderExplain(), 160);
  });
  FX.mo.observe(stage, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["aria-pressed", "hidden"] });
  if (state.charts) requestAnimationFrame(() => Object.values(state.charts).forEach((c) => { if (c?.canvas && el.contains(c.canvas)) c.resize(); }));
  $("#focus-close").focus({ preventScroll: true });
  if (still) return;
  // grow from where the block sat in the page
  const f = panel.getBoundingClientRect(), cl = (v) => Math.max(0, v);
  const from = `inset(${cl(r.top - f.top)}px ${cl(f.right - r.right)}px ${cl(f.bottom - r.bottom)}px ${cl(r.left - f.left)}px round 16px)`;
  FX.anim = panel.animate([{ clipPath: from, opacity: 0.6 }, { clipPath: "inset(0px 0px 0px 0px round 22px)", opacity: 1 }], { duration: 520, easing: EASE });
  $("#focus-back").animate([{ opacity: 0 }, { opacity: 1 }], { duration: 320, easing: "ease-out" });
  stage.animate([{ transform: "scale(.985)", opacity: 0.4 }, { transform: "none", opacity: 1 }], { duration: 520, easing: EASE });
  FX.anim.onfinish = FX.anim.oncancel = () => { FX.anim = null; };
}

function closeFocus(instant = false) {
  if (!FX.open) return;
  const el = FX.el, ph = FX.ph, ov = $("#focus"), panel = $("#focus-panel");
  const done = () => {
    FX.mo?.disconnect(); clearTimeout(FX.moT);
    el.classList.remove("in-focus");
    ph.replaceWith(el);
    ov.hidden = true;
    document.documentElement.classList.remove("focus-lock");
    $("#focus-side").innerHTML = "";
    FX.open = false; FX.el = FX.ph = FX.key = null; FX.anim = null; FX.token++;
    requestAnimationFrame(() => { Object.values(state.charts || {}).forEach((c) => { if (c?.canvas && el.contains(c.canvas)) c.resize(); }); if (state.view === "overview") renderMap(); });
    if (!instant) (FX.lastFocus && document.contains(FX.lastFocus) ? FX.lastFocus : el).focus?.({ preventScroll: true });
  };
  FX.anim?.cancel();
  if (instant || still) { done(); return; }
  const r = ph.getBoundingClientRect(), f = panel.getBoundingClientRect(), cl = (v) => Math.max(0, v);
  const to = `inset(${cl(r.top - f.top)}px ${cl(f.right - r.right)}px ${cl(f.bottom - r.bottom)}px ${cl(r.left - f.left)}px round 16px)`;
  FX.anim = panel.animate([{ clipPath: "inset(0px 0px 0px 0px round 22px)", opacity: 1 }, { clipPath: to, opacity: 0 }], { duration: 380, easing: "cubic-bezier(.4, 0, .6, 1)", fill: "forwards" });
  $("#focus-back").animate([{ opacity: 1 }, { opacity: 0 }], { duration: 380, easing: "ease-in", fill: "forwards" });
  FX.anim.onfinish = () => { FX.anim.cancel(); $("#focus-back").getAnimations().forEach((a) => a.cancel()); done(); };
}

/* ---------- wiring ---------- */
function canvasHit(e) {   // a click on a chart's bar or point that the chart itself handles (opens a stock, picks a move)
  if (e.target.tagName !== "CANVAS") return false;
  const ch = Object.values(state.charts || {}).find((c) => c?.canvas === e.target);
  return !!(ch?.options?.onClick && ch.getElementsAtEventForMode(e, "nearest", { intersect: true }, false).length);
}
document.addEventListener("click", (e) => {
  if (FX.open) return;
  const el = e.target.closest("[data-focus]");
  if (!el || el.closest("#focus") || e.target.closest(FOCUS_SKIP) || canvasHit(e)) return;
  if (String(getSelection?.() || "").trim()) return;   // selecting text, not opening
  openFocus(el);
});
$$("[data-focus]").forEach((el) => {
  el.classList.add("focusable");
  el.tabIndex = 0;
  el.setAttribute("aria-haspopup", "dialog");
  el.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === el) { e.preventDefault(); openFocus(el); } });
});
$$(".focus-btn").forEach((b) => b.addEventListener("click", () => openFocus(b.closest("[data-focus]"))));
$("#focus-back").addEventListener("click", () => closeFocus());
$("#focus-close").addEventListener("click", () => closeFocus());
addEventListener("keydown", (e) => {
  if (!FX.open) return;
  if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeFocus(); }
  if (e.key === "Tab") {   // keep the keyboard inside the window
    const f = $$("#focus-panel a[href], #focus-panel button:not([disabled]), #focus-panel input, #focus-panel select, #focus-panel [tabindex='0']").filter((x) => x.offsetParent);
    if (!f.length) return;
    if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f.at(-1).focus(); }
    else if (!e.shiftKey && document.activeElement === f.at(-1)) { e.preventDefault(); f[0].focus(); }
  }
}, true);

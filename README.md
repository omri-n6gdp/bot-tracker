# Bot tracker

A private website where friends can follow the trading bot's Trading 212 pie live: its value,
its return against the S&P 500, what it holds, and every move the bot makes. Only the bot's pie is
shown, never the rest of the ISA. The tracker is read-only: it cannot place or change orders,
and it doesn't touch the bot.

## Run it on your Mac

```bash
cd "Bot Tracker"
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

python -m tracker serve --demo      # demo data → open http://127.0.0.1:8212
```

With a working API key:

```bash
python -m tracker check             # tests the key (prints no secrets)
python -m tracker find-pie          # lists your pies with their ids
cp .env.example .env                # then set T212_PIE_ID=<the bot pie's id>
python -m tracker once              # one snapshot, to check the numbers against the app
python -m tracker poll              # keep polling (leave running in one terminal)
python -m tracker serve             # the website, in another terminal
```

The key and secret are read from the `API Keys` file (comment line, then key, then secret),
or from `T212_API_KEY` / `T212_API_SECRET` in `.env`. Both files are git-ignored.

The key needs these **read** permissions: account data, portfolio, history, pies. It doesn't
need order permissions. If you lock it to an IP address, that has to be the server's.

## One-click start (Dock app)

`Bot Tracker.app` in this folder runs `python -m tracker restart`: it stops any running
poller and website, starts both again in the background, waits for the site and opens it. The site runs on its own port, http://127.0.0.1:8212, and the launcher only ever stops this tracker's own processes: another program on that port is left running and the site moves to the next free port.
Drag it into the Dock and click it whenever you want the tracker running with the latest
code and settings. Logs are in `data/logs/`. The first time, macOS may say it can't check
the app: right-click it, choose Open, then Open again.

## Pages

- **Overview**: the value and key statistics, a performance chart you can drag across to see any past day, the pie as a heat map (by today, since bought, or analyst upside), movers, news and the analysts' view.
- **Holdings**: the table (click a column to sort, a row for detail, a sector to filter), gains and losses per holding, analyst price targets on one shared scale, sectors and drift from target.
- **Quant**: the implied volatility surface for any US holding with its smile and term structure, a calendar of every trading day, drawdown, rolling returns, how the holdings move together, the distribution of daily returns, months, and every move the bot made.
- **A stock** (search with `/`, or click any ticker): price with the bot's trades, the analyst gauge and target range, the position, its volatility surface, key numbers, trades and news.

The eye button hides every £ amount, for showing the page to someone without showing the sums.

Also on the Overview:

- **Today's recap**: the day in a few lines (since the open against the S&P, best and worst stock, any move, the game). Saved at the 21:30 close and sent to Telegram when `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set (use a group's chat id so friends get it).
- **Guess the week**: friends guess the pie's Monday-to-Friday return. Guesses close at Monday's New York open and stay hidden until then; closest wins; there's a leaderboard. Players are listed in `.env` as `PLAYERS=Name,Name,...` (kept out of the code). This is the only thing visitors can write, into the tracker's own database.
- **Where it could go**: a Monte Carlo range for the next month, three months or year, from how today's stocks have moved over the past year, with no trend assumed.

On the Quant page, each of the bot's moves shows **did it help?** (the new mix against keeping the old one, since the move) and **headlines from around the move**.

Until the pie has 5 daily closes, the statistics, return and charts are worked out from its readings every minute (⚠️ marks numbers estimated from too little data; hover for why).

## Deposits (net put in)

`deposits.csv` lists money you paid into the bot from outside, one row per deposit
(negative for withdrawals). It drives "net deposits", "profit" and the deposits line on
the £ chart. It does **not** affect the return %: that's the unit price, worked out from
the bot's own trades, so reinvested profits and money rolled over from an older pie never
count as gains.

```csv
date,amount,note
2026-06-09,400,first deposit
```

## Switching to another pie

Each pie keeps its own database, so switching never mixes histories. In `.env`:

```bash
T212_PIE_ID=12345678                 # from python -m tracker find-pie
TRACKER_DB=my-pie.db       # data/<this file>; the old pie's data/tracker.db is left alone
DEPOSITS_CSV=deposits-my-pie.csv
BOT_START_DATE=2026-11-12
```

Another pie can hold the same stocks. Trades only count for this pie when its own share
counts moved by those amounts between snapshots, and dividends are split by this pie's
share of the account's holding, so the other pie's activity never leaks in. For the same
reason, don't run `backfill` on a pie that shares stocks with another one: start it
fresh and let the poller record it from day one.

## Fill in the history

```bash
python -m tracker backfill              # rebuild the pie's past from order history + prices
python -m tracker backfill --use-cache  # same, reusing the order history already downloaded
```

## How it works

| Piece | What it does |
| --- | --- |
| `tracker/t212.py` | Read-only Trading 212 client: GET endpoints only, retries, spacing between calls (the bot shares the rate limits) |
| `tracker/poller.py` | Every 60 s it saves a pie snapshot; it also handles cash, names, dividends, the benchmark, and a 21:30 daily close |
| `tracker/rebalance.py` | Spots the bot's moves: a change in the pie's target weights (`expectedShare`) = a rebalance |
| `tracker/engine.py` + `stats.py` | Unit price, returns, drawdown, volatility, Sharpe, monthly returns, move win rate |
| `tracker/service.py` + `api.py` | Read-only JSON API + the dashboard |
| `web/` | The site: Overview, Holdings, Moves, Stats, About. No external requests (fonts and Chart.js are bundled) |
| `tracker/demo.py` | Synthetic data for building and showing the site |

**Backup path.** Trading 212 has marked the pie endpoints as deprecated. If they stop
working, the poller switches itself to backup pricing: the last known pie quantities × live
prices from `/positions`. You get a Telegram alert when that happens.

**Privacy.** Positions for the rest of your ISA are read only to price the bot's tickers and
are filtered out before anything is stored. A test fails if any non-pie ticker reaches the API.

## Tests

```bash
pytest -q
```

## Put it online (free, always on)

The server runs three small containers: the poller, the website, and a Cloudflare tunnel.
The tunnel connects outwards, so the server opens no ports, and Cloudflare Access asks your
friends for a one-time code sent to their email before they can see anything.

1. **Get a free server.** Oracle Cloud Always Free: create an instance with shape
   `VM.Standard.A1.Flex` (1 OCPU, 6 GB is plenty), image Ubuntu 24.04, and download the SSH
   key it offers. If it says "out of capacity", try again later or pick another availability
   domain. Fallback: Google Cloud `e2-micro` in us-central1 with a standard disk and no static IP.
2. **Let Trading 212 accept it.** If your API key is limited to certain IP addresses, add the
   server's public IP in Trading 212 (Settings → API).
3. **Make the tunnel.** Put a domain on Cloudflare (any cheap one). In Zero Trust → Networks →
   Tunnels, create a tunnel (Cloudflared), copy its token into `.env` as `TUNNEL_TOKEN=...`, and
   add a public hostname such as `bot.yourdomain.com` pointing to service `http://web:8000`.
4. **Lock it to your friends.** Zero Trust → Access → Applications → Add → Self-hosted, for that
   hostname. Policy: Allow, Emails = you and your friends. Login method: One-time PIN.
5. **Push it**, from this folder on the Mac:
   `deploy/push.sh ubuntu@SERVER_IP ~/Downloads/ssh-key.key`
   This stops the tracker on the Mac, copies the code, `.env`, `API Keys` and (the first time)
   your history, installs Docker on the server and starts everything. Run it again after any
   change to update the server. Add `PUBLIC_URL=https://bot.yourdomain.com` to `.env` and the
   Dock button opens the online site instead of starting a second copy on the Mac.

On the server: `sudo docker compose -f deploy/docker-compose.yml logs -f poller` shows the
poller, and `deploy/backup.sh` runs nightly into `backups/` (30 days kept).

## Status against the roadmap

- [x] Phase 1: Trading 212 data layer (client, poller, backup path)
- [x] Phase 2: database
- [x] Phase 3: performance engine + tests
- [x] Phase 4: read-only API
- [x] Phase 5: dashboard
- [x] Phase 6: deployment files (Docker, tunnel, backups, alerts). The server and accounts are yours to create.
- [ ] Phase 0: working API key, pie id, contributions log ← needed to go live
- [ ] Phase 7: a week of private running, checking the site against the app

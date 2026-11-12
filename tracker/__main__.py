"""Command line: python -m tracker <command>

  check        test the API key (read-only call, prints no secrets)
  find-pie     list your pies with IDs, so you can set T212_PIE_ID
  once         run one full poll cycle and recompute
  poll         run the poller forever (Ctrl+C to stop)
  serve        run the website (add --demo to use demo data)
  demo         create demo data in data/demo.db
  rebuild      recompute daily values from stored snapshots
  backfill     rebuild the bot pie's past from order history + market prices
  restart      stop any running poll/serve, start both in the background, open the site
               (what the "Bot Tracker" Dock app runs)
"""
from __future__ import annotations

import argparse
import logging
import sys

from .config import get_settings, load_credentials


def _client(cfg):
    from .t212 import T212Client
    return T212Client(load_credentials(), base_url=cfg.base_url)


def _free_port(host: str, start: int) -> int:
    """First port from `start` that nothing else is listening on."""
    import socket
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # a just-stopped server isn't "busy"
            try:
                sock.bind((host if host not in ("localhost",) else "127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m tracker", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["check", "find-pie", "once", "poll", "serve", "demo", "rebuild", "backfill", "restart", "stop"])
    p.add_argument("--demo", action="store_true", help="serve demo data")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    p.add_argument("--start", help="backfill: first day of history (YYYY-MM-DD)")
    p.add_argument("--use-cache", action="store_true", help="backfill: reuse data/orders_cache.json")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # keeps URLs out of logs
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # noisy "possibly delisted" messages
    if a.command == "restart":
        return _restart(open_browser=not a.no_browser)
    if a.command == "stop":
        from .config import ROOT
        stopped = _stop_own(ROOT)
        print(f"Stopped {len(stopped)} tracker process(es) on this computer." if stopped else "The tracker wasn't running here.")
        return 0

    cfg = get_settings()

    if a.command == "check":
        from .t212 import T212Error
        c = _client(cfg)
        try:
            summ = c.account_summary()
        except T212Error as e:
            print(f"FAILED: {e}. 401 = wrong key/secret or key disabled; 403 = missing permission or IP restriction.")
            return 1
        print(f"OK: connected to {cfg.env} account, currency {summ.get('currency')}")
        try:
            print(f"Pies visible: {len(c.pies())}")
        except T212Error as e:
            print(f"Pies endpoint: {e} (enable the pies read permission on the key)")
        return 0

    if a.command == "find-pie":
        c = _client(cfg)
        for pie in c.pies():
            d = c.pie(pie.id)
            print(f"id={pie.id:<12} name={d.settings.name!r:<30} holdings={len(d.instruments):<3} "
                  f"value=£{(pie.result.priceAvgValue or 0):,.2f}")
        print("\nPut the bot's id in .env as T212_PIE_ID=<id>")
        return 0

    if a.command in ("once", "poll"):
        from .db import make_session_factory
        from .poller import Poller
        poller = Poller(_client(cfg), make_session_factory(cfg.database_url), cfg)
        if a.command == "poll":
            poller.run_forever()
            return 0
        poller.poll_pie_list()
        poller.snapshot()
        poller.poll_positions()
        n_div = poller.poll_dividends()
        n_con = poller.sync_contributions()
        try:
            n_b = poller.update_benchmark()
        except Exception as e:
            n_b = f"failed ({e})"
        poller.recompute()
        print(f"Snapshot saved. dividends +{n_div}, contributions {n_con}, benchmark +{n_b}")
        return 0

    if a.command == "backfill":
        from datetime import date
        from .backfill import fetch_inputs, run
        from .db import make_session_factory
        if cfg.pie_id is None:
            print("Set T212_PIE_ID in .env first (python -m tracker find-pie)")
            return 1
        print("Reading your pie and order history from Trading 212 (takes about a minute)…", flush=True)
        data = fetch_inputs(_client(cfg), cfg, use_cache=a.use_cache)
        print("Matching prices on Yahoo Finance…", flush=True)
        res = run(make_session_factory(cfg.database_url), cfg, data,
                  start=date.fromisoformat(a.start) if a.start else None)
        print(f"Done: {res['days']} days of history from {res['start']}, {res['moves']} bot moves, "
              f"{res['trades']} bot trades across {res['tickers']} stocks.")
        if res["unmatched_prices"]:
            print("Priced from your own fills instead of Yahoo:", ", ".join(res["unmatched_prices"]))
        return 0

    if a.command == "demo":
        from .demo import seed
        print("Demo data written to", seed())
        return 0

    if a.command == "rebuild":
        from .db import make_session_factory
        from . import service
        with make_session_factory(cfg.database_url)() as s:
            rows = service.rebuild(s, cfg)
            s.commit()
        print(f"Rebuilt {len(rows)} daily values")
        return 0

    if a.command == "serve":
        import uvicorn
        from dataclasses import replace
        from .api import create_app
        if a.demo:
            from .demo import DEMO_URL, seed
            from pathlib import Path
            if not Path(DEMO_URL.removeprefix("sqlite:///")).exists():
                seed()
            cfg = replace(cfg, database_url=DEMO_URL, stale_after_s=10**9)
        port = _free_port(a.host, a.port)
        url = f"http://{'127.0.0.1' if a.host in ('0.0.0.0', '::') else a.host}:{port}"
        print(f"\n  Bot tracker{' (demo data)' if a.demo else ''} is running at {url}")
        if port != a.port:
            print(f"  (port {a.port} was busy, so it's using {port})")
        print("  Leave this window open. Press Ctrl+C to stop.\n", flush=True)
        if a.host in ("127.0.0.1", "localhost") and not a.no_browser:
            import threading
            import webbrowser
            threading.Timer(1.2, lambda: webbrowser.open(url)).start()
        uvicorn.run(create_app(cfg), host=a.host, port=port, log_level="warning")
        return 0
    return 1


DEFAULT_PORT = 8212  # the tracker's own port, so it never collides with other local projects


def _free_tracker_port(port: int) -> None:
    """Stop a previous copy of THIS tracker's website if it is still holding `port`.
    Only a process started as `python -m tracker serve` from this folder counts; any other
    program on the port is left alone and the site moves to the next free port."""
    import subprocess
    import time
    from .config import ROOT
    try:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True).stdout
    except FileNotFoundError:
        return
    for pid in {p.strip() for p in out.split() if p.strip().isdigit()}:
        cmd = subprocess.run(["ps", "-o", "command=", "-p", pid], capture_output=True, text=True).stdout
        cwd = subprocess.run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"], capture_output=True, text=True).stdout
        ours = "-m tracker serve" in cmd and f"n{ROOT}" in cwd.splitlines()
        if not ours:
            print(f"Port {port} is in use by another program, which is left running; the site will use the next free port.")
            continue
        subprocess.run(["kill", pid])
        for _ in range(20):
            if subprocess.run(["kill", "-0", pid], capture_output=True).returncode != 0:
                break
            time.sleep(0.25)


def _own_processes(root) -> list[str]:
    """PIDs of this tracker's own poller/website: the ones it recorded when it started them,
    plus any `python -m tracker poll|serve` whose working folder is this one. Nothing else."""
    import json
    import os
    import subprocess
    pids: set[str] = set()
    try:
        recorded = json.loads((root / "data" / "logs" / "pids.json").read_text())
    except (OSError, ValueError):
        recorded = []
    for pid in map(str, recorded):
        cmd = subprocess.run(["ps", "-o", "command=", "-p", pid], capture_output=True, text=True).stdout
        if "-m tracker" in cmd:
            pids.add(pid)
    out = subprocess.run(["pgrep", "-f", "[-]m tracker (poll|serve)"], capture_output=True, text=True).stdout
    for pid in out.split():
        cwd = subprocess.run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"], capture_output=True, text=True).stdout
        if f"n{root}" in cwd.splitlines():
            pids.add(pid)
    pids.discard(str(os.getpid()))
    return sorted(pids)


def _stop_own(root) -> list[str]:
    """Stop this tracker's own poller/website (never anything else) and wait for them to exit."""
    import subprocess
    import time
    mine = _own_processes(root)
    for pid in mine:
        subprocess.run(["kill", pid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        if not any(subprocess.run(["kill", "-0", pid], capture_output=True).returncode == 0 for pid in mine):
            break
        time.sleep(0.25)
    return mine


def _restart(open_browser: bool = True, timeout_s: float = 30.0) -> int:
    """Stop running copies, start the poller and the website detached from this process,
    wait until the site answers, then open it. Logs go to data/logs/."""
    import json
    import re
    import subprocess
    import time
    import urllib.request
    import webbrowser
    from .config import ROOT

    hosted = ROOT / "deploy" / ".hosted"
    if hosted.exists():
        # It runs on the server now; a second copy here would double the Trading 212 calls.
        import os
        public = os.getenv("PUBLIC_URL", "").strip()
        print(f"The tracker runs on your server ({hosted.read_text().strip()}), so nothing was started here."
              + (f" Opening {public}." if public else " Add PUBLIC_URL=https://... to .env to open it from here.")
              + f" To run it on this Mac again, move {hosted} out of the deploy folder.")
        if public and open_browser:
            webbrowser.open(public)
        return 0

    logs = ROOT / "data" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    _stop_own(ROOT)
    _free_tracker_port(DEFAULT_PORT)

    poll_log = logs / "poll.log"
    if poll_log.exists() and poll_log.stat().st_size > 5_000_000:
        poll_log.replace(logs / "poll.old.log")
    serve_log = logs / "serve.log"

    def spawn(args, log, mode):
        # own session: survives the Dock app (or terminal) that started it
        return subprocess.Popen([sys.executable, "-m", "tracker", *args], cwd=ROOT, stdin=subprocess.DEVNULL,
                                stdout=open(log, mode), stderr=subprocess.STDOUT, start_new_session=True)

    poller = spawn(["poll"], poll_log, "ab")
    server = spawn(["serve", "--no-browser"], serve_log, "wb")
    (logs / "pids.json").write_text(json.dumps([poller.pid, server.pid]))
    deadline = time.monotonic() + timeout_s
    url = None
    while time.monotonic() < deadline:
        if server.poll() is not None:
            break
        m = re.findall(r"running at (http://[0-9.:]+)", serve_log.read_text(errors="ignore"))
        if m:
            try:
                urllib.request.urlopen(f"{m[-1]}/api/health", timeout=2).close()
                url = m[-1]
                break
            except OSError:
                pass
        time.sleep(0.4)
    if not url:
        print(f"The website didn't start. See {serve_log}")
        return 1
    print(f"Bot tracker is running at {url} (poller and website in the background; logs in {logs})")
    if open_browser:
        webbrowser.open(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())

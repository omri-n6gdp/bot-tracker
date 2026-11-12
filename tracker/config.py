"""Settings and credentials.

Credentials come from env vars (T212_API_KEY / T212_API_SECRET) or, failing that,
from the keys file (default: "API Keys" in the project root: a comment line, then
the key on one line and the secret on the next). Secrets are never printed.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional .env support
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

ROOT = Path(__file__).resolve().parent.parent

if load_dotenv:
    load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Credentials:
    key: str = field(repr=False)
    secret: str = field(repr=False)

    def __repr__(self) -> str:  # never leak
        return "Credentials(<hidden>)"


def load_credentials(keys_file: Path | None = None) -> Credentials:
    key, secret = os.getenv("T212_API_KEY"), os.getenv("T212_API_SECRET")
    if key and secret:
        return Credentials(key.strip(), secret.strip())
    path = keys_file or ROOT / os.getenv("T212_KEYS_FILE", "API Keys")
    if not path.exists():
        raise FileNotFoundError(
            f"No credentials: set T212_API_KEY/T212_API_SECRET or create '{path.name}'"
        )
    lines = [
        ln.strip()
        for ln in path.read_text(encoding="utf-8-sig").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    # Accept either bare values (key line, then secret line) or LABEL=value lines,
    # e.g. TRADING212_API_KEY=... / TRADING212_API_SECRET=... in any order.
    labelled: dict[str, str] = {}
    bare: list[str] = []
    for ln in lines:
        m = re.match(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_ ]*?)\s*[=:]\s*(.+)$", ln)
        if m and not re.fullmatch(r"[A-Za-z0-9_\-+/]+=*", ln):  # a bare base64 value can end in '='
            labelled[m.group(1).upper()] = m.group(2).strip().strip("'\"")
        else:
            bare.append(ln.strip("'\""))
    labelled = {k: v for k, v in labelled.items()
                if not any(x in k for x in ("XAI", "GROK", "GROQ", "ANTHROPIC", "CLAUDE"))}  # other services' keys are never Trading 212's
    if labelled:
        secret = next((v for k, v in labelled.items() if "SECRET" in k), None)
        key = next((v for k, v in labelled.items() if "SECRET" not in k and ("KEY" in k or "ID" in k)), None)
        if key and secret:
            return Credentials(key, secret)
    if len(bare) >= 2:
        return Credentials(bare[0], bare[1])
    raise ValueError(f"'{path.name}' needs the key and the secret, e.g. TRADING212_API_KEY=... and TRADING212_API_SECRET=...")


@dataclass(frozen=True)
class Settings:
    env: str = "live"  # live | demo
    pie_id: int | None = None
    database_url: str = f"sqlite:///{ROOT / 'data' / 'tracker.db'}"
    contributions_csv: Path = ROOT / "contributions.csv"
    # comparison lines: Yahoo ticker -> name (GBP-priced, accumulating = total return)
    benchmarks: tuple = (("VUAG.L", "S&P 500"), ("VWRL.L", "FTSE All-World"))
    timezone: str = "Europe/London"
    daily_close_time: str = "21:30"  # local time, after the US close
    stale_after_s: int = 600
    mode: str = "auto"  # auto | pie | fallback
    risk_free_rate: float = 0.0
    display_name: str | None = None  # what friends see; defaults to the pie's name
    bot_start_date: str | None = None  # history starts here; defaults to the pie's creation date
    deposits_path: Path | None = None  # DEPOSITS_CSV; defaults to deposits.csv next to contributions.csv
    players: tuple = ()  # PLAYERS: who can play the weekly prediction game

    @property
    def benchmark_ticker(self) -> str:
        return self.benchmarks[0][0]

    @property
    def benchmark_name(self) -> str:
        return self.benchmarks[0][1]

    @property
    def deposits_csv(self) -> Path:
        return self.deposits_path or self.contributions_csv.with_name("deposits.csv")

    @property
    def base_url(self) -> str:
        return f"https://{self.env}.trading212.com"

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.getenv
        return cls(
            env=e("T212_ENV", "live"),
            pie_id=int(e("T212_PIE_ID")) if e("T212_PIE_ID") else None,
            # TRACKER_DB: a file name in data/, so each pie can keep its own history
            database_url=e("DATABASE_URL") or (f"sqlite:///{ROOT / 'data' / e('TRACKER_DB')}" if e("TRACKER_DB") else cls.database_url),
            contributions_csv=ROOT / e("CONTRIBUTIONS_CSV", "contributions.csv"),
            benchmarks=_parse_benchmarks(e("BENCHMARKS")) or cls.benchmarks,
            timezone=e("TRACKER_TZ", cls.timezone),
            daily_close_time=e("DAILY_CLOSE_TIME", cls.daily_close_time),
            stale_after_s=int(e("STALE_AFTER_S", str(cls.stale_after_s))),
            mode=e("TRACKER_MODE", cls.mode),
            risk_free_rate=float(e("RISK_FREE_RATE", "0")),
            display_name=e("PIE_DISPLAY_NAME") or None,
            bot_start_date=e("BOT_START_DATE") or None,
            deposits_path=ROOT / e("DEPOSITS_CSV") if e("DEPOSITS_CSV") else None,
            players=tuple(n.strip() for n in (e("PLAYERS") or "").split(",") if n.strip()),
        )


def _parse_benchmarks(text: str | None) -> tuple:
    """"VUAG.L=S&P 500, VWRL.L=FTSE All-World" -> (("VUAG.L","S&P 500"), ...)"""
    if not text:
        return ()
    out = []
    for part in text.split(","):
        if "=" in part:
            t, n = part.split("=", 1)
            out.append((t.strip(), n.strip()))
        elif part.strip():
            out.append((part.strip(), part.strip()))
    return tuple(out)


def get_settings() -> Settings:
    return Settings.from_env()

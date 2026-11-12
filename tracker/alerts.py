"""Phone alerts via a Telegram bot (optional).

Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env to switch on. Without them,
alerts only go to the log. An alert fires when a job has failed 3 times in a row,
and again when it recovers, so a flaky minute doesn't wake you up.
"""
from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger(__name__)
FAILS_BEFORE_ALERT = 3


def send(text: str) -> bool:
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    log.warning("ALERT: %s", text)
    if not (token and chat):
        return False
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       json={"chat_id": chat, "text": f"Bot tracker: {text}"}, timeout=10)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def send_message(text: str) -> bool:
    """A plain message to the Telegram chat (the daily recap). False if Telegram isn't set up."""
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       json={"chat_id": chat, "text": text, "disable_web_page_preview": True}, timeout=10)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


class FailureTracker:
    def __init__(self, sender=send):
        self.streak: dict[str, int] = {}
        self.alerted: set[str] = set()
        self.send = sender

    def failed(self, job: str, error: str) -> None:
        self.streak[job] = self.streak.get(job, 0) + 1
        if self.streak[job] >= FAILS_BEFORE_ALERT and job not in self.alerted:
            self.alerted.add(job)
            self.send(f"'{job}' has failed {self.streak[job]} times in a row. Last error: {error}")

    def ok(self, job: str) -> None:
        self.streak[job] = 0
        if job in self.alerted:
            self.alerted.discard(job)
            self.send(f"'{job}' is working again.")

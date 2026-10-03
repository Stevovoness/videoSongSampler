"""Telling you a video is waiting: a Telegram message, an email, or (with neither set up) just the console."""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

import requests


def send(text: str) -> str:
    """Send `text` the first way that's configured; returns which one was used."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text}, timeout=20)
        r.raise_for_status()
        return "telegram"
    host, to = os.environ.get("SMTP_HOST"), os.environ.get("NOTIFY_EMAIL")
    if host and to:
        msg = EmailMessage()
        msg["Subject"], msg["To"] = "Today's video is ready for review", to
        msg["From"] = os.environ.get("SMTP_FROM", to)
        msg.set_content(text)
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
            s.starttls()
            if os.environ.get("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            s.send_message(msg)
        return "email"
    print(text, flush=True)
    return "console"

"""Seasonal themes (Mother's Day, Christmas, elections…) that steer the idea, the text and the call to action."""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml

ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "last": -1}
WEEKDAYS = {d.lower(): i for i, d in enumerate(calendar.day_name)}
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}


@dataclass
class Theme:
    name: str
    date: str
    lead_days: int = 14
    after_days: int = 0
    kind: str = "family"            # family | political
    words: list[str] = field(default_factory=list)
    tone: str = ""
    cta: str = ""
    weight: float = 1.0


def easter(year: int) -> date:
    """Easter Sunday (Gregorian), by the anonymous algorithm."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    return date(year, month, (h + l_ - 7 * m + 114) % 31 + 1)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    days = [d for d in calendar.Calendar().itermonthdates(year, month) if d.month == month and d.weekday() == weekday]
    return days[n - 1] if n > 0 else days[n]


def on(rule: str, year: int) -> date | None:
    """The date a rule falls on in `year` (None if it doesn't happen that year)."""
    r = rule.strip().lower()
    if r.startswith("easter"):
        return easter(year) + timedelta(days=int(r[6:] or 0))
    if r == "us-election":
        if year % 2:
            return None
        return nth_weekday(year, 11, 0, 1) + timedelta(days=1)
    parts = r.split()
    if len(parts) == 4 and parts[2] == "of":
        return nth_weekday(year, MONTHS[parts[3]], WEEKDAYS[parts[1]], ORDINALS[parts[0]])
    month, day = (int(x) for x in r.split("-"))
    return date(year, month, day)


def load(path: str | Path) -> list[Theme]:
    p = Path(path)
    if not p.exists():
        return []
    return [Theme(**t) for t in yaml.safe_load(p.read_text(encoding="utf-8")) or []]


def active(themes: list[Theme], today: date) -> list[Theme]:
    """Themes running on `today`, the nearest event first."""
    out = []
    for t in themes:
        for year in (today.year - 1, today.year, today.year + 1):
            d = on(t.date, year)
            if d is not None and d - timedelta(days=t.lead_days) <= today <= d + timedelta(days=t.after_days):
                out.append((abs((d - today).days), t))
                break
    return [t for _, t in sorted(out, key=lambda x: x[0])]

"""Your recorded sign-off clips ("I'll make you one…"), picked to suit today's theme.

Put the clips in <data_dir>/outros/ and list them in outros.yaml there:
    - file: nan.mp4
      themes: []                 # empty = any day
    - file: mothers_day.mp4
      themes: [mothers_day_uk, mothers_day_us]
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml


def pick(folder: Path, theme_names: list[str], today: date) -> str:
    """A clip for today: one made for an active theme first, else a general one; "" if there are none."""
    listing = folder / "outros.yaml"
    if not listing.exists():
        return ""
    entries = [e for e in (yaml.safe_load(listing.read_text(encoding="utf-8")) or [])
               if (folder / e["file"]).exists()]
    themed = [e for e in entries if set(e.get("themes") or []) & set(theme_names)]
    pool = themed or [e for e in entries if not e.get("themes")]
    if not pool:
        return ""
    return str(folder / pool[today.toordinal() % len(pool)]["file"])

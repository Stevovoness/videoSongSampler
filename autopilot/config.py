"""Settings: autopilot/config.example.yaml, overridden by autopilot/config.yaml if it exists."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent      # the project folder
HERE = Path(__file__).resolve().parent


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


@dataclass
class Config:
    raw: dict[str, Any] = field(default_factory=dict)
    root: Path = ROOT

    def __getattr__(self, name: str) -> Any:
        try:
            return self.raw[name]
        except KeyError:
            raise AttributeError(name) from None

    def path(self, value: str | Path) -> Path:
        p = Path(value)
        return p if p.is_absolute() else self.root / p

    @property
    def data(self) -> Path:
        d = self.path(self.raw["data_dir"])
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def work(self) -> Path:
        return self.path(self.raw["work_dir"]) if self.raw.get("work_dir") else self.data / "work"

    @property
    def runs(self) -> Path:
        d = self.data / "runs"
        d.mkdir(parents=True, exist_ok=True)
        return d


def load(path: str | Path | None = None, **overrides: Any) -> Config:
    """The settings, with `path` (default autopilot/config.yaml) on top of the example file."""
    raw = yaml.safe_load((HERE / "config.example.yaml").read_text(encoding="utf-8"))
    user = Path(path) if path else HERE / "config.yaml"
    if user.exists():
        raw = _merge(raw, yaml.safe_load(user.read_text(encoding="utf-8")) or {})
    raw = _merge(raw, overrides)
    root = Path(raw.pop("root")) if "root" in raw else ROOT
    return Config(raw, root)

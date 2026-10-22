"""BugPrint configuration.

Values come from environment variables (highest priority) or bugprint.toml
(lower priority).  Defaults are applied last.  Call `load()` once at startup.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-reuse-def]
    except ImportError:
        tomllib = None  # type: ignore[assignment]


_TOML_FILE = "bugprint.toml"


class Config:
    """Holds all runtime configuration for BugPrint."""

    def __init__(
        self,
        db: str = ".bugprint/bugprint.db",
        decider_url: str = "http://127.0.0.1:8099",
        decider_model: str = "strands-decider-latest",
        act_threshold: float = 0.70,
        idle_gap_minutes: int = 15,
        allow_fallback: bool = False,
        bedrock_model_id: str = "",
        aws_region: str = "us-east-1",
    ) -> None:
        self.db = db
        self.decider_url = decider_url
        self.decider_model = decider_model
        self.act_threshold = act_threshold
        self.idle_gap_minutes = idle_gap_minutes
        self.allow_fallback = allow_fallback
        self.bedrock_model_id = bedrock_model_id
        self.aws_region = aws_region

    def __repr__(self) -> str:
        return (
            f"Config(db={self.db!r}, decider_url={self.decider_url!r}, "
            f"act_threshold={self.act_threshold}, allow_fallback={self.allow_fallback})"
        )


def _read_toml(path: Path) -> dict:
    """Read a TOML file; return empty dict if missing or tomllib unavailable."""
    if tomllib is None or not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def load(toml_path: str | Path | None = None) -> Config:
    """Build a Config from TOML file + environment variables.

    Environment variables always override TOML values.
    """
    toml_file = Path(toml_path) if toml_path else Path(_TOML_FILE)
    raw = _read_toml(toml_file)

    bugprint_section = raw.get("bugprint", {})
    decider_section = raw.get("decider", {})
    session_section = raw.get("session", {})
    bedrock_section = raw.get("bedrock", {})

    def env_or(key: str, default: str) -> str:
        return os.environ.get(key, default)

    def env_bool(key: str, default: bool) -> bool:
        raw_val = os.environ.get(key)
        if raw_val is None:
            return default
        return raw_val.strip().lower() in ("1", "true", "yes")

    def env_float(key: str, default: float) -> float:
        raw_val = os.environ.get(key)
        if raw_val is None:
            return default
        return float(raw_val)

    def env_int(key: str, default: int) -> int:
        raw_val = os.environ.get(key)
        if raw_val is None:
            return default
        return int(raw_val)

    return Config(
        db=env_or("BUGPRINT_DB", bugprint_section.get("db", ".bugprint/bugprint.db")),
        decider_url=env_or(
            "DECIDER_URL", decider_section.get("url", "http://127.0.0.1:8099")
        ),
        decider_model=env_or(
            "DECIDER_MODEL",
            decider_section.get("model", "strands-decider-latest"),
        ),
        act_threshold=env_float(
            "ACT_THRESHOLD", decider_section.get("act_threshold", 0.70)
        ),
        idle_gap_minutes=env_int(
            "IDLE_GAP_MINUTES", session_section.get("idle_gap_minutes", 15)
        ),
        allow_fallback=env_bool(
            "ALLOW_FALLBACK", decider_section.get("allow_fallback", False)
        ),
        bedrock_model_id=env_or(
            "BEDROCK_MODEL_ID", bedrock_section.get("model_id", "")
        ),
        aws_region=env_or("AWS_REGION", bedrock_section.get("region", "us-east-1")),
    )

"""Central paths and connection config, resolved from env with sensible defaults.

Env overrides:
  NHBOT_DATA  base data dir (default: <repo>/data)
  NHBOT_DSN   libpq connection string (default: dbname=nhbot)
"""
import os
from pathlib import Path

# repo root = two levels up from src/nhbot/config.py
REPO_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR      = Path(os.environ.get("NHBOT_DATA", REPO_ROOT / "data"))
RAW_DIR       = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

DSN = os.environ.get("NHBOT_DSN", "dbname=nhbot")

def raw_year(year) -> Path:
    return RAW_DIR / str(year)

# ensure output dir exists for ingest modules
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

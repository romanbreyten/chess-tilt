"""Shared settings: which month we analyze and where files live."""
from pathlib import Path

# The Lichess monthly dump to analyze (see docs/decisions.md #1 for why April 2017).
MONTH = "2017-04"

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"

RAW_FILE = RAW_DIR / f"lichess_db_standard_rated_{MONTH}.pgn.zst"
GAMES_FILE = PROCESSED_DIR / "games.parquet"

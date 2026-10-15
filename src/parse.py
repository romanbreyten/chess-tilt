"""Stream-parse a Lichess .pgn.zst dump into data/processed/games.parquet (one row per game).

Usage:  python src/parse.py [--limit N]

The file is decompressed and read line by line, so memory use stays flat no matter
how big the dump is. We read the PGN headers plus the clock times in the move text;
we never replay the chess moves themselves (see docs/decisions.md #2 for the benchmark
that justified a simple regex parser over python-chess).

This script only *extracts* data. All filtering (rated, blitz/rapid, abandoned games...)
happens in features.py, so that we can still see each player's full game history.
"""
import argparse
import io
import re
import time
from datetime import datetime

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import zstandard

from config import GAMES_FILE, PROCESSED_DIR, RAW_FILE

HEADER_RE = re.compile(r'\[(\w+) "(.*)"\]')
CLOCK_RE = re.compile(r"%clk (\d+):(\d\d):(\d\d)")  # e.g. [%clk 0:02:58]
BATCH_SIZE = 500_000  # rows held in memory before being written to Parquet

SCHEMA = pa.schema([
    ("game_id", pa.string()),
    ("event", pa.string()),
    ("white", pa.string()),
    ("black", pa.string()),
    ("result", pa.string()),            # "1-0", "0-1", "1/2-1/2"
    ("start_time", pa.timestamp("s")),  # UTC
    ("white_elo", pa.int32()),
    ("black_elo", pa.int32()),
    ("white_rating_diff", pa.int32()),
    ("black_rating_diff", pa.int32()),
    ("white_title", pa.string()),
    ("black_title", pa.string()),
    ("time_control", pa.string()),      # raw header, e.g. "180+2"
    ("base_seconds", pa.int32()),
    ("increment_seconds", pa.int32()),
    ("termination", pa.string()),
    ("n_clock_plies", pa.int32()),      # half-moves that carry a clock time
    ("white_final_clock", pa.int32()),  # seconds left after White's last move
    ("black_final_clock", pa.int32()),
])


def iter_games(lines):
    """Yield (headers, movetext) for each game in an iterable of PGN lines.

    Every Lichess game starts with an [Event ...] header, so that line marks
    the boundary between games.
    """
    headers, movetext = {}, []
    for line in lines:
        if line.startswith("[Event ") and headers:
            yield headers, "".join(movetext)
            headers, movetext = {}, []
        if line.startswith("["):
            m = HEADER_RE.match(line)
            if m:
                headers[m.group(1)] = m.group(2)
        elif line.strip():
            movetext.append(line)
    if headers:
        yield headers, "".join(movetext)


def to_int(value):
    """'1850' -> 1850, '+8' -> 8, '-9' -> -9; missing or '?' -> None."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_time_control(tc):
    """'180+2' -> (180, 2). Unlimited / correspondence games ('-') -> (None, None)."""
    base, sep, inc = (tc or "").partition("+")
    if not sep:
        return None, None
    return to_int(base), to_int(inc)


def parse_start_time(date, clock):
    """('2017.04.01', '13:05:09') -> datetime. Returns None if malformed."""
    try:
        return datetime.strptime(f"{date} {clock}", "%Y.%m.%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def game_to_row(headers, movetext):
    """Turn one game's headers + move text into a flat dict matching SCHEMA."""
    clocks = [int(h) * 3600 + int(m) * 60 + int(s) for h, m, s in CLOCK_RE.findall(movetext)]
    white_clocks, black_clocks = clocks[0::2], clocks[1::2]  # moves alternate W, B, W, B...
    base, inc = parse_time_control(headers.get("TimeControl"))

    return {
        "game_id": headers.get("Site", "").rsplit("/", 1)[-1],
        "event": headers.get("Event"),
        "white": headers.get("White"),
        "black": headers.get("Black"),
        "result": headers.get("Result"),
        "start_time": parse_start_time(headers.get("UTCDate"), headers.get("UTCTime")),
        "white_elo": to_int(headers.get("WhiteElo")),
        "black_elo": to_int(headers.get("BlackElo")),
        "white_rating_diff": to_int(headers.get("WhiteRatingDiff")),
        "black_rating_diff": to_int(headers.get("BlackRatingDiff")),
        "white_title": headers.get("WhiteTitle"),
        "black_title": headers.get("BlackTitle"),
        "time_control": headers.get("TimeControl"),
        "base_seconds": base,
        "increment_seconds": inc,
        "termination": headers.get("Termination"),
        "n_clock_plies": len(clocks),
        "white_final_clock": white_clocks[-1] if white_clocks else None,
        "black_final_clock": black_clocks[-1] if black_clocks else None,
    }


def open_pgn(path):
    """Open a .pgn.zst file as a stream of text lines (decompressed on the fly)."""
    raw = open(path, "rb")
    return io.TextIOWrapper(zstandard.ZstdDecompressor().stream_reader(raw), encoding="utf-8", errors="replace")


def parse(src, dest, limit=None):
    """Parse `src` into Parquet at `dest`, writing in batches. Returns the number of games."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    writer = pq.ParquetWriter(dest, SCHEMA)
    batch, n, t0 = [], 0, time.time()

    with open_pgn(src) as lines:
        for headers, movetext in iter_games(lines):
            batch.append(game_to_row(headers, movetext))
            n += 1
            if len(batch) == BATCH_SIZE:
                writer.write_table(pa.Table.from_pylist(batch, SCHEMA))
                batch = []
                print(f"  {n:>12,} games  ({n / (time.time() - t0):,.0f} games/s)", flush=True)
            if limit and n >= limit:
                break

    if batch:
        writer.write_table(pa.Table.from_pylist(batch, SCHEMA))
    writer.close()
    print(f"Parsed {n:,} games in {time.time() - t0:,.0f}s -> {dest}")
    return n


def summarize(path):
    """Print row counts, a sample, and sanity-check distributions."""
    con = duckdb.connect()
    con.execute(f"CREATE VIEW g AS SELECT * FROM read_parquet('{path.as_posix()}')")

    def show(title, sql):
        print(f"\n=== {title} ===")
        print(con.sql(sql).df().to_string(index=False))  # plain text: works in any Windows console

    show("Row count and date range", """
        SELECT count(*) AS games, count(DISTINCT game_id) AS unique_ids,
               min(start_time) AS first_game, max(start_time) AS last_game FROM g""")
    show("Sample rows", "SELECT game_id, event, white, black, result, start_time, white_elo, black_elo, time_control, termination, n_clock_plies FROM g USING SAMPLE 5 ROWS")
    show("Event type (top 10)", """
        SELECT regexp_replace(event, ' https?://\\S+', '') AS event, count(*) AS games,
               round(100 * count(*) / sum(count(*)) OVER (), 2) AS pct
        FROM g GROUP BY 1 ORDER BY games DESC LIMIT 10""")
    show("Result distribution", """
        SELECT result, count(*) AS games, round(100 * count(*) / sum(count(*)) OVER (), 2) AS pct
        FROM g GROUP BY 1 ORDER BY games DESC""")
    show("Termination", """
        SELECT termination, count(*) AS games, round(100 * count(*) / sum(count(*)) OVER (), 3) AS pct
        FROM g GROUP BY 1 ORDER BY games DESC""")
    show("Rating distribution (both colours)", """
        WITH r AS (SELECT white_elo AS elo FROM g UNION ALL SELECT black_elo FROM g)
        SELECT count(elo) AS rated_sides, count(*) - count(elo) AS missing,
               min(elo) AS min, quantile_cont(elo, 0.05)::INT AS p5, quantile_cont(elo, 0.25)::INT AS p25,
               median(elo)::INT AS median, quantile_cont(elo, 0.75)::INT AS p75,
               quantile_cont(elo, 0.95)::INT AS p95, max(elo) AS max FROM r""")
    show("Titles (incl. BOT)", """
        WITH t AS (SELECT white_title AS title FROM g UNION ALL SELECT black_title FROM g)
        SELECT title, count(*) AS player_games FROM t WHERE title IS NOT NULL GROUP BY 1 ORDER BY 2 DESC""")
    show("Time controls (top 10)", """
        SELECT time_control, count(*) AS games, round(100 * count(*) / sum(count(*)) OVER (), 2) AS pct
        FROM g GROUP BY 1 ORDER BY games DESC LIMIT 10""")
    show("Clock data coverage", """
        SELECT round(100 * avg((n_clock_plies > 0)::INT), 2) AS pct_games_with_clocks,
               median(n_clock_plies) AS median_plies FROM g""")
    show("Distinct players", """
        SELECT count(DISTINCT player) AS players FROM (SELECT white AS player FROM g UNION ALL SELECT black FROM g)""")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, help="only parse the first N games (for quick tests)")
    args = ap.parse_args()

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    parse(RAW_FILE, GAMES_FILE, args.limit)
    summarize(GAMES_FILE)


if __name__ == "__main__":
    main()

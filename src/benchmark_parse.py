"""Benchmark our regex parser against python-chess on the first N games, and check they agree.

Usage:  python src/benchmark_parse.py [--n 100000] [--path data/raw/...pgn.zst]

Three methods:
  regex          our parser (parse.py): headers + clock times, no move replay
  chess_headers  python-chess read_headers(): fast, but skips move text, so no clock times
  chess_full     python-chess read_game(): replays every move, gives clock times
"""
import argparse
import time
from pathlib import Path

import chess.pgn

from config import RAW_FILE
from parse import game_to_row, iter_games, open_pgn


def run_regex(path, n):
    rows = []
    with open_pgn(path) as lines:
        for headers, movetext in iter_games(lines):
            rows.append(game_to_row(headers, movetext))
            if len(rows) == n:
                break
    return rows


def run_chess_headers(path, n):
    out = []
    with open_pgn(path) as stream:
        while len(out) < n and (headers := chess.pgn.read_headers(stream)) is not None:
            out.append(dict(headers))
    return out


def run_chess_full(path, n):
    """Return the same core fields as game_to_row, computed by python-chess."""
    out = []
    with open_pgn(path) as stream:
        while len(out) < n and (game := chess.pgn.read_game(stream)) is not None:
            clocks = [round(node.clock()) for node in game.mainline() if node.clock() is not None]
            h = game.headers
            out.append({
                "game_id": h["Site"].rsplit("/", 1)[-1],
                "white": h["White"], "black": h["Black"], "result": h["Result"],
                "white_elo": h.get("WhiteElo"), "black_elo": h.get("BlackElo"),
                "time_control": h.get("TimeControl"), "termination": h.get("Termination"),
                "n_clock_plies": len(clocks),
                "white_final_clock": clocks[0::2][-1] if clocks[0::2] else None,
                "black_final_clock": clocks[1::2][-1] if clocks[1::2] else None,
            })
    return out


def timed(fn, *args):
    t0 = time.perf_counter()
    result = fn(*args)
    return result, time.perf_counter() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100_000)
    ap.add_argument("--path", type=Path, default=RAW_FILE)
    args = ap.parse_args()

    regex_rows, t_regex = timed(run_regex, args.path, args.n)
    header_rows, t_headers = timed(run_chess_headers, args.path, args.n)
    full_rows, t_full = timed(run_chess_full, args.path, args.n)

    print(f"{'method':<15}{'games':>10}{'seconds':>10}{'games/s':>12}   clock times?")
    for name, rows, t, clocks in [("regex", regex_rows, t_regex, "yes"),
                                  ("chess_headers", header_rows, t_headers, "no"),
                                  ("chess_full", full_rows, t_full, "yes")]:
        print(f"{name:<15}{len(rows):>10,}{t:>10.1f}{len(rows) / t:>12,.0f}   {clocks}")

    # Agreement check: every field python-chess produced must match our parser exactly.
    def as_str(v):
        return None if v in (None, "?") else str(v)

    mismatches = 0
    for ours, ref in zip(regex_rows, full_rows, strict=True):
        for key, ref_value in ref.items():
            if as_str(ours[key]) != as_str(ref_value):
                mismatches += 1
                if mismatches <= 5:
                    print(f"  MISMATCH game {ref['game_id']} field {key}: regex={ours[key]!r} chess={ref_value!r}")
    n_checked = len(full_rows) * len(full_rows[0])
    print(f"\nAgreement: {n_checked - mismatches:,} / {n_checked:,} field values identical ({mismatches} mismatches)")
    print(f"Regex parser is {t_full / t_regex:.1f}x faster than full python-chess.")
    print(f"Estimated time for 11.35M games: regex ~{11.35e6 / (len(regex_rows) / t_regex) / 60:.0f} min, "
          f"python-chess ~{11.35e6 / (len(full_rows) / t_full) / 60:.0f} min")


if __name__ == "__main__":
    main()

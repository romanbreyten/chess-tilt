"""Tests for the PGN parser, using a small hand-written PGN that mimics Lichess's format."""
import io
from datetime import datetime

import pyarrow.parquet as pq
import zstandard

from parse import game_to_row, iter_games, parse, parse_time_control, to_int

# Three games covering the tricky cases:
#   1. normal clocked game with increment, odd number of half-moves (White moved last)
#   2. missing rating ("?"), BOT title, unlimited time control, no moves, abandoned
#   3. tournament game, even number of half-moves (Black moved last), titled player
SAMPLE_PGN = """\
[Event "Rated Blitz game"]
[Site "https://lichess.org/AAAA1111"]
[White "alice"]
[Black "bob"]
[Result "1-0"]
[UTCDate "2017.04.01"]
[UTCTime "13:05:09"]
[WhiteElo "1850"]
[BlackElo "1790"]
[WhiteRatingDiff "+8"]
[BlackRatingDiff "-9"]
[ECO "C20"]
[Opening "King's Pawn Game"]
[TimeControl "180+2"]
[Termination "Normal"]

1. e4 { [%clk 0:03:00] } e5 { [%clk 0:03:00] } 2. Qh5 { [%clk 0:02:55] } Nc6 { [%clk 0:02:41] } 3. Qxf7# { [%clk 0:02:50] } 1-0

[Event "Rated Classical game"]
[Site "https://lichess.org/BBBB2222"]
[White "carol"]
[Black "dave_bot"]
[Result "0-1"]
[UTCDate "2017.04.02"]
[UTCTime "08:00:00"]
[WhiteElo "?"]
[BlackElo "1500"]
[BlackTitle "BOT"]
[TimeControl "-"]
[Termination "Abandoned"]

0-1

[Event "Rated Bullet tournament https://lichess.org/tournament/xyz"]
[Site "https://lichess.org/CCCC3333"]
[White "erin"]
[Black "frank"]
[Result "1/2-1/2"]
[UTCDate "2017.04.03"]
[UTCTime "23:59:59"]
[WhiteElo "2400"]
[BlackElo "2410"]
[WhiteRatingDiff "+1"]
[BlackRatingDiff "-1"]
[WhiteTitle "GM"]
[TimeControl "60+0"]
[Termination "Time forfeit"]

1. d4 { [%clk 0:01:00] } d5 { [%clk 0:01:00] } 2. c4 { [%clk 0:00:58] } e6 { [%clk 1:00:05] } 1/2-1/2

"""


def parsed_games():
    return list(iter_games(io.StringIO(SAMPLE_PGN)))


def test_iter_games_splits_on_event_header():
    games = parsed_games()
    assert len(games) == 3
    assert [h["Site"][-8:] for h, _ in games] == ["AAAA1111", "BBBB2222", "CCCC3333"]


def test_iter_games_keeps_header_values_with_quotes_and_spaces():
    headers, _ = parsed_games()[0]
    assert headers["Opening"] == "King's Pawn Game"
    assert headers["Event"] == "Rated Blitz game"


def test_normal_game_row():
    row = game_to_row(*parsed_games()[0])
    assert row["game_id"] == "AAAA1111"
    assert row["white"] == "alice" and row["black"] == "bob"
    assert row["result"] == "1-0"
    assert row["start_time"] == datetime(2017, 4, 1, 13, 5, 9)
    assert (row["white_elo"], row["black_elo"]) == (1850, 1790)
    assert (row["white_rating_diff"], row["black_rating_diff"]) == (8, -9)
    assert (row["base_seconds"], row["increment_seconds"]) == (180, 2)
    assert row["white_title"] is None


def test_clocks_alternate_white_black():
    # Game 1: 5 half-moves, White moved last. White's clocks: 180, 175, 170. Black's: 180, 161.
    row = game_to_row(*parsed_games()[0])
    assert row["n_clock_plies"] == 5
    assert (row["white_first_clock"], row["black_first_clock"]) == (180, 180)
    assert row["white_final_clock"] == 170
    assert row["black_final_clock"] == 161


def test_clock_hours_are_parsed():
    # Game 3: Black's last clock is 1:00:05 -> 3605 seconds.
    row = game_to_row(*parsed_games()[2])
    assert row["n_clock_plies"] == 4
    assert row["white_final_clock"] == 58
    assert row["black_final_clock"] == 3605
    assert row["white_title"] == "GM"


def test_missing_values_become_none():
    row = game_to_row(*parsed_games()[1])
    assert row["white_elo"] is None          # "?" in the PGN
    assert row["white_rating_diff"] is None  # header absent
    assert row["black_title"] == "BOT"
    assert (row["base_seconds"], row["increment_seconds"]) == (None, None)  # "-" time control
    assert row["n_clock_plies"] == 0
    assert row["white_first_clock"] is None and row["black_first_clock"] is None
    assert row["white_final_clock"] is None and row["black_final_clock"] is None
    assert row["termination"] == "Abandoned"


def test_to_int():
    assert to_int("+8") == 8
    assert to_int("-9") == -9
    assert to_int("?") is None
    assert to_int(None) is None


def test_parse_time_control():
    assert parse_time_control("300+0") == (300, 0)
    assert parse_time_control("-") == (None, None)
    assert parse_time_control(None) == (None, None)


def test_parse_end_to_end_writes_parquet(tmp_path):
    src = tmp_path / "sample.pgn.zst"
    src.write_bytes(zstandard.ZstdCompressor().compress(SAMPLE_PGN.encode()))
    dest = tmp_path / "games.parquet"

    assert parse(src, dest) == 3
    table = pq.read_table(dest)
    assert table.num_rows == 3
    assert table.column("game_id").to_pylist() == ["AAAA1111", "BBBB2222", "CCCC3333"]
    assert table.column("white_elo").to_pylist() == [1850, None, 2400]


def test_parse_respects_limit(tmp_path):
    src = tmp_path / "sample.pgn.zst"
    src.write_bytes(zstandard.ZstdCompressor().compress(SAMPLE_PGN.encode()))
    assert parse(src, tmp_path / "out.parquet", limit=2) == 2

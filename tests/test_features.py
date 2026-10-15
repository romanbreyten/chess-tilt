"""Tests for features.py: tiny hand-built game histories where we know the right answer."""
from datetime import datetime, timedelta

import duckdb
import pandas as pd
import pyarrow as pa
import pytest

from features import build_sample, build_timeline
from parse import SCHEMA

T0 = datetime(2017, 4, 10, 12, 0, 0)


def game(game_id, white, black, result, start, *, tc="180+0", plies=40,
         first=(None, None), final=(100, 100), termination="Normal",
         event="Rated Blitz game", white_elo=1500, black_elo=1500, black_title=None):
    """One row of games.parquet. `start` is minutes after T0. Clocks default to a plain game."""
    base, _, inc = tc.partition("+")
    base, inc = (int(base), int(inc)) if inc else (None, None)
    white_first, black_first = first
    return {
        "game_id": game_id, "event": event, "white": white, "black": black, "result": result,
        "start_time": T0 + timedelta(minutes=start),
        "white_elo": white_elo, "black_elo": black_elo,
        "white_rating_diff": 0, "black_rating_diff": 0,
        "white_title": None, "black_title": black_title,
        "time_control": tc, "base_seconds": base, "increment_seconds": inc,
        "termination": termination, "n_clock_plies": plies,
        "white_first_clock": white_first if white_first is not None else (base if plies >= 1 else None),
        "black_first_clock": black_first if black_first is not None else (base if plies >= 2 else None),
        "white_final_clock": final[0] if plies >= 1 else None,
        "black_final_clock": final[1] if plies >= 2 else None,
    }


def run(games):
    """Run the feature pipeline on a list of game dicts; return (timeline df, sample df)."""
    con = duckdb.connect()
    con.register("games", pa.Table.from_pylist(games, SCHEMA))
    build_timeline(con)
    timeline = con.sql("SELECT * FROM timeline ORDER BY player, game_idx").df()
    sample = build_sample(con).df().sort_values(["player", "game_idx"]).reset_index(drop=True)
    return timeline, sample


def rows_for(df, player):
    return df[df.player == player].reset_index(drop=True)


# ---------- scoring ----------

def test_two_rows_per_game_and_residuals_cancel():
    _, s = run([game("g1", "alice", "bob", "1-0", 0, white_elo=1700, black_elo=1500)])
    assert len(s) == 2
    assert s.score.sum() == 1.0
    assert s.residual.sum() == pytest.approx(0.0)
    alice = rows_for(s, "alice").iloc[0]
    assert (alice.color, alice.opponent, alice.score) == ("white", "bob", 1.0)


def test_expected_score_formula():
    # 400 points stronger -> expected score 10/11
    _, s = run([game("g1", "alice", "bob", "1/2-1/2", 0, white_elo=1900, black_elo=1500)])
    alice = rows_for(s, "alice").iloc[0]
    assert alice.expected_score == pytest.approx(10 / 11)
    assert alice.residual == pytest.approx(0.5 - 10 / 11)


def test_rating_bands():
    games = [game(f"g{elo}", f"p{elo}", f"opp{elo}", "1-0", 0, white_elo=elo)
             for elo in (1199, 1200, 1599, 1600, 1999, 2000)]
    _, s = run(games)
    s = s[s.color == "white"]  # just the players we set ratings for
    bands = dict(zip(s.player, s.rating_band))
    assert bands == {"p1199": "<1200", "p1200": "1200-1600", "p1599": "1200-1600",
                     "p1600": "1600-2000", "p1999": "1600-2000", "p2000": "2000+"}


# ---------- speed and game length ----------

def test_speed_uses_lichess_rule_base_plus_40_increments():
    tcs = {"120+1": "bullet", "180+0": "blitz", "300+3": "blitz",
           "480+0": "rapid", "600+0": "rapid", "900+15": "classical"}
    games = [game(tc, f"w{tc}", f"b{tc}", "1-0", 0, tc=tc) for tc in tcs]
    t, _ = run(games)
    got = dict(zip(t.time_control, t.speed))
    assert got == tcs


def test_duration_from_clocks():
    # 180+2, 41 plies -> White made 21 moves, Black 20.
    # White: 180 + 2*20 - 100 = 120 s.  Black: 180 + 2*19 - 150 = 68 s.  Total 188 s.
    _, s = run([game("g1", "alice", "bob", "1-0", 0, tc="180+2", plies=41, final=(100, 150))])
    assert s.duration_seconds.iloc[0] == 188
    assert s.last_move_time.iloc[0] == T0 + timedelta(seconds=188)


def test_berserk_uses_half_clock_and_no_increment():
    # White berserked: starts at 90 (half of 180) and earns no increment.
    # White: 90 + 0*20 - 30 = 60 s.  Black (normal): 180 + 2*19 - 150 = 68 s.  Total 128 s.
    _, s = run([game("g1", "alice", "bob", "1-0", 0, tc="180+2", plies=41,
                     first=(90, 180), final=(30, 150), event="Rated Blitz tournament https://x")])
    assert s.duration_seconds.iloc[0] == 128
    assert s.is_tournament.all()


def test_time_forfeit_ends_at_last_move_not_when_flag_falls():
    # Player walked away with 120 s left; game length stops at the last move.
    # 180+0, 20 plies: White 180 - 100 = 80, Black 180 - 120 = 60 -> 140 s.
    _, s = run([game("g1", "alice", "bob", "0-1", 0, plies=20, final=(100, 120), termination="Time forfeit")])
    assert s.duration_seconds.iloc[0] == 140


# ---------- timelines, gaps, sessions ----------

def test_gap_session_and_bucket():
    # Each game lasts 160 s (two sides x 80 s). g2 starts 30 s after g1's last move, g3 45 min later.
    games = [
        game("g1", "alice", "x1", "1-0", 0, final=(100, 100)),
        game("g2", "alice", "x2", "1-0", (160 + 30) / 60, final=(100, 100)),
        game("g3", "alice", "x3", "1-0", (160 + 30) / 60 + (160 / 60) + 45, final=(100, 100)),
    ]
    _, s = run(games)
    a = rows_for(s, "alice")
    assert pd.isna(a.gap_minutes.iloc[0]) and pd.isna(a.gap_bucket.iloc[0])  # first game: no previous game
    assert a.gap_minutes.iloc[1] == pytest.approx(0.5)
    assert a.gap_minutes.iloc[2] == pytest.approx(45)
    assert list(a.gap_bucket.iloc[1:]) == ["<1 min", "30+ min"]
    assert list(a.new_session) == [True, False, True]
    assert list(a.session_id) == [1, 1, 2]
    assert list(a.game_in_session) == [1, 2, 1]


def test_loss_streak_counts_within_session_and_resets():
    # alice: W L L L | 40 min break | L D L
    results = ["1-0", "0-1", "0-1", "0-1", "0-1", "1/2-1/2", "0-1"]
    starts = [0, 5, 10, 15, 60, 65, 70]  # games last 160 s, so 5 min apart = same session
    games = [game(f"g{i}", "alice", f"x{i}", r, st) for i, (r, st) in enumerate(zip(results, starts))]
    _, s = run(games)
    a = rows_for(s, "alice")
    assert list(a.loss_streak_before) == [0, 0, 1, 2, 0, 1, 0]
    assert list(a.prev_score.fillna(-1)) == [-1, 1.0, 0.0, 0.0, 0.0, 0.0, 0.5]


def test_bullet_game_counts_as_previous_game_but_not_as_outcome():
    games = [
        game("g1", "alice", "x1", "0-1", 0),                            # blitz loss
        game("g2", "alice", "x2", "0-1", 5, tc="60+0", final=(20, 20)),  # bullet loss
        game("g3", "alice", "x3", "1-0", 10),                           # blitz
    ]
    _, s = run(games)
    a = rows_for(s, "alice")
    assert list(a.game_id) == ["g1", "g3"]  # bullet game not analyzed...
    g3 = a.iloc[1]
    assert (g3.prev_game_id, g3.prev_speed, g3.prev_score) == ("g2", "bullet", 0.0)  # ...but it is the previous game
    assert g3.loss_streak_before == 2
    assert g3.prev2_score == 0.0


def test_correspondence_games_are_not_part_of_the_timeline():
    games = [
        game("g1", "alice", "x1", "1-0", 0),
        game("corr", "alice", "x2", "0-1", 2, tc="-", plies=0),
        game("g3", "alice", "x3", "1-0", 10),
    ]
    t, s = run(games)
    assert "corr" not in set(t.game_id)
    assert rows_for(s, "alice").prev_game_id.iloc[1] == "g1"


def test_sample_filters():
    games = [
        game("ok", "alice", "x1", "1-0", 0),
        game("abandoned", "bob", "x2", "1-0", 0, termination="Abandoned"),
        game("no_moves", "carol", "x3", "1-0", 0, plies=1),
        game("unfinished", "dave", "x4", "*", 0),
        game("bot", "erin", "x5", "1-0", 0, black_title="BOT"),
        game("classical", "frank", "x6", "1-0", 0, tc="1800+0"),
    ]
    _, s = run(games)
    assert set(s.game_id) == {"ok"}


def test_opponent_state_is_attached():
    # bob lost his previous game; alice won hers. In g2 each row sees the other's state.
    games = [
        game("a1", "alice", "x1", "1-0", 0),
        game("b1", "x2", "bob", "1-0", 0),
        game("g2", "alice", "bob", "1-0", 5),
    ]
    _, s = run(games)
    g2 = s[s.game_id == "g2"].set_index("player")
    assert (g2.loc["alice", "prev_score"], g2.loc["alice", "opp_prev_score"]) == (1.0, 0.0)
    assert (g2.loc["bob", "prev_score"], g2.loc["bob", "opp_prev_score"]) == (0.0, 1.0)
    assert g2.loc["alice", "opp_loss_streak_before"] == 1


def test_rematch_flag():
    games = [game("g1", "alice", "bob", "1-0", 0), game("g2", "bob", "alice", "1-0", 4)]
    _, s = run(games)
    assert s[s.game_id == "g2"].is_rematch.all()
    assert not s[s.game_id == "g1"].is_rematch.fillna(False).any()

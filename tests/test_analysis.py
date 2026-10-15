"""Tests for the statistics helpers in analysis.py, on synthetic data with known answers."""
import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pytest
import statsmodels.api as sm

from analysis import contrast, describe, fe_fit, load_model_frame, tidy
from features import build_sample, build_timeline
from parse import SCHEMA
from test_features import game  # same tiny-game builder used in the feature tests


def synthetic_panel(n_players=400, games=40, effect=-0.03, seed=0):
    """Players with their own skill offset; a previous loss shifts the residual by `effect`."""
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({
        "player": np.repeat([f"p{i}" for i in range(n_players)], games),
        "game_idx": np.tile(np.arange(games), n_players),
    })
    df["opponent"] = rng.choice([f"p{i}" for i in range(n_players)], len(df))
    df["prev_loss"] = rng.integers(0, 2, len(df)).astype("int8")
    df["gap_long"] = rng.integers(0, 2, len(df)).astype("int8")
    player_offset = np.repeat(rng.normal(0, 0.1, n_players), games)
    df["residual"] = player_offset + effect * df.prev_loss + rng.normal(0, 0.4, len(df))
    df["player"] = df["player"].astype("category")
    return df


def test_describe_clustered_se_matches_statsmodels():
    df = synthetic_panel(n_players=50, games=20)
    df["score"], df["expected_score"], df["opp_elo"], df["player_elo"] = 0.5, 0.5, 1500, 1500
    df["grp"] = "a"
    con = duckdb.connect()
    con.register("s", df.assign(player=df.player.astype(str)))

    ours = describe(con, {"grp": "grp"}, "TRUE").iloc[0]

    ols = sm.OLS(df.residual, np.ones(len(df))).fit(cov_type="cluster", cov_kwds={"groups": df.player.cat.codes})
    assert ours.mean_residual == pytest.approx(ols.params.iloc[0])
    assert ours.se == pytest.approx(ols.bse.iloc[0], rel=1e-6)
    assert ours.n == len(df) and ours.players == 50


def test_fe_fit_recovers_known_effect():
    df = synthetic_panel(effect=-0.03)
    res = fe_fit(df, ["prev_loss"])
    t = tidy(res).set_index("term")
    assert t.loc["prev_loss", "ci_low"] < -0.03 < t.loc["prev_loss", "ci_high"]
    assert t.loc["prev_loss", "n_players"] == 400
    assert t.loc["prev_loss", "elo_equiv"] == pytest.approx(t.loc["prev_loss", "coef"] * 400 / (np.log(10) * 0.25))
    assert "const" not in t.index


def test_fe_fit_two_way_clustering_runs_and_keeps_estimate():
    df = synthetic_panel()
    one = fe_fit(df, ["prev_loss"])
    two = fe_fit(df, ["prev_loss"], two_way=True)
    assert two.params["prev_loss"] == pytest.approx(one.params["prev_loss"])  # same point estimate
    assert two.std_errors["prev_loss"] > 0


def test_fe_fit_drops_constant_columns():
    df = synthetic_panel().assign(always_zero=0)
    res = fe_fit(df, ["prev_loss", "always_zero"])
    assert "always_zero" not in res.params.index


def test_contrast_matches_manual_calculation():
    df = synthetic_panel()
    res = fe_fit(df, ["prev_loss", "gap_long"])
    c = contrast(res, "prev_loss", "gap_long").iloc[0]
    cov = res.cov
    assert c.coef == pytest.approx(res.params["prev_loss"] - res.params["gap_long"])
    assert c.se == pytest.approx(np.sqrt(cov.loc["prev_loss", "prev_loss"] + cov.loc["gap_long", "gap_long"]
                                         - 2 * cov.loc["prev_loss", "gap_long"]))


def test_rating_controls():
    # alice plays 7 blitz games at ratings 1500, 1510, ..., 1560 (own average 1530), 5 min apart.
    games = [game(f"g{i}", "alice", f"x{i}", "1-0", 5 * i, white_elo=1500 + 10 * i) for i in range(7)]
    con = duckdb.connect()
    con.register("games", pa.Table.from_pylist(games, SCHEMA))
    build_timeline(con)
    con.execute(f"CREATE TABLE s AS {build_sample(con).sql_query()}")

    a = load_model_frame(con).query("player == 'alice'").sort_values("game_idx")
    # game 1 has no previous game, so the model frame starts at game 2 (rating 1510)
    assert list(a.rating_vs_own_avg.round(2)) == [-0.2, -0.1, 0.0, 0.1, 0.2, 0.3]
    # look-ahead-free version: needs 5 earlier games. Game 6 (1550) vs avg of 1500..1540 = 1520.
    assert a.rating_vs_recent20.isna().sum() == 4
    assert list(a.rating_vs_recent20.dropna().round(2)) == [0.3, 0.35]

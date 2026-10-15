"""Tilt dashboard: pick a time control and rating band, see how losing changes performance.

Run from the repo root:   streamlit run app/streamlit_app.py

Reads only results/*.csv (written by src/analysis.py), so it starts instantly and never
touches the raw data. Every number on the page comes from those files.
"""
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
DECISIONS = ROOT / "docs" / "decisions.md"

AUTHOR = "Roman Breytenbikher"
REPO_URL = ""      # add your GitHub repo link to show it in the footer
LINKEDIN_URL = ""  # add your LinkedIn profile link to show it in the footer

ELO_PER_SCORE_POINT = 400 / (np.log(10) * 0.25)  # same constant as src/analysis.py
MAIN = "own monthly average"                       # rating_control value of the main models
MINUS = "−"
FONT = "Schibsted Grotesk, Segoe UI, sans-serif"  # loaded by the theme in .streamlit/config.toml

SPEEDS = {"All": "All", "Blitz": "blitz", "Rapid": "rapid"}
BANDS = {"All": "All", "Under 1200": "<1200", "1200–1600": "1200-1600",
         "1600–2000": "1600-2000", "2000+": "2000+"}

st.set_page_config(page_title="Tilt — chess after a loss", page_icon="♟", layout="wide")


# ---------------------------------------------------------------- data

@st.cache_data
def load(name):
    return pd.read_csv(RESULTS / name)


def with_elo(df):
    """Elo-equivalent estimate and 95% CI, plus display strings for labels and tooltips."""
    df = df.assign(elo=df.coef * ELO_PER_SCORE_POINT,
                   elo_low=df.ci_low * ELO_PER_SCORE_POINT,
                   elo_high=df.ci_high * ELO_PER_SCORE_POINT)
    df["value_label"] = df.elo.map(fmt_elo) + np.where(df.pvalue >= 0.05, " (n.s.)", "")
    df["ci_text"] = [f"{fmt_num(lo)} to {fmt_num(hi)}" for lo, hi in zip(df.elo_low, df.elo_high)]
    df["p_text"] = df.pvalue.map(lambda p: "< 0.001" if p < 0.001 else f"{p:.3f}")
    df["n_text"] = df.n_obs.map(lambda n: f"{n:,.0f}")
    return df


def fmt_num(v, decimals=1):
    return f"{v:.{decimals}f}".replace("-", MINUS)


def fmt_elo(v):
    """-6.3 -> '−6', values that round to zero -> '≈ 0'."""
    r = round(v)
    return "≈ 0" if r == 0 else f"{MINUS if r < 0 else '+'}{abs(r)}"


def effects(speed, band):
    g = with_elo(load("effects_by_group.csv"))
    return g[(g.speed == speed) & (g.rating_band == band)].set_index("term")


# ---------------------------------------------------------------- colours (follow the light/dark theme)

def palette():
    if st.context.theme.type == "dark":
        return dict(ink="#f3f2ee", ink2="#c3c2b7", muted="#898781", grid="#2c2c2a", baseline="#4a4a45",
                    blue="#3987e5", orange="#d95926", gray="#7a7973", surface="#1a1a19")
    return dict(ink="#16181d", ink2="#52514e", muted="#898781", grid="#e9e8e2", baseline="#c3c2b7",
                blue="#2a78d6", orange="#eb6834", gray="#c9c7bf", surface="#fcfcfb")


def finish(chart, height=300):
    """Shared chart chrome: transparent background, theme font, hairline grid, quiet axes."""
    p = palette()
    return (chart.properties(height=height, background="transparent")
            .configure(font=FONT)
            .configure_view(stroke=None)
            .configure_axis(labelColor=p["ink2"], titleColor=p["ink2"], gridColor=p["grid"], domain=False,
                            ticks=False, labelFontSize=13, titleFontSize=12, titleFontWeight=500, labelPadding=8)
            .configure_legend(labelColor=p["ink"], titleColor=p["ink2"], labelFontSize=13, orient="top",
                              title=None, symbolStrokeWidth=3, symbolOpacity=1, labelLimit=320))


TOOLTIP = [alt.Tooltip("group:N", title="Group"), alt.Tooltip("value_label:N", title="Effect (Elo)"),
           alt.Tooltip("ci_text:N", title="95% CI (Elo)"), alt.Tooltip("p_text:N", title="p-value"),
           alt.Tooltip("n_text:N", title="Player-games")]


# ---------------------------------------------------------------- charts

def effect_bars(df, order, bar_size=26):
    """Bars from 0 with 95% CI whiskers and the value under (or over) each bar.

    `df` needs: x, elo, elo_low, elo_high, value_label, color, and the tooltip fields.
    A "|" in an x label starts a second line on the axis.
    """
    p = palette()
    lo = min(df.elo_low.min(), 0) * 1.25 - 1
    hi = max(df.elo_high.max(), 0) * 1.25 + 1
    df = df.assign(
        group=df.x.str.replace("|", " ", regex=False),
        bar_label=df.value_label.str.replace(" (n.s.)", "\nn.s.", regex=False),
        label_y=np.where(df.elo < 0, df[["elo", "elo_low"]].min(axis=1), df[["elo", "elo_high"]].max(axis=1)))
    x = alt.X("x:N", sort=order, title=None,
              axis=alt.Axis(labelAngle=0, labelFontSize=13, labelExpr="split(datum.label, '|')"))
    y = alt.Y("elo:Q", title="Elo points", scale=alt.Scale(domain=[lo, hi], nice=False),
              axis=alt.Axis(tickCount=5, labelExpr="replace(datum.label, '-', '−')"))
    base = alt.Chart(df).encode(x=x)
    color = alt.Color("color:N", scale=None)
    # Round only the data end of each bar: the bottom of bars going down, the top of bars going up.
    down = base.transform_filter("datum.elo < 0").mark_bar(
        size=bar_size, cornerRadiusBottomLeft=4, cornerRadiusBottomRight=4).encode(y=y, color=color, tooltip=TOOLTIP)
    up = base.transform_filter("datum.elo >= 0").mark_bar(
        size=bar_size, cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(y=y, color=color, tooltip=TOOLTIP)
    ci = base.mark_rule(strokeWidth=1.5, color=p["ink2"]).encode(y="elo_low:Q", y2="elo_high:Q", tooltip=TOOLTIP)
    text = dict(fontSize=14, fontWeight=600, color=p["ink"], lineBreak="\n", font=FONT)
    below = base.transform_filter("datum.elo < 0").mark_text(baseline="top", dy=6, **text).encode(
        y="label_y:Q", text="bar_label:N")
    above = base.transform_filter("datum.elo >= 0").mark_text(baseline="bottom", dy=-6, **text).encode(
        y="label_y:Q", text="bar_label:N")
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=p["baseline"], strokeWidth=1.2).encode(y="y:Q")
    return alt.layer(down, up, zero, ci, below, above)


def streak_chart(e):
    p = palette()
    terms = ["streak_1", "streak_2", "streak_3", "streak_4", "streak_5plus", "loss_x_gap_30plus"]
    labels = ["1", "2", "3", "4", "5+", "Then a|30+ min|break"]
    df = e.loc[terms].assign(x=labels, color=[p["blue"]] * 5 + [p["gray"]])
    return effect_bars(df, labels)


def cooldown_chart(e):
    p = palette()
    terms = ["loss_x_gap_lt1", "loss_x_gap_1_5", "loss_x_gap_5_30", "loss_x_gap_30plus"]
    labels = ["Under|1 min", "1–5|min", "5–30|min", "30+|min"]
    df = e.loc[terms].assign(x=labels, color=p["blue"])
    return effect_bars(df, labels)


def band_chart(speed, band):
    """The loss effect for every rating band; the selected band is highlighted, the rest are context."""
    p = palette()
    g = with_elo(load("effects_by_group.csv"))
    df = g[(g.speed == speed) & (g.rating_band != "All") & (g.term == "prev_loss")].copy()
    names = {v: k for k, v in BANDS.items()}
    df["x"] = df.rating_band.map(names)
    df["color"] = np.where((band == "All") | (df.rating_band == band), p["blue"], p["gray"])
    return effect_bars(df, [k for k in BANDS if k != "All"])


def requeue_chart(speed):
    p = palette()
    s = load("selection.csv")
    order = ["win", "loss (1 in a row)", "loss (2 in a row)", "loss (3 in a row)", "loss (4 in a row)", "loss (5+ in a row)"]
    names = dict(zip(order, ["Won", "1", "2", "3", "4", "5+"]))
    s = s[s.this_game.isin(order) & ((s.speed == speed) | (speed == "All"))].copy()
    s["x"] = s.this_game.map(names)
    s["Time control"] = s.speed.str.capitalize()
    s["pct"] = 100 * s.share_next_within_1min
    s["pct_text"] = s.pct.map(lambda v: f"{v:.1f}%")
    s["n_text"] = s.n.map(lambda n: f"{n:,}")
    color = alt.Color("Time control:N", scale=alt.Scale(domain=["Blitz", "Rapid"], range=[p["blue"], p["orange"]]))
    base = alt.Chart(s).encode(
        x=alt.X("x:N", sort=list(names.values()), title="Result of the game just played (losses in a row)",
                axis=alt.Axis(labelAngle=0)),
        y=alt.Y("pct:Q", title="Started a new game within 1 minute (%)", scale=alt.Scale(domain=[0, 65])),
        color=color,
        tooltip=[alt.Tooltip("Time control:N"), alt.Tooltip("x:N", title="Losses in a row"),
                 alt.Tooltip("pct_text:N", title="Requeued within 1 min"), alt.Tooltip("n_text:N", title="Games")])
    lines = base.mark_line(strokeWidth=2.5)
    points = base.mark_point(filled=True, size=90, opacity=1, stroke=p["surface"], strokeWidth=2)
    end = base.transform_filter("datum.x == '5+'").mark_text(align="left", dx=12, fontSize=14, fontWeight=600).encode(
        text="pct_text:N", color=alt.value(p["ink"]))  # values in ink; the line carries the colour
    return alt.layer(lines, points, end)


def raw_vs_expected_chart(speed, band):
    p = palette()
    d = load("descriptive_prev_result.csv")
    d = d[(d.speed == speed) & (d.rating_band == band) & d.prev_result.isin(["win", "loss"])].copy()
    long = pd.concat([
        d.assign(kind="Actual score", value=100 * d.mean_score),
        d.assign(kind="Expected from both players’ ratings", value=100 * d.mean_expected),
    ])
    long["y"] = long.prev_result.map({"win": "After a win", "loss": "After a loss"})
    long["value_text"] = long.value.map(lambda v: f"{v:.1f}%")
    lo, hi = np.floor(long.value.min() - 2), np.ceil(long.value.max() + 2)
    base = alt.Chart(long).encode(
        y=alt.Y("y:N", sort=["After a win", "After a loss"], title=None),
        x=alt.X("value:Q", title="Score in the next game (win = 100%, draw = 50%)", scale=alt.Scale(domain=[lo, hi]),
                axis=alt.Axis(tickCount=5, format=".0f", labelExpr="datum.label + '%'")),
        tooltip=[alt.Tooltip("y:N", title="Previous game"), alt.Tooltip("kind:N", title="Measure"),
                 alt.Tooltip("value_text:N", title="Score")])
    kinds = alt.Scale(domain=["Actual score", "Expected from both players’ ratings"], range=[p["blue"], p["orange"]])
    ring = base.transform_filter("datum.kind != 'Actual score'").mark_point(size=420, strokeWidth=3, filled=False).encode(
        color=alt.Color("kind:N", scale=kinds))
    dot = base.transform_filter("datum.kind == 'Actual score'").mark_point(
        size=120, filled=True, opacity=1, stroke=p["surface"], strokeWidth=1.5).encode(color=alt.Color("kind:N", scale=kinds))
    return alt.layer(ring, dot)


def sign_flip_chart():
    p = palette()
    m = with_elo(load("models.csv"))
    order = ["streak_1", "streak_2", "streak_3", "streak_4", "streak_5plus"]
    m = m[(m.model == "streak") & m.term.isin(order)].copy()
    m["x"] = m.term.map(dict(zip(order, ["1", "2", "3", "4", "5+"])))
    m["Model"] = m.rating_control.map({"none": "Naive: vs. their current rating", MAIN: "Main: vs. their usual level"})
    color = alt.Color("Model:N", scale=alt.Scale(domain=["Naive: vs. their current rating", "Main: vs. their usual level"],
                                                 range=[p["gray"], p["blue"]]))
    base = alt.Chart(m).encode(
        x=alt.X("x:N", sort=["1", "2", "3", "4", "5+"], title="Losses in a row before the game", axis=alt.Axis(labelAngle=0)),
        color=color)
    line = base.mark_line(strokeWidth=2.5).encode(y=alt.Y("elo:Q", title="Elo points"))
    band = base.mark_area(opacity=0.18).encode(y="elo_low:Q", y2="elo_high:Q")
    pts = base.mark_point(filled=True, size=80, opacity=1, stroke=p["surface"], strokeWidth=2).encode(
        y="elo:Q", tooltip=[alt.Tooltip("Model:N"), alt.Tooltip("x:N", title="Losses in a row"),
                            alt.Tooltip("value_label:N", title="Effect (Elo)"), alt.Tooltip("ci_text:N", title="95% CI")])
    end = base.transform_filter("datum.x == '5+'").mark_text(align="left", dx=12, fontSize=14, fontWeight=600).encode(
        y="elo:Q", text="value_label:N", color=alt.value(p["ink"]))
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=p["baseline"], strokeWidth=1.2).encode(y="y:Q")
    return alt.layer(line, band, zero, pts, end)  # line first, so the legend shows solid line swatches


# ---------------------------------------------------------------- words

def who(speed_label, band_label):
    speed = "" if speed_label == "All" else speed_label.lower() + " "
    band = "" if band_label == "All" else " rated " + band_label.replace("Under", "under")
    return f"{speed}players{band}"


def verdict(e, speed_label, band_label):
    """The plain-English answer for the current selection, built from the estimates."""
    loss, five, brk = e.loc["prev_loss"], e.loc["streak_5plus"], e.loc["loss_x_gap_30plus"]
    people = who(speed_label, band_label)
    clear_drop = lambda row: row.pvalue < 0.05 and row.elo < 0
    if not clear_drop(loss):
        s1 = f"After a loss, {people} show no clear drop in strength ({fmt_elo(loss.elo)} Elo, not statistically significant)."
        s2 = (f"After five or more losses in a row, they play about {abs(round(five.elo))} points below their usual level."
              if clear_drop(five) else "Even after five or more losses in a row, there’s no clear effect.")
        return f"{s1} {s2}"
    s1 = f"After a loss, {people} play about {abs(round(loss.elo))} rating points below their usual level in the next game."
    s2 = (f"After five or more losses in a row, about {abs(round(five.elo))} points below." if clear_drop(five)
          else "For five or more losses in a row there are too few games to tell.")
    if brk.pvalue >= 0.05:
        s3 = "After a 30-minute break, the drop is gone."
    elif brk.elo < 0:
        s3 = f"Even after a 30-minute break, they still play {abs(round(brk.elo))} points below."
    else:
        s3 = f"After a 30-minute break, they play {round(brk.elo)} points above their usual level."
    return f"{s1} {s2} {s3}"


def tile(column, label, row, what):
    """A stat tile: the estimate in Elo, with its 95% CI (or 'not significant') as a quiet note."""
    note = "not significant" if row.pvalue >= 0.05 else f"95% CI {fmt_elo(row.elo_low)} to {fmt_elo(row.elo_high)}"
    column.metric(label, f"{fmt_elo(row.elo)} Elo", delta=note, delta_color="off", delta_arrow="off",
                  border=True, help=tile_help(row, what))


def tile_help(row, what):
    return (f"{what}. 95% CI {row.ci_text} Elo, p {row.p_text}, {row.n_text} player-games. "
            "Compared with the same player after a win.")


# ---------------------------------------------------------------- page

st.markdown("""
<style>
  .block-container { max-width: 1180px; padding-top: 2.5rem; }
  .byline { color: var(--st-gray-text-color, #6b6a66); font-size: 0.95rem; margin-bottom: 0.2rem; }
  .verdict { font-size: 1.6rem; line-height: 1.4; max-width: 46rem; margin: 0.6rem 0 1.8rem 0; font-weight: 450; }
  @media (max-width: 640px) {
    .verdict { font-size: 1.25rem; }
    h1 { font-size: 2.1rem !important; }
    /* let the filter buttons wrap onto a second line instead of scrolling sideways */
    [data-testid="stButtonGroup"] > div { flex-wrap: wrap; }
  }
  .note { color: var(--st-gray-text-color, #6b6a66); font-size: 0.9rem; max-width: 48rem; }
</style>
""", unsafe_allow_html=True)

st.markdown(f'<div class="byline">A data project by {AUTHOR}</div>', unsafe_allow_html=True)
st.title("Do chess players play worse after losing?")

f1, f2 = st.columns([1, 2])
speed_label = f1.segmented_control("Time control", list(SPEEDS), default="All", key="speed") or "All"
band_label = f2.segmented_control("Player rating", list(BANDS), default="All", key="band") or "All"
speed, band = SPEEDS[speed_label], BANDS[band_label]
e = effects(speed, band)

st.markdown(f'<p class="verdict">{verdict(e, speed_label, band_label)}</p>', unsafe_allow_html=True)

t1, t2, t3, t4 = st.columns(4)
loss, five, brk, n = e.loc["prev_loss"], e.loc["streak_5plus"], e.loc["loss_x_gap_30plus"], e.loc["prev_loss"].n_obs
tile(t1, "After a loss", loss, "Next game in the same session, after any loss")
tile(t2, "After 5+ losses", five, "Next game after five or more losses in a row, same session")
tile(t3, "After a break", brk, "First game after a loss followed by a break of 30+ minutes")
t4.metric("Games analyzed", f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}K", border=True,
          delta=f"{e.loc['prev_loss'].n_players:,.0f} players", delta_color="off", delta_arrow="off",
          help=f"{n:,.0f} player-games: games played within a session, Lichess blitz and rapid, April 2017. "
               "Each game counts once per player.")

tab_effect, tab_behaviour, tab_method, tab_data = st.tabs(
    ["The effect", "How players react", "How it was measured", "Data"])

with tab_effect:
    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.subheader("Does each loss in a row cost more?")
        st.caption("Strength in the next game vs. the same player’s usual level, by losses in a row "
                   "(gray: a loss followed by a 30+ minute break). Hover for details.")
        st.altair_chart(finish(streak_chart(e), 330), width="stretch", theme=None)
    with c2:
        st.subheader("Does a break help?")
        st.caption("The loss effect by how long the player waited before the next game. "
                   "30+ minutes starts a new session.")
        st.altair_chart(finish(cooldown_chart(e), 330), width="stretch", theme=None)
    st.subheader("Who is hit hardest?")
    st.caption(f"The effect of a loss for each rating band ({speed_label.lower() if speed_label != 'All' else 'all time controls'}). "
               "Your selected band is highlighted.")
    st.altair_chart(finish(band_chart(speed, band), 300), width="stretch", theme=None)
    st.markdown('<p class="note">Elo points: how much weaker a player would need to be to score that much less '
                'in an even match. Bars show estimates with 95% confidence intervals; n.s. means not statistically '
                'significant.</p>', unsafe_allow_html=True)

with tab_behaviour:
    m = load("models.csv")
    loss_coef = lambda model: m[(m.model == model) & (m.term == "this_loss")].coef.item()
    quit_pp, fast_pp = -100 * loss_coef("keeps_playing"), 100 * loss_coef("requeues_1min")
    st.subheader("The more they lose, the faster they jump back in")
    st.write(f"Comparing each player with themselves, a loss makes them **{quit_pp:.1f} percentage points less likely** "
             f"to play again within 30 minutes, but **{fast_pp:.1f} points more likely** to start a new game within a "
             "minute. Some players walk away; the ones who stay tend to rush back in, which is exactly when they "
             "play worst.")
    st.altair_chart(finish(requeue_chart(speed), 330), width="stretch", theme=None)
    st.caption("Raw rates for lobby games (not tournaments), last day of the month excluded. "
               "The rating filter doesn’t apply to this chart.")
    st.subheader("Why raw win rates are misleading")
    d = load("descriptive_prev_result.csv")
    d = d[(d.speed == speed) & (d.rating_band == band)].set_index("prev_result")
    raw_gap = d.loc["win", "mean_score"] - d.loc["loss", "mean_score"]
    exp_gap = d.loc["win", "mean_expected"] - d.loc["loss", "mean_expected"]
    st.write(f"After a loss, {who(speed_label, band_label)} score **{100 * raw_gap:.1f} points less** than after a win. "
             f"But **{exp_gap / raw_gap:.0%}** of that is predicted by the ratings alone, mostly because they then face "
             "tougher opponents (rematches, for example). That’s why this project compares results with the rating "
             "prediction rather than raw win rate.")
    st.altair_chart(finish(raw_vs_expected_chart(speed, band), 150), width="stretch", theme=None)

with tab_method:
    st.subheader("How it was measured")
    funnel = load("sample_funnel.csv").set_index("step")["count"]
    st.markdown(f"""
1. **Data.** Every rated game on Lichess in April 2017: {funnel['games in the dump'] / 1e6:.1f} million games,
   {funnel['players in the dump']:,} players (Lichess open database, CC0). Clock times let us work out when
   each game ended.
2. **Timelines.** Each player's games in order. A break of 30+ minutes starts a new *session*;
   losing streaks are counted within a session.
3. **Performance.** Actual score minus the score the two ratings predict, so beating a stronger
   opponent counts for more than beating a weaker one.
4. **Compare players with themselves.** Fixed-effects regression: each player's games after a loss
   vs. their own games after a win, with errors clustered by player. Controls: playing White,
   blitz vs. rapid, how deep into the session, and the opponent's own last result.
5. **Remove the rating artifact.** A loss lowers your rating, so in the next game you're briefly
   underrated and "beat" the prediction. Controlling for the player's rating vs. their own monthly
   average fixes this; without it the effect even looks positive (chart below).
6. **Cool-down test.** The loss effect is estimated separately by how long the player waited before
   the next game, to see whether it fades.
""")
    st.markdown("##### Why the naive model says losing makes you play better")
    st.altair_chart(finish(sign_flip_chart(), 280), width="stretch", theme=None)

    st.markdown("##### Does it hold up?")
    r = with_elo(load("robustness.csv"))
    r["check"] = r.check.replace({
        "main (lobby games)": "Main result",
        "no rating control (spec as first written)": "Without the rating control",
        "rating vs previous 20 games (no look-ahead)": "Rating vs. previous 20 games",
        "players with 20+ games": "Players with 20+ games",
        "blitz only": "Blitz only",
        "rapid only": "Rapid only",
        "tournament games only": "Tournament games only",
        "previous game same speed": "Previous game at the same speed",
        "excluding rematches": "Excluding rematches",
        "two-way clustered SE (player + opponent)": "Errors clustered by both players",
    })
    loss_rows = r[r.estimate == "loss effect (same session)"].set_index("check")
    cool_rows = r[r.estimate.str.startswith("cool-down")].set_index("check")
    table = pd.DataFrame({
        "Loss effect (Elo)": loss_rows.value_label,
        "95% CI": loss_rows.ci_text,
        "Quick restart vs. 30+ min break (Elo)": cool_rows.value_label,
    })
    table.index.name = "Check"
    st.dataframe(table, width="stretch")
    placebo = r[r.check.str.startswith("placebo")].set_index("term")
    st.write(f"**Placebo test.** A loss two games back matters about as much ({placebo.loc['prev2_loss', 'value_label']} Elo) "
             f"as the most recent loss ({placebo.loc['prev_loss', 'value_label']} Elo). So the effect builds up over a "
             "session rather than reacting to one game.")

    st.markdown("##### What this can’t tell you")
    st.markdown("""
- **Tilt or a bad day?** Emotional tilt and simply having an off day look the same in this data:
  both cause losses and worse play that ends when you stop. Either way, a break helps.
- **Who keeps playing.** We only see performance for players who chose to continue after a loss.
- **One month, 2017.** Lichess had no "rapid" category then; speeds were classified from the time
  control. Results may differ today.
- **Elo points are approximate.** They translate score differences at an even match.
""")
    if DECISIONS.exists():
        with st.expander("Every methodological decision, with reasons"):
            st.markdown(DECISIONS.read_text(encoding="utf-8"))

with tab_data:
    st.subheader(f"Estimates for this view")
    labels = {"prev_loss": "After a loss", "streak_1": "1 loss in a row", "streak_2": "2 in a row",
              "streak_3": "3 in a row", "streak_4": "4 in a row", "streak_5plus": "5+ in a row",
              "loss_x_gap_lt1": "Loss, next game < 1 min", "loss_x_gap_1_5": "Loss, next game 1–5 min",
              "loss_x_gap_5_30": "Loss, next game 5–30 min", "loss_x_gap_30plus": "Loss, next game 30+ min",
              "loss_x_gap_lt1 - loss_x_gap_30plus": "Quick restart vs. 30+ min break"}
    view = e.loc[list(labels)].assign(Estimate=lambda d: d.index.map(labels))
    st.dataframe(view[["Estimate", "value_label", "ci_text", "p_text", "n_text"]].rename(columns={
        "value_label": "Elo", "ci_text": "95% CI (Elo)", "p_text": "p-value", "n_text": "Player-games"}),
        hide_index=True, width="stretch")
    st.subheader("Download the results")
    cols = st.columns(4)
    for i, f in enumerate(sorted(RESULTS.glob("*.csv"))):
        cols[i % 4].download_button(f.name, f.read_bytes(), file_name=f.name, mime="text/csv", width="stretch")

st.divider()
links = " · ".join(f"[{name}]({url})" for name, url in [("Code", REPO_URL), ("LinkedIn", LINKEDIN_URL)] if url)
st.caption(f"Built by {AUTHOR}. Data: Lichess open database (CC0), April 2017. {links}")

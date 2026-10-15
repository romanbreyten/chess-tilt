"""Smoke test for the dashboard: every input combination renders every tab without an error."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py")


@pytest.mark.parametrize("speed", ["All", "Blitz", "Rapid"])
@pytest.mark.parametrize("rating", [900, 1400, 1800, 2300])  # one rating in each band
@pytest.mark.parametrize("streak", ["1", "5+"])
def test_dashboard_renders(speed, rating, streak):
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["speed"] = speed
    at.session_state["rating"] = rating
    at.session_state["streak"] = streak
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    assert any("you play like a" in m.value for m in at.markdown)  # the calculator answered

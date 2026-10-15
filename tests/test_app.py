"""Smoke test for the dashboard: every filter combination renders every tab without an error."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py")
SPEEDS = ["All", "Blitz", "Rapid"]
BANDS = ["All", "Under 1200", "1200–1600", "1600–2000", "2000+"]


@pytest.mark.parametrize("speed", SPEEDS)
@pytest.mark.parametrize("band", BANDS)
def test_dashboard_renders(speed, band):
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["speed"] = speed
    at.session_state["band"] = band
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    assert len(at.metric) == 4
    assert any("After a loss" in m.value for m in at.markdown)  # the verdict sentence

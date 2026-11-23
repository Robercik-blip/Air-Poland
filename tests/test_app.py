"""Testy dymne interfejsu: każda strona musi się wyrenderować bez wyjątku (Streamlit AppTest)."""
from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
PAGES = [
    "views/dashboard.py", "views/time_analysis.py", "views/spatial_analysis.py", "views/correlation.py",
    "views/multivariate_analysis.py", "views/data_explorer.py", "views/data_import.py",
]


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page):
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180).run()
    assert not at.exception, at.exception
    at.switch_page(page).run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error


def test_pollutant_switch_keeps_working():
    """Zmiana wskaźnika na innej stronie nie może wywołać błędu (stacje i daty zależą od wskaźnika)."""
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=180).run()
    at.switch_page("views/time_analysis.py").run()
    box = next(w for w in at.selectbox if w.key == "w_pollutant")
    box.select("O3").run()
    assert not at.exception, [e.value for e in at.exception]
    at.switch_page("views/dashboard.py").run()
    assert not at.exception
    assert next(w for w in at.selectbox if w.key == "w_pollutant").value == "O3"  # filtr przetrwał zmianę strony

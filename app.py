"""AIR POLAND - punkt wejścia aplikacji Streamlit.

Uruchomienie:  streamlit run app.py

Nawigacja jest zbudowana przez st.navigation (zamiast automatycznego katalogu pages/),
dzięki czemu mamy pełną kontrolę nad kolejnością, ikonami i polskimi nazwami stron.
Widoki znajdują się w katalogu views/.
"""
from __future__ import annotations

import streamlit as st

from modules import ui
from modules.config import APP_NAME, APP_SUBTITLE, ASSETS_DIR
from modules import services
from modules.utils import get_logger

logger = get_logger("air_poland")

st.set_page_config(page_title=f"{APP_NAME} · {APP_SUBTITLE}", page_icon="🌫️", layout="wide",
                   initial_sidebar_state="expanded")


def main() -> None:
    ui.load_css()
    st.logo(str(ASSETS_DIR / "logo.svg"), size="large")
    ds = services.current_dataset()

    pages = [
        st.Page("views/dashboard.py", title="Panel główny", icon=":material/dashboard:", default=True, url_path="panel"),
        st.Page("views/time_analysis.py", title="Analiza czasowa", icon=":material/show_chart:", url_path="czas"),
        st.Page("views/spatial_analysis.py", title="Analiza przestrzenna", icon=":material/map:", url_path="przestrzen"),
        st.Page("views/correlation.py", title="Analiza korelacji", icon=":material/grid_on:", url_path="korelacja"),
        st.Page("views/multivariate_analysis.py", title="Analiza wielowymiarowa", icon=":material/bubble_chart:", url_path="wielowymiarowa"),
        st.Page("views/data_explorer.py", title="Eksploracja danych", icon=":material/table_chart:", url_path="dane"),
        st.Page("views/data_import.py", title="Źródło danych i import", icon=":material/upload_file:", url_path="import"),
    ]
    navigation = st.navigation(pages, position="sidebar")
    side_slot = st.sidebar.container()  # miejsce na status zbioru (wypełniane po uruchomieniu strony)
    navigation.run()
    ui.render_sidebar(services.current_dataset(), side_slot)


main()

"""Wspólny ekran dla modułów planowanych w Części 2 (korelacje, PCA i klasteryzacja)."""
from __future__ import annotations

import streamlit as st

from modules import services, ui


def render(title: str, subtitle: str, planned: list[str]) -> None:
    """Pokazuje opis przyszłego modułu zamiast pustej strony lub wyników 'na pokaz'."""
    ds = services.current_dataset()
    ui.page_header(title, subtitle, ds)
    ui.callout("<b>Moduł w przygotowaniu (Część 2).</b> Celowo nie pokazujemy tu przykładowych wyników, "
               "aby nie sugerować analiz, które nie zostały jeszcze zaimplementowane i zweryfikowane.", "info")
    with st.container(border=True):
        ui.section_title("Zakres modułu")
        for item in planned:
            st.markdown(f"- {item}")

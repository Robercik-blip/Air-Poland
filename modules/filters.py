"""Spójne filtry globalne (wskaźnik, stacje, zakres dat, czas uśredniania) oparte o st.session_state.

Problem, który to rozwiązuje: Streamlit usuwa stan widżetu, gdy widżet nie jest renderowany
na bieżącej stronie. Dlatego wartości filtrów trwale przechowujemy pod kluczami ``flt_*``,
a widżety (klucze ``w_*``) tylko je odzwierciedlają i zapisują zmiany w callbackach.
Dzięki temu wybór użytkownika zachowuje się przy przechodzeniu między stronami.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
import streamlit as st

from modules.analysis import averaging_step
from modules.data_loader import Dataset
from modules.utils import pollutant_label


@dataclass(frozen=True)
class FilterState:
    """Aktualny wybór filtrów (niezmienny - bezpieczny jako klucz pamięci podręcznej)."""

    pollutant: str
    stations: tuple[str, ...]
    start: date
    end: date
    averaging: str
    unit: str
    unit_assumed: bool

    @property
    def step(self) -> pd.Timedelta | None:
        return averaging_step(self.averaging)

    @property
    def step_seconds(self) -> float | None:
        step = self.step
        return step.total_seconds() if step is not None else None

    @property
    def key(self) -> tuple:
        return (self.pollutant, self.stations, self.start, self.end, self.averaging)


def reset_filters() -> None:
    """Czyści wszystkie filtry (np. po wczytaniu nowego zbioru danych)."""
    for key in list(st.session_state.keys()):
        if key.startswith(("flt_", "w_")):
            del st.session_state[key]


def _default_stations(ds: Dataset, pollutant: str) -> list[str]:
    """Domyślny wybór stacji: wszystkie, a przy dużej sieci - 5 o największej liczbie pomiarów."""
    codes = ds.stations_for(pollutant)
    if len(codes) <= 8:
        return codes
    sub = ds.catalog[ds.catalog["pollutant"] == pollutant].groupby("station_code")["n"].sum()
    return list(sub.sort_values(ascending=False).index[:5])


def _on_pollutant_change(ds: Dataset) -> None:
    """Po zmianie wskaźnika zmieniają się dostępne stacje, daty i uśrednianie - czyścimy zależne widżety."""
    st.session_state["flt_pollutant"] = st.session_state["w_pollutant"]
    for key in ("w_stations", "w_dates", "w_averaging", "flt_averaging", "flt_dates"):
        st.session_state.pop(key, None)
    pollutant = st.session_state["flt_pollutant"]
    available = set(ds.stations_for(pollutant))
    kept = [s for s in st.session_state.get("flt_stations", []) if s in available]
    st.session_state["flt_stations"] = kept or _default_stations(ds, pollutant)


def _sync(widget_key: str, state_key: str) -> None:
    st.session_state[state_key] = st.session_state[widget_key]


def _sync_dates() -> None:
    value = st.session_state["w_dates"]
    if isinstance(value, (tuple, list)) and len(value) == 2:
        st.session_state["flt_dates"] = (value[0], value[1])


def _sync_station_single() -> None:
    st.session_state["flt_stations"] = [st.session_state["w_station_single"]]


def render_filter_bar(ds: Dataset, *, multi_station: bool = True) -> FilterState | None:
    """Rysuje pasek filtrów i zwraca aktualny wybór; None, gdy wybór jest niepełny.

    Strony powinny wywołać ``st.stop()``, gdy otrzymają None.
    """
    state = st.session_state
    pollutants = ds.pollutants

    if state.get("flt_pollutant") not in pollutants:
        state["flt_pollutant"] = "PM10" if "PM10" in pollutants else pollutants[0]
    pollutant = state["flt_pollutant"]

    available = ds.stations_for(pollutant)
    current = [s for s in state.get("flt_stations", []) if s in available]
    if "flt_stations" not in state or (not current and "w_stations" not in state):
        current = _default_stations(ds, pollutant)
    state["flt_stations"] = current

    lo, hi = ds.date_bounds(pollutant)
    saved = state.get("flt_dates")
    if saved is None or saved[1] < lo or saved[0] > hi:
        saved = (lo, hi)
    start, end = max(saved[0], lo), min(saved[1], hi)
    state["flt_dates"] = (start, end)

    averaging_opts = ds.averaging_options(pollutant)
    if state.get("flt_averaging") not in averaging_opts:
        state["flt_averaging"] = ds.default_averaging(pollutant)

    with st.container(key="filter_bar"):
        c1, c2, c3, c4 = st.columns([1.3, 3.4, 2.3, 1.5])
        c1.selectbox(
            "Zanieczyszczenie", pollutants, index=pollutants.index(pollutant),
            format_func=pollutant_label, key="w_pollutant",
            on_change=_on_pollutant_change, args=(ds,),
            help="Wskaźnik jakości powietrza. Nie każda stacja mierzy każdy wskaźnik.",
        )
        if multi_station:
            c2.multiselect(
                "Stacje pomiarowe", available, default=current, format_func=ds.station_label,
                key="w_stations", on_change=_sync, args=("w_stations", "flt_stations"),
                help=f"Stacje, na których zmierzono {pollutant_label(pollutant)}.",
            )
        else:
            idx = available.index(current[0]) if current else 0
            c2.selectbox(
                "Stacja pomiarowa", available, index=idx, format_func=ds.station_label,
                key="w_station_single", on_change=_sync_station_single,
            )
        c3.date_input(
            "Zakres dat", value=(start, end), min_value=lo, max_value=hi, format="DD.MM.YYYY",
            key="w_dates", on_change=_sync_dates,
            help="Zakres ogranicza się do okresu, w którym wybrany wskaźnik ma pomiary.",
        )
        if len(averaging_opts) > 1:
            c4.selectbox(
                "Czas uśredniania danych", averaging_opts, index=averaging_opts.index(state["flt_averaging"]),
                key="w_averaging", on_change=_sync, args=("w_averaging", "flt_averaging"),
                help="Ten wskaźnik występuje w kilku rozdzielczościach. Każdą analizuję osobno, nie łączę ich w jednej serii.",
            )
        else:
            c4.markdown(
                f'<div class="ap-caption" style="padding-top:1.7rem">Dane: uśrednianie <b>{averaging_opts[0] or "nieznane"}</b></div>',
                unsafe_allow_html=True,
            )

    stations = tuple(state["flt_stations"])
    raw_dates = state.get("w_dates", (start, end))
    if not (isinstance(raw_dates, (tuple, list)) and len(raw_dates) == 2):
        st.info("Wybierz datę końcową zakresu.")
        return None
    if not stations:
        st.warning("Wybierz co najmniej jedną stację pomiarową.")
        return None

    unit, assumed = ds.unit_for(pollutant)
    return FilterState(
        pollutant=pollutant, stations=stations, start=state["flt_dates"][0], end=state["flt_dates"][1],
        averaging=state["flt_averaging"], unit=unit, unit_assumed=assumed,
    )

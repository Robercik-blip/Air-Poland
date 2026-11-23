"""Warstwa pamięci podręcznej (cache) łącząca dane z analizami.

Duże tabele przekazujemy do funkcji ``st.cache_data`` jako argumenty z prefiksem ``_``
(Streamlit ich wtedy nie haszuje), a tożsamość danych określamy przez ``dataset_id`` i
parametry filtrów. Dzięki temu zmiana np. wskaźnika nie powoduje kosztownego haszowania
milionów wierszy, a powtórny wybór tych samych filtrów jest natychmiastowy.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from modules import analysis
from modules.data_loader import Dataset, build_demo_dataset
from modules.filters import FilterState


@st.cache_data(show_spinner=False, max_entries=24)
def _selection_cached(_meas: pd.DataFrame, dataset_id: str, key: tuple) -> pd.DataFrame:
    pollutant, stations, start, end, averaging = key
    return analysis.filter_measurements(_meas, pollutant, list(stations), start, end, averaging)


def selection(ds: Dataset, flt: FilterState) -> pd.DataFrame:
    """Pomiary wybrane filtrami (kolumny: timestamp, station_code, value)."""
    return _selection_cached(ds.measurements, ds.dataset_id, flt.key)


@st.cache_data(show_spinner=False, max_entries=48)
def _aggregated_cached(
    _meas: pd.DataFrame, dataset_id: str, key: tuple, freq: str, step_seconds: float | None, min_coverage: float
) -> pd.DataFrame:
    data = _selection_cached(_meas, dataset_id, key)
    step = pd.Timedelta(seconds=step_seconds) if step_seconds else None
    return analysis.aggregate_series(data, freq, step, min_coverage)


def aggregated(ds: Dataset, flt: FilterState, freq: str, min_coverage: float) -> pd.DataFrame:
    """Dane zagregowane do okresów ``freq`` z kontrolą pokrycia."""
    return _aggregated_cached(ds.measurements, ds.dataset_id, flt.key, freq, flt.step_seconds, min_coverage)


@st.cache_data(show_spinner=False, max_entries=24)
def _overview_cached(
    _meas: pd.DataFrame, dataset_id: str, stations: tuple, start, end, default_avg: tuple
) -> pd.DataFrame:
    return analysis.pollutant_overview(_meas, start, end, list(stations), dict(default_avg))


def pollutant_overview(ds: Dataset, flt: FilterState) -> pd.DataFrame:
    """Przegląd wszystkich wskaźników zbioru dla wybranych stacji i dat."""
    default_avg = tuple((p, ds.default_averaging(p)) for p in ds.pollutants)
    all_stations = tuple(sorted(set(flt.stations)))
    return _overview_cached(ds.measurements, ds.dataset_id, all_stations, flt.start, flt.end, default_avg)


@st.cache_resource(show_spinner="Przygotowuję dane demonstracyjne…")
def demo_dataset() -> Dataset:
    """Zbiór demonstracyjny - generowany raz i współdzielony (tylko do odczytu)."""
    return build_demo_dataset()


def current_dataset() -> Dataset:
    """Aktywny zbiór danych bieżącej sesji (domyślnie demonstracyjny)."""
    if "dataset" not in st.session_state:
        st.session_state["dataset"] = demo_dataset()
    return st.session_state["dataset"]

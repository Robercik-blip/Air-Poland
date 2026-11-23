"""Eksploracja danych: tabela pomiarów, filtry, statystyki opisowe i eksport CSV."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from modules import services, ui
from modules.utils import fmt_date, fmt_int, pollutant_label

MAX_DISPLAY_ROWS = 50_000

ds = services.current_dataset()
ui.page_header(
    "Eksploracja danych",
    "Podgląd pomiarów użytych w analizach: filtrowanie, sortowanie, statystyki opisowe i eksport do CSV.",
    ds,
)

lo, hi = ds.overall_bounds()
with st.container(key="filter_bar"):
    c1, c2, c3, c4 = st.columns([1.6, 2.6, 2, 1.4])
    pollutants = c1.multiselect("Zanieczyszczenia", ds.pollutants, default=ds.pollutants[:1],
                                format_func=pollutant_label, key="ex_pollutants")
    avail_codes = sorted({c for p in pollutants for c in ds.stations_for(p)})
    stations = c2.multiselect("Stacje pomiarowe", avail_codes, default=avail_codes[:3],
                              format_func=ds.station_label, key="ex_stations")
    dates = c3.date_input("Zakres dat", value=(lo, hi), min_value=lo, max_value=hi, format="DD.MM.YYYY", key="ex_dates")
    sort_by = c4.selectbox("Sortuj według", ["Czas (rosnąco)", "Czas (malejąco)", "Wartość (malejąco)", "Wartość (rosnąco)"],
                           key="ex_sort")

if not pollutants or not stations:
    ui.callout("Wybierz co najmniej jedno zanieczyszczenie i jedną stację.", "info")
    st.stop()
if not (isinstance(dates, (tuple, list)) and len(dates) == 2):
    st.info("Wybierz datę końcową zakresu.")
    st.stop()


@st.cache_data(show_spinner=False, max_entries=12)
def _filter(_meas: pd.DataFrame, dataset_id: str, pollutants: tuple, stations: tuple, start, end) -> pd.DataFrame:
    ts = _meas["timestamp"]
    mask = _meas["pollutant"].isin(pollutants) & _meas["station_code"].isin(stations)
    mask &= (ts >= pd.Timestamp(start)) & (ts < pd.Timestamp(end) + pd.Timedelta(days=1))
    out = _meas.loc[mask].copy()
    for col in ("pollutant", "station_code", "unit", "averaging"):
        out[col] = out[col].astype(str)
    return out.reset_index(drop=True)


view = _filter(ds.measurements, ds.dataset_id, tuple(pollutants), tuple(stations), dates[0], dates[1])
if view.empty:
    ui.callout("Brak pomiarów dla wybranych filtrów.", "warn")
    st.stop()

ascending = sort_by.endswith("(rosnąco)")
key_col = "timestamp" if sort_by.startswith("Czas") else "value"
view = view.sort_values(key_col, ascending=ascending, kind="stable").reset_index(drop=True)

table = view.rename(columns={
    "timestamp": "Czas pomiaru", "station_code": "Kod stacji", "pollutant": "Wskaźnik",
    "value": "Wartość", "unit": "Jednostka", "averaging": "Uśrednianie",
})
table.insert(1, "Stacja", table["Kod stacji"].map(ds.station_label))

ui.kpi_row([
    ui.kpi_card("Wybrane pomiary", fmt_int(len(view)), accent="teal"),
    ui.kpi_card("Stacje", fmt_int(view["station_code"].nunique()), accent="blue"),
    ui.kpi_card("Wskaźniki", fmt_int(view["pollutant"].nunique()), accent="blue"),
    ui.kpi_card("Okres", fmt_date(view["timestamp"].min()), sub=f"do {fmt_date(view['timestamp'].max())}", accent="green"),
])
st.write("")

tab_table, tab_stats, tab_export = st.tabs(["Tabela pomiarów", "Statystyki opisowe", "Eksport CSV"])

with tab_table:
    shown = table.head(MAX_DISPLAY_ROWS)
    if len(table) > MAX_DISPLAY_ROWS:
        ui.callout(f"Wyświetlono pierwsze {fmt_int(MAX_DISPLAY_ROWS)} z {fmt_int(len(table))} wierszy. "
                   "Pełny wynik filtrowania dostępny jest w eksporcie CSV.", "info")
    st.dataframe(
        shown, hide_index=True, width="stretch", height=520,
        column_config={
            "Czas pomiaru": st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm"),
            "Wartość": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    ui.source_caption(ds, "Wartości to pomiary po czyszczeniu danych (bez braków i duplikatów).")

with tab_stats:
    grp = view.groupby(["pollutant", "unit", "averaging", "station_code"])["value"]
    stats = grp.agg(n="size", mean="mean", std="std", min="min", median="median", max="max").reset_index()
    stats["p95"] = grp.quantile(0.95).to_numpy()
    stats["Stacja"] = stats["station_code"].map(ds.station_label)
    stats = stats[["pollutant", "Stacja", "unit", "averaging", "n", "mean", "std", "min", "median", "p95", "max"]]
    stats.columns = ["Wskaźnik", "Stacja", "Jednostka", "Uśrednianie", "Liczba pomiarów", "Średnia",
                     "Odch. std.", "Minimum", "Mediana", "P95", "Maksimum"]
    st.dataframe(
        stats, hide_index=True, width="stretch",
        column_config={c: st.column_config.NumberColumn(format="%.2f") for c in
                       ["Średnia", "Odch. std.", "Minimum", "Mediana", "P95", "Maksimum"]},
    )
    ui.source_caption(ds, "Statystyki obliczone z wybranych pomiarów, osobno dla stacji i wskaźnika "
                          "(różne jednostki i czasy uśredniania nie są mieszane).")

with tab_export:
    st.markdown(
        f"Plik będzie zawierał **{fmt_int(len(view))}** wierszy wybranych filtrami, w kodowaniu UTF-8 (z BOM), "
        "z separatorem „;” i przecinkiem dziesiętnym – do bezpośredniego otwarcia w polskim Excelu."
    )
    if ds.is_synthetic:
        st.caption("Uwaga: eksport zawiera DEMONSTRACYJNE DANE SYNTETYCZNE – nie są to pomiary GIOŚ.")
    if st.button("Przygotuj plik CSV", key="ex_prepare"):
        out = view.rename(columns={"timestamp": "czas", "station_code": "kod_stacji", "pollutant": "wskaznik",
                                   "value": "wartosc", "unit": "jednostka", "averaging": "usrednianie"})
        st.session_state["ex_csv"] = out.to_csv(index=False, sep=";", decimal=",", date_format="%Y-%m-%d %H:%M",
                                                encoding="utf-8-sig").encode("utf-8-sig")
        st.session_state["ex_csv_rows"] = len(out)
    if "ex_csv" in st.session_state:
        st.download_button("Pobierz CSV", st.session_state["ex_csv"], file_name="air_poland_pomiary.csv",
                           mime="text/csv", key="ex_download")
        st.caption(f"Przygotowano {fmt_int(st.session_state['ex_csv_rows'])} wierszy. "
                   "Po zmianie filtrów przygotuj plik ponownie.")

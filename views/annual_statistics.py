"""Explore annual GIOŚ statistics without mixing them with measurement time series."""
from __future__ import annotations

import hashlib

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules import ui, visualizations as viz
from modules.data_processing import DataFormatError
from modules.gios_statistics import GiosAnnualStatistics, read_gios_annual_statistics
from modules.utils import fmt_int, normalise_text

SESSION_DATA_KEY = "gios_annual_statistics"
SESSION_DIGEST_KEY = "gios_annual_statistics_digest"
SESSION_FILENAME_KEY = "gios_annual_statistics_filename"


@st.cache_data(show_spinner=False, max_entries=2)
def _read_cached(content: bytes) -> GiosAnnualStatistics:
    return read_gios_annual_statistics(content)


ui.page_header(
    "Statystyki roczne GIOŚ",
    "Osobny widok dla skoroszytu „Statystyki z lat 2000–2025”. Dane pozostają rocznymi statystykami stacji "
    "i nie zastępują ani nie zmieniają aktywnego zbioru pomiarowego.",
)
uploaded = st.file_uploader(
    "Wgraj skoroszyt „Statystyki z lat 2000–2025” (.xlsx)",
    type=["xlsx"],
    key="gios_annual_statistics_upload",
)

if uploaded is not None:
    content = uploaded.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    if digest != st.session_state.get(SESSION_DIGEST_KEY) or SESSION_DATA_KEY not in st.session_state:
        try:
            with st.spinner("Wczytuję arkusze statystyk rocznych GIOŚ…"):
                loaded = _read_cached(content)
        except DataFormatError as exc:
            st.session_state.pop(SESSION_DATA_KEY, None)
            st.session_state.pop(SESSION_DIGEST_KEY, None)
            st.session_state.pop(SESSION_FILENAME_KEY, None)
            st.error(str(exc))
            st.stop()
        st.session_state[SESSION_DATA_KEY] = loaded
        st.session_state[SESSION_DIGEST_KEY] = digest
        st.session_state[SESSION_FILENAME_KEY] = uploaded.name

statistics = st.session_state.get(SESSION_DATA_KEY)
if not isinstance(statistics, GiosAnnualStatistics):
    st.info("Wybierz skoroszyt Excel GIOŚ, aby zobaczyć roczne statystyki.")
    st.stop()

data = statistics.data
filename = st.session_state.get(SESSION_FILENAME_KEY, uploaded.name if uploaded else "plik GIOŚ")
st.caption(
    f"Plik: {filename} · {fmt_int(len(data))} wierszy stacja–rok · "
    f"{fmt_int(data['station_code'].nunique())} kodów stacji · "
    f"lata {int(data['year'].min())}–{int(data['year'].max())}."
)
if statistics.skipped_rows:
    ui.callout(
        f"Pominięto {fmt_int(statistics.skipped_rows)} wierszy z niepoprawnym rokiem lub bez kodu stacji/wskaźnika.",
        "warn",
    )
ui.callout(
    "Wartości w skoroszycie są gotowymi statystykami rocznymi. Średnia dla wielu stacji poniżej to "
    "nieważona średnia arytmetyczna dostępnych wartości stacji — nie oficjalna, ważona średnia krajowa. "
    "Liczba i zestaw stacji mogą się zmieniać z roku na rok; braków danych nie zamieniamy na zero.",
    "info",
)

indicators = sorted(data["pollutant"].dropna().unique().tolist(), key=str.casefold)
preferred_indicators = ["PM10", "PM2.5", "NO2", "O3", "SO2", "CO", "C6H6"]
default_indicator = next((value for value in preferred_indicators if value in indicators), indicators[0])
indicator = st.selectbox(
    "Wskaźnik",
    indicators,
    index=indicators.index(default_indicator),
    key="gios_annual_indicator",
)
indicator_data = data[data["pollutant"] == indicator]
source_sheets = sorted(indicator_data["source_sheet"].dropna().unique().tolist())

metric_names = sorted(
    {
        metric
        for sheet in source_sheets
        for source_sheet, metric in statistics.metric_units
        if source_sheet == sheet and metric in indicator_data and indicator_data[metric].notna().any()
    },
    key=str.casefold,
)
if not metric_names:
    ui.callout("Brak dostępnych statystyk liczbowych dla wybranego wskaźnika.", "warn")
    st.stop()

default_metric = next((name for name in metric_names if normalise_text(name) == "srednia"), metric_names[0])
metric = st.selectbox(
    "Statystyka z arkusza",
    metric_names,
    index=metric_names.index(default_metric),
    key="gios_annual_metric",
)
description = statistics.metric_descriptions.get(normalise_text(metric))
if description:
    st.caption(description)

available = indicator_data[indicator_data[metric].notna()].copy()
averaging_options = sorted(available["averaging"].dropna().unique().tolist(), key=str.casefold)
if not averaging_options:
    ui.callout("Brak wartości dla tej statystyki.", "warn")
    st.stop()
most_common_averaging = available["averaging"].value_counts().index[0]
averaging = st.selectbox(
    "Czas uśredniania serii źródłowej",
    averaging_options,
    index=averaging_options.index(most_common_averaging),
    key="gios_annual_averaging",
    help="Czasy uśredniania z danych źródłowych nie są łączone.",
)

unit_by_sheet = {
    sheet: statistics.metric_units.get((sheet, metric), "")
    for sheet in source_sheets
    if sheet in available["source_sheet"].unique()
}
unit_options = sorted(
    {
        unit_by_sheet[sheet]
        for sheet in unit_by_sheet
        if available.loc[available["source_sheet"] == sheet, metric].notna().any()
    },
    key=str.casefold,
)
if len(unit_options) > 1:
    unit = st.selectbox("Jednostka", unit_options, key="gios_annual_unit")
else:
    unit = unit_options[0] if unit_options else ""
    if unit:
        st.caption(f"Jednostka: {unit}")

filtered = available[available["averaging"] == averaging]
if len(unit_options) > 1:
    allowed_sheets = [sheet for sheet, sheet_unit in unit_by_sheet.items() if sheet_unit == unit]
    filtered = filtered[filtered["source_sheet"].isin(allowed_sheets)]
if filtered.empty:
    ui.callout("Brak danych po zastosowaniu wybranych filtrów.", "warn")
    st.stop()

scope = st.selectbox(
    "Sposób prezentacji",
    [
        "Średnia arytmetyczna ze stacji",
        "Średnia arytmetyczna w województwie",
        "Pojedyncza stacja",
    ],
    key="gios_annual_scope",
)
region: str | None = None
station: str | None = None
if scope == "Średnia arytmetyczna w województwie":
    regions = sorted(filtered["voivodeship"].dropna().unique().tolist(), key=str.casefold)
    if not regions:
        ui.callout("W pliku nie ma nazw województw dla wybranego wskaźnika.", "warn")
        st.stop()
    region = st.selectbox("Województwo", regions, key="gios_annual_region")
    filtered = filtered[filtered["voivodeship"] == region]
elif scope == "Pojedyncza stacja":
    stations = sorted(filtered["station_code"].dropna().unique().tolist(), key=str.casefold)
    station = st.selectbox("Kod stacji", stations, key="gios_annual_station")
    filtered = filtered[filtered["station_code"] == station]

station_year = (
    filtered.groupby(["year", "station_code"], as_index=False, sort=True)[metric]
    .mean()
)
annual = (
    station_year.groupby("year", as_index=False, sort=True)
    .agg(value=(metric, "mean"), station_count=("station_code", "nunique"))
)
year_min, year_max = int(filtered["year"].min()), int(filtered["year"].max())
year_range = range(year_min, year_max + 1)
annual = (
    annual.set_index("year")
    .reindex(year_range)
    .rename_axis("year")
    .reset_index()
)
annual["station_count"] = annual["station_count"].fillna(0).astype(int)

unit_suffix = f" [{unit}]" if unit else ""
if scope == "Pojedyncza stacja":
    series_label = f"Stacja {station}"
    methodology = f"Roczne wartości ze stacji {station}; kod stacji pochodzi bezpośrednio ze skoroszytu."
elif scope == "Średnia arytmetyczna w województwie":
    series_label = f"{region} — średnia ze stacji"
    methodology = (
        f"Nieważona średnia arytmetyczna wartości stacji w województwie {region}; "
        "liczba stacji może się zmieniać między latami."
    )
else:
    series_label = "Średnia arytmetyczna ze stacji"
    methodology = "Nieważona średnia arytmetyczna dostępnych wartości stacji w każdym roku."

figure = go.Figure(
    go.Scatter(
        x=annual["year"],
        y=annual["value"],
        mode="lines+markers",
        connectgaps=False,
        name=series_label,
        line={"color": "#2dd4bf", "width": 2},
        marker={"size": 6},
        hovertemplate="Rok: %{x}<br>Wartość: %{y:.3f}<extra></extra>",
    )
)
viz.apply_theme(
    figure,
    height=430,
    y_title=f"{metric}{unit_suffix}",
    x_title="Rok",
    legend=False,
)
tick_years = list(range(year_min, year_max + 1, 2))
if year_max not in tick_years:
    tick_years.append(year_max)
figure.update_xaxes(tickmode="array", tickvals=tick_years, range=[year_min - 0.5, year_max + 0.5])
figure.update_layout(title=f"{indicator} · {metric}{unit_suffix}")
st.plotly_chart(figure, width="stretch", config=viz.PLOT_CONFIG)

st.caption(methodology)
table = annual.rename(
    columns={
        "year": "Rok",
        "value": f"{metric}{unit_suffix}",
        "station_count": "Liczba stacji",
    }
)
st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    column_config={
        f"{metric}{unit_suffix}": st.column_config.NumberColumn(format="%.3f"),
        "Liczba stacji": st.column_config.NumberColumn(format="%d"),
    },
)

detail_columns = [
    "year", "voivodeship", "zone_code", "zone_name", "station_code",
    "pollutant", "averaging", metric,
]
details = filtered[detail_columns].rename(
    columns={
        "year": "Rok",
        "voivodeship": "Województwo",
        "zone_code": "Kod strefy",
        "zone_name": "Nazwa strefy",
        "station_code": "Kod stacji",
        "pollutant": "Wskaźnik",
        "averaging": "Czas uśredniania",
        metric: metric + unit_suffix,
    }
)
with st.expander("Dane źródłowe stacja–rok i eksport"):
    st.dataframe(details, hide_index=True, width="stretch", height=360)
    st.download_button(
        "Pobierz wybrane dane CSV",
        data=details.to_csv(index=False, sep=";", decimal=",", encoding="utf-8-sig").encode("utf-8-sig"),
        file_name="gios_statystyki_roczne.csv",
        mime="text/csv",
    )

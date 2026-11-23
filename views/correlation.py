"""Analiza korelacji między zanieczyszczeniami."""
from __future__ import annotations

from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules import advanced_analysis as advanced, analysis, services, ui, visualizations as viz
from modules.config import DEFAULT_MIN_COVERAGE
from modules.filters import render_filter_bar
from modules.utils import pollutant_label

ds = services.current_dataset()
ui.page_header(
    "Analiza korelacji",
    "Porównuj współzmienność stężeń w czasie na jednej stacji lub pomiędzy profilami stacji.",
    ds,
)
flt = render_filter_bar(ds)
if flt is None:
    st.stop()

eligible = ds.measurements
eligible = eligible.loc[
    eligible["station_code"].isin(flt.stations)
    & eligible["averaging"].astype(str).eq(flt.averaging)
    & (eligible["timestamp"] >= pd.Timestamp(flt.start))
    & (eligible["timestamp"] < pd.Timestamp(flt.end) + timedelta(days=1))
]
available_pollutants = [p for p in ds.pollutants if p in set(eligible["pollutant"])]
default_pollutants = available_pollutants[: min(4, len(available_pollutants))]
if flt.pollutant in available_pollutants and flt.pollutant not in default_pollutants:
    default_pollutants.insert(0, flt.pollutant)

selected_pollutants = st.multiselect(
    "Wskaźniki do porównania",
    available_pollutants,
    default=default_pollutants,
    format_func=pollutant_label,
    key="corr_pollutants",
    help="Wszystkie wskaźniki analizowane są dla tego samego czasu uśredniania i zakresu dat.",
)
if len(selected_pollutants) < 2:
    ui.callout("Wybierz co najmniej dwa wskaźniki dostępne w bieżącym wyborze stacji i okresu.", "warn")
    st.stop()

mode = st.radio(
    "Jednostka obserwacji",
    ["Szereg czasowy jednej stacji", "Profile wybranych stacji"],
    horizontal=True,
    key="corr_mode",
    help="Szereg czasowy dopasowuje wskaźniki po identycznym czasie. Profile porównują średnie wskaźników między stacjami.",
)
selected = advanced.select_analysis_measurements(
    ds.measurements, selected_pollutants, list(flt.stations), flt.start, flt.end, flt.averaging
)
conflicts = advanced.unit_conflicts(selected, selected_pollutants)
if conflicts:
    details = "; ".join(
        f"{pollutant_label(p)}: {', '.join(units)}" for p, units in conflicts.items()
    )
    ui.callout(f"Nie można łączyć różnych jednostek tego samego wskaźnika. Sprawdź dane: {details}.", "warn")
    st.stop()

if mode == "Szereg czasowy jednej stacji":
    station = st.selectbox(
        "Stacja do analizy szeregu",
        list(flt.stations),
        format_func=ds.station_label,
        key="corr_station",
    )
    selected = selected.loc[selected["station_code"] == station]
    frequency_options = [
        "source",
        *(freq for freq in ("D", "MS", "YS") if freq in analysis.allowed_freqs(flt.step)),
    ]
    frequency_labels = {
        "source": f"Dane źródłowe ({flt.averaging})",
        "D": "Średnie dobowe",
        "MS": "Średnie miesięczne",
        "YS": "Średnie roczne",
    }
    frequency = st.selectbox(
        "Agregacja szeregu",
        frequency_options,
        format_func=frequency_labels.get,
        key="corr_frequency",
    )
    min_coverage = DEFAULT_MIN_COVERAGE
    if frequency != "source":
        min_coverage = st.slider(
            "Minimalne pokrycie agregatu",
            0.5,
            1.0,
            DEFAULT_MIN_COVERAGE,
            0.05,
            key="corr_min_coverage",
            help="Agregaty poniżej progu są traktowane jako brak danych.",
        )
    try:
        matrix = advanced.time_matrix(
            selected, selected_pollutants, frequency=frequency, min_coverage=min_coverage
        )
    except ValueError as exc:
        ui.callout(str(exc), "warn")
        st.stop()
    observation_label = ds.station_label(station)
else:
    matrix = advanced.station_profile_matrix(selected, selected_pollutants, list(flt.stations))
    observation_label = "stacje"
    st.caption(
        "Profil stacji = średnia z dostępnych pomiarów w wybranym zakresie; nie jest to "
        "normatywna średnia roczna. Stacje bez kompletu danych są pomijane parami w korelacji."
    )

if matrix.empty or matrix.dropna(how="all").empty:
    ui.callout("Brak wspólnych danych dla wybranych wskaźników, stacji i okresu.", "warn")
    st.stop()

max_observations = max(3, min(len(matrix), 100))
min_observations = st.slider(
    "Minimalna liczba wspólnych obserwacji",
    min_value=3,
    max_value=max_observations,
    value=min(10, max_observations),
    key="corr_min_observations",
    help="Każda para wskaźników jest liczona wyłącznie dla wierszy, w których oba pomiary są dostępne.",
)
pearson, spearman, counts = advanced.correlation_matrices(
    matrix, min_observations=min_observations
)
ui.kpi_row([
    ui.kpi_card("Obserwacje", f"{len(matrix):,}".replace(",", " "), sub=observation_label),
    ui.kpi_card("Wskaźniki", str(len(selected_pollutants))),
    ui.kpi_card("Metody", "Pearson · Spearman"),
])

if not pearson.notna().any().any():
    ui.callout(
        "Żadna para nie ma wystarczającej liczby wspólnych obserwacji albo zmienności. "
        "Zwiększ zakres danych lub zmniejsz wymagane minimum.",
        "warn",
    )


def _heatmap(correlations: pd.DataFrame, method: str) -> go.Figure:
    labels = [pollutant_label(p) for p in correlations.columns]
    fig = go.Figure(go.Heatmap(
        z=correlations.to_numpy(),
        x=labels,
        y=labels,
        customdata=counts.to_numpy(),
        text=correlations.round(2).astype(str).to_numpy(),
        texttemplate="%{text}",
        zmin=-1,
        zmax=1,
        colorscale=[[0, "#ef4444"], [0.5, "#facc15"], [1, "#22c55e"]],
        colorbar=dict(title="r"),
        hovertemplate=(
            "%{y} × %{x}<br>" + method + ": %{z:.3f}<br>"
            "Wspólne obserwacje: %{customdata}<extra></extra>"
        ),
    ))
    return viz.apply_theme(fig, height=max(360, 70 * len(labels)), legend=False)


tab_pearson, tab_spearman = st.tabs(["Pearson", "Spearman"])
with tab_pearson:
    st.plotly_chart(_heatmap(pearson, "Pearson r"), width="stretch", config=viz.PLOT_CONFIG)
with tab_spearman:
    st.plotly_chart(_heatmap(spearman, "Spearman ρ"), width="stretch", config=viz.PLOT_CONFIG)

ui.callout(
    "Wartość bliska +1 oznacza dodatnią, a bliska −1 ujemną zależność liniową (Pearson) "
    "lub monotoniczną (Spearman); okolice 0 oznaczają słabą zależność danego rodzaju. "
    "Korelacja nie dowodzi związku przyczynowego. Wartości są wyliczane wyłącznie na "
    "wspólnych obserwacjach każdej pary; liczebność pary sprawdzisz po najechaniu na komórkę.",
    "info",
)
ui.source_caption(ds, "Wartości nie są uzupełniane zerami; różne czasy uśredniania nie są łączone.")

"""Analiza głównych składowych i klasteryzacja."""
from __future__ import annotations

from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from scipy.cluster.hierarchy import dendrogram

from modules import advanced_analysis as advanced, analysis, services, ui, visualizations as viz
from modules.config import DEFAULT_MIN_COVERAGE
from modules.filters import render_filter_bar
from modules.utils import pollutant_label

ds = services.current_dataset()
ui.page_header(
    "Analiza wielowymiarowa",
    "PCA i klasteryzacja profili zanieczyszczeń dla stacji lub obserwacji czasowych.",
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
default_pollutants = available_pollutants[: min(5, len(available_pollutants))]
if flt.pollutant in available_pollutants and flt.pollutant not in default_pollutants:
    default_pollutants.insert(0, flt.pollutant)

selected_pollutants = st.multiselect(
    "Wskaźniki do analizy",
    available_pollutants,
    default=default_pollutants,
    format_func=pollutant_label,
    key="multi_pollutants",
    help="Każdy wskaźnik musi mieć ten sam źródłowy czas uśredniania.",
)
if len(selected_pollutants) < 2:
    ui.callout("Wybierz co najmniej dwa wskaźniki dostępne dla wybranych stacji i okresu.", "warn")
    st.stop()

mode = st.radio(
    "Rodzaj obserwacji",
    ["Profil stacji", "Szereg czasowy jednej stacji"],
    horizontal=True,
    key="multi_mode",
    help="Profil = średnie wskaźników dla każdej stacji. Szereg czasowy = wspólne obserwacje jednej stacji.",
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

if mode == "Profil stacji":
    matrix = advanced.station_profile_matrix(selected, selected_pollutants, list(flt.stations))
    st.caption(
        "Każdy wiersz to stacja, a jej cechy są średnimi z dostępnych pomiarów w okresie. "
        "To eksploracyjne profile, nie roczne wartości normatywne."
    )
else:
    station = st.selectbox(
        "Stacja do analizy szeregu",
        list(flt.stations),
        format_func=ds.station_label,
        key="multi_station",
    )
    selected = selected.loc[selected["station_code"] == station]
    frequency_options = ["source", *(
        freq for freq in ("D", "MS", "YS") if freq in analysis.allowed_freqs(flt.step)
    )]
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
        key="multi_frequency",
    )
    min_coverage = DEFAULT_MIN_COVERAGE
    if frequency != "source":
        min_coverage = st.slider(
            "Minimalne pokrycie agregatu",
            0.5,
            1.0,
            DEFAULT_MIN_COVERAGE,
            0.05,
            key="multi_min_coverage",
        )
    try:
        matrix = advanced.time_matrix(
            selected, selected_pollutants, frequency=frequency, min_coverage=min_coverage
        )
    except ValueError as exc:
        ui.callout(str(exc), "warn")
        st.stop()
    st.caption(f"Obserwacja: {ds.station_label(station)}.")

if matrix.empty or matrix.dropna(how="all").empty:
    ui.callout("Brak danych dla wybranych wskaźników, stacji i zakresu.", "warn")
    st.stop()

tab_pca, tab_clusters = st.tabs(["PCA", "Klasteryzacja"])
with tab_pca:
    try:
        pca = advanced.pca_analysis(matrix)
    except ValueError as exc:
        ui.callout(str(exc), "warn")
    else:
        if pca.dropped_rows:
            ui.callout(
                f"PCA pomija {pca.dropped_rows} obserwacji z brakami, aby zachować wspólny zestaw cech.",
                "info",
            )
        if pca.constant_features:
            ui.callout(
                "Pominięto cechy bez zmienności: " + ", ".join(pca.constant_features) + ".",
                "info",
            )
        variance_text = " · ".join(
            f"{name}: {value:.1%}" for name, value in pca.explained_variance.iloc[:2].items()
        )
        ui.kpi_row([
            ui.kpi_card("Obserwacje kompletne", f"{len(pca.samples):,}".replace(",", " ")),
            ui.kpi_card("Cechy użyte", str(pca.samples.shape[1])),
            ui.kpi_card("Wariancja PC1 + PC2", f"{pca.explained_variance.iloc[:2].sum():.1%}"),
        ])

        left, right = st.columns(2)
        with left:
            ui.section_title("Rzut obserwacji na składowe")
            score_fig = go.Figure(go.Scatter(
                x=pca.scores["PC1"],
                y=pca.scores["PC2"],
                mode="markers",
                marker=dict(color="#2dd4bf", size=9, opacity=0.82),
                text=[str(index) for index in pca.scores.index],
                hovertemplate="%{text}<br>PC1: %{x:.3f}<br>PC2: %{y:.3f}<extra></extra>",
            ))
            score_fig.update_xaxes(title=f"PC1 ({pca.explained_variance['PC1']:.1%})")
            score_fig.update_yaxes(title=f"PC2 ({pca.explained_variance['PC2']:.1%})")
            st.plotly_chart(
                viz.apply_theme(score_fig, height=400, legend=False),
                width="stretch",
                config=viz.PLOT_CONFIG,
            )
        with right:
            ui.section_title("Wariancja wyjaśniona")
            cumulative = pca.explained_variance.cumsum()
            variance_fig = go.Figure()
            variance_fig.add_bar(
                x=pca.explained_variance.index,
                y=pca.explained_variance,
                name="Składowa",
                marker_color="#38bdf8",
                hovertemplate="%{x}: %{y:.1%}<extra></extra>",
            )
            variance_fig.add_scatter(
                x=cumulative.index,
                y=cumulative,
                name="Skumulowana",
                mode="lines+markers",
                yaxis="y2",
                line=dict(color="#f59e0b"),
                hovertemplate="%{x}: %{y:.1%}<extra></extra>",
            )
            variance_fig.update_layout(yaxis=dict(title="Udział wariancji", tickformat=".0%"),
                                       yaxis2=dict(title="Skumulowany udział", tickformat=".0%",
                                                   overlaying="y", side="right", range=[0, 1]))
            st.plotly_chart(
                viz.apply_theme(variance_fig, height=400, legend=True),
                width="stretch",
                config=viz.PLOT_CONFIG,
            )

        ui.section_title("Ładunki zmiennych")
        loading_fig = go.Figure()
        for component in ("PC1", "PC2"):
            loading_fig.add_bar(
                x=[pollutant_label(p) for p in pca.loadings.index],
                y=pca.loadings[component],
                name=component,
            )
        loading_fig.add_hline(y=0, line_color="rgba(255,255,255,0.3)")
        st.plotly_chart(
            viz.apply_theme(loading_fig, height=330, y_title="Ładunek", legend=True),
            width="stretch",
            config=viz.PLOT_CONFIG,
        )
        st.caption(
            f"Pierwsze dwie składowe wyjaśniają łącznie {pca.explained_variance.iloc[:2].sum():.1%} "
            f"zmienności. {variance_text}. PCA wykonano po standaryzacji cech."
        )

with tab_clusters:
    complete = matrix.apply(pd.to_numeric, errors="coerce").dropna(how="any")
    n_complete = len(complete)
    if n_complete < 3 or complete.nunique().lt(2).sum() > len(complete.columns) - 2:
        ui.callout("Klasteryzacja wymaga co najmniej trzech kompletnych obserwacji i dwóch zmiennych zmiennych.", "warn")
    else:
        algorithm_choice = st.radio(
            "Metoda grupowania",
            ["K-średnich", "Hierarchiczna (Ward)"],
            horizontal=True,
            key="multi_cluster_method",
        )
        algorithm = "kmeans" if algorithm_choice == "K-średnich" else "hierarchical"
        n_clusters = st.slider(
            "Liczba klastrów",
            min_value=2,
            max_value=min(8, n_complete - 1),
            value=min(3, n_complete - 1),
            key="multi_n_clusters",
        )
        try:
            clustered = advanced.cluster_analysis(
                matrix, algorithm=algorithm, n_clusters=n_clusters
            )
        except ValueError as exc:
            ui.callout(str(exc), "warn")
        else:
            if clustered.dropped_rows:
                ui.callout(
                    f"Klasteryzacja pomija {clustered.dropped_rows} obserwacji z brakami.",
                    "info",
                )
            if clustered.constant_features:
                ui.callout(
                    "Pominięto cechy bez zmienności: " + ", ".join(clustered.constant_features) + ".",
                    "info",
                )
            if clustered.sampled:
                ui.callout(
                    "Dla hierarchicznej metody Ward użyto powtarzalnej próbki 500 obserwacji ze względu na koszt obliczeń.",
                    "info",
                )
            if clustered.silhouette is None:
                ui.callout("Nie można obliczyć silhouette dla uzyskanych etykiet klastrów.", "warn")
            else:
                st.metric("Współczynnik silhouette", f"{clustered.silhouette:.3f}")

            try:
                pca_for_clusters = advanced.pca_analysis(matrix)
                scores = pca_for_clusters.scores.join(clustered.labels)
                cluster_fig = go.Figure()
                for cluster_id, group in scores.groupby("cluster"):
                    cluster_fig.add_trace(go.Scatter(
                        x=group["PC1"],
                        y=group["PC2"],
                        mode="markers",
                        name=f"Klaster {int(cluster_id) + 1}",
                        text=[str(index) for index in group.index],
                        hovertemplate="%{text}<br>PC1: %{x:.3f}<br>PC2: %{y:.3f}<extra></extra>",
                    ))
                cluster_fig.update_xaxes(title=f"PC1 ({pca_for_clusters.explained_variance['PC1']:.1%})")
                cluster_fig.update_yaxes(title=f"PC2 ({pca_for_clusters.explained_variance['PC2']:.1%})")
                st.plotly_chart(
                    viz.apply_theme(cluster_fig, height=420, legend=True),
                    width="stretch",
                    config=viz.PLOT_CONFIG,
                )
            except ValueError as exc:
                ui.callout(f"Nie udało się pokazać projekcji klastrów PCA: {exc}", "warn")

            group_means = clustered.samples.assign(cluster=clustered.labels).groupby("cluster").mean()
            group_means.index = [f"Klaster {int(value) + 1}" for value in group_means.index]
            group_means.columns = [pollutant_label(column) for column in group_means.columns]
            st.dataframe(group_means.style.format("{:.3f}"), width="stretch")

            if clustered.linkage_matrix is not None:
                tree = dendrogram(clustered.linkage_matrix, no_labels=True, no_plot=True)
                dendrogram_fig = go.Figure()
                for xs, ys in zip(tree["icoord"], tree["dcoord"]):
                    dendrogram_fig.add_scatter(
                        x=[value / 10 for value in xs],
                        y=ys,
                        mode="lines",
                        line=dict(color="#38bdf8", width=1),
                        showlegend=False,
                        hoverinfo="skip",
                    )
                st.plotly_chart(
                    viz.apply_theme(
                        dendrogram_fig,
                        height=420,
                        x_title="Obserwacje (bez etykiet)",
                        y_title="Odległość Ward",
                        legend=False,
                    ),
                    width="stretch",
                    config=viz.PLOT_CONFIG,
                )

ui.callout(
    "PCA i klastry są metodami eksploracyjnymi. Klastry nie są kategoriami ryzyka zdrowotnego; "
    "wynik zależy od wybranych cech, okresu, braków danych i standaryzacji.",
    "info",
)
ui.source_caption(ds, "Analizy wykorzystują wybrane wskaźniki z tym samym źródłowym czasem uśredniania.")

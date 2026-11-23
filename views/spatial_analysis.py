"""Analiza przestrzenna: mapa stacji pomiarowych z wartościami wybranego wskaźnika."""
from __future__ import annotations

from dataclasses import replace

import streamlit as st
from streamlit_folium import st_folium

from modules import analysis, maps, services, ui, visualizations as viz
from modules.config import DEFAULT_MIN_COVERAGE, REFERENCE_NOTE
from modules.filters import render_filter_bar
from modules.utils import fmt_int, fmt_number, fmt_percent, pollutant_label

ds = services.current_dataset()
ui.page_header(
    "Analiza przestrzenna",
    "Rozmieszczenie stacji pomiarowych i różnice średnich stężeń między nimi w wybranym okresie. "
    "Na mapie pokazano wyłącznie stacje pomiarowe, których współrzędne pochodzą z danych lub metadanych.",
    ds,
)
if ds.is_synthetic:
    ui.callout("Dane demonstracyjne: współrzędne to środki miast (punkty umowne), nie lokalizacje rzeczywistych stacji.", "demo")

flt = render_filter_bar(ds)
if flt is None:
    st.stop()

data = services.selection(ds, flt)
if data.empty:
    ui.callout("Brak pomiarów dla wybranych filtrów.", "warn")
    st.stop()

pl = pollutant_label(flt.pollutant)
unit = flt.unit
summary = analysis.station_summary(data, flt.start, flt.end, flt.step)
merged = maps.stations_with_values(ds.stations, summary, ds.station_labels)

left, right = st.columns([1.7, 1])
with left:
    with st.container(border=True):
        ui.section_title(f"Mapa stacji – średnie stężenie {pl}")
        fmap, missing = maps.build_station_map(merged, pollutant=flt.pollutant, unit=unit)
        if fmap is None:
            ui.callout(
                "W danych nie ma współrzędnych stacji, więc mapa nie może zostać narysowana. "
                "Współrzędnych nie uzupełniamy samodzielnie – wczytaj plik z metadanymi stacji "
                "(kolumny: kod stacji, szerokość i długość geograficzna) w zakładce „Źródło danych i import”.", "warn",
            )
        else:
            result = st_folium(fmap, height=560, use_container_width=True,
                               returned_objects=["last_object_clicked_tooltip"], key="spatial_map")
            if missing:
                st.caption(f"{missing} z {len(merged)} stacji nie ma współrzędnych – pominięto na mapie.")
            ui.source_caption(ds, "Kolor = średnia z pomiarów w wybranym okresie względem poziomów odniesienia. "
                                  + REFERENCE_NOTE)

with right:
    with st.container(border=True):
        ui.section_title("Szczegóły stacji")
        codes = list(summary["station_code"])
        choice = st.selectbox("Stacja", codes, format_func=ds.station_label, key="spatial_station")
        row = merged[merged["station_code"] == choice].iloc[0]
        place = ", ".join(str(v) for v in (row.get("city"), row.get("voivodeship")) if v and str(v) != "nan")
        st.markdown(f"**{ds.station_label(choice)}**" + (f"  \n{place}" if place else ""))
        m1, m2 = st.columns(2)
        m1.metric(f"Średnia {pl} [{unit}]", fmt_number(row["mean"]))
        m2.metric(f"Maksimum [{unit}]", fmt_number(row["max"]))
        m3, m4 = st.columns(2)
        m3.metric("Pomiary", fmt_int(row["n"]))
        m4.metric("Kompletność", fmt_percent(row["completeness"]))
        if row["latitude"] == row["latitude"] and row["longitude"] == row["longitude"]:
            st.caption(f"Współrzędne: {row['latitude']:.4f}°N, {row['longitude']:.4f}°E")
        else:
            st.caption("Brak współrzędnych tej stacji w danych.")
        one = replace(flt, stations=(choice,))
        agg = services.aggregated(ds, one, "MS", DEFAULT_MIN_COVERAGE) if "MS" in analysis.allowed_freqs(flt.step) else None
        if agg is not None and not agg.empty:
            net = analysis.network_mean(agg)
            st.plotly_chart(
                viz.monthly_series_chart(net, pollutant=flt.pollutant, unit=unit, y_title=f"{pl} [{unit}]", height=230),
                width="stretch", config=viz.PLOT_CONFIG,
            )
            st.caption("Średnie miesięczne tej stacji (miesiące z pokryciem ≥ 75%).")

with st.container(border=True):
    ui.section_title("Ranking stacji")
    st.plotly_chart(
        viz.station_bar_chart(summary, ds.station_labels, pollutant=flt.pollutant, unit=unit, height=max(260, 34 * len(summary) + 90)),
        width="stretch", config=viz.PLOT_CONFIG,
    )
ui.callout(
    "Wizualizacja choropletyczna (np. średnie wojewódzkie) wymaga granic województw – zewnętrznego pliku GeoJSON. "
    "Nie jest dołączona w tej wersji, aby nie opierać pracy na niezweryfikowanych geometriach.", "info",
)

"""Panel główny: KPI, szereg czasowy, przegląd wskaźników, porównanie stacji i mapa."""
from __future__ import annotations

import html

import streamlit as st
from streamlit_folium import st_folium

from modules import analysis, maps, services, ui, visualizations as viz
from modules.config import DEFAULT_MIN_COVERAGE, DEMO_LABEL, REFERENCE_NOTE
from modules.filters import render_filter_bar
from modules.utils import (
    fmt_date, fmt_int, fmt_number, fmt_percent, pollutant_label, station_color_map,
)

ds = services.current_dataset()
ui.page_header(
    "Panel główny",
    "Przegląd jakości powietrza dla wybranych stacji pomiarowych i zakresu dat. "
    "Wszystkie wskaźniki i wykresy aktualizują się po zmianie filtrów.",
    ds,
)
if ds.is_synthetic:
    ui.callout(
        f"<b>{DEMO_LABEL}.</b> Wyświetlane liczby nie są pomiarami GIOŚ i służą wyłącznie do "
        "zaprezentowania interfejsu. Rzeczywiste pliki wczytasz w zakładce „Źródło danych i import”.",
        "demo",
    )

flt = render_filter_bar(ds)
if flt is None:
    st.stop()

data = services.selection(ds, flt)
if data.empty:
    ui.callout("Brak pomiarów dla wybranych filtrów. Rozszerz zakres dat lub wybierz inne stacje.", "warn")
    st.stop()

pl = pollutant_label(flt.pollutant)
unit = flt.unit
unit_txt = f" [{unit}]" if unit else ""
y_title = f"Stężenie {pl}{unit_txt}"
kpi = analysis.compute_kpis(data, flt.start, flt.end, flt.step)

# --- Karty KPI ----------------------------------------------------------------
max_sub = f"{html.escape(ds.station_label(kpi['max_station']))}<br>{fmt_date(kpi['max_time'])}"
ui.kpi_row([
    ui.kpi_card("Liczba stacji pomiarowych", fmt_int(kpi["n_stations"]),
                sub=f"z {fmt_int(len(ds.stations_for(flt.pollutant)))} mierzących {pl}", accent="blue"),
    ui.kpi_card("Liczba analizowanych pomiarów", fmt_int(kpi["n"]),
                sub=f"kompletność danych: {fmt_percent(kpi['completeness'])}", accent="teal"),
    ui.kpi_card(f"Średnie stężenie {pl}", fmt_number(kpi["mean"]), unit,
                sub=f"średnia z pomiarów, uśrednianie {flt.averaging or 'nieznane'}",
                accent="teal"),
    ui.kpi_card("Maksymalne stężenie", fmt_number(kpi["max"]), unit, sub=max_sub,
                accent="blue"),
    ui.kpi_card("Zakres analizowanych danych", f"{fmt_date(kpi['first'])}",
                sub=f"do {fmt_date(kpi['last'])}", accent="green"),
])
if flt.unit_assumed:
    ui.callout(f"Plik nie zawiera jednostki dla {pl}; wyświetlono jednostkę zakładaną ({unit}). Zweryfikuj ją.", "warn")
st.write("")

# --- Szereg czasowy + przegląd wskaźników ------------------------------------------
left, right = st.columns([2.1, 1])
with left:
    with st.container(border=True):
        options = analysis.allowed_freqs(flt.step)
        default = analysis.auto_freq(flt.start, flt.end, flt.step)
        head, ctrl = st.columns([2, 1])
        head.markdown(f'<div class="ap-section-title">Przebieg stężenia {pl} w czasie</div>', unsafe_allow_html=True)
        freq = ctrl.selectbox(
            "Agregacja", options, index=options.index(default), format_func=analysis.FREQ_LABELS.get,
            key="dash_freq", label_visibility="collapsed",
        )
        agg = services.aggregated(ds, flt, freq, DEFAULT_MIN_COVERAGE)
        net = analysis.network_mean(agg)
        ref = {"D": "daily", "YS": "annual"}.get(freq)
        st.plotly_chart(
            viz.band_chart(net, pollutant=flt.pollutant, unit=unit, y_title=y_title, ref_kind=ref, height=380),
            width="stretch", config=viz.PLOT_CONFIG,
        )
        n_bad = int((~agg["reliable"]).sum()) if not agg.empty else 0
        ui.source_caption(
            ds,
            f"Wartości: średnie {analysis.FREQ_LABELS[freq].lower()} ze stacji (obliczone z pomiarów); "
            f"okresy z pokryciem poniżej {fmt_percent(DEFAULT_MIN_COVERAGE)} pominięto"
            + (f" ({fmt_int(n_bad)})." if n_bad else "."),
        )

with right:
    with st.container(border=True):
        ui.section_title("Przegląd zanieczyszczeń", "Dla wybranych stacji i dat; różne jednostki nie są porównywane bezpośrednio.")
        ov = services.pollutant_overview(ds, flt)
        if ov.empty:
            st.caption("Brak danych.")
        else:
            rows = "".join(
                f"<tr><td>{pollutant_label(r.pollutant)} <small>{html.escape(ds.unit_for(r.pollutant)[0])}</small></td>"
                f"<td>{fmt_number(r.mean)}</td><td>{fmt_number(r.p95)}</td><td>{fmt_number(r.max)}</td>"
                f"<td>{int(r.stations)}</td></tr>"
                for r in ov.itertuples()
            )
            st.markdown(
                '<table class="ap-table"><thead><tr><th>Wskaźnik</th><th>Średnia</th><th>P95</th>'
                f"<th>Maks.</th><th>Stacje</th></tr></thead><tbody>{rows}</tbody></table>",
                unsafe_allow_html=True,
            )
            st.markdown('<div class="ap-caption" style="margin-top:0.6rem">P95 – 95. percentyl pomiarów. '
                        "Statystyki obliczone z pomiarów o domyślnym (najkrótszym) czasie uśredniania każdego wskaźnika.</div>",
                        unsafe_allow_html=True)

# --- Porównanie stacji + mapa -----------------------------------------------------
summary = analysis.station_summary(data, flt.start, flt.end, flt.step)
c1, c2 = st.columns([1.15, 1])
with c1:
    with st.container(border=True):
        ui.section_title(f"Porównanie stacji – średnie stężenie {pl}")
        st.plotly_chart(
            viz.station_bar_chart(summary, ds.station_labels, pollutant=flt.pollutant, unit=unit),
            width="stretch", config=viz.PLOT_CONFIG,
        )
        ui.source_caption(ds, REFERENCE_NOTE)
with c2:
    with st.container(border=True):
        ui.section_title("Mapa stacji pomiarowych", "Kolor oznacza średnie stężenie względem poziomów odniesienia.")
        merged = maps.stations_with_values(ds.stations, summary, ds.station_labels)
        fmap, missing = maps.build_station_map(merged, pollutant=flt.pollutant, unit=unit)
        if fmap is None:
            st.info("Brak współrzędnych geograficznych stacji w danych – mapa niedostępna. "
                    "Wczytaj plik metadanych stacji w zakładce „Źródło danych i import”.")
        else:
            st_folium(fmap, height=330, use_container_width=True, returned_objects=[], key="dash_map")
            if missing:
                st.caption(f"{missing} z {len(merged)} stacji nie ma współrzędnych i nie jest pokazane na mapie.")
            if ds.is_synthetic:
                st.caption("Dane demonstracyjne: punkty to środki miast, nie rzeczywiste lokalizacje stacji.")

# --- Informacje o zbiorze -----------------------------------------------------------
with st.expander("Informacje o zbiorze danych"):
    d0, d1 = ds.overall_bounds()
    st.markdown(
        f"**Nazwa:** {html.escape(ds.name)}  \n**Rodzaj źródła:** {ds.source_kind}  \n"
        f"**Zakres:** {fmt_date(d0)} – {fmt_date(d1)}  \n"
        f"**Stacje:** {fmt_int(ds.catalog['station_code'].nunique())} · "
        f"**Wskaźniki:** {', '.join(pollutant_label(p) for p in ds.pollutants)}  \n"
        f"**Pochodzenie:** {html.escape(ds.attribution)}"
    )
    for note in ds.report.notes + ds.report.warnings:
        st.caption("• " + note)

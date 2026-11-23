"""Analiza czasowa: szereg czasowy, średnie miesięczne i roczne, porównanie lat, sezony."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from modules import analysis, services, ui, visualizations as viz
from modules.config import DEFAULT_MIN_COVERAGE
from modules.filters import render_filter_bar
from modules.utils import PL_MONTHS_SHORT, fmt_int, fmt_number, fmt_percent, pollutant_label, station_color_map

ds = services.current_dataset()
ui.page_header(
    "Analiza czasowa",
    "Zmiany stężeń w czasie: od przebiegu godzinowego po trendy wieloletnie. Średnie z okresów, "
    "w których brakuje zbyt wielu pomiarów, są pomijane lub wyraźnie oznaczane.",
    ds,
)
flt = render_filter_bar(ds)
if flt is None:
    st.stop()

pl = pollutant_label(flt.pollutant)
unit = flt.unit
y_title = f"Stężenie {pl}" + (f" [{unit}]" if unit else "")
colors = station_color_map(ds.stations_for(flt.pollutant))

with st.container(key="ta_options"):
    o1, o2 = st.columns([1, 3])
    min_cov = o1.slider(
        "Minimalne pokrycie okresu", 0.5, 1.0, DEFAULT_MIN_COVERAGE, 0.05, format="%.2f", key="ta_min_cov",
        help="Średnia za okres (doba, miesiąc, rok) jest liczona tylko wtedy, gdy udział ważnych "
             "pomiarów w tym okresie jest co najmniej taki. Dla oceny jakości powietrza w UE "
             "stosuje się zwykle 75%.",
    )
    o2.markdown(
        '<div class="ap-caption" style="padding-top:1.6rem">Pokrycie = liczba ważnych pomiarów / liczba pomiarów '
        "oczekiwanych w pełnym okresie kalendarzowym. Niepełny rok lub miesiąc na brzegach zakresu "
        "jest więc automatycznie oznaczany jako niewiarygodny.</div>", unsafe_allow_html=True,
    )

data = services.selection(ds, flt)
if data.empty:
    ui.callout("Brak pomiarów dla wybranych filtrów.", "warn")
    st.stop()

tab_ts, tab_month, tab_year, tab_cmp, tab_season = st.tabs(
    ["Szereg czasowy", "Średnie miesięczne", "Średnie roczne", "Porównanie lat", "Sezony"]
)

# --- 1. Szereg czasowy ------------------------------------------------------------
with tab_ts:
    options = analysis.allowed_freqs(flt.step)
    default = analysis.auto_freq(flt.start, flt.end, flt.step)
    c1, c2, c3, c4 = st.columns([1.2, 1.2, 1.2, 1.4])
    freq = c1.selectbox("Agregacja czasowa", options, index=options.index(default),
                        format_func=analysis.FREQ_LABELS.get, key="ta_freq")
    show_stations = c2.checkbox("Linie stacji", value=True, key="ta_show_st")
    multi = len(flt.stations) > 1
    show_mean = c3.checkbox("Średnia ze stacji", value=multi, disabled=not multi, key="ta_show_mean")
    use_roll = c4.checkbox("Średnia krocząca", value=False, key="ta_roll")
    window = 7
    if use_roll:
        window = st.slider("Okno średniej kroczącej (liczba okresów agregacji)", 2, 60, 7, key="ta_window",
                           help="Np. przy agregacji dziennej okno 7 oznacza średnią z 7 kolejnych dób.")

    agg = services.aggregated(ds, flt, freq, min_cov)
    if agg.empty:
        ui.callout("Brak danych po agregacji.", "warn")
    else:
        if freq == "h" and (pd.Timestamp(flt.end) - pd.Timestamp(flt.start)).days > 400:
            ui.callout("Wybrano agregację godzinową dla zakresu dłuższego niż rok – wykres może się wolno rysować. "
                       "Rozważ agregację dzienną.", "info")
        wide = analysis.to_wide(agg, freq)
        mean_line = wide.mean(axis=1, skipna=True).where(wide.notna().any(axis=1)) if multi and show_mean else None
        rolling = None
        if use_roll:
            base = mean_line.to_frame("__mean__") if mean_line is not None else wide
            rolling = analysis.rolling_mean(base, window)
        ref = {"D": "daily", "YS": "annual"}.get(freq)
        st.plotly_chart(
            viz.timeseries_chart(
                wide, ds.station_labels, colors, pollutant=flt.pollutant, unit=unit, y_title=y_title,
                rolling=rolling, mean_line=mean_line, ref_kind=ref, show_stations=show_stations, height=480,
            ),
            width="stretch", config=viz.PLOT_CONFIG,
        )
        n_bad = int((~agg["reliable"]).sum())
        share = n_bad / len(agg)
        if freq != analysis.native_freq(flt.step) and n_bad:
            ui.callout(
                f"{fmt_int(n_bad)} okresów ({fmt_percent(share)}) ma pokrycie poniżej {fmt_percent(min_cov)} "
                "i zostało pominiętych – na wykresie widoczne jako przerwy.", "warn",
            )
        ui.source_caption(
            ds, f"Pokazano: średnie {analysis.FREQ_LABELS[freq].lower()} (wartości obliczone z pomiarów, "
                f"uśrednianie źródłowe: {flt.averaging or 'nieznane'}).",
        )

# --- 2. Średnie miesięczne -------------------------------------------------------
agg_m = services.aggregated(ds, flt, "MS", min_cov) if "MS" in analysis.allowed_freqs(flt.step) else pd.DataFrame()
with tab_month:
    net_m = analysis.network_mean(agg_m)
    if net_m.empty:
        ui.callout("Brak wiarygodnych średnich miesięcznych dla wybranego zakresu i progu pokrycia.", "warn")
    else:
        a, b = st.columns([1.6, 1])
        with a:
            ui.section_title("Średnie miesięczne w czasie", "Średnia ze stacji o wiarygodnej średniej miesięcznej.")
            st.plotly_chart(viz.monthly_series_chart(net_m, pollutant=flt.pollutant, unit=unit, y_title=y_title),
                            width="stretch", config=viz.PLOT_CONFIG)
        with b:
            ui.section_title("Typowy przebieg roczny", "Średnia dla miesięcy kalendarzowych; wąsy = odch. standardowe.")
            st.plotly_chart(
                viz.month_profile_chart(analysis.calendar_month_profile(agg_m), unit=unit, y_title=y_title),
                width="stretch", config=viz.PLOT_CONFIG,
            )
        ui.source_caption(ds, "Kolory słupków odnoszą się orientacyjnie do rocznych poziomów WHO/UE (średnia miesięczna nie jest "
                              "równoważna średniej rocznej). Odchylenie standardowe liczone między miesiącami z różnych lat – "
                              "przy mniej niż 3 latach jest tylko orientacyjne.")

# --- 3. Średnie roczne -------------------------------------------------------------
with tab_year:
    agg_y = services.aggregated(ds, flt, "YS", min_cov) if "YS" in analysis.allowed_freqs(flt.step) else pd.DataFrame()
    summary = analysis.annual_summary(agg_y)
    if summary.empty:
        ui.callout("Brak danych do obliczenia średnich rocznych.", "warn")
    else:
        st.plotly_chart(
            viz.annual_chart(summary, agg_y, ds.station_labels, colors, pollutant=flt.pollutant, unit=unit, y_title=y_title),
            width="stretch", config=viz.PLOT_CONFIG,
        )
        bad_years = summary.loc[~summary["reliable"], "year"].tolist()
        if bad_years:
            ui.callout(
                "Lata <b>" + ", ".join(str(y) for y in bad_years) + "</b> nie mają żadnej stacji z wymaganym pokryciem "
                f"({fmt_percent(min_cov)}) – słupki są kreskowane, a ich wartości <b>nie nadają się do porównań</b> "
                "z pozostałymi latami.", "warn",
            )
        partial = summary[(summary["reliable"]) & (summary["stations_ok"] < summary["stations"])]
        if not partial.empty:
            ui.callout("W niektórych latach część stacji ma zbyt niskie pokrycie, więc zestaw stacji w średniej "
                       "różni się między latami – może to wpływać na porównanie trendu.", "info")
        table = summary.assign(
            Rok=summary["year"], **{
                "Średnia roczna": summary["mean"].map(fmt_number),
                "Stacje wiarygodne": summary["stations_ok"].astype(str) + " z " + summary["stations"].astype(str),
                "Średnie pokrycie": summary["coverage"].map(fmt_percent),
                "Wiarygodna": summary["reliable"].map({True: "tak", False: "nie"}),
            },
        )[["Rok", "Średnia roczna", "Stacje wiarygodne", "Średnie pokrycie", "Wiarygodna"]]
        st.dataframe(table, hide_index=True, width="stretch")
        ui.source_caption(ds, "Średnia roczna jest wartością obliczoną: średnią ze średnich rocznych stacji "
                              "spełniających wymóg pokrycia. Linie poziomów odniesienia dotyczą średniej rocznej.")

# --- 4. Porównanie lat ------------------------------------------------------------
with tab_cmp:
    ym_all = analysis.year_month_means(agg_m, list(range(1990, 2100))) if not agg_m.empty else pd.DataFrame()
    if ym_all.empty:
        ui.callout("Brak wiarygodnych średnich miesięcznych do porównania lat.", "warn")
    else:
        years_available = sorted(ym_all["year"].unique().tolist())
        years = st.multiselect(
            "Lata do porównania", years_available, default=years_available[-3:], key="ta_years",
            help="Porównanie obejmuje tylko lata z zakresu dat wybranego w filtrach.",
        )
        if not years:
            ui.callout("Wybierz co najmniej jeden rok.", "info")
        else:
            ym = analysis.year_month_means(agg_m, years)
            st.plotly_chart(viz.year_comparison_chart(ym, years, unit=unit, y_title=y_title),
                            width="stretch", config=viz.PLOT_CONFIG)
            counts = ym.groupby("year")["month"].nunique().reindex(years).fillna(0).astype(int)
            incomplete = counts[counts < 12]
            if not incomplete.empty:
                ui.callout(
                    "Niepełne lata w porównaniu: " + "; ".join(f"<b>{y}</b> – {n} z 12 miesięcy" for y, n in incomplete.items())
                    + ". Różnice między latami mogą wynikać z odmiennych miesięcy, a nie ze zmiany jakości powietrza.", "warn",
                )
            pivot = ym.pivot(index="month", columns="year", values="value").reindex(range(1, 13))
            pivot.index = PL_MONTHS_SHORT
            st.dataframe(pivot.map(lambda v: fmt_number(v)).rename_axis("Miesiąc"), width="stretch")
            ui.source_caption(ds, "Wartości: średnie miesięczne ze stacji (obliczone). Puste pola – miesiąc bez "
                                  "wystarczającego pokrycia danymi.")

# --- 5. Sezony ------------------------------------------------------------------------
with tab_season:
    if "D" not in analysis.allowed_freqs(flt.step):
        ui.callout("Analiza sezonowa wymaga danych godzinowych lub dobowych.", "info")
    else:
        agg_d = services.aggregated(ds, flt, "D", min_cov)
        seasons = analysis.season_table(agg_d, min_cov)
        st.plotly_chart(viz.season_box_chart(seasons, pollutant=flt.pollutant, unit=unit, y_title=y_title),
                        width="stretch", config=viz.PLOT_CONFIG)
        if not seasons.empty:
            tbl = (seasons.groupby(["season_year", "season"], observed=True)["value"].mean().unstack("season")
                   .reindex(columns=[s for s in ["Zima", "Wiosna", "Lato", "Jesień"]]))
            st.dataframe(tbl.map(lambda v: fmt_number(v)).rename_axis("Rok sezonu"), width="stretch")
        ui.source_caption(
            ds, "Sezony meteorologiczne: zima = XII–II (zima danego roku kończy się w lutym tego roku), "
                "wiosna = III–V, lato = VI–VIII, jesień = IX–XI. Sezon uwzględniono, gdy ma dane z co najmniej "
                f"{fmt_percent(min_cov)} dni. Pudełka pokazują rozkład dobowych średnich z sieci stacji; "
                "linie – poziomy odniesienia dla średnich dobowych.",
        )

"""Wykresy Plotly w spójnym, ciemnym stylu - komponenty wielokrotnego użytku.

Wszystkie podpisy są po polsku, daty w formacie DD.MM.RRRR, liczby z przecinkiem dziesiętnym.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from modules.config import COLORS, LEVEL_COLORS, REFERENCE_LEVELS, SERIES_PALETTE
from modules.utils import PL_MONTHS_SHORT, SEASON_ORDER, hex_to_rgba, level_color, pollutant_label

PLOT_CONFIG = {
    "displaylogo": False,
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
    "toImageButtonOptions": {"format": "png", "scale": 2},
}
_GRID = "rgba(255,255,255,0.06)"
_FONT = "Source Sans Pro, Segoe UI, Arial, sans-serif"

_DATE_TICKS = [
    {"dtickrange": [None, 86400000], "value": "%d.%m.%Y\n%H:%M"},
    {"dtickrange": [86400000, "M1"], "value": "%d.%m.%Y"},
    {"dtickrange": ["M1", "M12"], "value": "%m.%Y"},
    {"dtickrange": ["M12", None], "value": "%Y"},
]


def apply_theme(
    fig: go.Figure, *, height: int = 420, y_title: str | None = None, x_title: str | None = None,
    legend: bool = True, date_axis: bool = False,
) -> go.Figure:
    """Nakłada wspólny motyw: przezroczyste tło, subtelna siatka, polskie separatory liczb."""
    fig.update_layout(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=_FONT, color=COLORS["text"], size=13),
        margin=dict(l=8, r=8, t=28, b=8),
        separators=", ",
        hovermode="x unified",
        hoverlabel=dict(bgcolor=COLORS["panel2"], bordercolor=COLORS["border"], font=dict(color=COLORS["text"])),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=12)),
        colorway=SERIES_PALETTE,
    )
    fig.update_xaxes(
        gridcolor=_GRID, zeroline=False, linecolor=COLORS["border"], title=x_title,
        title_font=dict(color=COLORS["muted"], size=12), tickfont=dict(color=COLORS["muted"]),
    )
    fig.update_yaxes(
        gridcolor=_GRID, zeroline=False, linecolor="rgba(0,0,0,0)", title=y_title,
        title_font=dict(color=COLORS["muted"], size=12), tickfont=dict(color=COLORS["muted"]),
    )
    if date_axis:
        fig.update_xaxes(tickformatstops=_DATE_TICKS, hoverformat="%d.%m.%Y %H:%M")
    return fig


def empty_figure(message: str, height: int = 320) -> go.Figure:
    """Pusty wykres z komunikatem - zamiast błędu lub wprowadzającego w błąd wykresu."""
    fig = go.Figure()
    fig.add_annotation(text=message, x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False,
                       font=dict(size=14, color=COLORS["muted"]))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return apply_theme(fig, height=height, legend=False)


def _reference_lines(fig: go.Figure, pollutant: str, kind: str) -> None:
    """Dodaje poziomy odniesienia ('annual' lub 'daily') jako poziome linie przerywane."""
    ref = REFERENCE_LEVELS.get(pollutant, {})
    for key, label, color in (
        (f"who_{kind}", "WHO 2021", COLORS["amber"]),
        (f"eu_{kind}", "UE", COLORS["red"]),
    ):
        val = ref.get(key)
        if val is not None:
            fig.add_hline(
                y=val, line=dict(color=hex_to_rgba(color, 0.75), width=1, dash="dash"),
                annotation_text=f"{label}: {val:g}", annotation_position="top left",
                annotation_font=dict(size=11, color=hex_to_rgba(color, 0.95)),
            )


def _trace_class(n_points: int):
    return go.Scattergl if n_points > 4000 else go.Scatter


def timeseries_chart(
    wide: pd.DataFrame, labels: dict[str, str], colors: dict[str, str], *, pollutant: str, unit: str,
    y_title: str, rolling: pd.DataFrame | None = None, mean_line: pd.Series | None = None,
    ref_kind: str | None = None, show_stations: bool = True, height: int = 460, freq_label: str = "",
) -> go.Figure:
    """Szereg czasowy: linie stacji, opcjonalnie średnia z sieci i średnia krocząca."""
    fig = go.Figure()
    n_points = int(wide.size) if not wide.empty else 0
    Trace = _trace_class(n_points)
    unit_txt = f" {unit}" if unit else ""
    if show_stations:
        for code in wide.columns:
            fig.add_trace(Trace(
                x=wide.index, y=wide[code], name=labels.get(code, code), mode="lines",
                line=dict(width=1.3, color=colors.get(code)), connectgaps=False,
                hovertemplate=f"%{{y:.1f}}{unit_txt}",
            ))
    if mean_line is not None:
        fig.add_trace(Trace(
            x=mean_line.index, y=mean_line.values, name="Średnia z wybranych stacji", mode="lines",
            line=dict(width=2.2, color=COLORS["text"]), connectgaps=False,
            hovertemplate=f"%{{y:.1f}}{unit_txt}",
        ))
    if rolling is not None:
        for code in rolling.columns:
            fig.add_trace(Trace(
                x=rolling.index, y=rolling[code], mode="lines",
                name=("Średnia krocząca: " + labels.get(code, code)) if code != "__mean__" else "Średnia krocząca (średnia z stacji)",
                line=dict(width=2.4, color=colors.get(code, COLORS["text"]), dash="dot"),
                hovertemplate=f"%{{y:.1f}}{unit_txt}",
            ))
    if ref_kind:
        _reference_lines(fig, pollutant, ref_kind)
    apply_theme(fig, height=height, y_title=y_title, date_axis=True)
    fig.update_xaxes(rangeslider=dict(visible=True, thickness=0.05, bgcolor=COLORS["panel"]))
    return fig


def band_chart(net: pd.DataFrame, *, pollutant: str, unit: str, y_title: str, height: int = 360,
               ref_kind: str | None = None) -> go.Figure:
    """Średnia z sieci stacji z pasmem min–max (rozrzut między stacjami)."""
    if net.empty:
        return empty_figure("Brak wiarygodnych danych dla wybranego zakresu.", height)
    unit_txt = f" {unit}" if unit else ""
    fig = go.Figure()
    multi = net["n_stations"].max() > 1
    if multi:
        fig.add_trace(go.Scatter(x=net["timestamp"], y=net["max"], mode="lines", line=dict(width=0),
                                 showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(
            x=net["timestamp"], y=net["min"], mode="lines", line=dict(width=0), fill="tonexty",
            fillcolor=hex_to_rgba(COLORS["teal"], 0.14), name="Zakres między stacjami (min–max)", hoverinfo="skip",
        ))
    fig.add_trace(go.Scatter(
        x=net["timestamp"], y=net["mean"], mode="lines", name="Średnia z wybranych stacji",
        line=dict(width=2, color=COLORS["teal"]), hovertemplate=f"%{{y:.1f}}{unit_txt}",
    ))
    if ref_kind:
        _reference_lines(fig, pollutant, ref_kind)
    return apply_theme(fig, height=height, y_title=y_title, date_axis=True)


def station_bar_chart(summary: pd.DataFrame, labels: dict[str, str], *, pollutant: str, unit: str,
                      height: int = 360) -> go.Figure:
    """Porównanie średnich stężeń między stacjami (kolor wg poziomów odniesienia)."""
    if summary.empty:
        return empty_figure("Brak danych do porównania stacji.", height)
    df = summary.sort_values("mean", ascending=True)
    unit_txt = f" {unit}" if unit else ""
    fig = go.Figure(go.Bar(
        x=df["mean"], y=[labels.get(c, c) for c in df["station_code"]], orientation="h",
        marker=dict(color=[level_color(v, pollutant) for v in df["mean"]]),
        customdata=np.column_stack([df["max"], df["p95"], df["n"], df["completeness"] * 100]),
        hovertemplate=(
            "<b>%{y}</b><br>Średnia: %{x:.1f}" + unit_txt + "<br>P95: %{customdata[1]:.1f}"
            "<br>Maksimum: %{customdata[0]:.1f}<br>Pomiary: %{customdata[2]:,.0f}"
            "<br>Kompletność: %{customdata[3]:.0f}%<extra></extra>"
        ),
    ))
    apply_theme(fig, height=height, x_title=f"Średnie stężenie {pollutant_label(pollutant)}" + (f" [{unit}]" if unit else ""),
                legend=False)
    fig.update_layout(hovermode="closest")
    fig.update_yaxes(automargin=True)
    return fig


def annual_chart(summary: pd.DataFrame, agg_year: pd.DataFrame, labels: dict[str, str],
                 colors: dict[str, str], *, pollutant: str, unit: str, y_title: str,
                 height: int = 420) -> go.Figure:
    """Średnie roczne. Lata o niewystarczającym pokryciu są kreskowane i półprzezroczyste."""
    if summary.empty:
        return empty_figure("Brak danych rocznych dla wybranego zakresu.", height)
    unit_txt = f" {unit}" if unit else ""
    vals = np.where(summary["reliable"], summary["mean"], summary["mean_all"])
    fig = go.Figure(go.Bar(
        x=summary["year"].astype(str), y=vals,
        marker=dict(
            color=[level_color(v, pollutant) if r else COLORS["faint"] for v, r in zip(vals, summary["reliable"])],
            opacity=[0.9 if r else 0.5 for r in summary["reliable"]],
            pattern=dict(shape=["" if r else "/" for r in summary["reliable"]]),
        ),
        name="Średnia roczna",
        customdata=np.column_stack([summary["stations_ok"], summary["stations"], summary["coverage"] * 100]),
        hovertemplate=(
            "<b>%{x}</b><br>Średnia: %{y:.1f}" + unit_txt + "<br>Stacje z wiarygodną średnią: "
            "%{customdata[0]:.0f} z %{customdata[1]:.0f}<br>Średnie pokrycie: %{customdata[2]:.0f}%<extra></extra>"
        ),
    ))
    if not agg_year.empty:
        ok = agg_year[agg_year["reliable"]].copy()
        ok["year"] = ok["timestamp"].dt.year.astype(str)
        if ok["station_code"].nunique() > 1:
            fig.add_trace(go.Scatter(
                x=ok["year"], y=ok["value"], mode="markers", name="Pojedyncze stacje",
                marker=dict(size=8, color=[colors.get(c, COLORS["blue"]) for c in ok["station_code"]],
                            line=dict(width=1, color=COLORS["bg"])),
                text=[labels.get(c, c) for c in ok["station_code"]],
                hovertemplate="%{text}: %{y:.1f}" + unit_txt + "<extra></extra>",
            ))
    _reference_lines(fig, pollutant, "annual")
    apply_theme(fig, height=height, y_title=y_title)
    fig.update_layout(hovermode="closest", bargap=0.35)
    fig.update_xaxes(type="category")
    return fig


def monthly_series_chart(net: pd.DataFrame, *, pollutant: str, unit: str, y_title: str,
                         height: int = 380) -> go.Figure:
    """Średnie miesięczne w czasie (słupki, kolor wg poziomów odniesienia)."""
    if net.empty:
        return empty_figure("Brak wiarygodnych średnich miesięcznych.", height)
    unit_txt = f" {unit}" if unit else ""
    fig = go.Figure(go.Bar(
        x=net["timestamp"], y=net["mean"], marker=dict(color=[level_color(v, pollutant) for v in net["mean"]]),
        customdata=net["n_stations"], name="Średnia miesięczna",
        hovertemplate="%{y:.1f}" + unit_txt + " (stacji: %{customdata})<extra></extra>",
    ))
    apply_theme(fig, height=height, y_title=y_title, date_axis=True, legend=False)
    fig.update_xaxes(tickformat="%m.%Y", hoverformat="%m.%Y")
    return fig


def month_profile_chart(profile: pd.DataFrame, *, unit: str, y_title: str, height: int = 380) -> go.Figure:
    """Typowy przebieg roczny: średnia dla miesięcy kalendarzowych (słupki błędów = odch. standardowe)."""
    if profile.empty:
        return empty_figure("Brak danych do wyznaczenia przebiegu rocznego.", height)
    unit_txt = f" {unit}" if unit else ""
    fig = go.Figure(go.Bar(
        x=[PL_MONTHS_SHORT[m - 1] for m in profile["month"]], y=profile["mean"],
        error_y=dict(type="data", array=profile["std"].fillna(0), color=COLORS["muted"], thickness=1),
        marker=dict(color=COLORS["blue"], opacity=0.85), customdata=profile["n_years"],
        hovertemplate="Średnia: %{y:.1f}" + unit_txt + "<br>liczba lat w średniej: %{customdata}<extra></extra>",
    ))
    apply_theme(fig, height=height, y_title=y_title, legend=False)
    fig.update_layout(hovermode="closest")
    return fig


def year_comparison_chart(ym: pd.DataFrame, years: list[int], *, unit: str, y_title: str,
                          height: int = 420) -> go.Figure:
    """Porównanie lat: linie średnich miesięcznych, jedna linia na rok."""
    if ym.empty:
        return empty_figure("Brak wiarygodnych średnich miesięcznych dla wybranych lat.", height)
    unit_txt = f" {unit}" if unit else ""
    fig = go.Figure()
    ordered = sorted(years)
    for i, year in enumerate(ordered):
        part = ym[ym["year"] == year].sort_values("month")
        if part.empty:
            continue
        shade = 0.35 + 0.65 * (i / max(len(ordered) - 1, 1))
        color = _blend(COLORS["blue"], COLORS["teal"], i / max(len(ordered) - 1, 1))
        fig.add_trace(go.Scatter(
            x=[PL_MONTHS_SHORT[m - 1] for m in part["month"]], y=part["value"], mode="lines+markers", name=str(year),
            line=dict(width=1.5 + 1.6 * shade, color=color), marker=dict(size=6),
            customdata=part["n_stations"], connectgaps=False,
            hovertemplate="%{y:.1f}" + unit_txt + " (stacji: %{customdata})",
        ))
    apply_theme(fig, height=height, y_title=y_title)
    fig.update_xaxes(categoryorder="array", categoryarray=PL_MONTHS_SHORT, type="category")
    return fig


def _blend(c1: str, c2: str, t: float) -> str:
    a = [int(c1[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(round(x + (y - x) * t)):02x}" for x, y in zip(a, b))


def season_box_chart(seasons: pd.DataFrame, *, pollutant: str, unit: str, y_title: str,
                     height: int = 420) -> go.Figure:
    """Rozkład dobowych średnich w sezonach meteorologicznych, osobno dla lat."""
    if seasons.empty:
        return empty_figure("Brak sezonów o wystarczającym pokryciu danymi.", height)
    fig = go.Figure()
    years = sorted(seasons["season_year"].unique())
    for i, year in enumerate(years):
        part = seasons[seasons["season_year"] == year]
        fig.add_trace(go.Box(
            x=part["season"].astype(str), y=part["value"], name=str(year),
            marker=dict(color=_blend(COLORS["blue"], COLORS["teal"], i / max(len(years) - 1, 1))),
            line=dict(width=1.2), boxmean=True, hoverinfo="y+name",
        ))
    apply_theme(fig, height=height, y_title=y_title)
    fig.update_layout(boxmode="group", hovermode="closest")
    fig.update_xaxes(categoryorder="array", categoryarray=SEASON_ORDER, type="category")
    _reference_lines(fig, pollutant, "daily")
    return fig

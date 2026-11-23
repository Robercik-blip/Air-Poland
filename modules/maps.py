"""Mapy stacji pomiarowych (Folium). Wyświetlane są wyłącznie stacje ze współrzędnymi z danych/metadanych."""
from __future__ import annotations

import html
from typing import Literal

import folium
import numpy as np
import pandas as pd
from branca.colormap import LinearColormap

from modules.config import COLORS, LEVEL_COLORS, LEVEL_LABELS
from modules.utils import classify_level, fmt_number, fmt_percent, level_color, pollutant_label

POLAND_CENTER = (52.0, 19.4)
# Jawny adres kafelków (folium 1.0 wymaga klucza API dla wbudowanego dostawcy CartoDB).
TILES_URL = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png?key=cb1_5ebm_1_c8f6c44e4889a923888b553b"
TILES_ATTR = "&copy; autorzy OpenStreetMap &copy; CARTO"


def stations_with_values(
    stations: pd.DataFrame, summary: pd.DataFrame, labels: dict[str, str]
) -> pd.DataFrame:
    """Łączy tabelę stacji ze statystykami wybranego wskaźnika (po kodzie stacji)."""
    merged = summary.merge(stations, on="station_code", how="left")
    merged["label"] = merged["station_code"].map(lambda c: labels.get(c, c))
    return merged


def build_station_map(
    data: pd.DataFrame, *, pollutant: str, unit: str, color_mode: Literal["levels", "gradient"] = "levels"
) -> tuple[folium.Map | None, int]:
    """Buduje mapę stacji z kolorami poziomów odniesienia albo ciągłym gradientem.

    Args:
        data: wynik ``stations_with_values`` (kolumny: station_code, label, latitude, longitude,
            mean, max, n, completeness, city, voivodeship).
        color_mode: ``levels`` - progi WHO/UE; ``gradient`` - względna skala wartości stacji.
    Returns:
        (mapa lub None gdy brak stacji ze współrzędnymi, liczba stacji bez współrzędnych)
    """
    if color_mode not in {"levels", "gradient"}:
        raise ValueError(f"Nieznany tryb kolorowania mapy: {color_mode}")
    has_coords = data["latitude"].notna() & data["longitude"].notna()
    missing = int((~has_coords).sum())
    pts = data.loc[has_coords].copy()
    color_scale = None
    if color_mode == "gradient":
        pts["mean"] = pd.to_numeric(pts["mean"], errors="coerce")
        pts = pts.loc[np.isfinite(pts["mean"])].copy()
        if not pts.empty:
            low, high = float(pts["mean"].min()), float(pts["mean"].max())
            if low == high:
                low, high = low - 0.5, high + 0.5
            caption = html.escape(f"Średnie stężenie [{unit}] · skala względna") if unit else "Średnie stężenie · skala względna"
            color_scale = LinearColormap(
                colors=["#22c55e", "#facc15", "#ef4444"],
                vmin=low,
                vmax=high,
                caption=caption,
            )
    if pts.empty:
        return None, missing

    fmap = folium.Map(location=POLAND_CENTER, zoom_start=6, tiles=None,
                      control_scale=True, zoom_control=True, prefer_canvas=True)
    folium.TileLayer(tiles=TILES_URL, attr=TILES_ATTR, name="Mapa podkładowa", subdomains="abcd",
                     max_zoom=19).add_to(fmap)
    unit_txt = f" {unit}" if unit else ""
    for _, row in pts.iterrows():
        color = color_scale(float(row["mean"])) if color_scale else level_color(row["mean"], pollutant)
        popup_html = (
            f"<div style='font-family:Segoe UI,Arial,sans-serif;font-size:12.5px;min-width:190px'>"
            f"<b>{html.escape(str(row['label']))}</b><br>"
            f"<span style='color:#666'>{html.escape(str(row.get('city') or ''))}"
            f"{', ' if row.get('city') and row.get('voivodeship') else ''}{html.escape(str(row.get('voivodeship') or ''))}</span>"
            f"<hr style='margin:5px 0'>"
            f"{pollutant_label(pollutant)} – średnia: <b>{fmt_number(row['mean'])}{unit_txt}</b><br>"
            f"Maksimum: {fmt_number(row['max'])}{unit_txt}<br>"
            f"Liczba pomiarów: {int(row['n']):,}".replace(",", "&nbsp;") + "<br>"
            f"Kompletność danych: {fmt_percent(row['completeness'])}<br>"
            f"<span style='color:#888'>Współrz.: {row['latitude']:.4f}, {row['longitude']:.4f}</span></div>"
        )
        folium.CircleMarker(
            location=(float(row["latitude"]), float(row["longitude"])), radius=9,
            color="#0d1117", weight=1.5, fill=True, fill_color=color, fill_opacity=0.92,
            tooltip=f"{row['label']}: {fmt_number(row['mean'])}{unit_txt}",
            popup=folium.Popup(popup_html, max_width=280),
        ).add_to(fmap)

    south, north = pts["latitude"].min(), pts["latitude"].max()
    west, east = pts["longitude"].min(), pts["longitude"].max()
    if len(pts) > 1:
        fmap.fit_bounds([[south, west], [north, east]], padding=(28, 28))
    else:
        fmap.location = (south, west)
        fmap.options["zoom"] = 9

    if color_scale:
        color_scale.add_to(fmap)
    else:
        present = {classify_level(v, pollutant) for v in pts["mean"]}
        items = "".join(
            f"<div><span style='display:inline-block;width:10px;height:10px;border-radius:50%;"
            f"background:{LEVEL_COLORS[k]};margin-right:6px'></span>{LEVEL_LABELS[k]}</div>"
            for k in ("ok", "elevated", "high", "unknown") if k in present
        )
        legend = (
            "<div style='position:fixed;bottom:22px;left:12px;z-index:9999;background:rgba(19,25,32,0.92);"
            f"border:1px solid {COLORS['border']};border-radius:8px;padding:8px 10px;color:#e6edf3;"
            "font-family:Segoe UI,Arial,sans-serif;font-size:11.5px;line-height:1.55'>"
            "<b>Średnie stężenie</b>" + items + "</div>"
        )
        fmap.get_root().html.add_child(folium.Element(legend))
    return fmap, missing

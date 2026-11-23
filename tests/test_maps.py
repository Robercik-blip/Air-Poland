from __future__ import annotations

import pandas as pd

from modules.maps import build_station_map


def _stations() -> pd.DataFrame:
    return pd.DataFrame({
        "station_code": ["A", "B"],
        "label": ["Stacja A", "Stacja B"],
        "latitude": [52.0, 53.0],
        "longitude": [19.0, 20.0],
        "mean": [10.0, 50.0],
        "max": [20.0, 60.0],
        "n": [100, 100],
        "completeness": [0.9, 0.95],
        "city": ["Miasto A", "Miasto B"],
        "voivodeship": ["region A", "region B"],
    })


def test_gradient_map_colors_station_means_from_green_to_red():
    station_map, missing = build_station_map(
        _stations(), pollutant="PM10", unit="µg/m³", color_mode="gradient"
    )

    assert station_map is not None
    assert missing == 0
    rendered = station_map.get_root().render()
    assert '"fillColor": "#22c55eff"' in rendered
    assert '"fillColor": "#ef4444ff"' in rendered
    assert "skala wzgl" in rendered


def test_reference_map_keeps_existing_who_eu_legend():
    station_map, _ = build_station_map(_stations(), pollutant="PM10", unit="µg/m³")

    assert station_map is not None
    rendered = station_map.get_root().render()
    assert "poniżej poziomu WHO" in rendered
    assert "powyżej poziomu UE" in rendered


def test_gradient_map_handles_identical_values():
    stations = _stations()
    stations["mean"] = 12.0

    station_map, missing = build_station_map(
        stations, pollutant="PM10", unit="µg/m³", color_mode="gradient"
    )

    assert station_map is not None
    assert missing == 0

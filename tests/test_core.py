"""Testy jednostkowe warstwy danych i analizy (uruchamianie: python -m pytest tests -q)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules import analysis
from modules.data_loader import build_dataset, build_demo_dataset, detect_mapping, read_table
from modules.data_processing import (
    ColumnMapping, DataFormatError, normalise_averaging_label, normalise_pollutant_name,
    parse_datetime, parse_numeric,
)


def test_parse_numeric_decimal_separators_and_missing():
    s = pd.Series(["1,5", "2.5", "1 234,5", "1,234.5", "", "NA", "brak", "abc", "-0,3"])
    out, n_bad = parse_numeric(s)
    assert out.iloc[0] == 1.5
    assert out.iloc[1] == 2.5
    assert out.iloc[2] == 1234.5
    assert out.iloc[3] == 1234.5
    assert out.iloc[4:7].isna().all()  # brak danych to NaN, nigdy 0
    assert n_bad == 1  # tylko "abc" jest błędem, puste i NA nie
    assert out.iloc[8] == -0.3


def test_parse_datetime_formats_and_24h():
    iso, _ = parse_datetime(pd.Series(["2023-01-31 10:00:00", "2023-02-01 11:00:00"]))
    assert iso.iloc[0] == pd.Timestamp("2023-01-31 10:00")
    pl, _ = parse_datetime(pd.Series(["31.01.2023 10:00", "01.02.2023 11:00"]))
    assert pl.iloc[0] == pd.Timestamp("2023-01-31 10:00")  # dzień pierwszy, nie miesiąc
    h24, notes = parse_datetime(pd.Series(["2023-01-01 23:00", "2023-01-01 24:00"]))
    assert h24.iloc[1] == pd.Timestamp("2023-01-02 00:00")
    assert any("24:00" in n for n in notes)


def test_normalisers():
    assert normalise_pollutant_name("PM2,5") == "PM2.5"
    assert normalise_pollutant_name(" pm10 ") == "PM10"
    assert normalise_averaging_label("1g") == "1h"
    assert normalise_averaging_label("doba") == "24h"


def _wide_frame() -> pd.DataFrame:
    times = pd.date_range("2023-01-01", periods=48, freq="h")
    return pd.DataFrame({
        "Data": times.strftime("%d.%m.%Y %H:%M"),
        "Stacja": ["ST1"] * 48,
        "PM10": [f"{v:.1f}".replace(".", ",") for v in np.linspace(10, 30, 48)],
        "NO2": [""] * 24 + ["12,0"] * 24,
    })


def test_build_dataset_pollutant_columns_keeps_missing_as_missing():
    raw = _wide_frame()
    mapping, _ = detect_mapping(list(raw.columns))
    assert mapping.layout == "pollutant_columns"
    ds = build_dataset([raw], mapping, name="t", source_kind="upload")
    m = ds.measurements
    assert (m["value"] > 0).all()  # żadnych "zer" w miejsce braków
    assert len(m[m["pollutant"] == "NO2"]) == 24
    assert len(m[m["pollutant"] == "PM10"]) == 48
    assert ds.report.n_missing_value == 24
    assert set(ds.averaging_options("PM10")) == {"1h"}  # oszacowane z odstępów


def test_duplicates_and_negatives():
    raw = pd.DataFrame({
        "Data": ["2023-01-01 01:00"] * 3 + ["2023-01-01 02:00"],
        "Stacja": ["A"] * 4,
        "Wskaźnik": ["PM10"] * 4,
        "Wartość": ["10", "10", "20", "-5"],
    })
    mapping, _ = detect_mapping(list(raw.columns))
    ds = build_dataset([raw], mapping, name="t", source_kind="upload")
    assert ds.report.n_dup_exact == 1
    assert ds.report.n_dup_conflict == 2
    assert ds.report.n_negative == 1
    assert len(ds.measurements) == 1  # ujemna -> brak, konflikt -> średnia 15
    assert ds.measurements["value"].iloc[0] == 15


def test_mapping_validation_error_is_readable():
    bad = ColumnMapping(layout="long", time_col="x")
    with pytest.raises(DataFormatError, match="Mapowanie"):
        bad.validate(["a", "b"])


def test_read_table_encoding_and_separator():
    content = "Data;Stacja;PM10\n01.01.2023 01:00;Łódź;1,5\n".encode("cp1250")
    df = read_table(content, "x.csv")
    assert list(df.columns) == ["Data", "Stacja", "PM10"]
    assert df.loc[0, "Stacja"] == "Łódź"


def test_aggregation_coverage_masks_incomplete_periods():
    times = pd.date_range("2023-01-01", "2023-01-31 23:00", freq="h")
    data = pd.DataFrame({"timestamp": times, "station_code": "A", "value": 10.0})
    # doba 5 stycznia: tylko 12 z 24 godzin
    drop = (data["timestamp"].dt.day == 5) & (data["timestamp"].dt.hour < 12)
    data = data[~drop]
    daily = analysis.aggregate_series(data, "D", pd.Timedelta(hours=1), 0.75)
    day5 = daily[daily["timestamp"] == "2023-01-05"].iloc[0]
    assert day5["coverage"] == pytest.approx(0.5)
    assert not day5["reliable"] and np.isnan(day5["value"]) and day5["value_all"] == 10.0
    monthly = analysis.aggregate_series(data, "MS", pd.Timedelta(hours=1), 0.75)
    assert monthly["reliable"].all()
    assert monthly["n_expected"].iloc[0] == 31 * 24


def test_partial_year_is_flagged_unreliable():
    ds = build_demo_dataset()
    stations = ds.stations_for("PM10")
    data = analysis.filter_measurements(
        ds.measurements, "PM10", stations, pd.Timestamp("2021-01-01").date(),
        pd.Timestamp("2025-08-31").date(), "1h",
    )
    yearly = analysis.aggregate_series(data, "YS", pd.Timedelta(hours=1), 0.75)
    summary = analysis.annual_summary(yearly).set_index("year")
    assert summary.loc[2022, "reliable"]
    assert not summary.loc[2025, "reliable"]  # styczeń-sierpień = ~67% roku


def test_allowed_freqs_and_auto():
    assert analysis.allowed_freqs(pd.Timedelta(hours=1)) == ["h", "D", "MS", "YS"]
    assert analysis.allowed_freqs(pd.Timedelta(hours=24)) == ["D", "MS", "YS"]
    assert analysis.allowed_freqs(None) == ["MS", "YS"]


def test_season_table_assigns_december_to_next_winter():
    days = pd.date_range("2022-12-01", "2023-02-28", freq="D")
    agg = pd.DataFrame({
        "station_code": "A", "timestamp": days, "value": 1.0, "value_all": 1.0,
        "n_valid": 24, "n_expected": 24.0, "coverage": 1.0, "reliable": True,
    })
    t = analysis.season_table(agg, 0.75)
    assert set(t["season_year"]) == {2023}
    assert set(t["season"].astype(str)) == {"Zima"}


def test_station_metadata_wgs84_column_names():
    """Nazwy typu 'WGS84 φ N' / 'WGS84 λ E' (greckie litery są pomijane przy normalizacji) mapują się na współrzędne."""
    from modules.data_loader import parse_station_metadata
    meta = parse_station_metadata(pd.DataFrame({
        "Kod stacji": ["A1"], "Nazwa stacji": ["Test"], "WGS84 φ N": ["50,0575"], "WGS84 λ E": ["19,9262"],
    }))
    assert meta.loc[0, "latitude"] == "50,0575" and meta.loc[0, "longitude"] == "19,9262"

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy.cluster.hierarchy import dendrogram

from modules.advanced_analysis import (
    cluster_analysis,
    correlation_matrices,
    pca_analysis,
    select_analysis_measurements,
    time_matrix,
    unit_conflicts,
)


def _hourly_measurements() -> pd.DataFrame:
    timestamps = pd.date_range("2024-01-01", periods=48, freq="h")
    rows = []
    for i, timestamp in enumerate(timestamps):
        rows.append({
            "timestamp": timestamp,
            "station_code": "S1",
            "pollutant": "PM10",
            "value": float(i),
            "unit": "µg/m³",
            "averaging": "1h",
        })
        if i < 24 or i >= 36:
            rows.append({
                "timestamp": timestamp,
                "station_code": "S1",
                "pollutant": "NO2",
                "value": float(i * 2),
                "unit": "µg/m³",
                "averaging": "1h",
            })
    return pd.DataFrame(rows)


def test_time_matrix_uses_exact_timestamps_and_pairwise_counts():
    selected = select_analysis_measurements(
        _hourly_measurements(), ["PM10", "NO2"], ["S1"],
        pd.Timestamp("2024-01-01").date(), pd.Timestamp("2024-01-02").date(), "1h",
    )
    matrix = time_matrix(selected, ["PM10", "NO2"])
    pearson, _, counts = correlation_matrices(matrix, min_observations=3)

    assert len(matrix) == 48
    assert matrix.loc[pd.Timestamp("2024-01-02 12:00"), "NO2"] == 72
    assert counts.loc["PM10", "NO2"] == 36
    assert pearson.loc["PM10", "NO2"] == 1


def test_daily_time_matrix_masks_incomplete_aggregates():
    selected = select_analysis_measurements(
        _hourly_measurements(), ["PM10", "NO2"], ["S1"],
        pd.Timestamp("2024-01-01").date(), pd.Timestamp("2024-01-02").date(), "1h",
    )
    matrix = time_matrix(selected, ["PM10", "NO2"], frequency="D", min_coverage=0.75)

    assert matrix.loc[pd.Timestamp("2024-01-01")].notna().all()
    assert pd.notna(matrix.loc[pd.Timestamp("2024-01-02"), "PM10"])
    assert np.isnan(matrix.loc[pd.Timestamp("2024-01-02"), "NO2"])


def test_unit_conflicts_are_reported_for_the_selected_indicator():
    measurements = _hourly_measurements()
    measurements.loc[measurements["pollutant"] == "NO2", "unit"] = "mg/m³"
    selected = select_analysis_measurements(
        measurements, ["PM10", "NO2"], ["S1"],
        pd.Timestamp("2024-01-01").date(), pd.Timestamp("2024-01-02").date(), "1h",
    )

    assert unit_conflicts(selected, ["PM10", "NO2"]) == {}
    first_no2 = selected.index[selected["pollutant"] == "NO2"][0]
    selected.loc[first_no2, "unit"] = "µg/m³"
    assert unit_conflicts(selected, ["PM10", "NO2"]) == {"NO2": ("mg/m³", "µg/m³")}


def test_pca_drops_incomplete_rows_and_constant_features():
    matrix = pd.DataFrame({
        "PM10": [1.0, 2.0, 3.0, np.nan],
        "NO2": [5.0, 3.0, 1.0, 8.0],
        "SO2": [10.0, 10.0, 10.0, 10.0],
    })
    result = pca_analysis(matrix)

    assert result.dropped_rows == 1
    assert result.constant_features == ("SO2",)
    assert result.scores.shape == (3, 2)
    assert result.explained_variance.sum() == pytest.approx(1.0)
    assert set(result.loadings.index) == {"PM10", "NO2"}


def test_clustering_returns_labels_and_silhouette_for_both_methods():
    matrix = pd.DataFrame({
        "PM10": [0.0, 0.1, 0.2, 0.3, 10.0, 10.1, 10.2, 10.3],
        "NO2": [1.0, 1.1, 0.9, 1.2, 12.0, 12.1, 11.9, 12.2],
    }, index=[f"S{i}" for i in range(8)])

    for algorithm in ("kmeans", "hierarchical"):
        result = cluster_analysis(matrix, algorithm=algorithm, n_clusters=2)
        assert len(result.labels) == 8
        assert result.labels.nunique() == 2
        assert result.silhouette is not None
        assert -1 <= result.silhouette <= 1
        assert (result.linkage_matrix is not None) == (algorithm == "hierarchical")


def test_hierarchical_dendrogram_coordinates_do_not_require_matplotlib():
    matrix = pd.DataFrame({
        "PM10": [0.0, 0.1, 0.2, 10.0, 10.1, 10.2],
        "NO2": [1.0, 1.1, 0.9, 12.0, 12.1, 11.9],
    })
    result = cluster_analysis(matrix, algorithm="hierarchical", n_clusters=4)

    tree = dendrogram(result.linkage_matrix, no_labels=True, no_plot=True)

    assert tree["icoord"]
    assert tree["dcoord"]

"""Testable matrix preparation and multivariate calculations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from modules.analysis import aggregate_series, averaging_step

_ANALYSIS_COLUMNS = ["timestamp", "station_code", "pollutant", "value", "unit", "averaging"]
_MAX_HIERARCHICAL_SAMPLES = 500
_MAX_SILHOUETTE_SAMPLES = 2000


@dataclass
class PCAResult:
    samples: pd.DataFrame
    scores: pd.DataFrame
    explained_variance: pd.Series
    loadings: pd.DataFrame
    dropped_rows: int
    constant_features: tuple[str, ...]


@dataclass
class ClusteringResult:
    samples: pd.DataFrame
    labels: pd.Series
    silhouette: float | None
    linkage_matrix: np.ndarray | None
    dropped_rows: int
    constant_features: tuple[str, ...]
    sampled: bool


def select_analysis_measurements(
    measurements: pd.DataFrame,
    pollutants: list[str],
    stations: list[str],
    start: date,
    end: date,
    averaging: str,
) -> pd.DataFrame:
    """Select one compatible time window and averaging label from the Dataset."""
    timestamps = measurements["timestamp"]
    mask = measurements["pollutant"].isin(pollutants) & measurements["station_code"].isin(stations)
    mask &= measurements["averaging"] == averaging
    mask &= (timestamps >= pd.Timestamp(start)) & (timestamps < pd.Timestamp(end) + pd.Timedelta(days=1))
    selected = measurements.loc[mask, _ANALYSIS_COLUMNS].copy()
    for column in ("station_code", "pollutant", "unit", "averaging"):
        selected[column] = selected[column].astype(str)
    return selected.reset_index(drop=True)


def unit_conflicts(selected: pd.DataFrame, pollutants: list[str]) -> dict[str, tuple[str, ...]]:
    """Return indicators that have more than one unit in the selected station set."""
    conflicts = {}
    for pollutant in pollutants:
        units = tuple(sorted(selected.loc[selected["pollutant"] == pollutant, "unit"].fillna("").unique()))
        if len(units) > 1:
            conflicts[pollutant] = units
    return conflicts


def station_profile_matrix(selected: pd.DataFrame, pollutants: list[str], stations: list[str]) -> pd.DataFrame:
    """Create one row per station from the selected period's mean for each indicator."""
    if selected.empty:
        return pd.DataFrame(index=pd.Index(stations, name="station_code"), columns=pollutants, dtype=float)
    matrix = selected.groupby(["station_code", "pollutant"], observed=True)["value"].mean().unstack("pollutant")
    matrix = matrix.reindex(index=stations, columns=pollutants)
    matrix.index = pd.Index(matrix.index.astype(str), name="station_code")
    return matrix


def time_matrix(
    selected: pd.DataFrame,
    pollutants: list[str],
    *,
    frequency: Literal["source", "D", "MS", "YS"] = "source",
    min_coverage: float = 0.75,
) -> pd.DataFrame:
    """Align one station's pollutant series by timestamp; incomplete aggregates stay missing."""
    if selected["station_code"].nunique() > 1:
        raise ValueError("Szereg czasowy wymaga wyboru jednej stacji.")
    if frequency not in {"source", "D", "MS", "YS"}:
        raise ValueError(f"Nieobsługiwana agregacja: {frequency}")

    averaging_labels = selected["averaging"].dropna().unique()
    step = averaging_step(str(averaging_labels[0])) if len(averaging_labels) == 1 else None
    if frequency != "source" and step is None:
        raise ValueError("Nie można agregować serii bez rozpoznanego czasu uśredniania.")

    columns = []
    for pollutant in pollutants:
        series = selected.loc[selected["pollutant"] == pollutant, ["timestamp", "station_code", "value"]]
        if frequency == "source":
            values = series.groupby("timestamp")["value"].mean()
        else:
            aggregated = aggregate_series(series, frequency, step, min_coverage)
            values = aggregated.loc[aggregated["reliable"]].set_index("timestamp")["value"]
        columns.append(values.rename(pollutant))

    if not columns:
        return pd.DataFrame()
    return pd.concat(columns, axis=1).reindex(columns=pollutants).sort_index()


def correlation_matrices(
    matrix: pd.DataFrame, *, min_observations: int = 3
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return Pearson, Spearman, and pairwise-complete sample-count matrices."""
    numeric = matrix.apply(pd.to_numeric, errors="coerce")
    counts = numeric.notna().astype("int64").T.dot(numeric.notna().astype("int64"))
    pearson = numeric.corr(method="pearson", min_periods=min_observations)
    spearman = numeric.corr(method="spearman", min_periods=min_observations)
    return pearson, spearman, counts


def _prepare_features(matrix: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, int, tuple[str, ...]]:
    numeric = matrix.apply(pd.to_numeric, errors="coerce")
    complete = numeric.dropna(axis=0, how="any")
    dropped_rows = len(numeric) - len(complete)
    variable = complete.nunique(dropna=True)
    constant_features = tuple(variable.index[variable < 2].astype(str))
    complete = complete.drop(columns=list(constant_features))
    if len(complete) < 2 or complete.shape[1] < 2:
        raise ValueError("Potrzeba co najmniej dwóch kompletnych obserwacji i dwóch zmiennych o zmienności.")
    scaled = pd.DataFrame(
        StandardScaler().fit_transform(complete),
        index=complete.index,
        columns=complete.columns,
    )
    return complete, scaled, dropped_rows, constant_features


def pca_analysis(matrix: pd.DataFrame) -> PCAResult:
    """Run PCA on complete cases after standardizing each pollutant."""
    samples, scaled, dropped_rows, constant_features = _prepare_features(matrix)
    components = min(scaled.shape)
    pca = PCA(n_components=components)
    scores = pd.DataFrame(
        pca.fit_transform(scaled),
        index=scaled.index,
        columns=[f"PC{i + 1}" for i in range(components)],
    )
    explained = pd.Series(
        pca.explained_variance_ratio_,
        index=scores.columns,
        name="explained_variance",
    )
    loadings = pd.DataFrame(
        pca.components_.T * np.sqrt(pca.explained_variance_),
        index=scaled.columns,
        columns=scores.columns,
    )
    return PCAResult(samples, scores, explained, loadings, dropped_rows, constant_features)


def _sample_silhouette(values: pd.DataFrame, labels: pd.Series) -> float | None:
    distinct = labels.nunique()
    if distinct < 2 or distinct >= len(labels) or distinct >= _MAX_SILHOUETTE_SAMPLES:
        return None
    if len(values) <= _MAX_SILHOUETTE_SAMPLES:
        sample_values, sample_labels = values, labels
    else:
        rng = np.random.default_rng(42)
        label_values = labels.to_numpy()
        first_by_cluster = [int(np.flatnonzero(label_values == cluster)[0]) for cluster in labels.unique()]
        remaining = np.setdiff1d(np.arange(len(labels)), first_by_cluster)
        needed = _MAX_SILHOUETTE_SAMPLES - len(first_by_cluster)
        selected = first_by_cluster + rng.choice(remaining, size=needed, replace=False).tolist()
        sample_values, sample_labels = values.iloc[selected], labels.iloc[selected]
    return float(silhouette_score(sample_values, sample_labels))


def cluster_analysis(
    matrix: pd.DataFrame,
    *,
    algorithm: Literal["kmeans", "hierarchical"],
    n_clusters: int,
) -> ClusteringResult:
    """Cluster standardized complete cases; cap hierarchical linkage to a reproducible sample."""
    samples, scaled, dropped_rows, constant_features = _prepare_features(matrix)
    sampled = algorithm == "hierarchical" and len(scaled) > _MAX_HIERARCHICAL_SAMPLES
    linkage_matrix = None

    if sampled:
        rng = np.random.default_rng(42)
        indices = np.sort(rng.choice(len(scaled), _MAX_HIERARCHICAL_SAMPLES, replace=False))
        samples = samples.iloc[indices]
        scaled = scaled.iloc[indices]
    if n_clusters < 2 or n_clusters >= len(scaled):
        raise ValueError("Liczba skupień musi być mniejsza od liczby analizowanych obserwacji.")

    if algorithm == "kmeans":
        labels = pd.Series(
            KMeans(n_clusters=n_clusters, random_state=42, n_init=10).fit_predict(scaled),
            index=scaled.index,
            name="cluster",
        )
    elif algorithm == "hierarchical":
        linkage_matrix = linkage(scaled.to_numpy(), method="ward")
        labels = pd.Series(
            fcluster(linkage_matrix, t=n_clusters, criterion="maxclust") - 1,
            index=scaled.index,
            name="cluster",
        )
    else:
        raise ValueError(f"Nieobsługiwany algorytm klasteryzacji: {algorithm}")

    silhouette = _sample_silhouette(scaled, labels)
    return ClusteringResult(
        samples, labels, silhouette, linkage_matrix, dropped_rows, constant_features, sampled
    )

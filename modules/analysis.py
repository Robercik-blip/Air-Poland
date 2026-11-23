"""Obliczenia analityczne (bez zależności od Streamlit - łatwe do testowania i opisania w pracy).

Kluczowa zasada: średnia za okres (doba, miesiąc, rok) jest uznawana za WIARYGODNĄ tylko
wtedy, gdy udział ważnych pomiarów w tym okresie (pokrycie) wynosi co najmniej
``min_coverage`` (domyślnie 75% - wartość stosowana w europejskiej ocenie jakości powietrza
jako minimalna kompletność danych). Okresy poniżej progu są oznaczane i pomijane w średnich,
zamiast po cichu wpływać na wynik.
"""
from __future__ import annotations

import calendar
import re
from datetime import date

import numpy as np
import pandas as pd

from modules.utils import SEASON_MONTHS, SEASON_ORDER

FREQ_LABELS = {"h": "Godzinowa", "D": "Dzienna", "MS": "Miesięczna", "YS": "Roczna"}
FREQ_ORDER = ["h", "D", "MS", "YS"]
_FREQ_MIN_SPAN = {
    "h": pd.Timedelta(hours=1),
    "D": pd.Timedelta(days=1),
    "MS": pd.Timedelta(days=28),
    "YS": pd.Timedelta(days=365),
}


# --- Pomocnicze ---------------------------------------------------------------
def averaging_step(averaging: str) -> pd.Timedelta | None:
    """Zamienia etykietę uśredniania ('1h', '24h') na przedział czasu; None, gdy nieznany."""
    m = re.fullmatch(r"(\d+)h", averaging or "")
    if m:
        return pd.Timedelta(hours=int(m.group(1)))
    return None


def native_freq(step: pd.Timedelta | None) -> str | None:
    """Częstość odpowiadająca natywnej rozdzielczości danych (1h -> 'h', 24h -> 'D')."""
    if step == pd.Timedelta(hours=1):
        return "h"
    if step == pd.Timedelta(hours=24):
        return "D"
    return None


def allowed_freqs(step: pd.Timedelta | None) -> list[str]:
    """Agregacje, które mają sens dla danej rozdzielczości danych źródłowych.

    Nie da się uzyskać średnich godzinowych z danych dobowych; przy nieznanej
    rozdzielczości dopuszczamy tylko agregacje miesięczne i roczne.
    """
    if step is None:
        return ["MS", "YS"]
    return [f for f in FREQ_ORDER if _FREQ_MIN_SPAN[f] >= step]


def auto_freq(start: date, end: date, step: pd.Timedelta | None) -> str:
    """Rozsądna domyślna agregacja zależna od długości wybranego zakresu."""
    days = (pd.Timestamp(end) - pd.Timestamp(start)).days + 1
    options = allowed_freqs(step)
    if days <= 45 and "h" in options:
        return "h"
    if days <= 1100 and "D" in options:
        return "D"
    return "MS" if "MS" in options else options[-1]


def season_of_month(month: int) -> str:
    for season, months in SEASON_MONTHS.items():
        if month in months:
            return season
    raise ValueError(month)


# --- Filtrowanie --------------------------------------------------------------
def filter_measurements(
    meas: pd.DataFrame, pollutant: str, stations: list[str], start: date, end: date, averaging: str
) -> pd.DataFrame:
    """Wybiera pomiary jednego wskaźnika, dla wybranych stacji, zakresu dat i czasu uśredniania."""
    ts = meas["timestamp"]
    mask = (meas["pollutant"] == pollutant) & meas["station_code"].isin(stations)
    mask &= meas["averaging"] == averaging
    mask &= (ts >= pd.Timestamp(start)) & (ts < pd.Timestamp(end) + pd.Timedelta(days=1))
    out = meas.loc[mask, ["timestamp", "station_code", "value"]].copy()
    out["station_code"] = out["station_code"].astype(str)
    return out.reset_index(drop=True)


# --- Agregacja z kontrolą pokrycia --------------------------------------------
AGG_COLUMNS = ["station_code", "timestamp", "value", "value_all", "n_valid", "n_expected", "coverage", "reliable"]


def aggregate_series(
    data: pd.DataFrame, freq: str, step: pd.Timedelta | None, min_coverage: float = 0.75
) -> pd.DataFrame:
    """Agreguje pomiary każdej stacji osobno do okresów o częstości ``freq``.

    Zwraca kolumny:
        timestamp   - początek okresu,
        value_all   - średnia ze wszystkich dostępnych pomiarów okresu,
        value       - to samo, ale NaN, gdy pokrycie < min_coverage,
        n_valid     - liczba ważnych pomiarów w okresie,
        n_expected  - liczba oczekiwanych pomiarów (pełny okres kalendarzowy / krok danych),
        coverage    - n_valid / n_expected (nie więcej niż 1),
        reliable    - czy pokrycie >= min_coverage.

    Okresy na brzegach wybranego zakresu dat liczone są względem PEŁNEGO okresu
    kalendarzowego, dlatego niepełny miesiąc/rok jest uczciwie oznaczony jako niewiarygodny.
    """
    if data.empty:
        return pd.DataFrame(columns=AGG_COLUMNS)

    if step is None:
        step = pd.Timedelta(hours=1)
    nat = native_freq(step)
    if freq == nat:
        out = data[["station_code", "timestamp", "value"]].copy()
        out["value_all"] = out["value"]
        out["n_valid"] = 1
        out["n_expected"] = 1.0
    else:
        grouped = (
            data.groupby(["station_code", pd.Grouper(key="timestamp", freq=freq)])["value"]
            .agg(["mean", "count"])
            .reset_index()
            .rename(columns={"mean": "value_all", "count": "n_valid"})
        )
        grouped = grouped[grouped["n_valid"] > 0].reset_index(drop=True)
        starts = pd.DatetimeIndex(grouped["timestamp"])
        ends = starts + pd.tseries.frequencies.to_offset(freq)
        grouped["n_expected"] = np.asarray((ends - starts) / step, dtype=float)
        out = grouped

    out["coverage"] = np.minimum(out["n_valid"] / out["n_expected"], 1.0)
    out["reliable"] = out["coverage"] >= min_coverage
    out["value"] = out["value_all"].where(out["reliable"])
    return out[AGG_COLUMNS].sort_values(["station_code", "timestamp"]).reset_index(drop=True)


def to_wide(agg: pd.DataFrame, freq: str, column: str = "value") -> pd.DataFrame:
    """Tabela szeroka: indeks = okres, kolumny = stacje; brakujące okresy jako NaN (przerwy na wykresie)."""
    if agg.empty:
        return pd.DataFrame()
    wide = agg.pivot(index="timestamp", columns="station_code", values=column).sort_index()
    return wide.asfreq(freq)


def rolling_mean(wide: pd.DataFrame, window: int) -> pd.DataFrame:
    """Średnia krocząca po regularnym indeksie; wymaga min. 60% ważnych okresów w oknie."""
    min_periods = max(1, int(np.ceil(window * 0.6)))
    return wide.rolling(window=window, min_periods=min_periods).mean()


def network_mean(agg: pd.DataFrame) -> pd.DataFrame:
    """Średnia z wiarygodnych wartości stacji w każdym okresie (+ min, max, liczba stacji).

    Najpierw liczymy średnie dla stacji, potem średnią ze stacji - dzięki temu stacja z większą
    liczbą pomiarów nie dominuje wyniku.
    """
    ok = agg[agg["reliable"]]
    if ok.empty:
        return pd.DataFrame(columns=["timestamp", "mean", "min", "max", "n_stations"])
    return (
        ok.groupby("timestamp")["value"]
        .agg(mean="mean", min="min", max="max", n_stations="count")
        .reset_index()
    )


# --- Statystyki opisowe i KPI -------------------------------------------------
def expected_count(start: date, end: date, step: pd.Timedelta | None, n_stations: int) -> float:
    """Liczba pomiarów oczekiwanych dla n stacji w wybranym zakresie dat."""
    if step is None or n_stations == 0:
        return float("nan")
    span = (pd.Timestamp(end) + pd.Timedelta(days=1)) - pd.Timestamp(start)
    return n_stations * span / step


def compute_kpis(data: pd.DataFrame, start: date, end: date, step: pd.Timedelta | None) -> dict:
    """Wskaźniki KPI dla wybranego wycinka danych (wszystkie wartości policzone, nie zmyślone)."""
    if data.empty:
        return {"n": 0, "n_stations": 0, "mean": np.nan, "max": np.nan, "max_station": None,
                "max_time": None, "first": None, "last": None, "completeness": np.nan}
    idx_max = data["value"].idxmax()
    n_stations = data["station_code"].nunique()
    expected = expected_count(start, end, step, n_stations)
    return {
        "n": int(len(data)),
        "n_stations": int(n_stations),
        "mean": float(data["value"].mean()),
        "max": float(data.loc[idx_max, "value"]),
        "max_station": data.loc[idx_max, "station_code"],
        "max_time": data.loc[idx_max, "timestamp"],
        "first": data["timestamp"].min(),
        "last": data["timestamp"].max(),
        "completeness": float(min(len(data) / expected, 1.0)) if expected and not np.isnan(expected) else np.nan,
    }


def station_summary(data: pd.DataFrame, start: date, end: date, step: pd.Timedelta | None) -> pd.DataFrame:
    """Statystyki dla każdej stacji: liczba pomiarów, średnia, mediana, P95, maksimum, kompletność."""
    if data.empty:
        return pd.DataFrame(columns=["station_code", "n", "mean", "median", "p95", "max", "completeness"])
    grp = data.groupby("station_code")["value"]
    out = grp.agg(n="size", mean="mean", median="median", max="max").reset_index()
    out["p95"] = grp.quantile(0.95).to_numpy()
    out["completeness"] = [
        min(n / expected_count(start, end, step, 1), 1.0) if step is not None else np.nan for n in out["n"]
    ]
    return out[["station_code", "n", "mean", "median", "p95", "max", "completeness"]]


def pollutant_overview(
    meas: pd.DataFrame, start: date, end: date, stations: list[str], default_avg: dict[str, str]
) -> pd.DataFrame:
    """Przegląd wszystkich wskaźników w zbiorze dla wybranych stacji i zakresu dat.

    Dla każdego wskaźnika używany jest jego domyślny (najkrótszy) czas uśredniania.
    Wartości różnych wskaźników mają różne jednostki - nie porównujemy ich bezpośrednio.
    """
    ts = meas["timestamp"]
    mask = meas["station_code"].isin(stations)
    mask &= (ts >= pd.Timestamp(start)) & (ts < pd.Timestamp(end) + pd.Timedelta(days=1))
    sub = meas.loc[mask]
    rows = []
    for pollutant, avg in default_avg.items():
        part = sub[(sub["pollutant"] == pollutant) & (sub["averaging"] == avg)]
        if part.empty:
            continue
        rows.append({
            "pollutant": pollutant,
            "averaging": avg,
            "stations": part["station_code"].nunique(),
            "n": len(part),
            "mean": part["value"].mean(),
            "p95": part["value"].quantile(0.95),
            "max": part["value"].max(),
        })
    return pd.DataFrame(rows, columns=["pollutant", "averaging", "stations", "n", "mean", "p95", "max"])


# --- Zestawienia roczne, miesięczne, sezonowe ---------------------------------
def annual_summary(agg_year: pd.DataFrame) -> pd.DataFrame:
    """Podsumowanie roczne: średnia ze stacji o wystarczającym pokryciu oraz informacje o jakości."""
    if agg_year.empty:
        return pd.DataFrame(columns=["year", "mean", "mean_all", "stations", "stations_ok", "coverage", "reliable"])
    df = agg_year.copy()
    df["year"] = df["timestamp"].dt.year
    rows = []
    for year, g in df.groupby("year"):
        ok = g[g["reliable"]]
        rows.append({
            "year": int(year),
            "mean": ok["value"].mean() if len(ok) else np.nan,
            "mean_all": g["value_all"].mean(),
            "stations": g["station_code"].nunique(),
            "stations_ok": len(ok),
            "coverage": g["coverage"].mean(),
            "reliable": len(ok) > 0,
        })
    return pd.DataFrame(rows)


def year_month_means(agg_month: pd.DataFrame, years: list[int]) -> pd.DataFrame:
    """Średnie miesięczne (po stacjach) dla wybranych lat - tylko z wiarygodnych miesięcy."""
    if agg_month.empty:
        return pd.DataFrame(columns=["year", "month", "value", "n_stations"])
    ok = agg_month[agg_month["reliable"]].copy()
    ok["year"] = ok["timestamp"].dt.year
    ok["month"] = ok["timestamp"].dt.month
    ok = ok[ok["year"].isin(years)]
    return (
        ok.groupby(["year", "month"])["value"]
        .agg(value="mean", n_stations="count")
        .reset_index()
    )


def calendar_month_profile(agg_month: pd.DataFrame) -> pd.DataFrame:
    """Typowy przebieg roczny: średnia i odchylenie standardowe dla miesięcy kalendarzowych (1-12)."""
    net = network_mean(agg_month)
    if net.empty:
        return pd.DataFrame(columns=["month", "mean", "std", "n_years"])
    net["month"] = net["timestamp"].dt.month
    prof = net.groupby("month")["mean"].agg(mean="mean", std="std", n_years="count").reset_index()
    return prof


def _season_days(season_year: int, season: str) -> int:
    """Liczba dni kalendarzowych sezonu meteorologicznego (zima: XII poprzedniego roku - II)."""
    total = 0
    for month in SEASON_MONTHS[season]:
        year = season_year - 1 if (season == "Zima" and month == 12) else season_year
        total += calendar.monthrange(year, month)[1]
    return total


def season_table(agg_day: pd.DataFrame, min_coverage: float) -> pd.DataFrame:
    """Dobowe średnie z sieci stacji z przypisaniem do sezonu meteorologicznego.

    Sezon danego roku jest uwzględniany tylko, gdy ma wystarczająco dużo dni z danymi.
    Grudzień należy do zimy następnego roku (zima 2023 = XII 2022 - II 2023).
    """
    net = network_mean(agg_day)
    if net.empty:
        return pd.DataFrame(columns=["timestamp", "value", "season", "season_year", "season_coverage"])
    month = net["timestamp"].dt.month
    net["season"] = month.map(season_of_month)
    net["season_year"] = net["timestamp"].dt.year + (month == 12).astype(int)
    counts = net.groupby(["season_year", "season"]).size().rename("n_days").reset_index()
    counts["expected"] = [_season_days(int(y), s) for y, s in zip(counts["season_year"], counts["season"])]
    counts["season_coverage"] = counts["n_days"] / counts["expected"]
    merged = net.merge(counts[["season_year", "season", "season_coverage"]], on=["season_year", "season"])
    merged = merged[merged["season_coverage"] >= min_coverage]
    merged["season"] = pd.Categorical(merged["season"], categories=SEASON_ORDER, ordered=True)
    return merged.rename(columns={"mean": "value"})[
        ["timestamp", "value", "season", "season_year", "season_coverage"]
    ]

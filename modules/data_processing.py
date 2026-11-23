"""Czyszczenie i standaryzacja danych pomiarowych.

Wejście: surowa tabela tekstowa (wszystkie kolumny jako str) + ColumnMapping.
Wyjście: dwie tabele w ujednoliconym schemacie:

* ``measurements`` (format długi):
  timestamp, station_code, pollutant, value, unit, averaging
* ``stations``:
  station_code, station_name, city, voivodeship, latitude, longitude

Zasady (istotne dla pracy dyplomowej):
* brak pomiaru NIGDY nie jest zamieniany na zero - wiersz z brakiem jest usuwany
  (i policzony w raporcie), a pokrycie danymi liczone jest osobno;
* wartości ujemne są domyślnie traktowane jako brak danych (opcja), a ich liczba
  jest raportowana;
* duplikaty identyczne są usuwane, duplikaty o różnych wartościach uśredniane;
* żadne współrzędne nie są uzupełniane "z głowy" - tylko z danych lub metadanych.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from modules.config import POLAND_BBOX, POLLUTANTS
from modules.utils import get_logger, normalise_text

logger = get_logger(__name__)

NA_TOKENS = {"", "na", "n/a", "nan", "null", "none", "-", "--", "brak", "b/d", "nd", "#n/a"}

LAYOUT_LONG = "long"
LAYOUT_POLLUTANT_COLUMNS = "pollutant_columns"
LAYOUT_STATION_COLUMNS = "station_columns"
LAYOUT_LABELS = {
    LAYOUT_LONG: "Format długi: jedna kolumna ze wskaźnikiem i jedna z wartością",
    LAYOUT_POLLUTANT_COLUMNS: "Kolumny wskaźników: osobna kolumna dla każdego zanieczyszczenia",
    LAYOUT_STATION_COLUMNS: "Kolumny stacji: osobna kolumna dla każdej stacji (jeden wskaźnik)",
}


class DataFormatError(ValueError):
    """Błąd struktury lub zawartości danych. Komunikat jest po polsku i gotowy do wyświetlenia."""


# --- Mapowanie kolumn ---------------------------------------------------------
@dataclass
class ColumnMapping:
    """Opis tego, które kolumny pliku pełnią jakie role."""

    layout: str = LAYOUT_LONG
    time_col: str | None = None
    station_col: str | None = None
    pollutant_col: str | None = None
    value_col: str | None = None
    pollutant_cols: list[str] = field(default_factory=list)
    station_cols: list[str] = field(default_factory=list)
    station_name_col: str | None = None
    city_col: str | None = None
    voivodeship_col: str | None = None
    lat_col: str | None = None
    lon_col: str | None = None
    unit_col: str | None = None
    averaging_col: str | None = None
    fixed_pollutant: str | None = None
    fixed_station: str | None = None
    fixed_unit: str | None = None
    fixed_averaging: str | None = None

    def validate(self, columns: list[str]) -> None:
        """Sprawdza kompletność mapowania i istnienie kolumn; rzuca DataFormatError."""
        cols = set(columns)
        problems: list[str] = []
        if not self.time_col:
            problems.append("nie wskazano kolumny z czasem pomiaru")
        if self.layout == LAYOUT_LONG:
            if not self.station_col:
                problems.append("nie wskazano kolumny ze stacją")
            if not self.pollutant_col:
                problems.append("nie wskazano kolumny ze wskaźnikiem")
            if not self.value_col:
                problems.append("nie wskazano kolumny z wartością")
        elif self.layout == LAYOUT_POLLUTANT_COLUMNS:
            if not self.pollutant_cols:
                problems.append("nie wskazano kolumn ze wskaźnikami")
            if not self.station_col and not self.fixed_station:
                problems.append("nie wskazano kolumny stacji ani nazwy stacji stałej dla pliku")
        elif self.layout == LAYOUT_STATION_COLUMNS:
            if not self.station_cols:
                problems.append("nie wskazano kolumn ze stacjami")
            if not self.fixed_pollutant:
                problems.append("nie wskazano wskaźnika, którego dotyczy plik")
        else:
            problems.append(f"nieznany układ danych: {self.layout}")

        used = [
            self.time_col, self.station_col, self.pollutant_col, self.value_col,
            self.station_name_col, self.city_col, self.voivodeship_col, self.lat_col,
            self.lon_col, self.unit_col, self.averaging_col,
            *self.pollutant_cols, *self.station_cols,
        ]
        missing = sorted({c for c in used if c and c not in cols})
        if missing:
            problems.append("w pliku brakuje kolumn: " + ", ".join(missing))
        if problems:
            raise DataFormatError("Mapowanie kolumn jest niepoprawne: " + "; ".join(problems) + ".")


# --- Raport walidacji ---------------------------------------------------------
@dataclass
class ValidationReport:
    """Podsumowanie czyszczenia danych - wyświetlane użytkownikowi i opisywalne w pracy."""

    rows_read: int = 0
    values_read: int = 0
    rows_valid: int = 0
    n_bad_time: int = 0
    n_bad_station: int = 0
    n_missing_value: int = 0
    n_bad_value: int = 0
    n_negative: int = 0
    negative_policy: str = "nan"
    n_dup_exact: int = 0
    n_dup_conflict: int = 0
    n_stations_no_coords: int = 0
    n_coords_out_of_bounds: int = 0
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_rows(self) -> list[tuple[str, str]]:
        """Wiersze (opis, wartość) do tabeli w interfejsie."""
        neg_txt = "oznaczone jako brak danych" if self.negative_policy == "nan" else "pozostawione bez zmian"
        return [
            ("Wiersze w pliku", f"{self.rows_read:,}".replace(",", " ")),
            ("Odczytane wartości (po rozwinięciu kolumn)", f"{self.values_read:,}".replace(",", " ")),
            ("Poprawne pomiary po czyszczeniu", f"{self.rows_valid:,}".replace(",", " ")),
            ("Brakujące wartości (usunięte, nie zastąpione zerem)", f"{self.n_missing_value:,}".replace(",", " ")),
            ("Wartości niemożliwe do odczytania jako liczba", f"{self.n_bad_value:,}".replace(",", " ")),
            ("Wartości ujemne (" + neg_txt + ")", f"{self.n_negative:,}".replace(",", " ")),
            ("Wiersze z niepoprawnym czasem", f"{self.n_bad_time:,}".replace(",", " ")),
            ("Wiersze bez kodu stacji", f"{self.n_bad_station:,}".replace(",", " ")),
            ("Duplikaty identyczne (usunięte)", f"{self.n_dup_exact:,}".replace(",", " ")),
            ("Duplikaty o różnych wartościach (uśrednione)", f"{self.n_dup_conflict:,}".replace(",", " ")),
            ("Stacje bez współrzędnych", str(self.n_stations_no_coords)),
            ("Współrzędne poza obszarem Polski (odrzucone)", str(self.n_coords_out_of_bounds)),
        ]


# --- Parsowanie wartości ------------------------------------------------------
def parse_numeric(series: pd.Series) -> tuple[pd.Series, int]:
    """Konwertuje tekst na liczby, obsługując przecinek i kropkę jako separator dziesiętny.

    Zwraca (wartości float64, liczba niepustych wpisów, których nie dało się odczytać).
    Puste wpisy i tokeny typu "NA", "-", "brak" dają NaN (nie zero).
    """
    s = (
        series.astype("string")
        .str.strip()
        .str.replace(" ", "", regex=False)
        .str.replace(" ", "", regex=False)
    )
    is_na = s.isna() | s.str.lower().isin(NA_TOKENS)
    s = s.mask(is_na)

    has_comma = s.str.contains(",", regex=False).fillna(False).astype(bool)
    has_dot = s.str.contains(".", regex=False).fillna(False).astype(bool)

    only_comma = has_comma & ~has_dot
    if only_comma.any():
        s = s.mask(only_comma, s.str.replace(",", ".", regex=False))

    both = has_comma & has_dot
    if both.any():
        sub = s[both]
        comma_is_decimal = (sub.str.rfind(",") > sub.str.rfind(".")).astype(bool)
        as_comma_decimal = sub.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
        as_dot_decimal = sub.str.replace(",", "", regex=False)
        fixed = as_comma_decimal.where(comma_is_decimal, as_dot_decimal)
        s = s.mask(both, fixed.reindex(s.index))

    out = pd.to_numeric(s, errors="coerce").astype("float64")
    n_bad = int((~is_na & out.isna()).sum())
    return out, n_bad


_DT_FORMATS = [
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
    "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M",
    "%Y/%m/%d %H:%M", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y",
]
_H24_PATTERN = r"(?:^|[ T])24:00(?::00)?$"


def parse_datetime(series: pd.Series) -> tuple[pd.Series, list[str]]:
    """Parsuje daty i godziny w wielu popularnych formatach (ISO, DD.MM.RRRR, ...).

    * wybiera jeden format na podstawie próbki (szybciej i bezpieczniej niż zgadywanie
      każdej wartości osobno, zwłaszcza dla DD.MM vs MM.DD);
    * rozpoznaje zapis godziny "24:00" jako północ następnego dnia;
    * znaczniki ze strefą czasową są przeliczane na czas Europe/Warsaw i zapisywane
      bez strefy; znacznikom bez strefy nie przypisuję strefy czasowej.

    Zwraca (znaczniki czasu datetime64[ns], lista notatek dla raportu).
    """
    notes: list[str] = []
    s = series.astype("string").str.strip()
    s = s.mask(s.str.lower().isin(NA_TOKENS))

    shifted = s.str.contains(_H24_PATTERN, regex=True).fillna(False).astype(bool)
    if shifted.any():
        s = s.str.replace(r"([ T])24:00(?::00)?$", r"\g<1>00:00", regex=True)
        notes.append(
            f"Zapis godziny „24:00” ({int(shifted.sum()):,} wierszy) zinterpretowano jako 00:00 następnego dnia."
            .replace(",", " ")
        )

    non_null = s.dropna()
    if non_null.empty:
        return pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]"), notes

    sample = non_null.iloc[:500]
    best_fmt, best_rate = None, 0.0
    for fmt in _DT_FORMATS:
        rate = float(pd.to_datetime(sample, format=fmt, errors="coerce").notna().mean())
        if rate > best_rate:
            best_fmt, best_rate = fmt, rate
        if rate == 1.0:
            break

    if best_fmt is not None and best_rate >= 0.95:
        ts = pd.to_datetime(s, format=best_fmt, errors="coerce")
        notes.append(f"Format daty rozpoznany automatycznie: {best_fmt}.")
    else:
        dayfirst = bool(sample.str.match(r"^\d{1,2}[./-]\d{1,2}[./-]\d{4}").mean() > 0.5)
        ts = pd.to_datetime(s, format="mixed", dayfirst=dayfirst, errors="coerce")
        notes.append(
            "Nie rozpoznano jednolitego formatu daty - użyto elastycznego parsera"
            + (" (dzień na początku)." if dayfirst else ".")
        )

    if isinstance(ts.dtype, pd.DatetimeTZDtype):
        ts = ts.dt.tz_convert("Europe/Warsaw").dt.tz_localize(None)
        notes.append("Znaczniki czasu ze strefą przeliczono na Europe/Warsaw i zapisano bez strefy.")

    ts = ts.astype("datetime64[ns]")
    if shifted.any():
        ts = ts.mask(shifted, ts + pd.Timedelta(days=1))
    return ts, notes


# --- Normalizacja etykiet -----------------------------------------------------
_POLLUTANT_ALIASES = {
    "pm10": "PM10", "pm25": "PM2.5", "no2": "NO2", "so2": "SO2", "o3": "O3",
    "co": "CO", "c6h6": "C6H6", "benzen": "C6H6", "ozon": "O3",
    "tlenekwegla": "CO", "dwutlenekazotu": "NO2", "dwutlenekssiarki": "SO2",
    "dwutleneksiarki": "SO2",
}


def normalise_pollutant_name(name: object) -> str:
    """Sprowadza zapis wskaźnika do kodu kanonicznego (np. 'PM2,5' -> 'PM2.5').

    Nierozpoznane nazwy są zwracane bez zmian (po usunięciu białych znaków).
    """
    key = normalise_text(name)
    if key in _POLLUTANT_ALIASES:
        return _POLLUTANT_ALIASES[key]
    for token in ("pm25", "pm10", "c6h6", "benzen"):
        if token in key:
            return _POLLUTANT_ALIASES[token]
    return str(name).strip()


def normalise_averaging_label(label: object) -> str:
    """Ujednolica czas uśredniania: '1g', '1 h', 'godzinowy' -> '1h'; 'doba' -> '24h'."""
    s = str(label).strip().lower().replace(" ", "")
    if s in {"", "nan", "none", "<na>"}:
        return ""
    m = re.fullmatch(r"(\d+)(?:g|h|godz|godzin\w*|hr)?", s)
    if m:
        return f"{int(m.group(1))}h"
    if s in {"doba", "dobowy", "dzien", "dzienny", "daily", "day"}:
        return "24h"
    if s in {"godzina", "godzinowy", "hourly", "hour"}:
        return "1h"
    if s in {"rok", "roczny", "year", "yearly", "annual"}:
        return "1y"
    return s


def normalise_unit(label: object) -> str:
    """Ujednolica zapis jednostki: 'ug/m3', 'μg/m3' -> 'µg/m³'."""
    s = str(label).strip()
    if s.lower() in NA_TOKENS:
        return ""
    s = s.replace("μ", "µ")  # grecka mi -> znak mikro
    s = re.sub(r"^(?:ug|µg|mikrog\w*)", "µg", s, flags=re.IGNORECASE)
    s = re.sub(r"^mg", "mg", s, flags=re.IGNORECASE)
    s = re.sub(r"m3$", "m³", s)
    s = s.replace("^3", "³")
    return s


def _map_unique(series: pd.Series, func) -> pd.Series:
    """Stosuje funkcję tylko do unikalnych wartości (szybkie dla dużych tabel)."""
    uniques = series.dropna().unique()
    mapping = {u: func(u) for u in uniques}
    return series.map(mapping)


# --- Konwersja do formatu długiego --------------------------------------------
def _to_long(raw: pd.DataFrame, m: ColumnMapping) -> pd.DataFrame:
    """Przekształca surową tabelę do formatu długiego o stałych nazwach kolumn."""
    base: dict[str, pd.Series] = {"time_raw": raw[m.time_col]}
    if m.station_col:
        base["station_code"] = raw[m.station_col]
    elif m.fixed_station:
        base["station_code"] = pd.Series(m.fixed_station, index=raw.index)
    if m.unit_col:
        base["unit_raw"] = raw[m.unit_col]
    if m.averaging_col:
        base["averaging_raw"] = raw[m.averaging_col]
    frame = pd.DataFrame(base)

    if m.layout == LAYOUT_LONG:
        frame["pollutant_raw"] = raw[m.pollutant_col]
        frame["value_raw"] = raw[m.value_col]
        return frame

    if m.layout == LAYOUT_POLLUTANT_COLUMNS:
        value_vars = list(dict.fromkeys(m.pollutant_cols))
        wide = pd.concat([frame, raw[value_vars]], axis=1)
        return wide.melt(
            id_vars=list(frame.columns), value_vars=value_vars,
            var_name="pollutant_raw", value_name="value_raw",
        )

    value_vars = list(dict.fromkeys(m.station_cols))
    wide = pd.concat([frame, raw[value_vars]], axis=1)
    long = wide.melt(
        id_vars=list(frame.columns), value_vars=value_vars,
        var_name="station_code", value_name="value_raw",
    )
    long["pollutant_raw"] = m.fixed_pollutant
    return long


def _infer_averaging(df: pd.DataFrame) -> pd.Series:
    """Wnioskuje czas uśredniania z mediany odstępów między kolejnymi pomiarami serii."""
    keys = ["station_code", "pollutant"]
    ordered = df.sort_values(keys + ["timestamp"])
    diff = ordered.groupby(keys, sort=False)["timestamp"].diff()
    median_step = diff.groupby([ordered[k] for k in keys], sort=False).median()
    hours = median_step.dt.total_seconds() / 3600

    def label(h: float) -> str:
        if pd.isna(h):
            return ""
        if h <= 1.5:
            return "1h"
        if 22 <= h <= 26:
            return "24h"
        return f"{round(h)}h"

    labels = hours.map(label).rename("averaging").reset_index()
    merged = df[keys].merge(labels, on=keys, how="left")
    return pd.Series(merged["averaging"].fillna("").to_numpy(), index=df.index)


def finalise_measurements(df: pd.DataFrame) -> pd.DataFrame:
    """Ustala typy (kategorie) i sortuje dane - wspólny krok dla danych z plików i demo."""
    out = df[["timestamp", "station_code", "pollutant", "value", "unit", "averaging"]].copy()
    for col in ("station_code", "pollutant", "unit", "averaging"):
        out[col] = out[col].astype("category")
    out = out.sort_values(["pollutant", "station_code", "timestamp"], kind="stable")
    return out.reset_index(drop=True)


def _deduplicate(df: pd.DataFrame, report: ValidationReport) -> pd.DataFrame:
    """Usuwa duplikaty identyczne, a duplikaty o różnych wartościach uśrednia."""
    keys = ["timestamp", "station_code", "pollutant", "averaging"]
    before = len(df)
    df = df.drop_duplicates(subset=keys + ["value", "unit"])
    report.n_dup_exact = before - len(df)

    dup_mask = df.duplicated(subset=keys, keep=False)
    n_conflict = int(dup_mask.sum())
    if n_conflict:
        agg = (
            df[dup_mask]
            .groupby(keys, as_index=False, sort=False)
            .agg(value=("value", "mean"), unit=("unit", "first"))
        )
        df = pd.concat([df[~dup_mask], agg[df.columns]], ignore_index=True)
        report.n_dup_conflict = n_conflict
        report.warnings.append(
            f"Znaleziono {n_conflict} wierszy o tym samym czasie, stacji i wskaźniku, ale różnych wartościach; "
            "zastąpiono je średnią. Sprawdź źródło danych."
        )
    return df


# --- Tabela stacji ------------------------------------------------------------
STATION_COLUMNS = ["station_code", "station_name", "city", "voivodeship", "latitude", "longitude"]


def _clean_coordinates(stations: pd.DataFrame, report: ValidationReport) -> pd.DataFrame:
    """Parsuje współrzędne i odrzuca te spoza obszaru Polski (bez zgadywania poprawek)."""
    st = stations.copy()
    for col in ("latitude", "longitude"):
        st[col], _ = parse_numeric(st[col]) if col in st else (pd.Series(np.nan, index=st.index), 0)
    b = POLAND_BBOX
    bad = (
        st["latitude"].notna() & st["longitude"].notna()
        & ~(st["latitude"].between(b["lat_min"], b["lat_max"]) & st["longitude"].between(b["lon_min"], b["lon_max"]))
    )
    report.n_coords_out_of_bounds = int(bad.sum())
    if bad.any():
        report.warnings.append(
            f"{int(bad.sum())} stacji ma współrzędne poza obszarem Polski - współrzędne pominięto. "
            "Możliwa zamiana szerokości z długością geograficzną lub inny format zapisu."
        )
        st.loc[bad, ["latitude", "longitude"]] = np.nan
    return st


def build_station_table(
    raw: pd.DataFrame,
    mapping: ColumnMapping,
    codes_in_data: list[str],
    station_meta: pd.DataFrame | None,
    report: ValidationReport,
) -> pd.DataFrame:
    """Buduje tabelę stacji z kolumn pliku i/lub zewnętrznych metadanych.

    Stacje występujące w pomiarach, a niewystępujące w metadanych, otrzymują puste atrybuty.
    """
    attr_cols = {
        "station_name": mapping.station_name_col, "city": mapping.city_col,
        "voivodeship": mapping.voivodeship_col, "latitude": mapping.lat_col,
        "longitude": mapping.lon_col,
    }
    table = pd.DataFrame({"station_code": codes_in_data})
    present = {k: v for k, v in attr_cols.items() if v}
    if mapping.station_col and present:
        sub = pd.DataFrame({"station_code": raw[mapping.station_col].astype("string").str.strip()})
        for k, v in present.items():
            sub[k] = raw[v]
        sub = sub.replace({"": pd.NA}).dropna(subset=["station_code"])
        grouped = sub.groupby("station_code", sort=False).first().reset_index()
        table = table.merge(grouped, on="station_code", how="left")

    for col in STATION_COLUMNS[1:]:
        if col not in table:
            table[col] = pd.NA

    if station_meta is not None and not station_meta.empty:
        meta = station_meta.copy()
        meta["station_code"] = meta["station_code"].astype("string").str.strip()
        meta = meta.drop_duplicates("station_code").set_index("station_code")
        table = table.set_index("station_code")
        for col in STATION_COLUMNS[1:]:
            if col in meta:
                table[col] = meta[col].reindex(table.index).combine_first(table[col])
        table = table.reset_index()
        unmatched = [c for c in codes_in_data if c not in set(meta.index)]
        if unmatched:
            report.warnings.append(
                f"{len(unmatched)} kodów stacji z pomiarów nie występuje w pliku metadanych "
                f"(np. {', '.join(unmatched[:3])})."
            )

    table = _clean_coordinates(table, report)
    table["station_code"] = table["station_code"].astype(str)
    for col in ("station_name", "city", "voivodeship"):
        table[col] = table[col].astype("string").fillna("").astype(str).str.strip()
    report.n_stations_no_coords = int((table["latitude"].isna() | table["longitude"].isna()).sum())
    return table[STATION_COLUMNS].reset_index(drop=True)


# --- Główna funkcja standaryzacji ---------------------------------------------
def standardise(
    raw: pd.DataFrame,
    mapping: ColumnMapping,
    *,
    negative_policy: str = "nan",
    station_meta: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, ValidationReport]:
    """Czyści surową tabelę i zwraca (pomiary, stacje, raport).

    Args:
        raw: tabela z wszystkimi kolumnami jako tekst.
        mapping: przypisanie ról kolumnom.
        negative_policy: "nan" - wartości ujemne traktuj jako brak danych; "keep" - zachowaj.
        station_meta: opcjonalna tabela metadanych stacji (kolumny wg STATION_COLUMNS).

    Raises:
        DataFormatError: gdy mapowanie jest niepoprawne albo po czyszczeniu nie zostaje żaden pomiar.
    """
    report = ValidationReport(rows_read=len(raw), negative_policy=negative_policy)
    mapping.validate(list(raw.columns))
    long = _to_long(raw, mapping)
    report.values_read = len(long)

    ts, notes = parse_datetime(long["time_raw"])
    report.notes.extend(notes)
    report.n_bad_time = int(ts.isna().sum())

    values, n_bad_value = parse_numeric(long["value_raw"])
    report.n_bad_value = n_bad_value
    report.n_missing_value = int(values.isna().sum()) - n_bad_value

    negative = values < 0
    report.n_negative = int(negative.sum())
    if report.n_negative and negative_policy == "nan":
        values = values.mask(negative)
        report.warnings.append(
            f"{report.n_negative} wartości ujemnych potraktowano jako brak danych "
            "(ujemne stężenia są fizycznie niemożliwe; mogą wynikać z dryfu przyrządu)."
        )
    elif report.n_negative:
        report.warnings.append(f"{report.n_negative} wartości ujemnych pozostawiono w danych.")

    code = long["station_code"].astype("string").str.strip()
    code_ok = code.notna() & (code != "")
    report.n_bad_station = int((~code_ok).sum())

    work = pd.DataFrame({
        "timestamp": ts,
        "station_code": code.astype(object),
        "pollutant": _map_unique(long["pollutant_raw"].astype("string").str.strip(), normalise_pollutant_name).astype(object),
        "value": values,
    })
    if "unit_raw" in long:
        work["unit"] = _map_unique(long["unit_raw"].astype("string"), normalise_unit).fillna("")
    else:
        work["unit"] = normalise_unit(mapping.fixed_unit) if mapping.fixed_unit else ""
    if "averaging_raw" in long:
        work["averaging"] = _map_unique(long["averaging_raw"].astype("string"), normalise_averaging_label).fillna("")
    elif mapping.fixed_averaging:
        work["averaging"] = normalise_averaging_label(mapping.fixed_averaging)
    else:
        work["averaging"] = ""

    valid = work["timestamp"].notna() & code_ok.to_numpy() & work["value"].notna()
    work = work.loc[valid].reset_index(drop=True)
    if work.empty:
        raise DataFormatError(
            "Po czyszczeniu nie zostały żadne poprawne pomiary. Sprawdź mapowanie kolumn, format daty "
            "i separator dziesiętny."
        )

    if (work["averaging"] == "").all():
        work["averaging"] = _infer_averaging(work)
        report.notes.append("Czas uśredniania nie był podany - oszacowano go z odstępów między pomiarami.")
    elif (work["averaging"] == "").any():
        report.warnings.append("Część wierszy nie ma podanego czasu uśredniania (etykieta pusta).")

    if (work["unit"] == "").all():
        report.notes.append(
            "Plik nie zawiera jednostek - w interfejsie pokazano jednostki domyślne "
            "(µg/m³; CO: mg/m³) jako ZAŁOŻONE. Zweryfikuj je w metadanych źródła."
        )

    work = _deduplicate(work, report)
    unknown = sorted(set(work["pollutant"]) - set(POLLUTANTS))
    if unknown:
        report.warnings.append("Nierozpoznane wskaźniki (zachowane bez zmian): " + ", ".join(unknown) + ".")

    measurements = finalise_measurements(work)
    report.rows_valid = len(measurements)
    codes = sorted(measurements["station_code"].astype(str).unique())
    stations = build_station_table(raw, mapping, codes, station_meta, report)
    logger.info("Standaryzacja zakończona: %s pomiarów, %s stacji", report.rows_valid, len(stations))
    return measurements, stations, report


def build_catalog(meas: pd.DataFrame) -> pd.DataFrame:
    """Katalog serii: dla każdej kombinacji wskaźnik/uśrednianie/stacja/jednostka liczba pomiarów i zakres dat."""
    cat = (
        meas.groupby(["pollutant", "averaging", "station_code", "unit"], observed=True)
        .agg(n=("value", "size"), first=("timestamp", "min"), last=("timestamp", "max"))
        .reset_index()
    )
    for col in ("pollutant", "averaging", "station_code", "unit"):
        cat[col] = cat[col].astype(str)
    return cat

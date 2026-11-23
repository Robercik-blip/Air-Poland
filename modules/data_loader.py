"""Wczytywanie plików użytkownika, rocznych archiwów GIOŚ, danych demo i adaptera API.

CSV/XLSX użytkownika przechodzą przez wykrywanie mapowania i jawne reguły czyszczenia.
Archiwa ZIP GIOŚ mają osobny parser oparty na zweryfikowanym układzie ich skoroszytów.
"""
from __future__ import annotations

import csv
import io
import uuid
import zipfile
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import openpyxl
import pandas as pd

from modules import demo_data
from modules.config import (
    DEMO_LABEL, POLLUTANT_ORDER, POLLUTANTS, RAW_DATA_DIR, SOURCE_GIOS_ARCHIVES, SOURCE_GIOS_NAME,
)
from modules.data_processing import (
    LAYOUT_LONG, LAYOUT_POLLUTANT_COLUMNS, LAYOUT_STATION_COLUMNS, STATION_COLUMNS, ColumnMapping,
    DataFormatError, ValidationReport, build_catalog, finalise_measurements,
    normalise_pollutant_name, standardise,
)
from modules.utils import get_logger, normalise_text

logger = get_logger(__name__)

SOURCE_LOCAL = "local"
SOURCE_UPLOAD = "upload"
SOURCE_DEMO = "demo"
SOURCE_API = "api"
SOURCE_GIOS_ARCHIVE = "gios_archive"
SOURCE_LABELS = {
    SOURCE_LOCAL: "Plik lokalny (data/raw)",
    SOURCE_UPLOAD: "Plik wgrany przez użytkownika",
    SOURCE_DEMO: DEMO_LABEL,
    SOURCE_API: "GIOŚ API",
    SOURCE_GIOS_ARCHIVE: "Archiwum pomiarowe GIOŚ",
}


# --- Obiekt zbioru danych -----------------------------------------------------
@dataclass
class Dataset:
    """Kompletny, zwalidowany zbiór danych wraz z metadanymi."""

    dataset_id: str
    name: str
    source_kind: str
    is_synthetic: bool
    measurements: pd.DataFrame
    stations: pd.DataFrame
    report: ValidationReport
    attribution: str
    catalog: pd.DataFrame = field(init=False)
    station_labels: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        self.catalog = build_catalog(self.measurements)
        labels: dict[str, str] = {}
        names = self.stations.set_index("station_code")["station_name"].to_dict()
        for code in self.catalog["station_code"].unique():
            name = str(names.get(code, "") or "").strip()
            labels[code] = f"{name} [{code}]" if name and name != code else code
        self.station_labels = labels
        self._check_units()

    def _check_units(self) -> None:
        for pollutant, grp in self.catalog.groupby("pollutant"):
            units = sorted({u for u in grp["unit"] if u})
            if len(units) > 1:
                self.report.warnings.append(
                    f"Wskaźnik {pollutant} występuje w różnych jednostkach ({', '.join(units)}). "
                    "Łączenie takich serii w jednej analizie byłoby błędne."
                )

    # -- dostęp do metadanych zbioru
    @property
    def pollutants(self) -> list[str]:
        present = set(self.catalog["pollutant"])
        known = [p for p in POLLUTANT_ORDER if p in present]
        return known + sorted(present - set(known))

    def stations_for(self, pollutant: str) -> list[str]:
        codes = self.catalog.loc[self.catalog["pollutant"] == pollutant, "station_code"].unique()
        return sorted(codes)

    def averaging_options(self, pollutant: str) -> list[str]:
        opts = list(self.catalog.loc[self.catalog["pollutant"] == pollutant, "averaging"].unique())
        return sorted(opts, key=_averaging_sort_key)

    def default_averaging(self, pollutant: str) -> str:
        options = self.averaging_options(pollutant)
        return options[0] if options else ""

    def unit_for(self, pollutant: str) -> tuple[str, bool]:
        """Zwraca (jednostka, czy_założona). Jednostka z danych ma pierwszeństwo."""
        units = [u for u in self.catalog.loc[self.catalog["pollutant"] == pollutant, "unit"].unique() if u]
        if units:
            return units[0], False
        return POLLUTANTS.get(pollutant, {}).get("default_unit", ""), True

    def date_bounds(self, pollutant: str) -> tuple[date, date]:
        sub = self.catalog[self.catalog["pollutant"] == pollutant]
        return sub["first"].min().date(), sub["last"].max().date()

    def overall_bounds(self) -> tuple[date, date]:
        return self.catalog["first"].min().date(), self.catalog["last"].max().date()

    def station_label(self, code: str) -> str:
        return self.station_labels.get(code, code)


def _averaging_sort_key(label: str) -> tuple[int, float]:
    """Sortuje etykiety uśredniania od najkrótszych; puste/nieznane na końcu."""
    digits = "".join(ch for ch in label if ch.isdigit())
    if label.endswith("h") and digits:
        return (0, float(digits))
    if label == "1y":
        return (0, 8760.0)
    return (1, 0.0)


# --- Odczyt plików ------------------------------------------------------------
_ENCODINGS = ("utf-8-sig", "utf-8", "cp1250", "iso-8859-2")


def _decode(content: bytes) -> str:
    for enc in _ENCODINGS:
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1")


def _detect_separator(text: str) -> str:
    """Wybiera separator, który daje stałą liczbę pól w pierwszych liniach pliku."""
    lines = [ln for ln in text.splitlines()[:30] if ln.strip()]
    best, best_score = ";", -1
    for sep in (";", "\t", "|", ","):
        counts = [len(next(csv.reader([ln], delimiter=sep))) for ln in lines]
        if not counts or max(counts) < 2:
            continue
        score = (counts.count(max(set(counts), key=counts.count)) / len(counts)) * max(counts)
        if score > best_score:
            best, best_score = sep, score
    return best


def read_table(content: bytes, filename: str, header_row: int = 0) -> pd.DataFrame:
    """Wczytuje CSV (lub XLSX) jako tabelę tekstową.

    Args:
        content: bajty pliku.
        filename: nazwa pliku (rozszerzenie decyduje o formacie).
        header_row: numer wiersza nagłówka (od 0) - przydatne, gdy plik ma wiersze opisowe na górze.
    """
    name = filename.lower()
    try:
        if name.endswith((".xlsx", ".xls")):
            try:
                df = pd.read_excel(io.BytesIO(content), dtype=str, header=header_row)
            except ImportError as exc:
                raise DataFormatError(
                    "Odczyt plików Excel wymaga pakietu openpyxl (pip install openpyxl)."
                ) from exc
        else:
            text = _decode(content)
            sep = _detect_separator(text)
            df = pd.read_csv(
                io.StringIO(text), sep=sep, dtype=str, keep_default_na=False,
                header=header_row, skip_blank_lines=True,
            )
    except DataFormatError:
        raise
    except (pd.errors.ParserError, pd.errors.EmptyDataError, ValueError) as exc:
        logger.warning("Nie udało się odczytać pliku %s: %s", filename, exc)
        raise DataFormatError(f"Nie udało się odczytać pliku „{filename}”: {exc}") from exc

    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[:, [c for c in df.columns if not c.startswith("Unnamed:") or df[c].notna().any()]]
    if df.empty:
        raise DataFormatError(f"Plik „{filename}” nie zawiera danych.")
    return df


def list_local_files() -> list[Path]:
    """Lista plików CSV/XLSX w katalogu data/raw."""
    if not RAW_DATA_DIR.exists():
        return []
    files = [p for p in RAW_DATA_DIR.iterdir() if p.suffix.lower() in {".csv", ".txt", ".xlsx", ".xls"}]
    return sorted(files)


# --- Automatyczne wykrywanie mapowania ----------------------------------------
_SYN_TIME = {"datetime", "date", "time", "timestamp", "data", "datapomiaru", "dataczas", "datagodzina",
             "czas", "czaspomiaru", "dataiczas", "datetimeutc", "datapomiarowa"}
_SYN_STATION = {"stacja", "kodstacji", "stationcode", "station", "stationid", "idstacji", "symbolstacji",
                "kodstanowiska"}
_SYN_STATION_NAME = {"nazwastacji", "stationname", "nazwa", "name"}
_SYN_POLLUTANT = {"wskaznik", "zanieczyszczenie", "pollutant", "parametr", "parameter", "param",
                  "kodwskaznika", "wskaznikkod", "substance"}
_SYN_VALUE = {"wartosc", "value", "stezenie", "result", "pomiar", "measurement", "val", "wartoscpomiaru"}
_SYN_UNIT = {"jednostka", "unit", "jednostkamiary", "units"}
_SYN_AVERAGING = {"czasuredniania", "averaging", "avgtime", "averagingtime", "okresuredniania", "uredniane"}
_SYN_LAT = {"lat", "latitude", "szerokosc", "szerokoscgeograficzna", "wspolrzednalat", "wgslat",
             "wgs84n", "wgs84lat", "latwgs84"}
_SYN_LON = {"lon", "lng", "long", "longitude", "dlugosc", "dlugoscgeograficzna", "wspolrzednalon", "wgslon",
             "wgs84e", "wgs84lon", "lonwgs84"}
_SYN_CITY = {"miejscowosc", "miasto", "city", "town", "gmina"}
_SYN_VOIV = {"wojewodztwo", "voivodeship", "province", "region"}


def _find_column(columns: list[str], synonyms: set[str], exclude: set[str] | None = None) -> str | None:
    exclude = exclude or set()
    for col in columns:
        if col not in exclude and normalise_text(col) in synonyms:
            return col
    return None


def guess_pollutant_from_text(text: str) -> str | None:
    """Próbuje odgadnąć wskaźnik z nazwy pliku (np. '2023_PM10_1g.csv')."""
    key = normalise_text(text)
    for token in ("pm25", "pm10", "c6h6", "benzen", "no2", "so2"):
        if token in key:
            return normalise_pollutant_name(token)
    return None


def detect_mapping(columns: list[str], filename_hint: str = "") -> tuple[ColumnMapping, list[str]]:
    """Proponuje mapowanie kolumn na podstawie ich nazw. Zwraca (mapowanie, uwagi)."""
    notes: list[str] = []
    cols = list(columns)
    m = ColumnMapping()
    m.time_col = _find_column(cols, _SYN_TIME)
    m.station_col = _find_column(cols, _SYN_STATION)
    m.station_name_col = _find_column(cols, _SYN_STATION_NAME)
    m.pollutant_col = _find_column(cols, _SYN_POLLUTANT)
    m.value_col = _find_column(cols, _SYN_VALUE)
    m.unit_col = _find_column(cols, _SYN_UNIT)
    m.averaging_col = _find_column(cols, _SYN_AVERAGING)
    m.lat_col = _find_column(cols, _SYN_LAT)
    m.lon_col = _find_column(cols, _SYN_LON)
    m.city_col = _find_column(cols, _SYN_CITY)
    m.voivodeship_col = _find_column(cols, _SYN_VOIV)

    pollutant_like = [c for c in cols if normalise_pollutant_name(c) in POLLUTANTS]
    if m.pollutant_col and m.value_col:
        m.layout = LAYOUT_LONG
        notes.append("Wykryto format długi (kolumny: wskaźnik + wartość).")
    elif pollutant_like:
        m.layout = LAYOUT_POLLUTANT_COLUMNS
        m.pollutant_cols = pollutant_like
        notes.append("Wykryto kolumny wskaźników: " + ", ".join(pollutant_like) + ".")
    else:
        m.layout = LAYOUT_STATION_COLUMNS
        taken = {m.time_col, m.station_col, m.station_name_col, m.unit_col, m.averaging_col,
                 m.lat_col, m.lon_col, m.city_col, m.voivodeship_col}
        m.station_cols = [c for c in cols if c not in taken]
        m.fixed_pollutant = guess_pollutant_from_text(filename_hint)
        notes.append(
            "Nie wykryto kolumn wskaźników - przyjęto układ „kolumny stacji”. "
            "Wskaż, którego wskaźnika dotyczy plik, i sprawdź listę kolumn stacji."
        )
    if not m.time_col:
        notes.append("Nie rozpoznano kolumny z czasem pomiaru - wskaż ją ręcznie.")
    return m, notes


def parse_station_metadata(df: pd.DataFrame) -> pd.DataFrame:
    """Dopasowuje kolumny pliku metadanych stacji do schematu STATION_COLUMNS."""
    cols = list(df.columns)
    found = {
        "station_code": _find_column(cols, _SYN_STATION | {"kod"}),
        "station_name": _find_column(cols, _SYN_STATION_NAME),
        "city": _find_column(cols, _SYN_CITY),
        "voivodeship": _find_column(cols, _SYN_VOIV),
        "latitude": _find_column(cols, _SYN_LAT),
        "longitude": _find_column(cols, _SYN_LON),
    }
    if not found["station_code"]:
        raise DataFormatError(
            "W pliku metadanych nie rozpoznano kolumny z kodem stacji "
            "(oczekiwano np. „Kod stacji”, „station_code”)."
        )
    out = pd.DataFrame({k: df[v] for k, v in found.items() if v})
    for col in STATION_COLUMNS:
        if col not in out:
            out[col] = pd.NA
    return out[STATION_COLUMNS]


# --- Budowa zbioru ------------------------------------------------------------
def build_dataset(
    raw_frames: list[pd.DataFrame],
    mapping: ColumnMapping,
    *,
    name: str,
    source_kind: str,
    station_meta: pd.DataFrame | None = None,
    negative_policy: str = "nan",
    is_synthetic: bool = False,
) -> Dataset:
    """Łączy tabele (np. kolejne lata) i buduje zwalidowany Dataset.

    Raises:
        DataFormatError: przy niespójnych kolumnach między plikami lub błędach czyszczenia.
    """
    if not raw_frames:
        raise DataFormatError("Nie wskazano żadnych plików.")
    first_cols = list(raw_frames[0].columns)
    for i, frame in enumerate(raw_frames[1:], start=2):
        if list(frame.columns) != first_cols:
            raise DataFormatError(
                f"Plik nr {i} ma inne kolumny niż pierwszy plik. Wszystkie pliki wczytywane razem "
                "muszą mieć identyczny układ kolumn."
            )
    raw = pd.concat(raw_frames, ignore_index=True) if len(raw_frames) > 1 else raw_frames[0]
    measurements, stations, report = standardise(
        raw, mapping, negative_policy=negative_policy, station_meta=station_meta
    )
    return Dataset(
        dataset_id=uuid.uuid4().hex[:12],
        name=name,
        source_kind=source_kind,
        is_synthetic=is_synthetic,
        measurements=measurements,
        stations=stations,
        report=report,
        attribution=(
            f"Dane użytkownika. Oficjalne źródło wzorcowe: {SOURCE_GIOS_NAME} ({SOURCE_GIOS_ARCHIVES})."
            if not is_synthetic else DEMO_LABEL
        ),
    )


_GIOS_POLLUTANTS = {
    "pm10": "PM10",
    "pm25": "PM2.5",
    "no2": "NO2",
    "o3": "O3",
    "so2": "SO2",
    "co": "CO",
    "c6h6": "C6H6",
    "benzen": "C6H6",
}


def _read_gios_metadata(content: bytes) -> tuple[pd.DataFrame, dict[str, str]]:
    """Wczytuje oficjalne arkusze STACJE i STANOWISKA bez zgadywania kolumn."""
    try:
        with pd.ExcelFile(io.BytesIO(content), engine="openpyxl") as workbook:
            required_sheets = {"STACJE", "STANOWISKA"}
            missing = required_sheets - set(workbook.sheet_names)
            if missing:
                raise DataFormatError(
                    "W skoroszycie metadanych GIOŚ brakuje arkuszy: " + ", ".join(sorted(missing)) + "."
                )
        station_raw = pd.read_excel(io.BytesIO(content), sheet_name="STACJE", dtype=str, engine="openpyxl")
        position_raw = pd.read_excel(io.BytesIO(content), sheet_name="STANOWISKA", dtype=str, engine="openpyxl")
    except DataFormatError:
        raise
    except (ImportError, OSError, ValueError, zipfile.BadZipFile, openpyxl.utils.exceptions.InvalidFileException) as exc:
        raise DataFormatError(f"Nie udało się odczytać skoroszytu metadanych GIOŚ: {exc}") from exc

    stations = parse_station_metadata(station_raw)
    old_code_col = next(
        (col for col in station_raw.columns if normalise_text(col).startswith("starykodstacji")),
        None,
    )
    if old_code_col:
        old_codes = station_raw[old_code_col].fillna("").astype(str).str.strip()
        aliases = stations.loc[old_codes.ne("")].copy()
        aliases["station_code"] = old_codes[old_codes.ne("")].to_numpy()
        stations = pd.concat([stations, aliases], ignore_index=True)

    position_columns = {normalise_text(col): col for col in position_raw.columns}
    position_code_col = position_columns.get("kodstanowiska")
    station_code_col = position_columns.get("kodstacji")
    if not position_code_col or not station_code_col:
        raise DataFormatError(
            "W arkuszu STANOWISKA nie rozpoznano kolumn „Kod stanowiska” i „Kod stacji”."
        )
    positions: dict[str, str] = {}
    for position_code, station_code in zip(position_raw[position_code_col], position_raw[station_code_col]):
        position_code = "" if pd.isna(position_code) else str(position_code).strip()
        station_code = "" if pd.isna(station_code) else str(station_code).strip()
        if position_code and station_code:
            positions.setdefault(position_code, station_code)
    return stations, positions


def build_gios_archive_dataset(
    archives: list[tuple[str, bytes]],
    metadata_content: bytes,
    *,
    name: str,
    selected_pollutants: list[str],
    negative_policy: str = "nan",
) -> Dataset:
    """Buduje Dataset z jednego lub kilku rocznych ZIP-ów pomiarowych GIOŚ."""
    if not archives:
        raise DataFormatError("Wybierz co najmniej jedno archiwum ZIP GIOŚ.")
    if not selected_pollutants:
        raise DataFormatError("Wybierz co najmniej jeden wskaźnik do wczytania.")
    unknown_selection = sorted(set(selected_pollutants) - set(POLLUTANTS))
    if unknown_selection:
        raise DataFormatError("Nieobsługiwane wskaźniki: " + ", ".join(unknown_selection) + ".")

    station_meta, position_lookup = _read_gios_metadata(metadata_content)
    selected = set(selected_pollutants)
    records: dict[str, list[object]] = {
        "timestamp": [],
        "station_code": [],
        "pollutant": [],
        "value": [],
        "unit": [],
        "averaging": [],
    }
    unsupported: Counter[str] = Counter()
    missing_values = 0
    bad_timestamps = 0
    unknown_positions: set[str] = set()
    position_mismatches: set[tuple[str, str]] = set()
    processed_files: list[str] = []
    series_found: set[str] = set()

    expected_labels = ("nr", "kodstacji", "wskaznik", "czasusredniania", "jednostka", "kodstanowiska")
    for archive_name, archive_content in archives:
        try:
            archive = zipfile.ZipFile(io.BytesIO(archive_content))
        except (OSError, zipfile.BadZipFile) as exc:
            raise DataFormatError(f"Plik „{archive_name}” nie jest poprawnym archiwum ZIP.") from exc

        with archive:
            members = sorted(
                (info for info in archive.infolist()
                 if not info.is_dir() and info.filename.lower().endswith(".xlsx")),
                key=lambda info: info.filename.lower(),
            )
            if not members:
                raise DataFormatError(f"Archiwum „{archive_name}” nie zawiera plików XLSX.")

            for member in members:
                try:
                    workbook_bytes = archive.read(member)
                    workbook = openpyxl.load_workbook(
                        io.BytesIO(workbook_bytes), read_only=True, data_only=True
                    )
                except (OSError, ValueError, KeyError, zipfile.BadZipFile,
                        openpyxl.utils.exceptions.InvalidFileException) as exc:
                    raise DataFormatError(
                        f"Nie udało się odczytać pliku „{member.filename}” z archiwum „{archive_name}”: {exc}"
                    ) from exc

                try:
                    if not workbook.worksheets:
                        raise DataFormatError(f"Plik „{member.filename}” nie zawiera arkusza.")
                    worksheet = workbook.worksheets[0]
                    rows = worksheet.iter_rows(values_only=True)
                    header_rows = [next(rows, None) for _ in range(6)]
                    if any(row is None for row in header_rows):
                        raise DataFormatError(f"Plik „{member.filename}” ma mniej niż 6 wierszy metadanych.")
                    raw_indicators = header_rows[2][1:] if len(header_rows[2]) > 1 else ()
                    supported_indicators = {
                        _GIOS_POLLUTANTS.get(normalise_text(value))
                        for value in raw_indicators if value is not None and str(value).strip()
                    }
                    supported_indicators.discard(None)
                    if not supported_indicators:
                        for value in raw_indicators:
                            if value is not None and str(value).strip():
                                if normalise_text(value) == "wskaznik":
                                    continue
                                unsupported[str(value).strip()] += 1
                        continue

                    actual_labels = tuple(normalise_text(row[0]) if row else "" for row in header_rows)
                    if actual_labels != expected_labels:
                        raise DataFormatError(
                            f"Nieoczekiwany układ nagłówka w „{member.filename}”. "
                            "Oczekiwano wierszy: Nr, Kod stacji, Wskaźnik, Czas uśredniania, Jednostka, Kod stanowiska."
                        )

                    station_codes = header_rows[1][1:]
                    pollutant_codes = header_rows[2][1:]
                    averaging_labels = header_rows[3][1:]
                    units = header_rows[4][1:]
                    position_codes = header_rows[5][1:]
                    column_count = max(
                        len(station_codes), len(pollutant_codes), len(averaging_labels),
                        len(units), len(position_codes),
                    )

                    def cell(values: tuple[object, ...], index: int) -> object:
                        return values[index] if index < len(values) else None

                    series: list[tuple[int, str, str, str, str]] = []
                    for column_index in range(column_count):
                        station_code = str(cell(station_codes, column_index) or "").strip()
                        if not station_code:
                            continue
                        raw_pollutant = str(cell(pollutant_codes, column_index) or "").strip()
                        pollutant = _GIOS_POLLUTANTS.get(normalise_text(raw_pollutant))
                        if pollutant is None:
                            if raw_pollutant:
                                unsupported[raw_pollutant] += 1
                            continue
                        if pollutant not in selected:
                            continue
                        averaging = str(cell(averaging_labels, column_index) or "").strip()
                        unit = str(cell(units, column_index) or "").strip()
                        position_code = str(cell(position_codes, column_index) or "").strip()
                        if not averaging or not unit or not position_code:
                            raise DataFormatError(
                                f"Brakuje czasu uśredniania, jednostki lub kodu stanowiska "
                                f"w serii „{station_code} / {raw_pollutant}” ({member.filename})."
                            )
                        known_station = position_lookup.get(position_code)
                        if known_station is None:
                            unknown_positions.add(position_code)
                        elif known_station != station_code:
                            position_mismatches.add((position_code, station_code))
                        series.append((column_index + 1, station_code, pollutant, averaging, unit))
                        series_found.add(pollutant)

                    if not series:
                        continue

                    file_has_data = False
                    for row in rows:
                        timestamp = cell(row, 0)
                        values = [cell(row, column_index) for column_index, *_ in series]
                        if timestamp is None or (isinstance(timestamp, str) and not timestamp.strip()):
                            bad_timestamps += sum(
                                value is not None and not pd.isna(value)
                                and not (isinstance(value, str) and not value.strip())
                                for value in values
                            )
                            continue
                        for (column_index, station_code, pollutant, averaging, unit), value in zip(series, values):
                            if value is None or pd.isna(value) or (isinstance(value, str) and not value.strip()):
                                missing_values += 1
                                continue
                            records["timestamp"].append(timestamp)
                            records["station_code"].append(station_code)
                            records["pollutant"].append(pollutant)
                            records["value"].append(value)
                            records["unit"].append(unit)
                            records["averaging"].append(averaging)
                            file_has_data = True
                    if file_has_data:
                        processed_files.append(f"{archive_name}:{member.filename}")
                finally:
                    workbook.close()

    if not records["value"]:
        raise DataFormatError(
            "Nie znaleziono pomiarów dla wybranych wskaźników. Sprawdź archiwa i wybór wskaźników."
        )

    raw = pd.DataFrame(records)
    mapping = ColumnMapping(
        layout=LAYOUT_LONG,
        time_col="timestamp",
        station_col="station_code",
        pollutant_col="pollutant",
        value_col="value",
        unit_col="unit",
        averaging_col="averaging",
    )
    dataset = build_dataset(
        [raw],
        mapping,
        name=name,
        source_kind=SOURCE_GIOS_ARCHIVE,
        station_meta=station_meta,
        negative_policy=negative_policy,
    )
    dataset.report.n_missing_value += missing_values
    dataset.report.n_bad_time += bad_timestamps
    dataset.report.rows_read += missing_values + bad_timestamps
    dataset.report.values_read += missing_values + bad_timestamps
    dataset.attribution = (
        f"Roczne archiwa ({', '.join(archive_name for archive_name, _ in archives)}) "
        f"— {SOURCE_GIOS_NAME}. "
        "Czas w danych GIOŚ jest udostępniany w CET; nie przeliczano znaczników bez strefy."
    )
    dataset.report.notes.append(
        f"Wczytano wskaźniki: {', '.join(sorted(series_found))}; pliki serii: {len(processed_files)}."
    )
    if missing_values:
        dataset.report.notes.append(
            f"Puste komórki w wybranych seriach (pominięte, nie zastąpione zerem): {missing_values:,}."
            .replace(",", " ")
        )
    if unsupported:
        skipped_count = sum(unsupported.values())
        examples = ", ".join(sorted(unsupported)[:8])
        dataset.report.warnings.append(
            f"Pominięto {len(unsupported)} typów wskaźników spoza zakresu aplikacji "
            f"({skipped_count} serii; np. {examples})."
        )
    if series_found != selected:
        absent = sorted(selected - series_found)
        dataset.report.notes.append(
            "W wybranych archiwach nie znaleziono serii dla: " + ", ".join(absent) + "."
        )
    if unknown_positions:
        dataset.report.warnings.append(
            f"{len(unknown_positions)} kodów stanowisk pomiarowych nie występuje w arkuszu STANOWISKA "
            f"(np. {', '.join(sorted(unknown_positions)[:3])})."
        )
    if position_mismatches:
        examples = ", ".join(f"{position} → {station}" for position, station in sorted(position_mismatches)[:3])
        dataset.report.warnings.append(
            f"{len(position_mismatches)} kodów stanowisk wskazuje inny kod stacji niż arkusz STANOWISKA "
            f"(np. {examples})."
        )
    if bad_timestamps:
        dataset.report.warnings.append(
            f"Pominięto {bad_timestamps} pomiarów bez daty/czasu."
        )
    return dataset


def build_demo_dataset() -> Dataset:
    """Dane demonstracyjne - zawsze oznaczone jako syntetyczne."""
    meas, stations = demo_data.generate_demo_data()
    report = ValidationReport(rows_read=len(meas), values_read=len(meas), rows_valid=len(meas))
    report.notes.append(
        f"{DEMO_LABEL}: dane wygenerowane algorytmicznie. Nie są to pomiary GIOŚ. "
        "Współrzędne to środki miast (punkty umowne), a nie lokalizacje rzeczywistych stacji."
    )
    return Dataset(
        dataset_id="demo-v1-seed42",
        name=DEMO_LABEL,
        source_kind=SOURCE_DEMO,
        is_synthetic=True,
        measurements=finalise_measurements(meas),
        stations=stations,
        report=report,
        attribution=DEMO_LABEL + " - wygenerowane na potrzeby prezentacji interfejsu.",
    )


# --- Adapter GIOŚ API (szkielet) ----------------------------------------------
class DataAdapter(ABC):
    """Interfejs źródła danych. Każdy adapter zwraca dane w ujednoliconym schemacie długim.

    Schemat zwracanych tabel jest taki sam jak w modules.data_processing:
    pomiary: timestamp, station_code, pollutant, value, unit, averaging
    stacje:  station_code, station_name, city, voivodeship, latitude, longitude
    """

    name: str = "adapter"

    @abstractmethod
    def fetch_stations(self) -> pd.DataFrame:
        """Zwraca tabelę stacji."""

    @abstractmethod
    def fetch_measurements(
        self, station_codes: list[str], pollutants: list[str], start: date, end: date
    ) -> pd.DataFrame:
        """Zwraca pomiary w formacie długim dla podanych stacji, wskaźników i zakresu dat."""


class GiosApiAdapter(DataAdapter):
    """Adapter API GIOŚ - NIEZAIMPLEMENTOWANY (celowo).

    Dlaczego szkielet: według komunikatu GIOŚ z marca 2025 starsze usługi (ścieżki
    /pjp-api/rest/...) miały zostać wycofane po 30.06.2025, a nowe API jest opisane w
    dokumentacji OpenAPI (Swagger UI) serwisu api.gios.gov.pl. Schematu odpowiedzi nie
    zgadujemy. Do implementacji potrzebne są: (1) aktualna specyfikacja OpenAPI,
    (2) przykładowe odpowiedzi JSON dla listy stacji, listy stanowisk i pomiarów,
    (3) informacja o głębokości historii dostępnej przez API.
    """

    name = "GIOŚ API"

    def fetch_stations(self) -> pd.DataFrame:
        raise NotImplementedError("Adapter GIOŚ API nie jest jeszcze zaimplementowany (brak zweryfikowanego schematu).")

    def fetch_measurements(self, station_codes, pollutants, start, end) -> pd.DataFrame:
        raise NotImplementedError("Adapter GIOŚ API nie jest jeszcze zaimplementowany (brak zweryfikowanego schematu).")

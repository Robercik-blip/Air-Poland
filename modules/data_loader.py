"""Wczytywanie danych: pliki lokalne, wgrywane przez interfejs, dane demo oraz adapter GIOŚ API.

Struktura plików archiwalnych GIOŚ nie jest tu zakładana. Zamiast wymyślać "oficjalny"
schemat, loader:
  1. wczytuje plik jako tekst (wykrywa kodowanie, separator, ewentualny wiersz nagłówka),
  2. automatycznie PROPONUJE mapowanie kolumn (słowniki synonimów PL/EN),
  3. pozwala użytkownikowi je poprawić w interfejsie,
  4. czyści dane wg jednego, jawnego zestawu reguł (modules.data_processing).
"""
from __future__ import annotations

import csv
import io
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

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
SOURCE_LABELS = {
    SOURCE_LOCAL: "Plik lokalny (data/raw)",
    SOURCE_UPLOAD: "Plik wgrany przez użytkownika",
    SOURCE_DEMO: DEMO_LABEL,
    SOURCE_API: "GIOŚ API",
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

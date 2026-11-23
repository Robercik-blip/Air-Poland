"""Stałe konfiguracyjne, metadane wskaźników i paleta kolorów aplikacji AIR POLAND."""
from __future__ import annotations

from pathlib import Path

# --- Ścieżki -----------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "assets"
RAW_DATA_DIR = BASE_DIR / "data" / "raw"
PROCESSED_DATA_DIR = BASE_DIR / "data" / "processed"

# --- Identyfikacja aplikacji i źródeł ----------------------------------------
APP_NAME = "AIR POLAND"
APP_SUBTITLE = "Analityka jakości powietrza w Polsce"

SOURCE_GIOS_NAME = "Główny Inspektorat Ochrony Środowiska (GIOŚ), Państwowy Monitoring Środowiska"
SOURCE_GIOS_ARCHIVES = "https://powietrze.gios.gov.pl/pjp/archives"
SOURCE_GIOS_API = "https://powietrze.gios.gov.pl/pjp/content/api"
DEMO_LABEL = "DEMONSTRACYJNE DANE SYNTETYCZNE"

# --- Wskaźniki zanieczyszczeń ------------------------------------------------
# default_unit służy WYŁĄCZNIE do wyświetlania, gdy plik źródłowy nie zawiera
# informacji o jednostce. Wartość jest wtedy oznaczana jako "założona".
POLLUTANTS: dict[str, dict[str, str]] = {
    "PM10": {"pretty": "PM10", "name": "Pył zawieszony PM10", "default_unit": "µg/m³"},
    "PM2.5": {"pretty": "PM2.5", "name": "Pył zawieszony PM2.5", "default_unit": "µg/m³"},
    "NO2": {"pretty": "NO₂", "name": "Dwutlenek azotu", "default_unit": "µg/m³"},
    "SO2": {"pretty": "SO₂", "name": "Dwutlenek siarki", "default_unit": "µg/m³"},
    "O3": {"pretty": "O₃", "name": "Ozon", "default_unit": "µg/m³"},
    "CO": {"pretty": "CO", "name": "Tlenek węgla", "default_unit": "mg/m³"},
    "C6H6": {"pretty": "C₆H₆", "name": "Benzen", "default_unit": "µg/m³"},
}
POLLUTANT_ORDER = list(POLLUTANTS.keys())

# --- Poziomy odniesienia (orientacyjne) --------------------------------------
# Wartości służą do kolorowania wykresów i map oraz jako linie pomocnicze.
# UWAGA: przed powołaniem się na nie w pracy należy zweryfikować je w aktach
# prawnych (dyrektywa 2008/50/WE oraz wytyczne WHO z 2021 r.).
REFERENCE_LEVELS: dict[str, dict[str, float | None]] = {
    "PM10": {"who_annual": 15, "eu_annual": 40, "who_daily": 45, "eu_daily": 50},
    "PM2.5": {"who_annual": 5, "eu_annual": 25, "who_daily": 15, "eu_daily": None},
    "NO2": {"who_annual": 10, "eu_annual": 40, "who_daily": 25, "eu_daily": None},
    "SO2": {"who_annual": None, "eu_annual": None, "who_daily": 40, "eu_daily": 125},
    "C6H6": {"who_annual": None, "eu_annual": 5, "who_daily": None, "eu_daily": None},
}
REFERENCE_NOTE = (
    "Poziomy odniesienia są orientacyjne (wytyczne WHO 2021, dyrektywa 2008/50/WE) "
    "i dotyczą wartości w jednostce µg/m³. Średnia z dowolnego okresu nie jest "
    "równoważna rocznej wartości dopuszczalnej."
)

# --- Granice Polski (przybliżony prostokąt) do walidacji współrzędnych --------
POLAND_BBOX = {"lat_min": 48.9, "lat_max": 55.0, "lon_min": 14.0, "lon_max": 24.3}

# --- Parametry jakości danych -------------------------------------------------
DEFAULT_MIN_COVERAGE = 0.75  # minimalny udział ważnych pomiarów w okresie agregacji

# --- Paleta kolorów (ciemny motyw) -------------------------------------------
COLORS = {
    "bg": "#0d1117",
    "panel": "#131920",
    "panel2": "#18212b",
    "border": "#243040",
    "text": "#e6edf3",
    "muted": "#8b98a9",
    "faint": "#5d6b7c",
    "teal": "#2dd4bf",
    "blue": "#38bdf8",
    "green": "#34d399",
    "indigo": "#818cf8",
    "amber": "#f59e0b",
    "red": "#ef4444",
    "demo": "#a78bfa",
}

# Kategorie stacji na wykresach: chłodne barwy; czerwień i pomarańcz zarezerwowane
# dla podwyższonych stężeń.
SERIES_PALETTE = [
    "#2dd4bf", "#38bdf8", "#818cf8", "#34d399", "#22d3ee",
    "#60a5fa", "#a3e635", "#c4b5fd", "#94a3b8", "#67e8f9",
]

LEVEL_COLORS = {
    "ok": COLORS["teal"],
    "elevated": COLORS["amber"],
    "high": COLORS["red"],
    "unknown": COLORS["blue"],
}
LEVEL_LABELS = {
    "ok": "poniżej poziomu WHO",
    "elevated": "powyżej poziomu WHO",
    "high": "powyżej poziomu UE",
    "unknown": "brak poziomu odniesienia",
}

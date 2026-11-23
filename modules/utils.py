"""Funkcje pomocnicze: formatowanie po polsku, normalizacja tekstu, poziomy stężeń."""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date

import pandas as pd

from modules.config import COLORS, LEVEL_COLORS, POLLUTANTS, REFERENCE_LEVELS, SERIES_PALETTE

_LOGGING_READY = False

PL_MONTHS_SHORT = ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"]
PL_MONTHS = [
    "styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec",
    "lipiec", "sierpień", "wrzesień", "październik", "listopad", "grudzień",
]
SEASON_ORDER = ["Zima", "Wiosna", "Lato", "Jesień"]
SEASON_MONTHS = {
    "Zima": [12, 1, 2],
    "Wiosna": [3, 4, 5],
    "Lato": [6, 7, 8],
    "Jesień": [9, 10, 11],
}

_PL_TRANSLATION = str.maketrans(
    {"ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n", "ó": "o", "ś": "s", "ź": "z", "ż": "z"}
)


def get_logger(name: str) -> logging.Logger:
    """Zwraca logger; przy pierwszym wywołaniu konfiguruje format logów."""
    global _LOGGING_READY
    if not _LOGGING_READY:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        )
        _LOGGING_READY = True
    return logging.getLogger(name)


def normalise_text(value: object) -> str:
    """Normalizuje tekst do porównań: małe litery, bez polskich znaków i znaków specjalnych.

    Służy do dopasowywania nazw kolumn (np. "Kod stacji" -> "kodstacji").
    """
    text = str(value).strip().lower().translate(_PL_TRANSLATION)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", text)


def pollutant_label(code: str) -> str:
    """Ładna etykieta wskaźnika (np. NO2 -> NO₂)."""
    return POLLUTANTS.get(code, {}).get("pretty", code)


def fmt_number(value: object, decimals: int = 1) -> str:
    """Liczba w polskim formacie: spacja jako separator tysięcy, przecinek dziesiętny."""
    if value is None or pd.isna(value):
        return "—"
    text = f"{float(value):,.{decimals}f}"
    return text.replace(",", " ").replace(".", ",")


def fmt_int(value: object) -> str:
    """Liczba całkowita z separatorem tysięcy."""
    if value is None or pd.isna(value):
        return "—"
    return f"{int(value):,}".replace(",", " ")


def fmt_date(value: date | pd.Timestamp | None) -> str:
    """Data w formacie DD.MM.RRRR."""
    if value is None or pd.isna(value):
        return "—"
    return pd.Timestamp(value).strftime("%d.%m.%Y")


def fmt_percent(value: object, decimals: int = 0) -> str:
    """Ułamek jako procent, np. 0.753 -> '75%'."""
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value) * 100:.{decimals}f}".replace(".", ",") + "%"


def hex_to_rgba(hex_color: str, alpha: float) -> str:
    """Konwertuje kolor #rrggbb na rgba() z zadaną przezroczystością."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def classify_level(value: float | None, pollutant: str) -> str:
    """Klasyfikuje stężenie względem poziomów odniesienia (roczne WHO / UE).

    Zwraca: 'ok', 'elevated', 'high' lub 'unknown' (brak poziomu odniesienia).
    """
    if value is None or pd.isna(value):
        return "unknown"
    ref = REFERENCE_LEVELS.get(pollutant)
    if not ref:
        return "unknown"
    who, eu = ref.get("who_annual"), ref.get("eu_annual")
    if who is None and eu is None:
        return "unknown"
    if eu is not None and value > eu:
        return "high"
    if who is not None and value > who:
        return "elevated"
    return "ok"


def level_color(value: float | None, pollutant: str) -> str:
    """Kolor odpowiadający klasie poziomu stężenia."""
    return LEVEL_COLORS[classify_level(value, pollutant)]


def station_color_map(all_codes: list[str]) -> dict[str, str]:
    """Stałe przypisanie kolorów do stacji (spójne między wykresami)."""
    return {code: SERIES_PALETTE[i % len(SERIES_PALETTE)] for i, code in enumerate(sorted(all_codes))}


def muted_color() -> str:
    return COLORS["muted"]

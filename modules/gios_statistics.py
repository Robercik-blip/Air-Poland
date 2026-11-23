"""Parser annual GIOŚ statistics workbooks without converting them to measurements."""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from zipfile import BadZipFile

import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from modules.data_processing import DataFormatError
from modules.utils import normalise_text

_IDENTIFIERS = {
    "year": {"rok"},
    "voivodeship": {"wojewodztwo"},
    "zone_code": {"kodstrefy"},
    "zone_name": {"nazwastrefy"},
    "station_code": {"kodstacji"},
    "pollutant": {"wskaznik"},
    "averaging": {"czasusredniania"},
}
_MISSING_VALUES = {"", "-", "--", "na", "n/a", "nan", "none", "null", "brak", "b/d", "nd"}
_UNIT_LABELS = {"(-)": ""}


@dataclass
class GiosAnnualStatistics:
    """Yearly station statistics, kept separate from the measurement Dataset."""

    data: pd.DataFrame
    metric_units: dict[tuple[str, str], str]
    metric_descriptions: dict[str, str]
    skipped_rows: int = 0


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _number(value: object) -> float:
    if value is None or isinstance(value, bool):
        return math.nan
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else math.nan

    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if text.lower() in _MISSING_VALUES:
        return math.nan
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        number = float(text)
        return number if math.isfinite(number) else math.nan
    except ValueError:
        return math.nan


def _definitions(workbook) -> dict[str, str]:
    if "OPIS" not in workbook.sheetnames:
        return {}
    descriptions: dict[str, str] = {}
    for row in workbook["OPIS"].iter_rows(min_row=2, values_only=True):
        if len(row) < 3:
            continue
        metric, description = _text(row[1]), _text(row[2])
        if metric and description:
            descriptions.setdefault(normalise_text(metric), description)
    return descriptions


def read_gios_annual_statistics(content: bytes) -> GiosAnnualStatistics:
    """Read the GIOŚ ``Statystyki z lat`` workbook into annual station rows."""
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (BadZipFile, InvalidFileException, OSError, ValueError) as exc:
        raise DataFormatError("Nie można odczytać pliku. Wybierz skoroszyt Excel GIOŚ (.xlsx).") from exc

    records: list[dict[str, object]] = []
    metric_units: dict[tuple[str, str], str] = {}
    descriptions = _definitions(workbook)
    skipped_rows = 0
    supported_sheets = 0

    try:
        for sheet in workbook.worksheets:
            if normalise_text(sheet.title) == "opis":
                continue
            row_iter = sheet.iter_rows(values_only=True)
            headers_row = next(row_iter, None)
            units_row = next(row_iter, None)
            if headers_row is None or units_row is None:
                continue

            headers = [_text(value) for value in headers_row]
            positions: dict[str, int] = {}
            for index, header in enumerate(headers):
                normalised = normalise_text(header)
                for field, aliases in _IDENTIFIERS.items():
                    if normalised in aliases:
                        if field in positions:
                            raise DataFormatError(
                                f"Arkusz „{sheet.title}” zawiera niejednoznaczną kolumnę „{header}”."
                            )
                        positions[field] = index

            missing = [field for field in _IDENTIFIERS if field not in positions]
            if missing:
                continue

            metric_columns = [
                (index, header)
                for index, header in enumerate(headers)
                if header and index not in positions.values()
            ]
            if not metric_columns:
                continue
            supported_sheets += 1

            for index, metric in metric_columns:
                unit = _text(units_row[index] if index < len(units_row) else "")
                metric_units[(sheet.title, metric)] = _UNIT_LABELS.get(unit, unit.strip("[]"))

            for row in row_iter:
                if row is None or not any(value is not None for value in row):
                    continue
                year_value = _number(row[positions["year"]] if positions["year"] < len(row) else None)
                if not math.isfinite(year_value) or not year_value.is_integer():
                    skipped_rows += 1
                    continue
                station_code = _text(row[positions["station_code"]] if positions["station_code"] < len(row) else None)
                pollutant = _text(row[positions["pollutant"]] if positions["pollutant"] < len(row) else None)
                if not station_code or not pollutant:
                    skipped_rows += 1
                    continue

                record: dict[str, object] = {
                    "year": int(year_value),
                    "voivodeship": _text(row[positions["voivodeship"]] if positions["voivodeship"] < len(row) else None),
                    "zone_code": _text(row[positions["zone_code"]] if positions["zone_code"] < len(row) else None),
                    "zone_name": _text(row[positions["zone_name"]] if positions["zone_name"] < len(row) else None),
                    "station_code": station_code,
                    "pollutant": pollutant,
                    "averaging": _text(row[positions["averaging"]] if positions["averaging"] < len(row) else None),
                    "source_sheet": sheet.title,
                }
                for index, metric in metric_columns:
                    record[metric] = _number(row[index] if index < len(row) else None)
                records.append(record)
    finally:
        workbook.close()

    if supported_sheets == 0 or not records:
        raise DataFormatError(
            "W skoroszycie nie znaleziono arkuszy ze statystykami GIOŚ "
            "(wymagane kolumny m.in. Rok, Kod stacji, Wskaźnik i Czas uśredniania)."
        )

    return GiosAnnualStatistics(
        data=pd.DataFrame.from_records(records),
        metric_units=metric_units,
        metric_descriptions=descriptions,
        skipped_rows=skipped_rows,
    )

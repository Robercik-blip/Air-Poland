from __future__ import annotations

from io import BytesIO

import numpy as np
import pytest
from openpyxl import Workbook

from modules.data_processing import DataFormatError
from modules.gios_statistics import read_gios_annual_statistics


def _statistics_workbook() -> bytes:
    workbook = Workbook()
    description = workbook.active
    description.title = "OPIS"
    description.append(["Wskaźnik", "Kod statystyki / pola", "Opis"])
    description.append(["Wszystkie", "Średnia", "Wartość średnia roczna stężenia."])

    sheet = workbook.create_sheet("PM10")
    sheet.append([
        "Rok", "Województwo", "Kod strefy", "Nazwa strefy", "Kod stacji", "Wskaźnik",
        "Czas uśredniania", "Średnia", "Liczba pomiarów", "Kompletność ",
    ])
    sheet.append(["(-)", "(-)", "(-)", "(-)", "(-)", "(-)", "(-)", "[µg/m3]", "(-)", "[%]"])
    sheet.append([2000, "małopolskie", "PL1201", "Aglomeracja Krakowska", "MpKrakAlKras",
                  "PM10", "24g", 12.5, 300, 85.2])
    sheet.append([2002, "małopolskie", "PL1201", "Aglomeracja Krakowska", "MpKrakAlKras",
                  "PM10", "24g", "13,5", 280, 80])
    sheet.append([2003, "małopolskie", "PL1201", "Aglomeracja Krakowska", "MpKrakAlKras",
                  "PM10", "24g", None, 250, None])
    return _save(workbook)


def _save(workbook: Workbook) -> bytes:
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_reads_gios_yearly_rows_units_and_descriptions_without_filling_gaps():
    result = read_gios_annual_statistics(_statistics_workbook())

    assert result.data["year"].tolist() == [2000, 2002, 2003]
    assert result.data["pollutant"].tolist() == ["PM10", "PM10", "PM10"]
    assert result.data["Średnia"].iloc[:2].tolist() == [12.5, 13.5]
    assert np.isnan(result.data.loc[2, "Kompletność"])
    assert result.metric_units[("PM10", "Średnia")] == "µg/m3"
    assert result.metric_descriptions["srednia"] == "Wartość średnia roczna stężenia."
    assert result.skipped_rows == 0


def test_rejects_workbooks_without_gios_annual_sheets():
    workbook = Workbook()
    workbook.active.title = "Not GIOS"

    with pytest.raises(DataFormatError, match="nie znaleziono arkuszy"):
        read_gios_annual_statistics(_save(workbook))


def test_rejects_non_excel_bytes():
    with pytest.raises(DataFormatError, match="skoroszyt Excel"):
        read_gios_annual_statistics(b"not an xlsx")

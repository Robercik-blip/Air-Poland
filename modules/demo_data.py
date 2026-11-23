"""Generator DEMONSTRACYJNYCH DANYCH SYNTETYCZNYCH.

Dane służą wyłącznie do prezentacji interfejsu i testowania analiz. NIE SĄ to oficjalne
pomiary GIOŚ i nie wolno ich prezentować jako takich.

Model (celowo prosty, ale z cechami typowymi dla jakości powietrza w Polsce):
* sezonowość: wyższe PM10/PM2.5/NO2/SO2 w sezonie grzewczym, wyższy O3 latem,
* cykl dobowy i tygodniowy (szczyty komunikacyjne),
* wspólny składnik regionalny (pogoda) - stacje są ze sobą skorelowane,
* O3 jest ujemnie skorelowany z NO2,
* łagodny trend spadkowy,
* braki danych: losowe godziny, dłuższe awarie, późniejszy start niektórych serii,
* ostatni rok jest niepełny (do sierpnia), aby pokazać ostrzeżenia o pokryciu.

Współrzędne to ŚRODKI MIAST (punkty umowne), a nie lokalizacje rzeczywistych stacji.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.signal import lfilter

DEMO_START = "2021-01-01 00:00"
DEMO_END = "2025-08-31 23:00"

# kod, nazwa, miasto, województwo, szerokość, długość (środek miasta), poziom miejski (mnożnik)
_STATIONS = [
    ("DEMO-KR01", "DEMO Kraków (punkt umowny)", "Kraków", "małopolskie", 50.0647, 19.9450, 1.30),
    ("DEMO-KT01", "DEMO Katowice (punkt umowny)", "Katowice", "śląskie", 50.2649, 19.0238, 1.25),
    ("DEMO-WA01", "DEMO Warszawa (punkt umowny)", "Warszawa", "mazowieckie", 52.2297, 21.0122, 1.00),
    ("DEMO-WR01", "DEMO Wrocław (punkt umowny)", "Wrocław", "dolnośląskie", 51.1079, 17.0385, 1.05),
    ("DEMO-GD01", "DEMO Gdańsk (punkt umowny)", "Gdańsk", "pomorskie", 54.3520, 18.6466, 0.80),
    ("DEMO-LU01", "DEMO Lublin (punkt umowny)", "Lublin", "lubelskie", 51.2465, 22.5684, 0.90),
    ("DEMO-PO01", "DEMO Poznań (punkt umowny)", "Poznań", "wielkopolskie", 52.4064, 16.9252, 0.95),
]

# wskaźnik -> (średnia roczna µg/m³ dla mnożnika 1.0, amplituda sezonowa, wpływ pogody, szum lokalny)
_PARAMS = {
    "PM10": (32.0, 0.55, 0.45, 0.22),
    "PM2.5": (23.0, 0.65, 0.45, 0.22),
    "NO2": (24.0, 0.30, 0.35, 0.20),
    "SO2": (7.0, 0.55, 0.40, 0.25),
    "O3": (52.0, -0.45, -0.25, 0.12),
}

# Które stacje mierzą które wskaźniki (nie każda stacja mierzy wszystko).
_COVERAGE = {
    "DEMO-KR01": ["PM10", "PM2.5", "NO2", "SO2", "O3"],
    "DEMO-KT01": ["PM10", "PM2.5", "NO2", "SO2"],
    "DEMO-WA01": ["PM10", "PM2.5", "NO2", "O3"],
    "DEMO-WR01": ["PM10", "PM2.5", "NO2", "O3"],
    "DEMO-GD01": ["PM10", "NO2", "O3"],
    "DEMO-LU01": ["PM10", "PM2.5", "NO2", "SO2"],
    "DEMO-PO01": ["PM10", "NO2", "O3", "PM2.5"],
}
# Późniejszy start wybranych serii (nierówne pokrycie w czasie).
_LATE_START = {("DEMO-LU01", "PM10"): "2022-03-01", ("DEMO-PO01", "PM2.5"): "2023-01-01"}


def _ar1(rng: np.random.Generator, n: int, phi: float) -> np.ndarray:
    """Szereg AR(1) o jednostkowej wariancji."""
    noise = rng.standard_normal(n) * np.sqrt(1 - phi**2)
    return lfilter([1.0], [1.0, -phi], noise)


def _diurnal(hour: np.ndarray, pollutant: str) -> np.ndarray:
    if pollutant == "O3":
        return 1 + 0.45 * np.sin((hour - 9) / 24 * 2 * np.pi)
    if pollutant == "NO2":
        return 1 + 0.35 * (np.exp(-((hour - 8) ** 2) / 8) + np.exp(-((hour - 18) ** 2) / 10)) - 0.1
    return 1 + 0.18 * (np.exp(-((hour - 8) ** 2) / 10) + np.exp(-((hour - 20) ** 2) / 12)) - 0.05


def generate_demo_data(seed: int = 42) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Zwraca (pomiary w formacie długim, tabela stacji) dla danych demonstracyjnych."""
    rng = np.random.default_rng(seed)
    times = pd.date_range(DEMO_START, DEMO_END, freq="h")
    n = len(times)
    doy = times.day_of_year.to_numpy()
    hour = times.hour.to_numpy()
    dow = times.day_of_week.to_numpy()
    years = (times.year.to_numpy() - 2021) + doy / 365.25

    regional = _ar1(rng, n, 0.985)  # wspólny "składnik pogodowy"
    weekly = np.where(dow >= 5, 0.92, 1.0)

    frames: list[pd.DataFrame] = []
    for code, _name, _city, _voiv, _lat, _lon, urban in _STATIONS:
        for pollutant in _COVERAGE[code]:
            base, season_amp, weather, local_sd = _PARAMS[pollutant]
            seasonal = 1 + season_amp * np.cos(2 * np.pi * (doy - 15) / 365.25)
            trend = 0.97 ** years
            local = _ar1(rng, n, 0.95)
            station_factor = urban if pollutant != "O3" else 2 - urban * 0.6
            log_noise = weather * regional + local_sd * local
            value = (
                base * station_factor * seasonal * trend * _diurnal(hour, pollutant) * weekly
                * np.exp(log_noise - 0.5 * (weather**2 + local_sd**2))
            )
            if pollutant == "PM2.5":
                value *= 0.95
            value = np.round(np.clip(value, 0.3, None), 1)

            valid = np.ones(n, dtype=bool)
            valid &= rng.random(n) > 0.012  # pojedyncze brakujące godziny
            for _ in range(rng.integers(2, 5)):  # awarie przyrządu
                length = int(rng.integers(6, 24 * 10))
                start = int(rng.integers(0, n - length))
                valid[start : start + length] = False
            late = _LATE_START.get((code, pollutant))
            if late:
                valid &= times >= pd.Timestamp(late)

            frames.append(
                pd.DataFrame({
                    "timestamp": times[valid],
                    "station_code": code,
                    "pollutant": pollutant,
                    "value": value[valid],
                    "unit": "µg/m³",
                    "averaging": "1h",
                })
            )

    measurements = pd.concat(frames, ignore_index=True)
    stations = pd.DataFrame(
        [(c, nm, city, voiv, lat, lon) for c, nm, city, voiv, lat, lon, _ in _STATIONS],
        columns=["station_code", "station_name", "city", "voivodeship", "latitude", "longitude"],
    )
    return measurements, stations

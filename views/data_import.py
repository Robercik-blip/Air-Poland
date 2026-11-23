"""Źródło danych i import: pliki lokalne, wgrywanie CSV, mapowanie kolumn, raport walidacji."""
from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from modules import services, ui
from modules.config import (
    DEMO_LABEL, POLLUTANT_ORDER, RAW_DATA_DIR, SOURCE_GIOS_API, SOURCE_GIOS_ARCHIVES, SOURCE_GIOS_NAME,
)
from modules.data_loader import (
    SOURCE_DEMO, SOURCE_LABELS, SOURCE_LOCAL, SOURCE_UPLOAD, build_dataset, detect_mapping,
    list_local_files, parse_station_metadata, read_table,
)
from modules.data_processing import (
    LAYOUT_LABELS, LAYOUT_LONG, LAYOUT_POLLUTANT_COLUMNS, LAYOUT_STATION_COLUMNS, ColumnMapping, DataFormatError,
)
from modules.filters import reset_filters
from modules.utils import fmt_date, fmt_int, get_logger, pollutant_label

logger = get_logger("air_poland.import")
NONE = "— brak —"

ds = services.current_dataset()
ui.page_header(
    "Źródło danych i import",
    "Wczytaj własne pliki CSV lub XLSX (np. z archiwum GIOŚ), wskaż znaczenie kolumn i sprawdź raport walidacji. "
    "Struktura plików nie jest zakładana – aplikacja proponuje mapowanie, a Ty je potwierdzasz.",
    ds,
)
flash = st.session_state.pop("import_flash", None)
if flash:
    ui.callout(flash, "ok")


@st.cache_data(show_spinner=False, max_entries=8)
def _read_cached(content: bytes, name: str, header_row: int) -> pd.DataFrame:
    return read_table(content, name, header_row)


def _opt(value: str | None) -> str | None:
    return None if value in (None, NONE) else value


def _select(col, label: str, columns: list[str], default: str | None, key: str, help: str | None = None) -> str | None:
    options = [NONE] + columns
    idx = options.index(default) if default in options else 0
    return _opt(col.selectbox(label, options, index=idx, key=key, help=help))


tab_load, tab_report, tab_help = st.tabs(["Wczytaj dane", "Raport walidacji", "Format plików i GIOŚ API"])

# --- Wczytywanie ----------------------------------------------------------------------
with tab_load:
    mode = st.radio(
        "Źródło danych", ["Dane demonstracyjne", "Plik lokalny (data/raw)", "Wgraj plik z komputera"],
        horizontal=True, key="imp_mode",
    )

    if mode == "Dane demonstracyjne":
        ui.callout(f"<b>{DEMO_LABEL}</b> – dane wygenerowane algorytmicznie wyłącznie do prezentacji interfejsu. "
                   "Nie są to pomiary GIOŚ.", "demo")
        if st.button("Użyj danych demonstracyjnych", type="primary", key="imp_use_demo"):
            st.session_state["dataset"] = services.demo_dataset()
            reset_filters()
            st.session_state["import_flash"] = "Aktywny zbiór: dane demonstracyjne."
            st.rerun()
        st.stop()

    files: list[tuple[str, bytes]] = []
    source_kind = SOURCE_UPLOAD
    if mode.startswith("Plik lokalny"):
        source_kind = SOURCE_LOCAL
        local = list_local_files()
        if not local:
            ui.callout(f"Katalog <code>{RAW_DATA_DIR}</code> jest pusty. Skopiuj tam pliki CSV/XLSX i odśwież stronę.", "info")
            st.stop()
        chosen = st.multiselect("Pliki z katalogu data/raw", [p.name for p in local], key="imp_local")
        files = [(n, (RAW_DATA_DIR / n).read_bytes()) for n in chosen]
    else:
        uploaded = st.file_uploader("Pliki CSV / XLSX (można wskazać kilka plików o tym samym układzie kolumn)",
                                    type=["csv", "txt", "xlsx", "xls"], accept_multiple_files=True, key="imp_upload")
        files = [(f.name, f.getvalue()) for f in uploaded or []]
    if not files:
        st.info("Wybierz co najmniej jeden plik.")
        st.stop()

    header_row = st.number_input("Numer wiersza nagłówka (0 = pierwszy wiersz pliku)", 0, 50, 0, key="imp_header",
                                 help="Jeśli plik zaczyna się od wierszy opisowych, wskaż wiersz z nazwami kolumn.")
    frames: list[pd.DataFrame] = []
    try:
        for name, content in files:
            frames.append(_read_cached(content, name, int(header_row)))
    except DataFormatError as exc:
        st.error(str(exc))
        st.stop()

    first_name = files[0][0]
    columns = list(frames[0].columns)
    st.markdown(f"**Podgląd:** {first_name} · {fmt_int(len(frames[0]))} wierszy · {len(columns)} kolumn")
    st.dataframe(frames[0].head(8), width="stretch", hide_index=True)

    sig = hashlib.md5(("|".join(n for n, _ in files) + "|".join(columns) + str(header_row)).encode()).hexdigest()[:8]
    guess, notes = detect_mapping(columns, first_name)
    for n in notes:
        st.caption("• " + n)

    with st.container(border=True):
        ui.section_title("Mapowanie kolumn", "Wskaż, które kolumny pliku zawierają czas, stację, wskaźnik i wartość.")
        layouts = list(LAYOUT_LABELS)
        layout = st.selectbox("Układ danych", layouts, index=layouts.index(guess.layout),
                              format_func=LAYOUT_LABELS.get, key=f"imp_layout_{sig}")
        k = lambda s: f"imp_{s}_{sig}"  # noqa: E731
        m = ColumnMapping(layout=layout)
        a, b, c = st.columns(3)
        m.time_col = _select(a, "Kolumna z czasem pomiaru", columns, guess.time_col, k("time"))
        if layout == LAYOUT_LONG:
            m.station_col = _select(b, "Kolumna ze stacją (kod)", columns, guess.station_col, k("station"))
            m.pollutant_col = _select(c, "Kolumna ze wskaźnikiem", columns, guess.pollutant_col, k("pollutant"))
            m.value_col = _select(a, "Kolumna z wartością", columns, guess.value_col, k("value"))
        elif layout == LAYOUT_POLLUTANT_COLUMNS:
            m.station_col = _select(b, "Kolumna ze stacją (kod)", columns, guess.station_col, k("station"))
            if not m.station_col:
                m.fixed_station = c.text_input("Kod stacji dla całego pliku", key=k("fixed_station")) or None
            m.pollutant_cols = st.multiselect("Kolumny wskaźników", columns, default=guess.pollutant_cols, key=k("pcols"))
        else:
            m.station_cols = st.multiselect("Kolumny stacji (każda = osobna stacja)", columns,
                                            default=guess.station_cols, key=k("scols"))
            default_p = guess.fixed_pollutant if guess.fixed_pollutant in POLLUTANT_ORDER else None
            pick = st.selectbox("Wskaźnik, którego dotyczy plik", [NONE] + POLLUTANT_ORDER,
                                index=([NONE] + POLLUTANT_ORDER).index(default_p) if default_p else 0,
                                format_func=lambda x: x if x == NONE else pollutant_label(x), key=k("fixedp"))
            m.fixed_pollutant = _opt(pick)

        with st.expander("Kolumny opcjonalne (nazwa stacji, współrzędne, jednostka, uśrednianie)"):
            d1, d2, d3 = st.columns(3)
            m.station_name_col = _select(d1, "Nazwa stacji", columns, guess.station_name_col, k("sname"))
            m.city_col = _select(d2, "Miejscowość", columns, guess.city_col, k("city"))
            m.voivodeship_col = _select(d3, "Województwo", columns, guess.voivodeship_col, k("voiv"))
            m.lat_col = _select(d1, "Szerokość geograficzna", columns, guess.lat_col, k("lat"))
            m.lon_col = _select(d2, "Długość geograficzna", columns, guess.lon_col, k("lon"))
            m.unit_col = _select(d3, "Jednostka", columns, guess.unit_col, k("unit"))
            m.averaging_col = _select(d1, "Czas uśredniania", columns, guess.averaging_col, k("avg"))
            if not m.unit_col:
                m.fixed_unit = d2.text_input("Jednostka dla całego pliku (np. µg/m³)", key=k("funit"),
                                             help="Jeśli pusta, jednostka pozostanie nieznana i zostanie oznaczona jako założona.") or None
            if not m.averaging_col:
                m.fixed_averaging = d3.text_input("Czas uśredniania dla całego pliku (np. 1h, 24h)", key=k("favg")) or None

    meta_file = st.file_uploader("Opcjonalnie: plik metadanych stacji (kod, nazwa, współrzędne)",
                                 type=["csv", "txt", "xlsx", "xls"], key="imp_meta")
    station_meta = None
    if meta_file is not None:
        try:
            station_meta = parse_station_metadata(read_table(meta_file.getvalue(), meta_file.name))
            st.caption(f"Metadane: {fmt_int(len(station_meta))} stacji.")
        except DataFormatError as exc:
            st.error(str(exc))

    o1, o2 = st.columns([2, 1])
    name = o1.text_input("Nazwa zbioru", value=", ".join(n for n, _ in files)[:80], key=f"imp_name_{sig}")
    neg = o2.radio("Wartości ujemne", ["Traktuj jako brak danych", "Zachowaj bez zmian"], key="imp_neg",
                   help="Ujemne stężenia zwykle oznaczają błąd lub szum przyrządu. Zachowaj je tylko świadomie.")

    if st.button("Wczytaj i zwaliduj dane", type="primary", key="imp_go"):
        try:
            with st.spinner("Czyszczę i waliduję dane…"):
                new = build_dataset(
                    frames, m, name=name or first_name, source_kind=source_kind, station_meta=station_meta,
                    negative_policy="nan" if neg.startswith("Traktuj") else "keep",
                )
        except DataFormatError as exc:
            logger.warning("Błąd importu: %s", exc)
            st.error(str(exc))
        else:
            st.session_state["dataset"] = new
            reset_filters()
            st.session_state["import_flash"] = (
                f"Wczytano zbiór „{new.name}”: {fmt_int(len(new.measurements))} pomiarów, "
                f"{fmt_int(new.catalog['station_code'].nunique())} stacji. Szczegóły w zakładce „Raport walidacji”."
            )
            st.rerun()

# --- Raport -----------------------------------------------------------------------------
with tab_report:
    st.markdown(f"**Aktywny zbiór:** {ds.name} · {SOURCE_LABELS.get(ds.source_kind, ds.source_kind)}")
    left, right = st.columns([1, 1.4])
    with left:
        ui.section_title("Raport czyszczenia danych")
        st.dataframe(pd.DataFrame(ds.report.as_rows(), columns=["Etap", "Liczba"]), hide_index=True, width="stretch")
    with right:
        ui.section_title("Katalog serii pomiarowych", "Każdy wskaźnik tylko na stacjach, które go faktycznie mierzą.")
        cat = ds.catalog.assign(Stacja=ds.catalog["station_code"].map(ds.station_label))
        cat = cat[["pollutant", "Stacja", "averaging", "unit", "n", "first", "last"]]
        cat.columns = ["Wskaźnik", "Stacja", "Uśrednianie", "Jednostka", "Pomiary", "Od", "Do"]
        st.dataframe(cat, hide_index=True, width="stretch", height=360,
                     column_config={"Od": st.column_config.DatetimeColumn(format="DD.MM.YYYY"),
                                    "Do": st.column_config.DatetimeColumn(format="DD.MM.YYYY")})
    for note in ds.report.notes:
        ui.callout(note, "info")
    for warning in ds.report.warnings:
        ui.callout(warning, "warn")
    d0, d1 = ds.overall_bounds()
    st.caption(f"Zakres danych: {fmt_date(d0)} – {fmt_date(d1)}.")

# --- Pomoc ------------------------------------------------------------------------------
with tab_help:
    st.markdown(
        f"""
**Źródło wzorcowe:** {SOURCE_GIOS_NAME}
Archiwum: {SOURCE_GIOS_ARCHIVES} · API: {SOURCE_GIOS_API}

**Obsługiwane układy plików** (wykrywane automatycznie, do ręcznej korekty):
1. *Format długi* – kolumny: czas, stacja, wskaźnik, wartość.
2. *Kolumny wskaźników* – jeden wiersz = czas + stacja, osobna kolumna na każde zanieczyszczenie.
3. *Kolumny stacji* – jeden wiersz = czas, osobna kolumna na każdą stację (plik dotyczy jednego wskaźnika).

**Co aplikacja robi z danymi:** rozpoznaje separator i kodowanie, formaty dat i separator dziesiętny (przecinek/kropka),
usuwa duplikaty, traktuje braki jako brak danych (nigdy jako zero) i raportuje każdą ingerencję.

**Aby wiarygodnie obsłużyć archiwum GIOŚ** potrzebne są próbki rzeczywistych plików – bez nich nie zgadujemy ich schematu:
- 1–2 pliki pomiarowe z archiwum (np. jeden wskaźnik, jeden rok), z nagłówkiem i pierwszymi wierszami,
- plik z kodami i lokalizacją stacji/stanowisk (metadane, współrzędne),
- informacja, w jakiej strefie czasowej i konwencji (początek/koniec godziny) zapisano czas pomiaru.

**Adapter GIOŚ API** jest przygotowany jako osobna klasa (`modules/data_loader.py → GiosApiAdapter`), ale celowo
niezaimplementowany: wymaga aktualnej specyfikacji API i przykładowych odpowiedzi.
"""
    )

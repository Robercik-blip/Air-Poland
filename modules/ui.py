"""Elementy interfejsu wielokrotnego użytku: CSS, nagłówek, karty KPI, komunikaty, pasek boczny."""
from __future__ import annotations

import html

import streamlit as st

from modules.config import ASSETS_DIR, DEMO_LABEL, SOURCE_GIOS_ARCHIVES, SOURCE_GIOS_NAME
from modules.data_loader import SOURCE_LABELS, Dataset
from modules.utils import fmt_date, fmt_int


def load_css() -> None:
    """Wstrzykuje arkusz stylów z assets/styles.css."""
    css = (ASSETS_DIR / "styles.css").read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def page_header(title: str, subtitle: str, ds: Dataset | None = None) -> None:
    """Tytuł strony, krótki opis i plakietka źródła danych."""
    badge = ""
    if ds is not None:
        if ds.is_synthetic:
            badge = f'<span class="ap-badge ap-badge-demo">{DEMO_LABEL}</span>'
        else:
            badge = '<span class="ap-badge ap-badge-ok">Dane użytkownika</span>'
    st.markdown(
        f'<div class="ap-header"><div><h1>{html.escape(title)}</h1><p>{subtitle}</p></div><div>{badge}</div></div>',
        unsafe_allow_html=True,
    )


def callout(text: str, kind: str = "info") -> None:
    """Komunikat w ramce. kind: info | warn | demo | ok. Tekst może zawierać zaufany HTML."""
    st.markdown(f'<div class="ap-callout ap-callout-{kind}">{text}</div>', unsafe_allow_html=True)


def section_title(title: str, caption: str | None = None) -> None:
    st.markdown(f'<div class="ap-section-title">{html.escape(title)}</div>', unsafe_allow_html=True)
    if caption:
        st.markdown(f'<div class="ap-caption">{caption}</div>', unsafe_allow_html=True)


def source_caption(ds: Dataset, extra: str = "") -> None:
    """Podpis pod wykresem: skąd pochodzą dane i czym są pokazywane wartości."""
    if ds.is_synthetic:
        src = f"Źródło: <b>{DEMO_LABEL}</b> (nie są to pomiary GIOŚ)."
    else:
        src = f"Źródło danych: {html.escape(ds.name)}."
    st.markdown(f'<div class="ap-caption">{src} {extra}</div>', unsafe_allow_html=True)


def kpi_card(label: str, value: str, unit: str = "", sub: str = "", accent: str = "teal") -> str:
    """HTML pojedynczej karty KPI."""
    unit_html = f"<small>{html.escape(unit)}</small>" if unit else ""
    sub_html = f'<div class="kpi-sub">{sub}</div>' if sub else ""
    return (
        f'<div class="kpi accent-{accent}"><div class="kpi-label">{html.escape(label)}</div>'
        f'<div class="kpi-value">{value}{unit_html}</div>{sub_html}</div>'
    )


def kpi_row(cards: list[str]) -> None:
    """Rząd kart KPI o równej szerokości."""
    for col, card in zip(st.columns(len(cards)), cards):
        col.markdown(card, unsafe_allow_html=True)


def render_sidebar(ds: Dataset, container=None) -> None:
    """Dolna część paska bocznego (pod nawigacją): źródło, status zbioru, zakres dat.

    ``container`` pozwala zarezerwować miejsce przed uruchomieniem strony, a wypełnić je po niej,
    dzięki czemu wybrany zakres dat jest zawsze aktualny (bez opóźnienia o jedno odświeżenie).
    """
    d0, d1 = ds.overall_bounds()
    dates = st.session_state.get("flt_dates")
    selected = f"{fmt_date(dates[0])} – {fmt_date(dates[1])}" if dates else "pełny zakres"
    dot = "ap-dot ap-dot-demo" if ds.is_synthetic else "ap-dot"
    status = "Dane demonstracyjne" if ds.is_synthetic else "Zbiór załadowany"
    with (container if container is not None else st.sidebar):
        st.markdown('<div class="ap-side-label">Źródło danych</div>', unsafe_allow_html=True)
        if ds.is_synthetic:
            src_html = f"<b>{DEMO_LABEL}</b><br><span style='color:#8b98a9'>Nie są to pomiary GIOŚ.</span>"
        else:
            src_html = (
                f"<b>{html.escape(ds.name)}</b><br><span style='color:#8b98a9'>{SOURCE_LABELS.get(ds.source_kind, '')}</span>"
            )
        st.markdown(f'<div class="ap-side-card">{src_html}</div>', unsafe_allow_html=True)

        st.markdown('<div class="ap-side-label">Status zbioru</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="ap-side-card"><div><span class="{dot}"></span>{status}</div>'
            f'<div class="row"><span>Pomiary</span><span>{fmt_int(len(ds.measurements))}</span></div>'
            f'<div class="row"><span>Stacje</span><span>{fmt_int(ds.catalog["station_code"].nunique())}</span></div>'
            f'<div class="row"><span>Wskaźniki</span><span>{len(ds.pollutants)}</span></div>'
            f'<div class="row"><span>Dane od–do</span><span>{fmt_date(d0)}<br>{fmt_date(d1)}</span></div></div>',
            unsafe_allow_html=True,
        )

        st.markdown('<div class="ap-side-label">Wybrany zakres dat</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="ap-side-card">{selected}</div>', unsafe_allow_html=True)

        st.markdown(
            '<div class="ap-side-foot">Wzorcowe źródło danych:<br>'
            f"{html.escape(SOURCE_GIOS_NAME)}<br>"
            f'<a href="{SOURCE_GIOS_ARCHIVES}" target="_blank" style="color:#2dd4bf">powietrze.gios.gov.pl</a><br><br>'
            "Aplikację opracowałem w ramach projektu inżynierskiego na AGH w Krakowie.</div>",
            unsafe_allow_html=True,
        )

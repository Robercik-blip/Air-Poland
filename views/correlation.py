"""Analiza korelacji (Część 2)."""
from views import _upcoming

_upcoming.render(
    "Analiza korelacji",
    "Zależności między zanieczyszczeniami: współczynniki Pearsona i Spearmana oraz mapa ciepła macierzy korelacji.",
    [
        "Korelacje Pearsona i Spearmana między wskaźnikami, z wyborem wskaźników.",
        "Dopasowanie pomiarów w czasie i według stacji przed obliczeniem korelacji (bez łączenia niepowiązanych obserwacji).",
        "Jawna obsługa braków danych (usuwanie parami) oraz minimalna liczba wspólnych obserwacji.",
        "Interaktywna mapa ciepła i objaśnienie interpretacji współczynnika.",
    ],
)

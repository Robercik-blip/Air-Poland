"""Analiza wielowymiarowa: PCA i klasteryzacja (Część 2)."""
from views import _upcoming

_upcoming.render(
    "Analiza wielowymiarowa",
    "Analiza głównych składowych (PCA) i analiza skupień (k-średnich) dla stacji i okresów.",
    [
        "Standaryzacja zmiennych, wariancja wyjaśniona i skumulowana, rzut obserwacji na PC1/PC2 oraz ładunki.",
        "K-średnich z wyborem liczby skupień, wykres w przestrzeni PC1/PC2 i podsumowanie statystyczne skupień.",
        "Komunikat zamiast wyniku, gdy zbiór nie ma wystarczającej liczby obserwacji lub wskaźników.",
        "Omówienie ograniczeń metod (założenia liniowości, wrażliwość na wartości odstające i braki danych).",
    ],
)

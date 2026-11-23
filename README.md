# AIR POLAND – interaktywna analityka jakości powietrza w Polsce

Aplikacja Streamlit do analizy i wizualizacji danych o jakości powietrza (projekt inżynierski, AGH Kraków).
Cały interfejs jest po polsku. Źródłem wzorcowym jest GIOŚ – Państwowy Monitoring Środowiska
(<https://powietrze.gios.gov.pl/pjp/archives>, <https://powietrze.gios.gov.pl/pjp/content/api>).

> **Stan: Część 2 (w trakcie).** Gotowe: układ i nawigacja, wczytywanie i walidacja danych, Panel główny,
> Analiza czasowa, Analiza przestrzenna (w tym względna skala zielony–czerwony na mapie), Eksploracja danych,
> import GIOŚ, statystyki roczne GIOŚ, analiza korelacji oraz PCA i klasteryzacja.

## Uruchomienie na Windows (VS Code + wirtualne środowisko)

1. Zainstaluj Python 3.11 lub nowszy (python.org; zaznacz „Add python.exe to PATH”).
2. Otwórz folder projektu w VS Code (*File → Open Folder*), potem terminal (*Terminal → New Terminal*, PowerShell).
3. Utwórz i aktywuj środowisko:
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   ```
   Jeśli PowerShell blokuje skrypty: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, albo użyj `.venv\Scripts\activate.bat` w cmd.
4. Zainstaluj zależności i uruchom:
   ```powershell
   pip install -r requirements.txt
   streamlit run app.py
   ```
5. Aplikacja otworzy się pod `http://localhost:8501`. W VS Code wybierz interpreter z `.venv` (*Ctrl+Shift+P → Python: Select Interpreter*).
6. Testy: `python -m pytest tests -q`.

Mapa wymaga dostępu do internetu (biblioteka Leaflet i kafelki podkładowe CARTO/OpenStreetMap są ładowane z sieci).

## Architektura

```
app.py                  punkt wejścia: motyw, nawigacja (st.navigation), pasek boczny
views/                  strony (każda to skrypt wywoływany przez st.navigation)
modules/
  config.py             stałe, metadane wskaźników, poziomy odniesienia, paleta
  utils.py              formatowanie po polsku, normalizacja tekstu, kolory poziomów
  data_processing.py    czyszczenie: daty, separatory, braki, duplikaty, jednostki, raport walidacji
  data_loader.py        odczyt CSV/XLSX/ZIP GIOŚ, wykrywanie mapowania, Dataset, adapter API (szkielet)
  gios_statistics.py   parser osobnego skoroszytu rocznych statystyk GIOŚ
  demo_data.py          generator DEMONSTRACYJNYCH DANYCH SYNTETYCZNYCH
  analysis.py           agregacje z kontrolą pokrycia, KPI, zestawienia roczne/miesięczne/sezonowe (bez Streamlit)
  advanced_analysis.py  przygotowanie macierzy, korelacje, PCA i klasteryzacja (bez Streamlit)
  filters.py            spójne filtry globalne w st.session_state
  services.py           pamięć podręczna (st.cache_data / cache_resource)
  visualizations.py     wykresy Plotly w jednolitym ciemnym stylu
  maps.py               mapa stacji (Folium)
  ui.py                 CSS, nagłówki, karty KPI, pasek boczny
assets/styles.css       styl interfejsu
tests/                  testy jednostkowe i dymne
```

Zmiana względem szkicu z zadania: katalog stron nazywa się `views/`, a nie `pages/`. Nazwa `pages/` włącza w Streamlit
automatyczną nawigację, która koliduje z `st.navigation` (potrzebnym do własnej kolejności, ikon i polskich nazw).

Przepływ danych: plik → `read_table` → `detect_mapping` (propozycja) → mapowanie potwierdzone przez użytkownika →
`standardise` (czyszczenie + raport) → `Dataset` (pomiary w formacie długim + stacje + katalog serii) → filtry → `analysis` → wykresy.

## Metody i decyzje (do opisu w pracy)

- **Braki danych** nigdy nie są zamieniane na zero: wiersz bez wartości jest usuwany i policzony w raporcie.
  Wartości ujemne domyślnie są traktowane jako brak danych (opcja zmiany w imporcie).
- **Pokrycie okresu**: średnia dobowa/miesięczna/roczna jest wiarygodna, gdy udział ważnych pomiarów w *pełnym*
  okresie kalendarzowym wynosi ≥ 75% (próg do zmiany suwakiem). Niepełny rok na brzegu zakresu jest więc oznaczany jako niewiarygodny.
- **Średnia z wielu stacji**: najpierw średnie stacji, potem średnia ze stacji (stacja z większą liczbą pomiarów nie dominuje).
- **Czas uśredniania** (np. 1h, 24h) jest atrybutem serii; różne rozdzielczości nie są łączone w jednej serii.
- **Sezony**: meteorologiczne (zima = XII–II; grudzień należy do zimy następnego roku).
- **Poziomy odniesienia** (WHO 2021, dyrektywa 2008/50/WE) są orientacyjne – przed cytowaniem zweryfikuj je w aktach prawnych.
  Kolory (turkus / pomarańcz / czerwień) odnoszą się do poziomów rocznych, więc dla średnich krótszych okresów mają charakter poglądowy.

## Dane

- Domyślnie aplikacja startuje na **DEMONSTRACYJNYCH DANYCH SYNTETYCZNYCH** (oznaczone w nagłówku, pasku bocznym i podpisach wykresów).
  Współrzędne to środki miast, a nie lokalizacje stacji. Nie wolno prezentować ich jako pomiarów GIOŚ.
- Własne dane: zakładka **Źródło danych i import** (plik lokalny z `data/raw/` lub wgranie z komputera).
  Obsługiwane układy: format długi, kolumny wskaźników, kolumny stacji. Kodowanie, separator, format dat i separator dziesiętny są wykrywane.
- Archiwa pomiarowe GIOŚ: wybierz jeden lub kilka rocznych plików ZIP, skoroszyt metadanych z arkuszami
  `STACJE` i `STANOWISKA` oraz wskaźniki do wczytania. Parser obsługuje serie PM10, PM2.5, NO2, SO2, O3,
  CO i C6H6; pozostałe kody są raportowane, a nie mapowane na podobnie nazwane zanieczyszczenia.
  Czas pomiarów zostaje taki, jak zapisano w archiwum GIOŚ (CET); nie jest sztucznie przesuwany.
- Skoroszyt `Statystyki z lat 2000-2025` wczytuje się osobno na stronie **Statystyki roczne GIOŚ**.
  Arkusze wskaźników są traktowane jako gotowe statystyki roczne per stacja; dane nie są zamieniane na pomiary
  ani łączone z aktywnym `Dataset`. Można wybrać wskaźnik, statystykę, źródłowy czas uśredniania i stację lub
  województwo. Średnia dla wielu stacji jest nieważoną średnią arytmetyczną wartości dostępnych stacji, a nie
  oficjalną średnią krajową; skład sieci może zmieniać się między latami.
- **Adapter GIOŚ API** (`GiosApiAdapter`) jest szkieletem – celowo niezaimplementowanym.

## Ograniczenia tej wersji

- Brak wizualizacji choropletycznej (wymaga zewnętrznego pliku z granicami województw).
- Korelacje, PCA i klasteryzacja są metodami eksploracyjnymi; ich wyniki zależą od zakresu, braków danych,
  wybranych wskaźników i przyjętej metody.
- Mapa nie była testowana wizualnie w środowisku bez dostępu do internetu; pozostałe strony przeszły testy renderowania.
- Konwencja czasu (np. 24:00 jako północ następnego dnia) jest obsłużona, ale strefa czasowa danych nie jest przeliczana.

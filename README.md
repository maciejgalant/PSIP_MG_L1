# PSIP\_MG\_L1
- Stanisław - turecki
- MACIEJ - KUTNOWSKI
- Daniel - sochaczewski
- Jan - miński
- Dominik - koniński
- Kacper - legnicki
- Tymoteusz - toruński
- Dawid - bocheński
- Michał - biłgorajski
- Bartek - radomszczański
- Marcin - miński
- Jakub - chełmski
- Julia - hajnowski
- Jan - warszawski

## LAB 4 – indeks przejezdności terenu (IOP)

Skrypty (kontener `python`):

| Krok | Polecenie | Wynik |
|---|---|---|
| normalizacja + wagi + IOP | `python /app/compute_iop.py` | `grid.h3_iop`, widoki `grid.h3_iop_res7/8/9`, `grid.iop_weights`, styl QGIS w `public.layer_styles` |
| mapy PNG | `python /app/plot_iop.py` | `/app/output/iop_res{7,8,9}.png` |

Metoda VRF wg K. Pokonieczny (2018), *Comparison of land passability maps created with use of different spatial data bases*, Geografie 123/3 (wzory 1–2, rys. 4):

1. Normalizacja kryteriów min-max (wzór 1): `k = (V − Vmin) / (Vmax − Vmin)` → 0..1, osobno dla każdej rozdzielczości H3 (jednostki m², m, szt. przestają mieć znaczenie).
2. Współczynniki IVRF (wagi) `∈ [−1, 1]` z `app/iop_weights.csv`: dodatnie ułatwiają przejazd, ujemne utrudniają, 0 = neutralne.
3. `iop_suma = Σ kᵢ·IVRFᵢ` (wzór 2); dodatkowo `iop_srednia = iop_suma / n` (wariant z zajęć, −1..1).
4. `iop_norm` – ponowna normalizacja min-max `iop_suma` do 0..1 (0 = najgorsza, 1 = najlepsza przejezdność w powiecie).

Klasy (`iop_klasa`): 5 równych przedziałów `iop_norm` od 0 do 1, kolory czerwony (0) → biały (0,5) → zielony (1).
W QGIS wystarczy dodać warstwę `grid.h3_iop_res*` – styl wczyta się automatycznie.

Wymaga `grid.h3_aggregation` z kolumną `resolution` (aktualna wersja `aggregate_h3.py`).
Workflow `Start Remote Database` liczy IOP po każdym pushu i publikuje mapy jako artefakt `iop-maps-<środowisko>`.

## GUI (Tkinter, bez dodatkowych pakietów na hoście)

Uruchom z katalogu repozytorium, przy działającym `docker-compose-local.yml`:

```powershell
py gui/lab3_app.py        # panel: kontenery, kontrola bazy, agregacja, Oblicz IOP, Mapy PNG
py gui/h3_map.py          # mapa H3: klasy IOP lub dowolne kryterium *_norm, szczegóły komórki
py gui/weights_editor.py  # edytor wag IVRF -> app/iop_weights.csv, "Zapisz i przelicz IOP"
```

Mapę i edytor wag można też otworzyć przyciskami w panelu. Na mapie: kółko myszy = zoom,
przeciąganie = przesuwanie, kliknięcie heksagonu = IOP komórki i kryteria o największym wpływie (k·w).

## Aplikacja GIS (Tkinter, branch `gui/mapa-gis`)

```powershell
py gui/gis_app.py         # albo przycisk „Aplikacja GIS” w panelu lab3_app.py
```

Tylko biblioteka standardowa Pythona – SHP, układy współrzędnych i PDF są zaimplementowane w `gui/`.
Przy pierwszym uruchomieniu zakładasz konto (zapis w `gui/users.json`, hasła PBKDF2-SHA256, plik w `.gitignore`).
Każdy przycisk paska narzędzi otwiera okno modalne.

| Przycisk | Funkcja |
|---|---|
| Wczytaj SHP | przeglądarka folderów i plików `.shp` (+ systemowe „Przeglądaj…”), rozpoznanie układu z `.prj` (2180, 4326, 3857, dowolny TM), przeliczenie do EPSG:2180 |
| H3 z PostGIS | warstwa `grid.h3_iop` (R7/R8/R9) + granica `public.powiat` (wymaga `docker-compose-local.yml`) |
| Analiza i wagi | suwak wagi IVRF −1..1 przy każdym kryterium `*_norm`, IOP przeliczany na żywo (Σ k·w → min-max, jak `compute_iop.py`), zapis do `app/iop_weights.csv`, przeliczenie w PostGIS |
| Styl warstwy | jeden symbol albo schemat stopniowany: pole, zakres min–max, liczba klas, kolory schematu, obrys |
| Szukaj | wyszukiwanie w atrybutach (pole / wszystkie pola, `zawiera = ≠ > ≥ < ≤`), tabela wyników, zaznaczanie i zoom |
| Zoom do obiektu | lista obiektów warstwy z filtrem, zoom do obiektu / warstwy / wszystkich warstw |
| Pomiar odległości | 2 punkty na mapie → odległość w metrach (geodezyjna GRS80 + płaska 2180) |
| Warstwa punktowa / Zapisz SHP | dodawanie punktów kliknięciem (Ctrl+Z cofa, Esc kończy), zapis `.shp/.shx/.dbf/.cpg/.prj` (EPSG:2180) |
| Jasny / ciemny | motyw całej aplikacji i tła mapy |
| Eksport PDF | wektorowy PDF (A4/A3, poziomo/pionowo) z legendą, podziałką, skalą wydruku i osadzoną czcionką |
| Wyloguj | potwierdzenie i powrót do ekranu logowania |

Panel warstw: widoczność, kolejność rysowania (▲▼, góra listy = na wierzchu), usuwanie, legenda pod każdą warstwą.
Pasek stanu: współrzędne kursora w EPSG:4326 / 3857 / 2180, aktualna skala (można wpisać własną), tryb pracy.
Mapa: kółko = zoom, przeciąganie = przesuwanie, klik = informacje o obiekcie.

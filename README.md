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

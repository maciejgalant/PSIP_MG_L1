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

Model: `IOP_norm = Σ(wᵢ·kᵢ) / Σ|wᵢ|`, gdzie
- `kᵢ` – kryterium z `grid.h3_aggregation` znormalizowane do 0..1 (`x / max(x)`, maksimum liczone osobno dla każdej rozdzielczości H3; jednostki m², m, szt. przestają mieć znaczenie),
- `wᵢ ∈ [-1, 1]` – waga ekspercka z `app/iop_weights.csv` (dodatnia = ułatwia przejazd, ujemna = utrudnia),
- mianownik `Σ|wᵢ|` utrzymuje wynik w zakresie −1..1; `IOP_DENOMINATOR=n` dzieli przez liczbę kryteriów.

Klasy (`iop_klasa`): 5 równych przedziałów od −1 do 1, kolory czerwony (−1) → biały (0) → zielony (1).
W QGIS wystarczy dodać warstwę `grid.h3_iop_res*` – styl wczyta się automatycznie.

Wymaga `grid.h3_aggregation` z kolumną `resolution` (aktualna wersja `aggregate_h3.py`).
Workflow `Start Remote Database` liczy IOP po każdym pushu i publikuje mapy jako artefakt `iop-maps-<środowisko>`.

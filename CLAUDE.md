# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the script

```powershell
python analisis_traslados.py
```

Output is saved to `Output/Plan_Traslados_YYYYMMDD.xlsx`.

Dependencies: `pandas`, `openpyxl` (Python 3.x).

---

## Architecture

Single-file script (`analisis_traslados.py`). No modules, no tests, no config files.

### Input files (`Input/`)

| File | Sheet | Key columns | Purpose |
|---|---|---|---|
| `DB_Tiendas.xlsx` | default | `BODEGA` (e.g. BOS06), `C.O` (e.g. S06), `NOMBRE DE TIENDA`, `Zona` | 94 stores → zone + name |
| `DB_CELES.xlsx` | `Items` | `C BODEGA`, `Item`, `Cuadrante de Producto`, `(=) Inventario Total`, `Consumo Diario` | Full national inventory — 13 cuadrantes, ~117 000 rows |
| `DB_PORTAFOLIO_FRUVER.xlsx` | default | `COD SIESA`, `ITEM` | Authorized portfolio per store — **FRUVER only** |
| `DB_POLITICA_NACIONAL_DIAS_DE_INVENTARIO_POR_CUADRANTE.xlsx` | default | `CUADRANTE`, `Dias de Inventario Maximos` | Max inventory days per cuadrante (policy thresholds) |

**Join chain**: `DB_Tiendas.BODEGA` = `DB_CELES.C BODEGA`; `DB_Tiendas.C.O` = `DB_PORTAFOLIO_FRUVER.COD SIESA`

### Configurable parameters (`analisis_traslados.py`, top of file)

| Constant | Value | Meaning |
|---|---|---|
| `DIAS_EXCESO_ORIGEN` | 15 | Fallback max-days threshold (used if cuadrante not in policy file) |
| `DIAS_MINIMO_ORIGEN` | 4 | Minimum projected days that must remain at origin after transfer — universal, all cuadrantes |
| `CAJAS_MINIMO_ORIGEN` | 0.8 | Minimum boxes to retain at origin — general fallback |
| `CAJAS_MINIMO_POR_CUADRANTE` | Q17→0.7, Q18→0.5 | Per-cuadrante overrides for minimum boxes at origin |
| `CAJAS_MINIMO_TRASLADO` | 1 | Minimum boxes per individual transfer |
| `DIAS_MAXIMO_DESTINO` | 15 | Cap used in post-process Filtro 1: if a destination already has ≥ this many days from prior transfers in the same run, additional transfers to it are removed |
| `CAJAS_MINIMO_TOTAL_ORIGEN` | 5 | Minimum total boxes a source store must send (per transfer type) to justify the logistics operation |

### Algorithm (greedy, single-pass)

1. Load `dias_max_cuadrante` dict from the policy file: maps each `Cuadrante de Producto` string to its max inventory days.
2. Build `portafolio_valido`: set of `(BODEGA, item)` tuples authorized to receive stock — **applies to FRUVER (Q16) only**.
3. Build `stock_simulado`: mutable copy of `Inventario Total` — updated after every transfer so subsequent transfers see current stock.
4. For each **source** row where `Dias Calc > dias_max_cuadrante[cuadrante]`, `Consumo diario > 0`, `Inventario Total > 0`:
   - `cajas_minimo_src` = per-cuadrante minimum (from `CAJAS_MINIMO_POR_CUADRANTE`, fallback `CAJAS_MINIMO_ORIGEN`).
   - `minimo_origen = max(cajas_minimo_src, consumo_src × DIAS_MINIMO_ORIGEN)` — origin must keep at least this much.
   - `disponible = floor(stock_simulado − minimo_origen)` — must be ≥ `CAJAS_MINIMO_TRASLADO` to proceed.
   - Find **destinations**: same item, different store, `Consumo diario > 0`, simulated `Dias Inv < dias_max_cuadrante[cuadrante]`.
     - If FRUVER: destination must also be in `portafolio_valido`.
   - Sort candidates by urgency (lowest days first). Same zone first (`Misma Zona`), other zones second (`Sugerencia Otra Zona`).
   - Compute `cajas = floor(min(ceil((dias_max − dias_dst) × consumo_dst), disponible))`. Then cap: `cajas = min(cajas, floor(dias_max × consumo_dst − stock_dst))` — ensures the destination never exceeds the policy maximum after receiving stock. If the capped value < `CAJAS_MINIMO_TRASLADO`, skip this destination.
   - Decrement `disponible` and update `stock_simulado` for both origin and destination.
5. If no destination found → append to `sin_destino` with diagnostic `Motivo`.
6. **Post-process filters** (applied to the collected transfers DataFrame):
   - **Filtro 1** (anti over-stock combined): for each `(Item, Bodega Destino)` group that has multiple incoming transfers, remove subsequent transfers once the destination reaches `DIAS_MAXIMO_DESTINO`.
   - **Filtro 2** (minimum volume): evaluated **per transfer type** (`Misma Zona` and `Sugerencia Otra Zona` independently). Origins sending fewer than `CAJAS_MINIMO_TOTAL_ORIGEN` boxes in a given type have all transfers of that type removed.

### Business rules (do not change without user approval)

- **Do not use** `Inv Traslado proces` column — only `(=) Inventario Total`.
- Minimum retained at origin: **cuadrante-specific** — 0.8 boxes general, 0.7 for Q17, 0.5 for Q18.
- Destination cap: transfer quantity is capped so that the destination's projected days after the transfer do not exceed the cuadrante's policy maximum. If even 1 box would push the destination over the limit, the transfer is skipped.
- Minimum days retained at origin: **4 days** (all cuadrantes).
- Transfer quantities: **whole integers only** (`math.floor`).
- Items with `Consumo diario = 0` are **excluded** entirely (no source, no dead-stock fallback).
- `Dias de Inv` thresholds: **from policy file** per cuadrante (e.g., 10 days for FRUVER, 15 for ALIMENTOS, 8 for PANADERIA CROSS, etc.). Fallback = 10.
- Portfolio restriction (`portafolio_valido`): **FRUVER (Q16) only** — all other cuadrantes have no portfolio restriction.
- Anti-cycle: `ya_fue_origen` set prevents a store that already sent a given item from receiving that same item.
- **Q14 - PANADERIA CROSS**: products with shelf life < 30 days (8-day policy). **Q14 - PANADERIA**: products with shelf life ≥ 30 days (15-day policy). Classification is pre-assigned in `Cuadrante de Producto` column of DB_CELES.

### Output sheets (in order)

1. **Resumen Ejecutivo** — styled openpyxl cells; totals, top items/stores, resumen por cuadrante, resumen por zona.
2. **Traslados Misma Zona** — transfers within the same zone. Includes `Cuadrante` column.
3. **Sugerencias Otra Zona** — cross-zone suggestions. Includes `Cuadrante` column.
4. **Sin Destino Posible** — items with excess but no valid recipient. Includes `Cuadrante` column.
5. **Resumen por Zona** — aggregated stats per zone.

Column order in sheets 2–3: item info (`Item`, `Producto`, `Cuadrante`, `Cajas a Trasladar`, `Tipo`) → origin block (Bodega, Tienda, Zona, stocks, consumo, días) → destination block (same structure).

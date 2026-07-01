# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the script

```powershell
python analisis_traslados.py
```

Output is saved to `Output/Plan_Traslados_YYYYMMDD.xlsx`.

For the routing step (requires OR-Tools):

```powershell
python ruteo_traslados.py
```

Dependencies: `pandas`, `openpyxl`, `ortools` (Python 3.x).

---

## Architecture

Two active scripts. No modules, no tests, no config files.

| Script | Purpose |
|---|---|
| `analisis_traslados.py` | Main script — detects inventory excess and generates transfer plan |
| `ruteo_traslados.py` | Companion script — reads the plan output and generates an optimal visit route per zone using OR-Tools TSP |

### Input files (`Input/`)

| File | Sheet | Key columns | Purpose |
|---|---|---|---|
| `DB_Tiendas.xlsx` | default | `BODEGA` (e.g. BOS06), `C.O` (e.g. S06), `NOMBRE DE TIENDA`, `Zona` | 94 unique store bodegas → zone + name. Note: file has 95 rows (one empty row at the end). |
| `DB_CELES.xlsx` | `Items` | `Código de Bodega`, `Código de Producto`, `Nombre de Producto`, `Cuadrante de Producto`, `(=) Inventario Total`, `Consumo Diario (Unidades de Distribución)` | Full national inventory — multiple cuadrantes, ~117 000 rows |
| `DB_PORTAFOLIO_FRUVER.xlsx` | default | `COD SIESA`, `DB_Portafolio_Fruver.ITEM` | Authorized portfolio per store — **FRUVER only** |
| `DB_POLITICA_NACIONAL_DIAS_DE_INVENTARIO_POR_CUADRANTE.xlsx` | default | `CUADRANTE`, `Dias de Inventario Maximos` | Max inventory days per cuadrante (policy thresholds) |

**Join chain**: `DB_Tiendas.BODEGA` = `DB_CELES.Código de Bodega`; `DB_Tiendas.C.O` = `DB_PORTAFOLIO_FRUVER.COD SIESA`

### Configurable parameters (`analisis_traslados.py`, top of file)

| Constant | Current value | Meaning |
|---|---|---|
| `DIAS_EXCESO_ORIGEN` | 20 | Fallback max-days threshold (used if cuadrante not in policy file) |
| `DIAS_MINIMO_ORIGEN` | 10 | Minimum projected days that must remain at origin after transfer — universal, all cuadrantes |
| `CAJAS_MINIMO_ORIGEN` | 1 | Minimum boxes to retain at origin — general fallback |
| `CAJAS_MINIMO_POR_CUADRANTE` | Q17→1, Q18→0.8 | Per-cuadrante overrides for minimum boxes at origin |
| `CUADRANTES_EXCLUIDOS` | Q01, Q17, Q18, Q25 | Cuadrantes excluded from the analysis entirely |
| `CAJAS_MINIMO_TRASLADO` | 1 | Minimum boxes per individual transfer |
| `DIAS_MAXIMO_DESTINO` | 15 | Fallback for post-process Filtro 1, only used if an item's cuadrante isn't in the policy file. The normal case uses that cuadrante's own max-days from `dias_max_cuadrante`. |
| `CAJAS_MINIMO_TOTAL_ORIGEN` | 15 | Minimum total boxes a source store must send to justify the logistics operation |

### Algorithm (greedy, single-pass)

1. Load `dias_max_cuadrante` dict from the policy file: maps each `Cuadrante de Producto` string to its max inventory days.
2. Build `portafolio_valido`: set of `(BODEGA, item)` tuples authorized to receive stock — **applies to FRUVER (Q16) only**.
3. Build `stock_simulado`: mutable copy of `Inventario Total` — updated after every transfer so subsequent transfers see current stock.
4. Filter **source** rows where `Dias Calc > dias_max_cuadrante[cuadrante]`, `Consumo diario > 0`, `Inventario Total > 0`, and cuadrante not in `CUADRANTES_EXCLUIDOS`; sort them by `Dias Calc` descending (highest inventory-days first) so the tiendas at greatest risk of merma compete for destination capacity before less urgent ones. For each source row, in that order:
   - `cajas_minimo_src` = per-cuadrante minimum (from `CAJAS_MINIMO_POR_CUADRANTE`, fallback `CAJAS_MINIMO_ORIGEN`).
   - `minimo_origen = max(cajas_minimo_src, consumo_src × DIAS_MINIMO_ORIGEN)` — origin must keep at least this much.
   - `disponible = floor(stock_simulado − minimo_origen)` — must be ≥ `CAJAS_MINIMO_TRASLADO` to proceed.
   - Find **destinations**: same item, same zone, different store, `Consumo diario > 0`, simulated `Dias Inv < dias_max_cuadrante[cuadrante]`.
     - If FRUVER: destination must also be in `portafolio_valido`.
   - Sort candidates by urgency (lowest days first).
   - Compute `cajas = floor(min(ceil((dias_max − dias_dst) × consumo_dst), disponible))`. Then cap: `cajas = min(cajas, floor(dias_max × consumo_dst − stock_dst))` — ensures the destination never exceeds the policy maximum after receiving stock. If the capped value < `CAJAS_MINIMO_TRASLADO`, skip this destination.
   - Decrement `disponible` and update `stock_simulado` for both origin and destination.
5. **Post-process filters** (applied to the collected transfers DataFrame):
   - **Filtro 1** (anti over-stock combined): for each `(Item, Bodega Destino)` group that has multiple incoming transfers, remove subsequent transfers once the destination reaches that item's cuadrante max-days (`dias_max_cuadrante`, fallback `DIAS_MAXIMO_DESTINO` if unmapped).
   - **Filtro 2** (minimum volume): origins sending fewer than `CAJAS_MINIMO_TOTAL_ORIGEN` boxes have all their transfers removed.

### Business rules (do not change without user approval)

- **Do not use** `Inv Traslado proces` column — only `(=) Inventario Total`.
- Minimum retained at origin: **cuadrante-specific** — 1 box general, 1 for Q17, 0.8 for Q18.
- Destination cap: transfer quantity is capped so that the destination's projected days after the transfer do not exceed the cuadrante's policy maximum. If even 1 box would push the destination over the limit, the transfer is skipped.
- Minimum days retained at origin: **10 days** (all cuadrantes).
- Transfer quantities: **whole integers only** (`math.floor`).
- Items with `Consumo diario = 0` are **excluded** entirely (no source, no dead-stock fallback).
- `Dias de Inv` thresholds: **from policy file** per cuadrante. Fallback = 20 (`DIAS_EXCESO_ORIGEN`).
- Portfolio restriction (`portafolio_valido`): **FRUVER (Q16) only** — all other cuadrantes have no portfolio restriction.
- Anti-cycle: `ya_fue_origen` set prevents a store that already sent a given item from receiving that same item.
- Current algorithm only generates **same-zone** transfers (`Misma Zona`). Cross-zone suggestions are not active.
- **Q14 - PANADERIA CROSS**: products with shelf life < 30 days (8-day policy). **Q14 - PANADERIA**: products with shelf life ≥ 30 days (15-day policy). Classification is pre-assigned in `Cuadrante de Producto` column of DB_CELES.

### Output sheets (in order)

1. **Resumen Ejecutivo** — styled openpyxl cells; totals, top items/stores, resumen por cuadrante, resumen por zona, full list of sending stores.
2. **Traslados Misma Zona** — transfers within the same zone. Includes `Cuadrante` column.
3. **Resumen por Tienda** — aggregated stats per store (sent and received).

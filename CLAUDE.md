# CLAUDE.md — Distribuidor Fruver

Uso e instalación: [README.md](README.md). El proyecto se corre **solo** desde la terminal o el IDE
(`python run.py`, `streamlit run app.py`, `pytest`); no hay ejecutables, instaladores ni accesos directos.

## Estructura

```
run.py      CLI: Input/ → Output/ (único punto, junto con la descarga de app.py, que escribe a disco)
app.py      UI Streamlit (mismo pipeline, archivos subidos en pantalla)
core/       loader → preprocessor → algorithm → exporter   (distribución)
            maestros, compras_params, calendario, compras_preprocessor, compras, compras_exporter (compra)
tests/      pytest (pyproject.toml pone la raíz en sys.path; compras_helpers.py = datos sintéticos)
Input/      5 Excel (versionados) · Output/ resultados (ignorado por git)
```

## Pipeline

```
load_inputs(stock, celes, inventario_tiendas, maestros, compras_transito=None) → (dfs, extra, alertas)
  → build_distribution_df(dfs)          → (df_merged, alertas)
  → run_distribution(df_merged)          → (df_output, alertas)  → generate_excel → distribucion_*.xlsx
  → ejecutar_compra(dfs, df_output, …)   → resultado             → generate_purchase_excel → compra_*.xlsx
```
La compra va en su propio try/except: si falla, la distribución ya quedó guardada. `core/` nunca
escribe a disco. `loader.load_files` (archivos maestros sueltos) solo se conserva para los tests.

## Normalización de claves (CRÍTICO; fuente de verdad: `core/preprocessor.py`)

1. **Tienda**: Celes `BOS03` ↔ Tiendas `S03` → se quita el prefijo `BO` (`normalize_store`).
2. **Ítem**: Stock `'0000155'` (texto) ↔ resto `155` → todo a entero (`normalize_item`).
3. **Stock** trae una fila «Gran total» (o con "total") que se elimina; ítems repetidos se suman,
   fracciones se redondean hacia abajo (con alerta).
4. **Estado activo** (`'NNN - ESTADO'`): se compara el último segmento **exacto**, nunca con
   `contains` (`'INACTIVO'` contiene `'ACTIVO'`). Filtra Portafolio y Tiendas×Ítem; en Stock
   `ESTADO DEL PRODUCTO` **no** bloquea (solo alerta): si hay existencia física, se distribuye.
5. La elegibilidad sale **solo** de Tiendas×Ítem (`ESTADO = ACTIVO`); Celes «Aplica para
   Distribución Automática?» se ignora. Tiendas es inner: tienda que no está, no recibe.

## Distribución (`core/algorithm.py`)

- `inventario_efectivo = existencias + tránsito` de Celes (no «(=) Inventario Total», que viene
  inflado). `dias_proyectados = inventario_efectivo / consumo_diario`. Todo en **cajas enteras**.
- Prioridad por grupo espejo: AGOTADA (< `MIN_STOCK_AGOTADO` = 0.4 cajas) → SAFETY (< 0.9) →
  REPOSICIÓN (< `TARGET_DAYS` = 4 días) → CUBIERTA; dentro de cada nivel, mayor consumo primero.
- **Excluidas del reparto normal**: `bloqueado_por_max` (existencias + tránsito ≥ `MAX` de
  Tiendas×Ítem; `MAX = 0` = sin tope) y `sin_match_celes` (sin fila en Celes). Solo la Fase 5 nivel 2
  puede darles cajas, si no queda otro destino. Se listan en la hoja Novedades.

| Fase | Qué hace |
|---|---|
| 1 | Rondas con cap creciente (1 → `MIN_CAJAS_INICIAL` = 2): todas reciben su 1.ª caja antes que alguien la 2.ª |
| 2 | Completar `TARGET_DAYS`; si no alcanza, reparto proporcional a la necesidad (Hamilton) |
| 3 | Renovación por vida útil: 1 caja (`CAJAS_RENOVACION`) si el último ingreso es más viejo que `VIDA UTIL EXHIBICION` y aún hay inventario; no si hay tránsito, ya recibió en F1/F2 o un SKU espejo tiene ingreso fresco |
| 4 | Excedente: 1 caja por tienda por ronda, peso `consumo / días_actuales`, sin pasar `TOPE_EXCEDENTE` = 7 días ni `MAX_CAJAS_POR_ITEM` = 3 |
| 5 | Cero residuo: lo que quede se reparte igual (única fase que supera el tope). Mucho volumen aquí = sobre-compra |

Grupos espejo (hoja Espejo): la necesidad de F1/F2 y la elegibilidad/peso de F4 se calculan con el
inventario y consumo combinado del grupo; cada SKU sale de su propio stock.
Los parámetros están al inicio de `core/algorithm.py`; el código es la fuente de verdad.

## Compra (`core/compras*.py`, `core/calendario.py`)

- Universo: ítems ACTIVOS del Portafolio (desde STATUS), aunque tengan 0 en el CEDI.
- Calendario: se compra de lunes a sábado; llega al CEDI en 2 días **con recepción** (el domingo no
  recibe: viernes→lunes, sábado→martes) y a tienda 1 día después. Cada compra cubre hasta la llegada
  de la siguiente (la del jueves cubre domingo y lunes). Festivos: capacidad ≈ 40 %, lo faltante se
  anticipa. `DIA DE COMPRA` por ítem (STATUS): todos los días, días fijos o «CADA N DIAS».
- Modelo por tienda × ítem:
  `Inv_llegada = max(0, existencias + tránsito + pedido_hoy + OC_asignada − venta_hasta_llegada)`;
  `Objetivo = max(MIN, min(venta_cobertura + SS, consumo × vida_útil))` (≤ MAX);
  `Compra_tienda = floor(max(0, Objetivo − Inv_llegada) + 1 − UMBRAL_REDONDEO)`.
- Ítem: `Base = Σ venta_cobertura`, `Bruto = Σ compra_tienda`; suavizado `α↑ = 0.5` / `α↓ = 1`;
  luego múltiplo y mínimo del proveedor, y se cuadra por tienda. Un recorte nunca toca a las Agotadas.
- Con `INVENTARIO_SEGUN_VIDA_UTIL` solo cuenta el inventario que se vende antes de vencer.
- OC de Siesa (`Compras_Transito.xlsx`) clasificadas: en tránsito, atrasada (≤ 3 días), recibida
  según Stock, vencida. Solo las que cuentan se restan.
- Defaults en `core/compras_params.py`; se sobrescriben con las hojas Parametros, Dias de compra y
  Calendario de `Maestros Fruver.xlsx` o en pantalla (app). **La compra no cambia la distribución.**

## Maestros (`core/maestros.py`)

`Maestros Fruver.xlsx` concentra todo lo que se mantiene a mano. ESTADO / DIA DE COMPRA del
Portafolio se calculan desde STATUS y el MAX de Tiendas x Item desde BD_MAX (primera coincidencia;
sin coincidencia = DESCONTINUADO / MAX 0), así que funciona aunque Excel no haya recalculado.

## Reglas que no se negocian

- Cero residuo: todo el stock del CEDI se reparte. Solo cajas enteras.
- Los Excel de entrada nunca se modifican; `core/` trabaja en memoria.
- Toda anomalía de datos se reporta como alerta (hoja Alertas), no se descarta en silencio.
- Cambiar umbrales o reglas de negocio solo con confirmación del usuario. Correr `pytest` tras cada cambio.

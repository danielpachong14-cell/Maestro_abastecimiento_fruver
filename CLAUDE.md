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
   `contains` (`'INACTIVO'` contiene `'ACTIVO'`); cualquier otro valor, aunque sea un error de
   tipeo (`'INCATIVO'`), cuenta como no activo. Filtra Portafolio y Tiendas×Ítem; en Stock
   `ESTADO DEL PRODUCTO` **no** bloquea (solo alerta): si hay existencia física, se distribuye.
5. La elegibilidad sale **solo** de Tiendas×Ítem (`ESTADO = ACTIVO`); Celes «Aplica para
   Distribución Automática?» se ignora. Tiendas es inner: tienda que no está, no recibe.

## Distribución (`core/algorithm.py`)

- `inventario_efectivo = existencias + tránsito`: existencias = `Cant. disponible` de Inventario Tiendas
  (Siesa, suma de sub-bodegas, en unidades exactas; sin fila = 0), tránsito = «(-) inventario de traslado en
  proceso» de Celes menos lo que Siesa ya recibió (`transito_pendiente`). No «(=) Inventario Total».
  `dias_proyectados = inventario_efectivo / consumo_diario`. Lo que se reparte, en **cajas enteras**.
- Prioridad por grupo espejo: AGOTADA (< `MIN_STOCK_AGOTADO` = 0.3 cajas) → SAFETY (< 0.7) →
  REPOSICIÓN (< `TARGET_DAYS` = 3 días) → CUBIERTA. La Fase 1 ordena solo por consumo (mayor
  primero); los niveles AGOTADA/SAFETY los usa la Fase 2.
- **Excluidas del reparto normal**: `bloqueado_por_max` (existencias + tránsito ≥ `MAX` de
  Tiendas×Ítem; `MAX = 0` = sin tope) y `sin_match_celes` (sin fila en Celes). Solo la Fase 5 nivel 2
  puede darles cajas, si no queda otro destino. Se listan en la hoja Novedades.

| Fase | Qué hace |
|---|---|
| 1 | Completar `TARGET_DAYS`, por venta: de mayor a menor consumo, en rondas (todas reciben su 1.ª caja antes que alguien la 2.ª), máximo `MIN_CAJAS_INICIAL` = 2 cajas por tienda |
| 2 | Garantizar el agotado: con lo que sobre, 1 caja a cada tienda que salió de F1 con 0 cajas: primero todas las AGOTADAS, luego las de SAFETY (mayor consumo primero) |
| 3 | Renovación por vida útil: 1 caja (`CAJAS_RENOVACION`) si el último ingreso es más viejo que `VIDA UTIL EXHIBICION` y aún hay inventario; no si hay tránsito, ya recibió en F1/F2 o un SKU espejo tiene ingreso fresco |
| 4 | Excedente: 1 caja por tienda por ronda, peso `consumo / días_actuales`, sin pasar `TOPE_EXCEDENTE` = 7 días ni `MAX_CAJAS_POR_ITEM` = 3 |
| 5 | Cero residuo: lo que quede se reparte igual (única fase que supera el tope). Mucho volumen aquí = sobre-compra |

Grupos espejo (hoja Espejo): la necesidad de F1 y la elegibilidad/peso de F4 se calculan con el
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

## Decisiones de negocio vigentes (no cambiar sin consultar)

- **MAX**: se compara contra existencias + tránsito; el corte es `>= MAX`, sin redondear. Funciona como
  interruptor (si no está bloqueada, la limita `MAX_CAJAS_POR_ITEM`) y aplica por SKU, no por grupo espejo.
- **Orden Fase 1 / Fase 2** (24-nov-2026): primero las que más venden (F1, completar `TARGET_DAYS` días) y luego
  agotado → seguridad (F2, solo con el sobrante). Aceptado que, con stock escaso, tiendas pequeñas
  agotadas o en seguridad queden sin caja (simulación del 23-nov: 69 agotadas sin caja vs 16 antes).
- **Sin datos en Celes**: no se le envía (llegaría como AGOTADA por falta del dato) y no se compra
  (va a la hoja «Sin Pronóstico»).
- Stock con estado distinto de `ACTIVO` (p. ej. PRODUCTO PRUEBA): se distribuye, pero no se compra.
- Duplicados: en Stock se suman las cajas; en Tiendas×Ítem prevalece el MAX más restrictivo y el MIN
  más alto; en Tiendas se conserva la primera fila. Todo con alerta.
- `store_name` sale siempre de Tiendas, no de Celes (Celes nombra distinto algunas tiendas).
- Las tiendas de «No generar pedido» no reciben distribución, pero sí entran a la compra (siguen vendiendo).
- `MIN` de Tiendas x Item = exhibición en canastillas (vacío = 1). Manda la exhibición: el MIN se compra
  aunque haya merma o consumo 0, y no lo limita la vida útil.
- **Proyección por lotes (FIFO)**: existencias, tránsito y pedido de hoy se venden día a día desde el lote
  más viejo; con `INVENTARIO_SEGUN_VIDA_UTIL` un lote deja de contar al pasar su vida útil, y la venta
  que no alcanza a cubrirse se pierde. Lo que está en camino (OC + stock CEDI que no se repartió) se
  asigna por prioridad: Agotada → Exhibición → Reposición.
- **CADA N DIAS**: se revisa a diario. Compra N días de cobertura, sin suavizado, solo si la red llega
  a la compra por debajo de su objetivo; si no, compra 0.
- **OC de Siesa**: cuentan las que están en tránsito y llegan antes que la compra de hoy, y las
  atrasadas hasta 3 días. No cuentan las cerradas, las de otra bodega, los ítems inactivos ni las
  vencidas. «Recibida según Stock» = el Stock tiene `Fecha última entrada` posterior a la entrega,
  porque Siesa no cierra la línea al recibir.
- **Existencias de tienda** (27-nov-2026): salen de `Cant. disponible` de Inventario Tiendas (Siesa), no de
  Celes, en distribución y compra (mismo helper `resumen_inventario_tiendas`). Exactas: Siesa redondea a 2
  decimales y se recupera la unidad entera, `round(cajas × Factor U.M.) / Factor U.M.` (factor ≤ 100;
  2,92 × 36 → 105 → 2,916667). Las de Celes quedan de referencia (`inventario_tienda_celes`).
- **Tránsito ya recibido** (27-nov-2026): Celes es una foto anterior a Siesa (26 contra 27-nov). Si Siesa tiene
  un ingreso posterior a «Última Fecha de Ingreso» de Celes, se descuenta del tránsito lo que subieron las
  existencias (tope: el tránsito); si no, el tránsito queda completo. Validación del 27-nov: 295 dobles conteos
  eliminados y 0 restantes; el efectivo coincide con el total de Celes en 2.876 de 2.927 pares (el resto
  son ventas o ingresos entre las dos fotos, y vale Siesa). Alertas: tránsito recibido y diferencias > 0,5.
  Corrida final del 27-nov (10 tiendas en «No generar pedido», antes 16): alerta de 310 pares / 353 cajas de
  tránsito ya recibido, 35 diferencias > 0,5; 1.056 cajas repartidas (CEDI en 0) y compra de 1.515 cajas.
- **Parámetros de distribución** (26-nov-2026): `TARGET_DAYS` = 3 días (antes 5) y `TOPE_EXCEDENTE` = 7
  días (antes 8).
- «Resumen Ítems» muestra F1 a F5 (con `F5 Cero residuo`, sin %), así la fila suma «Total Cajas».
- Columna «Portafolio» (CLUSTERIZACIÓN del Portafolio: BASICO / EXTENDIDO / NEVERA F), solo informativa:
  sale en Distribución, Resumen Ítems, Novedades y hojas por tienda, y en la compra (antes «Cluster»).
- Σ compra por tienda = compra general del ítem, siempre. No se escribe ningún log en `Output/`.
- Si un Excel de salida está abierto, `run.py` avisa y termina con código 1 (sin traceback).
- Una corrida asume que `Input/` no cambia mientras corre: si se editan los Excel en paralelo, el
  resultado cambia.

## Pendientes

1. Base de exhibición (`MIN`) por tienda. 2. Factores de venta por día de la semana (hoy 1,0).
3. CADA N DIAS con fecha ancla, si hace falta. 4. Dos semanas en modo sombra para calibrar
`UMBRAL_REDONDEO`, `ALFA_SUBIDA` y el MIN. 5. Validar el tratamiento del puente en el primer lunes
festivo real (11-ene-2027).

Historial completo (auditorías, casos reales, cifras de cada corrida, guía de usuario):
`git show cfec81a:"archivo_docs/Distribucion-Fruver/distribuidor-fruver/CLAUDE.md"` (y las demás
rutas de `archivo_docs/` en ese commit).

## Reglas que no se negocian

- Cero residuo: todo el stock del CEDI se reparte. Solo cajas enteras.
- Los Excel de entrada nunca se modifican; `core/` trabaja en memoria.
- Toda anomalía de datos se reporta como alerta (hoja Alertas), no se descarta en silencio.
- Cambiar umbrales o reglas de negocio solo con confirmación del usuario. Correr `pytest` tras cada cambio.

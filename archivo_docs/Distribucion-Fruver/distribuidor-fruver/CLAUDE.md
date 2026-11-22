# Distribuidor Fruver — Isimo

Herramienta interna de distribución automática del inventario Fruver del CEDI a las
tiendas activas. Procesa los Excel de `Input/` (3 del día + el libro único de maestros +
las OC en tránsito) y genera un archivo de pedidos donde el inventario disponible queda en 0. Desde nov-2026, en la
misma corrida, calcula también la **compra al proveedor** (por tienda y general) — ver
«Módulo de compras» al final.

## Comandos

```bash
source venv/bin/activate          # activar entorno virtual
pip install -r requirements.txt   # instalar dependencias
streamlit run app.py              # iniciar la aplicación (http://localhost:8501)
PYTHONPATH=. pytest tests/ -v     # correr tests unitarios
```

## Tech Stack

Python 3.11+ · Streamlit ≥1.35 · pandas ≥2.2 · openpyxl ≥3.1 (ver `requirements.txt`
para las versiones mínimas exactas).

## Arquitectura

```
7+1 Excel → loader.py → preprocessor.py → algorithm.py → exporter.py → distribución.xlsx
                                                  │ df_output (pedido de hoy)
   + parámetros (Maestros) / Compras_Transito     ▼
              → compras_preprocessor.py → compras.py → compras_exporter.py → compra.xlsx
```

- `app.py` — UI Streamlit: upload de los archivos, botón de ejecución, descarga del resultado.
- `core/loader.py` — Carga y valida columnas requeridas de cada Excel (hoja Celes = `Items`).
- `core/preprocessor.py` — Limpieza, normalización de claves, merge en un DataFrame
  tienda × ítem, y detección de problemas de calidad de datos (ver «Alertas de
  calidad de datos» más abajo). Retorna `(df_merged, alertas)`.
- `core/algorithm.py` — Motor de distribución con prioridades y garantía de 0 residuo.
- `core/exporter.py` — Genera el Excel de salida (5 hojas: Distribución, Resumen,
  Resumen Ítems, Novedades, Alertas).
- `core/maestros.py` — libro único `Maestros Fruver.xlsx` (lectura, escritura y migración desde
  los archivos sueltos). Ver «Archivos de entrada».
- `core/calendario.py`, `core/compras_params.py`, `core/compras_preprocessor.py`,
  `core/compras.py`, `core/compras_exporter.py` — módulo de compras (ver al final).

## Archivos de entrada

**Formato vigente (nov-2026): 5 archivos en `Input/`.** Los que se descargan cada día de los
sistemas van sueltos; todo lo que se mantiene a mano vive en **un solo libro**,
`Maestros Fruver.xlsx` (`core/maestros.py`, se carga con `loader.load_inputs`):

| Archivo / hoja | Clave interna | Obligatorio |
|---|---|---|
| `Stock.xlsx` | `stock` | Sí (cada día) |
| `Celes.xlsx` | `celes` | Sí (cada día) |
| `Inventario Tiendas.xlsx` | `inventario_tiendas` | Sí (cada día; fecha del último ingreso — Fase 3) |
| `Compras_Transito.xlsx` (export de OC de Siesa) | `compras_transito` | No (compras; sin él, alerta de compra doble) |
| `Maestros Fruver.xlsx` › `Portafolio` | `portafolio` | Sí (vida útil, costo; ESTADO y DIA DE COMPRA salen de STATUS) |
| › `STATUS` | (parámetros por ítem) | Sí: ítems que se compran (STATUS), DIA DE COMPRA, crecimiento/múltiplo/mínimo |
| › `Tiendas` | `tiendas` | Sí |
| › `Tiendas x Item` (+ `BD_MAX`) | `tiendas_item` | Sí (elegibilidad, MIN de exhibición; MAX calculado desde BD_MAX) |
| › `Espejo` | `espejo` | Sí |
| › `No generar pedido` | `excluidas` | No (si tiene filas) |
| › `Parametros` · `Dias de compra` · `Calendario` | `parametros_compra` | No (sin ellas, valores por defecto) |

**El sistema no depende del recálculo de Excel**: `leer_maestros` calcula ESTADO y DIA DE COMPRA
del Portafolio desde STATUS y el MAX desde BD_MAX (misma lógica que los XLOOKUP del libro: primera
coincidencia; sin coincidencia = DESCONTINUADO / 0). Verificado el 2026-11-19: MAX calculado = MAX
de Excel en las 5.207 filas, y la distribución con el libro nuevo sale idéntica (1.318 cajas, mismas
tienda × ítem × fase) a la de los archivos sueltos. Las fórmulas siguen en el libro solo para que el
usuario las vea en Excel (abre sin reparación y sin errores, verificado con Excel).

**Formato anterior (respaldo)**: si `Input/` no tiene `Maestros Fruver.xlsx`, `run.py` usa los
archivos sueltos de siempre con `loader.load_files` (que no cambió) y `load_purchase_files`
(`DB_Portafolio Fruver.xlsx`, `DB_Tiendas.xlsx`, `DB_Tiendas Por itmes y portafolio.xlsx`,
`DB_ProductosEspejo.xlsx`, `Tiendas_No generar pedido.xlsx`, `Parametros_Compra.xlsx`). Los de
esta máquina se movieron a `../Respaldo_Input_20261119/` (con sus hojas de trabajo y backups, que
el libro nuevo no copia). Para rehacer el libro desde ellos: `maestros.hojas_desde_sueltos(...)` +
`escribir_maestros(...)` (con `activar=(2247,)` se agregó el limón X 1000 G a STATUS).

## Normalización de claves de cruce (CRÍTICO)

Los archivos reales NO cruzan con un merge directo. El preprocessor normaliza automáticamente:

1. **Tienda**: Celes `Código de Bodega` = `BOS03`; Base de Tiendas `COD SIESA` = `S03`.
   Se quita el prefijo `BO` → `normalize_store()`.
2. **Ítem**: Stock `Item` = `'0000155'` (texto con ceros); el resto usa `155` (entero).
   Se normaliza todo a entero → `normalize_item()` (con fast-path vectorizado cuando
   la columna ya es entera — Portafolio, Tiendas×Ítem, Celes y Espejo lo son en
   producción; solo Stock necesita el parseo por fila).
3. **Fila basura**: Stock incluye una fila totalizadora (`'Gran total'` u otra
   variante que contenga "total") que se elimina.
4. **Estado activo**: cualquier columna `ESTADO` con formato `'NNN - ESTADO'` se
   compara por el ÚLTIMO segmento tras el guion, exacto e insensible a
   mayúsculas — **no** con `str.contains`. Ver «Bug corregido: `_is_active`» más
   abajo. Se usa para **filtrar elegibilidad** en Portafolio y Tiendas×Ítem, pero
   **no** en Stock (`ESTADO DEL PRODUCTO`) — ver siguiente sección.

```
stock.Item (norm int)   ↔  tiendas_item.DB_Portafolio_Fruver.ITEM (norm int)  ↔  celes.Código de Producto
tiendas_item.COD SIESA  ↔  celes.Código de Bodega sin prefijo 'BO'
```

## Bug corregido: `_is_active` (auditoría 2026-07)

`_is_active()` filtraba con `series.str.contains('ACTIVO', case=False)`. Como
`'INACTIVO'` **contiene** la subcadena `'ACTIVO'`, ese filtro clasificaba como
activas las filas marcadas `INACTIVO` — confirmado contra datos reales: 955 de
4878 filas de Tiendas × Ítem y 23 de 84 de Portafolio Fruver. El fix compara el
último segmento tras el guion (`'NNN - ESTADO'`) con igualdad exacta, no
contención. Este era probablemente el bug de mayor impacto del sistema: hacía que
ítems descontinuados en el portafolio de una tienda igual recibieran pedido.

## `ESTADO DEL PRODUCTO` en Stock — informativo, NO bloquea (decisión de negocio)

`loader.py` exige la columna, pero `preprocessor.py` **ya no filtra por ella**:
toda existencia física en el CEDI se distribuye sin importar su
`ESTADO DEL PRODUCTO` (pruebas, dados de baja, etc.) — decisión explícita del
negocio (ver caso del ítem 1815, `'005 - PRODUCTO PRUEBA'`, que debe distribuirse
igual que uno vigente si tiene stock). Lo único que cambia: si un ítem con
`cajas_disponibles > 0` no tiene `ESTADO DEL PRODUCTO` exactamente `ACTIVO`, se
genera una alerta `ADVERTENCIA` (no bloqueante) para que el negocio pueda
revisarlo — mismo patrón que la validación cruzada con Portafolio, más abajo.

Nota histórica: durante la auditoría técnica de 2026-07 este campo llegó a
filtrarse (excluir del todo los ítems no-`ACTIVO`) como corrección de un bug real
detectado con el ítem 1815. El negocio revisó esa decisión y determinó que el
estado no debe bloquear el envío — solo alertar. Si vuelve a surgir la pregunta,
esta es la decisión vigente.

## Validación cruzada con Portafolio Fruver

`DB_Portafolio Fruver.xlsx` (columnas `ITEM`, `CLUSTERIZACIÓN`, `ESTADO`, `VIDA UTIL EXHIBICION`) ya no se
descarta tras cargarse: se usa para dos chequeos que generan alertas (no bloquean
la distribución):

1. **Ítem nuevo no catalogado**: ítem con stock en CEDI cuyo código no aparece en
   absoluto en Portafolio.
2. **Ítem descontinuado con stock**: ítem marcado inactivo en Portafolio (según
   `_is_active`) que aún tiene existencia > 0 en el CEDI.

`CLUSTERIZACIÓN` no se usa todavía en el cálculo de distribución.

**Vida útil (nov-2026).** El archivo es ahora una tabla (hoja `DB_Portafolio_Fruver`; el loader
lee la primera hoja, el nombre no importa) con tres columnas de días:

| Columna | Uso hoy | Notas |
|---|---|---|
| `VIDA UTIL EXHIBICION` | **Fase 3** (también `vida_util_portafolio` en el df) | Requerida. Antes se llamaba `VIDA UTIL`: el loader sigue aceptando ese nombre. Vacía o 0 = el ítem se omite |
| `DIAS CONSERVACION CEDI` | Ninguno (columna `vida_util_cedi` en `df_merged`) | Opcional. Para el futuro análisis de compras |
| `TOTAL VIDA UTIL` | Ninguno (columna `vida_util_total`) | Opcional. Fórmula `EXHIBICION + CEDI`; si llega vacía (Excel sin recalcular) se reconstruye con esa suma |

Alertas de calidad: `TOTAL` distinto de `EXHIBICION + CEDI`, días negativos y celdas no numéricas.
Solo la vida útil de **exhibición** se compara con los días desde el último ingreso a la tienda: los
días en CEDI ocurren antes de llegar a la tienda.

## Parámetros del algoritmo (core/algorithm.py)

```python
TARGET_DAYS       = 3.0   # días MÍNIMOS de inventario proyectado objetivo por tienda
MIN_STOCK_AGOTADO = 0.4   # inventario_efectivo < 0.4 cajas → AGOTADA (prioridad máxima)
MIN_STOCK_SAFETY  = 0.9   # inventario_efectivo < 0.9 cajas → STOCK SEGURIDAD (confirmado por el negocio, nov-2026)
MIN_CAJAS_INICIAL = 2     # número de rondas (cap 1, 2) en Fase 1 — el nombre viene de
                          # versiones anteriores; hoy es el cap MÁXIMO por ronda, no un mínimo
MAX_CAJAS_POR_ITEM = 3    # tope duro: máximo que una tienda recibe por ítem (todas las fases)
TOPE_EXCEDENTE    = 5     # máximo días que puede acumular una tienda del excedente
CAJAS_RENOVACION  = 1     # Fase 3: cajas enviadas a una tienda con producto vencido por vida útil
UMBRAL_VENTA_RENOVACION = 0.0  # Fase 3: venta esperada mínima (consumo × vida útil, en cajas); 0 = sin umbral
UMBRAL_CONCENTRACION_SIN_CONSUMO = 5  # cajas: ver «Reparto equitativo» más abajo
```

**Estos son los valores REALES en el código** — si ves otro valor documentado en
algún lugar, el código (`core/algorithm.py`) es la fuente de verdad; repórtalo
para corregir la doc.

## Decisiones de negocio vigentes (auditoría 360, 2026-11-12)

- **Umbrales confirmados:** `MIN_STOCK_AGOTADO = 0.4` y `MIN_STOCK_SAFETY = 0.9` cajas
  (en cajas absolutas, no en días). Valores previos de la documentación (0.2/0.5,
  0.4/0.7) quedan obsoletos.
- **Cero residuo se mantiene tal cual:** la Fase 5 sigue repartiendo todo el
  sobrante del CEDI aunque supere `TOPE_EXCEDENTE`. En la corrida del 2026-11-12
  fue el 47 % de las cajas (media histórica ≈ 53 %); se interpreta como señal de
  sobre-compra, no como defecto del algoritmo.

## Duplicados y cantidades fraccionarias (auditoría 360, 2026-11-12)

`preprocessor.build_distribution_df` ya no descarta filas repetidas en silencio:

- **Stock**: un ítem repetido **suma** sus cajas (descripción/U.M. de la primera fila) y
  genera una alerta por ítem. Cantidades fraccionarias: se redondean hacia abajo y se
  alerta con el total de cajas decimales descartadas.
- **Tiendas × Ítem**: un par tienda×ítem repetido se reduce a una fila y, si trae `MAX`
  distintos, prevalece el **más restrictivo** (el menor `MAX > 0`; si todos son 0, sin
  tope). Una alerta agregada reporta cuántos pares y cuántos con `MAX` distinto.
- **DB_Tiendas**: un `COD SIESA` repetido conserva la primera fila y alerta (antes el
  merge duplicaba los pedidos de esa tienda).
- **Reparto equitativo de Fase 5** (portafolio sin consumo): llena primero hasta
  `MAX_CAJAS_POR_ITEM` por tienda y solo lo supera si ya no queda alternativa.
- **Nivel 2 de Fase 5**: una tienda bloqueada por `MAX`/sin datos Celes entra a la
  rotación cuando las libres ya están en `MAX_CAJAS_POR_ITEM` (no espera a que las
  libres se llenen más allá de ese máximo). Comportamiento vigente, cubierto por
  `test_bloqueadas_reciben_solo_despues_de_las_libres`.
- Limitación conocida: `df_merged` solo contiene ítems con stock en CEDI, así que el
  inventario en tienda de un hermano espejo **sin stock** no entra a las métricas del
  grupo.

## Lógica de inventario

Todo se trabaja en **cajas completas** (enteros). Nunca en unidades.

```
inventario_efectivo = inventario_tienda + inventario_transito   (clip a >= 0)
dias_proyectados    = inventario_efectivo / consumo_diario
cajas_necesarias    = ceil( max(0, TARGET_DAYS × consumo_diario − inventario_efectivo) )
```

`inventario_tienda` = Celes.`'Inventario Disponible en esta Tienda (Unidades
de Distibución)'` (Existencias); `inventario_transito` = Celes.`'(-) inventario
de traslado en proceso'`. Celes.`'(=) Inventario Total'` se conserva como
columna informativa (`inventario_total` en `df_merged`) pero ya NO participa en
esta fórmula — histórico del valor usado:

- **Hasta jul-2026**: `inventario_tienda + inventario_transito`.
- **Jul-2026 → sep-2026**: se cambió a `Celes.'(=) Inventario Total'` directo,
  tras detectar que `inventario_transito` quedaba en 0 en el 18.7% de las
  filas tienda-ítem reales aunque sí había mercancía en tránsito (el efecto
  solo podía ser sobre-pedir a tiendas que ya tenían pedido en camino, nunca
  lo contrario).
- **Sep-2026 (vigente)**: se revirtió a `inventario_tienda + inventario_transito`
  tras un caso real reportado por el usuario (tienda Campín, ítem 1210):
  `'(=) Inventario Total'` mostraba 9.33 cajas mientras Existencias + Tránsito
  daba 3.33 (1.33 + 2) — es decir, esa columna de Celes también puede venir
  **inflada** frente a la suma real, no solo en 0. El negocio decidió priorizar
  no sobrestimar el inventario de la tienda; el riesgo conocido del período
  anterior (transito=0 cuando sí hay mercancía en camino) sigue existiendo con
  esta fórmula.

  Impacto medido corriendo el pipeline completo dos veces sobre el mismo
  `Input/` (fórmula nueva vs. vieja, auditoría 2026-09): de 3,032 filas
  tienda-ítem, 643 (21%) tenían `inventario_efectivo` distinto — en las 643,
  la fórmula vieja daba un valor mayor o igual (nunca menor) que la nueva,
  confirmando que en estos datos `'(=) Inventario Total'` solo sobrestima o
  coincide, no subestima. A nivel de pedido final, 736 filas tienda-ítem (89
  tiendas, 43 ítems) terminan con una cantidad de cajas distinta; el total
  de cajas repartidas es idéntico (806 en ambos casos) — el cambio
  **redistribuye** entre tiendas, no altera cuánto sale del CEDI.

  **Investigación del origen del 9.33 (caso Campín/1210, auditoría 2026-09)**:
  se extrajo la fila completa de `Celes.xlsx` (hoja `Items`) para esa
  combinación tienda-ítem para intentar reconstruir `'(=) Inventario Total'`
  a partir de las demás columnas del mismo archivo. No fue posible: ni
  `Existencias (1.33) + inventario de traslado en proceso (2)` ni sumar
  `Inventario de Exhibición` ni `Inventario Ordenado al CEDI (98 — esta es
  una cifra de CEDI completo, no de esta tienda)` reproducen 9.33 con una
  suma simple — `'(=) Inventario Total'` sale de una fórmula interna de
  Celes no expuesta como columnas separadas en este extracto. Dato adicional
  que refuerza la decisión: la misma fila trae `Capacidad máxima de la
  tienda = 1` caja para ese ítem — un inventario de 9.33 es operativamente
  incoherente con esa capacidad, mientras que 3.33 (Existencias + Tránsito)
  al menos es plausible. Conclusión: `'(=) Inventario Total'` de Celes no es
  una fuente confiable para este cálculo — ni por diseño transparente (no se
  puede auditar con las columnas disponibles) ni por consistencia con otros
  campos de la misma fila (capacidad de la tienda).

`Cant. disponible` en Stock ya viene en cajas — no se divide por Factor U.M.
`U.M.` (ej. `CJ20`) es solo referencia para el output; no se usa en cálculos.
Los valores numéricos de Celes se recortan a `>= 0` (`.clip(lower=0)`) como defensa
ante datos negativos aguas arriba, que de otro modo producirían pesos negativos en
Fase 4.

## Grupos de productos espejo

`DB_ProductosEspejo.xlsx` (columnas `grupo_id`, `item_id`) agrupa SKUs que son el
mismo producto físico bajo distinto código — compiten por la misma demanda en
tienda y no deben duplicar reposición ni excedente entre sí. Un ítem sin entrada en
este archivo queda en un grupo de tamaño 1 (comportamiento idéntico al de un ítem
sin espejo). Afecta tres partes del algoritmo:

- **Prioridad** (`get_priority`): se calcula sobre `inventario_efectivo_grupo` /
  `dias_proyectados_grupo` (suma del grupo en esa tienda), no sobre el ítem individual.
- **Fase 1/2** (`_apportion_grupo_targets`): la necesidad de `TARGET_DAYS` se
  calcula UNA VEZ por grupo y se reparte entre los ítems del grupo (piso +
  corrección Hamilton) — aplicar `ceil()` por ítem por separado sobreprovisiona
  (bug corregido en la auditoría 2026-07: hasta 300% de sobreprovisión con 3 SKUs
  al 33% cada uno).
- **Fase 4** (`_fase4_grupal`): elegibilidad, `TOPE_EXCEDENTE` y peso se calculan
  sobre el inventario/consumo combinado del grupo; cada ítem sigue entregándose
  desde su propio stock CEDI (`MAX_CAJAS_POR_ITEM` sigue por SKU). Garantiza 1 caja
  a cualquier ítem del grupo con 0 unidades en tienda antes de repartir proporcional.

## Lógica de prioridades

```
Priority 3 (AGOTADO):      inventario_efectivo_grupo < 0.4 cajas → mínimo 1 caja, primera en recibir
Priority 2 (SAFETY):       inventario_efectivo_grupo < 0.9 cajas → mínimo 1 caja
Priority 1 (REPOSICIÓN):   dias_proyectados_grupo    < 3.0       → cajas para llegar a 3 días
Priority 0 (CUBIERTO):     dias_proyectados_grupo    ≥ 3.0       → solo recibe si sobra inventario
```

## Las cinco fases (Fase 1, 2, 3, 4, 5)

**Numeración (nov-2026):** al agregar la Fase 3 (renovación por vida útil) entre la
Fase 2 y el excedente, el excedente voluntario pasó de Fase 3 a **Fase 4** y el cero
residuo de Fase 4 a **Fase 5**. Las columnas de salida siguen esa numeración
(`cajas_fase3` = renovación; `cajas_fase4` = excedente; `cajas_fase5` = cero residuo).
Los párrafos históricos de abajo ya usan los números nuevos.

Historia de nombres (auditoría 2026-07 / 2026-09):

Renombradas en la auditoría 2026-07 porque son pasos secuenciales con timing y
alcance de negocio distintos (Fase 2 = cobertura obligatoria de `TARGET_DAYS` por
ítem; Fase 4 = distribución voluntaria del excedente, coordinada a nivel de grupo
espejo), no variantes del mismo paso. La Fase 5 (antes el "Paso B" dentro de la
Fase 4) se separó en su propia fase en la auditoría 2026-09: a partir de un caso
real reportado por el usuario (ítem 1561, Aguacate Hass — 73 de 89 tiendas
recibieron caja aunque 77 ya estaban en o sobre `TOPE_EXCEDENTE` *antes* del
pedido), quedó claro que mezclar "reparto sano bajo el tope" y "cero-residuo
forzado que ignora el tope" en la misma columna (`cajas_fase4`) ocultaba cuánta
distribución de un ítem era en realidad sobre-stock inevitable por exceso de
compra frente al portafolio real, no reparto normal.

**Fase 1** — rondas con cap creciente (1 → `MIN_CAJAS_INICIAL`): todas las tiendas
elegibles reciben su 1.ª caja antes de que cualquiera reciba su 2.ª, evitando que
tiendas de alto consumo acaparen cuando el stock es escaso.

**Fase 2** — completa `TARGET_DAYS` de forma proporcional cuando el stock no
alcanza para todas (corrección Hamilton para residuos enteros).

**Fase 3** — renovación por vida útil. Ver «Fase 3: renovación por vida útil» más
abajo (regla, datos y fundamento).

**Fase 4** — el sobrante se reparte por rondas, **proporcional a
`consumo_diario / días_actuales`** (equivalente a `consumo_diario² / stock_actual`)
entre las tiendas elegibles (`consumo > 0`, que tras recibir una caja más sigan
bajo `TOPE_EXCEDENTE` días, y que no superen `MAX_CAJAS_POR_ITEM` cajas por
entrega), con **máximo 1 caja por tienda por ronda** (no hasta
`MAX_CAJAS_POR_ITEM` de una vez) y corrección Hamilton:

```
dias_actuales = (inventario_efectivo + cajas_ya_asignadas) / consumo_diario
peso_tienda   = consumo_diario / dias_actuales
```

Una tienda con `dias_actuales == 0` (la más urgente posible dentro de las
elegibles) recibe el mayor peso, no cero — el cálculo usa un epsilon pequeño en
vez de dividir literalmente por 0 (bug corregido en la auditoría 2026-07: antes
`dias_actuales==0` se traducía en peso NULO por el manejo de la división,
invirtiendo la prioridad que el propio diseño documenta).

**Por qué 1 caja por ronda y no hasta el tope de una vez** (bug corregido en
la auditoría 2026-07, a partir de un caso real reportado por el usuario —
ítem 2001, tienda S53): antes, cuando solo 1-2 tiendas quedaban elegibles con
mucho sobrante, `_reparto_proporcional` les daba hasta `MAX_CAJAS_POR_ITEM`
cajas en una sola ronda sin volver a chequear `TOPE_EXCEDENTE` entre la 1ª y
la 3ª caja — terminaban con 10+ días de sobre-stock mientras una 3ª tienda,
apenas por encima del tope, se quedaba en 0. Con el cap de 1/ronda, el mismo
bucle de rondas (ya existente) vuelve a evaluar elegibilidad tras cada caja, y
el sobrante se reparte primero entre más tiendas antes de que cualquiera
reciba una 2ª.

**Por qué ese peso y no "1 caja por tienda" ni "proporcional al consumo a secas"**:
repartir la MISMA CANTIDAD DE CAJAS reparte cantidades MUY DISTINTAS de DÍAS
(`Δdías ≈ cajas / consumo`), y el consumo puede variar mucho entre tiendas — la
tienda lenta gana muchos más días por caja que la rápida, generando el desbalance
opuesto al deseado. Ponderar por `consumo_diario / días_actuales` combina ambas
señales — cuánto vende y qué tan atrás va respecto al resto — y produce
`Δdías_i = k / días_i`: la tienda más atrasada gana más días por ronda, cerrando
la brecha en vez de mantenerla.

**Fase 5** — red de seguridad de cero residuo. Fase 4 **nunca** le da a una
tienda una caja que la deje por encima de `TOPE_EXCEDENTE`; si todas las
tiendas elegibles llegan a `MAX_CAJAS_POR_ITEM` o a `TOPE_EXCEDENTE` (caso
extremo: el CEDI recibió más stock del que el portafolio puede absorber sin
sobre-stockearse) y aún sobran cajas, Fase 4 termina con remanente. La Fase 5
existe solo para ese remanente — es la ÚNICA fase que puede pasar el tope de
días — y reparte lo que quede por menor `días_actuales`, en dos niveles:
(1) primero solo entre tiendas que aún no llegan a `MAX_CAJAS_POR_ITEM`;
(2) solo si TODAS las tiendas con consumo ya están en ese máximo y aún sobran
cajas — matemáticamente imposible repartir sin superarlo — se ignora también
ese límite, con la misma filosofía que ya aplica para `TOPE_EXCEDENTE`: cero
residuo tiene prioridad, pero solo cuando es la única opción. Antes de la
auditoría 2026-07 el nivel (1) no existía y este mecanismo ignoraba
`MAX_CAJAS_POR_ITEM` siempre — bug real confirmado en producción (ítem 2473:
dos tiendas terminaban con 4 cajas en vez del máximo de 3, mientras otras con
espacio real recibían menos de lo que podían absorber).

**Orden dentro de la Fase 5 (nov-2026):** el nivel 1 reparte en dos pasadas. **Pasada 1**: solo las
tiendas SIN caja de renovación (Fase 3), 1 caja cada una, por menor `días_actuales`. **Pasada 2** (solo
si aún sobra): todas las tiendas, renovadas incluidas, por **mayor consumo diario** (desempate por
menos días), 1 caja por vuelta y con tope `MAX_CAJAS_POR_ITEM`. Así una tienda renovada solo recibe del
sobrante cuando todas las demás ya tienen su caja. Antes se ordenaba solo por días y una tienda con 1 caja
de Fase 3 podía llevarse además una de Fase 5 por 0.5 días de diferencia (caso ítem 1072, 16-nov: SB0 y
S86 con 2 cajas mientras SB1 y SE2 no recibían ninguna). Con los datos del 16-nov ningún ítem llega a la
2.ª pasada y el efecto sobre merma es casi nulo (110 de 112 cajas de Fase 5 igual se venderían fuera de
su vida útil con cualquier orden): la merma se resuelve comprando menos, no reordenando. Tests:
`test_fase5_no_da_caja_extra_a_la_tienda_ya_renovada_si_hay_otras_sin_caja`,
`test_fase5_segunda_pasada_ordena_por_mayor_consumo`.

**Por qué se mantiene separada de Fase 4**: la Fase 5 fuerza sobre-stock porque no hay alternativa
para vaciar el CEDI, no porque las tiendas lo necesiten. Sigue en `cajas_fase5` (df_output), pero
desde nov-2026 el Excel ya no la muestra (a pedido del usuario: sin información de sobre-envío).

### Reparto equitativo cuando falta consumo en el portafolio

Si el Paso B de una sola tienda concentraría `>= UMBRAL_CONCENTRACION_SIN_CONSUMO`
(5) cajas porque es la única con consumo registrado en un portafolio más amplio,
se activa reparto equitativo entre TODAS las tiendas del portafolio activo del
ítem en vez de concentrar en una sola, y se genera una alerta `ADVERTENCIA`.

## Fase 3: renovación por vida útil (nov-2026)

**Problema.** Celes reporta el inventario que la tienda *tiene en el sistema*, no el
que es vendible. Si el último ingreso de un ítem fue hace más días que su vida útil
y la tienda aún figura con inventario, lo más probable es que ese producto ya no se
pueda vender (vencido) y no se haya dado de baja. El motor lo cuenta como inventario,
la tienda queda "cubierta" y nunca se repone: la góndola se queda sin producto fresco.

**Regla** (`algorithm._fase3_renovacion`, entre Fase 2 y Fase 4; por tienda × grupo
espejo). Se envían `CAJAS_RENOVACION` (1) caja(s) si se cumple TODO:

1. `VIDA UTIL EXHIBICION > 0` en Portafolio (vacía o 0 = se omite).
2. `dias_desde_ingreso > vida_util`, con fecha válida y `inventario_tienda > 0`.
3. Sin tránsito (ningún SKU del grupo): el producto en camino ya renueva la góndola.
4. No recibió nada en Fase 1/2 (ningún SKU del grupo).
5. Ningún SKU hermano del grupo espejo tiene ingreso fresco (≤ vida útil).
6. **Venta esperada** `consumo_diario_grupo × vida_util >= UMBRAL_VENTA_RENOVACION`.
   **Hoy el umbral es 0 (sin filtro)**: decisión del negocio (nov-2026) — si el
   ingreso ya superó la vida útil SIEMPRE se renueva la caja, aunque la tienda venda
   poco. Es un parámetro: con 0.7 se evitarían las cajas que no se alcanzarían a
   vender antes de que venzan (ver «Fundamento»).
7. Tienda×ítem no excluida (MAX / sin datos Celes) y con stock en CEDI.

Solo usa el sobrante que dejó la Fase 2. **No aumenta lo que sale del CEDI**: esas
cajas igual saldrían por Fase 4/5 (cero residuo); cambia a dónde van. Si el stock no
alcanza, se prioriza por mayor venta esperada → más días de vencimiento → `store_code`,
y se alerta con las tiendas que quedaron sin caja. Con espejo, una sola caja por
tienda×grupo, del SKU con mayor `item_share_consumo` que tenga stock.

**Datos.** Los días se cuentan contra la **fecha de corte del archivo** (su fecha de
ingreso más reciente), no contra el reloj: el resultado no cambia si se corre otro
día con el mismo archivo, y un extracto viejo genera una alerta. `Inventario
Tiendas.xlsx` (extracto de Siesa, hoja `Inv Tiendas`): tienda = `C.O. bodega`
(coincide con `COD SIESA`), ítem = `Item`, fecha = `Fecha última entrada`. Un ítem con
varias sub-bodegas por C.O. toma el ingreso **más reciente**. Se usa solo para la
fecha: el inventario sigue saliendo de Celes (en cajas, igual que `Existencia`).

**Fundamento (hard discount).**

- *La caja es la unidad de exhibición*: el fruver se exhibe en su empaque y el surtido
  es corto y de alta rotación; el indicador que gobierna la merma es el *sell-through*
  de la caja dentro de su vida útil (`consumo × vida útil`). Con umbral 0 se prioriza
  la exhibición sobre la merma: en la corrida del 2026-11-12, 7 de las 20 renovadas
  esperan vender menos de 0.7 caja y 4 menos de 0.5 (merma esperada alta). Un umbral
  de 0.7 las excluiría; queda como palanca si la merma de renovación se vuelve un
  problema.
- *Inventario fantasma* (inventory record inaccuracy): un inventario que figura en
  sistema pero no es vendible congela la reposición automática. Dos remedios: corregir
  el registro (baja en tienda) o reponer igual. La fase repone; la hoja «Renovación
  Vida Útil» expone lo primero como lista de bajas pendientes para operaciones.
- *Venta perdida vs. merma* (newsvendor): el negocio eligió evitar la venta perdida
  (góndola sin producto fresco) aun a costa de algo de merma. Las tiendas de venta
  esperada baja quedan visibles en la hoja «Novedades» (situación «Renovación vida útil»; antes columna `Venta
  Esperada en Vida Útil`) como candidatas a revisión de surtido: un ítem que no rota
  una caja por ciclo de vida útil en una tienda puede no corresponder a su portafolio.
- *Ciclo*: el envío actualiza la fecha de ingreso y la regla puede volver a dispararse
  cada «vida útil» días si la tienda no vende; sin umbral de venta ese ciclo no se
  frena solo — la hoja de la fase permite detectar las tiendas reincidentes.

**Impacto del Portafolio nuevo** (nov-2026, mismo `Input/`; los 47 ítems con stock ya tienen
`VIDA UTIL EXHIBICION`, valores más cortos: mediana 4 días): 862 tienda×ítem con ingreso vencido →
**104 renovadas** (antes 20), 659 cajas y cero residuo intactos, Fase 5 de 293 a 210. De las 104,
94 esperan vender < 0.7 caja (merma alta); con umbral 0.5 serían 32 y con 0.7, 10. Las 392 candidatas
sin stock salen en **una sola alerta resumen** (detalle en la hoja «Novedades»).

**Impacto previo** (`Input/` del 2026-11-12, solo 9 ítems con vida útil de 47): 95
tienda×ítem con ingreso vencido → 20 renovadas + 3 vía el SKU espejo hermano (30 con
SKU espejo fresco, 13 con tránsito, 6 ya recibían en Fase 1/2 y **23 sin stock en
CEDI**: la Fase 1 ya había usado las pocas cajas de ítems como 1868, 3103 o 3351 y la
renovación solo usa el sobrante — salen en una alerta por ítem, priorizadas por venta
esperada). Total 659 cajas y cero residuo intactos; Fase 5 bajó de 312 a 293. Con
umbral 0.7 habrían sido 13 renovadas.

**Tránsito y doble envío (nov-2026).** La regla 3 es lo que evita enviar dos veces: hoy se
renueva, mañana el ingreso a la tienda sigue siendo el viejo (la caja aún no llegó), pero Celes ya
muestra lo enviado como tránsito (`(-) inventario de traslado en proceso`) y la tienda se omite.
Se evalúa por tienda × grupo espejo (el tránsito de cualquier SKU hermano también cuenta) y cualquier
valor > 0 bloquea. Evidencia con los Output reales (informe puntual, ya retirado del repo):
de lo pedido el 2026-11-12, el **97 %** figura hoy en tránsito (343 de 353); el de hace 2, 3 y 4
días baja a 40 %, 32 % y 30 % porque ya llegó.
Límite conocido: depende de que Celes registre el traslado; ~3 % de lo pedido ayer no aparecía.
Decisión del usuario: no se agrega una 2.ª guardia con el historial de Output.
`run.py` y la app muestran cuántas candidatas se omitieron por tránsito
(`exporter.resumen_renovacion_texto`).

Columnas de Celes revisadas y **descartadas** como señal de envío en camino: `Fecha Esperada de entrada
a Tienda` (viene en todas las filas: es la fecha estándar de entrega), `Pedido realizado en` y
`Cantidad Solicitada con Celes` (vacías) y `Última Fecha de Ingreso` (difiere de la de Siesa en
todas las candidatas y no es la recepción en tienda: decisión del usuario, se ignora; la referencia
es `Fecha última entrada` de `Inventario Tiendas.xlsx`).

**Salida.** Columna `F3 Renovación` en «Resumen Ítems» y situación «Renovación vida útil» en «Novedades» con
todos los candidatos, las cajas enviadas y el `Motivo` de cada decisión.

## Elegibilidad

- La elegibilidad tienda-ítem viene de **Tiendas × Ítem** (filas cuyo `ESTADO` sea
  exactamente `ACTIVO`, ver «Bug corregido: `_is_active`»).
- Se distribuye a **todas** las tiendas que aparecen en Celes para ese ítem.
  El campo `Aplica para DistribuciónAutomática?` se ignora (Celes no lo trae
  siquiera entre las columnas requeridas de `loader.py`).
- **DB_Tiendas** es la única fuente de tiendas activas: el cruce final con ella es
  `inner`, no `left` — tiendas cerradas/dadas de baja quedan excluidas aunque
  tengan datos de consumo o portafolio "activo".
- Una combinación elegible puede además quedar **fuera del reparto normal** por
  dos causas, unificadas en la columna `excluida_del_reparto`: el tope `MAX` de
  Tiendas × Ítem (ver «Tope `MAX` por tienda × ítem») o la falta de datos en
  Celes (ver «Exclusión por falta de datos en Celes»).

## Tope `MAX` por tienda × ítem

`DB_Tiendas Por itmes y portafolio.xlsx` trae una columna **`MAX`** con el
máximo de existencias que esa tienda puede tener de ese ítem. Es una columna
**calculada dentro del propio Excel**: `Hoja1` es una tabla (`Tabla1`) con

- `LLAVE` = `CONCAT([COD SIESA], [ITEM])` → `S961210`
- `MAX`   = `IFERROR(XLOOKUP([LLAVE], Tabla2[LLAVE], Tabla2[MAX],,0,1), )`

donde `Tabla2` vive en la hoja auxiliar **`BD_MAX`** (las filas que el negocio
mantiene a mano). El pipeline lee `Hoja1` y toma el valor ya resuelto.

### Reglas (decisiones de negocio, no negociables sin consultar)

1. **Se compara contra el inventario efectivo (Existencias + tránsito).**
   Igual que el resto del motor, lo que ya viene en camino cuenta como si
   estuviera en la tienda. **Cambio de nov-2026**: hasta entonces se
   comparaba solo contra `inventario_tienda` (Existencias) para que el
   tránsito no bloqueara un envío (caso S96/1210: 5.07 + 3 = 8.07 con `MAX` 6
   seguía recibiendo). Se invirtió a raíz del caso real S53/ciruela 1797:
   1.17 de existencias + 3 en tránsito = 4.17 con `MAX` 2 recibía igual 1
   caja y la tienda reportaba sobre-stock. Ahora esa fila queda bloqueada, y
   S96/1210 (8.07 ≥ 6) también. Si el tránsito fuera 0, 1.17 < 2 y la fila
   recibiría normal (3 cajas en ese caso).
   Impacto medido (`Input/` del 2026-11-07): filas bloqueadas en la red 25 →
   86; S53 51 → 47 cajas, S96 35 → 23; total 1040 y cero residuo intactos.
   Riesgo conocido: si Celes trae el tránsito inflado o desfasado, la tienda
   queda bloqueada de más (antes el riesgo era el contrario).
2. **El corte es `Existencias + tránsito >= MAX`.** Con `MAX` 6: 5.9 reparte;
   6.0 y 6.1 bloquean. Comparación en float, sin redondear.
3. **Es un interruptor, no un techo.** Si la fila no está bloqueada participa
   con normalidad, acotada por `MAX_CAJAS_POR_ITEM` como cualquier otra. No se
   calcula "espacio restante hasta el MAX".
4. **`MAX = 0` (o vacío) = sin tope definido**, no "no enviar". Hoy 4808 de
   4809 filas están en 0; leerlo como bloqueo apagaría el reparto completo.
5. **Cero residuo sigue ganando.** Fases 1/2/3/4 y el nivel 1 de Fase 5 excluyen
   a las bloqueadas. Solo el **nivel 2 de Fase 5** (cuando ya no queda ningún
   otro destino) puede darles cajas, siempre al final del orden y dejando una
   alerta por tienda con su `MAX`, sus existencias y su tránsito reales.

El bloqueo es por tienda × **ítem**, no por grupo espejo: un hermano bloqueado
no frena a los demás SKU del grupo.

### Implementación

- `loader.py`: `MAX` es columna requerida de `tiendas_item`.
- `preprocessor.py`: normaliza a `max_existencias` (paso 3) y calcula la
  máscara `bloqueado_por_max` (paso 9.b, `inventario_efectivo >= MAX`, después
  del `fillna(0).clip()` de existencias y tránsito). Ambas viajan en `cols_out`. Alerta agregada con el
  conteo de combinaciones excluidas.
- `algorithm.py`: helper `_bloqueadas()` (si faltan las columnas asume que
  ninguna lo está, para no romper llamadas aisladas ni tests antiguos) y
  `_alertas_forzadas()` para las alertas del nivel 2 de Fase 5.
- `exporter.py`: situación «Bloqueada por MAX» en la hoja «Novedades» (ver «Output»).

### Si el `XLOOKUP` llega sin recalcular (plan B — implementado, nov-2026)

pandas lee el **valor cacheado** de la fórmula: si el archivo se guarda desde LibreOffice o se
genera por script, la columna `MAX` puede llegar vacía. Con el libro `Maestros Fruver.xlsx`,
`maestros.leer_maestros` ya no usa la columna: reconstruye el MAX desde la hoja `BD_MAX` (clave
tienda + ítem, primera coincidencia, sin coincidencia = 0). El formato anterior (archivos sueltos)
sigue leyendo el valor cacheado.

## Exclusión por falta de datos en Celes (sep-2026)

Una combinación tienda×ítem con **portafolio ACTIVO pero sin fila en Celes**
tampoco entra al reparto normal. Antes sí recibía, y por el peor motivo: el
`fillna(0)` del paso 8 del preprocessor la dejaba con `consumo_diario = 0` e
`inventario_efectivo = 0`, o sea por debajo de `MIN_STOCK_AGOTADO` → prioridad 3
(AGOTADA) → mínimo garantizado de 1 caja en Fase 1. **Recibía mercancía
precisamente porque faltaba el dato**, no porque la tienda estuviera agotada.

### Reglas (decisiones de negocio, no negociables sin consultar)

1. **No se le envía.** Sin datos de consumo/inventario no hay forma de saber si
   la tienda necesita el ítem; el negocio prefiere no enviar a enviar a ciegas.
2. **Cero residuo sigue ganando.** Misma mecánica que el tope `MAX`: Fases 1/2/3/4
   y el nivel 1 de Fase 5 la excluyen, y solo el **nivel 2 de Fase 5** puede
   darle cajas, al final del orden y con alerta por tienda. En la práctica solo
   ocurre si TODAS las tiendas elegibles de un ítem están sin match.
3. **Queda relacionada en la hoja «Novedades»** (situación «Sin datos Celes») del Excel, con la causa
   del faltante (tienda ausente / ítem ausente / par faltante).

### Implementación

- `preprocessor.py`: paso 6.b calcula `causa_sin_match` (necesita el Celes ya
  normalizado, por eso se hace ahí y no en el exporter); paso 9.c calcula
  `excluida_del_reparto = bloqueado_por_max | sin_match_celes`. Las tres
  columnas viajan en `cols_out`, y las dos de origen se conservan por separado
  porque las hojas de trazabilidad necesitan distinguir la causa.
- `algorithm.py`: `_bloqueadas()` lee `excluida_del_reparto` (con fallback a
  `bloqueado_por_max`), así que **la exclusión aplica en los ~10 puntos del
  motor sin tocar ninguna fase**. `_alertas_forzadas()` ramifica el mensaje
  según la causa y expone la clave `causa_forzado` ∈ {`MAX`, `SIN DATOS CELES`}.
- `exporter.py`: hoja «Novedades» (unifica Sin datos Celes, Bloqueada por MAX y Renovación vida útil).

### Impacto medido (corrida real 2026-09-23, mismo `Input/` antes y después)

De 2804 combinaciones elegibles, 24 estaban sin match (0.9%, 19 tiendas, 2
ítems). 5 de ellas recibían 1 caja cada una (ítem 1079, Lechuga Crespa Verde);
tras el cambio esas 5 cajas pasaron a 5 tiendas del **mismo ítem** con datos
reales. **El total despachado no cambió: 843 cajas en ambas corridas, cero
residuo en los 41 ítems.** El cambio redistribuye, no reduce lo que sale del CEDI.

## Alertas de calidad de datos (`preprocessor.build_distribution_df`)

Además de construir `df_merged`, retorna una lista de alertas con problemas
detectados durante el cruce — antes se perdían en silencio:

- **Sin match en Celes**: combinaciones tienda-ítem elegibles sin fila
  correspondiente en Celes se rellenan con `consumo=0`/`inventario=0`. Desde
  sep-2026 **quedan fuera del reparto** además de alertarse (ver «Exclusión por
  falta de datos en Celes»); un solo alerta agregado (no uno por fila) reporta el
  conteo, el % del total y las tiendas/ítems afectados, y el detalle fila por
  fila va en la hoja «Novedades».
- **Ítems huérfanos**: ítems con stock en CEDI que no llegan a ninguna fila de
  `df_merged` (los `inner join` los descartan si no tienen ninguna tienda
  elegible) — alerta `CRÍTICO` por ítem con las cajas perdidas.
- **Filas descartadas por normalización**: cuando `normalize_item` no puede
  convertir un código, se cuenta y reporta por archivo de origen.
- **Validación cruzada con Portafolio**: ver sección arriba.

`run_distribution(df_merged, alertas_extra=...)` antepone estas alertas a las que
genera internamente (residuo sin tiendas elegibles, reparto equitativo, etc.).

## `store_name` sale siempre de DB_Tiendas (auditoría 2026-09)

`store_name` se toma de `DB_Tiendas.'NOMBRE DE LA TIENDA'` para TODAS las filas,
tengan o no match en Celes. Antes se prefería `Celes.'Nombre de Bodega'` cuando
había match y solo se caía a DB_Tiendas si no lo había — pero Celes y DB_Tiendas
pueden nombrar la misma tienda distinto (caso real: `SE4` es `'ISIMO LEON XIII'`
en Celes y `'LEON XIII'` en DB_Tiendas), y como el `groupby` de la hoja Resumen
del exporter agrupa por nombre además de por código, esa mezcla partía una
misma tienda en dos filas de Resumen con nombres distintos. Usar siempre
DB_Tiendas — ya la única fuente de tiendas activas (`zona`, cruce `inner`) —
da un nombre único por `store_code` sin importar el estado del match con
Celes. Sin este fix también existía el riesgo inverso (histórico): antes de
que existiera el fallback, `store_name` quedaba vacío en el 43% de las filas
del Excel final cuando faltaba el match — ver test `test_store_name_sale_de_db_tiendas_cuando_no_hay_match_en_celes`.

## Garantía de distribución total

Para cada ítem que SÍ llega a `df_merged`, `sum(cajas_asignadas) == cajas_disponibles_cedi`.
Si un ítem tiene stock en CEDI pero 0 tiendas elegibles tras el cruce (ítem
huérfano, ver arriba), se reporta como alerta `CRÍTICO` con el residuo — el
algoritmo (`algorithm.py`) nunca llega a verlo porque `preprocessor.py` ya lo
descartó del `groupby('item_code')`.

## Output

`distribucion_fruver_YYYYMMDD.xlsx`, 5 hojas con encabezado fijo y filtros. Las dos primeras son
el entregable; el resto es análisis de apoyo, con columnas homogéneas
(`Tienda · Nombre Tienda · Código Ítem · Nombre Ítem`).

| Hoja | Responde | Columnas clave |
|---|---|---|
| **Distribución** | ¿Qué se envía a cada tienda? | `Centro Operacional de la Bodega`, `Código de Producto`, `UM`, **`Pedido Final`** (entero) + consumo, stock y días antes/después |
| **Resumen** | ¿Cuántas cajas por tienda? | `Total Cajas` |
| Resumen Ítems | ¿Cuántas por ítem y de qué fase? | `F1 Urgentes · F2 Cobertura 3d · F3 Renovación · F4 Excedente` |
| **Novedades** | ¿Qué tienda×ítem merece revisión? | `Situación` (Sin datos Celes · Bloqueada por MAX · Renovación vida útil, cada una con su color), `Tienda`, `Ítem`, `Detalle` (una frase: causa, «Existencias + Tránsito ≥ MAX», o último ingreso/días/vida útil/motivo), `Cajas Enviadas` |
| Alertas | ¿Qué revisar? | `Nivel · Ítem · Qué pasó` (semáforo: Crítico rojo, Advertencia naranja, OK verde; lo crítico primero; «Todo en orden» si no hubo) |

- «Novedades» sale de `df_merged` (`df_output` solo trae filas con pedido) y solo se genera si se
  pasó `df_merged`. `Cajas Enviadas` > 0 en Sin datos/MAX = el nivel 2 de Fase 5 les envió igual
  (cero residuo). Una misma tienda×ítem puede salir en más de una situación (una fila por cada una).
- Sin hoja de análisis para el comprador desde nov-2026 (se eliminó a pedido del usuario, junto con
  el umbral `UMBRAL_RIESGO_SOBRESTOCK` y las columnas F5 Forzado / % Forzado).
- Los datos estructurados extra de cada alerta no van al Excel (están en el mensaje).

## Reglas No Negociables

1. **Cero residuo.** `sum(Pedido Final) == cajas CEDI disponibles` por ítem que
   llegue a `df_merged` (ítems huérfanos se reportan como alerta CRÍTICA, no
   como violación silenciosa).
2. **Solo cajas completas.** `Pedido Final` siempre `int`.
3. **Respetar portafolio.** Elegibilidad exclusivamente desde Tiendas × Ítem
   con `ESTADO` exactamente `ACTIVO`. Además, una tienda×ítem queda fuera del
   reparto normal si `Existencias + tránsito >= MAX` o si no tiene datos en Celes (solo el
   nivel 2 de Fase 5 puede forzarla, con alerta — ver «Tope `MAX` por tienda ×
   ítem» y «Exclusión por falta de datos en Celes»).
4. **Sin efectos secundarios.** No escribir a disco; el único output es el botón de
   descarga (UI) o `Output/` (CLI batch `run.py`).
5. **Alertas visibles y persistidas.** Toda alerta aparece en la hoja **Alertas**
   del Excel de salida, además de mostrarse en la UI de Streamlit o en la
   consola de `run.py` (no se escribe ningún archivo de log adicional en
   `Output/` — decisión explícita del usuario).

## Nota operativa: determinismo asume `Input/` estático durante la corrida

El pipeline es determinista dado un mismo conjunto de archivos, pero **no
congela una copia de `Input/` al iniciar** — si `Stock.xlsx`, `Celes.xlsx` u
otro archivo se edita mientras una corrida está en curso, o entre dos
corridas sucesivas, el resultado cambia porque cambiaron los datos de
origen. Confirmado durante la investigación de un reporte de
sobre-concentración en una tienda (SE9): entre dos ejecuciones consecutivas
el stock de un ítem había cambiado (31→40 cajas) porque el archivo se
estaba editando en paralelo — no había ningún bug en el algoritmo. Tratar
`Input/` como solo lectura mientras haya una corrida en curso.

## Módulo de compras (nov-2026)

Compra al proveedor **por tienda × ítem** y **general por ítem**. Corre después de la distribución,
en la misma corrida (`run.py` y `app.py`), y usa su `df_output` como «pedido de hoy». **No cambia
nada de la distribución**: con el mismo `Input/`, el Excel de distribución sale idéntico antes y
después del módulo (verificado hoja por hoja el 2026-11-19). En `run.py` va en su propio
`try/except`: si la compra falla, la distribución ya quedó guardada.

| Archivo | Rol |
|---|---|
| `core/calendario.py` | Funciones puras de fechas: llegada al CEDI y a tienda, días de venta hasta la llegada, cobertura, festivos de Colombia (Ley Emiliani + Pascua, calculados por año), puentes y anticipación |
| `core/compras_params.py` | **Valores por defecto** (bloque «PARÁMETROS EDITABLES», fuente de verdad), lectura de las hojas de parámetros (del libro de maestros o del `Parametros_Compra.xlsx` anterior) y tablas/plantillas |
| `core/compras_preprocessor.py` | `build_purchase_df`: universo + cruces + proyección (ver abajo) |
| `core/compras.py` | Motor `run_compras` y pipeline `ejecutar_compra(dfs, df_output, ...)` → `ResultadoCompra` |
| `core/compras_exporter.py` | `compra_fruver_YYYYMMDD.xlsx` (8 hojas) y textos de resumen |

### Decisiones de negocio (usuario, 2026-11-19)

- **Universo = ítems `ACTIVO` en el Portafolio** (ESTADO sale de la tabla STATUS del libro de
  maestros: un ítem que no está en STATUS queda DESCONTINUADO) × tienda×ítem `ACTIVO` × Tiendas. No se
  reutiliza `build_distribution_df` porque hace *inner* con Stock: justo los ítems en 0 en el CEDI
  son los que más urgen comprar. Los ítems con stock que no están activos (PRODUCTO PRUEBA) se
  distribuyen pero **no se compran** (alerta).
- **`MIN` de Tiendas × Ítem = exhibición, en cajas (canastillas)**; vacío → `MIN_EXHIBICION_DEFAULT`
  = 1. La exhibición manda: el MIN se compra aunque genere merma, también con consumo 0
  (`EXHIBIR_SIN_VENTA`). Par repetido → MIN más alto, MAX más restrictivo. El usuario enviará la
  base de exhibición por tienda (habrá tienda×ítem con 3).
- **Inventario según vida útil** (`INVENTARIO_SEGUN_VIDA_UTIL = Sí`, decisión del usuario 19-nov, segunda
  versión): en la tienda solo cuenta lo que se alcanza a vender antes de vencer. Reemplaza la decisión
  inicial «Celes tal cual», que dejaba la compra en ~250 cajas cuando el negocio compra >1.000 por día:
  el 62 % del inventario de Celes (2.373 de 3.842 cajas) pasa su vida útil antes de que llegue la
  compra y, mientras la tienda no lo da de baja, Celes lo sigue contando y la exhibición nunca se
  renovaba. Con `No` vuelve la regla anterior (Celes tal cual); `DESCONTAR_INVENTARIO_VENCIDO` sigue
  como variante más débil (solo lo vencido hoy).
- **OC en camino** al CEDI: `Input/Compras_Transito.xlsx` = export de Siesa de OC abiertas (hoja
  «OC en tránsito»). Ver «OC en tránsito» abajo. Sin archivo o vacío → alerta de compra doble.
- **Frecuencia de compra por ítem** (columna DIA DE COMPRA de STATUS, nov-2026): ver «Frecuencia de
  compra por ítem» abajo.
- **2247 LIMÓN X 1000 G** se agregó a STATUS como ACTIVO (había quedado DESCONTINUADO por no estar
  en la tabla, con 17 cajas/día de venta).
- **Calendario**: compra lunes a sábado; lead time 2 días **con recepción** en el CEDI (el domingo
  no recibe) → Vie→Lun y Sáb→Mar (3 días calendario); CEDI → tienda 1 día. Festivos, y el domingo
  anterior a un lunes festivo, reciben al 40 % (`CAPACIDAD_RECEPCION_FESTIVO`).
- Ítems activos **sin datos en Celes** y tienda×ítem sin fila en Celes **no se compran**: van a la
  hoja «Sin Pronóstico» (con Σ MIN como compra de lanzamiento de referencia).
- Las tiendas de `Tiendas_No generar pedido` **sí** entran a la compra (siguen vendiendo;
  `INCLUIR_TIENDAS_SIN_PEDIDO`).

### Modelo

```
D               = consumo diario Celes × (1 + crecimiento global/ítem) × factor de fecha/día
Venta_hasta     = D × días de venta desde hoy (incluido) hasta el día antes de la llegada a tienda
Inv_proy        = lo que queda VIVO a la llegada tras vender día a día por lotes (FIFO, abajo)
Venta_cobertura = D × días desde la llegada hasta la siguiente llegada (+ anticipación de puente)
SS              = SS_Celes × √((días_hasta + días_cobertura) / (LT_Celes + R_Celes)) × (1 + crecimiento)
Objetivo        = max(MIN, min(Venta_cobertura + SS, D × VIDA_UTIL_EXHIBICION))   y ≤ MAX si MAX > 0
Compra_tienda   = floor(max(0, Objetivo − Inv_proy) + 1 − UMBRAL_REDONDEO)
Ítem            : Base = Σ Venta_cobertura, Bruto = Σ Compra_tienda
                  Compra = Base + α↑(Bruto − Base) si Bruto > Base, si no Base + α↓(Bruto − Base)
                  → redondeo half-up → múltiplo / mínimo del proveedor (columnas de STATUS)
```

- **Vida útil** limita solo la parte de demanda; el MIN no (la exhibición manda). «Riesgo merma» =
  días de inventario después de la compra > vida útil (o inventario con consumo 0).
- **Proyección por lotes** (`compras._simular_lotes`): lotes = existencias (ingresadas hace
  `dias_desde_ingreso` días), tránsito de Celes (llega hoy) y pedido de distribución de hoy (llega en
  `LT_CEDI_TIENDA` días). Cada día hasta la llegada se vende `consumo × factor`, primero del lote más
  viejo; la venta que no se cubre **se pierde**. Con `INVENTARIO_SEGUN_VIDA_UTIL`, un lote deja de
  contar cuando sus días desde el ingreso superan la vida útil (misma regla que la Fase 3); sin él,
  no vence. Sin fecha de último ingreso, las existencias se toman como frescas; con varios ingresos,
  se toma el más reciente (optimista).
- **Lo que está en camino** (OC + stock CEDI que la distribución de hoy no repartió) llega a tienda en
  `pool_llegada_tienda` (la fecha más temprana de las OC contadas: `max(fecha esperada, hoy) +
  LT_CEDI_TIENDA`) y absorbe la venta perdida desde ese día: `Inv_proy = vivo + max(0, OC_asignada −
  venta perdida desde su llegada)`. Caso que lo motivó (test
  `test_oc_que_llega_antes_se_come_parte_con_la_venta_hasta_la_compra`): sumar la OC al inventario y
  restar toda la venta hacía que una OC de mañana casi no redujera la compra.
- **Asignación de lo que está en camino** (como lo hará la distribución): niveles 1 Agotada
  (`Inv_proy < MIN_STOCK_AGOTADO` de `algorithm.py`) → 2 Exhibición (`< MIN`) → 3 Reposición,
  proporcional dentro del nivel con `algorithm._reparto_proporcional` (Hamilton). Cupo por tienda =
  lo que la deja en su objetivo **a la llegada de la compra** (`objetivo + venta después de la OC −
  inventario antes de la OC`).
- **Suavizado asimétrico** (tipo *proportional order-up-to*): α↑ = 0,5 cierra la mitad del hueco
  por compra (flujo estable, sin picos); α↓ = 1 reacciona completo ante sobre-stock (menos merma).
  Un recorte sale primero de Reposición y luego de Exhibición; **nunca de las Agotadas** (si hace
  falta, la compra sube y se alerta). Un agregado (múltiplo/mínimo) va proporcional a la venta esperada.
- **Σ compra por tienda = compra general** del ítem, siempre (lo verifican los tests).
- Puente (16-nov-2026, lunes festivo): la compra del jueves llega el sábado y cubre el domingo
  **más** el 60 % de lo que debían traer las llegadas del domingo y el lunes (al 40 %): 1 + 0,6 +
  1,2 = 2,8 días; las del viernes y el sábado traen 0,4 y 0,8. Total 4 = dom–mié.
  **Pendiente validar con el usuario en el primer puente real** (el siguiente lunes festivo es el
  11-ene-2027; el 8-dic-2026 cae martes y se trata como día al 40 %).

### Frecuencia de compra por ítem (DIA DE COMPRA)

`calendario.interpretar_dia_compra` lee el texto de STATUS; `compras_preprocessor._regimen_compra`
arma la cobertura de cada ítem y `compras._comprar_segun_frecuencia` aplica la regla:

| DIA DE COMPRA | Regla |
|---|---|
| `Todos los dias` (o vacío) | Calendario general (lunes a sábado), cobertura hasta la siguiente compra |
| Días separados por guion, coma o «y» (`LUNES-MIERCOLES-VIERNES`) | Solo se compra esos días; cada compra cubre hasta la llegada de la **siguiente compra del ítem** (L-M-V: lun cubre jue+vie, mié cubre sáb–lun, vie cubre mar+mié). Otro día: compra 0, motivo «No se compra hoy (…); próxima compra dd/mm» |
| `CADA N DIAS` | Se revisa a diario. Se **activa** cuando la red llega a la compra por debajo de su objetivo normal (Σ inventario proyectado < Σ objetivo); entonces compra N días de cobertura (objetivo con `venta_cobertura_periodo`, SS a N días, tope de vida útil) **sin suavizado**. Si no, compra 0 («Cubierta hasta la próxima compra (cada N días)»). No hay fecha ancla: cuando la OC está en tránsito, la proyección la incluye y no se vuelve a activar |
| Texto no reconocido | Calendario general + alerta |

Limitación conocida de CADA N: la activación es de red; una tienda vacía con la red cubierta no la
activa (el desbalance entre tiendas no se corrige comprando).

### OC en tránsito (export de Siesa, `compras_preprocessor._clasificar_oc`)

Columnas que usa: `Item`, `Cant. ordenada`, `Cant. entrada`, `Fecha de Entrega` (obligatorias las
dos de cantidad ordenada y fecha), `Fecha orden`, `Estado`, `Fecha cumplido`, `Bodega`, `Nro orden`,
`Razón social proveedor`. Pendiente = ordenada − entrada. Clasificación de cada línea:

| Clasificación | Regla | ¿Cuenta? |
|---|---|---|
| Cerrada | `Fecha cumplido` o Estado anulada/cerrada/cumplida/rechazada | No |
| Otra bodega | `Bodega` distinta de la del Stock (BOCES) | No |
| Ítem no activo | No está ACTIVO en el Portafolio | No |
| En tránsito | Fecha esperada > hoy y ≤ llegada de la compra de hoy | **Sí** |
| Llega después | Fecha esperada > llegada de la compra de hoy | No (la cuenta la siguiente) |
| Recibida según Stock | Fecha esperada ≤ hoy y Stock tiene `Fecha última entrada` del ítem ≥ esa fecha | No |
| Atrasada | Fecha esperada ≤ hoy, sin ingreso posterior y atraso ≤ `DIAS_MAX_ATRASO_OC` (3) | **Sí**, como si llegara hoy |
| Vencida sin recibir | Igual, con más atraso | No (alerta: cerrarla en Siesa) |

Sin `Fecha de Entrega` → Fecha orden + lead time. **Por qué «Recibida según Stock»**: en el export
real (19-nov) `Cant. entrada` siempre es 0 y `Fecha cumplido` siempre vacía — Siesa no cierra la
línea al recibir —, así que el único indicio de recepción es el ingreso en Stock. Resultado ese día:
76 líneas en tránsito (1.340 cajas), 5 atrasadas (148), 25 recibidas según Stock (312), 1 vencida
(OC de octubre) y 1 de ítem no activo (2606). Proveedor de cada ítem = el de su OC más reciente
(columna «Proveedor (última OC)» de Compra General). Detalle línea por línea en la hoja «OC en Tránsito».

### Salida `compra_fruver_YYYYMMDD.xlsx`

| Hoja | Contenido |
|---|---|
| Compra General | Una fila por ítem activo (incluidos los sin Celes, en 0): día de compra, si se compra hoy, cobertura del ítem, venta esperada, necesidad, OC, ajuste de suavizado, **Compra Sugerida** y **Compra Final** (amarilla, editable por el comprador), costo y valor, días de inventario de la red después, Compra/Venta %, proveedor (última OC) y Observación |
| Compra por Tienda | Tienda×ítem con datos: proyección a la llegada, MIN, SS, objetivo, compra, días antes/después, último ingreso, vida útil, riesgo de merma y **Motivo** (Agotada proyectada · Exhibición · Reposición · Cubierta · Bajo umbral de redondeo · Recortada por suavizado · Ajuste de compra) |
| Costo Exhibición | Tienda×ítem con `MIN > D × vida útil`: merma estructural `7 × (MIN / vida útil − D)` cajas/semana y en COP. Herramienta para decidir el MIN |
| Bajas Pendientes | Último ingreso más viejo que la vida útil y existencias > 0 en Celes |
| Sin Pronóstico | Ítems activos sin Celes y tienda×ítem sin fila en Celes |
| OC en Tránsito | Cada línea del export de OC con su clasificación y si cuenta para la compra |
| Parámetros | Resumen, fechas del plan y valores usados |
| Alertas | Semáforo, igual que la distribución |

Costo: `Costo prom. uni.` de Stock (por caja); si falta, `COSTO` del Portafolio.

### Primera corrida (Input del 2026-11-19, jueves, cubre dom 22 y lun 23)

- **577 cajas** en 38 ítems (COP 23,8 M) contra 1.092 de venta esperada en los 2 días: según Celes
  la red llega a la compra con **7,8 días** de inventario. El modelo compra poco donde hay
  sobre-stock y concentra la compra en exhibición y agotadas.
- **Costo Exhibición**: 2.572 tienda×ítem cuyo MIN no se vende dentro de la vida útil → ~2.700
  cajas/semana de merma estructural (~COP 100 M/semana). **Bajas Pendientes**: 1.063 tienda×ítem
  (879 cajas, ~COP 32,6 M).
- Corrección al análisis previo: el prototipo del plan estimaba ~1.400–1.700 cajas porque le
  daba MIN = 1 a las 1.068 tienda×ítem **sin datos en Celes** (18 ítems activos que Celes no trae).
  Y de las «1.638 tienda×ítem sin venta» de ese análisis, solo 56 tienen fila en Celes con consumo
  0: el resto no tiene datos.

### Con OC reales, días de compra y libro de maestros (misma fecha, `CRECIMIENTO_VENTA` 18 %)

**260 cajas** en 17 ítems (COP 10,9 M) contra 1.356 de venta esperada: lo pedido ayer (1.340 cajas
en tránsito + 148 atrasadas) ya cubre casi todo. Fresa 2603 (L-M-V) no se compra el jueves;
Flor de Jamaica 3100 (cada 15 días) no se activa (60 cajas en camino); 2247 activo, con 81 en camino.
`CRECIMIENTO_VENTA` = 0,18 en el `Parametros_Compra.xlsx` del usuario se leyó como 18 % (regla: un
valor entre 0 y 1 es fracción) y en el libro nuevo quedó escrito como 18.

### Validación «¿por qué la compra está tan baja?» (19-nov, con inventario según vida útil)

| Regla de inventario | Compra de hoy | Inventario a la llegada |
|---|---|---|
| Celes tal cual (anterior) | 242 cajas, 17 ítems | 5.201 cajas (7,7 días) |
| **Según vida útil (vigente)** | **654 cajas, 38 ítems, COP 26,3 M** | 3.701 cajas (5,5 días) |
| Según vida útil sin suavizado | 697 | 3.701 |

- No es un problema de unidades (CN = canastilla en OC, Stock y Celes) ni de doble conteo del
  tránsito (Celes del 19-nov es anterior a la distribución del 19-nov).
- Hoy sale menos que el promedio porque la OC del 18-nov (1.301 cajas, la mayor del export) y la
  distribución de hoy (1.318) llegan frescas viernes/sábado y esta compra solo cubre dom + lun: ayer
  + hoy ≈ 1.955 cajas ≈ 980/día, en línea con lo que compra el negocio (>1.000 por día de compra).
- Flujo estable que exige la política (venta +18 % o renovar el MIN cada vida útil, lo mayor):
  ~1.054 cajas/día ≈ 1.229 por día de compra (6 por semana).
- De 654 cajas: 551 a tiendas que el domingo estarían en 0 (Agotada proyectada), 97 por exhibición.

### Pendientes

1. Base de exhibición por tienda (columna `MIN` de Tiendas x Item, se podrá renombrar).
2. Histórico de ventas diarias → factores por día de semana (hoja «Dias de compra», hoy 1,0).
3. CADA N DIAS con fecha ancla (última compra) si el negocio la necesita.
4. Modo sombra 2 semanas: comparar Compra Final vs sugerida y calibrar `UMBRAL_REDONDEO`, `ALFA_SUBIDA` y el MIN.

Tests: `tests/test_calendario.py`, `tests/test_compras.py`, `tests/test_compras_exporter.py`,
`tests/test_maestros.py` (datos sintéticos en `tests/compras_helpers.py`, fecha fija jueves 2026-11-19).

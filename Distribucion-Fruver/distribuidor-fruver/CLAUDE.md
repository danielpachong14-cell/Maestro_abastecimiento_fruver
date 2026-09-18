# Distribuidor Fruver — Isimo

Herramienta interna de distribución automática del inventario Fruver del CEDI a las
tiendas activas. Procesa 6 archivos Excel obligatorios (+ 1 opcional) y genera un
archivo de pedidos donde el inventario disponible queda en 0.

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
6+1 Excel → loader.py → preprocessor.py → algorithm.py → exporter.py → distribución.xlsx
```

- `app.py` — UI Streamlit: upload de los archivos, botón de ejecución, descarga del resultado.
- `core/loader.py` — Carga y valida columnas requeridas de cada Excel (hoja Celes = `Items`).
- `core/preprocessor.py` — Limpieza, normalización de claves, merge en un DataFrame
  tienda × ítem, y detección de problemas de calidad de datos (ver «Alertas de
  calidad de datos» más abajo). Retorna `(df_merged, alertas)`.
- `core/algorithm.py` — Motor de distribución con prioridades y garantía de 0 residuo.
- `core/exporter.py` — Genera el Excel de salida (5 hojas: Distribución, Resumen,
  Resumen Ítems, Análisis Comprador, Alertas).

## Archivos de entrada

| Archivo | Clave interna | Obligatorio |
|---|---|---|
| `Stock.xlsx` | `stock` | Sí |
| `Celes.xlsx` | `celes` | Sí |
| `DB_Portafolio Fruver.xlsx` | `portafolio` | Sí (validación cruzada, ver abajo) |
| `DB_Tiendas.xlsx` | `tiendas` | Sí |
| `DB_Tiendas Por itmes y portafolio.xlsx` | `tiendas_item` | Sí (fuente de elegibilidad) |
| `DB_ProductosEspejo.xlsx` | `espejo` | Sí (grupos de productos espejo) |
| `Tiendas_No generar pedido.xlsx` | `excluidas` | No |

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

`DB_Portafolio Fruver.xlsx` (columnas `ITEM`, `CLUSTERIZACIÓN`, `ESTADO`) ya no se
descarta tras cargarse: se usa para dos chequeos que generan alertas (no bloquean
la distribución):

1. **Ítem nuevo no catalogado**: ítem con stock en CEDI cuyo código no aparece en
   absoluto en Portafolio.
2. **Ítem descontinuado con stock**: ítem marcado inactivo en Portafolio (según
   `_is_active`) que aún tiene existencia > 0 en el CEDI.

`CLUSTERIZACIÓN` no se usa todavía en el cálculo de distribución.

## Parámetros del algoritmo (core/algorithm.py)

```python
TARGET_DAYS       = 3.0   # días MÍNIMOS de inventario proyectado objetivo por tienda
MIN_STOCK_AGOTADO = 0.4   # inventario_efectivo < 0.4 cajas → AGOTADA (prioridad máxima)
MIN_STOCK_SAFETY  = 0.7   # inventario_efectivo < 0.7 cajas → STOCK SEGURIDAD
MIN_CAJAS_INICIAL = 2     # número de rondas (cap 1, 2) en Fase 1 — el nombre viene de
                          # versiones anteriores; hoy es el cap MÁXIMO por ronda, no un mínimo
MAX_CAJAS_POR_ITEM = 3    # tope duro: máximo que una tienda recibe por ítem (todas las fases)
TOPE_EXCEDENTE    = 5     # máximo días que puede acumular una tienda del excedente
UMBRAL_CONCENTRACION_SIN_CONSUMO = 5  # cajas: ver «Reparto equitativo» más abajo
```

**Estos son los valores REALES en el código** — si ves otro valor documentado en
algún lugar, el código (`core/algorithm.py`) es la fuente de verdad; repórtalo
para corregir la doc.

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
Fase 3.

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
- **Fase 3** (`_fase3_grupal`): elegibilidad, `TOPE_EXCEDENTE` y peso se calculan
  sobre el inventario/consumo combinado del grupo; cada ítem sigue entregándose
  desde su propio stock CEDI (`MAX_CAJAS_POR_ITEM` sigue por SKU). Garantiza 1 caja
  a cualquier ítem del grupo con 0 unidades en tienda antes de repartir proporcional.

## Lógica de prioridades

```
Priority 3 (AGOTADO):      inventario_efectivo_grupo < 0.4 cajas → mínimo 1 caja, primera en recibir
Priority 2 (SAFETY):       inventario_efectivo_grupo < 0.7 cajas → mínimo 1 caja
Priority 1 (REPOSICIÓN):   dias_proyectados_grupo    < 3.0       → cajas para llegar a 3 días
Priority 0 (CUBIERTO):     dias_proyectados_grupo    ≥ 3.0       → solo recibe si sobra inventario
```

## Las cuatro fases (Fase 1, Fase 2, Fase 3, Fase 4 — antes "2a"/"2b"; Fase 4 antes "Paso B")

Renombradas en la auditoría 2026-07 porque son pasos secuenciales con timing y
alcance de negocio distintos (Fase 2 = cobertura obligatoria de `TARGET_DAYS` por
ítem; Fase 3 = distribución voluntaria del excedente, coordinada a nivel de grupo
espejo), no variantes del mismo paso. La Fase 4 (antes el "Paso B" dentro de la
Fase 3) se separó en su propia fase en la auditoría 2026-09: a partir de un caso
real reportado por el usuario (ítem 1561, Aguacate Hass — 73 de 89 tiendas
recibieron caja aunque 77 ya estaban en o sobre `TOPE_EXCEDENTE` *antes* del
pedido), quedó claro que mezclar "reparto sano bajo el tope" y "cero-residuo
forzado que ignora el tope" en la misma columna (`cajas_fase3`) ocultaba cuánta
distribución de un ítem era en realidad sobre-stock inevitable por exceso de
compra frente al portafolio real, no reparto normal.

**Fase 1** — rondas con cap creciente (1 → `MIN_CAJAS_INICIAL`): todas las tiendas
elegibles reciben su 1.ª caja antes de que cualquiera reciba su 2.ª, evitando que
tiendas de alto consumo acaparen cuando el stock es escaso.

**Fase 2** — completa `TARGET_DAYS` de forma proporcional cuando el stock no
alcanza para todas (corrección Hamilton para residuos enteros).

**Fase 3** — el sobrante se reparte por rondas, **proporcional a
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

**Fase 4** — red de seguridad de cero residuo. Fase 3 **nunca** le da a una
tienda una caja que la deje por encima de `TOPE_EXCEDENTE`; si todas las
tiendas elegibles llegan a `MAX_CAJAS_POR_ITEM` o a `TOPE_EXCEDENTE` (caso
extremo: el CEDI recibió más stock del que el portafolio puede absorber sin
sobre-stockearse) y aún sobran cajas, Fase 3 termina con remanente. La Fase 4
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

**Por qué importa verla separada de Fase 3 en el reporte**: un ítem con mucho
volumen en `Cajas Fase 4` (columna propia en "Resumen Ítems" desde la
auditoría 2026-09) es una señal directa de que el CEDI recibió más de ese
ítem del que el portafolio activo necesita — la Fase 4 fuerza sobre-stock
porque no hay alternativa para vaciar el CEDI, no porque las tiendas lo
necesiten. Antes de separarla, ese volumen se sumaba en silencio a
`Cajas Fase 3` y era indistinguible del reparto sano bajo el tope.

### Reparto equitativo cuando falta consumo en el portafolio

Si el Paso B de una sola tienda concentraría `>= UMBRAL_CONCENTRACION_SIN_CONSUMO`
(5) cajas porque es la única con consumo registrado en un portafolio más amplio,
se activa reparto equitativo entre TODAS las tiendas del portafolio activo del
ítem en vez de concentrar en una sola, y se genera una alerta `ADVERTENCIA`.

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
  el tope `MAX` de Tiendas × Ítem (ver «Tope `MAX` por tienda × ítem»).

## Tope `MAX` por tienda × ítem

`DB_Tiendas Por itmes y portafolio.xlsx` trae una columna **`MAX`** con el
máximo de existencias que esa tienda puede tener de ese ítem. Es una columna
**calculada dentro del propio Excel**: `Hoja1` es una tabla (`Tabla1`) con

- `LLAVE` = `CONCAT([COD SIESA], [ITEM])` → `S961210`
- `MAX`   = `IFERROR(XLOOKUP([LLAVE], Tabla2[LLAVE], Tabla2[MAX],,0,1), )`

donde `Tabla2` vive en la hoja auxiliar **`BD_MAX`** (las filas que el negocio
mantiene a mano). El pipeline lee `Hoja1` y toma el valor ya resuelto.

### Reglas (decisiones de negocio, no negociables sin consultar)

1. **Se compara contra Existencias, NO contra el inventario efectivo.** El
   resto del motor usa `inventario_efectivo = Existencias + tránsito`, pero
   el bloqueo mira solo `inventario_tienda` (Existencias): el tránsito ya va
   en camino y no debe impedir un envío. Caso real S96/1210: Existencias 5.07
   + tránsito 3 = 8.07 con `MAX` 6 → **sigue recibiendo**, porque 5.07 < 6.
2. **El corte es `Existencias >= MAX`.** Con `MAX` 6: 5.9 reparte; 6.0 y 6.1
   bloquean. Comparación en float, sin redondear.
3. **Es un interruptor, no un techo.** Si la fila no está bloqueada participa
   con normalidad, acotada por `MAX_CAJAS_POR_ITEM` como cualquier otra. No se
   calcula "espacio restante hasta el MAX".
4. **`MAX = 0` (o vacío) = sin tope definido**, no "no enviar". Hoy 4808 de
   4809 filas están en 0; leerlo como bloqueo apagaría el reparto completo.
5. **Cero residuo sigue ganando.** Fases 1/2/3 y el nivel 1 de Fase 4 excluyen
   a las bloqueadas. Solo el **nivel 2 de Fase 4** (cuando ya no queda ningún
   otro destino) puede darles cajas, siempre al final del orden y dejando una
   alerta por tienda con su `MAX` y sus existencias reales.

El bloqueo es por tienda × **ítem**, no por grupo espejo: un hermano bloqueado
no frena a los demás SKU del grupo.

### Implementación

- `loader.py`: `MAX` es columna requerida de `tiendas_item`.
- `preprocessor.py`: normaliza a `max_existencias` (paso 3) y calcula la
  máscara `bloqueado_por_max` (paso 9.b, después del `fillna(0).clip()` de
  `inventario_tienda`). Ambas viajan en `cols_out`. Alerta agregada con el
  conteo de combinaciones excluidas.
- `algorithm.py`: helper `_bloqueadas()` (si faltan las columnas asume que
  ninguna lo está, para no romper llamadas aisladas ni tests antiguos) y
  `_alertas_max_forzado()` para las alertas del nivel 2 de Fase 4.
- `exporter.py`: columna `# Tiendas Bloqueadas por MAX` en Análisis Comprador.

### Si el `XLOOKUP` llega sin recalcular (plan B, NO implementado)

pandas lee el **valor cacheado** de la fórmula. Excel lo mantiene al día, pero
si el archivo se guarda desde LibreOffice o se genera por script, la columna
`MAX` puede llegar vacía. Si eso llega a pasar, el plan B es leer la hoja
`BD_MAX` directamente y reconstruir el cruce en Python
(`merge(left_on=['COD SIESA','ITEM'])`), en vez de depender de la columna
calculada. No se implementó porque añade otra hoja al loader y hoy no hace
falta — pero es el camino a seguir si aparecen `MAX` vacíos con `BD_MAX` lleno.

## Alertas de calidad de datos (`preprocessor.build_distribution_df`)

Además de construir `df_merged`, retorna una lista de alertas con problemas
detectados durante el cruce — antes se perdían en silencio:

- **Sin match en Celes**: combinaciones tienda-ítem elegibles sin fila
  correspondiente en Celes se rellenan con `consumo=0`/`inventario=0`, lo que las
  clasifica como AGOTADA sin serlo necesariamente. Un solo alerta agregado (no uno
  por fila) reporta el conteo y % del total.
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

`distribucion_fruver_YYYYMMDD.xlsx`, hoja **Distribución**:

| Columna | Contenido |
|---|---|
| `Centro Operacional de la Bodega` | COD SIESA de la tienda (ej. `S03`) |
| `Código de Producto` | código del ítem (entero) |
| `UM` | unidad de medida (solo referencia) |
| `Pedido Final` | cajas completas a enviar (entero) |

Más hojas: **Resumen** (total cajas por tienda), **Resumen Ítems** (desglose por
Fase 1/2/3/4 por ítem), **Análisis Comprador** (exceso de CEDI vs. mínimo necesario
por ítem, incluidas columnas de riesgo de merma — `# Tiendas con Riesgo Merma` /
`% Tiendas con Riesgo` — calculadas contra el umbral `min(10, vida_útil)`) y
**Alertas** (todos los casos borde detectados, ordenados por severidad;
`"OK — Sin alertas"` si no hubo ninguna).

**Análisis Comprador** también incluye `# Tiendas con Riesgo Sobrestock (Xd)` /
`% Tiendas con Riesgo Sobrestock`, calculadas contra `UMBRAL_RIESGO_SOBRESTOCK`
(`core/exporter.py`, valor actual: 10 días). Este umbral es **exclusivo del
reporte** y está desacoplado a propósito de `TOPE_EXCEDENTE` (`core/algorithm.py`,
valor actual: 5 días) — el tope real que usa el algoritmo en Fase 3 para dejar
de asignarle más sobrante a una tienda. Cambiar `UMBRAL_RIESGO_SOBRESTOCK` solo
afecta esta columna de evaluación de riesgo; no cambia una sola caja de lo que
se despacha. Decisión explícita del usuario (auditoría 2026-07): quería subir el
umbral de evaluación de riesgo de sobre-stock/merma de 6 a 10 días sin alterar
el comportamiento real de distribución.

La hoja **Riesgo Merma** (detalle tienda-ítem del mismo cálculo) existió hasta
que se quitó a pedido del usuario — el resumen por ítem sigue disponible en
Análisis Comprador.

## Reglas No Negociables

1. **Cero residuo.** `sum(Pedido Final) == cajas CEDI disponibles` por ítem que
   llegue a `df_merged` (ítems huérfanos se reportan como alerta CRÍTICA, no
   como violación silenciosa).
2. **Solo cajas completas.** `Pedido Final` siempre `int`.
3. **Respetar portafolio.** Elegibilidad exclusivamente desde Tiendas × Ítem
   con `ESTADO` exactamente `ACTIVO`. Además, una tienda×ítem con
   `Existencias >= MAX` queda fuera del reparto normal (solo el nivel 2 de
   Fase 4 puede forzarla, con alerta — ver «Tope `MAX` por tienda × ítem»).
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

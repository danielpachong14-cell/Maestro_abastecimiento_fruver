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
MIN_STOCK_AGOTADO = 0.2   # inventario_efectivo < 0.2 cajas → AGOTADA (prioridad máxima)
MIN_STOCK_SAFETY  = 0.5   # inventario_efectivo < 0.5 cajas → STOCK SEGURIDAD
MIN_CAJAS_INICIAL = 2     # número de rondas (cap 1, 2) en Fase 1 — el nombre viene de
                          # versiones anteriores; hoy es el cap MÁXIMO por ronda, no un mínimo
MAX_CAJAS_POR_ITEM = 3    # tope duro: máximo que una tienda recibe por ítem (todas las fases)
TOPE_EXCEDENTE    = 6     # máximo días que puede acumular una tienda del excedente
UMBRAL_CONCENTRACION_SIN_CONSUMO = 5  # cajas: ver «Reparto equitativo» más abajo
```

**Estos son los valores REALES en el código** — si ves otro valor documentado en
algún lugar, el código (`core/algorithm.py`) es la fuente de verdad; repórtalo
para corregir la doc.

## Lógica de inventario

Todo se trabaja en **cajas completas** (enteros). Nunca en unidades.

```
inventario_efectivo = existencias_en_tienda + cajas_en_tránsito   (clip a >= 0)
dias_proyectados    = inventario_efectivo / consumo_diario
cajas_necesarias    = ceil( max(0, TARGET_DAYS × consumo_diario − inventario_efectivo) )
```

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
Priority 3 (AGOTADO):      inventario_efectivo_grupo < 0.2 cajas → mínimo 1 caja, primera en recibir
Priority 2 (SAFETY):       inventario_efectivo_grupo < 0.5 cajas → mínimo 1 caja
Priority 1 (REPOSICIÓN):   dias_proyectados_grupo    < 3.0       → cajas para llegar a 3 días
Priority 0 (CUBIERTO):     dias_proyectados_grupo    ≥ 3.0       → solo recibe si sobra inventario
```

## Las tres fases (Fase 1, Fase 2, Fase 3 — antes "2a"/"2b")

Renombradas en la auditoría 2026-07 porque son pasos secuenciales con timing y
alcance de negocio distintos (Fase 2 = cobertura obligatoria de `TARGET_DAYS` por
ítem; Fase 3 = distribución voluntaria del excedente, coordinada a nivel de grupo
espejo), no variantes del mismo paso.

**Fase 1** — rondas con cap creciente (1 → `MIN_CAJAS_INICIAL`): todas las tiendas
elegibles reciben su 1.ª caja antes de que cualquiera reciba su 2.ª, evitando que
tiendas de alto consumo acaparen cuando el stock es escaso.

**Fase 2** — completa `TARGET_DAYS` de forma proporcional cuando el stock no
alcanza para todas (corrección Hamilton para residuos enteros).

**Fase 3** — el sobrante se reparte por rondas, **proporcional a
`consumo_diario / días_actuales`** (equivalente a `consumo_diario² / stock_actual`)
entre las tiendas elegibles (`consumo > 0`, que tras recibir una caja más sigan
bajo `TOPE_EXCEDENTE` días, y que no superen `MAX_CAJAS_POR_ITEM` cajas por
entrega), con corrección Hamilton:

```
dias_actuales = (inventario_efectivo + cajas_ya_asignadas) / consumo_diario
peso_tienda   = consumo_diario / dias_actuales
```

Una tienda con `dias_actuales == 0` (la más urgente posible dentro de las
elegibles) recibe el mayor peso, no cero — el cálculo usa un epsilon pequeño en
vez de dividir literalmente por 0 (bug corregido en la auditoría 2026-07: antes
`dias_actuales==0` se traducía en peso NULO por el manejo de la división,
invirtiendo la prioridad que el propio diseño documenta).

**Por qué ese peso y no "1 caja por tienda" ni "proporcional al consumo a secas"**:
repartir la MISMA CANTIDAD DE CAJAS reparte cantidades MUY DISTINTAS de DÍAS
(`Δdías ≈ cajas / consumo`), y el consumo puede variar mucho entre tiendas — la
tienda lenta gana muchos más días por caja que la rápida, generando el desbalance
opuesto al deseado. Ponderar por `consumo_diario / días_actuales` combina ambas
señales — cuánto vende y qué tan atrás va respecto al resto — y produce
`Δdías_i = k / días_i`: la tienda más atrasada gana más días por ronda, cerrando
la brecha en vez de mantenerla.

Si todas las tiendas elegibles llegan a `MAX_CAJAS_POR_ITEM` o `TOPE_EXCEDENTE`
(caso extremo), un "Paso B" de cero-residuo reparte lo que quede por menor
`días_actuales`, ignorando `MAX_CAJAS_POR_ITEM` — la garantía de cero residuo
tiene prioridad sobre el tope de cajas por entrega.

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
`store_name` cae a `DB_Tiendas.'NOMBRE DE LA TIENDA'` cuando no hay match en Celes
(antes quedaba vacío: 43% de filas del Excel final en un caso real de producción).

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
Fase 1/2/3 por ítem), **Análisis Comprador** (exceso de CEDI vs. mínimo necesario
por ítem, incluidas columnas de riesgo de merma — `# Tiendas con Riesgo Merma` /
`% Tiendas con Riesgo` — calculadas contra el umbral `min(10, vida_útil)`) y
**Alertas** (todos los casos borde detectados, ordenados por severidad;
`"OK — Sin alertas"` si no hubo ninguna).

**Análisis Comprador** también incluye `# Tiendas con Riesgo Sobrestock (Xd)` /
`% Tiendas con Riesgo Sobrestock`, calculadas contra `UMBRAL_RIESGO_SOBRESTOCK`
(`core/exporter.py`, valor actual: 10 días). Este umbral es **exclusivo del
reporte** y está desacoplado a propósito de `TOPE_EXCEDENTE` (`core/algorithm.py`,
valor actual: 6 días) — el tope real que usa el algoritmo en Fase 3 para dejar
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
   con `ESTADO` exactamente `ACTIVO`.
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

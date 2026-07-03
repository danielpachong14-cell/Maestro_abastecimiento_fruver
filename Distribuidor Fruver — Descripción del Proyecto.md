# Distribuidor Fruver — Documentación del Proyecto

## ¿Qué es?

El **Distribuidor Fruver** es una herramienta interna desarrollada por Isimo que automatiza la distribución del inventario de productos Fruver desde el CEDI hacia las tiendas activas de la cadena.

Funciona como una aplicación web liviana: el operador sube seis archivos Excel (y un séptimo opcional), presiona un botón y en segundos descarga el archivo de pedidos listo para ejecutarse en el sistema, con el 100% del inventario disponible repartido entre las tiendas.

---

## Archivos de entrada (6 Excel obligatorios + 1 opcional)

| Archivo | Clave interna | Obligatorio | Contenido |
|---|---|---|---|
| `Stock.xlsx` | `stock` | Sí | Inventario disponible en el CEDI por ítem (en cajas) |
| `Celes.xlsx` | `celes` | Sí | Consumos históricos, existencias en tienda e inventario en tránsito |
| `DB_Portafolio Fruver.xlsx` | `portafolio` | Sí | Catálogo de ítems, usado para alertar ítems nuevos sin catalogar o descontinuados con existencia |
| `DB_Tiendas.xlsx` | `tiendas` | Sí | Maestro de tiendas activas con sus códigos |
| `DB_Tiendas Por itmes y portafolio.xlsx` | `tiendas_item` | Sí | Matriz de elegibilidad: qué tiendas pueden recibir qué ítem (fuente de verdad) |
| `DB_ProductosEspejo.xlsx` | `espejo` | Sí | Agrupa SKUs que son el mismo producto físico bajo distinto código, para no duplicar reposición entre ellos |
| `Tiendas_No generar pedido.xlsx` | `excluidas` | No | Tiendas a excluir de una corrida puntual |

### Qué campo se usa de cada archivo

**Stock (Siesa)**

| Campo | Uso |
|---|---|
| `Item` | Código del ítem (texto con ceros: `"0000155"` → se normaliza a entero `155`) |
| `Desc. item` | Nombre del ítem |
| `Cant. disponible` | Cajas disponibles en el CEDI *(ya en cajas, no se divide por Factor U.M.)* |
| `U.M.` | Unidad de medida — solo para el output, no para cálculos |
| `ESTADO DEL PRODUCTO` | NO filtra la distribución (decisión de negocio: toda existencia física se envía sin importar el estado) — si no es `ACTIVO`, genera una alerta informativa no bloqueante |

**Celes**

| Campo | Uso |
|---|---|
| `Código de Bodega` | Código de tienda con prefijo `BO` (`BOS03` → se normaliza a `S03`) |
| `Nombre de Bodega` | Nombre de la tienda |
| `Código de Producto` | Código del ítem |
| `Consumo Diario (Unidades de Distribución)` | Cajas vendidas por día |
| `(=) Días de Inventario Actuales` | Días de stock actuales |
| `Inventario Disponible en esta Tienda` | Cajas físicas en tienda |
| `(-) inventario de traslado en proceso` | Cajas en tránsito hacia la tienda |
| `UM` | Unidad de medida desde Celes |

**Portafolio Fruver**

| Campo | Uso |
|---|---|
| `ITEM` | Código de ítem — catálogo completo para validación cruzada |
| `ESTADO` | `ACTIVO` / no-activo — cruza contra Stock para alertar ítems nuevos sin catalogar o descontinuados con existencia |

> No participa en el cálculo de cajas ni en la elegibilidad tienda-ítem (esa viene de Tiendas × Ítem). `CLUSTERIZACIÓN` no se usa todavía en el cálculo.

**Tiendas × Ítem**

| Campo | Uso |
|---|---|
| `COD SIESA` | Código tienda |
| `DB_Portafolio_Fruver.ITEM` | Código ítem |
| `DB_Portafolio_Fruver.ESTADO` | Se filtra solo `ACTIVO` |

> **Fuente de verdad de elegibilidad**: determina qué tienda puede recibir qué ítem.

**Productos Espejo**

| Campo | Uso |
|---|---|
| `grupo_id` | Identificador del grupo de productos espejo |
| `item_id` | Código de ítem que pertenece a ese grupo |

> Agrupa SKUs que son el mismo producto físico bajo distinto código — compiten por la misma demanda en tienda y no deben duplicar reposición/excedente entre sí. Un ítem sin entrada aquí queda en un grupo de tamaño 1 (comportamiento idéntico al de un ítem sin espejo).

**Base de Tiendas**

| Campo | Uso |
|---|---|
| `COD SIESA` | Clave de cruce con el resto del pipeline |
| `NOMBRE DE LA TIENDA` | Respaldo del nombre de tienda cuando no hay match en Celes |
| `ZONA` | Zona geográfica de la tienda — se incluye en el output |

**Tiendas sin pedido** *(opcional)*

| Campo | Uso |
|---|---|
| `Centro Operacional de la Bodega` | Lista de COD SIESA que se excluyen completamente de la distribución en esa ejecución |

---

## Cómo funciona

```
6+1 Excel → carga y validación → normalización → algoritmo de distribución → archivo de pedidos
```

1. **Carga y validación** — verifica que cada archivo tenga las columnas requeridas y alerta si falta algo antes de procesar.
2. **Normalización** — resuelve automáticamente las diferencias de codificación entre los archivos (ej. el stock usa `'0000155'` como texto, mientras los otros usan `155` como número; Celes usa `BOS03` mientras la base de tiendas usa `S03`; los estados tipo `'001 - ACTIVO'` se comparan por el último segmento tras el guion, exacto — no por contención de texto, porque `'INACTIVO'` también contiene la subcadena `'ACTIVO'`).
3. **Distribución** — el motor asigna las cajas disponibles priorizando las tiendas con mayor urgencia y garantizando que el inventario del CEDI quede en cero.
4. **Exportación** — genera el Excel de salida con cinco hojas: Distribución, Resumen, Resumen Ítems, Análisis Comprador y Alertas.

### Cálculos del preprocesamiento

```
inventario_efectivo = inventario_tienda + inventario_transito
dias_proyectados    = inventario_efectivo / consumo_diario      (= ∞ si consumo_diario == 0)
cajas_necesarias    = ceil( max(0, TARGET_DAYS × consumo_diario − inventario_efectivo) )
```

### Lógica de prioridades

Cada tienda recibe cajas según su nivel de urgencia (calculado sobre el inventario/consumo del **grupo de producto espejo**, no del ítem individual — para un ítem sin espejo son idénticos):

| Prioridad | Condición | Acción |
|---|---|---|
| **AGOTADA** | Inventario efectivo de grupo < 0.3 cajas | Recibe primero, mínimo 1 caja |
| **STOCK DE SEGURIDAD** | Inventario efectivo de grupo < 0.6 cajas | Segunda en recibir, mínimo 1 caja |
| **REPOSICIÓN** | Menos de 3 días de inventario proyectado de grupo | Recibe cajas para llegar al objetivo |
| **CUBIERTA** | 3 o más días de inventario proyectado de grupo | Solo recibe si queda sobrante |

El inventario proyectado considera tanto las existencias físicas en tienda como el inventario ya en tránsito, evitando sobre-abastecimiento.

### Las tres fases de reparto

Dentro de cada ítem, las tiendas elegibles se ordenan por prioridad descendente y consumo descendente, y se reparten en tres fases secuenciales con alcance de negocio distinto:

**Fase 1 — Rondas crecientes (cap 1 → `MIN_CAJAS_INICIAL`)**
Todas las tiendas elegibles reciben su 1.ª caja antes de que cualquiera reciba su 2.ª. Evita que tiendas de alto consumo acaparen el stock cuando hay múltiples tiendas agotadas.

**Fase 2 — Proporcional a `TARGET_DAYS`**
Si el stock no alcanza para llevar a todas las tiendas a 3 días, la escasez se reparte proporcionalmente a la necesidad de cada una (`floor` + corrección Hamilton para el residuo entero). Para ítems que comparten grupo de producto espejo, la necesidad se calcula UNA VEZ por grupo y se reparte entre sus SKUs — no se redondea hacia arriba (`ceil`) por ítem por separado, porque eso sobreprovisiona el CEDI.

**Fase 3 — Sobrante ponderado por días**
Si quedan cajas tras cubrir 3 días en todas las tiendas, se reparten por rondas proporcional a `consumo_diario / días_actuales` (no "1 en 1" ni por consumo a secas) entre las tiendas elegibles, siempre que estén bajo `TOPE_EXCEDENTE = 6` días proyectados tras recibir. Esto reparte DÍAS de forma pareja, no cajas — repartir la misma cantidad de cajas entre tiendas de consumo muy distinto da días muy distintos por caja. Si todas las tiendas elegibles llegan al tope, un "Paso B" de cero-residuo reparte lo que quede por menor `días_actuales`, ignorando el tope de cajas por entrega — la garantía de cero residuo tiene prioridad.

### Restricciones absolutas

| Restricción | Detalle |
|---|---|
| Solo cajas completas | `Pedido Final` siempre es entero — nunca fracciones |
| Tope por tienda-ítem | Máximo 3 cajas por tienda-ítem (salvo el fallback de cero residuo) |
| Tiendas sin consumo | Solo reciben si están en prioridad AGOTADA o SAFETY |
| Cero residuo | El sobrante final se vacía en round-robin sobre tiendas con `consumo_diario > 0` |
| Sin efectos secundarios | Ningún módulo escribe a disco; solo `run.py` y el botón de descarga de la UI persisten archivos |

### Garantía de distribución total

El algoritmo tiene una regla no negociable: **la suma de cajas asignadas a todas las tiendas debe ser igual al 100% de las cajas disponibles en el CEDI**, para cada ítem que llegue a tener al menos una tienda elegible. La validación ocurre en tres capas:

1. **Durante la distribución**: si tras las 3 fases queda residuo, se genera una alerta `CRÍTICO` (solo ocurre si un ítem no tiene ninguna tienda elegible en Celes).
2. **En el output**: la hoja **Alertas** (una fila por problema, ordenadas por severidad, o `"OK — Sin alertas"`) y la métrica "Cajas sin distribuir" en la UI.
3. **Calidad de datos aguas arriba** (`preprocessor.py`): combinaciones tienda-ítem sin match en Celes, ítems huérfanos con stock pero sin tienda elegible, filas descartadas por código no normalizable, e ítems nuevos o descontinuados detectados por validación cruzada contra Portafolio Fruver.

El algoritmo es completamente determinista: ejecutarlo varias veces con los mismos archivos produce exactamente el mismo output. Esto asume que los archivos de `Input/` no cambian entre ejecuciones — si el Stock, Celes u otro archivo se edita mientras una corrida está en curso (o entre una corrida y la siguiente), el resultado sí cambia, porque cambiaron los datos de origen, no porque el algoritmo haya dejado de ser determinista.

### Parámetros del algoritmo

Ubicados en `distribuidor-fruver/core/algorithm.py` (fuente de verdad si este documento queda desactualizado):

| Parámetro | Valor actual | Descripción |
|---|---|---|
| `TARGET_DAYS` | `3.0` | Días de inventario objetivo por tienda |
| `MIN_STOCK_AGOTADO` | `0.3` | Umbral de stock (cajas) para clasificar como AGOTADA |
| `MIN_STOCK_SAFETY` | `0.6` | Umbral de stock (cajas) para clasificar como SAFETY |
| `MIN_CAJAS_INICIAL` | `2` | Cap máximo de la Fase 1 (rondas hasta cap 2) |
| `MAX_CAJAS_POR_ITEM` | `3` | Tope duro de cajas por tienda-ítem |
| `TOPE_EXCEDENTE` | `6` | Días máximos que puede acumular una tienda del sobrante |
| `UMBRAL_CONCENTRACION_SIN_CONSUMO` | `5` | Cajas a partir de las cuales se activa reparto equitativo en vez de concentrar en una sola tienda por falta de datos de consumo |

---

## Archivo de salida

`distribucion_fruver_YYYYMMDD.xlsx`, con **cinco hojas**: Distribución, Resumen, Resumen Ítems, Análisis Comprador y Alertas.

### Hoja "Distribución"

El pedido ejecutable, ordenado por tienda y luego por ítem.

| Columna | Contenido |
|---|---|
| Centro Operacional de la Bodega | COD SIESA de la tienda destino (ej. `S03`) |
| Nombre Tienda | Nombre de la tienda |
| Zona | Zona geográfica de la tienda |
| Código de Producto | Código del ítem (entero) |
| Nombre Ítem | Descripción del ítem |
| UM | Unidad de medida (referencia, ej. `CJ20`) |
| Consumo Diario | Cajas/día de esa tienda |
| Stock Antes Pedido | Inventario efectivo antes del pedido |
| Días Inventario Actual | Días de stock antes del pedido |
| **Pedido Final** | **Cajas completas a enviar (siempre entero)** |
| Stock Después Pedido | Inventario efectivo tras recibir el pedido |
| Días Inventario Proyectado | Días de stock proyectados después del pedido |

### Hoja "Resumen"

Total de cajas por tienda, ordenado de mayor a menor. Incluye COD SIESA, Nombre Tienda, Zona y Total Cajas.

### Hoja "Resumen Ítems"

Total de cajas por ítem, desglosado por Fase 1 / Fase 2 / Fase 3, con notas explicativas de cada fase en las primeras filas.

### Hoja "Análisis Comprador"

Por ítem, compara las cajas disponibles en CEDI contra el mínimo necesario para cubrir `TARGET_DAYS` en todas las tiendas, y clasifica el nivel de riesgo de sobre-compra para orientar la próxima decisión de compra. Incluye el riesgo de merma por ítem (`# Tiendas con Riesgo Merma` / `% Tiendas con Riesgo`, contra el umbral `min(10, vida_útil)`) — el detalle tienda-ítem vivía antes en una hoja aparte ("Riesgo Merma"), que se quitó a pedido del usuario.

### Hoja "Alertas"

Todos los casos borde detectados durante el procesamiento (algoritmo y preprocesamiento), ordenados por severidad. Si todo va bien, muestra `"OK — Sin alertas"`.

---

## Mejoras frente al proceso manual en Excel

### Resumen ejecutivo

| Dimensión | Proceso manual | Distribuidor Fruver |
|---|---|---|
| Tiempo de ejecución | 2–4 horas | < 30 segundos |
| Errores de cruce de datos | Frecuentes (códigos distintos entre archivos) | Eliminados (normalización automática) |
| Criterio de distribución | Subjetivo / basado en experiencia individual | Algoritmo reproducible con reglas documentadas |
| Garantía de 0 residuo | Manual, propenso a errores | Garantizado matemáticamente |
| Trazabilidad | Baja (depende del archivo de cada persona) | Alta (hojas Resumen, Resumen Ítems y Alertas auditables) |
| Riesgo operativo | Alto (depende de una persona) | Bajo (cualquier operador puede ejecutarlo) |

### Detalle de mejoras

**1. Velocidad**
El proceso manual requiere abrir múltiples archivos, hacer cruces con VLOOKUP o tablas dinámicas, y ajustar manualmente los pedidos ítem por ítem. La herramienta procesa todos los ítems simultáneamente en segundos.

**2. Eliminación de errores de cruce**
Los archivos de entrada usan convenciones de codificación distintas (prefijos en códigos de bodega, ceros a la izquierda en códigos de producto, nombres de columnas variables). En el proceso manual estos cruces fallan silenciosamente: un VLOOKUP que no encuentra el código simplemente devuelve vacío y la tienda no recibe pedido. La herramienta normaliza automáticamente todas las claves y reporta en la hoja Alertas cuando un cruce no produce resultados (p. ej. tienda-ítem sin match en Celes, o ítems con stock que se quedaron sin ninguna tienda elegible).

**3. Criterio objetivo y consistente**
En el proceso manual, la decisión de cuánto enviar a cada tienda depende del criterio y la experiencia de quien lo ejecuta. Esto genera variabilidad: distintas personas producen distribuciones distintas con los mismos datos. El algoritmo aplica siempre las mismas reglas: inventario proyectado mínimo de 3 días, prioridad a tiendas agotadas, distribución equitativa del sobrante.

**4. Consideración del inventario en tránsito**
El proceso manual frecuentemente distribuye sin considerar los pedidos ya en camino, lo que genera sobre-abastecimiento. La herramienta combina existencias físicas más tránsito para calcular el inventario efectivo real de cada tienda antes de asignar cajas.

**5. Garantía matemática de cero residuo**
Manualmente es fácil que queden cajas sin asignar por errores de redondeo o porque una tienda recibió de más. El motor de distribución garantiza algebraicamente que `suma(pedidos) = cajas disponibles CEDI`, ítem por ítem.

**6. Alertas automáticas**
Cuando hay un problema (ítem sin tiendas elegibles, archivo con columnas faltantes, cajas sin distribuir), la herramienta lo señala inmediatamente con un mensaje de error en la interfaz y lo registra en la hoja Alertas del Excel de salida. En el proceso manual estos problemas pasan desapercibidos o se detectan días después.

**7. Trazabilidad y auditoría**
El archivo de salida incluye cinco hojas: la distribución detallada, un resumen de cajas por tienda, un desglose por ítem y fase de reparto, un análisis para el comprador (incluye riesgo de merma por ítem) y una hoja de alertas. Cualquier persona puede revisar por qué una tienda recibió determinada cantidad. En el proceso manual esto requiere recordar o documentar manualmente las decisiones tomadas.

**8. Reducción del riesgo operativo**
El proceso manual depende de que una persona específica sepa ejecutarlo correctamente. Si esa persona no está disponible, la distribución se retrasa o la ejecuta alguien sin experiencia con mayor riesgo de error. La herramienta puede ser operada por cualquier miembro del equipo con acceso a los archivos de entrada.

---

## Tecnología

La herramienta corre como aplicación web local (o en la nube) usando Python y Streamlit. No requiere instalación de software adicional por parte del operador más allá de abrir el navegador. Los archivos de entrada nunca se modifican ni se almacenan en el servidor — todo se procesa en memoria y el único output es el archivo descargable.

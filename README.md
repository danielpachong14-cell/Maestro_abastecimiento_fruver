# Plan de Traslados Nacional

Genera un plan diario de traslados de inventario entre tiendas de la cadena, redistribuyendo excesos desde tiendas con demasiados días de inventario hacia tiendas que están por debajo del umbral permitido por cuadrante.

---

## Cómo ejecutar

```powershell
python analisis_traslados.py
```

El archivo de salida se crea en `Output/Plan_Traslados_YYYYMMDD.xlsx`.

**Dependencias:** Python 3.x, `pandas`, `openpyxl`.

---

## Archivos de entrada (`Input/`)

| Archivo | Hoja | Columnas clave | Propósito |
|---|---|---|---|
| `DB_Tiendas.xlsx` | default | `BODEGA`, `C.O`, `Zona`, `NOMBRE DE TIENDA` | Maestro de las 95 tiendas: código, zona y nombre |
| `DB_CELES.xlsx` | `Items` | `C BODEGA`, `Item`, `Cuadrante de Producto`, `(=) Inventario Total`, `Consumo Diario` | Inventario nacional (~117 000 filas, 13 cuadrantes) |
| `DB_PORTAFOLIO_FRUVER.xlsx` | default | `COD SIESA`, `ITEM` | Portafolio autorizado: qué productos puede recibir cada tienda — **exclusivo para Q16-FRUVER** |
| `DB_POLITICA_NACIONAL_DIAS_DE_INVENTARIO_POR_CUADRANTE.xlsx` | default | `CUADRANTE`, `Dias de Inventario Maximos` | Umbral máximo de días de inventario por cuadrante |

**Cómo se cruzan:**
- `DB_Tiendas.BODEGA` = `DB_CELES.C BODEGA` — enriquece inventario con zona y nombre
- `DB_Tiendas.C.O` = `DB_PORTAFOLIO_FRUVER.COD SIESA` — convierte código de portafolio al código de bodega

---

## Parámetros configurables

Todos los umbrales están agrupados al inicio del script y se pueden modificar sin tocar la lógica.

| Parámetro | Valor por defecto | Descripción |
|---|---|---|
| `DIAS_EXCESO_ORIGEN` | 15 | Fallback de días máximos para cuadrantes no definidos en el archivo de política |
| `DIAS_MINIMO_ORIGEN` | 4 | Días mínimos que debe conservar la tienda origen después de enviar (todos los cuadrantes) |
| `CAJAS_MINIMO_ORIGEN` | 0.8 | Cajas mínimas absolutas que debe retener el origen (se toma el mayor entre este y `DIAS_MINIMO_ORIGEN × consumo`) |
| `CAJAS_MINIMO_POR_CUADRANTE` | Q17→0.7, Q18→0.5 | Override del mínimo de cajas por cuadrante (se superpone a `CAJAS_MINIMO_ORIGEN`) |
| `CAJAS_MINIMO_TRASLADO` | 1 | Cantidad mínima de cajas por traslado individual |
| `DIAS_MAXIMO_DESTINO` | 15 | Umbral del filtro de verificación post-proceso: destino que ya supera este nivel no recibe traslados adicionales en el mismo ciclo |
| `CAJAS_MINIMO_TOTAL_ORIGEN` | 5 | Mínimo de cajas totales que debe enviar un origen para justificar el operativo; si no llega a este valor sus traslados se eliminan en verificación |

---

## Algoritmo paso a paso

### 1. Preparación de datos

- Se lee el inventario y se calculan los **días de inventario**: `Dias Calc = Inventario Total / Consumo diario`.
- Se carga `dias_max_cuadrante` desde el archivo de política: mapa de cuadrante → umbral máximo de días.
- Se construye `portafolio_valido`: conjunto de tuplas `(bodega, item)` que indica qué productos puede recibir cada tienda. **Aplica únicamente a Q16-FRUVER**; todos los demás cuadrantes no tienen restricción de portafolio.
- Se construye `stock_simulado`: copia mutable del inventario. Se actualiza después de cada traslado para que las iteraciones siguientes reflejen el estado acumulado.

### 2. Selección de orígenes (fuentes)

Una tienda-producto es candidata a enviar si cumple las tres condiciones:

```
Dias Calc > dias_max_cuadrante[cuadrante]   (tiene exceso según política)
Consumo diario > 0                           (el producto se vende)
Inventario Total > 0                         (tiene stock físico)
```

### 3. Cálculo del disponible en origen

Para cada origen, las cajas que puede enviar sin quedar en riesgo:

```
cajas_minimo = CAJAS_MINIMO_POR_CUADRANTE.get(cuadrante, CAJAS_MINIMO_ORIGEN)
minimo_retener = max(cajas_minimo, consumo × DIAS_MINIMO_ORIGEN)
disponible     = floor(stock_simulado - minimo_retener)
```

Si `disponible < CAJAS_MINIMO_TRASLADO` el origen no puede enviar nada y se registra en **Sin Destino Posible**.

### 4. Búsqueda y ordenamiento de destinos

Para el item del origen, se recorren todas las tiendas que lo tienen y se descartan:

| Condición de exclusión | Motivo |
|---|---|
| Misma bodega que el origen | No se traslada a sí misma |
| `(bodega, item) ∈ ya_fue_origen` | Anti-ciclo: esa tienda ya envió este item en esta corrida |
| Par no está en `portafolio_valido` | Solo para Q16-FRUVER: no está autorizado a recibir este producto |
| `Consumo diario = 0` | Sin consumo no tiene sentido enviar |
| `Dias Inv ≥ dias_max_cuadrante[cuadrante]` | Ya tiene stock suficiente según política |

Los candidatos que pasan los filtros se clasifican en:
- **Misma Zona** — prioridad 1
- **Sugerencia Otra Zona** — prioridad 2

Dentro de cada grupo se ordenan de menor a mayor días de inventario (los más urgentes primero).

### 5. Cálculo de cajas a trasladar

Para cada destino candidato:

```
necesidad = ceil((dias_max_cuadrante[cuadrante] - dias_destino) × consumo_destino)
cajas     = floor(min(necesidad, disponible))
```

Si `cajas < CAJAS_MINIMO_TRASLADO` se salta ese destino. Si es válido:

- Se registra el traslado.
- Se actualiza `stock_simulado` para origen y destino.
- Se reduce `disponible` del origen.
- Se marca `(bodega_src, item)` en `ya_fue_origen`.

### 6. Verificación post-proceso

Después de calcular todos los traslados se aplican dos filtros adicionales:

**Filtro 1 — Sobre-stock combinado en destino**
Si un destino recibe traslados de múltiples orígenes y ya alcanzó `DIAS_MAXIMO_DESTINO` gracias al primer traslado, los traslados posteriores a ese mismo (item, destino) se eliminan. El primer traslado siempre se acepta.

**Filtro 2 — Volumen mínimo por origen**
Si la suma total de cajas enviadas por un origen en un tipo de traslado (Misma Zona o Sugerencia Otra Zona) es menor a `CAJAS_MINIMO_TOTAL_ORIGEN`, todos sus traslados de ese tipo se eliminan porque no justifican el operativo logístico. Los dos tipos se evalúan de forma independiente.

---

## Reglas de negocio (no modificar sin aprobación)

- Se usa únicamente la columna `(=) Inventario Total` — no `Inv Traslado proces`.
- Items con `Consumo diario = 0` se excluyen completamente (ni origen ni destino).
- Las cantidades trasladadas son **enteros enteros** (`math.floor`).
- El mínimo a retener en origen es el mayor entre: `CAJAS_MINIMO_POR_CUADRANTE` (Q17→0.7, Q18→0.5, resto→0.8) y `DIAS_MINIMO_ORIGEN × consumo`.
- Los umbrales de días por cuadrante vienen del archivo de política nacional; el fallback es `DIAS_EXCESO_ORIGEN`.
- **Portafolio**: restricción de qué (tienda, item) puede recibir stock — **aplica únicamente a Q16-FRUVER**. Todos los demás cuadrantes pueden recibir cualquier item sin restricción.
- Una tienda que ya actuó como **origen** de un item no puede ser **destino** del mismo item en la misma corrida (control anti-ciclo). El anti-ciclo es por item: una misma tienda puede recibir items distintos al que envió.

---

## Hojas de salida (en orden)

| # | Hoja | Contenido |
|---|---|---|
| 1 | **Resumen Ejecutivo** | KPIs: total traslados, cajas, impacto en días de inventario, top 5 ítems y tiendas, traslados eliminados en verificación, resumen por cuadrante y zona |
| 2 | **Traslados Misma Zona** | Traslados confirmados entre tiendas de la misma zona |
| 3 | **Sugerencias Otra Zona** | Traslados sugeridos entre zonas distintas (requieren aprobación logística adicional) |
| 4 | **Sin Destino Posible** | Ítems con exceso que no tienen receptor válido; incluye motivo diagnóstico |
| 5 | **Resumen por Zona** | Cajas enviadas, recibidas e ítems sin solución agregados por zona |

**Orden de columnas en hojas 2–3:** Item → Producto → Cuadrante → Cajas → Tipo → Bloque Origen (Bodega, Tienda, Zona, stocks, consumo, días antes/después) → Bloque Destino (misma estructura).

---

## Tests

```powershell
python test_traslados.py
```

23 escenarios cubiertos:

| # | Escenario |
|---|---|
| 1 | Traslado básico exitoso |
| 2 | Portafolio bloquea el traslado (Q16-FRUVER) |
| 3 | Destino saturado (≥ umbral del cuadrante) no recibe |
| 4 | Anti-ciclo: tienda origen no puede recibir el mismo item que envió |
| 5 | Múltiples destinos hasta agotar el disponible |
| 6 | Anti-ciclo confirmado (variante) |
| 7 | Prioridad misma zona sobre otra zona (aunque otra zona sea más urgente) |
| 8 | Item con consumo = 0 excluido como fuente |
| 9 | Destino con consumo = 0 excluido |
| 10 | Stock insuficiente en origen → sin_destino con motivo diagnóstico |
| 11 | Umbral exacto (dias_calc == dias_max) no entra como fuente |
| 12 | Cross-zona cuando no hay candidatos en misma zona |
| 13 | Días proyectados del destino llegan exactamente al umbral del cuadrante |
| 14 | Origen conserva el mínimo de días tras el traslado |
| 15 | Dos ítems distintos procesados sin interferencia |
| 16 | Verificación Filtro 1: destino combinado ya saturado → segundo traslado eliminado |
| 17 | Verificación Filtro 2: origen envía menos de `CAJAS_MINIMO_TOTAL_ORIGEN` → traslados eliminados |
| 18 | Anti-ciclo no afecta ítems distintos (origen de A puede recibir B) |
| 19 | Portafolio no aplica a cuadrantes distintos de Q16-FRUVER |
| 20 | Por-cuadrante dias_max: cada cuadrante usa el suyo como umbral |
| 21 | Cajas trasladadas son siempre enteros (`math.floor` garantizado) |
| 22 | Stock simulado se actualiza en cadena entre fuentes sucesivas |
| 23 | Filtro 2 evaluado independientemente por tipo (Misma Zona vs Otra Zona) |

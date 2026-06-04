# Resumen del Sistema — Distribuidor Fruver

---

## 1. Archivos de origen (6 Excel)

| Archivo | Clave interna | Obligatorio |
|---|---|---|
| `Stock.xlsx` | `stock` | Sí |
| `Celes.xlsx` | `celes` | Sí |
| `DB_Portafolio Fruver.xlsx` | `portafolio` | Sí |
| `DB_Tiendas.xlsx` | `tiendas` | Sí |
| `DB_Tiendas Por itmes y portafolio.xlsx` | `tiendas_item` | Sí |
| `Tiendas_No generar pedido.xlsx` | `excluidas` | No |

---

## 2. Datos usados de cada archivo

### Stock (Siesa)
| Campo | Uso |
|---|---|
| `Item` | Código del ítem (texto con ceros: `"0000155"` → se normaliza a entero `155`) |
| `Desc. item` | Nombre del ítem |
| `Cant. disponible` | Cajas disponibles en el CEDI *(ya en cajas, no se divide por Factor U.M.)* |
| `U.M.` | Unidad de medida — solo para el output, no para cálculos |

### Celes
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

### Portafolio Fruver
| Campo | Uso |
|---|---|
| `ITEM` | Código de ítem |
| `ESTADO` | Se filtra solo registros con estado `ACTIVO` |

> No aporta datos al cálculo. Sirve como referencia de ítems activos.

### Tiendas × Ítem
| Campo | Uso |
|---|---|
| `COD SIESA` | Código tienda |
| `DB_Portafolio_Fruver.ITEM` | Código ítem |
| `DB_Portafolio_Fruver.ESTADO` | Se filtra solo `ACTIVO` |

> **Fuente de verdad de elegibilidad**: determina qué tienda puede recibir qué ítem.

### Base de Tiendas
| Campo | Uso |
|---|---|
| `COD SIESA` | Clave de cruce con el resto del pipeline |
| `ZONA` | Zona geográfica de la tienda — se incluye en el output |

### Tiendas sin pedido *(opcional)*
| Campo | Uso |
|---|---|
| `Centro Operacional de la Bodega` | Lista de COD SIESA que se excluyen completamente de la distribución en esa ejecución |

---

## 3. Cálculos que se realizan

### Preprocesamiento

```
inventario_efectivo = inventario_tienda + inventario_transito
dias_proyectados    = inventario_efectivo / consumo_diario
                      (= ∞ si consumo_diario == 0)
cajas_necesarias    = ceil( max(0, TARGET_DAYS × consumo_diario − inventario_efectivo) )
```

### Clasificación de prioridad (por tienda-ítem)

```
Prioridad 3 — AGOTADA      si inventario_efectivo < 0.3 cajas
Prioridad 2 — SAFETY       si inventario_efectivo < 0.5 cajas
Prioridad 1 — REPOSICIÓN   si dias_proyectados    < 3.0 días
Prioridad 0 — CUBIERTA     si dias_proyectados    ≥ 3.0 días
```

### Fase 2a — escala proporcional

```
scale                  = min(1.0, remaining / total_need)
cajas_proporcionales   = floor(need × scale)
residuo entero         → tiendas con mayor fracción decimal (corrección Hamilton)
```

### Fase 2b — días proyectados actualizados para el sobrante

```
dias_actuales = (inventario_efectivo + cajas_asignadas) / consumo_diario
```

### Output

```
stock_despues_pedido  = inventario_efectivo + cajas_asignadas
dias_proyectado_final = stock_despues_pedido / consumo_diario
```

---

## 4. Procedimiento y restricciones

### Flujo por ítem

1. Se toman todas las tiendas elegibles para ese ítem (cruce `tiendas_item` × `celes`).
2. Se excluyen las tiendas del archivo opcional `Tiendas_No generar pedido.xlsx`.
3. Se clasifican por prioridad y se ordenan: **prioridad DESC, consumo DESC**.

**Fase 1 — Rondas crecientes (cap 1 → 2)**
Todas las tiendas reciben su 1.ª caja antes de que alguna reciba la 2.ª. Evita que tiendas de alto consumo acaparen el stock cuando hay múltiples tiendas agotadas.

**Fase 2a — Proporcional a TARGET_DAYS**
Si el stock no alcanza para llevar a todas las tiendas a 3 días, la escasez se reparte proporcionalmente a la necesidad de cada una. Ninguna tienda acapara cajas a costa de otra.

**Fase 2b — Sobrante equitativo**
Si quedan cajas tras cubrir 3 días en todas las tiendas, se dan de 1 en 1 a las tiendas con **menos cajas asignadas** (anti-concentración), siempre que estén bajo `TOPE_EXCEDENTE = 5` días proyectados.

### Restricciones absolutas

| Restricción | Detalle |
|---|---|
| Solo cajas completas | `Pedido Final` siempre es entero — nunca fracciones |
| Tope por tienda-ítem | Máximo `3` cajas por tienda-ítem (salvo el fallback de cero residuo) |
| Tiendas sin consumo | Solo reciben si están en prioridad AGOTADA o SAFETY |
| Cero residuo | El sobrante final se vacía en round-robin sobre tiendas con `consumo_diario > 0` |
| Sin efectos secundarios | Ningún módulo escribe a disco; solo `run.py` y el botón de descarga de la UI persisten archivos |

---

## 5. Parámetros del algoritmo

Ubicados en `distribuidor-fruver/core/algorithm.py`:

| Parámetro | Valor actual | Descripción |
|---|---|---|
| `TARGET_DAYS` | `3.0` | Días de inventario objetivo por tienda |
| `MIN_STOCK_AGOTADO` | `0.3` | Umbral de stock (cajas) para clasificar como AGOTADA |
| `MIN_STOCK_SAFETY` | `0.5` | Umbral de stock (cajas) para clasificar como SAFETY |
| `MIN_CAJAS_INICIAL` | `2` | Cap máximo de la Fase 1 (rondas hasta cap 2) |
| `MAX_CAJAS_POR_ITEM` | `3` | Tope duro de cajas por tienda-ítem |
| `TOPE_EXCEDENTE` | `5` | Días máximos que puede acumular una tienda del sobrante |

---

## 6. Validación de que todo se envíe correctamente

El sistema garantiza `sum(cajas_asignadas) == cajas_disponibles_cedi` para cada ítem.

### Capa 1 — durante la distribución

Si tras las 3 fases `remaining > 0`, se genera una alerta tipo `CRÍTICO`:

```
[CRÍTICO] Ítem 155: Quedaron N caja(s) sin distribuir — revisar portafolio/tiendas elegibles
```

Esto solo ocurre si un ítem no tiene ninguna tienda elegible en Celes.

### Capa 2 — en el output

La hoja **Alertas** del Excel y la métrica **"Cajas sin distribuir"** en la UI muestran el total de cajas no distribuidas. Si el valor es `0`, la distribución es completa y correcta.

### Determinismo

El algoritmo es completamente determinista. Ejecutar el proceso múltiples veces con los mismos archivos de input produce exactamente el mismo output. No hay aleatoriedad, ni valores dependientes del tiempo de ejecución. El único cambio entre ejecuciones distintas es el nombre del archivo (incluye la fecha del día).

---

## 7. Output

Archivo `distribucion_fruver_YYYYMMDD.xlsx` con **3 hojas**:

### Hoja "Distribución"

El pedido ejecutable, ordenado por tienda y luego por ítem.

| Columna | Descripción |
|---|---|
| Centro Operacional de la Bodega | COD SIESA de la tienda (ej. `S03`) |
| Nombre Tienda | Nombre de la tienda |
| Zona | Zona geográfica de la tienda (ej. `BOGOTA NORTE`) |
| Código de Producto | Código del ítem (entero) |
| Nombre Ítem | Descripción del ítem |
| UM | Unidad de medida (ej. `CJ20`) |
| Consumo Diario | Cajas/día de esa tienda |
| Stock Antes Pedido | Inventario efectivo antes del pedido |
| Días Inventario Actual | Días de stock antes del pedido |
| **Pedido Final** | **Cajas a enviar (entero)** |
| Stock Después Pedido | Inventario efectivo tras recibir el pedido |
| Días Inventario Proyectado | Días de stock proyectados después del pedido |

### Hoja "Resumen"

Total de cajas por tienda, ordenado de mayor a menor. Incluye COD SIESA, Nombre Tienda, Zona y Total Cajas.

### Hoja "Alertas"

Casos borde detectados durante el procesamiento. Si todo va bien, muestra `OK — Sin alertas`.

# Distribuidor Fruver — Descripción del Proyecto

## ¿Qué es?

El **Distribuidor Fruver** es una herramienta interna desarrollada por Isimo que automatiza la distribución del inventario de productos Fruver desde el CEDI hacia las tiendas activas de la cadena.

Funciona como una aplicación web liviana: el operador sube cinco archivos Excel, presiona un botón y en segundos descarga el archivo de pedidos listo para ejecutarse en el sistema, con el 100% del inventario disponible repartido entre las tiendas.

---

## ¿Cómo funciona?

### Entradas

La herramienta recibe cinco archivos Excel que ya existen en los procesos del negocio:

| Archivo | Contenido |
|---|---|
| **Stock CEDI (Siesa)** | Inventario disponible en el CEDI por ítem (en cajas) |
| **Celes** | Consumos históricos, existencias en tienda e inventario en tránsito |
| **Portafolio Fruver** | Clasificación de tipo de portafolio por ítem |
| **Base de Tiendas** | Maestro de tiendas activas con sus códigos |
| **Tiendas × Ítem** | Matriz de elegibilidad: qué tiendas pueden recibir qué ítem |

### Proceso interno

```
5 Excel → carga y validación → normalización → algoritmo de distribución → archivo de pedidos
```

1. **Carga y validación** — verifica que cada archivo tenga las columnas requeridas y alerta si falta algo antes de procesar.
2. **Normalización** — resuelve automáticamente las diferencias de codificación entre los archivos (ej. el stock usa `'0000155'` como texto, mientras los otros usan `155` como número; Celes usa `BOS03` mientras la base de tiendas usa `S03`).
3. **Distribución** — el motor asigna las cajas disponibles priorizando las tiendas con mayor urgencia y garantizando que el inventario del CEDI quede en cero.
4. **Exportación** — genera el Excel de salida con tres hojas: Distribución, Resumen y Alertas.

### Lógica de prioridades

Cada tienda recibe cajas según su nivel de urgencia:

| Prioridad | Condición | Acción |
|---|---|---|
| **AGOTADA** | Inventario efectivo < 0.3 cajas | Recibe primero, mínimo 1 caja |
| **STOCK DE SEGURIDAD** | Inventario efectivo < 0.5 cajas | Segunda en recibir, mínimo 1 caja |
| **REPOSICIÓN** | Menos de 3 días de inventario proyectado | Recibe cajas para llegar al objetivo |
| **CUBIERTA** | 3 o más días de inventario proyectado | Solo recibe si queda sobrante |

El inventario proyectado considera tanto las existencias físicas en tienda como el inventario ya en tránsito, evitando sobre-abastecimiento.

### Garantía de distribución total

El algoritmo tiene una regla no negociable: **la suma de cajas asignadas a todas las tiendas debe ser igual al 100% de las cajas disponibles en el CEDI**. Si algún ítem no puede distribuirse completamente (por ejemplo, porque no tiene tiendas elegibles), la herramienta genera una alerta crítica visible.

### Archivo de salida

`distribucion_fruver_YYYYMMDD.xlsx` con las columnas:

| Columna | Contenido |
|---|---|
| Centro Operacional de la Bodega | Código de la tienda destino |
| Código de Producto | Código del ítem |
| UM | Unidad de medida (referencia) |
| Pedido Final | Cajas completas a enviar (siempre entero) |

---

## Mejoras frente al proceso manual en Excel

### Resumen ejecutivo

| Dimensión | Proceso manual | Distribuidor Fruver |
|---|---|---|
| Tiempo de ejecución | 2–4 horas | < 30 segundos |
| Errores de cruce de datos | Frecuentes (códigos distintos entre archivos) | Eliminados (normalización automática) |
| Criterio de distribución | Subjetivo / basado en experiencia individual | Algoritmo reproducible con reglas documentadas |
| Garantía de 0 residuo | Manual, propenso a errores | Garantizado matemáticamente |
| Trazabilidad | Baja (depende del archivo de cada persona) | Alta (hojas Resumen y Alertas auditables) |
| Riesgo operativo | Alto (depende de una persona) | Bajo (cualquier operador puede ejecutarlo) |

### Detalle de mejoras

**1. Velocidad**
El proceso manual requiere abrir múltiples archivos, hacer cruces con VLOOKUP o tablas dinámicas, y ajustar manualmente los pedidos ítem por ítem. La herramienta procesa todos los ítems simultáneamente en segundos.

**2. Eliminación de errores de cruce**
Los cinco archivos de entrada usan convenciones de codificación distintas (prefijos en códigos de bodega, ceros a la izquierda en códigos de producto, nombres de columnas variables). En el proceso manual estos cruces fallan silenciosamente: un VLOOKUP que no encuentra el código simplemente devuelve vacío y la tienda no recibe pedido. La herramienta normaliza automáticamente todas las claves y detecta cuando un cruce no produce resultados.

**3. Criterio objetivo y consistente**
En el proceso manual, la decisión de cuánto enviar a cada tienda depende del criterio y la experiencia de quien lo ejecuta. Esto genera variabilidad: distintas personas producen distribuciones distintas con los mismos datos. El algoritmo aplica siempre las mismas reglas: inventario proyectado mínimo de 3 días, prioridad a tiendas agotadas, distribución equitativa del sobrante.

**4. Consideración del inventario en tránsito**
El proceso manual frecuentemente distribuye sin considerar los pedidos ya en camino, lo que genera sobre-abastecimiento. La herramienta combina existencias físicas más tránsito para calcular el inventario efectivo real de cada tienda antes de asignar cajas.

**5. Garantía matemática de cero residuo**
Manualmente es fácil que queden cajas sin asignar por errores de redondeo o porque una tienda recibió de más. El motor de distribución garantiza algebraicamente que `suma(pedidos) = cajas disponibles CEDI`, ítem por ítem.

**6. Alertas automáticas**
Cuando hay un problema (ítem sin tiendas elegibles, archivo con columnas faltantes, cajas sin distribuir), la herramienta lo señala inmediatamente con un mensaje de error en la interfaz y lo registra en la hoja Alertas del Excel de salida. En el proceso manual estos problemas pasan desapercibidos o se detectan días después.

**7. Trazabilidad y auditoría**
El archivo de salida incluye tres hojas: la distribución detallada, un resumen de cajas por tienda y una hoja de alertas. Cualquier persona puede revisar por qué una tienda recibió determinada cantidad. En el proceso manual esto requiere recordar o documentar manualmente las decisiones tomadas.

**8. Reducción del riesgo operativo**
El proceso manual depende de que una persona específica sepa ejecutarlo correctamente. Si esa persona no está disponible, la distribución se retrasa o la ejecuta alguien sin experiencia con mayor riesgo de error. La herramienta puede ser operada por cualquier miembro del equipo con acceso a los cinco archivos.

---

## Tecnología

La herramienta corre como aplicación web local (o en la nube) usando Python y Streamlit. No requiere instalación de software adicional por parte del operador más allá de abrir el navegador. Los archivos de entrada nunca se modifican ni se almacenan en el servidor — todo se procesa en memoria y el único output es el archivo descargable.

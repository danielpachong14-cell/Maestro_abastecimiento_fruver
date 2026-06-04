# Distribuidor Fruver — Isimo

Herramienta interna de distribución automática del inventario Fruver del CEDI a las
tiendas activas. Procesa 5 archivos Excel y genera un archivo de pedidos donde el
inventario disponible queda en 0.

## Comandos

```bash
source venv/bin/activate          # activar entorno virtual
pip install -r requirements.txt   # instalar dependencias
streamlit run app.py              # iniciar la aplicación (http://localhost:8501)
PYTHONPATH=. pytest tests/ -v     # correr tests unitarios
```

## Tech Stack

Python 3.11+ · Streamlit 1.58 · pandas 3.0 · openpyxl 3.1

## Arquitectura

```
5 Excel → loader.py → preprocessor.py → algorithm.py → exporter.py → distribución.xlsx
```

- `app.py` — UI Streamlit: upload de 5 archivos, botón de ejecución, descarga del resultado.
- `core/loader.py` — Carga y valida columnas requeridas de cada Excel (hoja Celes = `Items`).
- `core/preprocessor.py` — Limpieza, normalización de claves y merge en un DataFrame tienda × ítem.
- `core/algorithm.py` — Motor de distribución con prioridades y garantía de 0 residuo.
- `core/exporter.py` — Genera el Excel de salida (hojas Distribución / Resumen / Alertas).

## Normalización de claves de cruce (CRÍTICO)

Los archivos reales NO cruzan con un merge directo. El preprocessor normaliza automáticamente:

1. **Tienda**: Celes `Código de Bodega` = `BOS03`; Base de Tiendas `COD SIESA` = `S03`.
   Se quita el prefijo `BO` → `normalize_store()`.
2. **Ítem**: Stock `Item` = `'0000155'` (texto con ceros); el resto usa `155` (entero).
   Se normaliza todo a entero → `normalize_item()`.
3. **Fila basura**: Stock incluye una fila `'Gran total'` que se elimina.

```
stock.Item (norm int)   ↔  tiendas_item.DB_Portafolio_Fruver.ITEM (norm int)  ↔  celes.Código de Producto
tiendas_item.COD SIESA  ↔  celes.Código de Bodega sin prefijo 'BO'
```

## Parámetros del algoritmo (core/algorithm.py)

```python
TARGET_DAYS       = 4.0   # días MÍNIMOS de inventario proyectado objetivo por tienda
MIN_STOCK_AGOTADO = 0.3   # inventario_efectivo < 0.3 cajas → AGOTADA (prioridad máxima)
MIN_STOCK_SAFETY  = 0.5   # inventario_efectivo < 0.5 cajas → STOCK SEGURIDAD
MIN_CAJAS_INICIAL = 3     # cap de cajas por tienda en la primera pasada
```

## Lógica de inventario

Todo se trabaja en **cajas completas** (enteros). Nunca en unidades.

```
inventario_efectivo = existencias_en_tienda + cajas_en_tránsito
dias_proyectados    = inventario_efectivo / consumo_diario
cajas_necesarias    = ceil( max(0, TARGET_DAYS × consumo_diario − inventario_efectivo) )
```

`Cant. disponible` en Stock ya viene en cajas — no se divide por Factor U.M.
`U.M.` (ej. `CJ20`) es solo referencia para el output; no se usa en cálculos.

## Lógica de prioridades

Los umbrales AGOTADO y SAFETY se miden en **unidades de stock** (cajas), no en días:

```
Priority 3 (AGOTADO):      inventario_efectivo < 0.3 cajas → mínimo 1 caja, primera en recibir
Priority 2 (SAFETY):       inventario_efectivo < 0.5 cajas → mínimo 1 caja
Priority 1 (REPOSICIÓN):   dias_proyectados < 4.0          → cajas para llegar a 4 días
Priority 0 (CUBIERTO):     dias_proyectados ≥ 4.0          → solo recibe si sobra inventario
```

## Distribución del sobrante

Los 4 días son el **mínimo**, no el máximo. Si quedan cajas sin asignar tras el pase 1,
se suben pases adicionales (cap 4, 5, 6…) ordenando las tiendas por **menos días proyectados**:

```
dias_actuales = (inventario_efectivo + cajas_ya_asignadas) / consumo_diario
```

Ejemplo: tienda A (consumo=1.0, stock=7 cajas) vs tienda B (consumo=0.9, stock=3 cajas).
B tiene 3.3 días proyectados vs A con 7.0 → B recibe primero aunque A tenga más consumo.

## Elegibilidad

- La elegibilidad tienda-ítem viene de **Tiendas × Ítem** (filas con `ESTADO = ACTIVO`).
- Se distribuye a **todas** las tiendas que aparecen en Celes para ese ítem.
  El campo `Aplica para DistribuciónAutomática?` se ignora.

## Garantía de distribución total

Para cada ítem, `sum(cajas_asignadas) == cajas_disponibles_cedi`.
Si un ítem no tiene tiendas en Celes, se reporta alerta CRÍTICA con el residuo.

## Output

`distribucion_fruver_YYYYMMDD.xlsx`, hoja **Distribución**:

| Columna | Contenido |
|---|---|
| `Centro Operacional de la Bodega` | COD SIESA de la tienda (ej. `S03`) |
| `Código de Producto` | código del ítem (entero) |
| `UM` | unidad de medida (solo referencia) |
| `Pedido Final` | cajas completas a enviar (entero) |

Más hojas **Resumen** (total cajas por tienda) y **Alertas**.

## Reglas No Negociables

1. **Cero residuo.** `sum(Pedido Final) == cajas CEDI disponibles` por ítem.
2. **Solo cajas completas.** `Pedido Final` siempre `int`.
3. **Respetar portafolio.** Elegibilidad exclusivamente desde Tiendas × Ítem.
4. **Sin efectos secundarios.** No escribir a disco; el único output es el botón de descarga.
5. **Alertas visibles.** Ítems con residuo aparecen en rojo en la UI y en la hoja Alertas.

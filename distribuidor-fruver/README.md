# 🥦 Distribuidor Fruver — Isimo

Herramienta interna que distribuye automáticamente el inventario Fruver disponible en el
CEDI entre las tiendas activas: sube 6 archivos Excel (+ 1 opcional), presiona un botón y
descarga el archivo de pedidos listo para ejecutar, con el inventario disponible
repartido al 100%.

## Instalación

```bash
cd distribuidor-fruver
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Uso

```bash
streamlit run app.py              # abre http://localhost:8501
```

1. **Cargar archivos** — sube los 6 Excel obligatorios (+ 1 opcional):

   | Archivo | Contenido | Hoja |
   |---------|-----------|------|
   | Stock CEDI (Siesa) | Inventario disponible en el CEDI | `Sheet1` |
   | Celes | Consumos y días de inventario por tienda-ítem | `Items` |
   | Portafolio Fruver | Catálogo de ítems, usado para validación cruzada (ítems nuevos/descontinuados) | — |
   | Base de Tiendas | Maestro de tiendas activas | — |
   | Tiendas × Ítem | Matriz de elegibilidad tienda-ítem (fuente de verdad) | — |
   | Productos Espejo | Agrupa SKUs que son el mismo producto físico | — |
   | Tiendas sin pedido *(opcional)* | Tiendas a excluir de esta corrida | — |

2. **Ejecutar Distribución** — procesa y muestra métricas (ítems, cajas, existencia final)
   y alertas.

3. **Descargar** — `distribucion_fruver_YYYYMMDD.xlsx` con las hojas Distribución, Resumen,
   Resumen Ítems, Análisis Comprador y Alertas.

## Cómo funciona

```
6+1 Excel → loader → preprocessor → algorithm → exporter → distribución.xlsx
```

- **loader** valida que cada archivo traiga sus columnas requeridas.
- **preprocessor** limpia, normaliza las claves de cruce (ver más abajo), arma un único
  DataFrame tienda × ítem y detecta problemas de calidad de datos (retorna alertas).
- **algorithm** distribuye priorizando tiendas agotadas y de alto consumo, completando hasta
  `TARGET_DAYS` de inventario **proyectado** (Existencias + Tránsito) y garantizando 0 residuo.
- **exporter** genera el Excel final formateado, incluida la hoja Alertas.

### Por qué hay normalización de claves

Los archivos reales usan codificaciones distintas que **no cruzan con un merge directo**.
El preprocessor lo resuelve automáticamente:

- Tienda: Celes `BOS03` ↔ Base de Tiendas `S03` (se quita el prefijo `BO`).
- Ítem: Stock `'0000155'` (texto) ↔ resto `155` (entero) → se normaliza a entero.
- Estado (Stock, Portafolio, Tiendas×Ítem): formato `'NNN - ESTADO'` → se compara el
  último segmento tras el guion, EXACTO (no basta con "contiene ACTIVO": `'INACTIVO'`
  también contiene esa subcadena — bug corregido en la auditoría 2026-07).
- Se elimina la fila totalizadora `'Gran total'` (o cualquier variante con "total") del Stock.

### Reglas clave

- El campo `Aplica para DistribuciónAutomática?` de Celes **se ignora** — la elegibilidad
  real viene exclusivamente de Tiendas × Ítem (`ESTADO = ACTIVO`). Ver
  [CLAUDE.md](CLAUDE.md) para el detalle verificado contra el código.
- Solo **cajas completas** (enteros).
- Los archivos de entrada **nunca se modifican** (todo en memoria); el único output es la
  descarga (o `Output/` al correr `run.py` desde la raíz del repo).

## Tests

```bash
PYTHONPATH=. pytest tests/ -v
```

## Despliegue

- **Local** (recomendado): `streamlit run app.py`.
- **Streamlit Community Cloud**: subir el repo, seleccionar `app.py` como entry point.

## Parámetros ajustables

En [core/algorithm.py](core/algorithm.py): `TARGET_DAYS`, `MIN_STOCK_AGOTADO`,
`MIN_STOCK_SAFETY`, `MIN_CAJAS_INICIAL`, `MAX_CAJAS_POR_ITEM`, `TOPE_EXCEDENTE`. Ver
[CLAUDE.md](CLAUDE.md) para el detalle de la lógica y los valores actuales.

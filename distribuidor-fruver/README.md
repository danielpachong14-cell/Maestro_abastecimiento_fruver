# 🥦 Distribuidor Fruver — Isimo

Herramienta interna que distribuye automáticamente el inventario Fruver disponible en el
CEDI entre las tiendas activas: sube 5 archivos Excel, presiona un botón y descarga el
archivo de pedidos listo para ejecutar, con el inventario disponible repartido al 100%.

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

1. **Cargar archivos** — sube los 5 Excel:

   | Archivo | Contenido | Hoja |
   |---------|-----------|------|
   | Stock CEDI (Siesa) | Inventario disponible en el CEDI | `Sheet1` |
   | Celes | Consumos, días de inventario y flag de distribución | `Items` |
   | Portafolio Fruver | Tipo de portafolio por ítem | — |
   | Base de Tiendas | Maestro de tiendas activas | — |
   | Tiendas × Ítem | Matriz de elegibilidad tienda-ítem | — |

2. **Ejecutar Distribución** — procesa y muestra métricas (ítems, cajas, existencia final)
   y alertas.

3. **Descargar** — `distribucion_fruver_YYYYMMDD.xlsx` con las hojas Distribución, Resumen
   y Alertas.

## Cómo funciona

```
5 Excel → loader → preprocessor → algorithm → exporter → distribución.xlsx
```

- **loader** valida que cada archivo traiga sus columnas requeridas.
- **preprocessor** limpia, normaliza las claves de cruce (ver más abajo) y arma un único
  DataFrame tienda × ítem.
- **algorithm** distribuye priorizando tiendas agotadas y de alto consumo, completando hasta
  ~3.5 días de inventario **proyectado** (Existencias + Tránsito) y garantizando 0 residuo.
- **exporter** genera el Excel final formateado.

### Por qué hay normalización de claves

Los archivos reales usan codificaciones distintas que **no cruzan con un merge directo**.
El preprocessor lo resuelve automáticamente:

- Tienda: Celes `BOS03` ↔ Base de Tiendas `S03` (se quita el prefijo `BO`).
- Ítem: Stock `'0000155'` (texto) ↔ resto `155` (entero) → se normaliza a entero.
- Estado producto: Stock trae `'001 - ACTIVO'` → se filtra por contiene `ACTIVO`.
- Se elimina la fila totalizadora `'Gran total'` del Stock.

### Reglas clave

- Solo se distribuye a tiendas con **`Aplica para DistribuciónAutomática? = SI`**. Las `NO`
  son exclusión deliberada del negocio y se omiten en silencio.
- Solo **cajas completas** (enteros).
- Los archivos de entrada **nunca se modifican** (todo en memoria); el único output es la
  descarga.

## Tests

```bash
pytest tests/ -v
```

## Despliegue

- **Local** (recomendado): `streamlit run app.py`.
- **Streamlit Community Cloud**: subir el repo, seleccionar `app.py` como entry point.

## Parámetros ajustables

En [core/algorithm.py](core/algorithm.py): `TARGET_DAYS`, `MIN_DAYS_AGOTADO`,
`MIN_DAYS_SAFETY`, `MIN_CAJAS_INICIAL`. Ver [CLAUDE.md](CLAUDE.md) para el detalle de la
lógica.

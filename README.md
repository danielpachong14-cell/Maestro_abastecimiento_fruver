# Distribuidor Fruver — Isimo

Reparte el inventario Fruver del CEDI entre las tiendas activas, en cajas enteras y sin dejar
sobrante, y calcula la compra al proveedor (por tienda y general) en la misma corrida.

## Instalación (una sola vez)

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
```

## Cómo correrlo

| Modo | Comando |
|---|---|
| Terminal (lee `Input/`, escribe `Output/`) | `python run.py` |
| Interfaz web (subir archivos y descargar) | `streamlit run app.py` → http://localhost:8501 |
| VS Code / IDE | abrir `run.py` y pulsar ▶ Run (intérprete: `.venv`) |
| Tests | `pytest` |

## Entrada: `Input/`

Reemplazar cada día con el **mismo nombre**, con los Excel cerrados:

| Archivo | Contenido | Frecuencia |
|---|---|---|
| `Stock.xlsx` | Cajas disponibles en el CEDI (Siesa) | Diario |
| `Celes.xlsx` | Consumo e inventario por tienda × ítem | Diario |
| `Inventario Tiendas.xlsx` | Último ingreso por tienda × ítem (Siesa) | Diario |
| `Compras_Transito.xlsx` | OC abiertas de Siesa (opcional, evita comprar doble) | Diario |
| `Maestros Fruver.xlsx` | Portafolio, STATUS, Tiendas, Tiendas x Item (MIN/MAX), BD_MAX, Espejo, No generar pedido, Parametros, Dias de compra, Calendario | Cuando cambie |

## Salida: `Output/`

- `distribucion_fruver_AAAAMMDD.xlsx`: **Distribución** (el pedido, columna `Pedido Final`),
  Resumen, Resumen Ítems, Novedades y Alertas.
- `compra_fruver_AAAAMMDD.xlsx`: **Compra General** (la orden; `Compra Final` es editable),
  Compra por Tienda, Costo Exhibición, Bajas Pendientes, Sin Pronóstico, OC en Tránsito,
  Parámetros y Alertas.

Si la terminal muestra `Existencia CEDI    : 0 OK`, todo el stock quedó repartido.
Si la compra falla, la distribución igual queda guardada.
Cerrar los Excel de `Output/` antes de correr: si están abiertos, no se pueden sobrescribir.

Detalle técnico (reglas, fases, parámetros): [CLAUDE.md](CLAUDE.md).

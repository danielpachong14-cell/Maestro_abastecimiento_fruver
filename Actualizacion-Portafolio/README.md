# Automatización Portafolio Fruver — Centro

## Archivos incluidos

| Archivo | Para qué sirve |
|---|---|
| `transformar_portafolio.py` | Lógica principal de transformación |
| `EJECUTAR_PORTAFOLIO.bat` | Doble clic para correr manualmente |
| `watcher.py` | Monitorea el archivo y corre solo al detectar cambios |

---

## Requisitos (una sola vez)

Asegúrate de tener Python instalado.
Luego abre una terminal y ejecuta:

```
pip install pandas openpyxl watchdog
```

---

## Opción 1 — Manual (doble clic)

1. Edita `EJECUTAR_PORTAFOLIO.bat` y ajusta las dos rutas:
   ```
   set ORIGEN=C:\tu\ruta\Segmentación_Portafolio.xlsx
   set DESTINO=C:\tu\ruta\Portafolio_Fruver_Centro_Largo.xlsx
   ```
2. Doble clic en `EJECUTAR_PORTAFOLIO.bat` cuando el archivo fuente se actualice.
3. El archivo de salida se abre automáticamente al terminar.

---

## Opción 2 — Automático con watcher

Deja `watcher.py` corriendo en segundo plano.
Cada vez que guardes el archivo fuente, el output se regenera solo.

```bash
python watcher.py --origen "C:\tu\ruta\archivo.xlsx" --destino "C:\tu\ruta\salida.xlsx"
```

Para que inicie automáticamente con Windows, crea un acceso directo
de `watcher.py` y ponlo en:
```
C:\Users\TuUsuario\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup
```

---

## Opción 3 — Tarea programada (Windows Task Scheduler)

Útil si el archivo se actualiza en un horario fijo (ej. cada lunes).

1. Busca "Programador de tareas" en el menú inicio
2. Crea tarea básica → Semanal / Diaria según tu ciclo
3. Acción: Iniciar programa
   - Programa: `python`
   - Argumentos: `"C:\ruta\transformar_portafolio.py" --origen "C:\ruta\fuente.xlsx" --destino "C:\ruta\salida.xlsx"`

---

## Actualizar rutas

Todo está centralizado en `transformar_portafolio.py`, líneas:

```python
ORIGEN_DEFAULT  = Path(__file__).parent / "Segmentación_Portafolio.xlsx"
DESTINO_DEFAULT = Path(__file__).parent / "Portafolio_Fruver_Centro_Largo.xlsx"
HOJA_FUENTE     = "Segmentación CENTRO"
```

Si la hoja en el Excel fuente cambia de nombre, solo modifica `HOJA_FUENTE`.

---

## Si el archivo fuente cambia de estructura

El script es robusto ante cambios menores (más tiendas, más ítems).
Solo necesita ajuste si:
- Cambia el nombre de la hoja → modifica `HOJA_FUENTE`
- Los encabezados de tienda se mueven de fila → ajusta `df.iloc[0...]` en `construir_dim_tiendas`
- Las columnas de ítems empiezan en otra columna → ajusta `range(13, ...)` en ambas funciones

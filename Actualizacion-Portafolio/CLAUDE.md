# Fruver Portafolio — Automatización

## Qué hace este proyecto
Transforma el archivo Excel de segmentación de portafolio Fruver (hoja "Segmentación CENTRO")
a formato largo: una fila por combinación tienda × ítem marcado con X.

## Archivos clave
- `transformar_portafolio.py` — lógica principal, se puede importar o ejecutar directo
- `watcher.py` — monitorea el archivo fuente y re-ejecuta al detectar cambios
- `Segmentación_Portafolio.xlsx` — archivo fuente (se reemplaza con cada actualización)

## Comandos disponibles

### Correr la transformación una vez
```
python transformar_portafolio.py
```

### Correr con rutas explícitas
```
python transformar_portafolio.py --origen "Segmentación_Portafolio.xlsx" --destino "Portafolio_Fruver_Centro_Largo.xlsx"
```

### Activar el watcher (auto-regenera al guardar el fuente)
```
python watcher.py
```

## Dependencias
```
pip install pandas openpyxl watchdog
```

## Salida esperada
- `Portafolio_Fruver_Centro_Largo.xlsx` con una fila por tienda × ítem con X
- Pestaña "Resumen" con metadata de ejecución (fecha, total filas, tiendas, ítems únicos)
- Log en consola con timestamps

## Estructura del Excel fuente
- Hoja: "Segmentación CENTRO"
- Filas 1-9: metadata de tiendas (zona, código, nombre, NSE, tipo portafolio, muebles)
- Fila 10+: ítems con columnas de presencia (X) por tienda
- Columnas 0-12 (índice 0-based): atributos del ítem (ITEM, DESCRIPCION, CLUSTERIZACIÓN, etc.)
- Columnas 13+: una columna por tienda

## Detalles de implementación

### construir_dim_tiendas()
- Lee filas 0-7 del DataFrame para extraer metadata de tiendas
- Solo incluye columnas donde la fila "Tienda" (iloc[1]) empieza con "S" seguido de dígito o letra mayúscula
- Extrae: Zona (fila 0), Tienda (fila 1), Nombre tienda (fila 2), NSE (fila 3), N° Muebles (fila 5), Tipo portafolio (fila 7)

### construir_items()
- Lee desde fila 9 en adelante (índice 0-based)
- Filtra solo filas donde ITEM es numérico (elimina subtotales y filas vacías)
- Normaliza ITEM como string entero ("12345")

### transformar()
- Melt: convierte columnas de tienda a filas
- Filtra solo filas donde el valor == "X"
- Merge con dim_tiendas por índice de columna
- Ordena: Zona → Tienda → CLUSTERIZACIÓN → ITEM

## Si algo falla
- Verificar que `HOJA_FUENTE = "Segmentación CENTRO"` coincida exactamente con el nombre de la hoja en el Excel
- Las tiendas deben empezar con "S" seguido de dígito/letra — si cambia, ajustar el regex en `construir_dim_tiendas()`
- Si los ítems empiezan en otra fila, ajustar `df.iloc[9:, :]` en `construir_items()`
- Si los atributos del ítem ocupan más/menos columnas, ajustar `range(13, df.shape[1])` en ambas funciones

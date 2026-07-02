"""Carga y validación de los 5 archivos Excel de entrada.

No escribe a disco ni modifica los archivos originales: todo se trabaja sobre
copias en memoria.
"""

import pandas as pd

# Columnas mínimas requeridas en cada archivo. Si falta alguna, se detiene la
# ejecución con un mensaje claro (regla no negociable: no proceder con datos
# incompletos).
REQUIRED_COLUMNS = {
    'stock': ['Item', 'Desc. item', 'Cant. disponible', 'Factor U.M.', 'U.M.',
              'ESTADO DEL PRODUCTO'],
    'celes': ['Código de Bodega', 'Nombre de Bodega', 'Código de Producto',
              'Consumo Diario (Unidades de Distribución)',
              '(=) Días de Inventario Actuales',
              'Inventario Disponible en esta Tienda (Unidades de Distibución)',
              '(-) inventario de traslado en proceso',
              'UM'],
    'portafolio': ['ITEM', 'CLUSTERIZACIÓN', 'ESTADO'],
    'tiendas': ['COD SIESA', 'NOMBRE DE LA TIENDA', 'TIPO DE PORTAFOLIO', 'ZONA'],
    'tiendas_item': ['COD SIESA', 'TIPO DE PORTAFOLIO', 'ITEM', 'ESTADO'],
    'espejo': ['grupo_id', 'item_id'],
    'excluidas': ['Centro Operacional de la Bodega'],
}

# Hoja a leer por archivo. Celes trae los datos en la hoja 'Items'; el resto
# usa la primera hoja.
SHEET_BY_KEY = {
    'stock': 0,
    'celes': 'Items',
    'portafolio': 0,
    'tiendas': 0,
    'tiendas_item': 0,
    'espejo': 0,
    'excluidas': 0,
}

# Etiquetas legibles para los mensajes de error.
LABELS = {
    'stock': 'Stock CEDI (Siesa)',
    'celes': 'Celes (consumos)',
    'portafolio': 'Portafolio Fruver',
    'tiendas': 'Base de Tiendas',
    'tiendas_item': 'Tiendas × Ítem',
    'espejo': 'Productos Espejo',
    'excluidas': 'Tiendas sin pedido',
}


def _read_one(file_obj, key):
    """Lee un Excel a DataFrame. Intenta la hoja preferida y cae a la primera
    si no existe (robustez ante archivos con nombres de hoja distintos)."""
    sheet = SHEET_BY_KEY[key]
    try:
        return pd.read_excel(file_obj, sheet_name=sheet)
    except ValueError:
        # La hoja preferida no existe (ej. Celes con otro nombre): usar la 1ª.
        if hasattr(file_obj, 'seek'):
            file_obj.seek(0)
        return pd.read_excel(file_obj, sheet_name=0)


def _validate_columns(df, key):
    missing = [c for c in REQUIRED_COLUMNS[key] if c not in df.columns]
    if missing:
        raise ValueError(
            f"Al archivo «{LABELS[key]}» le faltan columnas requeridas: "
            f"{', '.join(missing)}"
        )


def load_files(stock, celes, portafolio, tiendas, tiendas_item, espejo, tiendas_excluidas=None):
    """Carga y valida los archivos de entrada.

    Acepta objetos UploadedFile de Streamlit o rutas/buffers compatibles con
    pandas.read_excel. Retorna un dict con DataFrames o lanza ValueError con
    un mensaje claro si falta una columna.

    espejo es obligatorio: define los grupos de productos espejo (mismo
    producto físico, distinto código de ítem) usados por el preprocessor
    para calcular necesidad conjunta por grupo.

    tiendas_excluidas es opcional: si se pasa, se carga y valida el archivo
    «Tiendas sin pedido» y se añade al dict bajo la clave 'excluidas'.
    """
    inputs = {
        'stock': stock,
        'celes': celes,
        'portafolio': portafolio,
        'tiendas': tiendas,
        'tiendas_item': tiendas_item,
        'espejo': espejo,
    }
    dataframes = {}
    for key, file_obj in inputs.items():
        if file_obj is None:
            raise ValueError(f"Falta cargar el archivo «{LABELS[key]}».")
        df = _read_one(file_obj, key)
        # Normalizar espacios en encabezados (algunos vienen con espacios al final).
        df.columns = [str(c).strip() if isinstance(c, str) else c for c in df.columns]
        _validate_columns(df, key)
        dataframes[key] = df

    if tiendas_excluidas is not None:
        df = _read_one(tiendas_excluidas, 'excluidas')
        df.columns = [str(c).strip() if isinstance(c, str) else c for c in df.columns]
        _validate_columns(df, 'excluidas')
        dataframes['excluidas'] = df

    return dataframes

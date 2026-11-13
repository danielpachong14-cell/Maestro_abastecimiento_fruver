"""Carga y validación de los 7 archivos Excel obligatorios (+1 opcional) de entrada.

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
              '(=) Inventario Total',
              'UM'],
    'portafolio': ['ITEM', 'CLUSTERIZACIÓN', 'ESTADO', 'VIDA UTIL EXHIBICION'],
    'tiendas': ['COD SIESA', 'NOMBRE DE LA TIENDA', 'TIPO DE PORTAFOLIO', 'ZONA'],
    'tiendas_item': ['COD SIESA', 'TIPO DE PORTAFOLIO', 'ITEM', 'ESTADO', 'MAX'],
    'espejo': ['grupo_id', 'item_id'],
    'excluidas': ['Centro Operacional de la Bodega'],
    # Extracto de Siesa (hoja «Inv Tiendas»): una fila por ítem × bodega. Se usa
    # SOLO para la fecha de último ingreso (Fase 3, renovación por vida útil).
    'inventario_tiendas': ['Item', 'C.O. bodega', 'Fecha última entrada'],
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
    'inventario_tiendas': 0,
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
    'inventario_tiendas': 'Inventario Tiendas (Siesa)',
}


# Nombres anteriores de columnas que se siguen aceptando (archivos viejos no deben romper la
# corrida): {archivo: {nombre viejo: nombre vigente}}. Portafolio: `VIDA UTIL` pasó a
# `VIDA UTIL EXHIBICION` (nov-2026) al agregar `DIAS CONSERVACION CEDI` y `TOTAL VIDA UTIL`,
# que son OPCIONALES (insumo de un análisis futuro de compras; no bloquean la carga).
_ALIAS_COLUMNAS = {
    'portafolio': {'VIDA UTIL': 'VIDA UTIL EXHIBICION'},
}


def _strip_columns(df, key=None):
    """Normalizar espacios en encabezados (algunos vienen con espacios al final) y
    renombrar los nombres antiguos de columna (`_ALIAS_COLUMNAS`) si el vigente no está."""
    df.columns = [str(c).strip() if isinstance(c, str) else c for c in df.columns]
    for viejo, nuevo in _ALIAS_COLUMNAS.get(key, {}).items():
        if viejo in df.columns and nuevo not in df.columns:
            df = df.rename(columns={viejo: nuevo})
    return df


def _has_required(df, key):
    return all(c in df.columns for c in REQUIRED_COLUMNS[key])


def _read_one(file_obj, key):
    """Lee un Excel a DataFrame. Intenta la hoja preferida; si no existe o no
    trae las columnas requeridas, usa la primera hoja del libro que sí las
    tenga (robustez ante hojas auxiliares agregadas al inicio, ej. una tabla
    dinámica antes de los datos). Si ninguna las tiene, retorna la hoja
    preferida (o la 1ª) para que la validación reporte qué falta."""
    if hasattr(file_obj, 'seek'):
        file_obj.seek(0)
    xls = pd.ExcelFile(file_obj)
    sheet = SHEET_BY_KEY[key]
    if isinstance(sheet, str) and sheet not in xls.sheet_names:
        sheet = 0
    df = _strip_columns(xls.parse(sheet_name=sheet), key)
    if _has_required(df, key):
        return df

    preferida = xls.sheet_names[sheet] if isinstance(sheet, int) else sheet
    for nombre in xls.sheet_names:
        if nombre == preferida:
            continue
        candidato = _strip_columns(xls.parse(sheet_name=nombre), key)
        if _has_required(candidato, key):
            return candidato
    return df


def _validate_columns(df, key):
    missing = [c for c in REQUIRED_COLUMNS[key] if c not in df.columns]
    if missing:
        raise ValueError(
            f"Al archivo «{LABELS[key]}» le faltan columnas requeridas: "
            f"{', '.join(missing)}"
        )


def load_files(stock, celes, portafolio, tiendas, tiendas_item, espejo, tiendas_excluidas=None,
               inventario_tiendas=None):
    """Carga y valida los archivos de entrada.

    Acepta objetos UploadedFile de Streamlit o rutas/buffers compatibles con
    pandas.read_excel. Retorna un dict con DataFrames o lanza ValueError con
    un mensaje claro si falta una columna.

    espejo es obligatorio: define los grupos de productos espejo (mismo
    producto físico, distinto código de ítem) usados por el preprocessor
    para calcular necesidad conjunta por grupo.

    inventario_tiendas es OBLIGATORIO (extracto de Siesa con la fecha del último
    ingreso de cada ítem a cada tienda, usado por la Fase 3 de renovación por vida
    útil). Se recibe como keyword para no alterar el orden posicional de los demás.

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
        'inventario_tiendas': inventario_tiendas,
    }
    dataframes = {}
    for key, file_obj in inputs.items():
        if file_obj is None:
            raise ValueError(f"Falta cargar el archivo «{LABELS[key]}».")
        df = _read_one(file_obj, key)
        _validate_columns(df, key)
        dataframes[key] = df

    if tiendas_excluidas is not None:
        df = _read_one(tiendas_excluidas, 'excluidas')
        _validate_columns(df, 'excluidas')
        dataframes['excluidas'] = df

    return dataframes

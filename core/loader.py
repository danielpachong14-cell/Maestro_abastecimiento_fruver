"""Carga y valida las columnas requeridas de cada Excel de entrada. Todo en memoria: no escribe a disco.
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
    # Módulo de compras (opcional): export de Siesa de órdenes de compra abiertas (hoja «OC en tránsito»).
    # Pendiente = Cant. ordenada − Cant. entrada; ver compras_preprocessor._clasificar_oc.
    'compras_transito': ['Item', 'Cant. ordenada', 'Fecha de Entrega'],
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
    'compras_transito': 0,
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
    'compras_transito': 'Compras en tránsito (OC)',
    'parametros_compra': 'Parámetros de compra',
}


# Nombres anteriores de columnas que se siguen aceptando (archivos viejos no deben romper la
# corrida): {archivo: {nombre viejo: nombre vigente}}. Portafolio: `VIDA UTIL` pasó a
# `VIDA UTIL EXHIBICION` (nov-2026) al agregar `DIAS CONSERVACION CEDI` y `TOTAL VIDA UTIL`,
# que son OPCIONALES (insumo de un análisis futuro de compras; no bloquean la carga).
_ALIAS_COLUMNAS = {
    'portafolio': {'VIDA UTIL': 'VIDA UTIL EXHIBICION'},
    # Plantilla provisional de OC (nov-2026) → columnas del export de Siesa.
    'compras_transito': {'Cantidad (cajas)': 'Cant. ordenada', 'Fecha esperada CEDI': 'Fecha de Entrega'},
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


def load_purchase_files(parametros=None, compras_transito=None):
    """Carga los archivos OPCIONALES del módulo de compras.

    parametros: `Parametros_Compra.xlsx`; se leen TODAS sus hojas (General, Compra por día,
    Calendario, Por Ítem) → dict {hoja: DataFrame} bajo la clave 'parametros_compra'.
    compras_transito: `Compras_Transito.xlsx` (OC abiertas); valida columnas → DataFrame bajo
    'compras_transito'.

    Un archivo en None no se incluye en el resultado (el módulo de compras usa sus valores por
    defecto y alerta). Lanza ValueError si falta una columna requerida de las OC.
    """
    resultado = {}
    if parametros is not None:
        if hasattr(parametros, 'seek'):
            parametros.seek(0)
        hojas = pd.read_excel(parametros, sheet_name=None)
        resultado['parametros_compra'] = {nombre: _strip_columns(df) for nombre, df in hojas.items()}
    if compras_transito is not None:
        df = _read_one(compras_transito, 'compras_transito')
        _validate_columns(df, 'compras_transito')
        resultado['compras_transito'] = df
    return resultado


def load_inputs(stock, celes, inventario_tiendas, maestros, compras_transito=None):
    """Formato vigente (nov-2026): 3 archivos diarios + `Maestros Fruver.xlsx` (+ OC opcional).

    Retorna (dfs, extra, alertas):
    - dfs: mismo dict que `load_files` (stock, celes, inventario_tiendas, portafolio, tiendas,
      tiendas_item, espejo y, si la hoja tiene filas, excluidas) — la distribución no cambia.
    - extra: {'parametros_compra': hojas de parámetros, 'compras_transito': OC (si se pasó)}.
    - alertas: avisos de calidad del libro de maestros (van a la compra).
    Lanza ValueError con un mensaje claro si falta un archivo, una hoja o una columna.
    """
    from .maestros import ARCHIVO, HOJA_DE_CLAVE, leer_maestros

    dfs = {}
    for key, file_obj in (('stock', stock), ('celes', celes), ('inventario_tiendas', inventario_tiendas)):
        if file_obj is None:
            raise ValueError(f"Falta cargar el archivo «{LABELS[key]}».")
        df = _read_one(file_obj, key)
        _validate_columns(df, key)
        dfs[key] = df
    if maestros is None:
        raise ValueError(f"Falta cargar el archivo «{ARCHIVO}».")
    maestros_dfs, hojas_parametros, alertas = leer_maestros(maestros)
    for key, df in maestros_dfs.items():
        df = _strip_columns(df, key)
        faltan = [c for c in REQUIRED_COLUMNS[key] if c not in df.columns]
        if faltan:
            raise ValueError(f"«{ARCHIVO}», hoja «{HOJA_DE_CLAVE[key]}»: faltan columnas requeridas: "
                             f"{', '.join(faltan)}")
        dfs[key] = df
    extra = {'parametros_compra': hojas_parametros}
    extra.update(load_purchase_files(compras_transito=compras_transito))
    return dfs, extra, alertas

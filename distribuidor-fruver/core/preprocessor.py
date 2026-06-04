"""Limpieza, normalización de claves de cruce y merge en un único DataFrame
tienda × ítem.

Aquí viven los ajustes necesarios para que las claves reales de los archivos
crucen correctamente (validados contra los datos de producción):

  1. Códigos de tienda: Celes usa «BOS03» y la Base de Tiendas «S03». Se quita
     el prefijo «BO» del código de Celes para obtener el COD SIESA.
  2. Códigos de ítem: Stock trae «0000155» (texto con ceros a la izquierda) y el
     resto de archivos usa 155 (entero). Se normaliza todo a entero.
  3. Stock incluye una fila totalizadora «Gran total» que debe eliminarse.

Además, para el cálculo de necesidad/prioridad se conserva el inventario en
tránsito («(-) inventario de traslado en proceso»), de modo que el inventario
efectivo de cada tienda sea Existencias + Tránsito.
"""

import math

import numpy as np
import pandas as pd


def normalize_item(value):
    """Normaliza un código de ítem a entero. '0000155' -> 155, 155.0 -> 155.

    Retorna None si no es convertible (se filtra aguas arriba)."""
    if pd.isna(value):
        return None
    s = str(value).strip()
    if s == '' or not any(ch.isdigit() for ch in s):
        return None
    try:
        return int(float(s)) if ('.' in s or 'e' in s.lower()) else int(s.lstrip('0') or '0')
    except (ValueError, TypeError):
        return None


def normalize_store(value):
    """Quita el prefijo 'BO' del Código de Bodega de Celes para obtener el
    COD SIESA. 'BOS03' -> 'S03'. Códigos sin prefijo se devuelven igual."""
    if pd.isna(value):
        return None
    s = str(value).strip()
    return s[2:] if s.upper().startswith('BO') else s


def _is_active(series):
    """True para filas cuyo estado contiene 'ACTIVO' (cubre '001 - ACTIVO')."""
    return series.astype(str).str.contains('ACTIVO', case=False, na=False)


def build_distribution_df(dfs):
    """Construye el DataFrame unificado tienda × ítem elegible.

    Cada fila es una combinación tienda-ítem elegible con las métricas
    necesarias para el algoritmo de distribución.
    """
    # 1. STOCK: quitar totalizador, filtrar activo y con existencia.
    # Cant. disponible ya está en cajas completas — no se divide por Factor U.M.
    # U.M. (ej. CJ20) indica cuántas unidades tiene la caja, pero la distribución
    # trabaja en cajas, no en unidades.
    stock = dfs['stock'].copy()
    stock = stock[stock['Item'].astype(str).str.strip().str.lower() != 'gran total']
    stock['Cant. disponible'] = pd.to_numeric(stock['Cant. disponible'], errors='coerce').fillna(0)
    stock = stock[stock['Cant. disponible'] > 0].copy()
    stock['cajas_disponibles'] = stock['Cant. disponible'].apply(math.floor)
    stock = stock[stock['cajas_disponibles'] > 0]
    stock['item_key'] = stock['Item'].apply(normalize_item)
    stock = stock.dropna(subset=['item_key'])
    stock['item_key'] = stock['item_key'].astype(int)

    # 2. PORTAFOLIO: solo activos (sirve de referencia; la elegibilidad real
    #    sale de tiendas_item).
    portafolio = dfs['portafolio'].copy()
    portafolio = portafolio[_is_active(portafolio['ESTADO'])]
    portafolio['item_key'] = portafolio['ITEM'].apply(normalize_item)

    # 3. TIENDAS × ÍTEM: solo combinaciones activas. Fuente de verdad de elegibilidad.
    tiendas_item = dfs['tiendas_item'].copy()
    tiendas_item = tiendas_item[_is_active(tiendas_item['DB_Portafolio_Fruver.ESTADO'])].copy()
    tiendas_item['item_key'] = tiendas_item['DB_Portafolio_Fruver.ITEM'].apply(normalize_item)
    tiendas_item = tiendas_item.dropna(subset=['item_key'])
    tiendas_item['item_key'] = tiendas_item['item_key'].astype(int)
    tiendas_item['COD SIESA'] = tiendas_item['COD SIESA'].astype(str).str.strip()

    # 4. CELES: consumos e inventarios. Se distribuye a TODAS las tiendas presentes,
    #    sin filtrar por el flag de distribución automática.
    celes = dfs['celes'].copy()
    celes['cod_siesa'] = celes['Código de Bodega'].apply(normalize_store)
    celes['item_key'] = celes['Código de Producto'].apply(normalize_item)
    celes = celes.dropna(subset=['item_key', 'cod_siesa'])
    celes['item_key'] = celes['item_key'].astype(int)
    # Si una tienda-ítem aparece duplicada en Celes, conservar una sola fila.
    celes = celes.drop_duplicates(['cod_siesa', 'item_key'], keep='first')

    if 'Vida Útil' in celes.columns:
        celes['vida_util'] = pd.to_numeric(celes['Vida Útil'], errors='coerce').fillna(0)
    else:
        celes['vida_util'] = 0.0

    # 5. MERGE 1: elegibilidad (tiendas_item) × stock disponible.
    merged = tiendas_item.merge(
        stock[['item_key', 'Desc. item', 'cajas_disponibles', 'U.M.']],
        on='item_key',
        how='inner',
    )

    # 6. MERGE 2: con Celes para consumo, días e inventarios (existencias + tránsito).
    celes_cols = [
        'cod_siesa', 'item_key', 'Nombre de Bodega',
        'Consumo Diario (Unidades de Distribución)',
        '(=) Días de Inventario Actuales',
        'Inventario Disponible en esta Tienda (Unidades de Distibución)',
        '(-) inventario de traslado en proceso',
        'UM', 'vida_util',
    ]
    merged = merged.merge(
        celes[celes_cols],
        left_on=['COD SIESA', 'item_key'],
        right_on=['cod_siesa', 'item_key'],
        how='left',
        indicator='_celes_match',
    )

    # 7. Renombrar a nombres cortos para el algoritmo.
    merged = merged.rename(columns={
        'item_key': 'item_code',
        'COD SIESA': 'store_code',
        'Nombre de Bodega': 'store_name',
        'cajas_disponibles': 'cajas_disponibles_cedi',
        'Consumo Diario (Unidades de Distribución)': 'consumo_diario',
        '(=) Días de Inventario Actuales': 'dias_inventario_actuales',
        'Inventario Disponible en esta Tienda (Unidades de Distibución)': 'inventario_tienda',
        '(-) inventario de traslado en proceso': 'inventario_transito',
    })

    # Descripción del ítem: preferir la del portafolio, caer a la del stock.
    if 'DB_Portafolio_Fruver.DESCRIPCION' in merged.columns:
        merged['item_desc'] = merged['DB_Portafolio_Fruver.DESCRIPCION'].fillna(merged['Desc. item'])
    else:
        merged['item_desc'] = merged['Desc. item']

    # UM para el output: la de Celes; si falta, la del stock.
    merged['um'] = merged['UM'].fillna(merged['U.M.']) if 'UM' in merged.columns else merged['U.M.']

    # 8. Rellenar NaN en columnas numéricas usadas por el algoritmo.
    # Todas las magnitudes de Celes están en Unidades de Distribución = cajas.
    for col in ['consumo_diario', 'dias_inventario_actuales', 'inventario_tienda', 'inventario_transito', 'vida_util']:
        merged[col] = pd.to_numeric(merged[col], errors='coerce').fillna(0)

    # 9. Inventario efectivo (cajas existentes + cajas en tránsito) y días proyectados
    # antes de esta distribución.
    merged['inventario_efectivo'] = merged['inventario_tienda'] + merged['inventario_transito']
    merged['dias_proyectados'] = np.where(
        merged['consumo_diario'] > 0,
        merged['inventario_efectivo'] / merged['consumo_diario'].replace(0, np.nan),
        np.inf,
    )

    # 10. Agregar zona desde DB_Tiendas.
    tiendas = dfs['tiendas'][['COD SIESA', 'ZONA']].copy()
    tiendas['COD SIESA'] = tiendas['COD SIESA'].astype(str).str.strip()
    merged = merged.merge(tiendas, left_on='store_code', right_on='COD SIESA', how='left')
    merged['zona'] = merged['ZONA'].fillna('').astype(str).str.strip()
    merged = merged.drop(columns=['COD SIESA_y'] if 'COD SIESA_y' in merged.columns else [], errors='ignore')
    merged = merged.drop(columns=['ZONA'], errors='ignore')

    # 11. Excluir tiendas marcadas como "sin pedido".
    if 'excluidas' in dfs:
        excl = dfs['excluidas'].copy()
        excl.columns = [str(c).strip() for c in excl.columns]
        excl_codes = set(excl['Centro Operacional de la Bodega'].astype(str).str.strip())
        merged = merged[~merged['store_code'].isin(excl_codes)]

    cols_out = [
        'item_code', 'item_desc', 'store_code', 'store_name', 'zona',
        'consumo_diario', 'dias_inventario_actuales', 'inventario_tienda',
        'inventario_transito', 'inventario_efectivo', 'dias_proyectados',
        'cajas_disponibles_cedi', 'um', 'vida_util',
    ]
    return merged[cols_out].reset_index(drop=True)

"""Limpieza, normalización de claves (BO-prefijo de tienda, ítem texto→entero, estado exacto) y merge en
un DataFrame tienda × ítem. Marca `bloqueado_por_max` y `sin_match_celes` (= `excluida_del_reparto`).
`build_distribution_df` retorna `(df_merged, alertas)`. Reglas en CLAUDE.md.
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


def _normalize_item_series(series):
    """Aplica `normalize_item` a una columna. Si ya es entera (Portafolio,
    Tiendas×Ítem, Celes y Espejo; solo Stock trae texto) la devuelve tal cual,
    evitando el `.apply()` fila por fila."""
    if pd.api.types.is_integer_dtype(series):
        return series
    return series.apply(normalize_item)


def normalize_store(value):
    """Quita el prefijo 'BO' del Código de Bodega de Celes para obtener el
    COD SIESA. 'BOS03' -> 'S03'. Códigos sin prefijo se devuelven igual."""
    if pd.isna(value):
        return None
    s = str(value).strip()
    return s[2:] if s.upper().startswith('BO') else s


def _is_active(series):
    """True para filas cuyo estado (tras el código numérico, ej. '001 - ACTIVO')
    es EXACTAMENTE 'ACTIVO'.

    `str.contains('ACTIVO')` no sirve: 'INACTIVO' también lo contiene (bug real:
    afectaba 955 de 4878 filas de Tiendas × Ítem y 23 de 84 de Portafolio).
    Se toma el último segmento tras separar por '-'."""
    ultimo_segmento = series.astype(str).str.split('-').str[-1].str.strip().str.upper()
    return ultimo_segmento == 'ACTIVO'


def _alertar_filas_descartadas(n_antes, n_despues, archivo_label, alertas):
    """Registra cuántas filas se perdieron por código de ítem no normalizable."""
    n_drop = n_antes - n_despues
    if n_drop > 0:
        alertas.append({
            'tipo': 'ADVERTENCIA',
            'item': '-',
            'mensaje': f'{archivo_label}: se descartaron {n_drop} fila(s) con código de '
                       'ítem no normalizable (no numérico) al cruzar claves.',
            'filas_descartadas': int(n_drop),
            'archivo': archivo_label,
        })


# Factor U.M. hasta el que el redondeo de Siesa a 2 decimales se deshace sin ambigüedad:
# el error es ≤ 0,005 caja = 0,005 × factor unidades, que debe quedar ≤ 0,5 unidad.
FACTOR_MAX_UNIDADES_EXACTAS = 100


def a_fecha_excel(serie):
    """Fechas que llegan como fecha, texto o número de serie de Excel (46352 = 2026-11-26)."""
    serie = pd.Series(serie)
    if pd.api.types.is_datetime64_any_dtype(serie):
        return serie
    num = pd.to_numeric(serie, errors='coerce')
    fechas = pd.to_datetime(num, unit='D', origin='1899-12-30', errors='coerce')
    resto = num.isna() & serie.notna()
    if resto.any():
        fechas[resto] = pd.to_datetime(serie[resto].astype(str), errors='coerce')
    return fechas


def cajas_exactas(cajas, factor):
    """Siesa entrega `Cant. disponible` en cajas con 2 decimales (105 unidades / 36 = 2,92). Las
    existencias son unidades enteras, así que se recupera el valor exacto: round(cajas × factor) / factor
    (2,92 → 105 → 2,916667). Con factor vacío, ≤ 0 o > FACTOR_MAX_UNIDADES_EXACTAS queda como viene."""
    cajas = pd.to_numeric(cajas, errors='coerce')
    factor = pd.to_numeric(factor, errors='coerce')
    ok = (factor > 0) & (factor <= FACTOR_MAX_UNIDADES_EXACTAS) & cajas.notna()
    exactas = (cajas * factor).round() / factor.where(ok)
    return exactas.where(ok, cajas)


def transito_pendiente(existencias_siesa, existencias_celes, transito_celes, fecha_siesa, fecha_celes):
    """Parte del tránsito de Celes que todavía no entró a la tienda según Siesa.

    Celes es una foto anterior a la de Siesa (p. ej. 26-nov contra 27-nov): un traslado que Celes muestra
    «en proceso» puede ya estar sumado en `Cant. disponible` de Siesa, y contarlo en ambos lo duplica.
    Se da por recibido cuando Siesa tiene un ingreso POSTERIOR al último que conoce Celes, y la cantidad
    recibida es lo que subieron las existencias entre las dos fotos, con el tránsito como tope:
        recibido  = min(tránsito, max(0, existencias_siesa − existencias_celes))
        pendiente = tránsito − recibido
    Sin ingreso nuevo, sin dato de Siesa o sin fecha de Celes, el tránsito queda completo.
    Retorna (pendiente, recibido)."""
    transito = pd.to_numeric(transito_celes, errors='coerce').fillna(0)
    ingreso_nuevo = fecha_siesa.notna() & fecha_celes.notna() & (fecha_siesa > fecha_celes)
    ganancia = (existencias_siesa - existencias_celes.fillna(0)).clip(lower=0)
    recibido = np.minimum(ganancia, transito).where(ingreso_nuevo & existencias_siesa.notna(), 0.0)
    recibido = recibido.round(6)
    return (transito - recibido).clip(lower=0), recibido


def resumen_inventario_tiendas(inv_t, alertas):
    """Extracto de Siesa (Inventario Tiendas, una fila por ítem × bodega) → por (store_code, item_key):
    `existencias_siesa` = suma de `Cant. disponible` de las sub-bodegas del C.O. (en cajas exactas, ver
    `cajas_exactas`; ≥ 0; NaN si el archivo no trae la columna) y `fecha_ultimo_ingreso` = la más reciente.
    Retorna (resumen, fecha_corte): la fecha de corte es el ingreso más reciente del archivo."""
    inv_t = inv_t.copy()
    inv_t['store_code'] = inv_t['C.O. bodega'].astype(str).str.strip()
    inv_t['item_key'] = _normalize_item_series(inv_t['Item'])
    inv_t['_fecha'] = pd.to_datetime(inv_t['Fecha última entrada'], errors='coerce')
    _n_antes = len(inv_t)
    inv_t = inv_t.dropna(subset=['item_key'])
    _alertar_filas_descartadas(_n_antes, len(inv_t), 'Inventario Tiendas (Siesa)', alertas)
    inv_t['item_key'] = inv_t['item_key'].astype(int)
    if 'Cant. disponible' in inv_t.columns:
        inv_t['_cant'] = pd.to_numeric(inv_t['Cant. disponible'], errors='coerce').fillna(0)
        if 'Factor U.M.' in inv_t.columns:
            inv_t['_cant'] = cajas_exactas(inv_t['_cant'], inv_t['Factor U.M.'])
    else:
        inv_t['_cant'] = np.nan
    resumen = (
        inv_t.groupby(['store_code', 'item_key'])
        .agg(existencias_siesa=('_cant', lambda s: s.sum(min_count=1)), fecha_ultimo_ingreso=('_fecha', 'max'))
        .reset_index()
    )
    negativos = resumen['existencias_siesa'] < 0
    if negativos.any():
        alertas.append({
            'tipo': 'ADVERTENCIA', 'item': '-',
            'mensaje': f'Inventario Tiendas (Siesa): {int(negativos.sum())} par(es) tienda-ítem con '
                       '«Cant. disponible» negativa; se tomaron como 0 existencias.',
            'pares_negativos': int(negativos.sum()),
        })
        resumen['existencias_siesa'] = resumen['existencias_siesa'].clip(lower=0)
    return resumen, inv_t['_fecha'].max()


def build_distribution_df(dfs):
    """Construye el DataFrame unificado: una fila por combinación tienda-ítem
    elegible, con las métricas del algoritmo.

    Retorna (df_merged, alertas): alertas es una lista de dicts
    {tipo, item, mensaje, ...} (ver docstring del módulo).
    """
    alertas = []

    # 1. STOCK: quitar totalizador y quedarse con existencia. Cant. disponible ya
    # está en cajas completas (no se divide por Factor U.M.). ESTADO DEL PRODUCTO
    # NO filtra (ver alerta informativa más abajo).
    stock = dfs['stock'].copy()
    es_totalizador = stock['Item'].astype(str).str.strip().str.lower().str.contains('total', na=False)
    stock = stock[~es_totalizador]
    stock['Cant. disponible'] = pd.to_numeric(stock['Cant. disponible'], errors='coerce').fillna(0)
    stock = stock[stock['Cant. disponible'] > 0].copy()
    stock['cajas_disponibles'] = stock['Cant. disponible'].apply(math.floor)
    # Solo se reparten cajas completas; la parte decimal descartada se reporta.
    fracc = stock[stock['Cant. disponible'] != stock['cajas_disponibles']]
    if len(fracc) > 0:
        descartado = float((fracc['Cant. disponible'] - fracc['cajas_disponibles']).sum())
        alertas.append({
            'tipo': 'ADVERTENCIA', 'item': '-',
            'mensaje': f"Stock CEDI (Siesa): {len(fracc)} fila(s) con cantidad fraccionaria; "
                       f"se redondearon hacia abajo a cajas completas y se descartaron "
                       f"{descartado:.2f} caja(s) en total.",
            'filas_fraccionarias': int(len(fracc)),
            'cajas_descartadas': round(descartado, 2),
        })
    stock = stock[stock['cajas_disponibles'] > 0]
    stock['item_key'] = _normalize_item_series(stock['Item'])
    _n_antes = len(stock)
    stock = stock.dropna(subset=['item_key'])
    _alertar_filas_descartadas(_n_antes, len(stock), 'Stock CEDI (Siesa)', alertas)
    stock['item_key'] = stock['item_key'].astype(int)
    # Ítem repetido en Stock: se SUMAN sus cajas (conservar la primera perdía
    # cajas sin avisar) y se alerta; las demás columnas salen de la primera fila.
    if stock['item_key'].duplicated().any():
        sumas = stock.groupby('item_key')['cajas_disponibles'].sum()
        repetidos = stock.loc[stock['item_key'].duplicated(keep=False), 'item_key']
        for item_key, n_filas in repetidos.value_counts().sort_index().items():
            alertas.append({
                'tipo': 'ADVERTENCIA', 'item': int(item_key),
                'mensaje': f'Ítem {int(item_key)} aparece {int(n_filas)} veces en Stock CEDI; '
                           f'se sumaron sus cajas ({int(sumas[item_key])} en total). '
                           'Revisar el archivo de origen.',
                'cajas_en_cedi': int(sumas[item_key]),
                'filas_repetidas': int(n_filas),
            })
        stock = stock.drop_duplicates(['item_key'], keep='first').copy()
        stock['cajas_disponibles'] = stock['item_key'].map(sumas).astype(int)

    # Alerta informativa: ítems con existencia cuyo ESTADO DEL PRODUCTO no es
    # exactamente ACTIVO se distribuyen igual, pero se reportan.
    stock_no_activo = stock[~_is_active(stock['ESTADO DEL PRODUCTO'])]
    for _, row in stock_no_activo.iterrows():
        item_key = int(row['item_key'])
        cajas = int(row['cajas_disponibles'])
        estado = str(row['ESTADO DEL PRODUCTO']).strip()
        alertas.append({
            'tipo': 'ADVERTENCIA', 'item': item_key,
            'mensaje': f"Ítem {item_key} tiene {cajas} caja(s) en CEDI con ESTADO DEL "
                       f"PRODUCTO = '{estado}' (no ACTIVO) — se "
                       "distribuye igual; revisar si corresponde.",
            'cajas_en_cedi': cajas,
            'estado_producto': estado,
        })

    # 2. PORTAFOLIO: catálogo completo (activos e inactivos) solo para validar Stock:
    # detecta ítems sin catalogar o descontinuados.
    portafolio = dfs['portafolio'].copy()
    portafolio['item_key'] = _normalize_item_series(portafolio['ITEM'])
    _n_antes = len(portafolio)
    portafolio = portafolio.dropna(subset=['item_key'])
    _alertar_filas_descartadas(_n_antes, len(portafolio), 'Portafolio Fruver', alertas)
    portafolio['item_key'] = portafolio['item_key'].astype(int)

    items_catalogados = set(portafolio['item_key'])
    items_descontinuados = set(portafolio.loc[~_is_active(portafolio['ESTADO']), 'item_key'])
    stock_item_keys = set(stock['item_key'])

    for item_key in sorted(stock_item_keys - items_catalogados):
        cajas = int(stock.loc[stock['item_key'] == item_key, 'cajas_disponibles'].sum())
        alertas.append({
            'tipo': 'ADVERTENCIA', 'item': item_key,
            'mensaje': f'Ítem {item_key} tiene {cajas} caja(s) en CEDI pero no está '
                       'catalogado en Portafolio Fruver — revisar si es un ítem nuevo.',
            'cajas_en_cedi': cajas,
        })
    for item_key in sorted(stock_item_keys & items_descontinuados):
        cajas = int(stock.loc[stock['item_key'] == item_key, 'cajas_disponibles'].sum())
        alertas.append({
            'tipo': 'ADVERTENCIA', 'item': item_key,
            'mensaje': f'Ítem {item_key} está descontinuado en Portafolio Fruver pero '
                       f'tiene {cajas} caja(s) en CEDI — revisar si el inventario debe '
                       'darse de baja.',
            'cajas_en_cedi': cajas,
        })

    # 3. TIENDAS × ÍTEM: solo combinaciones activas. Fuente de verdad de elegibilidad.
    tiendas_item = dfs['tiendas_item'].copy()
    tiendas_item = tiendas_item[_is_active(tiendas_item['ESTADO'])].copy()
    tiendas_item['item_key'] = _normalize_item_series(tiendas_item['ITEM'])
    _n_antes = len(tiendas_item)
    tiendas_item = tiendas_item.dropna(subset=['item_key'])
    _alertar_filas_descartadas(_n_antes, len(tiendas_item), 'Tiendas × Ítem', alertas)
    tiendas_item['item_key'] = tiendas_item['item_key'].astype(int)
    tiendas_item['COD SIESA'] = tiendas_item['COD SIESA'].astype(str).str.strip()
    # MAX: tope de existencias de esa tienda×ítem; ya llega resuelto como número
    # (XLOOKUP dentro del Excel). 0 / vacío = sin tope definido.
    tiendas_item['max_existencias'] = (
        pd.to_numeric(tiendas_item['MAX'], errors='coerce').fillna(0).clip(lower=0)
    )
    # Pares tienda×ítem repetidos: se conserva una fila y prevalece el MAX más
    # restrictivo (menor MAX > 0) para no sobre-abastecer.
    _clave = ['COD SIESA', 'item_key']
    _rep = tiendas_item[tiendas_item.duplicated(_clave, keep=False)]
    if len(_rep) > 0:
        _min_pos = (
            _rep['max_existencias'].where(_rep['max_existencias'] > 0)
            .groupby([_rep['COD SIESA'], _rep['item_key']]).min()
            .rename('_max_restrictivo').reset_index()
        )
        _n_dif = int(_rep.groupby(_clave)['max_existencias'].nunique().gt(1).sum())
        tiendas_item = tiendas_item.drop_duplicates(_clave, keep='first').merge(
            _min_pos, on=_clave, how='left'
        )
        tiendas_item['max_existencias'] = (
            tiendas_item['_max_restrictivo'].fillna(tiendas_item['max_existencias'])
        )
        tiendas_item = tiendas_item.drop(columns=['_max_restrictivo'])
        alertas.append({
            'tipo': 'ADVERTENCIA', 'item': '-',
            'mensaje': f'Tiendas × Ítem: {len(_min_pos)} par(es) tienda-ítem repetido(s) '
                       f'({_n_dif} con MAX distinto); se conservó una fila por par y se '
                       'aplicó el MAX más restrictivo. Revisar el archivo de origen.',
            'pares_repetidos': int(len(_min_pos)),
            'pares_con_max_distinto': _n_dif,
        })

    # 4. CELES: consumos e inventarios, de TODAS las tiendas presentes (sin filtrar
    #    por el flag de distribución automática).
    celes = dfs['celes'].copy()
    celes['cod_siesa'] = celes['Código de Bodega'].apply(normalize_store)
    celes['item_key'] = _normalize_item_series(celes['Código de Producto'])
    _n_antes = len(celes)
    celes = celes.dropna(subset=['item_key', 'cod_siesa'])
    _alertar_filas_descartadas(_n_antes, len(celes), 'Celes (consumos)', alertas)
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

    # 6. MERGE 2: con Celes (consumo, días, tránsito; sus existencias quedan solo de referencia). `indicator`
    # marca las combinaciones sin match (`sin_match_celes`): se rellenan con
    # consumo=0/inventario=0 y sin esa marca parecerían AGOTADAS.
    for _opcional in ('Inventario Disponible en esta Tienda (Unidades de Distibución)', 'Última Fecha de Ingreso'):
        if _opcional not in celes.columns:
            celes[_opcional] = np.nan
    celes['fecha_ingreso_celes'] = a_fecha_excel(celes['Última Fecha de Ingreso'])
    celes_cols = [
        'cod_siesa', 'item_key', 'Nombre de Bodega',
        'Consumo Diario (Unidades de Distribución)',
        '(=) Días de Inventario Actuales',
        'Inventario Disponible en esta Tienda (Unidades de Distibución)',
        '(-) inventario de traslado en proceso',
        '(=) Inventario Total',
        'UM', 'vida_util', 'fecha_ingreso_celes',
    ]
    merged = merged.merge(
        celes[celes_cols],
        left_on=['COD SIESA', 'item_key'],
        right_on=['cod_siesa', 'item_key'],
        how='left',
        indicator='_celes_match',
    )
    merged['sin_match_celes'] = merged['_celes_match'] == 'left_only'
    merged = merged.drop(columns=['_celes_match'])

    # 6.b CAUSA del sin-match para la hoja "Novedades": tienda ausente, ítem
    # ausente o solo ese par; cada una pide una acción correctiva distinta.
    tiendas_en_celes = set(celes['cod_siesa'])
    items_en_celes = set(celes['item_key'])
    merged['causa_sin_match'] = np.select(
        [
            ~merged['sin_match_celes'],
            ~merged['COD SIESA'].isin(tiendas_en_celes),
            ~merged['item_key'].isin(items_en_celes),
        ],
        ['', 'Tienda ausente de Celes', 'Ítem ausente de todo Celes'],
        default='Par tienda-ítem faltante',
    )

    # 7. Renombrar a nombres cortos para el algoritmo.
    merged = merged.rename(columns={
        'item_key': 'item_code',
        'COD SIESA': 'store_code',
        'Nombre de Bodega': 'store_name',
        'cajas_disponibles': 'cajas_disponibles_cedi',
        'Consumo Diario (Unidades de Distribución)': 'consumo_diario',
        '(=) Días de Inventario Actuales': 'dias_inventario_actuales',
        'Inventario Disponible en esta Tienda (Unidades de Distibución)': 'inventario_tienda_celes',
        '(-) inventario de traslado en proceso': 'inventario_transito',
        '(=) Inventario Total': 'inventario_total',
    })

    # Descripción del ítem: preferir la de Tiendas × Ítem (DB_Portafolio_Fruver.DESCRIPCION,
    # que viaja en ese archivo), caer a la del stock si falta.
    if 'DESCRIPCION' in merged.columns:
        merged['item_desc'] = merged['DESCRIPCION'].fillna(merged['Desc. item'])
    else:
        merged['item_desc'] = merged['Desc. item']

    # UM para el output: la de Celes; si falta, la del stock.
    merged['um'] = merged['UM'].fillna(merged['U.M.']) if 'UM' in merged.columns else merged['U.M.']

    # 8. Rellenar NaN y descartar negativos (no deben generar pesos/necesidades
    # negativas aguas abajo). Las magnitudes de Celes ya están en cajas.
    for col in ['consumo_diario', 'dias_inventario_actuales', 'inventario_tienda_celes', 'inventario_transito', 'inventario_total', 'vida_util']:
        merged[col] = pd.to_numeric(merged[col], errors='coerce').fillna(0).clip(lower=0)
    merged['inventario_transito_celes'] = merged['inventario_transito']

    # 8.b EXISTENCIAS DE LA TIENDA (27-nov-2026): `Cant. disponible` de Inventario Tiendas (Siesa),
    # sumando sus sub-bodegas y en unidades exactas; el TRÁNSITO sale de Celes, menos lo que Siesa ya
    # muestra recibido (`transito_pendiente`). Par sin fila en Siesa = 0 existencias (con alerta). Del
    # mismo extracto sale la fecha de último ingreso (paso 12.b).
    # Sin el archivo o sin la columna (solo pasa fuera de `loader`) se usan las de Celes, con alerta.
    inv_t = dfs.get('inventario_tiendas')
    if inv_t is not None:
        resumen_inv, _fecha_corte = resumen_inventario_tiendas(inv_t, alertas)
        merged = merged.merge(
            resumen_inv, left_on=['store_code', 'item_code'],
            right_on=['store_code', 'item_key'], how='left',
        ).drop(columns=['item_key'])
    else:
        _fecha_corte = pd.NaT
        merged['existencias_siesa'] = np.nan
        merged['fecha_ultimo_ingreso'] = pd.NaT
    _existencias_de_siesa = inv_t is not None and 'Cant. disponible' in inv_t.columns
    if _existencias_de_siesa:
        merged['_sin_fila_siesa'] = merged['existencias_siesa'].isna()
        merged['inventario_tienda'] = merged['existencias_siesa'].fillna(0)
        # Tránsito de Celes que Siesa ya muestra recibido: se descuenta para no contarlo dos veces.
        merged['inventario_transito'], merged['transito_ya_recibido'] = transito_pendiente(
            merged['existencias_siesa'], merged['inventario_tienda_celes'], merged['inventario_transito_celes'],
            merged['fecha_ultimo_ingreso'], merged['fecha_ingreso_celes'],
        )
    else:
        merged['_sin_fila_siesa'] = False
        merged['inventario_tienda'] = merged['inventario_tienda_celes']
        merged['transito_ya_recibido'] = 0.0
        if inv_t is not None:
            alertas.append({
                'tipo': 'ADVERTENCIA', 'item': '-',
                'mensaje': 'Inventario Tiendas (Siesa) no trae «Cant. disponible»: las existencias de las '
                           'tiendas se tomaron de Celes.',
            })
    merged = merged.drop(columns=['existencias_siesa', 'fecha_ingreso_celes'])

    # 9. Inventario efectivo (existencias + tránsito) y días proyectados.
    # `inventario_total` queda como columna informativa.
    merged['inventario_efectivo'] = merged['inventario_tienda'] + merged['inventario_transito']
    merged['dias_proyectados'] = np.where(
        merged['consumo_diario'] > 0,
        merged['inventario_efectivo'] / merged['consumo_diario'].replace(0, np.nan),
        np.inf,
    )

    # 9.b BLOQUEO POR MAX: si existencias + tránsito ya alcanzan el MAX, la tienda
    # sale del reparto normal (interruptor de elegibilidad, no techo de cantidad).
    # El corte es `>=`: con MAX 6, 6.0 o 6.1 bloquea y 5.9 no. Riesgo conocido: si
    # Celes trae el tránsito inflado o desfasado, la tienda queda bloqueada de más.
    # `max_existencias == 0` = sin tope, no "no enviar".
    merged['bloqueado_por_max'] = (
        (merged['max_existencias'] > 0)
        & (merged['inventario_efectivo'] >= merged['max_existencias'])
    )

    # 9.c EXCLUSIÓN DEL REPARTO NORMAL: tope MAX o sin datos en Celes. Sin datos
    # llegaría como AGOTADA y recibiría 1 caja en Fase 2 solo por faltar el dato;
    # decisión de negocio: no se le envía. El algoritmo la consulta vía
    # `algorithm._bloqueadas` (aplica en Fases 1-4 y nivel 1 de Fase 5); solo el
    # nivel 2 de Fase 5 puede forzarles cajas, con alerta. Se conservan las dos
    # columnas de origen porque la hoja "Novedades" necesita la causa.
    merged['excluida_del_reparto'] = merged['bloqueado_por_max'] | merged['sin_match_celes']

    # 10. Restringir a tiendas activas (DB_Tiendas, cruce inner) y traer zona y
    # nombre. store_name sale SIEMPRE de DB_Tiendas: mezclarlo con el de Celes
    # (ej. SE4: "ISIMO LEON XIII" vs "LEON XIII") partía una tienda en dos filas
    # del Resumen. Se renombra a `_nombre_tienda_maestro` porque Tiendas×Ítem ya
    # trae 'NOMBRE DE LA TIENDA' y pandas sufijaría ambas (_x/_y).
    tiendas = dfs['tiendas'][['COD SIESA', 'NOMBRE DE LA TIENDA', 'ZONA']].copy()
    tiendas = tiendas.rename(columns={'NOMBRE DE LA TIENDA': '_nombre_tienda_maestro'})
    tiendas['COD SIESA'] = tiendas['COD SIESA'].astype(str).str.strip()
    # Tienda repetida en el maestro: un merge 1:N duplicaría sus pedidos.
    _dup_t = tiendas['COD SIESA'].duplicated()
    if _dup_t.any():
        alertas.append({
            'tipo': 'ADVERTENCIA', 'item': '-',
            'mensaje': f"DB_Tiendas: {int(_dup_t.sum())} fila(s) con COD SIESA repetido "
                       f"({', '.join(sorted(tiendas.loc[_dup_t, 'COD SIESA'].unique()))}); "
                       "se conservó la primera de cada una.",
            'filas_repetidas': int(_dup_t.sum()),
        })
        tiendas = tiendas[~_dup_t]
    merged = merged.merge(tiendas, left_on='store_code', right_on='COD SIESA', how='inner')
    merged['zona'] = merged['ZONA'].fillna('').astype(str).str.strip()
    merged['store_name'] = merged['_nombre_tienda_maestro']
    # Las columnas crudas se descartan al final con `cols_out`.

    # 11. Excluir tiendas marcadas como "sin pedido".
    if 'excluidas' in dfs:
        excl = dfs['excluidas'].copy()
        excl.columns = [str(c).strip() for c in excl.columns]
        excl_codes = set(excl['Centro Operacional de la Bodega'].astype(str).str.strip())
        merged = merged[~merged['store_code'].isin(excl_codes)]

    # 12. PRODUCTOS ESPEJO: SKUs distintos del mismo producto físico comparten
    # grupo para que prioridad/necesidad se calculen sobre el conjunto; la
    # distribución sigue siendo por ítem. Un ítem sin espejo es un grupo de
    # tamaño 1 (las fórmulas de grupo dan los valores individuales).
    espejo = dfs['espejo'].copy()
    espejo['item_key'] = _normalize_item_series(espejo['item_id'])
    _n_antes = len(espejo)
    espejo = espejo.dropna(subset=['item_key', 'grupo_id'])
    _alertar_filas_descartadas(_n_antes, len(espejo), 'Productos Espejo', alertas)
    item_to_grupo = dict(zip(espejo['item_key'].astype(int), espejo['grupo_id'].astype(str)))

    # `.astype('object')`: si ningún ítem está en el espejo, `.map()` da float64
    # y asignar strings revienta con TypeError en pandas 3.x.
    merged['grupo_id'] = merged['item_code'].map(item_to_grupo).astype('object')
    sin_grupo = merged['grupo_id'].isna()
    merged.loc[sin_grupo, 'grupo_id'] = 'ITEM_' + merged.loc[sin_grupo, 'item_code'].astype(str)

    merged['consumo_diario_grupo'] = merged.groupby(['store_code', 'grupo_id'])['consumo_diario'].transform('sum')
    merged['inventario_efectivo_grupo'] = merged.groupby(['store_code', 'grupo_id'])['inventario_efectivo'].transform('sum')
    merged['dias_proyectados_grupo'] = np.where(
        merged['consumo_diario_grupo'] > 0,
        merged['inventario_efectivo_grupo'] / merged['consumo_diario_grupo'].replace(0, np.nan),
        np.inf,
    )

    n_items_grupo = merged.groupby(['store_code', 'grupo_id'])['item_code'].transform('size')
    merged['item_share_consumo'] = np.where(
        merged['consumo_diario_grupo'] > 0,
        merged['consumo_diario'] / merged['consumo_diario_grupo'].replace(0, np.nan),
        1.0 / n_items_grupo,
    )

    # 12.b RENOVACIÓN POR VIDA ÚTIL (insumos de la Fase 3). La vida útil sale de
    # VIDA UTIL EXHIBICION de Portafolio Fruver (vacía o 0 = ítem omitido) y el
    # último ingreso de Inventario Tiendas (Siesa, una fila por ítem × bodega).
    # Los días se cuentan contra la fecha de corte del archivo (su ingreso más
    # reciente), no contra el reloj: el resultado no cambia según el día en que
    # se corra y un archivo viejo se detecta con alerta. DIAS CONSERVACION CEDI y
    # TOTAL VIDA UTIL (opcionales) no entran al algoritmo; quedan para análisis
    # de compras. Si TOTAL llega vacío se reconstruye como EXHIBICION + CEDI.
    def _numerica(col):
        """(serie numérica con NaN, cantidad de celdas no numéricas) de una columna opcional."""
        if col not in portafolio.columns:
            return pd.Series(np.nan, index=portafolio.index), 0
        crudo = portafolio[col]
        num = pd.to_numeric(crudo, errors='coerce')
        return num, int((crudo.notna() & num.isna()).sum())

    _vu_ex, _bad_ex = _numerica('VIDA UTIL EXHIBICION')
    _vu_ce, _bad_ce = _numerica('DIAS CONSERVACION CEDI')
    _vu_to_crudo, _bad_to = _numerica('TOTAL VIDA UTIL')
    _vu_to = _vu_to_crudo.fillna(_vu_ex + _vu_ce)

    _items = portafolio['item_key']
    # Portafolio al que pertenece el ítem (CLUSTERIZACIÓN: BASICO / EXTENDIDO / NEVERA F); solo informativo.
    if 'CLUSTERIZACIÓN' in portafolio.columns:
        _cl = portafolio.drop_duplicates('item_key').set_index('item_key')['CLUSTERIZACIÓN']
        merged['portafolio'] = merged['item_code'].map(_cl.fillna('').astype(str).str.strip()).fillna('')
    else:
        merged['portafolio'] = ''
    _vu_map = dict(zip(_items[_vu_ex > 0], _vu_ex[_vu_ex > 0]))
    _ce_map = dict(zip(_items[_vu_ce >= 0], _vu_ce[_vu_ce >= 0]))
    _to_map = dict(zip(_items[_vu_to >= 0], _vu_to[_vu_to >= 0]))
    merged['vida_util_portafolio'] = merged['item_code'].map(_vu_map).astype(float)
    merged['vida_util_cedi'] = merged['item_code'].map(_ce_map).astype(float)
    merged['vida_util_total'] = merged['item_code'].map(_to_map).astype(float)

    _n_no_num = _bad_ex + _bad_ce + _bad_to
    _neg = _items[(_vu_ex < 0) | (_vu_ce < 0) | (_vu_to_crudo < 0)]
    _incons = _items[
        _vu_to_crudo.notna() & _vu_ex.notna() & _vu_ce.notna()
        & ((_vu_to_crudo - (_vu_ex + _vu_ce)).abs() > 1e-9)
    ]
    for _lista, _txt in (
        (_neg, 'con días de vida útil negativos'),
        (_incons, 'con TOTAL VIDA UTIL distinto de VIDA UTIL EXHIBICION + DIAS CONSERVACION CEDI'),
    ):
        if len(_lista) > 0:
            alertas.append({
                'tipo': 'ADVERTENCIA', 'item': '-',
                'mensaje': f'Portafolio Fruver: {len(_lista)} ítem(s) {_txt} '
                           f'({", ".join(str(int(i)) for i in sorted(_lista)[:10])}'
                           f'{"…" if len(_lista) > 10 else ""}). Revisar el archivo.',
                'items_afectados': [int(i) for i in sorted(_lista)],
            })
    if _n_no_num > 0:
        alertas.append({
            'tipo': 'ADVERTENCIA', 'item': '-',
            'mensaje': f'Portafolio Fruver: {_n_no_num} celda(s) de vida útil no numéricas '
                       '(se tratan como vacías).',
            'celdas_no_numericas': _n_no_num,
        })

    # `fecha_ultimo_ingreso` ya viene del paso 8.b (varias sub-bodegas del C.O.: la MÁS RECIENTE).
    merged['fecha_ultimo_ingreso'] = pd.to_datetime(merged['fecha_ultimo_ingreso'])
    merged['fecha_corte_inventario'] = _fecha_corte
    merged['dias_desde_ingreso'] = (
        (merged['fecha_corte_inventario'] - merged['fecha_ultimo_ingreso']).dt.days.astype(float)
    )
    merged['venta_esperada_vida_util'] = merged['consumo_diario_grupo'] * merged['vida_util_portafolio']

    # Ingreso fresco por tienda × grupo espejo: si un SKU hermano entró hace menos
    # de la vida útil del grupo, no se renueva con el otro SKU.
    _vu_grupo = merged.groupby(['store_code', 'grupo_id'])['vida_util_portafolio'].transform('max')
    _fresco = merged['dias_desde_ingreso'].notna() & (merged['dias_desde_ingreso'] <= _vu_grupo)
    merged['grupo_con_ingreso_fresco'] = (
        _fresco.groupby([merged['store_code'], merged['grupo_id']]).transform('any')
    )

    if inv_t is not None:
        if pd.isna(_fecha_corte):
            alertas.append({
                'tipo': 'ADVERTENCIA', 'item': '-',
                'mensaje': 'Inventario Tiendas no trae ninguna fecha de último ingreso válida: '
                           'la Fase 3 (renovación por vida útil) no se puede aplicar.',
            })
        elif _fecha_corte < pd.Timestamp.today().normalize() - pd.Timedelta(days=1):
            alertas.append({
                'tipo': 'ADVERTENCIA', 'item': '-',
                'mensaje': f'Inventario Tiendas parece desactualizado: su ingreso más reciente es '
                           f'del {_fecha_corte:%Y-%m-%d}. Los días desde el último ingreso se '
                           'cuentan contra esa fecha; revisar que sea el extracto de hoy.',
                'fecha_corte_inventario': f'{_fecha_corte:%Y-%m-%d}',
            })
        _sin_fecha = merged['vida_util_portafolio'].notna() & merged['fecha_ultimo_ingreso'].isna()
        if _sin_fecha.any():
            alertas.append({
                'tipo': 'ADVERTENCIA', 'item': '-',
                'mensaje': f'{int(_sin_fecha.sum())} combinación(es) tienda-ítem con vida útil '
                           'definida no tienen fecha de último ingreso en Inventario Tiendas; '
                           'no se evalúan para la renovación por vida útil.',
                'combos_sin_fecha_ingreso': int(_sin_fecha.sum()),
            })

    # 13. Ítems con stock en CEDI que los inner join de los pasos 5 y 10 dejaron sin
    # ninguna fila: el algoritmo ni los ve y se viola el cero residuo.
    items_distribuidos = set(merged['item_code'].unique())
    for item_key in sorted(stock_item_keys - items_distribuidos):
        cajas = int(stock.loc[stock['item_key'] == item_key, 'cajas_disponibles'].sum())
        alertas.append({
            'tipo': 'CRÍTICO', 'item': item_key,
            'mensaje': f'Ítem {item_key} tiene {cajas} caja(s) en CEDI pero 0 tiendas '
                       'elegibles tras el cruce (Tiendas × Ítem activo / Tiendas activas) '
                       '— no se distribuyó nada. Revisar portafolio.',
            'cajas_sin_distribuir': cajas,
        })

    # 13.b Alertas de existencias (paso 8.b), sobre las combinaciones que quedaron (tiendas activas,
    # con pedido) y con datos en Celes, para que cuadren con la salida.
    if _existencias_de_siesa:
        _con_datos = ~merged['sin_match_celes']
        _sin_fila = merged['_sin_fila_siesa'] & _con_datos
        if _sin_fila.any():
            alertas.append({
                'tipo': 'ADVERTENCIA', 'item': '-',
                'mensaje': f'{int(_sin_fila.sum())} combinación(es) tienda-ítem no aparecen en Inventario '
                           'Tiendas (Siesa); se tomaron con 0 existencias.',
                'combos_sin_inventario_siesa': int(_sin_fila.sum()),
            })
        _recibido = _con_datos & (merged['transito_ya_recibido'] > 0)
        if _recibido.any():
            _cajas_rec = float(merged.loc[_recibido, 'transito_ya_recibido'].sum())
            alertas.append({
                'tipo': 'ADVERTENCIA', 'item': '-',
                'mensaje': f'{int(_recibido.sum())} combinación(es) tienda-ítem ya recibieron en Siesa el '
                           f'tránsito que Celes aún mostraba en camino ({_cajas_rec:.2f} caja(s)); se '
                           'descontó del tránsito para no contarlo dos veces.',
                'combos_transito_recibido': int(_recibido.sum()),
                'cajas_transito_recibido': round(_cajas_rec, 2),
            })
        # Diferencia que el tránsito recibido NO explica (ventas o ingresos entre las dos fotos).
        _dif = (merged['inventario_tienda'] - merged['inventario_tienda_celes']
                - merged['transito_ya_recibido']).abs()
        _difieren = _con_datos & ~_sin_fila & (_dif > 0.5)
        if _difieren.any():
            alertas.append({
                'tipo': 'ADVERTENCIA', 'item': '-',
                'mensaje': f'{int(_difieren.sum())} combinación(es) tienda-ítem tienen existencias distintas '
                           'en Inventario Tiendas (Siesa) y en Celes (más de 0,5 caja, sin contar el tránsito '
                           'ya recibido): se movieron entre las dos fotos; se usó Siesa.',
                'combos_existencias_distintas': int(_difieren.sum()),
                'tiendas_afectadas': int(merged.loc[_difieren, 'store_code'].nunique()),
            })

    # 14. Alerta agregada (una sola) de combinaciones excluidas por el tope MAX,
    # para que el efecto de BD_MAX sea auditable.
    n_bloqueadas = int(merged['bloqueado_por_max'].sum())
    if n_bloqueadas > 0:
        detalle = merged.loc[merged['bloqueado_por_max'], ['store_code', 'item_code']]
        n_tiendas_bloq = detalle['store_code'].nunique()
        n_items_bloq = detalle['item_code'].nunique()
        alertas.append({
            'tipo': 'ADVERTENCIA', 'item': '-',
            'mensaje': (
                f'{n_bloqueadas} combinación(es) tienda-ítem quedaron fuera del '
                f'reparto normal por el tope MAX ({n_tiendas_bloq} tienda(s), '
                f'{n_items_bloq} ítem(s)): ya tienen en existencias más tránsito '
                'tanto o más de lo definido como máximo para ese ítem.'
            ),
            'combos_bloqueados': n_bloqueadas,
            'tiendas_afectadas': int(n_tiendas_bloq),
            'items_afectados': int(n_items_bloq),
        })

    cols_out = [
        'item_code', 'item_desc', 'portafolio', 'store_code', 'store_name', 'zona',
        'consumo_diario', 'dias_inventario_actuales', 'inventario_tienda', 'inventario_tienda_celes',
        'inventario_transito', 'inventario_transito_celes', 'transito_ya_recibido',
        'inventario_total', 'inventario_efectivo', 'dias_proyectados',
        'cajas_disponibles_cedi', 'um', 'vida_util',
        'sin_match_celes', 'causa_sin_match',
        'max_existencias', 'bloqueado_por_max', 'excluida_del_reparto',
        'grupo_id', 'consumo_diario_grupo', 'inventario_efectivo_grupo',
        'dias_proyectados_grupo', 'item_share_consumo',
        'vida_util_portafolio', 'vida_util_cedi', 'vida_util_total',
        'fecha_ultimo_ingreso', 'fecha_corte_inventario',
        'dias_desde_ingreso', 'venta_esperada_vida_util', 'grupo_con_ingreso_fresco',
    ]
    return merged[cols_out].reset_index(drop=True), alertas

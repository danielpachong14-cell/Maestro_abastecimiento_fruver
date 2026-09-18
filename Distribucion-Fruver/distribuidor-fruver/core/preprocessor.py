"""Limpieza, normalización de claves de cruce y merge en un único DataFrame
tienda × ítem.

Aquí viven los ajustes necesarios para que las claves reales de los archivos
crucen correctamente (validados contra los datos de producción):

  1. Códigos de tienda: Celes usa «BOS03» y la Base de Tiendas «S03». Se quita
     el prefijo «BO» del código de Celes para obtener el COD SIESA.
  2. Códigos de ítem: Stock trae «0000155» (texto con ceros a la izquierda) y el
     resto de archivos usa 155 (entero). Se normaliza todo a entero.
  3. Stock incluye una fila totalizadora («Gran total» u otra variante con
     "total" en el texto) que debe eliminarse. `ESTADO DEL PRODUCTO` (pruebas,
     dados de baja, etc.) NO filtra la distribución — decisión del negocio:
     si hay existencia física en el CEDI, se distribuye sin importar el
     estado — pero si no es `ACTIVO` se reporta como alerta informativa.

Además, para el cálculo de necesidad/prioridad el inventario efectivo de cada
tienda se calcula como «Inventario Disponible en esta Tienda» + «(-) inventario
de traslado en proceso» (Existencias + tránsito), en vez de tomar directamente
«(=) Inventario Total» de Celes. Se volvió a este cálculo (auditoría 2026-09)
tras confirmar con datos reales que «(=) Inventario Total» puede venir inflado
frente a Existencias + Tránsito (caso tienda Campín, ítem 1210: Celes reportaba
9.33 cajas en «(=) Inventario Total» mientras Existencias + Tránsito daba 3.33
= 1.33 + 2). Nota: entre jul-sep 2026 se usó «(=) Inventario Total» directamente
porque en el 18.7% de las filas tienda-ítem «(-) inventario de traslado en
proceso» quedaba en 0 sin capturar tránsito real que sí existía — ese caso
sigue siendo un riesgo conocido de esta fórmula (puede subestimar el inventario
cuando el campo de tránsito de Celes no captura el dato), pero el negocio
decidió priorizar no sobrestimar el inventario de la tienda.

DB_Tiendas es la única fuente de tiendas activas: el cruce final con ella es
inner, no left — Celes y Tiendas×Ítem pueden traer tiendas que ya no operan
(cerradas, dadas de baja) y esas quedan excluidas del reparto aunque tengan
datos de consumo o portafolio activo.

Tope MAX por tienda×ítem: Tiendas×Ítem trae una columna `MAX` (calculada
dentro del propio Excel con XLOOKUP contra su hoja auxiliar BD_MAX) con el
máximo de existencias que esa tienda puede tener de ese ítem. Si
`Existencias >= MAX` la combinación queda marcada en `bloqueado_por_max` y
sale del reparto normal — a diferencia del inventario efectivo que usa el
resto del pipeline, aquí se compara SOLO contra Existencias, porque el
tránsito ya va en camino y no debe bloquear un envío. `MAX = 0` (el caso de
casi todas las filas) significa "sin tope definido", no "no enviar".

`build_distribution_df` retorna `(df_merged, alertas)`: además del DataFrame
unificado, reporta como alertas los problemas de calidad de datos detectados
durante el cruce (filas descartadas por código no normalizable, tienda-ítem
sin match en Celes, ítems con stock en CEDI que quedaron sin ninguna tienda
elegible, e ítems de Stock sin catalogar o descontinuados en Portafolio
Fruver) — antes se perdían en silencio.
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
    """Aplica `normalize_item` a una columna completa. Cuando la columna ya
    es de tipo entero (el caso real de Portafolio, Tiendas×Ítem, Celes y
    Espejo, verificado contra los archivos de producción — solo Stock trae
    el código como texto con ceros a la izquierda), `normalize_item` es un
    no-op matemático sobre cada valor, así que se evita el costo de
    `.apply()` fila por fila y se retorna la columna tal cual."""
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

    No basta con `str.contains('ACTIVO')`: 'INACTIVO' también contiene la
    subcadena 'ACTIVO', así que un contains simple clasificaba como activas
    filas marcadas INACTIVO. Bug real de producción confirmado contra los
    datos de `Input/`: afectaba 955 de 4878 filas de Tiendas × Ítem (la
    fuente de verdad de elegibilidad) y 23 de 84 de Portafolio Fruver — casi
    una quinta parte de las combinaciones tienda-ítem se trataban como
    elegibles estando marcadas inactivas. Se toma el último segmento tras
    separar por '-' (cubre 'NNN - ESTADO' y también un estado sin prefijo)."""
    ultimo_segmento = series.astype(str).str.split('-').str[-1].str.strip().str.upper()
    return ultimo_segmento == 'ACTIVO'


def _alertar_filas_descartadas(n_antes, n_despues, archivo_label, alertas):
    """Registra cuántas filas se perdieron al no poder normalizar su código
    de ítem (dropna posterior a `_normalize_item_series`) — antes se perdían
    sin ningún rastro en el resultado final."""
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


def build_distribution_df(dfs):
    """Construye el DataFrame unificado tienda × ítem elegible.

    Cada fila es una combinación tienda-ítem elegible con las métricas
    necesarias para el algoritmo de distribución.

    Retorna (df_merged, alertas): alertas es una lista de dicts
    {tipo, item, mensaje, ...} con problemas de calidad de datos detectados
    durante el cruce (ver docstring del módulo).
    """
    alertas = []

    # 1. STOCK: quitar totalizador y quedarse con existencia. Cant. disponible
    # ya está en cajas completas — no se divide por Factor U.M. U.M. (ej.
    # CJ20) indica cuántas unidades tiene la caja, pero la distribución
    # trabaja en cajas, no en unidades. ESTADO DEL PRODUCTO NO filtra (ver
    # alerta informativa más abajo) — decisión del negocio: toda existencia
    # física en el CEDI se distribuye, sin importar su estado.
    stock = dfs['stock'].copy()
    es_totalizador = stock['Item'].astype(str).str.strip().str.lower().str.contains('total', na=False)
    stock = stock[~es_totalizador]
    stock['Cant. disponible'] = pd.to_numeric(stock['Cant. disponible'], errors='coerce').fillna(0)
    stock = stock[stock['Cant. disponible'] > 0].copy()
    stock['cajas_disponibles'] = stock['Cant. disponible'].apply(math.floor)
    stock = stock[stock['cajas_disponibles'] > 0]
    stock['item_key'] = _normalize_item_series(stock['Item'])
    _n_antes = len(stock)
    stock = stock.dropna(subset=['item_key'])
    _alertar_filas_descartadas(_n_antes, len(stock), 'Stock CEDI (Siesa)', alertas)
    stock['item_key'] = stock['item_key'].astype(int)
    stock = stock.drop_duplicates(['item_key'], keep='first')

    # Alerta informativa (no bloqueante): ítems con existencia cuyo ESTADO DEL
    # PRODUCTO no es exactamente ACTIVO (pruebas, dados de baja, etc.) se
    # distribuyen igual — el negocio decidió que el estado no debe frenar el
    # envío — pero se reportan para que se pueda revisar si corresponde.
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

    # 2. PORTAFOLIO: catálogo completo de ítems (activos e inactivos) para
    # validación cruzada contra Stock — no aporta datos al DataFrame final
    # (la elegibilidad real sale de tiendas_item), pero detecta ítems de
    # Stock que no están catalogados o que están descontinuados.
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
    # MAX: tope de existencias que esa tienda puede tener de ese ítem. Es una
    # columna calculada dentro del propio Excel (XLOOKUP de la hoja Hoja1
    # contra la hoja auxiliar BD_MAX, cruzando por LLAVE = COD SIESA + ITEM),
    # así que aquí llega ya resuelta como número. 0 / vacío = sin tope
    # definido, que es el caso de la inmensa mayoría de las filas.
    tiendas_item['max_existencias'] = (
        pd.to_numeric(tiendas_item['MAX'], errors='coerce').fillna(0).clip(lower=0)
    )
    tiendas_item = tiendas_item.drop_duplicates(['COD SIESA', 'item_key'], keep='first')

    # 4. CELES: consumos e inventarios. Se distribuye a TODAS las tiendas presentes,
    #    sin filtrar por el flag de distribución automática.
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

    # 6. MERGE 2: con Celes para consumo, días e inventarios (Existencias +
    # tránsito, ver docstring del módulo — inventario_efectivo se calcula a
    # partir de estas dos columnas, no de «(=) Inventario Total»).
    # indicator marca qué combinaciones tienda-ítem NO tuvieron match en Celes
    # (left_only) — se conserva como columna `sin_match_celes` porque esas filas
    # se rellenan con consumo=0/inventario=0 más abajo, lo que las clasifica
    # como AGOTADA en el algoritmo aunque en realidad solo falte el dato.
    celes_cols = [
        'cod_siesa', 'item_key', 'Nombre de Bodega',
        'Consumo Diario (Unidades de Distribución)',
        '(=) Días de Inventario Actuales',
        'Inventario Disponible en esta Tienda (Unidades de Distibución)',
        '(-) inventario de traslado en proceso',
        '(=) Inventario Total',
        'UM', 'vida_util',
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

    # 8. Rellenar NaN en columnas numéricas usadas por el algoritmo y descartar
    # negativos (no deberían llegar así de Celes, pero un valor negativo aguas
    # arriba no debe traducirse en pesos/necesidades negativas aguas abajo).
    # Todas las magnitudes de Celes están en Unidades de Distribución = cajas.
    for col in ['consumo_diario', 'dias_inventario_actuales', 'inventario_tienda', 'inventario_transito', 'inventario_total', 'vida_util']:
        merged[col] = pd.to_numeric(merged[col], errors='coerce').fillna(0).clip(lower=0)

    # 9. Inventario efectivo (Existencias + tránsito, ver docstring del módulo)
    # y días proyectados antes de esta distribución. `inventario_total` («(=)
    # Inventario Total» de Celes) se conserva como columna informativa pero ya
    # no participa en esta fórmula.
    merged['inventario_efectivo'] = merged['inventario_tienda'] + merged['inventario_transito']
    merged['dias_proyectados'] = np.where(
        merged['consumo_diario'] > 0,
        merged['inventario_efectivo'] / merged['consumo_diario'].replace(0, np.nan),
        np.inf,
    )

    # 9.b BLOQUEO POR MAX: la tienda ya tiene tanto o más de lo que puede tener
    # de este ítem, así que queda fuera del reparto normal (es un interruptor
    # de elegibilidad, no un techo de cantidad: si NO está bloqueada participa
    # con normalidad, acotada por MAX_CAJAS_POR_ITEM como cualquier otra).
    #
    # Se compara contra `inventario_tienda` (Existencias) a propósito, NO
    # contra `inventario_efectivo` (Existencias + tránsito) que usa el resto
    # del motor: el tránsito ya va en camino a la tienda y no debe impedir un
    # envío. Decisión de negocio explícita (caso S96/1210: Existencias 5.07 +
    # tránsito 3 = 8.07 con MAX 6 — sigue recibiendo porque 5.07 < 6).
    #
    # El corte es `>=`: con MAX 6, una tienda con 6.0 o 6.1 ya llegó a su
    # máximo y no recibe; con 5.9 todavía entra al reparto normal.
    #
    # `max_existencias == 0` significa "sin tope definido", no "no enviar" —
    # tratarlo como bloqueo dejaría fuera casi todo el portafolio.
    merged['bloqueado_por_max'] = (
        (merged['max_existencias'] > 0)
        & (merged['inventario_tienda'] >= merged['max_existencias'])
    )

    # 10. Restringir a tiendas activas (DB_Tiendas) y agregar su zona y nombre.
    # DB_Tiendas es la única fuente de tiendas activas: Celes y Tiendas×Ítem
    # pueden traer tiendas que ya no operan (cerradas, dadas de baja), así que
    # el cruce es inner — quien no esté en DB_Tiendas queda fuera del reparto.
    # NOMBRE DE LA TIENDA es también la única fuente de store_name — antes se
    # prefería el nombre de Celes ('Nombre de Bodega') y solo se caía al de
    # DB_Tiendas cuando la tienda-ítem no tenía match en Celes; como los dos
    # archivos pueden nombrar la misma tienda distinto (ej. SE4: Celes trae
    # "ISIMO LEON XIII", DB_Tiendas trae "LEON XIII"), esa mezcla partía una
    # misma tienda en dos filas en la hoja Resumen del exporter, que agrupa
    # por nombre además de por código. Usar siempre DB_Tiendas garantiza un
    # nombre único por store_code sin importar el estado del match con Celes.
    # Se renombra a un nombre interno único antes del merge porque Tiendas×Ítem
    # (DB_Tiendas Por itmes y portafolio.xlsx) YA trae su propia columna
    # 'NOMBRE DE LA TIENDA' — sin este rename, pandas sufija ambas como
    # '..._x'/'..._y' y la referencia directa más abajo falla con KeyError.
    tiendas = dfs['tiendas'][['COD SIESA', 'NOMBRE DE LA TIENDA', 'ZONA']].copy()
    tiendas = tiendas.rename(columns={'NOMBRE DE LA TIENDA': '_nombre_tienda_maestro'})
    tiendas['COD SIESA'] = tiendas['COD SIESA'].astype(str).str.strip()
    merged = merged.merge(tiendas, left_on='store_code', right_on='COD SIESA', how='inner')
    merged['zona'] = merged['ZONA'].fillna('').astype(str).str.strip()
    merged['store_name'] = merged['_nombre_tienda_maestro']
    # No hace falta eliminar las columnas crudas ('ZONA', 'COD SIESA',
    # '_nombre_tienda_maestro', etc.): `cols_out` al final del pipeline ya
    # selecciona explícitamente solo las columnas necesarias.

    # 11. Excluir tiendas marcadas como "sin pedido".
    if 'excluidas' in dfs:
        excl = dfs['excluidas'].copy()
        excl.columns = [str(c).strip() for c in excl.columns]
        excl_codes = set(excl['Centro Operacional de la Bodega'].astype(str).str.strip())
        merged = merged[~merged['store_code'].isin(excl_codes)]

    # 12. PRODUCTOS ESPEJO: ítems que son el mismo producto físico bajo distinto
    # código de SKU compiten por la misma demanda en tienda. Se les asigna un
    # grupo común para que prioridad/necesidad se calculen sobre el conjunto y
    # no se dupliquen — la distribución física sigue siendo por ítem real.
    # Un ítem sin entrada en el archivo espejo queda en un grupo de tamaño 1
    # (su propio item_code), por lo que las fórmulas de grupo colapsan a los
    # valores individuales actuales: cero cambio de comportamiento.
    espejo = dfs['espejo'].copy()
    espejo['item_key'] = _normalize_item_series(espejo['item_id'])
    _n_antes = len(espejo)
    espejo = espejo.dropna(subset=['item_key', 'grupo_id'])
    _alertar_filas_descartadas(_n_antes, len(espejo), 'Productos Espejo', alertas)
    item_to_grupo = dict(zip(espejo['item_key'].astype(int), espejo['grupo_id'].astype(str)))

    # .astype('object') fuerza el dtype independientemente de si item_to_grupo
    # está vacío o no: si NINGÚN ítem de la corrida tiene entrada en el
    # archivo espejo, `.map()` da una columna 100% NaN que pandas infiere
    # como float64, y la asignación de strings de abajo revienta con
    # TypeError bajo pandas 3.x sin este cast explícito.
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

    # 13. Ítems con stock en CEDI que no llegan a ninguna fila del DataFrame
    # final (los inner join de los pasos 5 y 10 los descartan silenciosamente
    # si no tienen ninguna tienda elegible activa) — violan la garantía de
    # cero residuo sin que el algoritmo llegue siquiera a verlos.
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

    # 14. Alerta agregada (una sola, no por fila) de combinaciones excluidas
    # por el tope MAX, para que el impacto de la tabla BD_MAX sea auditable
    # desde la hoja de Alertas en vez de ser un filtro silencioso.
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
                f'{n_items_bloq} ítem(s)): ya tienen en existencias tanto o más '
                'de lo definido como máximo para ese ítem.'
            ),
            'combos_bloqueados': n_bloqueadas,
            'tiendas_afectadas': int(n_tiendas_bloq),
            'items_afectados': int(n_items_bloq),
        })

    cols_out = [
        'item_code', 'item_desc', 'store_code', 'store_name', 'zona',
        'consumo_diario', 'dias_inventario_actuales', 'inventario_tienda',
        'inventario_transito', 'inventario_total', 'inventario_efectivo', 'dias_proyectados',
        'cajas_disponibles_cedi', 'um', 'vida_util', 'sin_match_celes',
        'max_existencias', 'bloqueado_por_max',
        'grupo_id', 'consumo_diario_grupo', 'inventario_efectivo_grupo',
        'dias_proyectados_grupo', 'item_share_consumo',
    ]
    return merged[cols_out].reset_index(drop=True), alertas

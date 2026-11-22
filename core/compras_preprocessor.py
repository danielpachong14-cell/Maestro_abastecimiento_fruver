"""DataFrame tienda × ítem de la compra: universo = ítems ACTIVOS del Portafolio (cruce left con Stock),
suma el pedido de distribución de hoy y proyecta cada tienda × ítem al día de llegada de la compra.
Retorna `(df, items, alertas, oc_detalle)`.
"""

import math
from datetime import timedelta

import numpy as np
import pandas as pd

from .preprocessor import (
    _alertar_filas_descartadas,
    _is_active,
    _normalize_item_series,
    normalize_store,
)

_COL_CELES = {
    'Consumo Diario (Unidades de Distribución)': 'consumo_diario',
    'Inventario Disponible en esta Tienda (Unidades de Distibución)': 'existencias_celes',
    '(-) inventario de traslado en proceso': 'transito',
    '(+) Stock de Seguridad': 'ss_celes',
    'Tiempo de Entrega desde Centro de Distribución (Días)': 'lt_celes',
    'Periodo de Revisión de Distribución (Días)': 'r_celes',
    'Nivel de Servicio': 'nivel_servicio',
}


def _num(serie, default=0.0):
    return pd.to_numeric(serie, errors='coerce').fillna(default)


def a_fecha(serie):
    """Fechas de Excel: objetos fecha, texto ISO (2026-11-20) o día primero (20/11/2026)."""
    serie = pd.Series(serie)
    fechas = pd.to_datetime(serie, errors='coerce', format='ISO8601')
    resto = fechas.isna() & serie.notna()
    if resto.any():
        fechas[resto] = pd.to_datetime(serie[resto].astype(str), errors='coerce', dayfirst=True, format='mixed')
    return fechas


def _portafolio_activo(dfs, alertas):
    port = dfs['portafolio'].copy()
    port['item_code'] = _normalize_item_series(port['ITEM'])
    n = len(port)
    port = port.dropna(subset=['item_code'])
    _alertar_filas_descartadas(n, len(port), 'Portafolio Fruver', alertas)
    port['item_code'] = port['item_code'].astype(int)
    port = port[_is_active(port['ESTADO'])].drop_duplicates('item_code')
    vu = pd.to_numeric(port.get('VIDA UTIL EXHIBICION'), errors='coerce')
    dia = port['DIA DE COMPRA'] if 'DIA DE COMPRA' in port else pd.Series('Todos los dias', index=port.index)
    return pd.DataFrame({
        'dia_compra': dia.values,
        'item_code': port['item_code'].values,
        'item_desc': (port['DESCRIPCION'] if 'DESCRIPCION' in port else port['ITEM']).astype(str).str.strip().values,
        'cluster': port['CLUSTERIZACIÓN'].fillna('').astype(str).str.strip().values,
        'um_portafolio': (port['UM'] if 'UM' in port else pd.Series('', index=port.index)).values,
        'costo_portafolio': _num(port['COSTO'] if 'COSTO' in port else pd.Series(np.nan, index=port.index),
                                 np.nan).values,
        'vida_util': vu.where(vu > 0).values,
    })


def _stock_cedi(dfs):
    """Cajas en CEDI por ítem (sin fila totalizadora) y costo promedio por caja."""
    stock = dfs['stock'].copy()
    es_total = stock['Item'].astype(str).str.strip().str.lower().str.contains('total', na=False)
    stock = stock[~es_total].copy()
    stock['item_code'] = _normalize_item_series(stock['Item'])
    stock = stock.dropna(subset=['item_code'])
    stock['item_code'] = stock['item_code'].astype(int)
    stock['cajas'] = _num(stock['Cant. disponible']).clip(lower=0).apply(math.floor)
    costo = (_num(stock['Costo prom. uni.'], np.nan) if 'Costo prom. uni.' in stock
             else pd.Series(np.nan, index=stock.index))
    stock['costo'] = costo.where(costo > 0)
    return stock.groupby('item_code').agg(
        cedi_stock=('cajas', 'sum'), costo_stock=('costo', 'first'), um_stock=('U.M.', 'first'),
    ).reset_index()


_OC_ALIAS = {'Cantidad (cajas)': 'Cant. ordenada', 'Fecha esperada CEDI': 'Fecha de Entrega'}
_OC_CERRADA = ('anul', 'cancel', 'cerr', 'cumpl', 'rechaz', 'liquid')
OC_CUENTAN = ('En tránsito', 'Atrasada', 'Sin fecha')
_COLS_OC_DETALLE = ['Nro orden', 'Fecha orden', 'item_code', 'Desc. item', 'Proveedor', 'Fecha de Entrega',
                    'pendiente', 'clasificacion', 'cuenta', 'ultima_entrada_cedi']


def _clasificar_oc(compras_transito, plan, items_activos, stock_raw, params, alertas):
    """Clasifica cada línea del export de OC de Siesa y suma por ítem lo que está en camino.

    Pendiente = Cant. ordenada − Cant. entrada. Se descartan las cerradas (Fecha cumplido o Estado
    anulada/cerrada/cumplida…), las de otra bodega distinta al CEDI y las de ítems no activos. Con la
    fecha esperada (Fecha de Entrega; si falta, Fecha orden + lead time):

    - después de hoy y hasta la llegada de la compra de hoy → «En tránsito» (cuenta);
    - después de la llegada de la compra de hoy → «Llega después» (la cuenta la siguiente compra);
    - hoy o antes: si Stock ya registra un ingreso del ítem desde esa fecha → «Recibida según Stock»
      (no cuenta: en el export, Cant. entrada no se actualiza); si no, hasta DIAS_MAX_ATRASO_OC días →
      «Atrasada» (cuenta, se asume que llega hoy); más vieja → «Vencida sin recibir» (no cuenta).

    Retorna (por_item, detalle).
    """
    vacio = pd.DataFrame(columns=['item_code', 'oc_transito', 'oc_posterior', 'oc_llegada_tienda', 'proveedor'])
    if compras_transito is None:
        alertas.append({
            'tipo': 'ADVERTENCIA', 'item': '-',
            'mensaje': 'No se cargó «Compras_Transito.xlsx» (órdenes de compra en camino al CEDI): la '
                       'compra se calcula como si no hubiera nada pedido al proveedor. Si hay OC abiertas, '
                       'se comprará doble — revisar antes de enviar la compra.',
        })
        return vacio, pd.DataFrame(columns=_COLS_OC_DETALLE)
    oc = compras_transito.copy()
    oc.columns = [str(c).strip() for c in oc.columns]
    oc = oc.rename(columns={a: b for a, b in _OC_ALIAS.items() if a in oc.columns and b not in oc.columns})
    oc['item_code'] = _normalize_item_series(oc['Item'])
    n = len(oc)
    oc = oc.dropna(subset=['item_code'])
    _alertar_filas_descartadas(n, len(oc), 'Compras en tránsito (OC)', alertas)
    if len(oc) == 0:
        alertas.append({
            'tipo': 'ADVERTENCIA', 'item': '-',
            'mensaje': '«Compras_Transito.xlsx» está vacío: la compra se calcula como si no hubiera '
                       'órdenes de compra en camino al CEDI. Si las hay, se comprará doble.',
        })
        return vacio, pd.DataFrame(columns=_COLS_OC_DETALLE)
    oc['item_code'] = oc['item_code'].astype(int)
    recibida = _num(oc['Cant. entrada']) if 'Cant. entrada' in oc else 0.0
    oc['pendiente'] = (_num(oc['Cant. ordenada']) - recibida).clip(lower=0)
    oc['Proveedor'] = oc['Razón social proveedor'].astype(str).str.strip() if 'Razón social proveedor' in oc else ''
    for col in ('Nro orden', 'Desc. item'):
        if col not in oc:
            oc[col] = ''
    oc['Fecha de Entrega'] = a_fecha(oc['Fecha de Entrega']).dt.date
    oc['Fecha orden'] = a_fecha(oc['Fecha orden']).dt.date if 'Fecha orden' in oc else None

    # Último ingreso de cada ítem al CEDI (Stock) y bodegas del CEDI.
    ultima = {}
    bodegas = set()
    if stock_raw is not None:
        st = stock_raw.copy()
        st = st[~st['Item'].astype(str).str.strip().str.lower().str.contains('total', na=False)]
        st['_i'] = _normalize_item_series(st['Item'])
        st = st.dropna(subset=['_i'])
        if 'Fecha última entrada' in st:
            f = a_fecha(st['Fecha última entrada'])
            ultima = {int(k): v.date() for k, v in f.groupby(st['_i'].astype(int)).max().items() if pd.notna(v)}
        if 'Bodega' in st:
            bodegas = set(st['Bodega'].dropna().astype(str).str.strip())

    cal = params.calendario()
    hoy = plan.fecha_datos
    estado = oc['Estado'].astype(str).str.lower() if 'Estado' in oc else pd.Series('', index=oc.index)
    cumplido = a_fecha(oc['Fecha cumplido']).notna() if 'Fecha cumplido' in oc else pd.Series(False, index=oc.index)
    bodega = oc['Bodega'].astype(str).str.strip() if 'Bodega' in oc else pd.Series('', index=oc.index)

    clases, esperadas = [], []
    for i, r in oc.iterrows():
        esperada = r['Fecha de Entrega'] if pd.notna(r['Fecha de Entrega']) else None
        if esperada is None and r['Fecha orden'] is not None and pd.notna(r['Fecha orden']):
            esperada = cal.llegada_cedi(r['Fecha orden'])
        esperadas.append(esperada)
        if cumplido[i] or any(p in estado[i] for p in _OC_CERRADA):
            clases.append('Cerrada')
        elif r['pendiente'] <= 0:
            clases.append('Recibida')
        elif bodegas and 'Bodega' in oc and bodega[i] not in bodegas:
            clases.append('Otra bodega')
        elif r['item_code'] not in items_activos:
            clases.append('Ítem no activo')
        elif esperada is None:
            clases.append('Sin fecha')
        elif esperada > hoy:
            clases.append('En tránsito' if esperada <= plan.llegada_cedi else 'Llega después')
        else:
            ult = ultima.get(int(r['item_code']))
            if ult is not None and ult >= esperada:
                clases.append('Recibida según Stock')
            elif (hoy - esperada).days <= params.dias_max_atraso_oc:
                clases.append('Atrasada')
            else:
                clases.append('Vencida sin recibir')
    oc['clasificacion'] = clases
    oc['_esperada'] = esperadas
    oc['cuenta'] = oc['clasificacion'].isin(OC_CUENTAN)
    oc['ultima_entrada_cedi'] = oc['item_code'].map(ultima)
    llega_cedi = oc['_esperada'].map(lambda d: max(d, hoy) if d is not None and pd.notna(d) else hoy)
    oc['_llegada_tienda'] = (llega_cedi + timedelta(days=int(params.lt_cedi_tienda))).where(oc['cuenta'])
    oc['_cuenta_cajas'] = oc['pendiente'].where(oc['cuenta'], 0.0)
    oc['_despues'] = oc['pendiente'].where(oc['clasificacion'] == 'Llega después', 0.0)

    # Proveedor: el de la OC más reciente de cada ítem (las cerradas también sirven de referencia).
    orden = oc.sort_values('Fecha orden', ascending=False, na_position='last', key=lambda c: pd.to_datetime(c))
    proveedor = orden.drop_duplicates('item_code').set_index('item_code')['Proveedor']

    por_item = oc.groupby('item_code').agg(
        oc_transito=('_cuenta_cajas', 'sum'), oc_posterior=('_despues', 'sum'),
        oc_llegada_tienda=('_llegada_tienda', lambda v: min([x for x in v if x is not None and pd.notna(x)], default=None)),
    ).reset_index()
    por_item['proveedor'] = por_item['item_code'].map(proveedor)

    # Alerta resumen (una sola) + avisos accionables.
    resumen = oc.groupby('clasificacion')['pendiente'].agg(['count', 'sum'])
    partes = [f'{c}: {int(resumen.loc[c, "count"])} línea(s), {resumen.loc[c, "sum"]:.0f} cajas'
              for c in ('En tránsito', 'Atrasada', 'Sin fecha', 'Llega después', 'Recibida según Stock',
                        'Vencida sin recibir', 'Ítem no activo', 'Otra bodega', 'Cerrada', 'Recibida')
              if c in resumen.index]
    alertas.append({
        'tipo': 'ADVERTENCIA', 'item': '-',
        'mensaje': f'Compras en tránsito ({oc["Nro orden"].nunique()} OC, {len(oc)} líneas) — '
                   + ' · '.join(partes) + '. Cuentan para la compra de hoy: En tránsito, Atrasada y Sin fecha. '
                   'Detalle en la hoja «OC en Tránsito».',
        'oc_por_clasificacion': {c: int(resumen.loc[c, 'count']) for c in resumen.index},
    })
    vencidas = oc[oc['clasificacion'] == 'Vencida sin recibir']
    if len(vencidas):
        alertas.append({
            'tipo': 'ADVERTENCIA', 'item': '-',
            'mensaje': f'{vencidas["Nro orden"].nunique()} OC vencidas hace más de {params.dias_max_atraso_oc} día(s) '
                       f'y sin ingreso en Stock ({", ".join(sorted(vencidas["Nro orden"].astype(str).unique())[:8])}): '
                       'no se cuentan como en camino. Cerrarlas o anularlas en Siesa.',
        })
    atrasadas = oc[oc['clasificacion'] == 'Atrasada']
    if len(atrasadas):
        alertas.append({
            'tipo': 'ADVERTENCIA', 'item': '-',
            'mensaje': f'{len(atrasadas)} línea(s) de OC atrasadas sin ingreso en Stock '
                       f'({atrasadas["pendiente"].sum():.0f} cajas; ítems {", ".join(str(i) for i in sorted(atrasadas["item_code"].unique())[:10])}): '
                       'se cuentan como si llegaran hoy. Confirmar con el proveedor.',
        })
    detalle = oc.rename(columns={'_esperada': 'fecha_esperada'})
    detalle = detalle[_COLS_OC_DETALLE[:5] + ['fecha_esperada'] + _COLS_OC_DETALLE[6:]]
    return por_item, detalle


def _regimen_compra(items, plan, params, alertas):
    """Frecuencia de compra por ítem (columna DIA DE COMPRA del Portafolio / STATUS).

    todos → calendario general · dias → solo esos días; cubre hasta la siguiente compra del ítem ·
    cada N → se revisa a diario y, cuando se activa, compra N días (ver compras._comprar_periodico).
    """
    from dataclasses import replace

    from .calendario import interpretar_dia_compra

    cal = params.calendario()
    filas, raros = [], []
    for code, texto in zip(items['item_code'], items['dia_compra']):
        tipo, valor = interpretar_dia_compra(texto)
        fila = {'item_code': code, 'regimen': 'todos', 'se_compra_hoy': True,
                'peso_cobertura_item': plan.peso_cobertura, 'n_dias_cobertura_item': plan.n_dias_cobertura,
                'siguiente_compra_item': plan.siguiente_compra, 'peso_periodo': np.nan, 'n_dias_periodo': np.nan}
        if tipo == 'dias':
            cal_i = replace(cal, dias_compra=valor)
            fila['regimen'] = 'dias'
            fila['se_compra_hoy'] = cal_i.es_dia_compra(plan.fecha_compra)
            if fila['se_compra_hoy']:
                p_i = cal_i.plan(plan.fecha_datos)
                fila.update(peso_cobertura_item=p_i.peso_cobertura, n_dias_cobertura_item=p_i.n_dias_cobertura,
                            siguiente_compra_item=p_i.siguiente_compra)
            else:
                fila['siguiente_compra_item'] = cal_i.siguiente_compra(plan.fecha_compra)
        elif tipo == 'cada':
            dias_n = cal._rango(plan.llegada_tienda, plan.llegada_tienda + timedelta(days=int(valor)))
            fila.update(regimen='cada', peso_periodo=cal._peso(dias_n), n_dias_periodo=int(valor))
        elif tipo in ('desconocido', 'descontinuado'):
            raros.append(f'{code} («{texto}»)')
        filas.append(fila)
    if raros:
        alertas.append({'tipo': 'ADVERTENCIA', 'item': '-',
                        'mensaje': f'DIA DE COMPRA no reconocido o inconsistente con STATUS ACTIVO en {len(raros)} ítem(s) '
                                   f'({", ".join(raros[:8])}): se compran con el calendario general.'})
    return pd.DataFrame(filas, columns=['item_code', 'regimen', 'se_compra_hoy', 'peso_cobertura_item',
                                        'n_dias_cobertura_item', 'siguiente_compra_item', 'peso_periodo',
                                        'n_dias_periodo'])


def _objetivos(df, peso, n_cov, plan, params):
    """(venta_cobertura, stock_seguridad, objetivo_demanda, objetivo, tope_max) para una cobertura."""
    venta = df['consumo_ajustado'] * peso
    if params.usar_ss_celes:
        base_celes = (df['lt_celes'].fillna(1) + df['r_celes'].fillna(1)).clip(lower=1)
        ss = df['ss_celes'] * np.sqrt((plan.n_dias_hasta_llegada + n_cov) / base_celes) * (1 + df['crecimiento'])
    else:
        ss = pd.Series(0.0, index=df.index)
    demanda = venta + ss
    tope_vu = df['consumo_ajustado'] * df['vida_util']
    obj_dem = pd.Series(np.where(df['vida_util'].notna(), np.minimum(demanda, tope_vu), demanda), index=df.index)
    objetivo = np.maximum(df['min_exhibicion'], obj_dem)
    tope_max = (df['max_existencias'] > 0) & (objetivo > df['max_existencias'])
    objetivo = pd.Series(np.where(df['max_existencias'] > 0, np.minimum(objetivo, df['max_existencias']), objetivo),
                         index=df.index)
    return venta, ss, obj_dem, objetivo, tope_max


def build_purchase_df(dfs, df_distribucion, plan, params, compras_transito=None):
    """Construye (df tienda×ítem, df ítems, alertas, detalle de OC) para `compras.run_compras`.

    dfs: dict de `loader.load_files`. df_distribucion: `df_output` de la distribución de hoy (o None).
    plan: `calendario.PlanCompra`. params: `compras_params.ParametrosCompra`.
    compras_transito: DataFrame de OC (o None si no se cargó el archivo).
    """
    alertas = []
    items = _portafolio_activo(dfs, alertas)
    activos = set(items['item_code'])

    # Tiendas × Ítem: combinaciones ACTIVAS de ítems activos del Portafolio. MIN = exhibición (cajas).
    ti = dfs['tiendas_item'].copy()
    ti = ti[_is_active(ti['ESTADO'])].copy()
    ti['item_code'] = _normalize_item_series(ti['ITEM'])
    n = len(ti)
    ti = ti.dropna(subset=['item_code'])
    _alertar_filas_descartadas(n, len(ti), 'Tiendas × Ítem', alertas)
    ti['item_code'] = ti['item_code'].astype(int)
    ti['store_code'] = ti['COD SIESA'].astype(str).str.strip()
    ti['min_archivo'] = pd.to_numeric(ti['MIN'], errors='coerce') if 'MIN' in ti else np.nan
    ti['max_existencias'] = _num(ti['MAX']).clip(lower=0)
    # Pares repetidos: MIN más alto (la exhibición manda) y MAX más restrictivo (menor > 0).
    ti['_max_pos'] = ti['max_existencias'].where(ti['max_existencias'] > 0)
    ti = ti.groupby(['store_code', 'item_code'], as_index=False).agg(
        min_archivo=('min_archivo', 'max'), _max_pos=('_max_pos', 'min'))
    ti['max_existencias'] = ti['_max_pos'].fillna(0)
    ti = ti.drop(columns='_max_pos')
    ti = ti[ti['item_code'].isin(activos)]

    # Tiendas activas (cruce inner con DB_Tiendas, igual que la distribución).
    tiendas = dfs['tiendas'][['COD SIESA', 'NOMBRE DE LA TIENDA', 'ZONA']].copy()
    tiendas['store_code'] = tiendas['COD SIESA'].astype(str).str.strip()
    tiendas = tiendas.drop_duplicates('store_code')
    df = ti.merge(tiendas[['store_code', 'NOMBRE DE LA TIENDA', 'ZONA']], on='store_code', how='inner')
    df = df.rename(columns={'NOMBRE DE LA TIENDA': 'store_name', 'ZONA': 'zona'})
    df['zona'] = df['zona'].fillna('').astype(str).str.strip()

    if 'excluidas' in dfs and not params.incluir_tiendas_sin_pedido:
        excl = dfs['excluidas'].copy()
        excl.columns = [str(c).strip() for c in excl.columns]
        codigos = set(excl['Centro Operacional de la Bodega'].astype(str).str.strip())
        df = df[~df['store_code'].isin(codigos)]

    # Celes (left): consumo, existencias, tránsito, stock de seguridad.
    celes = dfs['celes'].copy()
    celes['store_code'] = celes['Código de Bodega'].apply(normalize_store)
    celes['item_code'] = _normalize_item_series(celes['Código de Producto'])
    celes = celes.dropna(subset=['store_code', 'item_code'])
    celes['item_code'] = celes['item_code'].astype(int)
    celes = celes.drop_duplicates(['store_code', 'item_code'])
    presentes = [c for c in _COL_CELES if c in celes.columns]
    if '(+) Stock de Seguridad' not in presentes and params.usar_ss_celes:
        alertas.append({'tipo': 'ADVERTENCIA', 'item': '-',
                        'mensaje': 'Celes no trae «(+) Stock de Seguridad»: la compra se calcula sin stock de seguridad.'})
    celes = celes[['store_code', 'item_code'] + presentes].rename(columns=_COL_CELES)
    df = df.merge(celes, on=['store_code', 'item_code'], how='left', indicator='_m')
    df['sin_match_celes'] = df['_m'] == 'left_only'
    df = df.drop(columns='_m')
    for col in _COL_CELES.values():
        if col not in df:
            df[col] = np.nan
    for col in ('consumo_diario', 'existencias_celes', 'transito', 'ss_celes'):
        df[col] = _num(df[col]).clip(lower=0)

    df = df.merge(items, on='item_code', how='left')
    items_en_celes = set(celes['item_code'])
    items['en_celes'] = items['item_code'].isin(items_en_celes)
    df['con_datos'] = ~df['sin_match_celes']

    # Pedido de distribución de hoy (llega mañana a la tienda).
    if df_distribucion is not None and len(df_distribucion) > 0:
        pedido = (df_distribucion.assign(store_code=df_distribucion['store_code'].astype(str).str.strip(),
                                         item_code=df_distribucion['item_code'].astype(int))
                  .groupby(['store_code', 'item_code'])['cajas_asignadas'].sum()
                  .rename('pedido_hoy').reset_index())
        distribuido = pedido.groupby('item_code')['pedido_hoy'].sum().rename('cedi_distribuido_hoy')
    else:
        pedido = pd.DataFrame(columns=['store_code', 'item_code', 'pedido_hoy'])
        distribuido = pd.Series(dtype=float, name='cedi_distribuido_hoy')
        alertas.append({'tipo': 'ADVERTENCIA', 'item': '-',
                        'mensaje': 'La compra se calculó sin el pedido de distribución de hoy: el stock del '
                                   'CEDI se cuenta como inventario por repartir.'})
    df = df.merge(pedido, on=['store_code', 'item_code'], how='left')
    df['pedido_hoy'] = _num(df['pedido_hoy'])

    # Último ingreso (Inventario Tiendas) → días desde ingreso e «ingreso vencido» (informativo).
    df['fecha_ultimo_ingreso'] = pd.NaT
    fecha_corte = pd.NaT
    inv_t = dfs.get('inventario_tiendas')
    if inv_t is not None:
        inv_t = inv_t.copy()
        inv_t['store_code'] = inv_t['C.O. bodega'].astype(str).str.strip()
        inv_t['item_code'] = _normalize_item_series(inv_t['Item'])
        inv_t = inv_t.dropna(subset=['item_code'])
        inv_t['item_code'] = inv_t['item_code'].astype(int)
        inv_t['_f'] = pd.to_datetime(inv_t['Fecha última entrada'], errors='coerce')
        fecha_corte = inv_t['_f'].max()
        ult = inv_t.groupby(['store_code', 'item_code'])['_f'].max().rename('_ult').reset_index()
        df = df.merge(ult, on=['store_code', 'item_code'], how='left')
        df['fecha_ultimo_ingreso'] = df['_ult']
        df = df.drop(columns='_ult')
    df['fecha_ultimo_ingreso'] = pd.to_datetime(df['fecha_ultimo_ingreso'])
    df['dias_desde_ingreso'] = (fecha_corte - df['fecha_ultimo_ingreso']).dt.days.astype(float)
    df['ingreso_vencido'] = (
        df['vida_util'].notna() & df['dias_desde_ingreso'].notna()
        & (df['dias_desde_ingreso'] > df['vida_util']) & (df['existencias_celes'] > 0)
    )
    df['existencias'] = np.where(df['ingreso_vencido'] & params.descontar_inventario_vencido,
                                 0.0, df['existencias_celes'])

    # Demanda y objetivo a la llegada de la compra.
    df['crecimiento'] = df['item_code'].map(params.crecimiento_item).astype(float)
    df['consumo_ajustado'] = df['consumo_diario'] * (1 + df['crecimiento'])
    df['venta_hasta_llegada'] = df['consumo_ajustado'] * plan.peso_hasta_llegada
    df['inv_bruto'] = df['existencias'] + df['transito'] + df['pedido_hoy']
    df['min_exhibicion'] =df['min_archivo'].fillna(params.min_exhibicion_default).clip(lower=0)
    if not params.exhibir_sin_venta:
        df.loc[df['consumo_diario'] <= 0, 'min_exhibicion'] = 0.0
    regimen = _regimen_compra(items, plan, params, alertas)
    items = items.merge(regimen, on='item_code', how='left')
    df = df.merge(regimen[['item_code', 'regimen', 'peso_cobertura_item', 'n_dias_cobertura_item',
                           'peso_periodo', 'n_dias_periodo']], on='item_code', how='left')
    (df['venta_cobertura'], df['stock_seguridad'], df['objetivo_demanda'], df['objetivo'],
     df['tope_max']) = _objetivos(df, df['peso_cobertura_item'], df['n_dias_cobertura_item'], plan, params)
    # CADA N DIAS: objetivo alternativo si hoy se activa la compra (N días de cobertura).
    per = df['regimen'] == 'cada'
    for col in ('venta_cobertura_periodo', 'stock_seguridad_periodo', 'objetivo_periodo'):
        df[col] = np.nan
    if per.any():
        v, ss, _, obj, _ = _objetivos(df[per], df.loc[per, 'peso_periodo'], df.loc[per, 'n_dias_periodo'], plan, params)
        df.loc[per, 'venta_cobertura_periodo'] = v
        df.loc[per, 'stock_seguridad_periodo'] = ss
        df.loc[per, 'objetivo_periodo'] = obj

    # Nivel ítem: stock CEDI que la distribución de hoy no repartió, OC y ajustes por ítem.
    stock = _stock_cedi(dfs)
    items = items.merge(stock, on='item_code', how='left')
    items['cedi_stock'] = _num(items['cedi_stock'])
    items['cedi_distribuido_hoy'] = _num(items['item_code'].map(distribuido))
    items['cedi_remanente'] = (items['cedi_stock'] - items['cedi_distribuido_hoy']).clip(lower=0)
    if df_distribucion is not None and len(df_distribucion) > 0:
        # Con distribución, el stock de hoy ya salió (cero residuo). Un ítem con stock que la distribución
        # no repartió es porque no tenía tienda elegible: ese stock no llegará a ninguna tienda y no cuenta.
        items['cedi_remanente'] = items['cedi_remanente'].where(items['item_code'].isin(distribuido.index), 0)
    oc, oc_detalle = _clasificar_oc(compras_transito, plan, activos, dfs.get('stock'), params, alertas)
    items = items.merge(oc, on='item_code', how='left')
    items['oc_transito'] = _num(items['oc_transito'])
    items['oc_posterior'] = _num(items['oc_posterior'])
    # Llegada a tienda del stock en camino (OC + CEDI sin repartir, que sale hoy): la más temprana.
    sale_hoy = plan.fecha_datos + timedelta(days=int(params.lt_cedi_tienda))
    llegadas = [d if pd.notna(d) else None for d in items['oc_llegada_tienda']]
    items['pool_llegada_tienda'] = [
        min([x for x in (oc_d if oc_t > 0 else None, sale_hoy if rem > 0 else None) if x is not None], default=None)
        for oc_d, oc_t, rem in zip(llegadas, items['oc_transito'], items['cedi_remanente'])
    ]
    items['costo'] = items['costo_stock'].fillna(items['costo_portafolio'].where(items['costo_portafolio'] > 0))
    items['um'] = items['um_portafolio'].where(items['um_portafolio'].astype(str).str.strip() != '',
                                               items['um_stock']).fillna(items['um_stock'])
    items['multiplo'] = items['item_code'].map(params.multiplo_item).astype(int)
    items['minimo'] = items['item_code'].map(params.minimo_item).astype(int)
    conteo = df.groupby('item_code').agg(n_tiendas=('store_code', 'nunique'),
                                         n_con_datos=('con_datos', 'sum'),
                                         suma_min=('min_exhibicion', 'sum'))
    items = items.merge(conteo, on='item_code', how='left')
    for col in ('n_tiendas', 'n_con_datos', 'suma_min'):
        items[col] = _num(items[col])
    df = df.merge(items[['item_code', 'costo', 'um']], on='item_code', how='left')

    # ── Alertas resumen ──────────────────────────────────────────────────────
    sin_celes = items[~items['en_celes']]
    if len(sin_celes) > 0:
        alertas.append({
            'tipo': 'ADVERTENCIA', 'item': '-',
            'mensaje': f'{len(sin_celes)} ítem(s) ACTIVOS en Portafolio no tienen datos en Celes (sin consumo '
                       f'ni inventario): no se compran automáticamente ({", ".join(str(i) for i in sin_celes["item_code"][:12])}'
                       f'{"…" if len(sin_celes) > 12 else ""}). Ver hoja «Sin Pronóstico».',
            'items_sin_celes': [int(i) for i in sin_celes['item_code']],
        })
    combos = df[df['sin_match_celes'] & df['item_code'].isin(items_en_celes)]
    if len(combos) > 0:
        alertas.append({
            'tipo': 'ADVERTENCIA', 'item': '-',
            'mensaje': f'{len(combos)} tienda×ítem activas sin fila en Celes ({combos["store_code"].nunique()} '
                       'tienda(s)): no se les compra porque falta el consumo y el inventario. Ver «Sin Pronóstico».',
        })
    sin_tiendas = items[items['n_tiendas'] == 0]
    if len(sin_tiendas) > 0:
        alertas.append({
            'tipo': 'ADVERTENCIA', 'item': '-',
            'mensaje': f'{len(sin_tiendas)} ítem(s) activos en Portafolio sin ninguna tienda activa en '
                       f'Tiendas × Ítem ({", ".join(str(i) for i in sin_tiendas["item_code"][:12])}): no se compran.',
        })
    stock_no_activo = stock[(stock['cedi_stock'] > 0) & ~stock['item_code'].isin(activos)]
    if len(stock_no_activo) > 0:
        alertas.append({
            'tipo': 'ADVERTENCIA', 'item': '-',
            'mensaje': f'{len(stock_no_activo)} ítem(s) con stock en CEDI que no están ACTIVOS en Portafolio '
                       f'({", ".join(str(i) for i in stock_no_activo["item_code"][:12])}): se distribuyen, '
                       'pero no se compran.',
        })
    n_tope = int((df['tope_max'] & df['con_datos']).sum())
    if n_tope > 0:
        alertas.append({
            'tipo': 'ADVERTENCIA', 'item': '-',
            'mensaje': f'{n_tope} tienda×ítem con objetivo de compra por encima de su MAX de Tiendas × Ítem: '
                       'se compró hasta el MAX (revisar si el MIN de exhibición es mayor que el MAX).',
        })
    sin_vu = items[items['vida_util'].isna() & (items['n_tiendas'] > 0)]
    if len(sin_vu) > 0:
        alertas.append({
            'tipo': 'ADVERTENCIA', 'item': '-',
            'mensaje': f'{len(sin_vu)} ítem(s) activos sin VIDA UTIL EXHIBICION en Portafolio: la compra no se '
                       f'limita por vida útil ({", ".join(str(i) for i in sin_vu["item_code"][:12])}).',
        })
    if params.descontar_inventario_vencido and df['ingreso_vencido'].any():
        alertas.append({
            'tipo': 'ADVERTENCIA', 'item': '-',
            'mensaje': f'DESCONTAR_INVENTARIO_VENCIDO activo: {int(df["ingreso_vencido"].sum())} tienda×ítem con '
                       'último ingreso más viejo que la vida útil se tomaron con existencias 0.',
        })

    return df.reset_index(drop=True), items.reset_index(drop=True), alertas, oc_detalle

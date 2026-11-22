"""Excel de la distribución (`distribucion_fruver_YYYYMMDD.xlsx`): Distribución, Resumen, Resumen Ítems,
Novedades (sin datos Celes, bloqueadas por MAX, renovación) y Alertas.
"""

import io

import numpy as np
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .algorithm import TARGET_DAYS, UMBRAL_VENTA_RENOVACION

HEADER_FILL = PatternFill("solid", fgColor="2D6A4F")
HEADER_FONT = Font(color="FFFFFF", bold=True)
HEADER_ALIGN = Alignment(horizontal='center')

# Colores suaves: una situación por color en «Novedades» y un nivel por color en «Alertas».
_FILL_SITUACION = {
    'Sin datos Celes':      PatternFill("solid", fgColor="E7E6E6"),  # gris
    'Bloqueada por MAX':    PatternFill("solid", fgColor="FFE699"),  # amarillo
    'Renovación vida útil': PatternFill("solid", fgColor="BDD7EE"),  # azul
}
_FILL_NIVEL = {
    'Crítico':     PatternFill("solid", fgColor="F4A6A6"),  # rojo claro
    'Advertencia': PatternFill("solid", fgColor="FBE0A6"),  # naranja claro
    'OK':          PatternFill("solid", fgColor="D4EDDA"),  # verde claro
}
_NIVEL_ALERTA = {'CRÍTICO': 'Crítico', 'ERROR': 'Crítico', 'ADVERTENCIA': 'Advertencia', 'OK': 'OK'}

_ORDEN_TIPO_ALERTA = {'CRÍTICO': 0, 'ERROR': 0, 'ADVERTENCIA': 1}

_COL_TIENDA = ['Tienda', 'Nombre Tienda', 'Código Ítem', 'Nombre Ítem']


def _style_header(ws, row=1):
    for cell in ws[row]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = HEADER_ALIGN


def _autofit(ws):
    for col in ws.columns:
        max_len = max((len(str(cell.value or '')) for cell in col), default=0) + 2
        ws.column_dimensions[col[0].column_letter].width = min(max_len, 50)


def _tabla(ws, header_row=1):
    """Encabezado fijo y filtros en cada columna: más fácil de recorrer y filtrar."""
    ws.freeze_panes = f'A{header_row + 1}'
    ws.auto_filter.ref = f'A{header_row}:{get_column_letter(ws.max_column)}{max(ws.max_row, header_row)}'


def _cajas_forzadas_por_combo(df_output):
    """{(store_code, item_code): cajas}: lo que la Fase 5 (nivel 2) terminó dándole a una
    combinación excluida cuando no quedaba otro destino (cero residuo manda)."""
    if df_output is None or len(df_output) == 0:
        return {}
    return {
        (str(row.store_code), row.item_code): int(row.cajas_asignadas)
        for row in df_output.itertuples()
    }


def _base_tienda_item(df, forzadas=None):
    """Columnas comunes Tienda/Ítem de las hojas de apoyo (+ cajas forzadas si se pide)."""
    out = pd.DataFrame({
        'Tienda':        df['store_code'],
        'Nombre Tienda': df['store_name'],
        'Código Ítem':   df['item_code'],
        'Nombre Ítem':   df['item_desc'],
    })
    if forzadas is not None:
        out['_forzadas'] = [forzadas.get((str(s), i), 0) for s, i in zip(df['store_code'], df['item_code'])]
    return out


def _build_sin_datos_celes_sheet(df_merged, forzadas):
    """Fuente de «Novedades» (Sin datos Celes): tienda×ítem con portafolio ACTIVO pero sin fila en Celes. Sin
    consumo/inventario no se sabe si necesitan mercancía, así que no se les envía (salvo cero
    residuo: ver `Cajas Forzadas`, que en «Novedades» es `Cajas Enviadas`). `Causa`: falta la tienda, el ítem o solo ese par."""
    cols = _COL_TIENDA + ['Causa', 'Cajas Forzadas']
    if 'sin_match_celes' not in df_merged.columns:
        return pd.DataFrame(columns=cols)
    sm = df_merged[df_merged['sin_match_celes'].fillna(False)]
    if len(sm) == 0:
        return pd.DataFrame(columns=cols)

    out = _base_tienda_item(sm, forzadas)
    out['Causa'] = sm['causa_sin_match'] if 'causa_sin_match' in sm.columns else 'Sin match en Celes'
    out['Cajas Forzadas'] = out.pop('_forzadas')
    return out[cols].sort_values(['Código Ítem', 'Tienda']).reset_index(drop=True)


def _build_bloqueadas_max_sheet(df_merged, forzadas):
    """Fuente de «Novedades» (Bloqueada por MAX): tienda×ítem cuyas Existencias + Tránsito ya igualan o superan
    su MAX, por lo que no reciben (salvo cero residuo). Basta ver que `Existencias + Tránsito`
    sea >= `MAX`."""
    cols = _COL_TIENDA + ['Existencias', 'Tránsito', 'Existencias + Tránsito', 'MAX', 'Cajas Forzadas']
    if 'bloqueado_por_max' not in df_merged.columns:
        return pd.DataFrame(columns=cols)
    bl = df_merged[df_merged['bloqueado_por_max'].fillna(False)]
    if len(bl) == 0:
        return pd.DataFrame(columns=cols)

    out = _base_tienda_item(bl, forzadas)
    out['Existencias'] = bl['inventario_tienda'].round(2)
    out['Tránsito'] = bl['inventario_transito'].round(2)
    out['Existencias + Tránsito'] = (bl['inventario_tienda'] + bl['inventario_transito']).round(2)
    out['MAX'] = bl['max_existencias'].round(2)
    out['Cajas Forzadas'] = out.pop('_forzadas')
    return out[cols].sort_values(['Código Ítem', 'Tienda']).reset_index(drop=True)


_RENOVACION_COLS = _COL_TIENDA + [
    'Último Ingreso', 'Días Desde Ingreso', 'Vida Útil (días)', 'Existencias',
    'Venta Esperada (cj)', 'Cajas Enviadas', 'Motivo',
]


def _build_renovacion_sheet(df_merged, df_output):
    """Fuente de «Novedades» (Renovación vida útil): tienda×ítem con ingreso más viejo que la vida útil y aún con
    inventario (probable producto vencido sin baja). Lista todos los candidatos y explica en
    `Motivo` por qué recibieron (Fase 3) o no la caja; las de baja venta esperada son candidatas
    a baja de inventario o revisión de surtido. El motivo se evalúa por tienda × grupo espejo."""
    requeridas = ('vida_util_portafolio', 'dias_desde_ingreso', 'fecha_ultimo_ingreso')
    if not all(c in df_merged.columns for c in requeridas):
        return pd.DataFrame(columns=_RENOVACION_COLS)

    fases = ['cajas_fase1', 'cajas_fase2', 'cajas_fase3']
    if df_output is not None and len(df_output) > 0:
        out = df_output.reindex(columns=['store_code', 'item_code'] + fases, fill_value=0)
        out = out.groupby(['store_code', 'item_code'], as_index=False)[fases].sum()
    else:
        out = pd.DataFrame(columns=['store_code', 'item_code'] + fases)
    base = df_merged.drop(columns=[c for c in fases if c in df_merged.columns])
    m = base.merge(out, on=['store_code', 'item_code'], how='left')
    m[fases] = m[fases].fillna(0)

    por_grupo = [m['store_code'], m['grupo_id']]
    m['_f12_g'] = (m['cajas_fase1'] + m['cajas_fase2']).groupby(por_grupo).transform('sum')
    m['_f3_g'] = m['cajas_fase3'].groupby(por_grupo).transform('sum')
    m['_tr_g'] = m['inventario_transito'].groupby(por_grupo).transform('sum')

    mask = (
        (m['vida_util_portafolio'] > 0)
        & (m['dias_desde_ingreso'] > m['vida_util_portafolio'])
        & (m['inventario_tienda'] > 0)
    )
    c = m[mask].copy()
    if len(c) == 0:
        return pd.DataFrame(columns=_RENOVACION_COLS)

    def _motivo(r):
        if r['cajas_fase3'] > 0:
            return 'Renovada'
        if r['_f3_g'] > 0:
            return 'Renovada con otro SKU espejo'
        if bool(r['excluida_del_reparto']):
            return 'Excluida (MAX / sin datos Celes)'
        if bool(r['grupo_con_ingreso_fresco']):
            return 'SKU espejo con ingreso fresco'
        if r['_f12_g'] > 0:
            return 'Ya recibió en Fase 1/2'
        if r['_tr_g'] > 0:
            return 'Tiene tránsito'
        if r['venta_esperada_vida_util'] < UMBRAL_VENTA_RENOVACION:
            return f"Venta esperada < {UMBRAL_VENTA_RENOVACION:g} caja"
        return 'Sin stock en CEDI'

    res = _base_tienda_item(c)
    res['Último Ingreso'] = c['fecha_ultimo_ingreso'].dt.strftime('%Y-%m-%d')
    res['Días Desde Ingreso'] = c['dias_desde_ingreso'].astype(int)
    res['Vida Útil (días)'] = c['vida_util_portafolio'].round(0).astype(int)
    res['Existencias'] = c['inventario_tienda'].round(2)
    res['Venta Esperada (cj)'] = c['venta_esperada_vida_util'].round(2)
    res['Cajas Enviadas'] = c['cajas_fase3'].astype(int)
    res['Motivo'] = c.apply(_motivo, axis=1)
    return res[_RENOVACION_COLS].sort_values(
        ['Cajas Enviadas', 'Código Ítem', 'Tienda'], ascending=[False, True, True]
    ).reset_index(drop=True)


def resumen_renovacion(df_merged, df_output):
    """{motivo: cantidad} de las tienda×ítem candidatas a renovación por vida útil (las mismas
    filas de «Novedades» con situación «Renovación vida útil»). Sirve para dejar a la vista, por ejemplo, cuántas
    se omitieron porque ya tienen mercancía en tránsito. Vacío si no hay candidatas."""
    hoja = _build_renovacion_sheet(df_merged, df_output)
    if len(hoja) == 0:
        return {}
    return hoja['Motivo'].value_counts().to_dict()


def resumen_renovacion_texto(df_merged, df_output):
    """Una línea legible con el resultado de la Fase 3 ('' si no hubo candidatas), p. ej.:
    «104 renovadas · 89 omitidas por tránsito · 392 sin stock en CEDI · 30 otras»."""
    r = resumen_renovacion(df_merged, df_output)
    if not r:
        return ''
    renovadas = r.get('Renovada', 0) + r.get('Renovada con otro SKU espejo', 0)
    transito = r.get('Tiene tránsito', 0)
    sin_stock = r.get('Sin stock en CEDI', 0)
    otras = sum(r.values()) - renovadas - transito - sin_stock
    return (f'{renovadas} renovadas · {transito} omitidas por tránsito · '
            f'{sin_stock} sin stock en CEDI · {otras} otras (ver hoja «Novedades»)')


_NOVEDADES_COLS = ['Situación'] + _COL_TIENDA + ['Detalle', 'Cajas Enviadas']


def _n(x):
    """Número corto y legible: 4.0 → '4', 4.25 → '4.25'."""
    return f'{float(x):.2f}'.rstrip('0').rstrip('.')


def _build_novedades_sheet(df_merged, df_output):
    """Hoja "Novedades": las tres listas de tienda×ítem en una sola tabla.

    Situación → Sin datos Celes (no hay consumo/inventario: no se les envía), Bloqueada por MAX
    (Existencias + Tránsito ya igualan su MAX) y Renovación vida útil (ingreso más viejo que la vida
    útil). `Detalle` explica el caso en una frase; `Cajas Enviadas` es lo que terminó recibiendo
    (Fase 3 en renovación; en las otras dos, solo si el cero residuo no tuvo otro destino).
    """
    forzadas = _cajas_forzadas_por_combo(df_output)
    partes = []

    sd = _build_sin_datos_celes_sheet(df_merged, forzadas)
    if len(sd):
        p = sd[_COL_TIENDA].copy()
        p.insert(0, 'Situación', 'Sin datos Celes')
        p['Detalle'] = sd['Causa']
        p['Cajas Enviadas'] = sd['Cajas Forzadas']
        partes.append(p)

    bm = _build_bloqueadas_max_sheet(df_merged, forzadas)
    if len(bm):
        p = bm[_COL_TIENDA].copy()
        p.insert(0, 'Situación', 'Bloqueada por MAX')
        p['Detalle'] = [f'Existencias {_n(e)} + tránsito {_n(t)} = {_n(et)} ≥ MAX {_n(m)}'
                        for e, t, et, m in zip(bm['Existencias'], bm['Tránsito'],
                                               bm['Existencias + Tránsito'], bm['MAX'])]
        p['Cajas Enviadas'] = bm['Cajas Forzadas']
        partes.append(p)

    rv = _build_renovacion_sheet(df_merged, df_output)
    if len(rv):
        p = rv[_COL_TIENDA].copy()
        p.insert(0, 'Situación', 'Renovación vida útil')
        p['Detalle'] = [f'Último ingreso {f} ({d} d) · vida útil {v} d · {mot}'
                        for f, d, v, mot in zip(rv['Último Ingreso'], rv['Días Desde Ingreso'],
                                                rv['Vida Útil (días)'], rv['Motivo'])]
        p['Cajas Enviadas'] = rv['Cajas Enviadas']
        partes.append(p)

    if not partes:
        return pd.DataFrame(columns=_NOVEDADES_COLS)
    out = pd.concat(partes, ignore_index=True)
    orden = {k: i for i, k in enumerate(_FILL_SITUACION)}
    out['_k'] = out['Situación'].map(orden)
    out = out.sort_values(['_k', 'Código Ítem', 'Tienda'], kind='stable').drop(columns='_k')
    out['Cajas Enviadas'] = out['Cajas Enviadas'].astype(int)
    return out[_NOVEDADES_COLS].reset_index(drop=True)


def _build_alertas_sheet(alertas):
    """Hoja "Alertas": Nivel · Ítem · Qué pasó, lo crítico primero. Los datos estructurados extra de
    cada alerta (conteos, listas) quedan en la lista de alertas, no en el Excel: ya van en el mensaje."""
    if not alertas:
        return pd.DataFrame([{'Nivel': 'OK', 'Ítem': '-', 'Qué pasó': 'Todo en orden: no hay alertas.'}])

    out = pd.DataFrame(alertas)[['tipo', 'item', 'mensaje']]
    out['_orden'] = out['tipo'].map(_ORDEN_TIPO_ALERTA).fillna(2)
    out = out.sort_values('_orden', kind='stable').drop(columns=['_orden']).reset_index(drop=True)
    out['tipo'] = out['tipo'].map(lambda t: _NIVEL_ALERTA.get(str(t).upper(), str(t).capitalize()))
    out.columns = ['Nivel', 'Ítem', 'Qué pasó']
    return out


def _resumen_items(d):
    cols = ['Código Ítem', 'Nombre Ítem', 'Total Cajas', 'F1 Urgentes', f'F2 Cobertura {TARGET_DAYS:g}d',
            'F3 Renovación', 'F4 Excedente']
    if d is None:
        return pd.DataFrame(columns=cols)
    r = (
        d.groupby('item_code', as_index=False)
        .agg(nombre=('item_desc', 'first'), total=('cajas_asignadas', 'sum'),
             f1=('cajas_fase1', 'sum'), f2=('cajas_fase2', 'sum'), f3=('cajas_fase3', 'sum'),
             f4=('cajas_fase4', 'sum'))
        .sort_values('item_code')
    )
    r.columns = cols
    return r


def generate_excel(df_output, alertas=None, df_merged=None):
    """Genera el Excel de distribución y lo retorna como bytes.

    df_merged (opcional): DataFrame pre-algoritmo con TODAS las tiendas elegibles; habilita la hoja
    'Novedades' (necesita las filas que no recibieron cajas, que no están en df_output).
    """
    alertas = alertas or []
    has_data = df_output is not None and len(df_output) > 0

    if has_data:
        d = df_output.copy()
        for col in ('cajas_fase1', 'cajas_fase2', 'cajas_fase3', 'cajas_fase4'):
            if col not in d.columns:
                d[col] = 0
        d['_stock_despues'] = d['inventario_efectivo'] + d['cajas_asignadas']
        d['_dias_despues'] = np.where(
            d['consumo_diario'] > 0,
            (d['_stock_despues'] / d['consumo_diario']).round(1),
            np.nan,
        )
    else:
        d = None

    # ── Distribución ───────────────────────────────────────────────────────────
    if not has_data:
        dist_df = pd.DataFrame(columns=[
            'Centro Operacional de la Bodega', 'Nombre Tienda', 'Zona',
            'Código de Producto', 'Nombre Ítem', 'UM',
            'Consumo Diario', 'Stock Antes Pedido', 'Días Inventario Actual',
            'Pedido Final', 'Stock Después Pedido', 'Días Inventario Proyectado',
        ])
    else:
        dist_df = pd.DataFrame({
            'Centro Operacional de la Bodega': d['store_code'],
            'Nombre Tienda':                   d['store_name'],
            'Zona':                            d['zona'],
            'Código de Producto':              d['item_code'],
            'Nombre Ítem':                     d['item_desc'],
            'UM':                              d['um'],
            'Consumo Diario':                  d['consumo_diario'].round(2),
            'Stock Antes Pedido':              d['inventario_efectivo'].round(2),
            # dias_proyectados puede ser inf (consumo 0): se sanitiza a NaN (celda vacía).
            'Días Inventario Actual':          d['dias_proyectados'].replace([np.inf, -np.inf], np.nan).round(1),
            'Pedido Final':                    d['cajas_asignadas'].astype(int),
            'Stock Después Pedido':            d['_stock_despues'].round(2),
            'Días Inventario Proyectado':      d['_dias_despues'],
        }).sort_values(['Centro Operacional de la Bodega', 'Código de Producto'])

    # ── Resumen ────────────────────────────────────────────────────────────────
    if len(dist_df) > 0:
        resumen_df = (
            dist_df.groupby(['Centro Operacional de la Bodega', 'Nombre Tienda', 'Zona'], as_index=False)['Pedido Final']
            .sum()
            .rename(columns={'Pedido Final': 'Total Cajas'})
            .sort_values('Total Cajas', ascending=False)
        )
    else:
        resumen_df = pd.DataFrame(columns=['Centro Operacional de la Bodega', 'Nombre Tienda', 'Zona', 'Total Cajas'])

    resumitem_df = _resumen_items(d)

    # ── Novedades (desde df_merged) ────────────────────────────────────────────
    novedades_out = _build_novedades_sheet(df_merged, df_output) if df_merged is not None else None

    alertas_out = _build_alertas_sheet(alertas)

    # ── Escritura ──────────────────────────────────────────────────────────────
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        dist_df.to_excel(writer, index=False, sheet_name='Distribución')
        resumen_df.to_excel(writer, index=False, sheet_name='Resumen')
        # Una línea de leyenda arriba (fila 1) y la tabla desde la fila 3.
        resumitem_df.to_excel(writer, index=False, sheet_name='Resumen Ítems', startrow=2)
        if novedades_out is not None:
            novedades_out.to_excel(writer, index=False, sheet_name='Novedades')
        alertas_out.to_excel(writer, index=False, sheet_name='Alertas')

        ws_ri = writer.sheets['Resumen Ítems']
        ws_ri.cell(row=1, column=1, value=(
            f'F1 Urgentes (agotadas / seguridad) · F2 Cobertura a {TARGET_DAYS:g} días · F3 Renovación por vida útil · '
            'F4 Excedente voluntario.'
        ))

        for ws in writer.book.worksheets:
            if ws.title == 'Resumen Ítems':
                _style_header(ws, row=3)
                _tabla(ws, header_row=3)
                _autofit(ws)
                ws.column_dimensions['A'].width = 12   # la leyenda de la fila 1 no define el ancho
                continue
            _style_header(ws)
            _tabla(ws)
            _autofit(ws)
        if 'Novedades' in writer.sheets:
            ws = writer.sheets['Novedades']
            for fila in range(2, ws.max_row + 1):
                celda = ws.cell(row=fila, column=1)
                celda.fill = _FILL_SITUACION.get(celda.value, celda.fill)
            ws.column_dimensions['F'].width = 70   # Detalle
            ws.column_dimensions['A'].width = 22
        ws = writer.sheets['Alertas']
        ws.column_dimensions['A'].width = 14
        ws.column_dimensions['B'].width = 10
        ws.column_dimensions['C'].width = 100
        for fila in range(2, ws.max_row + 1):
            nivel = ws.cell(row=fila, column=1)
            nivel.fill = _FILL_NIVEL.get(nivel.value, nivel.fill)
            nivel.alignment = Alignment(horizontal='center', vertical='top')
            ws.cell(row=fila, column=3).alignment = Alignment(wrap_text=True, vertical='top')

    return buffer.getvalue()

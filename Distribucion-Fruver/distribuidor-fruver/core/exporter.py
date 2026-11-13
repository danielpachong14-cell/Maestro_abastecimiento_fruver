"""Genera el Excel de salida con la distribución.

Hojas (las dos primeras son el entregable; el resto es análisis de apoyo):
  Distribución · Resumen          el pedido por tienda×ítem y el total por tienda.
  Resumen Ítems                   cajas por ítem y por fase (F1…F5) + % forzado.
  Análisis Comprador              por ítem: CEDI vs. necesidad a 3 días, riesgo y recomendación.
  Sin Datos Celes                 tienda×ítem sin datos en Celes (no se les envía) y la causa.
  Bloqueadas por MAX              tienda×ítem cuyo inventario ya iguala o supera su MAX.
  Renovación Vida Útil            tienda×ítem con ingreso más viejo que la vida útil, con su motivo.
  Alertas                         casos borde del procesamiento ("OK — Sin alertas" si no hubo).

Las hojas de análisis se construyen desde `df_merged` (universo pre-algoritmo), porque
`df_output` solo trae filas con pedido; solo se generan si se pasó `df_merged`.
Convención de columnas en las hojas de apoyo: Tienda · Nombre Tienda · Código Ítem · Nombre Ítem.
"""

import io

import numpy as np
import pandas as pd
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .algorithm import TARGET_DAYS, UMBRAL_VENTA_RENOVACION

# Umbral de riesgo de sobre-stock de "Análisis Comprador": solo de reporte, independiente
# del TOPE_EXCEDENTE del algoritmo (no cambia ni una caja de lo que se despacha).
UMBRAL_RIESGO_SOBRESTOCK = 10

HEADER_FILL = PatternFill("solid", fgColor="2D6A4F")
HEADER_FONT = Font(color="FFFFFF", bold=True)
HEADER_ALIGN = Alignment(horizontal='center')

# Semáforo de los % de riesgo (mismos cortes que 'Nivel de Riesgo').
_FILL_ALTO  = PatternFill("solid", fgColor="F4A6A6")  # rojo claro   (>= 50 %)
_FILL_MEDIO = PatternFill("solid", fgColor="FBE0A6")  # naranja claro (>= 20 %)
_FILL_BAJO  = PatternFill("solid", fgColor="FFF6B3")  # amarillo claro (> 0 %)
_FILL_OK    = PatternFill("solid", fgColor="D4EDDA")  # verde claro   (0 %)

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


def _fill_riesgo(pct):
    if pct >= 50:
        return _FILL_ALTO
    if pct >= 20:
        return _FILL_MEDIO
    return _FILL_BAJO if pct > 0 else _FILL_OK


def _resaltar_riesgo(ws, comprador_out):
    """Colorea los dos % de riesgo por severidad y deja un comentario corto en cada encabezado."""
    notas = {
        '% Tiendas Riesgo Merma': 'Tiendas que quedan por encima de min(10, VIDA UTIL EXHIBICION) días tras el pedido.',
        f'% Tiendas Sobrestock (>{UMBRAL_RIESGO_SOBRESTOCK}d)':
            f'Tiendas con más de {UMBRAL_RIESGO_SOBRESTOCK} días tras el pedido. Solo informativo: no cambia '
            'las cajas despachadas.',
    }
    for nombre, nota in notas.items():
        col = comprador_out.columns.get_loc(nombre) + 1
        for row_idx, pct in enumerate(comprador_out[nombre], start=2):
            ws.cell(row=row_idx, column=col).fill = _fill_riesgo(pct)
        comentario = Comment(nota + '\nVerde 0 % · Amarillo >0 · Naranja ≥20 % · Rojo ≥50 %.', 'Distribuidor Fruver')
        comentario.width, comentario.height = 300, 90
        ws.cell(row=1, column=col).comment = comentario


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
    """Hoja "Sin Datos Celes": tienda×ítem con portafolio ACTIVO pero sin fila en Celes. Sin
    consumo/inventario no se sabe si necesitan mercancía, así que no se les envía (salvo cero
    residuo: ver `Cajas Forzadas`). `Causa`: falta la tienda, el ítem o solo ese par."""
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
    """Hoja "Bloqueadas por MAX": tienda×ítem cuyas Existencias + Tránsito ya igualan o superan
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
    """Hoja "Renovación Vida Útil": tienda×ítem con ingreso más viejo que la vida útil y aún con
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
    filas de la hoja «Renovación Vida Útil»). Sirve para dejar a la vista, por ejemplo, cuántas
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
            f'{sin_stock} sin stock en CEDI · {otras} otras (ver hoja «Renovación Vida Útil»)')


def _build_alertas_sheet(alertas):
    """Hoja "Alertas": Tipo · Ítem · Mensaje, CRÍTICO primero. Los datos estructurados extra de
    cada alerta (conteos, listas) quedan en la lista de alertas, no en el Excel: ya van en el mensaje."""
    if not alertas:
        return pd.DataFrame([{'Tipo': 'OK', 'Ítem': '-', 'Mensaje': 'Sin alertas'}])

    out = pd.DataFrame(alertas)[['tipo', 'item', 'mensaje']]
    out['_orden'] = out['tipo'].map(_ORDEN_TIPO_ALERTA).fillna(2)
    out = out.sort_values('_orden', kind='stable').drop(columns=['_orden']).reset_index(drop=True)
    out.columns = ['Tipo', 'Ítem', 'Mensaje']
    return out


def _resumen_items(d):
    cols = ['Código Ítem', 'Nombre Ítem', 'Total Cajas', 'F1 Urgentes', f'F2 Cobertura {TARGET_DAYS:g}d',
            'F3 Renovación', 'F4 Excedente', 'F5 Forzado', '% Forzado']
    if d is None:
        return pd.DataFrame(columns=cols)
    r = (
        d.groupby('item_code', as_index=False)
        .agg(nombre=('item_desc', 'first'), total=('cajas_asignadas', 'sum'),
             f1=('cajas_fase1', 'sum'), f2=('cajas_fase2', 'sum'), f3=('cajas_fase3', 'sum'),
             f4=('cajas_fase4', 'sum'), f5=('cajas_fase5', 'sum'))
        .sort_values('item_code')
    )
    r['pct'] = (r['f5'] / r['total'] * 100).round(0)
    r.columns = cols
    return r


def generate_excel(df_output, alertas=None, df_merged=None):
    """Genera el Excel de distribución y lo retorna como bytes.

    df_merged (opcional): DataFrame pre-algoritmo con TODAS las tiendas elegibles; habilita
    'Análisis Comprador', 'Sin Datos Celes', 'Bloqueadas por MAX' y 'Renovación Vida Útil'
    (necesitan las filas que no recibieron cajas, que no están en df_output).
    """
    alertas = alertas or []
    has_data = df_output is not None and len(df_output) > 0

    if has_data:
        d = df_output.copy()
        for col in ('cajas_fase1', 'cajas_fase2', 'cajas_fase3', 'cajas_fase4', 'cajas_fase5', 'vida_util'):
            if col not in d.columns:
                d[col] = 0
        d['_stock_despues'] = d['inventario_efectivo'] + d['cajas_asignadas']
        d['_dias_despues'] = np.where(
            d['consumo_diario'] > 0,
            (d['_stock_despues'] / d['consumo_diario']).round(1),
            np.nan,
        )
        # Umbral de riesgo de merma: min(10, vida_util) si vida_util > 0; si no, 10.
        # La vida útil sale del Portafolio (VIDA UTIL EXHIBICION); si el ítem no la tiene se
        # usa la de Celes como respaldo.
        vu_portafolio = (pd.to_numeric(d['vida_util_portafolio'], errors='coerce')
                         if 'vida_util_portafolio' in d.columns else pd.Series(np.nan, index=d.index))
        vu_celes = pd.to_numeric(d['vida_util'], errors='coerce')
        vida_util_num = vu_portafolio.where(vu_portafolio > 0, vu_celes)
        d['_umbral'] = np.where(vida_util_num > 0, vida_util_num.clip(upper=10.0), 10.0)
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

    # ── Análisis Comprador ─────────────────────────────────────────────────────
    # CEDI vs. mínimo para cubrir TARGET_DAYS en todas las tiendas: el exceso es lo que el
    # comprador puede reducir sin afectar la cobertura mínima en tienda.
    col_sobre = f'% Tiendas Sobrestock (>{UMBRAL_RIESGO_SOBRESTOCK}d)'
    if has_data and df_merged is not None:
        mn = df_merged.copy()
        necesidad_min = (TARGET_DAYS * mn['consumo_diario'] - mn['inventario_efectivo']).clip(lower=0)
        mn['_need'] = np.where(mn['consumo_diario'] > 0, np.ceil(necesidad_min), 0).astype(int)
        item_base = mn.groupby('item_code', as_index=False).agg(
            item_desc       =('item_desc',              'first'),
            cajas_cedi      =('cajas_disponibles_cedi', 'first'),
            n_tiendas       =('store_code',             'count'),
            cajas_min_3dias =('_need',                  'sum'),
        )
        item_base['exceso_cedi'] = (item_base['cajas_cedi'] - item_base['cajas_min_3dias']).clip(lower=0)
        item_base['pct_exceso'] = np.where(
            item_base['cajas_cedi'] > 0,
            (item_base['exceso_cedi'] / item_base['cajas_cedi'] * 100).round(1),
            0.0,
        )

        # Tiendas en riesgo de merma (umbral por vida útil) y de sobrestock (umbral de reporte).
        d['_is_merma'] = (
            (d['_dias_despues'].notna() & (d['_dias_despues'] > d['_umbral']))
            | (d['_dias_despues'].isna() & (d['cajas_asignadas'] > 0))
        ).astype(int)
        d['_excede_tope'] = (
            (d['_dias_despues'].notna() & (d['_dias_despues'] > UMBRAL_RIESGO_SOBRESTOCK))
            | (d['_dias_despues'].isna() & (d['cajas_asignadas'] > 0))
        ).astype(int)
        item_riesgo = d.groupby('item_code', as_index=False).agg(
            n_tiendas_merma=('_is_merma', 'sum'), n_tiendas_tope=('_excede_tope', 'sum'),
        )

        cb = item_base.merge(item_riesgo, on='item_code', how='left')
        cb['n_tiendas_merma'] = cb['n_tiendas_merma'].fillna(0).astype(int)
        cb['n_tiendas_tope'] = cb['n_tiendas_tope'].fillna(0).astype(int)
        cb['pct_merma'] = (cb['n_tiendas_merma'] / cb['n_tiendas'] * 100).round(1)
        cb['pct_tope'] = (cb['n_tiendas_tope'] / cb['n_tiendas'] * 100).round(1)

        def _nivel(row):
            if row['exceso_cedi'] <= 0:
                return 'OK'
            if row['pct_merma'] >= 50:
                return 'ALTO'
            if row['pct_merma'] >= 20:
                return 'MEDIO'
            return 'BAJO' if row['n_tiendas_merma'] > 0 else 'SIN RIESGO MERMA'

        cb['Nivel de Riesgo'] = cb.apply(_nivel, axis=1)

        def _recomendar(row):
            nivel, exc, min3 = row['Nivel de Riesgo'], int(row['exceso_cedi']), int(row['cajas_min_3dias'])
            if nivel in ('ALTO', 'MEDIO'):
                return f'Reducir {exc} Cj — mín. necesario: {min3} Cj'
            if nivel in ('BAJO', 'SIN RIESGO MERMA'):
                return f'Evaluar reducir {exc} Cj (bajo riesgo de merma)'
            return 'Compra adecuada o insuficiente'

        comprador_out = pd.DataFrame({
            'Código Ítem':                cb['item_code'],
            'Nombre Ítem':                cb['item_desc'],
            'Cajas CEDI':                 cb['cajas_cedi'],
            'Tiendas Activas':            cb['n_tiendas'],
            f'Cajas Necesarias ({TARGET_DAYS:g} días)': cb['cajas_min_3dias'],
            'Exceso CEDI (Cj)':           cb['exceso_cedi'],
            '% Exceso':                   cb['pct_exceso'],
            '% Tiendas Riesgo Merma':     cb['pct_merma'],
            col_sobre:                    cb['pct_tope'],
            'Nivel de Riesgo':            cb['Nivel de Riesgo'],
            'Recomendación':              cb.apply(_recomendar, axis=1),
        })
        orden = {'ALTO': 0, 'MEDIO': 1, 'BAJO': 2, 'SIN RIESGO MERMA': 3, 'OK': 4}
        comprador_out = (
            comprador_out.assign(_k=comprador_out['Nivel de Riesgo'].map(orden))
            .sort_values(['_k', 'Exceso CEDI (Cj)'], ascending=[True, False])
            .drop(columns=['_k']).reset_index(drop=True)
        )
    else:
        comprador_out = None

    # ── Hojas de apoyo (desde df_merged) ───────────────────────────────────────
    if df_merged is not None:
        _forzadas = _cajas_forzadas_por_combo(df_output)
        sin_celes_out = _build_sin_datos_celes_sheet(df_merged, _forzadas)
        bloq_max_out = _build_bloqueadas_max_sheet(df_merged, _forzadas)
        renov_out = _build_renovacion_sheet(df_merged, df_output)
    else:
        sin_celes_out = bloq_max_out = renov_out = None

    alertas_out = _build_alertas_sheet(alertas)

    # ── Escritura ──────────────────────────────────────────────────────────────
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        dist_df.to_excel(writer, index=False, sheet_name='Distribución')
        resumen_df.to_excel(writer, index=False, sheet_name='Resumen')
        # Una línea de leyenda arriba (fila 1) y la tabla desde la fila 3.
        resumitem_df.to_excel(writer, index=False, sheet_name='Resumen Ítems', startrow=2)
        for nombre, df_hoja in (('Análisis Comprador', comprador_out), ('Sin Datos Celes', sin_celes_out),
                                ('Bloqueadas por MAX', bloq_max_out), ('Renovación Vida Útil', renov_out)):
            if df_hoja is not None:
                df_hoja.to_excel(writer, index=False, sheet_name=nombre)
        alertas_out.to_excel(writer, index=False, sheet_name='Alertas')

        ws_ri = writer.sheets['Resumen Ítems']
        ws_ri.cell(row=1, column=1, value=(
            f'F1 Urgentes (agotadas / seguridad) · F2 Cobertura a {TARGET_DAYS:g} días · F3 Renovación por vida útil · '
            'F4 Excedente voluntario · F5 Cero residuo forzado (un % alto indica sobre-compra del ítem).'
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
        if 'Análisis Comprador' in writer.sheets and comprador_out is not None:
            _resaltar_riesgo(writer.sheets['Análisis Comprador'], comprador_out)
        writer.sheets['Alertas'].column_dimensions['C'].width = 130

    return buffer.getvalue()

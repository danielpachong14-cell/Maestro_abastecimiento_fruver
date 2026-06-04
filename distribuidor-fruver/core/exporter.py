"""Genera el Excel de salida formateado con la distribución.

Hojas:
  - Distribución:   el pedido por tienda-ítem (lo que se ejecuta).
  - Resumen:        totales de cajas por tienda.
  - Resumen Ítems:  totales y desglose por fase de distribución, agrupado por ítem.
  - Alertas:        casos borde detectados durante el procesamiento.
  - Riesgo Merma:   tiendas-ítem donde los días proyectados post-pedido superan
                    el umbral min(10, vida_útil).
"""

import io

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill

HEADER_FILL = PatternFill("solid", fgColor="2D6A4F")
HEADER_FONT = Font(color="FFFFFF", bold=True)
HEADER_ALIGN = Alignment(horizontal='center')


def _style_header(ws):
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = HEADER_ALIGN


def _autofit(ws):
    for col in ws.columns:
        max_len = max((len(str(cell.value or '')) for cell in col), default=0) + 2
        ws.column_dimensions[col[0].column_letter].width = min(max_len, 50)


def generate_excel(df_output, alertas=None):
    """Genera el archivo Excel de distribución y lo retorna como bytes."""
    alertas = alertas or []
    has_data = df_output is not None and len(df_output) > 0

    # Pre-computar columnas derivadas una sola vez para reutilizar en todas las hojas.
    if has_data:
        d = df_output.copy()
        for col in ('cajas_fase1', 'cajas_fase2a', 'cajas_fase2b', 'vida_util'):
            if col not in d.columns:
                d[col] = 0
        d['_stock_despues'] = d['inventario_efectivo'] + d['cajas_asignadas']
        d['_dias_despues'] = d.apply(
            lambda r: round(r['_stock_despues'] / r['consumo_diario'], 1)
            if r['consumo_diario'] > 0 else None,
            axis=1,
        )
    else:
        d = None

    # ── Hoja Distribución ──────────────────────────────────────────────────────
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
            'Días Inventario Actual':          d['dias_proyectados'].round(1),
            'Pedido Final':                    d['cajas_asignadas'].astype(int),
            'Stock Después Pedido':            d['_stock_despues'].round(2),
            'Días Inventario Proyectado':      d['_dias_despues'],
        }).sort_values(['Centro Operacional de la Bodega', 'Código de Producto'])

    # ── Hoja Resumen ───────────────────────────────────────────────────────────
    if len(dist_df) > 0:
        resumen_df = (
            dist_df.groupby(
                ['Centro Operacional de la Bodega', 'Nombre Tienda', 'Zona'], as_index=False
            )['Pedido Final']
            .sum()
            .rename(columns={'Pedido Final': 'Total Cajas'})
            .sort_values('Total Cajas', ascending=False)
        )
    else:
        resumen_df = pd.DataFrame(
            columns=['Centro Operacional de la Bodega', 'Nombre Tienda', 'Zona', 'Total Cajas']
        )

    # ── Hoja Resumen Ítems ─────────────────────────────────────────────────────
    if has_data:
        resumitem_df = (
            d.groupby(['item_code', 'item_desc'], as_index=False)
            .agg(
                total_cajas=('cajas_asignadas', 'sum'),
                fase1=('cajas_fase1', 'sum'),
                fase2a=('cajas_fase2a', 'sum'),
                fase2b=('cajas_fase2b', 'sum'),
            )
            .rename(columns={
                'item_code':   'Código Ítem',
                'item_desc':   'Nombre Ítem',
                'total_cajas': 'Total Cajas',
                'fase1':       'Cajas Fase 1',
                'fase2a':      'Cajas Fase 2a',
                'fase2b':      'Cajas Fase 2b',
            })
            .sort_values('Código Ítem')
        )
    else:
        resumitem_df = pd.DataFrame(columns=[
            'Código Ítem', 'Nombre Ítem', 'Total Cajas',
            'Cajas Fase 1', 'Cajas Fase 2a', 'Cajas Fase 2b',
        ])

    # ── Hoja Riesgo Merma ──────────────────────────────────────────────────────
    _MERMA_COLS = [
        'Zona', 'Centro Operacional de la Bodega', 'Nombre Tienda',
        'Código Ítem', 'Nombre Ítem',
        'Días Proy. Post-Pedido', 'Vida Útil (días)', 'Umbral Merma (días)', 'Exceso (días)',
    ]
    if has_data:
        # Umbral: min(10, vida_util) si vida_util > 0; si no, 10.
        d['_umbral'] = d['vida_util'].apply(
            lambda v: min(10.0, float(v)) if pd.notna(v) and float(v) > 0 else 10.0
        )
        # Incluye filas con dias_despues > umbral Y filas con consumo=0 que reciben cajas
        # (inventario infinito = riesgo seguro de merma).
        is_riesgo = (
            (d['_dias_despues'].notna() & (d['_dias_despues'] > d['_umbral']))
            | (d['_dias_despues'].isna() & (d['cajas_asignadas'] > 0))
        )
        merma_rows = d[is_riesgo].copy()
        if len(merma_rows) > 0:
            merma_rows['_exceso'] = (merma_rows['_dias_despues'] - merma_rows['_umbral']).round(1)
            merma_out = pd.DataFrame({
                'Zona':                            merma_rows['zona'],
                'Centro Operacional de la Bodega': merma_rows['store_code'],
                'Nombre Tienda':                   merma_rows['store_name'],
                'Código Ítem':                     merma_rows['item_code'],
                'Nombre Ítem':                     merma_rows['item_desc'],
                'Días Proy. Post-Pedido':          merma_rows['_dias_despues'],
                'Vida Útil (días)':                merma_rows['vida_util'].fillna(0).astype(int),
                'Umbral Merma (días)':             merma_rows['_umbral'],
                'Exceso (días)':                   merma_rows['_exceso'],
            }).sort_values('Exceso (días)', ascending=False, na_position='first')
        else:
            merma_out = pd.DataFrame(
                [dict.fromkeys(_MERMA_COLS, None) | {'Nombre Ítem': 'Sin riesgo de merma detectado'}]
            )
    else:
        merma_out = pd.DataFrame(columns=_MERMA_COLS)

    # ── Escritura ──────────────────────────────────────────────────────────────
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        dist_df.to_excel(writer, index=False, sheet_name='Distribución')
        resumen_df.to_excel(writer, index=False, sheet_name='Resumen')
        resumitem_df.to_excel(writer, index=False, sheet_name='Resumen Ítems',
                              startrow=4)
        merma_out.to_excel(writer, index=False, sheet_name='Riesgo Merma')

        # Notas de fases en las primeras filas de "Resumen Ítems".
        _notas = [
            'Fase 1 — Urgentes: cubre tiendas AGOTADAS y en STOCK DE SEGURIDAD con cap '
            'creciente por rondas (máx. 2 cajas/ronda). Todas reciben su 1.ª caja antes '
            'de que alguna reciba su 2.ª.',
            'Fase 2a — Proporcional: completa hasta 3 días de inventario. Si el stock no '
            'alcanza para todos, la escasez se reparte de forma proporcional '
            '(corrección Hamilton para residuos enteros).',
            'Fase 2b — Excedente: el sobrante se distribuye por rondas a las tiendas con '
            'menos cajas acumuladas (consumo > 0, tope 5 días). Evita que las tiendas de '
            'mayor consumo acaparen el excedente.',
        ]
        ws_ri = writer.sheets['Resumen Ítems']
        for i, nota in enumerate(_notas, start=1):
            ws_ri.cell(row=i, column=1, value=nota)

        for sheet in ('Distribución', 'Resumen', 'Riesgo Merma'):
            ws = writer.sheets[sheet]
            _style_header(ws)
            _autofit(ws)

        # Encabezado de "Resumen Ítems" desplazado por las notas (fila 5 en Excel).
        for cell in ws_ri[5]:
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = HEADER_ALIGN
        _autofit(ws_ri)

    return buffer.getvalue()

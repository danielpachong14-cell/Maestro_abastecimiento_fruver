"""Genera el Excel de salida formateado con la distribución.

Hojas:
  - Distribución: el pedido por tienda-ítem (lo que se ejecuta).
  - Resumen: totales de cajas por tienda.
  - Alertas: casos borde detectados durante el procesamiento.
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
    """Genera el archivo Excel de distribución y lo retorna como bytes.

    Columnas de la hoja principal:
      Centro Operacional de la Bodega (COD SIESA) | Código de Producto | UM | Pedido Final
    """
    alertas = alertas or []

    if df_output is None or len(df_output) == 0:
        dist_df = pd.DataFrame(columns=[
            'Centro Operacional de la Bodega', 'Nombre Tienda',
            'Código de Producto', 'Nombre Ítem', 'UM',
            'Consumo Diario', 'Stock Antes Pedido', 'Días Inventario Actual',
            'Pedido Final', 'Stock Después Pedido', 'Días Inventario Proyectado',
        ])
    else:
        d = df_output.copy()
        d['_stock_despues'] = d['inventario_efectivo'] + d['cajas_asignadas']
        d['_dias_despues'] = d.apply(
            lambda r: round(r['_stock_despues'] / r['consumo_diario'], 1)
            if r['consumo_diario'] > 0 else None,
            axis=1,
        )
        dist_df = pd.DataFrame({
            'Centro Operacional de la Bodega': d['store_code'],
            'Nombre Tienda':                   d['store_name'],
            'Código de Producto':              d['item_code'],
            'Nombre Ítem':                     d['item_desc'],
            'UM':                              d['um'],
            'Consumo Diario':                  d['consumo_diario'].round(2),
            'Stock Antes Pedido':              d['inventario_efectivo'].round(2),
            'Días Inventario Actual':          d['dias_proyectados'].round(1),
            'Pedido Final':                    d['cajas_asignadas'].astype(int),
            'Stock Después Pedido':            d['_stock_despues'].round(2),
            'Días Inventario Proyectado':      d['_dias_despues'],
        })
        dist_df = dist_df.sort_values(
            ['Centro Operacional de la Bodega', 'Código de Producto']
        )

    # Resumen: total de cajas por tienda.
    if len(dist_df) > 0:
        resumen_df = (
            dist_df.groupby(
                ['Centro Operacional de la Bodega', 'Nombre Tienda'], as_index=False
            )['Pedido Final']
            .sum()
            .rename(columns={'Pedido Final': 'Total Cajas'})
            .sort_values('Total Cajas', ascending=False)
        )
    else:
        resumen_df = pd.DataFrame(
            columns=['Centro Operacional de la Bodega', 'Nombre Tienda', 'Total Cajas']
        )

    # Alertas.
    if alertas:
        alertas_df = pd.DataFrame(alertas)[
            [c for c in ['tipo', 'item', 'mensaje'] if c in pd.DataFrame(alertas).columns]
        ].rename(columns={'tipo': 'Tipo', 'item': 'Ítem', 'mensaje': 'Mensaje'})
    else:
        alertas_df = pd.DataFrame([{'Tipo': 'OK', 'Ítem': '-', 'Mensaje': 'Sin alertas'}])

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        dist_df.to_excel(writer, index=False, sheet_name='Distribución')
        resumen_df.to_excel(writer, index=False, sheet_name='Resumen')
        alertas_df.to_excel(writer, index=False, sheet_name='Alertas')
        for sheet in ('Distribución', 'Resumen', 'Alertas'):
            ws = writer.sheets[sheet]
            _style_header(ws)
            _autofit(ws)

    return buffer.getvalue()

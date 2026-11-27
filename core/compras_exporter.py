"""Excel de la compra (`compra_fruver_YYYYMMDD.xlsx`): Compra General, Compra por Tienda, Costo
Exhibición, Bajas Pendientes, Sin Pronóstico, OC en Tránsito, Parámetros y Alertas.
"""

import io

import numpy as np
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill

from .calendario import DIAS_SEMANA
from .compras_params import tabla_parametros
from .exporter import _FILL_NIVEL, _autofit, _build_alertas_sheet, _style_header, _tabla

_FILL_EDITABLE = PatternFill("solid", fgColor="FFF2CC")   # amarillo: celda que el comprador puede cambiar
_FILL_MOTIVO = {
    'Agotada proyectada': PatternFill("solid", fgColor="F4A6A6"),
    'Exhibición':         PatternFill("solid", fgColor="BDD7EE"),
    'Reposición':         PatternFill("solid", fgColor="D4EDDA"),
}


def _fecha(d):
    return f'{DIAS_SEMANA[d.weekday()][:3]} {d:%d/%m/%Y}'


def _r(serie, n=2):
    """Redondea a `n` decimales; inf → vacío. Acepta Series (conserva el índice) o arrays."""
    if isinstance(serie, np.ndarray):
        arr = np.asarray(serie, dtype=float)
        return np.round(np.where(np.isfinite(arr), arr, np.nan), n)
    return pd.to_numeric(serie, errors='coerce').replace([np.inf, -np.inf], np.nan).round(n)


def _hoja_general(res):
    it = res.df_item.sort_values('item_code')
    plan = res.plan
    return pd.DataFrame({
        'Código Ítem': it['item_code'].astype(int),
        'Nombre Ítem': it['item_desc'],
        'Portafolio': it['cluster'],
        'UM': it['um'],
        'Día de Compra': it['dia_compra'] if 'dia_compra' in it else '',
        'Se Compra Hoy': np.where(it['se_compra_hoy'].fillna(True).astype(bool), 'Sí', 'No')
                         if 'se_compra_hoy' in it else 'Sí',
        'Llega CEDI': _fecha(plan.llegada_cedi),
        'Días Cobertura': np.where(it['periodo_activado'].fillna(False).astype(bool), it['n_dias_periodo'],
                                   it['n_dias_cobertura_item']) if 'n_dias_cobertura_item' in it else plan.n_dias_cobertura,
        'Tiendas': it['n_tiendas'].astype(int),
        'Consumo Diario Red': _r(it['consumo_red']),
        'Venta Esperada Cobertura': _r(it['venta_esperada'], 1),
        'Necesidad Tiendas': _r(it['necesidad_tiendas'], 1),
        'OC en Tránsito': _r(it['oc_transito'], 0),
        'Compra Bruta Tiendas': it['compra_bruta'].astype(int),
        'Ajuste Suavizado': it['ajuste_suavizado'].astype(int),
        'Compra Sugerida': it['compra_sugerida'].astype(int),
        'Compra Final': it['compra_final'].astype(int),
        'Costo Caja': _r(it['costo'], 0),
        'Valor Compra': _r(it['valor_compra'], 0),
        'Días Inv. Red Después': _r(it['dias_inv_red_despues'], 1),
        'Compra / Venta %': _r(it['compra_vs_venta'] * 100, 0),
        'Proveedor (última OC)': it['proveedor'] if 'proveedor' in it else '',
        'Observación': it['observacion'],
    })


def _hoja_tienda(res):
    d = res.df_tienda[res.df_tienda['con_datos']].sort_values(['store_code', 'item_code'])
    return pd.DataFrame({
        'Tienda': d['store_code'], 'Nombre Tienda': d['store_name'], 'Zona': d['zona'],
        'Código Ítem': d['item_code'].astype(int), 'Nombre Ítem': d['item_desc'],
        'Consumo Diario': _r(d['consumo_diario'], 3), 'Consumo Ajustado': _r(d['consumo_ajustado'], 3),
        'Existencias': _r(d['existencias']), 'Tránsito': _r(d['transito']),
        'Pedido Hoy': d['pedido_hoy'].astype(int), 'OC Asignada': d['oc_asignada'].astype(int),
        'Venta hasta Llegada': _r(d['venta_hasta_llegada']),
        'Inv. Proyectado Llegada': _r(d['inv_proy_llegada']),
        'Días Inv. Llegada': _r(d['dias_inv_llegada'], 1),
        'MIN Exhibición': _r(d['min_exhibicion']), 'Stock Seguridad': _r(d['stock_seguridad']),
        'Objetivo': _r(d['objetivo']), 'Necesidad': _r(d['necesidad']),
        'Compra': d['compra'].astype(int),
        'Inv. Después Compra': _r(d['inv_despues']), 'Días Inv. Después': _r(d['dias_inv_despues'], 1),
        'Último Ingreso': pd.to_datetime(d['fecha_ultimo_ingreso']).dt.date,
        'Días desde Ingreso': d['dias_desde_ingreso'],
        'Vida Útil': d['vida_util'],
        'Ingreso Vencido': np.where(d['ingreso_vencido'], 'Sí', ''),
        'Riesgo Merma': np.where(d['riesgo_merma'], 'Sí', ''),
        'Motivo': d['motivo'],
    })


def _hoja_costo_exhibicion(res):
    d = res.df_tienda[res.df_tienda['con_datos'] & res.df_tienda['vida_util'].notna()].copy()
    d['venta_vu'] = d['consumo_ajustado'] * d['vida_util']
    d = d[d['min_exhibicion'] > d['venta_vu'] + 1e-9]
    d['merma_dia'] = (d['min_exhibicion'] / d['vida_util'] - d['consumo_ajustado']).clip(lower=0)
    d['merma_sem'] = d['merma_dia'] * 7
    d['merma_cop'] = d['merma_sem'] * d['costo']
    d = d.sort_values(['merma_cop', 'merma_sem'], ascending=False, na_position='last')
    return pd.DataFrame({
        'Tienda': d['store_code'], 'Nombre Tienda': d['store_name'],
        'Código Ítem': d['item_code'].astype(int), 'Nombre Ítem': d['item_desc'], 'Portafolio': d['cluster'],
        'MIN Exhibición': _r(d['min_exhibicion']), 'Consumo Diario': _r(d['consumo_ajustado'], 3),
        'Vida Útil': d['vida_util'], 'Venta en Vida Útil': _r(d['venta_vu']),
        'Días para Vender MIN': _r(np.where(d['consumo_ajustado'] > 0,
                                            d['min_exhibicion'] / d['consumo_ajustado'].replace(0, np.nan), np.nan), 1),
        'Merma Estructural Semana (cajas)': _r(d['merma_sem'], 1),
        'Merma Estructural Semana (COP)': _r(d['merma_cop'], 0),
    })


def _hoja_bajas(res):
    d = res.df_tienda[res.df_tienda['ingreso_vencido']].sort_values(['store_code', 'item_code'])
    return pd.DataFrame({
        'Tienda': d['store_code'], 'Nombre Tienda': d['store_name'],
        'Código Ítem': d['item_code'].astype(int), 'Nombre Ítem': d['item_desc'],
        'Existencias': _r(d['existencias_tienda']),
        'Último Ingreso': pd.to_datetime(d['fecha_ultimo_ingreso']).dt.date,
        'Días desde Ingreso': d['dias_desde_ingreso'], 'Vida Útil': d['vida_util'],
        'Consumo Diario': _r(d['consumo_diario'], 3),
        'Valor (COP)': _r(d['existencias_tienda'] * d['costo'], 0),
    })


def _hoja_sin_pronostico(res):
    it = res.df_item
    en_celes_mask = it['en_celes'].astype(bool)
    filas = []
    for _, r in it[~en_celes_mask].sort_values('item_code').iterrows():
        filas.append({'Tipo': 'Ítem sin datos en Celes', 'Código Ítem': int(r['item_code']),
                      'Nombre Ítem': r['item_desc'], 'Portafolio': r['cluster'], 'Tienda': '',
                      'Nombre Tienda': f'{int(r["n_tiendas"])} tienda(s) con portafolio activo',
                      'Detalle': 'Sin consumo ni inventario en Celes: compra manual. La referencia es el MIN de '
                                 'exhibición de todas sus tiendas (compra de lanzamiento).',
                      'Compra Referencia (Σ MIN)': round(float(r['suma_min']), 1)})
    en_celes = set(it.loc[en_celes_mask, 'item_code'])
    d = res.df_tienda[res.df_tienda['sin_match_celes'] & res.df_tienda['item_code'].isin(en_celes)]
    for _, r in d.sort_values(['item_code', 'store_code']).iterrows():
        filas.append({'Tipo': 'Tienda×ítem sin fila en Celes', 'Código Ítem': int(r['item_code']),
                      'Nombre Ítem': r['item_desc'], 'Portafolio': r['cluster'], 'Tienda': r['store_code'],
                      'Nombre Tienda': r['store_name'],
                      'Detalle': 'Portafolio activo pero Celes no trae consumo ni inventario: no se le compra.',
                      'Compra Referencia (Σ MIN)': round(float(r['min_exhibicion']), 1)})
    cols = ['Tipo', 'Código Ítem', 'Nombre Ítem', 'Portafolio', 'Tienda', 'Nombre Tienda', 'Detalle',
            'Compra Referencia (Σ MIN)']
    return pd.DataFrame(filas, columns=cols)


def _hoja_oc(res):
    d = res.oc_detalle
    cols = ['Nro orden', 'Fecha orden', 'Código Ítem', 'Nombre Ítem', 'Proveedor', 'Fecha Esperada CEDI',
            'Pendiente (cajas)', 'Clasificación', 'Cuenta para la Compra', 'Último Ingreso CEDI (Stock)']
    if d is None or len(d) == 0:
        return pd.DataFrame(columns=cols)
    orden = {'En tránsito': 0, 'Atrasada': 1, 'Sin fecha': 2, 'Llega después': 3, 'Recibida según Stock': 4,
             'Vencida sin recibir': 5, 'Ítem no activo': 6, 'Otra bodega': 7, 'Cerrada': 8, 'Recibida': 9}
    d = d.assign(_o=d['clasificacion'].map(orden)).sort_values(['_o', 'fecha_esperada', 'item_code'], kind='stable')
    return pd.DataFrame({
        'Nro orden': d['Nro orden'], 'Fecha orden': d['Fecha orden'], 'Código Ítem': d['item_code'].astype(int),
        'Nombre Ítem': d['Desc. item'], 'Proveedor': d['Proveedor'], 'Fecha Esperada CEDI': d['fecha_esperada'],
        'Pendiente (cajas)': _r(d['pendiente'], 0), 'Clasificación': d['clasificacion'],
        'Cuenta para la Compra': np.where(d['cuenta'], 'Sí', 'No'),
        'Último Ingreso CEDI (Stock)': d['ultima_entrada_cedi'],
    })


def resumen_plan(plan):
    """[(etiqueta, valor)] con las fechas del plan (hoja Parámetros, consola y app)."""
    filas = [
        ('Fecha de los datos', _fecha(plan.fecha_datos)),
        ('Fecha de compra', _fecha(plan.fecha_compra)),
        ('Llega al CEDI', _fecha(plan.llegada_cedi)),
        ('Llega a tienda', _fecha(plan.llegada_tienda)),
        ('Recepción CEDI ese día', f'{plan.capacidad_llegada:.0%}'),
        ('Días de venta hasta la llegada', f'{plan.n_dias_hasta_llegada} ({", ".join(_fecha(d) for d in plan.dias_hasta_llegada)})'),
        ('Días que cubre la compra', f'{plan.n_dias_cobertura} ({", ".join(_fecha(d) for d in plan.dias_cobertura)})'),
        ('Días de venta ponderados (cobertura)', round(plan.peso_cobertura, 2)),
        ('Siguiente compra', f'{_fecha(plan.siguiente_compra)} → llega a tienda {_fecha(plan.siguiente_llegada_tienda)}'),
    ]
    for a in plan.anticipaciones:
        filas.append(('Anticipa llegada reducida',
                      f'compra {_fecha(a["fecha_compra"])} (CEDI {_fecha(a["llegada_cedi"])}, recepción '
                      f'{a["capacidad"]:.0%}): {a["peso_anticipado"]:.2f} día(s) de venta'))
    return filas


def resumen_compra_texto(res):
    """Una línea para consola/app."""
    p = res.plan
    n_items = int((res.df_item['compra_sugerida'] > 0).sum()) if len(res.df_item) else 0
    return (f'{res.total_cajas:,} cajas en {n_items} ítem(s) · valor ${res.valor_total:,.0f} · compra del '
            f'{_fecha(p.fecha_compra)} llega al CEDI {_fecha(p.llegada_cedi)} y a tienda {_fecha(p.llegada_tienda)} '
            f'· cubre {p.n_dias_cobertura} día(s)')


def _hoja_parametros(res):
    it = res.df_item
    filas = [('RESUMEN', '')]
    filas += [
        ('Compra sugerida (cajas)', res.total_cajas),
        ('Valor compra (COP)', round(res.valor_total)),
        ('Venta esperada en la cobertura (cajas)', round(float(it['venta_esperada'].sum()), 1) if len(it) else 0),
        ('Compra bruta de tiendas (cajas)', int(it['compra_bruta'].sum()) if len(it) else 0),
        ('OC en tránsito contadas (cajas)', round(float(it['oc_transito'].sum())) if len(it) else 0),
        ('Ítems activos / con compra', f'{len(it)} / {int((it["compra_sugerida"] > 0).sum()) if len(it) else 0}'),
        ('', ''), ('PLAN DE LA COMPRA', ''),
    ]
    filas += resumen_plan(res.plan)
    especiales = it[it['regimen'].isin(['dias', 'cada'])] if 'regimen' in it else it.iloc[0:0]
    if len(especiales):
        filas += [('', ''), ('ÍTEMS CON DÍA DE COMPRA PROPIO', '')]
        for _, r in especiales.iterrows():
            estado = ('hoy se compra' if r['compra_sugerida'] > 0 else 'hoy no se compra')
            filas.append((f'{int(r["item_code"])} {r["item_desc"]}', f'{r["dia_compra"]} · {estado}'))
    filas += [('', ''), ('PARÁMETROS', '')]
    filas += tabla_parametros(res.params)
    return pd.DataFrame(filas, columns=['Parámetro', 'Valor'])


def generate_purchase_excel(res):
    """Genera el Excel de la compra (bytes) a partir de un `compras.ResultadoCompra`."""
    hojas = [
        ('Compra General', _hoja_general(res)),
        ('Compra por Tienda', _hoja_tienda(res)),
        ('Costo Exhibición', _hoja_costo_exhibicion(res)),
        ('Bajas Pendientes', _hoja_bajas(res)),
        ('Sin Pronóstico', _hoja_sin_pronostico(res)),
        ('OC en Tránsito', _hoja_oc(res)),
        ('Parámetros', _hoja_parametros(res)),
        ('Alertas', _build_alertas_sheet(res.alertas)),
    ]
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        for nombre, df in hojas:
            df.to_excel(writer, index=False, sheet_name=nombre)
            ws = writer.sheets[nombre]
            _style_header(ws)
            _tabla(ws)
            _autofit(ws)

        ws = writer.sheets['Compra General']
        cols = [c.value for c in ws[1]]
        col_final = cols.index('Compra Final') + 1
        for fila in range(2, ws.max_row + 1):
            ws.cell(row=fila, column=col_final).fill = _FILL_EDITABLE
            ws.cell(row=fila, column=col_final).font = Font(bold=True)
        ws.column_dimensions[ws.cell(row=1, column=cols.index('Observación') + 1).column_letter].width = 70

        ws = writer.sheets['Compra por Tienda']
        cols = [c.value for c in ws[1]]
        col_motivo = cols.index('Motivo') + 1
        for fila in range(2, ws.max_row + 1):
            celda = ws.cell(row=fila, column=col_motivo)
            if celda.value in _FILL_MOTIVO:
                celda.fill = _FILL_MOTIVO[celda.value]

        ws = writer.sheets['Parámetros']
        ws.column_dimensions['A'].width = 42
        ws.column_dimensions['B'].width = 90
        for fila in range(2, ws.max_row + 1):
            if ws.cell(row=fila, column=1).value in ('RESUMEN', 'PLAN DE LA COMPRA', 'PARÁMETROS',
                                                     'ÍTEMS CON DÍA DE COMPRA PROPIO'):
                ws.cell(row=fila, column=1).font = Font(bold=True)

        ws = writer.sheets['Alertas']
        ws.column_dimensions['A'].width = 14
        ws.column_dimensions['B'].width = 10
        ws.column_dimensions['C'].width = 100
        for fila in range(2, ws.max_row + 1):
            nivel = ws.cell(row=fila, column=1)
            if nivel.value in _FILL_NIVEL:
                nivel.fill = _FILL_NIVEL[nivel.value]
            nivel.alignment = Alignment(horizontal='center', vertical='top')
            ws.cell(row=fila, column=3).alignment = Alignment(wrap_text=True, vertical='top')
    return buffer.getvalue()

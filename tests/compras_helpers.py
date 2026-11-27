"""Datos sintéticos para los tests del módulo de compras (mismas columnas que entrega el loader).

Fecha de referencia: jueves 2026-11-19 → la compra llega al CEDI el sábado 21, a tienda el domingo
22, se venden 3 días antes de la llegada (jue, vie, sáb) y cubre 2 días (dom y lun).
"""

from datetime import date

import pandas as pd

from core.compras import ejecutar_compra

JUEVES = date(2026, 11, 19)


def tienda(store, item, consumo, existencias=0.0, transito=0.0, ss=0.0, min_ex=None, max_ex=0,
           en_celes=True, ultimo_ingreso='2026-11-18'):
    """Una tienda×ítem: fila de Celes (si en_celes) + fila de Tiendas × Ítem + último ingreso."""
    return dict(store=store, item=item, consumo=consumo, existencias=existencias, transito=transito,
                ss=ss, min_ex=min_ex, max_ex=max_ex, en_celes=en_celes, ultimo_ingreso=ultimo_ingreso)


def item(code, vida_util=5, estado='ACTIVO', cedi=0, costo=10000.0, cluster='BASICO', dia_compra='Todos los dias',
         ultima_entrada=None):
    """Un ítem del Portafolio (+ su fila de Stock). ultima_entrada: último ingreso al CEDI (Stock)."""
    return dict(code=code, vida_util=vida_util, estado=estado, cedi=cedi, costo=costo, cluster=cluster,
                dia_compra=dia_compra, ultima_entrada=ultima_entrada)


def armar_dfs(filas, items, excluidas=None):
    """dict como el de `loader.load_files` a partir de listas de `tienda(...)` e `item(...)`."""
    tiendas_codigos = sorted({f['store'] for f in filas})
    celes = pd.DataFrame([{
        'Código de Bodega': 'BO' + f['store'], 'Nombre de Bodega': f'Tienda {f["store"]}',
        'Código de Producto': f['item'],
        'Consumo Diario (Unidades de Distribución)': f['consumo'],
        '(=) Días de Inventario Actuales': 0.0,
        'Inventario Disponible en esta Tienda (Unidades de Distibución)': f['existencias'],
        '(-) inventario de traslado en proceso': f['transito'],
        '(=) Inventario Total': f['existencias'] + f['transito'], 'UM': 'CN10',
        '(+) Stock de Seguridad': f['ss'],
        'Tiempo de Entrega desde Centro de Distribución (Días)': 1,
        'Periodo de Revisión de Distribución (Días)': 1,
        'Nivel de Servicio': 90,
    } for f in filas if f['en_celes']])
    if len(celes) == 0:
        celes = pd.DataFrame(columns=['Código de Bodega', 'Nombre de Bodega', 'Código de Producto',
                                      'Consumo Diario (Unidades de Distribución)',
                                      'Inventario Disponible en esta Tienda (Unidades de Distibución)',
                                      '(-) inventario de traslado en proceso', '(+) Stock de Seguridad'])
    dfs = {
        'stock': pd.DataFrame([{
            'Item': f'{i["code"]:07d}', 'Desc. item': f'Item {i["code"]}', 'Cant. disponible': i['cedi'],
            'Factor U.M.': 10, 'U.M.': 'CN10', 'ESTADO DEL PRODUCTO': '001 - ACTIVO',
            'Costo prom. uni.': i['costo'], 'Bodega': 'BOCES', 'Fecha última entrada': i['ultima_entrada'],
        } for i in items]),
        'celes': celes,
        'portafolio': pd.DataFrame([{
            'ITEM': i['code'], 'DESCRIPCION': f'Item {i["code"]}', 'CLUSTERIZACIÓN': i['cluster'],
            'ESTADO': i['estado'], 'UM': 'CN10', 'COSTO': i['costo'], 'VIDA UTIL EXHIBICION': i['vida_util'],
            'DIA DE COMPRA': i['dia_compra'],
        } for i in items]),
        'tiendas': pd.DataFrame([{'COD SIESA': s, 'NOMBRE DE LA TIENDA': f'Tienda {s}',
                                  'TIPO DE PORTAFOLIO': 'B', 'ZONA': 'Zona 1'} for s in tiendas_codigos]),
        'tiendas_item': pd.DataFrame([{
            'COD SIESA': f['store'], 'TIPO DE PORTAFOLIO': 'B', 'ITEM': f['item'], 'ESTADO': 'ACTIVO',
            'MIN': f['min_ex'], 'MAX': f['max_ex'],
        } for f in filas]),
        'espejo': pd.DataFrame(columns=['grupo_id', 'item_id']),
        'inventario_tiendas': pd.DataFrame([{
            'Item': f['item'], 'C.O. bodega': f['store'], 'Cant. disponible': f['existencias'],
            'Fecha última entrada': f['ultimo_ingreso'],
        } for f in filas] + [{'Item': 0, 'C.O. bodega': 'X', 'Cant. disponible': 0.0,
                              'Fecha última entrada': str(JUEVES)}]),
    }
    if excluidas is not None:
        dfs['excluidas'] = pd.DataFrame({'Centro Operacional de la Bodega': excluidas})
    return dfs


def distribucion(asignaciones):
    """`df_output` mínimo de la distribución: [(tienda, ítem, cajas)]."""
    return pd.DataFrame(asignaciones, columns=['store_code', 'item_code', 'cajas_asignadas'])


def oc(filas):
    """DataFrame de Compras_Transito: [(ítem, cajas, 'AAAA-MM-DD')]."""
    return pd.DataFrame(filas, columns=['Item', 'Cantidad (cajas)', 'Fecha esperada CEDI'])


def comprar(filas, items, dist=None, oc_df=None, fecha=JUEVES, sin_ss=True, **overrides):
    """Ejecuta la compra completa. Por defecto sin stock de seguridad (cuentas exactas)."""
    if sin_ss:
        overrides.setdefault('usar_ss_celes', False)
    dist = distribucion([]) if dist is None else dist
    return ejecutar_compra(armar_dfs(filas, items), dist, compras_transito=oc_df, fecha=fecha,
                           overrides=overrides)


def fila(res, store, item_code):
    t = res.df_tienda
    return t[(t['store_code'] == store) & (t['item_code'] == item_code)].iloc[0]


def compra_item(res, item_code):
    return int(res.df_item.loc[res.df_item['item_code'] == item_code, 'compra_sugerida'].iloc[0])

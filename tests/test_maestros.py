"""Tests del libro único de maestros (`core/maestros.py`) y de `loader.load_inputs`."""

import io

import openpyxl
import pandas as pd
import pytest

from core.compras_params import ParametrosCompra, cargar_parametros, tabla_calendario, tabla_dias, tabla_general
from core.loader import load_inputs
from core.maestros import (
    COL_STATUS,
    HOJA_BD_MAX,
    HOJA_CALENDARIO,
    HOJA_DIAS,
    HOJA_ESPEJO,
    HOJA_NO_PEDIDO,
    HOJA_PARAMETROS,
    HOJA_PORTAFOLIO,
    HOJA_STATUS,
    HOJA_TIENDAS,
    HOJA_TIENDAS_ITEM,
    escribir_maestros,
    hojas_desde_sueltos,
    leer_maestros,
)


def _hojas(**cambios):
    params = ParametrosCompra(crecimiento_venta=0.18)
    hojas = {
        HOJA_PORTAFOLIO: pd.DataFrame({
            'ITEM': [100, 200, 300], 'DESCRIPCION': ['A', 'B', 'C'], 'CLUSTERIZACIÓN': ['BASICO'] * 3,
            'ESTADO': None, 'UM': 'CN10', 'COSTO': [1000.0, 0, 0], 'VIDA UTIL EXHIBICION': [5, 4, 3],
            'DIAS CONSERVACION CEDI': [2, 1, 1], 'TOTAL VIDA UTIL': None, 'DIA DE COMPRA': None,
        }),
        HOJA_STATUS: pd.DataFrame({
            'Código de Producto': [100, 300], 'DESCRIPCION': None, 'STATUS': ['ACTIVO', 'ACTIVO'],
            'DIA DE COMPRA': ['Todos los dias', 'LUNES-MIERCOLES-VIERNES'], 'CRECIMIENTO VENTA (%)': [None, 10],
            'MULTIPLO COMPRA (cajas)': [5, None], 'COMPRA MINIMA (cajas)': [None, None],
        })[COL_STATUS],
        HOJA_TIENDAS: pd.DataFrame({'COD SIESA': ['S01', 'S09'], 'NOMBRE DE LA TIENDA': ['T1', 'T9'],
                                    'TIPO DE PORTAFOLIO': 'B', 'ZONA': 'Zona 1'}),
        HOJA_TIENDAS_ITEM: pd.DataFrame({
            'COD SIESA': ['S01', 'S01', 'S09'], 'NOMBRE DE LA TIENDA': ['T1', 'T1', 'T9'], 'TIPO DE PORTAFOLIO': 'B',
            'ITEM': [100, 200, 100], 'DESCRIPCION': ['A', 'B', 'A'], 'ESTADO': 'ACTIVO', 'MIN': [2, None, None],
            'MAX': None, 'LLAVE': None,
        }),
        HOJA_BD_MAX: pd.DataFrame({'COD SIESA': ['S01'], 'NOMBRE DE LA TIENDA': ['T1'], 'ITEM': [100],
                                   'DESCRIPCION': ['A'], 'MAX': [4], 'LLAVE': None}),
        HOJA_ESPEJO: pd.DataFrame({'grupo_id': ['G1'], 'item_id': [100]}),
        HOJA_NO_PEDIDO: pd.DataFrame({'Centro Operacional de la Bodega': ['S09'], 'Nombre Tienda': ['T9']}),
        HOJA_PARAMETROS: tabla_general(params),
        HOJA_DIAS: tabla_dias(params),
        HOJA_CALENDARIO: tabla_calendario(params, anios=(2026,)),
    }
    hojas.update(cambios)
    return hojas


def _libro(**cambios):
    return io.BytesIO(escribir_maestros(_hojas(**cambios)))


def test_estado_y_dia_de_compra_salen_de_status_sin_depender_de_excel():
    dfs, _, alertas = leer_maestros(_libro())
    p = dfs['portafolio'].set_index('ITEM')
    assert p.loc[100, 'ESTADO'] == 'ACTIVO'
    assert p.loc[200, 'ESTADO'] == 'DESCONTINUADO'          # no está en STATUS
    assert p.loc[300, 'DIA DE COMPRA'] == 'LUNES-MIERCOLES-VIERNES'
    assert p.loc[200, 'DIA DE COMPRA'] == 'DESCONTINUADO'
    assert p.loc[100, 'TOTAL VIDA UTIL'] == 7
    assert alertas == []


def test_max_sale_de_bd_max_y_sin_coincidencia_es_cero():
    dfs, _, _ = leer_maestros(_libro())
    ti = dfs['tiendas_item'].set_index(['COD SIESA', 'ITEM'])
    assert ti.loc[('S01', 100), 'MAX'] == 4
    assert ti.loc[('S01', 200), 'MAX'] == 0
    assert ti.loc[('S09', 100), 'MAX'] == 0


def test_tiendas_sin_pedido_solo_si_la_hoja_tiene_filas():
    dfs, _, _ = leer_maestros(_libro())
    assert list(dfs['excluidas']['Centro Operacional de la Bodega']) == ['S09']
    vacia = pd.DataFrame(columns=['Centro Operacional de la Bodega', 'Nombre Tienda'])
    dfs, _, _ = leer_maestros(_libro(**{HOJA_NO_PEDIDO: vacia}))
    assert 'excluidas' not in dfs


def test_parametros_y_ajustes_por_item_desde_el_libro():
    _, hojas_par, _ = leer_maestros(_libro())
    params, alertas = cargar_parametros(hojas_par)
    assert alertas == []
    assert params.crecimiento_venta == pytest.approx(0.18)    # se escribe como 18 (%)
    assert params.multiplo_item(100) == 5
    assert params.crecimiento_item(300) == pytest.approx(0.10)
    assert params.crecimiento_item(100) == pytest.approx(0.18)


def test_el_libro_trae_tablas_y_formulas_para_excel():
    wb = openpyxl.load_workbook(_libro())
    assert {HOJA_PORTAFOLIO, HOJA_STATUS, HOJA_TIENDAS_ITEM, HOJA_BD_MAX} <= set(wb.sheetnames)
    p = wb[HOJA_PORTAFOLIO]
    cab = [c.value for c in p[1]]
    assert p.cell(2, cab.index('ESTADO') + 1).value.startswith('=_xlfn.XLOOKUP(')
    t = wb[HOJA_TIENDAS_ITEM]
    cab = [c.value for c in t[1]]
    assert 'Tabla_MAX[MAX]' in t.cell(2, cab.index('MAX') + 1).value
    assert 'Portafolio_Fruver' in wb[HOJA_PORTAFOLIO].tables


def test_falta_hoja_obligatoria():
    hojas = _hojas()
    del hojas[HOJA_STATUS]
    with pytest.raises(ValueError, match='STATUS'):
        leer_maestros(io.BytesIO(escribir_maestros(hojas)))


def test_status_repetido_o_huerfano_alerta():
    st = pd.DataFrame({'Código de Producto': [100, 100, 999], 'DESCRIPCION': None,
                       'STATUS': ['ACTIVO', 'INACTIVO', 'ACTIVO'], 'DIA DE COMPRA': 'Todos los dias',
                       'CRECIMIENTO VENTA (%)': None, 'MULTIPLO COMPRA (cajas)': None, 'COMPRA MINIMA (cajas)': None})
    dfs, _, alertas = leer_maestros(_libro(**{HOJA_STATUS: st}))
    assert dfs['portafolio'].set_index('ITEM').loc[100, 'ESTADO'] == 'ACTIVO'   # primera fila, como el XLOOKUP
    textos = ' '.join(a['mensaje'] for a in alertas)
    assert 'repetido' in textos and '999' in textos


def _diarios():
    def xl(data, hoja='Sheet1'):
        buf = io.BytesIO()
        pd.DataFrame(data).to_excel(buf, index=False, sheet_name=hoja)
        buf.seek(0)
        return buf
    stock = xl({'Item': ['0000100'], 'Desc. item': ['A'], 'Cant. disponible': [3], 'Factor U.M.': [10],
                'U.M.': ['CN10'], 'ESTADO DEL PRODUCTO': ['001 - ACTIVO']})
    celes = xl({'Código de Bodega': ['BOS01'], 'Nombre de Bodega': ['T1'], 'Código de Producto': [100],
                'Consumo Diario (Unidades de Distribución)': [1.0], '(=) Días de Inventario Actuales': [1.0],
                'Inventario Disponible en esta Tienda (Unidades de Distibución)': [1.0],
                '(-) inventario de traslado en proceso': [0.0], '(=) Inventario Total': [1.0], 'UM': ['CN10']},
               hoja='Items')
    inv = xl({'Item': [100], 'C.O. bodega': ['S01'], 'Cant. disponible': [1.0],
              'Fecha última entrada': ['2026-11-18']}, hoja='Inv Tiendas')
    return stock, celes, inv


def test_load_inputs_arma_el_mismo_dict_que_el_formato_anterior():
    stock, celes, inv = _diarios()
    oc = io.BytesIO()
    pd.DataFrame({'Item': [100], 'Cant. ordenada': [2], 'Fecha de Entrega': ['2026-11-20']}).to_excel(oc, index=False)
    dfs, extra, alertas = load_inputs(stock, celes, inv, _libro(), compras_transito=oc)
    assert set(dfs) == {'stock', 'celes', 'inventario_tiendas', 'portafolio', 'tiendas', 'tiendas_item',
                        'espejo', 'excluidas'}
    assert set(extra) == {'parametros_compra', 'compras_transito'}
    from core.algorithm import run_distribution
    from core.preprocessor import build_distribution_df
    dm, al = build_distribution_df(dfs)
    out, _ = run_distribution(dm, alertas_extra=al)
    assert int(out['cajas_asignadas'].sum()) == 3


def test_load_inputs_valida_columnas_de_cada_hoja():
    stock, celes, inv = _diarios()
    malo = _hojas()[HOJA_TIENDAS].drop(columns=['ZONA'])
    with pytest.raises(ValueError, match='hoja «Tiendas».*ZONA'):
        load_inputs(stock, celes, inv, _libro(**{HOJA_TIENDAS: malo}))
    with pytest.raises(ValueError, match='Maestros Fruver'):
        load_inputs(stock, celes, inv, None)


def test_migracion_desde_archivos_sueltos():
    def xl(hojas):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf) as w:
            for nombre, df in hojas.items():
                df.to_excel(w, index=False, sheet_name=nombre)
        buf.seek(0)
        return buf
    # Portafolio con la tabla STATUS en la misma hoja (columnas N:P), como el archivo real.
    port = pd.DataFrame({'ITEM': [100, 200], 'DESCRIPCION': ['A', 'B'], 'CLUSTERIZACIÓN': ['BASICO'] * 2,
                         'ESTADO': ['ACTIVO', 'DESCONTINUADO'], 'UM': 'CN10', 'COSTO': 0,
                         'VIDA UTIL EXHIBICION': [5, 4], 'DIAS CONSERVACION CEDI': [2, 1], 'TOTAL VIDA UTIL': [7, 5],
                         'DIA DE COMPRA': ['Todos los dias', 'DESCONTINUADO'], 'Unnamed: 10': None,
                         'Código de Producto': [100, None], 'DIA DE COMPRA.1': ['Todos los dias', None],
                         'STATUS': ['ACTIVO', None]})
    tiendas = xl({'Hoja1': _hojas()[HOJA_TIENDAS]})
    ti = xl({'Hoja1': _hojas()[HOJA_TIENDAS_ITEM].assign(MAX=[4, 0, 0]), 'BD_MAX': _hojas()[HOJA_BD_MAX],
             'BackUp': pd.DataFrame({'x': [1]})})
    excl = xl({'Sheet1': pd.DataFrame({'Centro Operacional de la Bodega': ['S09'], 'Unnamed: 1': ['T9']})})
    hojas = hojas_desde_sueltos(xl({'P': port}), tiendas, ti, xl({'Hoja1': _hojas()[HOJA_ESPEJO]}), excl,
                                activar=(200,), anios=(2026,))
    st = hojas[HOJA_STATUS].set_index('Código de Producto')
    assert set(st.index) == {100, 200} and st.loc[200, 'STATUS'] == 'ACTIVO'
    assert list(hojas[HOJA_NO_PEDIDO].columns) == ['Centro Operacional de la Bodega', 'Nombre Tienda']
    dfs, _, _ = leer_maestros(io.BytesIO(escribir_maestros(hojas)))
    assert set(dfs['portafolio'].loc[dfs['portafolio']['ESTADO'] == 'ACTIVO', 'ITEM']) == {100, 200}
    assert dfs['tiendas_item'].set_index(['COD SIESA', 'ITEM']).loc[('S01', 100), 'MAX'] == 4

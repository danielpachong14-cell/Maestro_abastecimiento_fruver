"""Tests del Excel de compra (`compras_exporter.generate_purchase_excel`)."""

import io

import pandas as pd
import pytest

from compras_helpers import comprar, distribucion, item, oc, tienda
from core.compras_exporter import generate_purchase_excel, resumen_compra_texto

HOJAS = ['Compra General', 'Compra por Tienda', 'Costo Exhibición', 'Bajas Pendientes', 'Sin Pronóstico',
         'OC en Tránsito', 'Parámetros', 'Alertas']


def _escenario():
    filas = [
        tienda('S01', 100, 1.0, existencias=1, min_ex=1),
        tienda('S02', 100, 0.05, existencias=0, min_ex=2),                          # MIN > venta en vida útil
        tienda('S01', 200, 0.2, existencias=3, ultimo_ingreso='2026-11-01'),        # ingreso vencido
        tienda('S01', 300, 0, en_celes=False),                                      # ítem sin Celes
    ]
    return comprar(filas, [item(100, vida_util=4), item(200, vida_util=5), item(300)],
                   dist=distribucion([('S01', 100, 1)]), oc_df=oc([(100, 1, '2026-11-21')]))


def _leer(res):
    return pd.read_excel(io.BytesIO(generate_purchase_excel(res)), sheet_name=None)


def test_genera_las_ocho_hojas_en_orden():
    hojas = _leer(_escenario())
    assert list(hojas) == HOJAS


def test_compra_general_lista_todos_los_activos_y_compra_final_igual_a_sugerida():
    res = _escenario()
    g = _leer(res)['Compra General']
    assert list(g['Código Ítem']) == [100, 200, 300]
    assert (g['Compra Final'] == g['Compra Sugerida']).all()
    assert int(g['Compra Sugerida'].sum()) == res.total_cajas
    assert g.loc[g['Código Ítem'] == 300, 'Compra Sugerida'].iloc[0] == 0
    assert 'Sin Pronóstico' in g.loc[g['Código Ítem'] == 300, 'Observación'].iloc[0]


def test_compra_por_tienda_suma_lo_mismo_que_la_general():
    hojas = _leer(_escenario())
    t, g = hojas['Compra por Tienda'], hojas['Compra General']
    por_item = t.groupby('Código Ítem')['Compra'].sum()
    for _, r in g.iterrows():
        assert int(por_item.get(r['Código Ítem'], 0)) == int(r['Compra Sugerida'])
    assert 300 not in set(t['Código Ítem'])          # sin datos en Celes no va a la compra por tienda


def test_costo_exhibicion_bajas_y_sin_pronostico():
    hojas = _leer(_escenario())
    ce = hojas['Costo Exhibición']
    s02 = ce[(ce['Tienda'] == 'S02') & (ce['Código Ítem'] == 100)].iloc[0]
    # MIN 2 en 4 días de vida útil = 0,5 caja/día que hay que renovar; se venden 0,05 → 0,45 × 7.
    assert s02['Merma Estructural Semana (cajas)'] == pytest.approx(3.15, abs=0.06)
    assert list(hojas['Bajas Pendientes']['Código Ítem']) == [200]
    sp = hojas['Sin Pronóstico']
    assert list(sp['Código Ítem']) == [300]
    assert sp['Tipo'].iloc[0] == 'Ítem sin datos en Celes'


def test_parametros_y_alertas():
    res = _escenario()
    hojas = _leer(res)
    par = dict(zip(hojas['Parámetros']['Parámetro'], hojas['Parámetros']['Valor']))
    assert par['Fecha de compra'] == 'Jue 19/11/2026'
    assert par['Llega a tienda'] == 'Dom 22/11/2026'
    assert int(par['Compra sugerida (cajas)']) == res.total_cajas
    assert 'UMBRAL_REDONDEO (fracción de caja)' in par
    assert set(hojas['Alertas']['Nivel']) <= {'Crítico', 'Advertencia', 'OK'}
    assert 'cubre 2 día(s)' in resumen_compra_texto(res)


def test_sin_items_activos_no_rompe():
    res = comprar([tienda('S01', 100, 1.0)], [item(100, estado='INACTIVO')])
    assert res.total_cajas == 0
    hojas = _leer(res)
    assert list(hojas) == HOJAS
    assert 'No hay ítems ACTIVOS' in ' '.join(hojas['Alertas']['Qué pasó'])


def test_dia_de_compra_proveedor_y_hoja_de_oc():
    filas = [tienda('S01', 100, 1.0, min_ex=0), tienda('S01', 200, 1.0, min_ex=0)]
    oc_df = pd.DataFrame({'Nro orden': ['OC1'], 'Item': [100], 'Cant. ordenada': [1], 'Cant. entrada': [0],
                          'Fecha orden': ['2026-11-18'], 'Fecha de Entrega': ['2026-11-21'],
                          'Razón social proveedor': ['PROV A']})
    res = comprar(filas, [item(100), item(200, dia_compra='LUNES-MIERCOLES-VIERNES')], oc_df=oc_df)
    hojas = _leer(res)
    g = hojas['Compra General'].set_index('Código Ítem')
    assert g.loc[100, 'Se Compra Hoy'] == 'Sí' and g.loc[200, 'Se Compra Hoy'] == 'No'
    assert g.loc[200, 'Día de Compra'] == 'LUNES-MIERCOLES-VIERNES'
    assert g.loc[100, 'Proveedor (última OC)'] == 'PROV A'
    o = hojas['OC en Tránsito']
    assert o['Clasificación'].tolist() == ['En tránsito'] and o['Cuenta para la Compra'].tolist() == ['Sí']
    par = hojas['Parámetros']['Parámetro'].astype(str).tolist()
    assert 'ÍTEMS CON DÍA DE COMPRA PROPIO' in par

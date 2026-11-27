"""Tests del módulo de compras: parámetros, proyección a la llegada, MIN, OC, redondeo y suavizado.

Escenario base (ver compras_helpers): jueves 2026-11-19, 3 días de venta antes de la llegada
(jue, vie, sáb) y 2 días de cobertura (dom, lun). Sin stock de seguridad salvo que se indique.
"""

import io
from datetime import date

import pandas as pd
import pytest

from compras_helpers import JUEVES, armar_dfs, compra_item, comprar, distribucion, fila, item, oc, tienda
from core.compras_params import (
    ParametrosCompra,
    cargar_parametros,
    plantilla_compras_transito_bytes,
    plantilla_parametros_bytes,
)
from core.loader import LABELS, load_purchase_files


def _cuadra(res):
    """Σ compra por tienda == compra general, ítem por ítem."""
    t = res.df_tienda
    por_tienda = t[t['con_datos']].groupby('item_code')['compra'].sum()
    for _, r in res.df_item.iterrows():
        assert int(por_tienda.get(r['item_code'], 0)) == int(r['compra_sugerida'])


# ── Proyección y objetivo ────────────────────────────────────────────────────

def test_proyecta_a_la_llegada_con_existencias_transito_y_pedido_de_hoy():
    """Consumo 1/día: inv = 2 + 1 + 1 (pedido de hoy) − 3 días = 1. Objetivo = 2 días de
    cobertura = 2 → compra 1."""
    filas = [tienda('S01', 100, 1.0, existencias=2, transito=1, min_ex=1)]
    res = comprar(filas, [item(100)], dist=distribucion([('S01', 100, 1)]))
    f = fila(res, 'S01', 100)
    assert f['inv_proy_llegada'] == pytest.approx(1.0)
    assert f['objetivo'] == pytest.approx(2.0)
    assert f['compra'] == 1
    _cuadra(res)


def test_la_venta_no_deja_inventario_negativo():
    filas = [tienda('S01', 100, 2.0, existencias=1, min_ex=0)]
    res = comprar(filas, [item(100)])
    f = fila(res, 'S01', 100)
    assert f['inv_proy_llegada'] == 0
    assert f['compra'] == 4          # 2 días × 2 cajas


def test_min_vacio_usa_una_canastilla_por_defecto():
    filas = [tienda('S01', 100, 0.05, existencias=0)]
    res = comprar(filas, [item(100)])
    f = fila(res, 'S01', 100)
    assert f['min_exhibicion'] == 1.0
    assert f['compra'] == 1
    assert f['motivo'] == 'Agotada proyectada'


def test_min_del_archivo_manda_sobre_la_demanda():
    filas = [tienda('S01', 100, 0.1, existencias=0.6, min_ex=3)]
    res = comprar(filas, [item(100)], alfa_subida=1.0)
    f = fila(res, 'S01', 100)
    # inv = 0,6 − 0,3 = 0,3 (< 0,4: agotada) → objetivo 3 → necesidad 2,7 → 3 cajas.
    assert f['objetivo'] == pytest.approx(3.0)
    assert f['compra'] == 3


def test_exhibicion_se_compra_aunque_no_haya_venta():
    filas = [tienda('S01', 100, 0.0, existencias=0)]
    assert fila(comprar(filas, [item(100)]), 'S01', 100)['compra'] == 1
    assert fila(comprar(filas, [item(100)], exhibir_sin_venta=False), 'S01', 100)['compra'] == 0


def test_vida_util_limita_la_demanda_pero_no_el_min():
    """Consumo 1/día y vida útil 1 día: la demanda de 2 días (+ SS) se limita a 1 caja. El MIN
    no se limita (la exhibición manda)."""
    filas = [tienda('S01', 100, 1.0, existencias=0, ss=5, min_ex=0)]
    res = comprar(filas, [item(100, vida_util=1)], sin_ss=False)
    assert fila(res, 'S01', 100)['objetivo'] == pytest.approx(1.0)

    filas = [tienda('S01', 100, 1.0, existencias=0, min_ex=3)]
    res = comprar(filas, [item(100, vida_util=1)])
    f = fila(res, 'S01', 100)
    assert f['objetivo'] == pytest.approx(3.0)
    assert bool(f['riesgo_merma'])


def test_stock_de_seguridad_de_celes_se_escala_al_horizonte_de_la_compra():
    """Celes calcula su SS con LT 1 + revisión 1 = 2 días; la compra mira 3 + 2 = 5 días."""
    filas = [tienda('S01', 100, 1.0, existencias=10, ss=0.5, min_ex=0)]
    res = comprar(filas, [item(100, vida_util=None)], sin_ss=False)
    assert fila(res, 'S01', 100)['stock_seguridad'] == pytest.approx(0.5 * (5 / 2) ** 0.5)


def test_max_de_tiendas_por_item_limita_el_objetivo():
    filas = [tienda('S01', 100, 0.1, existencias=0, min_ex=3, max_ex=2)]
    res = comprar(filas, [item(100)])
    f = fila(res, 'S01', 100)
    assert f['objetivo'] == pytest.approx(2.0)
    assert f['compra'] == 2
    assert any('MAX' in a['mensaje'] for a in res.alertas)


def test_umbral_de_redondeo():
    # inv = 0,7 − 0,1×3 = 0,4 → necesidad 0,6 contra MIN 1.
    filas = [tienda('S01', 100, 0.1, existencias=0.7, min_ex=1)]
    assert fila(comprar(filas, [item(100)]), 'S01', 100)['compra'] == 1
    assert fila(comprar(filas, [item(100)], umbral_redondeo=0.7), 'S01', 100)['compra'] == 0
    assert fila(comprar(filas, [item(100)], umbral_redondeo=0.7), 'S01', 100)['motivo'] == 'Bajo umbral de redondeo'


def test_crecimiento_de_venta_global_y_por_item():
    filas = [tienda('S01', 100, 1.0, existencias=3, min_ex=0)]
    res = comprar(filas, [item(100)], crecimiento_venta=0.5)
    # Consumo 1,5: inv = 3 − 4,5 = 0 → 2 días × 1,5 = 3 cajas.
    assert fila(res, 'S01', 100)['compra'] == 3

    params = ParametrosCompra(por_item={100: {'crecimiento': 1.0}})
    assert params.crecimiento_item(100) == 1.0
    assert params.crecimiento_item(200) == 0.0


# ── Universo ─────────────────────────────────────────────────────────────────

def test_solo_se_compran_items_activos_del_portafolio():
    filas = [tienda('S01', 100, 1.0), tienda('S01', 200, 1.0)]
    res = comprar(filas, [item(100), item(200, estado='PRODUCTO PRUEBA', cedi=5)])
    assert set(res.df_item['item_code']) == {100}
    assert set(res.df_tienda['item_code']) == {100}
    assert any('no están ACTIVOS' in a['mensaje'] for a in res.alertas)


def test_item_activo_sin_celes_no_se_compra_y_queda_como_sin_pronostico():
    filas = [tienda('S01', 100, 1.0), tienda('S01', 300, 0, en_celes=False),
             tienda('S02', 300, 0, en_celes=False, min_ex=2)]
    res = comprar(filas, [item(100), item(300)])
    r = res.df_item.set_index('item_code').loc[300]
    assert not r['en_celes']
    assert r['compra_sugerida'] == 0
    assert r['suma_min'] == pytest.approx(3.0)       # 1 (default) + 2
    assert any('no tienen datos en Celes' in a['mensaje'] for a in res.alertas)


def test_tienda_item_sin_fila_en_celes_no_recibe_compra():
    filas = [tienda('S01', 100, 1.0), tienda('S02', 100, 0, en_celes=False)]
    res = comprar(filas, [item(100)])
    f = fila(res, 'S02', 100)
    assert not f['con_datos']
    assert f['compra'] == 0
    assert f['motivo'] == 'Sin datos Celes'


def test_tiendas_sin_pedido_se_incluyen_por_defecto():
    filas = [tienda('S01', 100, 1.0), tienda('S02', 100, 1.0)]
    dfs = armar_dfs(filas, [item(100)], excluidas=['S02'])
    from core.compras import ejecutar_compra
    res = ejecutar_compra(dfs, distribucion([]), fecha=JUEVES)
    assert 'S02' in set(res.df_tienda['store_code'])
    res = ejecutar_compra(dfs, distribucion([]), fecha=JUEVES, overrides={'incluir_tiendas_sin_pedido': False})
    assert 'S02' not in set(res.df_tienda['store_code'])


# ── OC en tránsito y stock CEDI ──────────────────────────────────────────────

def test_oc_que_llega_con_la_compra_se_descuenta_completa():
    """Dos tiendas vacías de consumo 1: sin OC compran 2 + 2. Una OC de 3 cajas que llega al CEDI
    el mismo sábado que la compra entra entera: quedan 2 y 1 cajas → falta 1."""
    filas = [tienda('S01', 100, 1.0, min_ex=0), tienda('S02', 100, 1.0, min_ex=0)]
    assert compra_item(comprar(filas, [item(100)]), 100) == 4
    con_oc = comprar(filas, [item(100)], oc_df=oc([(100, 3, '2026-11-21')]))
    assert compra_item(con_oc, 100) == 1
    assert int(con_oc.df_tienda['oc_asignada'].sum()) == 3
    _cuadra(con_oc)


def test_oc_que_llega_antes_se_come_parte_con_la_venta_hasta_la_compra():
    """La OC llega al CEDI el viernes → tienda el sábado: el sábado se vende 1 caja en cada tienda
    con OC (jueves y viernes la tienda estaba en 0: esa venta se perdió, no sale de la OC).
    Tienda con 2 de OC llega con 1 → pide 1; tienda con 1 de OC llega con 0 → pide 2. Total 3."""
    filas = [tienda('S01', 100, 1.0, min_ex=0), tienda('S02', 100, 1.0, min_ex=0)]
    res = comprar(filas, [item(100)], oc_df=oc([(100, 3, '2026-11-20')]))
    assert compra_item(res, 100) == 3
    _cuadra(res)


def test_oc_que_llega_despues_de_la_compra_no_la_afecta():
    filas = [tienda('S01', 100, 1.0, min_ex=0)]
    res = comprar(filas, [item(100)], oc_df=oc([(100, 5, '2026-11-23')]))
    assert compra_item(res, 100) == 2
    assert res.df_item.set_index('item_code').loc[100, 'oc_posterior'] == 5


def test_oc_atrasada_sin_ingreso_en_stock_cuenta_como_si_llegara_hoy():
    """Entrega 17-nov (2 días de atraso ≤ DIAS_MAX_ATRASO_OC) y Stock sin ingreso del ítem desde
    entonces: se asume que llega hoy → tienda el viernes. 4 cajas − venta de vie y sáb = 2 = objetivo."""
    filas = [tienda('S01', 100, 1.0, min_ex=0)]
    res = comprar(filas, [item(100)], oc_df=oc([(100, 4, '2026-11-17')]))
    assert compra_item(res, 100) == 0
    assert res.oc_detalle['clasificacion'].tolist() == ['Atrasada']
    assert any('atrasadas' in a['mensaje'] for a in res.alertas)


def test_oc_vencida_hace_mucho_no_se_cuenta_y_alerta():
    filas = [tienda('S01', 100, 1.0, min_ex=0)]
    res = comprar(filas, [item(100)], oc_df=oc([(100, 4, '2026-11-10')]))
    assert compra_item(res, 100) == 2
    assert res.oc_detalle['clasificacion'].tolist() == ['Vencida sin recibir']
    assert any('vencidas' in a['mensaje'] and 'Siesa' in a['mensaje'] for a in res.alertas)


def test_oc_vencida_con_ingreso_en_stock_se_da_por_recibida():
    """Cant. entrada del export no se actualiza: si Stock registra un ingreso del ítem desde la fecha
    de entrega, la OC ya llegó (y está en el stock que se distribuyó hoy)."""
    filas = [tienda('S01', 100, 1.0, min_ex=0)]
    res = comprar(filas, [item(100, ultima_entrada='2026-11-19')], oc_df=oc([(100, 4, '2026-11-18')]))
    assert res.oc_detalle['clasificacion'].tolist() == ['Recibida según Stock']
    assert compra_item(res, 100) == 2


def test_formato_siesa_pendiente_estado_y_bodega():
    """Pendiente = Cant. ordenada − Cant. entrada; las cerradas y las de otra bodega no cuentan;
    sin Fecha de Entrega se usa Fecha orden + lead time."""
    filas = [tienda('S01', 100, 1.0, min_ex=0, existencias=0), tienda('S02', 100, 1.0, min_ex=0)]
    oc_df = pd.DataFrame({
        'Nro orden': ['OC1', 'OC2', 'OC3', 'OC4'],
        'Item': [100, 100, 100, 100],
        'Cant. ordenada': [5, 9, 9, 2],
        'Cant. entrada': [2, 0, 0, 0],
        'Estado': ['Aprobado', 'Anulado', 'Aprobado', 'Aprobado'],
        'Bodega': ['BOCES', 'BOCES', 'BOX99', 'BOCES'],
        'Fecha orden': ['2026-11-18', '2026-11-18', '2026-11-18', '2026-11-19'],
        'Fecha de Entrega': ['2026-11-21', '2026-11-21', '2026-11-21', None],
        'Fecha cumplido': [None, None, None, None],
        'Razón social proveedor': ['PROV A', 'PROV B', 'PROV C', 'PROV D'],
    })
    res = comprar(filas, [item(100)], oc_df=oc_df)
    d = res.oc_detalle.set_index('Nro orden')
    assert d.loc['OC1', 'clasificacion'] == 'En tránsito' and d.loc['OC1', 'pendiente'] == 3
    assert d.loc['OC2', 'clasificacion'] == 'Cerrada'
    assert d.loc['OC3', 'clasificacion'] == 'Otra bodega'
    assert d.loc['OC4', 'clasificacion'] == 'En tránsito'          # 19-nov + 2 días con recepción = 21-nov
    r = res.df_item.set_index('item_code').loc[100]
    assert r['oc_transito'] == 5                                   # 3 + 2
    assert r['proveedor'] == 'PROV D'                              # la OC más reciente


def test_sin_archivo_de_oc_alerta_riesgo_de_comprar_doble():
    res = comprar([tienda('S01', 100, 1.0)], [item(100)])
    assert any('Compras_Transito' in a['mensaje'] for a in res.alertas)


def test_oc_se_reparte_primero_a_las_agotadas():
    filas = [tienda('S01', 100, 1.0, existencias=0, min_ex=1),     # agotada
             tienda('S02', 100, 1.0, existencias=3.6, min_ex=1)]   # inv 0,6: sobre agotada
    res = comprar(filas, [item(100)], oc_df=oc([(100, 1, '2026-11-20')]))
    assert fila(res, 'S01', 100)['oc_asignada'] == 1
    assert fila(res, 'S02', 100)['oc_asignada'] == 0


def test_sin_distribucion_el_stock_cedi_cuenta_como_por_repartir():
    """Sin la distribución de hoy, el stock CEDI (4) se reparte mañana: llega el viernes y cubre
    vie + sáb (2) y los 2 días de la compra."""
    filas = [tienda('S01', 100, 1.0, min_ex=0)]
    res = comprar(filas, [item(100, cedi=4)])
    assert compra_item(res, 100) == 0
    assert any('sin el pedido de distribución' in a['mensaje'] for a in res.alertas)


def test_con_distribucion_el_stock_cedi_ya_esta_en_el_pedido_de_hoy():
    filas = [tienda('S01', 100, 1.0, min_ex=0)]
    res = comprar(filas, [item(100, cedi=2)], dist=distribucion([('S01', 100, 2)]))
    f = fila(res, 'S01', 100)
    assert f['pedido_hoy'] == 2
    assert res.df_item.set_index('item_code').loc[100, 'cedi_remanente'] == 0
    assert compra_item(res, 100) == 2                # inv 2 − 3 días de venta = 0 → 2 días de cobertura


# ── Suavizado, recortes, múltiplos ───────────────────────────────────────────

def _cuatro_tiendas_bajo_exhibicion():
    """inv = 0,8 − 0,3 = 0,5 (≥ 0,4: no agotada) con MIN 3 → 3 cajas cada una (bruto 12);
    venta de la cobertura = 4 × 0,2 = 0,8."""
    return [tienda(f'S0{i}', 100, 0.1, existencias=0.8, min_ex=3) for i in range(1, 5)]


def test_suavizado_al_subir_cierra_la_mitad_del_hueco():
    res = comprar(_cuatro_tiendas_bajo_exhibicion(), [item(100)])
    r = res.df_item.set_index('item_code').loc[100]
    assert r['compra_bruta'] == 12
    assert r['compra_sugerida'] == 6                 # 0,8 + 0,5 × (12 − 0,8) = 6,4 → 6
    _cuadra(res)


def test_alfa_subida_1_compra_todo_el_hueco():
    res = comprar(_cuatro_tiendas_bajo_exhibicion(), [item(100)], alfa_subida=1.0)
    assert compra_item(res, 100) == 12


def test_al_bajar_alfa_1_compra_solo_lo_que_piden_las_tiendas():
    filas = [tienda('S01', 100, 1.0, existencias=10, min_ex=0), tienda('S02', 100, 1.0, existencias=3, min_ex=0)]
    res = comprar(filas, [item(100)])
    r = res.df_item.set_index('item_code').loc[100]
    assert r['venta_esperada'] == pytest.approx(4.0)
    assert r['compra_sugerida'] == r['compra_bruta'] == 2


def test_recorte_por_suavizado_nunca_deja_agotadas_sin_compra():
    filas = [tienda(f'S0{i}', 100, 0.1, existencias=0, min_ex=3) for i in range(1, 5)]
    res = comprar(filas, [item(100)])
    assert compra_item(res, 100) == 12
    assert bool(res.df_item.set_index('item_code').loc[100, 'protegio_agotadas'])
    assert any('agotadas' in a['mensaje'] for a in res.alertas)


def test_recorte_empieza_por_reposicion_antes_que_exhibicion():
    """S09 (consumo 5): inv 16 − 15 = 1 → necesita 9 de reposición. Bruto 12 + 9 = 21, base 10,8 →
    suavizado 16: los 5 de recorte salen todos de la reposición; la exhibición queda completa."""
    filas = _cuatro_tiendas_bajo_exhibicion() + [tienda('S09', 100, 5.0, existencias=16, min_ex=0)]
    res = comprar(filas, [item(100)])
    assert compra_item(res, 100) == 16
    assert fila(res, 'S09', 100)['compra'] == 4
    assert all(fila(res, f'S0{i}', 100)['compra'] == 3 for i in range(1, 5))
    _cuadra(res)


def test_multiplo_y_minimo_de_compra_por_item():
    hojas = {'Por Ítem': pd.DataFrame({'ITEM': [100], 'Múltiplo compra (cajas)': [5],
                                       'Compra mínima (cajas)': [None]})}
    from core.compras import ejecutar_compra
    dfs = armar_dfs([tienda('S01', 100, 1.0, min_ex=0)], [item(100)])
    res = ejecutar_compra(dfs, distribucion([]), hojas_parametros=hojas, fecha=JUEVES,
                          overrides={'usar_ss_celes': False})
    assert compra_item(res, 100) == 5                 # 2 → múltiplo de 5
    _cuadra(res)

    hojas = {'Por Ítem': pd.DataFrame({'ITEM': [100], 'Compra mínima (cajas)': [8]})}
    res = ejecutar_compra(dfs, distribucion([]), hojas_parametros=hojas, fecha=JUEVES,
                          overrides={'usar_ss_celes': False})
    assert compra_item(res, 100) == 8


# ── Vida útil / último ingreso ───────────────────────────────────────────────

def test_ingreso_vencido_se_marca_y_por_defecto_no_cuenta_como_inventario():
    """Por defecto (INVENTARIO_SEGUN_VIDA_UTIL, decisión del usuario 19-nov) lo vencido no llega a la
    compra; con la regla «Celes tal cual» sí cuenta, y DESCONTAR_INVENTARIO_VENCIDO lo pone en 0 antes."""
    filas = [tienda('S01', 100, 0.1, existencias=2, min_ex=1, ultimo_ingreso='2026-11-01')]
    res = comprar(filas, [item(100, vida_util=5)])
    f = fila(res, 'S01', 100)
    assert bool(f['ingreso_vencido'])
    assert f['dias_desde_ingreso'] == 18
    assert f['existencias'] == 2 and f['inv_proy_llegada'] == 0 and f['compra'] == 1

    celes = comprar(filas, [item(100, vida_util=5)], inventario_segun_vida_util=False)
    assert fila(celes, 'S01', 100)['compra'] == 0

    res = comprar(filas, [item(100, vida_util=5)], inventario_segun_vida_util=False, descontar_inventario_vencido=True)
    f = fila(res, 'S01', 100)
    assert f['existencias'] == 0 and f['compra'] == 1


# ── Parámetros ───────────────────────────────────────────────────────────────

def test_sin_archivo_de_parametros_se_usan_los_valores_por_defecto():
    params, alertas = cargar_parametros(None)
    assert params == ParametrosCompra()
    assert alertas == []


def test_general_lee_porcentajes_booleanos_y_alerta_lo_invalido():
    hojas = {'General': pd.DataFrame({
        'Parámetro': ['CRECIMIENTO_VENTA (%)', 'CAPACIDAD_RECEPCION_FESTIVO', 'EXHIBIR_SIN_VENTA',
                      'ALFA_SUBIDA', 'UMBRAL_REDONDEO', 'NO_EXISTE'],
        'Valor': [10, 0.3, 'No', 'mucho', 3, 1],
    })}
    params, alertas = cargar_parametros(hojas)
    assert params.crecimiento_venta == pytest.approx(0.10)
    assert params.capacidad_festivo == pytest.approx(0.3)
    assert params.exhibir_sin_venta is False
    assert params.alfa_subida == ParametrosCompra().alfa_subida          # inválido → default
    assert params.umbral_redondeo == ParametrosCompra().umbral_redondeo  # fuera de rango → default
    textos = ' '.join(a['mensaje'] for a in alertas)
    assert 'mucho' in textos and 'umbral_redondeo' in textos and 'NO_EXISTE' in textos


def test_compra_por_dia_y_calendario():
    hojas = {
        'Compra por dia': pd.DataFrame({'Día': ['Sabado', 'Domingo'], 'Se compra (S/N)': ['No', 'Si'],
                                        'LT proveedor (días hábiles)': [None, 2], 'Factor venta': [None, 1.2]}),
        'Calendario': pd.DataFrame({'Fecha': ['24/12/2026'], 'Capacidad recepción CEDI (%)': [0],
                                    'Se compra (S/N)': [None], 'Factor venta': [1.4], 'Motivo': ['Nochebuena']}),
    }
    params, alertas = cargar_parametros(hojas)
    assert alertas == []
    assert 5 not in params.dias_compra and 6 in params.dias_compra
    assert params.factor_dia_semana == {6: 1.2}
    from datetime import date
    assert params.excepciones[date(2026, 12, 24)] == {'capacidad': 0.0, 'factor_venta': 1.4, 'motivo': 'Nochebuena'}


def test_plantilla_de_parametros_se_lee_igual_a_los_valores_por_defecto():
    portafolio = pd.DataFrame({'ITEM': [100, 200], 'DESCRIPCION': ['A', 'B'], 'ESTADO': ['ACTIVO', 'INACTIVO']})
    archivo = io.BytesIO(plantilla_parametros_bytes(portafolio, anios=(2026,)))
    hojas = load_purchase_files(parametros=archivo)['parametros_compra']
    assert list(hojas['Por Ítem']['ITEM']) == [100]
    params, alertas = cargar_parametros(hojas)
    assert alertas == []
    d = ParametrosCompra()
    for campo in ('lt_proveedor_cedi', 'capacidad_festivo', 'crecimiento_venta', 'umbral_redondeo',
                  'alfa_subida', 'alfa_bajada', 'exhibir_sin_venta', 'dias_compra'):
        assert getattr(params, campo) == getattr(d, campo)
    cal, cal_d = params.calendario(), d.calendario()
    from datetime import date, timedelta
    for i in range(365):
        dia = date(2026, 1, 1) + timedelta(days=i)
        assert cal.capacidad(dia) == cal_d.capacidad(dia)


def test_plantilla_de_oc_es_valida_y_vacia():
    hojas = load_purchase_files(compras_transito=io.BytesIO(plantilla_compras_transito_bytes()))
    assert len(hojas['compras_transito']) == 0


def test_loader_valida_columnas_de_oc():
    buf = io.BytesIO()
    pd.DataFrame({'Item': [100], 'Cantidad': [3]}).to_excel(buf, index=False)
    buf.seek(0)
    with pytest.raises(ValueError) as exc:
        load_purchase_files(compras_transito=buf)
    assert LABELS['compras_transito'] in str(exc.value)
    assert load_purchase_files() == {}


# ── Frecuencia de compra por ítem (DIA DE COMPRA) ────────────────────────────

LMV = 'LUNES-MIERCOLES-VIERNES'


def test_dias_fijos_no_se_compra_el_dia_que_no_le_toca():
    filas = [tienda('S01', 100, 1.0, min_ex=0)]
    res = comprar(filas, [item(100, dia_compra=LMV)])            # jueves
    f = fila(res, 'S01', 100)
    assert f['compra'] == 0
    assert f['motivo'].startswith('No se compra hoy') and '20/11' in f['motivo']
    r = res.df_item.set_index('item_code').loc[100]
    assert not r['se_compra_hoy'] and r['compra_sugerida'] == 0
    assert 'Hoy no se compra' in r['observacion']


@pytest.mark.parametrize('fecha, cobertura', [
    (date(2026, 11, 23), 2),     # lunes → llega jue 26; la del miércoles llega sáb 28: cubre jue + vie
    (date(2026, 11, 25), 3),     # miércoles → llega sáb 28; la del viernes llega mar 1-dic: sáb, dom, lun
    (date(2026, 11, 27), 2),     # viernes → llega mar 1-dic; la del lunes llega jue 3: mar + mié
])
def test_dias_fijos_cubren_hasta_la_siguiente_compra_del_item(fecha, cobertura):
    filas = [tienda('S01', 100, 1.0, min_ex=0)]
    res = comprar(filas, [item(100, dia_compra=LMV), item(200)], fecha=fecha)
    r = res.df_item.set_index('item_code')
    assert r.loc[100, 'n_dias_cobertura_item'] == cobertura
    assert r.loc[100, 'compra_sugerida'] == cobertura              # tienda vacía, consumo 1
    assert r.loc[200, 'n_dias_cobertura_item'] == res.plan.n_dias_cobertura


def test_cada_n_dias_no_compra_si_la_red_esta_cubierta():
    filas = [tienda('S01', 100, 1.0, existencias=20, min_ex=1)]
    res = comprar(filas, [item(100, dia_compra='CADA 15 DIAS', vida_util=365)])
    r = res.df_item.set_index('item_code').loc[100]
    assert r['compra_sugerida'] == 0 and not r['periodo_activado']
    assert 'hoy no hace falta' in r['observacion']


def test_cada_n_dias_se_activa_y_compra_el_periodo_completo_sin_suavizado():
    """Tienda vacía: se activa y compra 15 días de venta (con α↑ = 0,5 normal se recortaría)."""
    filas = [tienda('S01', 100, 1.0, existencias=0, min_ex=1)]
    res = comprar(filas, [item(100, dia_compra='CADA 15 DIAS', vida_util=365)])
    r = res.df_item.set_index('item_code').loc[100]
    assert r['periodo_activado']
    assert r['compra_sugerida'] == 15
    assert fila(res, 'S01', 100)['objetivo'] == pytest.approx(15.0)


def test_cada_n_dias_respeta_la_vida_util():
    filas = [tienda('S01', 100, 1.0, existencias=0, min_ex=1)]
    res = comprar(filas, [item(100, dia_compra='CADA 15 DIAS', vida_util=6)])
    assert compra_item(res, 100) == 6


def test_dia_de_compra_desconocido_usa_el_calendario_general_y_alerta():
    filas = [tienda('S01', 100, 1.0, min_ex=0)]
    res = comprar(filas, [item(100, dia_compra='quincenal')])
    assert compra_item(res, 100) == 2
    assert any('DIA DE COMPRA no reconocido' in a['mensaje'] for a in res.alertas)


# ── Inventario según vida útil (proyección por lotes) ────────────────────────

def test_vida_util_lo_ya_vencido_no_cuenta_como_exhibicion():
    filas = [tienda('S01', 100, 0.1, existencias=2, min_ex=1, ultimo_ingreso='2026-11-01')]   # 18 días, VU 5
    assert compra_item(comprar(filas, [item(100, vida_util=5)], inventario_segun_vida_util=False), 100) == 0
    res = comprar(filas, [item(100, vida_util=5)])                                               # por defecto
    assert fila(res, 'S01', 100)['inv_proy_llegada'] == 0
    assert compra_item(res, 100) == 1


def test_vida_util_lo_que_vence_antes_de_la_llegada_no_llega_a_la_cobertura():
    """Ingreso hace 3 días, vida útil 5: el domingo (3 días más) tendrá 6 días → vencido. Las 10 cajas
    sirven para vender jue–sáb, pero el resto no llega vendible al domingo."""
    filas = [tienda('S01', 100, 1.0, existencias=10, min_ex=0, ultimo_ingreso='2026-11-16')]
    assert compra_item(comprar(filas, [item(100, vida_util=5)], inventario_segun_vida_util=False), 100) == 0
    res = comprar(filas, [item(100, vida_util=5)])
    assert fila(res, 'S01', 100)['inv_proy_llegada'] == 0
    assert compra_item(res, 100) == 2


def test_vida_util_corta_tambien_vence_el_transito():
    """Vida útil 2: el tránsito llega hoy y el domingo tiene 3 días → vencido."""
    filas = [tienda('S01', 100, 0.5, existencias=0, transito=3, min_ex=0)]
    assert compra_item(comprar(filas, [item(100, vida_util=2)], inventario_segun_vida_util=False), 100) == 0
    res = comprar(filas, [item(100, vida_util=2)])
    assert compra_item(res, 100) == 1


def test_el_pedido_de_hoy_llega_manana():
    """Tienda vacía: la venta de hoy se pierde (el pedido llega mañana), no se descuenta del pedido."""
    filas = [tienda('S01', 100, 1.0, existencias=0, min_ex=0)]
    res = comprar(filas, [item(100)], dist=distribucion([('S01', 100, 3)]))
    assert fila(res, 'S01', 100)['inv_proy_llegada'] == pytest.approx(1.0)    # 3 − vie − sáb
    assert compra_item(res, 100) == 1


def test_existencias_de_la_compra_salen_de_inventario_tiendas_y_no_de_celes():
    """Celes dice 0 existencias pero Siesa 3: inv = 3 + 1 (tránsito Celes) − 3 días = 1. Objetivo 2 → compra 1."""
    filas = [tienda('S01', 100, 1.0, existencias=3, transito=1, min_ex=1)]
    dfs = armar_dfs(filas, [item(100)])
    dfs['celes']['Inventario Disponible en esta Tienda (Unidades de Distibución)'] = 0.0
    from core.compras import ejecutar_compra
    res = ejecutar_compra(dfs, distribucion([]), fecha=JUEVES, overrides={'usar_ss_celes': False})
    f = fila(res, 'S01', 100)
    assert f['existencias'] == pytest.approx(3.0)
    assert f['transito'] == pytest.approx(1.0)
    assert f['inv_proy_llegada'] == pytest.approx(1.0)
    assert f['compra'] == 1


def test_la_compra_no_cuenta_dos_veces_el_traslado_ya_recibido_en_siesa():
    """Celes (último ingreso 18-nov): 0 + 2 en tránsito; Siesa (ingreso 19-nov): 2 → el traslado ya entró.
    inv = 2 + 0 − 3 días = 0 (no 1). Objetivo 2 → compra 2."""
    filas = [tienda('S01', 100, 1.0, existencias=2, transito=2, min_ex=1, ultimo_ingreso='2026-11-19')]
    dfs = armar_dfs(filas, [item(100)])
    dfs['celes']['Inventario Disponible en esta Tienda (Unidades de Distibución)'] = 0.0
    dfs['celes']['Última Fecha de Ingreso'] = pd.Timestamp('2026-11-18')
    from core.compras import ejecutar_compra
    res = ejecutar_compra(dfs, distribucion([]), fecha=JUEVES, overrides={'usar_ss_celes': False})
    f = fila(res, 'S01', 100)
    assert (f['existencias'], f['transito'], f['transito_ya_recibido']) == pytest.approx((2.0, 0.0, 2.0))
    assert f['inv_proy_llegada'] == pytest.approx(0.0)
    assert f['compra'] == 2

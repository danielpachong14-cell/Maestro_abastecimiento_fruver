"""Tests unitarios del motor de distribución."""

import pandas as pd
import pytest

from core.algorithm import (
    MIN_STOCK_AGOTADO,
    MIN_STOCK_SAFETY,
    TARGET_DAYS,
    distribuir_item,
)


def _store(store_code, consumo, dias, existencias=0.0, transito=0.0):
    """Construye una fila de tienda-ítem para el algoritmo.

    consumo, existencias y transito están en cajas (Unidades de Distribución).
    `dias` es dias_proyectados = inventario_efectivo / consumo.
    """
    inv_efectivo = existencias + transito
    return {
        'store_code': store_code,
        'consumo_diario': consumo,
        'dias_proyectados': dias,
        'inventario_tienda': existencias,
        'inventario_transito': transito,
        'inventario_efectivo': inv_efectivo,
    }


def _df(rows, item_code='X', cajas=10):
    df = pd.DataFrame(rows)
    df['item_code'] = item_code
    df['cajas_disponibles_cedi'] = cajas
    df['store_name'] = df['store_code']
    df['um'] = 'UND'
    df['item_desc'] = 'Producto X'
    return df


def test_distribucion_100_pct():
    """La suma de cajas asignadas debe igualar las cajas disponibles."""
    rows = [_store('A', 1.0, 0.1), _store('B', 0.5, 1.0), _store('C', 0.2, 2.0)]
    tiendas, remaining = distribuir_item('X', 25, _df(rows))
    assert remaining == 0
    assert int(tiendas['cajas_asignadas'].sum()) == 25


def test_priority_agotado():
    """Tienda con inventario_efectivo < MIN_STOCK_AGOTADO siempre recibe >= 1 caja."""
    # existencias=0, transito=0 → inventario_efectivo=0 < 0.3 → AGOTADA
    rows = [_store('AGOTADA', 0.4, 0.0, existencias=0.0, transito=0.0),
            _store('OK', 5.0, 10.0, existencias=50.0)]
    tiendas, _ = distribuir_item('X', 8, _df(rows, cajas=8))
    asignada = tiendas.loc[tiendas['store_code'] == 'AGOTADA', 'cajas_asignadas'].iloc[0]
    assert asignada >= 1


def test_priority_safety_stock():
    """Tienda con MIN_STOCK_AGOTADO <= inventario_efectivo < MIN_STOCK_SAFETY recibe >= 1 caja."""
    # existencias+transito entre 0.3 y 0.5 → STOCK SEGURIDAD
    stock_medio = (MIN_STOCK_AGOTADO + MIN_STOCK_SAFETY) / 2
    rows = [_store('SAFETY', 0.4, stock_medio / 0.4, existencias=stock_medio, transito=0.0),
            _store('OK', 5.0, 10.0, existencias=50.0)]
    tiendas, _ = distribuir_item('X', 8, _df(rows, cajas=8))
    asignada = tiendas.loc[tiendas['store_code'] == 'SAFETY', 'cajas_asignadas'].iloc[0]
    assert asignada >= 1


def test_no_portafolio_violation():
    """Solo se distribuye a las tiendas presentes en el DataFrame de entrada
    (la elegibilidad viene de Tiendas × Ítem; el algoritmo no inventa tiendas)."""
    rows = [_store('A', 1.0, 0.1), _store('B', 1.0, 0.1)]
    df_in = _df(rows, cajas=6)
    tiendas, _ = distribuir_item('X', 6, df_in)
    assert set(tiendas['store_code']) == {'A', 'B'}


def test_consumo_cero():
    """Si todas las tiendas tienen consumo 0, igual se distribuye todo."""
    rows = [_store('A', 0.0, float('inf')), _store('B', 0.0, float('inf')),
            _store('C', 0.0, float('inf'))]
    tiendas, remaining = distribuir_item('X', 7, _df(rows, cajas=7))
    assert remaining == 0
    assert int(tiendas['cajas_asignadas'].sum()) == 7


def test_single_store():
    """Con una sola tienda elegible, recibe todas las cajas."""
    rows = [_store('UNICA', 2.0, 0.5)]
    tiendas, remaining = distribuir_item('X', 50, _df(rows, cajas=50))
    assert remaining == 0
    assert int(tiendas['cajas_asignadas'].iloc[0]) == 50


def test_max_excedido():
    """Con muchas cajas disponibles, el cap se eleva hasta distribuir todo."""
    rows = [_store('A', 3.0, 0.1), _store('B', 2.0, 0.2)]
    tiendas, remaining = distribuir_item('X', 500, _df(rows, cajas=500))
    assert remaining == 0
    assert int(tiendas['cajas_asignadas'].sum()) == 500


def test_inventario_efectivo_descuenta_transito():
    """Una tienda cubierta (exist+tránsito >= TARGET_DAYS*consumo) no pide en el
    pase 1; el sobrante va a la tienda agotada."""
    consumo = 1.0
    # existencias = TARGET_DAYS * consumo = 4 cajas → 4 días exactos → necesidad 0.
    cubierta = _store('CUBIERTA', consumo, TARGET_DAYS,
                      existencias=TARGET_DAYS * consumo, transito=0.0)
    # Tienda agotada (inventario_efectivo=0): prioridad máxima.
    agotada = _store('AGOTADA', consumo, 0.0, existencias=0.0, transito=0.0)
    tiendas, remaining = distribuir_item('X', 2, _df([cubierta, agotada], cajas=2))
    assert remaining == 0
    agotada_asig = tiendas.loc[tiendas['store_code'] == 'AGOTADA', 'cajas_asignadas'].iloc[0]
    assert agotada_asig >= 1



def test_surplus_prioriza_menos_stock():
    """Sobrante va primero a la tienda con menos días proyectados aunque tenga
    consumo ligeramente menor.

    Tienda A: consumo=1.0, stock=7 cajas → 7 días proyectados
    Tienda B: consumo=0.9, stock=3 cajas → 3.3 días proyectados
    → la caja sobrante debe ir a B (menos días), no a A (más consumo).
    """
    # Pase 1 no asigna nada: ambas superan TARGET_DAYS (4) o quedan en necesidad
    # pequeña. Forzamos la situación: días proyectados pre-distribución:
    #   A: 7/1.0 = 7.0 → priority 0 (cubierta)
    #   B: 3/0.9 = 3.3 → priority 1 (necesita)
    # Con 1 sola caja disponible, el algoritmo debe dársela a B.
    a = _store('A', consumo=1.0, dias=7.0, existencias=7.0)
    b = _store('B', consumo=0.9, dias=3.33, existencias=3.0)
    tiendas, remaining = distribuir_item('X', 1, _df([a, b], cajas=1))
    assert remaining == 0
    b_asig = tiendas.loc[tiendas['store_code'] == 'B', 'cajas_asignadas'].iloc[0]
    a_asig = tiendas.loc[tiendas['store_code'] == 'A', 'cajas_asignadas'].iloc[0]
    assert b_asig >= 1, "B debía recibir la caja (menos días proyectados)"
    assert a_asig == 0, "A no debía recibir (ya cubierta con más días)"


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))

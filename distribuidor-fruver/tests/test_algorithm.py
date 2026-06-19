"""Tests unitarios del motor de distribución."""

import pandas as pd
import pytest

from core.algorithm import (
    MAX_CAJAS_POR_ITEM,
    MIN_STOCK_AGOTADO,
    MIN_STOCK_SAFETY,
    TARGET_DAYS,
    TOPE_EXCEDENTE,
    UMBRAL_CONCENTRACION_SIN_CONSUMO,
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
    tiendas, remaining, _ = distribuir_item('X', 25, _df(rows))
    assert remaining == 0
    assert int(tiendas['cajas_asignadas'].sum()) == 25


def test_priority_agotado():
    """Tienda con inventario_efectivo < MIN_STOCK_AGOTADO siempre recibe >= 1 caja."""
    # existencias=0, transito=0 → inventario_efectivo=0 < 0.3 → AGOTADA
    rows = [_store('AGOTADA', 0.4, 0.0, existencias=0.0, transito=0.0),
            _store('OK', 5.0, 10.0, existencias=50.0)]
    tiendas, _, _ = distribuir_item('X', 8, _df(rows, cajas=8))
    asignada = tiendas.loc[tiendas['store_code'] == 'AGOTADA', 'cajas_asignadas'].iloc[0]
    assert asignada >= 1


def test_priority_safety_stock():
    """Tienda con MIN_STOCK_AGOTADO <= inventario_efectivo < MIN_STOCK_SAFETY recibe >= 1 caja."""
    # existencias+transito entre 0.3 y 0.5 → STOCK SEGURIDAD
    stock_medio = (MIN_STOCK_AGOTADO + MIN_STOCK_SAFETY) / 2
    rows = [_store('SAFETY', 0.4, stock_medio / 0.4, existencias=stock_medio, transito=0.0),
            _store('OK', 5.0, 10.0, existencias=50.0)]
    tiendas, _, _ = distribuir_item('X', 8, _df(rows, cajas=8))
    asignada = tiendas.loc[tiendas['store_code'] == 'SAFETY', 'cajas_asignadas'].iloc[0]
    assert asignada >= 1


def test_no_portafolio_violation():
    """Solo se distribuye a las tiendas presentes en el DataFrame de entrada
    (la elegibilidad viene de Tiendas × Ítem; el algoritmo no inventa tiendas)."""
    rows = [_store('A', 1.0, 0.1), _store('B', 1.0, 0.1)]
    df_in = _df(rows, cajas=6)
    tiendas, _, _ = distribuir_item('X', 6, df_in)
    assert set(tiendas['store_code']) == {'A', 'B'}


def test_consumo_cero():
    """Si todas las tiendas tienen consumo 0, igual se distribuye todo."""
    rows = [_store('A', 0.0, float('inf')), _store('B', 0.0, float('inf')),
            _store('C', 0.0, float('inf'))]
    tiendas, remaining, _ = distribuir_item('X', 7, _df(rows, cajas=7))
    assert remaining == 0
    assert int(tiendas['cajas_asignadas'].sum()) == 7


def test_single_store():
    """Con una sola tienda elegible, recibe todas las cajas."""
    rows = [_store('UNICA', 2.0, 0.5)]
    tiendas, remaining, _ = distribuir_item('X', 50, _df(rows, cajas=50))
    assert remaining == 0
    assert int(tiendas['cajas_asignadas'].iloc[0]) == 50


def test_max_excedido():
    """Con muchas cajas disponibles, el cap se eleva hasta distribuir todo."""
    rows = [_store('A', 3.0, 0.1), _store('B', 2.0, 0.2)]
    tiendas, remaining, _ = distribuir_item('X', 500, _df(rows, cajas=500))
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
    tiendas, remaining, _ = distribuir_item('X', 2, _df([cubierta, agotada], cajas=2))
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
    tiendas, remaining, _ = distribuir_item('X', 1, _df([a, b], cajas=1))
    assert remaining == 0
    b_asig = tiendas.loc[tiendas['store_code'] == 'B', 'cajas_asignadas'].iloc[0]
    a_asig = tiendas.loc[tiendas['store_code'] == 'A', 'cajas_asignadas'].iloc[0]
    assert b_asig >= 1, "B debía recibir la caja (menos días proyectados)"
    assert a_asig == 0, "A no debía recibir (ya cubierta con más días)"


def test_surplus_no_amplifica_brecha_de_dias():
    """El sobrante no debe ampliar la brecha de días entre tiendas lentas y
    rápidas — el problema reportado por el usuario (ítem 157: tiendas de bajo
    consumo terminaban con MUCHOS más días que las de alto consumo).

    Repartir la MISMA CANTIDAD DE CAJAS reparte cantidades MUY DISTINTAS de
    DÍAS (Δdías ≈ cajas/consumo): la tienda lenta (consumo bajo) ganaría ~9
    días por caja mientras la rápida ganaría ~0.6 — ampliando la brecha en
    vez de cerrarla. El reparto correcto pondera por
    `consumo_diario / días_actuales`, así que LENTA (ya muy adelantada, con
    poco peso) no debe recibir más cajas que RAPIDA (más consumo y más
    atrasada), y la brecha de días entre ambas no debe crecer.

      LENTA:  consumo=0.2, stock=3 → 15.0 días (muy cubierta)
      MEDIA:  consumo=1.0, stock=4 →  4.0 días
      RAPIDA: consumo=1.8, stock=6 →  3.3 días (la más atrasada)
    """
    lenta = _store('LENTA', consumo=0.2, dias=15.0, existencias=3.0)
    media = _store('MEDIA', consumo=1.0, dias=4.0, existencias=4.0)
    rapida = _store('RAPIDA', consumo=1.8, dias=3.33, existencias=6.0)
    tiendas, remaining, _ = distribuir_item('X', 6, _df([lenta, media, rapida], cajas=6))
    assert remaining == 0

    lenta_row = tiendas.loc[tiendas['store_code'] == 'LENTA'].iloc[0]
    rapida_row = tiendas.loc[tiendas['store_code'] == 'RAPIDA'].iloc[0]

    assert rapida_row['cajas_asignadas'] >= lenta_row['cajas_asignadas'], (
        "RAPIDA (más consumo, más atrasada en días) no debía recibir menos "
        "cajas que LENTA (menos consumo, ya muy adelantada)"
    )

    dias_final_lenta = ((lenta_row['inventario_efectivo'] + lenta_row['cajas_asignadas'])
                        / lenta_row['consumo_diario'])
    dias_final_rapida = ((rapida_row['inventario_efectivo'] + rapida_row['cajas_asignadas'])
                         / rapida_row['consumo_diario'])
    brecha_inicial = lenta_row['dias_proyectados'] - rapida_row['dias_proyectados']
    brecha_final = dias_final_lenta - dias_final_rapida
    assert brecha_final <= brecha_inicial, (
        "La brecha de días entre LENTA y RAPIDA no debía ampliarse "
        f"(inicial={brecha_inicial:.2f}, final={brecha_final:.2f})"
    )


def test_excedente_respeta_tope_para_todas_las_tiendas():
    """El reparto del sobrante (Fase 2b) no debe dejar a NINGUNA tienda —
    de alto, medio o bajo consumo — por encima de TOPE_EXCEDENTE días, ni
    recibiendo más de MAX_CAJAS_POR_ITEM cajas, cuando el sobrante alcanza
    para que el "Paso A" (proporcional, con tope) resuelva todo sin caer
    al "Paso B" de cero-residuo. Es la validación directa de la preocupación
    del usuario: que las tiendas de consumo medio no terminen sobre-stockeadas.

    Las 3 tiendas arrancan EXACTAMENTE en TARGET_DAYS (cubiertas, no piden
    nada en Fase 1/2a). El sobrante (7 cajas) coincide con la suma exacta
    del "espacio" que cada una tiene bajo sus topes:
      ALTA  (consumo=3.0, 9.0 cj =  3.0d): tope de cajas (MAX=3) manda   → cabe hasta +3
      MEDIA (consumo=1.0, 3.0 cj =  3.0d): tope de cajas (MAX=3) manda   → cabe hasta +3
      BAJA  (consumo=0.5, 1.5 cj =  3.0d): tope de días (6.0d) manda     → cabe hasta +1
    """
    alta = _store('ALTA', consumo=3.0, dias=TARGET_DAYS, existencias=9.0)
    media = _store('MEDIA', consumo=1.0, dias=TARGET_DAYS, existencias=3.0)
    baja = _store('BAJA', consumo=0.5, dias=TARGET_DAYS, existencias=1.5)
    tiendas, remaining, _ = distribuir_item('X', 7, _df([alta, media, baja], cajas=7))
    assert remaining == 0
    assert int(tiendas['cajas_asignadas'].sum()) == 7

    for _, row in tiendas.iterrows():
        dias_final = ((row['inventario_efectivo'] + row['cajas_asignadas'])
                      / row['consumo_diario'])
        assert dias_final <= TOPE_EXCEDENTE + 1e-9, (
            f"{row['store_code']} terminó con {dias_final:.2f} días — "
            f"supera TOPE_EXCEDENTE ({TOPE_EXCEDENTE})"
        )
        assert row['cajas_asignadas'] <= MAX_CAJAS_POR_ITEM, (
            f"{row['store_code']} recibió {row['cajas_asignadas']} cajas — "
            f"supera MAX_CAJAS_POR_ITEM ({MAX_CAJAS_POR_ITEM}) sin que se "
            "activara el Paso B de cero-residuo"
        )


def test_paso_b_activa_y_reparte_por_menor_dias_actuales():
    """Caso extremo (raro en la práctica, según CLAUDE.md): el CEDI manda
    MUCHO más de lo que las tiendas elegibles pueden absorber bajo sus topes.
    Todas quedan inelegibles para el Paso A antes de agotar el sobrante, así
    que el "Paso B" debe activarse para garantizar cero residuo — repartiendo
    lo que falta por menor `días_actuales` e ignorando MAX_CAJAS_POR_ITEM
    (algorithm.py:247-260; la garantía de cero residuo manda sobre el tope).

      P: consumo=2.0, existencias=0   → AGOTADA; en Fase 1 llega a su tope
         de 3 cajas y queda en 1.5 días (muy atrasada, va a la cabeza del
         round-robin de Paso B)
      Q: consumo=1.0, existencias=6.0 → CUBIERTA; arranca ya en 6.0 días
         (= TOPE_EXCEDENTE), inelegible para el sobrante desde la ronda 1

    Ninguna sigue elegible para el Paso A (P por tope de cajas, Q por tope
    de días) → Paso B reparte las 5 cajas restantes alternando, empezando
    por P (menos días_actuales) — por eso P termina con más que Q (3 vs 2).
    Este es justamente el mecanismo que puede generar sobre-stock visible:
    no es un defecto del algoritmo, es la garantía de cero residuo chocando
    con un sobrante que excede lo que el portafolio de tiendas puede tomar
    — exactamente lo que "Análisis Comprador" existe para señalar.
    """
    p = _store('P', consumo=2.0, dias=0.0, existencias=0.0)
    q = _store('Q', consumo=1.0, dias=6.0, existencias=6.0)
    tiendas, remaining, _ = distribuir_item('X', 8, _df([p, q], cajas=8))
    assert remaining == 0
    assert int(tiendas['cajas_asignadas'].sum()) == 8

    p_row = tiendas.loc[tiendas['store_code'] == 'P'].iloc[0]
    q_row = tiendas.loc[tiendas['store_code'] == 'Q'].iloc[0]

    # Paso B se activó: P superó MAX_CAJAS_POR_ITEM, algo imposible en Paso A
    # (su `room` siempre está acotado por `MAX_CAJAS_POR_ITEM - cajas_asignadas`).
    assert p_row['cajas_asignadas'] > MAX_CAJAS_POR_ITEM, (
        "Se esperaba que el Paso B ignorara MAX_CAJAS_POR_ITEM para P"
    )
    assert q_row['cajas_fase2b'] > 0, "Q también debía recibir parte del residuo"

    # P iba más atrasada (1.5 días tras Fase 1, frente a los 6.0 de Q) →
    # el round-robin de Paso B la prioriza, dándole el primer (y por tanto
    # más) reparto de las 5 cajas restantes (3 vs 2).
    assert p_row['cajas_fase2b'] > q_row['cajas_fase2b'], (
        "El Paso B debía priorizar a P (menor días_actuales) sobre Q "
        f"(P recibió {p_row['cajas_fase2b']}, Q recibió {q_row['cajas_fase2b']})"
    )


def test_frontera_tope_excedente():
    """La elegibilidad para el sobrante mira el estado TRAS recibir una caja
    más (`dias_tras_una <= TOPE_EXCEDENTE`, algorithm.py:225-234) — no el
    estado actual. Una tienda que terminaría EXACTAMENTE en el tope sigue
    siendo elegible; una que lo cruzaría por una décima de día, no.

      F6:  consumo=0.5, existencias=2.0 → tras +1 caja: (2.0+1)/0.5 = 6.0 (== tope)
      F61: consumo=0.5, existencias=2.1 → tras +1 caja: (2.1+1)/0.5 = 6.2 (> tope)

    Ambas arrancan cubiertas (no piden nada en Fase 1/2a) y compiten por la
    única caja de sobrante: debe ir a F6 (queda justo en el límite, 6.0d) y
    no a F61 (lo superaría).
    """
    f6 = _store('F6', consumo=0.5, dias=4.0, existencias=2.0)
    f61 = _store('F61', consumo=0.5, dias=4.2, existencias=2.1)
    tiendas, remaining, _ = distribuir_item('X', 1, _df([f6, f61], cajas=1))
    assert remaining == 0

    f6_row = tiendas.loc[tiendas['store_code'] == 'F6'].iloc[0]
    f61_row = tiendas.loc[tiendas['store_code'] == 'F61'].iloc[0]

    assert f6_row['cajas_asignadas'] == 1, (
        "F6 queda justo en TOPE_EXCEDENTE tras recibir — debía ser elegible"
    )
    assert f61_row['cajas_asignadas'] == 0, (
        "F61 superaría TOPE_EXCEDENTE tras recibir — no debía ser elegible"
    )

    dias_final_f6 = ((f6_row['inventario_efectivo'] + f6_row['cajas_asignadas'])
                     / f6_row['consumo_diario'])
    assert dias_final_f6 == pytest.approx(TOPE_EXCEDENTE)


def test_concentracion_por_falta_de_consumo_activa_reparto_equitativo():
    """Si Paso B concentraría >= UMBRAL_CONCENTRACION_SIN_CONSUMO cajas en una
    sola tienda porque es la única con consumo>0 en un portafolio más amplio,
    debe activarse reparto equitativo entre TODAS las tiendas del portafolio
    y generarse una alerta ADVERTENCIA (el caso real reportado: ítem 1815,
    SA2 era la única tienda con consumo de 32 en el portafolio).
    """
    unica = _store('UNICA', consumo=5.0, dias=0.0, existencias=0.0)
    sin_consumo = [_store(f'SC{i}', consumo=0.0, dias=float('inf'), existencias=0.0)
                   for i in range(4)]
    rows = [unica] + sin_consumo
    tiendas, remaining, alertas_item = distribuir_item('X', 31, _df(rows, cajas=31))

    assert remaining == 0
    assert int(tiendas['cajas_asignadas'].sum()) == 31

    unica_asig = tiendas.loc[tiendas['store_code'] == 'UNICA', 'cajas_asignadas'].iloc[0]
    assert unica_asig < UMBRAL_CONCENTRACION_SIN_CONSUMO + MAX_CAJAS_POR_ITEM, (
        "UNICA no debía concentrar la mayoría del remanente"
    )

    for code in ['SC0', 'SC1', 'SC2', 'SC3']:
        asignada = tiendas.loc[tiendas['store_code'] == code, 'cajas_asignadas'].iloc[0]
        assert asignada > 0, f"{code} debía recibir parte del reparto equitativo"

    asigs = tiendas['cajas_asignadas']
    assert asigs.max() - asigs.min() <= 1, "El reparto equitativo no quedó parejo"

    assert len(alertas_item) == 1
    assert alertas_item[0]['tipo'] == 'ADVERTENCIA'
    assert alertas_item[0]['tiendas_sin_consumo'] == 4
    assert alertas_item[0]['tiendas_con_consumo'] == 1
    assert alertas_item[0]['tienda_concentracion_original'] == 'UNICA'


def test_sin_concentracion_no_genera_alerta():
    """Cuando el remanente bajo Paso B no llegaría al umbral de concentración
    (o todas las tiendas tienen consumo), no debe generarse ninguna alerta y
    el Paso B normal (por menor días_actuales) debe seguir aplicando."""
    p = _store('P', consumo=2.0, dias=0.0, existencias=0.0)
    q = _store('Q', consumo=1.0, dias=6.0, existencias=6.0)
    tiendas, remaining, alertas_item = distribuir_item('X', 8, _df([p, q], cajas=8))
    assert remaining == 0
    assert alertas_item == []


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))

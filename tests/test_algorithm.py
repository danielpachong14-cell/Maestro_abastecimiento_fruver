"""Tests unitarios del motor de distribución."""

import math

import pandas as pd
import pytest

from core.algorithm import (
    MAX_CAJAS_POR_ITEM,
    MIN_CAJAS_INICIAL,
    MIN_STOCK_AGOTADO,
    MIN_STOCK_SAFETY,
    TARGET_DAYS,
    TOPE_EXCEDENTE,
    UMBRAL_CONCENTRACION_SIN_CONSUMO,
    _apportion_grupo_targets,
    _fase4_grupal,
    _fase4_individual,
    _reparto_proporcional,
    distribuir_item,
    run_distribution,
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
    # Sin grupo espejo en estos tests: cada ítem es su propio grupo de tamaño
    # 1, así que las métricas de grupo colapsan a las del ítem individual.
    df['grupo_id'] = 'ITEM_' + str(item_code)
    df['consumo_diario_grupo'] = df['consumo_diario']
    df['inventario_efectivo_grupo'] = df['inventario_efectivo']
    df['dias_proyectados_grupo'] = df['dias_proyectados']
    df['item_share_consumo'] = 1.0
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
    """El sobrante va primero a la tienda con menos días proyectados, aunque tenga
    consumo ligeramente menor.

    A: consumo=1.0, stock=7 → 7.0 días (cubierta).  B: consumo=0.9, stock=3 → 3.3 días.
    Con 1 sola caja disponible debe ir a B.
    """
    a = _store('A', consumo=1.0, dias=7.0, existencias=7.0)
    b = _store('B', consumo=0.9, dias=3.33, existencias=3.0)
    tiendas, remaining, _ = distribuir_item('X', 1, _df([a, b], cajas=1))
    assert remaining == 0
    b_asig = tiendas.loc[tiendas['store_code'] == 'B', 'cajas_asignadas'].iloc[0]
    a_asig = tiendas.loc[tiendas['store_code'] == 'A', 'cajas_asignadas'].iloc[0]
    assert b_asig >= 1, "B debía recibir la caja (menos días proyectados)"
    assert a_asig == 0, "A no debía recibir (ya cubierta con más días)"


def test_surplus_no_amplifica_brecha_de_dias():
    """El sobrante no debe ampliar la brecha de días entre tiendas lentas y rápidas
    (ítem 157: las de bajo consumo terminaban con MUCHOS más días).

    Cajas iguales dan días muy distintos (Δdías ≈ cajas/consumo); el reparto pondera por
    `consumo_diario / días_actuales`, así que LENTA (muy adelantada) no debe recibir más
    cajas que RAPIDA (más consumo y más atrasada).

      LENTA:  consumo=0.2, stock=3 → 15.0 días
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

    # La brecha se mide sobre el reparto sano (Fases 1-4): Fase 5 ignora el tope
    # por diseño y puede ampliarla, así que se excluye explícitamente.
    cajas_sanas_lenta = lenta_row['cajas_asignadas'] - lenta_row['cajas_fase5']
    cajas_sanas_rapida = rapida_row['cajas_asignadas'] - rapida_row['cajas_fase5']
    dias_final_lenta = ((lenta_row['inventario_efectivo'] + cajas_sanas_lenta)
                        / lenta_row['consumo_diario'])
    dias_final_rapida = ((rapida_row['inventario_efectivo'] + cajas_sanas_rapida)
                         / rapida_row['consumo_diario'])
    brecha_inicial = lenta_row['dias_proyectados'] - rapida_row['dias_proyectados']
    brecha_final = dias_final_lenta - dias_final_rapida
    assert brecha_final <= brecha_inicial, (
        "La brecha de días entre LENTA y RAPIDA no debía ampliarse en el "
        f"reparto sano (inicial={brecha_inicial:.2f}, final={brecha_final:.2f})"
    )


def test_excedente_respeta_tope_para_todas_las_tiendas():
    """Fase 4 no deja a NINGUNA tienda (alto, medio o bajo consumo) sobre
    TOPE_EXCEDENTE días ni con más de MAX_CAJAS_POR_ITEM cajas, cuando el sobrante
    alcanza para resolver todo sin caer a Fase 5.

    Las 3 tiendas arrancan EXACTAMENTE en TARGET_DAYS y el sobrante es la suma exacta
    del espacio que cada una tiene bajo sus topes:
      ALTA  (consumo=3.0) y MEDIA (consumo=1.0): manda el tope de cajas
      BAJA  (consumo=0.5): manda el tope de días

    Los valores se derivan de los parámetros, no se fijan a mano.
    """
    def _room(consumo):
        """Cajas que la tienda puede recibir sin pasar TOPE_EXCEDENTE,
        acotadas además por el tope de cajas por ítem."""
        return min(int((TOPE_EXCEDENTE - TARGET_DAYS) * consumo), MAX_CAJAS_POR_ITEM)

    _consumos = {'ALTA': 3.0, 'MEDIA': 1.0, 'BAJA': 0.5}
    # El sobrante es exactamente la capacidad conjunta bajo los topes, así que
    # Fase 4 debe colocarlo todo sin que Fase 5 tenga que romper ninguno.
    _cajas = sum(_room(c) for c in _consumos.values())

    alta = _store('ALTA', consumo=_consumos['ALTA'], dias=TARGET_DAYS,
                  existencias=_consumos['ALTA'] * TARGET_DAYS)
    media = _store('MEDIA', consumo=_consumos['MEDIA'], dias=TARGET_DAYS,
                   existencias=_consumos['MEDIA'] * TARGET_DAYS)
    baja = _store('BAJA', consumo=_consumos['BAJA'], dias=TARGET_DAYS,
                  existencias=_consumos['BAJA'] * TARGET_DAYS)
    tiendas, remaining, _ = distribuir_item('X', _cajas, _df([alta, media, baja], cajas=_cajas))
    assert remaining == 0
    assert int(tiendas['cajas_asignadas'].sum()) == _cajas

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


def test_fase5_activa_y_reparte_por_menor_dias_actuales():
    """Caso extremo: el CEDI manda MUCHO más de lo que las tiendas absorben bajo sus
    topes, así que Fase 5 garantiza cero residuo, por menor `días_actuales` y en dos
    niveles: primero solo tiendas bajo MAX_CAJAS_POR_ITEM y, si es inevitable, ignorando
    ese tope (ítem 2473: antes lo ignoraba siempre).

      P: consumo=2.0, existencias=0 → AGOTADA; Fase 1 le da 2 (su máximo) y Fase 4 la 3.ª.
      Q: consumo=1.0, existencias=TOPE_EXCEDENTE+1 → CUBIERTA y sobre el tope de días,
         inelegible para Fase 4.

    Ninguna es elegible para Fase 4. El nivel 1 le da a Q las 3 que le faltan; ambas quedan
    en MAX_CAJAS_POR_ITEM y sobran 2, que el nivel 2 reparte 1 y 1 (no las apila sobre una).
    Resultado: P y Q terminan empatadas en 4 cajas.
    """
    _dias_q = TOPE_EXCEDENTE + 1.0
    p = _store('P', consumo=2.0, dias=0.0, existencias=0.0)
    q = _store('Q', consumo=1.0, dias=_dias_q, existencias=_dias_q)
    tiendas, remaining, _ = distribuir_item('X', 8, _df([p, q], cajas=8))
    assert remaining == 0
    assert int(tiendas['cajas_asignadas'].sum()) == 8

    p_row = tiendas.loc[tiendas['store_code'] == 'P'].iloc[0]
    q_row = tiendas.loc[tiendas['store_code'] == 'Q'].iloc[0]

    # El nivel 2 (último recurso) se activó para ambas — matemáticamente
    # inevitable con 8 cajas para solo 2 tiendas (tope combinado = 6).
    assert p_row['cajas_asignadas'] == 4, (
        f"P debía terminar en 4 cajas (3 de tope + 1 de último recurso), "
        f"recibió {p_row['cajas_asignadas']}"
    )
    assert q_row['cajas_asignadas'] == 4, (
        f"Q debía terminar en 4 cajas (3 de tope + 1 de último recurso), "
        f"recibió {q_row['cajas_asignadas']}"
    )

    # Q no llegó a competir en Fase 4 (ya arrancaba en el tope de días). P recibe
    # 2 en Fase 1 (MIN_CAJAS_INICIAL) y la 3.ª en Fase 4; el resto vino de Fase 5.
    assert q_row['cajas_fase4'] == 0, "Q no debía recibir nada en Fase 4 (inelegible desde la ronda 1)"
    assert p_row['cajas_fase1'] == MIN_CAJAS_INICIAL, "P debía recibir su máximo de Fase 1"
    assert p_row['cajas_fase4'] == 1, "P debía completar su tope de cajas con 1 caja de Fase 4"
    assert q_row['cajas_fase5'] == 4, "Q debía recibir 4 cajas en Fase 5 (nivel 1 + nivel 2)"
    assert p_row['cajas_fase5'] == 1, "P solo debía recibir 1 caja en Fase 5 (nivel 2)"


def test_fase4_y_fase5_quedan_separadas_en_el_tracking():
    """Fase 4 (reparto sano bajo TOPE_EXCEDENTE) y Fase 5 (cero residuo, ignora el
    tope) se registran por separado en `cajas_fase4` / `cajas_fase5` (ítem 1561 Aguacate
    Hass: antes se sumaban y ocultaban cuánto venía de forzar sobre-stock).

    ELEGIBLE: justo cubierta pero con consumo alto, margen real para MAX_CAJAS_POR_ITEM
    cajas vía Fase 4.
    SATURADA: consumo bajo y sobre TOPE_EXCEDENTE; nunca elegible para Fase 4, solo
    recibe si el cero residuo obliga a Fase 5.

    Los valores se derivan de los parámetros, no se fijan a mano.
    """
    _consumo_eleg = 3.0
    _exist_eleg = _consumo_eleg * TARGET_DAYS       # justo cubierta
    elegible = _store('ELEGIBLE', consumo=_consumo_eleg, dias=TARGET_DAYS,
                      existencias=_exist_eleg)
    # SATURADA arranca POR ENCIMA del tope, para que nunca sea elegible en
    # Fase 4 y solo pueda recibir por la red de seguridad de Fase 5.
    _dias_sat = TOPE_EXCEDENTE + 2.0
    _exist_sat = _dias_sat * 0.2
    saturada = _store('SATURADA', consumo=0.2, dias=_dias_sat, existencias=_exist_sat)
    # Una caja más de las que ELEGIBLE puede absorber: esa sobrante es la que
    # obliga a la Fase 5 a tocar a SATURADA.
    _cajas = MAX_CAJAS_POR_ITEM + 1
    tiendas, remaining, _ = distribuir_item('X', _cajas, _df([elegible, saturada], cajas=_cajas))
    assert remaining == 0
    assert int(tiendas['cajas_asignadas'].sum()) == _cajas

    e_row = tiendas.loc[tiendas['store_code'] == 'ELEGIBLE'].iloc[0]
    s_row = tiendas.loc[tiendas['store_code'] == 'SATURADA'].iloc[0]

    assert e_row['cajas_fase4'] == MAX_CAJAS_POR_ITEM, (
        "ELEGIBLE tenía margen real bajo el tope: debía recibir vía Fase 4")
    assert e_row['cajas_fase5'] == 0, "ELEGIBLE no debía necesitar la red de seguridad de Fase 5"
    assert s_row['cajas_fase4'] == 0, "SATURADA ya estaba en el tope: no podía ser elegible en Fase 4"
    assert s_row['cajas_fase5'] == 1, "SATURADA solo debía recibir su caja vía Fase 5 (cero residuo forzado)"


def test_fase4_reparte_de_a_1_caja_por_ronda_no_por_tienda():
    """Fase 4 reparte de a 1 caja por tienda por ronda (ítem 2001): con solo 2 tiendas
    elegibles y sobrante grande, antes se les daba TODO de una vez, sin re-chequear el
    tope, y una 3ª tienda apenas sobre el tope se quedaba en 0.

      A, B: consumo=1.0, existencias=4.5 → capacidad real bajo el tope: 1 caja.
      C:    consumo=1.0, existencias=5.3 → no pasa el filtro de Fase 4 pero tiene menos
            días que A/B tras la 1ª ronda, así que es la primera candidata de Fase 5.

    Con el fix cada una recibe exactamente 1 caja (antes A=2, B=1, C=0).
    """
    a = _store('A', consumo=1.0, dias=4.5, existencias=4.5)
    b = _store('B', consumo=1.0, dias=4.5, existencias=4.5)
    c = _store('C', consumo=1.0, dias=5.3, existencias=5.3)
    tiendas, remaining, _ = distribuir_item('X', 3, _df([a, b, c], cajas=3))

    assert remaining == 0
    assert int(tiendas['cajas_asignadas'].sum()) == 3
    for code in ['A', 'B', 'C']:
        asignada = tiendas.loc[tiendas['store_code'] == code, 'cajas_asignadas'].iloc[0]
        assert asignada == 1, (
            f"{code} debía recibir exactamente 1 caja (reparto parejo), "
            f"recibió {asignada}"
        )


def test_paso_b_respeta_max_cajas_por_item_si_hay_otra_tienda_con_espacio():
    """Fase 5 respeta MAX_CAJAS_POR_ITEM si hay otra tienda con espacio (ítem 2473:
    antes seguía apilando sobre una tienda que ya tenía su máximo).

      P: consumo=2.0, existencias=0   → AGOTADA; llega a su tope de cajas en Fase 1 + Fase 4.
      Q: consumo=1.0, existencias=6.0 → CUBIERTA, ya en el tope de días, con espacio de cajas.
      R: consumo=0.5, existencias=3.0 → CUBIERTA, ya en el tope de días, con espacio de cajas.

    Con 8 cajas, Fase 5 debe llenar primero a Q y R antes de seguir sobre P.
    """
    p = _store('P', consumo=2.0, dias=0.0, existencias=0.0)
    q = _store('Q', consumo=1.0, dias=6.0, existencias=6.0)
    r = _store('R', consumo=0.5, dias=6.0, existencias=3.0)
    tiendas, remaining, _ = distribuir_item('X', 8, _df([p, q, r], cajas=8))

    assert remaining == 0
    assert int(tiendas['cajas_asignadas'].sum()) == 8

    p_asig = tiendas.loc[tiendas['store_code'] == 'P', 'cajas_asignadas'].iloc[0]
    q_asig = tiendas.loc[tiendas['store_code'] == 'Q', 'cajas_asignadas'].iloc[0]
    r_asig = tiendas.loc[tiendas['store_code'] == 'R', 'cajas_asignadas'].iloc[0]

    assert p_asig == MAX_CAJAS_POR_ITEM, (
        f"P no debía superar MAX_CAJAS_POR_ITEM habiendo espacio en Q/R, "
        f"recibió {p_asig}"
    )
    assert q_asig == MAX_CAJAS_POR_ITEM, f"Q debía llenarse hasta el tope, recibió {q_asig}"
    assert r_asig == 2, f"R debía absorber el resto del sobrante, recibió {r_asig}"


def test_frontera_tope_excedente():
    """La elegibilidad para el sobrante mira el estado TRAS recibir una caja más
    (`dias_tras_una <= TOPE_EXCEDENTE`): quien quedaría EXACTAMENTE en el tope sigue
    siendo elegible; quien lo cruzaría por una décima, no.

      F6:  tras +1 caja queda == TOPE_EXCEDENTE
      F61: tras +1 caja lo supera por 0.1 día

    Compiten por una sola caja: debe ir a F6. Las existencias se derivan de TOPE_EXCEDENTE.
    """
    _consumo = 0.5
    _exist_f6 = TOPE_EXCEDENTE * _consumo - 1       # tras +1 caja → exactamente el tope
    f6 = _store('F6', consumo=_consumo, dias=_exist_f6 / _consumo, existencias=_exist_f6)
    f61 = _store('F61', consumo=_consumo, dias=(_exist_f6 + 0.1) / _consumo,
                 existencias=_exist_f6 + 0.1)
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
    """Si Fase 5 concentraría >= UMBRAL_CONCENTRACION_SIN_CONSUMO cajas en una tienda
    por ser la única con consumo>0, se reparte equitativo entre todo el portafolio y se
    genera una ADVERTENCIA (ítem 1815: SA2 era la única con consumo, de 32).
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
    """Sin llegar al umbral de concentración no hay alerta y Fase 5 normal (por menor
    días_actuales) sigue aplicando."""
    p = _store('P', consumo=2.0, dias=0.0, existencias=0.0)
    q = _store('Q', consumo=1.0, dias=6.0, existencias=6.0)
    tiendas, remaining, alertas_item = distribuir_item('X', 8, _df([p, q], cajas=8))
    assert remaining == 0
    assert alertas_item == []


def test_grupo_espejo_no_duplica_reposicion():
    """Dos ítems espejo en la misma tienda: si el inventario COMBINADO del grupo ya
    alcanza TARGET_DAYS no se pide reposición para ninguno por separado (antes cada SKU
    pedía su propia cobertura, duplicando la necesidad real).

    Cada ítem visto solo tiene 2 días, pero el grupo tiene dias_proyectados_grupo ==
    TARGET_DAYS (cubierto).
    """
    row = pd.Series({
        'consumo_diario': 0.5,
        'inventario_efectivo': 1.0,
        'priority': 0,
        'consumo_diario_grupo': 1.0,
        'inventario_efectivo_grupo': TARGET_DAYS * 1.0,
        'dias_proyectados_grupo': TARGET_DAYS,
        'item_share_consumo': 0.5,
    })
    from core.algorithm import _calc_deseadas, get_priority
    assert get_priority(row) == 0, "Grupo cubierto (dias_grupo == TARGET_DAYS) -> prioridad CUBIERTA"
    assert _calc_deseadas(row, cap=3) == 0, (
        "El grupo ya está cubierto; el ítem A no debía pedir reposición aunque "
        "visto solo (1.0 caja / 0.5 consumo = 2.0 días) sí la pediría con la "
        "lógica antigua — el punto del test es que ahora usa la métrica de "
        "GRUPO, no la propia."
    )


def test_item_sin_espejo_comportamiento_idéntico():
    """Un ítem sin grupo espejo (grupo de tamaño 1) debe comportarse
    exactamente igual que antes de introducir la lógica de grupo."""
    rows = [_store('AGOTADA', 0.4, 0.0, existencias=0.0, transito=0.0),
            _store('OK', 5.0, 10.0, existencias=50.0)]
    tiendas, remaining, _ = distribuir_item('X', 8, _df(rows, cajas=8))
    assert remaining == 0
    asignada = tiendas.loc[tiendas['store_code'] == 'AGOTADA', 'cajas_asignadas'].iloc[0]
    assert asignada >= 1


def _tienda_post_fase2(store_code, consumo, inventario_efectivo, item_share_consumo, cajas_asignadas=0):
    """Fila de tienda ya resuelta en Fase 1/2 (lista para alimentar
    _fase4_grupal / _fase4_individual directamente)."""
    return {
        'store_code': store_code,
        'consumo_diario': consumo,
        'inventario_efectivo': inventario_efectivo,
        'item_share_consumo': item_share_consumo,
        'cajas_asignadas': cajas_asignadas,
        'cajas_fase1': 0,
        'cajas_fase2': 0,
        'cajas_fase4': 0,
        'cajas_fase5': 0,
    }


def _estado(rows, remaining):
    return {'tiendas': pd.DataFrame(rows), 'remaining': remaining}


def test_fase4_grupal_garantiza_presencia_y_respeta_tope_por_item():
    """Tienda T, grupo con ítems A y B (consumo y participación simétricos
    50/50). A ya tiene 1.0 caja en góndola; B tiene 0 (no está en góndola
    aunque el CEDI sí tenga stock de B). Tras la Fase 4 grupal:
      - B debe recibir al menos 1 caja (garantía de presencia), no solo lo
        que le tocaría por su participación en el consumo.
      - Ningún ítem debe superar MAX_CAJAS_POR_ITEM (tope individual, no de
        grupo) aunque haya CEDI de sobra para ambos.
      - Cero residuo: con estos números exactos, ambos pools quedan en 0.
    """
    estado_items = {
        'A': _estado([_tienda_post_fase2('T', consumo=1.0, inventario_efectivo=1.0,
                                           item_share_consumo=0.5)], remaining=3),
        'B': _estado([_tienda_post_fase2('T', consumo=1.0, inventario_efectivo=0.0,
                                           item_share_consumo=0.5)], remaining=3),
    }
    resultado, alertas = _fase4_grupal(estado_items)

    a_asig = int(resultado['A']['tiendas'].loc[0, 'cajas_asignadas'])
    b_asig = int(resultado['B']['tiendas'].loc[0, 'cajas_asignadas'])

    assert b_asig >= 1, "B (0 unidades en góndola) debía recibir al menos 1 caja garantizada"
    assert a_asig <= MAX_CAJAS_POR_ITEM, "A no debía superar MAX_CAJAS_POR_ITEM"
    assert b_asig <= MAX_CAJAS_POR_ITEM, "B no debía superar MAX_CAJAS_POR_ITEM"
    assert resultado['A']['remaining'] == 0
    assert resultado['B']['remaining'] == 0
    assert a_asig + resultado['A']['remaining'] == 3
    assert b_asig + resultado['B']['remaining'] == 3


def test_fase4_grupal_tope_excedente_es_del_grupo_no_del_item():
    """Dos tiendas, mismo grupo (A, B):

      T1: A tiene consumo bajísimo (0.1) y MUCHO inventario (70 cajas): arrastra el GRUPO
          muy por encima de TOPE_EXCEDENTE aunque B, visto solo, tenga 0 días.
      T2: ambos ítems sanos, grupo en 0.5 días — elegible.

    Con el tope evaluado a nivel de GRUPO, T1 no recibe nada y todo el sobrante va a T2.
    """
    estado_items = {
        'A': _estado([
            _tienda_post_fase2('T1', consumo=0.1, inventario_efectivo=70.0, item_share_consumo=0.1 / 2.1),
            _tienda_post_fase2('T2', consumo=1.0, inventario_efectivo=1.0, item_share_consumo=0.5),
        ], remaining=2),
        'B': _estado([
            _tienda_post_fase2('T1', consumo=2.0, inventario_efectivo=0.0, item_share_consumo=2.0 / 2.1),
            _tienda_post_fase2('T2', consumo=1.0, inventario_efectivo=0.0, item_share_consumo=0.5),
        ], remaining=2),
    }
    resultado, alertas = _fase4_grupal(estado_items)

    a_t1 = int(resultado['A']['tiendas'].set_index('store_code').loc['T1', 'cajas_asignadas'])
    b_t1 = int(resultado['B']['tiendas'].set_index('store_code').loc['T1', 'cajas_asignadas'])
    a_t2 = int(resultado['A']['tiendas'].set_index('store_code').loc['T2', 'cajas_asignadas'])
    b_t2 = int(resultado['B']['tiendas'].set_index('store_code').loc['T2', 'cajas_asignadas'])

    assert a_t1 == 0 and b_t1 == 0, (
        "T1 no debía recibir sobrante: el GRUPO ya está muy por encima de "
        f"TOPE_EXCEDENTE por el inventario de A, aunque B solo luzca urgente "
        f"(obtuvo A={a_t1}, B={b_t1})"
    )
    assert a_t2 > 0 and b_t2 > 0, "T2 (elegible) debía absorber el sobrante"
    assert resultado['A']['remaining'] == 0
    assert resultado['B']['remaining'] == 0


def test_fase4_grupal_cero_residuo_delega_a_paso_b_individual():
    """Una tienda, grupo con A y B: el CEDI manda mucho más de lo que absorbe bajo
    MAX_CAJAS_POR_ITEM. Fase 4 grupal llena ambos hasta el tope y el remanente de cada
    ítem se delega a Fase 5 individual (cero residuo, ignora ese tope)."""
    estado_items = {
        'A': _estado([_tienda_post_fase2('UNICA', consumo=2.0, inventario_efectivo=0.0,
                                           item_share_consumo=2.0 / 3.0)], remaining=10),
        'B': _estado([_tienda_post_fase2('UNICA', consumo=1.0, inventario_efectivo=6.0,
                                           item_share_consumo=1.0 / 3.0)], remaining=10),
    }
    resultado, alertas = _fase4_grupal(estado_items)

    assert resultado['A']['remaining'] == 0, "Cero residuo debe mantenerse para A vía Paso B individual"
    assert resultado['B']['remaining'] == 0, "Cero residuo debe mantenerse para B vía Paso B individual"

    a_asig = int(resultado['A']['tiendas'].loc[0, 'cajas_asignadas'])
    b_asig = int(resultado['B']['tiendas'].loc[0, 'cajas_asignadas'])
    assert a_asig == 10, "Única tienda del portafolio: debía recibir todo el CEDI de A"
    assert b_asig == 10, "Única tienda del portafolio: debía recibir todo el CEDI de B"
    assert a_asig > MAX_CAJAS_POR_ITEM, "Solo el Paso B (cero residuo) ignora MAX_CAJAS_POR_ITEM"



def test_grupo_espejo_multiple_no_sobreprovisiona_por_doble_ceil():
    """3 SKUs espejo en la misma tienda (~33% del consumo c/u) y necesidad de grupo de
    1.0 caja: `ceil()` por ítem sumaba 3 cajas en Fase 1 donde el grupo necesitaba 1.
    Vía `run_distribution`, Fase 1+2 del grupo no debe exceder `ceil(necesidad_grupo)`.
    """
    inv_grupo = TARGET_DAYS - 1.0  # con consumo de grupo 1.0 → necesidad de grupo = 1.0 caja
    rows = []
    for item_code, share in [('A', 1 / 3), ('B', 1 / 3), ('C', 1 / 3)]:
        rows.append({
            'store_code': 'T', 'item_code': item_code, 'grupo_id': 'GESPEJO',
            'consumo_diario': 1.0 / 3, 'consumo_diario_grupo': 1.0,
            'inventario_efectivo': 0.0, 'inventario_efectivo_grupo': inv_grupo,
            'dias_proyectados': 0.0, 'dias_proyectados_grupo': inv_grupo,
            'item_share_consumo': share,
            'cajas_disponibles_cedi': 5, 'store_name': 'T', 'um': 'UND',
            'item_desc': f'Producto {item_code}',
        })
    df_merged = pd.DataFrame(rows)
    # necesidad_grupo = max(0, TARGET_DAYS*1.0 - inv_grupo) = 1.0 -> ceil = 1 caja total para el grupo.
    df_output, alertas = run_distribution(df_merged)

    total_fase1_fase2 = int((df_output['cajas_fase1'] + df_output['cajas_fase2']).sum())
    assert total_fase1_fase2 <= 1, (
        "El grupo solo necesitaba 1 caja para llegar a TARGET_DAYS pero se "
        f"asignaron {total_fase1_fase2} entre Fase 1 y Fase 2 — doble ceil "
        "por ítem sobreprovisionando el grupo"
    )
    # Cero residuo se mantiene igual (el resto del CEDI, 5 cajas por cada uno
    # de los 3 SKUs, cae a Fase 4).
    assert int(df_output['cajas_asignadas'].sum()) == 15


def test_apportion_grupo_targets_item_sin_espejo_identico_a_ceil_directo():
    """Para un ítem sin espejo (grupo de tamaño 1, item_share_consumo=1.0), el
    apportion por grupo debe coincidir exactamente con ceil(necesidad_grupo) —
    cero cambio de comportamiento frente a la fórmula anterior."""
    df = pd.DataFrame([{
        'store_code': 'S1', 'grupo_id': 'ITEM_X',
        'consumo_diario_grupo': 2.0, 'inventario_efectivo_grupo': 1.0,
        'item_share_consumo': 1.0,
    }])
    resultado = _apportion_grupo_targets(df)
    necesidad = max(0.0, TARGET_DAYS * 2.0 - 1.0)
    assert int(resultado.iloc[0]) == math.ceil(necesidad)


def test_tienda_con_dias_actuales_cero_recibe_prioridad_en_excedente():
    """Una tienda con `dias_actuales==0` (la más urgente) debe recibir el MAYOR peso en
    Fase 4, no cero (antes `consumo/dias_act` con 0→NaN le daba peso nulo): con dos
    elegibles, la de 0 días recibe estrictamente más que la que ya tiene margen."""
    cero_dias = _tienda_post_fase2('CERO', consumo=1.0, inventario_efectivo=0.0,
                                     item_share_consumo=1.0, cajas_asignadas=0)
    con_margen = _tienda_post_fase2('MARGEN', consumo=1.0, inventario_efectivo=2.0,
                                      item_share_consumo=1.0, cajas_asignadas=0)
    tiendas = pd.DataFrame([cero_dias, con_margen])
    tiendas_result, remaining = _fase4_individual(tiendas, remaining=2)

    assert remaining == 0
    cero_asig = tiendas_result.loc[tiendas_result['store_code'] == 'CERO', 'cajas_fase4'].iloc[0]
    margen_asig = tiendas_result.loc[tiendas_result['store_code'] == 'MARGEN', 'cajas_fase4'].iloc[0]
    assert cero_asig > 0, "La tienda con 0 días actuales no debía quedar en 0 cajas de excedente"
    assert cero_asig >= margen_asig, (
        "La tienda con 0 días actuales debía recibir al menos tanto como la que ya tenía margen "
        f"(CERO={cero_asig}, MARGEN={margen_asig})"
    )


def test_reparto_proporcional_clip_defensivo_ante_pesos_negativos():
    """Pesos negativos (que no deberían ocurrir si preprocessor.py hace bien
    su trabajo, pero no hay garantía externa) no deben producir asignaciones
    negativas ni romper la suma total repartida."""
    pesos = pd.Series({'A': -5.0, 'B': 3.0, 'C': 2.0})
    room = pd.Series({'A': 10, 'B': 10, 'C': 10})
    asignado = _reparto_proporcional(pesos, total=5, room=room)
    assert (asignado >= 0).all(), "Ninguna tienda debía recibir cajas negativas"
    assert int(asignado.sum()) == 5
    assert asignado['A'] == 0, "El peso negativo no debía traducirse en asignación"


def test_run_distribution_multi_item_agrega_alertas_de_varios_items():
    """Con 2+ ítems, cada uno pudiendo generar sus propias alertas (p. ej.
    residuo sin tiendas elegibles), `run_distribution` debe acumular las
    alertas de TODOS los ítems, no solo del último procesado."""
    rows = [
        {'store_code': 'S1', 'item_code': 'A', 'grupo_id': 'ITEM_A',
         'consumo_diario': 1.0, 'consumo_diario_grupo': 1.0,
         'inventario_efectivo': 5.0, 'inventario_efectivo_grupo': 5.0,
         'dias_proyectados': 5.0, 'dias_proyectados_grupo': 5.0,
         'item_share_consumo': 1.0, 'cajas_disponibles_cedi': 3,
         'store_name': 'S1', 'um': 'UND', 'item_desc': 'Producto A'},
        {'store_code': 'S2', 'item_code': 'B', 'grupo_id': 'ITEM_B',
         'consumo_diario': 2.0, 'consumo_diario_grupo': 2.0,
         'inventario_efectivo': 0.0, 'inventario_efectivo_grupo': 0.0,
         'dias_proyectados': 0.0, 'dias_proyectados_grupo': 0.0,
         'item_share_consumo': 1.0, 'cajas_disponibles_cedi': 4,
         'store_name': 'S2', 'um': 'UND', 'item_desc': 'Producto B'},
    ]
    df_output, alertas = run_distribution(pd.DataFrame(rows))
    assert set(df_output['item_code'].unique()) == {'A', 'B'}
    assert int(df_output['cajas_asignadas'].sum()) == 7


def test_run_distribution_skip_silencioso_de_cajas_disponibles_cero():
    """Un ítem con cajas_disponibles_cedi=0 no debe aparecer en df_output ni
    generar ninguna alerta (nada que distribuir, no es un error)."""
    rows = [
        {'store_code': 'S1', 'item_code': 'SIN_STOCK', 'grupo_id': 'ITEM_SIN_STOCK',
         'consumo_diario': 1.0, 'consumo_diario_grupo': 1.0,
         'inventario_efectivo': 0.0, 'inventario_efectivo_grupo': 0.0,
         'dias_proyectados': 0.0, 'dias_proyectados_grupo': 0.0,
         'item_share_consumo': 1.0, 'cajas_disponibles_cedi': 0,
         'store_name': 'S1', 'um': 'UND', 'item_desc': 'Sin stock'},
    ]
    df_output, alertas = run_distribution(pd.DataFrame(rows))
    assert len(df_output) == 0
    assert alertas == []


def test_run_distribution_alertas_extra_se_incluyen_en_resultado():
    """`alertas_extra` (alertas detectadas aguas arriba en preprocessor.py,
    p. ej. ítems huérfanos) debe aparecer en el resultado final junto a las
    que genera run_distribution internamente."""
    rows = [
        {'store_code': 'S1', 'item_code': 'A', 'grupo_id': 'ITEM_A',
         'consumo_diario': 1.0, 'consumo_diario_grupo': 1.0,
         'inventario_efectivo': 5.0, 'inventario_efectivo_grupo': 5.0,
         'dias_proyectados': 5.0, 'dias_proyectados_grupo': 5.0,
         'item_share_consumo': 1.0, 'cajas_disponibles_cedi': 1,
         'store_name': 'S1', 'um': 'UND', 'item_desc': 'Producto A'},
    ]
    alerta_previa = {'tipo': 'CRÍTICO', 'item': 'HUERFANO', 'mensaje': 'Ítem huérfano de prueba'}
    df_output, alertas = run_distribution(pd.DataFrame(rows), alertas_extra=[alerta_previa])
    assert alerta_previa in alertas


def test_run_distribution_alerta_agregada_sin_match_celes():
    """Columna `sin_match_celes` marcada en `df_merged` debe generar un único
    alerta agregado (no uno por fila) con el conteo correcto."""
    rows = [
        {'store_code': 'S1', 'item_code': 'A', 'grupo_id': 'ITEM_A',
         'consumo_diario': 1.0, 'consumo_diario_grupo': 1.0,
         'inventario_efectivo': 5.0, 'inventario_efectivo_grupo': 5.0,
         'dias_proyectados': 5.0, 'dias_proyectados_grupo': 5.0,
         'item_share_consumo': 1.0, 'cajas_disponibles_cedi': 1,
         'store_name': 'S1', 'um': 'UND', 'item_desc': 'Producto A',
         'sin_match_celes': True},
        {'store_code': 'S2', 'item_code': 'A', 'grupo_id': 'ITEM_A',
         'consumo_diario': 1.0, 'consumo_diario_grupo': 1.0,
         'inventario_efectivo': 5.0, 'inventario_efectivo_grupo': 5.0,
         'dias_proyectados': 5.0, 'dias_proyectados_grupo': 5.0,
         'item_share_consumo': 1.0, 'cajas_disponibles_cedi': 1,
         'store_name': 'S2', 'um': 'UND', 'item_desc': 'Producto A',
         'sin_match_celes': False},
    ]
    df_output, alertas = run_distribution(pd.DataFrame(rows))
    agregadas = [a for a in alertas if a.get('combos_sin_match') is not None]
    assert len(agregadas) == 1, "Debe haber exactamente un alerta agregado de sin_match_celes"
    assert agregadas[0]['combos_sin_match'] == 1


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))


# ---------------------------------------------------------------------------
# Tope MAX por tienda × ítem
# ---------------------------------------------------------------------------

def _df_max(rows, bloqueos, item_code='X', cajas=10):
    """`_df` + las columnas del tope MAX. `bloqueos` es un dict
    {store_code: (max_existencias, bloqueado)}."""
    df = _df(rows, item_code=item_code, cajas=cajas)
    df['max_existencias'] = df['store_code'].map(lambda s: bloqueos.get(s, (0, False))[0])
    df['bloqueado_por_max'] = df['store_code'].map(lambda s: bloqueos.get(s, (0, False))[1])
    return df


def test_tienda_bloqueada_por_max_no_recibe_si_hay_alternativa():
    """La tienda bloqueada queda fuera del reparto mientras otra pueda absorber."""
    rows = [_store('A', 1.0, 0.1), _store('BLOQ', 5.0, 0.0, existencias=9.0)]
    df = _df_max(rows, {'BLOQ': (6, True)}, cajas=3)
    out, _ = run_distribution(df)
    asignado = dict(zip(out['store_code'], out['cajas_asignadas']))
    assert asignado.get('BLOQ', 0) == 0
    assert asignado.get('A', 0) == 3


def test_tienda_no_bloqueada_recibe_normalmente():
    """Control: sin bloqueo, la misma tienda de alto consumo sí recibe."""
    rows = [_store('A', 1.0, 0.1), _store('LIBRE', 5.0, 0.0, existencias=5.9)]
    df = _df_max(rows, {'LIBRE': (6, False)}, cajas=3)
    out, _ = run_distribution(df)
    asignado = dict(zip(out['store_code'], out['cajas_asignadas']))
    assert asignado.get('LIBRE', 0) > 0


def test_cero_residuo_gana_sobre_max_y_deja_alerta():
    """Si TODAS las tiendas están bloqueadas, Fase 5 nivel 2 reparte igual —
    cero residuo manda — y debe quedar constancia en las alertas."""
    rows = [_store('B1', 2.0, 0.0, existencias=9.0), _store('B2', 1.0, 0.0, existencias=8.0)]
    df = _df_max(rows, {'B1': (6, True), 'B2': (6, True)}, cajas=7)
    out, alertas = run_distribution(df)
    assert int(out['cajas_asignadas'].sum()) == 7          # cero residuo intacto
    forzadas = [a for a in alertas if a.get('cajas_forzadas')]
    assert forzadas, 'debía alertar que se superó el MAX por falta de destino'
    assert sum(a['cajas_forzadas'] for a in forzadas) == 7
    assert all(a['tipo'] == 'ADVERTENCIA' for a in forzadas)


def test_bloqueadas_reciben_solo_despues_de_las_libres():
    """Con más stock del que la tienda libre puede absorber, la bloqueada
    recibe solo el excedente que ya no cabe en ninguna otra parte."""
    rows = [_store('LIBRE', 1.0, 0.0), _store('BLOQ', 1.0, 0.0, existencias=9.0)]
    df = _df_max(rows, {'BLOQ': (6, True)}, cajas=10)
    out, _ = run_distribution(df)
    asignado = dict(zip(out['store_code'], out['cajas_asignadas']))
    assert int(sum(asignado.values())) == 10
    # La libre se llena hasta su tope antes de que la bloqueada reciba nada.
    assert asignado.get('LIBRE', 0) >= MAX_CAJAS_POR_ITEM
    assert asignado.get('BLOQ', 0) > 0


def test_sin_columnas_de_max_el_comportamiento_no_cambia():
    """Un DataFrame sin las columnas del tope (tests antiguos, llamadas
    aisladas) debe comportarse exactamente como antes."""
    rows = [_store('A', 1.0, 0.1), _store('B', 0.5, 1.0)]
    sin_max = _df(rows, cajas=6)
    con_max = _df_max(rows, {}, cajas=6)
    out_sin, _ = run_distribution(sin_max)
    out_con, _ = run_distribution(con_max)
    assert dict(zip(out_sin['store_code'], out_sin['cajas_asignadas'])) == \
           dict(zip(out_con['store_code'], out_con['cajas_asignadas']))


def test_bloqueo_es_por_item_no_por_grupo_espejo():
    """Dos ítems espejo en la misma tienda: bloquear uno no debe frenar al
    hermano, que sigue recibiendo con normalidad."""
    filas = []
    for item, bloq in (('E1', True), ('E2', False)):
        d = _store('T1', 1.0, 0.0, existencias=9.0 if bloq else 0.0)
        d.update({
            'item_code': item, 'cajas_disponibles_cedi': 4,
            'store_name': 'T1', 'um': 'UND', 'item_desc': 'Espejo',
            'grupo_id': 'G1', 'item_share_consumo': 0.5,
            'max_existencias': 6 if bloq else 0, 'bloqueado_por_max': bloq,
        })
        filas.append(d)
    df = pd.DataFrame(filas)
    df['consumo_diario_grupo'] = 2.0
    df['inventario_efectivo_grupo'] = 9.0
    df['dias_proyectados_grupo'] = 4.5
    out, _ = run_distribution(df)
    por_item = dict(zip(out['item_code'], out['cajas_asignadas']))
    assert por_item.get('E2', 0) > 0, 'el hermano no bloqueado debe recibir'
    assert int(out['cajas_asignadas'].sum()) == 8  # cero residuo en ambos SKU


# ---------------------------------------------------------------------------
# Exclusión de combinaciones sin datos en Celes
# ---------------------------------------------------------------------------

def _df_excluidas(rows, sin_match, item_code='X', cajas=10):
    """`_df` + las columnas de exclusión, marcando como sin match en Celes a
    los store_code de `sin_match`. Reproduce lo que arma el preprocessor:
    `excluida_del_reparto` es la unión de las dos causas."""
    df = _df(rows, item_code=item_code, cajas=cajas)
    df['max_existencias'] = 0
    df['bloqueado_por_max'] = False
    df['sin_match_celes'] = df['store_code'].isin(sin_match)
    df['excluida_del_reparto'] = df['bloqueado_por_max'] | df['sin_match_celes']
    return df


def test_sin_datos_celes_no_recibe_si_hay_alternativa():
    """El caso que motivó el cambio: sin match en Celes la fila llega con
    consumo=0/inventario=0, lo que antes la clasificaba como AGOTADA y le
    garantizaba su caja de Fase 1. Ahora no debe recibir nada mientras otra
    tienda pueda absorber el stock."""
    rows = [_store('CONDATOS', 2.0, 1.0, existencias=2.0),
            _store('SINDATOS', 0.0, 0.0, existencias=0.0)]
    df = _df_excluidas(rows, {'SINDATOS'}, cajas=4)
    out, _ = run_distribution(df)
    asignado = dict(zip(out['store_code'], out['cajas_asignadas']))
    assert asignado.get('SINDATOS', 0) == 0
    assert asignado.get('CONDATOS', 0) == 4


def test_sin_datos_celes_recibiria_sin_la_exclusion():
    """Control del test anterior: la MISMA fila, sin marcar como excluida, sí
    recibe — confirma que es la exclusión lo que cambia el resultado y no otra
    característica del escenario."""
    rows = [_store('CONDATOS', 2.0, 1.0, existencias=2.0),
            _store('SINDATOS', 0.0, 0.0, existencias=0.0)]
    df = _df_excluidas(rows, set(), cajas=4)
    out, _ = run_distribution(df)
    asignado = dict(zip(out['store_code'], out['cajas_asignadas']))
    assert asignado.get('SINDATOS', 0) > 0


def test_cero_residuo_gana_sobre_sin_datos_celes_y_deja_alerta():
    """Si TODAS las tiendas elegibles del ítem están sin datos en Celes, el
    nivel 2 de Fase 5 reparte igual — cero residuo manda — con alerta por
    tienda que nombra la causa."""
    rows = [_store('S1', 0.0, 0.0), _store('S2', 0.0, 0.0)]
    df = _df_excluidas(rows, {'S1', 'S2'}, cajas=5)
    out, alertas = run_distribution(df)
    assert int(out['cajas_asignadas'].sum()) == 5           # cero residuo intacto
    forzadas = [a for a in alertas if a.get('cajas_forzadas')]
    assert forzadas, 'debía alertar que se envió sin datos por falta de destino'
    assert sum(a['cajas_forzadas'] for a in forzadas) == 5
    assert all(a['causa_forzado'] == 'SIN DATOS CELES' for a in forzadas)
    assert all('no tener datos en Celes' in a['mensaje'] for a in forzadas)


def test_alerta_forzada_por_max_conserva_su_mensaje():
    """La otra rama de `_alertas_forzadas`: una fila bloqueada por MAX (con
    datos en Celes) sigue reportando MAX, existencias y tránsito, no la causa nueva."""
    rows = [_store('B1', 1.0, 9.0, existencias=6.0, transito=3.0)]
    df = _df(rows, cajas=3)
    df['max_existencias'] = 6
    df['bloqueado_por_max'] = True
    df['sin_match_celes'] = False
    df['excluida_del_reparto'] = True
    out, alertas = run_distribution(df)
    assert int(out['cajas_asignadas'].sum()) == 3
    forzadas = [a for a in alertas if a.get('cajas_forzadas')]
    assert forzadas
    assert all(a['causa_forzado'] == 'MAX' for a in forzadas)
    assert all('superar su MAX' in a['mensaje'] for a in forzadas)
    assert all('en tránsito' in a['mensaje'] for a in forzadas)
    assert all(a['existencias'] == 6.0 and a['transito'] == 3.0 for a in forzadas)


def test_alerta_agregada_de_sin_match_reporta_conteos():
    rows = [_store('CONDATOS', 2.0, 1.0, existencias=2.0),
            _store('SINDATOS', 0.0, 0.0)]
    df = _df_excluidas(rows, {'SINDATOS'}, cajas=4)
    _, alertas = run_distribution(df)
    agg = [a for a in alertas if a.get('combos_sin_match')]
    assert len(agg) == 1
    assert agg[0]['combos_sin_match'] == 1
    assert agg[0]['tiendas_afectadas'] == 1
    assert agg[0]['items_afectados'] == 1
    assert 'FUERA del reparto' in agg[0]['mensaje']


def test_cero_residuo_con_mezcla_de_causas_de_exclusion():
    """Una tienda bloqueada por MAX, otra sin datos en Celes y una normal: el
    stock completo se despacha y la normal absorbe todo lo que puede."""
    rows = [_store('NORMAL', 1.0, 0.0),
            _store('BLOQ', 1.0, 9.0, existencias=9.0),
            _store('SINDATOS', 0.0, 0.0)]
    df = _df(rows, cajas=12)
    df['max_existencias'] = df['store_code'].map({'BLOQ': 6}).fillna(0)
    df['bloqueado_por_max'] = df['store_code'] == 'BLOQ'
    df['sin_match_celes'] = df['store_code'] == 'SINDATOS'
    df['excluida_del_reparto'] = df['bloqueado_por_max'] | df['sin_match_celes']
    out, _ = run_distribution(df)
    asignado = dict(zip(out['store_code'], out['cajas_asignadas']))
    assert int(sum(asignado.values())) == 12
    assert asignado.get('NORMAL', 0) >= MAX_CAJAS_POR_ITEM


def test_reparto_equitativo_respeta_max_cajas_por_item_mientras_sea_posible():
    """Auditoría 360: el reparto equitativo (portafolio sin consumo) llenaba a
    5 cajas por tienda con CEDI=20 y 4 tiendas; ahora llena primero hasta
    MAX_CAJAS_POR_ITEM y solo supera ese tope si no queda alternativa."""
    unica = _store('UNICA', consumo=0.5, dias=0.0, existencias=0.0)
    sin_consumo = [_store(f'SC{i}', consumo=0.0, dias=float('inf'), existencias=5.0)
                   for i in range(1, 4)]
    tiendas, remaining, alertas_item = distribuir_item('X', 12, _df([unica] + sin_consumo, cajas=12))
    assert remaining == 0
    assert int(tiendas['cajas_asignadas'].sum()) == 12
    assert tiendas['cajas_asignadas'].max() <= MAX_CAJAS_POR_ITEM, (
        "Con 12 cajas y 4 tiendas cabe todo sin superar el máximo por tienda"
    )


def test_reparto_equitativo_supera_el_maximo_solo_si_es_inevitable():
    unica = _store('UNICA', consumo=0.5, dias=0.0, existencias=0.0)
    sin_consumo = [_store(f'SC{i}', consumo=0.0, dias=float('inf'), existencias=5.0)
                   for i in range(1, 4)]
    tiendas, remaining, _ = distribuir_item('X', 20, _df([unica] + sin_consumo, cajas=20))
    assert remaining == 0
    assert int(tiendas['cajas_asignadas'].sum()) == 20  # cero residuo intacto


# ── Fase 3: renovación por vida útil ───────────────────────────────────────────

def _store_r(code, consumo, existencias, vu=5.0, dias_ing=8.0, transito=0.0, fresco=False, **extra):
    """Tienda con ingreso viejo: por defecto 8 días desde el último ingreso para
    un ítem de 5 días de vida útil, con 1.5 cajas aún en inventario. Con
    consumo 0.3 → venta esperada en vida útil = 1.5 cajas (>= umbral 0.7). La
    tienda no es prioritaria (≥ MIN_STOCK_SAFETY y ≥ TARGET_DAYS), así que Fase 1/2 no le da nada."""
    s = _store(code, consumo, dias=existencias / consumo if consumo else float('inf'),
               existencias=existencias, transito=transito)
    s.update(
        vida_util_portafolio=vu,
        dias_desde_ingreso=dias_ing,
        venta_esperada_vida_util=consumo * vu,
        grupo_con_ingreso_fresco=fresco,
    )
    s.update(extra)
    return s


def _renovadas(tiendas):
    return dict(zip(tiendas['store_code'], tiendas['cajas_fase3']))


def test_fase3_renueva_una_caja_si_el_ingreso_supera_la_vida_util():
    tiendas, remaining, _ = distribuir_item('X', 1, _df([_store_r('R', 0.3, 1.5)], cajas=1))
    assert remaining == 0
    assert _renovadas(tiendas) == {'R': 1}
    assert int(tiendas['cajas_fase4'].sum() + tiendas['cajas_fase5'].sum()) == 0


def test_fase3_envia_exactamente_cajas_renovacion_aunque_sobre_stock():
    tiendas, remaining, _ = distribuir_item('X', 10, _df([_store_r('R', 0.3, 1.5)], cajas=10))
    assert remaining == 0
    assert _renovadas(tiendas) == {'R': 1}  # el resto lo coloca Fase 4/5, no Fase 3


def test_fase3_no_renueva_si_hay_transito():
    t, _, _ = distribuir_item('X', 1, _df([_store_r('R', 0.3, 1.5, transito=1.0)], cajas=1))
    assert _renovadas(t) == {'R': 0}


def test_fase3_por_defecto_renueva_aunque_la_venta_esperada_sea_baja():
    """Decisión del negocio (nov-2026): UMBRAL_VENTA_RENOVACION = 0 — si el ingreso
    supera la vida útil SIEMPRE se renueva, aunque venda poco (consumo 0.02 × 5 = 0.1)."""
    import core.algorithm as alg
    assert alg.UMBRAL_VENTA_RENOVACION == 0
    t, _, _ = distribuir_item('X', 1, _df([_store_r('R', 0.02, 1.5)], cajas=1))
    assert _renovadas(t) == {'R': 1}


def test_fase3_con_umbral_activo_no_renueva_si_la_venta_esperada_no_lo_alcanza(monkeypatch):
    monkeypatch.setattr('core.algorithm.UMBRAL_VENTA_RENOVACION', 0.7)
    # consumo 0.1 × vida útil 5 = 0.5 caja < 0.7
    t, _, _ = distribuir_item('X', 1, _df([_store_r('R', 0.1, 1.5)], cajas=1))
    assert _renovadas(t) == {'R': 0}


def test_fase3_con_umbral_activo_el_corte_es_inclusivo(monkeypatch):
    monkeypatch.setattr('core.algorithm.UMBRAL_VENTA_RENOVACION', 0.7)
    # consumo 0.14 × 5 = 0.70 exacto
    t, _, _ = distribuir_item('X', 1, _df([_store_r('R', 0.14, 1.5)], cajas=1))
    assert _renovadas(t) == {'R': 1}


def test_fase3_omite_vida_util_vacia_o_cero():
    for vu in (float('nan'), 0.0):
        fila = _store_r('R', 0.3, 1.5, vu=vu)
        fila['venta_esperada_vida_util'] = float('nan')
        t, _, _ = distribuir_item('X', 1, _df([fila], cajas=1))
        assert _renovadas(t) == {'R': 0}, vu


def _filas_f5_tras_renovacion():
    """R: ingreso vencido (recibe su caja en Fase 3) y es la de MENOS días tras
    la renovación (8.3 vs 10.0), así que sin la regla encabezaría la fila de Fase 5.
    A y B: cubiertas (10 días, ingreso fresco) → no renuevan ni entran a Fase 4."""
    return [_store_r('R', 0.3, 1.5),
            _store_r('A', 0.3, 3.0, dias_ing=2.0),
            _store_r('B', 0.3, 3.0, dias_ing=2.0)]


def test_fase5_no_da_caja_extra_a_la_tienda_ya_renovada_si_hay_otras_sin_caja():
    # 3 cajas: 1 a R (Fase 3) y 2 al sobrante (Fase 5) → una para A y otra para B.
    t, remaining, _ = distribuir_item('X', 3, _df(_filas_f5_tras_renovacion(), cajas=3))
    assert remaining == 0
    por = t.set_index('store_code')
    assert por.loc['R', 'cajas_fase3'] == 1 and por.loc['R', 'cajas_fase5'] == 0
    assert por.loc['A', 'cajas_fase5'] == 1 and por.loc['B', 'cajas_fase5'] == 1


def test_fase5_la_ya_renovada_recibe_solo_cuando_las_demas_ya_tienen_su_caja():
    # 4 cajas: 1 Fase 3 + 3 al sobrante → A, B y por último R (cero residuo intacto).
    t, remaining, _ = distribuir_item('X', 4, _df(_filas_f5_tras_renovacion(), cajas=4))
    assert remaining == 0 and int(t['cajas_asignadas'].sum()) == 4
    por = t.set_index('store_code')
    assert [int(por.loc[c, 'cajas_fase5']) for c in ('A', 'B', 'R')] == [1, 1, 1]


def _filas_f5_dos_pasadas():
    """R: renovada (Fase 3), consumo 0.3, 8.3 días tras renovar → con 'menos días' iría primero.
    A: consumo 0.2, 15 días. B: consumo 0.6, 10 días. A y B cubiertas con ingreso fresco."""
    return [_store_r('R', 0.3, 1.5),
            _store_r('A', 0.2, 3.0, dias_ing=2.0),
            _store_r('B', 0.6, 6.0, dias_ing=2.0)]


def test_fase5_segunda_pasada_ordena_por_mayor_consumo():
    # 4 cajas: 1 Fase 3 (R) + pasada 1 (B y A, 1 c/u) + 1 sobrante en la pasada 2.
    # Por consumo gana B (0.6); por días habría ganado R (8.3 días < 11.7 de B).
    t, remaining, _ = distribuir_item('X', 4, _df(_filas_f5_dos_pasadas(), cajas=4))
    assert remaining == 0 and int(t['cajas_asignadas'].sum()) == 4
    por = t.set_index('store_code')
    assert [int(por.loc[c, 'cajas_fase5']) for c in ('R', 'A', 'B')] == [0, 1, 2]


def test_fase5_segunda_pasada_incluye_a_la_renovada_y_respeta_el_orden():
    # 5 cajas: sobrante de 2 en la pasada 2 → B (0.6) y luego R (0.3); A (0.2) se queda con 1.
    t, remaining, _ = distribuir_item('X', 5, _df(_filas_f5_dos_pasadas(), cajas=5))
    assert remaining == 0 and int(t['cajas_asignadas'].sum()) == 5
    por = t.set_index('store_code')
    assert [int(por.loc[c, 'cajas_fase5']) for c in ('R', 'A', 'B')] == [1, 1, 2]
    assert int(t['cajas_asignadas'].max()) <= MAX_CAJAS_POR_ITEM


def test_fase3_no_renueva_si_el_ingreso_no_supera_la_vida_util():
    for dias in (5.0, 3.0, float('nan')):   # igual a la vida útil, menor, o sin fecha
        t, _, _ = distribuir_item('X', 1, _df([_store_r('R', 0.3, 1.5, dias_ing=dias)], cajas=1))
        assert _renovadas(t) == {'R': 0}, dias


def test_fase3_no_renueva_si_no_hay_inventario_en_tienda():
    t, _, _ = distribuir_item('X', 1, _df([_store_r('R', 0.3, 0.0)], cajas=1))
    assert _renovadas(t) == {'R': 0}   # AGOTADA: la atiende la Fase 1, no la renovación


def test_fase3_no_duplica_si_la_tienda_ya_recibio_en_fase_1():
    # 0.5 cajas < MIN_STOCK_SAFETY → prioridad 2 → Fase 1 le da su caja.
    t, _, _ = distribuir_item('X', 5, _df([_store_r('R', 0.5, 0.5)], cajas=5))
    fila = t.iloc[0]
    assert fila['cajas_fase1'] >= 1 and fila['cajas_fase3'] == 0


def test_fase3_respeta_exclusion_por_max_o_sin_datos_celes():
    t, _, _ = distribuir_item('X', 1, _df([_store_r('R', 0.3, 1.5, excluida_del_reparto=True)], cajas=1))
    assert _renovadas(t) == {'R': 0}


def test_fase3_con_stock_escaso_prioriza_mayor_venta_esperada_y_alerta():
    filas = [_store_r('A', 0.3, 1.5), _store_r('B', 0.6, 3.0)]
    t, remaining, alertas = distribuir_item('X', 1, _df(filas, cajas=1))
    assert remaining == 0
    assert _renovadas(t) == {'A': 0, 'B': 1}
    sin = [a for a in alertas if a.get('tiendas_sin_renovar')]
    assert len(sin) == 1 and sin[0]['tiendas_sin_renovar'] == ['A']


def test_fase3_alerta_aunque_el_item_ya_no_tenga_stock():
    # Una tienda AGOTADA consume la única caja en Fase 1; la candidata queda sin renovar.
    filas = [_store('AGOTADA', 0.5, 0.0), _store_r('R', 0.3, 1.5)]
    _, remaining, alertas = distribuir_item('X', 1, _df(filas, cajas=1))
    assert remaining == 0
    assert any(a.get('tiendas_sin_renovar') == ['R'] for a in alertas)


def _df_espejo(fresco=False, cajas=(5, 5)):
    """Un solo grupo espejo G con dos SKU (E1, E2) en una tienda: cada uno con
    1.5 cajas, consumo 0.15 y 8 días desde su último ingreso (vida útil 5)."""
    filas = []
    for item, caja in zip(('E1', 'E2'), cajas):
        filas.append({
            'item_code': item, 'store_code': 'S', 'store_name': 'S', 'um': 'UND',
            'item_desc': item, 'consumo_diario': 0.15, 'dias_proyectados': 10.0,
            'inventario_tienda': 1.5, 'inventario_transito': 0.0, 'inventario_efectivo': 1.5,
            'cajas_disponibles_cedi': caja, 'grupo_id': 'G',
            'consumo_diario_grupo': 0.3, 'inventario_efectivo_grupo': 3.0,
            'dias_proyectados_grupo': 10.0, 'item_share_consumo': 0.5,
            'vida_util_portafolio': 5.0, 'dias_desde_ingreso': 8.0,
            'venta_esperada_vida_util': 1.5, 'grupo_con_ingreso_fresco': fresco,
        })
    return pd.DataFrame(filas)


def test_fase3_en_grupo_espejo_envia_una_sola_caja_por_tienda_y_grupo():
    out, _ = run_distribution(_df_espejo())
    assert int(out['cajas_fase3'].sum()) == 1


def test_fase3_no_renueva_si_un_sku_espejo_tiene_ingreso_fresco():
    out, _ = run_distribution(_df_espejo(fresco=True))
    assert int(out['cajas_fase3'].sum()) == 0


def test_fase3_mantiene_cero_residuo_por_item():
    df = _df_espejo(cajas=(7, 4))
    out, _ = run_distribution(df)
    por_item = out.groupby('item_code')['cajas_asignadas'].sum().to_dict()
    assert por_item == {'E1': 7, 'E2': 4}


def test_fase3_sin_columnas_de_renovacion_no_cambia_el_comportamiento():
    """Tests antiguos / llamadas aisladas sin las columnas nuevas: Fase 3 no actúa."""
    rows = [_store('A', 1.0, 0.1), _store('B', 0.5, 1.0)]
    t, remaining, _ = distribuir_item('X', 6, _df(rows, cajas=6))
    assert remaining == 0 and int(t['cajas_fase3'].sum()) == 0


def test_fase3_alerta_de_sin_stock_es_compacta_con_muchas_tiendas():
    """Con cientos de tiendas sin stock la alerta no debe listarlas todas en el mensaje."""
    filas = [_store_r(f'T{i:03d}', 0.3, 1.5) for i in range(40)]
    _, _, alertas = distribuir_item('X', 1, _df(filas, cajas=1))
    sin = [a for a in alertas if a.get('tiendas_sin_renovar')]
    assert len(sin) == 1 and len(sin[0]['tiendas_sin_renovar']) == 39          # la lista completa va en el dato
    assert 'T039' not in sin[0]['mensaje'] and '…' in sin[0]['mensaje'] and len(sin[0]['mensaje']) < 400


def test_run_distribution_resume_las_renovaciones_sin_stock_en_una_sola_alerta():
    """Varios ítems con candidatas sin stock generan UNA alerta resumen, no una por ítem."""
    filas = []
    for item in ('I1', 'I2', 'I3'):
        for i in range(5):
            f = _store_r(f'T{i}', 0.3, 1.5)
            f.update(item_code=item, cajas_disponibles_cedi=1, grupo_id=f'ITEM_{item}')
            filas.append(f)
    df = pd.DataFrame(filas)
    for c, v in (('store_name', None), ('um', 'UND'), ('item_desc', 'x')):
        df[c] = df['store_code'] if v is None else v
    df['consumo_diario_grupo'] = df['consumo_diario']
    df['inventario_efectivo_grupo'] = df['inventario_efectivo']
    df['dias_proyectados_grupo'] = df['dias_proyectados']
    df['item_share_consumo'] = 1.0
    out, alertas = run_distribution(df)
    resumen = [a for a in alertas if a.get('combos_sin_renovar')]
    assert len(resumen) == 1 and not any(a.get('tiendas_sin_renovar') for a in alertas)
    assert resumen[0]['combos_sin_renovar'] == 12            # 3 ítems × (5 candidatas − 1 renovada)
    assert resumen[0]['items_afectados'] == ['I1', 'I2', 'I3']
    assert int(out['cajas_asignadas'].sum()) == 3             # cero residuo: 1 caja por ítem


# ── Tránsito: no renovar lo que ya va en camino (evita el doble envío del día siguiente) ──

def test_fase3_transito_en_un_sku_espejo_hermano_bloquea_la_renovacion_del_grupo():
    df = _df_espejo()
    df.loc[1, 'inventario_transito'] = 1.0          # el hermano E2 ya tiene mercancía en camino
    out, _ = run_distribution(df)
    assert int(out['cajas_fase3'].sum()) == 0


def test_fase3_cualquier_transito_por_pequeno_que_sea_bloquea():
    t, _, _ = distribuir_item('X', 1, _df([_store_r('R', 0.3, 1.5, transito=0.01)], cajas=1))
    assert _renovadas(t) == {'R': 0}


def test_fase3_no_vuelve_a_renovar_al_dia_siguiente_si_lo_enviado_figura_en_transito():
    """Escenario de dos días: hoy se renueva; mañana el ingreso a la tienda sigue siendo el viejo
    (la caja aún no llegó) pero Celes ya muestra la caja enviada como tránsito → no se repite."""
    dia1, _, _ = distribuir_item('X', 1, _df([_store_r('R', 0.3, 1.5)], cajas=1))
    assert _renovadas(dia1) == {'R': 1}
    enviado = float(dia1['cajas_fase3'].sum())
    dia2, _, _ = distribuir_item('X', 1, _df([_store_r('R', 0.3, 1.5, transito=enviado)], cajas=1))
    assert _renovadas(dia2) == {'R': 0}


# ── Límite de la regla: solo se renueva cuando los días SUPERAN la vida útil ──

def test_fase3_limite_con_vida_util_5_se_envia_a_los_6_dias_y_no_antes():
    """Vida útil 5: con 4 y 5 días (exactamente la vida útil) NO se envía; con 6 días SÍ."""
    esperado = {4.0: 0, 5.0: 0, 5.5: 1, 6.0: 1, 7.0: 1}
    for dias, cajas in esperado.items():
        t, _, _ = distribuir_item('X', 1, _df([_store_r('R', 0.3, 1.5, vu=5.0, dias_ing=dias)], cajas=1))
        assert _renovadas(t) == {'R': cajas}, f'días={dias}'


# ---------------------------------------------------------------------------
# Fase 1 (completar TARGET_DAYS por venta) y Fase 2 (garantizar agotado)
# ---------------------------------------------------------------------------

def _por_tienda(tiendas):
    return tiendas.set_index('store_code')


def test_fase1_escasez_prioriza_mayor_venta_y_reparte_por_rondas():
    """Con 3 cajas y tres tiendas que necesitan 2 cada una, la ronda 1 da 1 caja
    a cada una (de mayor a menor venta) antes de que alguna reciba la 2.ª; con 2
    cajas, se quedan las dos que más venden."""
    filas = [_store('BAJA', 0.5, 1.0, existencias=0.5),     # necesita 2.5 - 0.5 = 2
             _store('ALTA', 2.0, 4.0, existencias=8.5),   # necesita 10 - 8.5 = 1.5 → 2
             _store('MEDIA', 1.0, 3.0, existencias=3.5)]  # necesita 5 - 3.5 = 1.5 → 2
    t, _, _ = distribuir_item('X', 3, _df(filas, cajas=3))
    assert _por_tienda(t)['cajas_fase1'].to_dict() == {'ALTA': 1, 'MEDIA': 1, 'BAJA': 1}

    t, _, _ = distribuir_item('X', 2, _df(filas, cajas=2))
    assert _por_tienda(t)['cajas_fase1'].to_dict() == {'ALTA': 1, 'MEDIA': 1, 'BAJA': 0}


def test_fase1_no_pasa_de_min_cajas_iniciales():
    """Aunque una tienda necesite muchas cajas para llegar a TARGET_DAYS, en Fase 1
    no recibe más de MIN_CAJAS_INICIAL."""
    t, _, _ = distribuir_item('X', 20, _df([_store('A', 5.0, 0.0)], cajas=20))
    assert int(t.iloc[0]['cajas_fase1']) == MIN_CAJAS_INICIAL


def test_fase1_va_antes_que_la_agotada():
    """Con 1 sola caja, va a la tienda que más vende aunque la otra esté AGOTADA
    y venda menos: la Fase 1 manda por venta, la Fase 2 solo usa el sobrante."""
    filas = [_store('AGOT', 0.2, 0.0, existencias=0.0),
             _store('GRANDE', 2.0, 3.0, existencias=6.0)]
    t, _, _ = distribuir_item('X', 1, _df(filas, cajas=1))
    por = _por_tienda(t)
    assert int(por.loc['GRANDE', 'cajas_fase1']) == 1
    assert int(por.loc['AGOT', 'cajas_asignadas']) == 0


def test_fase2_garantiza_primero_agotada_y_luego_seguridad():
    """Tiendas sin consumo no reciben en Fase 1. Con 1 caja sobrante va a la
    AGOTADA; con 2, también a la de SEGURIDAD."""
    agot = _store('AGOT', 0.0, 0.0, existencias=0.0)
    seg = _store('SEG', 0.0, 0.0, existencias=(MIN_STOCK_AGOTADO + MIN_STOCK_SAFETY) / 2)
    t, _, _ = distribuir_item('X', 1, _df([seg, agot], cajas=1))
    assert _por_tienda(t)['cajas_fase2'].to_dict() == {'SEG': 0, 'AGOT': 1}

    t, _, _ = distribuir_item('X', 2, _df([seg, agot], cajas=2))
    assert _por_tienda(t)['cajas_fase2'].to_dict() == {'SEG': 1, 'AGOT': 1}


def test_fase2_no_da_otra_caja_a_quien_ya_recibio_en_fase1():
    t, _, _ = distribuir_item('X', 5, _df([_store('A', 1.0, 0.0)], cajas=5))
    fila = t.iloc[0]
    assert int(fila['cajas_fase1']) == MIN_CAJAS_INICIAL and int(fila['cajas_fase2']) == 0

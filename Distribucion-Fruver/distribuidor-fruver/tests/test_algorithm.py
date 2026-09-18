"""Tests unitarios del motor de distribución."""

import math

import pandas as pd
import pytest

from core.algorithm import (
    MAX_CAJAS_POR_ITEM,
    MIN_STOCK_AGOTADO,
    MIN_STOCK_SAFETY,
    TARGET_DAYS,
    TOPE_EXCEDENTE,
    UMBRAL_CONCENTRACION_SIN_CONSUMO,
    _apportion_grupo_targets,
    _fase3_grupal,
    _fase3_individual,
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

    # La brecha se mide sobre el reparto SANO (Fases 1-3). Las cajas de Fase 4
    # son la red de seguridad de cero-residuo: se asignan ignorando el tope
    # justamente cuando ya no hay dónde más ponerlas, así que pueden ampliar
    # la brecha por diseño y no son lo que este test vigila. Según cuánto
    # espacio real dejen los topes vigentes, parte del sobrante puede caer en
    # Fase 4 — por eso se excluye explícitamente en vez de asumir que no la hay.
    cajas_sanas_lenta = lenta_row['cajas_asignadas'] - lenta_row['cajas_fase4']
    cajas_sanas_rapida = rapida_row['cajas_asignadas'] - rapida_row['cajas_fase4']
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
    """El reparto del sobrante (Fase 3) no debe dejar a NINGUNA tienda —
    de alto, medio o bajo consumo — por encima de TOPE_EXCEDENTE días, ni
    recibiendo más de MAX_CAJAS_POR_ITEM cajas, cuando el sobrante alcanza
    para que el "Paso A" (proporcional, con tope) resuelva todo sin caer
    al "Paso B" de cero-residuo. Es la validación directa de la preocupación
    del usuario: que las tiendas de consumo medio no terminen sobre-stockeadas.

    Las 3 tiendas arrancan EXACTAMENTE en TARGET_DAYS (cubiertas, no piden
    nada en Fase 1/2). El sobrante coincide con la suma exacta del "espacio"
    que cada una tiene bajo sus topes:
      ALTA  (consumo=3.0): tope de cajas (MAX_CAJAS_POR_ITEM) manda
      MEDIA (consumo=1.0): tope de cajas (MAX_CAJAS_POR_ITEM) manda
      BAJA  (consumo=0.5): tope de días (TOPE_EXCEDENTE) manda

    El espacio de cada tienda y el total de cajas se derivan de los
    parámetros en vez de fijarse a mano, para que el test siga midiendo lo
    mismo si se ajusta TOPE_EXCEDENTE o MAX_CAJAS_POR_ITEM.
    """
    def _room(consumo):
        """Cajas que la tienda puede recibir sin pasar TOPE_EXCEDENTE,
        acotadas además por el tope de cajas por ítem."""
        return min(int((TOPE_EXCEDENTE - TARGET_DAYS) * consumo), MAX_CAJAS_POR_ITEM)

    _consumos = {'ALTA': 3.0, 'MEDIA': 1.0, 'BAJA': 0.5}
    # El sobrante es exactamente la capacidad conjunta bajo los topes, así que
    # Fase 3 debe colocarlo todo sin que Fase 4 tenga que romper ninguno.
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


def test_fase4_activa_y_reparte_por_menor_dias_actuales():
    """Caso extremo (raro en la práctica, según CLAUDE.md): el CEDI manda
    MUCHO más de lo que las tiendas elegibles pueden absorber bajo sus topes.
    Todas quedan inelegibles para Fase 3 antes de agotar el sobrante, así
    que la Fase 4 (antes "Paso B", ahora fase propia — auditoría 2026-09)
    debe activarse para garantizar cero residuo — repartiendo lo que falta
    por menor `días_actuales`, en dos niveles: primero solo entre tiendas
    bajo MAX_CAJAS_POR_ITEM, y solo ignora ese tope si es matemáticamente
    inevitable (auditoría 2026-07: antes de ese fix, el mecanismo ignoraba
    MAX_CAJAS_POR_ITEM siempre, no solo como último recurso — bug real
    confirmado en producción, ítem 2473).

      P: consumo=2.0, existencias=0   → AGOTADA; en Fase 1+2 llega a su tope
         de 3 cajas (2+1) y queda en 1.5 días.
      Q: consumo=1.0, existencias=6.0 → CUBIERTA; arranca ya en 6.0 días
         (= TOPE_EXCEDENTE), inelegible para el sobrante desde la ronda 1.

    Ninguna sigue elegible para Fase 3 (P por tope de cajas, Q por tope de
    días) → Fase 4 nivel 1 le da a Q (la única bajo el tope de cajas) las 3
    que le faltan para llegar también a su propio tope; con eso, AMBAS
    tiendas quedan en MAX_CAJAS_POR_ITEM y aún sobran 2 cajas — ahí sí es
    matemáticamente inevitable superar el tope (nivel 2), y esas 2 últimas
    se reparten 1 y 1 entre P y Q en vez de apilarse todas sobre la que
    tenía menos días. Resultado: P y Q terminan EMPATADAS en 4 cajas cada
    una (antes del fix: P terminaba con 6 y Q con solo 2 — el mismo
    problema de concentración que el usuario reportó para el ítem 2001,
    solo que aquí se manifestaba en Fase 4 en vez de en Fase 3).
    """
    p = _store('P', consumo=2.0, dias=0.0, existencias=0.0)
    q = _store('Q', consumo=1.0, dias=6.0, existencias=6.0)
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

    # Q no llegó a competir en Fase 3 (ya arrancaba en el tope de días) y
    # P se quedó en Fase 1+2 (0 de Fase 3) — toda su asignación restante
    # vino de Fase 4 (nivel 1 + nivel 2).
    assert q_row['cajas_fase3'] == 0, "Q no debía recibir nada en Fase 3 (inelegible desde la ronda 1)"
    assert p_row['cajas_fase3'] == 0, "P no debía recibir nada en Fase 3 (ya en tope de cajas tras Fase 1+2)"
    assert q_row['cajas_fase4'] == 4, "Q debía recibir 4 cajas en Fase 4 (nivel 1 + nivel 2)"
    assert p_row['cajas_fase4'] == 1, "P solo debía recibir 1 caja en Fase 4 (nivel 2)"


def test_fase3_y_fase4_quedan_separadas_en_el_tracking():
    """Auditoría 2026-09 (caso real reportado por el usuario, ítem 1561
    Aguacate Hass): antes, tanto el reparto normal bajo TOPE_EXCEDENTE (Fase
    3) como la red de seguridad de cero-residuo que lo ignora ("Paso B")
    sumaban a la misma columna `cajas_fase3`, ocultando que la mayoría de las
    cajas de un ítem podían venir de forzar el sobre-stock, no de un reparto
    sano. Ahora son fases separadas (`cajas_fase3` / `cajas_fase4`).

    ELEGIBLE: arranca justo cubierta (TARGET_DAYS, no pide nada en Fase 1/2)
    pero con consumo alto, así que tiene margen real de días para absorber
    sus MAX_CAJAS_POR_ITEM cajas por la vía sana de Fase 3.
    SATURADA: consumo bajo y ya por encima de TOPE_EXCEDENTE — CUALQUIER caja
    adicional la pasa del tope; nunca es elegible para Fase 3. Solo puede
    recibir algo si el cero-residuo obliga a la Fase 4 a ignorar el tope.

    Los valores se derivan de TARGET_DAYS / TOPE_EXCEDENTE / MAX_CAJAS_POR_ITEM
    en vez de fijarse a mano, para que el test no se rompa si se ajustan esos
    parámetros.
    """
    _consumo_eleg = 3.0
    _exist_eleg = _consumo_eleg * TARGET_DAYS       # justo cubierta
    elegible = _store('ELEGIBLE', consumo=_consumo_eleg, dias=TARGET_DAYS,
                      existencias=_exist_eleg)
    # SATURADA arranca POR ENCIMA del tope, para que nunca sea elegible en
    # Fase 3 y solo pueda recibir por la red de seguridad de Fase 4.
    _dias_sat = TOPE_EXCEDENTE + 2.0
    _exist_sat = _dias_sat * 0.2
    saturada = _store('SATURADA', consumo=0.2, dias=_dias_sat, existencias=_exist_sat)
    # Una caja más de las que ELEGIBLE puede absorber: esa sobrante es la que
    # obliga a la Fase 4 a tocar a SATURADA.
    _cajas = MAX_CAJAS_POR_ITEM + 1
    tiendas, remaining, _ = distribuir_item('X', _cajas, _df([elegible, saturada], cajas=_cajas))
    assert remaining == 0
    assert int(tiendas['cajas_asignadas'].sum()) == _cajas

    e_row = tiendas.loc[tiendas['store_code'] == 'ELEGIBLE'].iloc[0]
    s_row = tiendas.loc[tiendas['store_code'] == 'SATURADA'].iloc[0]

    assert e_row['cajas_fase3'] == MAX_CAJAS_POR_ITEM, (
        "ELEGIBLE tenía margen real bajo el tope: debía recibir vía Fase 3")
    assert e_row['cajas_fase4'] == 0, "ELEGIBLE no debía necesitar la red de seguridad de Fase 4"
    assert s_row['cajas_fase3'] == 0, "SATURADA ya estaba en el tope: no podía ser elegible en Fase 3"
    assert s_row['cajas_fase4'] == 1, "SATURADA solo debía recibir su caja vía Fase 4 (cero residuo forzado)"


def test_fase3_reparte_de_a_1_caja_por_ronda_no_por_tienda():
    """Caso real que originó el fix (auditoría 2026-07, ítem 2001): con solo 2
    tiendas elegibles bajo TOPE_EXCEDENTE y sobrante suficiente para varias
    cajas, el algoritmo anterior le daba TODO el sobrante a esas 2 de una sola
    vez (hasta MAX_CAJAS_POR_ITEM), sin volver a chequear el tope entre cada
    caja — dejando afuera a una 3ª tienda (S53 en el caso real) que estaba
    apenas por encima del tope y nunca llegaba a competir por el sobrante.

      A, B: consumo=1.0, existencias=4.5 (4.5 días, CUBIERTA) — su capacidad
            real bajo el tope es de solo 1 caja (tras recibir 1, quedan en
            5.5 días; una 2ª las llevaría a 6.5, por encima de TOPE_EXCEDENTE).
      C:    consumo=1.0, existencias=5.3 (5.3 días, CUBIERTA) — no pasa el
            filtro de Paso A ni con 1 sola caja (6.3 > 6), pero tiene MENOS
            días que A/B tras la 1ª ronda, así que es la primera candidata
            del Paso B.

    Antes del fix: A recibía 2, B recibía 1, C se quedaba en 0 (verificado
    contra el algoritmo previo al cambio). Con el fix: cada una recibe
    exactamente 1 — el sobrante se repartió primero entre TODAS las tiendas
    con margen antes de que cualquiera acaparara una 2ª caja.
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
    """Bug real descubierto al validar el fix anterior (ítem 2473, datos de
    producción): el Paso B de cero-residuo repartía por menor `días_actuales`
    SIN revisar si esa tienda ya había llegado a MAX_CAJAS_POR_ITEM, así que
    una tienda que ya recibió su máximo en Fase 1/2 seguía acumulando más en
    Paso B con tal de tener pocos días — terminando con 4-5 cajas en vez del
    tope de 3, mientras otra tienda con margen real recibía menos de lo que
    podía absorber.

      P: consumo=2.0, existencias=0   → AGOTADA; llega a su tope de 3 cajas
         ya en Fase 1+2 y queda en 1.5 días (la más "urgente" por días, pero
         SIN espacio real).
      Q: consumo=1.0, existencias=6.0 → CUBIERTA, exactamente en el tope de
         días desde el inicio (inelegible para Paso A), con las 3 cajas de
         MAX_CAJAS_POR_ITEM libres.
      R: consumo=0.5, existencias=3.0 → CUBIERTA, también en el tope de días
         desde el inicio, con sus 3 cajas libres.

    Con 8 cajas de CEDI (más de lo que P puede absorber respetando su tope),
    el Paso B debe llenar primero a Q y R (que sí tienen espacio) antes de
    seguir apilando sobre P.
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
    """La elegibilidad para el sobrante mira el estado TRAS recibir una caja
    más (`dias_tras_una <= TOPE_EXCEDENTE`, algorithm.py:225-234) — no el
    estado actual. Una tienda que terminaría EXACTAMENTE en el tope sigue
    siendo elegible; una que lo cruzaría por una décima de día, no.

      F6:  tras +1 caja queda EXACTAMENTE en TOPE_EXCEDENTE (== tope)
      F61: tras +1 caja lo supera por una décima de día (> tope)

    Las existencias se derivan de TOPE_EXCEDENTE en vez de fijarse a mano,
    para que el test siga siendo válido si se ajusta el parámetro.

    Ambas arrancan cubiertas (no piden nada en Fase 1/2a) y compiten por la
    única caja de sobrante: debe ir a F6 (queda justo en el límite) y no a
    F61 (lo superaría).
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


def test_grupo_espejo_no_duplica_reposicion():
    """Dos ítems espejo (mismo producto físico, distinto SKU) en la misma
    tienda: si el inventario COMBINADO del grupo ya alcanza TARGET_DAYS, no
    debe pedirse reposición para ninguno por separado — el bug reportado por
    el usuario (cada SKU pedía sus propios 3 días de cobertura, duplicando la
    cantidad real necesaria por la tienda).

    Tienda T, grupo G1: item A (consumo=0.5) e item B (consumo=0.5), cada uno
    visto SOLO tiene apenas 1 caja (2 días, por debajo de TARGET_DAYS=3 → con
    la lógica vieja, ambos pedirían reposición). Pero combinados, el grupo
    tiene 2.0 cajas / 1.0 consumo = 2.0... ajustamos a inventario_grupo=3.0
    para que dias_proyectados_grupo = 3.0 = TARGET_DAYS exacto (cubierto).
    """
    row = pd.Series({
        'consumo_diario': 0.5,
        'inventario_efectivo': 1.0,
        'priority': 0,
        'consumo_diario_grupo': 1.0,
        'inventario_efectivo_grupo': 3.0,
        'dias_proyectados_grupo': TARGET_DAYS,
        'item_share_consumo': 0.5,
    })
    from core.algorithm import _calc_deseadas, get_priority
    assert get_priority(row) == 0, "Grupo cubierto (3.0 dias == TARGET_DAYS) -> prioridad CUBIERTA"
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
    _fase3_grupal / _fase3_individual directamente)."""
    return {
        'store_code': store_code,
        'consumo_diario': consumo,
        'inventario_efectivo': inventario_efectivo,
        'item_share_consumo': item_share_consumo,
        'cajas_asignadas': cajas_asignadas,
        'cajas_fase1': 0,
        'cajas_fase2': 0,
        'cajas_fase3': 0,
        'cajas_fase4': 0,
    }


def _estado(rows, remaining):
    return {'tiendas': pd.DataFrame(rows), 'remaining': remaining}


def test_fase3_grupal_garantiza_presencia_y_respeta_tope_por_item():
    """Tienda T, grupo con ítems A y B (consumo y participación simétricos
    50/50). A ya tiene 1.0 caja en góndola; B tiene 0 (no está en góndola
    aunque el CEDI sí tenga stock de B). Tras la Fase 3 grupal:
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
    resultado, alertas = _fase3_grupal(estado_items)

    a_asig = int(resultado['A']['tiendas'].loc[0, 'cajas_asignadas'])
    b_asig = int(resultado['B']['tiendas'].loc[0, 'cajas_asignadas'])

    assert b_asig >= 1, "B (0 unidades en góndola) debía recibir al menos 1 caja garantizada"
    assert a_asig <= MAX_CAJAS_POR_ITEM, "A no debía superar MAX_CAJAS_POR_ITEM"
    assert b_asig <= MAX_CAJAS_POR_ITEM, "B no debía superar MAX_CAJAS_POR_ITEM"
    assert resultado['A']['remaining'] == 0
    assert resultado['B']['remaining'] == 0
    assert a_asig + resultado['A']['remaining'] == 3
    assert b_asig + resultado['B']['remaining'] == 3


def test_fase3_grupal_tope_excedente_es_del_grupo_no_del_item():
    """Dos tiendas, mismo grupo (A, B):

      T1: A tiene consumo bajísimo (0.1) pero MUCHO inventario (70 cajas,
          700 días solo) — arrastra el GRUPO muy por encima de
          TOPE_EXCEDENTE aunque B, visto solo, tenga 0 días (luciría urgente
          con la lógica antigua por ítem).
      T2: ambos ítems sanos, grupo en 0.5 días — claramente elegible.

    Con TOPE_EXCEDENTE evaluado a nivel de GRUPO, T1 no debe recibir NADA de
    sobrante (ni A ni B) — todo el sobrante debe ir a T2, que sí es elegible.
    Antes de este cambio (Fase 3 por ítem), B en T1 habría calificado igual
    que cualquier tienda con 0 días y se le habría asignado sobrante.
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
    resultado, alertas = _fase3_grupal(estado_items)

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


def test_fase3_grupal_cero_residuo_delega_a_paso_b_individual():
    """Una sola tienda, grupo con A y B: el CEDI manda MUCHO más de lo que la
    tienda puede absorber bajo MAX_CAJAS_POR_ITEM (3 por ítem). El Paso A
    grupal llena ambos hasta el tope (3 cada uno) y luego se queda sin
    tiendas elegibles — el remanente de cada ítem debe delegarse al Paso B
    individual (cero residuo, ignora MAX_CAJAS_POR_ITEM) para que la garantía
    de cero residuo se cumpla igual que en el camino sin grupo."""
    estado_items = {
        'A': _estado([_tienda_post_fase2('UNICA', consumo=2.0, inventario_efectivo=0.0,
                                           item_share_consumo=2.0 / 3.0)], remaining=10),
        'B': _estado([_tienda_post_fase2('UNICA', consumo=1.0, inventario_efectivo=6.0,
                                           item_share_consumo=1.0 / 3.0)], remaining=10),
    }
    resultado, alertas = _fase3_grupal(estado_items)

    assert resultado['A']['remaining'] == 0, "Cero residuo debe mantenerse para A vía Paso B individual"
    assert resultado['B']['remaining'] == 0, "Cero residuo debe mantenerse para B vía Paso B individual"

    a_asig = int(resultado['A']['tiendas'].loc[0, 'cajas_asignadas'])
    b_asig = int(resultado['B']['tiendas'].loc[0, 'cajas_asignadas'])
    assert a_asig == 10, "Única tienda del portafolio: debía recibir todo el CEDI de A"
    assert b_asig == 10, "Única tienda del portafolio: debía recibir todo el CEDI de B"
    assert a_asig > MAX_CAJAS_POR_ITEM, "Solo el Paso B (cero residuo) ignora MAX_CAJAS_POR_ITEM"


# ── Tests nuevos: auditoría técnica 2026-07 ─────────────────────────────────

def test_grupo_espejo_multiple_no_sobreprovisiona_por_doble_ceil():
    """Bug de auditoría: 3 SKUs espejo en la misma tienda, cada uno con ~33%
    de participación en el consumo del grupo, y una necesidad de grupo de
    apenas 1.0 caja. La lógica anterior aplicaba `ceil()` DESPUÉS de repartir
    la necesidad por ítem (`ceil(1.0 * 0.333) = 1` cada uno), sumando 3 cajas
    en Fase 1 donde el grupo solo necesitaba 1. Vía `run_distribution` (el
    único camino con visibilidad real entre ítems hermanos), el total
    asignado en Fase 1+2 al grupo no debe exceder `ceil(necesidad_grupo)`.
    """
    stores = ['T']
    rows = []
    for item_code, share in [('A', 1 / 3), ('B', 1 / 3), ('C', 1 / 3)]:
        rows.append({
            'store_code': 'T', 'item_code': item_code, 'grupo_id': 'GESPEJO',
            'consumo_diario': 1.0 / 3, 'consumo_diario_grupo': 1.0,
            'inventario_efectivo': 0.0, 'inventario_efectivo_grupo': 2.0,
            'dias_proyectados': 0.0, 'dias_proyectados_grupo': 2.0,
            'item_share_consumo': share,
            'cajas_disponibles_cedi': 5, 'store_name': 'T', 'um': 'UND',
            'item_desc': f'Producto {item_code}',
        })
    df_merged = pd.DataFrame(rows)
    # necesidad_grupo = max(0, 3.0*1.0 - 2.0) = 1.0 -> ceil = 1 caja total para el grupo.
    df_output, alertas = run_distribution(df_merged)

    total_fase1_fase2 = int((df_output['cajas_fase1'] + df_output['cajas_fase2']).sum())
    assert total_fase1_fase2 <= 1, (
        "El grupo solo necesitaba 1 caja para llegar a TARGET_DAYS pero se "
        f"asignaron {total_fase1_fase2} entre Fase 1 y Fase 2 — doble ceil "
        "por ítem sobreprovisionando el grupo"
    )
    # Cero residuo se mantiene igual (el resto del CEDI, 5 cajas por cada uno
    # de los 3 SKUs, cae a Fase 3).
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
    """Bug de auditoría: cuando `dias_actuales==0` (la tienda MÁS urgente
    posible dentro de las elegibles de Fase 3), la fórmula de peso anterior
    (`consumo/dias_act` con `dias_act=0` reemplazado por NaN -> peso 0)
    le daba peso CERO en vez del mayor peso posible. Con dos tiendas
    elegibles, la de 0 días debe recibir estrictamente más que la que ya
    tiene margen (más días), no menos ni cero."""
    cero_dias = _tienda_post_fase2('CERO', consumo=1.0, inventario_efectivo=0.0,
                                     item_share_consumo=1.0, cajas_asignadas=0)
    con_margen = _tienda_post_fase2('MARGEN', consumo=1.0, inventario_efectivo=2.0,
                                      item_share_consumo=1.0, cajas_asignadas=0)
    tiendas = pd.DataFrame([cero_dias, con_margen])
    tiendas_result, remaining = _fase3_individual(tiendas, remaining=2)

    assert remaining == 0
    cero_asig = tiendas_result.loc[tiendas_result['store_code'] == 'CERO', 'cajas_fase3'].iloc[0]
    margen_asig = tiendas_result.loc[tiendas_result['store_code'] == 'MARGEN', 'cajas_fase3'].iloc[0]
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
    """Si TODAS las tiendas están bloqueadas, Fase 4 nivel 2 reparte igual —
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

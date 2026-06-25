"""Motor de distribución — el corazón del sistema.

Distribuye el 100% de las cajas disponibles en el CEDI entre las tiendas
elegibles de cada ítem en tres fases:

  FASE 1 — cubrir necesidades urgentes con cap creciente por rondas:
    Ordena tiendas por prioridad (AGOTADA → SAFETY → REPOSICIÓN → CUBIERTA)
    y dentro de cada nivel por consumo descendente. Ejecuta MIN_CAJAS_INICIAL
    rondas con cap 1, 2, 3…: en la ronda k cada tienda recibe hasta su k-ésima
    caja (deseadas(k) − ya_asignado). Todas las elegibles reciben su 1.ª caja
    antes de que cualquiera reciba su 2.ª, evitando concentración en tiendas de
    alto consumo cuando el stock es escaso.

  FASE 2a — escalar proporcionalmente a TARGET_DAYS:
    Cuando el stock no alcanza para llevar a TODOS a TARGET_DAYS, la escasez
    se reparte de forma proporcional a la necesidad de cada tienda. Ninguna
    tienda acapara cajas a costa de otras; todas reciben la misma fracción
    de su necesidad (corrección Hamilton para los residuos enteros).
    Cuando el stock SÍ alcanza, cada tienda recibe exactamente lo que necesita
    y el verdadero sobrante va a Fase 2b.

  FASE 2b — sobrante repartido por días, no por cajas:
    Repartir la misma CANTIDAD DE CAJAS a tiendas con consumos muy distintos
    reparte cantidades muy distintas de DÍAS (Δdías ≈ cajas/consumo). Por eso
    el sobrante se reparte por rondas proporcional a `consumo_diario /
    días_actuales` entre las tiendas elegibles (consumo > 0, bajo
    TOPE_EXCEDENTE tras recibir, sin alcanzar aún MAX_CAJAS_POR_ITEM) con
    corrección Hamilton para los residuos enteros. El resultado: cada tienda
    gana más días cuanto más atrás vaya respecto al resto (Δdías_i = k /
    días_i), cerrando la brecha en lugar de mantenerla o ampliarla — sin que
    ninguna reciba más cajas por entrega que MAX_CAJAS_POR_ITEM (límite de
    espacio en tienda). Tiendas sin consumo solo reciben en Fase 1 si están
    AGOTADAS o en SAFETY. Si todas llegan al tope (caso extremo), un Paso B de
    cero-residuo reparte por menor días proyectados ignorando ese límite.

    Para ítems de un mismo GRUPO ESPEJO (mismo producto físico, distinto SKU —
    ver `grupo_id` en preprocessor.py), la Fase 2b se ejecuta de forma
    CONJUNTA (`_fase2b_grupal`): elegibilidad, TOPE_EXCEDENTE y peso se
    calculan sobre el inventario/consumo COMBINADO del grupo en cada tienda,
    pero cada ítem sigue entregándose desde su propio stock CEDI
    (MAX_CAJAS_POR_ITEM sigue por SKU). Si una tienda recibe sobrante del
    grupo y un ítem del grupo tiene 0 unidades en esa tienda mientras el CEDI
    aún tiene stock de él, se le garantiza 1 caja de ese ítem antes de
    repartir el resto proporcional al consumo de cada SKU. Si tras esto algún
    ítem del grupo todavía tiene remanente (p. ej. recibió mucho más stock
    CEDI del que el portafolio combinado puede absorber), ese remanente se
    delega al Paso B individual de siempre (cero residuo por SKU).
"""

import math

import pandas as pd

TARGET_DAYS = 3.0           # días objetivo de inventario proyectado por tienda
MIN_STOCK_AGOTADO = 0.3     # por debajo de este STOCK (unidades) = AGOTADA
MIN_STOCK_SAFETY = 0.6      # por debajo de este STOCK (unidades) = STOCK SEGURIDAD
MIN_CAJAS_INICIAL = 2       # cap de cajas por tienda en Fase 1 (por ronda)
MAX_CAJAS_POR_ITEM = 3      # tope duro: máximo que una tienda recibe por ítem (todas las fases)
TOPE_EXCEDENTE = 6       # máximo días que puede acumular una tienda del excedente
UMBRAL_CONCENTRACION_SIN_CONSUMO = 5  # cajas: si Paso B concentraría >= esto en 1 tienda
                                       # por falta de consumo en el resto, reparto equitativo


def get_priority(row):
    """Clasifica la urgencia de una tienda-ítem.

    Usa las métricas a nivel de GRUPO espejo (inventario_efectivo_grupo,
    dias_proyectados_grupo), no las del ítem individual: dos ítems espejo que
    cubren la misma demanda no deben clasificarse como AGOTADA/REPOSICIÓN por
    separado si entre los dos ya hay stock suficiente en la tienda. Para un
    ítem sin espejo (grupo de tamaño 1) estos valores son idénticos a los
    individuales, así que el comportamiento no cambia.
    """
    inv = row.inventario_efectivo_grupo
    if inv < MIN_STOCK_AGOTADO:
        return 3  # AGOTADA — stock prácticamente en 0
    if inv < MIN_STOCK_SAFETY:
        return 2  # STOCK DE SEGURIDAD
    if row.dias_proyectados_grupo < TARGET_DAYS:
        return 1  # NECESITA REPOSICIÓN
    return 0      # CUBIERTA (solo recibe si sobra)


def _calc_deseadas(row, cap):
    """Cajas que la tienda querría recibir, limitadas por el cap del pase.

    La necesidad se calcula a nivel de GRUPO espejo (cuántas cajas hacen falta
    para que el conjunto de ítems espejo llegue a TARGET_DAYS) y se reparte
    entre los ítems del grupo proporcional al consumo de cada uno
    (item_share_consumo) — así nunca se duplica la reposición entre ítems que
    cubren la misma demanda. Para un ítem sin espejo, item_share_consumo == 1
    y el resultado es idéntico al cálculo individual de antes.

    consumo_diario e inventario_efectivo están en cajas (Unidades de Distribución),
    igual que cajas_disponibles_cedi, así que no se necesita conversión.
    """
    if row.consumo_diario <= 0:
        # Sin consumo: no se puede proyectar demanda; mínimo según prioridad.
        return 1 if row.priority >= 2 else 0
    # Cajas necesarias para que el GRUPO alcance el objetivo de días, repartidas
    # entre los ítems del grupo según su participación en el consumo.
    necesidad_grupo = max(0.0, TARGET_DAYS * row.consumo_diario_grupo - row.inventario_efectivo_grupo)
    cajas_needed = math.ceil(necesidad_grupo * row.item_share_consumo)
    min_boxes = 1 if row.priority >= 2 else 0
    return max(min_boxes, min(cajas_needed, cap))


def _reparto_proporcional(pesos, total, room):
    """Reparte hasta `total` cajas (entero) proporcional a `pesos` (>= 0), sin
    exceder `room` (espacio restante) por tienda. Usa piso + corrección
    Hamilton para que la suma entera coincida con `total` — o con `room.sum()`
    si éste es menor — repartiendo el residuo a quienes tienen mayor fracción
    pendiente y todavía tienen espacio.

    Si el peso de cada tienda es proporcional a su `consumo_diario`, el
    incremento de stock resultante también lo es (`Δstock_i = k · peso_i`), lo
    que hace que el incremento de días proyectados (`Δdías_i = Δstock_i /
    consumo_i`) sea igual para todas — reparte días, no solo cajas.

    Retorna una Series de enteros con las cajas asignadas en esta pasada
    (mismo índice que `pesos`).
    """
    asignado = pd.Series(0, index=pesos.index, dtype=int)
    total_pesos = float(pesos.sum())
    total = int(total)
    if total_pesos <= 0 or total <= 0:
        return asignado

    objetivo = (pesos / total_pesos * total).clip(upper=room)
    piso = objetivo.apply(math.floor).astype(int)
    asignado += piso
    sobra = total - int(piso.sum())

    if sobra > 0:
        espacio = room - piso
        fracs = (objetivo - piso).where(espacio > 0, -1.0).sort_values(ascending=False)
        for idx in fracs.index:
            if sobra <= 0 or fracs[idx] < 0:
                break
            asignado[idx] += 1
            sobra -= 1

    return asignado


def _priorizar_y_fase1_2a(cajas_disponibles, tiendas_df):
    """Calcula prioridad y ejecuta Fase 1 + Fase 2a para un ítem.

    Retorna (tiendas, remaining). No genera alertas — las fases 1/2a nunca
    dejan remanente "atascado" sin tiendas elegibles de forma anómala (eso
    solo puede pasar en Fase 2b); si `tiendas_df` está vacío, `remaining`
    simplemente queda igual a `cajas_disponibles`.
    """
    tiendas = tiendas_df.copy()
    tiendas['priority'] = tiendas.apply(get_priority, axis=1)
    tiendas['cajas_asignadas'] = 0
    tiendas['cajas_fase1'] = 0
    tiendas['cajas_fase2a'] = 0
    tiendas['cajas_fase2b'] = 0
    remaining = int(cajas_disponibles)

    if len(tiendas) == 0:
        return tiendas, remaining

    # Orden: prioridad DESC, consumo DESC (agotados y mejores vendedores primero).
    tiendas = tiendas.sort_values(
        ['priority', 'consumo_diario'], ascending=False
    ).reset_index(drop=True)

    # FASE 1 — rondas con cap creciente (1 → MIN_CAJAS_INICIAL).
    # En cada ronda, la tienda recibe el delta entre deseadas(cap) y lo ya
    # asignado. Todas las elegibles reciben su 1.ª caja antes de que cualquiera
    # reciba su 2.ª, evitando que las tiendas de alto consumo acaparen cuando
    # el stock es escaso y hay múltiples tiendas AGOTADAS.
    for cap in range(1, MIN_CAJAS_INICIAL + 1):
        if remaining <= 0:
            break
        for idx, row in tiendas.iterrows():
            if remaining <= 0:
                break
            deseadas = _calc_deseadas(row, cap)
            adicional = max(0, deseadas - int(tiendas.at[idx, 'cajas_asignadas']))
            asignar = min(adicional, remaining)
            tiendas.at[idx, 'cajas_asignadas'] += asignar
            tiendas.at[idx, 'cajas_fase1'] += asignar
            remaining -= asignar

    # FASE 2a — completar TARGET_DAYS de forma proporcional.
    # Calcula la necesidad de cada tienda para llegar a TARGET_DAYS y distribuye
    # el stock restante con un factor de escala. Si el stock alcanza para todos,
    # cada tienda recibe exactamente lo que necesita. Si no alcanza, la escasez
    # se reparte de forma proporcional (corrección Hamilton para residuos).
    if remaining > 0:
        con_consumo = tiendas['consumo_diario'] > 0
        needs = pd.Series(0.0, index=tiendas.index)
        needs[con_consumo] = (
            (
                TARGET_DAYS * tiendas.loc[con_consumo, 'consumo_diario_grupo']
                - tiendas.loc[con_consumo, 'inventario_efectivo_grupo']
            ).clip(lower=0) * tiendas.loc[con_consumo, 'item_share_consumo']
            - tiendas.loc[con_consumo, 'cajas_asignadas']
        ).clip(lower=0)

        # Tope por ítem: la tienda solo puede recibir hasta lo que le falta para MAX_CAJAS_POR_ITEM.
        room = (MAX_CAJAS_POR_ITEM - tiendas['cajas_asignadas']).clip(lower=0)
        needs = needs.clip(upper=room)

        total_need = needs.sum()

        if total_need > 0:
            # min(remaining, total_need): si el stock alcanza, cada tienda
            # recibe exactamente su `needs` (ya acotada por `room`); si no
            # alcanza, `_reparto_proporcional` reparte `remaining` de forma
            # proporcional a `needs` — equivalente al `scale` anterior, pero
            # sin arriesgar exceder `room` en la corrección Hamilton.
            asignado = _reparto_proporcional(needs, min(remaining, total_need), room)
            tiendas['cajas_asignadas'] += asignado
            tiendas['cajas_fase2a'] += asignado
            remaining -= int(asignado.sum())

    return tiendas, remaining


def _fase2b_individual(item_code, tiendas, remaining):
    """Reparte el sobrante de UN ítem entre sus tiendas (sin ver otros ítems
    del mismo grupo espejo). Usado para ítems sin espejo y como red de
    seguridad de cero-residuo cuando la Fase 2b grupal no logra colocar todo
    el remanente de un ítem específico.

    Retorna (tiendas, remaining, alertas_item). remaining debe terminar en 0.
    """
    tiendas = tiendas.copy()
    alertas_item = []

    if len(tiendas) == 0 or remaining <= 0:
        return tiendas, remaining, alertas_item

    # FASE 2b — excedente: repartir cada ronda proporcional a
    # `consumo_diario / días_actuales` entre las tiendas elegibles (consumo > 0,
    # bajo TOPE_EXCEDENTE tras recibir, sin alcanzar aún MAX_CAJAS_POR_ITEM).
    #
    # Por qué ese peso y no "1 caja por tienda" ni "proporcional al consumo a
    # secas": repartir cantidades IGUALES de cajas reparte cantidades MUY
    # DESIGUALES de días (Δdías ≈ cajas / consumo, y el consumo varía hasta
    # ~12x entre tiendas) — la tienda lenta gana muchos más días por caja que
    # la rápida, que es justo el desbalance que se busca evitar. Ponderar por
    # `consumo_diario / días_actuales` (= consumo² / stock) dosifica el
    # excedente según dos señales: cuánto vende (consumo) y qué tan atrás va
    # respecto al resto (1/días); el resultado es que el incremento de días de
    # cada tienda es inversamente proporcional a sus días actuales
    # (Δdías_i = k / días_i): las que van más atrasadas se acercan más rápido
    # al resto en lugar de mantener — o ampliar — la brecha.
    con_consumo_mask = tiendas['consumo_diario'] > 0

    if con_consumo_mask.any():
        # Paso A: por rondas — cada ronda puede sacar tiendas de la
        # elegibilidad (alcanzan el tope de días o el máximo de cajas), así
        # que se recalcula el peso y el espacio disponible y se reparte de
        # nuevo lo que quede entre las que siguen elegibles.
        prev = remaining + 1
        while remaining > 0 and remaining < prev:
            prev = remaining
            stock_act = tiendas['inventario_efectivo'] + tiendas['cajas_asignadas']
            dias_act = pd.Series(float('inf'), index=tiendas.index)
            dias_act[con_consumo_mask] = (
                stock_act[con_consumo_mask]
                / tiendas.loc[con_consumo_mask, 'consumo_diario']
            )
            # Elegible si, tras recibir una caja más, sigue bajo el tope —
            # evita que una sola caja empuje a una tienda lenta muy por
            # encima de TOPE_EXCEDENTE (chequear con `dias_act` permitía
            # el sobrepaso porque medía el estado ANTES de asignar).
            dias_tras_una = pd.Series(float('inf'), index=tiendas.index)
            dias_tras_una[con_consumo_mask] = (
                (stock_act[con_consumo_mask] + 1)
                / tiendas.loc[con_consumo_mask, 'consumo_diario']
            )
            elegibles_mask = (
                con_consumo_mask
                & (dias_tras_una <= TOPE_EXCEDENTE)
                & (tiendas['cajas_asignadas'] < MAX_CAJAS_POR_ITEM)
            )
            if not elegibles_mask.any():
                break
            pesos = (tiendas['consumo_diario'] / dias_act.replace(0, float('nan'))).where(elegibles_mask, 0.0)
            pesos = pesos.fillna(0.0)
            room = ((MAX_CAJAS_POR_ITEM - tiendas['cajas_asignadas'])
                    .clip(lower=0)
                    .where(elegibles_mask, 0))
            asignado = _reparto_proporcional(pesos, remaining, room)
            tiendas['cajas_asignadas'] += asignado
            tiendas['cajas_fase2b'] += asignado
            remaining -= int(asignado.sum())

        # Paso B: si todas llegaron al tope (caso extremo), red de
        # seguridad de cero-residuo por menos días proyectados — ignora
        # MAX_CAJAS_POR_ITEM porque ya no hay tiendas elegibles bajo tope.
        if remaining > 0:
            stock_act = tiendas['inventario_efectivo'] + tiendas['cajas_asignadas']
            dias_act = (stock_act[con_consumo_mask]
                        / tiendas.loc[con_consumo_mask, 'consumo_diario'])
            order = dias_act.sort_values(ascending=True).index.tolist()

            n_con_consumo = int(con_consumo_mask.sum())
            n_sin_consumo = len(tiendas) - n_con_consumo
            max_concentracion = math.ceil(remaining / n_con_consumo)

            if max_concentracion >= UMBRAL_CONCENTRACION_SIN_CONSUMO and n_sin_consumo > 0:
                # El reparto normal de Paso B (solo tiendas con consumo>0)
                # concentraría demasiadas cajas en pocas tiendas porque el
                # resto del portafolio no tiene datos de venta para este
                # ítem. En su lugar, repartir parejo entre TODAS las
                # tiendas del portafolio activo del ítem.
                tienda_concentracion = tiendas.at[order[0], 'store_code']
                remaining_original = remaining
                while remaining > 0:
                    idx_min = tiendas['cajas_asignadas'].idxmin()
                    tiendas.at[idx_min, 'cajas_asignadas'] += 1
                    tiendas.at[idx_min, 'cajas_fase2b'] += 1
                    remaining -= 1
                alertas_item.append({
                    'tipo': 'ADVERTENCIA',
                    'item': item_code,
                    'mensaje': (
                        f"Ítem {item_code}: solo {n_con_consumo} de {len(tiendas)} "
                        f"tienda(s) del portafolio tienen consumo registrado "
                        f"({n_sin_consumo} sin datos de venta) — el sobrante se iba a "
                        f"concentrar en {tienda_concentracion} (+{max_concentracion} "
                        f"cajas). Se aplicó reparto equitativo entre las {len(tiendas)} "
                        f"tiendas del portafolio activo. Revisar si faltan datos de "
                        f"venta o si llegó más inventario del que la demanda real del "
                        f"ítem justifica."
                    ),
                    'cajas_redistribuidas': int(remaining_original),
                    'tiendas_sin_consumo': int(n_sin_consumo),
                    'tiendas_con_consumo': int(n_con_consumo),
                    'tienda_concentracion_original': str(tienda_concentracion),
                    'cajas_concentracion_original': int(max_concentracion),
                })
            else:
                i = 0
                while remaining > 0:
                    tiendas.at[order[i % len(order)], 'cajas_asignadas'] += 1
                    tiendas.at[order[i % len(order)], 'cajas_fase2b'] += 1
                    remaining -= 1
                    i += 1
    else:
        # Sin consumo en ninguna tienda: round-robin hasta vaciar (caso extremo).
        indices = list(tiendas.index)
        i = 0
        while remaining > 0 and indices:
            tiendas.at[indices[i % len(indices)], 'cajas_asignadas'] += 1
            tiendas.at[indices[i % len(indices)], 'cajas_fase2b'] += 1
            remaining -= 1
            i += 1

    return tiendas, remaining, alertas_item


def _fase2b_grupal(estado_items):
    """Reparte el sobrante de un GRUPO de ítems espejo de forma conjunta.

    `estado_items` es un dict {item_code: {'tiendas': df, 'remaining': int}}
    con todos los ítems de un mismo `grupo_id` ya resueltos en Fase 1/2a
    (vía `_priorizar_y_fase1_2a`). Cada `tiendas` df tiene columna
    `store_code` única por fila.

    Elegibilidad, TOPE_EXCEDENTE y peso (`consumo/días_actuales`) se calculan
    sobre el inventario y consumo COMBINADO del grupo en cada tienda; el total
    repartido a una tienda en una ronda se separa luego entre los ítems del
    grupo presentes en ella, garantizando primero 1 caja a cualquier ítem que
    la tienda tenga en 0 unidades (si el CEDI todavía tiene stock de él), y
    repartiendo el resto proporcional a `item_share_consumo`. MAX_CAJAS_POR_ITEM
    se sigue respetando por ítem individual. Cada ítem nunca usa el stock CEDI
    de otro — solo se comparte la decisión de "cuánto le toca a esta tienda".

    Retorna (resultado, alertas_por_item):
      - resultado: dict {item_code: {'tiendas': df, 'remaining': int}}
      - alertas_por_item: dict {item_code: [alertas...]}
    """
    alertas_por_item = {code: [] for code in estado_items}
    tablas = {}
    remaining = {}

    for code, est in estado_items.items():
        t = est['tiendas'].copy()
        if len(t) > 0:
            t = t.set_index('store_code', drop=False)
            t['garantizado'] = False
        tablas[code] = t
        remaining[code] = int(est['remaining'])

    items_con_tiendas = [c for c in tablas if len(tablas[c]) > 0]

    # PASO A GRUPAL — análogo al Paso A individual, pero agregando por tienda
    # todos los ítems del grupo presentes en ella.
    total_remaining = sum(remaining.values())
    prev_total = total_remaining + 1
    while total_remaining > 0 and total_remaining < prev_total:
        prev_total = total_remaining

        activos = [c for c in items_con_tiendas if remaining[c] > 0]
        if not activos:
            break

        stores_union = sorted(set().union(*(tablas[c].index for c in activos)))
        if not stores_union:
            break

        consumo_grupo = pd.Series(0.0, index=stores_union)
        stock_act_grupo = pd.Series(0.0, index=stores_union)
        room_store = pd.Series(0, index=stores_union, dtype=int)
        for c in items_con_tiendas:
            t = tablas[c]
            presentes = t.index.intersection(stores_union)
            if len(presentes) == 0:
                continue
            consumo_grupo.loc[presentes] += t.loc[presentes, 'consumo_diario']
            stock_act_grupo.loc[presentes] += (
                t.loc[presentes, 'inventario_efectivo'] + t.loc[presentes, 'cajas_asignadas']
            )
            room_item = (MAX_CAJAS_POR_ITEM - t.loc[presentes, 'cajas_asignadas']).clip(lower=0)
            room_item = room_item.clip(upper=remaining[c]).astype(int)
            room_store.loc[presentes] += room_item

        con_consumo_mask = consumo_grupo > 0
        dias_act = pd.Series(float('inf'), index=stores_union)
        dias_act[con_consumo_mask] = stock_act_grupo[con_consumo_mask] / consumo_grupo[con_consumo_mask]
        dias_tras_una = pd.Series(float('inf'), index=stores_union)
        dias_tras_una[con_consumo_mask] = (
            (stock_act_grupo[con_consumo_mask] + 1) / consumo_grupo[con_consumo_mask]
        )
        elegibles_mask = con_consumo_mask & (dias_tras_una <= TOPE_EXCEDENTE) & (room_store > 0)
        if not elegibles_mask.any():
            break

        pesos = (consumo_grupo / dias_act.replace(0, float('nan'))).where(elegibles_mask, 0.0).fillna(0.0)
        room_elig = room_store.where(elegibles_mask, 0)
        total_pool = sum(remaining[c] for c in activos)
        asignado_store = _reparto_proporcional(pesos, total_pool, room_elig)

        for store in asignado_store[asignado_store > 0].index:
            n = int(asignado_store[store])

            candidatos = [
                c for c in items_con_tiendas
                if store in tablas[c].index
                and remaining[c] > 0
                and (MAX_CAJAS_POR_ITEM - int(tablas[c].at[store, 'cajas_asignadas'])) > 0
            ]
            if not candidatos:
                continue

            # Garantía de presencia: 1 caja a cualquier ítem del grupo con 0
            # unidades en esta tienda, antes de repartir proporcional.
            for c in candidatos:
                if n <= 0:
                    break
                t = tablas[c]
                stock_item = t.at[store, 'inventario_efectivo'] + t.at[store, 'cajas_asignadas']
                if stock_item <= 1e-9 and not bool(t.at[store, 'garantizado']):
                    t.at[store, 'cajas_asignadas'] += 1
                    t.at[store, 'cajas_fase2b'] += 1
                    t.at[store, 'garantizado'] = True
                    remaining[c] -= 1
                    n -= 1

            if n <= 0:
                continue

            candidatos_restantes = [
                c for c in candidatos
                if remaining[c] > 0
                and (MAX_CAJAS_POR_ITEM - int(tablas[c].at[store, 'cajas_asignadas'])) > 0
            ]
            if not candidatos_restantes:
                continue

            pesos_item = pd.Series({c: tablas[c].at[store, 'item_share_consumo'] for c in candidatos_restantes})
            room_item = pd.Series({
                c: min(MAX_CAJAS_POR_ITEM - int(tablas[c].at[store, 'cajas_asignadas']), remaining[c])
                for c in candidatos_restantes
            })
            asignado_item = _reparto_proporcional(pesos_item, n, room_item)
            for c, cajas in asignado_item.items():
                if cajas <= 0:
                    continue
                tablas[c].at[store, 'cajas_asignadas'] += int(cajas)
                tablas[c].at[store, 'cajas_fase2b'] += int(cajas)
                remaining[c] -= int(cajas)

        total_remaining = sum(remaining.values())

    # PASO B — cualquier remanente por ítem (p. ej. un SKU recibió mucho más
    # stock CEDI del que el portafolio combinado puede absorber) se delega al
    # Paso B individual de siempre: a estas alturas el grupo ya cumplió su
    # función (evitar duplicar necesidad/excedente mientras había alternativa
    # real); lo que queda es excedente genuino de ESE SKU que debe salir
    # completo del CEDI sin importar el grupo.
    resultado = {}
    for code, est in estado_items.items():
        t = tablas[code]
        if len(t) > 0:
            t = t.drop(columns=['garantizado']).reset_index(drop=True)
        rem = remaining[code]
        if rem > 0:
            t, rem, alertas_item = _fase2b_individual(code, t, rem)
            alertas_por_item[code].extend(alertas_item)
        resultado[code] = {'tiendas': t, 'remaining': rem}

    return resultado, alertas_por_item


def distribuir_item(item_code, cajas_disponibles, tiendas_df):
    """Distribuye `cajas_disponibles` de un ítem entre sus tiendas elegibles,
    tratándolo como un grupo de tamaño 1 (sin coordinación con otros ítems).

    Retorna (tiendas_df_con_asignacion, remaining, alertas_item). remaining debe
    ser 0; si es > 0 significa que no hubo tiendas suficientes para absorber el
    inventario. alertas_item es una lista (posiblemente vacía) de dicts
    {tipo, item, mensaje, ...} con advertencias detectadas durante el reparto.
    """
    tiendas, remaining = _priorizar_y_fase1_2a(cajas_disponibles, tiendas_df)
    if len(tiendas) == 0:
        return tiendas, remaining, []
    return _fase2b_individual(item_code, tiendas, remaining)


def run_distribution(df_merged):
    """Ejecuta la distribución para todos los ítems del DataFrame unificado.

    Fase 1/2a se calcula por ítem (ya usa métricas de grupo espejo
    precalculadas). Fase 2b se ejecuta por ítem individual para grupos de
    tamaño 1, o de forma conjunta (`_fase2b_grupal`) para grupos con más de
    un ítem con stock CEDI disponible.

    Retorna (df_output, alertas):
      - df_output: filas con cajas_asignadas > 0
      - alertas: lista de dicts {tipo, item, mensaje}
    """
    results = []
    alertas = []

    if df_merged is None or len(df_merged) == 0:
        return pd.DataFrame(), [{
            'tipo': 'ERROR', 'item': '-',
            'mensaje': 'No hay combinaciones tienda-ítem elegibles tras el cruce. '
                       'Revisar archivos de entrada.'
        }]

    # Pasada 1: prioridad + Fase 1 + Fase 2a, por ítem.
    estado = {}
    grupo_de_item = {}
    for item_code, group in df_merged.groupby('item_code'):
        cajas_disponibles = int(group['cajas_disponibles_cedi'].iloc[0])
        if cajas_disponibles <= 0:
            continue  # skip silencioso: ítem sin stock en cajas

        tiendas, remaining = _priorizar_y_fase1_2a(cajas_disponibles, group)
        estado[item_code] = {'tiendas': tiendas, 'remaining': remaining}
        grupo_de_item[item_code] = group['grupo_id'].iloc[0]

    # Pasada 2: Fase 2b — individual para grupos de tamaño 1, conjunta para
    # grupos con más de un ítem (productos espejo).
    items_por_grupo = {}
    for item_code, grupo_id in grupo_de_item.items():
        items_por_grupo.setdefault(grupo_id, []).append(item_code)

    for grupo_id, item_codes in items_por_grupo.items():
        if len(item_codes) == 1:
            item_code = item_codes[0]
            est = estado[item_code]
            if len(est['tiendas']) == 0:
                continue  # remaining queda igual; se reporta como alerta abajo
            tiendas_result, remaining, alertas_item = _fase2b_individual(
                item_code, est['tiendas'], est['remaining']
            )
            estado[item_code] = {'tiendas': tiendas_result, 'remaining': remaining}
            alertas.extend(alertas_item)
        else:
            estado_grupo = {c: estado[c] for c in item_codes}
            resultado_grupo, alertas_por_item = _fase2b_grupal(estado_grupo)
            for c in item_codes:
                estado[c] = resultado_grupo[c]
                alertas.extend(alertas_por_item[c])

    for item_code, est in estado.items():
        remaining = est['remaining']
        if remaining > 0:
            alertas.append({
                'tipo': 'CRÍTICO',
                'item': item_code,
                'mensaje': f'Quedaron {remaining} caja(s) del ítem {item_code} sin distribuir — '
                           f'revisar portafolio/tiendas elegibles',
                'cajas_sin_distribuir': int(remaining),
            })

        tiendas_result = est['tiendas']
        tiendas_result = tiendas_result[tiendas_result['cajas_asignadas'] > 0]
        if len(tiendas_result) > 0:
            results.append(tiendas_result)

    df_output = pd.concat(results, ignore_index=True) if results else pd.DataFrame()
    return df_output, alertas

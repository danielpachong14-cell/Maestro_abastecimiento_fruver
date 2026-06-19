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
"""

import math

import pandas as pd

TARGET_DAYS = 3.0           # días objetivo de inventario proyectado por tienda
MIN_STOCK_AGOTADO = 0.3     # por debajo de este STOCK (unidades) = AGOTADA
MIN_STOCK_SAFETY = 0.6      # por debajo de este STOCK (unidades) = STOCK SEGURIDAD
MIN_CAJAS_INICIAL = 3       # cap de cajas por tienda en Fase 1 (por ronda)
MAX_CAJAS_POR_ITEM = 3      # tope duro: máximo que una tienda recibe por ítem (todas las fases)
TOPE_EXCEDENTE = 6       # máximo días que puede acumular una tienda del excedente
UMBRAL_CONCENTRACION_SIN_CONSUMO = 5  # cajas: si Paso B concentraría >= esto en 1 tienda
                                       # por falta de consumo en el resto, reparto equitativo


def get_priority(row):
    """Clasifica la urgencia de una tienda-ítem.

    Los dos primeros niveles se basan en stock absoluto (inventario_efectivo en
    unidades de distribución). El tercer nivel, en días proyectados.
    """
    inv = row.inventario_efectivo
    if inv < MIN_STOCK_AGOTADO:
        return 3  # AGOTADA — stock prácticamente en 0
    if inv < MIN_STOCK_SAFETY:
        return 2  # STOCK DE SEGURIDAD
    if row.dias_proyectados < TARGET_DAYS:
        return 1  # NECESITA REPOSICIÓN
    return 0      # CUBIERTA (solo recibe si sobra)


def _calc_deseadas(row, cap):
    """Cajas que la tienda querría recibir, limitadas por el cap del pase.

    consumo_diario e inventario_efectivo están en cajas (Unidades de Distribución),
    igual que cajas_disponibles_cedi, así que no se necesita conversión.
    """
    if row.consumo_diario <= 0:
        # Sin consumo: no se puede proyectar demanda; mínimo según prioridad.
        return 1 if row.priority >= 2 else 0
    # Cajas necesarias para alcanzar el objetivo de días, descontando lo ya asegurado.
    cajas_needed = math.ceil(max(0.0, TARGET_DAYS * row.consumo_diario - row.inventario_efectivo))
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


def distribuir_item(item_code, cajas_disponibles, tiendas_df):
    """Distribuye `cajas_disponibles` de un ítem entre sus tiendas elegibles.

    Retorna (tiendas_df_con_asignacion, remaining, alertas_item). remaining debe
    ser 0; si es > 0 significa que no hubo tiendas suficientes para absorber el
    inventario. alertas_item es una lista (posiblemente vacía) de dicts
    {tipo, item, mensaje, ...} con advertencias detectadas durante el reparto.
    """
    tiendas = tiendas_df.copy()
    tiendas['priority'] = tiendas.apply(get_priority, axis=1)
    tiendas['cajas_asignadas'] = 0
    tiendas['cajas_fase1'] = 0
    tiendas['cajas_fase2a'] = 0
    tiendas['cajas_fase2b'] = 0
    remaining = int(cajas_disponibles)
    alertas_item = []

    if len(tiendas) == 0:
        return tiendas, remaining, alertas_item

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
            TARGET_DAYS * tiendas.loc[con_consumo, 'consumo_diario']
            - tiendas.loc[con_consumo, 'inventario_efectivo']
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
    if remaining > 0:
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


def run_distribution(df_merged):
    """Ejecuta la distribución para todos los ítems del DataFrame unificado.

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

    for item_code, group in df_merged.groupby('item_code'):
        cajas_disponibles = int(group['cajas_disponibles_cedi'].iloc[0])
        if cajas_disponibles <= 0:
            continue  # skip silencioso: ítem sin stock en cajas

        tiendas_result, remaining, alertas_item = distribuir_item(item_code, cajas_disponibles, group)
        alertas.extend(alertas_item)

        if remaining > 0:
            alertas.append({
                'tipo': 'CRÍTICO',
                'item': item_code,
                'mensaje': f'Quedaron {remaining} caja(s) del ítem {item_code} sin distribuir — '
                           f'revisar portafolio/tiendas elegibles',
                'cajas_sin_distribuir': int(remaining),
            })

        tiendas_result = tiendas_result[tiendas_result['cajas_asignadas'] > 0]
        if len(tiendas_result) > 0:
            results.append(tiendas_result)

    df_output = pd.concat(results, ignore_index=True) if results else pd.DataFrame()
    return df_output, alertas

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

  FASE 2b — sobrante equitativo hasta el tope:
    El sobrante se da por rondas a las tiendas con MENOS cajas totales
    recibidas (consumo > 0, bajo TOPE_EXCEDENTE días). Esto evita que
    las tiendas de mayor consumo acumulen el excedente desproporcionalmente.
    Tiendas sin consumo solo reciben en Fase 1 si están AGOTADAS o en SAFETY.
    Ninguna tienda supera MAX_CAJAS_POR_ITEM cajas por ítem en el total de fases
    (Paso B, fallback de cero residuo, queda sin este tope).
"""

import math

import pandas as pd

TARGET_DAYS = 3.0           # días objetivo de inventario proyectado por tienda
MIN_STOCK_AGOTADO = 0.4     # por debajo de este STOCK (unidades) = AGOTADA
MIN_STOCK_SAFETY = 0.7      # por debajo de este STOCK (unidades) = STOCK SEGURIDAD
MIN_CAJAS_INICIAL = 2       # cap de cajas por tienda en Fase 1 (por ronda)
MAX_CAJAS_POR_ITEM = 3      # tope duro: máximo que una tienda recibe por ítem (todas las fases)
TOPE_EXCEDENTE = 5        # máximo días que puede acumular una tienda del excedente


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


def distribuir_item(item_code, cajas_disponibles, tiendas_df):
    """Distribuye `cajas_disponibles` de un ítem entre sus tiendas elegibles.

    Retorna (tiendas_df_con_asignacion, remaining). remaining debe ser 0; si es
    > 0 significa que no hubo tiendas suficientes para absorber el inventario.
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
            TARGET_DAYS * tiendas.loc[con_consumo, 'consumo_diario']
            - tiendas.loc[con_consumo, 'inventario_efectivo']
            - tiendas.loc[con_consumo, 'cajas_asignadas']
        ).clip(lower=0)

        # Tope por ítem: la tienda solo puede recibir hasta lo que le falta para MAX_CAJAS_POR_ITEM.
        room = (MAX_CAJAS_POR_ITEM - tiendas['cajas_asignadas']).clip(lower=0)
        needs = needs.clip(upper=room)

        total_need = needs.sum()

        if total_need > 0:
            scale = min(1.0, remaining / total_need)
            scaled = needs * scale

            floor_s = scaled.apply(math.floor).astype(int)
            tiendas['cajas_asignadas'] += floor_s
            tiendas['cajas_fase2a'] += floor_s
            remaining -= int(floor_s.sum())

            # Corrección Hamilton: el residuo va a las tiendas con mayor fracción pendiente.
            if remaining > 0:
                fracs = (scaled - floor_s).sort_values(ascending=False)
                for idx in fracs.index:
                    if remaining <= 0:
                        break
                    tiendas.at[idx, 'cajas_asignadas'] += 1
                    tiendas.at[idx, 'cajas_fase2a'] += 1
                    remaining -= 1

    # FASE 2b — excedente: dar 1 caja por ronda a la tienda con menor días
    # proyectados (solo consumo > 0, respetando tope de TOPE_EXCEDENTE días).
    # Así la tienda con menos inventario relativo recibe antes que la que ya
    # tiene mucho acumulado, aunque esta última venda más.
    if remaining > 0:
        con_consumo_mask = tiendas['consumo_diario'] > 0

        if con_consumo_mask.any():
            # Paso A: round-robin por rondas entre elegibles bajo el tope,
            # ordenadas por menos cajas_asignadas (anti-concentración directa).
            prev = remaining + 1
            while remaining > 0 and remaining < prev:
                prev = remaining
                stock_act = tiendas['inventario_efectivo'] + tiendas['cajas_asignadas']
                dias_act = pd.Series(float('inf'), index=tiendas.index)
                dias_act[con_consumo_mask] = (
                    stock_act[con_consumo_mask]
                    / tiendas.loc[con_consumo_mask, 'consumo_diario']
                )
                elegibles_mask = (
                    con_consumo_mask
                    & (dias_act < TOPE_EXCEDENTE)
                    & (tiendas['cajas_asignadas'] < MAX_CAJAS_POR_ITEM)
                )
                if not elegibles_mask.any():
                    break
                elegibles = (tiendas[elegibles_mask]
                             .sort_values('cajas_asignadas', ascending=True)
                             .index.tolist())
                for idx in elegibles:
                    if remaining <= 0:
                        break
                    tiendas.at[idx, 'cajas_asignadas'] += 1
                    tiendas.at[idx, 'cajas_fase2b'] += 1
                    remaining -= 1

            # Paso B: si todas llegaron al tope, round-robin entre consumo > 0
            # (garantía de 0 residuo sin involucrar tiendas sin consumo).
            if remaining > 0:
                order = (tiendas[con_consumo_mask]
                         .sort_values('consumo_diario', ascending=False)
                         .index.tolist())
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

    return tiendas, remaining


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

        tiendas_result, remaining = distribuir_item(item_code, cajas_disponibles, group)

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

"""Motor de distribución: reparte el 100 % de las cajas del CEDI entre las tiendas elegibles de cada
ítem en 5 fases (completar TARGET_DAYS por venta, garantizar agotado/seguridad, renovación por vida
útil, excedente, cero residuo).
Grupos espejo: la necesidad y el excedente se calculan por grupo. Detalle en CLAUDE.md.
"""

import math

import numpy as np
import pandas as pd

# =============================================================================
# >>> PARÁMETROS EDITABLES (cambia solo el número; todo el código y los tests
# >>> leen estas constantes, no hay valores duplicados en otro lado)
# =============================================================================
TOPE_EXCEDENTE = 8          # <<< TOPE DE DÍAS: máximo de días de inventario que una tienda puede
                            #     acumular al recibir el SOBRANTE del CEDI (Fases 4/5). Ninguna tienda
                            #     recibe una caja que la deje por encima de este valor.
TARGET_DAYS = 5.0           # días objetivo de inventario proyectado por tienda (Fase 1)
MAX_CAJAS_POR_ITEM = 3      # tope duro: máximo que una tienda recibe por ítem (todas las fases)
MIN_CAJAS_INICIAL = 2       # máximo de cajas por tienda en Fase 1 (completar TARGET_DAYS, por rondas)
MIN_STOCK_AGOTADO = 0.3     # por debajo de este STOCK (cajas) = AGOTADA
MIN_STOCK_SAFETY = 0.7      # por debajo de este STOCK (cajas) = STOCK SEGURIDAD
# =============================================================================

CAJAS_RENOVACION = 1     # Fase 3: cajas que se envían a una tienda con producto vencido por vida útil
UMBRAL_VENTA_RENOVACION = 0.0  # Fase 3: venta esperada mínima (cajas) para renovar; 0 = siempre se renueva
                               # (decisión del negocio, nov-2026). Subirlo (p. ej. 0.7) evita cajas que no se venderían.
UMBRAL_CONCENTRACION_SIN_CONSUMO = 5  # cajas: si Fase 5 concentraría >= esto en 1 tienda
                                       # por falta de consumo en el resto, reparto equitativo
_EPS_DIAS_ACTUALES = 1e-3  # reemplaza días_actuales==0 al ponderar: 0 días es la tienda MÁS urgente
                            # y no debe quedar con peso cero ni dividir por 0


def _fmt_num(valor):
    """Formatea para alertas: sin decimales si es entero exacto (6), con dos si no (5.07)."""
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    return str(int(f)) if f == int(f) else f'{f:.2f}'


def _bloqueadas(tiendas_df):
    """Máscara de filas excluidas del reparto normal (`excluida_del_reparto`, ver preprocessor).

    Dos causas con la misma consecuencia:
      - Tope MAX: existencias + tránsito ya alcanzan lo que la tienda puede tener del ítem.
      - Sin datos en Celes: llegaría con consumo=0/inventario=0 y se clasificaría
        AGOTADA solo porque falta el dato; decisión de negocio: no se le envía.

    No participan en Fases 1-4 ni en el nivel 1 de Fase 5; solo el nivel 2 de
    Fase 5 (último recurso de cero residuo) puede asignarles cajas. Si falta la
    columna unificada cae a `bloqueado_por_max`, y a "ninguna" si faltan ambas.
    """
    for col in ('excluida_del_reparto', 'bloqueado_por_max'):
        if col in tiendas_df.columns:
            return tiendas_df[col].fillna(False).astype(bool)
    return pd.Series(False, index=tiendas_df.index)


def _alertas_forzadas(item_code, tiendas, forzadas):
    """Alertas de las cajas que el nivel 2 de Fase 5 asignó a tiendas excluidas
    (MAX o sin datos en Celes) por no haber otro destino (cero residuo manda).

    `forzadas` es {índice de fila: cajas}. Si la fila tiene ambas causas se
    reporta la falta de datos (la más grave). Retorna una alerta por tienda.
    """
    alertas = []
    tiene_max = 'max_existencias' in tiendas.columns
    tiene_exist = 'inventario_tienda' in tiendas.columns
    tiene_transito = 'inventario_transito' in tiendas.columns
    tiene_sin_match = 'sin_match_celes' in tiendas.columns
    for idx, n_cajas in forzadas.items():
        max_ex = tiendas.at[idx, 'max_existencias'] if tiene_max else 0
        exist = tiendas.at[idx, 'inventario_tienda'] if tiene_exist else 0
        transito = tiendas.at[idx, 'inventario_transito'] if tiene_transito else 0
        store_code = tiendas.at[idx, 'store_code']
        sin_match = bool(tiendas.at[idx, 'sin_match_celes']) if tiene_sin_match else False

        if sin_match:
            causa = 'SIN DATOS CELES'
            detalle = 'pese a no tener datos en Celes'
        else:
            causa = 'MAX'
            detalle = (
                f'pese a superar su MAX ({_fmt_num(max_ex)}) con '
                f'{_fmt_num(exist)} en existencias + {_fmt_num(transito)} en tránsito'
            )

        alertas.append({
            'tipo': 'ADVERTENCIA',
            'item': item_code,
            'mensaje': (
                f'Ítem {item_code}: la tienda {store_code} recibió '
                f'{n_cajas} caja(s) {detalle} — no había ningún otro destino '
                'posible para el sobrante (cero residuo).'
            ),
            'cajas_forzadas': int(n_cajas),
            'causa_forzado': causa,
            'store_code': str(store_code),
            'max_existencias': float(max_ex),
            'existencias': float(exist),
            'transito': float(transito),
        })
    return alertas


def get_priority(row):
    """Urgencia de una tienda-ítem: 3 AGOTADA, 2 SAFETY, 1 REPOSICIÓN, 0 CUBIERTA.

    Usa las métricas a nivel de GRUPO espejo (para un ítem sin espejo son
    iguales a las individuales). Versión por fila de `_compute_priority`; deben
    dar el mismo resultado.
    """
    inv = row.inventario_efectivo_grupo
    if inv < MIN_STOCK_AGOTADO:
        return 3  # AGOTADA — stock prácticamente en 0
    if inv < MIN_STOCK_SAFETY:
        return 2  # STOCK DE SEGURIDAD
    if row.dias_proyectados_grupo < TARGET_DAYS:
        return 1  # NECESITA REPOSICIÓN
    return 0      # CUBIERTA (solo recibe si sobra)


def _compute_priority(tiendas_df):
    """Versión vectorizada de `get_priority` para un DataFrame completo."""
    inv = tiendas_df['inventario_efectivo_grupo']
    dias = tiendas_df['dias_proyectados_grupo']
    return pd.Series(
        np.select(
            [inv < MIN_STOCK_AGOTADO, inv < MIN_STOCK_SAFETY, dias < TARGET_DAYS],
            [3, 2, 1],
            default=0,
        ),
        index=tiendas_df.index,
    )


def _apportion_grupo_targets(tiendas_df):
    """Cajas necesarias en Fase 1 para que el GRUPO espejo de cada
    (store_code, grupo_id) alcance TARGET_DAYS, repartidas entre sus ítems con
    piso + Hamilton (`_reparto_proporcional`).

    El `ceil` se aplica UNA vez por grupo, no por ítem: con 3 ítems al 33% y
    necesidad 1.0, redondear cada uno pediría 3 cajas en vez de 1. Para un ítem
    sin espejo equivale a `ceil(necesidad_grupo)`.

    Retorna una Series de enteros alineada a `tiendas_df`.
    """
    necesidad_grupo = (
        TARGET_DAYS * tiendas_df['consumo_diario_grupo'] - tiendas_df['inventario_efectivo_grupo']
    ).clip(lower=0)

    resultado = pd.Series(0, index=tiendas_df.index, dtype=int)
    for _, idx in tiendas_df.groupby(['store_code', 'grupo_id']).groups.items():
        total = math.ceil(necesidad_grupo.loc[idx].iloc[0])
        if total <= 0:
            continue
        pesos = tiendas_df.loc[idx, 'item_share_consumo']
        room = pd.Series(total, index=idx)
        resultado.loc[idx] = _reparto_proporcional(pesos, total, room)
    return resultado


def _calc_deseadas(row, cap):
    """Fase 1: cajas que la tienda querría recibir para llegar a TARGET_DAYS,
    limitadas por el cap de la ronda. Sin piso por prioridad: garantizar la caja
    de la Agotada/Seguridad es tarea de la Fase 2.

    Lee `cajas_target_grupo` (ver `_apportion_grupo_targets`); si la fila no la
    trae (llamadas aisladas en tests) la recalcula, con el mismo resultado para
    un ítem sin espejo. Consumo e inventario ya están en cajas.
    """
    if row.consumo_diario <= 0:
        return 0  # sin consumo no se proyecta demanda (la Fase 2 cubre si está agotada)
    cajas_target = getattr(row, 'cajas_target_grupo', None)
    if cajas_target is None:
        necesidad_grupo = max(0.0, TARGET_DAYS * row.consumo_diario_grupo - row.inventario_efectivo_grupo)
        cajas_target = math.ceil(necesidad_grupo * row.item_share_consumo)
    return max(0, min(int(cajas_target), cap))


def _reparto_proporcional(pesos, total, room):
    """Reparte hasta `total` cajas proporcional a `pesos`, sin exceder `room`
    por tienda. Piso + corrección Hamilton: la suma coincide con `total` (o con
    `room.sum()` si es menor); el residuo va a quienes tienen mayor fracción
    pendiente y aún tienen espacio.

    Con pesos proporcionales a `consumo_diario`, el incremento de días
    proyectados es igual para todas: reparte días, no solo cajas. Los pesos
    negativos se descartan.

    Retorna una Series de enteros con el mismo índice que `pesos`.
    """
    pesos = pesos.clip(lower=0)
    asignado = pd.Series(0, index=pesos.index, dtype=int)
    total_pesos = float(pesos.sum())
    total = int(total)
    if total_pesos <= 0 or total <= 0:
        return asignado

    objetivo = (pesos / total_pesos * total).clip(lower=0, upper=room)
    piso = objetivo.apply(math.floor).astype(int)
    asignado += piso
    sobra = total - int(piso.sum())

    if sobra > 0:
        espacio = room - piso
        fracs = (objetivo - piso).where(espacio > 0, -1.0).sort_values(ascending=False, kind='stable')
        for idx in fracs.index:
            if sobra <= 0 or fracs[idx] < 0:
                break
            asignado[idx] += 1
            sobra -= 1

    return asignado


def _priorizar_y_fases_1_2(cajas_disponibles, tiendas_df):
    """Calcula prioridad y ejecuta Fase 1 + Fase 2 para un ítem.

    Si falta `cajas_target_grupo` la calcula sobre las filas recibidas (válido
    para un ítem aislado); `run_distribution` la precalcula sobre todo
    `df_merged` para coordinar ítems espejo. Retorna (tiendas, remaining); no
    genera alertas.
    """
    tiendas = tiendas_df.copy()
    tiendas['priority'] = _compute_priority(tiendas)
    tiendas['cajas_asignadas'] = 0
    tiendas['cajas_fase1'] = 0
    tiendas['cajas_fase2'] = 0
    tiendas['cajas_fase3'] = 0
    tiendas['cajas_fase4'] = 0
    tiendas['cajas_fase5'] = 0
    remaining = int(cajas_disponibles)

    if len(tiendas) == 0:
        return tiendas, remaining

    if 'cajas_target_grupo' not in tiendas.columns:
        tiendas['cajas_target_grupo'] = _apportion_grupo_targets(tiendas)

    # Orden: mayor consumo primero (sin mirar el nivel de prioridad); store_code
    # desempata de forma determinista.
    tiendas = tiendas.sort_values(
        ['consumo_diario', 'store_code'], ascending=[False, True]
    ).reset_index(drop=True)

    # Las excluidas (ver `_bloqueadas`) no reciben en Fases 1-3 ni en el nivel 1
    # de Fase 5, pero se conservan en el DataFrame para el nivel 2 y el concat final.
    bloqueadas = _bloqueadas(tiendas)

    # FASE 1 — completar TARGET_DAYS, por venta: rondas con cap creciente
    # (1 → MIN_CAJAS_INICIAL). En cada ronda, de mayor a menor consumo, la tienda
    # recibe el delta entre deseadas(cap) y lo ya asignado: todas las que lo
    # necesitan reciben su 1.ª caja antes de que alguna reciba la 2.ª.
    for cap in range(1, MIN_CAJAS_INICIAL + 1):
        if remaining <= 0:
            break
        for idx, row in tiendas.iterrows():
            if remaining <= 0:
                break
            if bloqueadas.at[idx]:
                continue
            deseadas = _calc_deseadas(row, cap)
            adicional = max(0, deseadas - int(tiendas.at[idx, 'cajas_asignadas']))
            asignar = min(adicional, remaining)
            tiendas.at[idx, 'cajas_asignadas'] += asignar
            tiendas.at[idx, 'cajas_fase1'] += asignar
            remaining -= asignar

    # FASE 2 — garantizar el agotado: con lo que sobre, 1 caja a cada tienda que
    # salió de la Fase 1 sin nada. Primero TODAS las AGOTADAS (prioridad 3) y,
    # si aún sobra, las de STOCK DE SEGURIDAD (prioridad 2); mayor consumo primero
    # (el DataFrame ya viene en ese orden).
    for nivel in (3, 2):
        if remaining <= 0:
            break
        candidatas = tiendas.index[
            (tiendas['priority'] == nivel) & (tiendas['cajas_asignadas'] == 0) & ~bloqueadas
        ]
        for idx in candidatas:
            if remaining <= 0:
                break
            tiendas.at[idx, 'cajas_asignadas'] += 1
            tiendas.at[idx, 'cajas_fase2'] += 1
            remaining -= 1

    return tiendas, remaining


_COLS_RENOVACION = ('vida_util_portafolio', 'dias_desde_ingreso',
                    'venta_esperada_vida_util', 'grupo_con_ingreso_fresco')


def _fase3_renovacion(estado, item_codes):
    """FASE 3 — renovación por vida útil de UN grupo espejo (o un ítem solo).

    `estado` es {item_code: {'tiendas': df, 'remaining': int}} tras Fase 1/2;
    `item_codes` son los ítems del grupo con stock en CEDI. Muta `estado` y
    retorna la lista de alertas.

    Una tienda×GRUPO es candidata si algún SKU tiene ingreso más viejo que su
    vida útil con inventario > 0 y NINGÚN SKU tiene tránsito, cajas de Fase 1/2
    ni ingreso fresco. La caja va al SKU de mayor `item_share_consumo` con stock
    y no excluido. Si el stock no alcanza se prioriza por venta esperada, luego
    días de vencimiento y `store_code`, y se alertan las que quedaron sin caja.
    Sin las columnas de renovación la fase no hace nada.
    """
    alertas = []
    tablas = {
        c: estado[c]['tiendas'].set_index('store_code', drop=False)
        for c in item_codes
        if len(estado[c]['tiendas']) > 0
        and all(col in estado[c]['tiendas'].columns for col in _COLS_RENOVACION)
    }
    if not tablas:
        return alertas
    remaining = {c: int(estado[c]['remaining']) for c in tablas}

    # Se evalúan aunque no quede stock, para alertarlas como "sin caja" en vez de omitirlas.
    candidatas = []
    for store in sorted(set().union(*(t.index for t in tablas.values()))):
        filas = [(c, tablas[c].loc[store]) for c in tablas if store in tablas[c].index]
        if any(bool(r['grupo_con_ingreso_fresco']) for _, r in filas):
            continue
        if sum(float(r['inventario_transito']) for _, r in filas) > 0:
            continue
        if sum(int(r['cajas_asignadas']) for _, r in filas) > 0:
            continue
        viejas = [
            r for _, r in filas
            if r['vida_util_portafolio'] > 0
            and r['dias_desde_ingreso'] > r['vida_util_portafolio']
            and r['inventario_tienda'] > 0
        ]
        if not viejas:
            continue
        venta = max(float(r['venta_esperada_vida_util']) for r in viejas)
        if venta < UMBRAL_VENTA_RENOVACION:
            continue
        exceso = max(float(r['dias_desde_ingreso'] - r['vida_util_portafolio']) for r in viejas)
        candidatas.append((store, venta, exceso))

    candidatas.sort(key=lambda x: (-x[1], -x[2], str(x[0])))
    sin_stock = []
    for store, _, _ in candidatas:
        receptores = [
            c for c in tablas
            if store in tablas[c].index
            and remaining[c] > 0
            and not bool(_bloqueadas(tablas[c]).at[store])
        ]
        if not receptores:
            sin_stock.append(str(store))
            continue
        receptor = min(receptores, key=lambda c: (-float(tablas[c].at[store, 'item_share_consumo']), str(c)))
        n = min(CAJAS_RENOVACION, remaining[receptor], MAX_CAJAS_POR_ITEM)
        tablas[receptor].at[store, 'cajas_asignadas'] += n
        tablas[receptor].at[store, 'cajas_fase3'] += n
        remaining[receptor] -= n

    for c, t in tablas.items():
        estado[c] = {'tiendas': t.reset_index(drop=True), 'remaining': remaining[c]}

    if sin_stock:
        alertas.append({
            'tipo': 'ADVERTENCIA',
            'item': sorted(tablas)[0],
            'mensaje': (
                f'Ítem {sorted(tablas)[0]}: {len(sin_stock)} tienda(s) con producto vencido por '
                'vida útil quedaron sin caja de renovación por falta de stock en CEDI '
                f'(primeras: {", ".join(sin_stock[:10])}{"…" if len(sin_stock) > 10 else ""}). '
                'Se priorizó por mayor venta esperada; lista completa en la hoja '
                '«Novedades» (situación «Renovación vida útil», motivo «Sin stock en CEDI»).'
            ),
            'tiendas_sin_renovar': sin_stock,
        })
    return alertas


def _fase4_individual(tiendas, remaining):
    """Reparte el excedente VOLUNTARIO de UN ítem (sin ver sus hermanos espejo).
    Se usa para ítems sin espejo y como paso previo a Fase 5.

    NUNCA deja a una tienda sobre TOPE_EXCEDENTE, por lo que puede terminar con
    `remaining > 0` (lo recoge Fase 5). Retorna (tiendas, remaining); no genera
    alertas.
    """
    tiendas = tiendas.copy()

    if len(tiendas) == 0 or remaining <= 0:
        return tiendas, remaining

    # FASE 4 — cada ronda reparte proporcional a `consumo_diario / días_actuales`
    # entre las elegibles (consumo > 0, bajo TOPE_EXCEDENTE tras recibir, bajo
    # MAX_CAJAS_POR_ITEM). Repartir cajas iguales da días muy desiguales; con
    # este peso Δdías_i = k / días_i, así las más atrasadas cierran la brecha.
    # Ver `_EPS_DIAS_ACTUALES` para las tiendas con 0 días. Las excluidas
    # (MAX / sin datos en Celes) no participan.
    con_consumo_mask = (tiendas['consumo_diario'] > 0) & ~_bloqueadas(tiendas)

    if con_consumo_mask.any():
        # Por rondas: cada una puede sacar tiendas de la elegibilidad, así que se
        # recalculan pesos y espacio.
        prev = remaining + 1
        while remaining > 0 and remaining < prev:
            prev = remaining
            stock_act = tiendas['inventario_efectivo'] + tiendas['cajas_asignadas']
            dias_act = pd.Series(float('inf'), index=tiendas.index)
            dias_act[con_consumo_mask] = (
                stock_act[con_consumo_mask]
                / tiendas.loc[con_consumo_mask, 'consumo_diario']
            )
            # Elegible si, tras una caja más, sigue bajo el tope (medir el estado
            # ANTES de asignar permitía sobrepasarlo).
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
            dias_act_ponderable = dias_act.mask(dias_act <= 0, _EPS_DIAS_ACTUALES)
            pesos = (tiendas['consumo_diario'] / dias_act_ponderable).where(elegibles_mask, 0.0)
            # Máximo 1 caja por tienda por ronda: re-chequea el tope tras cada caja
            # y reparte primero entre más tiendas.
            room = ((MAX_CAJAS_POR_ITEM - tiendas['cajas_asignadas'])
                    .clip(lower=0, upper=1)
                    .where(elegibles_mask, 0))
            asignado = _reparto_proporcional(pesos, remaining, room)
            tiendas['cajas_asignadas'] += asignado
            tiendas['cajas_fase4'] += asignado
            remaining -= int(asignado.sum())

    return tiendas, remaining


def _fase5_individual(item_code, tiendas, remaining):
    """FASE 5 — red de seguridad de cero residuo: reparte lo que Fase 4 no pudo
    colocar bajo TOPE_EXCEDENTE. Es la ÚNICA función que puede dejar a una
    tienda sobre ese tope (cero residuo tiene prioridad).

    Las excluidas del reparto normal (MAX o sin datos en Celes, ver
    `_bloqueadas`) quedan fuera del nivel 1; solo el nivel 2 (sin otro destino)
    puede darles cajas, al final del orden y con una alerta por tienda
    (`_alertas_forzadas`).

    Nivel 1, dos pasadas: (1) una caja a cada tienda SIN caja de renovación de
    Fase 3, por menor días_actuales; (2) si aún sobra, todas por mayor consumo,
    1 caja por vuelta respetando MAX_CAJAS_POR_ITEM.

    Retorna (tiendas, remaining, alertas_item); remaining debe terminar en 0.
    """
    tiendas = tiendas.copy()
    alertas_item = []

    if len(tiendas) == 0 or remaining <= 0:
        return tiendas, remaining, alertas_item

    # Las excluidas solo vuelven a considerarse en el nivel 2 (último recurso).
    bloqueadas = _bloqueadas(tiendas)
    con_consumo_mask = (tiendas['consumo_diario'] > 0) & ~bloqueadas

    if con_consumo_mask.any():
        # Por menor días proyectados, en dos niveles (ver docstring).
        stock_act = tiendas['inventario_efectivo'] + tiendas['cajas_asignadas']
        dias_act = (stock_act[con_consumo_mask]
                    / tiendas.loc[con_consumo_mask, 'consumo_diario'])
        # Orden puro por días (menor primero): lo usa el aviso del reparto equitativo.
        order = dias_act.sort_values(ascending=True, kind='stable').index.tolist()

        # Nivel 1 en dos pasadas: (1) tiendas SIN caja de renovación, por menor
        # días_actuales; (2) si aún sobra, todas por mayor consumo (desempate por
        # menor días), 1 caja por vuelta. Sin `cajas_fase3` nadie es «renovada».
        if 'cajas_fase3' in tiendas.columns:
            ya_renovada = (tiendas.loc[con_consumo_mask, 'cajas_fase3'] > 0)
        else:
            ya_renovada = pd.Series(False, index=dias_act.index)
        order_p1 = [i for i in order if not ya_renovada.at[i]]
        pasada2_df = pd.DataFrame({'consumo': tiendas.loc[con_consumo_mask, 'consumo_diario'],
                                   'dias': dias_act})
        order_p2 = pasada2_df.sort_values(['consumo', 'dias'], ascending=[False, True],
                                          kind='stable').index.tolist()

        n_con_consumo = int(con_consumo_mask.sum())
        n_sin_consumo = len(tiendas) - n_con_consumo - int(bloqueadas.sum())
        max_concentracion = math.ceil(remaining / n_con_consumo)

        if max_concentracion >= UMBRAL_CONCENTRACION_SIN_CONSUMO and n_sin_consumo > 0:
            # El reparto normal concentraría demasiadas cajas en pocas tiendas porque
            # el resto no tiene datos de venta: se reparte parejo entre todo el
            # portafolio activo del ítem.
            tienda_concentracion = tiendas.at[order[0], 'store_code']
            remaining_original = remaining
            # Cubre el portafolio activo salvo excluidas; si todas lo están se usa
            # igual (cero residuo manda) y el nivel 2 emite la alerta.
            candidatas_eq = tiendas.index[~bloqueadas]
            if len(candidatas_eq) == 0:
                candidatas_eq = tiendas.index
            while remaining > 0:
                # Primero entre quienes no llegan a MAX_CAJAS_POR_ITEM; solo si todas
                # están en el máximo se ignora (cero residuo).
                pool = [i for i in candidatas_eq
                        if tiendas.at[i, 'cajas_asignadas'] < MAX_CAJAS_POR_ITEM]
                if not pool:
                    pool = list(candidatas_eq)
                idx_min = tiendas.loc[pool, 'cajas_asignadas'].idxmin()
                tiendas.at[idx_min, 'cajas_asignadas'] += 1
                tiendas.at[idx_min, 'cajas_fase5'] += 1
                remaining -= 1
            alertas_item.append({
                'tipo': 'ADVERTENCIA',
                'item': item_code,
                'mensaje': (
                    f"Ítem {item_code}: solo {n_con_consumo} de {len(tiendas)} "
                    f"tienda(s) del portafolio tienen consumo registrado "
                    f"({n_sin_consumo} sin datos de venta) — el sobrante se iba a "
                    f"concentrar en {tienda_concentracion} (+{max_concentracion} "
                    f"cajas). Se aplicó reparto equitativo entre las {len(candidatas_eq)} "
                    f"tiendas elegibles del portafolio activo (hasta "
                    f"{MAX_CAJAS_POR_ITEM} cajas por tienda mientras fue posible). Revisar si faltan datos de "
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
            # Nivel 1: round-robin por menor días, saltando tiendas en MAX_CAJAS_POR_ITEM.
            # Pasada 1: una caja a cada tienda sin renovación, por menor días.
            for idx in order_p1:
                if remaining <= 0:
                    break
                if tiendas.at[idx, 'cajas_asignadas'] < MAX_CAJAS_POR_ITEM:
                    tiendas.at[idx, 'cajas_asignadas'] += 1
                    tiendas.at[idx, 'cajas_fase5'] += 1
                    remaining -= 1

            # Pasada 2: si aún sobra, todas las tiendas por mayor consumo.
            con_espacio = [idx for idx in order_p2 if tiendas.at[idx, 'cajas_asignadas'] < MAX_CAJAS_POR_ITEM]
            i = 0
            while remaining > 0 and con_espacio:
                idx = con_espacio[i % len(con_espacio)]
                tiendas.at[idx, 'cajas_asignadas'] += 1
                tiendas.at[idx, 'cajas_fase5'] += 1
                remaining -= 1
                if tiendas.at[idx, 'cajas_asignadas'] >= MAX_CAJAS_POR_ITEM:
                    con_espacio.pop(i % len(con_espacio))
                else:
                    i += 1

            # Nivel 2 (último recurso): todas las tiendas con consumo están en
            # MAX_CAJAS_POR_ITEM y aún sobran cajas; cero residuo prima sobre el
            # tope. Las excluidas se reincorporan SIEMPRE al final del orden y cada
            # caja así asignada se registra para la alerta.
            order_n2 = order_p2 + [idx for idx in tiendas.index if bloqueadas.at[idx]]
            forzadas = {}
            i = 0
            while remaining > 0:
                idx = order_n2[i % len(order_n2)]
                tiendas.at[idx, 'cajas_asignadas'] += 1
                tiendas.at[idx, 'cajas_fase5'] += 1
                if bloqueadas.at[idx]:
                    forzadas[idx] = forzadas.get(idx, 0) + 1
                remaining -= 1
                i += 1

            alertas_item.extend(_alertas_forzadas(item_code, tiendas, forzadas))
    else:
        # Ninguna tienda elegible con consumo (sin consumo registrado o todas
        # excluidas): round-robin prefiriendo las no excluidas; si todas lo están
        # reciben igual (cero residuo manda) y se alerta por tienda.
        indices = [idx for idx in tiendas.index if not bloqueadas.at[idx]]
        if not indices:
            indices = list(tiendas.index)
        forzadas = {}
        i = 0
        while remaining > 0 and indices:
            idx = indices[i % len(indices)]
            tiendas.at[idx, 'cajas_asignadas'] += 1
            tiendas.at[idx, 'cajas_fase5'] += 1
            if bloqueadas.at[idx]:
                forzadas[idx] = forzadas.get(idx, 0) + 1
            remaining -= 1
            i += 1
        alertas_item.extend(_alertas_forzadas(item_code, tiendas, forzadas))

    return tiendas, remaining, alertas_item


def _fase4_grupal(estado_items):
    """Reparte el sobrante de un GRUPO de ítems espejo de forma conjunta.

    `estado_items` es {item_code: {'tiendas': df, 'remaining': int}} con los
    ítems de un `grupo_id` ya resueltos en Fase 1/2 (`store_code` único por fila).

    Elegibilidad, TOPE_EXCEDENTE y peso se calculan sobre el inventario y
    consumo COMBINADO del grupo en cada tienda, con el cap de 1 caja combinada
    por tienda por ronda de `_fase4_individual`. Lo asignado a la tienda se
    separa entre sus ítems: 1 caja primero a cualquiera con 0 unidades (si el
    CEDI tiene stock), y el resto proporcional a `item_share_consumo`.
    MAX_CAJAS_POR_ITEM se respeta por ítem y cada ítem usa solo su stock CEDI.

    Retorna (resultado, alertas_por_item), ambos dicts por item_code.
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

    # FASE 4 GRUPAL — análoga a _fase4_individual, agregando por tienda los ítems del grupo.
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
            # Un ítem bloqueado por MAX no aporta espacio (el bloqueo es por
            # tienda×ÍTEM; un hermano bloqueado no frena a los demás).
            room_item = room_item.where(~_bloqueadas(t).loc[presentes], 0)
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

        dias_act_ponderable = dias_act.mask(dias_act <= 0, _EPS_DIAS_ACTUALES)
        pesos = (consumo_grupo / dias_act_ponderable).where(elegibles_mask, 0.0)
        # Cap de 1 caja combinada por tienda por ronda (misma razón que en _fase4_individual).
        room_elig = room_store.clip(upper=1).where(elegibles_mask, 0)
        total_pool = sum(remaining[c] for c in activos)
        asignado_store = _reparto_proporcional(pesos, total_pool, room_elig)

        for store in asignado_store[asignado_store > 0].index:
            n = int(asignado_store[store])

            # Un ítem bloqueado por MAX no recibe en Fase 4 (solo el nivel 2 de Fase 5).
            candidatos = [
                c for c in items_con_tiendas
                if store in tablas[c].index
                and remaining[c] > 0
                and (MAX_CAJAS_POR_ITEM - int(tablas[c].at[store, 'cajas_asignadas'])) > 0
                and not bool(_bloqueadas(tablas[c]).at[store])
            ]
            if not candidatos:
                continue

            # 1 caja a cualquier ítem del grupo con 0 unidades en la tienda, antes del reparto proporcional.
            for c in candidatos:
                if n <= 0:
                    break
                t = tablas[c]
                stock_item = t.at[store, 'inventario_efectivo'] + t.at[store, 'cajas_asignadas']
                if stock_item <= 1e-9 and not bool(t.at[store, 'garantizado']):
                    t.at[store, 'cajas_asignadas'] += 1
                    t.at[store, 'cajas_fase4'] += 1
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
                tablas[c].at[store, 'cajas_fase4'] += int(cajas)
                remaining[c] -= int(cajas)

        total_remaining = sum(remaining.values())

    # FASE 5 — el remanente por ítem se delega a la Fase 5 individual: es excedente
    # genuino de ese SKU y debe salir del CEDI sin importar el grupo.
    resultado = {}
    for code, est in estado_items.items():
        t = tablas[code]
        if len(t) > 0:
            t = t.drop(columns=['garantizado']).reset_index(drop=True)
        rem = remaining[code]
        if rem > 0:
            t, rem, alertas_item = _fase5_individual(code, t, rem)
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
    tiendas, remaining = _priorizar_y_fases_1_2(cajas_disponibles, tiendas_df)
    if len(tiendas) == 0:
        return tiendas, remaining, []
    estado = {item_code: {'tiendas': tiendas, 'remaining': remaining}}
    alertas_renov = _fase3_renovacion(estado, [item_code])
    tiendas, remaining = estado[item_code]['tiendas'], estado[item_code]['remaining']
    tiendas, remaining = _fase4_individual(tiendas, remaining)
    tiendas, remaining, alertas_item = _fase5_individual(item_code, tiendas, remaining)
    return tiendas, remaining, alertas_renov + alertas_item


def run_distribution(df_merged, alertas_extra=None):
    """Ejecuta la distribución de todos los ítems del DataFrame unificado.

    `alertas_extra`: alertas detectadas aguas arriba (preprocessor), que se
    anteponen a las generadas aquí.

    Fase 1/2 va por ítem, con la necesidad de grupo espejo precalculada sobre
    todo `df_merged`. Fase 4 (+ Fase 5 si queda remanente) va por ítem para
    grupos de tamaño 1 y conjunta (`_fase4_grupal`) para grupos con varios ítems
    con stock CEDI.

    Retorna (df_output, alertas): filas con cajas_asignadas > 0 y lista de
    dicts {tipo, item, mensaje}.
    """
    results = []
    alertas = list(alertas_extra or [])

    if df_merged is None or len(df_merged) == 0:
        alertas.append({
            'tipo': 'ERROR', 'item': '-',
            'mensaje': 'No hay combinaciones tienda-ítem elegibles tras el cruce. '
                       'Revisar archivos de entrada.'
        })
        return pd.DataFrame(), alertas

    df_merged = df_merged.copy()

    # Alerta agregada (una sola, no por ítem): las combinaciones sin match en Celes
    # quedan FUERA del reparto (ver `_bloqueadas`); sin esa exclusión recibirían
    # 1 caja como AGOTADAS solo por faltar el dato. Detalle en la hoja "Novedades".
    if 'sin_match_celes' in df_merged.columns:
        sin_match_mask = df_merged['sin_match_celes'].fillna(False)
        n_sin_match = int(sin_match_mask.sum())
        if n_sin_match > 0:
            pct = round(n_sin_match / len(df_merged) * 100, 1)
            n_items = df_merged.loc[sin_match_mask, 'item_code'].nunique()
            n_tiendas = df_merged.loc[sin_match_mask, 'store_code'].nunique()
            alertas.append({
                'tipo': 'ADVERTENCIA',
                'item': '-',
                'mensaje': (
                    f'{n_sin_match} combinación(es) tienda-ítem ({pct}% del total, '
                    f'{n_tiendas} tienda(s), {n_items} ítem(s)) no tuvieron match en '
                    'Celes y quedaron FUERA del reparto: tienen portafolio activo '
                    'pero sin datos de consumo/inventario no se puede saber si '
                    'necesitan mercancía. Detalle en la hoja "Novedades". '
                    'Revisar cobertura de Celes.'
                ),
                'combos_sin_match': n_sin_match,
                'tiendas_afectadas': int(n_tiendas),
                'items_afectados': int(n_items),
                'pct_del_total': pct,
            })

    # Necesidad de grupo espejo sobre TODO el universo, antes de trocear por ítem.
    df_merged['cajas_target_grupo'] = _apportion_grupo_targets(df_merged)

    # Pasada 1: prioridad + Fase 1 + Fase 2, por ítem.
    estado = {}
    grupo_de_item = {}
    for item_code, group in df_merged.groupby('item_code'):
        cajas_disponibles = int(group['cajas_disponibles_cedi'].iloc[0])
        if cajas_disponibles <= 0:
            continue  # skip silencioso: ítem sin stock en cajas

        tiendas, remaining = _priorizar_y_fases_1_2(cajas_disponibles, group)
        estado[item_code] = {'tiendas': tiendas, 'remaining': remaining}
        grupo_de_item[item_code] = group['grupo_id'].iloc[0]

    # Pasada 2: Fase 4 (+ Fase 5 si queda remanente) — individual para grupos
    # de tamaño 1, conjunta para grupos con más de un ítem (productos espejo).
    items_por_grupo = {}
    for item_code, grupo_id in grupo_de_item.items():
        items_por_grupo.setdefault(grupo_id, []).append(item_code)

    alertas_renovacion = []
    for grupo_id, item_codes in items_por_grupo.items():
        # Fase 3 (renovación por vida útil) va entre Fase 2 y Fase 4, por grupo.
        alertas_renovacion.extend(_fase3_renovacion(estado, item_codes))
        if len(item_codes) == 1:
            item_code = item_codes[0]
            est = estado[item_code]
            if len(est['tiendas']) == 0:
                continue  # remaining queda igual; se reporta como alerta abajo
            tiendas_result, remaining = _fase4_individual(est['tiendas'], est['remaining'])
            tiendas_result, remaining, alertas_item = _fase5_individual(
                item_code, tiendas_result, remaining
            )
            estado[item_code] = {'tiendas': tiendas_result, 'remaining': remaining}
            alertas.extend(alertas_item)
        else:
            estado_grupo = {c: estado[c] for c in item_codes}
            resultado_grupo, alertas_por_item = _fase4_grupal(estado_grupo)
            for c in item_codes:
                estado[c] = resultado_grupo[c]
                alertas.extend(alertas_por_item[c])

    # Una sola alerta resumen de las renovaciones sin stock (el detalle por tienda
    # está en «Novedades»).
    sin_renovar = {a['item']: len(a['tiendas_sin_renovar'])
                   for a in alertas_renovacion if a.get('tiendas_sin_renovar')}
    alertas.extend(a for a in alertas_renovacion if not a.get('tiendas_sin_renovar'))
    if sin_renovar:
        n_combos = sum(sin_renovar.values())
        top = sorted(sin_renovar.items(), key=lambda kv: (-kv[1], str(kv[0])))[:8]
        alertas.append({
            'tipo': 'ADVERTENCIA',
            'item': '-',
            'mensaje': (
                f'{n_combos} tienda×ítem con producto vencido por vida útil quedaron sin caja de '
                f'renovación por falta de stock en CEDI ({len(sin_renovar)} ítem(s); más afectados: '
                + ', '.join(f'{i} ({n})' for i, n in top)
                + '). Detalle en la hoja «Novedades» (motivo «Sin stock en CEDI»).'
            ),
            'combos_sin_renovar': n_combos,
            'items_afectados': sorted(sin_renovar, key=str),
            'detalle_por_item': {str(i): n for i, n in sorted(sin_renovar.items(), key=lambda kv: str(kv[0]))},
        })

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

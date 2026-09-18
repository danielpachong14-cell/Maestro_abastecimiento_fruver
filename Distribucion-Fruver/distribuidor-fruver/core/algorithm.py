"""Motor de distribución — el corazón del sistema.

Distribuye el 100% de las cajas disponibles en el CEDI entre las tiendas
elegibles de cada ítem en CUATRO fases:

  FASE 1 — cubrir necesidades urgentes con cap creciente por rondas:
    Ordena tiendas por prioridad (AGOTADA → SAFETY → REPOSICIÓN → CUBIERTA)
    y dentro de cada nivel por consumo descendente. Ejecuta MIN_CAJAS_INICIAL
    rondas con cap 1, 2, 3…: en la ronda k cada tienda recibe hasta su k-ésima
    caja (deseadas(k) − ya_asignado). Todas las elegibles reciben su 1.ª caja
    antes de que cualquiera reciba su 2.ª, evitando concentración en tiendas de
    alto consumo cuando el stock es escaso.

  FASE 2 — escalar proporcionalmente a TARGET_DAYS:
    Cuando el stock no alcanza para llevar a TODOS a TARGET_DAYS, la escasez
    se reparte de forma proporcional a la necesidad de cada tienda. Ninguna
    tienda acapara cajas a costa de otras; todas reciben la misma fracción
    de su necesidad (corrección Hamilton para los residuos enteros).
    Cuando el stock SÍ alcanza, cada tienda recibe exactamente lo que necesita
    y el verdadero sobrante va a Fase 3.

  FASE 3 — excedente VOLUNTARIO, respetando el tope de sobre-stock:
    Repartir la misma CANTIDAD DE CAJAS a tiendas con consumos muy distintos
    reparte cantidades muy distintas de DÍAS (Δdías ≈ cajas/consumo). Por eso
    el sobrante se reparte por rondas proporcional a `consumo_diario /
    días_actuales` entre las tiendas elegibles (consumo > 0, bajo
    TOPE_EXCEDENTE tras recibir, sin alcanzar aún MAX_CAJAS_POR_ITEM), con
    MÁXIMO 1 CAJA POR TIENDA POR RONDA (no hasta MAX_CAJAS_POR_ITEM de un
    tirón) y corrección Hamilton para los residuos enteros. Capar a 1 por
    ronda obliga a re-chequear TOPE_EXCEDENTE tras cada caja individual, en
    vez de asignar varias de una vez a las pocas tiendas elegibles cuando hay
    mucho sobrante y poca competencia — evita que 1-2 tiendas se lleven todo
    mientras otras, apenas por encima del tope, se quedan en 0 (auditoría
    2026-07: caso real ítem 2001, dos tiendas terminaban con 10+ días de
    sobre-stock mientras una tercera con 4.8 días no recibía nada). El
    resultado: cada tienda gana más días cuanto más atrás vaya respecto al
    resto (Δdías_i = k / días_i), cerrando la brecha en lugar de mantenerla o
    ampliarla — sin que ninguna reciba más cajas por entrega que
    MAX_CAJAS_POR_ITEM (límite de espacio en tienda). Tiendas sin consumo solo
    reciben en Fase 1 si están AGOTADAS o en SAFETY. Fase 3 SIEMPRE respeta
    TOPE_EXCEDENTE: ninguna tienda recibe una caja que la deje por encima —
    puede terminar con cajas del CEDI sin repartir (`remaining > 0`), que
    pasan a Fase 4.

  FASE 4 — red de seguridad de cero-residuo (antes "Paso B" dentro de Fase 3):
    Si TODAS las tiendas elegibles llegaron al tope de días o al máximo de
    cajas (caso extremo: el CEDI recibió más stock del que el portafolio
    puede absorber sin sobre-stockearse), Fase 3 deja cajas sin asignar.
    La regla no negociable de cero residuo (todo el stock del CEDI debe
    despacharse) exige repartir ese resto de todos modos — esta fase existe
    para eso, y es la ÚNICA que puede asignarle a una tienda una caja que la
    deja por encima de TOPE_EXCEDENTE. Reparte por rondas de 1 caja, en orden
    de menor a mayor días proyectados, en DOS niveles: (1) primero solo entre
    tiendas que aún no llegan a MAX_CAJAS_POR_ITEM; (2) solo si TODAS las
    tiendas con consumo ya están en ese máximo y aún sobran cajas —
    matemáticamente imposible repartir sin superarlo— se ignora también ese
    límite. Antes de que existiera el nivel (1), el round-robin ignoraba
    MAX_CAJAS_POR_ITEM siempre, no solo como último recurso (auditoría
    2026-07: bug real confirmado en producción, ítem 2473, dos tiendas
    terminaban con 4 cajas en vez del máximo de 3). Al separarse de Fase 3
    (auditoría 2026-09) queda visible por separado en el reporte
    (`cajas_fase4`, columna propia en "Resumen Ítems") en vez de sumarse en
    silencio a "Cajas Fase 3" — un ítem con mucho volumen en Fase 4 es una
    señal de sobre-compra frente al portafolio real, no de reparto normal.

    Para ítems de un mismo GRUPO ESPEJO (mismo producto físico, distinto SKU —
    ver `grupo_id` en preprocessor.py), la Fase 3 se ejecuta de forma
    CONJUNTA (`_fase3_grupal`): elegibilidad, TOPE_EXCEDENTE y peso se
    calculan sobre el inventario/consumo COMBINADO del grupo en cada tienda,
    pero cada ítem sigue entregándose desde su propio stock CEDI
    (MAX_CAJAS_POR_ITEM sigue por SKU). Si una tienda recibe sobrante del
    grupo y un ítem del grupo tiene 0 unidades en esa tienda mientras el CEDI
    aún tiene stock de él, se le garantiza 1 caja de ese ítem antes de
    repartir el resto proporcional al consumo de cada SKU. Si tras esto algún
    ítem del grupo todavía tiene remanente (p. ej. recibió mucho más stock
    CEDI del que el portafolio combinado puede absorber), ese remanente se
    delega a la Fase 4 individual de siempre (cero residuo por SKU).

  GRUPOS ESPEJO EN FASE 1/2 — el mismo problema de doble conteo que resuelve
    la Fase 3 grupal existe en Fase 1/2: la necesidad de TARGET_DAYS se calcula
    a nivel de grupo (`_apportion_grupo_targets`) y se reparte ENTERA entre los
    ítems del grupo (piso + Hamilton) en vez de redondear hacia arriba la
    necesidad de cada ítem por separado — redondear por ítem sobreprovisiona
    el CEDI (p. ej. 3 SKUs al 33% cada uno con necesidad_grupo=1.0 pedirían
    3 cajas en vez de 1).
"""

import math

import numpy as np
import pandas as pd

TARGET_DAYS = 3.0           # días objetivo de inventario proyectado por tienda
MIN_STOCK_AGOTADO = 0.4     # por debajo de este STOCK (unidades) = AGOTADA
MIN_STOCK_SAFETY = 0.7      # por debajo de este STOCK (unidades) = STOCK SEGURIDAD
MIN_CAJAS_INICIAL = 2       # cap de cajas por tienda en Fase 1 (por ronda)
MAX_CAJAS_POR_ITEM = 3      # tope duro: máximo que una tienda recibe por ítem (todas las fases)
TOPE_EXCEDENTE = 5       # máximo días que puede acumular una tienda del excedente
UMBRAL_CONCENTRACION_SIN_CONSUMO = 5  # cajas: si Fase 4 concentraría >= esto en 1 tienda
                                       # por falta de consumo en el resto, reparto equitativo
_EPS_DIAS_ACTUALES = 1e-3  # sustituye a días_actuales==0 al ponderar el excedente: una
                            # tienda con 0 días es la MÁS urgente y debe recibir el mayor
                            # peso posible, no cero (dividir por un epsilon pequeño en vez
                            # de por 0 evita la división indefinida sin perder esa prioridad)


def _fmt_num(valor):
    """Formatea una cantidad para los mensajes de alerta: sin decimales si es
    entero exacto (6 en vez de 6.0), con dos si no (5.07)."""
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    return str(int(f)) if f == int(f) else f'{f:.2f}'


def _bloqueadas(tiendas_df):
    """Máscara booleana de filas excluidas por el tope MAX (ver preprocessor).

    Una tienda×ítem bloqueada ya tiene en existencias tanto o más de lo que
    puede tener de ese ítem, así que no participa en Fases 1-3 ni en el nivel
    1 de Fase 4. Solo el nivel 2 de Fase 4 (último recurso de cero residuo,
    cuando no queda ningún otro destino) puede asignarle cajas.

    Si el DataFrame no trae la columna (llamadas aisladas en tests con filas
    construidas a mano), se asume que ninguna está bloqueada — así el
    comportamiento histórico se mantiene intacto.
    """
    if 'bloqueado_por_max' not in tiendas_df.columns:
        return pd.Series(False, index=tiendas_df.index)
    return tiendas_df['bloqueado_por_max'].fillna(False).astype(bool)


def _alertas_max_forzado(item_code, tiendas, forzadas):
    """Construye las alertas de las cajas que Fase 4 tuvo que asignarle a
    tiendas bloqueadas por su MAX porque no quedaba ningún otro destino.

    `forzadas` es un dict {índice de fila: cajas forzadas}. Retorna una lista
    (vacía si no hubo ninguna) — una alerta por tienda afectada.
    """
    alertas = []
    tiene_max = 'max_existencias' in tiendas.columns
    tiene_exist = 'inventario_tienda' in tiendas.columns
    for idx, n_cajas in forzadas.items():
        max_ex = tiendas.at[idx, 'max_existencias'] if tiene_max else 0
        exist = tiendas.at[idx, 'inventario_tienda'] if tiene_exist else 0
        alertas.append({
            'tipo': 'ADVERTENCIA',
            'item': item_code,
            'mensaje': (
                f"Ítem {item_code}: la tienda {tiendas.at[idx, 'store_code']} "
                f"recibió {n_cajas} caja(s) pese a superar su MAX "
                f"({_fmt_num(max_ex)}) con {_fmt_num(exist)} en existencias — "
                "no había ningún otro destino posible para el sobrante "
                "(cero residuo)."
            ),
            'cajas_forzadas': int(n_cajas),
            'store_code': str(tiendas.at[idx, 'store_code']),
            'max_existencias': float(max_ex),
            'existencias': float(exist),
        })
    return alertas


def get_priority(row):
    """Clasifica la urgencia de una tienda-ítem.

    Usa las métricas a nivel de GRUPO espejo (inventario_efectivo_grupo,
    dias_proyectados_grupo), no las del ítem individual: dos ítems espejo que
    cubren la misma demanda no deben clasificarse como AGOTADA/REPOSICIÓN por
    separado si entre los dos ya hay stock suficiente en la tienda. Para un
    ítem sin espejo (grupo de tamaño 1) estos valores son idénticos a los
    individuales, así que el comportamiento no cambia.

    Opera fila a fila (recibe un `pd.Series`); para un DataFrame completo usar
    la versión vectorizada `_compute_priority`, que debe producir el mismo
    resultado.
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
    """Versión vectorizada de `get_priority` para un DataFrame completo —
    evita el costo de `.apply(get_priority, axis=1)` fila por fila. Misma
    lógica, mismo resultado."""
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
    """Cajas necesarias en Fase 1/2 para que el GRUPO espejo de cada
    (store_code, grupo_id) alcance TARGET_DAYS, repartidas ENTRE los ítems del
    grupo con piso + corrección Hamilton (`_reparto_proporcional`) — el techo
    (`ceil`) se aplica UNA sola vez por grupo, no por ítem.

    Aplicar `ceil()` después de multiplicar por `item_share_consumo` (como
    hacía la versión anterior de `_calc_deseadas`) sobreprovisiona: con 3
    ítems espejo al 33% cada uno y `necesidad_grupo=1.0`, cada uno redondeaba
    `ceil(0.33)=1` por separado, sumando 3 cajas donde el grupo solo
    necesitaba 1. Redondeando una sola vez a nivel de grupo y repartiendo ese
    entero, la suma nunca excede `ceil(necesidad_grupo)`.

    Para un ítem sin espejo (grupo de tamaño 1, `item_share_consumo == 1.0`)
    el resultado es idéntico a `ceil(necesidad_grupo)` — mismo comportamiento
    de siempre.

    Retorna una Series de enteros (cajas objetivo por ítem) alineada al
    índice de `tiendas_df`.
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
    """Cajas que la tienda querría recibir, limitadas por el cap del pase.

    Lee `row.cajas_target_grupo` (precalculado por `_apportion_grupo_targets`
    a nivel de grupo, ver módulo) en vez de volver a redondear la necesidad
    del ítem por separado. Si la fila no trae esa columna (p. ej. una llamada
    aislada en tests con un `pd.Series` construido a mano) se recalcula con la
    fórmula anterior — para un ítem sin espejo (`item_share_consumo == 1`) da
    exactamente el mismo resultado.

    consumo_diario e inventario_efectivo están en cajas (Unidades de Distribución),
    igual que cajas_disponibles_cedi, así que no se necesita conversión.
    """
    if row.consumo_diario <= 0:
        # Sin consumo: no se puede proyectar demanda; mínimo según prioridad.
        return 1 if row.priority >= 2 else 0
    cajas_target = getattr(row, 'cajas_target_grupo', None)
    if cajas_target is None:
        necesidad_grupo = max(0.0, TARGET_DAYS * row.consumo_diario_grupo - row.inventario_efectivo_grupo)
        cajas_target = math.ceil(necesidad_grupo * row.item_share_consumo)
    min_boxes = 1 if row.priority >= 2 else 0
    return max(min_boxes, min(int(cajas_target), cap))


def _reparto_proporcional(pesos, total, room):
    """Reparte hasta `total` cajas (entero) proporcional a `pesos`, sin
    exceder `room` (espacio restante) por tienda. Usa piso + corrección
    Hamilton para que la suma entera coincida con `total` — o con `room.sum()`
    si éste es menor — repartiendo el residuo a quienes tienen mayor fracción
    pendiente y todavía tienen espacio.

    Si el peso de cada tienda es proporcional a su `consumo_diario`, el
    incremento de stock resultante también lo es (`Δstock_i = k · peso_i`), lo
    que hace que el incremento de días proyectados (`Δdías_i = Δstock_i /
    consumo_i`) sea igual para todas — reparte días, no solo cajas.

    Pesos negativos se descartan (`clip(lower=0)`): pueden aparecer si un
    valor de inventario negativo se cuela aguas arriba (no debería, ver
    `.clip(lower=0)` en preprocessor.py, pero esta función no depende de esa
    garantía externa para mantener la suma exacta).

    Retorna una Series de enteros con las cajas asignadas en esta pasada
    (mismo índice que `pesos`).
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

    Si `tiendas_df` no trae ya la columna `cajas_target_grupo` (ver
    `_apportion_grupo_targets`), la calcula sobre las filas recibidas —
    correcto para una llamada aislada de un solo ítem (sin coordinación con
    sus hermanos espejo), pero `run_distribution` la precalcula sobre TODO
    `df_merged` antes de trocearlo por ítem, para que sí haya coordinación
    real entre ítems del mismo grupo.

    Retorna (tiendas, remaining). No genera alertas — las fases 1/2 nunca
    dejan remanente "atascado" sin tiendas elegibles de forma anómala (eso
    solo puede pasar en Fase 3); si `tiendas_df` está vacío, `remaining`
    simplemente queda igual a `cajas_disponibles`.
    """
    tiendas = tiendas_df.copy()
    tiendas['priority'] = _compute_priority(tiendas)
    tiendas['cajas_asignadas'] = 0
    tiendas['cajas_fase1'] = 0
    tiendas['cajas_fase2'] = 0
    tiendas['cajas_fase3'] = 0
    tiendas['cajas_fase4'] = 0
    remaining = int(cajas_disponibles)

    if len(tiendas) == 0:
        return tiendas, remaining

    if 'cajas_target_grupo' not in tiendas.columns:
        tiendas['cajas_target_grupo'] = _apportion_grupo_targets(tiendas)

    # Orden: prioridad DESC, consumo DESC (agotados y mejores vendedores
    # primero); store_code como desempate terciario para que el orden sea
    # determinista entre corridas ante empates exactos de prioridad/consumo.
    tiendas = tiendas.sort_values(
        ['priority', 'consumo_diario', 'store_code'], ascending=[False, False, True]
    ).reset_index(drop=True)

    # FASE 1 — rondas con cap creciente (1 → MIN_CAJAS_INICIAL).
    # En cada ronda, la tienda recibe el delta entre deseadas(cap) y lo ya
    # asignado. Todas las elegibles reciben su 1.ª caja antes de que cualquiera
    # reciba su 2.ª, evitando que las tiendas de alto consumo acaparen cuando
    # el stock es escaso y hay múltiples tiendas AGOTADAS.
    # Filas excluidas por el tope MAX: no reciben en Fase 1/2 (ni en 3, ni en
    # el nivel 1 de Fase 4). Se conservan en el DataFrame — no se separan —
    # para que el nivel 2 de Fase 4 pueda usarlas como último recurso y para
    # que el concat final de `run_distribution` no las pierda.
    bloqueadas = _bloqueadas(tiendas)

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

    # FASE 2 — completar TARGET_DAYS de forma proporcional.
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
        # Las bloqueadas por MAX quedan con room 0 — fuera del reparto.
        room = (MAX_CAJAS_POR_ITEM - tiendas['cajas_asignadas']).clip(lower=0)
        room = room.where(~bloqueadas, 0)
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
            tiendas['cajas_fase2'] += asignado
            remaining -= int(asignado.sum())

    return tiendas, remaining


def _fase3_individual(tiendas, remaining):
    """Reparte el excedente VOLUNTARIO de UN ítem entre sus tiendas (sin ver
    otros ítems del mismo grupo espejo). Usado para ítems sin espejo y como
    paso previo a Fase 4 cuando la Fase 3 grupal no logra colocar todo el
    remanente de un ítem específico.

    A diferencia de Fase 4, esta función NUNCA le da a una tienda una caja
    que la deje por encima de TOPE_EXCEDENTE — por diseño puede terminar con
    `remaining > 0` si todas las tiendas elegibles llegaron al tope o al
    máximo de cajas (ver Fase 4, que se encarga de ese remanente).

    Retorna (tiendas, remaining). No genera alertas.
    """
    tiendas = tiendas.copy()

    if len(tiendas) == 0 or remaining <= 0:
        return tiendas, remaining

    # FASE 3 — excedente: repartir cada ronda proporcional a
    # `consumo_diario / días_actuales` entre las tiendas elegibles (consumo > 0,
    # bajo TOPE_EXCEDENTE tras recibir, sin alcanzar aún MAX_CAJAS_POR_ITEM).
    #
    # Por qué ese peso y no "1 caja por tienda" ni "proporcional al consumo a
    # secas": repartir cantidades IGUALES de cajas reparte cantidades MUY
    # DESIGUALES de días (Δdías ≈ cajas / consumo) — la tienda lenta gana
    # muchos más días por caja que la rápida, que es justo el desbalance que
    # se busca evitar. Ponderar por `consumo_diario / días_actuales` (=
    # consumo² / stock) dosifica el excedente según dos señales: cuánto vende
    # (consumo) y qué tan atrás va respecto al resto (1/días); el resultado es
    # que el incremento de días de cada tienda es inversamente proporcional a
    # sus días actuales (Δdías_i = k / días_i): las que van más atrasadas se
    # acercan más rápido al resto en lugar de mantener — o ampliar — la
    # brecha. Detalle de implementación en `_EPS_DIAS_ACTUALES` (arriba):
    # una tienda con 0 días actuales es la más urgente y debe tener el mayor
    # peso, no uno nulo.
    # Las bloqueadas por el tope MAX no participan del excedente voluntario.
    con_consumo_mask = (tiendas['consumo_diario'] > 0) & ~_bloqueadas(tiendas)

    if con_consumo_mask.any():
        # Por rondas — cada ronda puede sacar tiendas de la elegibilidad
        # (alcanzan el tope de días o el máximo de cajas), así que se
        # recalcula el peso y el espacio disponible y se reparte de nuevo lo
        # que quede entre las que siguen elegibles.
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
            dias_act_ponderable = dias_act.mask(dias_act <= 0, _EPS_DIAS_ACTUALES)
            pesos = (tiendas['consumo_diario'] / dias_act_ponderable).where(elegibles_mask, 0.0)
            # Cap de 1 caja/tienda/ronda (no hasta MAX_CAJAS_POR_ITEM de una
            # vez): con pocas tiendas elegibles y sobrante grande, dar hasta 3
            # cajas en una sola ronda las empujaba muy por encima de
            # TOPE_EXCEDENTE sin volver a chequear el tope entre la 1ª y la
            # 3ª caja. Al capar a 1, el bucle de rondas (ya existente) vuelve
            # a evaluar elegibilidad tras cada caja, y el sobrante se reparte
            # primero entre más tiendas antes de que cualquiera reciba una 2ª.
            room = ((MAX_CAJAS_POR_ITEM - tiendas['cajas_asignadas'])
                    .clip(lower=0, upper=1)
                    .where(elegibles_mask, 0))
            asignado = _reparto_proporcional(pesos, remaining, room)
            tiendas['cajas_asignadas'] += asignado
            tiendas['cajas_fase3'] += asignado
            remaining -= int(asignado.sum())

    return tiendas, remaining


def _fase4_individual(item_code, tiendas, remaining):
    """FASE 4 — red de seguridad de cero-residuo: reparte lo que Fase 3 no
    logró colocar sin violar TOPE_EXCEDENTE (todas las tiendas elegibles ya
    estaban en el tope de días o en MAX_CAJAS_POR_ITEM). Es la ÚNICA función
    del módulo que puede asignarle a una tienda una caja que la deje por
    encima de TOPE_EXCEDENTE — existe porque la regla de cero residuo (todo
    el stock del CEDI se despacha) tiene prioridad sobre el tope cuando ya no
    hay forma de repartir sin superarlo.

    Lo mismo aplica al tope MAX por tienda×ítem: el nivel 1 excluye a las
    tiendas bloqueadas, y solo el nivel 2 (cuando ya no queda ningún otro
    destino) puede darles cajas, siempre al final del orden y dejando una
    alerta por tienda que nombra el MAX y las existencias reales.

    Retorna (tiendas, remaining, alertas_item). remaining debe terminar en 0.
    """
    tiendas = tiendas.copy()
    alertas_item = []

    if len(tiendas) == 0 or remaining <= 0:
        return tiendas, remaining, alertas_item

    # Las bloqueadas por el tope MAX se excluyen de todo el reparto normal de
    # Fase 4; solo el nivel 2 (último recurso) vuelve a considerarlas, y al
    # final del orden. Ver `_bloqueadas` y el paso 9.b del preprocessor.
    bloqueadas = _bloqueadas(tiendas)
    con_consumo_mask = (tiendas['consumo_diario'] > 0) & ~bloqueadas

    if con_consumo_mask.any():
        # Reparto por menor días proyectados, en dos niveles: primero solo
        # entre tiendas bajo MAX_CAJAS_POR_ITEM; ignora ese límite solo si es
        # matemáticamente inevitable (ver docstring del módulo).
        stock_act = tiendas['inventario_efectivo'] + tiendas['cajas_asignadas']
        dias_act = (stock_act[con_consumo_mask]
                    / tiendas.loc[con_consumo_mask, 'consumo_diario'])
        order = dias_act.sort_values(ascending=True, kind='stable').index.tolist()

        n_con_consumo = int(con_consumo_mask.sum())
        n_sin_consumo = len(tiendas) - n_con_consumo - int(bloqueadas.sum())
        max_concentracion = math.ceil(remaining / n_con_consumo)

        if max_concentracion >= UMBRAL_CONCENTRACION_SIN_CONSUMO and n_sin_consumo > 0:
            # El reparto normal de Fase 4 (solo tiendas con consumo>0)
            # concentraría demasiadas cajas en pocas tiendas porque el
            # resto del portafolio no tiene datos de venta para este
            # ítem. En su lugar, repartir parejo entre TODAS las
            # tiendas del portafolio activo del ítem.
            tienda_concentracion = tiendas.at[order[0], 'store_code']
            remaining_original = remaining
            # El reparto equitativo cubre todas las tiendas del portafolio
            # activo EXCEPTO las bloqueadas por MAX; si todas estuvieran
            # bloqueadas se cae a ellas igual (cero residuo manda) y el
            # nivel 2 de más abajo emite la alerta correspondiente.
            candidatas_eq = tiendas.index[~bloqueadas]
            if len(candidatas_eq) == 0:
                candidatas_eq = tiendas.index
            while remaining > 0:
                idx_min = tiendas.loc[candidatas_eq, 'cajas_asignadas'].idxmin()
                tiendas.at[idx_min, 'cajas_asignadas'] += 1
                tiendas.at[idx_min, 'cajas_fase4'] += 1
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
            # Nivel 1: round-robin por menor días, saltando tiendas que
            # ya llegaron a MAX_CAJAS_POR_ITEM — la versión anterior no
            # revisaba este tope aquí y podía darle una 4ª+ caja a la
            # tienda con menos días aunque ya hubiera alcanzado el
            # máximo (bug real confirmado con datos de producción: ítem
            # 2473, dos tiendas terminaban con 4 cajas).
            con_espacio = [idx for idx in order if tiendas.at[idx, 'cajas_asignadas'] < MAX_CAJAS_POR_ITEM]
            i = 0
            while remaining > 0 and con_espacio:
                idx = con_espacio[i % len(con_espacio)]
                tiendas.at[idx, 'cajas_asignadas'] += 1
                tiendas.at[idx, 'cajas_fase4'] += 1
                remaining -= 1
                if tiendas.at[idx, 'cajas_asignadas'] >= MAX_CAJAS_POR_ITEM:
                    con_espacio.pop(i % len(con_espacio))
                else:
                    i += 1

            # Nivel 2 (último recurso real): todas las tiendas con
            # consumo ya están en MAX_CAJAS_POR_ITEM y aún sobran cajas —
            # matemáticamente imposible repartir sin superar el tope. Se
            # ignora aquí, misma filosofía que ya aplica para
            # TOPE_EXCEDENTE: cero residuo tiene prioridad sobre el tope
            # cuando es la única forma de no dejar cajas sin asignar.
            #
            # Las bloqueadas por MAX se reincorporan aquí, pero SIEMPRE al
            # final del orden: una tienda saturada solo recibe cuando ya no
            # existe ningún otro destino. Cada caja así asignada se registra
            # para emitir la alerta de más abajo.
            order_n2 = order + [idx for idx in tiendas.index if bloqueadas.at[idx]]
            forzadas = {}
            i = 0
            while remaining > 0:
                idx = order_n2[i % len(order_n2)]
                tiendas.at[idx, 'cajas_asignadas'] += 1
                tiendas.at[idx, 'cajas_fase4'] += 1
                if bloqueadas.at[idx]:
                    forzadas[idx] = forzadas.get(idx, 0) + 1
                remaining -= 1
                i += 1

            alertas_item.extend(_alertas_max_forzado(item_code, tiendas, forzadas))
    else:
        # No queda ninguna tienda elegible con consumo: o ninguna tiene consumo
        # registrado, o todas las que lo tienen están bloqueadas por su MAX.
        # Round-robin hasta vaciar, prefiriendo las no bloqueadas; si todas lo
        # están reciben igual — cero residuo manda — y se deja alerta por
        # tienda, con el mismo formato que el nivel 2 de más arriba.
        indices = [idx for idx in tiendas.index if not bloqueadas.at[idx]]
        if not indices:
            indices = list(tiendas.index)
        forzadas = {}
        i = 0
        while remaining > 0 and indices:
            idx = indices[i % len(indices)]
            tiendas.at[idx, 'cajas_asignadas'] += 1
            tiendas.at[idx, 'cajas_fase4'] += 1
            if bloqueadas.at[idx]:
                forzadas[idx] = forzadas.get(idx, 0) + 1
            remaining -= 1
            i += 1
        alertas_item.extend(_alertas_max_forzado(item_code, tiendas, forzadas))

    return tiendas, remaining, alertas_item


def _fase3_grupal(estado_items):
    """Reparte el sobrante de un GRUPO de ítems espejo de forma conjunta.

    `estado_items` es un dict {item_code: {'tiendas': df, 'remaining': int}}
    con todos los ítems de un mismo `grupo_id` ya resueltos en Fase 1/2
    (vía `_priorizar_y_fases_1_2`). Cada `tiendas` df tiene columna
    `store_code` única por fila.

    Elegibilidad, TOPE_EXCEDENTE y peso (`consumo/días_actuales`) se calculan
    sobre el inventario y consumo COMBINADO del grupo en cada tienda, con el
    mismo cap de 1 caja COMBINADA del grupo por tienda por ronda que usa
    `_fase3_individual` (ver docstring del módulo); el total repartido a una
    tienda en una ronda se separa luego entre los ítems del grupo presentes en
    ella, garantizando primero 1 caja a cualquier ítem que la tienda tenga en
    0 unidades (si el CEDI todavía tiene stock de él), y repartiendo el resto
    proporcional a `item_share_consumo`. MAX_CAJAS_POR_ITEM se sigue
    respetando por ítem individual. Cada ítem nunca usa el stock CEDI de otro
    — solo se comparte la decisión de "cuánto le toca a esta tienda".

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

    # FASE 3 GRUPAL — análogo a _fase3_individual, pero agregando por tienda
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
            # Un ítem bloqueado por MAX en esta tienda no aporta espacio: el
            # bloqueo es por tienda×ÍTEM, no por grupo espejo, así que un
            # hermano bloqueado no frena a los demás ítems del grupo.
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
        # Cap de 1 caja COMBINADA del grupo por tienda por ronda — mismo
        # mecanismo y misma razón que en _fase3_individual (ver ahí):
        # evita que 1-2 tiendas elegibles se lleven varias cajas de un
        # tirón sin volver a chequear TOPE_EXCEDENTE entre cada una.
        room_elig = room_store.clip(upper=1).where(elegibles_mask, 0)
        total_pool = sum(remaining[c] for c in activos)
        asignado_store = _reparto_proporcional(pesos, total_pool, room_elig)

        for store in asignado_store[asignado_store > 0].index:
            n = int(asignado_store[store])

            # Un ítem bloqueado por MAX en esta tienda no es candidato a
            # recibir en Fase 3 (solo el nivel 2 de Fase 4 puede forzarlo).
            candidatos = [
                c for c in items_con_tiendas
                if store in tablas[c].index
                and remaining[c] > 0
                and (MAX_CAJAS_POR_ITEM - int(tablas[c].at[store, 'cajas_asignadas'])) > 0
                and not bool(_bloqueadas(tablas[c]).at[store])
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
                    t.at[store, 'cajas_fase3'] += 1
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
                tablas[c].at[store, 'cajas_fase3'] += int(cajas)
                remaining[c] -= int(cajas)

        total_remaining = sum(remaining.values())

    # FASE 4 — cualquier remanente por ítem (p. ej. un SKU recibió mucho más
    # stock CEDI del que el portafolio combinado puede absorber) se delega a
    # la Fase 4 individual de siempre: a estas alturas el grupo ya cumplió su
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
            t, rem, alertas_item = _fase4_individual(code, t, rem)
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
    tiendas, remaining = _fase3_individual(tiendas, remaining)
    return _fase4_individual(item_code, tiendas, remaining)


def run_distribution(df_merged, alertas_extra=None):
    """Ejecuta la distribución para todos los ítems del DataFrame unificado.

    `alertas_extra` (opcional): alertas ya detectadas aguas arriba (p. ej. en
    `preprocessor.build_distribution_df`, ítems huérfanos o filas descartadas
    por normalización) que se anteponen a las que genera esta función.

    Fase 1/2 se calcula por ítem, pero con la necesidad de grupo espejo
    precalculada sobre TODO `df_merged` (`_apportion_grupo_targets`) antes de
    trocear por ítem, para que sí haya coordinación real entre ítems
    hermanos. Fase 3 (+ Fase 4 si queda remanente) se ejecuta por ítem
    individual para grupos de tamaño 1, o de forma conjunta (`_fase3_grupal`,
    que delega a `_fase4_individual` el remanente por SKU) para grupos con
    más de un ítem con stock CEDI disponible.

    Retorna (df_output, alertas):
      - df_output: filas con cajas_asignadas > 0
      - alertas: lista de dicts {tipo, item, mensaje}
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

    # Alerta agregada (una sola, no por ítem, para no saturar la hoja de
    # Alertas): combinaciones tienda-ítem sin match en Celes se tratan con
    # consumo=0/inventario=0 aguas abajo, lo que las clasifica como AGOTADA
    # sin serlo necesariamente — ver preprocessor.build_distribution_df.
    if 'sin_match_celes' in df_merged.columns:
        sin_match_mask = df_merged['sin_match_celes'].fillna(False)
        n_sin_match = int(sin_match_mask.sum())
        if n_sin_match > 0:
            pct = round(n_sin_match / len(df_merged) * 100, 1)
            n_items = df_merged.loc[sin_match_mask, 'item_code'].nunique()
            alertas.append({
                'tipo': 'ADVERTENCIA',
                'item': '-',
                'mensaje': (
                    f'{n_sin_match} combinación(es) tienda-ítem ({pct}% del total, '
                    f'{n_items} ítem(s) afectados) no tuvieron match en Celes — se '
                    'trataron con consumo=0 e inventario=0 (riesgo de clasificarse '
                    'como AGOTADA sin serlo). Revisar cobertura de Celes.'
                ),
                'combos_sin_match': n_sin_match,
                'items_afectados': int(n_items),
                'pct_del_total': pct,
            })

    # Necesidad de grupo espejo precalculada sobre TODO el universo antes de
    # trocear por ítem — ver docstring del módulo y de _apportion_grupo_targets.
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

    # Pasada 2: Fase 3 (+ Fase 4 si queda remanente) — individual para grupos
    # de tamaño 1, conjunta para grupos con más de un ítem (productos espejo).
    items_por_grupo = {}
    for item_code, grupo_id in grupo_de_item.items():
        items_por_grupo.setdefault(grupo_id, []).append(item_code)

    for grupo_id, item_codes in items_por_grupo.items():
        if len(item_codes) == 1:
            item_code = item_codes[0]
            est = estado[item_code]
            if len(est['tiendas']) == 0:
                continue  # remaining queda igual; se reporta como alerta abajo
            tiendas_result, remaining = _fase3_individual(est['tiendas'], est['remaining'])
            tiendas_result, remaining, alertas_item = _fase4_individual(
                item_code, tiendas_result, remaining
            )
            estado[item_code] = {'tiendas': tiendas_result, 'remaining': remaining}
            alertas.extend(alertas_item)
        else:
            estado_grupo = {c: estado[c] for c in item_codes}
            resultado_grupo, alertas_por_item = _fase3_grupal(estado_grupo)
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

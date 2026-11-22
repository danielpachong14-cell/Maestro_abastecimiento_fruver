"""Motor de compras: compra por tienda × ítem y general por ítem (nivel objetivo con MIN de exhibición
como piso, límite por vida útil y suavizado α↑/α↓). Fórmulas en CLAUDE.md.
"""

import math
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from .algorithm import MIN_STOCK_AGOTADO, _reparto_proporcional
from .compras_params import cargar_parametros
from .compras_preprocessor import build_purchase_df

NIVEL_CUBIERTA, NIVEL_AGOTADA, NIVEL_EXHIBICION, NIVEL_REPOSICION = 0, 1, 2, 3
_NOMBRE_NIVEL = {NIVEL_AGOTADA: 'Agotada proyectada', NIVEL_EXHIBICION: 'Exhibición',
                 NIVEL_REPOSICION: 'Reposición'}
_SIN_TOPE = 10 ** 6


@dataclass
class ResultadoCompra:
    df_tienda: pd.DataFrame
    df_item: pd.DataFrame
    plan: object
    params: object
    alertas: list = field(default_factory=list)
    oc_detalle: pd.DataFrame = None          # cada línea de OC con su clasificación (hoja «OC en Tránsito»)

    @property
    def total_cajas(self):
        return int(self.df_item['compra_sugerida'].sum()) if len(self.df_item) else 0

    @property
    def valor_total(self):
        return float(self.df_item['valor_compra'].fillna(0).sum()) if len(self.df_item) else 0.0


def _nivel(inv, min_ex, necesidad):
    return np.select(
        [necesidad <= 1e-9, inv < MIN_STOCK_AGOTADO, inv < min_ex],
        [NIVEL_CUBIERTA, NIVEL_AGOTADA, NIVEL_EXHIBICION],
        default=NIVEL_REPOSICION,
    )


def _asignar_por_niveles(necesidad, nivel, total, room):
    """Reparte `total` cajas enteras de nivel 1 a 3; dentro de cada nivel, proporcional a la
    necesidad (Hamilton), sin superar `room` por tienda. Retorna (asignado, sobrante)."""
    asignado = pd.Series(0, index=necesidad.index, dtype=int)
    restante = int(total)
    for n in (NIVEL_AGOTADA, NIVEL_EXHIBICION, NIVEL_REPOSICION):
        if restante <= 0:
            break
        m = (nivel == n) & (room > 0)
        if not m.any():
            continue
        x = _reparto_proporcional(necesidad[m], min(restante, int(room[m].sum())), room[m])
        asignado.loc[x.index] += x
        restante -= int(x.sum())
    return asignado, restante


def _recortar(compra, nivel, cajas):
    """Quita `cajas` de la compra: primero Reposición, luego Exhibición; nunca Agotadas.
    Retorna (compra, cajas que no se pudieron quitar)."""
    compra = compra.copy()
    for n in (NIVEL_REPOSICION, NIVEL_CUBIERTA, NIVEL_EXHIBICION):
        if cajas <= 0:
            break
        m = (nivel == n) & (compra > 0)
        if not m.any():
            continue
        quita = _reparto_proporcional(compra[m].astype(float), min(cajas, int(compra[m].sum())), compra[m])
        compra.loc[quita.index] -= quita
        cajas -= int(quita.sum())
    return compra, int(cajas)


def _agregar(compra, pesos, cajas):
    """Suma `cajas` a la compra, proporcional a `pesos` (venta esperada; parejo si todo es 0)."""
    if cajas <= 0 or len(compra) == 0:
        return compra
    pesos = pesos.clip(lower=0)
    if float(pesos.sum()) <= 0:
        pesos = pd.Series(1.0, index=compra.index)
    room = pd.Series(_SIN_TOPE, index=compra.index)
    return compra + _reparto_proporcional(pesos, int(cajas), room)


def _ajustar_multiplo(cajas, multiplo, minimo):
    if cajas <= 0:
        return 0
    cajas = max(cajas, minimo)
    return int(math.ceil(cajas / multiplo) * multiplo)


def _simular_lotes(g, plan, params, dia_pool):
    """Simula día a día, hasta la llegada de la compra, el inventario de cada tienda×ítem por lotes.

    Lotes: existencias (recibidas hace `dias_desde_ingreso` días), tránsito de Celes (llega hoy) y
    pedido de distribución de hoy (llega en LT_CEDI_TIENDA días). Cada día se vende `consumo ajustado
    × factor del día`, primero del lote más viejo (FIFO); la venta que no se cubre se pierde. Con
    INVENTARIO_SEGUN_VIDA_UTIL, un lote deja de contar cuando sus días desde el ingreso superan la vida
    útil de exhibición (misma regla que la Fase 3 de la distribución); sin él, los lotes no vencen
    (inventario de Celes tal cual). Sin fecha de último ingreso, las existencias se toman como frescas.

    Retorna (inventario vivo a la llegada, venta perdida desde `dia_pool`) — esa venta perdida es la que
    absorbe lo que llega en camino ese día (OC / CEDI), antes de que llegue la compra.
    """
    n = len(g)
    dias = plan.n_dias_hasta_llegada
    factores = list(plan.factores_hasta_llegada) or [1.0] * dias
    inicio = 0 if (plan.dias_hasta_llegada and plan.dias_hasta_llegada[0] == plan.fecha_datos) else 1
    d_cons = g['consumo_ajustado'].to_numpy(float)
    if params.inventario_segun_vida_util:
        vu = g['vida_util'].to_numpy(float)
        vu = np.where(np.isnan(vu) | (vu <= 0), np.inf, vu)
    else:
        vu = np.full(n, np.inf)
    edad = np.nan_to_num(g['dias_desde_ingreso'].to_numpy(float), nan=0.0).clip(min=0)
    lt = float(params.lt_cedi_tienda)
    # (cantidad, día de ingreso relativo a hoy): ordenados del más viejo al más nuevo.
    lotes = [
        [g['existencias'].to_numpy(float).copy(), -edad],
        [g['transito'].to_numpy(float).copy(), np.zeros(n)],
        [g['pedido_hoy'].to_numpy(float).copy(), np.full(n, lt)],
    ]
    perdida_despues = np.zeros(n)
    for k in range(dias):
        dia = inicio + k                       # día relativo a hoy (0 = hoy)
        demanda = d_cons * factores[k]
        for cant, ingreso in lotes:
            disponible = (ingreso <= dia) & ((dia - ingreso) <= vu)
            vende = np.where(disponible, np.minimum(cant, demanda), 0.0)
            cant -= vende
            demanda = demanda - vende
        if dia >= inicio + dia_pool:
            perdida_despues += demanda
    llegada = inicio + dias
    vivo = sum(np.where((ingreso <= llegada) & ((llegada - ingreso) <= vu), cant, 0.0) for cant, ingreso in lotes)
    return pd.Series(vivo, index=g.index), pd.Series(perdida_despues, index=g.index)


def _comprar_item(g, item, params, plan):
    """Calcula la compra de un ítem sobre sus tienda×ítem con datos. Retorna (g con columnas, resumen)."""
    g = g.copy()
    pool = int(round(item['oc_transito'] + item['cedi_remanente']))

    # Proyección por lotes hasta la llegada (ver `_simular_lotes`): la venta sale primero del lote más
    # viejo; una tienda en 0 pierde esa venta (no se descuenta de lo que llega después). Lo que está en
    # camino al CEDI (OC + CEDI sin repartir) llega a la tienda en `dia_pool`.
    llegada_pool = item.get('pool_llegada_tienda')
    dia_pool = plan.n_dias_hasta_llegada
    if pool > 0 and llegada_pool is not None and not pd.isna(llegada_pool):
        llegada_pool = llegada_pool.date() if hasattr(llegada_pool, 'date') and callable(llegada_pool.date) else llegada_pool
        dia_pool = max(0, (llegada_pool - plan.fecha_datos).days - (0 if plan.dias_hasta_llegada and
                                                                     plan.dias_hasta_llegada[0] == plan.fecha_datos else 1))
    inv0, perdida_despues = _simular_lotes(g, plan, params, dia_pool)
    nec0 = (g['objetivo'] - inv0).clip(lower=0)
    niv0 = pd.Series(_nivel(inv0, g['min_exhibicion'], nec0), index=g.index)

    # Lo que está en camino se asigna como lo hará la distribución: primero a las agotadas. Cada tienda
    # recibe hasta lo que la deja en su objetivo a la llegada (incluye la venta que absorbe antes).
    nec_pool = (nec0 + perdida_despues).where(nec0 > 1e-9, 0.0)
    room0 = np.ceil(nec_pool - 1e-9).clip(lower=0).astype(int)
    g['oc_asignada'], pool_sobrante = _asignar_por_niveles(nec_pool, niv0, pool, room0)
    g['inv_proy_llegada'] = inv0 + (g['oc_asignada'] - perdida_despues).clip(lower=0)
    g['necesidad'] = (g['objetivo'] - g['inv_proy_llegada']).clip(lower=0)
    g['nivel'] = _nivel(g['inv_proy_llegada'], g['min_exhibicion'], g['necesidad'])
    g['compra_redondeada'] = np.floor(g['necesidad'] + 1 - params.umbral_redondeo + 1e-9).clip(lower=0).astype(int)

    base = float(g['venta_cobertura'].sum())
    bruto = int(g['compra_redondeada'].sum())
    alfa = params.alfa_subida if bruto > base else params.alfa_bajada
    suavizada = max(0, math.floor(base + alfa * (bruto - base) + 0.5))
    multiplo, minimo = int(item['multiplo']), int(item['minimo'])
    objetivo_item = _ajustar_multiplo(suavizada, multiplo, minimo)

    compra = g['compra_redondeada'].copy()
    nivel = pd.Series(g['nivel'], index=g.index)
    protegido = False
    if objetivo_item < bruto:
        compra, no_quitado = _recortar(compra, nivel, bruto - objetivo_item)
        if no_quitado > 0:  # no se recorta a las agotadas: la compra sube hasta cubrirlas
            protegido = True
            objetivo_item = _ajustar_multiplo(int(compra.sum()), multiplo, minimo)
    compra = _agregar(compra, g['venta_cobertura'], objetivo_item - int(compra.sum()))
    g['compra'] = compra.astype(int)

    motivo_compra = pd.Series(g['nivel']).map(_NOMBRE_NIVEL).fillna('Ajuste de compra')
    motivo_sin = np.select(
        [g['necesidad'] <= 1e-9, g['compra_redondeada'] > 0],
        ['Cubierta', 'Recortada por suavizado'], default='Bajo umbral de redondeo')
    g['motivo'] = np.where(g['compra'] > 0, motivo_compra.values, motivo_sin)

    resumen = {
        'venta_esperada': base, 'necesidad_tiendas': float(g['necesidad'].sum()),
        'compra_bruta': bruto, 'compra_suavizada': suavizada, 'oc_asignada': int(g['oc_asignada'].sum()),
        'oc_sobrante': int(pool_sobrante), 'compra_sugerida': int(g['compra'].sum()),
        'protegio_agotadas': protegido,
    }
    return g, resumen


def run_compras(df, items, plan, params, alertas_extra=None):
    """Ejecuta la compra. df/items: salida de `build_purchase_df`. Retorna ResultadoCompra."""
    alertas = list(alertas_extra or [])
    df = df.copy()
    for col, val in (('oc_asignada', 0), ('inv_proy_llegada', np.nan), ('necesidad', 0.0), ('nivel', 0),
                     ('compra_redondeada', 0), ('compra', 0), ('motivo', 'Sin datos Celes')):
        df[col] = val

    filas_item = []
    for _, item in items.iterrows():
        code = item['item_code']
        m = (df['item_code'] == code) & df['con_datos']
        resumen = {'venta_esperada': 0.0, 'necesidad_tiendas': 0.0, 'compra_bruta': 0, 'compra_suavizada': 0,
                   'oc_asignada': 0, 'oc_sobrante': int(round(item['oc_transito'] + item['cedi_remanente'])),
                   'compra_sugerida': 0, 'protegio_agotadas': False}
        resumen['periodo_activado'] = False
        if m.any():
            g, resumen = _comprar_segun_frecuencia(df[m], item, params, plan)
            df.loc[g.index, g.columns] = g
        filas_item.append({**item.to_dict(), **resumen})

    _RESUMEN = ['venta_esperada', 'necesidad_tiendas', 'compra_bruta', 'compra_suavizada', 'oc_asignada',
                'oc_sobrante', 'compra_sugerida', 'protegio_agotadas', 'periodo_activado']
    df_item = pd.DataFrame(filas_item, columns=list(items.columns) + _RESUMEN)
    if len(df_item) == 0:
        alertas.append({'tipo': 'ERROR', 'item': '-',
                        'mensaje': 'No hay ítems ACTIVOS en Portafolio Fruver: no se calculó ninguna compra.'})

    datos = df[df['con_datos']]
    con = datos.groupby('item_code').agg(consumo_red=('consumo_ajustado', 'sum'),
                                         inv_llegada_red=('inv_proy_llegada', 'sum'))
    df_item = df_item.merge(con, on='item_code', how='left')
    df_item['consumo_red'] = df_item['consumo_red'].fillna(0)
    df_item['compra_final'] = df_item['compra_sugerida']
    df_item['ajuste_suavizado'] = df_item['compra_sugerida'] - df_item['compra_bruta']
    df_item['valor_compra'] = df_item['compra_sugerida'] * df_item['costo']
    df_item['dias_inv_red_despues'] = np.where(
        df_item['consumo_red'] > 0,
        (df_item['inv_llegada_red'].fillna(0) + df_item['compra_sugerida']) / df_item['consumo_red'].replace(0, np.nan),
        np.nan)
    df_item['compra_vs_venta'] = np.where(df_item['venta_esperada'] > 0,
                                          df_item['compra_sugerida'] / df_item['venta_esperada'].replace(0, np.nan),
                                          np.nan)
    df_item['observacion'] = _observaciones(df_item, plan)

    # Métricas por tienda×ítem después de la compra.
    d = df['consumo_ajustado']
    df['inv_despues'] = df['inv_proy_llegada'] + df['compra']
    df['dias_inv_llegada'] = np.where(d > 0, df['inv_proy_llegada'] / d.replace(0, np.nan), np.nan)
    df['dias_inv_despues'] = np.where(d > 0, df['inv_despues'] / d.replace(0, np.nan), np.nan)
    df['riesgo_merma'] = df['con_datos'] & (df['inv_despues'] > 0) & (
        (d <= 0) | (df['vida_util'].notna() & (df['dias_inv_despues'] > df['vida_util'])))

    sin_costo = df_item[(df_item['compra_sugerida'] > 0) & df_item['costo'].isna()]
    if len(sin_costo) > 0:
        alertas.append({'tipo': 'ADVERTENCIA', 'item': '-',
                        'mensaje': f'{len(sin_costo)} ítem(s) con compra pero sin costo en Stock ni en Portafolio '
                                   f'({", ".join(str(i) for i in sin_costo["item_code"][:10])}): el valor de la compra '
                                   'no los incluye.'})
    protegidos = df_item[df_item['protegio_agotadas']]
    if len(protegidos) > 0:
        alertas.append({'tipo': 'ADVERTENCIA', 'item': '-',
                        'mensaje': f'{len(protegidos)} ítem(s) compraron más de lo que indicaba el suavizado para no '
                                   f'dejar tiendas agotadas ({", ".join(str(i) for i in protegidos["item_code"][:10])}).'})
    return ResultadoCompra(df.reset_index(drop=True), df_item, plan, params, alertas)


def _comprar_segun_frecuencia(g, item, params, plan):
    """Aplica la frecuencia de compra del ítem (DIA DE COMPRA) sobre `_comprar_item`.

    - todos / días fijos que incluyen hoy: compra normal (la cobertura ya viene por ítem).
    - días fijos que NO incluyen hoy: se calcula igual (para ver la necesidad) pero la compra es 0.
    - CADA N DIAS: se revisa a diario. Se activa cuando la red llega a la compra por debajo de su
      objetivo normal (Σ inventario proyectado < Σ objetivo); entonces compra N días de cobertura, sin
      suavizado (la compra periódica es grande a propósito). Si no se activa, compra 0.
    """
    regimen = item.get('regimen', 'todos')
    g_calc, resumen = _comprar_item(g, item, params, plan)
    if regimen == 'cada':
        n = int(item['n_dias_periodo'])
        activa = float(g_calc['inv_proy_llegada'].sum()) < float(g_calc['objetivo'].sum()) - 1e-9
        if activa:
            g_per = g.copy()
            g_per['objetivo'] = g_per['objetivo_periodo']
            g_per['venta_cobertura'] = g_per['venta_cobertura_periodo']
            g_per['stock_seguridad'] = g_per['stock_seguridad_periodo']
            g_calc, resumen = _comprar_item(g_per, item, replace(params, alfa_subida=1.0, alfa_bajada=1.0), plan)
            resumen['periodo_activado'] = True
            return g_calc, resumen
        texto = f'Cubierta hasta la próxima compra (cada {n} días)'
        g_calc['motivo'] = np.where(g_calc['compra'] > 0, texto, g_calc['motivo'])
        g_calc['compra'] = 0
        resumen.update(compra_sugerida=0, periodo_activado=False)
        return g_calc, resumen
    if not bool(item.get('se_compra_hoy', True)):
        sig = item.get('siguiente_compra_item')
        texto = f'No se compra hoy ({item.get("dia_compra")})'
        if sig is not None and not pd.isna(sig):
            texto += f'; próxima compra {sig:%d/%m}'
        g_calc['motivo'] = np.where(g_calc['necesidad'] > 1e-9, texto, g_calc['motivo'])
        g_calc['compra'] = 0
        resumen.update(compra_sugerida=0)
    resumen['periodo_activado'] = False
    return g_calc, resumen


def _observaciones(df_item, plan):
    obs = []
    for _, r in df_item.iterrows():
        notas = []
        if not r.get('en_celes', True):
            notas.append('Sin datos en Celes: ver «Sin Pronóstico»')
        elif r.get('n_tiendas', 0) == 0:
            notas.append('Sin tiendas activas en Tiendas × Ítem')
        if r.get('oc_sobrante', 0) > 0 and r.get('en_celes', True):
            notas.append(f'OC/stock en camino supera la necesidad en {int(r["oc_sobrante"])} caja(s)')
        if r.get('protegio_agotadas'):
            notas.append('Se compró extra para no dejar tiendas agotadas')
        if plan.capacidad_llegada < 1 and r.get('compra_sugerida', 0) > 0:
            notas.append(f'Llegada con recepción al {plan.capacidad_llegada:.0%}')
        regimen = r.get('regimen', 'todos')
        sig = r.get('siguiente_compra_item')
        if regimen == 'dias' and not r.get('se_compra_hoy', True) and r.get('en_celes', True):
            notas.append(f'Hoy no se compra ({r.get("dia_compra")})'
                         + (f': próxima compra {sig:%d/%m}' if sig is not None and not pd.isna(sig) else ''))
        if regimen == 'cada' and r.get('en_celes', True):
            n = int(r['n_dias_periodo']) if not pd.isna(r.get('n_dias_periodo')) else 0
            notas.append(f'Compra cada {n} días: ' + ('hoy se activa (cubre los próximos días hasta la siguiente)'
                                                       if r.get('periodo_activado') else 'hoy no hace falta'))
        obs.append(' · '.join(notas))
    return obs


def ejecutar_compra(dfs, df_distribucion, hojas_parametros=None, compras_transito=None, fecha=None,
                    overrides=None, alertas_maestros=None):
    """Pipeline completo de la compra: parámetros → plan (calendario) → datos → motor.

    dfs: dict de `loader.load_files` (los mismos DataFrames de la distribución).
    df_distribucion: `df_output` de `run_distribution` de hoy (o None).
    hojas_parametros / compras_transito: de `loader.load_inputs` / `load_purchase_files` (o None).
    fecha: date de los datos (por defecto hoy). overrides: dict de parámetros (app).
    alertas_maestros: avisos de calidad del libro de maestros (`loader.load_inputs`).
    """
    from datetime import date as _date

    params, alertas = cargar_parametros(hojas_parametros, overrides)
    plan = params.calendario().plan(fecha or _date.today())
    if not plan.es_dia_compra_hoy:
        alertas.append({'tipo': 'ADVERTENCIA', 'item': '-',
                        'mensaje': f'Hoy no es día de compra: se calculó la compra del '
                                   f'{plan.fecha_compra:%d/%m/%Y} (llega al CEDI el {plan.llegada_cedi:%d/%m/%Y}).'})
    if plan.capacidad_llegada < 1:
        alertas.append({'tipo': 'ADVERTENCIA', 'item': '-',
                        'mensaje': f'La compra llega al CEDI el {plan.llegada_cedi:%d/%m/%Y}, día con recepción al '
                                   f'{plan.capacidad_llegada:.0%}: se compra esa fracción de la cobertura (el resto se '
                                   'anticipó en la compra anterior con recepción completa).'})
    if plan.anticipaciones:
        dias = sum(len(a['dias']) for a in plan.anticipaciones)
        alertas.append({'tipo': 'ADVERTENCIA', 'item': '-',
                        'mensaje': f'Puente festivo: esta compra anticipa {sum(a["peso_anticipado"] for a in plan.anticipaciones):.1f} '
                                   f'día(s) de venta de {len(plan.anticipaciones)} llegada(s) con recepción reducida '
                                   f'({dias} día(s) de cobertura).'})
    df, items, alertas_pre, oc_detalle = build_purchase_df(dfs, df_distribucion, plan, params, compras_transito)
    res = run_compras(df, items, plan, params, alertas + list(alertas_maestros or []) + alertas_pre)
    res.oc_detalle = oc_detalle
    return res

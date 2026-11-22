"""Tests unitarios de preprocessor.py — normalización de claves y calidad de datos.

Los DataFrames de entrada se construyen a mano (no vía Excel real) porque
`build_distribution_df` opera sobre DataFrames ya cargados por loader.py —
la carga de archivos Excel se prueba por separado en test_loader.py.
"""

import pandas as pd
import pytest

from core.preprocessor import build_distribution_df, normalize_item, normalize_store


@pytest.mark.parametrize('valor,esperado', [
    ('0000155', 155),
    (155.0, 155),
    ('155', 155),
    (None, None),
    ('', None),
    ('ABC', None),
])
def test_normalize_item_casos_reales(valor, esperado):
    assert normalize_item(valor) == esperado


@pytest.mark.parametrize('valor,esperado', [
    ('BOS03', 'S03'),
    ('S03', 'S03'),
    (None, None),
])
def test_normalize_store_casos_reales(valor, esperado):
    assert normalize_store(valor) == esperado


def _dfs_base(**overrides):
    """dict de DataFrames mínimo y válido para build_distribution_df: una
    sola tienda (S01) y un solo ítem (155) elegible, sin grupo espejo. Cada
    override reemplaza uno de los DataFrames para forzar un escenario."""
    base = {
        'stock': pd.DataFrame({
            'Item': [155], 'Desc. item': ['Producto A'], 'Cant. disponible': [10.0],
            'U.M.': ['CJ20'], 'ESTADO DEL PRODUCTO': ['001 - ACTIVO'],
        }),
        'celes': pd.DataFrame({
            'Código de Bodega': ['BOS01'], 'Nombre de Bodega': ['Tienda Uno'],
            'Código de Producto': [155], 'Consumo Diario (Unidades de Distribución)': [1.0],
            '(=) Días de Inventario Actuales': [2.0],
            'Inventario Disponible en esta Tienda (Unidades de Distibución)': [2.0],
            '(-) inventario de traslado en proceso': [0.0],
            '(=) Inventario Total': [2.0], 'UM': ['CJ20'],
        }),
        'portafolio': pd.DataFrame({
            'ITEM': [155], 'CLUSTERIZACIÓN': ['A'], 'ESTADO': ['001 - ACTIVO'],
        }),
        'tiendas': pd.DataFrame({
            'COD SIESA': ['S01'], 'NOMBRE DE LA TIENDA': ['Tienda Uno'],
            'TIPO DE PORTAFOLIO': ['A'], 'ZONA': ['NORTE'],
        }),
        'tiendas_item': pd.DataFrame({
            'COD SIESA': ['S01'], 'TIPO DE PORTAFOLIO': ['A'], 'ITEM': [155],
        'ESTADO': ['001 - ACTIVO'], 'MAX': [0],
        }),
        'espejo': pd.DataFrame({'grupo_id': pd.Series(dtype='object'), 'item_id': pd.Series(dtype='object')}),
    }
    base.update(overrides)
    return base


def test_filtro_gran_total_elimina_fila_totalizadora():
    dfs = _dfs_base(stock=pd.DataFrame({
        'Item': [155, 'Gran total'], 'Desc. item': ['Producto A', ''],
        'Cant. disponible': [10.0, 999.0], 'U.M.': ['CJ20', 'CJ20'],
        'ESTADO DEL PRODUCTO': ['001 - ACTIVO', '001 - ACTIVO'],
    }))
    df_merged, _ = build_distribution_df(dfs)
    assert set(df_merged['item_code'].unique()) == {155}


def test_estado_producto_no_activo_no_bloquea_pero_genera_alerta():
    """Decisión de negocio: el ESTADO DEL PRODUCTO de Stock (ej. ítem 1815,
    '005 - PRODUCTO PRUEBA') NO debe bloquear la distribución — si hay
    existencia física en el CEDI, se distribuye igual — pero sí debe generar
    una alerta informativa para que el negocio pueda revisarlo."""
    dfs = _dfs_base(stock=pd.DataFrame({
        'Item': [155], 'Desc. item': ['Producto de prueba'], 'Cant. disponible': [10.0],
        'U.M.': ['CJ20'], 'ESTADO DEL PRODUCTO': ['005 - PRODUCTO PRUEBA'],
    }))
    df_merged, alertas = build_distribution_df(dfs)
    assert len(df_merged) == 1, "El ítem debía distribuirse pese a no estar ACTIVO"
    assert int(df_merged.loc[0, 'cajas_disponibles_cedi']) == 10

    adv = [a for a in alertas if a['item'] == 155 and a.get('estado_producto')]
    assert len(adv) == 1, "Debía generarse una alerta informativa sobre el estado"
    assert adv[0]['tipo'] == 'ADVERTENCIA'
    assert '005 - PRODUCTO PRUEBA' in adv[0]['mensaje']


def test_dedup_stock_no_infla_merge():
    dfs = _dfs_base(stock=pd.DataFrame({
        'Item': [155, '0000155'], 'Desc. item': ['Producto A', 'Producto A (dup)'],
        'Cant. disponible': [10.0, 10.0], 'U.M.': ['CJ20', 'CJ20'],
        'ESTADO DEL PRODUCTO': ['001 - ACTIVO', '001 - ACTIVO'],
    }))
    df_merged, _ = build_distribution_df(dfs)
    assert len(df_merged) == 1


def test_dedup_tiendas_item_no_infla_merge():
    dfs = _dfs_base(tiendas_item=pd.DataFrame({
        'COD SIESA': ['S01', 'S01'], 'TIPO DE PORTAFOLIO': ['A', 'A'],
        'ITEM': [155, 155], 'ESTADO': ['001 - ACTIVO', '001 - ACTIVO'], 'MAX': [0, 0],
    }))
    df_merged, _ = build_distribution_df(dfs)
    assert len(df_merged) == 1


def test_store_name_sale_de_db_tiendas_cuando_no_hay_match_en_celes():
    """Bug de auditoría: 43% de filas del Excel final quedaban con Nombre
    Tienda vacío porque store_name solo salía de Celes. Debe salir de
    DB_Tiendas.NOMBRE DE LA TIENDA cuando la tienda-ítem no matchea en Celes."""
    dfs = _dfs_base(celes=pd.DataFrame({
        'Código de Bodega': pd.Series(dtype='object'), 'Nombre de Bodega': pd.Series(dtype='object'),
        'Código de Producto': pd.Series(dtype='object'),
        'Consumo Diario (Unidades de Distribución)': pd.Series(dtype='float64'),
        '(=) Días de Inventario Actuales': pd.Series(dtype='float64'),
        'Inventario Disponible en esta Tienda (Unidades de Distibución)': pd.Series(dtype='float64'),
        '(-) inventario de traslado en proceso': pd.Series(dtype='float64'),
        '(=) Inventario Total': pd.Series(dtype='float64'),
        'UM': pd.Series(dtype='object'),
    }))
    df_merged, _ = build_distribution_df(dfs)
    assert len(df_merged) == 1
    assert df_merged.loc[0, 'store_name'] == 'Tienda Uno'
    assert bool(df_merged.loc[0, 'sin_match_celes']) is True


def test_store_name_unico_aunque_celes_use_otro_nombre_que_db_tiendas():
    """Bug real de producción (tienda SE4): Celes nombra la bodega 'ISIMO LEON
    XIII' mientras DB_Tiendas la nombra 'LEON XIII'. Antes store_name salía de
    Celes cuando había match, lo que partía una misma tienda en dos filas en
    la hoja Resumen del exporter (agrupa por nombre además de por código).
    DB_Tiendas debe ser la única fuente de store_name, con o sin match en
    Celes, para que el nombre sea siempre el mismo para un store_code dado."""
    dfs = _dfs_base(celes=pd.DataFrame({
        'Código de Bodega': ['BOS01'], 'Nombre de Bodega': ['ISIMO NOMBRE DISTINTO'],
        'Código de Producto': [155],
        'Consumo Diario (Unidades de Distribución)': [1.0],
        '(=) Días de Inventario Actuales': [5.0],
        'Inventario Disponible en esta Tienda (Unidades de Distibución)': [5.0],
        '(-) inventario de traslado en proceso': [0.0],
        '(=) Inventario Total': [5.0],
        'UM': ['CJ20'],
    }))
    df_merged, _ = build_distribution_df(dfs)
    assert len(df_merged) == 1
    assert df_merged.loc[0, 'store_name'] == 'Tienda Uno'
    assert bool(df_merged.loc[0, 'sin_match_celes']) is False


def test_items_huerfanos_generan_alerta_critica_con_cajas_perdidas():
    """Ítem con stock en CEDI pero sin ninguna tienda elegible en
    Tiendas×Ítem — antes se perdía en silencio, violando la garantía de
    cero residuo sin que el algoritmo llegara siquiera a verlo."""
    dfs = _dfs_base(tiendas_item=pd.DataFrame({
        'COD SIESA': pd.Series(dtype='object'), 'TIPO DE PORTAFOLIO': pd.Series(dtype='object'),
        'ITEM': pd.Series(dtype='object'), 'ESTADO': pd.Series(dtype='object'),
        'MAX': pd.Series(dtype='float'),
    }))
    df_merged, alertas = build_distribution_df(dfs)
    assert len(df_merged) == 0
    criticos = [a for a in alertas if a['tipo'] == 'CRÍTICO' and a['item'] == 155]
    assert len(criticos) == 1
    assert criticos[0]['cajas_sin_distribuir'] == 10


def test_filas_con_item_key_no_convertible_generan_alerta_de_conteo():
    dfs = _dfs_base(tiendas_item=pd.DataFrame({
        'COD SIESA': ['S01', 'S01'], 'TIPO DE PORTAFOLIO': ['A', 'A'],
        'ITEM': [155, 'ABC'], 'ESTADO': ['001 - ACTIVO', '001 - ACTIVO'], 'MAX': [0, 0],
    }))
    df_merged, alertas = build_distribution_df(dfs)
    adv = [a for a in alertas if a.get('archivo') == 'Tiendas × Ítem']
    assert len(adv) == 1
    assert adv[0]['filas_descartadas'] == 1


def test_clip_lower_zero_defensivo_ante_inventario_negativo():
    """Un valor negativo de inventario (no debería ocurrir, pero no hay
    garantía externa) no debe propagarse como inventario_efectivo negativo
    aguas abajo — ver Bug #1 de la auditoría (pesos negativos en Fase 4)."""
    dfs = _dfs_base(celes=pd.DataFrame({
        'Código de Bodega': ['BOS01'], 'Nombre de Bodega': ['Tienda Uno'],
        'Código de Producto': [155], 'Consumo Diario (Unidades de Distribución)': [1.0],
        '(=) Días de Inventario Actuales': [2.0],
        'Inventario Disponible en esta Tienda (Unidades de Distibución)': [-5.0],
        '(-) inventario de traslado en proceso': [0.0],
        '(=) Inventario Total': [-5.0], 'UM': ['CJ20'],
    }))
    df_merged, _ = build_distribution_df(dfs)
    assert df_merged.loc[0, 'inventario_tienda'] >= 0
    assert df_merged.loc[0, 'inventario_total'] >= 0
    assert df_merged.loc[0, 'inventario_efectivo'] >= 0


def test_inventario_efectivo_usa_existencias_mas_transito_no_inventario_total():
    """Caso real de producción (tienda Campín, ítem 1210): '(=) Inventario
    Total' de Celes puede venir inflado frente a Existencias + Tránsito (9.33
    vs. 1.33 + 2 = 3.33). El inventario efectivo debe calcularse como
    Existencias + Tránsito y no tomar '(=) Inventario Total' directamente —
    de lo contrario el algoritmo subestima la necesidad real de la tienda."""
    dfs = _dfs_base(celes=pd.DataFrame({
        'Código de Bodega': ['BOS01'], 'Nombre de Bodega': ['Tienda Uno'],
        'Código de Producto': [155], 'Consumo Diario (Unidades de Distribución)': [1.0],
        '(=) Días de Inventario Actuales': [2.0],
        'Inventario Disponible en esta Tienda (Unidades de Distibución)': [1.33],
        '(-) inventario de traslado en proceso': [2.0],
        '(=) Inventario Total': [9.33], 'UM': ['CJ20'],
    }))
    df_merged, _ = build_distribution_df(dfs)
    assert df_merged.loc[0, 'inventario_efectivo'] == pytest.approx(3.33)


def test_grupo_espejo_sin_entrada_colapsa_a_grupo_tamano_1():
    dfs = _dfs_base()
    df_merged, _ = build_distribution_df(dfs)
    assert df_merged.loc[0, 'grupo_id'] == 'ITEM_155'
    assert df_merged.loc[0, 'item_share_consumo'] == 1.0


def test_item_no_catalogado_en_portafolio_genera_alerta():
    """Validación cruzada con Portafolio (pedida por el usuario): un ítem con
    stock en CEDI que no aparece en absoluto en Portafolio Fruver debe
    generar una alerta de posible ítem nuevo sin catalogar."""
    dfs = _dfs_base(portafolio=pd.DataFrame({
        'ITEM': pd.Series(dtype='object'), 'CLUSTERIZACIÓN': pd.Series(dtype='object'),
        'ESTADO': pd.Series(dtype='object'),
    }))
    df_merged, alertas = build_distribution_df(dfs)
    adv = [a for a in alertas if a['item'] == 155 and 'no está catalogado' in a['mensaje']]
    assert len(adv) == 1


def test_item_descontinuado_en_portafolio_con_stock_genera_alerta():
    """Validación cruzada con Portafolio: un ítem marcado como descontinuado
    (ESTADO sin 'ACTIVO') que aún tiene stock en CEDI debe generar alerta."""
    dfs = _dfs_base(portafolio=pd.DataFrame({
        'ITEM': [155], 'CLUSTERIZACIÓN': ['A'], 'ESTADO': ['002 - INACTIVO'],
    }))
    df_merged, alertas = build_distribution_df(dfs)
    adv = [a for a in alertas if a['item'] == 155 and 'descontinuado' in a['mensaje']]
    assert len(adv) == 1


def test_item_catalogado_y_activo_no_genera_alertas_de_portafolio():
    dfs = _dfs_base()
    _, alertas = build_distribution_df(dfs)
    assert alertas == []


# ---------------------------------------------------------------------------
# Tope MAX por tienda × ítem
# ---------------------------------------------------------------------------

def _dfs_max(max_valor, existencias, transito=0.0):
    """Escenario de un solo tienda-ítem con un MAX y unas existencias dadas."""
    return _dfs_base(
        tiendas_item=pd.DataFrame({
            'COD SIESA': ['S01'], 'TIPO DE PORTAFOLIO': ['A'], 'ITEM': [155],
            'ESTADO': ['001 - ACTIVO'], 'MAX': [max_valor],
        }),
        celes=pd.DataFrame({
            'Código de Bodega': ['BOS01'], 'Nombre de Bodega': ['Tienda Uno'],
            'Código de Producto': [155],
            'Consumo Diario (Unidades de Distribución)': [1.0],
            '(=) Días de Inventario Actuales': [1.0],
            'Inventario Disponible en esta Tienda (Unidades de Distibución)': [existencias],
            '(-) inventario de traslado en proceso': [transito],
            '(=) Inventario Total': [existencias + transito], 'UM': ['CJ20'],
        }),
    )


@pytest.mark.parametrize('max_valor,existencias,esperado', [
    (6, 5.9, False),   # justo por debajo del tope: sigue recibiendo
    (6, 6.0, True),    # alcanzó el MAX exacto: bloqueada (corte es >=)
    (6, 6.1, True),    # por encima: bloqueada
    (6, 0.0, False),   # vacía: recibe
    (0, 999.0, False),  # MAX 0 = sin tope definido, nunca bloquea
])
def test_bloqueo_por_max_usa_corte_mayor_o_igual(max_valor, existencias, esperado):
    df_merged, _ = build_distribution_df(_dfs_max(max_valor, existencias))
    assert len(df_merged) == 1
    assert bool(df_merged['bloqueado_por_max'].iloc[0]) is esperado


@pytest.mark.parametrize('max_valor,existencias,transito,esperado', [
    (2, 1.17, 3.0, True),    # caso S53/ciruela 1797: 4.17 >= 2, bloqueada
    (2, 1.17, 0.0, False),   # sin tránsito: 1.17 < 2, recibe normal
    (2, 1.0, 1.0, True),     # justo en el MAX por suma: el corte es >=
    (2, 0.5, 1.0, False),    # 1.5 < 2: la suma todavía no llega al MAX
    (0, 5.0, 5.0, False),    # MAX 0 = sin tope definido, nunca bloquea
])
def test_max_cuenta_existencias_mas_transito(max_valor, existencias, transito, esperado):
    df_merged, _ = build_distribution_df(_dfs_max(max_valor, existencias, transito=transito))
    assert bool(df_merged['bloqueado_por_max'].iloc[0]) is esperado


def test_max_se_compara_contra_inventario_efectivo_no_solo_existencias():
    """Antes (caso S96/1210) Existencias 5.07 + tránsito 3 = 8.07 con MAX 6
    seguía recibiendo porque 5.07 < 6. Desde nov-2026 el tránsito cuenta: el
    inventario efectivo (8.07) supera el MAX y la fila queda bloqueada."""
    df_merged, _ = build_distribution_df(_dfs_max(6, 5.07, transito=3.0))
    fila = df_merged.iloc[0]
    assert fila['inventario_efectivo'] == pytest.approx(8.07)
    assert bool(fila['bloqueado_por_max']) is True
    assert bool(fila['excluida_del_reparto']) is True


def test_max_vacio_o_nan_se_trata_como_sin_tope():
    df_merged, _ = build_distribution_df(_dfs_max(None, 999.0))
    assert df_merged['max_existencias'].iloc[0] == 0
    assert bool(df_merged['bloqueado_por_max'].iloc[0]) is False


def test_bloqueo_por_max_genera_alerta_agregada():
    _, alertas = build_distribution_df(_dfs_max(6, 9.0))
    adv = [a for a in alertas if a.get('combos_bloqueados')]
    assert len(adv) == 1
    assert adv[0]['combos_bloqueados'] == 1
    assert adv[0]['tipo'] == 'ADVERTENCIA'


def _celes_vacio():
    """Celes sin ninguna fila: fuerza el escenario de sin match para todas las
    combinaciones elegibles."""
    return pd.DataFrame({
        'Código de Bodega': pd.Series(dtype='object'),
        'Nombre de Bodega': pd.Series(dtype='object'),
        'Código de Producto': pd.Series(dtype='object'),
        'Consumo Diario (Unidades de Distribución)': pd.Series(dtype='float'),
        '(=) Días de Inventario Actuales': pd.Series(dtype='float'),
        'Inventario Disponible en esta Tienda (Unidades de Distibución)': pd.Series(dtype='float'),
        '(-) inventario de traslado en proceso': pd.Series(dtype='float'),
        '(=) Inventario Total': pd.Series(dtype='float'),
        'UM': pd.Series(dtype='object'),
    })


def test_sin_match_en_celes_no_se_bloquea_por_max():
    """Sin datos de Celes las existencias quedan en 0 — no se debe asumir
    saturación y bloquear por MAX (queda excluida por la otra causa, ver
    `test_sin_match_en_celes_queda_excluida_del_reparto`)."""
    dfs = _dfs_base(
        tiendas_item=pd.DataFrame({
            'COD SIESA': ['S01'], 'TIPO DE PORTAFOLIO': ['A'], 'ITEM': [155],
            'ESTADO': ['001 - ACTIVO'], 'MAX': [6],
        }),
        celes=_celes_vacio(),
    )
    df_merged, _ = build_distribution_df(dfs)
    assert bool(df_merged['bloqueado_por_max'].iloc[0]) is False


# ---------------------------------------------------------------------------
# Exclusión del reparto: unión de tope MAX + sin datos en Celes
# ---------------------------------------------------------------------------

def test_sin_match_en_celes_queda_excluida_del_reparto():
    """Aunque no la bloquee el MAX, una combinación sin datos en Celes debe
    quedar fuera del reparto: llega con consumo=0/inventario=0 y sin esto se
    clasificaría como AGOTADA, recibiendo cajas porque falta el dato."""
    df_merged, _ = build_distribution_df(_dfs_base(celes=_celes_vacio()))
    fila = df_merged.iloc[0]
    assert bool(fila['sin_match_celes']) is True
    assert bool(fila['bloqueado_por_max']) is False
    assert bool(fila['excluida_del_reparto']) is True


def test_excluida_del_reparto_cubre_el_bloqueo_por_max():
    df_merged, _ = build_distribution_df(_dfs_max(6, 9.0))
    fila = df_merged.iloc[0]
    assert bool(fila['sin_match_celes']) is False
    assert bool(fila['bloqueado_por_max']) is True
    assert bool(fila['excluida_del_reparto']) is True


def test_combinacion_normal_no_queda_excluida():
    df_merged, _ = build_distribution_df(_dfs_base())
    assert bool(df_merged['excluida_del_reparto'].iloc[0]) is False


# ---------------------------------------------------------------------------
# Causa del sin-match (para la hoja "Novedades")
# ---------------------------------------------------------------------------

def _celes_una_fila(cod_bodega, cod_producto):
    return pd.DataFrame({
        'Código de Bodega': [cod_bodega], 'Nombre de Bodega': ['Otra Tienda'],
        'Código de Producto': [cod_producto],
        'Consumo Diario (Unidades de Distribución)': [1.0],
        '(=) Días de Inventario Actuales': [1.0],
        'Inventario Disponible en esta Tienda (Unidades de Distibución)': [1.0],
        '(-) inventario de traslado en proceso': [0.0],
        '(=) Inventario Total': [1.0], 'UM': ['CJ20'],
    })


def test_causa_sin_match_tienda_ausente_de_celes():
    """El ítem 155 sí está en Celes, pero para otra tienda (S99): lo que falta
    es la tienda S01."""
    df_merged, _ = build_distribution_df(_dfs_base(celes=_celes_una_fila('BOS99', 155)))
    assert df_merged['causa_sin_match'].iloc[0] == 'Tienda ausente de Celes'


def test_causa_sin_match_item_ausente_de_celes():
    """La tienda S01 sí está en Celes, pero con otro ítem (999)."""
    df_merged, _ = build_distribution_df(_dfs_base(celes=_celes_una_fila('BOS01', 999)))
    assert df_merged['causa_sin_match'].iloc[0] == 'Ítem ausente de todo Celes'


def test_causa_sin_match_par_faltante():
    """Tienda e ítem existen en Celes por separado, pero no la combinación:
    dos filas cruzadas (S01/999 y S99/155) dejan S01/155 sin match."""
    celes = pd.concat([
        _celes_una_fila('BOS01', 999),
        _celes_una_fila('BOS99', 155),
    ], ignore_index=True)
    df_merged, _ = build_distribution_df(_dfs_base(celes=celes))
    assert df_merged['causa_sin_match'].iloc[0] == 'Par tienda-ítem faltante'


def test_causa_sin_match_vacia_cuando_si_hay_match():
    df_merged, _ = build_distribution_df(_dfs_base())
    assert df_merged['causa_sin_match'].iloc[0] == ''


# ── Auditoría 360 (2026-11-12): duplicados y cantidades fraccionarias ──────────

def test_stock_repetido_suma_cajas_y_alerta():
    """Un ítem repetido en Stock suma sus cajas (antes se perdía la 2ª fila)."""
    dfs = _dfs_base(stock=pd.DataFrame({
        'Item': [155, '0000155'], 'Desc. item': ['Producto A', 'Producto A (dup)'],
        'Cant. disponible': [17.0, 5.0], 'U.M.': ['CJ20', 'CJ20'],
        'ESTADO DEL PRODUCTO': ['001 - ACTIVO', '001 - ACTIVO'],
    }))
    df_merged, alertas = build_distribution_df(dfs)
    assert len(df_merged) == 1
    assert int(df_merged.loc[0, 'cajas_disponibles_cedi']) == 22
    rep = [a for a in alertas if a.get('filas_repetidas') and a['item'] == 155]
    assert len(rep) == 1 and rep[0]['cajas_en_cedi'] == 22


def test_stock_fraccionario_alerta_cajas_descartadas():
    dfs = _dfs_base(stock=pd.DataFrame({
        'Item': [155], 'Desc. item': ['Producto A'], 'Cant. disponible': [4.9],
        'U.M.': ['CJ20'], 'ESTADO DEL PRODUCTO': ['001 - ACTIVO'],
    }))
    df_merged, alertas = build_distribution_df(dfs)
    assert int(df_merged.loc[0, 'cajas_disponibles_cedi']) == 4
    fr = [a for a in alertas if a.get('filas_fraccionarias')]
    assert len(fr) == 1 and fr[0]['cajas_descartadas'] == pytest.approx(0.9)


def test_tiendas_item_repetido_usa_max_mas_restrictivo():
    """Par repetido con MAX distintos: prevalece el menor MAX > 0, sin importar
    el orden de las filas, y se alerta."""
    for maxes in ([8, 5], [5, 8], [0, 5], [5, 0]):
        dfs = _dfs_base(tiendas_item=pd.DataFrame({
            'COD SIESA': ['S01', 'S01'], 'TIPO DE PORTAFOLIO': ['A', 'A'], 'ITEM': [155, 155],
            'ESTADO': ['001 - ACTIVO', '001 - ACTIVO'], 'MAX': maxes,
        }))
        df_merged, alertas = build_distribution_df(dfs)
        assert len(df_merged) == 1
        assert df_merged.loc[0, 'max_existencias'] == 5, maxes
        assert any(a.get('pares_repetidos') == 1 for a in alertas)


def test_tiendas_item_repetido_sin_max_queda_sin_tope():
    dfs = _dfs_base(tiendas_item=pd.DataFrame({
        'COD SIESA': ['S01', 'S01'], 'TIPO DE PORTAFOLIO': ['A', 'A'], 'ITEM': [155, 155],
        'ESTADO': ['001 - ACTIVO', '001 - ACTIVO'], 'MAX': [0, 0],
    }))
    df_merged, _ = build_distribution_df(dfs)
    assert df_merged.loc[0, 'max_existencias'] == 0
    assert not df_merged.loc[0, 'bloqueado_por_max']


def test_tienda_repetida_en_maestro_no_duplica_filas():
    dfs = _dfs_base(tiendas=pd.DataFrame({
        'COD SIESA': ['S01', 'S01'], 'NOMBRE DE LA TIENDA': ['Tienda Uno', 'Tienda Uno'],
        'TIPO DE PORTAFOLIO': ['A', 'A'], 'ZONA': ['NORTE', 'NORTE'],
    }))
    df_merged, alertas = build_distribution_df(dfs)
    assert len(df_merged) == 1
    assert any('DB_Tiendas' in a['mensaje'] for a in alertas)


# ── Fase 3: insumos de renovación por vida útil (Inventario Tiendas + VIDA UTIL EXHIBICION) ─

def _dfs_renovacion(fechas, vida_util=5, **overrides):
    """_dfs_base + Inventario Tiendas + VIDA UTIL EXHIBICION. `fechas` es una lista de
    (C.O. bodega, ítem, fecha) del extracto; la fecha MÁS RECIENTE del archivo
    es la fecha de corte contra la que se cuentan los días."""
    base = _dfs_base()
    base['portafolio'] = pd.DataFrame({
        'ITEM': [155], 'CLUSTERIZACIÓN': ['A'], 'ESTADO': ['001 - ACTIVO'], 'VIDA UTIL EXHIBICION': [vida_util],
    })
    base['inventario_tiendas'] = pd.DataFrame({
        'Item': [f[1] for f in fechas],
        'C.O. bodega': [f[0] for f in fechas],
        'Fecha última entrada': [pd.Timestamp(f[2]) if f[2] else pd.NaT for f in fechas],
    })
    base.update(overrides)
    return base


def test_renovacion_calcula_dias_contra_la_fecha_de_corte_del_archivo():
    dfs = _dfs_renovacion([('S01', 155, '2026-11-04'), ('S99', 999, '2026-11-12')])
    df_merged, _ = build_distribution_df(dfs)
    fila = df_merged.iloc[0]
    assert fila['dias_desde_ingreso'] == 8
    assert fila['vida_util_portafolio'] == 5
    assert fila['fecha_ultimo_ingreso'] == pd.Timestamp('2026-11-04')
    assert fila['venta_esperada_vida_util'] == pytest.approx(1.0 * 5)   # consumo 1.0 × vida útil


def test_renovacion_vida_util_vacia_o_cero_se_omite():
    for vu in (None, 0):
        df_merged, _ = build_distribution_df(
            _dfs_renovacion([('S01', 155, '2026-11-04'), ('S99', 999, '2026-11-12')], vida_util=vu))
        assert pd.isna(df_merged.iloc[0]['vida_util_portafolio']), vu
        assert pd.isna(df_merged.iloc[0]['venta_esperada_vida_util']), vu


def test_renovacion_varias_sub_bodegas_toman_el_ingreso_mas_reciente():
    dfs = _dfs_renovacion([('S01', 155, '2026-10-01'), ('S01', 155, '2026-11-10'),
                           ('S99', 999, '2026-11-12')])
    df_merged, _ = build_distribution_df(dfs)
    assert df_merged.iloc[0]['dias_desde_ingreso'] == 2


def test_renovacion_sin_fecha_de_ingreso_deja_dias_vacios_y_alerta():
    dfs = _dfs_renovacion([('S01', 155, None), ('S99', 999, '2026-11-12')])
    df_merged, alertas = build_distribution_df(dfs)
    assert pd.isna(df_merged.iloc[0]['dias_desde_ingreso'])
    assert any(a.get('combos_sin_fecha_ingreso') == 1 for a in alertas)


def test_renovacion_alerta_si_el_inventario_tiendas_esta_desactualizado():
    dfs = _dfs_renovacion([('S01', 155, '2020-01-01')])
    _, alertas = build_distribution_df(dfs)
    assert any('desactualizado' in a['mensaje'] for a in alertas)


def test_renovacion_sin_inventario_tiendas_no_rompe_y_deja_columnas_vacias():
    df_merged, _ = build_distribution_df(_dfs_base())
    assert pd.isna(df_merged.iloc[0]['dias_desde_ingreso'])
    assert not df_merged.iloc[0]['grupo_con_ingreso_fresco']


def test_renovacion_marca_ingreso_fresco_si_un_sku_espejo_entro_recientemente():
    dfs = _dfs_base()
    dfs['stock'] = pd.DataFrame({
        'Item': [155, 156], 'Desc. item': ['A', 'B'], 'Cant. disponible': [10.0, 10.0],
        'U.M.': ['CJ20', 'CJ20'], 'ESTADO DEL PRODUCTO': ['001 - ACTIVO'] * 2,
    })
    dfs['celes'] = pd.concat([dfs['celes'], dfs['celes'].assign(**{'Código de Producto': 156})],
                             ignore_index=True)
    dfs['portafolio'] = pd.DataFrame({
        'ITEM': [155, 156], 'CLUSTERIZACIÓN': ['A', 'A'], 'ESTADO': ['001 - ACTIVO'] * 2,
        'VIDA UTIL EXHIBICION': [5, 5],
    })
    dfs['tiendas_item'] = pd.DataFrame({
        'COD SIESA': ['S01', 'S01'], 'TIPO DE PORTAFOLIO': ['A', 'A'], 'ITEM': [155, 156],
        'ESTADO': ['001 - ACTIVO'] * 2, 'MAX': [0, 0],
    })
    dfs['espejo'] = pd.DataFrame({'grupo_id': ['G', 'G'], 'item_id': [155, 156]})
    dfs['inventario_tiendas'] = pd.DataFrame({
        'Item': [155, 156], 'C.O. bodega': ['S01', 'S01'],
        'Fecha última entrada': [pd.Timestamp('2026-11-01'), pd.Timestamp('2026-11-11')],
    })
    df_merged, _ = build_distribution_df(dfs)
    assert df_merged['grupo_con_ingreso_fresco'].all()   # 156 entró hace 1 día (≤ 5)


# ── Portafolio nuevo: VIDA UTIL EXHIBICION / DIAS CONSERVACION CEDI / TOTAL VIDA UTIL ──

def _dfs_portafolio(**cols):
    return _dfs_renovacion([('S01', 155, '2026-11-04'), ('S99', 999, '2026-11-12')],
                           portafolio=pd.DataFrame({
                               'ITEM': [155], 'CLUSTERIZACIÓN': ['A'], 'ESTADO': ['001 - ACTIVO'], **cols}))


def test_vida_util_total_y_cedi_quedan_en_df_merged():
    dfs = _dfs_portafolio(**{'VIDA UTIL EXHIBICION': [5], 'DIAS CONSERVACION CEDI': [3], 'TOTAL VIDA UTIL': [8]})
    f = build_distribution_df(dfs)[0].iloc[0]
    assert (f['vida_util_portafolio'], f['vida_util_cedi'], f['vida_util_total']) == (5, 3, 8)


def test_la_fase_3_usa_solo_vida_util_exhibicion():
    """DIAS CONSERVACION CEDI y TOTAL no alteran los días que se comparan con el ingreso."""
    dfs = _dfs_portafolio(**{'VIDA UTIL EXHIBICION': [5], 'DIAS CONSERVACION CEDI': [30], 'TOTAL VIDA UTIL': [35]})
    f = build_distribution_df(dfs)[0].iloc[0]
    assert f['vida_util_portafolio'] == 5 and f['venta_esperada_vida_util'] == pytest.approx(1.0 * 5)


def test_vida_util_total_se_reconstruye_si_la_formula_llega_vacia():
    dfs = _dfs_portafolio(**{'VIDA UTIL EXHIBICION': [5], 'DIAS CONSERVACION CEDI': [3], 'TOTAL VIDA UTIL': [float('nan')]})
    df_merged, alertas = build_distribution_df(dfs)
    assert df_merged.iloc[0]['vida_util_total'] == 8
    assert not any('TOTAL VIDA UTIL' in a['mensaje'] for a in alertas)


def test_alerta_si_total_no_cuadra_con_exhibicion_mas_cedi():
    dfs = _dfs_portafolio(**{'VIDA UTIL EXHIBICION': [5], 'DIAS CONSERVACION CEDI': [3], 'TOTAL VIDA UTIL': [20]})
    _, alertas = build_distribution_df(dfs)
    assert any('TOTAL VIDA UTIL distinto' in a['mensaje'] and a['items_afectados'] == [155] for a in alertas)


def test_alerta_por_vida_util_negativa_o_no_numerica():
    dfs = _dfs_portafolio(**{'VIDA UTIL EXHIBICION': ['abc'], 'DIAS CONSERVACION CEDI': [-2]})
    df_merged, alertas = build_distribution_df(dfs)
    assert pd.isna(df_merged.iloc[0]['vida_util_portafolio'])          # no numérica = vacía
    assert any('negativos' in a['mensaje'] for a in alertas)
    assert any(a.get('celdas_no_numericas') == 1 for a in alertas)


def test_sin_columnas_opcionales_el_preprocesador_no_se_rompe():
    df_merged, _ = build_distribution_df(_dfs_portafolio(**{'VIDA UTIL EXHIBICION': [5]}))
    f = df_merged.iloc[0]
    assert f['vida_util_portafolio'] == 5 and pd.isna(f['vida_util_cedi']) and pd.isna(f['vida_util_total'])


def test_limite_de_la_regla_de_extremo_a_extremo_vida_util_5():
    """Con vida útil 5 y fecha de corte 2026-11-12: ingreso el 11-08 (4 días) y el 11-07 (5 días) no
    renuevan; el 11-06 (6 días) sí. Recorre preprocessor + algoritmo completos."""
    from core.algorithm import run_distribution
    resultados = {}
    for ingreso in ('2026-11-08', '2026-11-07', '2026-11-06'):
        dfs = _dfs_renovacion([('S01', 155, ingreso), ('S99', 999, '2026-11-12')])
        dfs['celes'] = dfs['celes'].assign(**{
            'Consumo Diario (Unidades de Distribución)': 0.3,
            'Inventario Disponible en esta Tienda (Unidades de Distibución)': 1.5,
            '(=) Días de Inventario Actuales': 5.0, '(=) Inventario Total': 1.5,
        })
        df_merged, _ = build_distribution_df(dfs)
        out, _ = run_distribution(df_merged)
        resultados[ingreso] = (int(df_merged.iloc[0]['dias_desde_ingreso']), int(out['cajas_fase3'].sum()))
    assert resultados == {'2026-11-08': (4, 0), '2026-11-07': (5, 0), '2026-11-06': (6, 1)}, resultados

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
    aguas abajo — ver Bug #1 de la auditoría (pesos negativos en Fase 3)."""
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


def test_max_se_compara_contra_existencias_no_contra_inventario_efectivo():
    """Caso real S96/1210: Existencias 5.07 + tránsito 3 = 8.07 con MAX 6.
    Debe seguir recibiendo — el tránsito ya va en camino y no bloquea, aunque
    el inventario efectivo (8.07) supere el MAX."""
    df_merged, _ = build_distribution_df(_dfs_max(6, 5.07, transito=3.0))
    fila = df_merged.iloc[0]
    assert fila['inventario_efectivo'] == pytest.approx(8.07)
    assert bool(fila['bloqueado_por_max']) is False


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


def test_sin_match_en_celes_no_se_bloquea_por_max():
    """Sin datos de Celes las existencias quedan en 0 — no se debe asumir
    saturación y bloquear el envío."""
    dfs = _dfs_base(
        tiendas_item=pd.DataFrame({
            'COD SIESA': ['S01'], 'TIPO DE PORTAFOLIO': ['A'], 'ITEM': [155],
            'ESTADO': ['001 - ACTIVO'], 'MAX': [6],
        }),
        celes=pd.DataFrame({
            'Código de Bodega': pd.Series(dtype='object'),
            'Nombre de Bodega': pd.Series(dtype='object'),
            'Código de Producto': pd.Series(dtype='object'),
            'Consumo Diario (Unidades de Distribución)': pd.Series(dtype='float'),
            '(=) Días de Inventario Actuales': pd.Series(dtype='float'),
            'Inventario Disponible en esta Tienda (Unidades de Distibución)': pd.Series(dtype='float'),
            '(-) inventario de traslado en proceso': pd.Series(dtype='float'),
            '(=) Inventario Total': pd.Series(dtype='float'),
            'UM': pd.Series(dtype='object'),
        }),
    )
    df_merged, _ = build_distribution_df(dfs)
    assert bool(df_merged['bloqueado_por_max'].iloc[0]) is False

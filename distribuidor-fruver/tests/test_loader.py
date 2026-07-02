"""Tests unitarios de loader.py — carga y validación de los archivos de entrada."""

import io

import pandas as pd
import pytest

from core.loader import LABELS, REQUIRED_COLUMNS, load_files


def _excel_bytes(data, sheet_name='Sheet1'):
    buf = io.BytesIO()
    pd.DataFrame(data).to_excel(buf, index=False, sheet_name=sheet_name)
    buf.seek(0)
    return buf


def _archivos_validos(**overrides):
    """Set completo de 6 archivos válidos (BytesIO) con una sola tienda/ítem.
    Cada override reemplaza uno por clave para forzar un escenario de error."""
    archivos = {
        'stock': _excel_bytes({
            'Item': ['155'], 'Desc. item': ['Prod A'], 'Cant. disponible': [10],
            'Factor U.M.': [1], 'U.M.': ['CJ20'], 'ESTADO DEL PRODUCTO': ['001 - ACTIVO'],
        }),
        'celes': _excel_bytes({
            'Código de Bodega': ['BOS03'], 'Nombre de Bodega': ['Tienda 3'],
            'Código de Producto': [155], 'Consumo Diario (Unidades de Distribución)': [1.0],
            '(=) Días de Inventario Actuales': [2.0],
            'Inventario Disponible en esta Tienda (Unidades de Distibución)': [1.0],
            '(-) inventario de traslado en proceso': [0.0], 'UM': ['CJ20'],
        }, sheet_name='Items'),
        'portafolio': _excel_bytes({
            'ITEM': [155], 'CLUSTERIZACIÓN': ['A'], 'ESTADO': ['001 - ACTIVO'],
        }),
        'tiendas': _excel_bytes({
            'COD SIESA': ['S03'], 'NOMBRE DE LA TIENDA': ['Tienda 3'],
            'TIPO DE PORTAFOLIO': ['A'], 'ZONA': ['NORTE'],
        }),
        'tiendas_item': _excel_bytes({
            'COD SIESA': ['S03'], 'TIPO DE PORTAFOLIO': ['A'], 'ITEM': [155], 'ESTADO': ['001 - ACTIVO'],
        }),
        'espejo': _excel_bytes({'grupo_id': ['G1'], 'item_id': [155]}),
    }
    archivos.update(overrides)
    return archivos


def test_carga_exitosa_con_columnas_completas():
    dfs = load_files(**_archivos_validos())
    assert set(dfs.keys()) == {'stock', 'celes', 'portafolio', 'tiendas', 'tiendas_item', 'espejo'}
    assert len(dfs['stock']) == 1


def test_falta_columna_requerida_lanza_valueerror_con_mensaje_claro():
    archivos = _archivos_validos(stock=_excel_bytes({
        'Item': ['155'], 'Desc. item': ['Prod A'], 'Cant. disponible': [10],
        'Factor U.M.': [1], 'U.M.': ['CJ20'],  # falta ESTADO DEL PRODUCTO
    }))
    with pytest.raises(ValueError) as exc:
        load_files(**archivos)
    assert 'ESTADO DEL PRODUCTO' in str(exc.value)
    assert LABELS['stock'] in str(exc.value)


def test_estado_del_producto_requerido_en_stock():
    """ESTADO DEL PRODUCTO no filtra la distribución (decisión de negocio:
    toda existencia física se distribuye sin importar el estado), pero sigue
    siendo columna requerida porque alimenta una alerta informativa en
    preprocessor.py cuando un ítem con stock no está ACTIVO."""
    assert 'ESTADO DEL PRODUCTO' in REQUIRED_COLUMNS['stock']


def test_fallback_a_primera_hoja_si_falta_hoja_preferida():
    """Celes usa la hoja 'Items'; si no existe, debe caer a la primera hoja."""
    archivos = _archivos_validos(celes=_excel_bytes({
        'Código de Bodega': ['BOS03'], 'Nombre de Bodega': ['Tienda 3'],
        'Código de Producto': [155], 'Consumo Diario (Unidades de Distribución)': [1.0],
        '(=) Días de Inventario Actuales': [2.0],
        'Inventario Disponible en esta Tienda (Unidades de Distibución)': [1.0],
        '(-) inventario de traslado en proceso': [0.0], 'UM': ['CJ20'],
    }, sheet_name='Sheet1'))
    dfs = load_files(**archivos)
    assert len(dfs['celes']) == 1


def test_espacios_en_encabezados_se_normalizan():
    archivos = _archivos_validos(stock=_excel_bytes({
        'Item ': ['155'], 'Desc. item': ['Prod A'], 'Cant. disponible': [10],
        'Factor U.M.': [1], 'U.M.': ['CJ20'], 'ESTADO DEL PRODUCTO': ['001 - ACTIVO'],
    }))
    dfs = load_files(**archivos)
    assert 'Item' in dfs['stock'].columns


def test_tiendas_excluidas_opcional_no_rompe_si_ausente():
    dfs = load_files(**_archivos_validos(), tiendas_excluidas=None)
    assert 'excluidas' not in dfs


def test_tiendas_excluidas_se_valida_si_se_provee():
    excluidas_ok = _excel_bytes({'Centro Operacional de la Bodega': ['S03']})
    dfs = load_files(**_archivos_validos(), tiendas_excluidas=excluidas_ok)
    assert 'excluidas' in dfs
    assert len(dfs['excluidas']) == 1

    excluidas_mal = _excel_bytes({'Otra Columna': ['S03']})
    with pytest.raises(ValueError):
        load_files(**_archivos_validos(), tiendas_excluidas=excluidas_mal)


def test_falta_archivo_obligatorio_lanza_valueerror():
    with pytest.raises(ValueError) as exc:
        load_files(**_archivos_validos(stock=None))
    assert LABELS['stock'] in str(exc.value)

"""Tests del generador de Excel — foco en bugs de agregación de datos."""

import io

import pandas as pd

from core.exporter import generate_excel


def _row(item_code, item_desc, store_code, consumo, existencias, cajas_asignadas,
         cajas_cedi, zona='Zona 1', vida_util=10):
    return {
        'item_code': item_code,
        'item_desc': item_desc,
        'store_code': store_code,
        'store_name': f'Tienda {store_code}',
        'zona': zona,
        'um': 'UND',
        'consumo_diario': consumo,
        'inventario_tienda': existencias,
        'inventario_transito': 0.0,
        'inventario_efectivo': existencias,
        'dias_proyectados': existencias / consumo if consumo > 0 else float('inf'),
        'cajas_asignadas': cajas_asignadas,
        'cajas_fase1': 0,
        'cajas_fase2': 0,
        'cajas_fase3': cajas_asignadas,
        'cajas_disponibles_cedi': cajas_cedi,
        'vida_util': vida_util,
    }


def test_descripcion_inconsistente_no_duplica_fila_en_analisis_comprador():
    """Si el mismo item_code llega con `item_desc` ligeramente distinto entre
    tiendas (p.ej. 'MANZANA ROYAL GALA UND' vs 'MANZANA  ROYAL GALA UND' — con
    doble espacio, tal como llega de `DB_Portafolio_Fruver.DESCRIPCION` en el
    preprocesador) "Análisis Comprador" no debe generar una fila por cada
    variante de descripción ni inflar los porcentajes de riesgo.

    Bug real detectado en producción (Output/distribucion_fruver_20260608.xlsx,
    ítem 1671 "MANZANA ROYAL GALA"): `item_base` agrupaba por
    `(item_code, item_desc)` mientras que `item_merma` agrupaba solo por
    `item_code` (exporter.py), así que el merge ('left' sobre item_code)
    pegaba las MISMAS estadísticas de merma (calculadas sobre TODAS las
    tiendas del ítem) a cada fila duplicada — la fila con menos tiendas
    terminaba con `% Tiendas con Riesgo = 2200%`, un valor imposible.
    El arreglo agrupa ambos por `item_code` únicamente, tomando
    `item_desc` con 'first' — igual que ya se hace con `vida_util`.
    """
    rows_normales = [
        _row(1671, 'MANZANA ROYAL GALA UND', f'S{i:02d}', consumo=1.0,
             existencias=20.0, cajas_asignadas=1, cajas_cedi=100)
        for i in range(1, 21)
    ]
    # Una sola tienda con descripción "sucia" (doble espacio) para el mismo ítem.
    rows_sucia = [
        _row(1671, 'MANZANA  ROYAL GALA UND', 'S99', consumo=1.0,
             existencias=20.0, cajas_asignadas=1, cajas_cedi=100)
    ]
    df_output = pd.DataFrame(rows_normales + rows_sucia)
    df_merged = df_output.copy()

    excel_bytes = generate_excel(df_output, alertas=[], df_merged=df_merged)
    comprador = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Análisis Comprador')

    filas_item = comprador[comprador['Código Ítem'] == 1671]
    assert len(filas_item) == 1, (
        "El ítem 1671 generó más de una fila en 'Análisis Comprador' — "
        "descripciones con espacios distintos no deben duplicar el ítem "
        f"(filas encontradas: {len(filas_item)})"
    )

    fila = filas_item.iloc[0]
    assert fila['# Tiendas Activas'] == 21
    assert 0 <= fila['% Tiendas con Riesgo'] <= 100, (
        f"% Tiendas con Riesgo imposible: {fila['% Tiendas con Riesgo']}"
    )

    # El mismo patrón de agrupación existe en "Resumen Ítems" — debe estar igual de protegido.
    resumen_items = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Resumen Ítems', skiprows=4)
    filas_resumen = resumen_items[resumen_items['Código Ítem'] == 1671]
    assert len(filas_resumen) == 1, (
        "El ítem 1671 generó más de una fila en 'Resumen Ítems' por "
        f"descripciones inconsistentes (filas encontradas: {len(filas_resumen)})"
    )


def test_dias_inventario_actual_no_contiene_literal_inf():
    """Bug de auditoría: una fila con consumo_diario=0 (dias_proyectados=inf
    antes del pedido) escribía el string literal 'inf' en la columna 'Días
    Inventario Actual' del Excel, a diferencia de su columna hermana 'Días
    Inventario Proyectado' que sí sanitizaba a vacío. Debe quedar vacía
    (NaN) en ambas, no un texto no numérico."""
    df_output = pd.DataFrame([
        _row(99, 'Producto sin consumo', 'S01', consumo=0.0,
             existencias=5.0, cajas_asignadas=2, cajas_cedi=2),
    ])
    excel_bytes = generate_excel(df_output, alertas=[])
    dist = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Distribución')

    valor = dist.loc[0, 'Días Inventario Actual']
    assert pd.isna(valor), f"Se esperaba celda vacía, se obtuvo {valor!r} (tipo {type(valor)})"
    valor_proy = dist.loc[0, 'Días Inventario Proyectado']
    assert pd.isna(valor_proy), f"Se esperaba celda vacía, se obtuvo {valor_proy!r}"


def test_hoja_distribucion_columnas_y_totales():
    """La hoja Distribución debe traer todas las columnas esperadas y el
    total de 'Pedido Final' debe coincidir con las cajas asignadas."""
    df_output = pd.DataFrame([
        _row(1, 'Producto A', 'S01', consumo=2.0, existencias=1.0, cajas_asignadas=3, cajas_cedi=3),
        _row(1, 'Producto A', 'S02', consumo=1.0, existencias=4.0, cajas_asignadas=0, cajas_cedi=3),
    ])
    excel_bytes = generate_excel(df_output, alertas=[])
    dist = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Distribución')

    columnas_esperadas = [
        'Centro Operacional de la Bodega', 'Nombre Tienda', 'Zona',
        'Código de Producto', 'Nombre Ítem', 'UM', 'Consumo Diario',
        'Stock Antes Pedido', 'Días Inventario Actual', 'Pedido Final',
        'Stock Después Pedido', 'Días Inventario Proyectado',
    ]
    assert list(dist.columns) == columnas_esperadas
    assert int(dist['Pedido Final'].sum()) == 3


def test_hoja_resumen_agrega_por_tienda():
    """La hoja Resumen debe sumar 'Total Cajas' por tienda, no por fila."""
    df_output = pd.DataFrame([
        _row(1, 'Producto A', 'S01', consumo=2.0, existencias=1.0, cajas_asignadas=2, cajas_cedi=5),
        _row(2, 'Producto B', 'S01', consumo=1.0, existencias=0.0, cajas_asignadas=1, cajas_cedi=1),
        _row(1, 'Producto A', 'S02', consumo=1.0, existencias=4.0, cajas_asignadas=0, cajas_cedi=5),
    ])
    excel_bytes = generate_excel(df_output, alertas=[])
    resumen = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Resumen')

    fila_s01 = resumen[resumen['Centro Operacional de la Bodega'] == 'S01'].iloc[0]
    assert int(fila_s01['Total Cajas']) == 3


def test_hoja_riesgo_merma_ya_no_se_genera():
    """La hoja 'Riesgo Merma' se eliminó a pedido del usuario — el Excel no
    debe traerla, aunque el cálculo de riesgo de merma por ítem se mantenga
    dentro de 'Análisis Comprador' (columnas '# Tiendas con Riesgo Merma' /
    '% Tiendas con Riesgo')."""
    df_output = pd.DataFrame([
        _row(1, 'Producto A', 'S01', consumo=2.0, existencias=1.0, cajas_asignadas=1,
             cajas_cedi=1, vida_util=30),
    ])
    df_merged = df_output.copy()
    excel_bytes = generate_excel(df_output, alertas=[], df_merged=df_merged)
    hojas = pd.ExcelFile(io.BytesIO(excel_bytes)).sheet_names
    assert 'Riesgo Merma' not in hojas

    comprador = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Análisis Comprador')
    assert '# Tiendas con Riesgo Merma' in comprador.columns
    assert int(comprador.loc[0, '# Tiendas con Riesgo Merma']) == 0


def test_riesgo_sobrestock_usa_umbral_propio_desacoplado_del_algoritmo():
    """UMBRAL_RIESGO_SOBRESTOCK (10 días, exporter.py) es un umbral de reporte
    independiente de TOPE_EXCEDENTE (6 días, algorithm.py) — el tope real que
    usa el algoritmo en Fase 3 para dejar de asignar más sobrante a una
    tienda. Una tienda con 8 días proyectados tras el pedido ya superaría el
    tope real del algoritmo, pero NO debe marcarse en '# Tiendas con Riesgo
    Sobrestock' porque sigue por debajo del umbral de reporte (10 días) —
    prueba que el reporte quedó desacoplado del comportamiento real."""
    df_output = pd.DataFrame([
        _row(1, 'Producto A', 'S01', consumo=1.0, existencias=0.0, cajas_asignadas=8, cajas_cedi=20),
        _row(1, 'Producto A', 'S02', consumo=1.0, existencias=0.0, cajas_asignadas=12, cajas_cedi=20),
    ])
    df_merged = df_output.copy()
    excel_bytes = generate_excel(df_output, alertas=[], df_merged=df_merged)
    comprador = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Análisis Comprador')

    col_conteo = [c for c in comprador.columns if c.startswith('# Tiendas con Riesgo Sobrestock')]
    assert col_conteo == ['# Tiendas con Riesgo Sobrestock (10d)'], list(comprador.columns)

    fila = comprador[comprador['Código Ítem'] == 1].iloc[0]
    # Solo S02 (12 días > 10) cuenta; S01 (8 días) queda fuera aunque supere
    # el tope real del algoritmo (6 días).
    assert int(fila['# Tiendas con Riesgo Sobrestock (10d)']) == 1


def test_generate_excel_has_data_false_no_rompe():
    """df_output vacío/None no debe lanzar excepción; cada hoja debe quedar
    con sus columnas esperadas pero sin filas."""
    excel_bytes = generate_excel(pd.DataFrame(), alertas=[])
    dist = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Distribución')
    assert len(dist) == 0
    assert 'Pedido Final' in dist.columns

    excel_bytes_none = generate_excel(None, alertas=[])
    dist_none = pd.read_excel(io.BytesIO(excel_bytes_none), sheet_name='Distribución')
    assert len(dist_none) == 0


def test_generate_excel_sin_df_merged_omite_analisis_comprador():
    """Sin df_merged no debe generarse la hoja 'Análisis Comprador'."""
    df_output = pd.DataFrame([
        _row(1, 'Producto A', 'S01', consumo=2.0, existencias=1.0, cajas_asignadas=1, cajas_cedi=1),
    ])
    excel_bytes = generate_excel(df_output, alertas=[], df_merged=None)
    hojas = pd.ExcelFile(io.BytesIO(excel_bytes)).sheet_names
    assert 'Análisis Comprador' not in hojas


def test_hoja_alertas_contiene_alertas_heterogeneas_sin_error():
    """Alertas de distintos tipos (con distintas claves extra) no deben
    romper la escritura del Excel — pandas rellena con NaN lo que falte."""
    df_output = pd.DataFrame([
        _row(1, 'Producto A', 'S01', consumo=2.0, existencias=1.0, cajas_asignadas=1, cajas_cedi=1),
    ])
    alertas = [
        {'tipo': 'CRÍTICO', 'item': 99, 'mensaje': 'Ítem huérfano', 'cajas_sin_distribuir': 5},
        {'tipo': 'ADVERTENCIA', 'item': '-', 'mensaje': 'Sin match en Celes', 'combos_sin_match': 12},
    ]
    excel_bytes = generate_excel(df_output, alertas=alertas)
    alertas_sheet = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Alertas')

    assert len(alertas_sheet) == 2
    assert list(alertas_sheet.columns[:3]) == ['Tipo', 'Ítem', 'Mensaje']
    # CRÍTICO debe ir antes que ADVERTENCIA (orden por severidad).
    assert alertas_sheet.iloc[0]['Tipo'] == 'CRÍTICO'


def test_hoja_alertas_vacia_muestra_ok_sin_alertas():
    """Sin alertas, la hoja debe mostrar una fila 'OK — Sin alertas' en vez
    de quedar completamente vacía."""
    df_output = pd.DataFrame([
        _row(1, 'Producto A', 'S01', consumo=2.0, existencias=1.0, cajas_asignadas=1, cajas_cedi=1),
    ])
    excel_bytes = generate_excel(df_output, alertas=[])
    alertas_sheet = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Alertas')
    assert len(alertas_sheet) == 1
    assert alertas_sheet.iloc[0]['Tipo'] == 'OK'
